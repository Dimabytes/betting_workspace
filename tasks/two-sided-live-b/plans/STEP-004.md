# STEP-004: `Merged` event in the core, delivered through the outbox

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-004.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001 `8dadf747`, STEP-002 `1d380527` (it may get a review-fix amend), then STEP-003 as planned in `W/tasks/two-sided-live-b/plans/STEP-003.md`.
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. These rules matter here:
- Function names are verbs.
- Arguments are required and keyword-only.
- No `dict[str, Any]` and no anonymous multi-field tuples.
- No branches for inputs that cannot occur.
- **New code gets no comments and no docstrings** (review config). Do not edit docstrings of functions you only extend by one item.

## Precondition

Start only after STEP-003 is committed. In E:

```bash
git status --short          # only the two untracked data/backtests/dota_maker/validation_*ts-regress-* dirs
grep -n "def apply_merge\|_LEDGER_MERGED = \|AND status != ?" src/trader/wallet_store.py
```

You must see `apply_merge(self, *, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool`, `_LEDGER_MERGED = "MERGED"`, and `fill_for_key` filtering MERGED. `make lint` runs pre-commit, which stashes unstaged files and type-checks the whole project. A dirty tree mixes another step's errors into this one.

If the STEP-002 amend renamed the test helpers in `tests/test_strategy_two_sided.py` (`_quoting`, `_run`, `T0`, `T1`, `POLICY`), use the current names. The logic below does not change.

## What STEP-003 gives this step (assumed, per its plan)

- `store.apply_merge(tx_hash=..., token_ids=(yes, no), qty=q)` writes two `fill_ledger` rows in one commit. Key `merge:{tx}:{token_id}`, `status='MERGED'`, `side='MERGE'`, `size=q` (the full qty, not clamped). Store positions drop by `q`, clamped at 0. Two `fill_outbox` rows, `event='merged'`, `acked=1`, in `token_ids` order.
- `store.core_outbox_after(...)` returns those rows. `outbox_after` JOINs `fill_ledger` on `fill_key`, so every returned item has a ledger row.
- `store.fill_for_key(merge_key)` returns **None**. Today `consume_core_outbox` calls `fill_for_key` first and returns on None, so the core cursor would stop at the first merged row. That is the bug this step fixes.

## Goal

The core learns about a merge from the outbox and lowers both token inventories by `qty`. No Recovery happens. After a merge, core inventory equals the store sizes again, so `LiveCore._position_mismatch` does not fire. `sell_only`, `recovery_pending`, `recovery_generation` and the orders do not change. The outbox cursor moves past the merged rows, and the core snapshot is saved. `Merged` round-trips through the trace codec.

Service A does not change. A never writes MERGED rows, so A never sees a `merged` event. The engine only gets one new event type. A trace never contains `Merged`. `DIGEST_GROUPS` is unchanged.

## Decisions (made without the owner, with reasons)

1. **`Merged` goes through `_apply_fill_path` by joining the existing or-pattern**, `case Recovery() | RecoveryVerified() | OwnershipResolved() | SettlementStatus() | Merged():`. It is applied in `_apply_session_event` with a new `case Merged():`.
   - Reason: ruff C901 caps McCabe at 10, and `_apply_fill_path` is already at 10. I checked this with ruff 0.16.1 on a copy in a scratch dir. A separate `case Merged():` in `_apply_fill_path` gives 11 and fails `make lint`. The or-pattern route keeps `_apply_fill_path` at 10 and moves `_apply_session_event` from 5 to 6.
   - This still meets the feature text ("a Merged branch in `engine._apply_fill_path`"). Like `SettlementStatus`, `Merged` is a ledger-delivered session event.
2. **`apply_merged` reuses `_credit_sell_inventory`.** For inventory a merge works like selling each leg:
   - qty drops by `qty`. `drop_share_residue` takes it to 0 at or below one share tick, so it never goes below 0.
   - `cost_basis` drops by avg × qty, which is proportional.
   - `last_buy_ns` is kept, and becomes `None` at 0.

   Its other effects are no-ops in two-sided state. `_debit_rung_lots` does nothing when `rungs == ()`. The `winding_down` flag needs `episode_id != 0`, and two-sided keeps `episode_id = 0`. It does not touch `sell_only`, `recovery_pending`, `recovery_generation`, orders or `seen_fill_ids`. A probe on a real `LiveCore` gave 30/12.0 − 20 → 10/4.0, then 20 − 20 → 0, and 20 − 25 → 0.
3. **No `_reenter_recovery` and no generation bump.** The feature text requires this, and it is safe:
   - `RecoveryCoordinator.accept_if_proven` reads the **ledger** sizes, enqueues `RecoveryVerified`, and drains in the same call.
   - PairMerger (STEP-005) runs `apply_merge` and `consume_core_outbox` with no await between them.
   - So an RV applied before `Merged` carries pre-merge sizes, and `Merged` then subtracts. An RV applied after carries post-merge sizes and overwrites the inventory absolutely. Neither order double-counts.
4. **No dedupe in the core.** The outbox cursor delivers each merge leg once. `persist_core_snapshot` commits the cursor and the state together. The two legs have different keys and different token indexes.
5. **`Merged` marks the schedule dirty, like `Fill`, but does not force an evaluation** (`should_evaluate` is unchanged).
   - A merge cuts both legs by the same qty, so `net = qty_radiant − qty_dire` does not change. `desired_bids` depends only on `net`, so the quotes are the same.
   - The next debounce or forced Wake picks up the new inventory. PairMerger also wakes the quoter (STEP-005).
6. **The ledger reader is `read_merge_leg(conn, *, fill_key) -> MergeLeg` in `src/trader/core_persistence.py`**, not a `WalletStateStore` method as the STEP-003 handoff suggested.
   - `wallet_store.py` is 927 lines and reaches about 990 after STEP-003, so a new method would push it past 1000.
   - `core_persistence.py` (952 lines, about 968 after) already owns `outbox_after`, the core-cursor read that JOINs `fill_ledger`.
   - `core_session_io.py` already imports from it and uses `store._conn` under a file-level `reportPrivateUsage=false`.
7. **`read_merge_leg` returns `MergeLeg`, not `MergeLeg | None`.** `outbox_after` JOINs `fill_ledger` on the same key, so the row exists for every item the cursor sees. A None branch would handle an input that cannot occur (AGENTS rule). If the file is corrupted, the `None[...]` TypeError is raised inside `consume_core_outbox`'s try. The core is reverted and the error propagates, which is the existing failure mode.
8. **`consume_core_outbox` is split into the loop plus `_note_outbox_item(...) -> bool` and `_note_merge_leg(...) -> bool`.**
   - `True` means an event was queued and the cursor moves on. `False` means the cursor stops, which is the existing "unreadable row" behavior.
   - The `merged` branch is checked **before** `fill_for_key`.
   - A flat `elif` inside the existing loop would push the loop's complexity from 7 to about 9 and nest a third level.
   - The split moves `memory = core.capture()` above the row read. The normal path is unchanged.
   - The one difference: a sqlite exception from `fill_for_key` now also runs `core.revert`. Nothing was queued at that point, so the revert is a no-op, plus one `write_revert` trace row on A in a crash path.
9. **A merge leg whose token is not in this core stops the cursor (`return False`).** This is the same as the sibling `matched` branch. The check narrows `int | None` for `note_merge`. Today it cannot trigger: both callers pass the worker's own two tokens.
10. **Trace codec: only a decoder is added.** Encoding is the generic `jsonable`. For the roundtrip test, `Merged(1, 0, 20.0)` is added to `_sample_events()` in `tests/test_core_trace.py`. Two existing tests then check it: `test_codec_round_trip`, and `test_inbound_event_codec_is_complete`, which fails until the decoder is registered. No separate bad-keys test: `require_exact_keys` is already tested.
11. **`LiveCore` stays typed `Follow300Policy`** (STEP-006 widens it). `note_merge` does not depend on the policy. The consume test uses a Follow300 `LiveCore`, like the existing `test_outbox_consume_advances_cursor_only_after_apply`.
12. **No checkpoint or schema change.** `Merged` only changes `inventory`, which the checkpoint already stores.
13. `session_core.py` is already 1108 lines. This step adds about 3 lines and does not split it.

## Order of edits (all paths relative to E)

### 1. `src/strategy/types.py`

After `class SettlementStatus` add:

```python
@dataclass(frozen=True)
class Merged:
    now_ns: int
    token_index: int
    qty: float
```

Append `| Merged` as the last member of `InboundEvent`.

### 2. `src/strategy/lifecycle.py`

- Add `Merged,` to the `from strategy.types import (...)` block (ruff sorts it).
- Right after `apply_fill(...)` (line ~689) and before `apply_ownership_resolved`:

```python
def apply_merged(*, state: StrategyState, event: Merged) -> StrategyState:
    return _credit_sell_inventory(state=state, token_index=event.token_index, qty=event.qty)
```

### 3. `src/strategy/engine.py`

- Import `apply_merged` from `strategy.lifecycle` and `Merged` from `strategy.types`.
- In `_apply_session_event`, after the `SettlementStatus` case:

```python
        case Merged():
            return apply_merged(state=state, event=event)
```

- In `_apply_fill_path`, change the or-pattern line to:

```python
        case Recovery() | RecoveryVerified() | OwnershipResolved() | SettlementStatus() | Merged():
```

  If ruff-format wraps it, keep the wrapped form.

### 4. `src/strategy/scheduling.py`

- Import `Merged`.
- In `note_schedule`, add `| Merged` to the `isinstance(event, BookUpdate | SignalUpdate | Fill | ...)` union, right after `| Fill`. Nothing else changes, and the docstring stays.

### 5. `src/trader/session_core.py`

- Add `Merged,` to the `from strategy.types import (...)` block.
- In `LiveCore`, right after `note_fill(...)`:

```python
    def note_merge(self, *, token_index: int, qty: float, now_ns: int) -> None:
        self.enqueue(Merged(now_ns=now_ns, token_index=token_index, qty=qty))
```

### 6. `src/trader/core_persistence.py`

Right after `outbox_after(...)` at the end of the file:

```python
@dataclass(frozen=True)
class MergeLeg:
    token_id: str
    qty: float


def read_merge_leg(conn: sqlite3.Connection, *, fill_key: str) -> MergeLeg:
    row = conn.execute(
        "SELECT token_id, size FROM fill_ledger WHERE fill_key=?", (fill_key,)
    ).fetchone()
    return MergeLeg(token_id=str(row["token_id"]), qty=float(row["size"]))
```

`dataclass` and `sqlite3` are already imported. `size` is the full merged qty (STEP-003 decision 4). The core clamps it itself.

### 7. `src/trader/core_session_io.py`

- Add `read_merge_leg` to the `from trader.core_persistence import (...)` block. Change the store import to `from trader.wallet_store import OutboxItem, WalletStateStore`.
- Replace `consume_core_outbox` with the version below and add the two helpers after it. The `matched`/`confirmed`/`failed` handling keeps its existing order and calls.

```python
def consume_core_outbox(
    *,
    store: WalletStateStore,
    core: LiveCore,
    identity: SessionIdentity,
    tokens: frozenset[str],
) -> None:
    for item in store.core_outbox_after(after_seq=core.last_outbox_seq, tokens=tokens):
        memory = core.capture()
        try:
            if not _note_outbox_item(store=store, core=core, item=item):
                return
            core.drain_apply()
            core.last_outbox_seq = item.seq
            persist_core_snapshot(store=store, core=core, identity=identity)
        except Exception:
            core.revert(memory)
            raise


def _note_merge_leg(*, store: WalletStateStore, core: LiveCore, fill_key: str) -> bool:
    leg = read_merge_leg(store._conn, fill_key=fill_key)
    token_index = core.token_index(leg.token_id)
    if token_index is None:
        return False
    core.note_merge(token_index=token_index, qty=leg.qty, now_ns=core_now_ns())
    return True


def _note_outbox_item(*, store: WalletStateStore, core: LiveCore, item: OutboxItem) -> bool:
    if item.event == "merged":
        return _note_merge_leg(store=store, core=core, fill_key=item.fill_key)
    fill = store.fill_for_key(item.fill_key)
    if fill is None:
        return False
    if item.event == "failed":
        core.note_recovery(now_ns=core_now_ns())
        return True
    if item.event not in ("matched", "confirmed"):
        return False
    _clob, venue_id = split_fill_key(fill.trade_id)
    token_index = core.token_index(fill.token_id)
    if token_index is None:
        return False
    core.note_fill(
        fill_key=fill.trade_id,
        venue_id=venue_id,
        qty=fill.size,
        price=fill.price,
        now_ns=core_now_ns(),
        token_index=token_index,
        side="BUY" if fill.side.value == "BUY" else "SELL",
    )
    return True
```

Every `return False` happens before anything is enqueued, so returning from inside the try without a revert leaves the core unchanged. That was already true before this change.

### 8. `src/trader/core_trace_codec.py`

- Add `Merged,` to the `from strategy.types import (...)` block.
- After `_decode_settlement`:

```python
def _decode_merged(fields: dict[str, object]) -> Merged:
    label = "Merged"
    require_exact_keys(fields, frozenset({"now_ns", "token_index", "qty"}), label)
    return Merged(
        now_ns=require_int(fields, "now_ns", label),
        token_index=require_int(fields, "token_index", label),
        qty=require_number(fields, "qty", label),
    )
```

- In `EVENT_DECODERS`, add `"Merged": _decode_merged,` after `"SettlementStatus": _decode_settlement,`.

### 9. Tests

No docstrings or comments in new tests.

**a) `tests/test_strategy_two_sided.py`**: add `apply_merged` to the `strategy.lifecycle` import and `Merged` to the `strategy.types` import. Append:

```python
def test_merged_cuts_both_legs_and_keeps_bids_and_recovery() -> None:
    held = replace(
        _quoting(radiant_index=0),
        inventory=(
            TokenInventory(token_index=0, qty=30.0, cost_basis=12.0, last_buy_ns=T0),
            TokenInventory(token_index=1, qty=20.0, cost_basis=11.0, last_buy_ns=T0),
        ),
    )
    settled = _run(state=held, events=(Wake(now_ns=T1, forced=True),))[-1].state
    merge_ns = T1 + 1_000
    outputs = _run(
        state=settled,
        events=(
            Merged(now_ns=merge_ns, token_index=0, qty=20.0),
            Merged(now_ns=merge_ns, token_index=1, qty=20.0),
        ),
    )
    merged = outputs[-1].state
    radiant, dire = merged.inventory
    assert radiant.qty == 10.0
    assert radiant.cost_basis == pytest.approx(4.0)
    assert radiant.last_buy_ns == T0
    assert dire == TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None)
    assert merged.orders == settled.orders
    assert merged.sell_only == settled.sell_only
    assert merged.recovery_pending == settled.recovery_pending
    assert merged.recovery_generation == settled.recovery_generation
    assert merged.schedule.dirty_open_ns == merge_ns
    assert all(out.plan.places == () and out.plan.cancels == () for out in outputs)
    assert desired_bids(state=merged, policy=POLICY) == desired_bids(state=settled, policy=POLICY)
    over = apply_merged(state=merged, event=Merged(now_ns=merge_ns, token_index=0, qty=25.0))
    assert over.inventory[0] == TokenInventory(
        token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None
    )
```

The forced Wake on `held` matters. `_quoting` ends with `OrderAccepted` at T0, which leaves the dirty window open at T0. The Wake closes it, so the test proves that `Merged` opens it at `merge_ns`.

**b) `tests/test_core_trace.py`**: import `Merged` from `strategy.types`. Append `Merged(1, 0, 20.0),` as the last item of `_sample_events()`, after `SettlementStatus(1, "f1", "matched"),`.

**c) `tests/test_trader_core_persistence.py`**: (922 lines, about 975 after)
- Extract the inline `LiveCore(...)` from `test_outbox_consume_advances_cursor_only_after_apply` into a module helper `_live_core() -> LiveCore` with the same arguments, and use it there. This is a mechanical change; the assertions do not change.
- Add, right after that test:

```python
def test_outbox_consume_applies_merge_without_recovery(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    core = _live_core()
    identity = SessionIdentity(session_id="0xcond", key=_key())
    tokens = frozenset({YES, NO})
    store.apply_confirmed_fill(
        Fill(YES, Side.BUY, 0.40, 30.0, "t1:v1", 1.0, is_maker=True), "t1:v1"
    )
    store.apply_confirmed_fill(
        Fill(NO, Side.BUY, 0.55, 20.0, "t2:v2", 1.0, is_maker=True), "t2:v2"
    )
    core.last_outbox_seq = store.core_outbox_after(after_seq=0, tokens=tokens)[-1].seq
    core.note_recovery(now_ns=1)
    core.drain_apply()
    core.note_recovery_verified(
        now_ns=2,
        generation=core.state.recovery_generation,
        inventory=(
            TokenInventory(token_index=0, qty=30.0, cost_basis=12.0, last_buy_ns=None),
            TokenInventory(token_index=1, qty=20.0, cost_basis=11.0, last_buy_ns=None),
        ),
    )
    core.drain_apply()
    before = core.state
    assert before.recovery_pending is False
    assert before.sell_only is True
    assert before.recovery_generation == 1

    assert store.apply_merge(tx_hash="0xmerge1", token_ids=(YES, NO), qty=20.0) is True
    consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)

    yes, no = core.state.inventory
    assert yes.qty == 10.0
    assert yes.cost_basis == pytest.approx(4.0)
    assert no == TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None)
    assert yes.qty == store.position(YES).size
    assert no.qty == store.position(NO).size
    assert core.state.recovery_pending is False
    assert core.state.sell_only is True
    assert core.state.recovery_generation == 1
    assert core.state.orders == before.orders
    rows = store.core_outbox_after(after_seq=0, tokens=tokens)
    assert [row.event for row in rows[-2:]] == ["merged", "merged"]
    assert core.last_outbox_seq == rows[-1].seq
    session = get_session(store._conn, "0xcond")
    assert session is not None
    assert session.last_outbox_seq == rows[-1].seq

    consume_core_outbox(store=store, core=core, identity=identity, tokens=tokens)
    assert core.state.inventory == (yes, no)
    store.close()
```

Why the test seeds the core this way:
- Moving the cursor past the two `confirmed` rows means they are not replayed as unknown-venue fills, which would create pending ownership.
- Recovery → RecoveryVerified is the path B uses after a restart.
- A probe confirmed that a Follow300 `LiveCore` ends in `recovery_pending=False, sell_only=True, generation=1` with no orders and no plan.
- `store.position(...)` equality is the "no Recovery" proof, because `_position_mismatch` compares exactly these numbers.

## Edge cases

- **Merge while `recovery_pending`:** the core inventory is lowered, and the later RV overwrites it with ledger sizes that already include the merge (decision 3).
- **Core holds less than `qty`** (stale core during recovery): it clamps to 0. RV or `_position_mismatch` fix the rest, as they do today.
- **Float residue** (for example `20.123456` merged from `20.13`): the core drops less than 0.01 share to 0, while the store keeps about 0.0065. `share_qty_matches` floors both to 0.00, so no mismatch.
- **Exception during consume after `apply_merge`:** the core is reverted and the cursor is not moved. The next consume (the next fill, or registration) re-delivers the row. Until then `_position_mismatch` triggers a Recovery, which re-reads the ledger. That is safe.
- **Two merged rows, one snapshot each:** after a crash between them, B rebuilds from chain and ledger on restart (STEP-006), and A never gets here.
- **Unknown future outbox events** still stop the cursor, as before.

## What later steps need from this step

- **STEP-005 (PairMerger):**
  - After `store.apply_merge(...)` (True **or** False), call `consume_core_outbox(store=store, core=worker.core, identity=worker._identity(), tokens=frozenset({yes, no}))` in the same event-loop turn, with no await before it. `wallet_host._consume_core_outbox(token_id)` already resolves identity and tokens, and is the other option.
  - `consume_core_outbox` re-raises after reverting the core. The merge itself succeeded, so a consume error must not turn the outcome into `unknown`. Log it. The next quote cycle's Recovery corrects the core.
  - `Merged` does not trigger an evaluation, so PairMerger must call `engine._wake_cid(cid)` (already in the feature text).
  - Every consume persists a snapshot through `core.export_checkpoint`, which reads `policy.sell_min_life_s`. PairMerger tests with a real core should use a Follow300 `LiveCore` (as here) or wait for STEP-006.
- **STEP-006 (TwoSidedWorker):**
  - Widen `LiveCore` to `Policy`. `export_checkpoint` must work for `TwoSidedPolicy` (`sell_min_life_s` → 0.0), because consume persists after every merged row.
  - A clean core (no checkpoint restore) starts with `last_outbox_seq = 0`. `WalletHost.register_worker` then runs `_consume_core_outbox` for both tokens, which **replays every old row** for this map:
    - Old fills become unknown-venue `Fill`s, which create pending ownership. `ownership_unresolved` then pulls both bids.
    - Old merges are subtracted again. RV later overwrites the inventory, but not the pending ownership.
  - So `TwoSidedWorker.open_core` must set `core.last_outbox_seq` to the outbox head (`match_worker._outbox_head(store)`) before registration. The ledger sizes that RV reads already contain those rows.
  - Put a merge before the restart in the restart test, to prove it is not replayed.
- **STEP-010:** nothing extra. The A replay goldens and the trace digest groups are unchanged by this step.

## Do not touch

- `src/trader/wallet_store.py` (STEP-003's file).
- `src/trader/wallet_host.py`, `src/trader/match_worker.py`, `src/trader/dust_sweep.py`.
- `src/strategy/quoting.py`, `src/strategy/two_sided_quoting.py`.
- `src/backtest/**`, `config/**`, `compose.yaml`, `scripts/**`.
- The test files `tests/test_strategy_core.py` and `tests/test_follow300_replay.py`.
- `../poly-maker`.

Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests (feature list: two_sided, core_trace, core_session_io, session_core)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_two_sided.py tests/test_core_trace.py \
  tests/test_trader_core_persistence.py tests/test_trader_session_core.py -q

# A regression and every other user of engine / scheduling / consume_core_outbox / the codec
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_core.py tests/test_follow300_replay.py tests/test_strategy_scheduling.py \
  tests/test_strategy_recovery_quotes.py tests/test_strategy_late_fills.py tests/test_strategy_budget.py \
  tests/test_strategy_imports.py tests/test_kill_gate.py tests/test_mid_spike.py \
  tests/test_trader_core_recovery.py tests/test_trader_core_execution.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_unsettled_buy_recovery.py \
  tests/test_trader_wallet_store.py tests/test_trader_wallet_host.py tests/test_trader_match_lifecycle.py \
  tests/test_trader_core_state_report.py tests/test_trader_engine_seams.py \
  tests/test_trader_live_execution.py tests/test_trader_dust_sweep.py tests/test_live_archives.py -q

# A's golden test files untouched
git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py

# McCabe stays under 10 (the reason for decision 1)
uv run ruff check --select C901 src/strategy/engine.py src/trader/core_session_io.py

# file sizes
wc -l src/trader/core_persistence.py src/trader/core_trace_codec.py tests/test_trader_core_persistence.py  # each < 1000

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/strategy/types.py src/strategy/lifecycle.py src/strategy/engine.py src/strategy/scheduling.py \
  src/trader/session_core.py src/trader/core_persistence.py src/trader/core_session_io.py \
  src/trader/core_trace_codec.py tests/test_strategy_two_sided.py tests/test_core_trace.py \
  tests/test_trader_core_persistence.py
make lint
git status --short   # only the untracked backtest dirs remain
```

Expected: all green, empty `git diff` for the two A test files, no C901 finding. If `make lint` reformats files, re-stage them and run it again. Leave `PYTEST_N` unset. A full serial suite is STEP-010's job, but run `make test` here too if time allows.

Then, per the implement skill:
- One commit in E on `main`. Suggested message: `Deliver pair merges to the core as a Merged event through the outbox.`
- Set `passes: true` for STEP-004 in `W/tasks/two-sided-live-b/feature.json`.
- Append to `W/tasks/two-sided-live-b/progress.txt`. Record decisions 1, 2, 6 and 8, plus the STEP-006 handoff about the outbox cursor on a clean core.
- No push.
