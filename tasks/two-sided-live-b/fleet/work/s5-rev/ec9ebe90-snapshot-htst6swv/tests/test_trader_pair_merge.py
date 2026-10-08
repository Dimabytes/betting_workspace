# pyright: reportPrivateUsage=false

import asyncio
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.config import Config
from polymaker.domain import Fill, Side
from trader_session_fixtures import AlertRecorder

from shared.constants.strategy import LIVE_MAX_POSITION_LEVELS
from strategy.policy import follow300_policy
from strategy.types import FreshnessLimits, MarketLimits, TokenInventory
from trader import pair_merge
from trader.core_persistence import CoreSessionKey
from trader.core_session_io import SessionIdentity, consume_core_outbox
from trader.ctf_merge import MergeOutcome
from trader.pair_merge import MergeAccountingError, PairMerger, pick_merge_pause_s
from trader.session_core import CollateralCache, LiveCore
from trader.trading_mode import ExecutionMode
from trader.wallet_store import WalletStateStore

CID = "0xcond"
MATCH = "8944931337"
YES = "yes-token"
NO = "no-token"
TOKENS = frozenset({YES, NO})
TX = "0xmerge"
IDENTITY = SessionIdentity(
    session_id=CID,
    key=CoreSessionKey(
        condition_id=CID, game="dota", yes_token=YES, no_token=NO, yes_is_radiant=True
    ),
)


class _FakeClobClient:
    def __init__(self) -> None:
        self.allowance_updates = 0

    def update_balance_allowance(self, params: object) -> None:
        del params
        self.allowance_updates += 1


class _FakeGateway:
    def __init__(self) -> None:
        self._client = _FakeClobClient()

    async def _io(self, fn: Callable[[object], None], arg: object) -> None:
        fn(arg)

    async def collateral_balance(self) -> float:
        return 205.0


class _FakeEngine:
    def __init__(self) -> None:
        self._chain_lock = asyncio.Lock()
        self.cfg = object()
        self.gateway = _FakeGateway()
        self.woken: list[str] = []

    def _wake_cid(self, condition_id: str) -> None:
        self.woken.append(condition_id)


class _FakeHost:
    def __init__(self, store: WalletStateStore, core: LiveCore) -> None:
        self.store = store
        self.core = core
        self.engine = _FakeEngine()
        self._budget_cache = CollateralCache()
        self.chain: dict[str, float] | None = {YES: 500.0, NO: 500.0}
        self.consume_error: Exception | None = None
        self.held_at_consume: bool | None = None

    async def read_fresh_balances(self, token_ids: list[str]) -> dict[str, float] | None:
        del token_ids
        return self.chain

    def _consume_core_outbox(self, token_id: str) -> None:
        del token_id
        self.held_at_consume = self.store.merge_is_held(YES) and self.store.merge_is_held(NO)
        if self.consume_error is not None:
            raise self.consume_error
        consume_core_outbox(store=self.store, core=self.core, identity=IDENTITY, tokens=TOKENS)


class _ScriptedMerge:
    def __init__(self, outcome: MergeOutcome) -> None:
        self.outcome = outcome
        self.amounts: list[int] = []
        self.held_during_call: list[bool] = []
        self.store: WalletStateStore | None = None

    def __call__(
        self, *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, deadline_s
        if self.store is not None:
            self.held_during_call.append(
                self.store.merge_is_held(YES) and self.store.merge_is_held(NO)
            )
        self.amounts.append(amount_raw)
        return self.outcome


def _buy(store: WalletStateStore, *, token_id: str, size: float, price: float, key: str) -> None:
    fill = Fill(token_id, Side.BUY, price, size, key, 1.0, is_maker=True)
    store.apply_confirmed_fill(fill, key)


def _open_host(tmp_path: Path, *, yes: float, no: float, price: float) -> _FakeHost:
    store = WalletStateStore(tmp_path / "w.db")
    _buy(store, token_id=YES, size=yes, price=price, key="t1:v1")
    _buy(store, token_id=NO, size=no, price=price, key="t2:v2")
    core = LiveCore(
        policy=follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=MarketLimits(
            min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_MAX_POSITION_LEVELS["dota"],
    )
    core.last_outbox_seq = store.core_outbox_after(after_seq=0, tokens=TOKENS)[-1].seq
    core.note_recovery(now_ns=1)
    core.drain_apply()
    core.note_recovery_verified(
        now_ns=2,
        generation=core.state.recovery_generation,
        inventory=(
            TokenInventory(token_index=0, qty=yes, cost_basis=yes * price, last_buy_ns=None),
            TokenInventory(token_index=1, qty=no, cost_basis=no * price, last_buy_ns=None),
        ),
    )
    core.drain_apply()
    return _FakeHost(store, core)


def _merger_for(host: _FakeHost, *, mode: ExecutionMode) -> PairMerger:
    merger = PairMerger(
        host=cast(Any, host), cid=CID, match_id=MATCH, yes_token=YES, no_token=NO, mode=mode
    )
    merger.attach_core(host.core)
    return merger


def _script(monkeypatch: pytest.MonkeyPatch, outcome: MergeOutcome) -> _ScriptedMerge:
    scripted = _ScriptedMerge(outcome)
    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", scripted)
    return scripted


def _record_alerts(monkeypatch: pytest.MonkeyPatch) -> AlertRecorder:
    alerts = AlertRecorder()
    monkeypatch.setattr(pair_merge, "notify_in_background", alerts)
    return alerts


def _released(host: _FakeHost) -> bool:
    return not host.store.merge_is_held(YES) and not host.store.merge_is_held(NO)


async def _schedule_and_wait(merger: PairMerger) -> None:
    merger.schedule()
    await merger.wait_inflight()


MERGED = MergeOutcome(status="merged", tx_hash=TX, reason="")


@pytest.mark.parametrize(
    ("yes", "no", "price", "calls"),
    [(150.0, 120.0, 0.50, 1), (150.0, 109.0, 0.50, 0), (258.0, 4.0, 0.50, 0)],
    ids=["held_135_pairs_120", "held_129_5", "pairs_4"],
)
def test_schedule_merges_at_130_usdc_held_and_5_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    yes: float,
    no: float,
    price: float,
    calls: int,
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=yes, no=no, price=price)
    scripted.store = host.store
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert len(scripted.amounts) == calls
    assert scripted.held_during_call == [True] * calls
    assert _released(host)


async def _block_in_flight(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._task = asyncio.get_running_loop().create_task(asyncio.sleep(0.05))


async def _block_unacked_matched(merger: PairMerger, host: _FakeHost) -> None:
    del merger
    late = Fill(YES, Side.BUY, 0.50, 10.0, "t3:v3", 2.0, is_maker=True)
    host.store.apply_matched_fill(late, "t3:v3")


async def _block_recovery(merger: PairMerger, host: _FakeHost) -> None:
    del merger
    host.core.note_recovery(now_ns=3)
    host.core.drain_apply()


async def _block_pause(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._paused_until_s = time.monotonic() + 30.0


async def _block_disabled(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger._disabled = True


async def _block_quiesce(merger: PairMerger, host: _FakeHost) -> None:
    del host
    merger.mark_quiesced()


@pytest.mark.parametrize(
    "block",
    [
        _block_in_flight,
        _block_unacked_matched,
        _block_recovery,
        _block_pause,
        _block_disabled,
        _block_quiesce,
    ],
    ids=["in_flight", "unacked_matched", "recovery", "pause", "disabled", "quiesce"],
)
def test_schedule_skips_each_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    block: Callable[[PairMerger, _FakeHost], Awaitable[None]],
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await block(merger, host)
        await _schedule_and_wait(merger)

    asyncio.run(run())
    assert scripted.amounts == []
    assert _released(host)


def test_paper_mode_never_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="paper")))
    assert scripted.amounts == []
    assert _released(host)


@pytest.mark.parametrize(
    ("chain", "amounts"),
    [
        (None, []),
        ({YES: 0.0, NO: 500.0}, []),
        ({YES: 200.0, NO: 100.1234567}, [100_123_456]),
    ],
    ids=["stale_chain", "zero_on_chain", "chain_below_store"],
)
def test_merge_amount_is_clamped_to_fresh_chain_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chain: dict[str, float] | None,
    amounts: list[int],
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    host.chain = chain
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert scripted.amounts == amounts
    assert scripted.held_during_call == [True] * len(amounts)
    assert _released(host)


def test_successful_merge_moves_store_core_cash_and_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    cash_before = host.store.running_net_cash
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert scripted.amounts == [120_000_000]
    assert scripted.held_during_call == [True]
    assert host.held_at_consume is True
    assert _released(host)
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert host.store.position(NO).size == 0.0
    assert host.store.running_net_cash == pytest.approx(cash_before + 120.0)
    yes_inventory, no_inventory = host.core.state.inventory
    assert yes_inventory.qty == pytest.approx(30.0)
    assert no_inventory.qty == 0.0
    assert (
        host.core.last_outbox_seq
        == host.store.core_outbox_after(after_seq=0, tokens=TOKENS)[-1].seq
    )
    assert host.core.state.recovery_pending is False
    assert host.engine.woken == [CID]
    assert host.engine.gateway._client.allowance_updates == 1
    assert host._budget_cache.value == 205.0
    assert alerts.messages == [f"trader merge: match {MATCH} pairs 120.00 tx {TX}"]


def test_core_consume_error_keeps_the_merge_booked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    host.consume_error = RuntimeError("core snapshot write failed")
    merger = _merger_for(host, mode="live")
    asyncio.run(_schedule_and_wait(merger))
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert host.held_at_consume is True
    assert _released(host)
    assert merger._disabled is False
    assert merger._failures == 0
    assert host.engine.woken == [CID]
    assert len(alerts.messages) == 1


def test_failed_merge_alerts_pauses_and_success_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(
        monkeypatch, MergeOutcome(status="failed", tx_hash="", reason="post: relayer 429: quota")
    )
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        assert merger._failures == 1
        assert _released(host)
        assert merger._paused_until_s - time.monotonic() == pytest.approx(30.0, abs=1.0)
        assert alerts.messages == [
            f"trader merge failed: match {MATCH} pause 30s tx -: post: relayer 429: quota"
        ]
        await _schedule_and_wait(merger)
        assert len(scripted.amounts) == 1
        assert host.store.position(YES).size == 150.0
        merger._paused_until_s = 0.0
        scripted.outcome = MERGED
        await _schedule_and_wait(merger)
        assert merger._failures == 0
        assert _released(host)

    asyncio.run(run())
    assert scripted.held_during_call == [True, True]
    assert [pick_merge_pause_s(failures) for failures in range(1, 7)] == [
        30.0,
        60.0,
        120.0,
        300.0,
        300.0,
        300.0,
    ]


def test_unknown_merge_turns_off_merges_for_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(
        monkeypatch,
        MergeOutcome(status="unknown", tx_hash="0xdead", reason="receipt: TimeExhausted"),
    )
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        await _schedule_and_wait(merger)
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == [120_000_000]
    assert scripted.held_during_call == [True]
    assert _released(host)
    assert merger._disabled is True
    assert host.store.position(YES).size == 150.0
    assert alerts.messages == [
        f"trader merge unknown: match {MATCH} merges off for this map tx 0xdead: "
        "receipt: TimeExhausted"
    ]


def test_adapter_exception_disables_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    calls = {"n": 0}

    def blow(*, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float) -> MergeOutcome:
        del cfg, condition_id, amount_raw, deadline_s
        calls["n"] += 1
        assert host.store.merge_is_held(YES) is True
        assert host.store.merge_is_held(NO) is True
        raise RuntimeError("relayer blew up")

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", blow)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        await _schedule_and_wait(merger)
        await merger.merge_all()

    asyncio.run(run())
    assert calls["n"] == 1
    assert _released(host)
    assert merger._disabled is True
    assert host.store.position(YES).size == 150.0
    assert host.held_at_consume is None
    assert "merges off for this map tx -" in alerts.messages[0]
    assert "RuntimeError" in alerts.messages[0]


def test_gated_thread_keeps_the_wallet_until_it_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = _open_host(other_dir, yes=150.0, no=120.0, price=0.50)
    other.engine = host.engine
    gate = threading.Event()
    entered = threading.Event()
    active = {"n": 0, "max": 0}

    def adapter(
        *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, amount_raw, deadline_s
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        entered.set()
        gate.wait()
        active["n"] -= 1
        return MERGED

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", adapter)
    first = _merger_for(host, mode="live")
    second = _merger_for(other, mode="live")

    async def run() -> None:
        first.schedule()
        for _ in range(50):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        assert host.engine._chain_lock.locked()
        assert host.store.merge_is_held(YES) is True
        second.schedule()
        await asyncio.sleep(0.05)
        assert active["n"] == 1
        gate.set()
        await first.wait_inflight()
        await second.wait_inflight()

    asyncio.run(run())
    assert active["max"] == 1
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert other.store.position(YES).size == pytest.approx(30.0)
    assert _released(host)
    assert _released(other)


def test_cancel_does_not_drop_the_active_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    gate = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}

    def adapter(
        *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, amount_raw, deadline_s
        calls["n"] += 1
        entered.set()
        gate.wait()
        return MERGED

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", adapter)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        merger.schedule()
        for _ in range(50):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        merger.cancel()
        merger.schedule()
        assert calls["n"] == 1
        assert host.store.merge_is_held(YES) is True
        assert host.engine._chain_lock.locked()
        gate.set()
        await merger.wait_inflight()
        merger.schedule()
        await merger.wait_inflight()

    asyncio.run(run())
    assert calls["n"] == 1
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert alerts.messages == [f"trader merge: match {MATCH} pairs 120.00 tx {TX}"]


def test_cancelled_waiter_does_not_drop_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    gate = threading.Event()
    entered = threading.Event()

    def adapter(
        *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, amount_raw, deadline_s
        entered.set()
        gate.wait()
        return MERGED

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", adapter)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        merger.schedule()
        waiter = asyncio.create_task(merger.wait_inflight())
        for _ in range(50):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert merger._task is not None and not merger._task.done()
        assert host.store.merge_is_held(YES) is True
        assert host.engine._chain_lock.locked()
        gate.set()
        await merger.wait_inflight()

    asyncio.run(run())
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert _released(host)


def test_queued_merge_rechecks_recovery_and_unacked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await host.engine._chain_lock.acquire()
        merger.schedule()
        await asyncio.sleep(0)
        host.core.note_recovery(now_ns=3)
        host.core.drain_apply()
        late = Fill(YES, Side.BUY, 0.50, 10.0, "t3:v3", 2.0, is_maker=True)
        host.store.apply_matched_fill(late, "t3:v3")
        host.engine._chain_lock.release()
        await merger.wait_inflight()

    asyncio.run(run())
    assert scripted.amounts == []
    assert _released(host)


def test_balance_read_rechecks_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store

    async def read_fresh_balances(token_ids: list[str]) -> dict[str, float] | None:
        del token_ids
        host.core.note_recovery(now_ns=9)
        host.core.drain_apply()
        return host.chain

    host.read_fresh_balances = read_fresh_balances
    asyncio.run(_schedule_and_wait(_merger_for(host, mode="live")))
    assert scripted.amounts == []
    assert _released(host)


def test_queued_final_merge_stops_after_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    gate = threading.Event()
    entered = threading.Event()
    calls = {"n": 0}

    def adapter(
        *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, deadline_s
        calls["n"] += 1
        calls["raw"] = amount_raw
        entered.set()
        gate.wait()
        return MergeOutcome(status="unknown", tx_hash="0xdead", reason="post: relayer 408: ")

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", adapter)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        merger.schedule()
        for _ in range(50):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        final = asyncio.create_task(merger.merge_all())
        await asyncio.sleep(0.05)
        assert calls["n"] == 1
        gate.set()
        await final

    asyncio.run(run())
    assert calls["n"] == 1
    assert merger._disabled is True
    assert host.store.position(YES).size == 150.0
    assert "merges off for this map" in alerts.messages[0]


def test_booking_failure_retries_without_a_second_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    cash_before = host.store.running_net_cash
    original = host.store.apply_merge
    failed = {"once": False}

    def flaky(*, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool:
        if not failed["once"]:
            failed["once"] = True
            raise sqlite3.OperationalError("database is locked")
        return original(tx_hash=tx_hash, token_ids=token_ids, qty=qty)

    monkeypatch.setattr(host.store, "apply_merge", flaky)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        assert scripted.amounts == [120_000_000]
        assert host.store.merge_is_held(YES) is True
        assert host.store.merge_is_held(NO) is True
        assert host.store.position(YES).size == 150.0
        assert host.store.running_net_cash == pytest.approx(cash_before)
        assert merger._disabled is False
        await _schedule_and_wait(merger)

    asyncio.run(run())
    merged = [
        row
        for row in host.store.core_outbox_after(after_seq=0, tokens=TOKENS)
        if row.event == "merged"
    ]
    assert scripted.amounts == [120_000_000]
    assert len(merged) == 2
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert host.store.running_net_cash == pytest.approx(cash_before + 120.0)
    assert _released(host)
    assert any(
        "trader merge accounting:" in message and TX in message for message in alerts.messages
    )
    assert alerts.messages[-1] == f"trader merge: match {MATCH} pairs 120.00 tx {TX}"


def test_final_merge_still_requires_settled_positions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    merger = _merger_for(host, mode="live")
    host.core.note_recovery(now_ns=3)
    host.core.drain_apply()

    async def run() -> None:
        await merger.merge_all()
        host.core.note_recovery_verified(
            now_ns=4,
            generation=host.core.state.recovery_generation,
            inventory=(
                TokenInventory(token_index=0, qty=150.0, cost_basis=75.0, last_buy_ns=None),
                TokenInventory(token_index=1, qty=120.0, cost_basis=60.0, last_buy_ns=None),
            ),
        )
        host.core.drain_apply()
        assert host.core.state.recovery_pending is False
        late = Fill(YES, Side.BUY, 0.50, 10.0, "t9:v9", 2.0, is_maker=True)
        host.store.apply_matched_fill(late, "t9:v9")
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == []


def test_merge_all_ignores_quiesce_and_pause_and_needs_one_cent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=0.5, no=0.005, price=0.50)
    scripted.store = host.store
    host.chain = {YES: 0.5, NO: 0.5}
    merger = _merger_for(host, mode="live")
    merger.mark_quiesced()
    merger._paused_until_s = time.monotonic() + 300.0

    async def run() -> None:
        await merger.merge_all()
        assert scripted.amounts == []
        assert _released(host)
        _buy(host.store, token_id=NO, size=0.015, price=0.50, key="t4:v4")
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == [20_000]
    assert scripted.held_during_call == [True]
    assert _released(host)


def test_final_booking_failure_blocks_cleanup_until_local_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = _record_alerts(monkeypatch)
    scripted = _script(monkeypatch, MERGED)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    scripted.store = host.store
    cash_before = host.store.running_net_cash
    original = host.store.apply_merge

    def locked(*, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool:
        del tx_hash, token_ids, qty
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(host.store, "apply_merge", locked)
    merger = _merger_for(host, mode="live")
    cleaned = False

    async def run() -> None:
        nonlocal cleaned
        with pytest.raises(MergeAccountingError) as caught:
            await merger.merge_all()
            cleaned = True
        assert cleaned is False
        err = caught.value
        assert err.tx_hash == TX
        assert err.qty == pytest.approx(120.0)
        assert merger._pending is not None
        assert host.store.merge_is_held(YES) is True
        assert host.store.merge_is_held(NO) is True
        assert host.store.position(YES).size == 150.0
        assert host.store.running_net_cash == pytest.approx(cash_before)
        monkeypatch.setattr(host.store, "apply_merge", original)
        await merger.merge_all()

    asyncio.run(run())
    assert scripted.amounts == [120_000_000]
    assert host.store.position(YES).size == pytest.approx(30.0)
    assert host.store.position(NO).size == pytest.approx(0.0)
    assert host.store.running_net_cash == pytest.approx(cash_before + 120.0)
    assert merger._pending is None
    assert _released(host)
    assert any(
        "trader merge accounting:" in message and TX in message for message in alerts.messages
    )
    assert alerts.messages[-1] == f"trader merge: match {MATCH} pairs 120.00 tx {TX}"


def test_merge_all_books_pending_then_sends_the_final_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    cash_before = host.store.running_net_cash
    amounts: list[int] = []
    tail_tx = "0x" + "cd" * 32

    def adapter(
        *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
    ) -> MergeOutcome:
        del cfg, condition_id, deadline_s
        amounts.append(amount_raw)
        tx_hash = TX if len(amounts) == 1 else tail_tx
        return MergeOutcome(status="merged", tx_hash=tx_hash, reason="")

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", adapter)
    original = host.store.apply_merge
    failed = {"once": False}

    def flaky(*, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool:
        if not failed["once"]:
            failed["once"] = True
            raise sqlite3.OperationalError("database is locked")
        return original(tx_hash=tx_hash, token_ids=token_ids, qty=qty)

    monkeypatch.setattr(host.store, "apply_merge", flaky)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        await _schedule_and_wait(merger)
        assert amounts == [120_000_000]
        assert merger._pending is not None
        _buy(host.store, token_id=NO, size=20.0, price=0.50, key="t5:v5")
        assert host.store.position(NO).size == pytest.approx(140.0)
        host._consume_core_outbox(NO)
        assert host.core.state.recovery_pending is False
        assert host.store.has_unacked_matched({YES, NO}) is False
        merger.mark_quiesced()
        await merger.merge_all()

    asyncio.run(run())
    assert amounts == [120_000_000, 20_000_000]
    assert host.store.position(YES).size == pytest.approx(10.0)
    assert host.store.position(NO).size == pytest.approx(0.0)
    assert host.store.running_net_cash == pytest.approx(cash_before - 10.0 + 140.0)
    assert merger._pending is None
    assert _released(host)
