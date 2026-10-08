"""Archive index: schedule extraction, verdicts, duplicates, report counts."""

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from grid_widget_fixtures import (
    NET_WORTHS,
    SCOREBOARD,
    SERIES_TABLE,
    copy_series_table,
    table_rows,
    wrap,
)

from archive_index.index import INDEX_FILENAME, build_index, run_index
from archive_index.schedule import (
    EXTRACTION_RULES_VERSION,
    SCHEDULE_SCHEMA_VERSION,
    FeedSchedule,
    compute_fingerprint,
    extract_schedule,
    read_schedule,
    schedule_path_for,
)
from archive_index.universe import UniverseMarket
from shared.utils.match_time import datetime_to_ns, parse_utc
from trader.grid_feed import FEED_GONE_FRAME
from trader.grid_widget_types import ScoreboardPayload, SeriesTablePayload
from trader.grid_widgets import clock_stamp_unix_seconds
from trader.live_feed import unix_seconds_to_iso_z
from trader.match_meta import read_match_meta
from trader.paths import GRID_STATE_ARCHIVE_FILENAME, ODDIN_STATE_ARCHIVE_FILENAME

OUTCOME_0 = "Team Lynx"
OUTCOME_1 = "Klim Sani4"
CONDITION_ID = "0xabc123"
YES_TOKEN = "111"
NO_TOKEN = "222"
# Recorded board clock is 2647 s stamped 2026-08-24T11:07:52.653Z; a table frame
# delayed 8 s and received at T shows second = 2647 + round(T - stamp) - 8.
BOARD_CLOCK = 2647
TABLE_DELAY = 8


def _meta_document(**overrides: object) -> dict[str, object]:
    """A schema-8 match.json document bound to the recorded Lynx/Klim frames."""
    document: dict[str, object] = {
        "schema_version": 8,
        "match_id": "grid-test-m1",
        "game": "dota",
        "steam_match_id": "8944931337",
        "server_steam_id": None,
        "league_id": None,
        "tournament": "EPL Masters II (Play-Ins)",
        "teams": {"radiant": "Team Lynx", "dire": "Klim Sani4"},
        "map_number": 1,
        "joined_at_second": 2647,
        "joined_at_utc": "2026-08-24T11:07:54.653000Z",
        "horn_at_utc": "2026-08-24T10:23:45Z",
        "market": {
            "condition_id": CONDITION_ID,
            "market_slug": "dota-2-lynx-vs-klim-game-1",
            "event_slug": "dota-2-lynx-vs-klim",
            "yes_token_id": YES_TOKEN,
            "no_token_id": NO_TOKEN,
            "yes_is_radiant": True,
            "outcome_0_name": OUTCOME_0,
            "outcome_1_name": OUTCOME_1,
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "grid_series_id": "2995964",
        },
        "model": {"name": "m", "trained_at": "2026-08-20T00:00:00Z"},
        "feed_source": "grid",
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": 8.0,
        "oddin_match_id": None,
        "oddin_delay_s": None,
        "final": None,
    }
    document.update(overrides)
    return document


def _universe_market(**overrides: object) -> UniverseMarket:
    fields: dict[str, object] = {
        "condition_id": CONDITION_ID,
        "event_id": "41810",
        "team_a": OUTCOME_0,
        "team_b": OUTCOME_1,
        "token_ids": (YES_TOKEN, NO_TOKEN),
        "outcome_names": None,
        "game_number": 1,
        "contract_kind": "map_winner",
        "market_slug": "dota-2-lynx-vs-klim-game-1",
        "event_slug": None,
    }
    fields.update(overrides)
    return UniverseMarket(**fields)  # type: ignore[arg-type]


def _write_meta(archive_dir: Path, **overrides: object) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    meta_path = archive_dir / "match.json"
    meta_path.write_text(json.dumps(_meta_document(**overrides)), encoding="utf-8")
    return meta_path


def _grid_record(received_at_utc: str, frame: str) -> str:
    return json.dumps({"received_at_utc": received_at_utc, "frame": frame})


def _write_grid_archive(
    archive_dir: Path,
    *,
    clock_seconds: int = BOARD_CLOCK,
    table_received: tuple[str, ...] = ("2026-08-24T11:07:55.653000Z",),
    tables: tuple[object, ...] = (SERIES_TABLE,),
    terminal: bool = True,
    terminal_received: str = "2026-08-24T11:20:00.000000Z",
    feed_gone: bool = False,
    ticking: bool = True,
    table_delay: int = TABLE_DELAY,
    kill_frames: tuple[tuple[str, object], ...] = (),
) -> Path:
    """grid_state.jsonl: live scoreboard, one table tick per stamp, optional finish."""
    scoreboard = copy.deepcopy(SCOREBOARD)
    scoreboard["games"][0]["gameClock"]["isTicking"] = ticking
    scoreboard["games"][0]["gameClock"]["currentSeconds"] = clock_seconds
    records = [
        _grid_record("2026-08-24T11:07:54.653000Z", wrap("series_scoreboard_v2", 0, scoreboard))
    ]
    for received, table in zip(table_received, tables, strict=True):
        records.append(_grid_record(received, wrap("series_table", table_delay, table)))
    for received, kill in kill_frames:
        records.append(_grid_record(received, wrap("series_scoreboard_v2", 0, kill)))
    if terminal:
        finished = copy.deepcopy(scoreboard)
        finished["games"][0]["status"] = "finished"
        records.append(_grid_record(terminal_received, wrap("series_scoreboard_v2", 0, finished)))
    if feed_gone:
        records.append(
            json.dumps({"received_at_utc": "2026-08-24T11:21:00.000000Z", "frame": FEED_GONE_FRAME})
        )
    feed_path = archive_dir / GRID_STATE_ARCHIVE_FILENAME
    feed_path.write_text("\n".join(records) + "\n", encoding="utf-8")
    return feed_path


def _table_with_extra_worth() -> SeriesTablePayload:
    """A second table at the same clock: one player richer, so it is a new tick."""
    table = copy_series_table()
    row = table_rows(table)[0]
    row["NetWorth"]["value"] = (row["NetWorth"]["value"] or 0) + 1000
    return table


def _extract(archive_dir: Path) -> FeedSchedule:
    """Extract a schedule from the meta and feed files already on disk."""
    meta = read_match_meta(archive_dir / "match.json")
    return extract_schedule(archive_dir, meta, event_id="41810", yes_token_index=0)


def test_grid_schedule_has_ticks_terminal_and_stable_fingerprint(tmp_path: Path) -> None:
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir)
    schedule = _extract(archive_dir)
    assert schedule.stats.tick_count == 2
    assert schedule.stats.terminal is True
    assert schedule.ticks[-1].terminal is True
    first = schedule.ticks[0]
    assert first.game_second == BOARD_CLOCK + 3 - TABLE_DELAY
    assert first.phase == "in_progress"
    assert first.paused is False
    assert first.radiant_nw + first.dire_nw == sum(row[3] for row in NET_WORTHS)
    assert first.deaths_radiant + first.deaths_dire == sum(row[5] for row in NET_WORTHS)
    assert first.radiant_nw_adv == first.radiant_nw - first.dire_nw
    assert schedule.stats.delay_s == 8.0
    assert schedule.stats.delay_evidence == "meta"
    assert _extract(archive_dir).fingerprint == schedule.fingerprint


def test_schedule_horn_comes_from_ticks_not_match_json(tmp_path: Path) -> None:
    """identity.horn_at_utc is the first positive-clock tick, not match.json."""
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir, horn_at_utc="1999-01-01T00:00:00Z")
    _write_grid_archive(archive_dir)
    schedule = _extract(archive_dir)
    expected = unix_seconds_to_iso_z(
        clock_stamp_unix_seconds("2026-08-24T11:07:52.653Z") - BOARD_CLOCK
    )
    assert schedule.identity.horn_at_utc == expected
    assert schedule.identity.horn_at_utc != "1999-01-01T00:00:00Z"


def test_duplicate_game_seconds_kept_when_arrivals_differ(tmp_path: Path) -> None:
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(
        archive_dir,
        table_received=("2026-08-24T11:07:55.100000Z", "2026-08-24T11:07:55.900000Z"),
        tables=(SERIES_TABLE, _table_with_extra_worth()),
        ticking=False,
    )
    schedule = _extract(archive_dir)
    live_ticks = [tick for tick in schedule.ticks if not tick.terminal]
    assert len(live_ticks) == 2
    assert {tick.game_second for tick in live_ticks} == {BOARD_CLOCK - TABLE_DELAY}
    assert live_ticks[0].received_at_utc != live_ticks[1].received_at_utc


def test_fingerprint_changes_with_feed_bytes(tmp_path: Path) -> None:
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir)
    feed_path = _write_grid_archive(archive_dir)
    schedule = _extract(archive_dir)
    meta = read_match_meta(archive_dir / "match.json")
    recomputed = compute_fingerprint(
        feed_sha256=schedule.feed_sha256,
        meta=meta,
        rules_version=EXTRACTION_RULES_VERSION,
        event_id="41810",
        yes_token_index=0,
    )
    assert recomputed == schedule.fingerprint
    other_universe = compute_fingerprint(
        feed_sha256=schedule.feed_sha256,
        meta=meta,
        rules_version=EXTRACTION_RULES_VERSION,
        event_id="99999",
        yes_token_index=0,
    )
    assert other_universe != schedule.fingerprint
    with feed_path.open("a", encoding="utf-8") as handle:
        handle.write(
            _grid_record("2026-08-24T11:21:00.000000Z", wrap("series_table", 8, SERIES_TABLE))
            + "\n"
        )
    assert _extract(archive_dir).fingerprint != schedule.fingerprint


def _oddin_player(nickname: str, net_worth: int) -> dict[str, object]:
    return {
        "player": {"nickname": nickname},
        "hero": {"name": "Kez"},
        "netWorth": net_worth,
        "kills": 0,
        "deaths": 0,
        "assists": 0,
        "alive": True,
        "hasAegis": False,
    }


def _oddin_side(name: str, faction: str, prefix: str) -> dict[str, object]:
    return {
        "team": {"name": name},
        "faction": faction,
        "kills": 0,
        "netWorth": 0,
        "towers": 0,
        "barracks": 0,
        "roshans": 0,
        "players": [_oddin_player(f"{prefix}{i}", 5000 + i) for i in range(5)],
    }


def _write_oddin_archive(archive_dir: Path, *, reconnect: bool = False) -> Path:
    """oddin_state.jsonl: one seed (skipped), one live ws tick, one finish."""
    payload: dict[str, object] = {
        "matchStatus": "LIVE",
        "dataStatus": "VALID_DATA",
        "lastUpdatedAt": "2026-09-19 16:57:34.676000000 +0000 UTC",
        "mapPaused": False,
        "homeTeam": {"name": "LGD Gaming"},
        "awayTeam": {"name": "YB"},
        "currentMap": {
            "id": "map-3",
            "mapOrder": 3,
            "gameTime": 120,
            "homeTeam": _oddin_side("LGD Gaming", "RADIANT", "R"),
            "awayTeam": _oddin_side("YB", "DIRE", "D"),
        },
    }
    records: list[dict[str, object]] = [
        {"received_at_utc": "2026-09-19T16:57:35.000000Z", "event": "snapshot", "payload": payload},
        {"received_at_utc": "2026-09-19T16:57:36.000000Z", "event": "ws", "payload": payload},
    ]
    if reconnect:
        records.append(
            {
                "received_at_utc": "2026-09-19T16:58:00.000000Z",
                "event": "reconnect",
                "payload": None,
            }
        )
    records.append(
        {
            "received_at_utc": "2026-09-19T17:00:00.500000Z",
            "event": "ws",
            "payload": {
                **payload,
                "matchStatus": "FINISHED",
                "lastUpdatedAt": "2026-09-19 17:00:00.100000000 +0000 UTC",
            },
        }
    )
    feed_path = archive_dir / ODDIN_STATE_ARCHIVE_FILENAME
    feed_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return feed_path


def test_oddin_schedule_extraction(tmp_path: Path) -> None:
    archive_dir = tmp_path / "oddin-test-m3"
    _write_meta(
        archive_dir,
        match_id="oddin-test-m3",
        map_number=3,
        feed_source="oddin",
        grid_delay_s=None,
        oddin_match_id="bitsler-1",
        oddin_delay_s=3,
    )
    _write_oddin_archive(archive_dir)
    schedule = _extract(archive_dir)
    assert schedule.stats.tick_count == 2
    assert schedule.stats.terminal is True
    assert schedule.ticks[0].game_second == 120
    assert schedule.stats.delay_s == 3.0
    assert schedule.stats.delay_evidence == "meta"


def test_oddin_measured_delay_and_reconnect_marker(tmp_path: Path) -> None:
    archive_dir = tmp_path / "oddin-test-m3"
    _write_meta(
        archive_dir,
        match_id="oddin-test-m3",
        map_number=3,
        feed_source="oddin",
        grid_delay_s=None,
        oddin_match_id="bitsler-1",
        oddin_delay_s=None,
    )
    _write_oddin_archive(archive_dir, reconnect=True)
    schedule = _extract(archive_dir)
    # server_timestamp is a whole-second floor: 16:57:36.000 - 16:57:34 -> 2.0 s;
    # 17:00:00.500 - 17:00:00 -> 0.5 s; median 1.25 s.
    assert schedule.stats.delay_evidence == "measured"
    assert schedule.stats.delay_s == pytest.approx(1.25, abs=1e-9)
    assert len(schedule.interruptions) == 1
    mark = schedule.interruptions[0]
    assert mark.kind == "reconnect"
    assert mark.received_at_utc == "2026-09-19T16:58:00.000000Z"
    assert mark.seq_after == 1
    assert schedule.stats.interruption_count == 1


def test_grid_feed_gone_terminal_is_not_a_finish(tmp_path: Path) -> None:
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, terminal=False, feed_gone=True)
    schedule = _extract(archive_dir)
    assert schedule.stats.terminal is True
    assert schedule.stats.terminal_interrupted is True
    assert len(schedule.interruptions) == 1
    mark = schedule.interruptions[0]
    assert mark.kind == "feed_gone"
    assert mark.received_ns == schedule.ticks[-1].received_ns
    assert mark.seq_after == len(schedule.ticks) - 1


def _monkeypatch_universe(monkeypatch: pytest.MonkeyPatch, *markets: UniverseMarket) -> None:
    mapping = {market.condition_id: market for market in markets}

    def load_stub(game: str) -> dict[str, UniverseMarket]:
        return dict(mapping)

    monkeypatch.setattr("archive_index.index.load_universe", load_stub)


def _market_with_condition(condition_id: str) -> dict[str, object]:
    market = dict(cast(dict[str, object], _meta_document()["market"]))
    market["condition_id"] = condition_id
    return market


def _build(tmp_path: Path) -> Any:
    """Build an index over the prepared `archives` root into `out`."""
    return build_index({"t": tmp_path / "archives"}, tmp_path / "out", 1)


def _admissions(frame: Any) -> dict[str, str]:
    return dict(zip(frame["archive_id"], frame["admission"], strict=True))


def test_index_admits_and_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=300)
    result = _build(tmp_path)
    row = result.frame.iloc[0]
    assert row["admission"] == "admitted"
    assert bool(row["schedule_published"])
    assert schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1").is_file()

    # A second run reuses the stored schedule: extract_schedule must not run.
    def _fail_extract(*args: object, **kwargs: object) -> FeedSchedule:
        raise AssertionError("extract_schedule called on an unchanged feed")

    monkeypatch.setattr("archive_index.index.extract_schedule", _fail_extract)
    again = build_index({"t": tmp_path / "archives"}, tmp_path / "out", 1)
    assert again.frame.iloc[0]["schedule_fingerprint"] == row["schedule_fingerprint"]
    assert again.frame.iloc[0]["admission"] == "admitted"
    assert again.frame.iloc[0]["horn_at_utc"] == row["horn_at_utc"]


def test_index_refuses_archive_with_no_positive_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A feed that never passes 0 is not admitted, even when the window has ticks."""
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=-47, ticking=False)
    frame = _build(tmp_path).frame
    assert _admissions(frame)["grid-test-m1"] == "feed:no_horn"
    assert not schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1").exists()


def test_index_row_horn_matches_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The index horn column is the schedule horn, not match.json."""
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir, horn_at_utc="1999-01-01T00:00:00Z")
    _write_grid_archive(archive_dir, clock_seconds=300)
    result = _build(tmp_path)
    row = result.frame.set_index("archive_id").loc["grid-test-m1"]
    schedule = read_schedule(schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1"))
    assert row["horn_at_utc"] == schedule.identity.horn_at_utc
    assert row["horn_at_utc"] != "1999-01-01T00:00:00Z"


def test_index_unpublishes_schedule_when_feed_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=300)
    _build(tmp_path)
    schedule_file = schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1")
    assert schedule_file.is_file()
    (archive_dir / GRID_STATE_ARCHIVE_FILENAME).write_text("{bad line\n", encoding="utf-8")
    again = _build(tmp_path)
    assert _admissions(again.frame)["grid-test-m1"] == "feed:feed_corrupt"
    assert not schedule_file.exists()


def test_index_delay_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_universe(
        monkeypatch,
        _universe_market(condition_id="0xslow"),
        _universe_market(condition_id="0xsilent"),
        _universe_market(condition_id="0xmeasured"),
    )
    root = tmp_path / "archives"
    _write_meta(
        root / "slow-grid",
        match_id="slow-grid",
        grid_delay_s=90.0,
        market=_market_with_condition("0xslow"),
    )
    _write_grid_archive(root / "slow-grid", clock_seconds=300)
    _write_meta(
        root / "silent-grid",
        match_id="silent-grid",
        grid_delay_s=None,
        market=_market_with_condition("0xsilent"),
    )
    _write_grid_archive(
        root / "silent-grid",
        clock_seconds=300,
        tables=(),
        table_received=(),
        terminal_received="2026-08-24T11:08:00.000000Z",
    )
    _write_meta(
        root / "measured-grid",
        match_id="measured-grid",
        grid_delay_s=None,
        market=_market_with_condition("0xmeasured"),
    )
    _write_grid_archive(root / "measured-grid", clock_seconds=300)
    frame = _build(tmp_path).frame
    admissions = _admissions(frame)
    assert admissions["slow-grid"] == "feed:delay_over_limit"
    assert admissions["silent-grid"] == "feed:delay_unknown"
    assert admissions["measured-grid"] == "admitted"
    measured = frame.set_index("archive_id").loc["measured-grid"]
    assert measured["delay_evidence"] == "measured"
    assert measured["delay_s"] == TABLE_DELAY


def test_index_rejects_feed_gone_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=300, terminal=False, feed_gone=True)
    frame = _build(tmp_path).frame
    assert _admissions(frame)["grid-test-m1"] == "record:feed_gone"
    assert not schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1").exists()


def test_index_parallel_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=300)
    result = build_index({"t": tmp_path / "archives"}, tmp_path / "out", 2)
    assert _admissions(result.frame)["grid-test-m1"] == "admitted"
    assert schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1").is_file()


def test_index_missing_meta_and_missing_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    root = tmp_path / "archives"
    bare = root / "feed-no-meta"
    bare.mkdir(parents=True)
    (bare / GRID_STATE_ARCHIVE_FILENAME).write_text("{}\n", encoding="utf-8")
    _write_meta(root / "meta-no-feed", match_id="meta-no-feed")
    admissions = _admissions(_build(tmp_path).frame)
    assert admissions["feed-no-meta"] == "meta:missing"
    assert admissions["meta-no-feed"] == "feed:feed_missing"


def test_index_rejects_malformed_meta_and_corrupt_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    root = tmp_path / "archives"
    bad_meta = root / "bad-meta"
    bad_meta.mkdir(parents=True)
    (bad_meta / "match.json").write_text("{not json", encoding="utf-8")
    corrupt = root / "grid-test-m1"
    _write_meta(corrupt)
    (corrupt / GRID_STATE_ARCHIVE_FILENAME).write_text("{bad line\n", encoding="utf-8")
    admissions = _admissions(_build(tmp_path).frame)
    assert admissions["bad-meta"].startswith("meta:")
    assert admissions["grid-test-m1"] == "feed:feed_corrupt"


def test_index_flags_universe_miss_and_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    root = tmp_path / "archives"
    foreign_market = dict(cast(dict[str, object], _meta_document()["market"]))
    foreign_market["condition_id"] = "0xnot-in-universe"
    _write_meta(root / "unknown-market", match_id="unknown-market", market=foreign_market)
    _write_meta(root / "dup-a", match_id="same-match")
    _write_meta(root / "dup-b", match_id="same-match", joined_at_utc="2026-08-24T11:08:00.000000Z")
    admissions = _admissions(_build(tmp_path).frame)
    assert admissions["unknown-market"] == "identity:condition_not_in_universe"
    assert admissions["dup-a"] == "duplicate_of:dup-b"
    assert admissions["dup-b"] == "feed:feed_missing"


def test_index_no_terminal_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    _write_grid_archive(archive_dir, clock_seconds=300, terminal=False)
    summary = run_index({"t": tmp_path / "archives"}, tmp_path / "out", 1)
    assert summary.admitted == 0
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "record:no_terminal" in report
    assert (tmp_path / "out" / "index.parquet").is_file()
    assert not schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1").exists()


def _kill_board(team_index: int, score: int, occurred_at: str) -> ScoreboardPayload:
    """A live board frame with one side's score bumped and a fresh clock stamp."""
    board = copy.deepcopy(SCOREBOARD)
    board["games"][0]["teams"][team_index]["score"] = score
    board["games"][0]["gameClock"]["occurredAt"] = occurred_at
    return board


def test_grid_schedule_records_kill_gates(tmp_path: Path) -> None:
    """A score bump on a fresh board lands in kill_gates, not in ticks."""
    archive_dir = tmp_path / "grid-test-m1"
    _write_meta(archive_dir)
    kill = _kill_board(1, 23, "2026-08-24T11:07:55.000Z")
    _write_grid_archive(archive_dir, kill_frames=(("2026-08-24T11:07:56.200000Z", kill),))
    schedule = _extract(archive_dir)
    assert len(schedule.kill_gates) == 1
    gate = schedule.kill_gates[0]
    assert gate.received_ns == datetime_to_ns(parse_utc("2026-08-24T11:07:56.200000Z"))
    # LYNX (radiant) score 22 -> 23: dire is the victim; awaited = dire table deaths + 1.
    assert gate.dire.awaited_deaths == 23
    assert gate.dire.until_ns == gate.received_ns + 10 * 1_000_000_000
    assert gate.radiant.awaited_deaths == 24
    assert gate.radiant.until_ns == gate.received_ns
    assert schedule.stats.tick_count == 2


def test_index_reextracts_schedule_on_old_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prior row with an older schedule_schema_version forces a fresh extract.

    Ticks and stats come back identical; the only difference is the schema the
    old file could not have carried (kill_gates).
    """
    _monkeypatch_universe(monkeypatch, _universe_market())
    archive_dir = tmp_path / "archives" / "grid-test-m1"
    _write_meta(archive_dir)
    kill = _kill_board(1, 23, "2026-08-24T11:07:55.000Z")
    _write_grid_archive(
        archive_dir, clock_seconds=300, kill_frames=(("2026-08-24T11:07:56.200000Z", kill),)
    )
    first = _build(tmp_path)
    row = first.frame.iloc[0]
    assert row["schedule_schema_version"] == SCHEDULE_SCHEMA_VERSION
    schedule_file = schedule_path_for(tmp_path / "out", "t", "dota", "grid-test-m1")
    before = read_schedule(schedule_file)
    assert len(before.kill_gates) == 1

    payload = json.loads(schedule_file.read_text(encoding="utf-8"))
    payload.pop("kill_gates")
    payload["schema_version"] = SCHEDULE_SCHEMA_VERSION - 1
    schedule_file.write_text(json.dumps(payload), encoding="utf-8")
    stale_frame = first.frame.copy()
    stale_frame["schedule_schema_version"] = SCHEDULE_SCHEMA_VERSION - 1
    stale_frame.to_parquet(tmp_path / "out" / INDEX_FILENAME)

    calls = {"n": 0}
    real_extract = extract_schedule

    def counting_extract(*args: object, **kwargs: object) -> FeedSchedule:
        calls["n"] += 1
        return real_extract(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("archive_index.index.extract_schedule", counting_extract)
    again = build_index({"t": tmp_path / "archives"}, tmp_path / "out", 1)
    assert calls["n"] == 1
    new_row = again.frame.iloc[0]
    assert new_row["schedule_schema_version"] == SCHEDULE_SCHEMA_VERSION
    assert new_row["tick_count"] == row["tick_count"]
    republished = read_schedule(schedule_file)
    assert republished.ticks == before.ticks
    assert republished.stats == before.stats
    assert republished.kill_gates == before.kill_gates
