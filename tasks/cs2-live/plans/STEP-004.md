# STEP-004 plan: Trader 2A — move Follow300Signals out of the worker

Step: `STEP-004` of `tasks/cs2-live/feature.json` (priority 4, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `6c5f6ed0983ddc19a1619646ccae69b9e4dd4993`.
Evidence folder: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-004/`).
`report.md` exists. Append a `## STEP-004` section. Never overwrite the file.
No Figma or design links exist for this step.

The step is a pure refactor. Dota/LoL behavior must not change. No CS2 code lands here.
The MOBA signal state leaves `MatchWorker` for a new `Follow300Signals` object behind a `SignalSource` protocol.
The worker keeps the session lifecycle, the prior latch, the books gate, the cell, the journal and the core.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-005+.

## 0. Validated prototype

I applied this plan to a scratch copy of HEAD and checked it. Everything is in
`betting_workspace/tasks/cs2-live/orchestrate/work/plan-s4/` (below: `$P4`).

- `$P4/proto.patch` (981 lines): the full src + test change. `git apply --check` passes on HEAD `6c5f6ed0`.
  Use it as a reference. The plan text below is the contract.
- Signal differential on the prototype: 27 514 lines, 0 different lines vs the baseline (§5).
- Prototype checks: basedpyright strict on the 7 touched/new files plus `wallet_host.py`: `0 errors, 0 warnings, 0 notes`.
  `ruff check` and `ruff format --check` with the repo `pyproject.toml`: clean.
- Prototype targeted pytest (the §6.3 list): 375 passed. The other 12 are `test_trader_wallet_host.py` tests that fail with
  `OddinFeedError: ODDIN_BRAND_TOKEN is not set` on the untouched baseline snapshot too (the snapshot has no `.env`).
  In the repo they pass: baseline targeted run is `373 passed`.

## 1. Facts this plan rests on

All line numbers are `src/trader/match_worker.py` at `6c5f6ed0` unless named.

- MOBA signal fields on the worker: `self._history = SnapshotHistory(history_policy)` (`:205-206`), `self._board = BoardHistory()` (`:207`),
  `self._board_handles` (`:209`), `self._last_raw_delta` (`:211`).
- `run()` (`:251-258`): a `KillTick` goes to `_on_kill_tick`; every `FeedEvent` calls `self._board.record_table(core_now_ns(), ...)`
  and `_record_history` (`:297-300`, skips finished) before the first-event and quoting logic.
- `_on_event` (`:448-464`): `_maybe_start_prior`, `_maybe_verify_recovery`, `_board_source = None`, `received_ns = core_now_ns()`,
  then the required-lags check (`:454-459`). A gap writes `history_gap` and returns before `self._watchdog.consume()` (`:461`).
  So a gap tick does not re-arm the freshness watchdog. `tests/test_trader_match_lifecycle.py:126-164` pins this.
- `_quote_pm` (`:466-475`) → `_gate_pair` → `_latch_fair` (`:524-546`) → `_cell.publish` → `_enqueue_core_signal` (`:587-616`) → `_compute_decision` (`:627-675`).
- `_latch_fair` reads `self._prior`, calls `self._board.derive(core_now_ns(), ...)`, `self._model.predict_fair(...)`, sets
  `self._last_raw_delta`, maps YES with `yes_fair_from_model(prediction.fair, self._yes_is_radiant)`, and turns
  `ModelPredictionError` into `MODEL_ERROR`. A missing prior gives `MISSING_PRIOR` only when the gate has no reason (`:531-534`).
- `_enqueue_core_signal` builds `RawDeltaSignal(self._last_raw_delta, ...)` only when the reason is `MODEL` and the market price exists.
  `MODEL` means `_latch_fair` succeeded on this very call, so the cached delta is always this call's delta. The cache can go.
- `_gate_pair` (`:488-522`) returns `market_p_radiant` exactly when its reason is `None`.
- `_on_kill_tick` (`:548-576`): `record_board` always; when `self._core is None` it stops there. Otherwise it enqueues
  `KillGateUpdate`, wakes the CID, prunes `_board_handles`, and schedules `_quote_board` after `BOARD_REACTION_SECONDS` (0.11 s).
- `_quote_board` (`:578-585`) needs worker state: `_board_source`, `_quoting`, `_quiesced`, `_watchdog.entry_timeout_seconds`, `_quote_pm`.
- `_stop_feed_timers` (`:291-295`) cancels the board handles. Its only production call is `run()`'s `finally` (`:282`).
  The worker has no other stop path: `wallet_host` never calls `_quiesce` or `_stop_feed_timers`.
- `_pin_feed_orientation` (`:302-315`) changes `self._yes_is_radiant` on the first tick, after `__init__`.
  So the YES polarity cannot be a constructor argument of the signal source.
- `RoundSignal` subclasses `RawDeltaSignal` (`src/backtest/cs2_round_sides.py:24`). `RawDeltaSignal` is the right core signal type for the contract.
- `LiveCore.enqueue(event: InboundEvent)` (`src/trader/session_core.py:311`); `InboundEvent` is in `src/strategy/types.py:462`.
- History policies (`strategy_catalog(...).history`): Dota GRID and LoL GRID `start_second=60, max_pivot_gap_seconds=30.0, drop_gap_ticks=True`;
  Dota Oddin `start_second=-60, max_pivot_gap_seconds=15.0, drop_gap_ticks=False`. After a tape of 60, 70 the
  lag check at 500 is `False` for GRID Dota and LoL and `True` for Oddin (command output in §5 notes).
- Tests that touch the moved members (grep of `tests/`): `test_board_signal_contract.py` (25 refs),
  `test_trader_match_lifecycle.py` (17), `test_trader_session_quoting.py` (3). No `src/` or `scripts/` file outside the worker uses them.
- Tests patch `trader.match_worker.core_now_ns` in 6 files (`test_board_signal_contract.py:142,187`, `test_trader_match_lifecycle.py:1197`,
  `test_trader_sell_venue_cancel.py:149,283,290`, `test_trader_unsettled_buy_activation.py:384,698,701`, `test_trader_live_execution.py:201,345`).
  So every clock read must stay in `match_worker.py`. The signal source gets `now_ns` as an argument.
- Baseline: `basedpyright` → `0 errors, 0 warnings, 0 notes`; `ruff check src tests scripts` → `All checks passed!`;
  `ruff format --check src tests scripts` → `529 files already formatted`; targeted pytest (§6.3 list without the new file) → `373 passed`.

## 2. Design decisions

1. **`SignalSource` has four methods, not two.** The feature names `on_feed_event` and `on_tick`. Two more are required by today's behavior:
   - `check_history(snapshot) -> bool`: the worker must skip a gap tick *before* `watchdog.consume()`. `on_feed_event` runs after the books gate,
     which needs `fresh` from `consume()`. One method cannot do both without changing the watchdog order.
   - `stop()`: the feature asks that the signal timers belong to the signal object and get cancelled on session stop.
2. **`on_tick` takes every tick.** `on_tick(tick: FeedEvent | KillTick, now_ns)`. A `FeedEvent` tapes table deaths and history (before and after attach, like today).
   A `KillTick` does the kill gate. The `run()` loop calls `on_tick` first, then `continue`s on a `KillTick`.
3. **`on_feed_event` signature** (keyword-only after `event`):
   `on_feed_event(event, *, market_p_radiant, prior, reason, yes_is_radiant, now_ns, source_ns) -> SignalOutcome`.
   PLAN.md had `(event, market_p_radiant, prior, reason, now_ns)` and `yes_is_radiant` in the constructor.
   - `yes_is_radiant` is per call because the worker pins it on the first tick, after construction (§1).
   - `source_ns` is needed because the source builds the core signal (`source_received_ns`). A board re-quote passes the old table receipt.
4. **`SignalOutcome` carries the core signal.** Frozen dataclass: `reason: SignalReason`, `model_fair: ModelFair | None`, `signal: RawDeltaSignal | None`.
   `reason` is `MODEL` on success. The worker no longer builds `RawDeltaSignal`, so STEP-009 can return a `RoundSignal` through the same field.
   `self._last_raw_delta` is deleted.
5. **One clock read per quote.** `_quote_pm` reads `now_ns = core_now_ns()` once, after `_gate_pair`, and passes it to `on_feed_event`.
   The source uses it for `board.derive` and `RawDeltaSignal.received_ns`. The worker uses the same value for `feed_core`.
   Known deviation: today `received_ns` and the core clock come from a second read *after* the model call (`:473`).
   They now come from the read before it. The shift is the model call time: `_predict_delta` takes 0.175–0.245 ms per row on this Mac
   (3 production catalogs, 300 calls each). `received_ns == GameClock.now_ns` still holds, and signals stay strictly ordered.
   A second read inside the source would need a clock dependency that the 6 test files above do not patch. I chose the single read.
6. **Worker callbacks are explicit constructor arguments.** `Follow300Signals(model, history, board, enqueue_core, requote)`:
   - `enqueue_core: Callable[[InboundEvent], bool]` = new worker method `_enqueue_core`: enqueue + wake the CID, `False` when no core exists.
     The `bool` keeps today's rule: no re-quote timer before attach (record-only maps never schedule timers).
   - `requote: Callable[[], None]` = the existing worker method `_quote_board`. It keeps `_board_source` and the lifecycle checks in the worker.
   No callback reads a private worker field.
7. **`history` and `board` are passed in.** The worker still builds them in `__init__` from `strategy_catalog(...).history`.
   STEP-005 moves this construction into `Follow300Strategy.build_signals`. Passing them in lets the unit tests own and inspect them.
8. **File placement.** `SignalSource` and `SignalOutcome` go into a new `src/trader/strategy_spec.py`, where PLAN.md puts `StrategySpec` in STEP-005.
   `Follow300Signals` goes into a new `src/trader/follow300_signals.py`.
9. **The invariant in §1 (`_gate_pair`) becomes an `assert`.** After the prior and reason returns, `assert market_p_radiant is not None`.
   Today an impossible `(None, None)` gate would journal `MODEL` with no fair. AGENTS.md: no branches for inputs that cannot occur.
10. **Names that stay.** `_board_source`, `_quote_board`, `_stop_feed_timers`, `_on_event`, `handle_event`, `_gate_pair`, `_compute_decision`, `_enqueue_core_signal`
    keep their names. The differential harness (§5) and the remaining tests call them.

## 3. Code changes (in this order)

### 3.1 New `src/trader/strategy_spec.py`

Module docstring: one line (prototype: `"""Strategy contracts the match worker trades through."""`).
Imports: `dataclasses.dataclass`, `typing.Protocol`, `strategy.types.RawDeltaSignal`, `trader.live_feed` (`FeedEvent`, `GameSnapshot`, `KillTick`),
`trader.session_types` (`ModelFair`, `SignalReason`). No import of `match_worker` or `follow300_signals`.

```python
@dataclass(frozen=True)
class SignalOutcome:
    """One feed event's core signal and model fair, or the reason there is none."""

    reason: SignalReason
    model_fair: ModelFair | None
    signal: RawDeltaSignal | None


class SignalSource(Protocol):
    """Feed ticks in, core signals out; the worker owns the session and the core."""

    def on_tick(self, tick: FeedEvent | KillTick, now_ns: int) -> None:
        """Record one feed tick, before and after attach."""
        ...

    def check_history(self, snapshot: GameSnapshot) -> bool:
        """False when this snapshot lacks the history a signal needs."""
        ...

    def on_feed_event(self, event: FeedEvent, *, market_p_radiant: float | None, prior: float | None,
                      reason: SignalReason | None, yes_is_radiant: bool, now_ns: int, source_ns: int) -> SignalOutcome:
        """The core signal for this event, or the first reason it has none."""
        ...

    def stop(self) -> None:
        """Cancel every timer this source owns."""
        ...
```

Follow the `LiveFeed` protocol style (`src/trader/live_feed.py:154-161`).

### 3.2 New `src/trader/follow300_signals.py`

Class `Follow300Signals`, one-line class docstring. Constructor (all arguments required):
`model: ModelServer`, `history: SnapshotHistory`, `board: BoardHistory`, `enqueue_core: Callable[[InboundEvent], bool]`, `requote: Callable[[], None]`.
Fields: `_model`, `_history`, `_board`, `_enqueue_core`, `_requote`, `_board_handles: list[asyncio.TimerHandle] = []`.

- `on_tick(tick, now_ns)`:
  - `KillTick` → `self._on_kill_tick(tick, now_ns)`; return.
  - `FeedEvent` → `self._board.record_table(now_ns, snapshot.deaths_radiant, snapshot.deaths_dire)`; then, if not `snapshot.finished`,
    `self._history.record(snapshot.second, snapshot_history_levels(snapshot))`. Same order as `:255-258` + `:297-300`.
- `check_history(snapshot)`: `return snapshot.finished or self._history.check_required_lags(snapshot.second)` (the `:454-456` condition, negated).
- `on_feed_event(...)` — the body of `_latch_fair` plus the signal build of `_enqueue_core_signal`:
  1. `prior is None` → `SignalOutcome(reason=MISSING_PRIOR if reason is None else reason, model_fair=None, signal=None)`.
  2. `reason is not None` → `SignalOutcome(reason=reason, model_fair=None, signal=None)`.
  3. `assert market_p_radiant is not None`.
  4. `try:` `board = self._board.derive(now_ns, deaths_radiant, deaths_dire)`; `prediction = self._model.predict_fair(snapshot, market_p_radiant, prior, self._history, board)`;
     `yes_fair = yes_fair_from_model(prediction.fair, yes_is_radiant)`; `except ModelPredictionError:` → `SignalOutcome(reason=MODEL_ERROR, model_fair=None, signal=None)`.
  5. `signal = RawDeltaSignal(predicted_delta=prediction.raw_delta, source_received_ns=source_ns, received_ns=now_ns, anchor_p=market_p_radiant, deaths_radiant=snapshot.deaths_radiant, deaths_dire=snapshot.deaths_dire)`.
  6. `return SignalOutcome(reason=MODEL, model_fair=ModelFair(prediction.fair, yes_fair, prediction.raw_delta), signal=signal)`.
- `stop()`: cancel every handle in `_board_handles`, then clear the list (the old `:293-295`).
- `_on_kill_tick(tick, now_ns)`: keep the old docstring idea in one line. `record_board(now_ns, ...)`; build the `KillGateUpdate` exactly as `:554-567`;
  `if not self._enqueue_core(gate): return`; prune handles with `not handle.cancelled() and handle.when() > loop.time()`;
  append `loop.call_later(BOARD_REACTION_SECONDS, self._requote)`. Use one `loop = asyncio.get_running_loop()`.

Imports: `asyncio`, `collections.abc.Callable`, `BOARD_REACTION_SECONDS`, `BoardHistory` (`shared.utils.board_features`), `SnapshotHistory`,
`snapshot_history_levels` (`shared.utils.dota_features`), `NS_PER_SECOND` (`shared.utils.match_time`), `InboundEvent`, `KillGate`, `KillGateUpdate`,
`KillWait`, `RawDeltaSignal` (`strategy.types`), `FeedEvent`, `GameSnapshot`, `KillTick` (`trader.live_feed`), `ModelPredictionError`, `ModelServer`,
`yes_fair_from_model` (`trader.model_server`), `ModelFair`, `SignalReason` (`trader.session_types`), `SignalOutcome` (`trader.strategy_spec`).
None of these import `match_worker` (checked), so there is no cycle.

### 3.3 `src/trader/match_worker.py`

- Imports: drop `BOARD_REACTION_SECONDS`, `snapshot_history_levels`, `KillGate`, `KillGateUpdate`, `KillWait`, `GameSnapshot`, `ModelPredictionError`,
  `yes_fair_from_model`, `ModelFair`. Keep `BoardHistory`, `SnapshotHistory` (construction), `NS_PER_SECOND` (`_quote_board`), `RawDeltaSignal`, `KillTick`, `asyncio`.
  Add `InboundEvent` (`strategy.types`), `Follow300Signals` (`trader.follow300_signals`), `SignalOutcome`, `SignalSource` (`trader.strategy_spec`).
- Delete `_LatchedFair` (`:166-171`).
- `__init__` (`:205-211`): keep `history_policy = strategy_catalog(...).history`. Replace `_history`, `_board`, `_board_handles`, `_last_raw_delta` with
  ```python
  self._signals: SignalSource = Follow300Signals(
      model=model,
      history=SnapshotHistory(history_policy),
      board=BoardHistory(),
      enqueue_core=self._enqueue_core,
      requote=self._quote_board,
  )
  ```
  The `SignalSource` annotation makes basedpyright check that `Follow300Signals` satisfies the protocol.
- `run()` (`:251-258`): `self._signals.on_tick(event, core_now_ns())`, then `if isinstance(event, KillTick): continue`. Delete the two record lines.
- `_stop_feed_timers`: `self._watchdog.disarm()` then `self._signals.stop()`. Delete `_record_history`.
- `_on_event` (`:454-456`): `if not self._signals.check_history(event.snapshot):` — the gap branch body stays.
- `_quote_pm`:
  ```python
  gated = self._gate_pair(window_reason(event.snapshot, fresh))
  now_ns = core_now_ns()
  outcome = self._signals.on_feed_event(
      event,
      market_p_radiant=gated.market_p_radiant,
      prior=self._prior,
      reason=gated.reason,
      yes_is_radiant=self._yes_is_radiant,
      now_ns=now_ns,
      source_ns=source_ns,
  )
  model_fair = outcome.model_fair
  self._last_second = event.snapshot.second
  self._cell.publish(model_fair.yes if model_fair is not None else None)
  self._enqueue_core_signal(event, outcome.signal, now_ns)
  decision = self._compute_decision(event, arrived_at, gated, outcome)
  self._write_decision(decision)
  ```
- Delete `_latch_fair` and `_on_kill_tick`. Add in their place:
  ```python
  def _enqueue_core(self, update: InboundEvent) -> bool:
      """Queue one core input and wake the quoter; False before the core opens."""
      if self._core is None:
          return False
      self._core.enqueue(update)
      self._host.engine._wake_cid(self._cid)
      return True
  ```
- `_enqueue_core_signal(self, event: FeedEvent, signal: RawDeltaSignal | None, now_ns: int)`: keep the `self._core is None` return, `feed_core(...)` with `signal=signal`, and `_alert_stale_cancels()`.
- `_compute_decision(self, event, arrived_at, gated, outcome: SignalOutcome)`: `model_fair = outcome.model_fair`; `reason=outcome.reason`. Nothing else changes.
- `_quote_board`, `_board_source`, `_BoardSource`, the prior latch, `_on_exit_feed_timeout` stay as they are.

Prototype diff stat for this file: 38 insertions, 112 deletions.

## 4. Tests

### 4.1 Update (seams only; keep every existing assertion unless named here)

- `tests/test_board_signal_contract.py`:
  - Add helpers `_tape(worker, event)` = `worker._signals.on_tick(event, match_worker.core_now_ns())` and
    `_last_core_signal(worker) -> RawDeltaSignal | None` = the `.signal` of the last `SignalUpdate` in `worker.core._pending`.
    `match_worker.core_now_ns()` resolves the monkeypatched clock where a test patches it.
  - The 11 `worker._record_history(X.snapshot)` calls (`:147, 192, 199, 230, 235, 240, 244, 345, 373, 398, 439`) → `_tape(worker, X)`.
  - `:154` `worker._on_kill_tick(KillTick(...))` → `worker._signals.on_tick(kill, now[0])`.
  - `test_skip_error_journal_never_reuses_the_cached_delta`: replace the `_last_raw_delta` asserts (`:233, 238, 259`) with core-signal asserts:
    after the first event the last core signal has `predicted_delta == approx(0.031)`; after the paused event it is `None`; after the error event it is `None`.
    The journal assertions stay unchanged.
  - Add `from strategy.types import RawDeltaSignal, SignalUpdate`.
- `tests/test_trader_match_lifecycle.py`:
  - Move `test_worker_tapes_every_non_terminal_snapshot` (`:115-123`) to the new file (§4.2) and delete it here.
  - `:148, 155, 1201, 1212` → `worker._signals.on_tick(X, match_worker.core_now_ns())`.
  - `:162-163` → `worker._signals._history._seconds` / `worker._signals._history._first_second` (the fixture returns the worker as `Any`).
  - `:1218` `worker._on_kill_tick(tick)` → `worker._signals.on_tick(tick, now[0])`.
- `tests/test_trader_session_quoting.py`:
  - Add `_outcome_for(worker, event, market_p_radiant, reason, now_ns) -> SignalOutcome` that calls `worker._signals.on_feed_event(...)` with `prior=worker._prior`,
    `yes_is_radiant=worker._yes_is_radiant`, `source_ns=now_ns`.
  - `_decision_for` (`:137-142`): gate once, `outcome = _outcome_for(worker, event, gated.market_p_radiant, gated.reason, core_now_ns())`, then `worker._compute_decision(event, arrived_at, gated, outcome)`.
  - `test_anchor_uses_stripped_mid` (`:211-249`, the change is at `:237-243`): gate, `outcome = _outcome_for(..., now_ns)`, assert `outcome.signal.anchor_p == approx(stripped_p)`,
    then `worker._enqueue_core_signal(event, outcome.signal, now_ns)`. The latch assertions after it stay.
  - Add `from trader.strategy_spec import SignalOutcome`.

### 4.2 Add `tests/test_follow300_signals.py` (15 tests in the prototype)

Direct tests of `Follow300Signals` with `FakeModelServer` (`tests/trader_session_fixtures.py:355`) cast to `Any`, a real `BoardHistory`,
a real `SnapshotHistory(strategy_catalog(game=..., feed_source=...).history)`, and a small `_Worker` dataclass stand-in with
`enqueue_core(update) -> bool` (records, returns its `attached` flag) and `requote()` (counts). One test per behavior the feature lists:

| Feature item | Test |
|---|---|
| finish, pause (tape) | `test_tick_tapes_every_non_terminal_snapshot`: tape 70, 80, paused 85, finished 90 → `history._seconds == [70, 80, 85]` (moved from lifecycle, paused added) |
| required lags (Dota/LoL policies) | `test_check_history_follows_the_catalog_lag_policy` parametrized dota/GRID → False, dota/ODDIN → True, lol/GRID → False for 500 after a 60, 70 tape; 70 → True; finished 500 → True |
| prior, pause, finish, stale | `test_gate_and_prior_reasons_skip_the_model`: (None, None) → MISSING_PRIOR; (None, PAUSED) → PAUSED; (0.5, PAUSED/STALE/FINISHED) → same; no model call; whole outcome equality |
| fair | `test_model_fair_and_core_signal` parametrized yes_is_radiant True/False: fair 0.6, mid 0.4, prior 0.48 → MODEL, yes 0.6/0.4, delta 0.2, `received_ns`/`source_received_ns`, deaths (1, 3), `fair_args == [(0.4, 0.48)]` |
| model error | `test_model_error_has_no_signal` |
| kill gate, board freshness | `test_kill_tick_gates_the_core_and_requotes_after_the_board_reaction`: exact `KillGateUpdate` (until 21 s / 11 s), no re-quote before 0.11 s, one after `asyncio.sleep(BOARD_REACTION_SECONDS + 0.05)`; then the model board: deaths 2, pending 1, age `0.11`, dire pending 0 |
| kill before attach | `test_kill_tick_before_attach_tapes_the_board_without_a_requote` |
| timers stop | `test_stop_cancels_pending_requotes` |

The existing worker tests stay the worker-level regressions: the board re-quote (`test_board_signal_contract.py`,
`test_live_board_uses_received_snapshot_without_rearming_watchdog` at `tests/test_trader_match_lifecycle.py:1190`), the history gap without watchdog re-arm, the gates and provenance.

## 5. Baseline Dota/LoL differential (evidence, feature item "regressions on baseline facts")

Scratch tool from planning, tested on the baseline and on the prototype: `$P4/signals_diff.py` and `$P4/sessions.txt`
(the STEP-003 session list: 6 Dota + 6 LoL finished sessions in local `data/trader`).
Copy both into `investigations/2026-10-23-cs2-live-validation/work/step-004/` (`$W`).

What it does, per tick, on a worker built with `trader_session_fixtures.build_attached_worker`:
- Replays `grid_state.jsonl` through `profile.feed.build_reducer(...).reduce_frame` and keeps `FeedEvent` and `KillTick` in order.
- Clock: `trader.match_worker.core_now_ns` returns the frame receipt time. Books: the journaled `market_p_radiant` of that second. Prior: the journaled `prior`.
- Models: the real production catalogs, loaded from the repo `data/` (Dota GRID 81, Dota Oddin 74 on the Dota sessions, LoL 81).
- Base path: `_board.record_table` + `_record_history` / `_on_kill_tick`. New path: `_signals.on_tick` (chosen by `hasattr(worker, "_signals")`).
  Both then call `handle_event`. Board re-quotes fire as `_quote_board()` at kill time + 0.11 s on the scripted clock.
- Hashes per tick: new journal rows (without `recorded_at_utc`), new core inbound events, each model call (catalog feature row, board features, prediction).
- Plus 40 synthetic seeds × 3 catalogs (kills, pauses, clock gaps, missing prior, finished).

Baseline result at `6c5f6ed0` (planning run, 34 s): 27 514 lines, sha256 prefix `7b70dde69205f782`, deterministic over two runs.
Coverage tally (stderr): 17 598 `model` signal rows, 3 406 `history_gap`, 898 `missing_prior`, 860 `paused`, 138 `finished`, 119 `pre_horn`,
3 604 `KillGateUpdate`, 2 347 model calls from board re-quotes. A mutant that drops `record_table` in `on_tick` gives 3 106 different lines.

Procedure (from the trader repo root):

1. Before any edit: `git rev-parse HEAD` and `git status --short`. Expect `6c5f6ed0…` and a clean tree.
2. Base run: `PYTHONPATH=src:tests .venv/bin/python $W/signals_diff.py data/trader $W/sessions.txt > $W/signals-base.txt 2> $W/signals-base.err`.
   If the edits already started: `mkdir -p $W/base_snapshot && git archive 6c5f6ed0 src tests config | tar -x -C $W/base_snapshot`,
   then run with `PYTHONPATH=$W/base_snapshot/src:$W/base_snapshot/tests` from the repo root. `config/` is required (`read_template`). Do not create a worktree.
3. After the change: the same command into `$W/signals-head.txt` / `signals-head.err`.
4. Write `$W/signals-diff.json`: `base_rev`, `head_rev`, line counts per prefix (`synthetic dota-grid`, `synthetic dota-oddin`, `synthetic lol-grid`,
   `session dota-grid`, `session dota-oddin`, `session lol-grid`), the tally lines, `different_lines`, first 5 differing pairs.
   Expected: 27 514 lines each and `different_lines == 0`. Any difference fails the step.

## 6. Verification

From the trader repo root:

1. `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` → clean.
2. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`.
3. Targeted pytest (baseline 373 passed without the new file; prototype adds 15, removes 1):
   ```
   PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 \
     tests/test_follow300_signals.py tests/test_board_signal_contract.py tests/test_trader_match_lifecycle.py \
     tests/test_trader_session_quoting.py tests/test_trader_live_execution.py tests/test_trader_sell_venue_cancel.py \
     tests/test_trader_unsettled_buy_activation.py tests/test_trader_durable_place.py tests/test_lol_prior.py \
     tests/test_trader_wallet_sidecar.py tests/test_trader_wallet_host.py tests/test_model_server.py
   ```
   Expect `387 passed`.
4. Full suite: `PYTEST_N=8 make test` on this Mac. Record pass/fail counts (STEP-003 recorded 3587 passed).
5. Signal differential: §5 → `different_lines == 0`.
6. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.strategy_spec, trader.follow300_signals, trader.match_worker, trader.wallet_host"`.
7. `grep -n "_last_raw_delta\|_record_history\|_latch_fair\|_on_kill_tick\|_LatchedFair\|self\._history\|self\._board\b\|_board_handles" src/trader/match_worker.py` → no output.
8. `git diff --stat` touches only: `src/trader/match_worker.py`, `src/trader/strategy_spec.py` (new), `src/trader/follow300_signals.py` (new),
   `tests/test_follow300_signals.py` (new), `tests/test_board_signal_contract.py`, `tests/test_trader_match_lifecycle.py`, `tests/test_trader_session_quoting.py`.

No browser check: the feature item for this step has none, and the step touches no dashboard file.

Write the results (revision, differential result and tally, pytest counts, basedpyright and ruff output, total time) into `report.md`
of the evidence folder under a `## STEP-004` heading.

## 7. Commit

One commit in `esports-trader` on `main`, for example:
`refactor(trader): move MOBA signal state into Follow300Signals behind SignalSource`.
No push. `progress.txt` gets the usual STEP-004 entry from the implement skill.

## 8. Out of scope (later steps own these)

- `StrategySpec`, `Follow300Strategy`, `build_signals`, moving `strategy_catalog`/history-policy construction out of the worker,
  `LIVE_MAX_POSITION_LEVELS`, `QuoteSides` in `LiveCore`, `BUY_CUTOFF_SECOND_BY_GAME`, `LeagueFilter` (STEP-005).
- The game branch in `_maybe_start_prior` (`:683`, LoL book prior vs Dota journal prior). The prior latch stays in the worker; the source gets `prior` per call.
- Widening `on_tick` to CS2 round ticks, `RoundSignals`, buy windows (STEP-007/009).
- Renaming `_board_source` / `_quote_board`. The harness and 13 test references (8 `_board_source`, 5 `_quote_board`) use these names.
- New comments. The review config forbids narrating comments in new code. Docstrings follow the file's existing style.

## 9. Risks and hard parts

- **Watchdog order.** If the gap check moves into `on_feed_event`, a gap tick re-arms the watchdog.
  `test_history_gap_tick_journals_and_keeps_its_snapshot` (`tests/test_trader_match_lifecycle.py:126`) fails on `_generation`. Keep `check_history` before `consume()`.
- **Clock reads.** A `core_now_ns` call inside `follow300_signals.py` escapes the `trader.match_worker.core_now_ns` patches in 6 test files and in the harness.
  Pass `now_ns` from the worker everywhere (`run()`, `_quote_pm`).
- **The one timing shift (§2.5).** Reviewers may see `received_ns` read before the model call. It is deliberate, sub-millisecond, and keeps
  `received_ns == GameClock.now_ns`. The harness cannot see it (scripted clock). Core trace replay (STEP-005 gate 2) replays recorded inputs, so it cannot see it either.
- **YES polarity.** A `yes_is_radiant` stored in the constructor would miss `_pin_feed_orientation`.
  `test_raw_delta_stays_radiant_oriented_and_unclipped` sets `worker._yes_is_radiant = False` after construction and catches it.
- **Timer before attach.** Scheduling the re-quote without the `enqueue_core` result would add no-op timers on record-only maps.
  `test_kill_tick_before_attach_tapes_the_board_without_a_requote` pins the old rule.
- **Test seeding changes.** `_tape` records table deaths as well as history; the old tests seeded history only. Board features then see a table count earlier.
  In the prototype every existing assertion still passed, including `board_death_age_radiant_s == approx(0.11)` in the lifecycle board test.
- **Snapshot runs.** A `git archive` snapshot needs `config/` for `read_template`. In a snapshot, 12 `test_trader_wallet_host.py` tests fail on
  `ODDIN_BRAND_TOKEN is not set` (no `.env`) on base and head alike. Run pytest in the repo, not in a snapshot.
- **Real-time sleeps.** Three new tests sleep `BOARD_REACTION_SECONDS + 0.05` (0.16 s each). Keep them; they prove the timer, not a direct call.
