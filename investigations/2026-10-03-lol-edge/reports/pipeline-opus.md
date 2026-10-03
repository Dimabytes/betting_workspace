# pipeline-opus: adversarial code review of the LoL data pipeline vs Dota
Status: FINAL
## Verdict (≤10 lines)
1. I found one large LoL-only training defect and no large train/live wiring bug. LoL trains on 1 Hz rows (≈388 rows per map), Dota on minute rows (≈19 per map). Both use the same `min_data_in_leaf=200`. The LoL ensemble therefore uses the per-map constant `market_radiant_prior` as a map identifier: 26 % of its splits (Dota 5 %). Permuting the prior across maps costs 1.83 ¢ of in-sample gated DIR, but only 0.13 ¢ out of sample. Early stopping ends at 37–61 trees (Dota 56–83). Verified on the LIVE catalog; I did not retrain, so the PnL gain of a fix is not measured.
2. The live wiring matches training within small, measured offsets. On 343 archived September maps: `second` +3 s, GRID arrival 10.3 s after the frame stamp (training uses 11 s), level-ups 1–2 s early vs gold, |Δ nw_adv| 50 gold, sides correct on 99.5 % of maps, prior anchor offset 0 s. Scored on the LIVE model, these offsets move the delta by ≤0.035 ¢ on average. They do not explain the gap to Dota.
3. "Late third ≈ flat" hides two populations. The 290 September archive-replay maps improved on every change: engine PnL 1,250 → 2,721 → 4,111 and BUY markout 0.26 → 0.92 → 1.19 ¢ (rebuild → w540lv6 → histfix). The 79 synthetic maps of 22–31 August lose under all three models (−952 to −1,523). In the late third, LEC lost −2,884 over 48 maps and LCK gained +4,333 over 29 maps.
4. Top-3 by expected late-third impact: (1) stop per-map memorization (thin rows or scale the leaf minimum; E1); (2) judge each change per population: Sep archive maps, late-Aug synthetic maps, LEC (E2, E3); (3) label-gate selection: 12.7 % of training rows ≤540 lose their label to a wide or stale book at +311 s (E4).

## Findings

### F1. LoL trains on 1 Hz rows with Dota's leaf size, and the trees memorize maps through `market_radiant_prior` — design difference, train-only — verified
- **Code.** LoL training rows are every age-gated second 0..540. See `src/lol/05_prepare_dataset.py:254-287` (one row per `livestats.grid_rows` slot, `LOL_TRAIN_GRID_SECONDS = 1`) and `src/lol/06_train_model.py:81-87`. Dota training rows are minute states only. See `src/prepare_dataset/prepare_dataset.py` `match_minute_states` → `build_minute_rows` and `src/train_model/train_model.py:122-125`. Both use `LGB_PARAMS` (`src/shared/utils/gbm.py:27-34`: `num_leaves=63`, `min_data_in_leaf=200`, L1). In LoL, 200 rows are about half of one map. In Dota, 200 rows are 10+ maps. `market_radiant_prior` is constant within a map (`05_prepare_dataset.py:248`), so a split on it separates whole maps.
- **Rows per map, member_00, exact reproduction of the sub90 draw.** LoL: 890,576 rows / 2,296 maps = 387.9 rows per map. The reproduced leaf counts equal the model's `leaf_count` on 100 % of leaves. Dota: 29,793 rows / 1,570 maps = 19.0. The Dota dataset was rebuilt today, so only 54 % of Dota leaves reproduce exactly. Command: `work/pipeline-opus/leaf_maps.py`.
- **Effective maps per leaf (Herfindahl).** LoL leaf median 36.5; 45.3 % of leaves have <30 effective maps and 19.7 % have <10. Dota leaf median 144.6; 0.8 % of leaves have <30 and 0 % have <10. Only 0.8 % of LoL rows fall into leaves with <10 effective maps.
- **Split use.** Totals over all 10 members, from `work/pipeline-opus/prior_splits.py` and a feature-importance one-liner:
  - LoL LIVE catalog (`data/lol/models/experiments/hist-policy-20261003`): `market_radiant_prior` 7,701 of 29,202 splits (26.4 %), 23.9 % of gain, rank 2. 8.8 % of its prior splits sit in nodes of ≤10 map-equivalents. Gain on market features (prior + logit + market_vs_prior) is 40 %. `second` has 42 splits (0.09 % gain).
  - Dota (`data/new_model/research`): the prior has 5.1 % of splits and 4.5 % of gain. 0 % of its prior splits sit in nodes of ≤10 maps. Gain on market features is 19 %.
- **Memorization test.** I permuted the prior across maps; `market_vs_prior` stays as is. Model: member_00. Rows: second <480. Commands: `work/pipeline-opus/prior_perm.py` and `prior_perm_dota.py`.

  | | LoL in-sample | LoL out-of-sample | Dota in-sample | Dota out-of-sample |
  |---|---|---|---|---|
  | MAE | 8.486 → 8.859 ¢ (+0.372) | 9.183 → 9.291 ¢ (+0.108) | 7.430 → 7.536 (+0.107) | 7.435 → 7.494 (+0.059) |
  | gated DIR (\|Δ\|≥0.02) | 5.343 → 3.516 ¢ | 2.322 → 2.193 ¢ | 5.002 → 4.977 | 3.317 → 3.299 |

  LoL MAE is 8.49 ¢ in-sample and 9.18 ¢ out of sample, a gap of 0.70 ¢. Dota MAE is 7.43 ¢ on both sides. The LoL prior is worth 3.4× more MAE in-sample than out of sample, and 14× more DIR. The Dota prior has almost no DIR value on either side.
- **What it costs, and what it does not.** Early stopping on validation ends at 37–61 trees per member, mean 47 (`model.json`). Dota ends at 56–83, mean 69. I tested the few-map leaves directly. Removing the contribution of leaves with <30 effective maps from member_00 does not raise out-of-sample DIR. The 6,650 rows that pass the gate only because of those leaves realize 3.38 ¢, against 2.45 ¢ for all gated rows (`leaf_contrib.py`, first 200k validation rows). So the harm is not toxic leaves. The model spends capacity on map identity, and validation L1 then stops it after about 47 shallow-learning-rate trees.
- **Fix.** Make one leaf represent many maps. Options: train on one row per 10 s or 30 s per map, set a LoL-only `min_data_in_leaf` of about 200 × rows-per-map-ratio (≈4,000), or weight rows by 1/rows_per_map. See E1.

### F2. The late third is two populations; history plus histfix helped the September archive maps 3.3× — analysis — verified
Command: `work/pipeline-opus/src_by_period.py <run_dir>`. Values are engine PnL before rebate, 3-seed mean. Thirds are by horn and equal in maps (413 each). "grid" = archive-schedule replay; "synthetic" = grid-v1.

| late third (08-22..09-29) | maps | rebuild-20260930 | w540lv6 | histfix (LIVE) | BUY 300 s markout, rebuild → w540 → histfix |
|---|---|---|---|---|---|
| September archive maps (grid) | 290 | 1,250 (4.3/map) | 2,721 (9.4/map) | 4,111 (14.2/map) | 0.26 → 0.92 → 1.19 ¢ |
| 22–31 August synthetic | 79 | −1,427 | −952 | −1,523 | −1.71 / −1.31 / −1.67 ¢ |
| September synthetic | 44 | +1,239 | +497 | +743 | 0.17 / 2.60 / 0.47 ¢ |

- For comparison, histfix early and middle thirds (all synthetic): +27.1 and +20.2 per map, markout 2.28 and 3.06 ¢.
- By league, histfix late third: LEC −1,341 (29 Sep archive maps) and −1,543 (19 late-Aug maps); LCK +4,333 (29 Sep maps); EMEA Masters −246 (75 maps); Hitpoint Masters −259 (40 maps). By patch: late-Aug synthetic maps are mostly 16.16 (65/79); Sep archive maps are mostly 16.17 (176/290).
- Per-map prepare audit stats (pauses, skipped frames, skipped market rows) do not differ for 22–31 August versus other periods (`audit.parquet` one-liner). So I found no pipeline cause for the late-August loss. That question goes to leagues-dev and decay-*.
- Consequence: "nothing improved" is true only for the aggregate. The changes improved the most live-like replays.

### F3. Live `second` sits about 3 s ahead of training `second` — train/live mismatch, nit — verified
- **Live.** `second = live_clock_seconds(board, age) - table.feed_delay`, with `feed_delay = 8` (`src/trader/grid_feed.py:199-200`, `src/trader/grid_widgets.py:292-301`). **Training.** Spawn-anchored, pause-adjusted livestats time (`src/lol/livestats_frames.py:537-563`, `632-655`). **Backtest grid-v1.** Training seconds, because `lag_seconds=0` for LoL (`src/backtest/run.py:1500`).
- Measured on 343 archived maps by matching total net worth: GRID − livestats = +3 s (median of per-map medians; p25 2, p75 4). Death steps give +2 s; this is an upper bound because ticks are sparse. GRID horn − livestats spawn = 0 s (p5 −1, p75 0). So the GRID clock and the spawn anchor agree. The real `series_table` delay is about 11 s, not the declared 8 s.
- The dual-feed recording `lol-fxw7-los-2026-08-29` gives +6.7 s (n=10 deaths, `scripts/compare_lol_grid_livestats.py`). That script anchors on the first non-zero gold frame, not on `find_spawn_index`, so I treat it as an outlier.
- Impact: none measurable. Scoring the LIVE ensemble with `second + 3` changes no prediction (mean |Δdelta| 0.000 ¢ on 418,571 validation rows). The model has 42 splits on `second` (`perturb_check.py`).
- Fix (optional): use the measured table lag instead of the declared `delay` in `_live_snapshot`, or leave it as is.

### F4. Training uses an 11 s source lag; the measured GRID arrival is 9.7–10.3 s — mismatch, nit, conservative — verified
- **Where the lag is applied.** Training: current mid at stamp + 11 and label at stamp + 311 (`05_prepare_dataset.py:270-285`). Backtest grid-v1: decision at `state_ts + 11 s` (`src/backtest/signals.py:524`, `386`). `second` is not shifted, because `lag_seconds=0` (`run.py:1500`). Live: the lag is implicit in GRID arrival. The lag is not applied twice anywhere, and no path misses it.
- **Measured.** GRID `received_ns` − livestats `state_ts_us` at the first tick that shows death k, on 329 maps: median of per-map medians 10.30 s; median of per-map minima 9.66 s (`grid_vs_livestats_clock.py`).
- Effect: the backtest and the training rows see the book 0.7–1.3 s later than live. This direction is conservative. It is not a lead-time loss in live.

### F5. XP-from-level definitions match, but GRID level-ups lead livestats by 1–2 s — mismatch, nit — verified
- **Definitions.** Live: GRID `increaseLevel + 1` → `LOL_LEVEL_XP` (`grid_widgets.py:231-240`, `grid_feed.py:208`). Training: livestats `level` → `LOL_LEVEL_XP` (`livestats_frames.py:456-460`). Neither side uses GRID `ExperiencePoints`, so the brief's suspected definition mismatch does not exist.
- **Timing.** At the per-map nw-aligned second, `radiant_xp_adv` is equal on 44 % (minute 1) to 75 % (minute 8) of 28,410 ticks. Mean |gap| is 190–254 XP, while mean |xp_adv| is 257–892 (`xp_gap_nwaligned.py`). Of the mismatched ticks, 80 % match livestats 1–3 s later, and 10 % stay unresolved within ±20 s (`xp_shift.py`).
- **Impact on the LIVE model.** I fed xp_adv (and its tape) from second + 2. Mean |Δdelta| 0.035 ¢, p95 0.193 ¢, 0.84 % gate flips, DIR 2.127 → 2.132 ¢ (`perturb_check.py`).

### F6. Net worth: training rebuilds GRID-style net worth from livestats; parity is good after minute 1 — design difference — verified
- Livestats `totalGold` is gold earned; it never decreases (see invariant rule 2, `livestats_frames.py:381-400`). Training uses `totalGold − cumulative consumed-item gold`. The consumed gold comes from `details` inventories priced with the patch's Data Dragon table (`livestats_frames.py:435-449`, `networth.py:204-311`). Live uses GRID `NetWorth` directly (`grid_widgets.py:241`).
- **Parity at aligned seconds.** Median |Δ radiant_nw_adv| is 50 gold in every minute. The signal median is 89 gold in minute 1 and 173–808 in minutes 2–8. Median |Δ top1_nw_adv| is 5–20 gold.
- Top-player ratios use the over-total rule on both sides (`livestats_frames.py:463`, `grid_feed.py:195-196`). Deaths match on 96–99 % of ticks.
- One validation map looks corrupt: 117229242613241570 has a −19,262 late gold lead, but the market is 0.705 for Radiant and Radiant won (`orientation_check.py`).

### F7. Synthetic cadence and first tick do not match current archives — backtest-vs-live mismatch, low — verified
- `LOL_GRID_V1_BANDS` = 8/6/5/5 s and `GRID_V1_FIRST_TICK_SECOND["lol"] = 76` (`signals.py:70`, `106-111`, `521`).
- Archive ticks, 405 admitted maps (`cadence_check.py`): mean gap by band 4.55 / 5.81 / 4.54 / 4.01 s (0–180 / 180–360 / 360–540 / 540+), stable from August to October. The first in-game tick is at second 4 (median; p75 21).
- So grid-v1 has about 1.75× fewer ticks in 0–180 s and never decides before 76 s. Live decides from about 4 s. Archive replays made 33 BUY fills before 76 s over 3 seeds (`early_fills.py`), which is negligible.

### F8. Spawn anchor and pause clock have tail errors — data noise, low — verified
- `find_spawn_index` takes the first frame with all players at 500 gold, level 1 and deaths 0 (`livestats_frames.py:351-373`). A pause is a wall gap >5 s with unchanged gold, kills and HP sums (`livestats_frames.py:114-124`, `537-563`). Before passive gold starts, any feed gap >5 s counts as a pause. A "paused" state that keeps the feed ticking is invisible to the detector (the ponytail note at `:547`).
- **Measured** on 300 sampled maps (`spawn_pause_check.py`). For 2026 maps, passive-gold onset on our clock is at 67.9 s median (IQR 67.86–68.89). 18/295 maps (6 %) are off by >3 s and 6/295 (2 %) by >10 s, mostly maps with early pauses. Maps from 2025-10/11 start gold at ~103 s, which is a patch difference, not a bug. The monthly medians are flat, so there is no drift into the late period.

### F9. The label gate drops 12.7 % of training rows — design difference, same rule as Dota — speculative impact
- A row keeps its label only if the book at +311 s is ≤5 s old, has a spread under 6 ticks, and the two token mids sum to 1 ± 0.05 (`telonex_book.py:190-240`, `05_prepare_dataset.py:278-285`).
- Unlabeled share for rows ≤540: training 12.7 % (2026-01: 18.5 %), validation 7.3 % (2026-09: 8.8 %). If wide books follow big swings, training under-represents large moves. I did not measure the label those rows would get.

### F10. Early stopping overlaps the backtest maps, and live trades a catalog the backtest never runs — design, same as Dota — verified from code
- Research early stopping uses all labeled validation rows ≤540 of all leagues (`06_train_model.py:185-195`). Those are the backtest maps (plus non-whitelisted maps).
- Live loads `LOL_PRODUCTION_MODEL_DIR` (`src/trader/game_profile.py:92-97`). That catalog trains on train + validation at the research tree count (`06_train_model.py:269-310`). The backtest runs the research catalog.

## What I ruled out
- **Side orientation.** For maps with a late gold lead ≥8k, the market agrees with the gold side on 99.82 % of train maps (1,714) and 99.54 % of validation maps (1,293). There is no month below 99.2 % (`orientation_check.py`). Training orients through `radiant_token_index` (`05_prepare_dataset.py:153-157`). Live orients GRID BLUE/RED through market names (`grid_feed.py:95-126`, `game_profile.py:90-91`).
- **Prior definition.** Training uses the last two-sided pair in [spawn − 61 s, spawn) (`05_prepare_dataset.py:345`). Live uses [horn − 61 s, horn) (`trader/lol_prior.py:47-56`, `match_worker.py:620-633`). horn − spawn = 0 s median, IQR 0–0, on 345 maps. About 5 % of maps have the horn later by ≥98 s, from GRID re-anchoring after a pause.
- **Pauses inside the horizon.** In both training and backtest, the label is wall stamp + 311 s. History lags are game seconds without pauses in both: the training tape is keyed on pause-adjusted seconds, and in live the GRID clock stops in a pause and `SnapshotHistory.record` overwrites the same second. There are no training rows inside a detected pause.
- **Deaths semantics.** Both sides use cumulative per-team sums (`livestats_frames.py:461-462`, `grid_feed.py:209-210`); parity is 96–99 % per tick.
- **History math parity.** The backtest and live share `SnapshotHistory` / `walk_feed_ticks`. Training uses exact-second pivots on the dense tape (`dota_features.py:411-447`, gap 0). That difference is the previous investigation's dense-vs-received effect; I did not redo it.
- **`second` and XP offsets as PnL drivers.** Scoring shows ≤0.035 ¢ mean delta change (F3, F5).
- **Few-map leaves as toxic signals.** Rows gated only by those leaves realize 3.38 ¢ (F1).
- **Late-August prepare anomalies.** Pause counts, skip fractions and skipped market rows are flat across periods.

## Proposed experiments
Run each from `esports-trader/`. Judge each on the late-third table in F2: Sep archive maps, late-Aug synthetic maps, and LEC separately. Also judge on paired `scripts/compare_backtests.py`.

**E1 — stop map memorization. Priority 1. Cost: 1 research train (~same as `lol-train`) + 3-seed backtest.**
Two research variants, both with 77 columns, ≤540, and the histfix policy:
- (a) Thin rows. In `src/lol/06_train_model.py:81` `slice_model_rows`, add `& ((frame["second"] - frame["match_id"] % 10) % 10 == 0)`. This keeps about 39 rows per map, with a per-map phase. Apply it to training rows only; early stopping keeps all validation rows ≤540.
- (b) Keep all rows, but use a LoL-only `min_data_in_leaf=4000` through a params override in `gbm.ensemble_member_params` for LoL.

Commands:
```
PYTHONPATH=src uv run python scripts/run_lol_stage.py 06_train_model --model-dir data/lol/models/experiments/thin10
for s in 0 1 2; do make lol-backtest ARGS="--validation --name thin10 --model-dir data/lol/models/experiments/thin10 --signal-cadence-seed $s"; done
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python scripts/report_seeds.py data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_thin10 --expected-seeds 3
PYTHONPATH=src:scripts uv run python ../betting_workspace/investigations/2026-10-03-lol-edge/work/pipeline-opus/src_by_period.py data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_thin10
```
The seed loop follows how earlier runs were made. Check the repo's multi-seed wrapper if one exists.

Pre-backtest diagnostics, read-only, a few minutes:
```
PYTHONPATH=src:scripts uv run python ../betting_workspace/investigations/2026-10-03-lol-edge/work/pipeline-opus/prior_perm.py <new_catalog>
PYTHONPATH=src:scripts uv run python ../betting_workspace/investigations/2026-10-03-lol-edge/work/pipeline-opus/leaf_maps.py lol <new_catalog> ../betting_workspace/investigations/2026-10-03-lol-edge/work/pipeline-opus/leaf_maps_new.parquet
```
Expected, if F1 is the mechanism:
- member trees >80
- prior split share <10 %
- in-sample vs out-of-sample MAE gap <0.3 ¢ (now 0.70)
- out-of-sample gated DIR ≥2.4 ¢ (now 2.32 on member_00)

The size of the PnL effect is a guess. I expect +0.1 to +0.3 ¢ DIR and a higher Sep-archive markout than 1.19 ¢. If DIR does not move, F1 is only a cost in capacity, and the window question (window-dev) dominates.

**E2 — measure the replay regime on the same maps. Priority 2. Cost: a small code change + 3-seed backtest on 290 maps.**
- Add an env flag in `src/backtest/feed_schedules.py:276-305` `_resolve_lol_match` that returns `GridV1Plan` when `LOL_FORCE_GRID_V1=1`. Run the 290 Sep archive maps as grid-v1 with the LIVE catalog, and compare per map with LIVE.
- This tells whether the Jun–Aug synthetic edge (+20 to +27 per map) is inflated against archive-realistic replays (+14 per map). That matters before trusting any total-PnL comparison.

**E3 — population per period. Priority 2. Cost: read-only.**
- Rerun `src_by_period.py` per league and third for the 3 runs, and add LEC playoff dates. LEC lost −2,884 over 48 late-third maps.
- If LEC (or the late-Aug window) is the late collapse, the fix is admission (whitelist or period), not features. This overlaps leagues-dev; my number is the input.

**E4 — label-gate selection. Priority 3. Cost: prepare rebuild in `--output-dir` style + diagnostic only.**
- In `05_prepare_dataset.join_market_rows`, also store the ungated mid at +311 (no spread or age gate) for rows whose label is None.
- Compare |move| and sign versus current-state features on those 12.7 % of training rows. If they are large-move rows, relax the gate for labels: microprice, or spread < 10 ticks.

**E5 (optional, low) — cadence realism.** Set `LOL_GRID_V1_BANDS` to 5/6/5/4 s and the LoL first tick to about 4 s, then rerun grid-v1. Expect a small change in fills (F7).

## Top-3 by expected impact on late-third PnL
1. **F1, per-map memorization (E1).** This is the only LoL-only defect with a large, measured train-side effect. Watch the Sep-archive markout (now 1.19 ¢) and the late-Aug synthetic PnL (now −1,523).
2. **F2, population and period (E2, E3).** The late-third result is set by LEC (−2,884 over 48 maps) and the 22–31 August window (−1,523 over 79 maps), not by the September archive maps (+4,111). A fix that does not move these two cells will not move the late third.
3. **F9, label-gate selection (E4).** 12.7 % of training rows ≤540 have no label. The size of the effect is unknown.

All other discrepancies (F3–F8, F10) are measured or reasoned to be ≤0.035 ¢ of delta, or negligible in fills.

## Scripts
All files are in `work/pipeline-opus/`. All are read-only. Run each from `esports-trader/` as `PYTHONPATH=src:scripts nice -n 10 uv run python ../betting_workspace/investigations/2026-10-03-lol-edge/work/pipeline-opus/<script> [args]`.

| script | args | what it measures | output |
|---|---|---|---|
| `spawn_pause_check.py` | `<out.parquet> 300` | spawn anchor and pause clock on sampled maps (F8) | `spawn_pause.parquet` |
| `grid_vs_livestats_clock.py` | `<out.parquet>` | GRID archive vs livestats: `second` offset, arrival lag, nw/xp parity (F3, F4, F6) | `grid_clock.parquet`, `grid_clock_ticks.parquet` |
| `xp_gap_check.py`, `xp_gap_nwaligned.py`, `xp_shift.py` | `grid_clock.parquet` | XP parity and timing (F5) | — |
| `cadence_check.py` | none | archive tick cadence and first tick (F7) | — |
| `early_fills.py` | none | BUY fills by game second and source | — |
| `src_by_period.py` | `[run_dir]` | late-third split by source, sub-period, league and patch (F2) | — |
| `orientation_check.py` | none | side orientation (ruled out) | — |
| `leaf_maps.py` | `lol\|dota <model_dir> <out.parquet>` | distinct and effective maps per leaf, exact member_00 rebuild (F1) | `leaf_maps_lol.parquet`, `leaf_maps_dota.parquet` |
| `leaf_contrib.py` | `<model_dir> leaf_maps_lol.parquet` | value of few-map leaves out of sample (F1) | — |
| `prior_splits.py` | none | split share and node size on the prior, LoL vs Dota (F1) | — |
| `prior_perm.py` | `<model_dir>` | map-level prior permutation, in- vs out-of-sample (F1) | — |
| `prior_perm_dota.py` | none | the same permutation for Dota | — |
| `perturb_check.py` | `<model_dir>` | delta sensitivity to `second` +3 and xp +2 (F3, F5) | — |

`scripts/compare_lol_grid_livestats.py data/lol_dual_feed/<run>` is the repo script I used for the dual-feed check.
