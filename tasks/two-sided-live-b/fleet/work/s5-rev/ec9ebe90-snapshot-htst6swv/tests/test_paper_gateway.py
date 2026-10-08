"""Offline tests for PaperGateway: paper lifecycle, fill simulation, engine seam.

Everything runs against the real editable fork types (Config/PathsConfig/
Secrets, MarketMeta/TokenMeta/Quote/Side/Fill, StateStore, OrderBook,
TradePrint) with temporary SQLite files and in-memory books. No Engine.start,
no websocket, no HTTP, no Docker. The only fork monkeypatch is the one
US-011 itself applies: polymaker.engine.ExecutionGateway -> PaperGateway.
"""

# The engine seam test deliberately binds engine._on_fill — the one narrow
# private boundary US-011 composes. Production paper_gateway never touches it.
# pyright: reportPrivateUsage=false

import asyncio
import inspect
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from polymaker.config import Config, PathsConfig, Secrets, StrategyProfile
from polymaker.domain import (
    Fill,
    MarketMeta,
    OpenOrder,
    OrderState,
    Quote,
    Side,
    TokenMeta,
)
from polymaker.engine import Engine
from polymaker.execution.gateway import ExecutionGateway
from polymaker.journal import Journal
from polymaker.marketdata.orderbook import OrderBook
from polymaker.marketdata.parse import TradePrint
from polymaker.state.store import StateStore

from trader.paper_gateway import PaperGateway

ORDER_ID_PATTERN = r"paper-[0-9a-f]{32}-\d+"


def make_meta(tick_size: float = 0.01) -> MarketMeta:
    """A minimal real MarketMeta for the two test tokens."""
    return MarketMeta(
        condition_id="cond-1",
        question="who wins",
        slug="who-wins",
        tokens=(TokenMeta("yes-1", "Yes"), TokenMeta("no-1", "No")),
        tick_size=tick_size,
        neg_risk=False,
        min_order_size=1.0,
        rewards_min_size=1.0,
        rewards_max_spread=0.03,
        rewards_daily_rate=0.0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=None,
        event_id=None,
    )


def make_book(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts: float
) -> OrderBook:
    """One in-memory book with a real apply_snapshot."""
    book = OrderBook(tick_size=0.01)
    book.apply_snapshot(bids, asks, ts, "hash")
    return book


def place(gateway: ExecutionGateway, meta: MarketMeta, quote: Quote) -> OpenOrder:
    """Place one quote and return its fabricated LIVE order."""
    placed = asyncio.run(gateway.place([quote], meta))
    assert len(placed) == 1
    return placed[0]


def make_trade(price: float, size: float, ts: float, aggressor: Side = Side.BUY) -> TradePrint:
    """One real TradePrint on the yes token."""
    return TradePrint(
        asset_id="yes-1", condition_id="cond-1", aggressor=aggressor, price=price, size=size, ts=ts
    )


def count_fill_rows(db_path: Path) -> int:
    """The number of durable fill rows in the persistent fills table."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        connection.close()


class FillRecorder:
    """Synchronous stand-in for engine._on_fill recording every callback."""

    def __init__(self) -> None:
        self.fills: list[Fill] = []

    def __call__(self, fill: Fill) -> None:
        self.fills.append(fill)


class PaperBed:
    """One offline gateway with its engine-owned store, journal and fill recorder."""

    def __init__(self, tmp_path: Path, *, on_fill: Callable[[Fill], None] | None = None) -> None:
        self.db_path = tmp_path / "state.db"
        self.cfg = Config(
            paths=PathsConfig(db=str(self.db_path), journal_dir=str(tmp_path / "journal"))
        )
        self.journal = Journal(self.cfg.paths.journal_dir, enabled=True, day="paper")
        self.state = StateStore(self.db_path)
        self.gateway = PaperGateway(self.cfg, self.journal, paper=True)
        self.recorder = FillRecorder()
        self.gateway.bind_fill_sink(self.state, on_fill or self.recorder)

    def open_order_ids(self) -> list[str]:
        """The live order ids reported by the gateway snapshot."""
        return [o.order_id for o in asyncio.run(self.gateway.open_orders())]


# ── constructor / no-network boundary ────────────────────────────────────


def test_paper_flag_false_is_rejected_before_any_live_path(tmp_path: Path) -> None:
    """PaperGateway refuses to construct without paper=True."""
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "state.db")))
    with pytest.raises(ValueError, match="paper"):
        PaperGateway(cfg, paper=False)


def test_connect_and_reads_stay_local_even_with_wallet_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake wallet secrets never reach the network: connect and reads are local."""
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "state.db"), journal_dir=str(tmp_path / "journal")),
        secrets=Secrets(PK="0xPRIVATEKEY", BROWSER_ADDRESS="0xWALLET"),
    )
    gateway = PaperGateway(cfg, paper=True)

    def fail_io(*args: object, **kwargs: object) -> object:
        raise AssertionError("blocking network pool must not run")

    monkeypatch.setattr(ExecutionGateway, "_io", fail_io)

    class FailHttpClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("httpx client must not be built")

    monkeypatch.setattr("polymaker.execution.gateway.httpx.AsyncClient", FailHttpClient)

    asyncio.run(gateway.connect())
    assert gateway.address == "0xWALLET"
    assert gateway.funder == "0xWALLET"
    assert asyncio.run(gateway.positions()) == {}
    assert asyncio.run(gateway.token_balance("yes-1")) == 0.0
    assert asyncio.run(gateway.token_balances(["yes-1", "no-1"])) == {}
    assert asyncio.run(gateway.get_book("yes-1")) == {}
    assert asyncio.run(gateway.get_full_book("yes-1")) is None
    assert asyncio.run(gateway.heartbeat()) is True
    assert asyncio.run(gateway.market_order("yes-1", Side.BUY, 5.0, make_meta())) == {"paper": True}
    gateway.close()


def test_bind_is_one_time_and_market_data_requires_it(tmp_path: Path) -> None:
    """Market data before bind fails fast; a second bind fails fast."""
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "state.db"), journal_dir=str(tmp_path / "journal"))
    )
    gateway = PaperGateway(cfg, paper=True)
    state = StateStore(tmp_path / "state.db")
    recorder = FillRecorder()
    crossing_book = make_book([(0.48, 1.0)], [(0.49, 1.0)], 100.0)
    with pytest.raises(RuntimeError, match="bind"):
        gateway.process_book_update("yes-1", crossing_book)
    with pytest.raises(RuntimeError, match="bind"):
        gateway.process_trade_print(make_trade(0.49, 1.0, 100.0))
    gateway.bind_fill_sink(state, recorder)
    with pytest.raises(RuntimeError, match="already bound"):
        gateway.bind_fill_sink(state, recorder)
    gateway.close()


# ── placement / lifecycle / reconcile ────────────────────────────────────


def test_place_three_rungs_does_not_reject_batch(tmp_path: Path) -> None:
    """Three 0.01-step BUY rungs on one token all place; the batch is not rejected."""
    bed = PaperBed(tmp_path)
    meta = make_meta()
    quotes = [
        Quote("yes-1", Side.BUY, 0.50, 130.0),
        Quote("yes-1", Side.BUY, 0.49, 132.65),
        Quote("yes-1", Side.BUY, 0.48, 135.42),
    ]
    placed = asyncio.run(bed.gateway.place(quotes, meta))
    assert len(placed) == 3
    assert [order.price for order in placed] == [0.50, 0.49, 0.48]
    assert all(order.state is OrderState.LIVE for order in placed)
    live = asyncio.run(bed.gateway.open_orders())
    assert len(live) == 3


def test_place_and_lifecycle_two_token_batch(tmp_path: Path) -> None:
    """A valid two-token batch yields unique LIVE orders that survive reconcile."""
    bed = PaperBed(tmp_path)
    meta = make_meta()
    quotes = [
        Quote("yes-1", Side.BUY, 0.50, 10.0),
        Quote("no-1", Side.SELL, 0.50, 5.0),
    ]
    placed = asyncio.run(bed.gateway.place(quotes, meta))
    assert len(placed) == 2
    ids = [o.order_id for o in placed]
    assert len(set(ids)) == 2
    for order in placed:
        assert re.fullmatch(ORDER_ID_PATTERN, order.order_id)
        assert order.state is OrderState.LIVE
    assert {o.token_id for o in placed} == {"yes-1", "no-1"}
    assert all(o.price == 0.50 for o in placed)
    assert {o.size for o in placed} == {10.0, 5.0}

    # the paper branch of the base place journals the outgoing batch
    bed.journal.close()
    journal_text = (tmp_path / "journal" / "paper.jsonl").read_text()
    assert journal_text.count("orders_out") == 1
    assert '"price": 0.5' in journal_text

    live = asyncio.run(bed.gateway.open_orders())
    assert len(live) == 2
    by_id = {o.order_id: o for o in placed}
    for order in live:
        original = by_id[order.order_id]
        assert order is not original  # defensive copies
        assert order.state is OrderState.LIVE
        assert order.token_id == original.token_id
        assert order.price == original.price
        assert order.size == original.size
        assert order.created_ts == original.created_ts

    # the engine's own upsert plus an authoritative reconcile keep them alive
    for order in placed:
        bed.state.upsert_order(order)
    for token_id in ("yes-1", "no-1"):
        bed.state.replace_open_orders(
            token_id, [o for o in live if o.token_id == token_id], grace_s=0.0
        )
    assert len(bed.state.orders_for("yes-1")) == 1
    assert len(bed.state.orders_for("no-1")) == 1

    # cancel by id: unknown and repeated ids are idempotent successes
    assert asyncio.run(bed.gateway.cancel(["unknown-id"])) is True
    assert asyncio.run(bed.gateway.cancel([ids[0]])) is True
    bed.state.remove_order(ids[0])  # the engine does this after a successful cancel
    assert bed.open_order_ids() == [ids[1]]
    assert asyncio.run(bed.gateway.cancel([ids[0]])) is True
    assert bed.open_order_ids() == [ids[1]]

    # cancel_asset removes only that token's orders
    more = asyncio.run(
        bed.gateway.place(
            [Quote("yes-1", Side.BUY, 0.49, 3.0), Quote("no-1", Side.BUY, 0.49, 4.0)], meta
        )
    )
    assert len(more) == 2
    assert asyncio.run(bed.gateway.cancel_asset("yes-1")) is True
    assert [o.token_id for o in asyncio.run(bed.gateway.open_orders())] == ["no-1", "no-1"]

    # cancel_all clears everything
    assert asyncio.run(bed.gateway.cancel_all()) is None
    assert asyncio.run(bed.gateway.open_orders()) == []

    # a full fill clears both the gateway and the engine state orders
    filled = place(bed.gateway, meta, Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(filled)
    fills = bed.gateway.process_book_update("yes-1", make_book([(0.48, 2.0)], [(0.49, 2.0)], 101.0))
    assert len(fills) == 1
    assert asyncio.run(bed.gateway.open_orders()) == []
    assert bed.state.orders_for("yes-1") == []
    assert bed.state.position("yes-1").size == 10.0
    assert bed.recorder.fills == fills


def test_place_rejects_invalid_batch_entirely(tmp_path: Path) -> None:
    """One bad quote rejects the whole batch: no orders, no journal, no position."""
    bed = PaperBed(tmp_path)
    meta = make_meta()
    bad_quotes: list[Quote] = [
        Quote("yes-1", Side.BUY, 0.0, 5.0),  # zero price
        Quote("yes-1", Side.BUY, 1.0, 5.0),  # price at one
        Quote("yes-1", Side.BUY, 0.505, 5.0),  # off the tick grid
        Quote("yes-1", Side.BUY, 0.50, 0.0),  # zero size
        Quote("yes-1", Side.BUY, 0.50, -1.0),  # negative size
        Quote("yes-1", Side.BUY, 0.50, float("nan")),  # nan size
        Quote("yes-1", Side.BUY, float("nan"), 5.0),  # nan price
        Quote("yes-1", Side.BUY, float("inf"), 5.0),  # inf price
        Quote("other-9", Side.BUY, 0.50, 5.0),  # unknown token
        Quote("", Side.BUY, 0.50, 5.0),  # empty token
        Quote("yes-1", cast(Side, "BUY"), 0.50, 5.0),  # wrong side runtime type
    ]
    for bad_quote in bad_quotes:
        placed = asyncio.run(
            bed.gateway.place([Quote("yes-1", Side.BUY, 0.50, 1.0), bad_quote], meta)
        )
        assert placed == []
        assert asyncio.run(bed.gateway.open_orders()) == []
        assert bed.state.position("yes-1").size == 0.0
        assert bed.recorder.fills == []
    bed.journal.close()
    journal_text = (tmp_path / "journal" / "paper.jsonl").read_text()
    assert "orders_out" not in journal_text


def test_place_rejects_bad_tick_size(tmp_path: Path) -> None:
    """A zero, negative or non-finite tick size rejects the batch."""
    bed = PaperBed(tmp_path)
    for tick_size in (0.0, -0.01, float("nan"), float("inf")):
        placed = asyncio.run(
            bed.gateway.place([Quote("yes-1", Side.BUY, 0.50, 5.0)], make_meta(tick_size))
        )
        assert placed == []


# ── strict crossing matrix ───────────────────────────────────────────────


def test_book_crossing_strict_matrix_buy(tmp_path: Path) -> None:
    """BUY at 0.50: ask touch no-fill, ask strictly below fills."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    touch = make_book([(0.49, 2.0)], [(0.50, 2.0)], 100.0)
    assert bed.gateway.process_book_update("yes-1", touch) == []
    assert bed.state.position("yes-1").size == 0.0
    assert bed.open_order_ids() == [order.order_id]
    through = make_book([(0.48, 2.0)], [(0.49, 2.0)], 101.0)
    fills = bed.gateway.process_book_update("yes-1", through)
    assert len(fills) == 1
    assert fills[0].price == 0.50
    assert fills[0].size == 10.0
    assert bed.state.position("yes-1").size == 10.0


def test_book_crossing_strict_matrix_sell(tmp_path: Path) -> None:
    """SELL at 0.50: bid touch no-fill, bid strictly above fills."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 10.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    touch = make_book([(0.50, 2.0)], [(0.51, 2.0)], 100.0)
    assert bed.gateway.process_book_update("yes-1", touch) == []
    assert bed.state.position("yes-1").size == 10.0
    through = make_book([(0.51, 2.0)], [(0.52, 2.0)], 101.0)
    fills = bed.gateway.process_book_update("yes-1", through)
    assert len(fills) == 1
    assert fills[0].price == 0.50
    assert fills[0].size == 10.0
    assert bed.state.position("yes-1").size == 0.0


def test_trade_crossing_strict_matrix_buy(tmp_path: Path) -> None:
    """BUY at 0.50: trade print touch no-fill, print strictly below fills."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    assert bed.gateway.process_trade_print(make_trade(0.50, 2.0, 100.0)) == []
    assert bed.state.position("yes-1").size == 0.0
    assert bed.open_order_ids() == [order.order_id]
    fills = bed.gateway.process_trade_print(make_trade(0.49, 0.25, 101.0))
    assert len(fills) == 1
    assert fills[0].price == 0.50
    assert fills[0].size == 10.0
    assert bed.state.position("yes-1").size == 10.0


def test_trade_crossing_strict_matrix_sell(tmp_path: Path) -> None:
    """SELL at 0.50: trade print touch no-fill, print strictly above fills."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 10.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    assert bed.gateway.process_trade_print(make_trade(0.50, 2.0, 100.0)) == []
    assert bed.state.position("yes-1").size == 10.0
    fills = bed.gateway.process_trade_print(make_trade(0.51, 0.25, 101.0))
    assert len(fills) == 1
    assert fills[0].price == 0.50
    assert fills[0].size == 10.0
    assert bed.state.position("yes-1").size == 0.0


@pytest.mark.parametrize("aggressor", [Side.BUY, Side.SELL])
def test_trade_aggressor_is_not_the_fill_rule(tmp_path: Path, aggressor: Side) -> None:
    """Only the print price decides a fill; aggressor and print size never do."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 10.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    fills = bed.gateway.process_trade_print(make_trade(0.51, 1.0, 100.0, aggressor))
    assert len(fills) == 1
    assert fills[0].size == 10.0
    assert fills[0].price == 0.50


# ── fill payload / full size ─────────────────────────────────────────────


def test_book_fill_payload_is_full_size_maker_at_limit(tmp_path: Path) -> None:
    """Tiny public depth still yields one full-size fill at the limit price."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 100.0))
    bed.state.upsert_order(order)
    book = make_book([(0.48, 0.5)], [(0.49, 1.0)], 1234.5)
    fills = bed.gateway.process_book_update("yes-1", book)
    assert len(fills) == 1  # exactly one fill, never partial
    fill = fills[0]
    assert fill.token_id == "yes-1"
    assert fill.side is Side.BUY
    assert fill.price == 0.50  # the order limit, not the 0.49 source price
    assert fill.size == 100.0  # the full order size, not the public depth
    assert fill.is_maker is True
    assert fill.ts == 1234.5
    assert fill.trade_id == f"{order.order_id}:fill"
    assert bed.state.position("yes-1").size == 100.0
    assert bed.state.position("yes-1").avg_price == 0.50
    assert bed.recorder.fills == [fill]


def test_trade_fill_payload_is_full_size_maker_at_limit(tmp_path: Path) -> None:
    """A tiny trade print still yields one full-size fill at the limit price."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 100.0))
    bed.state.upsert_order(order)
    fills = bed.gateway.process_trade_print(make_trade(0.49, 0.25, 4321.0))
    assert len(fills) == 1
    fill = fills[0]
    assert fill.price == 0.50
    assert fill.size == 100.0
    assert fill.is_maker is True
    assert fill.ts == 4321.0
    assert fill.trade_id == f"{order.order_id}:fill"
    assert bed.state.position("yes-1").size == 100.0


def test_sell_with_insufficient_position_stays_open(tmp_path: Path) -> None:
    """A SELL bigger than the position is never clipped into a short fill."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 3.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    book = make_book([(0.51, 5.0)], [(0.52, 5.0)], 100.0)
    assert bed.gateway.process_book_update("yes-1", book) == []
    assert bed.state.position("yes-1").size == 3.0  # unchanged, not clamped
    assert bed.open_order_ids() == [order.order_id]
    assert bed.recorder.fills == []


def test_sell_with_exact_position_fills_fully(tmp_path: Path) -> None:
    """A SELL exactly matching the position fills to zero."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 10.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    fills = bed.gateway.process_book_update("yes-1", make_book([(0.51, 5.0)], [(0.52, 5.0)], 100.0))
    assert len(fills) == 1
    assert bed.state.position("yes-1").size == 0.0


# ── missing / malformed / stale-cache semantics ──────────────────────────


def test_missing_and_malformed_book_sources_never_fill(tmp_path: Path) -> None:
    """Empty/one-sided/wrong-token/zero/nonfinite book inputs change nothing."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    cases = [
        make_book([], [], 100.0),  # empty book
        make_book([(0.49, 2.0)], [], 100.0),  # one-sided: no ask side for a BUY
        make_book([], [(0.49, 2.0)], 100.0),  # one-sided: no bid side either
        make_book([(0.0, 2.0)], [(0.49, 2.0)], 100.0),  # zero-price bid side
        make_book([(float("inf"), 2.0)], [(0.49, 2.0)], 100.0),  # inf-price bid side
        make_book([(0.49, 2.0)], [(0.49, 2.0)], 0.0),  # zero timestamp
        make_book([(0.49, 2.0)], [(0.49, 2.0)], float("nan")),  # nan timestamp
        make_book([(0.49, 2.0)], [(0.49, 2.0)], float("inf")),  # inf timestamp
    ]
    for book in cases:
        assert bed.gateway.process_book_update("yes-1", book) == []
    wrong_token = make_book([(0.49, 2.0)], [(0.49, 2.0)], 100.0)
    assert bed.gateway.process_book_update("no-1", wrong_token) == []
    zero_price_ask = make_book([(0.48, 2.0)], [(0.0, 2.0)], 100.0)
    assert bed.gateway.process_book_update("yes-1", zero_price_ask) == []
    inf_price_ask = make_book([(0.48, 2.0)], [(float("inf"), 2.0)], 100.0)
    assert bed.gateway.process_book_update("yes-1", inf_price_ask) == []
    zero_size_ask = make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0)
    zero_size_ask.asks[0.45] = 0.0  # a zero-size level becomes the best ask
    assert bed.gateway.process_book_update("yes-1", zero_size_ask) == []
    zero_size_bid = make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0)
    zero_size_bid.bids[0.48] = 0.0  # a zero-size level becomes the best bid
    assert bed.gateway.process_book_update("yes-1", zero_size_bid) == []
    assert bed.state.position("yes-1").size == 0.0
    assert bed.open_order_ids() == [order.order_id]
    assert bed.recorder.fills == []


def test_sell_requires_valid_bid_and_ask_sides(tmp_path: Path) -> None:
    """A SELL never fills when the opposite (ask) side is missing or malformed."""
    bed = PaperBed(tmp_path)
    bed.state.set_position("yes-1", 10.0, 0.45)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.SELL, 0.50, 10.0))
    bed.state.upsert_order(order)
    cases = [
        make_book([(0.51, 2.0)], [], 100.0),  # one-sided: no ask side for a SELL
        make_book([(0.51, 2.0)], [(0.0, 2.0)], 100.0),  # zero-price ask side
        make_book([(0.51, 2.0)], [(1.5, 2.0)], 100.0),  # out-of-range ask price
        make_book([(0.51, 2.0)], [(float("inf"), 2.0)], 100.0),  # inf-price ask side
    ]
    for book in cases:
        assert bed.gateway.process_book_update("yes-1", book) == []
    zero_size_ask = make_book([(0.51, 2.0)], [(0.52, 2.0)], 100.0)
    zero_size_ask.asks[0.51] = 0.0  # a zero-size level becomes the best ask
    assert bed.gateway.process_book_update("yes-1", zero_size_ask) == []
    assert bed.state.position("yes-1").size == 10.0
    assert bed.open_order_ids() == [order.order_id]
    assert bed.recorder.fills == []


def test_malformed_trade_sources_never_fill(tmp_path: Path) -> None:
    """Zero/nonfinite/out-of-range trade fields change nothing."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    bad_trades = [
        make_trade(0.49, 2.0, 0.0),  # zero timestamp
        make_trade(0.49, 2.0, -1.0),  # negative timestamp
        make_trade(0.49, 2.0, float("nan")),  # nan timestamp
        make_trade(0.49, 2.0, float("inf")),  # inf timestamp
        make_trade(0.0, 2.0, 100.0),  # zero price
        make_trade(1.5, 2.0, 100.0),  # out-of-range price
        make_trade(float("nan"), 2.0, 100.0),  # nan price
        make_trade(float("inf"), 2.0, 100.0),  # inf price
        make_trade(0.49, 0.0, 100.0),  # zero size
        make_trade(0.49, -2.0, 100.0),  # negative size
        make_trade(0.49, float("nan"), 100.0),  # nan size
    ]
    for trade in bad_trades:
        assert bed.gateway.process_trade_print(trade) == []
    assert bed.state.position("yes-1").size == 0.0
    assert bed.open_order_ids() == [order.order_id]
    assert bed.recorder.fills == []


def test_old_trade_cannot_fill_later_placed_order(tmp_path: Path) -> None:
    """A print is a one-shot observation: it never fills a later placement."""
    bed = PaperBed(tmp_path)
    assert bed.gateway.process_trade_print(make_trade(0.49, 2.0, 100.0)) == []
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    assert bed.state.position("yes-1").size == 0.0  # placement filled nothing
    assert bed.recorder.fills == []
    # a later non-crossing book update must not re-apply the old print either
    non_crossing = make_book([(0.50, 2.0)], [(0.51, 2.0)], 101.0)
    assert bed.gateway.process_book_update("yes-1", non_crossing) == []
    assert bed.state.position("yes-1").size == 0.0
    assert bed.open_order_ids() == [order.order_id]


# ── idempotency / exception ordering ─────────────────────────────────────


def test_duplicate_crossing_event_applies_once(tmp_path: Path) -> None:
    """The same crossing source twice yields one fill, one row, one position."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    book = make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0)
    first = bed.gateway.process_book_update("yes-1", book)
    assert len(first) == 1
    assert bed.gateway.process_book_update("yes-1", book) == []
    assert len(bed.recorder.fills) == 1
    assert bed.state.position("yes-1").size == 10.0
    assert count_fill_rows(bed.db_path) == 1


def test_two_gateway_instances_fill_independently_on_one_db(tmp_path: Path) -> None:
    """Namespace ids keep two gateways on one persistent DB independent."""
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "state.db"), journal_dir=str(tmp_path / "journal"))
    )
    state_a = StateStore(tmp_path / "state.db")
    gateway_a = PaperGateway(cfg, paper=True)
    recorder_a = FillRecorder()
    gateway_a.bind_fill_sink(state_a, recorder_a)
    order_a = place(gateway_a, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    state_a.upsert_order(order_a)
    fills_a = gateway_a.process_book_update("yes-1", make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0))
    assert len(fills_a) == 1

    state_b = StateStore(tmp_path / "state.db")  # reloads positions from the same DB
    gateway_b = PaperGateway(cfg, paper=True)
    recorder_b = FillRecorder()
    gateway_b.bind_fill_sink(state_b, recorder_b)
    assert state_b.position("yes-1").size == 10.0
    order_b = place(gateway_b, make_meta(), Quote("yes-1", Side.BUY, 0.49, 5.0))
    state_b.upsert_order(order_b)
    assert order_b.order_id != order_a.order_id
    fills_b = gateway_b.process_book_update("yes-1", make_book([(0.47, 2.0)], [(0.48, 2.0)], 200.0))
    assert len(fills_b) == 1
    assert state_b.position("yes-1").size == 15.0
    assert fills_a[0].trade_id != fills_b[0].trade_id
    assert count_fill_rows(tmp_path / "state.db") == 2
    gateway_a.close()
    gateway_b.close()
    state_a.close()
    state_b.close()


def test_apply_fill_failure_keeps_order_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising apply_fill leaves the order in place; a fresh input retries."""
    bed = PaperBed(tmp_path)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    original_apply_fill = bed.state.apply_fill
    failing = [True]

    def flaky_apply_fill(fill: Fill) -> bool:
        if failing[0]:
            raise RuntimeError("sqlite gone")
        return original_apply_fill(fill)

    monkeypatch.setattr(bed.state, "apply_fill", flaky_apply_fill)
    book = make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0)
    with pytest.raises(RuntimeError):
        bed.gateway.process_book_update("yes-1", book)
    assert bed.open_order_ids() == [order.order_id]
    assert len(bed.state.orders_for("yes-1")) == 1
    assert bed.state.position("yes-1").size == 0.0
    assert bed.recorder.fills == []
    failing[0] = False
    fills = bed.gateway.process_book_update("yes-1", book)
    assert len(fills) == 1
    assert bed.recorder.fills == fills
    assert count_fill_rows(bed.db_path) == 1


def test_callback_failure_is_terminal_and_never_retried(tmp_path: Path) -> None:
    """A raising callback leaves a durable position and a terminal order."""

    def bad_fill(fill: Fill) -> None:
        raise ValueError("risk boom")

    bed = PaperBed(tmp_path, on_fill=bad_fill)
    order = place(bed.gateway, make_meta(), Quote("yes-1", Side.BUY, 0.50, 10.0))
    bed.state.upsert_order(order)
    book = make_book([(0.48, 2.0)], [(0.49, 2.0)], 100.0)
    with pytest.raises(ValueError, match="risk boom"):
        bed.gateway.process_book_update("yes-1", book)
    assert bed.state.position("yes-1").size == 10.0  # position durable
    assert bed.open_order_ids() == []  # order terminal in the gateway
    assert bed.state.orders_for("yes-1") == []  # and in the state store
    assert bed.gateway.process_book_update("yes-1", book) == []  # never double-applies
    assert bed.state.position("yes-1").size == 10.0
    assert count_fill_rows(bed.db_path) == 1


# ── the same-event-loop contract ─────────────────────────────────────────


def test_fill_processors_are_synchronous_for_the_engine_loop(tmp_path: Path) -> None:
    """The callback-side process methods never await: no interleave on the loop."""
    bed = PaperBed(tmp_path)
    assert not inspect.iscoroutinefunction(bed.gateway.process_book_update)
    assert not inspect.iscoroutinefunction(bed.gateway.process_trade_print)


# ── the real engine seam ─────────────────────────────────────────────────


def test_engine_seam_uses_paper_gateway_and_fills_durably_before_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The US-011 patch builds a PaperGateway whose fill updates StateStore
    before engine._on_fill runs and moves net cash exactly once."""
    cfg = Config(
        paths=PathsConfig(db=str(tmp_path / "engine.db"), journal_dir=str(tmp_path / "journal"))
    )
    monkeypatch.setattr("polymaker.engine.ExecutionGateway", PaperGateway)
    engine = Engine(cfg, paper=True)
    try:
        gateway = cast(PaperGateway, engine.gateway)
        assert isinstance(gateway, PaperGateway)
        assert gateway.paper is True
        meta = make_meta()
        engine.metas[meta.condition_id] = meta
        engine._token_cid["yes-1"] = meta.condition_id
        engine.est[meta.condition_id] = engine._make_estimators(StrategyProfile())
        gateway.bind_fill_sink(engine.state, engine._on_fill)
        order = place(gateway, meta, Quote("yes-1", Side.BUY, 0.50, 10.0))
        engine.state.upsert_order(order)

        observed_positions: list[float] = []
        original_note_fill = engine.risk.note_fill

        def noting_fill(fill: Fill) -> None:
            observed_positions.append(engine.state.position(fill.token_id).size)
            original_note_fill(fill)

        monkeypatch.setattr(engine.risk, "note_fill", noting_fill)
        book = make_book([(0.48, 1.0)], [(0.49, 1.0)], 1000.0)
        fills = gateway.process_book_update("yes-1", book)
        assert len(fills) == 1
        assert engine.state.position("yes-1").size == 10.0
        assert observed_positions == [10.0]  # durable position before the callback
        assert engine.risk.net_cash == -5.0  # changed exactly once
        assert asyncio.run(gateway.open_orders()) == []
    finally:
        engine.journal.close()
        engine.state.close()
        engine.catalog.close()
        engine.gateway.close()
