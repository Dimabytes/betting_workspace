# PLAN ONLY — US-001

You are the PLANNER. Do not implement. Do not edit product code. Do not run `yarn check` as a gate. Read, then write a plan.

## Skill

Read and follow `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md` for context about how implementation later works, but this turn is planning only.

## Task

Write an execution plan for feature step **US-001** only.

Plan path (create/overwrite this exact file):

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-001.md`

When finished, also write a short status file (path only + 10-line summary, not the full plan):

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-001-plan.md`

Reply in the terminal with only: the two file paths. Do not dump the plan in chat.

## Scope

Workspace: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace`

Product code lives in sibling repos, not in betting_workspace:

- `../polymarket-collector` — implement here
- `../esports-trader` — fixtures to copy from; do not change trader in US-001 unless a fixture copy requires it (it should not)
- `../poly-maker` — FROZEN, never edit

Feature: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json`

Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`

## Mandatory reads before planning

1. `current-task/feature.json` (US-001 changes, FRs, resolved questions, non-goals)
2. All `relatedSources` listed in feature.json
3. `../polymarket-collector/AGENTS.md`
4. `../polymarket-collector/docs/learnings.md`
5. `../polymarket-collector/.cursor/rules/ponytail.mdc` if present, else workspace ponytail: follow YAGNI / reuse / stdlib / fewest files
6. Existing collector schema/writer/tests so the plan names real files, not invented modules

## US-001 acceptance (must be covered in the plan)

- Catalog from both archive `metadata/markets/<condition_id>.json`; token → game/condition/outcome index/sibling; IDs stay strings; missing observation timestamps conservatively include the market
- Decode OrderFilled + OrdersMatched for CTF Exchange `0xE111180000d2663C0091e4f400237545B87B996B` and Neg Risk `0xe2222d279d744050d28e00520010520000310F59`; own + mirrored sibling rows; drop taker-aggregate fill against actual exchange address; price 6-decimal half-even; amount/fee decimal strings; ABI integers bigint
- DuckDB Parquet with 26 ONCHAIN_SCHEMA columns in Telonex order and physical types: INT64 `block_number`/`block_timestamp_us`, INT32 `transaction_index`/`log_index`/`chain_id`, UINT8 `outcome_id`, BOOLEAN `mirrored`, remaining STRING
- Copy needed recorded Telonex/RPC fixtures from esports-trader into collector tests; document any decode-field mismatch with Python before renaming fields to hide it
- Tests: conflicting pair/index (diagnostic, not complete); large token IDs not via JS number; empty table with full schema; sort order block_number/transaction_index/log_index/contract_address/mirrored
- Tests pass, typecheck passes (`yarn check` in polymarket-collector)

## Plan file format

Markdown. Include:

1. Goal of US-001 in one paragraph
2. Files to add/change (real paths after reading the tree)
3. Implementation order (small commits/checkpoints inside the step)
4. Test plan matching the required cases
5. Explicit non-goals (RPC, planner/ledger, compose, VPS — later steps)
6. Risks / gotchas from Python/Telonex parity
7. How the implementer should verify (`yarn check`)

Do not implement. Stop after writing the two files.
