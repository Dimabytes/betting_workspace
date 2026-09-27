# Follow-up brief: fill-grok — how much backtest PnL survives an honest queue

Report: `$R/reports/fill-grok-queue.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.

Orchestrator verified your B4 mechanism in the installed Nautilus
(`esports-trader/.venv/lib/python3.13/site-packages/nautilus_trader/backtest/engine.pyx`):
`process_order_book_delta` calls `_clear_all_queue_positions()` (line ~6903: every resting order's
queue-ahead := 0) on every snapshot flag / CLEAR; `_seed_tob_baseline()` only records the top of book and does
not re-seed queue-ahead. The Telonex loader starts every book snapshot with CLEAR, and the collector writes a
full snapshot per book change. So after the first snapshot following a submit, our order is first in the
queue: the next trade at our price fills us (up to trade size), and a snapshot that touches our price fills us.

## Tasks

1. **How often.** Snapshot cadence per token in LIVE windows (median seconds between snapshots, by era:
   paid Telonex vs collector). Typical time from our submit to the first CLEAR.
2. **Honest-queue replay of the fills (offline, no engine run).** For every fill in LIVE seeds 0–2, rebuild a
   conservative queue: `ahead0` = displayed size at our price when the order was submitted (the fill row's
   `queue_ahead` is the depth stored at submit — confirm in `strategy.py`); then subtract only (a) onchain trade
   volume at our price with the right aggressor side after the submit, and (b) cancels you can infer only if
   the level size drops below `ahead` (never let later ADDs at our price jump ahead of us, never reset).
   Classify each fill: `queue_valid` (enough volume traded through before the fill time), `touch_only` (filled
   because a snapshot moved the opposite touch onto our price with no trade), `early` (a trade filled us before
   the honest queue cleared). Also check price-improving placements: when our resting BUY is above the market's
   best bid (latched rung), a snapshot ask at our price means a seller would have hit us — that one is plausible;
   report it separately from a BUY that sits AT the best bid behind displayed size.
3. **Money.** Engine PnL, BUY turnover, rebate and markout 30/300 s by class and in total, per seed, and by
   signal mode (grid_v1 vs schedule). What is the PnL if only `queue_valid` fills (plus plausible price-improving
   touches) happen? (First-order: drop the invalid fills and their paired exits; say how you handle exits.)
4. Compare with live fill rates (per quoted rung-second, if `quote_events` and live `session.jsonl` let you) as
   a sanity check.

Deliver the numbers with the script. This decides whether the backtest's headline PnL is trustworthy.
