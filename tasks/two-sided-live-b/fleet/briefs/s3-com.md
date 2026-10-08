# Brief: s3-com — comment review of STEP-003

1. Read `~/.agents/skills/feature-json-no-comments-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff b86cdcdc..4a4018fb` (the STEP-003 commit(s)). Review only comments and suppressions that this diff adds or touches, and comments inside functions this diff refactors.
3. REPORT ONLY. Never edit, stage, or commit any file. Read-only on E and W except your report.
4. Each finding: `file:line`, the comment or suppression, DELETE or MUST KILL with the reshape. Then the skips with the keep clause. Say "no findings" if there are none.

Report `R/reports/s3-com.md`.
