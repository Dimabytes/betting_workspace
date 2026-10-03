# Brief: grok-code — exact trace of the LoL signal path: training vs backtest vs live

Code only (plus tiny read-only confirmations). Produce the authoritative, line-referenced trace of how one LoL model input vector is built in each of the three paths, so the other agents' data findings can be matched to code.

For each path write the sequence of functions with `file:line`, the clock used (game second vs wall), where the +11 s lag enters, how `second` is anchored (spawn frame / GRID clock), how the prior is defined, how the 60 history columns are built (which tape, which pivot rule, which `HistoryPolicy` constants, how NaN is produced), which ticks are dropped and why, and when the strategy is allowed to BUY/SELL:

1. Training: `src/lol/05_prepare_dataset.py` (`build_one_map`, `join_market_rows`, `build_dataset_row`, `build_game_feature_rows`) → `src/lol/06_train_model.py` (`slice_model_rows`, `load_lol_dataset`) → `src/shared/utils/dota_features.py` (`attach_catalog_features`, `GRID_HISTORY_POLICY`) → `src/shared/utils/gbm.py` (`build_price_delta_labels`, `fit_research_members`, params).
2. Backtest: `src/backtest/run.py` → `lol_inputs.py` → `signals.py` (grid-v1 cadence `LOL_GRID_V1_BANDS`, `GRID_V1_FIRST_TICK_SECOND`, decision lag, history from received tape, `drop_gap_ticks`) → `feed_schedules.py` / `live_archives.py` (archived live cadence) → `strategy.py` → `src/strategy/{engine,policy,quoting,kill_gate,signals}.py`.
3. Live: `src/trader/orchestrator.py` / `discovery.py` / `lol_league_filter.py` → `match_worker.py` (feed events, history buffer, `_on_event` ordering) → `grid_live_feed.py` / `grid_feed.py` / `grid_widgets.py` (which GRID fields become nw/xp/deaths/top3; clock) → `lol_prior.py` / `market_prior.py` → `model_server.py` → `session_*.py`.

Then a difference table: row per aspect (clock, anchor, lag, prior, nw source, xp source, deaths, top-3 fields, history tape, pivot rule, first tick second, tick cadence, invalid-tick handling, buy cutoff, sell rule), columns train / backtest / live / Dota-train / Dota-live, with file:line in each cell and a "same? y/n" verdict. Flag every cell where LoL differs from Dota in a way that the model cannot see.

Confirm two things with data (read-only, `PYTHONPATH=src uv run python` from the esports-trader root, columns=[...]): (a) `GRID_HISTORY_POLICY.start_second` and `max_pivot_gap_seconds` values and whether LoL uses the same object as Dota GRID; (b) on 20 validation maps, the first `second` with a non-NaN `total_5m` column after `attach_catalog_features` vs the first tick second the backtest emits (76).

Write the report to `reports/grok-code.md` in the context's format; `Status: FINAL` on line 2 when done.
