# Brief: s7-impl — implement STEP-007

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-007`. Plan: `W/tasks/two-sided-live-b/plans/STEP-007.md`.
2. Implement the plan. If the plan is wrong against the real code, follow the code and the feature.json step, and write the deviation in the report.
3. Run the step's tests, `make lint` on staged files, and the typecheck. Do not commit broken code.
4. Do NOT set `passes` in feature.json: the orchestrator sets it after review. Append to `W/tasks/two-sided-live-b/progress.txt`.
5. One commit in E on main for the step. Do not commit in W.
6. Later the orchestrator sends you review findings in this same session. Then fix what is right, re-run checks, amend your own step commit (or add one commit), update the report, and set `Status: FINAL` again.

Report `R/reports/s7-impl.md`: what changed (files), deviations from the plan, commands with results (test counts, lint, typecheck), commit hash, open issues.

## Worktree (overrides "Work on main in E" and "one implementer" for this step)

- Another implementer fixes STEP-006 review findings in the main E checkout right now. You work ONLY in the worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s7` (E7), branch `two-sided-s7`, based on main `f605a6b9`, own `.venv` already synced. Every path "E" in your brief and plan means E7 for edits, tests, lint and the commit.
- Never cd into, edit, stage, stash or commit in the main E checkout. Never switch branches. Commit on `two-sided-s7`. The orchestrator cherry-picks onto main later. For review fixes later, add a commit on the same branch (amend is fine too: the branch is yours).
- E7 has no `.env` and no `data/` symlinks. Do not copy or link `.env`. Tests must not need the network or `.env`: the plan notes that existing `run()` tests in `tests/test_trader_wallet_host.py` call Disir through `refresh_now` with a token from `.env`; patch that out in the new test, and if an existing test fails only because `.env`/network is missing in E7, say so in the report instead of changing it.
- E7 already has: STEP-001..006 (TwoSidedWorker `77a85364`, may get a small review-fix commit on main that you will not see), STEP-008 compose/config_b (`a58d19c9`), STEP-009 summarize.py (`2ebea5e6`), merge deadline fix (`f605a6b9`). Read the real code; it wins over the plan.
- STEP-009 wrote these strings into the vps-trader runbook from your plan; keep them exactly or report the change: `trader wallet: strategy=%s signature_type=%d funder=%s`, the empty-whitelist refusal `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist`, and `discovery skip reason=title_whitelist`.
