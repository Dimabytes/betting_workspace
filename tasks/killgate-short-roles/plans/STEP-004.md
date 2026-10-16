# STEP-004 plan — E2: labels and short models 30/60 s

Worktree `../esports-trader-exp`, branch `exp-part2`, head `3518f50d`. Paths are relative to the worktree unless they start with `$INV` or `betting_workspace`.

`$INV` = `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-13-killgate-short-roles` (exists, gitignored in the workspace repo).

No Figma links: this step has no UI.

## Goal

1. `data/experiments/short/validation_labels.parquet`: the mid 60 s after every validation row, column `signal_market_p_radiant_60s`.
2. Four short models, one at a time: `data/new_model/experiments/short-h30-xp`, `short-h30-noxp`, `short-h60-xp`, `short-h60-noxp`. Train on the 451 validation maps that start before D = 1787942975. Each dir also holds `test_anchor.txt`.
3. `$INV/work/e2/eval.md` (F1–F3, |pred| quantiles, zero-label share) and `$INV/work/e2/gates.json` (g and x for E2b).
4. One backtest smoke on one test map with the short-h30 pair as the main model.
5. The repo does not change. All code is three scripts in `$INV/work/`. There is no commit in this step.

## Facts I verified

Read-only probes in the worktree. Probe scripts are in `orchestrate/work/plan4/`.

Validation dataset (`data/new_processed/dataset/validation_dataset.parquet`):

- 2 347 459 rows, 903 maps. `second` starts at −50. No gaps in `second` per map. No duplicate `(match_id, second)` or `(match_id, state_ts_us)` keys.
- **The self-join on `state_ts_us + 30e6` is not equal to `signal_market_p_radiant_30s`.** 22 241 rows (0.95 %, 749 maps) differ. In every one of them, the join gives NaN and the stored label has a value. 4 264 of them are in the last 30 s of a map. The rest are anchors whose target falls inside a pause: `get_state_available_ts` (`src/shared/utils/match_time.py:62-77`) skips paused wall-clock time, so no row has that `state_ts_us`. 19 415 of the 22 241 are `ok` rows.
- `build_market_second_rows` (`src/market_data/build_market_data.py:102-149`) rebuilt two validation maps from the raw Telonex books. `state_ts_us`, `market_p_radiant`, `signal_market_p_radiant_30s`, and `signal_market_p_radiant_300s` were equal to the dataset bit for bit. Each map took about 1.85 s.
- `market_p_radiant` on a row and `lookup_market_p_after` (`src/shared/utils/telonex_book.py:248-259`) run the same `resolve_market_pair(find_asof_quote(...))` on the same target. A self-join hit is therefore equal to the lookup by construction.
- Maps before D: 451. Maps at or after D: 452. The first test map is `8970886632`, start `1787942975`. Catalog `start_time` equals the dataset `start_time` on all 903 maps. The catalog has 455 maps at or after D; 452 of them are validation maps. `--since-match` selects on catalog `start_time` (`src/backtest/selection.py:122-139`).
- Early-stop split by start time: `int(451 · 0.8)` = 360 fit maps, 91 early-stop maps. The first early-stop map is `8929124210` (start `1785854132`).
- Pre-D `ok` rows: 938 834. With a 30 s label: 832 598. With a 60 s label (join estimate): 800 396. Pre-D `ok` rows with `second < 480`: 211 228.
- Labels are not zero-inflated. Pre-D exact-zero share: 30 s 16.4 %, 60 s 9.1 %, 300 s 2.0 %. Median |Δ|: 1.5¢, 2.0¢, 7.0¢. Test maps match (16.8 %, 9.1 %, 2.0 %).

Code (`3518f50d`; `feature.json` line numbers drifted):

- The trainer builds validation features at `src/train_model/train_model.py:232-240`: `attach_catalog_features(frame, game_features tape, key_seconds=second − lag, start_second=history_policy.start_second, board_tape=tape if board else None)`. `lagged_source_features` (`:87-91`) then sets `second = second − lag`. `feature.json` cites `:215-222`.
- The feature lists contain `second` and `market_p_radiant` (`src/shared/utils/dota_features.py:99-127`). `--no-xp` uses `DOTA_NOXP_FEATURE_COLUMNS` (`train_model.py:431`).
- Row filters to reuse: `select_usable_validation_rows` (`train_model.py:110-113`: `second ≥ −60`, status `ok`) and `select_validation_prediction_frame` (`:116-119`: plus `second < BUY_CUTOFF_SECOND`). `load_validation_dataset` is at `:99-101`.
- `ResearchMemberFit` (`src/shared/utils/gbm.py:235-250`) uses `train[features]` and `build_price_delta_labels(train)` (`:137-141`). `fit_research_members` (`:304-311`) runs 10 members in `spawn` processes (`:290-301`). The worker unpickles the fit object.
- `build_ensemble_model_meta` (`gbm.py:323-350`) accepts `metrics=None`. The backtest reads only `model.json` (`name`, `features`, `source_lag_seconds`), the members, and `extra_features.parquet` from a model dir (`src/backtest/signals.py:299, 324`, `src/backtest/run.py:816, 853`, `src/backtest/replay_inputs.py:186`). No `split.parquet` or metrics are needed.
- `staging_model_dir` / `publish_model_dir` are at `src/shared/utils/model_registry.py:34-62`. An extra file in staging ships with the model.
- The backtest uses the no-XP catalog only on the Oddin feed (`src/backtest/feed_schedules.py:205`). All other maps use the XP catalog.
- `exit_abs_delta` is the hysteresis floor of the open gate (`src/strategy/signals.py:63-65`).
- Constants: `BUY_CUTOFF_SECOND = 480`, `MIN_ABS_DELTA = 0.02`, `EXIT_ABS_DELTA = 0.015`, `MIN_ENTRY_PRICE = 0.45`, `MAX_ENTRY_PRICE = 0.85` (`src/shared/constants/strategy.py`).
- Research members have 64–96 trees (XP) and 51–76 trees (no-XP).
- Huber precedent: `docs/experiments/huber-monotone.md` used `objective=huber`, α = 0.02. `data/new_model/experiments/huber/model.txt` has `[alpha: 0.02]`.
- `data/new_model/experiments/*` is not gitignored (`.gitignore:8`). New model dirs show as untracked.
- `bootstrap_series_cluster_ci` (`src/shared/utils/market_scenario_report.py:157-193`) takes one `SeriesMetricTotals(numerator_sum, row_count)` per cluster.
- Scripts run with `make run F=<abs path>` (`Makefile:69-70`, `PYTHONPATH=src`). Python puts the script's own dir on `sys.path`, so `eval_short.py` can import `train_short`.

## Decisions

1. **Labels come from the Telonex books, for every row.** The self-join misses pauses and map tails (verified above). `label_60s.py` rebuilds each map's two books with the same window as `build_market_second_rows`, then calls `lookup_market_p_after(..., 60)`. The 30 s assert becomes "the same lookup at 30 s equals the stored `signal_market_p_radiant_30s` bit for bit on all rows". The self-join stays as a cross-check: where it finds a row, it must equal the lookup. This is the fallback that `feature.json` names. It also gives the 60 s label the same wall-clock meaning as the stored 30 s and 300 s labels, and as `short_expiry_ns = signal_ts + h` in STEP-005.
2. **One path, no hybrid.** 749 of 903 maps have at least one miss, so a hybrid still loads almost every book. The full rebuild takes about 4 min on 8 workers.
3. **Training rows are validation rows only**, with the trainer's validation feature path. Fit rows and early-stop rows both go through `attach_catalog_features(key_seconds=second − 10)`, then `second −= 10`. The minute training dataset is not used.
4. **Rows:** `select_usable_validation_rows` and a non-null h label, whole map. This is the research validation-fit rule with the h label instead of the 300 s label.
5. **Objective stays `regression_l1` (via `fit_research_members`).** Labels are not zero-inflated (16 % / 9 % zeros, median 1.5¢ / 2.0¢), so a collapse is not likely. The huber retrain is written only if `eval_short.py` shows the trigger. Trigger: share of `|pred_h| < 0.001` on test rows with `second < 480` ≥ 0.95. The snippet is in "Contingency: huber".
6. **`model.json`:** `train_matches` = fit maps (360). `train_dataset_sha256` and `validation_dataset_sha256` = SHA256 of `validation_dataset.parquet`. `metrics=None` (300 s metrics do not fit). The 60 s labels SHA goes into `progress.txt`. Members embed the objective, and `model_identity_sha256` hashes the members.
7. **`test_anchor.txt` goes into staging before publish** in each of the four dirs. Content: `8970886632\n`.
8. **Eval rows:** `select_usable_validation_rows`, all 903 maps (pre-D for the F2 `c`, test for everything else). "M < 480" = `select_validation_prediction_frame`: `−60 ≤ second < 480`. This is the research metrics window (`esports-trader/AGENTS.md`).
9. **Eval for both catalogs.** XP: short-h-xp vs `data/new_model/research`. No-XP: short-h-noxp vs `research-noxp`. `gates.json` uses the XP pair only. The backtest takes one `--min-abs-delta` / `--exit-abs-delta` for both catalogs, and only Oddin maps use no-XP. `eval.md` also shows the no-XP matched values.
10. **CI clusters are maps (`match_id`)**, as `feature.json` says ("по матчам").
11. **F2 `c`:** grid `0.00…1.00` step 0.01, minimum MAE of `y_h − c·pred300` on pre-D rows (whole map), one `c` per (catalog, h).
12. **F3 token price is the mid:** `market_p_radiant` for side +1 and `1 − market_p_radiant` for side −1. The strategy may check another price; F3 is for the report only.
13. **Gates:** on test `ok` rows with `−60 ≤ second < 480`. `share_g = mean(|pred300| ≥ 0.02)`, `g = quantile(|pred_h|, 1 − share_g)`. `share_x = mean(|pred300| ≥ 0.015)`, `x = quantile(|pred_h|, 1 − share_x)`. The match uses predictions only, never labels. Assert `g > x > 0`. Values are rounded to 6 decimals.
14. **No repo change, no commit, no unit tests.** The scripts carry their own asserts. Model dirs stay untracked. Do not commit them.

## Implementation

Write no comments and no docstrings (review rule for this task). Use named intermediate variables and verb function names (AGENTS). Every script ends with `if __name__ == "__main__": main()`. The `spawn` workers in `label_60s.py` and in `fit_research_members` re-import the script, so top-level code must not run work.

### 1. `$INV/work/label_60s.py`

```python
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from market_data.build_market_data import MarketCacheJob, get_state_us, load_market_data_sources
from shared.constants.dataset import (
    DOTA_PRIOR_ANCHOR_SECOND,
    DOTA_PRIOR_WINDOW_SECONDS,
    MODEL_TARGET_HORIZON_SECONDS,
)
from shared.constants.paths import (
    DATA_DIR,
    MATCH_CATALOG_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.hashing import sha256_file
from shared.utils.parquet_io import write_parquet
from shared.utils.telonex_book import US_PER_SECOND, load_token_book, lookup_market_p_after

LABELS_PATH = DATA_DIR / "experiments" / "short" / "validation_labels.parquet"
LABEL_60S = "signal_market_p_radiant_60s"
WORKERS = 8


def lookup_future_prices(job: MarketCacheJob, rows: pd.DataFrame) -> pd.DataFrame:
    prior_us = get_state_us(job, DOTA_PRIOR_ANCHOR_SECOND)
    start_us = prior_us - DOTA_PRIOR_WINDOW_SECONDS * US_PER_SECOND
    end_us = get_state_us(job, job.duration_seconds) + MODEL_TARGET_HORIZON_SECONDS * US_PER_SECOND
    books = [
        load_token_book(
            token_id=token_id, start_us=start_us, end_us=end_us, telonex_root=job.telonex_root
        )
        for token_id in job.token_ids
    ]
    radiant_book = books[job.radiant_token_index]
    dire_book = books[1 - job.radiant_token_index]
    if radiant_book is None or dire_book is None:
        raise ValueError(f"match {job.match_id}: no Telonex book")
    anchors = rows["state_ts_us"].tolist()
    prices_30s = [lookup_market_p_after(radiant_book, dire_book, anchor, 30) for anchor in anchors]
    prices_60s = [lookup_market_p_after(radiant_book, dire_book, anchor, 60) for anchor in anchors]
    return rows[["match_id", "second"]].assign(
        rebuilt_30s=np.asarray(prices_30s, dtype=np.float64),
        **{LABEL_60S: np.asarray(prices_60s, dtype=np.float64)},
    )
```

`main`:

1. Read `VALIDATION_DATASET_PATH` columns `match_id, second, state_ts_us, market_status, market_p_radiant, signal_market_p_radiant_30s`.
2. `jobs = {job.match_id: job for job in load_market_data_sources(MATCH_CATALOG_PATH, RAW_TELONEX_POLYMARKET_DIR)}`.
3. Group rows by `match_id`. `ProcessPoolExecutor(max_workers=WORKERS).map(lookup_future_prices, job_list, row_groups)`. Concat, sort by `(match_id, second)`.
4. Merge onto the validation rows (`how="left", validate="one_to_one"`). Row count must stay 2 347 459.
5. Assert 1: `np.array_equal(rebuilt_30s, signal_market_p_radiant_30s, equal_nan=True)`. If it fails, stop. It means the books changed since the dataset build.
6. Assert 2 (self-join cross-check): merge `(match_id, state_ts_us + 60e6)` onto `(match_id, state_ts_us, market_p_radiant)` with `indicator=True`. On rows with `_merge == "both"`, `np.array_equal(join_price, label_60s, equal_nan=True)`. Print the found share and, for reference, the +30 s join miss count (22 241 expected).
7. Print the NaN share of `signal_market_p_radiant_60s` on all rows, on `ok` rows, and on `ok` rows with `second < 480`, overall and split before / after D.
8. `write_parquet(labels[["match_id", "second", LABEL_60S]], LABELS_PATH)`. Print rows and `sha256_file(LABELS_PATH)`.

### 2. `$INV/work/train_short.py`

```python
import argparse
import sys
from collections.abc import Sequence

import numpy as np
import pandas as pd

from shared.constants.paths import (
    DATA_DIR,
    GAME_FEATURES_DATASET_PATH,
    NEW_MODEL_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.dota_features import (
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
    attach_catalog_features,
    catalog_has_board,
    history_policy_for_columns,
)
from shared.utils.gbm import (
    build_ensemble_model_meta,
    build_price_delta_labels,
    fit_research_members,
    model_identity_sha256,
    write_ensemble_members,
)
from shared.utils.hashing import sha256_file
from shared.utils.model_registry import publish_model_dir, staging_model_dir, write_model_meta
from train_model.train_model import (
    lagged_source_features,
    load_validation_dataset,
    select_usable_validation_rows,
)

TEST_START_TIME = 1_787_942_975
LAG_SECONDS = 10
EARLY_STOP_SHARE = 0.2
LABELS_PATH = DATA_DIR / "experiments" / "short" / "validation_labels.parquet"
SHORT_MODELS_DIR = NEW_MODEL_DIR / "experiments"
TEST_ANCHOR_FILENAME = "test_anchor.txt"


def name_future_price_column(horizon: int) -> str:
    return f"signal_market_p_radiant_{horizon}s"


def name_short_model_dir(horizon: int, no_xp: bool) -> str:
    return f"short-h{horizon}-{'noxp' if no_xp else 'xp'}"


def load_labelled_rows() -> pd.DataFrame:
    validation = load_validation_dataset(VALIDATION_DATASET_PATH)
    labels = pd.read_parquet(LABELS_PATH)
    merged = validation.merge(labels, on=["match_id", "second"], how="left", validate="one_to_one")
    return select_usable_validation_rows(merged)


def attach_short_features(rows: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
    second_tape = pd.read_parquet(GAME_FEATURES_DATASET_PATH)
    board_tape = second_tape if catalog_has_board(features) else None
    return attach_catalog_features(
        rows,
        second_tape,
        key_seconds=rows["second"] - LAG_SECONDS,
        start_second=history_policy_for_columns(features).start_second,
        board_tape=board_tape,
    )


def find_test_anchor(rows: pd.DataFrame) -> int:
    test_maps = rows.loc[rows["start_time"] >= TEST_START_TIME, ["match_id", "start_time"]]
    ordered = test_maps.drop_duplicates("match_id").sort_values(["start_time", "match_id"])
    return int(ordered["match_id"].iloc[0])


def check_delta_target(rows: pd.DataFrame, label_column: str) -> None:
    sample = rows.sample(n=100, random_state=0)
    future = sample[label_column].to_numpy(dtype=np.float64)
    current = sample["market_p_radiant"].to_numpy(dtype=np.float64)
    if not np.array_equal(build_price_delta_labels(sample), future - current):
        raise ValueError("training target is not the future price minus the current price")
```

`main(argv)`:

1. `--horizon` (`type=int`, `choices=(30, 60)`, required), `--no-xp` (`store_true`).
2. `features = list(DOTA_NOXP_FEATURE_COLUMNS if no_xp else DOTA_XP_FEATURE_COLUMNS)`. `label_column = name_future_price_column(horizon)`.
3. `rows = load_labelled_rows()`. `anchor = find_test_anchor(rows)`.
4. `train_rows = rows.loc[(rows["start_time"] < TEST_START_TIME) & rows[label_column].notna()]`.
5. `train_rows = attach_short_features(train_rows, features).assign(signal_market_p_radiant_300s=lambda frame: frame[label_column])`. The 300 s column now holds the future price mid(M + h), not Δ.
6. `check_delta_target(train_rows, label_column)`.
7. Map split: unique `(match_id, start_time)` sorted by `(start_time, match_id)`. `fit_count = int(len(maps) * (1 - EARLY_STOP_SHARE))`. The first `fit_count` maps fit; the rest early-stop. Print both counts (expected 360 / 91) and both row counts.
8. `fit_frame = lagged_source_features(fit_rows, LAG_SECONDS, features).assign(match_id=fit_rows["match_id"], signal_market_p_radiant_300s=fit_rows["signal_market_p_radiant_300s"])`. Keep only these columns: the pickle goes to 10 workers.
9. `valid_X = lagged_source_features(stop_rows, LAG_SECONDS, features)`, `valid_y = build_price_delta_labels(stop_rows)`.
10. `boosters = fit_research_members(fit_frame, valid_X, valid_y, features)`.
11. Publish to `model_dir = SHORT_MODELS_DIR / name_short_model_dir(horizon, no_xp)`:
    - `staging_dir = staging_model_dir(model_dir)`; `member_names = write_ensemble_members(staging_dir, boosters)`; `member_trees = tuple(booster.num_trees() for booster in boosters)`;
    - `dataset_sha256 = sha256_file(VALIDATION_DATASET_PATH)`;
    - `write_model_meta(build_ensemble_model_meta(fit_count, dataset_sha256, features=features, validation_dataset_sha256=dataset_sha256, metrics=None, members=member_names, member_trees=member_trees, source_lag_seconds=LAG_SECONDS), staging_dir / "model.json")`;
    - `(staging_dir / TEST_ANCHOR_FILENAME).write_text(f"{anchor}\n")`;
    - `publish_model_dir(staging_dir, model_dir, SHORT_MODELS_DIR / "archive")`.
12. Print member trees (min, max, mean), `model_identity_sha256(model_dir)`, and the anchor.

### 3. `$INV/work/eval_short.py`

```python
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from shared.constants.paths import RESEARCH_MODEL_DIR, RESEARCH_NOXP_MODEL_DIR
from shared.constants.strategy import (
    BUY_CUTOFF_SECOND,
    EXIT_ABS_DELTA,
    MAX_ENTRY_PRICE,
    MIN_ABS_DELTA,
    MIN_ENTRY_PRICE,
)
from shared.utils.dota_features import DOTA_NOXP_FEATURE_COLUMNS, DOTA_XP_FEATURE_COLUMNS
from shared.utils.gbm import load_predictor
from shared.utils.market_scenario_report import SeriesMetricTotals, bootstrap_series_cluster_ci
from train_model.train_model import lagged_source_features
from train_short import (
    LAG_SECONDS,
    SHORT_MODELS_DIR,
    TEST_START_TIME,
    attach_short_features,
    load_labelled_rows,
    name_future_price_column,
    name_short_model_dir,
)

HORIZONS = (30, 60)
SHORT_VETO_DELTA = 0.005
NEAR_ZERO_PRED = 0.001
HUBER_TRIGGER_SHARE = 0.95
C_GRID = np.round(np.arange(0.0, 1.0001, 0.01), 2)
PRED_QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
OUT_DIR = Path(__file__).resolve().parent / "e2"


@dataclass(frozen=True)
class Catalog:
    name: str
    features: tuple[str, ...]
    research_dir: Path
    no_xp: bool


@dataclass(frozen=True)
class MetricSummary:
    mean: float
    low: float
    high: float
    rows: int


def summarize_by_match(
    values: npt.NDArray[np.float64], match_ids: npt.NDArray[np.int64]
) -> MetricSummary:
    totals = pd.DataFrame({"match_id": match_ids, "value": values}).groupby("match_id")["value"]
    series = [
        SeriesMetricTotals(numerator_sum=float(total), row_count=int(count))
        for total, count in zip(totals.sum(), totals.size(), strict=True)
    ]
    interval = bootstrap_series_cluster_ci(series)
    if interval is None:
        return MetricSummary(mean=float("nan"), low=float("nan"), high=float("nan"), rows=0)
    return MetricSummary(
        mean=float(values.mean()), low=interval.low, high=interval.high, rows=len(values)
    )
```

`interval is None` happens only on an empty cell (for example, no closed F3 seconds on a collapsed model). That is a real input here, so the branch stays.

`main`:

1. `rows = load_labelled_rows()`. Arrays: `price = market_p_radiant`, `match_ids`, `is_test = start_time ≥ TEST_START_TIME`, `is_early = second < BUY_CUTOFF_SECOND`, `y300 = signal_market_p_radiant_300s − price`, `y_h = name_future_price_column(h) − price`.
2. For each `Catalog` (`xp` with `DOTA_XP_FEATURE_COLUMNS` and `RESEARCH_MODEL_DIR`; `noxp` with `DOTA_NOXP_FEATURE_COLUMNS` and `RESEARCH_NOXP_MODEL_DIR`):
   - `features_frame = lagged_source_features(attach_short_features(rows, features), LAG_SECONDS, features)`;
   - `pred300 = load_predictor(research_dir).predict(features_frame)`;
   - per h: `pred_h = load_predictor(SHORT_MODELS_DIR / name_short_model_dir(h, no_xp)).predict(features_frame)`.
3. Per (catalog, h), windows `all` (test) and `M<480` (test and `is_early`):
   - **F1:** rows with a label. `gain_i = |y_h| − |y_h − pred_h|`. Report MAE no-move, MAE model, gain, CI (`summarize_by_match`), all in ¢.
   - **F2:** `c` = argmin over `C_GRID` of `mean|y_h − c·pred300|` on pre-D rows with a label. Test `gain_i = |y_h − c·pred300| − |y_h − pred_h|`. Report `c`, gain, CI.
   - **F3:** test, `is_early`, `|pred300| ≥ MIN_ABS_DELTA`, `side = sign(pred300)`, token mid in `[MIN_ENTRY_PRICE, MAX_ENTRY_PRICE]`. `closed = side·pred_h < −SHORT_VETO_DELTA`. Report the closed share (over all such seconds), and `summarize_by_match` of `side·y_h` and of `side·y300` in closed and in open seconds (rows with that label).
   - **|pred_h| quantiles** `PRED_QUANTILES` in ¢ on test, both windows. Share of `|pred_h| < NEAR_ZERO_PRED` on test `M<480`. If it is ≥ `HUBER_TRIGGER_SHARE`, print `HUBER RETRAIN NEEDED <model>`.
   - **Zero-label share:** `mean(y_h == 0)` on test rows with a label, both windows.
   - **Gates** (Decision 13) on test `M<480` rows.
4. Write `OUT_DIR / "eval.md"`: one table each for F1, F2, F3, quantiles + zero share, gates. Header: model dirs, member trees, test maps / rows, the anchor, the D boundary.
5. Write `OUT_DIR / "gates.json"` from the XP catalog:

```json
{
  "catalog": "xp",
  "window": "test maps, ok rows, -60 <= second < 480",
  "research_open_share": {"min_abs_delta": 0.0, "exit_abs_delta": 0.0},
  "h30": {"min_abs_delta": 0.0, "exit_abs_delta": 0.0},
  "h60": {"min_abs_delta": 0.0, "exit_abs_delta": 0.0}
}
```

STEP-006 `queue.sh` reads `h30.min_abs_delta` → `--min-abs-delta`, `h30.exit_abs_delta` → `--exit-abs-delta`. Assert `g > x > 0` per h before the write.

6. Print the progress lines: F1 gain and CI per model and window, `c` and F2 gain, F3 closed share and the four means, |pred| p50 / p90, near-zero share, g / x.

### Contingency: huber

Do this only if `eval_short.py` printed `HUBER RETRAIN NEEDED` for a model.

1. Add `--objective` (`choices=("regression_l1", "huber")`, default `regression_l1`) to `train_short.py`.
2. For `huber`, fit through this subclass instead of `fit_research_members`. It keeps the member RNG, the map subsample, and the member order:

```python
from dataclasses import dataclass

import lightgbm as lgb

from shared.utils.gbm import (
    EARLY_STOP_PATIENCE,
    MAX_BOOST_ROUNDS,
    ResearchMemberFit,
    _fit_ensemble_members,
    ensemble_member_params,
)

HUBER_ALPHA = 0.02


@dataclass(frozen=True, eq=False)
class HuberMemberFit(ResearchMemberFit):
    def __call__(self, train: pd.DataFrame) -> lgb.Booster:
        params = {**ensemble_member_params(), "objective": "huber", "alpha": HUBER_ALPHA}
        train_data = lgb.Dataset(train[list(self.features)], build_price_delta_labels(train))
        validation_data = lgb.Dataset(self.valid_X, self.valid_y)
        return lgb.train(
            params,
            train_data,
            num_boost_round=MAX_BOOST_ROUNDS,
            valid_sets=[validation_data],
            valid_names=["validation"],
            callbacks=[lgb.early_stopping(EARLY_STOP_PATIENCE, verbose=False)],
        )
```

   - Call: `_fit_ensemble_members(fit_frame, HuberMemberFit(valid_X, valid_y, tuple(features)))`.
   - Early stopping stays on `metric: l1` from `LGB_PARAMS`, so it stops on MAE, like F1.
   - The class lives in `__main__`. Under `spawn` the worker imports the script as `__mp_main__`, and unpickling works because of the `main()` guard.
   - α = 0.02 is the repo precedent. On these labels (median |Δ| 1.5–2¢) huber acts close to L2, so the model predicts a mean, not a median. That removes the collapse.
3. Retrain only the flagged model (`publish_model_dir` archives the L1 one into `data/new_model/experiments/archive/`). Check `grep -c "objective=huber" member_00.txt` = 1. Re-run `eval_short.py`. Write the decision into `progress.txt`.

## Run order

From the worktree. Before each heavy step, run `pgrep -fl "backtest.run|run_seeds|train_model|train_short"`. If a backtest or another training runs, wait. Do not stop processes you did not start.

1. `make run F=$INV/work/label_60s.py`. About 4–5 min, 8 workers.
2. `make run F=$INV/work/train_short.py ARGS="--horizon 30"`, then `ARGS="--horizon 30 --no-xp"`, then `ARGS="--horizon 60"`, then `ARGS="--horizon 60 --no-xp"`. Strictly one at a time. My estimate is 5–20 min each (about 660k fit rows, single-thread members, early stopping at patience 100). I did not measure it.
3. `make run F=$INV/work/eval_short.py`. About 5–10 min.
4. If the huber trigger fired: the contingency, then step 3 again.
5. Smoke, from the worktree:

   ```bash
   PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run \
     --game dota --match-id 9040578015 --name short-smoke \
     --model-dir data/new_model/experiments/short-h30-xp \
     --model-dir-noxp data/new_model/experiments/short-h30-noxp
   ```

   - List `data/backtests/dota_maker/` and `data/backtests/dota_maker/_logs/` before and after the run, into your own scratch dir. Delete only the new entries (expected `data/backtests/dota_maker/match_9040578015_short-smoke`).
   - `9040578015` is a test map (start `1791744342`) and the STEP-001 smoke map.
   - Success: exit 0, `summary.json` exists, manifest `model_sha256` equals `model_identity_sha256(data/new_model/experiments/short-h30-xp)`. Record `engine_pnl`, BUY and SELL fills. Zero fills is acceptable: the default gate is 2¢ and short predictions are smaller.
   - If `uv run` without `--group backtest` removed backtest packages, run `uv sync --group backtest --inexact` once.
6. Append the `progress.txt` entry (see "Done when").

## Hard parts and risks

- **Self-join labels are wrong on 0.95 % of rows** (pauses and map tails). The plan does not use the join for labels (Decision 1). If assert 1 fails, the raw books differ from the dataset build. Stop and report. Do not run `make market-data` or `make prepare` in the worktree (`PLAN.md` rule 8).
- **L1 can still predict near zero.** Short-horizon mids are close to a martingale, so the conditional median can be small even with only 16 % exact zeros. The trigger and the huber path are defined above. Huber with α = 0.02 is close to L2 here.
- **Training memory.** `fit_frame` is about 660k rows × 83 columns float64 (about 440 MB). Each of 10 `spawn` workers holds a copy, the grouped copy, and its member frame. I estimate a peak of 12–17 GB. Never overlap with a backtest or with another training.
- **E2b gates use test-map predictions.** The match uses the |pred| distribution only, no labels, as `feature.json` says. Pre-D maps are in-sample for the short model, so their |pred| scale would be wrong for the match.
- **The exit gate is hysteresis.** Matching `|pred| ≥ x` share alone approximates the real open time. The LoL precedent used the same simple match.
- **Research early-stopped on all 903 validation maps**, test maps included. Research-side numbers in F2 and F3 are slightly optimistic. The backtest has the same property.
- **One g / x for both catalogs.** The XP pair sets them. No-XP maps (Oddin feed only) get XP-matched gates.
- **Wall-clock horizons.** The 30 s and 60 s labels, the 300 s label, and STEP-005 `short_expiry_ns = signal_ts + h·10^9` are all wall-clock. A label whose window holds a pause compares prices 60 s of wall clock apart while the game clock stands still. This matches how the trader acts.
- **Untracked dirs.** `data/new_model/experiments/short-*` and `data/new_model/experiments/archive/` are not gitignored. Do not `git add` them. Do not `git add -A`. A crash can leave `data/new_model/experiments/.next-short-*`. The next run of the same model removes it.
- **Series across D.** A Bo3 can have maps on both sides of D. The correlation is small. I do not handle it.

## Verification

1. `label_60s.py`: assert 1 passes on all 2 347 459 rows. Assert 2 passes. NaN shares and the labels SHA are printed.
2. `train_short.py`, each of the four runs:
   - the 100-row target check passes;
   - fit / early-stop maps are 360 / 91 (if a pre-D map has no labelled row, the counts drop; record them);
   - the model dir holds `model.json`, `member_00.txt` … `member_09.txt`, `test_anchor.txt` = `8970886632`;
   - `model.json` `features` equal the canonical XP or no-XP list, `source_lag_seconds` = 10;
   - `model_identity_sha256` printed.
3. `eval_short.py`: `eval.md` and `gates.json` exist. `g > x > 0` for h30 and h60. The huber trigger was checked for all four models.
4. Smoke as in step 5 of "Run order". The smoke dir is deleted.
5. Typecheck: `uv run python -m basedpyright` in the worktree has 0 errors. The repo did not change, so this confirms the base. For the scripts: `uv run ruff check --config pyproject.toml $INV/work/label_60s.py $INV/work/train_short.py $INV/work/eval_short.py` and `uv run ruff format --check --config pyproject.toml` on the same files pass. Optional: `uv run python -m basedpyright <the three scripts>`; fix real errors, record pandas unknown-type noise.
6. `git -C ../esports-trader-exp status --short`: no tracked change. Only untracked dirs under `data/new_model/experiments/`.
7. Unit tests: none. No repo code changed. If you change repo code anyway, stop: that is outside this step.

## Done when

`progress.txt` gets one entry with:

- labels: rows, NaN share (all / `ok` / `ok` and `second < 480`, before and after D), the +30 s join miss count, assert results, SHA256 of `validation_labels.parquet`;
- per model: fit / early-stop maps and rows, member trees (min–max, mean), `model_identity_sha256`, objective;
- F1 gain and CI (test, whole map and M < 480), `c` and the F2 gain, F3 closed share and the four means with CI, |pred| p50 / p90, near-zero share, zero-label share;
- g and x for h30 and h60 and the research open shares;
- the huber decision (not needed / retrained which model);
- smoke: map, exit code, `engine_pnl`, fills, `model_sha256` match, smoke dir deleted;
- typecheck result.

`passes` stays false. The orchestrator sets it after review.
