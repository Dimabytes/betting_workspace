#!/bin/zsh
# usage: review-briefs.sh <s#> <STEP-ID> <base> <head> [extra scope text]
source ${0:A:h}/env.sh
S=$1; ID=$2; B=$3; H=$4
common="- Slug: live-trader-dashboard. Step: $ID. Plan: $WS/tasks/live-trader-dashboard/plans/$ID.md
- Scope: the diff in $ET: \`git diff $B..$H\`$5
- Review only. Never edit, never commit, never fix anything.
- Report file starts: line 1 \`$ID review\`, line 2 \`Status: FINAL\`. If you have no findings at all, line 3 is exactly \`NO FINDINGS\`."
cat > $R/briefs/$S-review.md <<X
# Brief: maintainability review $ID

Role: reviewer. Read and follow /Users/dimabytes/.claude/skills/feature-json-step-review/SKILL.md (also review.instructions in the config).
$common
- Each finding: file:line, severity, what to change.
X
cat > $R/briefs/$S-comments.md <<X
# Brief: comments review $ID

Role: comments reviewer. Read and follow /Users/dimabytes/.claude/skills/feature-json-no-comments-review/SKILL.md.
$common
- Judge only comments and suppressions that the diff adds or touches. Old comments in functions the diff refactors count too.
X
