# Brief: s8-rev — strict maintainability review of STEP-008

1. Read `~/.agents/skills/feature-json-step-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff 2fe56a67..be0cc83c` (the STEP-008 commit(s)). Context: STEP-008 in `W/tasks/two-sided-live-b/feature.json`, plan `W/tasks/two-sided-live-b/plans/STEP-008.md`, implementer report `R/reports/s8-impl.md`.
3. REPORT ONLY. Never edit, stage, or commit any file (this overrides the skill's "prefer fixing"). Read-only on E and W except your report.
4. Also flag correctness bugs you find in the diff (live money code: wrong sizes, prices, missing cancels, SELL paths, Follow300/service A behavior change).
5. Each finding: severity (blocker / major / minor / nit), `file:line`, the problem, the concrete fix. No finding without a fix. Say "no findings" if there are none.

Report `R/reports/s8-rev.md`.
6. The commit is on branch two-sided-s8 in worktree /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8 (shared git objects: the diff command works from E). Read files there (`git -C E show be0cc83c:<path>`). Ignore that STEP-006/007 are not in that tree.
