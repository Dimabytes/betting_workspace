# Step 3A — write implementation plan only

You are the planner. Do not implement. Do not commit. Do not edit product code.

## Output contract

Write the FULL plan to:
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-3a.md`
In chat reply with ONLY that path.

## Context

- Spec: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` section «Шаг 3» / 3A
- Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main`
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
- Step 2 is done (commits de273edd, 0e40b87c, 0d1ae767). Read those contracts (`match_links`, catalog timing, `archive_index` schedules).
- Do NOT use `betting_workspace/current-task/` — different on-chain collector feature. Do not plan edits to `../polymarket-collector` or esports-trader onchain scripts (`telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`).
- `../poly-maker` is frozen.
- Follow `AGENTS.md` and ponytail. Train + backtest runs happen AFTER 3A and 3B, not in this step.

## What 3A is

From the spec: game features read by `(game, match_id, game_second)` independent of the train lag shift. Archive schedule applied with the chosen research model. No extra lag on the archive (arrival, game_second) pair. Missing feature row = dataset readiness error, not live-archive substitution. Automatic mode: full schedule → use it; no archive → existing seeded GRID cadence; incomplete archive → exclude with reason. GRID research vs Oddin no-XP vs LoL research. Strategy gets observed clocks (phase/paused/game-second/terminal), not `paused=False` stubs. Causal market price at arrival. Out of scope: CLI/`--policy` cleanup and mixed-model report (3B), train-lag change, poly-maker.

Trace the real dataset + backtest path. Name actual files. Include tests, ruff, basedpyright. No full suite during a live map.
