# Brief: s7-com — comment review of STEP-007

1. Read `~/.agents/skills/feature-json-no-comments-review/SKILL.md` and apply it. Also follow every line in `review.instructions` of `W/.feature-json.config.json`.
2. Scope: `git -C E diff f605a6b9..629657b0` (the STEP-007 commit(s)). Review only comments and suppressions that this diff adds or touches, and comments inside functions this diff refactors.
3. REPORT ONLY. Never edit, stage, or commit any file. Read-only on E and W except your report.
4. Each finding: `file:line`, the comment or suppression, DELETE or MUST KILL with the reshape. Then the skips with the keep clause. Say "no findings" if there are none.

Report `R/reports/s7-com.md`.
6. The commit is on branch two-sided-s7 in worktree /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s7 (shared git objects: the diff works from E). Read files with `git -C E show 629657b0:<path>`. Focus: without DOTA_STRATEGY service A must behave exactly as before (FR-7); each refused start for two_sided; the whitelist cannot let B trade a non-BLAST-Slam map.
