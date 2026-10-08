"""Regressions for mandatory scoreboard settings, death ages, and signal provenance."""

# pyright: reportPrivateUsage=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import cast

import pytest
from trader_session_fixtures import (
    NO_TOKEN,
    YES_TOKEN,
    FakeLiveFeed,
    build_attached_worker,
    build_event,
    read_session_records,
)

from backtest.run import build_parser
from shared.utils.board_features import BoardFeatures, BoardHistory
from shared.utils.dota_features import SnapshotHistory
from shared.utils.match_time import NS_PER_SECOND as NS
from shared.utils.match_time import parse_utc
from trader import match_worker, session_journal
from trader.bindings import ModelReference
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, KillTick, MatchPhase, SideWait
from trader.model_server import ModelPrediction, ModelPredictionError
from trader.session_types import SignalReason


@pytest.mark.parametrize(
    "flags",
    [
        ["--board-ticks"],
        ["--board-reaction-seconds", "0.11"],
        ["--kill-gate-directional"],
    ],
)
def test_backtest_has_no_board_or_directional_mode_switch(
    flags: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--validation", "--name", "current", *flags])
    assert "unrecognized arguments" in capsys.readouterr().err


def test_board_history_keeps_death_age_after_table_catches_up() -> None:
    board = BoardHistory()
    board.record_table(90 * NS, 0, 0)
    board.record_board(100 * NS, 1, 0)
    pending = board.derive(102 * NS, 0, 0)
    assert pending.pending_deaths_radiant == 1
    assert pending.board_death_age_radiant_s == 2
    board.record_table(108 * NS, 1, 0)
    caught_up = board.derive(120 * NS, 1, 0)
    assert caught_up.pending_deaths_radiant == 0
    assert caught_up.board_death_age_radiant_s == 20
    board.record_table(210 * NS, 2, 0)
    assert board.derive(210 * NS, 2, 0).board_death_age_radiant_s == 8


class ScriptedModel:
    """predict_fair replaying scripted predictions and errors in call order."""

    def __init__(self, steps: list[ModelPrediction | ModelPredictionError]) -> None:
        self.model_reference = ModelReference(name="scripted-model", trained_at="t")
        self._steps = list(steps)
        self.calls = 0

    def predict_fair(
        self,
        snapshot: GameSnapshot,
        market_p_radiant: float,
        market_radiant_prior: float,
        history: SnapshotHistory,
        board: BoardFeatures,
    ) -> ModelPrediction:
        """Count every attempt; raise scripted errors instead of returning."""
        del snapshot, market_p_radiant, market_radiant_prior, history, board
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, ModelPredictionError):
            raise step
        return step


def _journal_stamps(monkeypatch: pytest.MonkeyPatch, base: datetime) -> list[datetime]:
    """Pin session_journal.datetime to a clock returning base + N seconds per call."""
    stamps: list[datetime] = []

    class ScriptedUtc:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            del tz
            stamp = base + timedelta(seconds=len(stamps) + 1)
            stamps.append(stamp)
            return stamp

    monkeypatch.setattr(session_journal, "datetime", ScriptedUtc)
    return stamps


def _iso_z(stamp: datetime) -> str:
    """The session.jsonl UTC stamp format for one scripted clock tick."""
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ready_books(worker: match_worker.MatchWorker) -> None:
    """Mark both token books ready for this attach generation."""
    readiness = worker._host.readiness
    readiness.attach_token(YES_TOKEN)
    readiness.attach_token(NO_TOKEN)
    readiness.note_snapshot(YES_TOKEN)
    readiness.note_snapshot(NO_TOKEN)


def _signal_records(tmp_path: Path) -> list[dict[str, object]]:
    """Session journal signal rows for this test's worker."""
    return [
        record
        for record in read_session_records(tmp_path / "journal")
        if record["kind"] == "signal"
    ]


def test_board_requote_keeps_feed_provenance_and_journals_each_delta(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Table and board rows share source/receipt/snapshot; each carries its own result."""
    model = ScriptedModel(
        [
            ModelPrediction(raw_delta=0.031, fair=0.55),
            ModelPrediction(raw_delta=-0.012, fair=0.48),
        ]
    )
    worker, _fake = build_attached_worker(tmp_path, request, model=model, feed_timeout_seconds=16.0)
    _ready_books(worker)
    now = [time.time_ns()]
    monkeypatch.setattr(match_worker, "core_now_ns", lambda: now[0])
    stamps = _journal_stamps(monkeypatch, datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC))
    event = build_event(100, radiant_nw_adv=100)

    async def scenario() -> None:
        worker._record_history(event.snapshot)
        await worker._on_event(event)
        assert model.calls == 1
        source = worker._board_source
        assert source is not None
        source_ns = source.received_ns
        generation = worker._watchdog._generation
        worker._on_kill_tick(KillTick(event.received_at_utc, SideWait(2, 10.0), SideWait(3, 0.0)))
        now[0] += 110_000_000
        worker._quote_board()
        assert model.calls == 2
        assert worker._watchdog._generation == generation
        assert worker._board_source is not None
        assert worker._board_source.received_ns == source_ns
        worker._stop_feed_timers()

    asyncio.run(scenario())
    signals = _signal_records(tmp_path)
    assert len(signals) == 2
    table, board = signals
    assert table["recorded_at_utc"] != board["recorded_at_utc"]
    assert [row["recorded_at_utc"] for row in signals] == [_iso_z(stamp) for stamp in stamps]
    for row in (table, board):
        assert row["feed_source"] == "grid"
        assert row["feed_received_at_utc"] == event.received_at_utc
        assert row["game_snapshot"] == table["game_snapshot"]
        assert row["model_evaluated"] is True
        assert parse_utc(str(row["recorded_at_utc"])) > parse_utc(event.received_at_utc)
    assert table["raw_delta"] == pytest.approx(0.031)
    assert board["raw_delta"] == pytest.approx(-0.012)


def test_board_requote_without_source_or_after_age_writes_no_signal(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No board row without a source, on an aged source, or after a history gap."""
    model = ScriptedModel([ModelPrediction(raw_delta=0.031, fair=0.55)])
    worker, _fake = build_attached_worker(tmp_path, request, model=model, feed_timeout_seconds=16.0)
    _ready_books(worker)
    now = [time.time_ns()]
    monkeypatch.setattr(match_worker, "core_now_ns", lambda: now[0])

    async def scenario() -> None:
        worker._quote_board()
        event = build_event(100)
        worker._record_history(event.snapshot)
        await worker._on_event(event)
        assert model.calls == 1
        now[0] += 20_000_000_000
        worker._quote_board()
        assert model.calls == 1
        gap = build_event(500)
        worker._record_history(gap.snapshot)
        await worker._on_event(gap)
        assert worker._board_source is None
        worker._quote_board()
        worker._stop_feed_timers()

    asyncio.run(scenario())
    assert model.calls == 1
    kinds = [record["kind"] for record in read_session_records(tmp_path / "journal")]
    assert kinds == ["signal", "history_gap"]
    signals = _signal_records(tmp_path)
    assert len(signals) == 1
    assert signals[0]["second"] == 100


def test_skip_error_journal_never_reuses_the_cached_delta(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """success -> paused -> success -> error: each row carries its own call's delta."""
    model = ScriptedModel(
        [
            ModelPrediction(raw_delta=0.031, fair=0.55),
            ModelPrediction(raw_delta=-0.012, fair=0.48),
            ModelPredictionError("scripted model error"),
        ]
    )
    worker, _fake = build_attached_worker(tmp_path, request, model=model)
    _ready_books(worker)

    async def scenario() -> None:
        first = build_event(100)
        worker._record_history(first.snapshot)
        await worker._on_event(first)
        assert model.calls == 1
        assert worker._last_raw_delta == pytest.approx(0.031)
        paused = build_event(101, paused=True)
        worker._record_history(paused.snapshot)
        await worker._on_event(paused)
        assert model.calls == 1
        assert worker._last_raw_delta == pytest.approx(0.031)
        second = build_event(102)
        worker._record_history(second.snapshot)
        await worker._on_event(second)
        assert model.calls == 2
        third = build_event(103)
        worker._record_history(third.snapshot)
        await worker._on_event(third)
        assert model.calls == 3

    asyncio.run(scenario())
    signals = _signal_records(tmp_path)
    assert [row["reason"] for row in signals] == ["model", "paused", "model", "model_error"]
    assert signals[0]["model_evaluated"] is True
    assert signals[0]["raw_delta"] == pytest.approx(0.031)
    assert signals[1]["model_evaluated"] is False
    assert signals[1]["raw_delta"] is None
    assert signals[2]["model_evaluated"] is True
    assert signals[2]["raw_delta"] == pytest.approx(-0.012)
    assert signals[3]["model_evaluated"] is False
    assert signals[3]["raw_delta"] is None
    assert worker._last_raw_delta == pytest.approx(-0.012)


@dataclass(frozen=True)
class _GateCase:
    """One model gate: the feed event, expected reason, and worker setup flags."""

    event: FeedEvent
    reason: SignalReason
    books_ready: bool
    prior_available: bool
    stale: bool


_GATE_CASES = [
    _GateCase(
        event=build_event(100),
        reason=SignalReason.STALE,
        books_ready=True,
        prior_available=True,
        stale=True,
    ),
    _GateCase(
        event=build_event(100),
        reason=SignalReason.MISSING_BOOK,
        books_ready=False,
        prior_available=True,
        stale=False,
    ),
    _GateCase(
        event=build_event(100),
        reason=SignalReason.MISSING_PRIOR,
        books_ready=True,
        prior_available=False,
        stale=False,
    ),
    _GateCase(
        event=build_event(100, phase=MatchPhase.PRE_MATCH),
        reason=SignalReason.PRE_HORN,
        books_ready=True,
        prior_available=True,
        stale=False,
    ),
    _GateCase(
        event=build_event(-90, phase=MatchPhase.PRE_HORN),
        reason=SignalReason.PRE_HORN,
        books_ready=True,
        prior_available=True,
        stale=False,
    ),
    _GateCase(
        event=build_event(100, paused=True),
        reason=SignalReason.PAUSED,
        books_ready=True,
        prior_available=True,
        stale=False,
    ),
    _GateCase(
        event=build_event(200, phase=MatchPhase.FINISHED),
        reason=SignalReason.FINISHED,
        books_ready=True,
        prior_available=True,
        stale=False,
    ),
]


def _no_prior(
    yes_token_id: str, no_token_id: str, anchor_ts: int, *, yes_is_radiant: bool
) -> float | None:
    """A prices-history fetch that found nothing."""
    del yes_token_id, no_token_id, yes_is_radiant, anchor_ts
    return None


@pytest.mark.parametrize(
    "case",
    _GATE_CASES,
    ids=lambda case: f"{case.reason}-{case.event.snapshot.second}",
)
def test_gated_decisions_journal_model_not_evaluated(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    case: _GateCase,
) -> None:
    """Every gate writes one signal with model_evaluated=false and a null delta."""
    model = ScriptedModel([ModelPrediction(raw_delta=0.031, fair=0.55)])
    worker, _fake = build_attached_worker(
        tmp_path, request, model=model, prior=0.5 if case.prior_available else None
    )
    if case.books_ready:
        _ready_books(worker)
    if not case.prior_available:
        monkeypatch.setattr(match_worker, "fetch_market_prior", _no_prior)
    if case.stale:
        worker._watchdog._expired = True

    async def scenario() -> None:
        worker._record_history(case.event.snapshot)
        await worker._on_event(case.event)
        if worker._prior_task is not None:
            await worker._prior_task

    asyncio.run(scenario())
    assert model.calls == 0
    signals = _signal_records(tmp_path)
    assert len(signals) == 1
    row = signals[0]
    assert row["reason"] == case.reason
    assert row["model_evaluated"] is False
    assert row["raw_delta"] is None
    assert row["feed_source"] == "grid"
    assert row["feed_received_at_utc"] == case.event.received_at_utc
    snapshot = row["game_snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["second"] == case.event.snapshot.second


def test_model_success_past_cutoff_keeps_evaluated_and_raw_delta(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A BUY-blocked success is still a successful evaluation with a real delta."""
    model = ScriptedModel([ModelPrediction(raw_delta=-0.012, fair=0.48)])
    worker, _fake = build_attached_worker(tmp_path, request, model=model)
    _ready_books(worker)

    async def scenario() -> None:
        event = build_event(500)
        worker._record_history(event.snapshot)
        await worker._on_event(event)

    asyncio.run(scenario())
    assert model.calls == 1
    signals = _signal_records(tmp_path)
    assert len(signals) == 1
    row = signals[0]
    assert row["reason"] == "model"
    assert row["model_evaluated"] is True
    assert row["raw_delta"] == pytest.approx(-0.012)
    assert row["entry_block"] == "cutoff"


def test_raw_delta_stays_radiant_oriented_and_unclipped(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """yes_is_radiant=false keeps the model-side delta; clipping is not reversed."""
    model = ScriptedModel([ModelPrediction(raw_delta=0.75, fair=0.99)])
    worker, _fake = build_attached_worker(tmp_path, request, model=model)
    worker._yes_is_radiant = False
    _ready_books(worker)

    async def scenario() -> None:
        event = build_event(100)
        worker._record_history(event.snapshot)
        await worker._on_event(event)

    asyncio.run(scenario())
    signals = _signal_records(tmp_path)
    assert len(signals) == 1
    row = signals[0]
    radiant_fair = cast(float, row["radiant_fair"])
    market_p = cast(float, row["market_p_radiant"])
    assert row["model_evaluated"] is True
    assert row["raw_delta"] == pytest.approx(0.75)
    assert radiant_fair == pytest.approx(0.99)
    assert market_p == pytest.approx(0.485 / 0.99)
    assert row["yes_fair"] == pytest.approx(0.01)
    assert row["raw_delta"] != pytest.approx(radiant_fair - market_p)


@pytest.mark.parametrize(
    ("game", "feed_source", "event_source"),
    [
        ("dota", FeedSource.GRID, FeedSource.ODDIN),
        ("dota", FeedSource.ODDIN, FeedSource.GRID),
        ("lol", FeedSource.GRID, FeedSource.ODDIN),
    ],
)
def test_signal_provenance_contract_is_source_neutral(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    game: str,
    feed_source: FeedSource,
    event_source: FeedSource,
) -> None:
    """The journaled source is the event's, never the feed-catalog source."""
    model = ScriptedModel([ModelPrediction(raw_delta=0.0, fair=0.5)])
    feed = FakeLiveFeed()
    feed.source = feed_source
    worker, _fake = build_attached_worker(tmp_path, request, model=model, feed=feed, game=game)
    _ready_books(worker)
    event = replace(build_event(100), source=event_source)

    async def scenario() -> None:
        worker._record_history(event.snapshot)
        await worker._on_event(event)

    asyncio.run(scenario())
    assert model.calls == 1
    signals = _signal_records(tmp_path)
    assert len(signals) == 1
    row = signals[0]
    assert row["feed_source"] == event_source.value
    assert row["feed_source"] != feed_source.value
    assert row["feed_received_at_utc"] == event.received_at_utc
    assert row["model_evaluated"] is True
    assert row["raw_delta"] == 0.0
    snapshot = row["game_snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["second"] == 100
