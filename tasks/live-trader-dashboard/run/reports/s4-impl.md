esports-trader 68ca7867, betting_workspace 6f1b6a5
Status: FINAL

# STEP-004 — Главный экран (main/home screen)

Read-only Streamlit dashboard over the process-wide LiveHub. Runs as a separate
process (`make dashboard`, 127.0.0.1:8501), never mutates trader state, reads
immutable hub snapshots.

## Changed files (esports-trader 68ca7867)

- `pyproject.toml`, `uv.lock` — `dashboard` dep group (streamlit>=1.63, plotly).
- `Makefile` — `dashboard` target: `PYTHONPATH=src nice -n 10 uv run --group dashboard streamlit run src/dashboard/app.py`.
- `.streamlit/config.toml` — headless 127.0.0.1:8501, dark theme, Barlow font.
- `src/dashboard/app.py` — entrypoint: cached `_hub()`/`_sell_watcher()` via
  st.cache_resource, `_fast()` fragment (1s: metric strip + diagnostics),
  `_lists()` fragment (60s: map lists), `?match=` route scaffold, unknown-match
  warning, home link.
- `src/dashboard/home.py` — pure projections from HubSnapshot: build_strip
  (collateral/cash/day-PnL/rebate with fresh/stale/no-data verdicts),
  build_diagnostics (health, sell watcher, halt, evidence), build_lists
  (active/idle/closed-today/residual rows), find_match for the route,
  formatters (fmt_money, fmt_age, verdicts). Europe/Berlin day semantics; saved
  prior-day fold never shown as today's PnL; wallet uncertainty explicit;
  book_stale marks distinct from book.
- `src/dashboard/home_view.py` — render-only: metric strip, diagnostics
  caption, four sections in spec order (Торгуем сейчас / Скоро-не торгуем /
  Закрытые сегодня / Остатки завершённых карт), expanders with position legs +
  evidence popovers, dataframes, Polymarket links, match-route links.
- `src/dashboard/fresh_view.py` — CCv2 `trader_fresh` component:
  role="status", aria-live="polite", states fresh/stale/updating/no_data,
  persistent dot node, pulse suppressed under prefers-reduced-motion, `—`
  for missing values.
- `src/dashboard/health.py` — container/docker health facts for diagnostics.
- `src/dashboard/catalog.py` — DecisionFacts, SessionParams, map labels,
  closed_observed_at extraction for closed rows.
- `src/dashboard/hub_types.py` — WalletFacts (sells/flags/bindings/unsettled/
  token_cids), reserve detail token/created_at, NontradingFacts, day inflight,
  merge helpers.
- `src/dashboard/live_hub.py` — catalog_s in HubTiming (injectable lane cadence),
  nontrading/health lanes wired into snapshots.
- `src/dashboard/logs.py` — `log_ts` made public.
- `tests/dashboard_fixture.py` — offline fixture: fake books driver, balance,
  logs, fetch/subprocess callbacks, match/journal writers, HubTiming override.
- `tests/test_dashboard_home.py` — pure-projection coverage: strip verdicts,
  diagnostics, active/idle/closed/residual classification, wallet-unknown,
  reserve/redeem, unsettled lines, Berlin-day PnL, stale marks.
- `tests/test_dashboard_app.py` — AppTest: all sections, match route, unknown
  route, snapshot refresh. `st.cache_resource.clear()` per run (resource cache
  is process-global across AppTests).
- `tests/test_dashboard_live_hub.py` — source-lane contract additions.

## Changed files (betting_workspace 6f1b6a5)

- `.shared-skills/vps-trader/SKILL.md` — one line: `make dashboard`, then
  `ssh -L 8501:localhost:8501 sun`, open localhost:8501. Only this file staged;
  nothing under tasks/ committed.

## Verification

- `PYTHONPATH=src uv run pytest tests/test_dashboard_home.py tests/test_dashboard_app.py tests/test_dashboard_live_hub.py tests/test_dashboard.py -q` — 102 passed.
- `uv run ruff check` — all checks passed.
- `uv run ruff format --check` — 21 files already formatted.
- `uv run basedpyright` — 0 errors, 0 warnings, 0 notes.
- `uv lock --check` — resolved, lock consistent.
- Pre-commit hooks on the product commit: all passed.
- Browser acceptance (fixture app + Playwright/agent-browser, harness at
  `run/work/s4-impl/browser_acceptance.py`): 31/31 — four sections in order,
  diagnostics, active/idle/residual rows, closed grid, 4 status components with
  aria-live=polite, fresh/stale/updating/no_data transitions, balance fail keeps
  last value then recovers, book disconnect → book_stale mark → reconnect,
  updating pulse + reduced-motion suppression, XSS escaped, second tab shares
  hub, 1366px and 1440px layouts, match route + unknown-route warning,
  Polymarket hrefs. Screenshots in `run/work/s4-impl/`.

## Notes / gotchas

- st.cache_resource is process-global: the last AppTest would read the first
  test's hub without an explicit clear — fixed via `st.cache_resource.clear()`
  in the test patch helper (was a deterministic 3-in-a-row failure).
- fmt_age takes a duration; ClosedRow carries closed_age_s computed in the pure
  layer — raw timestamps were rendered as ~497568h before the fix.
- `_lists` runs every 60s; browser checks open <details> and poll up to 80s.
- `.fv` component nodes live inside page.frames, not body innerText.
- Fixture launcher (`run/work/s4-impl/fixture_app.py`) is scratch, not
  committed; it caches the fixture world once per process and mutates state via
  a control file (balance fail/recover, book disconnect/reconnect, fetch sleep,
  xss map).
