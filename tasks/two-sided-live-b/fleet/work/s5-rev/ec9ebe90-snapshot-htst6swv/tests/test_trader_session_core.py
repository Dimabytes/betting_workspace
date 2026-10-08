"""Unit cases for the live Follow300 adapter."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from polymaker.config import StrategyProfile
from polymaker.domain import OpenOrder, Position, Quote, Regime, Side
from polymaker.marketdata.orderbook import BookView, OrderBook
from polymaker.strategy.quoting import QuoteInputs
from trader_session_fixtures import NO_TOKEN, YES_TOKEN, make_meta

from shared.constants.strategy import LIVE_DOTA_MAX_POSITION_LEVELS
from strategy.lifecycle import mark_canceling, occupy_sell, sell_occupied
from strategy.policy import follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    BuySettled,
    CancelAck,
    CancelTimeout,
    CancelUnsettled,
    Fill,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    OrderRecord,
    OrderStatus,
    Permissions,
    PermissionsUpdate,
    PlaceOrder,
    Plan,
    RawDeltaSignal,
    RestingOrder,
    Rung,
    SignalUpdate,
    TokenInventory,
)
from trader.core_persistence import (
    CoreCommand,
    CoreSessionKey,
    insert_unsettled_buy,
    prove_unsettled_buy,
    resolve_settled_buys,
    upsert_command,
)
from trader.core_session_io import (
    SessionIdentity,
    apply_and_persist,
    load_core_snapshot,
    persist_core_snapshot,
)
from trader.session_budget import budget_from_orders
from trader.session_core import (
    STALE_CANCEL_SECONDS,
    CollateralCache,
    LiveCore,
    LiveSources,
    books_from_md,
    books_from_views,
    drive,
    entry_block_from_reason,
    held_position,
    live_sources,
    make_esports_reconcile,
    permissions_from_quote,
)
from trader.session_types import EntryBlock
from trader.wallet_store import WalletStateStore


def _policy():
    return follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0)


def _limits() -> MarketLimits:
    return MarketLimits(
        min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05, radiant_token_index=0
    )


def _freshness() -> FreshnessLimits:
    return FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0)


def _core(*, drop_sell: Callable[[Quote], bool] | None = None) -> LiveCore:
    return LiveCore(
        policy=_policy(),
        limits=_limits(),
        freshness=_freshness(),
        yes_token=YES_TOKEN,
        no_token=NO_TOKEN,
        drop_sell=drop_sell or (lambda _quote: False),
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )


def _view(bid: float | None, ask: float | None) -> BookView:
    return BookView(
        best_bid=bid,
        best_bid_size=10.0,
        best_ask=ask,
        best_ask_size=10.0,
        second_bid=None,
        second_ask=None,
        bid_depth=10.0,
        ask_depth=10.0,
    )


def _inputs(*, regime: Regime = Regime.QUIET) -> QuoteInputs:
    view = _view(0.50, 0.52)
    return QuoteInputs(
        meta=make_meta(),
        regime=regime,
        fv=0.51,
        vol_short=0.0,
        toxicity=0.0,
        yes_view=view,
        no_view=view,
        pos_yes=Position(YES_TOKEN, 0.0, 0.0),
        pos_no=Position(NO_TOKEN, 0.0, 0.0),
        profile=StrategyProfile(),
        now=0.0,
    )


def _sources(
    *,
    now_ns: int = 0,
    books: BookPair | None = None,
    regime: Regime = Regime.QUIET,
    store: WalletStateStore | None = None,
    budget: float = 10_000.0,
    store_yes: float = 0.0,
    store_no: float = 0.0,
    second: int = 100,
    has_books: bool = True,
    settled_buys: dict[str, float] | None = None,
) -> LiveSources:
    yes = YES_TOKEN
    no = NO_TOKEN
    permissions = Permissions(
        halt=regime is Regime.HALTED,
        reduce_only=regime is Regime.REDUCE_ONLY,
        allow_buy=True,
        allow_sell=True,
        sell_unconfirmed=False,
    )
    if store is not None:
        permissions = permissions_from_quote(
            regime=regime,
            store=store,
            yes=yes,
            no=no,
            sidecar_usable=True,
            held_token=yes if store_yes >= 5.0 else (no if store_no >= 5.0 else None),
        )
    pair = books
    if books is None and has_books:
        pair = books_from_views(yes=_view(0.50, 0.52), no=_view(0.48, 0.50), ts_ns=now_ns)
    return LiveSources(
        now_ns=now_ns,
        books=pair,
        clock=GameClock(now_ns=now_ns, game_second=second, paused=False, game_ended=False),
        limits=_limits(),
        permissions=permissions,
        budget=Budget(
            cash_usdc=budget, cap_room_usdc=float("inf"), account_cap_room_usdc=float("inf")
        ),
        store_yes=store_yes,
        store_no=store_no,
        settled_buys={} if settled_buys is None else settled_buys,
    )


def _prime(core: LiveCore, now_ns: int = 0) -> LiveCore:
    core.enqueue(
        SignalUpdate(
            now_ns=now_ns,
            signal=RawDeltaSignal(
                predicted_delta=0.05,
                source_received_ns=now_ns,
                received_ns=now_ns,
                anchor_p=0.51,
                deaths_radiant=0,
                deaths_dire=0,
            ),
        )
    )
    drive(core, _inputs(), _sources(now_ns=now_ns))
    return core


def _venue(quote: Quote, venue_id: str) -> OpenOrder:
    return OpenOrder(venue_id, quote.token_id, quote.side, quote.price, quote.size)


def test_permissions_map_halt_reduce_buy_block_freeze_inflight(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    halt = permissions_from_quote(
        regime=Regime.HALTED,
        store=store,
        yes=YES_TOKEN,
        no=NO_TOKEN,
        sidecar_usable=True,
        held_token=None,
    )
    assert halt.halt is True
    reduce = permissions_from_quote(
        regime=Regime.REDUCE_ONLY,
        store=store,
        yes=YES_TOKEN,
        no=NO_TOKEN,
        sidecar_usable=True,
        held_token=None,
    )
    assert reduce.reduce_only is True
    store.block_buy(YES_TOKEN)
    blocked = permissions_from_quote(
        regime=Regime.QUIET,
        store=store,
        yes=YES_TOKEN,
        no=NO_TOKEN,
        sidecar_usable=True,
        held_token=None,
    )
    assert blocked.allow_buy is False
    store.freeze_sell(YES_TOKEN, 12.0)
    frozen = permissions_from_quote(
        regime=Regime.QUIET,
        store=store,
        yes=YES_TOKEN,
        no=NO_TOKEN,
        sidecar_usable=True,
        held_token=YES_TOKEN,
    )
    assert frozen.allow_sell is False
    store.mark_inflight(NO_TOKEN)
    inflight = permissions_from_quote(
        regime=Regime.QUIET,
        store=store,
        yes=YES_TOKEN,
        no=NO_TOKEN,
        sidecar_usable=True,
        held_token=None,
    )
    assert inflight.sell_unconfirmed is True
    store.close()


def test_missing_book_is_none() -> None:
    assert books_from_views(yes=_view(None, 0.52), no=_view(0.48, 0.50), ts_ns=0) is None
    assert books_from_views(yes=_view(0.50, 0.52), no=_view(0.48, None), ts_ns=0) is None


def _md_book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBook:
    book = OrderBook(tick_size=0.01)
    book.apply_snapshot(bids, asks, 1000.0)
    return book


def _no_book() -> OrderBook:
    return _md_book([(0.48, 70.0)], [(0.50, 70.0)])


def _own_order(
    order_id: str,
    *,
    token_index: int,
    side: Literal["BUY", "SELL"],
    price: float,
    qty: float,
    filled: float = 0.0,
    accepted: bool = True,
) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=token_index,
        side=side,
        price=price,
        submitted_qty=qty,
        filled_qty=filled,
        level_index=0 if side == "BUY" else None,
        status="live" if accepted else "pending",
        accepted=accepted,
        partially_filled=filled > 0.0,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        # Far before the snapshot, so the strip treats the order as already in the book.
        accepted_ns=-1_000_000_000_000 if accepted else None,
    )


def test_books_from_md_strips_own_bid_level() -> None:
    yes = _md_book([(0.50, 100.0), (0.49, 60.0)], [(0.52, 80.0)])
    own = [_own_order("c0", token_index=0, side="BUY", price=0.50, qty=100.0)]
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=own)
    assert pair is not None
    assert pair.tokens[0].bid == 0.49
    assert pair.tokens[0].bid_size == 60.0
    assert pair.tokens[0].ask == 0.52


def test_books_from_md_keeps_a_gone_bid() -> None:
    yes = _md_book([(0.50, 40.0)], [(0.52, 80.0)])
    gone = replace(_own_order("c0", token_index=0, side="BUY", price=0.50, qty=40.0), status="gone")
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=[gone])
    assert pair is not None
    assert pair.tokens[0].bid == 0.50
    assert pair.tokens[0].bid_size == 40.0


def test_books_from_md_keeps_foreign_leftover() -> None:
    yes = _md_book([(0.50, 100.0)], [(0.52, 80.0)])
    own = [_own_order("c0", token_index=0, side="BUY", price=0.50, qty=40.0)]
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=own)
    assert pair is not None
    assert pair.tokens[0].bid == 0.50
    assert pair.tokens[0].bid_size == 60.0


def test_books_from_md_counts_only_the_fill_leftover() -> None:
    yes = _md_book([(0.50, 100.0)], [(0.52, 80.0)])
    own = [_own_order("c0", token_index=0, side="BUY", price=0.50, qty=100.0, filled=40.0)]
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=own)
    assert pair is not None
    assert pair.tokens[0].bid_size == 40.0


def test_books_from_md_strips_own_ask() -> None:
    yes = _md_book([(0.50, 100.0)], [(0.52, 80.0), (0.53, 30.0)])
    own = [_own_order("s0", token_index=0, side="SELL", price=0.52, qty=80.0)]
    pair = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=own)
    assert pair is not None
    assert pair.tokens[0].ask == 0.53
    assert pair.tokens[0].ask_size == 30.0
    assert pair.tokens[0].bid == 0.50


def test_books_from_md_ignores_pending_and_empty_orders() -> None:
    yes = _md_book([(0.50, 100.0)], [(0.52, 80.0)])
    pending = _own_order("c0", token_index=0, side="BUY", price=0.50, qty=100.0, accepted=False)
    stripped = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=[pending])
    assert stripped is not None
    assert stripped.tokens[0].bid_size == 100.0
    plain = books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=())
    assert plain is not None
    assert plain.tokens[0].bid == 0.50
    assert plain.tokens[0].bid_size == 100.0


def test_books_from_md_skips_accept_newer_than_the_snapshot() -> None:
    with patch("trader.session_core.time.time", return_value=2000.0):
        yes = _md_book([(0.50, 100.0)], [(0.52, 80.0)])
        no = _no_book()
        yes.local_ts = 2000.0
        no.local_ts = 2000.0
        late = replace(
            _own_order("c0", token_index=0, side="BUY", price=0.50, qty=40.0), accepted_ns=1
        )
        pair = books_from_md(yes=yes, no=no, now_ns=0, own_orders=[late])
    assert pair is not None
    assert pair.tokens[0].bid_size == 100.0


def test_books_from_md_empty_side_returns_none() -> None:
    yes = _md_book([(0.50, 100.0)], [(0.52, 80.0)])
    own = [_own_order("c0", token_index=0, side="BUY", price=0.50, qty=100.0)]
    assert books_from_md(yes=yes, no=_no_book(), now_ns=0, own_orders=own) is None


def test_stale_book_reports_from_core() -> None:
    core = _core()
    core.enqueue(
        SignalUpdate(
            now_ns=0,
            signal=RawDeltaSignal(
                predicted_delta=0.05,
                source_received_ns=0,
                received_ns=0,
                anchor_p=0.51,
                deaths_radiant=0,
                deaths_dire=0,
            ),
        )
    )
    drive(core, _inputs(), _sources(has_books=False))
    assert core.last_block == "stale_book"


def test_plan_to_reconcile_maps_venue_ids() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    assert planned.to_place
    assert all(item.quote.price > 0 for item in planned.to_place)
    assert planned.to_cancel == ()
    reconcile = make_esports_reconcile({make_meta().condition_id: core})
    core._planned = planned
    core._cycle = planned.cycle
    plan = reconcile(
        type("T", (), {"condition_id": make_meta().condition_id})(),
        [],
        tick=0.01,
        reprice_ticks=0,
        resize_frac=0.2,
    )
    assert [quote.price for quote in plan.to_place] == [
        item.quote.price for item in planned.to_place
    ]


def test_frozen_sell_dropped_from_batch() -> None:
    dropped: list[Quote] = []

    def drop(quote: Quote) -> bool:
        if quote.side is Side.SELL:
            dropped.append(quote)
            return True
        return False

    core = _core(drop_sell=drop)
    core.stash(
        Plan(
            keep=(),
            moves=(),
            cancels=(),
            places=(
                PlaceOrder(
                    order_id="c0",
                    episode_id=1,
                    token_index=0,
                    side="SELL",
                    price=0.56,
                    quantity=10.0,
                    level_index=None,
                    reduce_only=True,
                ),
                PlaceOrder(
                    order_id="c1",
                    episode_id=1,
                    token_index=0,
                    side="BUY",
                    price=0.50,
                    quantity=130.0,
                    level_index=0,
                    reduce_only=False,
                ),
            ),
            block_reason="",
        )
    )
    planned = core.take_plan()
    assert planned is not None
    assert len(planned.to_place) == 1
    assert planned.to_place[0].quote.side is Side.BUY
    assert dropped


def test_place_short_mints_submit_timeout() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    assert len(planned.to_place) >= 2
    first = _venue(planned.to_place[0].quote, "v1")
    core.note_placed([first], 0)
    events = core.drain()
    kinds = [type(event).__name__ for event in events]
    assert kinds.count("OrderAccepted") == 1
    assert kinds.count("SubmitTimeout") == len(planned.to_place) - 1
    assert core._venue_to_core["v1"] == planned.to_place[0].order_id


def test_place_gap_in_middle_maps_by_quote() -> None:
    core = _core()
    core.stash(
        Plan(
            keep=(),
            moves=(),
            cancels=(),
            places=(
                PlaceOrder("c0", 1, 0, "BUY", 0.50, 130.0, 0, False),
                PlaceOrder("c1", 1, 0, "SELL", 0.56, 10.0, None, True),
                PlaceOrder("c2", 1, 0, "BUY", 0.49, 132.65, 1, False),
            ),
            block_reason="",
        )
    )
    planned = core.take_plan()
    assert planned is not None
    assert [item.order_id for item in planned.to_place] == ["c0", "c1", "c2"]
    core.note_placed(
        [
            OpenOrder("v-lo", YES_TOKEN, Side.BUY, 0.49, 132.65),
            OpenOrder("v-hi", YES_TOKEN, Side.BUY, 0.50, 130.0),
        ],
        0,
    )
    events = core.drain()
    kinds = [type(event).__name__ for event in events]
    assert kinds == ["OrderAccepted", "SubmitTimeout", "OrderAccepted"]
    assert core._venue_to_core["v-hi"] == "c0"
    assert core._venue_to_core["v-lo"] == "c2"
    assert "c1" not in core._core_to_venue


def test_held_position_larger_wins() -> None:
    both = held_position(yes_size=12.0, no_size=20.0, min_size=5.0)
    assert both is not None
    assert both.token_index == 1
    assert both.qty == 20.0
    tie = held_position(yes_size=10.0, no_size=10.0, min_size=5.0)
    assert tie is not None
    assert tie.token_index == 0
    assert held_position(yes_size=3.0, no_size=3.0, min_size=5.0) is None


def test_cancel_false_mints_timeout() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    core.note_placed([_venue(planned.to_place[0].quote, "v1")], 0)
    core.drain()
    core.note_cancel(["v1"], False, 1)
    events = core.drain()
    assert any(type(event).__name__ == "CancelTimeout" for event in events)
    assert any(order.order_id == planned.to_place[0].order_id for order in core.state.orders)


def test_cancel_ok_buy_goes_gone_and_sell_acks() -> None:
    core = _prime(_core())
    buy_id = _accept_first_buy(core)
    core.note_cancel(["v-buy"], True, 1)
    core.drain_apply()
    buy = next(order for order in core.state.orders if order.order_id == buy_id)
    assert buy.status == "gone"
    sell = _resting("live", order_id="s0", side="SELL")
    core._state = replace(core.state, orders=(*core.state.orders, sell))
    core.bind_venue(core_id="s0", venue_id="v-sell")
    core.note_cancel(["v-sell"], True, 2)
    core.drain_apply()
    assert all(order.order_id != "s0" for order in core.state.orders)
    assert any(order.order_id == buy_id and order.status == "gone" for order in core.state.orders)
    record = OrderRecord(
        order_id=buy_id,
        episode_id=buy.episode_id,
        token_index=buy.token_index,
        side="BUY",
        price=buy.price,
        submitted_qty=buy.submitted_qty,
        filled_qty=buy.filled_qty,
        level_index=buy.level_index,
        terminal=True,
        accepted=True,
        partially_filled=False,
        placed_ns=0,
        accepted_ns=0,
    )
    newbie = _resting("live", order_id="c-new")
    kept = tuple(order for order in core.state.orders if order.order_id != buy_id)
    core._state = replace(
        core.state,
        orders=(*kept, newbie),
        records=(*core.state.records, record),
    )
    core.bind_venue(core_id="c-new", venue_id="v-new")
    core.note_cancel(["v-buy"], True, 3)
    core.drain_apply()
    assert any(order.order_id == "c-new" and order.status == "live" for order in core.state.orders)


def test_cancel_ack_for_an_unmapped_venue_id_mints_nothing() -> None:
    """A proven cancel naming an id this core never mapped is a dropped ack."""
    core = _prime(_core())
    core.drain()
    core.note_cancel(["venue-this-core-never-saw"], True, 1)
    assert core.drain() == []


def test_unproven_cancel_is_flagged_once_and_retires_nothing() -> None:
    """A cancel the venue never proves has to surface before the map ends.

    `_venue_cancels` resends it every cycle, so silence past the window means the
    ack is not coming. The order stays: only the venue may retire a resting order.
    """
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    order_id = planned.to_place[0].order_id
    core.note_placed([_venue(planned.to_place[0].quote, "v1")], 0)
    core.drain()
    core._state = mark_canceling(state=core.state, order_id=order_id, reason="reprice")
    drive(core, _inputs(), _sources(now_ns=1))
    assert core.take_stale_cancels() == ()
    late_ns = 1 + int(STALE_CANCEL_SECONDS * 1e9) + 1
    drive(core, _inputs(), _sources(now_ns=late_ns))
    assert core.take_stale_cancels() == (order_id,)
    assert core.take_stale_cancels() == ()
    drive(core, _inputs(), _sources(now_ns=late_ns + 1))
    assert core.take_stale_cancels() == ()
    assert any(order.order_id == order_id for order in core.state.orders)


def test_cancel_ack_before_wake_reaches_batch() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    buy_id = planned.to_place[0].order_id
    core.note_placed([_venue(planned.to_place[0].quote, "v1")], 0)
    core.drain()
    core.enqueue(CancelAck(now_ns=1, order_id=buy_id))
    core._state = mark_canceling(state=core.state, order_id=buy_id, reason="reprice")
    drive(core, _inputs(), _sources(now_ns=1))
    next_plan = core.take_plan()
    assert next_plan is not None
    assert next_plan.to_place or next_plan.to_cancel


def test_raised_construct_leaves_no_stash() -> None:
    core = _prime(_core())
    assert core.take_plan() is not None
    core.clear_plan()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert core.take_plan() is None
    reconcile = make_esports_reconcile({make_meta().condition_id: core})
    live = [OpenOrder("v1", YES_TOKEN, Side.BUY, 0.50, 10.0)]
    plan = reconcile(
        type("T", (), {"condition_id": make_meta().condition_id})(),
        live,
        tick=0.01,
        reprice_ticks=0,
        resize_frac=0.2,
    )
    assert plan.to_cancel == ["v1"]
    assert plan.to_place == []


def test_fill_key_credits_once() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    core.note_placed([_venue(planned.to_place[0].quote, "v1")], 0)
    core.drain()
    core.note_fill(
        fill_key="t1:v1",
        venue_id="v1",
        qty=5.0,
        price=0.50,
        now_ns=0,
        token_index=0,
        side="BUY",
    )
    drive(core, _inputs(), _sources(now_ns=1, store_yes=5.0))
    qty = core.state.position.qty
    core.note_fill(
        fill_key="t1:v1",
        venue_id="v1",
        qty=5.0,
        price=0.50,
        now_ns=2,
        token_index=0,
        side="BUY",
    )
    drive(core, _inputs(), _sources(now_ns=2, store_yes=5.0))
    assert core.state.position.qty == qty


def test_failed_queues_recovery_sell_only() -> None:
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    buys = tuple(item.order_id for item in planned.to_place)
    core.note_recovery(now_ns=1)
    drive(core, _inputs(), _sources(now_ns=1, store_yes=10.0))
    assert core.state.sell_only is True
    assert core.state.recovery_pending is True
    assert all(order.status == "canceling" for order in core.state.orders if order.order_id in buys)


def test_ledger_mismatch_queues_recovery() -> None:
    core = _prime(_core())
    core.take_plan()
    drive(core, _inputs(), _sources(now_ns=1, store_yes=20.0))
    assert core.state.sell_only is True
    assert core.state.recovery_pending is True
    assert core.state.position.qty == 0.0


def test_held_subtick_ledger_drift_does_not_queue_recovery() -> None:
    """198.35 vs 198.3567 is one CLOB tick; recovery must not pull the exit."""
    core = _prime(_core())
    core.take_plan()
    core._state = replace(
        core.state,
        inventory=(
            core.state.inventory[0],
            TokenInventory(token_index=1, qty=198.35, cost_basis=115.0, last_buy_ns=0),
        ),
        has_buy_fill=True,
    )
    drive(core, _inputs(), _sources(now_ns=1, store_no=198.3567))
    assert core.state.sell_only is False
    assert core.state.recovery_pending is False


def test_subtick_ledger_gap_does_not_queue_recovery() -> None:
    """Sqlite write-off zeros <0.01; that residue must not latch sell_only."""
    core = _prime(_core())
    core.take_plan()
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(token_index=0, qty=0.008926, cost_basis=0.0, last_buy_ns=None),
            core.state.inventory[1],
        ),
    )
    drive(core, _inputs(), _sources(now_ns=1, store_yes=0.0))
    assert core.state.sell_only is False
    assert core.state.recovery_pending is False


def test_whole_tick_ledger_gap_still_queues_recovery() -> None:
    core = _prime(_core())
    core.take_plan()
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(token_index=0, qty=3.18, cost_basis=0.0, last_buy_ns=None),
            core.state.inventory[1],
        ),
    )
    drive(core, _inputs(), _sources(now_ns=1, store_yes=0.0))
    assert core.state.sell_only is True
    assert core.state.recovery_pending is True


def _inflight_buy(store: WalletStateStore, *, price: float, quantity: float) -> None:
    upsert_command(
        store._conn,
        CoreCommand(
            command_id="inflight:b0:c0",
            session_id="other",
            revision=1,
            batch_id="b0",
            kind="place",
            core_order_id="c0",
            token_index=0,
            side="BUY",
            price=price,
            quantity=quantity,
            venue_id=None,
            order_hash="h",
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        ),
    )
    store._conn.commit()


def test_budget_subtracts_resting_buys(tmp_path: Path) -> None:
    cache = CollateralCache()
    cache.value = 100.0
    store = WalletStateStore(tmp_path / "w.db")
    store.orders["v1"] = OpenOrder("v1", YES_TOKEN, Side.BUY, 0.50, 40.0)
    core = _core()
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=1_000.0
    )
    assert budget.cash_usdc == 80.0
    assert budget.cap_room_usdc == core.max_position_levels * 65.0 - 20.0
    store.close()


def test_budget_reserves_core_buys_when_store_empty(tmp_path: Path) -> None:
    cache = CollateralCache()
    cache.value = 400.0
    core = _prime(_core())
    core.take_plan()
    reserved = core.reserved_buy_notional()
    assert reserved > 0
    store = WalletStateStore(tmp_path / "w.db")
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=1_000.0
    )
    assert budget.cash_usdc == 400.0 - reserved
    assert budget.cap_room_usdc == core.max_position_levels * 65.0 - reserved
    store.close()


def test_budget_does_not_double_count_store_row_owned_by_core(tmp_path: Path) -> None:
    cache = CollateralCache()
    cache.value = 400.0
    core = _prime(_core())
    planned = core.take_plan()
    assert planned is not None
    first = planned.to_place[0]
    placed = [_venue(first.quote, "v1")]
    core.note_placed(placed, 0)
    store = WalletStateStore(tmp_path / "w.db")
    store.orders["v1"] = OpenOrder(
        "v1", first.quote.token_id, first.quote.side, first.quote.price, first.quote.size
    )
    reserved = core.reserved_buy_notional()
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=1_000.0
    )
    assert budget.cash_usdc == 400.0 - reserved
    assert budget.cap_room_usdc == core.max_position_levels * 65.0 - reserved
    store.close()


def test_budget_subtracts_durable_inflight_buys(tmp_path: Path) -> None:
    cache = CollateralCache()
    cache.value = 100.0
    store = WalletStateStore(tmp_path / "w.db")
    _inflight_buy(store, price=0.50, quantity=40.0)
    core = _core()
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=1_000.0
    )
    assert budget.cash_usdc == 80.0
    assert budget.cap_room_usdc == core.max_position_levels * 65.0
    assert budget.account_cap_room_usdc == 1_000.0 - 20.0
    store.close()


def test_account_cap_counts_a_position_with_no_core(tmp_path: Path) -> None:
    """A closed map keeps its shares until redeem, so they still spend the account cap."""
    cache = CollateralCache()
    cache.value = 1_000.0
    store = WalletStateStore(tmp_path / "w.db")
    store.set_position("CLOSED_YES", 100.0, 0.50)
    core = _core()
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=1_000.0
    )
    assert budget.account_cap_room_usdc == 1_000.0 - 50.0
    assert budget.cap_room_usdc == core.max_position_levels * 65.0
    store.close()


def test_budget_shared_cap_counts_held_positions(tmp_path: Path) -> None:
    """Held cost shares this map's level cap with its resting BUYs."""
    cache = CollateralCache()
    cache.value = 10_000.0
    core = _prime(_core())
    core.take_plan()
    reserved = core.reserved_buy_notional()
    store = WalletStateStore(tmp_path / "w.db")
    store.set_position(YES_TOKEN, 400.0, 0.50)
    budget = budget_from_orders(
        cache=cache, cores=(core,), store=store, quoting=core, account_cap_usdc=10_000.0
    )
    room = core.max_position_levels * core.policy.level_usdc - 200.0 - reserved
    assert budget.cash_usdc == 10_000.0 - reserved
    assert budget.cap_room_usdc == room
    store.close()


def test_budget_cap_is_this_maps_clip(tmp_path: Path) -> None:
    """The other card's clip and reserves do not raise or spend this map's cap."""
    cache = CollateralCache()
    cache.value = 10_000.0
    big = _prime(_core())
    small = LiveCore(
        policy=follow300_policy(level_usdc=20.0, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=_freshness(),
        yes_token="OTHER_YES",
        no_token="OTHER_NO",
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    store = WalletStateStore(tmp_path / "w.db")
    budget = budget_from_orders(
        cache=cache,
        cores=(big, small),
        store=store,
        quoting=small,
        account_cap_usdc=10_000.0,
    )
    reserved = big.reserved_buy_notional() + small.reserved_buy_notional()
    assert budget.cash_usdc == 10_000.0 - reserved
    assert budget.cap_room_usdc == small.max_position_levels * 20.0
    assert budget.account_cap_room_usdc == 10_000.0 - reserved
    store.close()


def test_entry_block_maps_known_reasons() -> None:
    assert entry_block_from_reason("") is EntryBlock.NONE
    assert entry_block_from_reason("cutoff") is EntryBlock.CUTOFF
    assert entry_block_from_reason("min_price") is EntryBlock.MIN_PRICE
    assert entry_block_from_reason("max_price") is EntryBlock.MAX_PRICE
    assert entry_block_from_reason("no_cash") is EntryBlock.NO_CASH
    assert entry_block_from_reason("position_cap") is EntryBlock.POSITION_CAP
    assert entry_block_from_reason("account_cap") is EntryBlock.ACCOUNT_CAP
    assert entry_block_from_reason("recovery") is EntryBlock.RECOVERY
    assert entry_block_from_reason("winding_down") is EntryBlock.WINDING_DOWN
    assert entry_block_from_reason("ownership_unresolved") is EntryBlock.OWNERSHIP_UNRESOLVED
    assert entry_block_from_reason("position_open") is EntryBlock.POSITION_OPEN
    assert entry_block_from_reason("paused") is EntryBlock.PAUSED
    assert entry_block_from_reason("halt") is EntryBlock.HALT
    assert entry_block_from_reason("mid_spike") is EntryBlock.MID_SPIKE


def test_preview_block_reason_sees_a_queued_pause_without_draining() -> None:
    """The journal can read this tick's pause before the quoter cycle drains it."""
    core = _core()
    core.set_clock(GameClock(now_ns=1, game_second=10, paused=True, game_ended=False))
    assert core.preview_block_reason() == "paused"
    assert core.last_block == ""
    assert core._pending


def test_missing_core_reconcile_cancels_live() -> None:
    reconcile = make_esports_reconcile({})
    live = [OpenOrder("v1", YES_TOKEN, Side.BUY, 0.50, 10.0)]
    plan = reconcile(
        type("T", (), {"condition_id": make_meta().condition_id})(),
        live,
        tick=0.01,
        reprice_ticks=0,
        resize_frac=0.2,
    )
    assert plan.to_cancel == ["v1"]
    assert plan.to_place == []


def test_dropped_sell_place_does_not_hold_the_exit() -> None:
    """A SELL place `_drop_sell` withholds must not keep the exit slot for the map.

    The order carries no venue id, so neither an accept nor a cancel ack can retire
    it. Left in state, `sell_occupied` stays true and `_place_missing` skips every
    later SELL target — the shape that left grid-2996027-m1 holding 27 shares with
    no exit for the rest of the map.
    """
    core = _core(drop_sell=lambda quote: quote.side is Side.SELL)
    core = _prime(core)
    phantom = RestingOrder(
        order_id="s0",
        episode_id=core.state.episode_id,
        token_index=0,
        side="SELL",
        price=0.56,
        submitted_qty=10.0,
        filled_qty=0.0,
        level_index=None,
        status="pending",
        accepted=False,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=None,
    )
    core._state = occupy_sell(state=core.state, order=phantom)
    core.stash(
        Plan(
            keep=(),
            moves=(),
            cancels=(),
            places=(PlaceOrder("s0", core.state.episode_id, 0, "SELL", 0.56, 10.0, None, True),),
            block_reason="",
        )
    )
    assert sell_occupied(state=core.state)
    drive(core, _inputs(), _sources(now_ns=1))
    drive(core, _inputs(), _sources(now_ns=2))
    assert not sell_occupied(state=core.state)
    assert all(order.order_id != "s0" for order in core.state.orders)


def _held_yes_core(now_ns: int) -> LiveCore:
    core = _core()
    core.enqueue(
        SignalUpdate(
            now_ns=now_ns,
            signal=RawDeltaSignal(
                predicted_delta=0.05,
                source_received_ns=now_ns,
                received_ns=now_ns,
                anchor_p=0.51,
                deaths_radiant=0,
                deaths_dire=0,
            ),
        )
    )
    core._state = replace(
        core.state,
        inventory=(
            TokenInventory(token_index=0, qty=59.0, cost_basis=23.75, last_buy_ns=0),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ),
        has_buy_fill=True,
    )
    return core


def _pending_sell(order_id: str, *, episode_id: int) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=episode_id,
        token_index=0,
        side="SELL",
        price=0.56,
        submitted_qty=59.0,
        filled_qty=0.0,
        level_index=None,
        status="pending",
        accepted=False,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=None,
    )


def test_cancel_ack_sell_without_venue_does_not_hold_the_exit() -> None:
    """CancelAck can mint a SELL inside drain_apply; dropping that plan must not lock the slot.

    The live hole on grid-2996027-m2: c15 was planned on c14's CancelAck, the
    place never reached the venue, Wake kept the phantom, and sell_occupied
    blocked every later exit.
    """
    now_ns = 15_000_000_000
    core = _held_yes_core(now_ns)
    core._state = occupy_sell(state=core.state, order=_pending_sell("c15", episode_id=1))
    assert sell_occupied(state=core.state)
    drive(core, _inputs(), _sources(now_ns=now_ns, store_yes=59.0, second=266))
    assert all(order.order_id != "c15" for order in core.state.orders)
    planned = core.take_plan()
    assert planned is not None
    sells = [item for item in planned.to_place if item.quote.side is Side.SELL]
    assert sells
    assert sells[0].quote.size == 59.0


def test_cancel_timeout_retries_sell_then_replaces() -> None:
    now_ns = 15_000_000_000
    sources = _sources(now_ns=now_ns, store_yes=59.0, second=266)
    core = _held_yes_core(now_ns)
    drive(core, _inputs(), sources)
    planned = core.take_plan()
    assert planned is not None
    sell = next(item for item in planned.to_place if item.quote.side is Side.SELL)
    core.note_placed([_venue(sell.quote, "v-sell")], now_ns)
    core.drain_apply()
    core._state = mark_canceling(state=core.state, order_id=sell.order_id, reason="reprice")
    core.note_cancel(["v-sell"], False, now_ns)
    core.drain_apply()
    stuck = next(order for order in core.state.orders if order.order_id == sell.order_id)
    assert stuck.status == "unknown"
    drive(core, _inputs(), sources)
    retry = core.take_plan()
    assert retry is not None
    assert "v-sell" in retry.to_cancel
    core.note_cancel(["v-sell"], True, now_ns)
    core.drain_apply()
    drive(core, _inputs(), sources)
    replaced = core.take_plan()
    assert replaced is not None
    assert any(item.quote.side is Side.SELL for item in replaced.to_place)
    assert all(order.order_id != sell.order_id for order in core.state.orders)


NS = 1_000_000_000


def _resting(
    status: OrderStatus, *, order_id: str = "c0", side: Literal["BUY", "SELL"] = "BUY"
) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=0,
        side=side,
        price=0.50,
        submitted_qty=40.0,
        filled_qty=0.0,
        level_index=0,
        status=status,
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=0,
    )


def _halt(now_ns: int) -> PermissionsUpdate:
    return PermissionsUpdate(
        now_ns=now_ns,
        permissions=Permissions(
            halt=True,
            reduce_only=False,
            allow_buy=False,
            allow_sell=True,
            sell_unconfirmed=False,
        ),
    )


def _accept_first_buy(core: LiveCore) -> str:
    planned = core.take_plan()
    assert planned is not None
    item = next(place for place in planned.to_place if place.quote.side is Side.BUY)
    core.note_placed([_venue(item.quote, "v-buy")], 0)
    core.drain_apply()
    return item.order_id


def _buy_lines(caplog: pytest.LogCaptureFixture, prefix: str) -> list[str]:
    return [record.message for record in caplog.records if record.message.startswith(prefix)]


def _read_sources(store: WalletStateStore, core: LiveCore) -> LiveSources:
    return live_sources(
        now_ns=0,
        books=None,
        clock=core.state.clock,
        limits=core.state.limits,
        regime=Regime.QUIET,
        store=store,
        cache=CollateralCache(1_000.0),
        cores=(core,),
        quoting=core,
        account_cap_usdc=1_000.0,
        sidecar_usable=True,
        yes=YES_TOKEN,
        no=NO_TOKEN,
    )


def test_sync_inputs_repeats_buy_settled_until_the_order_leaves() -> None:
    core = _core()
    core._state = replace(core.state, orders=(_resting("gone"),))
    core.bind_venue(core_id="c0", venue_id="v1")
    sources = _sources(now_ns=1, settled_buys={"v1": 3.0}, has_books=False)
    first = [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)]
    second = [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)]
    assert first == second == [BuySettled(now_ns=1, order_id="c0", matched_qty=3.0)]
    core.apply(first[0])
    assert any(order.order_id == "c0" for order in core.state.orders)
    assert [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)] == first
    core.apply(
        Fill(
            now_ns=2,
            fill_id="f1",
            order_id="c0",
            qty=3.0,
            price=0.50,
            token_index=0,
            side="BUY",
        )
    )
    core.apply(BuySettled(now_ns=3, order_id="c0", matched_qty=3.0))
    assert all(order.order_id != "c0" for order in core.state.orders)
    assert [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)] == []


@pytest.mark.parametrize("status", ["live", "canceling", "unknown", "gone"])
def test_sync_inputs_emits_zero_settlement_for_each_terminal_buy(status: OrderStatus) -> None:
    core = _core()
    core._state = replace(core.state, orders=(_resting(status),))
    core.bind_venue(core_id="c0", venue_id="v1")
    sources = _sources(settled_buys={"v1": 0.0}, has_books=False)
    events = [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)]
    assert events == [BuySettled(now_ns=0, order_id="c0", matched_qty=0.0)]


def test_sync_inputs_ignores_sells_and_unbound_or_stale_venues() -> None:
    core = _core()
    sell = _resting("live", order_id="s0", side="SELL")
    pending = _resting("pending", order_id="c-pending")
    core._state = replace(core.state, orders=(sell, pending))
    core.bind_venue(core_id="s0", venue_id="v-sell")
    core.bind_venue(core_id="c-old", venue_id="v-old")
    sources = _sources(
        settled_buys={"v-sell": 1.0, "v-old": 1.0, "v-missing": 1.0}, has_books=False
    )
    assert [event for event in core.sync_inputs(sources) if isinstance(event, BuySettled)] == []


def test_live_sources_queries_resolved_buys_only_for_a_bound_active_buy(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    core = _core()
    with patch("trader.session_core.resolved_unsettled_buys") as spy:
        _read_sources(store, core)
        core._state = replace(core.state, orders=(_resting("pending"),))
        _read_sources(store, core)
        sell = _resting("live", order_id="s0", side="SELL")
        core._state = replace(core.state, orders=(sell,))
        core.bind_venue(core_id="s0", venue_id="v-sell")
        _read_sources(store, core)
        core._state = replace(
            core.state,
            orders=(),
            records=(
                OrderRecord(
                    order_id="c-old",
                    episode_id=1,
                    token_index=0,
                    side="BUY",
                    price=0.50,
                    submitted_qty=40.0,
                    filled_qty=0.0,
                    level_index=0,
                    terminal=True,
                    accepted=True,
                    partially_filled=False,
                    placed_ns=0,
                    accepted_ns=0,
                ),
            ),
        )
        core.bind_venue(core_id="c-old", venue_id="v-old")
        _read_sources(store, core)
        assert spy.call_count == 0
    core._state = replace(core.state, orders=(_resting("live"),))
    core.bind_venue(core_id="c0", venue_id="v1")
    core.session_id = "sess-a"
    with patch("trader.session_core.resolved_unsettled_buys", return_value={"v1": 4.0}) as spy:
        sources = _read_sources(store, core)
    assert spy.call_count == 1
    assert spy.call_args.args[1] == "sess-a"
    assert sources.settled_buys == {"v1": 4.0}
    store.close()


def test_first_cycle_after_load_delivers_a_buy_resolved_without_a_worker(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    store = WalletStateStore(path)
    core = _core()
    core.session_id = "0xcond"
    core._state = replace(
        core.state,
        episode_id=1,
        episode_counter=1,
        episode_token_index=0,
        rungs=(Rung(index=0, price=0.50, filled_qty=0.0, live_id="c0", done=False),),
        orders=(_resting("gone"),),
    )
    core.bind_venue(core_id="c0", venue_id="v1")
    core._state = replace(
        core.state,
        records=(
            OrderRecord(
                order_id="c0",
                episode_id=1,
                token_index=0,
                side="BUY",
                price=0.50,
                submitted_qty=40.0,
                filled_qty=0.0,
                level_index=0,
                terminal=False,
                accepted=True,
                partially_filled=False,
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
    )
    insert_unsettled_buy(
        store._conn,
        venue_id="v1",
        session_id="0xcond",
        token_id=YES_TOKEN,
        price=0.50,
        qty=40.0,
    )
    prove_unsettled_buy(store._conn, "v1", 0.0)
    assert resolve_settled_buys(store._conn) == ("v1",)
    store._conn.commit()
    persist_core_snapshot(
        store=store,
        core=core,
        identity=SessionIdentity(
            session_id="0xcond",
            key=CoreSessionKey(
                condition_id="0xcond",
                game="dota",
                yes_token=YES_TOKEN,
                no_token=NO_TOKEN,
                yes_is_radiant=True,
            ),
        ),
    )
    fresh = _core()
    fresh.session_id = "0xcond"
    load_core_snapshot(store=store, core=fresh, session_id="0xcond")
    assert fresh.state.orders[0].status == "gone"
    fresh.finish_cycle(_read_sources(store, fresh))
    assert all(order.order_id != "c0" for order in fresh.state.orders)
    store.close()


def test_cancel_to_removal_logs_one_gone_and_one_wait(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    core = _prime(_core())
    order_id = _accept_first_buy(core)
    core.apply(_halt(NS))
    assert core._buy_cancel_timing[order_id].started_ns == NS
    core.apply(CancelUnsettled(now_ns=2 * NS, order_id=order_id))
    core.apply(CancelUnsettled(now_ns=3 * NS, order_id=order_id))
    core.apply(CancelTimeout(now_ns=3 * NS, order_id=order_id))
    core.apply(BuySettled(now_ns=3 * NS, order_id=order_id, matched_qty=1.0))
    assert core._buy_cancel_timing[order_id].started_ns == NS
    assert any(order.order_id == order_id for order in core.state.orders)
    core.apply(BuySettled(now_ns=4 * NS, order_id=order_id, matched_qty=0.0))
    assert all(order.order_id != order_id for order in core.state.orders)
    assert _buy_lines(caplog, "trader core buy gone") == [f"trader core buy gone id={order_id}"]
    assert _buy_lines(caplog, "trader core buy settled") == [
        f"trader core buy settled id={order_id} wait_ms=3000 cause=buy_settled"
    ]


def test_zero_settlement_before_gone_logs_wait_without_a_gone_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    core = _prime(_core())
    order_id = _accept_first_buy(core)
    core.apply(_halt(NS))
    core.apply(BuySettled(now_ns=4 * NS, order_id=order_id, matched_qty=0.0))
    assert _buy_lines(caplog, "trader core buy gone") == []
    assert f"trader core buy settled id={order_id} wait_ms=3000 cause=buy_settled" in _buy_lines(
        caplog, "trader core buy settled"
    )


def test_full_fill_of_a_gone_buy_logs_a_distinct_cause(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    core = _prime(_core())
    order_id = _accept_first_buy(core)
    core.apply(_halt(NS))
    core.apply(CancelUnsettled(now_ns=2 * NS, order_id=order_id))
    order = next(item for item in core.state.orders if item.order_id == order_id)
    core.apply(
        Fill(
            now_ns=4 * NS,
            fill_id="full",
            order_id=order_id,
            qty=order.submitted_qty - order.filled_qty,
            price=order.price,
            token_index=order.token_index,
            side="BUY",
        )
    )
    assert all(item.order_id != order_id for item in core.state.orders)
    assert _buy_lines(caplog, "trader core buy settled") == [
        f"trader core buy settled id={order_id} wait_ms=3000 cause=fill"
    ]


def test_failed_apply_restores_the_cancel_clock(tmp_path: Path) -> None:
    core = _prime(_core())
    order_id = _accept_first_buy(core)
    core.apply(_halt(NS))
    memory = core.capture()

    def boom() -> None:
        core.apply(CancelUnsettled(now_ns=2 * NS, order_id=order_id))
        raise RuntimeError("persist failed")

    store = WalletStateStore(tmp_path / "w.db")
    with pytest.raises(RuntimeError, match="persist failed"):
        apply_and_persist(
            store=store,
            core=core,
            identity=SessionIdentity(
                session_id="s",
                key=CoreSessionKey(
                    condition_id="s",
                    game="dota",
                    yes_token=YES_TOKEN,
                    no_token=NO_TOKEN,
                    yes_is_radiant=True,
                ),
            ),
            apply=boom,
        )
    store.close()
    restored = next(order for order in core.state.orders if order.order_id == order_id)
    assert restored.status == "canceling"
    assert core._buy_cancel_timing[order_id].started_ns == NS
    assert memory.buy_cancel_clocks[0].started_ns == NS


def test_restored_gone_buy_reports_wait_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="trader.session_core")
    store = WalletStateStore(tmp_path / "w.db")
    core = _core()
    core.session_id = "0xcond"
    core._state = replace(core.state, orders=(_resting("gone"),))
    core.bind_venue(core_id="c0", venue_id="v1")
    persist_core_snapshot(
        store=store,
        core=core,
        identity=SessionIdentity(
            session_id="0xcond",
            key=CoreSessionKey(
                condition_id="0xcond",
                game="dota",
                yes_token=YES_TOKEN,
                no_token=NO_TOKEN,
                yes_is_radiant=True,
            ),
        ),
    )
    fresh = _core()
    load_core_snapshot(store=store, core=fresh, session_id="0xcond")
    fresh.apply(BuySettled(now_ns=4 * NS, order_id="c0", matched_qty=0.0))
    assert _buy_lines(caplog, "trader core buy settled") == [
        "trader core buy settled id=c0 wait_ms=unavailable origin=restored cause=buy_settled"
    ]
    store.close()
