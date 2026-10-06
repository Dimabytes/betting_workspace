# STEP-003 — LiveHub: один процесс, стакан и баланс

## Scope and inspected state

Implement STEP-003 only: a process-wide read-only background hub providing immutable public books, event-driven collateral balance, wallet reserve estimates, bounded Docker-log retrieval, and five-minute day/rebate refresh. STEP-001 and STEP-002 have `passes: true`; STEP-003 (priority 3) is the next pending step. The behavior authority remains `tasks/live-trader-dashboard/plan.md`.

Read in full: feature.json and its relatedSources, plan.md, progress.txt, workspace/code AGENTS.md, phase config, and the specified feature-json-create-step-plan skill. `plan.instructions` is empty. No Figma/designReference links exist. Inspected code HEAD: `e4c659d494a096d2543d902a99dfeff54b04a09c`; code working tree was clean. The earlier-step review split sources.py into wallet.py, reserve.py, tails.py, catalog.py and logs.py; use those modules directly, with no sources.py barrel.

This is planning, not implementation: write this plan and the planner report only, do not edit product code, commit, change passes/progress, or start a live hub. Implementation must not change trader behavior, checkpoint/schema/accounting, fork code, or paper mode. No accounts, real requests/orders, SSH/VPS, daemon restarts, deployment, or server resource measurements during acceptance. Streamlit screens, dependency group/theme/Makefile, game projections, graph rendering, and launch documentation remain STEP-004/005/006.

## Reuse and constraints established from code

| Source | Relevant contract |
| --- | --- |
| `dashboard.wallet` | `read_wallet_snapshot` copies related tables in a short read-only BEGIN/COMMIT, closes connection before checkpoint decoding; `read_outbox_page` reads all seq > cursor regardless of acked. Outbox event is lowercase; joined ledger status/cash_delta are current, not historical event values. |
| `dashboard.reserve` | `compute_reserve`, `compute_orphans`, `read_order_log` already handle commitments/deduplication. ReserveReport is intentionally always incomplete because order_log cannot prove current memory-only orphan coverage. Preserve that limitation. |
| `dashboard.catalog`, `dashboard.tails` | MatchCatalog discovery and full summaries are cached for 60 s; TailCache provides bounded journal tails. classify_entry distinguishes live, stale, terminal, final and incomplete. Old core rows alone do not prove a live map. |
| `dashboard.logs` | read_service_logs is synchronous and bounded to 2000 lines / 1 MiB with 15 s subprocess timeout. Failed read is explicit; latest_skip_reasons/list_nontrading are reusable. It does not prove current container start/health. |
| `dashboard.summarize` | fetch_day has a shared 60 s activity+positions deadline and 10 s per request; publish_day retains a previous same-day full fold/time on failure. Day completeness and last-payout-search completeness differ. accrued_rebate_since returns a mutable per_game dictionary inside its frozen dataclass. |
| `dashboard.diagnostics` | Pure freshness precedence already exists: absent value, then failure/incomplete/expired/disconnected/awaiting book, then updating, then fresh. Thresholds are balance 120 s and full day 600 s. |
| Fork `marketdata/service.py` | set_markets only rebuilds desired `_subs`; it neither closes the current socket nor removes old books/_token_condition. stop only sets an Event and does not close a socket blocked in receive. No public reconnect/full-book callback. on_dirty includes price_change, so it cannot establish snapshot readiness. |
| Fork `marketdata/orderbook.py` | bids/asks are mutable SortedDicts; local_ts changes for both snapshot and delta. BookView is frozen but holds only top/depth analytics, insufficient for the UI ladder. Capture named immutable levels in the owning loop. |
| Fork `execution/gateway.py` | Reuse client configuration/auth pattern, not ExecutionGateway itself: it owns trading infrastructure. ClobClient, Secrets and WalletConfig are already available through polymaker dependencies. |
| Installed SDK `py_clob_client_v2` 1.0.2 | get_balance_allowance uses L2 auth and BalanceAllowanceParams(COLLATERAL), with signature_type from client.builder. Helpers use a module-global httpx.Client; locally verified its finite default timeout is 5 s. No client constructor timeout/transport parameter. Do not mutate or close the SDK's process-global transport on hub replacement. |
| Installed Streamlit 1.63.0 | Global cache_resource shares the resource by reference across sessions and requires thread safety; on_release exists for removal, but is not guaranteed at app shutdown. UI wrapper belongs to STEP-004. |

Documentation was fetched via Context7 (`/polymarket/py-clob-client-v2`, `/streamlit/docs`) and the installed Streamlit skill/performance reference. Account calls/auth match the [official SDK reference](https://github.com/Polymarket/py-clob-client-v2/blob/main/_autodocs/api-reference/ClobClient.account.md). Global resource semantics are described in [Streamlit caching](https://docs.streamlit.io/develop/concepts/architecture/caching). Collateral `balance` is a fixed-point integer with six decimals, verified directly in the [official CLOB OpenAPI schema](https://docs.polymarket.com/api-spec/clob-openapi.yaml), `BalanceAllowanceResponse`. Do not copy doctor._extract_balance's magnitude heuristic: raw `500000` is $0.50, raw `1000000` is $1, raw `0` is valid zero.

## Files and boundaries

Add focused modules rather than one large scheduler/transport/state file:

- `src/dashboard/live_hub.py`: process singleton, lifecycle, async tasks, source orchestration and atomic snapshot publication.
- `src/dashboard/market_books.py`: thin fork adapter, per-connection readiness and immutable book snapshots.
- `src/dashboard/balance.py`: pure fill/request state reducer plus a small synchronous SDK balance reader, typed results and scheduling inputs.
- `src/dashboard/hub_types.py`: shared frozen published DTOs only if needed to avoid cycles; keep domain DTOs with balance/books otherwise.
- `tests/test_dashboard_live_hub.py`: deterministic lifecycle, scheduling, books and worker integration tests; extend `tests/test_dashboard.py` for directly changed STEP-002 readers.
- Small necessary edits to `dashboard.wallet`, `dashboard.reserve` and/or diagnostics only for the integration gaps explicitly below. Keep summarize stdlib-only and preserve its formulas/CLI.

No new runtime dependency, __init__.py, framework for generic jobs, broad Any, suppression pragma, unnamed multi-field tuple contracts, narration comments/docstrings, or unrelated refactor. Use frozen named dataclasses, tuples/frozensets and copied MappingProxyType values. Required arguments for new functions; explicit clocks, paths, transport callables/factories make tests independent of production.

## Implementation sequence

### 1. Define immutable snapshots and ownership

Publish one HubSnapshot containing generation/publication time, immutable BookSnapshot collection, BalanceSnapshot, ReserveReport with wallet source time, wallet read status/source facts needed by later screens, day/rebate snapshot, catalog/subscription provenance and ServiceLogs read status. A source can fail while other sources stay healthy; carry errors/times per source rather than a single global fresh flag.

BookSnapshot includes condition/token, ordered bid levels (descending), ask levels (ascending), tick size, optional book hash/exchange timestamp, local last-change time, connection generation, initialized-ever, ready-in-current-generation, connected and disconnected_since. Valid empty or one-sided full book is a received snapshot; it is not 'no data'. Snapshot readiness is not tradability.

BalanceSnapshot includes optional collateral_usdc, request_started_at, response_at of latest successful response, latest attempt/error, queued/inflight state, request cutoff seq and successful covered seq, last significant event seq, and immutable pending-event evidence. A failed response never replaces a known value with zero or renews response_at. Numeric value and reserve completeness are separate: raw collateral can be fresh while derived available_cash is an incomplete estimate.

DaySnapshot separates retained full fold/date/as-of time, latest attempt/error/completeness, complete retained positions, and payout/accrual coverage/time. Copy RebateAccrual.per_game to an immutable mapping; do not expose mutable SessionSummary/TailView dictionaries or cache objects to UI. If wallet/checkpoint structures contain mutable nested values, project the needed named immutable fields rather than assuming frozen outer dataclasses make the entire graph immutable.

Only the daemon asyncio-loop mutates live hub state and fork books. Source workers return detached results; reduce/merge them in the loop. Build the next snapshot outside the lock; acquire a publication lock only to swap its reference. get_snapshot briefly captures the reference, releases the lock and returns it. No HTTP, filesystem scan, level copy, reserve calculation or rendering under that lock. No synchronous I/O in snapshot getters or constructors imported by UI.

### 2. Build lifecycle and process singleton before networking

Provide a no-tab-argument get_live_hub() with a module-owned registry guarded independently from publication. The first call constructs/starts one hub; later calls (including concurrent callers) return the same running object. Production paths/config are process-scoped; selected match, browser session and fragment must not be factory arguments. Tests construct hubs through explicit injected dependencies without starting real transports.

Hub start runs one daemon thread with one asyncio-loop and explicit task handles. Startup exceptions publish an error/failed-start state and close partially created resources. Importing modules does not open files, read credentials, start workers or make requests. The synchronous getter need not wait for successful HTTP; return a valid 'no data, starting' snapshot.

close() is idempotent and thread-safe. From outside the hub thread, signal shutdown using call_soon_threadsafe/run_coroutine_threadsafe; never manipulate asyncio.Event/socket directly from UI threads. Stop producing jobs, stop/close MDS socket, cancel and await async tasks, and drain outstanding worker calls before finishing executor shutdown/join. Cancellation of await run_in_executor does not terminate the running sync function: retain the underlying future and do not submit a replacement of that request type while it still executes. Worker results after shutdown are discarded.

An explicit replace/close registry operation fully retires the previous tasks/socket and worker ownership before starting a new hub; do not publish a new singleton while the previous one still performs requests. Do not hold publication lock during retirement. If shutdown cannot complete, report that failure rather than silently launching duplicate workers. Register process shutdown cleanup as a best-effort fallback; explicit replacement and later cache on_release remain the reliable application lifecycle paths. Tests must prove replacement order and absence of orphan tasks/thread/socket.

For STEP-004 handoff: wrap this getter once with global st.cache_resource, no session/match args, no TTL-driven hub churn; use installed-version on_release to close the resource when explicitly evicted. Closing one browser tab must not stop the global hub. Do not add Streamlit import/dependency to STEP-003 runtime.

### 3. Adapt the existing MarketDataService without fork edits

Use a small dashboard subclass/adapter around the installed fork. Encapsulate the necessary private connection/book/socket hooks in market_books.py; no access to those hooks from UI or general hub state. Existing MDS remains responsible for websocket URL, message parsing, snapshot/delta updates, pings and reconnect backoff.

- On each `_connect_and_listen` attempt, advance generation and reset ready tokens before entering the inherited listener; publish loss of readiness even if the disconnect/reconnect interval is too short for a periodic status sampler. On exit publish disconnected status. Subscription-triggered reconnect uses the same lifecycle.
- Intercept accepted `book` handling using parse_book validation and current desired-token membership, delegate book application to the inherited method, then mark only that token ready. A dirty callback from price_change never sets readiness; invalid/unknown-token book cannot do so. Schedule/defer publication so inherited dirty wake cannot publish that token as fresh before readiness is set. A small duplicate parse at this narrow boundary is preferable to duplicating the service's full dispatch/update logic.
- Preserve previous immutable levels on disconnect/reconnect only as stale historical values. Clear mutable book data for the new generation (or ignore deltas until its full book); pre-snapshot deltas must not corrupt the retained last-full snapshot or make it fresh. A fresh book is required independently for YES and NO.
- Capture levels/view only inside the owner loop. Limit rendered ladder snapshot to a named constant such as top 20 levels per side, with an explicit truncation flag; full book maintenance remains MDS-owned. Coalesce bursts into at most one pending publish callback rather than copying every token on every frame.
- Build canonical desired subscriptions from cached live catalog/classify_entry evidence: exact condition ID and its saved YES/NO token IDs, exclude terminal/final/stale/record-only/incomplete entries, deduplicate tokens, expose missing/conflicting mappings instead of guessing. Viewing a historical map does not alter subscriptions.
- Every 5 s recompute desired set from current cached source data. Only on change call set_markets and await closing the active socket so the inherited run loop reconnects. Remove unsubscribed token books, condition map entries, readiness and published snapshots from adapter memory. Empty desired set also closes the previous socket. Re-adding a token requires a new full book.

Match discovery still uses the existing 60 s catalog cache, so a brand-new archive can take up to that interval to become known; the 5 s task applies changes to known desired subscriptions, not a new full-history scan. Fresh tail session_end can remove a known map sooner. Publish catalog built_at and subscription applied_at so this delay is visible. A quiet ready book on a connected link stays fresh regardless of last-change age; use diagnostics.freshness without applying the balance/PnL age threshold to quiet books.

### 4. Read startup state and fill events correctly

Target the host live tree (`data/trader_live/wallet/live.db`), with explicit injectable paths. Do not use trader.paths.TRADER_DIR blindly: it is the in-container `data/trader` root. Do not switch to a paper wallet. Reuse read_wallet_snapshot, read_order_log, TailCache and cached catalog; short database reads/filesystem work run in the disk lane and connections close before computation/publication.

Extend the existing read-only wallet snapshot with current MATCHED fill keys copied inside its same BEGIN/COMMIT as max_outbox_seq. Seed the hub's set of already-accounted awaiting-confirmation keys and its cursor from this coherent initial cut. This avoids historical replay and correctly suppresses CONFIRMED for a MATCHED recorded before dashboard startup. No added table/index/migration, WalletStateStore instance, writes or ACK changes at runtime. Keep the new key set proportional to unresolved MATCHED fills, not all lifetime fills.

If startup wallet/outbox read fails or its required fill tables are missing, do not seed a fake zero boundary. Publish source error, retry with backoff and keep the cursor uninitialized. On first coherent cut initialize once and queue initial balance. A failed later source read preserves cursor and last successful snapshot with error/age; never skip to max seq on failure.

Poll indexed seq > cursor on the disk lane (approximately every 0.5 s), bounded pages (e.g. 256), process ordered rows, then advance only to the last processed seq. has_more causes a yielding next page, not loss of the remainder or a busy synchronous drain of unlimited rows. Read ACKed rows too. Page failure preserves cursor. Do not update the event cursor merely because a later full wallet snapshot exposes a larger max_outbox_seq.

Use `FillEvent.event` to distinguish lifecycle transitions, not joined current ledger status. The same page may contain matched then confirmed with both joined statuses already CONFIRMED, or matched then failed with cash_delta already zero. Derive expected cash direction from side/price/size for the matched transition when needed; failed must remove prior expectation and mark a new invalidation even when its current cash_delta is zero. Rows with missing/invalid associated data leave a source/completeness limitation rather than silently presenting a clean result.

| Event | Reducer action |
| --- | --- |
| first matched after cursor | Add its fill_key to awaiting-confirmation keys; record significant seq and expected cash evidence once; queue balance refresh. |
| confirmed with known prior matched | Remove awaiting-confirmation key; do not add cash, significant seq or another refresh. Existing pending matched refresh remains pending. |
| confirmed without prior matched | Record the newly accounted direct-confirmed effect; significant seq advances; queue refresh. |
| failed | Remove awaiting-confirmation/pending matched expectation, retain rollback evidence, advance significant seq and queue refresh even if expected net effect is zero. |

Cursor idempotency handles replay of the same outbox rows. Terminal keys do not need a permanent dedup map because store writes terminal transitions once. Never dedup by clob_trade_id alone: fill_key identifies the individual maker leg. A single trade can contain multiple legs.

### 5. Implement the balance state machine and synchronous reader

Use monotonic time for deadlines/rate limits/backoff and wall UTC timestamps for user provenance. Store one queued boolean/deadline plus one in-flight request, not a task per event. Startup, significant fill events and periodic refresh all feed the same scheduler.

A request can start only when no prior balance call is executing and monotonic time is at least max(last_request_start + 5 s, retry_not_before). Schedule periodic polling for 60 s after last request start; event-driven success does not create a parallel periodic request. Multiple early events coalesce into one delayed call; events received during a request only dirty the next refresh. A long call never overlaps a second. Failure increases backoff (e.g. 5, 10, 20, 40, 60 s), clears only after success, and new events do not bypass it. Apply the same policy to 429, respecting Retry-After if available from the exception. No tight retry loop inside transport.

At submission capture request_started_at and request_cutoff_seq = last fully processed outbox cursor. This conservative cut must never jump to a max seq whose rows have not been reduced. On successful response publish response_at and advance covered_seq only to that captured cut; clear pending evidence only through it. Later significant events remain pending even if the returned number looks plausible or expected deltas sum to zero. An event committed during the request but not read until after response still has seq > cut and schedules the next refresh. Ignored matched-to-confirmed rows may advance the read cursor but do not dirty a clean balance.

Initial SDK client creation/auth also runs on the single balance worker, using PK/BROWSER_ADDRESS from project .env/Secrets and signature_type from config/trading.toml. Reuse WalletConfig defaults for host/chain, explicit project paths, and create_or_derive_api_key()/set_api_creds as gateway does. Do not instantiate gateway, engine, user websocket, heartbeat or strategy. Do not print/put secrets or credentials in snapshots/errors. Check configured funder against read wallet identity before publishing that account's money; mismatch/absence is explicit unknown, never a guessed wallet.

Call only get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)) for account polling; no update_balance_allowance, orders, approvals, redeem or mutations. Validate response object and finite nonnegative integer balance, convert raw base units by 1,000,000 exactly once; reject bool/blank/missing/malformed/NaN/negative. Keep zero valid and allowance separate from cash. Typed reader returns amount/error without arbitrary response dictionaries crossing the hub boundary.

The installed SDK's httpx default 5 s connect/read/write/pool timeouts satisfy finite transport timeouts. Explicitly verify them in a fake-transport test; document SDK version coupling. Do not claim asyncio.wait_for cancels an HTTP worker. Do not replace the SDK-global client just to set a timeout, or shut it down on replacement. If the installed version changes this contract, adapt the small dashboard reader with an owned finite-timeout transport before accepting the step, with no fork/SDK source edits or global monkeypatch in production.

### 6. Isolate blocking jobs and publish reserve estimates

Use separate owned single-worker lanes for balance, day/rebate, fast disk reads, slow archive/catalog scans, and Docker logs. Explicit small executors are sufficient; no general-purpose job framework. Each coroutine awaits its current job before scheduling another of that type, so queues cannot accumulate. A slow day API or archive scan must not delay balance/outbox polling; a 15 s Docker timeout must not occupy the disk lane. Keep WS loop callbacks nonblocking and calculations small; archive history scans and heavier reserve/source assembly also remain outside it.

| Job | Cadence / failure behavior |
| --- | --- |
| Wallet/outbox | ~0.5 s, one disk read chain; error preserves cursor/value, finite SQLite timeout and short read cuts. |
| Catalog | 60 s cached full discovery/summaries on slow lane; tails of known maps can refresh ~1 s on disk lane. |
| Desired subscriptions | 5 s against already published source snapshots; only changed sets reconnect. |
| Balance | Initial/event request, minimum 5 s between starts, periodic 60 s, one dirty flag/backoff. |
| Day and accrued rebate | Initial request then 300 s, one composite job with bounded API deadline. |
| Docker logs | 60 s, read_service_logs timeout/limits, one job, increasing pause after failure. |

Use compute_orphans and compute_reserve without new money arithmetic. Pass proven final/terminal CIDs, not all non-live CIDs: stale unterminated maps still retain commitments. Recompute reserve when successful wallet/order sources or collateral change. Missing order_log and unavoidable orphan uncertainty retain incomplete=True and a specific limitations message, even if known reserve is zero. If necessary, add a narrow limitation string in reserve.py because currently its always-incomplete result can have an empty limitations tuple. Derived available_cash remains collateral minus known reserve and may be negative; do not clamp it or imply spend permission.

CheckpointCache currently accumulates a key per historical (session,updated_at,revision). A long-running hub must retain only latest decoded version per session and evict absent sessions (small scoped wallet.py change with a focused test). Bound adapter books, published prior snapshots, pending fills and cache entries; do not retain each historical HubSnapshot. TailCache and MatchCatalog already have their own bounds/lifetime behavior.

### 7. Refresh day/PnL/rebate atomically using summarize

Use fetch_day(funder, now=start_wall, ...) in the day worker; it already calls fetch_activity/fetch_positions, fixed end, dust inclusion and fold_polymarket_day. Reuse publish_day for same-day last-full fold retention. Keep the existing Berlin-day formula and local maker estimate unchanged; do not recalculate daily PnL from wallet cash or duplicate pagination/accounting code.

Keep an independently retained complete PositionsResult for residual-market UI. publish_day may attach the latest partial positions/activity while retaining an older fold; consumers must never take those latest candidates as the previous complete inventory. Publish day fold, complete residue snapshot and their source times coherently; expose candidate stop_reason/error separately. On an error or incomplete traversal, retained numbers/times stay unchanged and freshness is stale; without an earlier full value show no data. A fully valid empty inventory/day can produce true zero.

Payout search is independent of day completeness: a full day can publish while accrued rebate is unknown. Compute last_rebate_payout/accrued_rebate_since only with a proven payout boundary; if a complete exhausted history contains no payout, show 'no known payout / accrual cut unavailable' rather than invent a timestamp. Paid rebate is the day fold's rebate; accumulated maker estimate is a different field. Preserve a prior estimate with its own age/error when a later payout search/accrual fails.

Use explicit live/legacy live match paths from the cached catalog for accrued_rebate_since, so its default match_dirs does not pull paper mode or enumerate archives from a wrong root. Accrual scans happen once per slow five-minute job, never in fast getters. Publish all mutable result mappings as detached immutable projections. Day turnover uses Berlin date; do not show yesterday's fold as today's number during midnight fetch/failure. Tag request/result by funder, day and hub generation; discard retired-generation/account results.

Docker log failure retains previous successful text with age plus the latest error; do not read Docker on UI rerun. Expose logs as observations, not proof that the trader is alive or a historical HALT is current. STEP-004 can render unknown current health when container-start provenance is unavailable.

## Focused verification and acceptance

Use fake clocks, fake SDK/data fetches, fake MDS/socket and temp SQLite/archive fixtures. No network, live DB or real .env in tests. Use the existing test schema fixture convention (WalletStateStore may construct only a temporary fixture DB), then write through temporary sqlite connection/upsert_session; runtime readers remain mode=ro. Async tests can use asyncio.run rather than add pytest-asyncio. Control slow fake calls with threading/asyncio Events, not wall-clock sleeps. Inject factories into internal constructors; the public process getter remains no-argument.

| Group | Required observable checks |
| --- | --- |
| Singleton/lifecycle | Two and concurrent get calls return one object/start one loop/job set; get_snapshot triggers no I/O; replacement closes old socket and awaits tasks/workers before starting new jobs; close twice is safe; partial startup failure cleans up. |
| Immutable publication | Old snapshot unchanged after book mutation/new publication; nested tuples/mappings cannot be changed by caller; UI snapshot getter completes while HTTP is held; lock not held during worker/render/computation. |
| Reconnect | YES/NO previously ready -> reconnect -> both stale; YES delta not ready; valid YES book makes YES fresh only; NO still awaits its own book; invalid book ignored; quiet ready connected books remain fresh; disconnect stale; valid empty snapshot initializes. |
| Subscriptions | Same set does not reconnect; changed/empty sets invoke set_markets and socket close once; removed tokens purged from all adapter maps/snapshots; re-add needs full book; selected UI match cannot produce extra subscriptions. |
| Fill dedup | MATCHED -> CONFIRMED causes one event refresh, including different pages and startup-seeded MATCHED; direct CONFIRMED refreshes; two maker legs count separately; acked rows processed; joined status already CONFIRMED/FAILED does not erase original event meaning. |
| FAILED | Removes pending expectation, queues refresh with ledger cash_delta=0; failed after covered MATCHED still dirties; failure during request remains dirty after response; no double cash estimate. |
| Rate/seq | Many events within 5 s yield one delayed request; 5 s exact boundary accepted; periodic 60 s coalesces; one request held while later events arrive; successful response covers only its cut and leaves newer events queued, including event not read until after response; no dirty erasure when expected net=0. |
| Worker/backoff | Held balance/day/log jobs do not prevent fake socket book processing; one call per type; timeout/429 errors retain previous values and back off without event bypass; cancellation does not start another sync call while old one is running. |
| Startup/source | Coherent current-MATCHED keys plus initial seq; no historical replay; missing DB not created; missing required fill tables never seed fake boundary; failed pages preserve cursor; page limit remainder processed; connections closed before network/fold. |
| Balance parser/auth | raw 0, 1, 500000, 1000000, 125500000 convert to 0, .000001, .5, 1, 125.5; blank/missing/negative/NaN/bool rejected; fake client sees COLLATERAL/signature/funder/auth once, and no trading/update methods; finite timeout contract verified. |
| Reserve/cache | Integration preserves STEP-002 partial/canceled/unsettled arithmetic; finished core excluded but old unsettled retained; orphan incomplete and explanation visible; wallet failure cannot look like fresh zero; repeated checkpoint updates retain bounded latest cache versions. |
| Day/rebate | Initial/300 s cadence; later page timeout/incomplete response retains exact prior full fold and original age; independently retained full positions cannot be replaced by partial candidate; no previous success -> no data; payout-search failure does not falsify full day; unknown cut stays unknown; date turnover never relabels yesterday; rebate mapping immutable. |
| Freshness | No data distinct from true zero; updating when significant seq uncovered; failure/age/coverage priority over pending; balance raw freshness independent from reserve estimate completeness; per-token snapshot-generation readiness used. |

Run only added/touched tests, from esports-trader:

```bash
PYTHONPATH=src:scripts uv run python -m pytest tests/test_dashboard_live_hub.py tests/test_dashboard.py -q
uv run python -m ruff check src/dashboard tests/test_dashboard_live_hub.py tests/test_dashboard.py
uv run python -m ruff format --check src/dashboard tests/test_dashboard_live_hub.py tests/test_dashboard.py
uv run python -m basedpyright
```

The read-layer tests are included because wallet/cache/limitation changes touch them. If summarize is changed, also run its isolated existing --self-check with system python3 as its explicit stdlib-CLI exception; otherwise do not broaden tests. All other Python uses project uv run. No full make test, test_extraction_oracle.py, test_follow300_replay.py, golden replay or unrelated suites. This is a backend step: browser/curl production smoke is neither necessary nor authorized; fake transport verifies public behavior without accounts. STEP-004 will verify two local fixture-backed browser tabs share the same hub.

Review diffs after checks: only dashboard modules and named tests, no trader/fork/strategy/config/dependency/UI changes. Acceptance requires every STEP-003 feature.json behavior, deterministic race/lifecycle tests, strict typecheck and lint/format passing; no placeholder/TODO implementation or unfinished resource cleanup. Planner does not toggle passes or commit. Handoff to STEP-004 includes no-argument singleton and cache cleanup contract, frozen source snapshots with full times/coverage/errors, subscribed token provenance, latest successful balance/day/positions/rebate values, and explicit incomplete available-cash estimates.
