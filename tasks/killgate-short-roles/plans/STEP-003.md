# STEP-003 plan — E3: extra-feature channel, roles, death tables

Worktree `../esports-trader-exp`, branch `exp-part2`, base `9786f9a2`. Paths are relative to the worktree unless they start with `$INV` or `betting_workspace`.

`$INV` = `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-13-killgate-short-roles` (exists, gitignored in the workspace repo; `work/build_kill_top2.py` is already there).

`$R1` = `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-13-role-features` (part 1, read only).

No Figma links: this step has no UI.

## Goal

1. The 300 s model can train with extra columns from one table: `make train ARGS="... --extra-features PATH"`. The model dir keeps a copy as `extra_features.parquet`.
2. The backtest joins that copy onto every model decision by `(match_id, decision game second)`. No new backtest flag: the model dir carries the table.
3. `model_identity_sha256` covers the table. Old catalogs keep their hash.
4. `data/experiments/roles/assigned_positions.parquet`: positions 1–5 per player for every training and validation map, from strictly earlier history.
5. `data/experiments/e3/deaths.parquet` (4 columns) and `data/experiments/e3/roles.parquet` (47 columns), with self-checks.
6. This step does not train models and does not run backtests. STEP-006 does both.

## Facts I verified

Code (line numbers are on `9786f9a2`; some moved since `feature.json` was written):

- `history_policy_for_columns` is at `src/shared/utils/dota_features.py:135-142`. It accepts only the exact XP or no-XP list. Callers: `src/backtest/signals.py:301` (`history_policy_for_model_dir`), `src/train_model/train_model.py:204, 321`, `src/trader/game_profile.py:40`.
- `require_catalog_features` is at `src/backtest/signals.py:282-285`. Its only caller is `_pin_cli_model_dir` (`src/backtest/run.py:1381-1402`), for `--model-dir` / `--model-dir-noxp`.
- `_assemble_match_signals` is at `src/backtest/signals.py:554-608`. It builds one row per `ModelDecision` and predicts per model dir with `decision_rows.iloc[positions][list(columns)]`. The row's `second` is `decision.snapshot.second` (`:565`).
  - grid-v1: `snapshot.second = validation second − lag_seconds` (`:653`). Rows are filtered to `second − lag ≥ 48` (`:629`).
  - archive maps: `snapshot.second = tick.game_second` (`:714`). A decision needs `has_features`, which needs a `game_features.parquet` row at that second (`:715`, `src/backtest/signal_replay.py:163`).
- Trainer: train rows join history by `second` (`train_model.py:210`); validation rows by `second − lag` (`:219`). `lagged_source_features` (`:83-87`) sets the validation `second` to `second − lag` before the fit. `main` builds the feature list at `:397`.
- `model_identity_sha256` is at `src/shared/utils/gbm.py:484-502`. The manifest uses it twice: `model_sha256` and `model_sha256_by_dir` (`src/backtest/run.py:853, 865-868`). So a new table SHA in the identity already blocks a stale `_archive` and `--resume`.
- Research workers get `valid_X` through `ProcessPoolExecutor` `initargs` (`gbm.py:286-307`), so each of the 10 workers holds its own copy.
- `MODEL_META_FILENAME` lives in `src/shared/utils/model_registry.py:10`. `publish_model_dir` renames the whole staging dir (`:42-60`), so an extra file in staging ships with the model.
- `data/new_model/experiments/*` is not gitignored (`.gitignore:8` un-ignores `data/new_model/`). `data/experiments/*` is ignored (`.gitignore:6`).

Data (read-only probes in the worktree):

- `training_dataset.parquet`: 32 444 rows, 1 645 maps, `second` −60…5 100, all multiples of 60. Every `(match_id, second)` exists in `game_history_minutes.parquet`.
- `validation_dataset.parquet`: 2 347 459 rows, 903 maps. Every `(match_id, second − 10)` exists in `game_features.parquet`, including the 1 847 556 usable rows.
- `game_features.parquet`: 903 maps (the validation maps), `game_second` −60…6 648, no duplicate keys.
- `game_history_minutes.parquet`: 2 856 maps, no duplicate keys. 308 of them are in neither training, validation, nor `production/training_dataset.parquet`. All 39 732 validation minute keys are also in `game_features`.
- Both tapes carry `radiant_nw`, `dire_nw`, `deaths_radiant`, `deaths_dire`, so the self-checks need no other input.
- Every training and validation map has a STRATZ cache file (19 619 files in `data/raw/stratz_matches`).
- `$R1/work/orchestrator/stratz_players.parquet` **does not exist on disk any more.** The extractor `$R1/work/orchestrator/extract_stratz_players.py` is still there. Part 1 measured the old file: 195 920 rows, 19 592 maps, 2025-01-01…2026-10-12, `position` null in 4.17 % of rows, all catalog maps fully labelled.
- Dataset networth: minute rows use `networthPerMinute[s // 60]` for `s ≥ 0` and the playback cursor for `s < 0` (`src/prepare_dataset/stratz_seconds.py:156-165`). A `−60` minute row exists only when every player has playback. Per-second rows use the playback cursor (`:250-293`). `assert_minute_consistency` (`:96-121`) already forces cursor = `networthPerMinute` at every minute mark of a validation map.
- Dataset deaths: `bisect_right(death_times(match, side), second)` (`stratz_seconds.py:194-195`).
- Identity before the change: `model_identity_sha256(data/new_model/research)` = `2ad6c9ec8568068e535251b64b14dfa825d2927316e25a1581228d4a8f42482b`, `research-noxp` = `7822165088a5a6655ab0520414e1c06e7800e9bc03d7f30356d56409b3cd6486`. Both equal the LIVE seed0 manifest.
- `scipy` 1.18.0 is in the worktree `.venv` (a LightGBM dependency).
- File sizes: `tests/test_backtest_signals.py` 941 lines, `tests/test_feed_schedules.py` 1 217 lines, `tests/test_train_model.py` 852 lines. The STEP-002 review flagged growth past 1 000 lines.

## Decisions

1. **One code commit.** The channel is one feature (`main` takes it or not as a whole). Scripts live in `$INV/work/` and are not committed (gitignored; never `git add -f`).
2. **Function rename: `strip_extra_feature_columns`, not `canonical_feature_columns`.** `esports-trader/AGENTS.md` wants verb names. Behavior is the one `feature.json` describes.
3. **Extras are found from the model's feature list, not from the file.** The tail of `model.json` `features` after `strip_extra_feature_columns` is the extra list. `require_catalog_features` refuses a catalog whose list has a tail but whose dir has no `extra_features.parquet`. `feature.json` says "reads the file if it exists"; a stray file without a feature tail would only waste a read.
4. **Tail rule:** strip trailing names that are in `EXPERIMENT_EXTRA_FEATURE_COLUMNS`. An extra name in the middle, or an unknown name at the end, leaves a non-canonical prefix, and `history_policy_for_columns` raises as today.
5. **NaN after the join is an error in the trainer and in the backtest.** `attach_extra_features` itself returns NaN on a miss (the `feature.json` test). Both callers then call one `require_extra_features`. A miss means the table and the dataset disagree, like `attach_history_features` raising on a missing match.
6. **Backtest reads only the shard's maps:** `pd.read_parquet(..., filters=[("match_id", "in", ids)])`. `build_extras.py` writes sorted rows with `row_group_size=100_000`, so the filter skips row groups. Each of the 4 shards then does not load the full table.
7. **Tables keep integer columns (`int32`).** Every value is a count or a networth (integers). It halves the per-worker `valid_X` pickle against `float64`, and the values reach LightGBM unchanged.
8. **Table keys: union of `game_history_minutes` and `game_features` keys, restricted to training ∪ validation maps (2 548 maps).** The 308 extra minute-tape maps are in no dataset. `assign_roles.py` covers exactly those 2 548 maps. On a shared key the `game_features` row wins (values are equal there anyway).
9. **Regenerate the missing STRATZ player table** with the part-1 extractor into `$INV/work/roles/stratz_players.parquet`. I do not write into `$R1`. The cache has 27 more maps than in part 1 (all late). History is strictly earlier, so only maps after them could change.
10. **Players join the role table by `(match_id, hero_id)`.** A hero is unique in a captains-mode map. `account_id` can repeat for anonymous accounts.
11. **No new manifest key and no `compare_backtests.py` change.** `model_sha256` and `model_sha256_by_dir` already change with the table. STEP-006 compares with `--allow-drift`.

## Implementation — code commit

Write no comments and no new docstrings (review rule for this task: "new code should not have comments"). Names carry the meaning. Use named intermediate variables (AGENTS).

### 1. `src/shared/utils/model_registry.py`

Next to `MODEL_META_FILENAME`:

```python
EXTRA_FEATURES_FILENAME = "extra_features.parquet"
```

### 2. `src/shared/utils/dota_features.py`

After `DOTA_NOXP_FEATURE_COLUMNS` (`:121-127`):

```python
_SIDES = ("radiant", "dire")
_WINDOWS = ("1m", "5m")
_POSITIONS = (1, 2, 3, 4, 5)

EXPERIMENT_EXTRA_FEATURE_COLUMNS: tuple[str, ...] = (
    *(f"deaths_{window}_{side}" for window in _WINDOWS for side in _SIDES),
    *(f"pos{position}_deaths_{side}" for position in _POSITIONS for side in _SIDES),
    *(
        f"pos{position}_deaths_{window}_{side}"
        for position in _POSITIONS
        for window in _WINDOWS
        for side in _SIDES
    ),
    *(f"pos{position}_nw_adv" for position in _POSITIONS),
    *(
        f"{group}_deaths_{window}_{side}"
        for group in ("cores", "supports")
        for window in _WINDOWS
        for side in _SIDES
    ),
)
_EXPERIMENT_EXTRA_FEATURES = frozenset(EXPERIMENT_EXTRA_FEATURE_COLUMNS)


def strip_extra_feature_columns(feature_columns: Sequence[str]) -> tuple[str, ...]:
    columns = tuple(feature_columns)
    end = len(columns)
    while end and columns[end - 1] in _EXPERIMENT_EXTRA_FEATURES:
        end -= 1
    return columns[:end]
```

- 4 + 10 + 20 + 5 + 8 = 47 names. The first four are the `deaths.parquet` columns.
- `history_policy_for_columns`: `columns = strip_extra_feature_columns(feature_columns)`, the rest unchanged (same error text, so the test `match="canonical"` holds).

Next to `attach_history_features`:

```python
_EXTRA_FEATURE_KEYS = ["match_id", "second"]


def attach_extra_features(
    frame: pd.DataFrame, extras: pd.DataFrame, *, key_seconds: pd.Series
) -> pd.DataFrame:
    keys = pd.DataFrame(
        {
            "match_id": frame["match_id"].to_numpy(dtype=np.int64),
            "second": key_seconds.loc[frame.index].to_numpy(dtype=np.int64),
        }
    )
    joined = keys.merge(extras, on=_EXTRA_FEATURE_KEYS, how="left", validate="many_to_one")
    values = joined.drop(columns=_EXTRA_FEATURE_KEYS).set_axis(frame.index)
    return pd.concat([frame, values], axis=1)


def require_extra_features(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing_rows = int(frame[list(columns)].isna().any(axis=1).sum())
    if missing_rows:
        raise ValueError(f"{label}: {missing_rows} rows have no extra-feature row")
```

- `how="left"` keeps the left row order. `set_axis(frame.index)` keeps a filtered, non-contiguous index (train rows after `slice_train_rows`).
- `validate="many_to_one"` rejects duplicate table keys.
- An integer column stays `int32` when every key matches. A miss turns that column into `float64` with NaN.

### 3. `src/shared/utils/gbm.py`

`model_identity_sha256`, after the `source_lag_seconds` update:

```python
    extras_path = model_dir / EXTRA_FEATURES_FILENAME
    if extras_path.is_file():
        hasher.update(b"\nextra_features\n")
        hasher.update(sha256_file(extras_path).encode("ascii"))
    return hasher.hexdigest()
```

Without the file the hashed bytes are the same as today. Extend the existing docstring by a few words only if it would become false ("…and the extra-feature table when present"); do not add a new one.

### 4. `src/train_model/train_model.py`

1. `parse_args`: `parser.add_argument("--extra-features", type=Path, default=None)`. If it is set and `--model-dir` is not: `parser.error("--extra-features requires --model-dir")`.
2. `_validate_experiment_args`: if `args.extra_features` is set, `expanduser()`, refuse a missing file (`parser.error(f"--extra-features not found: {path}")`), store `.resolve()`.
3. Helper:

```python
def read_extra_feature_names(path: Path) -> list[str]:
    return [name for name in pq.read_schema(path).names if name not in ("match_id", "second")]
```

   (`import pyarrow.parquet as pq`, the same pattern as `src/shared/utils/telonex_book.py:143`.)
4. `main`, experiment branch (`:396-410`):

```python
        features = list(DOTA_NOXP_FEATURE_COLUMNS if args.no_xp else DOTA_XP_FEATURE_COLUMNS)
        if args.extra_features is not None:
            features += read_extra_feature_names(args.extra_features)
```

   Pass `extra_features_path=args.extra_features`. The live loop passes `extra_features_path=None`. Keep `features` as the last positional argument: the existing `main` tests read `args[-1]` and `args[-2]`.
5. `train_research` gets a required keyword `extra_features_path: Path | None`. After both `attach_catalog_features` calls (after `:222`), one block:

```python
    if extra_features_path is not None:
        extras = pd.read_parquet(extra_features_path)
        extra_columns = read_extra_feature_names(extra_features_path)
        train_dataset = attach_extra_features(
            train_dataset, extras, key_seconds=train_dataset["second"]
        )
        validation_dataset = attach_extra_features(
            validation_dataset, extras, key_seconds=validation_dataset["second"] - lag_seconds
        )
        require_extra_features(train_dataset, extra_columns, "training rows")
        require_extra_features(validation_dataset, extra_columns, "validation rows")
        del extras
```

   Then `validation_fit`, `validation_prediction`, `lagged_source_features` and `ResearchMemberFit` pick the columns up through `features` with no other change.
6. Before `publish_model_dir` (next to `copy2(split_path, ...)`, `:296`):

```python
    if extra_features_path is not None:
        copy2(extra_features_path, staging_dir / EXTRA_FEATURES_FILENAME)
```

7. `train_production` is not touched: the experiment path never runs it.

With `extra_features_path=None` every frame and every call is the same as today. STEP-006's control retrain depends on that.

### 5. `src/backtest/signals.py`

1. `require_catalog_features`:

```python
def require_catalog_features(model_dir: Path, expected: Sequence[str], flag: str) -> None:
    features = load_model_feature_columns(model_dir)
    if strip_extra_feature_columns(features) != tuple(expected):
        raise ValueError(f"{flag} is not the {len(expected)}-column catalog: {model_dir}")
    if len(features) > len(expected) and not (model_dir / EXTRA_FEATURES_FILENAME).is_file():
        raise ValueError(f"{flag} lists extra features but has no {EXTRA_FEATURES_FILENAME}: {model_dir}")
```

   Keep the existing docstring only if it stays true; otherwise drop it.
2. New helper next to `predict_model_deltas`:

```python
def attach_model_extra_features(
    rows: pd.DataFrame, model_dir: Path, extra_columns: Sequence[str]
) -> pd.DataFrame:
    match_ids = sorted({int(match_id) for match_id in rows["match_id"]})
    extras = pd.read_parquet(
        model_dir / EXTRA_FEATURES_FILENAME,
        columns=["match_id", "second", *extra_columns],
        filters=[("match_id", "in", match_ids)],
    )
    attached = attach_extra_features(rows, extras, key_seconds=rows["second"])
    require_extra_features(attached, extra_columns, str(model_dir))
    return attached
```

3. `_assemble_match_signals`:
   - in the row loop add `row["match_id"] = decision.match_id`;
   - in the per-model loop:

```python
            columns = load_model_feature_columns(model_dir)
            extra_columns = columns[len(strip_extra_feature_columns(columns)) :]
            model_rows = decision_rows.iloc[positions]
            if extra_columns:
                model_rows = attach_model_extra_features(model_rows, model_dir, extra_columns)
            predictions = predict_model_deltas(model_dir, columns, model_rows[list(columns)])
```

   Prediction still selects `list(columns)`, so the new `match_id` column never reaches a model. LoL and canonical Dota catalogs have no tail: no read, no behavior change.
4. `build_match_signals` and `build_schedule_match_signals` need no change: both end in `_assemble_match_signals`.

`run.py` needs no change: `_pin_cli_model_dir` already calls `require_catalog_features`, and the manifest already hashes the model dir.

## Tests

New module `tests/test_extra_features.py` (keeps `test_backtest_signals.py` and `test_feed_schedules.py` from growing; cross-test imports are an existing pattern, e.g. `tests/test_feed_schedules.py:24`). No docstrings needed if the test names say it.

1. `test_experiment_extra_feature_columns_are_47_names_outside_both_catalogs`: 47 names, all unique, disjoint from XP ∪ no-XP, first four are `deaths_1m_radiant, deaths_1m_dire, deaths_5m_radiant, deaths_5m_dire`.
2. `test_strip_extra_feature_columns_keeps_the_catalog_prefix`: XP + all 47 → XP; no-XP + first 4 → no-XP; XP alone → XP.
3. `test_history_policy_accepts_a_trailing_extra_tail`: XP + 4 → `GRID_HISTORY_POLICY`; no-XP + 47 → `ODDIN_HISTORY_POLICY`; an extra name inserted at index 1 → `ValueError(match="canonical")`; XP + `["foo"]` → same error.
4. `test_attach_extra_features_joins_on_match_and_key_second`: frame index `[10, 11, 12]`, `match_id [1, 1, 2]`, `second [70, 71, 70]`, `key_seconds = second − 10`; table rows `(1, 60, 5)`, `(1, 61, 6)`; result column `[5, 6, NaN]`, index `[10, 11, 12]`, original columns unchanged. `require_extra_features` on it raises; on the first two rows it passes.
5. `test_attach_extra_features_rejects_duplicate_table_keys`: two table rows with the same key → `pandas.errors.MergeError`.
6. `test_require_catalog_features_needs_the_table_for_an_extra_tail` (tmp catalogs with `model.json` only): XP + 4 with `extra_features.parquet` → passes; same without the file → `ValueError(match="extra features")`; no-XP + 4 checked as XP → `ValueError(match="catalog")`.
7. `test_backtest_joins_model_extra_features_on_the_decision_second`: import `build_signal_frame`, `predict_signals`, `FakeBooster`, `install_fake_booster` from `test_backtest_signals`. Model dir: `features = XP + ["deaths_1m_radiant"]`, `member_00.txt`, `extra_features.parquet` with one row `(1, 57, 3)` (decision at second 67, lag 10). A `FakeBooster` subclass records `features["deaths_1m_radiant"].tolist()`. Assert recorded columns `== XP + ["deaths_1m_radiant"]` and value `[3]`.
8. `test_backtest_rejects_a_decision_without_an_extra_row`: same setup, table row at second 58 → `ValueError(match="extra-feature")`.

`tests/test_gbm_catalog.py`:

9. `test_research_identity_is_unchanged_without_extra_features`: `model_identity_sha256(RESEARCH_MODEL_DIR) == "2ad6c9ec…482b"` and `RESEARCH_NOXP_MODEL_DIR == "7822165…6486"` (full strings above). This is the `feature.json` regression. It breaks only when the git-tracked research model is republished; then update both constants in that commit.
10. `test_identity_changes_with_the_extra_feature_table`: tmp catalog; hash A without the table; write a 2-row table → hash B ≠ A; change one value → hash C ≠ B.

`tests/test_train_model.py` (stays under 1 000 lines):

11. `test_train_research_joins_extra_features_by_train_second_and_lagged_validation_second`: reuse `write_dataset_files`, `write_flat_tape`, `build_existing_model_meta`. Table keys: every training `(match_id, second)` and every validation `(match_id, second − TRAIN_LAG_SECONDS)` (all 5 rows, including the `missing_quote` and no-label rows), value `deaths_1m_radiant = key second`. Fake `fit_research_members` records `train_frame` and `valid_X` and returns one model whose `predict` returns zeros and whose `save_model` writes a file. Assert:
    - `train_frame["deaths_1m_radiant"] == train_frame["second"]`;
    - `valid_X["deaths_1m_radiant"] == valid_X["second"]` (valid `second` is already shifted by the lag, so a join on the unshifted second fails here);
    - `research_dir / "extra_features.parquet"` bytes equal the source;
    - `model.json` `features == XP + ["deaths_1m_radiant"]`.
12. `test_train_research_rejects_a_validation_row_without_extra_features`: drop one validation key → `ValueError(match="validation rows")`.
13. `test_main_experiment_extra_features_appends_table_columns`: a tiny table `(match_id, second, deaths_1m_radiant, deaths_1m_dire)`. The fake `research` records `args[-1]` and `kwargs["extra_features_path"]`. XP run → XP + 2 names and the resolved path; `--no-xp` run → no-XP + 2 names.
14. Parser: `--extra-features` without `--model-dir` → `SystemExit`; a missing file → `SystemExit`.
15. Existing direct call (`:476`) and nothing else: add `extra_features_path=None`.

## Scripts in `$INV/work/` (not committed)

Rules from the STEP-002 review apply: no tuple returns for our own multi-field values (frozen dataclass), compute coverage counts once where the data is prepared, validate inputs before any lossy cast, no comments. Run every script from the worktree root with `make run F=<absolute path>`, so `src` is on the path and `DATA_DIR` is the worktree `data/`.

### A. STRATZ player table (reuse the part-1 extractor)

```bash
mkdir -p $INV/work/roles
make run F=$R1/work/orchestrator/extract_stratz_players.py \
  ARGS="data/raw/stratz_matches $INV/work/roles/stratz_players.parquet"
```

Expect about 196 000 rows, 19 600 maps, position null near 4 %. It reads 19 619 gz files: several minutes.

### B. `$INV/work/assign_roles.py`

Method of `$R1/work/orchestrator/baseline_assign.py`, without its evaluation counters.

1. Read `$INV/work/roles/stratz_players.parquet` (`match_id, start_time, account_id, hero_id, player_slot, is_radiant, position`). Sort by `["start_time", "match_id", "player_slot"]`.
2. Wanted maps: `match_id` values of `TRAINING_DATASET_PATH` ∪ `VALIDATION_DATASET_PATH` (2 548). Fail if a wanted map is missing from the player table.
3. Constants: `PLAYER_WINDOW = 30`, `PLAYER_ALPHA = 0.5`, `HERO_DAYS = 60`, `HERO_ALPHA = 1.0`, `POSITIONS = ("POSITION_1", …, "POSITION_5")`.
4. History: `player_history: defaultdict[int, deque[str]]` with `maxlen=30`; `hero_history: defaultdict[int, list[HeroSeen]]` where `HeroSeen(start_time: int, position: str)` is a frozen dataclass.
5. Walk `groupby("start_time", sort=True)`. For each wanted map in the group:
   - require 10 rows, 5 per side, 10 distinct heroes;
   - per side (rows already in `player_slot` order): cost row `i` = `−(log p_player + log p_hero)`. `p_player` = position counts of the player's last 30 matches + 0.5, normalised. `p_hero` = hero position counts with `start_time ≥ start − 60·86400` + 1, normalised. No history gives a uniform prior by these formulas;
   - `linear_sum_assignment(cost)`; position = column index + 1.
   - After the whole group: append every row whose `position` is a string to both histories (strictly earlier rule).
6. Before writing, print the count of wanted players with a null or zero `account_id`. If it is 0, add no special case. If it is not 0, stop and report: two anonymous players would share one history.
7. Output `data/experiments/roles/assigned_positions.parquet`: `match_id` int64, `account_id` int64, `hero_id` int64, `is_radiant` bool, `position` int64. Sort by `["match_id", "is_radiant", "position"]`, `index=False`. 25 480 rows.
8. Accuracy against STRATZ labels, printed for training maps and validation maps separately:
   - per player: assigned == label;
   - per position: for each label 1–5, the share assigned to it;
   - whole team: all 5 correct;
   - core vs support: `(assigned ≤ 3) == (label ≤ 3)`.
   Part 1 gives an anchor: 95.79 % per player and 90.54 % whole team over 18 775 maps. Training maps include the cache hole 2025-02…2025-09, so expect lower numbers there.

### C. `$INV/work/build_extras.py`

Inputs: `assigned_positions.parquet`, `GAME_FEATURES_DATASET_PATH`, `GAME_HISTORY_MINUTES_PATH`, the two dataset match-id sets, the STRATZ cache (`stratz_match_cache_path`, `read_gzip_json`, `player_death_times` from `src/shared/utils/stratz.py`). It must not read `networth_end`, final kills/deaths/last hits, `durationSeconds`, `didRadiantWin`.

1. Keys: concat `game_features` then `game_history_minutes` columns `match_id, game_second, radiant_nw, dire_nw, deaths_radiant, deaths_dire`, add `is_second_key` (True for the `game_features` part), `drop_duplicates(["match_id", "game_second"], keep="first")`, keep training ∪ validation maps, rename `game_second` → `second`, sort by `["match_id", "second"]`.
2. Per map (`groupby("match_id", sort=True)`), with `s` = the map's key seconds (`int64` array):
   - load the STRATZ match; position per player from the role table by `(match_id, heroId)`; require positions 1–5 once per side;
   - per player: `times = np.asarray(player_death_times(player))`; `deaths = searchsorted(times, s, "right")`; `deaths_1m = deaths − searchsorted(times, s − 60, "right")`; `deaths_5m = deaths − searchsorted(times, s − 300, "right")`. A death at time ≤ s counts;
   - networth `networth_at(player, s)`: with `playbackData`, sort `playerUpdateGoldEvents` by `time` and take the last `networth` with time ≤ s, 0 before the first event; without playback, `networthPerMinute[s // 60]`, NaN when the index is outside the list (same as `nw_at` in `$R1/work/astra-value/features.py:26-35`);
   - columns, built in a dict in `EXPERIMENT_EXTRA_FEATURE_COLUMNS` order:
     - `deaths_{w}_{side}` = sum of the side's five `deaths_{w}`;
     - `pos{p}_deaths_{side}`, `pos{p}_deaths_{w}_{side}` from the player at position p;
     - `pos{p}_nw_adv` = networth(Radiant pos p) − networth(Dire pos p);
     - `cores_deaths_{w}_{side}` = pos 1–3, `supports_deaths_{w}_{side}` = pos 4–5;
   - check columns (dropped before writing): `radiant_nw_sum`, `dire_nw_sum` (five-player sums), `radiant_deaths_sum`, `dire_deaths_sum` (sum of `pos{p}_deaths_{side}`).
3. Concat all maps. Self-checks, printed:
   - networth: a row fails when either side has `|sum − dataset nw| > 0.01 · max(dataset nw, 1)`. Print the failing share for `is_second_key` rows and for minute-only rows. Second keys must be 0 (prepare asserts the same cursor); if not, stop and report;
   - deaths: share of rows where either side's position sum ≠ `deaths_{side}`. Must be 0; if not, stop;
   - NaN count over the 47 columns must be 0 before the cast; if not, stop;
   - `assert list(columns) == list(EXPERIMENT_EXTRA_FEATURE_COLUMNS)`.
4. Cast the 47 columns to `int32`, keys `int64`. Write with `index=False, row_group_size=100_000`:
   - `data/experiments/e3/deaths.parquet`: `match_id, second` and the first 4 columns;
   - `data/experiments/e3/roles.parquet`: `match_id, second` and all 47.
5. Print row count, map count, and `sha256_file` of both files.

Expected size: about 2.44 M rows (2.36 M second keys + ~80 k minute keys of training maps). It reads 2 548 STRATZ files: a few minutes, under 2 GB RAM.

## Order of work

1. Record the identity hashes before any edit (one-liner in Verification step 1).
2. Code changes 1–5 and tests. Typecheck, lint, targeted tests. Commit.
3. Scripts A → B (twice, SHA check) → C. Join check (Verification step 6).
4. Progress entry: commit SHA, all printed numbers, both SHA256 values, any deviation.

Commit message in the repo's style (one sentence, period), e.g. `Train and backtest the 300 s model with an optional extra-feature table.` Stage files by name; never `git add -A` in this worktree.

## Verification

From `../esports-trader-exp`:

1. Identity before and after:

```bash
PYTHONPATH=src uv run python -c "from shared.utils.gbm import model_identity_sha256 as h; from shared.constants.paths import RESEARCH_MODEL_DIR as x, RESEARCH_NOXP_MODEL_DIR as n; print(h(x)); print(h(n))"
```

   Both runs must print `2ad6c9ec…482b` and `7822165…6486`.
2. Typecheck: `uv run python -m basedpyright` → 0 errors.
3. Lint: stage the files, then `uv run python -m pre_commit run` (ruff check/format, basedpyright, no `__init__.py`).
4. Targeted tests (orchestrator approved unit tests for this task; no live trader runs on this machine):

```bash
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_extra_features.py tests/test_gbm_catalog.py tests/test_train_model.py \
  tests/test_dota_features.py tests/test_backtest_signals.py tests/test_feed_schedules.py \
  tests/test_trader_game_profile.py tests/test_model_server.py tests/test_backtest.py \
  tests/test_since_match.py tests/test_lol_backtest.py -q
```

5. Full suite once: `PYTEST_N=8 make test`. The 18 failures listed in `progress.txt` (13 `ODDIN_BRAND_TOKEN` host tests, 2 LoL smokes without `backtest_audit.parquet`, 3 Dota seed0 goldens) are pre-existing. Any other failure is this step's.
6. Join check on real tables (read-only, about 1 min, a few GB):

```bash
PYTHONPATH=src uv run python - <<'EOF'
import pandas as pd
from shared.constants.paths import TRAINING_DATASET_PATH, VALIDATION_DATASET_PATH
from shared.utils.dota_features import EXPERIMENT_EXTRA_FEATURE_COLUMNS, attach_extra_features, require_extra_features
for name, width in (("deaths", 4), ("roles", 47)):
    extras = pd.read_parquet(f"data/experiments/e3/{name}.parquet")
    columns = EXPERIMENT_EXTRA_FEATURE_COLUMNS[:width]
    assert list(extras.columns) == ["match_id", "second", *columns]
    train = pd.read_parquet(TRAINING_DATASET_PATH, columns=["match_id", "second"])
    val = pd.read_parquet(VALIDATION_DATASET_PATH, columns=["match_id", "second"])
    require_extra_features(attach_extra_features(train, extras, key_seconds=train["second"]), columns, f"{name} train")
    require_extra_features(attach_extra_features(val, extras, key_seconds=val["second"] - 10), columns, f"{name} validation")
    print(name, "join ok", len(train), len(val))
EOF
```

7. Determinism: `shasum -a 256 data/experiments/roles/assigned_positions.parquet` after each of the two `assign_roles.py` runs; the values must be equal.
8. Self-checks of `build_extras.py`: networth mismatch share 0 on second keys, deaths mismatch 0, NaN 0.
9. `git status --short` in the worktree: only the intended source and test files are staged; `data/experiments/` does not appear (ignored).

No backtest and no training in this step. The first real use of the channel is STEP-006 (`make train ... --extra-features`, then a one-map smoke with the `e3-roles` model dirs).

## Numbers for `progress.txt`

- commit SHA;
- identity hashes before/after (equal);
- extractor: rows, maps, position null share;
- `assign_roles.py`: anonymous-player count; accuracy per player, per position (1–5), whole team, core vs support, for training and validation maps; SHA256 of both runs;
- `build_extras.py`: rows, maps, networth mismatch share (second keys / minute keys), deaths mismatch share, NaN count, SHA256 of `deaths.parquet` and `roles.parquet`;
- join check result;
- test counts and the full-suite failure list.

## Edge cases

- **Validation keys below 0.** Validation `second` starts at −50, key −60. `game_features` starts at −60. The −60 minute row exists only for full-playback maps; networth there is the playback cursor (0 before the first event), the same as the dataset.
- **Player without playback** (training maps only): `networthPerMinute[s // 60]`. Out of range gives NaN and the NaN check stops the script. Prepare already dropped maps with a short list (`MatchDataError`), so this does not happen with the current data.
- **Training minute keys with playback:** the table uses the cursor, the dataset uses `networthPerMinute`. They can differ slightly; the minute-key mismatch share measures it. This is the only expected non-zero check.
- **Deaths at exactly s** count (`side="right"`), the same as the dataset.
- **Duplicate table keys** → `MergeError` in `attach_extra_features`.
- **Float or wrong-typed keys** in a hand-made table: any key that does not match gives NaN, then `require_extra_features` raises. Nothing joins silently wrong.
- **Unknown or misplaced extra column** → `history_policy_for_columns` raises at the start of training; `require_catalog_features` gives `parser.error` in the backtest.
- **Catalog with a tail but no table** → `parser.error` at backtest start, not a crash after 30 minutes of archive work.
- **Same `start_time` for two maps** (series boundaries): history updates after the whole group, so neither sees the other.
- **A wanted map without STRATZ labels**: assignment does not need labels; accuracy counts only labelled rows. Part 1 says every catalog map is labelled.
- **LoL**: LoL catalogs never have a tail. `_assemble_match_signals` adds a `match_id` column that prediction never selects. No LoL behavior change (the STEP-002 review flagged LoL drift).

## Hard parts and risks

1. **Optimistic availability on archive maps.** Archive decisions use GRID table levels, but the extras come from STRATZ at the GRID game second. Deaths in the table lag the event by about 8 s, so per-position deaths can be ahead of what the decision saw. grid-v1 maps are consistent (both STRATZ at `second − 10`). STEP-006 measures the violation share (`check_availability.py`); this step does not fix it (`feature.json` non-goal).
2. **Training memory in STEP-006.** Each of the 10 research workers holds its own `valid_X` (about 1.6 M rows). `e3-roles` adds 47 columns to 81, about +58 % feature width. LightGBM converts the frame to `float64` inside every worker. The 15–20 GB peak can grow to roughly 24–32 GB on a 32 GB Mac. `int32` tables cut the pickles, not the conversion. Run `e3-roles` training alone, with nothing else open. If it swaps hard, the lever is fewer member workers for that run; that is a code change for STEP-006 to decide, not this step.
3. **Untracked model dirs.** `data/new_model/experiments/<arm>/extra_features.parquet` (tens of MB) will show as untracked after STEP-006 training, because `data/new_model/` is not ignored. Do not commit them; the pre-commit large-file hook (4 300 KB) would also refuse.
4. **Role accuracy on training maps.** The STRATZ cache has almost no maps from 2025-02 to 2025-09. Players in early training maps have thin history, so their roles are noisier. The per-split accuracy shows this; E3-roles can underperform for that reason alone.
5. **Changed STRATZ player table.** The regenerated table can differ from part 1 by 27 late maps. Strictly-earlier history means only later maps can change. The accuracy is recomputed here, so the part-1 number is only an anchor.
6. **Identity test pins the git-tracked research model.** A republish of `data/new_model/research` breaks test 9 on purpose; whoever republishes updates the constants.
7. **Canonical stripping also reaches live** (`src/trader/game_profile.py:40`). Live catalogs are always canonical, and the live model server still compares feature names exactly, so an E3 catalog cannot go live by accident. Live support is a non-goal.
8. **Hash cost.** `model_identity_sha256` now reads the table (tens of MB) each time the manifest is built. It is under a second per call.
