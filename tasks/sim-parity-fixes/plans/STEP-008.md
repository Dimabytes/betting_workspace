# STEP-008 plan: the core hears a book fill at its WS print (B3)

Base: `esports-trader` `main` at `0d9b9775` (no uncommitted changes under `src/` or `tests/`). The reference patch is `orchestrate/work/plan-008/step008.patch`: 3 files, +244/−7. `git apply --check` passes against that HEAD. The implementer can apply it as it is, or write the same code by hand from §3–§4.

This plan continues an earlier planner's draft (`orchestrate/work/plan-008-devin/`). That draft's patch has a crash bug that the real engine hits (§2.4, §5.2). The patch here fixes it and adds the test that catches it.

## 0. Result in one paragraph

`DotaMakerStrategy.on_order_filled` looks up the fill's trade time in `config.late_prints`, which STEP-007 filled in. If the trade printed late, the core and telemetry are not told yet. A `FILL_NOTICE:<seq>` alert is set at the print time, and its callback runs the old fill body (renamed `_hear_fill`) with `now_ns` = the print. The Nautilus book still matches at the trade time, and the fill row keeps that time.

While an order has a fill the core has not heard yet:
- a `CANCEL_SEND` for that order is re-armed to just after the order's latest pending print;
- `_order_already_gone` reports the order as still there, so a core cancel can't retire it as `missing_live` and free the rung early.

Fills that print on time take the old path unchanged. A seed-0 replay of a map with no late prints is byte-identical to HEAD (§5.2).

## 1. What happens today (evidence)

### 1.1 Fill path in the adapter

- `src/backtest/strategy.py:373-424` `on_order_filled`. It uses `now_ns = int(event.ts_event)` (`:390`) for three things:
  - the core `Fill.now_ns`, so the core's `last_buy_ns` and rung `done` happen at the trade time;
  - `fill_id = f"{client_order_id}:{now_ns}:{fill_qty}"` (`:395`);
  - the telemetry row `ts_ns` (`:422`).
- After the core step, the same handler:
  - arms the debounce wake (`:405`);
  - credits `LiveOrder.filled_qty` and detaches a full order (`_credit_venue_fill`, `:1258-1267`);
  - updates the episode notional;
  - cancels leftover BUYs on a flat wind-down (`:411-412`);
  - writes the `FillRecord`.
- `src/backtest/two_sided_core.py` does not override `on_order_filled`, so one change covers both products.

### 1.2 How the fill time reaches us

- The framework turns each rewritten onchain row into a `TradeTick` with `ts_event = timestamp_us*1000 + occurrence`. The occurrence counter numbers ticks that share a µs and is capped at 999 (`prediction-market-backtesting/crates/core/src/telonex.rs:838-842`, pinned `e2cfebb`). `ts_init = ts_event` (`:871`).
- Nautilus stamps the fill at the matching engine clock, `OrderFilled.ts_event = ts_now` (`.venv/.../nautilus_trader/backtest/engine.pyx:8274-8293`). For a trade-tick fill that clock is the tick's time.
- So `fill.ts_event - fill.ts_event % 1000` recovers the row's `timestamp_us * 1000`. Real data needs this floor. On map 9015175653, fills carry offsets of 0–14 ns, and 8 of the 11 late fills have a non-zero offset (§5.2).
- Fills driven by book deltas carry the book row's time. Tape v3 puts a late trade exactly on a book row's `timestamp_us`. So a book fill on that row maps to the same key and waits for the same print. That is the same sweep, so it is the intended result.

### 1.3 Late prints (STEP-007)

- `src/backtest/onchain_retime.py:70-76` `LatePrints(trade_ns, print_ns)`, index-aligned and per token.
- `read_late_prints` (`:223-238`) keeps only rows with `print_us > trade_us`, and the latest print per trade µs. A print is therefore always at least 1 µs after its trade, which is more than any occurrence offset (at most 999 ns). So a late fill's print is always in the future when the fill arrives.
- The config field is required: `src/backtest/strategy.py:186`. It is loaded per match in `src/backtest/run.py:664-666` (`load_late_prints`, `telonex_local.py:359-366`) and passed at `run.py:578`. Nothing reads it yet.

### 1.4 Where the rung frees before the core heard the fill

The core never sees an order whose venue fill it hasn't heard. Today, though, cancelling such an order can free its rung through three paths:

1. **Core cancel on a closed order.** `_execute_plan` (`:689-695`) sees `_order_already_gone(live)` True, because Nautilus has already set `is_closed` (`:1224-1227`). It calls `_retire_gone_cancel` (`:1229-1232`), which sends `OrderRejected(missing_live)`, and the core drops the order and frees the rung.
2. **Cancel send on a closed order.** `_on_cancel_send` (`:594-604`) sees `_order_already_gone`, calls `_ack_gone_cancel` (`:1234-1242`), then `_ack_venue_cancel` → `CancelAck`. `apply_cancel_ack` frees the rung (`src/strategy/lifecycle.py:349-354`).
3. **Cancel send on a partially filled order.** `cancel_order` reaches Nautilus at once: venue cancel latency is `0.0` (`run.py:325`). The resulting `on_order_canceled` path sends `CancelAck`, and the rung is free before the print.

A `Fill` that lands after the order is dropped is still credited from the order record (`lifecycle.py:714-724`, `_owner_for_fill`). The damage is that the rung was already re-quoted.

The venue cancel latency is 0, so a `cancel_order` sent before a book fill reaches the venue in the same instant. The only gap between a book fill and its print is the adapter's own 60 ms cancel hold (`ORDER_CANCEL_LATENCY_MS`, `run.py:328`). Deferring `CANCEL_SEND` therefore covers path 3.

### 1.5 What live does

- Live hears a fill from the user WS. `MatchWorker.note_core_fill` stamps the core `Fill` with `now_ns = core_now_ns()` at receive time (`src/trader/match_worker.py:1012-1027`), not with the venue match time.
- The replica docstring says `user_trade.timestamp` equals the WS print, p5–p95 within 2 ms (`investigations/2026-10-08-liveb-postmortem/sim/replica/replica_repo.py:401-406`).
- `port.md:39` measures the replica arm `fn` against `fb`: 1128±30 shares against 1164±57, PnL −6.59±5.16. The exact replay `qn1` stays exact.

### 1.6 The replica (`FillNoticeMixin`, `replica_repo.py:401-461`)

- It builds `{trade_us: max print_us}` per token from the tape. That is what `LatePrints` now carries.
- `on_order_filled` looks up `ts_event // 1000`. If the fill is late, it parks the event under `FILL_NOTICE:<seq>`, records the order's max pending print, and sets an alert at the print.
- `_on_fill_notice` pops the pending print with `.get(..., 0) <= ts` / `.pop(..., None)`, then calls the base `on_order_filled(event)`.
- Because it calls the base with the original event, its core `Fill.now_ns` and fill row are both the trade time. Live stamps receive time (§1.5), so this port gives the core the print time instead (§2.2).
- `_on_cancel_send` re-arms at `notice_ns + 1` while a notice is pending.
- `_order_already_gone` is not touched in the replica. In the product, the core-cancel path (§1.4, path 1) still frees the rung, so the port adds that guard.

## 2. Design decisions

1. **Defer in the adapter, keep the venue on its own clock.** Nautilus, its portfolio, and `LiveOrder.order.is_closed` still see the fill at the trade time. Only the core step and the adapter's own bookkeeping move to the print (`_credit_venue_fill`, episode notional, fill row, wind-down cancel). Nautilus has no hook to delay its own fill event, and the replica measured this split.
2. **Core time is the print.** `Fill.now_ns = print_ns`, so `last_buy_ns`, rung `done`, and the next debounce wake start at the print, the way live's receive stamp does (§1.5). This differs from the replica (§1.6). The test pins `inventory[0].last_buy_ns == PRINT_NS`.
3. **Fill row and `fill_id` keep the venue time.**
   - `fill_id` must stay unique per tick. Real data has two equal-size fills of one order on one print: two 10-share BUYs at `…773000000` and `…773000001` on 9015175653. With the print in `fill_id`, the core would treat the second as already seen and drop 10 shares.
   - The row's `ts_ns` is the trade time. That is when Nautilus moved the cash, and markouts (`postprocess.py:150`) and the wallet path sort on it (`postprocess.py:266`, `wallet_path.py:256`, `compare_backtests.py:256`, `build_reserve_events` via `wallet_path.py:351`).
   - The row's state fields are read when the core hears the fill: `position_after`, `level_index`, `reserved_buy_notional`, `gate_reason_at_fill`. That is what the core knew at that moment.
4. **One pending print per order, popped safely.** `_unheard_fill_ns[client_order_id]` holds the latest pending print for the order. A notice drops the entry only when its time has reached that print. It must use `.get(id, 0)` and `.pop(id, None)`: two notices of one order can share a print (both orders above), and the second would otherwise raise `KeyError`. The earlier draft used `[...]`/`del` and crashes the real replay of 9015175653 (§5.2).
5. **Defer the cancel send, as the replica does (design A).** `_on_cancel_send` re-arms the same alert name at `print_ns + seq` while the order has an unheard fill. `seq` (`_cancel_send_seq`) keeps the re-arm strictly after the notice at `print_ns`, and keeps a retry from colliding with another order's alert.
   - Re-arming a fired alert's name inside its callback is safe in Nautilus: `TestClock.set_time_alert_ns` refuses only live names (`component.pyx:651`). `_on_cancel_release` already does it (`strategy.py:551-555`), and the real replay of 9007208887 re-armed 3 sends.
   - **Known ceiling:** a partly filled order keeps resting at the venue until the print (up to 500 ms, `WINDOW_US`), so it can take a venue fill live's earlier HTTP cancel would have prevented. The upgrade path is to send the venue cancel on time and defer only the core-side ack (`_ack_venue_cancel` / `_retire_gone_cancel`). Not done here: the step text says the cancel waits, and the replica that `port.md` measured did exactly this. 3 deferred sends on 9007208887 changed no fill (§5.2).
6. **`_order_already_gone` returns False for an order with an unheard fill.** This sends a core cancel down the normal `_request_cancel` path, where the deferred send catches it. Once the notice is heard:
   - a full fill detaches the live order and completes the core order, so the late send finds no live order and returns;
   - a partial fill gets a real venue cancel and `CancelAck`.
7. **No change to `two_sided_core.py`.** It inherits everything. Its `_drive_cancel_ack` → `BUY_SETTLE` → `next(order …)` (`two_sided_core.py:211-231`) is reached only after the deferred send, when no fill of that order is still unheard.
8. **Leave out:**
   - a `USER_WS_DELAY_MS` on top of the print (STEP-009 adds it for every fill);
   - `VenueConfirmMixin`, the `BUY_CONFIRM_MS` settle (STEP-009);
   - any change to `on_order_canceled`, which can't arrive between a book fill and its print while venue cancel latency is 0 (§1.4).

## 3. Changes — `src/backtest/strategy.py` (+46/−7)

1. Imports: `from functools import partial` after `from decimal import Decimal` (`:10`), and `from shared.utils.telonex_book import NS_PER_US` after the `match_time` import (`:78`). `onchain_retime.py:39` imports `NS_PER_US` from the same place.
2. A module constant next to `PLACEMENT_JOIN` (`:120`): `FILL_NOTICE_PREFIX = "FILL_NOTICE:"`.
3. `__init__`, after `self._cancel_send_seq` (`:257`):
   ```python
   self._print_ns_by_trade_ns: tuple[dict[int, int], ...] = tuple(
       dict(zip(prints.trade_ns, prints.print_ns, strict=True))
       for prints in config.late_prints
   )
   self._unheard_fill_ns: dict[str, int] = {}
   self._fill_notice_seq: int = 0
   ```
   `read_late_prints` already deduplicates by trade time, so the dict loses nothing.
4. Replace the head of `on_order_filled` (`:373-374`):
   ```python
   def on_order_filled(self, event: VenueFilled) -> None:
       """Live hears a fill from `user_trade`, stamped at its WS print, not at the book row."""
       venue_ns = int(event.ts_event)
       trade_ns = venue_ns - venue_ns % NS_PER_US
       token_index = self._token_index_of(event.instrument_id)
       print_ns = self._print_ns_by_trade_ns[token_index].get(trade_ns)
       if print_ns is None:
           self._hear_fill(event, now_ns=venue_ns)
           return
       client_order_id = str(event.client_order_id)
       unheard_ns = self._unheard_fill_ns.get(client_order_id, print_ns)
       self._unheard_fill_ns[client_order_id] = max(unheard_ns, print_ns)
       self._fill_notice_seq += 1
       self.clock.set_time_alert_ns(
           f"{FILL_NOTICE_PREFIX}{self._fill_notice_seq}",
           print_ns,
           callback=partial(self._on_fill_notice, event),
       )

   def _on_fill_notice(self, event: VenueFilled, notice: TimeEvent) -> None:
       now_ns = int(notice.ts_event)
       client_order_id = str(event.client_order_id)
       if self._unheard_fill_ns.get(client_order_id, 0) <= now_ns:
           self._unheard_fill_ns.pop(client_order_id, None)
       self._hear_fill(event, now_ns=now_ns)

   def _hear_fill(self, event: VenueFilled, *, now_ns: int) -> None:
       venue_ns = int(event.ts_event)
       # ...the old body from `token_index = …` (:375) on, with three edits:
   ```
   - Delete `now_ns = int(event.ts_event)` (`:390`). `now_ns` is now the parameter.
   - `fill_id=f"{client_order_id}:{venue_ns}:{fill_qty}"` (`:395`).
   - `ts_ns=venue_ns` in `_record_fill_row` (`:422`).
   - Delete the two-line `# Fill dirties the schedule…` comment (`:403-404`). It narrates the `_arm_wake` call below it, and the review rule asks to drop comments in a function being reshaped.

   The old docstring ("Credit the core, then telemetry…") is replaced by the one line above, which records the live fact the code can't show. The two new methods get no docstrings (comments-review rule, see `nocomments-007.md`). A `partial` as the callback means the parked event needs no dict keyed by alert name.
5. `_on_cancel_send` (`:594`), right after `client_order_id = …`:
   ```python
   print_ns = self._unheard_fill_ns.get(client_order_id)
   if print_ns is not None:
       self._cancel_send_seq += 1
       self.clock.set_time_alert_ns(
           event.name, print_ns + self._cancel_send_seq, callback=self._on_cancel_send
       )
       return
   ```
6. `_order_already_gone` (`:1224-1227`), between the `live is None` check and the `is_closed` read:
   ```python
   if str(live.order.client_order_id) in self._unheard_fill_ns:
       return False
   ```

Not changed: `_credit_venue_fill`, `_ack_venue_cancel`, `_drive_cancel_ack`, `on_order_canceled`, `two_sided_core.py`, `run.py`, `onchain_retime.py`.

## 4. Tests (+198, 8 new items; HEAD's existing tests unchanged)

### 4.1 `tests/test_backtest_maker.py` (+169/−1)

- `build_maker_config` gets a `late_prints: tuple[LatePrints, LatePrints] = (NO_LATE_PRINTS, NO_LATE_PRINTS)` parameter, passed through at `:222` instead of the hard-coded pair.
- New module values: `TRADE_NS = NS_PER_SECOND`, `PRINT_NS = TRADE_NS + 100 ms`, `LATE_PRINT = LatePrints(trade_ns=(TRADE_NS,), print_ns=(PRINT_NS,))`.
- New helpers:
  - `fire_due_alerts(strategy, *, prefix, until_ns)` pops and runs, in time order, every recorded `_alert_callbacks` entry with that prefix that is due. It is generic. The existing `fire_cancel_release` (`:411-450`) and the two-sided `_fire_settle` each fire one named alert, so neither covers this.
  - `build_late_print_harness(monkeypatch, *, late_prints)`: harness, first `_evaluate(0)`, `accept_all`, `take_records()`. Returns the top BUY.

New tests:
1. `test_book_fill_before_its_print_reaches_the_core_at_the_print`. A full fill at `TRADE_NS + 2`:
   - Before the print: the order is still in `_live`, position is 0, and no fill row exists.
   - An `_evaluate` 30 ms later places nothing, so `submitted` stays at `BUY_LEVEL_COUNT`.
   - At `PRINT_NS`: rung 0 is `done`, `inventory[0].last_buy_ns == PRINT_NS`, and the fill row `ts_ns == TRADE_NS + 2`.
   - `_evaluate(PRINT_NS)` places exactly one new 0.48 BUY, and its `submitted` quote event is at `PRINT_NS`.

   This is the step's "new bid no earlier than the print".
2. `test_fill_off_the_late_prints_reaches_the_core_at_once[next_microsecond|other_token]`: a fill at `TRADE_NS + 1000`, or on the other token's late print, is heard at once and arms no `FILL_NOTICE`. This is "fill printed at the same moment → as before".
3. `test_cancel_of_an_unheard_fill_waits_for_the_print[full|partial]`. A core cancel plan at `TRADE_NS + 10 ms` and a cancel release 60 ms later:
   - Nothing is cancelled before the print, the rung keeps its `live_id`, and position is 0.
   - After the notice and the deferred send: a full fill sends no venue cancel and the core order is gone (completed by the fill); a partial fill gets one venue cancel and the core order is still there awaiting its ack.

   This is "cancel of a filled order before the print does not release the rung before the print".
4. `test_a_cancel_waits_for_the_last_unheard_fill_of_its_order`: two partial fills with prints at `PRINT_NS` and `PRINT_NS + 50 ms`. The cancel is released between the two prints and goes out only after the second.
5. `test_two_fills_printed_together_reach_the_core_once_each`: two 20-share fills of one order at `TRADE_NS` and `TRADE_NS + 1` (two ticks in one µs). Both are heard at `PRINT_NS`, giving 40 shares and two rows. This guards the `KeyError` and the venue-time `fill_id`.

### 4.2 `tests/test_backtest_two_sided_core.py` (+29)

- `test_two_sided_core_hears_a_book_fill_at_its_print`: a 12-share YES fill 40 ms before its print leaves `inventory[0].qty == 0.0` until the `FILL_NOTICE`, then 12.0.
- The config is `msgspec.structs.replace(_two_sided_config((_tick(0),)), late_prints=(late, NO_LATE_PRINTS))`. `msgspec` is a direct dependency (`pyproject.toml:11`).
- New imports: `msgspec`, `NO_LATE_PRINTS`, `fill_order`, `fire_due_alerts` from `test_backtest_maker`, and `LatePrints` from `backtest.onchain_retime`.

No other caller changes: every config builder already passes `late_prints` (STEP-007).

## 5. Scratch evidence (`orchestrate/work/plan-008/`)

The scratch is `git archive` of HEAD `0d9b9775` (`HEAD.txt`) at `et/` with the patch, plus a `base/` worktree of HEAD. Both share the main `.venv`.

### 5.1 Unit tests and mutations

- **Targeted 8 files** (§7 list): 332 passed on the patched tree. HEAD has 324 (`impl-007.md`), so that is 8 new and none lost.
- **Full suite without the golden replay**, both trees, `PYTEST_N=8`: base has 3,265 passed and 13 failed; patched has 3,273 passed and the same 13 failed (`full_base.log`, `full_et.log`, identical failure sets). The 13 failures need `.env`: `test_trader_wallet_host.py` ×12 and `test_trader_unsettled_buy_recovery.py::test_user_stream_is_bound_on_the_host_run_path`. `impl-007.md` shows them passing on the real checkout, which has 3,278.
- **basedpyright** on the whole patched tree: 0 errors, 0 warnings, 0 notes, 11.9 s (`pyright_full.log`). ruff check and format are clean on the 3 files. No added `#` line.
- **Mutations** (`mutate.py`, `mutate.log`): 11 of 11 fail a test:
  - an exact-ns lookup;
  - no deferral;
  - the other token's prints;
  - the core hearing at the trade time;
  - the fill row at the notice time;
  - `fill_id` at the notice time;
  - the earliest pending print kept;
  - the pending print dropped on the first notice;
  - `[...]`/`del` instead of `get`/`pop`;
  - no cancel deferral;
  - no `_order_already_gone` guard.

  The draft's last-print test could not catch "dropped on the first notice", because its cancel was released before both prints. This plan's version releases it between them.

### 5.2 Real engine, seed-0 smoke maps

Run from each tree with `XDG_CACHE_HOME=work/plan-008/cache`, so the machine-wide Telonex caches were neither read nor evicted.

| map | late prints / token | fills | late fills = notices fired | deferred `CANCEL_SEND` | fills (ts, order, side, px, qty) vs base | cash_flow / engine_pnl vs base |
|---|---|---|---|---|---|---|
| 9007700576 | 0 / 0 | 13 | 0 | 0 | identity JSON byte-identical | same |
| 9015175653 | 48 / 48 | 178 | 11 | 0 | equal | same (−126.81 / −126.79) |
| 9007208887 | 116 / 116 | 62 | 18 | 3 | equal | same (−8.05 / −8.05); cancels 132 → 135 |

- Map 9007700576 has 0 raw `onchain_fills` rows, a capture gap. All its fills come from book deltas.
- On 9015175653 the late lags are 279 ms (one SELL order, 7 ticks at offsets 0–6 ns), 53 ms, and 255 ms (two 10-share BUYs, one order, offsets 0 and 1). The only row changes are on those two BUYs: `level_index` 1 → 0 and `reserved_buy_notional` +11.8, both read when the core hears the fill. 9 `place_cancel` rows differ only in `reserved_buy_notional`. Fill rows of order `-101` move later in the record list.
- On 9007208887 the core decides differently while it doesn't yet know about the fills, but no fill changes.
- The draft's `[...]`/`del` crashes the 9015175653 replay with `KeyError: 'O-20260925-105817-001-9015175653-101'` (`identity_devin_9015175653.log:2198`).
- Run times: cold cache 21.5 s, 29.6 s, 66.0 s; warm cache 7–12 s.

Tools: `fill_probe.py` (per fill: offset, late flag, lag; notice and deferred-send counts), `identity_dump.py`, `replay_probe.py`. They read `_print_ns_by_trade_ns`, `_unheard_fill_ns` and `_on_fill_notice(self, event, notice)`, so keep those names or adapt the probe.

## 6. Behavior changes and risks

- **Sim output moves on maps with late prints.** Fill times, prices, and sizes stayed equal on both maps that had late prints. Core decisions after a late fill shift (more cancels on 9007208887), and so do the fill rows' state fields. The follow300 golden smoke and `LIVE` drift. `tests/test_follow300_replay.py` stays ignored until STEP-014, which rewrites the goldens. Do not re-pin them here.
- **Fill rows are written in the order the core hears them,** so `records.fills` is no longer sorted by `ts_ns`. Every consumer found sorts first (§2.3). The extraction identity keeps record order, which only matters for the goldens (STEP-014).
- **Deferred cancel ceiling** (§2.5). The venue order rests until the print and can take extra venue fills. Upgrade path: defer only the core ack.
- **Nautilus cash moves at the venue time.** `_budget()` reads `account.balance_free` (`:1102-1119`), so a SELL's proceeds show as free cash before the core hears the SELL. A BUY's notional is locked at placement, so a BUY fill changes nothing there. Small, and inside 500 ms.
- **No notice is lost at the end.** Orders are cancelled at game end with the 60 ms hold. The book window ends at game end + 1 min (`context.py:25-32`) and the engine clock runs to `clock_end` ≥ that (`context.py:46-48`, `run.py:677-678`). An unheard fill keeps its order in `_live`, so `_mark_trading_done` (`:648-657`) can't stop the strategy first. No force stop exists (`run.py:681` only resets it).
- **STEP-009 hand-off:**
  - `USER_WS_DELAY_MS` should go on top of `now_ns` in both the on-time and the deferred paths.
  - If STEP-009 gives the venue cancel a non-zero latency, a venue `OrderCanceled` can land between a book fill and its print. `_ack_venue_cancel` then needs the same `_unheard_fill_ns` deferral, or the `BUY_CONFIRM` settle must key on the venue fill time (replica `VenueConfirmMixin`, `replica_repo.py:464-480`).
  - `_unheard_fill_ns` holds print times, not venue fill times.

## 7. Verification

Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md), and do not set `PYTEST_N` on the VPS.

```bash
# targeted (HEAD 324 passed per impl-007; expect 332 passed)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_onchain_retime.py tests/test_backtest_validation.py tests/test_lol_backtest.py \
  tests/test_backtest.py tests/test_feed_schedules.py tests/test_live_archives.py \
  tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py

# the new tests alone (expect 8 passed)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_maker.py tests/test_backtest_two_sided_core.py -k "print or unheard"

# full suite without the golden replay ("Tests pass" for STEP-001..013); HEAD 3278 passed per impl-007, expect 3286 passed, 0 failed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors, 0 warnings, 0 notes)
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/strategy.py tests/test_backtest_maker.py \
  tests/test_backtest_two_sided_core.py

# no new comments: expect no output
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'

# optional mutation check against a scratch copy (~50 s): expect 11 KILLED, 0 SURVIVED
#   (edit ET in the script if the copy lives elsewhere)
#   python ../betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-008/mutate.py

# optional real-engine check (~10-70 s per map on a cold scratch cache; keeps the shared cache untouched)
mkdir -p /tmp/step008-cache
for m in 9015175653 9007208887; do
  XDG_CACHE_HOME=/tmp/step008-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:../prediction-market-backtesting \
    uv run --group backtest python ../betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-008/fill_probe.py \
    dota $m $m /tmp/step008_fills_$m.json | tail -1
done
# expect 9015175653: late_prints_per_token [48, 48], fills 178, late_fills 11, notices 11, deferred_cancel_sends 0
# expect 9007208887: late_prints_per_token [116, 116], fills 62, late_fills 18, notices 18, deferred_cancel_sends 3
```

Not run in this step: `tests/test_follow300_replay.py` (STEP-014), any full backtest, training, data rebuild, or live start.

## 8. Commit

Paths:
- `src/backtest/strategy.py`
- `tests/test_backtest_maker.py`
- `tests/test_backtest_two_sided_core.py`

Add them by name, not with `-A`. At planning time, `AGENTS.md` and 10 files under `docs/` had uncommitted changes made by someone else. They stay out of this commit.

Message: `fix: STEP-008 - hear a book fill at its WS print and hold its cancel until then`.

Do not push. Do not set `passes`.
