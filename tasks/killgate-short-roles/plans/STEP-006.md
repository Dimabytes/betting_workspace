# STEP-006 plan — queue script, E3 training, and checks before the queue

Worktree `../esports-trader-exp`, branch `exp-part2`, head `6022fec4`. All paths are relative to the worktree unless they start with `$INV` or `betting_workspace`.

`$INV` = `betting_workspace/investigations/2026-10-13-killgate-short-roles` (absolute: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-13-killgate-short-roles`).

## Goal

1. One script `$INV/work/queue.sh`. It runs every arm strictly one at a time, skips finished arms, survives a failed arm, and writes the comparisons after each arm.
2. Six trained catalogs: `control-{xp,noxp}`, `e3-deaths-{xp,noxp}`, `e3-roles-{xp,noxp}` under `data/new_model/experiments/`.
3. All checks before the queue pass, and their numbers are in `progress.txt`.

## Facts I verified in the code

- **Gap: a `--since-match` seed never reads `_archive`.** `main()` calls `load_seed_archive(..., validation=args.validation, limit=args.limit, ...)` (`src/backtest/run.py:2528-2535`). `load_seed_archive` returns an empty archive unless `validation or limit is not None` (`:2191-2206`). `--since-match` and `--validation` are mutually exclusive (`:1452-1468`), so `args.validation` is `False` for every E2 arm. Consequences:
  - The `feature.json` hash check cannot pass as written. The warning `shared archive at … does not match this run` is logged only inside `load_shared_archive` (`src/backtest/shared_archive.py:117-129`), and a `--since-match` seed never calls it.
  - All 358 LIVE archive maps start after D (LIVE seed0: 437 test rows = 79 grid-v1 + 261 schedule/grid + 97 schedule/oddin; 449 pre-D rows are all grid-v1). The archive stage of an E2 arm replays those 358 maps, and then seed0 replays them again. An E2 arm then takes about 64 min instead of about 35 min.
  - In STEP-007, seeds 1–2 of an E2 arm would replay archive maps with seed-1/2 latency draws. `e1-base` seeds 1–2 graft the seed-0 archive (`--archives-only` is pinned to the default seed, `run.py:1645-1646`). Paired deltas on archive maps would then mix seed noise with the knob effect.
- `run_seeds.sh` skips the archive stage when `data/backtests/dota_maker/validation_*_${NAME}/_archive/DONE` exists (`scripts/run_seeds.sh:100`). A second run with the same name and a changed input then leaves the stale `_archive`. The seed must detect the mismatch itself.
- Run dir name: `validation_join_delta{min}{_x{exit}}_cut480_p45_<name>` (`run.py:952-964`). E2b arms get `validation_join_delta001438_x000919_cut480_p45_e2b-h30` and `validation_join_delta005263_x003768_cut480_p45_e2b-h60`. A glob `validation_*_<arm>` finds every arm. No arm name is a suffix of another arm name.
- A rerun without `--resume` restarts unfinished shards: `plan_shard_replay` clears its own shard dir (`run.py:1966-2012`). A merged seed has `summary.json`. A finished archive has `DONE`. So "skip when `seed<N>/summary.json` exists, else rerun" is safe. A crash loses at most the unfinished stage of one arm (≤ 31 min).
- `compare_backtests.py` takes two run dirs (catalog roots), pairs the shared `seed<N>` dirs, and exits 0 with `--allow-drift` (`scripts/compare_backtests.py:1082-1127`). One full compare (868 maps × 3 seeds) took 9.6 s. One compare with 88 maps took 3.9 s.
- `--kill-hold-top2` requires `--kill-post-table-hold-s > 0` (`run.py:1812-1823`). The hash check therefore passes both flags.
- `train_model.py --model-dir` refuses the live catalogs and archives a previous model under `data/new_model/experiments/archive/` (`src/train_model/train_model.py:172-183, :439`). `--extra-features` appends the table columns and copies the table into the catalog (`:236-248, :327-328`).
- Member fits use one LightGBM thread each (`src/shared/utils/gbm.py:44, :154-158`). The current datasets have the SHA256 values recorded in `data/new_model/research/model.json`: train `8076a877…`, validation `36bfaaac…`. So a retrained control is expected to be bit-identical.
- Research metrics (`model.json`): XP `mae_gain_300_cents` 0.2995 [0.2166, 0.3848], `dir_300_cents` 1.8897, trees 64–96. No-XP 0.2909 [0.2075, 0.3774], 1.8583, trees 51–76.
- The backtest raises when a decision row has no extra-feature row (`attach_model_extra_features` → `require_extra_features`, `src/backtest/signals.py:345-356`, `src/shared/utils/dota_features.py:501-504`). One missing key fails the whole shard, so the whole E3 arm fails.
- `_assemble_match_signals` joins extras on `decision.snapshot.second` (`signals.py:611-633`). `MatchSignals` does not carry that second. The only producer of decisions is `replay_received_snapshots`, imported into `backtest.signals` and called at `signals.py:731` (grid-v1) and `:802` (schedule).
- LIVE is `validation_join_delta02_x015_cut480_p45_rebuild-1012` with seeds 0, 1, 2. Its seed0 has 886 rows: 528 grid-v1, 261 schedule/grid, 97 schedule/oddin. The latest archive map is `9040578015` (horn 2026-10-11 18:49Z, schedule/grid). It is the only map at or after its own start.
- rebuild-1012 logs: archive with 4 shards 12:44 → 13:15 (31 min). Seeds ran one at a time, 12 min each.
- `/opt/homebrew/bin/bash` is 5.3 and is first on `PATH`. `/bin/bash` is 3.2. `shellcheck` is not installed. Disk: 132 GiB free; one LIVE run is 183 MB plus about 44 MB of gzipped logs.

## Decisions

1. **Fix the `--since-match` archive gap first, in one small commit.** `load_seed_archive` gets a `since_match: int | None` parameter and reads `_archive` for a `--validation`, `--since-match`, or `--limit` seed. Without it the hash check fails, E2 arms cost about 3 extra hours, and STEP-007 seeds 1–2 of E2 arms are not comparable with `e1-base`. This completes the STEP-001 intent (PLAN §1: "поиск `_archive/DONE` … работает").
2. **`queue.sh` items are the 13 rows of PLAN "Этап 2".** Row 13 is one item `e1-base-seeds12`. It runs `SEED_LIST="1"`, then `SEED_LIST="2"`, and writes one status row. This gives the 13 `ok` rows that STEP-007 expects.
3. **Skip rule:** an item is done when every `seed<N>/summary.json` it needs exists. The exit code of `run_seeds.sh` is ignored.
4. **Compare rule:** a compare file is rewritten when it is missing or older than any `seed*/summary.json` of the candidate run. A failed compare goes to `<file>.err` and is retried on the next start. After `e1-base-seeds12`, `e1-base` vs LIVE is rewritten on 3 seeds. Arm-vs-base files stay on seed 0. STEP-007 rewrites them when an arm gets seeds 1–2.
5. **`check_availability.py` records decisions by wrapping `backtest.signals.replay_received_snapshots`.** The wrapper calls the real function and keeps each `SignalReplay`. Then `resolve_run_signals` runs exactly as a `--validation` seed 0 does. This gives the real decision rows (`match_id`, `snapshot.second`, table deaths) without changing repo code.
6. **The availability script also counts missing keys** in `roles.parquet` and `deaths.parquet` over all decisions. Missing must be 0. Otherwise the E3 arms crash in the queue: stop and report.
7. **e3-control is conditional.** If the control members match research by SHA256, `queue.sh` has no `e3-control`. If they differ, add the arm before `e3-deaths` and add the pairs `e3-deaths`/`e3-control` and `e3-roles`/`e3-control`.
8. **No new comments** in code or scripts, per `.feature-json.config.json`. Untracked outputs (`data/new_model/experiments/*`, `data/experiments/*`, `$INV/work/*`) are never staged.

## Implementation

Before every heavy job (train, backtest, availability script): `pgrep -fl "backtest.run|run_seeds|train_model|train_short|check_availability"`. If another heavy job runs, wait. Do not stop processes you did not start. Record wall time with `/usr/bin/time -p`.

### Phase 0 — `--since-match` seeds graft `_archive` (commit)

`src/backtest/run.py`:

```python
def load_seed_archive(
    report_dir: Path,
    *,
    archives_only: bool,
    match_id: int | None,
    validation: bool,
    since_match: int | None,
    limit: int | None,
    expected_manifest: Callable[[], Mapping[str, Any]],
) -> SharedArchive:
    """Read `_archive` for a validation, --since-match, or --limit seed. Skip the hash unless DONE exists."""
    cohort = validation or since_match is not None or limit is not None
    if archives_only or match_id is not None or not cohort:
        return empty_shared_archive()
    ...
```

Call site in `main()`: add `since_match=args.since_match,`. Keep the line length under the ruff limit; split the docstring line if needed.

The archive stage of an E2 arm runs with the same `TARGET`, so its manifest has the same `since_match` and the same schedule cohort (`manifest_for_selection` with `archives_only=True`, `run.py:2146-2189`). The seed's expected manifest then matches.

Test in `tests/test_since_match.py` (654 lines now):

```python
def test_since_match_seed_reads_the_shared_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = SharedArchive(results=(), fills=(), events=(), ids=frozenset({7}))
    seen: list[Path] = []

    def load_archive(archive_dir: Path, _manifest: Mapping[str, Any]) -> SharedArchive:
        seen.append(archive_dir)
        return archive

    monkeypatch.setattr(backtest_run, "load_shared_archive", load_archive)
    archive_dir = tmp_path / ARCHIVE_DIRNAME
    archive_dir.mkdir()
    (archive_dir / ARCHIVE_DONE_FILENAME).write_text("ok\n")

    def read_seed_archive(since_match: int | None) -> SharedArchive:
        return load_seed_archive(
            tmp_path / "seed1",
            archives_only=False,
            match_id=None,
            validation=False,
            since_match=since_match,
            limit=None,
            expected_manifest=lambda: {"model_name": "seed"},
        )

    assert read_seed_archive(7) is archive
    assert seen == [archive_dir]
    assert read_seed_archive(None).ids == frozenset()
    assert seen == [archive_dir]
```

Imports: `load_seed_archive` from `backtest.run`; `ARCHIVE_DIRNAME`, `ARCHIVE_DONE_FILENAME`, `SharedArchive` from `backtest.shared_archive`. `Mapping` and `Any` are already imported there.

Checks, then commit only these two files:

```bash
uv run --group backtest python -m basedpyright
PYTEST_N=8 PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest pytest -q \
  tests/test_since_match.py tests/test_backtest_validation.py tests/test_backtest.py
git add src/backtest/run.py tests/test_since_match.py
git commit -m "Graft the shared archive into --since-match seeds."
```

Use the targeted pytest invocation that the earlier steps used, if it differs (conftest reads `PYTEST_N`). Optional: `PYTEST_N=8 make test`. The 18 known failures are allowed (13 `ODDIN_BRAND_TOKEN` host tests, 2 LoL audit parquet, 3 Dota grid-v1 seed0 goldens). Any other failure blocks the commit.

### Phase 1 — control training

One at a time:

```bash
DS=data/new_processed/dataset
/usr/bin/time -p make train ARGS="--lag-seconds 10 --training-dataset $DS/training_dataset.parquet --validation-dataset $DS/validation_dataset.parquet --model-dir data/new_model/experiments/control-xp"
/usr/bin/time -p make train ARGS="--lag-seconds 10 --training-dataset $DS/training_dataset.parquet --validation-dataset $DS/validation_dataset.parquet --model-dir data/new_model/experiments/control-noxp --no-xp"
```

Compare members:

```bash
for pair in "research control-xp" "research-noxp control-noxp"; do
  set -- $pair
  if diff <(cd "data/new_model/$1" && shasum -a 256 member_*.txt) \
          <(cd "data/new_model/experiments/$2" && shasum -a 256 member_*.txt); then
    echo "$2 match"
  else
    echo "$2 MISMATCH"
  fi
done
```

Record "match" or "MISMATCH" for both, and `member_trees` of control vs research. If either pair mismatches, apply decision 7. Do not investigate the cause in this step; record the first differing member and its tree counts.

### Phase 2 — E3 training

One at a time, same command plus `--extra-features`:

| catalog | flags |
| --- | --- |
| `e3-deaths-xp` | `--extra-features data/experiments/e3/deaths.parquet` |
| `e3-deaths-noxp` | `--no-xp --extra-features data/experiments/e3/deaths.parquet` |
| `e3-roles-xp` | `--extra-features data/experiments/e3/roles.parquet` |
| `e3-roles-noxp` | `--no-xp --extra-features data/experiments/e3/roles.parquet` |

After each run:

- `model.json` `features` has 85 / 74 names (deaths) or 128 / 117 names (roles), with the extra names at the end.
- `extra_features.parquet` in the catalog has the source SHA256 (`deaths` `fe2fee07…`, `roles` `90816fa8…`).
- Record `metrics.mae_gain_300_cents` with its CI, `metrics.dir_300_cents`, `member_trees` min–max, wall time, and `model_identity_sha256`:

```bash
PYTHONPATH=src uv run python -c "from pathlib import Path; from shared.utils.gbm import model_identity_sha256; import sys; print(model_identity_sha256(Path(sys.argv[1])))" data/new_model/experiments/e3-roles-xp
```

### Phase 3 — `$INV/work/check_availability.py`

Run from the worktree:

```bash
/usr/bin/time -p env PYTHONPATH=src:../prediction-market-backtesting \
  uv run --group backtest python "$INV/work/check_availability.py"
```

Script:

```python
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

import backtest.signals as backtest_signals
from backtest.feed_schedules import MatchFeedPlan, SchedulePlan, resolve_feed_plans
from backtest.marks import MidSeries
from backtest.postprocess import load_match_mid_series
from backtest.run import load_run_selection, resolve_run_signals
from backtest.signal_replay import ReceivedSnapshot, SignalReplay, replay_received_snapshots
from shared.utils.dota_features import HistoryPolicy
from strategy.types import KillGateUpdate

ROLES_PATH = Path("data/experiments/e3/roles.parquet")
DEATHS_PATH = Path("data/experiments/e3/deaths.parquet")
KEYS = ["match_id", "second"]
SIDES = ("radiant", "dire")
POSITION_COLUMNS = {
    side: [f"pos{position}_deaths_{side}" for position in range(1, 6)] for side in SIDES
}

recorded: list[SignalReplay] = []


def record_replay(
    match_id: int,
    model_dir: Path,
    snapshots: Sequence[ReceivedSnapshot],
    gates: Sequence[KillGateUpdate],
    series: MidSeries,
    history_policy: HistoryPolicy,
    entry_age_seconds: float,
    prior: float,
) -> SignalReplay:
    replay = replay_received_snapshots(
        match_id, model_dir, snapshots, gates, series, history_policy, entry_age_seconds, prior
    )
    recorded.append(replay)
    return replay


def label_cohort(plan: MatchFeedPlan) -> str:
    if isinstance(plan, SchedulePlan):
        return f"schedule_{plan.binding.feed_source}"
    return "grid_v1"


def build_decision_frame(cohorts: Mapping[int, str]) -> pd.DataFrame:
    decisions = [decision for replay in recorded for decision in replay.decisions]
    return pd.DataFrame(
        {
            "match_id": [decision.match_id for decision in decisions],
            "second": [decision.snapshot.second for decision in decisions],
            "deaths_radiant": [int(decision.snapshot.levels["deaths_radiant"]) for decision in decisions],
            "deaths_dire": [int(decision.snapshot.levels["deaths_dire"]) for decision in decisions],
            "cohort": [cohorts[decision.match_id] for decision in decisions],
        }
    )


def print_share(label: str, frame: pd.DataFrame) -> None:
    over = frame["over"]
    print(
        f"{label}: decisions {len(frame)} over {int(over.sum())} share {over.mean():.6f}"
        f" maps {frame['match_id'].nunique()} maps_over {frame.loc[over, 'match_id'].nunique()}"
        f" mean_excess {frame.loc[over, 'excess'].mean():.3f} max_excess {frame['excess'].max()}"
    )


def main() -> None:
    backtest_signals.replay_received_snapshots = record_replay
    selection = load_run_selection(
        game="dota", match_id=None, since_match=None, limit=None, allowed_event_ids=None
    )
    feed = resolve_feed_plans(
        selection.archive_join, selection.selected_ids, None, None, model_override_noxp=None
    )
    match_ids = tuple(
        match_id for match_id in selection.selected_ids if match_id not in feed.exclusions
    )
    resolve_run_signals(
        signal_cadence_seed=0,
        cadence_mean_interval=None,
        game="dota",
        plan_match_ids=match_ids,
        signal_rows=selection.signal_rows,
        model_dir=selection.model_dir,
        selection_lag_seconds=selection.lag_seconds,
        plans=feed.plans,
        mids=load_match_mid_series(match_ids),
        kill_top2=None,
        short_models=None,
    )
    decisions = build_decision_frame(
        {match_id: label_cohort(feed.plans[match_id]) for match_id in match_ids}
    )
    columns = [column for side in SIDES for column in POSITION_COLUMNS[side]]
    roles = pd.read_parquet(ROLES_PATH, columns=[*KEYS, *columns])
    joined = decisions.merge(roles, on=KEYS, how="left", validate="many_to_one")
    missing_roles = joined[columns].isna().any(axis=1)
    deaths_keys = pd.MultiIndex.from_frame(pd.read_parquet(DEATHS_PATH, columns=KEYS))
    missing_deaths = ~pd.MultiIndex.from_frame(decisions[KEYS]).isin(deaths_keys)
    print(
        f"decisions {len(decisions)} maps {decisions['match_id'].nunique()}"
        f" missing_roles {int(missing_roles.sum())} missing_deaths {int(missing_deaths.sum())}"
    )
    found = joined[~missing_roles]
    excess = pd.concat(
        [found[POSITION_COLUMNS[side]].sum(axis=1) - found[f"deaths_{side}"] for side in SIDES],
        axis=1,
    ).max(axis=1)
    found = found.assign(excess=excess, over=excess > 0)
    print_share("all", found)
    for cohort, part in found.groupby("cohort", sort=True):
        print_share(str(cohort), part)


if __name__ == "__main__":
    main()
```

Notes:

- `load_run_selection` uses the same selection as `--validation` (886 maps after the 15 archive exclusions). Seed 0 cadence only.
- "Over" means the position sum from STRATZ is larger than the table deaths that this decision sees, on either side. That is the availability violation. It is measured only; it is not fixed (non-goal).
- Expected cost: one process, about 5–15 min, peak below 15 GB (validation rows + game features of 358 archive maps + one research prediction over all decisions). Do not overlap with training.
- If `missing_roles` or `missing_deaths` is not 0, stop. The E3 arms would fail in `require_extra_features`. Report the first missing keys.
- basedpyright strict can need `cast` on `groupby` keys and on the `.mean()` results, as in STEP-004 scripts.

### Phase 4 — single-map checks

Command shape (one at a time, seed 0):

```bash
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run \
  --game dota --match-id <MAP> --name <NAME> [flags]
```

The output dir is `data/backtests/dota_maker/match_<MAP>_<NAME>/`. Before the first run, save `ls data/backtests/dota_maker data/backtests/dota_maker/_logs` to your scratch dir.

**No-change check.** `9038344101` (schedule/grid, LIVE seed0: 58 BUY, 25 SELL, `engine_pnl` −115.081054), name `noop-check`, no flags:

```bash
uv run python - <<'EOF'
import pandas as pd
from pandas.testing import assert_frame_equal
run = pd.read_parquet("data/backtests/dota_maker/match_9038344101_noop-check/results.parquet")
live = pd.read_parquet("data/backtests/dota_maker/LIVE/seed0/results.parquet")
live = live[live["match_id"] == 9038344101]
assert_frame_equal(run.reset_index(drop=True), live.reset_index(drop=True), check_dtype=False)
print("noop ok", run["engine_pnl"].iloc[0], run["buy_fills"].iloc[0], run["sell_fills"].iloc[0])
EOF
```

`check_dtype=False` covers the known `merge_shares` / `merge_usdc` int-vs-float difference of a one-row file (STEP-005 learning). Any other difference fails the check.

**Smokes.** Each run must exit 0 and write `summary.json`. Record `engine_pnl`, BUY and SELL fills, and the manifest keys below.

| name | map | flags | manifest check |
| --- | --- | --- | --- |
| `smoke-hold` | `9040578015` | `--kill-post-table-hold-s 20` | `kill_gate_post_table_hold_s` = 20.0 |
| `smoke-top2` | `9040578015` | `--kill-post-table-hold-s 20 --kill-hold-top2 data/experiments/kill-top2/top2_deaths.parquet` | `kill_gate_hold_top2_only` = true, `kill_top2_sha256` = `af41dc79…` |
| `smoke-e3roles-oddin` | `9010695310` (schedule/oddin) | `--model-dir data/new_model/experiments/e3-roles-xp --model-dir-noxp data/new_model/experiments/e3-roles-noxp` | `model_sha256_by_dir` has the e3-roles-noxp identity from Phase 2 |
| `smoke-e3roles-v1` | `8987569122` (grid_v1) | same as above | `model_sha256` = e3-roles-xp identity from Phase 2 |
| `smoke-e2b-h30` | `9040578015` | `--model-dir data/new_model/experiments/short-h30-xp --model-dir-noxp data/new_model/experiments/short-h30-noxp --min-abs-delta 0.001438 --exit-abs-delta 0.000919` | `min_abs_delta` 0.001438, `exit_abs_delta` 0.000919, `model_sha256` `c79e8da0…` |

Take g and x from `$INV/work/e2/gates.json` (`h30.min_abs_delta`, `h30.exit_abs_delta`), not from this table, if the file changed.

Delete the six `match_<MAP>_<NAME>` dirs that these runs made. The listing must equal the saved one.

### Phase 5 — hash check (needs Phase 0)

```bash
HASH="$INV/work/hash"
mkdir -p "$HASH"
uv run python - "$HASH/top2_deaths_changed.parquet" <<'EOF'
import sys
import pandas as pd
frame = pd.read_parquet("data/experiments/kill-top2/top2_deaths.parquet")
frame.loc[0, "is_top2"] = not bool(frame.loc[0, "is_top2"])
frame.to_parquet(sys.argv[1], index=False)
print(frame.dtypes.to_dict())
EOF
RUN=data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_hash-smoke
LOG=data/backtests/dota_maker/_logs/hash-smoke/seed0/run.log
SEEDS=1 SHARDS=1 TARGET="--since-match 9040578015" scripts/run_seeds.sh dota hash-smoke \
  --kill-post-table-hold-s 20 --kill-hold-top2 data/experiments/kill-top2/top2_deaths.parquet || true
grep -c "does not match this run" "$LOG" || true
grep "resumed rows" "$LOG"
SEEDS=1 SHARDS=1 TARGET="--since-match 9040578015" scripts/run_seeds.sh dota hash-smoke \
  --kill-post-table-hold-s 20 --kill-hold-top2 "$HASH/top2_deaths_changed.parquet" || true
grep "does not match this run" "$LOG"
grep "matches still missing" "$LOG"
uv run python -c "import json,sys; print(json.load(open(sys.argv[1]))['kill_top2_sha256'], json.load(open(sys.argv[2]))['kill_top2_sha256'])" "$RUN/_archive/manifest.json" "$RUN/seed0/manifest.json"
```

Pass conditions:

- Run 1: `_archive/DONE` exists. The seed log has no "does not match this run" and has `resumed rows: 1` (the seed grafted the archive; this also proves Phase 0).
- Run 2: the archive stage is skipped (DONE glob). The seed log has "does not match this run" with `kill_top2_sha256` in the key list, and `matches still missing: 1` (the seed replayed the archive map). `seed0/summary.json` exists.
- `_archive/manifest.json` has `af41dc79…`. `seed0/manifest.json` has the hash of the changed copy.

The seed log is overwritten by run 2, so save the run-1 grep output first. Both `run_seeds.sh` calls exit 1 because of the LIVE compare; that is expected.

Delete `$RUN`, `data/backtests/dota_maker/_logs/hash-smoke`, and `$HASH`.

### Phase 6 — `$INV/work/queue.sh`

If Phase 1 found a mismatch, add the three marked lines. Otherwise leave them out.

```bash
#!/usr/bin/env bash
set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
  echo "queue.sh needs bash >= 4" >&2
  exit 1
fi

WORK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE="$WORK/queue"
COMPARE="$WORK/compare"
GATES="$WORK/e2/gates.json"
RUNS=data/backtests/dota_maker
MODELS=data/new_model/experiments
TOP2=data/experiments/kill-top2/top2_deaths.parquet
ANCHOR_FILE="$MODELS/short-h30-xp/test_anchor.txt"
DRY="${DRY:-}"

if [[ ! -x scripts/run_seeds.sh || "$(git branch --show-current)" != exp-part2 ]]; then
  echo "run queue.sh from the esports-trader-exp worktree on exp-part2" >&2
  exit 1
fi

for input in "$TOP2" "$GATES" "$ANCHOR_FILE" \
  "$MODELS"/{e3-deaths,e3-roles,short-h30,short-h60}-{xp,noxp}/model.json; do
  if [[ ! -f "$input" ]]; then
    echo "missing queue input: $input" >&2
    exit 1
  fi
done

read_gate() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' \
    "$GATES" "$1" "$2"
}

TEST="--since-match $(<"$ANCHOR_FILE")"
SHORT30=(--short-model-dir "$MODELS/short-h30-xp" --short-model-dir-noxp "$MODELS/short-h30-noxp"
  --short-horizon-seconds 30)
SHORT60=(--short-model-dir "$MODELS/short-h60-xp" --short-model-dir-noxp "$MODELS/short-h60-noxp"
  --short-horizon-seconds 60)
E2B30=(--model-dir "$MODELS/short-h30-xp" --model-dir-noxp "$MODELS/short-h30-noxp"
  --min-abs-delta "$(read_gate h30 min_abs_delta)" --exit-abs-delta "$(read_gate h30 exit_abs_delta)")
E2B60=(--model-dir "$MODELS/short-h60-xp" --model-dir-noxp "$MODELS/short-h60-noxp"
  --min-abs-delta "$(read_gate h60 min_abs_delta)" --exit-abs-delta "$(read_gate h60 exit_abs_delta)")

if [[ -z "$DRY" ]]; then
  mkdir -p "$QUEUE" "$COMPARE"
  if [[ ! -f "$QUEUE/status.tsv" ]]; then
    printf 'item\tstart\tend\tstatus\n' >"$QUEUE/status.tsv"
  fi
fi

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

summary_exists() {
  compgen -G "$RUNS/validation_*_$1/seed$2/summary.json" >/dev/null
}

run_arm() {
  local arm="$1" seeds="$2" target="$3"
  shift 3
  local item="$arm"
  if [[ "$seeds" != 0 ]]; then
    item="$arm-seeds${seeds// /}"
  fi
  local seed pending=()
  for seed in $seeds; do
    if ! summary_exists "$arm" "$seed"; then
      pending+=("$seed")
    fi
  done
  if (( ${#pending[@]} == 0 )); then
    echo "skip $item"
    return
  fi
  local start status=ok seed_list
  start="$(date -u +%FT%TZ)"
  [[ -n "$DRY" ]] || echo "start $item $start"
  for seed in "${pending[@]}"; do
    seed_list=""
    if (( seed > 0 )); then
      seed_list="$seed"
    fi
    local cmd=(env SEEDS=1 SHARDS=4 ARCHIVE_SHARDS=4 "TARGET=$target" "SEED_LIST=$seed_list"
      scripts/run_seeds.sh dota "$arm" "$@")
    if [[ -n "$DRY" ]]; then
      print_command "${cmd[@]}"
      continue
    fi
    "${cmd[@]}" >>"$QUEUE/$item.log" 2>&1 || true
  done
  [[ -z "$DRY" ]] || return 0
  for seed in $seeds; do
    summary_exists "$arm" "$seed" || status=fail
  done
  printf '%s\t%s\t%s\t%s\n' "$item" "$start" "$(date -u +%FT%TZ)" "$status" >>"$QUEUE/status.tsv"
  echo "end $item $status"
}

run_dir() {
  if [[ "$1" == LIVE ]]; then
    echo "$RUNS/LIVE"
    return
  fi
  local pattern="$RUNS/validation_*_$1" found
  if [[ -n "$DRY" ]]; then
    echo "$pattern"
    return
  fi
  found="$(compgen -G "$pattern" || true)"
  if [[ -z "$found" || "$found" == *$'\n'* ]]; then
    echo "expected one run dir for $1, found: ${found:-none}" >&2
    return 1
  fi
  echo "$found"
}

is_fresh() {
  local out="$1" summary
  [[ -f "$out" ]] || return 1
  for summary in "$2"/seed*/summary.json; do
    [[ "$out" -nt "$summary" ]] || return 1
  done
}

compare_slice() {
  local out="$COMPARE/$1.txt" candidate baseline
  candidate="$(run_dir "$2")" || return 0
  baseline="$(run_dir "$3")" || return 0
  shift 3
  local cmd=(env PYTHONPATH=src:../prediction-market-backtesting:scripts
    uv run --group backtest python scripts/compare_backtests.py "$baseline" "$candidate"
    --allow-drift "$@")
  if [[ -n "$DRY" ]]; then
    print_command "${cmd[@]}"
    return
  fi
  if is_fresh "$out" "$candidate"; then
    return
  fi
  if "${cmd[@]}" >"$out.tmp" 2>&1; then
    mv "$out.tmp" "$out"
    rm -f "$out.err"
  else
    mv "$out.tmp" "$out.err"
  fi
}

compare() {
  local name="$1__vs__$2"
  compare_slice "$name" "$1" "$2"
  compare_slice "${name}__grid_v1" "$1" "$2" --signal-mode grid_v1
  compare_slice "${name}__schedule_grid" "$1" "$2" --signal-mode schedule --feed-source grid
}

run_arm e1-base 0 ""
compare e1-base LIVE

run_arm e1-h20 0 "" --kill-post-table-hold-s 20
compare e1-h20 e1-base

run_arm e1-h10 0 "" --kill-post-table-hold-s 10
compare e1-h10 e1-base

run_arm e1-h20top2-stratz 0 "" --kill-post-table-hold-s 20 --kill-hold-top2 "$TOP2"
compare e1-h20top2-stratz e1-base
compare e1-h20top2-stratz e1-h20

# ONLY ON CONTROL MISMATCH:
#   run_arm e3-control 0 "" --model-dir "$MODELS/control-xp" --model-dir-noxp "$MODELS/control-noxp"
#   compare e3-control e1-base

run_arm e3-deaths 0 "" --model-dir "$MODELS/e3-deaths-xp" --model-dir-noxp "$MODELS/e3-deaths-noxp"
compare e3-deaths e1-base
# ONLY ON CONTROL MISMATCH: compare e3-deaths e3-control

run_arm e3-roles 0 "" --model-dir "$MODELS/e3-roles-xp" --model-dir-noxp "$MODELS/e3-roles-noxp"
compare e3-roles e1-base
compare e3-roles e3-deaths
# ONLY ON CONTROL MISMATCH: compare e3-roles e3-control

run_arm e2a-buy-h30 0 "$TEST" "${SHORT30[@]}" --short-veto-delta 0.005
compare e2a-buy-h30 e1-base

run_arm e2a-buy-h60 0 "$TEST" "${SHORT60[@]}" --short-veto-delta 0.005
compare e2a-buy-h60 e1-base

run_arm e2a-buysell-h30 0 "$TEST" "${SHORT30[@]}" --short-veto-delta 0.005 --short-sell-hold-delta 0.005
compare e2a-buysell-h30 e1-base
compare e2a-buysell-h30 e2a-buy-h30

run_arm e2a-buysell-h60 0 "$TEST" "${SHORT60[@]}" --short-veto-delta 0.005 --short-sell-hold-delta 0.005
compare e2a-buysell-h60 e1-base
compare e2a-buysell-h60 e2a-buy-h60

run_arm e2b-h30 0 "$TEST" "${E2B30[@]}"
compare e2b-h30 e1-base

run_arm e2b-h60 0 "$TEST" "${E2B60[@]}"
compare e2b-h60 e1-base

run_arm e1-base "1 2" ""
compare e1-base LIVE
```

The `# ONLY ON CONTROL MISMATCH` lines are plan markers. Do not copy them into the script. On a mismatch, add the real lines without comments, and add `control-{xp,noxp}` to the input loop.

I tested this exact text (planner scratch, not committed):

- `bash -n` passes.
- `DRY=1` from the worktree, with the four not-yet-trained `e3-*` catalogs removed from the input loop: exit 0, 14 `run_seeds.sh` lines, 51 compare lines, gates `0.001438 / 0.000919` and `0.005263 / 0.003768`, `TARGET=--since-match\ 8970886632`.
- A fake git repo on `exp-part2` with a stub `run_seeds.sh` (writes `seed<N>/summary.json`, always exits 1) and a stub `uv`. Run 1 with `e1-h10` failing: 13 status rows, `e1-h10` `fail`, 45 compare files, no `e1-h10` compares. Run 2: 12 `skip` lines, one new `e1-h10 ok` row, its 3 compares written, and no other compare file rewritten. `e1-h10.log` kept both attempts.

Behaviour notes:

- `TARGET=""` for full arms falls back to `--validation` inside `run_seeds.sh` (`${TARGET:---validation}`). `SEED_LIST=""` falls back to seed 0.
- Seeds 1 and 2 of `e1-base` reuse its `_archive` (DONE exists), so each takes about 12 min.
- An item's log is appended, not overwritten, so a retried arm keeps the first failure.
- `status.tsv` is append-only. A retried item adds a second row. STEP-007 reads the last row per item: 13 items, all `ok`.
- If `e1-base` fails, every compare against it lands in `.err`. The next start of `queue.sh` retries `e1-base` and those compares.
- Run dirs are relative to the worktree. `scripts/run_seeds.sh` `cd`s to the repo root itself.
- STEP-007 launches `nohup caffeinate -i bash $INV/work/queue.sh >> $INV/work/queue/queue.log 2>&1 &`. The shell opens that log before `queue.sh` runs, so `$INV/work/queue/` must exist. Phase 7 creates it.

### Phase 7 — final checks and progress

1. `bash -n "$INV/work/queue.sh"`.
2. From the worktree: `DRY=1 bash "$INV/work/queue.sh" > "$INV/work/queue/dry-run.txt"` (create `$INV/work/queue/` first). Expected: 14 `run_seeds.sh` lines (12 arms on seed 0, then `SEED_LIST=1` and `SEED_LIST=2` for `e1-base`) and 51 compare lines (3 + 33 + 12 + 3). With `e3-control`: 15 and 60. The two E2b lines carry `--min-abs-delta 0.001438 --exit-abs-delta 0.000919` and `0.005263 / 0.003768`. Every E2 line carries `TARGET=--since-match\ 8970886632`.
3. Inputs exist: `data/experiments/kill-top2/top2_deaths.parquet`, `data/new_model/experiments/e3-{deaths,roles}-{xp,noxp}`, `data/new_model/experiments/short-h{30,60}-{xp,noxp}`, `$INV/work/e2/gates.json`, `test_anchor.txt`. The script's own input loop checks them; DRY exits 0 only when all exist.
4. `git status --short` in the worktree: only untracked model dirs (`short-*`, `control-*`, `e3-*`). No other change. Head is the Phase 0 commit.
5. `data/backtests/dota_maker/` lists only `_logs`, `LIVE`, `LIVE_NOXP`. `_logs` is empty.
6. Append the STEP-006 entry to `tasks/killgate-short-roles/progress.txt`. Do not set `passes`.

The entry has:

- Phase 0 commit hash and test result.
- Control: match / MISMATCH per catalog, tree counts, and whether `e3-control` is in the queue.
- E3: per catalog `mae_gain_300_cents` [CI], `dir_300_cents`, trees min–max, identity, wall time. Research reference values from "Facts".
- Availability: total decisions, maps, `missing_roles`, `missing_deaths`, violation share overall and per cohort, maps with a violation, mean and max excess, wall time.
- No-change check: map, `engine_pnl`, fills, "equal to LIVE seed0".
- Smokes: per run `engine_pnl`, fills, manifest values.
- Hash: the run-1 and run-2 log lines, the two `kill_top2_sha256` values.
- Queue: DRY line counts, the anchor, g/x, expected queue time.
- Learnings: the `--since-match` archive fix; the compare and skip rules; `e1-base-seeds12` is one status row.

## Verification

| check | command | pass |
| --- | --- | --- |
| typecheck repo | `uv run --group backtest python -m basedpyright` | 0 errors |
| typecheck script | `uv run --group backtest python -m basedpyright "$INV/work/check_availability.py"` from the worktree (it uses the worktree `pyrightconfig.json`; if the file is outside its scope, use a scratch `pyrightconfig.json` with the same `extraPaths`, as in STEP-004) | 0 errors |
| lint script | `uv run ruff check "$INV/work/check_availability.py"` and `uv run ruff format --check …` | pass |
| unit tests | targeted pytest of Phase 0 | pass |
| shell | `bash -n queue.sh`; `DRY=1 bash queue.sh` | exit 0, line counts above |
| control | member SHA256 | recorded |
| E3 training | four catalogs, metrics | recorded |
| availability | `missing_roles` = `missing_deaths` = 0 | violation share recorded |
| no-change | `assert_frame_equal(..., check_dtype=False)` | equal |
| smokes | five runs, six maps total | exit 0, manifest keys |
| hash | two `run_seeds.sh` runs | log lines as above |
| cleanup | listings and `git status` | only expected leftovers |

## Expected run time

STEP-006 itself (heavy parts, strictly one at a time):

| part | estimate |
| --- | --- |
| Phase 0 tests | 2–5 min targeted, about 3 min more for `make test` |
| 6 × `make train` | 2–5 min each, 15–30 min total; peak 15–20 GB (PLAN §7), roles a few GB more |
| availability script | 5–15 min |
| no-change + 5 smokes | 1–3 min each, about 10–15 min |
| hash check | 2 runs × 2–4 min |
| total | about 45–75 min of compute |

Evidence for training: research XP and research no-XP were published 77 s apart on 2026-10-12, and that gap includes the XP production fit. Trees are 51–96 per member.

Queue (STEP-007), with Phase 0 in place:

| items | each | total |
| --- | --- | --- |
| 6 full arms (`e1-*`, `e3-*`) | archive 31 + seed 12 = 43 min | 258 min |
| 6 test arms (`e2a-*`, `e2b-*`) | archive 31 (all 358 archive maps are test maps) + seed 3–4 = about 35 min | about 210 min |
| `e1-base` seeds 1 and 2 | 12 min | 24 min |
| 51 compares | under 10 s | under 10 min |
| total | | about 8.3–8.5 h |

Add 43 min if `e3-control` joins. Without Phase 0 each test arm takes about 64 min, and the queue takes about 11.3 h.

## Risks

- **Phase 0 changes `src/backtest/run.py`.** STEP-006 did not list it. It is needed for the hash check, for 3 h of queue time, and for comparable STEP-007 seeds 1–2 on E2 arms. If the orchestrator rejects it, the hash check needs `TARGET=--validation` (a 43-min run), and STEP-007 must not run seeds 1–2 for E2 arms.
- **Control mismatch.** Datasets and code path are the same, and fits are single-threaded, so I expect a match. A mismatch adds one arm (43 min) and two pairs. A different `lightgbm` build in the worktree `.venv` would also cause a mismatch.
- **`check_availability.py` replaces a module attribute** (`backtest.signals.replay_received_snapshots`). It is investigation code. It breaks if `backtest.signals` stops calling the function by that module-level name.
- **E2b gates are XP gates.** On 97 Oddin maps the no-XP short model gets the XP g/x (STEP-004 decision). The no-XP quantiles are smaller (h30 0.000796 / 0.000535), so E2b opens less often on Oddin maps.
- **Resume granularity.** A crash restarts the unfinished stage of the current arm: up to 31 min of archive work.
- **Mac sleep.** `caffeinate -i` blocks idle sleep, not lid-close sleep. The lid stays open and the power stays connected (PLAN §9.7).
- **E3 memory.** `roles.parquet` adds 47 columns to 2.35M validation rows and to every member worker's holdout copy. If a training run swaps or is killed, record it; do not run two trainings at once.
- **`e1-base` vs LIVE.** The `schedule` slice must be identical. The `grid_v1` slice differs because of fix A. The three known grid-v1 golden mismatches are pre-existing.

## Out of scope

Running the queue, STEP-007 confirmations and summary, `report.md`, live code, `main`, any fix of availability violations.
