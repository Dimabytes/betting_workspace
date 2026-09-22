# Step 3A — implement now

You are a NEW session. Implement step 3A only. Do not start 3B. Do not train. Do not backtest full seeds. Do not push.

## Output contract

1. Implement from the plan in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main`.
2. Targeted tests from the plan + `make lint` + `uv run basedpyright`. No full pytest suite. No live/paper restart.
3. APPEND to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
4. Commit: `feat: [step 3A] - features by game-second and archive schedule`
5. Write `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/agent-replies/step-3a-impl.md`
6. Chat ONLY that path.

## Plan (source of truth)

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-3a.md`

Spec §3A: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md`

Read `AGENTS.md`. Ponytail. No `dict[str, Any]`. Frozen dataclasses not anonymous tuples.

## Do not touch

- `betting_workspace/current-task/` (other on-chain feature)
- `../polymarket-collector`
- esports-trader onchain: `telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`
- `../poly-maker`
- If git status shows dirty files you did not write, leave them.

## Hard constraints

- Features by `(game, match_id, game_second)` — not `second - lag`.
- Archive tick `(arrival, game_second)` as-is; no extra 10/15/17s shift.
- Full schedule → use it. No archive → seeded GRID. Incomplete → exclude with reason.
- GRID research / Oddin no-XP / LoL research. Observed clocks in strategy.
- 3B is CLI/policy/report — not this step.
