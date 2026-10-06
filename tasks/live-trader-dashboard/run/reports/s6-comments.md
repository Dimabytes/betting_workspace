STEP-006 review
Status: FINAL

Scope: `git diff 7e69b435dec45d289df805c43310d9a5cf8f34d7..777bad68` in esports-trader — 12 files, +2949/−69. The diff adds zero `#` comments outside the suppression listed below, no docstrings (module or function), no commented-out code, no TODO/FIXME markers, no other suppressions. Refactored bodies in home_diag.py (`_map_findings` → `_map_finding`/`_diagnostics_view`) and hub_types.py (`_sell_facts`, `_session_facts`, `_order_facts`) contain no comments before or after; none were deleted. One file-level suppression is the entire inventory.

## Findings

1. `src/dashboard/match_page.py:1` — `# pyright: reportUnknownMemberType=false`, file-level, over a 269-line module. Verified by running basedpyright on the file with the header stripped: exactly one diagnostic fires, `fig.update_layout(height=CHART_HEIGHT, uirevision=...)` at line 207, because plotly stubs type `update_layout` as `(dict1: Unknown | None = None, overwrite: bool = False, **kwargs: Unknown) -> Figure`. The suppression need is foreign-dep-forced (plotly's generated stubs; we cannot reshape them), but the file-level scope is not: it silences a correctness rule on every present and future line of the module to cover one call. The repo's own convention confirms this — single-site suppressions go inline (`src/shared/utils/gbm.py:86`, `src/shared/utils/parquet_io.py:18`), while file-level `reportUnknownMemberType=false` headers sit only on files saturated with untyped-dep calls (`viewer/plot.py`, `viewer/live_app.py`, `trader/wallet_store.py`). match_page.py is not saturated. DELETE. MUST KILL the file-level directive — reshape: drop line 1, put `# pyright: ignore[reportUnknownMemberType]` on `fig.update_layout(...)` at line 207.

## Skips (examined, keep clause applies or out of skill scope)

- `_CSS = """..."""` at `src/dashboard/match_view.py:32` — a CSS string constant, not a docstring or comment; not a finding.
- `cast(dict[str, ChartFacts] | None, st.session_state.get(_CHART_MEMO_KEY))` at `src/dashboard/match_page.py:65` — boundary narrowing over untyped session state; sanctioned ingestion-boundary pattern, not a suppression comment.
- Old comments in diff-touched existing files — app.py, catalog.py, home_diag.py, hub_types.py, test_dashboard_app.py, test_dashboard_home.py: the hunks touch no comments; nothing added, nothing removed.

## Notes

- basedpyright runs in strict mode repo-wide (`pyrightconfig.json`, `typeCheckingMode: strict`), so the suppression is live, not decorative.
- Verification method: copied `match_page.py` minus line 1 to a scratch dir, ran `basedpyright` with a scratch pyrightconfig (strict, repo `.venv`, repo `src` on extraPaths) — output: 1 error, line 206/207 `reportUnknownMemberType` on `fig.update_layout`. Scratch copies under `run/work/s6-comments/`.
- No other MUST KILL flags; production code carries no comments at all.
