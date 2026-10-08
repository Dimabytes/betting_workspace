#!/bin/bash
# Sequential two-sided cells: 5 shards, then merge, one queue mode at a time.
set -euo pipefail
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
export PYTHONPATH=src:../prediction-market-backtesting
LOG=data/backtests/dota_maker/_logs
mkdir -p "$LOG"

run_mode() {
  local name=$1
  shift
  echo "START $name $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local i pids=() fail=0
  for i in 0 1 2 3 4; do
    uv run --group backtest python -m backtest.run \
      --validation --archives-only --strategy two-sided \
      --name "$name" "$@" --shard "$i/5" \
      > "$LOG/${name}-shard${i}.log" 2>&1 &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || fail=1
  done
  if [[ $fail -ne 0 ]]; then
    echo "FAIL shards $name"
    return 1
  fi
  uv run --group backtest python -m backtest.run \
    --validation --archives-only --strategy two-sided \
    --name "$name" "$@" --merge-shards 5 \
    > "$LOG/${name}-merge.log" 2>&1
  echo "DONE $name $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

run_mode ts-full-n30 --net-max-shares 30
run_mode ts-full-n30-nq --net-max-shares 30 --no-queue-position
run_mode ts-full-n50-g4e-4 --net-max-shares 50 --skew-per-share 0.0004
run_mode ts-full-n50-g4e-4-nq --net-max-shares 50 --skew-per-share 0.0004 --no-queue-position
echo "ALL_DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
