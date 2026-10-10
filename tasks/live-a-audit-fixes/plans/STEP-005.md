# STEP-005 plan: RISK-01 run protective cancels when the YES book is bad

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, HEAD `21e3e890` (STEP-001..004 done). Code paths below are relative to that worktree. The fork is the editable checkout `../poly-maker/src/polymaker/` (frozen, read-only). `engine.py` there is byte-identical to the audit copy `evidence/deployed-polymaker/src/polymaker/engine.py` (`diff -q` gives no output). Planner probe and outputs: `betting_workspace/tasks/live-a-audit-fixes/orchestrate/work/plan-step-005/`.

No Figma links: the step has no `designReference`.

## 0. Result in one paragraph

The fork `Engine._recompute_locked` returns before the strategy when the YES book is missing, one-sided, empty, or crossed (`poly-maker/src/polymaker/engine.py:369-375`). Our core then never runs a cycle, so queued pause, cutoff, kill, stale, halt, and finish events never become cancels. The fix is one seam in our integration. When the YES book fails that same check, a wrapper around `engine._recompute_locked` runs the core cycle with `books=None` instead of calling the fork. It then sends only the planned cancels through the normal wrapped `engine.gateway.cancel`. It never calls `place`. `books=None` is the core's existing "no usable book" input: the core blocks every new place with `stale_book` and pulls orders by its existing policy. No fork, strategy, schema, timer, or tolerance change. The wrapper is installed only for `follow300` (wallet A). B (`two_sided`) keeps today's path. Size: about +80/−15 production lines in 2 files, 1 new test file, 1 updated test file. The probe on the real fork `Engine` with a real `LiveCore` shows 0 cancels on HEAD and the planned cancel with 0 places after the fix, in all 10 bad-book cases.

## 1. What is broken today (evidence)

Fork code (read-only):

- `engine.py:369-370`: `if yes_book is None or yes_book.is_empty: return`. `is_empty` is `len(bids) == 0 or len(asks) == 0` (`marketdata/orderbook.py:99-101`).
- `engine.py:373-375`: `if bb is None or ba is None or bb.price >= ba.price: return` (crossed or locked).
- `engine.py:378-380`: `micro is None` returns too. This branch cannot fire after the two checks above. `microprice` returns None only when a side is empty (`orderbook.py:117-127`), and `apply_snapshot` / `apply_delta` drop levels with size `<= 0` (`orderbook.py:81-82`, `:89-90`). The guard does not copy it.
- Only after the guard does the fork call `construct_quotes` (`engine.py:447`) and `reconcile` (`engine.py:456`). It then sends `plan.to_cancel` (`engine.py:462-472`). On `ok` it removes the ids from `self.state`. On failure it calls `_refresh_token_orders(meta, grace_s=10.0)`, sets `_dirty[cid]`, and returns.

Our wiring:

- `construct_quotes` is `quotes_adapter` (`src/trader/host_resources.py:73-120`). It calls `quote_cycle`, and that function drains the core and runs `finish_cycle` (`src/trader/session_core.py:955-986`). `reconcile` is `esports_reconcile` (`src/trader/session_core.py:1014-1035`). It is the only path that turns core cancels into `gateway.cancel` calls. So when the fork skips, the queued events wait in `core._pending`, and the venue order stays live.
- The NO book has no fork guard. A bad NO book with a valid YES book reaches the core as `books=None` through `books_from_md` (`src/trader/book_strip.py:40-65`). The core then cancels with `stale_book`. The audit repro shows this (`work/orchestrator/guard-independent.txt`, case D).

Reproduction on the current HEAD (probe `probe_guard.py` with `NO_GUARD=1`, output `probe-head.txt`). Setup: real fork `Engine(cfg, paper=False)`, real `LiveCore` (follow300, 3 BUY rungs placed through the real adapter and reconcile), stub gateway:

| case | cancels sent | places |
| --- | --- | --- |
| YES asks empty / bids empty / both empty / crossed | 0 / 0 / 0 / 0 | 0 |
| bad YES + pause / finished / cutoff / kill / stale / halt | 0 in all 6 | 0 |
| bad NO, valid YES (fork path) | 1 (`stale_book`) | 0 |

Audit scope (`reports/a2-dev-guards.md` F1): 163 bad-book segments in 88 of 104 sessions, about 2,453 signal-seconds, 0 fills inside. The independent cancel paths stay as they are: feed watchdog, sidecar, finish quiesce, Gamma close, shutdown, and the venue dead-man.

## 2. Design decisions

1. **Where.** The fix wraps `engine._recompute_locked` per instance. `_recompute` calls `self._recompute_locked(cid)` under the per-market lock (`engine.py:357-362`), so an instance attribute takes over. The code already does this in two places: `engine._next_wake_s` (`src/trader/engine_seams.py:1397-1420`) and `wrap_recompute_retire` (`src/trader/core_execution.py:286-298`, installed by `src/trader/wallet_host.py:883-901`). The fork stays frozen.
2. **Same predicate as the fork.** The wrapper checks the YES book with the exact fork conditions (`None`, `is_empty`, `bid >= ask`) before it calls anything. No `await` runs between the check and the call to the original, so both read the same book object. When the check passes, the original runs unchanged.
3. **`books=None`, not a synthetic book.** On a bad YES book the core gets `books=None`. Every follow300 requote path then ends in `_blocked(...)` or `empty_plan(...)`: `halt`, `paused`, and `game_end` at `src/strategy/quoting.py:932-937`, `stale_book` at `:877` and `:899`, and `cutoff` at `:875`. So the core plans no BUY and no SELL. `finish_cycle` drops a place whose order is no longer `pending` (`canceled_mid_cycle`, `src/trader/session_core.py:703-711`). The wrapper also reads only `to_cancel` and never calls `gateway.place`. This keeps "protective transitions" apart from "admission of new quotes": the transitions run, and admission stays blocked until the YES book is valid again.
4. **Regime on the bad-book cycle.** The fork computes no regime on a bad book: `regime_m.decide` needs `fv` (`engine.py:437-445`). The core uses the regime only for `Permissions.halt` and `reduce_only` (`permissions_from_quote`, `src/trader/session_core.py:239-255`). With `books=None`, `halt` and `stale_book` cancel the same set: every order (`_blocked` → `_cancel_orders(buys_only=False)`, `src/strategy/quoting.py:88-105`). So the wrapper passes `Regime.QUIET`, and the core reports the real block (`paused`, `game_end`, `cutoff`, `stale_book`). `Regime.HALTED` would relabel every cancel as `halt` and hide the source event. Fork halts (`_halted`, WS stale, heartbeat blind, risk) are not recomputed on a bad book. They apply again at the first cycle on a valid book.
5. **Dispatch through existing routes.** The wrapper calls `engine.gateway.cancel(order_ids)`. That call is the latch wrapper, then `_durable_cancel`, then strict REST (`src/trader/wallet_host.py:803-806`, `:546-570`). So the command rows, `note_cancel_result`, `CancelUnsettled` / `CancelAck` / `CancelTimeout`, the quote journal, and the STEP-001 venue diagnostics all apply as on the normal path. The `ok` and failure handling copies the fork (`engine.py:462-472`).
6. **No repeat of a finished cancel.** `_venue_cancels` resends only orders in `already_canceling` (`src/trader/session_core.py:862-880`). After `ok`, a BUY becomes `gone` and `mark_canceling` skips `gone` (`src/strategy/lifecycle.py:310-313`). A SELL is removed. After a failure, `CancelTimeout` keeps the order `canceling`, so the next cycle resends it. That is the intended retry.
7. **Scope gate.** The wrapper is installed only when `strategy == "follow300"`, next to `install_core_quoter_wake` in `open_wallet_host`. `two_sided` (B) keeps the fork early return, so B behavior does not change. The `install_core_quoter_wake` docstring ("The fork skips the core on a one-sided book…") stays true for B, so leave it as is.
8. **Order of wrappers.** `open_wallet_host` installs the guard before `WalletHost(...)`. `WalletHost.__init__` → `_install_runtime_seams` → `_wrap_unsent_places` then wraps the guard from outside. So `retire_unsent` still runs in its `finally` after a guard cycle.
9. **Diagnostic.** The wrapper writes one `log_event("book_guard", ...)` line, and only when it sends a cancel. Fields: `cid`, `guard` (`missing` / `empty` / `crossed`), `block` (`core.last_block`), `reasons` (sorted `cancel_reason` of the orders in `already_canceling`, read before the cancel), `order_ids`, `ok`. `reasons` keeps the source event when `block` does not. Example: kill gives `block="stale_book"` and `reasons=["kill"]`. No line is written on a bad-book tick with nothing to cancel, so a long dead-book tail adds no lines.

## 3. Changes

### 3.1 `src/trader/session_core.py`: split the cycle out of `quote_cycle`

Add `run_core_cycle` with the current body of `quote_cycle` minus `target_quotes`. Make `quote_cycle` call it. The `quote_cycle` signature stays the same, so its 5 test callers do not change (`tests/test_trader_shared_budget.py:147`, `tests/test_backtest_two_sided_core.py:328`, `tests/adapter_contract_fixtures.py:679`, `tests/test_trader_two_sided_worker.py:194`, and the adapter).

```python
def run_core_cycle(
    core: LiveCore,
    *,
    regime: Regime,
    yes: str,
    no: str,
    now_ns: int,
    books: BookPair | None,
    clock: GameClock,
    limits: MarketLimits,
    store: WalletStateStore,
    cache: CollateralCache,
    cores: Iterable[LiveCore],
    account_cap_usdc: float,
) -> None:
    core.clear_plan()
    core.drain_apply()
    sources = live_sources(
        now_ns=now_ns,
        books=books,
        clock=clock,
        limits=limits,
        regime=regime,
        store=store,
        cache=cache,
        cores=cores,
        quoting=core,
        account_cap_usdc=account_cap_usdc,
        sidecar_usable=core.sidecar_usable,
        yes=yes,
        no=no,
    )
    core.finish_cycle(sources)


def quote_cycle(core, inp, *, now_ns, books, clock, limits, store, cache, cores, account_cap_usdc) -> TargetQuotes:
    run_core_cycle(
        core,
        regime=inp.regime,
        yes=inp.meta.yes.token_id,
        no=inp.meta.no.token_id,
        now_ns=now_ns, books=books, clock=clock, limits=limits,
        store=store, cache=cache, cores=cores, account_cap_usdc=account_cap_usdc,
    )
    return core.target_quotes(inp)
```

Keep the existing `quote_cycle` annotations. The sketch shortens them. Keep the cycle order exactly: `clear_plan`, then `drain_apply`, then `live_sources`, then `finish_cycle`. Do not reuse `LiveCore.run_cycle`: it builds `sources` before `drain_apply`.

### 3.2 `src/trader/host_resources.py`: the guard

1. Extract `_market_limits(core: LiveCore, meta: MarketMeta) -> MarketLimits` from `quotes_adapter` (`:105-110`) and use it there.
2. Add `_check_yes_book(book: OrderBook | None) -> str | None`. It copies `engine.py:369-375`:

```python
def _check_yes_book(book: OrderBook | None) -> str | None:
    if book is None:
        return "missing"
    if book.is_empty:
        return "empty"
    bid = book.best_bid()
    ask = book.best_ask()
    if bid is None or ask is None or bid.price >= ask.price:
        return "crossed"
    return None
```

3. Add `_install_book_guard`:

```python
def _install_book_guard(
    engine: Engine,
    cores: dict[str, LiveCore],
    cache: CollateralCache,
    store: WalletStateStore,
    account_cap_usdc: float,
) -> None:
    original = engine._recompute_locked

    async def recompute_locked(cid: str) -> None:
        meta = engine.metas[cid]
        fault = _check_yes_book(engine.md.book(meta.yes.token_id))
        core = cores.get(cid)
        if fault is None or core is None:
            await original(cid)
            return
        run_core_cycle(
            core,
            regime=Regime.QUIET,
            yes=meta.yes.token_id,
            no=meta.no.token_id,
            now_ns=core_now_ns(),
            books=None,
            clock=core.latest_clock,
            limits=_market_limits(core, meta),
            store=store,
            cache=cache,
            cores=cores.values(),
            account_cap_usdc=account_cap_usdc,
        )
        planned = core.take_plan()
        if planned is None or not planned.to_cancel:
            return
        order_ids = list(planned.to_cancel)
        reasons = sorted(
            {order.cancel_reason for order in core.state.orders if already_canceling(order)}
        )
        ok = await engine.gateway.cancel(order_ids)
        log_event(
            "book_guard",
            cid=cid,
            guard=fault,
            block=core.last_block,
            reasons=reasons,
            order_ids=order_ids,
            ok=ok,
        )
        if ok:
            for order_id in order_ids:
                engine.state.remove_order(order_id)
            return
        await engine._refresh_token_orders(meta, grace_s=10.0)
        engine._dirty[cid].set()

    engine._recompute_locked = recompute_locked
```

`take_plan()` is never None after `finish_cycle`, because `stash` always sets `_planned` (`session_core.py:825-843`). The `planned is None` check only narrows the type. It shares one `if` with the empty case and adds no new path.

4. In `open_wallet_host`, after `install_core_quoter_wake(engine, cores)` (`:211`):

```python
        if strategy == "follow300":
            _install_book_guard(engine, cores, budget_cache, state, template.account_cap_usdc)
```

New imports: `Regime` and `MarketMeta` from `polymaker.domain`, `OrderBook` from `polymaker.marketdata.orderbook`, `already_canceling` from `strategy.lifecycle`, `log_event` from `trader.diag`, `run_core_cycle` from `trader.session_core`. The file already has `# pyright: reportPrivateUsage=false`. Write no code comments (review rule in `.feature-json.config.json`).

### 3.3 Not changed

- `../poly-maker` (frozen). The strategy (`src/strategy/*`). The `stale_book` policy. `match_worker.py`. `wallet_host.py`. `engine_seams.py`. Persistence and schema. Config.
- B (`two_sided`): no install. Paper: dead, but it gets the same guard under follow300, which is harmless.

## 4. Tests

### 4.1 New `tests/test_trader_book_guard.py`

Header `# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false`. Basedpyright needs it for the fork private seams (STEP-004 learning). Build the harness from the probe `orchestrate/work/plan-step-005/probe_guard.py`:

- Real `Engine(Config(paths=PathsConfig(db=…, journal_dir=…), profiles={"p": StrategyProfile()}, markets=[]), paper=False)` with `cfg.secrets.pk = "0x" + "00" * 32`. Seed each market with `seed_real_engine` from `tests/trader_session_fixtures.py`. `set_markets` uses `setdefault` for books, so a second market keeps the first market's books.
- Real `LiveCore`: `follow300_policy(level_usdc=65.0, debounce_ms=100, fallback_timer_s=2.0)`, the limits and freshness of `tests/test_trader_session_core.py:82-89`. A separate `WalletStateStore(tmp_path / "wallet.db")` as `store`. `CollateralCache(value=10_000.0)`.
- `monkeypatch.setattr(polymaker_engine, "construct_quotes", _bind_quotes_adapter(engine, cores, cache, store, "live", 1e9))` and `monkeypatch.setattr(polymaker_engine, "reconcile", make_esports_reconcile(cores))`. The monkeypatch restores the fork globals after each test. Then `_install_book_guard(engine, cores, cache, store, 1e9)`.
- Stub gateway. `place` returns `OpenOrder("v<n>", …)` per quote, then calls `core.note_placed(placed, core_now_ns())` and `core.drain_apply()`, like `_finish_place`. `cancel` records the ids, pops an `ok` from a queue (default True), then calls `core.note_cancel(ids, ok, core_now_ns())` and `core.drain_apply()` for the owning core, like `_finish_cancel`. Also `open_orders() -> []`, `cancel_asset -> True`, `order_pressure = 0.0`, `heartbeat_failures = 0`.
- Books: valid YES `[(0.50, 100.0)]` / `[(0.52, 100.0)]`, NO `[(0.48, 100.0)]` / `[(0.50, 100.0)]`. Signal through `feed_core(..., signal=RawDeltaSignal(predicted_delta=0.05, anchor_p=0.51, received_ns=now, source_received_ns=now, deaths 0/0))` with `now = core_now_ns()`. One `await engine._recompute(cid)` on valid books places 3 YES BUYs (`v1..v3`). That covers "normal YES".
- Capture diagnostics with `monkeypatch.setattr("trader.host_resources.log_event", capture)` and the `CapturedEvent` pattern of `tests/test_trader_buy_terminal_proof.py:59-72`.

Cases (expected values come from `probe-with-guard.txt`):

1. `test_bad_yes_book_cancels_resting_buys`, parametrized `asks_empty`, `bids_empty`, `both_empty`, `crossed`. After the seed, set the bad YES book and run 2 ticks. Expect `cancels == [["v1","v2","v3"]]` (one send over 2 ticks), no new place, and one `book_guard` event with `guard` `empty` (or `crossed`), `block="stale_book"`, `reasons=["stale_book"]`, `ok=True`.
2. `test_bad_no_book_keeps_the_fork_path`: valid YES, NO asks empty. The fork path sends `[["v1","v2","v3"]]`, `core.last_block == "stale_book"`, and the wrapper writes no `book_guard` event.
3. `test_protective_events_on_bad_yes_book`, parametrized on a YES book with empty asks. Expect one cancel of v1..v3, no place, and this `(block, reasons)`:
   - pause (`feed_core(paused=True)`) → `("paused", ["paused"])`
   - finished (`finished=True`) → `("game_end", ["game_end"])`
   - cutoff (`second=10_000`, flat) → `("cutoff", ["cutoff"])`
   - kill (`KillGateUpdate` with `radiant=KillWait(awaited_deaths=1, until_ns=now+10s)`, signal deaths 0) → `("stale_book", ["kill"])`
   - stale (signal `source_received_ns` 60 s old) → `("stale_book", ["stale_book"])`
   - halt (`engine._halted.add(cid)`) → `("stale_book", ["stale_book"])`
4. `test_failed_guard_cancel_retries_once_more`: cancel results `[False, True]`, 4 ticks on a bad book. Expect 2 identical sends, and `engine._dirty[cid].is_set()` after the failed one.
5. `test_valid_yes_book_after_bad_book_places_again`: no seed. Bad YES and a fresh signal give no place, no cancel, and `last_block == "stale_book"`. Then valid YES gives 3 BUY places.
6. `test_bad_book_on_one_map_leaves_other_map_quoting`: markets `a` (seeded, bad YES) and `b` (valid). `await asyncio.gather(engine._recompute("a"), engine._recompute("b"))`. Expect `a` cancelled and `b` places 3 BUYs.

### 4.2 `tests/test_trader_host_resources.py`

- The fake `Eng` (`:61-71`) gets `async def _recompute_locked(self, _cid: str) -> None: return None`, because the follow300 install reads it. Record each `Eng` in a new `ResourceLog.engines: list[object]`.
- `test_live_follow300_writes_process_start`: add `assert "_recompute_locked" in vars(log.engines[0])`.
- `test_two_sided_leaves_http_hooks_and_diag_root_alone`: add `assert "_recompute_locked" not in vars(log.engines[0])`.

The new test file imports `_install_book_guard`, so it cannot run on HEAD. The HEAD baseline is the planner probe with the guard off. Rerun it if needed:

```
W=../betting_workspace/tasks/live-a-audit-fixes/orchestrate/work/plan-step-005
NO_GUARD=1 PYTHONPATH=src:scripts:tests uv run --no-sync python $W/probe_guard.py $W/tmp-noguard
```

Expect 0 cancels in every bad-YES row and 1 cancel in the bad-NO row (`probe-head.txt`).

## 5. Behavior changes and risks

1. **SELLs on a bad YES book.** The core's `stale_book` policy cancels every order, SELLs included (`_blocked` default `buys_only=False`, `src/strategy/quoting.py:100-105`, `:877`, `:899`). After this step a resting exit SELL is pulled on a bad YES book and placed again when the book is valid. HEAD already does this on a bad NO book, and when the strip leaves no foreign level (`book_strip.py:63-64`). The audit's follow-up idea assumed that `stale_book` keeps SELLs. The code shows it does not. Keeping SELLs needs a core policy change for both books. That is out of scope and is not planned.
2. **More cancels and re-places.** Each bad-YES segment now starts with a cancel of every resting order, and the next cycle on a valid book places them again (163 segments in Oct 5–9). This uses the same rate path as a bad NO book. The core trace now records cycles in those windows, about 5 events per tick, the same as a normal tick. Before, it recorded nothing.
3. **Halt on a bad book** is not recomputed (decision 4). The cancel set is the same. The label is `stale_book`.
4. **A core exception on the guard path** goes to the fork `_quoter`: `quoter_error`, then a retry after 0.5 s (`engine.py:336-339`). It does not fail closed like `quotes_adapter` (`host_resources.py:116-118`, where an empty plan makes `esports_reconcile` cancel everything). This is a deliberate simplification. Tests 1 and 3 run the `books=None` path in every protective state. Upgrade path: copy the adapter `try/except` and cancel `engine.state.orders_for(yes) + orders_for(no)`.
5. **Starvation.** The guard holds only its own market lock and awaits only the cancel, like the fork path. It sets `_dirty` only after a failed cancel, like the fork. On a bad book with nothing to cancel it returns after one core cycle.
6. **Restart and persistence.** No new state. Cancel outcomes persist through `note_cancel_result` → `_apply_core`, as before. No schema or checkpoint change, so no migration check is needed.
7. **Not claimed.** The 13.2 s dual-map pause (LIVE-06 open question) and its link to this guard stay unproven. This step does not resolve them.

## 6. Verification (no golden tests)

Run from the worktree. Never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Before: the HEAD baseline is `probe-head.txt` (section 4.2, last paragraph). After the change, the new tests give the `probe-with-guard.txt` values.
2. Targeted tests:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_book_guard.py tests/test_trader_host_resources.py tests/test_trader_session_core.py \
  tests/test_trader_shared_budget.py tests/test_trader_two_sided_worker.py tests/test_backtest_two_sided_core.py \
  tests/test_adapter_contract.py tests/test_core_trace.py tests/test_trader_engine_seams.py \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

3. Live-path regression (local Mac, xdist allowed):

```
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_*.py tests/test_adapter_contract.py tests/test_core_trace.py \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

4. Typecheck: `uv run python -m basedpyright` (whole project, as the feature defines it).
5. Lint: `uv run python -m pre_commit run --files src/trader/host_resources.py src/trader/session_core.py tests/test_trader_book_guard.py tests/test_trader_host_resources.py`.
6. Check by reading: `git diff --stat` shows only the 4 files above. `../poly-maker` has no change (`git -C ../poly-maker status --short` is empty for `src/`). Grep shows no new comments in `src/`.
