"""Replay window, Telonex presence, and 1 Hz market seconds for LoL validation maps."""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from lol.constants import (
    LOL_PRIOR_WINDOW_SECONDS,
    LOL_REPLAY_DRAIN,
    LOL_TARGET_HORIZON_SECONDS,
    REASON_ACCEPTED,
)
from lol.livestats_frames import GridRow, LivestatsOk, PauseGap, wall_us_for_second
from lol.types import LolBacktestMarketSecondRow, LolLinkRow
from shared.constants.lol import (
    LOL_REPLAY_LEAD,
    LOL_SOURCE_LAG_SECONDS,
    REASON_MISSING_REQUIRED_CHANNEL,
    REASON_ZERO_SIGNAL_ROWS,
)
from shared.constants.telonex import ONCHAIN_CHANNEL, TELONEX_BOOK_CHANNEL
from shared.types.dataset import MarketQuoteStatus
from shared.utils.telonex_book import (
    MAX_BOOK_AGE_SECONDS,
    US_PER_SECOND,
    TokenBook,
    find_asof_quote,
    resolve_market_pair,
)
from shared.utils.telonex_capture import asset_channel_dir


@dataclass(frozen=True)
class ClobTokens:
    """CLOB token ids in list order, not Radiant/Dire order."""

    token_id_0: str
    token_id_1: str


@dataclass(frozen=True)
class ReplayBounds:
    """Spawn-relative replay window ending at the last livestats frame."""

    replay_start_wall: float
    game_ended_at_wall: float
    replay_end_wall: float
    last_market_second: int


@dataclass(frozen=True)
class ChannelPresence:
    """Required Telonex channels for one map's replay window."""

    has_books: bool
    has_onchain_fills: bool


@dataclass(frozen=True)
class BookWindow:
    """Inclusive microsecond window used to load Telonex books."""

    start_us: int
    end_us: int


def parse_clob_tokens(link: LolLinkRow) -> ClobTokens | None:
    """Read the two CLOB token ids in list order."""
    raw = json.loads(link["clob_token_ids_json"])
    if not isinstance(raw, list):
        return None
    items = cast(list[object], raw)
    if len(items) != 2:
        return None
    tokens = [str(item) for item in items]
    if any(not token for token in tokens):
        return None
    return ClobTokens(token_id_0=tokens[0], token_id_1=tokens[1])


def utc_days_touched(start_wall: float, end_wall: float) -> set[str]:
    """UTC date stems the inclusive wall window touches."""
    start_day = datetime.fromtimestamp(start_wall, tz=UTC).date()
    end_day = datetime.fromtimestamp(end_wall, tz=UTC).date()
    needed: set[str] = set()
    day = start_day
    while day <= end_day:
        needed.add(day.isoformat())
        day += timedelta(days=1)
    return needed


def token_has_channel_days(
    telonex_root: Path, channel: str, token_id: str, needed: set[str]
) -> bool:
    """True when every UTC day in needed has a parquet stem under channel/asset_id."""
    asset_dir = asset_channel_dir(telonex_root, channel, token_id)
    if not asset_dir.is_dir():
        return False
    present = {path.stem for path in asset_dir.glob("*.parquet")}
    return needed <= present


def has_required_channels(
    telonex_root: Path, tokens: ClobTokens, start_wall: float, end_wall: float
) -> ChannelPresence:
    """Book and onchain_fills day files for both CLOB tokens over the replay window."""
    needed = utc_days_touched(start_wall, end_wall)
    has_books = token_has_channel_days(
        telonex_root, TELONEX_BOOK_CHANNEL, tokens.token_id_0, needed
    ) and token_has_channel_days(telonex_root, TELONEX_BOOK_CHANNEL, tokens.token_id_1, needed)
    has_onchain_fills = token_has_channel_days(
        telonex_root, ONCHAIN_CHANNEL, tokens.token_id_0, needed
    ) and token_has_channel_days(telonex_root, ONCHAIN_CHANNEL, tokens.token_id_1, needed)
    return ChannelPresence(has_books, has_onchain_fills)


def replay_bounds(livestats: LivestatsOk) -> ReplayBounds:
    """Use the final livestats frame as map end and add replay lead/drain."""
    game_ended_at_wall = livestats.last_frame_wall_seconds
    last_market_second = math.floor(livestats.last_game_time)
    replay_start_wall = livestats.spawn_wall_seconds - LOL_REPLAY_LEAD.total_seconds()
    replay_end_wall = game_ended_at_wall + LOL_REPLAY_DRAIN.total_seconds()
    return ReplayBounds(
        replay_start_wall=replay_start_wall,
        game_ended_at_wall=game_ended_at_wall,
        replay_end_wall=replay_end_wall,
        last_market_second=last_market_second,
    )


def unix_seconds(wall: float) -> int:
    """Round a float unix wall clock to an integer timestamp."""
    return round(wall)


def book_load_window(
    livestats: LivestatsOk, bounds: ReplayBounds, grid_rows: Sequence[GridRow]
) -> BookWindow:
    """Union of prior, labeled 300s, inference lag, and market-second + markout windows."""
    first_market_us = wall_us_for_second(livestats.spawn_wall_seconds, livestats.pauses, 0)
    last_market_us = wall_us_for_second(
        livestats.spawn_wall_seconds, livestats.pauses, bounds.last_market_second
    )
    last_grid_wall = livestats.spawn_us
    if grid_rows:
        last_grid_wall = max(slot.state_wall_us for slot in grid_rows)
    prior_start = livestats.spawn_us - LOL_PRIOR_WINDOW_SECONDS * US_PER_SECOND
    market_start = first_market_us - int(MAX_BOOK_AGE_SECONDS * US_PER_SECOND)
    labeled_end = (
        last_grid_wall + (LOL_TARGET_HORIZON_SECONDS + LOL_SOURCE_LAG_SECONDS) * US_PER_SECOND
    )
    market_end = last_market_us + LOL_TARGET_HORIZON_SECONDS * US_PER_SECOND
    return BookWindow(min(prior_start, market_start), max(labeled_end, market_end))


def build_market_seconds(
    match_id: int,
    event_id: str,
    condition_id: str,
    spawn_wall_seconds: float,
    pauses: Sequence[PauseGap],
    last_second: int,
    radiant_book: TokenBook | None,
    dire_book: TokenBook | None,
) -> list[LolBacktestMarketSecondRow]:
    """Dense 1 Hz pair mids; stale/missing seconds keep status and a null price."""
    rows: list[LolBacktestMarketSecondRow] = []
    for second in range(0, last_second + 1):
        state_ts_us = wall_us_for_second(spawn_wall_seconds, pauses, second)
        status: MarketQuoteStatus
        price: float | None
        if radiant_book is None or dire_book is None:
            status = "missing_quote"
            price = None
        else:
            pair = resolve_market_pair(
                find_asof_quote(radiant_book, state_ts_us),
                find_asof_quote(dire_book, state_ts_us),
            )
            status = pair.status
            price = pair.market_p_radiant
        rows.append(
            {
                "match_id": match_id,
                "event_id": event_id,
                "condition_id": condition_id,
                "second": second,
                "state_ts_us": state_ts_us,
                "market_status": status,
                "market_p_radiant": price,
            }
        )
    return rows


def audit_reason(has_books: bool, has_onchain_fills: bool, signal_count: int) -> str:
    """First failing eligibility check in the fixed order; otherwise accepted."""
    if not has_books or not has_onchain_fills:
        return REASON_MISSING_REQUIRED_CHANNEL
    if signal_count == 0:
        return REASON_ZERO_SIGNAL_ROWS
    return REASON_ACCEPTED
