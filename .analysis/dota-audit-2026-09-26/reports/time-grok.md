# time-grok — timing and look-ahead across dataset, backtest, live
Status: FINAL

## 1. Summary

- On maps with a pre-horn pause, the archived horn is early by that pause. The catalog prefers it, so the market-second cache, the validation join, and the production minute rows line STRATZ state up with a book from before that state. Orchestrator event study: 58 maps, 57 in the validation set (146,373 rows). Live GRID `second` is not shifted; the quote clock is the scoreboard.
- Live Oddin is a ~16 s feed. Training and `model.json` assume 10 s. Every Oddin decision pairs a state the market has already had ~6 extra seconds to price, on the $200 clip. Measured, not the ~2 s the 1 Hz cadence suggests.
- Live GRID is a flat 8 s table delay (feature age p50 8.43 s on 16 archives). The grid-v1 backtest, 464 of 613 maps in the published Dota run, uses the training 10 s. It is slightly stale versus live GRID, and it never raises `paused`.
- 92 of 717 validation maps are dropped from bulk backtests because some 120 s book hole exists anywhere from second −60 through 900. That hole is often after the buy cutoff. The published PnL is on a future-conditioned subset.
- Research early stopping still fits on the same validation maps the backtest scores.
- Dota grid-v1 `decision_lag_ns = 0` is the right counterpart of the join: the row timestamp is already the market second. Adding another 10 s would double-count. No feature as-of reads the future book.

## 2. Findings

| ID | Sev | Layer | Title | Confidence | Impact |
|---|---|---|---|---|---|
| time-grok-F10 | S1 | collect / prepare / train | Archive horn early by the pre-horn pause shifts the market join | verified | 57 validation maps, 146k rows; state is ahead of the book |
| time-grok-F1 | S2 | live / train | Oddin state is ~16 s old; the model was trained at 10 s | verified | $200 clip; residual move overstated |
| time-grok-F2 | S2 | prepare / backtest | Whole-map book gap drops maps using future tape | verified | 92/717 maps leave the backtest |
| time-grok-F3 | S2 | backtest | grid-v1 clock is never paused | verified | 464/613 published maps quote into pauses |
| time-grok-F4 | S2 | train | Early stopping uses the backtest maps | verified | model selection sees the evaluation labels |
| time-grok-F5 | S3 | live / backtest | GRID is 8.4 s, grid-v1 and training are 10 s | verified | ~1.6 s extra staleness; buy cutoff ~10 s early |
| time-grok-F6 | S3 | live / backtest | Fair is computed past the last trained second (540) | verified | exit prices after 480 are out of support |
| time-grok-F7 | S3 | backtest series | Series bucket clock is the market second, 10 s ahead of the model second | verified | bucket flips near 120/240/360/480 on grid-v1 |
| time-grok-F8 | S3 | prior | Pre-horn pauses move the live prior anchor later than the training anchor | likely | up to the pause length; price delta not measured |
| time-grok-F9 | S4 | backtest | Steam entry-stale constant does not match live; Steam is not selected | verified | latent until `stream_delay_s` ≤ 61 |

## 3. Findings detail

### time-grok-F1 — Oddin ~16 s vs training lag 10 s

Where: `src/trader/oddin_feed.py:382-404` (snapshot `second` is payload `gameTime`, no lag subtracted). `src/trader/model_server.py:290` passes that second straight in. `src/trader/game_profile.py:61-65` loads the no-XP catalog and checks `source_lag_seconds` against `TRAIN_LAG_SECONDS` (10). `src/shared/constants/dataset.py:21-23`.

What is wrong: the contract check compares two constants. It does not compare either constant to the feed. Oddin `second` is the game clock inside a snapshot whose `lastUpdatedAt` is already ~16 s behind receipt. The book the model sees is the book at receipt (`src/trader/match_worker.py:407-444`). Training pairs STRATZ second S with the book at game-second S+10 (`src/prepare_dataset/prepare_dataset.py:147-163`).

Mechanism: at decision time the market has had ~16 s to react to the state the features describe. The trees were fit on markets that had had 10 s. Predicted |Δ| is the move still expected after 10 s, so on Oddin it counts ~6 s of move that is already in the price. Entries get easier. The archive replay does the same thing on purpose: `src/backtest/signals.py:640-661` sets `second` to `tick.game_second` and prices the book at `received_ns`. Those 13 `schedule:oddin` maps in the published run match live Oddin and still disagree with training.

Evidence: `work/time-grok/measure.py` on local `data/trader` (25 Oddin maps with ≥30 in-progress ticks, seconds 1..540). Per-map median of `received − (horn + game_second)`: p50 16.04 s, p25 15.71, p75 16.29, p90 21.07. Per-map median of `received − lastUpdatedAt`: p50 15.60 s, p99 15.65 s (the transport lag does not wander). One of the 25 maps has a feature-age median in the hundreds of seconds while transport stays ~15.6 s: the horn is frozen on the first `game_time > 0` (`oddin_feed.py:383-384`) and a later clock no longer matches it. Archive index, Dota, admission `admitted`: 15 Oddin rows, `delay_s` p50 16 s, all `delay_evidence=meta` (`data/archive_index/index.parquet`). Published seed0 `summary.json` `signal_groups`: `schedule:oddin` 13. Local `match.json`: 76 Oddin / 657. `config/trading.toml` Oddin clip is $200.

Impact: every live Oddin map, and the 13 archived ones in the backtest. Direction is optimistic entries versus the training definition. Size not re-fit (full backtests are out of scope).

Confirm or fix: refit or shift the Oddin feature second so the market is 10 s after the state, or train an Oddin-lag catalog and point the satellite at it. Name the drifted-horn map before changing horn pinning.

Introduced: the 10 s contract is `TRAIN_LAG_SECONDS`; Oddin reducer has used raw `gameTime` since the feed landed. The lag check does not look at the feed (`model_server.py:271-272`).

### time-grok-F2 — Book-gap exclusion reads the rest of the map

Where: `src/prepare_dataset/prepare_dataset.py:111-120` and `:338-339`. Window is `MODEL_START_SECOND` (−60) through `VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE` (900) (`src/shared/constants/dataset.py:12-19`). `src/backtest/selection.py:53-68` and `:100` drop `backtest_book_gap_excluded` maps.

What is wrong: a 120 s run of non-`ok` seconds anywhere in that window removes the map from bulk backtests. A hole at second 700 removes the map's trades at second 100. Live has no such filter.

Evidence: research split parquet, validation rows: 717 maps, 92 flagged. Of the first 80 flagged ids, 16 still have a market-seconds cache; on those the excluding run ends at p50 second 640, and 11/16 end at or after 480. Published run selects 613 maps (`LIVE/seed0/manifest.json`). `1e894b24` dropped a different tape filter; `find_longest_book_gap` is still at HEAD (blame history includes `1ae88f73`, `da0d7253`).

Impact: the headline backtest is conditioned on maps that stayed quotable through second 900. 92/717 is 13% of the validation list. Direction is a cleaner sample. Not a per-decision feature leak.

Confirm or fix: drop the flag, or compute the gap only on seconds the policy can still enter (under 480) using books known at that second.

### time-grok-F3 — grid-v1 has no pause

Where: `src/backtest/run.py:493-507` sets `observed_clock = None` for non-schedule plans. `src/backtest/strategy.py:1042-1058`: with no tape, `paused` is always false and `game_second` is 0 before the cutoff and 540 after. `src/strategy/quoting.py:958-959` blocks the whole plan when `clock.paused` (live sets that from the snapshot, `src/trader/match_worker.py:487-491`).

What is wrong: on a schedule replay the tick carries `paused` (`run.py:488-491`). On grid-v1 the only pause effect is a hole in `state_ts_us`, and the signal then ages out at 16 s entry / 45 s exit (`GRID_FEED_STALE_SECONDS`, `EXIT_FEED_STALE_SECONDS`). Live stops on the pause tick.

Evidence: seed0 `signal_groups.grid_v1` = 464 of 613. Catalog pauses (`match_catalog.parquet`, 3207 maps): 2201 maps have a pause, duration p50 85 s, p75 174 s. A pause longer than 16 s still leaves the backtest quoting for the first 16 s (entries) and 45 s (exits).

Impact: bounded, on the synthetic majority of the published run. Archive maps (136 GRID + 13 Oddin) follow the live pause bit. Short pauses are where the fills differ; long pauses converge after the stale timers.

Confirm or fix: drive grid-v1 from the same pause list `get_state_available_ts` already uses, and set `paused` across the wall gap.

### time-grok-F4 — Early stopping on the validation maps

Where: `src/train_model/train_model.py:87-88`, `:110-113`, `:189-193`. `fit_research_members` early-stops on `validation_features` / `validation_target` built from the validation parquet. Those match ids are the backtest universe (`selection.py` reads the research split).

What is wrong: the booster's stopping round is chosen on the maps whose PnL is later treated as out-of-sample. Labels are the 300 s-ahead mids (`gbm.py:150-154`). This is the LoL-audit item A.5, still at HEAD.

Impact: research model `20260924T183856Z` (the published backtest model) is the one this path produces. Production training is a separate catalog; the decision-relevant backtest number uses the research one (`manifest.json` `model_path` …`/research`).

Confirm or fix: stop on a slice that is neither the backtest maps nor their labels. Owned in depth by train-luna; recorded here because it is look-ahead in the number this audit is about.

### time-grok-F5 — GRID 8 s vs the 10 s join

Where: `src/trader/grid_widgets.py:14-15` (`series_table` documented at 8 s). `src/trader/grid_feed.py:180-181`: `second = live_clock_seconds(board, age) - table.feed_delay`. `src/trader/source_picker.py:116-125` probes that table delay, not the scoreboard. Training: `prepare_dataset.py:148` joins state S to market row S+10. grid-v1 then acts at that market row's `state_ts_us` with `decision_lag_ns = 0` (`signals.py:473`) and feeds `second - 10` (`signals.py:484`).

What is wrong: live GRID's feature age is the table delay plus a fraction of a second of receipt. It is not 10. grid-v1 implements 10, so versus live GRID the backtest shows the model a slightly older state than the bot had. Buy cutoff diverges the same way: grid-v1 cuts at wall time of true game-second 480 (`run.py:495-500`), when the model second is 470. Live cuts when `snapshot.second >= 480` (`strategy/quoting.py:127`), and that second is already `clock - 8`, so the true clock is ~488.

Evidence: archive index, 204 admitted Dota GRID rows, `delay_s` exactly 8.0 (203 from meta, 1 measured). Replay of 16 local GRID archives, in-progress, second 0..540, not paused: per-map median feature age p50 8.43 s, range of those medians 8.34–8.63 s. Table `frame.delay` median 8 on all 16. `model.json` `source_lag_seconds` is 10 for research and production.

Impact: ~1.6 s on 464 grid-v1 maps and on every live GRID decision (581/657 local archives). Too small to invent a PnL, large enough that "lag 10" is the wrong sentence for GRID. Archive GRID maps (136) replay the real 8 s and match live.

Confirm or fix: set the Dota training join to 8 s for GRID, or subtract `10 - feed_delay` from the live GRID second so the pair matches the join. Do one of those, not both.

### time-grok-F6 — Model window does not end at 540

Where: minute states are `range(-60, 600, 60)`, so the last trained second is 540 (`prepare_dataset.py:140`, `TRAIN_END_SECOND_EXCLUSIVE = 600`). Live `in_model_window` for `IN_PROGRESS` is `second >= 0` with no end (`session_quoting.py:94-100`; test `test_in_progress_model_window_has_no_clock_end`). Buys stop at 480 (`BUY_CUTOFF_SECOND`). Archive decisions keep any tick whose `game_second` is ≥ −60 and ≤ the last feature row, and feature rows run to `durationSeconds` (`signals.py:551-559`, `stratz_seconds.py` exact-second loop). grid-v1 validation rows do the same: sample match 8837869969 joins through second 3694.

What is wrong: after 540 the `second` feature is outside every training row. The fair is still passed to SELLs. Live and both backtest paths do this, so it is not a live-vs-backtest skew. It can hold a position when the OOD fair sits above the touch (`SELL = max(ceil(ask), ceil(fair))` in the strategy notes). That is one candidate for the 39/39 settlement tail; this pass did not score those fairs, so the link stays speculative.

Impact: exits after 480 on maps that are still open past 9 minutes. Entries are already cut.

Confirm or fix: hold the last in-support fair after 540, or train through the seconds the exit path actually scores.

### time-grok-F7 — Series c-bucket uses the un-lagged second on grid-v1

Where: `src/backtest/series_run.py:566-580`. For Dota grid-v1, `lag_ns` is 0 and the stored second is the validation row's `second`, which is the market second M (`prepare_dataset.py:212-220` spreads `**market_row`). The model was shown M−10. `series_c_bucket` edges are 120, 240, 360, 480 (`strategy/series_link.py:22`). Archive plans store `tick.game_second`, which is the second the model saw.

What is wrong: on grid-v1 series runs the multiplier bucket is 10 s ahead of the feature clock. Seconds within 10 s below an edge take the next bucket.

Impact: series backtests only. Map-winner LIVE run does not read this map. Off-by-one-bucket rate is about 10/120 of decisions near the edges.

Confirm or fix: store `second - TRAIN_LAG_SECONDS` in that dict for Dota grid-v1.

### time-grok-F8 — Prior anchors match; the pause hits the catalog horn instead

Where: training prior anchor is GRID spawn, else horn (`src/collect/s05a_fetch_prices_history.py:56-73`). Live Dota latches `event.horn_unix_seconds - 90` on the first pre-horn or in-progress tick (`match_worker.py:544-554`) and does not recompute it.

What holds: a true horn would put the live anchor `spawn + pre-horn pause` against a training anchor at spawn (998/3207 maps, pause p50 66 s). The pin in F10 stores a horn that is early by that same pause, so `pinned_horn - 90` lands on spawn. The two errors cancel for the prior. N4's event-study residual of the horn itself is about −0.6 s, so the priors match to about a second, not to the pause. Not an in-game price either way.

Impact: drop the "prior is D seconds late" reading. The D seconds are in the catalog horn and the market join (F10).

### time-grok-F10 — Archived horn is early by the pre-horn pause

Where: `src/trader/live_feed.py:102-111` `horn_is_pinnable` keeps the first PRE_HORN tick for Steam and GRID. Oddin waits until `second > 0` (`deebb730`). `match_meta.py:329-347` writes that stamp into `horn_at_utc` and will replace it while the file is unfinalized; `finalize_match` then forces `trusted_horn` (`match_meta.py:372-373`), and both GRID and Oddin summaries take `first_event_horn_iso` (`grid_archive.py:64`, `oddin_archive.py:39`), which is the same first pinnable tick. `s06_publish_catalog.py:119-127` prefers `archive_horn_at_utc` over `get_horn_datetime`. The market cache stamps every second with that horn (`build_market_data.py:92-98`, `get_state_available_ts`).

What is wrong: GRID's pre-horn clock stops during a pause, so `occurredAt - clock_seconds` (`grid_feed.py:113-115`) stays at the pre-pause origin. That origin is early by the pause length D versus spawn + 90 s + D. The live feature second does not use this horn. `grid_feed.py:180-181` sets `second` from the scoreboard clock minus the table delay, and after the pause that clock matches the GRID-derived horn (orchestrator dump of `grid-3006669-m1`: clock 60 at 16:28:08 implies horn 16:27:08, catalog formula 16:27:09, stored `horn_at_utc` 16:19:40, pause 449 s). Buy cutoff and the model window on the live clock are not off by D.

What the early horn does shift: `state_ts` for game second S is D seconds before the wall time when the game clock actually read S. STRATZ state at S is the true clock. The training and validation joins then pair that state with the book at cache second S+10, i.e. the book at true time of S+10 minus D (`prepare_dataset.py:147-163` and `:199-220`). For D > 10 the features are from after the market timestamp. The 300 s label is 300 s after that same early stamp, so it is early by D as well. Research early stopping reads those validation rows (`train_model.py:189-193`). Production training concatenates the validation minute rows into the fit (`prepare_dataset.py:274`, `train_model.py:269-281`) with no holdout.

Schedule-mode decisions do not inherit the shift. They price `lookup_reference_mid` at `tick.received_ns` (`signals.py:562`). The mid series is the cache's `(state_ts, market_p)` pairs (`postprocess.py:115-131`); each pair is the real book at that wall time, and receipt is a real wall time, so the as-of price at a live tick is the book then. Buy cutoff on a schedule is `tick.game_second` (`run.py:476-483`), the live clock. `game_end_ns` prefers the terminal tick's receipt (`run.py:484-487`). The replay window is the part that moves: it is `[horn − 2 min, ended_at + 1 min]` (`context.py:12-33`), and `ended_at` uses the same early horn (`s06` `get_state_available_ts` at `duration`). The loaded book stops D − 60 s before the true end. Median D 70 s is about 10 s short; the max 549 s is about 8 minutes short.

Evidence: orchestrator N4 / N4b (event study: grid-derived horns peak at lag 0, archive horns with D ≥ 20 s peak at +62.5 s median). Counts from that study, not re-fit here: 58 archive-horn maps with a pre-horn pause (56 GRID, 2 Oddin), D median 70 s, max 549 s; 57 in the validation dataset (146,373 rows); 50 in the LIVE backtest, all `schedule` mode, engine PnL $331 of ~$714 schedule PnL. Code path above is what makes a D-second early horn move the join and not the live `second`.

Impact: look-ahead of about D − 10 s (median ~60 s) in the validation rows that stop the research model, and in the production minute rows the live model fits. The 50 schedule maps' quote clock is the archive receipt, so their backtest decisions are not stamped D seconds early; their book coverage and any fallback `game_ended_at` are. Every new archive with a pre-horn pause takes the same horn (about a third of catalog maps have one).

Confirm or fix: pin GRID/Steam the way Oddin was pinned, only once the clock is past the pause (`second > 0` and ticking), and rebuild catalog horn, market cache, and both datasets for the 58. Do not "fix" it by also subtracting D from the live second.

### time-grok-F9 — Steam stale mismatch is unused

Where: live Steam entry stale is 3 s (`steam_live_feed.py:10`). `entry_stale_seconds` returns 15 s for Oddin and 16 s for everything else (`feed_schedules.py:385-389`), so a Steam schedule would get GRID's 16 s. The picker compares `stream_delay_s` from GetLiveLeagueGames (`discovery.py:363-372`, `feed_selection.py:154-155`) against `MAX_FEED_DELAY_SECONDS = 61` (`source_picker.py:32`).

Evidence: 657 local `match.json` files: feed_source grid 581, oddin 76, steam 0. `steam_delay_s` is null (330), 900 (291), or 300 (36). All present values fail the 61 s gate. Archive index Dota rows: grid 246, oddin 60, steam 0. No `state.jsonl` under `data/trader`, so GetRealtimeStats age was not measured. The client comment says that API is fresh every poll (`steam_client.py:17-21`); the picker never asks it that question.

Impact: none on current archives. If a league shows up with `stream_delay_s` ≤ 61, Steam wins the tie-break (`SOURCE_TIE_ORDER` puts Steam first) and the model would see a ~1 s state against a 10 s training pair, with a 3 s live stale timer the backtest would not reproduce.

## 4. Architecture / performance / debuggability

Ranked.

1. Three clocks, one constant named "lag". Training bakes 10 s into the join. grid-v1 acts at the market-second timestamp, which implements that 10. Live and archive schedules act at receipt and use whatever age the source actually has (GRID 8, Oddin 16). `model.json` `source_lag_seconds` only checks the constant. A manifest field `backtest_lag_seconds: 10` on an `auto` run (464 grid-v1 + 149 schedules) describes the grid-v1 slice only.
2. grid-v1's strategy clock is a boolean in disguise: `game_second` is 0 or 540, `paused` is false (`strategy.py:1044-1048`). The real second exists only inside the precomputed feature row. Pause bugs and cutoff bugs hide there.
3. Validation parquet `second` is the market second. Training parquet `second` is the state second. Both are named `second`. Every consumer must remember to subtract 10 on the validation frame (`train_model.py:75-79`, `signals.py:484`) and must not subtract on archive ticks. The sample check below is the kind of assert that should live next to the join.
4. `window_seconds` in results adds `PREHORN_LEAD_SECONDS` (50) to `game_ended_at - horn` (`results.py:213-215`). That 50 is `-(−60+10)`, i.e. the first market second, not a 50 s pre-horn. Easy to misread as coverage.
5. `datetime_to_ns` multiplies `timestamp()` by 1e9 in float (`match_time.py:29-31`). Microsecond dust, not a money bug.

## 5. Checked and OK

- Dota grid-v1 decision lag 0 is correct. Validation rows are the market row plus features from M−10 (`prepare_dataset.py:199-220`). Match 8837869969: joined seconds are a subset of market-cache seconds, joined min −50, market min −60 (the lag eats the first 10 market seconds). `signals.py:484` then sets the model second to M−10. Acting at `state_ts_us` of M is the training pair (state S, book at S+10). `0e843075` added `LOL_SOURCE_LAG_SECONDS` only for LoL, where the row is the frame time. Putting that add on Dota would double the lag.
- Label horizon is 300 wall seconds after the market observation, not after the state. `lookup_market_p_after` (`telonex_book.py:268-279`) anchors on the market row's `state_ts_us`. Training attaches that label from the S+10 row (`prepare_dataset.py:151-163`). `find_asof_quote` is `bisect_right − 1` (`telonex_book.py:210-218`): features and the decision book never read a later snapshot. The label is not a feature column.
- `radiant_win` is on the dataset row and is not in `FEATURE_COLUMNS` (`gbm.py:37-51`). Archive `GameFeatureRow` omits it (`dataset.py:78-87`).
- Archive decisions price the book at `tick.received_ns` (`signals.py:562`) and overwrite snapshot columns from the tick the reducer stored (`signals.py:642-643`). That is the live input, including GRID's `clock - feed_delay`.
- Split instant `VALIDATION_START_TIME = 1780563592` is 2026-06-04T08:59:52Z. `start_time` is spawn if known, else horn (`match_catalog.py:67`). Zero catalog maps within 120 s of the cut have spawn and horn on opposite sides of it.
- Live GRID horn uses `occurredAt - clock_seconds`, not the extrapolated clock (`grid_feed.py:113-115`). Oddin transport lag is stable (p99 15.65 s across 25 maps) even when one map's frozen horn drifts.
- Kill-gate and mid-spike share `src/strategy/` and key off `now_ns`. Archive kill gates replay the live receipt stamps (`signals.py:621-636`). grid-v1 places the board at `state_ts - 10s + drawn scoreboard delay` (`signals.py:364-398`), which matches the comment that Dota rows are stamped at the market second. No forward join found there.
- Steam is not a current timing leak: the picker rejects the delays these archives actually carry (300 s and 900 s).
- Live GRID/Steam `second` is the scoreboard or `game_time`, not `horn_at_utc`. After a pre-horn pause the clock matches a GRID-derived horn. The early stamp is the archived horn only (F10). N4's open question on "is live `second` off by D" is no.
- Schedule-mode `market_p` at `received_ns` is an as-of of a wall-clock sample. The shifted horn does not move that lookup. It moves the join, the labels, the replay window, and `ended_at`.

## 6. Open questions / Needs from VPS

- Name the Oddin archive whose feature-age median is hundreds of seconds (horn frozen, later `gameTime` diverges) and check whether that map was admitted. Transport lag on it is still ~16 s, so the model second, not the book time, is the broken one. Separate from F10: that drift is `lastUpdatedAt - gameTime` frozen on the first positive tick.
- F10 row counts and the +62.5 s kill-response peak are the orchestrator's event study. This pass confirmed the code path and the `grid-3006669-m1` clock dump they cite. Not re-run here.
- The 2 Oddin maps inside the 58: Oddin already refuses `second <= 0`. Worth seeing whether those two pinned on a positive clock that was still inside the pause.
- F6 versus the 39/39 settlement tail: distribution of predicted fair minus touch after second 540 on the seed0 positions that were held. N1 says SELLs still fill after second 645, and grid-v1 holds 31 maps versus 4 on GRID schedules. Consistent with F3 (no pause, no feed gaps) plus F6. Not scored here.
- No VPS request. Local `match.json` already says the recorded feeds are GRID and Oddin. Steam `state.jsonl` is absent locally, so GetRealtimeStats age is unmeasured; the picker is not using Steam on this corpus.

Numbers: `work/time-grok/measure_out.json`, script `work/time-grok/measure.py`.
