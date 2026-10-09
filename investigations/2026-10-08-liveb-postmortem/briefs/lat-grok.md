# Brief: lat-grok — execution speed, pick-offs, stale quotes

## Question

Does our execution speed matter for wallet B? Would a faster stack (Rust, a faster machine, less debounce) have saved money today? How much, in $ per map?

## Task

1. Measure our latency chain from `$D/trader_live_b/wallet/engine_journal/live.jsonl` and the code:
   - decision to exchange PLACEMENT: `orders_out` local `ts` vs `user_order` PLACEMENT `timestamp`;
   - cancel decision to CANCELLATION confirmation (find where cancels are recorded; if the journal lacks cancel requests, say so and use what exists);
   - market move to our decision: for each book change in `$D/book_journal_20261008_liveb.jsonl.gz` that moves the mid ≥ 1 tick, how long until our next `orders_out` for that market. Note the debounce (250 ms), quoter tick, fallback timer (1 s), the 300 ms reprice hold and the 2-tick reprice rule in `src/strategy/two_sided_quoting.py` / `src/strategy/policy.py` / `src/trader/two_sided_worker.py`.
   - Give p50/p90/p99 for each leg. Note the clocks: the collector and trader run on the same VPS, exchange timestamps are Polymarket's clock.
2. Pick-off analysis. For every maker fill of ours (`user_trade` in the engine journal, or `fill` in `session.jsonl`):
   - markout: mid of that token 1 s, 5 s, 30 s, 120 s after the fill, minus the fill price, in $ for the fill size;
   - was our order stale at fill time: had the book already moved so that our live config would have wanted a different price (or a pull) before the fill? For how many ms?
   - group fills by staleness (0–100 ms, 100–500 ms, 0.5–2 s, > 2 s) and sum the markouts per group.
3. Counterfactual: if our total latency were 10 ms, 50 ms, 200 ms (vs measured), which stale fills would not have happened? Sum the $ saved. Be explicit that other traders' speed decides who gets filled; a fill we avoid is only the fills where we were stale longer than the new latency.
4. Who hits us: from `last_trade_price` sizes and timing around our fills, is it one big sweep (an informed taker reacting to a game event) or a slow drift? Report the share of our adverse markout that comes from fills within 2 s of a ≥ 3-tick mid jump.
5. Answer the owner directly: does a Rust rewrite help, yes or no, with the $ number. If the bottleneck is the strategy logic (debounce, holds, timers) and not the language, say which knob and how much it would save.

## Output

Report: `$R/reports/lat-grok.md`. Scripts: `$R/work/lat-grok/`. No long jobs needed beyond streaming the journal.
