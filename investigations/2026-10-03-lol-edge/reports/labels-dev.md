# labels-dev: LoL label/time alignment audit — spawn anchor, pause clock, label horizon, feed lag
Status: FINAL
## Verdict (≤10 lines)

The LoL label/time alignment is mechanically correct: `signal_market_p_radiant_300s` is exactly
the book mid at `state_ts_us + 311 s` (verified bit-exact on 22,747 labeled rows / 20 maps), and
the spawn anchor is NOT late (mid == prior at second 0 on every probed map; first >2¢ move lands
+65 s after second 0, first kill +224 s). The problems are elsewhere: (1) the market mid reacts to
game state ~3–4 s after the second boundary while our join reads it at +11 s — a post-reaction
anchor, symmetric with Dota (+3 s), but the fast-market share grew 61%→86% Jun→Sep and the
slow-market cohort that pays 2×/map collapsed in Sep (+2.6/map); (2) 22.9% of rows carry NaN labels
(dead/stale book at the +311 s target — mostly map tail, +3.6 pp on pause maps); (3) pause maps
earn +11.0 vs +26.7/map and are 34→44% of the whitelist, and the grid-v1 backtest keeps quotes
live ~16 s into pauses where live cancels instantly (2% of pause-map fills happen inside pause
wall-gaps — fills live could never take).

## Findings

1. **Label is wall-clock +311 s, not game-second +300 — verified bit-exact.** Recomputed
   `lookup_market_p_after(rb, db, state_ts_us, 11)` and `(311)` with `load_token_book` on 20 maps:
   `market_p_radiant` matches on 100% of 30,657 rows; the 300 s label matches on 100% of the 22,747
   non-NaN rows; all 7,910 stored-NaN labels recompute NaN. Against alternatives:
   `market_seconds[second+300]` matches only 30.2% of labels; mid at `wall(second+300)+11 s`
   matches 80.8%. So a pause between the anchor and the target compresses the label's game-time
   horizon — but that is the correct convention for a holder (the position is worth the mid 300
   *wall* seconds later). Evidence: `src/lol/05_prepare_dataset.py:271-286`,
   `src/shared/utils/telonex_book.py:248-258`; measured `work/labels-dev/label_exact.parquet`.
   `verified`

2. **Spawn anchor is accurate; the market is quiet at second 0.** On 205 sampled maps (152
   stratified + all 62 spawn-gap>90 s): `mid_at_spawn == market_radiant_prior` exactly on all 163
   maps with both values (strict prior already uses the last two-sided mid in [spawn−61 s, spawn)).
   First >2¢ departure from prior on the game-second tape: median +65 s after second 0 (47% ≤60 s,
   8.6% >300 s, 2/1239 never move >2¢). First kill (dataset deaths>0): median game second 224
   (wall +251 s). Token books are live ≥2 h before spawn (8-map probe hit the −7200 s window edge
   every time); the mid wanders a median max of 4¢ across the 15-min loading window and reverts to
   prior by spawn. `verified` — `work/labels-dev/clock_audit.parquet`, `firstmove.parquet`.

3. **Market reacts ~3–4 s after the second boundary; our join reads at +11 s.** Per-map
   cross-correlation of Δ`radiant_nw_adv` (game_features dense tape) vs Δmid (market_seconds ok
   rows) at offsets −30..+60 on 1,226 whitelisted maps: median argmax +3 s; mean curve peaks at
   +3 (r=0.127), +4 (0.077), +5 (0.043), back to baseline 0.012 by +11. 81 maps (6.6%) have argmax
   <0 — mid partially anticipates/leads our second axis. Dota (150 LIVE maps): median argmax +3 s —
   the handicap is symmetric; the difference is trend, not level: LoL share of maps with argmax ≤4 s
   went 61.2% → 79.6% → 74.1% → **86.2%** Jun→Sep, and mean corr at +11 decayed 0.020 → 0.011 →
   0.015 → **0.0055**. Dota shows no such trend (52–68%). `verified` — `lag_xcorr_{lol,dota}.parquet`.
   Caveat: offsets are measured on the dataset's own second axis (spawn-clocked wall for LoL,
   horn-relative for Dota); they bound the market-vs-join gap but do not separate livestats-stamp
   emission delay from market speed.

4. **The slow-market cohort pays 2× and collapsed in September.** Splitting maps at argmax ≤4 s:
   945 fast maps earn +17.8/map (+16,843); 281 slower maps earn +30.9/map (+8,681). By month the
   slow cohort's PnL went +31.6 (Jun, n=59) → +49.2 (Jul, n=59) → +32.4 (Aug, n=117) → **+2.6
   (Sep, n=46)** — both fewer slow maps and near-zero edge on them. This is a direct measurement of
   the "market got faster than our 11 s join" decay channel. Note argmax>11 s (146 maps) means the
   market incorporates state *slower* than our GRID-table lag — the only regime where we hold a
   timing lead. `verified` — `lag_xcorr_lol.parquet` + w540lv6 mean per-map net.

5. **Pause handling: dataset clock compresses pauses, market_seconds reinserts them, live cancels
   instantly, grid-v1 backtest cancels ~16 s late.** `assign_game_times` removes >5 s frozen-stat
   wall gaps from game time (`livestats_frames.py:537-563`); `wall_us_for_second` puts them back
   between second boundaries in `market_seconds` (`replay.py:161-198`). Labels/currents are
   wall-clock lookups, so they naturally price pause-flat periods. Live: `paused=not
   clock_ticking` → `SignalReason.PAUSED` and `_blocked(reason="paused")` cancels all orders
   (`grid_feed.py:212`, `session_quoting.py:109`, `strategy/quoting.py:920-921`, `_cancel_orders`).
   Grid-v1 backtest: `observed_clock=None` → `clock.paused` is always False
   (`backtest/run.py:506-520`, `backtest/strategy.py:1040-1051`); the pause only manifests as a
   wall gap with no ticks, so orders stay live until the 16 s stale-signal gate
   (`GRID_FEED_STALE_SECONDS`). Measured on the 161 maps with pause_seconds>120: **37/1,816 fills
   (2.0%) land inside pause wall-gaps** — fills live could never take (one map: 10/31 fills inside a
   pause). `verified` (code) + `verified` (fill timestamps).

6. **Pause maps earn less than half per map, every month.** 481/1,239 whitelisted maps have a
   detected pause (share: 34.6% Jun, 43.8% Jul, 33.5% Aug, 43.7% Sep; pause_seconds>60 share
   19%→27.5%). w540lv6 3-seed mean net: pause +5,298 (+11.0/map) vs non-pause +20,226 (+26.7/map).
   Per month (pause vs not): Jun −4.1 vs +41.3; Jul +25.3 vs +38.5; Aug +9.3 vs +21.5;
   Sep +5.9 vs +17.2. Partly mechanical — pause maps have ok-quote share 79.6% vs 86.2%, 6.9 vs
   10.6 buy fills, 31% vs 26.5% NaN-label share — i.e., they are also the quieter, more retail
   maps. `verified` — `map_stats.parquet`, `firstmove.parquet`.

7. **Label NaN share is 22.9% of all validation rows — mostly mechanical, but mid-map staleness
   is a real band.** Tail (last ~320 game s): 65% of NaNs (book stops at game end; +311 s target
   escapes the tape). Mid-map: 157,098 NaN rows (8.1–14.2% of non-tail rows by month), of which
   90.7k (58%) have the last ok boundary >5 s before the target = stale-book drops (quiet markets +
   pause spans); only 4,526 NaNs have targets inside pause gaps. Buy-window (<540 s) NaN share
   6.3%. These rows are still traded in backtest/live — the label only gates training. `verified`
   — `label_clock_rows.parquet` (per-row), `label_clock_map.parquet`.

8. **Row integrity is clean.** 0 duplicate (match_id, second); 0 non-monotone `state_ts_us`; no
   NaN/0/1 `market_p_radiant` (those rows are dropped at join → `skipped_market_rows`); dataset
   frame wall lags the market_seconds boundary by 0–1.96 s, median 95 ms (the ≤2 s age gate).
   Median second coverage 94.6% of [0, sec_max]; 294,793 in-range seconds lack a dataset row
   (age-gate + market-join drops). 1.6% of rows have `market_p == label` exactly — dead-flat
   300 s stretches at mid prices (0.2–0.9), spread through mid-game, not tail-pinned — real quiet
   market, ~flat per month. `verified` — `map_stats.parquet`.

9. **14% of maps have no second-0 row; 85 maps (6.9%) start after second 76 and earn ~0.**
   `sec_min>0` on 176 maps (median 0, max 2157). Cause: the current-mid lookup fails in early
   seconds — 13.6% of market seconds 0–120 are non-ok (9.2% `wide_spread`, 4.3% `stale_quote`).
   Maps starting after second 76 (the grid-v1 first-tick threshold) earn +1.6/map vs +22.0 — they
   simply can't trade (no signal, wide book). Correct behavior, but it means ~7% of the whitelist
   contributes nothing; they show up as `no_trades` in results. `verified`.

10. **Spawn-gap>90 s maps (62) are illiquid minor-league cards, not anchor errors.** Gap =
    spawn wall − `loading_anchor_ts` (first archive frame). Median gap overall 6.2 s, max 242 s.
    Gap maps: PnL +11.2 vs +21.1; early-book ok share 66.5% vs 88.3% in seconds 0–300; pre-spawn
    mid drift 10¢ vs 5.5¢; first-kill timing normal (211 vs 225 s). So the gap is long
    loading + thin early books, not a late anchor. `verified`.

11. **History lags are on the pause-compressed game-second axis everywhere — consistent.**
    Training tape (game_features dense seconds), backtest received tape, and live SnapshotHistory
    all index by game second; a 5 m lag is `second − 300` on each (`dota_features.py:241-288,
    291-369`, `backtest/signals.py:357-421`). During a pause no new seconds exist on any of them, so
    lags cover the same stretched wall interval. The 30 s `max_pivot_gap_seconds` trips only on
    game-second gaps (feed stalls where the game kept running), which exist in the data: 295k
    in-range missing seconds. `verified` (code) + `likely` (data contribution).

12. **Dota clock comparison (30 LIVE maps).** First >2¢ move median second −60 (25/30 maps move
    pre-horn — Dota drafts move the market early); first kill median +92 s (LoL +224 s); Dota xcorr
    argmax +3 s like LoL. LoL's pregame market also trades but reverts to prior at spawn; Dota's
    drifts *through* second 0. No analog of LoL's long-loading gap>90 cohort in Dota (OpenDota horn
    is the anchor, not a livestats spawn frame). `verified`.

## What I ruled out

- **Late spawn anchor**: mid == prior at second 0 on all 163 probed maps; first mid move is +65 s
  *after* second 0, not before. The market is not ahead of our clock at the anchor.
- **Wrong label convention** (game-second+300 vs wall+311): the pipeline implements wall+311
  bit-exactly; the alternative matches only 30% of labels. Pause compression of the label horizon
  exists (~1% of buy-window rows have in-pause targets) but is the economically correct target and
  only mildly flat (|label−cur| 0.099 vs 0.109).
- **Row-level corruption**: no dupes, no non-monotone clocks, no 0/1/NaN mids, frame age ≤1.96 s.
- **"Market moves before second 0" as a bug**: pregame mid drifts ±4¢ (±10¢ on gap>90 maps) but
  reverts to the strict prior at spawn — that's ordinary pregame repricing, not a clock error.
- **Pause mislabeling**: pause labels are wall-correct; the cost is NaN inflation (+3.6 pp) and a
  small fill-timing divergence, not mislabeled data.
- **`skipped_*` explosion**: audit `skipped_age_rows` is dominated by the fixed 0..7200 grid past
  map end (every second after last game second is age-skipped) — cosmetic, not a coverage bug.
  In-map coverage median 94.6%.

## Proposed experiments

1. **Pause-aware grid-v1 clock (backtest/live parity).** In `backtest/run.py:506-520` else-branch
   build an `ObservedClockTape` for the grid-v1 path: mark feed ticks `paused=True` when the wall
   gap to the next market_second boundary is >5 s (data already in `market_seconds.state_ts_us`).
   Then `requote`'s paused gate fires like live. Expected: removes the ~2% of pause-map fills inside
   pause gaps and cancels resting orders ~16 s earlier per pause; w540lv6 pause-map net should move
   toward live. Watch: net PnL on the 481 pause maps, late third. Cost: one LoL validation backtest
   rerun (3 seeds). Code change sketch only — no data rebuild.
   `uv run python -m backtest ...` (same w540lv6 config; orchestrator knows the command).

2. **Ungated label backfill for mid-map staleness.** Rebuild stage-05 labels with
   `find_asof_quote` without the 5 s age gate (or gate=30 s) so the 157k mid-map NaN rows get the
   realized (stale) mid — which IS what a holder would mark to. Restrict the backfill to
   `second < sec_max − 320` to keep the dead-tail convention. Expected labeled share mid-map
   +8–14 pp; watch DIR/MAE on validation <480 and late-third PnL after retrain. Cost:
   `lol-prepare` rebuild (map cache makes it fast if stamped on code version — verify) + one 06
   train + backtest.

3. **Market-latency segmentation as a signal.** Per-map xcorr argmax (or a simpler live proxy:
   realized corr of Δstate vs Δmid at +3..+11 s on the first 300 s) predicts PnL: slow cohort
   +30.9 vs +17.8/map. Experiment: replay w540lv6 with clip scaling ∝ per-map measured market
   latency, or whitelist argmax>4 maps only. Watch: late-third net on the slow cohort (Sep: 46 maps
   at +2.6 — if even that cohort is dead now, this is a Jun–Aug artifact, not a fix).

4. **Early-book quality flag in audit.** Persist per-map `ok_quote_fraction` for seconds 0–120
   (or first-ok-second) in `audit.parquet`; the 85 maps starting after second 76 (+1.6/map) and the
   62 gap>90 maps (+11.2, 66% early-ok) are diagnosable today only by reconstruction. Expected: a
   cheap coverage reason code; informs a live "dead market, don't bother" gate. Cost: trivial add
   to `build_audit_rows`; no model change needed to evaluate (correlate flag with PnL offline).

## Scripts

All under `work/labels-dev/`, run from the esports-trader root with
`PYTHONPATH=src:scripts uv run python <script>`:

- `01_maps.py` — builds `maps_master.parquet` (audit+links+spawn gap+month) and
  `pnl_by_seed.parquet` (w540lv6 per-map net = engine + rebate − taker).
- `02_dataset_stats.py` — `map_stats.parquet`: dup/monotone/missing/NaN/stale-label stats,
  market_seconds status mix, first-kill second, per whitelisted map.
- `03_clock_audit.py` — `clock_audit.parquet`: book loads over [spawn−15m, spawn+15m], first
  pair-mid move vs prior, pre-spawn drift, on 152 stratified + 62 gap>90 maps.
- `04_label_clock.py` — `label_clock_rows.parquet`/`label_clock_map.parquet`: every row's stored
  label vs wall+311 boundary-asof vs market_seconds[s+300], pause-target detection via wall gaps.
- `05_lag_xcorr.py` — `lag_xcorr_{lol,dota}.parquet` + `*_curves.npy`: per-map argmax offset of
  Δnw_adv vs Δmid, LoL 1,226 maps + Dota 150.
- `06_firstmove_pnl.py` — `firstmove.parquet`, `dota_firstmove.parquet`: first >2¢ move, first
  kill, PnL slices (pause/gap/move cohorts).
- `07_label_exact.py` — `label_exact.parquet`: bit-exact recompute of stored current/label via
  `lookup_market_p_after` on 20 maps.
