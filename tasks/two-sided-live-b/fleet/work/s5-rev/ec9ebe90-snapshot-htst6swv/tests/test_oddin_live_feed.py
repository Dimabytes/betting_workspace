"""Oddin live-feed reconnects: a tick resets the fault counter, an empty complete spends one."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

import pytest

from shared.utils.top_players import ZERO_TOP
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, MatchPhase
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_client import iter_scoreboard_payloads
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.oddin_live_feed import OddinLiveFeed, append_series_closed
from trader.oddin_types import ScoreboardSilent, WidgetConfig
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME


class _Boom:
    """Marker placed in a scripted socket round. The fake socket raises instead of yielding it."""


class _Silent:
    """Marker: this socket round raises ScoreboardSilent instead of yielding."""


def _event(finished: bool) -> FeedEvent:
    phase = MatchPhase.FINISHED if finished else MatchPhase.IN_PROGRESS
    snapshot = GameSnapshot(10, 10, phase, 0, 100, 100, 0, 0, 0, ZERO_TOP, False)
    return FeedEvent(snapshot, "2026-09-26T12:00:00Z", FeedSource.ODDIN, 0, True)


def _apply(
    self: OddinSnapshotReducer, payload: Mapping[str, object], received_at: datetime
) -> FeedEvent | None:
    del self, received_at
    kind = payload.get("kind")
    if kind == "tick":
        return _event(False)
    if kind == "done":
        return _event(True)
    return None


def _open_envelope(envelope: str, key: bytes) -> dict[str, object]:
    del envelope, key
    return {}


def _widget(match_id: str) -> WidgetConfig:
    del match_id
    return WidgetConfig("t", "id")


def _key() -> bytes:
    return b"k"


def _keep(_oddin_match_id: str, _map_number: int) -> bool:
    return False


def _ended(_oddin_match_id: str, _map_number: int) -> bool:
    return True


async def _collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rounds: Sequence[Sequence[dict[str, object] | _Boom | _Silent]],
    map_ended: Callable[[str, int], bool],
) -> list[FeedEvent]:
    step = {"i": 0}

    async def fake_iter(
        config: WidgetConfig, key: bytes, stale_seconds: float
    ) -> AsyncIterator[dict[str, object]]:
        del config, key, stale_seconds
        if step["i"] >= len(rounds):
            raise RuntimeError("feed did not stop")
        batch = rounds[step["i"]]
        step["i"] += 1
        for item in batch:
            if isinstance(item, _Boom):
                raise TimeoutError("socket")
            if isinstance(item, _Silent):
                raise ScoreboardSilent()
            yield item

    async def fake_seed(client: object, config: WidgetConfig) -> str:
        del client, config
        return "envelope"

    monkeypatch.setattr("trader.oddin_live_feed.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.oddin_live_feed.RECONNECT_SECONDS", 0)
    monkeypatch.setattr("trader.oddin_live_feed.load_feed_key", _key)
    monkeypatch.setattr("trader.oddin_live_feed.fetch_snapshot_envelope", fake_seed)
    monkeypatch.setattr("trader.oddin_live_feed.open_envelope", _open_envelope)
    monkeypatch.setattr("trader.oddin_live_feed.iter_scoreboard_payloads", fake_iter)
    monkeypatch.setattr("trader.oddin_live_feed.widget_config", _widget)
    monkeypatch.setattr(OddinSnapshotReducer, "apply_payload", _apply)
    feed = OddinLiveFeed("od:match:3232752", 1, "9001", True, map_ended)
    events: list[FeedEvent] = []
    async for event in feed.ticks():
        events.append(event)
    return events


def test_socket_break_then_finished_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[list[dict[str, object] | _Boom]] = [[_Boom()], [{"kind": "done"}]]
    events = asyncio.run(_collect(tmp_path, monkeypatch, rounds, _keep))
    assert len(events) == 1
    assert events[0].snapshot.finished


def test_ticked_map_outlasts_five_faults_until_the_catalog_ends_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rounds: list[list[dict[str, object] | _Boom | _Silent]] = [
        [{"kind": "tick"}, _Boom()],
        *[[_Boom()] for _ in range(6)],
    ]
    checks = {"n": 0}

    def ended_on_seventh_check(_oddin_match_id: str, map_number: int) -> bool:
        assert map_number == 1
        checks["n"] += 1
        return checks["n"] == 7

    events = asyncio.run(_collect(tmp_path, monkeypatch, rounds, ended_on_seventh_check))
    assert len(events) == 2
    assert checks["n"] == 7
    text = (tmp_path / "9001" / ODDIN_STATE_ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert '"event": "closed"' in text or '"event":"closed"' in text


def test_five_empty_completes_stop_the_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty: list[list[dict[str, object] | _Boom | _Silent]] = [[] for _ in range(5)]
    events = asyncio.run(_collect(tmp_path, monkeypatch, empty, _keep))
    assert events == []


def test_scoreboard_silence_does_not_stop_the_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rounds: list[list[dict[str, object] | _Boom | _Silent]] = [[_Silent()] for _ in range(6)]
    rounds.append([{"kind": "done"}])
    events = asyncio.run(_collect(tmp_path, monkeypatch, rounds, _keep))
    assert len(events) == 1
    assert events[0].snapshot.finished
    text = (tmp_path / "9001" / ODDIN_STATE_ARCHIVE_FILENAME).read_text(encoding="utf-8")
    assert '"event": "closed"' not in text and '"event":"closed"' not in text


def test_quiet_scoreboard_closes_when_the_series_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = asyncio.run(_collect_real(tmp_path, monkeypatch, _ended))
    assert len(events) == 1
    assert events[0].snapshot.finished
    _assert_replay_finished(tmp_path / "9001" / ODDIN_STATE_ARCHIVE_FILENAME)


def test_append_series_closed_makes_replay_terminal(tmp_path: Path) -> None:
    archive = tmp_path / "9001"
    archive.mkdir()
    append_series_closed(archive)
    _assert_replay_finished(archive / ODDIN_STATE_ARCHIVE_FILENAME)


def _assert_replay_finished(path: Path) -> None:
    events = list(
        replay_oddin_records(iter_oddin_archive_records(path), OddinSnapshotReducer(1, True))
    )
    assert events[-1].snapshot.finished


async def _collect_real(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    map_ended: Callable[[str, int], bool],
) -> list[FeedEvent]:
    async def fake_iter(
        config: WidgetConfig, key: bytes, stale_seconds: float
    ) -> AsyncIterator[dict[str, object]]:
        del config, key, stale_seconds
        raise ScoreboardSilent()
        yield {}

    async def fake_seed(client: object, config: WidgetConfig) -> str:
        del client, config
        return "envelope"

    monkeypatch.setattr("trader.oddin_live_feed.TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.oddin_live_feed.RECONNECT_SECONDS", 0)
    monkeypatch.setattr("trader.oddin_live_feed.load_feed_key", _key)
    monkeypatch.setattr("trader.oddin_live_feed.fetch_snapshot_envelope", fake_seed)
    monkeypatch.setattr("trader.oddin_live_feed.open_envelope", _open_envelope)
    monkeypatch.setattr("trader.oddin_live_feed.iter_scoreboard_payloads", fake_iter)
    monkeypatch.setattr("trader.oddin_live_feed.widget_config", _widget)
    feed = OddinLiveFeed("od:match:3232752", 1, "9001", True, map_ended)
    events: list[FeedEvent] = []
    async for event in feed.ticks():
        events.append(event)
    return events


def test_pings_do_not_keep_a_quiet_scoreboard(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = [json.dumps({"type": "connection_ack"})] + [json.dumps({"type": "ping"})] * 20

    class _Socket:
        async def __aenter__(self) -> "_Socket":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def send(self, _message: str) -> None:
            await asyncio.sleep(0.03)

        async def recv(self) -> str:
            if not frames:
                await asyncio.sleep(30)
                return ""
            return frames.pop(0)

    def connect(*_args: object, **_kwargs: object) -> _Socket:
        return _Socket()

    monkeypatch.setattr("trader.oddin_client.websockets.connect", connect)

    async def drive() -> None:
        stream = iter_scoreboard_payloads(WidgetConfig("brand", "mid"), b"k", 0.05)
        with pytest.raises(ScoreboardSilent):
            async for _payload in stream:
                pass

    asyncio.run(asyncio.wait_for(drive(), timeout=2))
