"""Two live markets share one wallet snapshot; a shortfall never shrinks the other clip."""

# pyright: reportPrivateUsage=false

from dataclasses import replace
from pathlib import Path

import pytest
from polymaker.config import StrategyProfile
from polymaker.domain import Fill, MarketMeta, OpenOrder, Position, Quote, Regime, Side, TokenMeta
from polymaker.marketdata.orderbook import BookView
from polymaker.strategy.quoting import QuoteInputs
from test_trader_session_core import _freshness, _limits, _view

from shared.constants.strategy import (
    LIVE_DOTA_MAX_POSITION_LEVELS,
    LIVE_LOL_MAX_POSITION_LEVELS,
)
from shared.utils.trading import buy_share_quantity
from strategy.lifecycle import apply_cancel_unsettled
from strategy.policy import follow300_policy
from strategy.types import (
    BookPair,
    Budget,
    GameClock,
    RawDeltaSignal,
    RestingOrder,
    Rung,
    SignalUpdate,
)
from trader.core_persistence import (
    CoreCommand,
    insert_unsettled_buy,
    prove_unsettled_buy,
    resolve_settled_buys,
    unsettled_buy_notional,
    upsert_command,
)
from trader.session_budget import budget_from_orders
from trader.session_core import (
    CollateralCache,
    LiveCore,
    PlannedBatch,
    books_from_views,
    quote_cycle,
)
from trader.wallet_store import WalletStateStore

DOTA_CLIP = 65.0
LOL_CLIP = 20.0
JOIN_PRICES = (0.50, 0.49, 0.48)
NOW_NS = 0
AMPLE_CASH = 1_000.0


def _rung_qty(level_usdc: float, price: float) -> float:
    return buy_share_quantity(base_size_usdc=level_usdc, price=price)


def _rung_notional(level_usdc: float, price: float) -> float:
    return price * _rung_qty(level_usdc, price)


def _ladder_notional(level_usdc: float) -> float:
    return sum(_rung_notional(level_usdc, price) for price in JOIN_PRICES)


def _meta(*, condition_id: str, yes: str, no: str) -> MarketMeta:
    return MarketMeta(
        condition_id=condition_id,
        question=condition_id,
        slug=condition_id,
        tokens=(TokenMeta(yes, "Yes"), TokenMeta(no, "No")),
        tick_size=0.01,
        neg_risk=False,
        min_order_size=5.0,
        rewards_min_size=0.0,
        rewards_max_spread=0.0,
        rewards_daily_rate=0.0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=None,
        event_id=None,
    )


def _books() -> BookPair:
    pair = books_from_views(yes=_view(0.50, 0.52), no=_view(0.48, 0.50), ts_ns=NOW_NS)
    assert pair is not None
    return pair


def _inputs(*, condition_id: str, yes: str, no: str, view: BookView) -> QuoteInputs:
    return QuoteInputs(
        meta=_meta(condition_id=condition_id, yes=yes, no=no),
        regime=Regime.QUIET,
        fv=0.51,
        vol_short=0.0,
        toxicity=0.0,
        yes_view=view,
        no_view=view,
        pos_yes=Position(yes, 0.0, 0.0),
        pos_no=Position(no, 0.0, 0.0),
        profile=StrategyProfile(),
        now=0.0,
    )


def _core(*, yes: str, no: str, level_usdc: float, max_position_levels: int) -> LiveCore:
    core = LiveCore(
        policy=follow300_policy(level_usdc=level_usdc, debounce_ms=100, fallback_timer_s=2.0),
        limits=_limits(),
        freshness=_freshness(),
        yes_token=yes,
        no_token=no,
        drop_sell=lambda _quote: False,
        trace=None,
        max_position_levels=max_position_levels,
    )
    core.enqueue(
        SignalUpdate(
            now_ns=NOW_NS,
            signal=RawDeltaSignal(
                predicted_delta=0.05,
                source_received_ns=NOW_NS,
                received_ns=NOW_NS,
                anchor_p=0.51,
                deaths_radiant=0,
                deaths_dire=0,
            ),
        )
    )
    return core


def _quote(
    core: LiveCore,
    inputs: QuoteInputs,
    books: BookPair,
    *,
    store: WalletStateStore,
    cache: CollateralCache,
    cores: tuple[LiveCore, ...],
    account_cap_usdc: float = 1_000_000.0,
) -> PlannedBatch:
    quote_cycle(
        core,
        inputs,
        now_ns=NOW_NS,
        books=books,
        clock=GameClock(now_ns=NOW_NS, game_second=100, paused=False, game_ended=False),
        limits=_limits(),
        store=store,
        cache=cache,
        cores=cores,
        account_cap_usdc=account_cap_usdc,
    )
    planned = core.take_plan()
    assert planned is not None
    return planned


def _buy_quotes(planned: PlannedBatch) -> tuple[Quote, ...]:
    return tuple(item.quote for item in planned.to_place if item.quote.side is Side.BUY)


def _assert_full_clip(quotes: tuple[Quote, ...], level_usdc: float) -> None:
    assert quotes
    for quote in quotes:
        assert quote.size == _rung_qty(level_usdc, quote.price)
        assert quote.price * quote.size == _rung_notional(level_usdc, quote.price)


def _wallet(path: Path, cash: float) -> tuple[WalletStateStore, CollateralCache]:
    return WalletStateStore(path), CollateralCache(value=cash)


def _pair() -> tuple[LiveCore, QuoteInputs, LiveCore, QuoteInputs, BookPair]:
    books = _books()
    view = _view(0.50, 0.52)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    lol = _core(
        yes="LOL_YES",
        no="LOL_NO",
        level_usdc=LOL_CLIP,
        max_position_levels=LIVE_LOL_MAX_POSITION_LEVELS,
    )
    dota_inputs = _inputs(condition_id="dota", yes="DOTA_YES", no="DOTA_NO", view=view)
    lol_inputs = _inputs(condition_id="lol", yes="LOL_YES", no="LOL_NO", view=view)
    return dota, dota_inputs, lol, lol_inputs, books


def test_ample_cash_second_market_sees_first_reserve(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    dota_planned = _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    dota_buys = _buy_quotes(dota_planned)
    assert [quote.price for quote in dota_buys] == list(JOIN_PRICES)
    _assert_full_clip(dota_buys, DOTA_CLIP)
    snapshot = budget_from_orders(
        cache=cache, cores=cores, store=store, quoting=dota, account_cap_usdc=AMPLE_CASH
    )
    assert snapshot.cash_usdc == AMPLE_CASH - dota.reserved_buy_notional()
    assert snapshot.cap_room_usdc == (
        LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - dota.reserved_buy_notional()
    )
    lol_planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=cores)
    lol_buys = _buy_quotes(lol_planned)
    assert [quote.price for quote in lol_buys] == list(JOIN_PRICES)
    _assert_full_clip(lol_buys, LOL_CLIP)
    # The core spends its own placed BUYs from the snapshot. Dota's standing
    # BUY reduces cash and the account room; it does not eat LoL's map room.
    dota_reserved = dota.reserved_buy_notional()
    lol_reserved = lol.reserved_buy_notional()
    budget = lol.state.budget
    assert budget.cash_usdc == pytest.approx(AMPLE_CASH - dota_reserved - lol_reserved)
    assert budget.cap_room_usdc == pytest.approx(
        LIVE_LOL_MAX_POSITION_LEVELS * LOL_CLIP - lol_reserved
    )
    assert budget.account_cap_room_usdc == pytest.approx(1_000_000.0 - dota_reserved - lol_reserved)


def test_each_card_keeps_its_own_rung_cap(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=4,
    )
    lol = _core(
        yes="LOL_YES",
        no="LOL_NO",
        level_usdc=LOL_CLIP,
        max_position_levels=7,
    )
    dota_budget = _budget(store, cache, dota, (dota, lol), AMPLE_CASH)
    lol_budget = _budget(store, cache, lol, (dota, lol), AMPLE_CASH)
    assert dota_budget.cap_room_usdc == pytest.approx(4 * DOTA_CLIP)
    assert lol_budget.cap_room_usdc == pytest.approx(7 * LOL_CLIP)
    store.close()


def test_tight_cash_does_not_resize_lol(tmp_path: Path) -> None:
    leftover_for_one_lol = _rung_notional(LOL_CLIP, JOIN_PRICES[0]) + 1.0
    store, cache = _wallet(tmp_path / "w.db", _ladder_notional(DOTA_CLIP) + leftover_for_one_lol)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    dota_planned = _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    _assert_full_clip(_buy_quotes(dota_planned), DOTA_CLIP)
    assert len(_buy_quotes(dota_planned)) == 3
    lol_planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=cores)
    lol_buys = _buy_quotes(lol_planned)
    assert len(lol_buys) == 1
    _assert_full_clip(lol_buys, LOL_CLIP)
    assert lol.last_block == ""


def test_dota_shortfall_skips_rung_and_reports_no_cash(tmp_path: Path) -> None:
    two_rungs = _rung_notional(DOTA_CLIP, JOIN_PRICES[0]) + _rung_notional(
        DOTA_CLIP, JOIN_PRICES[1]
    )
    store, cache = _wallet(tmp_path / "w.db", two_rungs + 1.0)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    dota_planned = _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    dota_buys = _buy_quotes(dota_planned)
    assert [quote.price for quote in dota_buys] == [0.50, 0.49]
    _assert_full_clip(dota_buys, DOTA_CLIP)
    lol_planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=cores)
    assert _buy_quotes(lol_planned) == ()
    assert lol.last_block == "no_cash"
    empty_store, empty_cache = _wallet(tmp_path / "empty.db", _rung_notional(DOTA_CLIP, 0.50) - 1.0)
    broke, broke_inputs, _, _, books = _pair()
    broke_planned = _quote(
        broke, broke_inputs, books, store=empty_store, cache=empty_cache, cores=(broke,)
    )
    assert _buy_quotes(broke_planned) == ()
    assert broke.last_block == "no_cash"


def test_whichever_quotes_first_reserves_first(tmp_path: Path) -> None:
    leftover_for_one_lol = _rung_notional(LOL_CLIP, JOIN_PRICES[0]) + 1.0
    cash = _ladder_notional(DOTA_CLIP) + leftover_for_one_lol
    store, cache = _wallet(tmp_path / "w.db", cash)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    lol_planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=cores)
    lol_buys = _buy_quotes(lol_planned)
    assert [quote.price for quote in lol_buys] == list(JOIN_PRICES)
    _assert_full_clip(lol_buys, LOL_CLIP)
    dota_planned = _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    dota_buys = _buy_quotes(dota_planned)
    assert len(dota_buys) == 2
    _assert_full_clip(dota_buys, DOTA_CLIP)
    assert [quote.price for quote in dota_buys] == [0.50, 0.49]


def test_map_cap_does_not_spend_the_other_card(tmp_path: Path) -> None:
    """Held cost on Dota leaves LoL its own level cap."""
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    dota_planned = _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    _assert_full_clip(_buy_quotes(dota_planned), DOTA_CLIP)
    store.set_position("DOTA_YES", 660.0, 0.50)
    lol_planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=cores)
    _assert_full_clip(_buy_quotes(lol_planned), LOL_CLIP)


def test_account_cap_blocks_the_second_core(tmp_path: Path) -> None:
    """First card to spend the account cap leaves the next one account_cap, not a shrunk clip."""
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    store.set_position("DOTA_YES", 660.0, 0.50)
    lol_planned = _quote(
        lol, lol_inputs, books, store=store, cache=cache, cores=cores, account_cap_usdc=330.0
    )
    assert _buy_quotes(lol_planned) == ()
    assert lol.last_block == "account_cap"


def test_position_cap_leaves_whole_rungs(tmp_path: Path) -> None:
    """Cap room that fits one rung buys a full clip; the rest stays unplaced."""
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota, dota_inputs, lol, lol_inputs, books = _pair()
    cores = (dota, lol)
    _quote(dota, dota_inputs, books, store=store, cache=cache, cores=cores)
    reserved = dota.reserved_buy_notional()
    held_qty = (LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - reserved - 21.0) / 0.50
    store.set_position("DOTA_YES", held_qty, 0.50)
    lol_planned = _quote(
        lol,
        lol_inputs,
        books,
        store=store,
        cache=cache,
        cores=cores,
        account_cap_usdc=LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP,
    )
    buys = _buy_quotes(lol_planned)
    assert len(buys) == 1
    _assert_full_clip(buys, LOL_CLIP)


def test_durable_inflight_buy_skips_a_rung(tmp_path: Path) -> None:
    inflight = _rung_notional(LOL_CLIP, JOIN_PRICES[0]) + _rung_notional(LOL_CLIP, JOIN_PRICES[1])
    store, cache = _wallet(tmp_path / "w.db", _ladder_notional(LOL_CLIP) + 1.0)
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
            price=0.50,
            quantity=inflight / 0.50,
            venue_id=None,
            order_hash="h",
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        ),
    )
    store._conn.commit()
    _, _, lol, lol_inputs, books = _pair()
    planned = _quote(lol, lol_inputs, books, store=store, cache=cache, cores=(lol,))
    buys = _buy_quotes(planned)
    assert len(buys) == 1
    _assert_full_clip(buys, LOL_CLIP)
    assert buys[0].price == JOIN_PRICES[0]


def _resting(order_id: str, *, price: float, qty: float) -> RestingOrder:
    return RestingOrder(
        order_id=order_id,
        episode_id=1,
        token_index=0,
        side="BUY",
        price=price,
        submitted_qty=qty,
        filled_qty=0.0,
        level_index=0,
        status="live",
        accepted=True,
        partially_filled=False,
        cancel_reason="",
        ack_reason="",
        placed_ns=0,
        accepted_ns=0,
    )


def _budget(
    store: WalletStateStore,
    cache: CollateralCache,
    quoting: LiveCore,
    cores: tuple[LiveCore, ...],
    account_cap_usdc: float,
) -> Budget:
    return budget_from_orders(
        cache=cache,
        cores=cores,
        store=store,
        quoting=quoting,
        account_cap_usdc=account_cap_usdc,
    )


def test_gone_and_durable_row_reserve_once(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    dota.session_id = "dota"
    dota._state = replace(
        dota.state,
        orders=(_resting("c-live", price=0.50, qty=40.0), _resting("c-gone", price=0.50, qty=8.0)),
        rungs=(Rung(index=0, price=0.50, filled_qty=0.0, live_id="c-gone", done=False),),
    )
    dota.bind_venue(core_id="c-live", venue_id="v-live")
    dota.bind_venue(core_id="c-gone", venue_id="v-gone")
    store.upsert_order(OpenOrder("v-gone", "DOTA_YES", Side.BUY, 0.50, 3.0))
    insert_unsettled_buy(
        store._conn,
        venue_id="v-gone",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=8.0,
    )
    dota._state = apply_cancel_unsettled(state=dota.state, order_id="c-gone")
    assert dota.reserved_buy_notional() == pytest.approx(20.0)
    budget = _budget(store, cache, dota, (dota,), AMPLE_CASH)
    assert budget.cash_usdc == pytest.approx(AMPLE_CASH - 24.0)
    assert budget.account_cap_room_usdc == pytest.approx(AMPLE_CASH - 24.0)
    assert budget.cap_room_usdc == pytest.approx(LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - 24.0)
    store.close()


def test_detached_row_reduces_other_map_account_room(tmp_path: Path) -> None:
    path = tmp_path / "w.db"
    store, cache = _wallet(path, AMPLE_CASH)
    insert_unsettled_buy(
        store._conn,
        venue_id="v-dota",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=10.0,
    )
    store._conn.commit()
    _, _, lol, _, _ = _pair()
    lol.session_id = "lol"
    budget = _budget(store, cache, lol, (lol,), AMPLE_CASH)
    assert budget.cash_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert budget.account_cap_room_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert budget.cap_room_usdc == pytest.approx(LIVE_LOL_MAX_POSITION_LEVELS * LOL_CLIP)
    store.close()
    reopened, reopened_cache = _wallet(path, AMPLE_CASH)
    lol_again = _core(
        yes="LOL_YES",
        no="LOL_NO",
        level_usdc=LOL_CLIP,
        max_position_levels=LIVE_LOL_MAX_POSITION_LEVELS,
    )
    lol_again.session_id = "lol"
    again = _budget(reopened, reopened_cache, lol_again, (lol_again,), AMPLE_CASH)
    assert again.cash_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert again.account_cap_room_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert again.cap_room_usdc == pytest.approx(LIVE_LOL_MAX_POSITION_LEVELS * LOL_CLIP)
    reopened.close()


def test_proof_and_ledger_move_reserve_into_held_cost(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    dota.session_id = "dota"
    insert_unsettled_buy(
        store._conn,
        venue_id="v1",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=8.0,
    )
    account_cap = 100.0
    matched = Fill("DOTA_YES", Side.BUY, 0.50, 3.0, "t1:v1", 10.0, is_maker=True)
    store.apply_matched_fill(matched, "t1:v1")
    held = _budget(store, cache, dota, (dota,), account_cap)
    assert held.account_cap_room_usdc == pytest.approx(account_cap - 1.5 - 2.5)
    prove_unsettled_buy(store._conn, "v1", 5.0)
    proved = _budget(store, cache, dota, (dota,), account_cap)
    assert proved.account_cap_room_usdc == pytest.approx(account_cap - 1.5 - 1.0)
    insert_unsettled_buy(
        store._conn,
        venue_id="v-zero",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=8.0,
    )
    parked = _budget(store, cache, dota, (dota,), account_cap)
    assert parked.account_cap_room_usdc == pytest.approx(account_cap - 1.5 - 1.0 - 4.0)
    prove_unsettled_buy(store._conn, "v-zero", 0.0)
    assert resolve_settled_buys(store._conn) == ("v-zero",)
    released = _budget(store, cache, dota, (dota,), account_cap)
    assert released.account_cap_room_usdc == pytest.approx(account_cap - 1.5 - 1.0)
    store.close()


def test_failed_match_restores_reserve_and_confirmed_keeps_held(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    dota.session_id = "dota"
    insert_unsettled_buy(
        store._conn,
        venue_id="v1",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=8.0,
    )
    account_cap = 100.0
    fill = Fill("DOTA_YES", Side.BUY, 0.50, 8.0, "t1:v1", 10.0, is_maker=True)
    store.apply_matched_fill(fill, "t1:v1")
    matched = _budget(store, cache, dota, (dota,), account_cap)
    assert matched.account_cap_room_usdc == pytest.approx(account_cap - 4.0)
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(0.0)
    store.apply_failed_fill(fill, "t1:v1")
    failed = _budget(store, cache, dota, (dota,), account_cap)
    assert failed.account_cap_room_usdc == pytest.approx(account_cap - 4.0)
    assert store.position("DOTA_YES").size == pytest.approx(0.0)
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(4.0)
    again = Fill("DOTA_YES", Side.BUY, 0.50, 8.0, "t2:v1", 12.0, is_maker=True)
    store.apply_confirmed_fill(again, "t2:v1")
    assert resolve_settled_buys(store._conn) == ("v1",)
    confirmed = _budget(store, cache, dota, (dota,), account_cap)
    assert unsettled_buy_notional(store._conn, None) == pytest.approx(0.0)
    assert confirmed.account_cap_room_usdc == pytest.approx(account_cap - 4.0)
    assert confirmed.cap_room_usdc == pytest.approx(LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - 4.0)
    store.close()


def test_session_row_counts_on_its_map_when_the_order_is_gone_from_state(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    dota.session_id = "dota"
    insert_unsettled_buy(
        store._conn,
        venue_id="v-dota",
        session_id="dota",
        token_id="DOTA_YES",
        price=0.50,
        qty=4.0,
    )
    insert_unsettled_buy(
        store._conn,
        venue_id="v-other",
        session_id="other",
        token_id="OTHER",
        price=0.50,
        qty=6.0,
    )
    budget = _budget(store, cache, dota, (dota,), AMPLE_CASH)
    assert dota.state.orders == ()
    assert budget.cash_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert budget.account_cap_room_usdc == pytest.approx(AMPLE_CASH - 5.0)
    assert budget.cap_room_usdc == pytest.approx(LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - 2.0)
    store.close()


def test_empty_unsettled_table_keeps_command_and_orphan_reserve(tmp_path: Path) -> None:
    store, cache = _wallet(tmp_path / "w.db", AMPLE_CASH)
    dota = _core(
        yes="DOTA_YES",
        no="DOTA_NO",
        level_usdc=DOTA_CLIP,
        max_position_levels=LIVE_DOTA_MAX_POSITION_LEVELS,
    )
    dota.session_id = "dota"
    upsert_command(
        store._conn,
        CoreCommand(
            command_id="other:b0:c0",
            session_id="other",
            revision=1,
            batch_id="b0",
            kind="place",
            core_order_id="c0",
            token_index=0,
            side="BUY",
            price=0.50,
            quantity=10.0,
            venue_id=None,
            order_hash="h",
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        ),
    )
    store.upsert_order(OpenOrder("orphan", "DOTA_YES", Side.BUY, 0.50, 4.0))
    store._conn.commit()
    assert unsettled_buy_notional(store._conn, None) == 0.0
    budget = _budget(store, cache, dota, (dota,), AMPLE_CASH)
    assert budget.cash_usdc == pytest.approx(AMPLE_CASH - 7.0)
    assert budget.account_cap_room_usdc == pytest.approx(AMPLE_CASH - 7.0)
    assert budget.cap_room_usdc == pytest.approx(LIVE_DOTA_MAX_POSITION_LEVELS * DOTA_CLIP - 2.0)
    store.close()
