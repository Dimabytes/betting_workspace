# Brief: s6-rev — strict maintainability review of STEP-006

1. Read `~/.agents/skills/feature-json-step-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff a58d19c9..77a85364` (the STEP-006 commit(s)). Context: STEP-006 in `W/tasks/two-sided-live-b/feature.json`, plan `W/tasks/two-sided-live-b/plans/STEP-006.md`, implementer report `R/reports/s6-impl.md`.
3. REPORT ONLY. Never edit, stage, or commit any file (this overrides the skill's "prefer fixing"). Read-only on E and W except your report.
4. Also flag correctness bugs you find in the diff (live money code: wrong sizes, prices, missing cancels, SELL paths, Follow300/service A behavior change).
5. Each finding: severity (blocker / major / minor / nit), `file:line`, the problem, the concrete fix. No finding without a fix. Say "no findings" if there are none.

Report `R/reports/s6-rev.md`.
6. Focus: Follow300 (service A) behavior must not change through the MatchWorker extraction; the final-merge ordering (merge_all after the fence, before end_snapshot/zero_token_sizes/unregister_worker); restart with outbox cursor at head; no SELL path for B.
