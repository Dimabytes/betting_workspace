# time-grok-lag — edge at the real per-source lag
Status: FINAL

Offline cost of serving the published models with STRATZ state that is L seconds older than the market second, on the validation buy window. No training. Models read from `data/new_model/research` and `data/new_model/production-noxp`. Scratch: `work/time-grok/score_lags.py`, `lag_extra.py`, `lag_out.json`, `lag_extra.json`.

## Method

Validation rows are market second M (`validation_dataset.parquet`). Kept rows with `0 ≤ M < 480` (buy cutoff is `BUY_CUTOFF_SECOND = 480`, so the window stops before 480), `market_status == ok`, and a non-null `signal_market_p_radiant_300s`. That is 255,282 rows and 310 event series. Every lag aligned all 255,282 rows: `game_features.parquet` has a state row at M−L for each of them (L up to 30, so the oldest state second is −30).

For each L the feature `second` is set to M−L, the same assignment grid-v1 makes in `src/backtest/signals.py:484` (`second=model_rows["second"] - lag_seconds`). Net-worth, deaths, top-1, and `market_radiant_prior` come from the game-features row at that second. `market_p_radiant` and the 300 s label stay on the validation row at M. production-noxp drops `radiant_xp_adv`.

Fair price is `clip(market_p + Δ̂, 0, 1)`, the same restore as `predict_future_prices` (`src/shared/utils/gbm.py:157-164`). Metrics follow `market_metrics.build_market_comparison` and `market_scenario_report.aggregate_scenario_bucket`: MAE gain is mean(|future−current| − |future−model|), bias is mean(model−future), directional markout is sign(model≥market) × (future−current). The entry gate is the raw delta, `|Δ̂| ≥ 0.02` (`MIN_ABS_DELTA`). Event-series CIs are `bootstrap_series_cluster_ci`: 2,000 replicates, seed 20260810, clusters are `event_id`. Figures below are in cents (1c = 0.01 probability).

## Lag × model

All 255,282 rows. CI is the 2.5/97.5 event-series interval.

| L (s) | model | MAE gain (c) | gain CI | dir. markout 300 (c) | markout CI | bias (c) | \|Δ̂\|≥2c |
| ---: | --- | ---: | --- | ---: | --- | ---: | --- |
| 0 | research | 0.311 | [0.204, 0.418] | 1.989 | [1.561, 2.432] | +0.089 | 41.98% [39.58, 44.30] |
| 0 | production-noxp | 0.459 | [0.360, 0.560] | 2.504 | [2.081, 2.936] | −0.078 | 39.32% [36.82, 41.89] |
| 2 | research | 0.301 | [0.195, 0.408] | 1.957 | [1.525, 2.405] | +0.086 | 41.74% [39.34, 44.05] |
| 2 | production-noxp | 0.451 | [0.352, 0.551] | 2.469 | [2.042, 2.900] | −0.079 | 39.11% [36.62, 41.68] |
| 5 | research | 0.288 | [0.182, 0.394] | 1.915 | [1.485, 2.360] | +0.082 | 41.37% [39.00, 43.66] |
| 5 | production-noxp | 0.439 | [0.341, 0.539] | 2.425 | [1.993, 2.856] | −0.081 | 38.82% [36.33, 41.37] |
| 8 | research | 0.277 | [0.172, 0.383] | 1.877 | [1.449, 2.323] | +0.079 | 41.04% [38.68, 43.30] |
| 8 | production-noxp | 0.430 | [0.332, 0.530] | 2.390 | [1.956, 2.823] | −0.084 | 38.55% [36.09, 41.10] |
| 10 | research | 0.271 | [0.167, 0.376] | 1.850 | [1.424, 2.298] | +0.076 | 40.78% [38.44, 43.06] |
| 10 | production-noxp | 0.425 | [0.327, 0.525] | 2.372 | [1.940, 2.801] | −0.085 | 38.37% [35.93, 40.90] |
| 12 | research | 0.266 | [0.161, 0.370] | 1.830 | [1.398, 2.279] | +0.074 | 40.53% [38.19, 42.78] |
| 12 | production-noxp | 0.420 | [0.322, 0.519] | 2.355 | [1.922, 2.787] | −0.087 | 38.17% [35.73, 40.67] |
| 16 | research | 0.255 | [0.151, 0.359] | 1.780 | [1.346, 2.232] | +0.068 | 40.04% [37.72, 42.25] |
| 16 | production-noxp | 0.409 | [0.312, 0.509] | 2.320 | [1.887, 2.756] | −0.091 | 37.77% [35.35, 40.25] |
| 20 | research | 0.245 | [0.142, 0.350] | 1.721 | [1.286, 2.174] | +0.063 | 39.58% [37.30, 41.78] |
| 20 | production-noxp | 0.400 | [0.303, 0.499] | 2.282 | [1.848, 2.717] | −0.094 | 37.32% [34.94, 39.78] |
| 30 | research | 0.225 | [0.122, 0.329] | 1.616 | [1.188, 2.066] | +0.052 | 38.48% [36.23, 40.61] |
| 30 | production-noxp | 0.376 | [0.282, 0.474] | 2.182 | [1.748, 2.617] | −0.101 | 36.27% [33.89, 38.71] |

Level CIs are wide and overlap across lags. The paired difference on the same rows does not. Event-series CI of (metric at L − metric at L=10), all 255,282 rows:

| model | contrast | MAE gain (c) | gain CI | dir. markout (c) | markout CI |
| --- | --- | ---: | --- | ---: | --- |
| research | L=8 − L=10 | +0.0059 | [+0.0047, +0.0070] | +0.0264 | [+0.0191, +0.0340] |
| research | L=16 − L=10 | −0.0167 | [−0.0199, −0.0135] | −0.0704 | [−0.0889, −0.0526] |
| production-noxp | L=8 − L=10 | +0.0052 | [+0.0042, +0.0063] | +0.0178 | [+0.0097, +0.0263] |
| production-noxp | L=16 − L=10 | −0.0155 | [−0.0183, −0.0127] | −0.0527 | [−0.0742, −0.0331] |

From the training lag out to 30 s, research MAE gain falls from 0.271c to 0.225c and markout from 1.850c to 1.616c. The slope is smooth. A 6 s miss versus the training lag is about 0.02c of MAE gain.

## Entry rows at L=10

The 300 s label does not move with L. On the rows where `|Δ̂| ≥ 2c` at L=10, “move left” is the directional markout of that same future−current when the side and the delta are recomputed at L=8 and L=16.

Research, 104,107 rows, 306 events. Mean absolute realized move 8.086c.

| L | MAE gain (c) | dir. markout (c) | markout CI | side agrees with L=10 | still \|Δ̂\|≥2c |
| ---: | ---: | ---: | --- | ---: | ---: |
| 8 | 0.577 | 3.011 | [2.300, 3.722] | 99.96% | 94.72% |
| 10 | 0.573 | 3.011 | [2.300, 3.721] | 100% | 100% |
| 16 | 0.554 | 3.003 | [2.296, 3.706] | 99.73% | 88.27% |

production-noxp, 97,953 rows, 301 events. Mean absolute realized move 8.344c.

| L | MAE gain (c) | dir. markout (c) | markout CI | side agrees with L=10 | still \|Δ̂\|≥2c |
| ---: | ---: | ---: | --- | ---: | ---: |
| 8 | 0.949 | 3.911 | [3.183, 4.558] | 99.97% | 94.97% |
| 10 | 0.946 | 3.909 | [3.181, 4.555] | 100% | 100% |
| 16 | 0.926 | 3.908 | [3.180, 4.554] | 99.78% | 89.31% |

At L=16 the research entry set keeps 99.7% of its L=10 directional markout (3.003c of 3.011c). The noxp entry set keeps 99.98% (3.908c of 3.909c). The side flips on 0.27% of research entry rows and 0.22% of noxp entry rows. What does change is the gate: 11.7% of research entries and 10.7% of noxp entries fall below 2c when the state is 16 s old instead of 10 s. At 8 s, 5.3% and 5.0% fall below the gate, and the markout is unchanged.

## The Oddin archive with feature age in the hundreds

Local replay of 73 Oddin archives through the current `OddinSnapshotReducer`. Feature age is `received_at − (horn_unix + game_second)` on unpaused ticks with `0 < second ≤ 540`. Transport is `received_at − server_timestamp`. Per-map median feature age has p50 15.86 s.

The map in the hundreds is archive `9007618656` (match id 9007618656): median feature age 587.70 s (p90 755.3 s, n=921), median transport 15.59 s. Two smaller local outliers were not indexed: `9014106505` median 143.1 s, `9015175653` median 64.8 s.

`9007618656` is admitted in `data/archive_index/index.parquet` (`admission=admitted`, `feed_source=oddin`, `delay_s=26`, `delay_evidence=meta`). `match.json` `oddin_delay_s` is 26. LIVE seed0 includes it: `signal_mode=schedule`, `feed_source=oddin`, `model_name=research-noxp`, engine PnL +$11.83.

The 588 s is the in-game pause sitting in a pause-unaware age. The reducer freezes the horn at the first `gameTime > 0` (`oddin_feed.py:383-384`), which on this file is second 1 at `2026-09-20T07:19:05Z`, `mapPaused=false`. Catalog pauses after that are (101, 100) and (109, 471). Transport 15.59 + 100 + 471 = 586.6 s, against the measured median 587.7 s. Ticks after second 109 carry that pause wall time because age uses `horn + game_second` and the game clock does not advance while paused.

The stored catalog horn is a different, earlier pin. `match.json` `horn_at_utc` is `2026-09-20T07:05:28Z`. That equals `int(lastUpdatedAt) − gameTime` on a `gameTime=-90` row whose `lastUpdatedAt` is `2026-09-20 07:03:58` (`07:03:58 + 90 s`). The clock then stays at −90, with `mapPaused=false`, until `07:17:05`, and reaches `gameTime=1` only at `07:19:06` (horn estimate `07:19:05`, 817 s after the stored horn). `horn_is_pinnable` (`live_feed.py:102-111`) refuses Oddin until `second > 0` so a fresh replay does not keep `07:05:28`. The catalog kept the −90 pin anyway: this map is one of the two Oddin rows in the 58 archive-horn maps with a pre-horn pause (D=32 s from pause `(−77, 32)`).

## The two Oddin maps in the 58

Catalog `horn_source=archive` and `get_paused_seconds_before(pauses, 0) > 0` is 58 maps: 56 GRID, 2 Oddin. Both Oddin maps are admitted and both are in LIVE seed0 as schedule / oddin / research-noxp.

| archive | pre-horn pause | stored horn | pin tick | paused at pin | LIVE engine PnL |
| --- | ---: | --- | --- | --- | ---: |
| 9007618656 | 32 s `(−77, 32)` | `2026-09-20T07:05:28Z` | `gameTime=-90`, lastUpdated `07:03:58` | false | +$11.83 |
| 9008125103 | 35 s `(−58, 35)` | `2026-09-20T13:32:28Z` | `gameTime=1`, lastUpdated `13:32:29` | false | −$17.26 |

`9008125103` stored horn matches the first positive clock exactly (`int(lastUpdatedAt) − 1 = 13:32:28`). `mapPaused` is false. The pre-horn pause freezes the clock at −58, and the clock has already reached 0 (`13:32:26`, paused false) before this pin. The first paused positive tick is second 428. Median feature age on the current reducer is 15.33 s, transport 15.63 s.

`9007618656` was pinned while the clock was still −90, not on a positive second. The first positive tick (second 1, `07:19:06`) is also `mapPaused=false`, and that second is not a catalog pause start. Neither map pinned on a positive clock inside a pause.

## Verdict

On this buy window the published noxp catalog still has 0.409c MAE gain and 2.32c directional markout at a 16 s state lag, against 0.425c and 2.37c at the 10 s lag it was built with, and the rows it would enter at 10 s keep 99.98% of that entry markout at 16 s (3.908c of 3.909c) with 89% still clearing the 2c gate; the research catalog at a GRID-like 8 s is 0.006c of MAE gain and 0.026c of markout ahead of its own 10 s lag, and its entry markout is unchanged (3.011c). The paired event-series intervals on those gaps exclude zero, and the gaps are a few hundredths of a cent per row, so the live Oddin clip of $200 and the GRID clip of $60 are not a response to this lag: six extra seconds of state age removes about 0.016c of MAE gain and about 0.05–0.07c of unconditional markout, and it leaves the realized 300 s move on the actual entry rows in place. The one Oddin archive whose feature age is hundreds of seconds, `9007618656`, was admitted (`delay_s` 26) and is in the LIVE backtest; that 588 s median is the 100 s and 471 s in-game pauses inside `received − (horn + game_second)`, on top of a 15.6 s transport, and its catalog horn is the earlier −90 pin at `07:05:28Z`.
