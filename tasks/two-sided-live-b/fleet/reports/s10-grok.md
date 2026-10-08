# s10-grok — STEP-010
Status: FINAL

## Plan

Release gate on E `main`. No plan file. Decisions are in this report.

1. Confirm the two reported failures on HEAD and fix each root cause with one commit. Shared code was not changed, so service A behavior stays.
2. Serial pytest (`env -u PYTEST_N`), basedpyright, ruff check, ruff format --check, `backtest.two_sided` self-check. `NET_MAX_SHARES` stays 50.
3. The serial suite includes the Follow300 replay goldens, `tests/test_strategy_core.py`, and the recovery tests. That is the STEP-004 proof.
4. Same 20-map command as `ts-regress-before`, name `ts-regress-final`. Compare fills, results, and quote_events with `pyarrow.Table.equals`.
5. Compose with `--services` and `--no-interpolate` only. Diff the `live` service and `config/trading.toml` against `ac475db2`. `poly-maker` stays clean.
6. Commit the regression trees. `data/backtests` is a tracked record. `quote_events.parquet` is gitignored (`.gitignore:19`). Do not delete the files.
7. Correct the vps-trader runbook where the copied plan text differs from the code.
8. Set STEP-010 `passes: true` only after the checks above are green. Append `progress.txt` with the owner action list at the end. One workspace commit. No push.

## Fixes

`d86e516d10d86f930973f4a4252b3d2168d0fdd5` — Set mode on the host run-path stub so the user-stream bind test reaches boot scan.

Root cause, verified on HEAD before the edit: `tests/test_trader_unsettled_buy_recovery.py::test_user_stream_is_bound_on_the_host_run_path` builds `WalletHost` with `object.__new__` and `_games = ()`. `WalletHost.run` calls `close_ended_oddin_archives(..., self._mode)` after `_bind_order_terminal` and before `_boot_scan` (`src/trader/wallet_host.py:1253-1262`). The stub had no `_mode`, so `run` raised `AttributeError` and never reached the stubbed `RuntimeError("bound")`. Production `__init__` always sets `_mode` (`wallet_host.py:710`). `data/trader` exists on this machine, so the test also points `trader.wallet_host.TRADER_DIR` at `tmp_path`. Otherwise the Oddin close scan would walk the real tree. Re-run: passed in 2.31s with the home test.

`37fcb72e638b96ca08752b70da1c2c5232f62f38` — Stop expecting доступно on the home page when available cash equals collateral.

Root cause: `357cb989` hides the line unless available cash differs from collateral (`src/dashboard/home_strip.py:215-219`, `src/dashboard/home_view.py:167-169`). The unit test `test_strip_portfolio_and_available_visibility` was updated then. `tests/test_dashboard_app.py::test_home_renders_all_sections` still required `доступно`. The app fixture has collateral and no buy reserve, so the numbers match and the line is absent. The test now requires `портфолио` and rejects `доступно`.

`a7d72621b07268808628980ba45ae94ad174e45b` — Record the two-sided wave-1 regression runs used by the live-b gate.

Twelve files: fills, results, manifest, summary for `ts-regress-before`, `ts-regress-after`, and `ts-regress-final`. The end-of-file hook added a trailing newline to the json files on the first attempt; that attempt did not create a commit. The second attempt committed the fixed files. `quote_events.parquet` is ignored and stays on disk.

## Checks

### Serial pytest

Shell had `PYTEST_N=10`. Command:

```
env -u PYTEST_N PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --tb=line
```

Result: `3069 passed, 9 warnings, 2 subtests passed in 422.84s (0:07:02)`. Process exit 0. One pytest process, no xdist workers. Log: `fleet/work/s10-grok/pytest-serial.log`.

This run includes `tests/test_follow300_replay.py`, `tests/test_strategy_core.py`, and the recovery tests. No shared production file changed in this step.

### basedpyright, ruff, self-check

```
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python -m basedpyright
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.two_sided
```

ruff check: all checks passed. ruff format: 456 files already formatted. basedpyright: 0 errors, 0 warnings, 0 notes. Self-check printed `two_sided ok`. `src/strategy/two_sided.py:8` is `NET_MAX_SHARES = 50`.

### Regression

```
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run \
  --match-ids-file /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/work/wave1-maps.txt \
  --strategy two-sided \
  --name ts-regress-final
```

Exit 0, 86s. Log: `fleet/work/s10-grok/ts-regress-final.log`.

`pyarrow.Table.equals` of `seed0` against `ts-regress-before`:

| file | rows | equal | bytes |
| --- | --- | --- | --- |
| fills.parquet | 1349 | yes | 143801 |
| results.parquet | 20 | yes | 33738 |
| quote_events.parquet | 114533 | yes | 1432887 |

Terminal report (balance chart is in the log):

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

FILLS through RISK in the same log match the STEP-001 before-run: buy 30s 0.712¢, buy 300s 1.287¢, loss rate 45.0%, worst match −$17.663, lowest capital point $31.92.

### Compose

```
docker compose config --services
docker compose config --no-interpolate --format json | jq '{DOTA_STRATEGY: .services.live_b.environment.DOTA_STRATEGY, stop_grace_period: .services.live_b.stop_grace_period, volume_sources: [.services.live_b.volumes[].source], compress_volume_sources: [.services.compress.volumes[].source]}'
```

Services printed: `compress`, `live`, `live_b`, `paper`. Set is `{live, live_b, paper, compress}`.

`DOTA_STRATEGY` is `two_sided`. `stop_grace_period` is `240s`. Volume sources resolve to the dota archive, `data/trader_live_b`, `data/new_model`, `src`, `config_b`, and `.git`. Compress sources include `data/trader_live_b`. No interpolated config was printed.

### Diffs against ac475db2

`git diff --exit-code ac475db2 HEAD -- config/trading.toml` exit 0.

The `live:` service block in `compose.yaml` is byte-identical to `ac475db2` (49 lines). `live_b` is the added service. Its `stop_grace_period` is `240s` (`compose.yaml:70`).

`git -C ../poly-maker status --short` is empty.

### Runbook

Compared `.shared-skills/vps-trader/SKILL.md` (from `7fee213`) with the code on E HEAD.

Left as written, because the code matches:

- Start line `trader wallet: strategy=%s signature_type=%d funder=%s`, funder `cfg.secrets.browser_address` (`src/trader/host_resources.py:178-183`).
- Empty tiers: `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist`, raised in `select_title_whitelist` before `engine.start` (`src/trader/wallet_host.py:164-168` and `1242-1250`).
- Discovery info line `discovery skip reason=%s cid=%s title=%r` with reason `title_whitelist` the first time a cid is skipped; later passes are debug (`src/trader/discovery.py:246-260`).
- A late fill the fresh core does not own sets `ownership_unresolved` and pulls both bids (`tests/test_trader_two_sided_worker.py::test_late_fill_of_a_pre_restart_order_pulls_both_bids`). Process start calls `gateway.cancel_all` (`../poly-maker/src/polymaker/engine.py:223-225`). Not edited.
- `stop_grace_period` 240s. Not edited.

Changed:

- SKILL.md after-start paragraph now quotes the non-BLAST refusal: `DOTA_STRATEGY=two_sided title whitelist must be BLAST Slam only, got <names>` (`wallet_host.py:169-172`). The old sentence only described an empty name list.
- The same paragraph now includes `cid` and `title` on the discovery skip, and says repeats are debug.
- `log-map.md` wallet line: the cash sum is MATCHED+CONFIRMED+MERGED (`src/trader/wallet_store.py` ledger sum; `apply_merge` is only called from `src/trader/pair_merge.py`). A writes no MERGED rows.

`fleet/work` largest file is 833K. E's large-file hook limit is 4096 KB. W has no pre-commit hook. Nothing in `tasks/two-sided-live-b/` is over that limit.

## Git

E `main`, not pushed. `git status --short` empty. `git rev-list --count origin/main..main` is 26.

```
a7d72621 Record the two-sided wave-1 regression runs used by the live-b gate.
37fcb72e Stop expecting доступно on the home page when available cash equals collateral.
d86e516d Set mode on the host run-path stub so the user-stream bind test reaches boot scan.
2275bbb2 Pick the Dota strategy from DOTA_STRATEGY: two-sided worker, adapter merge, and a BLAST Slam-only title whitelist.
5ed8a8a3 Keep clean-restart order ids and drain an in-flight merge before shutdown.
f605a6b9 Stop merge HTTP at the absolute deadline during header reads.
2ebea5e6 Count Polymarket MERGE cash in the day fold and point summarize at another live tree.
77a85364 Run two-sided maps in a TwoSidedWorker: book fair, clean restart, final merge.
a58d19c9 Add wallet B: config_b and the live_b compose service for the two-sided trader.
ec9ebe90 Merge YES+NO pairs through the pUSD adapter with phase-classified outcomes.
7b1b6990 Deliver pair merges to the core as a Merged event through the outbox.
c779fa21 Record pair merges in the wallet ledger as MERGED rows.
b86cdcdc Quote two-sided bids in the strategy core behind TwoSidedPolicy.
8dadf747 Move two-sided quote math into strategy so live and backtest share it.
ac475db2 Recapture follow300 smoke goldens on the 20261005 catalogs and the LoL 10-rung cap.
01d33aca Record the two-sided n50 runs: same PnL, smaller worst map.
d59dc77c Track the per-market fee terms the two-sided backtest needs.
07769361 upd next steps
8f4dca0f upd next steps
70ae5c03 Merge branch 'main' of github.com:Dimabytes/esports-trader
1a155344 Let the merge probe run a deposit wallet through the relayer.
67f4fa76 Record the two-sided maker runs: wave 1 grid and the 307-map archive.
8a3596c7 Add a merge probe for the pUSD collateral adapter.
53d1f46a Add the two-sided maker backtest with merge accounting.
8207bc65 Document ideal Dota feed experiment results
e845325f Record the live vs backtest gap experiment: no execution gap at live's rung.
```

W gate commit `385059baf641cda588c7a5904138cc22c250fa45` adds the task folder, the runbook fixes, and this report. The commit on top of it only inserts that hash into this file. Not pushed. `git log --oneline origin/main..385059ba`:

```
385059ba Record the two-sided live-b gate and correct the wallet B runbook.
7fee213 Describe wallet B in the VPS trader skill: day PnL, restart gate, and deploy.
fad260b Record the n50 result: net cap 50 for the live pilot.
f836ae8 Merge branch 'main' of github.com:Dimabytes/betting_workspace
e922738 Keep the two-sided maker investigation: plan, fleet reports, wave 2 runbook.
e8844b9 Keep the live vs backtest gap report: no gap at live's clip.
```

`poly-maker` status empty. No push.

## Open issues

- Push and the VPS deploy are not done. No SSH, no `docker compose up/build/run`.
- `stop_grace_period` 240s covers one in-flight merge plus the fence and the drain. Two maps merging at once, or SIGTERM during the final merge, are still outside that window. The runbook gate is `restart_check SAFE`.
- Day-fold `MERGE` cash was not checked against a live Polymarket activity row.
- `quote_events.parquet` matched on disk and is gitignored, so it is not in `a7d72621`.
- `9 × $20 = $180` is still not a limit the two-sided core enforces. The runbook says so.
