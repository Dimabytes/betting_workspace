#!/bin/zsh
# usage: fix.sh <s#> <STEP-ID>
source ${0:A:h}/env.sh
S=$1; ID=$2
$HF/prompt.sh $S-impl "Review findings for $ID are ready. Read fully both reports: $R/reports/$S-review.md and $R/reports/$S-comments.md. Follow the 'If the parent resumes you with review findings' section of the implement skill: you decide what to fix; reviewers can be wrong, and NIT labels can be wrong. Fix, rerun the step tests, typecheck and lint, commit in esports-trader (no push). Do not set passes in feature.json. Write the fix report to $R/reports/$S-fix.md: line 1 commit hash(es), line 2 'Status: FINAL', then each finding with fixed / skipped and one-line reason. Then reply with only the report path."
