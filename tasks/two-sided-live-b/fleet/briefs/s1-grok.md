# Brief: s1-grok — STEP-001 alone (plan + implement, no separate review)

You are the only agent for STEP-001. No planner and no reviewer run for this step.

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-001`.
2. There is no plan file. Make your own short plan from the step's `changes` list and the related sources. Write it at the top of your report.
3. Do every item in `changes`, in order. The regression backtest before the code change is required: run it on the current HEAD first.
4. Run the step's tests, `make lint` on staged files, and the typecheck.
5. Set `passes: true` for STEP-001 in `W/tasks/two-sided-live-b/feature.json`. Append to `W/tasks/two-sided-live-b/progress.txt`.
6. Commit in E on main (one commit). Do not commit in W.

Report `R/reports/s1-grok.md`: plan, what changed (files), commands run with results (tests count, lint, typecheck), regression before/after paths and the comparison result, commit hash, open issues.
