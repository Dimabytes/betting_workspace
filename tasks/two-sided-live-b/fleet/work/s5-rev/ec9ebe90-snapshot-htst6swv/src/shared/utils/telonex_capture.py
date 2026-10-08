# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Telonex capture-path and download helpers shared by book fetch and readers."""

import random
from dataclasses import dataclass
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from shared.constants.telonex import TELONEX_API, TELONEX_EXCHANGE


class FatalDownloadError(RuntimeError):
    """Stop the run after HTTP 401/403 or an unclassified 4xx."""


@dataclass(frozen=True)
class ValidationResult:
    """Footer/schema/asset_id check of one provider parquet."""

    valid: bool
    rows: int


def asset_channel_dir(capture_root: Path, channel: str, token_id: str) -> Path:
    """Capture directory for one token under one Telonex channel."""
    return capture_root / channel / f"asset_id={token_id}"


def download_url(channel: str, day: str) -> str:
    """Authenticated Telonex download URL for one channel and UTC day."""
    return f"{TELONEX_API}/downloads/{TELONEX_EXCHANGE}/{channel}/{day}"


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Bounded backoff; honor Retry-After on 429, capped at 120s."""
    if response is not None and response.status_code == 429:
        raw = response.headers.get("Retry-After", "0")
        try:
            return min(120.0, float(raw) or 2**attempt)
        except ValueError:
            pass
    return min(60.0, float(2**attempt)) + random.random()


def write_arrow_table(table: pa.Table, path: Path) -> None:
    """Write one Arrow table through a temp sibling, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(path)


def validate_target(
    path: Path, asset_id: str, required_columns: frozenset[str]
) -> ValidationResult:
    """Validate parquet footer, required columns, and asset_id values."""
    if not path.exists() or path.stat().st_size < 12:
        return ValidationResult(valid=False, rows=0)
    try:
        parquet = pq.ParquetFile(path)
    except Exception:
        return ValidationResult(valid=False, rows=0)
    names = set(parquet.schema_arrow.names)
    if not names >= required_columns:
        return ValidationResult(valid=False, rows=0)
    rows = parquet.metadata.num_rows
    if rows == 0:
        return ValidationResult(valid=True, rows=0)
    for batch in parquet.iter_batches(batch_size=100_000, columns=["asset_id"]):
        for value in batch.column("asset_id").to_pylist():
            if value is None:
                continue
            if str(value) != asset_id:
                return ValidationResult(valid=False, rows=rows)
    return ValidationResult(valid=True, rows=rows)
