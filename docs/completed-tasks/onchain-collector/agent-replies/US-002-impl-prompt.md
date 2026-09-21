# IMPLEMENT US-002

You are the IMPLEMENTER. Fresh session. Work only on US-002. Do not start US-003+.

## Skill

Read and follow `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`.

## Inputs

- Feature: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json`
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`
- Plan: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-002.md`
- Collector: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`
- US-001 already shipped there (`6467487`, `a6a7ce4`) — reuse decode/catalog/writer; do not rewrite them unless a tiny hook is required
- poly-maker is FROZEN

Read collector `AGENTS.md` and `docs/learnings.md` before coding.

## Rules

- Implement US-002 only, matching the plan and feature.json changes list.
- Follow existing collector patterns (Effect.gen, tagged errors, no barrel index.ts).
- `yarn check` in polymarket-collector must pass.
- Do NOT commit broken code.
- Commit in polymarket-collector. Message: `feat: [US-002] - RPC client, two endpoints, UTC block bounds` unless a stronger convention exists.
- Set US-002 `passes: true` in feature.json only after tests/typecheck pass.
- APPEND to progress.txt (never replace).
- Do not git push.

## Done report

Write:

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-002-impl.md`

Include files changed, tests run, commit hashes, leftover risks. Reply in the terminal with only that file path.
