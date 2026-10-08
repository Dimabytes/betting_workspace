# Brief: s6-com — comment review of STEP-006

1. Read `~/.agents/skills/feature-json-no-comments-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff a58d19c9..77a85364` (the STEP-006 commit(s)). Review only comments and suppressions that this diff adds or touches, and comments inside functions this diff refactors.
3. REPORT ONLY. Never edit, stage, or commit any file. Read-only on E and W except your report.
4. Each finding: `file:line`, the comment or suppression, DELETE or MUST KILL with the reshape. Then the skips with the keep clause. Say "no findings" if there are none.

Report `R/reports/s6-com.md`.
