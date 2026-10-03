# tails-dev: fill-level forensics of the worst LoL histfix maps
Status: FINAL
## Verdict
The tail losses are a strategy-structure problem amplified by change A (cap 6→9 + new code), not primarily a histfix-model regression. On every worst map the same mechanism repeats: the model emits a small (+0.02..0.06) delta on a drifting side, the strategy accumulates a 3-5k share position by gs ~480, the *game genuinely turns* (deaths/net-worth collapse), the mid falls 30-90c, and the passive exit (resting sell at max(ask, fair)) bleeds down in dust fills over 100-300s at 20-40c below cost. A explains most of the tail increase (worst map -1,056 -> -1,587; incomplete 23->53; every cap-bound loss grew ~1.5x). B (history retrain) changed entry deltas only +-0.003..0.009 but reshuffled *which seeds* bled worst — worse on ns-fox1/drx-fox1/hle1-ns, a wash elsewhere; net ~-984, worst-map ~-500. One amplifier for both: the exit has no time/price bound, so the bleed-out price is queue luck — identical entries return -480..-2,088 across seeds. The model is mid-tracking and blind to throws; the exits fill at the bottom.

## Findings

### F1. Clean attribution: A (cap+code) grew the tails; B (retrain) mostly reshuffled them
Recomputed per-seed totals from results.parquet (engine_pnl):

| run | net s0/s1/s2 | mean | worst maps | incomplete |
|---|---|---|---|---|
| w540lv6 (cap6, old code) | 24,236 / 19,206 / 21,634 | 21,692 | -1,107 / -1,056 / -1,056 | 23 |
| cur-20261003r2 (cap9, new code, same w540 model) | 27,779 / 21,981 / 21,760 | 23,840 | -1,587 / -1,387 / -1,481 | 53 |
| histfix-20261003r2 (cap9, new code, hist model) | 25,921 / 22,767 / 19,879 | 22,856 | -1,913 / -1,915 / -2,088 | 53 |

- A: +2,148 mean net but worst-map -531 and incomplete +30. B: -984 net, worst-map -501, incomplete unchanged.
- Command: `uv run python - <<EOF` summing `engine_pnl` over `data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_{w540lv6,cur-20261003r2,histfix-20261003r2}/seed*/results.parquet`. Worst/incomplete numbers match the orchestrator's verified values; my mean for cur (23,840) differs from the quoted 28,352 — likely a different aggregation; the pair deltas agree.
- Confidence: **verified**.

### F2. The tail mechanism, verified on 12 maps
Every worst map has the same skeleton (evidence: `map_facts.py` output below; fills.parquet per run):

| map | bought (won?) | buy qty@avg | sell_avg | tok_mid path | pnl w540/cur/hist (worst seed) |
|---|---|---|---|---|---|
| ns-fox1-g3 115548147900684656 | tok1 radiant FOX1 (lost) | 5,368@0.615 | 0.258 | 0.56 -> 0.02 | -245 / -1,172 / **-1,913** (s2 hist -2,088) |
| t1-hle1-g3 115548147900553444 | tok1 HLE / s2 tok0 T1 (lost) | 12,684@0.571 | 0.452 | dip 0.54->0.47, rec. | -530 / -1,481 / **-1,513** |
| tsw-tes-g4 115570934355614573 | tok1 radiant TES (lost) | 5,559@0.580 | 0.268 | 0.34 -> 0.78 -> dump | -901 / -1,387 / **-1,442** |
| t1-drx 116929405286725871 | tok1 radiant DRX (lost) | 4,546@0.556 | 0.251 | 0.55 -> 0.21 | -791 / -1,389 / **-1,318** |
| t1-kt-g2 115548147900553463 | tok0 T1 (lost) | 3,933@0.686 | 0.351 | 0.67 -> 0.34 | -827 / -1,318 / **-1,318** |
| drx-ns-g2 115548147900684663 | tok1 NS (**WON**) | 4,912@0.539 | 0.265 | 0.58 -> 0.22 -> win | -823 / -1,345 / **-1,352** |
| drx-fox1-g1 115548147900750226 | tok1 FOX (lost) | 4,698@0.525 | 0.290 | 0.64 -> 0.29 | -830 / -799 / **-1,242** |
| kc-mkoi-g2 115548681803406329 | tok0 KC (lost) | 2,930@0.649 | 0.279 | 0.60 -> 0.14 | -962 / -1,138 / **-1,084** |
| tlnpir-anb-g2 117171782819927873 | tok0 TLN (lost) | 1,888@0.649 | 0.111 | 0.67 -> 0.04 | -1,056 / -1,080 / **-1,074** |
| hle1-ns 116929405348526328 | tok1 HLE (lost) | 3,583@0.726 | 0.252 | 0.81 -> 0.06 | -818 / -1,587 / **-1,915** |
| bombat-su 117171782819927856 | tok0 BOMBA (**WON**) | 4,402@0.557 | 0.276 | 0.65 -> 0.04 -> win | -839 / -1,243 / **-1,192** |
| dk-bro2-g1 115548128963037564 | tok0 DK (**WON**) | 3,254@0.648 | 0.210 | 0.64 -> 0.17 -> win | -1,107 / -1,309 / **-1,026** |

- Same buys at ~identical prices across runs; what changes is (a) position size (+50% under cap9: $2,700 vs $1,800 cost cap — maxpos in shares ~4,400-4,800 vs ~2,900-3,200), (b) exit fill prices (sell_avg 0.12-0.53 for the same mid path), (c) occasionally which side/episode structure.
- The mid decline is smooth over 100-300s and tracks the game-state collapse (e.g., ns-fox1: radiant nw_adv +5,057@gs984 -> -4,396@end, deaths 1:8 -> 17:12, mid 0.935 -> 0.024). Real throws/comebacks, not thin-book artifacts.
- Confidence: **verified**.

### F3. The model is mid-tracking; delta never anticipates the collapse
On ns-fox1 seed2 ticks (replay_map.py): while radiant mid fell 0.93 -> 0.22 (gs ~1,050-1,279), delta_cur/delta_new stayed +0.01..+0.04; delta only went negative (~-0.01) at mid <= 0.22 — i.e., at the bottom. The features that drive the model (nw_adv +3,271, deaths 1:7 at gs984) still showed the thrown team ahead while the market had already priced the collapse ~100-200s earlier. The strategy therefore *adds* on the way up (deltas stay >0.02 while mid climbs 0.56 -> 0.75) and exits only after the reversal is complete.
- On every map the buys are "delta says cheap on a drifting/rising side"; the market move against the position is the game turning, which the 11s-lagged game-state features cannot see.
- Evidence: `ticks_115548147900684656_seed2.parquet` rows gs1183-1279 (delta +0.033/+0.040 at mid 0.40-0.44, then -0.011 at mid 0.22); fill bursts: hist s0 bought 2,993 shares gs94-230 and 1,415 more gs244-259 at 0.62-0.70 while mid peaked 0.75.
- Confidence: **verified**.

### F4. Exits are unbounded passive asks; the bleed-out price is queue luck
- `src/strategy/quoting.py:467-507` — the exit sell is priced at `max(joined ask, ceil(fair))`: a resting ask that only fills if a taker lifts it, and reprices down as the book falls.
- ns-fox1 seed2, identical 4,402-share exit: cur's order was lifted in a 1,932-share clip @0.44 at gs1279-1280 (sell_avg 0.486, done by gs1280); hist's order got only dust fills and chased the book to 0.06 by gs1462 (sell_avg 0.124). Same tape, same mechanism — different queue position at each reprice.
- Seed variance on *identical entries* is enormous: hist s0 -1,913 / s1 -480 / s2 -2,088 on ns-fox1 (sell_avg 0.258 / 0.533 / 0.124). The tail is partly a fill-model lottery: whether a big taker clears your clip at 0.44 or you bleed to 0.02.
- Confidence: **verified**.

### F5. Whipsaw maps: the exit rule is also wrong in the other direction
On drx-ns, bombat-su, dk-bro2 the strategy bought the **eventual winner** at 0.54-0.65, then sold at 0.20-0.29 during a mid-game dip on delta reversal; the side then recovered to win. So the same "sell when delta flips" logic (a) rides throws to the bottom when delta stays positive too long (F3), and (b) dumps winners at the dip bottom when delta flips during a comeback. Both directions lose because the model's delta does not predict regime changes — it is a small residual on top of the mid.
- Confidence: **verified**.

### F6. Invalid ticks are NOT the driver; but the first cadence tick is always stale in the new policy
- ns-fox1 seed2: 396 cadence ticks -> 395 ok under new policy (1 stale = the first tick, gs76). blg-hle1 seed2: 314 -> 313 ok (same single first-tick stale). Old policy marked both ok.
- `feed_schedules` semantics: the first surviving tick "connects and is stale" — systematic one-tick delay at map open under the new code, no mass invalidation.
- History-feature NaN rate *improved*: new_hist_nan mean 3.8-5.6% vs old 6.8-8.0% (max 60 = all-NaN at map start, as expected). The ±30s pivot finds more anchors than backward-16s.
- Evidence: `ticks_*.parquet` columns new_status/old_status, new_hist_nan/old_hist_nan.
- Confidence: **verified**.

### F7. Boundary-delta chaos: +-0.003 flips whole episodes (blg-hle1 case)
blg-hle1-g1 (115570934355614576) seed2: w540 made +778 (ep0 -82 + ep1 +860 on an open 2,374-share position that resolved a win); cur and hist made -1.27 — a single 2.27-share buy at gs113, then silence for the whole map. Tick replay shows delta_old and delta_cur are ~identical early but the run's decisions sit exactly on the 0.02 gate for tens of minutes (delta -0.005..-0.024 through gs108-175); a +-0.003 model/feature shift decides whether an episode ever opens. Same phenomenon in reverse on ns-fox1: 13 of 396 ticks sit within +-0.005 of the gate where delta_old and delta_cur straddle it.
- Implication: on maps where the true signal is ~0, fills are decided by noise; cap/code/model changes shuffle which maps trade at all. The B retrain's map-level effect is dominated by this lottery, not by systematically different predictions.
- Evidence: `ticks_115570934355614576_seed2.parquet` head rows; `all_episodes.py` ep table for 115570934355614576.
- Confidence: **verified**.

### F8. First divergence w540 -> cur -> hist: delta paths, not tick availability
On ns-fox1 seed2 the first *decision* divergence is at gs ~122-182: the new ±30s pivot produces systematically ~0.005-0.009 *smaller* deltas on the same w540 model (delta_cur < delta_old, e.g. 0.0339 vs 0.0392 @gs182, 0.0210 vs 0.0296 @gs205), yet cur/hist bought MORE — because the cap, not the gate, bounded position. The hist model's own deltas (delta_new) move +-0.003 around delta_cur — enough to shift individual fill ticks and episode merges (e.g., tsw-tes: w540 had 2 episodes gs222-697; cur/hist merged into one gs222-877 because the position never hit 0).
- Confidence: **verified**.

### F9. Pauses, remakes, missing tape: not the mechanism
Audit: only 2/14 target maps have any pause (bombat-su 91.6s, t1-drx 103s); none near the collapse windows. No remake flags; kill-bearing rows are retained before cadence sampling so no death bursts are missing from the tape. skipped_age_rows (~5,000/map) is the prepare-time frame filter, uniform across maps.
- Confidence: **verified**.

### F10. Anecdotal rule counterfactuals (histfix fills, per-episode sim, `rule_sims.py`)
Sum over the 14 target map-seeds (38 losing rows, actual -43,286):

| rule | sim pnl | vs actual |
|---|---|---|
| actual | -43,286 | — |
| cap6 (scale to $1,800) | -33,510 | +23% |
| stop15: cross-sell all when tok_mid < vwap - 0.15 | **-21,793** | **+50%** |
| stop25: same at -0.25 | -31,748 | +27% |
| tstop900: flat by gs900 | -26,866 | +38% |
| noavg: no adds while underwater >5c | -36,805 | +15% |

- stop15 helps on *both* failure modes: throws (ns-fox1 s0 -1,913 -> -493) and whipsaws (drx-ns/bombat-su would exit ~0.39-0.45 vs actual 0.26-0.28). Counterexample: dnf-bro2 gets worse (-233 -> -322, -519 -> -772, -472 -> -667) — locks a dip that partially recovered.
- tstop900 is the second-biggest but is flattered by this adversarial map set (collapses cluster gs1,100-1,500); needs a fleet-wide run.
- cap6 linear scaling understates the real w540 result (-245 vs -1,044 sim on ns-fox1 s0) because cap also changes *exit timing*, not just size.
- Caveat: maps were selected as worst-loss; all percentages overstate the fleet effect. Anecdotal, not a substitute for a rerun.
- Confidence: **speculative** (fills-level counterfactual, real mid path).

## What I ruled out
- **Invalid-tick storms / history-policy data loss**: <0.5% ticks stale on the worst maps; NaN rate *lower* under new policy. Not the tail driver. (F6)
- **Pauses/remakes/missing tape**: 2/14 maps, small and off-window. (F9)
- **Own fills moving the midpoint**: declines are smooth over 100-300s and track deaths/nw_adv collapse across thousands of tape seconds; our ~$2-3k positions can't explain 40-90c moves. strip_own_book unnecessary — the game narrative matches.
- **History features being NaN/extreme on crash ticks**: hist NaN means ~4-6% and *shrunk* vs old policy; deltas on crash ticks are explained by base features (nw_adv, deaths) still showing the leader ahead.
- **A model-side regression as the main cause**: delta_new vs delta_cur differ only +-0.003-0.009; the largest losses come from exposure + exit mechanics (A), with B mostly reshuffling which seeds/maps bleed.
- **The 23->53 incomplete-map jump being a histfix effect**: already 53 in cur-20261003r2 — it is A (cap/code). Verified against results.parquet.

## Proposed experiments
Ranked by expected tail relief per unit cost. "Late-3rd" = PnL on the last third of maps' holding window / late-game maps.

1. **Revert max_position_levels to 6 (or replace with a per-map max loss of ~$600)** — biggest verified lever; A inflated every cap-bound loss ~1.5x while adding no offsetting edge (mean +2,148 came with worst-map -531 and 30 more incomplete).
   - Experiment: rerun the histfix model with `max_position_levels=6`, same seeds/dataset as histfix-20261003r2. Expect worst-map ~-1,000..-1,300, CVaR back toward w540's, incomplete back toward ~23.
   - Watch: worst-map, CVaR, incomplete count, mean net.
   - Cost: 1-line config + one 3-seed backtest.
2. **Hard mid stop vs cost basis**: when holding > $X and tok_mid < position_vwap - 0.15, cross the spread to flat (not a passive ask).
   - Experiment: implement as an exit branch in `quoting.py` behind a flag; rerun histfix config. Sims on the 14 maps: -43.3k -> -21.8k; expect fleet-level smaller but same sign. Also compare stop15 vs stop25 and vs a delta-reversal-triggered aggressive exit (sell at bid-0.01 once exit condition fires instead of resting ask).
   - Watch: worst-map, CVaR, whipsaw maps (drx-ns/bombat-su should *improve*, not worsen — verify), late-3rd.
   - Cost: ~30-line strategy change + one backtest.
3. **Time-in-position cap**: force-flat by gs ~900 or N=400s after last buy. Mechanically bounds exposure through the late-game coinflip zone where every tail loss occurs.
   - Experiment: add to the same flag; rerun. Sim: -43.3k -> -26.9k on target maps. Check on the full map set (not just losers) whether gs900 forfeits late-game winners.
   - Watch: mean net, worst-map, whipsaw maps, late-3rd.
   - Cost: ~10 lines + one backtest.
4. **Bound the exit in time/price**: the bleed-out is a passive ask chasing the book for 100-300s (ns-fox1: 0.46 -> 0.06 over 260s; queue luck decides 0.12 vs 0.49 average). Add a max-exit-duration (~60s) after which the sell crosses.
   - Experiment: flag + rerun; measure sell_avg vs mid-at-exit-start on the tail maps. Expect sell_avg to move from 0.11-0.26 toward 0.35-0.45 on collapse maps.
   - Watch: markout_300s on sells, worst-map, seed-to-seed variance (should shrink — the fill lottery is the tail).
   - Cost: ~20 lines + one backtest.
5. **Model-side, longer-term**: the delta is mid-tracking and blind to throws (stayed +0.03 while mid fell 50c); investigate (a) larger min_abs_delta or delta-scaled sizing so boundary-noise maps don't trade (F7), (b) objective/tempo features that lead nw_adv (towers, baron, item spikes) — the market led game-state by ~100-200s on every collapse.
   - Experiment: ablate min_abs_delta 0.02 -> 0.03 on histfix config; and a feature-importance dump on the 5 throw maps to see if any catalog column moved before the mid.
   - Watch: buy-300s markout, fill count on flat maps, worst-map.
   - Cost: one backtest each; model work is days.
6. **Eval hygiene**: seed variance on identical entries spans -480..-2,088 — the queue fill model injects pure noise into run-to-run comparisons (F4/F7). Any A/B on tails needs >=5-8 seeds or a fixed-seed fill tape.
   - Cost: compute only.

## Scripts
All in `work/tails-dev/`; run from `esports-trader` with `PYTHONPATH=src:scripts uv run python <script>`:

- `find_maps.py` — worst maps per seed + paired diffs w540->hist (precursor; superseded by all_episodes).
- `all_episodes.py <match_id ...>` — episode table (gs window, buy/sell qty+avg, maxpos, open pos, first delta) per map x run x seed for all three runs. Saved: `episodes_all.parquet`.
- `map_facts.py <match_id ...>` — the per-map forensic table in F2 (winner token, side bought, buy/sell avgs, maxpos, tok_mid path) + game-state narrative (nw_adv/deaths/mid at 8 points). Uses `map_meta.parquet`, `map_markets.parquet`, `map_audit.parquet` (built by `map_overview.py`).
- `fill_detail.py <match_id> <seed> <h|c|w>` — burst-level fill grouping + raw last fills for one map/run.
- `replay_map.py <match_id> <seed>` — replays the signal tape: cadence ticks, old vs new HistoryPolicy status, and delta_old/delta_cur/delta_new (w540-old-policy vs w540-new-policy vs hist model). Writes `ticks_<match_id>_seed<seed>.parquet`.
- `rule_sims.py` — per-episode counterfactuals in F10. Writes `rule_sims.parquet`.
- `map_story.py <match_id> <seed>` — merged tick/fill view (precursor; map_facts covers it).
