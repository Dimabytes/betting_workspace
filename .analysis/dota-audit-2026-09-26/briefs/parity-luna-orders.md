# Follow-up brief: parity-luna — order-lifecycle parity on the 49-map cohort (is the backtest over-filling?)

Report: `$R/reports/parity-luna-orders.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.
Context: orchestrator verified that the installed Nautilus engine resets every resting order's queue-ahead to 0
on each book snapshot/CLEAR (`engine.pyx` `_clear_all_queue_positions`, called per snapshot; the Telonex loader
starts every snapshot with CLEAR). fill-grok is doing an offline honest-queue replay of backtest fills
(`reports/fill-grok-queue.md`). You measure the same question from the live side.

On your 49 common GRID maps (and the 5 Oddin ones as a side table):

1. Per BUY order (rung L0/L1/L2), live vs backtest: displayed size at our price when placed (live: book at
   placement from `core_trace`/`session.jsonl` quote rows; backtest: `queue_ahead` at submit), resting time,
   time-to-first-fill, fill probability per resting minute, filled fraction, and whether the fill happened on a
   trade at our price vs the level being swept.
2. Conditional on similar placement (same rung, similar displayed size ahead, similar game second), does the
   backtest fill faster/more often than live? Give a hazard-ratio-style comparison with event-series CIs.
3. Markout 30/300 s of BUY fills by rung and by "queue ahead at placement" bucket, live vs backtest. Front-of-queue
   fills should look less toxic; does the backtest show that signature?
4. SELL side: same for exits (time-to-fill of the exit, price vs mid at fill).
5. Verdict: how much of the +0.51% vs +10.52% gap looks like fill-model optimism vs model version vs exits.
