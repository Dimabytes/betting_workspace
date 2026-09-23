# LoL live gap research — shared context (read fully before your brief)

Date: 2026-09-23. Orchestrator: Claude. You are one of 10 research agents.
Research dir (below: `$R`): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23`
Project repo (below: `$E`): `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`

## The question

The owner trades Polymarket map-winner markets for Dota 2 and League of Legends (LoL).
The same strategy family runs on both. The model predicts the Polymarket midpoint
300 s ahead from live game state, and the trader quotes maker orders on that edge.

- Dota is clearly profitable live.
- LoL is roughly flat live, even after the latest fixes.
- The LoL backtest shows a large profit, larger than Dota per map.

The owner never read the LoL code line by line. Every serious bug so far was found by
reading code: swapped sides, wrong training time, broken matches in the data. Errors
compound stage by stage: collect → link market to game → prepare dataset → train →
backtest → live. Find where the LoL pipeline is wrong, or where the backtest is more
optimistic than live, and explain why LoL differs from Dota. A clean "this stage is OK,
here is the evidence" is also a valid result.

## Hard rules

1. Read-only on code and data. Do not edit, create, move, or delete anything inside
   `$E` (src, config, tests, scripts, data, docs), `../poly-maker`, `../polymarket-collector`,
   `../prediction-market-backtesting`. Write only to `$R/work/<your-name>/` and to your
   report file `$R/reports/<your-name>.md`.
2. Git: read-only commands only (`log`, `show`, `diff`, `blame`, `grep`). No commit, checkout,
   stash, reset, pull, push, worktree.
3. Do not SSH to the VPS (`sun`). It trades real money. All data you need is local.
   If something exists only on the VPS, write the exact request under "Needs from VPS".
4. No pytest, no `make test`, no model training, no full backtest runs, unless your brief
   explicitly allows it. Small analysis scripts are fine and expected.
5. Run Python from `$E` so project modules and pyarrow/polars/duckdb/lightgbm load:
   `cd $E && PYTHONPATH=src uv run python $R/work/<your-name>/<script>.py`
   Keep scripts in `$R/work/<your-name>/`. Reuse project functions instead of re-implementing
   parsers (grep `src/shared`, `src/lol`, `src/trader` first).
6. `data/lol/raw/telonex` is 122 GB. Do not scan it whole. Prefer processed parquet
   (`data/lol/processed/datasets/*.parquet`) and per-map reads.
7. Every claim needs evidence: `file:line`, the command, and the numbers it printed.
   Mark each finding `verified` (you reproduced it), `likely`, or `speculative`.
8. The other agents work in parallel on overlapping topics. Think independently.

## Live numbers (VPS `summarize.py`, pulled 2026-09-23 ~11:30 UTC)

Files: `$R/work/shared/lol_summary.txt`, `$R/work/shared/dota_summary.txt`, daily fold in
`$R/work/shared/live_daily.txt` (net = realized + imv + rebate per map, USDC; only maps
with fills and a known net are summed).

| since 2026-09-18 | maps | maps with fills | net USDC | clip |
|---|---:|---:|---:|---|
| LoL (all GRID) | 44 | 24 | +2.27 | $5 until 2026-09-21, then $20 |
| Dota GRID | 47 | 24 | +89.38 | $60 |
| Dota Steam | 51 | 40 | +290.93 | $60 (Oddin satellite $100) |

Live clip history (git log): LoL clip moved many times ($20 → $50 → $100 → $200 → $30 → $15
→ $30 → $5 → $30 → $5 on 2026-09-16 → $20 on 2026-09-21). Dota clip $60/$100.
Normalize by clip or by BUY notional before comparing.

## Backtest numbers (current LIVE catalogs)

`$E/data/backtests/lol_maker/LIVE -> validation_join_delta02_x015_cut480_p35_s06-playback-gf`
(945 validation maps, model research `20260921T095801Z`, `base_size_usdc=300` = 3 layers x $100,
`source_lag_seconds=10`, `max_signal_age_seconds=16`, `feed_schedule_rules_version=feed-schedule-v3`,
`signal_source=auto`, fill model `queue`, execution from `onchain_fills`).
`$E/data/backtests/dota_maker/LIVE` is the same run name for Dota (583 maps).

| seed0 | maps | net | pre-rebate | buy markout 30s | buy markout 300s | sell markout 30s | fill rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| LoL | 945 | $6403 | $5171 | **+0.51¢** | +2.83¢ | −0.59¢ | 0.121 |
| Dota | 583 | $2608 | $2120 | **−0.38¢** | +1.25¢ | −0.14¢ | 0.062 |

Red flag to explain: LoL backtest BUY fills gain in the first 30 s (positive short markout).
Maker buys usually get adversely selected at 30 s, as in Dota.
Files per seed: `summary.json`, `manifest.json`, `results.parquet`, `fills.parquet`,
`quote_events.parquet`.

## Models

LoL production (live since 2026-09-21): `$E/data/lol/models/production/model.json`
name `20260921T095813Z`, 12 features `second, radiant_nw_adv, radiant_nw, dire_nw,
radiant_xp_adv, deaths_radiant, deaths_dire, top1_nw_adv, radiant_top1_nw_ratio,
dire_top1_nw_ratio, market_radiant_prior, market_p_radiant`, K=10 `default_sub90` ensemble,
`state_source=lolesports_window_details_grid_networth`, `xp_source=level`,
`source_lag_seconds=10`, train_matches 3825. Research twin: `$E/data/lol/models/research`.
Dota: `$E/data/new_model/{research,production}` (same 12 feature names).

## Sources

- LoL training: Riot lolesports livestats `window` + `details` frames
  (`data/lol/raw/lolesports/{windows,details}/<esports_game_id>.jsonl.gz`, synced to 2026-09-20).
  Net worth is reconstructed as totalGold minus consumed items (`src/lol/networth.py`).
  XP comes from champion level via `LOL_LEVEL_XP` (`src/shared/constants/lol.py`).
  Row clock: `src/lol/livestats_frames.py` (`find_spawn_index`, `assign_game_times`,
  `select_grid_rows`, `wall_us_for_second`), `GridRow.state_wall_us` = chosen frame wall time.
- LoL live: GRID widget socket only (`wss://api.grid.gg/widgets-v2/live/<id>?delay=zero`),
  services `series_scoreboard_v2` (clock, BLUE/RED) and `series_table` (NetWorth, XP, deaths).
  `match.json` shows `grid_delay_s: 8.0`. Docs: `$E/docs/experiments/lol-grid-widget.md`
  (GRID ~7 s behind wall, table declared 8 s, livestats ~55 s availability delay).
- Dota training: Steam/OpenDota/STRATZ minute rows; live Steam GetRealtimeStats, GRID, Oddin.
- Market: Telonex books + onchain fills (history), Polymarket WS (live).

## Recent LoL history the owner knows

- Early (before live): sides were swapped in LoL. Fixed.
- 2026-09-09 `bef88cb9` "Orient GRID-fed maps from GRID sides, not leftover Steam",
  `30ebe5b7` "Keep GRID side flips out of the pinned-binding check".
- 2026-09-19 `332e1c17` "Join LoL prepare current mid at as-of 0, not source lag"
  (before: current mid at `state_wall_us + 10 s`, label at +310 s; after: as-of 0, label +300 s).
  The owner describes the old code as "looking 10 s into the future". Verify which direction
  is right for live. `c9fb5cd2` retrained on the contemporaneous mid.
- 2026-09-16 `ff23c36f` two leagues cut (no GRID live feed: LPL, LCK Challengers).
  Mandatory 17-league whitelist `config/lol_league_whitelist.json`.
- 2026-09-21/22 archive-linking + feed replay (steps 2, 3A, 3B): backtest uses archive
  feed schedules; docs in `../betting_workspace/docs/archive-linking-and-feed-replay/`.

## Code map (LoL first, Dota for comparison)

- Collect/link: `src/lol/01_build_universe.py`, `02_fetch_telonex_books.py`,
  `03_link_lolesports.py`, `04_fetch_lolesports.py`, `lolesports_match.py`, `live_schedule.py`
- Prepare: `src/lol/05_prepare_dataset.py`, `livestats_frames.py`, `networth.py`,
  `map_build_cache.py`, `replay.py`, `types.py`, `constants.py`, `src/shared/constants/lol.py`
- Train: `src/lol/06_train_model.py`, `src/train_model/*`, `src/shared/utils/gbm.py`,
  `src/lol/lol_validation_metrics.py`
- Backtest: `src/backtest/run.py`, `lol_inputs.py`, `replay_inputs.py`, `feed_schedules.py`,
  `signals.py`, `strategy.py`, `src/strategy/*`, `src/archive_index/*`
- Live: `src/trader/grid_feed.py`, `grid_live_feed.py`, `grid_widgets.py`,
  `grid_widget_types.py`, `grid_archive.py`, `game_profile.py`, `match_meta.py`,
  `match_worker.py`, `model_server.py`, `market_prior.py`, `discovery.py`,
  `lol_league_filter.py`, `session_binding.py`, `feed_selection.py`, `source_picker.py`
- Dota: `src/collect/*`, `src/prepare_dataset/*`, `src/trader/steam_*.py`, `oddin_*.py`
- Useful scripts: `scripts/compare_lol_grid_livestats.py`, `record_lol_dual_feed.py`,
  `measure_lol_source_lag.py`, `reconstruct_lol_networth.py`, `measure_live_grid_cadence.py`
- Docs: `$E/docs/experiments/README.md` (experiment index), `$E/docs/as-is.md` (Dota),
  `../betting_workspace/.learnings/*.md`

## Data map

- Live tapes (synced today): `$E/data/trader/<match_id>/` — `match.json`
  (`game`, `market.yes_token_id`, `yes_is_radiant`, `teams`, `grid_delay_s`, `final`),
  `session.jsonl` (kinds `session_start`, `signal`, `quote`, `fill`, `session_end`),
  `grid_state.jsonl` (`received_at_utc` + raw GRID `frame` string), `core_trace.jsonl`.
  LoL ids look like `grid-<series>-m<map>`. Record kinds:
  `../betting_workspace/.shared-skills/vps-trader/log-map.md`.
  `signal` rows carry `second`, `yes_mid`, `no_mid`, `market_p_radiant`,
  `market_radiant_prior`, `radiant_fair`, `yes_fair`, `reason`, `entry_block`.
- LoL datasets: `$E/data/lol/processed/datasets/` (`training.parquet`, `validation.parquet`,
  `production_training.parquet`, `split.parquet`, `game_features.parquet`,
  `market_seconds.parquet`, `audit.parquet`, `backtest_audit.parquet`)
- LoL links/universe: `$E/data/lol/processed/lolesports_links/links.parquet`,
  `$E/data/lol/processed/universe/markets.parquet`, Gamma events `data/lol/raw/polymarket/gamma`
- Dual-feed captures: `$E/data/lol_dual_feed/`, lag captures `$E/data/live_lag/`
- Backtests: `$E/data/backtests/lol_maker/`, `$E/data/backtests/dota_maker/`

## Report format (write to `$R/reports/<your-name>.md`)

1. **TL;DR** — up to 7 findings ranked by expected impact on the live-vs-backtest gap.
2. **Findings** — per finding: ID, title, stage (collect / link / prepare / train / backtest /
   live), claim, evidence (`file:line`, command, numbers), mechanism (why live < backtest or
   why LoL < Dota), severity (high / medium / low), confidence (verified / likely / speculative),
   next check or fix proposal (words only, no code edits).
3. **Checked and OK** — what you verified as consistent, with evidence.
4. **Open questions for the owner.**
5. **Needs from VPS** (if any).
6. **Scripts and outputs** — paths under `$R/work/<your-name>/`.

Write the report in English. Keep identifiers, paths, and numbers exact.
When you finish, reply in chat with only the report path.
