"""The live-core two-sided adapter with wallet B's live inventory instead of the sim's own fills.

LiveNetStrategy keeps every decision of TwoSidedCoreStrategy but sets the core
inventory from the LIVE_NET file before each core sync: B's shares per token as
the live core knew them (journal receipt time of each size_matched increase).
Sim fills still move the order state, but the next sync overwrites the
inventory. Each live fill opens the core's coalescing window at its receipt
time, as a Fill does in the live core. Merges are off: LIVE_NET counts bought
shares and never drops merged pairs, and only the net enters the quote.
"""

import json
import os
from bisect import bisect_right
from dataclasses import replace

from nautilus_trader.common.events import TimeEvent

from backtest.two_sided_core import TwoSidedCoreStrategy
from strategy.types import SignalUpdate, TokenInventory

LIVE_FILL = "LIVE_FILL:"


class LiveNetStrategy(TwoSidedCoreStrategy):
    def on_start(self) -> None:
        super().on_start()
        steps = json.loads(open(os.environ["LIVE_NET"]).read()).get(str(self._config.match_id), [])
        self._net_ts = [row[0] for row in steps]
        self._net_qty = [(row[1], row[2]) for row in steps]
        now = self.clock.timestamp_ns()
        for ts in sorted(set(self._net_ts)):
            if now < ts < self._config.game_end_ns:
                self.clock.set_time_alert_ns(f"{LIVE_FILL}{ts}", ts, self._on_live_fill)

    def _on_live_fill(self, event: TimeEvent) -> None:
        now_ns = int(event.ts_event)
        out = self._drive(event=SignalUpdate(now_ns=now_ns, signal=None))
        self._arm_wake(out.next_wake_ns)

    def _sync_inputs(self, now_ns: int) -> None:
        index = bisect_right(self._net_ts, now_ns) - 1
        radiant, dire = self._net_qty[index] if index >= 0 else (0.0, 0.0)
        radiant_index = self._core.limits.radiant_token_index
        qty = [dire, dire]
        qty[radiant_index] = radiant
        self._core = replace(
            self._core,
            inventory=(
                TokenInventory(token_index=0, qty=qty[0], cost_basis=qty[0] * 0.5, last_buy_ns=None),
                TokenInventory(token_index=1, qty=qty[1], cost_basis=qty[1] * 0.5, last_buy_ns=None),
            ),
        )
        super()._sync_inputs(now_ns)

    def _merge_pairs(self, now_ns: int) -> None:
        return
