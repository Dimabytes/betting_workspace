# Control B gap: grok-sim vs engine on the same 16 maps

Status: FINAL (orchestrator). Code: `work/orchestrator/control_b_gap.py`, `control_b_ablate.py`; sim ablation flags in `work/grok-sim/sim.py` (all default off, grid reproduces).
Engine run: `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_twosided-control-b/seed0` (h=3, g=2e-4, N=100, s=20, k=0, kill gate off, mid-spike off, band 0.90, debounce 100 ms).

## Headline

| | PnL | bought | fills | c/$ | pair margin / pair | P(pair<0) | leftover PnL |
|---|---|---|---|---|---|---|---|
| grok-sim base | +261.7 | $6,757 | 812 | 3.87 | 3.8 t | 27% | −15.4 |
| grok-sim, Nautilus fill semantics only | +105.2 | $4,518 | 798 | 2.33 | 2.5 t | 34% | −15.5 |
| grok-sim, all five engine behaviours | +78.1 | $2,752 | 544 | 2.84 | 4.8 t | 25% | −58.6 |
| engine (control B) | +27.2 (14.2 + 13.0 rebate) | $3,942 | 670 | 0.69 | 1.8 t | 37% | −53.2 |

PnL = pair margin (FIFO, merge at 20) + leftover settlement + maker rebate; the FIFO replay reproduces engine_pnl + rebate to the cent.

## What explains what (sim ablations, 16 maps)

| behaviour mimicked in the sim | PnL | bought | c/$ | effect |
|---|---|---|---|---|
| base | 261.7 | 6,757 | 3.87 | |
| `floor_px` — floor(fair − h) instead of round-to-nearest | 257.0 | 5,187 | 4.95 | −23% volume, same dollars: a half-tick wider quote on odd spreads |
| `fair_pair` — fair from both books | 255.0 | 6,804 | 3.75 | nil |
| `band_hi 0.90` — pull only the leg above 0.90 | 246.0 | 6,307 | 3.90 | leftover −15 → −61: cheap leg keeps buying the loser, nothing to pair with |
| `band 0.90 both legs` | 243.2 | 6,249 | 3.89 | free fix for the above |
| `shrink_add` — adding side ×(1 − \|n\|/N), min 5 | 271.3 | 6,206 | 4.37 | slightly positive |
| `through_cap` — price priority, qty = min(ours, printed below) | 240.0 | 6,197 | 3.87 | the sim's "full fill on through" is only ~$20 optimistic |
| `through_cap`, pessimistic queue | 189.1 | 5,140 | 3.68 | honest lower bound with price priority |
| **`nautilus_fill`** — through-print fills only once displayed queue was eaten by at-price prints, qty = print size | **105.2** | **4,518** | **2.33** | **−60% PnL, the lever** |
| all five | 78.1 | 2,752 | 2.84 | |
| all five, pessimistic | 38.7 | 1,720 | 2.25 | |

62% of the sim's fills (71% of its notional) are through-fills. Nautilus (`queue_position=True`, engine.pyx `determine_trade_fill_qty`) blocks any fill while `ahead > 0` at the order's price, decrements `ahead` only on SELLER prints at exactly that price, zeroes it on a DELETE delta, and caps a fill at the print size. On-chain prints carry whole-second timestamps, so the print arrives before the book DELETE that it caused; the displayed queue also includes size that was cancelled, not traded. Result: a bid that the market traded through is not filled.

## Engine-only behaviours (not in the sim) — the remaining ~$50 and the lower margin per pair

| | value |
|---|---|
| orders accepted / 16 maps | 27,719 (≈1,700 per map) |
| median order life (accept → cancel request) | 0.34 s; p25 0.06 s |
| cancel requested before the order was even accepted | 29% |
| re-submitted at the same price and size after a cancel | 4,111 (15%) |
| ±1-tick reprices | 18,127; 42% revert to the price two orders ago, median 0.56 s apart |
| fair on a half-tick (two-book average) | 55% of submits |
| no-order gap per reprice (cancel 60 ms → ack → submit → accept 175 ms) | median 183 ms |
| total no-order gap | ≈1,000 s per map, both sides (~20% of the window per side) |
| `no_quote` seconds, 16 maps | stale_signal 3,364 (8%, with k=0 the signal is not used), paused 1,076, stale_book 590, pair_tolerance 80, cutoff 1,466 (pre −60 s) |

Each sim fill against the engine's state at that instant (812 fills, $6,757):

| engine state on that side at the sim fill time | n | notional | engine filled same side within ±2 s |
|---|---|---|---|
| no resting order | 327 | $2,853 | 232 |
| order in flight (submitted, not yet accepted) | 114 | $859 | 26 |
| resting at a lower price | 247 | $2,096 | 47 |
| resting at the same price, not filled | 105 | $813 | 45 |
| resting at a higher price | 19 | $137 | 8 |

"No resting order" reasons by the engine's `no_quote` record in that second: none/churn 250, stale_signal 44, pair_tolerance 18, paused 7, cutoff 7, stale_book 1.

Why churn costs margin and not only volume: an order that lives 0.34 s re-enters the queue at the back every time; the fills it does get are the ones where its level is being swept. Pair margin 1.8 t vs 2.5 t for the same fill semantics in the sim; P(pair < 0) 37% vs 34%.

## Nautilus bracket (same quoter, 16 maps, sim)

| fill semantics | PnL | bought | c/$ |
|---|---|---|---|
| Nautilus `queue_position=True` (what control B used) | 105 | 4,518 | 2.33 |
| Nautilus `queue_position=False` (no queue, every print at/through fills min(ours, print)) | 360 | 8,975 | 4.02 |
| price priority + print-size cap (truth-ish) | 240 | 6,197 | 3.87 |
| price priority + cap, last in queue (truth-ish, pessimistic) | 189 | 5,140 | 3.68 |

The two Nautilus settings are one `ExecutionModelConfig(queue_position=...)` flag in `run.py:700`; the truth sits between them. Follow300 on the same 16 maps (seed0, `shared-20261005`, $300 clip): +$2,836 engine_pnl (+$97 rebate) on $20,477 bought, 14.3 c/$, 260 buy fills, 5/16 negative, worst −$469; one map (navi-lgd) is +$2,278 — without it ≈ +$560 on ~$18k ≈ 3 c/$.

## What this means for control B

1. The engine cannot reproduce grok-sim base: Nautilus has no price-priority through-fill. With Nautilus semantics the same quoter earns ~$105 / 2.3 c/$ on these maps in the sim. The engine's target is that row, not $262; the bracket for the truth is $189 (pessimistic, price priority) to $240.
2. Of the engine's shortfall against $105: churn (back of queue, 20% no-order gaps, 29% cancelled in flight), stale-signal pulls at k=0 (8% of time), and the one-sided band (leftover −$46) are engine-side and fixable in esports-trader. floor vs round is a definition, not a bug, but it must match whichever side the control uses.

## Fix list (esports-trader, in order of size; nothing touches poly-maker or the library)

1. Requote discipline in `two_sided_strategy.py`: do not cancel an order that is not yet accepted; reprice only when the desired price differs from the resting one by ≥1 tick for ≥ ~300 ms (or ≥2 ticks at once); drop quantity from `_same_order` or use a ≥5-share tolerance; compute fair from the quoted token's own book (or require both books to agree on the move). Expect: order life seconds not 0.3 s, no-order gaps −80%, same-price resubmits → 0.
2. `_signal`: with `model_k == 0` do not pull bids on a stale model signal (keep `stale_book`; the kill gate is off in the control).
3. Band: pull both legs when either token leaves [0.10, 0.90] (`band_both` in the sim costs nothing vs 0.97; the one-sided version costs the leftover). Fix the plan §4 too.
4. Price definition: either round like the sim for the control, or define h in integer ticks from the best bid and re-run the sim the same way.
5. Through-fill realism experiment (data side, esports-trader `telonex_local.py` on-chain rewrite): order prints inside a second by `log_index` and shift each print to just after the first book delta of that second, so the DELETE of a swept level reaches Nautilus before the print; measure how much of the Nautilus block is a timestamp-ordering artefact. If the engine then lands in $105–$240 on these 16 maps with 1–4 done, control B passes.

Re-run control B after 1–4 with `--band 0.97` (or sim `band_both`) and compare with the sim's `nautilus_fill` row; keep both numbers in the report.
