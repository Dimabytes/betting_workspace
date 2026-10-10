# STEP-007 plan: LIVE-05 match own size by the exact venue price

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `871cd0e0`. All paths below are relative to that worktree unless they start with `runs/` or `orchestrate/`.

Evidence scripts and outputs: `orchestrate/work/plan-step-007/` (`raw_snapshots.py/.txt`, `core_plan_m3.py/.txt`, `core_plan_9034149426.py/.txt`, `strip_cases.py/.txt`). Each script ran on the worktree code at `871cd0e0`. The "exact" runs monkeypatch the strip key to `round(price / 0.0001)`.

## 0. Result in one paragraph

`src/trader/book_strip.py` keys own size by `price_cents` (`round(price / 0.01)`), so one own order is subtracted from every book level in its cent. The fix replaces that key with an integer key on the finest venue tick, `round(price / 0.0001)`, in the same two places. The aggregation per key and the one lookup per book level already exist, so a level now loses only the own size at its own exact price. `price_cents` has no other user and is deleted from `src/strategy/signals.py`, so the strip key and the 0.01 quote grid are separate. The diag note field `cents` becomes `key`. Size: about +7/−9 lines in 2 production files, plus tests. On a 0.01-tick market the new key is 100 × the old key, so nothing changes there. Only sub-cent books change. No quoting, config, schema, backtest, or `poly-maker` change.

## 1. What is broken today (evidence)

1. `_own_qty` sums own leftovers into `qty[price_cents(order.price)]` (`src/trader/book_strip.py:132-133`). `_best_foreign` subtracts `own.get(price_cents(price), 0.0)` from every level (`book_strip.py:170-171`). `price_cents` is `round(price / QUOTE_GRID)` with `QUOTE_GRID = 0.01` (`src/strategy/signals.py:127-129`, `src/shared/constants/strategy.py:26`). Its docstring says it exists only for this strip, and `grep -rn price_cents src tests scripts` finds no other user.
2. Prices 0.375 through 0.385 all map to cent 38 (`runs/live-a-money-audit-20261009/reports/a2-grok-book.md:107-114`). So own BUY 0.380 × 100 makes `_best_foreign` skip 0.385 × 40, 0.384 × 25, 0.380 × 100, and 0.379 × 30, and it returns 0.374 × 80 (`strip_cases.txt`, row `regression_buy`). The test `tests/test_trader_diag.py:167,184-185` pins this wrong result today (`bid == 0.374`).
3. Book prices are venue decimals parsed with `float(...)` (`poly-maker/src/polymaker/marketdata/parse.py:72,105,121`), one `OrderBook` per token (`poly-maker/.../marketdata/service.py:67-82`). The venue tick set is `"0.1" | "0.01" | "0.001" | "0.0001"` (`.venv/.../py_clob_client/clob_types.py:209`). Every venue price is therefore a whole number of 0.0001 steps.
4. Raw depth confirms the defect, and the real core plan changes (`raw_snapshots.txt`, `core_plan_*.txt`):

| Map, snapshot `timestamp_us` | Own BUY rungs | Old strip best → join | Exact strip best → join | Old core plan | Exact core plan |
|---|---|---|---|---|---|
| `9034149426`, `1791408077375000`, NO token | 0.54 × 259.25, 0.53 × 264.15, 0.52 × 269.23 | 0.53 × 34.99 → 0.53 | 0.542 × 112.15 → 0.54 | cancel c0 (0.54), place 0.51 × 274.5 | keep all three |
| `grid-3011822-m3`, `1791490071744001`, YES token | 0.67 × 298.5, 0.66 × 303.03, 0.65 × 307.69 | 0.667 × 1.5 → 0.66 | 0.671 × 90 → 0.67 | cancel c0 (0.67), place 0.64 × 312.5 | keep all three |

   The old 9034149426 plan is the audit's "14 buys of 274.50 at `0.51` against an exact join of `0.52`" (`a2-grok-book.md:140`). The core-plan probes build a real `LiveCore` (follow300 policy, `level_usdc` 200 and 140) and reproduce the live order prices and sizes in the prime cycle.
5. Third audit snapshot, `grid-3011822-m3` `1791490089049005`: the raw YES book is crossed (bid 0.678, ask 0.673, `raw_snapshots.txt`). STEP-005 sends that book to `_check_yes_book` → `crossed` (`src/trader/host_resources.py:129-140`), so the core runs with `books=None` under either key. It is not a plan fixture.

## 2. Design decisions

1. **Key on the finest venue tick, not on the market tick.** `round(price / 0.0001)` is exact for every venue tick in item 1.3 and needs no tick argument. A tick change during a map (0.01 → 0.001 near the price extremes) needs no handling. Passing `meta.tick_size` into `books_from_md` adds a parameter to three callers and gives the same result.
2. **Keep the integer key and the existing dict.** `_own_qty` already sums all own leftovers at one key. `_best_foreign` already looks up each level once. Two own orders at the same exact price are summed and subtracted once. A float-noise own price (for example `math.nextafter(0.38, 1.0)`) rounds to the same key as the book's 0.38. Exact float equality would miss it.
3. **Delete `price_cents` and the `QUOTE_GRID` import from `signals.py`.** That separates the strip key from the strategy grid, as the step asks. `QUOTE_GRID`, `floor_to_grid`, `ceil_to_grid`, the ladder, and the join rules do not change.
4. **The key lives in `book_strip.py` as a private helper.** One module uses it. STEP-008 and STEP-009 edit the same file and reuse it (the mirror price is `_price_key(1.0 - price)`).
5. **Diag: rename the note field `cents` to `key`.** Each `own` note already has `order_id`, `price`, `leftover`, and `skip`. With `key` it has the source price, its exact key, and the contribution of each order (the leftover when `skip` is empty). Each walk row already has `price`, `size`, `own` (summed own at that exact key), and `left`. No reader of `cents` exists (`grep` over `src`, `tests`, `scripts`, `docs`, `betting_workspace/.shared-skills`, `.learnings`).
6. **Backtest strip stays as it is.** `src/backtest/strip_own_book.py:23,46-48` has its own 0.01 key. The feature excludes sim work, and the step says not to move this fix there.
7. **B and paper.** B uses the same `core_books` (`host_resources.py:100`, `match_worker.py:507`), so B gets the same key. For 0.01-grid prices the old and new keys map one to one, so B on 0.01 ticks is unchanged. Paper passes `own_orders=()` (`src/trader/session_core.py:942`) and is unchanged.

## 3. Changes

### 3.1 `src/trader/book_strip.py`

1. Remove `from strategy.signals import price_cents` (`:10`).
2. After `_TOKENS` (`:14`):

```python
_FINEST_VENUE_TICK = 0.0001


def _price_key(price: float) -> int:
    return round(price / _FINEST_VENUE_TICK)
```

3. `_own_qty` (`:117-148`): compute the key once, before the skip check, and use it for the sum and the note:

```python
    for order in orders:
        if order.token_index != token_index or order.side != side:
            continue
        key = _price_key(order.price)
        leftover = order.submitted_qty - order.filled_qty
        skip = _own_skip(order, book_ts_ns, leftover)
        if not skip:
            qty[key] = qty.get(key, 0.0) + leftover
```

   In the note dict, replace `"cents": price_cents(order.price),` with `"key": key,`.
4. `_best_foreign` (`:170`): `mine = own.get(_price_key(price), 0.0)`.

Do not change `left > 0.0`, `_own_skip`, the walk stop, or the diag record shape. STEP-008 owns the float remainder.

### 3.2 `src/strategy/signals.py`

Delete `price_cents` (`:127-129`, with its blank lines) and `from shared.constants.strategy import QUOTE_GRID` (`:3`). `price_cents` is the only user of that import in the file.

### 3.3 Not changed

`session_core.py`, `host_resources.py`, `match_worker.py`, `quoting.py`, `shared/utils/trading.py`, `src/backtest/strip_own_book.py`, config, traces, checkpoints, and `poly-maker`. New code gets no comments or docstrings (review rule in `.feature-json.config.json`).

## 4. Tests

All new tests go into `tests/test_trader_session_core.py`, next to the `books_from_md` tests (`:278-393`), with the existing `_md_book`, `_no_book`, and `_own_order` helpers. Add `import math`.

### 4.1 Regression fixture, BUY and SELL

`test_books_from_md_strips_only_the_exact_venue_price`, parametrized:

- BUY: own BUY 0.38 × 100. YES bids 0.385 × 40, 0.384 × 25, 0.38 × 100, 0.379 × 30, 0.374 × 80. Expect `bid == 0.385`, `bid_size == 40.0`. The old key gives 0.374 × 80.
- SELL: own SELL 0.62 × 100. YES asks 0.616 × 40, 0.619 × 25, 0.62 × 100, 0.624 × 30, 0.626 × 80. Expect `ask == 0.616`, `ask_size == 40.0`. The old key gives 0.626 × 80.

### 4.2 Old rounding bounds and 0.0001 neighbors

`test_books_from_md_keeps_a_neighbor_of_the_own_price`, parametrized over 0.385, 0.375, 0.3801, 0.3799. Own BUY 0.38 × 100. Bids: neighbor × 10, 0.38 × 100, 0.30 × 50. Expect `(bid, bid_size) == (neighbor, 10.0)`. The old key gives 0.30 × 50 in all four cases. 0.385 and 0.375 are the old cent bounds. 0.3801 and 0.3799 are tick-0.0001 prices.

### 4.3 Several own orders at one exact price

`test_books_from_md_sums_own_orders_at_one_exact_level`:

- Own A: BUY 0.38 × 60, filled 20 (leftover 40). Own B: BUY `math.nextafter(0.38, 1.0)` × 60 (float noise, same key). Total own leftover 100.
- Bids 0.38 × 130, 0.379 × 70 → `(0.38, 30.0)`: own plus foreign on one level, subtracted once.
- Bids 0.38 × 100, 0.379 × 70 → `(0.379, 70.0)`: the summed own size consumes the level. One order alone would leave 40 or 60. The old key wipes 0.379 too and returns `None`.

The 0.01-tick cases stay covered by the existing tests (`strips_own_bid_level`, `keeps_foreign_leftover`, `counts_only_the_fill_leftover`, `strips_own_ask`). They must pass unchanged.

### 4.4 Raw snapshots through the real core plan

`test_raw_sub_cent_book_keeps_the_resting_rungs`, parametrized over the two rows of section 1.4. Use the head levels from `raw_snapshots.txt`:

| Case | `level_usdc` | Signal `predicted_delta`, `anchor_p` | Prime views YES / NO | Expected prime places |
|---|---|---|---|---|
| `9034149426` | 140.0 | −0.05, 0.455 | (0.452, 0.458) / (0.54, 0.548) | token 1 BUY 0.54 × 259.25, 0.53 × 264.15, 0.52 × 269.23 |
| `grid-3011822-m3` | 200.0 | 0.05, 0.675 | (0.67, 0.679) / (0.321, 0.329) | token 0 BUY 0.67 × 298.5, 0.66 × 303.03, 0.65 × 307.69 |

Raw levels:

- `9034149426`: YES bids 0.452 × 20, 0.451 × 62.44, 0.45 × 111.42. YES asks 0.458 × 112.15, 0.46 × 259.25, 0.47 × 299.14, 0.48 × 269.23, 0.514 × 5.09. NO bids 0.542 × 112.15, 0.54 × 259.25, 0.53 × 299.14, 0.52 × 269.23, 0.486 × 5.09. NO asks 0.548 × 20, 0.549 × 62.44, 0.55 × 111.42.
- `grid-3011822-m3`: YES bids 0.671 × 90, 0.67 × 232.25, 0.669 × 30, 0.667 × 300, 0.666 × 2184.49, 0.665 × 43, 0.664 × 892.85, 0.66 × 303.02. YES asks 0.679 × 178.49, 0.68 × 751, 0.685 × 33. NO bids 0.321 × 178.49, 0.32 × 751, 0.315 × 33. NO asks 0.329 × 90, 0.33 × 232.25, 0.331 × 30, 0.333 × 300.

Steps, the same as `core_plan_*.py`:

1. Give `_policy` and `_core` a keyword `level_usdc: float = 65.0` and pass it to `follow300_policy`. All 55 current `_core(...)` calls keep 65.
2. Enqueue `SignalUpdate` at 0, then `drive(core, _inputs(), _sources(now_ns=0, books=books_from_views(...)))`. Assert the three prime places (token, side, price, size).
3. `core.note_placed([_venue(item.quote, f"v{n}") ...], 0)`, then `core.drain_apply()`. All three orders are `live` with `accepted_ns == 0`.
4. Build both raw books with `_md_book`. Set `yes.local_ts = no.local_ts = 1000.0`. Patch `trader.book_strip.time.time` to `1000.0`, as `test_books_from_md_skips_accept_newer_than_the_snapshot` does. With `now_ns = 1_000_000_000`, the book `ts_ns` equals `now_ns`, so the strip never skips an own order as `accepted_after_snapshot`. Without this, a slow run could skip the own orders, and the test would pass under the old key.
5. Call `books_from_md(..., own_orders=core.state.orders)`. Assert the stripped best of the quoted token: 0.542 × 112.15, or 0.671 × 90.
6. Enqueue a new signal at `now_ns`, then `drive` with those books. Assert `take_plan()` is not None, `to_cancel == ()`, and `to_place == ()`.

The old key gives "cancel `v0`, place 0.51 × 274.5" and "cancel `v0`, place 0.64 × 312.5" (`core_plan_*.txt`).

### 4.5 `tests/test_trader_diag.py`

`_pair` (`:292-323`) is the regression fixture. Update the pinned old result:

- `:167`: `off.tokens[0].bid == on.tokens[0].bid == 0.385`.
- `:184`: walk bids `== [0.385]`. The walk stops at the chosen level, so this still proves the stop.
- `:185`: `yes["bid_price"] == 0.385`.
- Add: the YES `own` note is `{"order_id": "own", "price": 0.38, "key": 3800, "leftover": 100.0, "skip": ""}`.

## 5. Behavior changes and risks

1. Intentional changes against the historical baseline (record them in the commit message, because new code has no comments):
   - Sub-cent books only (tick 0.001 or 0.0001). An own order no longer hides foreign levels in the same cent. The stripped bid is higher or the stripped ask is lower, and the 0.01 join can move one cent toward the touch. Section 1.4 shows the two raw snapshots, where the core now keeps its rungs and does not reprice down.
   - Audit full-depth replay, cent key against exact key: `9034149426` 17,952 side-snapshots with a different best and 2,138 join changes, `grid-3011822-m3` 10,446 and 868 (`a2-grok-book.md:122-123`). The live strip now follows the audit's exact-key path. I did not re-run that 413 MB replay. It is a long job, which `00-context.md` forbids, and the two snapshots above are the raw-fixture check.
   - A BUY joined one cent higher pays one cent more if it fills. The audit found the cent key made quotes less aggressive. Its one walked fill was in our favor, 4.4636 USDC (`a2-grok-book.md:148-150`). The fix restores the intended join. It is not a PnL claim.
2. 0.01-tick markets (98 of the 104 audited maps): no change. The keys map one to one for 0.01-grid prices.
3. The float remainder after a subtraction is still `left > 0.0` (LIVE-03). STEP-008 fixes it on top of these keys.
4. The mirror representation is still missing (LIVE-04). STEP-009 adds it with `_price_key(1.0 - price)`.
5. The backtest strip keeps its 0.01 key, so live and backtest differ on sub-cent books. That is the sim scope that the feature excludes.
6. Goldens are not run (user rule). They replay recorded post-strip `BookUpdate`s and never call `books_from_md`, so they have no input to change.

## 6. Verification (no golden tests)

Run from the worktree. Never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Red first: add the tests from section 4, then run them on unchanged `src/`. 4.1, 4.2 (all four), the second case of 4.3, both cases of 4.4, and the updated diag test must fail. The first case of 4.3 passes under both keys.
2. Make the section 3 change. Then run the targeted tests:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_session_core.py tests/test_trader_diag.py tests/test_trader_live_execution.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_book_guard.py \
  tests/test_trader_host_resources.py tests/test_trader_two_sided_worker.py tests/test_strategy_core.py \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

3. Full suite without goldens, required before the commit (about 30-50 s):

```
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests \
  --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py
```

   Baseline on `871cd0e0`: 3165 passed (progress log). Expected: 3174 passed (+9 new test cases), 0 failures.
4. Typecheck: `uv run python -m basedpyright` (whole project, as the feature defines it).
5. Lint: `uv run python -m pre_commit run --files src/trader/book_strip.py src/strategy/signals.py tests/test_trader_session_core.py tests/test_trader_diag.py`.
6. Checks by reading:
   - `grep -rn price_cents src tests scripts` prints nothing.
   - `git diff --stat` shows only the 2 production files and the 2 test files.
   - `git -C ../poly-maker status --short` shows no change.
   - `git diff` adds no comment or docstring lines in `src/`.
