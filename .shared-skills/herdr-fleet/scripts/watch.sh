#!/bin/zsh
# usage: R=<run dir> watch.sh <agent> [report-basename] [timeout_s]   (run in background)
# Exits DONE when the report is >1.5 KB, unchanged 180 s and the agent is not working;
# exits early on BLOCKED / GONE, or TIMEOUT.
: ${R:?set R to the run dir}
N=$1; F=$R/reports/${2:-$1}.md; T=${3:-5400}
start=$(date +%s); last=-1; since=$start
while true; do
  now=$(date +%s); size=0; [ -f $F ] && size=$(stat -f %z $F)
  [ $size -ne $last ] && { last=$size; since=$now; }
  st=$(herdr agent get $N 2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["agent"]["agent_status"])
except Exception: print("gone")')
  [ "$st" = blocked ] && { echo "BLOCKED $N"; exit 0; }
  [ "$st" = gone ] && { echo "GONE $N size=$size"; exit 0; }
  [ $size -gt 1500 ] && [ $((now-since)) -ge 180 ] && [ "$st" != working ] && { echo "DONE $N size=$size"; exit 0; }
  [ $((now-start)) -ge $T ] && { echo "TIMEOUT $N size=$size status=$st"; exit 0; }
  sleep 60
done
