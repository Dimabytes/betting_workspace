# PILOT-FIX-1 — pilot fix pack (s05 empty timer, s05 parse isolation, CS2 thin-book gate)

Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, branch `cs2-signal`, HEAD `62671bd6`.
Spec: bugs 1 and 2 of `orchestrate/reports/pilot-1-pilot.md`, the PILOT-FIX-1 orchestrator decision in `progress.txt` (lines 315–317), section «Баги» of `investigations/2026-10-18-cs2-follow300/report.md`.
This is not a feature.json step. Do not change `passes` in feature.json. One commit for the whole pack.

Background pilot jobs (s03/s04 full, pids 97597, 99450, 16927) run from this worktree. Do not write `data/cs2`, do not kill them. All checks below only read `data/cs2`.

Probe scripts and outputs of this plan: `orchestrate/work/planpf1/`. Five real April rounds fetched for it: `orchestrate/work/planpf1/rounds/`.

## 1. What the pack does

1. **Bug 1.** `build_carried_frames` carries `time_remaining` from the previous frame, like `phase`. The first frame must have both fields.
2. **Bug 1, stall check.** `count_timer_stalls` skips a pair of frames with the same tick. Without this, the carried timer turns a repeated tick into a false stall, and the map becomes `pause_in_round`.
3. **Parse isolation.** In s05 a parse error of one map writes a log line, gives the map the status `replay_parse_error`, and the run goes on. Only 429/403 stop the run, as today.
4. **Bug 2.** The CS2 thin-book gate stops reading a book file as soon as it has `min_book_events` (25) events. The decision per map stays exactly the same, so the coverage report keeps the same thin-book count.

Every fix gets a test (section 5).

## 2. Facts I checked

### Bug 1

1. `build_carried_frames` (`src/cs2/s05_parse_replay.py:222-251`) checks `phase` in the first frame (`:229-231`), carries `phase` (`:238`), and reads `timer["time_remaining"]` without a fallback (`:247`). `ReplayTimer.time_remaining` is a required key (`:30-32`).
2. Repro round `investigations/…/work/repro/167241_12.json`: 810 frames, 1 frame without `time_remaining` (frame 808, tick 102055, timer `{}`), 726 frames without `phase` (`work/planpf1/probe_empty_timer.py`).
3. The ladder output `investigations/…/work/ladder/pilot_w8b.json` has 171 parse errors, all `KeyError: 'time_remaining'`. The failing round (last request of each failed map) is round 13 in 119 maps, round 12 in 31, round 11 in 13, round 28 in 4, rounds 1, 2, 22, 34 in 1 each.
4. I fetched 5 failing rounds (5 requests to `files.bo3.gg`, all 200): `165065/11`, `166000/1`, `166026/13`, `167245/13`, `168552/28`. Each has exactly 1 frame with timer `{}`. That frame always has the same tick as the frame before it (`probe_rounds.py`):
   - 4 rounds: frame 1, same tick as frame 0, carried phase 0;
   - `167241/12` (repro): frame 808, carried phase 3, round end;
   - `165065/11`: frame 1062 of 1266, tick 87152, **carried phase 1**.
   The first frame has `time_remaining` and `phase` in all 6 rounds.
5. `count_timer_stalls` (`:444-453`) counts a pair in phases 1–2 when `current.timer_s >= previous.timer_s`. It does not look at ticks.
6. Stalls per round (`probe_rule.py`, output `probe_rule.out`):

   | Round | Carry, current stall rule | Carry, skip same tick | Empty frame removed |
   |---|---:|---:|---:|
   | 165065/11 | **1** | 0 | 0 |
   | 166000/1, 166026/13, 167245/13, 168552/28, 167241/12 | 0 | 0 | 0 |
   | 186745/13, 186818/19, 189105/13 (check-2 repro rounds) | 1 | 1 | 1 |

   So plain carry makes `165065` a `pause_in_round` map. Skipping same-tick pairs gives the same stalls as removing the frame. The 3 check-2 rounds have 0 same-tick pairs and keep their stall.
7. Same-tick pairs over the 9 rounds: only the 6 empty-timer frames, plus 1 pair with explicit equal timers in phase 3 right after the empty frame of `167241/12` (`probe_same_tick.py`). The 3 check-2 rounds (11,666 frames) have none.
8. Phase moments (`freeze_end_at`, `won_at`) are the same with the carried frame and without it; only `won_index` moves by one when the frame is removed (`probe_carry.py`). Carry keeps the frame, so kill keys and player picks keep their frame indices.

### Parse isolation

1. `fetch_map` (`src/cs2/s05_fetch_bo3_replays.py:405-432`) calls `parse_downloaded_round` (`:394-402`, `json.loads` + `parse_round`) with no guard. An error goes out of the worker. `fetch_jobs` catches it at `:477-479`, sets `stop`, and raises. `main` never reaches the tables.
2. Log lines exist today only for non-200 downloads (`:420-429`): 404, 429/403, 5xx, transport errors. A line with `status_code == 200` never occurs.
3. `decide_map_status` (`src/cs2/s05_parse_replay.py:575-592`): no summary → `replay_missing` if the map has a 404 log line (`read_missing_game_ids`, `s05_fetch_bo3_replays.py:524-525`), else `replay_not_fetched`.
4. `STATUS_NAMES` (`src/cs2/s05_replay_contract.py:18`) feeds `S05_DROP_STATUSES` (`src/cs2/s06_publish_catalog.py:57`) and the s07 funnel (`src/cs2/s07_link_readiness.py:49`, `:156`, `:206`). A new status reaches s06/s07 with no other change. No test lists the s05 statuses.
5. The pilot wrapper `investigations/…/work/ladder.py` patches `s05.download_round`, `s05.run_map_job`, `s05.fetch_jobs` by name. Their signatures must stay the same: the continue task runs the ladder with it.
6. Ruff does not select `BLE`. `except Exception as exc` per item is an existing pattern (`src/collect/s05_fetch_opendota_matches.py:75`, `:88`).

### Bug 2

1. `_gate_map` (`src/backtest/cs2_inputs.py:224-254`) calls `count_window_book_events` for both tokens of every validation map (`:249-251`) and only compares the result with `min_book_events` (`:252`). `--limit` and `--match-id` apply later (`:322-329`). The function has no other caller (grep over `src`, `scripts`, `tests`).
2. `count_window_book_events` (`src/backtest/telonex_local.py:209-228`) passes every row group of each window day file to the native `telonex_parquet_book_snapshot_diff_rows` and returns `len(set(event_index))`.
3. The native core (`../prediction-market-backtesting/crates/core/src/telonex.rs`):
   - `parquet_book_snapshot_diff_rows` (`:428`) builds string levels for every row with `ts ≤ end_ns` (`:492`), so for every row before the window too;
   - `flat_book_snapshot_diff_rows` (`:320`) sorts rows by time (stable). A row before `start_ns` only sets `previous` (`:366`). The first in-window row with a non-empty book is always a snapshot event (`:372`), whatever `previous` holds. So the count depends only on rows with `start_ns ≤ ts ≤ end_ns`;
   - after the snapshot, one event per row that differs from the row before it (levels with size ≤ 0 dropped, sorted, deduped by price text, `flat_book_side_levels` `:650`).
4. Consequence: the event count of any subset of the in-window rows (same relative order) is ≤ the count of all of them. Removing a row never adds a change between neighbours (equality of books is transitive). So "subset count ≥ 25" proves "full count ≥ 25".
5. `telonex_nested_book_snapshot_diff_rows` (Python wrapper `prediction_market_extensions/_native.py:175`, Rust `crates/python/src/lib.rs:211`) feeds the same core from Python lists of level dicts. It skips a `None` level and a level with `None` price or size (`lib.rs:309` `py_nested_book_side`), like the parquet path.
6. Real Telonex day files (`esports-trader/data/cs2/raw/telonex/polymarket/book_snapshot_full`, 40 sampled): 1 row group up to ~1 M rows, 2 groups at 1.34 M rows, `timestamp_us` stats present (`probe_row_groups.py`). Row-group pruning alone does not help.
7. Exactness probe on real pilot validation maps (`probe_book_events.py`, `probe_nested.py`, outputs `*.out`):
   - flat native count on in-window rows only = current count for all 20 token windows of 10 maps (36,474 … 214,294 events);
   - nested native count = current count for 4 token windows (maps 186747, 187734).
8. Speed and memory (`probe_book_events.py`, `probe_stream.py`):

   | Way | Maps | Time | Max RSS |
   |---|---:|---:|---:|
   | current `count_window_book_events` | 10 | 73.4 s | 10,101 MB |
   | in-window prefix of 200 rows, whole file read with `pq.read_table` | 10 | 11.7 s | 6,724 MB |
   | in-window prefix of 200 rows, streamed batches 16,384 rows, `use_threads=False` | 60 | 26.4 s (0.44 s/map) | 709 MB |

   The streamed check returned "not thin" for all 60 maps from the 200-row prefix. No map needed the full count. Events in a 200-row prefix: 193–200.
9. `count_window_book_rows` and `has_local_telonex_days` together take 0.3 s for 60 maps (`probe_rows.py`). The event count is the whole cost.
10. `src/shared/utils/telonex_book.py` already streams book files: `_open_book` (`:147`), `_group_spans` (`:156`), `_drop_null_stamps` (`:227`), `_groups_touching` (`:333`), `_READ_BATCH_ROWS = 16_384` (`:25`), `_BOOK_COLUMNS` (`:26`). It has a file-level pragma for pyarrow unknown types (`:1`). The same reader in `telonex_local.py` needs new `pyright: ignore` lines: my sketch got 13 strict errors from `iter_batches` and `pc.*` (`typing_probe.py`).

### Baseline

Fast suite at `62671bd6` (command from the task): `3376 passed, 64 warnings, 2 subtests passed in 28.79s`.

## 3. Decisions

1. **Carry `time_remaining`.** `ReplayTimer.time_remaining` becomes `NotRequired[int]`. The first frame without `time_remaining` raises `RuntimeError("round file is missing the first time_remaining")`, next to the phase check.
2. **Same tick is not a stall.** `count_timer_stalls` skips a pair with `current.tick == previous.tick`. Reason: no game time passed, so the timer cannot fall. Facts 6–7 show it changes no stall count except the empty-frame case. This goes past the literal orchestrator text ("carry forward like phase"). Without it 1 of 6 sampled empty-timer rounds drops its map.
3. **Parse isolation scope.** A `try` around `parse_downloaded_round` only, in `fetch_map`. `except Exception as exc`: write a log line (`status_code` = the download code 200, `error` = `f"{type(exc).__name__}: {exc}"`), return `MapFetchResult(outcome="failed", …)`. The rest of that map's rounds are not downloaded. `build_map_summary` and `write_map_summary` stay outside the `try`: a disk error still stops the run.
4. **Status.** New `Cs2ReplayStatus` value `replay_parse_error`, in `STATUS_NAMES` right after `replay_missing`. A map gets it when it has no summary and a log line with `status_code == 200`. Order in `decide_map_status`: `replay_missing`, then `replay_parse_error`, then `replay_not_fetched`. A map with a summary takes its status from the summary, as today.
5. **No new outcome or counter.** A parse error counts as `failed` in the progress line. The final summary prints the status counts and every log line with its error text (`print_replay_summary`, `:637-648`).
6. **Thin-book gate = bool.** `count_window_book_events` becomes `has_window_book_events(token_id, window, capture_root, min_events) -> bool`. Per window day:
   - read the in-window rows in file order, at most `min_events × BOOK_EVENT_PREFIX_ROWS_PER_EVENT` (= 8, so 200) rows, streamed, and stop at that row;
   - count events on them with `telonex_nested_book_snapshot_diff_rows`;
   - if the read stopped early and the running total is still below `min_events`, count the day exactly with the old native parquet call;
   - add the day count to the total; return True as soon as the total reaches `min_events`.
   The returned bool equals `old_count >= min_events` (facts 3–5). The old per-day body stays as the exact fallback.
7. **Bounds.** Keep today's ns bounds: `start_ns = int(window.start.timestamp() * NS_PER_SECOND)`, same for `end_ns`. The row filter uses `first_us = -(-start_ns // 1_000)` and `last_us = end_ns // 1_000`. Then the filter keeps exactly the rows the native call keeps (`ts_us × 1000` in `[start_ns, end_ns]`), and the fast path and the fallback see the same rows.
8. **Reader location.** The streamed reader goes in `src/shared/utils/telonex_book.py` (it reads Telonex book files, has the pragma and the helpers). No new `pyright: ignore` in `telonex_local.py`.
9. **Gate order.** `_gate_map` checks events first with `all(...)` over the two tokens, then counts raw rows only for a map that passes. `MapGate` and `_select_ids` do not change, so `ValidationCoverage` and the `thin_book` log do not change.
10. **Not chosen:** "gate only the maps this process replays". Coverage needs the thin count over all maps, and shard weights need `book_rows` of every eligible map.
11. **No new docstrings, no new suppressions** (STEP-013 user rule). Names are verbs.
12. **One commit**, message like `Carry the CS2 replay timer, isolate s05 parse errors, stop the thin-book count at the gate.` with the `Co-Authored-By` line.

## 4. Changes by file

### `src/cs2/s05_parse_replay.py`
- `ReplayTimer`: `time_remaining: NotRequired[int]`.
- `build_carried_frames`: `first_timer = states[0]["timer"]`; check `phase` (as now) and `time_remaining` (new message); start `time_remaining` from the first frame; in the loop `time_remaining = timer.get("time_remaining", time_remaining)`; `timer_s=time_remaining / 1e9`.
- `count_timer_stalls`: first guard in the loop `if current.tick == previous.tick: continue`.
- `decide_map_status(map_input, rounds, missing_ids, unparsed_ids)`: after the `replay_missing` check, `if map_input.bo3_game_id in unparsed_ids: return "replay_parse_error"`.

### `src/cs2/s05_replay_contract.py`
- `Cs2ReplayStatus` and `STATUS_NAMES`: add `"replay_parse_error"` after `"replay_missing"`.

### `src/cs2/s05_fetch_bo3_replays.py`
- `fetch_map`: in the `download.kind == "ok"` branch:
  ```python
  try:
      parsed.append(parse_downloaded_round(download.body, job, index))
  except Exception as exc:
      write_log_line(log, job.bo3_game_id, grid_round.round_number, download.status_code, f"{type(exc).__name__}: {exc}")
      return MapFetchResult(outcome="failed", payload_bytes=payload_bytes)
  continue
  ```
- New `read_unparsed_game_ids(log_path) -> set[int]`: ids of log lines with `status_code == 200`, next to `read_missing_game_ids`.
- `build_replay_tables`: read both sets, pass `unparsed_ids` to `decide_map_status`.
- `download_round`, `run_map_job`, `fetch_jobs`: no signature change (ladder wrapper).

### `src/shared/utils/telonex_book.py`
- New frozen dataclass `WindowLevels(timestamps_us: list[int], bids: list[object], asks: list[object], complete: bool)`.
- New `read_window_levels(path: Path, *, start_us: int, end_us: int, max_rows: int) -> WindowLevels`:
  - `parquet = _open_book(path)`, `groups = _groups_touching(_group_spans(parquet), start_us, end_us)`; no groups → empty, `complete=True`;
  - `parquet.iter_batches(batch_size=_READ_BATCH_ROWS, row_groups=groups, columns=list(_BOOK_COLUMNS), use_threads=False)`;
  - per batch: `_drop_null_stamps`; mask `start_us ≤ stamp ≤ end_us`; filter; slice to the rows still needed; extend the three lists (`to_pylist()` on `bids` and `asks` of the sliced rows only);
  - when the lists hold `max_rows` rows → return with `complete=False`; after the last batch → `complete=True`.

### `src/backtest/telonex_local.py`
- Import `telonex_nested_book_snapshot_diff_rows` next to the parquet one, and `read_window_levels`, `WindowLevels`.
- `BOOK_EVENT_PREFIX_ROWS_PER_EVENT = 8`.
- `_count_day_book_events(path: Path, start_ns: int, end_ns: int) -> int`: the old per-day body (row groups, parquet native call, `len(set(diff_rows[1]))`).
- `_count_level_events(levels: WindowLevels, start_ns: int, end_ns: int) -> int`: nested native call with `timestamp_ns=[stamp * 1_000 for stamp in levels.timestamps_us]`, `len(set(rows[1]))`. Empty lists give 0 from the native call; no branch.
- `has_window_book_events(token_id, window, capture_root, min_events) -> bool`: decision 6 with the bounds of decision 7.
- Delete `count_window_book_events`.

### `src/backtest/cs2_inputs.py`
- Import `has_window_book_events` in place of `count_window_book_events`.
- `_gate_map`:
  ```python
  if not all(has_window_book_events(token_id, replay, CS2_TELONEX_ROOT, min_book_events) for token_id in tokens):
      return MapGate(kind="thin", book_rows=0)
  raw_counts = tuple(count_window_book_rows(token_id, replay, CS2_TELONEX_ROOT) for token_id in tokens)
  return MapGate(kind="ok", book_rows=raw_counts[0] + raw_counts[1])
  ```

## 5. Tests (one per fix)

| Fix | File | Test |
| --- | --- | --- |
| Bug 1 carry + stall rule | `tests/test_cs2_replays.py` | `test_empty_timer_patch_keeps_previous_timer`: `grid = clock(11, STARTED, 200.0)`; `clean = parse_round(load_document(), grid, grid.start + 200.0, False)`. Copy the document; take a phase-1 frame `live` (carried phase 1, a few frames after freeze end); insert after it a copy with the same `ingame_tick`, `timer={}`, `bomb={}`, `players=[]`; shift kill keys `> live` by +1. Parse → `seconds == clean.seconds`, `draft == clean.draft`, `timer_stalls == clean.timer_stalls == 0`. `build_carried_frames` on the patched states: frame `live + 1` has the `timer_s` and `phase` of frame `live`. Without the same-tick rule this test gets `timer_stalls == 1`. |
| Bug 1 first frame | `tests/test_cs2_replays.py` | In the same test: delete `time_remaining` from frame 0 → `RuntimeError` match `first time_remaining`. |
| Parse isolation | `tests/test_cs2_replays.py` | `test_parse_error_marks_map_and_run_goes_on`: copy of `test_404_marks_replay_missing` (`:636`). Handler: map 111 round 1 → 200 with the JSON of a document whose frame 0 has no `phase`; all else → `FIXTURE_BYTES`. `main(fetch=True, workers=1, …)` returns (no `typer.Exit`). 111 starts later, so it runs first and 222 runs after the error. Assert: `state_dir/"111"` absent, `state_dir/"222"` present; a log line for 111 with `status_code == 200` and `error` starting `RuntimeError: round file is missing the first phase`; status of 111 = `replay_parse_error`; status of 222 ≠ `replay_parse_error`. |
| Typing fallout | `tests/test_cs2_replays.py` | `test_timer_stall_is_pause_in_round` `:559` reads a now-`NotRequired` key. Read with `.get("time_remaining")` and `assert previous_timer is not None`. The test must still pass: its stalled pair has different ticks. |
| Bug 2 early stop | `tests/test_cs2_backtest.py` | `test_book_event_gate_reads_a_prefix`: `write_token_book` one token, `book_snapshots(1_000, start, vary=True)` on day `2026-09-10` at 12:00; `ReplayWindow(start, start + 2_000 s)`. Monkeypatch `backtest.telonex_local.telonex_parquet_book_snapshot_diff_rows` to raise, and wrap `backtest.telonex_local.telonex_nested_book_snapshot_diff_rows` to record `len(timestamp_ns)`. `has_window_book_events(token, window, root, 25)` is True; every recorded length ≤ 200. |
| Bug 2 exact fallback | `tests/test_cs2_backtest.py` | `test_book_event_gate_falls_back_to_full_count`: book A = 250 identical rows (`vary=False`) then 50 varying rows (1 s steps after them) → True (prefix of 200 rows has 1 event; full count 51). Book B = 300 identical rows → False (1 event). |
| Bug 2 coverage | `tests/test_cs2_backtest.py` | `test_thin_book_is_excluded` (`:729`) unchanged and passing: 24 varying → thin, 25 identical → thin, 25 varying → kept with `book_rows == 50`. |

## 6. Edge cases

1. **Empty timer in frame 0.** Raises `RuntimeError`; the map gets `replay_parse_error`, the run goes on.
2. **Empty timer at the first tick** (frame 1, same tick as frame 0, phase 0). Carry gives frame 0 values. Phase 0 is outside the stall check anyway.
3. **Explicit equal timers at the same tick in phase 1/2.** Today a stall, after the fix not. Seen only in phase 3 (fact 7).
4. **Truncated or non-JSON body.** `json.loads` raises inside the `try` → `replay_parse_error`.
5. **A 404 in an old run and a parse error in a new run.** `replay_missing` wins (decision 4).
6. **Parse-error map in the next run.** It has no summary, so s05 downloads it again, like a 404 map today. After this fix the 171 April maps download and parse.
7. **Bug outside the parse step** (`build_map_summary`, frame typing in `write_map_summary`, disk). Not caught: the run stops as today. A dtype error there leaves a `.<id>.*` temp dir in `round_state/`. I did not see this case.
8. **Window over two UTC days.** Each day file gives its own snapshot event, as in the old count. The running total adds day counts; a lower bound in one day plus exact earlier days is still a lower bound.
9. **Unsorted book file.** The prefix is a subset in file order; the native call sorts stably; the bound holds (fact 4).
10. **Null `timestamp_us` rows.** `_drop_null_stamps` drops them; the native parquet path skips them too.
11. **Exactly 200 in-window rows.** The reader returns `complete=False`. If they give fewer than 25 events, the fallback runs once more for nothing. Result is still exact.
12. **Quiet start, active later** (≥ 200 identical rows at the window start). The fallback runs the old full-day native parse: old time and memory for that token-day. 0 of 60 probe maps.

## 7. Checks

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`. Write outputs to `orchestrate/work/<impl agent>/`.

1. Fast suite (task command):
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
   Baseline 3376 passed. Expect 3376 + new tests, 0 failed. Goldens are skipped (only `src/cs2`, `src/backtest/cs2_inputs.py`, `src/backtest/telonex_local.py` CS2 gate, and a new reader change; Dota/LoL gates do not call `has_window_book_events`).
2. `uv run basedpyright` — 0 errors. `uv run ruff check` and `uv run ruff format --check` on every changed file.
3. Bug 1 repro from the pilot report — now returns 810 frames, no `KeyError`:
   `PYTHONPATH=src uv run python -c "import json; from cs2.s05_parse_replay import build_carried_frames; d=json.load(open('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-18-cs2-follow300/work/repro/167241_12.json')); print(len(build_carried_frames(d['states'], '2026-04-01T00:00:00.000Z', 64)))"`
4. Stalls on the 9 real rounds (5 in `orchestrate/work/planpf1/rounds/`, 4 in `investigations/…/work/repro/`) with `count_timer_stalls(build_carried_frames(states, "2026-04-01T00:00:00.000Z", tick_rate))`: 0 for `165065_11`, `166000_1`, `166026_13`, `167245_13`, `168552_28`, `167241_12`; 1 for `186745_13`, `186818_19`, `189105_13`.
5. Selection time on the pilot data (reads `data/cs2` only):
   `/usr/bin/time -l env PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -c "import time; from backtest.cs2_inputs import load_cs2_selection; t=time.time(); s=load_cs2_selection(match_id=None, limit=3, min_book_events=25); print(round(time.time()-t,1), s.coverage, s.selected_ids)"`
   Expect the pilot coverage: validation 302, without_signal_rows 19, without_local_telonex 1, eligible 282. Pilot selection took 1,114 s at 8–11 GB. Estimate now ≈ 282 × 0.44 s ≈ 2 min, max RSS well under 11 GB. Report the numbers. If the coverage differs, check first whether `data/cs2/datasets` changed since the pilot.
6. `git status` clean after the commit; `git log -1` shows the `Co-Authored-By` line. No push. Untracked `data/cs2/` stays untracked.

## 8. Risks and open questions

1. **Same-tick rule changes check 2 a little.** A repeated tick is never a stall now. On 9 real rounds it changes only the empty-frame case. If October maps have explicit equal timers at a repeated tick in phase 1/2, they move from `pause_in_round` to `ok`. The continue pilot compares `pause_in_round` with the pause count (26: 5 April, 21 October).
2. **Broad `except` hides parse bugs as a status.** The continue task must read `replay_parse_error: N` and the log errors after s05. Expect 0 on the 171 April maps after the fix; any other number needs a look before STEP-015.
3. **Bug 1 evidence is 6 rounds of 6 maps.** All 171 failures were the same `KeyError`, and all 6 sampled empty frames share one shape (same tick as the frame before). Another shape now shows as `replay_parse_error`, not a crash.
4. **Exactness of the gate rests on the Rust code** (facts 3–5) plus probes: flat count = old count on 20 token windows, nested = old on 4. The fallback keeps the old call, so a book that misses the fast path gets the old answer.
5. **Each shard still runs the selection.** ≈ 0.44 s per map → 1,700–2,600 validation maps ≈ 12–19 min per shard process, under 1 GB for the gate. Sharing one selection between shards is not in this pack.
6. **Fallback cost** is the old cost (full-day native parse, up to ~10 GB on a 1.3 M-row day) for a token-day with a quiet window start. Not seen in 60 maps.
7. **Departure from the orchestrator text:** decision 2 (same-tick rule) is extra to "carry forward like phase". Decision 6 keeps the bug-2 decision ("stop counting at `min_book_events`") but stops inside a day file, not only between days.

## 9. Not in this pack

- s03 GRID 503 outer retry (STEP-015, pilot `work/retry.sh`).
- Check 1 rule change (drop a malformed round 1, 1.0 s tolerance) — user decision after the pilot.
- Gating only selected maps, sharing the selection between shards, caching gate results on disk.
- Any Rust change in `../prediction-market-backtesting`.
- Runs that write `data/cs2` (s05 on the 171 April maps, s06–s11, backtests): the continue pilot does them.
