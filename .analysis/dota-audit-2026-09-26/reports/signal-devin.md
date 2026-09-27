# signal-devin — backtest signal inputs: what the model sees, cadence, market cache, selection
Status: FINAL

Scope: backtest inputs and signal construction for Dota — what the model sees at
each tick, map selection, the market-second cache, archive-schedule timing and
the feed-second → STRATZ-second mapping, cadence/staleness, cache identity and
stale-artifact risk, and input-loading performance.

Evidence base: `src/backtest/{signals,feed_schedules,selection,run,postprocess,marks,live_archives}.py`,
`src/prepare_dataset/{prepare_dataset,stratz_seconds}.py`,
`src/market_data/build_market_data.py`, `src/shared/utils/{telonex_book,match_time,engine_cadence}.py`,
`src/trader/{grid_feed,live_feed,oddin_feed}.py`, `src/collect/{s05a_fetch_prices_history,s06_publish_catalog}.py`,
`src/archive_index/*`; empirical checks in `work/signal-devin/` against
`data/archive_index/index.parquet`, `data/new_processed/match_links`,
`data/new_processed/market_seconds/`, `data/backtests/dota_maker/LIVE/seed0/`,
and per-map schedule files. Orchestrator note N4 is incorporated (verified
independently by my own intercept fit).

Run inspected: `data/backtests/dota_maker/LIVE` manifest — 613 selected matches,
`signal_source=auto`, `train_lag_seconds=10`, `backtest_lag_seconds=10`,
`buy_cutoff_second=480`, `max_signal_age_seconds=16.0`,
`feed_schedule_rules_version=feed-schedule-v4`, `admission_rules_version=admission-v2`.
All manifest artifact hashes (validation dataset, game features, schedule map,
selected matches, both model dirs) re-verified against current files — the run
consumed today's artifacts. That does not make the artifacts correct.

Findings sorted by severity. "Verified" = demonstrated in code + data;
"likely" = strong code evidence, thin empirical coverage; "open" = hypothesis.

---

## S1 — Archive horn is early by the pre-horn pause D; every artifact keyed by catalog horn is shifted on 57 validation maps (~146k rows) — verified

**Mechanism (verified end-to-end).** `live_feed.py:102-111` `horn_is_pinnable`
accepts the *first* GRID PRE_HORN tick and pins `horn = occurred_at −
clock_seconds ≈ spawn + 90` — before any pre-horn pause has accumulated, and
`_maybe_pin_horn` never re-pins. Oddin got this exact fix in `deebb730`; GRID
and Steam did not. `s06_publish_catalog.py` prefers `archive_horn_at_utc` over
the GRID-derived horn whenever an archive is linked, so the wrong horn enters
the catalog and propagates into `build_market_data.py`
(`resolve_catalog_row` → `job.horn` → `get_state_available_ts` →
`state_ts_us`).

**Effect inside my scope.** Every market-cache row on a shifted map is sampled
`D` seconds earlier than its labeled game second:

- `dataset market_p_radiant` is stale by `D` beyond the designed 10s lag.
  Effective train/validation market lag becomes `10 − D` game-seconds; for
  `D > 10` the market feature is sampled at game time *before* the state it
  labels (still no future leak — purely stale, but a different regime than
  designed).
- `signal_market_p_radiant_300s` labels keep a true 300s *wall-clock* horizon
  but the labeled `second` is off by `D`, so the model learns a `300 + D`
  game-second horizon on shifted maps — up to ~849s for `D=549`.
- `prepare_dataset` folds `validation_minute_rows` into the **production**
  training set (`prepare_dataset.py:274`), so these corrupt rows reach the
  model that trades live, not just research.

**Scope.** 58 catalog maps (56 GRID + 2 Oddin), D median 70s / max 549s
(N4 event study: shifted maps peak at lag +62.5s vs control at 0 — the market
reacts to kills ~D later than the cache says, proving the shift is real and
directional). 57 maps in validation (~146k rows), **50 maps in the LIVE
backtest, all schedule mode**. Every future map with a pre-horn pause gets the
same error (~1/3 of incoming maps). My own fit (`horn_shift.csv`): feed-clock
intercept ≈ true horn + ~7s feed delay on GRID maps, i.e., the feed's
`game_second` labels are correct in-game seconds — it is the archived horn
that is wrong, not the clock.

**Backtest money impact is bounded but real.** Schedule-mode anchors are
looked up on the mid series at real wall receipt time (`signals.py:562`,
`lookup_reference_mid(series, tick.received_ns)`), so fill-time prices and
markouts stay wall-time consistent — the shift relabels *which second* a quote
belongs to, not *when* it happened. Residual effects: mid-series coverage ends
`D` seconds before true map end (markouts near end fall back to terminal bid /
settlement earlier), and the dataset `market_p` vs label misalignment degrades
model calibration for all downstream uses. The dominant cost is corrupt
training/validation rows, not a direct PnL leak — hence S1 on data
correctness, not on backtest arithmetic.

**Also touched by the same horn:** `s05a_fetch_prices_history.load_anchors`
falls back to `archive_horn_at_utc` when no GRID window exists → the 6h
trailing quote window and `last_pre_anchor_quote` prior are sampled `D` early
on archive-linked maps without GRID windows.

## S2 — Four Oddin schedule maps in LIVE carry archive horns ~14–17 min early — verified offset, open mechanism

`horn_shift.csv`: feed-clock intercept − archive horn = **+1009.7 / +852.9 /
+836.4 / +814.4 s** on match_ids **9007618656, 9007700576, 9007208887,
9007618767** (pre-horn pauses 32/0/0/0 — not the N4 mechanism). All four are in
the LIVE 613, all `signal_mode=schedule`, all `feed_source=oddin`, all scored
by `research-noxp`.

Impact: market cache rows for these maps sample the book ~14 minutes before
their labeled game seconds — dataset `market_p_radiant` and the +300s labels
for these maps are effectively unrelated to game state (pregame features point
at prices from ~14 min before horn). Cache coverage also ends ~814s early in
true wall time, so late-window markouts fall to terminal/settlement and
anchors at map end silently return None → ticks skipped. Backtest signal
anchors remain wall-time consistent (same as S1), so the main damage is again
dataset corruption — but at ~15 min scale rather than ~1 min.

Likely a wrong archive↔map association or an Oddin horn pinned on a stale
`lastUpdatedAt − game_time` — hand off to feed-devin/archive-admission owner;
the archive-index admission audit did not catch a 15-minute horn error, so
horn-vs-feed-clock sanity is missing from admission.

## S2 — grid-v1 synthetic cadence cannot express real feed gaps; it covers 76% of the cohort — verified

Measured on all 149 schedule-mode maps (join fixed via `match_links →
archive_root+archive_id → index`; scratch: `check_cadence_selection.py`):

- in-window inter-tick intervals: n=24,394, **p50=0.62s, p90=10.5s,
  p99=31.6s, max=4,622s** (mean 4.36s).
- **67/149 maps (45%) have ≥1 in-window gap >45s; 92 gaps total.**
- ticks run to last game_second p50=2471 / max 5221 (well past the 899 label
  window — fine, just filtered); ~59 pre-window ticks/map (filtered).

grid-v1 (`select_cadence_rows`, `signals.py:258-308`): per-second
Bernoulli keep with mean interval 6–11s by band + unconditional keep of
kill-carrying rows. The gap distribution is geometric with mean ≤11s — it
**cannot produce** the observed multi-minute outages, and
`grid_v1_scoreboard_delay_seconds` draws each kill lag independently
(its own ponytail comment admits serial stalls are underrepresented).

Consequence on the 464/613 (76%) grid-v1 maps: `recovery_stale_mask` and the
16s `find_signal_asof` latch almost never engage, so the strategy keeps
quoting through periods where the real feed went dark. Backtest fills exist on
these maps that live could never attempt → inflated trade count/opportunity
set; direction is optimistic bias, magnitude bounded by however much of the
PnL comes from post-gap windows. In the LIVE seed0 results, gate tallies show
`stale_signal` dominates real maps (e.g. 3012–4299 gated seconds on the Oddin
maps above) — staleness gating is a first-order effect on real feeds and is
nearly absent on the 464-map majority.

## S2 — Market cache identity: `CACHE_VERSION` omits horn source, pauses, and token mapping; no GC; manifest does not pin cache contents — verified

`build_market_data.py:37-51`: `CACHE_VERSION = sha256(repr((
MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE, MAX_ENTRY_SPREAD_TICKS, 30,
MODEL_TARGET_HORIZON_SECONDS, MODEL_START_SECOND)))[:8]`. Rebuild skips any
match whose `v<ver>/match_id=<id>.parquet` exists
(`run_market_data_build:167`).

- A catalog horn change (e.g., fixing S1, relinking an archive, pause
  corrections) does **not** bump the version → stale rows are silently reused
  into datasets, labels, markouts, and anchors. This will bite the moment the
  horn bug is fixed unless the whole `v*/` tree is hand-deleted.
- `radiant_token_index` / `token_ids` / `condition_id` are likewise outside
  the key — a corrected Gamma mapping silently reuses old mids.
- Disk currently holds three dead/alive version dirs: `v59b14c69` (2,277
  files), `va75c29ad` (3,081 — *more* files than the current catalog covers),
  current `v9c88adc2` (3,041); ~456MB total, never collected.
- The LIVE manifest hashes dataset, game features, schedule map, selected ids
  and model dirs — but not the per-match market-second files actually
  consumed. Two runs with byte-identical manifests can read different cache
  contents.

## S3 — One "auto" cohort silently mixes two timing regimes — verified

`resolve_run_signals` partitions into 149 `SchedulePlan` (real archive feed
ticks; market anchor = live book as-of at true wall receipt; features = feed
tick snapshot + dataset prior) and 464 `GridV1Plan` (dataset seconds stamped
at market-cache `state_ts_us`; market feature = dataset `market_p_radiant`,
i.e., cache value at `second+10`; no as-of book lookup — `series` is only
consulted for LoL, `signals.py:437`).

So within one PnL pool: signal timestamps are wall-clock receipts for 24% of
maps and *cache-constructed market-clock times* for 76%; the market feature is
a live as-of quote for one subset and a precomputed column for the other;
signal availability gates engage on one subset and are nearly unreachable on
the other (S2). `schedule_map_sha256` does pin the per-match mode+fingerprint,
but the manifest's human-visible `signal_source: "auto"` hides the mix — a
reader comparing runs cannot see the regime balance move as the archive index
grows, and aggregate ROI silently blends two different simulations of
"freshness". Per-mode PnL is already split in results (`signal_mode` column);
the gap is at the cohort-reporting level, not the data.

## S3 — Cohort selection conditions on post-match information — verified, partially by design

Selection waterfall on current artifacts (`select_validation_matches`):
717 validation → **−92 `backtest_book_gap_excluded`** (longest non-ok run in
seconds −60..899 > 120s) → 0 without catalog/usable-rows/telonex → 625
eligible → −12 archive exclusions (`no_window_updates` 4,
`schedule_identity_mismatch` 4, `no_terminal` 3, `archive_unlinked` 1) →
613 selected.

- The book-gap window (−60..899) correctly covers model window + 300s label
  horizon — no leakage *into the label* — but the rule uses full-window book
  health, unknowable at map start, and drops 12.8% of maps, preferentially
  thin/quiet books. The measured cohort is systematically more liquid than
  the live population → backtest PnL runs warm vs. what live will see.
- Archive admission adds the same direction of conditioning (needs a terminal
  record, non-corrupt feed, resolvable identity). These are reasonable data-
  quality gates for research; the finding is that nobody should read LIVE PnL
  as an unbiased estimate of live-trading PnL — ~14% of the population is
  excluded on information only available post-match.

## S3 — `join_validation_rows` hard-crashes the whole prepare on one bad market row — verified

`prepare_dataset.py:205-211`: a market row whose `market_second − lag` has no
STRATZ state raises `ValueError`, uncaught in `main`'s per-match loop (contrast
`build_minute_states`, which catches `MatchDataError` and skips the match).
Market cache rows extend to catalog `duration`; exact-second states extend to
STRATZ `durationSeconds` — the invariant `catalog.duration ≤ stratz.duration`
is nowhere checked, so a one-second-long catalog duration (or truncated STRATZ
playback) aborts an entire dataset build deep into the loop. Debuggability:
the error message is good, but there is no isolation — one map kills the run.

## S4 — Smaller verified items

- **Two market-leg ages are gated independently** (`find_asof_quote` ≤5s each)
  → pair skew up to ~10s inside one "ok" row; pair-sum tolerance 0.05 is the
  only cross-leg guard. Benign at current spreads, worth knowing.
- **`usable` ≠ model window**: `select_usable_validation_rows` keeps
  `second ≥ −60 & market_status==ok` for the *entire* map duration
  (validation parquets carry rows to ~5,000s). grid-v1 therefore builds
  signals far outside the 599s model window; only the 480s buy cutoff and
  downstream gates discard them. Costs a few wasted predict calls per map and,
  more importantly, makes "usable" rows look like signal coverage where none
  is decision-relevant. Cosmetic.
- **Feed-tick horn field drifts**: `tick_horn_nuniq>1` on all 136 fitted GRID
  maps (`tickhorn_minus_archive` median −1s, min −12s) — the feed re-derives
  horn per tick. Schedules store per-tick `horn_unix_seconds` while consumers
  use the pinned catalog horn; the ±seconds jitter is immaterial today but is
  an unnamed invariant.
- **Stale cache dirs accumulate** (see S2) — 456MB, no GC, and the
  over-populated `va75c29ad` shows "exists()" reuse would happily serve rows
  for maps no longer in the catalog.
- **Performance notes** (no blocker): `load_match_mid_series` /
  `load_market_second_rows` re-read per-match parquets in postprocess,
  signals, and prepare_dataset independently (same files, ≥3 full passes over
  ~3k files across the pipeline); `load_game_feature_rows` reads the full
  55MB parquet then filters in pandas (fine at 149 ids); `build_exact_second_states`
  is a per-second × 10-player Python loop (~20–50k iterations/map) — slow but
  correctness-friendly; the market-data build is properly pooled via
  `ProcessPoolExecutor` but each cold worker globs + scans per-token
  `book_snapshot_full` dirs, so a cache bust costs a full re-scan of all
  assets.

## Verified correct (non-findings worth recording)

- **No look-ahead in any as-of path**: `find_asof_quote`, `lookup_reference_mid`,
  `last_two_sided_mid`, and `lookup_strict_prior` all bisect strictly ≤ target;
  stale results carry no quote, so no caller can read past the gate.
- **Schedule plumbing is guarded**: non-decreasing receipt check, per-tick
  stale mask (post-gap tick dropped, matching live), feature-row join with
  duplicate-second hard-fail, market-anchor requirement, fingerprint check at
  index *and* file level, `schedule_identity_mismatch` guard, and
  `assert_archive_binding` refusing silent fallback. Exclusion reasons are
  explicit and land in the manifest.
- **The model sees the right thing in each mode**: schedule mode — tick
  snapshot features + dataset prior + `game_second` key + live as-of market
  anchor, with `--lag-seconds/--cadence/--validation-dataset` refused when a
  schedule exists (`reject_schedule_flags`); grid-v1 — dataset row features,
  `second` re-keyed to `second − lag`, market feature pre-lagged by
  construction.
- **Kill gates** draw from per-game scoreboard-lag quantiles and hold
  `KILL_GATE_HOLD_S`, rather than assuming instant board updates.
- **Manifest hashes match current artifacts** for the inspected LIVE run —
  no silent dataset drift *for that run* (does not cover market-second cache).

## Open questions / handoffs

1. The 4 Oddin ~15-min horn offsets (S2) — mechanism unknown; likely wrong-map
   archive binding or stale Oddin pin. Nothing in admission checks horn sanity
   vs the feed's own clock — recommend feed-devin picks this up; a
   `|feed-implied horn − archive horn|` admission gate would catch both this
   and any future variant.
2. Feed `game_second` ↔ STRATZ `second` alignment is assumed, never verified
   per map — ticks with no feature row are silently skipped
   (`_match_schedule_decisions`). A systematic clock skew would present as
   reduced decision coverage, not an error. Suggest a publish/admission
   metric: fraction of in-window ticks lacking a feature row.
3. `dataset.market_seconds/index.parquet` referenced in earlier notes does not
   exist on disk (only `v<ver>/` dirs) — if any consumer expects it, it's a
   stale contract; I found no reader in `src/`.
4. Whether `receivedAtUs` vs exchange timestamp in collector parquets adds a
   systematic receipt-vs-event skew to anchors — `timestamp_us` prefers
   exchange ms; if exchange clock skews vs collector receipt, as-of ordering
   stays correct but anchor age shifts uniformly. Not measured here.
