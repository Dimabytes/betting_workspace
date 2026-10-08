"""GRID reducer and socket rules on the recorded series-2995964 widget payloads."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import websockets
from grid_widget_fixtures import (
    KLIM,
    LYNX,
    SCOREBOARD,
    SERIES_TABLE,
    copy_series_table,
    foreign_table,
    make_portraitless_row,
    table_rows,
    table_with_extra_row,
    wrap,
)
from websockets.datastructures import Headers
from websockets.http11 import Response

from shared.utils.dota_levels import radiant_xp_advantage
from shared.utils.top_players import ZERO_TOP, build_top_player_features
from trader.archive_paths import archive_has_prior_session
from trader.archive_types import MatchMeta
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import summarize
from trader.grid_feed import FEED_GONE_FRAME, GridFrameReducer, GridOrientationError
from trader.grid_live_feed import GridLiveFeed
from trader.grid_widget_types import ScoreboardPayload, SeriesTablePayload
from trader.grid_widgets import MAX_CONSECUTIVE_FAILURES, clock_stamp_unix_seconds, parse_frame
from trader.live_feed import FeedEvent, FeedSource, KillTick, MatchPhase, unix_seconds_to_iso_z
from trader.paths import GRID_STATE_ARCHIVE_FILENAME, MATCH_META_FILENAME

OUTCOME_0 = "Team Lynx"
OUTCOME_1 = "Klim Sani4"
NOW = datetime(2026, 8, 24, 11, 7, 54, 653000, tzinfo=UTC)
OCCURRED_AT = "2026-08-24T11:07:52.653Z"
MATCH_ID = "8944931337"


def _feed() -> GridFrameReducer:
    """Pinned map 1 with market names matching GRID RADIANT/DIRE Lynx/Klim."""
    return GridFrameReducer(1, OUTCOME_0, OUTCOME_1, GAME_PROFILES["dota"])


def _grid_live_feed() -> GridLiveFeed:
    """Socket adapter for the recorded Lynx/Klim series."""
    return GridLiveFeed("2995964", 1, OUTCOME_0, OUTCOME_1, MATCH_ID, GAME_PROFILES["dota"])


def _read(reducer: GridFrameReducer, raw: str) -> FeedEvent | None:
    """Parse one raw frame and fold it into the reducer (kill frames go via _kill)."""
    event = reducer.reduce_frame(parse_frame(raw), NOW)
    assert not isinstance(event, KillTick)
    return event


def _kill(feed: GridFrameReducer, raw: str) -> FeedEvent | KillTick | None:
    """Reduce one frame that may be a kill marker."""
    return feed.reduce_frame(parse_frame(raw), NOW)


def _scoreboard_then_table(
    feed: GridFrameReducer, scoreboard: object, table: object, delay: int
) -> FeedEvent | None:
    """Store the scoreboard (no tick), then ingest one table frame."""
    assert _read(feed, wrap("series_scoreboard_v2", 0, scoreboard)) is None
    return _read(feed, wrap("series_table", delay, table))


def _invalid_status() -> websockets.InvalidStatus:
    """Handshake reject the live GRID widget uses when the series URL is gone."""
    return websockets.InvalidStatus(Response(403, "Forbidden", Headers(), b""))


class _InvalidStatusSocket:
    """Async context manager that fails on enter with InvalidStatus."""

    async def __aenter__(self) -> "_InvalidStatusSocket":
        raise _invalid_status()

    async def __aexit__(self, *args: object) -> None:
        del args


class _ClosedSocket:
    """Async context manager that fails on enter with a closed GRID socket."""

    async def __aenter__(self) -> "_ClosedSocket":
        raise websockets.ConnectionClosed(None, None)

    async def __aexit__(self, *args: object) -> None:
        del args


class _SilentClosedSocket:
    """Connects, then closes before any widget frame."""

    async def __aenter__(self) -> "_SilentClosedSocket":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    def __aiter__(self) -> "_SilentClosedSocket":
        return self

    async def __anext__(self) -> str:
        raise websockets.ConnectionClosed(None, None)


class _FrameSocket:
    """Async context manager that yields a scripted list of widget frames."""

    def __init__(self, frames: tuple[str, ...]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> "_FrameSocket":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    def __aiter__(self) -> "_FrameSocket":
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise websockets.ConnectionClosed(None, None)
        return self._frames.pop(0)


def test_second_is_clock_plus_stamp_age_minus_table_delay() -> None:
    event = _scoreboard_then_table(_feed(), SCOREBOARD, SERIES_TABLE, 8)
    assert event is not None
    assert event.snapshot.second == 2641
    assert event.snapshot.paused is False
    assert event.snapshot.phase is MatchPhase.IN_PROGRESS


def test_paused_clock_does_not_add_stamp_age() -> None:
    paused = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    paused["games"][0]["gameClock"]["isTicking"] = False
    event = _scoreboard_then_table(_feed(), paused, SERIES_TABLE, 8)
    assert event is not None
    assert event.snapshot.second == 2639
    assert event.snapshot.paused is True


def test_repeated_table_frame_yields_no_event() -> None:
    feed = _feed()
    first = _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    assert first is not None
    assert _read(feed, wrap("series_table", 8, SERIES_TABLE)) is None
    assert _read(feed, wrap("series_table", 60, SERIES_TABLE)) is None


def test_feed_gone_after_live_board_emits_finished() -> None:
    """A widget-gone sentinel after a pinned live board is the same FINISHED tick."""
    feed = _feed()
    live = _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    assert live is not None
    event = _read(feed, FEED_GONE_FRAME)
    assert event is not None
    assert event.snapshot.phase is MatchPhase.FINISHED


def test_feed_gone_without_a_board_yields_nothing() -> None:
    """Feed-gone with no pinned sides is not a fake finish."""
    assert _read(_feed(), FEED_GONE_FRAME) is None


def test_table_for_another_map_yields_no_event() -> None:
    other = cast(SeriesTablePayload, json.loads(json.dumps(SERIES_TABLE)))
    other["stateGroups"][1]["states"][0]["sequenceNumber"] = 2
    assert _scoreboard_then_table(_feed(), SCOREBOARD, other, 8) is None


def test_active_game_mismatch_yields_no_event() -> None:
    other_active = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    other_active["activeGameIndex"] = 1
    assert _scoreboard_then_table(_feed(), other_active, SERIES_TABLE, 8) is None


def test_grid_orients_when_discovery_sides_are_stale() -> None:
    """GRID map sides win: outcome 0 named Dire still ticks, YES is not Radiant."""
    feed = GridFrameReducer(1, OUTCOME_1, OUTCOME_0, GAME_PROFILES["dota"])
    event = _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    assert event is not None
    assert event.yes_is_radiant is False


def test_unresolvable_market_names_raise() -> None:
    """A complete live board whose names cannot orient against the market raises."""
    feed = GridFrameReducer(1, "Team Synapse", "Inner Circle", GAME_PROFILES["dota"])
    with pytest.raises(GridOrientationError, match="orientation unresolved"):
        _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)


def test_upcoming_scoreboard_does_not_lock_sides() -> None:
    """A full upcoming board stays pending, so a table on it is not a tick."""
    upcoming = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    upcoming["games"][0]["status"] = "upcoming"
    assert _scoreboard_then_table(_feed(), upcoming, SERIES_TABLE, 8) is None


def test_draft_without_teams_then_live_board_then_table_ticks() -> None:
    """Empty draft sides stay pending; the first complete live board plus table ticks."""
    draft = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    draft["games"][0]["teams"] = []
    feed = _feed()
    assert _read(feed, wrap("series_scoreboard_v2", 0, draft)) is None
    assert _read(feed, wrap("series_scoreboard_v2", 0, SCOREBOARD)) is None
    event = _read(feed, wrap("series_table", 8, SERIES_TABLE))
    assert event is not None
    assert event.snapshot.phase is MatchPhase.IN_PROGRESS


def test_market_inner_circle_ticks_when_grid_omits_steam_suffix() -> None:
    """Steam 'Inner Circle x Insanity' is not a gate; market and GRID names tick."""
    board = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    names = {LYNX: "Inner Circle", KLIM: "FTS"}
    for team in board["series"]["teams"]:
        team["name"] = names[team["id"]]
    feed = GridFrameReducer(1, "Inner Circle", "FTS", GAME_PROFILES["dota"])
    event = _scoreboard_then_table(feed, board, SERIES_TABLE, 8)
    assert event is not None
    assert event.snapshot.phase is MatchPhase.IN_PROGRESS


def test_features_come_from_series_table_rows() -> None:
    event = _scoreboard_then_table(_feed(), SCOREBOARD, SERIES_TABLE, 8)
    assert event is not None
    snapshot = event.snapshot
    radiant_nw = 23074 + 22479 + 22279 + 10884 + 8641
    dire_nw = 30784 + 26369 + 14776 + 13263 + 26438
    assert snapshot.radiant_nw == radiant_nw
    assert snapshot.dire_nw == dire_nw
    assert snapshot.radiant_nw_adv == radiant_nw - dire_nw
    assert snapshot.radiant_xp_adv == radiant_xp_advantage(
        [20, 19, 18, 14, 13], [23, 20, 20, 20, 25]
    )
    assert snapshot.deaths_radiant == 24
    assert snapshot.deaths_dire == 22
    assert snapshot.top == build_top_player_features(
        [23074, 22479, 22279, 10884, 8641], [30784, 26369, 14776, 13263, 26438]
    )


def test_substitute_row_is_dropped_from_side_features() -> None:
    """A sixth dire row with no portrait is roster noise: dire_nw and top ignore it."""
    ghost = make_portraitless_row(KLIM, "JANTER", 600)
    event = _scoreboard_then_table(_feed(), SCOREBOARD, table_with_extra_row(ghost), 8)
    assert event is not None
    snapshot = event.snapshot
    assert snapshot.dire_nw == 30784 + 26369 + 14776 + 13263 + 26438
    assert snapshot.top == build_top_player_features(
        [23074, 22479, 22279, 10884, 8641], [30784, 26369, 14776, 13263, 26438]
    )


def test_horn_unix_seconds_is_occurred_at_minus_current_seconds() -> None:
    event = _scoreboard_then_table(_feed(), SCOREBOARD, SERIES_TABLE, 8)
    assert event is not None
    assert event.horn_unix_seconds == clock_stamp_unix_seconds(OCCURRED_AT) - 2647
    assert event.snapshot.server_timestamp == clock_stamp_unix_seconds(OCCURRED_AT)


def test_finished_map_yields_terminal_event_without_a_new_table() -> None:
    finished = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished["games"][0]["status"] = "finished"
    event = _read(_feed(), wrap("series_scoreboard_v2", 0, finished))
    assert event is not None
    assert event.snapshot.phase is MatchPhase.FINISHED
    assert event.snapshot.finished is True
    assert event.snapshot.second == 2649
    assert event.snapshot.radiant_nw == 0
    assert event.snapshot.dire_nw == 0
    assert event.snapshot.radiant_nw_adv == 0
    assert event.snapshot.radiant_xp_adv == 0
    assert event.snapshot.deaths_radiant == 0
    assert event.snapshot.deaths_dire == 0
    assert event.snapshot.top == ZERO_TOP


def test_live_scoreboard_change_yields_no_event() -> None:
    feed = _feed()
    assert _read(feed, wrap("series_scoreboard_v2", 0, SCOREBOARD)) is None
    later = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    later["games"][0]["gameClock"]["currentSeconds"] = 2650
    assert _read(feed, wrap("series_scoreboard_v2", 0, later)) is None


def test_source_is_grid() -> None:
    event = _scoreboard_then_table(_feed(), SCOREBOARD, SERIES_TABLE, 8)
    assert event is not None
    assert event.source is FeedSource.GRID
    assert event.received_at_utc == "2026-08-24T11:07:54.653000Z"


def test_empty_payload_yields_no_event() -> None:
    raw = json.dumps(
        {
            "service": "integrity_safe_series_table",
            "delay": 8,
            "scope": {"id": "2995964", "type": ""},
            "data": [],
        }
    )
    assert _read(_feed(), raw) is None


def test_closed_socket_reconnects_to_the_same_series_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []
    finished = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished["games"][0]["status"] = "finished"
    frames = (wrap("series_scoreboard_v2", 0, finished),)
    attempts = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _ClosedSocket | _FrameSocket:
        del kwargs
        urls.append(url)
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _ClosedSocket()
        return _FrameSocket(frames)

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert len(urls) == 2
    assert urls[0] == urls[1]
    assert "2995964" in urls[0]
    assert "delay=zero" in urls[0]
    assert len(events) == 1
    assert events[0].snapshot.phase is MatchPhase.FINISHED
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["frame"] == frames[0]


def test_ticks_archives_every_raw_frame_before_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicates, empty payloads, and non-ticks still land in grid_state.jsonl."""
    empty = json.dumps(
        {
            "service": "integrity_safe_series_table",
            "delay": 8,
            "scope": {"id": "2995964", "type": ""},
            "data": [],
        }
    )
    scoreboard = wrap("series_scoreboard_v2", 0, SCOREBOARD)
    table = wrap("series_table", 8, SERIES_TABLE)
    finished_board = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished_board["games"][0]["status"] = "finished"
    finished = wrap("series_scoreboard_v2", 0, finished_board)
    frames = (empty, scoreboard, table, table, finished)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert [event.snapshot.phase for event in events] == [
        MatchPhase.IN_PROGRESS,
        MatchPhase.FINISHED,
    ]
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert [record["frame"] for record in records] == list(frames)
    assert all("received_at_utc" in record for record in records)


def test_nonempty_grid_state_is_not_a_prior_session(tmp_path: Path) -> None:
    """Feed frames before the first yield must not refuse a new session journal."""
    archive_dir = tmp_path / MATCH_ID
    archive_dir.mkdir()
    assert archive_has_prior_session(archive_dir) is False
    (archive_dir / GRID_STATE_ARCHIVE_FILENAME).write_text("{}\n", encoding="utf-8")
    assert archive_has_prior_session(archive_dir) is False
    (archive_dir / MATCH_META_FILENAME).write_text("{}\n", encoding="utf-8")
    assert archive_has_prior_session(archive_dir) is True


def test_closed_sockets_without_a_frame_stop_the_feed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five empty closes stop the feed; the counter does not reset on connect."""
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _SilentClosedSocket:
        del url, kwargs
        opens["n"] += 1
        return _SilentClosedSocket()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert events == []
    assert opens["n"] == MAX_CONSECUTIVE_FAILURES


def test_archived_frame_resets_closed_socket_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame after four empty closes resets the counter so a later tick still emits."""
    finished = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished["games"][0]["status"] = "finished"
    frames = (wrap("series_scoreboard_v2", 0, finished),)
    attempts = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _SilentClosedSocket | _FrameSocket:
        del url, kwargs
        attempts["n"] += 1
        if attempts["n"] <= 4:
            return _SilentClosedSocket()
        return _FrameSocket(frames)

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].snapshot.phase is MatchPhase.FINISHED
    assert attempts["n"] == 5


def test_ticks_orients_when_discovery_sides_are_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID orients the swapped market names; that is not a crash."""
    finished = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished["games"][0]["status"] = "finished"
    frames = (wrap("series_scoreboard_v2", 0, finished),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)
    feed = GridLiveFeed("2995964", 1, OUTCOME_1, OUTCOME_0, MATCH_ID, GAME_PROFILES["dota"])

    async def collect() -> list[FeedEvent]:
        return [event async for event in feed.ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].yes_is_radiant is False
    assert events[0].snapshot.phase is MatchPhase.FINISHED


def _early_scoreboard() -> ScoreboardPayload:
    """Live scoreboard whose clock is still before BUY_CUTOFF_SECOND.

    Pause the clock so GridLiveFeed's wall `now` does not add days of stamp age
    onto the recorded occurredAt.
    """
    board = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    clock = board["games"][0]["gameClock"]
    clock["currentSeconds"] = 100
    clock["isTicking"] = False
    return board


class _ExhaustedSocket:
    """Scripted frames, then StopAsyncIteration — not a GRID ConnectionClosed."""

    def __init__(self, frames: tuple[str, ...]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> "_ExhaustedSocket":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    def __aiter__(self) -> "_ExhaustedSocket":
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def test_clean_socket_end_does_not_rearchive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A StopAsyncIteration socket must not reconnect and rewrite grid_state.jsonl."""
    frames = (
        wrap("series_scoreboard_v2", 0, _early_scoreboard()),
        wrap("series_table", 8, SERIES_TABLE),
    )
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _ExhaustedSocket:
        del url, kwargs
        opens["n"] += 1
        return _ExhaustedSocket(frames)

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert opens["n"] == 1
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert [record["frame"] for record in records] == list(frames)


def test_invalid_status_after_cutoff_finishes_without_five_reconnects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-cutoff InvalidStatus writes the sentinel and yields FINISHED on the next handshake."""
    live_frames = (
        wrap("series_scoreboard_v2", 0, SCOREBOARD),
        wrap("series_table", 8, SERIES_TABLE),
    )
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket | _InvalidStatusSocket:
        del url, kwargs
        opens["n"] += 1
        if opens["n"] == 1:
            return _FrameSocket(live_frames)
        return _InvalidStatusSocket()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert [event.snapshot.phase for event in events] == [
        MatchPhase.IN_PROGRESS,
        MatchPhase.FINISHED,
    ]
    assert opens["n"] == 2
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["frame"] == FEED_GONE_FRAME


def test_invalid_status_before_cutoff_does_not_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """InvalidStatus before cutoff burns the reconnect budget and does not synthesize FINISHED."""
    live_frames = (
        wrap("series_scoreboard_v2", 0, _early_scoreboard()),
        wrap("series_table", 8, SERIES_TABLE),
    )
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket | _InvalidStatusSocket:
        del url, kwargs
        opens["n"] += 1
        if opens["n"] == 1:
            return _FrameSocket(live_frames)
        return _InvalidStatusSocket()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert [event.snapshot.phase for event in events] == [MatchPhase.IN_PROGRESS]
    assert events[0].snapshot.second < 540
    # Scripted frames then ConnectionClosed counts as the first of five faults.
    assert opens["n"] == MAX_CONSECUTIVE_FAILURES
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert all(record["frame"] != FEED_GONE_FRAME for record in records)


def test_five_closed_sockets_after_cutoff_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five ConnectionClosed after a cutoff tick still synthesize FINISHED."""
    live_frames = (
        wrap("series_scoreboard_v2", 0, SCOREBOARD),
        wrap("series_table", 8, SERIES_TABLE),
    )
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket | _SilentClosedSocket:
        del url, kwargs
        opens["n"] += 1
        if opens["n"] == 1:
            return _FrameSocket(live_frames)
        return _SilentClosedSocket()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert [event.snapshot.phase for event in events] == [
        MatchPhase.IN_PROGRESS,
        MatchPhase.FINISHED,
    ]
    # Scripted frames then ConnectionClosed counts as the first of five faults.
    assert opens["n"] == MAX_CONSECUTIVE_FAILURES
    archive = tmp_path / MATCH_ID / GRID_STATE_ARCHIVE_FILENAME
    records = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["frame"] == FEED_GONE_FRAME


def test_invalid_status_without_a_live_tick_does_not_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handshake InvalidStatus with no live tick is not a fake finish."""
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _InvalidStatusSocket:
        del url, kwargs
        opens["n"] += 1
        return _InvalidStatusSocket()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.asyncio.sleep", no_sleep)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent]:
        return [event async for event in _grid_live_feed().ticks() if isinstance(event, FeedEvent)]

    events = asyncio.run(collect())
    assert events == []
    assert opens["n"] == MAX_CONSECUTIVE_FAILURES


def _kill_board(
    scoreboard: object, *, team_index: int, score: int, occurred_at: str | None = None
) -> str:
    """A live board frame with one side's score changed and a fresh clock stamp."""
    board = cast(ScoreboardPayload, json.loads(json.dumps(scoreboard)))
    board["games"][0]["teams"][team_index]["score"] = score
    if occurred_at is not None:
        board["games"][0]["gameClock"]["occurredAt"] = occurred_at
    return wrap("series_scoreboard_v2", 0, board)


def _bumped_table_deaths(row_index: int, deaths: int) -> SeriesTablePayload:
    """A SERIES_TABLE deep copy with one player row's Deaths rewritten."""
    table = copy_series_table()
    table_rows(table)[row_index]["Deaths"]["value"] = deaths
    return table


def test_first_scoreboard_sets_kill_base_without_a_gate() -> None:
    assert _read(_feed(), wrap("series_scoreboard_v2", 0, SCOREBOARD)) is None


def test_score_growth_emits_kill_tick_for_the_victim() -> None:
    """LYNX (radiant) score +1: dire is the victim; awaited is dire table deaths + 1."""
    feed = _feed()
    _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    event = _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=23))
    assert isinstance(event, KillTick)
    assert event.dire.awaited_deaths == 23
    assert event.dire.seconds_left == pytest.approx(10.0)
    assert event.radiant.awaited_deaths == 24
    assert event.radiant.seconds_left == 0.0
    assert event.received_at_utc == "2026-08-24T11:07:54.653000Z"


def test_stale_scoreboard_frame_opens_no_kill_gate() -> None:
    feed = _feed()
    _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    stale = _kill_board(SCOREBOARD, team_index=1, score=23, occurred_at="2026-08-24T11:07:40.000Z")
    assert _read(feed, stale) is None


def test_score_drop_and_return_emits_no_kill_tick() -> None:
    feed = _feed()
    _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    assert _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=21)) is None
    assert _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=22)) is None


def test_expired_wait_resets_base_to_latest_table_deaths() -> None:
    """A kill after `until` rebases awaited on the newest table, not the old wait."""
    feed = _feed()
    _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    first = _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=23))
    assert isinstance(first, KillTick)
    assert first.dire.awaited_deaths == 23
    later = NOW + timedelta(seconds=11)
    later_stamp = "2026-08-24T11:08:04.653Z"
    # the table caught up with the first kill: KLIM (dire) deaths 22 -> 23
    table = _bumped_table_deaths(row_index=0, deaths=4)
    caught_up = feed.reduce_frame(parse_frame(wrap("series_table", 8, table)), later)
    assert isinstance(caught_up, FeedEvent)
    second = feed.reduce_frame(
        parse_frame(_kill_board(SCOREBOARD, team_index=1, score=24, occurred_at=later_stamp)),
        later,
    )
    assert isinstance(second, KillTick)
    assert second.dire.awaited_deaths == 24
    assert second.dire.seconds_left == pytest.approx(10.0)


def test_degenerate_table_is_no_tick_and_keeps_the_last_good() -> None:
    """A one-row foreign table emits no tick and does not replace the stored table."""
    feed = _feed()
    assert _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8) is not None
    assert _read(feed, wrap("series_table", 8, foreign_table())) is None
    later = _bumped_table_deaths(row_index=0, deaths=4)
    event = _read(feed, wrap("series_table", 8, later))
    assert event is not None
    assert event.snapshot.deaths_dire == 23


def test_kill_after_a_degenerate_table_awaits_last_good_deaths() -> None:
    """A rejected table does not zero the kill gate: awaited is last-good dire deaths + 1."""
    feed = _feed()
    _scoreboard_then_table(feed, SCOREBOARD, SERIES_TABLE, 8)
    assert _read(feed, wrap("series_table", 8, foreign_table())) is None
    event = _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=23))
    assert isinstance(event, KillTick)
    assert event.dire.awaited_deaths == 23


def test_degenerate_table_before_lock_is_dropped_on_lock() -> None:
    """A foreign table stored pre-lock is dropped at lock: a kill awaits only the growth."""
    feed = _feed()
    assert _read(feed, wrap("series_table", 8, foreign_table())) is None
    assert _read(feed, wrap("series_scoreboard_v2", 0, SCOREBOARD)) is None
    event = _kill(feed, _kill_board(SCOREBOARD, team_index=1, score=23))
    assert isinstance(event, KillTick)
    assert event.dire.awaited_deaths == 1
    assert event.dire.seconds_left == pytest.approx(10.0)


def test_live_feed_yields_kill_tick_without_reading_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A KillTick has no snapshot; cutoff and finished checks must skip it."""
    kill = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    kill["games"][0]["teams"][1]["score"] = 23
    kill["games"][0]["gameClock"]["occurredAt"] = datetime.now(UTC).isoformat()
    finished = cast(ScoreboardPayload, json.loads(json.dumps(SCOREBOARD)))
    finished["games"][0]["status"] = "finished"
    frames = (
        wrap("series_scoreboard_v2", 0, SCOREBOARD),
        wrap("series_table", 8, SERIES_TABLE),
        wrap("series_scoreboard_v2", 0, kill),
        wrap("series_scoreboard_v2", 0, finished),
    )

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.grid_live_feed.websockets.connect", fake_connect)
    monkeypatch.setattr("trader.grid_live_feed.TRADER_DIR", tmp_path)

    async def collect() -> list[FeedEvent | KillTick]:
        return [event async for event in _grid_live_feed().ticks()]

    events = asyncio.run(collect())
    assert len(events) == 3
    assert isinstance(events[0], FeedEvent)
    assert isinstance(events[1], KillTick)
    assert events[1].dire.awaited_deaths == 23
    assert isinstance(events[2], FeedEvent)
    assert events[2].snapshot.finished


def test_summarize_trusted_horn_is_after_pre_horn_pause(tmp_path: Path) -> None:
    """A pause at a negative clock must not become the archive horn."""
    archive_dir = tmp_path / "grid-pause"
    archive_dir.mkdir()
    paused_at = "2026-08-24T10:19:13.000Z"
    live_at = "2026-08-24T10:27:09.000Z"
    paused = copy.deepcopy(SCOREBOARD)
    paused["games"][0]["gameClock"]["isTicking"] = False
    paused["games"][0]["gameClock"]["currentSeconds"] = -47
    paused["games"][0]["gameClock"]["occurredAt"] = paused_at
    live = copy.deepcopy(SCOREBOARD)
    live["games"][0]["gameClock"]["isTicking"] = True
    live["games"][0]["gameClock"]["currentSeconds"] = 1
    live["games"][0]["gameClock"]["occurredAt"] = live_at
    later_table = copy_series_table()
    row = table_rows(later_table)[0]
    row["NetWorth"]["value"] = (row["NetWorth"]["value"] or 0) + 1000
    records = [
        json.dumps(
            {"received_at_utc": paused_at, "frame": wrap("series_scoreboard_v2", 0, paused)}
        ),
        json.dumps({"received_at_utc": paused_at, "frame": wrap("series_table", 0, SERIES_TABLE)}),
        json.dumps({"received_at_utc": live_at, "frame": wrap("series_scoreboard_v2", 0, live)}),
        json.dumps({"received_at_utc": live_at, "frame": wrap("series_table", 0, later_table)}),
    ]
    (archive_dir / GRID_STATE_ARCHIVE_FILENAME).write_text(
        "\n".join(records) + "\n", encoding="utf-8"
    )
    document = cast(
        MatchMeta,
        {
            "map_number": 1,
            "game": "dota",
            "market": {"outcome_0_name": OUTCOME_0, "outcome_1_name": OUTCOME_1},
        },
    )
    outcome = summarize(archive_dir, document)
    assert outcome.trusted_horn == unix_seconds_to_iso_z(clock_stamp_unix_seconds(live_at) - 1)
