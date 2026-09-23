# Brief: clock-devin — timing audit from code (train vs backtest vs live)

Your name: `clock-devin`. Report: `$R/reports/clock-devin.md`. Work dir: `$R/work/clock-devin/`.

Goal: state exactly what information each LoL training row, each LoL backtest signal,
and each LoL live signal has, on one common wall clock. Any look-ahead (data from after
the moment live could act) or look-behind (live sees older state than training assumes)
is a finding.

1. Training rows (`src/lol/05_prepare_dataset.py` `join_market_rows`, `src/lol/livestats_frames.py`):
   define `second`, `state_wall_us`, spawn detection (`find_spawn_index`, `is_spawn_sides`,
   `loading_anchor_ts`), pause handling (`assign_game_times`, `wall_us_for_second`),
   `select_grid_rows` (latest frame with game_time <= S, age gate).
   What does `lookup_market_p_after` return exactly (first book at/after, last at/before,
   freshness rule)? Current mid join (as-of 0 since `332e1c17`), label join (+300 s),
   prior window. Any path where current mid, prior, or features use data later than
   `state_wall_us` + the real live delay?
2. Backtest (`src/backtest/lol_inputs.py`, `replay_inputs.py`, `feed_schedules.py`, `signals.py`,
   `run.py` LoL path, `src/lol/replay.py`, `src/archive_index/schedule.py`): for a map
   WITHOUT a live archive (most of the 945 validation maps), at what wall time does the
   strategy get the features of game second s? How are `source_lag_seconds=10`,
   `max_signal_age_seconds=16`, grid-v1 cadence, `signal_cadence_seed`, `LOL_REPLAY_LEAD` used?
   Same question for a map WITH a live archive schedule (feed-schedule-v3 / "playback").
   Which book does the model get as `market_p_radiant` in the backtest: at signal arrival
   time or at state time?
3. Live (`src/trader/grid_feed.py`, `grid_live_feed.py`, `grid_widgets.py`, `match_worker.py`,
   `model_server.py`, `market_prior.py`): which `second` the live model gets (GRID
   `currentSeconds`? `occurredAt`? any offset or interpolation?), which book (current MDS
   at compute time?), and the path latency from a GRID frame to the model call.
4. One timeline: for a game event at true wall time W, give the wall time of (a) the feature
   state, (b) the market mid fed to the model, (c) the label start, in training, backtest,
   and live. Show the offsets in seconds. Decide whether `332e1c17` moved training toward
   live or away from live. Compare with Dota training (`src/prepare_dataset/prepare_dataset.py`,
   `stratz_seconds.py`, shared utils): which lag Dota uses for the current mid and label.
5. `second` alignment: is LoL second 0 the same physical moment in livestats (spawn-shaped
   frame) and in GRID (`currentSeconds` = 0)? A constant offset shifts the `second` feature
   and the net-worth curve.
6. `market_radiant_prior`: training definition and time window vs live definition and time.
