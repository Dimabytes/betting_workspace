# STEP-007 plan — run the queue and confirm winners on seeds 1–2

Worktree `../esports-trader-exp`, branch `exp-part2`, head `9f4bedf24f96d32429699868700f4e60dda3ab21`. This step changes no repo code and makes no commit.

Paths used in every command:

```bash
WT=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-exp
INV=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-13-killgate-short-roles
TASK=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/killgate-short-roles
```

Run every command from `$WT` (`cd "$WT"` first). `queue.sh` refuses to run from anywhere else.

## Goal

1. `$INV/work/queue.sh` runs all 13 items detached from the agent session.
2. Every arm with a positive seed-0 net delta against `e1-base` gets seeds 1 and 2, one seed at a time.
3. A failed queue item gets exactly one rerun.
4. `progress.txt` gets one STEP-007 entry with the comparison summary that `feature.json` asks for.

## Execution model

The implementer works in 3 or 4 short turns. Between turns the machine runs a detached job, and the orchestrator watches files.

| Phase | Implementer does | Detached job | Orchestrator watches | Job length |
| --- | --- | --- | --- | --- |
| 1 | preflight, write 3 helper scripts, launch the queue | `queue.sh` | `$INV/work/queue/queue.log` ends with `queue-exit <code> <utc>` | about 8.5 h |
| 2 | read the queue result, pick winners, launch the driver | `confirm.sh` | `$INV/work/queue/confirm.log` gets its 1st `confirm-exit` line | 0.2–3.5 h |
| 3 | only if needed: late winners or one confirmation retry | `confirm.sh` | 2nd `confirm-exit` line | 0.2–1 h |
| 4 | summary into `progress.txt`, final report | none | the final report | none |

Each phase ends with **END TURN**. At that marker the implementer writes its phase report and stops. It does not wait, poll, or loop.

## Facts I verified

- **Current state.** No `backtest.run`, `run_seeds`, training, or queue process runs. `$INV/work/queue/` holds only `dry-run.txt`. There is no `status.tsv`, `inputs.sha256`, or `$INV/work/compare/`. `$WT/data/backtests/dota_maker/` holds `_logs` (empty), `LIVE`, and `LIVE_NOXP`. `git status --short` shows only the 10 untracked `data/new_model/experiments/*` dirs. `../prediction-market-backtesting` is on `local-day-native`. `bash` on `PATH` is `/opt/homebrew/bin/bash` 5.3. The Mac is on AC power. Disk has 133 GiB free.
- **`queue.sh` items.** 13 status items: `e1-base`, `e1-h20`, `e1-h10`, `e1-h20top2-stratz`, `e3-deaths`, `e3-roles`, `e2a-buy-h30`, `e2a-buy-h60`, `e2a-buysell-h30`, `e2a-buysell-h60`, `e2b-h30`, `e2b-h60`, `e1-base-seeds12`. `status.tsv` is append-only. Read the latest row per item.
- **Compare files.** `queue.sh` makes 17 `compare` calls (51 commands) over 16 labels. That is 48 distinct files `$INV/work/compare/<label>[__grid_v1|__schedule_grid].txt`. A failed compare is `<label>[…].err`.
- **The queue writes no `--signal-mode schedule` slice.** `feature.json` asks for `e1-base` vs LIVE in that slice. Phase 2 adds it.
- **Rerun behavior** (`queue.sh` `run_arm`, `queue_check.py arm_rows`):
  - `missing` seed: a rerun launches it again.
  - `invalid` (unexpected `terminated_early`, short catalog) or `mismatch` (manifest differs from the flags): a rerun does **not** launch it. The status row stays `fail`. Only the log explains why.
  - `work/queue/inputs.sha256` covers `queue.sh`, `queue_check.py`, the top-2 table, `gates.json`, the anchor, and 8 `model.json` files. Any edit to them makes the next start exit with `refusing to resume`.
- **`QUEUE_LIB_ONLY=1`** makes `queue.sh` define its variables and functions and `return` before the queue body. A sourced `queue.sh` gives `run_arm`, `compare`, `require_lock`, `TEST`, `TOP2`, `MODELS`, `SHORT30`, `SHORT60`, `E2B30`, `E2B60`. I tested a draft `confirm.sh` with `DRY=1` from the worktree: it printed the right seed-1 and seed-2 commands, and the flags equal the seed-0 lines in `dry-run.txt`. DRY wrote no file.
- **`run_arm <arm> "0 1 2"` is a free flag check.** `arm-state` compares the existing seed-0 manifest with the flags passed. Wrong flags give `mismatch` and nothing launches. Seeds 1 and 2 launch one after the other, as `SEED_LIST=1` and then `SEED_LIST=2`. The status item is `<arm>-seeds012`.
- **Schedule maps are seed-independent.** `--archives-only` is pinned to seed 0, and every seed grafts `_archive` (`load_seed_archive`, `src/backtest/run.py:2191-2207`). In LIVE, the `--signal-mode schedule` slice has the same shared PnL `+18,321.07` on seeds 0, 1, and 2. Seeds 1–2 add information only on grid-v1 maps.
- **E2 cohorts.** In LIVE seed 0, maps with `match_id >= 8970886632` are 79 grid-v1, 261 schedule/grid, and 97 schedule/oddin. All 358 schedule maps are test maps.
- **Compare output format** (`scripts/compare_backtests.py`):
  - `candidate: <dir>  seeds 0,1,2  …` and `shared matches (all N runs): K`.
  - One `-- seedN --` block per paired seed. Its metric table has a row `pnl with rebate  <base> <cand> <delta>` and a row `worst map (pre)  <base> <cand> <delta>`.
  - `SHARED-SET METRICS` repeats both rows. The last token is the delta of means.
  - The line after `== pooled: mean_k delta[m,k] ==` is `paired t: t=… p=X | wilcoxon: W=… p=Y (…)` or `identical`.
  - A cohort slice with 0 shared maps exits 0 and prints `identical`. Read `shared` before trusting `identical`.
- **Compare time.** One compare on 88 maps × 3 seeds took 3.9 s. plan6 measured 9.6 s on 868 maps × 3 seeds.
- **Detached launch.** I tested `detach.sh` below with a dummy script. macOS `caffeinate -i <utility>` forks: the parent becomes the utility, and the child holds the `PreventUserIdleSystemSleep` assertion. The wrapper `bash` has PPID 1 and its own session. The log ends with `<name>-exit <code> <utc>`. A second launch while the first is alive exits 1.
- **Run times** (plan6, rebuild-1012 logs): all-map arm 31 min archive + 12 min seed. E2 arm 31 min archive + 3–4 min seed. One extra seed: 12 min (all maps), 3–4 min (test maps).

## Decisions

1. **Launch through `detach.sh`.** It runs `nohup caffeinate -i` inside a new session (`perl POSIX::setsid`). A CLI that kills its process group at the end of a turn then cannot kill the queue. It writes `<name>.pid` and a final `<name>-exit <code> <utc>` line. Those two files are what the orchestrator watches.
2. **One confirmation driver, `confirm.sh`.** It sources `queue.sh` with `QUEUE_LIB_ONLY=1` and reuses `run_arm` and `compare`. With `RERUN=1` it first runs `queue.sh` once: that is the one rerun of failed items. Then it confirms the arms named as arguments, one at a time, in queue order. Then it refreshes all 15 arm compares. `is_fresh` skips files that did not change.
3. **Winner rule** (from `feature.json`): the seed-0 `pnl with rebate` delta in `<arm>__vs__e1-base.txt` is > 0. `+0.00` is not a winner. `e1-base` is never a candidate. A pair partner is not added unless it is a winner itself.
4. **`summarize.py` reads the compare files.** It writes one TSV row per label with the whole set and three slices. Phase 2 uses `--winners`. Phase 4 builds the `progress.txt` summary from the TSV.
5. **One rerun per item.** `RERUN=1` is passed only in Phase 2. A failed confirmation item (`still missing seeds`) gets one retry in Phase 3. An `invalid` or `mismatch` item is not retried, because the rerun cannot change it. Its reason goes into `progress.txt`.
6. **No repo change.** No commit. Do not edit `queue.sh`, `queue_check.py`, `gates.json`, the top-2 table, or any `model.json`. The lock refuses a resume after such an edit. The new scripts have no comments (`.feature-json.config.json`).
7. **Delete nothing.** Run dirs, logs, `.started`, `.pid`, and compare files stay. They are the result.

## Helper scripts

Write these three files in Phase 1. All live in the gitignored `$INV/work/`.

### `$INV/work/detach.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
name="$1"
shift
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/queue"
if [[ -f "$dir/$name.pid" ]] && ps -p "$(<"$dir/$name.pid")" -o command= | grep -q "queue/$name "; then
  echo "$name is already running" >&2
  exit 1
fi
nohup perl -MPOSIX -e 'fork and exit; POSIX::setsid() or die "setsid: $!\n"; exec @ARGV or die "exec: $!\n"' \
  caffeinate -i "$BASH" -c 'echo $$ >"$0.pid"; "$BASH" "$@"; echo "${0##*/}-exit $? $(date -u +%FT%TZ)"' \
  "$dir/$name" "$@" >>"$dir/$name.log" 2>&1 </dev/null &
```

Usage: `bash "$INV/work/detach.sh" <name> <script> [args…]` from `$WT`. Environment variables pass through. It writes `$INV/work/queue/<name>.pid` and appends to `$INV/work/queue/<name>.log`.

### `$INV/work/confirm.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARMS=(e1-h20 e1-h10 e1-h20top2-stratz e3-deaths e3-roles e2a-buy-h30 e2a-buy-h60
  e2a-buysell-h30 e2a-buysell-h60 e2b-h30 e2b-h60)
for arm in "$@"; do
  [[ " ${ARMS[*]} " == *" $arm "* ]] || { echo "unknown arm $arm" >&2; exit 1; }
done
if [[ "${RERUN:-}" == 1 ]]; then
  bash "$HERE/queue.sh" || echo "queue rerun exit $?"
fi
QUEUE_LIB_ONLY=1
source "$HERE/queue.sh"
[[ -n "$DRY" ]] || require_lock
for arm in "${ARMS[@]}"; do
  [[ " $* " == *" $arm "* ]] || continue
  case "$arm" in
    e1-h20) run_arm "$arm" "0 1 2" "" --kill-post-table-hold-s 20 ;;
    e1-h10) run_arm "$arm" "0 1 2" "" --kill-post-table-hold-s 10 ;;
    e1-h20top2-stratz) run_arm "$arm" "0 1 2" "" --kill-post-table-hold-s 20 --kill-hold-top2 "$TOP2" ;;
    e3-deaths) run_arm "$arm" "0 1 2" "" --model-dir "$MODELS/e3-deaths-xp" --model-dir-noxp "$MODELS/e3-deaths-noxp" ;;
    e3-roles) run_arm "$arm" "0 1 2" "" --model-dir "$MODELS/e3-roles-xp" --model-dir-noxp "$MODELS/e3-roles-noxp" ;;
    e2a-buy-h30) run_arm "$arm" "0 1 2" "$TEST" "${SHORT30[@]}" --short-veto-delta 0.005 ;;
    e2a-buy-h60) run_arm "$arm" "0 1 2" "$TEST" "${SHORT60[@]}" --short-veto-delta 0.005 ;;
    e2a-buysell-h30) run_arm "$arm" "0 1 2" "$TEST" "${SHORT30[@]}" --short-veto-delta 0.005 --short-sell-hold-delta 0.005 ;;
    e2a-buysell-h60) run_arm "$arm" "0 1 2" "$TEST" "${SHORT60[@]}" --short-veto-delta 0.005 --short-sell-hold-delta 0.005 ;;
    e2b-h30) run_arm "$arm" "0 1 2" "$TEST" "${E2B30[@]}" ;;
    e2b-h60) run_arm "$arm" "0 1 2" "$TEST" "${E2B60[@]}" ;;
  esac
done
compare e1-h20 e1-base
compare e1-h10 e1-base
compare e1-h20top2-stratz e1-base
compare e1-h20top2-stratz e1-h20
compare e3-deaths e1-base
compare e3-roles e1-base
compare e3-roles e3-deaths
compare e2a-buy-h30 e1-base
compare e2a-buy-h60 e1-base
compare e2a-buysell-h30 e1-base
compare e2a-buysell-h30 e2a-buy-h30
compare e2a-buysell-h60 e1-base
compare e2a-buysell-h60 e2a-buy-h60
compare e2b-h30 e1-base
compare e2b-h60 e1-base
```

Notes:

- The flag lines copy `queue.sh:336-371` with seeds `"0 1 2"`. Seed 0 is already `ready`, so only seeds 1 and 2 launch. A copy error makes seed 0 `mismatch`, and nothing launches.
- The arm loop walks `ARMS` in queue order, whatever the argument order.
- `DRY=1` prints a seed-0 line too. The real run skips seed 0.

### `$INV/work/summarize.py`

```python
import re
import sys
from pathlib import Path

SLICES = ("", "__grid_v1", "__schedule_grid", "__schedule")
COLUMNS = ("seeds", "shared", "net", "net_mean", "pooled_p", "worst")


def last_tokens(lines: list[str], label: str) -> list[str]:
    return [line.split()[-1].replace(",", "") for line in lines if line.startswith(label)]


def parse(text: str) -> list[str]:
    seeds = re.search(r"^candidate:\s+\S+\s+seeds (\S+)", text, re.MULTILINE)
    shared = re.search(r"^shared matches \(all \d+ runs\): (\d+)", text, re.MULTILINE)
    pooled = re.search(r"^== pooled.*\n(.*)", text, re.MULTILINE)
    pooled_line = pooled.group(1).strip() if pooled else ""
    lines = text.splitlines()
    net = last_tokens(lines, "pnl with rebate")
    worst = last_tokens(lines, "worst map (pre)")
    return [
        seeds.group(1) if seeds else "",
        shared.group(1) if shared else "",
        "/".join(net[:-1]),
        net[-1] if net else "",
        "identical"
        if pooled_line == "identical"
        else "/".join(re.findall(r"p=([\d.]+)", pooled_line)),
        "/".join(worst[:-1]),
    ]


def cells(path: Path) -> list[str]:
    if path.is_file():
        return parse(path.read_text())
    return ["ERR" if path.with_suffix(".err").is_file() else "-"] * len(COLUMNS)


def labels(compare: Path) -> list[str]:
    names: set[str] = set()
    for path in [*compare.glob("*.txt"), *compare.glob("*.err")]:
        name = path.stem
        for suffix in reversed(SLICES):
            name = name.removesuffix(suffix)
        names.add(name)
    return sorted(names)


def main(argv: list[str]) -> None:
    compare = Path(argv[-1])
    if argv[:1] == ["--winners"]:
        for label in labels(compare):
            arm, _, base = label.partition("__vs__")
            seed0 = cells(compare / f"{label}.txt")[2].split("/")[0]
            if base == "e1-base" and re.fullmatch(r"[+-]?\d+\.\d+", seed0) and float(seed0) > 0:
                print(arm)
        return
    print("\t".join(["label", *(f"{c}{s}" for s in SLICES for c in COLUMNS)]))
    for label in labels(compare):
        row = [label]
        for suffix in SLICES:
            row += cells(compare / f"{label}{suffix}.txt")
        print("\t".join(row))


if __name__ == "__main__":
    main(sys.argv[1:])
```

Column meaning, per slice (`""` = whole set):

- `seeds`: paired seeds, e.g. `0,1,2`.
- `shared`: shared maps over all paired runs.
- `net`: `pnl with rebate` delta per seed, e.g. `+12.30/-4.10/+7.00`. This is the "Δ net PnL".
- `net_mean`: the delta of means over seeds.
- `pooled_p`: `<paired t p>/<wilcoxon p>` on the per-map mean over seeds, or `identical`.
- `worst`: `worst map (pre)` delta per seed. A value ≥ 0 means the candidate's worst map is not worse than the base.
- `ERR`: only `<label>.err` exists. `-`: neither file exists.

I tested this code on real compare output (LIVE vs `ts-e0-p0`, 3 seeds, and a 0-map slice). `ruff check` and `ruff format --check` with the worktree `pyproject.toml` pass.

## Phase 1 — preflight and launch the queue

1. Preflight. Stop if any line is wrong. Report the problem and end the turn without a launch.

   ```bash
   cd "$WT"
   git branch --show-current                                   # exp-part2
   git rev-parse HEAD                                          # 9f4bedf24f96d32429699868700f4e60dda3ab21
   git log --oneline cb04556b..HEAD | wc -l                    # 11 (STEP-001…STEP-006)
   git status --short                                          # only the 10 '?? data/new_model/experiments/…' lines
   git -C ../prediction-market-backtesting branch --show-current   # local-day-native
   bash --version | head -1                                    # version 5.x
   pgrep -fl "backtest.run|run_seeds|train_model|train_short|check_availability|queue.sh|confirm.sh"   # nothing
   ls "$INV/work/queue"                                        # dry-run.txt only
   ls data/backtests/dota_maker data/backtests/dota_maker/_logs   # _logs LIVE LIVE_NOXP; _logs empty
   pmset -g batt | head -1                                     # AC Power
   df -h . | tail -1                                           # at least 20 GiB free
   ```

   - If `status.tsv` or `inputs.sha256` already exists, a queue was started before. Do not launch a second one. Report and end the turn.
   - If a heavy process runs, do not stop it. Report and end the turn.

2. Typecheck (the `feature.json` "Typecheck passes" item): `uv run --group backtest python -m basedpyright`. Expect 0 errors. Run it now, because nothing heavy runs yet.

3. Write the three helper scripts exactly as above.

4. Check them:

   ```bash
   bash -n "$INV/work/detach.sh"
   bash -n "$INV/work/confirm.sh"
   uv run ruff check --config pyproject.toml "$INV/work/summarize.py"
   uv run ruff format --config pyproject.toml --check "$INV/work/summarize.py"
   DRY=1 bash "$INV/work/confirm.sh" e1-h20 e1-h10 e1-h20top2-stratz e3-deaths e3-roles \
     e2a-buy-h30 e2a-buy-h60 e2a-buysell-h30 e2a-buysell-h60 e2b-h30 e2b-h60 \
     >"$INV/work/queue/confirm-dry.txt"
   grep -c "run_seeds.sh" "$INV/work/queue/confirm-dry.txt"        # 33
   grep -c "compare_backtests.py" "$INV/work/queue/confirm-dry.txt"  # 45
   for arm in e1-h20 e1-h10 e1-h20top2-stratz e3-deaths e3-roles e2a-buy-h30 e2a-buy-h60 \
     e2a-buysell-h30 e2a-buysell-h60 e2b-h30 e2b-h60; do
     diff <(grep "run_seeds.sh dota $arm " "$INV/work/queue/dry-run.txt" | sed 's/SEED_LIST=[0-9]* //') \
       <(grep "run_seeds.sh dota $arm " "$INV/work/queue/confirm-dry.txt" | sed 's/SEED_LIST=[0-9]* //' | sort -u) \
       && echo "$arm flags match"
   done                                                            # 11 "flags match" lines
   DRY=1 bash "$INV/work/confirm.sh" e9-bogus; echo "exit $?"      # "unknown arm e9-bogus", exit 1
   ls "$INV/work/queue"                                            # still no status.tsv, no inputs.sha256
   ```

   - The `diff` loop needs bash. If the CLI shell is zsh, run the loop inside `bash -c '…'`.

5. Launch:

   ```bash
   cd "$WT"
   bash "$INV/work/detach.sh" queue "$INV/work/queue.sh"
   ```

6. Verify the start after about 45 s:

   ```bash
   sleep 45
   ps -o pid,ppid,pgid,etime,command -p "$(cat "$INV/work/queue/queue.pid")"   # alive, PPID 1, command has queue.sh
   tail -5 "$INV/work/queue/queue.log"          # "start e1-base <utc>", no "queue-exit"
   cat "$INV/work/queue/status.tsv"             # header row only
   test -s "$INV/work/queue/inputs.sha256" && echo lock-written
   pgrep -fl "backtest.run" | wc -l             # 4 or more (archive shards); 0 is fine if uv still starts
   ls data/backtests/dota_maker/_logs/e1-base/  # archive/ exists
   ```

   - If `queue-exit` is already in the log, the queue died at start. Copy the log tail into the report. Do not relaunch. Lock refusals and missing inputs need the orchestrator.

7. Write the Phase 1 report: preflight output, typecheck result, script checks, launch time (UTC), pid, the first log lines.

**END TURN.** Do not wait for the queue.

### Orchestrator, during Phase 1

- **Done:** `tail -1 "$INV/work/queue/queue.log"` starts with `queue-exit `. The code is normally 0. Arm failures do not change it. Non-zero means `queue.sh` itself stopped (`set -e`).
- **Alive:** `ps -p "$(cat "$INV/work/queue/queue.pid")" -o command=` contains `queue.sh`.
- **Crash:** not alive and no `queue-exit` line. Wake the implementer for Phase 2 anyway. The rerun resumes the queue.
- **Process names:** `bash …/work/queue.sh`, `scripts/run_seeds.sh dota <arm>`, `python -m backtest.run`, `scripts/compare_backtests.py`.
- **Progress:** `awk -F'\t' 'NR>1{s[$1]=$4} END{for(i in s) print i"\t"s[i]}' "$INV/work/queue/status.tsv" | sort`. The current item is the last `start <item>` line in `queue.log`.
- **Poll** every 10 min. Expected cumulative end times after launch:

  | item | ends near |
  | --- | --- |
  | `e1-base` | 0:45 |
  | `e1-h20` | 1:30 |
  | `e1-h10` | 2:15 |
  | `e1-h20top2-stratz` | 3:00 |
  | `e3-deaths` | 3:45 |
  | `e3-roles` | 4:30 |
  | `e2a-buy-h30` | 5:07 |
  | `e2a-buy-h60` | 5:44 |
  | `e2a-buysell-h30` | 6:21 |
  | `e2a-buysell-h60` | 6:58 |
  | `e2b-h30` | 7:35 |
  | `e2b-h60` | 8:12 |
  | `e1-base-seeds12` | 8:38 |

- **Hang rule:** the current item runs longer than 70 min (all-map arm), 55 min (E2 arm), or 40 min (`e1-base-seeds12`), and `find "$WT/data/backtests/dota_maker/_logs/<arm>" -type f -mmin -30 | head -1` prints nothing. Then wake the implementer with "suspected hang". `<arm>` for `e1-base-seeds12` is `e1-base`.
- **Emergency stop, only on the orchestrator's decision:** `kill -TERM -- -"$(cat "$INV/work/queue/queue.pid")"`. The wrapper is a session leader, so this stops the whole group. A later `RERUN=1` resumes the queue.
- **Machine rules until Phase 4:** no pytest, no training, no other backtest on this machine. One backtest peaks at 20–28 GB of 32 GB. Keep the lid open and AC power on. `caffeinate -i` does not stop lid-close sleep.
- **Wake prompt:** "STEP-007 Phase 2. The queue exited: `<last line of queue.log>` (or: the queue process died without `queue-exit`). Follow `plans/STEP-007.md` Phase 2."

## Phase 2 — read the queue result, pick winners, launch the driver

1. Confirm that nothing runs:

   ```bash
   cd "$WT"
   tail -1 "$INV/work/queue/queue.log"
   pgrep -fl "work/queue.sh|work/confirm.sh|backtest.run|run_seeds"   # nothing
   ```

   If a queue process still runs, report it and end the turn.

2. Latest status per item. Expect 13 items, all `ok`:

   ```bash
   awk -F'\t' 'NR>1{s[$1]=$4} END{for(i in s) print i"\t"s[i]}' "$INV/work/queue/status.tsv" | sort
   ```

   For each item that is `fail` or absent, copy the reason into the report: `tail -20 "$INV/work/queue/<item>.log"` and the newest file under `data/backtests/dota_maker/_logs/<arm>/`. Phase 4 needs these reasons if the rerun fails too.

3. Compare files:

   ```bash
   ls "$INV/work/compare"/*.txt | wc -l      # 48 when every item is ok
   ls "$INV/work/compare"/*.err 2>/dev/null  # list; head -5 each
   ```

   An `.err` next to a valid catalog is a compare problem, not an arm failure. Record it.

4. If `e1-base` is `ok`, add the schedule slice against LIVE:

   ```bash
   env PYTHONPATH=src:../prediction-market-backtesting:scripts uv run --group backtest python \
     scripts/compare_backtests.py data/backtests/dota_maker/LIVE \
     data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_e1-base \
     --allow-drift --signal-mode schedule \
     >"$INV/work/compare/e1-base__vs__LIVE__schedule.txt" 2>&1
   sed -n '/^== pooled/,+1p' "$INV/work/compare/e1-base__vs__LIVE__schedule.txt"   # expected: identical
   ```

   If it is not `identical`, record the numbers in the report. It does not block the confirmation: arms compare against `e1-base`, not LIVE.

5. Summary table:

   ```bash
   python3 -I "$INV/work/summarize.py" "$INV/work/compare" >"$INV/work/summary.tsv"
   column -t -s $'\t' "$INV/work/summary.tsv" | cut -c1-220
   ```

   Cross-check one row by hand. Take `e1-base__vs__LIVE`: compare its seed-0 `net` and `pooled_p` with the raw `.txt`. On a mismatch, fix `summarize.py`, not the numbers.

6. Winners:

   ```bash
   python3 -I "$INV/work/summarize.py" --winners "$INV/work/compare"
   ```

   Keep only arms whose latest queue status is `ok`.

7. `RERUN` decision. Set `RERUN=1` if any of these is true:
   - the `queue-exit` code is not 0, or the queue died without that line;
   - one of the 13 items is absent from `status.tsv`;
   - one item's latest status is `fail`.

   Otherwise leave `RERUN` empty.

8. If there are no winners and `RERUN` is empty, go to Phase 4 in this turn.

9. If `e1-base` is not `ok`, no winner can be known yet. Launch the driver with `RERUN=1` and no arms. Phase 3 picks the winners.

10. Launch the driver. Use the winner list from step 6, in any order:

    ```bash
    cd "$WT"
    DRY=1 bash "$INV/work/confirm.sh" <winners…> | grep "run_seeds.sh"   # 3 lines per winner
    RERUN=<1 or empty> bash "$INV/work/detach.sh" confirm "$INV/work/confirm.sh" <winners…>
    sleep 45
    ps -o pid,ppid,etime,command -p "$(cat "$INV/work/queue/confirm.pid")"   # alive, command has confirm.sh
    tail -5 "$INV/work/queue/confirm.log"   # "start <item>" or "skip <item>" lines, no "confirm-exit"
    ```

11. Write the Phase 2 report:
    - the latest status per item;
    - `.err` files;
    - the schedule-slice result;
    - the winners with their seed-0 `net`;
    - the `RERUN` value and its reason;
    - the launch time and the expected driver length.

**END TURN.**

### Orchestrator, during Phase 2

- **Done:** `grep -c '^confirm-exit ' "$INV/work/queue/confirm.log"` returns 1.
- **Alive:** `ps -p "$(cat "$INV/work/queue/confirm.pid")" -o command=` contains `confirm.sh`. With `RERUN=1`, `pgrep -fl work/queue.sh` also shows the inner queue.
- **Crash:** not alive and no new `confirm-exit` line. Wake the implementer with that fact.
- **Progress:** `<arm>-seeds012` rows in `status.tsv`, plus `start` lines in `confirm.log`.
- **Length:**
  - rerun part: about 45 min per failed all-map arm and 37 min per failed E2 arm (longer if the queue stopped early);
  - about 25–30 min per all-map winner and 10–15 min per E2 winner;
  - 5–10 min of compare refresh at the end.
- **Hang rule:** no file under `$WT/data/backtests/dota_maker/_logs/` changed in 30 min while the driver is alive.
- **Wake prompt:** "STEP-007 Phase 3. The driver exited: `<last line of confirm.log>`. Follow `plans/STEP-007.md` Phase 3."

## Phase 3 — late winners and one confirmation retry (only if needed)

1. Confirm that nothing runs (same `pgrep` as Phase 2, step 1). Read `tail -1 "$INV/work/queue/confirm.log"`.
2. Rebuild `summary.tsv` (Phase 2, step 5) and print the latest status per item.
3. Collect arms for a second driver:
   - **Late winners:** arms that were not `ok` in Phase 2, are `ok` now, and are in `--winners`. If `e1-base` was rerun, this is every arm in `--winners`.
   - **Confirmation retry:** arms whose `<arm>-seeds012` latest status is `fail` with `still missing seeds` in `$INV/work/queue/<arm>-seeds012.log`. Each gets one retry here.
   - Arms that fail again after the Phase 2 rerun: copy the reason from the item log. Do not rerun them.
4. If the list is empty, go to Phase 4 in this turn.
5. Otherwise launch **without** `RERUN`:

   ```bash
   cd "$WT"
   bash "$INV/work/detach.sh" confirm "$INV/work/confirm.sh" <arms…>
   ```

   Verify it as in Phase 2, step 10. Never pass `RERUN=1` here: queue items get exactly one rerun.

6. Write the Phase 3 report.

**END TURN.**

### Orchestrator, during Phase 3

- **Done:** `grep -c '^confirm-exit ' "$INV/work/queue/confirm.log"` returns 2.
- Same alive, crash, progress, and hang rules as Phase 2. Length: 10–30 min per arm plus 5–10 min of compares.
- **Wake prompt:** "STEP-007 Phase 4. The driver exited: `<last line of confirm.log>`. Follow `plans/STEP-007.md` Phase 4."

## Phase 4 — summary into `progress.txt`

1. Confirm that nothing runs (same `pgrep`).
2. Recompute the schedule slice against LIVE (Phase 2, step 4). It takes about 4 s, and `e1-base` seeds can change in a rerun.
3. Rebuild `$INV/work/summary.tsv`.
4. Append one entry to `$TASK/progress.txt` in this shape. Copy numbers from `summary.tsv`. Grid-v1 and schedule/grid map counts come from the `shared` column of each slice.

   ```markdown
   ## <YYYY-MM-DD HH:MM> CEST - STEP-007

   - Worktree `../esports-trader-exp`, branch `exp-part2`, head `9f4bedf2`. No commit. `passes` left false.
   - Queue: launched <utc>, `queue-exit <code>` <utc>, wall <h>. Latest status: <n>/13 ok. Rerun: <none | items and result>. Failures that stayed: <item: reason from the log>.
   - Confirmation (seed-0 Δ net > 0 against `e1-base`): <arms | none>. Driver runs: <start–end, wall>. `<arm>-seeds012`: <ok | fail and reason>.
   - Definitions: Δ net = `pnl with rebate` delta (candidate − base) on shared maps. p = pooled paired t / Wilcoxon over shared maps. Worst = `worst map (pre)` delta per seed (≥ 0 = not worse). Schedule maps graft the seed-0 archive, so schedule/grid deltas repeat on every seed.
   - Arms against `e1-base`:
     - `<arm>`: seeds <s>; shared <n>; Δ net <per seed> (mean <m>); p t/W <pt>/<pw>; grid_v1 (<n>) Δ net <per seed>; schedule/grid (<n>) Δ net <per seed>; worst <per seed>.
     - … one line for each of the 11 arms, including arms that failed (`ERR` and the reason).
   - Pairs:
     - `e1-h20top2-stratz` vs `e1-h20`: same fields.
     - `e3-roles` vs `e3-deaths`: same fields.
     - `e2a-buysell-h30` vs `e2a-buy-h30`, `e2a-buysell-h60` vs `e2a-buy-h60`: same fields.
   - `e1-base` vs LIVE: whole set <fields>; `--signal-mode schedule` (<n> maps) <identical | numbers>; grid_v1 (<n>) <fields> (effect of fix A); schedule/grid <fields>.
   - Arms that meet all four PLAN §11 checks on 3 seeds (Δ net > 0 on every seed, both p < 0.05, same sign in grid_v1 and schedule/grid, worst ≥ 0 on every seed): <arms | none>. This is a mechanical check, not the keep/drop decision.
   - Files: `$INV/work/queue/status.tsv`, `$INV/work/compare/*.txt`, `$INV/work/summary.tsv`.
   - **Learnings for future iterations:**
     - <what a later session must know: timings, failures, surprises>
   ```

5. Final checks:

   ```bash
   cd "$WT"
   git rev-parse HEAD        # still 9f4bedf24f96d32429699868700f4e60dda3ab21
   git status --short        # still only the 10 untracked experiment dirs
   bash -n "$INV/work/detach.sh"
   bash -n "$INV/work/confirm.sh"
   uv run ruff check --config pyproject.toml "$INV/work/summarize.py"
   ```

   The Phase 1 basedpyright result stands, because the head and the tree did not change.

6. Write the final report. Leave `passes` false. The orchestrator sets it.

**END TURN.**

## Edge cases

- **`e1-base` fails.** No seed-0 delta exists. Phase 2 launches `RERUN=1` with no arms. Phase 3 picks all winners.
- **`e1-base-seeds12` fails.** The rerun runs first in the driver, so base seeds 1–2 exist before any winner compare. If it fails again, winner compares pair only seed 0 and the seeds that exist. The summary shows the real seed list.
- **`queue.sh` stops early (`set -e`).** Examples: `arm-state returned no row`, a Python crash in `queue_check.py`. Items after the stop never ran. The single rerun continues from there and can take hours. Winners then come in Phase 3.
- **`refusing to resume`.** Someone changed `queue.sh`, `queue_check.py`, or an input. Stop and report. Do not delete `inputs.sha256`.
- **`invalid` or `mismatch` item.** The rerun does not relaunch it. Record the reason from `<item>.log`.
- **Δ net exactly `+0.00` on seed 0.** Not a winner. This is likely when a knob never fires on any map.
- **Zero-map slice.** It prints `identical` with `shared 0`. Report the count, and do not call it a tie.
- **Stale pid.** The `*-exit` line is the main signal. A pid alone can belong to a new process after reuse.
- **zsh.** `$args` without quotes does not split in zsh. Run multi-word loops through `bash -c`.

## Verification

- Phase 1: preflight lines as listed, `basedpyright` 0 errors, `bash -n` on both shell scripts, `ruff check` and `ruff format --check` on `summarize.py`, DRY counts 33 and 45, 11 "flags match" lines, the unknown-arm test exits 1, the queue alive after 45 s, `inputs.sha256` written.
- Phase 2: 13 `ok` items (or the rerun is launched), 48 compare `.txt` files, the schedule slice `identical`, one `summary.tsv` row checked by hand against the raw compare file.
- Phase 3–4: each `<arm>-seeds012` is `ok`, or its failure reason is recorded. Each winner's `<arm>__vs__e1-base.txt` shows `seeds 0,1,2`. The head and `git status` are unchanged. `progress.txt` has the entry.
- No pytest: no repo code changes in this step.

## Expected time

| part | length |
| --- | --- |
| Phase 1 agent work (preflight, basedpyright, scripts) | 15–25 min |
| Queue | about 8.5 h (range 8–9.5 h) |
| Phase 2 agent work | 10–20 min |
| Driver, no rerun, typical 3–6 winners | 1–2 h |
| Driver, worst case (11 winners: 5 all-map × 27 min, 6 E2 × 10 min) | about 3.5 h |
| Rerun, if needed | +37–45 min per failed arm |
| Phase 3, if needed | 0.2–1 h |
| Phase 4 agent work | 20–30 min |
| **Total wall time** | **about 10–13 h, typical 10.5–11.5 h** |

## Open risks

- **Pairs can stay on seed 0.** A pair gets 3 seeds only when both arms are winners against `e1-base`. Example: `e1-h20top2-stratz` wins and `e1-h20` does not. The pair `e1-h20top2-stratz` vs `e1-h20` then stays on seed 0. To get 3 seeds, add the partner name to the `confirm.sh` arguments. Cost: about 27 min per all-map arm and 10–15 min per E2 arm. Default: follow `feature.json` and do not add it.
- **The seed-0 filter is weak.** About half of the arms with no real effect pass Δ net > 0 on one seed. Expect 3–6 confirmations.
- **3-seed evidence covers only grid-v1 maps.** Schedule maps are the same on every seed. For E2 arms that is about 79 grid-v1 maps.
- **The CLI can end a detached job.** `detach.sh` starts a new session to prevent that. I verified this on this Mac with a dummy job, not with the Grok CLI. The orchestrator's first poll after Phase 1 confirms it.
- **Unknown full-queue compare time.** The 51 compares take seconds each. Each `run_seeds.sh` call also ends with its own compare against LIVE. Both are inside the estimates above.
