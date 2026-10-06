#!/bin/zsh
# usage: plan.sh <s#> <STEP-ID>   -> new tab ltd-<s#>, sol planner; prints root pane
source ${0:A:h}/env.sh; cd $WS
S=$1; ID=$2; N=$S-plan
cat > $R/briefs/$N.md <<X
# Brief: plan $ID

Role: planner. Read and follow /Users/dimabytes/.claude/skills/feature-json-create-step-plan/SKILL.md.
- Slug: live-trader-dashboard. Step: $ID only. Earlier steps are done; their notes are in progress.txt.
- Write the plan to $WS/tasks/live-trader-dashboard/plans/$ID.md
- Read the code in $ET. Do not edit code. Do not commit.
- Your report file is $R/reports/$N.md: line 1 the plan path, line 2 \`Status: FINAL\`, then 3-5 lines summary.
X
P=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label ltd-$S --cwd "$R" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
echo "root $P"
$HF/launch.sh $N $P sol || exit 1
sleep 15
$HF/prompt.sh $N "You are agent '$N'. Read fully: $R/00-context.md, then $R/briefs/$N.md. Do the brief. Write the full report to $R/reports/$N.md (file, not chat). Scratch files go to $R/work/$N/. When the report is complete, make its line 2 exactly: Status: FINAL. Then reply with only the report path."
