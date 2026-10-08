"""Shared helpers for Telonex book fixture tests."""

from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

import pandas as pd


class TelonexBookLevel(TypedDict):
    """One bid/ask level of a synthetic Telonex book row."""

    price: float
    size: float


TelonexRawLevels = Sequence[TelonexBookLevel | None] | None


def _book_cell(levels: TelonexRawLevels) -> list[dict[str, str] | None] | None:
    """Serialize one book cell the way Telonex writes it: price/size as strings."""
    if levels is None:
        return None
    return [
        None if level is None else {"price": str(level["price"]), "size": str(level["size"])}
        for level in levels
    ]


def write_token_book(
    root: Path,
    *,
    token_id: str,
    rows: list[tuple[int, TelonexRawLevels, TelonexRawLevels]],
    day: str,
) -> None:
    """Write a synthetic Telonex book_snapshot_full partition."""
    asset_dir = root / "book_snapshot_full" / f"asset_id={token_id}"
    asset_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        {
            "timestamp_us": [row[0] for row in rows],
            "bids": [_book_cell(row[1]) for row in rows],
            "asks": [_book_cell(row[2]) for row in rows],
        }
    )
    frame.to_parquet(asset_dir / f"{day}.parquet", index=False)


def write_token_tape(
    root: Path,
    *,
    token_id: str,
    timestamps_us: list[int],
) -> None:
    """Write a synthetic Telonex onchain_fills partition."""
    asset_dir = root / "onchain_fills" / f"asset_id={token_id}"
    asset_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"block_timestamp_us": timestamps_us}).to_parquet(
        asset_dir / "2026-01-01.parquet", index=False
    )
