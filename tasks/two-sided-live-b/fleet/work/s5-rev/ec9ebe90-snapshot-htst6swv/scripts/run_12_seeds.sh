#!/usr/bin/env bash
# Run 12 seeds as four sequential batches of 3 seeds x 4 shards, then report
# all 12 seeds and compare against LIVE.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GAME="$1"
NAME="$2"
shift 2

for batch in "0 1 2" "3 4 5" "6 7 8" "9 10 11"; do
  echo "=== ${GAME} ${NAME}: seeds ${batch} ==="
  SEED_LIST="$batch" SHARDS=4 scripts/run_seeds.sh "$GAME" "$NAME" "$@"
done

shopt -s nullglob
matches=("data/backtests/${GAME}_maker/"validation_*_"${NAME}")
if (( ${#matches[@]} != 1 )); then
  echo "expected one run root for ${GAME} ${NAME}, found ${#matches[@]}" >&2
  printf '%s\n' "${matches[@]}" >&2
  exit 1
fi

echo
echo "=== ${GAME} ${NAME}: all 12 seeds ==="
PYTHONPATH="src:../prediction-market-backtesting:scripts" uv run --group backtest \
  python scripts/report_seeds.py "${matches[0]}" --expected-seeds 12
echo
echo "============================ ${GAME} vs LIVE ============================"
PYTHONPATH="src:../prediction-market-backtesting:scripts" uv run --group backtest \
  python scripts/compare_backtests.py "data/backtests/${GAME}_maker/LIVE" "${matches[0]}"
