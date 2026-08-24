# US-001 — lag 2 vs lag 10 (s2-join)

Numeric quality gate: none. Read the numbers and decide.

## Models

| | baseline research | candidate research | production (not backtested) |
|---|---|---|---|
| name | `20260824T143419Z` | `20260824T200814Z` | `20260824T152512Z` |
| source_lag_seconds | 2 | 10 | 2 |
| poll_interval_seconds in model.json | 1 | absent | 1 |
| train_matches | 2042 | 2046 | 2482 |
| rows (holdout eval) | 279240 | 276008 | n/a (metrics null) |
| mae_gain_300_cents | 0.277 [0.164, 0.396] | 0.246 [0.136, 0.366] | n/a |
| model_mae_300_cents | 7.806 | 7.868 | n/a |
| no_move_mae_300_cents | 8.083 | 8.114 | n/a |

Production `20260824T152512Z` is not the backtested baseline: no validation backtest exists for it, and a production fit trains on the validation matches. `20260824T143419Z` is that model's same-day research sibling (`model_comparison` names both). After this code change, loading production fails closed on `source_lag_seconds` (lag 2 vs live 10). The daemon is not restarted in this story.

`mae_gain` is not comparable across these two research fits: `rows` differ because the datasets were rebuilt at lag 10 (`docs/domain.md`).

## Backtests (s2-join only)

- Baseline run: `data/backtests/dota_maker/validation_b0_s2_delta01_cut540_nw350_20260824T143419Z` (compared with `--arm s2`)
- Candidate run: `data/backtests/dota_maker/validation_s2_delta01_cut540_nw350_20260824T200814Z`

Candidate s2 headline: PnL before rebate **+$592.49** ($1.371/match), with rebate **+$894.22**, 432 completed, 0 terminated, 385 traded / 47 no-trade.

### Paired s2 comparison (`compare_backtests.py --arm s2`)

- Shared matches: **432** (none candidate-only, none baseline-only)
- Shared PnL: candidate **+$592.49** vs baseline **+$1,179.50**, delta **−$587.02**
- Per-match diff: mean **−$1.3588**, 10% trimmed mean **−$0.5958**
- Paired t: t=−1.839, p=0.0666
- Wilcoxon: W=28416.5, p=0.1939 (351 nonzero diffs)
- Better 164 / worse 185 / tie 83 (tie: |diff| < $0.005)

Round buckets over shared matches: identical 101 ($0), diverged 121 (−$162.46), baseline-only 396 (−$227.65), candidate-only 441 (−$196.90). Residual $0.

## Window-seconds warning

`PREHORN_LEAD_SECONDS` moved 58 → 50 with the lag change. `window_seconds` in the backtest summary is `game_ended − horn + PREHORN_LEAD_SECONDS`, so it shifts by 8 seconds per match with no change in model quality.

Observed: baseline s2 `window_seconds` 1,152,181 vs candidate 1,148,725 (Δ −3,456 = 432 × 8). Do not compare that column to older runs.
