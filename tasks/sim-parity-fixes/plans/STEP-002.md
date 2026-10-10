# STEP-002 plan: prove CONFIRMED when nothing is MATCHED, and a fast ledger proof after an HTTP cancel (A3 + A6)

Code: `esports-trader` `main` at `95c431e3` (clean `git status`). Paths below are relative to `esports-trader/`. `poly-maker` is read-only and is only cited. Planner scratch (copies of `src/` and `tests/` at HEAD and patched, probes, logs, reference diffs): `betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-002/` (below: `work/plan-002/`). Reference diffs: `work/plan-002/step002_src.diff` and `work/plan-002/step002_tests.diff`.

No Figma links: the step has no `designReference`. `progress.txt` has STEP-001 only (commits `e5c3dcee`, `95c431e3`); it does not touch the files below.

## 0. Result in one paragraph

Two changes to the unsettled-BUY proof in `src/trader/wallet_host.py`.

**A3.** On the `below_confirmed` branch of `_apply_unsettled_proofs`, a proof below the CONFIRMED ledger sum is accepted at the CONFIRMED size when the venue id has no MATCHED BUY rows. Example: a REST zero against 17.02 CONFIRMED becomes a proof of 17.02. If a MATCHED row exists, the proof is ignored as today.

**A6.** A row created by a successful HTTP cancel no longer waits `REST_PROOF_MIN_AGE_S` (60 s) or two REST zeros. The host keeps the monotonic time of the cancel response per venue id. A new wrapper around `gateway.open_orders` then proves the row from the local ledger (MATCHED + CONFIRMED). This happens on the first successful snapshot that was sent at least `CANCEL_WS_GRACE_S = 2.0` s after the response and does not list the id. A 2 s timer started by the cancel sets `engine._reconcile_now` only if the row is still unproven by then.

Size: +94/−12 production lines in 3 files and +173/−9 test lines in 3 files. No schema change and no `poly-maker` change. Verified on a scratch copy:
- The full suite without the golden replay has the same 13 environment-only failures as HEAD (§6); 10 new test cases pass.
- basedpyright reports 0 errors, ruff is clean, and no added line has a comment.
- Five single-line mutations of the patch are each caught by at least one new test (§4.4).

## 1. What is broken today (evidence)

### 1.1 A3: a REST short read below CONFIRMED is never accepted

- `_apply_unsettled_proofs` (`src/trader/wallet_host.py:1018-1057`) ignores any proof with `proof.matched_qty < confirmed - HALF_SHARE_TICK` (`:1030-1040`). It logs `ignored` / `below_confirmed` and does not prove the row.
- `collect_rest_buy_proofs` (`src/trader/unsettled_buy_recovery.py:79-133`) reads `get_trades` for one hour. It returns 0 for an order whose trades are missing from that page, and 122.43 for c21, whose CONFIRMED ledger sum is 208.944546.
- Result: the row stays `proven=0`. `unsettled_buy_notional` (`src/trader/core_persistence.py:894-901`, SQL `:810-822`) keeps reserving `(qty − booked) × price`, and the rung is not released.
- This continues until `ORDER_LIFETIME_S` / the stale alert (`unsettled_buy_recovery.py:25-26`, `wallet_host.py:1090-1098`).
- STEP-004 of `live-a-audit-fixes` pinned this behavior on purpose. Its tests are `tests/test_trader_buy_terminal_proof.py:285-309` (WS zero against 5.0 CONFIRMED) and `:344-382` (REST c1 / c21).
- The user's resolved question allows exactly one exception: "proof ниже CONFIRMED отбрасывается, кроме правила A3".

### 1.2 A6: a row from an HTTP cancel waits 60 s and two REST zeros when the WS frame is lost

- `_durable_cancel` (`wallet_host.py:551-574`) inserts the row with `qty = submitted` via `_commit_cancel_reserves` (`:524-548`). It then calls `_apply_unsettled_proofs(())` (`:572-573`). The row is proven only by a WS `CANCELLATION` (`_on_order_terminal`, `:930-953`) or by REST.
- The REST path, `_reconcile_unsettled_buys` (`:1068-1088`), runs only from the positions wrapper `install_rest_fill_recovery` (`src/trader/engine_seams.py:535-566`). It skips rows younger than 60 s (`eligible_rest_rows`, `unsettled_buy_recovery.py:72-76`) and needs a second zero read (`_rest_zero_ids`, `wallet_host.py:732`).
- The poly-maker reconcile interval is 20 s (`reconcile_interval_s`, `poly-maker/src/polymaker/engine.py:577-578`). A lost frame therefore holds the reserve and the rung for 60–100 s.
- 2026-10-08 live B data (`work/plan-002/cancel_ws_lag.py`, output `cancel_ws_lag.out`): 10,313 `unsettled_buys` rows in 10 hours (mean 1,031 per hour; busiest hours 2,080, 1,818 and 1,614).
  - 160 rows never got a WS `CANCELLATION`. 147 of them had a MATCHED order message or a maker trade, and 21 were proven at qty 0 later by REST.
  - 10,153 rows got the frame: 10,041 before the row was written and 112 after it.
  - The 112 late frames arrived at p50 0.177 s, p99 1.248 s, max 1.447 s. 9 were over 1 s and none over 2 s.

### 1.3 Why calling `_reconcile_now` right after the cancel is not enough

poly-maker `_reconcile_loop` (`engine.py:570-623`) runs one pass in this order:
1. `expire_inflight` (`:586`).
2. `gateway.positions()` (`:591`). This is our wrapper, which runs `_pull_missed_fills` (one hour of trades, `engine_seams.py:75`, `:509-532`) and then `_reconcile_unsettled_buys`.
3. `gateway.open_orders()` (`:594`).
4. `replace_open_orders` per token (`:608`).
5. On a forced pass, `_wake_all()` (`:612`).

Consequences:
- The unsettled check of a pass runs before the open_orders snapshot of the same pass. A check inside the positions wrapper would never see a fresh snapshot.
- A forced pass right after the cancel sends its snapshot a few ms after the response. That snapshot is inside the WS-lag window (§2, decision 3), so it cannot prove anything.
- Setting the event after every BUY cancel would force up to about 2,080 passes per hour at peak. Each pass is a one-hour trades backfill, positions, collateral and open_orders, plus a `_wake_all` that wakes every quoter, which the sim does not model.

## 2. Design decisions

1. **The ledger proof runs inside a wrapper on `gateway.open_orders`** (`_wrap_open_orders_proof`), installed right after `install_strict_rest(engine)` (`wallet_host.py:808`).
   - It sees exactly the snapshot it proves from, whichever caller asked for it: the reconcile loop (`engine.py:594`) or `_refresh_token_orders` (`:515`).
   - The strict wrapper `_open_orders_with_proof` (`engine_seams.py:627-646`) raises `RestUnproven` on a failed read, so a failed snapshot proves nothing.
   - `ShutdownLatch.wrap_gateway` (`engine_seams.py:300-330`) and `install_rest_snapshot_stamps` (`:491-506`) do not touch `open_orders`.
2. **The ack time lives in memory** (`WalletHost._cancel_acked_at: dict[str, float]`, `time.monotonic()` right after `await original_cancel` returns).
   - No column and no migration.
   - After a restart the map is empty and the row takes today's REST path (60 s plus a second zero read). That is the safe direction.
   - The map is pruned to unproven open rows on every snapshot, so it stays bounded.
3. **`CANCEL_WS_GRACE_S = 2.0`** in `unsettled_buy_recovery.py` next to `REST_PROOF_MIN_AGE_S`.
   - The snapshot must be sent at or after `acked_at + 2.0`. The user rule only asks for "after the response", so this is stricter.
   - Reason: a fill's WS trade message can still be in flight when the cancel response arrives. The worst observed WS lag behind the row is 1.447 s (§1.2).
   - The ledger is only trusted after the window in which those messages land.
4. **`_reconcile_now` is set from a `call_later(CANCEL_WS_GRACE_S)` timer and only when a cancelled row is still unproven and unresolved** (`_force_reconcile_if_unproven`). This deviates from the step text "после успешной HTTP-отмены звать `_reconcile_now`":
   - An immediate set would produce a snapshot inside the grace, which proves nothing (§1.3).
   - With the timer the forced pass happens once the snapshot can count. Its `_pull_missed_fills` runs before its open_orders, so a fill visible to REST is in the ledger before the ledger is read.
   - Forced passes drop from one per BUY cancel (10,313 in 10 hours) to one per row whose frame is lost: at most 160 in 10 hours, and about 13 if the 147 rows with a MATCHED order message or a maker trade are proven by WS first.
   - The timer returns early after `close()` (`_closed`, `wallet_host.py:738`, set before the store and engine are released).
5. **Positive ledger proofs get the same snapshot gate as zero ones.** For example, a ledger of 17.07 on a 20-share order is proven only from a snapshot that does not list the id. The order cannot be partially filled and still live, and one rule is simpler than two.
6. **A3 changes the shared branch**, so it applies to WS, REST and ledger proofs alike. A ledger proof is never below CONFIRMED, because MATCHED + CONFIRMED ≥ CONFIRMED.
   - The accepted proof carries the raised size, so the existing `applied` diag record logs `matched = confirmed`.
   - With a MATCHED row present, the proof stays ignored. The next reconcile after that fill is CONFIRMED (or FAILED) decides.
   - The STEP-004 rules stay: a REST zero needs two reads (`test_rest_zero_needs_two_reads`, `tests/test_trader_buy_terminal_proof.py:312-341`, unchanged and green).
7. **The snapshot is the merged REST + local list from `_open_orders_with_proof`.**
   - poly-maker drops a cancelled order from local state right after `gateway.cancel` returns ok (`engine.py:463-466`).
   - A stale local copy (for example one re-added by a late WS frame) can only delay the proof to the REST path, never accept one early.
8. **New diag stage `ledger_proof`** with `order_id`, `matched` and `waited_s` (seconds from the cancel response to the snapshot send). The `applied` record already follows from `_apply_unsettled_proofs`.

## 3. Changes

### 3.1 `src/trader/core_persistence.py`: two ledger reads after `read_confirmed_buy_qty` (`:861-867`)

Same style as `read_confirmed_buy_qty`:

```python
def count_matched_buys(conn: sqlite3.Connection, venue_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM fill_ledger "
        "WHERE maker_order_id=? AND side='BUY' AND status='MATCHED'",
        (venue_id,),
    ).fetchone()
    return int(row[0])


def read_booked_buy_qty(conn: sqlite3.Connection, venue_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(size), 0.0) FROM fill_ledger "
        "WHERE maker_order_id=? AND side='BUY' AND status IN ('MATCHED', 'CONFIRMED')",
        (venue_id,),
    ).fetchone()
    return float(row[0])
```

`MATCHED` + `CONFIRMED` is the same "booked" set as `_UNSETTLED_NOTIONAL_SQL` (`:810-822`). FAILED, SUPERSEDED and MERGED (`src/trader/wallet_store.py:49-53`) are excluded.

### 3.2 `src/trader/unsettled_buy_recovery.py`

Add `CANCEL_WS_GRACE_S = 2.0` after `REST_PROOF_MIN_AGE_S = 60` (`:24`).

### 3.3 `src/trader/wallet_host.py`

- Imports:
  - add `count_matched_buys` after `CoreSessionKey,` (`:57`);
  - add `read_booked_buy_qty` after `prove_unsettled_buy,` (`:64`);
  - add `CANCEL_WS_GRACE_S` before `UNSETTLED_ALERT_AGE_S,` (`:143`).
- `_durable_cancel` (`:551-574`):
  - add `acked_at = time.monotonic()` right after `ok = await original_cancel(order_ids)` (`:566`);
  - in `if inserted:` (`:572-573`), after `host._apply_unsettled_proofs(())`, add `host._watch_cancelled_buys(tuple(buy.venue_id for buy in buys), acked_at)`.
  - `inserted` is `ok and bool(buys)` (`:524-548`), so only a successful cancel of BUYs is watched.
- `WalletHost.__init__`: add `self._cancel_acked_at: dict[str, float] = {}` after `self._rest_zero_ids` (`:732`).
- `_install_runtime_seams` (`:778-819`): add `self._wrap_open_orders_proof()` right after `install_strict_rest(engine)` (`:808`).
- New method after `_wrap_quote_journal` (ends `:922`):

```python
    def _wrap_open_orders_proof(self) -> None:
        gateway = self.engine.gateway
        original_open_orders = gateway.open_orders

        async def open_orders() -> list[OpenOrder]:
            sent_at = time.monotonic()
            live = await original_open_orders()
            self._prove_cancelled_buys(sent_at, frozenset(order.order_id for order in live))
            return live

        gateway.open_orders = open_orders
```

- A3 in `_apply_unsettled_proofs`: replace the gate and append (`:1030-1043`) with:

```python
                matched_qty = proof.matched_qty
                if matched_qty < confirmed - HALF_SHARE_TICK:
                    if count_matched_buys(conn, proof.venue_id) > 0:
                        log_event(
                            "ignored",
                            kind="buy_proof",
                            order_id=proof.venue_id,
                            matched=matched_qty,
                            confirmed=confirmed,
                            reason="below_confirmed",
                        )
                        continue
                    matched_qty = confirmed
                prove_unsettled_buy(conn, proof.venue_id, matched_qty)
                accepted_ids.add(proof.venue_id)
                accepted.append(BuyExecutionProof(venue_id=proof.venue_id, matched_qty=matched_qty))
                progressed.append(row.session_id)
```

- Three new methods after `_apply_unsettled_proofs` (before `_wake_unsettled_sessions`, `:1059`):

```python
    def _watch_cancelled_buys(self, venue_ids: tuple[str, ...], acked_at: float) -> None:
        for venue_id in venue_ids:
            self._cancel_acked_at[venue_id] = acked_at
        asyncio.get_running_loop().call_later(
            CANCEL_WS_GRACE_S, self._force_reconcile_if_unproven, venue_ids
        )

    def _force_reconcile_if_unproven(self, venue_ids: tuple[str, ...]) -> None:
        if self._closed:
            return
        for venue_id in venue_ids:
            row = get_unsettled_buy(self.store._conn, venue_id)
            if row is not None and not row.proven and not row.resolved:
                self.engine._reconcile_now.set()
                return

    def _prove_cancelled_buys(self, sent_at: float, listed: frozenset[str]) -> None:
        if not self._cancel_acked_at:
            return
        conn = self.store._conn
        unproven = {row.venue_id for row in open_unsettled_buys(conn) if not row.proven}
        self._cancel_acked_at = {
            venue_id: acked_at
            for venue_id, acked_at in self._cancel_acked_at.items()
            if venue_id in unproven
        }
        proofs: list[BuyExecutionProof] = []
        for venue_id, acked_at in sorted(self._cancel_acked_at.items()):
            if sent_at < acked_at + CANCEL_WS_GRACE_S or venue_id in listed:
                continue
            matched_qty = read_booked_buy_qty(conn, venue_id)
            log_event(
                "ledger_proof",
                order_id=venue_id,
                matched=matched_qty,
                waited_s=sent_at - acked_at,
            )
            proofs.append(BuyExecutionProof(venue_id=venue_id, matched_qty=matched_qty))
        if proofs:
            self._apply_unsettled_proofs(tuple(proofs))
```

Notes for the implementer:
- `_durable_cancel` is async, so `get_running_loop()` always has a loop.
- `_apply_unsettled_proofs` already does the resolve, the `_deliver_*` calls and the session wakes, so nothing else changes on the worker side.
- `engine._reconcile_now` is the poly-maker `asyncio.Event` (`engine.py:85`). This step only calls `.set()` on it.
- `wallet_host.py` grows from 1743 to about 1818 lines. Moving the unsettled-BUY proof methods into their own module is a separate refactor and out of scope.

### 3.4 Not changed

- `poly-maker`: frozen.
- The REST path (`_reconcile_unsettled_buys`, `collect_rest_buy_proofs`) and its 60 s age and two-read zero rule.
- `unsettled_buys` schema (`core_persistence.py:97-109`).
- Paper mode.
- `cancel_asset` / `cancel_all` paths: they do not create rows through `_commit_cancel_reserves`.

## 4. Tests

Reference text: `work/plan-002/step002_tests.diff`. Two fake-host fixtures need the new attributes:
- `open_buy_rig` (`tests/test_trader_unsettled_buy_activation.py:142-146`) gets `host._cancel_acked_at = {}` and `host._closed = False`, because the cancel path writes the map and the end-to-end test fires the timer.
- `_host` (`tests/test_trader_unsettled_buy_recovery.py:151-157`) gets `host._cancel_acked_at = {}`.

No other fake host reaches the new code: the full suite is green apart from environment-only failures.

### 4.1 `tests/test_trader_buy_terminal_proof.py` (A3)

- Add constants after `C21_REST` (`:56`): `C100 = "c100"`, `C100_REQUESTED = 215.38`, `C100_CONFIRMED = 17.02`.
- `test_proof_below_confirmed_is_ignored` (`:285-309`) becomes `test_proof_below_confirmed_is_ignored_while_a_fill_is_matched`.
  - It adds a 3.0 MATCHED fill `t2:<venue>` via `rig.store.apply_matched_fill`.
  - The reserve assertion becomes `(rig.qty - 8.0) * rig.price`. The rest stays: row open, one `below_confirmed` record with matched 0.0 and confirmed 5.0.
- New `test_ws_proof_below_confirmed_proves_confirmed_without_a_matched_fill`: same setup without the MATCHED fill. A WS zero gives a row of `(5.0, True, True)`, reserve 0, no `below_confirmed` record, and one `applied` record with matched 5.0.
- `test_rest_short_read_below_confirmed_is_not_proof` (`:344-382`) becomes `..._proves_confirmed_without_a_matched_fill` with both payloads:
  - after REST pass 1, c21 is `(C21_CONFIRMED, True, True)`;
  - after pass 2, c1 is `(ACCEPTED, True, True)`, which is the second zero read;
  - there are no `below_confirmed` records and `unsettled_buy_notional` is 0.
- New `test_c100_rest_zero_proves_confirmed_once_no_fill_is_matched[pending]`: a row of 215.38, aged 70 s, with 17.02 CONFIRMED and REST returning nothing.
  - Check the reserve before the passes: `(215.38 − booked) × row.price`.
  - `pending=False`: two passes give a row of `(17.02, True, True)` and reserve 0.
  - `pending=True` (one 5.0 MATCHED fill): two passes leave the row at `(215.38, False, False)` with one `below_confirmed` record, matched 0.0 and confirmed 17.02.
  - Then `apply_confirmed_fill` on that fill and one more pass give `(22.02, True, True)` and reserve 0.

### 4.2 `tests/test_trader_unsettled_buy_recovery.py` (A6, unit)

- Import `CANCEL_WS_GRACE_S`. Add `ACKED = 1_000.0` next to `PRICE` (`:44`).
- New helper `_cancelled_buy(tmp_path)` after `_freeze_now` (`:168-169`). It seeds `venue-1` (20 shares, aged 1 s, ack `ACKED`) and `no-ack` (20 shares, no ack), uses `_Trades({TOKEN: []})`, and registers worker `sess`.
- `test_cancelled_buy_proves_the_ledger_zero_before_the_rest_age`:
  - `_reconcile_unsettled_buys` leaves `venue-1` open, because the row is younger than 60 s.
  - `_prove_cancelled_buys(ACKED + CANCEL_WS_GRACE_S, {"other"})` gives `(0.0, True, True)`.
  - `no-ack` is untouched, `wakes == ["sess"]`, and `trades.params == []`.
- `test_cancelled_buy_proves_the_ledger_fill_and_resolves_on_confirm`:
  - 17.07 MATCHED (`fill_key("t1", "venue-1")`) gives `(17.07, True, False)`.
  - After `apply_confirmed_fill` and `_apply_unsettled_proofs(())` the row is `(17.07, True, True)`.
  - `trades.params == []`.
- `test_cancelled_buy_waits_for_a_later_snapshot_without_the_order`, with three cases:
  - `sent_before_the_cancel_ack`: sent at `ACKED − 0.5`;
  - `inside_the_ws_grace`: sent at `ACKED + grace − 0.001`;
  - `order_still_listed`: sent at `ACKED + grace` with `venue-1` listed.
  - Each case leaves the row at `(20.0, False, False)` with no wake. A proper snapshot afterwards gives `(0.0, True, True)`.

### 4.3 `tests/test_trader_unsettled_buy_activation.py` (A6, end-to-end through the cancel)

New `test_http_cancel_proves_the_ledger_zero_from_a_later_snapshot[frame]`, placed before `test_ws_skip_sell_and_unknown_venue` (`:307`):
- Setup:
  - monkeypatch `trader.wallet_host.CANCEL_WS_GRACE_S` to 0.0;
  - `rig.host.engine._reconcile_now = asyncio.Event()`;
  - a fake `gateway.open_orders` that returns `[]`;
  - `rig.host._wrap_open_orders_proof()`.
- Inside one `asyncio.run`:
  - run `_durable_cancel` with an ok cancel;
  - in the `arrives` case, deliver `_msg(venue, "0")`;
  - `await asyncio.sleep(0.01)`, so the timer fires;
  - assert `reconcile_now.is_set() is (frame == "lost")`;
  - await the wrapped `open_orders()`.
- Both cases: the row is `(0.0, True, True)`, and after `finish_cycle(_sources(rig, NS))` the BUY is gone from the core.
- Use `sleep(0.01)`, not `sleep(0)`. A zero-delay timer runs after the task resumes from `sleep(0)`, so the assertion would run first.

### 4.4 Fail-before evidence (scratch)

- The new tests on HEAD: the recovery and terminal-proof modules fail to import (no `CANCEL_WS_GRACE_S`), and both end-to-end cases fail.
- Single-line mutations of the patched `wallet_host.py`, run on the three files (`work/plan-002/mut/`):

| Mutation | Caught by |
|---|---|
| A3 off (`count_matched_buys(...) > 0` → `True`) | 5 tests: the WS case, both REST payloads, both c100 cases |
| A3 ignores MATCHED (`→ False`) | the "while a fill is matched" test and c100[True] |
| grace off | `inside_the_ws_grace` |
| `listed` check off | `order_still_listed` |
| timer unconditional | `arrives` |

## 5. Behavior changes and risks

1. **Early release with a lost fill.** If the WS trade message and the `CANCELLATION` are both lost, and REST `get_trades` does not yet show the trade at ack + 2 s (the forced pass backfills before it snapshots), the ledger proof is short. The row resolves early and the reserve is freed early. The backfill (`_pull_missed_fills`, one-hour lookback, every reconcile pass) books the fill as a late fill on the first pass that sees it in REST. Until then the budget overstates free cash by that fill's notional.
2. **An HTTP cancel answered "not found / already canceled"** counts as ok (`_cancel_with_proof`, `engine_seams.py:601-624`). If the venue still has the order live but REST open_orders lags, the snapshot would not list it. The 2 s grace covers normal REST lag; a longer lag is a residual risk.
3. **The forced pass still calls `_wake_all()`** (`engine.py:612`), roughly 13 to 160 times per 10 hours instead of 0 today. These are extra quoter wakes that the sim does not model.
4. **After a restart the ack map is empty.** Rows from cancels before the restart wait for the REST path, as today.
5. **A3 releases rows whose remaining fills are FAILED or missing from the ledger** as soon as nothing is MATCHED. This is intended by the step. A trade that arrives later goes through the late-fill path.
6. **Clocks**: `acked_at` and `sent_at` both use `time.monotonic()`, so wall-clock steps cannot reorder them. The `time.time()` freeze in the tests (`_freeze_now`) does not affect them.

## 6. Verification

Run from `esports-trader/` in the main checkout. Do not run the suite during a live map on the VPS, and set `PYTEST_N` only locally.

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# targeted (scratch: 202 passed; the 13 env-only failures below are in two of these files)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_trader_buy_terminal_proof.py tests/test_trader_unsettled_buy_recovery.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_wallet_host.py \
  tests/test_trader_durable_place.py tests/test_trader_match_lifecycle.py

# full suite without golden replay ("Tests pass" for STEP-001..013; scratch: 3229 passed in 25 s)
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors)
uv run python -m basedpyright

# ruff check + format + repo hooks on the touched files
uv run python -m pre_commit run --files src/trader/core_persistence.py src/trader/unsettled_buy_recovery.py \
  src/trader/wallet_host.py tests/test_trader_buy_terminal_proof.py \
  tests/test_trader_unsettled_buy_recovery.py tests/test_trader_unsettled_buy_activation.py

# no new comments (expect no output)
git diff -U0 -- src tests | rg '^\+[^+].*#'
```

Expected result: 0 failures.
- On the scratch copy the full run gave 3229 passed and 13 failed. HEAD in the same scratch setup failed the identical 13 tests (`diff work/plan-002/base.failed work/plan-002/patch.failed` is empty).
- All 13 fail only because the copy has no `ODDIN_BRAND_TOKEN` (`test_trader_wallet_host.py` lol/run tests and `test_user_stream_is_bound_on_the_host_run_path`). They pass in the real checkout, as in STEP-001.

This step needs no backtest, training or data rebuild. The golden replay is not affected; STEP-014 owns it.

## 7. Commit

In `esports-trader` on `main`, add only the 6 paths listed above (`git add <paths>`). Use the repo's message style, for example `fix: STEP-002 - prove CONFIRMED when nothing is matched and prove a cancelled buy from the ledger`. Do not push.
