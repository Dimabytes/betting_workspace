STEP-005 review
Status: FINAL

Scope: `git diff 4044b4bb7073d544aa15dfb8f7377e786c71eec8..879efa1d` in esports-trader. The frozen summary, the GRID/Oddin folds, and the shared `RecordTail` read are the right split. `game_state.py` then rebuilds that summary from a second observation model, and `TailCache.read` repeats `read_records`. `tests/test_dashboard_game_state.py` is 1051 lines. It stays one module: the body is the acceptance cases, and splitting the cases would only move them.

## Findings

1. `src/dashboard/game_state.py:447` — blocker

   `game_state.py` is 1040 lines. This pushes a new file past 1000 lines. Decompose it first.

   The adapters already reduce a tail. This file then keeps a second tree — `GridPlayerObs`, `GridBoardObs`, `GridTableObs`, `OddinSliceObs` — and unpacks it again in `_grid_player` through `_oddin_table` (`game_state.py:447-621`). `_project_slices` (`game_state.py:716`) is the type switch that exists only because of that tree. `build_game_summary` (`game_state.py:774`) takes a live `GridFold | OddinFold` to copy `control`, `rejected`, and `terminal`. Nothing but the reader calls it. Moving those functions into the adapter files would relocate the same model.

   Delete the observation dataclasses. Each fold should store `BoardSlice` and `TableSlice` (plus the private scoreboard, signature, and reducer it needs for duplicate checks). `_compact`, `_side_slice`, and the continuity note stay in `game_state.py`; the fold returns players, source gold, and the board fields. `_project_slices` goes away. `build_game_summary` takes the slices and the three source counters, not the fold.

   That cuts this file under 1000 and removes the import cycle that pinned the mapping here. Do not add a barrel. If the dataclasses have to move so the adapters can import them, one types module is the whole extra file.

2. `src/dashboard/tails.py:171` — major

   `read` and `read_records` are the same stamp check, the same three-cache drop, and the same race return. `_read_tail` already does the one stat/read/verify. The shell around it was copied (`tails.py:171-215`).

   `read_records` is the primitive. `read` should call it and return the cached `TailView` for that `RecordTail`, or `_build_tail` when the view is missing. Keep the current race rule: a second conflicting stat does not replace the cached tail, and the caller returns the previous one.

3. `src/dashboard/game_state.py:502` — major

   `SideSlice.gold` is source team gold, then silently the sum of player net worth when that argument is None. GRID always passes None, so `gold` and `players_gold` are the same number. `_compare` (`game_state.py:633`) already ignores `gold` and reads `players_gold`, which is the tell that `gold` is not one invariant.

   `oddin_side` (`tests/dashboard_game_fixtures.py:173`) sets `netWorthNullable` to that same sum. `test_oddin_home_dire_factions_and_no_xp` asserts `side_0.gold == ODDIN_RADIANT_NW`. A projection that dropped team gold and always stored the sum would still pass.

   Assign `gold` only from the feed. GRID sides stay `None`. Set the Oddin team gold to a value that is not the player sum, assert `gold` is that value, and assert `players_gold` is the sum. Point the GRID test at `players_gold` for the sum.

4. `tests/test_dashboard_game_state.py:1` — minor

   New modules are narrating themselves. Delete the module docstrings at `tests/test_dashboard_game_state.py:1` and `tests/dashboard_game_fixtures.py:1`.
