# STEP-004 — Главный экран

## Scope and authority

Implement STEP-004 only. STEP-001/002/003 have `passes: true`; STEP-004 is the highest-priority pending step. Build the read-only Streamlit home screen over the existing process-wide LiveHub, with its dependency group, launch target, theme and one launch-documentation line. The behavior authority is `tasks/live-trader-dashboard/plan.md`, supplemented by feature.json and earlier-step progress notes. `plan.instructions` is empty. There are no Figma links or designReference assets.

This planner inspected the code after STEP-003 review fixes, including live_hub.py, hub_types.py, balance.py, catalog.py, diagnostics.py, logs.py, market_books.py, reserve.py, tails.py, wallet.py, summarize.py, journal/archive/checkpoint contracts, pyproject.toml, Makefile and verification configuration. The earlier `sources.py` was split and removed: use its domain modules directly, without recreating a barrel.

Planning writes this plan and the planner report only. Implementation must not change trader strategy, checkpoints, database schema, accounting or signal serialization; edit poly-maker; touch paper mode; deploy; SSH; restart a daemon; access accounts; or send real orders. Do not implement STEP-005 game/player projections or STEP-006 book ladders, graph and trading detail screen. Do not change feature.json/progress during planning or commit run/ artifacts.

## Existing interfaces and the gaps to close

| Inspected source | What already exists | STEP-004 work |
| --- | --- | --- |
| `live_hub.get_live_hub()` | Zero-argument process registry; start/close/replace lifecycle; five single-worker lanes; snapshot getter | Add a Streamlit resource wrapper, preserve registry ownership and lane scheduling |
| `hub_types.HubSnapshot` | Frozen balance, wallet, reserve, day, map, book, subscription and log slices | Publish the additional small home-screen facts at their source apply sites |
| `WalletFacts` / `SessionFacts` | Wallet positions, session identity/revision/update time/recovery | Project checkpoint SELL/orders, sell_only/recovery_pending, binding and unsettled evidence; raw checkpoints stay private |
| `MapView` / `ArchiveEntry` | Terminal/live/stale/incomplete classification; IDs, tokens, slug, joined time, realized and rebate | Add labels/map number and compact summary fields; expose decision facts from already-read tails |
| `MatchCatalog._facts()` | One cached summarize_session reduction per changed journal on the 60 s catalog lane | Keep scalar imv/net/fill count, dated terminal evidence and latest decision facts from this same reduction; do not summarize twice |
| `_DiskLane` / `_tail_views` | Bounded TailCache reads for nonterminal journals, read-only wallet/outbox snapshots | Convert tail records into typed immutable decision facts; UI must not reach into these private maps |
| `logs.list_nontrading()` | Sidecar scan, active-CID subtraction, skip reason source/time | Wire into the slow lane at 60 s; publish result and source completeness |
| `LogsFacts` | Last successful bounded Docker log tail, error/time | Add independently proven container identity/state/start time and a current-run health reduction |
| `DaySnapshot` | Retained full fold/date/time, positions, payout and accrued rebate | Add request-in-progress and latest positions/accrual attempt metadata needed to distinguish updating from retained/stale data |
| `ReserveReport` | Cash estimate, component totals, IDs and limitations | Reuse unchanged accounting; enrich published unsettled evidence with created_at/token/remaining reserve for residual rows |
| `diagnostics` | Freshness precedence, reason/entry-block labels, exit assessment and SELL observation | Reuse these functions; add a focused home diagnostic reducer and evidence DTO, without a generic rule engine |

Important limits to preserve:

- `HubSnapshot.running` describes the dashboard thread, not the trader container. A successful log read or an old core_sessions row does not prove that the trader is running.
- TailView is frozen but contains mutable dictionaries. Never expose TailView or a SessionSummary directly to multiple UI threads. Project only named immutable fields; any evidence mapping must be copied and made immutable.
- ArchiveEntry currently omits imv, fill_count and closure date. `session_end`, match.json.final and execution_cleanup.json have no wall-clock closure timestamp. `joined_at_utc` is a join date, and file mtime can change after late fills or compression. Neither is an exact closure time.
- The existing reserve is always incomplete because disk order_log cannot prove memory-only orphan coverage. Fresh collateral can coexist with an explicitly incomplete available-cash estimate.
- DaySnapshot.complete describes the retained fold. A subsequent failed/incomplete candidate must still show the latest attempt failure. Retained complete positions similarly need the new attempt's error/completeness, not a falsely renewed as-of time.
- Fresh independent public books do not prove the trader's MDS is healthy. Quiet, connected, initialized books are not stale simply because no levels changed.

## Recommended structure

Use native Streamlit layout/table/link/expander controls. The small freshness indicator needs a persistent DOM dot, aria-live and reduced-motion behavior; use a focused inline CCv2 component for `render_fresh`, with no npm build, React, packaged component scaffold or other frontend dependency. Keep the component's data transformation separate from rendering. Ordinary home rows and evidence use native elements.

This was selected over putting disk reads in each Streamlit session: source work is already owned by LiveHub and must remain shared between tabs. It also avoids a large all-HTML home screen, which would duplicate routing/layout/accessibility behavior. A status component is justified specifically by the animation lifetime: replacing an st.html subtree every second can restart its dot animation.

Proposed file responsibilities:

- `src/dashboard/app.py`: thin entry script, set_page_config, query routing, resource acquisition and fragment invocation.
- `src/dashboard/home.py`: pure typed home rows, token joins, marks/unrealized and presentation verdicts; no Streamlit or I/O.
- `src/dashboard/home_view.py`: top strip, four sections, row links and diagnostic disclosure; consumes home DTOs.
- `src/dashboard/fresh_view.py`: `render_fresh` and its one inline status component; own CSS only, no global Streamlit DOM selectors.
- `src/dashboard/health.py`: bounded read-only container status reader plus current-run log health reduction; runner injected for tests. Keep container subprocess work off the socket loop.
- Targeted edits to catalog.py, hub_types.py, live_hub.py and diagnostics.py for the missing published facts. New home DTOs stay in home.py; source DTOs stay in their domain modules. Do not append all UI business logic to the already 762-line live_hub.py or broadly refactor its scheduler.
- `.streamlit/config.toml`, pyproject.toml, uv.lock and Makefile.
- `tests/test_dashboard_home.py`, `tests/test_dashboard_app.py`, focused additions to existing dashboard tests for source contracts, and an offline fixture/bootstrap module under tests for browser acceptance.
- `betting_workspace/.shared-skills/vps-trader/SKILL.md`: exactly one concise launch/tunnel line.

Respect project style: namespace packages, no new __init__.py, required arguments, action-verb function names, named intermediate values, frozen named dataclasses for internal records, no dict[str, Any], blanket type suppressions, narration comments/docstrings or unnecessary abstractions.

## Implementation sequence

### 1. Dependencies, configuration and launch target

1. Add a separate `dashboard` dependency group containing Streamlit and Plotly. Installed Streamlit is 1.63.0; declare `streamlit>=1.63.0` so the planned resource cleanup and inline component APIs match the inspected version, then lock the actual resolved version. Plotly can retain the existing backtest minimum (`>=5.0.0`). Keep the backtest group available for its existing apps; dashboard must not activate it.
2. Update uv.lock with the project's normal uv workflow. Check the diff for unrelated source/dependency churn. Do not run backtest-install or build Nautilus.
3. Add `.PHONY` and the Makefile target with this recipe and a help description:

   `PYTHONPATH=src nice -n 10 uv run --group dashboard streamlit run src/dashboard/app.py`

4. Configure `[server] address = "127.0.0.1"`, `port = 8501`, `headless = true`; `[browser] gatherUsageStats = false`.
5. Configure the dark theme with background #17212C, secondaryBackgroundColor #1F2B38, textColor #E6EAEE, muted gray #8B98A5, Radiant/Blue #4F9D7E, Dire/Red #C4513F and amber #E3A93B for our emphasis. Use configuration for page colors and font; do not style Streamlit's private DOM. No shadow cards, decorative charts or loading animations beyond the required status dot.
6. Set Barlow Semi Condensed through documented theme font/fontFaces options. There are no existing font assets: use a valid HTTPS font file URL in fontFaces and a sans-serif fallback rather than inventing a local static path. The app remains readable if the browser cannot fetch the font. Set tabular numerals on the owned status text and compact row styling where supported, without altering global framework selectors. Verify actual font configuration with the installed `streamlit config show`.

### 2. Publish the home facts once, on existing source lanes

**Catalog, 60 s.** Extend the existing cached journal reduction with imv/net/fill count, last terminal signal timestamp if present, and a compact last-decision projection. Extend ArchiveEntry with teams/map number/outcome names and typed pinned policy values read from the existing trace header: min_abs_delta, buy_cutoff_second, min_order_size and entry/exit freshness limits where available. Use viewer.archive_read helpers and catalog.read_trace_header/trace_limits. Validate header session/match/token identity before applying parameters. Never substitute current trading.toml or training constants for an unrecorded session parameter. Keep metadata/header caches bounded and remove disappeared entries.

**Closure date.** A terminal signal with recorded_at_utc and reason/phase `finished` can establish when completion was observed. Store that timestamp and label it as observed completion, rather than an invented session_end time. Terminal evidence has priority even when winner is null. If no timestamped terminal evidence exists, closure date is unknown. The «Закрытые сегодня» block uses Europe/Berlin boundaries and contains confirmed dated rows; its disclosure also lists terminal rows whose closure date is unknown, with an explicit unknown-date label. Do not silently classify them by join date or mtime. This preserves visibility of old archives without claiming an exact date the stored format cannot provide. Do not add a trader timestamp field or parse/replay game feeds in this step. Test a map joined yesterday and observed finished today.

**Disk, existing cadence.** In wallet_facts and the disk apply path, project bounded immutable session facts needed by the home diagnostic: checkpoint decode failure, token-bound non-gone SELL orders, remaining quantities, sell_only, recovery_pending, unconfirmed/pending ownership counts and winding_down. Include venue bindings, unresolved BUY/unsettled evidence and its created_at. Preserve ledger positions and updated_ts, wallet read time and notes. Use the existing short transaction, CheckpointCache and reserve fold; do not instantiate WalletStateStore outside fixtures or duplicate reserve math.

Project each last signal into a `DecisionFacts` record carrying root/game_snapshot second, phase/paused when present, reason, entry_block, model_evaluated, raw_delta, feed_source, the two independent timestamps and source path. Non-finite, malformed and absent numbers remain optional. Only show delta if model_evaluated is true. These facts come from the hub's existing bounded tails; closed/catalog rows use the already cached summarize_session facts. No mutable JSON dictionaries leave the source layer. Drop disappeared tail/decision entries and retain the existing path count bound.

**Nontrading, 60 s.** Resolve DOTA_ARCHIVE_ROOT and LOL_ARCHIVE_ROOT from the existing game-profile/env mechanism into host-visible paths. Missing/unreadable roots become explicit source limitations, not a successful empty list. Add the roots to injected HubConfig. In the slow catalog lane call logs.list_nontrading with cached latest_skip_reasons and the set of currently traded/attached live condition IDs. Surface stale/lost-feed sessions visibly, rather than suppressing their sidecar and then hiding the session. Deduplicate rows by condition/token identity and prefer the live tree when the same archive appears in live/legacy discovery; do not double count totals. Use log or explicit sidecar flags; never assume an absent skip means «ждём фид». Preserve invalid-sidecar counts, scan time, reason time and reason source. Retain the last successful result on failure and mark it stale.

**Health, existing logs lane.** Add one bounded read-only status/log job at the existing 60 s cadence and backoff. Resolve the current compose service container ID and inspect only its state/start identity; request logs for that same container and current start boundary. Use an argv runner, finite timeouts and byte/line limits, no shell interpolation, compose exec, environment dump or restart. A container replacement while the job runs invalidates current-state evidence; publish unknown until the next consistent job. Keep status and log errors separate and preserve last known evidence with its old timestamp. All subprocesses run in the logs executor, never UI or WS loop.

Reduce current-run risk_halt/HALTED and clear events in time order. Existing clear syntax is `alert cleared key=risk_halt:...` in engine_seams; a string containing risk_halt is not automatically a new halt. Scope keys to their recorded account/map identifier; preserve truncated identifiers as such, without guessing collisions. Restart drops previous current-run latches. A bounded/truncated history without enough transitions yields «последнее известное состояние; текущее неизвестно». Container running, risk state and feed freshness are distinct facts. A normal min_delta or no fills is neutral. Keep the reader/reducer in health.py; the hub only applies the returned frozen slice.

**Day lane.** Add attempt-start/inflight fields for day/positions/accrual and clear them on every success/failure path. Carry the most recent candidate positions completeness/error separately from the retained full result. Payout-search failure must not invalidate an independently complete day PnL. Apply the existing funder/generation gate and retention rules. Do not add API calls, change summarize's formulas, or shorten its 300 s refresh period.

### 3. Build pure home DTOs and financial presentation

Join by condition_id/token_id, never by display title or team order. A compact immutable HomeSnapshot consists of strip metrics, diagnostics and four row collections. Source stamps are carried with every derived value. Build it from exactly one HubSnapshot and an explicit current wall time; separate the cheap strip/diagnostic builder from the 60 s list builder.

- **Cash:** primary value is collateral_usdc. Show reserve total/components and «оценка доступного остатка» separately, from ReserveReport.available_cash. Its incomplete flag and limitations stay visible. On missing collateral, source error or funder mismatch, never substitute zero or combine accounts. Actual zero is `$0.00`. Pending fill evidence gives expected cash changes, not proof of what the API has applied; current FillEvidence has event/key/seq/expected_cash, so do not fabricate BUY quantity or price unless a deliberate typed projection supplies them.
- **Today PnL:** use DaySnapshot.fold.pnl, its retained day and fold_at. Label the day Europe/Berlin and expose cash/open_mark/paid rebate in disclosure; use the summarize formula unchanged. A retained yesterday fold cannot be labeled today after midnight. Failed/incomplete refresh retains the last value/time and shows stale/incomplete; without a same-day complete fold show «—».
- **Accrued rebate:** use accrual_total/accrual_at with known payout_ts. Label it an estimate since the last payout; the paid amount in DayFold.rebate is separate. Unknown payout or failed search does not become zero or «all-time since epoch». Show real zero only for a known successful accrual.
- **Active positions:** bind WalletPosition to explicit YES/NO tokens and recorded outcome labels; no guessed Radiant/Blue orientation. Use best-bid/best-ask midpoint as mark only for a valid finite noncrossed pair from the independent book, and label that source. `unrealized = size * (mark - avg_price)`. A one-sided/absent/uninitialized book yields unknown mark/PnL, not zero; a retained disconnected/reconnecting book can provide a visibly stale last-known estimate. Do not use fair/model price as market mark. An empty fully read local wallet means no local position; a failed read means unknown. Recompute marks on each list render from its newly fetched snapshot.
- **Closed totals:** use cached summarize_session.net, realized, imv and rebate with fill_count. The displayed formula is `net = realized + imv + rebate`; missing imv keeps net unknown even if cash/rebate are known. Preserve summarize_session's late-fill handling instead of recomputing a cash-only total. These per-map lifecycle numbers do not claim to sum to account day PnL.
- **Finished residuals:** include terminal/final maps of any age with nonzero API positions, local positions, active BUY commitments or unsettled reserve. Match complete API positions by condition and asset. API size/cur_price/redeemable drive exchange residual valuation/redeem status; local size/avg are explicitly trader accounting, not a claim that redeem has not happened. Never sum duplicate API and local positions. Show both if they disagree, with source times. Reserve-only rows remain visible even if size is zero. Preserve tiny sizes; do not apply min_order_size or API default sizeThreshold. Include unresolved API positions not found in the catalog when redeemable or otherwise proven terminal, with ID/title and unknown match details. Do not label unknown-status external positions as completed just because they are absent from active maps; disclose unmatched records and incomplete coverage.
- **Unsettled detail:** show venue/core IDs when available, token, quantity, remaining reserved notional from the existing fold, and age from created_at. Retained data is last-known when the wallet/source is stale. Old terminal sessions excluded from active-core reserve math remain excluded; do not reintroduce them to the global reserve.

### 4. Diagnostics and freshness

Create a deterministic home diagnostic reducer with explicit severity/source/time/IDs/values. Always render one line; when no issue is proven it reports the available observed state or «текущее состояние неизвестно» where sources are missing. Show the highest-severity finding in the compact line and all available supporting findings in disclosure. Order fatal/read/account/container failures, current-run halt, SELL/ownership/unsettled warnings, stale feed/model errors and incomplete source coverage before neutral BUY blocks. A single always-incomplete orphan estimate should not hide a more actionable finding.

Use diagnostics.explain_exit and observe_sell with per-order/token/session/status state. Observe SELL from checkpoint facts at the fast diagnostic cadence, not once every 60 s. State is bounded to currently observed keys; disappearance, live status, change of status/order, stale input and hub generation change reset its continuity. After dashboard startup, an unknown transition time begins a new observation interval. Pending/canceling/unknown SELL only warns after more than 30 s of continuous fresh observation with held size. Dust and explained unconfirmed/recovery waits are neutral; no SELL without an explanation remains «причина не установлена», not a guaranteed bug. BUY entry_block must not explain SELL. Do not run core replay or model inference.

Diagnostic disclosure displays the actual source (table/file/container log), condition/session/order/venue IDs, revision/core updated_at, relevant numeric values and source ages. Render only already published evidence. An expander does not justify full history reads; there is no full replay action in STEP-004.

Use diagnostics.freshness for all money metrics:

| Value | has_value / age | Failure/completeness | Pending |
| --- | --- | --- | --- |
| Collateral | non-None collateral; age of response_at; 120 s | last_error, funder mismatch and unresolved source gaps | queued/inflight or significant_seq beyond covered_seq |
| Day PnL | same-Berlin-day full fold; age of fold_at; 600 s | latest refresh error/incomplete candidate | day request in progress |
| Accrued rebate | non-None estimate with known payout; accrual_at; 600 s | accrual_error/payout uncertainty | accrual request in progress |
| Available cash | collateral plus ReserveReport | reserve.incomplete, wallet read error and source age, visibly labeled estimate | follows collateral refresh |

Preserve precedence: missing value → no_data; failure/incomplete/too old → stale; otherwise pending → updating; otherwise fresh. Previous values remain on failure. Source time, not HubSnapshot.published_at, determines age. For book-derived marks use connected + initialized + per-token ready after reconnect; do not age out an otherwise ready quiet book using last-change time. Current-running health is text evidence, not a reused money freshness assertion.

`render_fresh(value, state, note)` produces a text label and aria-live="polite" status region with one persistent opacity dot. Use a stable caller identity/key (for example cash/day/rebate) in the focused wrapper; never derive the key from value, age, timestamp or state. Declare the CCv2 component once. Static HTML/CSS defines the dot once; on new data mutate value/note textContent and change the state class only when state changes. Do not replace innerHTML on each fragment run, emit events back to Python, or add a JS timer. Updating uses #8B98A5 and the 1.2 s opacity pulse; stale is muted with dashed underline and no pulse. `@media (prefers-reduced-motion: reduce)` disables animation. No-data uses «— · нет данных», including the first render. Escape any server-generated markup and use textContent for archive/log values.

### 5. Home rendering, refresh and routing

1. `app.py` sets a wide layout and compact page title before rendering. Acquire the hub through a zero-argument global `st.cache_resource` wrapper over get_live_hub. No TTL, selected match, session or query parameter in the cache key. Use a thread-safe on_release callback closing the released hub and keep the hub's existing atexit fallback; closing one browser tab must not retire the process-wide resource. On cache replacement, retire the previous resource before constructing another. Do not add a second registry.
2. Top strip + one diagnostic line use `@st.fragment(run_every=1)`. Obtain the current resource/snapshot inside every invocation, not as a captured startup snapshot argument. Only call snapshot getters and cheap pure transforms. The 1 s fragment performs no archive scan, sqlite read, subprocess or HTTP call.
3. One lists fragment with `run_every=60` renders all four headings, including explicit empty-state text. It likewise obtains a new snapshot inside each invocation and uses the hub-owned catalog/nontrading/day results. Use stable row identities and ordering; account for sources finishing their first job after the initial empty screen. A small UI rerun/refresh interaction may request another snapshot render but must not submit additional network/source jobs.
4. Render «Торгуем сейчас»: teams/map, game, mm:ss/second from the stored decision, phase/window, position, unrealized and human-readable reason. Label active rows «по последним данным» and expose both signal/feed ages. Stale/lost-feed attached sessions remain visible with their stale state; final/terminal sessions are excluded from active rows. Prefer BUY entry-block text for a model decision, otherwise reason_label; a stale decision's reason is last-known. Raw delta/min_delta text uses the current evaluated signal and pinned threshold. Game time never advances from wall time, especially on pause. Missing cutoff gives unknown window, not an assumed 480 s.
5. Render «Не торгуем» from sidecars with latest-known reason/source/time; also expose incomplete/record-only archive entries that cannot be safely classified as trading. Make active and nontrading CID subtraction explicit and keep stale attached maps in the observed-session section. Missing roots or failed scans are visible beside the heading instead of «нет игр».
6. Render «Закрытые сегодня» with net breakdown/fill count and the dated/undated behavior above; render «Остатки завершённых карт» with side/source/size/mark/value/redeem state and reserve evidence. Closed maps can occur in both closed and residual sections because those sections answer different questions.
7. Ordinary compact native rows/columns or a read-only dataframe with links are acceptable; keep each group readable with long names, separate numeric columns and full-name tooltips/disclosure. Use width="stretch" or native defaults, not deprecated use_container_width. No editable controls, trade buttons, third screen or model/book charts.
8. A map row has an internal URL `?match=<encoded-match-id>` and a Polymarket event URL based on the recorded event_slug where available (fall back to the stored market slug without inventing one). Persist IDs instead of list indices. Sidecars without an owned archive can show the external market link but must not invent a dashboard match ID. Missing slug removes the external link. Encode IDs/slugs and render untrusted team names as plain text.
9. Add only a route scaffold for ?match: validate against catalog IDs, show the selected map identity and a home link, and state that its detail view belongs to the subsequent game-page step. Unknown/duplicate-ambiguous IDs get an explicit result and home link, never filesystem path interpolation. STEP-006 replaces this branch with its real screen; STEP-004 must not implement game_state, book ladders or plot_match to make the link work.
10. Keep fragment output within its own body/containers to prevent accumulating rows. Keep disclosure/widget keys stable over reruns. Do not use parallel fragments when all work is already cheap snapshot rendering.

### 6. Documentation

Add one line in `betting_workspace/.shared-skills/vps-trader/SKILL.md`: launch with `make dashboard`, open through `ssh -L 8501:localhost:8501 sun`, then visit localhost:8501. This documents a user action; do not SSH or launch on the VPS. Existing summarize commands/log-map remain unchanged. Do not edit the .agents/.claude/.cursor symlink copies separately.

## Verification and acceptance

All execution is local, uses the esports-trader venv via uv run, and uses synthetic snapshots/archives and temporary sqlite. No real accounts, public sockets, Docker daemon or production paths are needed. This planning pass does not execute these checks or claim they have passed.

### Targeted automated checks

- `test_dashboard_home.py`: token/condition joins and duplicate live/legacy entries; active/stale/record-only/final-null-winner categories; paused clock and unknown cutoff; evaluated/unevaluated/legacy delta; true zero vs missing values; midpoint mark/unrealized with one-sided/crossed/missing/reconnecting/quiet books; closed net with missing imv and late fills; Berlin midnight/DST and joined-yesterday/finished-today; unknown closure dates; tiny API residuals, local/API disagreements, reserve-only old maps, unmatched terminal API position and failed/incomplete positions refresh.
- Source tests only for changed readers/merges: published DTO immutability including nested evidence; terminal journal facts from one cached summary read; nontrading subtraction and missing/invalid roots; copied checkpoint/binding/unsettled facts from temporary sqlite; source error retains its timestamp; day in-progress flags and failed candidate metadata/funder gate.
- Health reducer/reader tests use the injected runner: stopped/missing container, bounded read failure, fresh running state, previous-run halt after restart, halt followed by matching `alert cleared`, truncated history, inconsistent container ID and unknown scope. Do not issue real Docker commands in tests.
- Diagnostic tests: normal min_delta/dust/no fills neutral; position without explained SELL not asserted as a bug; same-order nonlive >30 s warning; order/status/generation change reset; stale input does not continue the timer; evidence includes source/time/IDs/values.
- `test_dashboard_app.py` uses AppTest with an injected snapshot resource: all four headings/empty states; diagnostic always visible; internal/external links and missing slug; selected/unknown match route and return home; refreshed snapshots actually change visible values; no background production acquisition on test import. AppTest cannot validate component DOM/CSS/JS; use the browser for those assertions.
- Keep a fixture runner under tests that builds temporary live.db through existing WalletStateStore/upsert_session fixture patterns, synthetic match.json/session.jsonl/sidecars and deterministic injected HubConfig callbacks. Patch/replace the configuration before running app.py; never call default_hub_config in this runner. Fake books/fetch/balance/log status must fail closed if unexpected I/O occurs. Share the same fixture builder between tests and browser bootstrap, without exposing a demo toggle in the product UI. Two browser tabs should use one injected hub and one source polling schedule.

Run from esports-trader after implementation:

```bash
PYTHONPATH=src uv run --group dashboard python -m pytest tests/test_dashboard_home.py tests/test_dashboard_app.py
PYTHONPATH=src uv run --group dashboard python -m pytest tests/test_dashboard.py tests/test_dashboard_live_hub.py
uv run python -m ruff check src/dashboard tests/test_dashboard_home.py tests/test_dashboard_app.py tests/test_dashboard.py tests/test_dashboard_live_hub.py
uv run python -m ruff format --check src/dashboard tests/test_dashboard_home.py tests/test_dashboard_app.py tests/test_dashboard.py tests/test_dashboard_live_hub.py
uv run --group dashboard python -m basedpyright
uv lock --check
make -n dashboard
```

Include added fixture files in lint/typecheck. If an existing test file was not changed, omit it from the pytest invocation; do not broaden to unrelated tests. Never run full make test or tests/test_extraction_oracle.py / tests/test_follow300_replay.py. Do not alter typecheck configuration to hide new app errors.

Verify the standalone dependency group with `uv sync --group dashboard --dry-run` and, if needed, an isolated temporary venv outside the project's main .venv. The planned selection must not include the backtest group/Nautilus. The default dev group remains selected by uv; `--only-group dashboard` would omit the project dependencies and is not the intended launch. Existing lock entries and packages already installed in the developer .venv do not prove dashboard activation needs them. Inspect import paths: app/home/hub must not import viewer.plot, replay_core_trace, backtest or a watcher for this step.

### Required local browser pass

Before browser execution read the agent-browser skill, then use only `agent-browser --session s4-impl` (or the actual implementer's name) against the offline fixture app on 127.0.0.1. Run a fixture Streamlit entry point, not production make dashboard connected to local real data. Use an unused local port if 8501 is occupied; do not stop unrelated apps. Store screenshots/evidence under the implementer's run/work directory and stop only the fixture process/session that implementer owns.

Check 1366×768 and 1440×900 at 100% zoom: strip and diagnostic remain readable; all four sections render, with fixture rows in each; no clipping with long names and a warning; full IDs/source evidence are disclosed. Check row ?match navigation, reload of a selected/unknown ID, home return and Polymarket href without navigating external accounts.

Inspect the status component's aria-live and text for no-data, actual zero, fresh, updating and stale. Trigger pending/failed/recovered fixture states deterministically; prior numbers remain on failure. While updating persists across at least several 1 s ticks, verify the same dot DOM node and animation currentTime continue across ticks instead of restarting. Check 1.2 s opacity duration and prefers-reduced-motion=reduce yields a static dot. Test malicious-looking names/log values render as text.

Use fixture source-call counters to verify two tabs share one hub, a 1 s UI strip adds no archive scans/HTTP/subprocesses, and list refresh does not create a second polling schedule. Wait for one 60 s list cycle, allowing normal communication during the wait, and verify new rows/marks appear without duplicated headings or evidence blocks. A retained quiet book remains usable; a disconnected/reconnected token is explicitly stale until its own snapshot.

Record exact commands/results, fixture path/port, screenshots, component DOM/animation evidence and any limits in the implementation report. Acceptance requires the four blocks, truthful money/source states, visible diagnostics with evidence, working navigation scaffold, safe local launch configuration, one shared hub and passing targeted type/lint/tests. VPS resource measurement/live trading comparison is the user's later deployment work, not STEP-004 acceptance.

## Documentation grounding

Read the installed Streamlit 1.63.0 skill and its dashboard, data display, theme, performance, testing and inline CCv2 references. Context7 resolved `/streamlit/docs` (official/high reputation) and queried fragment lifetime, shared resource caching and keyed component DOM updates separately. Relevant official sources:

- [Fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments): independent timed reruns; external-container output can accumulate.
- [Resource cache](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource): global shared objects require thread safety. Installed API confirms on_release closes removed entries and is not guaranteed at shutdown.
- [CCv2 mount behavior](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/mount): stable keys update an existing component instance when data changes.
- [CCv2 status/theming](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/theming): static HTML/CSS plus data-driven text/class updates and isolated theme variables.
- [Theme configuration](https://docs.streamlit.io/develop/api-reference/configuration/config.toml): use documented theme/fontFaces/server/browser options; verify against the locked installed version before implementing.
- [App testing](https://docs.streamlit.io/develop/concepts/app-testing): AppTest covers Python UI behavior; component JavaScript, CSS and layout require actual browser verification.
- [uv syncing and groups](https://docs.astral.sh/uv/concepts/projects/sync/): Context7 also resolved `/astral-sh/uv` and confirmed default dev-group inclusion, explicit additional groups and `uv lock --check` semantics.

Recheck unfamiliar signatures with the locked installation's `streamlit docs` commands during implementation. The behavior specification takes precedence over skill template styling and its default chart choices; Plotly is a requested dependency for later plot_match reuse, not a reason to add a home chart.
