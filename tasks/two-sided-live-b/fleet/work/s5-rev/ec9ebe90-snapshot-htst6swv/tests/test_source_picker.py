"""Source picker: delay gate, Steam-on-tie, and the GRID widget probe."""

# pyright: reportPrivateUsage=false, reportUnusedFunction=false

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
import websockets
from grid_widget_fixtures import SCOREBOARD, SERIES_TABLE, wrap
from websockets.datastructures import Headers
from websockets.http11 import Response

from trader.grid_widgets import map_number_from_scoreboard
from trader.live_feed import FeedSource
from trader.oddin_types import VALID_DATA, OddinTick, WidgetConfig
from trader.source_picker import (
    FeedCandidate,
    pick_source,
    probe_grid_delay,
    probe_grid_scoreboard_cycle,
    probe_grid_scoreboards,
    probe_oddin_delay,
)


def _candidates(grid: int | None, oddin: int | None) -> list[FeedCandidate]:
    """Typed candidate list; a missing delay is a dropped source, not a row."""
    rows: list[FeedCandidate] = []
    if grid is not None:
        rows.append(FeedCandidate(FeedSource.GRID, grid))
    if oddin is not None:
        rows.append(FeedCandidate(FeedSource.ODDIN, oddin))
    return rows


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

    async def recv(self) -> str:
        if not self._frames:
            raise websockets.ConnectionClosed(None, None)
        return self._frames.pop(0)

    async def send(self, message: str) -> None:
        del message


@pytest.mark.parametrize(
    ("grid", "oddin", "expected"),
    [
        (8, None, FeedSource.GRID),
        (61, None, FeedSource.GRID),
        (62, None, None),
        (None, None, None),
        (None, 15, FeedSource.ODDIN),
        (None, 62, None),
        (8, 15, FeedSource.GRID),
        (30, 8, FeedSource.ODDIN),
        (90, 15, FeedSource.ODDIN),
        (30, 30, FeedSource.GRID),
        (62, 62, None),
    ],
)
def test_pick_source_picks_faster_usable_or_skips(
    grid: int | None, oddin: int | None, expected: FeedSource | None
) -> None:
    """Delay gate 61s, min delay wins, a tie is GRID over Oddin."""
    picked = pick_source(_candidates(grid, oddin))
    assert (None if picked is None else picked.source) is expected


def test_probe_grid_delay_uses_the_table_not_the_scoreboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model waits on series_table. A zero-delay scoreboard is not the lag."""
    frames = (
        wrap("series_scoreboard_v2", 0, SCOREBOARD),
        wrap("series_table", 8, SERIES_TABLE),
    )

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert asyncio.run(probe_grid_delay("2995964", 1)) == 8


def test_probe_grid_delay_ignores_a_scoreboard_without_a_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft scoreboard with no table is not a delay the picker can use."""
    draft = json.loads(json.dumps(SCOREBOARD))
    draft["games"][0]["status"] = "upcoming"
    frames = (wrap("series_scoreboard_v2", 8, draft),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert asyncio.run(probe_grid_delay("2995964", 1)) is None


def test_probe_grid_delay_returns_none_when_pinned_map_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table for another map is not a delay."""
    frames = (wrap("series_table", 8, SERIES_TABLE),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert asyncio.run(probe_grid_delay("2995964", 2)) is None


def test_probe_grid_delay_reads_a_table_only_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pinned map's table delay is enough; the scoreboard is not required."""
    frames = (wrap("series_table", 8, SERIES_TABLE),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert asyncio.run(probe_grid_delay("2995964", 1)) == 8


def test_probe_grid_delay_returns_none_on_closed_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed widget socket is a failed probe, not a daemon crash."""

    def fake_connect(url: str, **kwargs: object) -> _ClosedSocket:
        del url, kwargs
        return _ClosedSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert asyncio.run(probe_grid_delay("2995964", 1)) is None


def test_probe_timeout_logs_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A scoreboard probe TimeoutError is debug, not the info line used for socket errors."""

    class _HangSocket:
        """Connects but never yields a frame, so asyncio.timeout fires."""

        async def __aenter__(self) -> "_HangSocket":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        def __aiter__(self) -> "_HangSocket":
            return self

        async def __anext__(self) -> str:
            await asyncio.Event().wait()
            raise StopAsyncIteration

    def fake_connect(url: str, **kwargs: object) -> _HangSocket:
        del url, kwargs
        return _HangSocket()

    monkeypatch.setattr("trader.source_picker.GRID_FEED_STALE_SECONDS", 0.05)
    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    with caplog.at_level(logging.DEBUG, logger="trader.source_picker"):
        result = probe_grid_scoreboards(["2995964"])
    assert result == {}
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("TimeoutError" in msg and "scoreboard" in msg for msg in messages)
    assert not any(
        rec.levelno >= logging.INFO and "TimeoutError" in rec.getMessage() for rec in caplog.records
    )


def test_probe_grid_scoreboards_reads_the_live_game_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first scoreboard of a live Game 3 yields that series board on map 3."""
    raw = json.loads(json.dumps(SCOREBOARD))
    game = raw["games"][0]
    raw["games"] = [json.loads(json.dumps(game)) for _ in range(3)]
    raw["activeGameIndex"] = 2
    raw["games"][2]["centeredInfoText"] = "Game 3"
    raw["games"][2]["status"] = "live"
    frames = (wrap("series_scoreboard_v2", 0, raw),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    boards = probe_grid_scoreboards(["2995964"])
    assert map_number_from_scoreboard(boards["2995964"]) == 3


def test_probe_grid_scoreboards_reads_upcoming_map_two_after_finished_map_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finished map 1 with upcoming map 2 yields the map-2 board, even with empty teams."""
    raw = json.loads(json.dumps(SCOREBOARD))
    raw["games"][0]["status"] = "finished"
    raw["series"]["teams"][0]["score"] = 1
    raw["series"]["teams"][1]["score"] = 0
    game_two = json.loads(json.dumps(raw["games"][0]))
    game_two["status"] = "upcoming"
    game_two["centeredInfoText"] = "Game 2"
    game_two["teams"] = []
    game_two["gameClock"]["currentSeconds"] = 0
    raw["games"].append(game_two)
    frames = (wrap("series_scoreboard_v2", 0, raw),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    boards = probe_grid_scoreboards(["2995964"])
    board = boards["2995964"]
    assert board.game_number == 2
    assert board.game_status == "upcoming"
    assert board.teams == ()


def test_probe_grid_scoreboards_drops_a_table_only_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table-only socket carries no scoreboard."""
    frames = (wrap("series_table", 8, SERIES_TABLE),)

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert probe_grid_scoreboards(["2995964"]) == {}


def test_probe_grid_scoreboards_drops_a_closed_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed widget socket is a failed scoreboard probe, not a crash."""

    def fake_connect(url: str, **kwargs: object) -> _ClosedSocket:
        del url, kwargs
        return _ClosedSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert probe_grid_scoreboards(["2995964"]) == {}


def test_probe_grid_scoreboards_probes_every_series_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two series share one event loop; only the one with a scoreboard survives."""
    raw = json.loads(json.dumps(SCOREBOARD))
    boards = {
        "s-live": (wrap("series_scoreboard_v2", 0, raw),),
        "s-table": (wrap("series_table", 8, SERIES_TABLE),),
    }
    opened: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del kwargs
        series_id = url.rsplit("/", 1)[-1].split("?", 1)[0]
        opened.append(series_id)
        return _FrameSocket(boards[series_id])

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    probed = probe_grid_scoreboards(["s-table", "s-live", "s-live"])
    assert map_number_from_scoreboard(probed["s-live"]) == 1
    assert set(probed) == {"s-live"}
    assert sorted(opened) == ["s-live", "s-table"]


def test_probe_grid_scoreboards_without_a_series_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty series set is not a probe."""

    def fail_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        raise AssertionError("no socket for an empty series set")

    monkeypatch.setattr("trader.source_picker.websockets.connect", fail_connect)
    assert probe_grid_scoreboards([]) == {}


def test_invalid_status_does_not_skip_the_next_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """InvalidStatus is this cycle's miss; the next probe still opens a socket."""
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _InvalidStatusSocket:
        del url, kwargs
        opens["n"] += 1
        return _InvalidStatusSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert probe_grid_scoreboards(["2995964"]) == {}
    assert opens["n"] == 1
    assert probe_grid_scoreboards(["2995964"]) == {}
    assert opens["n"] == 2


def test_probe_grid_scoreboard_cycle_reports_unpublished_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handshake reject lands in unpublished and is not a board."""

    def fake_connect(url: str, **kwargs: object) -> _InvalidStatusSocket:
        del url, kwargs
        return _InvalidStatusSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    cycle = probe_grid_scoreboard_cycle(["2995964"])
    assert cycle.boards == {}
    assert cycle.unpublished == frozenset({"2995964"})


def test_closed_socket_does_not_skip_the_next_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectionClosed is a blip; the next cycle still opens a socket."""
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _ClosedSocket:
        del url, kwargs
        opens["n"] += 1
        return _ClosedSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert probe_grid_scoreboards(["2995964"]) == {}
    assert probe_grid_scoreboards(["2995964"]) == {}
    assert opens["n"] == 2


def test_probe_grid_delay_still_connects_after_scoreboard_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe_grid_delay does not share a skip cache with scoreboard probes."""
    opens = {"n": 0}

    def fake_connect(url: str, **kwargs: object) -> _InvalidStatusSocket:
        del url, kwargs
        opens["n"] += 1
        return _InvalidStatusSocket()

    monkeypatch.setattr("trader.source_picker.websockets.connect", fake_connect)
    assert probe_grid_scoreboards(["2995964"]) == {}
    assert asyncio.run(probe_grid_delay("2995964", 1)) is None
    assert opens["n"] == 2


def _oddin_tick(
    map_order: int | None = 2,
    data_status: str = VALID_DATA,
    updated: str = "2026-09-19 16:57:35.000000000 +0000 UTC",
) -> OddinTick:
    """One projected Oddin snapshot for delay-probe tests."""
    return OddinTick(
        match_status="LIVE",
        data_status=data_status,
        last_updated_at=updated,
        map_paused=False,
        home_name="Aurora",
        away_name="Secret",
        home_score=0,
        away_score=0,
        map_order=map_order,
        game_time=100,
        radiant=None,
        dire=None,
    )


def _patch_oddin_probe(
    monkeypatch: pytest.MonkeyPatch, frames: tuple[str, ...], tick: OddinTick
) -> None:
    """Widget config, key, decrypt, projection, and a scripted Oddin socket."""

    def fake_config(oddin_match_id: str) -> WidgetConfig:
        del oddin_match_id
        return WidgetConfig(brand_token="token", match_id="encoded")

    def fake_open_envelope(envelope: str, key: bytes) -> dict[str, object]:
        del envelope, key
        return {}

    def fake_project_tick(payload: object) -> OddinTick:
        del payload
        return tick

    def fake_connect(url: str, **kwargs: object) -> _FrameSocket:
        del url, kwargs
        return _FrameSocket(frames)

    monkeypatch.setattr("trader.source_picker.widget_config", fake_config)
    monkeypatch.setattr("trader.source_picker.load_feed_key", lambda: b"0" * 32)
    monkeypatch.setattr("trader.oddin_client.open_envelope", fake_open_envelope)
    monkeypatch.setattr("trader.source_picker.project_tick", fake_project_tick)
    monkeypatch.setattr("trader.oddin_client.websockets.connect", fake_connect)


class _FrozenClock:
    """datetime stand-in so the Oddin probe's received-at is a fixed instant."""

    UTC = UTC

    @staticmethod
    def now(tz: object = None) -> datetime:
        del tz
        return datetime(2026, 9, 19, 16, 57, 50, tzinfo=UTC)


def test_probe_oddin_delay_returns_rounded_age(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first VALID_DATA snapshot of the pinned map yields received - lastUpdatedAt."""
    frames = (
        json.dumps({"type": "connection_ack"}),
        json.dumps(
            {
                "type": "next",
                "payload": {"data": {"onDota2ScoreboardFeedData": {"id": "x", "data": "env"}}},
            }
        ),
    )
    _patch_oddin_probe(monkeypatch, frames, _oddin_tick())
    monkeypatch.setattr("trader.source_picker.datetime", _FrozenClock)
    assert asyncio.run(probe_oddin_delay("od:match:1", 2)) == 15


def test_probe_oddin_delay_skips_wrong_map_and_invalid_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong mapOrder or missing VALID_DATA is not a delay."""
    frames = (
        json.dumps({"type": "connection_ack"}),
        json.dumps(
            {
                "type": "next",
                "payload": {"data": {"onDota2ScoreboardFeedData": {"id": "x", "data": "env"}}},
            }
        ),
    )
    _patch_oddin_probe(monkeypatch, frames, _oddin_tick(map_order=1))
    assert asyncio.run(probe_oddin_delay("od:match:1", 2)) is None
    _patch_oddin_probe(monkeypatch, frames, _oddin_tick(data_status="NO_DATA"))
    assert asyncio.run(probe_oddin_delay("od:match:1", 2)) is None
