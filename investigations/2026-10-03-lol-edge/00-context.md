# Run 2026-10-03: why is LoL worse than Dota, and why does nothing move it

Code repo (READ-ONLY): /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader (branch `main`, HEAD `2a0f4b7e`).
Run dir: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-03-lol-edge
Previous investigation (read it, do not redo it): /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/lol-history-20261002/report.txt (Russian; scripts next to it are runnable).
Project briefing: `esports-trader/AGENTS.md`, `esports-trader/docs/experiments/README.md` (index of every experiment, keep/drop verdicts), `esports-trader/docs/as-is.md`, `esports-trader/docs/rebuild-order.md`.

## Goal

The owner wants LoL to make money like Dota (or better). Deliver: the root cause(s) of why LoL is worse than Dota and why the last two changes (history features, training window) did nothing or hurt, each claim backed by numbers, and a ranked list of concrete fixes with the exact experiment that would confirm each.

## What the owner did and saw

Both games predict the Polymarket midpoint 300 s ahead (LightGBM, L1, 10-member `default_sub90` ensemble, 77-column catalog `DOTA_XP_FEATURE_COLUMNS` in `src/shared/utils/dota_features.py`: 17 base features + 60 temporal history columns = 12 fields × lags 1..5 min). The maker strategy (Follow300, `src/strategy/`) buys when |model − mid| ≥ 0.02 before game second 480 (`BUY_CUTOFF_SECOND`), sells when the delta reverses by 0.015, keeps positions until the map ends.

Dota, in order: added the 60 history features (`hist77`, 2026-10-02) and simultaneously widened training from `second ≤ 540` to the whole map; then fixed a history bug (grid tape took a NaN/stale pivot; now `HistoryPolicy` requires a pivot within ±30 s of the lag target, otherwise the tick is invalid and not traded, `GRID_HISTORY_POLICY` in `dota_features.py`, commit `ea5367b9`). Dota result: PnL about the same, worst map 2× better, CVaR5 much better, seeds much more stable (`docs/experiments/hist-policy-20261003.md`).

LoL, same two changes a day later: 77 columns (`a66a4191`, `cf59c757`) + full-map training, then back to `second ≤ 540` training (`9af459c0`, run `w540lv6`), then the same history fix (`histfix`, run `histfix-20261003r2`, promoted as LIVE baseline in `2a0f4b7e`). LoL result: nothing improved, and the history fix made the tails worse:

| LoL run (3 cadence seeds, 1239 whitelisted validation maps, $300 clip) | rebuild-20260930 (12 cols, ≤540) | w540lv6 (77 cols, ≤540) | histfix-20261003r2 (77 cols, ≤540, ±30 s policy) = LIVE |
|---|---|---|---|
| net PnL mean (after rebate) | 21,892 (sd 4,832) | 25,524 (sd 2,522) | 27,233 (sd 3,043) |
| buy fills | 10,112 | 11,388 | 12,981 |
| buy 300 s markout | 2.13 ¢ | 2.15 ¢ | 2.03 ¢ |
| CVaR 5 % | −455 | −457 | −617 |
| worst map (per seed) | −1,090 / −1,706 / −1,090 | −1,107 / −1,056 / −1,056 | −1,913 / −1,915 / −2,088 |
| incomplete maps (catalog) | 23 | 23 | **53** |
| maxDD/tot | 0.111 | 0.079 | 0.138 |

**Correction 11:55 (verified from `seed0/manifest.json`):** `w540lv6` ran at `max_position_levels` 6; `histfix-20261003r2` at 9 and on the new HistoryPolicy backtest code. A fourth run `cur-20261003r2` = the same w540 model at cap 9 on the new code (mean net 28,352, CVaR −592/−637/−614, worst −1,587/−1,387/−1,481, incomplete 53, late buy-300s ≈ 0). Clean pairs: `w540lv6 → cur-20261003r2` = cap + backtest code; `cur-20261003r2 → histfix-20261003r2` = history-policy retrain only (−1,119 net, CVaR flat, worst map ≈ 487 worse). Also: `cat77lv6` (77 cols, full-map, cap 6) → `w540lv6` is the clean window comparison (+2,852 net); `rebuild-20260930 → cat77*` also changed the tape (feed-schedule v6→v8, new validation parquet). See `reports/grok-timeline.md`.

Run dirs: `data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_<name>/seed{0,1,2}/{results,fills,quote_events}.parquet + summary.json + manifest.json`. `data/backtests/lol_maker/LIVE` → histfix-20261003r2. Dota baseline: `data/backtests/dota_maker/LIVE` (hist77-20261002). Tools: `scripts/report_seeds.py <run_dir> --expected-seeds 3`, `scripts/compare_backtests.py <baseline> <candidate>` (paired per map), both need `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python ...` from the esports-trader root.

## Facts already measured by the orchestrator (verified, use them)

1. **LoL edge collapses over the validation period; Dota's grows.** Equal-match chronological thirds from `report_seeds.py`:
   - LoL w540lv6: early 2026-06-04..07-30: +12.4–12.8 k net, buy-300s markout 2.2–2.7 ¢; middle 07-30..08-22: +7.3–12.3 k, 3.0–3.3 ¢; **late 08-22..09-29: +3.2–3.7 k, 0.44–0.61 ¢** (410 maps per third, 3 seeds). rebuild-20260930 late third: +1.7–2.7 k, buy-300s −0.39..+0.01 ¢. histfix late: +4.3–5.0 k, 0.41–0.63 ¢.
   - Dota LIVE: early 06-04..07-24: +11.2–12.2 k, 2.3–2.9 ¢; middle: +6.3–8.6 k, 2.2–2.3 ¢; **late 09-03..09-30: +17.5–17.9 k, 5.5 ¢** (270 maps per third).
   So the LoL backtest total is carried by June–August; the most recent month, the one closest to live, is nearly flat. Any fix must be judged on the late third, not on the total.
2. **LoL has no obvious feed lead over the market.** `docs/experiments/lol-grid-widget.md` (2026-08-28): GRID scoreboard lags wall ~7 s, GRID table 8 s, lolesports livestats ~55 s, LEC video 60 s. "A death→mid probe on archived livestats peaked at 4–5 s after the frame stamp: that is GRID / other fast books, not our 10 s join." Live LoL trades off GRID `series_table` which arrives `LOL_SOURCE_LAG_SECONDS = 11` s after the livestats frame stamp (`src/shared/constants/lol.py`); training/backtest use the same 11 s. If the market moves 4–5 s after the frame stamp, our model sees the state ~6 s after the market has already priced it. Dota: GRID lag 10 s (`source_lag_seconds=10`), tournament stream delays are longer. This is the leading structural hypothesis; it has not been measured on the whole tape or per period.
3. **Train/trade population mismatch.** Stage 06 trains on every league in the split (model.json: train_matches 2551, validation_matches 2080), the backtest and live trade only the 17-league whitelist (`config/lol_league_whitelist.json`, 1239 validation maps). The whitelist-only retrain lost in 2026-09-08 (`docs/experiments/lol-league-whitelist.md`), so "just filter" is not the answer, but the mix per period is unmeasured.
4. **Datasets** (`data/lol/processed/datasets/`): training 3.79 M rows / 2713 maps (2025-10-14..2026-06-03), validation 3.40 M rows / 2195 maps (2026-06-04..2026-09-29), production_training 7.20 M rows / 4908 maps; `second` runs 0..3640; labeled rows ≤540: 0.99 M train / 0.95 M validation; labeled <480: 0.88 M / 0.84 M. `split.parquet` (match_id, event_id, esports_game_id, game_number, start_time, event_start_time, split), `audit.parquet` (per-map include/reason, pause_count, pause_seconds, frame_count, skipped_* counts), `game_features.parquet` (dense per-second tape used for history columns), `market_seconds.parquet`, `backtest_audit.parquet`. Validation cutoff `VALIDATION_START_TIME` ≈ 2026-06-04 for both games.
5. **Previous investigation (2026-10-02) findings**, on 3-member diagnostic ensembles, rows `second < 480`, backtest maps: history adds MAE −0.014 ¢ (CI excludes 0) and DIR +0.05 ¢ (CI includes 0); full-map training vs ≤540 costs DIR −0.22 ¢ (CI excludes 0); full-map training picks fewer trees (63/52/50 vs 90/113/130 by buy-window minimum) but that is not the whole effect; dense (train) vs received (backtest/live) history tape shifts delta by ~0.10 ¢ and flips the entry threshold on 2.4 % of ticks; on 290 archive-schedule maps PnL rose 1250→2240, on synthetic-cadence maps it fell 17216→16260. New history columns get 11.6 % of split gain, 9 pp of it `total_5m`.
6. **Earlier LoL experiments** (all in `docs/experiments/`): horizons 60..900 s (300 kept), minute specialists, late-phase models, exit model (`lol-exit-model.md`, negative), cut 900, league whitelist retrain, nw-off, no-unwind, grid-v1 cadence, sizing. Read the index before proposing something already tried; if you re-propose it, say what is different now.
7. Machine: 12 cores / 32 GB, a Dota `prepare_dataset` rebuild is running in parallel and must not be disturbed. 17 agents share this box.

## Where the code is

- LoL data: `src/lol/01_build_universe.py` → `03_link_lolesports.py` → `04_fetch_lolesports.py` (livestats window/details, 10 s frames) → `05_prepare_dataset.py` (spawn anchor `LOL_SPAWN_SEARCH_SECONDS`, pauses, prior `LOL_PRIOR_WINDOW_SECONDS=61`, label join `join_market_rows`, `build_game_feature_rows`) → `06_train_model.py` (`slice_model_rows` 0..540, `load_lol_dataset` → `attach_catalog_features`, `fit_research_members` early-stops on all labeled validation rows ≤540, metrics on <480). Helpers `src/lol/livestats_frames.py`, `networth.py` (gold reconstruction), `replay.py`, `live_schedule.py`, `lol_validation_metrics.py`; constants `src/lol/constants.py`, `src/shared/constants/lol.py`, `src/shared/constants/strategy.py`.
- Dota equivalents: `src/collect/`, `src/market_data/build_market_data.py`, `src/prepare_dataset/prepare_dataset.py`, `src/train_model/train_model.py`, `src/shared/utils/dota_features.py` (`HistoryPolicy`, `attach_catalog_features`, `GRID_HISTORY_POLICY`), `src/shared/utils/gbm.py` (LightGBM params, ensemble, labels, metrics).
- Backtest: `src/backtest/run.py`, `signals.py` (grid-v1 cadence bands, `GRID_V1_FIRST_TICK_SECOND = {dota 48, lol 76}`, LoL decision lag 11 s, history from received tape), `lol_inputs.py`, `feed_schedules.py`, `live_archives.py`, `strategy.py`, `postprocess.py` (`calculate_cvar_5`), `report.py`; strategy `src/strategy/{engine,policy,quoting,kill_gate,signals}.py`; Nautilus framework `../prediction-market-backtesting` (read-only).
- Live trader: `src/trader/match_worker.py` (feed events, history buffer), `grid_live_feed.py`, `grid_feed.py`, `lol_prior.py`, `lol_league_filter.py`, `model_server.py`, `session_*.py`; config `config/trading.toml`. Live archives synced to `data/trader/<match_id>/` (980 dirs, Dota + LoL), onchain fills under `data/raw/telonex/...` / `data/lol/raw/...` (see `scripts/sync_collector_parquet.py`, `scripts/backfill_trader_fills.py`).
- Telonex books: `data/lol/raw/telonex/polymarket/<channel>/...` (136 GB, read only the days/assets you need; `src/shared/utils/telonex_book.py`, `src/backtest/telonex_local.py`).

## Hard rules (all agents)

- You run with auto-approve — no permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run: no `rm` of existing files, no `mv` onto existing files, no `git clean` / `git reset --hard` / `git checkout --` / `git stash` / `git push` / `git commit`, no `kill`/`pkill` outside your own `work/<name>/` processes, no dropping/truncating data files, dirs, rows, tables, or branches. If something is in the way, write beside it or stop and report.
- READ-ONLY on `esports-trader`, `poly-maker`, `prediction-market-backtesting`, `polymarket-collector`. Do not edit code. Do not touch `data/` contents. Write only to `work/<name>/` and `reports/<name>.md`. The owner may edit the repo during the run (`git show HEAD:<path>` if a file looks half-written).
- NO training, NO backtests, NO `make prepare/train/lol-prepare/lol-train/catalog/prices/universe`, NO fetches that write under `data/`, NO SSH to the VPS, NO trader restart, NO deploy. If a hypothesis needs a retrain or a backtest, write the exact command and the expected numbers under "Proposed experiments" in your report; the orchestrator runs them.
- Allowed compute: read-only Python/pandas/pyarrow/LightGBM-predict analyses from the esports-trader root via `PYTHONPATH=src:scripts uv run python work/<name>/<script>.py` (add `:../prediction-market-backtesting` and `--group backtest` only if you import `backtest.*`). Run with `nice -n 10`. One Python process at a time per agent, ≤ 4 GB RSS: read parquet with `columns=[...]`, filter by match_id or second, sample maps when the full tape is not needed, write intermediate parquet under `work/<name>/` (≤ 200 MB per file). Scoring the published ensemble on a few hundred thousand rows is fine; fitting LightGBM is not (except an explicitly allowed tiny diagnostic in your brief).
- No accounts, no money, no signups. Browser only via `agent-browser --session <name>`; never `agent-browser close --all`.
- Every claim with evidence: `file:line` for code, command + the numbers it printed for data, URL + date for web. Mark each finding `verified` (you ran it), `likely` (strong indirect evidence), or `speculative`. Negative results are results: say what you checked and found nothing.
- Do not restate the facts above as findings. Build on them.

## Report format (`reports/<name>.md`)

```
# <name>: <one-line topic>
Status: WIP | FINAL          <- line 2, exactly "Status: FINAL" when complete
## Verdict (≤10 lines)        <- answer the brief's question; what you found, how sure
## Findings                   <- numbered, most important first; each: claim, evidence (file:line / command + numbers), verified|likely|speculative
## What I ruled out
## Proposed experiments       <- exact commands / code change sketch, expected effect, what number to watch (late third!), cost
## Scripts                    <- paths under work/<name>/ and how to rerun
```

Update the report file as you go (it is the only channel; chat is not read). When done, make line 2 exactly `Status: FINAL` and reply with only the report path.

## Agents

| name | model | topic |
|---|---|---|
| decay-sol | codex gpt-6.1-sol | LoL edge decay Jun→Sep: where did it go (time × league × liquidity × second) |
| decay-dev | devin swe-2-max | same question, independent: market microstructure per period (spread, depth, who moves the mid) |
| leadlag-sol | codex gpt-6.1-sol | lead/lag between LoL market mid and game state vs Dota, per period: do we have an information edge at 11 s |
| leadlag-dev | devin swe-2-max | web/browser research: LoL feed delays (GRID, livestats, broadcast), Polymarket LoL participants and bots, what changed Aug–Sep 2026 |
| tails-opus | claude opus 5.5 high | histfix vs w540lv6: why tails doubled, 53 vs 23 incomplete maps, +14 % fills; paired per-map forensics |
| tails-dev | devin swe-2-max | fill-level forensics of the 5 worst histfix maps and the 5 largest paired losses |
| hist-dev | devin swe-2-max | LoL history features parity train (dense tape) vs backtest (received tape) vs live; invalid-tick rate under HistoryPolicy |
| pipeline-opus | claude opus 5.5 high | adversarial code review of the LoL data pipeline vs Dota: labels, lag, pauses, spawn anchor, prior, replay, live |
| labels-dev | devin swe-2-max | data audit of LoL label/time alignment, pause handling, spawn-anchor errors and their PnL |
| leagues-dev | devin swe-2-max | train vs trade population: per-league / per-tier model quality and PnL per period; patch/season shifts |
| features-dev | devin swe-2-max | feature audit: predictive power of each LoL feature vs Dota at matched seconds; networth reconstruction and XP-from-level accuracy |
| live-dev | devin swe-2-max | live LoL trader results vs backtest on the same maps (parity, slippage, PnL by month) |
| market-dev | devin swe-2-max | backtest realism for LoL: book depth, fill model, PnL concentration, rebate share, vs Dota |
| window-dev | devin swe-2-max | the full-map vs ≤540 paradox: label distribution by second, L1 + early stopping, principled fix (weights / window / curriculum) |
| grok-code | cursor grok-4.7 (orchestrator subagent) | exact trace of the LoL signal path backtest vs live for features, lag, history, cadence |
| grok-timeline | cursor grok-4.7 (orchestrator subagent) | timeline of every LoL-affecting change since 2026-09-20 mapped to backtest numbers |
| grok-compare | cursor grok-4.7 (orchestrator subagent) | Dota vs LoL pipeline design diff table and ranked hypotheses |

Overlaps are deliberate. Do not coordinate with other agents; the orchestrator reconciles.
