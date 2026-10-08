# s1-grok — STEP-001
Status: FINAL

## Plan

Move the two-sided quote math into `src/strategy/two_sided.py` and add `TwoSidedPolicy` without changing backtest behavior.

1. Regression on HEAD `ac475db2` before any edit: 20 wave-1 maps, `--strategy two-sided`, `--name ts-regress-before`, queue position on. Strict queue is the default (`queue_position` true). Do not pass `--no-queue-position`. CLI defaults stay, including net max 100. `_read_match_ids_file` already skips `#` lines, so `wave1-maps.txt` is passed as-is.
2. New `src/strategy/two_sided.py` owns `TICK`, `MAX_BID_SUM_TICKS`, `ORDER_SHARES = 20`, `NET_MAX_SHARES = 50`, `HALF_SPREAD_TICKS = 3`, `SKEW_PER_SHARE = 2e-4`, `BAND_HI = 0.90`, `QUOTE_FROM_SECOND = -60`, `REPRICE_HOLD_NS = 300_000_000`, `REPRICE_NOW_TICKS = 2`, plus `BidTicks`, `price_bids`, `inventory_skew`, `scale_order_size`.
3. `backtest/two_sided.py` keeps `Inventory`, `apply_buy`, `merge_pairs`, `settle_cash`, and the self-check. It imports the names that file still calls. `backtest/two_sided_strategy.py` imports `QUOTE_FROM_SECOND`, `REPRICE_HOLD_NS`, `REPRICE_NOW_TICKS`, `TICK`, and the quote functions from `strategy.two_sided`. No second definition remains.
4. `policy.py`: frozen `TwoSidedPolicy`, `Policy = Follow300Policy | TwoSidedPolicy`, `two_sided_policy(*, debounce_ms, fallback_timer_s)`. Cadence is required. Quote knobs are filled from the module constants, same pattern as `follow300_policy`. Core signatures stay `Follow300Policy` until STEP-002.
5. `tests/test_strategy_two_sided.py` pins the backtest self-check values for `price_bids` and `scale_order_size`, plus `inventory_skew(fair=0.5, net_shares=50, skew_per_share=2e-4) == 0.01`.
6. Re-run the same 20 maps as `ts-regress-after`. Fills must match row for row. Then pytest, `make lint` (ruff plus basedpyright), `passes: true`, `progress.txt`, one commit on `esports-trader` `main`.

Decision: do not pass `--net-max-shares 50`. Resolved questions say this step does not change backtest CLI defaults. `NET_MAX_SHARES = 50` is the live constant only.

Decision: `backtest/two_sided.py` does not import `inventory_skew`, `QUOTE_FROM_SECOND`, or the reprice constants. That file no longer calls them. An unused import fails ruff F401. `two_sided_strategy.py` imports the constants it uses. `run.py` imports `BAND_HI` from `strategy.two_sided`.

## What changed

Commit `8dadf747` (`8dadf7479b7d245d23ee1728ef401e335954103e`) on `esports-trader` `main`. Not pushed. 6 files, +181 / −68.

- `src/strategy/two_sided.py` (new) — constants and quote functions
- `src/strategy/policy.py` — `TwoSidedPolicy`, `Policy`, `two_sided_policy`
- `src/backtest/two_sided.py` — quote math removed; self-check imports it
- `src/backtest/two_sided_strategy.py` — imports quote math and reprice constants from `strategy.two_sided`
- `src/backtest/run.py` — `BAND_HI` import moved
- `tests/test_strategy_two_sided.py` (new)

Workspace only (not committed; the brief says commit in E, not in W):

- `tasks/two-sided-live-b/feature.json` — STEP-001 `passes: true`. Later steps stay false.
- `tasks/two-sided-live-b/progress.txt` — appended

## Commands

Regression before, HEAD `ac475db2`, exit 0, 90s:

```
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run \
  --match-ids-file /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/work/wave1-maps.txt \
  --strategy two-sided \
  --name ts-regress-before
```

Path: `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-regress-before/seed0`

Log: `fleet/work/s1-grok/ts-regress-before.log`

```
join/queue

  model                     20261005T081730Z
  trainlag                  10s
  btlag                     10s
  seed                      0
  h                         3 t
  size                      20 sh
  band                      [0.10, 0.90]
  spike                     off
  queue                     on

RUN
  eligible                  n/a
  selected                  20
  completed                 20
  terminated                0
  traded matches            20
  no-trade                  0
  traded + no-trade         20
  buy fills                 1349  (67.5 / traded)
  sell fills                0
  turnover                  $8038.00
  median clip               $7.80
  fill rate                 4.9%

SIGNALS
  schedule:grid             15
  schedule:oddin            5
  excluded                  0
  model research            15
  model research-noxp       5

PNL
  span                      25d
  cash est (fills only)     $35.45
  deposit w/ reserves       $49.99
  deposit w/ res @close     $49.99
  peak reserved             $37.74
  pnl before rebate         $160.03
  ROI before rebate         451%
  rebate                    $26.24
  taker fee                 $0.00
  merge usdc                $8052.40
  leftover settlement       $145.63
  pnl with rebate           $186.27
  final balance with rebate $221.72
  ROI with rebate           525%
  net / deposit w/ reserves 3.726
  pnl per match             $8.001
  pnl per match with rebate $9.313
  median match pnl          $7.781
  pnl per share             0.953¢
  pnl per share with rebate 1.110¢
  maps at once              2
```

The balance chart and the rest of that terminal report are in the log.

Regression after, same flags, `--name ts-regress-after`, exit 0, 101s. Path: `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-regress-after/seed0`. Log: `fleet/work/s1-grok/ts-regress-after.log`.

Comparison (`pyarrow.Table.equals`, verified):

| file | rows | equal | bytes |
| --- | --- | --- | --- |
| fills.parquet | 1349 | yes | 143801 |
| results.parquet | 20 | yes | 33738 |
| quote_events.parquet | 114533 | yes | 1432887 |

Fills match on every column and every value, including `order_id`.

Self-check: `PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided` printed `two_sided ok`.

Pytest (env `PYTEST_N=10`, so xdist): `tests/test_strategy_two_sided.py`, `tests/test_strategy_core.py`, `tests/test_follow300_replay.py`, `tests/test_strategy_imports.py`, `tests/test_backtest.py`. **117 passed**, 20 warnings, 245s. There is no separate two-sided backtest test module; `test_backtest.py` is the backtest suite that loads `run.py`.

`make lint` on the staged files, exit 0: ruff check, ruff format, basedpyright, trailing whitespace, end-of-file, large files. basedpyright is the typecheck (`pass_filenames: false`, whole project).

## Commit

`8dadf7479b7d245d23ee1728ef401e335954103e` on `esports-trader` `main`. Message: "Move two-sided quote math into strategy so live and backtest share it." Branch is ahead of `origin/main` by 13. Not pushed.

## Open issues

- `TwoSidedPolicy` is not read by `engine.step` yet. STEP-002 switches the core.
- Backtest CLI default `net_max_shares` is still 100. Live `NET_MAX_SHARES` is 50. Intentional for this step.
- The two regression dirs are untracked local artifacts under `data/backtests/dota_maker/`. They were not part of the code commit. STEP-010 compares against `ts-regress-before` on this disk.
- `feature.json` and `progress.txt` are dirty in `betting_workspace` and were not committed.
