#!/bin/zsh
# Launch the 14 audit agents into the panes from panes.env.
R=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26
SK=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.claude/skills/herdr-fleet/scripts
P=/Users/dimabytes/work/polymarket/dota_2_bot
source $R/work/orchestrator/panes.env
export R
DEV=(--model swe-2-max --permission-mode bypass)
GROK=(--model grok-4.7-high --force --trust --sandbox disabled --add-dir $P/esports-trader --add-dir $P/betting_workspace --add-dir $P/prediction-market-backtesting --add-dir $P/polymarket-collector --add-dir $P/poly-maker)
LUNA=(-m gpt-6-luna -c 'model_reasoning_effort="max"' -c 'service_tier="priority"' --approve-for-me)
L=$R/work/orchestrator/launch.log
: > $L
{
$SK/launch.sh collect-devin $A1 devin -- $DEV &
$SK/launch.sh signal-devin  $A2 devin -- $DEV &
$SK/launch.sh fill-devin    $A3 devin -- $DEV &
$SK/launch.sh kernel-devin  $A4 devin -- $DEV &
$SK/launch.sh live-devin    $B1 devin -- $DEV &
$SK/launch.sh feed-devin    $B2 devin -- $DEV &
$SK/launch.sh history-devin $B3 devin -- $DEV &
$SK/launch.sh sun-devin     $B4 devin -- $DEV &
$SK/launch.sh fill-grok     $C1 cursor -- $GROK &
$SK/launch.sh time-grok     $C2 cursor -- $GROK &
$SK/launch.sh link-grok     $C3 cursor -- $GROK &
$SK/launch.sh parity-luna   $D1 codex -- $LUNA &
$SK/launch.sh data-luna     $D2 codex -- $LUNA &
$SK/launch.sh train-luna    $D3 codex -- $LUNA &
wait
} >> $L 2>&1
cat $L
