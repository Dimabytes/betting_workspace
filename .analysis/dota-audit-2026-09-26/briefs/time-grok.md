# Brief: time-grok — timing and look-ahead across dataset, backtest, live

Report: `$R/reports/time-grok.md`. Work dir: `$R/work/time-grok/`.

## Deliverable 1: timing table

For each stage and source give the timestamp base and the offset:

- training rows: state second S, market S+10, label at which time;
- validation rows: market second M, state M−10;
- backtest decision time for `grid-v1` (`src/backtest/signals.py:473` sets decision lag 0 for Dota) and for
  each archive schedule (Steam, GRID, Oddin): receive time vs frame game second;
- live: frame receive time, frame game time, how `second` is computed per source (e.g. `grid_feed.py`:
  `second = clock + age − feed_delay`), which book the model sees (receive time).

Where cheap, measure the real delay per live source on local archives (`$E/data/trader/*`): frame game clock vs
wall clock, using horn anchors or GRID `occurredAt`. Is 10 s right for Steam, GRID, and Oddin (1 s cadence)?
If Oddin's real delay is ~2 s and a path uses 10 s, the backtest is pessimistic there; if a path uses 0, it is
optimistic.

## Questions

1. **Look-ahead anywhere.** Features or market values from later than the decision time; labels that overlap
   the decision; forward "nearest" joins; game end, horn, or market close from the future used in decisions or
   gates; `radiant_win` or post-game data leaking into features or gates; a prior built from in-game prices.
2. **`second` feature.** Train: state second S. Validation rows: which second is stored? The backtest replaces
   it with `second − lag` (`signals.py:484`). Live: which second goes into the model? Check consistency.
3. **Model window.** Training −60..540 by minute; live runs the model on 0..599 (`outside_window` > 599) and
   keeps using the fair after 600 for SELL targets. Does the backtest do the same? Prehorn rows (−60): does
   live use the model before horn (draft)? `PREHORN_LEAD_SECONDS`.
4. **Pauses.** Training/validation clocks vs live clocks during pauses.
5. **Gates.** Kill-gate and `mid_spike` timing (board age etc.) in the backtest vs live.
6. **Units and zones.** ms / us / ns; UTC vs local; which `start_time` drives the `VALIDATION_START_TIME`
   split (horn? scheduled start?) and whether any maps land on the wrong side.

Scope: `src/prepare_dataset/*`, `src/market_data/build_market_data.py`, `src/backtest/{signals.py,
feed_schedules.py, replay_inputs.py, run.py}`, `src/archive_index/*`, `src/trader/{steam_feed.py,
grid_feed.py, oddin_feed.py, oddin_live_feed.py, live_feed.py, session_quoting.py, session_core.py,
match_worker.py, market_prior.py}`, `src/strategy/{kill_gate.py, mid_spike.py, scheduling.py}`,
`src/shared/utils/match_time.py`, `src/shared/constants/*`.
