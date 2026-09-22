# Step 3B — write implementation plan only

You are the planner. Do not implement. Do not commit. Do not edit product code.

## Output contract

Write the FULL plan to:
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-3b.md`
Chat: ONLY that path.

## Context

- Spec: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` — «Шаг 3» / 3B and «Модели и интерфейс» / LoL / reports
- Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main` (3A landed: `199705d4`, `21274237`)
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
- Do NOT use `betting_workspace/current-task/`. Do not plan edits to `../polymarket-collector` or esports-trader onchain scripts. `../poly-maker` frozen.
- Follow `AGENTS.md` and ponytail. Train + backtests happen AFTER this step, not in the plan as a required run.

## What 3B is

Simplified ordinary Dota/LoL backtest launch. Remove `--policy archive|current` and implicit old-signal `--live-since` from the normal path; keep `make parity` as a separate diagnostic. Cohort filter «from this match» uses catalog match time, not rsync mtime. `--lag-seconds` / `--cadence-mean-interval` not source-specific knobs; reject applying them to archive schedules. LoL uses the same schedule/mode mechanism with GRID LoL profile; existing whitelist stays on backtest not train. Mixed-model report: schedule source + model per match, exclusion reasons, group counts. Manifest: schedule fingerprints separate from dataset/model, admission/extract rule versions, policy, seeds, cohort, exclusions. Dataset rebuild must not invalidate schedule cache.

Trace the live CLI/Makefile/scripts/report after 3A. Name actual flags and files. Tests, ruff, basedpyright. No full suite during a live map.
