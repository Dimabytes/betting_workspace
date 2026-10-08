# bt-sweep — two-sided param backtests
Status: FINAL

Code under test: esports-trader `a7d72621`. Record commit: `903e0e57` on `main` (data only, working tree clean). No code edits. Framework pin `c76e77af` matches the n50 manifests. Comparison script: `fleet/work/bt-sweep/compare.py`. Driver: `fleet/work/bt-sweep/run_modes.sh`.

## Commands

Copied from `reports/2026-10-07-two-sided-merge/RUNBOOK-wave2.md` (`--validation --archives-only --strategy two-sided`) and the n50 logs (`shard 0/5` … `4/5`, then `--merge-shards 5`). Defaults this HEAD still stamps when the flag is omitted (`src/backtest/run.py` `_two_sided_settings`): half-spread 3, size 20, skew 0.0002, merge-min 20, mid-spike off. n50 passed only `--net-max-shares 50` on top of that. The free queue adds `--no-queue-position`.

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
export PYTHONPATH=src:../prediction-market-backtesting
uv run --group backtest python -m backtest.run \
  --validation --archives-only --strategy two-sided \
  --name <NAME> <KNOB> [--no-queue-position] --shard i/5
uv run --group backtest python -m backtest.run \
  --validation --archives-only --strategy two-sided \
  --name <NAME> <KNOB> [--no-queue-position] --merge-shards 5
```

Modes ran one at a time, five shards inside each mode. Wall clock 05:49:06Z–06:18:32Z.

| run | name | knob vs n50 | window (UTC) |
| --- | --- | --- | --- |
| 1 strict | `ts-full-n30` | `--net-max-shares 30` | 05:49:06–05:55:38 |
| 1 free | `ts-full-n30-nq` | `--net-max-shares 30 --no-queue-position` | 05:55:38–06:03:00 |
| 2 strict | `ts-full-n50-g4e-4` | `--net-max-shares 50 --skew-per-share 0.0004` | 06:03:00–06:10:02 |
| 2 free | `ts-full-n50-g4e-4-nq` | `--net-max-shares 50 --skew-per-share 0.0004 --no-queue-position` | 06:10:02–06:18:32 |

Run dirs (each holds `_archive/`):

- `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n30`
- `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n30-nq`
- `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n50-g4e-4`
- `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n50-g4e-4-nq`

Logs: `data/backtests/dota_maker/_logs/<name>-shard{0-4}.log` and `-merge.log` (gitignored).

## One-difference proof

`compare.py` diffs every manifest key. Each new run differs from its n50 twin by exactly one field. Selected-match hash, model hash, framework commit, size, half-spread, merge-min, mid-spike, and the queue flag are unchanged.

| candidate | only manifest change |
| --- | --- |
| `ts-full-n30` | `net_max_shares: 50.0 -> 30.0` |
| `ts-full-n30-nq` | `net_max_shares: 50.0 -> 30.0` |
| `ts-full-n50-g4e-4` | `skew_per_share: 0.0002 -> 0.0004` |
| `ts-full-n50-g4e-4-nq` | `skew_per_share: 0.0002 -> 0.0004` |

## How the numbers are defined

Per-map PnL = `engine_pnl + maker_rebate` (307 maps, inner join on `match_id`). p5 is the linear percentile from `src/backtest/postprocess.py` (`q * (n-1)`). Leftover and pairs follow `decompose_two_sided.py`: leftover = tail settlement cash minus the tail's average fill cost; pairs = total PnL − leftover. Paired t is the one-sample t of the per-map PnL difference (candidate − n50, ddof=1).

The runbook's n50 worst-map −$48 / −$38 and p5 −$10.4 are the pre-rebate engine figures, rounded. Tables below use the with-rebate series, which is what the paired total sums to. The terminal "worst match" line is the engine figure; it moves the same way.

Noise rule, as stated: a cohort gap under ~$400 with paired |t| < 1.5 is noise. A cell is taken only when the move has the same sign on both queues. The pilot goal is the n50 goal: a smaller worst map without giving up profit.

## n50 baseline

| metric | strict `ts-full-n50` | free `ts-full-n50-nq` |
| --- | ---: | ---: |
| PnL with rebate | 1610.25 | 3820.77 |
| worst map | -46.21 | -36.23 |
| p5 | -10.10 | -9.61 |
| leftover | -1398.10 | -1367.39 |
| pairs | 3008.35 | 5188.15 |
| terminal worst match | -47.69 | -38.12 |

## Run 1 — net cap 30

Strict queue:

| metric | n50 | n30 | delta |
| --- | ---: | ---: | ---: |
| PnL with rebate | 1610.25 | 1655.18 | +44.93 |
| worst map | -46.21 | -32.94 | +13.28 |
| p5 | -10.10 | -7.57 | +2.52 |
| leftover | -1398.10 | -1014.52 | +383.58 |
| pairs | 3008.35 | 2669.70 | -338.65 |
| paired t |  |  | 0.61 |

Free queue:

| metric | n50-nq | n30-nq | delta |
| --- | ---: | ---: | ---: |
| PnL with rebate | 3820.77 | 3715.77 | -105.00 |
| worst map | -36.23 | -28.86 | +7.37 |
| p5 | -9.61 | -6.42 | +3.19 |
| leftover | -1367.39 | -936.50 | +430.88 |
| pairs | 5188.15 | 4652.27 | -535.88 |
| paired t |  |  | -1.40 |

Terminal worst match: strict −$34.226 (was −$47.688), free −$31.786 (was −$38.118).

**Verdict: win.** Both queues: worst map and p5 improved, leftover improved (~+$384, +$431), pair income fell (−$339, −$536). PnL deltas +$45 (t=0.61) and −$105 (t=−1.40) are inside the noise band, so profit is not lost. Same pattern as 100→50: the tail shrinks, pairs give some back, the total holds. Free-queue t=−1.40 sits near the 1.5 line; the dollar gap is $105, so the stated rule still calls it noise.

## Run 2 — skew 0.0004 at net cap 50

Strict queue:

| metric | n50 | g4e-4 | delta |
| --- | ---: | ---: | ---: |
| PnL with rebate | 1610.25 | 1635.37 | +25.12 |
| worst map | -46.21 | -34.90 | +11.31 |
| p5 | -10.10 | -9.61 | +0.49 |
| leftover | -1398.10 | -1445.36 | -47.26 |
| pairs | 3008.35 | 3080.73 | +72.38 |
| paired t |  |  | 0.33 |

Free queue:

| metric | n50-nq | g4e-4-nq | delta |
| --- | ---: | ---: | ---: |
| PnL with rebate | 3820.77 | 3850.99 | +30.22 |
| worst map | -36.23 | -35.01 | +1.22 |
| p5 | -9.61 | -9.52 | +0.09 |
| leftover | -1367.39 | -1366.23 | +1.16 |
| pairs | 5188.15 | 5217.21 | +29.06 |
| paired t |  |  | 0.38 |

Terminal worst match: strict −$36.658 (was −$47.688), free −$37.199 (was −$38.118).

**Verdict: not a win.** PnL is noise on both queues (+$25 t=0.33, +$30 t=0.38). The strict worst map improved by $11, but the free worst map moved $1.22 and free p5 moved $0.09. Leftover, the tail this knob was supposed to shrink, got worse on the strict queue (−$47) and was flat on the free queue (+$1). Pair income rose by $72 and $29, both under the noise bar. The two queues do not show the same tail improvement, so the knob is not taken.

## Run 3

Not started. The brief runs n30+g4e-4 together only when both earlier runs win. Run 2 did not.

## Decision

Take net cap 30: on both queues the worst map and p5 improved and PnL stayed inside the noise band. Do not take skew 0.0004: the leftover tail did not shrink on both queues. The combined cell was not run.

`903e0e57` records the four run dirs (fills, results, manifest, summary, DONE). `quote_events.parquet` stays on disk and is gitignored. `git status` on esports-trader `main` is clean.
