# STEP-009 plan: LIVE-04 strip our own orders from the mirrored book

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `9a922151`. Paths are relative to that worktree unless they start with `runs/` or `orchestrate/`.

Evidence scripts and outputs: `orchestrate/work/plan-step-009/`. Each probe ran on `9a922151`. `mirror_patch.py` is the planned `_own_qty` from section 3. `mirror_plugin.py` installs it for a pytest run. `test_final_proto.py` and `test_diag_proto.py` hold the exact planned test code.

## 0. Result

`_own_qty` subtracts an own order only from its own token (`src/trader/book_strip.py:132`). The venue shows every order in both books: a NO SELL at `p` is also a YES bid at `1 - p` with the same size. So our NO SELL can be the YES best bid, and it moves `book_p`, the latch, and the plan. The fix adds a mirror branch to `_own_qty`: an order on the other token with the other side counts at the key of `1 - price`. `_own_skip`, `_price_key`, and the rounded remainder in `_best_foreign` stay as they are, so STEP-007/008 logic applies to the mirror with no new code. The diag note gets one field, `mirror`.

Size: one production file, about +3/−2 lines. Tests: 14 new test items in `tests/test_trader_session_core.py`, 3 small edits to existing tests there, and 1 edit in `tests/test_trader_diag.py`. No schema, config, quoting, backtest, or `poly-maker` change.

## 1. What is broken today (evidence)

1. `src/trader/book_strip.py:132`: `if order.token_index != token_index or order.side != side: continue`. Each token book only loses our orders on that token. `_token_book` (`:73-81`) calls `_own_qty` per token with that token's snapshot time.
2. The venue books are mirrored. On full CLOB depth, 3,536,617 of 3,536,635 compared snapshots have every YES level matched by a NO level at `1 - price` with the same size (`runs/live-a-money-audit-20261009/reports/a2-grok-book.md`, Finding 1 table). Subtracting our size once from each book does not double-count foreign size.
3. grid-3011822-m1, seq 13967 BookUpdate, raw depth 10.6 ms before the core book stamp (`seq13967_bookp.txt`):
   - Raw YES bids `0.52 × 3016.9`, `0.51 × 73.43`, `0.50 × 196.02`; NO asks are the mirror. Own `c78` is NO SELL `0.48 × 3016.9`.
   - HEAD strip gives YES `0.52/0.54`, NO `0.46/0.49`, `book_p = 0.527363184079602`. This equals the recorded core book exactly.
   - With the fix: YES bid `0.51 × 73.43`, `book_p = 0.525`.
4. grid-3011822-m1, seq 7367 forced Wake, trace line 7368 (`seq7367_plan.txt`). The replay to that line has 0 plan and 0 digest mismatches.
   - Own orders: NO BUY `c23 0.57 × 350.87`, `c26 0.58 × 344.82`, `c29 0.59 × 338.98`, NO SELL `c31 0.64 × 811.36`.
   - Raw YES asks `0.41 × 193.98`, `0.42 × 344.82`, `0.43 × 350.87`, `0.44 × 193.57` are the mirror of the NO bids, so 0.41-0.43 are our own NO BUYs.
   - HEAD strip on the raw depth equals the recorded books (YES `0.40/0.41`). `book_p = 0.41117`. Plan: cancel c23, c26, c29, keep c31. This is the recorded live plan.
   - With the fix: YES ask `0.44 × 193.57`, `book_p = 0.42`. Plan: cancel c23, c26, c29 **and c31** (reason `reprice`). This matches the audit replay `runs/live-a-money-audit-20261009/work/orchestrator/grid-3011822-m1-mirror-plan-changes.json`.
5. Only 5 state fields drive this plan difference: `signal`, `episode_token_index`, `orders`, `inventory`, `latch` (`min_state.txt`, greedy reset to a fresh `_core(level_usdc=200.0).state`). The trace header policy equals `follow300_policy(level_usdc=200.0, debounce_ms=100, fallback_timer_s=2.0)` (`trim_state.py`, `header policy == current follow300_policy(200) True`).

## 2. Design decisions

1. **One branch in `_own_qty`, nothing else.** The step asks to reuse the exact key (STEP-007) and the stable remainder (STEP-008). Both act on the `qty` dict that `_own_qty` returns. A mirrored order only needs the right key in that dict. `_best_foreign` and `_price_key` do not change.
2. **Mirror rule.** For the book of `token_index` and side `side`, an order counts when it is on this token with this side (native), or on the other token with the other side (mirror). The mirror key is `_price_key(1.0 - order.price)`, as the STEP-007 learning says. `round` in `_price_key` absorbs the float error of `1.0 - price` (for example `1.0 - 0.57 = 0.43000000000000005` gives key 4300).
3. **Each order counts once per book.** For one book and one side, an order matches either the native rule or the mirror rule, never both. A native and a mirrored own order at one key are two real venue orders, so their sizes add (`native-and-mirror-one-level` test).
4. **Snapshot time and order state.** The mirror goes through the same `_own_skip(order, book_ts_ns, leftover)` (`:156-165`), with the timestamp of the book being stripped. So `gone`, `pending`, `accepted_after_snapshot`, and `no_leftover` apply per book. `canceling` and `unknown` orders still strip, as on the native side. Partial fills subtract only the leftover.
5. **Diag: one new note field `mirror`.** The note already has `order_id` (source order), `price` (source order price), `key` (the price key in the stripped book, so the transformed price for a mirror), `leftover`, and `skip` (the admission reason for this snapshot). The `strip` row has `yes_ts_ns` and `no_ts_ns`. Adding `mirror: bool` gives the native/mirror direction. No other field.
6. **B and paper.** B uses the same `core_books` (`src/trader/host_resources.py:100`, `src/trader/match_worker.py:507`) and gets the same strip, as in STEP-007/008. B uses `core_book_p` as its fair (`src/strategy/two_sided_quoting.py:121-122`) and `mid` for its band (`:54-58`). B now reads both without its own mirrored bids. B guards against crossing its own bids by order state (`_opposite_bid_blocks`, `:206-216`), not by the book. Paper passes `own_orders=()` (`src/trader/session_core.py:942`). All B tests pass with the patch (`suite_with_patch.txt`: the 6 failures are A strip tests only).
7. **No self-cross.** A joins the stripped best bid (BUY) or ask (SELL), or a SELL at `ceil(fair)` above the ask (`src/strategy/quoting.py:482-500`). The raw venue book is not crossed, so a foreign best bid is always below the mirror of our own NO BUY, and a foreign ask is always above the mirror of our own NO SELL. The new strip cannot move a join price onto our own complementary order.
8. **Not changed.** `src/backtest/strip_own_book.py` stays native-only and cent-keyed. It is sim scope (non-goal), as in STEP-007. The comment-free policy holds: no comments or docstrings in new code.

## 3. Change: `src/trader/book_strip.py`

`_own_qty` (`:122-153`). Replace lines `:132-134`:

```python
        if order.token_index != token_index or order.side != side:
            continue
        key = _price_key(order.price)
```

with:

```python
        mirror = order.token_index != token_index
        if (order.side == side) == mirror:
            continue
        key = _price_key(1.0 - order.price if mirror else order.price)
```

and add `"mirror": mirror,` to the note dict after `"price": order.price,` (`:145`). The note becomes:

```python
                {
                    "order_id": order.order_id,
                    "price": order.price,
                    "mirror": mirror,
                    "key": key,
                    "leftover": leftover,
                    "skip": skip,
                }
```

The skip test reads: native needs the same side, mirror needs the other side. No other production line changes.

## 4. Tests

All edits stay in the files that hold the strip tests today. The review of STEP-006..008 offered to move strip tests to a new file; the orchestrator skipped that (`progress.txt`, last line), so this step does not move them. `tests/test_trader_session_core.py` grows from 2162 to about 2410 lines (232 new lines plus the edits).

### 4.1 Existing tests that the fix breaks, and the edits

The full suite without goldens with the patch: 6 failed, 3179 passed (`suite_with_patch.txt`). Each failure is a test book that is not mirrored, so the mirror strip empties a side and `books_from_md` returns None, or a diag dict without `mirror`.

1. `_no_book()` (`tests/test_trader_session_core.py:287-288`). Its levels `0.48/0.50` are the mirror of the YES top in `test_books_from_md_strips_own_bid_level` (`:321`) and `test_books_from_md_strips_own_ask` (`:357`). Move it one tick off:

   ```python
   def _no_book() -> OrderBook:
       return _md_book([(0.47, 70.0)], [(0.49, 70.0)])
   ```

   All 15 `_no_book()` users pass with the patch (`nobook_plugin.py` run: only the 3 tests below still fail).

2. `test_dust_rung_reprices_off_float_dust` (`:772`). Make the YES asks the mirror of the raw NO depth. Line `:809`:

   ```python
           yes = _md_book(
               [(yes_bid, 100.0)], [(round(1.0 - price, 2), size) for price, size in case.no_bids]
           )
   ```

   After the NO bid assertion (`:815-818`) add:

   ```python
       assert (books.tokens[0].ask, books.tokens[0].ask_size) == (
           round(1.0 - case.stripped_bid, 2),
           case.stripped_size,
       )
   ```

   This is the joint fixture of a mirrored order with a float leftover: own NO BUY `0.68` leftover `106.83999999999999` hits the YES ask `0.32 × 106.84`, the STEP-008 rounding drops it, and the YES ask becomes `0.33 × 80.71`. grid-3011793-m2 gives `0.31 × 31.88`. The plan assertions do not change.

3. `test_partly_filled_sell_skips_float_dust` (`:834`). Make the NO bids the mirror of the raw YES asks. Line `:877`:

   ```python
           no = _md_book(
               [(0.38, 276.1), (0.37, 10.23), (0.36, 100.0), (0.35, 1792.18)], [(0.39, 100.0)]
           )
   ```

   After `:882` add:

   ```python
       assert (books.tokens[1].bid, books.tokens[1].bid_size) == (0.37, 10.23)
   ```

   Both ids (`hold_over`, `hold`) keep their plan assertions (`fixups_proto.txt`, variant b).

4. `tests/test_trader_diag.py::test_stream_records_the_fork_path_and_strip_stops`. Line `:188` gets `"mirror": False`. After it add the NO walk check (own YES BUY `0.38` is the NO ask `0.62`, key 6200):

   ```python
       assert own == [
           {"order_id": "own", "price": 0.38, "mirror": False, "key": 3800, "leftover": 100.0, "skip": ""}
       ]
       no_walk = cast(dict[str, object], strips[0]["no"])
       assert no_walk["own"] == [
           {"order_id": "own", "price": 0.38, "mirror": True, "key": 6200, "leftover": 100.0, "skip": ""}
       ]
   ```

   Checked in `test_diag_proto.py` (`diag_proto.txt`: red on HEAD, green with the patch).

### 4.2 New tests, `tests/test_trader_session_core.py`

Insert after `test_books_from_md_drops_float_dust` (`:556-573`), before `_SubCentSnapshot` (`:576`). Imports to add: `from strategy.engine import step`, `from strategy.signals import core_book_p`, and `Latch`, `Wake` in the existing `strategy.types` import. The exact code is `orchestrate/work/plan-step-009/test_final_proto.py` from the line `_MIRROR_BIDS = ...` to the end. It passes strict basedpyright (`pyright_check/`, the only error was an unused `Literal` import in the prototype header). Run ruff format once; it joins one `books_from_md(...)` call onto one line.

1. Helpers (module level, next to the tests):
   - `_MIRROR_BIDS = ((0.40, 100.0), (0.39, 50.0))`, `_MIRROR_ASKS = ((0.42, 60.0), (0.43, 40.0))`.
   - `_mirrored_books(bids, asks) -> tuple[OrderBook, OrderBook]`: YES from `bids`/`asks`, NO bids `(round(1.0 - p, 4), s)` of the YES asks and NO asks of the YES bids. Every new test builds a venue-consistent pair through it.
   - `_no_sell(qty, *, filled=0.0, accepted=True)`: `_own_order("ns", token_index=1, side="SELL", price=0.60, ...)`.
2. `test_books_from_md_strips_own_orders_from_both_books`, parametrized `(orders, bids, asks, yes, no)`. It asserts `[(t.bid, t.bid_size, t.ask, t.ask_size) for t in pair.tokens] == [yes, no]`, so each case checks the native book and the mirror book together.

| id | own orders | YES bids / asks | expected YES | expected NO |
|---|---|---|---|---|
| `yes-buy` | YES BUY 0.40 × 100 | default | 0.39×50 / 0.42×60 | 0.58×60 / 0.61×50 |
| `yes-sell` | YES SELL 0.42 × 60 | default | 0.40×100 / 0.43×40 | 0.57×40 / 0.60×100 |
| `no-buy` | NO BUY 0.58 × 60 | default | 0.40×100 / 0.43×40 | 0.57×40 / 0.60×100 |
| `no-sell` | NO SELL 0.60 × 100 | default | 0.39×50 / 0.42×60 | 0.58×60 / 0.61×50 |
| `foreign-on-mirror-level` | NO SELL 0.60 × 70 | default | 0.40×30 / 0.42×60 | 0.58×60 / 0.60×30 |
| `native-and-mirror-one-level` | YES BUY 0.40 × 60 + NO SELL 0.60 × 40 | default | 0.39×50 / 0.42×60 | 0.58×60 / 0.61×50 |
| `partial-fill` | NO SELL 0.60 × 100, filled 40 | default | 0.40×40 / 0.42×60 | 0.58×60 / 0.60×40 |
| `pending` | NO SELL 0.60 × 100, not accepted | default | 0.40×100 / 0.42×60 | 0.58×60 / 0.60×100 |
| `canceling` | NO SELL 0.60 × 100, status `canceling` | default | 0.39×50 / 0.42×60 | 0.58×60 / 0.61×50 |
| `gone` | NO SELL 0.60 × 100, status `gone` | default | 0.40×100 / 0.42×60 | 0.58×60 / 0.60×100 |
| `sub-cent-float-dust` | NO SELL c136 0.481 × 1758.8, filled `1562.0 + 41.66` | (0.519, 155.14), (0.518, 12.5) / (0.53, 20.0) | 0.518×12.5 / 0.53×20 | 0.47×20 / 0.482×12.5 |

   "default" is `_MIRROR_BIDS` / `_MIRROR_ASKS`. The last row is the joint fixture: a mirrored order, a 0.001 tick, and a float leftover `155.13999999999987`. It goes through `_price_key` and the STEP-008 rounding with no extra code.

3. `test_books_from_md_strips_the_mirror_at_its_own_snapshot_time`. Pin `trader.book_strip.time.time` to `2000.0`. YES `local_ts = 1999.0`, NO `local_ts = 2000.0`, `now_ns = 10_000_000_000`, so the YES stamp is `9e9` and the NO stamp is `1e10`. Own NO SELL 0.60 × 100 with `accepted_ns = 9_500_000_000`. Expected: YES keeps `0.40 × 100` (accepted after the YES snapshot), NO ask becomes `0.61 × 50` (accepted before the NO snapshot).
4. `test_own_no_sell_does_not_set_the_yes_bid_seq13967`. `_mirrored_books(((0.52, 3016.9), (0.51, 73.43), (0.50, 196.02)), ((0.54, 812.06), (0.55, 398.11)))`, own `c78` NO SELL `0.48 × 3016.9`. Assert YES bid `(0.51, 73.43)`, NO ask `(0.49, 73.43)`, and `core_book_p(books=pair, limits=_limits()) == 0.525`. HEAD gives `0.527363184079602` (section 1.3); the red run shows it.
5. `test_mirror_strip_cancels_sell_c31_at_seq7367`. Real-map core plan, latch and holds included:
   1. `state = replace(_core(level_usdc=200.0).state, signal=..., episode_token_index=1, orders=..., inventory=..., latch=...)` with the trace values from section 1.4 and `min_state.txt`: signal `predicted_delta=-0.04362078978031719`, `source_received_ns=4158093691716931`, `received_ns=4158093693477102`, `anchor_p=0.405`, deaths 2/2; NO inventory `811.368539`, cost `459.074339`, `last_buy_ns=4158060081769785`; latch `fair_radiant=0.36137921021968283`, `anchor_p=0.405`, `book_p_radiant=0.405`, `fair_valid=True`, `anchored_ns=4158096223924569`. Orders use `_own_order` (c23, c26, c29 NO BUY, c31 NO SELL; sizes from 1.4).
   2. Strip with the pinned clock (`time.time` and both `local_ts` = 1000.0, `now_ns=4158096557584606`, the recorded YES book stamp). YES bids `(0.40, 913.98), (0.39, 1525.95)`; YES asks `(0.41, 193.98), (0.42, 344.82), (0.43, 350.87), (0.44, 193.57), (0.45, 192.0)`; NO is the mirror. Assert YES ask `(0.44, 193.57)`.
   3. `recorded` = the stripped pair with the YES ask set back to `0.41 × 193.98` (the HEAD book). `wake = Wake(now_ns=4158096572541318, forced=True)`. Run `step(..., policy=_policy(200.0), event=wake)` on both.
   4. Assert `recorded` keeps `c31` and cancels `c23, c26, c29` (the recorded live plan). Assert the stripped book keeps nothing, cancels `c23, c26, c29, c31` all with `reprice`, and places nothing.

   The `_own_order` defaults (`accepted_ns=-1e12`, `placed_ns=0`) give the same plans as the real stamps: c31 is past the 1 s sell min life either way, and `sell_unconfirmed` is False.

### 4.3 Red first

Add the tests and the test edits, then run them before the `src/` change. Expected (`final_proto.txt`, `fixups_proto.txt`, `diag_proto.txt`):

- New tests: 11 fail (`yes-buy`, `yes-sell`, `no-buy`, `no-sell`, `foreign-on-mirror-level`, `native-and-mirror-one-level`, `partial-fill`, `canceling`, `sub-cent-float-dust`, seq13967, seq7367), 3 pass (`pending`, `gone`, snapshot time). The 3 that pass guard the mirror against wrong admission rules.
- Edited tests: `test_dust_rung_reprices_off_float_dust` ×2, `test_partly_filled_sell_skips_float_dust` ×2, and the diag test fail. The two `_no_book()` tests pass.

Then make the `src/` change. All pass.

## 5. Behavior changes and risks

- Our own complementary orders no longer set the other token's best. `book_p`, the latch anchor check, mid-spike samples, sell joins, and the wide-spread check now see foreign liquidity only. On seq 7367 this adds the c31 reprice cancel. The audit counted 2,889 updates where the complement best was strictly our order (`a2-grok-book.md`, Finding 1); 36 sell places matched the dirty target, not the clean one.
- If our own orders are the whole side of the other token, `core_books` returns None. `_gate_pair` reports `OWN_LIQUIDITY_ONLY` (`src/trader/match_worker.py:485-490`) and the core treats the book as stale, which cancels resting orders (`src/strategy/quoting.py:100-105,876-877`). This is the existing rule for native own size and is rare on real depth.
- B gets the same strip (decision 2.6). If the orchestrator wants B untouched, the minimal alternative is a `mirror` flag through `core_books` → `books_from_md` → `_token_book` → `_own_qty`. This plan does not add it.
- Live and backtest strips now differ in the mirror too. Sim scope is a non-goal.
- `core_trace` `BookUpdate` rows change only when a mirrored own order was at the touch. The trace codec and replay read recorded books, so old traces still replay.

## 6. Verification (no golden tests)

Run from the worktree. Never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Red run, before the `src/` change:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_session_core.py tests/test_trader_diag.py -k "mirror or both_books or seq13967 or seq7367 or dust_rung or partly_filled_sell or strips_own_bid_level or strips_own_ask or fork_path" --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Expect the counts in 4.3 (adjust `-k` to the final names).
2. Targeted run, after the change: the same files plus the other `books_from_md` and `core_books` users:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_session_core.py tests/test_trader_diag.py tests/test_trader_live_execution.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_book_guard.py tests/test_trader_host_resources.py tests/test_trader_two_sided_worker.py tests/test_strategy_two_sided.py tests/test_strategy_core.py --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
3. Full suite without goldens, required before the commit:
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Baseline on `9a922151`: 3185 passed. Expected: 3199 passed (14 new items). The patched run without the test edits gave 6 failed, 3179 passed; section 4.1 covers all 6.
4. Typecheck: `uv run python -m basedpyright` → 0 errors.
5. Lint: `uv run python -m pre_commit run --files src/trader/book_strip.py tests/test_trader_session_core.py tests/test_trader_diag.py`.
6. Scope checks: `git diff --stat` shows only those 3 files; `git -C ../poly-maker status --short` is clean; no new comment, docstring, or suppression lines in the diff.
7. Commit on `live-a-audit-fixes`, no push. Suggested subject: `fix: STEP-009 - strip own orders from the mirrored book`. Leave `passes` in `feature.json` unchanged; append the step entry to `progress.txt`.
