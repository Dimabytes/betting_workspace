# STEP-003 — prove existing unsettled BUYs and deliver BuySettled

## Scope, baseline, and path convention

Implement only STEP-003, the next pending priority: B5 processing of existing rows, plus LiveSources/sync_inputs delivery and cancellation latency logging from B3. STEP-001 and STEP-002 have landed; the inspected checkout is clean on main at `021fcc1043d7719326d54d00dc00d9d584ad21a4`. Recheck HEAD/status before editing and preserve other agents' changes. Sources: `feature.json:104`, `feature.json:124`, `progress.txt:14`, `progress.txt:36`, `work/planner3/baseline.txt:2`.

`src/…`, `tests/…`, `AGENTS.md`, Makefile and dependency/config paths below are relative to `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`. Task paths are relative to this run directory. `poly-maker/…` is the read-only sibling `/Users/dimabytes/work/polymarket/dota_2_bot/poly-maker`. `original-plan` means `/Users/dimabytes/.claude/plans/pasted-content-id-c855-vast-flute.md`.

The planning skill is `/Users/dimabytes/.claude/skills/feature-json-create-step-plan/SKILL.md`; the workspace configuration has empty plan.instructions. This is a planning deliverable, with no product edits, tests, commit, push, SSH or deployment by the planner. Sources: `/Users/dimabytes/.claude/skills/feature-json-create-step-plan/SKILL.md:8`, `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.feature-json.config.json:9`, `00-context.md:12`, `00-context.md:14`, `00-context.md:15`.

**Activation boundary:** do not call insert_unsettled_buy from HTTP or WS, do not change note_cancel's CancelAck behavior, and do not add the gone filter in _own_remaining. These are STEP-004. Existing-row WS handling must never create a row or emit CancelUnsettled; tests explicitly seed rows and enqueue CancelUnsettled where needed. All four steps deploy together only on a separate user command. Sources: `feature.json:33`, `feature.json:34`, `feature.json:134`, `feature.json:135`, `feature.json:136`, `00-context.md:14`.

No UI/Figma/browser/curl work applies: this step changes wallet recovery and adapter delivery; designReference is empty. Source: `feature.json:106`, `feature.json:127`.

## Existing behavior and integration constraints

- Persistence already provides frozen UnsettledBuy rows, open/resolved reads, proof updates, and a set-based resolver. The helpers do not commit; proof changes only unresolved rows. Reuse these without schema changes. Sources: `src/trader/core_persistence.py:228`, `src/trader/core_persistence.py:825`, `src/trader/core_persistence.py:832`, `src/trader/core_persistence.py:858`, `progress.txt:25`.
- Reserve is derived from BUY MATCHED/CONFIRMED ledger size; resolution uses BUY CONFIRMED only, with HALF_SHARE_TICK. MATCHED alone and SUPERSEDED cannot resolve a positive row; FAILED restores reserve through the existing ledger predicates. Sources: `src/trader/core_persistence.py:799`, `src/trader/core_persistence.py:839`, `src/trader/wallet_store.py:375`, `feature.json:35`, `feature.json:36`.
- The kernel already accepts BuySettled on live/canceling/unknown/gone BUYs, waits for filled_qty, and ignores duplicates/absent orders. Events requote immediately and trace decoders are already present. Do not duplicate this logic in the adapter. Sources: `src/strategy/lifecycle.py:356`, `src/strategy/engine.py:100`, `src/strategy/scheduling.py:129`, `src/trader/core_trace_codec.py:628`.
- MATCHED→CONFIRMED commits status/outbox but does not call _fill_credited_handler. WalletFillProcessor instead calls _on_fill on confirmation; the host routes that to _dispatch_fill. REST backfill also confirms MATCHED without this callback. Sources: `src/trader/wallet_store.py:311`, `src/trader/wallet_store.py:321`, `src/trader/wallet_store.py:832`, `src/trader/wallet_host.py:643`, `src/trader/trade_backfill.py:153`.
- The REST seam runs backfill before the positions-send watermark, then drains journal outbox after positions returns. Preserve this ordering. Both ordinary and forced reconcile call gateway.positions, even with no attached market. Sources: `src/trader/engine_seams.py:460`, `src/trader/wallet_host.py:662`, `poly-maker/src/polymaker/engine.py:570`, `poly-maker/src/polymaker/engine.py:591`.
- Engine.user is None at construction and is created inside Engine.start. Installing a callback only in WalletHost._install_runtime_seams would miss it. Sources: `poly-maker/src/polymaker/engine.py:66`, `poly-maker/src/polymaker/engine.py:104`, `src/trader/wallet_host.py:627`, `src/trader/wallet_host.py:968`.
- Existing _canceling_since is a watchdog timestamp populated at the end of a cycle and pruned when already_canceling becomes false. Gone is not already_canceling, so that timestamp cannot measure the complete cancellation-to-retirement interval. Sources: `src/trader/session_core.py:714`, `src/trader/session_core.py:725`, `src/trader/session_core.py:741`, `src/strategy/lifecycle.py:292`.

## Implementation sequence

### 1. Keep payload parsing and REST proof collection in one small module

Add `src/trader/unsettled_buy_recovery.py` for terminal-order proof parsing, REST proof collection, and its constants. Keep WalletHost methods as coordination and engine_seams as hook installation, rather than embedding another large parser in either existing module. Their present responsibilities are host routing/runtime setup and engine adapters. Sources: `src/trader/wallet_host.py:568`, `src/trader/wallet_host.py:636`, `src/trader/engine_seams.py:113`, `src/trader/engine_seams.py:460`.

Suggested contracts (exact private names may follow local conventions):

- Frozen `BuyExecutionProof(venue_id: str, matched_qty: float)` for internal proof values.
- `parse_terminal_buy_proof(msg: Mapping[str, object]) -> BuyExecutionProof | None`.
- `collect_rest_buy_proofs(gateway, rows: tuple[UnsettledBuy, ...], now_s: float, …)`: asynchronous, reads external trades via the gateway and returns typed proofs; if it also backfills the returned trades as described below, accept store and other_token explicitly.
- Constants `REST_PROOF_MIN_AGE_S = 60`, `ORDER_LIFETIME_S = 7200`, and an alert-age constant of 600 seconds, each defined once.

Use required arguments, named intermediate values and frozen dataclasses for new multi-field values; external JSON can use Mapping[str, object]/dict[str, object], but do not introduce dict[str, Any] for internal data. Reuse existing maker_order_id_of, split_fill_key and numeric parsing where their contracts fit. `clob_number` still needs an isfinite/nonnegative check for proofs. Sources: `AGENTS.md:27`, `AGENTS.md:30`, `AGENTS.md:31`, `AGENTS.md:33`, `src/trader/fill_parsing.py:46`, `src/trader/fill_parsing.py:157`, `src/trader/trade_backfill.py:45`.

### 2. Forward raw WS messages and bind the host callback at the actual lifecycle point

In WalletUserStream, initialize a typed `on_order_terminal` callback to a no-op. Override _on_order, call `super()._on_order(msg)` first, then call the callback with the **same raw object**, without copying/normalizing away size_matched or removing fields. The callback name does not imply that the stream prefilters messages: the host/parser decides whether they are terminal. Preserve the fork's order tracking/journal and reconnect callback. Sources: `feature.json:108`, `poly-maker/src/polymaker/userstream/client.py:127`, `src/trader/wallet_store.py:843`, `src/trader/engine_seams.py:169`.

Add a small host binding method and invoke it immediately after `await self.engine.start()` in WalletHost.run, alongside pin_engine_identity/bind_user_fill_address and before _boot_scan. Bind an already-existing WalletUserStream in tests when needed. Do not alter the frozen fork or add a class-global callback. During the no-op startup interval, persisted rows remain recoverable through the REST seam; startup already calls positions before constructing the stream. Sources: `src/trader/wallet_host.py:968`, `poly-maker/src/polymaker/engine.py:100`, `poly-maker/src/polymaker/engine.py:104`, `feature.json:51`.

Host `_on_order_terminal` behavior:

1. Parse a valid BUY message and venue id from raw id; look up the matching existing open UnsettledBuy. The row's session/token identity is authoritative, so no worker or engine.state.orders lookup is required. Missing/resolved rows and SELL messages are no-ops; no insertion in this step. Source: `feature.json:109`, `feature.json:34`, `src/trader/core_persistence.py:858`.
2. A cancellation marker (`type=CANCELLATION` or `status=CANCELED`) or a full-match marker (`status=MATCHED` or parsed size_matched >= a valid positive original_size) is terminal evidence. Parse size_matched separately: absent, None, empty/whitespace, invalid, boolean, negative or nonfinite values give no proof. A numeric/string zero is valid evidence. Do not use a missing original_size as zero; partial UPDATE/LIVE messages give no proof. Sources: `feature.json:109`, `feature.json:38`, `original-plan:112`.
3. For a valid existing-row proof, update qty/proven and run resolve_settled_buys in one short caller-owned SQLite transaction. A nonzero proof can still remain open until CONFIRMED. A proven zero resolves immediately. Repeated WS notifications are harmless; proof helpers cannot reopen resolved rows. Sources: `src/trader/core_persistence.py:825`, `src/trader/core_persistence.py:832`, `feature.json:24`.
4. Commit before waking any session. Retain/open-read row metadata before the resolver removes rows from the open set; use returned newly resolved venue IDs to derive session IDs. Deduplicate session wakes. Also wake the affected available worker after proof progress; no direct BuySettled push is needed. Source: `feature.json:113`, `src/trader/core_persistence.py:845`.

Keep this handler synchronous, without REST waits. It processes only existing rows: it does not move a live order to gone, infer submitted_qty from normalized remaining size, or activate STEP-004's external-cancel behavior. Sources: `feature.json:34`, `feature.json:135`.

### 3. Centralize row resolution, wake routing and old-row alerts

Provide a host resolution helper that accepts/snapshots open rows, calls the existing global resolver in the caller's transaction, and routes newly resolved IDs back to their rows' session IDs **after commit**. Every resolution entry point must work without worker, core, live token map, or archive. Use `_worker_by_cid`/row.session_id for deciding whether a wake is useful; engine._token_cid is removed on detach. Sources: `feature.json:110`, `feature.json:112`, `src/trader/wallet_host.py:620`, `src/trader/wallet_host.py:889`, `src/trader/engine_seams.py:1140`.

Do not emit an alert for a row this pass just resolved. Inspect remaining open rows using `time.time() - created_at`, including proven/SUPERSEDED rows and rows whose session has ended. After age >600 seconds call engine.alerter.alert with `unsettled_buy:<venue8>` and a **stable message** naming the venue/session and saying its reserve remains held; omit changing age/qty/notional from the dedup message. Existing wrap_alert_transitions deduplicates by (key, message) and clears only risk_halt keys. This gives one alert per key during the host lifetime without a new DB column or timer task. Sources: `feature.json:113`, `src/trader/engine_seams.py:687`, `src/trader/engine_seams.py:694`, `src/trader/engine_seams.py:704`.

Alerting never proves/resolves a row, writes inventory, triggers cancel, or modifies reserve. Call this old-row sweep on each REST pass, including when trade requests fail. Restart may re-alert an outstanding row; durable once-ever alert metadata is outside the schema specified for this feature. Sources: `feature.json:113`, `feature.json:50`, `src/trader/core_persistence.py:97`.

### 4. Resolve on CONFIRMED independently of journaling and credited-fill callbacks

In _dispatch_fill, recover the recorded fill as today, derive maker_order_id with split_fill_key, and check the existing matching open row when the ledger status is CONFIRMED and the fill is BUY. Run the shared resolver and commit/wake before the early return for `seq is None`. Ledger confirmation is the authority, not whether a journal outbox row remains unacked. Sources: `feature.json:110`, `src/trader/wallet_host.py:779`, `src/trader/fill_parsing.py:157`, `src/trader/wallet_store.py:655`.

Preserve journal/markout/rebate and outbox ack idempotence. Do not move settlement solely to _on_fill_credited: MATCHED→CONFIRMED would be missed. Do not close a positive row on MATCHED/MINED/FAILED/SUPERSEDED, and do not invent a confirmation when dispatch is called manually. The resolver itself already enforces the final sum. Sources: `src/trader/wallet_store.py:302`, `src/trader/wallet_store.py:832`, `src/trader/wallet_host.py:786`, `src/trader/core_persistence.py:839`.

The REST sweep below covers backfill and confirmed rows already journal-acked before a crash. No need to rewrite drain_outbox to infer proof or couple money-row lifetime to archive lookup. Existing drain/journal tests must continue to pass. Sources: `src/trader/wallet_host.py:850`, `tests/test_trader_wallet_host.py:934`, `tests/test_trader_wallet_host.py:970`.

### 5. Add host reconciliation after REST backfill

Extend install_rest_fill_recovery with a required `reconcile_unsettled_buys: Callable[[], Awaitable[None]]` argument; pass the host's bound coroutine from runtime installation. In the live positions wrapper, attempt _pull_missed_fills, then independently attempt the host sweep, then await original_positions and drain_outbox as today. Failure in one trade read must not suppress the other recovery attempt or make positions look empty. Preserve the positions watermark ordering and paper path. Sources: `feature.json:111`, `src/trader/engine_seams.py:473`, `src/trader/wallet_host.py:663`.

Host sweep algorithm:

1. Read all open rows across all sessions. Select only !proven rows with age >=60s for REST proofs. Resolve already-confirmed/proven rows regardless of age and run the alert sweep even when no token qualifies. Do not filter candidates through engine.metas, attached tokens or live workers. Sources: `feature.json:111`, `feature.json:112`, `src/trader/core_persistence.py:858`.
2. Group eligible rows by token_id. For each token use `after = int(min(created_at of eligible rows for that token)) - 7200`. Call the existing client through gateway._io with `TradeParams(maker_address=gateway.funder, asset_id=token_id, after=after)`; one logical get_trades call per eligible token, retaining its default all-pages behavior. Do not use get_order or substitute LOOKBACK_S. Sources: `feature.json:111`, `feature.json:37`, `src/trader/engine_seams.py:434`, `.venv/lib/python3.13/site-packages/py_clob_client_v2/clob_types.py:172`, `.venv/lib/python3.13/site-packages/py_clob_client_v2/client.py:577`.
3. Sum only the requested venue's maker_orders[].matched_amount entries by order_id. Reuse maker_order_id_of; do not sum top-level trade.size, taker size, all makers, or ledger size as execution proof. Proof quantity is independent of trade settlement status; finalization remains the resolver's job. A successful complete empty result proves zero. Missing client/funder, transport/pagination errors, a malformed response/trade/maker_orders structure, or malformed relevant amount data provide no proof, never a synthetic zero; continue independent tokens. Sources: `feature.json:112`, `src/trader/fill_parsing.py:46`, `original-plan:119`, `original-plan:122`.
4. The 7200s proof reply may contain settled fills older than _pull_missed_fills's one-hour window. Feed that successful reply through existing backfill_trades before proof/resolution so such rows can eventually resolve after a long downtime; keep its existing status/identity/idempotence behavior. This is bounded reuse of the current backfill path, not a change to LOOKBACK_S or ledger rules. Sources: `src/trader/engine_seams.py:63`, `src/trader/engine_seams.py:449`, `src/trader/trade_backfill.py:117`, `original-plan:120`, `feature.json:112`.
5. Never keep a DB write transaction open across gateway._io awaits and never write SQLite from its executor thread. After fetching/backfill, reread open rows and apply a REST proof only to a candidate still unresolved **and still unproven**. A WS proof or resolution that arrived during the await wins. Source: `src/trader/core_persistence.py:825`, `src/trader/wallet_store.py:355`, `feature.json:109`, `feature.json:111`.
6. Apply remaining proofs and call resolve_settled_buys in a short transaction; commit, wake newly resolved/progressed available sessions, then inspect still-open rows for alerts. If proof fetches failed, still resolve from the now-committed ledger; already-proven rows need no proof request. Sources: `feature.json:112`, `feature.json:113`, `progress.txt:25`.

No separate scheduled task or reconnect mechanism is needed: engine's periodic and reconnect-forced reconcile both enter this same positions seam. Preserve WalletUserStream._on_reconnect behavior and test the forced route explicitly. Sources: `poly-maker/src/polymaker/engine.py:272`, `poly-maker/src/polymaker/engine.py:575`, `src/trader/engine_seams.py:169`.

Accepted limitation: a complete trades result can lag more than 60s and falsely prove zero. Do not add two-equal-pass proof, get_order fallback, reopening resolved rows, or timeout release in this step; those are explicitly excluded. Sources: `feature.json:37`, `feature.json:48`, `feature.json:50`.

### 6. Deliver resolved BUYs as repeated live inputs

Add required `LiveSources.settled_buys: Mapping[str, float]`, with no implicit default. Update both handwritten source factories to pass {} by default and allow settlement maps in focused tests. Sources: `feature.json:114`, `src/trader/session_core.py:101`, `tests/test_trader_session_core.py:142`, `tests/test_trader_live_execution.py:60`.

In live_sources, first inspect **active state.orders BUYs** with a core_to_venue binding. Query resolved_unsettled_buys(store._conn, quoting.session_id) only when at least one exists; otherwise pass {}. venue_ids alone is insufficient: bindings also retain terminal history and SELLs. Do not query for records-only, pending unbound BUYs, SELL-only state, or another session. Sources: `src/trader/session_core.py:409`, `src/trader/session_core.py:455`, `src/trader/session_core.py:832`, `src/trader/core_persistence.py:865`.

In sync_inputs, append one BuySettled(now_ns=sources.now_ns, order_id=core_id, matched_qty=map[venue_id]) per matching active BUY. Include zero via key membership, not truthiness. Iterate current active BUY orders, not every retained binding or every DB result. Preserve normal inputs and mismatch/recovery behavior; keep event order deterministic, with refreshed budget/permissions before settlement-triggered requote. Sources: `feature.json:115`, `src/trader/session_core.py:600`, `src/strategy/scheduling.py:132`.

Do this on every cycle without consuming/deleting resolved rows or tracking delivered IDs. If core has not credited enough filled_qty, BuySettled is a no-op and is retried; after removal it disappears from subsequent input lists even though bindings/row remain. The first normal cycle after attach/load handles a row resolved without a worker, with no special event in load_core_snapshot or host registration. Sources: `src/strategy/lifecycle.py:362`, `src/trader/session_core.py:660`, `src/trader/wallet_host.py:870`, `src/trader/core_session_io.py:65`, `original-plan:102`.

### 7. Log actual core transitions and cancellation-to-retirement latency

Observe prior/next active-order state inside LiveCore.apply, which covers queued fills, cancellations and sync_inputs. Log a transition only when a BUY actually becomes gone; repeated CancelUnsettled or late absent-order notifications must not duplicate it. Emit `trader core buy settled id=… wait_ms=…` only when BuySettled actually removes the old BUY, and when a full Fill removes a previously gone BUY (include cause so core retirement is distinct from durable CONFIRMED resolution). A BuySettled that is waiting for credited fills emits no removal log. Sources: `feature.json:116`, `src/trader/session_core.py:631`, `src/strategy/lifecycle.py:349`, `src/strategy/lifecycle.py:362`, `src/strategy/lifecycle.py:675`.

Maintain a separate per-core cancellation-start map in monotonic nanoseconds. Set once when an apply transition first enters already_canceling for a BUY, using that event's now_ns; preserve it through unknown, retries and gone until actual removal. For a venue-initiated cancellation without a preceding local cancel, initialize at the first applied CancelUnsettled. Do not restart the timer on proof, repeated settlement delivery, or gone. Compute elapsed milliseconds from the removal event's now_ns, not time since placement or latest proof. Sources: `src/trader/session_core.py:631`, `src/trader/session_core.py:714`, `src/strategy/lifecycle.py:302`, `feature.json:116`.

Keep watchdog state separate: it must still stop treating gone as an unproven exchange cancel. Include the new timing state in CoreMemory capture/revert using a frozen named timing value (or another typed copy), so failed apply_and_persist restores it with the orders. Clear timing for removed orders, including ordinary cancellation paths, to bound memory. Sources: `src/trader/session_core.py:89`, `src/trader/session_core.py:459`, `src/trader/session_core.py:471`, `src/trader/core_session_io.py:84`, `AGENTS.md:31`.

Checkpoint schema remains 3 and gains no timing fields. A restored gone order has no original monotonic cancellation start; report wait_ms as unavailable with a clear restored origin rather than fabricating a zero or measuring from placement/attach. Accurate live cancellation latency is the scoped metric; cross-restart original latency would require additional durable timing plumbing. Sources: `feature.json:49`, `src/trader/core_persistence.py:140`, `src/trader/core_persistence.py:386`, `src/trader/session_core.py:552`.

## Automated verification to add

Use real WalletStateStore/SQLite for row, ledger and budget assertions, with explicitly seeded rows. Mock gateway/client and clocks; do not contact the venue. Reuse _bare_host/_outbox_host and update narrow fake engines for the required callback/wake/alerter contracts. Sources: `feature.json:34`, `tests/test_trader_wallet_host.py:430`, `tests/test_trader_wallet_host.py:913`, `tests/conftest.py:62`.

### WS and confirmation — engine-seams / wallet-host tests

- WalletUserStream forwards the same raw message after super: journal/processor side effects precede callback, default callback does nothing, and raw size_matched is retained. Bind after Engine.start and test the production host run path, not only a hand-assigned callback. Sources: `feature.json:117`, `poly-maker/src/polymaker/userstream/client.py:127`, `src/trader/wallet_host.py:968`.
- Existing-row CANCELLATION/CANCELED proof for zero/partial/full quantities; MATCHED and full-size UPDATE proof; partial live UPDATE, SELL, unrelated venue, absent/resolved row and empty/malformed size give no proof. Assert no row creation and no emitted CancelUnsettled. Sources: `feature.json:109`, `feature.json:34`.
- Zero proof resolves immediately; positive proof waits for real CONFIRMED, preserves MATCHED reserve arithmetic, and leaves SUPERSEDED open. Repeat callbacks are idempotent. Prove/resolve transaction is committed before wake, verifiable with a second SQLite connection. Sources: `src/trader/core_persistence.py:825`, `src/trader/core_persistence.py:832`, `feature.json:24`.
- MATCHED→CONFIRMED through real WalletFillProcessor.on_trade resolves and wakes although no credited callback fires on the transition. Also test standalone CONFIRMED and replay dispatch with seq already acked; resolution cannot depend on journaling seq. Sources: `feature.json:110`, `src/trader/wallet_store.py:311`, `src/trader/wallet_store.py:832`.
- Repeat with no worker, no engine token mapping and a completed session: durable row still closes. With a worker, wake exactly its row.session_id; multiple resolutions for one cid coalesce. Existing journal/markout/ack tests retain their expectations. Sources: `feature.json:117`, `tests/test_trader_wallet_host.py:934`, `tests/test_trader_wallet_host.py:970`.
- Open row at age 599s has no alert; age >600s emits unsettled_buy:<venue8> once across repeated sweeps; include proven/SUPERSEDED and workerless rows, fetch-error passes and a just-resolved row. No alert path changes row flags/reserve. Use real wrap_alert_transitions to validate stable-message dedup. Sources: `feature.json:113`, `src/trader/engine_seams.py:694`.

### REST — engine-seams / wallet-host tests

- Ages 59.999s and 60s exercise the exact proof boundary; young and proven rows generate no proof fetch. Same-token rows across multiple sessions produce one token query, with maker_address=funder, asset_id=token, after=int(oldest eligible created_at)-7200. Distinct tokens each get one query. Sources: `feature.json:111`, `feature.json:118`.
- Build several trades with multiple makers and unrelated order IDs; sum requested maker_orders[].matched_amount only. Full successful empty result proves zero; malformed relevant amount, exception, missing client/funder, or incomplete page fetch yields no proof. One failing token does not block another. Source: `feature.json:112`, `feature.json:118`.
- Existing proven row closes after _pull_missed_fills backfills/confirmations without a proof query. Long-window settled trades older than LOOKBACK_S are booked through existing backfill before settlement. MATCHED/SUPERSEDED semantics remain unchanged. Sources: `src/trader/trade_backfill.py:153`, `src/trader/engine_seams.py:63`, `feature.json:112`.
- During awaited get_trades, inject WS proof and/or resolution; returned REST zero must not overwrite it. Assert executor does no DB work and no transaction is held over the await. Source: `src/trader/core_persistence.py:825`, `feature.json:111`.
- Verify sequence: backfill → host sweep → original positions/send watermark → journal drain. _pull exception still allows host sweep; proof exception still permits positions/drain and alerts. Preserve the existing paper path. Sources: `src/trader/engine_seams.py:460`, `src/trader/wallet_host.py:662`.
- Run the seam with no worker/metas and completed-session rows; force engine._on_user_reconnect and a reconcile positions pass to prove the same sweep runs without attach. Sources: `feature.json:112`, `poly-maker/src/polymaker/engine.py:272`, `poly-maker/src/polymaker/engine.py:591`.

### Live inputs and logging — session-core / live-execution tests

- A resolved venue bound to a BUY produces BuySettled on two successive sync_inputs calls while filled_qty<matched_qty. Apply partial Fill, repeat, then verify removal and no more events. Test live/canceling/unknown/gone and matched_qty=0; stale resolved bindings and SELLs produce no events. Sources: `feature.json:115`, `src/strategy/lifecycle.py:356`.
- Spy on resolved_unsettled_buys: no call without an active bound BUY (including SELL-only, pending-unbound and records-only cases); one session-scoped read otherwise. Sources: `feature.json:114`, `src/trader/session_core.py:832`.
- Seed/persist a gone BUY and binding, unregister/remove its worker, resolve its row, then attach a new core/load snapshot and run the first cycle. It receives BuySettled and removes the order without any new host-edge notification. Source: `feature.json:115`, `src/trader/core_session_io.py:65`, `src/trader/wallet_host.py:870`.
- With deterministic times cancel starts at t=1s, gone at t=2s, a retry at t=3s and removal at t=4s: assert exactly one gone log and one settled log with wait_ms=3000. Retry/no-op/late HTTP events do not reset timing or create removal logs. Include zero settlement before gone, full Fill removing gone, capture/revert restoration, and restored timing marked unavailable. Source: `feature.json:116`, `src/trader/session_core.py:631`, `src/trader/session_core.py:471`.
- Update all required LiveSources fixtures and retain ordinary BUY/SELL cancel behavior and own-book behavior for this step. Their activation tests belong to STEP-004. Sources: `tests/test_trader_session_core.py:142`, `tests/test_trader_live_execution.py:60`, `feature.json:136`.

## Verification commands and completion audit

The planner has not executed implementation tests. The implementer runs from esports-trader outside a live map, using its own unique basetemp and the project venv. Run-context commands supersede generic make-test wording: xdist is enabled by PYTEST_N, and the broad suite omits slow current_policy_smoke goldens for this adapter-only step. Sources: `00-context.md:19`, `00-context.md:23`, `00-context.md:25`, `tests/conftest.py:24`, `AGENTS.md:21`.

Targeted checks after implementation (append any new focused proof module test file if created):

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-implementer-step003 -q tests/test_trader_wallet_host.py tests/test_trader_engine_seams.py tests/test_trader_session_core.py tests/test_trader_live_execution.py tests/test_trader_trade_backfill.py tests/test_trader_core_persistence.py tests/test_trader_core_recovery.py tests/test_strategy_late_fills.py tests/test_core_trace.py
```

Then required broad suite and typecheck:

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-implementer-step003 -q tests -k "not current_policy_smoke"
uv run python -m basedpyright
git diff --check
```

The actual typechecker is basedpyright and its hook intentionally checks the whole project so configured exclusions apply; do not use an assumed pyright executable or change dependencies/test settings. Sources: `.pre-commit-config.yaml:16`, `.pre-commit-config.yaml:18`, `.pre-commit-config.yaml:23`, `pyproject.toml:29`.

Inspect the step diff, stage only owned implementation/tests when the implementer's skill requires the commit, inspect `git diff --cached`, then run `make lint`. Its staged checks include Ruff formatting/checks and whole-project basedpyright. Rerun only checks affected by fixes/format edits. Sources: `feature.json:121`, `Makefile:24`, `.pre-commit-config.yaml:1`, `.pre-commit-config.yaml:16`.

The prior adapter/refill/smoke failures are historical context, not authorization to ignore failures at this HEAD. Record failing test names, exact HEAD and evidence of whether a failure predates this step; do not rewrite unrelated tests/goldens. Kernel/policy/backtest changes are outside scope, but if such scope changes occur, run the applicable goldens and investigate drift without updating expected results. Sources: `progress.txt:11`, `00-context.md:25`, `feature.json:47`.

Final audit: expected production edits are the new small recovery module and narrow engine_seams, wallet_host and session_core changes, plus focused tests/fixtures. No new production insert_unsettled_buy call, no note_cancel activation, no _own_remaining filter, no checkpoint version/schema edits, no paper strategy, no part A edits, no frozen fork edits. All proof/resolve transactions commit before wakes; no worker is needed for durable settlement; BuySettled repeats until active core removal. Sources: `feature.json:34`, `feature.json:49`, `feature.json:51`, `feature.json:108`, `feature.json:114`, `feature.json:136`.

Implementation is one main commit governed by the implementer's separately invoked skill. Record changes/checks/limitations in progress.txt; only mark this step passed after implementation and review. Operational notes, live activation, push and deployment remain outside this planning task and outside STEP-003. Sources: `feature.json:40`, `feature.json:141`, `00-context.md:12`, `00-context.md:14`, `00-context.md:15`.
