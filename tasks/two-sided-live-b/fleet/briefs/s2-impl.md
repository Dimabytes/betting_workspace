# Brief: s2-impl — implement STEP-002

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-002`. Plan: `W/tasks/two-sided-live-b/plans/STEP-002.md`.
2. Implement the plan. If the plan is wrong against the real code, follow the code and the feature.json step, and write the deviation in the report.
3. Run the step's tests, `make lint` on staged files, and the typecheck. Do not commit broken code.
4. Do NOT set `passes` in feature.json: the orchestrator sets it after review. Append to `W/tasks/two-sided-live-b/progress.txt`.
5. One commit in E on main for the step. Do not commit in W.
6. Later the orchestrator sends you review findings in this same session. Then fix what is right, re-run checks, amend your own step commit (or add one commit), update the report, and set `Status: FINAL` again.

Report `R/reports/s2-impl.md`: what changed (files), deviations from the plan, commands with results (test counts, lint, typecheck), commit hash, open issues.
