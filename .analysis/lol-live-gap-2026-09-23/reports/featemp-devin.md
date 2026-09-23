# featemp-devin — LoL feature & prediction parity: livestats (train) vs GRID (live)

Brief: numerically compare training-side LoL livestats features against live GRID-derived
features on real shared maps, quantify timing offsets, run the production model on both,
and reproduce live `radiant_fair`/`yes_fair` from archives.

Sample: 305 LoL GRID trader archives scanned, 272 link to livestats (link table + raw
windows/details), 262 built cleanly through both production code paths
(`maps/*.parquet`, `build_results.csv`). All feature extraction uses production code:
GRID path via `GridFrameReducer`/`replay_grid_records` (`src/trader/grid_feed.py`,
`src/trader/grid_archive.py`), training path via `prepare_map_livestats_until`
(`src/lol/livestats_frames.py`, `src/lol/networth.py`, `src/lol/05_prepare_dataset.py`).
No parsers were reimplemented.

## TL;DR

1. **Live prediction path is exactly faithful** — replaying the archived GRID feed through
   the production reducer and the map's own model reproduces `radiant_fair` bit-exactly
   on 99.72% of 33,069 live signal rows (all 94 mismatches are duplicate-journal-row
   pairing artifacts on the oldest-model era, |err| ≤ 5.5¢); `yes_fair` is bit-exact on
   104,019 rows / 257 maps. No live prediction-path bug exists.
2. **`market_radiant_prior` is computed with two different anchors live vs dataset** —
   live: minute-bar mid before `horn − 90s`; dataset: last two-sided tick mid in
   `[spawn − 61s, spawn)`. Only 40/205 shared maps match exactly; median |Δ| = 2.0¢,
   72% > 1¢, 14% > 5¢, max 33.5¢. On live decision rows this moves predictions
   med |Δ| = 0.07¢, p90 = 1.1¢, p99 = 4.0¢, flips 4% of signs, and opens the 0.02 gate
   one-sidedly on 9.7% of decisions. Verified definition mismatch; medium severity.
3. **Live feed cadence is sparse and bursty; the backtest under-models it** — live median
   94 ticks per map in seconds 0..540 covering ~81 unique seconds (~15%), median gap 3.2s, median
   worst-gap 71s, mean stale-blocked share 16.1% (>16s without a tick). 782/945 (83%) of
   backtest maps replay synthetic `grid-v1` i.i.d. cadence (mean intervals 8/6/5s by band)
   whose expected stale share is ~6.7% — half the real blocking — and which cannot produce
   clustered stalls. Only 163/945 replay real archive schedules. Highest plausible
   contributor to live < backtest among what I measured.
4. **Feature-value skew (net worth / XP / top1) is real but near-zero-mean** —
   `radiant_nw_adv` med |Δ| = 79g (p90 452, p99 992) vs livestats reconstruction, exactly
   the residual the consumed-item reconstruction ceiling predicts (`src/lol/networth.py`);
   `radiant_xp_adv` nonzero on 21% of pairs (p90 680) but is pure level-boundary timing —
   GRID level sums are monotone on 41,729 frames / 72 maps. Net effect on production
   predictions: med |Δ| = 0.16–0.21¢, p99 ≈ 3¢, 7–8% sign flips, ~9–10.5% one-sided gate
   openings, mean ≈ 0. Noise, not a directional bias.
5. **GRID's `second` label runs +2.5s ahead of the livestats convention** for the same
   state (median; 259/262 maps within ±5s) — either the scoreboard clock is ~2.5s fast or
   the effective table delay is ~10.5s vs the declared 8. Verified, but **harmless**:
   `second` is a model no-op (±10s shift on 50k rows → med |Δpred| 0.000¢, p90 0.008¢).
   It also means the three-way `second` convention split (train raw S, validation/grid-v1
   S−10, live/schedule S_grid≈S+2.5) is numerically irrelevant.
6. **`market_p_radiant` co-timing differs between dataset/grid-v1 and live/schedule** —
   the dataset stores the mid at the *state's* wall time while live pairs the ~8s-old
   state with the mid at decision time; the schedule path (17% of backtest maps) reproduces
   this faithfully, the grid-v1 path (83%) does not. Verified construction difference;
   speculative PnL sign.
7. **Pause handling is consistent** (offset drift ≈ 0 on all 108 pause maps), GRID XP
   never regresses, deaths are identical on 98.8% of pairs, and side orientation is
   correct — the `GRID vs livestats` skew is a measurement-quality gap, not a logic bug.

## Findings

### F1 — Live `market_radiant_prior` uses a different anchor than the dataset prior

- **Stage:** live (prior fetch) vs prepare (dataset prior).
- **Claim:** the same model feature is two different quantities live vs training.
  - Dataset: `lookup_strict_prior` = last two-sided tick-book mid strictly inside
    `[spawn − 61s, spawn − 1s]` — `src/lol/05_prepare_dataset.py:180-192`,
    `LOL_PRIOR_WINDOW_SECONDS = 61` at `src/lol/constants.py:138`.
  - Live: `fetch_market_prior` anchored at `horn_unix_seconds − HORN_OFFSET_SECONDS`
    (`HORN_OFFSET_SECONDS = 90`, `src/shared/utils/match_time.py:9`,
    `src/trader/match_worker.py:508`), reading 1-minute `prices-history` bars over the
    trailing 6h (`QUOTE_TRAILING_SECONDS = 6*3600`, `src/shared/constants/api.py:14`)
    and taking `last_aligned_pre_anchor_pair` (`src/shared/utils/price_history.py:60-75`,
    rewinds both legs to the older bar when the lasts are >30s apart).
- **Evidence:**
  - `out/prior_diff.csv`: 205 maps with both priors. Exact match 40/205;
    |live − ds| median 2.0¢, mean 2.88¢, p75 3.5¢, max 33.5¢
    (e.g. grid-2965525-m1: live 0.595 vs ds 0.360; grid-2964617-m1: 0.49 vs 0.44).
    Command: `PYTHONPATH=src uv run python` heredoc comparing
    `signal.market_radiant_prior` vs `validation.parquet`/`production_training.parquet`
    prior joined via `split.parquet` (reproduced in `post_analysis.py`-adjacent script,
    see Scripts).
  - Uniform across map numbers (median 1.25–2.0¢ for m1..m5) — not a map-2+ horn bug;
    it is the ~90s anchor offset plus minute-bar granularity/staleness on thin markets.
  - `out/prior_impact.csv` (84,912 live `model` signal rows, production model
    `20260921T095813Z`, grid features + signal market_p, only prior swapped):
    Δpred live-prior vs ds-prior: mean −0.038¢, med 0.000¢, med|Δ| 0.071¢,
    p90|Δ| 1.126¢, p99|Δ| 3.960¢, max 9.528¢; sign flips 4.0%; one-sided 0.02-gate
    9.7% (live-only 5.5%, ds-only 4.1%); corr(prior diff, pred diff) = 0.374.
- **Mechanism:** the model reads `market_p_radiant` together with `market_radiant_prior`
  (e.g., "how far has the market moved from pre-game consensus"). The live prior is an
  ~90s-older and minute-quantized anchor, so `(market_p − prior)` is systematically
  shifted vs the training distribution. Mean shift is small (+0.31¢ prior, −0.04¢ pred),
  but per-map it reaches 30¢+ — on those maps every live decision ran on a prior the
  trainer never saw. In the backtest both sides always use the dataset prior, so the
  backtest cannot see this skew at all.
- **Severity:** medium (large per-map tail; near-zero mean).
- **Confidence:** verified (two different code paths + measured differences + measured
  prediction deltas).
- **Next check / fix:** pick one anchor definition. Cheapest consistent direction: live
  fetches the prior at horn−90s only because spawn isn't known at join; the dataset can
  reproduce the *live* convention exactly (minute bars before horn−90) if we want the
  backtest to see the skew. Alternatively anchor both at spawn−1s and pass the dataset
  prior to the worker from a local file instead of HTTP. Also worth logging the chosen
  bar timestamps: the 30¢+ outliers on thin map markets likely came from stale minute
  bars, which `last_aligned_pre_anchor_pair` silently accepts (only the 30s *leg-alignment*
  slop is bounded; bar age is unbounded within the 6h window).

### F2 — GRID `second` labels run +2.5s ahead of livestats labels for the same state

- **Stage:** live (clock/label) vs prepare (slot labels).
- **Claim:** for identical underlying game state, GRID `snapshot.second` ≈ livestats slot
  `second` + 2.5 (median). GRID `second` = `live_clock_seconds(board, age) − table.feed_delay`
  (`src/trader/grid_feed.py:172`, feed_delay = 8s declared GRID table delay);
  livestats `second` = integer slot with the latest frame of `game_time ≤ S` within 2s
  (`src/lol/livestats_frames.py:633-656`).
- **Evidence:**
  - `out/offsets.csv`: 262 maps; chosen per-map offset (state-aligned
    `grid_second − ls_second`) median +2.5, mean +3.14, 259/262 within ±5
    (107 maps +3.0, 112 +2.0, 15 +2.5). Two outliers: grid-2996949-m4 at −6.0
    (constant across the whole map — a GRID clock 6s *slow*), grid-3005559-m2 at +204
    (late join: archive starts at second 812, horn 15:06:16 vs join 15:19:56;
    offset is ill-posed — first tick already shows deaths 12/6 which livestats reached
    at ~775-784, so true offset ≈ +30s; either way the map contributes nothing inside
    the 0..540 model window).
  - Pairing jitter after alignment: n=99,208 pairs, `grid_second − ls_second − δ`
    mean −0.069, sd 0.788, p10/p90 = −1/+1 (`pairs.parquet`).
  - XP first-attain step offset (independent check): count=261, mean +2.377, median +2,
    i.e., the level-step function agrees with the global offset — no extra XP column
    defect (`out/xp_step_offsets.csv`).
  - `second` is a model no-op: production model on 50k validation rows, `second−10` →
    med|Δpred| 0.000¢, p90 0.008¢, sign flips 0.06%; `second+2.5` → p90 0.000¢
    (`post_analysis.py` output).
- **Mechanism:** the label bias shifts live `second` inputs ~2.5s up vs training rows —
  harmless to predictions (no-op feature) and to gates (BUY_CUTOFF 480 fires ~2.5s
  early). The *convention* finding still matters because the same `second` column means
  three different things: dataset/training rows store raw slot S
  (`fit_research_members`/`fit_production_members` fit `train[list(features)]` unshifted —
  `src/shared/utils/gbm.py:256-263, 273-279`), validation metrics and the grid-v1
  backtest feed `S − lag` (`src/train_model/train_model.py:75-79`,
  `src/backtest/signals.py:271`), while live and the archive-schedule path feed the GRID
  label ≈ S+2.5 (`src/trader/grid_feed.py:172`, `src/backtest/signals.py:396`).
  Verified harmless only because the model ignores `second` within ±10s.
- **Severity:** low (label bias itself), but the convention split is a latent footgun —
  if a future model learns `second`-sensitive splits, validation/grid-v1 evaluate a
  feature 12.5s away from what live serves.
- **Confidence:** verified (measured on 262 maps + model sensitivity).
- **Next check / fix:** decide which `second` the model input is supposed to be
  (state age vs decision second) and make all four paths emit it identically; add a
  `feature.second` regression test. To decompose the +2.5s (clock-fast vs
  table-delay-5.5s), log GRID `occurredAt` vs receive ts for one live map on the VPS.

### F3 — Net-worth / XP / top1 feature values differ (measurement-quality gap, ~zero mean)

- **Stage:** prepare (livestats reconstruction) vs live (GRID table).
- **Claim:** per-second GRID features differ from livestats features by a
  zero-mean-ish noise floor consistent with the consumed-item reconstruction ceiling
  (`src/lol/networth.py` docstring names GRID-vs-livestats residual the accuracy bound).
- **Evidence:** pooled aligned pairs `out/pairs.parquet`, n=99,208, 261 maps
  (`grid − livestats` at matched true-time state):
  | feature | mean | med\|Δ\| | p90\|Δ\| | p99\|Δ\| | nonzero | corr |
  |---|---|---|---|---|---|---|
  | radiant_nw_adv | −11.9 | 79 | 452 | 992 | 97.3% | 0.976 |
  | radiant_nw | +28.9 | 76 | 390 | 823 | 97.2% | 0.999 |
  | dire_nw | +40.8 | 75 | 390 | 749 | 97.2% | 0.997 |
  | radiant_xp_adv | −5.6 | 0 | 680 | 1180 | 20.8% | 0.963 |
  | deaths_radiant | +0.017 | 0 | 0 | 1 | 1.2% | 0.990 |
  | deaths_dire | +0.006 | 0 | 0 | 1 | 1.2% | 0.998 |
  | top1_nw_adv | −2.4 | 25 | 246 | 573 | 80.1% | 0.946 |
  | radiant_top1_nw_ratio | ~0 | 0.002 | ~0 | ~0 | 97.9% | 0.926 |
  | dire_top1_nw_ratio | ~0 | 0.002 | ~0 | ~0 | 97.8% | 0.927 |
  GRID per-side net worth runs +29/+41g higher than livestats reconstruction — the
  expected direction: livestats `totalGold − consumed` while GRID net worth keeps
  consumed value. The *advantage* is near-unbiased (−11.9 mean on ~hundreds-of-gold
  noise). XP nonzero rate is 20.8% but concentrated at level-boundary seconds
  (both sides use `xp_advantage(LOL_LEVEL_XP, levels)` — `livestats_frames.py:39`,
  `grid_feed.py:180`); per-side GRID level sums are strictly monotone — 0 regressions
  over 41,729 table frames / 72 maps — so the flicker is the +2.5s timing offset
  straddling level steps, not rollback. Lead/lag (`out/leadlag.csv`, per-map cross-cor
  argmax): median +2 for every feature (deaths +2.0, nw_adv +2.0, xp_adv +2.0),
  corr 0.94–1.00 — consistent with the global clock offset, no column-specific lag.
- **Model impact:** production model on the same market inputs (`out/model_diff.csv`,
  33,068 live decision rows):
  - nominal pairing (ls row at same labeled second): Δpred med 0.000¢, med|Δ| 0.208¢,
    p90|Δ| 1.275¢, p99|Δ| 3.317¢, max 11.7¢; sign flips 8.3%; one-sided 0.02-gate 10.5%
    (grid-only 4.9%, ls-only 5.6%).
  - aligned pairing (ls row at true-time-matched second): med|Δ| 0.160¢, p90|Δ| 1.072¢,
    p99|Δ| 2.930¢; sign flips 6.9%; one-sided gate 9.0%.
- **Mechanism:** feature noise translates into ~0.2¢ median prediction noise — an order
  of magnitude below the ~2.8¢ backtested per-fill markout — but near the 0.02 entry
  gate it flips ~1 decision in 10. Symmetric noise cannot explain a systematic
  live < backtest gap by itself; it can only widen variance and thin the strongest-edge
  decisions at the margin.
- **Severity:** low-medium.
- **Confidence:** verified.
- **Next check / fix:** none needed for correctness; if lower noise is wanted, the
  consumed-value residual is documented in `src/lol/networth.py` — the improvement path
  is per-purchase event reconstruction from details timelines rather than
  inventory-difference accounting.

### F4 — Live feed cadence is sparse and bursty; 83% of the backtest uses i.i.d. synthetic ticks

- **Stage:** live (feed) vs backtest (signal cadence).
- **Claim:** the live LoL trader makes ~94 decisions in the 0..540 window with clustered
  multi-second-to-minute stalls; the backtest's `grid-v1` fallback — used for 782/945
  maps — draws i.i.d. ticks that reproduce the *mean* rate but not the *clustering*,
  halving the time the strategy would be entry-blocked by the stale gate.
- **Evidence:**
  - Live cadence (`out/coverage.csv`, `post_analysis.py`; 260 maps with ≥10 ticks):
    median 94 ticks / 81 unique seconds in 0..540 (81/540 ≈ 15% coverage); median inter-tick gap 3.2s;
    median per-map p90 gap 12s; **median per-map max gap 71s** (p75 73s, max 439s).
    Fraction of the window where the freshest tick is >16s old
    (`GRID_FEED_STALE_SECONDS = 16`, `src/shared/constants/strategy.py:29`):
    mean 16.1%, median 13.6%, p90 25.7%; 189/260 maps exceed 10%.
  - grid-v1 bands (`src/backtest/signals.py:75-80`): LoL mean intervals 8s/6s/5s for
    0-180/180-360/360-540. i.i.d. expected stale share ≈ 6.7%
    (Σ band·(1−1/m)^16 / 540). Mean tick counts are roughly calibrated
    (live 26.7/30.9/39.7 per band vs grid-v1 ~22/30/36) — the *distribution* is what's
    wrong: geometric sampling can't produce a 71s median worst-case stall.
  - Backtest population: `data/backtests/lol_maker/LIVE/seed0/results.parquet` (945
    matches) ∩ `data/archive_index/index.parquet` (lol & admitted): **163/945 (17.3%)
    replay real archive schedules; 782/945 (82.7%) use seeded grid-v1**. Manifest:
    `signal_source: auto`, `signal_cadence_seed: 0`, `min_abs_delta: 0.02`,
    `model: data/lol/models/research 20260921T095801Z`. The archive index itself has
    281 valid LoL archives / 275 admitted — so even the 17% schedule-replay subset is
    ~60% of all archives; the rest of the validation set simply predates archiving.
  - Live book availability: of 132,482 signal rows, 14.4% `missing_book`, 2.2%
    `one_sided_book`, 1.1% `stale`, 0.2% each `paused`/`finished`/`missing_prior` —
    i.e., on ~18% of live feed ticks no `model` decision was emitted at all
    (`post_analysis.py` reason counts). In grid-v1 replay every sampled tick with a
    dataset row produces a decision; whether the replayed book stream reproduces the
    live book gaps needs a per-map fill-time book audit (see Needs).
- **Mechanism:** entry requires a fresh feed tick. Live loses a median 13.6% of the
  window to staleness outright, and the gaps cluster exactly where fills would
  concentrate (volatile stretches, pauses, feed stalls). grid-v1's i.i.d. ticks
  re-decide every ~6s on average and almost never sit stale → the backtest enters where
  live was frozen. Schedule-replay maps (17%) do not have this problem; the bulk of the
  LoL backtest does.
- **Severity:** high (largest measured structural difference between the live decision
  stream and the backtest decision stream).
- **Confidence:** verified for all counts/distributions; **likely** as a PnL driver —
  needs the rerun below to size the effect.
- **Next check / fix:** rerun `lol_maker` restricted to the 163 archive-bound matches
  and compare per-map outcomes vs their grid-v1 twins; if the schedule-replay cohort's
  markout drops toward live's ~flat result, cadence is confirmed as the dominant term.
  Separately, drive grid-v1 intervals from the empirical gap distribution (mixture with
  a stall component) instead of pure geometric.

### F5 — `market_p_radiant` co-timing differs: state-wall mid (dataset/grid-v1) vs decision-wall mid (live/schedule)

- **Stage:** prepare vs live vs backtest.
- **Claim:** the dataset row pairs `features@S` with the book mid at the *state's* wall
  time (`lookup_market_p_after(radiant_book, dire_book, slot.state_wall_us, 0)` —
  `src/lol/05_prepare_dataset.py:310`, and `state_wall_us` is the chosen frame's wall
  time — `livestats_frames.py:652`). Live pairs `features(state ≈8s stale)` with the book
  mid at decision time. The backtest's two paths disagree: the schedule path anchors at
  `tick.received_ns` (`src/backtest/signals.py:339` — faithful to live), while grid-v1
  feeds the dataset's stored `market_p_radiant` (`src/backtest/signals.py:281-287` —
  co-timed with the state). Model input `market_p_radiant` is therefore ~8s fresher
  relative to the state live than in either the training rows or the grid-v1 backtest.
- **Evidence:** construction (cited lines) + the F2 offset measurement (state is ~8s old
  at decision; GRID declared table delay 8s, measured effective ~10.5s).
- **Mechanism:** markets drift in 8s, especially early game. Live, the model sees a mid
  that has already moved ~8s beyond the game state it describes; training assumed them
  co-timed. Sign of the effect is not established (a fresher mid is *more* information,
  so this may mildly help live) — but it is an unconditional feature-distribution shift
  on every single live decision, stacked on top of F1's prior shift.
- **Severity:** low-medium.
- **Confidence:** verified construction difference; speculative on PnL sign.
- **Next check / fix:** quantify mid drift over the state→decision lag on the archived
  telonex book tapes (mean |mid(t) − mid(t−8s)| in cents), and decide which convention
  is intended; then align the dataset (mid at `state_wall + lag`) or the live signal
  (mid at the tick's *state* time, if a book tape is available).

### F6 — Live prediction path reproduces exactly; `yes_fair` conversion exact

- **Stage:** live.
- **Claim:** GRID archive → `GridFrameReducer` → `GameSnapshot` → `predict_fair` with the
  map's own historical model → `session.jsonl` `radiant_fair` reproduces to float
  precision on 99.72% of rows; `yes_fair` = `radiant_fair` (yes_is_radiant) or
  `1 − radiant_fair` exactly on 104,019 rows / 257 maps.
- **Evidence:** `out/sanity.csv` (33,069 rows): exact (<1e-9) 99.7157%; the 94
  mismatches are all on maps run under model `20260831T120859Z` where the session
  journal wrote duplicate signal rows per event (model + missing_book; 54/262 maps have
  signal/event ratio ≈ 2, 208 ≈ 1). |err| median 0.30¢, max 5.49¢ — consistent with
  pairing the row to the wrong same-second event, not a prediction defect.
  `post_analysis.py` yes_fair: max|err| = 0.0 on all 257 maps.
- **Mechanism:** rules out "the live model computes something different than designed"
  as a cause of the gap.
- **Severity:** none — this is a cleanliness result.
- **Confidence:** verified.

## Checked and OK

- **Feature column order/contract:** live `_build_feature_values` emits exactly
  `FEATURE_COLUMNS` order (`src/shared/utils/gbm.py:36-50`); model.json `features`
  match; booster-side feature names are asserted equal at load (`gbm.py:475-476`).
- **Side orientation:** GRID board→market orientation pinned by `GridFrameReducer`;
  deaths_radiant/dire identical between sources on 98.8% of pairs — a swapped-side bug
  would show systematic death/sign inversion; none seen.
- **Pause handling:** 108/261 maps have detected pauses (median 47.8s on
  grid-2964617-m1 etc.); per-map offset drift slope ≈ 0 everywhere (median −0.0000,
  p90 +0.0001 s/s) — GRID's clock freeze and livestats pause subtraction agree.
- **GRID XP monotonicity:** 0 level-sum regressions in 41,729 table frames over 72 maps
  — the `xp_adv` flicker is timing, not rollback.
- **`second` no-op:** ±10s shifts move production predictions <0.01¢ median — the
  train/validation/backtest/live `second` convention split (S / S−10 / S+2.5) is
  numerically irrelevant for this model.
- **Grid-v1 mean cadence is roughly calibrated** to live rates per band
  (26.7/30.9/39.7 live vs ~22/30/36 synthetic ticks per map) — only the gap
  *distribution* is off (F4).
- **Archive coverage:** 281 LoL archives with valid metadata, 275 admitted; exclusions
  are 2× `feed:no_window_updates`, 2× `record:feed_gone`, 2× `record:no_terminal` —
  small and explainable.

## Open questions for the owner

1. Which `market_radiant_prior` anchor is intended — `spawn−1s` tick mid (dataset) or
   `horn−90s` minute bar (live)? Should live load the prior from the dataset table
   instead of HTTP, or should the dataset adopt the live anchor for parity?
2. Is `market_p_radiant` meant to be the mid at state time or at decision time?
   Training and grid-v1 use state time; live and schedule replay use decision time.
3. What fraction of the 782 grid-v1 backtest maps have book data that even exists at
   the sampled ticks — i.e., does the book replay reproduce live's 14.4%
   `missing_book` + 2.2% `one_sided_book` rate, or does the backtest quote into books
   live never saw?
4. Should the live signal journal emit one row per event (current duplicate
   model/missing_book behavior on the old era makes replay audits ambiguous)?

## Needs from VPS

- None blocking — all archives analyzed are local. Optional: pull the telonex book tape
  for ~20 of the live maps to (a) measure |mid(t)−mid(t−8s)| drift for F5, (b) audit
  whether the backtest's book replay contains the same gaps that produced
  `missing_book`/`one_sided_book` live.
- Optional: on one live map, log GRID `occurredAt` vs receipt ts to decompose the
  +2.5s `second` bias (clock-fast vs declared-8s-delay overcount).

## Scripts and outputs

All under `…/.analysis/lol-live-gap-2026-09-23/work/featemp-devin/`:

- `build_features.py` — per-map extractor (GRID replay + livestats prep + signals) →
  `maps/<match>.parquet` + `<match>.meta.json` (262 ok of 272 candidates);
  `candidate_maps.csv`, `build_results.csv`.
- `analyze.py` — offset fit, aligned pairing, per-feature diffs, lead/lag →
  `out/offsets.csv`, `out/pairs.parquet`, `out/featdiff.csv`, `out/leadlag.csv`,
  `out/coverage.csv`, `out/xp_step_offsets.csv`.
- `model_diff.py` — sanity (live model reproduces radiant_fair) + production model on
  grid vs livestats features, same market inputs → `out/sanity.csv`,
  `out/model_diff.csv`.
- `post_analysis.py` — yes_fair check, signal reasons, stale share, band tick rates,
  `second` sensitivity → `out/post_analysis.txt`, `out/stale_share.csv`.
- `out/prior_diff.csv` — live vs dataset prior per map (205 maps).
- `out/prior_impact.csv` — prediction deltas under live vs dataset prior (84,912 rows).
- `smoke_one_map.py` — single-map end-to-end smoke test.

Commands (all run from `esports-trader`):

```bash
PYTHONPATH=src uv run python <workdir>/build_features.py          # ~262 maps, ~40min
PYTHONPATH=src uv run python <workdir>/analyze.py                 # pairing + stats
PYTHONPATH=src uv run python <workdir>/model_diff.py              # sanity + model skew
PYTHONPATH=src uv run python <workdir>/post_analysis.py           # consolidated checks
```
