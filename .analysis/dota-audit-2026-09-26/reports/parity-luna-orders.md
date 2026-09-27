# parity-luna-orders — order lifecycle parity
Status: FINAL

## Summary

- **No backtest over-fill is measured in this comparison.** On the same 499 matched GRID BUY placements (29 maps / 25 event series), the backtest had 21 orders fill versus 50 live; the first-fill rate per exposed order-minute was 0.259/min versus 0.648/min (backtest/live hazard-style ratio 0.400; event-series bootstrap 95% CI 0.237–0.613). The same direction holds with 15- and 60-second matching windows and within rung L0. This is selected, trace-covered placement evidence, not a causal estimate for the full return gap.
- The brief’s premise that Nautilus clears queue-ahead on every snapshot is superseded by the verified converter behavior. Telonex emits CLEAR only on the first in-window snapshot; later snapshots are UPDATE/DELETE diffs. Queue-ahead therefore persists and is modified by deltas and trade ticks. I classify candidate optimism against that live queue mechanism below.
- The queue-at-placement reconstruction is well supported: raw Telonex depth after applying the simulator’s same-token own-order stripping is within one share of persisted queue-ahead on 234/241 filled backtest orders (97.1%; MAE 0.98 shares). Live queue is measurable on 3,508/3,527 trace orders; 3,491/3,508 samples were under two seconds old.
- Known queue approximations can still create local phantom fills: wrong-side onchain trade ticks (~50% across token-file copies in the prior fill audit), late block-stamped ticks that can decrement after the book has already reduced queue, `cap_queue_ahead` counting all same-level shrink as ahead-of-us cancellations, and DELETE setting queue-ahead to zero without restoring it on re-add. **This study does not identify any of those as the driver of aggregate backtest over-filling because aggregate/conditional over-filling is not observed.**
- Markout buckets do not show a stable front-of-queue-is-less-toxic signature. SELL-side paired fills are sparse: among 244 similar placements, live fills 24 orders and backtest fills 7; the backtest's first-fill rate per exposed minute is numerically higher, but its event-series confidence interval is very wide and crosses parity. As-of-book fill prices are favorable versus midpoint for SELLs in both streams; backtest BUY executions are about one cent above midpoint in the valid-mid sample while live BUYs are about half a cent below.

## Scope and method

Main cohort: the 49 complete GRID maps from 2026-09-01–19 where live and seed-0 LIVE backtest both bought and live PnL is complete (the same return-comparison cohort in `reports/parity-luna.md`). Side table: five Oddin maps from Sep 20–26. Core traces exist for 31/49 GRID maps and all five Oddin maps; per-order live/backtest comparison is restricted to the trace-covered maps. The live session archive has 435 durable fills across the 54-map cohort; 325 unique trace Fill IDs link to journal fills. Repeated core-trace Fill notifications were de-duplicated by `fill_id`.

The backtest is the existing LIVE `seed0` run, not a newly run backtest. Live acceptance/rest/cancel/fill timing comes from core traces and `session.jsonl`; backtest acceptance, fills, and recorded `queue_ahead` come from `quote_events.parquet` and `fills.parquet`. Queue at order placement is reconstructed from the raw Telonex snapshot as-of submit. For backtest orders, it subtracts live resting same-token size using the same behavior as `strip_own_book.py`; same-token stripping matters because it reduces displayed competitors in the replay book. Fill exposure ends at first fill or terminal/censor time. The reported fills per resting minute are descriptive first-fill event rates (`orders with ≥1 fill / order-minutes exposed until first fill or censor`), not a proportional-hazards fit; cancellation and policy selection can be informative.

Conditional BUY and SELL pairs are greedy, nearest 1:1 matches without replacement within the same map, token, rung, and queue-ahead bucket, with price within $0.01 and game-second within the stated tolerance. Backtest wall time is mapped to live game time using trace `ClockUpdate`s, not catalog/archive horn. That avoids the known early archived-horn error: N4/N4b found the live GRID game-second itself is correct after the pre-horn pause, while the persisted/catalog archive horn is early by pause D. Confidence intervals resample event-series clusters (10,000 draws; fixed seed), so BO3 maps in one series are not treated as independent.

Raw per-token public trade and book archives can corroborate a fill near a print or show a quote at/through the limit, but the simulator does not persist the specific matching-engine event that caused each fill. Accordingly, tape/book associations are witnesses, not proof that a particular simulated order consumed a particular print or that a displayed level was actually swept. Markout references also differ: live 30/300-second marks use the next logged live signal-mid at game-second +30/+300; backtest marks are the persisted simulator fill marks. Treat those bucket means as descriptive, not a perfectly synchronized experiment.

## Findings

| ID | Severity | Claim | Evidence / impact |
|---|---|---|---|
| parity-luna-orders-F1 | S2 | Matched GRID BUYs fill less often in the backtest than live | 499 matched placements; 50 live vs 21 backtest first-filled orders; hazard-style ratio 0.400 (series bootstrap CI 0.237–0.613). Robust to ±15/±60 s match windows. Evidence against a broad queue-model over-fill in this matched sample. |
| parity-luna-orders-F2 | S1 mechanism / cohort impact unquantified | Persistent queue state still has optimistic amendments and trade-tick paths; no aggregate over-fill driver is isolated here | Queue is not reset per snapshot. Prior `fill-devin` verified wrong-side aggressor ticks (~50% in its 11-map sample), late queue double-decrement, DELETE/re-add promotion, and unmodeled venue secondsDelay. This cohort does not attribute net over-fill or PnL to any one path. |
| parity-luna-orders-F3 | S2 | Tape and book snapshots only partly identify a fill path | On trace-covered GRID fill events, exact same-token contra-side prints at the fill price within ±2 s corroborate 191/289 live events and 90/241 backtest events. A raw quote at/through the order limit is present at the simulator fill time on 112/241 BT rows. These are overlapping witnesses; fill telemetry has no causal event ID, so remaining fills cannot be split reliably into trade-at-price versus level-sweep causes. |
| parity-luna-orders-F4 | S3 | Markouts do not support a stable front-of-queue toxicity signature | By rung/queue bucket, signs vary across buckets and 300s values can reverse the 30s result. Small cell sizes at L1/L2, plus unlike live/backtest reference timing, limit inference. |
| parity-luna-orders-F5 | S2 | SELL exits have mixed order probability and exposure-rate results | Broad trace-covered GRID order fill probability is 9.12% live vs 5.36% BT, but first-fill rate is 0.126 vs 0.388 per order-minute. Among 244 similar SELL placements the probabilities are 9.84% vs 2.87%, while BT/live first-fill rate is 1.76 (95% event-series CI 0.36–4.27). Sparse events leave hazard parity unresolved. |

## Findings detail

### parity-luna-orders-F1 — No backtest over-fill in the matched BUY placement sample

**Coverage and broad order summaries.** Core traces cover 31 of the 49 GRID maps. On those same maps, live BUY orders are accepted 1,357 times and 128 orders receive a fill (9.43%); the simulator submits 1,818 BUY orders and 75 receive a fill (4.13%). Total quantity filled is 7.1% of submitted live BUY quantity and 2.9% for the backtest. Live first-fill exposure totals 172.42 order-minutes (0.742 fills/minute); backtest exposure totals 230.33 order-minutes (0.326/minute). These broad rates mix different numbers, prices, and placement times, so I use them as context, not as the main comparison. The table gives the requested per-rung view. Queue size is reconstructed displayed shares ahead at placement. `Fill/min` is filled orders divided by aggregate order-minutes exposed until first fill/censor; quantity fraction is total shares filled / shares submitted.

| Source / BUY rung | Accepted | Queue ahead, median [p25,p75] shares | Median rest | Median first-fill time among filled | Orders filled / probability | First-fill events / order-min | Filled quantity / submitted |
|---|---:|---:|---:|---:|---:|---:|---:|
| Live L0 | 254 | 92.7 [20.0, 238.0] | 7.52 s | 4.64 s | 88 / 34.6% | 1.772 | 25.7% |
| BT L0 | 460 | 63.9 [18.1, 157.6] | 3.00 s | 5.11 s | 53 / 11.5% | 0.786 | 9.1% |
| Live L1 | 454 | 143.8 [38.9, 731.6] | 3.96 s | 3.53 s | 27 / 5.95% | 0.442 | 4.7% |
| BT L1 | 645 | 94.1 [0.0, 314.0] | 2.88 s | 2.16 s | 15 / 2.33% | 0.176 | 1.9% |
| Live L2 | 649 | 98.5 [0.0, 515.0] | 2.02 s | 5.87 s | 13 / 2.00% | 0.211 | 2.0% |
| BT L2 | 713 | 50.0 [0.0, 259.9] | 1.73 s | 2.81 s | 7 / 0.98% | 0.090 | 1.1% |

**Conditional comparison.** At ±30 seconds there are 499 paired placements on 29 maps / 25 event series. Live has 50 filled orders (10.02%); backtest has 21 (4.21%). First-fill exposure is 77.106 live order-minutes and 81.004 backtest order-minutes, yielding 0.648 and 0.259 first-fills/minute. Backtest/live hazard-style ratio is **0.400** (95% event-series bootstrap CI **0.237–0.613**); fill-probability ratio is **0.420** (CI **0.275–0.595**). Median time-to-first-fill among filled pairs is close: 5.56 s live, 5.60 s backtest. Thus the backtest did not fill more often or faster in this matched placement set.

Window sensitivity: ±15 s gives 460 pairs and hazard ratio 0.394 (95% CI 0.231–0.621); ±60 s gives 543 pairs and 0.402 (0.247–0.589). Rung breakdown at ±30 s:

| BUY rung | Pairs | Live filled | BT filled | BT/live fill-probability ratio | BT/live hazard-style ratio (95% CI) |
|---|---:|---:|---:|---:|---:|
| L0 | 87 | 34 (39.1%) | 15 (17.2%) | 0.44 | 0.365 (0.203–0.560) |
| L1 | 173 | 11 (6.4%) | 5 (2.9%) | 0.45 | 0.476 (0.117–1.254) |
| L2 | 239 | 5 (2.1%) | 1 (0.4%) | 0.20 | 0.186 (0.000–1.012) |

Only L0 has enough paired fill events for a CI that clearly excludes equal rate. L1/L2 are sparse and inconclusive. The matching procedure conditions on both systems having an accepted order in the same map/token/rung/queue bucket and near the same price/time. It omits unpaired orders and unmatched maps, including 18 GRID maps without core traces, so it cannot prove the simulator is conservative for all live opportunities or every market regime.

**Queue validation.** There are 3,508/3,527 live trace orders with a raw-book queue observation; 3,491/3,508 are ≤2 s old, and 3,392/3,508 (96.7%) agree on top-of-book with the latest trace BookUpdate. For 241 backtest filled-order records with persisted queue-ahead, raw depth before own-order stripping is within one share of telemetry on 228/241 (94.6%; absolute error sum 850.86; MAE 3.53). After subtracting same-token live resting orders, 234/241 (97.1%) are within one share (absolute error sum 235.54; MAE 0.98). This both validates the reconstructed comparison queue and confirms the own-book-strip adjustment is material. For unfilled orders, 4,909 queues are reconstructed from the raw book; 14 of 5,164 submitted orders remain unknown.

### parity-luna-orders-F2 — Actual live queue mechanism and known optimistic paths

**Converter correction.** The initial brief said every snapshot starts with CLEAR and resets queue-ahead. The user correction is right: `prediction-market-backtesting/crates/core/src/telonex.rs:358,372-419` uses an `emitted_snapshot` gate in `parquet_book_snapshot_diff_rows` / `append_snapshot_rows` so only the first in-window snapshot per conversion call is emitted as CLEAR; later snapshots produce UPDATE/DELETE diffs with `RECORD_FLAG_LAST` and no `F_SNAPSHOT` flag. In Nautilus 1.226, `clear_all_queue_positions` is called for an actual CLEAR delta, not for each snapshot. Do not use a per-snapshot-reset replay or attribute the observed comparison to one.

Under the live mechanism verified in `reports/fill-devin.md` and the pinned matching-engine source:

1. On accept, `snapshot_queue_position` initializes queue-ahead to the full displayed size at our limit (appropriate for a newly joining order behind the existing level). Later level growth is not added to queue-ahead.
2. `cap_queue_ahead` clamps queue-ahead down to the current visible level size on each UPDATE. A cancel behind us also shrinks visible size, so it can be incorrectly treated as queue in front leaving.
3. `clear_queue_on_delete` sets queue-ahead to zero when the level is deleted; if it reappears, the queue position is not restored. That can promote an order to the front.
4. Onchain-fill ticks are block-stamped later than real-time trade prints. Consumption has a stale-trade guard, but the queue decrement path does not; if a book delta has already reduced the visible level, a later trade tick can decrement it again.
5. Onchain-fill rows are duplicated into both token files. `taker_side` refers to `taker_asset_id`, not necessarily the file’s `asset_id`; the converter passes that side through without inversion when the IDs differ. The prior fill audit measured ~50% wrong-side rows across the two token-file copies in an 11-map sample. Inverted ticks can starve the actually-hit side while making orders on the untouched side eligible to fill.
6. Gamma `secondsDelay=1` is not modeled. Backtest queue position is sampled after about 85 ms insert latency, while the real venue delays new orders by one second before they join the queue. The roughly 0.9 s priority advantage is another optimistic placement approximation, independent of snapshot resets.

These mechanisms are real, and the direction of the cancellation/deletion and duplicate-decrement approximations can be optimistic for some queue states. However, F1 finds lower backtest first-fill rates than live for similar placements. This analysis therefore reports no measured aggregate/conditional over-fill and assigns no observed over-fill driver. If an order-level false-positive investigation is done next, wrong-side trade ticks are the first targeted approximation because the prior audit establishes both broad exposure and a direct wrong-side fill path; late double-decrement and delete/re-add promotion are the next candidates. The 1 s `secondsDelay` omission can also place simulated orders near a second too early in queue. A numerical PnL attribution requires a corrected-side, no-double-decrement replay and event-level fill trigger logging; these were not run here.

### parity-luna-orders-F3 — Tape and quote evidence partly classify fill paths

I joined trace-covered fill events to the same-token real-time `trades` archive by expected contra-side, price, and timestamp; `trades.side` is per-asset. For a BUY, the expected contra print is sell-aggressor; for a SELL, buy-aggressor. A “through-limit” print means price ≤ the BUY limit or ≥ the SELL limit. I also inspected the latest raw book snapshot at or before each fill timestamp; quote-through means best ask ≤ BUY limit or best bid ≥ SELL limit. Windows are ±2 seconds for the primary print witness and ±15 seconds as a block-stamp sensitivity check. The raw `trades` channel was not the simulator's trade input; replay reads `onchain_fills`, so these matches are independent historical witnesses, not matching-engine causation.

| GRID fill events | N | Contra-side trade at exact fill price ±2 s | Same at ±15 s | Contra-side print at/through limit ±2 s | Opposing quote at/through limit in as-of book |
|---|---:|---:|---:|---:|---:|
| Live BUY | 164 | 114 (69.5%) | 126 (76.8%) | 132 (80.5%) | 34 (20.7%) |
| BT BUY | 99 | 41 (41.4%) | 57 (57.6%) | 65 (65.7%) | 92 (92.9%) |
| Live SELL | 125 | 77 (61.6%) | 84 (67.2%) | 95 (76.0%) | 12 (9.6%) |
| BT SELL | 142 | 49 (34.5%) | 73 (51.4%) | 64 (45.1%) | 20 (14.1%) |

Across BUY+SELL, same-price contra prints within ±2 s appear on 191/289 linked live fill rows (66.1%) and 90/241 simulator fill rows (37.3%); within ±15 s the counts are 210 (72.7%) and 130 (53.9%). All 289 trace-linked live events and all 241 trace-covered BT fill rows in this GRID slice are marked maker. The exact print and quote-cross columns overlap: 47 BT rows have both an exact print and quote crossing; 65 have a quote at/through the limit but no exact same-price print within ±2 s. The raw quote at/through order limit is especially common for BT BUY rows (92/99), while exact public tape matches are less common than live. That is compatible with order levels being crossed/swept, stale block ticks, and/or unmatched archive timing; without the matching-engine trigger ID it cannot distinguish those explanations or establish a false fill. The tape join is not one-to-one volume allocation: a public print can be a candidate witness for more than one partial simulator fill row.

After assigning exact-price prints first, then quote-cross-without-an-exact-print, 13 additional BT rows have a better-than-limit contra-side print but no quote-cross witness; the remaining 73/241 rows have none of those three witnesses within ±2 s. They are unclassified by this test, not proven phantom fills. The stored public tape is not complete causal logging of the simulated event stream, and the ±15-second sensitivity recovers some print matches. Raw book snapshots expose only the archived levels and do not identify which partial cancellations were ahead or behind our simulated order.

At-fill midpoint check uses `(best bid + best ask)/2` from the latest raw snapshot at or before fill time, excluding crossed bid/ask snapshots. The signed value below is `fill price − midpoint` for both sides: negative BUY values mean paid below mid; positive SELL values mean sold above mid.

| GRID fill events | N with non-crossed midpoint | Median fill−mid | Mean fill−mid |
|---|---:|---:|---:|
| Live BUY | 164 | −$0.005 | −$0.0057 |
| BT BUY | 78 | +$0.0075 | +$0.0104 |
| Live SELL | 125 | +$0.005 | +$0.0086 |
| BT SELL | 139 | +$0.015 | +$0.0171 |

The midpoint result is descriptive: BT BUY fills in this subset occur at prices above contemporaneous mid, while live BUYs occur below; BT SELLs also sell at a larger premium than live. It does not on its own imply fill optimism or better net exits because fills differ in time and policy, and the backtest BUY view is often immediately crossed by the opposing quote. For BT, the sampled raw quote age at fill was low (p95 17.8 ms for BUY, 345.3 ms for SELL); live p95 was 56.2/40.6 ms for BUY/SELL.

### parity-luna-orders-F4 — BUY markouts by rung and placement queue

Queue buckets are Q0=0 shares ahead, Q1=(0,25], Q2=(25,100], and Q3=>100. Table uses only the 31 trace-covered GRID maps and order-linked live fills; `n` is fill rows. Values are quantity-weighted BUY markouts in dollars/share at 30 s / 300 s; positive means the later reference mid is higher than fill price. Live reference is the next logged signal mid at game second +30/+300; BT reference is stored in `fills.parquet`.

| Rung / queue at placement | Live n; 30 s / 300 s | Backtest n; 30 s / 300 s |
|---|---:|---:|
| L0 / Q0 | — | 1; +0.0050 / −0.1150 |
| L0 / Q1 | 45; −0.0096 / +0.0018 | 21; −0.0025 / +0.0173 |
| L0 / Q2 | 29; +0.0054 / −0.0436 | 16; +0.0096 / +0.0031 |
| L0 / Q3 | 37; +0.0083 / −0.0013 | 29; +0.0013 / +0.0107 |
| L1 / Q0 | 17; −0.0045 / +0.0244 | 10; +0.0188 / +0.0120 |
| L1 / Q1 | 4; −0.0317 / −0.1150 | 1; +0.0100 / +0.0050 |
| L1 / Q2 | 4; +0.0088 / +0.0587 | 1; +0.0150 / +0.1600 |
| L1 / Q3 | 6; +0.0094 / +0.0527 | 9; −0.0037 / −0.0129 |
| L2 / Q0 | 8; +0.0054 / +0.0012 | 10; −0.0150 / −0.0123 |
| L2 / Q1 | 1; +0.0750 / +0.0600 | — |
| L2 / Q2 | 3; −0.0393 / −0.1563 | — |
| L2 / Q3 | 5; −0.0272 / −0.0878 | 1; −0.0150 / −0.0650 |

The signs are not ordered by queue bucket in a stable way: e.g. live L0 is increasingly positive at 30 s from Q1→Q3, but Q2 is sharply negative at 300 s; backtest L0 changes rank/order across horizons. Most L1/L2 cells have fewer than 10 observations, and several one-fill means are not interpretable. The desired “front of queue is less toxic” signature is not established.

### parity-luna-orders-F5 — SELL exits

Broad trace-covered GRID summary: live SELL order first-fill probability is 78/855 = 9.12%, median total rest 3.87 s and median time-to-first-fill 6.52 s; backtest 74/1,381 = 5.36%, median rest 4.16 s and time-to-first-fill 1.65 s. Live first-fill exposure is 619.93 order-minutes, backtest 190.51, so the descriptive first-fill rates are 0.126/min and 0.388/min. Backtest filled quantity is 3.1% of submitted SELL quantity versus 6.0% live.

Conditional on a similar SELL placement at ±30 s there are 244 pairs on 25 maps / 22 event series. Live fills 24/244 (9.84%), backtest 7/244 (2.87%); fill-probability ratio BT/live is 0.292 (95% event-series bootstrap CI 0.105–0.500). Live first-fill exposure is 261.64 order-minutes and BT 43.39, giving rates 0.092 and 0.161/min. BT/live hazard-style ratio is 1.76 (95% CI 0.36–4.27), too imprecise to resolve rate parity; among filled orders, median time is 7.05 s live and 3.03 s BT. Window sensitivity remains sparse: ±15 s gives 212 pairs and rate ratio 1.89 (CI 0.37–4.49); ±60 s gives 282 pairs and 1.93 (CI 0.50–4.27). Do not read a higher event rate per minute as more total exits: in all three windows the matched BT order fill probability is lower.

For fill price versus raw midpoint, among non-crossed trace-covered GRID snapshots, median SELL price is +0.5¢ above mid live (n=125) and +1.5¢ above mid BT (n=139); means are +0.86¢ and +1.71¢. This is the immediate premium at the timestamped fill snapshot, not realized exit PnL. It cannot be compared as a causal exit benefit without matching inventory, order intent, and subsequent inventory markout.

**Oddin side table (five maps).** Sample size is too small for matching or useful CIs. Live BUY fills: L0 11/47 orders (23.4%), L1 3/70 (4.3%), L2 3/73 (4.1%); BT: L0 8/40 (20%), L1 0/69, L2 0/86. Filled share fractions are 23.2%/4.4%/4.1% live and 22.9%/0%/0% BT by rung. Live SELL fills are 9/237 (3.8%), BT 7/158 (4.4%); filled quantity is 4.9% live and 5.4% BT. Treat as a side table only.

## Return-gap verdict

The earlier 49-map outcome comparison was live +$53.05 on $10,352 BUY notional (+0.51%) versus backtest mean +$647.18 on $6,152.52 (+10.52%) across three seeds. The $594.13 difference cannot be allocated from this lifecycle comparison: live PnL is session-end cash plus marked inventory; BT includes terminal settlement; the models and order portfolios differ. This study does **not** support broad queue-model fill optimism as the explanation: paired BUY fill rates are lower in BT, SELL order fill probabilities are lower in the paired subset, and broad traced BUY filled quantity is lower. It does not exclude individual phantom fills. The previous report finds 393/1,943 same-input rows change the `|delta| ≥ .02` gate when only the current artifact replaces the historical live model (20.2%), showing meaningful model-version drift without a dollar attribution. BT terminal tails on the 49 maps contribute +$57.49 versus +$14.89 live, a $42.60 difference (<8% of the aggregate gap). The broader seed-0 LIVE run's $5,534 settlement tail is from a different, 613-map population (N1); it should not be used as a 49-map fill effect. SELL midpoint premiums are mixed evidence and do not translate into realized exit PnL. A same-model/same-input comparison plus corrected-side, non-duplicative queue replay is required to apportion the gap.

## Checked and OK

- Queue state is not cleared at every snapshot: checked against the user's source correction and the Telonex converter's first-snapshot `emitted_snapshot` behavior.
- Core Fill trace repetitions are de-duplicated on `fill_id`; unique trace fills link to the session journal.
- Backtest queue reconstruction uses same-token own-size stripping, then validates against recorded filled-order queue-ahead.
- BUY paired matches use live-clock seconds from `ClockUpdate` records, avoiding the catalog archived-horn error from N4/N4b.
- Existing seed-0 telemetry shows all recorded backtest fills are maker (`is_maker=true`); this report does not treat price-touching market orders as simulated taker fills.
- Joined the per-token `trades` archive and as-of `book_snapshot_full` prices for every trace-linked live fill and trace-covered BT fill row; midpoint figures exclude crossed top-of-book snapshots. Reproducible outputs are in `work/parity-luna/fill_tape_witness.csv` and the lifecycle/matching scripts in the same scratch directory.

## Open questions / next checks

- Persist matching-engine trigger/source IDs for each fill so a future run can distinguish trade-driven queue clear, price crossing, and DELETE promotion at order level. The current exact-price/tape and quote-cross matches are not one-to-one causal execution records.
- To quantify queue approximation impact, an offline replay should flip `taker_side` when `taker_asset_id != file asset_id`, prevent a book-reduced level from being decremented twice by late ticks, and restore a defensible queue position on level delete/re-add. Compare fills and PnL one change at a time; no such intervention replay was run for this report.
