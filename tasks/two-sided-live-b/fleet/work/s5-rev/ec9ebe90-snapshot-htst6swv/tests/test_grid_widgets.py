"""Parsing of GRID widget frames, on payloads recorded from series 2995964."""

import json
from datetime import UTC, datetime
from typing import cast

from grid_widget_fixtures import (
    KLIM,
    LYNX,
    SCOREBOARD,
    SERIES_TABLE,
    copy_series_table,
    foreign_table,
    make_portraitless_row,
    make_row,
    table_rows,
    table_with_extra_row,
    wrap,
)

from trader.grid_widget_types import ScoreboardPayload, SeriesTablePayload, TableCell
from trader.grid_widgets import (
    build_socket_url,
    clock_age_seconds,
    clock_stamp_unix_seconds,
    hero_from_icon,
    live_clock_seconds,
    map_number_from_scoreboard,
    parse_frame,
    read_current_scoreboard,
    read_map_scoreboard,
    read_net_worth,
    read_scoreboard,
    same_table_content,
    select_side_players,
)


def test_socket_url_asks_for_zero_delay_and_plain_json() -> None:
    url = build_socket_url("2995964")
    assert url.startswith("wss://api.grid.gg/widgets-v2/live/2995964?")
    assert "delay=zero" in url
    assert "isCompressionEnabled=false" in url
    assert "events=series_scoreboard_v2,series_table" in url


def test_empty_service_frame_yields_no_payload() -> None:
    raw = json.dumps(
        {
            "service": "integrity_safe_series_table",
            "delay": 8,
            "scope": {"id": "2995964", "type": ""},
            "data": [],
        }
    )
    frame = parse_frame(raw)
    assert frame.service == "series_table"
    assert frame.delay == 8
    assert frame.payload == ""


def test_scoreboard_gives_clock_kills_and_sides() -> None:
    frame = parse_frame(wrap("series_scoreboard_v2", 0, SCOREBOARD))
    assert frame.service == "series_scoreboard_v2"
    assert frame.delay == 0
    board = read_scoreboard(frame.payload)
    assert board is not None
    assert board.clock_seconds == 2647
    assert board.clock_ticking is True
    assert board.game_number == 1
    assert board.active_game_number == 1
    assert board.game_label == "Game 1"
    assert board.series_status == "live"
    assert board.series_format == "best-of-3"
    assert board.publish_delay == 1
    sides = {team.side: team for team in board.teams}
    assert sides["RADIANT"].name == "Team Lynx"
    assert sides["RADIANT"].kills == 22
    assert sides["DIRE"].name == "Klim Sani4"
    assert sides["DIRE"].kills == 24
    assert sides["DIRE"].maps_won == 1
    assert sides["DIRE"].won is True
    assert sides["RADIANT"].won is False


def test_scoreboard_without_games_returns_none() -> None:
    board = read_scoreboard(json.dumps({**SCOREBOARD, "games": []}))
    assert board is None


def test_read_map_scoreboard_returns_a_non_active_map() -> None:
    game_two = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))["games"][0]
    game_two["centeredInfoText"] = "Game 2"
    game_two["gameClock"]["currentSeconds"] = 100
    two_maps = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    two_maps["activeGameIndex"] = 1
    two_maps["games"].append(game_two)
    board = read_map_scoreboard(json.dumps(two_maps), 1)
    assert board is not None
    assert board.game_number == 1
    assert board.active_game_number == 2
    assert board.clock_seconds == 2647
    assert board.game_label == "Game 1"
    active = read_scoreboard(json.dumps(two_maps))
    assert active is not None
    assert active.game_number == 2
    assert active.clock_seconds == 100


def test_read_map_scoreboard_returns_none_when_the_index_is_missing() -> None:
    assert read_map_scoreboard(json.dumps(SCOREBOARD), 2) is None


def _scoreboard_copy() -> ScoreboardPayload:
    """Deep-copy the recorded scoreboard payload."""
    return cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))


def test_map_number_from_live_scoreboard_is_the_active_game() -> None:
    """A live active map is that game's 1-based index."""
    board = read_scoreboard(json.dumps(SCOREBOARD))
    assert board is not None
    assert map_number_from_scoreboard(board) == 1


def test_map_number_from_finished_tied_maps_is_the_next_map() -> None:
    """Finished Game 2 at 1-1 is map 3, even if GRID has not switched the active index."""
    payload = _scoreboard_copy()
    payload["games"][0]["status"] = "finished"
    payload["series"]["teams"][0]["score"] = 1
    payload["series"]["teams"][1]["score"] = 1
    board = read_scoreboard(json.dumps(payload))
    assert board is not None
    assert map_number_from_scoreboard(board) == 3


def test_map_number_from_finished_series_is_none() -> None:
    """A finished series does not invent a next map."""
    payload = _scoreboard_copy()
    payload["series"]["status"] = "finished"
    board = read_scoreboard(json.dumps(payload))
    assert board is not None
    assert map_number_from_scoreboard(board) is None


def _finished_map_one_with_upcoming_map_two() -> ScoreboardPayload:
    """Map 1 finished 1-0, GRID still on index 0, map 2 upcoming with empty teams."""
    payload = _scoreboard_copy()
    payload["games"][0]["status"] = "finished"
    payload["series"]["teams"][0]["score"] = 1
    payload["series"]["teams"][1]["score"] = 0
    game_two = cast(ScoreboardPayload, json.loads(json.dumps(payload)))["games"][0]
    game_two["status"] = "upcoming"
    game_two["centeredInfoText"] = "Game 2"
    game_two["teams"] = []
    game_two["gameClock"]["currentSeconds"] = 0
    payload["games"].append(game_two)
    return payload


def test_read_current_scoreboard_keeps_a_live_active_game() -> None:
    """A live active map is that game, same as `read_scoreboard`."""
    board = read_current_scoreboard(json.dumps(SCOREBOARD))
    assert board is not None
    assert board.game_number == 1
    assert board.game_status == "live"


def test_read_current_scoreboard_keeps_an_upcoming_active_game() -> None:
    """An upcoming active game is the current map even before sides appear."""
    payload = _scoreboard_copy()
    payload["games"][0]["status"] = "upcoming"
    payload["games"][0]["teams"] = []
    board = read_current_scoreboard(json.dumps(payload))
    assert board is not None
    assert board.game_number == 1
    assert board.game_status == "upcoming"
    assert board.teams == ()


def test_read_current_scoreboard_advances_past_a_finished_active_game() -> None:
    """Finished map 1 while the series is live is map 2, including an empty upcoming slot."""
    board = read_current_scoreboard(json.dumps(_finished_map_one_with_upcoming_map_two()))
    assert board is not None
    assert board.game_number == 2
    assert board.game_status == "upcoming"
    assert board.teams == ()


def test_read_current_scoreboard_returns_none_when_the_next_map_slot_is_missing() -> None:
    """Finished map 1 with maps_won=1 and no games[1] is not a current board."""
    payload = _scoreboard_copy()
    payload["games"][0]["status"] = "finished"
    payload["series"]["teams"][0]["score"] = 1
    payload["series"]["teams"][1]["score"] = 0
    assert read_current_scoreboard(json.dumps(payload)) is None


def test_read_current_scoreboard_returns_none_when_the_series_is_not_live() -> None:
    """A finished series has no current map even if games remain on the payload."""
    payload = _scoreboard_copy()
    payload["series"]["status"] = "finished"
    assert read_current_scoreboard(json.dumps(payload)) is None


def test_series_table_gives_five_net_worths_per_side() -> None:
    frame = parse_frame(wrap("series_table", 8, SERIES_TABLE))
    snapshot = read_net_worth(frame.payload, frame.delay)
    assert snapshot is not None
    assert snapshot.game_number == 1
    assert snapshot.feed_delay == 8
    assert len(snapshot.players) == 10
    assert len([p for p in snapshot.players if p.team_id == KLIM]) == 5
    assert len([p for p in snapshot.players if p.team_id == LYNX]) == 5
    top = snapshot.players[0]
    assert top.nick == "423"
    assert top.hero == "life stealer"
    assert top.has_portrait is True
    assert top.net_worth == 30784
    assert (top.kills, top.deaths, top.assists) == (8, 3, 6)
    # GRID counts level-ups, so 22 of them is a level 23 hero.
    assert top.level == 23
    klim = select_side_players(snapshot, KLIM)
    lynx = select_side_players(snapshot, LYNX)
    assert klim is not None
    assert lynx is not None
    assert sum(player.net_worth for player in klim) == 30784 + 26369 + 14776 + 13263 + 26438
    assert sum(player.net_worth for player in lynx) == 23074 + 22479 + 22279 + 10884 + 8641


def test_select_side_players_drops_a_portraitless_sixth_row() -> None:
    """A roster substitute is an extra row with no portrait: it drops, five stay."""
    ghost = make_portraitless_row(KLIM, "JANTER", 600)
    snapshot = read_net_worth(json.dumps(table_with_extra_row(ghost)), 8)
    assert snapshot is not None
    klim = select_side_players(snapshot, KLIM)
    assert klim is not None
    assert len(klim) == 5
    assert all(player.nick != "JANTER" for player in klim)
    assert sum(player.net_worth for player in klim) == 30784 + 26369 + 14776 + 13263 + 26438


def test_select_side_players_rejects_six_rows_with_portraits() -> None:
    """Six rows that all carry portraits cannot be narrowed: the side is unreadable."""
    extra = make_row(KLIM, "standin", "pudge", 600, 0, 0, 0, 0)
    snapshot = read_net_worth(json.dumps(table_with_extra_row(extra)), 8)
    assert snapshot is not None
    assert select_side_players(snapshot, KLIM) is None
    assert select_side_players(snapshot, LYNX) is not None


def test_select_side_players_keeps_five_portraitless_rows() -> None:
    """Portraits only decide above five rows; a portraitless five still counts."""
    payload = copy_series_table()
    for row in table_rows(payload):
        del row["entity"]["iconUrl"]
    snapshot = read_net_worth(json.dumps(payload), 8)
    assert snapshot is not None
    klim = select_side_players(snapshot, KLIM)
    assert klim is not None
    assert len(klim) == 5


def test_select_side_players_rejects_a_foreign_table() -> None:
    """The degenerate table is one NW-0 row under a foreign team id: both sides are empty."""
    snapshot = read_net_worth(json.dumps(foreign_table()), 8)
    assert snapshot is not None
    assert select_side_players(snapshot, KLIM) is None
    assert select_side_players(snapshot, LYNX) is None


def test_null_cells_before_the_map_starts_read_as_zero() -> None:
    empty = make_row(KLIM, "423", "life-stealer", 0, 0, 0, 0, 0)
    blank: TableCell = {"value": None, "teamColor": None}
    empty["NetWorth"] = blank
    empty["Kills"] = blank
    empty["Deaths"] = blank
    empty["KillAssistsGiven"] = blank
    empty["increaseLevel"] = blank
    payload = copy_series_table()
    table_rows(payload)[:] = [empty]
    snapshot = read_net_worth(json.dumps(payload), 8)
    assert snapshot is not None
    assert snapshot.players[0].net_worth == 0
    assert snapshot.players[0].kills == 0
    assert snapshot.players[0].level == 1


def test_absent_level_column_before_the_first_level_up_reads_as_level_one() -> None:
    """GRID omits the whole increaseLevel column until a hero levels up on this map.

    Observed live on 2026-08-25 series 2998620: the draft-minute table carried
    every other column and no increaseLevel, which used to raise KeyError and
    kill the match task before match.json was written.
    """
    row = make_row(KLIM, "423", "life-stealer", 250, 0, 0, 0, 0)
    del row["increaseLevel"]
    payload = copy_series_table()
    table_rows(payload)[:] = [row]
    snapshot = read_net_worth(json.dumps(payload), 8)
    assert snapshot is not None
    assert snapshot.players[0].level == 1
    assert snapshot.players[0].net_worth == 250


def test_missing_icon_url_keeps_net_worth_and_empty_hero() -> None:
    """A live row without a hero portrait still yields gold; hero is blank."""
    payload = copy_series_table()
    row = table_rows(payload)[0]
    del row["entity"]["iconUrl"]
    table_rows(payload)[:] = [row]
    snapshot = read_net_worth(json.dumps(payload), 8)
    assert snapshot is not None
    assert snapshot.players[0].nick == "423"
    assert snapshot.players[0].hero == ""
    assert snapshot.players[0].has_portrait is False
    assert snapshot.players[0].net_worth == 30784


def test_empty_nick_row_is_skipped() -> None:
    """A team-total or blank nick in tableRows is not a player."""
    blank = make_row(KLIM, "", "life-stealer", 198987, 46, 46, 114, 182)
    payload = copy_series_table()
    table_rows(payload).append(blank)
    snapshot = read_net_worth(json.dumps(payload), 8)
    assert snapshot is not None
    assert len(snapshot.players) == 10
    assert all(player.nick for player in snapshot.players)


def test_only_empty_nick_rows_yield_no_snapshot() -> None:
    """A table of blank nicks is not a net-worth tick."""
    blank = make_row(KLIM, "", "life-stealer", 0, 0, 0, 0, 0)
    payload = copy_series_table()
    table_rows(payload)[:] = [blank]
    assert read_net_worth(json.dumps(payload), 8) is None


def test_series_table_without_a_game_state_returns_none() -> None:
    payload: SeriesTablePayload = {
        "Id": "2995964",
        "stateGroups": [{"name": "Game", "states": []}],
    }
    assert read_net_worth(json.dumps(payload), 8) is None


def test_same_table_content_ignores_feed_delay() -> None:
    left = read_net_worth(json.dumps(SERIES_TABLE), 8)
    right = read_net_worth(json.dumps(SERIES_TABLE), 60)
    assert left is not None
    assert right is not None
    assert left != right
    assert same_table_content(left, right) is True
    other = read_net_worth(json.dumps(SERIES_TABLE), 8)
    assert other is not None
    mutated = copy_series_table()
    table_rows(mutated)[0]["NetWorth"]["value"] = 1
    changed = read_net_worth(json.dumps(mutated), 8)
    assert changed is not None
    assert same_table_content(other, changed) is False


def test_clock_age_is_the_gap_between_the_grid_stamp_and_now() -> None:
    now = datetime(2026, 8, 24, 11, 7, 54, 653000, tzinfo=UTC)
    assert clock_age_seconds("2026-08-24T11:07:52.653Z", now) == 2.0


def test_clock_stamp_unix_seconds_truncates_the_fraction() -> None:
    assert clock_stamp_unix_seconds("2026-08-24T11:07:52.653Z") == int(
        datetime(2026, 8, 24, 11, 7, 52, 653000, tzinfo=UTC).timestamp()
    )


def test_ticking_clock_is_extrapolated_by_its_own_age() -> None:
    board = read_scoreboard(json.dumps(SCOREBOARD))
    assert board is not None
    assert live_clock_seconds(board, 0.4) == 2647
    assert live_clock_seconds(board, 9.6) == 2657


def test_paused_clock_is_printed_as_published() -> None:
    paused = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    paused["games"][0]["gameClock"]["isTicking"] = False
    board = read_scoreboard(json.dumps(paused))
    assert board is not None
    assert live_clock_seconds(board, 9.6) == 2647


def test_hero_name_comes_from_the_portrait_url() -> None:
    assert hero_from_icon("https://cdn.grid.gg/assets/dota/characters/monkey-king.png") == (
        "monkey king"
    )
    assert hero_from_icon("https://cdn.grid.gg/assets/dota/characters/life stealer.png") == (
        "life stealer"
    )
    assert (
        hero_from_icon(
            "https://cdn.grid.gg/assets/lol/characters/2db51c3e-7bf3-3ea4-94bd-aac8567b0df9.png"
        )
        == ""
    )
