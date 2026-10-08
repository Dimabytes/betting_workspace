# Brief: s8-com — comment review of STEP-008

1. Read `~/.agents/skills/feature-json-no-comments-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff 2fe56a67..be0cc83c` (the STEP-008 commit(s)). Review only comments and suppressions that this diff adds or touches, and comments inside functions this diff refactors.
3. REPORT ONLY. Never edit, stage, or commit any file. Read-only on E and W except your report.
4. Each finding: `file:line`, the comment or suppression, DELETE or MUST KILL with the reshape. Then the skips with the keep clause. Say "no findings" if there are none.

Report `R/reports/s8-com.md`.
6. The commit is on branch two-sided-s8 in worktree /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8 (shared git objects: the diff command works from E). Read files there (`git -C E show be0cc83c:<path>`). Ignore that STEP-006/007 are not in that tree.
