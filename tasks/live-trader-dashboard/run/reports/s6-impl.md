777bad68
Status: FINAL

## Scope

STEP-006 only — the match detail page behind `?match=<id>` in `src/dashboard/app.py`. STEP-005 game-summary and all home behavior preserved. Dashboard stays read-only: no trading logic, no model evaluation, no order commands, no own GRID/Oddin connections. `feature.json` untouched (`passes: false`), `progress.txt` appended but not committed.

## Changed files (esports-trader, commit 777bad68, not pushed)

Added:
- `src/dashboard/match_page.py` — route orchestration; fragments at 1s (header/summary/ages), 0.5s (now/position/orders/books), 15s (lazy chart, lazy event feed); handles missing/ambiguous/loading catalog states.
- `src/dashboard/match_state.py` — pure projection (no IO): side mapping and YES/NO orientation via archive token binding + `GAME_PROFILES` labels (Radiant/Dire, Blue/Red), current position text, SELL assessment, BUY-block explanation, data-age lines, position legs/marks, book panels with own-order overlays, live vs transitional (pending/canceling/unknown) order split, reserve details; `MatchObserver` tracks per-order first-seen, sell-wedge timing, status transitions.
- `src/dashboard/match_view.py` — Streamlit rendering for all blocks; escaped-HTML tables; details expander.
- `src/dashboard/match_history.py` — bounded cached reads: chart tapes + horn time, session journal tail/full, event projection (signal/fill/quote records, dedup, initial-state labeling, 40-row cap over ≤5000 source records).
- `src/dashboard/match_trace.py` — advisory core-trace tail reader: header identity validation (match/session/token), BudgetUpdate/PermissionsUpdate folding, malformed-truncated-tail handling, revert records, invalidation when tail can't prove correctness. Never money truth.
- `tests/test_dashboard_match.py` — new test file.

Modified:
- `src/dashboard/app.py` — `?match=<id>` routes to `render_match_page`; placeholder removed; home fragments unchanged.
- `src/dashboard/hub_types.py` — `OrderFacts`, `SessionFacts.orders`, `SessionFacts.held_cost` (read-only wallet projection).
- `src/dashboard/catalog.py` — `SessionParams.level_usdc` (policy per-level cost for the limit line).
- `src/dashboard/home_diag.py` — extracted `_map_finding`, added `build_match_diagnostics` (selected-map health/session/map findings).
- `tests/test_dashboard_app.py`, `tests/test_dashboard_home.py` — route assertion and constructor updates.

## Behavior notes

- Confirmed `live` checkpoint orders overlay the YES/NO ladder at #E3A93B; `pending`/`canceling`/`unknown` render separately with own state; remaining size = max(0, submitted − filled). Persisted `gone` orders surface as `unknown`+`unsettled` (checkpoint format from `core_persistence.py`).
- YES/NO orientation comes from archive/session token binding, not side order — verified reversed (YES=Dire) on a stale map.
- Stale/unreadable `live.db` → position/SELL explicitly unknown, orders labeled possibly stale. No fabricated zero state.
- Chart and event feed do no IO until their expanders open; reads are cached and bounded; chart fragment cadence 15s.

## Verification

- `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/ -x -q` → **164 passed, 80 warnings** (full suite; re-ran dashboard trio post-format: 44 passed).
- `uv run ruff check src/dashboard/ tests/test_dashboard_match.py` → **All checks passed**.
- `uv run basedpyright src/dashboard/` → **0 errors, 0 warnings, 0 notes**.
- `uv lock --check` → passed.
- Browser (`agent-browser --session s6-impl`, Streamlit on 127.0.0.1:8871): home renders all sections; match page at **1366x768 and 1440x900** shows title, home/Polymarket links, `YES = Nigma Galaxy (Radiant) · NO = Team Spirit (Dire) · статус final`, warnings, diagnostics, t= clock, game summary, `не покупаем` block with reason/threshold/window/limit, age lines with sources, books, `сейчас` line, position; stale map shows correct flipped YES=Dire orientation and stale labels; unknown id shows `карта не найдена в каталоге`; chart expander renders plot_match; event expander renders rows + `полная история` toggle; no browser console errors, no stException. Screenshots: `run/work/s6-impl/match-1366.png`, `match-1366-full.png`, `match-1440.png`.

## Known limits

- Screenshot capture occasionally stalls on the continuously-rerunning page; DOM/text extraction and the saved screenshots above are the evidence.
- `level_usdc` shows only when the session params blob carries `policy.level_usdc`; otherwise the limit line reports unknown.
