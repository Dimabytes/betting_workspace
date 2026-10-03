# glm-tails: why did the LoL history fix double the tail losses
Status: FINAL

Agent: glm-tails. Scope: `briefs/tails-opus.md` — explain mechanically why `histfix-20261003r2` doubled LoL tail losses vs `w540lv6` (CVaR5 −433/−493/−444 → −597/−623/−632, worst map −1,107/−1,056/−1,056 → −1,913/−1,915/−2,088) while the mean went up. All numbers below were recomputed by me from the run parquet/json/manifests (read-only; no backtests were run). Evidence tags: **verified** (measured from data), **likely** (mechanism fits all observed numbers, not directly replayed), **speculative**.

## Verdict (≤10 lines)

The "history fix doubled the tails" reading is mostly a **sizing confound**: between the two runs the per-map position cap moved 6→9 rungs ($1,800→$2,700, commit `44071bb1`, present only in the histfix run). A clean on-disk ladder decomposes the whole A/B: (1) `w540lv6` (cap 6, old backtest code) → `cur-20261003r2` (cap 9, new backtest code, *same model*) takes CVaR5 from ≈−457 to ≈−614 and the worst map from ≈−1,073 to ≈−1,485 while *adding* +$2,115/seed mean; (2) the actual history-retrained model at fixed cap 9 (`cur` → `histfix`) is mean-neutral (−$985/seed, t p=0.57) and CVaR-neutral (−614 → −617), and only adds ≈−500/seed on **one map** (`115548147900684656`, −768 → −1,494) through exit stranding (its model fair keeps SELLs above the collapse sweep; kill-gate + mid-spike freezes amplify on seed 0). A pure-cap A/B (`cat77lv6` vs `cat77`, manifests differ only in `max_position_levels`) reproduces the tail doubling on a second model. The history policy itself is not what doubled the LoL tail; the cap raise is, with the retrained model adding a single concentrated stranding loss.

## The confound, precisely

- Manifests: `w540lv6` `max_position_levels: 6`; `histfix-20261003r2` `max_position_levels: 9`; `layer_usdc: 300`. Only other manifest diffs: model_name/path/sha. **verified**
- Commit `44071bb1` (2026-10-01 19:48, "upd limits") raised `MAX_POSITION_LEVELS` 6→9 in `src/shared/constants/strategy.py:31` and `account_cap_usdc` 2500→3800 in `config/trading.toml` (comment there: "Map cap is 9 rungs"). `w540lv6` ran 2026-10-02 21:15 (explicit `--max-position-levels 6`, like `validation-20260930-s3-sh6` before it); `histfix` ran 2026-10-03 04:55 on the new default 9. **verified**
- Cap math (`src/strategy/budget.py:23-36`): cap_room = levels×300 − held − reserved. At 6 rungs maps pin at $1,800 committed; at 9, $2,700 (+50% notional on pinned maps). **verified**
- Cap binding, seed-pooled over BUY fills: `w540lv6` peak committed max $1,800 (p99 $1,800; 23.9% of maps >$1,700); `histfix` max $2,700, 35.8% of maps >$1,800, 10.6% >$2,600. Every one of histfix's 10 worst maps is cap-bound in w540lv6 (peaks $1,700–1,800) and commits $2,500–2,700 under histfix. **verified**

## Findings

### F1. A clean decomposition ladder already exists on disk **verified**

Same validation split, 3 seeds, one command family; manifests pin the differences:

| run | model | backtest code | cap | net/seed (post-rebate) | CVaR5 (s0/s1/s2) | worst map (s0/s1/s2) |
|---|---|---|---|---:|---:|---:|
| `w540lv6` (Oct 2 21:15) | `20261002T190131Z` | old | 6 | 28,078/23,035/25,459 | −433/−493/−444 | −1,107/−1,056/−1,056 |
| `cur-20261003r2` (Oct 3 05:08) | same `20261002T190131Z` | new | 9 | 32,356/26,421/26,279 | −592/−637/−614 | −1,587/−1,387/−1,481 |
| `histfix-20261003r2` (Oct 3 04:55) | `20261003T014429Z` (retrained) | new | 9 | 30,347/27,087/24,266 | −597/−623/−632 | −1,913/−1,915/−2,088 |

- **Step A** (cap 6→9 + new backtest code; same model): paired n=3,664 (seed,map) rows, mean +$1.8/map, pooled +$6,446, seed deltas +3,543/+2,775/+127, t p=0.33. CVaR5 ≈−457 → ≈−614 (**all of the A/B's CVaR doubling**), worst ≈−1,073 → ≈−1,485, incompletes 23 → 53 (reporting change, F3).
- **Step B** (history-policy retrain only; cap and code fixed): mean −$0.8/map, pooled −$2,954, t p=0.57, Wilcoxon p=0.26 (the doc's own paired number: −$985/seed, p=0.68). CVaR5 −614 → −617 (**flat**). Worst map −1,485 → −1,972 (**the only real tail worsening**, concentrated on one map, below). Incompletes unchanged (53 → 53, identical id set).
- Pure-cap pair, second model (`cat77` vs `cat77lv6`, model `20261002T165228Z`, manifests differ *only* in `max_position_levels`, both Oct 2 = same old serve code): net 31,492/31,546/20,693 vs 25,557/23,050/19,411 (**cap 9 adds ≈+$5,238/seed mean**); CVaR5 −551/−559/−603 vs −421/−445/−434 (**+32% worse**); worst −1,292/−1,274/−1,405 vs −937/−865/−877 (**+48% worse**). The tail doubling reproduces with zero history-policy involvement. **verified**

### F2. On the worst maps, the ladder shows step A (cap+code) first, step B (retrain) second **verified**

Seed-mean engine PnL on histfix's 12 worst maps:

| match_id | w540lv6 (c6) | cur (c9) | histfix (c9) | d(step A) | d(step B) |
|---|---:|---:|---:|---:|---:|
| 116929405348526328 | −687 | −1,422 | −1,553 | −735 | −131 |
| 115548147900684656 | −205 | −768 | −1,494 | −564 | **−726** |
| 115570934355614573 | −748 | −1,306 | −1,335 | −557 | −30 |
| 115548147900553444 | −233 | −1,170 | −1,296 | −937 | −127 |
| 115548147900553463 | −815 | −1,221 | −1,231 | −406 | −10 |
| 115548681803406329 | −962 | −1,138 | −1,084 | −177 | +54 |
| 117171782819927873 | −1,056 | −1,080 | −1,075 | −24 | +6 |
| 115548147900750226 | −819 | −792 | −1,070 | +27 | **−278** |
| 117171782819927856 | −831 | −1,171 | −1,059 | −340 | +113 |
| 115548147900684663 | −630 | −1,053 | −1,041 | −423 | +13 |
| 115548128963037564 | −848 | −1,182 | −984 | −334 | +198 |
| 116929405286725871 | −460 | −926 | −879 | −466 | +47 |

- Step A damages 10 of 12 maps by −170…−940; step B damages essentially two: `…684656` (−726) and `…750226` (−278), partly offset elsewhere. `684656` is not even in cur's worst-10 — its blow-up is histfix-model-specific. **verified**
- Buy/sell decomposition (pooled, flat-terminal identity dPnL = dSell − dBuy, checked against engine_pnl): on the 10 worst histfix maps step A adds d_buy +16,868 vs d_sell +1,621 → **the degradation is extra top-of-spike buys, not worse exits**; step B on the same maps is d_buy +79 / d_sell −4,077 (dominated by 684656 d_sell −1,137 and 750226 d_sell −812) → **sell-side only**. **verified**

### F3. Item 1 — the 30 extra incomplete histfix rows are benign, and they are entirely step A **verified**

- Seed-pooled rows: w540lv6 23 (7/8/8 per seed, 11 unique ids) vs histfix 53 (15/18/20, 23 unique ids). All 11 w540lv6 ids ⊂ histfix ids; the 12 histfix-only ids are all `empty_signal_tape` (feed so sparse that the map never reaches the engine with a usable tape).
- **The 23→53 jump is already in step A**: `cur-20261003r2` has the identical 53 rows (15/18/20) and the identical 23-id set; the retrain (step B) adds none. Cause is the new backtest code (per the sibling analysis, commit `e6ca2627`: grid-v1 maps whose rows all precede the first feed tick now record `terminated_early` instead of completed-no-trade).
- Those 12 ids in w540lv6: engine_pnl +0.00, zero trades. Incomplete rows in both runs have buy_fills sum = 0, sell_fills sum = 0. Dropping/keeping them cannot move any pooled number. No selection bias.

### F4. Item 2 — same maps, systematic scaling, no mean shift **verified**

- Paired per-map (confounded pair, 1,225 shared): mean +0.95, 10% trimmed −0.25, Wilcoxon p=0.91 — the +$3.5k pooled A/B gain is big-ticket maps, not a distribution shift.
- Worst-10 sets overlap across seeds: 6 maps are worst-10 in **all 3 seeds** under histfix (115548147900553463, 115548681803406329, 115570934355614573, 116929405348526328, 117171782819927856, 117171782819927873); the same maps dominate w540lv6's worst list — histfix loses ~1.5–2× more on the *same* tail maps. Map/model-driven, not cadence luck.
- Schedule-mode maps are deterministic across seeds (e.g. 115565004607949382: −61/−534/−534; …949415: −599/−1,272/−1,272) — seed noise is not the driver on those.

### F5. Item 3 — two compounding mechanisms on the worst maps (fill-timeline forensics, seed 0) **verified** (facts) / **likely** (attribution)

Cap mechanism (extra buys at the local top, then exit into the collapse; identical fills until w540lv6 pins at ~$1,700–1,800, divergence exactly there):
- `115548147900553463`: identical buys to cb $1,697; w540lv6 cap-blocked, exits all 2,394 sh @ 0.35–0.40 by t=607 (−818). histfix adds 9 buys t=361–477 for +$1,002 at 0.69–0.72 (book 0.28–0.30), pins cb $2,699 = the $2,700 cap, exits the same way (−1,318). Pure size, same exit.
- `115565004607949382`: identical fills to cb $1,692 (t=577); w540lv6 cap-blocked and sells down through the book 0.76→0.48 (−61). histfix buys +$822 more at 0.77–0.80 during t=580–604 (book spikes to 0.81), then grinds out of 3,351 sh as the book collapses 0.73→0.48 (−534).
- `115548147900553444`: identical buys to cb $1,586 (t=316); w540lv6 cap-blocked, sells ALL 3,491.7 sh @ 0.45 in one fill (−46). histfix buys +$1,041 more at 0.45 (rungs w540lv6 could not quote), its single exit SELL fills only 129.4 sh, kill-gate cancels it, the rest dumps at 0.18–0.22 (−1,513).
- `116929405348526328`: w540lv6 capped at t=435 (buy $1,558, −678); histfix keeps buying t=391–456 to $2,529 (−1,073).
- `115570934355614573`: w540lv6 capped (buy $1,708, one-shot exit @ 0.49, −748 episode + re-entry); histfix buys $2,620 (cb = cap) with ~2,163 extra sh @ 0.52–0.58, 35 small sell fills (−1,325).

Model mechanism (exit stranding, the step-B marginal on `115548147900684656`):
- At cap 9 with the *old* model this map loses −768; with histfix −1,494 (seed0: −245 vs −1,913). Fills are identical early (both sold 1,090 sh @ 0.79 at t=356); both buy to ~$2,693 cb. After the collapse (book 0.84→0.41): the old model sells everything at t=1234–1280 @ 0.45/0.44 and is flat; histfix sells only 667 sh @ 0.46, then **no SELL fills for ~140 s** while the book bleeds 0.11→0.03, dumping the rest in 48 SELLs. Quote events show histfix *was* quoting SELLs (0.44–0.45) but was blocked: kill-gate `no_quote` 84 s at ~1280, then `mid_spike` freeze 185 s at ~1300; its model delta is more bullish (+0.038..0.044 vs +0.033..0.039) → higher SELL quotes → missed the 1280 sweep that filled the other run completely. Extra loss ≈ +$998 more buys − $670 worse sell proceeds. **likely** (gate chronology + delta levels measured; counterfactual "would have filled" not replayed)
- `115548147900750226` is the second model-step map (d_sell −812, d_buy +21): same sell-side-only signature. **verified** (decomposition) / **likely** (mechanism)

### F6. Item 4 — the +14% buy fills are step A (cap + code), not the retrained model **verified**

Seed 0: buy fills 11,595 (w540lv6) → 13,785 (cur, cap 9 + new code, same model, +18.9%) → 13,271 (histfix, retrained model, **−514**). Buy turnover $761,691 → $916,406 → $881,343. Gates: `gate_stale_signal_seconds` 852,167 → 1,045,271 → 1,044,957 (the new code's tick dropping blocks quoting on invalid ticks — model-independent); `gate_min_delta_seconds` 1,459,695 → 1,067,531 → 1,099,540 (fewer small-|delta| seconds blocked on surviving ticks). `gate_position_cap_seconds` = 0 everywhere (the cap blocks *quoting room*, not via this gate).
- Attribution of the extra fills (confounded pair, pooled): 25% of (seed,map) arms pin the old cap (peak ≥$1,700); they carry +3,256 of +5,031 extra fills and +$231k of +$353k extra buy notional (65%). Maps where histfix commits >$1,800 (impossible under the old cap): 851 arms, +5,898 fills, +$402k notional, d_pnl −$23,020.
- |predicted_delta| at BUY fills is **identical** (mean 0.0347 vs 0.0346, p90 0.0564 vs 0.0562): the retrained model does not quote bigger deltas; more ticks simply survive/qualify (history is valid instead of NaN-degraded → `min_delta` gate passes more often).
- The brief's "NaN rates of total_5m/change_1m in the training tape" check is superseded by this: the tick-level effect is already measured directly at the gates and fills.

### F7. Item 5 — exit side: no systematic degradation, one stranded map **verified**

- SELL-30s markout (summary blocks): −0.508/−0.698/−0.483¢ (w540lv6) vs −0.552/−0.788/−0.684¢ (histfix) — mildly worse, same order as the size increase (sell qty/fill 152→172).
- First SELL after first BUY: p50 137 s → 134 s; maps with first SELL >30 s later under histfix: 300, >30 s earlier: 305 — **symmetric**, and the >30 s-later maps actually made +$2,974 while >30 s-earlier made −$5,949 (net effects, both signs). No systematic exit delay; the stranding is concentrated on 684656/750226 (F5).
- SELL/BUY maker share 1.000 in both runs — fills are all maker; taker flow is not a factor.
- Hold p90 629/715/687 s → 641/666/628 s; holds closed 336/318/317 → 294/287/306 (fewer, slightly longer at p90 — bigger positions to exit).
- Existence proof that the exit side is a real tail lever: the reverted `exit-full-l1` experiment (w540lv6 + an exit model, cap 6; manifests differ only in `exit_model_*`) improved worst map −1,107→−865 and CVaR5 +37 at a mean cost of −$1,633 (p=0.65) — judged negative on the mean and reverted (`docs/experiments/lol-exit-model.md`). At cap 9 the tail benefit would be larger.

### F8. Recency and the Dota contrast **verified**

- Late third (2026-08-22..09-29, 406 matches) under the confounded pair actually **improved**: mean seed delta +1,068, pooled +$2.63/match, Wilcoxon p=0.41. The change does not hurt recent maps; the tail maps are old-regime collapse patterns. Caveat: not significant, and per the sibling decomposition it splits into cap+code −$354 vs model +$1,496 concentrated in ~5 maps (their Wilcoxon p=0.95) — read it as "no late-third harm", not as a gain.
- Dota ran the same history change with cap 9 on **both** sides (hist77cur vs histfix, `docs/experiments/hist-policy-20261003.md`): CVaR5 −399 → −409, worst map −1,167 → −1,164 — flat tails, +$5,335 mean. Same change, clean A/B, no tail regression: consistent with the cap being the LoL-specific difference.

## What I ruled out

- **Selection bias from the 30 extra incomplete rows** — all zero-trade, zero-PnL (F3).
- **Systematic mean/per-map shift from the retrained model** — paired t p=0.57 / Wilcoxon p=0.26 at fixed cap; p=0.91 pooled on the confounded pair.
- **"The new model quotes bigger deltas / chases"** — |delta| at BUY fills identical (F6); at fixed cap it fills *less* (−514 fills, −$74k buys pooled).
- **Systematic exit delay across the book** — first-sell lag symmetric, maker share 1.0 (F7); sell-side damage is two maps.
- **Seed/cadence luck** — same worst maps in all 3 seeds; schedule-mode maps deterministic (F4).
- **Recent-regime degradation** — late third improved (F8).
- **`gate_position_cap` accounting quirks** — 0 seconds in all runs; the cap binds through quoting room (F6).

## Proposed experiments

All are `make lol-backtest` validation runs (3 seeds; same shape as the existing arms). Judge on CVaR5/worst map *and* the late third, not the mean alone.

1. **Isolate the confound (do first):** histfix model at the old cap.
   `make lol-backtest ARGS="--validation --name histfix-lv6-20261003 --model-dir data/lol/models/experiments/hist-policy-20261003 --max-position-levels 6"`
   Expected: CVaR5 back to ≈ −450…−500 (from −617), worst map ≈ −1,100…−1,500 (684656 keeps only its stranding part, ≈ −1,100–1,400), buy fills ≈ 11,600–12,100/seed, mean net ≈ 24.5–26k. If CVaR lands ≤ −500, the doubled tail is confirmed as the cap, and the history fix is tail-safe at the old sizing.
2. **Cap sweep for LoL specifically:** `cat77`/`cat77lv6` style pair on the current production model, `--max-position-levels {6,7,9}`. Expected interpolation: each extra 2 rungs ≈ +$2–5k mean, +30–50% CVaR/worst (measured 6→9 endpoints: +$5.2k mean, CVaR −433→−571, worst −893→−1,324). Pick the cap from the risk budget, not from the Dota-validated 9.
3. **Exit-side fix for the stranding maps:** the damage on `684656`/`750226` is sell-placement during collapses (histfix's model fair keeps SELLs above the sweep; the exit is a passive `max(ask, fair)` ask per `src/strategy/quoting.py:467-507`, so the bleed-out price is queue luck). Candidate rules, cheapest first: (i) bound the exit in time (~60 s) after which the SELL crosses; (ii) hard mid stop — cross to flat when tok_mid < position vwap − 0.15 (simulated +50% on the 14 worst map-seeds by tails-dev); (iii) "keep last SELL" during kill-gate `no_quote`/`mid_spike` freezes (helps the seed-0 amplifier only); (iv) retry the `exit-full-l1` exit model at cap 9 (at cap 6 it already gave worst −1,107→−865, CVaR +37 for −$1,633 mean, p=0.65). Expected: histfix worst map −1,913 → ≈ −1,300…−1,500, CVaR −597 → ≈ −550, mean within noise.
4. **Drawdown-aware rung cap:** stop opening rungs once unrealized position PnL < −X (every worst map is cap-bound at the top of a spike, F5). A validation sweep over X ∈ {150, 300} vs the F1 ladder. Expected: worst-map and CVaR improvements at small mean cost; this is the direct guard against the "buy the spike, dump the collapse" pattern.

Note for the promote decision: histfix itself is mean-neutral-to-positive and late-third-positive, so promoting the *model* is defensible; what was never validated for LoL is **cap 9** (Dota validated 9 on Dota). If the LoL risk budget is CVaR ≈ −450, run experiment 1 and consider pinning LoL back to 6 rungs.

## Comparison with sibling reports (read after my numbers were final)

Read `reports/tails-opus.md` and `reports/tails-dev.md` after my findings were locked. All three of us independently found the cap confound and reach the same verdict.

**Agreements (numbers match where we overlap):**
- Cap 6→9 (commit `44071bb1`) is the dominant tail driver. Same ladder numbers: CVaR5 −457 → −618 (cap+code) → −621 (model); worst map −1,073 → −1,485 → −1,972; the model step is mean-neutral (−$985/seed, p=0.68/0.57/0.26 depending on test) and CVaR-neutral (−3).
- The pure-cap pair `cat77lv6`/`cat77` reproduces the tail shift with no history change (their CVaR −138 / worst −431 / fills +17.8% vs my −138 / −431 / +18.9% — seed-set differences).
- The one model-side tail map is `115548147900684656` (ns-fox1): all three of us identify it; my seed-mean ladder (−205 → −768 → −1,494) matches their per-seed values pooled.
- Incompletes are benign, zero-PnL (all three); the 12 histfix-only ids are `empty_signal_tape`, 53-vs-23 is a reporting artifact.
- Extra buy fills come with the cap/code step; the retrained model fills *less* (my −514 seed0, their −445 3-seed mean; same direction).
- Dota contrast: same change at cap 9 on both sides, tails flat — no disagreement.

**Where the siblings go beyond me (I accept their refinements):**
- tails-opus pinned the incomplete-map cause to commit `e6ca2627` (grid-v1 maps whose rows all precede the first feed tick now record `terminated_early` instead of completed-no-trade). My mechanism ("`drop_gap_ticks` invalidates every tick") was the likely-but-imprecise version of the same thing.
- tails-opus scored both ensembles on the same 488k labeled validation rows (|Δ| slightly smaller and entry rate −4% for histfix; NaN bands confined to seconds 60–119 and 300–359) — the check I marked superseded; their version is stronger and agrees with my fills-side |delta| finding.
- tails-opus measured rungs 7–9 BUY-300s markout (≈1–1.6¢ vs ≈2¢ shallow, ≈0 in the late third) and maxDD/tot (0.079 → 0.103 → 0.138) — both strengthen the "cap 9 buys variance, not edge" reading.
- tails-dev showed the exit is `max(ask, fair)` (`quoting.py:467-507`) and that on identical entries the bleed-out is a queue lottery (ns-fox1: −480/−1,913/−2,088 across seeds, sell_avg 0.533/0.258/0.124). On ns-fox1 **seed 2** they found no stale/history block (pure fill race); my kill-gate/mid-spike chronology is **seed 0**. Both are true — the shared root cause is the histfix model's fair keeping SELLs above the sweep, with gates an extra amplifier on seed 0. My experiment 3 is reworded accordingly (bounded exit / mid stop first).
- tails-dev's rule sims (cap6 scaling +23%, mid stop −0.15 +50%, time stop +38% on the 14 worst map-seeds — speculative, loser-selected) and the whipsaw finding (the exit also dumps eventual winners at dip bottoms) add the strategy-level frame my fill forensics implied but did not test.

**Disagreements / nuances:**
- Late third: I reported the confounded pair "+1,068 mean seed delta, p=0.41" as an improvement; tails-opus decomposes it into cap+code −$354 vs model +$1,496 concentrated in ~5 maps (p=0.95). Correct reading is "no late-third harm", not a gain — F8 above now says so.
- Experiment A expected values differ in tightness, not direction: I predict histfix@cap6 CVaR5 ≈ −450…−500, worst ≈ −1,100…−1,500, fills ≈ 11.6–12.1k/seed; tails-opus predicts CVaR ≈ −430…−480, worst ≈ −1,000…−1,300, fills ≈ 11.0–11.5k. Same ballpark; the run settles it. Their invocation is more precise (`--model-dir data/lol/models/research` = byte-identical histfix members, with `--signal-cadence-seed N`).
- tails-opus rules out "cap position when history is NaN" with the NaN-band evidence (worst maps load deep rungs at 220–490 s with valid history) — consistent with my findings; I never proposed it.
- Where my report adds things the siblings lack: the full 12-map seed-mean ladder with per-map cap-step/model-step split; the buy/sell decomposition showing the model step's damage is sell-side-only (d_sell −4,077 vs d_buy +79 on the worst maps); the first-sell-lag symmetry test with its PnL split; and the `exit-full-l1` existence proof that exit-side changes move the LoL tail.

**Consensus recommendation:** run histfix at cap 6 first (decisive, ~3 seeds); treat the LoL cap as an unvalidated LoL-specific sizing decision (Dota validated 9 on Dota); then attack the exit side (bounded/aggressive exit, mid stop) which is where the residual model-step tail and most of the worst-map variance live.

## Scripts

All in `work/glm-tails/` (read-only analysis; run with `PYTHONPATH=src:scripts uv run python` from `esports-trader/`):

- `schemas.py` — parquet schemas/row counts for both runs.
- `compare_w540lv6_vs_histfix.txt` — captured output of `scripts/compare_backtests.py` (confounded pair: per-seed, per-third, round decomposition).
- `analysis_items_1_2.py` → `items_1_2_output.txt` — item 1 (incompletes) + item 2 (paired per-map deltas, worst-10 sets per seed, cap binding); saves `per_map_delta.parquet`.
- `analysis_item_3.py` → `item3_fills.txt` — full fill timelines for the six worst maps, both runs, seed 0.
- `analysis_quote_timeline.py` → `quote_timeline.txt` — quote-event chronology (gates, cancels, book) around the 684656 stranding.
- `analysis_items_4_5.py` → `items_4_5_output.txt` — buys/sells decomposition, extra-fill attribution (cap-bound vs free), |delta| at fills, exit-side stats; saves `per_map_decomp.parquet`.
- `analyze_clean_pairs.py` → `clean_pairs_output.txt` — the F1/F2 ladder: three-run per-map table, paired stats for both steps, cur→histfix and w540→cur buy/sell decomposition, pure-cap cat77 pair.
- `read_summaries.py`, `read_other_runs.py` — summary.json markout/hold/CVaR blocks for all arms.