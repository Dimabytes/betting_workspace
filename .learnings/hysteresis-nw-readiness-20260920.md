# NW off + delta hysteresis: review, 2026-09-20

Read-only review of local esports-trader changes and VPS `sun`. No product files,
configuration, processes, or git refs were changed. The only written artifact is
this review. Tests and report calculations ran locally, not on the VPS.

## Comparable runs

Candidate for each game:
`validation_join_delta02_x015_cut480_nw999999_p35_nwoff-hyst015`.

Baselines: `data/backtests/{dota,lol}_maker/LIVE`, pointing respectively to
`validation_join_delta02_cut480_nw350_p35_research-val556-0919` and
`validation_join_delta02_cut480_nw350_p35_research-val934-0919`.

Baseline has 12 seeds; candidate has 3. All comparisons below use **the same
seeds 0, 1, 2**, not the mean of 12 baseline seeds against 3 candidate seeds.
`scripts/compare_backtests.py --expected-seeds 3` accepted both comparisons.
The only seed0 manifest differences are exit_abs_delta and max_abs_nw_delta_30;
models and input selections match within each game.

555 shared completed Dota maps and 932 shared completed LoL maps; no completed
maps outside the intersection. Both sides exclude the same 1 Dota / 2 LoL
terminated maps. Dollars are simulator results at **$100 per rung, 3 rungs**,
not projected live earnings. Net includes modeled maker rebate; CVaR and worst
map below are before rebate. Each number is a mean of the three seed metrics.

| Metric | Dota baseline | Dota candidate | LoL baseline | LoL candidate |
|---|---:|---:|---:|---:|
| PnL before rebate | 1,639.69 | 2,467.26 | 3,270.30 | 5,585.69 |
| Net PnL | 1,986.27 | 2,977.10 | 4,120.38 | 6,771.17 |
| Net gain | | +990.82 / +49.9% | | +2,650.79 / +64.3% |
| CVaR 5% | -43.73 | -53.67 | -74.52 | -86.86 |
| Mean worst-map loss | -107.09 | -107.32 | -193.10 | -196.50 |
| BUY turnover | 59,836 | 88,631 | 147,113 | 204,151 |
| Net per 100 bought shares | 1.9352 | 1.9588 | 1.6230 | 1.9126 |
| Traded maps | 340.33 | 400.00 | 591.33 | 679.67 |

Dota's extra PnL primarily follows extra turnover; average earnings per share
are almost unchanged. LoL improves both turnover and earnings per share.
The roughly unchanged worst loss is not a bound on future losses. Dota's
worst-map identity actually changes in seeds 0 and 1.

Paired pre-rebate deltas by seed: Dota +649.96 / +1,142.75 / +689.98;
LoL +2,244.47 / +2,754.59 / +1,947.12. Pooled per-map paired t results:
Dota p=0.0022, LoL p<0.0001. These are descriptive tests without adjustment
for the history of parameter selection or dependence between maps in a series.
Seeds vary execution cadence on the same maps; they are not new market samples.

All chronological thirds improve in all three seeds. Mean pre-rebate gain in
the late third: Dota +283.40 (2026-08-11..09-13), LoL +489.20
(2026-08-17..09-17). Narrower September-only slice: Dota 100 maps,
-6.48 / +277.12 / +64.04; LoL 172 maps, +386.56 / +566.31 / +354.44.
Neither slice is an untouched holdout.

Removing the five largest positive per-map mean deltas still leaves gains:
Dota +494.15 and LoL +1,912.65 before rebate.

## Dota ablations already available

| Configuration | Net PnL mean | CVaR 5% mean |
|---|---:|---:|
| Current: entry/close 2/2, NW 350 | 1,986.27 | -43.73 |
| Only hysteresis 2/1.5, NW 350 | 2,248.60 | -45.81 |
| Only NW off, 2/2 | 2,663.40 | -52.64 |
| Both: 2/1.5, NW off | 2,977.10 | -53.67 |
| Wider hysteresis 2/1, NW 350 | 2,217.12 | -49.82 |

Most of the Dota tail deterioration comes with removing NW. The 1.5 lower
threshold is a more attractive observed tradeoff than 1.0. No matching LoL
single-change ablations were found for this model/window.

Old experiments are not identical controls: NW-off was rejected on older
models with 1-cent entry and cutoff 540; September 11 hysteresis tested
1.5/1.0 and 2.5/1.0 against another baseline. See project docs
`docs/experiments/lol-nwoff.md`, `no-unwind-nwoff.md`, and
`ensemble/plan.md` lines 744 onward. Reconsidering a gate after model and
policy changes is reasonable, but repeated selection on the same validation
data remains a source of optimism. The run's own assumptions say early
stopping used the validation split.

## Actual production readiness

Local HEAD at inspection: 354e37a4, with uncommitted hysteresis changes.
VPS HEAD: 78f36c0. VPS working tree was clean and lacked exit_abs_delta.
Both DOTA_TRADING_MODE and LOL_TRADING_MODE in the running container are live.
Recorded core-trace headers confirm min_abs_delta=0.02, NW cap=350, and no
hysteresis field. Main Dota uses level_usdc=60; LoL and Oddin satellite use 5.
The container was running with active/recent sessions. No restart was performed.

Live and backtest share `follow300_policy` and the strategy core. This is good:
there is no need to implement a separate LoL/live hysteresis algorithm. But
the local factory still sets exit_abs_delta=MIN_ABS_DELTA=0.02 and NW=350.
CLI overrides exist only for backtests. Adding keys to trading.toml alone
will not select the new strategy.

`999999` disables the velocity threshold in these tapes; it does not disable
the missing_nw gate. Candidate summaries still record missing-NW blocked time.
The price-drop cooloff (10-cent radiant-mid drop / 10s lookback / 30s cooloff),
entry price floor/ceiling, spread cap, and 480s BUY cutoff remain in force.

## Code findings

1. **Fix before making this the shared default:**
   `src/backtest/run.py:393-396` unconditionally assigns
   exit_abs_delta=policy.min_abs_delta when the CLI exit flag is absent.
   Reproduced in memory: a factory policy with 0.015 becomes 0.02 in a normal
   backtest; an archive policy with 0.015 also becomes 0.02. Thus changing
   only the factory would silently leave default backtests without hysteresis
   and misreplay future archived hysteresis sessions. Preserve the selected
   policy absent an explicit override; validate effective thresholds afterward.

2. **Signal-transition semantics need a deliberate decision:**
   `_advance_delta_gate` is called from requote, not on each SignalUpdate.
   Reproduced: an open gate sees 1.4 then 1.8 cents within one 100ms debounce
   window and remains open, because only 1.8 is observed by the gate.
   A signal-based Schmitt gate should close on 1.4 and stay closed at 1.8.
   If changed, rerun candidates: this changes strategy behavior. Production
   feeds are usually sparse, so this reproduction does not establish a large
   historical PnL impact.

3. **Restart and trace coverage:** delta_gate_open is absent from
   StructuralCheckpoint and digest_state. Reproduced: true restores as false;
   states differing only in this bit have identical trace digests. Decide
   whether restart deliberately requires rearming at 2 cents or preserves
   the bit, then test that contract. This is a continuity/observability gap,
   not evidence that orders cannot run.

The current gate is a persistent permission based on absolute delta, shared
across episodes, not just permission to keep an existing BUY order. It can
permit new BUYs at 1.5–2 after a prior crossing of 2, including after another
gate unblocks. A sampled sign flip does not itself reset the bit. The lower
threshold does not mean SELL or stop-loss at 1.5 cents. These semantics should
be understood before altering implementation and comparing PnL.

## Verification and decision

Local focused tests: **233 passed**, two existing Nautilus/NumPy deprecation
warnings. Files: test_strategy_core, test_core_trace, test_backtest,
test_backtest_maker, test_compare_backtests. Additional read-only reproductions
above exposed cases not covered by those tests. No new full backtest was run.

Assessment: promising candidate for a controlled production trial after the
default/replay fix and explicit lifecycle decisions; insufficient evidence
to call it a permanently superior default. LoL evidence is stronger; Dota
offers a milder alternative in hysteresis-only. Define the acceptable tail
risk and evaluate fixed settings on genuinely later data rather than adding
another gate merely to restore the previous CVaR number.

To activate later: reconcile and commit the code changes, set one shared
policy for live/default backtests, verify both adapters and archive replay,
deploy to VPS, restart the process when appropriate, and verify new trace
headers contain entry 0.02 / close 0.015 / chosen NW cap. Neither publishing
code nor restarting was authorized by this review question, so neither was done.

Reference on repeated backtest selection:
https://escholarship.org/uc/item/4w1110bb
