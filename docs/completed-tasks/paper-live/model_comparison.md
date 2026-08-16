# A4: old vs new model (US-001)

Same validation set: 327 eligible of 328 matches. Arms: b0 and s2. s32 was not run.

The new model is not an ablation. It changes XP (levels instead of STRATZ leads), source lag 8→2, and poll 10→1 together.

## Model metrics

From `data/new_model/<name>/model.json`. Validation MAE is on that model's own contract and split, not a shared row set.

| | old `20260813T233731Z` | new `20260814T165239Z` |
|---|---|---|
| source_lag_seconds | 8 | 2 |
| poll_interval_seconds | 10 | 1 |
| train_matches | 1750 | 1748 |
| valid rows in metrics (`future_300_n`) | 180988 | 182785 |
| trees | 39 | 39 |
| no_move MAE 300s, cents | 8.209 | 8.188 |
| model MAE 300s, cents | 7.974 | 7.955 |
| MAE gain 300s, cents | 0.235 | 0.233 |
| MAE gain 95% CI | 0.113 … 0.355 | 0.107 … 0.360 |
| bias 300s, cents | 0.043 | 0.072 |
| dir 300s, cents | 1.367 | 1.441 |

MAE gain is inside the noise of the old interval. Directional move is slightly larger.

## Validation backtest PnL

Old run: `data/backtests/dota_maker/validation_b0_s2_delta01_cut540_nw500/`

New run: `data/backtests/dota_maker/validation_b0_s2_delta01_cut540_nw500_steam-xp-lag2-1hz/`

| arm | old net PnL | old ROI | new net PnL | new ROI |
|---|---|---|---|---|
| b0 (book mid, no model) | -43.54 | -0.2663% | -67.13 | -0.4106% |
| s2 (model) | +17.01 | +0.1040% | +29.57 | +0.1808% |

s2 stays positive and beats its own old run. b0 is still the losing baseline and got worse; that arm ignores the model.

Going to live is not part of this story.
