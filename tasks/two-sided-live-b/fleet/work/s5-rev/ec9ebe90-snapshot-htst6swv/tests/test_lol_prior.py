"""LoL live prior uses the same pre-horn book window as training."""

from pathlib import Path

import pytest
from trader_session_fixtures import HORN_UNIX_SECONDS, build_attached_worker, build_event

from trader.live_feed import MatchPhase
from trader.lol_prior import LolPriorTape


def test_tape_prior_is_the_last_pair_strictly_before_horn() -> None:
    """[horn-61s, horn) keeps the last two-sided mid and drops the print at horn."""
    tape = LolPriorTape()
    tape.retain({"c": ("yes", "no")})
    horn = 1_700_000_000
    tape.note("yes", horn - 62, 0.20, 0.20)
    tape.note("no", horn - 62, 0.80, 0.80)
    tape.note("yes", horn - 1, 0.39, 0.41)
    tape.note("no", horn - 1, 0.59, 0.61)
    tape.note("yes", horn, 0.90, 0.90)
    tape.note("no", horn, 0.10, 0.10)
    assert tape.prior("yes", "no", horn, yes_is_radiant=True) == pytest.approx(0.40)

    stale = LolPriorTape()
    stale.retain({"c": ("yes", "no")})
    stale.note("yes", horn - 62, 0.20, 0.20)
    stale.note("no", horn - 62, 0.80, 0.80)
    assert stale.prior("yes", "no", horn, yes_is_radiant=True) is None


def _refuse_prices_history(*_args: object, **_kwargs: object) -> float:
    raise AssertionError("prices-history")


def test_lol_worker_freezes_book_prior_and_skips_prices_history(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After horn, LoL latches the tape and never calls prices-history."""
    monkeypatch.setattr("trader.match_worker.fetch_market_prior", _refuse_prices_history)
    worker, _fake = build_attached_worker(
        tmp_path, request, game="lol", prior=None, prior_ready=False
    )
    event = build_event(0, phase=MatchPhase.PRE_HORN)
    horn = event.horn_unix_seconds
    tape = worker._host.lol_prior
    tape.retain({worker._cid: (worker._yes, worker._no)})
    tape.note(worker._yes, horn - 10, 0.39, 0.41)
    tape.note(worker._no, horn - 10, 0.59, 0.61)
    tape.note(worker._yes, horn, 0.10, 0.12)
    tape.note(worker._no, horn, 0.88, 0.90)

    monkeypatch.setattr("trader.match_worker.time.time", lambda: horn - 1)
    worker._maybe_start_prior(event)
    assert worker._prior is None
    assert worker._lol_prior_done is False

    monkeypatch.setattr("trader.match_worker.time.time", lambda: float(horn + 3))
    worker._maybe_start_prior(event)
    assert worker._prior == pytest.approx(0.40)
    assert worker._lol_prior_done is True
    assert horn == HORN_UNIX_SECONDS


def test_lol_worker_stays_empty_when_the_window_has_no_book(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A miss stays empty. There is no horn-90 prices-history fallback."""
    monkeypatch.setattr("trader.match_worker.fetch_market_prior", _refuse_prices_history)
    worker, _fake = build_attached_worker(
        tmp_path, request, game="lol", prior=None, prior_ready=False
    )
    event = build_event(80)
    monkeypatch.setattr("trader.match_worker.time.time", lambda: float(event.horn_unix_seconds + 5))
    worker._maybe_start_prior(event)
    assert worker._prior is None
    assert worker._lol_prior_done is True
