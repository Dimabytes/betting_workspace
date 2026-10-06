STEP-004 review
Status: FINAL

Scope: `git diff 74ed1ec92898e4025a3443f10338b960d8b23a02..68ca7867` in esports-trader, plus `6f1b6a5` in betting_workspace. Nontrading and health land as frozen slices on the existing catalog and logs lanes, the catalog still reduces each journal once, and the skill file adds the one launch line. The home projection is the problem: one new 1416-line module, a health ladder copied three times, and a float map that feeds order size into the delta threshold.

## Findings

1. `src/dashboard/home.py:193` — blocker

   `home.py` is 1416 lines. This pushes a new file past 1000 lines. Decompose it first.

   `HomeView` is never constructed. `app.py` already calls `build_strip`, `build_diagnostics`, and `build_lists` on two cadences. Delete `HomeView`. Put each builder in its own module, with the DTOs that builder returns:

   - strip: `FreshMetric`, `StripView`, `_collateral_metric` through `build_strip`
   - lists: the row types and `_active_row` through `build_lists`
   - diagnostics: `Diagnostic`, `DiagnosticsView`, `SellWatcher`, `build_diagnostics`

   Shared formatters (`fmt_*`, `map_title`, the two URL helpers) stay in one small module. `app.py` and `home_view.py` import the builders directly. `SellWatcher` is mutable continuity state; it lives with the diagnostic reducer.

   Two copies come out of the list module. `_position_lines` (`home.py:544`) and `_residual_legs` (`home.py:895`) repeat the YES/NO token-to-team pairing. One function should return those sides. `build_lists` (`home.py:1029`) indexes books, positions, sessions, and unsettled buys, then `_residual_rows` (`home.py:986`) and `_unsettled_lines` (`home.py:827`) build those maps again. Index once and pass the indexes in. `used_api` (`home.py:996`) tracks consumed API rows with `id()`. Key them by `(condition_id, asset)`, the join this function already uses.

2. `src/dashboard/home.py:427` — major

   Container health is three ladders, and they have already drifted. `_health_line` reports an inconsistent read as "контейнер сменился во время чтения". `_health_findings` (`home.py:1151`) emits nothing for that case. `build_diagnostics` (`home.py:1389`) fills `observed` only when the read is `ok` and `consistent`, and a missing container is "контейнер не запущен" there and "контейнер трейдера не найден" in the other two. A failed read is `unknown` on the strip and `warn` in the findings.

   One function should return the strip text, the diagnostic or none, and the observed phrase. The strip and `build_diagnostics` both render that result.

   `build_diagnostics` takes `SellWatcher | None` (`home.py:1361`). The app and the tests always pass a watcher. Make `watcher` required.

3. `src/dashboard/home.py:1373` — major

   `params_by_cid` stores `SessionParams.min_order_size` as `Mapping[str, float | None]`. `_exit_findings` (`home.py:1118`) passes that float as `min_order_size`. `_map_findings` (`home.py:1350`) passes the same float as `min_abs_delta`. For a `min_delta` block, `entry_block_label` then compares `|raw_delta|` to the order size. The active-row path already does this correctly: `_row_reason` (`home.py:579`) reads `entry.params.min_abs_delta`. `test_active_row_marks_and_reason` only checks that `row.reason` contains "порога", so the diagnostic line can show order size `5` and still pass.

   Delete the float map. `_map_findings` should read `view.entry.params.min_abs_delta`, the same way `_row_reason` does. `_exit_findings` should take `min_order_size` from that same `SessionParams`, in a map named for that field. Assert the diagnostic text for the fixture params (`min_abs_delta` 0.05, `min_order_size` 5.0, `raw_delta` 0.02) contains the 0.05 threshold.

4. `src/dashboard/app.py:1` — minor

   New code is narrating itself. Delete the module docstrings in `app.py:1`, `home_view.py:1`, `fresh_view.py:1`, `tests/test_dashboard_app.py:2`, and `tests/dashboard_fixture.py:2`, and the comment at `home_view.py:26`. The dataframe cast can stay. `tests/test_dashboard_home.py:1` sets `reportPrivateUsage=false` and never touches a private attribute. Remove that suppression.
