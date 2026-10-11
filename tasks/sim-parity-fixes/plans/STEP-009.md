# STEP-009 plan: the poly-maker quote cycle runs in the shared backtest adapter (B4, part 1)

All line numbers are `esports-trader` at `ff2802d5` (HEAD at planning time) unless a path says `esports-trader-cadence`. This plan contains no code; it names files, functions, lines, the approach, edge cases and tests. The implementer writes and runs everything.

## 0. Result

After this step both products share one quote loop in `DotaMakerStrategy`. The loop works like poly-maker `Engine._quoter`: an event wakes it, it sleeps the 100 ms debounce, then it runs one cycle, and HTTP keeps it busy. A wake while busy gives a fresh debounce. `_arm_wake` only records the core deadline.

Three timing changes reach both products:
- The strategy sees a book row 10 ms after the venue.
- The core hears a fill 15 ms after its print.
- A BUY cancel is `CancelUnsettled` at the HTTP response, then `BuySettled`. With no fill it settles on the WS proof. With a fill it settles at CONFIRMED (7.25 s after the fill is heard).

Two-sided draws its HTTP latencies from the live table (45 ms insert plus the drawn remainder, 10 ms recompute). Follow300 keeps 175 ms insert and 60 ms cancel with no draw, and a 6 ms recompute. `TwoSidedCoreStrategy` shrinks to the empty signal, the pair gate, no cutoff, the bad-YES-book skip, and its two latency draws.

## 1. Evidence

### 1.1 Live loop and cancel path (what the sim must copy)
- `poly-maker/src/polymaker/engine.py:318-335` `_quoter`: `wait_for(ev, timeout=_next_wake_s)`. If the event is set it sleeps the debounce, then `ev.clear()`, then `_recompute`. A wake during the sleep is absorbed. A wake during `_recompute` leaves `ev` set, so the next pass sleeps a fresh debounce. A timeout recomputes at once.
- `config/trading.toml:7-8`: `debounce_ms = 100`, `quoter_tick_s = 2.0`. Both products read them (`run.py:453-454` puts them on the run policy).
- `src/trader/engine_seams.py:1421` `install_core_quoter_wake`: the idle timeout is capped at the core's next wake. A deadline already in the past fires once.
- `src/trader/session_core.py:540-555` `note_cancel`: at the HTTP response it enqueues `CancelUnsettled` for a BUY and `CancelAck` for a SELL.
- `session_core.py:557-587`: `sync_inputs` adds `BuySettled` for every BUY whose venue row is settled. That does not depend on the cancel response having arrived.
- `session_core.py:967-983` and `:713-748`: each cycle runs `drain_apply` (queued events, including `CancelUnsettled`), then `finish_cycle`: `sync_inputs` (including `BuySettled`), drain, forced `Wake`. It then drops places whose order is no longer `pending` (`:737-744`) and stashes one plan.
- `src/strategy/lifecycle.py:349-373`:
  - `apply_cancel_ack` frees the rung.
  - `apply_cancel_unsettled` sets a BUY to `gone` and ignores a missing order.
  - `apply_buy_settled` accepts `live`/`canceling`/`unknown`/`gone` and needs `filled_qty >= matched_qty - HALF_SHARE_TICK`.
  - So the core is correct whichever of `CancelUnsettled` and `BuySettled` arrives first.
- `src/strategy/scheduling.py:34,38,42,137`: `NS_PER_MS`, `debounce_ns`, `fallback_ns`, `next_wake_ns`.

### 1.2 The sim today (`src/backtest/strategy.py`, 1389 lines)
- Book: `on_order_book_deltas` `:340-355` applies each row at its venue `ts_event`, drives `BookUpdate`, then calls `_arm_wake`.
- Core timer: `_arm_wake` `:526-537` sets a `CORE_WAKE` alert, deduplicated through `_armed_wake_ns`/`_wake_generation` (`:250-251`). `_on_core_wake` `:501-508` calls `_evaluate` (`:662-669`, forced `Wake`).
- Places: `_execute_plan` `:700-720` runs cancels, then places. `_submit_place` `:722-798` submits at once and sets `RELEASE:` at `timestamp + order_latency_ns` (`:774-777`). `_schedule_cancel` `:844-859` sets `CANCEL:` at `timestamp + cancel_latency_ns` (`:857`).
- Cancel ack: `on_order_canceled` `:462-472` → `_ack_venue_cancel` `:1269-1278` (sync, `_drive_cancel_ack`, detach, execute plan, record block). Base `_drive_cancel_ack` `:1280-1281` drives `CancelAck`, which frees a BUY rung at the venue cancel time. Port note: `port.md` (`investigations/2026-10-10-sim-followup/reports/port.md:30`).
- Fills (STEP-008): `on_order_filled` `:382-400` hears an on-time fill at once (`:389-391`). A late print goes through `FILL_NOTICE:` → `_on_fill_notice` `:402-409` → `_hear_fill` `:411-460` (`Fill.now_ns` = heard time, `:429-439`).
- `progress.txt:115` (STEP-008 learnings) says: "STEP-009: add `USER_WS_DELAY_MS` on top of `now_ns` in both the on-time and the deferred path".
- Signal and kill gate: `_on_signal_alert` `:510-515` and `_on_kill_gate_alert` `:517-524` only call `_arm_wake`. STEP-010 makes them wake the quoter.

### 1.3 Two-sided today (`src/backtest/two_sided_core.py`, 252 lines)
- `BUY_SETTLE_MS = 140`, `BUY_SETTLE_PREFIX` `:38-41`; `buy_settle_ns` on `TwoSidedKernelConfig` `:52`, `TwoSidedSettings` `:63`, `build_two_sided_kernel` `:87`.
- `_drive_cancel_ack` `:211-218` drives `CancelUnsettled` and arms `BUY_SETTLE:` at a fixed 140 ms. `_on_buy_settled` `:220-231` drives `BuySettled` and calls `_arm_wake`.
- `_evaluate` `:188-196` skips a one-sided or crossed YES book and re-arms `now + fallback` (`:194`).

### 1.4 What to lift (uncommitted diff in `esports-trader-cadence`, base `6787bf3f`)
`esports-trader-cadence/src/backtest/two_sided_core.py`:
- Constants and measurement notes: `:58-93`, including `LATENCY_QUANTILES`, `PLACE_LATENCY_MS`, `RESPONSE_MS`, `QUOTER_RECOMPUTE_MS`, `USER_WS_DELAY_MS`, `CANCEL_LATENCY_*`, `BUY_CONFIRM_MS`, `VENUE_INSERT_MS`, `BOOK_VIEW_DELAY_MS` and the alert prefixes.
- Dataclasses `ReceivedBook`/`CancelResponse` `:121-133`; `draw_latency_ns` `:161-169`; RNG seeding `:185`.
- Book queue `:209-251`; `_on_signal_alert` wake `:269`; fill/accept wakes `:317-326`.
- Quoter `_arm_wake`/`_wake_quoter`/`_arm_quoter`/`_on_quoter`/`_end_busy`/`_run_cycle` `:328-388`; `_settle_arrived`/`_apply_cancel_responses` `:390-415`.
- `_cancel_latency_ns` `:417-432`; `_submit_place`/`_on_delayed_place` `:434-465`; `_ack_venue_cancel`/`_on_quoter_wake` `:467-489`; `_evaluate` skip `:506-513`.

Elsewhere in the cadence worktree:
- `run.py`: `build_order_latency(*, insert_latency_ms)` `:316`; `insert_latency_ms` choice `:648`; `latency_seed` `:1626`; stamp `:1650-1659`. `--buy-settle-ms` is gone; `rg buy_settle src` finds nothing.
- `tests/test_backtest_two_sided_core.py`: helpers `:114-170`; the 7 named tests `:252-374` and `:518-544`; drivers `:441-516`.

### 1.5 Size
`strategy.py` is already 1389 lines. The step-review rubric pushes back on files that sprawl past 1k lines. The lift adds about 200 lines to the adapter, so the pure measured latencies move to a small module (§2, D2).

## 2. Design decisions

- **D1. The base class owns the quoter.**
  - Lift the `idle`/`sleeping`/`busy` machine from cadence `:328-373` into `DotaMakerStrategy` unchanged in behavior. A wake while sleeping only sets the dirty flag. A wake while busy gives a fresh debounce when the busy period ends.
  - When idle, the next alert is at `min(now + fallback, future core deadline)`. A past, not-yet-fired deadline fires once at `now`.
  - Stale generations are ignored. `_finished` stops re-arming.
  - `_arm_wake` stores `_core_deadline_ns` and does nothing else. Delete `CORE_WAKE`, `_on_core_wake`, `_wake_generation` and `_armed_wake_ns`.
- **D2. Measured latencies go in a new module, `src/backtest/live_latency.py`.**
  - It holds `LATENCY_QUANTILES`, `PLACE_LATENCY_MS`, `VENUE_INSERT_MS = PLACE_LATENCY_MS[0]`, `RESPONSE_MS = 32`, `USER_WS_DELAY_MS = 15`, `BUY_CONFIRM_MS = 7_250`, `BOOK_VIEW_DELAY_MS = 10` and `draw_latency_ns(*, table_ms, rng)`.
  - Keep cadence's measurement comments: they state external facts, which earlier comment reviews kept.
  - `CANCEL_LATENCY_DISCOUNT_MS`/`CANCEL_LATENCY_FLOOR_MS` stay in `two_sided_core.py`, because only two-sided draws a cancel in this step.
  - `run.py` and STEP-010 (`postprocess.py`) import from this module without pulling in the adapter.
- **D3. `QUOTER_RECOMPUTE_MS` is a `ClassVar[int]`:** 6 on `DotaMakerStrategy`, 10 on `TwoSidedCoreStrategy` (`port.md:54`).
- **D4. Latency hooks.**
  - Base `_draw_place_ns()` returns `config.order_latency_ns`; base `_draw_cancel_ns()` returns `config.cancel_latency_ns`.
  - Two-sided overrides both. The place draw comes from `PLACE_LATENCY_MS`. The cancel draw is `max(draw − 15 ms, 20 ms)`.
  - Two-sided uses its own `random.Random(f"{latency_seed}:{match_id}")`, with `latency_seed` from `--signal-cadence-seed`.
  - `_cancel_latency_ns()` stays the per-cycle method, as feature.json names it: one cancel draw per cycle, which adds `draw + RESPONSE_MS` to the cycle's busy time. Outside a cycle each call draws on its own.
  - STEP-010 moves follow300 onto the draws, and can then fold the hooks away.
- **D5. One mutable `QuoterCycle` (`start_ns`, `http_ns`, `cancel_latency_ns: int | None`, `places: list[PlaceOrder]`) is held in `self._cycle`.**
  - It replaces cadence's `_in_cycle`, its four `_cycle_*` fields, `_cancel_tail_ns` (always `RESPONSE_MS`) and the `_delayed_places` dict.
  - `ReceivedBook(received_ns, deltas)` and `CancelResponse(respond_ns, order_id, side)` are frozen dataclasses, as in cadence. `side` is read from the core order, so a SELL response becomes `CancelAck`.
- **D6. Places in a cycle are collected and sent after the cycle's last plan.** This deviates from cadence on purpose.
  - Live merges the plans from responses, settles and the forced `Wake`, drops places no longer `pending`, and sends cancels before places (`session_core.py:722-745`). Cadence timed places at the first place call, so a later plan's cancel in the same cycle could go out after a place.
  - In a cycle, `_submit_place` only appends to `cycle.places`.
  - At the end of `_run_cycle`, `_send_cycle_places` keeps places whose core order is still `pending`, draws one place latency, and sets `submit_ns = start_ns + http_ns + draw − config.order_latency_ns`. It adds `draw + RESPONSE_MS` to `http_ns`. It sends each place now if `submit_ns <= now`; otherwise it sets `PLACE_AT:{order_id}` with `functools.partial(self._on_delayed_place, place)`.
  - `_on_delayed_place` skips unless the core order is still `pending` (cadence `:457-465`).
  - Outside a cycle (tests calling `_evaluate`, recovery, a pause clock) `_submit_place` sends at once.
  - The old body becomes `_send_place`. Its `RELEASE:` alert stays at send time + `order_latency_ns`, so the venue insert lands at `start + http + draw`.
  - Follow300's draw equals `order_latency_ns`, so its places go out right after the cycle's cancel HTTP (60 + 32 ms), or at once when the cycle sent no cancel.
- **D7. The cancel ack mirrors live.**
  - `_ack_venue_cancel` detaches the live order. If the core order still exists, it appends `CancelResponse(now + RESPONSE_MS)`.
  - For a BUY it also stores `settle_ns = now + USER_WS_DELAY_MS`. When the core order has `filled_qty > 0`, settle is instead the later of that and `last heard fill + BUY_CONFIRM_MS`.
  - It sets `QUOTER_WAKE:{order_id}:{ns}` alerts at `respond_ns` and at `max(settle_ns, respond_ns)`; `_on_quoter_wake` calls `_wake_quoter`.
  - `_run_cycle` applies arrived responses (BUY → `CancelUnsettled`, SELL → `CancelAck`, `_sync_inputs` before each, then execute the plan). It then applies arrived settles (`BuySettled` with `matched_qty` = core `filled_qty`), then calls `_evaluate`.
  - This is live's order: drain first, then `sync_inputs`. Cadence applied settles first; the core result is the same (§1.1).
  - With no fill, settle (+15 ms) is earlier than the response (+32 ms), so both land in the response cycle and the rung frees there. That matches `port.md:52`.
  - Delete base `_drive_cancel_ack` and the two-sided `_drive_cancel_ack`/`_on_buy_settled`/`BUY_SETTLE_*`. Old `_record_block_if_idle` after the ack goes too, because the cycle's `_evaluate` records it.
  - `_ack_gone_cancel` (`:1259-1267`) keeps routing through `_ack_venue_cancel`. A fully filled order is no longer in the core, so that path is a detach only.
- **D8. Every fill is heard at `(print or venue tick) + USER_WS_DELAY_MS`** (`progress.txt:115`).
  - `on_order_filled` always goes through `_unheard_fill_ns` and a `FILL_NOTICE:` alert at that heard time. The immediate branch at `:389-391` is deleted.
  - `_hear_fill` records `_last_fill_ns[core_id] = now_ns` (the heard time) and calls `_wake_quoter(now_ns)`. That wake time is the same as cadence's `ts + 15 ms`.
  - The fill row, `fill_id` and `fair_at_fill` stay on the venue tick (STEP-008).
  - STEP-008's deferred cancel send now also covers the 15 ms after every fill. So a venue cancel never lands before its fill is heard, and `_last_fill_ns` is set before any ack of a filled BUY.
  - `BUY_CONFIRM_MS` was measured from MATCHED to CONFIRMED, plus one cycle. Both are user-WS messages, so the heard time is the right base.
- **D9. `on_order_accepted` keeps driving `OrderAccepted` at the venue time and adds `_wake_quoter(now + USER_WS_DELAY_MS)`** (cadence `:324-326`).
- **D10. Book view.**
  - `on_order_book_deltas` queues `ReceivedBook(ts_event + BOOK_VIEW_DELAY_MS)` per token. It sets one `BOOK_RECEIVED:{token}:{ns}` alert for the queue head, via `partial(self._on_book_received, token_index)` instead of parsing the alert name.
  - The callback applies every row due by then, in order, then re-arms for the new head.
  - `_apply_received_book` is the old `on_order_book_deltas` body at the receive time, plus `_wake_quoter(now_ns)`. Freshness (`_book_ts_ns`) is stamped at the receive time.
- **D11. Two-sided becomes thin.**
  - Delete what the base now has: the book queue, fill/accept wakes, `_arm_wake`, the quoter, `_submit_place`/`_on_delayed_place`, `_ack_venue_cancel` and the dataclasses.
  - `_on_signal_alert` (`:110-125`) adds `_wake_quoter(now_ns)` after `_merge_pairs`.
  - `_evaluate` keeps the skip but drops the `now + fallback` re-arm (`:194`); the quoter's idle timeout retries.
  - On a skip cycle, arrived responses and settles are still applied before `_evaluate`. Live skips the core entirely on such a cycle. Note this in the risks; do not add a branch for it.
- **D12. `run.py` changes.**
  - `build_order_latency(*, insert_latency_ms: float)`. `run_batch` passes `ORDER_INSERT_LATENCY_MS` for follow300 and `VENUE_INSERT_MS` for two-sided (cadence `:648`).
  - Delete `--buy-settle-ms`, as cadence did: its only consumer goes away, and a flag without one is dead code. STEP-010's deletion item becomes a no-op; tell STEP-010 in progress.txt.
  - Manifest: resume compares every expected key (`results.py:454-466`). Follow300 keeps 175/60 ms but its results change, so the shared manifest needs new keys. Otherwise `--resume` would mix old and new maps.
- **D13. Follow300 signal and kill-gate alerts stay deadline-only** (STEP-010). Their deadline is read when the quoter goes idle, which is the live `_next_wake_s` behavior. Changing that is STEP-010's job.

## 3. Changes by file

### 3.1 `src/backtest/live_latency.py` (new, about 40 lines)
D2 contents, lifted from cadence `two_sided_core.py:59-88` and `:161-169`. It needs a one-line module docstring stating that the numbers are live wallet measurements from 2026-10-08. `NS_PER_MS` comes from `strategy.scheduling` (cadence redefines it at `:58`; do not).

### 3.2 `src/backtest/strategy.py`
- **Imports and module constants (`:85`, `:93`, `:123-124`).**
  - From `strategy.scheduling` import `NS_PER_MS`, `debounce_ns` and `fallback_ns` alongside `next_wake_ns` (still used by `_on_signal_alert` `:515`).
  - From `strategy.types` add `BuySettled`, `CancelUnsettled` and `RestingOrder`.
  - From `backtest.live_latency` import `BOOK_VIEW_DELAY_MS`, `BUY_CONFIRM_MS`, `RESPONSE_MS` and `USER_WS_DELAY_MS`. Tests patch `backtest.strategy.RESPONSE_MS`, so it must be read through this module's global.
  - Add prefixes `BOOK_RECEIVED_PREFIX`, `QUOTER_PREFIX`, `QUOTER_WAKE_PREFIX` and `PLACE_AT_PREFIX` next to `FILL_NOTICE_PREFIX`.
  - Add `QuoterState = Literal["idle", "sleeping", "busy"]`.
  - `_REQUOTE_EVENTS` keeps `CancelAck`; a SELL response still requotes through `_execute_plan`.
- **Dataclasses after `ObservedClockTape` (`:162-167`):** `ReceivedBook`, `CancelResponse` and `QuoterCycle` per D5.
- **Class (`:212`):** `QUOTER_RECOMPUTE_MS: ClassVar[int] = 6`.
- **`__init__` (`:250-251`).** Replace `_wake_generation`/`_armed_wake_ns` with:
  - per-token `deque[ReceivedBook]` pair;
  - `_quoter_state`, `_quoter_dirty`, `_quoter_generation`;
  - `_core_deadline_ns` (start 0) and `_fired_deadline_ns` (start −1);
  - `_cycle: QuoterCycle | None`;
  - `_cancel_responses: list[CancelResponse]`;
  - `_settle_at_ns: dict[str, int]` and `_last_fill_ns: dict[str, int]`.
- **`on_order_book_deltas` (`:340-355`):** becomes the queue (D10). Add `_arm_book_received`, `_on_book_received(token_index, event)` and `_apply_received_book(deltas, *, now_ns)`. Alert times use `max(at, clock.timestamp_ns())`, as cadence does.
- **`on_order_accepted` (`:361-380`):** add the D9 wake after the `OrderAccepted` drive (`:373`).
- **`on_order_filled` (`:382-400`).** Compute the heard time per D8 and always register `_unheard_fill_ns` plus the notice. `_on_fill_notice` is unchanged.
- **`_hear_fill` (`:411-460`):** after `_arm_wake` (`:440`), add the `_last_fill_ns` write and `_wake_quoter(now_ns)`.
- **Delete `_on_core_wake` (`:501-508`).** Replace `_arm_wake` (`:526-537`) with the one-line deadline store.
- **Add the quoter** (lift cadence `:332-415` with D5-D7 changes):
  - `_wake_quoter(now_ns)`, `_arm_quoter(at_ns)`, `_on_quoter(event)`, `_end_busy(now_ns)`.
  - `_run_cycle(now_ns) -> int`: at or after `game_end_ns` it returns only the recompute time. Otherwise it applies responses, then settles, calls `_evaluate`, clears `_cycle`, sends the cycle's places, and returns `recompute + http`.
  - `_apply_cancel_responses(now_ns)`; `_apply_arrived_settles(now_ns)` (cadence's `_settle_arrived`, renamed to a verb per AGENTS.md); `_send_cycle_places(cycle)`; `_on_quoter_wake(event)`.
  - `_on_quoter` keeps its one-line docstring naming poly-maker `Engine._quoter`. That is an external reference, which earlier comment reviews kept.
- **`_submit_place` (`:722-798`):** the body moves verbatim to `_send_place`. The new `_submit_place` appends to the cycle or sends at once (D6). Add `_on_delayed_place(place, event)`, `_draw_place_ns()` and `_draw_cancel_ns()`.
- **`_schedule_cancel` (`:844-859`):** the alert delay at `:857` becomes `self._cancel_latency_ns()`. Add `_cancel_latency_ns()` per D4.
- **`_ack_venue_cancel` (`:1269-1278`):** rewrite per D7. Delete `_drive_cancel_ack` (`:1280-1281`).
- **New lookup:** `_find_core_order(order_id) -> RestingOrder | None`, used by the ack, settles, delayed places and cycle places. The core's `lifecycle._find_order` (`lifecycle.py:101`) is private to that module.

### 3.3 `src/backtest/two_sided_core.py`
- **Imports:**
  - Drop `fallback_ns` (`:21`), `BuySettled`, `CancelUnsettled` and `EngineOutput` (`:25-28`), and `TimeEvent` if it ends up unused.
  - Add `random`, `NS_PER_MS`, and `PLACE_LATENCY_MS`/`draw_latency_ns` from `backtest.live_latency`.
- **Constants:** replace `BUY_SETTLE_*` (`:38-41`) with `CANCEL_LATENCY_DISCOUNT_MS = 15` and `CANCEL_LATENCY_FLOOR_MS = 20`, keeping cadence's measurement note (`:75-78`).
- **Config:** `buy_settle_ns` → `latency_seed` in `TwoSidedKernelConfig` (`:52`), `TwoSidedSettings` (`:63`) and `build_two_sided_kernel` (`:87`). Update the config docstring at `:46` from "BUY cancel settle delay" to the latency seed.
- **Class docstring (`:96`):** drop "BUY cancels settle late". Set `QUOTER_RECOMPUTE_MS = 10`. In `__init__`, `:104` becomes the seeded `self._rng`.
- **Behavior:**
  - `_on_signal_alert`: add `_wake_quoter(now_ns)`.
  - `_evaluate` (`:188-196`): drop `:194`.
  - Delete `_drive_cancel_ack` and `_on_buy_settled` (`:211-231`).
  - Add `_draw_place_ns` and `_draw_cancel_ns` (D4).
- **Size:** expect about 245 lines.

### 3.4 `src/backtest/run.py`
- **Imports:** drop `BUY_SETTLE_MS` from `:158`. Import `TwoSidedCoreStrategy` beside it, `DotaMakerStrategy` at `:155`, and the latency constants from `backtest.live_latency`.
- **`build_order_latency` (`:310-329`):** take keyword-only `insert_latency_ms`. Use it for insert, update and `order_latency_ns`; cancel stays `ORDER_CANCEL_LATENCY_MS`. Update its docstring if it mentions 175 ms.
- **`run_batch` (`:643`):** pick `ORDER_INSERT_LATENCY_MS` when `two_sided is None`, else `VENUE_INSERT_MS`.
- **`build_run_manifest` (`:819-830`):** add `response_ms`, `user_ws_delay_ms`, `buy_confirm_ms`, `book_view_delay_ms`, and `quoter_recompute_ms = DotaMakerStrategy.QUOTER_RECOMPUTE_MS`.
- **Delete the flag:** `--buy-settle-ms` (`:1533-1538`).
- **`_two_sided_settings` (`:1616-1629`):** drop `:1620`; `latency_seed=args.signal_cadence_seed`.
- **`_stamp_strategy` (`:1645-1663`):**
  - `two_sided_engine` becomes `"live_core_quoter"`. Drop `buy_settle_ms`.
  - Set `insert_latency_ms = VENUE_INSERT_MS` (otherwise a two-sided manifest says 175) and `quoter_recompute_ms = TwoSidedCoreStrategy.QUOTER_RECOMPUTE_MS`.
  - Add `latency_seed`, `place_latency_ms` (as a list), `cancel_latency_discount_ms` and `cancel_latency_floor_ms`.
- **`_check_two_sided_flags`:** drop `:1672` and `:1689-1690`.

### 3.5 Not touched
- `src/shared/constants/strategy.py` and `postprocess.py` (STEP-010 owns them).
- `poly-maker` (frozen).
- Investigation scripts `journal_replay.py`/`live_net.py` (STEP-013; `port.md:81-83` already lists what breaks).
- `docs/`: `rg CORE_WAKE docs/as-is.md` finds nothing. The old mentions in `docs/analysis` and `docs/experiments` are dated notes.

## 4. Edge cases the implementer must keep
- **Two book rows on one token with the same `ts_event`:** the alert is armed only when the queue was empty, and both are applied by one callback. No duplicate alert names.
- **Alert names stay unique:** `QUOTER:{match}:{generation}`, `QUOTER_WAKE:{order}:{ns}`, `PLACE_AT:{order}` (one per core order), `BOOK_RECEIVED:{token}:{ns}`, `FILL_NOTICE:{seq}`.
- **Game end:** `_on_game_end` (`:565-575`) still runs outside a cycle. Its cancels take `_cancel_latency_ns()` outside a cycle (own draw; 60 ms for follow300). Cycles at or after `game_end_ns` only add recompute time.
- **`_finished`:** `_on_quoter` returns without re-arming. `_mark_trading_done` still needs no live orders, so it cannot fire while a fill is unheard.
- **A delayed place whose core order was cancelled or rejected mid-flight:** skipped. A place cancelled in the same cycle goes through `_retire_gone_cancel` (`:1254-1257`, no live order yet) and is dropped at send because it is no longer `pending`.
- **A settle or response for an order the core already removed:** skipped (`_find_core_order` is `None`; `apply_cancel_unsettled` ignores it anyway).
- **A BUY with a fill:** the rung stays `gone` until heard fill + 7.25 s. Idle fallback cycles in between are forced `Wake`s that must not re-place that rung; the core holds it.
- **A fill within 15 ms of the replay end:** its notice may not fire. This is the same exposure STEP-008 already has for late prints.

## 5. Tests

### 5.1 Port into `tests/test_backtest_two_sided_core.py`
Port the seven feature.json names from cadence (lines in §1.4).

Helper adaptations:
- `_fastest_http` patches `backtest.two_sided_core.draw_latency_ns` (first table point) and `backtest.strategy.RESPONSE_MS` to 0.
- `_cycle` calls `strategy._run_cycle`.
- Keep `_cancel_at_venue`, `_core_order_status`, `_fire_quoter` and `_FixedDraw`.
- Constants come from `backtest.live_latency`.
- `SETTLE_NS` becomes `USER_WS_DELAY_MS * MS`.

The seven tests:
1. `test_quoter_debounces_a_wake_and_stays_busy_through_http`: a feed tick arms the quoter at T0 + debounce. Firing it submits the two bids and the state is `busy`. The next quoter alert is at `debounce + (10 + 45 + RESPONSE) ms`. Firing that makes the state `idle`, and the idle alert lies after the busy end.
2. `test_quoter_runs_a_fresh_debounce_when_woken_while_busy`: a wake during busy leaves the state `sleeping` at busy end, with the next alert at busy end + debounce.
3. `test_book_rows_reach_the_core_after_the_view_delay`: rows YES@t, YES@t+1 ms and NO@t are applied at t+10 (YES), t+10 (NO), t+11 (YES).
4. `test_latency_draw_follows_the_quantile_table`: a 0.525 draw interpolates halfway between quantile points 0.5 and 0.55; a 0.0 draw equals `VENUE_INSERT_MS`.
5. `test_canceled_bid_hears_the_response_then_settles_on_the_ws_proof`: right after the venue cancel the order is `canceling`. A cycle at +7 ms makes it `gone` with no new submit. A cycle at +15 ms removes it and submits a YES replacement at 0.48.
6. `test_partly_filled_cancel_settles_when_its_fills_confirm`: after a partial fill and a venue cancel, the order stays `gone` through `fill + USER_WS_DELAY_MS + BUY_CONFIRM_MS − 1 ms`. At that time + 1 ms it is removed and a YES bid is submitted. Cadence used `fill + BUY_CONFIRM_MS`; D8 adds the 15 ms.
7. `test_backtest_feeds_the_core_like_live`: the cadence tape with `TapeSettle` matches `LiveCore` at every row. Its drivers `_TwoSidedBacktest` (tick, wake → `_run_cycle`, settle → `_run_cycle`) and `_TwoSidedLive` replace `:319-377`. Drop the `_feed_cancel` override if the base driver already runs the response (5.3).

Other two-sided test updates:
- Delete `test_canceled_bid_waits_for_buy_settled` (`:212-234`), `_fire_settle` (`:136-144`), the `BUY_SETTLE_*` imports (`:52-53`) and `:65`. `_two_sided_config` passes `latency_seed=0` (`:96`).
- The factory test asserts `kernel.latency_seed == 0` (`:316`).
- `test_two_sided_core_hears_a_book_fill_at_its_print` (`:237-258`) fires notices up to `print_ns + USER_WS_DELAY_MS * MS`.

### 5.2 Follow300 updates in `tests/test_backtest_maker.py`
- **Helpers:**
  - `build_maker_config` `:190` → `build_order_latency(insert_latency_ms=ORDER_INSERT_LATENCY_MS)`.
  - `fill_order` `:457-475`: after `on_order_filled`, fire due `FILL_NOTICE:` alerts up to `ts_event + USER_WS_DELAY_MS` with the existing `fire_due_alerts` (`:3131-3143`). Late prints stay pending.
  - `fire_due_alerts` accepts a tuple of prefixes (`str.startswith` takes a tuple), so one call can run `QUOTER` (this prefix also matches `QUOTER_WAKE:`) together with `PLACE_AT:`.
  - `_FakeBook` (`:133-163`) gains an `apply_deltas` that takes bid/ask from the passed namespace, for the book test below.
- **Late-print tests (STEP-008):**
  - `fire_due_alerts` bounds move by `USER_WS_DELAY_MS`: notices at `:3176`, `:3240`, `:3274`, `:3281`, `:3298`, `:3325`; `CANCEL_SEND` bounds at `:3242`, `:3276`, `:3282`.
  - `last_buy_ns` asserts become `PRINT_NS + delay` (`:3178`, `:3334`) and `ts_event + delay` (`:3206`).
  - Check `:2557` (`_last_buy_ns == extra_ns`): it moves by the delay if the shim copies the core value.
- **`test_flat_after_cutoff_stops_later_wakes` (`:743-755`):** replace the `_on_core_wake` call with `_wake_quoter(2 s)` plus firing the quoter alert. Assert the core object is unchanged.
- **The 11 `on_order_canceled` sites, all of which assumed `CancelAck` at the venue cancel.** After the call, run quoter and `PLACE_AT` alerts up to `cancel_ns + RESPONSE_MS + about 100 ms`. That covers the response cycle and a place after 92 ms of cancel HTTP, but not the next fallback cycle at +1 s.
  - No-fill BUY, keep the final asserts: `test_cancel_ack_precedes_replacement` `:1142`, `test_fill_on_a_replaced_order_keeps_its_own_submitted_size` `:1286`, `test_grid_book_move_reanchors_latched_buy` `:1799`, `test_a_new_model_tick_re_anchors_the_ladder_before_and_after_the_first_fill` `:3045`.
  - Game end: `test_game_end_cancel_ack_does_not_submit_again` `:1369` (run the quoter; still no submit).
  - SELL: `test_sell_jumper_below_fair_reprices_to_ceil_fair` `:1911` (`CancelAck` in the response cycle).
  - BUY with a fill before the cancel: `test_late_fill_on_replaced_order_does_not_complete_level` `:2174`, `test_partial_fill_follows_the_book_and_a_full_fill_completes_the_level` `:2287`, `test_partial_cancel_replace_requotes_the_full_rung` `:2354`. Assert that the order is `gone` with no new order on that rung after the response cycle. Then reach the old final asserts at heard fill + `BUY_CONFIRM_MS`.
  - Fill fed after the venue cancel: `test_fill_after_cancel_ack_completes_the_level_it_last_held` `:2832`, `test_episode_survives_a_late_buy_fill_and_then_starts_a_new_one` `:2994`. Feed the fill before running the response cycle and keep the end state. If the end state differs, the test pins old `CancelAck` timing; rewrite it to the `CancelUnsettled` → `BuySettled` sequence and say so in progress.txt.

### 5.3 Adapter contract (`tests/adapter_contract_fixtures.py`, `tests/test_adapter_contract.py`)
- **`BacktestDriver._feed_cancel` (`:418-436`), ok path:**
  - Keep `fire_cancel_release` and `on_order_canceled`, then call `_apply_cancel_responses(event.now_ns)`, `_evaluate(now_ns=event.now_ns)` and `_harvest()`. This runs outside a cycle so follow300 places are not delayed out of `_harvest`.
  - Drop the `BudgetUpdate` drive and its comment (`:426-433`): `on_order_canceled` no longer syncs before the detach, and both calls above sync. Keep the drive only if a budget diff shows up.
- **`play_tape` (`:794-805`) / `build_drivers`:** patch `backtest.strategy.RESPONSE_MS` to 0, so the response lands at the tape row like live's `note_cancel` → `_quote` (`:625-632`). The settle (+15 ms or CONFIRMED) is never applied by the driver, and live has no `settled_buys` either.
- **Docstring (`:15-17`):** delete the sentences saying backtest `CancelAck` frees the BUY rung. They become false.
- **`test_adapter_contract.py`:**
  - Delete `_play_through_buy_cancel` (`:47-62`); its four users call `play_tape` and so keep parity through the cancel: `:82-104`, `:147-165`, `:238-255`, `:289-337`.
  - `_live_buy_stays_gone` (`:65-68`) takes any driver and is asserted on both.
  - `test_partial_buy_fill_then_replace` no longer replaces in backtest. Assert both keep the BUY `gone` with no 0.47 replacement, and rename it to match (for example `test_partial_buy_fill_then_cancel_holds_the_rung`).
  - The BUY price list in `test_recovery_verified_exit_then_flat` (`:330-334`) is re-derived. Parity guarantees backtest and live agree.
- **`tests/test_core_trace.py`:** uses only `LiveDriver` (`_record_tape` `:415-423`); no change.

### 5.4 New follow300 tests (`tests/test_backtest_maker.py`)
The maker harness clock always reads 0 (`:316-321`), and `_schedule_cancel`/`_send_place` time their alerts from `clock.timestamp_ns()`. So run timing tests at t = 0, or give the harness a settable `timestamp_ns`.

1. `test_follow300_book_row_wakes_a_cycle_after_the_view_delay`: a YES book move at t leaves the core books unchanged until t+10 ms. At t+10 the core sees it and a quoter alert is armed; firing it reprices the stale rung (cancel reason `reprice`).
2. `test_follow300_bid_cancel_frees_its_rung_at_the_http_response`: a no-fill BUY venue cancel at c leaves the core order present and nothing re-placed before c + `RESPONSE_MS`. After the response cycle the order is gone and the rung is re-placed.
3. `test_follow300_partly_filled_bid_holds_its_rung_until_confirmed`: after a partial fill heard at f, the cancel response makes the order `gone`. It stays so through f + `BUY_CONFIRM_MS` − 1 ms and is removed and re-placed at f + `BUY_CONFIRM_MS`.
4. `test_follow300_sell_cancel_acks_at_the_http_response`: the SELL's core order stays until the response cycle, then leaves through `CancelAck`.
5. `test_follow300_cycle_places_after_its_cancels_http`: a cycle with one reprice cancel and one new place:
   - the cancel alert is at start + 60 ms;
   - the place goes out on `PLACE_AT` at start + 60 + 32 ms;
   - the cycle stays busy for 6 + 92 + 175 + 32 ms.
6. `test_follow300_fill_is_heard_after_the_user_ws_delay`: an on-time fill at t leaves inventory unchanged until t + 15 ms. Then it is credited with `last_buy_ns == t + 15 ms`, and a quoter alert is armed at that time.
7. `test_idle_quoter_waits_for_the_core_deadline_or_the_fallback`:
   - after a cycle, a future deadline earlier than the fallback becomes the idle alert time;
   - a past unfired deadline fires at once;
   - the same deadline does not fire twice.

If it is cheap to construct, add `test_cycle_sends_places_after_every_plan_of_the_cycle` (D6): a place from a response plan still waits for a cancel that the same cycle's forced `Wake` sends.

### 5.5 `tests/test_backtest.py`
`:44`/`:164`: call `build_order_latency(insert_latency_ms=ORDER_INSERT_LATENCY_MS)`. `test_order_latency_matches_venue_release_offsets` keeps 175/60 ms; STEP-010 rewrites it.

## 6. Risks and STEP-010 hand-off
- **Follow300 changes in four ways at once:** book +10 ms, fills +15 ms, BUY rung held until settle/CONFIRMED, and places wait for the cycle's cancel HTTP. Golden `tests/test_follow300_replay.py` and any LIVE run drift; that is expected until STEP-014.
- **Follow300 model signal and kill gate do not wake the quoter until STEP-010.** They are picked up at the next book-row wake or the 2 s idle timeout. Book rows are dense during maps, but reaction can lag in quiet stretches.
- **Follow300 busy time uses the 175/60 ms round-trip constants plus 32 ms;** no draw until STEP-010. The cancel draw for follow300 is STEP-010's decision.
- **Two-sided skip cycles still apply arrived responses and settles** before the skip. Live skips the core on such a cycle (D11). Expect a small effect; STEP-013 measures two-sided.
- **`--buy-settle-ms` is deleted here,** so STEP-010's item for it becomes a no-op. The 143 ms `CancelUnsettled` → `BuySettled` check on a smoke map stays with STEP-010.
- **Run time:** one alert per book row is added (the `BOOK_RECEIVED` queue), plus quoter alerts. Real-engine replays get slower; measure one map (§7).
- **`journal_replay.py` and `live_net.py` override or call** `_ack_venue_cancel`, `on_order_accepted` and `_arm_wake`, so their behavior changes (`port.md:81-83`). STEP-013.

## 7. Verification commands
Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md), and do not set `PYTEST_N` on the VPS.

```bash
# targeted: the step's own files plus their neighbours
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_two_sided_core.py tests/test_backtest_maker.py tests/test_adapter_contract.py \
  tests/test_backtest.py tests/test_core_trace.py tests/test_lol_backtest.py \
  tests/test_backtest_validation.py tests/test_feed_schedules.py tests/test_onchain_retime.py

# the seven ported names and the new follow300 tests by name (expect all selected to pass)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_two_sided_core.py tests/test_backtest_maker.py \
  -k "quoter or view_delay or quantile_table or ws_proof or fills_confirm or feeds_the_core_like_live or follow300_"

# full suite without the golden replay ("Tests pass" for STEP-001..013): expect 0 failed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck: expect 0 errors
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/live_latency.py src/backtest/strategy.py \
  src/backtest/two_sided_core.py src/backtest/run.py tests/test_backtest_maker.py \
  tests/test_backtest_two_sided_core.py tests/test_backtest.py tests/adapter_contract_fixtures.py \
  tests/test_adapter_contract.py

# no new comments: every hit must be a lifted measurement note from cadence, nothing else
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'

# nothing left of the removed paths: expect no output
rg -n "CORE_WAKE|_on_core_wake|_wake_generation|_armed_wake_ns|_drive_cancel_ack|BUY_SETTLE|buy_settle_|buy-settle" src tests scripts

# real-engine crash check (optional, minutes): golden diffs are expected until STEP-014,
# but every failure must be a golden assertion, not a traceback from src/backtest
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_follow300_replay.py --tb=line
```

Record the wall time of the golden check in progress.txt as the first run-time number on the new cycle. Not run in this step: any full backtest, the 143 ms smoke check (STEP-010), training, data rebuild, live start.

## 8. Commit
Paths:
- `src/backtest/live_latency.py`
- `src/backtest/strategy.py`
- `src/backtest/two_sided_core.py`
- `src/backtest/run.py`
- `tests/test_backtest_maker.py`
- `tests/test_backtest_two_sided_core.py`
- `tests/test_backtest.py`
- `tests/adapter_contract_fixtures.py`
- `tests/test_adapter_contract.py`

Add them by name, not with `-A`. At planning time, `AGENTS.md` and 10 files under `docs/` had uncommitted changes made by someone else. They stay out of this commit.

Message: `fix: STEP-009 - run the poly-maker quote cycle in the shared backtest adapter`.

Do not push. Do not set `passes`. In progress.txt, note that `--buy-settle-ms` is already gone for STEP-010, and that every fill is now heard 15 ms after its print.
