# STEP-006 plan: LIVE-02 reconcile the core sub-tick leftover with the ledger

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `e76163b1`. All paths below are relative to that worktree unless they start with `poly-maker/` or `runs/`.

Evidence script and output: `orchestrate/work/plan-step-006/incident_check.py`, `incident_check.txt`.

## 0. Result in one paragraph

The core keeps its "zero a leftover below one share tick after a SELL" rule for trading, and it now also remembers the amount it zeroed, per token, in a new `StrategyState.share_residue` field. The field goes into the checkpoint with a default, so old checkpoints still load. `_position_mismatch` accepts a store size from the core size up to the core size plus that remembered amount plus half a tick. `share_qty_matches` stops flooring both sides to a cent: it compares raw sizes with the half tick and still treats two sub-tick sizes as equal. With the recorded numbers, all 7 audit incidents pass, while a missed fill of 0.02 or more still enters recovery. Size: about +30/−5 lines in 5 production files, plus tests. No strategy decision, no trace digest, no schema version, and no config changes.

## 1. What is broken today (evidence)

1. The core zeroes the leftover after a SELL. `_credit_sell_inventory` computes `leftover = drop_share_residue(qty_before - qty)`. If `share_floor(leftover) <= 0`, it writes `qty=0.0` (`src/strategy/lifecycle.py:496-518`, `src/shared/utils/trading.py:24-28`). The zeroed amount is not stored anywhere.
2. The store keeps the on-chain leftover. Two paths put it there:
   - The REST positions poll runs every 20 s (`config/trading.toml:10`, `poly-maker/src/polymaker/engine.py:590-593`). `WalletStateStore.reconcile_positions` writes the REST size into sqlite when it is above the store size and within `max(1.0, 0.02 * rest)` (`src/trader/wallet_store.py:286-318`). The REST size is the chain balance, so it includes the leftover.
   - The mid-session write-off `_write_off_sell_residue` runs only on a CONFIRMED SELL, and only if the store size is still below one tick (`src/trader/match_worker.py:798-807`, `:976-977`, `src/trader/wallet_host.py:1100-1110`). If a new BUY lands first, the leftover stays.
   - Docker log, `grid-3011820-m1`: the write-off zeroed 0.009394 at 09:24:47. The store matched the core at 183.478261 at 09:25:37. At 09:26:52 the store was `183.4876` (4-decimal REST value) against core `183.478261` (`runs/live-a-money-audit-20261009/evidence/live-docker.log.gz:35839-35882`).
3. `share_qty_matches` floors both sides to a cent and then compares with 0.005 (`src/trader/wallet_store.py:27-28`). A raw gap of 0.001468 becomes 0.01 when the two sizes sit on different cents.
4. `_position_mismatch` uses that check for core size against store size. On failure it returns `Recovery`, which cancels BUYs and sets `sell_only` (`src/trader/session_core.py:583-598`).
5. The audit numbers (`runs/.../work/a2-grok-recovery/residue.json`, docker mismatch lines), checked by `incident_check.txt`:

| Match | core | store | gap | core dropped | today | raw half tick | planned |
|---|---|---|---|---|---|---|---|
| 9030565813 | 585.6993140000001 | 585.700782 | 0.001468 | 0.001468 | mismatch | ok | ok |
| 9030693809 | 285.29690500000004 | 285.302405 | 0.005500 | 0.005556 | mismatch | mismatch | ok |
| 9030822324 | 147.263637 | 147.2746 | 0.010963 | 0.010981 (2 drops) | mismatch | mismatch | ok |
| grid-3011793-m3 | 153.85881 | 153.8622 | 0.003390 | 0.003449 | mismatch | ok | ok |
| grid-3011820-m1 | 183.478261 | 183.4876 | 0.009339 | 0.009394 | mismatch | mismatch | ok |
| grid-3008331-m2 | 8.493846000000005 | 8.50084600000001 | 0.007000 | 0.007074 | mismatch | mismatch | ok |
| grid-3008331-m4 | 86.53659599999999 | 86.5448 | 0.008204 | 0.008261 | mismatch | mismatch | ok |

A raw half-tick compare alone fixes 2 of 7, as the audit says. The REST rounding keeps every gap a little below the dropped amount (at most 6e-5).

## 2. Design decisions

1. **Keep the core drop for trading.** The drop feeds `winding_down`, the cutoff checks, the sell size, and the B core (`src/strategy/quoting.py:325-402,804-901`). Removing it changes strategy decisions and the backtest. That is the "tradability" half of the step, and it stays as it is.
2. **Remember the dropped amount in `StrategyState`, not in `TokenInventory`.** `digest_state` hashes `jsonable(state.inventory)` and an explicit list of other fields, not the whole state (`src/trader/core_trace_codec.py:239-267`). `decode_inventory` requires exact keys (`core_trace_codec.py:106,413-420`). A new `StrategyState` field is not hashed and not in the trace events. Old traces replay, follow300 digests stay the same, and the backtest only computes a value nobody reads. `CoreMemory.state` covers `revert` with no change (`session_core.py:398-421`).
3. **One update point.** `_credit_sell_inventory` is the only place that zeroes a token (`lifecycle.py:505`). Known SELL fills, unknown SELL fills (`_pend_fill`), `OwnershipResolved` from the dust sweeper (`src/trader/dust_sweep.py:239`), and `Merged` all pass through it. In the drop branch: `residue = max(0.0, residue + qty_before - qty)`. A positive value is the zeroed leftover. A SELL above the held size, for example the dust sweeper selling 0.01 of accumulated chain dust, is negative and uses up the residue.
4. **A range, not a fixed tolerance.** The store can hold none of the dropped dust (write-off), all of it (REST), or a 4-decimal rounding of it. So `_position_mismatch` accepts `core <= store <= core + residue + HALF_SHARE_TICK`, plus the existing raw match. The extra room is exactly the core's own dropped amount, so a fill the core missed still fails the check.
5. **`RecoveryVerified` does not reset the residue.** Verify copies the store size into the core (`src/trader/core_recovery.py:207-224`). If the store had written the dust off at that moment, REST can add it back later, and a reset would give a false recovery. If the store already held the dust, keeping the residue only widens the upper bound by that dust. `apply_recovery_verified` stays unchanged.
6. **Persist the residue in the checkpoint.** At restart `_restore_open_round` zeroes a sub-tick store size (`match_worker.py:832-837`), and REST adds the chain dust back within 20 s. Without the saved residue, the first BUY after a restart enters recovery again. `StructuralCheckpoint` gets `share_residue: tuple[float, float] = (0.0, 0.0)`. `decode_checkpoint` uses `msgspec.convert` (`src/trader/core_persistence.py:249-265`), which fills the default for an old row and ignores the field in a rollback (checked in the worktree venv). The `schema_version` stays 3. The trace `reset` row uses the same codec (`core_trace_codec.py:854-859`).
7. **`share_qty_matches` compares raw sizes.** Its other callers compare a chain balance (1e-6 units) with the store or the ledger (`core_recovery.py:186-189,327-330`, `src/trader/engine_seams.py:904`). Before `9994b8a9` these callers used a raw half-tick compare (`balances_match_ledger`). The new body is "both below one tick, or raw gap at most 0.005". The sub-tick branch keeps a written-off store (0) equal to the chain dust in the recovery proof.
8. **No change to the write-off paths, the store, or REST.** The range in decision 4 accepts both store states, so the racy write-off can stay. Making the store mirror the core drop does not work: the next REST round writes the dust back (item 2 in section 1).
9. **B uses the same check.** `two_sided_worker.py:106` builds a `LiveCore`, so B gets the same correction. B gets no new behavior beyond fewer false recoveries.

## 3. Changes

### 3.1 `src/strategy/types.py`

Add one field at the end of `StrategyState` (after `delta_gate_open`, `types.py:300`):

```python
    share_residue: tuple[float, float] = (0.0, 0.0)
```

`empty_state` needs no change (default).

### 3.2 `src/strategy/lifecycle.py`

In `_credit_sell_inventory` (`lifecycle.py:496-518`), first line of the `if leftover <= 0:` branch:

```python
        state = _add_share_residue(state, token_index=token_index, qty=qty_before - qty)
```

New helper next to `_set_token` (`lifecycle.py:442-445`), in the same style:

```python
def _add_share_residue(state: StrategyState, *, token_index: int, qty: float) -> StrategyState:
    residue = list(state.share_residue)
    residue[token_index] = max(0.0, residue[token_index] + qty)
    return replace(state, share_residue=(residue[0], residue[1]))
```

Do not touch `_debit_rung_lots`, `_credit_buy_inventory`, or `apply_recovery_verified`.

### 3.3 `src/trader/core_persistence.py`

1. `StructuralCheckpoint` (`core_persistence.py:157-180`): last field `share_residue: tuple[float, float] = (0.0, 0.0)`.
2. `snapshot_checkpoint` (`:386-459`): `share_residue=state.share_residue,`.
3. `apply_checkpoint` (`:528-567`): `share_residue=checkpoint.share_residue,`.

No upgrade function, no schema bump. The file goes from 1001 to about 1004 lines. A split is not part of this step.

### 3.4 `src/trader/wallet_store.py`

Replace `share_qty_matches` (`wallet_store.py:27-28`):

```python
def share_qty_matches(left: float, right: float) -> bool:
    if share_floor(left) <= 0.0 and share_floor(right) <= 0.0:
        return True
    return abs(left - right) <= HALF_SHARE_TICK
```

### 3.5 `src/trader/session_core.py`

1. Imports: `HALF_SHARE_TICK` from `shared.utils.trading`, `log_event` from `trader.diag`. `trader.diag` has no import of `session_core`. `book_strip`, which `session_core` already imports, already imports `trader.diag`.
2. Module-level helper:

```python
def _matches_ledger(core: float, residue: float, store: float) -> bool:
    return share_qty_matches(core, store) or core <= store <= core + residue + HALF_SHARE_TICK
```

3. `_position_mismatch` (`session_core.py:583-598`):

```python
    def _position_mismatch(self, sources: LiveSources) -> Recovery | None:
        if self._state.sell_only:
            return None
        yes, no = self._state.inventory
        residue = self._state.share_residue
        if _matches_ledger(yes.qty, residue[0], sources.store_yes) and _matches_ledger(
            no.qty, residue[1], sources.store_no
        ):
            return None
        logger.warning(
            "trader core/ledger position mismatch core=%s/%s residue=%s/%s store=%s/%s",
            yes.qty, no.qty, residue[0], residue[1], sources.store_yes, sources.store_no,
        )
        log_event(
            "position_mismatch",
            yes_token=self._yes,
            core=[yes.qty, no.qty],
            residue=list(residue),
            store=[sources.store_yes, sources.store_no],
        )
        buys = tuple(order.order_id for order in self._state.orders if order.side == "BUY")
        return Recovery(now_ns=sources.now_ns, restored_buy_ids=buys)
```

The diag record has the raw core, residue, and store values. It is written only on a mismatch, so the result is always "mismatch". A record on every match would come every cycle. `log_event` does nothing when diagnostics are off (`src/trader/diag.py:120-132`), so paper and tests are not affected.

### 3.6 Not changed

`match_worker.py` (both write-off calls), `wallet_store.reconcile_positions`, the store position math, `engine_seams.py`, `core_recovery.py`, `quoting.py`, `core_trace_codec.py`, the backtest, config, and `poly-maker`. No new comments or docstrings in new code (review rule in `.feature-json.config.json`).

## 4. Tests

Use the existing fakes. Fill events with `order_id="missing"` go through `_pend_fill(reason="unknown")` and credit inventory at once, as in `test_subtick_sell_residue_flattens` (`tests/test_strategy_core.py:1111`).

### 4.1 `tests/test_trader_wallet_store.py`

Replace `test_share_qty_matches_floors_to_the_same_tick` (`:36-39`) with a raw-size test:
- True in both argument orders: `(585.6993140000001, 585.700782)`, `(153.85881, 153.8622)` (the 0.001468 and 0.003390 gaps across a cent).
- True: `(0.008926, 0.0)`, `(0.0, 0.009394)` (both below one tick).
- False: `(198.35, 198.3567)` (same cent, gap 0.0067), `(3.18, 0.0)`.

### 4.2 `tests/test_strategy_core.py` (residue arithmetic through real fills)

1. Hold 433.739394, SELL 433.73: qty 0.0, `share_residue[0] == pytest.approx(0.009394)`.
2. Then BUY 5.0055: qty 5.0055, residue unchanged. SELL 5.0: residue about 0.014894 (accumulation over one tick).
3. Then SELL 0.01 at qty 0 (dust sweep): residue about 0.004894. A SELL far above the residue floors it at 0.0.
4. An exact SELL (no leftover) and a SELL that keeps a whole-tick leftover do not change the residue.
5. `apply_merged` that leaves a sub-tick leftover adds it to the residue.

### 4.3 `tests/test_trader_session_core.py`

1. Parametrized over the 7 rows of section 1 (token index, core qty, dropped amount, store): set `inventory` and `share_residue` with `replace` on a primed core. After `drive`, `sell_only` and `recovery_pending` are False, and no BUY has `cancel_reason == "recovery"`. This is the unit form of the historical check for 9030565813, grid-3011793-m3, and grid-3011820-m1 (0.009394 after 433.73 of 433.739394).
2. Update `test_held_subtick_ledger_drift_does_not_queue_recovery` (`:702-716`): set `share_residue=(0.0, 0.0067)` so 198.35 against 198.3567 stays without recovery. Add the same gap with residue 0: it now queues recovery. That is the intended behavior change.
3. A real mismatch still enters recovery: core 183.478261 with residue 0.009394 against store 184.478261 (missed BUY), 182.478261 (missed SELL), and 183.498261 (0.02, just above residue plus half a tick).
4. Restart: a flat core with `share_residue=(0.009394, 0.0)`. Run `export_checkpoint`, `encode_checkpoint`, `decode_checkpoint`, then `restore_checkpoint` on a new `_core()`. The residue is the same. Then set inventory 183.478261. Assert `sell_only is False` before `drive`. Restore the core flat on purpose: `apply_checkpoint` forces `sell_only` for held inventory without rungs (`core_persistence.py:536-541`), and `_position_mismatch` skips a `sell_only` core, so a held restore passes without testing anything. Drive with store 183.4876: no recovery.
5. Diag: monkeypatch `trader.session_core.log_event`. Capture into a frozen dataclass, as in `tests/test_trader_book_guard.py:46`. A mismatch writes one `position_mismatch` with `core`, `residue`, `store`. A gap that the residue covers writes nothing.
6. Keep `test_ledger_mismatch_queues_recovery`, `test_subtick_ledger_gap_does_not_queue_recovery`, and `test_whole_tick_ledger_gap_still_queues_recovery` unchanged. They pass under the new rules.

### 4.4 `tests/test_trader_core_persistence.py`

1. Round trip: a state with `share_residue=(0.009394, 0.003449)` goes through `snapshot_checkpoint`, `encode_checkpoint`, `decode_checkpoint`, and `apply_checkpoint`, and keeps the value.
2. Existing format: encode a current checkpoint, remove the `share_residue` key from the JSON, decode. The result is `(0.0, 0.0)` and the other fields are unchanged. Add the same assert to the v1 decode test (`:357`).

## 5. Behavior changes and risks

1. After a SELL that leaves dust, a store size between the core size and the core size plus its dropped dust no longer queues recovery. This holds for the next BUY, for several drops in one map, and after a restart. In the recorded cases this keeps the bids that recovery cancelled: c96, c97, c102 on 9030565813 and c40 on grid-3011793-m3 (the audit header-commit replays, plan and digest parity 0, `runs/.../reports/a2-grok-recovery.md`).
2. A store above the core by more than half a tick inside the same cent, with no dropped dust behind it, now queues recovery. Today it passes. The REST rounding (at most 6e-5) is far below half a tick.
3. Known limit: a missed BUY smaller than the residue plus 0.005 is not detected. The residue only grows within one map, by less than 0.01 per sub-tick exit. The commit message must state this limit, because new code has no comments.
4. Chain proofs (`prove_token_balances`, `accept_if_proven`, the restore in `engine_seams`): a cross-cent gap of 0.005 or less now matches. A same-cent gap above 0.005 now fails. The case "write-off, then a new BUY before the next REST round" can fail the proof for one round (20 s) when the dust is above 0.005. Today the same case fails when the sum crosses a cent.
5. Sessions that span the deploy have a checkpoint without the residue (0.0). If REST adds old dust back and a BUY follows, one recovery can still happen for that map.
6. Not fixed, outside this step: a `RecoveryVerified` that copies a store with dust only sets `sell_only` from `total_qty > 0` (`lifecycle.py:408-420`). This keeps the map sell-only. This exists today. The audit cases verified at 0.0 (trace lines 34903 and 21425).
7. Goldens are not run (user rule). The new field is not in `digest_state` and no quoting code reads it. `tests/test_follow300_replay.py` and `tests/test_extraction_oracle.py` therefore have no input to change.
8. A full replay of the 9030565813 and grid-3011793-m3 tapes is not part of this step. The tapes need their header commits (`f57e70e1`, `99e9bde2`). On the current tree the replay gives 213 plan mismatches (audit). It is also a long job, which `00-context.md` forbids. Test 4.3.1 runs the recorded numbers through the real `_position_mismatch`.

## 6. Verification (no golden tests)

Run from the worktree. Never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Evidence numbers: `python3 -I orchestrate/work/plan-step-006/incident_check.py` (workspace path) prints `planned=True` on all 7 rows and False for the three real-mismatch rows.
2. Targeted tests:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trading.py tests/test_trader_wallet_store.py tests/test_strategy_core.py \
  tests/test_strategy_late_fills.py tests/test_trader_session_core.py tests/test_trader_core_persistence.py \
  tests/test_trader_core_recovery.py tests/test_core_trace.py tests/test_trader_engine_seams.py \
  tests/test_trader_dust_sweep.py tests/test_trader_match_lifecycle.py tests/test_trader_two_sided_worker.py \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

3. Full suite without goldens, required before the commit (about 30-50 s):

```
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

   Baseline on `e76163b1`: 3146 passed (progress log). The new count is 3146 plus the new tests, with 0 failures.
4. Typecheck: `uv run python -m basedpyright` (whole project, as the feature defines it).
5. Lint: `uv run python -m pre_commit run --files src/strategy/types.py src/strategy/lifecycle.py src/trader/core_persistence.py src/trader/wallet_store.py src/trader/session_core.py <changed test files>`.
6. Check by reading: `git diff --stat` shows only the 5 production files and the 4 test files above. `git -C ../poly-maker status --short` shows no change. `git diff` adds no comment or docstring lines in `src/`.
