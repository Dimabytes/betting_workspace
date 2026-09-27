# Brief: train-luna — training, evaluation, inference parity

Report: `$R/reports/train-luna.md`. Work dir: `$R/work/train-luna/`.

## Scope

`src/train_model/{train_model.py, market_metrics.py}`, `src/shared/utils/{gbm.py, model_registry.py,
market_scenario_report.py}`, `src/trader/model_server.py` (inference side), `data/new_model/*`,
`data/new_processed/dataset/*`.

## Tasks

1. **Recipe.** Objective, target (Δ mid over 300 s? clipped?), sample weights, features, K=10 `sub90`
   bootstrap (by map or by row? if by row, rows of one map sit in-bag and out-of-bag → early-stopping leak),
   early-stopping set (validation = backtest maps), tree counts. Production uses fixed trees (39): how derived?
   Production trains on train + validation **minute** rows only: is that intended and sound?
2. **Honest evaluation.** How `model.json` metrics (`mae_gain_300`, `dir_300`, CIs) are computed: which rows
   (autocorrelated second rows?), CI by map bootstrap? Quantify early-stopping optimism with small offline fits
   (allowed, into `$R/work/train-luna/` only; never write under `$E/data/new_model`): retrain the research
   ensemble with early stopping on a chronological inner slice of train (or fixed trees), then evaluate on the
   last part of validation. How much of the claimed edge survives?
3. **Split leakage.** The same map twice under different ids? Maps of one series split across the boundary
   (fine), exact duplicates (not fine).
4. **Inference parity.** Load the research and production ensembles through `gbm.py` and through
   `model_server.py`'s path; predict on identical rows; check equality (feature order, float32 vs float64, NaN,
   ensemble mean vs median, clipping). Check `model.json` metadata against reality (`train_dataset_sha256` vs
   the current dataset file; is `source_lag_seconds` the real join?).
5. **Outside the training window.** Live uses the fair after 540 s for SELL prices. On validation rows compare
   Δ̂ vs realized for seconds 540–900 vs 0–540 (calibration, bias). Is late Δ̂ biased enough to misprice SELLs?
6. **Calibration by regime.** Large NW advantage, market_p near 0.85+, prehorn rows; overall bias
   (`model_bias_300_cents = +0.117`).
