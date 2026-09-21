# IMPLEMENT US-003

You are the IMPLEMENTER. Fresh session. Work only on US-003. Do not start US-004+.

## Skill

Read and follow `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`.

## Inputs

- Feature: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json`
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`
- Plan: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003.md`
- Collector: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`
- US-001/002 shipped (`6467487`, `a6a7ce4`, `d230388`, `3e0f892`). Honor the US-002 contract: `OnchainRpc.run` is `Effect<A, RpcError>`; skip is `unlessCommitted`; `markCommitted(range)` has no receipt.
- poly-maker is FROZEN

Read collector `AGENTS.md` and `docs/learnings.md` before coding.

## Rules

- Implement US-003 only.
- `yarn check` must pass.
- Do NOT commit broken code.
- Commit in polymarket-collector: `feat: [US-003] - Planner, durable ledger, and day publication`
- Set US-003 `passes: true` only after tests/typecheck pass.
- APPEND to progress.txt.
- Do not git push.

## Done report

Write `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-003-impl.md`

Reply in the terminal with only that file path.
