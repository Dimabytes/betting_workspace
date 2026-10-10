# STEP-001 plan: live A diagnostics (venue I/O, stalls, event path, book strip)

Step: `STEP-001` in `tasks/live-a-audit-fixes/feature.json` (priority 1, `passes: false`).
Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes` (branch `live-a-audit-fixes`, HEAD `6787bf3f`). Below, `CODE` means this worktree.
Fork: `../poly-maker` is frozen. Every seam below lives in esports-trader.
No Figma or design links exist for this step.

## 0. Result in one paragraph

Add one process-wide diagnostics runtime for live A. It writes versioned JSONL records to `data/trader/wallet/diag/` from a background thread. Producers never block and never raise into trading. Four channels feed it: (1) full redacted CLOB HTTP requests and responses plus logical venue calls; (2) event-loop lag, stall stack snapshots from a watchdog thread, heartbeat, SQLite, GC, CPU and RSS; (3) the inbound and outbound event path with stage and reason; (4) the book strip with the levels it walked. Records join by existing IDs (`command_id`, `core_order_id`, venue order id, `fill_key`) plus five new handles (`run`, `in_seq`, `call_id`, `strip_id`, `stall_id`). Storage rotates, compresses off the loop, keeps at most 7 days and stays under a size cap. Coverage loss is written down. This step fixes no bug and changes no trading decision.

## 1. Scope rules

- Install only for live A: `mode == "live"` and `strategy == "follow300"` in `open_wallet_host`. Wallet B (`DOTA_STRATEGY=two_sided`, `config_b`) and paper never install it. The shared hooks then cost one `None` check.
- Observe only. No change to orders, cancels, reserves, fills, ledger, checkpoints or strategy output. Existing tests must pass unchanged, except import-path updates from the `book_strip.py` move (section 3.6).
- No new dependency. Use stdlib plus the installed `httpx` 0.28.1.
- No narrating comments in new code (review rule in `WS/.feature-json.config.json`). Keep docstrings short and only where the module style already uses them.
- File size: `wallet_store.py` is 999 lines, `core_persistence.py` 984. Do not add lines to them. `session_core.py` (1109), `engine_seams.py` (1412), `match_worker.py` (1466), `wallet_host.py` (1669) are over 1k already: add only thin calls there. Put logic in new modules.
- AGENTS rules apply: action-verb function names, frozen dataclasses for multi-field values, no `dict[str, Any]` for our data, no anonymous tuples for our values, required arguments (the `DiagIds` bag with `None` defaults is the one deliberate exception).

## 2. What exists today (evidence)

| Gap | Where |
| --- | --- |
| Fork place swallows every exception and logs only `str(exc)`; a missing-id item is cut to 120 chars | `poly-maker/src/polymaker/execution/gateway.py:166-186` (`[:120]` at :183) |
| CLOB SDK uses one process-wide `httpx.Client`; we already replace it at boot | `py_clob_client_v2/http_helpers/helpers.py:24,47`; `CODE/src/trader/clob_transport.py:15` |
| `gateway._io` uses `run_in_executor`, so ContextVars do not reach the `clob-io` threads | `gateway.py:73-76` |
| Engine journal keeps raw `orders_out/user_order/user_trade` with the venue timestamp, not the receive time, and no responses | `CODE/src/trader/engine_seams.py:100-110`; fork `userstream/client.py:139-145` |
| REST BUY proof keeps no payload and no reason for a zero | `CODE/src/trader/unsettled_buy_recovery.py:73-178` |
| Terminal routing drops a proof without any record when the row is missing | `CODE/src/trader/wallet_host.py:917-940` |
| Book strip keeps no trace of walked levels or own size | `CODE/src/trader/session_core.py:242-338` |
| No loop, SQLite, GC, CPU or RSS data. The 13.2 s pause and the REST proof zeros stayed unexplained | `runs/live-a-money-audit-20261009/reports/a2-opus-exec.md:163-167`, `a2-dev-settle.md:88-91`, `a2-dev-guards.md:101-104`, `a2-grok-book.md:167-171`, `FINAL_REPORT_RU.md:261-262` |

## 3. New and moved modules

### 3.1 `src/trader/diag.py` (runtime and public API, about 200 lines)

- `DIAG_SCHEMA_VERSION = 1`.
- `DiagIds` frozen dataclass, all fields `None` by default: `session_id`, `match_id`, `token_id`, `command_id`, `core_order_id`, `venue_order_id`, `fill_key`, `in_seq`, `call_id`.
- `_SCOPE: ContextVar[DiagIds]` plus `diag_scope(ids)` context manager. It merges non-`None` fields over the current scope and resets the token on exit.
- `emit(kind, stage, *, outcome, reason, ids, payload, flush)`. It returns at once when no runtime is installed. It merges scope ids, stamps `wall_ns = time.time_ns()`, `mono_ns = time.monotonic_ns()` (the `core_now_ns` clock, so it joins `core_trace` `now_ns`) and the thread name. It calls `store.offer(record)` inside `try/except Exception` and counts failures in `emit_errors`.
- `diagnostics_active() -> bool`.
- `VenueCall` class-based context manager (class-based so it sees `CancelledError`, a `BaseException`). `__enter__` makes `call_id = f"{run8}-{n}"` from `itertools.count()`, sets the scope and emits `venue/call_start`. `__exit__` emits `venue/call_end` with `duration_ms`, the payload collected by `call.finish(...)`, and `outcome`: `ok`, `refused_closed` (`GatewayClosed`), `cancelled` (`asyncio.CancelledError`), `error` (other, with type and full redacted message). It never swallows the exception.
- `install_live_diagnostics(*, engine, settings, git_commit, secret_values)`: create `DiagStore`, start the writer thread, install the HTTP hook, the `_io` context wrapper, SQLite timing and the GC clock, emit `diag/process_start`.
- `start_loop_probes()`: called from the running loop in `WalletHost.run`; starts `LoopMonitor` and `StallWatchdog`.
- `add_secret_values(values)`: after `engine.start()`, register `gateway.creds` `api_key`, `api_secret`, `api_passphrase`.
- `close_diagnostics(timeout_s)`: stop probes, emit `diag/process_stop`, flush, join the writer, clear the runtime. Idempotent.

### 3.2 `src/trader/diag_records.py` (schema, about 250 lines)

- `DiagRecord` envelope: `v`, `run`, `seq` (writer-assigned), `kind`, `stage`, `outcome`, `reason`, `wall_ns`, `mono_ns`, `thread`, `ids`, `data`. The writer drops `None` ids.
- `kind` is a `Literal`: `diag`, `venue`, `event`, `rest_proof`, `book`, `health`.
- One frozen payload dataclass per stage (section 5). Raw venue JSON stays `object` because it is external data.
- Store `Side` and other enums as `.value`. Keep full float precision. LIVE-03 needs `1.1369e-13` visible.

### 3.3 `src/trader/diag_store.py` (settings, writer, retention, reader, about 350 lines)

- `DiagSettings` frozen dataclass in bytes, milliseconds and seconds. `load_diag_settings(path)` converts the TOML `*_mb` and `*_kb` keys to bytes. Tests build `DiagSettings` directly with byte-scale limits. The loader is strict: the exact key set, `int`/`float` types, all values positive, `segment_max_mb * 4 <= total_max_mb`, `stall_threshold_ms >= 2 * loop_tick_ms`. A bad file raises `TradingDisabled` at boot, like `read_template` (`session_config.py:52-120`).
- `DiagStore`:
  - `queue.Queue(maxsize=buffer_max_records)` plus a lock-guarded queued-body-byte counter capped by `buffer_max_mb`. `offer()` uses `put_nowait`. When the queue is full, it counts `dropped_queue_full` by kind with the first and last `wall_ns`. When the body budget is full, it keeps the record and replaces the body with `{"dropped": "buffer_bytes", "bytes": n}`. A body over `body_max_mb` is truncated with `truncated: true` and the original size.
  - The writer thread (daemon, name `diag-writer`) redacts, serializes with `json.dumps(separators=(",", ":"), default=str)`, applies the value scrub (3.4) to the whole line and appends to the active segment. It flushes every `flush_interval_ms`, and at once for records with `flush=True` (stall snapshots, `process_start`, `process_stop`).
  - Segment name: `seg-<UTC %Y%m%dT%H%M%S%fZ>-<run8>-<n:06d>.jsonl`. Rotate at `segment_max_mb` or `segment_max_age_s`. The writer thread gzips the closed segment to `.jsonl.gz` (`compresslevel=6`, `shutil.copyfileobj` in 1 MiB chunks; zlib releases the GIL) and deletes its own plain file.
  - Artifacts: a body larger than `inline_body_max_kb` goes to `artifacts/<sha[:2]>/<sha256>.json.gz`. The hash covers the redacted body. The record keeps `{"artifact": rel_path, "sha256": h, "bytes": n}`. Identical bodies (repeated `get_trades` windows) reuse the file and get an `os.utime` touch.
  - Sweep after each rotation, at startup and on each degraded retry. Step 1: delete segments and artifacts with mtime older than `retention_days`. Step 2: while total bytes exceed `total_max_mb`, delete the oldest file. Every deletion becomes a `diag/evicted` record `{path, first_wall (from name), last_wall (mtime), bytes, reason: retention|size_cap}`. The record goes to the stream and to `coverage.json`.
  - At startup, gzip any plain segment left by a crashed earlier run (another `run8`).
  - Disk failure: any `OSError` (including a root that is a file, `ENOSPC`, `EACCES`) sets state `degraded`, logs one warning per episode, drops records with counters, and retries every `writer_retry_s` after a sweep. On recovery it writes `diag/writer_gap` `{from_wall, to_wall, dropped_by_kind, body_dropped, write_errors, last_error}`.
  - `coverage.json` is rewritten atomically (tmp file plus `os.replace`) after each rotation and state change: `{updated_wall_ns, writer_state, oldest_retained_start, newest_segment, retained_bytes, evicted (last 200), drops, since_wall_ns}`. A frozen dataclass serializes it.
- `iter_diag_records(root) -> Iterator[dict[str, object]]`: segments in name order, `.jsonl.gz` and `.jsonl`. It skips only a truncated last line of the newest plain segment. Tests and investigators use it.

### 3.4 `src/trader/diag_redact.py` (about 90 lines)

- Key redaction (case-insensitive exact key): `signature`, `owner`, `apikey`, `api_key`, `secret`, `api_secret`, `passphrase`, `api_passphrase`, `authorization`, `cookie`, `set-cookie`, `private_key`, `pk`, every `poly_*` and `poly_builder_*` key. The value becomes `"[redacted]"`.
- Headers use an allowlist. Keep the values of `content-type`, `content-length`, `accept`, `accept-encoding`, `user-agent`, `date`, `server`, `cf-ray`, `x-request-id`, `retry-after`. Keep any other header name with the value `"[redacted]"`.
- Paths starting with `/auth/` (derive and create API key): replace both bodies with `{"omitted": "auth"}`.
- Value scrub: replace every registered secret value (length 6 or more) in the final JSON line. Registered values: `cfg.secrets` PK, builder key, secret and passphrase, `polygon_rpc` (an RPC URL can carry a key), then `gateway.creds` after connect.
- Exceptions: record type, full `str(exc)`, `PolyApiException.status_code` and `error_msg`, and the `__context__`/`__cause__` type and message. The writer scrubs all of them like any other text.

### 3.5 `src/trader/diag_probes.py` (about 300 lines)

- `install_http_capture(client)`: append a response hook to `helpers._http_client.event_hooks` (public httpx API). The hook calls `response.read()` (the httpx-documented pattern in hooks) and emits `venue/http`. Payload: method, path, query dict, request headers, request body bytes, status, allowlisted response headers, response body bytes, `elapsed_ms` from `response.elapsed`, `start_wall_ns = now - elapsed`. Bytes stay raw on the queue; the writer decodes, redacts and stores them. The hook never raises (`try/except Exception`, counted). Call it after `use_http1_clob_transport()` (`host_resources.py:238`) because that replaces the client. Network errors fire no hook; `_io` records them.
- `install_io_context(gateway)`: replace `gateway._io` with `context = contextvars.copy_context(); await loop.run_in_executor(gateway._pool, context.run, fn, *args)`. On `Exception` it emits `venue/io_error` with the exception chain and re-raises. On `CancelledError` it emits `venue/io_cancelled` (the thread keeps running) and re-raises. This is the only way the scope reaches the HTTP hook. The fork's place, cancel, open_orders, heartbeat and balance calls, plus our `_cancel_with_proof`, `_open_orders_with_proof`, `_pull_missed_fills` and `collect_rest_buy_proofs`, all go through `gateway._io`.
- `TimedConnection(sqlite3.Connection)`: override `execute`, `executemany`, `commit`, `__exit__` with the typeshed signatures. Time each call with `perf_counter_ns`. Mark `opened_txn = not in_transaction_before and self.in_transaction`; in WAL mode the statement that opens the write transaction carries any busy wait. Add each call to a loop-thread window aggregate keyed by `"<VERB> <table>"`. Emit `health/sqlite` when one call takes `sqlite_slow_ms` or more: op key, SQL head (60 chars, no params), `duration_ms`, `opened_txn`, `phase` (`statement` or `commit`). Python's `sqlite3` cannot split the C-level busy wait from execution. The record says so through `opened_txn`.
- `adopt_timed_connection(store)`: `commit()`, then close the fork connection (`fork state/store.py:52`) and reopen the same path (`PRAGMA database_list`) with `factory=TimedConnection`, `row_factory = sqlite3.Row` and `PRAGMA busy_timeout={BUSY_TIMEOUT_MS}`. Call it in `install_live_diagnostics` right after `Engine(cfg)`, before `WalletHost` and before `engine.start()`. Nothing caches `store._conn` before that point; callers read it per call. `WalletCatalogStore` keeps its own untimed connection.
- `LoopMonitor` (asyncio task `diag-loop`, failures logged like `_log_task_failure`):
  - Every `loop_tick_ms`, it sleeps and measures `lag_ms = actual - expected` and sets `beat_mono_ns`.
  - When `lag_ms >= loop_lag_record_ms`, it emits `health/loop_lag` with `lag_ms` and the `stall_id` the watchdog opened, if any.
  - Every `resource_interval_s`, it emits `health/loop_window`: tick count, lag p50/p95/p99/max (sort of at most about 100 samples), the SQLite window aggregate (count, total, max per op key), and the GC window (count per generation, total and max pause).
- `StallWatchdog` (daemon thread `diag-watchdog`):
  - It polls every `loop_tick_ms`. When `now - beat_mono_ns >= stall_threshold_ms`, it opens a `stall_id`.
  - It captures `sys._current_frames()[loop_thread_ident]` with `traceback.extract_stack(limit=stack_max_frames)`, plus the top 3 frames of every other thread with its name. It also records `observed_age_ms` and its own poll lag, which shows GIL starvation.
  - It emits `health/stall` with `flush=True`. It repeats every `stall_repeat_ms`, at most `stall_max_snapshots` per stall. `loop_lag` closes the stall with the total lag.
  - The snapshot does not depend on any callback in the blocked loop.
  - Keep every private fork or SDK access (`helpers._http_client`, `gateway._io`, `gateway._pool`, `sys._current_frames`) inside `diag_probes.py`, under the same file-level pragma as `clob_transport.py:3`. `sys._current_frames` is a documented CPython API.
- `ResourceSampler` (runs inside the watchdog thread every `resource_interval_s`, so it reports during a stall): `resource.getrusage(RUSAGE_SELF)` user and system CPU, `ru_nvcsw`, `ru_nivcsw`, `ru_majflt`; RSS from `/proc/self/statm` times page size, or `None` with `rss_source: "unavailable"` when the file is absent (macOS tests); cgroup v2 `/sys/fs/cgroup/cpu.stat` `nr_throttled` and `throttled_usec` when readable; `threading.active_count()`; queue depth; drop counters; writer state. Emits `health/resources`.
- `GcClock`: a `gc.callbacks` entry. It measures each collection from `start` to `stop` on any thread and feeds the window. It emits `health/gc` when one pause is `loop_lag_record_ms` or longer. `close_diagnostics` removes it.

### 3.6 `src/trader/book_strip.py` (moved strip and typed results, about 190 lines)

Move `wall_age_to_mono_ns` (`session_core.py:195`) and `_own_remaining`, `_best_foreign`, `_stripped_token_book`, `books_from_md` (`session_core.py:242-338`) out of `session_core.py`. Keep `books_from_views` and `core_books` in `session_core.py`. This takes about 110 lines out of an over-1k file and gives STEP-007/008/009 one home. Behavior stays identical.

- `OwnContribution(core_order_id, price, key, submitted_qty, filled_qty, leftover, status, accepted, accepted_ns, used, skip)`. `skip` is `""`, `"gone"`, `"not_accepted"`, `"accepted_after_snapshot"` or `"no_leftover"`. List only orders of this token and side.
- `WalkedLevel(price, size, own, left)`: every level the scan visited, in order, up to and including the selected one.
- `StrippedSide(token_index, side, levels_total, walked, own, best, none_reason)`. `best` is `BestLevel(price, size) | None`. `none_reason` is `""`, `"empty_side"` or `"all_own"`.
- `_own_remaining` returns `OwnRemaining(by_key, contributions)`. `_best_foreign` returns `ForeignScan(best, walked)`. `_stripped_token_book` returns `StrippedToken(book, bid, ask)`. `books_from_md` keeps its signature and return type. It then calls `record_book_strip(...)` (in `diag.py`) with the YES and NO `OrderBook.local_ts`, `last_update_ts`, `book_hash` and the `ts_ns` it computed.
- Update imports of `books_from_md` in `tests/test_trader_session_core.py:65`, `tests/test_trader_unsettled_buy_activation.py:46`, `tests/test_trader_live_execution.py:31`.

Strip recording rules (`record_book_strip`):

- Snapshot id = the book's `local_ts`. The fork sets it on every snapshot or delta (`orderbook.py:84,94`), so it is stable for one book state.
- `book/strip` detail: emit once per unique `(session, token_index, side, local_ts, own fingerprint)`. Emit only when the side has own contributions (used or skipped) or no best price. Fields: `strip_id` (12-hex hash of the key), token id and index, side, snapshot `{local_ts, exchange_ts, book_hash}`, `own`, `walked`, `best`, `none_reason`, `levels_total`. Keep only the last key per `(session, token_index, side)`, so memory stays bounded. This is the reusable snapshot artifact.
- `book/strip_use`: one small record per `books_from_md` call that has a detail side: `{now_ns, yes_ts_ns, no_ts_ns, strip_ids, pair: "ok" | "side_missing"}`. `ts_ns` jitters per call (`wall_age_to_mono_ns`), and `core_trace` `BookUpdate` carries that exact value. So `(session, token_index, ts_ns)` joins a strip use to its core trace event with no time window.
- Identity strips (no own order on either side and both best prices present) count in `health/loop_window` as `strip_identity` and write nothing.
- Session id comes from the scope: `core_books` (`session_core.py:1007`) wraps its call in `diag_scope(DiagIds(session_id=core.session_id))`. Two lines.

### 3.7 `config/diagnostics.toml` (new, live A only)

Read only by `install_live_diagnostics`. `read_template` and `config_b` stay untouched, so wallet B keeps its start path. Start values (step 7 sets the final ones):

```toml
retention_days = 7
total_max_mb = 3072
segment_max_mb = 64
segment_max_age_s = 3600.0
buffer_max_records = 50000
buffer_max_mb = 64
inline_body_max_kb = 32
body_max_mb = 8
flush_interval_ms = 1000
writer_retry_s = 10.0
loop_tick_ms = 100
loop_lag_record_ms = 250
stall_threshold_ms = 1000
stall_repeat_ms = 5000
stall_max_snapshots = 6
stack_max_frames = 48
sqlite_slow_ms = 50
resource_interval_s = 10.0
```

Root: `TRADER_WALLET_DIR / "diag"` (`trader/paths.py:5`), next to `engine_journal`. Every match scanner skips `wallet` (`archived_markets.py:69`, `oddin_close.py:39`, `dashboard/catalog.py:302`, `viewer/live_tape.py:256`, `archive_index/schedule.py:666`), and `scripts/sync_trader.py:62` excludes it from rsync.

### 3.8 `scripts/measure_diag_overhead.py` (bounded local measurement)

See section 7. Default 20 s per arm, about 1 minute in total. It writes JSON to stdout.

## 4. Instrumentation points (exact call sites)

All emits go through `diag.py`. In big files, add only the call or the context manager.

### 4.1 Venue I/O channel

| # | Site | Record | Notes |
| --- | --- | --- | --- |
| V1 | HTTP hook on `helpers._http_client` | `venue/http` | Every CLOB SDK request: `POST /orders`, `DELETE /orders`, `DELETE /cancel-market-orders`, `DELETE /cancel-all`, every `GET /data/orders` and `GET /data/trades` page, `GET /balance-allowance`, heartbeats, `/time`, `/auth/*` (bodies omitted). The scope gives `call_id` and session. |
| V2 | `gateway._io` wrapper | `venue/io_error`, `venue/io_cancelled` | Full exception chain. `PolyApiException("Request exception!")` keeps the httpx error in `__context__`. |
| V3 | `wallet_host._durable_place` (`:456`) | `VenueCall("place")` around the whole body: dispatch, await, `record_dispatch_results`, `note_place_result` | Outcome `ok` / `refused_closed` / `cancelled` / `error`. `call.finish(placed_venue_ids, quote_count)`. The core apply then inherits `call_id`. |
| V4 | `engine_seams._place_guarded` (`:978`) | `venue/place_parse` | Kept and dropped SELLs with `sell_drop_reason`, each raw item (`orderID`, `status`, full `errorMsg`, `makingAmount`, `takingAmount`), requested size and price per quote, placed ids, `unplaced_quotes_explained`. It runs inside the shielded inner task, so a late reply after a cancelled waiter still lands with the same `call_id`. |
| V5 | `wallet_host._durable_cancel` (`:539`) | `VenueCall("cancel")` around the whole body | `call.finish(ok, venue_ids)`. |
| V6 | `engine_seams._cancel_with_proof` (`:540`) | `venue/cancel_parse` | Terminal ids, full `not_canceled` reasons, unproven reason (`no_client` / `exception` / `unreadable_body` / `missing_ids`). |
| V7 | `install_strict_rest.cancel_asset` (`:403`), `_open_orders_with_proof` (`:566`) | `VenueCall("cancel_asset")`, `VenueCall("open_orders")` | Open orders: row count, `malformed` flag, ids. |
| V8 | `install_heartbeat_boot_grace.heartbeat` (`:525`) | `VenueCall("heartbeat")` | `ok`, `_hb_failures`, `grace_hold`. |
| V9 | `install_rest_snapshot_stamps.positions` (`:441`) | `VenueCall("positions")` | Token count and `sent_at`. The fork uses its own `httpx.AsyncClient` (`gateway.py:479-497`), so the record says `raw: "unavailable"`. |
| V10 | `install_collateral_snapshot.positions` (`:420`) | `VenueCall("collateral")` | Normalized `cache.value`. The raw `balance` field comes from V1. RISK-03 needs both. |
| V11 | `_pull_missed_fills` (`:448`) | `VenueCall("trades_backfill")` | `after`, `applied`, `confirmed`, `cash`, keys. |
| V12 | `collect_rest_buy_proofs` (`unsettled_buy_recovery.py:73`) | `VenueCall("trades_proof")` per token, plus `rest_proof/scan` | See 4.3. |

Not covered: `market_order` (dust FAK) gets V1 and V2 only, with no logical call. Chain RPC reads in `chain_balances.py` are skipped; no later step needs them.

### 4.2 Event path channel (received, parsed, matched, applied or ignored)

| # | Site | Record and reasons |
| --- | --- | --- |
| E1 | `WalletUserStream._handle` (new override in `engine_seams.py`, a copy of the fork's 9-line `_handle`) | Assign `in_seq` (loop-thread counter). `event/received` with `recv_wall_ns`, `recv_mono_ns`, byte size and the raw message (redacted: `owner` is the API key). Reasons: `malformed_json`, `non_dict_message`, `unknown_event_type`. Then dispatch inside `diag_scope(DiagIds(in_seq=n))`. This record exists before any owner lookup. |
| E2 | `WalletUserStream._on_trade` (new override; `normalize_maker_trades` directly, as `patch_engine_classes` does) | `event/parsed`: per `TradeEvent` the `fill_key`, token, side, price, size, status, venue `ts`; reason `no_maker_events` when the list is empty. When active, read `store.ledger_status(key)` before and after `self._proc.on_trade` and emit `event/applied` or `event/ignored` from `classify_trade_apply(status, before, after)` in `diag.py`: `matched_applied`, `confirmed_applied`, `confirmed_from_matched`, `failed_applied`, `duplicate`, `superseded`, `oversized_reject`, `already_confirmed`, `already_failed`, `failed_without_matched`, `mined`, `retrying`. `wallet_store.py` stays untouched. |
| E3 | `WalletUserStream._on_order` (`engine_seams.py:152`, full override) | `event/parsed`: id, `type`, `status`, side, `original_size`, `size_matched`, price, venue `timestamp`, condition; reason `unparsed_order` when `normalize_order` returns `None`. |
| E4 | `wallet_host._on_order_terminal` (`:917`) with `_open_cancelled_buy` and `_deliver_cancelled_buy` | Restructure into `_route_order_terminal(msg) -> TerminalRoute(venue_id, side, outcome, proof_qty)`, then one `event/matched` or `event/ignored` emit. Outcomes: `not_dict`, `not_buy_terminal` (today this includes a SELL `CANCELLATION`: LIVE-06 becomes visible), `already_resolved`, `no_wait_row` (LIVE-01 lost proof), `cancelled_buy_opened`, `proof_applied`, `cancel_delivered`, `no_owner`, `not_core_buy`. Same branches, same side effects. |
| E5 | `wallet_host._commit_cancel_reserves` (`:512`) and `_open_cancelled_buy` insert | `event/unsettled_row_inserted`: venue id, session, requested qty, price, source `cancel_ok` or `venue_cancel`. |
| E6 | `wallet_host._apply_unsettled_proofs` (`:997`) | One `event/unsettled_applied` per proof: `proved`, `no_wait_row`, `already_proven`, `duplicate_in_batch`; plus `resolved` ids from `resolve_settled_buys`. |
| E7 | `wallet_host._journal_fill` (`:1098`) | `event/applied` `journaled`, or `event/ignored` `late_event` (archive path) or `no_owner_no_archive`. |
| E8 | `wallet_host._consume_core_outbox` (`:1070`) | `event/ignored` `no_owner` when no worker holds the token. |
| E9 | `core_session_io.consume_core_outbox` and `_note_outbox_item` (`core_session_io.py:94,123`) | `_note_outbox_item` returns a `Literal` outcome instead of `bool`: `noted`, `fill_missing`, `event_ignored`, `token_unknown`, `merge_token_unknown`. Emit `event/core_outbox` with outbox `seq`, `fill_key`, outcome and the core trace `last_seq` after apply. |
| E10 | `core_session_io.apply_and_persist` (`:78`) | `event/core_applied` with session and `trace_seq` (`core._trace.last_seq`, or `None` when the core has no trace; the file already reads core privates). It inherits `call_id` (place and cancel) or `in_seq` (WS paths) from the scope. |
| E11 | `LiveCore.note_fill` unknown venue, `note_cancel` unmapped venue, `note_placed` extra orders (`session_core.py`) | One-line `event/matched` emits with reasons `unknown_venue`, `unmapped_venue`, `extra_orders`, next to the existing warnings. |
| E12 | `core_execution.prepare_dispatch`, `record_dispatch_results`, `persist_prepared_cancels`, `persist_cancel_outcome`, `retire_unsent` (`core_execution.py:59,81,153,214,230`) | `event/command` with stages `dispatch_started`, `result_recorded` (`venue_order_id`, outcome `accepted`/`unknown`), `cancel_dispatch_started`, `cancel_result_recorded` (`ok`), `retired_unsent`. Ids: `command_id` (`{session}:{batch}:{core_order_id}`), `core_order_id`, session, and side, price, qty. |
| E13 | `WalletHost.register_worker` (`:1143`) | `diag/session_bound`: session id, `match_id`, YES and NO tokens, game. Other records then need only `session_id`. |

Event time, receive time and apply time stay distinct. The venue `timestamp` sits in E1 and E2 payloads. `recv_*` is in E1. Each later stage has its own `mono_ns`.

### 4.3 REST BUY proof (`unsettled_buy_recovery.py`)

- Add `scan_trades(trades, venue_ids) -> TradesScan`: `sums`, `tainted`, `rows_received`, `maker_rows_ours` per venue id, `rejected` counts by reason (`trade_not_dict`, `maker_orders_not_list`, `maker_not_dict`, `order_id_missing`, `order_id_other`, `amount_invalid`), `parse_error` (`str | None`).
- `_proofs_from_trades` keeps its signature and result. It derives them from `scan_trades`, so the existing tests stay valid.
- `rest_proof/scan` per token: `TradeParams` (`asset_id`, `after`, `maker_address`), outcome `ok` / `request_error` / `not_list` / `parse_error`, and the scan. One row entry per eligible unsettled row: venue id, row qty, `created_at`, sum, and `zero_kind`. `zero_kind` is `""` when the sum is above 0. A zero sum gets `zero_empty_reply` (no trades), `zero_id_absent` (trades present, id never seen), `zero_id_present` (id seen with amount 0) or `tainted`.
- Behavior is unchanged. Today a zero still proves zero; STEP-004 decides what changes. The raw pages are in V1 under the same `call_id`.

## 5. Record schema v1 (summary)

```json
{"v":1,"run":"<uuid4 hex>","seq":123,"kind":"venue","stage":"call_end","outcome":"cancelled","reason":"",
 "wall_ns":1760000000000000000,"mono_ns":123456789,"thread":"MainThread",
 "ids":{"session_id":"0x..","call_id":"ab12cd34-17","command_id":"0x..:42:c7"},
 "data":{...stage payload...}}
```

| kind | stages |
| --- | --- |
| `diag` | `process_start` (pid, git commit, Python version, settings, hostname), `process_stop`, `session_bound`, `evicted`, `writer_gap` |
| `venue` | `http`, `io_error`, `io_cancelled`, `call_start`, `call_end`, `place_parse`, `cancel_parse` |
| `event` | `received`, `parsed`, `matched`, `applied`, `ignored`, `unsettled_row_inserted`, `unsettled_applied`, `command`, `core_outbox`, `core_applied` |
| `rest_proof` | `scan` |
| `book` | `strip`, `strip_use` |
| `health` | `loop_window`, `loop_lag`, `stall`, `sqlite`, `gc`, `resources` |

ID chains, all exact matches:

1. Outbound: core trace `PlaceOrder.order_id` → `event/command` `core_order_id`, `command_id` → same `call_id` on `venue/call_*`, `venue/http`, `venue/place_parse`, `venue/io_*` → `event/command result_recorded` `venue_order_id` → `event/core_applied` with `call_id` and `trace_seq`.
2. Inbound: `in_seq` → `event/parsed` (`fill_key` or venue id) → `event/applied` / `ignored` / `matched` → `event/core_outbox` (`fill_key`, outbox `seq`) → `event/core_applied` `trace_seq`.
3. BUY settle: venue id across E4, E5, `rest_proof/scan`, E6.
4. Book: `book/strip_use` `(session, token_index, ts_ns)` = core trace `BookUpdate.books.tokens[i].ts_ns`; `strip_ids` → `book/strip`.
5. Health: `stall_id` across `health/stall` and the closing `health/loop_lag`.

## 6. Wiring and lifecycle

1. `host_resources.open_wallet_host` (`:133`): after `engine = Engine(...)` and only for live A, `install_live_diagnostics(engine=engine, settings=load_diag_settings(BASE_DIR / "config" / "diagnostics.toml"), git_commit=..., secret_values=...)`. Order: store and writer, `adopt_timed_connection(engine.state)`, `install_io_context(engine.gateway)`, `install_http_capture(helpers._http_client)`, GC clock, `process_start`. The existing `except BaseException` cleanup also calls `close_diagnostics`.
2. `WalletHost.run` (`:1222`): after `await self.engine.start()`, call `add_secret_values(creds)` and `start_loop_probes()`. Both are no-ops when not installed.
3. `WalletHost.close` (`:1649`): `close_diagnostics(timeout_s=2.0)` after `close_engine_resources` so `cancel_all` at shutdown is recorded. Later emits drop silently.
4. Tests install through a fixture that points the root at `tmp_path`, uses tiny settings and always closes.

## 7. Choosing limits by measurement

`scripts/measure_diag_overhead.py --seconds 20` runs three arms on the same synthetic op mix inside one asyncio loop:

- Op mix:
  - `books_from_md` on a 20-level book with 0.001 and 0.01 prices and 6 own orders, mutating one level per iteration;
  - `WalletUserStream._handle` on order and trade JSON over a tmp `WalletStateStore` (MATCHED then CONFIRMED);
  - `_durable_place`-style calls through `ShutdownLatch`, a gateway with the patched `_io`, and a fake client that calls the real `http_helpers.post` against `httpx.MockTransport`;
  - one 60-trade `GET /data/trades` per second;
  - SQLite commits.
- Arm A: diagnostics off. Arm B: on, start settings. Arm C: on, `segment_max_mb = 1`, so gzip rotations happen during the run.
- Output per arm:
  - p50/p95/p99 µs per op;
  - CPU seconds (`getrusage` delta);
  - RSS (statm, or `None` on macOS) and `ru_maxrss` raw;
  - loop lag p99/max (arm C shows rotation cost);
  - raw and gzip bytes/s and records/s by stage;
  - drops.

Production volume estimate: a one-off local command, not committed. Count `orders_out`, `user_order`, `user_trade` per hour in `runs/live-a-money-audit-20261009/evidence/engine-journal-oct5-9.jsonl.gz` (2.4 MB). Count `BookUpdate` events per hour in two or three local core traces of busy maps. Multiply by the measured compressed bytes per stage. Add heartbeat (every 5 s, `poly-maker config.py:41`), reconcile (every 20 s, `config/trading.toml:10`) and backfill traffic.

Choose:
- `total_max_mb` ≥ 7 × peak-day compressed bytes × 1.5;
- `segment_max_mb` so one plain segment stays ≤ 1/4 of the cap;
- buffer limits ≥ 30 s of peak records;
- thresholds from the arm A lag distribution: `loop_lag_record_ms` above its p99.9, `stall_threshold_ms` = 1000 unless arm A shows natural lags near it.

Acceptance:
- arm B adds ≤ 1 ms p99 to every op;
- arm B adds ≤ 3% CPU;
- arm C rotation adds ≤ 20 ms loop lag p99;
- arms B and C have zero drops.

If an acceptance target fails, cut volume before you raise a limit: dedup `strip_use`, or move more bodies to artifacts. Write the measured numbers, the estimate, the final values and these limits in `progress.txt` and `config/diagnostics.toml`. VPS free disk is unknown (no ssh in this run). Write in `progress.txt` that the operator must confirm free space under `data/trader_live` before deploy.

## 8. Hard parts and edge cases

1. Context into threads: without the `_io` patch, HTTP records have no `call_id`. Test that the scope reaches the hook thread.
2. Cancel during place (RISK-02 shape): the waiter gets `CancelledError` at the latch shield (`engine_seams.py:300-314`). The inner task and the HTTP thread finish later. Expect `call_end outcome=cancelled` first, then `venue/http` and `venue/place_parse` with the same `call_id`. A crash mid-call leaves `call_start` with no `call_end`. That is the signal.
3. `GatewayClosed` before POST (`engine_seams.py:338-342`) gives `refused_closed` with no `venue/http`. STEP-002 uses this as proof of "not sent".
4. Secrets: the derive-key response carries `apiKey`, `secret`, `passphrase` before the creds are registered. Key redaction and the `/auth/` omission cover it. `owner` in WS messages and in the `POST /orders` body is the API key. L2 headers carry the key, passphrase and signature. The header allowlist covers them. The final value scrub is the last net.
5. Never block, never raise: `put_nowait` only, every hook in `try/except`, the writer owns all file I/O, and the serialization cost stays on the writer thread. Gzip runs on the writer thread; zlib releases the GIL. Measure it (arm C).
6. Memory bound: records, plus queued body bytes, plus per-body truncation. Strip dedup keeps one key per session, token and side.
7. Retention vs cap: whichever comes first. `coverage.json` shows the real retained range and every evicted range. A segment can point at an evicted artifact. The reader reports `artifact_missing` and does not crash.
8. Crash safety: flush at most every 1 s, and at once for stalls. The reader tolerates a torn last line. Startup gzips plain leftovers.
9. TimedConnection reopen: commit before close (closing rolls back an open transaction). Keep `sqlite3.Row`. Mirror the typeshed signatures so basedpyright strict accepts the overrides. `with conn:` calls the C commit and skips a Python `commit` override, so time `__exit__`.
10. Stack capture under the GIL: a C call that keeps the GIL delays the watchdog. `observed_age_ms` and the watchdog poll lag make that visible.
11. Clocks: `mono_ns` for durations and joins inside one run. `wall_ns` for humans. A new `run` after each restart.
12. Wallet B and paper: never installed. The overrides in `WalletUserStream` and the strip results run in B, but emit nothing and change nothing. Run the B tests.
13. Typing and lint: basedpyright strict, ruff `C901` (keep functions small), `PLC0415` (no nested imports), no `__init__.py`.
14. Performance of the strip refactor: it builds small lists per call. The arm A p99 for `books_from_md` stays within 10% of the `HEAD` baseline from verification step 1.

## 9. Tests

New test files (offline, deterministic, `tmp_path` roots, tiny settings):

`tests/test_trader_diag_store.py`
- Rotation by size and by age. The closed segment becomes `.jsonl.gz`, the writer thread does it, and `iter_diag_records` returns every record in order.
- 7-day expiry: files with `os.utime` 8 days back are deleted at the sweep. A `diag/evicted` record with `reason=retention` appears, and `coverage.json` lists it.
- Size cap: write past the cap with byte-scale test settings. Bytes on disk stay ≤ cap. The oldest files go first with `reason=size_cap`.
- Buffer overflow: hold the writer on an `Event`, offer 100 records with `buffer_max_records=10`, release. Expect 90 drops counted by kind, and a `writer_gap` with the time range. `offer` never blocks: time it under 5 ms per call.
- Body budget and truncation markers.
- Disk unavailable: the root is a regular file. Producers keep running, the state is `degraded`, nothing raises. Replace the file with a directory, then expect recovery and a `writer_gap` with dropped counts.
- Artifacts: a large body is stored once by sha256 and reused (one file), its mtime is touched, and a missing artifact gives `artifact_missing` in the reader.
- Settings: the committed `config/diagnostics.toml` loads. A missing key, an unknown key, a wrong type, a non-positive value or a broken invariant raises `TradingDisabled`.

`tests/test_trader_diag_redact.py`
- Header allowlist, key redaction (nested dicts and lists), `/auth/` omission, value scrub inside exception text.
- End to end: emit an HTTP record with `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_SIGNATURE` headers, a body with `owner`, `signature`, `apiKey`, `secret`, `passphrase`, a WS message with `owner`, and an exception whose text holds the secret. Close the runtime, then decompress every file under the root. No secret value appears in any byte; `[redacted]` does.

`tests/test_trader_diag_probes.py`
- Stall: start the probes with `loop_tick_ms=20`, `stall_threshold_ms=200`, then run `time.sleep(0.6)` inside a loop callback named `block_loop_for_test`. A `health/stall` record exists whose stack contains `block_loop_for_test`. Its `mono_ns` is earlier than the callback's return time, which proves the watchdog thread wrote it while the loop was blocked. Then `health/loop_lag` with the same `stall_id` follows.
- Slow SQLite: a second connection holds `BEGIN IMMEDIATE` for 300 ms in a thread while the timed store writes. Expect `health/sqlite` with `opened_txn=true` and `duration_ms` ≥ 250, and a matching window aggregate. Rows are `sqlite3.Row`, and store reads and writes work after the reopen.
- HTTP hook: a real `httpx.Client` with `MockTransport`, gzip-encoded body, inside `diag_scope(call_id=...)` on a worker thread through the patched `_io`. The record has the decoded body, the status, `elapsed_ms` and the `call_id`, and the SDK still receives the same JSON.
- `_io` error and cancel records with the exception chain.
- GC clock wiring and `health/resources` fields (`rss_source` is `"unavailable"` on macOS, a number on Linux).

`tests/test_trader_diag_replay.py`: the local replay required by the step, with chain assembly by exact ids only. It reuses `durable_place_race.py` patterns (`SimpleNamespace` host and worker, real `WalletStateStore`, `ShutdownLatch`).
Rig for scenarios 1 and 2: the fork `ExecutionGateway.place` with a fake `_client` (its `create_order` returns a dummy and makes no network call), the diag `_io` wrapper, `_place_guarded` called directly, and `ShutdownLatch.wrap_gateway`.

1. Full place failure: a fake client whose `post_orders` calls the real `http_helpers.post`, and a `MockTransport` that returns 500 with a 400-char error body. Expect `venue/call_end outcome=ok` with 0 placed (the fork swallows the error), `venue/io_error` with `status_code=500` and the full 400-char `error_msg`, `venue/http` with the full body, and `event/command` `dispatch_started` then `result_recorded outcome=unknown`. Join them only by `command_id` and `call_id`.
2. Cancel during place: cancel the waiting task, then the transport answers after 300 ms. Expect `call_end outcome=cancelled` first, then `http` and `place_parse` with the same `call_id`.
3. REST trades: `[]` gives `zero_empty_reply`; trades with other maker ids give `zero_id_absent`; `maker_orders` not a list gives `parse_error`; a `ConnectError` gives `request_error` plus `io_error` with the httpx error in its context. Each `scan` row links to the HTTP page by `call_id`.
4. Early terminal: feed a BUY `MATCHED` terminal JSON through `WalletUserStream._handle` before any unsettled row. Expect `received` (`in_seq` n), `parsed`, then `ignored reason=no_wait_row` with `proof_qty`. Insert the row through `_commit_cancel_reserves`. Expect `unsettled_row_inserted` for the same venue id. Assemble the chain by venue id and `in_seq`.
5. SELL `CANCELLATION`: expect `not_buy_terminal` with `side=SELL`, which documents LIVE-06 today.
6. Fractional levels: own BUY 0.380 × 100 accepted before the snapshot; levels 0.385 × 40, 0.384 × 25, 0.380 × 100, 0.379 × 30, 0.374 × 80. The `book/strip` record shows `walked` over all five levels with `own=100` on each cent-38 level, `best.price=0.374`, and the own contribution `key=38`. This characterizes today's LIVE-05 behavior; STEP-007 updates the expectation.
7. Core join: run `quote_cycle` with a real `LiveCore` and `CoreTrace` in `tmp_path` (reuse the builders in `tests/test_trader_live_execution.py`). Find the `BookUpdate` in `core_trace.jsonl` whose `ts_ns` equals `strip_use.yes_ts_ns`. Then apply a cancel result through `apply_and_persist` on the same core. `event/core_applied.trace_seq` equals the `seq` of the last row in `core_trace.jsonl`.

Each scenario asserts that the chain helper uses only equality on id fields. No time-window search.

Updated tests:
- Import path for `books_from_md` in the three files listed in 3.6.
- `tests/test_trader_unsettled_buy_recovery.py`: unchanged expectations. Add one case for `scan_trades` rejected-reason counts.
- `tests/test_trader_core_persistence.py` / `test_trader_session_core.py`: unchanged expectations for `consume_core_outbox` after the `Literal` return.
- `tests/test_trader_engine_seams.py`: `WalletUserStream` with a `SimpleNamespace` processor still works with diagnostics off (`test_..._connect...` at `:348`).
- `tests/test_trader_host_resources.py`: add a boot wiring test with the existing `_patch_engine_io_fakes` (`:40`). `mode="live"`, `strategy="follow300"`, and `TRADER_WALLET_DIR` pointed at `tmp_path`: the diag root holds a `process_start` record. `strategy="two_sided"`: no diag directory, and `helpers._http_client.event_hooks` is untouched.

## 10. Verification

Run everything from `CODE`. Golden tests are excluded on every run: never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Baseline before the strip move: on `HEAD`, run a one-off timing snippet (not committed) that calls `trader.session_core.books_from_md` on the section 7 book workload 20 000 times. Save p50/p99 to `WS/tasks/live-a-audit-fixes/orchestrate/work/<implementer-name>/strip-baseline.json` for edge case 14.
2. New tests:
   ```
   PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_diag_store.py tests/test_trader_diag_redact.py tests/test_trader_diag_probes.py tests/test_trader_diag_replay.py --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
   ```
3. Regression of touched live paths and B compatibility (`PYTEST_N=8` is allowed on this Mac):
   ```
   PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_wallet_host.py tests/test_trader_engine_seams.py tests/test_trader_unsettled_buy_recovery.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_session_core.py tests/test_trader_live_execution.py tests/test_trader_core_execution.py tests/test_trader_core_persistence.py tests/test_trader_core_recovery.py tests/test_trader_wallet_store.py tests/test_trader_trade_backfill.py tests/test_trader_clob_transport.py tests/test_trader_host_resources.py tests/test_trader_match_lifecycle.py tests/test_trader_session_config.py tests/test_core_trace.py tests/test_trader_two_sided_host.py tests/test_trader_two_sided_worker.py tests/test_trader_shared_budget.py tests/test_trader_pair_merge.py tests/test_trader_session_quoting.py tests/test_trader_wallet_sidecar.py tests/test_adapter_contract.py tests/test_backtest_two_sided_core.py --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
   ```
4. Typecheck: `uv run python -m basedpyright` → 0 errors.
5. Lint: `uv run python -m pre_commit run --files <every changed and new file>` → all hooks pass.
6. Measurement: `uv run python scripts/measure_diag_overhead.py --seconds 20` with the final settings. All acceptance targets from section 7 must hold. Copy the numbers into `progress.txt`.
7. Secret scan of the test roots (covered by the redaction test). Also grep the committed diff for real-looking keys: `git diff main...HEAD | grep -iE "POLY_API_KEY|passphrase\s*=\s*['\"][^'\"]{6,}"` must print nothing new beyond test fixtures with fake values.
8. The boot wiring test in `tests/test_trader_host_resources.py` (section 9) passes as part of step 3.

No live run, no VPS, no backtest, no training. The whole suite is not part of this step: `feature.json` defines "Tests pass" as the step's targeted tests, and the full regression set runs after STEP-011.

## 11. Progress log content (`tasks/live-a-audit-fixes/progress.txt`)

- Where diagnostics live (`data/trader/wallet/diag/`, `coverage.json`, `iter_diag_records`).
- Schema v1 kinds, stages and the five ID chains.
- Measured numbers per arm, the production estimate, the final settings and the reasoning.
- Known gaps:
  - positions raw body unavailable (fork `httpx.AsyncClient`);
  - `WalletCatalogStore` untimed;
  - dust `market_order` has no logical call record;
  - SQLite busy wait is folded into `opened_txn` statements;
  - VPS free disk is unconfirmed.
- Handoff:
  - STEP-002 uses `call_start`/`call_end`, `refused_closed`, `cancelled` and late `http` and `place_parse`.
  - STEP-003 uses E4 `not_buy_terminal` with `side=SELL`.
  - STEP-004 uses E4 `no_wait_row`, E5, `rest_proof/scan` `zero_kind` and E6.
  - STEP-005 adds a book-guard stage under `event` or `health`.
  - STEP-006 adds a raw/core/ledger qty-compare payload.
  - STEP-007/008/009 edit `book_strip.py`; strip records follow automatically.
  - STEP-010 adds anchor context.
  - STEP-011 reads `venue/http` `/balance-allowance` and `VenueCall("collateral")`.

## 12. Out of scope

- Any bug fix from STEP-002..011.
- Changes to `core_trace` (including its 256 MB cap), `session.jsonl`, the engine journal, the fill ledger or the checkpoints.
- Market-depth recording on every tick.
- Dashboards, external services or a query CLI.
- Edits in `../poly-maker`.
- Wallet B features.
- Deploy, restart or push.
