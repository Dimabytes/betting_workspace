# STEP-006 — Страница игры

## Scope and authority

Implement STEP-006 only, the match detail page at `?match=<id>`. STEP-001 through STEP-005 have passed; STEP-006 is the only pending step. Use `tasks/live-trader-dashboard/plan.md` as the behavior authority, together with feature.json and progress.txt. The phase config has no extra planning instructions. No Figma links or designReference assets were supplied.

The planner read the requested context and brief, the explicitly named planning skill, workspace/project AGENTS.md, configuration, all feature requirements and progress, and the current dashboard, journal/checkpoint, budget and viewer interfaces. This plan and the requested planner report are the only planning outputs. No product code, commits, feature.json or progress changes occur in this phase.

Implementation remains read-only toward the trader: no strategy, journal, checkpoint, money-accounting or database-schema changes; no model execution, trading-core step/replay on refresh, execution gateway, orders or cancellations. No GRID/Oddin connections, watcher process, SSH, production access, credentials for acceptance, deployment, restart, paper-mode work or edits to poly-maker. Existing public market/balance tasks remain owned by the one LiveHub. Keep the two-screen navigation.

## Inspected implementation and gaps

| Current interface | Reuse and required work |
| --- | --- |
| `dashboard/app.py` | Existing zero-argument cached hub and `?match` scaffold. Preserve home fragments and unknown/ambiguous route behavior; replace only the matched route with the detail page. |
| `home.find_match`, `map_title`, `polymarket_url`, `fmt_second`, `fmt_age`, `fmt_usd` | Reuse routing, escaped URL construction and formatting. Query input must resolve through the catalog, never become a filesystem path. |
| `game_state.GameStateReader.read(entry, now_s)` | STEP-005 provides a locked, bounded reader with frozen GameSummary DTOs. Wire it into the page; do not reproduce feed parsing in the view. Give it a private TailCache so unrelated readers cannot race its mutable caches. |
| `game_types.GameSummary` | Decision, board and table have independent timestamps and labels; table/player slices are additional archive data. Render comparison/continuity notes, unknown fields, XP not-used, and source controls. |
| `hub_types.SessionFacts` | Checkpoint projection currently exposes only nonempty SELL orders. Add a frozen all-order projection, including BUY and retained gone orders, in the existing wallet apply path. Preserve existing sells consumers. |
| `WalletFacts` / `ReserveReport` | Ledger positions, session revision/update time, bindings, unresolved BUY, unsettled and reserve components already exist. Use one published snapshot for each trading render; never query SQLite from a view. |
| `catalog.SessionParams` | Contains threshold/cutoff/min-size/freshness. The trace policy also pins level_usdc, but max_position_levels is not in TraceHeader, SessionParams or StructuralCheckpoint. Do not infer a cap from policy.level_count or checkpoint.rungs. |
| `strategy.budget.map_budget`, `trader.session_budget` | Map room is max_position_levels × level_usdc − held cost − map reserves. Wallet cash and account room are separate. These functions establish semantics; do not instantiate LiveCore/WalletStateStore to calculate the display. |
| `core_trace` | BudgetUpdate and PermissionsUpdate carry last-observed values, timestamps are monotonic anchored by header, and revert/reset records exist. A trace event is not durable current core state. Use only labeled advisory evidence with explicit continuity limits. |
| `market_books.BookSnapshot` | Immutable top-20 public levels, tick, readiness per token/generation, connection times and truncation. Build a compact ladder from it; do not traverse MarketDataService mutable books. |
| `diagnostics`, `home_diag`, `fresh_view` | Reuse reason text, freshness, exit assessment, SELL observation, current-run health and accessible render_fresh. Add selected-map scoping rather than showing another map's findings on this page. |
| `viewer.live_tape.load_live_tapes`, `viewer.plot.plot_match` | Reuse chart data and plot function; these read full archives/trace and feed replay, so run only in the selected, opened chart section at 15-second cadence. Do not import/run viewer.live_app, which includes sync/SSH actions. |
| `summarize.summarize_session`, `ArchiveEntry.realized/rebate` | Keep existing money semantics. summarize's realized is unavailable on an active session until terminal/late-fill evidence; journal net_cash is a cash-flow accumulator, not realized profit. Display unknown rather than inventing PnL. |
| `tests/dashboard_fixture.py`, `dashboard_game_fixtures.py` | Reuse fake hub dependencies, temporary SQLite/core upserts and three feed fixtures; add token/slug/session bindings absent from the game-only fixtures. Browser acceptance must never reach default production hub dependencies. |

## Recommended boundaries and files

1. `src/dashboard/match_page.py`: selected-entry resolution, session-owned observation state, fragment orchestration, disclosure keys and reader/resource access. Keep app.py a small router.
2. `src/dashboard/match_view.py`: compact header, game/player summaries, buy explanation, ages, position, orders and book rendering. Render prepared values; no source reads, strategy calls or network.
3. `src/dashboard/match_state.py`: frozen page DTOs and pure selected-map projection from HubSnapshot + GameSummary + observations. Split book/order projections into `match_books.py` if needed to keep each responsibility readable.
4. `src/dashboard/match_history.py`: selected-map chart data cache and event-history projection, explicit full-read gate, mtime/size identity, bounded cache, snapshot time and errors. A narrow advisory trace reader can live here or in `match_trace.py`; do not create a generic replay/rules engine.
5. Narrow edits to hub_types.py for all-order publication, catalog.py for genuinely saved policy facts, home_diag.py for selected-map diagnosis and home.py/home_lists.py only if extracting shared token/mark helpers prevents duplicate rules. Do not broadly refactor the hub scheduler or resurrect sources.py.
6. `tests/test_dashboard_match.py` for pure behavior; update `tests/test_dashboard_app.py` scaffold assertions into match-page assertions; small additions to existing source/game tests only where interfaces change. Add shared fixture helpers to the existing fixture modules or a small match-specific fixture module.

Use frozen named dataclasses, required arguments, action-verb functions and named intermediate values. Do not add own-data dict[str, Any], anonymous multi-field tuple DTOs, barrel modules, blanket type suppressions or narration comments/docstrings. Keep CSS limited to owned markup for book/player rows; theme and dependencies from STEP-004 already exist.

Streamlit guidance was checked against the installed package's bundled skill/references and Context7's official `/streamlit/docs`: [fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments) support independent run_every intervals; [dynamic containers](https://docs.streamlit.io/develop/concepts/app-design/layouts-and-containers) require tracked open state for lazy computation. Use the installed Streamlit 1.63 API surface, `width="stretch"`, stable keys and existing resource cleanup rather than adding another framework or copying outdated viewer-app cache patterns.

## Implementation sequence

### 1. Resolve identity, token mapping and publish all orders

1. Resolve exactly one catalog MapView via find_match. Missing/ambiguous IDs render their existing warning and return link without source reads. If the catalog initially has no data, distinguish loading from a completed not-found lookup. Refresh resolution inside periodic fragments so stale captured MapViews do not freeze metadata or accept a removed map.
2. Match a saved session by condition_id AND YES/NO token IDs; reject conflicting orientation/identity with a visible limitation. Never borrow another map's session because a display name matches. Include archive path in reader/history/widget identity to isolate equal match IDs across roots. Preserve ambiguous lookup behavior instead of selecting an arbitrary root.
3. Create immutable OrderFacts from every checkpoint order with session/core order ID, token index/ID, BUY/SELL, price, submitted/filled/remaining qty, status, acceptance, cancel/ack reason and bound venue. Remaining qty is max(0, submitted_qty − filled_qty). Include gone/zero-remaining rows for optional inspection but do not overlay them as resting liquidity. Bindings come from the same copied wallet transaction; surface conflicting/unproven bindings.
4. Publish all orders on the existing wallet-facts reducer, without adding a UI disk lane or reading raw checkpoints in Streamlit. Retain SessionFacts.sells or derive it through one shared projection so existing home/SellWatcher semantics remain intact. Update direct DTO construction in fixtures/tests explicitly.
5. Extract one shared token-to-team mapping if reuse requires it. Explicit None orientation must produce unknown side mapping rather than behaving like False. Use saved outcome labels when trustworthy, yes_is_radiant for side orientation, and GameIdentity.side_0/side_1 labels for Dota Radiant/Dire and LoL Blue/Red. Detect outcome/session conflicts; do not guess ordering. All order, position, fill and ladder captions use the same mapping.

### 2. Build the trading explanation and source ages

1. Build the page from one HubSnapshot and the selected GameSummary. Record independent source times instead of claiming an atomic snapshot across disk, feed and public WS. Source errors retain last values with their age/unknown status.
2. Present a compact “сейчас” line: positions per token, live or transitional SELL, and waiting explanation. Use checkpoint-backed flags and existing exit assessment. A pending/canceling/unknown SELL immediately says “ожидаем подтверждения”. Gone orders do not fulfill the exit requirement. Dust is neutral; unexplained missing SELL says “причина не установлена”. Entry_block never explains missing SELL.
3. Use a per-browser-session observer keyed by hub generation + session + token + order ID + status. Observe all displayed order states, prune missing rows and reset on generation/identity change; age is “наблюдаем в этом состоянии N с”. Core updated_at and order creation are not transition times. Reuse observe_sell's 30-second semantics for the SELL warning; do not stitch different orders/statuses. Stale/failed wallet evidence invalidates a current-wedge assertion. Avoid sharing an unlocked mutable observer across concurrent tabs.
4. “почему сейчас не покупаем” shows both reason and entry_block independently, with the decision timestamp and a label that entry_block is a preview at that decision. raw_delta is shown only when model_evaluated is exactly True and the value is finite; False → “не рассчитан”; missing legacy field → “нет данных”. Preserve signed model-side delta, annotate its side convention, and use abs only for the threshold comparison. min_delta formatting reuses entry_block_label.
5. Threshold, cutoff, clip/model/policy facts come from the matching saved session/trace header, not current TOML or current constants. Unknown values remain explicit. Validate header session identity against the selected saved session when available, in addition to existing match/token checks.
6. Remaining entry window is max(0, cutoff − decision game second); use game_snapshot second for the decision with labeled legacy fallback if needed. Never decrement it with now_s or a newer board clock. Show pause and “по последнему решению”; a stale decision may keep its last remaining value but cannot look current. At cutoff/after cutoff display boundary text consistent with existing strategy entry semantics.
7. Map budget must be separate from wallet cash/account limits. Project saved policy.level_usdc if useful, with source. The cap multiplier is currently unrecorded: do not multiply by policy.level_count or present a reconstructed current cap. Read a bounded selected trace tail for the latest finite BudgetUpdate.cap_room_usdc as “последняя записанная оценка лимита карты” with its own timestamp/seq and “текущее состояние неизвестно”; show account_cap_room_usdc separately only when present. If no usable advisory event exists, display “лимит карты неизвестен”. Show known held cost and map reserve components from the existing ReserveReport alongside their completeness; they are useful even without a proven cap.
8. Advisory trace handling must recognize type/identity, header monotonic-to-wall anchor, reset/revert and torn/truncated history. Remove events invalidated by a revert in the retained tail; if the target/anchor is outside the retained tail or continuity is uncertain, invalidate the advisory value. Never upgrade even a complete trace tail to current persisted budget/permissions: checkpoint has no corresponding commit boundary. No full core replay for this block, and no new trader persistence fields to make the display exact.
9. Age block: feed_received_at_utc, recorded_at_utc, core_sessions.updated_at plus revision; separately show archive board/table receipts and dashboard public-book state. Missing/invalid timestamp → “точное время неизвестно”. Use signal.feed_source with match.json legacy fallback, and describe legacy reconstruction as such. New scoreboard or board-driven model calls must not renew the original table's age. A persisted core update is not exchange confirmation.

### 3. Render compact game/player and money blocks

1. Render the STEP-005 GameSummary directly: game/map, sides/teams, signed mm:ss and exact second, phase/pause, board kills, decision gold/difference, source and age. Series score has a separate label. When decision and archive differ, show both slice labels and receipts; never mix recent board kills/players into the decision snapshot silently.
2. Render two five-row player sides with nick/hero, NW and K/D/A. Optional level/alive/respawn/Aegis appear only when present; full names via native help or escaped title/tooltips and detailed disclosure. Preserve player order and complete/incomplete status. Unknown fields use “—”; LoL portrait UUID remains unknown hero; Oddin XP not_used gets its existing text.
3. Objectives and richer player/decision details are closed by default. Render only fields supplied by GameSummary. Current Objectives exposes towers/barracks/roshans for Oddin, while GRID objectives are absent; use “нет данных” for unsupported GRID/LoL objectives rather than inventing dragon/baron counts or adding new feed parsing in the view. A narrow game-state extension is justified only if an already parsed source field actually exists and a focused fixture test proves its provenance.
4. Position rows: token/team, ledger size/avg, mark with source/readiness, unrealized = size × (mark − avg), map realized and rebate. Reuse the validated midpoint rule from home_lists; missing, crossed, one-sided, nonfinite or unready book yields unknown mark/unrealized. Disconnected retained data may be shown as stale, never fresh. A confirmed zero position differs from an unread/failed wallet source.
5. Map realized/rebate reuse cached catalog/summarize facts and their source/cut time; do not call summarize_session every second. Keep realized unknown for active maps when the existing fold cannot prove it. If displaying last fill net_cash for context, label it “денежный поток карты”, not realized profit. Separate full-map rebate estimate from since-last-payout accrual; do not substitute the entire day's wallet rebate. Terminal/late-fill facts and API residuals remain explicitly sourced.
6. Always render a selected-map diagnostic line above the columns. Reuse health/source logic, but filter session/map findings by selected identity before ranking/capping. Retain global current-run HALT/service failures and clearly label their scope. Add game-summary controls/rejections/source mismatch notes as evidence, not automatic bugs. Detail disclosure includes paths, condition/core/venue IDs, times, compared values and reserve/unsettled facts.
7. Keep HALT/reduce_only/recovery/sell_only distinct. Saved flags are checkpoint evidence; any permissions/budget from trace are last-observed advisory values with time and continuity limitations. Dashboard books alongside trader missing_book/own_liquidity_only show two sources/times and do not prove trader MDS is healthy. Normal min_delta, no_edge, cutoff, pre_horn, dust and expected waiting remain neutral.

### 4. Render independent YES/NO books and our orders

1. Show one compact YES and NO ladder, each labeled with its team and side, its own freshness via render_fresh, public source “WS дашборда” and last-change age. Live quiet initialized books stay fresh regardless of last change age. A connected socket does not make a token ready after reconnect. Historical/terminal tokens not subscribed by the hub simply have no current book; opening the page must not create a second socket or alter global subscriptions.
2. Build asks ascending and bids descending using BookSnapshot. Use the top 3–5 public levels per side in the default compact view; allow more levels on disclosure and note top-20 truncation. Group same-price own live orders by token, side and price/tick without floating comparison drift; do not round prices so broadly that distinct levels merge.
3. Overlay only positive-remaining live checkpoint orders in amber #E3A93B, with explicit BUY/SELL and “наши” text. Keep public total and our remaining size separate: public size may already include ours, and asynchronous slices may disagree. Never add ours to public total or assert the public level proves venue ownership.
4. A live order outside the displayed public levels or absent from the public book remains visible in a clearly labeled own-order row with checkpoint timestamp and no fake public volume. A stale checkpoint overlay says “последнее известное live”; its color must not imply current confirmed public liquidity.
5. pending/canceling/unknown orders are always visible separately from live ladder overlays with state and observed-state age. Retained gone rows stay in optional all-order details. Include core/venue ID, remaining size, cancel/ack reason and source time in inspection. Prepared placement/reserve entries without a checkpoint order are separate command evidence, not invented resting orders.
6. All owned HTML values must be escaped, including titles/nicks/heroes/source text, state labels and order IDs. No unsafe script evaluation. Prefer native Streamlit for text/controls; use owned st.html markup only for the compact ladders/player tables where native tables cannot meet the screen-height target.

### 5. Wire fragments and on-demand history

| Section | Cadence | Allowed work |
| --- | --- | --- |
| Match header/decision/diagnostics/game players | 1 s | Fresh hub snapshot, one bounded selected-map GameStateReader call, small selected trace tail, pure projection/render |
| Books/current orders/position mark | 0.5 s | Hub snapshot plus session observation update and pure ladder/position projection; no archive read |
| Chart | 15 s while opened | Full selected-map tapes/history cache refresh; no other map scan |
| Event details | Only opened; 15 s refresh if kept open | Selected journal/history cache; full history only behind explicit user control |

1. Use a zero-argument process resource for GameStateReader(TailCache(...)); the existing reader lock guards all cache/fold work. Keep its private tail cache distinct from advisory/history caches. Put mutable per-tab order observations in session state. No hub replacement based on query parameters, widget state or selected map.
2. Fragments render into containers created inside their bodies (or established safely by the full layout) so repeated writes do not accumulate. Do not nest fragment execution accidentally or enable parallel fragment writes into shared session observations. Keep stable keys across refreshes, keyed by selected archive identity, and reset on navigation.
3. Header and short rows render first; columns are game/players on the left and book/order/position on the right. Do not repeat the full wallet strip above the match page if it consumes essential screen height. Keep route/navigation and market link visible.
4. Heavy disclosures use state-aware `st.expander(..., on_change="rerun", key=...)` and `.open` gating, or explicit toggle gating supported by installed Streamlit. A collapsed ordinary expander alone does not prevent computation. Default chart/history/diagnostic-detail/objective sections are closed; opening them must not hide the always-visible warning/reason/position.
5. Chart uses load_live_tapes + plot_match, existing horn_at_utc and empty_game_state for price/pred by default. Rich game-history panels may use load_live_game_state only inside the opened chart. No horn or no tapes produces a clear unavailable message, not an exception. Existing load_live_tapes returns traded/quoted token tapes, so a pre-trade map may legitimately have no graph.
6. Cache expensive chart source data with max_entries (e.g. 8) and ttl 15 s. Key by archive identity plus resolved journal/feed/trace/match.json mtime_ns/size signatures; recognize gzip replacement and truncation. Do not hash full histories or include now_s in cache keys. Cheap interactive filters/figure styling stay outside the loader cache. Reuse last successful chart with its cut time on a read failure; reset identity after navigation. Bound figure height around 280–320 px for price/pred and preserve zoom with stable uirevision/key. Label trace-derived rests as historical explanatory observations; existing viewer trace fold does not validate reverts/current persistence, so its markers must not feed current order diagnosis.
7. Event disclosure projects fill/late_fill, quote and actual changes of (reason, entry_block), preserving the original timestamp/game second and missing-time states. Consecutive identical pairs do not flood the list; do not order unknown wall times using mtime. A bounded-tail first state has unknown predecessor and is labeled initial observed state. Full selected journal reads are allowed only on explicit full-history request, share the 15-second cache, and cap rendered rows with a clear truncation notice. No full core replay action is required for this step; if offered later, it needs its own explicit gate.

## Layout and accessibility acceptance

At browser zoom 100%, verify 1366×768 and 1440×900 for all three source/game variants. Budget the default layout to fit compact header, game totals, one diagnostic line, BUY explanation, “сейчас”, position and a small YES/NO book above the fold. Use short sentence-case labels, small vertical gaps, tabular numbers and the STEP-004 palette. Our orders are amber; Radiant/Blue green and Dire/Red red. No shadows or decorative animation; the existing freshness dot and reduced-motion behavior remain.

Long names must ellipsize or use the existing compact forms without obscuring data; full values are accessible through help/title/detail. Render pending states with text, freshness with text and aria-live, and provide keyboard-accessible disclosures/navigation. At 768 px height, collapse chart/details first; never collapse game totals, the BUY reason, current SELL/position or active warning to make room. Measure actual DOM bounding boxes and capture screenshots; browser viewport screenshots alone are insufficient proof if essential blocks lie below the fold.

## Verification plan

### Pure/source tests

Add focused tests for these observable behaviors (do not mirror DTO constructors only):

- All-order publication includes BUY/SELL/live/pending/canceling/unknown/gone, partial fills, overfilled remaining clamp, binding mismatch and checkpoint decode error; source values remain immutable.
- YES/NO orientation both ways for Dota and LoL; missing orientation and conflicting tokens remain unknown; orders/positions/books all agree with the same mapping.
- model_evaluated True/False/missing with stale or nonfinite raw_delta; actual zero delta survives; threshold unknown and min_delta mismatch formatting; entry_block remains decision-time preview.
- Pause and advancing wall clock cannot change decision cutoff remainder; newer scoreboard does not change decision/input age; missing UTC fields never fall back to mtime.
- Map limit uses saved advisory BudgetUpdate with labeled time, absent max_position_levels stays unknown, policy.level_count is never treated as cap; revert/reset/truncated/foreign trace invalidates advisory evidence. Map reserve uses the existing no-double-count components.
- Position marks for valid, one-sided, crossed, unready, disconnected books; genuine zero versus missing wallet; realized stays unknown without terminal evidence and net_cash is not relabeled profit.
- Own live level aggregation, off-screen/absent public levels, bid/ask sides, tick handling and independent public versus own volume; transitional orders never join the public ladder.
- State observation reset on status/order/navigation/hub generation; disappearance/live resets SELL waiting; 30 seconds exactly versus more than 30; stale wallet never asserts current wedge; dust remains neutral.
- Selected-map diagnostics ignore another map's SELL/feed error but retain global HALT; missing_book and own_liquidity_only remain distinct, own dashboard WS does not explain away either.
- Event pair deduplication, unknown timestamps, bounded first predecessor, failed chart read retains last cut, cache identity/replace/truncate behavior. Fast/book render does not call full loaders or summarize_session.

### AppTest

Patch dashboard.live_hub.get_live_hub before app execution and clear st.cache_resource between independent tests, as STEP-004 learned. Keep real hub lifecycle cleanup in fixtures. Test known/unknown/ambiguous routes, all three game fixtures, no current orders, residual terminal map, absent slug, long names and escaped malicious text. Assert no exception and the required sections/side labels. Spy on full history/tape/replay loaders to prove zero calls with disclosures closed and no calls on fast/book refresh; open chart/history deliberately and check refresh/cache behavior. Verify match navigation clears old session observations and preserves home rendering. AppTest cannot prove actual CSS/fragment timers, so use browser checks below for those.

### Offline browser acceptance

The implementer reads the agent-browser skill before use. Run a fixture-only Streamlit launcher on 127.0.0.1 at an unused local port; patch get_live_hub before runpy executes app.py. Reuse tests/dashboard_fixture fake dependencies and all three STEP-005 feed archives with coherent current timestamps. Do not launch the normal app against local live.db/accounts. Store scratch launcher, control data, screenshots and logs in `run/work/s6-impl/` (not committed).

Use only `agent-browser --session s6-impl ...`, never close --all. Verify 3 variants × 2 viewports, 100% zoom, long names and active warning; capture bounding boxes/screenshots confirming essential blocks within the viewport. Open objectives/player/full-order details, chart and event history; verify no accidental hidden full reads through fixture counters. Drive pause, model skip/error, pending→live/canceling/unknown, 30-second warning, wallet failure, NO-only disconnect/reconnect readiness and legacy missing timestamps. Check amber live overlays versus transitional rows and stale data text, Polymarket slug link and YES/NO Blue/Red orientation. Check two tabs share one hub/subscription set and do not double source network polls; independent per-tab observations are allowed. Test returning home and reentering a different match without stale content. Inspect actual DOM and console for escaping and exceptions. Stop only the acceptance server/processes started for this task.

### Commands and closing evidence

Run from esports-trader through the project environment, only the tests added/touched by this implementation:

```sh
uv run --group dashboard pytest tests/test_dashboard_match.py tests/test_dashboard_app.py
uv run --group dashboard ruff check <all changed Python paths>
uv run --group dashboard ruff format --check <all changed Python paths>
uv run --group dashboard basedpyright
uv lock --check
```

Replace the path placeholders with the actual changed files. If hub/source/home test modules were touched, run those specific modules too; no full make test and no tests/test_extraction_oracle.py or tests/test_follow300_replay.py. Do not broaden testing after scoped checks pass unless a concrete unresolved regression justifies it. Plan writing itself does not need product tests.

Implementation notes must record changed files, targeted test counts/results, type/lint results, browser acceptance matrix/screenshots and any limitations of advisory budget, unavailable objectives, active-map realized, compressed archive read costs or legacy provenance. Do not claim deployed/live-map validation. A passing step requires the functional page, visible critical content at both viewports, offline acceptance for all three variants, and explicit unknown/stale states wherever disk facts cannot prove a current value.

## Tradeoffs and completion criteria

The chosen design uses the existing game-summary reader and hub snapshots with thin domain projections. Putting everything in app.py would grow an untestable UI monolith; reconstructing the strategy from trace on every refresh would make current claims from buffered events and add unnecessary I/O. The proposed split preserves straightforward pure tests and source provenance without another worker architecture.

Two facts currently cannot be reconstructed exactly: current map cap is not persisted, and active-map realized is not provided by the existing terminal fold. Explicitly showing the unknown value and the last-observed budget estimate fulfills the specification's missing-data behavior without changing trading/persistence semantics. Similarly, render only source-proven objectives. These are concrete source limitations, not grounds to add trader state or invent defaults.

Complete STEP-006 when ?match resolves safely, STEP-005 summaries and separate clocks remain intact, BUY explanation/current SELL/position/own book are visible, unknown/stale values remain honest, chart/history are gated and cached at the prescribed cadence, and local targeted/browser checks pass. No implementation or commit is part of this planner task.
