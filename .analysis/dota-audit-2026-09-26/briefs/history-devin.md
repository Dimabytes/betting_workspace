# Brief: history-devin — bug archaeology, sibling hunt, leftovers

Report: `$R/reports/history-devin.md`. Work dir: `$R/work/history-devin/`.

## Sources

- `cd $E && git log` (1194 commits, ~275 fix-like): `git log --grep`, `git show <fix>`, `git log -S`.
- `$E/docs/experiments/*.md`, `$E/docs/analysis/*.md`, `$E/docs/next_steps.md`, `$E/docs/paper_review.md`,
  `$E/docs/as-is.md` (stale since 2026-09-04).
- `$W/.learnings/*.md`; `$W/.analysis/*/REPORT.md` and `report.md`; `$W/docs/analysis/2026-09-06-follow300-review.md`;
  `$W/docs/completed-tasks/*/progress.txt`; `$W/docs/archive-linking-and-feed-replay/progress.txt`;
  `$W/codex-session-*.md` (large: grep, do not read whole).

## Tasks

1. **Taxonomy of past bugs.** Classes such as: time/lag joins, side orientation, look-ahead and selection
   filters, train/serve skew, stale caches, metadata that lies, accounting / double counting, order state
   machine / wedges, rounding / ticks, feed parsing / gaps, discovery / binding, config drift, silent drops,
   reverts that left residue. For each class: count, 2–4 example commits (hash, date, one line), and the root
   cause pattern.
2. **Sibling hunt (most important).** For each class, search today's HEAD for the same pattern elsewhere,
   especially in Dota paths and shared code. Typical siblings: a fix applied to LoL only while the same code
   exists for Dota; a fix in live but not in the backtest, or the reverse; a duplicated constant fixed in one
   copy.
3. **Leftovers.** Early-phase things still in the path: constants (since when is `VALIDATION_START_TIME`
   fixed, and is it still a sound split?), old datasets or caches still read, dead code still executed,
   experiment knobs still active, residue of reverted experiments, docs that claim behaviour the code does not
   have, scripts whose outputs are consumed but no longer maintained.
4. **Hotspots.** Files with the most fix commits (`git log --format= --name-only` over fix commits |
   `sort | uniq -c`): the top 15, and what that says about where to look harder.

Each sibling you report is a finding (with severity, `file:line`, and evidence it is still present at HEAD).
