"""FAK dump of leftover below the CLOB resting minimum."""

# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polymaker.domain import MarketMeta, Side
from trader_session_fixtures import YES_TOKEN, build_attached_worker

from trader.dust_sweep import (
    DUST_SWEEP_MAX_FAILURES,
    DustSweepTracker,
    DustToken,
    dust_to_sweep,
    read_fak,
    sweep_dust,
)

YES = "yes-token"
NO = "no-token"


def _token(
    token_id: str = YES,
    *,
    qty: float = 3.18,
    bid_size: float = 10.0,
    blocked: bool = False,
) -> DustToken:
    return DustToken(token_id=token_id, qty=qty, bid_size=bid_size, blocked=blocked)


def _not_frozen(_token_id: str) -> bool:
    return False


def _noop_freeze(_token_id: str, _seconds: float) -> None:
    return None


def _always_frozen(_token_id: str) -> bool:
    return True


def _stub_sell_freeze(store: Any, *, frozen: bool = False) -> None:
    store.is_sell_frozen = _always_frozen if frozen else _not_frozen
    store.freeze_sell = _noop_freeze


def test_dust_to_sweep_skips_blocked() -> None:
    assert dust_to_sweep(tokens=(_token(blocked=True),), min_order_size=5.0) == ()


def test_dust_to_sweep_caps_at_best_bid() -> None:
    targets = dust_to_sweep(tokens=(_token(qty=3.18, bid_size=0.5),), min_order_size=5.0)
    assert len(targets) == 1
    assert targets[0].token_id == YES
    assert targets[0].qty == 0.5


def test_dust_to_sweep_skips_sellable_and_empty() -> None:
    targets = dust_to_sweep(tokens=(_token(qty=5.0), _token(NO, qty=0.004)), min_order_size=5.0)
    assert targets == ()


def test_read_fak() -> None:
    matched = read_fak({"status": "matched", "makingAmount": "1"})
    assert matched.filled is True
    assert matched.balance_reject is False
    missed = read_fak({"status": "failed", "error": "no match"})
    assert missed.filled is False
    rejected = read_fak({"status": "failed", "error": "not enough balance / allowance"})
    assert rejected.filled is False
    assert rejected.balance_reject is True


def test_sweep_dust_sends_then_skips_cooldown() -> None:
    calls: list[tuple[str, float]] = []

    async def send_sell(token_id: str, qty: float) -> dict[str, Any]:
        calls.append((token_id, qty))
        return {"status": "matched"}

    frozen: list[str] = []
    tracker = DustSweepTracker()
    token = _token(qty=2.0)

    async def run() -> None:
        first = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=10.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=frozen.append,
            force=False,
        )
        second = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=20.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=frozen.append,
            force=False,
        )
        assert first == dust_to_sweep(tokens=(token,), min_order_size=5.0)
        assert second == ()

    asyncio.run(run())
    assert calls == [(YES, 2.0)]
    assert frozen == []


def test_sweep_dust_retries_after_backoff_and_freezes_balance_reject() -> None:
    calls: list[tuple[str, float]] = []
    frozen: list[str] = []

    async def send_sell(token_id: str, qty: float) -> dict[str, Any]:
        calls.append((token_id, qty))
        return {"status": "failed", "error": "not enough balance / allowance"}

    tracker = DustSweepTracker()
    token = _token(qty=1.5)

    async def run() -> None:
        first = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=0.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=frozen.append,
            force=False,
        )
        skipped = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=10.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=frozen.append,
            force=False,
        )
        later = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=30.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=frozen.append,
            force=False,
        )
        assert len(first) == 1
        assert skipped == ()
        assert len(later) == 1

    asyncio.run(run())
    assert calls == [(YES, 1.5), (YES, 1.5)]
    assert frozen == [YES, YES]


def test_sweep_dust_stops_after_max_failures() -> None:
    calls: list[tuple[str, float]] = []

    async def send_sell(token_id: str, qty: float) -> dict[str, Any]:
        calls.append((token_id, qty))
        return {"status": "failed", "error": "no match"}

    tracker = DustSweepTracker()
    token = _token(qty=1.0)

    async def run() -> None:
        for step in range(DUST_SWEEP_MAX_FAILURES):
            await sweep_dust(
                tokens=(token,),
                min_order_size=5.0,
                now_s=float(step * 400),
                tracker=tracker,
                send_sell=send_sell,
                freeze_sell=lambda _token_id: None,
                force=False,
            )
        halted = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=10_000.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=lambda _token_id: None,
            force=False,
        )
        forced = await sweep_dust(
            tokens=(token,),
            min_order_size=5.0,
            now_s=10_001.0,
            tracker=tracker,
            send_sell=send_sell,
            freeze_sell=lambda _token_id: None,
            force=True,
        )
        assert halted == ()
        assert len(forced) == 1

    asyncio.run(run())
    assert len(calls) == DUST_SWEEP_MAX_FAILURES + 1


def test_paper_worker_skips_dust_fak(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    worker, fake = build_attached_worker(tmp_path, request)
    calls: list[float] = []

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del token_id, side, meta, fak
        calls.append(amount)
        return {"status": "matched"}

    fake.gateway.market_order = market_order
    fake.state.set_position(YES_TOKEN, 2.0, 0.4)
    asyncio.run(worker._dust.sweep(force=True))
    assert calls == []


def test_live_worker_faks_dust_on_force(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    worker, fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    worker._dust._mode = "live"
    _stub_sell_freeze(fake.state)
    calls: list[tuple[str, float]] = []

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del meta
        calls.append((token_id, amount))
        assert side is Side.SELL
        assert fak is True
        return {"status": "matched"}

    fake.gateway.market_order = market_order
    fake.state.set_position(YES_TOKEN, 2.0, 0.4)
    asyncio.run(worker._dust.sweep(force=True))
    assert calls == [(YES_TOKEN, 2.0)]


def test_live_fak_binds_venue_id_so_late_fill_is_known(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    worker, fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    worker._dust._mode = "live"
    _stub_sell_freeze(fake.state)

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del token_id, side, meta, fak
        return {"status": "delayed", "orderID": "0xdustorder"}

    fake.gateway.market_order = market_order
    fake.state.set_position(YES_TOKEN, 2.0, 0.4)
    asyncio.run(worker._dust.sweep(force=True))

    core = worker._core
    assert core is not None
    core.drain_apply()
    record = next((r for r in core.state.records if r.order_id == "0xdustorder"), None)
    assert record is not None
    assert record.side == "SELL"
    assert record.submitted_qty == 2.0

    core.note_fill(
        fill_key="dust-fill",
        venue_id="0xdustorder",
        qty=2.0,
        price=0.4,
        now_ns=1,
        token_index=0,
        side="SELL",
    )
    core.drain_apply()
    assert core.state.pending_ownership == ()
    assert core.state.inventory[0].qty == 0.0


def test_live_fak_without_order_id_still_sends(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    worker, fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    worker._dust._mode = "live"
    _stub_sell_freeze(fake.state)
    calls: list[float] = []

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del token_id, side, meta, fak
        calls.append(amount)
        return {"status": "failed", "error": "no match"}

    fake.gateway.market_order = market_order
    fake.state.set_position(YES_TOKEN, 2.0, 0.4)
    asyncio.run(worker._dust.sweep(force=True))
    assert calls == [2.0]


def test_live_force_skips_frozen_token(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    worker, fake = build_attached_worker(tmp_path, request)
    worker._mode = "live"
    worker._dust._mode = "live"
    _stub_sell_freeze(fake.state, frozen=True)
    calls: list[float] = []

    async def market_order(
        token_id: str,
        side: Side,
        amount: float,
        meta: MarketMeta,
        *,
        fak: bool = True,
    ) -> dict[str, str]:
        del token_id, side, meta, fak
        calls.append(amount)
        return {"status": "matched"}

    fake.gateway.market_order = market_order
    fake.state.set_position(YES_TOKEN, 2.0, 0.4)
    asyncio.run(worker._dust.sweep(force=True))
    assert calls == []
