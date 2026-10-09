"""backtest.run plus a read-only actor that logs the matching engine's queue state.

The actor trades nothing. It reads the engine's private queue maps through the
Cython probe (nt_queue_probe) and writes four JSONL files next to PROBE_OUT:

- *_accepts.jsonl: per OrderAccepted, the book level at the order price and the
  result of the same book query the engine's queue snapshot runs right after.
- *_queue.jsonl: every change of an order's queue-ahead value, with the data
  event that caused it (data callbacks run after the matching engine processed
  that item; a new entry appears after the accept that created it).
- *_trades.jsonl: every trade tick the engine saw.
- *_fills.jsonl: per OrderFilled, the queue state at fill time, the trade in
  flight (set only when a trade tick triggered the fill) and the data event
  that triggered it.

Build the probe once: cd sim/queue_probe && uv run --project <esports-trader> \
  --group backtest --with cython --with setuptools python setup_probe.py

usage (from esports-trader): PYTHONPATH=src:../prediction-market-backtesting:<this dir> \
       PROBE_OUT=<prefix>.jsonl TELONEX_TREE_CACHE=<scratch> TELONEX_CACHE_ROOT=<scratch> \
       .venv/bin/python <this dir>/run_probe.py <backtest.run args>
"""

import json
import os
import sys

import nt_queue_probe
from backtest import run
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.enums import OrderSide
from prediction_market_extensions.backtesting import _prediction_market_backtest as pmb

PREFIX = os.environ["PROBE_OUT"].removesuffix(".jsonl")
ACCEPTS = open(f"{PREFIX}_accepts.jsonl", "w")
QUEUE = open(f"{PREFIX}_queue.jsonl", "w")
FILLS = open(f"{PREFIX}_fills.jsonl", "w")
TRADES = open(f"{PREFIX}_trades.jsonl", "w")
SCALE = 10**16  # raw fixed-point on this build (Price 0.56 -> 5.6e15)


def _levels(levels, depth: int) -> list[tuple[float, float]]:
    return [(float(level.price), float(level.size())) for level in levels[:depth]]


def _deltas_summary(deltas) -> dict:
    return {
        "kind": "deltas",
        "ts": deltas.ts_event,
        "changes": [
            [int(d.action), int(d.order.side), float(d.order.price), d.order.price.raw,
             float(d.order.size), int(d.flags)]
            for d in deltas.deltas
        ],
    }


def _trade_summary(tick) -> dict:
    return {"kind": "trade", "ts": tick.ts_event, "px": float(tick.price), "px_raw": tick.price.raw,
            "size": float(tick.size), "aggressor": int(tick.aggressor_side)}


class QueueProbe(Actor):
    def __init__(self, engine) -> None:
        super().__init__(ActorConfig(component_id="QueueProbe"))
        self._engine = engine
        self._last_ahead: dict[str, dict[str, list[float]]] = {}
        self._pending_fills: dict[str, list[dict]] = {}

    def on_start(self) -> None:
        for instrument in self.cache.instruments():
            self.subscribe_order_book_deltas(instrument.id)
            self.subscribe_trade_ticks(instrument.id)
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)

    def _matching(self, instrument_id):
        exchange = nt_queue_probe.exchange(self._engine, instrument_id.venue)
        return exchange.get_matching_engine(instrument_id)

    def _after_data(self, instrument_id, summary: dict) -> None:
        iid = str(instrument_id)
        ahead, _excess, _pending, _trade = nt_queue_probe.queue_state(self._matching(instrument_id))
        now = {cid: [px / SCALE, qty / SCALE] for cid, (px, qty) in ahead.items()}
        before = self._last_ahead.get(iid, {})
        for cid in now.keys() | before.keys():
            old, new = before.get(cid), now.get(cid)
            if old != new:
                QUEUE.write(json.dumps({"cid": cid, "iid": iid, "old": old, "new": new,
                                        "event": summary}) + "\n")
        self._last_ahead[iid] = now
        for row in self._pending_fills.pop(iid, []):
            row["trigger"] = summary
            FILLS.write(json.dumps(row) + "\n")

    def on_order_book_deltas(self, deltas) -> None:
        self._after_data(deltas.instrument_id, _deltas_summary(deltas))

    def on_trade_tick(self, tick) -> None:
        summary = _trade_summary(tick)
        TRADES.write(json.dumps({"iid": str(tick.instrument_id), **summary}) + "\n")
        self._after_data(tick.instrument_id, summary)

    def _on_order_event(self, event) -> None:
        name = type(event).__name__
        if name == "OrderAccepted":
            self._accept_row(event)
        elif name == "OrderFilled":
            self._fill_row(event)

    def _accept_row(self, event) -> None:
        order = self.cache.order(event.client_order_id)
        instrument = self.cache.instrument(event.instrument_id)
        book = self._matching(event.instrument_id).get_book()
        query_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
        engine_query = book.get_quantity_at_level(order.price, query_side, instrument.size_precision)
        levels = book.bids() if order.side == OrderSide.BUY else book.asks()
        same = [lv for lv in levels if float(lv.price) == float(order.price)]
        ACCEPTS.write(json.dumps({
            "cid": str(order.client_order_id), "iid": str(event.instrument_id), "ts": event.ts_event,
            "side": order.side_string(), "px": float(order.price), "order_px_raw": order.price.raw,
            "level_px_raw": same[0].price.raw if same else None,
            "level_size": float(same[0].size()) if same else 0.0,
            "engine_query": float(engine_query),
            "bids": _levels(book.bids(), 4), "asks": _levels(book.asks(), 4),
        }) + "\n")

    def _fill_row(self, event) -> None:
        cid = str(event.client_order_id)
        matching = self._matching(event.instrument_id)
        ahead, excess, pending, trade = nt_queue_probe.queue_state(matching)
        book = matching.get_book()
        order = self.cache.order(event.client_order_id)
        row = {
            "cid": cid, "iid": str(event.instrument_id), "ts": event.ts_event,
            "last_qty": float(event.last_qty), "last_px": float(event.last_px),
            "order_px": float(order.price), "order_init_ts": order.ts_init,
            "trade_in_flight": trade,
            "queue_ahead": None if cid not in ahead else [ahead[cid][0] / SCALE, ahead[cid][1] / SCALE],
            "queue_excess": None if cid not in excess else excess[cid] / SCALE,
            "queue_pending": cid in pending,
            "bids": _levels(book.bids(), 4), "asks": _levels(book.asks(), 4),
        }
        self._pending_fills.setdefault(str(event.instrument_id), []).append(row)


_original_build_engine = pmb.PredictionMarketBacktest._build_engine


def _build_engine_with_probe(self):
    engine = _original_build_engine(self)
    engine.add_actor(QueueProbe(engine))
    return engine


pmb.PredictionMarketBacktest._build_engine = _build_engine_with_probe

if __name__ == "__main__":
    sys.argv[0] = "backtest.run"
    try:
        run.main()
    finally:
        for handle in (ACCEPTS, QUEUE, FILLS, TRADES):
            handle.close()
