"""Source-neutral live-match feed: phases, snapshots, and the ticks() contract."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from shared.utils.top_players import TopPlayerFeatures


class FeedSource(StrEnum):
    """Which live source produced this tick."""

    GRID = "grid"
    ODDIN = "oddin"


class MatchPhase(StrEnum):
    """Source-neutral match phase the quoting gate and horn pin read."""

    PRE_MATCH = "pre_match"
    PRE_HORN = "pre_horn"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


@dataclass(frozen=True)
class GameSnapshot:
    """One reduced tick of match state: the numbers the session and archive read."""

    second: int
    server_timestamp: int
    phase: MatchPhase
    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: TopPlayerFeatures
    paused: bool

    @property
    def finished(self) -> bool:
        """True when this tick is the post-game terminal phase."""
        return self.phase is MatchPhase.FINISHED


@dataclass(frozen=True)
class FeedEvent:
    """One source-neutral live tick: snapshot, receipt UTC, source, horn, and YES↔Radiant."""

    snapshot: GameSnapshot
    received_at_utc: str
    source: FeedSource
    horn_unix_seconds: int
    yes_is_radiant: bool


@dataclass(frozen=True)
class SideWait:
    """One map side's pending kill wait: table deaths owed, seconds left to wait."""

    awaited_deaths: int
    seconds_left: float


@dataclass(frozen=True)
class KillTick:
    """A scoreboard kill marker: the per-side wait the table must still confirm."""

    received_at_utc: str
    radiant: SideWait
    dire: SideWait


class LiveFeed(Protocol):
    """A live match feed that yields source-neutral ticks until the match ends."""

    source: FeedSource
    stale_seconds: float

    def ticks(self) -> AsyncIterator[FeedEvent | KillTick]:
        """Yield ticks and kill markers until the feed ends."""
        ...


def unix_seconds_to_iso_z(unix_seconds: int) -> str:
    """Format a Unix epoch second as ISO-8601 UTC with a Z suffix."""
    return (
        datetime.fromtimestamp(unix_seconds, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def horn_clock_is_positive(phase: MatchPhase, second: int) -> bool:
    """True when this clock is past the horn: in play or finished, and above 0.

    A pause before the horn freezes the clock at or below 0, so the stamp
    minus the clock sits early by the whole pause. `second` lags a GRID board
    clock by the table delay, so `second > 0` means that clock is already past 0.
    """
    return phase in {MatchPhase.IN_PROGRESS, MatchPhase.FINISHED} and second > 0


def horn_is_pinnable(event: FeedEvent) -> bool:
    """True when this tick's horn is the real horn, for every source."""
    return horn_clock_is_positive(event.snapshot.phase, event.snapshot.second)


def first_event_horn_iso(events: Sequence[FeedEvent]) -> str | None:
    """Horn stamp from the first emitted tick whose origin is pinnable."""
    for event in events:
        if horn_is_pinnable(event):
            return unix_seconds_to_iso_z(event.horn_unix_seconds)
    return None
