# live-devin — live execution, wallet, orders, risk, recovery, latency
Status: FINAL

Audit scope: live execution, wallet, orders, risk, recovery, latency.
Repos: `esports-trader` (product, mutable), `poly-maker` (frozen fork, read-only).
Method: full source read of `src/trader/` + `src/strategy/`, fork internals (`execution/gateway.py`,
`state/store.py`, `userstream/client.py`, `engine.py`), config `trading.toml`, and latency
measurement from 8 local archives all confirmed `execution_mode=live`.
Paths below are relative to `../esports-trader` (`$E`) and `../poly-maker` (`$P`).

Severity: S1 critical (money loss/corruption), S2 major (wedge/strand/mislead), S3 moderate, S4 observability.

---

## S1 — permanent cross-session budget leak + recovery wedge from a single mid-place exception

**F1. `dispatch_started` commands are write-once, never retired or consumed — a lost result is permanent.**

`_durable_place` commits `prepared` then `dispatch_started` *before* awaiting the venue call:

- `$E/src/trader/wallet_host.py:417-439` — `prepare_dispatch` at line 427, `await original_place` at 428,
  `record_dispatch_results` at 429-430. Any exception escaping between 428 and 430 skips the outcome write.
- `$E/src/trader/core_execution.py:59-78` — `prepare_dispatch` → `mark_dispatch_started` (two commits),
  persists `dispatch_state='dispatch_started'`, `outcome=''`, `consumed=False`.
- `$E/src/trader/core_execution.py:186-188` — `mark_dispatch_started` is the last write on the failure path.

Nothing ever clears that state:

- `consume_command` (`core_execution.py:226-227`) — **zero callers** (grep `src/`).
- `retire_unsent` (`core_execution.py:230-237`) only retires `dispatch_state=='prepared'` — a
  `dispatch_started` row falls straight through. Called via `wrap_recompute_retire` (268-280) after every
  recompute, so the gap is structural, not timing.
- `unresolved_order_ids` / `orphan_tokens` (core_recovery.py) — **zero callers**.

Consequences of one wedged command, both permanent and silent:

1. **Budget leak (process-wide, cross-session).** `reserved_buy_notional`
   (`$E/src/trader/core_persistence.py:746-748`, `_OPEN_BUY_WHERE` = `outcome IS NULL OR outcome=''`)
   counts every non-consumed open BUY command across *all* sessions. `budget_from_orders`
   (`$E/src/trader/session_core.py:848,854`) subtracts it from `available_usdc` every cycle. One wedged
   `$60`/`$200` clip permanently shrinks the buy budget for every future market until the row is manually
   fixed — the wallet sqlite file outlives the session.
2. **Recovery verify wedge (session-wide).** `unresolved_commands`
   (`core_persistence.py:736-743`) returns it → `RecoveryCoordinator.proof_blocks`
   (`$E/src/trader/core_recovery.py:150-162`) yields `unknown_command` → `accept_if_proven` can never pass
   → `recovery_pending` stays → `sell_only` stays (`lifecycle.py:366-397`,
   `quoting.py:_buy_closed_reason` "recovery"/"ownership_unresolved"). Session buys are dead for the rest
   of the map even though the position may be flat.
3. **Orphan venue order.** If the shielded inner place completed server-side before the outer raise
   (`ShutdownLatch._await_in_flight` `$E/src/trader/engine_seams.py:286-310` lets the inner task finish),
   a live order exists with no core binding → feeds F2/F4 below.

Trigger is a routine race, not exotic: `GatewayClosed` from `ShutdownLatch.wrap_gateway`
(`engine_seams.py:252-261, 324-328`) when a place is in flight at teardown/latch-close; `CancelledError`
(not caught by the gateway's `except Exception`); or `_order_bucket.acquire()` outside the try
(`$P/src/polymaker/execution/gateway.py:150-183` — semaphore acquire precedes the error-swallowing block).
Money impact: permanent phantom reservation + forced sell-only + possible live orphan. **S1.**

*Needs VPS confirmation:* frequency — count `dispatch_started`+`consumed=0` rows in
`data/trader/wallet.db` `core_command` on the VPS; one row per incident. Fix direction: a sweeper that
retires `dispatch_started` rows past a grace into `outcome='lost'`, consumed, before `reserved_buy_notional`
and `proof_blocks` run.

---

## S2 — order/ack orphaning and state-machine wedges

**F2. Orphaned place/cancel results are logged once, then lost — no core ever sees the ack.**

- `$E/src/trader/wallet_host.py:406-414` — `_warn_orphaned` docstring says it plainly: "no core will ever
  see its ack"; the core keeps `pending`/`canceling`, `sell_occupied` holds the exit slot, and neither
  `core_trace.jsonl` nor `session.jsonl` records the miss.
- `wallet_host.py:423-434` (place) and `447-451` (cancel): if `_worker_by_cid`/`_worker_by_token` miss, the
  result is only logged. `_durable_cancel` cannot route by core id — only venue id via token→worker
  (`_worker_for_order_ids`), so a cancel for an order whose worker was just unregistered is orphaned by
  construction.
- `sell_occupied` (`$E/src/strategy/lifecycle.py:263-267`, consumed in `quoting.py:698-721`) treats any
  SELL in `state.orders` as occupying the one exit slot — a stuck `pending`/`canceling` SELL blocks every
  replacement exit indefinitely.
- `has_unresolved_orders` (`lifecycle.py:377-382`) counts BUY in `pending|live|canceling|unknown`, which
  also stalls `RecoveryVerified`.

No escalation, no retry of the routing lookup, no archive write. **S2.** VPS check: grep
`trader order result orphaned` in `trader.log` against active sessions.

**F3. Wedged/unmatched venue orders are never cancelled intra-session — orphans rest until quiesce or a fill.**

`esports_reconcile` (`session_core.py:959-977`) computes `to_cancel` only from the plan via
`_venue_cancels` (819-836) — i.e. only orders already bound core↔venue. An order that is live at the venue
but unbound is never in `to_cancel`, never re-priced, never cancelled while a plan exists. Sources of
unbound venue orders: `note_placed` warns on extras (`session_core.py:577-582`) then leaves them unbound;
`bind_place_results` (`core_execution.py:240-261`) only matches exact token/side/price/size; shielded places
completing after latch; REST-adopted orders. The cancel-all branch (`to_cancel=[o.order_id for o in live]`)
only runs when `core.take_plan()` returns None — so orphans persist across normal requote cycles and are
cleaned only when the plan stream stops, at quiesce (`fence_no_orders`, `engine_seams.py:1247-1253`), or on
quarantine (`engine.py:492-511` — where fork `_quarantine` removes local orders even when the strict
`cancel_asset` returns False, `_prove_asset_orders_canceled` `engine_seams.py:566-582`). A fill on an
unbound venue id lands in `pending_ownership` (F4). **S2.**

**F4. Pre-binding / unbound fills park in `pending_ownership` forever and close buys for the session.**

- `session_core.py:493-530` — `note_fill` with an unknown venue id enqueues `Fill(order_id=<venue_id>)`.
- `lifecycle.py:667-677` → `_pend_fill` → `pending_ownership` keyed on that venue id; `_credit_buy_inventory`
  still credits inventory (425-446) so sells keep working.
- `quoting.py:205-211` — `_buy_closed_reason` returns `"ownership_unresolved"`/`"recovery"` → no BUYs placed.
- The only drain is `apply_ownership_resolved` (`lifecycle.py:680-729`) matching `pending.order_id ==
  event.order_id`, and the only producer of `OwnershipResolved` is `DustSweeper._bind_taker_sell`
  (`$E/src/trader/dust_sweep.py:223-251`, emits at 238) — dust FAK sells only. A maker fill that arrives
  between venue execution and `bind_venue` (`note_placed` binds after place returns, `session_core.py:592-593`)
  parks permanently; the `fill_id` dedupe inside `apply_fill` means even re-delivery can't recover it.
- `note_fill` itself warns once — `trader core unknown venue fill` (`session_core.py:504-519`).

Window is real: the venue can fill during the `await original_place` suspension before the HTTP response is
processed (~174ms median place→accept, measured). **S2.** VPS check: grep `unknown venue fill` /
`ownership_unresolved` in `session.jsonl`/`trader.log`.

**F5. User-WS `on_trade` exception → reconnect, but the FAILED reversal never lands → permanent MATCHED wedge.**

`$P/src/polymaker/userstream/client.py:107-125` — `_handle` calls the trade/order processor synchronously;
an exception escapes the WS iterator → socket closes → reconnect — but the event that raised is consumed
and never replayed (no replay buffer on user WS). If the raise was inside `WalletFillProcessor.on_trade`
(`$E/src/trader/wallet_store.py:812-842` — `apply_failed_fill` at 839-842 has no try/except), the ledger row
stays `MATCHED`:

- `has_unacked_matched` (`wallet_store.py:249-261`) → `_unacked_matched`/`fence_until` never passes
  (`wallet_host.py:515-531`) → `keep_quiet` at quiesce — market stays attached, never fenced.
- `matched_open` (`core_recovery.py:147-148`) blocks `accept_if_proven` → recovery never verifies →
  sell-only for the rest of the map.
- Position stays inflated: the reconcile loop's `expire_inflight` (fork `engine.py:586-588`, fork
  `state/store.py:144`) clears only the in-memory inflight counter — not the ledger MATCHED status that
  `has_unacked_matched` reads — so the wedge survives every 20s reconcile round indefinitely.

Trigger chain: `apply_failed_fill` (`wallet_store.py:376-411`) → `_rebuild_token_position` (764-785) replays
MATCHED+CONFIRMED through strict `_position_after_fill` (856-871, raises `OversizedSellError`). If the
ledger holds a backfilled SELL written with raw (unclamped) size — `_position_after_backfill` clamps at
*write* time (849-853) but the stored row keeps raw size — the strict replay at *rebuild* time overshoots →
raises → the whole `with self._conn:` (390) rolls back → exception reaches `_handle`. Compound but
plausible. **S2.**

**F6. Live latency violates the 85ms backtest assumption by 2-10×, with a fat tail.**

Measured from `core_trace.jsonl`/`session.jsonl` across 8 archives, all `execution_mode=live`
(scratch: `work/live-devin/latency.py`):

| hop | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| signal → place | 221 | 166ms | 479ms | 7,221ms | 9,759ms |
| book → place | 284 | 63ms | 86ms | 248ms | 494ms |
| place → venue accept | 349 | 174ms | 213ms | 328ms | 591ms |
| cancel → ack | 340 | 66ms | 103ms | 2,353,369ms | 3,113,114ms |
| place → fill | 67 | 3,830ms | 21,502ms | 82,482ms | 82,482ms |

- Median signal→venue-ack ≈ 340ms (166+174) — ~4× the 85ms assumption; p90 ~690ms.
- The cancel→ack p99/max are the `cancel > 0, place = 0` wedge class from the log-map — minutes-scale stuck
  cancels, not ordinary latency. Distinguish: healthy p50/p90 are fine; the tail is a wedge detector.
- Contributors on the loop: `debounce_ms=100` sleep at fork `engine.py:333` + `quoter_tick_s=2.0` baseline
  with a `max(1.0, wake)` floor (`engine.py:342-355`) (`trading.toml`), per-record `fsync` on every
  journal/state write (`$E/src/trader/archive_paths.py:104-110` `FsyncedJsonlWriter`), all sqlite commits in
  `_durable_place`/`record_dispatch_results`/`persist_core_snapshot`/`apply_*_fill`, model inference in the
  decision path, REST backfill piggybacked inside `positions()` on the reconcile tick
  (`engine_seams.py:460-483`). All on one event loop — IO jitter lands directly on the signal→place path.
- `install_core_quoter_wake` (`engine_seams.py:1256-1270`) caps the idle timeout at the core's
  `next_wake_ns` — good — but the base cadence and debounce still dominate steady-state latency.

**S2** for model/fill-timing calibration; the wedge tail is operational S2. Needs VPS: per-hop timing on the
production host, and whether `session.jsonl` `signal→place` tail correlates with fsync/sqlite stalls.

**F7. Paper gateway is systematically optimistic — paper PnL cannot validate live.**

- `$E/src/trader/paper_gateway.py:57-58, 212-232` — fill requires strict cross (a print *at* our bid does
  not fill), then fills **full size at our own limit** instantly. No queue position, no partial fills, no
  adverse selection, no ack delay, no cancel race.
- `cancel` always returns True (169-173); `positions()={}` (103-105) and `token_balances={}` (120-122) →
  divergence check is a no-op in paper; `_pull_missed_fills` and strict-REST are skipped
  (`engine_seams.py:474, 381-382`); heartbeat skipped (486-497).
- Sell rejections never occur beyond the local `sell_is_droppable` inventory check.

Net: paper ≥ live on entry fill rate and exit timing by construction. If paper results feed sizing/go-live
decisions they are biased optimistic. **S2** (calibration risk, not direct live-money risk).

---

## S3 — fail-open edges and recovery fragility

**F8. `collateral_balance` failure zeroes the buy budget.** `$P/.../gateway.py:399-409` returns `0.0` on any
REST error (`balance_allowance` swallows to `{}` at 500-517 → falls through to `return 0.0` at 409);
`install_collateral_snapshot` (`engine_seams.py:401-413`) writes it unconditionally →
`available_usdc=0` → all BUYs blocked until next successful reconcile (~20s, `reconcile_interval_s`). Fail-
closed but silent — one `info` log at 411 only when ≤0. Similarly `positions()` returns `{}` on error
(`gateway.py:496-498`) and `if positions:` skips the round — an API outage masquerades as "flat".

**F9. `_open_orders_with_proof` merges every local order into REST — REST can never prove an order gone.**
`engine_seams.py:544-563` `by_id.setdefault` injects all of `state.orders` into the result; then the fork's
`replace_open_orders` 10s-grace delete path (`$P/src/polymaker/state/store.py:174-197`) is unreachable.
Phantom orders (filled while user-WS blind, cancel-ack-lost, expired) persist in `state.orders` until a
terminal WS event or a proven cancel. Direction is conservative (never wrongly "gone" → no double-place),
but a phantom SELL pins `sell_occupied` and masks exit wedges for up to a requote cycle; phantom BUY
overstates reserved exposure. It also makes `fence_no_orders` / `replace` proofs take the slow path.

**F10. Startup order verification is fully suppressed.** `engine.py:224-242` —
`contextlib.suppress(Exception)` wraps both boot `cancel_all` and the strict `open_orders` verify; a
`RestUnproven` raise skips even the `startup_orders_remain` warning. Leftover venue orders silently persist
→ F3 orphans from boot.

**F11. Every halt pulls exit SELLs too — inventory rides the halt (and daily-loss halts ride to settlement).**
`session_core.py:314-329` (`permissions_from_quote`, `halt=regime is Regime.HALTED` at 325) → `requote` → `permissions.halt`
at `quoting.py:956-957` → `_blocked` (103-110) → `_cancel_orders(buys_only=False)` (91) — exit SELLs
included —
and no new sells until clear. Halts that clear (market-WS stale>30s, user-WS blind>15s, heartbeat≥3 after
60s boot grace `HEARTBEAT_BOOT_GRACE_SECONDS=60`, `engine_seams.py:493-500`) resume quoting; `daily_loss`
clears only on UTC day rollover → a daily-loss halt with open inventory strands it to settlement. Design
intent (can't trust books blind) but it is inventory-exposing, and worth a runbook note.

**F12. Restart classifies as "recovery" whenever any history exists — verification depends on live RPC.**
Because `consume_command` is dead (F1), `unresolved_commands` (`core_persistence.py:736-743` — any
`consumed=0` row in `prepared`/`dispatch_started`/`dispatched`) is non-empty for any session that ever
placed → `classify` (`core_recovery.py:115-129`, `outstanding` at 123) → `_begin_recovery` → `sell_only`
until `proof_blocks` empty AND
`prove_token_balances`/`share_qty_matches` (`core_recovery.py:274-297`) — an on-chain `token_balances` RPC
that returns None on failure defers `RecoveryVerified` indefinitely (`match_worker.py:667-718`,
`_maybe_verify_recovery` retried per feed event). Exits still run during the wait (`_recovery_exit`,
`quoting.py:908-920` — sells proceed), so the direction is safe, but an RPC outage at boot leaves the
session sell-only even when flat. `resume_exit`/`RESUME_LOCKED` (`session_engine.py:18-36`,
`match_worker.py:640`) are **write-only vestigial** — superseded by the Recovery event flow; dead readers.

**F13. MINED treated as terminal by REST backfill; a later FAILED is dropped.**
`trade_backfill.py:24` `_SETTLED={MINED,CONFIRMED}` → `apply_confirmed_fill` promotes MATCHED→CONFIRMED; a
subsequent WS FAILED hits `apply_failed_fill` which requires status `MATCHED` (`wallet_store.py:384-386`) →
returns False → phantom fill stays in position until size-down/divergence. `WalletFillProcessor.on_trade`
also ignores MINED (`wallet_store.py:828-829`) so WS MINED doesn't clear inflight — only CONFIRMED/FAILED do.

**F14. Backfilled oversized SELLs corrupt share truth and seed the F5 crash.**
`apply_backfilled_fill` (`wallet_store.py:335-346`) + clamp in `_position_after_backfill` (849-853): a
REST/backfilled SELL larger than the recorded position zeroes the position, but `_commit_confirmed_row`
(348-356) still credits cash at *full* size via `cash_delta(fill)` — ledger cash inflates by the clamped
amount, shares undercount vs on-chain, and the raw-size row is the input that later crashes
`_rebuild_token_position` (F5).

**F15. `apply_and_persist` rollback leaves the ledger ahead of core state.**
`core_session_io.py:77-90` — on snapshot-write failure the in-memory core state is reverted, but the ledger
write inside `apply_*_fill` already committed in its own `with self._conn:` transaction → core vs ledger
diverge until `_position_mismatch` (`session_core.py:621-636`) forces Recovery next cycle. Self-healing,
but the window is a real divergence.

**F16. Restart loses settle-window protection.** `is_settling` keys off in-memory `_last_fill_ts`
(`wallet_store.py`); a restart clears it → a REST write-down for a fill committed just before the crash can
size-down over the confirmed fill (no `fill_precedes_writedown` protection for the write-down itself).
Divergence monitor re-adds within ~80s (`ONCHAIN_EXCESS_BLOCK_ROUNDS=3`, `engine_seams.py:52,738`).

---

## S4 — observability / debuggability gaps

- **O1.** `notify_in_background` (`notify.py:58-60`) is a daemon thread — a crash drops in-flight alerts;
  `send_telegram_message` swallows all failures (37-55). Quiesce/keep_quiet and wedge events have no alert
  path beyond `trader.log`.
- **O2.** Quoter-loop exceptions log `quoter_error` only (`engine.py:336-340` → 0.5s retry); a persistent
  recompute fault is a silent quoting stall per market.
- **O3.** `_dispatch_fill` returns silently when no unacked confirmed outbox row exists
  (`wallet_host.py:776-785`) — the fill is in the ledger but missing from `session.jsonl`/`core_trace`;
  `_journal_fill` also drops when archive token lookup fails (829-836). Tape ≠ ledger in exactly the
  cases debugging needs most.
- **O4.** Stale-cancel watchdog warns once per order at `STALE_CANCEL_SECONDS=30`
  (`session_core.py:125,721-754`); an unprovable cancel re-sends every cycle (`_venue_cancels` re-adds
  `unknown`+`cancel_reason` orders) — indefinite retry with only one log line and rate-limit pressure.
- **O5.** `_open_orders_with_proof` local merge (F9) also hides REST-side disappearance from the journal —
  "proven gone" is never observable.
- **O6.** `apply_book_only_markets`/`_resubscribe_market_ws` (`engine_seams.py:1229-1244`) clears every other
  attached market's cell on resubscribe — one market's attach detaches books for all; a burst of matches
  attaching creates global book gaps. Functional but worth a metric.

---

## Architecture notes (verified-correct mechanisms — kept short)

- Idempotent fills: `INSERT OR IGNORE` by `trade_id`/fill key (`wallet_store.py:52-58,256+`); FAILED writes
  `:reverse` rows; REST CONFIRMED promotes MATCHED in place.
- `FsyncedJsonlWriter` crash-tail truncate + per-record fsync (`archive_paths.py:62,104-110`) — durable, at
  a latency cost (F6).
- `process_lock.py` non-blocking flock; `orchestrator.py` single daemon entry; HTTP/1.1 forced for CLOB
  (`clob_transport.py` — avoids shared HTTP/2 socket stalls).
- Quiesce is fail-closed: `fence_no_orders` unproven → `keep_quiet`, no `execution_cleanup` write
  (`match_worker.py:1161-1204`; boot scan `wallet_host.py:983-999`).
- Recovery is sell-first by design: `apply_recovery` → `sell_only`, `_recovery_exit` cancels BUYs, keeps
  SELLs, sells inventory via `decide_sell`/`reconcile` (`quoting.py:908-920`). Restart exits work.

## Brief question matrix

1. **Order state machine** — place→ack→live→cancel→proven-gone: the durable command trail is written but
   never consumed (F1); unbound results orphan (F2); unbound venue orders never intra-session cancelled (F3);
   unbound fills park buys (F4); restart mid-order → recovery classification works but verify can wedge on
   `unknown_command` (F1) or `matched` (F5); REST backfill is idempotent via fill key; double counting is
   guarded by `INSERT OR IGNORE` + ledger status checks.
2. **Position/cash truth** — user-WS + REST backfill + on-chain divergence monitor; reconcile cadence 20s
   (`reconcile_interval_s`); drift → `_position_mismatch` → Recovery; YES+NO merge via on-chain
   `token_balances` (fork `engine.py:521-543`); dust sweep bounded; late fills → `late_fill` archive record
   or dropped log line (O3). Weak links: F5, F13, F14, F16.
3. **Risk** — `daily_loss_kill`, exposure caps, market notional, ws-stale (30s), user-ws-blind (15s),
   heartbeat (3 fails after 60s grace), `max_order_error_rate=0.25` per-cid. Halts clear on condition
   clearing except `daily_loss` (UTC rollover) and `_halted` (metadata refresh). Halts strand inventory by
   pulling all exits (F11).
4. **Restart/recovery `resume_locked`** — vestigial; the live mechanism is Recovery→sell_only→exit→verify.
   Selling after restart works; buys gated until `accept_if_proven` (F12). Exits preserved.
5. **Latency vs 85ms** — F6: median ~340ms, tail in seconds; the assumption is unreachable at current
   architecture.
6. **Paper realism** — F7: optimistic by construction.
7. **Error handling** — swallowed-exception inventory: `place`→[] (gateway.py:181-183), `cancel`→False
   (216-218), `positions`→{} (496-498), `balance_allowance`→{} (515-517) → `collateral_balance`→0.0 (F8),
   boot `open_orders`/`cancel_all` suppressed (F10), quoter `except`→0.5s retry (engine.py:338-340),
   `send_telegram_message` never raises (O1). Retry-that-can-place-twice: `place` returning [] after a
   server-side post → F1+F3.

## Still needs VPS / live confirmation

- Count wedged `core_command` rows (`dispatch_started`,`consumed=0`) in VPS `wallet.db` — F1 frequency.
- `trader order result orphaned` / `unproven` / `undispatched` rates vs active sessions — F2/F3.
- `pending_ownership` non-empty in `session.jsonl` — F4.
- User-WS `_handle` exception log lines co-occurring with stuck MATCHED fills — F5.
- Signal→place tail on the VPS loop (fsync/sqlite stall correlation) — F6.
- Any session stuck `sell_only` while flat after an RPC hiccup at boot — F12.
