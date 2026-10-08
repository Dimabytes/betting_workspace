# STEP-002: two-sided quoting in the core (requote_two_sided, Policy in engine and scheduling)

Task: `W/tasks/two-sided-live-b/feature.json`, step STEP-002.
Code repo: `E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Base: STEP-001 is committed as `8dadf747` ("Move two-sided quote math into strategy so live and backtest share it."). This plan uses that code.
No Figma, no UI.

Read `W/AGENTS.md` and `E/AGENTS.md` first. Rules from them that matter here: function names are verbs, all arguments required and keyword-only, no `dict[str, Any]`, no anonymous `tuple[A, B]` for our own values (a per-token pair such as `tuple[TokenInventory, TokenInventory]` is the existing pattern and is fine), no branches for inputs that cannot occur, no comments that narrate the code. The review config also says: **no new comments or docstrings in new code.** A one-line module docstring for the new module is fine, because every module in `src/strategy` has one.

## Goal

The pure strategy core gets a second policy. With `TwoSidedPolicy`, `engine.step` calls `requote_two_sided`. It keeps one post-only BUY per token at the backtest prices and sizes, and pulls both bids for 12 reasons in a fixed order. A 1-tick move waits 300 ms; a move of 2 or more ticks happens at once. No SELL is ever planned. Follow300 (service A) must not change by a single byte: `tests/test_strategy_core.py` and `tests/test_follow300_replay.py` pass and are not edited. The live trader does not call this code yet.

## What STEP-001 gave us (real code)

- `src/strategy/two_sided.py`: constants `TICK, MAX_BID_SUM_TICKS, ORDER_SHARES=20, NET_MAX_SHARES=50, HALF_SPREAD_TICKS=3, SKEW_PER_SHARE=2e-4, BAND_HI=0.90, QUOTE_FROM_SECOND=-60, REPRICE_HOLD_NS=300_000_000, REPRICE_NOW_TICKS=2`, plus `BidTicks`, `price_bids`, `inventory_skew`, `scale_order_size`. It imports nothing from `strategy`.
- `src/strategy/policy.py`: `TwoSidedPolicy(order_shares, net_max_shares, half_spread_ticks, skew_per_share, band_hi, quote_from_second, tick, max_bid_sum_ticks, reprice_hold_ns, reprice_now_ticks, debounce_ms, fallback_timer_s)`, `Policy = Follow300Policy | TwoSidedPolicy`, `two_sided_policy(*, debounce_ms, fallback_timer_s)`. **`policy.py` imports constants from `strategy.two_sided` at module top.**
- `tests/test_strategy_two_sided.py` exists (self-check values). STEP-002 adds its tests to this file.

## Decisions (made without the owner, with reasons)

1. **The quoting code goes in a new module `src/strategy/two_sided_quoting.py`, not in `two_sided.py`.** `policy.py` imports `strategy.two_sided`. `quoting.py` and `lifecycle.py` import `strategy.policy`. If `two_sided.py` imported `quoting` or `lifecycle` (it needs `mark_canceling`, `place_missing`, `QuoteTarget`), the import chain `two_sided → quoting → policy → two_sided` would fail at import time. So `two_sided.py` stays pure math shared with the backtest. The live quoting sits next to it, like `quoting.py` sits next to `signals.py`. Adding about 150 lines to `quoting.py` (951 lines) would also push it past 1k lines.
2. **Quote knobs come from the `TwoSidedPolicy` fields** (`policy.order_shares`, `policy.half_spread_ticks`, `policy.band_hi`, ...), not from module constants. STEP-001 baked the constants into the policy; reading the constants again would leave those fields dead. The values are the same.
3. **Orientation: the backtest's "YES" leg is the Radiant token.** `fair = core_book_p(...)` (Radiant probability from the book pair, the same normalization the backtest uses). `net = inventory[radiant].qty - inventory[dire].qty`. `BidTicks.yes_ticks` goes to the Radiant token and `no_ticks` to the Dire token. `scale_order_size(token_index=...)` gets the leg index (0 = Radiant leg, 1 = Dire leg), not the venue token index. This matches `TwoSidedMakerStrategy._desired` line by line, so prices are the same for both `radiant_token_index` values. The venue token index is only used for `QuoteTarget.token_index` and `level_index`.
4. **Two-sided reconciles its own bids instead of calling `quoting.reconcile`.** `reconcile` skips a "reprice" cancel when the BUY is partly filled and that token holds less than `min_order_size` (`_skip_reprice` → `inventory_is_dust`). That rule protects a Follow300 SELL exit from dust. For two-sided it would freeze a partly filled 20-share bid at a stale price after a 1–4 share fill on a fresh map: a bid above fair that keeps buying. Two-sided never sells, so the rule does not apply. Our matching is about 15 lines, because each token has at most one BUY. We still reuse `quoting.place_missing` for budget checks, order ids, and `occupy_buy`.
5. **`StrategyState.reprice_since_ns` is a required field; `empty_state` sets `(None, None)`. It is NOT added to `StructuralCheckpoint` and NOT added to the trace digest.** The feature text says the checkpoint and trace codecs write and read this field. We do not do that, for these reasons:
   - `now_ns` is `time.monotonic_ns()` (`session_core.core_now_ns`). A raw monotonic value from an old process means nothing after a restart, and the checkpoint stores every other timing as remaining seconds. A restored hold start from a previous boot could also sit in the future and hold a 1-tick move until the price comes back.
   - B never restores a checkpoint (STEP-006) and has no trace (Resolved Questions: `trace=None`).
   - Adding the field to a digest group changes every Follow300 trace digest and breaks the A replay goldens. That is forbidden: the test files must stay unchanged.
   - `latch`, `mid_spike`, `kill_gate` and `schedule` are transient in the same way and are not in the checkpoint either.

   Result: Follow300 checkpoints without the field decode exactly as before, because the codec does not change. After `apply_checkpoint` the field stays `(None, None)` from `empty_state`.
6. **Reason names.** Only `"band"` is added to `BlockReason`. The other pull reasons reuse existing names, as the backtest and Follow300 do: halt → `"halt"`, pending ownership → `"ownership_unresolved"`, recovery_pending → `"recovery"`, game end → `"game_end"`, pause → `"paused"`, second < −60 → `"cutoff"` (the backtest's name), stale clock → `"stale_signal"` (the feed clock is two-sided's only signal), reduce_only → `"reduce_only"`, allow_buy false → `"fair"` (Follow300's name for it), stale book → `"stale_book"`, band → `"band"`, pair tolerance → `"pair_tolerance"`. A token whose live bid no longer has a target (its size fell below the minimum, or its price is ≤ 0 ticks) is cancelled with `"reprice"`.
7. **Stale clock** means `not is_fresh(now_ns=now_ns, ts_ns=state.clock.now_ns, max_age_s=state.freshness.entry_stale_s)`. The live `clock.now_ns` is the arrival time of the last feed tick (`feed_core` → `core.latest_clock`). The core's initial clock has `now_ns=0`, so nothing is quoted before the first feed tick.
8. **Size rounding is `share_floor(scale_order_size(...))`.** The backtest uses `round(size, 2)`. `share_floor` is the live convention (`buy_share_quantity`) and never rounds up past the net cap. The two differ by at most 0.01 share.
9. **Two private helpers in `quoting.py` become public, as mechanical renames:** `_books_unusable` → `books_unusable`, and `_place_missing` → `place_missing`. `_place_missing` also loses its `policy` parameter, which it never reads. The repo runs basedpyright in strict mode (`reportPrivateUsage` is an error), and a `# pyright:` suppression would be flagged by the comment review. No test imports these names (checked with grep).
10. **`_tick_gap` moves from `backtest/two_sided_strategy.py` to `strategy/two_sided.py` as `tick_gap(*, live_price, want_price)`**, with the same body. The backtest imports it. This keeps one copy of the hold-rule math, in the spirit of STEP-001. The behavior of the backtest does not change. STEP-010 re-runs the 20-map regression.
11. **`LiveCore` (trader/session_core.py) stays typed `Follow300Policy` in this step.** It reads `policy.sell_min_life_s` (checkpoint export/restore), and its readers expect Follow300 fields: `session_budget` reads `level_usdc` and `dust_sweep` calls `is_settling`. Widening it needs design work for B's budget (`TwoSidedPolicy` has no `level_usdc`), and its first caller is `TwoSidedWorker.open_core` in STEP-006. "Сигнатуры core" here means the pure strategy core: `engine.step` and the `scheduling` functions. See the handoff list at the end.
12. **The bid price is not capped below best ask.** The backtest does not cap it either. On Polymarket the two token books are mirrors, so the pair sum stays near 1 and a bid 3 ticks under fair cannot cross. If an inconsistent snapshot ever makes it cross, a post-only order is rejected; it is never filled as a taker. Watch for "crosses book" BUY rejects on the first live map.
13. **`sell_only` is cleared only when `recovery_pending` is False.** While recovery is pending, `_reenter_recovery` must still bump the generation on a fill. Otherwise a `RecoveryVerified` that read the chain before the fill would overwrite the inventory with old numbers. Once recovery has resolved, `requote_two_sided` sets `sell_only=False`, and later fills no longer re-enter recovery.
14. **Every pull also resets `reprice_since_ns` to `(None, None)`, and the hold step rebuilds the pair from scratch on every evaluation.** After an evaluation a hold start is either `None` or less than `reprice_hold_ns` old. A past hold boundary therefore can never make the core wake up in a loop.
15. **The hold applies to any non-cancelling, non-gone BUY, accepted or still pending.** The backtest skips unaccepted orders. In the core a cancel of a pending order already waits for the venue id, so the extra branch adds nothing.
16. **The public names follow feature.json** (`two_sided_pull_reason`, `desired_bids`, `reprice_hold_boundary_ns`) so they can be traced to the spec. They match existing core names such as `sell_boundary_ns` and `held_sell`. Private helpers use verbs.
17. `requote_two_sided` returns `tuple[StrategyState, Plan]`, the same contract as `quoting.requote`, which `engine.step` unpacks. Changing both is out of scope.

## Order of edits

All paths are relative to E.

### 1. `src/strategy/types.py`

- Add `"band",` to `BlockReason` (for example after `"pair_tolerance",`). Every consumer derives the set (`core_trace_codec._BLOCK_REASONS` uses `get_args`). `entry_block_from_reason` maps unknown reasons to `NO_EDGE`.
- In `StrategyState`, add a required field `reprice_since_ns: tuple[int | None, int | None]` right after `schedule: QuoteSchedule` and before `delta_gate_open: bool = False` (a required field cannot follow a defaulted one). Index = venue token index; the value is the monotonic ns at which a 1-tick hold started, or `None`.

### 2. `src/strategy/lifecycle.py`

- In `empty_state(...)`, pass `reprice_since_ns=(None, None),` next to `schedule=...`. `empty_state` is the only place that constructs `StrategyState` (checked with grep).

### 3. `src/strategy/two_sided.py` (pure math, still no `strategy` imports)

Append:

```python
def tick_gap(*, live_price: float, want_price: float) -> int:
    return abs(round(live_price / TICK) - round(want_price / TICK))
```

### 4. `src/backtest/two_sided_strategy.py`

- Add `tick_gap` to the existing `from strategy.two_sided import (...)` block.
- In `_should_reprice`, change the call to `tick_gap(live_price=live_price, want_price=want_price) >= REPRICE_NOW_TICKS`.
- Delete `def _tick_gap(...)` at the end of the file.

### 5. `src/strategy/quoting.py` (rename only, no logic change)

- `def _books_unusable(state, now_ns)` → `def books_unusable(state, now_ns)`. Update its 2 callers (`_quote_after_gates`, `_recovery_exit`).
- `def _place_missing(*, state, policy, missing, keeps, moves, now_ns)` → `def place_missing(*, state, missing, keeps, moves, now_ns)`. Drop `policy`; the body never reads it. In `reconcile`, call `place_missing(state=state, missing=..., keeps=..., moves=..., now_ns=now_ns)`.

### 6. New `src/strategy/two_sided_quoting.py`

Imports: `dataclass`, `replace`; `share_floor` from `shared.utils.trading`; `already_canceling`, `mark_canceling` from `strategy.lifecycle`; `TwoSidedPolicy` from `strategy.policy`; `QuoteTarget`, `books_unusable`, `empty_plan`, `place_missing` from `strategy.quoting`; `core_book_p`, `is_fresh` from `strategy.signals`; `inventory_skew`, `price_bids`, `scale_order_size`, `tick_gap` from `strategy.two_sided`; `BlockReason`, `BookPair`, `KeepOrder`, `Plan`, `RestingOrder`, `StrategyState` from `strategy.types`.

Reference implementation (adapt names freely, but keep the behavior):

```python
"""Two-sided maker quotes in the core: pull reasons, bid targets, one-tick hold."""


@dataclass(frozen=True)
class HeldBids:
    targets: tuple[QuoteTarget, ...]
    reprice_since_ns: tuple[int | None, int | None]


def _session_pull_reason(*, state: StrategyState) -> BlockReason | None:
    if state.permissions.halt:
        return "halt"
    if state.ownership_unresolved:
        return "ownership_unresolved"
    if state.recovery_pending:
        return "recovery"
    return None


def _clock_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    clock = state.clock
    if clock.game_ended:
        return "game_end"
    if clock.paused:
        return "paused"
    if clock.game_second < policy.quote_from_second:
        return "cutoff"
    if not is_fresh(now_ns=now_ns, ts_ns=clock.now_ns, max_age_s=state.freshness.entry_stale_s):
        return "stale_signal"
    return None


def _is_outside_band(*, books: BookPair, band_hi: float) -> bool:
    for book in books.tokens:
        mid = (book.bid + book.ask) / 2.0
        if mid > band_hi or mid < 1.0 - band_hi:
            return True
    return False


def _market_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    if state.permissions.reduce_only:
        return "reduce_only"
    if not state.permissions.allow_buy:
        return "fair"
    books = state.books
    if books is None or books_unusable(state, now_ns):
        return "stale_book"
    if _is_outside_band(books=books, band_hi=policy.band_hi):
        return "band"
    if core_book_p(books=books, limits=state.limits) is None:
        return "pair_tolerance"
    return None


def two_sided_pull_reason(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> BlockReason | None:
    reason = _session_pull_reason(state=state)
    if reason is None:
        reason = _clock_pull_reason(state=state, policy=policy, now_ns=now_ns)
    if reason is None:
        reason = _market_pull_reason(state=state, policy=policy, now_ns=now_ns)
    return reason
```

The three groups exist because one function with 12 `if`s fails ruff `C901` (McCabe > 10). The order of the groups and of the checks inside each group is the spec order: halt, ownership, recovery_pending, game end, pause, second < −60, stale clock, reduce_only, allow_buy, stale book, band, pair tolerance.

```python
def _leg_target(
    *,
    state: StrategyState,
    policy: TwoSidedPolicy,
    leg_index: int,
    token_index: int,
    ticks: int,
    net_shares: float,
) -> QuoteTarget | None:
    if ticks <= 0:
        return None
    size = share_floor(
        scale_order_size(
            size_shares=policy.order_shares,
            net_shares=net_shares,
            net_max_shares=policy.net_max_shares,
            token_index=leg_index,
        )
    )
    if size < state.limits.min_order_size:
        return None
    return QuoteTarget(
        token_index=token_index,
        side="BUY",
        price=round(ticks * policy.tick, 2),
        quantity=size,
        level_index=token_index,
    )


def desired_bids(*, state: StrategyState, policy: TwoSidedPolicy) -> tuple[QuoteTarget, ...]:
    books = state.books
    fair = None if books is None else core_book_p(books=books, limits=state.limits)
    if fair is None:
        return ()
    radiant = state.limits.radiant_token_index
    dire = 1 - radiant
    net = state.inventory[radiant].qty - state.inventory[dire].qty
    skew = inventory_skew(fair=fair, net_shares=net, skew_per_share=policy.skew_per_share)
    ticks = price_bids(fair=fair, half_spread_ticks=policy.half_spread_ticks, skew=skew)
    legs = (
        _leg_target(state=state, policy=policy, leg_index=0, token_index=radiant,
                    ticks=ticks.yes_ticks, net_shares=net),
        _leg_target(state=state, policy=policy, leg_index=1, token_index=dire,
                    ticks=ticks.no_ticks, net_shares=net),
    )
    return tuple(target for target in legs if target is not None)
```

(`desired_bids` returns `()` when there is no usable pair. Inside `requote_two_sided` this cannot happen after the pull checks. The `None` check is needed for typing, and the function is public and tested on its own.)

```python
def _live_bid(*, state: StrategyState, token_index: int) -> RestingOrder | None:
    for order in state.orders:
        if (
            order.side == "BUY"
            and order.token_index == token_index
            and order.status != "gone"
            and not already_canceling(order)
        ):
            return order
    return None


def hold_one_tick_moves(
    *,
    state: StrategyState,
    policy: TwoSidedPolicy,
    targets: tuple[QuoteTarget, ...],
    now_ns: int,
) -> HeldBids:
    held: list[QuoteTarget] = []
    since: list[int | None] = [None, None]
    for target in targets:
        live = _live_bid(state=state, token_index=target.token_index)
        if live is None:
            held.append(target)
            continue
        gap = tick_gap(live_price=live.price, want_price=target.price)
        if gap == 0 or gap >= policy.reprice_now_ticks:
            held.append(target)
            continue
        started = state.reprice_since_ns[target.token_index]
        if started is None:
            started = now_ns
        if now_ns - started >= policy.reprice_hold_ns:
            held.append(target)
            continue
        since[target.token_index] = started
        held.append(replace(target, price=live.price))
    return HeldBids(targets=tuple(held), reprice_since_ns=(since[0], since[1]))


def reprice_hold_boundary_ns(*, state: StrategyState, policy: TwoSidedPolicy) -> int | None:
    starts = [start for start in state.reprice_since_ns if start is not None]
    if not starts:
        return None
    return min(starts) + policy.reprice_hold_ns


def _pull_bids(*, state: StrategyState, reason: BlockReason) -> tuple[StrategyState, Plan]:
    state = replace(state, reprice_since_ns=(None, None))
    for order in state.orders:
        state = mark_canceling(state=state, order_id=order.order_id, reason=reason)
    return state, empty_plan(reason=reason)


def _reconcile_bids(
    *, state: StrategyState, targets: tuple[QuoteTarget, ...], now_ns: int
) -> tuple[StrategyState, Plan]:
    keeps: list[KeepOrder] = []
    missing: list[QuoteTarget] = []
    for target in targets:
        live = _live_bid(state=state, token_index=target.token_index)
        if live is not None and tick_gap(live_price=live.price, want_price=target.price) == 0:
            keeps.append(KeepOrder(order_id=live.order_id, level_index=live.level_index))
        else:
            missing.append(target)
    kept = {keep.order_id for keep in keeps}
    for order in state.orders:
        if order.order_id in kept or already_canceling(order):
            continue
        state = mark_canceling(state=state, order_id=order.order_id, reason="reprice")
    return place_missing(
        state=state, missing=tuple(missing), keeps=tuple(keeps), moves=(), now_ns=now_ns
    )


def requote_two_sided(
    *, state: StrategyState, policy: TwoSidedPolicy, now_ns: int
) -> tuple[StrategyState, Plan]:
    if state.sell_only and not state.recovery_pending:
        state = replace(state, sell_only=False)
    reason = two_sided_pull_reason(state=state, policy=policy, now_ns=now_ns)
    if reason is not None:
        return _pull_bids(state=state, reason=reason)
    targets = desired_bids(state=state, policy=policy)
    held = hold_one_tick_moves(state=state, policy=policy, targets=targets, now_ns=now_ns)
    state = replace(state, reprice_since_ns=held.reprice_since_ns)
    return _reconcile_bids(state=state, targets=held.targets, now_ns=now_ns)
```

Why this is correct:
- **No SELL anywhere.** Every target is `side="BUY"`, and `place_missing` sets `reduce_only = side == "SELL"`, which is False.
- **The replacement waits for the old bid to leave.** `place_missing` skips a target while `rung_occupied(level_index=token)` is True. That covers any BUY order with that `level_index`, including ones that are `canceling`, `unknown`, or `gone`. With `level_index = token_index` there is at most one BUY per token. A new bid appears only after `CancelAck` (or, on the live path, `CancelUnsettled` → `BuySettled`) removes the old order from `state.orders`.
- **Episodes and rungs stay idle.** `episode_id` stays 0 and `rungs` stays `()`. `occupy_buy`, `_credit_rung` and `_complete_buy_order` find no rung and return early. `end_episode_if_idle` returns early when `episode_id == 0`.
- **Engine cancels.** `engine.step` rebuilds `plan.cancels` from orders that newly became `canceling` (`_cancels_opened`). `_pull_bids` and `_reconcile_bids` only call `mark_canceling`.
- **Budget.** `place_missing` checks cash, map cap and account cap per BUY and reports the tightest one as `block_reason` when nothing could be placed.

### 7. `src/strategy/scheduling.py` (D2)

- Import `Policy, TwoSidedPolicy` from `strategy.policy` (instead of only `Follow300Policy`), and `reprice_hold_boundary_ns` from `strategy.two_sided_quoting`. No cycle: `two_sided_quoting` does not import `scheduling`.
- `debounce_ns(policy: Policy)`, `fallback_ns(policy: Policy)`, `armed_deadline_ns(..., policy: Policy, ...)`, `next_wake_ns(..., policy: Policy, ...)`, `should_evaluate(..., policy: Policy)`. Both policies have `debounce_ms` and `fallback_timer_s`.
- Split the cadence part out, so the Follow300 body stays the same and McCabe does not grow:

```python
def _cadence_deadline_ns(*, state: StrategyState, policy: Policy, now_ns: int) -> int:
    schedule = state.schedule
    dirty = schedule.dirty_open_ns
    if dirty is not None:
        return dirty + debounce_ns(policy)
    origin = schedule.last_eval_ns if schedule.last_eval_ns > 0 else now_ns
    return origin + fallback_ns(policy)


def _hold_deadline_ns(
    *, state: StrategyState, policy: TwoSidedPolicy, deadline: int, now_ns: int
) -> int:
    hold = reprice_hold_boundary_ns(state=state, policy=policy)
    if hold is None:
        return deadline
    if hold <= now_ns:
        return now_ns
    return min(deadline, hold)


def armed_deadline_ns(*, state: StrategyState, policy: Policy, now_ns: int) -> int:
    deadline = _cadence_deadline_ns(state=state, policy=policy, now_ns=now_ns)
    if isinstance(policy, TwoSidedPolicy):
        return _hold_deadline_ns(state=state, policy=policy, deadline=deadline, now_ns=now_ns)
    boundaries = (...)        # unchanged Follow300 code from here on
```

Leave the existing comments in the Follow300 part as they are (do not add new ones). `note_schedule`, `mark_evaluated` and `entry_stale_boundary_ns` do not change. For two-sided, a stale book or clock with no new events is caught on the fallback Wake (`fallback_timer_s`). Live also forces a Wake every quote cycle.

### 8. `src/strategy/engine.py` (D1)

- Import `Policy, TwoSidedPolicy` (drop the `Follow300Policy` import if it is no longer used), `requote_two_sided` from `strategy.two_sided_quoting`, and `Plan` from `strategy.types`.
- Add:

```python
def _requote(
    *, state: StrategyState, policy: Policy, now_ns: int
) -> tuple[StrategyState, Plan]:
    if isinstance(policy, TwoSidedPolicy):
        return requote_two_sided(state=state, policy=policy, now_ns=now_ns)
    return requote(state=state, policy=policy, now_ns=now_ns)
```

- `step(*, state, policy: Policy, event)`: replace `requote(...)` with `_requote(...)`. Nothing else changes.

Existing callers of `step`, `next_wake_ns` and so on (backtest/strategy.py, replay_core_trace, session_core, core_state_report) pass `Follow300Policy`, which still fits `Policy`. No edits there.

### 9. Tests: append to `tests/test_strategy_two_sided.py`

Write the helpers in this file. Do not import the private helpers from `test_strategy_core.py`; that would need a `# pyright: reportPrivateUsage=false` comment. No lambdas in `parametrize`: strict basedpyright reports untyped lambda parameters. Use a module-level dict of events instead.

Helpers (module level):

```python
NS = 1_000_000_000
T0 = 10 * NS
T1 = T0 + 1_000_000
POLICY = two_sided_policy(debounce_ms=100, fallback_timer_s=1.0)
PERMISSIONS = Permissions(halt=False, reduce_only=False, allow_buy=True, allow_sell=True, sell_unconfirmed=False)

def _books(*, bid0: float, ask0: float, bid1: float, ask1: float, ts_ns: int) -> BookPair: ...
def _even_books(*, ts_ns: int) -> BookPair:   # 0.49/0.51 on both tokens -> fair 0.50
def _idle(*, radiant_index: int) -> StrategyState:
    # empty_state with MarketLimits(min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.05,
    # radiant_token_index=radiant_index), FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0,
    # exit_stale_s=45.0), PERMISSIONS, Budget(cash_usdc=1000.0, cap_room_usdc=1000.0,
    # account_cap_room_usdc=1000.0), GameClock(now_ns=T0, game_second=100, paused=False, game_ended=False)
def _run(*, state: StrategyState, events: tuple[InboundEvent, ...]) -> list[EngineOutput]:
    # step each event with POLICY, threading state
def _quoting(*, radiant_index: int) -> StrategyState:
    # BookUpdate(T0, _even_books(ts_ns=T0)), Wake(T0, forced=True), then OrderAccepted(T0, id) for each place
def _bid_ids(state: StrategyState) -> dict[int, str]:
    # {order.token_index: order.order_id for BUY orders not canceling}
```

Expected numbers, all checked against `strategy.two_sided`. Fair 0.50, h=3, g0=2e-4, cap 50, size 20:

| fair | net (Radiant − Dire) | Radiant bid | Dire bid | Radiant size | Dire size |
|---|---|---|---|---|---|
| 0.50 | 0 | 0.47 | 0.47 | 20 | 20 |
| 0.50 | 20 | 0.47 | 0.47 | 12 | 20 |
| 0.50 | 30 | 0.46 | 0.48 | 8 | 20 |
| 0.50 | 32 | 0.46 | 0.48 | 7.2 | 20 |
| 0.50 | 37.5 | 0.46 | 0.48 | 5.0 | 20 |
| 0.50 | 45 / 50 | (none: 2.0 / 0.0 < 5) | 0.48 | — | 20 |
| 0.51 | 0 | 0.48 | 0.46 | 20 | 20 |
| 0.52 | 0 | 0.49 | 0.45 | 20 | 20 |

Books for these fairs (Radiant = token 0): fair 0.51 → token0 0.50/0.52 and token1 0.48/0.50. Fair 0.52 → token0 0.51/0.53 and token1 0.47/0.49.

Tests to add. Each item is one test function; the first two are parametrized.

1. **`test_first_quote_places_two_post_only_buys`**: `_quoting(radiant_index=0)`, taking the plan of the `Wake(T0)` step. `{(p.token_index, p.level_index, p.side, p.price, p.quantity, p.reduce_only)}` equals `{(0, 0, "BUY", 0.47, 20.0, False), (1, 1, "BUY", 0.47, 20.0, False)}`, and `block_reason == ""`. (This is the feature's "plan holds two BUYs with level_index 0 and 1".)
2. **`test_skew_leans_against_inventory`**, parametrized over `radiant_index in (0, 1)`. Set the state to `replace(_quoting(...), inventory=...)` with Radiant qty 30 and Dire qty 0, then call `desired_bids`. Radiant target 0.46 × 8.0, Dire target 0.48 × 20.0, each with `level_index == token_index`. This proves the direction is right in both orientations.
3. **`test_fill_shrinks_the_side_that_grew`**, parametrized over `radiant_index in (0, 1)`. From `_quoting`: `Fill(T1, fill_id="f1", order_id=<radiant id>, qty=20.0, price=0.47, token_index=radiant, side="BUY")`, then `Wake(T1, forced=True)`. The plan places exactly one BUY, on the Radiant token, at 0.47 × 12.0 (smaller than 20). The Dire bid is kept (it is in `plan.keep`, with quantity 20). No cancels. This is the feature's "after a fill the imbalance shrinks, not grows".
4. **`test_net_cap_shrinks_the_heavy_side_to_zero`**, parametrized over net in `(0, 20.0), (10, 16.0), (25, 10.0), (37.5, 5.0), (45, None), (50, None)` (the Radiant size; `None` means no Radiant target), with `radiant_index=0` and inventory Radiant = net, Dire = 0. Call `desired_bids`. The Dire target is always 20.0. At `NET_MAX_SHARES` there is no Radiant bid.
5. **`test_each_pull_reason_cancels_both_bids`**, parametrized over `PULL_EVENTS: dict[BlockReason, tuple[InboundEvent, ...]]`, all starting from `_quoting(radiant_index=0)`. `WAKE = Wake(now_ns=T1, forced=True)`.
   - `"halt"`: `PermissionsUpdate(T1, replace(PERMISSIONS, halt=True))`, WAKE
   - `"ownership_unresolved"`: `Fill(T1, fill_id="stray", order_id="venue-stray", qty=5.0, price=0.47, token_index=0, side="BUY")`, WAKE
   - `"recovery"`: `Recovery(T1, restored_buy_ids=())`, WAKE
   - `"game_end"`: `ClockUpdate(T1, GameClock(T1, 100, paused=False, game_ended=True))`, WAKE
   - `"paused"`: `ClockUpdate(T1, GameClock(T1, 100, paused=True, game_ended=False))`, WAKE
   - `"cutoff"`: `ClockUpdate(T1, GameClock(T1, -61, False, False))`, WAKE
   - `"stale_signal"`: only `Wake(now_ns=T0 + 16 * NS + 1, forced=False)`. No new events; books are stale too, so this also checks that the clock comes first.
   - `"reduce_only"`: `PermissionsUpdate(T1, replace(PERMISSIONS, reduce_only=True))`, WAKE
   - `"fair"`: `PermissionsUpdate(T1, replace(PERMISSIONS, allow_buy=False))`, WAKE
   - `"stale_book"`: only `Wake(now_ns=T0 + 5 * NS + 1, forced=False)`. No new events.
   - `"band"`: `BookUpdate(T1, _books(bid0=0.91, ask0=0.93, bid1=0.07, ask1=0.09, ts_ns=T1))`, WAKE
   - `"pair_tolerance"`: `BookUpdate(T1, _books(bid0=0.55, ask0=0.57, bid1=0.53, ask1=0.55, ts_ns=T1))`, WAKE (mids 0.56 + 0.54 = 1.10: both inside the band, pair broken)

   Assert: `{c.order_id: c.reason for out in outputs for c in out.plan.cancels} == {radiant_id: reason, dire_id: reason}`. The last output has `block_reason == reason` and `places == ()`. Every order in the final state is canceling, with `cancel_reason == reason`. The `forced=False` cases evaluate because `OrderAccepted` opened the debounce window at T0.
6. **`test_one_tick_move_waits_then_replaces_after_ack`**, from `_quoting(radiant_index=0)`:
   - `t1 = T0 + NS`. `BookUpdate(t1, fair-0.51 books, ts t1)`, then `Wake(t1, forced=True)`. No cancels, no places, both kept. `state.reprice_since_ns == (t1, t1)`. `out.next_wake_ns == t1 + REPRICE_HOLD_NS`.
   - `Wake(t1 + REPRICE_HOLD_NS - 1_000_000, forced=True)`: still no cancels.
   - `Wake(t1 + REPRICE_HOLD_NS, forced=False)`: it evaluates, because the hold boundary is the armed deadline. Both bids are cancelled with reason `"reprice"`, `places == ()`, and `reprice_since_ns == (None, None)`.
   - `CancelAck(radiant id)` → exactly one place: token 0 at 0.48 × 20. Then `CancelAck(dire id)` → exactly one place: token 1 at 0.46 × 20.
7. **`test_two_tick_move_cancels_at_once_and_waits_for_settle`**: from `_quoting(radiant_index=0)`, `BookUpdate(t1, fair-0.52 books)`, then `Wake(t1, forced=True)`. Both bids are cancelled with `"reprice"` immediately, `places == ()`, and `reprice_since_ns == (None, None)`. Then `CancelAck(radiant)` → place token 0 at 0.49. For Dire, use the live path: `CancelUnsettled(dire)` → no place; then `BuySettled(dire, matched_qty=0.0)` → place token 1 at 0.45.
8. **`test_recovery_chain_keeps_quoting_both_sides`**, from `_idle(radiant_index=0)` with books `_even_books(ts_ns=T0)` (use a BookUpdate):
   - `Recovery(T0, ())`: block `"recovery"`, no places.
   - `RecoveryVerified(T0, generation=1, inventory=(qty 30 on token 0, qty 10 on token 1))`: two BUYs, token 0 at 0.47 × 12.0 and token 1 at 0.47 × 20.0. `state.sell_only is False` and `recovery_pending is False`.
   - `OrderAccepted` for both, then a full `Fill` of the token-0 bid (12 @ 0.47), then `Wake(T1, forced=True)`: one new BUY on token 0 at 0.46 × 7.2. The token-1 bid is kept: it is 1 tick from 0.48, so it is held. The state has non-canceling BUYs on both tokens. `recovery_pending is False`, `sell_only is False`, `recovery_generation == 1`.
   - Over every output in the chain, `all(p.side == "BUY" for p in out.plan.places)`.
9. **`test_two_sided_deadline_ignores_follow300_boundaries`**: `state = replace(_quoting(radiant_index=0), signal=RawDeltaSignal(predicted_delta=0.05, received_ns=0, source_received_ns=0, anchor_p=0.5, deaths_radiant=0, deaths_dire=0))`, `now = T0 + 20 * NS`. `armed_deadline_ns(state=..., policy=follow300_policy(level_usdc=20.0, debounce_ms=100, fallback_timer_s=1.0), now_ns=now) == now` (Follow300's entry-stale boundary). `armed_deadline_ns(state=..., policy=POLICY, now_ns=now) == T0 + debounce_ns(POLICY)`.

Do not add tests for the checkpoint codec or the trace codec. Neither changes, and `tests/test_trader_core_persistence.py` and `tests/test_core_trace.py` already prove that Follow300 checkpoints and traces decode. Run them (see Verification).

## Edge cases covered by the design

- **Partial fill of a bid** (`partially_filled`, inventory under 5 shares): a later reprice still cancels it (decision 4).
- **A fill on a bid during a 1-tick hold**: the next evaluation finds no live bid (after a full fill) and the hold resets. A partial fill keeps the order live, and the hold timing continues.
- **A target disappears** (size under minimum at the net cap): the live bid on that token is cancelled with `"reprice"` and is not re-placed while the size stays under the minimum.
- **`gone` BUY** (live cancel before `BuySettled`): not a live bid, not cancelled again, and it keeps its token slot until `BuySettled`.
- **Initial clock `now_ns=0`**: stale, so there are no bids until the first feed tick.
- **Fill while `recovery_pending`**: `sell_only` is still True, so `_reenter_recovery` bumps the generation (decision 13).
- **Restored BUYs in `Recovery`**: `apply_recovery` already marks them canceling; the pull adds nothing.
- **A float exactly on a band edge**: the same expression as the backtest (`mid > band_hi or mid < 1.0 - band_hi`).

## Not in this step (handoff to STEP-004/006)

- `LiveCore(policy=...)` and `LiveCore.policy` widen to `Policy` in STEP-006. That also needs `export_checkpoint`/`restore_checkpoint` to stop reading `sell_min_life_s` from a `TwoSidedPolicy` (use 0.0 for two-sided, which has no SELL), `session_budget.budget_from_orders` to get B's per-map cap without `policy.level_usdc`, and `dust_sweep` to stay Follow300-only (B uses PairMerger).
- `entry_block_from_reason("band")` maps to `EntryBlock.NO_EDGE`. Add `EntryBlock.BAND` only if the B journal needs it.
- After a B restart with a clean core, late fills of old venue orders arrive as unknown fills and pull both bids (`ownership_unresolved`) until resolved. Check this in STEP-006's restart test.
- In live, a cancelled BUY becomes `gone` and waits for `BuySettled` before it is replaced. The gap between bids is therefore the cancel round trip plus the WS terminal proof, not only the cancel latency. Watch it on the first map.

## Do not touch

`src/trader/**` (including `session_core.py`, `core_persistence.py`, `core_trace_codec.py`), `config/**`, `compose.yaml`, the `tests/test_strategy_core.py` and `tests/test_follow300_replay.py` files, any other existing test file, and `../poly-maker`. Do not stage the untracked `data/backtests/dota_maker/validation_*ts-regress-*` directories.

## Verification (in E)

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# step tests + A regression (feature list)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_two_sided.py tests/test_strategy_core.py tests/test_follow300_replay.py \
  tests/test_core_trace.py -q

# other users of the strategy core and of the renamed helpers
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_scheduling.py tests/test_strategy_recovery_quotes.py \
  tests/test_strategy_late_fills.py tests/test_strategy_budget.py tests/test_strategy_imports.py \
  tests/test_kill_gate.py tests/test_mid_spike.py tests/test_trader_core_recovery.py \
  tests/test_trader_session_core.py tests/test_trader_core_persistence.py \
  tests/test_trader_core_state_report.py tests/test_trader_engine_seams.py -q

# backtest self-check still prints "two_sided ok" (tick_gap moved)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided

# A's test files are untouched
git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py

# typecheck (strict, whole project) and lint on staged files
uv run python -m basedpyright
git add src/strategy/types.py src/strategy/lifecycle.py src/strategy/two_sided.py \
  src/strategy/two_sided_quoting.py src/strategy/quoting.py src/strategy/scheduling.py \
  src/strategy/engine.py src/backtest/two_sided_strategy.py tests/test_strategy_two_sided.py
make lint
```

Expected: all green, `two_sided ok`, and empty `git diff` output for the two A test files. If `make lint` reformats files (ruff `--fix`/format), re-stage them and run it again. `test_follow300_replay.py` replays 8 maps and is the slowest test; leave `PYTEST_N` unset. A full serial suite run is STEP-010's job, but run it here as well if time allows.

Then, per the implement skill: one commit in E on `main` (suggested message: `Quote two-sided bids in the strategy core behind TwoSidedPolicy.`), set `passes: true` for STEP-002 in `W/tasks/two-sided-live-b/feature.json`, and append to `W/tasks/two-sided-live-b/progress.txt`. Note decisions 1, 4 and 5 there, because they differ from the feature text. No push.
