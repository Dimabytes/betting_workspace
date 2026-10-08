# Brief: s8-impl — implement STEP-008

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-008`. Plan: `W/tasks/two-sided-live-b/plans/STEP-008.md`.
2. Implement the plan. If the plan is wrong against the real code, follow the code and the feature.json step, and write the deviation in the report.
3. Run the step's tests, `make lint` on staged files, and the typecheck. Do not commit broken code.
4. Do NOT set `passes` in feature.json: the orchestrator sets it after review. Append to `W/tasks/two-sided-live-b/progress.txt`.
5. One commit in E on main for the step. Do not commit in W.
6. Later the orchestrator sends you review findings in this same session. Then fix what is right, re-run checks, amend your own step commit (or add one commit), update the report, and set `Status: FINAL` again.

Report `R/reports/s8-impl.md`: what changed (files), deviations from the plan, commands with results (test counts, lint, typecheck), commit hash, open issues.

## Worktree (overrides "Work on main in E" and "one implementer" for this step)

- Another implementer works in the main E checkout on STEP-005/006/007 right now. You work ONLY in the worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8` (E8), branch `two-sided-s8`, own `.venv` already synced. Every path "E" in your brief and plan means E8 for edits, tests, lint and the commit.
- Never cd into, edit, stage, stash or commit in the main E checkout. Never switch branches. Commit on `two-sided-s8` (one commit). The orchestrator cherry-picks it onto main later.
- E8 has no `.env` and no `data/` symlinks. Do not copy or link `.env`. `docker compose config` there warns about unset variables: that is expected. If a test truly needs `.env`, write it in the report instead of creating one.
- The STEP-005 code in E8 is an older amend; STEP-006/007 are not in E8. Do not depend on them. The env var names and log lines come from feature.json and plans/STEP-007.md.
