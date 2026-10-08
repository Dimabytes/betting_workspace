"""Resume-download Telonex books for the LoL included universe."""

import json
import os
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import typer
from dotenv import load_dotenv

from lol.constants import (
    LOL_TELONEX_AUDIT_PATH,
    LOL_TELONEX_BOOKS_DIR,
    LOL_TELONEX_CATALOG_PATH,
    REASON_TELONEX_DOWNLOADED,
    REASON_TELONEX_HTTP_404,
    REASON_TELONEX_MISSING_FROM_CATALOG,
    REASON_TELONEX_MISSING_INTERVAL,
    REASON_TELONEX_RETRIES_EXHAUSTED,
    REASON_TELONEX_SKIPPED_VALID,
    REASON_TELONEX_TOKEN_MISMATCH,
    TELONEX_BOOK_REQUIRED_COLUMNS,
    TELONEX_CATALOG_BATCH_ROWS,
    TELONEX_CATALOG_REQUIRED_COLUMNS,
)
from lol.parquet_io import read_parquet_rows, write_parquet_rows
from lol.types import LolTelonexAuditRow, LolUniverseMarketRow
from shared.constants.lol import LOL_UNIVERSE_PATH
from shared.constants.telonex import (
    TELONEX_API,
    TELONEX_BOOK_CHANNEL,
    TELONEX_DOWNLOAD_WORKERS,
    TELONEX_EXCHANGE,
    TELONEX_MAX_ATTEMPTS,
)
from shared.utils.log import print_count
from shared.utils.telonex_capture import (
    FatalDownloadError,
    asset_channel_dir,
    download_url,
    retry_delay,
    validate_target,
    write_arrow_table,
)

AUDIT_COLUMNS: list[str] = list(LolTelonexAuditRow.__annotations__)
AUDIT_INTEGER_COLUMNS = ["row_count"]
AUDIT_SORT_COLUMNS = ["condition_id", "channel", "asset_id", "date"]
PRINT_REASONS = (
    REASON_TELONEX_DOWNLOADED,
    REASON_TELONEX_SKIPPED_VALID,
    REASON_TELONEX_HTTP_404,
    REASON_TELONEX_RETRIES_EXHAUSTED,
    REASON_TELONEX_MISSING_FROM_CATALOG,
    REASON_TELONEX_TOKEN_MISMATCH,
    REASON_TELONEX_MISSING_INTERVAL,
)
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CatalogMarket:
    """Sliced Telonex catalog fields needed to plan book jobs."""

    market_id: str
    asset_id_0: str
    asset_id_1: str
    book_from: str
    book_to: str


@dataclass(frozen=True)
class BookJob:
    """One asset_id/UTC-day book parquet download."""

    condition_id: str
    asset_id: str
    date: str
    target: Path


@dataclass(frozen=True)
class PlannedWork:
    """Market-level misses plus deduplicated download jobs."""

    miss_rows: tuple[LolTelonexAuditRow, ...]
    jobs: tuple[BookJob, ...]


@dataclass(frozen=True)
class DownloadAttempt:
    """One channel GET: finished audit, or retry/exhaust."""

    audit: LolTelonexAuditRow | None
    retry: bool
    delay: float


@contextmanager
def http_stream(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
) -> Generator[httpx.Response]:
    """GET one object. Tests monkeypatch this to avoid the network."""
    # ponytail: client.stream leaked pool slots on 404 and deadlocked 20 workers.
    # Catalog fits in memory. Upgrade to stream if GET OOMs.
    response = client.get(url, headers=headers, params=params)
    try:
        yield response
    finally:
        response.close()


def discard_stream_body(response: httpx.Response) -> None:
    """Finish a streamed error so the connection returns to the httpx pool."""
    response.read()


def read_api_key() -> str:
    """Return TELONEX_API_KEY from the process environment."""
    key = os.environ.get("TELONEX_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TELONEX_API_KEY is required")
    return key


def catalog_url() -> str:
    """Public Telonex markets catalog parquet URL."""
    return f"{TELONEX_API}/datasets/{TELONEX_EXCHANGE}/markets"


def book_target(capture_root: Path, asset_id: str, day: str) -> Path:
    """On-disk book parquet for one token/UTC day."""
    return asset_channel_dir(capture_root, TELONEX_BOOK_CHANNEL, asset_id) / f"{day}.parquet"


def full_catalog_path(books_dir: Path) -> Path:
    """Temp full-catalog parquet next to book_snapshot_full."""
    return books_dir.parent / "markets.full.parquet"


def catalog_text(value: object) -> str:
    """Strip a catalog scalar to text; None becomes empty."""
    if value is None:
        return ""
    return str(value).strip()


def utc_date_iso(value: object) -> str:
    """Normalize a catalog date cell to YYYY-MM-DD UTC, or empty string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date().isoformat()
        return value.astimezone(UTC).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    if "T" in text:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.date().isoformat()
        return parsed.astimezone(UTC).date().isoformat()
    return date.fromisoformat(text[:10]).isoformat()


def expand_dates(from_date: str, to_exclusive: str) -> tuple[str, ...]:
    """Expand a half-open UTC catalog window into YYYY-MM-DD strings."""
    if not from_date or not to_exclusive:
        return ()
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_exclusive)
    if end <= start:
        return ()
    days: list[str] = []
    cursor = start
    while cursor < end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return tuple(days)


def telonex_http_client() -> httpx.Client:
    """Shared streaming client for catalog and channel downloads."""
    return httpx.Client(
        timeout=httpx.Timeout(120.0, connect=30.0),
        follow_redirects=True,
        headers={"User-Agent": "dota-2-model/0.1 lol-telonex"},
        limits=httpx.Limits(
            max_keepalive_connections=TELONEX_DOWNLOAD_WORKERS,
            max_connections=TELONEX_DOWNLOAD_WORKERS,
        ),
    )


def write_stream_body(response: httpx.Response, partial: Path) -> None:
    """Write streamed bytes into partial, creating the parent only now."""
    partial.parent.mkdir(parents=True, exist_ok=True)
    with partial.open("wb") as handle:
        for chunk in response.iter_bytes(CHUNK_BYTES):
            handle.write(chunk)


def read_parquet_table(path: Path) -> pa.Table:
    read_table = cast(Callable[[Path], pa.Table], pq.read_table)
    return read_table(path)


def iter_parquet_batches(
    reader: pq.ParquetFile,
    batch_size: int,
    columns: list[str] | None = None,
) -> Iterator[pa.RecordBatch]:
    iter_batches = cast(Callable[..., Iterator[pa.RecordBatch]], reader.iter_batches)
    if columns is None:
        return iter_batches(batch_size=batch_size)
    return iter_batches(batch_size=batch_size, columns=columns)


def raise_if_fatal_status(status: int) -> None:
    """Abort the run on 401/403 or unclassified 4xx. 404/429/5xx are not fatal."""
    if status in (401, 403):
        raise FatalDownloadError(f"Telonex HTTP {status}")
    if status == 404 or status == 429 or status >= 500:
        return
    if status >= 400:
        raise FatalDownloadError(f"Telonex HTTP {status}")


def download_catalog_file(client: httpx.Client, dest: Path) -> None:
    """Stream the full markets catalog to dest with bounded backoff."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    url = catalog_url()
    last_error = "retries_exhausted"
    for attempt in range(1, TELONEX_MAX_ATTEMPTS + 1):
        partial.unlink(missing_ok=True)
        try:
            with http_stream(client, url, {}, {}) as response:
                status = response.status_code
                if status != 200:
                    discard_stream_body(response)
                raise_if_fatal_status(status)
                if status == 404 or status == 429 or status >= 500:
                    last_error = f"http_{status}"
                    if status == 404 or attempt == TELONEX_MAX_ATTEMPTS:
                        break
                    time.sleep(retry_delay(response, attempt))
                    continue
                write_stream_body(response, partial)
            partial.replace(dest)
            return
        except FatalDownloadError:
            raise
        except (httpx.TransportError, httpx.TimeoutException):
            last_error = "transport"
            partial.unlink(missing_ok=True)
            if attempt == TELONEX_MAX_ATTEMPTS:
                break
            time.sleep(retry_delay(None, attempt))
    raise RuntimeError(f"Telonex catalog {last_error}")


def slice_catalog(full_path: Path, out_path: Path, condition_ids: set[str]) -> None:
    """Keep catalog rows whose market_id is in condition_ids and write atomically."""
    reader = pq.ParquetFile(full_path)
    names = set(reader.schema_arrow.names)
    missing = [name for name in TELONEX_CATALOG_REQUIRED_COLUMNS if name not in names]
    if missing:
        raise RuntimeError("Telonex catalog missing required columns")
    condition_array = pa.array(sorted(condition_ids), type=pa.string())
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer: pq.ParquetWriter | None = None
    try:
        is_in = cast(Callable[..., Any], pc.is_in)
        for batch in iter_parquet_batches(reader, TELONEX_CATALOG_BATCH_ROWS):
            mask = is_in(batch.column("market_id"), value_set=condition_array)
            filtered = batch.filter(mask)
            if filtered.num_rows == 0:
                continue
            table = pa.Table.from_batches([filtered])
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        write_arrow_table(reader.schema_arrow.empty_table(), out_path)
        tmp.unlink(missing_ok=True)
        return
    tmp.replace(out_path)


def load_catalog_markets(path: Path) -> dict[str, CatalogMarket]:
    """Load the sliced catalog keyed by market_id; duplicate ids are fatal."""
    table = read_parquet_table(path)
    markets: dict[str, CatalogMarket] = {}
    for raw_row in table.to_pylist():
        raw = cast(dict[str, object], raw_row)
        market_id = catalog_text(raw.get("market_id"))
        if not market_id:
            continue
        if market_id in markets:
            raise RuntimeError("duplicate market_id in catalog slice")
        markets[market_id] = CatalogMarket(
            market_id=market_id,
            asset_id_0=catalog_text(raw.get("asset_id_0")),
            asset_id_1=catalog_text(raw.get("asset_id_1")),
            book_from=utc_date_iso(raw.get("book_snapshot_full_from")),
            book_to=utc_date_iso(raw.get("book_snapshot_full_to")),
        )
    return markets


def refresh_catalog(
    client: httpx.Client,
    books_dir: Path,
    catalog_path: Path,
    condition_ids: set[str],
) -> dict[str, CatalogMarket]:
    """Download, slice, and delete the full catalog; return sliced rows."""
    full_path = full_catalog_path(books_dir)
    download_catalog_file(client, full_path)
    try:
        slice_catalog(full_path, catalog_path, condition_ids)
    finally:
        full_path.unlink(missing_ok=True)
        full_path.with_suffix(full_path.suffix + ".partial").unlink(missing_ok=True)
    return load_catalog_markets(catalog_path)


def gamma_token_set(row: LolUniverseMarketRow) -> set[str]:
    """Parse Gamma clob token ids from the universe JSON list."""
    raw = json.loads(row["clob_token_ids_json"])
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in cast(list[object], raw)}


def tokens_match_catalog(gamma_tokens: set[str], catalog: CatalogMarket) -> bool:
    """True when catalog tokens are two distinct ids equal to Gamma as a set."""
    if not catalog.asset_id_0 or not catalog.asset_id_1:
        return False
    if catalog.asset_id_0 == catalog.asset_id_1:
        return False
    return gamma_tokens == {catalog.asset_id_0, catalog.asset_id_1}


def market_miss_row(condition_id: str, channel: str, reason: str) -> LolTelonexAuditRow:
    """One market-level or empty-window audit row with no day jobs."""
    return {
        "condition_id": condition_id,
        "channel": channel,
        "asset_id": "",
        "date": None,
        "row_count": 0,
        "complete": False,
        "reason": reason,
    }


def job_audit_row(job: BookJob, row_count: int, reason: str) -> LolTelonexAuditRow:
    """One asset/day audit row after skip, download, or job failure."""
    complete = reason in (REASON_TELONEX_DOWNLOADED, REASON_TELONEX_SKIPPED_VALID)
    return {
        "condition_id": job.condition_id,
        "channel": TELONEX_BOOK_CHANNEL,
        "asset_id": job.asset_id,
        "date": job.date,
        "row_count": row_count,
        "complete": complete,
        "reason": reason,
    }


def included_markets(rows: Sequence[LolUniverseMarketRow]) -> list[LolUniverseMarketRow]:
    """Return Stage 01 included winner markets."""
    return [row for row in rows if row["included"]]


def plan_downloads(
    included: Sequence[LolUniverseMarketRow],
    catalog: dict[str, CatalogMarket],
    books_dir: Path,
) -> PlannedWork:
    """Build market-level misses and unique (asset_id, date) book jobs."""
    misses: list[LolTelonexAuditRow] = []
    jobs: list[BookJob] = []
    seen: set[tuple[str, str]] = set()
    capture_root = books_dir.parent
    for row in included:
        condition_id = str(row["condition_id"])
        entry = catalog.get(condition_id)
        if entry is None:
            misses.append(market_miss_row(condition_id, "", REASON_TELONEX_MISSING_FROM_CATALOG))
            continue
        if not tokens_match_catalog(gamma_token_set(row), entry):
            misses.append(market_miss_row(condition_id, "", REASON_TELONEX_TOKEN_MISMATCH))
            continue
        days = expand_dates(entry.book_from, entry.book_to)
        if not days:
            misses.append(
                market_miss_row(condition_id, TELONEX_BOOK_CHANNEL, REASON_TELONEX_MISSING_INTERVAL)
            )
            continue
        for asset_id in (entry.asset_id_0, entry.asset_id_1):
            for day in days:
                key = (asset_id, day)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(
                    BookJob(
                        condition_id=condition_id,
                        asset_id=asset_id,
                        date=day,
                        target=book_target(capture_root, asset_id, day),
                    )
                )
    jobs.sort(key=job_date_newest_first, reverse=True)
    return PlannedWork(miss_rows=tuple(misses), jobs=tuple(jobs))


def job_date_newest_first(job: BookJob) -> str:
    """Sort key so recent UTC days download before older ones."""
    return job.date


def retry_or_stop(attempt: int, delay: float) -> DownloadAttempt:
    """Retry with delay, or stop when this was the last attempt."""
    if attempt == TELONEX_MAX_ATTEMPTS:
        return DownloadAttempt(audit=None, retry=False, delay=0.0)
    return DownloadAttempt(audit=None, retry=True, delay=delay)


def apply_download_status(
    response: httpx.Response,
    job: BookJob,
    columns: frozenset[str],
    partial: Path,
    attempt: int,
) -> DownloadAttempt:
    """Map one HTTP status onto write, retry, or a finished audit."""
    status = response.status_code
    if status != 200:
        discard_stream_body(response)
    raise_if_fatal_status(status)
    if status == 404:
        return DownloadAttempt(
            audit=job_audit_row(job, 0, REASON_TELONEX_HTTP_404), retry=False, delay=0.0
        )
    if status == 429 or status >= 500:
        return retry_or_stop(attempt, retry_delay(response, attempt))
    write_stream_body(response, partial)
    check = validate_target(partial, job.asset_id, columns)
    if not check.valid:
        partial.unlink(missing_ok=True)
        return retry_or_stop(attempt, retry_delay(None, attempt))
    partial.replace(job.target)
    return DownloadAttempt(
        audit=job_audit_row(job, check.rows, REASON_TELONEX_DOWNLOADED), retry=False, delay=0.0
    )


def download_one(api_key: str, job: BookJob) -> LolTelonexAuditRow:
    """Download or skip one book parquet; 401/403 propagate as FatalDownloadError."""
    columns = TELONEX_BOOK_REQUIRED_COLUMNS
    if job.target.exists():
        return job_audit_row(
            job, pq.ParquetFile(job.target).metadata.num_rows, REASON_TELONEX_SKIPPED_VALID
        )
    partial = job.target.with_suffix(job.target.suffix + ".partial")
    partial.unlink(missing_ok=True)
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"asset_id": job.asset_id}
    url = download_url(TELONEX_BOOK_CHANNEL, job.date)
    with telonex_http_client() as client:
        for attempt in range(1, TELONEX_MAX_ATTEMPTS + 1):
            try:
                with http_stream(client, url, headers, params) as response:
                    outcome = apply_download_status(response, job, columns, partial, attempt)
                if outcome.audit is not None:
                    return outcome.audit
                if not outcome.retry:
                    break
                time.sleep(outcome.delay)
            except FatalDownloadError:
                raise
            except (httpx.TransportError, httpx.TimeoutException):
                partial.unlink(missing_ok=True)
                outcome = retry_or_stop(attempt, retry_delay(None, attempt))
                if not outcome.retry:
                    break
                time.sleep(outcome.delay)
    return job_audit_row(job, 0, REASON_TELONEX_RETRIES_EXHAUSTED)


def print_download_totals(
    included_count: int, job_count: int, rows: Sequence[LolTelonexAuditRow]
) -> None:
    """Print included/job totals and stable per-reason counts."""
    print_count("included_markets", included_count)
    print_count("jobs", job_count)
    totals: dict[str, int] = {reason: 0 for reason in PRINT_REASONS}
    for row in rows:
        reason = row["reason"]
        totals[reason] = totals.get(reason, 0) + 1
    for reason in PRINT_REASONS:
        print_count(reason, totals[reason])


def required_incomplete(rows: Sequence[LolTelonexAuditRow]) -> bool:
    """True when any audit row is incomplete."""
    return any(not row["complete"] for row in rows)


def fetch_telonex_books(
    universe_path: Path, books_dir: Path, catalog_path: Path, audit_path: Path
) -> None:
    """Slice the Telonex catalog and download books."""
    api_key = read_api_key()
    universe = cast(list[LolUniverseMarketRow], read_parquet_rows(universe_path))
    included = included_markets(universe)
    print_count("included_markets", len(included))
    condition_ids = {str(row["condition_id"]) for row in included}
    with telonex_http_client() as client:
        catalog = refresh_catalog(client, books_dir, catalog_path, condition_ids)
        planned = plan_downloads(included, catalog, books_dir)
        print_count("jobs", len(planned.jobs))
        rows: list[LolTelonexAuditRow] = list(planned.miss_rows)
        done = 0
        job_count = len(planned.jobs)
        http_batch = 250
        try:
            print(f"channel_start {TELONEX_BOOK_CHANNEL} {job_count}", flush=True)
            for start in range(0, len(planned.jobs), http_batch):
                batch = planned.jobs[start : start + http_batch]
                pool = ThreadPoolExecutor(max_workers=TELONEX_DOWNLOAD_WORKERS)
                try:
                    futures = [pool.submit(download_one, api_key, job) for job in batch]
                    for future in as_completed(futures):
                        rows.append(future.result())
                        done += 1
                        if done == job_count or done % 500 == 0:
                            print(f"download_progress {done}/{job_count}", flush=True)
                except BaseException:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                pool.shutdown(wait=True)
            print(f"channel_done {TELONEX_BOOK_CHANNEL}", flush=True)
        except KeyboardInterrupt:
            write_parquet_rows(
                rows, audit_path, AUDIT_COLUMNS, AUDIT_INTEGER_COLUMNS, AUDIT_SORT_COLUMNS
            )
            print_download_totals(len(included), len(planned.jobs), rows)
            raise
        write_parquet_rows(
            rows, audit_path, AUDIT_COLUMNS, AUDIT_INTEGER_COLUMNS, AUDIT_SORT_COLUMNS
        )
        print_download_totals(len(included), len(planned.jobs), rows)
        if required_incomplete(rows):
            raise typer.Exit(1)


def main(
    universe_path: Annotated[Path, typer.Option("--universe-path")] = LOL_UNIVERSE_PATH,
    books_dir: Annotated[Path, typer.Option("--books-dir")] = LOL_TELONEX_BOOKS_DIR,
    catalog_path: Annotated[Path, typer.Option("--catalog-path")] = LOL_TELONEX_CATALOG_PATH,
    audit_path: Annotated[Path, typer.Option("--audit-path")] = LOL_TELONEX_AUDIT_PATH,
) -> None:
    """Download Telonex books for included LoL markets."""
    load_dotenv()
    fetch_telonex_books(universe_path, books_dir, catalog_path, audit_path)


if __name__ == "__main__":
    typer.run(main)
