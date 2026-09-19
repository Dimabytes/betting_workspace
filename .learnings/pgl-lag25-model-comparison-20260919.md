# PGL lag-25 model comparison, 2026-09-19

Read-only analysis of the user's completed backtests. No retraining, replay,
production edits, or deployments were performed.

## Inputs and comparability

Repo: `../esports-trader`.

- Baseline: `data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_pgl25-t10/seed0`.
- Candidate: `data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_pgl25-t25/seed0`.
- Same 556 completed maps, identical selected-ID hash, validation-dataset hash,
  execution settings, and framework commit. Both execution lag 25s, cadence 1s.
- Catalog identities were recalculated with `model_identity_sha256`; both match
  their recorded backtest manifests.
- Baseline catalog `data/new_model/research/model.json`: trained September 15,
  source lag 10s, **1010 training matches**.
- Candidate `data/experiments/pgl-lag-25/catalog/model.json`: trained September 19,
  source lag 25s, **1152 training matches**.

This is a valid observed comparison between two catalog versions on the same
execution inputs, but **does not isolate training lag**: the training population
also changed. Current lag-10 prepared training data already has 1152 usable
matches; its SHA256 differs from the old model's recorded training hash.
Both current split files list 1152 train / 558 validation, but the baseline
split file was modified September 19, after the September 15 model. The current
split is not proof of what the older model actually trained on. Experimental
training copies the shared research split into its catalog. Lag-25 training
IDs have zero overlap with the 556 backtest maps.

## Observed results

| Metric | Train10 / execute25 | Train25 / execute25 |
|---|---:|---:|
| PnL before rebate | $2098.54 | $2322.31 |
| PnL including rebate | $2495.99 | $2743.69 |
| Buy turnover | $67163.21 | $73122.90 |
| PnL per bought share | 1.7817 cents | 1.8547 cents |
| Traded maps | 346 | 384 |
| Buy 300s markout | 1.6073 cents | 1.4528 cents |
| CVaR 5% | -$53.247 | -$49.588 |

Before-rebate gain: $223.77298, about 10.7%, or $0.40247 per completed map.
Paired differences: 205 improvements, 179 deteriorations, 172 ties (tie
tolerance $0.005). Chronological thirds: +$124.29, +$19.74, +$79.74.

Paired bootstrap, 20,000 replicates, NumPy default_rng seed 20260919: resample
260 actual `event_id` clusters from the experiment validation dataset, keeping
maps within a series together, then divide sampled summed PnL difference by
sampled map count. Percentile 95% interval for mean per-map difference:
**[-$0.92989, +$1.72442]**. Conditional exploratory uncertainty on this validation
panel; this does not account for model/strategy selection or future market shift.
Validation also participates in research-model early stopping.

Three largest positive map deltas sum to $240.35; removing only these gives
-$16.58. This asymmetric exclusion is a concentration diagnostic, not a fair
alternative performance estimate.

Trading on both models: 312 maps, +$394.68 difference. Only baseline trades:
34 maps, -$114.67 difference. Only candidate trades: 72 maps, -$56.24 difference.
Neither trades: 138 maps. Extra traded maps alone do not explain improvement.

## Decision

There is enough promise to continue evaluating a second model, but not enough
evidence that matching training lag caused improvement or merits immediate live
deployment. Retrain the 10s research control using the same raw-data snapshot,
1152 training IDs, split, features, and training recipe as the 25s candidate.
Compare both at execution lag 25 and 30 on common maps, then confirm on unseen
maps. The previous capture study still supports 30s as the main assumed PGL
lag; this backtest does not establish actual source latency.

Do not recommend multiple cadence seeds for these exact 1s runs:
`select_cadence_rows` returns every second before using the RNG when interval=1.

Paired per-map analysis table with actual event IDs:
`/private/tmp/pgl-lag25-paired-review-20260919/paired.csv`.
