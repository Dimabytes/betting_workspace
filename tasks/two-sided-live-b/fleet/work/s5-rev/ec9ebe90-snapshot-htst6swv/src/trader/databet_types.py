"""Thunderpick match cards and DATA.BET scoreboard shapes."""

from dataclasses import dataclass

DOTA_GAME_ID = 4

TERMINAL_STATUSES = frozenset({"ENDED", "CANCELLED", "CLOSED", "FINISHED"})


class DatabetError(ValueError):
    """Thunderpick API, DATA.BET GraphQL, or WebSocket protocol error."""


@dataclass(frozen=True)
class ThunderpickTeam:
    """Team name on a Thunderpick match card."""

    id: int
    name: str


@dataclass(frozen=True)
class ThunderpickMatch:
    """Match card from GET /api/matches/<id> or the POST /api/matches live list.

    `databet_widget_jwt` is only set by the detail endpoint; list rows leave it empty.
    """

    id: int
    game_id: int
    name: str
    is_live: bool
    home: ThunderpickTeam
    away: ThunderpickTeam
    competition: str
    home_score: int
    away_score: int
    best_of: int
    databet_widget_available: bool
    databet_widget_jwt: str


@dataclass(frozen=True)
class ThunderpickJwt:
    """Decoded DATA.BET widget token."""

    token: str
    event_id: str
    expires_at: int


@dataclass(frozen=True)
class DatabetEventInfo:
    """GetEventInfo result: sport, widget type names, scoreboard feature scopes."""

    sport: str
    widgets: tuple[str, ...]
    scopes: dict[str, bool]


@dataclass(frozen=True)
class DatabetPlayerKda:
    """Player kills/deaths/assists."""

    kills: int
    deaths: int
    assists: int


@dataclass(frozen=True)
class DatabetPlayerStats:
    """Player stats; `status` is ALIVE/DEAD."""

    level: int
    networth: int
    health_max: int
    health: int
    status: str
    disconnected: bool


@dataclass(frozen=True)
class DatabetPlayer:
    """One player's scoreboard row; `hero_id` is the Valve hero id as a string."""

    id: str
    name: str
    hero_id: str
    kda: DatabetPlayerKda
    stats: DatabetPlayerStats


@dataclass(frozen=True)
class DatabetTeamStatistics:
    """Team-level statistics."""

    players_killed: int
    towers_killed: int
    barracks_killed: int
    net_worth: int


@dataclass(frozen=True)
class DatabetTeam:
    """One team's scoreboard data; `side` is RADIANT/DIRE."""

    id: str
    name: str
    side: str
    players: tuple[DatabetPlayer, ...]
    statistics: DatabetTeamStatistics


@dataclass(frozen=True)
class DatabetTeamScore:
    """One entry of scores.total: series maps won per team."""

    team_id: str
    score: int


@dataclass(frozen=True)
class DatabetSnapshot:
    """Full Dota2Snapshot payload."""

    status: str
    start_time: str
    best_of: int
    map_number: int
    map_time_ms: int
    scores: tuple[DatabetTeamScore, ...]
    teams: tuple[DatabetTeam, ...]


@dataclass(frozen=True)
class DatabetTick:
    """Projected scoreboard tick. `radiant`/`dire` are None before a map starts;
    `home_score`/`away_score` are None when a team name cannot be matched."""

    match_status: str
    map_number: int | None
    game_time: int | None
    home_score: int | None
    away_score: int | None
    radiant: DatabetTeam | None
    dire: DatabetTeam | None

    @property
    def net_worth_lead(self) -> int | None:
        """Radiant net worth minus Dire net worth, or None before a map."""
        if self.radiant is None or self.dire is None:
            return None
        return self.radiant.statistics.net_worth - self.dire.statistics.net_worth
