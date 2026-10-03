# glm-leadlag: does the Polymarket LoL mid move before our 11 s clock, and is that the decay?

Status: FINAL

## Verdict (≤10 lines)

The structural hypothesis is half right, and the half that is right is not the half that kills us.

- Verified: the LoL mid half-prices a big kill swing 4.7 s after the livestats frame stamp; our model decides at stamp+11 s, after 73% of the 120 s event move is already in the mid. Dota's half-move is 9.3 s with lag 10 — its clock sits before its half-move, LoL's after (item 1).
- But the measured cost of our 11 s lag is small: re-scoring the LIVE model at read-times 0/5/11/20/30 s, entry markout falls only 2.84¢→2.48¢ (−13%) pooled, and the lag tax is stable across months (Sep −15%). The lag alone cannot explain a 2.9¢→0.5¢ collapse (item 4).
- The collapse is real, information-level, and venue-specific: on whitelisted (traded) maps, offline every-second entry markout is 2.69¢ in Sep 1–15 and 0.43¢ in Sep 16–30 — matching the backtest's late-third 0.44–0.61¢ — while non-whitelisted Sep-late maps keep ~7¢ mid-markout (items 4/s4).
- Mechanism (likely): the whitelisted market sped up and shrank its event repricing exactly where we trade — absorbed-by-+11s on traded kill events rose 0.65 (Jun)→0.85 (Sep-late), event repricing C(+120) fell 6.19¢→4.93¢. Non-traded books got slower/thinner (T50 22–47 s). The decay is competitor efficiency in pro leagues, not our feed lag, not momentum chasing, not execution.

## Findings

Numbered, evidence-tagged (verified = reproduced with my own runs; likely = inference from verified numbers; speculative = proposal).

### F1. The LoL mid prices big events ~2× faster than Dota's, and our clock sits after the half-move (verified)

Event study on all 2195 validation maps, Jun–Oct 2026: events = NMS(±10 s) peaks of |10 s change| in net-worth advantage (nw) and net kills (kill), game seconds 60..480, |change| ≥ pooled p95 (LoL nw 375 g, kill 1; Dota nw 415 g, kill 1). Anchor = livestats frame publish stamp (`state_ts_us`, ≈ theoretical boundary −0.1 s); mid at offset k = as-of pair mid at anchor+k on the per-second market tape (2.5 s tolerance). C(k) = mean direction×(mid_k−mid_0).

| pooled | n | T25 | T50 | T75 | first-1¢ med | ≤5 s | ≤11 s | C(+5) | C(+11) | C(+120) | absorbed@11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LoL kill | 4334 | 3.5 s | **4.7 s** | 12.5 s | 4.0 s | 73.1% | 87.2% | 3.12¢ | 4.12¢ | 5.63¢ | **73%** |
| LoL nw | 4540 | 2.4 s | 4.5 s | 24.0 s | 4.0 s | — | — | — | 2.08¢ | 3.30¢ | 63% |
| Dota kill | 3176 | 3.6 s | **9.3 s** | 21.7 s | 6.0 s | 49.5% | 70.0% | — | 1.45¢ | 2.68¢ | **54%** |
| Dota nw | 2996 | — | 13.7 s | — | 8.0 s | — | 60.3% | — | — | — | — |

- LoL kill half-move at 4.7 s vs our decision at +11 s: we act ~6 s after the half-move, with 73% of the 120 s move absorbed. Dota's lag-10 decision sits ~0.7 s before its 9.3 s half-move. (Command: `s1_events.py` fixed run; `work/glm-leadlag/events_lol.parquet`, `events_dota.parquet`, `event_curves_*.csv`.)
- Monthly LoL kill T50: Jun 6.6 s, Jul 4.8, Aug 4.6, Sep 4.4 — fast all year, slightly faster recently. Dota kill T50: Jun 6.8 s → Sep 12.6 s (Dota slowed while its edge grew).
- No pre-event leakage: C(−10) ≈ −0.3¢ (kill), C(−60) ≈ −2.2¢ (nw) — the mid does not move toward the event before the frame stamp; big nw swings follow ~2¢ of adverse drift (overshoot/reversal structure). No one trades the future; they react to the same frames faster than us.
- Quantization caveat (verified): targets at anchor+k pick the tape row at game second s+k−1 (frame stamp ≈ boundary−0.1 s), so all quoted offsets carry ~−0.9 s wall-clock skew, both games equally.

### F2. The 11 s lag costs only ~13% of the model's entry markout — and the tax did not grow (verified)

Lag sweep of the LIVE model (`hist-policy-20261003`, model `20261003T014429Z`, the exact ensemble pinned by `data/backtests/lol_maker/LIVE/seed0/manifest.json`, sha 1873235c…): for L ∈ {0, 5, 11, 20, 30} s after the frame stamp, current_L/future_L = as-of mid at stamp+L / stamp+L+300 from the market tape; the three market features (logit mid, market_vs_prior, market_p_radiant) are recomputed from current_L; snapshot, history, `second` stay at the frame. Metrics on the 683,711 labeled <480 s rows valid at all lags (same population per L).

| pooled, all maps | L=0 | L=5 | L=11 | L=20 | L=30 | native(exact) |
|---|---|---|---|---|---|---|
| dir markout, all rows (¢) | 1.64 | 1.54 | 1.48 | 1.39 | 1.29 | 1.47 |
| dir markout, entries \|Δ\|≥0.02 (¢) | 2.84 | 2.62 | 2.48 | 2.30 | 2.11 | 2.46 |
| entry rate | 36.8% | 36.6% | 36.5% | 36.5% | 36.5% | 36.5% |
| MAE gain (¢) | 0.28 | 0.25 | 0.24 | 0.23 | 0.21 | 0.24 |

- Monthly entry markout (all maps), L=0→L=11: Jun 3.96→3.58, Jul 2.11→1.66, Aug 2.70→2.35, Sep 3.35→3.09. Traded maps: Jun 3.65→3.25 (−11%), Jul 2.68→2.22 (−17%), Aug 3.12→2.73 (−12%), Sep 2.08→1.77 (−15%). The lag tax is stable ~12–17%; it did not worsen in Sep.
- Pipeline gates passed (verified): my re-scoring of the native rows reproduces `model.json` dir_300 = 1.4014¢ exactly on 840,345 rows; the L=11 tape reconstruction differs from the dataset's own `market_p_radiant`/`signal_..._300s` with median |Δ| = 0 (6.6%/8.5% of rows differ by >1¢ — 1 s tape-vs-raw-book quantization). Trainer semantics reproduced from `src/lol/06_train_model.py:81-143,206-210`, metric from `src/lol/lol_validation_metrics.py:64-67`, mean-of-members + clip from `src/shared/utils/gbm.py:82-90,140-147`.
- OOD caveat (likely): the frozen model was trained with the mid at +11 s, where event moves are largely absorbed. Fed a pre-absorption mid (L=0/5), it does not scale its delta up to claim the un-absorbed move (entry count barely moves: 251,350 vs 249,709). So L=0 gains are a lower bound on what a model retrained at a faster clock could express (see E1).

### F3. The decay is real at the information level, and it is whitelisted-venue-specific (verified)

Half-month buckets, native scoring, entries |Δ|≥0.02, traded = match in `data/backtests/lol_maker/LIVE/seed0/results.parquet` (1239 selected maps), cluster-bootstrap CI over maps (1000 resamples):

| bucket (traded) | n entries | maps | dir markout (¢) | 95% CI | hit rate |
|---|---|---|---|---|---|
| 2026-06a | 21,942 | 141 | 2.92 | [1.28, 4.63] | 0.645 |
| 2026-07a | 12,164 | 76 | 1.78 | [−0.72, 4.08] | 0.574 |
| 2026-07b | 31,761 | 191 | 2.64 | [1.07, 4.32] | 0.645 |
| 2026-08a | 39,194 | 241 | 2.95 | [1.55, 4.38] | 0.651 |
| 2026-08b | 23,281 | 178 | 2.53 | [1.05, 3.88] | 0.613 |
| 2026-09a | 24,093 | 175 | 2.69 | [1.02, 4.24] | 0.636 |
| **2026-09b** | 24,162 | 137 | **0.43** | [−1.59, 2.58] | **0.539** |

- All-maps 09b stays 2.82¢ [1.22, 4.41] (212 maps) → implied non-traded 09b ≈ 7.1¢ (13,626 entries). The offline instrument (every-second rows, mid-to-mid +300 s) reproduces the backtest's late-third level (0.44–0.61¢, per 00-context) on the same population — so the backtest collapse is not an execution artifact.
- The 09b CI is wide (entry markout is map-clustered), but the point estimate agrees with the independent backtest measurement of the same window; the all-maps control shows the signal is alive outside the whitelist.

### F4. The whitelisted market got faster and smaller exactly where we trade (verified; interpretation likely)

From the same event vectors, traded-only, half-month (n = 200–550 events per bucket):

| traded kill events | 06a | 07a | 07b | 08a | 08b | 09a | 09b |
|---|---|---|---|---|---|---|---|
| absorbed@+11 s (C11/C120) | 0.65 | 0.72 | 0.72 | 0.75 | 0.84 | 0.81 | **0.85** |
| T50 (s) | 5.7 | 4.6 | 4.8 | 4.4 | 4.0 | 4.1 | **3.8** |
| C(+120) (¢) | 6.19 | 6.48 | 4.86 | 6.05 | 5.62 | 6.08 | **4.93** |

- On traded maps the mid now absorbs 85% of a kill event within our 11 s (Jun: 65%), and the total event repricing shrank ~20%. Event sizes (max_move ~9.4¢) did not shrink — the market just prices them faster and more completely.
- Non-traded 09b maps: kill T50 = 22.3 s, absorbed@11 = 0.36, first-1¢ = 10 s; nw T50 = 46.9 s, absorbed = 0.25 — slow, thin books, consistent with the ~7¢ offline mid-markout there (and with why the whitelist never touched them).
- Likely chain: faster/more complete absorption on pro leagues ⇒ less residual at +11 s for our model ⇒ markout decay concentrated in (whitelist × late Sep). This is a crowding/competitor-efficiency story, not a model-rot story: the same model, same features, still finds 2.8¢ outside the whitelist.

### F5. Recent mid changes carry almost no signal in LoL; the model's edge is not momentum (verified)

Granger-style OLS on native rows (n = 583,499 with all r's finite), y = future_311 − current_311, m = model delta, r11/r30/r60 = decision mid − mid 11/30/60 s earlier (tape as-of):

- pooled: corr(y, m) = 0.145, corr(y, r11) = 0.019; R²(m) = 0.0211, R²(r's) = 0.0016, R²(m + r's) = 0.0212. Incremental value of recent mid changes over the model: +0.0001. Incremental value of the model over the r's: +0.0195.
- Momentum as a strategy: sign(r11) markout = 0.40¢ pooled (vs model 1.63¢ all-rows); where the model is quiet (|m|<0.02), momentum makes 0.25¢. Model delta orthogonalized on the r's still marks out 2.43¢ (226,416 entries) — the edge is state-based, not momentum.
- Entries where the mid already moved with the model's direction in the last 11 s mark out better, not worse (pooled 2.65¢ vs 2.20¢; holds every month) — no "market absorbed my signal" penalty within the entry set.
- Dota contrast (verified, same machinery, pinned snapshot, L=10 reconstruction exact on 100.0% of rows): corr(y, r11) = 0.136, R²(r's) = 0.0216 — 10× LoL — yet still only +0.0006 incremental over the model (R² 0.0499→0.0505). Dota's market momentum is real but already inside the model's mid feature.

### F6. Chase/quiet entry decomposition: no toxicity pattern (verified)

Entries with a large event (item-1 sets, nw ∪ kill) in [s−30, s]: 23% of entries pooled (70,501 of 309,538). Markout chase vs quiet: pooled 2.33¢ vs 2.47¢; Jun 4.55 vs 3.16 (chase better), Jul 1.89 vs 1.82 (even), Aug 1.56 vs 2.50 (chase worse), Sep 3.28 vs 2.73 (chase better). Chase entries carry bigger deltas (3.69¢ vs 3.49¢) as expected. No stable "chasing kills the entry" pattern — the problem is not that we trade into freshly-absorbed events; the whole entry book decayed on whitelisted maps (F3).

### F7. Dota contrast: smaller lag tax, growing edge (verified)

- Dota lag sweep (pinned `dota_snapshot/`, model `20261001T220726Z`, L=0..30 with native L=10): entry markout 3.45¢ (L=0) → 3.25¢ (L=10, −6%) → 2.98¢ (L=30). Dota's 10 s lag costs ~6% vs LoL's ~13%.
- Dota monthly entry markout at native: Jun 1.46¢, Jul 2.47, Aug 3.62, Sep 4.41 — growing, consistent with the backtest's late-third 5.5¢ (00-context).
- Dota native dir on the pinned snapshot = 1.7606¢ vs model.json 1.8188¢ — the snapshot is the 11:26 rebuild (sha 4e3f2323…), not the model's validation build (18d69e54…), so the small gap is expected; the L=10 reconstruction matches the dataset's own `market_p_radiant` exactly (share 1.0000), which also proves the market-seconds caches had not drifted from my pinned dataset.

## What I ruled out

- **Our 11 s lag as the primary decay driver** (verified): the lag tax is 12–17% of entry markout and stable across months; even crediting all of Sep-traded's L=0 gain (+0.31¢) to Sep-late, 0.43+0.31 ≈ 0.74¢ ≪ June's 2.9¢. Speed alone does not restore the edge under the frozen model.
- **Execution/backtest mechanics as the cause of the late-third collapse** (verified): the offline every-second mid-markout reproduces the collapse level (0.43¢ vs backtest 0.44–0.61¢) on the same whitelisted Sep-late population, while all-maps Sep-late is 2.82¢.
- **A global market speed-up** (verified): non-whitelisted Sep-late books got slower (kill T50 22 s, nw 47 s, first-1¢ 10 s). The speed-up is specific to the leagues we trade.
- **Momentum-chasing / signal-absorption-by-mid as the mechanism** (verified): r11/r30/r60 add ~nothing over the model (ΔR² = 0.0001); with-mid entries out-mark against-mid entries; orthogonalized model delta keeps 2.43¢.
- **Event-size shrinkage as the driver on traded maps** (verified): traded 09b max_move ≈ 9.4¢ vs 9.6¢ in June — sizes held; absorption speed is what changed.
- **Pipeline bugs in my measurements** (verified): native gate reproduces model.json dir_300 exactly (1.4014¢, 840,345 rows); LoL L=11 reconstruction median |Δ| = 0; Dota L=10 reconstruction exact (1.0000). Earlier draft of the event study had a tape-sorting bug (global timestamp sort interleaved concurrent maps, corrupting 70% of baselines) — fixed by grouping on match_id contiguity before the run reported here.

## Proposed experiments

Not run (training/prep/backtests are outside this brief's permissions). Ordered by expected value.

1. **Retrain at a faster decision clock (the main lever).** Set `LOL_SOURCE_LAG_SECONDS = 4` in `src/shared/constants/lol.py:52`, re-run Stage 05 prepare + Stage 06 train, then a 3-seed LIVE-config backtest. Rationale: F1/F4 — at +11 s the mid has absorbed 85% (Sep-late, traded) of the kill-event move; at +4 s only ~40–50% is absorbed (C(+5)=3.12¢ of 5.63¢ pooled). Expected effect: entry markout on whitelisted maps back toward ~2× current late level *if* the retrained model learns to claim pre-absorption moves (the frozen model cannot, F2 OOD caveat). Watch: Sep 16+ buy-300s on whitelisted maps specifically (the 09b bucket, not the monthly average); wide-spread/no-quote rate at +4 s right after fights (F1 shows status non-ok clusters at event seconds); label count shrink (rows need +304 s of book). Cost: full re-prepare (hours) + retrain + 3 backtests.
2. **Cheapest probe first — no retrain:** rerun `s2_lol_sweep.py` with `LAGS = [2, 3, 4]` (scoring-only, ~1 min). It bounds what a +2–4 s feed buys under the frozen model (interpolating F2: ~+0.1–0.25¢/entry) and calibrates whether E1's retrain is worth the pipeline. Watch: entry rate at L=2–4 (if the model goes OOD-noisy, entry markout will degrade — that itself is informative).
3. **Second-tier league probe (paper only).** Non-whitelisted Sep-late maps show ~7¢ mid-markout with slow books (T50 22–47 s). Extend `config/lol_league_whitelist.json` in a paper-run config, keep spread/depth gates on. Watch: realized spread + fill rate against the 7¢ mid-markout — thin books mean mid-markout overstates executable edge; if net-of-cost ≥1.5¢ for two weeks, escalate to paper LIVE. Cost: paper only.
4. **Crowding canary (adopt as routine).** Weekly re-run of `s1/s5` on the trailing 2 weeks: plot traded-kill absorbed@+11 s and T50. If absorbed@11 keeps rising (0.65→0.85 in 4 months) while markout falls, the edge is being competed away and venue/clock changes, not model tuning, are the response. Cost: minutes.
5. **Dota momentum features (low priority, Dota side).** r11 has real Dota signal (R² 0.0216, corr 0.136) that the current catalog lacks (only level + prior enter); adding recent-mid-change features at the next Dota retrain is cheap. For LoL this is pointless (ΔR² = 0.0001).

## Comparison with the sibling run (leadlag-sol, read only after my numbers above were final)

- Agreements (independent methods, same conclusions): LoL kill half-move ~4–5 s with ~73–86% of the 120 s move absorbed by +11 s in Sep (theirs: exact-kill onsets on LIVE maps, 86.1% Sep; mine: NMS peaks on all validation maps, 73% pooled / 85% traded Sep-late); Dota slower with more headroom, but its half-move also sits near its lag-10 clock (they rightly weaken "Dota comfortably leads"); lag-sweep entry markout nearly identical in level (their ALL L0→L30: 2.98→2.25¢ on 5 s-sampled eligible snapshots; mine 2.84→2.11¢ every-second); the latency repair is small (their frozen-native late +0 gain: +0.170¢ [0.078, 0.267]; my Sep-traded re-scored +0.31¢); momentum adds ~nothing over the model (their published-delta incremental R² retains 93%; my ΔR² from r's = +0.0001); quiet entries ≥ post-event entries in most cuts.
- What my run adds: the whitelisted-vs-rest control. Their study is LIVE-maps-only, so a "market got efficient" story is untestable there; my all-maps control shows Sep-late offline edge of 2.82¢ overall vs 0.43¢ on traded maps (non-traded ≈ 7¢ with SLOW books, kill T50 22 s), and the traded-kill absorbed@+11 trend 0.65→0.85 over four months — the decay is venue-specific, which supports their "not model rot" reading and sharpens it.
- What their run adds: the late-cut localization (Aug 22+, frozen-native directions, cluster CIs), the 40.7% "event not yet in the scored state" classification caveat, and the observation that quiet-entry markout also fell late (their 3.655→2.111¢) — a pure kill-repricing story is indeed insufficient; on my numbers the quiet fall is visible within the whitelist subset, and the venue contrast (F3/F4) reconciles both.
- Minor: they scored the research ensemble (20261003T091511Z), I scored the LIVE-pinned dir (hist-policy-20261003, model 20261003T014429Z) — same metrics (dir_300 1.4014¢), so results are comparable; my native gate reproduced model.json exactly.

## Scripts

All in `investigations/2026-10-03-lol-edge/work/glm-leadlag/`, run from the esports-trader root with `PYTHONPATH=src:scripts nice -n 10 uv run python <script>`:

- `s0_schema.py` — schema recon + Dota snapshot pinning (`dota_snapshot/`: validation_dataset sha 4e3f23239d28267a, game_features sha 0df1a5f1bee68dbd, research model copy; Dota data was being rewritten by a running prepare at 11:26, so everything Dota here reads only the pinned copies).
- `s1_events.py` — event study (item 1): events, per-event move vectors at −60..+120 s, pooled/monthly/traded curves. Outputs `events_lol.parquet`, `events_dota.parquet`, `event_curves_*.csv`.
- `s1_debug.py` — join debugging that exposed the tape-sorting bug (kept for the contiguity pattern).
- `s2_lol_sweep.py` — LoL lag sweep (item 4), Granger + agreement cuts (item 2), chase/quiet (item 3). Native gate 1.4014¢. Outputs `sweep_lol.csv`, `granger_lol.csv`, `chase_lol.csv`, `agree_lol.csv`.
- `s3_dota_sweep.py` — Dota lag sweep (item 5) on the pinned snapshot; L=10 exact-reconstruction canary (1.0000). Outputs `sweep_dota.csv`, `granger_dota.csv`, `chase_dota.csv`.
- `s4_halfmonth.py` — half-month traded-vs-all native entry markout with map-cluster bootstrap (F3). Output `halfmonth_lol.csv`.
- `s5_event_halfmonth.py` — traded-only, half-month event absorption/speed (F4) from the s1 vectors. Output `event_halfmonth_lol.csv`.

Key code references: `src/lol/05_prepare_dataset.py:254-287` (as-of join at +11/+311, prior at spawn), `src/shared/constants/lol.py:52` (lag 11), `src/lol/06_train_model.py:81-143,206-210` (row selection + scoring), `src/lol/lol_validation_metrics.py:64-67` (markout definition), `src/shared/utils/gbm.py:82-90,140-147` (ensemble mean, clip), `src/shared/utils/dota_features.py:86-88,411-470` (history attach + market-derived overwrite), `src/shared/utils/telonex_book.py:197-240` (5 s staleness + pair gates), `src/train_model/train_model.py:81-113` (Dota lag semantics, ok-row selection).