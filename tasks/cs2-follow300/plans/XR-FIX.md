# XR-FIX — cross-review fix pack (F1, F2, F3, F4 code part, F6)

Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, branch `cs2-signal`, HEAD `faebd971`.
Spec: findings F1, F2, F3, F4 (code part), F6 of `orchestrate/reports/xreview-1-xreview.md` and the XREVIEW orchestrator decision in `progress.txt` (line 234).
This is not a feature.json step. Do not change `passes` in feature.json. One commit for the whole pack.
F5 and F7, and the F4 pilot text, are STEP-013 / STEP-014 notes (progress.txt lines 235–236). They are not in this pack.

## 1. What the pack does

1. **F1.** s07 parses ISO time strings with `format="ISO8601"`, so a column with and without a fraction does not crash s07 or the s10 split. s01 and s03 write every UTC string in one format: `YYYY-MM-DDTHH:MM:SS.fffZ`. s10 builds the split frame before it replaces the dataset files.
2. **F2.** s05 marks a halftime-round second as paused when the second is at or after the round `won_at`, not when the raw phase is 3. `s05_check_live.pause_span` selects the break by the same flag.
3. **F3.** CS2 make targets drop the shared `ARGS`. `cs2-universe` reads `CS2_WINDOWS`, `cs2-replays` reads `CS2_REPLAY_ARGS`. No other CS2 stage takes extra arguments.
4. **F4 (code).** s01 drops events that start at or after `CS2_END_TIME` = 2026-10-18T00:00:00Z. s09 exits with code 3 (not 1) when books are pending. `make cs2-prepare` accepts exit 3 from s09 and runs s10.
5. **F6.** s09 writes a `team_a_token_id` column. s09 checks every cached file against the current catalog row and rebuilds a stale one. s10 runs the same check.

Every fix gets a test (section 5).

## 2. Facts I checked

### F1

1. `pd.to_datetime` is called in two places only, both in s07: `assign_split` (`src/cs2/s07_link_readiness.py:68`) and `read_event_months` (`:78`). All other stages parse one string at a time with `parse_utc` (`datetime.fromisoformat`), which accepts both forms (grep `parse_utc|fromisoformat|to_datetime` over `src/cs2`).
2. s10 reaches the crash only through `build_split_frame` → `assign_split` on all `spawn_at` values (`src/cs2/s10_prepare_dataset.py:860`). `split_of` (`:843`) calls `assign_split` on one value, which never crashes.
3. Probe `orchestrate/work/planxr/probe_iso.py`, pandas 2.3.3:
   - `pd.to_datetime(["…04:30.500Z", "…47:41Z", "…38.523000Z"], utc=True)` raises `time data "2026-07-31T10:47:41Z" doesn't match format "%Y-%m-%dT%H:%M:%S.%f%z", at position 1`;
   - the same call with `format="ISO8601"` returns all 3 timestamps.
4. Writers that keep raw strings:
   - s01 `build_event_row` writes `start_time` with `isoformat()` (`src/cs2/s01_build_universe.py:247`). A whole second gives `…:00Z`, a fraction gives `…:38.523000Z`;
   - s03 `build_round_row` copies segment `startedAt` (`src/cs2/s03_fetch_grid_rounds.py:333`). `build_game_row` copies game `startedAt` (`:351`);
   - s06 copies game `started_at` into catalog `spawn_at` (`src/cs2/s06_publish_catalog.py:328`). The catalog goes to STEP-013 code, which may parse it with pandas again.
5. s03 already has `parse_utc` / `format_utc` (`:267`, `:271`). `format_utc` writes milliseconds with `Z`. `compute_ended_at` (`:296`) and s05 round moments already use it.
6. s10 calls `writers.finish()` (`:929`) and then `write_parquet(build_split_frame(written), …)` (`:933`). A failure in `build_split_frame` leaves new datasets next to a missing or old `split.parquet`.

### F2

1. `is_pause_second(phase, timer_s, round_number)` (`src/cs2/s05_parse_replay.py:139`) returns True for `phase == 3` in a halftime round. `build_second_row` (`:279`) calls it with the raw carried phase.
2. `parse_round` (`:470`) builds the second rows before it calls `find_round_clock` (`:421`). `find_round_clock` gives `won_at` = first frame with phase 3 after phase 1 or 2. For the last round it gives `won_at` = GRID segment end.
3. s10 sets phase 3 for every second with `won_s ≤ s` (`src/cs2/s10_prepare_dataset.py:599-607`). `won_s` comes from the round clock `won_at` string.
4. Probe `orchestrate/work/planxr/probe_plant_after_win.py` (the test fixture `bo3_round_173713_11.json`, plant after win as in `test_plant_after_win_is_not_plant`, duration 300 s):
   - round 12: 117 seconds at or after `won_at`, phases {2, 3}, **5 paused**. The other 112 seconds are phase 2 and not paused;
   - round 11: 117 seconds, 0 paused (correct, not a halftime round).
5. `s05_check_live.pause_span(seconds, round_number, phase)` (`src/cs2/s05_check_live.py:279`) selects `paused` rows with one phase. `break_comparisons` asks for phase 3 (`:312`), `freeze_pause_comparisons` for phase 0 (`:351`).
6. `data/cs2/bo3/round_state/` does not exist (`ls` → "No such file or directory"). No cached summary carries the old flag.

### F3

1. Every CS2 target appends `$(ARGS)` (`Makefile:130-158`). `cs2-collect` calls each stage with `$(MAKE)` (`:160-167`), so a command-line `ARGS` reaches every stage.
2. `make -s cs2-collect PY=echo ARGS="--window 2026-04-01:2026-04-07"` prints `--window` on the s02, s03, s04 and s05 lines.
3. Stage options other than directory paths (grep `typer.Option` in `src/cs2/s0*.py`, `s10*.py`):
   - s01: `--window` (repeatable), `--fetch-universe`, `--as-of`;
   - s05: `--workers`, `--limit`, `--fetch`;
   - s02, s03, s04: `--fetch` only;
   - s06, s07, s08, s09, s10: directory options only.
4. Probe `orchestrate/work/planxr/probe.mk` on GNU Make 3.81: `$(foreach window,$(CS2_WINDOWS),--window $(window))` with `CS2_WINDOWS="2026-04-01:2026-04-07 2026-10-01:2026-10-17"` gives `--window 2026-04-01:2026-04-07 --window 2026-10-01:2026-10-17` in a sub-make. Empty `CS2_WINDOWS` gives nothing. `CS2_REPLAY_ARGS` reaches only the target that uses it.

### F4

1. `keep_start` (`src/cs2/s01_build_universe.py:162-167`) has a start bound only (`CS2_START_TIME = 1_760_140_800`, `src/cs2/constants.py:42`).
2. 2026-10-18T00:00:00Z = `1_792_281_600` (`python3 -c "…datetime(2026,10,18,tzinfo=utc).timestamp()"`). PLAN.md "Период данных": validation 04.06 – 17.10.2026.
3. s09 `main` raises `typer.Exit(code=1)` only when `books_pending` is not empty (`src/cs2/s09_build_market_data.py:436-437`). An uncaught exception also exits 1. So the Makefile cannot tell "books pending" from a crash by exit code 1.
4. `cs2-prepare: cs2-market-data` (`Makefile:157`) stops before s10 on any s09 failure. Probe: `S09_EXIT=3 make -s cs2-prepare PY="sh work/planxr/fake_py.sh"` → `make: *** [cs2-market-data] Error 3`, s10 not run.
5. Probe `probe.mk`: recipe `cmd || [ $$? -eq 3 ]` → exit 3 runs the next line and make exits 0; exit 1 stops make with exit 2; exit 0 runs the next line.
6. Two s01 tests use an event at `2026-10-18T00:30:00Z` (`tests/test_cs2_universe.py:148`): `test_markets_get_kind_map_number_and_outcome` (`:184`) expects it kept, `test_window_keeps_only_events_inside_it` (`:232`) expects it outside the window `2026-10-01:2026-10-17`. The end bound drops it, so the fixture dates move (section 4).

### F6

1. `plan_maps` (`src/cs2/s09_build_market_data.py:337`) counts a map as cached when `build_cache_path(…).is_file()`. `CACHE_VERSION` (`:84-95`) hashes constants and `MARKET_SECOND_COLUMNS` only.
2. `Cs2MarketSecondRow` (`:51`) has no team A token. `load_team_books` picks team A by `row.team_a_token_index` (`:181`).
3. s10 `check_market_seconds` (`src/cs2/s10_prepare_dataset.py:501`) checks condition, the second grid and `state_ts_us`, then raises `match <id> rerun cs2-market-data`. `expected_seconds` (`:495`) lives in s10 too. s10 already imports `build_cache_path`, `compute_zero_second`, `compute_last_second`, `second_state_us` from s09 (`:30-35`).
4. s10 copies market columns by name (`market_columns`, `:715-723`). A new s09 column does not reach the datasets.
5. Adding a column to `MARKET_SECOND_COLUMNS` changes `CACHE_VERSION`, so files of the old layout live under another `v<hash>/` folder and s09 never reads them. `data/cs2/market_seconds` is empty today (progress.txt STEP-010).
6. Probe `orchestrate/work/planxr/probe_cache_read.py`: reading 4 columns of a 2,500-row market file takes 0.88 ms (300 files, warm disk). 15,000 maps ≈ 13 s serial.

## 3. Decisions

1. **F1, parse.** Add `format="ISO8601"` to both `pd.to_datetime` calls in s07. s10 needs no parse change: it calls `assign_split`.
2. **F1, one format.** s03 writes game and round `started_at` through `format_utc(parse_utc(…))`. s01 writes `start_time` with `isoformat(timespec="milliseconds")`. Then `spawn_at`, `start_time` and round starts all have milliseconds and `Z`. Reason: STEP-013 reads the catalog and may parse `spawn_at` with pandas. Readers still accept any ISO form, so old files work.
3. **F1, s10 order.** Build the split frame inside the `try`, before `writers.finish()`. Write it after `finish()` as today. A split failure then aborts the writers and keeps the old datasets.
4. **F2, rule.** A second is paused when (phase 0 and timer < 0) or (halftime round and `second ≥ won_at`). `won_at` is `RoundMoments.won_at`, the same value s10 reads from the round clock. For the last round `won_at` = segment end, so at most the final second can be paused. The last round has no phase-3 frames (PLAN.md "Часы раундов"), so today it has no break pause either.
5. **F2, check_live.** `pause_span` takes `freeze: bool` in place of `phase: int` and selects `paused` rows with `(phase == 0) == freeze`. Break: `freeze=False` (phases 2 and 3 after the win). Freeze pause: `freeze=True`. The check keeps testing the s05 flag itself.
6. **F3.** Two named variables, no shared `ARGS` on any CS2 target:
   - `CS2_WINDOWS` — space-separated `START:END` days, only in `cs2-universe`;
   - `CS2_REPLAY_ARGS` — free text, only in `cs2-replays` (`--workers N --limit N`).
   s02–s04 and s06–s10 have no non-path options, so they get no variable. No `?=` lines: an unset variable expands to nothing.
7. **F4, period end.** `CS2_END_TIME = 1_792_281_600` (2026-10-18T00:00:00Z, exclusive) in `src/cs2/constants.py`, next to `CS2_START_TIME`. `keep_start` returns False for `start_ts >= CS2_END_TIME`, with or without `--window`.
8. **F4, exit code.** s09 exits with `BOOKS_PENDING_EXIT_CODE = 3` when books are pending. The orchestrator text says "exits 1", but a Python crash also exits 1, and `cs2-prepare` must not hide a crash. `cs2-prepare` runs s09 itself and accepts only code 3:
   ```make
   cs2-prepare:  ## CS2 s09 + s10 1 Hz dataset …; maps with books pending are skipped
   	$(PY) src/cs2/s09_build_market_data.py || [ $$? -eq 3 ]
   	$(PY) src/cs2/s10_prepare_dataset.py
   ```
   `make cs2-market-data` keeps the strict contract, like `make cs2-books`: pending → make error. s10 already skips maps without a market file and prints their ids (`no_market_seconds`).
9. **F6, token column.** s09 writes `team_a_token_id: str` = `(token_id_0, token_id_1)[team_a_token_index]`, placed after `condition_id` in `MARKET_SECOND_COLUMNS` and in `MARKET_SECOND_TEXT_COLUMNS`. `CACHE_VERSION` changes by itself.
10. **F6, one check in s09.** Move `expected_seconds` and the body of `check_market_seconds` from s10 to s09 as `is_market_file_current(frame, row) -> bool`: not empty, all `condition_id` = row, all `team_a_token_id` = row's team A token, `second` = expected grid, `state_ts_us` = expected stamps.
    - s09 `plan_maps`: if the cache file exists, read only `["condition_id", "team_a_token_id", "second", "state_ts_us"]`, sort by `second`, check. Current → `cached`. Stale → count in a new `stale` field and add a rebuild job. The job overwrites the file (`write_parquet` writes `.tmp` and replaces).
    - s09 `build_one_map`: on `no_books`, `job.cache_path.unlink(missing_ok=True)`. Otherwise a stale file whose rebuild finds no books stays, and s10 raises on every rerun.
    - s09 prints `stale` next to `cached` and `built`.
    - s10 `read_market_seconds` calls `is_market_file_current` and raises `market_error` when it is False. Its message "rerun cs2-market-data" is now true.
    - Serial check: about 13 s on 15,000 maps (fact F6.6). No pool.
11. **One commit**, message like `Fix cross-review findings: ISO time, halftime pause, CS2 make vars, period end, stale market files.` with the `Co-Authored-By` line.

## 4. Changes by file

### `src/cs2/s07_link_readiness.py`
- `assign_split`: `pd.to_datetime(spawn_at, utc=True, format="ISO8601")`.
- `read_event_months`: `pd.to_datetime(grid_links["start_time"], utc=True, format="ISO8601")`.

### `src/cs2/s03_fetch_grid_rounds.py`
- New `normalize_utc(value: str) -> str` = `format_utc(parse_utc(value))`, next to `format_utc`.
- `build_round_row`: `started_at` = `None` if the segment has no `startedAt`, else `normalize_utc(...)`.
- `build_game_row`: `"started_at": normalize_utc(game["startedAt"])`.

### `src/cs2/s01_build_universe.py`
- `build_event_row`: `isoformat(timespec="milliseconds")`.
- `keep_start`: `if start_ts < CS2_START_TIME or start_ts >= CS2_END_TIME: return False`. Import `CS2_END_TIME`.

### `src/cs2/constants.py`
- `CS2_END_TIME = 1_792_281_600` under `CS2_START_TIME`.

### `src/cs2/s05_parse_replay.py`
- `is_pause_second(phase: int, timer_s: float, in_break: bool) -> bool`: True for phase 0 with timer < 0, else `in_break`.
- New `is_break_second(round_number: int, won_at: datetime | None, second: int) -> bool`: False when `won_at` is None or the round is not a halftime round, else `second >= won_at.timestamp()`.
- `build_second_row(frame, second, round_number, in_break)`.
- `parse_round`: call `find_round_clock` first, then build the second rows with `is_break_second(round_number, moments.won_at, pick.second)`.

### `src/cs2/s05_check_live.py`
- `pause_span(seconds, round_number, freeze: bool)`: keep rows of the round with `paused` and `(int(row["phase"]) == 0) == freeze`.
- `break_comparisons`: `pause_span(replay.seconds, score_sum, False)`.
- `freeze_pause_comparisons`: `pause_span(replay.seconds, live.round_number, True)`.

### `src/cs2/s09_build_market_data.py`
- `Cs2MarketSecondRow`, `MARKET_SECOND_COLUMNS`, `MARKET_SECOND_TEXT_COLUMNS`: add `team_a_token_id`.
- New `find_team_a_token_id(row) -> str`; `assemble_rows` writes it.
- Moved from s10: `expected_seconds(row)`. New `is_market_file_current(frame, row) -> bool`, `MARKET_CHECK_COLUMNS`, and a reader for those columns sorted by `second`.
- `PlannedMaps` and `MarketSecondsSummary`: add `stale: int`. `plan_maps` checks the cached file. `summarize` and `print_summary` carry `stale`.
- `build_one_map`: unlink the cache file on `no_books`.
- `BOOKS_PENDING_EXIT_CODE = 3`; `main` raises `typer.Exit(code=BOOKS_PENDING_EXIT_CODE)`.

### `src/cs2/s10_prepare_dataset.py`
- Delete `expected_seconds` and `check_market_seconds`. `read_market_seconds` raises `market_error` when `is_market_file_current` is False. Drop imports that become unused.
- `build_cs2_dataset`: `split_frame = build_split_frame(written)` inside `try`, before `writers.finish()`; `write_parquet(split_frame, …)` after it.

### `Makefile`
- `cs2-universe`: `… --as-of "$(AS_OF)" $(foreach window,$(CS2_WINDOWS),--window $(window))`. Help: `CS2_WINDOWS="2026-04-01:2026-04-07 2026-10-01:2026-10-17"`.
- `cs2-link`, `cs2-rounds`, `cs2-bo3`, `cs2-books`, `cs2-market-data`: drop `$(ARGS)`.
- `cs2-replays`: `--fetch $(CS2_REPLAY_ARGS)`. Help: `CS2_REPLAY_ARGS="--workers 16 --limit 40"`.
- `cs2-market-data` help: `(exit 3 = books pending)`.
- `cs2-prepare`: no prerequisite; the two-line recipe from decision 8. Help names s09 + s10.
- `cs2-collect` stays as it is.

## 5. Tests (one per fix)

| Fix | File | Test |
| --- | --- | --- |
| F1 parse | `tests/test_cs2_catalog.py` | `test_split_and_months_accept_mixed_iso_formats`: `assign_split` on `["2026-07-01T08:04:30.500Z", "2026-07-31T10:47:41Z", "2026-04-15T17:59:38.523000Z"]` → validation, validation, train. `read_event_months` on the same `start_time` values → `2026-07`, `2026-07`, `2026-04`. |
| F1 format | `tests/test_cs2_grid_rounds.py` | `test_started_at_has_milliseconds`: `build_game_row` with `startedAt` `2026-09-09T15:00:00Z` → `2026-09-09T15:00:00.000Z`; `build_round_row` with `…:01.123456Z` → `…:01.123Z`. The existing `started_at is None` test (`:520`) still passes. |
| F1 format | `tests/test_cs2_universe.py` | In `test_markets_get_kind_map_number_and_outcome`: assert event `e1` `start_time` ends with `:00.000Z`. |
| F1 order | `tests/test_cs2_dataset.py` | `test_split_failure_keeps_dataset_files`: monkeypatch `cs2.s10_prepare_dataset.build_split_frame` to raise; run; `pytest.raises`; `training.parquet` does not exist in the output dir. |
| F2 s05 | `tests/test_cs2_replays.py` | `test_halftime_break_covers_plant_after_win`: the plant-after-win document from `test_plant_after_win_is_not_plant`, `clock(12, STARTED, 300.0)`. Every second with `second ≥ won_at` is paused and the set has phase 2. Same document in round 11: none of them is paused. Update `test_pause_flag`: replace `is_pause_second(3, 10.0, N)` with `is_halftime_round(N)` checks (24, 27, 30 True; 25, 26 False) and keep `not is_pause_second(1, -1.0, False)`. |
| F2 check_live | `tests/test_cs2_check_live.py` | `test_break_span_includes_plant_after_win`: round 12 rows: `second_row(90, 12, 0, -1.0, True)`, `(100, 12, 3, …, True)`, `(101, 12, 2, …, True)`, `(102, 12, 2, …, True)`. `pause_span(rows, 12, False)` = 100…103; `pause_span(rows, 12, True)` = 90…91. |
| F3 | `tests/test_cs2_makefile.py` (new) | `test_collect_passes_each_variable_to_one_stage`: `subprocess.run(["make", "-s", "cs2-collect", "PY=echo", "CS2_WINDOWS=2026-04-01:2026-04-07 2026-10-01:2026-10-17", "CS2_REPLAY_ARGS=--workers 16 --limit 40"], cwd=<repo root>, check=True)`. The s01 line ends with both `--window` pairs. The s05 line ends with `--workers 16 --limit 40`. No other line has `--window` or `--workers`. 7 lines in all. |
| F4 make | `tests/test_cs2_makefile.py` | `test_prepare_runs_s10_only_after_pending_exit`: a tmp `fake.sh` exits `$S09_EXIT` for the s09 path and prints `s10` for the s10 path. `make -s cs2-prepare PY="sh <tmp>/fake.sh"` with `S09_EXIT=3` → return code 0 and `s10` in stdout. With `S09_EXIT=1` → non-zero and no `s10`. |
| F4 s09 | `tests/test_cs2_market_seconds.py` | `test_main_exits_3_when_books_pending`: catalog with one map, audit `not_published`; `main(...)` raises `typer.Exit` with `exit_code == 3`. |
| F4 s01 | `tests/test_cs2_universe.py` | `test_end_bound_drops_october_18`: `keep_start(CS2_END_TIME - 1, [])` True; `keep_start(CS2_END_TIME, [])` False; `keep_start(CS2_END_TIME, [parse_window("2026-10-17:2026-10-18")])` False. Fixture move: `e1` → `2026-10-16T23:00:00Z`, `e2` → `2026-10-17T00:30:00Z`, window test → `2026-10-01:2026-10-16` (expect only `e1`, manifest windows `["2026-10-01:2026-10-16"]`). |
| F6 s09 rebuild | `tests/test_cs2_market_seconds.py` | `test_flipped_team_a_rebuilds_cached_file`: build one map with `team_a_token_index=0`; rewrite the catalog with index 1; rerun → `stale == 1`, `built == 1`, `cached == 0`, file `team_a_token_id` = `token_id_1`, `market_p_radiant` 0.40 (was 0.60). Third run → `cached == 1`, `stale == 0`. |
| F6 s09 no books | `tests/test_cs2_market_seconds.py` | `test_stale_file_without_books_is_removed`: build one map; rewrite the catalog row with token ids that have no books (same `condition_id`, so the audit still matches); rerun → `no_books == (match_id,)`, the cache file is gone. |
| F6 s10 | `tests/test_cs2_dataset.py` | `test_flipped_team_a_token_rejects_market_file`: like `test_shifted_catalog_clock_rejects_market_file` (`:584`), rewrite the catalog with `replace(fixture.row, team_a_token_index=1)` → `RuntimeError` `match <id> rerun cs2-market-data`. Fixture `market_rows` (`:269`) adds `"team_a_token_id": "token-0"`. |

## 6. Edge cases

1. **Round `startedAt` missing.** `build_round_row` keeps `None`. s05 `grid_clock` still raises on it (`s05_fetch_bo3_replays.py:261`).
2. **Last round 24 or 30 (13:11, 16:14).** `won_at` = segment end, so `is_break_second` is True at most for the final second, and only if the end is a whole second. s10 shows phase 3 on that second too.
3. **Millisecond truncation.** s10 parses `won_at` from a millisecond string, s05 compares with the full `datetime`. If `won_at` is in (k, k + 0.001) s, the second k is phase 3 in s10 but not paused in s05. Frame times are `started_at` (ms) + n/64 s, so this needs a sum ending in .000625 or similar. Effect: one second in a rare halftime round. I accept it.
4. **Plant after the win in a non-halftime round.** Not paused before or after the fix. Phase 3 in s10.
5. **Empty `CS2_WINDOWS`.** `foreach` gives nothing; s01 runs the whole period up to `CS2_END_TIME`.
6. **Old `ARGS` on the command line.** CS2 targets ignore it. `make cs2-universe ARGS="--window …"` builds the whole period. The STEP-014 text must use `CS2_WINDOWS`.
7. **Pilot window `2026-10-01:2026-10-18`** (feature.json STEP-014). The end bound cuts Oct 18 events, so the window works as `…:2026-10-17`.
8. **s09 pending map with an old cache file.** The pending check runs first, so the file is neither checked nor rebuilt. s10 then checks it. A file is built only after books were ready, so this needs a catalog change on a day that is not yet published. I do not handle it.
9. **Stale file and rebuild crash.** `write_parquet` replaces the file only after the write ends. A crash leaves the old file, and s10 rejects it.
10. **s09 exit 3 run directly.** `uv run python src/cs2/s09_build_market_data.py` exits 3 on pending. Typer usage errors exit 2, a crash exits 1. No clash.

## 7. Checks

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`.

1. `PYTHONPATH=src uv run python -m pytest -q tests/test_cs2_*.py` — all pass. Baseline at `faebd971`: 125 passed (xreview).
2. Full suite without goldens:
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
   No new failures. Name any known failure (for example `lol_*` follow300 smoke without `data/lol`) with its test id in the report.
3. `uv run basedpyright` — 0 errors.
4. `uv run ruff check` and `uv run ruff format --check` on every changed file.
5. `make -s cs2-collect PY=echo CS2_WINDOWS="2026-04-01:2026-04-07 2026-10-01:2026-10-17" CS2_REPLAY_ARGS="--workers 16 --limit 40"` — `--window` only on the s01 line, `--workers` only on the s05 line. Paste the output in the report.
6. `make -n cs2-prepare` — the s09 line ends with `|| [ $? -eq 3 ]`, then the s10 line.
7. Re-run the plan probes on the new code (read-only, outputs to `orchestrate/work/<impl agent>/`):
   - `probe_iso.py` is unchanged; call `assign_split` and `read_event_months` on its values;
   - `probe_plant_after_win.py`: round 12 must print `paused 117`, round 11 `paused 0`.
8. Grep `ARGS` in the CS2 block of the Makefile — no hits.
9. `git status` clean after the commit; `git log -1` shows the `Co-Authored-By` line. No push.

## 8. Risks and open questions

1. **Exit 3, not 1.** This departs from the literal orchestrator text, for the reason in decision 8. If the orchestrator wants exit 1, `cs2-prepare` cannot tell a crash from pending books.
2. **`CS2_END_TIME` is fixed.** A rebuild after 17.10.2026 that needs newer events must change the constant. The pilot and STEP-015 want exactly this bound.
3. **Pilot text (STEP-014) must change.** It must use `CS2_WINDOWS` / `CS2_REPLAY_ARGS`, not `ARGS`. The progress.txt pilot note already says "use the exact per-stage make variables from XR-FIX".
4. **Makefile tests need `make` on PATH.** macOS has GNU Make 3.81. I did not check the VPS. The VPS runs `make` targets for Dota and LoL, so I expect it there.
5. **Summaries built before this commit carry the old `paused`.** None exist in `data/cs2/bo3/round_state/` today. Any summary from a scratch probe must not be copied into that folder.
6. **Cache check cost** grows with the catalog: about 13 s for 15,000 maps on a warm disk, more on a cold one. Acceptable next to the s09 build time.

## 9. Not in this pack

- F5 (s05 concurrency measurement) and F7 (`seconds_delay`, Telonex root, onchain day gate): STEP-014 / STEP-013 text.
- F4 pilot window text: STEP-014.
- A cache version for s05 summaries; a rebuild rule for s05 `summary_ready` (F6 last bullet, low risk per xreview).
- Any change to s02, s04, s06, s08 code, or to `shared/`.
- Runs on real data. Only tests and the small probes above.
