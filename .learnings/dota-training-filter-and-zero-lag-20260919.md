# Dota retraining, tape filter, and train-lag 0: code audit

Read-only review on 2026-09-19 after research model `20260919T114801Z`
and production model `20260919T114805Z` were published in commit `d34e199a`.
No product edits, training, backtests, or deployments performed by this review.

## What changed since the September 15 models

Compared commit `a25456e3` with current HEAD, including split parquet loaded
directly from that old Git commit (the archived split had been overwritten by
later prepare before archival, so it is unsuitable for reconstructing the old
training population).

- Research training maps: 1010 -> 1152; **151 added, 9 removed**.
- Research validation maps: 531 -> 558; **32 added, 5 removed**.
- Same feature list, horizon 300s, train lag 10s, K=10 sub90 ensemble.
- No diff in `src/shared/utils/gbm.py`, STRATZ feature construction,
  market cache construction, or quote-quality rules across these commits.
- Tree counts are refitted: mean 40 -> 44. Research early stopping uses the
  changed validation data, so this is not an isolated addition of train rows.
- Removed upper `DATASET_END_TIME` filter and added experiment CLI lag flags.
- Dataset admission now reads onchain fills (`block_timestamp_us`) instead of
  Telonex trades (`timestamp_us`); the 10-record / 300-second threshold stayed.
- Raw training parquet at the old commit is not tracked; cannot prove all
  numerical rows for shared training IDs remained identical after data refresh.

Thus the fitting recipe stayed the same; changed data composition and refitting
produced the observed improvement. It does not establish that adding arbitrary
matches always helps.

## Tape filter applies to training too

`src/prepare_dataset/prepare_dataset.py:74` filters the entire match catalog with
`match_passes_tape_buckets` before the chronological train/validation split.
`src/shared/utils/telonex_tape.py:45` requires at least 10 onchain rows combined
across the two tokens in every full 300-second bucket from spawn to map end.
This is an activity/availability heuristic over the whole map, not a direct
test of corruption of game features or current/future market prices.

The supervised target is future midpoint at +300s minus current midpoint.
Game features plus correctly timestamped current/future books are sufficient
for an individual training example. Fill tape is required separately to model
executions in the queue backtest. Current prepare already skips minute rows
whose current book is invalid or whose +300s market target is missing.
Long book gaps >120s have a separate backtest-only exclusion mechanism.

Recommendation: test separating replay-tape eligibility from training-data
quality. Broaden training only where features, orientation/timing, current price,
and +300s target are valid. Keep the existing held-out replay panel and training
recipe fixed for that experiment. Do not silently add held-out maps to research
training. Low activity can indicate a different liquidity regime, so improvement
from broader data must be measured rather than assumed.

Local pre-cutoff catalog: 2485 maps, 2324 with a market-cache file, 1152 currently
in training. The other 1172 cached maps are only candidates; their usable rows,
STRATZ validity, and exclusion causes were not individually audited.

## Historical train-lag 0 experiment

`docs/experiments/lag-grid.md`, August 25, 432 common maps, older model/strategy:

- train10 / execute10: $1025.89 before rebate.
- train0 / execute10: $1087.98; paired gain $62.08, paired t-test p=0.76.
- train0 / execute60: $229.73.
- train10 / execute60: $209.57.

Historical verdict was lack of reliable benefit from train0, **not** evidence
that train0 was consistently worse. No comparable current train0 / execute25
experiment was identified. That is an optional separate experiment: train0
and train10 on the same data; evaluate both at execute25 and cadence 1s. Keep
training-population changes separate so the cause of any improvement is clear.

Execute25 is sufficient for comparing models under the same 25s scenario.
Execute30 was suggested only as sensitivity to the approximate real PGL delay,
not as a requirement that invalidates the user's execute25 comparison.
