# Brief: s4-rev — strict maintainability review of STEP-004

1. Read `~/.agents/skills/feature-json-step-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff c779fa21..624fb55f` (the STEP-004 commit(s)). Context: STEP-004 in `W/tasks/two-sided-live-b/feature.json`, plan `W/tasks/two-sided-live-b/plans/STEP-004.md`, implementer report `R/reports/s4-impl.md`.
3. REPORT ONLY. Never edit, stage, or commit any file (this overrides the skill's "prefer fixing"). Read-only on E and W except your report.
4. Also flag correctness bugs you find in the diff (live money code: wrong sizes, prices, missing cancels, SELL paths, Follow300/service A behavior change).
5. Each finding: severity (blocker / major / minor / nit), `file:line`, the problem, the concrete fix. No finding without a fix. Say "no findings" if there are none.

Report `R/reports/s4-rev.md`.
