# Brief: sim-grok — book-replay simulator and strategy sweep

You work independently of `sim-devin` (same task, different model). Do not read its files.

## Task

1. Build a book-replay simulator of the wallet B two-sided strategy from `$D/book_journal_20261008_liveb.jsonl.gz`, for the 10 maps in `00-context.md`. Rebuild each token's book from `book` snapshots plus `price_change` updates. Replay the quote rule from `src/strategy/two_sided.py` and `src/strategy/two_sided_quoting.py` (import it, do not re-type it). Use the same pull reasons you can reconstruct (pause and game end come from `session.jsonl` `signal` rows; band; quote start at game second −60).
2. Fill model. A resting BUY at price p fills when trades print at or through p (`last_trade_price` with side SELL at ≤ p) after the queue ahead of us is consumed. Model the queue: size at that level when we joined, decreased by trades and cancels at that level. Also report a "free queue" variant (fill on first touch). Add an order latency parameter L (place and cancel take effect L ms after the decision); default L = the median you can measure from the engine journal (`orders_out` local ts vs `user_order` PLACEMENT exchange timestamp). State that number.
3. Remove our own orders from the book where you can (our order ids and prices are in `$D/trader_live_b/wallet/engine_journal/live.jsonl`). If you cannot, say how much it biases the queue.
4. Calibrate. Run the live config per map (half spread 3 or 6 as in the table) and compare to the real fills in `session.jsonl`: fill count, shares per side, average price, map PnL. Report the error per map. Do not trust any sweep until calibration is within a stated tolerance; if it is not, say so and explain why.
5. Sweep (each variant vs the live config, per map and total, strict and free queue):
   - half spread 1, 2, 3, 4, 6, 8, 10 ticks;
   - NET_MAX_SHARES 0 (pairs only: after one leg fills, quote only the other leg), 10, 20, 30;
   - stronger skew: SKEW_PER_SHARE ×5, ×10, ×25;
   - hedge-take: when |net| ≥ X shares (X = 5, 10, 20) and has stayed for T seconds (T = 0, 5, 15, 30, 60), buy the missing leg as a taker at the best ask (pay the taker fee 0.05·p·(1−p) per share, walk the book for size). Report what each costs and earns;
   - cancel both bids when the mid moves ≥ K ticks within W seconds (K = 2, 3, 5; W = 1, 3, 10), re-quote after a cooldown of C seconds (C = 5, 15, 30);
   - quote only when the market spread (best ask − best bid) is ≥ S ticks (S = 2, 3, 4);
   - order size 10 and 5 shares instead of 20.
6. For the best 3 variants, show the per-map PnL so the owner sees whether one map drives the total.

## Output

Report: `$R/reports/sim-grok.md`. Scripts and outputs: `$R/work/sim-grok/`. Write a `variants.csv` with one row per (variant, queue mode, map).

You may run long local jobs (hours are fine). No `backtest.run` needed; optional only if you want to validate your simulator on 5–10 historical maps, output under `work/sim-grok/` only.

With 10 maps the sample is small. Always print the per-map spread of each delta, and a paired t over the 10 maps. Call a result real only if it holds in both queue modes.
