import gzip
import json
import threading
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import dashboard_game_fixtures as fx
import grid_widget_fixtures as gwf
import lol_grid_widget_fixtures as lwf
import pytest

from dashboard import game_grid
from dashboard.game_state import GameStateReader, GameSummary
from dashboard.game_types import Objectives, SeriesScore
from dashboard.tails import RecordTail, TailCache
from trader.grid_widget_types import ScoreboardGame, ScoreboardPayload, TableRow
from trader.paths import GRID_STATE_ARCHIVE_FILENAME
from viewer import game_state_replay

DOTA_RADIANT_NW = 23074 + 22479 + 22279 + 10884 + 8641
DOTA_DIRE_NW = 30784 + 26369 + 14776 + 13263 + 26438
DOTA_RADIANT_DEATHS = 4 + 4 + 5 + 6 + 5
DOTA_DIRE_DEATHS = 3 + 4 + 5 + 7 + 3
DOTA_TABLE_SECOND = 2647 + 2 - 8
ODDIN_RADIANT_NW = sum(5000 + i for i in range(5))
ODDIN_DIRE_NW = sum(7000 + i for i in range(5))
ODDIN_RADIANT_GOLD = ODDIN_RADIANT_NW + fx.ODDIN_TEAM_GOLD_PAD
ODDIN_DIRE_GOLD = ODDIN_DIRE_NW + fx.ODDIN_TEAM_GOLD_PAD
ODDIN_RADIANT_DEATHS = sum(range(5))
ODDIN_DIRE_DEATHS = sum(i + 1 for i in range(5))


def _reader() -> GameStateReader:
    return GameStateReader(TailCache())


def _grid_archive(
    tmp_path: Path,
    name: str = "m1",
    *,
    journal: list[dict[str, object]] | None = None,
    feed: list[dict[str, object]] | None = None,
) -> Path:
    archive = tmp_path / name
    fx.write_meta(archive, match_id=name)
    if journal is not None:
        fx.write_lines(fx.journal_path(archive), journal)
    if feed is not None:
        fx.write_lines(archive / GRID_STATE_ARCHIVE_FILENAME, feed)
    return archive


def _dota_board_frame(
    *,
    seconds: int = 2647,
    ticking: bool = True,
    status: str = "live",
    occurred: str = fx.GRID_BOARD_AT,
    active_index: int = 0,
    kills_0: int = 22,
    kills_1: int = 24,
    extra_game: bool = False,
) -> str:
    board = cast(ScoreboardPayload, json.loads(json.dumps(gwf.SCOREBOARD)))
    board["activeGameIndex"] = active_index
    game = board["games"][0]
    game["status"] = status
    game["teams"][0]["score"] = kills_1
    game["teams"][1]["score"] = kills_0
    game["gameClock"]["currentSeconds"] = seconds
    game["gameClock"]["isTicking"] = ticking
    game["gameClock"]["occurredAt"] = occurred
    if extra_game:
        second_game = cast(ScoreboardGame, json.loads(json.dumps(game)))
        second_game["status"] = "live"
        second_game["teams"][0]["score"] = 0
        second_game["teams"][1]["score"] = 0
        second_game["gameClock"]["currentSeconds"] = 10
        board["games"].append(second_game)
    return gwf.wrap("series_scoreboard_v2", 0, board)


def _dota_table_frame(
    *, sequence: int = 1, delay: int = 8, rows: list[TableRow] | None = None
) -> str:
    table = gwf.copy_series_table()
    table["stateGroups"][1]["states"][0]["sequenceNumber"] = sequence
    if rows is not None:
        gwf.table_rows(table)[:] = rows
    return gwf.wrap("series_table", delay, table)


def _dota_signal(
    *,
    second: int = DOTA_TABLE_SECOND,
    feed_source: str = "grid",
    feed_received_at_utc: str = fx.GRID_TABLE_AT,
    recorded_at_utc: str = "2026-08-24T11:07:56.000Z",
    phase: str = "in_progress",
    paused: bool = False,
    radiant_nw: int = DOTA_RADIANT_NW,
    dire_nw: int = DOTA_DIRE_NW,
    radiant_xp_adv: int = 1200,
    deaths_radiant: int = DOTA_RADIANT_DEATHS,
    deaths_dire: int = DOTA_DIRE_DEATHS,
    model_evaluated: bool | None = True,
    raw_delta: float | None = 0.05,
    reason: str = "model",
    entry_block: str = "none",
    market_radiant_prior: float | None = None,
    with_snapshot: bool = True,
) -> dict[str, object]:
    return fx.signal_record(
        second=second,
        feed_source=feed_source,
        feed_received_at_utc=feed_received_at_utc,
        recorded_at_utc=recorded_at_utc,
        phase=phase,
        paused=paused,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=radiant_xp_adv,
        deaths_radiant=deaths_radiant,
        deaths_dire=deaths_dire,
        model_evaluated=model_evaluated,
        raw_delta=raw_delta,
        reason=reason,
        entry_block=entry_block,
        market_radiant_prior=market_radiant_prior,
        with_snapshot=with_snapshot,
    )


def _dota_feed(
    *,
    board_at: str = fx.GRID_BOARD_AT,
    table_at: str = fx.GRID_TABLE_AT,
    board: str | None = None,
    table: str | None = None,
) -> list[dict[str, object]]:
    return [
        fx.grid_record(board or _dota_board_frame(), board_at),
        fx.grid_record(table or _dota_table_frame(), table_at),
    ]


def _oddin_archive(
    tmp_path: Path,
    name: str = "m2",
    *,
    journal: list[dict[str, object]] | None = None,
    feed: list[dict[str, object]] | None = None,
) -> Path:
    archive = tmp_path / name
    fx.write_meta(
        archive,
        match_id=name,
        feed_source="oddin",
        radiant=fx.ODDIN_AWAY,
        dire=fx.ODDIN_HOME,
    )
    if journal is not None:
        fx.write_lines(fx.journal_path(archive), journal)
    if feed is not None:
        fx.write_lines(fx.oddin_state_path(archive), feed)
    return archive


def _oddin_signal(
    *,
    second: int = 998,
    feed_source: str = "oddin",
    feed_received_at_utc: str = fx.ODDIN_WS_AT,
    recorded_at_utc: str = "2026-09-19T16:57:50.100Z",
    phase: str = "in_progress",
    paused: bool = False,
    radiant_nw: int = ODDIN_RADIANT_NW,
    dire_nw: int = ODDIN_DIRE_NW,
    radiant_xp_adv: int = 0,
    deaths_radiant: int = ODDIN_RADIANT_DEATHS,
    deaths_dire: int = ODDIN_DIRE_DEATHS,
    model_evaluated: bool | None = True,
    raw_delta: float | None = 0.05,
    reason: str = "model",
    entry_block: str = "none",
    with_snapshot: bool = True,
) -> dict[str, object]:
    return fx.signal_record(
        second=second,
        feed_source=feed_source,
        feed_received_at_utc=feed_received_at_utc,
        recorded_at_utc=recorded_at_utc,
        phase=phase,
        paused=paused,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=radiant_xp_adv,
        deaths_radiant=deaths_radiant,
        deaths_dire=deaths_dire,
        model_evaluated=model_evaluated,
        raw_delta=raw_delta,
        reason=reason,
        entry_block=entry_block,
        with_snapshot=with_snapshot,
    )


def _oddin_feed(*records: dict[str, object]) -> list[dict[str, object]]:
    return list(records)


def test_grid_dota_aligned_decision_board_table(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(market_radiant_prior=0.54)],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    decision = summary.decision
    assert decision.provenance == "signal"
    assert decision.second == DOTA_TABLE_SECOND
    assert decision.radiant_nw == DOTA_RADIANT_NW
    assert decision.dire_nw == DOTA_DIRE_NW
    assert decision.deaths_radiant == DOTA_RADIANT_DEATHS
    assert decision.deaths_dire == DOTA_DIRE_DEATHS
    assert decision.model_evaluated is True
    assert decision.raw_delta == 0.05
    assert decision.market_radiant_prior == 0.54
    assert decision.feed_source == "grid"
    assert decision.feed_received_at_utc == fx.GRID_TABLE_AT
    assert decision.phase == "in_progress"
    assert decision.paused is False
    assert decision.xp_status == "available"
    assert decision.radiant_xp_adv == 1200
    assert decision.top is not None and decision.top.top1_nw_adv == 500
    board = summary.board
    assert board is not None
    assert board.second == 2647
    assert board.clock_ticking is True
    assert board.paused is False
    assert board.status == "live"
    assert board.kills_0 == 22
    assert board.kills_1 == 24
    assert board.series == SeriesScore(
        name_0="Team Lynx", name_1="Klim Sani4", score_0=0, score_1=1
    )
    assert board.received_at_utc == fx.GRID_BOARD_AT
    assert board.updated_at == "2026-08-24T11:07:52.653Z"
    table = summary.table
    assert table is not None
    assert table.second == DOTA_TABLE_SECOND
    assert table.second_reconstructed is True
    assert table.received_at_utc == fx.GRID_TABLE_AT
    assert table.feed_delay == 8
    side_0 = table.side_0
    side_1 = table.side_1
    assert side_0.label == "Radiant"
    assert side_0.team_name == "Team Lynx"
    assert side_0.gold is None
    assert side_0.players_gold == DOTA_RADIANT_NW
    assert side_0.complete is True
    assert [p.nick for p in side_0.players] == [
        "naive-",
        "mellojul",
        "htiviy_vladik",
        "QBFY",
        "kreker",
    ]
    assert side_0.players[0].hero == "juggernaut"
    assert side_0.players[0].level == 20
    assert side_0.players[0].net_worth == 23074
    assert side_1.label == "Dire"
    assert side_1.team_name == "Klim Sani4"
    assert side_1.gold is None
    assert side_1.players_gold == DOTA_DIRE_NW
    assert summary.source.comparison == "aligned"
    assert summary.source.continuity == "cold"
    assert summary.identity.yes_is_side_0 is True
    assert summary.decision_label == "игровой снимок решения"
    assert summary.archive_label == "данные игры"


def test_grid_board_and_table_age_independently(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal()],
        feed=[
            *_dota_feed(),
            fx.grid_record(_dota_board_frame(seconds=3000), fx.GRID_NEXT_AT),
        ],
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.board is not None
    assert summary.board.received_at_utc == fx.GRID_NEXT_AT
    assert summary.board.second == 3000
    assert summary.table is not None
    assert summary.table.received_at_utc == fx.GRID_TABLE_AT
    assert summary.table.second == DOTA_TABLE_SECOND


def test_grid_new_table_does_not_renew_board(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal()],
        feed=[
            fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
            fx.grid_record(_dota_table_frame(), fx.GRID_TABLE_AT),
        ],
    )
    entry = fx.make_entry(archive, match_id="m1")
    reader = _reader()
    first = reader.read(entry, 0.0)
    assert first.table is not None
    rows2 = gwf.table_rows(gwf.copy_series_table())
    cell = rows2[0]["NetWorth"]
    cell["value"] = (cell["value"] or 0) + 200
    feed = [
        fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
        fx.grid_record(_dota_table_frame(rows=rows2), fx.GRID_NEXT_AT),
    ]
    fx.write_lines(fx.grid_state_path(archive), feed)
    second = reader.read(entry, 1.0)
    assert second.table is not None
    assert second.table.received_at_utc == fx.GRID_NEXT_AT
    assert second.board is not None
    assert second.board.received_at_utc == fx.GRID_BOARD_AT


def test_grid_duplicate_table_keeps_original_receipt(tmp_path: Path) -> None:
    feed = [
        *_dota_feed(),
        fx.grid_record(_dota_table_frame(), fx.GRID_NEXT_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.received_at_utc == fx.GRID_TABLE_AT


def test_grid_portraitless_substitute_keeps_roster(tmp_path: Path) -> None:
    table = gwf.table_with_extra_row(gwf.make_portraitless_row(gwf.KLIM, "coach", 0))
    frame = gwf.wrap("series_table", 8, table)
    feed = [
        fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
        fx.grid_record(frame, fx.GRID_TABLE_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert len(summary.table.side_1.players) == 5
    assert summary.table.side_1.complete is True


def test_grid_extra_portrait_row_rejected(tmp_path: Path) -> None:
    table = gwf.table_with_extra_row(gwf.make_row(gwf.KLIM, "sixth", "axe", 100, 0, 0, 0, 5))
    frame = gwf.wrap("series_table", 8, table)
    feed = [
        fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
        fx.grid_record(frame, fx.GRID_TABLE_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is None
    assert summary.source.rejected == 1
    assert "incomplete roster" in summary.source.control


def test_grid_foreign_map_table_rejected(tmp_path: Path) -> None:
    feed = [
        fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
        fx.grid_record(_dota_table_frame(sequence=2), fx.GRID_TABLE_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is None
    assert summary.source.rejected == 1
    assert "table for map 2" in summary.source.control


def test_grid_table_before_board_rejected(tmp_path: Path) -> None:
    feed = [
        fx.grid_record(_dota_table_frame(), fx.GRID_TABLE_AT),
        fx.grid_record(_dota_board_frame(), fx.GRID_NEXT_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.board is not None
    assert summary.table is None
    assert summary.source.rejected == 1


def test_grid_table_rejected_after_active_map_moved(tmp_path: Path) -> None:
    feed = [
        fx.grid_record(_dota_board_frame(active_index=1, extra_game=True), fx.GRID_BOARD_AT),
        fx.grid_record(_dota_table_frame(), fx.GRID_TABLE_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is None
    assert "table while other map active" in summary.source.control


def test_grid_feed_gone_is_terminal_evidence(tmp_path: Path) -> None:
    feed = [
        *_dota_feed(),
        fx.grid_record(
            '{"service":"live_paper_feed_gone","delay":0,"data":[]}',
            fx.GRID_NEXT_AT,
        ),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.source.terminal is True
    assert any("feed gone" in note for note in summary.source.control)
    assert summary.table is not None
    assert summary.board is not None


def test_grid_finished_board_is_terminal(tmp_path: Path) -> None:
    feed = _dota_feed(board=_dota_board_frame(status="finished", ticking=False))
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.board is not None
    assert summary.board.status == "finished"
    assert summary.board.paused is True
    assert summary.source.terminal is True


def test_grid_paused_clock_stays_fixed(tmp_path: Path) -> None:
    feed = _dota_feed(board=_dota_board_frame(seconds=1500, ticking=False))
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    reader = _reader()
    first = reader.read(entry, 0.0)
    later = reader.read(entry, 500.0)
    assert first.board is not None and first.board.second == 1500
    assert first.board.paused is True
    assert later is first


def test_grid_missing_level_cells_leave_level_unknown(tmp_path: Path) -> None:
    rows = gwf.table_rows(gwf.copy_series_table())
    for row in rows:
        del row["increaseLevel"]
    feed = [
        fx.grid_record(_dota_board_frame(), fx.GRID_BOARD_AT),
        fx.grid_record(_dota_table_frame(rows=rows), fx.GRID_TABLE_AT),
    ]
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert all(p.level is None for p in summary.table.side_0.players)


def test_grid_orientation_conflict_noted(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1", yes_is_radiant=False)
    summary = _reader().read(entry, 0.0)
    assert "board orientation conflicts saved yes_is_radiant" in summary.source.evidence


def test_lol_grid_map2_blue_red_uuid_heroes(tmp_path: Path) -> None:
    archive = tmp_path / "lol1"
    fx.write_meta(
        archive,
        match_id="lol1",
        game="lol",
        map_number=2,
        yes_is_radiant=False,
        radiant=fx.LOL_BLUE,
        dire=fx.LOL_RED,
    )
    signal = fx.signal_record(
        second=145,
        feed_source="grid",
        feed_received_at_utc="2026-08-29T18:17:50.000Z",
        radiant_nw=776 + 620 + 910 + 862 + 602,
        dire_nw=829 + 836 + 904 + 1013 + 708,
        deaths_radiant=0,
        deaths_dire=0,
    )
    fx.write_lines(fx.journal_path(archive), [signal])
    feed = [
        fx.grid_record(
            lwf.wrap("series_scoreboard_v2", 0, lwf.LEC_SCOREBOARD, lwf.LEC_SERIES_ID),
            "2026-08-29T18:17:46.000Z",
        ),
        fx.grid_record(
            lwf.wrap("series_table", 4, lwf.LEC_SERIES_TABLE, lwf.LEC_SERIES_ID),
            "2026-08-29T18:17:50.000Z",
        ),
    ]
    fx.write_lines(fx.grid_state_path(archive), feed)
    entry = fx.make_entry(
        archive,
        match_id="lol1",
        game="lol",
        map_number=2,
        yes_is_radiant=False,
        radiant=fx.LOL_BLUE,
        dire=fx.LOL_RED,
        outcome_0_name=fx.LOL_RED,
        outcome_1_name=fx.LOL_BLUE,
    )
    summary = _reader().read(entry, 0.0)
    board = summary.board
    assert board is not None
    assert board.status == "live"
    assert board.kills_0 == 0
    assert board.kills_1 == 0
    assert board.series == SeriesScore(
        name_0="GIANTX", name_1="Natus Vincere", score_0=0, score_1=1
    )
    table = summary.table
    assert table is not None
    assert table.side_0.label == "Blue"
    assert table.side_0.team_name == "GIANTX"
    assert table.side_1.label == "Red"
    assert table.side_1.team_name == "Natus Vincere"
    assert all(p.hero is None for p in table.side_0.players + table.side_1.players)
    nicks = [p.nick for p in table.side_0.players]
    assert nicks == ["Jackies", "TH Flakked", "ISMA", "Oscarinin", "Jun"]
    assert table.side_0.players[-1].level is None
    assert summary.identity.yes_is_side_0 is False
    assert summary.source.comparison == "aligned"


def test_lol_finished_map_shows_board_rejects_map2_table(tmp_path: Path) -> None:
    archive = tmp_path / "lol2"
    fx.write_meta(
        archive,
        match_id="lol2",
        game="lol",
        map_number=1,
        radiant=fx.LOL_BLUE,
        dire=fx.LOL_RED,
    )
    fx.write_lines(
        fx.journal_path(archive),
        [_oddin_signal(feed_source="grid", feed_received_at_utc="2026-08-29T18:17:50.000Z")],
    )
    feed = [
        fx.grid_record(
            lwf.wrap("series_scoreboard_v2", 0, lwf.LEC_SCOREBOARD, lwf.LEC_SERIES_ID),
            "2026-08-29T18:17:46.000Z",
        ),
        fx.grid_record(
            lwf.wrap("series_table", 4, lwf.LEC_SERIES_TABLE, lwf.LEC_SERIES_ID),
            "2026-08-29T18:17:50.000Z",
        ),
    ]
    fx.write_lines(fx.grid_state_path(archive), feed)
    entry = fx.make_entry(
        archive,
        match_id="lol2",
        game="lol",
        map_number=1,
        radiant=fx.LOL_BLUE,
        dire=fx.LOL_RED,
        outcome_0_name=fx.LOL_RED,
        outcome_1_name=fx.LOL_BLUE,
    )
    summary = _reader().read(entry, 0.0)
    assert summary.board is not None
    assert summary.board.status == "finished"
    assert summary.board.second == 1999
    assert summary.table is None
    assert summary.source.terminal is True


def test_oddin_home_dire_factions_and_no_xp(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(fx.oddin_payload(), "2026-09-19T16:57:49.000000Z", event="snapshot"),
        fx.oddin_record(fx.oddin_payload(), fx.ODDIN_WS_AT),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2", radiant=None, dire=None)
    summary = _reader().read(entry, 0.0)
    assert summary.decision.xp_status == "not_used"
    assert summary.decision.xp_text == "не используется этой моделью"
    assert summary.decision.radiant_xp_adv is None
    board = summary.board
    assert board is not None
    assert board.series is not None
    assert board.series.name_0 == "LGD Gaming"
    assert board.series.name_1 == "Yakult's Brothers"
    assert board.series.score_0 == 1
    assert board.series.score_1 == 1
    assert board.updated_at == fx.ODDIN_UPDATED
    table = summary.table
    assert table is not None
    assert table.side_0.team_name == "Yakult's Brothers"
    assert table.side_1.team_name == "LGD Gaming"
    assert [p.nick for p in table.side_0.players] == [f"R{i}" for i in range(5)]
    assert [p.nick for p in table.side_1.players] == [f"D{i}" for i in range(5)]
    assert table.side_0.gold == ODDIN_RADIANT_GOLD
    assert table.side_1.gold == ODDIN_DIRE_GOLD
    assert table.side_0.players_gold == ODDIN_RADIANT_NW
    assert table.side_1.players_gold == ODDIN_DIRE_NW
    assert table.objectives_0 == Objectives(towers=4, barracks=1, roshans=1)
    assert table.side_0.players[0].alive is True
    assert table.side_0.players[0].aegis is False
    assert table.side_0.players[0].level is None
    assert summary.source.comparison == "aligned"
    assert any("seed" in note for note in summary.source.control)
    assert summary.identity.team_0 == "Yakult's Brothers"


def test_oddin_reconnect_then_duplicate_rejected(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(fx.oddin_payload(), "2026-09-19T16:57:50.000000Z"),
        fx.oddin_record(None, "2026-09-19T16:57:50.500000Z", event="reconnect"),
        fx.oddin_record(fx.oddin_payload(), "2026-09-19T16:57:51.000000Z"),
        fx.oddin_record(
            fx.oddin_payload(game_time=1004, updated=fx.ODDIN_UPDATED_NEXT),
            "2026-09-19T16:57:52.000000Z",
        ),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.second == 1004
    assert any("reconnect" in note for note in summary.source.control)
    assert summary.source.rejected == 1


def test_oddin_out_of_order_rejected(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(
            fx.oddin_payload(game_time=1004, updated=fx.ODDIN_UPDATED_NEXT),
            "2026-09-19T16:57:51.000000Z",
        ),
        fx.oddin_record(fx.oddin_payload(), "2026-09-19T16:57:52.000000Z"),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.second == 1004
    assert summary.source.rejected == 1


def test_oddin_invalid_data_rejected(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(
            fx.oddin_payload(data_status="DELAYED_DATA"), "2026-09-19T16:57:49.000000Z"
        ),
        fx.oddin_record(fx.oddin_payload(), fx.ODDIN_WS_AT),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.second == 998
    assert summary.source.rejected == 1


def test_oddin_terminal_keeps_live_slice(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(fx.oddin_payload(), fx.ODDIN_WS_AT),
        fx.oddin_record(fx.oddin_payload(status="FINISHED"), "2026-09-19T16:57:51.000000Z"),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.source.terminal is True
    assert summary.table is not None
    assert summary.table.second == 998
    assert summary.table.side_0.gold == ODDIN_RADIANT_GOLD
    assert any("terminal" in note for note in summary.source.control)


def test_oddin_next_map_is_terminal_not_players(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(fx.oddin_payload(), fx.ODDIN_WS_AT),
        fx.oddin_record(
            fx.oddin_payload(
                map_order=2,
                map_id="map-2",
                game_time=-30,
                radiant_players=fx.oddin_five("N", 100),
            ),
            "2026-09-19T16:57:51.000000Z",
        ),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.source.terminal is True
    assert summary.table is not None
    assert [p.nick for p in summary.table.side_0.players] == [f"R{i}" for i in range(5)]


def test_oddin_incomplete_side_rejected(tmp_path: Path) -> None:
    feed = _oddin_feed(
        fx.oddin_record(
            fx.oddin_payload(radiant_players=fx.oddin_five("R", 5000)[:4]),
            fx.ODDIN_WS_AT,
        ),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.table is None
    assert summary.source.rejected == 1


def test_oddin_missing_fields_stay_none_zeros_survive(tmp_path: Path) -> None:
    players = fx.oddin_five("R", 5000)
    players[0] = fx.oddin_player("R0", 5000, 0, hero=None, alive=None, aegis=None)
    players[1] = fx.oddin_player("R1", 5001, 0, kills=0, aegis=False)
    feed = _oddin_feed(
        fx.oddin_record(fx.oddin_payload(radiant_players=players), fx.ODDIN_WS_AT),
    )
    archive = _oddin_archive(tmp_path, journal=[_oddin_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m2")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    first = summary.table.side_0.players[0]
    second = summary.table.side_0.players[1]
    assert first.hero is None
    assert first.alive is None
    assert first.aegis is None
    assert first.net_worth == 5000
    assert second.kills == 0
    assert second.aegis is False


def test_legacy_signal_projection(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(with_snapshot=False)],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    decision = summary.decision
    assert decision.provenance == "legacy"
    assert decision.second == DOTA_TABLE_SECOND
    assert decision.radiant_nw is None
    assert decision.xp_status == "unknown"
    assert "missing game_snapshot" in decision.notes
    assert summary.source.comparison == "unknown"


def test_no_journal_decision_only(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1", record_only=True)
    summary = _reader().read(entry, 0.0)
    assert summary.decision.provenance == "none"
    assert "journal unavailable" in summary.source.evidence
    assert summary.board is not None
    assert summary.table is not None
    assert summary.source.comparison == "unknown"


def test_unevaluated_signal_hides_residual_delta(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[
            _dota_signal(model_evaluated=False, raw_delta=0.5, reason="cooldown"),
        ],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.decision.model_evaluated is False
    assert summary.decision.raw_delta is None
    assert summary.decision.reason == "cooldown"


def test_missing_model_evaluated_is_unknown(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(model_evaluated=None, raw_delta=None)],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.decision.model_evaluated is None
    assert summary.decision.raw_delta is None


def test_terminal_signal_zeros_preserved(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[
            _dota_signal(
                second=2900,
                phase="finished",
                radiant_nw=0,
                dire_nw=0,
                radiant_xp_adv=0,
                deaths_radiant=0,
                deaths_dire=0,
                reason="finished",
            )
        ],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.decision.phase == "finished"
    assert summary.decision.radiant_nw == 0
    assert summary.decision.dire_nw == 0
    assert summary.decision.model_evaluated is True
    assert summary.decision.raw_delta == 0.05


def test_signal_ahead_of_archive_is_different(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(feed_received_at_utc=fx.GRID_NEXT_AT)],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.source.comparison == "different"
    assert summary.table is not None
    assert summary.decision.feed_received_at_utc == fx.GRID_NEXT_AT


def test_archive_ahead_of_signal_is_different(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(feed_received_at_utc="2026-08-24T11:07:54.000Z")],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.source.comparison == "different"


def test_mismatched_values_is_different(tmp_path: Path) -> None:
    archive = _grid_archive(
        tmp_path,
        journal=[_dota_signal(radiant_nw=1, dire_nw=2, deaths_radiant=0, deaths_dire=0)],
        feed=_dota_feed(),
    )
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.source.comparison == "different"


def test_unsupported_source_keeps_decision_only(tmp_path: Path) -> None:
    archive = tmp_path / "steam1"
    fx.write_meta(archive, match_id="steam1", feed_source="steam")
    fx.write_lines(
        fx.journal_path(archive),
        [_dota_signal(feed_source="steam")],
    )
    entry = fx.make_entry(archive, match_id="steam1")
    summary = _reader().read(entry, 0.0)
    assert summary.decision.provenance == "signal"
    assert summary.board is None
    assert summary.table is None
    assert summary.identity.source == "steam"
    assert any("unsupported feed source" in e for e in summary.source.evidence)


def test_bounded_tail_stays_bounded(tmp_path: Path) -> None:
    feed = [
        fx.grid_record(
            _dota_board_frame(seconds=100 + i),
            f"2026-08-24T11:{i // 60:02d}:{i % 60:02d}.000Z",
        )
        for i in range(600)
    ]
    feed.append(fx.grid_record(_dota_table_frame(), fx.GRID_TABLE_AT))
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=feed)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    tails = TailCache()
    tail = tails.read_records(fx.grid_state_path(archive))
    assert tail is not None
    assert tail.truncated_start is True
    assert len(tail.records) <= 512
    assert summary.board is not None
    assert summary.table is not None
    assert "feed tail truncated" in summary.source.evidence


def test_unchanged_scan_skips_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original = vars(TailCache)["_read_resolved"]

    def spy(self: TailCache, resolved: Path, mtime_ns: int, size: int) -> RecordTail:
        nonlocal calls
        calls += 1
        return original(self, resolved, mtime_ns, size)

    monkeypatch.setattr(TailCache, "_read_resolved", spy)
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    tails = TailCache()
    reader = GameStateReader(tails)
    first = reader.read(entry, 0.0)
    journal_calls = calls
    second = reader.read(entry, 1.0)
    assert calls == journal_calls
    assert second is first


def test_changed_tail_reduced_once_for_two_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applies = 0
    original = game_grid.GridFold.apply

    def spy(self: game_grid.GridFold, record: Mapping[str, object]) -> None:
        nonlocal applies
        applies += 1
        original(self, record)

    monkeypatch.setattr(game_grid.GridFold, "apply", spy)
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    reader = _reader()
    results: list[GameSummary] = []

    def work() -> None:
        results.append(reader.read(entry, 0.0))

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert applies == 2
    assert results[0] is results[1]


def test_gzip_tail(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()])
    records = _dota_feed()
    payload = "".join(json.dumps(record) + "\n" for record in records)
    gz_path = fx.grid_state_path(archive).with_name(fx.grid_state_path(archive).name + ".gz")
    with gzip.open(gz_path, "wt") as handle:
        handle.write(payload)
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.side_0.complete is True
    assert summary.table.archive_path == gz_path


def test_torn_tail_keeps_last_valid(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    path = fx.grid_state_path(archive)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"received_at_utc": "2026-08-24T11:08:01.000Z", "frame": "{\\"ser')
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    assert summary.table.received_at_utc == fx.GRID_TABLE_AT


def test_same_match_id_two_roots_no_leak(tmp_path: Path) -> None:
    a = _grid_archive(tmp_path, "same", journal=[_dota_signal()], feed=_dota_feed())
    b_root = tmp_path / "other"
    b = b_root / "same"
    fx.write_meta(b, match_id="same")
    fx.write_lines(fx.journal_path(b), [_oddin_signal(feed_source="grid")])
    reader = _reader()
    entry_a = fx.make_entry(a, match_id="same")
    entry_b = fx.make_entry(b, match_id="same")
    summary_a = reader.read(entry_a, 0.0)
    summary_b = reader.read(entry_b, 0.0)
    assert summary_a.table is not None
    assert summary_b.table is None
    assert summary_a.identity.archive_dir == a
    assert summary_b.identity.archive_dir == b


def test_file_replaced_reconstructs(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    reader = _reader()
    first = reader.read(entry, 0.0)
    assert first.source.continuity == "cold"
    fx.write_lines(
        fx.grid_state_path(archive),
        [fx.grid_record(_dota_table_frame(), fx.GRID_TABLE_AT)],
    )
    second = reader.read(entry, 1.0)
    assert second.source.continuity == "reconstructed"
    assert second.table is not None
    assert second.table.received_at_utc == fx.GRID_TABLE_AT


def test_reconstruct_gap_keeps_last_table(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    reader = _reader()
    first = reader.read(entry, 0.0)
    assert first.table is not None
    path = fx.grid_state_path(archive)
    with path.open("a", encoding="utf-8") as handle:
        for i in range(600):
            record = fx.grid_record(
                _dota_board_frame(seconds=3000 + i),
                f"2026-08-24T12:{i // 60:02d}:{i % 60:02d}.000Z",
            )
            handle.write(json.dumps(record) + "\n")
    second = reader.read(entry, 1.0)
    assert second.source.continuity == "reconstructed"
    assert second.table is not None
    assert second.table.received_at_utc == fx.GRID_TABLE_AT
    assert second.board is not None
    assert second.board.second == 3599


def test_reader_eviction_rebuilds_state(tmp_path: Path) -> None:
    a = _grid_archive(tmp_path, "a", journal=[_dota_signal()], feed=_dota_feed())
    b = _grid_archive(tmp_path, "b", journal=[_dota_signal()], feed=_dota_feed())
    reader = GameStateReader(TailCache(), max_states=1)
    entry_a = fx.make_entry(a, match_id="a")
    entry_b = fx.make_entry(b, match_id="b")
    first_a = reader.read(entry_a, 0.0)
    reader.read(entry_b, 1.0)
    second_a = reader.read(entry_a, 2.0)
    assert first_a.table is not None
    assert second_a.table is not None
    assert second_a.source.continuity == "cold"


def test_read_only_selected_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = _grid_archive(tmp_path, "a", journal=[_dota_signal()], feed=_dota_feed())
    b = _grid_archive(tmp_path, "b", journal=[_dota_signal()], feed=_dota_feed())
    touched: list[Path] = []
    tails = TailCache()
    original = tails.read_records

    def spy(path: Path) -> RecordTail | None:
        touched.append(path)
        return original(path)

    monkeypatch.setattr(tails, "read_records", spy)
    reader = GameStateReader(tails)
    entry_a = fx.make_entry(a, match_id="a")
    reader.read(entry_a, 0.0)
    assert all(str(path).startswith(str(a)) for path in touched)
    assert not any(str(path).startswith(str(b)) for path in touched)


def test_no_full_replay_on_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(*args: object, **kwargs: object) -> None:
        raise AssertionError("full replay on fast path")

    monkeypatch.setattr(game_state_replay, "load_live_replay", raiser)
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None


def test_summary_is_immutable(tmp_path: Path) -> None:
    archive = _grid_archive(tmp_path, journal=[_dota_signal()], feed=_dota_feed())
    entry = fx.make_entry(archive, match_id="m1")
    summary = _reader().read(entry, 0.0)
    assert summary.table is not None
    with pytest.raises(FrozenInstanceError):
        delattr(summary.decision, "second")
    assert isinstance(summary.decision.notes, tuple)
    assert isinstance(summary.table.side_0.players, tuple)
    player = summary.table.side_0.players[0]
    with pytest.raises(FrozenInstanceError):
        delattr(player, "net_worth")
