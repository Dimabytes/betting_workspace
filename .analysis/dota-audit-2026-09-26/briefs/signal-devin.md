# Brief: signal-devin — backtest inputs, signal construction, map selection, market cache

Report: `$R/reports/signal-devin.md`. Work dir: `$R/work/signal-devin/`.

## Scope (read line by line)

- `src/backtest/{signals.py, feed_schedules.py, replay_inputs.py, live_archives.py, selection.py, context.py,
  telonex_local.py, seed0_replay.py, extraction_identity.py}`; the input/selection parts of
  `src/backtest/run.py`; skim `series_inputs.py` and `lol_inputs.py` for shared helpers only.
- `src/archive_index/*` (schedules from live archives).
- `src/market_data/build_market_data.py`, `src/shared/utils/{telonex_book.py, telonex_capture.py,
  engine_cadence.py}`.
- Data: `data/new_processed/{dataset, market_seconds}`, the LIVE catalog manifests
  (`data/backtests/dota_maker/LIVE/seed*/manifest.json`), `data/archive_index` if present.

## Questions

1. **What the model sees.** For each Dota signal source under `signal_source=auto` (synthetic `grid-v1` and
   the archive schedules for Steam, GRID, Oddin), trace exactly: at backtest time T, which game second's
   features the model gets, which `market_p_radiant` it gets and from which timestamp, which prior. Compare with
   live: the latest frame (arriving with the source delay) plus the current book at receive time. Find any
   place where the backtest's `market_p_radiant` comes from later than the decision, or where features are
   fresher than live could have them.
2. **Cadence and staleness.** How `signal_cadence_seed` / `engine_cadence.py` simulate live cadence and gaps
   for `grid-v1`. Is `max_signal_age_seconds=16` applied as in live? Are feed gaps modeled?
3. **Archive schedule path.** It uses live receive times but STRATZ `game_features.parquet` values at the
   frame's game second. Is the feed-second → STRATZ-second mapping right (clock offsets, pauses, Oddin/Steam
   clock semantics)? What happens with frames before horn or after the STRATZ data ends? Are
   `archive_exclusions` (e.g. `schedule_identity_mismatch`, `no_window_updates`) biased?
4. **Map selection.** Which validation maps enter the LIVE backtest (613) and which are excluded, by which
   rule: book gap over the whole map (`find_longest_book_gap` over seconds −60..900 in
   `prepare_dataset.py`), missing market cache, `playback_available`, archive exclusions. Count per rule. Does
   a rule use information from after the decision time?
5. **Market cache.** In `build_market_data.py` / `telonex_book.py`: how the per-second mid is built (last
   snapshot at or before the second, or after?), quality flags (`is_ok_market_second`, stale, one-sided,
   crossed, spread), the label `signal_market_p_radiant_300s` (+300 from which base; does it skip bad seconds
   or take the nearest later second?), the timestamp base (exchange time vs local receive time; paid Telonex
   vs our collector). Look for off-by-one-second errors and "nearest" joins that peek forward.
6. **Rows through map end.** Validation rows run through map duration "so backtest FV can roll until game
   end". What happens after the book or the game ends? Is any future-known time (game end, market close)
   used to stop or gate something that live cannot know?
7. **Cache identity.** `CACHE_VERSION`, identity hashes, `extraction_identity.py`: can a backtest silently use
   a stale market cache or a stale `game_features.parquet` built by older code? Compare current file hashes
   with the LIVE manifest (`game_features_sha256`, `validation_dataset_sha256`).
8. **Performance.** Time per map spent on input loading; redundant work.

## Allowed

Read-only data scripts. You may import backtest modules to rebuild the signal rows for 2–3 maps and print them
next to the live `session.jsonl` / `core_trace` signal rows for the same maps. No full backtest.
