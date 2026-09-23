# Brief: train-luna — LoL training and offline validation

Your name: `train-luna`. Report: `$R/reports/train-luna.md`. Work dir: `$R/work/train-luna/`.

Goal: check whether the LoL model's offline edge is real, stable, and comparable to Dota,
and whether training leaks information.

1. Read `src/lol/06_train_model.py`, `src/train_model/*`, `src/shared/utils/gbm.py`,
   `src/lol/lol_validation_metrics.py`. Describe the splits (chronological by event or
   series?), research vs production, rows per map (LoL ~541 per map at 1 Hz vs Dota minute
   rows), weights, early stopping, the K=10 sub90 ensemble.
2. Leakage: can one series or event land in both train and validation? One game linked to two
   markets? Does early stopping use the same validation maps that the LIVE backtest replays
   (then the backtest is in-sample for tree count)?
3. Offline metrics of the research model on `data/lol/processed/datasets/validation.parquet`:
   MAE gain vs no-move, a directional metric, per month, per league, per game-second bucket,
   per |pred| bucket. Does the edge decay over time? Is it concentrated in leagues live does
   not trade (LPL, LCK Challengers) or in top leagues vs EMEA Masters tier? Compare with the
   Dota research model on its validation set (`data/new_model/research`, Dota datasets).
4. Labels: target = mid(t+300) − mid(t) after `332e1c17` (current mid as-of 0). Distribution,
   flat labels, rows near map end, paused maps.
5. Before vs after `332e1c17`: if the old model / datasets exist (`data/lol/models/archive`,
   git history of `split.parquet`, commits `c9fb5cd2`, `934c3e07`, `648af926`), compare offline
   metrics. A jump in offline edge after moving the current mid earlier would mean the model
   learned the market's reaction delay, which live cannot use.
