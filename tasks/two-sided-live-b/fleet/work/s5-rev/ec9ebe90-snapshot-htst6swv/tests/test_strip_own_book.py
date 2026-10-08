"""Strip live resting size out of Telonex book snapshots."""

# pyright: reportPrivateUsage=false

from pathlib import Path

import pandas as pd

from backtest.strip_own_book import (
    _price_key,
    _Resting,
    load_resting_events,
    resting_by_token_at,
    strip_book_frame,
)
from shared.constants.paths import MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR
from shared.utils.match_catalog import load_match_catalog


def test_price_key_ticks() -> None:
    assert _price_key(0.66) == 66
    assert _price_key(0.64) == 64


def test_strip_book_frame_removes_buy_level() -> None:
    events = (
        (
            1_000,
            "accept",
            (
                "c0",
                _Resting(token_index=0, side="BUY", price=0.66, quantity=45.45),
            ),
        ),
    )
    book = pd.DataFrame(
        [
            {
                "timestamp_us": 2_000,
                "bids": [
                    {"price": "0.66", "size": "101.43"},
                    {"price": "0.65", "size": "10"},
                ],
                "asks": [{"price": "0.70", "size": "5"}],
            }
        ]
    )
    out = strip_book_frame(book, token_index=0, events=events)
    bids = list(out.iloc[0].bids)
    assert float(bids[0]["price"]) == 0.66
    assert abs(float(bids[0]["size"]) - 55.98) < 1e-6
    assert float(bids[1]["size"]) == 10.0


def test_strip_removes_level_when_fully_ours() -> None:
    events = (
        (
            1_000,
            "accept",
            ("c0", _Resting(token_index=0, side="SELL", price=0.64, quantity=150.02)),
        ),
    )
    book = pd.DataFrame(
        [
            {
                "timestamp_us": 2_000,
                "bids": [],
                "asks": [
                    {"price": "0.64", "size": "150.02"},
                    {"price": "0.65", "size": "10"},
                ],
            }
        ]
    )
    out = strip_book_frame(book, token_index=0, events=events)
    asks = list(out.iloc[0].asks)
    assert len(asks) == 1
    assert float(asks[0]["price"]) == 0.65


def test_fill_reduces_resting() -> None:
    events = (
        (
            1_000,
            "accept",
            ("c0", _Resting(token_index=0, side="BUY", price=0.66, quantity=45.45)),
        ),
        (1_500, "fill", ("c0", 7.44)),
        (2_000, "remove", "c0"),
    )
    mid = resting_by_token_at(events, wall_us=1_600)
    assert abs(mid[0][("BUY", 66)] - (45.45 - 7.44)) < 1e-6
    end = resting_by_token_at(events, wall_us=2_100)
    assert end.get(0, {}) == {}


def test_archive_entry3_bid066_strips_to_foreign() -> None:
    arch = Path("data/trader/grid-3005975-m2")
    if not (arch / "core_trace.jsonl").is_file():
        return
    events = load_resting_events(arch)
    accept_us = int(1789286149.340232 * 1e6)
    token = load_match_catalog(MATCH_CATALOG_PATH)[8996565323].gamma.token_ids[0]
    book = pd.read_parquet(
        RAW_TELONEX_POLYMARKET_DIR
        / "book_snapshot_full"
        / f"asset_id={token}"
        / "2026-09-13.parquet"
    )
    sub = book[
        (book.timestamp_us >= accept_us - 100_000) & (book.timestamp_us <= accept_us + 100_000)
    ].copy()
    stripped = strip_book_frame(sub, token_index=0, events=events)
    row = stripped[stripped.timestamp_us <= accept_us].iloc[-1]
    size = next(float(lvl["size"]) for lvl in row.bids if abs(float(lvl["price"]) - 0.66) < 1e-9)
    assert abs(size - 55.98) < 0.01
