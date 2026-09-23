# Orchestrator notes (Claude), 2026-09-23

## O1. LoL grid-v1 backtest acts with zero feed delay (verified from code + catalogs)

- `src/lol/05_prepare_dataset.py:298-318` `join_market_rows`: row `state_ts_us = slot.state_wall_us`
  (livestats frame wall time), `market_p_radiant` = as-of mid at `state_wall_us + 0`
  (since `332e1c17`), label = as-of mid at `state_wall_us + 300`.
- `src/backtest/run.py:1405` LoL `RunSelection(lag_seconds=0)`; Dota uses `BACKTEST_LAG_SECONDS=10`.
- `src/backtest/signals.py:233-297` `build_match_signals`: decision timestamp = `state_ts_us`,
  model `second = row.second - lag_seconds` (LoL: -0), market input = row `market_p_radiant`.
  → LoL grid-v1 decision happens at the livestats frame wall time, no source delay.
- Dota: `src/prepare_dataset/prepare_dataset.py:154` (train: market row at `state.second + lag`),
  `:198-225` `join_validation_rows` (validation: market second M joined with state M-10).
  → Dota backtest decides at wall(M) with state from M-10.
- Live LoL: `src/trader/grid_feed.py:166-170` `second = live_clock_seconds(board, age) - table.feed_delay`
  (feed_delay 8 from the `series_table` frame), market = current book at receipt.
- Before `332e1c17` (2026-09-19): LoL rows had `market_p_radiant` at `state_wall_us + 10` while the
  backtest still decided at `state_wall_us` → the model input was a market mid from 10 s in the future
  relative to the decision time (true look-ahead). After: no future mid, but still zero delay.
- Training after `332e1c17`: state and market at the same instant; live state is ≥ 8 s older than the market.

## O2. Catalog split by feed path (LIVE backtests, seeds 0-2)

| game | path | maps | PnL/map (engine, pre-rebate) | BUY mk30 | BUY mk300 | PnL / BUY $ |
|---|---|---:|---:|---:|---:|---:|
| LoL | grid-v1 (synthetic, Jun 4..Sep 17) | 782 | $5.99 / $5.81 / $5.37 | +0.62 / +0.66 / +0.46¢ | +2.98..3.29¢ | 2.75% |
| LoL | GRID archive schedule (Sep 1..19) | 163 | $2.99 | +0.09¢ | +2.21¢ | 1.19% |
| Dota | grid-v1 (Jun 4..Sep 13) | 456 | $3.52 / $2.75 / $4.53 | −0.45 / −0.32 / −0.46¢ | +1.05¢ | 2.2% |
| Dota | GRID archive schedule | 126 | $3.65 | +0.06¢ | +1.97¢ | 4.2% |

Schedule path = real GRID receipt times + livestats features at the GRID game second
(`src/backtest/signals.py:313-420`, `src/archive_index/schedule.py:168-176`).
Confounded by period (schedule maps are Sep only).

## O3. Live PnL per BUY $ (local tapes, mode=live, session_end present)

| window | game/src | maps | traded | BUY $ | PnL pre-rebate | per BUY $ | + rebate est. |
|---|---|---:|---:|---:|---:|---:|---:|
| since 09-01 | dota grid | 208 | 135 | 23 536 | +549.34 | +2.33% | +2.89% |
| since 09-01 | dota oddin | 45 | 36 | 5 911 | +262.87 | +4.45% | +4.99% |
| since 09-01 | lol grid | 285 | 198 | 12 591 | −441.58 | −3.51% | −2.94% |
| since 09-18 | lol grid | 43 | 23 | 681 | −0.99 | −0.14% | +0.33% |
| 09-01..09-19 | lol grid | 257 | 185 | 12 055 | −433.10 | −3.59% | −3.02% |

Script: scratchpad `live_per_dollar.py` (copy below if needed).

## O4. Offline delay sensitivity (research models, 400 validation maps each)

`work/orchestrator/delay_sensitivity.py`: state from row S, market input and label from row S+d.
LoL edge (|pred|≥2¢, sign·Δmid): d0 3.16¢ → d8 2.74¢ → d10 2.69¢ → d20 2.49¢.
Dota (d0 already = 10 s delay): 3.19¢ → d10 2.96¢.
Mid-to-mid edge loses ~15% at 10 s. The bigger delay cost is in execution (stale quotes), which
the grid-v1 vs schedule split in O2 shows.

## O5. Same maps: backtest (current research model, GRID schedule) vs live (model of that day)

`work/orchestrator/same_map_parity.py`, joined by `condition_id`, live mode only.

| game | maps | live PnL / BUY $ | backtest PnL / BUY $ | per-map return corr |
|---|---:|---:|---:|---:|
| LoL | 162 | −1.85% (−162.55 on $8 808) | +1.18% (+482.94 on $40 867) | 0.638 (n=113) |
| Dota GRID | 125 | +0.32% (+54.86 on $16 890) | +4.54% (+513.87 on $11 329) | 0.359 (n=66) |

By live model (LoL): 0831 live +1.62% / bt +0.82%; 0904 −3.70% / +2.88%; 0912 −1.94% / +1.54%;
0915 −4.28% / −5.59%; 0919 −0.93% / −3.22%. The realistic-timing backtest itself goes negative
on Sep 16–19. Both games lose ~3–4 pp from backtest to live on the same GRID maps; LoL starts
from a much smaller realistic edge (1.2% vs 4.5%).

## O6. Live prior vs training prior (same 161 LoL maps)

`work/orchestrator/prior_parity.py`, `prior_effect.py`. Live: `src/trader/market_prior.py`
prices-history minute bars, last aligned pair before `horn − 90 s`, 6 h trailing
(`match_worker.py:505-508`). Train: last two-sided Telonex book in `[spawn − 61 s, spawn)`
(`05_prepare_dataset.py:180-183`). Diff live−train: median 0.00¢, mean|.| 2.74¢, p90 6.0¢,
13% of maps > 5¢, worst 22–30¢ (`grid-2965525-m1` 0.36 vs 0.66, `grid-3005562-m3` 0.155 vs 0.425,
`grid-3004388-m5` 0.625 vs 0.40). Model effect (production model, validation rows 0..480):
mean|Δpred| 0.34¢, p99 3.47¢, gate (|pred| ≥ 2¢) disagrees on 7.4% of rows. The schedule
backtest uses the training prior, so it cannot see this live error.
First live `reason=model` second: median 79, p90 90 (live loses the first ~80 s of the window).

## Agent cross-checks

- diff-grok DG-1/DG-2 = O1/O2 (independent). Verified.
- clock-grok F1-F4 verified against O1/O2; new measured numbers: receipt − livestats wall of the
  named second median +7.92 s (20 682 ticks, 209 maps); spawn offset −0.61 s; kill reaction
  +3.86¢ by 8 s, +4.13¢ by 10 s, +5.03¢ by 30 s (77–82% inside 8–10 s); schedule BUY markout
  −0.52¢ at 4 s. Prior gain share 26.8%.
- clock-grok F6 ("grid-v1 feeds second − 10 for LoL") is REFUTED: `run.py:1405` passes
  `lag_seconds=0` for LoL → `signals.py:271` subtracts 0. Low impact anyway (second = 0.8% gain).

## Correction (from live-luna F3)

`work/shared/agg.py` split Dota by archive-id prefix. Numeric Dota ids since 09-18 are
`feed_source=oddin`, not Steam. Correct split since 09-18 (live-luna): Dota Oddin 51 maps
+3.96% per BUY $ (BUY mk30 +1.17¢), Dota GRID 46 maps +2.41% (mk30 +0.05¢), LoL GRID 44 maps
−0.145% (mk30 −1.14¢). Oddin cadence is 1 s; GRID 3–4 s median.

## O7. Live model delta vs realized 300 s move, by model (`live_calibration_by_model.py`)

Rows: live `reason=model`, second 0..480, realized = radiant mid at first row ≥ s+300 (≤10 s off).

| game | model | maps | rows | corr | sign·Δ for |p|≥2¢ |
|---|---|---:|---:|---:|---:|
| dota | 0825 / 0904 / 0912 / 0915 | 45/66/42/35 | ~2k each | 0.25–0.31 | 3.3–5.0¢ |
| dota | 0919T182837Z / 0921T085717Z | 24/24 | ~19k each | 0.353 / 0.215 | 5.04 / 2.14¢ |
| lol | 0831 / 0904 / 0912 | 100/101/38 | 6.8k/7.1k/2.6k | 0.090 / 0.149 / 0.064 | 1.9 / 2.3 / 1.6¢ |
| lol | 0915 | 16 | 1.3k | −0.137 | −3.7¢ |
| lol | 0919 / 0921 (post-332) | 13/22 | 0.9k/1.5k | 0.152 / 0.052 | 1.9 / 1.05¢ |

Offline validation corr (O4): LoL 0.165 (d=0) → 0.141 (d=10); Dota 0.214 → 0.199.
LoL live signal is weaker than offline and far weaker than Dota live. Rows are autocorrelated.

## O8. `second − 10` claim refuted on data (`second_shift_check.py`)

400 LoL grid-v1 BUY fills (LIVE seed0), stored `predicted_delta` vs research model on the matched
validation row: exact match only with unshifted `second` = 21 fills; only with `second − 10` = 0;
both = 182 (second not used by the trees there). → grid-v1 feeds unshifted `second`
(`run.py:1405` lag 0). clock-grok F6 and lag-devin F5 are wrong on this point.

## Agent results so far (verified by me where marked)

- lag-devin: GRID table (features) arrives 10.98 s after the event (8 553 kills, 268 maps);
  scoreboard 2.77 s; livestats rfc460 = GRID occurredAt (0.00 s); LoL kill t50 = 3.58 s,
  Dota t50 = 9.60 s; Dota table lag 9.65 s. Recommends training lag ≈ 11 s.
- feat-devin: feature values GRID vs livestats in parity (NW median |Δ| 20–105 gold, deaths
  exact); live XP undercount after feed holes (`increaseLevel`), m4 +580 median xp_adv error;
  live scores `second` −60..−1 and > 540 (OOD); live-state-to-mid gap 10.4–14.7 s.
- diff-grok-prior: live prior = prices-history minute bar before horn−90 s; LoL training = book
  in [spawn−61 s, spawn); pre-horn jumps of 20–30¢ on worst maps; no flip, no race. Dota live
  vs catalog prior: mean abs 1.17¢ (same source).
