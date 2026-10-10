# STEP-003 plan: LIVE-06 deliver a venue SELL cancel to the core

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, HEAD `a5f55498` (STEP-001 and STEP-002 done). All paths below are relative to that worktree.

No Figma links: the step has no `designReference`.

## 0. Result in one paragraph

The user-WS hook `_on_order_terminal` parses BUY messages only. A venue `CANCELLATION` of a core SELL therefore never reaches the core, and an unchanged target keeps the dead SELL as the live exit. The core already has the right transition: `note_cancel_result([venue_id], True)` → `CancelAck` → the SELL is dropped and `should_evaluate` forces a requote. The fix makes the cancellation parser side-aware and routes a SELL cancel to that existing call, but only when the venue id belongs to a **live** core SELL. Everything else is ignored and logged through the STEP-001 `applied`/`ignored` diagnostics. No core, strategy, schema or fork change. Expected size: about +25/−8 production lines in 2 files, one new test file, one updated parser test.

## 1. What is broken today (evidence)

- `WalletUserStream._on_order` passes every order message to the host hook (`src/trader/engine_seams.py:166-168`). The host binds the hook in `_bind_order_terminal` (`src/trader/wallet_host.py:915-919`).
- `_on_order_terminal` (`src/trader/wallet_host.py:921-944`) uses only `parse_terminal_buy_proof` and `parse_buy_cancellation`. Both return `None` unless `side == "BUY"` (`src/trader/unsettled_buy_recovery.py:40-51`, `:54-64`). A SELL message returns at `:932`.
- The fork side is already correct: `super()._on_order(msg)` removes the order from the fork store (pinned by `tests/test_trader_unsettled_buy_activation.py:250-269`). Only the core keeps the phantom.
- With the target unchanged, nothing else retires the SELL. Only a later reprice cancel gets venue "not found", and `_cancel_reason_is_terminal` (`src/trader/engine_seams.py:683-692`) turns that into `CancelAck`.
- Audit case (`runs/live-a-money-audit-20261009/reports/a2-opus-exec.md:25-48`): `grid-3008325-m1` SELL `c47` 0.82 × 18.22, venue `0x1be18b3d`. Venue cancel at 16:08:37.867 UTC. The core kept `c47` live 3.19 s after resume, until `PLAN_CANCEL c47 reason=reprice` at 16:08:44.128. Census: 1 of 5,916 SELL venue cancels had no core cancel decision before it. The other 5,915 had the core decision first (median 58 ms before).
- Repro on HEAD, `betting_workspace/tasks/live-a-audit-fixes/orchestrate/work/plan-step-003/repro.py` (it imports the existing test rig and writes nothing in the worktree):

  ```
  HEAD after WS SELL cancel: ['c47', 'c1', 'c2', 'c3'] wakes: []
  HEAD next plan places: []
  fix applied: True orders: ['c1', 'c2', 'c3'] wakes: ['sess']
  repeat applied: False
  fix next plan SELLs: [(0.56, 18.22)]
  ```

  "fix" is the candidate delivery from §2.2, run from the script.

The existing core path that the fix reuses:

- `MatchWorker.note_cancel_result` applies, persists and wakes the session (`src/trader/match_worker.py:994-999`).
- `LiveCore.note_cancel` enqueues `CancelAck` for an owned SELL (`src/trader/session_core.py:533-548`).
- `apply_cancel_ack` drops the order into a terminal record and frees its rung (`src/strategy/lifecycle.py:349-354`, `:175-179`).
- `should_evaluate` returns True for `CancelAck` (`src/strategy/scheduling.py:146-150`), so the exit is replanned without a reprice.
- `requote` returns a blocked plan on halt, paused and game end (`src/strategy/quoting.py:932-937`).

## 2. Changes

### 2.1 One side-aware cancellation parser (`src/trader/unsettled_buy_recovery.py`)

Replace `BuyCancellation` and `parse_buy_cancellation` (`:35-51`) with:

```python
@dataclass(frozen=True)
class OrderCancellation:
    venue_id: str
    side: Side


def parse_cancellation(msg: Mapping[str, object]) -> OrderCancellation | None:
    ...same id / type == "CANCELLATION" / status == "CANCELED" checks...
    return OrderCancellation(venue_id=venue_id, side=side)
```

- `Side` is `strategy.types.Side` (`Literal["BUY", "SELL"]`, `src/strategy/types.py:6`). Any other `side` value returns `None`. The implementer picks the narrowing form that basedpyright accepts.
- Delete the old docstring. Do not add a new one. The empty-`size_matched` case stays pinned by the test.
- This is the shared terminal route that STEP-004 reuses for the BUY fix.

### 2.2 Route by side in the host (`src/trader/wallet_host.py`)

`_on_order_terminal` (`:921-944`): parse the cancellation first and route a SELL before the BUY code. Delete the old BUY-only docstring.

```python
    def _on_order_terminal(self, msg: object) -> None:
        if type(msg) is not dict:
            return
        raw = cast(Mapping[str, object], msg)
        cancellation = parse_cancellation(raw)
        if cancellation is not None and cancellation.side == "SELL":
            self._deliver_cancelled_sell(cancellation.venue_id)
            return
        proof = parse_terminal_buy_proof(raw)
        ...unchanged BUY code; `cancellation` is now BUY or None...
```

New method next to `_deliver_cancelled_buy` (`:978-988`), no docstring:

```python
    def _deliver_cancelled_sell(self, venue_id: str) -> None:
        worker = _worker_for_order_ids(
            self.engine, self._worker_by_token, self._worker_by_cid, [venue_id]
        )
        owner = None
        if worker is not None and worker.core is not None:
            owner = worker.core.find_owned_order(venue_id)
        live_sell = (
            isinstance(owner, RestingOrder) and owner.side == "SELL" and owner.status == "live"
        )
        if worker is None or not live_sell:
            log_event("ignored", kind="sell_cancel", order_id=venue_id, reason="no_live_sell")
            return
        log_event("applied", kind="sell_cancel", order_id=venue_id)
        worker.note_cancel_result([venue_id], True)
```

- Imports: `RestingOrder` from `strategy.types`, `log_event` added to the existing `trader.diag` import (`:66`), `parse_cancellation` replaces `parse_buy_cancellation` (`:138-144`).
- `wallet_host` already imports the fork enum `Side` (`:18`). Do not import `strategy.types.Side` here. The host compares against the string literal `"SELL"`.
- `log_event` is a no-op when diagnostics are off (`src/trader/diag.py:123-135`). Inside `_handle` it gets `in_seq` automatically, so the line joins the `received` frame. The stage names follow the STEP-001 user-stream vocabulary (`received`/`parsed`/`applied`/`ignored`).
- `_worker_for_order_ids` finds the worker through `core.venue_ids()` after the fork has already removed the order (`:366-393`). That is the same lookup the BUY path uses.

### 2.3 Not changed

- `LiveCore.note_cancel`, `apply_cancel_ack`, scheduling and quoting. The backtest runs on the same core, so the golden runs see no change.
- `_deliver_cancelled_buy`, `_open_cancelled_buy` and the BUY proof logic. STEP-004 owns the BUY ordering fix.
- `engine_seams.py` and `poly-maker`.

## 3. Outcome per path after the fix

| Message / state | Owner found | Result |
| --- | --- | --- |
| SELL CANCELLATION, core SELL `live` (c47 case) | `RestingOrder` SELL live | `applied`. `CancelAck` drops it, persists, wakes the session. The next cycle plans a new SELL at the same unchanged target if the gates allow. |
| Same message again | terminal `OrderRecord` | `ignored`, no wake, no forced requote |
| SELL CANCELLATION while the core's own cancel is in flight (`canceling`) | `RestingOrder` canceling | `ignored`. The REST answer acks it exactly as today. |
| Core SELL `unknown` after a `CancelTimeout` (keeps `cancel_reason`) | `RestingOrder` unknown | `ignored`. `_venue_cancels` resends each cycle (`src/trader/session_core.py:837`), and "not found" becomes `CancelAck` (`tests/test_trader_session_core.py:1054`). |
| SELL-side message for a BUY venue id | BUY | `ignored`, BUY untouched (`tests/test_trader_unsettled_buy_activation.py:307-316` still holds) |
| Dust FAK SELL (terminal record from `_bind_taker_sell`, `src/trader/dust_sweep.py:224-251`) | `OrderRecord` | `ignored` |
| Unknown venue id / no worker / `core is None` | none | `ignored` |
| BUY CANCELLATION | — | BUY path below the SELL branch, unchanged |

## 4. Hard parts and edge cases

- **Why only `live`.** A `canceling` SELL is a cancel the core sent itself. That is 5,915 of 5,916 audit cases. Its REST answer already ends it. If the WS path also acked it, the REST `CancelAck` would hit the terminal record and force a second requote about 14 ms later (`note_cancel` still enqueues for a record, `src/trader/session_core.py:538-548`). Skipping `canceling` keeps today's cadence for those cancels byte for byte. A bound SELL is only ever `live` or canceling-type: `note_placed` binds and applies `OrderAccepted` in one `_finish_place` call (`src/trader/match_worker.py:980-991`, `src/strategy/lifecycle.py:323-331`), and a `CancelTimeout` `unknown` keeps its `cancel_reason`.
- **Partial fill before the cancel.** A MATCHED trade credits the ledger and calls `_on_fill_credited` → `_consume_core_outbox` synchronously (`src/trader/wallet_store.py:362-381`, `src/trader/wallet_host.py:1067-1084`). The trade frame comes before the CANCELLATION frame on the same socket, so the core already holds the fill. The new SELL size is `share_floor(inventory)` (`src/strategy/quoting.py:462-466`). Probe: fill 5, then cancel → plan SELL 13.22.
- **Fill after the cancel.** `_owner_for_fill` rebuilds the owner from the terminal record (`src/strategy/lifecycle.py:590-597`). The fill credits inventory once, and `seen_fill_ids` drops a repeat. Probe: cancel, then fill 5 → record `filled_qty=5.0`, inventory 13.22, plan SELL 13.22. A repeat of the same fill key leaves 13.22.
- **Restore.** `persist_core_snapshot` writes bindings for every order with a record (`src/trader/core_session_io.py:29-52`). A live SELL has one from `occupy_sell` (`src/strategy/lifecycle.py:792-794`). `load_core_snapshot` rebinds it (`:66-75`), and `restore_orders` keeps `live`. Probe: restored `c47` live and bound → `applied` → dropped. Tests must seed the SELL with `occupy_sell`. A bare `orders=(sell,)` has no record, so no binding survives the restore.
- **Halt / paused / game end.** Probe: the venue cancel arrives while `c47` is live, then the next cycle has halt, paused or game end → no SELL placed, no SELL order left. If the gate came first, the core marks `c47` canceling itself, and the WS message is ignored by the `live` rule.
- **Shutdown.** `teardown` calls `cancel_all` after `stop_quoter` (`src/trader/wallet_host.py:1660-1667`). If the user socket delivers those cancels before `engine.shutdown`, they now drop live core SELLs and persist that. No dispatch follows: quoters are stopped and the latch is closed. I did not test this path; it uses the same call as the BUY cancel at shutdown.
- **B (`two_sided`).** `TwoSidedWorker` subclasses `MatchWorker` (`src/trader/two_sided_worker.py:72`) and uses the same `LiveCore`. B gets the same delivery through shared host code, with no new B behavior. The B host and worker tests must stay green.
- **Diagnostics.** The STEP-001 handoff names `not_buy_terminal`. That record does not exist after the STEP-001 cut (`grep not_buy_terminal src` → nothing). This step adds `applied` / `ignored` with `kind="sell_cancel"`.

## 5. Tests

Write the new tests first and run them on unchanged production code. The c47, partial-fill, restore and gate tests must fail on HEAD. Do not use `git stash`.

New file `tests/test_trader_sell_venue_cancel.py`. Reuse the rig from `tests/test_trader_unsettled_buy_activation.py` (`open_buy_rig`, `_core`, `_resting`, `_msg`, `_sources`, `CID`, `NS`). The repo already imports helpers across test modules (`:16`). One local helper `open_sell_rig(tmp_path)`:

- clear the rig's BUY: `orders=()`, `rungs=()`, `has_buy_fill=True`, inventory `TokenInventory(0, 18.22, 9.11, None)`;
- `occupy_sell` SELL `c47` 0.82 × 18.22, `bind_venue(core_id="c47", venue_id="0x1be18b3d")`, `store.set_position(YES_TOKEN, 18.22, 0.5)`;
- books YES `TokenBook(0, 0.79, 0.82, ...)`, NO `TokenBook(1, 0.18, 0.21, ...)`, signal `RawDeltaSignal(0.01, NS, NS, 0.80, 0, 0)`. Probe result: this core targets SELL 0.82 × 18.22, so `c47` is the unchanged target;
- sources with `allow_buy=False` (`replace(_sources(...), permissions=Permissions(False, False, False, True, False))`) so no BUY noise;
- one setup `run_cycle` + `take_plan`, then assert that `c47` is still `live` (unchanged target, no reprice).

Tests:

1. `test_venue_sell_cancel_retires_the_phantom_and_replans`: the c47 case through the real stream. Build `WalletUserStream`, set `on_order_terminal = rig.host._on_order_terminal`, `upsert_order` the SELL into the fork store, call `stream._on_order(_msg(venue, "0", side="SELL"))`. Assert that the fork store has no SELL, the core has no `c47`, `rig.wakes == [CID]`, and the next `run_cycle` plan has one SELL 0.82 × 18.22 with a new order id.
2. `test_venue_sell_cancel_counts_a_partial_fill_in_either_order`: parametrize `fill_first`. Fill 5.0 through `rig.worker.note_core_fill(Fill(YES_TOKEN, Side.SELL, 0.82, 5.0, "t1:0x1be18b3d", 1.0, is_maker=True))`, store position 13.22. Assert inventory ≈ 13.22 and next plan SELL size 13.22. Repeat the same fill → inventory unchanged.
3. `test_venue_sell_cancel_ignores_non_live_owners`: a repeat delivery after case 1 (one wake only); `c47` marked canceling with `mark_canceling(..., reason="reprice")` stays canceling with no wake; an unknown venue id changes nothing.
4. `test_venue_sell_cancel_after_restore`: `persist_core_snapshot(identity=_identity(rig.worker))`, `load_core_snapshot` into a fresh `_core(...)`, set `rig.worker._core = fresh`, deliver → `c47` gone in the fresh core. Load a third core from sqlite → no active `c47`.
5. `test_venue_sell_cancel_under_a_gate_places_nothing`: parametrize halt / paused / game_ended. Deliver while `c47` is live, then `finish_cycle` with gated sources → no SELL in the plan, no SELL order.

Update `tests/test_trader_unsettled_buy_recovery.py:195-215`: rename to the new parser, compare with `OrderCancellation("venue-1", "BUY")`, turn the SELL case into a positive `OrderCancellation("venue-1", "SELL")`, add `side="other"` → `None`. Update the import (`:30-37`).

Existing tests that must stay green unchanged: `tests/test_trader_unsettled_buy_activation.py` (BUY terminal flow, including the SELL-side message for a BUY id), `tests/test_trader_unsettled_buy_recovery.py` (other cases), `tests/test_trader_engine_seams.py`, `tests/test_trader_diag.py`.

## 6. Verification

Run from the worktree. Golden tests are excluded (`tests/test_extraction_oracle.py`, `tests/test_follow300_replay.py`).

1. Before the production edit, the new file fails on HEAD:

   ```
   PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_sell_venue_cancel.py --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
   ```

2. After the edit, targeted and neighbour tests pass:

   ```
   PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
     tests/test_trader_sell_venue_cancel.py tests/test_trader_unsettled_buy_recovery.py \
     tests/test_trader_unsettled_buy_activation.py tests/test_trader_engine_seams.py tests/test_trader_diag.py \
     tests/test_trader_wallet_host.py tests/test_trader_session_core.py tests/test_trader_core_recovery.py \
     tests/test_strategy_late_fills.py tests/test_trader_two_sided_host.py tests/test_trader_two_sided_worker.py \
     --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
   ```

3. `uv run python -m basedpyright` → 0 errors.
4. `uv run python -m pre_commit run --files src/trader/unsettled_buy_recovery.py src/trader/wallet_host.py tests/test_trader_sell_venue_cancel.py tests/test_trader_unsettled_buy_recovery.py`.
5. `grep -rn "parse_buy_cancellation\|BuyCancellation" src tests` → nothing.
6. Comments check: the diff adds no comments or docstrings, and the two touched docstrings are gone.

No backtest, no golden run, no VPS access.

## 7. Residuals (not fixed here, by design)

- A cancel that arrives before the place answer binds the venue id finds no owner and is ignored. That needs a venue dead-man exactly during an in-flight POST. Nothing seen in the audit.
- Cancel first, then the fill arrives only after the replacement SELL was already dispatched: the replacement is oversized by that fill. The venue then refuses it ("not enough balance") and the existing reject freeze runs. Today's REST reprice `CancelAck` has the same race (audit R3). Not changed.
- A SELL cancel that the user socket never delivers (the audit found 20 orders with no journal CANCELLATION) still waits for a reprice. Fixing that needs a REST compare for live SELLs. Out of scope.
- The 13.2 s event-loop stall that triggered the dead-man is a separate investigation (`FINAL_REPORT_RU.md`, LIVE-06 and open questions). This hook does not fix it.

## 8. Progress log notes (`tasks/live-a-audit-fixes/progress.txt`)

- `parse_cancellation` returns `OrderCancellation(venue_id, side)`. `_on_order_terminal` routes SELL first, and STEP-004 reworks the BUY branch below it.
- A WS SELL cancel acks only a `live` core SELL. A `canceling` SELL stays with its REST answer, so the core-requested cancel cadence is unchanged.
- Diag: `applied` / `ignored` with `kind="sell_cancel"`, `order_id`, and `reason="no_live_sell"` on ignore.
- Learning: seed a test SELL with `occupy_sell`. Without its record, `persist_core_snapshot` writes no binding and the restored order loses its venue id.
