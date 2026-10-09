"""Replay wallet B's real orders through the Nautilus matching engine, no strategy logic.

JournalReplayStrategy keeps the two-sided adapter's order plumbing and fill
records but makes no decisions: it never wakes the core. It reads the orders
for its match from the JSON file in REPLAY_ORDERS and, for each one, submits a
post-only GTC BUY so the venue accepts it at the live exchange PLACEMENT time
(submit = placed - order latency) and cancels it at the live exchange end time
(engine cancel latency is 0). The core does not own these orders: their fills
pend as unknown and a venue cancel only drops the order. It logs client order
id -> live order id to REPLAY_LOG.
"""

import json
import os
from typing import Any

from nautilus_trader.common.events import TimeEvent
from nautilus_trader.model.events import OrderAccepted

from backtest.two_sided_core import TwoSidedCoreStrategy
from strategy.types import PlaceOrder

SUBMIT = "REPLAY_SUBMIT:"
CANCEL = "REPLAY_CANCEL:"


class JournalReplayStrategy(TwoSidedCoreStrategy):
    def on_start(self) -> None:
        for instrument_id in self._config.instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise ValueError(f"instrument {instrument_id} is not loaded")
            self._instruments[instrument_id] = instrument
            self.subscribe_order_book_deltas(instrument_id)
        orders = json.loads(open(os.environ["REPLAY_ORDERS"]).read()).get(str(self._config.match_id), [])
        self._replay: dict[str, dict[str, Any]] = {}
        self._cid_of: dict[str, str] = {}
        self._replay_log = open(os.environ["REPLAY_LOG"], "a")
        now = self.clock.timestamp_ns()
        skipped = 0
        for order in orders:
            index = self._token_index_for(order["token_id"])
            submit_ns = order["placed_ns"] - self._config.order_latency_ns
            if index is None or submit_ns <= now:
                skipped += 1
                continue
            self._replay[order["order_id"]] = {**order, "token_index": index}
            self.clock.set_time_alert_ns(f"{SUBMIT}{order['order_id']}", submit_ns, self._on_submit)
            if order["end_ns"] is not None:
                self.clock.set_time_alert_ns(f"{CANCEL}{order['order_id']}", order["end_ns"], self._on_cancel)
        self._write({"kind": "start", "match_id": self._config.match_id, "now": now,
                     "scheduled": len(self._replay), "skipped": skipped})

    def _token_index_for(self, token_id: str) -> int | None:
        for index, instrument_id in enumerate(self._config.instrument_ids):
            if str(instrument_id).endswith(f"-{token_id}.POLYMARKET"):
                return index
        return None

    def _write(self, row: dict[str, Any]) -> None:
        self._replay_log.write(json.dumps(row) + "\n")
        self._replay_log.flush()

    def _on_submit(self, event: TimeEvent) -> None:
        live_id = event.name.removeprefix(SUBMIT)
        order = self._replay[live_id]
        place = PlaceOrder(
            order_id=f"replay:{live_id}",
            episode_id=0,
            token_index=order["token_index"],
            side="BUY",
            price=order["price"],
            quantity=order["size"],
            level_index=None,
            reduce_only=False,
        )
        self._submit_place(now_ns=int(event.ts_event), place=place)
        cid = self._core_to_venue[place.order_id]
        self._cid_of[live_id] = cid
        self._write({"kind": "submit", "match_id": self._config.match_id, "cid": cid,
                     "live_id": live_id, "ts": int(event.ts_event)})

    def _on_cancel(self, event: TimeEvent) -> None:
        cid = self._cid_of.get(event.name.removeprefix(CANCEL))
        live = None if cid is None else self._live.get(cid)
        if live is not None and not live.order.is_closed:
            self.cancel_order(live.order)

    def on_order_accepted(self, event: OrderAccepted) -> None:
        cid = str(event.client_order_id)
        live = self._live.get(cid)
        if live is not None:
            live.accepted = True
        self._record_order_lifecycle(kind="accepted", event_ts_ns=int(event.ts_event), reason="", client_order_id=cid)

    def _ack_venue_cancel(self, *, now_ns: int, client_order_id: str) -> None:
        self._detach_live(client_order_id)

    def _evaluate(self, *, now_ns: int) -> None:
        return

    def on_stop(self) -> None:
        self._replay_log.close()
