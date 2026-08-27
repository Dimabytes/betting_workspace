# US-005 Implementation Plan

Story: **US-005** — «Резолв из _pick_and_run, ретрай и Telegram bind (observe целиком)»

Repo: `/root/work/dota_2_model` on `main`. Observe slice only: start the public resolve task, one `IN_PROGRESS` retry, `match.json` kalshi update, Telegram bind line. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** add Kalshi orderbook / prior / execution / `kalshi.db` / auth WS (US-006+). Do **not** extend `ExecutionMode`. Do **not** import `kalshi` outside `kalshi_client.py`.

---

## Binding decisions (feature.json / overlay / US-001..004 learnings)

Cross-ref `current-task/feature.json` FR-1, FR-14, US-005 `changes`, Resolved Questions, Non-Goals, and `docs/plans/kalshi-overlay.md` §§ When to search, Retry, Journal and alerts.

1. **Resolve starts in `WalletHost._pick_and_run`, not in `MarketDiscovery.discover()`.** After `select_feed` returns a live feed and `announced=True`, before `MatchWorker(...)`. Identity is already frozen (`DiscoveredMatch` names + map + kind). No usable feed → return as today (`waiting_for_feed`); do **not** start Kalshi HTTP for a map we cannot join yet.
2. **Do not await the resolve task.** `asyncio.create_task(resolve_kalshi_match(handoff, cache))`, pass the `Task` into `MatchWorker`, then `await worker.run()`. Waiting in `_pick_and_run` would delay `feed.ticks()`, the raw Steam/GRID archive (archive-first *inside* `ticks()`), first `match.json`, and PM attach.
3. **`KALSHI_TRADING=off`: zero Kalshi HTTP, zero extra tasks, `kalshi.reason=off`.** `observe` / `paper` / `live` all run the public matcher. This story does not open auth WS, prior, or execution even in paper/live (US-006 / US-007 / US-013).
4. **Load Kalshi settings once on WalletHost boot.** US-004 left a ponytail: per-first-tick `start_document_kalshi_reason()`. Replace that call. `open_wallet_host` calls `load_kalshi_settings()` once (AGENTS.md: load once, pass the result). paper/live without keys fail process start (`TradingDisabled`) — that is the US-001 start gate, first wired here. `ExecutionMode` / `LIVE_TRADING` stay untouched.
5. **One host-owned `KalshiOpenMarketCache`.** US-003: not a module global, not on `KalshiRestClient`. Construct with `open_kalshi_rest_client(demo=False)` (production public REST; demo is US-014 smoke). Pass the same cache into every `resolve_kalshi_match`. Worker retry uses `self._host.kalshi_cache`.
6. **First `write_match_start` does not wait for matching.** First-event branch writes schema 4 immediately: `pending` if a resolve task was passed, `off` if not. Resume still does not rewrite (US-004). When resolve finishes, `update_kalshi_binding` replaces only `kalshi` on an unfinished schema-4 file.
7. **Retry is exactly one, and only for `none`.** Overlay: fail-closed `ambiguous` / `off_grid` / `error` do not get a second search. Hook A: first event is already `MatchPhase.IN_PROGRESS` → start the retry in the first-event branch **before** that branch's `continue`. Hook B: first resolve completes later and the latest observed phase is already `IN_PROGRESS` → retry immediately (do not wait for the next feed tick). Then stop. Never poll Kalshi HTTP for the rest of the game. Checking `task.done()` / awaiting the in-flight `Task` is not API polling.
8. **`BUY_CUTOFF_SECOND` (540) discards an in-flight result.** Once a horn-clock `IN_PROGRESS` tick has `second >= 540`, write `reason=cutoff`, cancel/ignore the in-flight search, start no book/prior/execution (those modules do not exist yet — the gate is the reason + a flag so US-007+ can read it), suppress the retry. Do **not** unbind an already-persisted `matched` / `off_grid`. Draft `PRE_MATCH` clocks can read `second >= 540` (documented Steam bug); cutoff applies only when `phase is MatchPhase.IN_PROGRESS`.
9. **Telegram bind is a second line, not a journal record.** After a *final* bind (retry finished, or first result is not retryable `none`): `notify_in_background("live-paper kalshi: match <id> ticker …")` or `… ticker none reason …`. No `kalshi_bind` row in `session.jsonl` (US-011 still schema 5). Session-started stays as today.
10. **poly-maker is frozen.** Zero file changes there. No new dependency. No Docker.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `open_wallet_host` | `execution_mode()` only | also `load_kalshi_settings()`; if trading ≠ off, production public REST + `KalshiOpenMarketCache` |
| `WalletHost._pick_and_run` | announce → `MatchWorker(host, handoff, model, mode, feed, stale)` → `await worker.run()` | after announce, `create_task(resolve_kalshi_match)` (or `None`), pass the task, still `await worker.run()` only |
| `MatchWorker.__init__` | 6 args | 7th required arg `kalshi_resolve: asyncio.Task[KalshiResolveResult] \| None` |
| First-event `run` | `write_match_start(..., start_document_kalshi_reason())` then pin/journal/attach/`continue` | write pending/off from whether the task is present; do **not** await resolve; arm consumer; if first event is `IN_PROGRESS` and first result is `none`, start retry **before** `continue`; attach still not waiting on Kalshi HTTP |
| `resolve_kalshi_match` | returns `matched` / `none` / `ambiguous` / `off_grid` / `error` | unchanged; caller owns `pending` / `cutoff` / `off` |
| `update_kalshi_binding` | exists (US-004) | worker is the first production caller; maps `KalshiResolveResult` → `MatchMetaKalshi` |
| `start_document_kalshi_reason()` | worker first tick | unused by worker; keep the helper (tests + no behavior change) |
| Telegram | session started / finished / faults | + one bind line after final kalshi reason |
| Book / prior / executor | none | still none |

`feed.ticks()` already archives raw Steam/GRID **then** yields. Starting `worker.run()` without awaiting Kalshi is what keeps the archive on time. Do not move archive writes.

`_bare_host` uses `object.__new__(WalletHost)` and never runs `__init__`. Any field `_pick_and_run` reads must be set there (`kalshi_cache=None`, settings off).

Two `_IdleWorker` stand-ins in `tests/test_live_paper_wallet_host.py` (lines ~1144 and ~1270) copy the `MatchWorker` constructor. They must take the 7th argument.

---

## Requirements traceability (US-005 `changes`)

| Change | Plan |
|---|---|
| `_pick_and_run` after `announced=True`, before `MatchWorker`, start asyncio resolve when `KALSHI_TRADING != off`; do not await; pass into worker | Design §§ 1–3 |
| off: no HTTP, no tasks, `reason=off` | Design § 1, first-event write |
| First event: `write_match_start` immediately pending; later `update_kalshi_binding` | Design § 4 |
| Retry exactly one after observed `IN_PROGRESS`; first-event hook before `continue`; later completion retries immediately; no further poll | Design § 5 |
| `second >= 540`: discard in-flight as `cutoff`, no book/prior/exec, retry suppressed | Design § 6 (phase-gated) |
| Resolve does not delay `feed.ticks()`, raw archive, first `match.json`, PM attach | Design §§ 2, 4; test “slow resolve” |
| Telegram bind via `notify_in_background`; no `kalshi_bind` journal row | Design § 7 |
| Autotests: first tick vs slow resolve, pending→matched, both retry cases, cutoff, off | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `resolve_kalshi_match(discovered, cache)` | The only search. Do not wrap in a second matcher. |
| `KalshiOpenMarketCache` | One instance on `WalletHost`. Retry goes through the same cache (60s TTL may serve the second call). |
| `open_kalshi_rest_client(demo=False)` | Observe/paper/live public REST. Do not assemble hosts. Do not `from_env()`. |
| `load_kalshi_settings()` | Boot, once. Do not read PEM. |
| `write_match_start` / `update_kalshi_binding` / `kalshi_reason_from_document` / `KALSHI_CENT_TICK_SIZE` | Persistence. Do not add a second writer. |
| `_atomic_write_json` | Inside `update_kalshi_binding` already. |
| `notify_in_background` | Bind line. Format in the worker (or a 5-line helper next to `_started_message`). Do not add `notify_now`. |
| `BUY_CUTOFF_SECOND` from `shared.constants.strategy` | Same 540 as `evaluate_entry`. Import the constant; do not copy `540`. |
| `MatchPhase.IN_PROGRESS` | Retry trigger and cutoff phase gate. |
| `_prior_task` cancel in `run()` `finally` | Same pattern for the resolve/retry/watch tasks. |
| `tests/test_live_paper_kalshi_match.py` `_FakeClient` / `_market` / `_yes_pair` | Copy the tiny fakes into the new observe test file (do not import from another test module). |
| `FakeLiveFeed` / `build_event` / `build_attached_worker` / `build_discovered` | Worker tests. |

Do not call `resolve_kalshi_match` from `match_meta.py`. Do not import `kalshi` in wallet_host / match_worker.

---

## Design

### 1. Boot — `open_wallet_host` / `WalletHost`

`open_wallet_host` already calls `execution_mode()`. Next to that, once:

```python
kalshi_settings = load_kalshi_settings()
```

Call it **before** `Engine(...)` so a paper/live missing-key failure does not construct an Engine. Existing `except BaseException` already closes lock/config/engine.

If `kalshi_settings.trading == "off"`: pass `kalshi_client=None`, `kalshi_cache=None`.

If `observe` / `paper` / `live"`:

```python
kalshi_client = open_kalshi_rest_client(demo=False)
kalshi_cache = KalshiOpenMarketCache(kalshi_client)
```

If `WalletHost(...)` then raises, `aclose()` the client in the existing cleanup.

Add three required `__init__` fields (no optionals):

- `_kalshi_settings: KalshiSettings`
- `_kalshi_client: KalshiRestClient | None`
- `kalshi_cache: KalshiOpenMarketCache | None` (public: worker retry)

`_bare_host` must set `_kalshi_settings` to an off `KalshiSettings`, `_kalshi_client=None`, `kalshi_cache=None`. Otherwise `_pick_and_run` `AttributeError`s.

`teardown` (already async) `aclose`s `_kalshi_client` with `suppress(Exception)` **after** match tasks are gathered and **before** the existing `engine.gateway.cancel_all`. A Kalshi close failure must not skip PM teardown. `close()` stays sync and does not do HTTP; process exit is the pool’s last chance if teardown did not run. ponytail: no sync SDK close; upgrade when the SDK exposes one.

Do **not** create auth WS, `kalshi.db`, or a second flock (US-008/US-013).

### 2. `_pick_and_run` — start task, do not await

Current:

```python
record.feed_source = feed.source
if not record.announced:
    record.announced = True
    notify_in_background(_started_message(record.handoff, self._mode))
worker = MatchWorker(
    self, record.handoff, self._model, self._mode, feed, feed.stale_seconds
)
await worker.run()
```

After announce, before `MatchWorker`:

```python
kalshi_resolve = self._start_kalshi_resolve(record.handoff)
worker = MatchWorker(
    self, record.handoff, self._model, self._mode, feed, feed.stale_seconds, kalshi_resolve
)
await worker.run()
```

`_start_kalshi_resolve(discovered) -> asyncio.Task[KalshiResolveResult] | None`:

1. If `self._kalshi_settings.trading == "off"` or `self.kalshi_cache is None`: return `None`. No `create_task`.
2. If `match_has_start(LIVE_PAPER_DIR, discovered.match_id)`: read the document (`_read_existing_meta` is private — use a tiny public reader or `kalshi_reason_from_document` on a helper). If `kalshi_reason_from_document(document) == "off"` (schema 3, or an explicit off): return `None`. If schema 4 reason is already a **terminal** non-pending value (`matched` / `none` / `ambiguous` / `off_grid` / `cutoff` / `error`): return `None` (do not flip a bound ticker on crash-restart). If `pending`: start a new search.
3. Else (no file yet): `return asyncio.create_task(resolve_kalshi_match(discovered, self.kalshi_cache), name=f"kalshi:{discovered.match_id}")`.

Do **not** `await` that task here. Do not start resolve when `select_feed` returned `None`.

ponytail: crash between first `none` and the `IN_PROGRESS` retry loses the retry (reason is already `none`, step 2 skips). Ceiling: one in-process retry. Upgrade: persist `retry_pending` if dry-run shows restarts in that window.

Add a public `read_match_meta(match_id) -> MatchMeta | None` only if `_read_existing_meta` cannot be reused without exposing the path. Prefer exporting a one-liner `kalshi_reason_for_match(match_id) -> KalshiMetaReason | None` (`None` = no file) on `match_meta.py` so wallet_host does not open JSON by hand. Missing file is not `"off"`.

### 3. `MatchWorker` constructor

Required 7th argument, no default:

```python
kalshi_resolve: asyncio.Task[KalshiResolveResult] | None
```

Store as `self._kalshi_resolve`. `build_attached_worker` passes `None`. Lifecycle tests stay off (conftest `KALSHI_TRADING=off` plus `None` task).

Start-document reason is derived, not a second constructor arg and not an env read:

```python
kalshi_reason = "pending" if self._kalshi_resolve is not None else "off"
write_match_start(start, event, kalshi_reason)
```

Drop the `start_document_kalshi_reason()` import from `match_worker.py`. Keep the helper in `kalshi_config.py` (existing tests).

Fake host: `FakeWalletHost.__init__` sets `kalshi_cache = None`. Retry tests assign a real `KalshiOpenMarketCache(fake_client)`.

### 4. First-event branch — pending immediately, consume concurrently

Do **not** `await self._kalshi_resolve` anywhere in `run()`’s first-event branch. `feed.ticks()` already started. Order of the first-event body:

1. `write_match_start(start, event, pending|off)` — first durable `match.json`.
2. `_record_kalshi_clock(event)` — latest `second` + whether `IN_PROGRESS` has been seen + cutoff flag.
3. If cutoff now: `_apply_kalshi_cutoff()` (cancel in-flight, `update_kalshi_binding` cutoff, Telegram, no retry). Skip 4.
4. Else `_arm_kalshi_resolve()`:
   - If task is `None`: nothing.
   - If `task.done()`: `_on_kalshi_result(task.result())` immediately (covers “resolve finished before the first tick”; `match.json` now exists).
   - Else: `self._kalshi_watch = create_task(self._watch_kalshi_resolve())` — applies as soon as the task finishes, even during `_try_attach`.
5. Existing `_maybe_pin_horn` / journal / finished → `_finish_terminal` / `_try_attach`.
6. **Before `continue`:** if first event is `IN_PROGRESS` and `_on_kalshi_result` did not already start a retry, `_maybe_start_kalshi_retry()` (hook A). Starting the retry is a `create_task`; do **not** await it (that would delay PM attach).

Later ticks: `_record_kalshi_clock(event)` first. If this tick is the first `IN_PROGRESS` and a `none` is waiting for retry, start retry here (still do not await). Then existing pin / `handle_event` / finish.

`_watch_kalshi_resolve`:

```python
async def _watch_kalshi_resolve(self) -> None:
    task = self._kalshi_resolve
    if task is None:
        return
    try:
        result = await task
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("kalshi resolve failed match=%s: %s", ..., type(exc).__name__)
        result = KalshiResolveResult(reason="error", series_ticker=..., binding=None)
    self._on_kalshi_result(result)
```

`resolve_kalshi_match` already maps `KalshiRestError` → `error`. The `except Exception` is a belt for unexpected bugs so PM is not killed. `CancelledError` on cutoff/quiesce must not write `error`.

If the first event is `FINISHED`, apply cutoff-or-result **before** `_finish_terminal`. `update_kalshi_binding` raises on a finalized file; a pending left behind would stick forever.

### 5. `_on_kalshi_result` / retry

Flags on the worker (plain attributes, no new class):

- `_kalshi_in_progress: bool` — any observed `MatchPhase.IN_PROGRESS`
- `_kalshi_cutoff: bool` — `IN_PROGRESS` and `second >= BUY_CUTOFF_SECOND`
- `_kalshi_retry_used: bool`
- `_kalshi_notified: bool`
- `_kalshi_committed: bool` — a non-pending reason has been written (and is not waiting on retry)

`_on_kalshi_result(result)`:

1. If `_kalshi_cutoff`: `_apply_kalshi_cutoff()` if not already committed as cutoff. Return.
2. If `result.reason == "none"` and `_should_retry()`: `_start_kalshi_retry()`; return **without** Telegram. Optionally persist `none` now or keep `pending` until retry completes — pick **keep pending** so the operator file does not flicker `none`→`matched`. If the worker exits before `IN_PROGRESS`, `finally` commits the stored `none` and Telegrams.
3. Else: `_commit_kalshi(result)` + `_notify_kalshi_bind(...)`.

`_should_retry()`: `not _kalshi_retry_used` and `not _kalshi_cutoff` and `_kalshi_in_progress` and `self._host.kalshi_cache is not None`.

`_maybe_start_kalshi_retry()` (hook A, first-event `continue`): if the current `self._kalshi_resolve` is done, result is `none`, and `_should_retry()`, call `_start_kalshi_retry()`.

`_start_kalshi_retry()`:

```python
self._kalshi_retry_used = True
self._kalshi_resolve = asyncio.create_task(
    resolve_kalshi_match(self._discovered, self._host.kalshi_cache)
)
self._kalshi_watch = asyncio.create_task(self._watch_kalshi_resolve())
```

Do not await. The watcher’s `_on_kalshi_result` for the retry must **not** retry again (`_kalshi_retry_used` already True). Persist whatever the second search returns (`matched` / `none` / `ambiguous` / …).

`ambiguous` / `off_grid` / `error` / `matched`: never retry.

### 6. Cutoff

`_record_kalshi_clock(event)`:

```python
if event.snapshot.phase is MatchPhase.IN_PROGRESS:
    self._kalshi_in_progress = True
    if event.snapshot.second >= BUY_CUTOFF_SECOND:
        self._kalshi_cutoff = True
```

Do **not** treat `PRE_MATCH` / `PRE_HORN` / `FINISHED` seconds as buy-cutoff. `FINISHED` still needs a committed kalshi object before finalize: if still in flight or pending, write `cutoff` (search is too late; match is over).

`_apply_kalshi_cutoff()`:

1. If `self._kalshi_resolve` is not done: `task.cancel()`.
2. `update_kalshi_binding(match_id, kalshi_null_block("cutoff", series_ticker))` with `series_ticker` from kind (`_series_ticker_from_kind` — export it or duplicate the two-branch `if` in the worker; **do not** import `kalshi_match` into `match_meta`. Prefer exporting `_series_ticker_from_kind` as `series_ticker_from_kind` from `match_meta`, already the write-path source).
3. Telegram `ticker none reason cutoff`.
4. `_kalshi_retry_used = True` so a late `none` cannot start a retry.

If `matched` is already committed, cutoff is a no-op on the document. Quoting cutoff stays US-013 / existing `evaluate_entry`.

No book, prior, or executor starts in this story; the committed `cutoff` / `_kalshi_cutoff` is the gate those stories will read.

### 7. `KalshiResolveResult` → `MatchMetaKalshi`

Mapping lives in `match_worker` (or a 20-line helper in `match_meta` that does **not** take `KalshiBinding`). `match_meta` stays off the matcher/SDK import graph.

Widen the existing private `_start_kalshi_block` into a public miss builder:

```python
def kalshi_null_block(reason: KalshiMetaReason, series_ticker: str | None) -> MatchMetaKalshi:
    """Nine-key kalshi object with bind fields null. pending/off/none/ambiguous/error/cutoff."""
```

`_start_kalshi_block("pending"|"off", ...)` becomes a one-line call. Worker uses it for `none` / `ambiguous` / `error` / `cutoff`.

`matched`: copy binding fields; `tick_size=KALSHI_CENT_TICK_SIZE` (`"0.01"` string).

`off_grid`: copy binding fields; `tick_size=None`; keep `price_level_structure`.

Never persist `price_ranges`. `yes_is_radiant` on a miss is JSON null.

`update_kalshi_binding` already no-ops an equal object and refuses schema 3 / finalized. Guard schema 3 in `_commit_kalshi`: if `kalshi_reason_from_document` would be `"off"` on the current file, skip the write (host should not have passed a task; belt).

### 8. Telegram

Reuse `notify_in_background` (already imported in `match_worker`). One line, once:

```text
live-paper kalshi: match 8944931337 ticker KXDOTA2MAP-26AUG281200PCKCPDYN-2-DYN
live-paper kalshi: match 8944931337 ticker none reason none
```

`matched` → first form (the bound ticker). Every other **final** reason → second form (`none`, `ambiguous`, `off_grid`, `cutoff`, `error`). `off_grid` keeps the ticker in `match.json` only; Telegram stays `ticker none reason off_grid` so a scan of the chat is bind-or-not. `off` mode does not send a bind line (no search).

No secrets, no PEM, no key id. Patch `match_worker.notify_in_background` in tests (import-bound name), same as lifecycle tests.

Do not write a session.jsonl row.

### 9. `run()` `finally`

Cancel `_kalshi_watch` and `_kalshi_resolve` the same way as `_prior_task`. If a `none` was held for a retry that never ran (no `IN_PROGRESS`), commit `none` + Telegram before cancel so the file is not left `pending`.

### 10. What this story does not touch

- `kalshi_client` auth / WS (US-006)
- `kalshi_prior` (US-007)
- `kalshi_store` / `kalshi.db` (US-008)
- `kalshi_session` / safe mode (US-009/010)
- `session.jsonl` schema 6 (US-011)
- `kalshi_executor` / dual `predict_fair` (US-012/013)
- Docker / operator Kalshi how-to (US-015) — only patch facts that this story makes wrong (Telegram bind line; resolve starts from `_pick_and_run`)
- `poly-maker`

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — `match_meta` miss builder + reason helper

1. Public `kalshi_null_block(reason, series_ticker)` used by `_start_kalshi_block`.
2. Public `kalshi_reason_for_match(match_id) -> KalshiMetaReason | None` (`None` if no file). Uses existing `_read_existing_meta` + `kalshi_reason_from_document`. Host skip logic needs this; do not duplicate JSON parse in `wallet_host`.
3. Export `series_ticker_from_kind` (rename/alias of `_series_ticker_from_kind`) so cutoff/miss can set series without importing `kalshi_match`.

No schema bump. No change to `update_kalshi_binding`.

### Step 2 — WalletHost boot + `_pick_and_run`

1. `open_wallet_host`: `load_kalshi_settings()`; maybe public REST + cache; pass into `WalletHost.__init__`; aclose on construct failure.
2. `_start_kalshi_resolve` as designed. `_pick_and_run` passes the task.
3. `teardown` acloses the REST client; PM `cancel_all` still always runs.
4. `_bare_host` off defaults.

### Step 3 — MatchWorker consume / retry / cutoff / Telegram

1. 7th constructor arg. Drop `start_document_kalshi_reason` at the first tick.
2. First-event order as Design § 4. Clock + arm + retry hook before `continue`. Later ticks record clock.
3. `_on_kalshi_result` / `_start_kalshi_retry` / `_apply_kalshi_cutoff` / `_commit_kalshi` / bind notify.
4. `finally` cancels Kalshi tasks; commits held `none` if needed.
5. Mapping helper `_kalshi_document(result) -> MatchMetaKalshi`.

### Step 4 — Call-site updates

1. `build_attached_worker`: pass `None`.
2. Both `_IdleWorker` classes: accept `kalshi_resolve`, `del` it; if a host observe test leaves a live Task, cancel it in `run()` so the loop does not warn.

### Step 5 — Tests

New `tests/test_live_paper_kalshi_observe.py` (table below). One host test in `test_live_paper_wallet_host.py` that `_pick_and_run` constructs the worker while a gated resolve is still waiting (task not awaited). Keep kalshi_match tests unchanged.

### Step 6 — Docs (facts only)

`docs/live-paper.md` Telegram bullet: after bind, one extra `live-paper kalshi:` line. WalletHost: resolve task starts in `_pick_and_run` after announce, concurrent with the feed. Do not write the US-015 operator section.

### Step 7 — Quality gate

See Verification. `git add` new files before `make lint-all`.

### Step 8 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: every live map searches Kalshi without stalling Steam/GRID or PM, and the operator sees the bind in Telegram + `match.json`.
- Set US-005 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (task passed not awaited; retry only `none`; cutoff is `IN_PROGRESS` not draft clock; schema-3 skip; Telegram once after final reason).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| `select_feed` is `None` | No resolve task. Same `waiting_for_feed` path. |
| Announce already True (crash restart of same record) | Still start resolve if document is missing or `pending`. No second session-started Telegram (existing `announced` guard). |
| Schema-3 unfinished resume | No task. `write_match_start` does not rewrite. No `update_kalshi_binding`. PM as today. |
| Schema-4 `pending` resume | New resolve task. First tick does not rewrite pending. Result updates kalshi. |
| Schema-4 already `matched` (restart) | No new HTTP. Leave the file. |
| Resolve finishes before first tick | Task sits `done()`. After `write_match_start`, apply immediately. |
| Slow resolve | First tick writes pending, attaches PM, continues. Watcher updates later. |
| First event already `IN_PROGRESS`, result `none` | Retry `create_task` before `continue`. Do not await. |
| First events `PRE_HORN`, result `none`, later `IN_PROGRESS` | Retry on that `IN_PROGRESS` tick. |
| Result `none` completes after `IN_PROGRESS` already seen | Retry in the watcher immediately (hook B). |
| First result `ambiguous` / `error` / `off_grid` at `IN_PROGRESS` | No retry. Persist + Telegram. |
| `IN_PROGRESS` `second >= 540` while search in flight | Cancel, persist `cutoff`, Telegram, no retry. Late `matched` result ignored. |
| Already `matched`, later second 540 | File stays `matched`. |
| `PRE_MATCH` `second=540` | Not cutoff. |
| First event `FINISHED` | Commit cutoff (or already-done result) before finalize. |
| `update_kalshi_binding` on finalized | Must not happen; apply before `_finish_terminal`. If it races, log and skip; do not crash PM. |
| Worker cancelled mid-resolve | `finally` cancels task/watch. CancelledError does not write `error`. |
| `kind is None` | `resolve_kalshi_match` returns `none` with zero HTTP. Still a task if mode ≠ off. |
| Two maps in one discovery cycle | Host cache coalesces in-flight list (US-003). |
| `KALSHI_TRADING=observe` without keys | Boot succeeds (US-001). Public REST only. |
| `KALSHI_TRADING=paper` without keys | `open_wallet_host` raises `TradingDisabled`. Tests stay `off` via conftest. |
| Duplicate `_on_kalshi_result` (done-task + watcher) | Idempotent commit; Telegram gated by `_kalshi_notified`. |

---

## Test plan

### New file: `tests/test_live_paper_kalshi_observe.py`

Drive `MatchWorker.run()` with `FakeLiveFeed`, a scripted `asyncio.Task`, and `FakeWalletHost.kalshi_cache`. Patch `LIVE_PAPER_DIR` on worker + `match_meta` (same as lifecycle). Patch `match_worker.notify_in_background` to a list. Stub `_try_attach` so PM attach is visible and Engine-free.

Reuse `build_event` / `build_discovered` / `build_attached_worker` (pass `kalshi_resolve=...`). Fake Kalshi client: gate + scripted `list_open_markets` like US-003 tests.

| Test | Setup | Expect |
|---|---|---|
| First tick does not wait for slow resolve | Resolve task blocked on `asyncio.Event`. Feed yields one `PRE_HORN` tick then hangs. `_try_attach` sets an `attached` event. | After `attached.wait()`: `match.json` exists, `kalshi.reason=="pending"`, bind fields null, `series_ticker=="KXDOTA2MAP"` (discovered kind), `_try_attach` ran, resolve gate still unset. Unblock gate → document becomes `matched`, Telegram `ticker <ticker>`. |
| pending → matched update | Same as above or a short sleep-then-result. | File was pending while blocked; after result only `kalshi` changed (`market` / `feed_source` identical). |
| Retry case A: first event already `IN_PROGRESS` | First resolve task already done with `none` (or finishes during first-event). Cache will unique-match on the next `list_open_markets`. First feed event `IN_PROGRESS` `second=10`. | `list_open_markets` called once from retry. Final reason `matched`. Telegram once (matched), not for the intermediate none. First-event attach still happened without awaiting retry HTTP (gate the retry client; attach event fires while gate is closed). |
| Retry case B: resolve completes later, `IN_PROGRESS` already seen | First event `PRE_HORN`. Resolve hangs. Second event `IN_PROGRESS`. Then complete first search with `none`. Cache unique-matches on retry. **No third feed event.** | Retry starts from the watcher (not from a later tick). Final `matched`. Exactly one retry. |
| No retry on `ambiguous` | First event `IN_PROGRESS`. First result `ambiguous`. | `list_open_markets` not called again. Document `ambiguous`. Telegram `ticker none reason ambiguous`. |
| Cutoff discards in-flight | Resolve gated. First event `IN_PROGRESS` `second=BUY_CUTOFF_SECOND`. | After first-event: `reason=="cutoff"`, bind fields null, Telegram `reason cutoff`, resolve gate still closed (cancelled/ignored). Opening the gate later does not flip to `matched`. Retry not started. |
| Cutoff suppresses retry | First result `none` in flight / done, first event `IN_PROGRESS` `second=540`. | No second `list_open_markets`. `cutoff`. |
| Draft clock is not cutoff | First event `PRE_MATCH` `second=540`, slow resolve then `matched`. | Not `cutoff`. Ends `matched`. |
| off: no HTTP, reason off | `kalshi_resolve=None` (build_attached_worker default). Spy on `resolve_kalshi_match`. One feed tick. | Spy not called. `kalshi.reason=="off"`. No `live-paper kalshi:` Telegram. PM attach still runs. |
| Schema-3 skip at host | Unfinished schema-3 `match.json`. `_bare_host` with observe settings + cache spy. `_pick_and_run` with idle worker. | `list_open_markets` not called. File bytes unchanged (no `kalshi` key). |

### `tests/test_live_paper_wallet_host.py`

| Test | Setup | Expect |
|---|---|---|
| `_pick_and_run` does not await resolve | Observe settings, cache with gated `list_open_markets`, `MatchWorker` stand-in that records `__init__` time and `kalshi_resolve.done()`. Announce path with a dummy feed. | Worker constructed with a Task, `done() is False`, `announced is True`. Session-started Telegram still exactly once. Cancel the hanging resolve in the stand-in `run()`. |
| off host starts no task | `_bare_host` defaults (off). Stand-in asserts `kalshi_resolve is None`. | No `resolve_kalshi_match` call. |
| `_IdleWorker` signature | Existing picker tests | 7th arg accepted; still skip/start/announce as today. |

### Mapping unit (same new file or `test_match_meta.py`)

| Test | Setup | Expect |
|---|---|---|
| `kalshi_null_block("cutoff", "KXDOTA2MAP")` | — | Nine keys, bind nulls, series set, reason cutoff |
| Worker matched mapping | `KalshiResolveResult` matched with a binding | `tick_size=="0.01"`, names/ticker copied, no `price_ranges` |

No live Kalshi HTTP. No browser. No `import kalshi` in worker/host tests (client fakes only). conftest autouse `KALSHI_TRADING=off` — observe tests patch `live_paper.kalshi_config.env_value` or pass settings objects directly; do not rely on `os.environ` (US-001 learning).

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
# story-specific first
uv run --group backtest python -m pytest \
  tests/test_live_paper_kalshi_observe.py \
  tests/test_live_paper_wallet_host.py \
  tests/test_live_paper_match_lifecycle.py \
  tests/test_match_meta.py \
  tests/test_live_paper_kalshi_match.py \
  tests/test_live_paper_kalshi_config.py

make test

git add src/live_paper/wallet_host.py src/live_paper/match_worker.py \
  src/live_paper/match_meta.py src/live_paper/kalshi_config.py \
  tests/test_live_paper_kalshi_observe.py tests/test_live_paper_wallet_host.py \
  tests/live_paper_session_fixtures.py docs/live-paper.md
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. First tick does not wait for a slow resolve (`pending` in the start document, attach ran).
2. `pending` → `matched` via `update_kalshi_binding` (only `kalshi` changes).
3. Retry when the first event is already `IN_PROGRESS`.
4. Retry when resolve completes later and `IN_PROGRESS` was already observed (no extra tick).
5. Cutoff at `IN_PROGRESS` `second >= 540` discards in-flight and suppresses retry.
6. `KALSHI_TRADING=off` / `kalshi_resolve is None`: no HTTP, `reason=off`.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **Cutoff is phase-gated.** Overlay text says `second >= 540` without a phase. Steam draft clocks routinely read +61…+540 in `PRE_MATCH`. Applying cutoff on raw `second` would kill observe on draft joins. Implement `phase is IN_PROGRESS and second >= BUY_CUTOFF_SECOND`. Flagged for the implementer; do not “fix” `evaluate_entry` here (pre-existing PM issue, out of scope).
2. **Retry is `none` only.** Feature.json says “оба случая ретрая” meaning the two *timings*, not two reasons. Overlay: only `none` retries. `error` (HTTP) does not get a second search in v1.
3. **Keep pending until retry completes.** Avoids `none`→`matched` flicker. If the implementer persists the first `none` immediately, Telegram must still fire once (after retry). Tests should assert the *final* document and a single bind line.
4. **`start_document_kalshi_reason` leaves the worker.** Reason is `pending` iff a resolve task was passed. That is equivalent for new maps. Schema-3 / off pass `None` and never rewrite.
5. **paper/live without keys now fail `open_wallet_host`.** US-001 defined that start error; WalletHost did not call the loader until now. VPS `.env` is `KALSHI_TRADING` unset/off today, so production is safe. Tests construct hosts via `_bare_host` / fakes and stay off.
6. **Host `__init__` grows three fields.** Required, no defaults. `_bare_host` must be updated in the same change or picker tests explode.
7. **`_IdleWorker` signatures.** Two copies. Miss one → `TypeError` on `_pick_and_run`.
8. **Watcher vs first-event double apply.** If the task is already done, first-event calls `_on_kalshi_result` and must not also spawn a watcher that applies twice. Telegram `_kalshi_notified` + equal-object no-op in `update_kalshi_binding`.
9. **Cancel vs cache inflight.** Cancelling our resolve `Task` must not cancel `KalshiOpenMarketCache`’s own fetch task (US-003 coalescing). `resolve_kalshi_match` *awaits* the cache; cancel unwinds the waiter only. Do not cancel through the cache.
10. **Assumption:** leftover unfinished schema-3 folders on the VPS resume PM and never grow `kalshi` (US-004). This story skips HTTP when `kalshi_reason_for_match` is `"off"`.
11. **Assumption:** `observe` is the operator mode for this slice, but paper/live also start the same public resolve so US-013 does not have to rewire `_pick_and_run`. They still must not start book/prior/exec here.
12. **`matched` Telegram has the ticker; misses do not.** `off_grid` ticker lives in `match.json` only.

No Figma. No poly-maker patch. No new dependency.
