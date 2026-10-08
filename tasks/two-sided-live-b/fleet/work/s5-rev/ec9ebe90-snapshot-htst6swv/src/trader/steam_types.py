"""Shapes of the Steam Web API live-match payloads, plus the OpenDota live row.

Only the fields the live watcher reads are typed here; add a field when you start
using it rather than mirroring the whole Valve schema. `radiant_team`, `buildings`
and `graph_data` are absent before the horn, so they are NotRequired.

These are static types only: `cast()` at the read boundary, no runtime validation.
"""

from typing import NotRequired, TypedDict


class SteamTeamName(TypedDict):
    """Team block of GetLiveLeagueGames: the display name only."""

    team_name: str


class SteamScoreboard(TypedDict):
    """Scoreboard block of GetLiveLeagueGames: the game clock only."""

    duration: float


class SteamLeagueGame(TypedDict):
    """One ticketed league game advertised by GetLiveLeagueGames.

    The three series fields are optional in the raw response shape: a missing
    or bad live row is skipped, and a usable discovery row validates the
    required values before projection. `series_type` is absent when Steam
    reports no series context; only exact 0/1/2 (Bo1/Bo3/Bo5) may authorize a
    `series_winner` market, and only on the series last map.
    """

    match_id: int
    league_id: int
    stream_delay_s: int
    radiant_team: NotRequired[SteamTeamName]
    dire_team: NotRequired[SteamTeamName]
    scoreboard: NotRequired[SteamScoreboard]
    series_type: NotRequired[int]
    radiant_series_wins: NotRequired[int]
    dire_series_wins: NotRequired[int]


class SteamLeagueGamesResult(TypedDict):
    """Result envelope of GetLiveLeagueGames."""

    games: list[SteamLeagueGame]


class SteamLiveLeagueGamesResponse(TypedDict):
    """Top level of GetLiveLeagueGames."""

    result: SteamLeagueGamesResult


class SteamTopLiveGame(TypedDict):
    """One game from GetTopLiveGame; the only Valve source of `server_steam_id`.

    `match_id` is a decimal string on the wire (measured 2026-08-15). The
    discovery reader accepts that string or a JSON number.
    """

    match_id: int | str
    league_id: int
    server_steam_id: str


class SteamTopLiveGameResponse(TypedDict):
    """Top level of GetTopLiveGame."""

    game_list: list[SteamTopLiveGame]


class OpenDotaLiveGame(TypedDict):
    """One row of OpenDota /live; ids arrive as strings there."""

    match_id: str
    league_id: int
    server_steam_id: str


class SteamRealtimePlayer(TypedDict):
    """One player in GetRealtimeStats."""

    accountid: int
    name: str
    team: int
    heroid: int
    level: int
    kill_count: int
    death_count: int
    assists_count: int
    lh_count: int
    denies_count: int
    gold: int
    net_worth: int


class SteamRealtimeTeam(TypedDict):
    """One side in GetRealtimeStats; team_number 2 is Radiant, 3 is Dire."""

    team_number: int
    score: int
    net_worth: int
    players: list[SteamRealtimePlayer]
    team_name: NotRequired[str]


class SteamRealtimeBuilding(TypedDict):
    """One building in GetRealtimeStats; type 0 is a tower."""

    team: int
    type: int
    lane: int
    tier: int
    destroyed: bool


class SteamRealtimeMatch(TypedDict):
    """Match header of GetRealtimeStats.

    `start_timestamp` is Unix seconds of the lobby/server start.
    `timestamp` is seconds since that start and ticks every second no matter
    what; it is not Unix. `game_time` is the horn clock and freezes on a pause,
    so a growing gap between `timestamp` and `game_time` means the game is
    paused. Horn Unix is `start_timestamp + timestamp - game_time`. `match_id`
    is null on a live draft and on a dead server.
    """

    match_id: str | None
    league_id: int
    start_timestamp: int
    timestamp: int
    game_time: int
    game_state: int


class SteamRealtimeGraphData(TypedDict):
    """Gold-lead history: a fixed 128-point downsample of the match so far.

    Length stays 128 (measured at game_time 186 and 680), so the resolution
    degrades as the match runs. Use it for backfill on a late join, not as a
    time series.
    """

    graph_gold: list[int]


class SteamRealtimeStats(TypedDict):
    """Top level of GetRealtimeStats."""

    match: SteamRealtimeMatch
    teams: list[SteamRealtimeTeam]
    buildings: NotRequired[list[SteamRealtimeBuilding]]
    graph_data: NotRequired[SteamRealtimeGraphData]
