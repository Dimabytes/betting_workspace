import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import streamlit as st

from dashboard.tails import TailCache
from shared.utils.jsonl_io import resolve_jsonl
from shared.utils.match_time import parse_utc
from trader.paths import (
    CORE_TRACE_FILENAME,
    GRID_STATE_ARCHIVE_FILENAME,
    MATCH_META_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from viewer.archive_read import (
    as_float,
    as_str,
    horn_at_utc,
    iter_json_objects,
    read_match_document,
)
from viewer.live_tape import load_live_tapes
from viewer.types import TokenTape


@dataclass(frozen=True)
class FileStamps:
    journal: tuple[int, int] | None
    feed: tuple[int, int] | None
    trace: tuple[int, int] | None
    meta: tuple[int, int] | None


@dataclass(frozen=True)
class ChartFacts:
    ok: bool
    error: str | None
    read_at: float
    tapes: tuple[TokenTape, ...]
    horn_at: datetime | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class JournalRead:
    ok: bool
    error: str | None
    truncated: bool
    records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EventRow:
    label: str
    second: float | None
    stamp_age_s: float | None
    text: str
    initial: bool


MAX_EVENTS = 40
_MAX_EVENT_SOURCE = 5000


def file_stamp(path: Path) -> tuple[int, int] | None:
    resolved = resolve_jsonl(path)
    try:
        stat = resolved.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def archive_stamps(archive_dir: Path) -> FileStamps:
    feed = file_stamp(archive_dir / GRID_STATE_ARCHIVE_FILENAME)
    if feed is None:
        feed = file_stamp(archive_dir / ODDIN_STATE_ARCHIVE_FILENAME)
    return FileStamps(
        journal=file_stamp(archive_dir / SESSION_JOURNAL_FILENAME),
        feed=feed,
        trace=file_stamp(archive_dir / CORE_TRACE_FILENAME),
        meta=file_stamp(archive_dir / MATCH_META_FILENAME),
    )


def read_chart(archive_dir: Path) -> ChartFacts:
    notes: list[str] = []
    try:
        document = read_match_document(archive_dir)
    except OSError as exc:
        return ChartFacts(False, type(exc).__name__, time.time(), (), None, ())
    if document is None:
        return ChartFacts(False, "match.json не читается", time.time(), (), None, ())
    horn = horn_at_utc(document)
    try:
        tapes = load_live_tapes(archive_dir)
    except OSError as exc:
        return ChartFacts(False, type(exc).__name__, time.time(), (), horn, ())
    if not tapes:
        notes.append("нет лент сделок — токены не покупались или журнала нет")
    return ChartFacts(True, None, time.time(), tapes, horn, tuple(notes))


def _stamp_age(record: Mapping[str, object], now_s: float) -> float | None:
    stamp = as_str(record.get("recorded_at_utc")) or as_str(record.get("ts_utc"))
    if stamp is None:
        return None
    try:
        return max(0.0, now_s - parse_utc(stamp).timestamp())
    except ValueError:
        return None


def _fill_text(record: Mapping[str, object]) -> str:
    side = as_str(record.get("side")) or "?"
    token = as_str(record.get("token")) or as_str(record.get("outcome")) or ""
    qty = as_float(record.get("qty")) or as_float(record.get("size"))
    price = as_float(record.get("price"))
    parts = [side]
    if qty is not None:
        parts.append(f"{qty:g}")
    if token:
        parts.append(token)
    if price is not None:
        parts.append(f"@{price:g}")
    return " ".join(parts)


def _quote_text(record: Mapping[str, object]) -> str:
    placed = record.get("placed")
    items = cast(list[object], placed) if isinstance(placed, list) else []
    count = len(items)
    texts: list[str] = []
    for raw in items[:3]:
        if not isinstance(raw, dict):
            continue
        fields = cast(dict[str, object], raw)
        side = as_str(fields.get("side")) or "?"
        qty = as_float(fields.get("qty")) or as_float(fields.get("size"))
        price = as_float(fields.get("price"))
        text = side
        if qty is not None:
            text += f" {qty:g}"
        if price is not None:
            text += f" @{price:g}"
        texts.append(text)
    if len(items) > 3:
        texts.append(f"+{len(items) - 3}")
    detail = "; ".join(texts)
    return f"размещено {count}" + (f": {detail}" if detail else "")


def project_events(
    records: Iterable[Mapping[str, object]],
    *,
    now_s: float,
    limit: int = MAX_EVENTS,
    initial_unknown: bool = True,
) -> tuple[EventRow, ...]:
    rows: list[EventRow] = []
    last_pair: tuple[str | None, str | None] | None = None
    seen_signal = False
    for record in records:
        kind = as_str(record.get("kind"))
        second = as_float(record.get("second"))
        age = _stamp_age(record, now_s)
        if kind == "signal":
            reason = as_str(record.get("reason"))
            block = as_str(record.get("entry_block"))
            pair = (reason, block)
            if seen_signal and pair == last_pair:
                continue
            seen_signal = True
            last_pair = pair
            initial = initial_unknown and not rows
            text = f"reason={reason or '—'} block={block or '—'}"
            rows.append(EventRow("signal", second, age, text, initial))
            continue
        if kind in ("fill", "late_fill"):
            label = "fill" if kind == "fill" else "late fill"
            rows.append(EventRow(label, second, age, _fill_text(record), False))
            continue
        if kind == "quote":
            rows.append(EventRow("quote", second, age, _quote_text(record), False))
            continue
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return tuple(rows)


def read_journal(archive_dir: Path, journal_file: str, *, full: bool) -> JournalRead:
    resolved = resolve_jsonl(archive_dir / journal_file)
    if full:
        records: list[dict[str, object]] = []
        try:
            for record in iter_json_objects(resolved):
                records.append(record)
                if len(records) >= _MAX_EVENT_SOURCE:
                    break
        except OSError as exc:
            return JournalRead(False, type(exc).__name__, False, ())
        return JournalRead(True, None, len(records) >= _MAX_EVENT_SOURCE, tuple(records))
    tail = TailCache().read_records(resolved)
    if tail is None:
        return JournalRead(False, "журнал не читается", False, ())
    return JournalRead(True, None, tail.truncated_start, tail.records)


CACHE_TTL_S = 15
CACHE_ENTRIES = 8


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_ENTRIES, show_spinner=False)
def cached_chart(archive_dir: str, stamps: FileStamps) -> ChartFacts:
    return read_chart(Path(archive_dir))


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_ENTRIES, show_spinner=False)
def cached_journal(
    archive_dir: str, journal_file: str, full: bool, stamps: FileStamps
) -> JournalRead:
    return read_journal(Path(archive_dir), journal_file, full=full)
