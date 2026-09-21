# IMPLEMENT US-001

You are the IMPLEMENTER. Work only on US-001. Do not start US-002+.

## Skill

Read and follow `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`.

## Inputs

- Feature: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json`
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`
- Plan: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-001.md`
- Collector: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`
- Trader (fixtures only): `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`
- poly-maker is FROZEN — never edit `/Users/dimabytes/work/polymarket/dota_2_bot/poly-maker`

Read the collector `AGENTS.md` and `docs/learnings.md` before coding.

## Rules

- Implement US-001 only, matching the plan and the feature.json changes list.
- Keep changes focused. Follow existing collector patterns (Effect.gen, typed errors, no barrel index.ts).
- `yarn check` in polymarket-collector must pass.
- Do NOT commit broken code.
- Commit in the repo you changed (`polymarket-collector`, and betting_workspace only for plan/progress/feature.json if you update those). Message: `feat: [US-001] - Catalog, ABI decode, and ONCHAIN_SCHEMA parquet` unless that repo has a stronger convention.
- Set US-001 `passes: true` in feature.json only after tests/typecheck pass.
- APPEND to progress.txt (never replace).
- Do not git push.

## Done report

Write a short completion report to:

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-001-impl.md`

Include: files changed, tests run, commit hashes, leftover risks. Then reply in the terminal with only that file path.
