# lat-grok — execution speed, pick-offs, stale quotes
Status: FINAL

## Answer (5–10 bullets, numbers in each)

- A Rust rewrite would not have saved today's loss. Insert latency, `orders_out` local time to the exchange PLACEMENT stamp, is p50 78 ms and p90 139 ms on 10,612 of 10,629 placements (minimum 45 ms). I measured that span myself from `live.jsonl`; it is the same 78 ms / ~140 ms figure. The journal line is the start of `place()`, after any cancel HTTP, so it is not the strategy eval. Python is not the 78 ms. The quotes that were actually wrong had been wrong for a median 43 ms, shorter than the fastest place we logged.
- Making the stack 10 ms, 50 ms, or 200 ms end to end, and dropping every fill we had been stale longer than that, does not make money at a 2-minute mark. Saved PnL at 120 s is −$7, −$48, and −$16 (negative means the fills we would have skipped were winners). The 10 ms and 50 ms targets are also unreachable: one place is already 78 ms, and the engine waits another 100 ms before it acts.
- What we can measure of the chain, same VPS clock versus the exchange stamp: the book arrives p50 9.5 ms after the exchange timestamp (p90 22 ms). A 2-tick move in the YES mid that we did replace took p50 295 ms from that arrival until `orders_out`. The exchange then accepted the new order p50 78 ms after `orders_out` (p90 139 ms, p99 712 ms).
- Cancel requests are not in the journal. `gateway.cancel` sends the HTTP call and writes nothing (`poly-maker/src/polymaker/execution/gateway.py:203`). `orders_out` is stamped at the start of the place that runs only after that cancel returns (`gateway.py:150-155`, `engine.py:462-487`). The fastest gap between two `orders_out` on one market today was 173 ms.
- All 523 fills were maker buys. Markout (later mid minus fill price, times size) is −$15 at 1 s, −$47 at 5 s, −$62 at 30 s, −$60 at 120 s. That is about −$6 per map at 2 minutes, on 10 maps. Of the −$60, −$56 is on 209 fills where the live rule still wanted that exact price. Speed does not touch those.
- The 5-second hole looks like a pick-off and then fades. 303 fills had been stale more than 10 ms; skipping them is +$61 at 5 s and −$7 at 120 s. The fast loss comes back.
- The prints that hit us are large. Taker size on our fill's trade has p50 308 shares against our 20. 211 of 523 fills were inside a print of at least 500 shares; those are −$35 of the −$47 at 5 s, and still −$23 at 120 s. Small prints (under 30 shares, 83 fills) are only −$2 at 5 s and −$31 at 120 s: a slow drift, not a sweep.
- 185 fills came within 2 s after the filled token's mid had already jumped at least 3 ticks. Their 120 s markout is −$37, about −$3.7 per map. 65% of the adverse 30 s dollars (the negative fills only) sit within 2 s of such a jump. Pulling quotes after a jump is the rule that lines up with the loss. A faster language does not.

## Method (data, script paths, assumptions)

Script: `work/lat-grok/analyze.py`. Numbers below are from one run of that script, written to `work/lat-grok/summary.json`. Python was `esports-trader/.venv`. Quote math is the live functions `price_bids` and `inventory_skew`.

Data: `work/data/trader_live_b/wallet/engine_journal/live.jsonl` (6,760 `orders_out`, 10,629 placements, 10,154 cancellations, all `user_order.owner` = `4b290f22-…`). Session fills from the 10 match `session.jsonl` files (523 fills, every one `is_maker`). Book: `work/data/book_journal_20261008_liveb.jsonl.gz`, 3,747,720 lines, streamed once (3,680,357 `price_change`, 22,196 `last_trade_price`).

Clocks. `orders_out.ts` is local `time.time()` at the start of `place` (`gateway.py:154`). `user_order.ts` and `user_trade.ts` are the exchange `timestamp`, not local receive (`poly-maker/src/polymaker/userstream/client.py:139`). Session fill `ts_utc` is that same exchange trade time (`esports-trader/src/trader/session_journal.py:425`). Book `receivedAtUs` is the collector on this VPS. Place latency is exchange stamp minus local send. It is never negative here, so the VPS clock is not ahead of the exchange by more than the round trip. Collector receive minus exchange stamp is p50 9.5 ms on price changes; the mean is useless because a fat tail (p99 26 s) is delayed snapshots.

Live cadence is the 100 ms debounce and the 2.0 s fallback, not the 250 ms / 1 s in the context note. `config_b/trading.toml:7-8` under `[engine]` is `debounce_ms = 100` and `quoter_tick_s = 2.0`, at both `f0fe4d33` and `76592d31`. `two_sided_worker.py:104-111` reads that file and passes `debounce_ms` and `fallback_timer_s=quoter_tick_s` into `two_sided_policy`. The engine quoter sleeps that 100 ms and then recomputes (`poly-maker/src/polymaker/engine.py:319-333`). The core is woken with `forced=True` (`esports-trader/src/trader/session_core.py:789`), and a forced wake evaluates immediately (`esports-trader/src/strategy/scheduling.py:161-164`). The strategy's own 100 ms debounce does not add a second wait. The 2.0 s figure is the idle fallback. A 1-tick price gap is held 300 ms; 2 ticks reprice now (`esports-trader/src/strategy/two_sided.py:13-14`, `two_sided_quoting.py:178`).

Stale means: from when the order was placed until the fill, walking both tokens' best bid/ask, the live rule wanted a different price or no bid. Half spread is 3 ticks on `f0fe4d33` maps and 6 on `76592d31` (`git show` of `two_sided.py`). A 1-tick gap starts the 300 ms hold and is not stale until the hold ends. A gap of 2 or more, a band pull (mid outside 0.10–0.90), a pair-sum break, or a leg scaled below 5 shares is stale immediately. Inventory is the last session `signal` `pos_yes` / `pos_no` at that time, in radiant-minus-dire space (`match_worker.py:1198`). Skew at the cap is about half a tick, so it rarely changes the rounded price. Markout is `(mid_later - fill_price) * size` on that token. Three fills have no 30 s mid and 16 have no 120 s mid (book ended).

The counterfactual drops a fill only when that actionable staleness is longer than the hypothetical latency, then scores the markout we would not have taken. A dropped winner counts as a loss. It does not model someone else hitting the new price, and it does not model a fill we would have got later.

## Results (tables)

Insert latency: `orders_out` local `ts` to the exchange PLACEMENT `timestamp`. That is the brief's decision-to-placement span. It starts when `place()` is called, which is after a cancel HTTP when the cycle cancels first. 10,612 matched inside 3 s to the latest same token/price/size `orders_out`. 17 placements unmatched. 67 `orders_out` quotes had no placement (reject, or still open). Recomputed in a second pass over the journal: p50 77.8 ms, p90 139.4 ms, minimum 44.5 ms, none negative.

| leg | n | p50 | p90 | p99 |
|---|---:|---:|---:|---:|
| place HTTP, ms | 10612 | 78 | 139 | 712 |
| book receive − exchange stamp, ms (price changes) | 400000 | 9.5 | 22 | tail, ignore |
| same, last_trade | 22196 | 12 | 35 | tail, ignore |
| YES mid move ≥1 tick → next `orders_out`, s, if one within 30 s | 5721 | 0.56 | 5.9 | 25 |
| same, move of 2 ticks | 1194 | 0.29 | 5.5 | 28 |
| same, move of ≥3 ticks | 616 | 0.30 | 10.5 | 25 |
| gap between `orders_out` on one market, s | 6750 | 1.70 | 10.3 | 40 |

Of YES-mid moves, no new order followed within 30 s for 6,240 / 11,961 one-tick moves (52%), 1,820 / 3,014 two-tick moves (60%), and 1,388 / 2,004 moves of 3 ticks or more (69%). `orders_out` is only written when we place. A move that leaves the rounded bid alone, or that only cancels, is in that silent share. The 295 ms p50 on two-tick moves is the ones we did replace: about 100 ms debounce plus the cancel round trip, then the place stamp. Verified as a reading of those lags, not as a traced span inside one process.

Order age at fill: p50 1.0 s, p90 5.9 s, max 90 s. We are not sitting on one quote for the whole map. We are also not beating a 40 ms taker.

Markout by whether the live rule still wanted the fill price. Dollars are the sum.

| group | fills | shares | 1 s | 5 s | 30 s | 120 s |
|---|---:|---:|---:|---:|---:|---:|
| fresh (rule still wanted it) | 209 | — | +33 | +22 | −33 | −56 |
| stale 0–100 ms | 263 | — | −49 | −65 | −37 | −14 |
| stale 100–500 ms | 31 | — | +0 | −6 | +2 | +9 |
| stale 0.5–2 s | 14 | — | 0 | +1 | +2 | −2 |
| stale >2 s | 6 | — | 0 | +1 | +4 | +4 |
| all | 523 | — | −15 | −47 | −62 | −60 |

Stale-duration histogram, 120 s markout. The short buckets are the pick-off. Past 50 ms the 2-minute markout turns positive: those quotes we were "slow" to move were not the losing ones.

| stale ms | fills | 5 s | 30 s | 120 s |
|---|---:|---:|---:|---:|
| 0–10 | 11 | −8 | −9 | −11 |
| 10–25 | 41 | −11 | −3 | −24 |
| 25–50 | 147 | −35 | −10 | −17 |
| 50–80 | 53 | −10 | −12 | +31 |
| 80–100 | 11 | 0 | −2 | +6 |
| 100–200 | 13 | −6 | −3 | −6 |
| 200–500 | 18 | +1 | +5 | +14 |
| 500–2000 | 14 | +1 | +2 | −2 |
| >2000 | 6 | +1 | +4 | +4 |

Why the quote was stale, and the 120 s markout. `gapN` is N ticks off the price the rule wanted. `gap1_hold` is the 300 ms one-tick hold, 7 fills, +$2 at 120 s. `size` is the heavy leg, which should have been below the 5-share minimum, 17 fills, −$9 at 120 s, median stale 0.5 s. That is the net cap not pulling, and it is small next to the fresh-fill −$56.

| reason | fills | stale p50 ms | 120 s |
|---|---:|---:|---:|
| gap2 | 123 | 37 | −14 |
| gap4 | 30 | 44 | −11 |
| size | 17 | 523 | −9 |
| gap3 | 78 | 37 | +3 |
| gap5 | 25 | 46 | +23 |
| gap1_hold | 7 | 170 | +2 |

Counterfactual. "Saved" is minus the markout of fills whose staleness exceeded the latency. A negative saved number is money we would have given up.

| if cancel landed within | fills skipped | saved at 5 s | saved at 30 s | saved at 120 s |
|---|---:|---:|---:|---:|
| 10 ms | 303 | +61 | +19 | −7 |
| 50 ms | 115 | +15 | +6 | −48 |
| 200 ms | 38 | −2 | −11 | −16 |
| 400 ms | 22 | +1 | −4 | −2 |
| 1000 ms | 14 | +2 | −6 | +1 |

Who hit us. Parent trade size is the taker print in `user_trade`, not our matched size.

| taker size | fills | 5 s | 30 s | 120 s |
|---|---:|---:|---:|---:|
| <30 | 83 | −2 | −15 | −31 |
| 30–100 | 73 | +6 | −14 | +4 |
| 100–500 | 156 | −16 | −18 | −10 |
| ≥500 | 211 | −35 | −15 | −23 |

Mid jumps of at least 3 ticks on the filled token. "Before" means the jump was in the 2 s before the fill, so it is not only the print that filled us.

| set | fills with a 30 s mid | 30 s sum | 120 s sum | share of adverse 30 s dollars |
|---|---:|---:|---:|---:|
| within 2 s of a ≥3-tick jump | 224 | −76 | −39 | 65% (−220 / −340) |
| jump already in the prior 2 s | 194 | −67 | −37 | 58% (−198 / −340) |
| all fills | 520 | −62 | −60 | 100% |

Half spread. The −$81 at 30 s is the six maps on 3 ticks (482 fills). The four maps on 6 ticks (41 fills) are +$19 at 30 s. Too few fills to call the wide quote a fix, and it is not a latency result. Stale-time p50 is 38 ms on the 3-tick maps and 53 ms on the 6-tick maps.

No fill landed while the last game signal was `paused`.

## Recommendations (ranked by expected $ per map, with confidence)

1. Pull both bids for about 2 s after the token mid jumps 3 ticks or more. On this day the fills that already sat in that window lost $37 at 120 s, $3.7 per map, and $67 at 30 s. Confidence medium: one day, 185 fills, and this counts the fills we got, not a full replay of quotes we would have skipped on the way back. It is a rule change, not a faster process. The 5-second sweep and the 2-minute loss are the same set of prints.

2. Do not rewrite the trader in Rust to go faster. Expected save about $0 per map at a 2-minute mark, confidence high. The place floor is 45–78 ms of HTTP. The toxic quotes were stale for ~40 ms. Canceling quotes that had been wrong for 200 ms or more would have dropped winners (−$1.6 per map at 120 s on the 38 fills in that cut).

3. Cutting debounce from 100 ms to 0 is the only latency knob that moves the pipeline, from roughly 170 ms (10 ms feed + 100 ms sleep + 80 ms cancel) toward roughly 90 ms. On these marks that cut does not pay: the newly avoided fills are the 50–200 ms bucket, and that bucket's 120 s markout is positive. Confidence medium. Not worth doing for PnL.

4. The 300 ms one-tick hold is 7 fills and +$2 at 120 s. Leave it. Confidence high that it is not today's loss.

5. The heavy-side size cap left 17 fills on a leg that should have been under 5 shares, −$9 at 120 s, about −$0.9 per map, several of them stale for more than half a second. That is the quoter not pulling a leg it had already decided to drop, not the language. Small dollars. Confidence low on the exact $9 because the signal inventory is a snapshot, not the core's own net at the decision.

## Refuted / open questions

- Refuted: a faster language saves this day's loss. The measured place leg and the sign of the latency counterfactual at 120 s both say no.
- Refuted: the context note's 250 ms debounce and 1 s fallback. Live `config_b` at both commits is 100 ms and 2.0 s, and the forced wake means the strategy debounce does not stack on the engine sleep.
- Refuted: most of the 2-minute loss is stale quotes. −$56 of −$60 is on fills where the rule still wanted that price.
- Open: this markout is not the settlement number. Merges and the unpaired tail are outside this report. The 120 s mid can still move before the map ends. It is the same order of magnitude as the day's −$42 telegram / −$59 cash figure, which is why the 120 s column is the one used for the yes/no.
- Open: a VPS sitting on a 45 ms minimum post is not next to the matching engine. Moving it closer could shave the place leg. It would not, on this sample, turn the latency counterfactual positive at 120 s.
- Open: our own bid is inside the mid the rule uses. A 1-tick mid move sometimes is us. The 3-tick jump cut is large enough that our own 20-share join is unlikely to be the jump.
- Open: the jump-pull was not run as a book replay. It is the markout of the fills that already happened inside the window.
