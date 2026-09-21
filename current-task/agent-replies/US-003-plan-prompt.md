# PLAN ONLY — US-003

You are the PLANNER. Fresh session. Do not implement. Do not edit product code.

## Skill

Read `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md` for how implementation later works. This turn is planning only.

## Task

Write an execution plan for feature step **US-003** only.

Plan: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003.md`
Status (10 lines): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-003-plan.md`

Reply in the terminal with only those two file paths.

## Scope

- Implement later in `../polymarket-collector`
- `../poly-maker` is FROZEN
- US-001 and US-002 already shipped. Reuse catalog, decode, writer, RPC pool, `unlessCommitted`, `OnchainLedger` seam. Do not re-plan them.

Shipped commits: `6467487`, `a6a7ce4`, `d230388`, `3e0f892`.

Important US-002 contract after review: `OnchainRpc.run` is `Effect<A, RpcError>` only. Committed-range skip is `unlessCommitted(ledger, range, effect)` next to the ledger. `markCommitted(range)` has no receipt arg. Planner owns “do not resubmit a committed chunk.”

## Mandatory reads

1. `current-task/feature.json` (US-003, FRs, resolved questions, non-goals)
2. `../polymarket-collector/docs/superpowers/specs/2026-09-20-onchain-collector-design.md`
3. Collector `AGENTS.md`, `docs/learnings.md`
4. US-001/002 modules: `onchain-catalog.ts`, `onchain-decode.ts`, `onchain-writer.ts`, `onchain-rpc.ts`, `onchain-days.ts`, `onchain-ledger.ts`, `onchain-config.ts`, `schema/onchain.ts`, `schema/manifest.ts` (must keep rejecting `onchain_fills`)
5. Existing compact staging/atomic-rename patterns if any

## US-003 acceptance

- Planner lists unfinished days from required `ONCHAIN_START_DATE` using a catalog fingerprint snapshot
- A new token after a scan marks that pair uncovered without invalidating other tokens' ready files
- Contradictory pairs get a diagnostic status and are not complete
- Durable ledger stores committed whole-block chunks (bounds, addresses, decoder version, catalog fingerprint, control block hashes), including successful empty ranges
- Restart repeats only uncommitted work; out-of-order completion cannot advance a cursor through a hole
- Stage under each game archive `.onchain/`, then atomic-rename into `parquet/onchain_fills/asset_id=<token>/<YYYY-MM-DD>.parquet`
- Empty files publish only after confirmed coverage for markets that could trade that day
- Each row's timestamp matches the filename UTC date
- Publish a per-game/day on-chain manifest last (files, sizes, hashes, row counts, coverage, catalog/decoder versions, control blocks, provenance)
- Compact Manifest still rejects channel `onchain_fills`
- A UTC day is complete only when both games are ready
- Crash tests: kill before durable chunk, after chunk, and between the two game publishes; restart finishes without gaps/duplicates and without deleting committed ledger rows
- Tests pass, typecheck passes (`yarn check`)

## Plan file format

1. Goal
2. Files to add/change (real paths)
3. Implementation order
4. Test plan including the three crash cases
5. Non-goals (compose/onchain-main/import/VPS/live Alchemy = US-004)
6. Risks
7. Verification

Do not implement.
