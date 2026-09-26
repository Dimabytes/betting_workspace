# lag-devin: measured LoL live lag — GRID vs lolesports vs market

Date: 2026-09-23. Agent: lag-devin. Scope: which training lag mimics live; where the LoL live-vs-backtest gap comes from.

## TL;DR — ranked findings

1. **The model's feature feed arrives ~11s after the event; the market reprices in ~3.6s.** GRID `series_table` (source of every model feature) delivers kill/death increments at median **10.98s** after the upstream event stamp (n=8,553 events, 268 maps, p10 10.29 / p90 11.76; declared `delay=8`). LoL map-winner mids move to 50% of their 60-second kill move in median **3.58s** (n=2,163). Live, the model's `market_p_radiant` input is therefore always a *post-reaction* price paired with an ~11s-old state.
2. **Training joins `market_p_radiant` at event+0 — a pairing live never produces.** Since commit `332e1c17`, `05_prepare_dataset.py:310` joins the mid at `state_wall_us + 0`, where `state_wall_us` is the livestats `rfc460Timestamp` (proven identical to GRID `occurredAt`, Δ=0.00 on 8,715 events). The live-equivalent join is `state_wall_us + ~11`. The pre-commit value `+10` was ~1s short; `+0` is ~11s optimistic. **Recommended training lag ≈ 11s on both the current mid and the 300s label.**
3. **83% of the LoL backtest runs on a cadence that cannot exist live.** 782 of 945 maps replay on synthetic `grid_v1` (signals fire at `state_ts_us` = event time; anchor = dataset mid(E+0)). grid_v1 fills front-run the 3.6s repricing: BUY markout-30s mean **+0.70¢** vs **−0.14¢** on the 163 archive-schedule maps. PnL: grid_v1 **+$4,684** ($5.99/map) vs schedule **+$487** ($2.99/map). The live-comparable figure is ~$3/map, not the ~$5.5/map headline.
4. **The lag metadata is stale**: `backtest_audit.source_lag_seconds = 10` and `model.json = 10`, while the join is 0 — `assert_lol_source_lag` (lol_inputs.py:65-80) verifies recorded constants, not the applied join, so it passes.
5. **The `second` feature is skewed ~13s between live and backtest.** Live emits `second = clock_extrapolated − declared_8s` = content_gt **+2.98s** (measured on core_trace ClockUpdates, n=1,485). The backtest feeds the model `second − lag_seconds` = content_gt **−10** (signals.py:271).
6. **Dota is consistent where LoL diverged.** Dota still trains at `TRAIN_LAG_SECONDS=10` and its table lag measures **9.65s** — training ≈ live. Dota's market reacts at t50 **9.6s** (vs LoL 3.6s), so even its synthetic-cadence fills have nothing to front-run (grid-v1 buy markout **−0.30¢**). The gap is LoL-specific: fast market + zero-lag join + event-time cadence.
7. **The archive-schedule path is near live-faithful** (anchor = mid at real receipt ≈ E+11; live label convention preserved), with one residual: feature content ~3s fresher than live's at the same label (live label = content+3 maps to the dataset row at content+3).

## Findings

### F1 — GRID table lag ≈ 11s; scoreboard ≈ 2.8s; both share the upstream event stamp — `verified`

Stage: live feed (collect → live).

Claim: lolesports `rfc460Timestamp`, GRID scoreboard `occurredAt`, and GRID `currentSeconds` all sit on the same event clock; the *delivery* lag differs sharply by service.

Evidence (scratch: `work/lag-devin/measure_lags.py` over 268 linked LoL maps, 8,727 kill events; outputs `events.parquet`, `map_meta.parquet`, `wire_lags.csv`):

| measurement | n | p10 | median | p90 |
|---|---:|---:|---:|---:|
| `sb_occurredAt − ls_rfc460Timestamp` | 8,715 | 0.00 | **0.00** | 0.00 |
| `sb_recv − ls_stamp` (scoreboard wire lag) | 8,715 | 2.06 | **2.77** | 12.52 |
| `tb_recv − ls_stamp` (table/model-feature lag) | 8,553 | 10.29 | **10.98** | 11.76 |
| `tb_recv − sb_occurredAt` | 8,541 | 10.21 | **10.95** | 11.72 |
| `sb_clock − ls_game_time` | 8,715 | −0.54 | **+0.13** | +0.86 |

All-frames scoreboard wire lag: n=130,992, median 2.72s, p90 10.92s. Per-map medians are tight (p10/p90 = 2.52/2.89). Declared `delay` in table frames = 8 on every map (`grid_delay_s`); measured delivery is ~3s beyond declared.

Mechanism: `series_scoreboard_v2` (`delay=0`) pushes kills/clock ~2.8s after the upstream stamp; `series_table` (`delay=8`) delivers the same event ~11s after the stamp — it is the sole source of model features (`read_net_worth` → `radiant_nw`, `xp`, `deaths`, `top1` in `grid_feed.py:159-185`) and the only frame type that emits live snapshots (`_on_table`, grid_feed.py:269-287; `_on_scoreboard` emits only terminal ticks).

Severity: high — this is the dominant live-vs-train timing term.

Command: `cd $E && PYTHONPATH=src uv run python work/lag-devin/measure_lags.py`

### F2 — The LoL mid reprices in ~3.6s; the move is done before the feature arrives — `verified`

Stage: live market.

Evidence (scratch `market_reaction.py`; kill events from `events.parquet` aligned to BookUpdate mids in `core_trace.jsonl.gz`, oriented by `radiant_token_index`; dead-side kill → expected direction of `p_radiant`):

- n=3,179 kill events on 154 maps with core_trace.
- Signed move (toward the killer): m3 med 0.005 / mean 0.0217; **m5 med 0.030 / mean 0.0458**; m8 med 0.030; m11 med 0.030 / mean 0.0491; m60 med 0.030 / mean 0.0496.
- t50 (time to 50% of the 60s signed move): n=2,163, p10 1.53s, **median 3.58s**, mean 8.11s, p90 22.98s.

The median kill's 60s move is ~100% realized by +5s; the feature row describing the kill reaches the model at ~+11s. Even the p10 fast-decision case (t50 1.5s) is inside the feed lag.

Severity: high — combined with F1 it means the live `market_p_radiant` input is structurally post-reaction.

### F3 — The training join at +0 creates a state/mid pairing that cannot occur live — `verified`

Stage: prepare → train.

- `src/lol/05_prepare_dataset.py:310`: `lookup_market_p_after(radiant_book, dire_book, slot.state_wall_us, 0)` — current mid at event time.
- `state_wall_us` is the livestats `rfc460Timestamp` (`livestats_frames.py:652`, `wall_us_for_second:567-573`), proven = GRID `occurredAt` (F1).
- Pre-`332e1c17` code joined `mid(state+10)` and label `mid(state+310)`; the commit set both to `+0`/`+300`. Commit rationale — "the live quote already sits on the GRID clock" — conflates the scoreboard clock (arrives +2.8s) with the table feature feed (+11s).
- Model input includes `market_p_radiant` as a feature (`gbm.py:37-50`); target = `signal_market_p_radiant_300s − market_p_radiant` (`gbm.py:150-154`).

Mechanism: training pairs (state at E, mid(E+0)) while live always pairs (state at E, mid(E+~11)). For post-kill states the trained delta embeds the full ~3¢ repricing; live applies that delta to an already-moved mid, so `fair` overshoots the realized future price by roughly the fast-reaction component on event-adjacent ticks.

Recommendation: restore a lagged join at **≈11s** (measured 10.98 median) on *both* current and label — the window stays 300s and the input mid pairs the way live produces it. Keeping the constant at 10 leaves ~1s residual; 11 is the measured value.

Confidence: verified mechanism; the +11 value is verified on 268 maps.

### F4 — 83% of the LoL backtest front-runs the repricing on synthetic cadence — `verified`

Stage: backtest.

- `feed_schedules.py`: a map with no admitted archive falls back to `GridV1Plan`; signals then fire at `state_ts_us` = event stamp (signals.py:253-296) with `anchor_p = dataset_market_ps` = mid(E+0) (strategy.py:1055).
- Measured on `data/backtests/lol_maker/LIVE/seed0`:

| signal_mode | maps | PnL | $/map | fills/map | BUY mk30s mean | BUY mk30s med |
|---|---:|---:|---:|---:|---:|---:|
| grid_v1 | 782 | +4,684.19 | 5.99 | 10.1 | **+0.70¢** | +0.50¢ |
| schedule | 163 | +486.88 | 2.99 | 15.9 | **−0.14¢** | +0.50¢ |

(grid_v1 SELL mk30 mean −0.35¢; schedule SELL −1.04¢. Equal medians, divergent means → the optimism is a fat tail of event-adjacent buys, consistent with front-running the repricing.)

grid_v1 `signal_age_seconds` median 2.9s (buys) — fills land inside the 3.6s half-move window by construction.

Mechanism: signals arrive at event time with the pre-reaction anchor; the replayed book at E+~3s still shows pre-move prices → fills capture a move that live can never reach (features don't exist until E+11).

Severity: high — ~90% of headline PnL comes from the mode with the fabricated timing.

### F5 — Metadata records lag 10 while the join is 0; `second` is skewed ~13s — `verified`

Stage: prepare → backtest plumbing.

- `backtest_audit.parquet.source_lag_seconds = {10}`; `model.json source_lag_seconds = 10` (production `20260921T095813Z`, research `20260921T095801Z`) — both written from the constant (`05_prepare_dataset.py:446`, `06_train_model.py:104`), not the applied join. `assert_lol_source_lag` (lol_inputs.py:65-80) compares these recorded values against `LOL_SOURCE_LAG_SECONDS=10` (`shared/constants/lol.py:50`) and passes.
- `signals.py:271`: model input `second = dataset.second − lag_seconds` = content−10.
- Live label: `grid_feed.py:172` `second = live_clock_seconds(board, age) − table.feed_delay` = extrapolated scoreboard clock − declared 8. Measured on core_trace ClockUpdates after kill table-receipts: label − true content game time = **+2.98s** median (n=1,485, p10 1.89 / p90 4.04) = actual lag (11) − declared (8).
- Net: for identical content, live's `second` is ~13s ahead of the backtested model input.

Fix proposal: write the *applied* join lag into the audit; replace the `second − lag_seconds` convention with the live label convention (content+3 today, or fix live to label content time directly).

Severity: medium (audit integrity + a systematic feature skew; `second` is one of 12 model features).

### F6 — Dota: consistent training lag + slower market ⇒ no analogous artifact — `verified`

- Dota training joins market at `state.second + TRAIN_LAG_SECONDS` = +10 (`prepare_dataset.py:154`, `shared/constants/dataset.py:20`); measured Dota table lag **9.65s** median (n=10,998 death events, `dota_table_lags.parquet`) → training ≈ live within ~0.4s.
- Dota kill reaction (`dota_reactions.parquet`, n=7,711, 158 maps): move60 med +0.020; m3 med 0.000 (mean 0.007); m11 med 0.005; m30 med 0.015; **t50 median 9.60s** (n=5,044). The Dota mid is still mid-move when the feature feed arrives — live and backtest see comparable states.
- Dota backtest: grid_v1 456 maps +$1,606.58, schedule 127 +$513.87; BUY mk30s = −0.295¢ in **both** modes — the synthetic cadence produces no positive markout tail because the market has barely moved at fill time.
- Dota scoreboard wire lag is even faster than LoL's (1.35s vs 2.77s median).

So the LoL–Dota difference is not feed mechanics (similar ~10-11s table lag both) — it is (a) LoL's lag-0 join breaking train/live pairing and (b) LoL's ~3x faster market making the synthetic-cadence optimism profitable.

### F7 — Archive-schedule replay ≈ live timing, minus ~3s feature freshness — `verified` (mechanism), `likely` (impact)

- `build_schedule_match_signals` (signals.py:354-425): tick `game_second` = the live-emitted label (`archive_index/schedule.py:176` `snapshot.second`); `anchor_p = lookup_reference_mid(series, tick.received_ns)` = real mid at real receipt ≈ E+11 — the live mid timing is preserved.
- The label convention propagates identically (`decision_rows["second"] = game_second`, signals.py:396), but the feature lookup maps label S → dataset row at content second S, while live's label-S tick carried content ~S−3 → replayed features ~3s fresher than live's.
- Schedule-mode results (+$2.99/map, mildly adverse buy markouts) are therefore the closest available proxy for live — and they sit ~50% below the headline.

## Checked and OK

- **Link layer**: `match.json.condition_id` → `links.parquet` → `esports_game_id`; 272/305 LoL tapes carry both GRID archive and livestats windows+details; 268 measured cleanly. (`scan_tapes.py`, `tapes.csv`)
- **Clock alignment**: GRID scoreboard `currentSeconds` ≈ livestats spawn-relative game time (+0.13s median); `assign_game_times`/`wall_us_for_second` pause-adjusted conversion is correct.
- **Stamp provenance**: livestats `rfc460Timestamp` = GRID `occurredAt` exactly (0.00s on 8,715 events) — the dataset's `state_ts_us` is true event time, so lag accounting in seconds is valid.
- **Live emission path**: model snapshots emit only on changed table frames (grid_feed.py:269-287); scoreboard frames never produce a feature row — so the table lag is the feature lag.
- **Token orientation**: dead-side → `p_radiant` direction verified via `radiant_token_index` in `LimitsUpdate`; sign convention consistent (median kill move +3.0¢ toward the killer).
- **Market reaction methodology**: t50 uses forward mid series from BookUpdates anchored on `opened_wall_s`/`opened_now_ns`; events with <60s of post-window or missing p0 excluded (3,179 usable of 8,727).
- `docs/experiments/lol-grid-widget.md`'s earlier observation reconciles: livestats *delivery* is ~55-60s behind real time but stamps event time; GRID delivery is ~3/11s — consistent with this measurement set.

## Open questions

- Does a lag-11-retrained model keep the schedule-mode +$2.99/map? Requires retrain + backtest (out of scope).
- Scoreboard wire-lag tail: p90 10.9s — during GRID stalls the feature lag stretches beyond 11s; how often does live `second` skew exceed ~5s?
- The m3→m5 jump (0.5¢→3.0¢ medians) suggests the dominant LoL market maker reprices in a ~4s window — worth a dedicated study of who moves first (Telonex trade prints vs book).
- Dota table lag measured via death events only (9.65s); NW-only ticks may carry slightly different effective lag.
- grid_v1 signal_age med 2.9s → the +$4,684 cannot be decomposed into front-running vs genuine edge without a lagged-anchor backtest variant.

## Needs from VPS

- One fresh dual-feed capture (`scripts/record_lol_dual_feed.py`) on current production to confirm the ~11s table lag still holds (measurement here covers archived tapes through Sep 2026).
- Live per-map PnL sliced by signal age / proximity-to-kill, to compare against the schedule-mode +$2.99/map estimate.

## Fix proposals (no code changed here)

1. `05_prepare_dataset.py:310,316`: restore lagged joins — `lookup_market_p_after(..., state_wall_us, L)` and label `state_wall_us, L + 300` with **L ≈ 11** (or keep 10, ~1s residual).
2. Record the *applied* join lag in `backtest_audit` (a measured field, not the constant) so `assert_lol_source_lag` catches drift.
3. Fix `second`: either emit content-time `second` live (replace `clock − declared_delay` with a content-derived clock), or offset training/backtest `second` by the measured label skew (+3) — the current `second − 10` in `signals.py:271` points the wrong way.
4. Report schedule-only PnL as the live-comparable headline, or gate the grid-v1 fallback behind an explicit flag.

## Scripts and outputs (all under `work/lag-devin/`)

| file | purpose | key output |
|---|---|---|
| `scan_tapes.py` → `tapes.csv` | tape↔link↔livestats join census | 305 tapes, 272 dual-source maps |
| `prototype_one.py` | single-map alignment proof | occ−ls = 0.00 on all 23 kills |
| `measure_lags.py` → `events.parquet` (8,727), `map_meta.parquet` (268), `wire_lags.csv` (131k) | event-level lag measurement | table 10.98s, scoreboard 2.77s, clock +0.13s |
| `market_reaction.py` → `reactions.parquet` (3,179) | LoL mid reaction to kills | t50 3.58s, move60 +3.0¢ |
| `dota_reaction.py` → `dota_reactions.parquet` (7,711) | Dota mid reaction to kills | t50 9.60s, move60 +2.0¢ |
| `dota_table_lag.py` → `dota_table_lags.parquet` (10,998) | Dota table lag | 9.65s |

Verification commands (run from `$E = /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, `PYTHONPATH=src uv run python`):
- lag measurement: `python <work>/measure_lags.py`
- market reaction: `python <work>/market_reaction.py`, `python <work>/dota_reaction.py`, `python <work>/dota_table_lag.py`
- backtest split: pandas over `data/backtests/lol_maker/LIVE/seed0/{results,fills}.parquet` grouped by `signal_mode`
- label skew: core_trace ClockUpdate `game_second` at first tick ≥ `tb_recv` minus livestats `ls_gt` → +2.98s median
