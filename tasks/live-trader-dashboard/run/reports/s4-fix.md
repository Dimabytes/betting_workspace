esports-trader 4044b4bb
Status: FINAL

# STEP-004 review fixes

## s4-review.md findings

1. `home.py` 1416 lines, blocker — **fixed.** Split per builder: `home.py` keeps
   shared formatters, `map_title`, the two URL helpers, `berlin_day`, `find_match`
   (74 lines); `home_strip.py` holds `FreshMetric`, `HealthLine`, `StripView`,
   `build_strip` (215); `home_lists.py` holds the row DTOs, `ListIndexes`,
   `build_lists` (700); `home_diag.py` holds `Diagnostic`, `DiagnosticsView`,
   `HealthAssessment`, `SellWatcher`, `build_diagnostics` (450). `HomeView`
   deleted (never constructed). YES/NO token-to-team pairing deduped into
   `_token_sides` shared by `_position_lines`/`_residual_legs`. Indexes built once
   in `_list_indexes` (books, positions, sessions, token cids, open buys,
   unsettled, terminal views, api rows, reserve maps, known cids) and passed into
   the row builders; `used_api` keyed by `(condition_id, asset)` instead of `id()`.

2. Container health three drifted ladders, major — **fixed.** `assess_health` in
   `home_diag.py` is the single reducer: returns `HealthAssessment` with strip
   state/text/details, an optional `Diagnostic`, and the `observed` phrase. The
   inconsistent-read and missing-container cases now produce identical wording on
   the strip, in findings, and in `observed`; a failed read is `unknown` state
   with a `warn` diagnostic in both places. `build_diagnostics` consumes
   `assessment.diagnostic`/`assessment.observed`; `build_strip` renders
   `HealthLine` from the same result. `watcher` is now a required argument
   (test call sites pass `SellWatcher()`).

3. `params_by_cid` float map fed order size into the delta threshold, major —
   **fixed.** The float map is gone from the map path: `_map_findings` reads
   `view.entry.params.min_abs_delta` the same way `_row_reason` does;
   `_exit_findings`/`_session_findings` take `min_order_by_cid` named for the
   field it carries (`SessionParams.min_order_size`). New test
   `test_min_delta_finding_uses_delta_threshold` asserts the diagnostic for the
   fixture params (min_abs_delta 0.05, min_order_size 5.0, raw_delta 0.02)
   contains `порога входа 0.05`; `test_active_row_marks_and_reason` now also
   asserts `0.05` in `row.reason`.

4. Narration docstrings/comments, minor — **fixed.** Module docstrings deleted in
   `app.py`, `home_view.py`, `fresh_view.py`, `test_dashboard_app.py`,
   `dashboard_fixture.py`; the `home_view.py` dataframe-overloads comment deleted
   (the `cast(Any, st).dataframe` itself kept); dead `reportPrivateUsage`
   directives removed from `test_dashboard_home.py`, `test_dashboard_app.py`,
   `dashboard_fixture.py`.

## s4-comments.md findings

1-6. Docstrings (`app.py:1`, `fresh_view.py:1`, `home_view.py:1`,
   `test_dashboard_app.py:2`, `dashboard_fixture.py` module + `FixtureState`) —
   **fixed**, all deleted.
7-8. Dead `reportPrivateUsage=false` directives in `dashboard_fixture.py:1` and
   `test_dashboard_home.py:1` — **fixed**, deleted; basedpyright stays clean.
9. `test_dashboard_app.py:1` directive + private fixture API — **fixed.** Renamed
   `dashboard_fixture._write_match`/`_write_journal` to `write_match`/`write_journal`
   (the two cross-module entry points), updated callers in `dashboard_fixture.py`
   and `test_dashboard_app.py`, dropped the directive. `_write_trace`/`_write_sidecar`
   stay private — same-module only.
- Keep-clause disagreement resolved: `home_view.py:26` overloads comment deleted
  per s4-review #4 despite s4-comments keep-verdict — the cast is self-evident
  and the strict no-new-comments rule governs. The `cast(Any, st)` workaround
  itself is unchanged and required.

## Verification

- `PYTHONPATH=src uv run pytest tests/test_dashboard_home.py tests/test_dashboard_app.py tests/test_dashboard_live_hub.py tests/test_dashboard.py -q` — 103 passed (was 102; +1 min-delta regression test).
- `uv run ruff check src tests` — all checks passed.
- `uv run ruff format --check` — clean.
- `uv run basedpyright` — 0 errors, 0 warnings, 0 notes.
- Pre-commit hooks on `4044b4bb` — all passed.
