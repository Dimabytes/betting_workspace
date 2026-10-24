# STEP-010 plan: CS2 replay CLI and acceptance on the 21-round tape

Repo: `../esports-trader` at `5f3cbecd` (worktree clean). Planner: plan-s10.
Scratch (measurement scripts on unchanged code only): `tasks/cs2-live/orchestrate/work/plan-s10/`.

The step adds `trader.replay_cs2_rounds`: a CLI that folds a recorded GRID tape through `Cs2FrameReducer`, fires each round call on a virtual clock, reads `m0` and the prior from a compatible book file, scores the call with the production models, and prints a round ledger. It adds the tape acceptance test, a synthetic test for the book paths, and a synthetic chain test feed → `RoundSignals` → `LiveCore` with $5 levels. It reruns every Dota/LoL gate on the final head. No order leaves the process. No deploy, no push.

## 1. Facts this plan rests on

### 1.1 Code the step builds on

- `Cs2FrameReducer` (`src/trader/cs2_feed.py:133`). Public: `reduce_frame(frame, now)` (:162), `note_feed_gap()` (:173), `read_pinned_horn_iso()` (:178), attribute `board: Scoreboard | None` (:147). Round facts live in the private list `self._rounds` (:154); no public accessor exists. Orientation: team a is `board.teams[0]` (`Cs2GridFeed.read_board_sides`, :103-118).
- The reducer emits `FeedEvent` (lock, clock boards, terminal), `RoundLiveTick`, `RoundDecisionDue`, `RoundHistoryLost` (`src/trader/cs2_round_types.py:43-61`). `RoundDecisionDue` carries `round_number`, `due_ns`, `team_a_ct`, `header: MapHeader`, and a frozen `rounds` snapshot (:49-55).
- The first `FeedEvent` carries `yes_is_radiant = sides.outcome_0_is_radiant` and `prior_anchor = PriorAnchor(map_live_unix - 85)`, or `PriorAnchor(None)` on a late join (`cs2_feed.py:594-625`).
- `replay_grid_records` (`src/trader/grid_feed.py:343-351`) yields only `FeedEvent`. It drops `RoundDecisionDue` and `RoundLiveTick`. Its one caller is the viewer (`src/viewer/game_state_replay.py:200`).
- Live call path (`src/trader/round_signals.py`): `_arm_due` sets a timer at `max(0, due_ns - time.time_ns())` (:158-166). On the timer the requote reads `market_p_radiant` and calls `_start_call` (:241-260). `None` m0 drops the call with cause `nan_m0` (:255-259). `_on_live_start` drops a pending due or call for the same or an earlier round (:175-184). `RoundHistoryLost` and a finished `FeedEvent` close the map (:125-129, :186-194). `_execute_call` = `build_round_frame(header, rounds, RoundCallInputs(team_a_ct, m0, prior))` + `predict_round_call(models, frame)` (:53-64).
- `build_round_frame` returns the raw frame; `FEATURES` exist only after `add_derived(frame, models.delta.map_codes)` (`src/trader/cs2_rounds.py:73-91`; progress STEP-008). `OUTCOME_FEATURES = (*FEATURES, "a_won")` (`src/cs2/s11_round_frame.py:46-78`). A NaN `m0` gives `model_delta = pred_delta = up_a = up_b = 0` (`known_cash_mask`, :363).
- Book replay: `parse_book_events(lines, needles)` reads collector-journal bytes lines (`kind == "market_event"`, payload type book / price_change / last_trade_price; a line without `\n` is skipped) (`src/trader/book_prior.py:158-168`). `replay_token_books(events, token_ids)` returns `dict[str, TokenBook]` (:171-193). `lookup_strict_prior(radiant, dire, anchor_us, window_seconds)` (`src/shared/utils/telonex_book.py:109-126`); live uses `DOTA_PRIOR_WINDOW_SECONDS = 61` (`book_prior.py:94-104`). The as-of mid with the 5 s age, spread and pair gates is `resolve_market_pair(find_asof_quote(a, t), find_asof_quote(b, t))` (`telonex_book.py:573-623`); its `status` names the failed gate.
- CLI pattern: `replay_core_trace.main(argv)` with argparse and `raise SystemExit(main(sys.argv[1:]))` (`src/trader/replay_core_trace.py:570-598`).
- Models: `load_cs2_round_models(CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR)` (`src/trader/cs2_models.py:42`), the same dirs as `GAME_PROFILES["cs2"]` (`src/trader/game_profile.py:111-125`).
- Worker chain hooks: `MatchWorker.run` calls `signals.on_tick(tick, core_now_ns())` on every tick, then `handle_event` on a `FeedEvent` (`src/trader/match_worker.py:233-262`). `open_round_buys` / `close_round_buys` set `core.buys_open`; close also schedules `_schedule_entry_cancel()` (:990-1000, :1029-1036). `feed_core` sets `GameClock(game_ended=finished)` (`src/trader/session_core.py:1013-1023`); the cycle reads `core.latest_clock` (`src/trader/host_resources.py:159-172`).
- Core gates for the chain test: `buy_closed_reason` returns `"fair"` when `allow_buy` is false, `"min_delta"` below `min_abs_delta` (`src/strategy/quoting.py:237-262`); `game_end` blocks at `quoting.py:901`. Rung size is `min(level_usdc, bid_depth_usdc)` → `buy_share_quantity` (`quoting.py:325-326`, `src/shared/utils/trading.py:15`). `MIN_RUNG_USDC = 5.0`, `BUY_LEVEL_COUNT = 3`, `MIN_ENTRY_PRICE = 0.45`, `MAX_ENTRY_PRICE = 0.85`, `CS2_MIN_ABS_DELTA = CS2_EXIT_ABS_DELTA = 0.01`, `EXIT_SETTLE_SECONDS = 10.0` (`src/shared/constants/strategy.py`). Round SELL fair is `anchor_p + up_radiant` for the radiant token (`src/strategy/round_sides.py:21-40`).
- Test helpers that exist: `push`, `board`, `table` (`tests/test_cs2_feed.py:52-122`); `r1_frames`, `MARKET_A/B`, `DAMAJUANA`, `HACHA`, stamps (`tests/cs2_grid_widget_fixtures.py:25-41, 294`); `wrap` (`tests/grid_widget_fixtures.py:173`); `_tiny_models`, `_ready_books`, `_install_predict` (`tests/test_trader_round_signals.py:82-165, 548-553`); `_accept_open_orders` (`tests/test_core_trace_round.py:46`); `build_attached_worker(..., game="cs2", level_usdc=..., feed_timeout_seconds=..., trace=...)` (`tests/trader_session_fixtures.py:618-680`). FakeEngine market books: YES 0.47/0.50, NO 0.49/0.52 (`trader_session_fixtures.py:479-484`).

### 1.2 Measurements on unchanged code (`work/plan-s10/`)

- `tape-baseline.txt` (`tape_baseline.py`, 0.67 s): 21 `RoundLiveTick` (R1 `21:13:21.315Z`), 21 `RoundDecisionDue` for rounds 1–21, 0 losses. Header `MapHeader(map_code=7, game_number=3, series_score_a=1, series_score_b=1)`. Due R1 `21:13:01.416Z` (freeze `21:12:59.416` + 2 s). Due R2 `21:14:28.670Z` (END R1 `21:14:18.670` + 10 s). Due R13 `21:40:21.124Z` (freeze `21:40:19.124` + 2 s). END R12 `21:39:58.377Z`, R13 `21:41:49.778Z`, R21 `21:58:37.295Z`. All 21 facts equal the PLAN 2C winners, win types, final kills and loadouts. `team_a_ct` false R1–12, true R13–21. R6 `planted` false (STEP-007 rule).
- Map end: the R21 END frame (line 1429, status `live`, score 8:13) and the first `finished` board (line 1431) both carry `occurredAt 21:58:37.295Z`. Receipts are `21:58:37.733Z` and `21:58:38.807Z`. So "finished 1 s after the last END" (PLAN 2B.3) is receipt time. By `occurredAt` the two are equal, and "map end 21:58:37" (PLAN 2C) is the `occurredAt` second.
- `call-margins.txt`: every due tick arrives 0.70–1.67 s before its `due_ns`, so the virtual call time is `due_ns` in all 21 rounds. Call to next live-start receipt is 16.6 s minimum (R14); R2 251.6 s, R10 55.9 s, R17 59.5 s.
- In-map socket markers: disconnect `21:16:46.620Z` (120 s idle, R2 pause) and `21:39:54.985Z` (R12). `grid.jsonl` has 1795 records, 156 markers, receipt order never decreases.
- `books-tokens.txt`: `target.json` and `event.json` have Map 1 Winner, Map 2 Winner, series moneyline and O/U markets. **There is no Map 3 Winner market.** `books.jsonl` holds only those tokens (series 531k mentions per token, Map 2 173k, Map 1 106). Its records are raw websocket messages (`{"received_at_utc", "raw"}` or markers), not the collector journal. For map 3 the book is absent by both rules.
- `tape-features-baseline.txt` (`tape_features_baseline.py`): model load 0.08 s, fold 0.71 s, 21 NaN-m0 calls 0.14 s. Call-row values after `add_derived`: R1 `cash_a=cash_b=4000`, `is_pistol_round=1`; R2 `cash_a=18250`, `cash_b=11700`, `prev_kills_a=3`, `prev_kills_b=4`, `prev_planted=1`; R13 `team_a_ct=1`, `is_pistol_round=1`, `half=2`, `prev_kills_a=2`, `prev_kills_b=5`, cash 4000/4000; R21 `team_a_score=8`, `team_b_score=12`, `loss_streak_a=4`. `m0`, `logit_m0`, `market_radiant_prior`, `a_won` are NaN. Every prediction field is 0.
- STEP-008 tape ledger with a synthetic `m0 = 0.5` (`investigations/2026-10-23-cs2-live-validation/work/step-008/tape-ledger-fix.txt`): R1 `pred_delta` 0, R13 `-1e-4`, every output finite, median call 143 ms.
- The 13 core-trace sessions in `work/plan-s5/core_sessions.txt` are all present under `data/trader/`. The latest gate outputs on `5f3cbecd` are `work/impl-s9/grid-fix.txt`, `signals-fix.txt`, `core-fix/`, `model-smoke-fix.txt` (sha `df2fb0a8…784c5e` and `7b70dde6…c3445e`, equal to the STEP-005 baselines).

## 2. Design decisions

D1. **The CLI folds frames itself.** It does not use `replay_grid_records`. Widening that generator to `FeedTick` would change the viewer's Dota/LoL path for no gain. The fold is the same three calls the reducer tests use: `parse_frame`, `parse_utc(received_at_utc)`, `reducer.reduce_frame`. Records with `marker` are skipped; the reducer never sees them. File order is receipt order (measured).

D2. **Virtual call clock, same rules as `RoundSignals`.** A due received at `r` has call time `max(due_ns, r)`. Before the CLI reduces a frame received at `t`, it fires the pending due if `t >= call time`. A `RoundLiveTick` for the same or a later round, received before the call time, cuts the pending due with status `live_round`. A `RoundHistoryLost` or the first finished `FeedEvent` cuts it with `map_closed`. A due still pending at the end of the file gets `tape_end`. A fired call with a NaN `m0` gets `nan_m0` (live drops it, `round_signals.py:255`). A fired call with a finite `m0` gets `called`. Every due is scored at its call time, whatever its status, so the ledger always shows the features. The ~143 ms model time is not simulated; the tape margin is ≥16.6 s.

D3. **No future data in a call.** A call uses only `due.rounds` (frozen at the due frame), `due.team_a_ct`, `due.header`, `m0` as-of the call time, and the prior as-of the lock. A prefix test pins this (§4.1).

D4. **Book status is explicit; no substitute price.** Order: (1) no market whose `question` ends with `Map {map_number} Winner` in `target.json` → `no_map_market`; (2) no `books.jsonl` → `missing`; (3) the first line of `books.jsonl` has no `kind` key → `incompatible`; (4) else `ok`. `target.json` is a required input of a tape dir; a missing one raises. With `ok`, `m0` = `resolve_market_pair(find_asof_quote(team_a, call_us), find_asof_quote(team_b, call_us)).market_p_radiant`, and `m0_status` is that result's `status`. Without `ok`, `m0` is NaN and `m0_status` is the book status. The prior is `lookup_strict_prior(team_a, team_b, anchor_us, DOTA_PRIOR_WINDOW_SECONDS)` with the lock event's `prior_anchor`, else NaN. Team-a token: the token of `outcome_0` when `yes_is_radiant`, else the token of `outcome_1` (token order from the market's `outcomes`). On this tape the result is `no_map_market` for map 3. The `ok` path is exercised only by a synthetic tape (§4.2). A raw-websocket adapter is out of scope: it is a new reader for one file whose map 3 has no market.

D5. **One public read on the reducer.** Add `Cs2FrameReducer.rounds -> tuple[RoundFacts, ...]` (property, `tuple(self._rounds)`). The R21 facts exist in no due (no due 22 after the map ends), and the test and the ledger need them. No other reducer change.

D6. **Ledger lines are plain `key=value` text.** Stamps print through `received_at_utc(datetime.fromtimestamp(ns / NS_PER_SECOND, UTC))` (`src/trader/live_feed.py:169`). Floats print with `:.6g` (`nan` stays `nan`). Tests assert on the returned dataclasses, not on text, except one `main()` smoke.

D7. **The chain test is new and synthetic.** It drives a `build_attached_worker(game="cs2")` worker from `Cs2FrameReducer` ticks the way `MatchWorker.run` dispatches them, with a stub `predict_round_call` so the core decisions are fixed. `build_round_frame` stays real, so the stub sees a frame built from the reducer's facts. The paper FakeEngine is the venue; nothing leaves the process.

## 3. File-by-file changes

### 3.1 `src/trader/cs2_feed.py`

Add the read-only property `rounds` on `Cs2FrameReducer` (D5), next to `read_pinned_horn_iso`. No other change. Dota/LoL do not import this module's reducer.

### 3.2 New `src/trader/replay_cs2_rounds.py`

Constants: `GRID_FILENAME = "grid.jsonl"`, `BOOKS_FILENAME = "books.jsonl"`, `TARGET_FILENAME = "target.json"`.

Types (frozen dataclasses; `Literal` for the two vocabularies, like `AsOfStatus`):

- `BookStatus = Literal["ok", "missing", "incompatible", "no_map_market"]`.
- `CallStatus = Literal["called", "nan_m0", "live_round", "map_closed", "tape_end"]`.
- `_TapeMarket` / `_TapeTarget` TypedDicts for the `target.json` fields read: `question: str`, `token_ids: list[str]`, `outcomes: list[str]`; `markets: list[_TapeMarket]`.
- `_TapeBooks(status: BookStatus, by_token: Mapping[str, TokenBook])`.
- `TapeLock(teams: tuple[TeamSide, ...], yes_is_radiant: bool, prior_anchor_unix: int | None, prior: float)` — teams in board order, team a first.
- `TapeCall(due: RoundDecisionDue, due_received_ns: int, call_ns: int, status: CallStatus, m0: float, m0_status: str, prior: float, prediction: RoundCallPrediction, features: tuple[float, ...])` — `features` aligned with `OUTCOME_FEATURES`.
- `TapeFinish(occurred_at: str, received_at: str)` — the finished board's raw `occurredAt` and the record's `received_at_utc`.
- `TapeReplay(book_status: BookStatus, lock: TapeLock | None, calls: tuple[TapeCall, ...], lives: tuple[RoundLiveTick, ...], losses: tuple[RoundHistoryLost, ...], rounds: tuple[RoundFacts, ...], disconnects: tuple[str, ...], finish: TapeFinish | None)`. `disconnects` are receipt stamps of `disconnect` markers after the lock and before the finish.

Functions (all arguments required, verb names):

- `replay_cs2_tape(tape_dir: Path, map_number: int, outcome_0: str, outcome_1: str, models: Cs2RoundModels) -> TapeReplay`: read the target, find the map market, read the books (D4), build `Cs2GridFeed().build_reducer(map_number, outcome_0, outcome_1, CS2_TEAM_ALIASES)`, fold `grid.jsonl` line by line through a small private fold object, return the result.
- `find_map_market(target, map_number) -> _TapeMarket | None`.
- `read_tape_books(path: Path, market: _TapeMarket | None) -> _TapeBooks`: D4 checks; on `ok`, `parse_book_events` over the binary file handle with the two token ids as needles, then `replay_token_books`.
- A private fold class (`_TapeFold`) holds the reducer, models, books, market, outcome names, the pending due and the lists. Methods: `take(record)` (marker → note disconnect; else fire a ripe pending due, reduce the frame, route the tick); one method per tick kind (due, live, loss, event); `_score(due, due_received_ns, call_ns, status) -> TapeCall` (m0 and prior per D4, `build_round_frame`, `predict_round_call`, `add_derived(frame, models.delta.map_codes).iloc[-1]` → `features`, `nan_m0` when the status would be `called` and `m0` is NaN); `result() -> TapeReplay` (closes a pending due as `tape_end`, takes `reducer.rounds`). Keep each method under the C901 limit (10).
- `print_ledger(replay: TapeReplay) -> None`. Lines, in this order:
  1. `book status=… prior=…`
  2. `lock team_a=<id>:<name> team_b=<id>:<name> yes_is_radiant=… prior_anchor=<iso|none>` (or `lock none`)
  3. per call: `call round=N due=… due_received=… call=… status=… m0=… m0_status=… prior=… model_delta=… pred_delta=… up_a=… up_b=…`, then `features round=N <name>=<value> …` over `OUTCOME_FEATURES`
  4. per fact: `round=N winner=<a|b> win_type=… kills=a/b seen_kills=a/b alive=a/b loadout=a/b team_a_ct=… planted=… live=… end=…`
  5. `live round=N live=…`, `loss round=N reason=…`, `disconnect received=…`
  6. `finish occurred=… received=… last_end=…` (or `finish none`)
  7. `summary rounds=… dues=… lives=… losses=… called=… nan_m0=… live_round=… map_closed=… tape_end=…`
- `main(argv: list[str]) -> int`: argparse positionals `tape_dir` (Path), `map_number` (int), `outcome_0`, `outcome_1`; load the models with `load_cs2_round_models(CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR)`; replay; print; return 0. `if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))`.

No comments, no narrating docstrings (config review rule). The module must not import `backtest`.

## 4. Tests

### 4.1 New `tests/test_cs2_tape_replay.py` — real tape

`TAPE_DIR = CS2_DATA_DIR / "live" / "cs2-damaju-hachae-2026-10-21"`. A `needs_tape = pytest.mark.skipif(not (TAPE_DIR / "grid.jsonl").is_file(), reason=...)` marker on the tape tests only. One module fixture replays the tape once with the real models (`load_cs2_round_models`), args `3, MARKET_A, MARKET_B`.

1. `test_tape_rounds_match_the_recorded_map`: 21 facts R1–21. `a_won` true exactly for {1, 2, 3, 4, 6, 7, 14, 15}. Win types: R1/2/6/9/10/13/14/17/18/21 `opponentEliminated`, R3/4/7/16/19/20 `bombExploded`, R8/11/12/15 `bombDefused`, R5 `timeExpired`. Final kills and loadouts for all 21 rounds (table in `investigations/.../work/step-007/tape_smoke.py` `EXPECTED`; it contains every PLAN 2C value). `team_a_ct` false R1–12, true R13–21. R6 `planted` false. `lock.teams[0].team_id == DAMAJUANA` (56753), `lock.teams[1].team_id == HACHA` (59048), `lock.yes_is_radiant` true. `calls[0].due.header == MapHeader(7, 3, 1, 1)`.
2. `test_tape_clock_marks_use_frame_occurred_at`: lives R1–21, R1 `live_ns == ns("2026-10-21T21:13:21.315Z")`. END R1 `21:14:18.670Z`, R12 `21:39:58.377Z`, R13 `21:41:49.778Z`, R21 `21:58:37.295Z` (compare ns with `datetime_to_ns(parse_utc(...))`). Due R1 = `21:12:59.416Z` + 2 s. Due R13 = freeze `21:40:19.124Z` + 2 s, and not END R12 + 10 s. Every other due = previous END + 10 s. 0 losses.
3. `test_tape_finish_is_a_separate_board_with_the_last_end_stamp`: `finish.occurred_at == "2026-10-21T21:58:37.295Z"` equals R21 END; `finish.received_at == "2026-10-21T21:58:38.807Z"`. This resolves the PLAN wording: equal `occurredAt`, a later receipt.
4. `test_tape_calls_fire_on_the_virtual_clock_with_no_book`: 21 calls R1–21. `call_ns == max(due.due_ns, due_received_ns)`. `call_ns` is before that round's `RoundLiveTick.live_ns`. `book_status == "no_map_market"`. Every call: status `nan_m0`, `m0` NaN, `m0_status == "no_map_market"`, prior NaN, all four prediction fields 0.0. `disconnects == ("2026-10-21T21:16:46.620Z", "2026-10-21T21:39:54.985Z")`.
5. `test_tape_call_rows_hold_the_offline_features`: the §1.2 values for R1, R2, R13, R21 by name through `OUTCOME_FEATURES`; `m0`, `logit_m0`, `market_radiant_prior`, `a_won` NaN.
6. `test_tape_facts_score_with_a_labeled_synthetic_m0`: for R1, R2, R13, R21, call `build_round_frame(call.due.header, call.due.rounds, RoundCallInputs(call.due.team_a_ct, 0.5, nan))` + `predict_round_call` directly. The test name and a variable say synthetic; the CLI never does this. All outputs finite. R1 and R13 `abs(pred_delta)` in `{0.0, 1e-4}`. R13 call row `team_a_ct` true.
7. `test_tape_prefix_does_not_change_an_earlier_call`: for R2 and R13, write `target.json` and the `grid.jsonl` lines with receipt ≤ that call's `due_received_ns` into `tmp_path`, replay, and compare the last call with the full run: same `call_ns`, `prediction`, features (NaN-equal), status `tape_end` instead of `nan_m0`.
8. `test_main_prints_one_line_per_round`: `main([str(TAPE_DIR), "3", MARKET_A, MARKET_B]) == 0`; `capsys` output has 21 lines starting with `round=` and 21 starting with `call `, and one `summary` line.

### 4.2 Same file — synthetic book paths (always run)

Helper writes a tape dir: `grid.jsonl` from `r1_frames()[:3]` (lock, freeze, live; receipts increase) as `{"received_at_utc": stamp, "frame": wrap(service, delay, payload)}`, plus one `disconnect` marker after the lock; `target.json` with one `"… - Map 3 Winner"` market, tokens `tok-a`/`tok-b`, outcomes `MARKET_A`/`MARKET_B`. Models: `_tiny_models()` from `tests/test_trader_round_signals.py`.

`test_book_status_marks_m0_and_prior` parametrized:

- `ok`: `books.jsonl` in journal form, each line ending in `\n`: `{"kind": "market_event", "receivedAtUs": …, "payload": {"type": "book", "tokenId": …, "bids": […], "asks": […], "timestamp": null}}`. At `21:11:00Z` team a 0.60/0.62, team b 0.38/0.40 (inside `[anchor - 61 s, anchor)`, anchor `21:11:34Z`). At `21:13:00Z` team a 0.55/0.57, team b 0.43/0.45. Expect book `ok`, prior ≈ 0.61, R1 call status `called`, `m0` ≈ 0.56, `m0_status == "ok"`, prediction finite.
- `incompatible`: `books.jsonl` with one raw-websocket line shaped like the tape. Expect m0 and prior NaN, status `nan_m0`, `m0_status == "incompatible"`.
- `missing`: no `books.jsonl`. `m0_status == "missing"`.
- `no_map_market`: the market says `Map 2 Winner`. `m0_status == "no_map_market"`.

All cases: one disconnect stamp recorded, one live tick, R1 `call_ns == due_ns`.

### 4.3 New `tests/test_cs2_round_chain.py` — feed → RoundSignals → core

Fixtures: module `tiny_models` from `_tiny_models()`. Worker: `build_attached_worker(tmp_path, request, game="cs2", model=tiny_models, level_usdc=5.0, feed_timeout_seconds=CS2_FEED_STALE_SECONDS, trace=CoreTrace.open(path))`; `_ready_books(worker)`; core clock patched through `trader.match_worker.core_now_ns`, as in `test_publish_opens_buys_on_the_trace`. Stub `trader.round_signals.predict_round_call` with a function that records `len(frame.index)` and returns `RoundCallPrediction(model_delta=d, pred_delta=d, m0=frame["m0"].iloc[-1], up_a=0.05, up_b=0.04)`. Cycle helper: `run_core_cycle(core, …, books=<YES 0.47/0.50, NO 0.49/0.52, depth DEEP_BOOK_SHARES>, clock=core.latest_clock, …)`, the same prices as the FakeEngine market books. Dispatch helper: `tick = push(reducer, frame)`; `worker._signals.on_tick(tick, now)`; `await worker.handle_event(tick)` for a `FeedEvent`. Frames: `r1_frames()[0]` (lock), `[1]` (freeze → due R1), `[2]` (live R1), then a finished board (`board(..., status="finished")`). Everything runs inside one `asyncio.run`.

1. `test_round_chain_opens_sizes_cancels_sells_and_ends` (`d = 0.012`):
   - After the lock and before the call: `core.buys_open` false; a cycle plans no BUY.
   - After the freeze: wait until `core.buys_open`. The stub saw one call with a 1-row frame; its `m0` equals `core_book_p` on the FakeEngine market books (about 0.49); the frame prior is 0.5 (fixture prior).
   - BUY cycle: exactly `BUY_LEVEL_COUNT` (3) BUY places (0.47, 0.46, 0.45 from the 0.47 join, `src/strategy/signals.py:137`), all on token 0, each price `>= 0.45` and `< 0.85` (`buy_reject_reason`, `signals.py:80-100`), each `quantity == buy_share_quantity(base_size_usdc=5.0, price=price)`.
   - Place those quotes on the fake gateway and state, accept them in the core, apply one `Fill` of the top rung to the core.
   - Live R1: `core.buys_open` false; after `asyncio.gather(*worker._scheduled_cancels)` the fake state has no BUY order; the next cycle has no BUY in `to_place`.
   - After `EXIT_SETTLE_SECONDS + 1` and one requote (`worker._quote_pm` on the lock event, to refresh the signal): one SELL on token 0 for the filled quantity, price within one tick of `anchor_p + up_radiant` of the core signal and above the YES ask 0.50 (round lots, not the ask).
   - Finished board: `worker._signals.buys_open` false; `core.latest_clock.game_ended` true; a cycle has no BUY in `to_place`.
2. `test_round_chain_below_threshold_places_no_buy` (`d = 0.008`): after the call, `core.buys_open` true, a cycle has no BUY, `core.preview_block_reason() == "min_delta"`.

If `_quote_board` does not publish inside `_until` (it needs a fresh board source from `handle_event(lock)`), requote once by hand, as the STEP-009 test does.

### 4.4 Unchanged tests

No existing test changes. `test_trader_round_signals.py` stays at 832 lines; the chain test gets its own file. Do not import fixture functions decorated with `@pytest.fixture` across modules; import `_tiny_models` and define the fixture locally.

## 5. Verification

Run from `../esports-trader`. Never run two `-n` suites at once. Do not run the suite during a live map. Logs go to `betting_workspace/tasks/cs2-live/orchestrate/work/impl-s10/`. Evidence goes to `betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-010/`.

The acceptance package is items 1–10. Write `date -u +%FT%TZ` to `work/step-010/package-start.txt` before item 1 and `package-end.txt` after item 10. The budget is one hour; the expected time is about 8 minutes (suite ~115 s, core replay ~136 s, signals ~42 s, GRID ~17 s, typecheck ~13 s). A failed or skipped item is a failure; an old result does not replace a rerun.

1. CLI: `PYTHONPATH=src .venv/bin/python -m trader.replay_cs2_rounds data/cs2/live/cs2-damaju-hachae-2026-10-21 3 Damajuana "Hacha Esports" > <step-010>/tape-ledger.txt`. Exit 0. 21 `round=` lines, 21 `call` lines, `book status=no_map_market`, `summary … nan_m0=21`.
2. Tape and book tests, not skipped: `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -rs tests/test_cs2_tape_replay.py tests/test_cs2_round_chain.py`. The result line must show 0 skipped.
3. `.venv/bin/ruff check src tests scripts`
4. `.venv/bin/ruff format --check src tests scripts`
5. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`
6. Targeted Dota/LoL shared-path and CS2 tests: `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 tests/test_grid_feed.py tests/test_follow300_signals.py tests/test_board_signal_contract.py tests/test_trader_session_core.py tests/test_core_trace.py tests/test_core_trace_round.py tests/test_trader_session_quoting.py tests/test_trader_game_profile.py tests/test_trader_wallet_host.py tests/test_trader_host_resources.py tests/test_trader_match_lifecycle.py tests/test_follow300_replay.py tests/test_extraction_oracle.py tests/test_cs2_feed.py tests/test_cs2_widgets.py tests/test_cs2_rounds.py tests/test_cs2_models.py tests/test_trader_round_signals.py tests/test_trader_cs2_discovery.py tests/test_cs2_tape_replay.py tests/test_cs2_round_chain.py`
7. Full: `PYTEST_N=8 make test`. Known flakes: `test_upcoming_rows_from_sidecars` fails 23:00–00:00 Berlin; `test_follow300_replay` `dota_9007700576` flakes under xdist (rerun alone, report both).
8. Dota/LoL gates on the final head, compared with the `5f3cbecd` outputs (all after STEP-005 path changes in STEP-006/007/009):
   - GRID: `PYTHONPATH=src:tests .venv/bin/python ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/grid_reducer_diff.py data/trader ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/sessions.txt > <impl-s10>/grid-head.txt`; `cmp` with `work/impl-s9/grid-fix.txt`; 24427 lines, sha `df2fb0a8…784c5e`.
   - Signals: same harness with `work/plan-s5/signals_diff.py`; `cmp` with `work/impl-s9/signals-fix.txt`; 27514 lines, sha `7b70dde6…c3445e`.
   - Core replay: `../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/run_core_replay.sh ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/core_sessions.txt <impl-s10>/core-head src`; 13 sessions, 1102495 events, each `plan=0 wake=0 digest=0`, `exit=0`; summary files byte-equal to `work/impl-s9/core-fix/`.
   - Models: `PYTHONPATH=src .venv/bin/python ../betting_workspace/tasks/cs2-live/orchestrate/work/impl-s9/model_smoke.py`; `diff` with `work/impl-s9/model-smoke-fix.txt` is empty.
9. Offline round predictions: `PYTHONPATH=src .venv/bin/python ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s8/round_preds_sha.py data/backtests/cs2_maker/LIVE/seed0/round_preds.parquet` → `sha256=8d6f38cd…4d8891`.
10. Imports: `PYTHONPATH=src .venv/bin/python -c "import trader.replay_cs2_rounds, trader.game_profile, trader.match_worker"`; `grep -rn --include='*.py' -E 'from backtest|import backtest' src/trader src/strategy src/cs2 src/shared` → `src/strategy/signals.py:1` only.
11. Append `## STEP-010` to `investigations/2026-10-23-cs2-live-validation/report.md` (append only): revisions; the package start/end stamps and duration; the ledger path; a table "PLAN 2C expected | tape actual" for winners, win types, kills, loadouts, END R1/R12/R13/R21, due R2/R13, live R1, map end; the six notes in §6 (no Map 3 market, raw book format, `occurredAt` vs receipt at map end, R6 planted, disconnects not replayed, pistol ±1e-4 only under synthetic m0); every gate with its command and result. Then the `progress.txt` entry and one commit in `../esports-trader`.

Data: all on this machine — the tape (`books.jsonl`, `event.json`, `grid.jsonl`, `target.json`), `data/cs2/models/round` and `round_outcome`, `data/trader/<13 sessions>`, `data/backtests/cs2_maker/LIVE/seed0/round_preds.parquet`, and the gate scripts and baselines under `tasks/cs2-live/orchestrate/work/`. No VPS fetch and no `sync_trader.py`.

## 6. Edge cases, risks, hand-offs

- **No real price on the tape.** Map 3 has no market in `target.json`, and `books.jsonl` is raw websocket text. Every real-tape call is `nan_m0` with zero outputs. The tape proves the facts, timing and features; the model on real facts is shown only under a labeled synthetic m0 (§4.1 #6). PLAN e2e "pistols ±1e-4" in the CLI output cannot hold; the report states this.
- **The compatible book path has no real input.** Only the synthetic §4.2 case runs it. The step text requires it. If a reviewer calls it dead code, the answer is the step's change list.
- **The replay ignores socket drops.** Live, the 21:39:54 abnormal close gives `FEED_GAP` and no calls for R13–R21; the 21:16:46 idle close ends the feed. The ledger prints both disconnects. The fix is STEP-012's (idle close) and a later gap-ban upgrade.
- **Clock mix.** `due_ns` is GRID `occurredAt`; the call time compares it with local receipt, as live compares it with `time.time_ns()`. A recorder clock skew moves the call by the skew; the 16.6 s minimum margin absorbs any skew under that.
- **Model time not simulated.** About 143 ms per call; no tape status changes inside a 16.6 s margin.
- **Model load notify.** `load_cs2_round_models` calls `report_startup_block` → `notify_in_background` on a load failure. A CLI run with missing models can send that alert if the notify env is set.
- **Map finish wording.** Tests and report use the frames' `occurredAt`; receipt is reported beside it.
- **R6 planted false** stays the STEP-007 rule; the test pins it.

## 7. Out of scope

- A raw-websocket book adapter, a GRID reconnect or gap-ban change, and simulated model latency.
- Dashboard CS2 labels, slate and `summarize.py --game cs2` (STEP-011).
- VPS rollout, live mode, idle-close fix, real-title checks (STEP-012).
- No change to `../poly-maker`, the backtest, model training, or Dota/LoL constants.
