# PILOT-FIX-3 — binding W, round 1 phase, phase clocks, map-end target

Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, branch `cs2-signal`, HEAD `e8629acc`.
Spec: the user decisions at the end of `progress.txt` (from «USER DECISIONS after the pilot» to the PILOT-FIX-3 entry), binding W with 0.3 s (`orchestrate/reports/check1-1-check1.md`, sections 4–6), `orchestrate/reports/pilot-2-continue.md`, investigation report section «Пилот», PLAN.md.
Orchestrator change: two parts, each with its own commits and its own review.

- **Part A** = s05 only. It lands first. The full s05 download of STEP-015 starts right after the part A review.
- **Part B** = s10, s11, backtest, PLAN.md. It is implemented while s05 downloads.

This is not a feature.json step. Do not change `passes`.

Agent `pilot2` writes `data/cs2` now. Do not write `data/cs2` or `data/backtests`. Tests write only to pytest tmp dirs. Scratch checks write only to `orchestrate/work/<agent>/`.

Probe scripts and outputs of this plan: `orchestrate/work/planpf3/` (`probe_w.py` → `probe_w.out`, `probe_no_win.py` → `probe_no_win.out`; read-only on `work/check1/out` and `data/cs2/bo3`).

Baseline fast suite at `e8629acc` (task command): `3383 passed, 64 warnings, 2 subtests passed in 28.81s`.

## 1. What part A must guarantee (so part B needs no new download)

Part B reads only s05 outputs. s05 keeps no raw replay files. So the summary written by part A is final for this download. Part A guarantees:

1. **Every time in the summary uses binding W.** `seconds.second`, `kills.at`, and the round moments in `rounds.parquet` are `clock_offset_s + tick / tick_rate`. GRID `started_at` is only an input of the offset.
2. **Every round keeps its 1 Hz rows, dropped or not.** A round with a clock step keeps its seconds. Part B flags them through `round_clock.parquet`. A map with a non-`ok` status still gets a full summary.
3. **Round 1 that starts at the freeze end is fixed in the summary.** Its live frames are phase 1, timer `115 − elapsed`, not paused. Its win moment exists.
4. **`rounds.parquet` stores the clock inputs and outputs per round:** `started_at` (GRID), `grid_duration_s`, `ended_at` (GRID end), `first_tick`, `last_tick`, `first_frame_at`, `starts_at_freeze_end`, `freeze_end_at`, `planted_at`, `won_at`, `clock_offset_s`, `start_spike_s`, `clock_step_s`, `timer_stalls`.
5. **No threshold is baked into the summary.** The W limits (0.3 s, 2 dropped rounds, 2 spikes in 5, 25%) apply only when s05 builds `replay_maps.parquet` and `round_clock.parquet` from the summaries. `s05` without `--fetch` rebuilds both tables in seconds, with no download. So a change of a limit, or the conservative variant Wc (also drop spike rounds), needs no download either: `start_spike_s` is stored.
6. **The summary folder carries a version:** `data/cs2/bo3/round_state_v2/<bo3_game_id>/`. The old 1 481 summaries stay in `round_state/`. s05 never writes into an existing summary folder.
7. **s10, s05_check_live, s06 and s07 run on part A output without part B.** s10 reads the same `round_clock` columns as today (section 4.6).

What still needs a new download: a change of the binding itself (window size, median, which rounds give an offset), of the second grid (`ceil` rules), of the round 1 fix, or new per-frame data. The part A review must settle these. Part B touches none of them.

## 2. Facts I checked

### Binding W, exact definitions (check1 scripts)

1. `o_r = startedAt_r − first_tick_r / 64`. A round 1 without the 0 → 1 transition has no `o` (`a1_rounds.py`: `bad_r1` → `o = NaN`).
2. `off_r` = median of `o` over 5 rounds that have `o`, centred on the nearest such round, the window moved inward at the map edges; with fewer than 5 such rounds, all of them (`a5_options.py:23-34`, `local_offsets`).
3. `sp_r = o_r − off_r`. `st_r = off_{r+1} − off_r`; for the last round `st = GRID end − (off + last_tick / 64)` (`a5_options.py:37-41`).
4. W (`a13_window_rule.py`, tol 0.3, share 0.25): a round is dropped if `|st| > 0.3` and it is not the last round. The map is out if dropped rounds > 2, or any window of 5 consecutive rounds with `o` holds > 2 rounds with `|sp| > 0.3` (fewer than 5 such rounds: their total), or rounds with `|sp| > 0.3` > 25% of all rounds of the map.
5. On 1 441 parsed pilot maps W keeps 1 107 maps, 62 dropped rounds in 37 maps (`a13_window_rule.txt`).

### New facts from my probes (`probe_w.out`, `probe_no_win.out`)

6. **Round first frames out of time order.** `b_r = off_r + first_tick_r / 64` is not strictly increasing in 18 rounds of 8 maps. Two of them W keeps:
   - 167328: round 1 ticks 2 702–26 102, round 2 starts at tick 6 (tick reset), `sp` −777.1 and −369.1. `b_1` is 42 s after `b_2`. Round 1 gets no 1 Hz rows.
   - 189592: rounds 3 and 4 have the same GRID `startedAt`, so `b_3 == b_4`; rounds 1–2 have `sp` +228.6 and +143.5. Rounds 1–4 sit ~193 s off.
   W misses both because the median of 5 takes the broken value at the map start. I add rule **R-order**: the map is out when `first_frame_at` is not strictly increasing.
7. **Rounds without a win moment.** 83 W-kept maps have a non-last round without `won_at` today. 78 of them are round 1 starting at the freeze end: its phases are `(0, 3)` (72) or `(0, 3, 2)` (5), and `became_win` needs 1 or 2 before 3. The round 1 fix turns 0 → 3 into 1 → 3, so the win is found. 6 maps stay `round_clock_incomplete` (rounds 4, 4, 2, 14, 13 with phases `(0, 1, 2)`, and the round 1 with no 1 Hz rows).
8. No round other than round 1 lacks the 0 → 1 transition (0 of 259 + 0).
9. W-kept maps with a timer stall (`pause_in_round`): 26.
10. **Expected `ok` on the same 1 441 maps: ~1 073** = 1 107 − 2 (R-order) − 26 (stall) − 6 (no win). check1's 1 107 did not apply the stall and clock checks. The old summaries give the phase runs, so this count is approximate.
11. Oracle numbers for fixtures:
    - 190444 round 1: GRID start 1792237736.833, first tick 4, `off` 1792237756.877. The first frame is at `off + 4/64` = 1792237756.940, 20.106 s after the GRID start. Rounds 2–4: `sp` −0.045, −0.077, 0.000.
    - 190447 round 4: `st` +1.316 (`off` 1792239988.971 → 1792239990.287), dropped. Rounds 5–6: `sp` +0.060, +0.068.
    - 189809 (30 rounds, kept): round 1 starts at the freeze end (first tick 4); spikes +0.869 (r2), +0.349 (r6), +0.553 (r14), +0.473 (r26).
    - Last round, W-kept maps: GRID end − replay end p1 −0.043, p50 +0.136, p99 +0.964, max +8.481 s.

### Code

12. `build_carried_frames` (`src/cs2/s05_parse_replay.py:222-256`) puts UTC on each frame from the GRID start of the same round (`:249`). `parse_round` (`:485-511`) builds seconds, players, kills and moments from that. `fetch_map` (`src/cs2/s05_fetch_bo3_replays.py:405-442`) parses each round right after its download and drops the JSON, then calls `build_map_summary` outside the `try` (`:440`).
13. Players for a picked second come from a second walk over `document["states"]` (`build_player_rows_for_picks`, `:338-350`). Part A must not keep the documents of a whole map: 16 workers × ~54 MB of JSON per map would become several GB of Python objects.
14. `summary_ready` (`s05_fetch_bo3_replays.py:280-281`) skips a map with a summary folder. All readers build the folder path from `CS2_ROUND_STATE_DIR.name`: s05 (`:682`), `s05_check_live.py:635`, `s10_prepare_dataset.py:801`.
15. `replay_maps` status is decided from `rounds.parquet` of the summary at table build (`build_replay_tables`, `:627-639`; `decide_map_status`, `s05_parse_replay.py:582-602`). s06 and s07 take the status names from `STATUS_NAMES`.
16. s10 reads `round_clock.parquet` columns `bo3_game_id, round_number, started_at, won_at, team_a_side, team_a_won` (`s10_prepare_dataset.py:151-158`) and the summary `seconds` and `players`. It uses `started_at` (GRID) for the phase 0 clock (`normalize_phase`, `:578-586`).
17. `replay_interval_s` and `max_duration_diff_s` have no reader outside s05 and test fixtures (`tests/test_cs2_dataset.py:209-235`, `tests/test_cs2_catalog.py:147`, `tests/test_cs2_check_live.py:101-103, 317`).
18. `s05_check_live.py:244-249` compares the live freeze start with GRID `started_at`.
19. The 300 s target is built in s09 (`lookup_market_p_after`, `s09_build_market_data.py:254-259`). s09 rows run from second −60 to `floor(ended_at) − zero_s` (`compute_last_second`, `:153-155`). `ended_at` of the catalog is the GRID end of the last segment (`s06_publish_catalog.py:329`).
20. The backtest uses the same `ended_at`: `game_ended_at` (`cs2_inputs.py:172`), buy cutoff (`:165-167`), settlement, and the `map_end_300s` bucket (`postprocess.py:573-603`: fills with `ts ≥ game_ended_at − 300 s`).
21. **What `ObservedClockTape.paused` drives** (CS2 path). The strategy reads it in `_clock_at` (`src/backtest/strategy.py:1251-1270`) into `GameClock.paused`. Then:
    - `requote` returns `_blocked(reason="paused")` before every other gate (`src/strategy/quoting.py:955-956`). `_blocked` cancels all orders, BUY and SELL, and plans nothing (`:102-107`). The position stays.
    - a paused `ClockUpdate` runs the plan at once (`_executes_plan`, `strategy.py:148-152`; `should_evaluate`, `src/strategy/scheduling.py:144`);
    - the run report counts these seconds as gate `paused`.
    It drives nothing else: not the buy cutoff (`game_second`), not settlement, not `game_ended`. `signal_replay.py:177` and `signals.py:640` are the Dota/LoL schedule path.
22. Without a decision, the strategy keeps the last signal. Entry stops after `GRID_FEED_STALE_SECONDS` = 16 s. After `EXIT_FEED_STALE_SECONDS` = 45 s it sells at the ask with reason `stale_signal` (`quoting.py:800-820`, `src/shared/constants/strategy.py:57-61`).

## 3. Decisions

1. **Two passes in s05.** Pass 1 runs per round right after download, as today: it reduces the JSON to frames in ticks and drops the document. Each frame keeps a tuple snapshot of the 10 players (fact 13). Pass 2 runs once per map after the last round: it fits the offsets and builds all rows. Memory: ~20–30 MB per map in flight.
2. **Binding W exactly as check1** (fact 2). Stdlib `statistics.median`.
3. **Round 1 fix (user rule).** A round 1 without a 0 → 1 transition starts at the freeze end. Frames before the first phase 2 or 3 frame get phase 1 and timer `115 − (tick − first_tick) / tick_rate`. Freeze end = first frame. No `o` for it. The fix runs in pass 1, before moments and stalls. Only round 1 (fact 8).
4. **Last round win = `off + last_tick / tick_rate`** (user rule). `ended_at` stays the GRID end. `clock_step_s` of the last round = GRID end − replay end, stored for checks, never a drop.
5. **Statuses.** `replay_grid_mismatch` is removed. New, in this order after `not_mr12` and the missing-summary statuses: `replay_clock_order` (R-order, fact 6), `grid_clock_broken` (spike window or share), `replay_clock_steps` (> 2 dropped rounds). Then `pause_in_round`, `round_clock_incomplete`, `ok` as today.
6. **R-order is a status, not an exception.** The summary is written, so a rerun does not download the map again. Seconds of such a map can overlap; s10 and check_live read only `ok` maps.
7. **Thresholds only at table build** (section 1, item 5). `round_clock.parquet` gets `clock_dropped`; `replay_maps.parquet` gets `dropped_rounds` and `start_spikes` in place of `max_duration_diff_s`.
8. **A pass 2 error is a parse error.** `fetch_map` wraps `build_map_summary` like pass 1. The log line has `round_number` 0, status 200. The map gets `replay_parse_error`; the run goes on.
9. **Versioned folder.** `CS2_ROUND_STATE_DIR = CS2_BO3_DIR / "round_state_v2"`. One constant; every reader follows it (fact 14). The two stale pilot summaries (166843, 166847) stay behind in `round_state/` and need no manual removal.
10. **Map end for the 300 s target (part B): the catalog `ended_at`** (GRID end of the last segment). Reasons:
    - it is the map end of every other piece: the last s09/s10 row, the backtest buy cutoff, settlement, and the `map_end_300s` bucket (facts 19–20). So the s11 slice «last 300 s» is the same set of seconds as the backtest bucket;
    - s10 already has it (`row.ended_at`);
    - the replay end differs by p50 +0.136 s, p99 +0.964 s (fact 11). For any gap under 1 s the rule moves by at most one 1 Hz row per map.
    Rule: `map_end_300s = zero_s + second + 300 ≥ ended_s`; on these rows the target is `float(team_a_win)`, whatever the book says. `≥` matches the backtest bucket.
11. **Phase clocks (part B).** Phase 0: `second − first_frame_s` (binding, not GRID). Phase 3: `second − won_s` (s10 sets phase 3 from `won_s`). Phases 1–2: replay timer, as today.
12. **`round_dropped` (part B)** = any of the three delayed lookups (scoreboard 1 s, table 10 s, pause 0 s) returns a row of a dropped round. The round leaves with its delay tail (check1 6.12).
13. **Backtest (part B): dropped seconds join the pause tape, unshifted.** `build_cs2_clock_tape` sets `paused = shifted pause OR round_dropped`. `_prepare_map` already skips decisions on paused ticks. Reason (facts 21–22): with a decision skip alone, the last signal keeps quoting for 16 s, and after 45 s the stale exit sells the position at the ask. Rounds last longer than 45 s. The pause tape cancels orders and holds the position, as check1 intended. Cost: gate `paused` also counts dropped seconds (~0.3% of seconds: 62 of ~23 000 pilot rounds).
14. **s11 metrics with and without the last 300 s.** A new slice `map_end` with buckets `last_300s` and `before_last_300s`. «With» = the existing `all` row. The `dir_300 ≤ 0` stop rule stays on `all`.
15. **No new docstrings, no new comments, no new suppressions.** An edited function loses its docstring (PILOT-FIX-2 learning). Names are verbs.
16. **Commits.** Part A: one commit (s05 parse, contract, fetch, check_live, constant, tests; the passes are coupled). Part B: commit B1 s10 + s11, commit B2 backtest. PLAN.md stays uncommitted in `betting_workspace`.

## 4. Part A — changes by file

### 4.1 `src/cs2/constants.py`

- `CS2_ROUND_STATE_DIR = CS2_BO3_DIR / "round_state_v2"`.

### 4.2 `src/cs2/s05_replay_contract.py`

- Remove `MAX_ROUND_DIFF_SECONDS`. Add:
  ```python
  CLOCK_TOLERANCE_SECONDS = 0.3
  CLOCK_WINDOW_ROUNDS = 5
  MAX_DROPPED_ROUNDS = 2
  MAX_WINDOW_SPIKES = 2
  MAX_SPIKE_SHARE = 0.25
  ROUND_TIMER_SECONDS = 115.0
  ```
- `Cs2ReplayStatus` and `STATUS_NAMES`: `not_mr12`, `replay_clock_order`, `grid_clock_broken`, `replay_clock_steps`, `pause_in_round`, `round_clock_incomplete`, `ok`, `replay_missing`, `replay_parse_error`, `replay_not_fetched`.
- One round row; `Cs2ReplayRoundDraft` and `replay_interval_s` go:
  ```python
  class Cs2ReplayRoundRow(TypedDict):
      round_number: int
      first_tick: int
      last_tick: int
      started_at: str
      first_frame_at: str
      starts_at_freeze_end: bool
      freeze_end_at: str | None
      planted_at: str | None
      won_at: str | None
      ended_at: str
      grid_duration_s: float
      clock_offset_s: float
      start_spike_s: float | None
      clock_step_s: float
      timer_stalls: int
  ```
- `Cs2RoundClockRow(Cs2ReplayRoundRow)`: add `clock_dropped: bool` after `team_a_won`.
- `Cs2ReplayMapRow`: replace `max_duration_diff_s` with `dropped_rounds: int | None` and `start_spikes: int | None`.
- Move `ParsedRound` out of the contract into `s05_parse_replay.py`, next to `CarriedFrame` and the new `TickKill`. `s05_fetch_bo3_replays.py` imports it from there. New fields: `round_number`, `tick_rate`, `frames: list[CarriedFrame]`, `kills: list[TickKill]`, `starts_at_freeze_end: bool`, `timer_stalls: int`.
- Column lists: `ROUND_BOOL_COLUMNS = ["starts_at_freeze_end"]`, `ROUND_FLOAT_COLUMNS = ["grid_duration_s", "clock_offset_s", "start_spike_s", "clock_step_s"]`, `ROUND_TEXT_COLUMNS` gets `first_frame_at`; `CLOCK_BOOL_COLUMNS = ["starts_at_freeze_end", "team_a_won", "clock_dropped"]`.

### 4.3 `src/cs2/s05_parse_replay.py`

Pass 1 (per round, no GRID time):

- `CarriedFrame`: `tick`, `phase`, `timer_s`, `bomb`, `players: tuple[PlayerState, ...]`. Drop `at` and `offset_s`.
- `build_carried_frames(states, tick_rate)`: as today without `started_at`; it also applies the player patches and stores a sorted snapshot tuple per frame. `build_player_rows_for_picks` goes; `build_player_rows(frame.players, second)` replaces it.
- `TickKill` (frozen): `round_number`, `tick`, `killer_side`, `victim_side`. `build_kill_rows` in pass 1 returns these.
- `has_freeze_end(frames) -> bool`: any pairwise 0 → 1.
- `start_at_freeze_end(frames, tick_rate) -> list[CarriedFrame]`: frames before the first phase 2/3 frame get `phase=1`, `timer_s=ROUND_TIMER_SECONDS − elapsed` (`dataclasses.replace`).
- `parse_round(document, round_number) -> ParsedRound`: frames; if `round_number == 1 and not has_freeze_end(frames)`, apply the fix and set `starts_at_freeze_end`; kills; `count_timer_stalls` on the fixed frames.
- `find_phase_moments` returns frame indexes (`freeze_end_index`, `won_index`), not times. `note_freeze_end` follows.

Pass 2 (per map):

- `local_offsets(starts: list[float | None]) -> list[float]`: check1 `local_offsets` (fact 2). No valid start → `RuntimeError("map has no round that starts at the freeze start")`.
- `RoundClockFit` (frozen): `offset_s`, `spike_s: float | None`, `step_s`, `first_frame_s`.
- `fit_round_clocks(job, parsed) -> tuple[RoundClockFit, ...]`: `o` per round (`None` when `starts_at_freeze_end`), offsets, spikes, steps (last: `grid.start + grid.duration_s − (offset + last_tick / tick_rate)`), `first_frame_s = offset + first_tick / tick_rate`.
- `list_round_seconds(first_frame_s, next_first_frame_s, last_frame_s, is_last)`: non-last `range(ceil(first), ceil(next))`; last `range(ceil(first), floor(last_frame_s) + 1)`.
- `pick_second_frames(frames, fit, tick_rate, seconds)`: times `fit.offset_s + tick / tick_rate`, `bisect_right` as today.
- Moments: freeze end = first frame if `starts_at_freeze_end`, else the 0 → 1 frame; won = the into-3 frame (non-last) or `offset + last_tick / tick_rate` (last); plant = first phase 2 frame before the won frame (non-last) or anywhere (last), as `plant_search_limit` today. `is_break_second` uses the new won time.
- `build_round_row(grid, parsed, fit, moments) -> Cs2ReplayRoundRow` (merges `build_round_draft` and `round_row_with_interval`).
- `build_map_summary(job, parsed_rounds) -> MapSummary`: count check as today, `fit_round_clocks`, then per round: seconds, players, kills (`at` from the offset), round row.
- Status helpers on summary rows (thresholds live only here):
  ```python
  def is_dropped_round(row: Cs2ReplayRoundRow, last_round: int) -> bool
  def count_dropped_rounds(rounds: list[Cs2ReplayRoundRow]) -> int
  def list_spike_flags(rounds: list[Cs2ReplayRoundRow]) -> list[bool]  # rounds with a spike value, in order
  def count_window_spikes(flags: list[bool]) -> int                     # max over windows of 5; sum if fewer
  def is_grid_clock_broken(rounds: list[Cs2ReplayRoundRow]) -> bool
  def is_round_order_broken(rounds: list[Cs2ReplayRoundRow]) -> bool    # first_frame_at not strictly increasing
  ```
  `decide_map_status`: `not_mr12` → missing summary statuses → `replay_clock_order` → `grid_clock_broken` → `replay_clock_steps` (`count_dropped_rounds > MAX_DROPPED_ROUNDS`) → `pause_in_round` → `round_clock_incomplete` → `ok`.
- Delete `duration_mismatch`, `max_duration_diff`, `replay_interval_s`, `round_row_with_interval`, `build_round_draft`, `find_round_clock`.

### 4.4 `src/cs2/s05_fetch_bo3_replays.py`

- `parse_downloaded_round` → `parse_round(cast(ReplayDocument, json.loads(body)), grid_round.round_number)`.
- `fetch_map`: after the loop, `build_map_summary` inside `try`; on an exception `write_log_line(log, job.bo3_game_id, 0, 200, f"{type(exc).__name__}: {exc}")` and outcome `failed`.
- `build_map_row`: `dropped_rounds`, `start_spikes` (`sum(list_spike_flags(...))`), `None` without a summary.
- `clock_row_from`: new fields, `clock_dropped=is_dropped_round(round_row, last_round)`; `build_clock_rows` passes `last_round`.
- `replay_rounds_frame`, `clock_frame`, `maps_frame`: new dtypes; `dropped_rounds` and `start_spikes` as `Int64` like `timer_stalls`.
- `print_replay_summary`: one line `dropped rounds in ok maps: <n>`.

### 4.5 `src/cs2/s05_check_live.py`

- `event_comparisons`: `freeze_start` replay = `None` if `starts_at_freeze_end`, else `epoch_of(replay_round["first_frame_at"])`.

### 4.6 s10 in part A

No code change. s10 reads `started_at`, `won_at`, `team_a_side`, `team_a_won` (fact 16); part A keeps all four. Between A and B, s10 would still take the phase 0 clock from GRID and keep dropped rounds; s10 does not run on full data before part B.

### 4.7 Part A tests

Fixture: `tests/fixtures/cs2/bo3_round_173713_11.json` (17 KB, first tick 87 583, freeze end 91 566, plant 97 644, win 99 236). Multi-round maps: `shift_document(DOCUMENT, k × shift)` with `shift = last − first + 8`. GRID start of round k = `true_offset + first_tick_k / 64 + spike_k`. One helper builds `(ReplayJob, parsed rounds, GRID rows)` from a list of spikes and a list of offset steps.

| Rule | File | Test |
| --- | --- | --- |
| Local offset ignores start spikes (189809) | `tests/test_cs2_replays.py` | 30 rounds, `true_offset` 1792004710.637, spikes +0.869 (r2), +0.349 (r6), +0.553 (r14), +0.473 (r26). Every `clock_offset_s` == `true_offset` (1e-6). `start_spike_s` equals the spikes. Kills `at` and `won_at` = `true_offset + tick / 64`, not GRID start + elapsed. Status `ok`, `start_spikes` 4. |
| Clock step drops one round (190447) | same | 8 rounds; `o` jumps by +1.316 from round 5. `clock_step_s` of round 4 = 1.316; `clock_dropped` only on round 4 in `round_clock`; status `ok`, `dropped_rounds` 1. Round 4 still has 1 Hz rows. |
| > 2 dropped rounds | same | 16 rounds, offset steps of 0.5 s after rounds 4, 8, 12 (spaced so the median switches in one round) → `replay_clock_steps`. Two such steps → `ok`, `dropped_rounds` 2. A 0.29 s step → not dropped. |
| GRID clock broken | same | spikes on rounds 2, 4, 6 of 10 → `grid_clock_broken` (window). Spikes on rounds 1, 6, 10 of 10 (window max 2, share 30%) → `grid_clock_broken`. Spikes on rounds 1, 6, 11 of 12 (share 25%) → `ok`. |
| Round 1 starts at the freeze end (190444) | same | Round 1 document = fixture states from the freeze-end frame on, phase forced to 0 and timer `20 − elapsed` until the plant frame, kill keys shifted. GRID round 1 start = first frame time − 20.106 s; rounds 2–4 normal with `sp` −0.045, −0.077, 0.000. Assert: `starts_at_freeze_end`; `freeze_end_at == first_frame_at`; `start_spike_s` null; round 1 offset = median of rounds 2–4 (no spike from it); first 1 Hz row is `ceil(first frame)` (no row in the 20 s before); those rows are phase 1, `timer_s == 115 − elapsed`, `paused` False; `won_at` found; 0 stalls; status `ok`. Variant without a plant (phases 0 → 3): `won_at` found. |
| Last round wins at the replay end | same | Last round GRID end = replay end + 0.964 s. `won_at = offset + last_tick / 64`; `ended_at` = GRID end; `clock_step_s` 0.964; not dropped; status `ok`. Replaces `test_last_round_wins_at_grid_end`. |
| Round first frames out of order (189592) | same | rounds 3 and 4 with the same GRID start and spikes on rounds 1–2 → `replay_clock_order`; the summary folder exists. |
| Seconds grid | same | two rounds: round 1 rows `[ceil(b1), ceil(b2))`, round 2 rows up to `floor(offset + last_tick / 64)`. Replaces the GRID parts of `test_seconds_take_last_frame`. |
| Pass 2 error isolates the map | same | a one-round map whose round 1 starts at the freeze end → no offset → log line round 0, status 200; status `replay_parse_error`; the next map is saved. |
| Versioned folder | same | `test_ready_summary_is_not_fetched` writes into `round_state_v2`; a folder in `round_state/` does not stop a fetch. |
| check_live freeze start | `tests/test_cs2_check_live.py` | freeze start compares with `first_frame_at`; a round with `starts_at_freeze_end` gives replay `None`. |
| Typing fallout | `tests/test_cs2_dataset.py`, `tests/test_cs2_catalog.py`, `tests/test_cs2_check_live.py` | round and map fixture dicts get the new fields. |

Update or delete: `test_frame_time_anchors_to_grid_start`, `test_round_mismatch_over_half_second` (→ step tests), `test_last_round_wins_at_grid_end`, `test_missing_round_clock` (still `round_clock_incomplete` for a non-last round without a win).

### 4.8 Part A checks on real data (scratch only, before the review)

Outputs in `orchestrate/work/<implementer>/`. Read-only on every input.

1. **Rules oracle.** A script feeds `out/rounds_options.parquet` of check1 (`o`, `first_tick`, `last_tick`, `s`, `grid_duration_s` per round) into `local_offsets` and the status helpers. Expect: `off` equal to check1 `off` (max |diff| < 1e-6), dropped rounds equal to `chosen_W03.dropped`, W map-out equal to `chosen_W03.map_bad` on all 1 441 maps; R-order removes exactly 167328 and 189592 from the W-kept set.
2. **Round 1 fix on raw files.** `parse_round` on `work/check1/raw/190444_1.json` and `165997_1.json`: `starts_at_freeze_end`, first frame phase 1 timer 115.0, a won frame exists, 0 stalls. On `190447_4.json`: no fix.
3. **Real smoke, 3 maps.** `P = investigations/2026-10-18-cs2-follow300/work/scratch_pilot` holds the pilot s02–s04 tables; all three maps are `linked` there. Copy `P/bo3/links.parquet` filtered to bo3 ids 190444, 190447, 189809 into `<scratch>/bo3_links/links.parquet`. Run
   `PYTHONPATH=src uv run python src/cs2/s05_fetch_bo3_replays.py --fetch --workers 3 --links-dir P/links --grid-dir P/grid --bo3-links-dir <scratch>/bo3_links --output-dir <scratch>/bo3`
   (~165 MB, ~75 requests to bo3.gg; stop on any 429/403). Expect: 190447 round 4 `clock_step_s` ≈ 1.316 and `clock_dropped` in `round_clock`; 189809 round 2 `start_spike_s` ≈ 0.869; 190444 round 1 `starts_at_freeze_end`. Then
   `PYTHONPATH=src uv run python src/cs2/s05_check_live.py --bo3-links-dir <scratch>/bo3_links --replay-dir <scratch>/bo3 --output <scratch>/live_check.json`
   (live recordings and bo3 raw read from `data/cs2`, read-only). Expect `freeze_end` and `plant` p50 ≈ 0.1 s and no +20 s pair on round 1.

## 5. Part B — changes by file

### 5.1 `src/cs2/s10_prepare_dataset.py`

- `CLOCK_READ_COLUMNS` += `first_frame_at`, `clock_dropped`. `RoundClock` += `first_frame_at: str`, `clock_dropped: bool`; `parse_round_clock` requires both. `clock_frame` passes them.
- `stamp_clocks`: `first_frame_s` from `first_frame_at` replaces `start_s`. `build_state_frame` merges `first_frame_s` and `clock_dropped`.
- `normalize_phase`:
  ```python
  phase_clock = np.select(
      [np.equal(phase, 0), np.equal(phase, 3)],
      [second - first_frame_s, second - won_s],
      default=timer_s,
  )
  ```
- `SCOREBOARD_COLUMNS`, `TABLE_COLUMNS`, `PAUSE_COLUMNS` each get `clock_dropped`; `take_columns` skips it like `seen`. In `assemble_frame`: `round_dropped = board, table, pause clock_dropped .eq(True)` OR-ed (no match = False).
- Target in `assemble_frame`:
  ```python
  ended_s = parse_utc(row.ended_at).timestamp()
  map_end = times.to_numpy() + MODEL_TARGET_HORIZON_SECONDS >= ended_s
  values["map_end_300s"] = map_end
  values["signal_market_p_radiant_300s"] = np.where(map_end, float(row.team_a_win), market target)
  ```
- `Cs2DatasetRow`: `round_dropped: bool` after `paused`; `map_end_300s: bool` after `signal_market_p_radiant_300s`. Both in `DATASET_BOOL_COLUMNS`.

### 5.2 `src/cs2/s11_train_model.py`

- `TRAINABLE_ROWS` += `& ~pc.field("round_dropped")`.
- `LOAD_COLUMNS` += `"map_end_300s"`.
- `SLICE_MAP_END = "map_end"`, buckets `"last_300s"`, `"before_last_300s"`; `SliceLabels.map_end`; `measure_model` adds `measure_labeled(model, SLICE_MAP_END, labels.map_end, ...)`.
- `print_metrics` prints slices `all` and `map_end`; `format_metric_line` prints `row.bucket` in place of `row.slice` (the `all` line stays `cs2 all: ...`).

### 5.3 `src/backtest/cs2_inputs.py`

- `CS2_SIGNAL_COLUMNS` += `"round_dropped"`.
- `build_cs2_clock_tape(seconds, paused, dropped)`: length check for both; `paused = tuple(flag or cut for flag, cut in zip(shifted, dropped))`.
- `_prepare_map` passes `ordered["round_dropped"]`. The skip on `clock.paused[index]` covers it.

### 5.4 Part B tests

| Rule | File | Test |
| --- | --- | --- |
| `round_dropped` span | `tests/test_cs2_dataset.py` | 3 rounds, round 2 `clock_dropped`. `round_dropped` is True from the first second of round 2 (pause lookup, delay 0) to the first second of round 3 + 9 (table delay 10); False elsewhere and before the first frame. |
| Phase 0 clock from the binding | same | round with `first_frame_at` 0.869 s before GRID `started_at` (189809 r2): phase 0 rows count from `first_frame_at`. Update `test_freeze_clock_counts_up_through_pause`. |
| Phase 3 clock | same | phase 3 rows: `phase_clock_s == second − won_s`; phase 1–2 rows keep `timer_s`. |
| Map-end target | same | `team_a_win` False: rows with `t + 300 ≥ ended_at` have target 0.0 and `map_end_300s`, also where s09 gave a number; earlier rows keep the s09 value (NaN stays NaN). |
| Trainable rows | `tests/test_cs2_train.py` | extend `test_loader_drops_not_ok_paused_and_empty_target`: a `round_dropped` row is dropped. |
| Last-300 s metrics | same | metric rows of slice `map_end` have buckets `last_300s` and `before_last_300s`; their row counts add up to the `all` row. |
| Dropped tape | `tests/test_cs2_backtest.py` | `build_cs2_clock_tape((0..5), paused F×6, dropped (F,F,T,T,F,F))` → paused `(F,F,T,T,F,F)` (no shift); with a pause too, both apply. Extend `test_no_decision_on_shifted_pause_or_missing_book`: no decision on dropped seconds. |

### 5.5 PLAN.md (`betting_workspace/tasks/cs2-follow300/PLAN.md`, uncommitted)

Rewrite these places; numbers from check1 and this plan:

1. «Сопоставление», item 5: round clocks by binding W (link to «Часы раундов»).
2. «Часы раундов (s05)»: binding W formulas (`o`, `off`, window of 5, rounds without `o`); round 1 that starts at the freeze end (phase 1, timer 115 − elapsed, not a pause); last round win = replay end, GRID end only a check; summary folder `round_state_v2`; summary ~0.1 MB per map.
3. «Сверки в s05», item 1: W rules (0.3 s step drops the round; map out on > 2 dropped rounds, > 2 spikes in 5 rounds, > 25% spike rounds) and R-order; statuses `replay_clock_order`, `grid_clock_broken`, `replay_clock_steps`.
4. «Сверки в s05», item 4: outcome of check 4 — timer phase 3 replaced, timer phases 1–2 and loadout kept.
5. «Признаки игры»: phase 0 = seconds since the round's first frame (binding); phase 3 = seconds since phase start; phases 1–2 = timer.
6. «Секунды рынка и датасет», target: map end ≤ t + 300 → `team_a_win`; map end = catalog `ended_at` and why; remove «Выплатой её не заменяем»; columns `round_dropped`, `map_end_300s`.
7. «Модель (s11)»: training drops `round_dropped`; metrics with and without the last 300 s.
8. «Бэктест»: `round_dropped` seconds stand on the pause tape without the 1 s shift.
9. «Риски», «Конец карты»: the target is no longer empty there.

## 6. Order of work

1. Part A: code, tests, basedpyright, the three real-data checks (4.8), one commit, report.
2. Part A review. Then STEP-015 starts the full s05 download (into `round_state_v2`).
3. Part B: code, tests, basedpyright, commits B1 and B2, PLAN.md, report. No data run.
4. Part B review. Then s06…s11 and the backtest read the new summaries.

Test command for both parts (goldens skipped; both parts touch only CS2 files):
`PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`, then `uv run python -m basedpyright`.

## 7. Edge cases

1. **Map whose every round lacks `o`.** Only round 1 can lack it (fact 8). A one-round map with that round 1 → pass 2 error → `replay_parse_error` (decision 8).
2. **Tick reset inside the map.** The median switches at the reset; the round before it gets a huge step and drops (check1 section 2). At the map start the median can take the broken value: R-order catches the two pilot cases (fact 6).
3. **Round 1 fix and a real pause at its start.** A file that starts at the freeze end hides that pause. check1 saw no phase 0 timer below −115 s (`a7_final.txt`). The fix does not flag such a pause.
4. **Last round clock stop.** W cannot see it (no next round). `clock_step_s` of the last round is stored for checks.
5. **Rows after the replay end.** s10 rows run to `floor(GRID end)`; the backward lookup carries the last summary row. With the map-end target these rows have target = result.
6. **Old `replay_fetch_log.jsonl`.** A 404 line still marks a map without a summary as `replay_missing`, and s05 still retries it. A status-200 line matters only for maps without a v2 summary.
7. **Overlapping 1 Hz rows** exist only in `replay_clock_order` maps; no reader takes non-`ok` maps.
8. **Phase 3 of the last round.** No phase 3 frame; s10 marks seconds ≥ `won_s` as phase 3, and the last summary row is `floor(won)`, before the win. So there are no phase 3 summary rows in the last round.

## 8. Risks

- **Binding change after the download** costs a new download of ~0.6 TB. The real smoke (4.8.3) and the rules oracle (4.8.1) exist to catch that before the review.
- **Expected `ok` is ~1 073 of 1 441 pilot maps, not 1 107** (fact 10). The STEP-015 catalog estimate in the investigation report uses neither number; pilot2 or STEP-015 should recount it.
- **Memory of s05.** Pass 2 holds the frames of every in-flight map: ~20–30 MB per map, ~0.5 GB at 16 workers. I did not measure it. The real smoke can print the peak RSS for 3 workers.
- **Pause gate count** in the backtest report mixes real pauses and dropped rounds (decision 13).
- **Map end by GRID** lags the replay end by up to +8.5 s on one W-kept map (fact 11). On such a map up to 8 rows ~300 s before the end keep the book value at a moment after the real end, not the result.
