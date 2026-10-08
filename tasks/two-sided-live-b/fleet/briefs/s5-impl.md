# Brief: s5-impl — implement STEP-005

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-005`. Plan: `W/tasks/two-sided-live-b/plans/STEP-005.md`.
2. Implement the plan. If the plan is wrong against the real code, follow the code and the feature.json step, and write the deviation in the report.
3. Run the step's tests, `make lint` on staged files, and the typecheck. Do not commit broken code.
4. Do NOT set `passes` in feature.json: the orchestrator sets it after review. Append to `W/tasks/two-sided-live-b/progress.txt`.
5. One commit in E on main for the step. Do not commit in W.
6. Later the orchestrator sends you review findings in this same session. Then fix what is right, re-run checks, amend your own step commit (or add one commit), update the report, and set `Status: FINAL` again.

Report `R/reports/s5-impl.md`: what changed (files), deviations from the plan, commands with results (test counts, lint, typecheck), commit hash, open issues.

## Changes since the plan was written (must handle)

- STEP-003 (commit `c779fa21`) added `WalletStateStore.hold_merge(...)` / `release_merge(...)`: while a merge is held, chain/REST reconcile skips the size-down of those tokens. PairMerger must call `hold_merge` for both tokens BEFORE the relayer submit and `release_merge` on EVERY terminal outcome (merged / failed / unknown / exception), after core delivery (`consume_core_outbox`). The hold is process memory.
- STEP-004 (commit `7b1b6990`): `RecoveryCoordinator.accept_if_proven` now replays pending outbox rows before it reads the ledger. Read the real STEP-003/004 code before you start; it wins over the plan.
- Never send a real merge, never call the relayer or RPC, never use `*_B` keys. Tests use fakes only.
