# glm-hist: independent check of LoL history-feature parity across train, backtest and live

Status: FINAL

Scratch: `work/glm-hist/` (scripts `a_setup.py` … `f_item5b_dota_sched.py`, outputs `maps.parquet`, `item1_grid_ticks.parquet`, `item1_sched_ticks.parquet`, `item2_ticks.parquet`, `item2_cols.parquet`, `item3_columns.parquet`, `item3_families.parquet`, `item4_sched_pauses.parquet`, `item5_dota_grid.parquet`, `item5b_dota_sched.parquet`). Serve-side replays use the exact backtest tick generation: **LoL runs `lag_seconds=0`** (hard-coded in the LoL selection, `run.py:1500`; `BACKTEST_LAG_SECONDS=10` is the Dota default — I verified this the hard way, see F1.4) and the 11s `LOL_SOURCE_LAG_SECONDS` moves only the decision/market clock. Evidence labels: **verified** = traced in code + measured on data here; **likely** = traced but not measured end-to-end; **speculative** = reasoning only.

## Verdict

**History semantics are consistent between train and serve; the ±30s pivot policy is a real but small distortion, and it is not the dominant train/serve gap.** Verified piece by piece:

1. **Warmup NaN parity is exact.** Training NaN is pure warmup (measured 22.2/33.3/44.4/55.5/66.5% per lag family = exactly 120..360/541 rows, identical across all 8 train months — the dense training tape has zero holes in 0..540). On the real received schedule tape, decision NaN is warmup-only to within 0.7 points (1m: 13.6% vs 14.3% window-uniform theory; 5m: 70.5% vs 71.4%). Pooled per-column, serve has ≤0.2 points more NaN than the training-style vector (item2_cols: serve_only_nan 0.10-0.20% vs dense_only 0.006-0.03%).
2. **Grid serve differs from training only through the ±30s snap and tick dropping** (received rows are bit-exact vs the dense tape at shared seconds — state-only score delta ≡ 0.0000 on all 20,793 grid window ticks). The snap policy flips **0.58-1.45%** of grid decisions across ±0.02 (mean |Δscore| 0.0006-0.0008), concentrated in [360,480) where `total_5m` turns on (1.0-3.4%). Pivots land 3.3-5.1s from targets on average, max 30s = the policy cap.
3. **The dominant serve/train divergence is not history and not the tape: it is schedule mode's state columns.** Of the schedule era's 5.82% flip02 vs the training-style vector, 5.54 points come from the live reducer's second-assignment misalignment vs the offline rebuild (nw mean |Δ| 46-60 gold ≈ 1-2s of game time, deaths agree 99%) and only 0.88 points from history. The two are near-additive (delta-series corr 0.10).
4. **Invalid ticks are a grid-backtest artifact, not a live risk.** The real received schedule tape drops 0.086% of buy-window ticks (1/290 maps); the synthetic grid cadence tape drops 0.31-5.37% by month (Sep worst) because dense-tape holes become dropped ticks while the real tape freezes instead (76.5 duplicate-second overwrites per map keep pivots alive through pauses). Long invalid runs sit late-game (S≥1300) except one Sep grid map with a mid-game hole (S=282-1108, partly in-window).
5. **Did the ±30s policy cause worse tails? At decision level, no.** The only localized amplification is the 5m warmup-snap band [360,376) (first 16s of `total_5m` availability, the top-gain history block): direction flips 2.01% vs 0.93% elsewhere — but 0.02-threshold flips are *lower* there (0.91% vs 1.65%). The 1m warmup-snap band [120,136) is not elevated at all (0.50% vs 0.88%, mean |Δ| 0.00032 vs 0.00070). So the 76-floor snap slightly increases *direction* noise exactly where the most important history family activates, but does not push decisions across trade thresholds. Sep's worse grid numbers come from late-start archived tapes and holes (F5), not from the policy. (hist-dev independently confirms at PnL level: corr(Δpnl, invalid) ≈ 0 across seeds, worst maps have zero dropped ticks.)

Caveat: train and serve populations are temporally disjoint (train 2025-10..2026-06, validation 2026-06..09), so parity is also drift.

## Findings

### F1. Semantics of the policy: what "invalid tick" and "NaN" actually are (verified, code + math)

Read from `src/shared/utils/dota_features.py` (not from other agents' notes):

- `history_feature_block` (lines 241-288): a lag column is NaN iff its target second is `< start_second` (60) or no taped pivot lies within `max_pivot_gap_seconds` (30). Eligibility is `target >= start_second` — **not** `>= first recorded second`. A missing pivot cascades NaN through the remaining minute-diffs of that field (`previous` chain), and `game_total_5m_*` compares against the 5m pivot only.
- `SnapshotHistory.check_required_lags` (338-360): a tick is dropped (invalid) iff a target `>= max(60, first_recorded_second)` has no pivot within 30s. Targets below `first_recorded_second` are "warmup" and keep the tick valid — but they still get *snapped forward* by the block if within 30s.
- Training attaches history with `max_pivot_gap_seconds=0.0` on the dense per-second tape; grid-v1/schedule/live use 30s on a cadence/received tape (`attach_history_features` vs `SnapshotHistory.derived`).
- **F1.4 (verified, `run.py:1500` + `signals.py:521,523,528-543`): LoL backtests run with `lag_seconds=0`** — the LoL selection hard-codes it (the `BACKTEST_LAG_SECONDS=10` default is the Dota path only), so feed second = dataset second and serve history targets are exact game seconds. The 11s `LOL_SOURCE_LAG_SECONDS` shifts only the decision clock (`decision_lag_ns`, market anchoring) and is enforced equal across model.json/backtest-audit (`lol_inputs.py:85-97`). My replays use lag 0 for LoL and lag 10 for Dota, matching each game's actual run configuration. (I first assumed the shared 10s default applied to LoL and reran everything at lag 10; that configuration does not exist for LoL — numbers in this report are from the correct lag-0 reruns, which exactly reproduce the backtest tick sets.)

Consequences, verified by replay below:

1. On **valid decision ticks the only NaN source is warmup** (target < 60), exactly like training. The drop_gap_ticks policy turns what training kept as NaN-cascade rows (target inside a tape hole) into *dropped ticks* at serve time instead.
2. On grid-v1, targets in `[60, 76)` always snap forward to the first tick, up to 16s "less history" than nominal, e.g. the first 5m total (decision S=360) references second ~76 instead of 60.
3. Pivot snapping is not rare: mean |pivot − target| is 3.3-5.1s per lag on decision ticks, max 30s (policy cap).

### F2. Item 1 — invalid-tick counts per month (verified, replay of exact backtest tick generation)

Population: the LIVE baseline run's 1239 maps (949 grid_v1 + 290 schedule, all schedule maps in 2026-09; Jun/Jul/Aug 100% grid_v1). Grid_v1: ~100 sampled maps/month, seeds 0/1/2, exact `select_cadence_rows(game="lol", lag_seconds=0)` + `walk_feed_ticks` replay (`received_ns = state_ts_us*1000 + 11s`, stale 45s). Schedule: all 290 archived FeedSchedules replayed like `_schedule_replay` (record_mask = not terminal, decision needs exact `game_features` row in [60,480)).

Buy window (ticks in game-second [60,480), where entries can happen; grid numbers seed-0 per-map means):

| month | mode | maps | win decisions/map | win invalid/map | invalid share of win ticks | maps with ≥1 win-invalid |
|---|---|---:|---:|---:|---:|---:|
| 2026-06 | grid_v1 | 100 | 61.6 | 0.19 | 0.31% | 5% |
| 2026-07 | grid_v1 | 100 | 66.2 | 0.36 | 0.54% | 4% |
| 2026-08 | grid_v1 | 100 | 59.6 | 1.07 | 1.76% | 7% |
| 2026-09 | grid_v1 | 44 | 46.9 | 2.66 | **5.37%** | 22.7% |
| 2026-09 | schedule (real received tape) | 290 | 80.1 | 0.069 | **0.086%** | 1/290 |

(3-seed pooled win shares: 0.34 / 0.57 / 1.60 / 5.43% — seed noise ≈ ±0.15 points.)

Over the whole map (all seconds, where sell/exit decisions live; 3 seeds pooled):

| month | mode | invalid/ticks | longest invalid run | where the long runs sit |
|---|---|---:|---:|---|
| 2026-06 | grid_v1 | 3.08% | 90 | S=554-1751 (one map whose tape starts at 331); also 1377-1649 |
| 2026-07 | grid_v1 | 1.73% | 56 | S=1486-1757 |
| 2026-08 | grid_v1 | 2.89% | 58 | S=440-2303 |
| 2026-09 | grid_v1 | 6.46% | 79 | S=639-2085; one map 282-1108 (partly *inside* the window) |
| 2026-09 | schedule | 0.25% | 79 | S=1416-1686 (12/290 maps have any invalid) |

Interpretation (verified by drill-down `windows=` output):

- **The synthetic grid_v1 cadence tape is 10-60x more invalid-prone than the real received tape in the same month** (Sep: 5.37% vs 0.086% of window ticks). Cause: grid_v1 samples the dense validation tape, which has *game-second holes* wherever frames stopped >2s (detected pauses / collector gaps) — a tick whose lag target lands in a hole is dropped. The real received schedule instead *freezes*: the game clock stands still and the tape accumulates repeated seconds (76.5 duplicate-second overwrites per map), so pivots survive pauses and almost nothing is dropped. (hist-dev traced the same holes to skipped market rows: corr(invalid, row-holes>60s)=0.81 — complementary root cause for the same artifact.)
- Long invalid runs are mostly late-game (S≥~1300): they suppress sell/exit decisions after the buy cutoff, not entries. Sep is the worst month in both senses: whole-map 6.5%, window 5.37%, and 25% of Sep grid maps (11/44) have a late-starting tape (first tick > 90; up to 847), vs 1-12% in earlier months. One Sep map has a mid-game hole (S=282-1108) that kills in-window ticks.
- Schedule maps' received tape starts at second p50=4 (p5=−4: pre-game snapshots exist; record drops <60), so the [60,76) warmup-snap band of grid_v1 does not exist on schedule/live.
- `no_feature` ticks: 0/290 maps — every fresh, valid schedule tick in [60,480) has its exact dense-tape row, i.e. schedule mode mixes a dense-tape base row with received-tape history by construction.

### F3. Item 1b — warmup NaN on decision ticks matches training at the edge; late-start tapes add serve-only NaN (verified)

Per-map means, grid_v1 seed 0, window decisions (61.6/66.2/59.6/46.9 per map by month):

| month | 1m NaN frac | of which warmup S<120 | 5m NaN frac | of which warmup S<360 |
|---|---:|---:|---:|---:|
| 2026-06 | 10.1% | 8.8% | 64.6% | 61.5% |
| 2026-07 | 8.2% | 8.4% | 65.1% | 63.8% |
| 2026-08 | 12.2% | 8.9% | 67.2% | 60.6% |
| 2026-09 | 16.3% | 9.7% | 73.9% | 55.6% |
| 2026-09 schedule | 13.6% | ~14.3% (theory) | 70.5% | ~71.4% (theory) |

The warmup share (S<120 for 1m, S<360 for 5m) reproduces the training NaN edge exactly (target<60 ⇒ NaN both sides). The excess (e.g. Sep 1m: 16.3% vs 9.7% warmup) is maps whose archived tape starts late (first tick > 90): their lag targets below the tape start have no pivot within 30s, producing NaN on valid ticks. Training *can* see the same for late-start maps in its own split (dense tape = same archive), so this is only a mismatch to the extent the map populations differ; the [60,76) snap band and ±30s snapping (F1) are the unconditional value distortions.

### F4. Item 3 — importance vs availability: 11.3% of model gain sits in the 60 history columns; `total_5m` is half of it and is NaN on 65-76% of buy-window decisions (verified)

Gain importance (`feature_importance(importance_type="gain")`, normalized per member, averaged over the 10 research boosters — byte-identical, `shasum`, to the LIVE `hist-policy-20261003` catalog; `model.json train_matches=2551`, 77 features):

- history columns: **11.31%** of total gain; non-history 88.69%.
- by family: `total_5m` **5.89%** (52% of the history gain; top columns `game_total_5m_dire_nw` 1.39%, `game_total_5m_radiant_nw` 1.06%), `change_1m` 1.59%, `change_2m` 1.29%, `change_3m` 1.18%, `change_4m` 0.69%, `change_5m` 0.67%.

Availability on decision ticks (window 60..480, seeds 0/1/2; code-true adjacent-pivot semantics: `change_km` NaN iff pivot_{k-1} or pivot_k missing; `total_5m` iff pivot_5 missing):

| family | gain share | train NaN (dense, rows 0..540) | grid NaN Jun/Jul/Aug/Sep | schedule NaN Sep | grid mean snap (s) | snap>0 share | dup-pivot share (schedule) |
|---|---:|---:|---|---:|---:|---:|---:|
| change_1m | 1.59% | 22.2% | 13.1/9.8/14.7/25.2% | 13.7% | 3.3-4.5 | 64-76% | 13.8% |
| change_2m | 1.29% | 33.3% | 24.0/20.8/25.9/38.5% | 32.6% | 3.6-4.4 | 53-67% | 10.5% |
| change_3m | 1.18% | 44.4% | 38.3/35.6/40.7/51.6% | 45.3% | 3.7-4.8 | 42-55% | 8.9% |
| change_4m | 0.69% | 55.5% | 52.8/50.6/54.2/64.8% | 56.1% | 3.9-4.8 | 30-43% | 8.5% |
| change_5m | 0.67% | 66.5% | 66.6/65.4/67.6/75.7% | 70.6% | 4.5-5.1 | 22-31% | 6.1% |
| total_5m | 5.89% | 66.5% | 66.6/65.4/67.6/75.7% | 70.6% | 4.5-5.1 | 22-31% | 6.1% |

(schedule snaps are slightly smaller: 3.05-3.28s; snap>0 1m 72.0% → 5m 24.0%.)

Key reads:

- **Training NaN is pure warmup**: measured train-split NaN rates are exactly 120/541, 180/541, 240/541, 300/541, 360/541 per family — identical across all 8 train months (2025-10..2026-06) — i.e. the dense tape has **no holes at all in seconds 0..540** for essentially every train match (any hole would lift the rate above warmup). The fit never saw post-hole NaN cascades.
- On the same window [60,480), warmup NaN parity is near-exact for the real schedule tape (1m 13.7% vs theoretical 14.3%, 5m 70.6% vs 71.4%). Grid's deviation (Sep 1m 25.2% vs 14.3%) is serve-only NaN from late-starting archived tapes (F5).
- **The train and serve populations are temporally disjoint**: `split.parquet` train = 2025-10..2026-06 (2713 matches), validation = 2026-06..2026-09 (2195). The backtest months barely overlap the training era — parity questions are also drift questions.
- Staleness: pivots are not exact-second at serve — 64-76% of 1m pivots and 22-31% of 5m pivots are snapped (mean |pivot−target| 3.3-5.1s, max 30s = the policy cap). On the real schedule tape, 6.1-13.8% of pivots land on a repeated (frozen) second, i.e. point at an overwritten snapshot.
- The single most important history family (`total_5m`, 5.9% of all gain) carries information only on the 24-34% of window decisions after second 360 — where its pivots are the most-snapped (4.5-5.1s mean) and where the grid flip rates in F6 peak.

### F5. Late-start archived tapes: training always sees full history, grid-v1 serve often does not (verified)

For every sampled grid map whose first archived tick is > 90 (Jun 9/100, Jul 1/100, Aug 12/100, **Sep 11/44**), `game_features` (the dense training tape) has min second 0 and **all 541 rows in 0..540**, while `validation.parquet` (the archived received tape the backtest serves from) starts at 94-1401 and has 0-447 of the 541 seconds. Values agree bit-exactly at shared seconds (F6: state-only delta ≡ 0.0000 on all grid ticks), so these maps' early game simply was never *received/archived*: the trader could not have seen it, and the backtest correctly has no ticks there — but the *model* was fit on rows whose dense tape always covered 0..540, so for these maps serve-time warmup NaN extends past the training warmup edge (targets below tape start have no pivot within 30s). This is the entire source of the "excess" NaN in F4's Sep grid column, and it barely touches the LIVE baseline's Sep, which runs schedule-mode for 290 of 334 maps (received tape starts at p50 second 4).

### F6. Item 2 — dense-vs-received value parity and model-score deltas: grid serve differs from training only via snapping (≤1.45% flips); the schedule era's divergence is 6x larger and lives in the state columns (verified)

Method: for every window decision tick of the item-1 populations (grid seed 0: 20,793 market-backed ticks over 344 maps; schedule: 20,742 of 23,223 ticks over 290 maps), build (a) the training-style dense vector (state+prior+history from `game_features` at the exact second, exact pivots, gap 0 — the `attach_history_features` call at `dota_features.py:437-444`) and (b) the serve vector exactly as the backtest builds it: grid = state+prior from the received validation row (same second, lag 0), history via `SnapshotHistory.derived` (gap 30s) over the cadence tape, current levels = received row (mirrors `_shifted_feed`); schedule = state from the archived tick + prior from the dense row, history from the received schedule tape (mirrors `build_schedule_match_signals`). Identical market columns on both sides, scored with the 10-member mean.

First, a structural fact the decomposition relies on (verified, bit-exact): **for grid maps the received validation row equals the dense `game_features` row at every shared second** — swapping only the 12 state columns changes no prediction (state-only delta ≡ 0.0000 on all 20,793 grid ticks). Grid-v1 serve therefore differs from training *only* through history snapping and tick dropping. Schedule ticks are different: their `game_second` comes from the live reducer's clock, and their state columns do not match the dense rebuild.

| mode | n | mean \|Δscore\| | p95 \|Δ\| † | 2¢-threshold flips | sign flips | history-only flips | state-only flips |
|---|---:|---:|---:|---:|---:|---:|---:|
| grid_v1 Jun | 6,156 | 0.0007 | 0.0023 | 0.58% | 0.75% | 0.58% | 0.00% |
| grid_v1 Jul | 6,616 | 0.0006 | 0.0023 | 0.67% | 0.73% | 0.67% | 0.00% |
| grid_v1 Aug | 5,959 | 0.0006 | 0.0023 | 0.96% | 0.72% | 0.96% | 0.00% |
| grid_v1 Sep | 2,062 | 0.0008 | 0.0023 | 1.45% | 0.44% | 1.45% | 0.00% |
| schedule Sep | 20,742 | 0.0053 | 0.0187 | **5.82%** | **9.18%** | 0.88% | **5.54%** |

(score deltas in probability units; † p95 is the pooled per-mode value; 2,481 schedule ticks lacking a validation-row market used a constant 0.5 on both sides and are excluded here; including them changes nothing material.)

The decomposition (hybrid vectors: dense state + received history, and received state + dense history) shows where the schedule-era divergence really lives:

- history swap alone: mean |Δ| 0.0008, flip02 **0.88%** — the ±30s pivot policy per se is a sub-1% decision-changer on the real tape;
- state swap alone: mean |Δ| 0.0050, flip02 **5.54%** — the live reducer's second assignment vs the offline rebuild misaligns which second a snapshot lands on (nw mean |Δ| 46-60 gold ≈ 1-2s of game time; deaths agree 99%), and that, not the history policy, is the dominant train/serve gap in the schedule era;
- the two effects are near-additive (5.82% ≈ 5.54% + 0.88% − overlap; delta-series corr 0.10).

By band, grid flips concentrate exactly where `total_5m` turns on: 0.00% in [60,120), 0.28-0.45% in [120,180), 0.30-0.67% in [180,360), **1.03-3.39% in [360,480)** (Sep 3.39%). Schedule flips are flat ~4.7-8.0% across bands (state misalignment is everywhere).

Per-column value parity, pooled over both modes (serve-snap vs dense-exact, both-finite ticks; `item2_cols.parquet`): exact-equal 16.9% (1m) to 7.9-13.9% (2-5m/total) — most decisions reference a snapped pivot; mean |Δ| 58-74 gold-points per nw field (p99 ~375-393), on `total_5m` 66 mean / 381 p99. NaN parity per column is tight: serve NaN − dense NaN ≤ 0.2 points (serve_only 0.10-0.20% vs dense_only 0.006-0.03%). Relative distortion is largest in the mid-lag change features (e.g. `game_change_5m_radiant_xp_adv` mean |Δ| = 53% of the dense value) — the received-vs-dense offset noise is of the same order as the change itself — yet the *model score* barely moves on grid ticks, because state and history offsets are coherent (both sides come from the same tape).

### F7. Item 4 — LoL specifics: warmup-snap bands, pauses game-vs-wall, and the 76 floor (verified)

**(a) Warmup-snap bands** (decisions whose lag target lands in [60,76) snap forward up to 16s to the first tick ~76). Grid, full serve-vs-dense deltas:

| band | n | mean \|Δ\| | p95 \|Δ\| | flip02 | sign flips |
|---|---:|---:|---:|---:|---:|
| 1m-snap [120,136) | 605 | 0.00032 | 0.00118 | 0.50% | 0.50% |
| 1m-rest [136,480) | 18,533 | 0.00070 | 0.00244 | 0.88% | 0.77% |
| 5m-snap [360,376) | 993 | 0.00147 | 0.00482 | 0.91% | **2.01%** |
| 5m-rest [376,480) | 6,424 | 0.00092 | 0.00308 | 1.65% | 0.93% |

- The **1m band is not amplified**: its deltas are actually *smaller* than the rest (0.00032 vs 0.00070) — the ≤16s forward snap of the 1m pivot is mild because `change_1m` subtracts two pivots whose snaps partially cancel.
- The **5m band shows a direction-flip excess only**: mean |Δ| 1.6x rest, sign flips 2.2x rest (2.01% vs 0.93%), but 0.02-threshold flips are *lower* than rest (0.91% vs 1.65%). Mechanism (likely): `total_5m` = current − pivot_5, and in [360,376) the pivot_5 target lands at ~60, snapping up to 16s forward — the oldest pivot carries the full snap error with no cancellation, right where the top-gain history block first turns on.
- So the `GRID_V1_FIRST_TICK_SECOND=76` floor costs a bounded, early-activation distortion; it does not create threshold-crossing excess.

**(b) Schedule pauses, game vs wall (290 maps, real received tapes):**

- **Desync** (wall span − game span): p50 **−4.72s** (≈0), mean 54.6s, p95 **263.5s**, max 1,354s; **69/290 maps > 60s**. corr(desync, Σ wall-gaps>45s) = **0.927**, corr(desync, dup_ticks) = 0.067 — desync *is* receive gaps (pauses), not frozen repeats. Per map: 1.48 gaps > 45s totalling 138.5s; max wall gap p50 72.9s, p95 242s, max 1,099s.
- **Frozen repeats are universal and harmless to the tape**: every one of the 290 maps has them — 61.5 frozen game-seconds/map, 76.6 duplicate ticks/map = **18.2% of all ticks** — but because the reducer re-sends the same game second, the tape's pivots stay alive through freezes (F2: 0.086% window invalids).
- **The `paused` flag cannot detect freezes**: it fires on only 2.17 ticks/map (p50 1, max 13) — ~30x fewer than the frozen stretches. The backtest audit has no per-tick pause columns (map-level `pause_seconds` from livestats only).
- Schedule tapes start pre-game (first tick p5 −4, p50 4, p95 75, max 378), so the [60,76) warmup-snap band never exists on schedule/live — it is a grid-backtest artifact on maps whose archive starts late.

### F8. Item 5 — Dota comparison (2026-09): same policy, same warmup, cleaner received tape; LoL's extra problems are late-start archives and cadence holes (verified, with one data caveat)

Population: the production Dota model (model.json 2026-10-01, 77 features; `split.parquet` is **train-only**, 2,534 matches, 280 in 2026-09 — the Sep population below is the model's own train matches, fine for tape-quality counts, flagged in-sample). Sampled 100 Sep matches; 92/100 have archived feeds (archive index: 397 dota archives, 366 admitted). Dota replays use lag 10 (the Dota default). Caveat: `data/new_processed/dataset/*` was rewritten today 11:26 by the parallel Dota rebuild (the new `training_dataset.parquet` has 0 Sep matches so far); the production model/split predate it.

| metric, 2026-09 | LoL grid_v1 | Dota grid_v1 | LoL schedule | Dota schedule |
|---|---:|---:|---:|---:|
| maps | 44 | 100 | 290 | 337 |
| win-invalid share of win ticks | 5.37% | **3.93%** | 0.086% | **0.005%** |
| maps with ≥1 win-invalid | 22.7% | 22.0% | 1/290 | 2/337 |
| whole-tape invalid/ticks | 6.46% | 4.62% | 0.25% | 0.30% |
| longest invalid run | 79 | 82 | 79 | 482 (all late-game, 0 in window) |
| 1m / 5m decision miss (warmup theory 14.3/71.4%) | 16.3 / 73.9% | 15.1 / 69.2% | 13.6 / 70.5% | 16.2 / 68.0% |
| snap 1m / 5m mean (s) | 3.3-4.5 / 4.5-5.1 | 4.9 / 6.1 | 3.3 / 3.1 | 3.4 / 3.9 |
| dup seconds/map | n/a (dense) | n/a (dense) | 76.5 | **587.8** |
| first feed tick p50 | 76 | 48 | 4 | −84 |

- Dota trains history on the **minute tape** (`game_history_minutes.parquet`): 100% of pivots are exactly minute-aligned (`game_second % 60 == 0` for all sampled rows; all 280 Sep matches present), attached with gap 0; LoL trains on the dense second tape with gap 0. Both are hole-free: Dota's dense second tape shows pure-warmup NaN (180/601, 240/601, 300/601, 360/601, 420/601 on rows −60..540) — zero holes for the sampled Sep maps.
- Dota grid is dirtier than its own schedule (3.93% vs 0.005% window invalids) for the same reason as LoL (dense-tape holes vs received-tape freezes) — the pattern is game-agnostic. Dota's received tapes freeze far more (588 dup seconds/map ≈ 40% of ticks, vs LoL 76.5) but start pre-game (joined at −84s), so warmup pivots always exist and window invalids vanish.
- LoL vs Dota differences are population/cadence, not policy: LoL Sep grid is worse (5.37% vs 3.93%) mostly via late-start tapes (11/44 vs 7/100 maps with first tick >60); Dota's coarser cadence bands (11/8/7/6s vs LoL 8/6/5/5s) give larger snaps (4.9-6.1s vs 3.3-5.1s); `GRID_HISTORY_POLICY` (start 60, gap 30), warmup NaN and the cap-30 snap behavior are identical across games. (hist-dev's Dota grid replay over all 297 Sep validation maps × 3 seeds gives 5.18% whole-tape invalids — same order, different population.)

### F9. Comparison with hist-dev (read only after my own numbers were final)

hist-dev's FINAL report and mine agree on every structural claim, measured independently: shared mechanics (`HistoryPolicy`/`SnapshotHistory`/`history_feature_block`); warmup-NaN parity (their xor_nan ≤0.5% synthetic / ≤0.14% archive vs my ≤0.2-point per-column NaN gap); the tape/sparsity channel is a non-factor (their mean |Δpred| 0.00064 grid / 0.00073 archive vs my 0.0006-0.0008 grid, history-only 0.88% schedule flips); grid invalids driven by dataset holes (their corr 0.81 with >60s row holes, 0.63 with skipped market rows — the market-coverage root cause behind my F5 late-start/hole mechanism); live/schedule drops inert (their 20 in-window ticks over 290 maps vs my 0.086%/1-of-290); history = 11.3-11.4% of gain with `total_5m` structurally NaN until 360; and tails not driven by drops (their corr(Δpnl, invalid) ≈ 0 matches my decision-level verdict).

Differences worth noting:

1. **hist-dev was right about `lag_seconds=0` for LoL** (`run.py:1500`); I initially assumed the shared `BACKTEST_LAG_SECONDS=10` default and had to rerun everything. My report's numbers are the corrected lag-0 replays.
2. **Populations**: hist-dev scored all 1246 eligible maps (their pooled grid invalid 2.52% whole-tape / ~1.1% window); I replayed the LIVE baseline run's 1239-map population month-by-month (window 0.31-5.37%), which exposes the Sep deterioration and the 22.7% of Sep grid maps with ≥1 window invalid that a pooled number hides.
3. **Flip definitions**: hist-dev reports ~0.01% threshold flips at matched levels; I count any sign-aware crossing of ±0.02 (0.58-1.45% grid, 5.82% schedule incl. state). Both support "sub-1% for the tape channel"; my schedule number additionally includes the state misalignment they separately quantify as the domain shift (their mean |Δpred| 0.008-0.010 live-vs-dataset, −0.4¢ in-window bias — consistent with my state-only 5.54% flip02 / mean |Δ| 0.0050, and their ~3-4s label-lead explanation matches my 1-2s nw offset scale).
4. My additions: month trend, warmup-snap band ablation (F7a), pause/desync stats and the paused-flag undercount (F7b), dup-pivot staleness, the importance-vs-availability table on the LIVE population, and the Dota schedule real-tape replay (their Dota number is grid-only).
5. hist-dev additions I did not measure: the pre-histfix rule's 26.4% archive decision miss (vs 0.09% now — the policy genuinely fixed live input quality), and the PnL-level tail check.

## What I ruled out

1. **Serve/train NaN-regime mismatch — ruled out.** Serve NaN ≈ dense NaN per column (≤0.2 points pooled; schedule 1m 13.6% vs 14.3% theory). The model never meets a NaN pattern at serve that it did not see in training, outside warmup. (verified)
2. **Training-tape holes — ruled out.** Train NaN = exact warmup fractions in all 8 months, so the dense tape is hole-free in 0..540; the fit never saw post-hole NaN cascades, and those cascades at serve become dropped ticks, not NaN rows (F1). (verified)
3. **"Important columns are the unavailable ones" — ruled out.** `total_5m` (5.9% gain) is NaN exactly when training had it NaN (warmup); its serve values differ by ≤74 gold mean with flips bounded by the 1.45-point grid budget; no family's gain sits in a chronically-NaN regime. (verified)
4. **Grid state-column divergence — ruled out.** Received rows are bit-exact vs dense at shared seconds (state-only ≡ 0.0000 on all 20,793 grid ticks), so grid serve deltas are pure history policy + tick dropping. (verified)
5. **The 76-floor warmup snap as a threshold-flip driver — ruled out.** The 1m band [120,136) is *milder* than the rest (0.50% vs 0.88%); the 5m band [360,376) adds direction flips (2.01% vs 0.93%) but fewer threshold flips (0.91% vs 1.65%). (verified)
6. **Pauses as a schedule-mode hole source — ruled out.** Freezes create duplicate seconds, not holes (0.086% window invalids); desync = receive gaps (corr 0.927) and lands outside the window; the 18.2% frozen-tick share costs staleness (dup-pivot 6.1-13.8%), not availability. (verified)
7. **The `paused` flag as a freeze detector — ruled out** (2.2 ticks/map vs 61.5 frozen seconds/map). (verified)
8. **A hypothetical LoL lag-10 serve relabel — ruled out as a real configuration.** `run.py:1500` pins `lag_seconds=0` for LoL; a lag-10 rerun produces 8.6% grid flip02 dominated by a 7.9-point state shift, but that configuration does not exist for LoL (it is Dota's). (verified)
9. **Dota minute-tape misalignment — ruled out** (100% minute-aligned pivots); **Dota dense-tape holes — ruled out** (pure-warmup NaN). (verified, with the same-day-rebuild caveat)

## Proposed experiments

1. **Snap-aware training (the history-side fix).** Augment training pivots with serve-realistic jitter — sample each lag pivot from the dense tape the way `GRID_HISTORY_POLICY` does at serve (nearest within ±30s, cadence-band offsets, including the warmup-snap forward case) — or attach history with `max_pivot_gap_seconds=30` on cadence-subsampled tapes instead of gap-0 dense rows. Expected to absorb the 0.58-1.45% grid flip02 and the 5m-band direction excess. Cheapest test: retrain the 10 members with jittered pivots, rerun the LIVE baseline, compare flip02 by band and CVaR. All in `esports-trader` (train/attach path only).
2. **Policy A/B at serve (no retrain).** Rerun the Sep schedule-mode backtest with (a) `max_pivot_gap_seconds` 10 vs 30 vs 60, and (b) drop-gap-ticks disabled (serve NaN rows like training instead of dropping ticks). One-flag change in the backtest's `history_policy` override; measures the PnL cost of the invalid-drop policy and the gap budget directly on the real tape, where the effect is small enough that a null result is informative.
3. **Warmup-snap band ablation.** For grid-mode maps, serve the dense-exact `total_5m` pivot for decisions in [360,376) (the tape floor makes the serve pivot up to 16s off) or drop those 993/20,793 ticks; compare sign flips. Isolates F7a's direction excess.
4. **State-side alignment (flagged, not history):** schedule mode's 5.5-point state misalignment (live reducer second assignment vs offline rebuild) is the largest measured serve/train gap; hist-dev traces ~80% of it to a ~3-4s label lead (`second = live_clock − feed_delay`). The fix belongs to the collector/reducer alignment (hist-dev's experiment 1: align dataset fields to the live code path) and is out of this brief's scope.
5. `poly-maker` is frozen — no maker-side changes proposed or planned here.

## Scripts

All in `work/glm-hist/`; run from `esports-trader/` with `nice -n 10 env PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python <script>`.

- `a_setup.py` — LIVE-run population: 1239 maps (949 grid_v1 + 290 schedule), month/mode splits; writes `maps.parquet`.
- `b_item1_ticks.py` — item 1 replay (grid_v1 cadence with LoL `lag_seconds=0` per `run.py:1500` + schedule received-tape), per-map tick/invalid/stale/decision/lag-miss/snap stats; writes `item1_grid_ticks.parquet`, `item1_sched_ticks.parquet`.
- `b_item1_table.py` — tidy per-month F2/F3 tables.
- `c_item3_importance.py` — item 3: gain shares (10 boosters, `shasum`-checked vs LIVE catalog), train-side NaN per family/month, serve-side NaN/snap/dup-pivot per family; writes `item3_columns.parquet`, `item3_families.parquet`.
- `d_item2_parity.py` — item 2: dense vs serve vectors (exact lag-0 grid construction + hybrid decomposition), per-column parity, score deltas/flips by month and band; writes `item2_ticks.parquet`, `item2_cols.parquet`.
- `e_item4_lol.py` — item 4: warmup-snap band deltas (from `item2_ticks.parquet`) + schedule pause/desync stats; writes `item4_sched_pauses.parquet`.
- `f_item5_dota.py` — item 5: Dota 2026-09 grid_v1 replay (dota bands, first tick 48, lag 10), minute-tape alignment, dense-tape NaN; writes `item5_dota_grid.parquet`.
- `f_item5b_dota_sched.py` — item 5: Dota schedule-mode real-tape replay over all 366 admitted archives; writes `item5b_dota_sched.parquet`.