"""Oddin catalog cards and scoreboard shapes."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

DOTA_SPORT = "od:sport:2"
LOL_SPORT = "od:sport:1"
FINISHED_STATUS = "FINISHED"
VALID_DATA = "VALID_DATA"
RADIANT = "RADIANT"
DIRE = "DIRE"


class OddinFeedError(ValueError):
    """Catalog, widget config, GraphQL, or a socket frame failed."""


class ScoreboardSilent(Exception):
    """The socket is up, but no scoreboard payload arrived within the stale window."""


@dataclass(frozen=True)
class OddinMatch:
    """One Dota match row from Disir `dota2TournamentInfo`."""

    id: str
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    is_closed: bool
    start_timestamp: str
    end_timestamp: str
    tournament_id: int
    tournament_name: str

    def phase(self) -> str:
        """`not_started`, `live`, or `finished` from start and `isClosed`."""
        if self.is_closed:
            return "finished"
        if self.start_timestamp:
            return "live"
        return "not_started"


@dataclass(frozen=True)
class OddinTournament:
    """One Dota tournament and the matches Disir returned with it."""

    numeric_id: int
    name: str
    starts_at: datetime
    ends_at: datetime
    matches: tuple[OddinMatch, ...]


@dataclass(frozen=True)
class WidgetConfig:
    """Disir brand token and the base64 GraphQL match id."""

    brand_token: str
    match_id: str


@dataclass(frozen=True)
class PlayerTick:
    """One player's scoreboard row."""

    nickname: str
    hero: str
    net_worth: int
    kills: int
    deaths: int
    assists: int
    alive: bool
    respawn_timer: int | None
    has_aegis: bool


@dataclass(frozen=True)
class TeamTick:
    """One faction on the current map."""

    name: str
    kills: int
    net_worth: int
    towers: int
    barracks: int
    roshans: int
    players: tuple[PlayerTick, ...]


@dataclass(frozen=True)
class OddinTick:
    """Projected scoreboard snapshot. `radiant`/`dire` are None before a map."""

    match_status: str
    data_status: str
    last_updated_at: str
    map_paused: bool
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    map_order: int | None
    game_time: int | None
    radiant: TeamTick | None
    dire: TeamTick | None

    @property
    def net_worth_lead(self) -> int | None:
        """Radiant net worth minus Dire net worth, or None before a map."""
        if self.radiant is None or self.dire is None:
            return None
        return self.radiant.net_worth - self.dire.net_worth


@dataclass(frozen=True)
class SocketFrame:
    """One graphql-transport-ws message."""

    type: str
    id: str | None
    payload: object


@dataclass(frozen=True)
class SocketAction:
    """Watcher side-effect for one protocol frame."""

    send_subscribe: bool
    send_pong: bool
    pong_payload: object
    envelope: str | None
    reconnect: bool
    stream_complete: bool


class OddinStateArchiveRecord(TypedDict):
    """One oddin_state.jsonl record: local receipt, event name, decrypted payload.

    `event` is `snapshot` (HTTP seed, not reduced), `ws`, or `reconnect`.
    Reconnect uses a JSON-null payload.
    """

    received_at_utc: str
    event: str
    payload: object
