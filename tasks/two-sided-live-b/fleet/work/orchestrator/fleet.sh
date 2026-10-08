# source me. Helpers for the orchestrator.
W=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace
T=$W/tasks/two-sided-live-b
R=$T/fleet
S=/Users/dimabytes/.claude/skills/herdr-fleet/scripts
closeagent() { local P; P=$(herdr agent get $1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["agent"]["pane_id"])') && herdr pane close $P >/dev/null && echo "closed $1"; R=$R $S/grid.py ts-live-b; }
setpass() { python3 - "$T/feature.json" "$1" <<'PY'
import json,sys
p,sid=sys.argv[1],sys.argv[2]
s=open(p).read()
i=s.index('"id": "%s"'%sid); end=s.find('"id": "STEP-',i+1); end=len(s) if end<0 else end
j=s.find('"passes": false',i,end)
if j<0: sys.exit("passes false not found for "+sid)
s=s[:j]+'"passes": true'+s[j+15:]
assert next(x for x in json.loads(s)["steps"] if x["id"]==sid)["passes"] is True
open(p,'w').write(s); print("set",sid)
PY
}
launch() { local PANE; PANE=$(R=$R $S/grid.py ts-live-b --new); R=$R $S/launch.sh $1 $PANE $2 && sleep 2 && $S/prompt.sh $1 "You are agent '$1'. Read fully: $R/00-context.md, then $R/briefs/$1.md. Do the brief. Write the full report to $R/reports/$1.md (file, not chat; update it as you go). Scratch files go to $R/work/$1/. When the report is complete, make its line 2 exactly: Status: FINAL. Then reply with only the report path."; }
mkimpl() { sed "s/STEP-002/STEP-00$1/g; s/s2-impl/s$1-impl/g" $R/briefs/s2-impl.md > $R/briefs/s$1-impl.md; }
send() { $S/prompt.sh "$@"; sleep 4; herdr agent read $1 --source visible --lines 8 | grep -q 'Pasted text' && herdr agent send-keys $1 enter >/dev/null && echo "flushed paste for $1"; herdr agent get $1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["agent"]["agent_status"])'; }
