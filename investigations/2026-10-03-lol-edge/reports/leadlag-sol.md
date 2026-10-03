# leadlag-sol: LoL market repricing is mostly finished before the 11-second table decision
Status: FINAL
## Verdict (≤10 lines)

1. **Verified:** LoL has no first-mover advantage on the typical observed kill at an 11-second decision lag: half the 120-second signed market response is complete at +4–6 seconds; September is +4 seconds, with 86% complete at +11.
2. **Verified:** Dota retains more event-response headroom in September: kill half-move +8 seconds, 63% complete at +11; the relaxed-window check gives LoL/Dota +5/+8 seconds and 83%/59% complete.
3. **Verified:** The stronger hypothesis that LoL's remaining edge is merely recent-mid momentum fails: about 93% of the published-delta incremental R² and 94% of its incremental MAE gain survive the momentum competitor over the full sample.
4. **Verified:** Earlier execution alone is a small repair: late-period fixed native entries gain 0.170¢ at lag 0, essentially 0 at lag 5; delaying to 30 loses 0.319¢, while substantial residual markout remains.
5. **Likely:** Weak post-event entries and stale valuation relative to the market matter more than missing minute history. In the late period quiet LoL entries mark out 2.111¢ versus 1.641¢ immediately after events; Dota event entries mark out 4.882¢.
6. Prioritize an isolated post-event entry experiment and residual calibration; do not replace the game-state model with a momentum rule or revive shorter horizons without a different hypothesis.

## Findings

### 1. Verified — the main event response arrives before the LoL table decision, and September leaves less headroom

**Method.** Read only the maps in `data/backtests/lol_maker/LIVE/seed0/results.parquet` (1,239 IDs) and Dota LIVE (810 IDs). LoL contributes 1,221 maps with source rows in the inspected early window; Dota contributes all 810. Event game seconds are 60…480 inclusive. For gold, take changes between exact source seconds S−10 and S on a non-overlapping 10-second grid; the pooled top-5% absolute-change thresholds are **327 gold LoL / 418 gold Dota**. For kills use the signed change in `deaths_dire − deaths_radiant`; its absolute 95th percentile on the 10-second grid is 1 in both games, so tied one-kill buckets all qualify. Exact one-second kill onsets are the timing robustness check and the most interpretable timing result. Equal simultaneous deaths have no net direction and are excluded.

Within each map and event type, keep the largest event and suppress other events within 30 game seconds to avoid counting a burst repeatedly. This suppression applies only to event curves; model entry classification uses **every** qualifying event. Align mids by UTC wall timestamps, never by just game seconds. Use as-of per-second prices with a maximum 1.5-second timestamp gap; keep null prices null. The primary study requires all 181 offsets −60…+120 to have finite mids. Each event curve is `sign(event) × [mid(stamp+offset) − mid(stamp−60)]`. The table reports the first crossing of 25/50/75% of the mean +120 value; the curve need not be monotone. CIs use 400 map-cluster bootstrap draws. These are descriptive response curves, not a causal decomposition of the event's effect.

**LoL exact kill onset:**

| Month | Events / maps | 120s cumulative move, ¢ | t25 / t50 / t75, s | t50 map CI, s | Move complete at +11s | Remaining +11→+120, ¢ |
|---|---|---|---|---|---|---|
| ALL | 1023 / 562 | 6.01 | 4 / 5 / 9 | 5…5 | 77.5% | 1.35 |
| 2026-06 | 89 / 44 | 6.61 | 4 / 6 / 22 | 5…9 | 68.4% | 2.09 |
| 2026-07 | 350 / 166 | 5.56 | 4 / 5 / 15 | 5…6 | 73.6% | 1.47 |
| 2026-08 | 341 / 197 | 6.73 | 4 / 5 / 7 | 5…5 | 78.1% | 1.47 |
| 2026-09 | 243 / 155 | 5.42 | 4 / 4 / 6 | 4…5 | 86.1% | 0.75 |

September's +11 completed fraction has map-cluster 95% CI **75.6…100.5%**. The residual signed move after +11 falls from **2.088¢ in June to 0.753¢ in September**. Values over 100% in a bootstrap mean that some resamples overshoot and later reverse, not invalid arithmetic. At the exact kill stamp only 4–7% of the mean −60→+120 move is done; the major repricing happens afterward and before our table decision. Thus the finding is not an artifact of a long pre-event drift.

**LoL gold bucket endpoint:**

| Month | Events / maps | 120s cumulative move, ¢ | t25 / t50 / t75, s | t50 map CI, s | Move complete at +11s | Remaining +11→+120, ¢ |
|---|---|---|---|---|---|---|
| ALL | 952 / 556 | 6.15 | -1 / 2 / 10 | 2…3 | 75.9% | 1.49 |
| 2026-06 | 83 / 45 | 6.84 | 0 / 3 / 17 | 2…5 | 70.7% | 2.00 |
| 2026-07 | 325 / 165 | 5.69 | -1 / 2 / 13 | 1…3 | 73.6% | 1.50 |
| 2026-08 | 310 / 193 | 6.81 | -1 / 3 / 9 | 2…3 | 76.7% | 1.59 |
| 2026-09 | 234 / 153 | 5.68 | -2 / 1 / 5 | 0…2 | 80.0% | 1.14 |

Gold and 10-second kill bucket half-moves are earlier than the exact kill-onset half-moves because the bucket endpoint can be up to 9 seconds after the underlying event. The kill-bucket half-moves Jun/Jul/Aug/Sep are **4/3/2/1s**, and completed fractions at +11 are **68.2/71.6/81.3/82.4%**. Do not interpret a gold bucket's +1-second half-move as one-second reaction to the initial kill.

**Evidence:** run command A under Scripts; `lol_event_summary.csv` and `lol_event_curves.csv`; log `analyze-final.log` prints threshold 327, retained events **952/1,945 gold**, **975/2,026 kill buckets**, **1,023/2,500 exact kill onsets**. The timestamp contract is `src/lol/livestats_frames.py:632` and `:651` (source frame timestamp), `src/lol/05_prepare_dataset.py:270` and `:278` (current at stamp+11, target at stamp+311), `src/shared/constants/lol.py:52`, `src/backtest/signals.py:499` and `:524` (delayed live-style decision).

### 2. Verified — Dota's late response has more headroom, but Dota also misses the first half of most observed kills

**Dota exact kill onset:**

| Month | Events / maps | 120s cumulative move, ¢ | t25 / t50 / t75, s | t50 map CI, s | Move complete at +11s | Remaining +11→+120, ¢ |
|---|---|---|---|---|---|---|
| ALL | 1024 / 385 | 3.01 | 3 / 6 / 16 | 5…8 | 66.0% | 1.02 |
| 2026-07 | 409 / 157 | 2.78 | 3 / 5 / 14 | 4…8 | 70.4% | 0.82 |
| 2026-09 | 397 / 131 | 3.07 | 4 / 8 / 18 | 5…10 | 63.4% | 1.12 |

**Dota gold bucket endpoint:**

| Month | Events / maps | 120s cumulative move, ¢ | t25 / t50 / t75, s | t50 map CI, s | Move complete at +11s | Remaining +11→+120, ¢ |
|---|---|---|---|---|---|---|
| ALL | 448 / 272 | 3.64 | -1 / 4 / 18 | 2…7 | 67.7% | 1.17 |
| 2026-07 | 170 / 109 | 3.59 | 0 / 5 / 14 | 2…9 | 70.1% | 1.07 |
| 2026-09 | 177 / 98 | 3.74 | -1 / 6 / 27 | 2…14 | 61.4% | 1.45 |

September gold residual +11→+120 is **1.445¢ Dota / 1.136¢ LoL**; exact-kill residual is **1.122¢ Dota / 0.753¢ LoL**. Dota's native decision is +10, while the event tables intentionally report +11 for a common comparison; +10 cannot contain more of the response than +11 at this part of the rising mean curve. June Dota has only 69 complete-window kill events and 25 gold events, so July and September are the more useful comparison.

The claim “Dota has a feed lead over the market while LoL does not” is too strong for these tapes. Dota's typical kill half-move still precedes +10. The defensible contrast is **more remaining event response and much stronger subsequent state-conditioned prediction**, especially in September.

**Evidence:** command A; `dota_event_summary.csv` and `dota_event_curves.csv`; retained events **448/1,534 gold**, **1,001/3,367 kill buckets**, **1,024/3,422 exact kills**. Avoid the tempting timestamp error: Dota validation `second` and `state_ts_us` are the **market clock**, while state features are from `second−10` (`src/prepare_dataset/prepare_dataset.py:182` and `:192`; `src/train_model/train_model.py:81`). I measure Dota events on `game_features.parquet` and take their wall stamps from the market row at the same original game second. For scoring I recover the source stamp at `validation.second−10` and keep the model's `second` on that source clock.

### 3. Verified — the result survives relaxing the complete-window filter and changing the response baseline

Requiring every offset to be finite discards many events, particularly in Dota. A second pass requires only −60, 0, +11 and +120 to be finite, leaving interior holes as NaN. Thus its interior curve has a varying population and is a robustness check, not a substitute for the primary constant-population curve.

| Exact kill onset | Complete windows: events / half-time / completed at +11 | Endpoint-only windows: events / half-time / completed at +11 |
|---|---|---|
| LoL June | 89 / 6s / 68.4% | 201 / 6s / 69.1% |
| LoL July | 350 / 5s / 73.6% | 587 / 5s / 75.4% |
| LoL August | 341 / 5s / 78.1% | 619 / 5s / 78.4% |
| LoL September | 243 / 4s / 86.1% | 487 / 5s / 82.8% |
| Dota July | 409 / 5s / 70.4% | 910 / 7s / 63.5% |
| Dota September | 397 / 8s / 63.4% | 731 / 8s / 58.5% |

Changing the endpoint-only cumulative baseline from −60 to **0** leaves September exact-kill half-times **5s LoL / 10s Dota** and fractions complete at +11 **82.9% / 53.6%**. Dota gold half-times with that zero baseline are **17s July / 13s September**, versus LoL **5s July / 4s September**. The LoL-versus-Dota contrast is not created by the pre-event baseline. September versus June LoL acceleration is plausible, but different leagues/events and small June samples prevent a causal claim that competitors became faster on a specific date.

**Evidence:** command A; `lol_event_endpoint_robustness.csv`, `dota_event_endpoint_robustness.csv`. [Response curves](../work/leadlag-sol/event_curves.png) were rendered and visually inspected: LoL has a sharp jump around +4…+6, Dota a broader response; no persistent negative pre-trend in the exact-kill LoL plots.

### 4. Verified — recent-mid momentum explains little of the game-state signal; pure momentum does not explain the surviving edge

For the native decision, regress the 300-second **future mid change** on current state and competing recent-mid changes. Five folds assign each whole map to one fold; fit only small linear OLS diagnostics, never LightGBM. Controls are current mid, mid², pregame prior, and source game second. `game_current` adds current gold advantage, XP advantage, both deaths, top1 and top3 gold advantage. `momentum` adds current-mid minus mid 11/30/60 wall seconds ago. `published_delta` is the frozen ten-member research GBM's nonlinear forecast, with controls. Every regressor uses the same valid rows. Historical prices are causally before the decision; at +11, the previous 11-second move starts at the source stamp.

| Game | Period | Regressors | R², % | MAE, ¢ | MAE gain vs no move, ¢ |
|---|---|---|---|---|---|
| lol | ALL | controls | -0.339 | 9.854 | 0.040 |
| lol | ALL | momentum | -0.126 | 9.847 | 0.047 |
| lol | ALL | game_current | 1.546 | 9.761 | 0.134 |
| lol | ALL | game_and_momentum | 1.692 | 9.757 | 0.137 |
| lol | ALL | published_delta | 1.530 | 9.725 | 0.169 |
| lol | ALL | delta_and_momentum | 1.619 | 9.725 | 0.169 |
| lol | Late | controls | -0.439 | 9.375 | 0.023 |
| lol | Late | momentum | -0.314 | 9.376 | 0.021 |
| lol | Late | game_current | 2.155 | 9.253 | 0.145 |
| lol | Late | game_and_momentum | 2.239 | 9.255 | 0.142 |
| lol | Late | published_delta | 0.257 | 9.319 | 0.079 |
| lol | Late | delta_and_momentum | 0.333 | 9.321 | 0.077 |
| dota | ALL | controls | 1.738 | 7.929 | 0.190 |
| dota | ALL | momentum | 1.755 | 7.928 | 0.191 |
| dota | ALL | game_current | 3.763 | 7.873 | 0.246 |
| dota | ALL | game_and_momentum | 3.747 | 7.873 | 0.245 |
| dota | ALL | published_delta | 4.165 | 7.814 | 0.304 |
| dota | ALL | delta_and_momentum | 4.062 | 7.817 | 0.302 |
| dota | Late | controls | 3.794 | 7.586 | 0.250 |
| dota | Late | momentum | 3.716 | 7.585 | 0.251 |
| dota | Late | game_current | 5.965 | 7.540 | 0.296 |
| dota | Late | game_and_momentum | 5.884 | 7.541 | 0.295 |
| dota | Late | published_delta | 6.147 | 7.496 | 0.340 |
| dota | Late | delta_and_momentum | 6.085 | 7.497 | 0.339 |

R² can be negative because these are held-out predictions, not in-sample fitted R². OLS is squared-error calibration of an L1 GBM, so its MAE gain need not equal the uncalibrated published model's gain. To measure how much of the GBM's predictive contribution survives momentum, compare **incremental** improvement over controls with incremental improvement over controls+momentum:

| Game | Period | Delta incremental R², pp | After momentum, pp | R² retained | Delta incremental MAE gain, ¢ | After momentum, ¢ | MAE retained |
|---|---|---|---|---|---|---|---|
| lol | ALL | 1.869 | 1.745 | 93.3% | 0.129 | 0.122 | 94.4% |
| lol | Late | 0.696 | 0.647 | 92.9% | 0.056 | 0.056 | 100.1% |
| dota | ALL | 2.427 | 2.307 | 95.1% | 0.114 | 0.111 | 96.9% |
| dota | Late | 2.353 | 2.369 | 100.7% | 0.090 | 0.088 | 98.4% |

The competing-price features do not swallow the model. Late LoL raw momentum signs have only **0.272 / 0.211 / 0.330¢** directional markout at 11/30/60-second lookbacks on nonzero-move rows. Corresponding late Dota values are **0.561 / 0.889 / 1.288¢**. Late LoL model entries following the previous 30-second market move mark out **1.737¢** (49.7% of entries); those opposing it **1.958¢** (29.2%); entries with no 30-second mid move **1.945¢** (21.1%). No single momentum or reversal label describes the residual edge.

**Likely interpretation:** this is state-versus-price valuation, with a mixture of continuation and correction of market under/overreaction. A simple current-state linear model's late MAE gain **0.145¢** exceeds the recalibrated published-delta diagnostic's **0.079¢**, so late-period model misspecification/calibration deserves an experiment. That comparison is a diagnostic on the known validation tape, not an independently selected replacement model.

**Evidence:** command A produces `*_granger_monthly.csv`; command B produces `*_granger_period_summary.csv`, `entry_momentum_summary.csv`. Full diagnostic row counts: **65,380 LoL / 34,589 Dota**; late counts **22,120 / 13,921**. Model names were printed and pinned during scoring: LoL **20261003T091511Z**, Dota **20261001T220726Z**. No refits or backtests were run.

### 5. Verified — most model entries are not immediate event chases; post-event LoL entries underperform quiet opportunities

A diagnostic entry means `|published delta|≥0.02` and the selected token's current mid in **0.45…0.85**. It is an eligible snapshot, not a new strategy episode, quote, or executed fill. Sample source seconds every 5 seconds from 80…475 for LoL, and 60…475 for Dota. LoL sampling respects the first usable feed second 76 (`src/backtest/signals.py:70`, `:521`, and LoL selection lag 0 at `src/backtest/run.py:1500`); Dota is also beyond its first feed second 48. These score tables omit position, queue, networth, kill-gate and extra strategy execution filters; current mid validity still inherits the dataset/book-quality gates. The window is source second <480, so a Dota source tick at 475 has a native decision at 485 and would miss a strict wall-clock BUY cutoff. The lag sweep deliberately holds source rows fixed rather than changing the cutoff population at each lag.

Find the latest **qualifying source event** at or before the decision (all top-5% gold buckets or all net kill onsets in 60…480). Immediate is age≤30s; intermediate 30…120; quiet is >120, including no qualifying earlier event. Quiet is not proof of anticipation: a state advantage can persist without a new large event.

| Game | Period | After event 0–30s: share / markout | 30–120s: share / markout | Quiet >120s: share / markout |
|---|---|---|---|---|
| lol | 2026-06 | 13.9% / 3.81¢ | 27.1% / 3.88¢ | 59.0% / 2.27¢ |
| lol | 2026-07 | 22.1% / 2.33¢ | 33.3% / 1.02¢ | 44.7% / 2.99¢ |
| lol | 2026-08 | 18.0% / 1.75¢ | 30.2% / 2.64¢ | 51.8% / 4.06¢ |
| lol | 2026-09 | 18.9% / 2.11¢ | 32.6% / 1.76¢ | 48.6% / 2.40¢ |
| lol | ALL | 18.8% / 2.19¢ | 31.3% / 2.07¢ | 49.9% / 3.13¢ |
| dota | 2026-06 | 36.9% / 1.95¢ | 37.8% / 0.19¢ | 25.3% / 2.56¢ |
| dota | 2026-07 | 44.9% / 2.24¢ | 32.8% / 2.91¢ | 22.3% / 2.91¢ |
| dota | 2026-08 | 42.4% / 4.40¢ | 36.2% / 3.29¢ | 21.3% / 2.73¢ |
| dota | 2026-09 | 42.4% / 5.13¢ | 34.9% / 5.13¢ | 22.7% / 3.92¢ |
| dota | ALL | 42.9% / 3.60¢ | 34.6% / 3.48¢ | 22.5% / 3.18¢ |

On the late calendar cut, LoL immediate / intermediate / quiet shares are **18.4 / 31.3 / 50.3%**, with **1.641 / 1.538 / 2.111¢** markouts. Dota shares are **42.1 / 35.1 / 22.8%**, with **4.882 / 4.561 / 3.375¢** markouts. Dota benefits more from event-related opportunities; LoL's better residual opportunities are quiet state/price discrepancies.

**Important classification limit:** 40.7% of late LoL immediate-event entries have the latest event **after the feature stamp but before the delayed decision**, so that event was not yet in the scored state. The whole immediate bucket should be read as “trading near an event,” not proof that the GBM literally chased the new event. A prospective gate must use actually received scoreboard/table events; archived future source events cannot be made available early. Existing kill logic protects victim-side quotes until table deaths catch up (`src/strategy/kill_gate.py:21` and `:25`); it does not implement the residual-entry experiment proposed below.

**Evidence:** commands A/B; `*_chase_summary.csv`, `late_summary.csv`, `lag_event_conditioned.csv`. Eligibility price constants are `src/shared/constants/strategy.py:10` and `:12`. Initial WIP classifications used only separated event-study events; the final artifact uses every qualifying event and therefore supersedes those interim figures.

### 6. Verified — lag sensitivity is real but does not produce the sharp “all gone by 11s” signature

For each lag, retain the same state/history features and source second, replace only the current market side with the midpoint at stamp+lag, recompute logit(mid) and market-minus-prior, re-score the frozen model, and measure against mid at stamp+lag+300. There is **no re-training**. Hold the population to rows with finite current/future markets at every tested lag. Native LoL current/future use the exact published dataset join; alternate lags use the 1Hz cache. This is a counterfactual feature-age diagnostic, not a simulated order execution. Frozen-native entry selection/directions depend on the +11 or +10 quote, so their earlier-lag marks are an explanatory aging decomposition, not a causally available earlier trading rule; the re-scored tables rebuild signals with only the quote at each alternate clock.

**Re-scored eligible-snapshot 300s directional markout, cents:**

LoL:

| Period | Common rows / maps | Lag 0 | Lag 5 | Lag 11 | Lag 20 | Lag 30 |
|---|---|---|---|---|---|---|
| 2026-06 | 7596 / 144 | 3.34 | 3.08 | 2.92 | 2.73 | 2.58 |
| 2026-07 | 17710 / 278 | 2.60 | 2.39 | 2.19 | 2.02 | 1.77 |
| 2026-08 | 24791 / 405 | 3.60 | 3.43 | 3.21 | 3.08 | 2.84 |
| 2026-09 | 18200 / 302 | 2.41 | 2.19 | 2.14 | 1.90 | 1.80 |
| ALL | 68297 / 1129 | 2.98 | 2.78 | 2.62 | 2.44 | 2.25 |

Dota:

| Period | Common rows / maps | Lag 0 | Lag 5 | Lag 11 | Lag 20 | Lag 30 |
|---|---|---|---|---|---|---|
| 2026-06 | 2367 / 46 | 1.87 | 1.63 | 1.38 | 1.30 | 1.05 |
| 2026-07 | 14377 / 248 | 2.77 | 2.69 | 2.55 | 2.44 | 2.28 |
| 2026-08 | 7951 / 176 | 3.76 | 3.69 | 3.65 | 3.50 | 3.46 |
| 2026-09 | 12735 / 245 | 5.05 | 4.97 | 4.87 | 4.74 | 4.60 |
| ALL | 37430 / 715 | 3.65 | 3.56 | 3.44 | 3.32 | 3.18 |

Dota's native lag is 10; its fixed native-entry markouts are **3.459¢ overall / 4.855¢ September / 4.425¢ late calendar cut**. Requested +11 is shown in the sweep for direct comparability.

**Late cut (2026-08-22 UTC onward; same wall-date range for both games):**

| Game | Lag 0 entry markout | Lag 5 | Lag 11 | Lag 20 | Lag 30 | Native entry markout |
|---|---|---|---|---|---|---|
| LoL | 2.136¢ | 1.924¢ | 1.846¢ | 1.652¢ | 1.525¢ | 1.846¢ at +11 |
| Dota | 4.623¢ | 4.546¢ | 4.435¢ | 4.286¢ | 4.140¢ | 4.425¢ at +10 |

To separate entry/direction selection changes from aging, freeze the native eligible entries and their native directions. Late LoL +0 versus +11 gains **0.170¢**, map-cluster 95% CI **0.078…0.267¢**; +5 gains **−0.002¢**, CI **−0.057…+0.059¢**; +20 loses **0.156¢**, CI **−0.233…−0.090¢**; +30 loses **0.319¢**, CI **−0.475…−0.185¢**. Late Dota +0 versus native+10 gains **0.230¢**, CI **0.141…0.310¢**; +5 gains **0.105¢**, CI **0.063…0.147¢**; +30 loses **0.255¢**, CI **−0.398…−0.090¢**. All use 600 map bootstrap draws.

LoL late native immediate-event entries gain **0.385¢** at +0, CI **0.058…0.675¢**; quiet entries gain only **0.117¢**, CI **0.014…0.240¢**. This localizes some latency cost near events. Still, there is no collapse from +5 to +11, and delaying to +30 leaves considerable predictive content in both games. The gain from removing latency is of similar order across games, while their late native markouts differ by **2.580¢**. Latency is one contributor, not a sufficient explanation of the performance gap or all of LoL's late decline.

**Evidence:** commands A/B; `*_lag_summary.csv`, `late_summary.csv`, `lag_event_conditioned.csv`. Predictor uses one thread (`src/shared/utils/gbm.py:92`). LoL initial cache-only reconstruction differs from exact native current/label by mean absolute **0.160/0.208¢** on the sampled pre-common-filter population; final native values are replaced by the exact dataset current/label, and printed reconstruction errors on scored native rows are **0 / 0**. Alternate-lag numbers still have up-to-one-second cache quantization. Do not treat a hundredth-cent difference as a precise achievable live gain.

### 7. Likely — the feed disadvantage explains limited response headroom, but the decay also occurs in quiet-state opportunities

Before August 22 versus late, LoL native entry markout falls **3.020→1.846¢**. Immediate-event markout falls **2.469→1.641¢**, while quiet markout falls **3.655→2.111¢**. Quiet deterioration is actually larger in absolute cents. A story confined to competitors suddenly repricing kills faster cannot explain that pattern by itself. Dota native markout rises **2.843→4.425¢**, and immediate-event markout rises **2.800→4.882¢**.

A longer history window cannot restore news that has already entered the market. More useful history has to estimate *residual* market-versus-state discrepancy or changing calibration. That causal explanation is **likely**, because no controlled model intervention was run; the measured timing and conditional-markout patterns are **verified**.

**Evidence:** command B, `late_summary.csv`; no market participant identity, actual feed receipt timestamps or regime cause was inferred from the anonymous mid tape.

## What I ruled out

- **Verified negative:** “At +11 LoL has no predictable future movement” is false on the scored early-window sample. Native late directional entry markout is 1.846¢, and at +30 it is still 1.525¢ under re-scoring. These are unexecuted midpoint marks and do not guarantee maker profit.
- **Verified negative:** the hypothesis that recent 11/30/60-second midpoint movement accounts for most game-state forecast information is unsupported. Incremental published-delta R²/MAE largely survives conditioning on it.
- **Verified negative:** most entries are immediate event chases. Only 18.8% overall and 18.4% late are within 30s of a qualifying source event; half are >120s after one.
- **Verified negative:** a bucket endpoint alone explains the lead/lag result. Exact kill-onset and zero-baseline checks preserve the LoL disadvantage.
- **Verified negative:** a faster table alone repairs the late performance gap. Frozen-native +0 gain is 0.170¢ versus the 2.580¢ late native LoL/Dota gap; even re-scored LoL +0 remains below Dota +30.
- **Not ruled out:** league/patch population changes, calibration drift, event quote-quality selection, execution/adverse selection, or actual feed-arrival variability. No participant/bot inference, VPS work, external fetching, training, or backtest was performed.

**Limits shared by all findings:** frozen research GBMs used this validation split for early stopping; OLS folds prevent same-map row leakage but use folds across months, and maps from one series can enter different folds. Map-cluster CIs therefore do not cover all event/series dependence. Events selected from available source rows and valid midpoint windows are not the entire raw game/book tape. The complete-window filter retains 41–49% of LoL event candidates and 29–30% of Dota candidates. Lag scoring retains **68,297 rows / 1,129 LoL maps**, **37,430 / 715 Dota**; the late cut retains **22,987 / 377**, **15,209 / 326**. Thus these diagnostics are not the supplied full-catalog maker markouts and must not be substituted for those numbers. The late diagnostic uses a wall-date cut from August 22 rather than recomputing equal-map thirds; both games are put on the same calendar cut. June source/event coverage is relatively thin. Exact state stamps support measurement relative to archived game state, not a direct measurement of our historical GRID receiver's arrival distribution.

## Proposed experiments

These are for the orchestrator, **not executed here**. Changes must be isolated from live catalogs. Evaluate late-period pre-rebate PnL, share-weighted fill markout, cents/share, CVaR5, worst map and fills, with paired seeds 0/1/2; rebate/whole-period total alone cannot pass. The exact late anchor obtained from LIVE IDs joined to LoL split is **115548147900684586**, start_time **1787386103**; **421** LIVE IDs start at or after August 22. Known late diagnostics have already been inspected, so confirm any promotion on a prospective period after September 29 as well.

1. **Rank 1 — residual entry quality after a received event; keep 300s and keep SELL unchanged.** Add an isolated experiment in `src/strategy/policy.py` / quote eligibility: suppress new BUY exposure for 120s after a **received** kill or large received gold-advantage jump (freeze its threshold from the training period before evaluation); cancel BUY rungs when the gate starts, preserve ordinary SELL and existing exposure management. Receive-time state must be carried from the feed; do not consult an offline future source frame. Pre-register the 120s arm (30s-only is a weaker comparator, not a sweep). The diagnostic quiet gate retains **3,893 / 7,732** late eligible snapshots and lifts markout **1.846→2.111¢**; suppressing only ≤30s lifts the residual average to about **1.892¢**, much less. Expected benefit is entry quality and potentially tails, with a substantial volume cost; net PnL could fall. This differs from the existing kill gate (victim-side stale table protection), the dropped “hold-gate” (delta persistence), and own-fill burst cooldown.

   After the isolated patch, run an unchanged control and the patched arm in separate checkouts/run names, sequentially per seed:

   ```bash
   for seed in 0 1 2; do
     PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.run --game lol --since-match 115548147900684586 --name "leadlag-quiet120-late-seed${seed}" --signal-cadence-seed "$seed"
   done
   ```

   The loop uses distinct seed names; the production three-seed wrapper supplies the usual catalog structure for a full validation run. The patch is a prerequisite; the unmodified runner has no quiet-event gate flag. Full paired catalog command after the patch: `SEEDS=3 SHARDS=1 scripts/run_seeds.sh lol leadlag-quiet120`. Compare its resulting root with `data/backtests/lol_maker/LIVE` using `scripts/compare_backtests.py`. Cost: one no-retrain three-seed replay, serial/shards constrained by memory; potentially hours. Pass only on late net PnL and quality/tails together, not merely a higher markout on half the volume.

2. **Rank 2 — add price reaction as a calibration variable, not a replacement momentum strategy.** In a disposable LoL-only experiment harness, attach causal current-mid minus past-mid at 11/30/60 **wall seconds** to training/inference. Keep the current 77 features, native lag 11, target 300, training ≤540, ten members, identical map admission and sizing. Add a source-event-age / received-event-age marker if parity can be guaranteed. Extend only the experiment feature contract and backtest builder, so no model catalog with unsupported columns reaches live. Fit interactions that can discount already-priced game changes, then score on late entries and compare three maker seeds.

   Once that isolated harness is implemented, the existing catalog-output and candidate-backtest CLI syntax is:

   ```bash
   PYTHONPATH=src:scripts uv run python src/lol/06_train_model.py --model-dir ../betting_workspace/investigations/2026-10-03-lol-edge/work/leadlag-sol/proposed-models/midreaction
   for seed in 0 1 2; do
     PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.run --game lol --since-match 115548147900684586 --model-dir ../betting_workspace/investigations/2026-10-03-lol-edge/work/leadlag-sol/proposed-models/midreaction --name "leadlag-midreaction-late-seed${seed}" --signal-cadence-seed "$seed"
   done
   ```

   Watch late entry markout above **1.846¢** offline **and** fill markout above the control, with positive paired pre-rebate PnL and no CVaR deterioration. Expected effect is modest: the OLS competitor adds under 0.1 percentage point of R² to the frozen-delta diagnostic and does not improve MAE overall. This is not the old rejected 60s-delta experiment blindly repeated: `docs/experiments/prior-delta60-features.md:5` bundled Dota prior/gold/mid features on a six-feature model; this is LoL-specific reaction conditioning, 11/30/60 wall-time parity and late held-out judgment. Cost: one ten-member candidate fit plus three replays, to be scheduled by the orchestrator.

3. **Rank 3 — quantify a realistically faster kill component before buying a faster full-state feed.** Use archived GRID scoreboard receipts, pair them with actual board kill timestamps, and predict only the new kill contribution at the measured receipt time; join the book at that receipt. Keep table gold/XP unavailable until actual table receipt. Run an isolated 300s kill-residual candidate or overlay, native table model as control. Counterfactual source+0 using a full source state is not deliverable by the scoreboard. The observed half-response at 4–6s creates potential headroom for an earlier scoreboard, but the average full-state +0 advantage over +11 is only **0.170¢ late** and immediate-event gain **0.385¢**, so require a demonstrated after-spread gain on actual receipts. New code sketch: archive-only receipt-clock harness, freeze row/game model and market join except the kill update, leave production constants untouched. Cost: archive read/score pass first (minutes), then one paired three-seed replay if positive. Existing `--lag-seconds` is **Dota-only** (`src/backtest/run.py:1286`); do not write a fake runnable LoL lag flag. Command A already reproduces the cheap source-lag upper-bound diagnostic.

Do **not** prioritize a generic shorter-horizon retrain: `docs/experiments/lol-horizon-train.md:15` tested 60/120/180/300/600/900 with a same-pipeline h300 control; `:47` says shorter-horizon apparent gains were a reshuffle. The new evidence says the main event jump precedes table availability, not that a shorter post-table horizon would recover it. A new horizon experiment would need a received-event residual target and an explicitly different entry/exit design.

## Scripts

All run artifacts are under `work/leadlag-sol/`; no product code or `data/` contents were changed. Source repo inspected at **2a0f4b7eef649b14a8ad60648142583d8bd166f9**. One analysis Python process at a time, reduced priority, one predictor thread. Read only selected parquet columns, early-second windows and LIVE map IDs; no full raw Telonex scan.

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`:

```bash
LEADLAG_WORK=../betting_workspace/investigations/2026-10-03-lol-edge/work/leadlag-sol
# A: event studies, exact-native model scores, lag sweep, monthly held-map OLS.
nice -n 10 env PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python "$LEADLAG_WORK/analyze.py"
# B: same saved scores, late cut, entry momentum/reversal, conditional lag, period OLS.
nice -n 10 env PYTHONPATH=src:scripts OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python "$LEADLAG_WORK/late_and_robustness.py"
```

Artifacts: `*_event_summary.csv` (timing and bootstrap intervals), `*_event_curves.csv`, `*_event_endpoint_robustness.csv`, `*_events.parquet`, `*_scores.parquet` (about 13/8 MB LoL/Dota), `*_lag_summary.csv`, `*_chase_summary.csv`, `*_granger_monthly.csv`, `*_granger_period_summary.csv`, `late_summary.csv`, `entry_momentum_summary.csv`, `lag_event_conditioned.csv`, and visually checked `event_curves.png`. Final run logs are `analyze-final.log`, `late-final.log`. Earlier logs and initial-pass artifacts are scratch history and are superseded by final named artifacts. `build_report.py` assembles these tables into this report.
