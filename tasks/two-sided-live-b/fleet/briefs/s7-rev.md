# Brief: s7-rev — strict maintainability review of STEP-007

1. Read `~/.agents/skills/feature-json-step-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff f605a6b9..629657b0` (the STEP-007 commit(s)). Context: STEP-007 in `W/tasks/two-sided-live-b/feature.json`, plan `W/tasks/two-sided-live-b/plans/STEP-007.md`, implementer report `R/reports/s7-impl.md`.
3. REPORT ONLY. Never edit, stage, or commit any file (this overrides the skill's "prefer fixing"). Read-only on E and W except your report.
4. Also flag correctness bugs you find in the diff (live money code: wrong sizes, prices, missing cancels, SELL paths, Follow300/service A behavior change).
5. Each finding: severity (blocker / major / minor / nit), `file:line`, the problem, the concrete fix. No finding without a fix. Say "no findings" if there are none.

Report `R/reports/s7-rev.md`.
6. The commit is on branch two-sided-s7 in worktree /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s7 (shared git objects: the diff works from E). Read files with `git -C E show 629657b0:<path>`. Focus: without DOTA_STRATEGY service A must behave exactly as before (FR-7); each refused start for two_sided; the whitelist cannot let B trade a non-BLAST-Slam map.
