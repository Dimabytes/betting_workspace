# LoL model-selection campaign

Date: 2026-08-28. Revised: 2026-08-28. Status: approved design.

The current LoL holdout is a tuning set. The campaign may overfit it. After the
campaign freezes the model and maker parameters, newly collected matches will
provide one untouched confirmation set. That confirmation is outside this
campaign.

## Goal

Select one LoL research model and one complete S2 maker configuration. Publish
the model only after the campaign has selected every parameter.

The campaign selects:

- the feature set;
- the label horizon;
- the training row grid;
- `BUY_CUTOFF_SECOND`;
- `MIN_ABS_DELTA`;
- `UNWIND_AFTER_SECONDS`;
- `MIN_ENTRY_PRICE`;
- `MAX_ABS_NW_DELTA_30`.

The last training second is no longer a campaign parameter. It is fixed at 540.

The campaign does not implement LoL live trading. It also keeps the order size,
latency, queue fill model, and other execution assumptions fixed.

## Fix the training window at 540 seconds

Every candidate trains on seconds `0..540` and quotes with
`BUY_CUTOFF_SECOND` at or below 540. The campaign does not test 660, 720, or
1200. Widening the window is dropped, not deferred.

This matches the Dota model, which trains on `-60..540`.

## Existing evidence

The existing LoL S2 baseline is:

```text
features=base
target_horizon_seconds=300
train_second_max=540
buy_cutoff_second=540
min_abs_delta=0.01
unwind_after_seconds=300
min_entry_price=0.35
max_abs_nw_delta_30=350
```

Its completed run is
`data/backtests/lol_maker/validation_s2_delta01_cut540_nw350_p35_20260828T084440Z`.
Do not rerun this baseline.

### The old offline sweep is history, not input

`docs/experiments/lol-horizon-window-deltas.md` and
`docs/experiments/lol-horizon-sweep/` trained every model on seconds `0..1200`
and evaluated on 1 435 198 rows. Step 2 below rebuilds the table at
`second_max=540`, where the eval set is 690 527 rows over 1455 matches.

The two tables are not comparable. Archive the old one. Do not extend it, and do
not carry its rankings into a decision. In particular, do not reuse its claims
that deltas peak at horizon 180 or that roles lead from horizon 300 up.

What survives from the old pass:

- the `10..420`-second label grid is usable and costs little at the long end;
- horizon 420 costs 58 matches of 1513 in the label intersection;
- offline directional metrics never established the maker configuration.

A separate Dota maker experiment showed why the last point matters. A shorter
label improved the offline view but lost maker PnL because profitable positions
often stayed open much longer than the label horizon. The campaign therefore
treats the label horizon and the unwind time as separate parameters.

### Dota is the reference for signal strength

Both models are significant at horizon 300. LoL is much weaker.

| | Dota | LoL |
|---|---:|---:|
| DIR_300 | +1.500c | +0.560c |
| 95% CI, cluster bootstrap on `event_id` | `[+1.041, +1.927]` | `[+0.185, +0.932]` |
| DIR over CI half-width | 3.39 | 1.50 |
| no-move MAE | 8.116c | 9.809c |
| `dir_ratio` | 0.185 | 0.057 |
| MAE gain | 0.226c | 0.115c |
| eval rows | 278 426 | 757 922 |
| eval matches | 458 | 1513 |
| eval events | 200 | 739 |
| trees | 48 | 39 |

Dota numbers come from `data/new_model/research/model.json` on window
`second < 600`, reproduced with the LoL estimator. LoL numbers come from
`sweep_control_540_base.csv` on window `second <= 540`.

Read three things from this table:

1. LoL scores 2.7 times lower on DIR and 3.2 times lower on `dir_ratio`.
2. The LoL market is noisier by 21 percent, which explains part but not most of
   the gap.
3. Dota separates from zero at 200 event clusters. LoL does not separate cleanly
   at 739. More data does not rescue a signal that is three times weaker.

Use this table when deciding whether any LoL candidate is worth live trading.

### The training row grid is a live hypothesis

Dota and LoL already train on the same interval. They do not train on the same
rows.

```text
DOTA train   second -60..540   rows=16818     distinct seconds = 11
LOL  train   second   0..540   rows=1328090   distinct seconds = 541
```

Dota keeps one row per minute: `-60, 0, 60, ..., 540`. LoL keeps every second.
LoL has 49 times the row density over 1.35 times the matches, and the extra rows
are near duplicates of their neighbours.

This changes how LightGBM behaves. `min_data_in_leaf`, the tree count, and early
stopping calibrate differently at 17 thousand rows and at 1.3 million. Step 3
tests the Dota grid directly.

## Keep the research dataset as it is

The campaign reads `data/lol/processed/datasets_roles/`. That directory already
holds what every step needs:

- seconds `0..1200`;
- the 12 base features;
- the role columns;
- all 20 deltas from the five delta bases and the `30/60/120/180`-second
  windows;
- the `10..420`-second label grid.

Do not rebuild Stage 05. Do not add dragon deltas and do not add role deltas.
The production directory `data/lol/processed/datasets/` stops at second 540 and
stays untouched during the offline stage.

Keep role columns, role parsing, and the committed role-model artifacts until
step 4 picks a feature set. Roles enter the campaign as part of the `all` set,
so deleting the role path now would drop a live candidate.

Candidate training filters the parquet by `second_max`, the row stride, and the
selected label column. No step rebuilds the dataset. Keep
`scripts/lol_horizon_sweep.py` and the multi-horizon labels as permanent
research tools.

## Make the LoL model contract explicit

The LoL trainer accepts `target_horizon_seconds` and `train_second_max`. It
reads only the selected feature columns and rows. Each candidate model writes
these values to `model.json` with the feature list and dataset hashes.

Stage 07 computes the same delta columns for the complete backtest signal
stream. It does not use the Dota-only feature assertion in `backtest.signals`.
Training, Stage 07, and backtest inference must agree on feature names and
order.

Train candidates in named campaign directories. Do not rotate
`data/lol/models/research/` during the campaign. Publish only the final winner.

Pass maker parameters through the backtest CLI and record them in the manifest.
Do not edit shared Dota constants between LoL runs.

## Keep the validation split at full size

Do not move the split date. Do not shrink validation to match the Dota
proportion. LoL earns less per match than Dota and scatters more.

| | valid matches | mean `engine_pnl` | std | SE | mean/SE |
|---|---:|---:|---:|---:|---:|
| Dota | 447 | 1.445 | 10.81 | 0.511 | 2.83 |
| LoL | 1408 | 0.727 | 14.23 | 0.379 | 1.92 |

Dota resolves its mean at 447 matches. LoL does not resolve its mean at 1408.
LoL needs more matches than Dota, not fewer.

At 1408 matches and a paired correlation of 0.8, the smallest detectable paired
PnL difference is about 0.48. That is 66 percent of the anchor PnL 0.727.
Cutting validation to the Dota proportion leaves about 718 completed matches,
raises the standard error by a factor of 1.40, and lifts the threshold to about
0.67. At that threshold the campaign cannot decide anything.

Buy speed from sharding, not from dropping matches. The baseline run used 3
shards and about 5 hours. Use 8 to 10 shards on the 12-core host and expect 1.5
to 2 hours per candidate.

## Spend offline sweeps first and backtests last

One offline sweep costs minutes. One full S2 backtest costs 1.5 to 2 hours. The
campaign therefore screens with sweeps and confirms with backtests.

Do not read a maker decision out of a sweep. A sweep ranks candidates; only a
replay measures PnL.

## Step 1. Add a row stride to the sweep

Add `--row-stride` to `scripts/lol_horizon_sweep.py`. It keeps rows where
`second % stride == 0` and defaults to 1, so existing behavior does not change.
Apply it only to the train frame, after `--second-max`. The eval frame stays at
full density for every run: all eight tables share one eval ruler, and live LoL
quoting is every second, so the operational measurement is also every second.

The `all` feature set already exists at `scripts/lol_horizon_sweep.py:97` as
`base + roles + deltas`. It has never been run.

Add one test that the stride keeps the expected second values and that stride 1
leaves the frame unchanged. Then run `make lint-all`.

## Step 2. Sweep four feature sets at 540, full density

Run four sweeps:

```text
--datasets-dir data/lol/processed/datasets_roles
--second-max 540
--horizons 30,60,90,120,180,240,300,360,420
--feature-set base | base_deltas | roles | all
```

`--second-max` caps the train frame and the eval frame together, at
`scripts/lol_horizon_sweep.py:355-356`.

Each sweep fits 9 boosters. The eval set is identical across all four sets:
690 527 rows over 1455 matches, fixed by the horizon-420 label intersection.
That makes the four tables directly comparable.

The output axes are the feature set and the training horizon. Nothing else.

The sweep still prints minute buckets. Keep them as diagnostics. Do not make a
decision from them: the cutoff is fixed at 540, so there is no cutoff to choose.

Cross-check: `base` at horizon 300 must land near `dir_cents` 0.560 from
`sweep_control_540_base.csv`. The eval sets differ, so expect a close value, not
an identical one.

## Step 3. Sweep the same four sets on the Dota row grid

Repeat step 2 with `--row-stride 60`. The train frame becomes
`0, 60, ..., 540`, which is 10 points per match and about 26 thousand rows
instead of 1.33 million. These runs are nearly free.

LoL has no pre-horn rows, so the grid is 10 points against Dota's 11. Matching
Dota's `-60` row is a Stage 05 task and stays out of this campaign.

## Step 4. Pick the offline winner

Choose the feature set, the label horizon, and the row stride from the tables of
steps 2 and 3. Rank by same-horizon `dir_cents` with its cluster interval, and
by `dir_ratio`. The user makes the call.

Compare the winner against the Dota reference table before going further. A
candidate far below `dir_ratio` 0.185 is unlikely to earn Dota-scale PnL.

Do not pick the argmax of the grid on its own. Prefer a horizon whose
neighbours also score well and whose interval excludes zero. An isolated peak
next to a non-significant neighbour is a tuning artifact.

The anchor horizon is 300. Keep 300 unless another horizon beats it on both
`dir_cents` and interval width.

## Step 5. Run the first full S2 backtest

Train the step 4 winner and replay it with the baseline maker configuration:

```text
features=<step 4 winner>
target_horizon_seconds=<step 4 winner>
train_second_max=540
buy_cutoff_second=540
min_abs_delta=0.01
unwind_after_seconds=300
min_entry_price=0.35
max_abs_nw_delta_30=350
```

Use 8 to 10 shards. Compare against the completed baseline run with
`scripts/compare_backtests.py`.

Keep `unwind_after_seconds` at 300 even when the label is shorter. The Dota
experiment showed that shortening the exit together with the label loses PnL.

## Step 6. Run the runner-up horizon

Run this step only when step 5 leaves the decision open: the candidate neither
clearly beats nor clearly loses to the baseline.

Take the second horizon from the step 4 table, keep the feature set and the row
stride, and replay once.

## Step 7. Tune at most two maker axes

The paired resolution is about 0.48 PnL per match and the anchor earns 0.727. A
one-axis maker change is expected to move PnL by much less than 0.48, so most of
these runs cannot resolve. Run at most the two axes whose diagnostic buckets
show the largest concentrated PnL. Keep the anchor value on every other axis.
Do not run all five axes.

Diagnostic buckets choose one direction for the next replay. They do not
replace the replay, because queue fills are path-dependent.

| Parameter | Anchor | Next candidate |
|---|---:|---|
| `BUY_CUTOFF_SECOND` | `540` | `360` or `420` when late entries lose. Never above 540. |
| `MIN_ABS_DELTA` | `0.01` | `0.007` only when the `0.7` to `1.0` cent band looks useful; `0.015` when marginal signals lose |
| `UNWIND_AFTER_SECONDS` | `300` | `180` when long holds lose; `600` when the 300-second-and-longer cohort earns the PnL |
| `MIN_ENTRY_PRICE` | `0.35` | `0.20` or `0.50`, in the one direction supported by price buckets |
| `MAX_ABS_NW_DELTA_30` | `350` | `250`, `500`, or disabled, in the one direction supported by NW buckets |

When both selected axes win alone, replay their combined configuration once. If
the combined run loses the improvement, keep the stronger single change.

## Step 8. Freeze, publish, and write up

1. Publish the winning candidate to `data/lol/models/research/`.
2. Reuse the winning full S2 run when its model and input hashes match the
   published artifacts. Run S2 again only when publication or cleanup changes a
   hash.
3. Write the keep and drop results, run paths, hashes, and final parameters to
   `docs/experiments/`.
4. State that the campaign tuned on the current holdout and requires new
   matches for confirmation.
5. Remove campaign-only branches and temporary overrides. Keep the sweep script
   with `--row-stride`, the generic trainer options, the comparison tool, and
   the experiment reports.
6. Delete the role columns, role parsing, and role artifacts only when step 4
   did not select a feature set containing roles.

The campaign ends after it has selected all eight parameters. Selected includes
keeping the anchor value when the campaign has no resolution to move it.

Expected budget: 8 offline sweeps and 1 to 4 full S2 runs.

## Use PnL per shared match as the main result

The headline comparison is mean `engine_pnl` before rebate across shared
completed matches. The report also shows:

- total PnL before rebate;
- the shared, candidate-only, and baseline-only match counts;
- paired PnL delta;
- the 10% trimmed mean;
- the paired bootstrap interval, t-test, and Wilcoxon result;
- traded maps, no-trade maps, BUY fills, and fill rate;
- CVaR, drawdown, markouts, hold times, unwind share, and forced inventory;
- the matches that contribute the largest absolute paired differences.

The user makes the final keep or drop decision by inspecting these results. The
extra metrics explain the PnL result; they are not automatic vetoes. When two
configurations look equivalent, keep the simpler or current configuration.

Use `scripts/compare_backtests.py` for paired cross-run comparisons. Extend that
script only with the missing bootstrap confidence interval and diagnostic
buckets. Do not build a generic optimizer or a new experiment framework.

## Generate diagnostics from each expensive run

Add compact buckets for:

- entry game minute;
- absolute predicted delta;
- round hold time;
- entry price;
- `nw_delta_30`.

For each bucket, show the round count, PnL before rebate, BUY markout, and the
share of total PnL. Keep the existing round-level reconciliation so bucket
totals cannot silently disagree with match-level PnL.

These tables pick the maker axes for step 7. They do not claim that a different
gate would have produced the same fills.

## Handle failures without advancing the campaign

Run one candidate configuration at a time. Run that configuration's shards in
parallel, then merge them. Do not run B0 and do not run per-candidate smoke
replays.

Advance only after every shard exits successfully and the merge writes a valid
summary. Resume the same candidate after a shard failure once. Do not select a
new candidate from a partial run. Stop the campaign if the same failure
repeats, or if a manifest, hash, or coverage-integrity check fails.

After each merge, show the result and start the next selected candidate unless
the user interrupts.

Before step 5, run the project tests for the changed sweep script, trainer,
Stage 07, signal builder, CLI, and comparison code. Do not run a separate
one-match backtest for every candidate.

Add focused checks for:

- the row stride, including the stride-1 no-op;
- identical delta values in Stage 05 and Stage 07;
- model metadata for the target horizon, training window, row stride, and
  feature order;
- propagation of each LoL CLI parameter into the strategy config and manifest;
- paired comparison and diagnostic-bucket reconciliation.

After Python changes, run `make lint-all` as required by `AGENTS.md`.

Live integration and the confirmation run on newly collected matches remain
separate tasks.
