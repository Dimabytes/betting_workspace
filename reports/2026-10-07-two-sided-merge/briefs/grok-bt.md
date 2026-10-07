# Brief: grok-bt — design the in-repo backtest for a two-sided + merge Dota strategy

Read first: `$R/00-context.md`, `$R/work/orchestrator/explore/backtest.md`, `$R/work/orchestrator/explore/strategy.md`, then the code: `esports-trader/src/backtest/{run.py,strategy.py,postprocess.py,signals.py,context.py,telonex_local.py,results.py,report.py}`, `esports-trader/src/strategy/{engine.py,quoting.py,types.py,policy.py,lifecycle.py,budget.py,kill_gate.py}`, `esports-trader/scripts/run_seeds.sh`, and in `prediction-market-backtesting` the execution/fill model and settlement code the explorer cites (`replay_adapters.py`, `_prediction_market_backtest.py`, `fill_model.py`, `_backtest_runtime.py`, `_result_policies.py`, `strategies/binary_pair_arbitrage.py`). Read-only. Do not write code into the repo; put sketches in `$R/work/grok-bt/`.

Target strategy to support (parameters, not final values):

- Quote maker BUY on both tokens all map long (from horn or −60 s to game end): bid_yes = fair − h − skew, bid_no = (1 − fair) − h + skew, snapped to tick, post-only, 1..3 rungs, size s(p) shares (e.g. $X / p or fixed shares).
- fair = book mid, optionally + our model delta (300 s) × k, optionally microprice.
- skew = g × net_inventory (shares or $); pull the adding side when |net| > N_max; optional taker flatten when |net| > N_taker (IOC BUY of the short side at ask, pays fee).
- merge: whenever min(yes,no) >= M shares: cash += M, yes −= M, no −= M (deterministic accounting; on-chain costs ignored or a constant). Leftover at game end settles by winner as today.
- Gates we already have: kill gate (no bid on victim token for 10 s), mid-spike, stale feed. Price band maybe [0.03, 0.97].

Deliver a design + file-by-file plan:

1. **Where the strategy plugs in.** `STRATEGY_PATH` in `run.py:231`; how to add a second strategy class/config and select it by CLI flag without touching Follow300. Can `DotaMakerStrategy` be subclassed, or is a new `Strategy` + a new kernel (`strategy/engine.step` analogue) cleaner? Which kernel types (`StrategyState`, `TokenInventory`, plans/intents) can be reused as-is?
2. **Dual inventory and merge accounting.** Exactly how the Nautilus venue tracks positions for the two instruments (NETTING per instrument), what cash account sees; how to represent a merge: (a) synthetic: adjust our own ledger and let both positions settle (winner pays 1, loser 0: a pair pays exactly 1, so merge-as-accounting equals hold-to-settlement in PnL, only capital timing differs) — verify this identity in the engine's settlement code and in `postprocess.py`; (b) if we want capital-timing realism, how to inject a cash credit and position reduction (is there an API to submit a synthetic fill/closing order at price 1.0 / 0.0?). Recommend one and say what the report needs (`report_capital.py`: capital = peak unreturned cost).
3. **Fill model correctness for two-sided quoting.** Queue model: does it handle resting orders on both instruments simultaneously, and does an on-chain fill on YES at p also consume NO at 1−p (mirrored prints)? Check how `onchain_fills` mirrored rows are fed (`telonex_local.py`, `strip_own_book.py`) — risk of double-counting a fill on both tokens. Insert/cancel latency constants. Whether the venue allows a BUY NO that crosses the YES book (it must be post-only; how is post-only rejection simulated?).
4. **Taker flatten.** How to submit an IOC/market BUY in this engine and what fee the postprocess attaches (`is_maker=False` path). Slippage model used.
5. **Signals.** How to run without the model (pure book MM) and with the model delta as fair input, using the existing `MatchSignals` plumbing; what the feed schedule (`feed_schedules.py`, live cadence) does for a strategy that quotes the whole map.
6. **Time range.** Current replay starts at −60 s and BUY cutoff 480 s; where to lift the cutoff, how game end is detected (`_on_game_end`), what happens to resting orders at settlement.
7. **Reporting.** What `summary.json` / two-column report fields need adding (merge count/$, peak capital, taker fee, pairs merged, leftover settled) and where (`postprocess.py`, `report.py`, `report_capital.py`).
8. **Runtime plan.** How to run on 20 maps (`--limit`, `seed0_replay.py`, shard flags), expected wall time, memory; a parameter-grid plan (h ∈ {1,2,3} ticks, g, N_max, M, with/without model) and how `scripts/run_seeds.sh` can run it.
9. **Effort.** List every file to touch with estimated lines, order of work, and what to test with one runnable check (the project rule: one assert-based self-check per non-trivial logic). Flag anything that would need a poly-maker change (not allowed) or a prediction-market-backtesting change (allowed but costly).

Also sanity-check the identity "merge PnL = settlement PnL" numerically on one finished Dota map from `data/backtests/dota_maker/LIVE/.../fills.parquet` if the data lets you (both-token fills on one map are rare under Follow300; if none, state so).

Deliverable `$R/reports/grok-bt.md`: design decisions with file:line, the file-by-file plan, open risks. FINAL within ~2 h.
