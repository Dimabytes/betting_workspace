# STEP-002 — Слой чтения: summarize, sources и чистые проверки

## Objective and scope

Create the reusable read layer for the live trader dashboard: the relocated stdlib summary CLI, bounded Data API retrieval with explicit completeness, read-only wallet/checkpoint snapshots, cached journal tails and match classification, reserve estimates, human-readable reasons, and pure freshness/SELL checks. STEP-003 and both screens must consume this layer rather than reimplementing its calculations.

This plan covers STEP-002 only, priority 2, the first `passes: false` step. STEP-001 passed and its review fixes are recorded in `progress.txt`; the current code HEAD is `29e3a690545babc6517484b471d6b1423453eb89`. The code working tree was clean during inspection. `plan.instructions` is empty. There are no Figma/designReference links. Read sources: feature.json including relatedSources, plan.md, progress.txt, workspace/code AGENTS.md, and the specified feature-json-create-step-plan skill.

Planning changes only this plan and the planner report. Implementation will add dashboard modules/tests and relocate the specified workspace script/documentation. It must not edit trader/strategy/fork behavior, migrate a live database, commit as part of planning, connect to production, use real accounts, or place/cancel orders. No LiveHub, sockets, Streamlit UI, theme, dependencies, launch target, game/player projections, or browser work belong to this step. The dashboard launch/tunnel documentation line is STEP-004. Preserve the existing summary CLI's historical compatibility without developing paper mode.

## Verified code and implications

| Existing source | What to reuse / account for |
| --- | --- |
| `betting_workspace/.shared-skills/vps-trader/scripts/summarize.py` | 836-line stdlib CLI. Preserve flags, day formula, maker estimate, late-fill correction, restart gate and archive summaries. Root is currently hardcoded `/root/work/esports-trader`; activity silently stops at offset 2000; positions is a single request with default size threshold. |
| `src/trader/core_persistence.py` | Public frozen checkpoint/session/binding/command/unsettled types and readers. `decode_checkpoint` handles versions 1/2/3 in memory. `get_session(conn, condition_id)` takes condition ID, not an arbitrary session ID, and already decodes; do not decode its result twice. `CoreSessionRow` omits created_at/updated_at, so copy those SQL columns separately. |
| `src/trader/core_persistence.py:reserved_buy_notional`, `unsettled_buy_notional` | Exact durable predicates: unresolved BUY place commands with dispatch_state prepared/dispatch_started/dispatched, consumed=0, empty/null outcome; unsettled notional subtracts booked BUY MATCHED/CONFIRMED by maker_order_id/venue_id. FAILED/SUPERSEDED do not subtract. |
| `src/trader/session_budget.py`, `src/strategy/budget.py` | Trader combines core remaining BUY notional, durable command reserve, unsettled reserve and orphan store BUYs; remaining notional floors at zero and excludes gone. Positions' cost belongs to held exposure, not cash reservation. |
| `src/trader/core_session_io.py` | Checkpoints/bindings are persisted together; revision is `next_order_seq`, not a counter guaranteed to change on every fill/status change. Cache invalidation must include updated_at, not revision alone. Checkpoints are structural saved state, not current venue truth. |
| `src/trader/wallet_store.py` | fill_ledger is durable; fill_outbox has monotonic seq and lowercase matched/confirmed/failed events. WalletStateStore constructor writes schemas/PRAGMAs and restores memory: do not instantiate it from dashboard runtime. |
| `../poly-maker/src/polymaker/state/store.py` (read only) | `positions` has updated_ts. `order_log` exists, but remove_order, clear_orders and REST replacement can mutate memory without mirroring removals. It cannot establish the complete current orphan order set. |
| `src/viewer/live_tape.py:list_live_matches` | Returns historically live-traded maps including completed ones and scans complete journals. Reuse only on a cached slow catalog refresh (60 s), never in the fast snapshot/tail path. It can reject incomplete metadata; expose such omissions rather than dropping unmatched database risk. |
| `src/viewer/archive_read.py`, `src/shared/utils/jsonl_io.py` | Tolerant JSON and plain/gzip readers already exist. Reuse for archive reading in sources; standalone summarize must not import project packages to achieve the same basic gzip compatibility. |
| `src/trader/collector_sidecars.py:scan_sidecars` | Returns frozen validated sidecars, prefiltered to mtime within 2 h. Apply map_winner and per-game filtering; retain scan coverage/errors. A missing directory is not evidence that there are no other markets. |
| `src/trader/archive_paths.py`, `src/trader/paths.py` | Cleanup proof is schema 1 with matching match_id/condition_id, not bare file existence. `own_execution_cleanup` uses the trader's global root: sources must validate the supplied archive's marker locally with the same contract. |
| STEP-001 fields in archive_types/session_journal | New signal has game_snapshot, model_evaluated/raw_delta, feed_source, feed_received_at_utc and recorded_at_utc. All six are optional when reading old records. The board receipt stays that of the accepted table. |
| `src/trader/core_trace_codec.py:decode_header_row` | Saved trace header exposes policy, limits and freshness. Read a bounded header once per archive/run for known thresholds/min size; no trace replay or live model loading. |
| `.shared-skills/vps-trader/log-map.md`, `session_types.py`, `strategy/types.py` | Log-map supplies human descriptions but is behind current enums and has an overly strong canceling/wedged claim. Translate the union of documented/historical labels and current labels; current warning requires observation for >30 s. |
| `.pre-commit-config.yaml`, `pyrightconfig.json` | Namespace packages only: no __init__.py. Ruff forbids future annotations. basedpyright is strict and whole-project. New code must not use broad Any/suppression scaffolding. |

## Files and dependency boundaries

Add `src/dashboard/summarize.py`, `src/dashboard/sources.py`, `src/dashboard/diagnostics.py` and `tests/test_dashboard.py`. Use existing trader types where possible; a small `src/dashboard/types.py` is justified only for frozen shared dashboard data contracts that would otherwise create circular imports. Keep I/O in sources/summarize, calculations in named pure functions, and observation memory in a small explicit tracker. No generic rules engine, repository abstraction, background task framework or universal JSON codec.

The standalone summarize file must import only stdlib. Its DTOs (activity page/result, positions page/result, day fold, rebate result, archive summary as needed) also remain stdlib. sources may import summarize and existing trader/viewer readers; summarize must not import sources, msgspec, polymaker, pandas or UI packages. Importing any dashboard module must not open a database, enumerate live archives, run Docker, read credentials or perform HTTP.

Represent owned multi-field results as frozen named dataclasses and tuple/frozenset collections, with explicit optional fields for unknown values. Use TypedDict only at an external JSON boundary when useful. Do not introduce dict[str, Any] or unnamed tuples as dashboard contracts. Keep all new function parameters explicit and required; package/runtime paths and current time are inputs to tests and pure calculations.

## Implementation sequence

### 1. Move summarize without changing accounting

1. Relocate the source into `src/dashboard/summarize.py`, then adapt it in place. Compute `ESPORTS_TRADER = Path(__file__).resolve().parents[2]` (dashboard -> src -> repo). Preserve live/legacy archive and wallet discovery and existing CLI flags: default listing, --today, --live, --game, --match, --wallet, --rebate, --restart-check, --self-check. Preserve historical paper reads already provided by the CLI; dashboard sources target the live process only.
2. Keep the formula unchanged: Berlin calendar day cash = -BUY + SELL + REDEEM + paid MAKER_REBATE; open mark = sum(size * curPrice); displayed day number = cash + open mark. Do not replace it with wallet_day, ledger net cash, true day equity-change, closed-position PnL or Telegram session sums. Match net remains realized + imv + estimated rebate when both components are known. Late_fill updates end cash/positions; absent post-close marks remain unknown unless flat.
3. Preserve maker estimate `0.15 * 0.05 * size * price * (1-price)` for maker fills, paid rebate separate, and accrued estimate since the newest proven payout. Never invent a cut when payout is unknown. Keep the existing restart gate semantics as a CLI compatibility check, not a dashboard action.
4. Type relocated owned results sufficiently for strict checks. Remove future-annotations import per repository rules. Avoid importing project DTOs just to satisfy typing. Use stdlib typing constructs compatible with the system Python used for the standalone CLI; do not introduce Python-3.13-only runtime syntax without confirming that deployment interpreter supports it.
5. Handle session.jsonl.gz with gzip in the standalone script and preserve tolerant old/mixed archives. The move must not make compressed historical maps disappear from accrued rebate or map summaries. No sys.path manipulation or dependencies needed for `python3 /.../src/dashboard/summarize.py` from any working directory.

### 2. Return bounded API results with independent completeness

Use named results rather than bare fetched lists. An activity result carries immutable rows, end/as-of, started_at/completed_at, page count, day_complete, payout_search_complete, newest payout (optional), stop reason and error (optional). A positions result carries rows and equivalent traversal metadata/completeness. A day result is authoritative for publication only when day_complete and positions_complete are true and neither required fetch failed. Rebate search completeness is independent.

Current API documentation was resolved through Context7 `/websites/polymarket_api-reference`, then checked against the official pages because indexed examples mix route versions. Keep the specified existing `/activity` and `/positions` routes; no v2 migration in this step. Activity accepts limit <=500 and offset <=5000 with TIMESTAMP/DESC and a fixed end; positions accepts limit <=500, offset <=10000, sizeThreshold >=0 and includeArchived. [Activity reference](https://docs.polymarket.com/api-reference/core/get-user-activity), [positions reference](https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user).

Concrete bounded default policy: page size 500, activity offsets 0..5000 inclusive (at most 11 requests), positions offsets 0..10000 inclusive (at most 21 requests), 10 s maximum per HTTP request and 60 s monotonic deadline for the whole day retrieval. Inject reduced page size/page budgets/time into deterministic tests. Deadline remaining must also cap the next request timeout. No retry loop in summarize; 429/error returns diagnostic failure for STEP-003's future backoff. Never issue an offset beyond the documented maximum.

Activity traversal:

- Capture end = current epoch second exactly once, before page 1; keep end, start and sorting unchanged on every offset. Use start=1 when searching account history rather than relying on the API's implicit recent-history window. Calculate local midnight and next midnight in Europe/Berlin, then convert to UTC; do not subtract a fixed 86400 s across DST.
- Validate response is a list of objects with finite required timestamps and finite amounts used in the fold; missing/malformed money is not a valid zero. Validate descending timestamp order across pages and range <=end. Avoid deduplication by timestamp or transactionHash: legitimate trades/redemptions can share either. Full repeated pages/nonprogress or invalid ordering make traversal incomplete; report evidence rather than silently folding twice.
- Prove day_complete once a validated descending row lies strictly before the target day's start, or after validated exhaustion (empty/short page). Rows exactly at midnight are in the day; process all equal-second rows, including those split across pages. If a boundary row is already in a full page, the day may be complete without requesting deeper history.
- Newest MAKER_REBATE in validated descending history proves its cut. Continue after the day boundary if payout has not been found, within bounds. Validated exhaustion with start=1 proves no payout; cap/deadline before finding it means payout search unknown, without invalidating an otherwise complete day.
- A normal well-formed short page is evidence of exhaustion; a truncated traversal, invalid payload, deadline or capped full final page is incomplete. Hitting the last allowed full page is not end of history. HTTP failure on any attempted page fails that refresh; do not promote partial new rows even if they happen to sum plausibly.

Positions traversal:

- Explicit user, sizeThreshold=0, includeArchived=true, limit/offset and fixed chosen sorting on every page. Do not filter to redeemable-only or currently trading conditions. Choose TITLE/ASC to avoid paging primarily on changing token size; it is still not an atomic portfolio snapshot.
- Keep sub-share dust and redeemable/archived residues; preserve asset, conditionId, size, curPrice/mark and redeemable metadata for downstream display. Validate asset identity and all money used by the fold; reject bool-as-number/nonfinite values. A valid empty full result is legitimate zero open inventory. Error objects and malformed rows are not empty positions.
- Exhaustion requires a valid short/empty final page. A full page at the last legal offset, page/time limit or repeated asset/page conflict means incomplete. Do not sum duplicate assets twice or silently drop them and declare completeness. API traversal_complete means pages exhausted, not simultaneity with SQLite or a guarantee that exchange balances reflected all fills.

Expose a single synchronous day-fetch/fold entry point usable by CLI and STEP-003. It returns success only for a complete candidate, or an explicit incomplete/error result with no fresh authoritative number. Retaining the previous full result and updating its failure/age metadata is STEP-003's job; provide a small pure publication/selection helper if required to test this contract here. Never mix new partial activity with older positions. CLI prints `polymarket_today n/a` or clearly `incomplete` plus reason; it must not print the usual exact pnl line for an incomplete/error candidate. --rebate similarly distinguishes known absence from incomplete search. Preserve successful output labels relied on by the skill. No live funder/API calls in verification.

### 3. Read a coherent SQLite snapshot without opening a writer

Open the supplied wallet path as `sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=<short finite timeout>)`, row_factory sqlite3.Row. mode=ro applies to the live WAL database; do not use immutable=1 or any journal_mode/migration/DDL pragma. Missing database/schema and unsupported checkpoints return unavailable/partial diagnostics, not a new database or a fresh empty wallet.

For related data use explicit BEGIN/COMMIT read transaction while copying required SELECT results, then close cursors and connection in finally. Decode checkpoints and fold reserves after copying/closing. Reuse public decode_checkpoint; either use existing read helpers for their minimal decoding or build a small dashboard core metadata DTO over copied rows rather than importing private row mappers. Do not instantiate WalletStateStore/LiveCore, restore a strategy state, replay a trace or run model code.

Copy these sources with source time/coverage:

- core_sessions: session_id, condition_id, game, tokens, yes_is_radiant, revision, updated_at/created_at, last_outbox_seq, raw checkpoint/schema metadata;
- core_commands and core_order_bindings, including terminal bindings for venue deduplication;
- positions (size, avg_price, updated_ts), token_cid and wallet_identity funder;
- active fill_ledger rows and needed maker_order_id aggregates, plus bounded new fill_outbox rows with seq > after_seq and corresponding ledger fields including failed events;
- unresolved unsettled_buys including created_at, price, qty, proof/resolution flags, and remaining booked-adjusted reserve;
- current max(fill_outbox.seq) in that same read snapshot for the hub's initial event boundary. Do not filter by acked: trader acknowledgments do not consume the dashboard's independent cursor.

Provide a cheap core stamp read and cached decoded checkpoints keyed by session_id + updated_at + revision. Handle a changed/deleted session set. Do not require revision to increment for freshness. If a stamp/payload races between cheap poll and full read, use the stamp from the actual payload snapshot. Keep full initial ledger/history reads off the 0.5 s loop: request only needed aggregates/recent events and paginate bounded outbox reads. If bounded event results have more pages, expose the next cursor and incompleteness rather than advancing over unseen seq. Never mutate acked/cursors in trader tables.

Consistent DB cuts do not make separately buffered files coherent with the database; retain each source's timestamp and avoid declaring inventory divergence a bug from mismatched cuts. Unsupported/corrupt checkpoint risk makes the reserve incomplete; do not replace it with an apparently empty core.

### 4. Calculate reserve components once per logical BUY

Publish a frozen reserve breakdown: core_buy, command_buy, unsettled_buy, observed_orphan_buy if evidence supports it, total_known, per-session/condition breakdown and completeness/limitations. Keep quantities/venue IDs and component provenance available for details. Available cash is successful collateral balance minus known reserved notional; without a balance it is unknown, never zero. Held cost/account/map capacity are separate fields; do not subtract held shares as pending cash.

- Core BUY: max(0, submitted_qty-filled_qty)*price for non-gone orders of cores supported by nonterminal session evidence. SELL contributes zero. Include pending/canceling/unknown as commitments, not only live. Exclude historical finished/cleanup-proven core checkpoints from current core reserve. A stale unterminated session is uncertain, not automatically retired: retain a conservative known commitment with incomplete/current-ownership note rather than silently freeing it.
- Command BUY: use the same predicate as reserved_buy_notional. known_not_sent, consumed, accepted/rejected/timeout/canceled outcomes and cancel commands are not additional open place reserve. Copy/reuse the public aggregate inside the read transaction and retain qualifying rows for attribution; pure post-read calculation must agree for nonoverlapping cases.
- Reconcile overlap by `(session_id, core_order_id)` and binding venue, not token/price. A saved core order and an unfinished place for that same logical BUY are one commitment. Use known checkpoint remainder when it represents that submitted order; command notional covers a place not yet represented. A contradictory price/quantity/identity must be explicitly incomplete; do not hide it with an arbitrary min/max. Check trader dispatch/persistence ordering while implementing: the dashboard must not add the same submitted size twice when disk snapshots expose both representations.
- Unsettled: preserve public unsettled_buy_notional semantics, account-wide and per session: max(0, qty-sum(booked MATCHED/CONFIRMED BUY size at venue))*price. MATCHED -> CONFIRMED is one ledger row, not two. A canceled gone BUY and its durable unsettled row count only the remaining unsettled amount. Resolved rows release it. FAILED restores previously booked reserve; SUPERSEDED does not consume it. Completed/old-map unsettled rows still reserve wallet funds.
- Orphans: read-only `order_log` can supply last-known evidence for details, but cannot prove current completeness. Do not present all nonterminal historical order_log rows as current live BUYs. Deduplicate every recoverable venue against all relevant current core mappings/commands/unsettled commitments; retain ambiguous evidence separately and mark available balance an estimate. Given current memory-only REST/removal behavior, disk-only reserve cannot normally claim complete orphan coverage. Do not fix this by changing fork/trader persistence or adding network order polling in this step.

This is the trader's component model plus explicit disk provenance/deduplication, not permission for new trading-accounting rules. Test numerical equivalence on valid lifecycle cases and report representational ambiguities. Unmatched database sessions/tokens/commands remain visible even if list_live_matches rejects an archive.

### 5. Cache bounded tails and classify map evidence

Make a bounded per-process cache keyed by resolved `(path, mtime_ns, size)`; choose explicit limits such as 128 paths, 256 KiB per uncompressed tail and 512 complete records. Read a small header separately for session_start and the trace policy/header; a growing tail will no longer contain that header. Reuse archive_read/jsonl_io where applicable. No durable incremental file-offset cursor. A one-shot seek to the last bounded bytes is a tail read, not offset-following ingestion; drop the first cut record and the last incomplete record. Ignore malformed lines/nonobjects with a coverage note. Cache parses, not mutable shared dicts; evict old versions of a path and old entries.

Use stat before/after for concurrent append/replace. If the file changes while read, do not cache the result under the later stat: keep captured source bounds, retry at most once or publish the previous known result as refreshing. Missing/replaced files and .jsonl -> .jsonl.gz transitions invalidate. Compressed closed archives require streaming once on the slow catalog path (or a cached summary), not full decompression every second. A size/record bound means absence of an older event cannot be proven from a tail; terminal/header facts come from retained cached summaries/metadata, not just today's last lines. Late fills beyond session_end must not reactivate a finished map.

Tail view keeps latest signal, quote and relevant errors/events with optional recorded/received timestamps and exact source path. Feed snapshot freshness uses signal.feed_received_at_utc, decision freshness uses recorded_at_utc, never journal mtime. On old records show unknown exact ages/delta. `model_evaluated=false` means current delta not calculated; never search backward for a model delta and attach it to this decision.

Classification priority:

1. Completed when durable session_end is found anywhere in cached slow summary, match.json has a nonnull final object even winner:null, or a schema-valid cleanup proof matches this match/CID. Empty/corrupt/mismatched marker does not prove completion. Completion wins over newer late-fill/raw-tail writes.
2. Active by latest evidence when no terminal proof and recent session/feed evidence exists. Return source/time and distinguish evidence that a file is receiving records from freshness of the accepted model table. A raw frame or board reaction must not rejuvenate model-table age. If source time is unavailable, mtime can only support a labeled archive-write observation; it is not a decision timestamp or proof the process is alive.
3. Feed lost/session stale when no terminal proof and writes/evidence stopped. Retain legacy 900 s archive-quiet threshold for coarse session classification/CLI compatibility, as an explicit named input. Model feed staleness is a separate tighter rule from the saved session trace freshness, if available; do not confuse the two thresholds or import current TOML as session parameters. Paused snapshots remain distinguishable from a dead process.

Build catalog (60 s cached list_live_matches plus metadata/summary) independently from fast tails/core snapshots. Supply condition IDs, token mapping, slug, game, terminal evidence and source ages for later screens. Closed-today grouping uses known closure evidence time (session_end/final), not just joined_at; do not fabricate closure time when absent. Preserve old completed maps with local/API residue or durable reserve in an independent remainder set for STEP-004; all API residue numbers carry their API freshness.

### 6. Sidecars and bounded diagnostic log input

Call scan_sidecars on explicitly supplied per-game collector archive roots and filter market_kind=map_winner. Root paths must be host paths, not Docker's /archive/dota or /archive/lol unless actually mounted there; project compose.yaml records /var/lib/polymarket-{dota,lol}-archive on the host. Keep paths injectable and avoid reading private .env values while planning/testing.

Subtract only condition IDs already in the active catalog; stale/completed maps remain eligible for the nontrading list. Pick latest timestamped `live-paper skip:` by exact cid and game/run scope where known, never prefix CID matching. Retain reason, source line/time and 'last known' label. Fallback to explicit sidecar closed/inactive/not accepting/book-disabled flags or 'причина неизвестна'; never invent 'ждём фид'. A sidecar disappearing from the 2-hour scan is a coverage limitation, not proof of closure.

sources supplies a bounded Docker-log read/parser for STEP-003 to call outside its socket loop: explicit compose file/root, live service, --since 3h, --tail bounded (e.g. 2000), --timestamps, no follow, finite timeout and bounded captured bytes. No subprocess on import or pure calculations; mock it in tests and do not invoke Docker during planning. Carry read failure/coverage. Keep current container identity/start time as input/provenance for future health checks; a risk_halt/HALTED before the current start is historical. Without proven run identity/current evidence, say 'последнее известное состояние; текущее неизвестно'. Do not infer process health from archive presence.

### 7. Pure freshness, reason and exit diagnostics

In diagnostics.py use small explicit functions with now and source metadata as inputs; no I/O and no notifier. Return value-independent state plus age/reason/coverage so STEP-004 can implement render_fresh.

Freshness decision order:

| Condition | State / contract |
| --- | --- |
| No successful complete prior value / token never had a full book | Нет данных; successful numeric 0 is not missing. |
| Prior value exists and last attempt failed, required coverage is incomplete, age exceeds threshold, WS disconnected or token awaiting post-reconnect book | Устарел; error/age wins over pending animation; retain previous value and its successful source time. |
| Prior value valid and request queued/running or event seq is newer than covered boundary | Обновляется; retain value. |
| Complete value within threshold, no pending uncovered change; book connected and ready for this token | Свежий. |

Balance threshold 120 s; complete day PnL threshold 600 s. At equality still within threshold; use `age > threshold` for stale. Quiet connected books with a post-reconnect token snapshot remain fresh even if no level change for a long time; last-change age is separately displayed. Distinguish token ever initialized from initialized in current connection generation. Expose request_started_at, response_at and covered/uncovered seq as inputs, but event coalescing, MATCHED/CONFIRMED dedup, FAILED scheduling and background jobs are STEP-003. Failed first request stays no data with error text. A new incomplete candidate cannot reset the age of an old complete value.

SELL observation is keyed by session/run identity + token + order_id + status, with first observed monotonic time. Check saved checkpoint orders, not signal.exit_state. A held nonzero position and the same occupying pending/canceling/unknown SELL observed continuously for strictly >30 s produces 'возможное зависание выхода' with order/venue/status/source evidence. At first dashboard observation, historical transition age is unknown; updated_at, placed time, trace monotonic offsets are not the status-start clock. Changed order/status, live, disappearance, flat position, session restart/recovery-generation change or stale/missing source ends the continuous interval. Multiple browser callers should not mutate shared timers independently: expose a pure reducer over frozen observation state for the future hub to own. Do not treat lack of checkpoint changes alone as source staleness when fresh trader evidence continues.

For no SELL, return the observed fact and sellable/dust distinction based on known saved min_order_size. Dust is neutral. Saved sell_only/recovery_pending, unconfirmed_keys/pending_ownership and proven permissions/pauses can supply an explanation; BUY entry_block cannot. Unknown min size/permission/cause remains unknown. No verified bug claim solely from 'no trades recently' or absent order. gone BUY is not a stuck SELL; gone SELL is terminal and not a pending-slot warning unless current lifecycle evidence specifically establishes that it still occupies an exit.

Translate log-map reason/entry/skip labels with a safe raw-code fallback. Keep ordinary min_delta/no_edge/cutoff/position_open/pre_horn/pause/dust neutral. Format min_delta as `|Δ| 0.011 < порога входа 0.02` only when this signal has model_evaluated=true, finite raw_delta and a known applicable saved threshold. Saved policy min_abs_delta/exit_abs_delta can involve hysteresis; if the active floor is not proven or numbers contradict the inequality, show values and label the policy floor as entry threshold without asserting a violated comparison. Do not reconstruct gate/model state to fill that gap. Older missing fields render unknown, not zero. Keep historical outside_window/history_gap labels even if absent from current enum; map all current EntryBlock cases (including ownership_unresolved, account_cap, winding_down etc.).

## Focused verification

Implementation adds only `tests/test_dashboard.py` unless a directly reused existing reader must be changed. Use tmp_path SQLite and archive files, upsert_session/upsert_binding/upsert_command/insert_unsettled_buy, and existing snapshot/state factories. Schema setup/write helpers are allowed only on the temporary writer fixture before opening the read-only dashboard reader. No fixture opens the real wallet or uses network; fake JSON fetches/clock/subprocess results cover integration. Keep tests meaningful to the observable contracts.

| Test group | Required cases |
| --- | --- |
| Read-only SQLite | Missing path never creates DB; write attempted through dashboard connection fails; writer can commit while reader exists, read cut is coherent, connection/transaction closed before fold callback; required tables included; updated_at changes invalidate even with unchanged revision; schema/corrupt checkpoint degrades explicitly. |
| Fill cursor | Initial max seq taken with initial state; subsequent seq > boundary including acked and failed rows; bounded pages do not skip unseen events; FAILED/SUPERSEDED excluded from active fill sums while failed event survives for hub. |
| Reserve | Partial BUY 40 submitted/10 filled at .50 =>15; overfill=>0; live SELL=>0; command-only prepared 10 at .50=>5; overlap of same core/command counted once; sent/unsent/outcome/consumed predicates; same price/token different order identities count separately. |
| Unsettled | gone BUY 8 at .50 plus booked 3 =>2.50 durable reserve, not4+2.50; another live 40 at .50 => combined22.50; MATCHED->CONFIRMED leaves reserve unchanged; FAILED restores booked portion; proven/resolved transitions; old completed maps still reserve; orphan evidence and unsupported core make completeness false. |
| Tails/catalog | Exact path/mtime_ns/size reuse; changed file invalidation; torn/oversized last line, nonobject, missing file, concurrent append/replace, bounded eviction; gzip transition; header outside tail; old signal missing fields; board newer decision with old feed receipt; final winner:null, session_end followed by late fills, valid/mismatched/corrupt cleanup, stale unterminated core. |
| Sidecars/logs | map_winner/per-game filtering; active CID subtraction; timestamped newest skip for exact cid; explicit flag fallback; no skip=>unknown; source failure/scan limits distinct from empty; halt before restart remains historical. |
| API paging | Every URL has expected sort/end/offset and positions sizeThreshold=0/includeArchived=true; full+short/empty pages; all-small residual positions; Berlin midnight exactly and equal timestamps across page boundary; DST day; new rows >fixed end rejected/excluded by contract; independent day/payout completeness. |
| API failure/bounds | Full final legal offset incomplete unless day+payout facts already proven; reduced request budget/deadline; repeated page/asset and invalid amounts; nonlist JSON/429/timeout/second-page failure; no fresh partial fold published, previous full value/time preserved; true complete empty inventory can be zero. |
| Freshness | 0 vs None; 120/600 boundary and above; pending while fresh, error/age priority; incomplete candidate doesn't renew last-good timestamp; connected quiet book; per-token waiting after reconnect; first fetch failure. |
| SELL/reasons | 29/30/>30 s; first observation/restart unknown age; same order continuously outside live; order/status switch, live, flat/disappearance/stale reset; dust/min_delta neutral; missing SELL unknown cause; min_delta exact text with known .011/.02 and safe fallback for missing/stale delta/threshold or unknown hysteresis. |
| CLI parity/self-check | Existing day fold 50.00 BUY/52.08 REDEEM/.20 rebate +4*.30 open produces cash2.28/pnl3.48; maker/nonmaker, late-fill and accrued cut; CLI flags/labels preserved; root independent of cwd; --self-check tests boundaries/completeness without data/API access. |

Extend --self-check with deterministic stdlib fake pages and a TemporaryDirectory archive. Existing `check_rebate_cut` currently scans real project archives despite being a self-check: isolate it to temporary/explicit fixture roots so system CLI validation never touches real maps. It must check midnight/equal-second/full-cap/valid-empty/invalid-page, fixed end and positions dust, and print success only after every assertion. System invocation must work without installed project dependencies (use `python3 -I <absolute file> --self-check` to prove that).

Commands for the implementer, run in esports-trader with no live map and without full make test:

```bash
PYTHONPATH=src:scripts uv run python -m pytest tests/test_dashboard.py -q
uv run python -m ruff check src/dashboard tests/test_dashboard.py
uv run python -m ruff format --check src/dashboard tests/test_dashboard.py
uv run python -m basedpyright
python3 -I src/dashboard/summarize.py --self-check
python3 -I src/dashboard/summarize.py --help
```

The system-python commands are the explicit standalone CLI exception; all other project Python uses uv run. basedpyright is the repository's configured whole-project quality check, not a full test run. Run only this step's added/touched tests; never tests/test_extraction_oracle.py, tests/test_follow300_replay.py or full make test. No browser/curl smoke is needed for a non-UI step; fake transport exercises request contracts without accounts. No SSH/VPS/tmux/restarts/RSS work.

## Relocation/documentation acceptance

After the new CLI and targeted tests pass, implementation removes only the explicitly authorized old `.shared-skills/vps-trader/scripts/summarize.py` and updates every executable invocation in SKILL.md/log-map.md to `python3 /root/work/esports-trader/src/dashboard/summarize.py ...`. The log-map currently has shorthand references rather than a full old executable path: make its summarize invocation examples/reference actionable without changing core_state.py commands. Keep `.agents/.claude/.cursor` symlinks untouched; edit the canonical .shared-skills files once. Do not bulk-rewrite unrelated workspace/history files.

Check with `rg -n 'summarize\.py' .shared-skills/vps-trader/SKILL.md .shared-skills/vps-trader/log-map.md` that executable commands use the new path, and `test ! -e .shared-skills/vps-trader/scripts/summarize.py`. Do not add the STEP-004 dashboard launch line early. Inspect both repos' diffs: allowed code changes are dashboard/tests only; workspace changes are the named summary removal/documentation updates plus the orchestrator's own progress files if authorized at implementation time. No poly-maker/trader strategy or monetary schema edits.

## Completion and handoff

STEP-002 is complete when targeted tests/self-check, strict typecheck and changed-file lint/format pass, the one canonical stdlib CLI and documentation paths exist, all read-only/unknown/completeness contracts are explicit, and no trader/core behavior was changed. The planner does not toggle passes or append implementation progress.

STEP-003 receives frozen wallet/core/reserve/fill-boundary/catalog/tail snapshots, an independent fill cursor interface, synchronous bounded day retrieval, pure freshness/observation helpers and explicit coverage/errors. It owns scheduling, event coalescing, prior-success retention and the single timer owner. STEP-004 renders values/states and the four map groups. STEP-005 owns feed player/scoreboard projections; STEP-006 builds the trading page. Exact orphan coverage remains explicitly incomplete with current disk sources; this is a supported estimate, not a reason to alter the frozen fork or trading persistence.
