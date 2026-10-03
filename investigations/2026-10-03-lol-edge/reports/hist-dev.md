# hist-dev: LoL history features — train vs backtest vs live parity
Status: FINAL

## Verdict

The history *mechanics* are now one shared code path (`HistoryPolicy` +
`SnapshotHistory` + `history_feature_block`) across training, backtest and
live — verified end to end. At matched level values, sparse/received-tape
history changes predictions negligibly: mean |Δpred| 0.0007 on the synthetic
cadence and 0.0016 on real archive tapes, with ~0 buy-window threshold flips.
So the tape-sparsity/jitter channel is a non-factor for LoL.

Three real inconsistencies remain:

1. The backtest's drop policy over-fires ~10× vs live (2.5% vs 0.245% of
   ticks) because the synthetic tape inherits *dataset row holes* — mostly
   market-coverage gaps (corr(invalid, rows-holes>60s)=0.81;
   corr(invalid, skipped_market_rows)=0.63), not feed stalls. Live history
   drops are essentially inert (20 in-window ticks over 290 archived maps).
2. The bigger live↔train gap is not the tape but the *values*: live snapshot
   fields come from the GRID scoreboard reducer, the dataset recomputes from
   livestats frames. Same-label networth differs ~114-170 g median (≈80%
   explained by a ~3-4 s per-tick label lead); `radiant_xp_adv` is level-
   bucketed live. Live-vs-dataset-domain prediction shift is ~0.008-0.01 mean
   |Δpred| — ~10× the tape effect — with a −0.4¢ systematic bias in-window.
3. History features only carry 11.4% of the LoL model's split gain (Dota:
   47.3%), and the top-gain history columns (`game_total_5m_*`) are
   structurally NaN until second 360 — 65-70% of the buy window. Whatever the
   tape does to history cannot move LoL much either way; that, not a bug, is
   why the last two changes barely moved LoL.

Tails: dropping ticks did not cause the worse tails — corr(Δpnl, invalid)
≈ 0 across seeds and every worst-10 histfix map has zero dropped ticks. The
tail widening is model-retrain variance between catalogs.

## Findings

### 1. The mechanics are genuinely shared (verified)

- `GRID_HISTORY_POLICY` = start_second 60, max_pivot_gap 30s,
  drop_gap_ticks=True (`src/shared/utils/dota_features.py:86`). Pivots are the
  nearest taped second within ±30 s, backward or forward
  (`_nearest_pivot_indices`, `dota_features.py:219-238`); minute diffs cascade
  pivot-to-pivot (`history_feature_block` `dota_features.py:280-287`).
- Training attach uses the same block on the dense per-second tape with gap
  0.0 → exact pivots (`attach_history_features` `dota_features.py:437-444`),
  and NaN for targets <60 — identical warmup semantics to live.
- Live records every non-terminal snapshot before the gate
  (`match_worker.py:234-235`, record; `match_worker.py:426-430`,
  `check_required_lags` → `history_gap` journal + early return), and decision
  ticks derive through `history.derived` in `predict_fair`
  (`match_worker.py:501`). `check_required_lags` drops a tick only when a
  lag target ≥ max(60, first_taped_second) has no pivot within 30 s
  (`dota_features.py:338-360`).
- NaN reaches LightGBM natively; splits on history columns carry NaN-aware
  decision types (member_00.txt `decision_type` bit 3 set on those splits —
  `data/lol/models/research/member_00.txt` header).

### 2. Invalid/dropped ticks — synthetic cadence (verified)

`work/hist-dev/lol_grid.py`, cmd:
`PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python work/hist-dev/lol_grid.py`
on all 1246 eligible whitelisted validation maps × seeds 0-2 (published runs
use 1239; difference is 7 maps dropped later, immaterial):

| month   | maps | ticks    | invalid | inv%  | inv in [60,480) | stale | maps w/ ≥1 inv | max run |
|---------|------|----------|---------|-------|-----------------|-------|----------------|---------|
| 2026-06 | 153  | 141,430  | 4,793   | 3.39% | 261             | 1,207 | 75             | 90      |
| 2026-07 | 290  | 292,310  | 5,749   | 1.97% | 361             | 1,702 | 72             | 67      |
| 2026-08 | 462  | 423,482  | 10,485  | 2.48% | 847             | 2,749 | 126            | 74      |
| 2026-09 | 341  | 315,335  | 8,544   | 2.71% | 1,036           | 2,074 | 87             | 80      |
| total   | 1246 | 1,172,557| 29,571  | 2.52% | 2,505 (~1.1% of window ticks) | 7,732 | 360 (28.9%) | 90 |

- No ticks below second 76 exist (`GRID_V1_FIRST_TICK_SECOND["lol"]=76`,
  `signals.py:70`); first tick is connect-only (stale) and the second is
  always fresh (`feed_schedules.py:477-487`).
- Ticks at seconds <360 can't have `game_total_5m_*` (target <60 → NaN):
  13.4% of parity-sample decisions carry all-NaN totals; `change_1m` all-NaN
  on ~1.7%. Identical NaN profile in the dense arm (start_second=60 applies
  to both).
- Driver: dataset row holes. 302/1246 maps have a >60 s gap between
  validation rows; corr(invalid, row_holes>60s) = 0.81; 295 of 360
  invalid-bearing maps have one. The holes trace to `skipped_market_rows`
  (corr 0.63; mean 317 skipped/map) — market-data gaps — not feed stalls
  (`skipped_age_rows` corr −0.01). On the 60 worst maps the drop rate reaches
  32-46% of ticks for seconds ≥360.
- So in the *backtest* the policy mostly encodes "don't decide where the
  dataset has no market row" — a data-coverage artifact, not feed quality.

### 3. Real feed cadence & invalid rate — live archives (verified)

`work/hist-dev/lol_archives.py` over the 290 admitted LoL schedules joined to
whitelisted validation maps (all 2026-09, the live deployment window):

- 122,380 ticks → 300 invalid (0.245%), 20 in [60,480); 552 stale (0.45%);
  12/290 maps (4.1%) saw ≥1 drop; longest invalid run 79.
- Arrival cadence: median gap 2.4 s, p90 11.2 s, p99 27.6 s, max 1099 s;
  265/290 maps have ≥1 wall gap >45 s; every map has ≥1 paused tick (630
  total). Real GRID ticks are ~2-4× denser than the synthetic bands (5-8 s).
- First tick game_second median 4 (vs synthetic floor 76); live may decide
  from second 0 — `in_model_window` accepts ≥0 (`session_quoting.py:96-101`),
  and `check_required_lags` is vacuous while the tape is empty — 93 archive
  decisions at second <60 (all-NaN history) that grid-v1 never produces.
- Pivot reach on the real tape (new rule): mean |pivot−target| 2.78 s, p90 7,
  p99 15 — near-exact.

### 4. Dense-vs-received parity + model shift (verified)

`work/hist-dev/score_parity.py` + `arch_score.py`; published model
`data/lol/models/research` (10-member default_sub90, L1).

Grid-v1 (557,710 decisions, 623-map parity sample × 3 seeds), buy window:
- mean |Δpred| = 0.00064, p99 = 0.005, max 0.037; |delta|≥0.02 threshold
  flips 0.01%; direction flips 0.00%. Sparse-vs-dense NaN disagreement
  (xor_nan) ≤0.5% per column; top diffs are xp_adv change cols (~230-260)
  and nw change cols (~125-150 gold).

Archive/live tape (120,461 decisions on 290 maps), buy window:
- mean |Δpred| = 0.00073, p99 = 0.005; threshold flips 0.00%, direction 0%.
- xor_nan ≤0.14% — real feed nearly always finds pivots.

→ At matched level values the received-tape history is a ~0.1-0.2¢
perturbation. Not the edge driver.

### 5. The real live↔train gap is the values, not the tape (verified)

`work/hist-dev/lol_arch_gaps_levels.py`, `arch_domain_shift.py`.

- Tick-vs-dataset at the same labeled second, maps with |δ|≤5 s:
  `radiant_nw` mean |Δ| ≈ 471 g (median 114 on s∈[80,900]); only 0.3% exact;
  `radiant_nw_adv` 352 g; `top*_ratio` ±0.003-0.006; `radiant_xp_adv` differs
  on 24% of ticks in level-table steps (±280…). Sources differ: live =
  Σ player.net_worth off the GRID scoreboard (`grid_feed.py:189-213`,
  `xp_advantage(level_xp,…)`); dataset = livestats-frame recompute.
- Timing decomposition: allowing ±15 s, the best-matching dataset second is
  tick_second −3 to −4 (mode −3); best-shift residual median |Δnw| ≈ 8 g
  (mean 33). So ≈80% of the raw diff is a ~3-4 s per-tick label lead of the
  live clock (`second = live_clock_seconds − feed_delay`, `grid_feed.py:200`),
  remainder source difference.
- Model impact of the full domain gap (tick fields + live history vs dataset
  fields + dense history, same anchor): mean |Δpred| 0.0097 all / 0.0080
  buy-window, p99 ≈ 0.049; in-window thr flips 0.01%; **systematic bias
  mean(delta_live − delta_ds) = −0.004** in the buy window — live reads the
  same market ~0.4¢ weaker on average.
- Clock origin δ = spawn_wall − grid_origin: median −0.19 s, but 25% of maps
  δ ≤ −36 s and outliers to −1367 s — a quarter of archives have the live
  `second` label systematically behind dataset seconds.

### 6. Importance vs availability (verified)

LightGBM split-gain share over the 10 members (`work/hist-dev/gains.csv`):

- LoL history columns total **11.4%** of gain (total_5m 5.9%, change_1m 1.6%);
  Dota `data/new_model/research`: **47.3%** (total_5m 13.4%, change_1m 8.7%).
- LoL top features: radiant_nw_adv 26.7%, market_radiant_prior 23.9%,
  logit_market 8.5%, market_vs_prior 7.6% — instantaneous state + market.
- Top history cols are exactly the ones unavailable in most of the buy
  window: `game_total_5m_*` is NaN until second 360 → NaN on 65% (synthetic)
  / 70% (archive) of buy-window decisions.

| column | gain% | bt NaN | lv NaN | bt mean\|Δ\| | lv mean\|Δ\| |
|---|---|---|---|---|---|
| game_total_5m_dire_nw | 1.39 | .651 | .695 | 106 | 158 |
| game_total_5m_radiant_nw | 1.06 | .651 | .695 | 106 | 160 |
| game_total_5m_radiant_nw_adv | 0.66 | .651 | .695 | 51 | 78 |
| game_change_2m_radiant_nw_adv | 0.39 | .197 | .312 | 77 | 104 |
| game_change_1m_radiant_nw_adv | 0.25 | .081 | .129 | 46 | 82 |

(NaN = warmup, identical by construction; diffs are gold-scale but the model
barely weights these columns.)

### 7. LoL-specific behavior (verified)

- `LOL_GRID_START_SECOND = 0`, `LOL_TRAIN_END_SECOND = 540`
  (`src/lol/constants.py:123-126`); training rows 0-540, seconds 0-59 train
  with all-NaN history — same NaN rows live produces; consistent.
- Backtest `lag_seconds=0` for LoL: feed second = dataset second; the 11 s
  source lag moves only the decision clock (`LOL_SOURCE_LAG_SECONDS`,
  `run.py` selection), so history lag targets are exact game seconds.
- All three paths use *game-second* domains: dataset second = spawn wall −
  pauses (`livestats_frames.py:538-560`), live `second` = GRID clock −
  feed_delay (freezes when `clock_ticking` false), backtest reuses dataset
  seconds. Pauses therefore do not desynchronize the tape — weak correlation
  of invalid counts with `pause_seconds` (0.12) confirms.
- `GRID_V1_FIRST_TICK_SECOND["lol"]=76` only gates the *synthetic* feed
  start; real schedules start ~4-32.

### 8. histfix changed more than "the policy" (verified, git ea5367b9^)

- Old live/backtest: backward-only pivot within **16 s**
  (`SnapshotHistory(feed_timeout_seconds)`, `GRID_FEED_STALE_SECONDS=16`),
  no start clip, no drops — tape recorded seconds <60 and warmup targets
  resolved against them.
- Old LoL *training* also used the 16 s backward budget
  (`06_train_model.py` pre-fix passed `GRID_FEED_STALE_SECONDS`) — on the
  dense tape that's exact pivots, but targets <60 resolved to the 0-start
  tape: the old model had *real* change_1m from second ~60 and total_5m from
  ~300. The new contract NaNs all targets <60, shifting every warmup column
  boundary +60 s for both train and live together.
- On the real archive tape, the old rule missed ≥1 pivot on **26.4%** of
  decision ticks (per-lag miss 5.6-7.1%) — NaN columns in regions where old
  training always had values. New rule: 0.09%. So histfix genuinely fixed a
  live input-quality problem; it just couldn't pay off in LoL because the
  affected features are only ~11% of the model.

### 9. Tails: not the drops (verified)

Per-map `engine_pnl` (`data/backtests/lol_maker/…_p45_{histfix,cur}-20261003r2
/seed{0,1,2}/results.parquet`) joined to my invalid counts:
corr(Δpnl, invalid) = −0.012 / +0.001 by seed; all 15 worst histfix maps have
0 dropped ticks; biggest histfix-vs-cur per-map loss (−1524) is on an
invalid=0 map. The A/B doc shows the arm diff is the retrained catalog
(`docs/experiments/hist-policy-20261003.md`), i.e. retrain noise.

### 10. Dota comparison — 2026-09 (verified)

`work/hist-dev/dota_grid.py`, 297 maps × seeds 0-2, lag 10 s, bands 11/8/7/6:

- 302,323 ticks → 15,672 invalid (**5.18%**, vs LoL 2.5%), 868 in-window;
  164/297 maps (55%) with ≥1 drop; max run 83; stale 3,343 (1.1%);
  first feed second 48.
- Dota's dataset rows are perfectly contiguous (max row gap 1 s on all 297
  maps) — every drop is pure cadence thinning, randomly distributed across
  quiet stretches rather than pinned to bad-data maps.
- Combined with Dota's 47.3% history gain share, the same policy change has
  ~4× the feature exposure and a fundamentally different drop population.

## What I ruled out

- **drop_gap_ticks as the tail driver**: zero correlation with per-map PnL
  deltas; worst maps all have zero drops. (verified)
- **Sparse-tape history noise moving predictions**: ≤0.0016 mean |Δpred|,
  ~0 buy-window flips on both synthetic and real tapes. (verified)
- **Live history_gap drops mattering**: 0.245% of archive ticks; live
  journals carry none (also they predate the Oct-3 deploy, so the check is
  the replay estimate above). (verified)
- **Pause desync**: both clocks freeze; invalid↔pause_seconds corr 0.12.
  (verified)
- **Warmup/NaN asymmetry between dense and sparse at matched levels**:
  xor_nan ≤0.5% (synthetic) / ≤0.14% (archive). (verified)
- **Model input plumbing differences in schedule replay**: model rows take
  snapshot cols from the tick and `market_p_radiant` = anchor mid
  (`signals.py:734-743`) — faithful to live. (verified)

## Proposed experiments

Ranked by expected value for LoL:

1. **Close the state-source gap (the real live↔train mismatch).** Rebuild a
   history/snapshot from the same code path live uses, or align the dataset:
   bucket `radiant_xp_adv` through `level_xp` (as `xp_advantage` does live),
   use scoreboard `netWorth` semantics, and/or shift labels by the measured
   ~3-4 s. *Experiment*: retrain on transformed fields; score archive
   decisions with live-vs-aligned arms — success = mean |Δpred| →~0.001 and
   the −0.4¢ in-window bias disappears. Then backtest; the residual tells
   whether the fix matters at PnL scale.
2. **Stop dropping on market-data holes.** Build the cadence tape from
   `game_features.parquet` (per-second coverage) rather than validation rows,
   or replay archived schedules where they exist. *Experiment*: rerun the
   histfix arm with the tape sourced from game_features — predicted: invalid
   rate falls toward the archive ~0.25%, PnL delta vs current arm isolates
   the over-drop cost.
3. **Give the buy window usable history.** `game_total_5m_*` is the only
   history block with real gain (5.9%) but is NaN until second 360. Add
   `game_total_3m_*` (available from second 240) or drop the 5m totals.
   *Experiment*: swap columns → retrain → same A/B harness; read gain share +
   validation MAE + seed-wise PnL.
4. **Trim the dead rows.** Seconds 0-59 (all-NaN history, never tradable on
   grid-v1 but reachable live) — either exclude from training or let live
   gate start at the first real history coverage (~120). *Experiment*:
   retrain without second<60 rows; compare validation MAE and in-window
   markouts.

## Scripts

All under `investigations/2026-10-03-lol-edge/work/hist-dev/`, run from
`esports-trader` with
`PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python <script>`:

- `lol_grid.py` — grid-v1 tick walk: invalid/stale/decision counts per
  month + parity decisions parquet (`tickstats.parquet`, `decisions.parquet`)
- `lol_archives.py` — real schedule replay: archive invalid/cadence/δ stats
  + sparse/dense blocks (`arch_mapstats.parquet`, `arch_gaps.parquet`,
  `arch_decisions.parquet`)
- `lol_arch_gaps_levels.py` — archive game-second holes + tick-vs-dataset
  level parity (`arch_gsec.parquet`, `level_parity.csv`)
- `score_parity.py` — per-column parity + 10-member scoring, dense vs sparse
  (`parity_gridv1.csv`, `parity_arch.csv`, `gains.csv`, `scores_*.parquet`)
- `arch_score.py` — corrected archive-arm scoring rerun
- `arch_domain_shift.py` — live-domain vs dataset-domain model input shift
  (`scores_domain.parquet`)
- `arch_old_vs_new.py` — pre-histfix (backward-16s) vs current pivot reach on
  the real tape
- `dota_grid.py` — Dota 2026-09 grid-v1 walk (`dota_tickstats.parquet`)
