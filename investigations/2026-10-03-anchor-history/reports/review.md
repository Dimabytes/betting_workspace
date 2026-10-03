# Review: prior anchor + shared HistoryPolicy
Status: FINAL

Commits: `73b529e3` (prior anchor), `ea5367b9` (HistoryPolicy). Read-only on `esports-trader`. Proof script: `work/review/check_policy.py`, run from the esports-trader root as `uv run python <that path>`.

## Findings

1. **bug** — `src/backtest/signals.py:526-535` and `src/backtest/signals.py:754-761`. The receive gap from the connect tick marks the next tick recovery-stale, so that tick is not a decision. Live does not do this.

   Live records the first snapshot and returns before `FreshnessWatchdog.consume` (`src/trader/match_worker.py:235-247`). `consume` is what arms the 45s exit timer (`src/trader/session_engine.py:135-140`, `EXIT_FEED_STALE_SECONDS = 45.0` at `src/shared/constants/strategy.py:44`). The second tick is the first `consume`, and it is fresh no matter how long the connect gap was. Plan 02 says the same thing: the first feed tick never decides, and the gap from that tick does not stale the next one.

   Both backtest paths do the first half and miss the second. They force `stale[first] = True` (connect, not a decision), then run `recovery_stale_mask` on the full surviving timestamp list, whose second entry is `gap(first, second) > 45`. The comment at `signals.py:533-534` says the first tick never goes stale; the mask on the next line of the loop still stales whatever follows it.

   Concrete case, from the proof script, GRID policy, both ticks history-valid (`check_required_lags` is true: seconds 4 and 5 are below `start_second` 60, so they never join the tape, and second 75's lag targets are all below 60):

   - LoL start hole the plan measured (tick at game-second 4, received t=1000; next at 75, received t=1071, gap 71s): `feed=(0, 1) stale=(True, True)`. The tick at +75s does not decide. That is the hole on 259 of 405 LoL maps.
   - The schedule fixture at `tests/test_feed_schedules.py:854-872` (received 100 then 200): `stale=(True, True)` and the test asserts `timestamps_ns == ()`. The second tick should be the first decision. The test locks the wrong result in.

   Grid-v1 has the same mask (`signals.py:526-535`). It shows up whenever the second kept cadence row is more than 45s after the first. Archive replays hit it on every map whose second real tick is that far out.

   Suggested fix: build the recovery mask from `feed_ts[1:]` and write it onto feed positions `[1:]`. Leave position 0 forced stale. The second surviving tick is then the first consume (never stale); later gaps still count from the previous surviving tick, which is what `recovery_stale_mask` is for. Do it in both `_shifted_feed` and `_schedule_feed_status`. Change `test_schedule_signals_drop_recovery_stale_ticks_from_decisions` so the tick 100s after connect is a decision, and add a third tick more than 45s after that one as the stale case.

2. **bug** — `src/shared/utils/dota_features.py:122-126`. Any catalog that is not exactly the 70-column no-XP list gets `GRID_HISTORY_POLICY` (`start_second=60`, gap 30, `drop_gap_ticks=True`). A catalog with no history columns still loses ticks to that drop. Backtest selects the policy from those columns (`src/backtest/signals.py:587-588` for grid-v1, `src/backtest/signals.py:796` for schedules), so this does not follow the directory path. That part of the extra check holds for a real 77/70 catalog under `data/experiments/...`: exact XP columns → GRID, exact no-XP columns → Oddin.

   The old archives do not. Proof script, reading `model.json`:

   - `data/new_model/archive/research/20260930T180848Z`: 12 columns, 0 history columns → GRID, `drop=True`.
   - `data/new_model/archive/research-noxp/20260930T180911Z`: 11 columns, 0 history columns → GRID, `drop=True` (not Oddin).

   Concrete failure: after the first taped second, a tick at 700 whose tape is only `{60, 300, 700}` is invalid under GRID (`tests/test_dota_features.py:380-385` is this case). Those snapshots stay on the tape and the tick leaves the feed. The 12-column model never reads `game_change_*` / `game_total_*`, so the tick should still be predicted. Plan 02 step 5 puts these archives on the research paths for the oldcat12 run; that path does not go through the CLI pin, and the drop fires. On a per-second Oddin tape the drop usually does not fire (a pivot is always within 30s); one hole wider than 30s drops an 11-column tick the same way.

   The flag path cannot be used as a workaround. `_pin_cli_model_dir` (`src/backtest/run.py:1266-1268`, unchanged in these commits, from `2d83928e`) rejects both archives before a run starts. Proof script: `require_catalog_features` raises `--model-dir is not the 77-column catalog: .../20260930T180848Z` and `--model-dir is not the 70-column catalog: .../20260930T180911Z`.

   Live does not drop these ticks. `ModelServer` refuses a catalog whose `features` are not the profile's 77 or 70 (`src/trader/model_server.py:285-286`) before `SnapshotHistory` is asked to skip a tick. The worker's policy is the profile constant (`src/trader/game_profile.py:68` and `:77`), not `model.json`.

   Suggested fix: if `feature_columns` contains none of `DOTA_HISTORY_FEATURE_NAMES`, return a policy with `drop_gap_ticks=False` (start second does not matter once drop is off). Keep exact-77 → GRID and exact-70 → Oddin. Teach the pin to accept a catalog with no history columns on the matching flag (12-column XP on `--model-dir`, 11-column no-XP on `--model-dir-noxp`) so the oldcat12 run can go through the flags.

3. **plan-mismatch** — `src/lol/06_train_model.py:141-142`. LoL training now passes `max_pivot_gap_seconds=30` (nearest, either side). Plan 02 step 4 says the training tolerance does not change, because the tapes are complete and the points are exact. Dota training does that: `max_pivot_gap_seconds=0.0` at `src/train_model/train_model.py:202`, `:210`, and `:309`. LoL previously passed `GRID_FEED_STALE_SECONDS` (16, last-at-or-before).

   On a complete per-second tape, gap 0, 16, and 30 all hit the exact second, so the published rows do not move. A missing lag second changes them. Decision at 200, target 140, tape has 120 and 155: the old 16s backward lookup is NaN (140−120=20), the new 30s nearest lookup uses 155.

   Suggested fix: pass `0.0`, same as Dota. `start_second=60` stays.

4. **test-gap** — `tests/test_trader_match_lifecycle.py:125-154`. Plan 02 step 2 asks for a test beside `test_feed_timeout_keeps_fair_and_sell_until_exit_timeout` (`:934`) where a history-gap tick does not touch the SELL and does not restart the watchdog. `test_history_gap_tick_journals_and_keeps_its_snapshot` checks the journal row, that the model is not called, and that second 500 stays on the tape with `_first_second == 60`. It never reads the cell, the resting SELL, or the watchdog generation.

   The implementation does the right thing: `_on_event` returns before `consume` (`src/trader/match_worker.py:426-433`), so the entry timer (16s, cancel BUY) and exit timer (45s, clear fair / pull SELL) keep running from the last valid tick. A regression that called `consume` on the gap tick, or that cleared the cell, would stay green.

   Suggested fix: arm the watchdog, rest a SELL, send the gap tick, assert the watchdog generation is unchanged, the cell fair is unchanged, and the only new journal row is `history_gap`.

5. **test-gap** — plan 02 step 3's other two tests are missing, and the first-tick constants are only half pinned.

   - An invalid history tick is removed from the feed (`signals.py:519-524` and `:751-752`), so `_sync_signal` (`src/backtest/strategy.py:1110-1118`) is not called for it and does not `_clear_signal`. Nothing asserts that a prior decision is still the active signal after that tick. The schedule test that was added asserts the connect-gap bug in finding 1 instead.
   - `DOTA_GRID_V1_FIRST_TICK_SECOND = 48` and `LOL_GRID_V1_FIRST_TICK_SECOND = 76` (`signals.py:70-71`) are applied at `signals.py:572-576`, before `select_cadence_rows` and before `_grid_v1_kill_gates` (`:628`). Dota fixtures start at dataset second 58, which is game-second 48 given `BACKTEST_LAG_SECONDS = 10`. LoL fixtures start at 76 (`tests/test_lol_backtest.py:676`). A constant lowered toward 0 would not fail those tests. A row at game-second 47 (Dota) or 75 (LoL) is never shown to be dropped while 48/76 is kept.

   Suggested fix: one schedule test with a decision, then a history-gap tick, then a book time still inside the previous signal, asserting the signal was not cleared. One grid-v1 test per game that a row one second before 48/76 is absent and the row at 48/76 remains the connect tick.

6. **test-gap** — `src/collect/s05a_fetch_prices_history.py:113-115`. Plan 01's function test is real (`tests/test_match_time.py:54-66`: pause 120s at −40 subtracted, pause at −200 ignored, empty list is −90, round-trip through `get_horn_datetime`). The load path is not. `load_anchors` skips the row when `pauses is None` instead of writing the raw horn. Restoring `anchors[condition_id] = horn_unix` would not fail any test. The plan's three cases are covered; this is the branch that was the bug.

   Suggested fix: a unit test that calls the skip with `pauses is None` and with a known pause list, without touching the `collect_quotes` tests the plan says to leave alone. Monkeypatch the parquet reads.

7. **nit** — `tests/test_dota_features.py:144`. The test is still named `test_snapshot_history_takes_last_received_at_or_before_target`. The lookup is now the nearest taped second on either side (`dota_features.py:208-230`). The body of that test still passes; the name describes the rule this commit removed.

## Categories with no finding

- **maintainability.** `HistoryPolicy`, `ScheduleFeedStatus`, and `StrategyCatalog.history` are frozen dataclasses. New arguments are required. `get_spawn_unix_from_horn` matches the existing `get_horn_datetime` naming. No dead branch and no new `dict[str, Any]` or anonymous pair. The two copies of the stale assembly are finding 1, not a separate abstraction.

## Checked, holds

- Plan 01 anchor math. `get_spawn_unix_from_horn` is `horn_unix - 90 - get_paused_seconds_before(pauses, 0)` (`src/shared/utils/match_time.py:58-60`). Proof script: empty pauses → −90; a 7s pause at time −90 → −97; a 7s pause at −91 → −90. That is the plan's [−90, 0). Spawn rows still copy the GRID spawn and do not read pauses (`s05a_fetch_prices_history.py:106-109`). No-spawn pauses are OpenDota, else that row's schedule, and only for rows without a spawn (`:70-90`), matching `resolve_pauses` (`src/collect/s06_publish_catalog.py:81-100`). `pauses is None` writes no anchor (`:113-114`). Live `_maybe_start_prior` is untouched.
- History rules 1–3 and 4–5 on the tape. Snapshots with `second < start_second` are not recorded (`dota_features.py:312-313`); proof script recorded 59 then 60 and the tape was `[60]`. Lag targets `< start_second` stay NaN; a target equal to `start_second` is looked up (proof script: decision 120, target 60, `game_change_1m` is 0.0). Nearest either side, inclusive gap, tie keeps the earlier second (`dota_features.py:229-230`). `drop_gap_ticks=False` always returns true (`:339`). Warmup is `target < first`, and `_first_second` survives eviction (`:318-319`, lifecycle test asserts `_first_second == 60` after 60 and 70 are evicted). A gap tick is recorded in `run` before `_on_event` (`match_worker.py:235`, `:268-271`), then journaled and skipped. Terminal snapshots are not recorded and still go through `consume`.
- Policy table for the three live catalogs. `GRID_HISTORY_POLICY` is (60, 30, drop on), `ODDIN_HISTORY_POLICY` is (−60, 15, drop off) (`dota_features.py:86-93`). `dota-map` and `lol-map` and Steam (primary) use GRID; `dota-oddin-map` uses Oddin (`game_profile.py:63-78`, `:91-96`). No remaining last-before-within-16s lookup; the only `searchsorted` is the two-sided nearest pivot (`dota_features.py:220`). On a per-second tape the Oddin pivots are the exact seconds (`tests/test_dota_features.py:421-429`).
- Train `start_second` follows the catalog role, not the directory. Default pairs and `--model-dir` / `--no-xp` both go through `history_policy_for_columns` (`train_model.py:196`, `:308`, `:384`): XP → 60, no-XP → −60. LoL training passes `start_second=60` (`06_train_model.py:141`). The LoL gap constant is finding 3; the start second is right.
- Grid-v1 first-tick filter and archive history-gap handling, apart from finding 1. Rows with game-second `< 48` (Dota) or `< 76` (LoL) are removed before cadence and before kill gates (`signals.py:572-576`, `:585`, `:628`). History-invalid ticks stay on the tape via `record_mask` and leave `feed_timestamps_ns`. `recovery_stale_mask` then sees only surviving ticks, so a hole of dropped ticks counts from the last valid one (`:523-531`, `:754-759`). The connect tick is the exception, and it is finding 1.
