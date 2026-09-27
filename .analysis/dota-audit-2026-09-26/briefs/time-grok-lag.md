# Follow-up brief: time-grok — edge at the real per-source lag

Report: `$R/reports/time-grok-lag.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.

Your F1/F5: live Oddin state is ~16 s old, live GRID ~8.4 s, training and grid-v1 use 10 s. Measure what that
costs, offline, with the published models (read-only; no training needed).

1. On validation rows (market second M, `data/new_processed/dataset/validation_dataset.parquet`), rebuild
   the model inputs with features from STRATZ exact-second state at M−L for L ∈ {0, 2, 5, 8, 10, 12, 16, 20,
   30} (`game_features.parquet` is keyed by game second; set `second` = M−L as the backtest does). Keep
   `market_p_radiant` at M and the label at M+300. Score the research model (and production-noxp, the Oddin
   satellite catalog) with the project metric path (`market_scenario_report` / `market_metrics`): MAE gain,
   directional markout, bias, and the share of rows with |Δ̂| ≥ 2c (entry gate). Restrict to seconds 0..480
   (buy window) and give event-series CIs.
2. Same for the entry-relevant subset: rows where |Δ̂| ≥ 2c at L=10. How much of their realized move is left
   at L=16 vs L=10 vs L=8?
3. Name the Oddin archive whose feature age is hundreds of seconds (frozen `lastUpdatedAt − gameTime` horn);
   was it admitted to the archive index / LIVE backtest? And the 2 Oddin maps among the 58 horn maps (N4):
   did they pin on a positive clock still inside a pause?

Deliver a table: lag × model × metrics, and a one-paragraph verdict for Oddin ($200 clip) and GRID ($60).
