# train-luna — LoL training and offline validation

Status: complete. Research only; no changes were made in `esports-trader`, and no model training, backtest, or test suite was run.

`$E` = `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`  
`$R` = `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23`

## TL;DR

1. **High, verified:** The 945-map LoL backtest uses the research model whose tree counts were early-stopped on the same 1,686-map validation set. Every backtest map is in that validation set. The run manifest and summary explicitly say the reported backtest edge is optimistic. This is a material backtest caveat, though Dota uses the same shared early-stopping helper.
2. **Medium, verified:** LoL’s raw 300-second prediction skill is not unusually strong versus Dota. On the common 0–540-second window, LoL gains 0.226¢ MAE over no-move with 1.620¢ directional markout; Dota gains 0.244¢ with 1.798¢. This does not support the hypothesis that LoL’s larger backtest PnL comes from a stronger raw model edge.
3. **Medium, verified:** The pre-332 current mid was sampled at `state_wall_us + 10s`, with the label at `+310s`. Current code samples at `state_wall_us` and labels at `+300s`, using as-of quotes. A paired run on 1,254 maps shows the earlier window improves MAE gain by 0.032–0.038¢, a modest effect. The historical full-holdout increase from 0.154¢ to 0.226¢ cannot be attributed to this alone because the train and validation parquets, hashes, and map counts also changed.
4. **Medium, likely:** LoL fits on about 541 labeled seconds per complete map, while Dota’s research fit has a median of 10 observations per map. Both use `min_data_in_leaf=200` and row-level early-stopping loss. LoL’s adjacent 1 Hz rows are highly dependent; the same validation set may therefore provide overly reassuring tree-count selection despite event-cluster confidence intervals.
5. **Low, verified:** Train eligibility accepts a map with one labeled row. Three of 2,139 train maps have fewer than 100 labeled 0–540 rows and 22 have fewer than 300. Most maps are complete, so this is a small data-quality issue, not an explanation for the live gap.
6. Current labels, event-level chronology, and LoL game-to-market links look consistent. First-window labels are present for 99.25% of otherwise usable validation rows, and performance does not decay monotonically by month or concentrate in the two feed-excluded leagues.

## Findings

### F1 — Tree count and backtest are selected on the same validation maps

- **Stage:** train / backtest
- **Claim:** The research model’s reported validation metrics and the current LoL LIVE backtest are not an independent test of tree count. This directly makes the backtest optimistic for model selection. The live production catalog is also a different model from the one in the backtest.
- **Evidence:** `src/shared/utils/gbm.py:174-190` trains each research member with early stopping on `valid_sets=[validation_data]`, patience 100, up to 3,000 rounds. `src/lol/06_train_model.py:161-184` passes the same filtered `validation_dataset` into `fit_research_members` and immediately predicts/evaluates that dataset; `:204-229` records those validation metrics. `src/lol/lol_validation_metrics.py:69-84, 93-121` clusters the reported metric confidence intervals by Polymarket `event_id`, but early stopping itself is still the LightGBM row-level validation loss.
- **Reproduced evidence:** `$E/data/lol/models/research/model.json:43-62` is model `20260921T095801Z`, trained on 2,139 maps and evaluated on 1,686. `$E/data/backtests/lol_maker/LIVE/seed0/manifest.json:38-52` names that same research catalog, selects 945 maps, and has `signals_sha256` equal to the model’s validation dataset SHA (`9e8e66c2…`). The result parquet has 945 rows; the analysis joined its map IDs to `split.parquet` and found **945/945** are validation maps, with zero outside the split (`$R/work/train-luna/offline-analysis.txt:73-78`). The summary itself says “tree count was chosen by early stopping on the same split — reported edge is optimistic” (`$E/data/backtests/lol_maker/LIVE/seed0/summary.json:252`).
- **Research versus production:** The current local production catalog is `20260921T095813Z`, has `train_matches=3825`, and `metrics=null` (`$E/data/lol/models/production/model.json:43-51`). `src/lol/06_train_model.py:235-275` fits production on `production_training.parquet` at the research mean tree count, with no holdout. Thus the backtest uses a 2,139-map research model, while the live production catalog was fitted on all 3,825 accepted train+validation maps. The historical validation metrics are not a clean performance estimate for production, and the research backtest is not a replay of the exact production catalog.
- **Mechanism:** Choosing each member’s tree count on these maps and then evaluating/backtesting them on the same maps selects for favorable validation noise. It does not mean validation rows enter the training gradients, but it does make reported returns and validation metrics optimistic. Production’s larger fitted set is a further model-identity difference when comparing backtest with live.
- **Severity:** high
- **Confidence:** verified
- **Next check / fix proposal:** Reserve a later chronological set of whole Polymarket events for final evaluation. Select tree count using earlier event folds, then replay that untouched set. Keep the current validation replay labeled as a tuning result.

### F2 — LoL’s offline model edge is close to Dota’s, not much larger

- **Stage:** train / validation
- **Claim:** The large difference between LoL and Dota backtest PnL is not explained by LoL having substantially higher 300-second model skill in its current validation results.
- **Evidence and command:** Reproduced with:

  ```sh
  cd $E && PYTHONPATH=src uv run python $R/work/train-luna/analyze_offline.py
  ```

  Output is saved in `$R/work/train-luna/offline-analysis.txt`. On LoL’s labeled 0–540 rows, `n=892,006` across 1,686 maps: no-move MAE 9.957¢, model MAE 9.731¢, gain **0.226¢**, directional markout **1.620¢**; the event-cluster 95% MAE-gain interval in `model.json` is 0.171–0.283¢ (`offline-analysis.txt:1-2`; `$E/data/lol/models/research/model.json:43-53`). Dota evaluated on the same 0–540 window, `n=312,756` across 598 maps, gains **0.244¢** with **1.798¢** directional markout (`offline-analysis.txt:131-137`). Dota’s full research interval `[-60,599]` is also close: 0.222¢ gain, 1.676¢ directional markout, 374,555 labeled rows (`offline-analysis.txt:131-133`). Dota features were shifted through the project’s `lagged_source_features` helper, matching its trainer (`src/train_model/train_model.py:75-79, 176-211`).
- **Second and predicted-edge buckets:** LoL’s 0–59s gain is only 0.017¢; later buckets are 0.119–0.349¢ (`offline-analysis.txt:54-65`). The gain is concentrated in larger predicted moves: `<0.5¢` has −0.002¢ gain; `0.5–1¢` 0.031¢; `1–2¢` 0.121¢; `2–5¢` 0.465¢; and `>=5¢` 1.179¢ (`offline-analysis.txt:66-72`). That pattern is consistent with the backtest’s `min_abs_delta=0.02` gate, but it is still measured on the same tuned validation split.
- **Mechanism / why LoL differs:** The raw validation forecast edge is roughly comparable, with Dota modestly higher on the common window. Training therefore does not provide evidence for a much stronger LoL predictor to account for the backtest/live gap. Execution, source timing, fill selection, and the research/production model difference need to account for more of that gap.
- **Severity:** medium
- **Confidence:** verified
- **Next check / fix proposal:** Compare model markouts at the actual live signal and fill times using an untouched event set and the exact production catalog. The all-second 300s validation metric is not enough to diagnose fill economics.

### F3 — The 332e1c17 time shift was real; its measured benefit is modest and live-clock dependent

- **Stage:** prepare / train
- **Claim:** Before `332e1c17`, the feature midpoint was 10 seconds after the state-frame wall time and the target was 310 seconds after it. Current code uses the midpoint at the frame wall time and target at +300 seconds. A paired local-data comparison shows higher offline edge with the corrected window, but it does not support attributing the entire historical edge increase to the shift.
- **Evidence:** `git show 332e1c17 -- src/lol/05_prepare_dataset.py` changes the current lookup from horizon `LOL_SOURCE_LAG_SECONDS` to `0` and the target from `LOL_SOURCE_LAG_SECONDS + LOL_TARGET_HORIZON_SECONDS` to `LOL_TARGET_HORIZON_SECONDS`. Current `src/lol/05_prepare_dataset.py:298-324` uses `slot.state_wall_us` for current and `+LOL_TARGET_HORIZON_SECONDS` for the label. `src/shared/utils/telonex_book.py:173-203, 227-238` chooses the last valid quote at or before the requested timestamp; it does not read a future snapshot. `src/lol/constants.py:123-142` sets the LoL model window to 0–540 seconds and the horizon to 300 seconds.
- **Paired comparison:** The September 1 auxiliary snapshot `$E/data/experiments/lol-horizon-train/datasets/validation.parquet` retains the earlier T+10/T+310 convention; it is not the byte-identical 20260915 canonical validation parquet. On the **1,254 common maps / 653,117 common labeled rows / 613 events**, every nonmarket game feature and event ID matched exactly. The old versus new midpoint differed by a mean absolute 1.064¢ (p95 4.50¢); the target differed by 1.369¢ (p95 5.50¢) (`$R/work/train-luna/mid-semantics-comparison.txt:1-2`). The 20260915 pre-fix model’s MAE gain on those same maps rises **0.159¢ → 0.191¢** when evaluated under T/T+300 semantics; the 20260919 post-fix model rises **0.165¢ → 0.204¢** (`mid-semantics-comparison.txt:3-6`). This is a measured 0.032–0.038¢ improvement from the 10-second window shift on that paired sample. It is not proof that the live feed can use that exact edge: the live quote/frame timestamp alignment remains the deciding clock question.
- **Historical artifact comparison:** Archived validation gain rises from 0.154¢ in `20260915T210420Z` to 0.226¢ in `20260919T112924Z` (`$E/data/lol/models/archive/research/20260915T210420Z/model.json:43-62`; `$E/data/lol/models/archive/research/20260919T112924Z/model.json:43-62`). But the train dataset SHA changed from `26af39d6…` to `1a9e48c3…`, train maps rose 1,934→2,139, and validation maps changed 1,588→1,661. The later validation SHA also differs. Current 20260921 research has the same training SHA as 20260919 but a further expanded 1,686-map validation set. So the 0.072¢ historical rise is confounded by dataset growth and changed rows, not just the clock correction. On the current 892,006 rows, the archived pre-fix model gains 0.206¢ and the post-fix model gains 0.226¢ (`offline-analysis.txt:1-6`).
- **Mechanism:** The earlier baseline moves the forecast interval to include the market’s response during those first 10 seconds. The paired result indicates a real change in the supervised target and modest improvement, not a large standalone alpha jump. Whether it is usable depends on whether live `market_p_radiant` is aligned with `state_wall_us`; the local training analysis cannot establish actual receive-time alignment.
- **Severity:** medium
- **Confidence:** verified for the code and paired result; live applicability remains open
- **Next check / fix proposal:** Confirm on captured live rows which market midpoint is available at the GRID frame’s `state_wall_us` and at trader receive time. Keep an explicit paired T+10/T+310 versus T/T+300 comparison on a frozen set of identical maps when the dataset changes.

### F4 — LoL’s 1 Hz fit gives each map far more correlated training rows than Dota

- **Stage:** train
- **Claim:** LoL’s model fitting is much denser in rows per map than Dota’s. This may make row-level early stopping more sensitive to correlated within-map observations and is a plausible contributor to LoL validation optimism, but I did not establish that it causes the measured live gap.
- **Evidence:** `src/lol/06_train_model.py:75-82` trains only labeled 0–540 seconds. In the current data that gives 1,120,284 labeled train rows over 2,139 maps, median 541 rows/map. Dota’s selected research train set has 11,063 rows over 1,152 maps, median 10 rows/map, at `-60,0,60,…,540` seconds (`offline-analysis.txt:73-74, 108, 136`). `src/shared/utils/gbm.py:27-34` uses `min_data_in_leaf=200`; `:216-223, 226-244` samples 90% of maps for a member and includes all rows from each selected map. Thus a LoL leaf’s 200 observations can come from a few adjacent minutes of one map, while 200 Dota training rows span roughly 20 maps. The event-cluster CI in `src/lol/lol_validation_metrics.py:69-84, 118-121` addresses uncertainty estimates, not the row-level loss used by early stopping.
- **Mechanism:** Adjacent LoL seconds share game state and market trajectory. They are useful decisions but not 541 independent examples. Row-level L1 can over-represent each continuous map path in fitting and tree selection relative to Dota’s sparse training observations.
- **Severity:** medium
- **Confidence:** likely
- **Next check / fix proposal:** Compare current fits against map-balanced or event-balanced row weights, and choose tree count on a separate chronological event holdout. Report results both per decision row and per map/event.

### F5 — Train-map eligibility permits very sparse game-feature coverage

- **Stage:** prepare
- **Claim:** A train map passes the labeled-row test with just one labeled second. This currently affects a small tail but should be visible as an audit condition.
- **Evidence:** `src/lol/05_prepare_dataset.py:523-532` counts labeled rows; `:553-562` only drops the train map when that count is below one (then checks Telonex tape). Current selected rows have 3/2,139 train maps below 100 rows and 22/2,139 below 300; the median is 541 rows (`offline-analysis.txt:108`).
- **Mechanism:** Those maps receive much less weight than complete maps in a row-level objective and may reflect incomplete live-state coverage. They cannot create a large effect at current frequency, but a single labeled row is a weak data-quality gate.
- **Severity:** low
- **Confidence:** verified
- **Next check / fix proposal:** Audit these 22 maps by `match_id`, league, and `audit.parquet` skipped-age counts. Consider a minimum labeled-row threshold tied to the intended 0–540 window, or explicitly exclude sparse maps from fitting.

## Checked and OK

- **No train/validation event leakage in the current split.** `src/lol/05_prepare_dataset.py:492-509, 606-625` computes each PM event’s earliest spawn and assigns the whole event to train or validation at `LOL_VALIDATION_START_TIME`. The current `split.parquet` has 3,825 unique maps: 2,139 train maps / 1,077 events and 1,686 validation maps / 755 events; map overlap and event overlap are both zero. Train event-start timestamps end at 1,780,503,568 and validation starts at 1,780,585,966 (`offline-analysis.txt:73-105`).
- **No duplicate current game-to-market link found.** `src/lol/03_link_lolesports.py:721-725` rejects duplicate `esports_game_id` and duplicate `(event_id, game_number)`. In the 5,565 local links, duplicate game ID, condition ID, and event/game pair counts are all zero; no validation map lacks a link (`offline-analysis.txt:73-74`).
- **Current labels are correctly shaped as a 300-second delta.** `src/shared/utils/gbm.py:150-164` defines target `future - current` and restores clipped future price. Current LoL preparation uses as-of quote pairs at frame time and frame time +300 seconds (`src/lol/05_prepare_dataset.py:298-324`; `src/shared/utils/telonex_book.py:173-203`). `radiant_win` is not in the 12 `FEATURE_COLUMNS` (`src/shared/utils/gbm.py:36-50`).
- **Label coverage is high in the model window.** On usable 0–540 validation rows, 892,006/898,745 have a label (99.25%); the remaining target timestamps are still before map end, so missing labels are not explained by maps ending before T+300 (`offline-analysis.txt:114-115`). At exact second 540, 1,662/1,676 validation rows have a label (99.16%); coverage is 98.6–99.4% in the 60-second buckets (`offline-analysis.txt:111-115`).
- **Label distribution is plausible and contains flat targets.** Train delta has median 0, mean +0.074¢, SD 12.671¢, 2.10% exactly flat, and p01/p99 −31.5/+31¢. Validation has median +0.5¢, mean +0.513¢, SD 12.957¢, 2.15% flat, and p01/p99 −32/+33¢ (`offline-analysis.txt:106-110`).
- **Paused maps do not dominate the validation edge.** 567/1,686 validation maps have pauses (1,112 pauses; 85,486 pause-seconds total). Paused rows gain 0.180¢ MAE versus 0.249¢ for unpaused rows; directional markout is about 1.62¢ for both (`offline-analysis.txt:118-127`).
- **No month-over-month decay or no-feed league concentration is visible.** Monthly MAE gain is June 0.229¢, July 0.136¢, August 0.231¢, September 0.329¢; this is not monotonic, although all months share the tuned validation set (`offline-analysis.txt:7-12`). The two no-live-feed leagues in `config/lol_league_whitelist.json:21-24` account for 276 maps / 147,120 rows (16.5%) and gain 0.276¢, versus 0.224¢ for the 948-map live-feed whitelist group (`offline-analysis.txt:49-53`). The replay-selected 945 maps are all from validation and have 0.225¢ gain; they exclude the no-live-feed leagues (`offline-analysis.txt:75-95`). EMEA Masters has 0.111¢ gain on 78 maps; LPL has 0.282¢ on 218 maps; LCK Challengers League has 0.250¢ on 58 maps (`offline-analysis.txt:19, 25, 33`).

Full current per-league table (all values are 300-second MAE gain / directional markout in cents; small league samples are noisy):

| League | Maps | MAE gain | Directional |
|---|---:|---:|---:|
| Arabian League | 1 | 0.528 | 3.635 |
| CBLOL | 57 | 0.426 | 2.110 |
| Circuito Desafiante | 59 | 0.290 | 1.703 |
| EBL | 13 | 0.312 | 4.710 |
| EMEA Masters | 78 | 0.111 | 0.765 |
| Esports World Cup | 48 | 0.267 | 2.174 |
| HLL | 3 | 1.046 | 6.515 |
| Hitpoint Masters | 37 | 0.158 | 2.299 |
| KeSPA Cup | 48 | 0.016 | 0.729 |
| LCK | 156 | 0.240 | 1.357 |
| LCK Challengers League | 58 | 0.250 | 1.666 |
| LCP | 83 | 0.139 | 2.022 |
| LCS | 64 | 0.128 | 1.057 |
| LEC | 129 | 0.183 | 1.464 |
| LES | 77 | 0.346 | 2.659 |
| LFL | 72 | 0.062 | −0.019 |
| LIT | 9 | 0.683 | 2.292 |
| LJL | 8 | −0.317 | 0.656 |
| LPL | 218 | 0.282 | 1.660 |
| LPLOL | 32 | 0.519 | 2.613 |
| LRN | 32 | 0.275 | 1.666 |
| LRS | 14 | 0.394 | 2.959 |
| Mid-Season Invitational | 59 | 0.109 | 0.196 |
| NACL | 81 | 0.258 | 2.389 |
| NLC | 34 | 0.559 | 3.531 |
| Prime League 1st Division | 101 | 0.125 | 1.288 |
| Rift Legends | 32 | −0.155 | 0.410 |
| Road Of Legends | 37 | 0.209 | 2.204 |
| TCL | 38 | 0.211 | 1.438 |
| World Star Challengers Invitational | 8 | 1.430 | 5.222 |

## Open questions for the owner

- Confirm that the production GRID market midpoint presented to the model is available at the same `state_wall_us` anchor used in the corrected training dataset. The paired study establishes the training-side shift, but the final live-clock alignment is outside this trainer-only pass.
- If an exact causal before/after-332 result is needed for the canonical 20260915 dataset, preserve or reconstruct that exact hashed parquet. The September 1 experiment snapshot enables the paired 1,254-map comparison above but does not match the 20260915 canonical dataset SHA.

## Needs from VPS

None. All checks used local artifacts.

## Scripts and outputs

- `$R/work/train-luna/analyze_offline.py` — saved-catalog LoL and Dota metrics, month/league/second/edge buckets, split/link overlap, label and pause diagnostics.
- `$R/work/train-luna/compare_mid_semantics.py` — paired pre/post midpoint convention evaluation on shared maps.
- `$R/work/train-luna/inspect_parquets.py` — local parquet schema and row inspection.
- `$R/work/train-luna/offline-analysis.txt`
- `$R/work/train-luna/mid-semantics-comparison.txt`
- `$R/work/train-luna/parquet-inspection.txt`
- `$R/work/train-luna/lol_by_month.csv`, `lol_by_league.csv`, `lol_by_feed_eligibility.csv`, `lol_by_whitelist_status.csv`, `lol_by_second.csv`, `lol_by_abs_pred.csv`, `lol_by_paused.csv`, `lol_backtest_subset_by_league.csv`
- `$R/work/train-luna/commit-332.diff`, `model-history.log`, `dataset-history.log`
