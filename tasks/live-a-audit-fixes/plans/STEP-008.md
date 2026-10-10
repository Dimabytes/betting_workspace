# STEP-008 plan: LIVE-03 skip float dust when picking the foreign best

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `7b979aec`. Paths are relative to that worktree unless they start with `runs/` or `orchestrate/`.

Evidence scripts and outputs: `orchestrate/work/plan-step-008/` (`strip_cases.py/.txt`, `buy_plan.py/.txt`, `buy_plan_m2.py/.txt`, `sell_plan.py/.txt`, `diag_row.py/.txt`). Each script ran on `7b979aec` and compares the current `_best_foreign` with a monkeypatched copy that applies the fix in section 3.

## 0. Result

`_best_foreign` accepts a level when `size - own > 0.0` (`src/trader/book_strip.py:175,181`). After a partial fill, `submitted_qty - filled_qty` is a float a few ULPs below the venue decimal, so our own order survives as a foreign level of size `1e-13`. The fix rounds the remainder to the venue size precision (6 decimals) before the test, returns that rounded size, and logs it next to the raw `left`. Size: one private constant plus about 3 changed lines in one production file. Tests: one strip fixture table, two real-map core-plan tests, one diag test. No schema, config, quoting, backtest, or `poly-maker` change.

## 1. What is broken today (evidence)

1. `src/trader/book_strip.py:173-182`: `left = size - mine`; `if left > 0.0: return (price, left), rows`. `mine` is the sum of `order.submitted_qty - order.filled_qty` per exact key (`:133-137`). `filled_qty` grows by float addition per fill.
2. The audit residues reproduce on `7b979aec` (`strip_cases.txt`, `current` rows):

| Case | Own leftover | Venue level | Current best | Fixed best |
|---|---|---|---|---|
| `9034019293` c136 SELL 0.60, 1758.8 − (1562.0 + 41.66) | 155.13999999999987 | 0.60 × 155.14 | 0.60 × 1.1368683772161603e-13 | 0.62 × 49.99 |
| `9034019293` c12 BUY 0.68, 205.88 − (69.04 + 30.0) | 106.83999999999999 | 0.68 × 106.84 | 0.68 × 1.4210854715202004e-14 | next level |
| two own BUYs at 0.68 (205.88 − 69.04) + 30.0 | 166.83999999999997 | 0.68 × 166.84 | 0.68 × 2.842170943040401e-14 | 0.67 × 80.71 |
| foreign 5.0 on the own level 0.68 | 136.83999999999997 | 0.68 × 141.84 | 0.68 × 5.000000000000028 | 0.68 × 5.0 |

3. The real core plan keeps the ghost rung or SELL (`buy_plan.txt`, `buy_plan_m2.txt`, `sell_plan.txt`). Section 4.2 and 4.3 list the numbers.

## 2. Design decisions

1. **Normalize the remainder, not the inputs.** `foreign = round(size - mine, 6)` is the one place where "own covers the level" is decided. It covers one partial fill, several fills, and the sum of several own orders at one key, because all float error ends up in this one subtraction.
2. **6 decimals is the venue size precision.** Conditional tokens have 6 decimals (`.venv/lib/python3.13/site-packages/py_clob_client/rfq/rfq_helpers.py:15`, `CONDITIONAL_TOKEN_DECIMALS = 6`). Own leftovers in the evidence have up to 6 decimals (`87.336176`, `91.74625`, `runs/live-a-money-audit-20261009/work/orchestrator/buy-ghost-depth-independent.txt`). Float error from a few add/sub operations on values below 1e7 is below 1e-8, far under the 5e-7 rounding bound. So a real 0.000001 foreign size stays visible (`min-foreign` row in `strip_cases.txt`: 0.60 × 1e-06), and float error becomes 0.0.
3. **Private constant in `book_strip.py`, no import from `py_clob_client.rfq`.** Same pattern as `_FINEST_VENUE_TICK` from STEP-007 (`book_strip.py:14`). Importing the rfq package into the live strip path for one integer is not worth it.
4. **Return the rounded size.** The returned `bid_size`/`ask_size` becomes the clean decimal (`80.71`, not `80.71000000000004`). `src/strategy` reads no `bid_size`/`ask_size` (`grep -rn -e bid_size -e ask_size src` hits only `strategy/types.py:47-48`, the trace codec, `dust_sweep.py`, and backtest). `dust_sweep.py:325-332` reads the raw md book, not this strip. With no own size, `round(size, 6)` returns the same double for any venue decimal, so books without own orders do not change.
5. **Diag: add one row field `foreign`.** Each walk row keeps raw `price`, `size`, `own`, `left` and adds the normalized `foreign`. The reason a level is skipped follows from these two numbers: `left > 0` and `foreign == 0` is float dust; `left <= 0` is own size covering the level. A separate reason string would restate the same two numbers, so the plan does not add one. If the orchestrator wants a literal field, it is one more dict entry.
6. **Not changed.** `_own_skip` keeps `leftover <= 0.0` (`book_strip.py:162`): a fully filled order with `+1e-14` dust only subtracts that dust, and the rounding removes it. `src/backtest/strip_own_book.py` is sim scope (non-goal). Venue rounding, where the book shows 2 decimals while our leftover has more (`91.74` vs `91.74625`), gives a negative `left` and strips correctly today; it is not float error, and a larger tolerance is a non-goal.
7. **B and paper.** B uses the same `core_books` (STEP-007 plan, `host_resources.py:100`, `match_worker.py:507`) and gets the same fix. B reads no book sizes. Paper passes `own_orders=()` (`src/trader/session_core.py:942`).

## 3. Change: `src/trader/book_strip.py`

1. After `_FINEST_VENUE_TICK = 0.0001` (`:14`):

```python
_VENUE_SIZE_DECIMALS = 6
```

2. `_best_foreign` (`:167-183`):

```python
    for price, size in levels:
        mine = own.get(_price_key(price), 0.0)
        left = size - mine
        foreign = round(left, _VENUE_SIZE_DECIMALS)
        if rows is not None:
            try:
                rows.append(
                    {"price": price, "size": size, "own": mine, "left": left, "foreign": foreign}
                )
            except Exception:
                rows = None
        if foreign > 0.0:
            return (price, foreign), rows
    return None, rows
```

No other production line changes. No comments or docstrings.

## 4. Tests

All new tests go into existing files next to the STEP-007 strip tests and reuse their helpers (`_md_book`, `_no_book`, `_own_order`, `_core`, `_view`, `_inputs`, `_sources`, `_venue`, `drive`, `books_from_views`). `tests/test_trader_session_core.py` is 1877 lines today, so no 1k-line crossing.

### 4.1 Strip fixtures, `tests/test_trader_session_core.py` (after `test_books_from_md_sums_own_orders_at_one_exact_level`, `:448-469`)

One parametrized test, for example `test_books_from_md_drops_float_dust`, same shape as `test_books_from_md_strips_only_the_exact_venue_price` (`:418-437`): BUY levels go to the YES bids with asks `[(0.90, 50.0)]`; SELL levels go to the YES asks with bids `[(0.30, 50.0)]`; assert `(price, size)` of the chosen side. Own orders use `_own_order(..., filled=<sum>)` on token 0. Write fills as a sum literal, for example `filled=1562.0 + 41.66`; it gives the same double as the core's step-by-step addition.

| id | side | own orders | levels (best first) | expected |
|---|---|---|---|---|
| `9034019293-sell-dust` | SELL | c136 0.60, qty 1758.8, filled 1562.0 + 41.66 | (0.60, 155.14), (0.62, 49.99) | 0.62, 49.99 |
| `two-fills-next-exact-level` | BUY | c12 0.68, qty 205.88, filled 69.04 + 30.0 | (0.68, 106.84), (0.679, 12.5), (0.67, 80.71) | 0.679, 12.5 |
| `two-own-orders-one-level` | BUY | a 0.68 qty 205.88 filled 69.04; b 0.68 qty 30.0 | (0.68, 166.84), (0.67, 80.71) | 0.67, 80.71 |
| `negative-dust` | BUY | c84 0.70, qty 32.24, filled 5.0 (leftover 27.240000000000002) | (0.70, 27.24), (0.69, 32.09) | 0.69, 32.09 |
| `foreign-on-own-level` | BUY | c12 0.68, qty 205.88, filled 69.04 | (0.68, 141.84), (0.67, 80.71) | 0.68, 5.0 |
| `min-foreign-size-stays` | SELL | c136 as row 1 | (0.60, 155.140001), (0.62, 49.99) | 0.60, 1e-06 |

All six pass with the fix (`strip_cases.txt`, `patched` rows). Before the fix, five fail and `negative-dust` passes.

### 4.2 BUY ghost through the real core plan, same file (after `test_raw_sub_cent_book_keeps_the_resting_rungs`)

1. Generalize `_raw_signal(now_ns, snapshot)` (`:596`) to `_raw_signal(now_ns: int, *, anchor_p: float, predicted_delta: float)`. Update its two STEP-007 calls to pass `snapshot.anchor_p` and `snapshot.predicted_delta`. Do not add a second signal helper.
2. A frozen dataclass for the case, for example `_DustRung(level_usdc, anchor_p, no_bid, fills, no_bids, stripped_bid, stripped_size, replacement_price, replacement_size)`. Do not use anonymous tuples for the case.
3. Steps (all on the NO token, index 1, `predicted_delta=-0.05`):
   1. `core = _core(level_usdc=case.level_usdc)`. Enqueue the signal at `now_ns=0`. `drive` with `books_from_views(yes=_view(1 − no_bid − 0.01, 1 − no_bid), no=_view(no_bid, no_bid + 0.01), ts_ns=0)` (round each price to 2 decimals).
   2. `take_plan()`, `note_placed([_venue(item.quote, f"v{index}") ...], 0)`, `drain_apply()`. Rung c0 has venue id `v0`.
   3. `note_fill(fill_key=f"f{index}", venue_id="v0", qty=qty, price=no_bid, now_ns=0, token_index=1, side="BUY")` for each fill, then `drain_apply()`. The strip reads `core.state.orders`, so the fills must be applied before the strip.
   4. Strip with the clock pinned as in STEP-007: `patch("trader.book_strip.time.time", return_value=1000.0)`, both `local_ts = 1000.0`, `now_ns = 1_000_000_000`. YES md book `[(yes_bid, 100.0)]` / `[(yes_ask, 100.0)]`; NO md book `case.no_bids` / `[(no_ask, 100.0)]`. Assert `(tokens[1].bid, tokens[1].bid_size) == (stripped_bid, stripped_size)`.
   5. Enqueue the signal at `now_ns`, `drive` with `_sources(now_ns=now_ns, books=books, store_no=sum(case.fills))`. Assert `plan.to_cancel == ("v0",)` and `[(item.quote.price, item.quote.size) for item in plan.to_place] == [(replacement_price, replacement_size)]`.
4. Cases (ids are the map ids; NO bids are raw collector depth from `buy-ghost-depth-independent.txt`):

| id | level_usdc | anchor_p | no_bid | prime rungs | fills on v0 | NO bids | stripped | plan, fixed | plan, today |
|---|---|---|---|---|---|---|---|---|---|
| `grid-3011793-m2` | 60.0 | 0.29 | 0.70 | 0.70 × 85.71, 0.69 × 86.95, 0.68 × 88.23 | 53.47 | (0.70, 32.24), (0.69, 118.83), (0.68, 107.87), (0.67, 59.04), (0.66, 124.86) | 0.69 × 31.88 | cancel v0, place 0.67 × 89.55 | bid 0.70 × 7.105427357601002e-15, empty plan |
| `9034019293` | 140.0 | 0.31 | 0.68 | 0.68 × 205.88, 0.67 × 208.95, 0.66 × 212.12 | 69.04, 30.0 | (0.68, 106.84), (0.67, 289.66), (0.66, 232.83), (0.65, 860.23), (0.64, 891.0) | 0.67 × 80.71 | cancel v0, place 0.65 × 215.38 | bid 0.68 × 1.4210854715202004e-14, empty plan |

   Sources: `buy_plan_m2.txt` and `buy_plan.txt` (`two fills` rows). The 9034019293 prime rungs match the live sizes exactly. On grid-3011793-m2 the test rung c1 is 86.95, while live c81 was 86.74 after an earlier fill, so the test foreign size at 0.69 is 31.88 and live was 32.09. The price, which the core uses, is the same. The fixed plan matches the live log at 19:38:33.120, where the core cancelled c12 for reprice as soon as the residue turned negative (`runs/live-a-money-audit-20261009/reports/a2-opus-exec-epsilon.md`, BUY ghosts).

### 4.3 SELL ghost through the real core plan, same file

Map `9033777506`, c61 SELL 0.62 × 575.28, fill 299.18, leftover 276.09999999999997. Parametrize `sell_unconfirmed` in `(False, True)` with expected cancels `("v0",)` and `()`; ids `hold_over` and `hold`.

1. `core = _core(level_usdc=140.0)`. Enqueue `_raw_signal(0, anchor_p=0.62, predicted_delta=-0.05)`. Set `core._state = replace(core.state, inventory=(TokenInventory(token_index=0, qty=575.28, cost_basis=300.0, last_buy_ns=-10**13), TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None)), has_buy_fill=True)`, as `_held_yes_core` does.
2. `drive` with `_sources(now_ns=0, store_yes=575.28, books=books_from_views(yes=_view(0.61, 0.62), no=_view(0.38, 0.39), ts_ns=0))`. The prime plan places only c0 SELL 0.62 × 575.28 (assert it).
3. `note_placed([_venue(quote, "v0")], 0)`, `drain_apply()`, `note_fill(fill_key="f0", venue_id="v0", qty=299.18, price=0.62, now_ns=0, token_index=0, side="SELL")`, `drain_apply()`.
4. Strip at `now_ns = 5_000_000_000` (past the 1 s SELL min life) with the pinned clock. YES bids `[(0.61, 100.0)]`, YES asks `[(0.62, 276.1), (0.63, 10.23), (0.64, 100.0), (0.65, 1792.18)]` (raw depth at 16:58:34.245, `runs/live-a-money-audit-20261009/work/orchestrator/sell-ghost-depth-verified.json`), NO `[(0.38, 100.0)]` / `[(0.39, 100.0)]`. Assert `(tokens[0].ask, tokens[0].ask_size) == (0.63, 10.23)`.
5. Enqueue the signal at `now_ns`. Build `sources = _sources(now_ns=now_ns, books=books, store_yes=575.28 - 299.18)` and `replace(sources, permissions=replace(sources.permissions, sell_unconfirmed=unconfirmed))`. `drive`, `take_plan()`. Assert `plan.to_cancel == cancels` and `plan.to_place == ()`.

Results (`sell_plan.txt`, anchor 0.62): fixed and hold over → cancel v0 (the reprice to 0.63 follows the cancel ack, as in STEP-003). Fixed and `sell_unconfirmed=True` → no change, because `unconfirmed_hold` keeps the covering SELL (`src/strategy/quoting.py:405-408,516-517`). This is the "other gate suppresses the plan change" case. Today both give ask 0.62 × 5.684341886080802e-14 and an empty plan.

### 4.4 Diag row, `tests/test_trader_diag.py`

1. Move the `RestingOrder` in `_pair` (`:303-319`) to a module constant, for example `_OWN`, and use it in `_pair`. Add `from dataclasses import replace`.
2. New test, for example `test_strip_logs_float_dust_as_zero_foreign`, in the style of `test_bad_book_tail_does_not_change_or_raise` (`:192-218`): `monkeypatch.setattr("trader.diag.TRADER_WALLET_DIR", tmp_path)`; YES `apply_snapshot([(0.67, 80.71), (0.68, 136.84)], [(0.69, 50.0)], 1000.0)`; NO `apply_snapshot([(0.30, 10.0)], [(0.32, 10.0)], 1000.0)`; own `replace(_OWN, price=0.68, submitted_qty=205.88, filled_qty=69.04)`; `install_live_diagnostics(git_commit="abc")`; `books_from_md(..., now_ns=1, own_orders=(own,))`; `close_diagnostics()` in `finally`.
3. Take the row with `stage == "strip"` and assert:

```python
assert yes_walk["bids"] == [
    {"price": 0.68, "size": 136.84, "own": 136.83999999999997, "left": 2.842170943040401e-14, "foreign": 0.0},
    {"price": 0.67, "size": 80.71, "own": 0.0, "left": 80.71, "foreign": 80.71},
]
```

The expected rows are checked in `diag_row.txt`. `test_stream_records_the_fork_path_and_strip_stops` (`:80-189`) asserts only `row["price"]`, so the new key does not break it.

### 4.5 Red first

Add the tests, run them before the `src/` change. Expected: 10 fail (5 strip rows, 2 BUY plan cases, 2 SELL plan cases on the strip assertion, 1 diag), 1 pass (`negative-dust`). Then make the `src/` change; all 11 pass.

## 5. Behavior changes and risks

- A level that only our partly filled order holds no longer becomes the foreign best. The BUY rung reprices one tick down and the SELL reprices one tick up, as the audit's correct join says. The audit counted 13 such episodes on 9 of 104 maps (`a2-opus-exec-epsilon.md`).
- `TokenBook` sizes from the strip are rounded to 6 decimals. The core trace `BookUpdate` sizes change only in float noise. No reader uses them for a decision.
- Known limit: a venue level that shows a rounded-up size while our leftover has more decimals gives a positive sub-cent remainder (for example 0.00375). That is venue rounding, not float error. The audit saw only truncation (negative remainder). A larger tolerance is a non-goal.

## 6. Verification (no golden tests)

Run from the worktree. Never run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`.

1. Red run, before the `src/` change:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_session_core.py tests/test_trader_diag.py -k "float_dust or dust_rung or partly_filled_sell or raw_sub_cent_book" --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   (adjust `-k` to the final test names). Expect the counts in 4.5. `test_raw_sub_cent_book_keeps_the_resting_rungs` must still pass after the `_raw_signal` change.
2. Targeted run, after the change: the same files plus the other `books_from_md` users:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_session_core.py tests/test_trader_diag.py tests/test_trader_live_execution.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_book_guard.py tests/test_trader_host_resources.py tests/test_trader_two_sided_worker.py tests/test_strategy_core.py --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
3. Full suite without goldens, required before the commit:
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Baseline on `7b979aec`: 3174 passed. Expected: 3185 passed (11 new).
4. Typecheck: `uv run python -m basedpyright` → 0 errors.
5. Lint: `uv run python -m pre_commit run --files src/trader/book_strip.py tests/test_trader_session_core.py tests/test_trader_diag.py`.
6. Scope checks: `git diff --stat` shows only those 3 files; `git -C ../poly-maker status --short` is clean; no new comment, docstring, or suppression lines.
7. Commit on `live-a-audit-fixes`, no push. Suggested subject: `fix: STEP-008 - skip float dust when picking the foreign best`. Leave `passes` in `feature.json` unchanged; append the step entry to `progress.txt`.
