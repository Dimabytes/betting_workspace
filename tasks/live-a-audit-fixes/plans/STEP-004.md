# STEP-004 plan: LIVE-01 keep an early BUY terminal proof and prove with venue quantities

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, HEAD `bd64ef9c` (STEP-001..003 done). All code paths below are relative to that worktree. Audit paths are relative to `betting_workspace/runs/live-a-money-audit-20261009/`. Planner scripts and outputs are in `betting_workspace/tasks/live-a-audit-fixes/orchestrate/work/plan-step-004/`.

No Figma links: the step has no `designReference`.

## 0. Result in one paragraph

A venue terminal `UPDATE MATCHED` often arrives before the `unsettled_buys` row exists, and `_on_order_terminal` drops it. The row then carries the requested size (304.34), the ledger confirms the venue size (304.33), the 0.005 cover test fails, and only the REST read after 60 s releases the rung, usually with a wrong zero. The fix has three small parts, all in the host and all through existing paths. (1) A terminal BUY proof with no row opens the row at once and proves it, as the CANCELLATION path already does today. The row lives in sqlite, so it survives a restart. (2) Every proof, WS or REST, goes through one gate in `_apply_unsettled_proofs`: a proof below the CONFIRMED ledger sum is not proof. (3) A REST zero counts only when two reconcile passes in a row read it. The existing `BuySettled` path then drops the BUY (live or gone) once CONFIRMED covers the proven size. No schema, strategy, core, tolerance, timer or fork change. Expected size: about +50/−40 production lines in 3 files, one new test file, 3 updated tests. Probes on the real core show c1 released at CONFIRMED (≈6 s after the fill) instead of 79.9 s.

## 1. What is broken today (evidence)

Code on HEAD:

- `_on_order_terminal` returns when there is no row and the message is not a CANCELLATION: `src/trader/wallet_host.py:939-942`. A full `UPDATE MATCHED` is never a CANCELLATION, so its proof is lost.
- The row is created with the requested size `submitted_qty`: `src/trader/wallet_host.py:511` (cancel path) and `:967` (WS cancel path).
- Cover rule: `CONFIRMED sum >= u.qty - HALF_SHARE_TICK` (`src/trader/core_persistence.py:876-890`, `HALF_SHARE_TICK = 0.005` in `src/shared/utils/trading.py:16`). 304.33 ≥ 304.335 is false.
- REST reads only unproven rows aged ≥ 60 s (`src/trader/unsettled_buy_recovery.py:25`, `:73-77`). It runs inside the positions poll (`src/trader/engine_seams.py:528-540`), every `reconcile_interval_s = 20.0` (`config/trading.toml:10`).
- A venue id absent from the trades read gets sum `0.0` (`src/trader/unsettled_buy_recovery.py:190`, `:254-261`). `prove_unsettled_buy` writes 0, and `resolve_settled_buys` then resolves it, because `CONFIRMED >= 0 - 0.005` is always true. That is the "wrong zero".
- The rung stays held while any BUY with that level is in state (`src/strategy/lifecycle.py:250-253`). `BuySettled` drops the BUY in status live/canceling/unknown/gone when `filled >= matched - 0.005` (`src/strategy/lifecycle.py:364-373`). `_settled_buy_events` emits it for every active BUY whose row resolved (`src/trader/session_core.py:566-581`).
- c1 stays active in core after its fill: `share_floor(304.33) < share_floor(304.34)` (`src/strategy/lifecycle.py:664`).

Audit fixture 9034957701 c1 (venue `0x8a92df7f…`), from `evidence/engine-journal-oct5-9.jsonl.gz` and `evidence/wallet-live.db` (read-only):

| ts (…) | event |
| --- | --- |
| 644.781 | order PLACEMENT, `original_size` 304.33 (requested 304.34) |
| 645.838 | trade MATCHED 304.33, then order `UPDATE MATCHED` 304.33/304.33 |
| 647.131 | row created, qty 304.34 (1.29 s after the terminal) |
| 647.887 | trade MINED |
| 651.724 | trade CONFIRMED 304.33 |
| 727.033 | row resolved by REST proof qty 0.0 (hold 79.9 s) |

`probe_db.py` on the wallet DB: `confirmed 304.33 row (0.0, 1, 1, …) hold_s 79.9`.

Venue message contract (`journal_status.py`, `matched_partial.py`, all BUY order messages Oct 5–9):

- Status `MATCHED` is terminal. 503 `UPDATE MATCHED` with `size_matched == original_size`. 62 more with `size_matched < original_size`, all with a gap below 0.05 shares (42 below 0.01, 20 in 0.01–0.05), for example 107.680572 of 107.69. None of the 62 is followed by a CANCELLATION.
- A partial fill on a resting order carries status `LIVE` (365 messages), so the current parser (`_terminal_buy`, `src/trader/unsettled_buy_recovery.py:137-147`) does not treat it as terminal.

Tail join (`work/orchestrator/settle-tail-independent.json`, 63 rows that waited > 60 s):

- 45 had a terminal before the row, 0 after it.
- 40 rows were proven 0 although they had CONFIRMED fills. 39 of the 40 had a terminal before the row. The one without: `9033909150 c100`, submitted 215.38, confirmed 17.02.
- 17 rows were genuine zero-fill cancels. None had a terminal at all, so only REST can prove them.
- 45 of 45 early-terminal rows have `confirmed >= terminal size_matched - 0.005`. They all resolve at CONFIRMED under the planned rule.
- Terminal lead before the row: min 0.02 s, median 0.16 s, max 26.41 s. HEAD hold after the row: 60.5–80.1 s.
- REST also returns positive sums below CONFIRMED: c21 122.43 vs 208.944546, c34 119.27 vs 202.882904, c44 32.0 vs 344.812382, another c21 227.31 vs 263.135. All four had an early terminal.
- `get_trades` follows `next_cursor` to `END_CURSOR` (`.venv/…/py_clob_client_v2/client.py:577-607`). The shortfall is not paging. Its cause stays open (FINAL_REPORT open question 2), and this step does not claim to fix it.

## 2. Changes

### 2.1 `src/trader/core_persistence.py`: one read helper

Add next to `prove_unsettled_buy`:

```python
def read_confirmed_buy_qty(conn: sqlite3.Connection, venue_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(size), 0.0) FROM fill_ledger "
        "WHERE maker_order_id=? AND side='BUY' AND status='CONFIRMED'",
        (venue_id,),
    ).fetchone()
    return float(row[0])
```

Same table, columns and status filter as `resolve_settled_buys`. No schema change.

### 2.2 `src/trader/wallet_host.py`: an early terminal opens the row

In `_on_order_terminal`, replace `:939-943`:

```python
        if row is None:
            self._open_unsettled_buy(venue_id, proof, cancelled=cancellation is not None)
            return
```

Replace `_open_cancelled_buy` (`:949-979`) with:

```python
    def _open_unsettled_buy(
        self, venue_id: str, proof: BuyExecutionProof | None, *, cancelled: bool
    ) -> None:
        worker = _worker_for_order_ids(
            self.engine, self._worker_by_token, self._worker_by_cid, [venue_id]
        )
        core = None if worker is None else worker.core
        owner = None if core is None else core.find_owned_order(venue_id)
        if worker is None or core is None or owner is None or owner.side != "BUY":
            log_event("ignored", kind="buy_proof", order_id=venue_id, reason="no_owner")
            return
        conn = self.store._conn
        with conn:
            insert_unsettled_buy(
                conn,
                venue_id=venue_id,
                session_id=worker._cid,
                token_id=core.token_id(owner.token_index),
                price=owner.price,
                qty=owner.submitted_qty,
            )
        if cancelled:
            worker.note_cancel_result([venue_id], True)
        self._apply_unsettled_proofs(() if proof is None else (proof,))
```

- The cancel path keeps today's behavior: insert, mark the BUY gone, apply the proof.
- A terminal without a cancellation (`UPDATE MATCHED`, `UPDATE CANCELED`) inserts and proves the row but does not touch the core order. The core keeps the BUY until `BuySettled` drops it.
- The inline `prove_unsettled_buy` block and its `stored.proven` / `stored.resolved` guards go away. They are dead: `row is None` was checked in the same synchronous call. All proofs now pass through the one gate in §2.3.
- The insert and the proof are two commits. If the process dies between them, the row stays unproven at the requested size. That is the safe direction: the rung and the reserve stay held and REST proves the row later.

### 2.3 `src/trader/wallet_host.py`: one proof gate in `_apply_unsettled_proofs`

Inside the existing loop, after the `row is None or row.proven` check:

```python
                confirmed = read_confirmed_buy_qty(conn, proof.venue_id)
                if proof.matched_qty < confirmed - HALF_SHARE_TICK:
                    log_event(
                        "ignored",
                        kind="buy_proof",
                        order_id=proof.venue_id,
                        matched=proof.matched_qty,
                        confirmed=confirmed,
                        reason="below_confirmed",
                    )
                    continue
```

- Keep the accepted proofs in a list. After the `with conn:` block commits, log one `applied` record per accepted proof: `kind="buy_proof"`, `order_id`, `requested` (the row qty before the proof), `matched`. Do not log `applied` inside the transaction. STEP-003 review flagged that pattern.
- Only CONFIRMED is a lower bound. MATCHED fills can still fail, so a proof below MATCHED + CONFIRMED is not a conflict.
- A proof above CONFIRMED is accepted. More fills may confirm, and the FAILED case keeps its path (`_RESOLVE_FAILED_BUYS_SQL`, `src/trader/core_persistence.py:832-848`).
- Import `HALF_SHARE_TICK` from `shared.utils.trading` and `read_confirmed_buy_qty` from `trader.core_persistence`.

### 2.4 `src/trader/wallet_host.py`: a REST zero needs two reads

Add `self._rest_zero_ids: set[str] = set()` in `WalletHost.__init__` next to `_worker_by_token` (`:724`). In `_reconcile_unsettled_buys`, between `collect_rest_buy_proofs` and `_apply_unsettled_proofs`:

```python
        zero_ids = {proof.venue_id for proof in proofs if proof.matched_qty == 0.0}
        for venue_id in sorted(zero_ids - self._rest_zero_ids):
            log_event("ignored", kind="buy_proof", order_id=venue_id, reason="first_zero")
        repeated = tuple(
            proof
            for proof in proofs
            if proof.matched_qty > 0.0 or proof.venue_id in self._rest_zero_ids
        )
        self._rest_zero_ids = zero_ids
        self._apply_unsettled_proofs(repeated)
```

- A positive REST sum still proves on one read, through the gate in §2.3.
- A zero proves only when the previous pass also read zero for that venue id. A failed token read, a parse error or a restart clears the memory, so two fresh reads are needed. That is the safe direction.
- The set holds only the last pass's zeros, so it does not grow.

### 2.5 `src/trader/unsettled_buy_recovery.py`

Delete the ponytail comment at `:23-24`. §2.4 implements its upgrade path. No other change: both parsers stay as they are.

### 2.6 Comments

Add no comments or docstrings in new code. Delete the docstrings of the functions this step touches: `_open_cancelled_buy` (replaced), `_deliver_cancelled_buy` (handed over by `orchestrate/reports/nocomments-step-003.md:22`), `_apply_unsettled_proofs` and `_reconcile_unsettled_buys`.

### 2.7 Not changed

- `HALF_SHARE_TICK`, the cover SQL, `REST_PROOF_MIN_AGE_S = 60`. The fast path is the WS proof, not a wider tolerance or a shorter timer. The non-goals forbid both.
- `_commit_cancel_reserves`: `INSERT OR IGNORE` already keeps an earlier proven row (pinned by `tests/test_trader_unsettled_buy_activation.py::test_ws_during_http_keeps_the_proved_row`).
- Strategy, core, `session_core`, checkpoint, sqlite schema, B worker, fork.

## 3. Requested, accepted and proven quantities

| quantity | where it lives | used for |
| --- | --- | --- |
| requested (304.34) | core `submitted_qty`; row `qty` while unproven | worst-case reserve; cover target until a proof arrives |
| venue accepted (304.33) | `original_size` in every venue order message; logged raw in the STEP-001 `received` record | not a release input |
| proven (304.33) | row `qty` after `prove_unsettled_buy` | cover target (`CONFIRMED >= qty - 0.005`) and `BuySettled.matched_qty` |

Why the release uses `size_matched` and not `original_size`: 62 terminal `MATCHED` messages have `size_matched` below `original_size` (gap up to 0.05). The ledger confirms `size_matched`. A cover against `original_size` would fail by up to 0.05 on those orders, which is the same bug again. So no new column. The `applied kind=buy_proof` record carries `requested` and `matched`. It carries the same `in_seq` as the raw `received` record, which holds `original_size`, because `log_event` copies `in_seq` from the user-stream scope (`src/trader/diag.py:123-135`, `src/trader/engine_seams.py:155-163`).

## 4. Outcome per path after the fix

| path | HEAD | after the fix |
| --- | --- | --- |
| terminal MATCHED, then cancel (c1) | proof dropped; row 304.34 open after CONFIRMED; REST zero at 60–80 s | row proven 304.33 at the terminal; resolved at CONFIRMED; c1 dropped on the next cycle |
| cancel, then terminal | proven, resolved at CONFIRMED | unchanged |
| terminal MATCHED, no cancel | core plans a cancel for the 0.01 leftover | row proven; at CONFIRMED `BuySettled` drops the live c1; no cancel |
| WS CANCELLATION with qty, before or after the row | proven | unchanged (now through the gate) |
| WS CANCELLATION without qty | row at requested size, held | unchanged |
| WS proof below CONFIRMED | proven, often resolved as 0 | ignored `below_confirmed`; row stays at requested size |
| REST found sum ≥ CONFIRMED | proven on one read | unchanged |
| REST zero, no CONFIRMED fills | proven on one read | proven on the second consecutive read (+20 s) |
| REST zero or short sum below CONFIRMED | proven 0 or short, resolved | ignored `below_confirmed`; row stays open, alert at 600 s |
| repeated terminal / repeated CONFIRMED | no change | no change |

Probe output (`probe_c1.out`, real core, the planned methods swapped in at runtime, nothing written in the worktree):

```
HEAD terminal_first
  confirmed +5.9s        row=(304.34, False, False) c1=('gone', 304.33) rung=c1
  next cycle             row=(304.34, False, False) c1=('gone', 304.33) rung=c1
HEAD terminal_no_cancel
  next cycle             row=None c1=('canceling', 304.33) rung=c1
  plan cancels ('0x8a92df7fcf1c65c44f60b1683cbe2fc767d4c1d466cfffacf8f687259c0d409e',)
PLANNED terminal_first
  terminal (no row)      row=(304.33, True, False) c1=('live', 304.33) rung=c1 unsettled_usd=0.0000 core_reserve=0.0046
  cancel +1.29s          row=(304.33, True, False) c1=('gone', 304.33) rung=c1 unsettled_usd=0.0000 core_reserve=0.0000
  confirmed +5.9s        row=(304.33, True, True) c1=('gone', 304.33) rung=c1
  next cycle             row=(304.33, True, True) c1=None rung=None
PLANNED terminal_no_cancel
  confirmed +5.9s        row=(304.33, True, True) c1=('live', 304.33) rung=c1
  next cycle             row=(304.33, True, True) c1=None rung=None
  plan cancels ()
```

`probe_rest.out` (rows aged 70 s; `filled` has CONFIRMED 304.33 and is absent from REST; `zero` is absent; `found` reads 3):

```
HEAD pass 1:    filled=(0.0, True, True) zero=(0.0, True, True) found=(3.0, True, False)
PLANNED pass 1: filled=(304.34, False, False) zero=(8.0, False, False) found=(3.0, True, False)
PLANNED pass 2: filled=(304.34, False, False) zero=(0.0, True, True) found=(3.0, True, False)
```

Existing tests with the planned methods loaded as a pytest plugin (`planned_patch.py`): 330 passed, 3 failed (exactly the 3 tests in §6.2). B tests `test_trader_two_sided_host.py` and `test_trader_two_sided_worker.py`: 29 passed.

## 5. Hard parts and edge cases

1. **A row for a BUY that is still live.** Today rows exist only after a cancel. Now a terminal opens one while the core still holds the BUY. `apply_buy_settled` already accepts status `live`, and `_settled_buy_events` already scans all active BUYs, so no core change is needed. `find_owned_order` also returns a retained record, so a BUY that the core already dropped on a full fill gets a row too. That row resolves at CONFIRMED and changes nothing in the core. It adds about one sqlite row per filled BUY (503 full + 62 dust terminals in 4 days).
2. **Reserve while the row and the active order coexist.** Core reserve is `(submitted - filled) * price` for non-gone BUYs (`src/strategy/budget.py:13-19`). The row adds `(qty - booked) * price`, where booked = MATCHED + CONFIRMED (`src/trader/core_persistence.py:818-830`). If the terminal arrives before the trade books, the matched notional counts twice until the trade message books it. For c1 the journal has the trade line first, in the same millisecond. The error is toward more reserve, never less.
3. **Proof before CONFIRMED.** A proven row waits for CONFIRMED. A missed WS trade still reaches the ledger: `_pull_missed_fills` backfills recent trades on every positions poll (`src/trader/engine_seams.py:485-508`, `:531`). This matches today's proven rows (`test_proven_row_resolves_from_backfill_without_a_proof_fetch`).
4. **Pre-binding terminal.** `find_owned_order` needs the core venue binding. 17 of 532 BUY terminal `MATCHED` messages arrived less than 0.2 s after their PLACEMENT (`placement_gap.py`). If such a terminal beats the POST result, the host logs `ignored reason=no_owner`. The later cancel row then goes through REST, and a found positive sum proves it on one read. Not fixed here (§8).
5. **Held conflict.** The c100 shape (REST zero, CONFIRMED 17.02, no WS terminal) stays open. The 600 s alert fires (`UNSETTLED_ALERT_AGE_S`). The rung and `(qty - booked) * price` stay reserved until a later read agrees or the operator acts. It was 1 of 63 tail rows. Releasing it needs a new trust rule, for example "prove the CONFIRMED sum after N equal reads". That changes what counts as proof, so it is the user's decision (§8).
6. **Two-read cost.** The 17 genuine zero rows wait one more positions poll, about 80–100 s instead of 60–80 s. The poll runs every `reconcile_interval_s = 20.0` (`config/trading.toml:10`), so the two reads are normally 20 s apart. Accepted.
7. **Idempotency.** A proven row is never proven again (`row.proven` check). `resolve_settled_buys` touches only `resolved=0`. `apply_buy_settled` does nothing once the order left state. `_wake_unsettled_sessions` wakes each session once per call. `INSERT OR IGNORE` keeps the first row.
8. **STEP-002 / STEP-003.** The SELL branch still runs first and is unchanged. A late accept binds the venue id through `record_dispatch_results` and `note_place_result`, and the BUY then takes the normal path. A cancel that fails (`ok=False`) after an early proof keeps the proven row, as `test_partial_proof_survives_a_late_cancel_timeout` already pins.

## 6. Tests

Write the new tests first and run them on HEAD. Expected HEAD failures: c1 `terminal_first`, no-cancel, WS conflict, REST `below_confirmed`, REST two reads, restart and the integration test. `row_first` passes on HEAD and stays as a regression test.

### 6.1 New file `tests/test_trader_buy_terminal_proof.py`

Reuse the rigs: `open_buy_rig`, `run_cancel`, `add_order`, `_msg`, `_sources` from `test_trader_unsettled_buy_activation`; `_host`, `_seed`, `_Trades`, `_flags`, `NOW` from `test_trader_unsettled_buy_recovery`; `_arm_pending`, `_Venue`, `_wire` from `test_trader_durable_place`. Capture diagnostics with `monkeypatch.setattr("trader.wallet_host.log_event", capture)`. The c1 constants: venue `0x8a92df7fcf1c65c44f60b1683cbe2fc767d4c1d466cfffacf8f687259c0d409e`, price 0.46, requested 304.34, `original_size` and `size_matched` "304.33". `_msg` sets `original_size` "40", so override it in the dict.

1. `test_c1_proof_releases_at_confirmed`, parametrized `terminal_first` / `row_first`. Matched fill 304.33, consumed into the core. Then the terminal and the cancel in the given order. Check each step: row qty/proven/resolved, c1 status, `rung_occupied` (rung `live_id`), `rig.core.reserved_buy_notional()`, `unsettled_buy_notional`. Before CONFIRMED, run one `finish_cycle`: the rung stays held. After CONFIRMED and `_resolve_confirmed_buy`: row resolved with qty 304.33; the next `finish_cycle` drops c1 and frees the rung. The release needs no `_reconcile_unsettled_buys` call and no clock advance. `terminal_first` also checks `row.created_at` and `updated_at` do not change at the cancel, and one `applied` record with `requested=304.34, matched=304.33`.
2. `test_terminal_without_cancel_retires_the_live_buy`. Fill, terminal, CONFIRMED. The next plan has no cancel for c1, and c1 is gone.
3. `test_partial_fill_then_cancel_proof`. A `UPDATE LIVE` partial message (10 of 40) opens no row. `CANCELLATION` with `size_matched` "10" proves the row at 10. MATCHED 10: rung held, unsettled 0. CONFIRMED 10: released.
4. `test_repeated_terminal_is_idempotent`. The terminal three times: before the row, after the cancel, after the resolve. One row, one `applied`, unchanged stamps, c1 dropped once, wakes not repeated after the resolve.
5. `test_proof_below_confirmed_is_ignored`. CONFIRMED 5 in the ledger, then a WS `CANCELLATION` with `size_matched` "0". The row stays unproven at 40, the BUY is gone, the rung is held, the reserve is `(40 - 5) * 0.5`. One `ignored` record with `reason="below_confirmed"`, `matched=0.0`, `confirmed=5.0`.
6. `test_rest_zero_needs_two_reads`. A row aged 70 s with no trades and no ledger fills. Pass 1: open, one `first_zero` record. Pass 2: proven 0, resolved. A failed token read between two zero reads resets the count.
7. `test_rest_short_read_below_confirmed_is_not_proof`. The c1 shape at the REST stage: row 304.34, CONFIRMED 304.33. Payloads: absent, and maker rows for other order ids only (unmatched payload). A second row has CONFIRMED 208.944546 and a REST sum of 122.43 (the c21 shape). After two passes both rows stay open, with `below_confirmed` records. A found sum of 304.33 then proves and resolves on one read.
8. `test_early_proof_survives_restart`. Fill, terminal (row proven 304.33), `persist_core_snapshot`, close the store. Reopen the same path with a new host and core (`load_core_snapshot`). CONFIRMED plus `_apply_unsettled_proofs(())` resolves the row, and `finish_cycle` drops c1. The `CORE_SCHEMA_VERSION` checkpoint reads back unchanged.
9. `test_late_accept_fill_terminal_and_sell_cancel_count_once`. A late accept via the `_arm_pending` / `_Venue` / `_wire` flow (STEP-002). Then a fill of 39.99 on the 40 BUY, and a terminal `MATCHED` 39.99/39.99, which proves the row at 39.99. A live SELL added with `add_order` gets a WS `CANCELLATION` (STEP-003) and leaves the core. Repeat both WS messages, then CONFIRMED. Checks: one `accepted` command row, one `unsettled_buys` row, ledger position 39.99, `reserved_buy_notional` 0, `unsettled_buy_notional` 0, the BUY dropped once, the old SELL id not back.

The zero-fill cancel with a valid proof and the cancel without a quantity are already pinned by `test_ws_zero_quantity_resolves_the_new_row` and `test_ws_cancel_without_a_quantity_holds_the_submitted_reserve`. They keep passing through the new gate. Do not copy them.

### 6.2 Existing tests to update

- `tests/test_trader_unsettled_buy_activation.py::test_ws_skip_full_execution_sell_and_unknown_venue` (`:307`). Its first message asserts the LIVE-01 bug: a full `UPDATE MATCHED` with no row must leave no row. Remove that message and rename the test `test_ws_skip_sell_and_unknown_venue`. Test 2 above covers the new behavior.
- `tests/test_trader_unsettled_buy_recovery.py::test_rest_proof_boundary_groups_tokens_and_sums_maker_amounts` (`:378`). `old-b` and `old-c` read zero. Assert them open after pass 1 and resolved after a second pass. Check `trades.params` on the first-pass slice only.
- `tests/test_trader_unsettled_buy_recovery.py::test_rest_errors_do_not_invent_zero_and_one_token_does_not_block_another` (`:423`). Run the second pass before `trades.malformed.add("token-ok")`, then assert `ok` resolved.
- `tests/test_trader_unsettled_buy_recovery.py::_host` (`:118`): set `host._rest_zero_ids = set()`. The other `object.__new__(WalletHost)` fakes do not call `_reconcile_unsettled_buys`.

## 7. Verification

Golden tests are excluded: `tests/test_extraction_oracle.py` and `tests/test_follow300_replay.py` are not run.

1. New tests on HEAD before the production edit. They fail as listed in §6.
2. Targeted and neighbouring tests after the edit:

   ```
   PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
     tests/test_trader_buy_terminal_proof.py tests/test_trader_unsettled_buy_recovery.py \
     tests/test_trader_unsettled_buy_activation.py tests/test_trader_sell_venue_cancel.py \
     tests/test_trader_durable_place.py tests/test_trader_engine_seams.py tests/test_trader_diag.py \
     tests/test_trader_wallet_host.py tests/test_trader_wallet_store.py tests/test_trader_session_core.py \
     tests/test_trader_core_recovery.py tests/test_strategy_late_fills.py \
     tests/test_trader_two_sided_host.py tests/test_trader_two_sided_worker.py \
     --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
   ```

   All pass. The two two-sided files are the B compatibility check.
3. `uv run python -m basedpyright` reports 0 errors.
4. `uv run python -m pre_commit run --files src/trader/core_persistence.py src/trader/unsettled_buy_recovery.py src/trader/wallet_host.py tests/test_trader_buy_terminal_proof.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_unsettled_buy_recovery.py` passes.
5. `git diff bd64ef9c..HEAD -- src | rg '^\+' | rg '#'` prints nothing (no new comments).
6. `rg -n "_open_cancelled_buy" src tests` prints nothing.
7. Optional reference: the planner probes in `orchestrate/work/plan-step-004/` (`probe_c1.py`, `probe_rest.py`) show HEAD and the planned behavior side by side. They import the test rigs and write nothing in the worktree.

## 8. Residuals (not fixed here, by design)

- **Held REST conflict** (the c100 shape, 1 of 63 tail rows): the row stays open with an alert. A release would need a new proof rule, for example the CONFIRMED sum after N equal reads. That is the user's decision.
- **Terminal before the venue binding** (up to 17 of 532 terminals fit the 0.2 s window): the proof is logged as `no_owner` and dropped. REST proves the row later. A fix would insert the row from message fields (`asset_id`, `price`) instead of core metadata.
- **Cause of short REST reads** (40 wrong zeros, 4 short sums): still open. This step only stops treating them as proof.
- **Short double reserve** when a terminal is handled before its trade books (§5.2).

## 9. Progress log notes (`tasks/live-a-audit-fixes/progress.txt`)

- A terminal BUY proof with no row opens the row and proves it (`_open_unsettled_buy`). A row can now exist for a BUY that the core still holds; `BuySettled` drops it at CONFIRMED.
- All proofs pass `_apply_unsettled_proofs`: a proof below the CONFIRMED sum is ignored (`below_confirmed`). A REST zero needs two consecutive reads (`first_zero`).
- Diag: `applied` / `ignored` with `kind="buy_proof"`, `order_id`, `requested`, `matched`, `confirmed`, `reason` in (`no_owner`, `below_confirmed`, `first_zero`).
- Learnings for later steps: status `MATCHED` is terminal even with `size_matched < original_size` (gap below 0.05). Cover against `size_matched`, not `original_size`. STEP-006 compares quantities with the same 0.005 tick and must not widen it.
