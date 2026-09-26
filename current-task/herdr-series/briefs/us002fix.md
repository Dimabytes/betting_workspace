Review of US-002 found two blockers. You are the same implementer. Fix what you agree is real. The reviewer can be wrong; if you skip one, say why in the report. Do not start US-003. Do not set `passes`. Do not push.

Read:
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/reports/us002review.md`

1. blocker, `src/backtest/run.py:1828`: the series pipeline is a second type hierarchy inside `run.py` (`_SeriesSelection`, `_SeriesFit`, `_SeriesReplaySignals`, `_SeriesRunFlags`, `_SeriesPostprocess`, through `_series_postprocess` at line 2134), and `main` at line 2285 then overwrites `context_by_match`, `replay_mids`, `terminal_marks`, `replay_match_ids`, and `book_schedule_archives`. `series_run.py` already has `SeriesCohort`, `SeriesLookups`, `SeriesSignalsOut`, and `SeriesReport`, and its module docstring says it owns this so `run.py` only parses flags and calls it. `run.py` goes from 1952 lines to 2374. Delete the five private dataclasses and the `cast(SeriesBranch, ...)` in `_series_run_flags`. Add one function in `series_run.py` that takes the resolved cohort, map contexts, map signals, plans, branch, and `c`, and returns the existing series types (contexts, replay ids, q0 refusals, transformed signals, mids, marks, `SeriesReport`). `main` calls that once. Keep flag checks, `RunSelection` filtering, and `engine_fault_match_result` rows in `run.py` — those already live next to `RunSelection` / `results.py` and must not import `run.py` from `series_run.py`.

2. blocker, `src/backtest/series_run.py:249`: `load_series_books` raises if either series leg is empty (line 249) or the horn-window map book is empty (line 261). `_fit_series_books` (`src/backtest/run.py:1884`) loads every pending map before the replay loop, so one empty window aborts the shard and never writes the `q0_*` fault row the plan requires. A missing horn sliver is `no_horn_quotes`: that map does not trade, the rest of the shard does. Do the same for an empty series leg (fault that map, do not trade it blind, do not raise). Same shard-kill at `src/backtest/marks.py:95`: `resolve_terminal_bids` raises when a leg has no as-of bid and no last mid, and `build_terminal_marks` runs that for every fitted map in one shot. Skip that map with a fault row; do not fabricate `0` and do not fail the shard.

Commit the fix in `esports-trader` only if tests and typecheck pass. Do not amend `861c142d`. New commit. Message: `fix: US-002 - move series assembly out of run.py`.

Re-run the focused pytest from the US-002 plan plus `uv run python -m basedpyright` and `uv run ruff check` on the files you touch. No `make test`, no seed backtest.

Immediately set line 2 of `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/reports/us002impl.md` to `Status: DRAFT`. When the fix commit is done, set line 2 back to `Status: FINAL` and add the new commit hash plus what you fixed or skipped.

Then reply with only that report path.
