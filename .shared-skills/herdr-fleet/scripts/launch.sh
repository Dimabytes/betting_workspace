#!/bin/zsh
# usage: R=<run dir> launch.sh <name> <pane> <kind> -- <agent args...>
# cd's the pane to $R, starts the agent, sends the standard file-report prompt.
: ${R:?set R to the run dir}
N=$1; PANE=$2; KIND=$3; shift 3; [ "$1" = "--" ] && shift
herdr pane run $PANE "cd $R" >/dev/null 2>&1; sleep 1
herdr agent start $N --kind $KIND --pane $PANE --timeout 90000 -- "$@" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result"); print("start", r["agent"]["name"], r["argv"]) if r else print("ERR", d)'
P="You are agent '$N'. Read fully: $R/00-context.md, then $R/briefs/$N.md. Do the brief. Write the full report to $R/reports/$N.md (file, not chat; update it as you go). Scratch files go to $R/work/$N/. Use agent-browser only with --session $N; never run agent-browser close --all. When the report is complete, make its line 2 exactly: Status: FINAL. Then reply with only the report path."
herdr agent prompt $N "$P" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("prompt", d.get("result",{}).get("agent",{}).get("agent_status"), d.get("error"))'
