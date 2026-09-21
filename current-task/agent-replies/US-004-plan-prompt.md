# PLAN ONLY — US-004

You are the PLANNER. Fresh session. Do not implement.

## Skill

Read `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`. Planning only.

## Task

Plan **US-004** only.

Write:
- `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-004.md`
- `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-004-plan.md` (10 lines)

Reply with only those two paths.

## Scope

Collector: `../polymarket-collector`. poly-maker FROZEN.
US-001..003 shipped. Reuse `runOnchainPass`, settings, ledger, publish.
US-005 will do VPS enable + Python deletion; this step is compose/import/local live day.

## US-004 acceptance (from feature.json)

- Compose service `onchain` on image `polymarket-collector:latest` with `command: ["node", "dist/onchain-main.js"]`, both archive bind mounts, dedicated on-chain state mount, no POLYMARKET_TAG_ID, two Alchemy URL env vars; existing four services stay collect/compact
- Process: single-writer lock, 5-minute catch-up after startup, SIGTERM via NodeRuntime, disk-stop without marking coverage complete, Telegram/logs with current UTC day, last full day, lag, remaining ranges, fills per game, retries, each endpoint state, pending/failed reason
- Import command on the same image: inventory of closed `.parquet` only; hash/schema/asset_id checks; statuses `legacy_imported`, `legacy_empty_unverified`, `full_day`; empty files of unknown origin are not full_day; writes ONCHAIN_START_DATE into the bootstrap report
- Compactor running at the same time does not delete `.onchain/` staging
- Agent runs one live Alchemy catch-up of a completed UTC day into a separate local directory; esports-trader PyArrow reads physical types; record time, requests, bytes, CU estimate; no speed claim
- Tests pass, typecheck passes, local live Alchemy day + import dry-run

Read design spec, AGENTS.md, learnings, compose.yaml, US-003 modules.

Do not implement.
