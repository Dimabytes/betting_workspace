# clock-grok — LoL look-ahead audit

Date: 2026-09-23. Read-only. No VPS, no training, no backtest run.

Question: which wall clock is the one live can actually trade, and how much of the LoL backtest’s profit is an earlier clock than that.

## TL;DR

1. **The published LoL backtest mostly trades at the livestats frame, ~8–11 s before live can act.** 782 of 945 maps are `grid_v1` and account for $4684 of the $5171 pre-rebate. Their quantity-weighted BUY markout at 30 s is **+0.62¢**. That is the +0.51¢ red flag.
2. **Live reads the book at GRID receipt, not at the frame.** Receipt is ~8 s after the game second the snapshot names, and ~10–11 s after a kill’s `rfc460Timestamp`. Training since `332e1c17` joins the current mid at the frame (as-of 0). The old as-of +10 s join was the one that matched live.
3. **Archive-schedule replay does not look ahead.** On 20,682 ticks the median receipt-minus-livestats-wall is **+7.92 s** (4 ticks earlier than −2 s). Those 163 maps have BUY markout **+0.09¢ at 30 s** and **−0.52¢ at 4 s**. BUY markout at 300 s is still **+2.21¢**, so the longer edge is not the 8 s reaction.
4. **A kill’s price move is mostly inside that 8–10 s.** On 48,941 one-sided kill seconds the signed mid move is mean **+3.86¢ by 8 s** and **+4.13¢ by 10 s**, out of **+5.03¢ by 30 s** (medians +2¢, +2¢, +3¢). Two live maps show the same shape, kill by kill.
5. **Live’s prior is the minute print before horn−90 s. Training’s prior is the book in the last second before spawn.** On the two maps that gap is 2.5¢ and 5¢. `market_radiant_prior` is 27% of research-model gain. This is a stale feature, not a future print.
6. **`grid_v1` also feeds `second − 10`.** LoL rows already store the state second, so the subtract that is correct for Dota lands 10 s early. `second` is 0.8% of gain. Schedule and live do not subtract.
7. **Spawn clocks agree.** Median GRID horn minus livestats spawn is **−0.61 s**. At a kill, GRID’s second is ~2 s ahead of that kill’s livestats game time; the tick still arrives ~8 s after the livestats wall of the second it names.

## Findings

### F1 — grid-v1 signals fire at the livestats frame

- Stage: backtest
- Severity: high
- Confidence: verified

**Claim.** For maps with no admitted GRID archive, the LoL backtest’s decision time is `state_ts_us` of the prepare row, which is the `rfc460Timestamp` of the chosen livestats frame. Live cannot act until the GRID table arrives, ~8 s after the wall of that second and ~10–11 s after a kill.

**Evidence.**

`build_match_signals` stamps the signal at the row’s `state_ts_us` and then subtracts `lag_seconds` from `second` only:

```260:271:src/backtest/signals.py
        timestamps_ns = [int(ts) * NS_PER_US for ts in ordered["state_ts_us"].to_list()]
        ...
        features = model_rows[feature_columns].assign(second=model_rows["second"] - lag_seconds)
```

The LoL row’s `state_ts_us` is the frame wall, not the frame plus a lag (`src/lol/livestats_frames.py` `select_grid_rows`, `state_wall_us=round(chosen.wall_seconds * 1_000_000)`; `src/lol/05_prepare_dataset.py` writes that into the row). Dota validation rows store the **market** second (`state.second + TRAIN_LAG_SECONDS` in `src/prepare_dataset/prepare_dataset.py`), so the same function’s timestamp is already the lagged book. LoL rows do not have that shift.

Seed0 of `validation_join_delta02_x015_cut480_p35_s06-playback-gf` labels the clock in `results.parquet` column `signal_mode`:

| signal_mode | maps | engine_pnl (pre-rebate) | mean / map | horn span |
|---|---:|---:|---:|---|
| grid_v1 | 782 | $4684.19 | $5.99 | 2026-06-04 .. 2026-09-17 |
| schedule | 163 | $486.88 | $2.99 | 2026-09-01 .. 2026-09-19 |
| all | 945 | $5171.07 | | |

`engine_pnl` sums to `summary.json` `pnl_before_rebate` ($5171.07). Net in that file is $6403.13. BUY fills are all maker.

Quantity-weighted markout, reconstructed from `market_seconds.parquet` and checked against stored `reference_30s` (error is 0 on all 5759 BUY fills):

| cohort | BUY n | qty | mk 0s | mk 4s | mk 8s | mk 10s | mk 30s | mk 300s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| grid_v1 | 4424 | 294,474 | +1.37¢ | +0.17¢ | +0.19¢ | +0.27¢ | **+0.62¢** | **+2.98¢** |
| schedule | 1335 | 73,088 | +1.28¢ | **−0.52¢** | **−0.51¢** | −0.41¢ | **+0.09¢** | **+2.21¢** |

The 0 s figure is the buy price sitting under the mid (about 1.3¢ on both clocks). After that the clocks diverge. Grid-v1 keeps the fill above water and is +0.62¢ at 30 s. Schedule gives the spread back within 4 s (adverse selection) and is flat at 30 s. Blended BUY 30 s is +0.513¢, matching `summary.json` `markout.buy_30s.estimate` 0.005131. Dollar BUY-30 s markout is $1823 on grid-v1 and $63 on schedule, so **about 97% of that dollar figure is the frame clock**.

Same calendar, so the era is not the whole story for the *markout*: grid-v1 maps with `horn_at >= 2026-09-01` are only 20 maps (the ones that did not get an admitted schedule). Their 80 BUY fills are still **+1.23¢ at 30 s** and +6.35¢ at 300 s, and those 20 maps’ engine PnL is −$14.51. Schedule over the same month is +0.09¢ at 30 s. The 20-map set is whatever failed archive admission, so it is a bad control for PnL and a fair control for “what does this code do to markout.”

**Mechanism.** The queue fill model rests the quote on the book at the signal time. On grid-v1 that time is the frame, which is before the kill print (F4). The fill is bought at the pre-move bid; the mid then steps toward the quote. That is favorable 30 s markout, which makers usually do not get. Dota’s grid-v1 timestamp is already `second + 10`, so the same fill model there shows the usual negative 30 s BUY markout (−0.38¢ in the shared context).

**Next check.** Rerun the 782 grid-v1 maps with the signal timestamp moved to `state_ts_us + 8 s` (or +10 s) and the `second` feature **not** reduced by 10. Do not change the schedule maps in that run. Expect BUY 30 s markout to fall toward the schedule number; do not expect BUY 300 s to disappear.

### F2 — as-of 0 is the wrong book for live; as-of ~8–10 s is the right one

- Stage: prepare / train
- Severity: high
- Confidence: verified

**Claim.** On one wall-clock line, the mid live can trade against a livestats frame is the book at **receipt of the GRID table that carries that frame’s state**, which is about 8 s after the frame and about 10 s after a kill. Joining the current mid at `state_wall_us + 0` pairs the kill with a price the bot does not see. Joining at `state_wall_us + 10` was the closer match.

**The clocks, from the code.**

Livestats frames have one stamp. On both hand maps the payload keys are only `blueTeam`, `gameState`, `redTeam`, `rfc460Timestamp`. `rfc460Timestamp` is parsed as UTC (`src/lol/04_fetch_lolesports.py` `parse_rfc460_seconds`) and becomes `state_wall_us`. There is no second game clock in the frame. The prepare grid picks the latest frame with `game_time <= S` and age ≤ 2 s, and stores **that frame’s wall**, not `S` plus a delay.

GRID live, for a table update at `now`:

```171:172:src/trader/grid_feed.py
    age = clock_age_seconds(board.occurred_at, now)
    second = live_clock_seconds(board, age) - table.feed_delay
```

`age` is `now − occurredAt`. `live_clock_seconds` adds that age onto `currentSeconds` while the clock is ticking (`src/trader/grid_widgets.py`). `table.feed_delay` is the socket’s declared `delay`. The book is then read from the in-memory CLOB at that same `now` (`src/trader/match_worker.py` `_gate_pair`, around the `engine.md.book` call). `received_at_utc` is the local receipt stamp written on the archive record (`src/trader/grid_feed.py` `received_at_utc`). `occurredAt` is “when GRID saw” the clock (`src/trader/grid_widget_types.py`).

So the snapshot’s `second` is a **label for the delayed table**, and the mid is the **book at receipt**. Those are not the same instant. `LOL_SOURCE_LAG_SECONDS = 10` is still written into `model.json` and checked by live (`src/trader/model_server.py` requires it equal `TRAIN_LAG_SECONDS`) but it is not applied to the book or to `second`. Live passes `snapshot.second` through unchanged.

`332e1c17` (2026-09-19 12:04 +0200) changed the join from `lookup_market_p_after(..., LOL_SOURCE_LAG_SECONDS)` to horizon 0, and the 300 s label from +310 s to +300 s. Commit text: “The live quote already sits on the GRID clock; lagging the training mid by 10s made second 0 read T+10 instead of the spawn print.” That treats the GRID second’s wall as the book the bot trades. The bot trades the book at receipt, which is ~8 s later than that wall. `c9fb5cd2` (2026-09-19 13:31 +0200) retrained on the new join. Production `model.json` is `20260921T095813Z`, research (this backtest) is `20260921T095801Z`, both `source_lag_seconds: 10`, both after the join change.

**The tapes.**

Two live maps with local windows, both `execution_mode=live`, `grid_delay_s=8`:

- `grid-3000375-m4`, esports game `115565004607949403`, horn `2026-09-18T22:30:40Z`
- `grid-3002603-m1`, esports game `115548681803406328`, horn `2026-09-19T15:14:59Z`

Of 39 LoL tapes with horn in 2026-09-14..09-20, 33 join `links.parquet` and have `windows/<id>.jsonl.gz`. `grid_state.jsonl.gz` is the archive (plain `grid_state.jsonl` is absent). Session `second` sequences match GRID events at 99.87% and 99.70%.

| measurement | m4 (Sep 18) | m1 (Sep 19) |
|---|---:|---:|
| scoreboard `now − occurredAt`, median | 2.56 s (n=950) | 2.83 s (n=961) |
| `series_table` `delay` | 8 on all 970 frames | 8 on all 969 frames |
| receipt − livestats wall of `snapshot.second`, median | +8.38 s (n=203) | +8.69 s (n=110) |
| receipt − (horn + second), median | +8.51 s | +8.25 s |
| horn − livestats spawn | −0.60 s | −0.36 s |
| paired kills in 0..540 s | 7/7 sides match | 2/2 sides match |
| receipt − kill `rfc460`, median | +10.16 s | +11.44 s |
| GRID second − livestats game time at the kill, median | +2.21 s | +2.48 s |

The August note in `docs/experiments/lol-grid-widget.md` says `occurredAt` lags wall by ~7 s. These two September tapes do not: the median age is ~2.6 s, with a long tail (p95 18 s and 17 s). The 8 s in the snapshot second is the declared table delay, not that age. The two add: a ~2.6 s stamp age plus subtracting 8 from a clock that was only extrapolated by 2.6 s leaves the named second ~8 s behind receipt. That is the measured internal lag.

Across every admitted LoL schedule that joins the audit (209 maps, 20,682 non-paused ticks with `game_second` in 0..540), receipt minus the livestats wall of that second is median **+7.92 s** (p05 +7.02, p95 +8.97). Match-level medians are the same (+7.93 s, n=209). Ticks more than 2 s **before** the frame: **4**. Spawn offset (horn − livestats second-0 wall) median **−0.61 s** (p05 −1.06, p95 −0.14; the mean +0.16 is two outlier maps).

**One kill, both prices.** `grid-3000375-m4`, first blood, blue died (`delta_blue=1`). `rfc460Timestamp` `2026-09-18T22:34:43.242Z`, livestats game time 175.1 s. Telonex paired mid P(blue):

| at rfc460 −5 s | at rfc460 | +4 s | +5 s | +8 s | +10 s | bot signal at the GRID tick |
|---:|---:|---:|---:|---:|---:|---:|
| 0.375 | 0.375 | 0.335 | 0.335 | 0.310 | 0.270 | 0.270 at second 178, 11.35 s after the stamp |

The bot’s signal within 3 s of the stamp is still 0.375. The bot’s signal when GRID reports the death is 0.270. The 10.5¢ move is over before the death is in the snapshot. Second kill on the same map (red died, stamp `2026-09-18T22:37:16.715Z`): mid 0.355 at the stamp, 0.485 at +5 s, 0.395 at +8 s; the GRID signal 10.16 s later is 0.395. The spike is gone; a smaller move remains. `grid-3002603-m1` first kill (red died, `2026-09-19T15:18:57.571Z`): 0.660 at the stamp, 0.705 at +4 s, 0.715 at +8 s and +10 s; the GRID signal is 0.715. The signal nearest the stamp is 0.660.

**Dataset check that the parquet is as-of 0.** `validation.parquet` `market_p_radiant` versus `market_seconds` at the same second: median absolute error **0** (n=3,160,982, mean 0.00060). Versus the mid at second+10: median **1.0¢**, mean 1.65¢. Frame wall sits a median 0.103 s before the integer-second boundary (the age gate is 2 s; these rows are much tighter). A 40-match probe of `training.parquet` did not join `market_seconds` (those match ids are not in that file); training rows are written by the same `join_market_rows`.

**Mechanism.** The model target is `mid(T+300) − mid(T)` with T = frame wall. The kill reaction (F4) is inside that target and is not inside the book at T+8. Served live, `market_p_radiant` is already the post-reaction mid (16% of gain), so a tree can in principle shrink the delta. It does not fully: schedule BUY fills, which use the post-reaction anchor, still carry median `|predicted_delta|` 2.84¢ versus 2.91¢ on grid-v1. The delta is aimed at a move that has partly happened. Schedule execution does not turn that into a positive 30 s markout (F1). Grid-v1 execution does, because it fills at T.

**Next check.** Put the prepare join back to horizon 8 or 10 (label at the same horizon + 300, not +310 on top of a mismatched anchor) and retrain. Compare schedule-replay BUY 30 s and 300 s markout to this run before touching live size.

### F3 — schedule replay is the live clock, and it is not where the 30 s markout comes from

- Stage: backtest
- Severity: high as a correction to the headline number; the path itself is clean
- Confidence: verified

**Claim.** `build_schedule_match_signals` decides at `tick.received_ns` and looks up livestats features at `tick.game_second`, with no lag subtract. That received time is the same reducer instant live uses. It is ~8 s **after** the feature’s livestats wall, so the feature is in the past. The +0.51¢ BUY 30 s markout is not this path.

**Evidence.** `src/backtest/signals.py` `_match_schedule_decisions` anchors the mid with `lookup_reference_mid(series, tick.received_ns)` and sets the model `second` to `game_second` (`decision_rows["second"] = decision_rows["game_second"]`). `reject_schedule_flags` refuses `--lag-seconds` on a schedule run. Schedule ticks are `GridFrameReducer` output (`src/archive_index/schedule.py` `_extract_grid`), so `game_second` is `snapshot.second` and `received_ns` is `received_at_utc`.

F1’s offset table is this clock. F1’s schedule markout row is this path: BUY 30 s **+0.086¢** ($63), BUY 300 s **+2.205¢** ($1611), SELL 30 s **−1.01¢**. Predicted deltas are not smaller than grid-v1 (median absolute 2.84¢ vs 2.91¢; fraction with `|delta| ≥ 0.02` is 90% vs 91%). The model is just as willing. The book has already moved.

Schedule pre-rebate is $486.88 of $5171 (9%). Maker rebate on schedule fills is $249.4, so this cohort’s net is about $736, against the headline net of $6403. Per map, schedule pre-rebate is $2.99 versus $5.99 on grid-v1, but grid-v1 is mostly June–August (762 maps before 2026-09-01, engine PnL $4699) and schedule is September. Do not read $5.99 − $2.99 as a pure delay effect. The delay effect that is measured on the same code path is the markout, not the summer PnL.

BUY 300 s staying at +2.2¢ on the live clock means an extra 8 s does **not** remove the backtest’s longer markout. It removes the short one.

**Next check.** Report September schedule seed0 on its own whenever the LoL backtest is compared with live. The blended 945-map number is a different clock.

### F4 — the kill reaction finishes before GRID delivers the kill

- Stage: prepare / live (the price path both of them sit on)
- Severity: high
- Confidence: verified on the aggregate; the two maps are the same shape with small n

**Claim.** After a one-sided death, the mid moves in the killer’s direction inside 4–10 s of `rfc460Timestamp`. Live’s first look at that death is ~10–11 s after the stamp, so the reaction is already in the book.

**Evidence.** Validation rows, one-sided death-count increases only (both sides increasing in the same second dropped). Direction +1 when red deaths rise (blue got the kill). Signed move of `market_p_radiant` from the row’s mid:

| horizon | n | mean | median | p75 | fraction negative |
|---|---:|---:|---:|---:|---:|
| +4 s | 48941 | +2.63¢ | +1.0¢ | +4.5¢ | 17% |
| +5 s | 48941 | +3.09¢ | +1.5¢ | +5.0¢ | 17% |
| +8 s | 48941 | +3.86¢ | +2.0¢ | +6.5¢ | 17% |
| +10 s | 48941 | +4.13¢ | +2.0¢ | +7.0¢ | 17% |
| +30 s | 48941 | +5.03¢ | +3.0¢ | +8.5¢ | 19% |

By 8 s the mean has 3.86/5.03 = 77% of the 30 s signed move; by 10 s, 82%. Medians: 2¢ of a 3¢ move. Unconditionally, across 883,745 labeled seconds, the absolute 10 s move is only **11.6%** of the absolute 300 s label (mean 10 s move +0.03¢, mean 300 s label +0.51¢). The timing gift is the kill seconds, not every second.

Hand-map signed moves (small): map m4, 6 one-sided kills, median +2.75¢ at +5 s and +4.25¢ at +8 s. Map m1, 1 one-sided kill with a book, +4.5¢ at +4 s and +5.5¢ at +8 s. The three fully quoted kills in F2 match this and show the bot’s own `market_p_radiant` printing the post-move price.

**Mechanism.** Grid-v1 sells the kill to the model at the pre-move mid and fills there. Live, and schedule replay, sell it at the post-move mid. An extra 8–10 s of delay on a grid-v1 fill would replace a +0.62¢ 30 s BUY markout with something near the schedule +0.09¢, on the evidence of the two clocks that already exist. It would not, by itself, zero the +2.2¢ schedule BUY markout at 300 s.

**Next check.** None required for the sign. A controlled delay rerun (F1) is the PnL number; this table is the price-path number.

### F5 — live prior is ~90 s staler than the training prior

- Stage: train / live
- Severity: medium
- Confidence: verified on the code and on two maps; the cent gap is those two maps only

**Claim.** Training `market_radiant_prior` is the last two-sided mid in `[spawn − 61 s, spawn)` (`lookup_strict_prior` in `src/lol/05_prepare_dataset.py`). Live sets `anchor_ts = horn_unix_seconds − 90` (`HORN_OFFSET_SECONDS` in `src/shared/utils/match_time.py`, used in `src/trader/match_worker.py` `_maybe_start_prior`) and takes the last minute bar strictly before that. For LoL, horn is GRID clock zero (within 1 s of livestats spawn, F2), so the live anchor is ~90 s before spawn, not 1 s before. The schedule backtest uses the training prior, via `game_features.parquet`.

**Evidence.** Research gain, mean across the 10 members: `market_radiant_prior` 26.8%, `market_p_radiant` 16.0%, `radiant_nw_adv` 35.5%, `second` 0.81%.

| map | session `market_radiant_prior` | book at horn−90 s | book at spawn−1 s |
|---|---:|---:|---:|
| grid-3000375-m4 | 0.345 | 0.355 | 0.370 |
| grid-3002603-m1 | 0.625 | 0.625 | 0.675 |

Map m1’s live prior equals the horn−90 print and is 5¢ off the training prior. Map m4 is 1¢ off horn−90 (minute bar versus last book print) and 2.5¢ off spawn−1.

**Mechanism.** This is not a future price. The spawn−1 print is public before the first quote. Live chooses an older one because the 90 s offset is the Dota pre-horn. It makes live’s feature differ from every backtest path, schedule included, so it does not explain grid-v1 versus schedule. It can explain a slice of live versus the September schedule backtest. Five cents on the second-largest feature is enough to move a split; it is not the 30 s markout.

**Next check.** On a few dozen linked live maps, distribution of (book at spawn−1) − (session prior). If the median is a few cents, decide whether LoL’s prior anchor should be spawn rather than horn−90.

### F6 — grid-v1 feeds `second − 10` on rows that are already the state second

- Stage: backtest
- Severity: low
- Confidence: verified

**Claim.** Dota’s subtract is “market second minus lag = state second.” LoL’s `second` column is already the state second, and training does not subtract (`src/lol/06_train_model.py` fits `FEATURE_COLUMNS` as stored; `fit_research_members` uses `train[list(self.features)]` with no lag shift). Grid-v1 inference still does `second − lag_seconds` with `lag_seconds = 10`. The model sees a clock 10 s earlier than the gold and deaths on that row, and 10 s earlier than live, which passes `snapshot.second` unchanged.

**Evidence.** `src/backtest/signals.py` line 271, and the schedule path which does not subtract (F3). Gain share of `second` is 0.81%. Median `|predicted_delta|` is almost the same on both clocks (F3), so this is not what splits the markout. F1’s timestamp is.

**Next check.** Fold it into the F1 rerun: when the signal moves to +8 s, stop subtracting 10 from LoL `second`.

### F7 — at the kill, GRID’s second is ~2 s ahead of livestats game time

- Stage: live / backtest feature lookup
- Severity: low
- Confidence: verified on 9 paired kills; consistent with the bulk offset

**Claim.** When GRID first shows a death, `snapshot.second` is about 2 s higher than the livestats game time of the frame that counted that death. The tick still arrives ~8 s after the livestats wall of that higher second, so the feature lookup is not a future frame relative to the book.

**Evidence.** Nine paired kills, sides matched on all nine (blue deaths = `deaths_radiant` on both feeds). Median GRID-second minus livestats game time: +2.21 s and +2.48 s. Median receipt minus `rfc460`: +10.2 s and +11.4 s. Difference is the bulk +8 s offset. Spawn itself is within 1 s (F2). A 2 s label skew is inside the noise of a 1 Hz frame and the 2 s age gate.

**Next check.** None, unless a later agent finds GRID gold leading livestats gold by much more than 2 s at the same receipt.

## Checked and OK

- **Schedule features are not from the future.** 20,682 ticks, median +7.92 s, 4 ticks early by more than 2 s. Command: `work/clock-grok/bt_clock.py`.
- **Spawn alignment.** Median horn − livestats second-0 wall = −0.61 s on 209 schedules. The 8 s gap is the table delay, not a mis-set spawn.
- **As-of 0 is what the validation parquet contains.** Median abs error versus the same-second mid is 0; versus second+10 it is 1¢. `lookup_market_p_after` only reads a book at or before the target (`src/shared/utils/telonex_book.py`); the look-ahead is the *game state* being paired with that early book, not a future quote.
- **Fill markout reconstruction matches the stored 30 s reference exactly** (n=5759, error 0), so the 4/8/10 s figures use the same mid series as `summary.json`.
- **`signal_mode` on `results.parquet` matches the archive join** (782 / 163, same PnL). The cohort split is the backtest’s own label.
- **Book at the fill equals the signal anchor.** `book_p_radiant − dataset_market_p` median 0 on both cohorts. The fill is not using a newer book than the signal.
- **Sides.** On both hand maps every paired kill has the death on the same side in livestats (blue = radiant in `features_from_sides`) and in the GRID reducer (BLUE = side 0 = radiant).
- **Session versus GRID.** Signal `second` aligns to reducer events in order at ≥99.7%. The session mid at the death tick is the post-move Telonex mid, not a second book.
- **`rfc460Timestamp` behaves as event time, not as the ~55 s availability time.** Mids are unchanged in the 5 s before the stamp and move in the 5 s after. If the stamp were the publish time, the move would already be in the past.

## Open questions for the owner

1. The blended 945-map LoL backtest is the wrong object to put next to live. September schedule replay is the comparable one: $487 pre-rebate, BUY 30 s markout +0.09¢, BUY 300 s +2.2¢. Is that the gap you care about, or the summer grid-v1 number?
2. Training should move back to an 8 s or 10 s book if the goal is “the mid live can trade.” Schedule execution is already on that clock; the model was fit on the earlier one. A retrain can still change the +2.2¢ 300 s markout. Worth a run before any live-size change.
3. LoL’s prior anchor subtracts Dota’s 90 s pre-horn. On one of the two maps that is a 5¢ feature error on a feature with 27% of the gain. Do you want the prior at spawn, the way prepare already defines it?

## Needs from VPS

None. Tapes, windows, schedules, and the seed0 backtest were local.

## Scripts and outputs

All under `betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok/`. Run from `esports-trader` with `PYTHONPATH=src uv run python`.

| path | what it is |
|---|---|
| `find_maps.py` | LoL tapes with horn in 2026-09-14..09-20, joined to links and windows |
| `candidates.json` | 39 tapes, 33 with livestats |
| `bt_clock.py` | as-of check, schedule offsets, markout by `signal_mode`, kill reaction, gain |
| `bt_clock.json` | those numbers |
| `hand_clock.py` | `grid-3000375-m4` and `grid-3002603-m1` |
| `hand_clock.json` | clock summaries for those two maps |
| `kills_grid-3000375-m4.csv` | 7 kills, Telonex mids and the bot’s signal |
| `kills_grid-3002603-m1.csv` | 2 kills |

Follow-up counts that are not in `bt_clock.json` (same seed0 files, `signal_mode` from `results.parquet`): September grid-v1 is 20 maps, engine PnL −$14.51, BUY 30 s markout +1.23¢ (n=80); pre-2026-09-01 grid-v1 is the rest of the $4684. Schedule fill rebate sums to $249.4. Median `|predicted_delta|` on BUY fills is 2.91¢ (grid-v1) and 2.84¢ (schedule).
