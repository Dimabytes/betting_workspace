#!/bin/zsh
# usage: prompt.sh <agent> "<text>"
# Send a follow-up. A working devin queues it ("Press Enter to send queued messages now"), so flush with Enter.
N=$1; shift
herdr agent prompt $N "$*" >/dev/null
sleep 4
herdr agent read $N --source recent-unwrapped --lines 15 | grep -q 'send queued messages' && herdr agent send-keys $N enter >/dev/null
herdr agent get $N | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["agent"]["agent_status"])'
