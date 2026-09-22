# Step 3A — implementation report

Commit: `199705d4 feat: [step 3A] - features by game-second and archive schedule`
on `esports-trader` `main`, not pushed. `poly-maker` untouched.

Implements `plans/step-3a.md` only. No 3B, no training run, no full-seed
backtest, no live/paper restart, no CLI/policy cleanup beyond what the plan
required.

## Dataset artifact — `game_features.parquet`

`prepare_dataset.py` gains `build_game_feature_rows(match_id, states,
market_prior) -> list[GameFeatureRow]` (`prepare_dataset.py`). Each row is
the same model-input block as `StateFeatures` minus `radiant_win` (settlement
data, deliberately not a column), keyed by actual `game_second` — not
`second - lag`. `market_radiant_prior` is stamped per row.

`GameFeatureRow` (TypedDict) lives in `shared/types/dataset.py:73`;
`GAME_FEATURES_DATASET_PATH = NEW_DATASET_DIR / "game_features.parquet"` in
`shared/constants/paths.py:28`. `write_rows` accepts the new row type and
`write_datasets` emits the artifact for both explicit `--output-dir` runs and
the canonical production paths. Emission needs only prepared states +
market prior — no model, no archive.

## Feed planning — `src/backtest/feed_schedules.py` (new)

Per-match resolution happens once, up front, against the persisted archive
index:

- `MatchFeedPlan(match_id, mode, binding, model_dir)` — `mode` is
  `"schedule"` or `"grid_v1"`.
- `ScheduleBinding` — archive root/id, schedule path, expected fingerprint,
  resolved archive dir, feed source.
- `FeedPlanResult(plans, exclusions)` — exclusions are `match_id -> reason`,
  never silent fallback.
- `resolve_dota_feed_plans` joins on catalog archive fields (steam match id
  cross-checked against the schedule identity);
  `resolve_lol_feed_plans` joins on audit `condition_id`.
- `_bind_schedule` validates: admitted archive row, non-duplicate
  admission, schedule file present/readable, fingerprint match, schema and
  rules versions, game/source identity, and match/condition identity.
  Anything else (`excluded_*`, unlinked, stale rules, unreadable,
  fingerprint mismatch, wrong identity) becomes an explicit exclusion with
  the admission/reason string.
- `entry_stale_seconds(binding)` — oddin → `ODDIN_FEED_STALE_SECONDS`,
  otherwise `GRID_FEED_STALE_SECONDS`.
- `schedule_map_sha256(plans)` fingerprints the resolved map (sorted
  match_id/mode/binding tuples) for manifest resume safety.
- `load_archive_index()` reads the published `index.parquet` once.

Model selection: dota `grid` → `RESEARCH_MODEL_DIR`, dota `oddin` →
`RESEARCH_NOXP_MODEL_DIR`, lol → `LOL_RESEARCH_MODEL_DIR`; `--model-dir`
overrides all.

## Schedule-driven signals — `src/backtest/signals.py`

`MatchSignals` gains `feed_game_seconds`, `feed_paused`, `feed_terminal`
tapes (empty on grid-v1 and live-archive paths). New types:
`MarketAnchorTape` + `market_anchor_tape(MidSeries)` (the causal ok-mid
tape), `anchor_at`, `DatasetReadinessError`, `ScheduleDecision` (typed
record per archive tick that recomputes a decision — no anonymous tuples).

`build_schedule_match_signals(schedule_ids, plans, schedules, feature_rows,
anchor_tapes)`:

- preserves persisted tick order and `received_ns` verbatim — no lag, no
  10/15/17s shift;
- drops decisions on stale ticks via `recovery_stale_mask` +
  `grid_exit_age_seconds(entry_stale_seconds(binding))`;
- joins feature rows by exact `game_second`; a missing row raises
  `DatasetReadinessError` (run.py surfaces it as
  `ValueError("dataset not ready for archive replay: ...")`), duplicates
  are corrupt data;
- reads the market anchor causally — latest mid at or before `received_ns`;
  ticks with no causal anchor stay feed-only;
- batches predictions per model directory (one predictor load per dir);
- emits the observed `game_second`/`paused`/`terminal` tapes on the
  signals.

`build_match_signals` (grid-v1) is unchanged apart from the three empty
tape fields — same seeded cadence, same lag.

## Strategy observed clock — `src/backtest/strategy.py`

`DotaMakerConfig` carries the three feed tapes. `_clock_at` (strategy.py:964)
uses them when present: game second is the last tick's observed
`game_second` at or before `now_ns`, `paused` is the tick's flag,
`game_ended` is the last tick's `terminal` OR `now_ns >= game_end_ns`
(defensive fallback). Empty tapes → the exact old behavior
(540-after-cutoff / 0, never paused, `game_end_ns` only).

`build_strategy_configs` (run.py:509) computes, for schedule matches:
- `buy_cutoff_ns` = first feed tick at or after `policy.buy_cutoff_second`
  (last tick if none reaches it);
- `game_end_ns` = last terminal feed tick when present, else catalog
  `game_ended_at`.

Non-schedule matches keep `get_state_available_ts(horn, cutoff, pauses)`
and catalog `game_ended_at`.

## Kernels, coverage, provenance, manifest — `run.py`/`results.py`/`selection.py`

- `build_current_kernels` takes `plans`; bound matches get
  `entry_stale_seconds(binding)` (oddin vs grid), unbound grid-v1 keeps
  `GRID_FEED_STALE_SECONDS`. Archive-policy mode unchanged (headers).
- `ValidationCoverage.archive_excluded` counts plan exclusions; `log_coverage`
  prints it.
- `SignalProvenance(signal_mode, model_name)` flows into
  `build_maker_match_results` and `engine_fault_match_result`; result rows
  record `signal_mode` + `model_name`.
- `build_run_manifest` gains `schedule_map_sha256` (when auto resolution
  ran), `feed_schedule_rules_version`, `admission_rules_version`, and
  `signal_source` (`"auto"` for default resolution, `"live_archive"` for
  archive/live-since). A changed schedule map → different manifest → no
  unsafe resume.

## Telonex source tree — `src/backtest/telonex_local.py`

`create_telonex_source_tree(contexts, capture_root, indexes,
schedule_archives)` — the new required mapping names the bound archive dir
per match and takes precedence over the live resolver, so non-`grid-*`
archive names still get our own book stripped. Callers
(`warm_replay_cache`, replay, tests) pass it explicitly.

## Default-run flow — `src/backtest/run.py`

`main` resolves feed plans right after `load_run_selection` (dota through
catalog fields, lol through audit `condition_id`; skipped entirely for
live-since/archive-policy modes). Exclusions are logged with reasons,
added to `ValidationCoverage.archive_excluded`, and removed from the
replay set. A `--match-id` that resolves to an exclusion aborts with the
reason. `--lag-seconds`/`--cadence-mean-interval` combined with any
schedule-bound match is rejected (`_reject_schedule_flags`) — the archive
schedule owns timing.

`_resolve_run_signals` dispatches per plan: `grid_v1` ids go to
`build_match_signals`; `schedule` ids load `game_features.parquet` (dota)
or rename lol signal-row `second` → `game_second`, build causal tapes from
`MidSeries`, and call `build_schedule_match_signals`. Schedule-bound
archive dirs feed `warm_replay_cache`/`create_telonex_source_tree` via
`_schedule_archive_dirs`.

`build_parser()` was extracted from `parse_args`, and `_resolve_feed_plans`,
`_schedule_archive_dirs`, `_signal_provenance_map`,
`_read_run_archive_headers` keep `main` under the ruff C901 budget.

## Tests

- `tests/test_feed_schedules.py` (new, 24 tests): binding validation
  (fingerprint/identity/schema/admission/duplicate), dota+lol resolution,
  exclusions with reasons, no silent fallback, causal anchor gating,
  missing-feature `DatasetReadinessError`, stale-tick decision skip,
  feed-only anchorless ticks, per-model-dir batching, observed tapes,
  `entry_stale_seconds`, `schedule_map_sha256` sensitivity.
- `test_backtest_maker.py`: `_clock_at` on observed tapes (second, pause,
  terminal) + `game_end_ns` fallback without a terminal tick.
- `test_prepare_dataset.py`: `build_game_feature_rows` keyed by
  `game_second`, no `radiant_win` column.
- Updated call sites for new required args: `MatchSignals` tapes,
  `build_run_manifest(schedule_map_sha256)`, `build_current_kernels(plans)`,
  `warm_replay_cache`/`create_telonex_source_tree(schedule_archives)`,
  `build_maker_match_results`/`engine_fault_match_result(provenance)`,
  `ValidationCoverage.archive_excluded`.

## Verification

- `pytest` targeted groups (plan list + every touched file):
  `test_feed_schedules.py` 24, signals/maker/prepare/validation/
  live_archives/postprocess/extraction_oracle — 281 pass; lol+archive
  group — 128 pass. **357 targeted pass total.**
- `ruff check` — clean; pre-commit `ruff check`/`ruff format`/`basedpyright`
  hooks passed on the commit.
- `basedpyright` on all touched src + test files — 0 errors.
- Full suite (beyond brief): 2289 pass; the only 4 failures are
  `test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
  — seed-0 golden fill-tape drift reproduced **identically at clean HEAD**
  in a scratch worktree (local market data was rebuilt this session:
  `make market-data` built 262, catalog/market caches rerun in step 2).
  The test file itself labels these current-policy tapes drift-prone.

## Notes

- A parallel session committed `ecd2a570 feat: [US-005] - VPS enable and
  delete the Python on-chain collector` mid-task; it owns the
  `telonex_onchain` → `telonex_capture` rename (including the
  `telonex_local.py` import line). My commit sits on top and contains only
  step-3A files — the two concerns stay separate.
- `game_features.parquet` is produced by `make prepare`; until it exists,
  a schedule-bound default run fails fast at `_load_game_feature_rows`
  with "run make prepare" rather than silently falling back to grid.
