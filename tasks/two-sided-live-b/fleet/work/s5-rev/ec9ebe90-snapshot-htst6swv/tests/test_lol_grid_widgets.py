"""GRID parser contract on compact LoL payloads from the 2026-08-29 recordings."""

from lol_grid_widget_fixtures import (
    CBLOL_SCOREBOARD,
    CBLOL_SERIES_ID,
    CBLOL_SERIES_TABLE,
    CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP,
    FLUXO,
    GIANTX,
    LEC_SCOREBOARD,
    LEC_SERIES_ID,
    LEC_SERIES_TABLE,
    LOS,
    NAVI,
    player_rows,
    wrap,
)

from trader.grid_widgets import (
    GAME_STATE_GROUP,
    PLAYER_ENTITY_GROUP,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
    select_side_players,
)

PLAYER_COLUMNS = ("NetWorth", "Kills", "Deaths", "KillAssistsGiven")


def test_cblol_scoreboard_has_exactly_blue_and_red() -> None:
    frame = parse_frame(wrap("series_scoreboard_v2", 0, CBLOL_SCOREBOARD, CBLOL_SERIES_ID))
    assert frame.service == "series_scoreboard_v2"
    assert frame.delay == 0
    board = read_map_scoreboard(frame.payload, 1)
    assert board is not None
    assert len(board.teams) == 2
    sides = {team.side: team for team in board.teams}
    assert set(sides) == {"BLUE", "RED"}
    assert sides["BLUE"].name == "Fluxo W7M"
    assert sides["BLUE"].team_id == FLUXO
    assert sides["BLUE"].kills == 4
    assert sides["RED"].name == "LOS"
    assert sides["RED"].team_id == LOS
    assert sides["RED"].kills == 6


def test_lec_scoreboard_map_two_has_exactly_blue_and_red() -> None:
    frame = parse_frame(wrap("series_scoreboard_v2", 0, LEC_SCOREBOARD, LEC_SERIES_ID))
    board = read_map_scoreboard(frame.payload, 2)
    assert board is not None
    assert board.game_number == 2
    assert len(board.teams) == 2
    sides = {team.side: team for team in board.teams}
    assert set(sides) == {"BLUE", "RED"}
    assert sides["BLUE"].name == "GIANTX"
    assert sides["BLUE"].team_id == GIANTX
    assert sides["RED"].name == "Natus Vincere"
    assert sides["RED"].team_id == NAVI


def test_cblol_table_has_ten_players_and_expected_sums() -> None:
    frame = parse_frame(wrap("series_table", 8, CBLOL_SERIES_TABLE, CBLOL_SERIES_ID))
    snapshot = read_net_worth(frame.payload, frame.delay)
    assert snapshot is not None
    assert snapshot.feed_delay == 8
    assert len(snapshot.players) == 10
    assert snapshot.players[0].nick == "Momochi"
    assert snapshot.players[0].hero == ""
    assert (snapshot.players[0].kills, snapshot.players[0].deaths, snapshot.players[0].assists) == (
        0,
        2,
        1,
    )
    assert len({player.team_id for player in snapshot.players}) == 2
    fluxo = select_side_players(snapshot, FLUXO)
    los = select_side_players(snapshot, LOS)
    assert fluxo is not None
    assert los is not None
    assert sum(player.net_worth for player in fluxo) == 2588 + 4824 + 4472 + 5522 + 5039
    assert sum(player.net_worth for player in los) == 4615 + 2677 + 4601 + 4064 + 5494
    assert sum(player.net_worth for player in snapshot.players) == 43896
    assert sum(player.deaths for player in snapshot.players) == 10


def test_lec_table_has_ten_players_and_expected_sums() -> None:
    frame = parse_frame(wrap("series_table", 8, LEC_SERIES_TABLE, LEC_SERIES_ID))
    snapshot = read_net_worth(frame.payload, frame.delay)
    assert snapshot is not None
    assert snapshot.game_number == 2
    assert len(snapshot.players) == 10
    assert len({player.team_id for player in snapshot.players}) == 2
    navi = select_side_players(snapshot, NAVI)
    giantx = select_side_players(snapshot, GIANTX)
    assert navi is not None
    assert giantx is not None
    assert sum(player.net_worth for player in navi) == 829 + 836 + 904 + 1013 + 708
    assert sum(player.net_worth for player in giantx) == 776 + 620 + 910 + 862 + 602
    assert sum(player.net_worth for player in snapshot.players) == 8060
    assert sum(player.deaths for player in snapshot.players) == 0


def test_lol_table_groups_match_parser_constants() -> None:
    group_names = {group["name"] for group in CBLOL_SERIES_TABLE["stateGroups"]}
    assert GAME_STATE_GROUP in group_names
    game = next(
        group for group in CBLOL_SERIES_TABLE["stateGroups"] if group["name"] == GAME_STATE_GROUP
    )
    entity_names = {group["name"] for group in game["states"][0]["entityGroups"]}
    assert PLAYER_ENTITY_GROUP in entity_names
    snapshot = read_net_worth(
        parse_frame(wrap("series_table", 8, CBLOL_SERIES_TABLE, CBLOL_SERIES_ID)).payload, 8
    )
    assert snapshot is not None


def test_lol_player_rows_carry_net_worth_kda_and_team_color() -> None:
    rows = player_rows(CBLOL_SERIES_TABLE)
    assert len(rows) == 10
    for row in rows:
        for column in PLAYER_COLUMNS:
            assert column in row
        team_id = row["entity"]["teamColor"].rsplit(".", 1)[-1]
        assert row["entity"]["teamColor"] == f"teams.{team_id}"
        assert team_id in {FLUXO, LOS}


def test_increase_level_is_absent_before_first_level_up() -> None:
    rows = player_rows(CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP)
    assert all("increaseLevel" not in row for row in rows)
    frame = parse_frame(
        wrap("series_table", 8, CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP, CBLOL_SERIES_ID)
    )
    snapshot = read_net_worth(frame.payload, frame.delay)
    assert snapshot is not None
    assert len(snapshot.players) == 10
    assert all(player.level == 1 for player in snapshot.players)
    assert sum(player.net_worth for player in snapshot.players) == 4800


def test_increase_level_is_present_after_first_level_up() -> None:
    rows = player_rows(CBLOL_SERIES_TABLE)
    assert all("increaseLevel" in row for row in rows)
    frame = parse_frame(wrap("series_table", 8, CBLOL_SERIES_TABLE, CBLOL_SERIES_ID))
    snapshot = read_net_worth(frame.payload, frame.delay)
    assert snapshot is not None
    by_nick = {player.nick: player for player in snapshot.players}
    assert by_nick["Momochi"].level == 5
    assert by_nick["Zothve"].level == 10
    assert by_nick["Peach"].level == 9
