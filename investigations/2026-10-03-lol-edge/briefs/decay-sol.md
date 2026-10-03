# Brief: decay-sol — where did the LoL edge go between June and September

Question: the LoL backtest's late third (2026-08-22..09-29) earns ~1/4 of the early third per map and buy-300s markout falls from ~2.5 ¢ to ~0.5 ¢ (context fact 1), while Dota's late third is its best. Decompose that decay. Is it (a) league/tournament mix, (b) market liquidity/spread, (c) the model being stale (trained on data to 2026-06-03), (d) the game-second profile of entries, (e) a data/feed change on a specific date, or (f) a genuine market efficiency change?

Data: `data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_w540lv6/seed{0,1,2}/{results,fills,quote_events}.parquet` (use w540lv6 as the main run, histfix as a check), `data/lol/processed/datasets/{validation,split,audit,market_seconds}.parquet`, league per event via `src/shared/utils/lol_leagues.py` + `config/lol_league_whitelist.json` + universe `data/lol/processed/universe/markets.parquet`. Read column names first (`pd.read_parquet(..., columns=None).columns` on one seed; results/fills schema in `src/backtest/report_types.py`, `results.py`).

Do:
1. Weekly series (by map start date) of: maps, traded maps, buy fills, buy turnover, engine PnL before rebate, net PnL, buy-300s markout, ¢/share, loss rate. Mark the week where markout drops. Is it a step or a slope?
2. Same split by league (whitelist name) and by tier (LCK/LPL/LEC/LCS vs the rest). Which leagues carry the early PnL and are they present in the late third? Normalise per map and per $ traded.
3. Model quality without execution: score the published ensemble (`data/lol/models/research`, `GbmPredictor` in `src/shared/utils/gbm.py`, features via `load_lol_dataset` in `src/lol/06_train_model.py` — copy, do not import 06 directly, `lol/types.py` shadows stdlib) on validation rows `second < 480`, per week: MAE gain vs no-move, directional markout at |delta| ≥ 0.02 and price 0.45–0.85. Does the model's own edge decay the same way as the backtest PnL? If yes it is signal; if no it is execution/fill.
4. Price regime: distribution of entry prices and |delta| per period; are late-period entries at more extreme prices or smaller deltas?
5. Liquidity: from quote_events/fills, average spread at entry, depth at best (if recorded), time-to-fill, fraction of rungs filled, per period.
6. Compare with Dota LIVE (`data/backtests/dota_maker/LIVE`) on the same weekly axis for the model-quality metric only (Dota research model `data/new_model/research`, features `src/train_model/train_model.py`), so the reader sees the divergence on one chart/table.

Deliver a ranked attribution: how much of the early→late PnL drop (in $ and in ¢/share) each factor explains, with the numbers. Then the 2–3 experiments that would confirm the top factor (e.g. retrain with production_training to 2026-08-xx and score Sept only; or restrict to leagues X).
