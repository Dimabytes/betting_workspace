# PLAN ONLY — US-002

You are the PLANNER. Fresh session. Do not implement. Do not edit product code.

## Skill

Read `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md` for how implementation later works. This turn is planning only.

## Task

Write an execution plan for feature step **US-002** only.

Plan path (create/overwrite):

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-002.md`

Short status (10 lines):

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-002-plan.md`

Reply in the terminal with only those two file paths.

## Scope

Workspace: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace`

- Implement later in `../polymarket-collector`
- `../esports-trader` is reference/fixtures only for this step
- `../poly-maker` is FROZEN — never edit

Feature: `current-task/feature.json`
Progress: `current-task/progress.txt`
US-001 already shipped in collector: commits `6467487` and `a6a7ce4`. Reuse `OnchainDecodeError`, `parseRpcLog` Effect, catalog/writer. Do not re-plan US-001.

## Mandatory reads

1. `current-task/feature.json` (US-002 changes, FRs, resolved questions, non-goals)
2. All `relatedSources` in feature.json, especially `../polymarket-collector/docs/superpowers/specs/2026-09-20-onchain-collector-design.md`
3. `../polymarket-collector/AGENTS.md` and `docs/learnings.md`
4. US-001 modules: `src/onchain-decode.ts`, `src/onchain-catalog.ts`, `src/schema/onchain.ts`
5. Existing Effect HttpClient usage in the collector
6. Python RPC client in `../esports-trader/scripts/parity_onchain_rpc.py` and `src/shared/utils/telonex_onchain.py` — copy semantics, not code

## US-002 acceptance

- JSON-RPC over Effect HttpClient, chain 137, both exchange addresses, topics OrderFilled and OrdersMatched
- Parse JSON-RPC error bodies on HTTP 400
- Typed errors: range-too-wide, rate-limit, quota, transport, invalid payload
- No filterStatusOk that drops the range-too-wide message
- Two Redacted endpoints, each with own CU weights, concurrency ceiling, 429/Retry-After pause, and quota handling
- A failed task may move to the other endpoint; a durably committed range is never reassigned
- UTC day bounds: first block with timestamp >= start and first block with timestamp >= end, end exclusive
- Readiness requires `finalized` head; no automatic latest fallback
- Disagreeing chain id or control block hashes leave the day pending
- Unsupported pre-current-Exchange ranges fail with explicit unsupported-period error, not an empty ready file
- Tests: fake Layers + recorded JSON-RPC fixtures: 400/range split, 429, partial quota, lost/invalid payload, disagreeing endpoints, unfinalized head
- Tests pass, typecheck passes (`yarn check`)

## Plan file format

1. Goal in one paragraph
2. Files to add/change (real paths after reading the tree)
3. Implementation order
4. Test plan matching required cases
5. Non-goals (planner/ledger/publish/compose/VPS are later steps). Durable "committed range never reassigned" may need a stub interface if the ledger is US-003 — say so explicitly rather than implementing the ledger here
6. Risks (Alchemy 400 bodies, CU accounting, finalized vs latest)
7. Verification: `yarn check`

Do not implement. Stop after writing the two files.
