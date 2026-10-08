"""Quote-event telemetry on disk: one parquet part per match, compacted once at the end.

A validation run logs millions of quote events. Holding them all in RAM and rewriting
one growing parquet after every map costs O(maps^2) serialization work. Each map writes
its own part here instead, the runner drops the events, and the run compacts the parts
into the compatible `quote_events.parquet` one time, streaming batch by batch.
"""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from backtest.paths import QUOTE_EVENTS_FILENAME
from backtest.report_types import QuoteEventCounts
from backtest.telemetry import QuoteEvent

QUOTE_EVENT_PARTS_DIRNAME = "quote_event_parts"

QUOTE_EVENT_FIELD_NAMES = tuple(field.name for field in fields(QuoteEvent))


def _arrow_type_for(annotation: object) -> pa.DataType:
    if annotation is int:
        return pa.int64()
    if annotation is float:
        return pa.float64()
    if annotation is str:
        return pa.string()
    raise TypeError(f"unsupported QuoteEvent field type {annotation!r}")


QUOTE_EVENT_SCHEMA = pa.schema(
    [pa.field(field.name, _arrow_type_for(field.type)) for field in fields(QuoteEvent)]
)

# build_reserve_events only reads BUY order lifecycle rows out of the tape.
RESERVE_EVENT_KINDS = ("submitted", "cancel_ack", "rejected", "denied", "expired")


@dataclass(frozen=True)
class QuoteKindCount:
    """How many events one match logged for one (kind, reason) pair."""

    match_id: int
    kind: str
    reason: str
    count: int


@dataclass(frozen=True)
class QuoteTelemetry:
    """What summary.json needs from the tape without materializing every event."""

    kind_counts: tuple[QuoteKindCount, ...]
    reserve_events: tuple[QuoteEvent, ...]
    merge_events: tuple[QuoteEvent, ...] = ()


def empty_quote_telemetry() -> QuoteTelemetry:
    """Telemetry of a run that logged no quote events."""
    return QuoteTelemetry(kind_counts=(), reserve_events=(), merge_events=())


def parts_dir(report_dir: Path) -> Path:
    """Directory holding the per-match quote-event parts of one run."""
    return report_dir / QUOTE_EVENT_PARTS_DIRNAME


def _to_arrow_table(events: Sequence[QuoteEvent]) -> pa.Table:
    """Build one typed Arrow table column by column, without per-row dicts."""
    columns = [
        pa.array([getattr(event, field.name) for event in events], type=field.type)
        for field in QUOTE_EVENT_SCHEMA
    ]
    return pa.Table.from_arrays(columns, schema=QUOTE_EVENT_SCHEMA)


def write_quote_event_parts(
    *,
    report_dir: Path,
    events: Sequence[QuoteEvent],
    match_ids: Sequence[int],
) -> None:
    """Write one part per replayed match, overwriting a part left by a crashed attempt."""
    events_by_match: dict[int, list[QuoteEvent]] = {match_id: [] for match_id in match_ids}
    for event in events:
        if event.match_id not in events_by_match:
            raise ValueError(f"quote event for match {event.match_id} outside the replayed batch")
        events_by_match[event.match_id].append(event)
    target = parts_dir(report_dir)
    target.mkdir(parents=True, exist_ok=True)
    for match_id, match_events in events_by_match.items():
        path = target / f"{match_id}.parquet"
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(_to_arrow_table(match_events), tmp)
        tmp.replace(path)


def clear_quote_event_parts(report_dir: Path) -> None:
    """Delete the parts of a previous run so a fresh run cannot adopt them."""
    shutil.rmtree(parts_dir(report_dir), ignore_errors=True)


def assert_quote_event_parts_resumable(report_dir: Path) -> None:
    """Refuse a resume whose checkpoint predates per-match parts; its tape is unsplittable."""
    if parts_dir(report_dir).is_dir():
        return
    if (report_dir / QUOTE_EVENTS_FILENAME).exists():
        raise ValueError(
            f"{report_dir} has {QUOTE_EVENTS_FILENAME} but no {QUOTE_EVENT_PARTS_DIRNAME}/; "
            "this checkpoint predates per-match quote-event parts or the run already "
            "finalized and cleaned them, rerun without --resume"
        )


def copy_quote_event_parts(*, source_dir: Path, target_dir: Path) -> None:
    """Adopt another run directory's parts; part names are match ids, so they cannot clash."""
    source = parts_dir(source_dir)
    if not source.is_dir():
        return
    target = parts_dir(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    for part in sorted(source.glob("*.parquet")):
        shutil.copyfile(part, target / part.name)


@dataclass(frozen=True)
class QuoteCompaction:
    """What one compact pass wrote; the gate for deleting parts afterwards."""

    rows: int
    missing: tuple[int, ...]
    unaccounted: tuple[str, ...]
    wrote: bool


def compact_quote_events(*, report_dir: Path, match_ids: Sequence[int]) -> QuoteCompaction:
    """Stream the parts of the given matches into one quote_events.parquet, in replay order."""
    source = parts_dir(report_dir)
    expected_names = {f"{match_id}.parquet" for match_id in match_ids}
    entries = {entry.name for entry in source.iterdir()} if source.is_dir() else set()
    unaccounted = tuple(sorted(entries - expected_names))
    paths = [source / f"{match_id}.parquet" for match_id in match_ids]
    present = [path for path in paths if path.is_file()]
    missing = tuple(
        dict.fromkeys(
            match_id for match_id, path in zip(match_ids, paths, strict=True) if not path.is_file()
        )
    )
    if not present:
        return QuoteCompaction(rows=0, missing=missing, unaccounted=unaccounted, wrote=False)
    path = report_dir / QUOTE_EVENTS_FILENAME
    tmp = path.with_suffix(".parquet.tmp")
    rows = 0
    with pq.ParquetWriter(tmp, QUOTE_EVENT_SCHEMA) as writer:
        for part in present:
            for batch in pq.ParquetFile(part).iter_batches():
                writer.write_batch(batch)
                rows += batch.num_rows
    tmp.replace(path)
    return QuoteCompaction(rows=rows, missing=missing, unaccounted=unaccounted, wrote=True)


def find_parquet_file_problems(
    path: Path, expected_rows: int, expected_columns: Sequence[str]
) -> tuple[str, ...]:
    """Metadata gate: the file exists and its row count and columns match what was written."""
    if not path.exists():
        return () if expected_rows == 0 else (f"{path.name} missing",)
    parquet = pq.ParquetFile(path)
    problems: list[str] = []
    if parquet.metadata.num_rows != expected_rows:
        problems.append(
            f"{path.name} has {parquet.metadata.num_rows} rows, expected {expected_rows}"
        )
    if expected_rows and parquet.schema_arrow.names != list(expected_columns):
        problems.append(f"{path.name} columns differ from {list(expected_columns)}")
    return tuple(problems)


def find_quote_events_file_problems(
    report_dir: Path, compacted: QuoteCompaction
) -> tuple[str, ...]:
    """Check quote_events.parquet against what the just-finished compact wrote."""
    path = report_dir / QUOTE_EVENTS_FILENAME
    if not compacted.wrote:
        if path.exists():
            return (f"{QUOTE_EVENTS_FILENAME} exists but no parts were compacted",)
        return ()
    if not path.is_file():
        return (f"{QUOTE_EVENTS_FILENAME} missing after compaction",)
    parquet = pq.ParquetFile(path)
    problems: list[str] = []
    if parquet.metadata.num_rows != compacted.rows:
        problems.append(
            f"{QUOTE_EVENTS_FILENAME} has {parquet.metadata.num_rows} rows, "
            f"compact wrote {compacted.rows}"
        )
    if parquet.schema_arrow != QUOTE_EVENT_SCHEMA:
        problems.append(f"{QUOTE_EVENTS_FILENAME} schema differs from the parts schema")
    return tuple(problems)


def _count_kinds(batch: pa.RecordBatch, counts: dict[tuple[int, str, str], int]) -> None:
    """Add one batch's (match_id, kind, reason) group sizes into counts."""
    keys = ["match_id", "kind", "reason"]
    grouped = pa.TableGroupBy(pa.Table.from_batches([batch]).select(keys), keys).aggregate(
        [([], "count_all")]
    )
    rows = zip(
        grouped.column("match_id").to_pylist(),
        grouped.column("kind").to_pylist(),
        grouped.column("reason").to_pylist(),
        grouped.column("count_all").to_pylist(),
        strict=True,
    )
    for match_id, kind, reason, count in rows:
        if match_id is None or kind is None or reason is None or count is None:
            raise ValueError("quote-event group-by produced a null key")
        key = (int(match_id), str(kind), str(reason))
        counts[key] = counts.get(key, 0) + int(count)


def _merge_mask(batch: pa.RecordBatch) -> pa.Array:
    """Rows whose kind is the accounting merge credit."""
    return pc.equal(batch.column("kind"), pa.scalar("merge"))


def _reserve_mask(batch: pa.RecordBatch) -> pa.Array:
    """Rows build_reserve_events would keep: a BUY order lifecycle row with an id."""
    is_reserve_kind = pc.is_in(batch.column("kind"), value_set=pa.array(list(RESERVE_EVENT_KINDS)))
    is_buy = pc.equal(batch.column("side"), pa.scalar("BUY"))
    has_order_id = pc.not_equal(batch.column("order_id"), pa.scalar(""))
    return pc.and_(pc.and_(is_reserve_kind, is_buy), has_order_id)


def _quote_events_from_table(table: pa.Table) -> tuple[QuoteEvent, ...]:
    """Rebuild QuoteEvent objects from an Arrow table with the full schema."""
    columns = [table.column(name).to_pylist() for name in QUOTE_EVENT_FIELD_NAMES]
    return tuple(QuoteEvent(*row) for row in zip(*columns, strict=True))


def read_quote_telemetry(report_dir: Path) -> QuoteTelemetry:
    """Stream quote_events.parquet once for per-match counts and BUY reserve rows."""
    path = report_dir / QUOTE_EVENTS_FILENAME
    if not path.exists():
        return empty_quote_telemetry()
    counts: dict[tuple[int, str, str], int] = {}
    reserve_batches: list[pa.RecordBatch] = []
    merge_batches: list[pa.RecordBatch] = []
    for batch in pq.ParquetFile(path).iter_batches():
        _count_kinds(batch, counts)
        kept = batch.filter(_reserve_mask(batch))
        if kept.num_rows:
            reserve_batches.append(kept)
        merged = batch.filter(_merge_mask(batch))
        if merged.num_rows:
            merge_batches.append(merged)
    reserve_table = pa.Table.from_batches(reserve_batches, schema=QUOTE_EVENT_SCHEMA)
    merge_table = pa.Table.from_batches(merge_batches, schema=QUOTE_EVENT_SCHEMA)
    return QuoteTelemetry(
        kind_counts=tuple(
            QuoteKindCount(match_id=match_id, kind=kind, reason=reason, count=count)
            for (match_id, kind, reason), count in counts.items()
        ),
        reserve_events=_quote_events_from_table(reserve_table),
        merge_events=_quote_events_from_table(merge_table),
    )


def quote_telemetry_from_events(events: Sequence[QuoteEvent]) -> QuoteTelemetry:
    """In-memory reference for read_quote_telemetry; small tapes and tests only."""
    counts: dict[tuple[int, str, str], int] = {}
    for event in events:
        key = (event.match_id, event.kind, event.reason)
        counts[key] = counts.get(key, 0) + 1
    return QuoteTelemetry(
        kind_counts=tuple(
            QuoteKindCount(match_id=match_id, kind=kind, reason=reason, count=count)
            for (match_id, kind, reason), count in counts.items()
        ),
        reserve_events=tuple(
            event
            for event in events
            if event.kind in RESERVE_EVENT_KINDS and event.side == "BUY" and event.order_id
        ),
        merge_events=tuple(event for event in events if event.kind == "merge"),
    )


def sum_quote_event_counts(
    kind_counts: Sequence[QuoteKindCount], match_ids: frozenset[int]
) -> QuoteEventCounts:
    """kind totals plus canceled-reason histogram over the given matches."""
    by_kind: dict[str, int] = {}
    canceled_reason: dict[str, int] = {}
    for row in kind_counts:
        if row.match_id not in match_ids:
            continue
        by_kind[row.kind] = by_kind.get(row.kind, 0) + row.count
        if row.kind == "canceled" and row.reason:
            canceled_reason[row.reason] = canceled_reason.get(row.reason, 0) + row.count
    return {"by_kind": by_kind, "canceled_reason": canceled_reason}
