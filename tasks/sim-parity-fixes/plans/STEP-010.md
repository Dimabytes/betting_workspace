# STEP-010 plan: follow300 on the new cycle (signal and kill wakes, bad YES book, drawn latency)

Repo `esports-trader` (C) on `main`, HEAD `3b3c5393`. All line numbers are at that commit.

## 0. Result

After this step, follow300 (Dota and LoL) and two-sided run on one latency model and one wake set:

- A model signal or board tick (`SIGNAL:` alert) and a kill-gate tick (`KILL:` alert) wake the quoter, as live A does on every feed tick and kill tick.
- A crossed or locked YES book reaches the follow300 core as `books=None`. The core cancels everything and places nothing (`stale_book`), as the live A book guard does. Two-sided keeps its own skip.
- The Nautilus insert is 45 ms (`VENUE_INSERT_MS`) for both products. Follow300 draws place latency from `PLACE_LATENCY_MS` and defers the submit by the rest of the draw. A cancel is the place draw minus 15 ms.
- `ORDER_INSERT_LATENCY_MS`, `ORDER_CANCEL_LATENCY_MS`, the config fields `order_latency_ns` and `cancel_latency_ns`, and the dead 20 ms cancel floor are deleted.
- `--buy-settle-ms` is already gone (deleted in STEP-009, `progress.txt` STEP-009 entry). That item is a no-op.
- One smoke map measures `CancelUnsettled` → `BuySettled` in sim time and records it in `progress.txt`.
- The worktree and branch removal cannot be done by the implementer under the run's hard rules (§7). It goes back to the orchestrator.

## 1. Evidence

### 1.1 Live A (what the sim copies)

- **Feed tick wakes.** `_on_event` → `_quote_pm` → `_write_decision` → `self._host.engine._wake_cid(self._cid)` (`src/trader/match_worker.py:445-483`). The only ticks that skip the wake are history-gap ticks (`:451-456`) and a journal write failure (`:475-482`).
- **Board reaction wakes.** `_quote_board` → `_quote_pm` (`:575-582`).
- **Kill tick wakes.** `_on_kill_tick` enqueues `KillGateUpdate` and calls `_wake_cid` (`:545-566`), dispatched from `run()` (`:249-251`).
- `TwoSidedWorker` inherits `run()` and `_on_kill_tick`: `rg "kill" src/trader/two_sided_worker.py` returns nothing. So live B also wakes on a kill tick.
- **Bad YES book.** `_check_yes_book` returns missing, empty, one_sided, or crossed (`bid.price >= ask.price`) (`src/trader/host_resources.py:130-141`). The guard runs `run_core_cycle(..., books=None)` and sends only `planned.to_cancel` (`:153-187`). It is installed for follow300 only (`:210-220`, called at `:312`).
- With `books=None`, `_quote_after_gates` returns `_blocked(state, "stale_book")` (`src/strategy/quoting.py:893-894`). `_blocked` cancels every order and plans nothing (`:100-105`).
- `books_from_md` has no crossed check, so a crossed NO book reaches the core in live too (`src/trader/book_strip.py:38-110`).
- **One gateway, one latency table.** The cancel model plus 32 ms gives p50 95 and p90 153 ms. Wallet A's cancel path (decision → HTTP response) measures p50 79–89 and p90 117–189 ms (`investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md:321`). The verdict is PORT-SHARED: use B's place table for A until A has its own (`investigations/2026-10-10-sim-followup/reports/port.md:49`). 45 ms for follow300 is only correct together with the deferred submit (`port.md:50`).
- **Target numbers.** On wallet A, `CancelUnsettled` → `BuySettled`:
  - no fill: n 2985, p10 112, p50 143, p90 219 ms;
  - with a fill: n 193, p50 6229 ms, which includes REST tails, so the target after STEP-004 is about 7.25 s.

  Source: `investigations/2026-10-10-sim-followup/reports/latency.md:125-133`.

### 1.2 The sim today

- `DotaMakerStrategy._on_signal_alert` (`src/backtest/strategy.py:571-576`) syncs the signal and calls `_arm_wake` only.
- `_on_kill_gate_alert` (`:578-585`) drives the `KillGateUpdate` and calls `_arm_wake` only.
- `_arm_wake` (`:587-588`) just stores `_core_deadline_ns`. An idle quoter keeps its old timer (`_end_busy`, `:619-632`), so a signal can wait up to `fallback_timer_s` before a cycle sees it.
- `KillGateUpdate` and `SignalUpdate` are store events: `_STORE_EVENTS` at `:151`, and `_executes_plan` at `:144-148` does not run a plan for them. Only the quoter cycle acts on them.
- `SIGNAL:` alerts are armed for every feed tick plus every board tick (`:348-358`). `KILL:` alerts are armed per kill gate (`:359-368`).
- **Book view.** `_snapshot_books` (`:1290-1319`) returns None for a missing book (`:1295-1296`) and for a missing bid or ask (`:1299-1300`). It does not check a crossed book. It feeds the core from `_apply_received_book` (`:419-421`) and from `_sync_inputs` (`:884-890`) on every cycle path.
- **Latency.** Base `_draw_place_ns` returns `self._config.order_latency_ns` (`:712-713`) and `_draw_cancel_ns` returns `self._config.cancel_latency_ns` (`:715-716`). `run_batch` sets 175 ms insert for follow300 and 45 ms for two-sided (`src/backtest/run.py:657-660`). `build_order_latency` returns the anonymous tuple `(StaticLatencyConfig, order_latency_ns, cancel_latency_ns)` with the 60 ms cancel (`:324-343`).
- The cycle submit time is `now + cycle.http_ns + draw − order_latency_ns` (`strategy.py:689-692`). The `RELEASE:` alert is set at `timestamp + order_latency_ns` (`:973-976`).
- **Two-sided overrides.** `_draw_place_ns` and `_draw_cancel_ns` (`src/backtest/two_sided_core.py:164-172`) draw from `self._rng = random.Random(f"{latency_seed}:{match_id}")` (`:94`). The cancel is `max(draw − 15 ms, 20 ms)` (`:35-36`).
- The 20 ms floor never binds. `draw_latency_ns` cannot return less than `table_ms[0]`: for draw 0, `upper` is 1 and `share` is 0 (`src/backtest/live_latency.py:21-29`). `PLACE_LATENCY_MS[0] = 45` (`:10-13`), so the minimum cancel is 45 − 15 = 30 ms.
- **Seed.** `TwoSidedSettings.latency_seed = args.signal_cadence_seed` (`run.py:1632-1643`).
  - `--archives-only` drops `signal_cadence_seed` from the manifest (`:856`) and writes `_archive` (`:969-970`). That one archive result is grafted into every seed run (`scripts/run_seeds.sh:95-117`).
  - Today the archive replay has no randomness, so the graft is seed-free.

### 1.3 Old constants still in use

`rg -n "ORDER_INSERT_LATENCY_MS|ORDER_CANCEL_LATENCY_MS" src tests` finds:

- `src/shared/constants/strategy.py:17-20`
- `src/backtest/run.py:199-200, 342, 657, 836-837`
- `src/backtest/postprocess.py:59, 76-77`
- `tests/test_backtest_maker.py:60-61, 79-80, 205`
- `tests/test_backtest.py:73, 165`
- `tests/test_adapter_contract.py:25, 29`
- `tests/test_core_trace.py:37, 109`

Only run.py writes the manifest keys `insert_latency_ms` and `cancel_latency_ms`. Nothing else reads them (`rg` over src, scripts, and tests).

## 2. Design decisions

1. **Wakes go in the two base alert handlers.**
   - Add `self._wake_quoter(now_ns)` at the end of `_on_signal_alert` and of `_on_kill_gate_alert`. In the kill handler, `now_ns` comes from `event.ts_event`.
   - Two-sided already overrides `_on_signal_alert` and wakes (`two_sided_core.py:100-116`), so the signal change touches follow300 only.
   - Two-sided inherits the kill wake. That matches live B (§1.1). The DO-NOT-PORT verdict on `KillWakeMixin` (`port.md:72`) was about a mixin with 0 events on the Oddin maps. It was not a claim that live B does not wake.
2. **Crossed YES becomes `None` in `_snapshot_books`.**
   - After the loop builds both `TokenBook`s, return None when token 0 has `bid >= ask`. A locked book counts as crossed, as in live.
   - Token 0 is YES: `MarketContext.instrument_ids` follows `token_ids` order (`src/backtest/context.py:90-97`). Two-sided's skip also reads `instrument_ids[0]` (`two_sided_core.py:189-196`).
   - Missing, empty, and one-sided YES already return None. NO stays unchecked, as in live `books_from_md`.
   - One shared function covers every caller: book receipt, `_sync_inputs`, and two-sided `_gates_pair`.
3. **Two-sided keeps its skip unchanged.**
   - Its `_evaluate` override stays as it is. `_gates_pair` already treats a crossed YES as reduce-only, so for it None and crossed give the same result (`two_sided_core.py:124-128`).
   - One corner changes: a crossed YES, no feed tick since the cross, and a cancel response or settle in that cycle. In that case `_apply_cancel_responses` now plans on `books=None`, which pulls with `stale_book`. Before, it planned on the crossed book. Today it already behaves this way for a one-sided YES. Leave it, and note it in the risks.
4. **One latency model in the base class.**
   - Move `self._rng` into `DotaMakerStrategy.__init__` with the same seed string. Two-sided draws stay byte-identical for the same event order.
   - Inline the place draw into `_send_cycle_places` and the cancel draw into `_cancel_latency_ns`. Delete `_draw_place_ns` and `_draw_cancel_ns` in both classes.
   - The cancel is the place draw minus `CANCEL_LATENCY_DISCOUNT_MS`. Move that constant to `live_latency.py` next to the table. Delete `CANCEL_LATENCY_FLOOR_MS`: it is dead code (§1.2), and no number moves.
5. **The engine insert is a constant, not a config field.**
   - Delete `DotaMakerConfig.order_latency_ns` and `cancel_latency_ns`. The strategy reads `VENUE_INSERT_MS * NS_PER_MS` for the submit offset and the `RELEASE:` alert.
   - `build_order_latency()` takes no arguments and returns only the `StaticLatencyConfig`: insert and update `VENUE_INSERT_MS`, cancel 0. This also removes the anonymous tuple.
   - Minimal fallback, if the reviewer objects: keep `order_latency_ns` and only delete `cancel_latency_ns`. That costs the same call sites, so it is not recommended.
6. **`latency_seed` moves to `MatchKernelConfig`.**
   - `build_kernels` gets a required `latency_seed`. run.py passes `args.signal_cadence_seed`; `seed0_replay.py` passes 0.
   - `build_two_sided_kernel` copies `kernel.latency_seed`. `TwoSidedSettings.latency_seed` is deleted.
   - The seed already rides in `kernels` through `run_batch`, so no other signature changes.
7. **Archive runs stay seed-free with a parser guard.**
   - In `parse_args`, next to the `signal_cadence_seed < 0` check (`run.py:1580-1581`), reject `--archives-only` when `--signal-cadence-seed` differs from `LIVE_GRID_TIMING.seed`. The error says archive maps replay once for every validation seed at the default seed.
   - `run_seeds.sh` never passes a seed to the archive stage (`scripts/run_seeds.sh:102-104`), so nothing breaks.
   - Ceiling: archive maps share one latency stream (seed 0) across all validation seeds. The upgrade path is per-seed archives (drop the graft). Record this in `progress.txt`, not as a code comment.
8. **Manifest.**
   - Shared keys:
     - `insert_latency_ms` becomes `VENUE_INSERT_MS`;
     - `cancel_latency_ms` is replaced by `place_latency_ms` (the table as a list) and `cancel_latency_discount_ms`.
   - `_stamp_strategy` drops its now-shared `insert_latency_ms`, `place_latency_ms`, and `cancel_latency_discount_ms`. It also drops `latency_seed`, because non-archive manifests already carry `signal_cadence_seed`, and `cancel_latency_floor_ms`.
   - `_stamp_strategy` keeps `quoter_recompute_ms` (10, overriding the shared 6).
9. **No doc edits.** `docs/` and `AGENTS.md` in C have someone else's uncommitted edits (`git status`), and none of them mention the old constants outside `docs/experiments/` history.

## 3. Changes by file

### 3.1 `src/backtest/live_latency.py`

- Add `CANCEL_LATENCY_DISCOUNT_MS = 15` beside `HTTP_RESPONSE_MS` (`:14-18`).
- Nothing else changes.

### 3.2 `src/backtest/strategy.py`

- **Imports (`:45-50`).** Add `CANCEL_LATENCY_DISCOUNT_MS`, `PLACE_LATENCY_MS`, `VENUE_INSERT_MS`, and `draw_latency_ns` from `backtest.live_latency`. Add `import random`.
- **`MatchKernelConfig` (`:168-175`).** Add the required `latency_seed: int` after `max_position_levels`, and extend the docstring by the seed.
- **`DotaMakerConfig` (`:224-225`).** Delete `order_latency_ns` and `cancel_latency_ns`.
- **`__init__` (`:253-330`).** Add `self._rng = random.Random(f"{config.kernel.latency_seed}:{config.match_id}")` next to the other quoter fields (`:289-296`).
- **`_on_signal_alert` (`:571-576`).** After `_arm_wake`, call `self._wake_quoter(now_ns)`.
- **`_on_kill_gate_alert` (`:578-585`).**
  - Bind `now_ns` from `event.ts_event` once and use it for the lookup.
  - After `_arm_wake(out.next_wake_ns)`, call `self._wake_quoter(now_ns)`.
  - The `update is None` return stays before the wake.
- **`_send_cycle_places` (`:681-701`).**
  - The draw becomes `draw_latency_ns(table_ms=PLACE_LATENCY_MS, rng=self._rng)` in place of `self._draw_place_ns()` at `:689`.
  - At `:691`, `submit_ns` subtracts `VENUE_INSERT_MS * NS_PER_MS` in place of `self._config.order_latency_ns`.
- **Delete `_draw_place_ns` and `_draw_cancel_ns` (`:712-716`).**
- **`_cancel_latency_ns` (`:718-726`).** At `:722`, the latency becomes the place-table draw minus `CANCEL_LATENCY_DISCOUNT_MS * NS_PER_MS`. The per-cycle cache in `QuoterCycle.cancel_latency_ns` stays: one cancel draw per cycle, matching one batch DELETE.
- **`_send_place` `RELEASE:` alert (`:973-976`).** Use `VENUE_INSERT_MS * NS_PER_MS` in place of `self._config.order_latency_ns`.
- **`_snapshot_books` (`:1290-1319`).** After the `len(tokens) != 2` check (`:1317-1318`), return None when `tokens[0].bid >= tokens[0].ask`. Keep it one condition with no token-index branch inside the loop.

### 3.3 `src/backtest/two_sided_core.py`

- Delete:
  - `import random` (`:8`);
  - the `live_latency` import (`:14`);
  - `NS_PER_MS` (`:21`), if it has no other use (it has none after the draw methods go);
  - `CANCEL_LATENCY_DISCOUNT_MS` and `CANCEL_LATENCY_FLOOR_MS` (`:35-36`).
- Delete `TwoSidedSettings.latency_seed` (`:54`).
- In `build_two_sided_kernel` (`:77`), copy `latency_seed=kernel.latency_seed`. `TwoSidedKernelConfig.latency_seed` (`:45`) stays.
- Delete the `self._rng` line in `__init__` (`:94`).
- Delete `_draw_place_ns` and `_draw_cancel_ns` (`:164-172`).
- `_evaluate` (`:189-196`) and `_gates_pair` (`:118-129`) are unchanged.

### 3.4 `src/backtest/run.py`

- **Imports.**
  - Drop `ORDER_CANCEL_LATENCY_MS` and `ORDER_INSERT_LATENCY_MS` (`:199-200`).
  - Drop `CANCEL_LATENCY_DISCOUNT_MS` and `CANCEL_LATENCY_FLOOR_MS` from the `two_sided_core` import (`:166-168`).
  - Import `CANCEL_LATENCY_DISCOUNT_MS` from `backtest.live_latency` (`:64-71`).
- **`build_order_latency` (`:324-343`).**
  - No parameters; returns `StaticLatencyConfig` with insert and update `VENUE_INSERT_MS` and cancel 0.
  - Rewrite the docstring in two sentences: engine insert is the live place floor; the strategy defers each submit by the rest of its draw and owns the cancel delay. Drop the September 175 ms note.
- **`build_kernels` (`:478-509`).** Add the required keyword `latency_seed: int` and pass it to every `MatchKernelConfig`.
- **`build_strategy_configs` (`:512-524`).** Delete the `order_latency_ns` and `cancel_latency_ns` parameters (`:518-519`) and their payload keys (`:589-590`).
- **`run_batch` (`:657-660`, `:677-678`).**
  - Delete the `insert_latency_ms` conditional.
  - Call `build_order_latency()` for the latency config only.
  - Stop passing the two latency arguments.
- **`build_run_manifest` (`:836-837`).**
  - `insert_latency_ms` becomes `VENUE_INSERT_MS`.
  - Replace `cancel_latency_ms` with `place_latency_ms: list(PLACE_LATENCY_MS)` and `cancel_latency_discount_ms: CANCEL_LATENCY_DISCOUNT_MS`.
- **`--signal-cadence-seed` help (`:1480-1485`).** Say it seeds grid-v1 sampling and the order-latency draws.
- **`parse_args` (`:1580-1581`).** Add the archives-only guard from §2.7 right after the negative-seed check.
- **`_two_sided_settings` (`:1632-1643`).** Delete `latency_seed=`.
- **`_stamp_strategy` (`:1659-1683`).** Drop:
  - `insert_latency_ms`
  - `latency_seed`
  - `place_latency_ms`
  - `cancel_latency_discount_ms`
  - `cancel_latency_floor_ms`
- **Main `build_kernels` call (`:2435-2440`).** Pass `latency_seed=args.signal_cadence_seed`.

### 3.5 `src/backtest/seed0_replay.py`

- At `:164-169`, pass `latency_seed=0` to `build_kernels`. This matches the seed-0 identity contract of `replay_seed0_identity_inprocess`.

### 3.6 `src/backtest/postprocess.py`

- Replace the import at `:59` with `CANCEL_LATENCY_DISCOUNT_MS`, `PLACE_LATENCY_MS`, and `VENUE_INSERT_MS` from `backtest.live_latency`.
- In `ASSUMPTIONS` (`:74-78`), the latency clause says: accept lands a drawn place latency after the cycle decision (live quantile table, floor `VENUE_INSERT_MS`, median the 0.5 point of the table, 78 ms), and the cancel release is that draw minus `CANCEL_LATENCY_DISCOUNT_MS`.
- `tests/test_backtest_postprocess.py:801` compares against the same `ASSUMPTIONS` symbol, so the text can change freely.

### 3.7 `src/shared/constants/strategy.py`

- Delete the comment at `:17-18` and both constants at `:19-20`.

### 3.8 Not touched

- `poly-maker`.
- The live trader.
- `two_sided_core._evaluate` and `_gates_pair`.
- `QUOTER_RECOMPUTE_MS` (6 and 10).
- `docs/`.
- `scripts/run_seeds.sh`: its archive stage passes no seed, so it already satisfies the guard.

## 4. Edge cases the implementer must keep

1. **Finished or unknown alerts.** A signal alert after `_finished` returns before the wake (`strategy.py:572-573`). A kill alert whose timestamp is not in `_kill_gates` returns before the drive and the wake (`:581-583`).
2. **Wakes fold into the debounce.** A wake while sleeping only sets dirty. It does not re-arm, so two events inside one debounce give one cycle (`_wake_quoter`, `:590-595`). A wake while busy gives a fresh debounce at busy end (`:619-623`).
3. **Wakes after game end.** They still run `_on_quoter`, but `_run_cycle` returns only the recompute time (`:635-636`).
4. **Crossed means `bid >= ask` on YES only.** A crossed NO book still reaches the core, as in live.
5. **The crossed-YES block also pulls SELLs.** `_blocked` cancels all orders. The live guard does the same: it sends every `to_cancel`.
6. **Floor draw sends at once.** A draw at the floor (45 ms) with `http_ns == 0` gives `submit_ns == now_ns` and takes the immediate-send path (`:694-696`). That is right: the engine adds the 45 ms.
7. **Cancels outside a cycle draw one by one.** Cutoff (`_on_buy_cutoff`) and game-end cancels run outside a cycle and draw one cancel latency per order, not one per batch. This was already true for two-sided after STEP-009. Leave it.
8. **Same draw stream for two-sided.** Same seed string, same call order. Two-sided numbers move only through the kill wake (maps with kill gates) and the crossed-YES corner in §2.3.
9. **Old archives replay on their own.** An `_archive/` built before this step no longer matches the expected manifest (insert 175 → 45, keys changed). Seed runs then replay archive maps themselves, as `run_seeds.sh:92-94` describes. That is safe, only slower.
10. **Live skips a few wakes the sim does not.** Live skips the wake on a history-gap tick (`match_worker.py:451-456`); the sim wakes on every feed tick. The sim gets an extra cycle on those ticks. Book rows already wake both sides, so do not model this.

## 5. Tests

The no-comments rule applies to tests too. Drop the comment at `tests/test_backtest.py:172` when that test is rewritten.

### 5.1 Harness and constants (`tests/test_backtest_maker.py`)

- **Pin the draw in `build_maker_harness` (`:261-357`).** Monkeypatch `backtest.strategy.draw_latency_ns` to return `table_ms[0]` in ns. The place draw is then 45 ms (no deferral when `http_ns == 0`) and the cancel is 30 ms. Two-sided `_harness` and the adapter-contract `build_drivers` both go through this harness, so they inherit the pin.
- **Constants (`:60-61`, `:79-80`).**
  - `INSERT_LATENCY_NS` becomes `VENUE_INSERT_MS` in ns.
  - `CANCEL_LATENCY_NS` becomes `(VENUE_INSERT_MS − CANCEL_LATENCY_DISCOUNT_MS)` in ns, which is 30 ms.
  - Drop the `shared.constants.strategy` latency imports.
- **`build_maker_config` (`:182-258`).**
  - Delete the `build_order_latency` call (`:205`) and the two config fields (`:235-236`).
  - Add `latency_seed=0` to `MatchKernelConfig` (`:238`).
  - Add a keyword `debounce_ms: int = 0`, passed to `follow300_policy` (`:216`), for the new debounce test. Live uses 100 ms from the poly-maker config (`run.py:462-468`).
- **Fills that test a fill inside the cancel latency.** These sit at +40 ms inside the old 60 ms window. Move them under the new 30 ms window, at +20 ms, so each still tests what its name says:
  - `test_fill_during_cutoff_cancel_still_cancels` (`:1071`)
  - `test_fill_during_reprice_cancel_keeps_partial_resting` (`:1147`)
  - `test_fill_during_cancel_and_repeat_cancel_and_reject_keep_submitted` (`:2347`)

  The other `CANCEL_LATENCY_NS` uses are symbolic and follow the new value. `fire_cancel_release` fires at the time it is given, not at the scheduled alert (`:426-451`).
- **`test_follow300_cycle_places_after_its_cancels_http` (`:3425-3450`).** Patch the draw to 100 ms inside the test. It then asserts, all in ms:
  - the cancel alert at start + 85;
  - `PLACE_AT:` at start + 85 + 32 + (100 − 45);
  - busy = 6 + (85 + 32) + (100 + 32).

  This pins the follow300 discount and the deferred submit in one test. Do not add a separate draw test.

### 5.2 New follow300 tests (`tests/test_backtest_maker.py`, next to the STEP-009 block at `:3319-3501`)

1. **`test_follow300_signal_alert_wakes_the_quoter`**
   - Setup: harness with `debounce_ms=100`; `_evaluate(0)`, `accept_all`, quoter set idle and clean.
   - Call `_on_signal_alert` at 2 s.
   - Assert: quoter state is `sleeping`, and `_quoter_alert_ns` contains 2 s + 100 ms.
2. **`test_follow300_kill_gate_alert_wakes_the_quoter`**
   - Setup: the same harness. Build a `KillGateUpdate` at 2 s the way `tests/test_kill_gate.py:138-153` does, and put it into `_kill_gates` as `on_start` does (`strategy.py:359-363`).
   - Call `_on_kill_gate_alert` at 2 s.
   - Assert: the core holds the gate, the quoter is `sleeping`, and a quoter alert sits at 2 s + 100 ms.
3. **`test_follow300_two_book_rows_inside_the_debounce_run_one_cycle`**
   - Setup: `debounce_ms=100`; book rows at 250 ms and 290 ms. Wrap `_run_cycle` on the instance to record start times, as `test_book_rows_reach_the_core_after_the_view_delay` patches an instance method.
   - Fire `BOOK_RECEIVED` and `QUOTER` alerts up to 260 + 100 ms.
   - Assert: exactly one cycle, starting at 360 ms; no quoter alert at 300 + 100 ms; quoter `busy`.
4. **`test_follow300_bad_yes_book_cancels_without_placing`**
   - Parametrize the YES book over crossed (0.52 / 0.50) and locked (0.50 / 0.50).
   - Setup: start from the accepted three-rung ladder. Set the YES book fresh at the cycle start, and set `clock.timestamp_ns` to the start, as the STEP-009 cycle test does.
   - Call `_run_cycle`.
   - Assert:
     - `_core.books is None`;
     - every live order has `cancel_reason == "stale_book"`;
     - `submitted` did not grow;
     - no `PLACE_AT:` alert.

### 5.3 Two-sided (`tests/test_backtest_two_sided_core.py`)

- **`_fastest_http` (`:127-131`).** Delete the `two_sided_core.draw_latency_ns` patch; the harness pins it now. Keep `HTTP_RESPONSE_MS = 0`.
- **`_two_sided_config` (`:91-124`).**
  - Delete `latency_seed=0` from `TwoSidedSettings` (`:112`).
  - Delete the `build_order_latency` call and the `order_latency_ns` override (`:115`, `:122`), and the import at `:60`.
- **`test_slow_draw_places_after_insert_and_skips_a_closed_order` (`:336-377`).** Patch `strategy_module.draw_latency_ns` (`:341`). Replace `strategy._config.order_latency_ns` with `VENUE_INSERT_MS * MS` (`:354`, `:363`). Drop the `two_sided_core` module import (`:48`) if nothing else uses it.
- **`test_config_builds_through_the_strategy_factory` (`:499-512`).** Keep the `kernel.latency_seed == 0` assert. The value now comes from `build_maker_config`'s kernel through `build_two_sided_kernel`.
- **New: `test_crossed_yes_book_skips_the_two_sided_cycle`.**
  - Setup: first tick and cycle place the two bids, as `test_first_wake_places_live_bids` does. Then `_set_books` with YES crossed (0.52 / 0.50).
  - Run `_cycle` with no feed tick in between.
  - Assert: no new submit, `_canceled == []`, and no `CANCEL:` alert added.
  - This pins "two-sided keeps its skip" against the follow300 test above.

### 5.4 `tests/test_backtest.py`

- **`test_order_latency_matches_venue_release_offsets` (`:163-173`).** Rewrite. `build_order_latency()` gives:
  - model insert = update = `VENUE_INSERT_MS` × 1e6;
  - model cancel 0;
  - `min(PLACE_LATENCY_MS) == VENUE_INSERT_MS` (no draw schedules a submit before its cycle).

  Drop `ORDER_INSERT_LATENCY_MS` from the imports (`:73`).
- **`test_strategy_configs_carry_no_policy_switches` (`:1012-1027`) and `test_lol_grid_v1_shifts_cutoff_and_game_end_by_source_lag` (`:1067-1082`).** Drop the two latency arguments and add `latency_seed=0` to `build_kernels`.
- **`test_run_manifest_records_model_name` (`:385-458`).** Extend it to assert:
  - `insert_latency_ms == VENUE_INSERT_MS`;
  - `place_latency_ms == list(PLACE_LATENCY_MS)`;
  - `cancel_latency_discount_ms == CANCEL_LATENCY_DISCOUNT_MS`;
  - `"cancel_latency_ms" not in manifest`.
- **New: `test_parse_args_rejects_a_cadence_seed_on_archives_only`.** argv `backtest --validation --name x --archives-only --signal-cadence-seed 3` raises `SystemExit` 2, like `:625-636`.

### 5.5 Other test files

- **`tests/test_feed_schedules.py:847-870`.** Add `latency_seed=0` to `MatchKernelConfig` and drop the two latency arguments of `build_strategy_configs`.
- **`tests/test_live_archives.py:107-146`.** Add `latency_seed=7` to `MatchKernelConfig`, drop the two config fields (`:134-135`), and assert that `decoded.kernel.latency_seed == 7` survives msgspec.
- **`tests/test_adapter_contract.py:25,29` and `tests/test_core_trace.py:37,109`.** `CANCEL_LATENCY_NS` is only a tape offset there. `fire_cancel_release` fires at the tape time (`adapter_contract_fixtures.py:418-428`), and core_trace is LiveDriver only. Replace the import with a local `60_000_000`.
  - Keep 60 ms, not the new 30: `test_fill_cancel_race_holds_rung` needs its +40 ms fill to land before the ack (`test_adapter_contract.py:127-131`).

## 6. Smoke check (`CancelUnsettled` → `BuySettled` on one map)

Throwaway script in `R/work/impl-010/`, not committed. It runs one Dota smoke map from `backtest.replay_inputs.SMOKE_MAPS` (`tests/fixtures/follow300_changes/smoke/`) through `backtest.seed0_replay.replay_seed0_identity_inprocess` (seed 0). Use one process per map: Nautilus logging cannot init twice (`seed0_replay.py:186`). Command, from C: `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python <R>/work/impl-010/<script>.py <match_id>`.

1. **Record cancel responses.** Before the replay, wrap `DotaMakerStrategy._ack_venue_cancel` at class level. For BUY core orders only, record the core id, `respond_ns = now_ns + HTTP_RESPONSE_MS` ms, and whether `filled_qty > 0`. Record before calling the original.
   - Live stamps `CancelUnsettled` at the HTTP response (`match_worker.py:994-1006`). The sim stamps it at the next cycle, together with `BuySettled`, so event-to-event in the sim is always 0. The gap that compares with live is response → `BuySettled` drive.
2. **Record settles.** Wrap `DotaMakerStrategy._drive` and record `now_ns` of every `BuySettled` per order id.
3. **Compute and print.** Gap = first `BuySettled` drive − `respond_ns`. Print n, p10, p50, and p90 separately for no-fill and filled. If one map gives fewer than 50 no-fill cancels, run the other five Dota smoke maps and pool them.
4. **Expected ranges.**
   - No fill:
     - about 106 ms when the cancel's cycle placed nothing (busy ends 6 ms after the response, then a fresh 100 ms debounce);
     - 183–260 ms when it also placed (6 + place draw + 32 + 100).
     - Live A is p10 112 / p50 143 / p90 219.
   - With a fill: about 7.25 s after the heard fill.
5. **Record, do not tune.** Write the numbers, the map ids, and the wall time in `progress.txt`. Do not tune any constant to hit 143. Report outliers.

## 7. Worktree and branch (`../esports-trader-cadence`, `two-sided-live-cadence`)

Read-only state at planning time:

- `git worktree list` shows the worktree at `6787bf3f`.
- `git merge-base --is-ancestor two-sided-live-cadence main` exits 0, so the branch is merged.
- The worktree is not clean:
  - 6 modified files, `git diff --stat` 566+/93−: run.py, strategy.py, two_sided_core.py, and three test files;
  - 2 untracked run dirs: `data/backtests/dota_maker/validation_*_liveb1008-957full-kb1` and `-kb2`, 4 KB each.

`git worktree remove` without `--force` refuses a dirty tree. The run's hard rules ban deleting what this run did not create, and ban branch deletion. So the implementer must not remove either one. Instead:

1. Save `git -C ../esports-trader-cadence diff` and `git -C ../esports-trader-cadence status --short` to `R/work/impl-010/` as new files, so nothing is lost when the user removes the worktree.
2. In the impl report, give the orchestrator the two commands the user can run after approving:
   - `git worktree remove --force ../esports-trader-cadence`
   - `git branch -d two-sided-live-cadence` (a plain `-d` works because the branch is merged).

## 8. Risks

- **Every follow300 run moves.** Insert 175 → drawn (median 78), cancel 60 → drawn (median 63, tail to 2.4 s), more wakes, and crossed-YES blocks. The goldens in `tests/test_follow300_replay.py` already fail until STEP-014. `LIVE` is promoted once, after all steps.
- **Two-sided corner.** A crossed YES with an arriving response, between feed ticks, now pulls with `stale_book` (§2.3). STEP-013 measures two-sided parity.
- **The worktree item cannot pass inside this run** (§7). The orchestrator decides whether STEP-010 passes with it handed to the user.
- **Unknown crossed-YES frequency.** How often Telonex YES books cross between delta batches is not measured. The block is correct by parity regardless. The smoke script may count `stale_book` quote events as a side number.
- **Run time.** A wake per feed tick and kill tick adds cycles. That is small next to the book-row wakes STEP-009 already added.

## 9. Verification commands

Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md), and do not set `PYTEST_N` on the VPS.

```bash
# targeted: the step's files plus neighbours
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py tests/test_adapter_contract.py \
  tests/test_backtest.py tests/test_core_trace.py tests/test_live_archives.py tests/test_feed_schedules.py \
  tests/test_lol_backtest.py tests/test_backtest_validation.py tests/test_since_match.py \
  tests/test_backtest_postprocess.py

# the new and changed tests by name (expect all selected to pass)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py tests/test_backtest.py tests/test_live_archives.py \
  -k "wakes_the_quoter or inside_the_debounce or bad_yes_book or crossed_yes or cycle_places_after or slow_draw or venue_release_offsets or archives_only or records_model_name or survives_msgspec or fill_during"

# full suite without the golden replay: expect 0 failed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck: expect 0 errors
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/live_latency.py src/backtest/strategy.py \
  src/backtest/two_sided_core.py src/backtest/run.py src/backtest/seed0_replay.py src/backtest/postprocess.py \
  src/shared/constants/strategy.py tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py \
  tests/test_backtest.py tests/test_feed_schedules.py tests/test_live_archives.py tests/test_adapter_contract.py \
  tests/test_core_trace.py

# no new comments: expect no output
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'

# removed names are gone: expect no output
rg -n "ORDER_INSERT_LATENCY_MS|ORDER_CANCEL_LATENCY_MS|CANCEL_LATENCY_FLOOR_MS|order_latency_ns|cancel_latency_ns|_draw_place_ns|_draw_cancel_ns|buy-settle|buy_settle_ms" src tests scripts

# real-engine crash check (optional, ~4 min): golden diffs are expected until STEP-014,
# but every failure must be a golden assertion, not a traceback from src/backtest
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_follow300_replay.py --tb=line -n0
```

The `rg` for removed names must not match the kept `QuoterCycle.cancel_latency_ns` field (`strategy.py:204`). If it does, narrow the pattern to `self._config.order_latency_ns|cancel_latency_ns=|order_latency_ns=`, and confirm the only hits are the `QuoterCycle` field and its uses at `:637` and `:720-725`.

Not run in this step: a full backtest, training, a data rebuild, a live start. The smoke map in §6 is the only replay beyond the optional golden check.

## 10. Commit

Paths, added by name and never with `-A`:

- `src/backtest/live_latency.py`
- `src/backtest/strategy.py`
- `src/backtest/two_sided_core.py`
- `src/backtest/run.py`
- `src/backtest/seed0_replay.py`
- `src/backtest/postprocess.py`
- `src/shared/constants/strategy.py`
- `tests/test_backtest_maker.py`
- `tests/test_backtest_two_sided_core.py`
- `tests/test_backtest.py`
- `tests/test_feed_schedules.py`
- `tests/test_live_archives.py`
- `tests/test_adapter_contract.py`
- `tests/test_core_trace.py`

Rules:

- Message: `fix: STEP-010 - wake follow300 on signal and kill ticks, block a crossed YES book, and draw its latency like two-sided`.
- Do not push. Do not set `passes`.
- `AGENTS.md` and 10 files under `docs/` have someone else's uncommitted edits (`git status` at planning time). They stay out of the commit.
- Add a `progress.txt` entry with:
  - the commit hash;
  - the smoke numbers from §6;
  - the archive-seed ceiling from §2.7;
  - the worktree hand-off from §7;
  - the golden-check wall time, if run.
