# s2-impl — STEP-002
Status: FINAL

## What changed

Two-sided quoting is in the strategy core. `TwoSidedPolicy` makes `engine.step` call `requote_two_sided`. One post-only BUY per token, at the backtest prices and sizes. Both bids pull for 12 reasons in the spec order. A 1-tick move waits 300 ms. A move of 2 or more ticks cancels at once. No SELL is planned. The live trader does not call this yet. `LiveCore` stays `Follow300Policy`.

Files in `esports-trader` (`b86cdcdc`, amended from `1d380527`):

- `src/strategy/two_sided_quoting.py` (new): `two_sided_pull_reason`, `desired_bids`, `hold_one_tick_moves`, `reprice_hold_boundary_ns`, `requote_two_sided`
- `src/strategy/types.py`: `BlockReason` `"band"`, required `StrategyState.reprice_since_ns`
- `src/strategy/lifecycle.py`: `empty_state` sets `reprice_since_ns=(None, None)`
- `src/strategy/two_sided.py`: `tick_gap`
- `src/backtest/two_sided_strategy.py`: imports `tick_gap`, deletes `_tick_gap`
- `src/strategy/quoting.py`: `_books_unusable` → `books_unusable`, `_place_missing` → `place_missing` (dropped unused `policy`)
- `src/strategy/scheduling.py`: cadence helpers take `Policy`; two-sided deadline is debounce/fallback plus the hold boundary
- `src/strategy/engine.py`: `step` takes `Policy` and dispatches through `_requote`
- `tests/test_strategy_two_sided.py`: first quote, skew, fill, net cap, 12 pull reasons, 1-tick hold, 2-tick move, recovery chain, deadline

`feature.json` STEP-002 `passes` is still false. The brief says the orchestrator sets it after review.

## Deviations from the plan

Code follows the plan, including the three places the plan already differs from the feature text:

1. Quoting is `src/strategy/two_sided_quoting.py`, not `two_sided.py`. `policy.py` imports `two_sided`. Quoting needs `policy`. Putting the functions in `two_sided.py` would cycle at import.
2. Two-sided does not call `quoting.reconcile`. That path skips a reprice cancel when a BUY is partly filled and the token is under `min_order_size`. Two-sided never sells, so that guard would leave a stale bid. Matching is local. Placement still goes through `place_missing`.
3. `reprice_since_ns` is not written by the checkpoint or trace codecs. The clock is `time.monotonic_ns()`. A restored value means nothing after restart. Adding the field to the trace digest would change every Follow300 golden. `tests/test_strategy_core.py` and `tests/test_follow300_replay.py` are untouched.

One process deviation: the plan's last paragraph says set `passes: true`. The brief says not to. Left false.

## Commands

`PYTEST_N` was `10` in the shell. The plan says leave it unset, so these runs used `env -u PYTEST_N`.

```text
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_two_sided.py tests/test_strategy_core.py tests/test_follow300_replay.py \
  tests/test_core_trace.py -q
```

129 passed, 2 warnings, 310.74s. Exit 0.

```text
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_strategy_scheduling.py tests/test_strategy_recovery_quotes.py \
  tests/test_strategy_late_fills.py tests/test_strategy_budget.py tests/test_strategy_imports.py \
  tests/test_kill_gate.py tests/test_mid_spike.py tests/test_trader_core_recovery.py \
  tests/test_trader_session_core.py tests/test_trader_core_persistence.py \
  tests/test_trader_core_state_report.py tests/test_trader_engine_seams.py -q
```

277 passed, 2.30s. Exit 0.

```text
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided
```

`two_sided ok`. Exit 0.

```text
git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py
```

Empty. Exit 0.

```text
uv run python -m basedpyright
```

0 errors, 0 warnings, 0 notes. Exit 0.

```text
make lint
```

On the staged step files: ruff check, ruff format, basedpyright, trailing whitespace, end-of-file, large-files all passed. Exit 0. The commit hook ran the same hooks and passed.

The full serial suite was not run. The plan leaves that to STEP-010 unless time allows.

## Commit

`b86cdcdc7a704cf9652c46ffe140242831515bd9` on `esports-trader` `main`. Amends `1d380527aebad8b68fd66147b9eabd4cd7745880`. Not pushed.

`Quote two-sided bids in the strategy core behind TwoSidedPolicy.`

Not pushed. Nothing committed in `betting_workspace`. `progress.txt` appended. Untracked `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-regress-*` left unstaged.

## Open issues

- `LiveCore`, checkpoint export, `session_budget`, and `dust_sweep` still assume `Follow300Policy`. STEP-006.
- `entry_block_from_reason("band")` maps to `EntryBlock.NO_EDGE`. Add `EntryBlock.BAND` only if the B journal needs it.
- The bid is not capped below the best ask. The backtest does not cap it either. A post-only reject is the backstop if a snapshot crosses.
- Size uses `share_floor`, not the backtest's `round(..., 2)`. Difference is at most 0.01 share.
- After a B restart, a late fill of an old venue order pulls both bids (`ownership_unresolved`) until resolved. STEP-006 restart test.
- A cancelled live BUY becomes `gone` and waits for `BuySettled` before replacement. The gap is the cancel round trip plus the terminal proof. A replacement that would push the bid-pair over 99 ticks waits until the opposite order leaves `state.orders`.

## Review fixes

Amended the step commit. Nothing skipped.

### s2-rev

1. **major — opposite bid still priced while one side is replaced. Fixed.** `_reconcile_bids` drops a candidate when its ticks plus any opposite BUY still in `state.orders` (canceling, unknown, or gone included) exceed `max_bid_sum_ticks`. Same-token occupancy is unchanged. Tests: fair 0.15 → 0.85, both orientations, `CancelAck` of the side that would reprint 0.82 beside a live 0.82, and the `CancelTimeout` / `CancelUnsettled` / `BuySettled` chain. No place until the opposite order is gone; then 0.82 and 0.12.
2. **minor — `clock.now_ns == 0` looked fresh inside 16s. Fixed.** The two-sided clock gate treats `now_ns == 0` as `stale_signal` before `is_fresh`. `is_fresh` itself is unchanged. Test starts from `GameClock(now_ns=0, game_second=0, ...)`, the `LiveCore` initial clock: no places on a forced Wake with fresh books, then two 0.47 bids after a nonzero `ClockUpdate`.

### s2-com

1. **`scheduling.py` past-boundary comment. Fixed.** Deleted. The `if boundary <= now_ns: return now_ns` line is the whole statement.
2. **Reanchor comment and hidden asymmetry. Fixed.** Deleted the comment. `armed_deadline_ns` now walks `waking` (past boundary returns now) and `tightening` (reanchor only tightens a future deadline). Same Follow300 results; the eight-map replay passed.
3. **`_should_reprice` docstring. Fixed.** Deleted. The hold rule lives in `hold_one_tick_moves` and `REPRICE_NOW_TICKS` / `REPRICE_HOLD_NS`.
4. **Stale test-module docstring. Fixed.** Deleted. The file is engine tests plus the original math checks.

### Checks after the fixes

`PYTEST_N` unset.

```text
env -u PYTEST_N PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --group backtest python -m pytest \
  tests/test_strategy_two_sided.py tests/test_strategy_core.py \
  tests/test_follow300_replay.py tests/test_core_trace.py \
  tests/test_strategy_scheduling.py tests/test_strategy_recovery_quotes.py \
  tests/test_strategy_late_fills.py tests/test_strategy_budget.py \
  tests/test_strategy_imports.py tests/test_kill_gate.py tests/test_mid_spike.py \
  tests/test_trader_core_recovery.py tests/test_trader_session_core.py \
  tests/test_trader_core_persistence.py tests/test_trader_core_state_report.py \
  tests/test_trader_engine_seams.py -q
```

411 passed, 2 warnings, 299.97s. Exit 0. Includes the 5 new tests and the eight-map replay.

```text
uv run python -m basedpyright
```

0 errors, 0 warnings, 0 notes. Exit 0.

```text
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided
git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py
```

`two_sided ok`. A test diff empty. Exit 0.

```text
make lint
```

Staged files: ruff check, ruff format, basedpyright, trailing whitespace, end-of-file, large-files passed. The amend hook passed the same set. Exit 0.

Commit: `b86cdcdc7a704cf9652c46ffe140242831515bd9`.
