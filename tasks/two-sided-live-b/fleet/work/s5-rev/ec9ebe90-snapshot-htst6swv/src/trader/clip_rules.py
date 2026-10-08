"""Tournament clip from the Polymarket title suffix. The smaller hit wins."""

from dataclasses import dataclass

from shared.utils.lol_leagues import parse_event_league


@dataclass(frozen=True)
class ClipTier:
    """One clip and the title-suffix names that select it."""

    clip_usdc: float
    names: tuple[str, ...]


@dataclass(frozen=True)
class ClipTable:
    """One game's default clip and the tiers that override it."""

    default_usdc: float
    tiers: tuple[ClipTier, ...]


@dataclass(frozen=True)
class ClipChoice:
    """The clip pinned for one map, and why."""

    clip_usdc: float
    reason: str


def choose_clip(table: ClipTable, event_title: str | None) -> ClipChoice:
    """Match `parse_event_league` against tier names. Two hits take the smaller clip."""
    league = parse_event_league(None, event_title)
    if league is None:
        return ClipChoice(table.default_usdc, "default")
    folded = league.casefold()
    hits = [
        (tier.clip_usdc, name)
        for tier in table.tiers
        for name in tier.names
        if name.casefold() in folded
    ]
    if not hits:
        return ClipChoice(table.default_usdc, "default")
    clip_usdc, name = min(hits)
    return ClipChoice(clip_usdc, name)
