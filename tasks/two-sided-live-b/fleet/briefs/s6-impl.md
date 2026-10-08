# Brief: s6-impl — implement STEP-006

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-006`. Plan: `W/tasks/two-sided-live-b/plans/STEP-006.md`.
2. Implement the plan. If the plan is wrong against the real code, follow the code and the feature.json step, and write the deviation in the report.
3. Run the step's tests, `make lint` on staged files, and the typecheck. Do not commit broken code.
4. Do NOT set `passes` in feature.json: the orchestrator sets it after review. Append to `W/tasks/two-sided-live-b/progress.txt`.
5. One commit in E on main for the step. Do not commit in W.
6. Later the orchestrator sends you review findings in this same session. Then fix what is right, re-run checks, amend your own step commit (or add one commit), update the report, and set `Status: FINAL` again.

Report `R/reports/s6-impl.md`: what changed (files), deviations from the plan, commands with results (test counts, lint, typecheck), commit hash, open issues.

## Changes since the plan was written (must handle)

- The plan was written against plans/STEP-005.md. The real STEP-005 code (commit `ec9ebe90`, `src/trader/ctf_merge.py`, `src/trader/pair_merge.py`) differs after two review rounds. Read it first; it wins over the plan:
  - `PairMerger.merge_all()` raises `MergeAccountingError(tx_hash, qty)` when a mined merge is still unbooked. The worker must NOT continue cleanup (end_snapshot, zero_token_sizes, unregister_worker) while that is raised: retry `merge_all` (it retries only `apply_merge`, never the transaction) with a bounded backoff, alert, and keep the hold. Add a worker test for it.
  - `merge_all` waits for an in-flight merge; the whole merge call has an absolute 190 s deadline (`MERGE_CALL_TIMEOUT_S`). `cancel()` blocks new submits and does not cancel a running one. Hold/release of tokens is inside PairMerger already.
- STEP-008 is on main (`a58d19c9`): `compose.yaml` live_b, `config_b/trading.toml`, stop_grace 240 s. Do not edit those files.
- STEP-009 is built in parallel in a separate worktree (summarize.py + vps-trader SKILL.md). Do not edit `src/dashboard/summarize.py`.
