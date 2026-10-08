# STEP-006: `TwoSidedWorker`: quote from the book, restart through Recovery, final merge

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-006.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001 `8dadf747`, STEP-002 `b86cdcdc`, STEP-003 `c779fa21`, STEP-004 `624fb55f` (may be amended by its review), then STEP-005 (`plans/STEP-005.md`: `ctf_merge.py`, `PairMerger` in `pair_merge.py`).
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. Rules that matter here:
- Function names are verbs. Arguments are required (the test fixture's existing defaults are the 1% exception and stay).
- No `dict[str, Any]` for our own data. No anonymous multi-field tuples.
- No branches for inputs that cannot occur.
- **New code gets no comments and no docstrings** (review config). `# pyright:` file pragmas are tool directives and are allowed. When you refactor a method below, delete its docstring.
- Service A (Follow300) behavior must not change. `../poly-maker` is frozen.
- No network in tests. Never use the `*_B` keys.

## Precondition

Start only after STEP-005 is committed. In E:

```bash
git status --short   # only the two untracked data/backtests/dota_maker/validation_*ts-regress-* dirs
grep -n "class PairMerger\|def merge_all\|def wait_inflight\|def attach_core" src/trader/pair_merge.py
grep -n "hold_merge\|release_merge" src/trader/pair_merge.py
grep -n "def install_adapter_merge" src/trader/ctf_merge.py
```

All greps must hit. The second one must show `hold_merge` right before the adapter call and `release_merge` in a `finally` that also covers the booking (STEP-003 handoff: hold before submit, release on every terminal outcome, after core delivery). If `release_merge` is not inside a `finally`, wrap it now in `PairMerger._merge_pairs`:

```python
            store.hold_merge(token_ids=(self._yes, self._no))
            try:
                outcome = await self._call_adapter(amount_raw)
                match outcome.status:
                    ...  # unchanged
            finally:
                store.release_merge(token_ids=(self._yes, self._no))
```

Reason: `MatchWorker.run()` calls `self._dust.cancel()` in its `finally`. A cancel lands inside `_call_adapter`; without a `finally` the hold would outlive the merge and block chain write-downs for the rest of the process. The test in section 7 pins this.

## Goal

1. `match_worker.py`: extract `_core_limits`, `_core_freshness`, `_adopt_core`, `_write_decision` (feature list) plus `_open_trace` (decision 5) without changing logic. Widen the `_dust` slot to `DustSweeper | PairMerger`.
2. Widen `LiveCore` to `Policy` so a two-sided core can quote, persist and budget. Narrow the one Follow300-only reader (`dust_sweep`).
3. New `src/trader/two_sided_worker.py`: `TwoSidedWorker(MatchWorker)`. Book fair in the window, no model, no history gap, clean core with the outbox cursor at the head, `PairMerger` in the `_dust` slot, `merge_all()` after the proven fence.
4. Tests: new `tests/test_trader_two_sided_worker.py` through `build_attached_worker`, one PairMerger cancel test.

Nothing in production constructs `TwoSidedWorker` yet. STEP-007 does.

## Decisions (made without the owner, with reasons)

1. **`TwoSidedPolicy` gets `level_usdc` and `sell_min_life_s`.** `two_sided_policy(*, level_usdc, debounce_ms, fallback_timer_s)` sets `level_usdc` from the caller and bakes `sell_min_life_s=0.0`.
   - `LiveCore.export_checkpoint` / `restore_checkpoint` read `policy.sell_min_life_s` for every order. B posts no SELL and never restores a checkpoint, so 0.0 is exact. `budget_from_orders` reads `quoting.policy.level_usdc`. B's map clip (20 from `config_b`) is the honest value.
   - With both fields on both union members, `LiveCore` only changes its annotations, `session_budget.py` does not change, and there is no `isinstance` switch. `tests/test_trader_session_core.py:868` (`core.policy.level_usdc`) keeps type-checking without an edit.
   - `requote_two_sided` ignores `state.budget`. The 9 × $20 map room shows up in `state.budget.cap_room_usdc`, but nothing enforces it for B. B's brakes are `NET_MAX_SHARES`, the merges and wallet cash. See the STEP-008 handoff.
2. **`dust_sweep.DustSweeper._blocked` narrows with `assert isinstance(policy, Follow300Policy)`.** `is_settling` needs `exit_settle_s`. `DustSweeper` only ever holds a Follow300 core (B uses `PairMerger`), so an assert is the narrowing, not a branch. The codebase already narrows with `assert` (`match_worker._gate_pair`, `engine_seams`).
3. **The `_dust` slot becomes `DustSweeper | PairMerger`, and `PairMerger` gets a no-op `async def sweep(self, *, force: bool) -> None`.** `MatchWorker._quiesce` calls `await self._dust.sweep(force=True)`. B never sells and has no dust FAK. This is the smallest diff and leaves `_quiesce` (A's shutdown path) untouched. `match_worker.py` imports `PairMerger` at runtime: `pair_merge` does not import `match_worker`, so there is no cycle, and STEP-007's `wallet_host` imports `ctf_merge` anyway.
4. **`TwoSidedWorker.__init__` calls `super().__init__(...)` and then sets `self._merger = PairMerger(...)` and `self._dust = self._merger`.** `_merger` is the `PairMerger`-typed handle for `merge_all()`, with no cast. The base constructor's `DustSweeper` is built and dropped. Its `__init__` only stores fields.
5. **New hook `MatchWorker._open_trace(archive_dir) -> CoreTrace | None`**, which returns `try_open_trace(archive_dir)`. `_try_attach` calls it. `TwoSidedWorker` overrides it to return `None`.
   - `try_open_trace` creates `core_trace.jsonl` and keeps a file handle. If B only ignored the trace in `open_core`, it would leak the handle and leave an empty trace that `scripts/core_state.py` would misread.
   - `TwoSidedWorker.open_core` keeps the base signature (`_try_attach` calls it with `trace=`) and does `del trace`.
6. **The outbox cursor goes to the head in `TwoSidedWorker.open_core`**: `core.last_outbox_seq = _outbox_head(store)` when `_wallet_store(self._host.store)` is a `WalletStateStore`.
   - `_try_attach` runs `open_core` → `_restore_open_round` → `register_worker` with no `await` in between. The cursor is in place before `register_worker` runs `_consume_core_outbox`. No row can land in the gap.
   - Old fills and merges are already in the store sizes, and RecoveryVerified copies them into the core.
7. **Window = `window_reason(event.snapshot, fresh)` from `session_quoting`, reused unchanged.** It returns FINISHED, PRE_HORN (outside "PRE_HORN with −60 ≤ s < 0, or IN_PROGRESS with s ≥ 0"; PRE_MATCH/draft is outside), PAUSED, or STALE (only on the first tick after the exit timeout fired).
   - This is the feature's window, because `QUOTE_FROM_SECOND == MODEL_START_SECOND == −60`. A test pins that equality.
   - The extra STALE tick is A's existing rule: the cell stays empty for one tick after a 45 s feed silence. The core's own clock check pulls the bids then anyway.
8. **Book YES fair = `yes_fair_from_model(gated.market_p_radiant, self._yes_is_radiant)`.** `market_p_radiant` comes from the existing `_gate_pair` (sidecar, readiness, raw pair, own-liquidity strip, pair tolerance). Those are the same checks the core runs, so the regime gate and `two_sided_pull_reason` agree. `yes_fair_from_model` is only a polarity map.
9. **Journal rows: new `SignalReason.BOOK = "book"`** when the book fair is published. Otherwise the window or book reason (`pre_horn`, `paused`, `finished`, `missing_book`, …).
   - `radiant_fair` / `yes_fair` stay `None`, and `model_evaluated` is `False`. `SignalDecision` says fair fields are None when the model was not called.
   - The book price is already in `market_p_radiant`, and `yes_is_radiant` is in `session_start`.
   - Readers treat `reason` as `str`: the dashboard labels use `.get(x, x)`, and `summarize.py` counts reasons generically.
10. **The core always gets `signal=None`** through the base `_enqueue_core_signal`. It builds a `RawDeltaSignal` only for `SignalReason.MODEL`. Reusing it also keeps `_alert_stale_cancels`.
11. **B skips the prior fetch, the history-gap check and the board re-quote.** `_on_event` does not call `_maybe_start_prior` (that is an HTTP call) and never sets `_board_source`, so `_quote_board` returns early. Kill ticks still enqueue `KillGateUpdate`. The two-sided requote ignores `kill_gate`, so they do no harm.
12. **New `EntryBlock.BAND = "band"`**, mapped in `session_core._ENTRY_BLOCK`. `"band"` is the only `BlockReason` that STEP-002 added. Without the mapping, B's journal shows `no_edge` for the most common pull near 0.10/0.90. A never emits `"band"`. The other two-sided pull reasons keep A's existing mapping (`cutoff`, `paused`, `halt`, `recovery`, `ownership_unresolved`, or `no_edge`).
13. **B's core gets `drop_sell=_refuse_sell`** (`quote.side is Side.SELL`), not `MatchWorker._drop_sell`. FR-1 says "SELL никогда". `drop_sell` is the existing venue-boundary seam. If the core ever planned a SELL, it would become `OrderRejected("sell_dropped")` plus a warning log, never a venue order.
14. **`TwoSidedWorker._finish_final`:** `if self._attached:` `await self._merger.merge_all()`, inside `try/except Exception` that logs a warning, then `await super()._finish_final()`.
    - `_finish_final` only runs after a proven fence. The base order then gives: merge → `end_snapshot` → journal end → cleanup → Telegram → `zero_token_sizes` → `unregister_worker` → detach. That is every ordering the feature and STEP-005 require.
    - Unattached means no fence ran, so no merge.
    - The `try` keeps finalization (summary, cleanup, zeroing) running if the merge raises on sqlite. `PairMerger` itself never raises on relayer or RPC errors.
15. **No `_quiesce` override for the 190 s `wait_inflight`.** `_quiesce` clears the cell before `wait_inflight()`. `cell.forced` then turns the regime REDUCE_ONLY, so the core pulls both bids (`reduce_only`) on the next quoter cycle (`quoter_tick_s = 2.0`, and the quoter still runs during the wait). On a game-end tick the core also pulls on `game_end`. The final test checks that the cell is forced when `wait_inflight` starts. Waking or cancelling earlier would mean editing A's `_quiesce`.
16. **A late fill of a pre-restart order after a B restart pulls both bids for the rest of that map. Accepted as fail-safe; the test documents it.**
    - Flow: the clean core does not know that venue id, so `apply_fill` parks it in `pending_ownership` and credits the inventory. `two_sided_pull_reason` then returns `ownership_unresolved`.
    - Nothing in production resolves pending ownership. Only `DustSweeper` pre-binds its FAK. `RecoveryVerified` resets inventory, not ownership.
    - Typical trigger: a fill MATCHED before a crash whose CONFIRMED row lands after attach. The engine's start `cancel_all` makes every pre-restart order terminal, so B stops quoting and nothing is lost.
    - Planned restarts of B are blocked by the runbook (STEP-009 `--two-sided --restart-check`: UNSAFE on any open session). The final merge still runs at map end.
    - Follow-up if crash restarts happen mid-map: seed the clean core's `seen_fill_ids` with `store.matched_keys_for_tokens(...)` at open, or resolve unknown pending fills with `OwnershipResolved(terminal=True)` as `DustSweeper._bind_taker_sell` does. Do not build it now.
17. **`finalize_match` (match_meta PnL) stays before the final merge.** `_finish_terminal` snapshots before `_quiesce`. That number values the pairs at book marks, about $1 per pair. The Telegram summary comes after the merge and carries the merge cash (FR-4).
18. **File sizes.** `match_worker.py` is 1374 lines and `session_core.py` 1112 before this step. Splitting them is out of scope: tests monkeypatch `match_worker.*` names, so a split means editing A's tests. This step keeps `match_worker.py` growth at about +20 lines or less by deleting the docstrings of the methods it refactors. It adds 1 line to `session_core.py`. `two_sided_worker.py` stays at about 130 lines and the new test file under 1000.

## Order of edits (paths relative to E)

### 1. `src/strategy/policy.py`

- `TwoSidedPolicy`: add `level_usdc: float` and `sell_min_life_s: float` after `fallback_timer_s`.
- `two_sided_policy`: signature `(*, level_usdc: float, debounce_ms: int, fallback_timer_s: float)`. Pass `level_usdc=level_usdc, sell_min_life_s=0.0`. Delete its docstring.

### 2. `src/trader/session_core.py`

- `from strategy.policy import Follow300Policy` → `from strategy.policy import Policy`.
- `LiveCore.__init__(..., policy: Policy, ...)` and `def policy(self) -> Policy:`. Nothing else in the class changes.
- `_ENTRY_BLOCK`: add `"band": EntryBlock.BAND,` (after `"kill"`).
- Module docstring line 1 and the `LiveCore` docstring: replace "Follow300 core" with "strategy core". Change nothing else.

### 3. `src/trader/session_types.py`

- `SignalReason`: add `BOOK = "book"` after `MODEL`.
- `EntryBlock`: add `BAND = "band"` after `MAX_PRICE`.

### 4. `src/trader/dust_sweep.py`

- Import `from strategy.policy import Follow300Policy`.
- In `_blocked`, replace the final `return is_settling(...)` with:

```python
        policy = core.policy
        assert isinstance(policy, Follow300Policy)
        return is_settling(state=core.state, policy=policy, now_ns=now_ns, token_index=token_index)
```

### 5. `src/trader/pair_merge.py`

After `wait_inflight`:

```python
    async def sweep(self, *, force: bool) -> None:
        del force
```

Plus the `finally` from the Precondition section, only if it is missing.

### 6. `src/trader/match_worker.py` (pure extraction; A logic unchanged)

- Import `from trader.pair_merge import PairMerger` (let ruff place it).
- `__init__`: `self._dust: DustSweeper | PairMerger = DustSweeper(host, self._cid, self._yes, self._no, mode)`.
- `_try_attach`: `trace=try_open_trace(archive_dir)` → `trace=self._open_trace(archive_dir)`.
- New, next to `open_core`:

```python
    def _open_trace(self, archive_dir: Path) -> CoreTrace | None:
        return try_open_trace(archive_dir)
```

- `_quote_pm`: delete the docstring. Replace the trailing journal block (`if self._journal is not None: try: ... return` plus `self._host.engine._wake_cid(self._cid)`) with `self._write_decision(decision)`. New method with exactly the removed body:

```python
    def _write_decision(self, decision: SignalDecision) -> None:
        if self._journal is not None:
            try:
                self._journal.write_signal(decision)
            except Exception as exc:
                if self._reporter is not None:
                    self._reporter.report(PHASE_DECISION, type(exc).__name__)
                self._cell.clear()
                return
        self._host.engine._wake_cid(self._cid)
```

- `open_core`: delete the docstring.
  - `limits = self._core_limits(min_order_size=min_order_size, tick_size=tick_size)`.
  - `freshness = self._core_freshness()`.
  - The header and `start_market_trace` stay as they are.
  - `core = LiveCore(...)` (same arguments), then `self._adopt_core(core)`, then the unchanged `store = _wallet_store(...)` / `load_core_snapshot(store=store, core=core, session_id=self._cid)` block.
  - The only order change: `self._cell.core` is now set before the snapshot load. Nothing reads the cell between them (sync code; the load touches only the core and sqlite).

```python
    def _core_limits(self, *, min_order_size: float, tick_size: float) -> MarketLimits:
        return MarketLimits(
            min_order_size=min_order_size,
            tick_size=tick_size,
            pair_sum_tolerance=PAIR_SUM_TOLERANCE,
            radiant_token_index=0 if self._yes_is_radiant else 1,
        )

    def _core_freshness(self) -> FreshnessLimits:
        return FreshnessLimits(
            book_stale_s=MAX_BOOK_AGE_SECONDS,
            entry_stale_s=self._watchdog.entry_timeout_seconds,
            exit_stale_s=EXIT_FEED_STALE_SECONDS,
        )

    def _adopt_core(self, core: LiveCore) -> None:
        core.session_id = self._cid
        self._core = core
        self._dust.attach_core(core)
        self._cell.core = core
```

Leave `_apply_sidecar_update`'s own `MarketLimits(...)` alone. It uses the new tick/min and the core's stored radiant index.

### 7. New `src/trader/two_sided_worker.py`

```python
# pyright: reportPrivateUsage=false

import time
from pathlib import Path
from typing import TYPE_CHECKING

from polymaker.domain import Quote, Side

from shared.constants.strategy import LIVE_MAX_POSITION_LEVELS
from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.log import get_logger
from strategy.policy import two_sided_policy
from trader.bindings import DiscoveredMatch
from trader.core_trace import CoreTrace
from trader.live_feed import FeedEvent, LiveFeed
from trader.match_worker import MatchWorker, _LatchedFair, _outbox_head, _wallet_store
from trader.model_server import ModelServer, yes_fair_from_model
from trader.pair_merge import PairMerger
from trader.session_core import LiveCore, core_now_ns
from trader.session_quoting import window_reason
from trader.session_types import SignalReason
from trader.trading_mode import ExecutionMode

if TYPE_CHECKING:
    from trader.wallet_host import WalletHost

logger = get_logger(__name__)


def _refuse_sell(quote: Quote) -> bool:
    return quote.side is Side.SELL


class TwoSidedWorker(MatchWorker):
    def __init__(
        self,
        host: "WalletHost",
        discovered: DiscoveredMatch,
        model: ModelServer,
        mode: ExecutionMode,
        feed: LiveFeed,
        feed_timeout_seconds: float,
        exit_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            host, discovered, model, mode, feed, feed_timeout_seconds, exit_timeout_seconds
        )
        self._merger = PairMerger(
            host=host,
            cid=self._cid,
            match_id=discovered.match_id,
            yes_token=self._yes,
            no_token=self._no,
            mode=mode,
        )
        self._dust = self._merger

    def open_core(
        self,
        *,
        level_usdc: float,
        min_order_size: float,
        tick_size: float,
        trace: CoreTrace | None,
    ) -> None:
        del trace
        cadence = read_engine_cadence()
        core = LiveCore(
            policy=two_sided_policy(
                level_usdc=level_usdc,
                debounce_ms=cadence.debounce_ms,
                fallback_timer_s=cadence.quoter_tick_s,
            ),
            limits=self._core_limits(min_order_size=min_order_size, tick_size=tick_size),
            freshness=self._core_freshness(),
            yes_token=self._yes,
            no_token=self._no,
            drop_sell=_refuse_sell,
            trace=None,
            max_position_levels=LIVE_MAX_POSITION_LEVELS[self._discovered.game],
        )
        self._adopt_core(core)
        store = _wallet_store(self._host.store)
        if store is not None:
            core.last_outbox_seq = _outbox_head(store)

    def _open_trace(self, archive_dir: Path) -> CoreTrace | None:
        del archive_dir
        return None

    async def _on_event(self, event: FeedEvent) -> None:
        self._maybe_verify_recovery()
        source_ns = core_now_ns()
        fresh = self._watchdog.consume()
        self._quote_pm(event, time.time(), fresh, source_ns)

    def _quote_pm(self, event: FeedEvent, arrived_at: float, fresh: bool, source_ns: int) -> None:
        gated = self._gate_pair(window_reason(event.snapshot, fresh))
        market_p_radiant = gated.market_p_radiant
        yes_fair = (
            None
            if market_p_radiant is None
            else yes_fair_from_model(market_p_radiant, self._yes_is_radiant)
        )
        reason = SignalReason.BOOK if gated.reason is None else gated.reason
        self._last_second = event.snapshot.second
        self._cell.publish(yes_fair)
        self._enqueue_core_signal(event, reason, market_p_radiant, core_now_ns(), source_ns)
        decision = self._compute_decision(
            event, arrived_at, gated, _LatchedFair(None, None), reason
        )
        self._write_decision(decision)

    async def _finish_final(self) -> None:
        if self._attached:
            try:
                await self._merger.merge_all()
            except Exception as exc:
                logger.warning(
                    "trader final merge failed match=%s err=%s",
                    self._discovered.match_id,
                    type(exc).__name__,
                )
        await super()._finish_final()
```

Notes:
- `_gate_pair` returns `None` for `market_p_radiant` whenever `gated.reason` is set, so `yes_fair` and `reason` agree.
- `_enqueue_core_signal` sends `signal=None` because `reason` is never `SignalReason.MODEL` here.
- `_maybe_verify_recovery` keeps the restart proof running on every tick, as in A.
- Overrides keep the base signatures exactly (basedpyright `reportIncompatibleMethodOverride`).
- `_LatchedFair(None, None)`: `_compute_decision` reads only `latched.model_fair`.

### 8. `tests/test_strategy_two_sided.py` (STEP-001/002's file)

- Line ~81: `two_sided_policy(level_usdc=20.0, debounce_ms=250, fallback_timer_s=1.0)`. Add `assert policy.level_usdc == 20.0` and `assert policy.sell_min_life_s == 0.0`.
- Line ~97: `POLICY = two_sided_policy(level_usdc=20.0, debounce_ms=100, fallback_timer_s=1.0)`.

### 9. `tests/trader_session_fixtures.py`

`build_attached_worker` gets two keyword parameters at the end, `worker_class: type[MatchWorker] = MatchWorker` and `yes_is_radiant: bool = True`. Construct with `worker_class(cast(Any, host), build_discovered(yes_is_radiant=yes_is_radiant, game=game), ...)`. Everything else stays the same. Existing callers keep the defaults, so the existing worker tests run unchanged.

### 10. New `tests/test_trader_two_sided_worker.py`

I could not run this file: STEP-005 is not committed yet. Write it as below and fix only mechanical details (import order, a renamed STEP-005 helper). Do not weaken an assertion.

```python
# pyright: reportPrivateUsage=false

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polymaker.config import StrategyProfile
from polymaker.domain import Fill, OpenOrder, Position, Quote, Regime, Side, TargetQuotes
from polymaker.marketdata.orderbook import BookView
from polymaker.strategy.quoting import QuoteInputs
from trader_session_fixtures import (
    NO_TOKEN,
    YES_TOKEN,
    FakeModelServer,
    build_attached_worker,
    build_event,
    make_binding,
    make_meta,
    read_session_records,
)

from shared.constants.dataset import MODEL_START_SECOND
from strategy.policy import TwoSidedPolicy
from strategy.two_sided import QUOTE_FROM_SECOND
from strategy.types import GameClock, SignalUpdate
from trader import match_worker, session_journal
from trader.core_session_io import consume_core_outbox
from trader.fill_parsing import fill_key
from trader.live_feed import FeedEvent, MatchPhase
from trader.pair_merge import PairMerger
from trader.paths import CORE_TRACE_FILENAME
from trader.session_core import (
    CollateralCache,
    LiveCore,
    books_from_views,
    core_now_ns,
    entry_block_from_reason,
    quote_cycle,
)
from trader.session_types import EntryBlock, SessionEndSnapshot, SidecarMeta
from trader.two_sided_worker import TwoSidedWorker
from trader.wallet_store import WalletStateStore

TOKENS = frozenset({YES_TOKEN, NO_TOKEN})


def _attach_two_sided(
    tmp_path: Path, request: pytest.FixtureRequest, *, yes_is_radiant: bool
) -> tuple[Any, Any, FakeModelServer]:
    model = FakeModelServer()
    worker, fake = build_attached_worker(
        tmp_path,
        request,
        model=model,
        level_usdc=20.0,
        worker_class=TwoSidedWorker,
        yes_is_radiant=yes_is_radiant,
    )
    return worker, fake, model


def _seed_books(worker: Any, *, yes_mid: float) -> None:
    readiness = worker._host.readiness
    for token_id, mid in ((YES_TOKEN, yes_mid), (NO_TOKEN, round(1.0 - yes_mid, 2))):
        book = worker._host.engine.md.book(token_id)
        book.apply_snapshot(
            [(round(mid - 0.01, 2), 10.0)], [(round(mid + 0.01, 2), 10.0)], 100.0, "hash"
        )
        readiness.attach_token(token_id)
        readiness.note_snapshot(token_id)


def _use_wallet_store(
    tmp_path: Path, request: pytest.FixtureRequest, worker: Any, fake: Any
) -> WalletStateStore:
    store = WalletStateStore(tmp_path / "wallet.db")
    request.addfinalizer(store.close)
    fake.state = store
    worker._host.store = store
    return store


def _buy(store: WalletStateStore, *, token_id: str, size: float, price: float, key: str) -> None:
    store.apply_confirmed_fill(Fill(token_id, Side.BUY, price, size, key, 1.0, is_maker=True), key)


def _view(bid: float, ask: float) -> BookView:
    return BookView(
        best_bid=bid,
        best_bid_size=100.0,
        best_ask=ask,
        best_ask_size=100.0,
        second_bid=None,
        second_ask=None,
        bid_depth=100.0,
        ask_depth=100.0,
    )


def _cycle(core: LiveCore, store: WalletStateStore) -> TargetQuotes:
    now_ns = core_now_ns()
    view = _view(0.50, 0.52)
    inputs = QuoteInputs(
        meta=make_meta(),
        regime=Regime.QUIET,
        fv=0.51,
        vol_short=0.0,
        toxicity=0.0,
        yes_view=view,
        no_view=view,
        pos_yes=Position(YES_TOKEN, 0.0, 0.0),
        pos_no=Position(NO_TOKEN, 0.0, 0.0),
        profile=StrategyProfile(),
        now=0.0,
    )
    return quote_cycle(
        core,
        inputs,
        now_ns=now_ns,
        books=books_from_views(yes=_view(0.50, 0.52), no=_view(0.48, 0.50), ts_ns=now_ns),
        clock=GameClock(now_ns=now_ns, game_second=100, paused=False, game_ended=False),
        limits=core.state.limits,
        store=store,
        cache=CollateralCache(value=190.0),
        cores=[core],
        account_cap_usdc=100_000.0,
    )


def test_two_sided_worker_builds_a_two_sided_core_without_trace(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    worker, _fake, _model = _attach_two_sided(tmp_path, request, yes_is_radiant=True)
    core = worker.core
    assert isinstance(core.policy, TwoSidedPolicy)
    assert core.policy.level_usdc == 20.0
    assert core._trace is None
    assert isinstance(worker._dust, PairMerger)
    assert worker._dust is worker._merger
    assert worker._merger._core is core
    assert worker._cell.core is core
    probe = tmp_path / "trace_probe"
    assert worker._open_trace(probe) is None
    assert not (probe / CORE_TRACE_FILENAME).exists()
    assert core._drop_sell(Quote(YES_TOKEN, Side.SELL, 0.50, 5.0)) is True
    assert core._drop_sell(Quote(YES_TOKEN, Side.BUY, 0.50, 5.0)) is False
    assert entry_block_from_reason("band") is EntryBlock.BAND


@pytest.mark.parametrize("yes_is_radiant", [True, False], ids=["yes_radiant", "yes_dire"])
@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (build_event(100), "book"),
        (build_event(QUOTE_FROM_SECOND, phase=MatchPhase.PRE_HORN), "book"),
        (build_event(QUOTE_FROM_SECOND - 1, phase=MatchPhase.PRE_HORN), "pre_horn"),
        (build_event(0, phase=MatchPhase.PRE_MATCH), "pre_horn"),
        (build_event(100, paused=True), "paused"),
        (build_event(2000, phase=MatchPhase.FINISHED), "finished"),
    ],
    ids=["live", "minus_60", "minus_61", "draft", "paused", "finished"],
)
def test_book_fair_is_published_only_inside_the_quote_window(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    event: FeedEvent,
    reason: str,
    yes_is_radiant: bool,
) -> None:
    worker, _fake, model = _attach_two_sided(tmp_path, request, yes_is_radiant=yes_is_radiant)
    _seed_books(worker, yes_mid=0.40)
    asyncio.run(worker.handle_event(event))
    published = reason == "book"
    assert QUOTE_FROM_SECOND == MODEL_START_SECOND
    if published:
        assert worker._cell.yes_fair == pytest.approx(0.40)
    else:
        assert worker._cell.yes_fair is None
    assert model.calls == 0
    updates = [item for item in worker.core._pending if isinstance(item, SignalUpdate)]
    assert updates[-1].signal is None
    assert worker.core.latest_clock.game_second == event.snapshot.second
    signals = [r for r in read_session_records(tmp_path / "journal") if r["kind"] == "signal"]
    assert signals[-1]["reason"] == reason
    assert signals[-1]["model_evaluated"] is False
    assert signals[-1]["yes_fair"] is None
    if published:
        assert signals[-1]["market_p_radiant"] == pytest.approx(0.40 if yes_is_radiant else 0.60)


def test_history_gap_tick_reaches_the_core(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    worker, _fake, model = _attach_two_sided(tmp_path, request, yes_is_radiant=True)
    _seed_books(worker, yes_mid=0.40)

    async def scenario() -> None:
        for second in (60, 70, 500):
            event = build_event(second)
            worker._record_history(event.snapshot)
            await worker.handle_event(event)

    asyncio.run(scenario())
    assert worker._history.check_required_lags(500) is False
    records = read_session_records(tmp_path / "journal")
    assert "history_gap" not in [record["kind"] for record in records]
    assert [r["second"] for r in records if r["kind"] == "signal"] == [60, 70, 500]
    assert worker.core.latest_clock.game_second == 500
    assert worker._cell.yes_fair == pytest.approx(0.40)
    assert model.calls == 0
```

The restart tests and the final-merge test continue in the same file:

```python
def _restart_attach(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, WalletStateStore, int]:
    worker, fake, _model = _attach_two_sided(tmp_path, request, yes_is_radiant=True)
    host = worker._host
    host.unregister_worker(worker._cid)
    fake.metas.pop(worker._cid, None)
    worker._attached = False
    worker._quoting = False
    store = _use_wallet_store(tmp_path, request, worker, fake)
    _buy(store, token_id=YES_TOKEN, size=20.0, price=0.40, key="t1:before-yes")
    _buy(store, token_id=NO_TOKEN, size=10.0, price=0.55, key="t2:before-no")
    store.apply_merge(tx_hash="0xbefore", token_ids=(YES_TOKEN, NO_TOKEN), qty=5.0)
    head = store.core_outbox_after(after_seq=0, tokens=TOKENS)[-1].seq

    def fake_attach(*_args: object, **_kwargs: object) -> None:
        fake.metas[worker._cid] = make_meta()

    def fake_sidecar_meta(*_args: object, **_kwargs: object) -> SidecarMeta:
        return SidecarMeta(make_meta(), "0.01", make_binding())

    def first_start(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            binding=make_binding(), clip=SimpleNamespace(clip_usdc=20.0, reason="pinned")
        )

    def scan_open_round(
        yes_token_id: str, no_token_id: str, min_order_size: float
    ) -> session_journal.JournalRound:
        del yes_token_id, no_token_id, min_order_size
        return session_journal.JournalRound(15.0, 5.0, None)

    monkeypatch.setattr(match_worker, "attach_market", fake_attach)
    monkeypatch.setattr(match_worker, "build_sidecar_meta", fake_sidecar_meta)
    journal = SimpleNamespace(first_start=first_start, scan_open_round=scan_open_round)
    archive_dir = tmp_path / "restart_archive"
    archive_dir.mkdir()
    asyncio.run(worker._try_attach(archive_dir, cast(Any, journal)))
    assert not (archive_dir / CORE_TRACE_FILENAME).exists()
    return worker, store, head


def _prove_chain(worker: Any) -> None:
    async def chain_balances(token_ids: list[str]) -> dict[str, float]:
        del token_ids
        return {YES_TOKEN: 15.0, NO_TOKEN: 5.0}

    worker._host.read_fresh_balances = chain_balances
    assert asyncio.run(worker.continue_recovery_async()) is True


def test_restart_takes_inventory_from_chain_then_bids_both_sides(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, store, head = _restart_attach(tmp_path, request, monkeypatch)
    core = worker.core
    assert core.last_outbox_seq == head
    assert core.state.recovery_pending is True
    consume_core_outbox(store=store, core=core, identity=worker._identity(), tokens=TOKENS)
    assert [item.qty for item in core.state.inventory] == [0.0, 0.0]
    _prove_chain(worker)
    assert core.state.recovery_pending is False
    assert [item.qty for item in core.state.inventory] == [15.0, 5.0]
    targets = _cycle(core, store)
    bids = {quote.token_id: quote for quote in targets.quotes}
    assert set(bids) == {YES_TOKEN, NO_TOKEN}
    assert all(quote.side is Side.BUY for quote in targets.quotes)
    assert bids[YES_TOKEN].size < bids[NO_TOKEN].size


def test_late_fill_of_a_pre_restart_order_pulls_both_bids(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, store, _head = _restart_attach(tmp_path, request, monkeypatch)
    core = worker.core
    _prove_chain(worker)
    _cycle(core, store)
    planned = core.take_plan()
    assert planned is not None
    placed = [
        OpenOrder(
            f"v-{item.quote.token_id}",
            item.quote.token_id,
            item.quote.side,
            item.quote.price,
            item.quote.size,
        )
        for item in planned.to_place
    ]
    core.note_placed(placed, core_now_ns())
    core.drain_apply()
    late_key = fill_key("t-late", "order-before-restart")
    late = Fill(YES_TOKEN, Side.BUY, 0.40, 5.0, late_key, time.time(), is_maker=True)
    store.apply_matched_fill(late, late_key)
    consume_core_outbox(store=store, core=core, identity=worker._identity(), tokens=TOKENS)
    assert core.state.ownership_unresolved is True
    targets = _cycle(core, store)
    pulled = core.take_plan()
    assert targets.quotes == ()
    assert pulled is not None
    assert set(pulled.to_cancel) == {f"v-{YES_TOKEN}", f"v-{NO_TOKEN}"}
    assert core.last_block == "ownership_unresolved"
    assert entry_block_from_reason(core.preview_block_reason()) is EntryBlock.OWNERSHIP_UNRESOLVED


def test_final_merge_runs_after_the_fence_and_before_the_summary_and_zeroing(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_worker, "TRADER_DIR", tmp_path)
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    worker, fake, _model = _attach_two_sided(tmp_path, request, yes_is_radiant=True)
    store = _use_wallet_store(tmp_path, request, worker, fake)
    _buy(store, token_id=YES_TOKEN, size=12.0, price=0.40, key="t1:yes")
    _buy(store, token_id=NO_TOKEN, size=10.0, price=0.55, key="t2:no")
    cash_before = store.ledger_net_cash_for_tokens({YES_TOKEN, NO_TOKEN})
    worker._cell.publish(0.40)
    host = worker._host
    calls: list[str] = []
    finished: list[tuple[object, ...]] = []
    market_orders: list[tuple[object, ...]] = []

    async def wait_inflight() -> None:
        calls.append(f"wait forced={worker._cell.forced}")

    async def fence_market(token_ids: set[str], timeout_s: float) -> bool:
        del token_ids, timeout_s
        calls.append("fence")
        return True

    async def merge_all() -> None:
        calls.append("merge_all")
        store.apply_merge(tx_hash="0xfinal", token_ids=(YES_TOKEN, NO_TOKEN), qty=10.0)

    end_snapshot = worker.end_snapshot

    def record_end_snapshot() -> SessionEndSnapshot:
        calls.append("end_snapshot")
        return end_snapshot()

    zero = store.zero_token_sizes

    def record_zero(token_ids: set[str]) -> None:
        calls.append("zero")
        zero(token_ids)

    unregister = host.unregister_worker

    def record_unregister(cid: str) -> None:
        calls.append("unregister")
        unregister(cid)

    async def market_order(*args: object, **kwargs: object) -> dict[str, object]:
        market_orders.append((args, kwargs))
        return {}

    def record_finished(*args: object) -> None:
        finished.append(args)

    monkeypatch.setattr(worker._merger, "wait_inflight", wait_inflight)
    monkeypatch.setattr(worker._merger, "merge_all", merge_all)
    monkeypatch.setattr(host, "fence_market", fence_market)
    monkeypatch.setattr(worker, "end_snapshot", record_end_snapshot)
    monkeypatch.setattr(store, "zero_token_sizes", record_zero)
    monkeypatch.setattr(host, "unregister_worker", record_unregister)
    monkeypatch.setattr(fake.gateway, "market_order", market_order)
    monkeypatch.setattr(match_worker, "notify_session_finished", record_finished)

    asyncio.run(worker._quiesce(True))

    assert calls == [
        "wait forced=True",
        "fence",
        "merge_all",
        "end_snapshot",
        "zero",
        "unregister",
    ]
    _alert, realized, _imv, _rebate, leftover_yes, leftover_no = finished[0]
    assert realized == pytest.approx(cash_before + 10.0)
    assert leftover_yes == pytest.approx(2.0)
    assert leftover_no == 0.0
    assert store.position(YES_TOKEN).size == 0.0
    assert market_orders == []
```

What each test covers:
- **builds_a_two_sided_core_without_trace**: decisions 1, 3, 4, 5, 12, 13.
- **book_fair_is_published_only_inside_the_quote_window** (feature: fair in the window; None on pause, before −60 and after game end):
  - both orientations: the YES fair always equals the YES mid;
  - boundary −60 quotes and −61 does not; draft does not;
  - no model call, `signal=None` to the core, journal reason `book`.
- **history_gap_tick_reaches_the_core** (feature: a tick with a history gap is not dropped): the same 60/70/500 sequence as A's `test_history_gap_tick_journals_and_keeps_its_snapshot`. `check_required_lags(500) is False` proves 500 is a gap.
- **restart_takes_inventory_from_chain_then_bids_both_sides** (feature: chain inventory → Recovery → RecoveryVerified → bids on both sides). It also covers:
  - the cursor at the head, including an old merge that is not replayed;
  - no `core_trace.jsonl` through the real `_try_attach`;
  - `budget_from_orders` on a `TwoSidedPolicy` core through `quote_cycle`;
  - the skew (the heavy YES side bids smaller).
- **late_fill_of_a_pre_restart_order_pulls_both_bids**: the restart handoff (decision 16).
- **final_merge_runs_after_the_fence_and_before_the_summary_and_zeroing** (feature: merge_all before zero_token_sizes; the summary includes merge cash; no FAK SELL). It also checks that the cell is forced while `wait_inflight` runs (decision 15).

### 11. `tests/test_trader_pair_merge.py` (STEP-005's file): one test

Use that file's helpers (`_open_host`, `_merger_for`, `_record_alerts`, `MERGED`, `YES`, `NO` in the STEP-005 plan). Add `import threading`.

```python
def test_cancel_mid_merge_releases_the_merge_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_alerts(monkeypatch)
    started = threading.Event()

    def slow_merge(*, cfg: Config, condition_id: str, amount_raw: int) -> MergeOutcome:
        del cfg, condition_id, amount_raw
        started.set()
        time.sleep(0.3)
        return MERGED

    monkeypatch.setattr(pair_merge, "merge_pairs_via_adapter", slow_merge)
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    merger = _merger_for(host, mode="live")

    async def run() -> None:
        merger.schedule()
        while not started.is_set():
            await asyncio.sleep(0.01)
        assert host.store.merge_is_held(YES)
        merger.cancel()
        await merger.wait_inflight()

    asyncio.run(run())
    assert host.store.merge_is_held(YES) is False
    assert host.store.merge_is_held(NO) is False
    assert host.store.position(YES).size == 150.0
```

`asyncio.run` waits for the default executor on exit, so the 0.3 s thread finishes before the test ends. It books nothing.

## Edge cases

| Case | What happens |
|---|---|
| Fresh map, empty wallet | `_restore_open_round` classifies `fresh`; quoting starts at −60 s once the book is good |
| Restart, inventory on chain | `recovery` → bids pulled (`recovery`) → `continue_recovery_async` proves chain == store → RV → `requote_two_sided` clears `sell_only` → bids on both sides |
| Restart, session row unreadable (`corrupt`) | no Recovery at attach; the first quote cycle's `_position_mismatch` (core 0 vs store) queues Recovery, same result |
| Restart, late fill of an old order | `ownership_unresolved` for the rest of the map; final merge still runs (decision 16) |
| Old fills/merges in the outbox | skipped by the head cursor; already in store sizes and RV |
| Own bid is the only bid on a token | `core_books` strips it → `OWN_LIQUIDITY_ONLY`, cell None, core `stale_book`; both gates agree, bids come back next tick |
| Mid outside [0.10, 0.90] | core pulls `band`; journal `entry_block=band` |
| Feed silent ≥ entry timeout | watchdog cancels BUYs (A's path); core clock goes stale → `stale_signal` |
| Feed silent ≥ exit timeout | cell cleared → REDUCE_ONLY; first tick after is `stale`, the next republishes |
| Threshold merge in flight at game end | `_quiesce` waits ≤ 190 s; bids already pulled (`game_end` + cell cleared); then cancel, fence, `merge_all` |
| Fence unproven | `keep_quiet`, no `_finish_final`, no final merge; pairs redeem at settlement (manual, Non-Goal) |
| Feed ends without final (`_quiesce(False)`) | no final merge; pairs stay on the wallet |
| `merge_all` raises (sqlite) | logged; finalization still runs; merge state on chain is whatever the relayer did |
| Final merge disabled after an `unknown` | `merge_all` returns at once (STEP-005); summary has no merge cash |
| match_meta final vs Telegram | match_meta is pre-merge at marks; Telegram is post-merge (decision 17) |
| Two B maps at once | one `PairMerger` each; `engine._chain_lock` serializes relayer calls |
| Kill ticks | `KillGateUpdate` enqueued and ignored by the two-sided requote; `_quote_board` returns early |

## What later steps need from this step

**STEP-007 (host / daemon):**
- `_pick_and_run`: build `TwoSidedWorker` when `strategy == "two_sided"`, else `MatchWorker`. Pass the same six positional arguments (`self, handoff, self._models[profile_name], self._mode, feed, feed.stale_seconds`).
  - B still needs the dota model loaded: `MatchStart` provenance and the journal pin read `model.model_reference`. `TwoSidedWorker` never calls `predict_fair`.
  - Test idea: monkeypatch `MatchWorker.run` / `TwoSidedWorker.run` with a recorder of `type(self)`.
- `_install_runtime_seams`: `install_adapter_merge(self.engine)` only for `two_sided` (STEP-005).
- `PairMerger` merges only in `mode == "live"`. The start check must require live (already in the feature).
- The clip from `[clips.dota].tiers` (BLAST Slam → 20.0) becomes `TwoSidedPolicy.level_usdc` through `_try_attach`. A clip with `reason == "default"` still sends A's "trader default clip" Telegram line. With the whitelist that should never fire for B.
- `wallet_host.py` needs no other change for B. `register_worker`, `_consume_core_outbox`, `read_fresh_balances` and `_budget_cache` are what `PairMerger` uses.

**STEP-008 (config_b):** the two-sided core does not read `Budget`, so "9 × $20 = $180" is not a limit B enforces. It only shows as `state.budget.cap_room_usdc`. Word the `base_size_usdc` comment so it does not promise a $180 map cap (the brakes are NET_MAX_SHARES 50, merges and wallet cash), or tell the owner. `stop_grace_period`: see STEP-005's note (190 s merge + cancel + 20 s fence).

**STEP-009 (summarize / runbook):**
- B journal signal rows: `reason=book` (fair published), `yes_fair`/`radiant_fair` null, `market_p_radiant` = book Radiant price, `model_evaluated=false`. `entry_block` can be `band`.
- `summarize.py`'s `last_model` stays None for B.
- After a crash restart mid-map, `entry_block=ownership_unresolved` for the rest of that map is expected (decision 16).
- An unproven fence or a feed drop means no final merge for that map: redeem by hand.

## Do not touch

- `src/trader/wallet_host.py`, `src/trader/engine_seams.py`, `src/trader/core_session_io.py`, `src/trader/core_persistence.py`, `src/trader/wallet_store.py`, `src/trader/ctf_merge.py`, `src/trader/session_budget.py`.
- `src/strategy/**` except `policy.py`. `src/backtest/**`, `config/**`, `compose.yaml`, `scripts/**`.
- Existing test files except `tests/trader_session_fixtures.py` (two defaulted kwargs), `tests/test_strategy_two_sided.py` (two call sites + two asserts) and `tests/test_trader_pair_merge.py` (one new test).
- `../poly-maker`. Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests + PairMerger + strategy two-sided
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_two_sided_worker.py tests/test_trader_pair_merge.py tests/test_trader_ctf_merge.py \
  tests/test_strategy_two_sided.py -q

# every existing match_worker test (must pass with no edits to these files)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_match_lifecycle.py tests/test_board_signal_contract.py \
  tests/test_trader_wallet_sidecar.py tests/test_lol_prior.py tests/test_trader_dust_sweep.py \
  tests/test_core_trace.py tests/test_trader_session_quoting.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_wallet_host.py \
  tests/test_trader_live_execution.py -q

# LiveCore / budget / persistence / journal users + A regression (Follow300 replay goldens)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_session_core.py tests/test_trader_shared_budget.py \
  tests/test_trader_core_persistence.py tests/test_trader_core_recovery.py \
  tests/test_trader_core_execution.py tests/test_trader_engine_seams.py \
  tests/test_trader_wallet_store.py tests/test_trader_session_journal.py \
  tests/test_strategy_core.py tests/test_follow300_replay.py -q

# backtest self-check unchanged
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided

# A's files untouched; goldens not rewritten
git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py tests/fixtures \
  tests/test_trader_match_lifecycle.py tests/test_trader_dust_sweep.py tests/test_core_trace.py
git status --short tests/fixtures   # expect nothing

# no comments/docstrings in new code; McCabe < 10; sizes
grep -nE '^\s*#|"""' src/trader/two_sided_worker.py tests/test_trader_two_sided_worker.py | grep -v '# pyright:'   # expect nothing
uv run ruff check --select C901 src/trader/two_sided_worker.py src/trader/match_worker.py src/trader/dust_sweep.py
wc -l src/trader/match_worker.py src/trader/session_core.py src/trader/two_sided_worker.py \
  tests/test_trader_two_sided_worker.py   # match_worker ≤ ~1395, session_core ≤ 1114, new files < 1000

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/strategy/policy.py src/trader/session_core.py src/trader/session_types.py \
  src/trader/dust_sweep.py src/trader/pair_merge.py src/trader/match_worker.py \
  src/trader/two_sided_worker.py tests/trader_session_fixtures.py tests/test_strategy_two_sided.py \
  tests/test_trader_pair_merge.py tests/test_trader_two_sided_worker.py
make lint
git status --short   # only the untracked backtest dirs remain
```

Expected:
- All green. `two_sided ok`. Empty `git diff` for A's files. 0 basedpyright errors. No C901 finding.
- If `make lint` reformats or re-sorts imports, re-stage and run it again.
- Leave `PYTEST_N` unset. The full serial suite is STEP-010's job. Run `make test` here too if time allows.
- No test touches the network. If one hangs, a fake is missing (`read_fresh_balances`, `merge_pairs_via_adapter`).

Review checklist for the diff of `match_worker.py`: only the import, the `_dust` annotation, `_open_trace`, `_write_decision`, `_core_limits`, `_core_freshness`, `_adopt_core`, the call-site swaps and the two deleted docstrings. No other line moves.

Then, per the implement skill:
- One commit in E on `main`. Suggested message: `Run two-sided maps in a TwoSidedWorker: book fair, clean restart, final merge.`
- Update `W/tasks/two-sided-live-b/feature.json` and append to `W/tasks/two-sided-live-b/progress.txt` as for the earlier steps (they were left `passes: false` for the orchestrator's review).
  - Record decisions 1, 5, 6, 9, 12, 13, 15 and 16.
  - Record the STEP-007/008/009 handoffs above.
- No push.
