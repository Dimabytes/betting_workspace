# US-001 — Series signal and Dota cohort
Status: FINAL

Implemented `current-task/plans/US-001.md` in `esports-trader` on `main`.

## Commit

`3a804f018b7aecc5cba013e0cc99e4bc73fa6e89` — `feat: US-001 - Сигнал серии и выборка Dota` (15 files, +1143/−1). Not pushed.

## What was built

- `src/strategy/series_link.py` — pure series math: `calculate_series_win` (BO3/BO5 recursion), `calculate_series_slope` (B = W(a+1,b) − W(a,b+1)), `fit_horn_q0` (97-point q grid 0.02–0.98, refusals `price_bounds` / `fit_error` >0.03 / `flat_slope` <0.05), `series_c_bucket` (0–119/120–239/240–359/360+), `calculate_series_delta` (`line` → B·Δ, `line_x_c` → B·c(bucket)·Δ), `pick_series_token_index` (name/alias orientation via `orient_outcomes`), `load_series_c` (validates four finite floats).
- `series_c.json` ×3 — Dota `(1.49, 1.07, 0.76, 0.49)` in `src/strategy/` and `data/new_model/research/`; LoL `(1.04, 1.42, 0.84, 0.49)` in `data/lol/models/research/`.
- `src/shared/utils/grid_series_state.py` + `GRID_SERIES_STATE_DIR` in `paths.py` — loads `data/raw/polymarket_dota/grid_game_starts/series_state/*.json` (nested `data.seriesState` with top-level fallback), sorts by `sequenceNumber`, emits the score before applying each game's result, tracks wins by team id across side swaps, keyed by the game's truncated `startedAt` Unix second → `SeriesGameScore`.
- `UniverseMarket.best_of` (populated for Dota and LoL) and `GammaMarket.outcome_names` (parsed from Gamma, cached, restored; old caches without the key still load). `GammaMarketIndexRow` extended; `match_catalog` passes `outcome_names=None`.
- `src/backtest/series_inputs.py` — `resolve_series_market` gates one catalog map (non-decider only: BO3 maps 1–2, BO5 maps 1–4; single `series_winner` per event; GRID score at `spawn_at`; outcome-name orientation; series book days over the availability window), `select_series_validation_matches` returns chronological `SeriesSelection` plus `SeriesCoverage` (existing `ValidationCoverage` untouched), `find_horn_quotes` scans ±30 s of the horn (0, −1, +1, −2, +2, …) requiring as-of `ok` and ≤5c spread on both books.

## Verification

- `pytest tests/test_series_link.py tests/test_series_inputs.py tests/test_archive_index.py tests/test_backtest_validation.py` → **109 passed** (2 pre-existing nautilus deprecation warnings).
- `uv run python -m basedpyright` → **0 errors, 0 warnings, 0 notes**.
- `uv run ruff check` + `ruff format --check` on all touched files → **clean**.
- Real-data sanity: `select_series_validation_matches` over the validation dataset → `SeriesCoverage(validation_matches=717, book_gap_excluded=92, map_eligible=625, not_series_map=201, without_series_contract=0, without_series_score=46, orientation_failed=0, without_series_book=0, eligible=378)`, 5377 GRID state files loaded. Eligible ≪ the research's 511 as expected; zero counters explained by verified universe invariants.

## Plan items skipped and why

- Full `make test` / seed backtest runs — explicitly out of scope for US-001 (and the suite must not run during a live map).
- US-002+ items (`--market`/`--series-branch` flags, `run.py` wiring, LoL cohort, `match_winner`/`links.parquet`, post-map SELL, experiments, `promote_backtest.py`, `trading.toml`) — later steps per the plan.
- `feature.json` `passes` — not touched, per instructions.
