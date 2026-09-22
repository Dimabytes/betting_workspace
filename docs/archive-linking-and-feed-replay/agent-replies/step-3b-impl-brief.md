# Step 3B — implement now

You are a NEW session. Implement step 3B only. Do not train. Do not run full-seed backtests. Do not push. Do not start a new experiment-mode system.

## Output contract

1. Implement from the plan in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main`.
2. Targeted tests from the plan + `make lint` + `uv run basedpyright`. No full pytest suite. No live/paper restart.
3. APPEND to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
4. Commit: `feat: [step 3B] - ordinary backtest launch and mixed-model report`
5. Write `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/agent-replies/step-3b-impl.md`
6. Chat ONLY that path.

## Plan (source of truth)

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-3b.md`

Spec §3B: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md`

Read `AGENTS.md`. Ponytail.

## Do not touch

- `betting_workspace/current-task/`
- `../polymarket-collector`
- esports-trader onchain: `telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`
- `../poly-maker`
- `make parity` / `trader.replay_core_trace` (keep as separate diagnostic)
- Dirty files you did not write

## Hard constraints

- Remove `--policy archive|current` and `--live-since` from the ordinary path; no implicit core_trace signals.
- `--since-match` is a catalog-time cohort filter, not archive mtime.
- Reject lag/cadence overrides on any archive-schedule match.
- LoL uses the same schedule/mode mechanism; whitelist stays on backtest not train.
- Report: schedule source + model per match, exclusion reasons, group counts.
- Manifest: schedule fingerprints separate from dataset/model.
