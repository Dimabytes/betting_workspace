"""Shapes of the GRID widget live-socket frames read by watch_grid_live.

The socket is `wss://api.grid.gg/widgets-v2/live/<seriesId>`; it is the feed the
Polymarket match page renders. Derived from live captures of series 2995964
with `isCompressionEnabled=false`, so the inner payload is a JSON string.

Static types only: `cast()` at the read boundary, no runtime validation.
"""

from typing import NotRequired, TypedDict


class GridStateArchiveRecord(TypedDict):
    """One grid_state.jsonl record: local receipt stamp and the raw socket text."""

    received_at_utc: str
    frame: str


class WidgetDataItem(TypedDict):
    """One payload slot of a frame. `data` is a JSON document as a string."""

    data: str
    isCompressed: bool


class WidgetScope(TypedDict):
    id: str
    type: str  # "series", or "" when the service has nothing to send


class WidgetFrame(TypedDict):
    """One socket frame. `service` is the event name with an `integrity_safe_` prefix."""

    service: str
    delay: int  # seconds this service lags the live game
    scope: WidgetScope
    data: list[WidgetDataItem]  # empty when the service has no state yet


class ScoreboardClock(TypedDict):
    """The in-game clock of one map. `occurredAt` stamps when GRID saw it."""

    isTicking: bool
    tickingBackwards: bool
    currentSeconds: int
    color: str
    occurredAt: str  # ISO 8601 with milliseconds and a Z suffix
    publishDelay: int


class ScoreboardInfoText(TypedDict):
    text: str  # "RADIANT" or "DIRE"
    color: str


class ScoreboardGameTeam(TypedDict):
    id: str
    score: int  # kills
    won: bool
    infoText: ScoreboardInfoText


class ScoreboardGame(TypedDict):
    mapName: str
    status: str  # "upcoming" | "live" | "finished"
    centeredInfoText: str  # "Game 1"
    teams: list[ScoreboardGameTeam]
    gameClock: ScoreboardClock


class ScoreboardSeriesTeam(TypedDict):
    id: str
    name: str
    logoUrl: str
    teamColor: str  # "teams.<id>"
    score: int  # maps won
    won: bool


class ScoreboardSeries(TypedDict):
    startTimeDate: str
    endTimeDate: str  # "" while the series runs
    status: str  # "upcoming" | "live" | "finished"
    format: str  # "best-of-3"
    title: str  # "dota"
    teams: list[ScoreboardSeriesTeam]


class ScoreboardTournament(TypedDict):
    name: str


class ScoreboardPayload(TypedDict):
    """Payload of `series_scoreboard_v2`, the zero-delay clock and score."""

    seriesId: str
    activeGameIndex: int
    tournament: ScoreboardTournament
    series: ScoreboardSeries
    games: list[ScoreboardGame]


class TableCell(TypedDict):
    """One numeric cell of a stats table. `value` is null before the game starts."""

    value: int | None
    teamColor: str | None


class TableEntity(TypedDict):
    """The row label: player nick, owning team and the hero portrait URL.

    `iconUrl` is absent on some live frames; it is display-only (hero name).
    """

    value: str
    hexColor: str
    teamColor: str  # "teams.<id>"
    iconUrl: NotRequired[str]


class TableRow(TypedDict):
    """One player row. Only the columns watch_grid_live prints are typed.

    `increaseLevel` counts the level-ups of this map, so the current hero level
    is that count plus the level 1 every hero spawns with. GRID omits the whole
    column until the first level-up of the map, so it is NotRequired.
    """

    entity: TableEntity
    NetWorth: TableCell
    Kills: TableCell
    Deaths: TableCell
    KillAssistsGiven: TableCell
    increaseLevel: NotRequired[TableCell]


class Table(TypedDict):
    tableRows: list[TableRow]
    totalRow: TableRow


class TableEntityGroup(TypedDict):
    name: str  # "Player" | "Teams" | "Team Summary" | "General"
    tables: list[Table]


class TableState(TypedDict):
    sequenceNumber: int  # the map number, 1-based
    entityGroups: list[TableEntityGroup]


class TableStateGroup(TypedDict):
    name: str  # "Series" | "Game"
    states: list[TableState]


class SeriesTablePayload(TypedDict):
    """Payload of `series_table`, 8s behind, carrying per-player net worth."""

    Id: str
    stateGroups: list[TableStateGroup]
