# Brief: parity-luna — empirical live vs backtest parity on the same Dota maps

Report: `$R/reports/parity-luna.md`. Work dir: `$R/work/parity-luna/`.

## Data

Live archives `$E/data/trader/*` (Dota: `match.json` `game` missing or `dota`), the LIVE backtest seeds
(`$E/data/backtests/dota_maker/LIVE/seed{0,1,2}`), `data/new_processed/dataset/{validation_dataset,
game_features}.parquet`, `data/new_processed/match_catalog`, books and onchain fills per map
(`data/raw/telonex/polymarket/...`, per-map reads only).
Reuse `$W/.analysis/lol-live-gap-2026-09-23/work/orchestrator/same_map_parity.py` (LoL + Dota GRID,
09-01..09-19). Extend it to the latest data and to all Dota sources (Steam, GRID, Oddin).

## Tasks

1. **Same-map PnL.** Maps traded both live and in the LIVE backtest. Per source (Steam / GRID / Oddin) and
   period: PnL per BUY $, fill count, fill prices, time of the first BUY, markouts 30/300 s, share of positions
   held to the end. Normalize by clip (live $60/$200 vs backtest 3 × $100) or by BUY notional. Decompose the
   gap: entries (signal), fills (execution), exits.
2. **Signal parity.** At the same game second, compare the live model inputs and Δ̂ (`session.jsonl` / 
   `core_trace` signal rows) with the backtest's (validation / game_features rows + model predictions from the
   research and production model files, loaded read-only through project `gbm.py` loaders). Report the
   distribution of |live Δ̂ − backtest Δ̂|, per-feature differences (NW, XP, deaths, top1, prior, market_p), and
   how often the entry decision flips. Which feature drives most of the gap?
3. **Timing parity.** Live signal time (receive) vs the backtest signal time for the same frame; age
   distribution; gaps.
4. **Settlement tail (lead B).** Which backtest held-to-end positions are on maps that also ran live, and what
   did live do there? How often does live hold to the end, and what is its win rate? Is the backtest's 39/39
   explained by the price path (winners run away from a resting SELL at fair)? Test it: for held positions, the
   token mid path vs our SELL price; for positions that exited, how many would have won.
5. **Population parity.** Live Dota maps (since archives began) that are NOT in the backtest, and why
   (exclusion rules). Maps in the backtest window with no live session (discovery misses?). Money impact.

## Allowed

Data scripts with `nice`. Model predictions with the model files (read-only). No backtest runs.
