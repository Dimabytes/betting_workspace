# STEP-002 plan: RISK-02 interrupted durable place and its reserve

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, HEAD `1ace90a2` (STEP-001 done). All paths below are relative to that worktree unless they start with `poly-maker/` (frozen fork, read only).

No Figma links: the step has no `designReference`.

## 0. Result in one paragraph

The root cause is wrapper order. `_durable_place` sits outside the shutdown latch, so it writes `dispatch_started` before the latch refuses, and it sits outside the latch's `asyncio.shield`, so a task cancel drops the late venue answer. The fix moves one line so `_durable_place` and `_durable_cancel` run inside the latch shield. Then a closed latch refuses before any command row exists, and a cancelled caller no longer loses the result: the shielded inner call records the outcome and notifies the core. Two small additions close the remaining gaps: `_quiesce` drains in-flight venue calls before `cancel_market`, and boot retires place rows that a dead process left in `prepared` or `dispatch_started`, but only after a REST fence proves no order rests on that market. No schema change, no new subsystem. Expected size: about +50/−5 production lines in 4 files, one new test file, one fixture line.

## 1. What is broken today (evidence)

- Wrapper order, `src/trader/wallet_host.py:798-800`: `self._latch.wrap_gateway(engine.gateway)`, then `wrap_inventory_place_guard`, then `self._wrap_quote_journal()`. Each wraps the current `gateway.place`, so the call chain is `_durable_place` → guard → latch → fork place.
- `_durable_place`, `src/trader/wallet_host.py:457-479`: `_dispatch_places` → `prepare_dispatch` commits `prepared` and then `dispatch_started` (`src/trader/core_execution.py:61-78`) before `await original_place(...)`. There is no `try`. `record_dispatch_results` and `worker.note_place_result` run only on a normal return.
- Closed latch: `ShutdownLatch.place` raises `GatewayClosed` (`src/trader/engine_seams.py:303-312`, `:375-379`) after the row is already `dispatch_started`. The venue never saw the order.
- Task cancel: `_await_in_flight` shields the inner task (`src/trader/engine_seams.py:337-351`). The caller gets `CancelledError`, the inner POST continues, and its result goes nowhere. `stop_quoter` cancels the quote task (`src/trader/engine_seams.py:1295-1305`), first in `_quiesce` (`src/trader/match_worker.py:1408`) and in `teardown` (`src/trader/wallet_host.py:1631-1643`).
- Nothing ever finishes a `dispatch_started` row. `retire_unsent` touches only `prepared` (`src/trader/core_execution.py:230-237`). `_OPEN_BUY_WHERE` keeps the BUY notional reserved (`src/trader/core_persistence.py:35-41`), `_account_reserved` subtracts it from cash and account room (`src/trader/session_budget.py:44-55`), and `proof_blocks` returns `unknown_command` (`src/trader/core_recovery.py:151-163`).
- Audit repro on deployed code (`runs/live-a-money-audit-20261009/work/a2-opus-exec/repro/durable_place_race.out`): A, closed latch: `rows=[('c7','dispatch_started','',None)] reserved_buy_notional=200.00 proof_blocks='unknown_command'`, same after reopen. B, cancel mid-POST: venue holds `('0xvenue0','BUY',0.5,400.0)`, `core_notified=[]`, same stuck row.
- Map-end race: after `stop_quoter`, `_quiesce` runs `cancel_entry_buys` and `cancel_market` at once (`src/trader/match_worker.py:1408-1416`). A POST that lands after `cancel_market` rests on the venue, and `fence_market` then times out into `keep_quiet` (`:1418-1427`).

Paths that already work and stay unchanged:

- Send error or timeout inside the fork: `poly-maker/src/polymaker/execution/gateway.py:179-185` catches every `Exception` and returns `[]`. `record_dispatch_results` writes `dispatched` + `outcome="unknown"` and keeps token, side, price and quantity in the row. The core gets `SubmitTimeout` (`src/trader/session_core.py:509-530`) and keeps the BUY reserved as `unknown` (`src/strategy/budget.py:13-19`) until the next `finish_cycle` runs `_forget_unmapped` (`src/trader/session_core.py:685-693`, `:721-737`). Before that next cycle, the fork `_requote` sees a short batch and runs `_quarantine` (`poly-maker/src/polymaker/engine.py:487-496`, `:504-510`), which cancels both tokens with REST proof (`src/trader/engine_seams.py:437-442`, `:625-640`).
- Boot: `Engine.start` runs `_startup_reconcile` (`poly-maker/src/polymaker/engine.py:100`, `:223-241`): `cancel_all`, then `cancel_asset` on stragglers.

## 2. Changes

### 2.1 Run the durable layer inside the latch (`src/trader/wallet_host.py`)

In `_install_runtime_seams`, replace lines 798-800 with:

```python
        self._wrap_quote_journal()
        self._latch.wrap_gateway(engine.gateway)
        wrap_inventory_place_guard(engine, self.cores)
```

New chains:

- `engine.gateway.place` = guard → latch → `_durable_place` → fork place.
- `engine.gateway.cancel` = latch → `_durable_cancel` → strict cancel (`install_strict_rest`, `:795`).

Effects:

- Closed latch: the latch raises `GatewayClosed` before `_durable_place` runs, so no command row exists and nothing is reserved. Only `teardown` closes the latch. The core's pending order has no venue id, so the next `finish_cycle` forgets it as `never_dispatched`, or the restore after a restart does the same. This is the proven "not sent" case, and it needs no new transition.
- Caller cancel during POST: the shielded inner task still runs `_durable_place` to the end. It records `accepted` + venue id, or `unknown`, then calls `worker.note_place_result(orders)` and `write_quote`. The core binds the venue id. After `stop_quoter` no new batch is planned, so `_last_batch` is still the dispatched batch that `note_placed` matches against.
- `_durable_cancel` gets the same protection: a cancelled caller no longer leaves a `prepared` cancel row.
- The guard stays above the latch. It sets `_PLACE_CID` and `_SKIP_ORDER_ERROR` in the caller's context (`src/trader/engine_seams.py:1015-1036`), and the fork reads them after `place` returns. Below the latch it would run in the shielded task's copied context and the per-cid error tracker would lose them. `_durable_place` uses no context variables.
- `_durable_place` now receives the guard's `PlaceBatch` of kept quotes. For core cids the guard keeps every quote (`src/trader/engine_seams.py:1054-1062`), so live A sees no difference. For other cids, dropped SELLs no longer get a row.

`_durable_place` itself needs no `try`/`except`.

### 2.2 Drain before the map-end cancel (`src/trader/match_worker.py`)

In `_quiesce`, directly after `await stop_quoter(self._host.engine, self._cid)` (`:1408`):

```python
            await self._host._latch.drain(DRAIN_TIMEOUT_S)
```

Import `DRAIN_TIMEOUT_S` from `trader.engine_seams`. The file already imports from it at `:58` and has `reportPrivateUsage=false`. `_quiesce` already runs under `asyncio.shield` in `run` (`:279`).

- The late place lands and is bound to the core before `cancel_entry_buys` and `cancel_market`. `cancel_market` then cancels it through REST proof, and the fence can pass.
- The drain is global and capped at 5 s (`src/trader/engine_seams.py:72`). The worst case is a 5 s longer map end. If a POST hangs longer, the existing unproven-fence path (`keep_quiet` plus a notification) still applies.

Test fixture: `FakeWalletHost.__init__` (`tests/trader_session_fixtures.py:476-498`) gets `self._latch = ShutdownLatch()`. An unused latch drains at once.

### 2.3 Finish rows a dead process left behind (boot)

The process can still die with a row in `prepared` (crash between the two commits in `prepare_dispatch`) or in `dispatch_started` (kill, OOM, loop shutdown, or a POST that outlives the 5 s teardown drain, after which the inner write hits a closed store). At boot no worker is attached, and `_startup_reconcile` already cancelled the wallet's orders. That is the safe moment to prove and finish these rows.

`src/trader/core_persistence.py`:

```python
def list_unfinished_places(conn: sqlite3.Connection) -> tuple[CoreCommand, ...]:
    rows = conn.execute(
        "SELECT * FROM core_commands WHERE kind='place' AND consumed=0 "
        "AND dispatch_state IN ('prepared','dispatch_started') ORDER BY command_id"
    ).fetchall()
    return tuple(_command_from_row(row) for row in rows)


def get_session_key(conn: sqlite3.Connection, session_id: str) -> CoreSessionKey | None:
    row = conn.execute("SELECT * FROM core_sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return None
    return _key_from_row(row)
```

Move the inline `CoreSessionKey(...)` build out of `_session_from_row` (`:591-610`) into `_key_from_row(row)` and use it in both places. `get_session_key` reads only plain columns. It never decodes the checkpoint or checks `schema_version`, so a corrupt session cannot crash boot.

`src/trader/core_execution.py`, next to `retire_unsent`:

```python
def retire_unknown_places(
    conn: sqlite3.Connection, commands: Sequence[CoreCommand]
) -> tuple[str, ...]:
    retired: list[str] = []
    for command in commands:
        if command.dispatch_state != "dispatch_started" or command.consumed:
            continue
        upsert_command(
            conn,
            replace(command, dispatch_state="dispatched", outcome="unknown", consumed=True),
        )
        log_event("place_retired", command_id=command.command_id)
        retired.append(command.core_order_id)
    return tuple(retired)
```

`src/trader/wallet_host.py`, a new method that `run()` calls right after `await self._boot_scan()` (`:1257`):

```python
    async def _retire_unfinished_places(self) -> None:
        conn = self.store._conn
        unfinished = list_unfinished_places(conn)
        retire_unsent(conn, unfinished)
        conn.commit()
        unknown = {c.session_id for c in unfinished if c.dispatch_state == "dispatch_started"}
        for session_id in sorted(unknown):
            key = get_session_key(conn, session_id)
            if key is None:
                continue
            tokens = {key.yes_token, key.no_token}
            await cancel_token_orders(self.engine.gateway, tokens)
            if not await fence_until(self.engine, tokens, FENCE_TIMEOUT_S):
                logger.warning("trader unknown place unproven session=%s", session_id[:12])
                continue
            retire_unknown_places(conn, [c for c in unfinished if c.session_id == session_id])
            conn.commit()
```

- `prepared` rows are proven unsent, because the POST starts only after the `dispatch_started` commit. `retire_unsent` already makes them `known_not_sent`, `canceled`, `consumed`. It needs no fence.
- `dispatch_started` rows retire only after `fence_until` proves that REST shows no order on the two tokens and no unacked MATCHED row. Fills from that order, if any, sit in `fill_ledger` and positions through the existing user-stream and REST fill recovery. Releasing the reserve therefore does not lose them.
- If the fence fails, the row stays: the reserve stays and recovery stays blocked. This is the safe side. The next boot tries again. The feature non-goals forbid releasing an unknown reserve on a timeout, so no timeout release is added.
- The normal boot cost is one SQL query that returns zero rows.
- Both retire functions skip consumed rows, so a second run changes nothing.

### 2.4 Link the outcome to the diagnostics log (`src/trader/core_execution.py`)

STEP-001 was cut, and it no longer has a diagnostic command ID (`progress.txt`, "STEP-001 cut"). The durable `command_id` (`{session}:{batch}:{core_order_id}`) is the ID. In the `record_dispatch_results` loop, add:

```python
        log_event("place_outcome", command_id=command.command_id, venue_id=venue_id, outcome=outcome)
```

Together with `place_retired` above, the diag JSONL gets one line per finished place command. The STEP-001 HTTP response record of the same POST carries the same `orderID`. `log_event` is a no-op when diagnostics are off and never raises (`src/trader/diag.py:123-135`). `core_execution` importing `trader.diag` creates no cycle: `diag` imports only `trader.diag_redact` and `trader.paths`.

### 2.5 Not changed

- No schema change, no migration. `dispatched`, `unknown` and `consumed` already exist (`src/trader/core_persistence.py:31-33`, `:76-92`). Current DBs and checkpoints read as before. Any `dispatch_started` row left by the deployed `d5ef7aa3` is finished by the same boot path. The audit found 0 such rows.
- No retry of a logical command. Nothing re-POSTs.
- `poly-maker` is not edited.

## 3. Outcome per path after the fix

| Path | Command row | Reserve | Core | Venue order |
| --- | --- | --- | --- | --- |
| Latch closed before POST | none | none | pending order forgotten next cycle or on restore | none |
| Caller cancelled during POST | `accepted` + venue id, written by the shielded call | released from the row; the core holds it once | `OrderAccepted`, venue bound | `_quiesce` drains, then `cancel_market` cancels with proof |
| Send error or timeout in fork | `dispatched`/`unknown`, token/side/price/qty kept | row released; the core holds it as `unknown` until the next cycle | `SubmitTimeout` | fork `_quarantine` cancels both tokens with REST proof (unchanged) |
| Late answer after a teardown drain timeout | stays `dispatch_started` (store closed) | kept | process ends | startup `cancel_all`, then the boot fence retires the row |
| Crash between `prepared` and `dispatch_started` | `prepared` | kept until boot | restored order forgotten | none (POST never started) → boot `retire_unsent` |
| Crash during POST | `dispatch_started` | kept until the fence proves | restored order forgotten | boot cancel + fence, then retire; fills stay in the ledger |
| Result delivered twice | unchanged | unchanged | `apply_accepted` is idempotent | unchanged |

## 4. Hard parts and edge cases

1. While a POST is in flight, the BUY counts twice: once in the row (`dispatch_started`, `outcome=''`) and once as the core's pending order. This is existing, conservative, and ends when the outcome is written. Tests assert "exactly once" at settled points and record the in-flight value explicitly.
2. A fill can arrive on the user stream before the HTTP answer. The core then pends it as unknown ownership (`src/strategy/lifecycle.py:600-618`, `:685-695`). This step does not change that path. It belongs to the fill-ownership rules, not to dispatch, and the test permutations below do not include it.
3. A POST that the server accepts after the fork's timeout and after the quarantine REST proof still escapes the core. It is pre-existing, not RISK-02, and listed in residuals.
4. `_release_when_done` swallows the inner exception when the late write hits a closed store. The row stays `dispatch_started`, which is correct, and boot handles it.
5. Wallet B (`two_sided`) shares `_install_runtime_seams` and `_quiesce`. The order change and the drain apply to B too and add no B feature. Run the B tests.
6. `run_in_executor` work keeps running when its future is cancelled at loop shutdown. That case is the same as "crash during POST", and boot covers it.
7. The audit script `durable_place_race.py` builds the old order by hand (latch first, then `_durable_place(host, gateway.place, ...)`). It cannot prove the fix. The new test reproduces the same BUY 0.5 × 400 cases on the production order instead.

## 5. Tests

New file `tests/test_trader_durable_place.py`. Reuse helpers: `_open_paper_host` (`tests/test_trader_two_sided_host.py:125`), `open_buy_rig`, `_core`, `_resting`, `_budget` (`tests/test_trader_unsettled_buy_activation.py`), and `build_attached_worker` (`tests/trader_session_fixtures.py:573`). Importing helpers from another test module is an existing pattern (`from test_trader_session_core import _md_book`). Keep fakes small:

- A venue gateway whose `place` waits on an event, appends `OpenOrder("0xvenue{n}", ...)` to `self.venue`, and returns the orders. It has a flag that returns `[]` after appending, which models a fork timeout after a POST that landed.
- A `_wire(host, gateway, latch)` helper with the production order: `WalletHost._wrap_quote_journal(host)`, then `latch.wrap_gateway(gateway)`.

Cases:

1. `test_install_runs_durable_place_inside_the_latch`: a paper host from `_open_paper_host(..., "follow300")`. Monkeypatch `trader.wallet_host._durable_place` with a recorder that stores `host._latch.in_flight` and returns `[]`. One BUY place records `in_flight == 1`. After `host._latch.close()`, place raises `GatewayClosed` and the recorder count does not change. This pins the order in `_install_runtime_seams`.
2. `test_closed_latch_writes_no_command_and_no_reserve`: the repro shape (SimpleNamespace worker and core with `_last_batch` BUY 0.5 × 400, like `durable_place_race.py`), wired and closed. Assert `GatewayClosed`, `list_commands == ()`, `reserved_buy_notional == 0.0`, `proof_blocks(core) is None`, no venue order.
3. `test_cancel_mid_post_binds_the_late_accept`: the same BUY 0.5 × 400. Cancel the caller task while the gateway is blocked and expect `CancelledError`. Assert the row is `dispatch_started`, `reserved_buy_notional == 200.0`, `proof_blocks == "unknown_command"`, and the core is not yet notified. Release the gateway and `await latch.drain(1.0)`. Assert `dispatched`/`accepted`/`0xvenue0`, reserve `0.0`, `proof_blocks is None`, and `note_place_result` called once with that order.
4. `test_restart_retires_unknown_place_only_after_fence`: write a session row (`persist_core_snapshot` with a `LiveCore`), one `dispatch_started` BUY 0.5 × 400 through `prepare_dispatch`, and one `prepared` row through `persist_prepared_places`. Close and reopen `WalletStateStore`. Assert reserve `200.0` plus the prepared notional, and `unknown_command`. Use a bare `WalletHost` (`object.__new__`) and monkeypatch `trader.wallet_host.cancel_token_orders` and `trader.wallet_host.fence_until`.
   - Fence `False`: the prepared row becomes `known_not_sent`, the unknown row stays, the reserve is `200.0`, and a warning names the session.
   - Fence `True`: the row is `dispatched`/`unknown`/consumed, the reserve is `0.0`, and `proof_blocks is None`.
   - A third run: no row changes and `cancel_token_orders` is not called.
5. `test_accept_fill_and_reserve_count_once`, parametrized, real `LiveCore` + `MatchWorker` + `WalletStateStore` through `open_buy_rig`. Replace its order with a pending, unbound BUY `c0` 0.5 × 40 and set `core._last_batch` to that place. Notional is 20, and `CASH` is 1000.
   - `on_time`: place returns. Row reserve 0, `core.reserved_buy_notional() == 20`, `_budget(rig).cash_usdc == 980`. Then credit a fill of 40 (`apply_matched_fill` + `core.note_fill` + `drain_apply`, as `_credit` in `tests/test_trader_wallet_host.py` does): core reserve 0, `cash_usdc == 1000`, `account_cap_room_usdc == 980`.
   - `late`: cancel mid-POST. While in flight, the row holds 20 and the core holds 20 (item 1 of section 4). After release and drain, the counts match `on_time`, and so do they after the fill.
   - `repeat`: `on_time`, then call `worker.note_place_result(orders)` again. Counts and `core._venue_to_core` do not change.
   - `send_error`: the venue keeps the order but `place` returns `[]`. The row is `dispatched`/`unknown` with token, price and quantity kept. Row reserve 0, the core order is `unknown`, `core.reserved_buy_notional() == 20`, `cash_usdc == 980`.
6. `test_quiesce_drains_in_flight_place_before_cancel_market`: `build_attached_worker`. Start `worker._host._latch._await_in_flight(slow())`, where `slow` sleeps 0.05 s and appends `"place"`. Monkeypatch `worker._host.cancel_market` to append `"cancel"`. Run `_quiesce(False)` and assert `["place", "cancel"]`.

Existing tests need no edits beyond the fixture line. `tests/test_trader_engine_seams.py:174-246` (latch) and `tests/test_trader_match_lifecycle.py:1024-1104` (journal wrap) do not depend on the order between the two wrappers.

## 6. Verification

Run from the worktree. Golden tests are excluded (`tests/test_extraction_oracle.py`, `tests/test_follow300_replay.py`). `PYTEST_N=8` is allowed on the local Mac.

1. Targeted tests:

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_durable_place.py tests/test_trader_core_execution.py tests/test_trader_core_persistence.py \
  tests/test_trader_core_recovery.py tests/test_trader_engine_seams.py tests/test_trader_wallet_host.py \
  tests/test_trader_match_lifecycle.py tests/test_trader_unsettled_buy_activation.py \
  tests/test_trader_unsettled_buy_recovery.py tests/test_trader_shared_budget.py \
  tests/test_trader_wallet_sidecar.py tests/test_trader_pair_merge.py \
  tests/test_trader_two_sided_worker.py tests/test_trader_two_sided_host.py \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

2. Typecheck: `uv run python -m basedpyright src/trader/wallet_host.py src/trader/match_worker.py src/trader/core_execution.py src/trader/core_persistence.py tests/test_trader_durable_place.py tests/trader_session_fixtures.py`, then the whole project with `uv run python -m basedpyright`. Both must report 0 errors.
3. Lint: `uv run python -m pre_commit run --files <changed files>`. The commit hook runs it too.
4. Diff checks:
   - `git diff --stat 1ace90a2..HEAD` shows about +50/−5 in `src/` across `wallet_host.py`, `match_worker.py`, `core_execution.py` and `core_persistence.py`, plus the new test file and one fixture line.
   - `git diff 1ace90a2..HEAD -- src | grep '^+.*#'` shows no new comments. The review config forbids them.
5. Behavior check: test 3 prints or asserts the before/after pair, reserve `200.0`/`unknown_command`, then `0.0`/`None`. This replaces the audit repro, which builds the old wrapper order by hand.

No backtests, no training, no VPS, no live orders.

## 7. Residuals (not fixed here, by design)

- A POST that the server accepts after the fork's client timeout and after the quarantine REST proof stays outside the core until `cancel_market` at map end or the next boot. This is pre-existing fork behavior.
- A fill that arrives before its place result follows the existing pending-ownership path.
- An unknown row whose boot fence fails stays until a later boot proves it. There is no runtime retry and no timeout release.
- The global 5 s drain in `_quiesce` can wait on another map's in-flight calls.

## 8. Progress log notes (`tasks/live-a-audit-fixes/progress.txt`)

- Root cause: `_durable_place` was outside the latch and its shield. A one-line reorder in `_install_runtime_seams` puts place and cancel journaling inside the shield.
- Closed latch: no command row. Caller cancel: the late result is recorded and bound to the core. Map end: drain before `cancel_market`. Boot: `prepared` → `known_not_sent`. `dispatch_started` → `dispatched`/`unknown`/consumed only after cancel + `fence_until`.
- Diag lines `place_outcome` and `place_retired` carry `command_id`.
- Learning: keep the inventory guard above the latch. Its context variables must stay in the caller's context.
