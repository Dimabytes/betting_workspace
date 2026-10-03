# tails-opus: why histfix doubled LoL tail losses vs w540lv6
Status: FINAL
## Verdict (≤10 lines)
The history fix did not cause the LoL tail change. The position cap did. w540lv6 ran with `--max-position-levels 6`
("lv6"). histfix ran with the code default `MAX_POSITION_LEVELS = 9` (`src/shared/constants/strategy.py:31`, commit `44071bb1`, 2026-10-01).
The control run `cur-20261003r2` uses the w540 model, cap 9 and the histfix code. It already shows 13.4 k buy fills, CVaR5 −614,
maxDD/tot 0.103 and 53 incomplete maps. A clean cap-only A/B (`cat77lv6` vs `cat77`, same model, same code) reproduces the shift:
fills +18 %, CVaR −433 → −571, worst map −893 → −1,324. Late-third gain from the cap: about $0.
The 30 extra incomplete maps come from a reporting change (`e6ca2627`). They have 0 fills and $0 PnL in every run.
The histfix model alone (cur → histfix): CVaR −3 $, fills −3 %, PnL −985 $ pre-rebate (p=0.68). It adds one new worst map
(ns-fox1, −2,088): a SELL that did not fill during a crash, on 9-rung inventory, in 2 of 3 seeds. Mechanism: 3 more rungs of inventory
(position up to 2,700 USDC instead of 1,800) ride to map end on the same losing maps. Fix: cap 6 for LoL. Confirm with experiment A. Confidence: high.

## Findings

### 0. Attribution A vs B (the orchestrator's clean pairs; numbers from finding 2 and `report_seeds`)
| metric (3-seed mean) | w540lv6 | (A) cap 6→9 + new code → cur | (B) history retrain → histfix | share A / B |
|---|---|---|---|---|
| net PnL (with rebate) | 25,524 | +2,828 → 28,352 | −1,119 → 27,233 | +2,828 / −1,119 |
| buy fills | 11,388 | +2,038 → 13,426 | −445 → 12,981 | 128 % / −28 % of the net +1,593 |
| CVaR5 (pre, shared set) | −461 | −157 → −618 | −3 → −621 | 98 % / 2 % |
| worst map (pre, mean of seeds) | −1,073 | −412 → −1,485 | −487 → −1,972 | 46 % / 54 % (B = one map, ns-fox1, 2 of 3 seeds) |
| maxDD/tot | 0.079 | +0.024 → 0.103 | +0.035 → 0.138 | 41 % / 59 % (B driven by seed2 0.181, the ns-fox1 seed) |
| incomplete maps | 23 | +30 → 53 | 0 → 53 | 100 % / 0 % ($0 PnL, finding 8) |
| late-third net (sum of seed-mean, pre) | — | −354 | +1,496 | B gain = 5 maps (finding "ruled out") |

Inside A, the cap-only pair cat77lv6 → cat77 (same model, old code) gives CVaR −138, worst −431, fills +17.8 %. So the cap explains almost all of A; the new backtest code adds at most about −20 $ CVaR (not isolated; experiment B).

### 1. The w540lv6 → histfix comparison changes two things: cap 6 → 9 and the history fix (verified)
- Manifest diff (seed0, `compare_backtests.py` header): w540lv6 `max_position_levels: 6`, histfix `9`. Every other diff is model name/path/sha.
- `git log -L` on `src/shared/constants/strategy.py`: `MAX_POSITION_LEVELS` 8 (`be734a1c`) → 6 (`6329eb8c`, 2026-09-29) → 9 (`44071bb1` "upd limits", 2026-10-01 19:48).
  Commit `783f1c76` states that cat77lv6 and w540lv6 run "at cap 6". histfix and cur used the default 9.
- Live LoL also runs 9 rungs: `src/trader/session_budget.py:38` passes `MAX_POSITION_LEVELS`; `config/trading.toml:14` says "Map cap is 9 rungs of that map's clip".
- 3-seed means (`scripts/report_seeds.py`, net = with rebate, full catalog):

| run | model | cap | code | net PnL | buy fills | CVaR5 | worst map | maxDD/tot | incomplete | late-third net per seed |
|---|---|---|---|---|---|---|---|---|---|---|
| w540lv6 | w540 `20261002T190131Z` | 6 | before `ea5367b9` | 25,524 | 11,388 | −457 | −1,073 | 0.079 | 23 | +3.3 / +3.2 / +3.7 k |
| cur-20261003r2 | w540 `20261002T190131Z` | 9 | histfix code | 28,352 | 13,426 | −614 | −1,485 | 0.103 | 53 | +3.4 / +2.9 / +2.7 k |
| histfix-20261003r2 | histfix `20261003T014429Z` | 9 | histfix code | 27,233 | 12,981 | −617 | −1,972 | 0.138 | 53 | +5.0 / +4.7 / +4.3 k |
| cat77lv6 | cat77 `20261002T165228Z` | 6 | before histfix | 22,673 | 12,460 | −433 | −893 | 0.083 | 23 | +2.9 / +1.8 / +1.9 k |
| cat77 | cat77 `20261002T165228Z` | 9 | before histfix | 27,910 | 14,682 | −571 | −1,324 | 0.103 | 23 | +4.1 / +0.9 / +2.9 k |

### 2. Decomposition: the cap gives the fills, CVaR and maxDD shift; the model gives one worst map (verified)
Paired results, 1216 shared maps (1228 for cat77), engine PnL before rebate, mean over 3 seeds (`compare_backtests.py`, `work/tails-opus/paired.py`):

| pair | what changes | buy volume | CVaR5 (pre) | worst map (pre) | PnL pre (seed deltas) | per-map seed-mean delta: mean / 10 % trim / Wilcoxon p | late third (sum of seed-mean deltas) |
|---|---|---|---|---|---|---|---|
| cat77lv6 → cat77 | cap only | +165,864 (+19.6 %) | −433 → −571 (−138) | −893 → −1,324 | +4,475 (+5,148 / +7,781 / +497), t p=0.12 | +3.61 / −0.29 / 0.88 | +408 |
| w540lv6 → cur | cap + code | +142,040 (+18.6 %) | −461 → −618 (−157) | −1,073 → −1,485 | +2,149 (+3,543 / +2,775 / +127), t p=0.52 | +1.73 / +0.40 / 0.68 | −354 |
| cur → histfix | model only | −30,259 (−3.3 %) | −618 → −621 (−3) | −1,485 → −1,972 | −985 (−1,858 / +786 / −1,882), t p=0.68 | −0.80 / −0.17 / 0.81 | +1,496 |
| w540lv6 → histfix | both | +111,781 | −461 → −621 (−160) | −1,073 → −1,972 | +1,164, t p=0.76 | +0.94 / −0.24 / 0.91 | +1,069 |

- Of the CVaR change (−160 $), −157 $ comes with cap+code. −3 $ comes with the model. The cap-only pair gives −138 $, so the code part is at most about −20 $.
- Of the worst-map change (−899 $), −412 $ comes with cap+code and −487 $ with the model. The model part is one map (finding 4).
- Net/deposit: w540lv6 13.9 → cur 9.6 → histfix 10.4. cat77lv6 11.0 → cat77 9.85. Cap 9 needs +$750..1,100 deposit and pays less per dollar.
- The cap adds PnL only in June–August. Late third (08-22..09-29): cap-only +408 $ (seed deltas +1,055 / −1,149 / +840), cap+code −354 $ (−95 / −546 / −1,225 pre). The cap buys no money in the period closest to live. It does buy tail.

### 3. The tail is systematic: the same maps lose in every seed and every cap-9 run, about 1.5× deeper (verified)
- Peak position cost basis per traded (map, seed): cap 6 runs max 1,800 (p90 1,618–1,656); cap 9 runs max 2,700 (p90 2,429–2,463) (`paired.txt`).
- Bottom-5 % (map, seed) of histfix (n=186): median peak cost 2,212 vs 1,557 on the same maps in w540lv6. 65 % of them go above 1,800, the cap-6 maximum. cur: 58 %, cat77: 61 %.
- Worst histfix maps, PnL per seed [s0, s1, s2]:
  - hle1-ns-2026-07-21: hf [−1073, −1915, −1670], cur [−1587, −1367, −1311], w540 [−678, −565, −818].
  - t1-hle1-2026-08-08: hf [−1513, −895, −1481], cur [−1117, −912, −1481], w540 [−46, −530, −123].
  - tsw-tes-2026-07-04-g4: hf [−1325, −1442, −1238], cur [−1334, −1387, −1197], w540 [−627, −901, −717].
  - drx-ns-2026-07-29-g2: hf [−472, −1298, −1352], cur [−472, −1343, −1345], w540 [−247, −823, −820].
  - t1-kt-2026-07-29-g2: hf [−1318, −1107, −1269], cur [−1318, −1107, −1237], w540 [−818, −827, −800].
  The histfix and cur numbers match (same cap, same fills) and are 1.5–2× the cap-6 loss. This is a model+cap effect, not cadence or fill noise.
- Only one top-10 histfix map depends on the model and the seed: ns-fox1-2026-08-01, hf [−1913, −480, −2088], cur [−1172, −569, −564], w540 [−245, −208, −161].

### 4. Fill paths of the 5 worst histfix maps: the extra loss is rungs 7–9 bought before the move, then held (verified)
`work/tails-opus/paths.py` → `paths.txt` (fills grouped into ≤10 s same-side bursts; t = seconds after horn).
- hle1-ns s1: all three runs buy the same first rungs at 0.69–0.84. w540 stops at cost 1,569 (2,110 sh). cur and hf keep buying at t=433..491 (0.67–0.77) to cost 2,603 (3,583 sh).
  The game turns and the token falls to 0.20–0.30. w540 −565, cur −1,367. hf −1,915: it sold 244 sh at 0.60 (t=634) instead of 804 sh at 0.76 (t=508, cur), and holds 855 sh to settlement.
- tsw-tes s1: w540 peaks at cost 1,696 → −901. cur and hf peak at 2,626 (4,416 sh) → −1,387 / −1,442. The fills match until t=349. hf misses one 935-sh SELL at 0.56 (t=349).
- drx-ns s2: w540 cost 1,618 → −820. cur 2,650 → −1,345. hf 2,636 → −1,352. Same entries, same exit at 0.26–0.28. The loss scales with size.
- t1-hle1 s0: w540 round-trips 2,988 sh at 0.53–0.56, then 3,492 sh on the other token at 0.45. cur and hf stack 4,475 / 5,804 sh on the second token at 0.45–0.47 and exit at 0.18–0.21. w540 −46, cur −1,117, hf −1,513.
- ns-fox1 s2: cur and hf are identical to t=260 (cost 2,630, 4,402 sh at 0.56–0.66; w540 stops at about 1,680). The token rises to 0.93. The SELL rests at fair (0.93–0.96, above mid) and never fills.
  At t≈1140 a fight crashes the mid to 0.435. At t=1280 cur's SELL at 0.44 (fair 0.438) fills 3,321 sh. hf's fair is 0.408, its SELL at 0.41–0.45 misses as the mid drops to 0.365, and hf exits at 0.06–0.07 at t=1454. Result: −2,088 vs −564.
  The quote timeline shows no stale-signal or history-gap block (`qe_nsfox_s2.txt`). It is a one-tick fill race on 9-rung inventory.
- In none of the 5 maps does a SELL disappear because ticks became invalid.

### 5. The +14 % buy fills is the cap. The histfix model makes fewer entries, not more (verified)
- Cap only: buy fills 12,460 → 14,682 (+17.8 %). Model only (cur → histfix): 13,426 → 12,981 (−3.3 %).
- `work/tails-opus/score.py`: both ensembles scored on the same 488,426 labeled validation rows (1,184 backtest maps, 0 ≤ second < 480).
  New-rule history features (HEAD `attach_catalog_features`, start 60, exact pivot) and old-rule features (`ea5367b9^`, 16 s pivot gap, no start clip):

| model / features | \|Δ\| p50 | \|Δ\| p90 | entry rate \|Δ\|≥0.02 | MAE | edge on entry rows |
|---|---|---|---|---|---|
| w540 / old rule (= w540lv6 backtest) | 1.41 ¢ | 4.21 ¢ | 37.1 % | 9.295 ¢ | 2.429 ¢ |
| w540 / new rule (= cur backtest) | 1.43 ¢ | 4.31 ¢ | 37.8 % | 9.297 ¢ | 2.418 ¢ |
| histfix / new rule (= histfix backtest) | 1.38 ¢ | 4.10 ¢ | 36.3 % | 9.303 ¢ | 2.386 ¢ |

  histfix − w540 on the same rows: mean −0.10 ¢, sd 0.30 ¢, corr 0.993. The entry decision differs on 4.7 % of rows. The histfix model is slightly less confident.
- NaN rates (radiant_nw_adv history, `score_report.txt`): the new rule differs from the old rule in two bands only. change_1m is NaN at seconds 60–119 (old: 0 %), because lag targets below 60 s are clipped.
  change_5m and total_5m are NaN at seconds 300–359 (old: 0.1 %). Elsewhere the rates match (≤0.2 %).
  The w540 model with new-rule features (the cur run) has a train/serve mismatch in those bands: entry rate at 300–359 s rises 46.7 % → 51.8 %, edge 2.82 → 2.69 ¢. The effect is small.

### 6. The exit side did not change in a way that explains the tails (verified)
`work/tails-opus/exits.py` → `exits.txt`:
- SELL cancels with reason `stale_signal` (3 seeds): w540 275, cur 300, hf 269, c6 327, c9 331. This is negligible against about 210 k reprice cancels. Invalid ticks do not pull SELLs.
- no_quote `stale_signal` events: w540 2.56 M → cur 3.14 M → hf 3.13 M (c6/c9 2.55 M). The new code (gap ticks dropped, connect-only first tick, rows before the first feed tick cut) adds about 22 % stale time.
  This blocks BUYs (signal older than 16 s), not SELLs (pulled only after 45 s, `src/strategy/quoting.py:777-801`). min_delta blocks fall by the same amount (4.39 M → 3.23 M).
- First SELL after first BUY, cur vs hf: hf is later on 113–120 maps and earlier on 138–149 maps per seed; median 130–138 s vs 144–145 s. histfix exits earlier, which fits the shorter hold p90.
- Qty-weighted sell 30 s markout per seed: w540 −0.51 / −0.70 / −0.48 ¢, cur −0.65 / −0.73 / −0.59, hf −0.55 / −0.79 / −0.68, c6 −0.52 / −0.59 / −0.44, c9 −0.56 / −0.56 / −0.60.
  Most of the drop comes with w540 → cur (cap + code). cur → hf is mixed by seed: noise.

### 7. Deep rungs carry half the edge of shallow rungs, and about zero in the late third (verified, noisy)
`work/tails-opus/rungs.py` → `rungs.txt`: BUY 300 s markout, qty-weighted, by cost basis before the fill:

| run | <600 | 600–1200 | 1200–1800 | 1800–2700 (rungs 7–9) | 1800–2700, late third |
|---|---|---|---|---|---|
| cur | 1.96 ¢ | 1.71 ¢ | 2.25 ¢ | 1.61 ¢ | −0.11 ¢ |
| histfix | 2.27 ¢ | 1.85 ¢ | 1.98 ¢ | 0.98 ¢ | 0.87 ¢ |
| cat77 | 1.57 ¢ | 1.60 ¢ | 1.44 ¢ | 1.05 ¢ | 0.06 ¢ |

Rungs 7–9 are about 76–97 k USDC of buy notional per seed. They add inventory on maps that are already deep in a position, and the strategy holds to map end. So they add little expected edge and a lot of end-of-map variance.

### 8. The 30 extra incomplete maps carry zero PnL (verified)
`work/tails-opus/incomplete.py` → `incomplete.txt`. "Incomplete" is the sum over 3 seeds of `terminated_early`: w540lv6 7/8/8 = 23, cur and histfix 15/18/20 = 53. cur and histfix have identical sets.
- stop_reason: `nautilus_zero_fill` 12 in every run. `empty_signal_tape` 11 (w540) → 41 (cur, histfix).
- The 30 new (map, seed) pairs are 13 maps (e.g. lol-ckz-esb-2026-09-19-game1/3, lol-mgz-llh-2026-08-25-game2/3, lol-fn-fec-2026-06-08). All are `empty_signal_tape`, with 0 buy fills and $0 engine PnL in all three runs. w540lv6 counted them as completed no-trade maps.
- Cause: `e6ca2627` (2026-10-03). A grid-v1 map whose rows all come before the first feed tick now gets an empty `MatchSignals`, and the engine records `terminated_early` (`src/backtest/signals.py:581-603` in that commit). Before, it was a completed map with no decision tick.
  The seed dependence comes from the grid-v1 cadence draw. No PnL bias.

## What I ruled out
- History-invalid ticks pull SELLs or delay exits → no. Stale-signal SELL cancels are about 300 per 3 seeds in every run, and histfix sells earlier (finding 6).
- The histfix model outputs larger |Δ| and so fills more → no. Its |Δ| is smaller and its entry rate 4 % lower. Fills fall 3.3 % vs cur (finding 5).
- The 53 incomplete maps bias the comparison → no. Zero fills and zero PnL (finding 8).
- Cadence or fill-model noise makes the tail → no for the CVaR shift. The worst maps are the same maps in all seeds and in both cap-9 runs (finding 3). Only ns-fox1 is a seed-dependent fill race.
- The code change (gap drops, connect-only tick, first-tick cut) makes the tail → mostly no. Cap only gives −138 CVaR and cap+code −157 (difference within seed noise, sd ≈ 20–30). I could not isolate the code effect exactly without a run (experiment B).
- The histfix late-third gain (+1,496 $ vs cur) is real → not shown. Top 5 maps give +2,330 and bottom 5 give −1,408; the rest gives +574. Wilcoxon p=0.95.

## Proposed experiments
All from the esports-trader root. Compare with `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python scripts/compare_backtests.py <base> <cand>`. Judge on the late-third rows and on CVaR5 / worst map.

A. **histfix model at cap 6 (decisive, recommended first).** This is the fair comparison to w540lv6 that the context table assumed.
   `make lol-backtest ARGS="--validation --name histfix-lv6 --model-dir data/lol/models/research --max-position-levels 6 --signal-cadence-seed N"` for N = 0, 1, 2 (`research` = histfix `20261003T091511Z`, byte-identical members). Use the same shards and merge as the histfix-20261003r2 run.
   Expected: buy fills ≈ 11.0–11.5 k, CVaR5 ≈ −430..−480, worst map ≈ −1,000..−1,300, maxDD/tot ≈ 0.08. Net ≈ 23.5–25.5 k (histfix minus 2–4 k of June–August cap PnL). Late third ≈ +4 k net per seed, so no worse than histfix at cap 9.
   Compare against both w540lv6 (model effect at cap 6) and histfix-20261003r2 (cap effect on this model). Cost: 3 backtest seeds.
B. **w540 model at cap 6 on the current code** (isolates the code change from the cap): `--name w540-lv6-newcode --model-dir data/lol/models/archive/research/20261002T190131Z --max-position-levels 6`.
   Expected vs w540lv6: CVaR within ±30, fills about −2..+2 %, incomplete 53 (reporting only). Watch late-third PnL for the gap-drop cost. Cost: 3 seeds. Optional if A is clear.
C. **LoL sizing sweep, cap 5 / 6 / 7 / 9 at the current clip,** with the histfix model. Dota had a sweep (`docs/experiments/sizing-sweep.md`); LoL inherited cap 9 from the shared constant without one.
   `--max-position-levels {5,7}` (6 and 9 come from A and LIVE). Watch net/deposit, CVaR5, worst map and the **late-third** net. Expected: late-third net is flat in the cap; CVaR scales about linearly (−140 $ per +3 rungs).
   Pick the smallest cap that keeps late-third net. Cost: 6 seeds.
D. **Code change if A confirms:** make the cap per-game, e.g. `LOL_MAX_POSITION_LEVELS = 6` next to `MAX_POSITION_LEVELS` in `src/shared/constants/strategy.py`, read in `src/trader/session_budget.py:38` and as the LoL default in `src/backtest/run.py:1878`.
   Live LoL now runs 9 rungs (`config/trading.toml:14`), so the live tail is the cap-9 tail.
- Not proposed, because the evidence does not support them: "invalid tick → hold quotes, no new BUY" (SELLs are not pulled by invalid ticks now), "cap position when history is NaN" (NaN bands are 60–119 s and 300–359 s, and the worst maps load their deep rungs at 220–490 s with valid history), and "keep the old history rule for LoL" (model-only tail effect −3 $ CVaR).
  Post-cutoff unwind and an exit model were tried before (`unwind180/420/600`, `no-unwind`, `lol-exit-model.md`, negative). Depth caps lost on 2026-09-11 (`map-first-entry-and-depth-caps.md`), but that test had a different cap baseline (8 rungs).

## Scripts
Run all from the esports-trader root (`cd ../esports-trader` from betting_workspace). `W=../betting_workspace/investigations/2026-10-03-lol-edge/work/tails-opus`.
- `$W/report_seeds_<run>.txt`, `$W/compare_<a>_vs_<b>.txt`: output of `scripts/report_seeds.py <run_dir> --expected-seeds 3` and `scripts/compare_backtests.py <base> <cand>`, with `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python`.
  Pairs: w540lv6→histfix, cur→histfix, w540lv6→cur, cat77lv6→cat77.
- `$W/incomplete.py` → `incomplete.txt`: terminated maps per run/seed and their PnL in the other runs.
- `$W/paired.py` → `paired.txt`, `per_map.parquet`: seed-pooled paired deltas, CVaR, worst maps across seeds/runs, peak exposure.
- `$W/paths.py` → `paths.txt`: fill paths of the 5 worst histfix maps under w540lv6/cur/histfix.
- `$W/qe_map.py <match_id> <seed> <t0> <t1>` → `qe_nsfox_s2.txt`: quote-event timeline (block reasons, SELL quotes) cur vs histfix.
- `$W/score.py` (uses `$W/old_dota_features.py` = `git show ea5367b9^:src/shared/utils/dota_features.py`) → `scored.parquet`; `$W/score_report.py` → `score_report.txt`: ensemble deltas, entry rates, NaN rates old vs new rule.
- `$W/exits.py` → `exits.txt`: block reasons, SELL cancel reasons, sell markouts, first-SELL timing.
- `$W/rungs.py` → `rungs.txt`: BUY markout by position depth and period. `$W/late.py` → `late.txt`: concentration of per-third deltas.
- Run each with `PYTHONPATH=src nice -n 10 uv run python $W/<script>.py`.
