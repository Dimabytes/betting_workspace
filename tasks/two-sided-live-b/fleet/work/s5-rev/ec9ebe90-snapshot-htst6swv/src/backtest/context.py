"""Per-market description every Dota backtest replay needs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypedDict

from prediction_market_extensions.backtesting._replay_specs import BookReplay

from backtest.marks import MidSeries
from shared.types.opendota import OpenDotaPause

REPLAY_LEAD = timedelta(minutes=2)
SETTLEMENT_BUFFER = timedelta(minutes=1)
POLYMARKET_VENUE = "POLYMARKET"


@dataclass(frozen=True)
class ReplayWindow:
    """Wall-clock book window: lead before the anchor through buffer after game end."""

    start: datetime
    end: datetime


def calculate_replay_window(*, horn_at: datetime, game_ended_at: datetime) -> ReplayWindow:
    """Book window anchored on the horn: two minutes of pre-match book through game end + 1min.

    Books stop at game end because nothing is traded after it that the strategy
    may act on, and the quiet tail up to settlement would load days of book that
    the local Telonex cache does not even hold.
    """
    return ReplayWindow(start=horn_at - REPLAY_LEAD, end=game_ended_at + SETTLEMENT_BUFFER)


def calculate_availability_window(
    *, map_load_at: datetime, game_ended_at: datetime
) -> ReplayWindow:
    """Disk-check window anchored on spawn — a superset of the horn replay window.

    Spawn is always before the horn (90s pre-game plus non-negative pre-horn
    pauses), so this window never drops a day the real replay needs.
    """
    return calculate_replay_window(horn_at=map_load_at, game_ended_at=game_ended_at)


def calculate_clock_end(*, game_ended_at: datetime, market_closed_at: datetime) -> datetime:
    """Simulation clock end: one minute past the later of game end and market close."""
    return max(game_ended_at, market_closed_at) + SETTLEMENT_BUFFER


def build_instrument_id(*, condition_id: str, token_id: str) -> str:
    """The Polymarket instrument id the framework loader assigns to one market token.

    Kept as a string: Nautilus parses it back into an `InstrumentId` when it
    decodes the strategy config.
    """
    return f"{condition_id}-{token_id}.{POLYMARKET_VENUE}"


class ReplayMetadata(TypedDict):
    """What the backtest framework carries alongside each replayed book."""

    sim_label: str
    match_id: int
    condition_id: str
    token_ids: list[str]
    radiant_token_index: int
    zero_signal_delay: bool


@dataclass(frozen=True)
class MarketContext:
    """Everything the backtest needs to describe one linked match/market pair."""

    match_id: int
    condition_id: str
    event_id: str
    market_slug: str
    token_ids: tuple[str, str]
    radiant_token_index: int
    radiant_win: bool
    seconds_delay: int
    horn_at: datetime
    game_ended_at: datetime
    market_closed_at: datetime
    replay_start: datetime
    replay_end: datetime
    clock_end: datetime

    @property
    def instrument_ids(self) -> tuple[str, str]:
        """Both market legs, ordered by token index like `token_ids`."""
        first, second = self.token_ids
        return (
            build_instrument_id(condition_id=self.condition_id, token_id=first),
            build_instrument_id(condition_id=self.condition_id, token_id=second),
        )

    def side_of(self, token_index: int) -> str:
        """Which Dota side a token pays out on."""
        return "radiant" if token_index == self.radiant_token_index else "dire"

    def as_replay_metadata(self, token_index: int) -> ReplayMetadata:
        """Framework metadata for one leg, labelled by the side that token pays out on."""
        return {
            "sim_label": f"{self.market_slug}-{self.side_of(token_index)}",
            "match_id": self.match_id,
            "condition_id": self.condition_id,
            "token_ids": list(self.token_ids),
            "radiant_token_index": self.radiant_token_index,
            "zero_signal_delay": True,
        }

    def as_book_replays(self) -> tuple[BookReplay, ...]:
        """One BookReplay per market token, over the same replay window."""
        return tuple(
            BookReplay(
                market_slug=self.market_slug,
                token_index=token_index,
                start_time=self.replay_start,
                end_time=self.replay_end,
                metadata=self.as_replay_metadata(token_index),
            )
            for token_index in range(len(self.token_ids))
        )


@dataclass(frozen=True)
class ReplayLookups:
    """Pauses, contexts, and mids loaded once for a set of match ids."""

    pauses_by_match: dict[int, list[OpenDotaPause]]
    context_by_match: dict[int, MarketContext]
    mids: dict[int, MidSeries]
