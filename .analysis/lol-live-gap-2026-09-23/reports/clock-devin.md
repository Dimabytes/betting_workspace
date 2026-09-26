# clock-devin: LoL timing audit — train vs backtest vs live on one wall clock

Scope: what information each LoL training row, each LoL backtest signal, and each LoL live
signal has, on one common wall clock; look-ahead and look-behind; second-zero alignment;
market-prior comparison; Dota contrast.

Clock vocabulary used below:

- **physical second s**: true in-game time, zero ≈ champion spawn.
- **livestats stamp**: `rfc460Timestamp` on each frame. `StampedFrame.wall_seconds` is the
  *stamp*, not the collector receipt (`src/lol/livestats_frames.py:481-487`). The dual-feed
  capture shows frames arriving `received_at − stamp` = **median 75.4 s** (p10 69.7, p90 81.8),
  i.e. the stamp is applied well upstream of delivery (the ~60 s API gate). Death-event
  algebra below puts the stamp ≈ event + ~0–5 s. livestats `game_time` = `stamp −
  spawn_stamp − pauses` (`assign_game_times`, `livestats_frames.py:540-562`).
- **GRID clock C(T)**: `currentSeconds` extrapolated to receipt `T`
  (`live_clock_seconds`, `src/trader/grid_widgets.py`). Publish→receipt median 2.79 s
  (this capture, n=165).
- **GRID label g**: `second` the live system assigns a table tick = `C(T) − feed_delay`
  (`feed_delay` constant 8 in the capture).
- **Telonex wall**: book timestamps on the collector clock.

## TL;DR (ranked by expected live-vs-backtest impact)

1. **LoL grid-v1 backtest is a zero-lag feed; live sees ~11 s-old state.** Signals/decisions
   fire at `state_ts_us` = the livestats frame *stamp* ≈ event time (`signals.py:260`), and
   run.py passes `lag_seconds=0` for LoL. 782/945 (83%) of the latest LIVE run replayed
   grid-v1. Live decides at GRID receipt, content ~10.9 s stale. Backtest therefore acts on
   fresh state at a market price that predates the market's reaction — systematic
   look-ahead, high severity.
2. **Commit 332e1c17 moved training *away* from live in the lag dimension.** It changed
   `current` mid from `state_wall_us + 10 s` to `+0`, and the label from `+310` to `+300`
   (`git show 332e1c17`). The old +10 s mid join was accidentally close to live's ~11 s
   pipeline delay; the new as-of-0 join models an instantaneous feed. It fixed the
   second-0 semantic but removed the only place the feed delay was modeled.
3. **Live `second` label runs ~+6.7 s ahead of the features it ships.** Same physical death
   events: GRID-labeled second = livestats game_time + 6.7 (n=10, spread 1.7 s). Live model
   gets `(second=g, features(g−6.7))` — a skew training never contains
   (`(second=s, features(s))`).
4. **Archive-schedule backtest (163/945 maps) is arrival-correct but feature-fresh.** It
   decides at `tick.received_ns` with the market as-of received_ns — correct — but loads
   `game_features` at `tick.game_second` = the live *label* g, i.e. features of physical
   second g while live's content was physical g−6.7: ~6.7 s feature look-ahead vs live.
5. **Dota is structurally consistent where LoL is not.** Dota validation rows pair
   market-second M with features of state M−10 (`prepare_dataset.py:207-211`) and the
   grid-v1 decision wall = wall(M) — modeling ~10 s staleness, matching Dota's live GRID
   delay. LoL has no equivalent lag term post-332e1c17.
6. **Market prior differs in source and window.** Train: last two-sided Telonex mid in
   `[spawn_stamp−61 s, spawn_stamp−1]` (`05_prepare_dataset.py:180-192`). Live: Polymarket
   prices-history minute bars, last aligned pair ≤ GRID-horn−90 s (horn = GRID clock-0
   estimate, measured ≈ spawn_stamp+28.3 s → anchor ≈ spawn_stamp−62), up to 6 h lookback.
   Similar target, different feed and freshness floor.
7. **Pause-clock semantics are unverified for GRID.** livestats compresses pause gaps
   (32 s pause detected in the capture). If GRID's clock does not freeze identically, the
   labeled `second` drifts by the pause length vs `game_features` keys, compounding the
   label skew after every pause.

## Findings

### F1 — grid-v1 backtest decides at state stamp time: zero feed lag (stage: backtest; severity: high; confidence: verified)

Claim: for the 782/945 maps replayed as grid-v1, the simulated decision wall time is the
livestats frame stamp ≈ physical event time. Features are fresh; the book is read at the
same stamp. Live can only act ~11 s later on ~11 s-stale content.

Evidence:

- `build_match_signals` sets both feed and decision timestamps to `state_ts_us × 1000`
  (`src/backtest/signals.py:260`, `274-287`); `state_ts_us` is `slot.state_wall_us`, the
  selected frame's stamp (`05_prepare_dataset.py:281`; `livestats_frames.py:650-656`).
- `run.py` passes `lag_seconds=0` for the LoL path; in `select_cadence_rows` the lag only
  picks the cadence band (`feature_second = second − lag_seconds`, `signals.py:197-203`)
  and in `build_match_signals` the model's `second` input is `second − lag` (`signals.py:272`).
- `LOL_GRID_V1_BANDS` = 8/6/5/5 s mean intervals (`signals.py:75-80`), drawn by a seeded
  blake2b per (match, second) (`signals.py:160-165`).
- Results: `results.parquet` in `data/backtests/lol_maker/LIVE/seed0` → `signal_mode`:
  `grid_v1` 782, `schedule` 163; `feed_source`: grid for all 163 schedule rows.
- Live side: table content ~10.9 s stale at receipt (F2/F3 evidence), model decision at
  receipt + ~ms (`match_worker._compute_decision`).

Mechanism: the backtest clock is "event-time": the strategy sees state(s) the instant it is
stamped, with the market mid from the same instant — before the market can react to the
event. Live sees the same state ~11 s after the event and quotes into a market that has had
~11 s to move. This inflates fill quality and markouts for state-chasing signals and
explains a flat-live vs profitable-backtest gap.

Next check: replay the same 945 maps with signal timestamps shifted by +11 s (state wall +
observed GRID pipeline) and compare PnL/markout; cheapest possible falsification.

### F2 — GRID `second` label is ~+6.7 s ahead of the table content's true second (stage: live; severity: high; confidence: verified for one capture, likely general)

Claim: live computes `second = live_clock_seconds(board, age) − feed_delay` = `C(T) − 8`,
but the content in that tick is the state of physical second ≈ g − 6.7. So the model's
`second` input overstates the features' age by ~6.7 s.

Evidence:

- Command: `PYTHONPATH=src uv run python scripts/compare_lol_grid_livestats.py
  data/lol_dual_feed/lol-fxw7-los-2026-08-29-20260829T180601Z` →
  `death offset: grid_second = livestats_second +6.7 (n=10 spread=1.7)`,
  per-event offsets +5.7..+7.4; `feed_delay` median/min/max = 8.
- `measure_dual_feed2.py` (this scratch): horn estimate `occurred_at − clock_seconds`
  median 1788026702.4 vs livestats spawn stamp 1788026674.16 → GRID clock-0 sits +28.3 s
  after the livestats zero on the stamp clock; the capture contains a 32 s pause
  (livestats gt 4.1) which dominates that decomposition (see F6). Post-pause GRID clock
  reads `livestats_gt + 3.8`.
- Decomposition (verified arithmetically, mechanism likely): label − content =
  (C − livestats_gt ≈ +3.8) + (true content age ≈ 10.9 − declared 8). The declared 8 s
  subtraction under-counts the pipeline by the ingest→publish→receipt tail (~2.4–2.8 s
  measured publish→receipt) plus any ingest staleness.

Mechanism: live feeds `(second=g, features(g−6.7))`; every training row is
`(second=s, features(s))`. `second` is a model feature, so this is a direct train/serve
skew; it also misplaces the signal relative to the net-worth curve by ~7 s.

Next check: record 2–3 more dual-feed captures (ideally pause-free) and confirm +6.7 is
stable; if so subtract a calibration constant in the live reducer.

### F3 — commit 332e1c17 moved training away from live (stage: prepare; severity: high; confidence: verified)

Claim: the as-of-0 mid join removed the only term approximating live's ~11 s feed delay.

Evidence (`git show 332e1c17`):

- `05_prepare_dataset.py:310`: `current = lookup_market_p_after(..., state_wall_us, 0)`
  (was `+ LOL_SOURCE_LAG_SECONDS` = +10).
- `05_prepare_dataset.py:316-321`: label now at `state_wall_us + 300` (was `+310`).
- `replay.py` book window dropped the `LOL_SOURCE_LAG_SECONDS` ends.
- `LOL_SOURCE_LAG_SECONDS = 10` is now metadata-only: written into dataset/model manifests
  (`05_prepare_dataset.py:446`, `06_train_model.py:104`) and asserted equal in backtest
  (`src/backtest/lol_inputs.py:66-78`) — it no longer shifts any join.

Mechanism: pre-commit convention = "features(s), market mid at s+10, label mid at s+310" —
a decision ~10 s after the state, looking 300 s ahead. That is within ~1 s of live's
effective "decide at receipt ≈ state+11, market mid at receipt". Post-commit = "decide at
state+0", which neither live (state+~11) nor the market (reacts ~5–10 s after the event;
doc `lol-grid-widget.md` death→mid probe peaked 4–5 s after the frame stamp) matches.
Direction: **away from live** on both the input mid and the label horizon (live's realized
300-s label starts ~11 s later than training's).

Caveat: the commit fixed a real second-0 mislabel (mid at s+10 while `second`=s made the
anchor read T+10). The correct fix is to keep `second`=s but re-introduce the delay on the
*decision* side (as the archive-schedule path already does structurally).

### F4 — archive-schedule replay is arrival-correct but looks features up at the mislabeled second (stage: backtest; severity: medium; confidence: verified structurally, magnitude likely)

Claim: for the 163 schedule-replayed maps, decisions fire at `tick.received_ns` and the
market anchor is the mid as-of received_ns — correct. But the feature row is
`game_features[game_second = tick.game_second]` where `tick.game_second` = the live label
g; live's actual content at that tick was physical second g−6.7.

Evidence:

- `_match_schedule_decisions` (`src/backtest/signals.py:306-345`): decision ts =
  `tick.received_ns`; `anchor = lookup_reference_mid(series, tick.received_ns)`;
  feature row by exact `tick.game_second`; ticks with no feature row or stale gap stay
  feed-only.
- `build_schedule_match_signals` (`signals.py:348+`): model input `second` =
  `game_second` (the label), `market_p_radiant` = anchor at received_ns.
- Feed plans are strict: `feed_schedules.py:332-343` refuses cadence/lag overrides for
  schedule-bound maps; exclusions are explicit (3 matches excluded: 2× feed_gone,
  1× no_terminal); `assert_archive_binding` verifies identity.

Mechanism: replay features are ~6.7 s fresher than live's content at the same tick
(look-ahead), while `second`, the book, and the wall clock match live. Smaller than F1 but
same sign — the backtest is optimistic in both modes.

Next check: shift `tick.game_second → g−7` (or a measured per-capture constant) when keying
`game_features`, or better: store the *content* second at collect time.

### F5 — market prior: same idea, different source and freshness floor (stage: prepare vs live; severity: low-medium; confidence: verified code, magnitude unmeasured)

Claim:

- Training: `lookup_strict_prior` = last two-sided Telonex mid per token in
  `[spawn_us − 61 s, spawn_us − 1]`, pair-normalized (`05_prepare_dataset.py:180-192`,
  joined at `:392`). Fails → map dropped (`REASON_MISSING_PRIOR`).
- Live: `market_prior.py` — Polymarket prices-history minute bars, trailing 6 h window
  ending at `horn − 90 s`, latest per-token quote with 30 s pair alignment, normalized.
  Horn = `occurred_at − clock_seconds` = GRID clock-0 estimate (measured ≈ spawn_stamp
  +28.3 s in this capture) → anchor ≈ spawn_stamp − 62 s.

Mechanism: targets are similar (~1 min pre-spawn) but the live quote can be minutes to
hours old if no aligned pair exists near the anchor, and the price source (CLOB minute
bars) differs from training's top-of-book Telonex mids. Any systematic offset shifts
`market_radiant_prior` and every `d_market_*` feature.

Next check: compare prior values on the 945-map set (training prior vs what the live path
would have produced) — a per-map distribution of the difference.

### F6 — pause handling can drift GRID labels vs livestats seconds (stage: collect/link; severity: medium; confidence: speculative)

Claim: `assign_game_times` compresses >5 s stamp gaps with frozen stats
(`livestats_frames.py:113-124`, `540-562`). In the fxw7 capture a 32 s pause at livestats
gt ≈ 4 precedes all GRID data (first board is at the pause tail), so whether GRID's
`currentSeconds` froze is unobservable here. Two consistent readings:

- GRID clock froze (isTicking honored): clock-0 ≈ spawn_stamp − 3.8 s; label skew stays
  ~+6.7 constant.
- GRID clock kept ticking: clock-0 = spawn_stamp + 28.3 s; pre-pause labels would have run
  ~25 s *behind* content and the skew jumps +32 s across the pause.

Either way, post-pause label-vs-content = +6.7 (F2). But if GRID does not freeze, every
pause adds its duration to the label-vs-livestats offset — schedule ticks' `game_second`
would then miss `game_features` keys or hit wrong rows after each pause.

Evidence: pause detected `start_game_time=4.075, duration=32.03`
(`fxw7_measure.out`); GRID boards begin only at clock=8/occurred=1788026710.3.

Next check: dual-feed capture covering a mid-game pause; inspect `isTicking` on boards
during pauses in existing live tapes.

### F7 — cadence realism: grid-v1 8/6/5 s vs measured table arrivals (stage: backtest; severity: low; confidence: verified numbers, impact unclear)

Claim: grid-v1 subsamples one tick per second by seeded Bernoulli with mean interval from
the bands (`signals.py:184-215`). Measured GRID table inter-arrival in the capture: median
2.0 s, p90 11.4 s, max 28.5 s — bursty reducer output, not a near-periodic 8/6/5 grid.

Mechanism: tick density/shape differs, so live gets more frequent updates than the
simulated grid; minor vs the zero-lag issue but adds noise to cadence-sensitive behavior
(stale detection, order refresh).

## One common timeline (event: game second s occurs at wall W; livestats stamp ≈ W + ε, ε≈0–5 s)

| Stage | Feature state (physical second) | Decision / signal wall | `second` input | Market mid used | Label |
|---|---|---|---|---|---|
| Train row s | s (frame ≤2 s old at boundary, `select_grid_rows` :633-657) | stamp(s) ≈ W+ε | s | as-of book at stamp(s), ≤5 s stale | as-of book at stamp(s)+300 |
| Backtest grid-v1 | s (same row) | stamp(s) ≈ W+ε — **~11 s before live can act** | s | book at stamp(s); strategy also sees book at stamp(s) | n/a (market_seconds give per-second mids by wall) |
| Backtest schedule | g (label) → physical g, i.e. **6.7 s fresher than live's g−6.7** | tick.received_ns ≈ W'+0 (true receipt) | g | book as-of received_ns | n/a |
| Live | content ≈ physical g−6.7 ≈ gt(T)−11 | receipt T ≈ event+~11 | g = C(T)−8 ≈ gt(T)−4 | live book at compute time (~receipt) | n/a |

Offsets in seconds (decision wall relative to event wall W): train 0..+ε; grid-v1 0..+ε;
schedule +~11 (real receipt); live +~11. Feature staleness at decision: train ~0; grid-v1
~0; schedule effectively −6.7 (fresher than live); live ~10.9.

## Checked and OK

- `find_asof_quote` never reads future data: latest two-sided quote ≤ target, rejects
  >5 s-old quotes (`src/shared/utils/telonex_book.py`) → the +300 s label is an honest
  as-of at `state_wall_us+300`.
- Frame-age gate: rows dropped when the newest frame at the second boundary is >2 s old
  (`livestats_frames.py:646-650`).
- Spawn anchor bounded: first spawn-shaped frame within `loading_anchor_ts .. +900 s`
  (`livestats_frames.py:360-373`; shape = all ten players 500 g/lvl 1/0 deaths, `:352-357`).
- Pause detection is conservative: a >5 s stamp gap counts as pause only when the
  gold/kills/HP progress key is unchanged (`:113-124`) — feed stalls with changing stats
  are correctly *not* compressed.
- Schedule path has no silent fallback: cadence/lag overrides refused, exclusions
  explicit, `assert_archive_binding` verifies the archive (`feed_schedules.py:332-343`).
- Live staleness gates: entry blocked past `GRID_FEED_STALE_SECONDS`=16 s; exit/latch up
  to `EXIT_FEED_STALE_SECONDS`=45 s; finished/pre-horn/paused snapshots blocked
  (`window_reason`, `match_worker.py`, `session_quoting.py`).
- Live market input is the current MDS book at compute time (`match_worker._compute_decision`,
  `model_server.py`) — arrival-aligned, not feed-stamp-aligned.
- Anchor consistency: model decisions rejected when current book differs from the signal
  anchor by >0.01 (`ANCHOR_TOLERANCE`), matching training's as-of pairing semantics.
- Livestats delivery is ~75 s after stamp (median, measured) — correctly archive-only;
  live does not depend on it.

## Open questions for the owner

1. Does GRID `currentSeconds` freeze during LoL pauses? The capture's only pause precedes
   the first board; `isTicking` during pauses decides whether F6's drift exists.
2. Is +6.7 stable across series/feed health? One capture, n=10 deaths. Needs 2–3 more
   dual-feed recordings, ideally pause-free.
3. Exact physical meaning of `rfc460Timestamp`: measurements here + parallel finding
   ("livestats rfc460 ≈ GRID occurredAt") put it ≈ event+~3–5 s, but the API's ~60 s gate
   means this can't be settled from delivery timing alone.
4. Dota live runs the same GRID reducer — does the same label-vs-content skew exist for
   Dota, and does Dota's baked-in 10 s lag absorb it differently?
5. Live prior uses `horn−90 s` on minute bars vs train's `spawn−61..−1 s` on book mids:
   measured distribution of |live_prior − train_prior| per map would size F5.

## Needs from VPS

- A `grid_state.jsonl` + `session.jsonl` pair from a live LoL map containing a pause, to
  check `isTicking` during pauses and to verify the +6.7 label skew in production tapes.
- If easy: one more `record_lol_dual_feed` capture on a live map.

## Scripts and outputs

Under `$R/work/clock-devin/`:

- `measure_dual_feed2.py` — boards/horn series, per-tick label offset and content age,
  tick cadence. Output: `fxw7_measure.out` (GRID ticks=167, boards=165, livestats
  frames=2401; pause 4.075 s/32.03 s; horn−spawn_wall=+28.3 s; scoreboard
  recv−occurred median 2.79 s; feed_delay=8 const; table inter-arrival median 2.0 s).
- `livestats_delivery_lag.out` — received_at − rfc460Timestamp: median 75.4 s
  (p10 69.7, p90 81.8), stamp cadence 0.2 s.
- `measure_dual_feed.py` — earlier draft (superseded by v2).
- Prior command run for the death offsets:
  `PYTHONPATH=src uv run python scripts/compare_lol_grid_livestats.py
  data/lol_dual_feed/lol-fxw7-los-2026-08-29-20260829T180601Z` → offset +6.7 s
  (n=10, spread 1.7), paired samples at +7 s: 1235.
- `results.parquet` mode counts: `signal_mode grid_v1=782, schedule=163`,
  `feed_source grid=163`.
