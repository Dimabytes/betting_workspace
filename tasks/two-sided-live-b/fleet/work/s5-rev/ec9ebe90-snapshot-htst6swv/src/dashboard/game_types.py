from dataclasses import dataclass
from pathlib import Path
from typing import Literal

COMPACT_LIMIT = 14

Comparison = Literal["aligned", "different", "unknown"]
Continuity = Literal["cold", "warm", "reconstructed"]
XpStatus = Literal["available", "not_used", "unknown"]
Provenance = Literal["signal", "legacy", "none"]


@dataclass(frozen=True)
class TopSummary:
    top1_nw_adv: int | None
    radiant_top1_nw_ratio: float | None
    dire_top1_nw_ratio: float | None
    top3_nw_adv: int | None
    radiant_top3_nw_ratio: float | None
    dire_top3_nw_ratio: float | None


@dataclass(frozen=True)
class GameIdentity:
    archive_dir: Path
    match_id: str
    condition_id: str | None
    game: str | None
    map_number: int | None
    source: str | None
    side_0_label: str
    side_1_label: str
    team_0: str | None
    team_1: str | None
    yes_is_side_0: bool | None
    outcome_0_name: str | None
    outcome_1_name: str | None


@dataclass(frozen=True)
class DecisionSlice:
    provenance: Provenance
    second: int | None
    server_timestamp: int | None
    phase: str | None
    paused: bool | None
    radiant_nw: int | None
    dire_nw: int | None
    radiant_nw_adv: int | None
    xp_status: XpStatus
    radiant_xp_adv: int | None
    xp_text: str | None
    deaths_radiant: int | None
    deaths_dire: int | None
    top: TopSummary | None
    model_evaluated: bool | None
    raw_delta: float | None
    market_radiant_prior: float | None
    reason: str | None
    entry_block: str | None
    feed_source: str | None
    feed_received_at_utc: str | None
    recorded_at_utc: str | None
    journal_path: Path
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SeriesScore:
    name_0: str | None
    name_1: str | None
    score_0: int | None
    score_1: int | None


@dataclass(frozen=True)
class BoardSlice:
    second: int | None
    clock_ticking: bool | None
    status: str | None
    paused: bool | None
    kills_0: int | None
    kills_1: int | None
    series: SeriesScore | None
    received_at_utc: str | None
    updated_at: str | None
    archive_path: Path | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class PlayerSummary:
    nick: str | None
    nick_compact: str | None
    hero: str | None
    hero_compact: str | None
    net_worth: int | None
    kills: int | None
    deaths: int | None
    assists: int | None
    level: int | None
    alive: bool | None
    respawn: int | None
    aegis: bool | None


@dataclass(frozen=True)
class SideSlice:
    label: str
    team_name: str | None
    gold: int | None
    players_gold: int | None
    players_deaths: int | None
    players: tuple[PlayerSummary, ...]
    complete: bool


@dataclass(frozen=True)
class Objectives:
    towers: int | None
    barracks: int | None
    roshans: int | None


@dataclass(frozen=True)
class TableSlice:
    second: int | None
    second_reconstructed: bool
    received_at_utc: str | None
    updated_at: str | None
    feed_delay: int | None
    board_received_at_utc: str | None
    archive_path: Path | None
    side_0: SideSlice
    side_1: SideSlice
    objectives_0: Objectives | None
    objectives_1: Objectives | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class SourceFacts:
    continuity: Continuity | None
    complete: bool
    evidence: tuple[str, ...]
    control: tuple[str, ...]
    rejected: int
    terminal: bool
    legacy_replay: bool
    comparison: Comparison


@dataclass(frozen=True)
class GameSummary:
    identity: GameIdentity
    decision: DecisionSlice
    board: BoardSlice | None
    table: TableSlice | None
    source: SourceFacts
    decision_label: str
    archive_label: str


def compact(value: str | None) -> str | None:
    if value is None or len(value) <= COMPACT_LIMIT:
        return value
    return value[: COMPACT_LIMIT - 1] + "…"


def side_slice(
    label: str,
    team_name: str | None,
    gold: int | None,
    players: tuple[PlayerSummary, ...],
) -> SideSlice:
    nws = [player.net_worth for player in players]
    deaths = [player.deaths for player in players]
    players_gold = (
        sum(value for value in nws if value is not None)
        if nws and all(value is not None for value in nws)
        else None
    )
    players_deaths = (
        sum(value for value in deaths if value is not None)
        if deaths and all(value is not None for value in deaths)
        else None
    )
    return SideSlice(
        label=label,
        team_name=team_name,
        gold=gold,
        players_gold=players_gold,
        players_deaths=players_deaths,
        players=players,
        complete=len(players) == 5 and all(p.nick is not None for p in players),
    )
