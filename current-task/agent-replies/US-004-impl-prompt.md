# IMPLEMENT US-004

You are the IMPLEMENTER. Fresh session. Work only on US-004. Do not start US-005.

## Skill

Read and follow `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`.

## Inputs

- Feature, progress, plan: `current-task/` (plan at `plans/US-004.md`)
- Collector: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`
- US-001..003 shipped including `4ebca5b`
- poly-maker FROZEN
- Alchemy URLs: `esports-trader/.env` (`ALCHEMY_POL_ENDPOINT`, `ALCHEMY_POL_ENDPOINT_RESERVE`). Never log/manifest the URLs.
- `ssh sun` BatchMode works for rsync of VPS sidecars into a local scratch dir if needed.

## Rules

- Implement US-004 including the **local live Alchemy catch-up of one completed UTC day** into a separate directory, PyArrow physical-type check, and import dry-run. Record time/requests/bytes/CU in the done report. No speed claim.
- `yarn check` green. Commit `feat: [US-004] - Compose onchain service, import CLI, local live Alchemy day`. Do not push.
- Set US-004 `passes: true` only after tests + live day + import dry-run.
- APPEND progress.txt.

## Done report

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-004-impl.md`

Reply with only that path.
