"""Tests for the Steam GetRealtimeStats reducer the viewer replays old archives with."""

from typing import cast

from trader import steam_feed
from trader.live_feed import GameSnapshot, MatchPhase
from trader.steam_types import (
    SteamRealtimeBuilding,
    SteamRealtimePlayer,
    SteamRealtimeStats,
    SteamRealtimeTeam,
)

RADIANT_TEAM_NUMBER = 2
DIRE_TEAM_NUMBER = 3


def build_player(
    team_number: int, level: int, death_count: int, accountid: int
) -> SteamRealtimePlayer:
    """Build one player block of GetRealtimeStats."""
    return cast(
        SteamRealtimePlayer,
        {
            "accountid": accountid,
            "name": f"player-{accountid}",
            "team": team_number,
            "heroid": accountid + 1,
            "level": level,
            "kill_count": 0,
            "death_count": death_count,
            "assists_count": 0,
            "lh_count": 0,
            "denies_count": 0,
            "gold": 0,
            "net_worth": 0,
        },
    )


def build_players(
    team_number: int, level: int, death_count: int, start_accountid: int
) -> list[SteamRealtimePlayer]:
    """Build five players of one side with a uniform level and death count."""
    return [
        build_player(team_number, level, death_count, start_accountid + index) for index in range(5)
    ]


def build_team(
    team_number: int,
    score: int,
    net_worth: int,
    players: list[SteamRealtimePlayer],
) -> SteamRealtimeTeam:
    """Build one side block of GetRealtimeStats."""
    return cast(
        SteamRealtimeTeam,
        {
            "team_number": team_number,
            "score": score,
            "net_worth": net_worth,
            "players": players,
        },
    )


def build_payload(
    game_time: int,
    timestamp: int,
    game_state: int,
    teams: list[SteamRealtimeTeam],
    match_id: str | None = "12345",
    buildings: list[SteamRealtimeBuilding] | None = None,
) -> SteamRealtimeStats:
    """Build one GetRealtimeStats payload around a match header and two sides."""
    payload: dict[str, object] = {
        "match": {
            "match_id": match_id,
            "league_id": 19719,
            "start_timestamp": 1_786_798_842,
            "timestamp": timestamp,
            "game_time": game_time,
            "game_state": game_state,
        },
        "teams": teams,
    }
    if buildings is not None:
        payload["buildings"] = buildings
    return cast(SteamRealtimeStats, payload)


def build_sides(
    radiant_level: int = 5, dire_level: int = 4
) -> tuple[SteamRealtimeTeam, SteamRealtimeTeam]:
    """Build default consistent sides: Radiant 20k/5 deaths-implied, Dire 14k/10."""
    radiant = build_team(
        RADIANT_TEAM_NUMBER,
        score=5,
        net_worth=20_000,
        players=build_players(RADIANT_TEAM_NUMBER, radiant_level, death_count=2, start_accountid=0),
    )
    dire = build_team(
        DIRE_TEAM_NUMBER,
        score=10,
        net_worth=14_000,
        players=build_players(DIRE_TEAM_NUMBER, dire_level, death_count=1, start_accountid=100),
    )
    return radiant, dire


def test_build_game_snapshot_resolves_sides_by_team_number() -> None:
    """Reversing the teams list does not flip Radiant and Dire in the snapshot."""
    radiant, dire = build_sides()
    payload = build_payload(
        game_time=120,
        timestamp=2_000_000_122,
        game_state=5,
        teams=[dire, radiant],
    )

    snapshot = steam_feed.build_game_snapshot(payload, previous_snapshot=None)

    assert snapshot.second == 120
    assert snapshot.server_timestamp == 2_000_000_122
    assert snapshot.phase is MatchPhase.IN_PROGRESS
    assert snapshot.radiant_nw_adv == 6_000
    assert snapshot.radiant_nw == 20_000
    assert snapshot.dire_nw == 14_000
    assert snapshot.radiant_xp_adv == 3_000
    assert snapshot.deaths_radiant == 10
    assert snapshot.deaths_dire == 5
    assert snapshot.paused is True
    assert snapshot.finished is False


def test_build_game_snapshot_computes_top_player_fields() -> None:
    """Top-player fields rank per-player Steam net_worth."""
    radiant_rows = [(500, 4), (1200, 1), (800, 0), (100, 2), (200, 3)]
    dire_rows = [(300, 2), (900, 7), (400, 3), (50, 1), (80, 5)]
    radiant_players: list[SteamRealtimePlayer] = []
    for index, (net_worth, death_count) in enumerate(radiant_rows):
        player = build_player(RADIANT_TEAM_NUMBER, 5, death_count, index)
        player["net_worth"] = net_worth
        radiant_players.append(player)
    dire_players: list[SteamRealtimePlayer] = []
    for index, (net_worth, death_count) in enumerate(dire_rows):
        player = build_player(DIRE_TEAM_NUMBER, 4, death_count, 100 + index)
        player["net_worth"] = net_worth
        dire_players.append(player)
    payload = build_payload(
        game_time=60,
        timestamp=1_000,
        game_state=5,
        teams=[
            build_team(RADIANT_TEAM_NUMBER, score=0, net_worth=2_800, players=radiant_players),
            build_team(DIRE_TEAM_NUMBER, score=0, net_worth=1_730, players=dire_players),
        ],
    )

    snapshot = steam_feed.build_game_snapshot(payload, previous_snapshot=None)

    assert snapshot.top.top1_nw_adv == 300
    assert snapshot.top.radiant_top1_nw_ratio == 1200 / 1600
    assert snapshot.top.dire_top1_nw_ratio == 900 / 830


def test_pause_follows_timestamp_minus_game_time_growth() -> None:
    """Paused is True on the first tick, then tracks offset growth vs the previous one."""
    radiant, dire = build_sides()

    def build_snapshot(
        game_time: int, timestamp: int, previous: GameSnapshot | None
    ) -> GameSnapshot:
        payload = build_payload(
            game_time=game_time, timestamp=timestamp, game_state=5, teams=[radiant, dire]
        )
        return steam_feed.build_game_snapshot(payload, previous)

    first = build_snapshot(game_time=100, timestamp=1_000, previous=None)
    duplicate = build_snapshot(game_time=100, timestamp=1_000, previous=first)
    paused_tick = build_snapshot(game_time=100, timestamp=1_010, previous=duplicate)
    resumed_tick = build_snapshot(game_time=105, timestamp=1_015, previous=paused_tick)

    assert first.paused is True
    assert duplicate.paused is False
    assert paused_tick.paused is True
    assert resumed_tick.paused is False


def test_state_eight_team_showcase_is_not_finished() -> None:
    """Team showcase (8) is a live draft state, not post-game."""
    radiant, dire = build_sides(radiant_level=0, dire_level=0)
    payload = build_payload(game_time=-15, timestamp=908, game_state=8, teams=[radiant, dire])

    snapshot = steam_feed.build_game_snapshot(payload, previous_snapshot=None)

    assert snapshot.finished is False
    assert snapshot.phase is MatchPhase.PRE_MATCH
    assert steam_feed.match_phase(8) is MatchPhase.PRE_MATCH
    assert steam_feed.match_phase(6) is MatchPhase.FINISHED
