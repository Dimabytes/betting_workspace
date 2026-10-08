"""Regressions for live book clocks, feed deadlines, and callback order plans."""

# pyright: reportPrivateUsage=false

import asyncio
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from polymaker.domain import OpenOrder, Side
from polymaker.marketdata.orderbook import OrderBook

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.lifecycle import mark_canceling
from strategy.policy import follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    Permissions,
    RawDeltaSignal,
    SignalUpdate,
    TokenBook,
    TokenInventory,
)
from trader.match_worker import MatchWorker
from trader.session_core import LiveCore, LiveSources, books_from_md
from trader.session_engine import StrategyCell
from trader.wallet_host import WalletHost, _durable_cancel
from trader.wallet_store import WalletStateStore

NS = 1_000_000_000
START_NS = 15 * NS
YES = "yes-token"
NO = "no-token"


@dataclass(frozen=True)
class LiveScenario:
    core: LiveCore
    worker: MatchWorker
    sell_id: str
    sell_venue_id: str
    buy_venue_ids: frozenset[str]


def make_books(now_ns: int, yes_ask: float) -> BookPair:
    return BookPair(
        tokens=(
            TokenBook(0, 0.50, yes_ask, 100.0, 100.0, now_ns),
            TokenBook(1, 1.0 - yes_ask, 0.50, 100.0, 100.0, now_ns),
        )
    )


def make_sources(core: LiveCore, now_ns: int, books: BookPair) -> LiveSources:
    return LiveSources(
        now_ns=now_ns,
        books=books,
        clock=GameClock(now_ns, 200, False, False),
        limits=core.state.limits,
        permissions=Permissions(False, False, True, True, False),
        budget=Budget(
            cash_usdc=1000.0, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        store_yes=core.state.inventory[0].qty,
        store_no=core.state.inventory[1].qty,
        settled_buys={},
    )


def open_scenario() -> LiveScenario:
    core = LiveCore(
        policy=follow300_policy(level_usdc=30.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=MarketLimits(5.0, 0.01, 0.05, 0),
        freshness=FreshnessLimits(5.0, 16.0, 45.0),
        yes_token=YES,
        no_token=NO,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(0, 59.0, 23.75, 0),
            TokenInventory(1, 0.0, 0.0, None),
        ),
        has_buy_fill=True,
    )
    core.enqueue(
        SignalUpdate(
            now_ns=START_NS,
            signal=RawDeltaSignal(0.05, START_NS, START_NS, 0.51, 0, 0),
        )
    )
    core.run_cycle(make_sources(core, START_NS, make_books(START_NS, 0.52)))
    batch = core.take_plan()
    assert batch is not None
    sell = next(item for item in batch.to_place if item.quote.side is Side.SELL)
    orders = [
        OpenOrder(
            f"venue-{item.order_id}",
            item.quote.token_id,
            item.quote.side,
            item.quote.price,
            item.quote.size,
        )
        for item in batch.to_place
    ]
    core.note_placed(orders, START_NS)
    core.drain_apply()
    worker = object.__new__(MatchWorker)
    worker._core = core
    worker._cid = "condition"
    worker._host = cast(WalletHost, Mock())
    worker._cell = StrategyCell()
    worker._cell.publish(0.56)
    return LiveScenario(
        core=core,
        worker=worker,
        sell_id=sell.order_id,
        sell_venue_id=f"venue-{sell.order_id}",
        buy_venue_ids=frozenset(order.order_id for order in orders if order.side is Side.BUY),
    )


def acknowledge_sell_cancel(scenario: LiveScenario) -> str:
    core = scenario.core
    core._state = mark_canceling(state=core.state, order_id=scenario.sell_id, reason="reprice")
    scenario.worker._finish_cancel([scenario.sell_venue_id], True, START_NS)
    return next(order.order_id for order in core.state.orders if order.side == "SELL")


def read_local_books(exchange_offset_s: float, local_age_s: float, now_ns: int) -> BookPair:
    yes = OrderBook(tick_size=0.01)
    no = OrderBook(tick_size=0.01)
    with patch("trader.session_core.time.time", return_value=1000.0):
        yes.apply_snapshot([(0.50, 100.0)], [(0.52, 100.0)], 1000.0 + exchange_offset_s)
        no.apply_snapshot([(0.48, 100.0)], [(0.50, 100.0)], 1000.0 + exchange_offset_s)
        yes.local_ts = 1000.0 - local_age_s
        no.local_ts = 1000.0 - local_age_s
        pair = books_from_md(yes=yes, no=no, now_ns=now_ns, own_orders=())
    assert pair is not None
    return pair


class BookClockTests(unittest.TestCase):
    def test_exchange_clock_skew_keeps_fresh_orders(self) -> None:
        for exchange_offset_s in (0.032, -300.0):
            with self.subTest(exchange_offset_s=exchange_offset_s):
                scenario = open_scenario()
                now_ns = START_NS + NS
                books = read_local_books(exchange_offset_s, 0.01, now_ns)
                scenario.core.run_cycle(make_sources(scenario.core, now_ns, books))
                batch = scenario.core.take_plan()
                assert batch is not None
                self.assertEqual(batch.to_cancel, ())
                self.assertEqual(batch.to_place, ())

    def test_recent_exchange_timestamp_does_not_refresh_old_local_book(self) -> None:
        scenario = open_scenario()
        now_ns = START_NS + NS
        books = read_local_books(0.0, 6.0, now_ns)
        scenario.core.run_cycle(make_sources(scenario.core, now_ns, books))
        batch = scenario.core.take_plan()
        assert batch is not None
        self.assertIn(scenario.sell_venue_id, batch.to_cancel)
        self.assertEqual(scenario.core.last_block, "stale_book")


class FeedDeadlineTests(unittest.TestCase):
    def test_entry_timeout_keeps_sell_and_exit_timeout_cancels_it(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        with patch.object(MatchWorker, "_schedule_entry_cancel") as cancel_buys:
            scenario.worker._on_entry_feed_timeout()
        cancel_buys.assert_called_once_with()
        now_ns = START_NS + 16 * NS + NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        entry_batch = core.take_plan()
        assert entry_batch is not None
        self.assertEqual(set(entry_batch.to_cancel), scenario.buy_venue_ids)
        self.assertEqual(entry_batch.to_place, ())
        self.assertIsNotNone(core.state.signal)
        self.assertTrue(any(order.order_id == scenario.sell_id for order in core.state.orders))

        now_ns = START_NS + 44 * NS
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        held_batch = core.take_plan()
        assert held_batch is not None
        self.assertNotIn(scenario.sell_venue_id, held_batch.to_cancel)

        with patch("trader.match_worker.core_now_ns", return_value=START_NS + 45 * NS):
            scenario.worker._on_exit_feed_timeout()
        now_ns = START_NS + 45 * NS + NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        exit_batch = core.take_plan()
        assert exit_batch is not None
        self.assertIn(scenario.sell_venue_id, exit_batch.to_cancel)

    def test_entry_timeout_does_not_allow_new_buys(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        core._state = replace(
            core.state,
            orders=(),
            inventory=(TokenInventory(0, 0.0, 0.0, None), TokenInventory(1, 0.0, 0.0, None)),
        )
        with patch.object(MatchWorker, "_schedule_entry_cancel"):
            scenario.worker._on_entry_feed_timeout()
        now_ns = START_NS + 17 * NS
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        batch = core.take_plan()
        assert batch is not None
        self.assertEqual(batch.to_place, ())


class CallbackPlanTests(unittest.TestCase):
    def test_cancel_ack_dispatches_same_replacement_once(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        replacement_id = acknowledge_sell_cancel(scenario)
        core.drain_apply()
        now_ns = START_NS + NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        batch = core.take_plan()
        assert batch is not None
        sells = [item for item in batch.to_place if item.quote.side is Side.SELL]
        self.assertEqual([item.order_id for item in sells], [replacement_id])
        quote = sells[0].quote
        core.note_placed(
            [OpenOrder("replacement", YES, Side.SELL, quote.price, quote.size)], now_ns
        )
        core.drain_apply()
        now_ns += NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        next_batch = core.take_plan()
        assert next_batch is not None
        self.assertEqual(next_batch.to_place, ())

    def test_halt_discards_queued_replacement(self) -> None:
        scenario = open_scenario()
        acknowledge_sell_cancel(scenario)
        now_ns = START_NS + NS // 10
        sources = make_sources(scenario.core, now_ns, make_books(now_ns, 0.52))
        sources = replace(sources, permissions=replace(sources.permissions, halt=True))
        scenario.core.run_cycle(sources)
        batch = scenario.core.take_plan()
        assert batch is not None
        self.assertEqual(batch.to_place, ())

    def test_signal_expiry_discards_queued_replacement(self) -> None:
        scenario = open_scenario()
        acknowledge_sell_cancel(scenario)
        now_ns = START_NS + 46 * NS
        scenario.core.run_cycle(make_sources(scenario.core, now_ns, make_books(now_ns, 0.52)))
        batch = scenario.core.take_plan()
        assert batch is not None
        self.assertEqual(batch.to_place, ())

    def test_rollback_removes_uncommitted_callback_plan(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        memory = core.capture()
        acknowledge_sell_cancel(scenario)
        core.revert(memory)
        replacement_id = acknowledge_sell_cancel(scenario)
        now_ns = START_NS + NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        batch = core.take_plan()
        assert batch is not None
        self.assertEqual([item.order_id for item in batch.to_place], [replacement_id])

    def test_rollback_restores_undispatched_callback_plan(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        replacement_id = acknowledge_sell_cancel(scenario)
        memory = core.capture()
        now_ns = START_NS + NS // 10
        sources = make_sources(core, now_ns, make_books(now_ns, 0.52))
        core.run_cycle(sources)
        core.revert(memory)
        core.run_cycle(sources)
        batch = core.take_plan()
        assert batch is not None
        self.assertEqual([item.order_id for item in batch.to_place], [replacement_id])

    def test_late_fill_reduces_replacement_before_dispatch(self) -> None:
        scenario = open_scenario()
        core = scenario.core
        replacement_id = acknowledge_sell_cancel(scenario)
        now_ns = START_NS + NS // 10
        core.note_fill(
            fill_key="late-sell-fill",
            venue_id=scenario.sell_venue_id,
            qty=9.0,
            price=0.56,
            now_ns=now_ns,
            token_index=0,
            side="SELL",
        )
        core.drain_apply()
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        first_batch = core.take_plan()
        assert first_batch is not None
        self.assertNotIn(replacement_id, [item.order_id for item in first_batch.to_place])
        now_ns += NS // 10
        core.run_cycle(make_sources(core, now_ns, make_books(now_ns, 0.52)))
        next_batch = core.take_plan()
        assert next_batch is not None
        sells = [item for item in next_batch.to_place if item.quote.side is Side.SELL]
        self.assertEqual([item.quote.size for item in sells], [50.0])


def test_proven_cancel_of_a_gone_sell_frees_the_exit(tmp_path: Path) -> None:
    """A filled SELL leaves the fork book before its cancel returns.

    The core still maps the venue id and keeps the order canceling. The proven
    cancel has to ack that id anyway, or the exit slot stays taken for the map.
    """
    scenario = open_scenario()
    scenario.worker._journal = None
    scenario.core._state = mark_canceling(
        state=scenario.core.state, order_id=scenario.sell_id, reason="reprice"
    )
    host = cast(WalletHost, Mock())
    host.engine.state.orders = {}
    host._worker_by_token = {}
    host._worker_by_cid = {scenario.worker._cid: scenario.worker}
    store = WalletStateStore(tmp_path / "wallet.db")
    host.store = store

    async def cancel(order_ids: list[str]) -> bool:
        assert order_ids == [scenario.sell_venue_id]
        return True

    with patch("trader.match_worker.core_now_ns", return_value=START_NS):
        ok = asyncio.run(_durable_cancel(host, cancel, [scenario.sell_venue_id]))
    store.close()
    assert ok
    assert all(order.order_id != scenario.sell_id for order in scenario.core.state.orders)
    replacement_id = next(
        order.order_id for order in scenario.core.state.orders if order.side == "SELL"
    )
    now_ns = START_NS + NS // 10
    scenario.core.run_cycle(make_sources(scenario.core, now_ns, make_books(now_ns, 0.52)))
    batch = scenario.core.take_plan()
    assert batch is not None
    sells = [item for item in batch.to_place if item.quote.side is Side.SELL]
    assert [item.order_id for item in sells] == [replacement_id]


if __name__ == "__main__":
    unittest.main()
