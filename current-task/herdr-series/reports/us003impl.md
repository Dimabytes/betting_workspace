# US-003: LoL series backtest + post-map SELL-only tail
Status: FINAL

## Commit

- `4dab09f43e910f38692fb75ef3385de029587f0e` on `main` (parent `185fdf2a`, tree was clean at handoff)
- 13 files, +512/-70; only `esports-trader` touched. `feature.json` untouched, no `passes` set.

## What landed

### Shared post-map tail (`sell_after_game_end`)

- `src/backtest/context.py`: `SERIES_POST_END = timedelta(minutes=15)`.
- `src/strategy/policy.py`: `Follow300Policy.sell_after_game_end: bool` (required). `follow300_policy`/`extraction_policy` set `False`; only series runs flip it.
- `src/trader/core_trace_codec.py`: key added to `_POLICY_KEYS`, `decode_policy` defaults missing historical traces to `False`.
- `src/strategy/quoting.py`: `requote` on `clock.game_ended` now branches — `False` keeps the old `_blocked("game_end")` (cancel everything); `True` runs `_post_end_sell`: core BUYs marked `game_end`, idle episodes end, stale/one-sided books refuse (`stale_book`), `decide_sell(..., fair=None)` joins the best ask via `_join_ask_sell`, one SELL target reconciled, flat inventory -> `empty_plan("game_end")`.
- `src/backtest/strategy.py`:
  - `_executes_plan(event, policy=...)` executes the game-ended `ClockUpdate` plan when the flag is on (the load-bearing fix — otherwise the boundary BUY cancels and first tail SELL are dropped).
  - `_evaluate` and `_arm_wake` no longer stop at `game_end_ns` when the flag is on.
  - `_on_game_end` still always drives the boundary `ClockUpdate` and records uptime; with the flag it cancels only live BUYs (`_cancel_live_buys("game_end")`), leaving SELLs resting.
  - `on_order_accepted` at/after `game_end_ns` still cancels BUYs but preserves accepted SELLs under the flag.
  - Dust/cancel-release paths unchanged.

### LoL series cohort + extended windows

- `src/backtest/series_inputs.py`:
  - Dota `resolve_series_market`: availability start kept (spawn-lead), end extended to `ended_at + SERIES_POST_END` for the Telonex day-file gate.
  - `resolve_lol_series_market`: window is `replay_start_ts -> game_ended_at_ts + SERIES_POST_END`; `replay_end_ts` no longer used for the series endpoint.
  - `LolSeriesIndexes` + `index_lol_series_inputs(...)` extracted; `select_lol_series_matches` reuses it.
- `src/backtest/series_run.py`:
  - `LolSeriesInputs` + `load_lol_series_inputs()` (`LOL_UNIVERSE_PATH`, `LOL_LINKS_PATH`, `index_gamma_markets(LOL_RAW_GAMMA_DIR / "events")`).
  - `resolve_lol_series_cohort(...)`: validation mode defers to `select_lol_series_matches` (full-cohort coverage); per-id mode resolves audit rows via the shared indexes, records `SeriesGate` drops, raises on `required_ids` drops or ids missing from the audit.
  - `build_series_context`: `replay_end`/`clock_end` = `game_ended_at + SERIES_POST_END` (never `market_closed_at`); terminal marks therefore read the best bid at the tail end.
  - `load_series_books`: leg `end_us` = `game_ended_at + SERIES_POST_END`.
  - `build_decision_seconds(..., game=...)`: LoL grid-v1 keys shift `state_ts_us` by `LOL_SOURCE_LAG_SECONDS`, matching the lagged `MatchSignals.timestamps_ns`.
  - `assemble_series_replay(..., game)` forwarded from `run.py`.
- `src/backtest/run.py`: parser no longer rejects `--market series --game lol`; `resolve_run_policies(..., sell_after_game_end=args.market == "series")`; `_resolve_series_cohort` dispatches LoL through `LolArchiveJoin.audit` + `selection.capture_root`; manifest records `sell_after_game_end`; `game` passed to `assemble_series_replay`.
- `src/backtest/seed0_replay.py`: `resolve_run_policies(..., sell_after_game_end=False)` — identity replay stays on the map policy.

## Verification (all run in `esports-trader`)

- `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_series_run.py tests/test_series_inputs.py tests/test_backtest_maker.py tests/test_lol_backtest.py tests/test_backtest.py -q` -> **234 passed, 0 failed** (verified; the documented `test_since_match`/`ok_quote_fraction` baseline failures did not appear in this file set).
- `uv run python -m basedpyright` -> **0 errors, 0 warnings**.
- `uv run ruff check . && uv run ruff format --check .` -> **clean** (format applied to 4 touched files, re-verified).

### New/changed tests

- `tests/test_backtest_maker.py`: `sell_after_game_end` knob on `build_maker_config`; `_enter_tail` helper; boundary update cancels BUYs and posts the join-ask SELL; post-end accept keeps SELL / cancels late BUY; SELL follows the ask (replacement lands on the cancel-ack visit); flat inventory stops quoting; wakes arm past `game_end_ns`.
- `tests/test_series_inputs.py`: `_lol_audit_row` gains `game_ended_at_ts`; Dota book-gate window ends `ENDED_AT + SERIES_POST_END`; LoL window `replay_start_ts -> game_ended_at_ts + SERIES_POST_END`; `resolve_lol_series_cohort` per-id/validation/required-drop coverage.
- `tests/test_series_run.py`: series context `replay_end`/`clock_end` = `GAME_END + SERIES_POST_END`; `--game lol --market series --series-branch line` parses; `build_decision_seconds` LoL lag shift.
- `tests/test_backtest.py`: `resolve_run_policies` flag coverage (off by default, on for series).

## Skipped / not run

- No seed backtests, no `run_seeds.sh`, no `compare_backtests.py`, no `make test`, no full suite — per brief.
- No series-outcome settlement closure (explicitly out of scope for US-003).

## Notes for the next step

- The tail needs fresh books to sell: the boundary `ClockUpdate` sells only if the last synced book is within `book_stale_s` of game end; otherwise the next armed wake/book update places the SELL (same path, `stale_book` -> retry on next evaluate).
- A resting tail SELL does not reprice while the venue still owes cancel acks on the killed BUYs (`unconfirmed_hold`) — same discipline as pre-end quoting.
- LoL validation mode needs the league-whitelisted audit; `run.py` passes `LolArchiveJoin.audit` which is already whitelisted upstream.
