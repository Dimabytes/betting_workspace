# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Lazy Telonex book reads: as-of quotes and age/pair gates."""

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard, cast

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from shared.constants.strategy import MAX_ENTRY_SPREAD_TICKS
from shared.types.dataset import MarketQuoteStatus, MarketSecondRow, OkMarketSecondRow
from shared.types.telonex import TELONEX_BOOK_SCHEMA
from shared.utils.trading import spread_ticks

MAX_BOOK_AGE_SECONDS = 5.0
PAIR_SUM_TOLERANCE = 0.05
US_PER_SECOND = 1_000_000
NS_PER_US = 1_000

AsOfStatus = Literal["ok", "stale", "missing"]


@dataclass(frozen=True)
class TokenBook:
    """Sorted best bid/ask per snapshot for one token inside a time window.

    Snapshots are parsed once at load. A side with no usable level keeps its
    timestamp and a `None` price, so age checks still see one-sided snapshots.
    """

    timestamps_us: tuple[int, ...]
    bids: tuple[float | None, ...]
    asks: tuple[float | None, ...]


@dataclass(frozen=True)
class SideQuote:
    """Best bid/ask/mid at one as-of timestamp for one token."""

    timestamp_us: int
    bid: float
    ask: float
    mid: float
    age_seconds: float


@dataclass(frozen=True)
class AsOfResult:
    """As-of lookup: usable two-sided quote, or why there isn't one.

    `quote` is set exactly when `status` is "ok". `book_ts_us` and `age_seconds`
    always describe the newest snapshot at or before the target, so stale and
    missing results still carry an audit trail.
    """

    status: AsOfStatus
    quote: SideQuote | None
    book_ts_us: int | None
    age_seconds: float | None


@dataclass(frozen=True)
class MarketPairQuote:
    """Age and pair-gated radiant/dire midpoint result."""

    status: MarketQuoteStatus
    market_p_radiant: float | None


def normalize_pair_mids(*, radiant_mid: float, dire_mid: float, tolerance: float) -> float | None:
    """Normalize a complementary mid pair into market_p_radiant, or None if inconsistent."""
    pair_sum = radiant_mid + dire_mid
    if abs(pair_sum - 1.0) > tolerance or pair_sum <= 0.0:
        return None
    return radiant_mid / pair_sum


def last_two_sided_mid(book: TokenBook, target_us: int, window_start_us: int) -> float | None:
    """Last two-sided mid at or before target_us and not older than window_start_us."""
    index = bisect_right(book.timestamps_us, target_us) - 1
    while index >= 0:
        stamp = book.timestamps_us[index]
        if stamp < window_start_us:
            return None
        bid = book.bids[index]
        ask = book.asks[index]
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        index -= 1
    return None


def lookup_strict_prior(
    radiant: TokenBook,
    dire: TokenBook,
    anchor_us: int,
    window_seconds: int,
) -> float | None:
    """Last two-sided P(Radiant) in [anchor - window, anchor). No age fallback."""
    window_start_us = anchor_us - window_seconds * US_PER_SECOND
    target_us = anchor_us - 1
    radiant_mid = last_two_sided_mid(radiant, target_us, window_start_us)
    dire_mid = last_two_sided_mid(dire, target_us, window_start_us)
    if radiant_mid is None or dire_mid is None:
        return None
    return normalize_pair_mids(
        radiant_mid=radiant_mid,
        dire_mid=dire_mid,
        tolerance=PAIR_SUM_TOLERANCE,
    )


def _select_best_prices(
    levels: "pa.ChunkedArray[Any]", pick: Literal["max", "min"]
) -> list[float | None]:
    """Best usable price per snapshot row: max for bids, min for asks; None when no usable level."""
    unified = levels.combine_chunks()
    flat = pc.list_flatten(unified)
    parents = pc.list_parent_indices(unified)
    prices = pc.struct_field(flat, "price")
    sizes = pc.struct_field(flat, "size")
    finite = pc.and_(pc.is_finite(prices), pc.is_finite(sizes))
    usable = pc.and_(finite, pc.greater(sizes, pa.scalar(0.0)))
    usable_levels = pa.table(
        {"parent": pc.filter(parents, usable), "price": pc.filter(prices, usable)}
    )
    best = usable_levels.group_by("parent").aggregate([("price", pick)])
    side: list[float | None] = [None] * len(unified)
    for row_index, price in zip(
        cast(list[int], best.column("parent").to_pylist()),
        cast(list[float | None], best.column(f"price_{pick}").to_pylist()),
        strict=True,
    ):
        side[row_index] = price
    return side


def _require_book_columns(path: Path) -> None:
    """Raise when a book file lacks a schema column; the schema read would fill it with nulls."""
    missing = set(TELONEX_BOOK_SCHEMA.names) - set(pq.read_schema(path).names)
    if missing:
        raise ValueError(f"{path} lacks Telonex book columns {sorted(missing)}")


def load_token_book(
    *, token_id: str, start_us: int, end_us: int, telonex_root: Path
) -> TokenBook | None:
    """Load one token's best bid/ask snapshots inside [start_us, end_us], or None when empty."""
    asset_dir = telonex_root / "book_snapshot_full" / f"asset_id={token_id}"
    paths = sorted(asset_dir.glob("*.parquet"))
    if not paths:
        return None
    for path in paths:
        _require_book_columns(path)

    timestamp_us = pc.field("timestamp_us")
    in_window = (timestamp_us >= start_us) & (timestamp_us <= end_us)
    snapshots = pq.read_table(paths, schema=TELONEX_BOOK_SCHEMA, filters=in_window).sort_by(
        "timestamp_us"
    )
    if snapshots.num_rows == 0:
        return None

    return TokenBook(
        timestamps_us=tuple(cast(list[int], snapshots.column("timestamp_us").to_pylist())),
        bids=tuple(_select_best_prices(snapshots.column("bids"), "max")),
        asks=tuple(_select_best_prices(snapshots.column("asks"), "min")),
    )


def _side_quote_at(book: TokenBook, index: int, target_us: int) -> SideQuote | None:
    """Read one snapshot as a two-sided SideQuote, or None if one-sided."""
    bid = book.bids[index]
    ask = book.asks[index]
    if bid is None or ask is None:
        return None
    timestamp_us = book.timestamps_us[index]
    return SideQuote(
        timestamp_us=timestamp_us,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2.0,
        age_seconds=(target_us - timestamp_us) / US_PER_SECOND,
    )


def find_asof_quote(book: TokenBook, target_us: int) -> AsOfResult:
    """Last two-sided quote at or before target_us; never reads a future snapshot.

    First candidate age is checked before any one-sided walk: no candidate is
    missing; first candidate too old is stale; only the one-sided walk bails on age.
    A stale result carries no quote, so no caller can read a price past the gate.
    """
    index = bisect_right(book.timestamps_us, target_us) - 1
    if index < 0:
        return AsOfResult(status="missing", quote=None, book_ts_us=None, age_seconds=None)

    first_ts = book.timestamps_us[index]
    first_age = (target_us - first_ts) / US_PER_SECOND
    if first_age > MAX_BOOK_AGE_SECONDS:
        return AsOfResult(status="stale", quote=None, book_ts_us=first_ts, age_seconds=first_age)

    while index >= 0:
        age_seconds = (target_us - book.timestamps_us[index]) / US_PER_SECOND
        if age_seconds > MAX_BOOK_AGE_SECONDS:
            break
        quote = _side_quote_at(book, index, target_us)
        if quote is not None:
            return AsOfResult(
                status="ok",
                quote=quote,
                book_ts_us=quote.timestamp_us,
                age_seconds=quote.age_seconds,
            )
        index -= 1
    # Walk exhausted the usable window without a two-sided book.
    return AsOfResult(status="missing", quote=None, book_ts_us=first_ts, age_seconds=first_age)


def resolve_market_pair(radiant: AsOfResult, dire: AsOfResult) -> MarketPairQuote:
    """Apply age and pair gates to a radiant/dire as-of pair."""
    if radiant.status == "stale" or dire.status == "stale":
        return MarketPairQuote(status="stale_quote", market_p_radiant=None)
    if radiant.quote is None or dire.quote is None:
        return MarketPairQuote(status="missing_quote", market_p_radiant=None)
    radiant_spread = spread_ticks(radiant.quote.bid, radiant.quote.ask)
    dire_spread = spread_ticks(dire.quote.bid, dire.quote.ask)
    if radiant_spread >= MAX_ENTRY_SPREAD_TICKS or dire_spread >= MAX_ENTRY_SPREAD_TICKS:
        return MarketPairQuote(status="wide_spread", market_p_radiant=None)
    market_p = normalize_pair_mids(
        radiant_mid=radiant.quote.mid,
        dire_mid=dire.quote.mid,
        tolerance=PAIR_SUM_TOLERANCE,
    )
    if market_p is None:
        return MarketPairQuote(status="inconsistent_pair", market_p_radiant=None)
    return MarketPairQuote(status="ok", market_p_radiant=market_p)


def is_ok_market_second(row: MarketSecondRow) -> TypeGuard[OkMarketSecondRow]:
    """True when the second carries a live mid. `resolve_market_pair` sets one only on "ok"."""
    return row["market_status"] == "ok"


def lookup_market_p_after(
    radiant_book: TokenBook,
    dire_book: TokenBook,
    anchor_us: int,
    horizon_seconds: int,
) -> float | None:
    """Normalized market_p `horizon_seconds` after an anchor, None when gates fail."""
    target_us = anchor_us + horizon_seconds * US_PER_SECOND
    return resolve_market_pair(
        find_asof_quote(radiant_book, target_us),
        find_asof_quote(dire_book, target_us),
    ).market_p_radiant
