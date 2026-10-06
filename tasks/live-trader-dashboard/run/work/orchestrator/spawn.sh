#!/bin/zsh
# usage: spawn.sh <name> <split-from-pane> <direction> <agent>   (brief at $R/briefs/<name>.md)
source ${0:A:h}/env.sh; cd $WS
N=$1; FROM=$2; DIR=$3; AG=$4
P=$(herdr pane split $FROM --direction $DIR --ratio 0.5 --cwd $R --no-focus | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])') || exit 1
echo "pane $P"
$HF/launch.sh $N $P $AG || exit 1
sleep 25
$HF/prompt.sh $N "You are agent '$N'. Read fully: $R/00-context.md, then $R/briefs/$N.md. Do the brief. Write the full report to $R/reports/$N.md (file, not chat). Scratch files go to $R/work/$N/. When the report is complete, make its line 2 exactly: Status: FINAL. Then reply with only the report path."
