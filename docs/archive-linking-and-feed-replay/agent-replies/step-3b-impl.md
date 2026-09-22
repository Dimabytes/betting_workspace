# Step 3B implementation report — ordinary backtest launch and mixed-model report

Commit: `85eccfc8` on `main` — `feat: [step 3B] - ordinary backtest launch and mixed-model report`
(19 files, +828/-793). Working tree clean afterward; no push performed.

## What changed

### 1. Ordinary backtest uses typed feed plans only

`src/backtest/run.py` no longer carries the legacy live-archive path:

- Parser: `--policy` and `--live-since` are removed. The mutually exclusive
  selector group is now `--match-id | --validation | --since-match`.
  `--since-match` requires `--name` (like `--validation`), allows
  `--limit`/`--resume`/`--shard`/`--merge-shards`, and is `type=int`.
- `resolve_feed_plans(selection.archive_join, selected_ids, model_override,
  match_id)` runs on every launch. `_apply_feed_exclusions` drops excluded
  matches from the cohort and counts them in
  `ValidationCoverage.archive_excluded`; if the whole selection is excluded
  the error now names each match and reason
  (`archive feed excluded every selected match (7: archive_unlinked, ...)`).
  `--match-id` on an excluded match still fails earlier inside
  `resolve_feed_plans` with `match <id>: <reason>`.
- `reject_schedule_flags` now refuses `--validation-dataset` in addition to
  `--lag-seconds` and `--cadence-mean-interval` when any `SchedulePlan`
  exists.
- `build_order_latency()` lost the `archive_parity` parameter; ordinary
  runs always use the network-latency path (`cancel_latency_ns` = RTT).
- Deleted: `build_archive_kernels`, `build_run_kernels`,
  `read_run_archive_headers`, `build_live_indexes`, `_lol_live_indexes`,
  the `build_live_archive_signals_by_match` signal branch, and all
  `ArchiveHeader`/`PolicyMode` plumbing in `main()`. Kernel selection is
  always `build_current_kernels(contexts, policies, plans)`.
- `_resolve_run_signals` now takes explicit scalar args and always builds
  per-plan signals: schedule ticks for `SchedulePlan`s (observed schedule
  clocks via the plan binding, wired through `build_strategy_configs`) and
  grid-v1 `SignalTiming` for `GridV1Plan`s. Mixed cohorts batch predictions
  per `model_dir`.

`trader.replay_core_trace` / `make parity` are untouched and remain the
diagnostic core-trace tool.

### 2. Signal provenance and results

`src/backtest/results.py`:

- `SignalProvenance` = `{signal_mode, feed_source, model_name}`;
  `signal_provenance(plan)` produces `schedule` + the bound feed source
  (`grid`/`oddin`) or `grid_v1` + `grid_v1`. The `live_archive` mode and the
  `ArchiveHeader` fallback are deleted; `signal_provenance_map(plans,
  match_ids)` no longer takes archive headers.
- `MakerMatchResult` gained `feed_source` (between `signal_mode` and
  `model_name`); both `build_maker_match_result` and
  `engine_fault_match_result` populate it.

`src/backtest/live_archives.py` is now inspector-only:
`LiveMatchIndexes`, `build_dota_live_indexes`, `load_match_json`,
`resolve_archive_match_id`, `read_session_live_equity`,
`live_equity_by_match_ids` — everything `inspect/tail.py` still imports.
Removed: `ArchiveHeader`, header readers, `resolve_live_since_match_ids`,
`find_archive_for_match_id`, `list_grid_archive_dirs`,
`build_match_signals_from_live_archive`, `build_live_archive_signals_by_match`.

### 3. Since-match selection

- `selection.py::select_dota_since_match_ids(sources, since_match_id)` —
  anchor from the match catalog `start_time`; cohort = catalog order
  (chronological) filtered by the same gates as validation selection:
  `usable_signal_match_ids`, `book_gap_excluded_match_ids`,
  `has_local_telonex_days` over the spawn-anchored availability window.
  Anchor need not be replayable; unknown anchor or empty cohort raises.
- `lol_inputs.py::select_lol_since_match_ids(audit, signal_rows,
  since_match_id)` — anchor horn from `signal_rows.start_time`; cohort =
  whitelist-filtered `audit.eligible` maps with `horn >= anchor`, sorted by
  `(horn, match_id)`.
- `run.py::load_run_selection` accepts `since_match`; Dota resolves the
  cohort then pins `load_dota_selection(match_ids=...)`; LoL resolves
  against a full probe load and overrides `selected_ids` (coverage=None).

### 4. Telonex source tree

`create_telonex_source_tree(contexts, capture_root, schedule_archives)`:

- The `LiveMatchIndexes` parameter and the implicit
  `_archive_for_context`/`find_archive_for_match_id` lookup are gone —
  ordinary replay never resolves archives on its own.
- `schedule_archives` (from `feed_schedules.schedule_archive_dirs`) names
  each schedule-bound match's real archive dir; its book day files are
  rewritten with own-book stripping.
- Guard restored from the old code path: a bound archive without
  `core_trace.jsonl` warns and links plain books rather than crashing in
  `load_resting_events` (ponytail-marked: tape may retain own orders).

### 5. Manifest identity

`build_run_manifest(model_dir, game, signal_cadence_seed, run_policy,
selected_ids, whitelist_keys, plans, exclusions, since_match, ...)`:

- `signal_source="auto"`, `backtest_policy="current"` always;
  `archive_policy_sha`/`archive_model_sha256` removed.
- `schedule_map_sha256(plans)` — now hashes `{match_id|mode|fingerprint}`
  only; `model_dir` is deliberately excluded so rebuilding a model does not
  change schedule identity (test asserts digest equality across model dirs).
- `feed_schedule_rules_version` (`EXTRACTION_RULES_VERSION`) and
  `admission_rules_version` always present.
- `archive_exclusions`: `{match_id: reason}` sorted map (empty `{}` when
  none).
- `model_sha256_by_dir`: one `model_identity_sha256` per distinct
  `plan.model_dir` — mixed-model runs pin every model.
- `game_features_sha256`: dota only, when at least one `SchedulePlan`
  exists.
- `since_match`: present only when the flag was used.

### 6. Mixed-model/feed reporting

- `ArmSummary` gained `signal_groups: dict[str,int]` and
  `model_groups: dict[str,int]`, computed in `summarize_arm` over all
  results (terminated included). Labels: `grid_v1` or
  `schedule:<feed_source>`.
- Terminal report gains a `SIGNALS` section (`report.py::_signal_rows`):
  per-group counts, an `excluded` row with reason-count detail from the
  manifest's `archive_exclusions`, and `model <name>` rows.
- `scripts/compare_backtests.py` `EXPECTED_MANIFEST_DIFFS` gained
  `signal_source`, `schedule_map_sha256`, `archive_exclusions`,
  `since_match`, `model_sha256_by_dir`, `game_features_sha256`
  (`archive_policy_sha`/`archive_model_sha256` kept for legacy manifests).
- `Makefile` `backtest`/`lol-backtest` help text now mentions
  `--since-match`.

## Tests

New/updated coverage (all passing):

- `test_backtest.py`: `--since-match` parse acceptance (limit/shard/resume),
  `--name` requirement, mutual exclusion with `--match-id`/`--validation`;
  removed-flag rejection list now includes `--policy`/`--live-since`;
  `select_dota_since_match_ids` tail ordering, gate filtering
  (usable/book-gap/telonex), replayable-exempt anchor, unknown-anchor and
  empty-cohort errors; `select_lol_since_match_ids` horn ordering, missing
  anchor rows, empty cohort; manifest feed-map assertions (auto source,
  schedule digest, rules versions, exclusions serialization,
  model_sha256_by_dir, since_match present/absent, no archive_* keys);
  warm-cache fake tree signature updated; `resolve_run_policies` new
  signature.
- `test_feed_schedules.py`: `resolve_feed_plans` refuses excluded
  `--match-id` with reason; mixed cohort returns bound plans + separate
  exclusions; `schedule_map_sha256` invariant to `model_dir`, sensitive to
  mode/fingerprint; manifest mixed-model test (two schedule feeds + grid
  plan → both model dirs hashed, `game_features_sha256` pinned);
  `signal_provenance_map` naming for schedule/grid_v1.
- `test_backtest_postprocess.py`: `build_result_row` gained
  signal_mode/feed_source/model_name kwargs; `summarize_arm` signal/model
  group counts; `format_terminal_report` SIGNALS section incl. exclusion
  reason aggregation.
- `test_backtest_validation.py`: provenance fixtures carry `feed_source`;
  `replay_matches`/`fake_tree` signatures updated; new source-tree tests —
  bound archive without `core_trace.jsonl` links plain books (no crash, no
  strip call), bound archive with `core_trace.jsonl` feeds resting events.
- `test_live_archives.py`: slimmed to `resolve_archive_match_id` slug
  resolution, `build_dota_live_indexes`, and the kernel-config msgspec
  test now decoding headers via `trader.core_trace_codec.decode_header_row`.
- `test_lol_backtest.py`, `test_extraction_oracle.py`: manifest/run_batch/
  source-tree call sites updated (`archive_parity`, `archive_header`,
  `build_live_indexes` removed).

## Verification

- `pytest` on all touched groups: **329 passed** (test_backtest,
  test_feed_schedules, test_live_archives, test_backtest_postprocess,
  test_backtest_validation, test_lol_backtest, test_backtest_maker,
  test_extraction_oracle); second batch (test_backtest_signals,
  test_compare_backtests, test_promote_backtest, test_backtest_inspect,
  test_backtest_telemetry, test_core_trace, test_backtest_imports):
  **119 passed**.
- `ruff check` + `ruff format`: clean on all touched files (and enforced by
  the pre-commit hook on commit).
- `uv run basedpyright` on all touched files: **0 errors**.
- `python -m backtest.run --help`: shows the new CLI (no `--policy`,
  `--live-since`; `--since-match` present with `--name` requirement).
- Known pre-existing failure: `test_follow300_replay.py`
  `test_seed0_map_replay_matches_current_policy_smoke[dota-8837869969-6]`
  (seed-0 golden fill-tape drift, `fills length 5 != 6`) — reproduced
  identically at clean HEAD via stash; not a Step 3B regression. The other
  three goldens were already failing at HEAD per Step 3A records.
- Not run: training, full-seed backtests, live/paper systems (per brief).

## Constraints honored

- Worked on `main`; no push; no edits to `../poly-maker`,
  `../polymarket-collector`, or protected on-chain files.
- No full-suite run during a live map; no new experiment-mode system.
- `model_dir` is excluded from `schedule_map_sha256`; model identity lives
  in `model_sha256_by_dir`/`model_sha256` instead.
