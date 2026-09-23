# On live LoL inputs, the post-332 model is not worse than the pre-332 model

**verified.** Same GRID ticks, same live book, several catalogs. Current production (`20260921T095813Z`) is slightly ahead of the last pre-332 production catalog (`20260915T210431Z`) on correlation and on the 2¢ gate. The 3¢ slice is the one place pre-332 production is ahead, by about 0.4¢. Research catalogs are tied. The O7 result (0915 negative, 0921 positive) came from scoring each model only on the maps it traded.

Primary panel: live `market_radiant_prior`, realized move = radiant mid 300 wall-seconds after the GRID receipt. Seconds 0..480, in progress, not paused. **290 maps, 20,641 rows.**

| catalog | corr | mean sign(pred)·move, \|pred\|≥2¢ | n at 2¢ | mean sign(pred)·move, \|pred\|≥3¢ | n at 3¢ | share \|pred\|≥2¢ |
|---|---:|---:|---:|---:|---:|---:|
| pre-332 research `20260915T210420Z` | 0.107 | +1.80¢ | 6,657 | +1.66¢ | 3,556 | 0.323 |
| pre-332 production `20260915T210431Z` | 0.146 | +2.56¢ | 6,905 | +3.51¢ | 3,162 | 0.335 |
| post-332 research `20260919T112924Z` | 0.106 | +1.80¢ | 7,940 | +1.78¢ | 4,609 | 0.385 |
| post-332 production `20260919T112946Z` | 0.148 | +2.54¢ | 8,402 | +3.07¢ | 4,610 | 0.407 |
| current research `20260921T095801Z` | 0.106 | +1.78¢ | 8,004 | +1.77¢ | 4,646 | 0.388 |
| current production `20260921T095813Z` | 0.154 | +2.75¢ | 8,392 | +3.14¢ | 4,581 | 0.407 |

Current research member files are byte-identical to archive `research/20260921T050646Z`. Current production member files are byte-identical to archive `production/20260921T050658Z`. The 09:58 names are a later stamp on the 05:06 weights. `20260919T112924Z` / `20260919T112946Z` are a different fit: those are the catalogs `c9fb5cd2` committed (`git show c9fb5cd2:data/lol/models/production/model.json` name `20260919T112946Z`). `332e1c17` (2026-09-19 12:04 +0200) is the prepare change; `c9fb5cd2` (13:31 +0200) is the retrain.

Post-332 production opens the 2¢ gate on 40.7% of ticks, pre-332 production on 33.5%. The extra ticks still have a positive conditional edge.

## Training-style prior, same ticks

Prior replaced by `lookup_strict_prior` (`src/lol/05_prepare_dataset.py:180`) on the book in `[horn−61s, horn)`, spawn = `match.json` `horn_at_utc` in microseconds. **267 maps, 18,955 rows** (268 of 293 proved maps had a two-sided book; one of those had no future mid).

| catalog | corr | ≥2¢ edge | n at 2¢ | ≥3¢ edge | n at 3¢ | share ≥2¢ |
|---|---:|---:|---:|---:|---:|---:|
| pre-332 research | 0.127 | +2.30¢ | 5,819 | +2.29¢ | 3,217 | 0.307 |
| pre-332 production | 0.199 | +3.60¢ | 6,121 | +4.97¢ | 2,910 | 0.323 |
| post-332 research `0919` | 0.132 | +2.50¢ | 6,898 | +2.49¢ | 4,020 | 0.364 |
| post-332 production `0919` | 0.205 | +3.58¢ | 7,421 | +4.38¢ | 4,099 | 0.392 |
| current research | 0.132 | +2.51¢ | 6,941 | +2.53¢ | 4,062 | 0.366 |
| current production | 0.213 | +3.83¢ | 7,401 | +4.87¢ | 3,949 | 0.390 |

Every catalog looks better with the spawn−1 prior. The ranking does not flip. Current production stays ahead of pre-332 production on corr (0.213 vs 0.199) and the 2¢ edge (+3.83¢ vs +3.60¢). The 3¢ gap shrinks to 0.10¢ (+4.87¢ vs +4.97¢).

On the 268 proved maps with both priors, live prior minus horn prior: median 0, mean −0.07¢, median absolute difference 1.5¢, 118 maps differ by ≥2¢, 38 by ≥5¢. The live prior is the session field, latched at horn − 90s (`src/trader/match_worker.py:508`, `HORN_OFFSET_SECONDS = 90` in `src/shared/utils/match_time.py:9`, fetched by `src/trader/market_prior.py:15`). The horn used here is the GRID horn second, not the livestats spawn. An earlier check on two maps put those spawns within about 0.6s (`reports/clock-grok.md`). **likely** that 0.6s rarely moves the last two-sided mid; it is not the livestats spawn the trainer uses.

## Game-second realized move

Same rows, future mid = first in-progress signal with game second ≥ s+300 and within 10 game-seconds (the O7 definition). Live prior: **290 maps, 19,728 rows.**

| catalog | corr | ≥2¢ edge | ≥3¢ edge | share ≥2¢ |
|---|---:|---:|---:|---:|
| pre-332 production | 0.145 | +2.61¢ | +3.59¢ | 0.334 |
| post-332 production `0919` | 0.147 | +2.54¢ | +3.10¢ | 0.406 |
| current production | 0.153 | +2.78¢ | +3.16¢ | 0.405 |
| pre-332 research | 0.110 | +1.94¢ | +1.65¢ | 0.322 |
| current research | 0.108 | +1.90¢ | +1.85¢ | 0.386 |

The ranking matches the wall-clock panel.

## Why O7 looked different

`work/orchestrator/live_calibration_by_model.py` scores the logged delta of whatever model was in `session_start`, on the maps that model traded. LoL 0915: corr −0.137, 2¢ edge −3.7¢, 16 maps, 1.3k rows. LoL 0921: corr 0.052, +1.05¢, 22 maps, 1.5k rows (`notes.md` O7).

Those are different maps. Rescoring both catalogs on the maps whose session model is already post-332 (`20260919T112946Z` or `20260921T095813Z`), live prior, wall +300: **35 maps, 2,551 rows.**

| catalog on post-332 maps | corr | ≥2¢ edge | ≥3¢ edge |
|---|---:|---:|---:|
| pre-332 production | 0.074 | +1.28¢ | +3.29¢ |
| current production | 0.122 | +2.32¢ | +3.08¢ |
| pre-332 research | 0.084 | +0.09¢ | +1.53¢ |
| current research | 0.138 | +1.87¢ | +2.45¢ |

On the pre-332 session maps (255 maps, 18,090 rows) current production is corr 0.157 / +2.79¢ at 2¢ against pre-332 production 0.152 / +2.67¢. The negative 0915 number in O7 does not survive once 0915's weights are applied to the same ticks as the later catalogs.

## Reproduction

Command:

```
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader && PYTHONPATH=src uv run python \
  /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok/live_model_score.py
```

Output: `work/clock-grok/live_model_score.json`. Printed `proved 293`, `prior 268`, the block lines quoted above. Runtime about 74s.

Local LoL trader dirs: 305. 7 have no `session.jsonl`, 2 archives emit no paired rows. 296 sessions, all `execution_mode=live` (no paper tapes in this tree).

Rebuild: `replay_grid_records` + `GridFrameReducer` + `GAME_PROFILES["lol"]` (`src/trader/grid_feed.py:188`, `:290`). Live `second` is `live_clock_seconds(board, age) - table.feed_delay` (`grid_feed.py:171-172`). LoL top ratios are top1 / team total (`src/shared/utils/top_players.py:63`). Features are `FEATURE_COLUMNS` (`src/shared/utils/gbm.py:37-50`). `market_p_radiant` and `market_radiant_prior` come from the session signal aligned to that tick. Session signals have no wall timestamp (`src/trader/session_journal.py:260-282`); alignment is the second sequence.

224 maps: the signal seconds are an exact contiguous window of the replayed event seconds. 72 maps: greedy pair (session longer than the archive, often about 2×, consistent with a resumed journal). A map is proved when ≥20 model rows, median abs error ≤ 1e-4, and ≥90% of model rows within 1e-4. Scoring then keeps only rows with abs error ≤ 1e-4.

293 maps proved. 156 of those have max abs error 0. 53 have a few mismatched rows (minimum exact fraction 0.972); those rows are dropped. 3 maps have zero `reason=model` rows (`grid-2965526-m1`, `grid-2965526-m2`, `grid-2968611-m1`: almost every signal is `missing_book`). Ensembles load with `load_predictor`. The two pre-ensemble production files (`20260831T120859Z`, `20260904T193238Z`, single `model.txt`) load as one booster with `num_threads=1`.

The check is clipped fair minus `market_p_radiant` against `radiant_fair - market_p_radiant`. Live stores the clipped fair (`src/trader/model_server.py:168-178`). 2,746 of 115,868 model rows have raw delta and session delta apart by >1e-4, which is the clip to 0 or 1. The metrics above use the raw delta, which is what the worker writes as `predicted_delta` (`src/trader/match_worker.py:426`).

Realized mid for the wall panel: first later in-progress tick with a finite `market_p_radiant` whose GRID `received_at_utc` is ≥ this tick's receipt + 300s and within 15s. Rows with no such tick are dropped. Paused ticks are dropped so a frozen second does not dominate.

## Limits

Rows are repeated ticks on 290 maps, so a corr gap of 0.008 is a small shift, not a new edge. **verified** that the post-332 catalogs are not worse on this sample. **speculative** to call current production meaningfully better.

The horn prior is the GRID horn, not `LivestatsOk.spawn_us` (`src/lol/livestats_frames.py:236`). Telonex was read per token for `[horn−61s, horn)` only, via `load_token_book`.
