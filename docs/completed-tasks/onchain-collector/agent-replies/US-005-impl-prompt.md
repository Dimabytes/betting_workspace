# IMPLEMENT US-005

You are the IMPLEMENTER. Fresh session. Work only on US-005.

## Skill

Read `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`.

## Inputs

- Plan: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-005.md`
- Feature/progress: `current-task/`
- Trader: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`
- Collector: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector` (US-001..004 shipped; no collector changes unless a real gap)
- poly-maker FROZEN

## Hard constraints

- Parallel work exists. Stage ONLY US-005 paths. Do not `git reset --hard`, `git checkout --` of unrelated files, `git clean -fd`, rebase, or amend others. Do not touch `betting_workspace/docs/archive-linking-and-feed-replay/`. Trader worktree is dirty from another feature (`backtest/*`, `telonex_local.py` extra hunks) — change only the import line in `telonex_local.py`.
- Do not git push. Do not `docker compose down`. Do not start VPS `onchain` (operator will git pull + up).
- `ssh sun` BatchMode as root is OK for rsync/inventory of closed parquet. If a command needs an SSH password, stop and write the remaining operator steps.
- 31k 20-column legacy files: follow plan default **(a) documented exclusion**. Do not loosen collector schema.
- Secrets: `ALCHEMY_POL_ENDPOINT` + `ALCHEMY_POL_ENDPOINT_RESERVE` → VPS `ONCHAIN_RPC_URL_A/B` by operator; never commit keys.
- Do not run the full trader suite during a live map.

## Done

Commit trader: `feat: [US-005] - VPS enable and delete the Python on-chain collector`
Set US-005 `passes: true` only after Mac-side tests + runbook written. APPEND progress.txt.

Write `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-005-impl.md` including the operator runbook (git pull, env, import UID 10001, ONCHAIN_START_DATE, `up -d onchain`). Reply with only that path.
