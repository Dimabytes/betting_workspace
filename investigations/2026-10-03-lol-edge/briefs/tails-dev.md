# Brief: tails-dev — fill-level forensics of the worst LoL maps

Goal: take the 5 worst maps of `histfix-20261003r2` (per seed; worst map −1,913 / −1,915 / −2,088, see `report_seeds.py`) and the 5 maps with the largest paired loss vs `w540lv6` (`scripts/compare_backtests.py <w540lv6 dir> <histfix dir>` prints them; run from the esports-trader root with `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python`), and tell the story of each map second by second.

Data: run dirs `data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_{histfix-20261003r2,w540lv6}/seed{0,1,2}/{results,fills,quote_events}.parquet` (schemas in `src/backtest/report_types.py`, `results.py`, `quote_store.py`, `telemetry.py`), dataset rows for the map (`data/lol/processed/datasets/validation.parquet` filtered by match_id: features, `market_p_radiant`, label), dense tape `game_features.parquet`, pauses in `audit.parquet`, the map's league and teams via `split.parquet` → event_id → `data/lol/processed/universe/markets.parquet`, raw livestats windows under `data/lol/raw/lolesports/windows/<esports_game_id>/` if you need the actual game events (kills, towers, dragons). The backtest inspector (`src/backtest/inspect/`) shows how the project itself reads these; do not launch streamlit, reuse its loaders.

For each map (≥ 8 maps total): a table of model ticks (second, mid, model fair, delta, valid/invalid, action) merged with fills (side, price, qty, running position, running PnL) and the game narrative (what happened in the game at that moment, from gold/kills). Then answer:
1. What did the model believe and why (which features drove the delta; compare the new history columns' values with the base features; was `total_5m`/`change_*` NaN or extreme)?
2. Did the market move against us because the game turned (model wrong on information) or because of something non-game (market spike, thin book, our own fills moving the mid — `strip_own_book.py`)?
3. Under w540lv6 the same map lost less: which tick differed first and why (invalid tick, different delta, different fill)?
4. Was there a pause, a remake, a long spawn gap (`audit.parquet` pause_seconds, `LOL_SPAWN_SEARCH_SECONDS` logic in `src/lol/05_prepare_dataset.py`) or a missing stretch in the tape?
5. Would any simple rule have avoided the loss without hindsight: max position per map, no new BUY after N consecutive invalid ticks, no BUY when |delta| grows while mid moves against the position, time stop? Simulate the rule on the fills of these maps only and state the PnL change (this is anecdotal; say so).

Deliver: per-map stories (short), a cross-map pattern (same mechanism in ≥ 3 maps?), and the rules to test with expected effect. Keep scripts under `work/tails-dev/`.
