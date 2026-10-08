#!/usr/bin/env bash
# Launch every seed of one named validation run at once; each seed runs
# SHARDS shards then merges them. Defaults: 3 seeds x 4 shards = 12 cores.
# SEEDS=12 SHARDS=1 runs twelve seeds with one unsharded process each.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "Usage: [SEEDS=3] [SHARDS=4] scripts/run_seeds.sh <dota|lol> <name> [extra flags]" >&2
  exit 1
}

if [[ $# -lt 2 ]]; then
  usage
fi

GAME="$1"
NAME="$2"
shift 2

if [[ "$GAME" != "dota" && "$GAME" != "lol" ]]; then
  usage
fi

EXTRA=("$@")
BT_PYTHONPATH="src:../prediction-market-backtesting"
SEEDS="${SEEDS:-3}"
SHARDS="${SHARDS:-4}"
# ponytail: experiment escape hatch. SEED_LIST="1 2" runs those seeds instead of
# 0..SEEDS-1, for seeds a known framework crash makes unusable. Delete with the
# ladder experiment flags.
SEED_LIST="${SEED_LIST:-}"
if [[ -n "$SEED_LIST" ]]; then
  read -r -a SEED_NUMBERS <<<"$SEED_LIST"
else
  SEED_NUMBERS=()
  for ((seed = 0; seed < SEEDS; seed++)); do
    SEED_NUMBERS+=("$seed")
  done
fi

run_bt() {
  PYTHONPATH="$BT_PYTHONPATH" uv run --group backtest python -m backtest.run "$@"
}

# Reporting scripts import backtest.context, which imports the framework, so they
# need the same interpreter and path as the run itself.
run_report() {
  PYTHONPATH="$BT_PYTHONPATH:scripts" uv run --group backtest python "$@"
}

# Wait for every listed pid, then return the first nonzero child status. A bare
# `wait` returns 0 for a group where one child failed, which hid dead seeds.
wait_all() {
  local pid status=0 child_status
  for pid in "$@"; do
    child_status=0
    wait "$pid" || child_status=$?
    if (( child_status != 0 && status == 0 )); then
      status=$child_status
    fi
  done
  return "$status"
}

run_one_seed() {
  local seed="$1"
  local log_dir="data/backtests/${GAME}_maker/_logs/${NAME}/seed${seed}"
  mkdir -p "$log_dir"
  if (( SHARDS == 1 )); then
    run_bt --game "$GAME" --validation --name "$NAME" --signal-cadence-seed "$seed" \
      "${EXTRA[@]}" >"${log_dir}/run.log" 2>&1
    return
  fi
  local i
  local shard_pids=()
  for ((i = 0; i < SHARDS; i++)); do
    run_bt --game "$GAME" --validation --name "$NAME" \
      --shard "${i}/${SHARDS}" --signal-cadence-seed "$seed" \
      "${EXTRA[@]}" \
      >"${log_dir}/shard_${i}.log" 2>&1 &
    shard_pids+=("$!")
  done
  wait_all "${shard_pids[@]}" || return $?
  run_bt --game "$GAME" --validation --name "$NAME" \
    --merge-shards "$SHARDS" --signal-cadence-seed "$seed" \
    "${EXTRA[@]}"
}

# ponytail: DONE is found by glob on NAME. Two run dirs with the same NAME and
# different policy can skip this build; the seed then sees a manifest mismatch
# and replays archive maps itself. Key the glob on the run dir if that happens.
run_archive() {
  local log_dir="data/backtests/${GAME}_maker/_logs/${NAME}/archive"
  mkdir -p "$log_dir"
  if compgen -G "data/backtests/${GAME}_maker/validation_*_${NAME}/_archive/DONE" >/dev/null; then
    return 0
  fi
  local archive_shards="${ARCHIVE_SHARDS:-$((SEEDS * SHARDS))}"
  local archive_args=(
    --game "$GAME" --validation --name "$NAME" --archives-only "${EXTRA[@]}"
  )
  if (( archive_shards == 1 )); then
    run_bt "${archive_args[@]}" >"${log_dir}/run.log" 2>&1
    return
  fi
  local i
  local shard_pids=()
  for ((i = 0; i < archive_shards; i++)); do
    run_bt "${archive_args[@]}" --shard "${i}/${archive_shards}" \
      >"${log_dir}/shard_${i}.log" 2>&1 &
    shard_pids+=("$!")
  done
  wait_all "${shard_pids[@]}" || return $?
  run_bt "${archive_args[@]}" --merge-shards "$archive_shards" >"${log_dir}/merge.log" 2>&1
}

if [[ "${RUN_SEEDS_STAGE:-}" == "archive" ]]; then
  run_archive
  exit
fi

if [[ "${RUN_SEEDS_STAGE:-}" == "seed" ]]; then
  run_one_seed "${RUN_SEEDS_SEED:?}"
  exit
fi

log_root="data/backtests/${GAME}_maker/_logs/${NAME}"
mkdir -p "$log_root"
RUN_SEEDS_STAGE=archive python3 scripts/run_locked.py --wait "${log_root}/archive.lock" -- \
  "$0" "$GAME" "$NAME" "${EXTRA[@]}"

seed_pids=()
for seed in "${SEED_NUMBERS[@]}"; do
  RUN_SEEDS_STAGE=seed RUN_SEEDS_SEED="$seed" \
    python3 scripts/run_locked.py --fail "${log_root}/seed${seed}.lock" -- \
    "$0" "$GAME" "$NAME" "${EXTRA[@]}" &
  seed_pids+=("$!")
done
seed_status=0
wait_all "${seed_pids[@]}" || seed_status=$?
if (( seed_status != 0 )); then
  echo "at least one seed failed (exit ${seed_status}); artifacts left for --resume" >&2
  exit "$seed_status"
fi

shopt -s nullglob
candidates=("data/backtests/${GAME}_maker/"validation_*_"${NAME}")
shopt -u nullglob
matches=()
for dir in "${candidates[@]}"; do
  complete=1
  for seed in "${SEED_NUMBERS[@]}"; do
    [[ -d "${dir}/seed${seed}" ]] || complete=0
  done
  if (( complete )); then
    matches+=("$dir")
  fi
done
if (( ${#matches[@]} != 1 )); then
  echo "expected one ${#SEED_NUMBERS[@]}-seed run root for ${GAME} ${NAME}, found ${#matches[@]}" >&2
  printf '%s\n' "${matches[@]}" >&2
  exit 1
fi

# --expected-seeds asserts seed0..seedN-1, so an explicit SEED_LIST reports the
# seeds it finds instead.
EXPECTED=(--expected-seeds "$SEEDS")
if [[ -n "$SEED_LIST" ]]; then
  EXPECTED=()
fi
run_report scripts/report_seeds.py "${matches[0]}" "${EXPECTED[@]}"

# seeds.json totals are not comparable across runs whose validation universe
# differs; the paired comparison against LIVE is the number to read.
LIVE="data/backtests/${GAME}_maker/LIVE"
if [[ -d "$LIVE" && "$(realpath "$LIVE")" != "$(realpath "${matches[0]}")" ]]; then
  echo
  echo "================================== vs LIVE =================================="
  run_report scripts/compare_backtests.py "$LIVE" "${matches[0]}"
fi
