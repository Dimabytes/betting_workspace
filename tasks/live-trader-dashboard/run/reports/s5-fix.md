7e69b435
Status: FINAL

# STEP-005 review fixes

## s5-review.md findings

1. `game_state.py:447` blocker (1040 lines, second observation model) — **fixed.**
   New `src/dashboard/game_types.py` (195 lines) holds the summary DTOs
   plus `compact`/`side_slice` (the helpers had to live next to the DTOs
   so both adapters could import them — putting them in the types module
   was the only way to honor "one types module"; they were not
   duplicated into the adapters). `GridPlayerObs`/`GridBoardObs`/
   `GridTableObs`/`OddinPlayerObs`/`OddinSliceObs` deleted; each fold
   stores `board: BoardSlice`/`table: TableSlice` plus its private
   scoreboard, board signature, table snapshot, and reducer.
   `_grid_player`..`_oddin_table`, `_Slices`, and `_project_slices` are
   gone. `GameStateReader._fold_slices` only stamps `archive_path` and
   `_acceptance_notes(continuity)` onto the fold's slices via
   `dataclasses.replace` (reader-owned fields the fold cannot know).
   `build_game_summary` takes `control`/`rejected`/`terminal`, not the
   fold. `game_state.py` is now 717 lines; no barrel.

2. `tails.py:171` major (`read`/`read_records` duplicated shell) — **fixed.**
   `read` now calls `read_records` (the primitive) and returns the
   cached `TailView` for the returned `RecordTail`, falling back to
   `_build_tail(raw)` when the view is missing. Race rule preserved:
   unverified reads keep and return the previous tail.

3. `game_state.py:502` major (`SideSlice.gold` silently = player sum) — **fixed.**
   `side_slice` assigns `gold` verbatim from the feed argument; GRID
   passes `None`, so `side.gold is None` and `players_gold` carries the
   sum. Fixture `netWorthNullable` is now `sum + ODDIN_TEAM_GOLD_PAD`
   (97), so `gold` (feed) and `players_gold` (sum) are provably
   distinct; Oddin test asserts both; GRID test asserts `gold is None`
   + `players_gold`.

4. `tests/test_dashboard_game_state.py:1` minor (module docstrings) — **fixed.**
   Both docstrings deleted (test file and `dashboard_game_fixtures.py`).

## s5-comments.md findings

1. `src/dashboard/game_state.py:1` docstring — **skipped (mistaken).**
   Commit `879efa1d` has `import json` at line 1; the quoted text is the
   *test file's* docstring, which is now deleted under review-finding 4.

2. `tests/dashboard_game_fixtures.py:1` docstring — **fixed.** Deleted.

3. `test_dashboard_game_state.py:891` private-usage suppression — **fixed.**
   `getattr` was ruff-blocked (B009 const-getattr); used
   `vars(TailCache)["_read_resolved"]` — reads the unbound function with
   no suppression, same effect.

4. `test_dashboard_game_state.py:1046` `type: ignore[misc]` — **fixed.**
   `setattr` was ruff-blocked (B010 const-setattr) and `del` trips
   pyright's frozen-dataclass modeling; `delattr(summary.decision,
   "second")` raises the same `FrozenInstanceError` through a dynamic
   builtin, no suppression.

5. `test_dashboard_game_state.py:1051` `type: ignore[misc]` — **fixed.**
   Same reshape: `delattr(player, "net_worth")`.

## Verification

- `PYTHONPATH=src uv run python -m pytest tests/test_dashboard_game_state.py
  tests/test_dashboard.py tests/test_dashboard_home.py
  tests/test_dashboard_live_hub.py tests/test_dashboard_app.py
  tests/test_grid_feed.py tests/test_lol_grid_feed.py
  tests/test_oddin_feed.py tests/test_oddin_reducer.py
  tests/test_archive_index.py -x -q` -> 253 passed
- `uv run ruff check` -> all checks passed
- `uv run ruff format --check` -> clean
- `uv run python -m basedpyright` -> 0 errors, 0 warnings, 0 notes
- Pre-commit hooks all passed on commit `7e69b435`.

## Notes

- `feature.json` untouched (`passes` not set). Progress appended to
  `tasks/live-trader-dashboard/progress.txt` (uncommitted). Not pushed.
