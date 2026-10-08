"""Oddin watcher terminal formatting and open-match selection."""

import pytest
from rich.console import Console
from watch_oddin_live import (
    WatchState,
    build_live_view,
    format_tick_lines,
    format_updated,
    next_watch_state,
    select_open_match,
)

from trader.oddin_types import OddinFeedError, OddinMatch, OddinTick, PlayerTick, TeamTick

LIVE = OddinMatch(
    "od:match:3211324",
    "Aurora Gaming",
    "Pipsqueak+4",
    0,
    1,
    False,
    "2026-09-26T12:00:00Z",
    "",
    14906,
    "PGL Wallachia Season 9",
)
OTHER = OddinMatch(
    "od:match:9",
    "Team Spirit",
    "Team Secret",
    0,
    0,
    False,
    "2026-09-26T12:00:00Z",
    "",
    14906,
    "PGL Wallachia Season 9",
)


def test_select_open_match_by_either_team() -> None:
    assert select_open_match((LIVE, OTHER), "aurora") is LIVE
    assert select_open_match((LIVE, OTHER), "PIPSQUEAK") is LIVE
    assert select_open_match((LIVE, OTHER), "secret") is OTHER


def test_ambiguous_selector() -> None:
    with pytest.raises(OddinFeedError, match="ambiguous"):
        select_open_match((LIVE, OTHER), "e")


def test_select_missing_open_match() -> None:
    with pytest.raises(OddinFeedError, match="no open"):
        select_open_match((LIVE,), "Yandex")


def _player(nickname: str, hero: str, net_worth: int) -> PlayerTick:
    return PlayerTick(nickname, hero, net_worth, 5, 2, 4, True, None, False)


def _team(name: str, kills: int, net_worth: int) -> TeamTick:
    return TeamTick(name, kills, net_worth, 4, 0, 1, (_player(name[:4], "Kez", net_worth),))


def _tick() -> OddinTick:
    return OddinTick(
        match_status="LIVE",
        data_status="VALID_DATA",
        last_updated_at="2026-09-19 16:44:12.123456 +0000 UTC",
        map_paused=False,
        home_name="Aurora Gaming",
        away_name="Pipsqueak+4",
        home_score=0,
        away_score=1,
        map_order=2,
        game_time=757,
        radiant=_team("Pipsqueak+4", 12, 35820),
        dire=_team("Aurora Gaming", 9, 34110),
    )


def test_format_updated() -> None:
    assert format_updated("2026-09-19 16:44:12.123456 +0000 UTC") == "2026-09-19T16:44:12.123Z"
    assert format_updated("") == "-"


def test_format_tick_has_clock_score_net_worth_and_players() -> None:
    lines = format_tick_lines(184, 0.214, "ws", _tick())
    assert lines[0].startswith("#184 dt=0.214s source=ws updated=2026-09-19T16:44:12.123Z")
    assert " lag=" in lines[0]
    assert "t=12:37" in lines[1]
    assert "LIVE" in lines[1]
    assert "map=2" in lines[1]
    assert "series=0:1" in lines[1]
    assert "kills=R12:D9" in lines[1]
    assert "nw=R35820:D34110" in lines[1]
    assert "lead=+1710" in lines[1]
    assert "paused=no" in lines[1]
    assert lines[2] == "     R Pipsqueak+4"
    assert "Pips" in lines[3]
    assert "Kez" in lines[3]
    assert "35820" in lines[3]
    assert "5/2/4" in lines[3]
    assert lines[4] == "     D Aurora Gaming"
    assert "objectives R towers=4 rax=0 roshan=1" in lines[6]
    assert "D towers=4 rax=0 roshan=1" in lines[6]


def test_format_tick_without_current_map() -> None:
    tick = OddinTick(
        "LIVE",
        "VALID_DATA",
        "now",
        False,
        "Aurora Gaming",
        "Pipsqueak+4",
        0,
        0,
        None,
        None,
        None,
        None,
    )
    lines = format_tick_lines(1, None, "snapshot", tick)
    assert lines[0].startswith("#1 dt=- source=snapshot")
    assert "t=-" in lines[1]
    assert "home Aurora Gaming" in lines[2]
    assert "away Pipsqueak+4" in lines[3]


def test_pre_horn_and_paused_and_dead_aegis() -> None:
    dead = PlayerTick("kaori", "Hoodwink", 797, 1, 1, 0, False, 12, True)
    radiant = TeamTick("R", 0, 1, 0, 0, 0, (dead,))
    dire = TeamTick("D", 0, 1, 0, 0, 0, (_player("x", "Sven", 1),))
    tick = OddinTick("LIVE", "VALID_DATA", "now", True, "H", "A", 0, 0, 1, -30, radiant, dire)
    lines = format_tick_lines(2, 0.0, "ws", tick)
    assert "t=-0:30" in lines[1]
    assert "paused=yes" in lines[1]
    assert "dead=12" in lines[3]
    assert "aegis" in lines[3]


def test_live_view_renders_dashboard() -> None:
    console = Console(record=True, width=120)
    console.print(build_live_view(7, 0.2, "ws", _tick(), "match-label"))
    text = console.export_text()
    assert "match-label" in text
    assert "Pipsqueak+4" in text
    assert "Kez" in text
    assert "35,820" in text
    assert "+1,710" in text
    assert "12:37" in text
    assert "lag=" in text
    # home_name is Aurora Gaming, which is the Dire team in this tick
    assert "(D)" in text
    assert "(R)" in text


def test_finished_match_line() -> None:
    tick = OddinTick("FINISHED", "VALID_DATA", "now", False, "H", "A", 2, 1, None, None, None, None)
    lines = format_tick_lines(9, 1.5, "ws", tick)
    assert "FINISHED" in lines[1]
    assert "series=2:1" in lines[1]


def test_every_payload_advances_seq_and_real_dt() -> None:
    first, seq1, dt1 = next_watch_state(WatchState(1, None), 10.0)
    second, seq2, dt2 = next_watch_state(first, 10.214)
    assert (seq1, dt1) == (1, None)
    assert seq2 == 2
    assert dt2 == pytest.approx(0.214)
    assert second.seq == 3
