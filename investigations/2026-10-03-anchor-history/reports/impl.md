# Implementation report: prior anchor + shared HistoryPolicy
Status: FINAL

## Commits

- `73b529e3` Anchor pregame prior at game-second -90 for spawn-less archives
- `ea5367b9` Share one HistoryPolicy across training, backtest, and live history
- `0da22250` Fix history-policy review findings and historyless catalogs
- `e6ca2627` Keep grid-v1 matches with no feed-eligible rows instead of crashing

## Files changed

Part 1 (73b529e3): `src/shared/utils/match_time.py`, `src/collect/s05a_fetch_prices_history.py`, `tests/test_match_time.py`

Part 2 (ea5367b9):

- Core: `src/shared/utils/dota_features.py`
- Live: `src/trader/game_profile.py`, `src/trader/match_worker.py`, `src/trader/session_journal.py`, `src/trader/archive_types.py`
- Backtest: `src/backtest/signals.py`, `src/backtest/run.py`, `src/backtest/strategy.py`
- Training/scripts: `src/train_model/train_model.py`, `src/lol/06_train_model.py`, `scripts/reconstruct_lol_networth.py`, `scripts/score_gap_experiment.py`, `docs/experiments/ensemble/stage2.py`
- Tests: `test_dota_features.py`, `test_backtest_signals.py`, `test_feed_schedules.py`, `test_backtest.py`, `test_lol_backtest.py`, `test_lol_train_model.py`, `test_model_server.py`, `test_trader_game_profile.py`, `test_trader_match_lifecycle.py`
- Goldens: 7 of 8 `tests/fixtures/follow300_changes/smoke/*_seed0.json` recaptured via `scripts/capture_follow300_smoke.py` (`dota_9007208887` and `inputs.json` byte-identical — inputs did not drift, the policy did)

## What each plan item became

### Plan 01 — prior anchor

- `match_time.py`: `get_spawn_unix_from_horn(horn_unix, pauses)` = `horn_unix - HORN_OFFSET_SECONDS - get_paused_seconds_before(pauses, 0)`, the inverse of `get_horn_datetime`.
- `s05a_fetch_prices_history.py::load_anchors`: no-spawn rows resolve pauses OpenDota-first (`try_load_opendota_pauses`), else that archive's schedule (`pauses_from_schedule` via `schedule_path_for`); schedules are read only for rows without spawn and without an OpenDota file. `pauses is None` → no anchor written — the row is skipped rather than silently anchored on the raw horn.
- `tests/test_match_time.py::test_spawn_unix_from_horn_inverts_get_horn_datetime`: pause at -40 subtracted, pause at -200 ignored, empty → horn-90, round-trip vs `get_horn_datetime`.

### Plan 02 — shared HistoryPolicy

- `dota_features.py`: `HistoryPolicy(start_second, max_pivot_gap_seconds, drop_gap_ticks)`; `GRID_HISTORY_POLICY(60, 30s, drop=on)`; `ODDIN_HISTORY_POLICY(-60, 15s, drop=off)`; `history_policy_for_columns` maps the no-XP catalog to Oddin and everything else to GRID. `history_feature_block` now clips tape rows below `start_second`, resolves each lag target to the nearest taped second in either direction within the pivot gap, and yields NaN for below-start targets or missing pivots. `SnapshotHistory` takes the policy, skips records below `start_second`, keeps `_first_second` across eviction as the warmup boundary, and exposes `check_required_lags`. `replay_tape_history`/`replay_tape_validity` share the same math so stored-tape vectorization matches the live incremental tape.
- Live: `strategy_catalog(game, feed_source)` returns the catalog a feed trades under (satellite when quoted, else primary); each `StrategyCatalog` carries its `history` policy (dota GRID + oddin satellite, lol GRID). `MatchWorker` builds `SnapshotHistory` from that policy, records every nonterminal snapshot, and on a failed `check_required_lags` writes a `history_gap` journal record (`SessionHistoryGapRecord`) and returns without quoting — the snapshot stays taped as a future pivot.
- Backtest grid-v1: `build_match_signals` drops rows before the archive-observed first feed tick (`DOTA_GRID_V1_FIRST_TICK_SECOND=48`, `LOL_GRID_V1_FIRST_TICK_SECOND=76`), replays tape validity per cadence row, removes history-invalid ticks from the feed, marks the first surviving tick connect-only (unarmed watchdog), and feeds `recovery_stale_mask` the surviving feed timestamps so gaps count from the last valid tick.
- Backtest schedules: `build_schedule_match_signals` does the same per recorded tick via `_schedule_feed_status` (`ScheduleFeedStatus(feed_ticks, stale)`); terminal ticks never record to the tape but remain feed/decision ticks.
- `MatchSignals.feed_tick_indices` maps each kept feed tick to its source tick position; `build_strategy_configs` uses it for the observed-clock tape, buy-cutoff lookup, and terminal selection. `strategy.py` passes `()` since the config already carries resolved timestamps.
- Training: `train_model.py` and `lol/06_train_model.py` resolve the policy from the model catalog and replay through the shared helpers; `reconstruct_lol_networth.py`, `score_gap_experiment.py`, and `ensemble/stage2.py` call the new signatures.

## Tests run

- `PYTHONPATH=src:scripts:../prediction-market-backtesting PYTEST_N=8 uv run pytest -x -q` → **2681 passed**, 2 subtests passed, 0 failed (205s).
- `uv run python -m pre_commit run` (on changed files) → ruff check, ruff format, basedpyright, whitespace/EOF hooks all pass.
- `scripts/capture_follow300_smoke.py` run once to recapture goldens; `tests/test_follow300_replay.py` → 11 passed.

## Verification numbers

- Suite: 2681 passed / 0 failed.
- Golden drift: `dota_8837869969` fills 43 → 26 (largest mover); two of nine fixture files unchanged.
- LoL fixture: `test_lol_predict_does_not_lag_second` rebased to seconds 76 (connect) / 85 (first cadence-kept decision for match 101, seed 0).

## Deviations

- `test_parse_args_accepts_model_dir` was already broken at HEAD (the `--model-dir`/`--model-dir-noxp` pairing landed in `2d83928e` without a test update); fixed by pointing the test at `--game lol`, where the dota-only pairing does not apply.
- The end-of-file hook wanted trailing newlines on tracked `data/backtests/lol_maker/...` manifests; those were reverted as unrelated artifacts — the hook only flags them under `--all-files`.
- Schedule-path stale mask semantics settled in the fix round: the recovery mask runs on `feed_ts[1:]` so the second tick (the first watchdog consume) stays fresh across any connect gap; see Fix round finding 1.

## Open questions

- Whether live should treat a long history-gap stretch as a feed-health signal beyond the per-tick journal record; today it only journals.

## Fix round

Commit `0da22250` — review-finding dispositions:

1. **Connect-gap stale (both backtest paths) — fixed.** `recovery_stale_mask` now runs over `feed_ts[1:]` / `feed_ticks[1:]` with the first surviving feed position forced stale: live arms the watchdog on the first `consume`, so the second tick is fresh regardless of the connect gap, and later gaps count from the last valid tick. Locked by the schedule test (tick 100s after connect decides; a third tick >45s later goes stale).
2. **Historyless catalogs — fixed.** `history_policy_for_columns` returns `NO_HISTORY_POLICY` (drop off) when the catalog is disjoint from `DOTA_HISTORY_FEATURE_NAMES`; full XP stays GRID, full no-XP stays Oddin. `require_catalog_features` accepts any historyless catalog on the matching flag via the `radiant_xp_adv` marker, so the old 12-column XP and 11-column no-XP research catalogs run under `--model-dir`/`--model-dir-noxp`; wrong-side and partial-history catalogs still reject.
3. **LoL training pivot gap — fixed.** `load_lol_dataset` attaches features with `max_pivot_gap_seconds=0.0`, matching the Dota trainer's exact-second semantics on the complete Stage 05 tape.
4. **Live watchdog/signal invariants — verified, no code change.** The lifecycle test now asserts a history-gap tick adds only the `history_gap` journal row: no model call, no watchdog-generation bump, published fair unchanged, resting SELL kept, snapshot stays taped.
5. **Signal clearing + first-tick boundaries — fixed in tests.** Schedule test: a history-invalid tick is absent from `feed_timestamps_ns` and does not clear the prior signal. Grid-v1 boundary tests per game (Dota <48 dropped, 48 connect-only; LoL <76 dropped, 76 connect-only).
6. **Anchor branches — covered.** `load_anchors` test monkeypatches `_fallback_pauses`: known pre-horn pause subtracts from horn-90, `pauses=None` skips the row, spawn-backed rows keep the spawn timestamp, raw horn never anchors.
7. **Test naming — done.** Renamed to `test_snapshot_history_uses_the_nearest_taped_second_within_budget`.

Orchestrator items:

- **A. Invalid-tick share** (`work/impl/verify_history.py`, 700 published GRID schedules): Dota 1/18035 in-window (second < `BUY_CUTOFF_SECOND=480`) = **0.0055%** ≈ plan's 0.01%; LoL 20/33242 = **0.0602%** ≈ plan's 0.06%. Whole-feed shares are 0.41%/0.22% — the rest are late-game receive holes past the entry window, correctly dropped.
- **B. Historyless catalogs** — covered above (finding 2).
- **C. LoL `--model-dir`** — added to `src/lol/06_train_model.py`: trains/publishes only the research ensemble into `<dir>` with `<dir>/../archive`, never touches the live research dir or production; refuses live dirs like the Dota trainer. Covered by `test_main_model_dir_publishes_research_only_and_refuses_live_dirs`.
- **LGD–Xtreme `grid-3011816-m3`** replayed through `SnapshotHistory` (583 feed events, 73 taped seconds, first=86): zero would-drop ticks, and no unexpected NaN columns at seconds 144/147/188 — only below-`start_second` targets stay NaN by contract.

Verification: full suite `2689 passed, 2 subtests` (202s); `pre-commit run --files` on all changed files green (ruff, ruff-format, basedpyright, whitespace/EOF). Scratch script lives at `investigations/2026-10-03-anchor-history/work/impl/verify_history.py`.

## Fix round 2

Commit `e6ca2627` — backtest crash on maps with no feed-eligible rows.

- **Root cause.** `build_match_signals` computed `missing = requested - present` *after* the archive first-tick cut (`second - lag >= 48`). Matches `8852555586` (one usable row, second 35) and `8971371061` (one usable row, second 47) had every usable validation row below the cut at `source_lag_seconds=10` — all other rows are `market_status != "ok"` — so they looked absent and the shard aborted. Pre-change these maps kept one cadence row and decided from it; under the new semantics their feed is empty, which is the live truth (GRID never delivered there).
- **Fix.** `present`/`missing` now computed on the unfiltered usable rows — genuinely-absent matches still raise — and the result dict iterates `present`, so an emptied map returns a fully-empty `MatchSignals`. The engine already handles that: `run.py` logs `no priced signal ticks` and records `terminated_early` (`EMPTY_SIGNAL_TAPE_STOP_REASON`), keeping the map in the universe with zero decisions — the same handling schedule-bound maps with no decisions always had. The schedule path needed no change (it returns per-plan entries unconditionally).
- **LoL.** Same `build_match_signals` with `LOL_GRID_V1_FIRST_TICK_SECOND=76`; the fix is game-agnostic and covers it.
- **Test.** `test_grid_v1_match_with_rows_only_before_first_tick_gets_empty_signals` — verified it fails on the old code with the production error (`ValueError ... matches [1]`) and passes on the fix; a healthy match in the same call still decides.
- **Smoke check.** `build_match_signals` on the real validation rows with the real model dir (`data/experiments/hist-policy-20261003/model`, lag 10, seed-0 cadence timing) for `[8852555586, 8971371061]` → both return `feed 0, decisions 0, kills 0`, no exception.

Verification: full suite `2690 passed, 2 subtests` (204s); `pre-commit run --files` green.

## Fix round 3

Commit `4db718de` — structural review findings, no behavior change.

- **Feed-status dedup (blocker).** `_shifted_feed` and `_schedule_feed_status` carried the same 15-line validity→stale logic. Now `backtest/feed_schedules.py` owns one walk — `walk_feed_ticks` yields `FeedTick(position, tape, valid, stale)` per position: snapshot records before the history check, the first surviving tick is connect-only (stale), the second is the first watchdog consume and stays fresh whatever the connect gap, later gaps measure from the last surviving tick. `feed_status` collects it into `FeedStatus(feed_positions, stale)`; `schedule_feed_status(plan, policy)` wraps a `SchedulePlan`. `recovery_stale_mask`, `ScheduleFeedStatus`, `_schedule_feed_status`, `PricedDecisions`, `_priced_decision_rows`, `_attach_decision_features` deleted; `grid_exit_age_seconds` moved next to `entry_stale_seconds`.
- **`feed_tick_indices` dropped from `MatchSignals`.** Grid-v1 computed it for nobody; the schedule path no longer transports it — `run.py`'s `build_strategy_configs` calls `schedule_feed_status(plan, policy)` itself (policy cached per `model_dir`) to rebuild kept ticks and the observed clock.
- **Catalog policy on the XP marker.** `history_policy_for_columns` was list-equality (`== DOTA_NOXP_FEATURE_COLUMNS`) while `require_catalog_features` pinned on `radiant_xp_adv`. One rule now: no history columns → `NO_HISTORY_POLICY`; marker present → `GRID`; absent → `ODDIN` — a future noxp variant with a different window count can't silently land on GRID. `StrategyCatalog.history` is now a property over `features`, so `GAME_PROFILES` derives through the same function instead of declaring a second truth.
- **`max_pivot_gap_seconds` removed from `attach_history_features`/`attach_catalog_features`** — complete training tapes use exact pivots (`0.0`) unconditionally. `scripts/score_gap_experiment.py`'s LoL call stopped passing the GRID gap and now matches the trainer.
- **Archive pauses deduped.** `load_archive_pauses` moved to `archive_index/schedule.py` (kept the `None`=unobserved tri-state); `s05a._fallback_pauses` is now spawnless-frame → OpenDota map → `archive.setdefault` fill (OpenDota precedence preserved); `s06`/`s07` import it.
- **`_nearest_pivot_indices`** → clipped before/after indices + `abs` distances; empty-side cases fold into a same-index tie that breaks to the earlier side. Verified identical semantics incl. ties, gap bound, and empty tape.
- **`check_required_lags`** → one vectorized `_nearest_pivot_indices` call over the five targets after masking warmup/sub-`start_second` targets (`>= max(start_second, first_second)`).
- **Single-pass `_shifted_feed`.** Grid-v1 now walks the tape once: per tick it records, checks, marks feed status, prices the mid (LoL), and derives the history row in place — the second replay for derived blocks is gone.
- **Minor.** `GRID_V1_FIRST_TICK_SECOND: dict[BacktestGame, int]` replaces the game ternary; `lol/06_train_model.main` collapsed to one `if model_dir is not None` block. The `match_worker._on_event` dead journal guard stays — pre-existing pattern, non-blocker per the review.
- **Tests.** Marker-rule cases added (reordered XP list → GRID; noxp+extra-column variant → ODDIN); the schedule observed-clock test points its plan at a real `_model_dir` since `build_strategy_configs` now resolves the policy itself; `check_policy.py` (review script) updated to `schedule_feed_status`/`feed_positions`.

Verification: full suite `2690 passed, 2 subtests` (240s); `pre-commit run` green; `verify_history.py` sweep unchanged (dota `1/18035 = 0.0055%` in-window, lol `20/33242 = 0.0602%`; grid-3011816-m3 identical); `check_policy.py` proof points unchanged (connect stale / second fresh, catalog pins).

## Fix round 4

Commit `707bc3ce` — follow-up structural findings, no behavior change.

- **`replay_tape_validity` deleted** with its dedicated test; the walk now reports `tick.valid` directly. `replay_tape_history`/`_walk_tape` stay — `scripts/reconstruct_lol_networth.py` still uses them.
- **Schedule path: 3 walks → 2.** `_match_schedule_decisions` + `_schedule_tape_history` + the `schedule_feed_status` call in `build_schedule_match_signals` folded into one `_schedule_replay` over `walk_feed_ticks`, returning `ScheduleReplay(feed_timestamps_ns, decisions, history)` — identical filters, now reading `tick.derived(...)` at decision positions in the same pass that computes staleness. The residual second walk is `run.py`'s `schedule_feed_status` per plan (marked optional by the review; a plan-carried status would couple plan construction to model.json reads).
- **`functools.cache` on `load_model_feature_columns` (→ `tuple[str, ...]`) and new `history_policy_for_model_dir`** — kills `policies_by_dir` in `run.py` and `columns_by_dir` in `build_schedule_match_signals`; `df[tuple]` indexers switched to `list(...)`.
- **`FeedTick.tape` → private `_tape` + lazy `derived(second, levels)`** — the "read before next yield" contract is now a method call, not a shared-mutable handle.
- **Nit:** `lol/06_train_model.main` reads `LOL_GAME_FEATURES_PATH` once (`tape` hoisted).
- **Tests/work:** gap-tick test rewritten against `walk_feed_ticks`; `verify_history.py` migrated off the deleted helper.

Verification: full suite `2690 passed` (247s); `pre-commit run` green; sweep unchanged (dota `0.0055%` in-window, lol `0.0602%`; grid-3011816-m3 identical).

## Fix round 5

Commit `b6e39024` — dead code after the round-4 fold.

- **`ScheduleDecision.tick_index` deleted** — its only reader was `_schedule_tape_history`.
- **`FeedStatus`/`feed_status`/`schedule_feed_status` collapsed to `schedule_feed_positions(plan, policy) -> tuple[int, ...]`** — the sole remaining caller (`run.py`) read only `feed_positions`; `stale` had no consumers. `check_policy.py` switched to `walk_feed_ticks` for its stale printout.

Verification: `test_feed_schedules.py` + `test_backtest_signals.py` green (62); `pre-commit run` green. Full suite skipped per instruction.
