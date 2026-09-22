# Step 3A review fix — response to `step-3a-review.md`

Commit: `21274237 refactor: [step 3A] - typed feed plans and strategy-owned clock tape`
on `esports-trader` `main`, not pushed. No Step 3B work started.

All four blockers fixed, all four should-fixes fixed, all nits applied or
verified unnecessary. Nothing skipped.

## Blockers

### 1. `MatchFeedPlan` is a real tagged union — FIXED

`MatchFeedPlan` is now `SchedulePlan | GridV1Plan`
(`src/backtest/feed_schedules.py`). `SchedulePlan` carries a required
`ScheduleBinding`; `GridV1Plan` is `match_id` + `model_dir`. `FeedMode`,
`mode`, the nullable `binding`, and every `binding is None` check and
"schedule plan without binding" raise are gone; dispatch is `isinstance`
at each consumer (`build_current_kernels`, `build_strategy_configs`,
`_resolve_run_signals`, `schedule_archive_dirs`, `schedule_map_sha256`,
`signal_provenance`). `_bind_schedule` returns `ScheduleBinding | str`
instead of an anonymous `(binding, reason)` tuple, and
`build_schedule_match_signals` takes `Mapping[int, SchedulePlan]` — the
schedule map no longer travels beside the plans.

### 2. Observed clock moved off `MatchSignals` — FIXED

`MatchSignals` is back to four fields (`feed_timestamps_ns`,
`timestamps_ns`, `predicted_deltas`, `dataset_market_ps`). The schedule
clock now lives in `ObservedClockTape` (`game_seconds`, `paused`,
`terminal`) on `DotaMakerConfig.observed_clock: ObservedClockTape | None`,
length-aligned against `feed_timestamps_ns` in `__post_init__`.
`build_strategy_configs` takes `plans` and builds the tape from
`plan.binding.schedule.ticks` (and derives `buy_cutoff_ns`/`game_end_ns`
from the same ticks); `_clock_at` reads `self._config.observed_clock`.
The msgspec JSON round-trip of a populated tape was verified directly.
All old `MatchSignals` ctors shrank instead of growing;
`docs/experiments/ensemble/stage2.py` already used the four-field shape
and needed no change (verified).

### 3. Step 3A orchestration out of `run.py` — FIXED

`run.py` dropped 2034 → 1940 lines; seven helpers left:

- `load_game_feature_rows` → `signals.py` (public; LoL frame now also
  filters to the requested schedule ids, closing the nit),
- `resolve_feed_plans`, `reject_schedule_flags`, `schedule_archive_dirs`
  → `feed_schedules.py` (flag rejection is now a `ValueError`, not
  `build_parser().error`),
- `read_run_archive_headers` → `live_archives.py`,
- `signal_provenance` / `signal_provenance_map` → `results.py`.

`main()` calls them directly; `_apply_feed_exclusions` (the
selection/coverage surgery) stays in `run.py` since `RunSelection` lives
there. `_resolve_run_signals` keeps its live-archive/current coexistence
for 3B, as instructed.

### 4. `MarketAnchorTape` deleted — FIXED

`MarketAnchorTape`, `market_anchor_tape`, and `anchor_at` are gone from
`signals.py`. `_match_schedule_decisions` anchors each tick with the
canonical `lookup_reference_mid(series, tick.received_ns)` on the shared
`MidSeries` map straight from `lookups.mids`. The tape fixture test was
deleted (it only re-tested the canonical helper); the causality behavior
is covered by `test_schedule_signals_use_causal_anchor_and_tick_times`,
which still asserts tick 0 stays feed-only when no earlier mid exists.

## Should-fixes

### 5. `SnapshotFeatures` split from `StateFeatures` — FIXED

`shared/types/dataset.py`: `SnapshotFeatures` holds the nine STRATZ
snapshot fields; `StateFeatures(SnapshotFeatures)` adds `radiant_win`;
`GameFeatureRow(SnapshotFeatures)` composes the same block. In
`prepare_dataset.py`, `state_features()` now returns `SnapshotFeatures`,
and both `build_minute_rows` and the validation-row builder add
`radiant_win=state.radiant_win` explicitly — `build_game_feature_rows` is
a single `GameFeatureRow(match_id=..., game_second=..., **state_features(state), ...)`
comprehension; no duplicated field list remains.

### 6. `ArchiveJoinInput` is a real two-variant union — FIXED

`DotaArchiveJoin(catalog)` and `LolArchiveJoin(audit)` live in
`feed_schedules.py`; `ArchiveJoinInput = DotaArchiveJoin | LolArchiveJoin`.
`RunSelection.archive_join` is now always populated (the join rows exist
in both live-since paths too), so the field is non-optional and the
`assert join is not None and join.X is not None` checks are gone.
`resolve_feed_plans` dispatches on the variant.

### 7. Identity mismatch is an exclusion reason — FIXED

Both resolvers now return `"schedule_identity_mismatch"` instead of
raising `ValueError` — same channel as fingerprint/missing-file
exclusions, so the match drops with a logged reason rather than crashing
the run. The Dota test was converted to assert the exclusion, and a new
`test_lol_schedule_identity_mismatch_excludes` covers the review's named
LoL case (index `condition_id` matches, persisted schedule identity does
not).

### 8. Shared `_finish_candidates` ladder — FIXED

`_finish_candidates(candidates) -> _ArchiveRow | str | None` in
`feed_schedules.py` returns the admitted row, else the first candidate's
`archive:{admission}` reason, else `None` (unbound). Both
`_resolve_dota_match` (unlinked branch) and `_resolve_lol_match` use it;
the duplicated admit-then-refuse loops are gone.

## Nits

- `parser.error` → `reject_schedule_flags` raises `ValueError` (same
  channel as the `--match-id` exclusion).
- LoL feature frame filtered to schedule ids inside
  `load_game_feature_rows` (both games filter now).
- Redundant `int(match_id)` wraps dropped in the resolvers.
- `stage2.py` needed no change (four-field `MatchSignals` already).

## Files changed

- `src/backtest/feed_schedules.py` — union plans, join variants,
  `_finish_candidates`, `_resolve_*_match` returning `MatchFeedPlan | str`,
  plus moved-in `resolve_feed_plans` / `reject_schedule_flags` /
  `schedule_archive_dirs`.
- `src/backtest/signals.py` — `MatchSignals` four fields, anchor tape
  deleted, `build_schedule_match_signals(plans, feature_rows, mids)`,
  moved-in `load_game_feature_rows`.
- `src/backtest/strategy.py` — `ObservedClockTape`, `observed_clock`
  config field + `__post_init__` alignment, `_clock_at` reads the tape.
- `src/backtest/run.py` — helpers removed, `build_strategy_configs` /
  `run_batch` / `replay_matches` take `plans`, `main` calls the moved
  helpers.
- `src/backtest/results.py` — `signal_provenance`, `signal_provenance_map`.
- `src/backtest/live_archives.py` — `read_run_archive_headers`, four-field
  `MatchSignals` ctor.
- `src/shared/types/dataset.py` — `SnapshotFeatures` split.
- `src/prepare_dataset/prepare_dataset.py` — `state_features` returns
  `SnapshotFeatures`; row builders compose it.
- Tests: `test_feed_schedules.py` (rewritten to the union + new builder
  signature, new LoL identity test and observed-clock wiring test),
  `test_backtest_maker.py` (`observed_clock` param), `test_backtest.py`,
  `test_backtest_signals.py`, `test_backtest_validation.py`,
  `test_extraction_oracle.py`, `test_live_archives.py` (ctor updates).

## Verification

- Targeted tests (all files above):
  `pytest tests/test_feed_schedules.py tests/test_backtest_signals.py
  tests/test_backtest_maker.py tests/test_prepare_dataset.py
  tests/test_backtest.py tests/test_live_archives.py
  tests/test_backtest_validation.py tests/test_lol_backtest.py
  tests/test_backtest_postprocess.py tests/test_extraction_oracle.py`
  → **364 passed**. No full suite run per the brief.
- `uv run ruff check src/ tests/` → clean; `uv run ruff format` applied to
  touched files.
- `uv run basedpyright` → **0 errors, 0 warnings**.
- `make lint` (pre-commit) ran clean at commit time — ruff, format,
  basedpyright hooks all passed on the staged set.
- Behavior preserved: stale ticks stay on the feed tape but out of
  decisions; missing feature rows still fail closed; unlinked/stale/
  invalid archives still exclude; grid-v1 only when no candidate exists;
  identity mismatch now also excludes instead of crashing (intended per
  the review).

## Notes

- Unrelated dirty/untracked files left untouched
  (`betting_workspace/.cursor/rules/herdr-orchestrate.mdc`, untracked docs).
- Not pushed; no Step 3B work performed.
