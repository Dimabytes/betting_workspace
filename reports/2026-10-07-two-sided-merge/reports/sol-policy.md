# Cross-game policy reconstruction for 0x6e2c
Status: FINAL

## Summary

- **Verified:** 367,893 BUY fills cost **$9.735M including $90,232 of fee residuals** across 2,110 buy markets in 2026-09-07 12:25:58–2026-10-07 12:25:49 UTC. Six additional markets have only cash-return events. Executed BUYs are 51.2% maker dollars / 48.8% taker dollars.
- **Verified:** 6,784 merges return **$9.240M**. They belong to **1,218 transactions**, with five markets per transaction at the median and twelve at the maximum; 94.7% are multi-market transactions.
- **Likely:** A global minute-based inventory sweep, with selective scheduling/eligibility, explains merging better than one large share threshold: 77.4% of transactions land at second 5–10 of the minute, 89.8% sweep at least 98% of available pairs on inventory-complete markets, and merge amounts run from 1 to 42,615 pairs.
- **Verified:** **83.0% of merge dollars return before the last BUY in the market**; about **94.9% of observed paired cash returns** use MERGE rather than leaving common pairs until REDEEM. Settlement timestamps are not available, so “before last BUY” is the observable definition of mid-market.
- **Verified:** BUY-fill-sampled absolute net inventory is 120 shares at median / 1,433 at p95 / 3,368 at p99. Its own-last-fill dollar value is $56 / $687 / $1,867. A universal share cap is not identifiable; observed peaks reach 21,097 shares.
- **Verified:** **58.1% of taker dollars buy the minority outcome**, 13.9% add/initialize inventory after a ≥2-cent own-price rise within 60 seconds, and 28.0% are other. Some minority-side trades overshoot: only 90.5% of these taker fills reduce absolute net inventory.
- **Verified:** There is a strong BUY price boundary: **zero BUY fills above 0.95**; 2,087 BUYs execute exactly at 0.95. Cheap-token BUYs extend to 0.001, with 3,245 fills at or below 0.02. Fill prices do not prove resting quote boundaries.
- **Verified:** The latest-window API delta is **$214.6k gross trade PnL − $90.1k fees = $124.5k position PnL**, with $124.8k realized; gross edge is **2.23 cents/$**, not a constant 3.5–4.0 cents/$. Gross edge averaged 3.39 cents/$ in W31–W36 and 2.40 cents/$ in W37–W40.
- **Verified:** Activity shows **$51.94k rebates paid**, but cumulative PnL shows only $13.03k of rebate growth: both API cumulative rebate fields freeze after September 12. This prevents exact income reconciliation from that series.
- **Verified / bounded:** The requested signed cashflow capital measure has **$22.9k peak and −$37.9k mean** because it retains profits. Positive per-market unreturned cash has **$107.4k peak / $67.0k p95 / $35.2k mean**, but includes stale residual losses. Active-span cost basis has **$50.7k peak / $6.8k p95 / $1.9k mean**; adding a one-hour tail gives $53.5k / $9.5k / $3.4k. These are different proxies, not a proven bankroll requirement.

All summary numbers come from the reproducible commands in Files and the section-specific outputs below. This report performs no live trading, external writes, model training, or backtest.

## Findings

### Method, ledger and evidence boundaries

**Verified methodology:** `work/sol-policy/analyze.py:9` sorts all activity chronologically, preserving reverse source order within a trade second and placing TRADE before MERGE before REDEEM. `analyze.py:18` builds the market ledger; `analyze.py:67` writes `market_timeline.parquet`. Outcomes 0/1 are called YES/NO for notation only; they are the listed teams, not necessarily radiant/dire. The parquet records before/after quantities, pairs, signed net inventory, own-last-fill value, cumulative buy cost and cash out, average-cost outstanding basis, and taker reference features. All fills are executions; no order-ID/submission/quote history is assumed.

BUY adds reported shares and cash cost. MERGE subtracts its cash/size amount from each token and releases $1 per pair. SELL subtracts sold shares and adds reported cash. REDEEM adds winning payout and clears both outcomes **economically**, treating losing tokens as worthless; this does not establish which losing tokens were physically burned. FIFO/lot selection does not affect net shares, but outstanding basis here uses moving average cost per token.

**Verified caveat:** Eleven conditions need inventory absent from the zero-start ledger, or have an unexplained quantity discrepancy (`supplement_summary.json:opening_markets`). Ten are September 7 conditions at the left boundary; one is `cs2-mandl-orgles-2026-09-22-game1`. Fourteen MERGE rows exceed reconstructed available pairs; all belong to those flagged markets, and none remains after exclusion. Their in-window BUY cost is $10,356.50, 0.106% of all BUY cost. “Inventory-complete” below means no such deficit detected; it cannot prove absence of unobserved transfers. Three apparent maker fee exceptions are all at the window's first seconds on `cs2-oldmix-pre-2026-09-07`, consistent with a role-matching boundary problem. There are 386 same-second merge/BUY ties (5.69% of merge events) and no same-second redemption/BUY ties (`final_stats.json:cash_same_second_buy`); taker categories also receive a strictly-earlier-second sensitivity check.

**Verified boundary:** `positions.json` has 500 rows and `has_more: true`. Twenty have positive current value, totalling $8,827.48; its cursor says sorting is CURRENT_VALUE descending and has reached value zero. **Likely:** those 500 rows capture the economically material positive values, but a complete fetch and an opening snapshot are absent. The PnL response labels `source_fidelity: 1d`; many requested hourly rows repeat a daily source value. Weekly figures must not be interpreted as precise intraday marks.

### 1. Merge policy

**Verified:** `analyze.py:43` records pairs before each merge, merged amount, first time since the previous merge that pair inventory reached that amount, lag to execution, previous-merge interval, and whether another BUY follows. The lag is **not the age of all merged pairs**: earlier-created pairs can wait much longer. `merge_metrics.parquet` has all 6,784 events.

Aggregate: merge size p10/p50/p90 = **51.5 / 531.4 / 3,941.4 pairs**; first-amount-crossing lag = **10 / 72 / 1,416 seconds**; repeated-market merge interval = **157 / 600 / 2,759 seconds**. 1,227 merges are below 100 pairs, 111 below 10, one exactly 1, none below 1. At median the merge removes 100% of available pairs; 89.79% of inventory-complete events remove at least 98%. Continued fills between amount selection and chain execution can explain some non-total sweeps, but intent is unobserved.

**Verified table:** `merge_by_game_kind.csv`; seconds are wall-clock, not game time.

| game | kind | n | usdc | pairs p50 | lag s p50 | interval s p50 | interval s p90 | mid_% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CS2 | map | 2556 | 3,930,460.91 | 575.48 | 76.00 | 420.00 | 2,271.60 | 66.71 |
| CS2 | series | 2694 | 2,846,066.77 | 367.21 | 85.00 | 721.00 | 3,001.40 | 83.85 |
| LoL | map | 566 | 846,166.21 | 709.70 | 49.00 | 711.00 | 2,640.00 | 61.84 |
| LoL | series | 500 | 335,551.74 | 371.79 | 82.50 | 1,141.00 | 3,012.20 | 76.60 |
| Valorant | map | 228 | 871,711.34 | 3,858.64 | 31.50 | 360.00 | 1,140.00 | 89.47 |
| Valorant | series | 240 | 410,148.33 | 1,193.97 | 39.00 | 431.00 | 1,380.90 | 96.67 |

**Verified:** `supplement.py:14` measures transaction scheduling. Of 1,218 transactions, 94.75% include multiple markets; batch-size p50/p90/max = 5/11/12. 77.42% execute at minute second 5–10, and 77.90% of consecutive transaction gaps are within three seconds of a multiple of 60. Common exact gaps include 180 seconds (61), 120 (45), 300 (39) and 240 (37), alongside 3-second gaps (41). This pattern supports a minute-based scanner plus transaction/batch execution delays. **Likely:** the maximum twelve markets is an implementation batch limit; the activity cannot establish whether gas, Safe payload size, or a chosen cap causes it. **Speculative:** eligibility may depend on age, size, liquidity, or a gas/priority calculation; one cannot infer the full selection rule from executed merges.

**Verified worked comparison:** G2–PARIVISION series (2026-10-06, condition `0xc57c2f14b4eeaa48d8dc6d41590c80c8edd4b512e4dfe5910f81dcb34d1c9168`) has 1,150 BUYs over 194.25 minutes, $204,017 cost, 50 merges, $206,917 merge return, $2,899 cash profit and $16,427 peak positive unreturned market cash. Merge size median 2,538 pairs; interval median 180 seconds; 49 of 50 merges precede the last BUY. In contrast, paiN–RED game 2 (2026-09-20, condition `0x84f15f73b3b66d7d91dd73d922129e2c682a7b902e3f45fc4c385690fd4d9ee8`) has 339 BUYs over 29.67 minutes and $2,846 cost. Its first merge returns 1,882.87 pairs 18.45 minutes after first BUY; its second returns 1,042.30 pairs 53.47 minutes after first BUY, 23.8 minutes after last BUY. The second amount sat available for 1,428 seconds. Thus a fixed timer that always merges every nonzero market each minute is contradicted, while larger volume and longer duration explain much of the 50-versus-two difference. Exact eligibility remains open. Sources: `supplement.py:35`, the two `*_merges.csv` files and `final_stats.py:17`.

**Verified paired-return decomposition:** On inventory-complete markets, $9.225M pairs are MERGEd; 498,084 common pairs remain immediately before REDEEM. **94.88%** of common paired returns therefore go through MERGE. That is a return-route statistic, not the fraction of all purchased shares paired. Across all markets, $7.670M / $9.240M = **83.01% of merged dollars** and 5,133 / 6,784 = 75.66% of merge rows precede the last BUY. The rest include late/post-trading sweeps. REDEEM returns $612,613 overall; part is paired inventory and part is an unpaired winning residual. Source: `final_stats.py:9`, `final_stats.json:paired_returns` and `analysis_summary.json:merges`.

### 2. Inventory skew and whether adding slows down

**Verified:** BUY-fill-sampled after-fill |net| has median/p95/p99/max **119.8 / 1,433.1 / 3,367.9 / 21,096.8 shares**. Own-last-fill excess-token value has **$56.12 / $686.76 / $1,866.51 / $9,586.62**. This is a stale execution-price mark, not a contemporaneous liquidation value. Across markets, peak |net| median/p95 = 276 / 2,738 shares; peak value median/p95 = $165 / $1,438. Peak excess-token value divided by total lifetime market buy cost has median 13.1%, p90 57.3%, p95 87.5%; the denominator is turnover, not deposited capital. Sources: `analyze.py:72`, `analysis_summary.json:inventory`; game table from `quality.py:19`.

| game | |net| p50 | |net| p95 | |net| max | excess $ p95 |
| --- | --- | --- | --- | --- |
| CS2 | 137.76 | 1,858.60 | 21,096.75 | 904.93 |
| LoL | 83.40 | 491.87 | 13,283.99 | 251.29 |
| Valorant | 524.75 | 2,363.06 | 6,077.62 | 1,425.17 |

**Verified fill asymmetry:** For inventory-complete market time between first and last BUY, exposure seconds in a net-size band are assigned to the existing imbalance; adding/reducing BUY counts then share the same exposure denominator. The table shows fills per minute and median time since the previous fill on the purchased side. Sources: `analyze.py:83`, `inventory_fill_rates.csv`. An adding fill increases |net| by construction; a reducing-side fill may cross through zero.

| game | net_band | direction | fills | fills_per_min | same_side_gap_p50 |
| --- | --- | --- | --- | --- | --- |
| CS2 | (-0.001, 100.0] | adding | 42268 | 0.92 | 6.00 |
| CS2 | (-0.001, 100.0] | reducing | 42542 | 0.92 | 6.00 |
| CS2 | (100.0, 500.0] | adding | 39616 | 0.96 | 5.00 |
| CS2 | (100.0, 500.0] | reducing | 54281 | 1.32 | 9.00 |
| CS2 | (1000.0, 2500.0] | adding | 6513 | 1.93 | 4.00 |
| CS2 | (1000.0, 2500.0] | reducing | 8432 | 2.50 | 5.00 |
| CS2 | (2500.0, 5000.0] | adding | 1885 | 1.78 | 3.00 |
| CS2 | (2500.0, 5000.0] | reducing | 3512 | 3.32 | 4.00 |
| LoL | (-0.001, 100.0] | adding | 34895 | 2.06 | 2.00 |
| LoL | (-0.001, 100.0] | reducing | 39464 | 2.33 | 2.00 |
| LoL | (100.0, 500.0] | adding | 19941 | 2.88 | 1.00 |
| LoL | (100.0, 500.0] | reducing | 30304 | 4.38 | 2.00 |
| LoL | (1000.0, 2500.0] | adding | 85 | 4.58 | 0.00 |
| LoL | (1000.0, 2500.0] | reducing | 397 | 21.40 | 1.00 |
| LoL | (2500.0, 5000.0] | reducing | 71 | 36.41 | 0.00 |
| Valorant | (-0.001, 100.0] | adding | 1045 | 1.82 | 8.00 |
| Valorant | (-0.001, 100.0] | reducing | 1028 | 1.79 | 6.00 |
| Valorant | (100.0, 500.0] | adding | 3837 | 1.93 | 6.00 |
| Valorant | (100.0, 500.0] | reducing | 4051 | 2.03 | 6.00 |
| Valorant | (1000.0, 2500.0] | adding | 1778 | 2.17 | 3.00 |
| Valorant | (1000.0, 2500.0] | reducing | 2528 | 3.09 | 5.00 |
| Valorant | (2500.0, 5000.0] | adding | 265 | 2.28 | 2.00 |
| Valorant | (2500.0, 5000.0] | reducing | 565 | 4.86 | 5.00 |

**Likely:** Inventory management is asymmetric: at 2,500–5,000 shares the reducing/adding fill-rate ratio is 1.86× in CS2 and 2.13× in Valorant; LoL has 71 reducing fills and zero adding fills in that band. At 1,000–2,500 shares LoL is 4.67×. These observations support suppressing the adding side and/or making the reducing side more competitive when skew is large.

**Not verified as a causal quote rule:** Adding-side absolute fill rates do not fall monotonically with |net|. Larger/liquid markets can have both higher inventory and faster fills; CS2 adding rates rise from 0.92/min below 100 shares to 1.78/min at 2,500–5,000. Median same-side waiting times are often shorter on the adding side because of same-side bursts. Fill-only conditioning does not separate quote suppression, changing market price, opposing flow, or increased reducing-side taker aggression. There is no demonstrated universal share cap: peak net inventory correlates 0.833 with market buy cost, consistent with size scaling. A risk cap should be dollar- and liquidity-aware, not copied from the observed 120-share median. Source: `supplement_summary.json:correlations`.

### 3. Taker categories and observable triggers

**Verified classification:** Normalize the previous fill in either outcome to an outcome-0 price; invert back to the token now purchased. If within 60 seconds, compare with the current own-token execution price. Categories are mutually exclusive: (a) buys the minority token when net before is nonzero; (b) otherwise buys after its own execution price increases ≥2 cents; (c) other. “Flatten” here means minority-side buying, **not proof of intent or guaranteed flattening**. Prices of fills are not reference midpoints, and a large taker execution can itself cause a jump. Source: `analyze.py:31`, `analyze.py:69`, `taker_classes.csv`.

Across all 81,382 taker BUYs ($4.749M), minority-side trades account for 42,926 fills / $2.760M / 58.11% of dollars; adding/neutral + upward jump trades account for 9,746 / $659,262 / 13.88%; other 28,710 / $1.330M / 28.01%.

| game | taker_class | fills | shares | cost | cost_%_within_game | fill_%_within_game | size_p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CS2 | adding/neutral + upward jump >=2c | 6665 | 881,789.84 | 478,067.78 | 13.24 | 11.13 | 40.00 |
| CS2 | other | 21841 | 2,093,519.99 | 1,015,607.64 | 28.13 | 36.46 | 33.00 |
| CS2 | reducing inventory | 31390 | 4,138,776.20 | 2,116,218.16 | 58.62 | 52.41 | 40.00 |
| LoL | adding/neutral + upward jump >=2c | 2662 | 190,047.86 | 97,666.16 | 18.47 | 15.41 | 36.00 |
| LoL | other | 5513 | 283,345.44 | 128,521.79 | 24.31 | 31.92 | 24.99 |
| LoL | reducing inventory | 9095 | 535,785.07 | 302,470.99 | 57.21 | 52.66 | 28.30 |
| Valorant | adding/neutral + upward jump >=2c | 419 | 152,412.78 | 83,528.55 | 13.68 | 9.94 | 155.00 |
| Valorant | other | 1356 | 341,789.02 | 186,115.49 | 30.47 | 32.16 | 168.43 |
| Valorant | reducing inventory | 2441 | 702,132.02 | 341,085.49 | 55.85 | 57.90 | 167.00 |

**Verified:** 90.49% of minority-side taker fills reduce absolute share net after the trade; the remaining 9.51% overshoot far enough to increase |net|. For maker minority-side fills the corresponding reduction share is 97.00%. Taker BUYs occur a median 18 seconds after the previous maker fill, p90 181 seconds and p95 336 seconds. 86.75% of taker fills / 92.44% of taker dollars have a previous own-market fill within 60 seconds. Counting jumps regardless of inventory direction, 34.99% of taker dollars follow a ≥2-cent own-price increase and 13.28% follow ≥5 cents. Sources: `supplement.py:17`, `supplement_summary.json:directions,taker_references`.

**Verified sensitivity:** Using the previous strictly earlier second instead of within-second fill order gives minority-side dollar share **59.81%**, total ≥2-cent jump share **37.23%**, and non-minority jump class **14.49%**. The broad result survives unresolved intra-second order. Source: `quality.py:12`, `quality_summary.json:taker_strict`.

**Likely policy:** Taker activity is a mix of inventory repair and responsive repricing, with substantial other demand. Pure “taker only to flatten” is inconsistent with $1.957M adding-side taker BUYs plus $32.4k neutral taker BUYs. Exact trigger thresholds, whether he follows a scoreboard/model, and profitability of each taker category require independent book/game-state references; own fills cannot establish them.

### 4. Sizing, partial executions and grouping limits

**Verified:** Fill sizes differ substantially by game. Sources: `analyze.py:91`, `size_game_kind.csv`, `size_game_role.csv`.

| game | kind | fills | size_p50 | size_p90 | cost_p50 |
| --- | --- | --- | --- | --- | --- |
| CS2 | map | 112557 | 25.00 | 142.85 | 9.60 |
| CS2 | series | 102997 | 20.00 | 88.54 | 7.80 |
| LoL | map | 85199 | 15.00 | 50.00 | 5.76 |
| LoL | series | 46330 | 10.00 | 40.00 | 4.70 |
| Valorant | map | 13548 | 90.00 | 300.00 | 31.50 |
| Valorant | series | 7262 | 77.21 | 300.00 | 26.00 |

| game | role | fills | size_p50 | size_p90 | cost_p50 |
| --- | --- | --- | --- | --- | --- |
| CS2 | MAKER | 155658 | 20.00 | 67.23 | 6.65 |
| CS2 | TAKER | 59896 | 39.80 | 180.00 | 16.08 |
| LoL | MAKER | 114259 | 10.00 | 40.00 | 4.80 |
| LoL | TAKER | 17270 | 28.35 | 130.00 | 14.23 |
| Valorant | MAKER | 16594 | 76.63 | 225.00 | 25.20 |
| Valorant | TAKER | 4216 | 166.92 | 671.32 | 69.81 |

**Verified price-level sizing:** `final_stats.py:21`, `size_price_exact.csv`; bins are nominal deciles with tiny price-rounding tolerance at boundaries.

| bucket | size_p50 | size_p90 | cost_p50 |
| --- | --- | --- | --- |
| 0.0–0.1 | 18.00 | 80.00 | 0.75 |
| 0.1–0.2 | 16.00 | 85.46 | 2.20 |
| 0.2–0.3 | 17.00 | 87.50 | 4.12 |
| 0.3–0.4 | 18.00 | 98.30 | 6.06 |
| 0.4–0.5 | 18.00 | 99.98 | 8.20 |
| 0.5–0.6 | 18.95 | 98.18 | 10.25 |
| 0.6–0.7 | 18.00 | 100.00 | 12.02 |
| 0.7–0.8 | 18.68 | 95.00 | 14.04 |
| 0.8–0.9 | 20.00 | 100.00 | 16.40 |
| 0.9–1.0 | 20.00 | 96.76 | 18.09 |

**Verified time-in-market sizing:** This is fractional wall-clock time between first and last observed BUY, not game-clock phase. The last quarter has 132,779 fills / $3.932M cost, versus 81,607 / $1.946M in the first quarter; median sizes change from 17.76 to 20 shares, and p90 from 89 to 105 shares. Long pauses and series structure affect these fractions. Source: `size_time.csv`.

**Verified grouping:** Grouping by condition + transaction hash + outcome + price produces 367,668 groups from 367,893 BUY rows, only 225 fewer. Median group size stays 18 shares; p99 675.03; maximum 18,596.93. Grouping by condition + second + outcome + price gives 349,992 groups, median 19.56 shares, p99 700, maximum 18,596.93; max rows per group 17. Thus neither grouping reconstructs total resting-order size. Repeated execution sizes are striking: CS2 5 shares (29,554 fills), 30 (20,796), 35 (18,849); LoL 8 (20,279), 10 (14,134), 18 (12,414); Valorant 150 (1,265), 101 (1,060), 300 (857). **Likely:** these reflect discrete sizing tiers and partial fills; they cannot prove submitted clips or a constant-$ formula. Sources: `analysis_summary.json:fills_tx_price,fills_second_price`, `quality_summary.json:size_heaping`.

### 5. Price bands and what PnL can be attributed to a fill price

**Verified:** All BUY execution prices are ≤0.95 in both roles and all three games. There are 2,087 exact-0.95 BUYs ($95,801), while 3,245 BUYs at ≤0.02 cost only $4,842.32. Minima: CS2 0.001, LoL 0.004, Valorant 0.0094474. A BUY of the cheap token supplies the economically equivalent high-price offer on the sibling token, so cheap execution does not imply willingness to BUY the expensive sibling at 0.98. **Likely:** 0.95 is an intentional BUY eligibility limit. Quote prices above it cannot be ruled out from unfilled orders. Sources: `quality.py:9`, `supplement_summary.json:price_extremes,low_high_2c`.

**Verified descriptive price table:** Cost and fees are directly observed. “Allocated cash PnL” distributes each market's cash net (`merge + redeem + sells − buys`) to its BUYs proportional to their cash cost, then aggregates by BUY-price decile. This explicitly avoids counting redeem twice. It is an approximate accounting allocation, **not identified return on fills in that bucket**; opening/terminal inventory and pairing choice affect market cash PnL. This allocation excludes the six conditions without any in-window BUYs. Independent price-bucket profitability is unverified. Sources: `quality.py:6`, `price_bands_exact.csv`.

| bucket | fills | cost | cost_% | fees | allocated_cash_net |
| --- | --- | --- | --- | --- | --- |
| 0.0–0.1 | 23022 | 57,928.99 | 0.60 | 815.80 | 1,688.49 |
| 0.1–0.2 | 33379 | 245,046.48 | 2.52 | 4,017.33 | 3,801.71 |
| 0.2–0.3 | 39839 | 504,813.52 | 5.19 | 8,413.08 | 6,692.25 |
| 0.3–0.4 | 47182 | 886,509.04 | 9.11 | 13,341.18 | 11,542.26 |
| 0.4–0.5 | 51319 | 1,283,062.59 | 13.18 | 16,022.35 | 12,641.65 |
| 0.5–0.6 | 50072 | 1,583,365.21 | 16.26 | 16,796.01 | 16,074.44 |
| 0.6–0.7 | 44682 | 1,672,827.81 | 17.18 | 14,957.15 | 20,973.68 |
| 0.7–0.8 | 37006 | 1,496,455.56 | 15.37 | 9,538.23 | 18,640.24 |
| 0.8–0.9 | 28424 | 1,341,948.29 | 13.78 | 5,182.58 | 18,982.52 |
| 0.9–1.0 | 12968 | 663,252.79 | 6.81 | 1,148.48 | 6,899.21 |

**Verified terminal-execution association:** Here “terminal” means the last observed fill normalized to outcome-0 price, not the final settlement probability. A last fill ≤0.05 has median pair-average cost 0.97174, with 20.9% above one; >0.95 has median 0.97252, with 20.6% above one. Midrange 0.20–0.80 has median 0.99662, with 45.5% above one. Thus near-extreme terminal own-fill prices do not generally coincide with worse lifetime pair cost. Unresolved markets and exposure differences can contribute. Source: `terminal_price_pair.csv`.

| terminal_band | markets | pair_p50 | over_one_share | cash_net |
| --- | --- | --- | --- | --- |
| (-0.001, 0.05] | 574 | 0.97 | 0.21 | 62,192.93 |
| (0.05, 0.2] | 315 | 0.99 | 0.42 | 6,974.71 |
| (0.2, 0.8] | 132 | 1.00 | 0.45 | -4,815.99 |
| (0.8, 0.95] | 360 | 0.99 | 0.43 | 494.02 |
| (0.95, 1.0] | 729 | 0.97 | 0.21 | 53,090.80 |
| — | 6 | — | 0.00 | 2,261.41 |

### 6. Pair-cost losers versus cash losers, and ten worked markets

**Verified:** Among 2,060 inventory-complete two-sided markets, 614 (29.81%) have lifetime average cost of outcome 0 plus outcome 1 above $1. This is not the same as realized loss: among 361 such markets with REDEEM, 116 still have positive cash net. Conversely 23 redeemed markets with pair-average cost below $1 have negative cash net. Unpaired residuals and unequal side quantities explain why comparing two average prices is insufficient. Source: `supplement.py:20`, `supplement_summary.json:pair_over_one`.

**Verified distinguishing features:** Pair-cost >1 markets have higher median taker cost shares in every game/kind group, especially LoL maps (55.6% versus 35.6%) and CS2 series (59.9% versus 49.9%). They do not consistently have longer durations or larger net peaks. The pooled correlation of pair cost with taker share is only 0.099, with duration 0.049 and cost 0.028; this is association, not attribution of every loss to taker use. Source: `pair_loss_comparison.csv`, `supplement_summary.json:correlations`.

| game | kind | loser_pair | markets | cost | cash_net | duration_p50 | taker_%_p50 | peak_net_p50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CS2 | map | False | 642 | 2,995,781.00 | 77,052.47 | 36.92 | 55.36 | 300.82 |
| CS2 | map | True | 334 | 1,061,938.12 | -37,359.83 | 38.08 | 64.44 | 320.96 |
| CS2 | series | False | 367 | 2,275,329.33 | 40,695.94 | 107.82 | 49.90 | 279.49 |
| CS2 | series | True | 159 | 654,266.54 | -9,967.40 | 99.77 | 59.86 | 274.40 |
| LoL | map | False | 238 | 769,210.61 | 17,162.84 | 27.32 | 35.60 | 257.35 |
| LoL | map | True | 86 | 242,493.67 | -4,355.38 | 25.92 | 55.59 | 188.00 |
| LoL | series | False | 149 | 350,388.42 | 8,414.80 | 78.18 | 34.76 | 180.99 |
| LoL | series | True | 20 | 54,850.88 | -787.46 | 63.64 | 42.23 | 193.34 |
| Valorant | map | False | 33 | 751,210.62 | 17,374.22 | 48.38 | 35.96 | 2,111.72 |
| Valorant | map | True | 10 | 142,470.37 | -1,709.47 | 45.72 | 41.11 | 1,829.18 |
| Valorant | series | False | 17 | 357,050.70 | 8,314.10 | 119.70 | 31.42 | 1,980.02 |
| Valorant | series | True | 5 | 68,200.83 | -2,163.47 | 83.75 | 32.62 | 766.86 |

**Verified worked-example selection:** The five most-negative cash-net inventory-complete redeemed markets with pair cost >1, and five most-positive redeemed markets with pair cost <1. This is intentionally an extreme sample, not representative prevalence. Full event ledgers and transaction hashes are preserved in each `work/sol-policy/<slug>_timeline.csv`. Phase prices below are cash-cost-weighted **per share**, including fees; each phase uses a quarter of first-to-last-BUY wall-clock duration. The signed net is measured at that phase's final BUY, so SELLs/returns after that BUY can change terminal exposure. Source: `analyze.py:127`, `supplement.py:38`, `worked_examples.json`.

| example | slug | buy_cost | cash_net | pair_avg_cost | taker_% | duration_min | merge_count | redeem_usdc | peak_net |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| loss | cs2-m80-tyloo-2026-10-05-game2 | 34,666.91 | -944.96 | 1.03 | 25.68 | 35.30 | 10 | 52.03 | 4,697.86 |
| loss | cs2-9z-navi-2026-10-05-game2 | 36,515.80 | -542.31 | 1.03 | 37.93 | 42.28 | 7 | 1,484.27 | 5,609.28 |
| loss | cs2-navij1-bw-2026-09-12-game2 | 5,186.85 | -540.56 | 1.09 | 77.03 | 64.80 | 2 | 815.42 | 631.34 |
| loss | cs2-thegol-pivsta-2026-09-10-game1 | 2,945.73 | -398.73 | 1.17 | 70.71 | 39.25 | 2 | 104.46 | 2,026.78 |
| loss | cs2-bb3-m80-2026-10-04 | 33,412.60 | -384.11 | 1.03 | 64.46 | 133.20 | 29 | 1,538.39 | 6,687.26 |
| win | cs2-fal2-navi-2026-10-06 | 95,996.42 | 3,636.60 | 0.96 | 57.10 | 189.35 | 39 | 474.49 | 8,351.30 |
| win | val-vit-loud-2026-09-30-game2 | 64,855.78 | 2,674.86 | 0.96 | 53.13 | 83.38 | 12 | 2,001.73 | 4,901.05 |
| win | cs2-9z-bb3-2026-10-06 | 52,789.17 | 2,286.86 | 0.96 | 49.38 | 107.30 | 25 | 587.52 | 8,857.23 |
| win | cs2-aur1-bb3-2026-10-05 | 81,708.56 | 1,911.52 | 0.97 | 48.02 | 168.67 | 27 | 1,275.60 | 5,646.16 |
| win | val-kc3-ns1-2026-10-03 | 65,994.88 | 1,681.34 | 0.98 | 47.62 | 162.95 | 31 | 629.84 | 5,341.74 |

#### Losing example: Counter-Strike: M80 vs TYLOO - Map 2 Winner

**Verified timeline:** `cs2-m80-tyloo-2026-10-05-game2`; condition `0x9b87e9b200c732fcaea21e325784e67d4e3c0faf9ad272ab46a0865ac2416ac3`. First/last BUY UTC: 2026-10-05 15:13:52 / 2026-10-05 15:49:10. M80 prices trend upward. Early minority imbalance is on TYLOO; he repairs it by buying M80 at progressively higher prices. Both first-half phase pair sums exceed one, and the final-quarter sum is 1.0138. A 25.7% taker share is not extreme, showing that maker executions alone do not eliminate adverse-selection losses.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 8.65 | 10,468.40 | 0.54 | 11,838.80 | 0.48 | 1.02 | 3,850.18 | -1,370.40 |
| 1 | 8.97 | 17.50 | 7,995.98 | 0.66 | 7,926.97 | 0.37 | 1.04 | 1,122.24 | -1,301.39 |
| 2 | 18.60 | 26.25 | 7,117.04 | 0.83 | 5,575.93 | 0.16 | 0.99 | 169.84 | 239.72 |
| 3 | 27.10 | 35.30 | 8,140.53 | 0.93 | 8,328.22 | 0.08 | 1.01 | 3,761.67 | 52.03 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-05 15:18:07 | 4,145.98 | 4,145.98 | -1,501.73 |
| middle MERGE | 2026-10-05 15:36:07 | 3,044.53 | 3,044.53 | -49.77 |
| last MERGE | 2026-10-05 15:53:07 | 2,293.08 | 2,293.08 | 52.03 |
| REDEEM | 2026-10-05 16:31:08 | 52.03 | -0.00 | 52.03 |

Cash route: 10 merges return $33,669.92; redemption $52.03; 0 SELL fills return $0.00. Cash net $-944.96. Complete per-event ledger: `work/sol-policy/cs2-m80-tyloo-2026-10-05-game2_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Losing example: Counter-Strike: 9z vs Natus Vincere - Map 2 Winner

**Verified timeline:** `cs2-9z-navi-2026-10-05-game2`; condition `0x72f890b6e0eac0e6a297f6aef94034748c8574f5dbd0d5c9b9352d942cd7d357`. First/last BUY UTC: 2026-10-05 13:18:53 / 2026-10-05 14:01:10. 9z initially accumulates a positive net, then its execution prices fall sharply. Third-quarter average cost is 0.1214 for 9z and 0.9015 for NAVI; he reverses skew while continuing both-sided buying. The NAVI winning residual returns $1,484, but cash loss remains $542.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 10.35 | 19,897.25 | 0.41 | 18,215.93 | 0.57 | 0.97 | 4,858.87 | 1,681.32 |
| 1 | 10.73 | 20.80 | 7,349.28 | 0.39 | 6,264.17 | 0.65 | 1.04 | 4,071.74 | 2,766.44 |
| 2 | 21.93 | 31.53 | 3,847.80 | 0.12 | 8,393.17 | 0.90 | 1.02 | 4,689.71 | -1,778.93 |
| 3 | 31.83 | 42.28 | 3,394.88 | 0.11 | 3,100.22 | 0.89 | 1.00 | 229.98 | -1,484.27 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-05 13:19:07 | 1,407.74 | 1,407.74 | 1,985.59 |
| middle MERGE | 2026-10-05 13:29:05 | 6,459.25 | 6,459.25 | 1,613.92 |
| last MERGE | 2026-10-05 14:06:10 | 3,544.88 | 3,544.88 | -1,484.27 |
| REDEEM | 2026-10-05 14:34:56 | 1,484.27 | -0.00 | -1,484.27 |

Cash route: 7 merges return $34,489.21; redemption $1,484.27; 0 SELL fills return $0.00. Cash net $-542.31. Complete per-event ledger: `work/sol-policy/cs2-9z-navi-2026-10-05-game2_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Losing example: Counter-Strike: NAVI Junior vs Bushido Wildcats - Map 2 Winner

**Verified timeline:** `cs2-navij1-bw-2026-09-12-game2`; condition `0x3bb5b474a47a0b7404b9e05b9fee3e3171df7b55d7a9dc90c6020af8daa58552`. First/last BUY UTC: 2026-09-12 18:52:39 / 2026-09-12 19:57:27. A long, taker-heavy map: 77.0% taker cost, with second-quarter phase pair sum 1.1661 and later sums above 1.07. Last-BUY net is +240 outcome-0 shares; winner is the other outcome. $815 redemption cannot offset the expensive matched inventory.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 14.62 | 509.91 | 0.55 | 462.47 | 0.48 | 1.02 | 298.09 | 47.43 |
| 1 | 17.57 | 32.25 | 744.94 | 0.55 | 900.37 | 0.61 | 1.17 | 645.71 | -107.99 |
| 2 | 32.40 | 47.20 | 1,758.02 | 0.58 | 1,573.32 | 0.51 | 1.09 | 1,316.98 | 76.71 |
| 3 | 48.67 | 64.80 | 1,873.35 | 0.38 | 1,710.13 | 0.70 | 1.07 | 1,734.47 | 239.93 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-09-12 19:30:07 | 1,677.21 | 1,691.03 | -518.45 |
| middle MERGE | 2026-09-12 19:49:07 | 2,153.66 | 2,153.66 | 36.55 |
| last MERGE | 2026-09-12 19:49:07 | 2,153.66 | 2,153.66 | 36.55 |
| REDEEM | 2026-09-12 22:02:51 | 815.42 | 815.42 | 239.93 |

Cash route: 2 merges return $3,830.87; redemption $815.42; 0 SELL fills return $0.00. Cash net $-540.56. Complete per-event ledger: `work/sol-policy/cs2-navij1-bw-2026-09-12-game2_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Losing example: Counter-Strike: The Golden Horde vs pivstar - Map 1 Winner

**Verified timeline:** `cs2-thegol-pivsta-2026-09-10-game1`; condition `0x3d8fa966a2e69dd487cc17da06c07026e85d6fa80b2c3fa0a20e7232d8ba3e54`. First/last BUY UTC: 2026-09-10 15:36:15 / 2026-09-10 16:15:30. This is the clearest expensive inventory repair: first half buys only about 50 outcome-0 shares against 1,837 outcome-1 shares. Later he buys 546 at 0.8536 and 1,951 at 0.8017 on outcome 0. The favored side wins, but pair cost reaches 1.1715 and a $104 residual redemption leaves a $399 loss.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 9.52 | 25.00 | 0.51 | 443.18 | 0.44 | 0.95 | 194.41 | -418.18 |
| 1 | 9.87 | 18.95 | 25.00 | 0.60 | 1,393.94 | 0.41 | 1.01 | 406.05 | -1,787.12 |
| 2 | 19.80 | 29.20 | 546.29 | 0.85 | 300.32 | 0.26 | 1.11 | 323.41 | -1,541.14 |
| 3 | 30.92 | 39.25 | 1,950.71 | 0.80 | 305.11 | 0.13 | 0.93 | 1,159.13 | 104.46 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-09-10 15:48:07 | 25.00 | 25.00 | -948.62 |
| middle MERGE | 2026-09-10 16:16:13 | 2,417.55 | 2,417.55 | 104.46 |
| last MERGE | 2026-09-10 16:16:13 | 2,417.55 | 2,417.55 | 104.46 |
| REDEEM | 2026-09-10 22:44:19 | 104.46 | -0.00 | 104.46 |

Cash route: 2 merges return $2,442.54; redemption $104.46; 0 SELL fills return $0.00. Cash net $-398.73. Complete per-event ledger: `work/sol-policy/cs2-thegol-pivsta-2026-09-10-game1_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Losing example: Counter-Strike: BetBoom Team vs M80 (BO3) - ESL Pro League Group Stage

**Verified timeline:** `cs2-bb3-m80-2026-10-04`; condition `0xc83cda6de1dcc36e76dd2ec6b3d16afb071bc546a90168250c946dd684dfae25`. First/last BUY UTC: 2026-10-04 17:11:08 / 2026-10-04 19:24:20. The second quarter spends $15,838 as taker while flipping net from −3,488 to +420 shares; its phase pair sum is 1.0516. Later quarters are below one, but the early repair cost and fees leave a $384 loss despite $1,538 winning redemption.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 32.28 | 4,089.34 | 0.61 | 7,576.87 | 0.37 | 0.98 | 2,084.60 | -3,487.53 |
| 1 | 34.78 | 63.70 | 19,554.34 | 0.67 | 15,646.74 | 0.39 | 1.05 | 15,838.06 | 420.07 |
| 2 | 83.48 | 99.48 | 2,996.80 | 0.85 | 1,690.38 | 0.13 | 0.98 | 869.15 | 1,726.49 |
| 3 | 103.63 | 133.20 | 6,388.01 | 0.85 | 6,576.11 | 0.13 | 0.98 | 2,746.78 | 1,538.39 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-04 17:15:08 | 161.00 | 161.00 | -3,540.70 |
| middle MERGE | 2026-10-04 18:08:08 | 1,336.74 | 1,336.74 | -2,084.45 |
| last MERGE | 2026-10-04 19:42:11 | 234.00 | 234.00 | 1,538.39 |
| REDEEM | 2026-10-04 19:57:01 | 1,538.39 | -0.00 | 1,538.39 |

Cash route: 29 merges return $31,490.10; redemption $1,538.39; 0 SELL fills return $0.00. Cash net $-384.11. Complete per-event ledger: `work/sol-policy/cs2-bb3-m80-2026-10-04_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Winning example: Counter-Strike: Team Falcons vs Natus Vincere (BO3) - ESL Pro League Group Stage

**Verified timeline:** `cs2-fal2-navi-2026-10-06`; condition `0x16bf6187b713dc4c937599080f8063ba55beb46a6b3508e895d4f1114fd4fa48`. First/last BUY UTC: 2026-10-06 19:05:39 / 2026-10-06 22:15:00. A winning series despite 57.1% taker dollars: all four phase pair sums are below one. Net flips from a −6,124 first-quarter residual toward the winning side. Six SELL events return $2,135.35; merges plus redeem alone would understate cash profit. SELLs are rare globally but matter in this example.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 46.45 | 28,476.03 | 0.62 | 34,600.04 | 0.36 | 0.97 | 16,629.58 | -6,124.02 |
| 1 | 47.48 | 93.65 | 15,275.14 | 0.86 | 10,428.14 | 0.08 | 0.94 | 5,906.71 | -1,277.02 |
| 2 | 97.13 | 141.73 | 37,306.04 | 0.81 | 37,346.78 | 0.14 | 0.95 | 21,155.62 | -1,317.76 |
| 3 | 144.68 | 189.35 | 19,440.48 | 0.60 | 14,648.23 | 0.34 | 0.95 | 11,118.04 | 3,474.49 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-06 19:09:08 | 2,279.30 | 2,279.30 | -3,694.54 |
| middle MERGE | 2026-10-06 20:38:06 | 1,911.90 | 2,687.59 | -499.02 |
| last MERGE | 2026-10-06 22:23:06 | 2,792.10 | 2,792.10 | 3,474.49 |
| REDEEM | 2026-10-06 23:15:49 | 474.49 | -0.00 | 474.49 |

Cash route: 39 merges return $97,023.19; redemption $474.49; 6 SELL fills return $2,135.35. Cash net $3,636.60. Complete per-event ledger: `work/sol-policy/cs2-fal2-navi-2026-10-06_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Winning example: Valorant: Team Vitality vs LOUD - Map 2 Winner

**Verified timeline:** `val-vit-loud-2026-09-30-game2`; condition `0x6e0ffdae6d4d979e1243eb1cbf5d9c1e3c48e2bcf877ce81025fde23facaa640`. First/last BUY UTC: 2026-09-30 10:03:12 / 2026-09-30 11:26:35. Both sides continue through major price reversal. Phase pair sums are 0.9704, 0.9763, 0.9350 and 0.9577, all favorable. A 53.1% taker share coexists with $2,675 cash profit; high taker use is not inherently loss-making.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 20.48 | 10,140.90 | 0.56 | 9,462.20 | 0.41 | 0.97 | 2,517.49 | 678.70 |
| 1 | 21.63 | 41.65 | 6,720.94 | 0.79 | 5,655.60 | 0.19 | 0.98 | 1,044.50 | 1,744.05 |
| 2 | 41.95 | 62.15 | 19,692.14 | 0.49 | 21,783.61 | 0.44 | 0.94 | 11,488.04 | -347.43 |
| 3 | 62.83 | 83.38 | 30,976.65 | 0.46 | 30,847.33 | 0.50 | 0.96 | 19,407.90 | -218.10 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-09-30 10:10:09 | 5,869.91 | 5,869.91 | 829.07 |
| middle MERGE | 2026-09-30 11:05:18 | 4,654.96 | 4,654.96 | -79.87 |
| last MERGE | 2026-09-30 11:25:09 | 3,032.90 | 3,032.90 | 801.20 |
| REDEEM | 2026-09-30 11:44:47 | 2,001.73 | 2,001.73 | -218.10 |

Cash route: 12 merges return $65,528.91; redemption $2,001.73; 0 SELL fills return $0.00. Cash net $2,674.86. Complete per-event ledger: `work/sol-policy/val-vit-loud-2026-09-30-game2_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Winning example: Counter-Strike: 9z vs BetBoom Team (BO3) - ESL Pro League Group Stage

**Verified timeline:** `cs2-9z-bb3-2026-10-06`; condition `0x13c6cc0af4836015fcfdab862cadc62b2117df300b69ba173416fcfe9110649b`. First/last BUY UTC: 2026-10-06 14:30:36 / 2026-10-06 16:17:54. Trading accelerates late: the final quarter buys 35,478 / 37,743 shares with $21,967 taker cost while its phase pair sum remains 0.9742. Net falls from +2,852 to +588 shares. $54,489 merges recycle capital and $588 redemption finishes a $2,287 cash gain.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 26.73 | 8,517.16 | 0.49 | 7,011.36 | 0.49 | 0.97 | 1,685.58 | 1,505.80 |
| 1 | 28.45 | 49.33 | 4,972.70 | 0.62 | 3,801.40 | 0.36 | 0.98 | 803.95 | 2,677.10 |
| 2 | 66.08 | 80.38 | 6,107.84 | 0.67 | 5,932.49 | 0.32 | 0.98 | 1,609.89 | 2,852.46 |
| 3 | 80.58 | 107.30 | 35,478.32 | 0.84 | 37,743.26 | 0.13 | 0.97 | 21,967.16 | 587.52 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-06 14:32:06 | 734.48 | 734.48 | -331.53 |
| middle MERGE | 2026-10-06 15:41:08 | 2,158.03 | 2,158.03 | 4,406.86 |
| last MERGE | 2026-10-06 16:18:08 | 4,809.45 | 4,809.45 | 587.52 |
| REDEEM | 2026-10-06 16:50:12 | 587.52 | 0.00 | 587.52 |

Cash route: 25 merges return $54,488.50; redemption $587.52; 0 SELL fills return $0.00. Cash net $2,286.86. Complete per-event ledger: `work/sol-policy/cs2-9z-bb3-2026-10-06_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Winning example: Counter-Strike: Aurora Gaming vs BetBoom Team (BO3) - ESL Pro League Group Stage

**Verified timeline:** `cs2-aur1-bb3-2026-10-05`; condition `0xfa9a81d8ed7204a9b1a8ef58c54cbb52638345d8e9d5de00ed4f8de6916d2b46`. First/last BUY UTC: 2026-10-05 17:22:52 / 2026-10-05 20:11:32. All quarter pair sums are below one. The final quarter buys roughly 52k shares on each side, at 0.8053 / 0.1581, while net stays close to −1,537. $82,344 merges plus $1,276 redeem produce $1,912 cash profit. There are no SELL fills in this market; the positive residual redemption coexists with a larger losing-token residual.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 38.20 | 9,852.84 | 0.71 | 10,144.22 | 0.26 | 0.97 | 4,028.03 | -291.37 |
| 1 | 55.67 | 83.32 | 4,502.47 | 0.79 | 4,166.03 | 0.20 | 0.99 | 0.00 | 45.07 |
| 2 | 85.90 | 126.07 | 16,904.94 | 0.70 | 18,516.07 | 0.29 | 0.99 | 8,911.06 | -1,566.07 |
| 3 | 126.97 | 168.67 | 52,359.83 | 0.81 | 52,330.84 | 0.16 | 0.96 | 26,294.22 | -1,537.07 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-05 17:25:07 | 2,000.00 | 2,000.00 | -382.60 |
| middle MERGE | 2026-10-05 18:51:07 | 2,217.99 | 2,217.99 | -430.54 |
| last MERGE | 2026-10-05 20:09:07 | 11,073.90 | 11,073.90 | 649.44 |
| REDEEM | 2026-10-05 20:43:23 | 1,275.60 | 1,275.60 | -1,537.07 |

Cash route: 27 merges return $82,344.49; redemption $1,275.60; 0 SELL fills return $0.00. Cash net $1,911.52. Complete per-event ledger: `work/sol-policy/cs2-aur1-bb3-2026-10-05_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

#### Winning example: Valorant: Karmine Corp vs Nongshim RedForce (BO3) - VCT Champions Group D

**Verified timeline:** `val-kc3-ns1-2026-10-03`; condition `0x40adae466285312ee71333d6a13ed125f8046b52fe5ef4bc71d3831fbc9a47ab`. First/last BUY UTC: 2026-10-03 11:26:51 / 2026-10-03 14:09:48. Winner reverses toward outcome 1. Final-quarter BUYs remain near-balanced (45,904 / 47,682 shares) at 0.3353 / 0.6339, a 0.9692 sum. Net flips to −630, and that winning residual is redeemed. Cash profit is $1,681.

| quarter | start_min | end_min | qty0 | avg0 | qty1 | avg1 | avg sum | taker_cost | net_end |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00 | 39.75 | 7,970.68 | 0.59 | 8,335.17 | 0.40 | 0.99 | 3,866.26 | -364.50 |
| 1 | 42.23 | 80.80 | 8,715.20 | 0.77 | 7,125.68 | 0.21 | 0.97 | 3,782.44 | 1,225.03 |
| 2 | 82.85 | 111.23 | 4,456.49 | 0.58 | 4,532.90 | 0.35 | 0.92 | 496.90 | 1,148.62 |
| 3 | 122.98 | 162.95 | 45,904.01 | 0.34 | 47,682.47 | 0.63 | 0.97 | 23,278.95 | -629.84 |

| event | UTC | return $ | pairs before | net before |
| --- | --- | --- | --- | --- |
| first MERGE | 2026-10-03 11:33:06 | 1,779.49 | 1,779.49 | -466.77 |
| middle MERGE | 2026-10-03 13:01:50 | 535.14 | 535.14 | 458.16 |
| last MERGE | 2026-10-03 14:11:57 | 2,455.24 | 2,455.24 | -629.84 |
| REDEEM | 2026-10-03 14:29:27 | 629.84 | -0.00 | -629.84 |

Cash route: 31 merges return $67,046.38; redemption $629.84; 0 SELL fills return $0.00. Cash net $1,681.34. Complete per-event ledger: `work/sol-policy/val-kc3-ns1-2026-10-03_timeline.csv`; phase and cash-event reconstruction: `worked_examples.json`.

### 7. Capital, concurrency and turnover

**Verified requested definition:** The one-minute grid now explicitly includes `requested_cost_minus_merge_redeem = cumulative buys − cumulative merges − cumulative redeems`, ignoring rare SELL proceeds exactly as the brief specifies. A separate `signed_unreturned` subtracts SELLs too. First/last grid: 2026-09-07 12:25 UTC through 2026-10-07 12:26 UTC, sampled at the end of all observed events up to that second. Source: `analyze.py:105`, `supplement.py:10`, `quality.py:15`, `final_stats.py:13`; final comprehensive grid is `capital_1min_scoped.parquet`.

This requested number becomes negative as cash profits accumulate; dividing weekly volume by its negative mean is economically meaningless. Profit withdrawal/deposit and opening available cash are unknown. It is a net funding cashflow, not “shares currently tying up capital.” Per-market clipping prevents profits in an earlier market from masking cash committed elsewhere, but it leaves fully realized losses as permanently positive cash deficits. Outstanding cost basis similarly retains worthless unredeemed loser dust unless one supplies settlement records. The first 500 current positions already contain 480 zero-value redeemable entries, illustrating the problem. Live bankroll must additionally fund outstanding orders, operational buffers and jumps, none of which is observed here.

| measure | mean | p95 | peak |
| --- | --- | --- | --- |
| Requested buys − merges − redeems | -37,948.88 | 1,410.11 | 22,921.76 |
| Signed including SELL receipts | -38,236.95 | 1,410.11 | 22,921.76 |
| Sum positive market cash deficits | 35,234.64 | 67,030.28 | 107,383.98 |
| Outstanding average-cost basis, all markets | 25,137.85 | 50,182.91 | 95,688.66 |
| Outstanding basis, inventory-complete | 28,423.75 | 53,428.10 | 98,933.86 |
| basis_active | 1,884.87 | 6,795.85 | 50,700.57 |
| basis_1h_tail | 3,369.53 | 9,498.64 | 53,494.39 |
| positive_active | 1,839.30 | 6,621.10 | 50,705.15 |
| positive_1h_tail | 3,300.28 | 9,299.42 | 53,508.46 |

**Verified concurrency:** Active means a market's observed BUY span includes that minute. Mean 2.88 markets, p95 9, p99 13, peak 25. It is neither count of live orders nor all unresolved holdings. **Bounded proxies:** `basis_active` removes exposure after a market's final BUY; `basis_1h_tail` keeps it for another hour. Both exclude quantity-incomplete markets. The first omits pending redemption/late merges, while the second uses a chosen expiry, so neither is an exact operational minimum. The remaining-basis-all-markets peak is $95.7k, while inventory-complete alone peaks at $98.9k because negative artifacts in incomplete markets otherwise offset the total.

**Verified weekly capital/turnover table:** Positive-market cash average below is the unexpired proxy. Turnover = observed weekly BUY cost / mean proxy dollars, including overnight idle minutes; W37 and W41 are partial activity weeks. Source: `activity_weekly.csv`, `capital_scoped_weekly.csv`.

| iso_week | buy_cost | mean_positive | turnover_positive | active_basis_mean | tail_basis_mean | turnover_tail | peak_active |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-W37 | 1,083,317.20 | 16,000.52 | 67.71 | 2,121.37 | 4,696.28 | 230.68 | 17 |
| 2026-W38 | 784,292.95 | 24,395.90 | 32.15 | 1,061.56 | 2,134.68 | 367.41 | 8 |
| 2026-W39 | 1,641,311.24 | 36,973.46 | 44.39 | 1,713.23 | 2,887.28 | 568.46 | 25 |
| 2026-W40 | 3,390,688.64 | 51,027.14 | 66.45 | 2,338.00 | 3,792.58 | 894.03 | 15 |
| 2026-W41 | 2,835,600.25 | 66,138.75 | 42.87 | 2,781.96 | 3,551.16 | 798.50 | 11 |

**Likely capital implication:** For the completed $3.391M W40, positive cash-deficit proxy averages $51.0k (66× weekly turnover), but active-plus-one-hour basis averages $3.79k (894×) and peaks $53.5k. The very high latter ratio reflects capital rotation and long periods with no trading. The example G2 $204k turnover uses only $16.4k peak positive market cash. These support aggressive recycling; they do not establish a $3M/week bot can safely run on a $3.8k bankroll. A conservative study should test at least peak concurrent holdings and outstanding order reserves, settlement delay, losses, and blocked merges.

### 8. PnL, fees and rebate reconciliation

**Verified cash bridge:** Source `analysis_summary.json:pnl_totals`, `positions_status.csv`. These values are window cashflows, not complete mark-to-market profit.

| component | USDC |
| --- | --- |
| BUY cash cost | -9,735,210.29 |
| MERGE returns | 9,240,105.30 |
| REDEEM returns | 612,613.19 |
| SELL returns | 2,689.67 |
| Net activity cash | 120,197.87 |
| Visible ending position value | 8,827.48 |
| Cash + visible ending positions | 129,025.35 |
| API realized PnL delta | 124,761.82 |
| API position PnL delta | 124,497.17 |
| API gross trade PnL delta | 214,604.95 |
| API fee delta | -90,107.78 |
| Activity maker rebates paid | 14,906.37 |
| Activity taker rebates paid | 37,031.52 |
| API rebate delta | 13,032.25 |

**Verified:** Cash + visible end value exceeds API position-PnL growth by $4,528.17. Without opening inventory value, exact agreement cannot be expected. Six redeem-only conditions contribute $2,261.41 of receipts for no in-window buys, and five more conditions have quantity deficits; end snapshots also differ by minutes/daily source points. **Speculative bridge:** an opening marked inventory value of about $4.5k, with no other timing or data differences, would close this gap. That opening value is not supplied or independently measured. Of current visible end value, $1,050.52 belongs to conditions outside the activity ledger and $7,776.96 to conditions inside it.

**Verified API boundary:** Nearest PnL points ≤activity endpoints are September 7 12:00 UTC and October 7 12:00 UTC. Their principal-volume delta is $9,605,792.28 versus $9,644,978.09 observed BUY price×shares; activity BUY cash includes fees and totals $9,735,210.29. The API trade-count delta is 368,030 versus 367,915 observed TRADE rows. Source-time mismatch and missing opening events preclude an exact row-for-row reconciliation. Over that API window gross edge = 2.234 cents/principal dollar, fee drag = 0.938 cents, position net = 1.296 cents and realized net = 1.299 cents. Realized and unrealized changes differ by $264.65.

**Verified taker fees:** BUY fee residual `cash − price×shares` totals $90,229.99; `0.05×shares×p×(1−p)` predicts $90,230.19, a $0.205 aggregate rounding difference. Taker residual median $0.3078; minimum $0.00004, maximum $128.89. Maker residuals sum $2.2075, all material residuals arising in three first-second boundary-role exceptions. For SELL the sign is reversed (`price×shares − cash`). SELL fees are not added to the BUY-fee tables; cash receipts already contain their effect. Source: `analysis_summary.json:fees,fee_formula`, `quality_summary.json:fee_exceptions`.

**Verified per-game/kind cash accounting:** “Gross cash proxy” adds observed BUY fee residuals back to cash net, then divides by BUY cash cost. This differs from API trade PnL (principal-volume denominator and marks). The first table includes opening/terminal effects; the second is restricted to inventory-complete redeemed markets, whose zero terminal economic value makes the cash edge better identified, but selection toward winning-residual redemptions is material. Sources: `cash_pnl_all.csv`, `cash_pnl_redeemed_complete.csv`. Rebates have no condition/game key and cannot be independently attributed.

| game | kind | markets | buy_cost | cash_net | gross cash proxy c/$ | BUY fee c/$ | cash net c/$ | taker cost % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CS2 | map | 1013 | 4,063,432.79 | 42,001.61 | 2.01 | 0.98 | 1.03 | 51.40 |
| CS2 | series | 538 | 2,935,885.99 | 35,950.49 | 2.21 | 0.98 | 1.22 | 51.81 |
| LoL | map | 329 | 1,011,717.17 | 12,805.56 | 1.90 | 0.64 | 1.27 | 36.94 |
| LoL | series | 171 | 405,241.81 | 7,624.83 | 2.58 | 0.70 | 1.88 | 38.24 |
| Valorant | map | 43 | 893,680.99 | 15,664.75 | 2.75 | 0.99 | 1.75 | 47.83 |
| Valorant | series | 22 | 425,251.53 | 6,150.62 | 2.26 | 0.81 | 1.45 | 43.09 |

| game | kind | markets | buy_cost | cash_net | gross cash proxy c/$ | BUY fee c/$ | cash net c/$ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CS2 | map | 563 | 1,593,788.50 | 36,018.27 | 3.26 | 1.00 | 2.26 |
| CS2 | series | 311 | 1,254,155.76 | 27,551.16 | 3.23 | 1.04 | 2.20 |
| LoL | map | 176 | 419,465.56 | 6,469.47 | 2.23 | 0.69 | 1.54 |
| LoL | series | 94 | 215,885.15 | 5,208.20 | 3.13 | 0.72 | 2.41 |
| Valorant | map | 34 | 686,703.74 | 15,207.63 | 3.24 | 1.02 | 2.21 |
| Valorant | series | 17 | 310,806.51 | 7,098.66 | 3.02 | 0.73 | 2.28 |

**Verified redemption-complete subset:** 1,195 condition ledgers (1,194 with in-window BUYs; one zero-value old redemption), $4.481M BUY cost, $97,553 cash profit (2.177 cents/$). This is a subset result, not the whole-wallet expected edge.

**Verified rebate payments:** 30 maker payouts total $14,906.37; median amount $270.95 and interval 86,402 seconds. 31 taker payouts total $37,031.52; median amount $585.92 and interval 86,399.5 seconds, including an extra payout six seconds after another. Largest taker payout is $7,500. Observed maker rebates / maker BUY cost = 0.299%; observed taker rebates / reconstructed taker BUY fees = 41.04%; combined rebates / total BUY cash cost = **0.5335 cents/$**. These ratios relate payouts to same-window activity, not exact earned fee rates; payout lag/bonus and share of a pool matter. Source: `analysis_summary.json:rebates`, `final_stats.json:rebate_rate`.

**Verified discrepancy:** Both PnL cumulative rebate fields last change at **2026-09-12 00:00 UTC**; they remain frozen through October 7 while activity continues daily payments. Their latest-window sum grows only $13,032.25; activity payouts exceed it by $38,905.64. **Likely:** cumulative rebate ingestion/reporting is stale, or definitions differ. It cannot be reconciled as ordinary timing alone without investigating the API. Do not add activity rebates on top of API economic PnL without removing its included rebate income.

**Approximate paid-income scenario:** Replacing API rebate growth with the observed activity payouts gives $124,497.17 position-PnL growth + $51,937.89 payments = **$176,435.07**, before opening/time/earnings attribution issues. On the activity principal-volume denominator that is about **1.83 cents/$**. Across game/kind tables a proportional-to-cost rebate allocation would mechanically add 0.5335 cents/$ to each cash-net rate; that allocation is **speculative**, so no fabricated “observed rebate by game” column is presented.

### 9. ISO weeks since July and capacity

**Verified:** Below, cumulative API fields are differenced between each week's last available point and the previous week's last point. Requested hourly timestamps sit on a daily source; W41 is partial. Activity-based taker share and market counts are available only from September 7, so earlier cells are explicitly unknown. Weeks W27–W29 (June 29–July 19) have zero recorded principal volume in the supplied cumulative series; the new strategy starts W30 (July 20–26). Source: `analyze.py:119`, `pnl_iso_week.csv`; role/count columns `activity_weekly.csv`.

| iso_week | volume_usdc | realized_pnl | gross c/$ | fee c/$ | realized c/$ | markets | taker cost % (activity) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-W30 | 149,301.84 | 2,672.57 | 2.89 | 1.10 | 1.79 | — | — |
| 2026-W31 | 1,627,902.01 | 34,908.66 | 3.41 | 1.27 | 2.14 | — | — |
| 2026-W32 | 1,033,315.36 | 19,165.76 | 3.22 | 1.36 | 1.85 | — | — |
| 2026-W33 | 3,139,679.65 | 81,066.79 | 3.84 | 1.26 | 2.58 | — | — |
| 2026-W34 | 4,354,240.69 | 54,784.68 | 2.62 | 1.37 | 1.26 | — | — |
| 2026-W35 | 3,828,344.36 | 75,917.00 | 3.34 | 1.31 | 1.98 | — | — |
| 2026-W36 | 3,346,487.78 | 91,985.63 | 4.07 | 1.36 | 2.75 | — | — |
| 2026-W37 | 1,641,294.60 | 22,717.27 | 2.37 | 0.98 | 1.38 | 493.00 | 52.23 |
| 2026-W38 | 775,074.76 | 12,843.58 | 2.36 | 0.84 | 1.66 | 296.00 | 44.11 |
| 2026-W39 | 1,603,698.35 | 19,142.62 | 2.28 | 1.03 | 1.19 | 721.00 | 54.62 |
| 2026-W40 | 3,355,586.58 | 53,252.66 | 2.49 | 0.91 | 1.59 | 482.00 | 46.42 |
| 2026-W41 | 2,790,820.38 | 27,753.52 | 1.92 | 0.92 | 0.99 | 123.00 | 48.21 |

**Verified temporal pattern:** W31–W36 volume-weighted gross edge is 3.389 cents/$; W37–W40 is 2.404 cents/$. Across completed W31–W40, weekly volume has correlation **+0.313** with gross edge and **+0.187** with realized edge, not a monotone negative relationship. W34 has $4.354M volume and 2.62-cent gross edge, W36 $3.346M and 4.07 cents, W40 $3.356M and 2.49 cents. W41's partial $2.791M / 1.92-cent gross edge is not a completed-week comparison. **Likely:** edge regime changed around September; **not verified:** falling edge is caused by reaching capacity. Game/tournament mix, fee schedule, adverse selection, market resolution timing and competition are confounded. Sources: `supplement_summary.json:weekly_capacity`, weekly table above.

For earlier July/August weeks the supplied PnL series supports volume/PnL, but does not contain role attribution or distinct condition IDs. Recovering those would require complete older activity. It was not fabricated from trade counts.

### Policy rules supported by the execution record

1. **Verified executed behavior / likely design:** Buy both outcomes repeatedly rather than routinely selling. 2,066 of 2,110 supplied summary buy markets buy both outcomes; only 22 SELL records versus 367,893 BUYs. A two-sided activity policy is identified; exact resting quotes are not. Source: `data/poly_target/summary.json:both_sides_markets,sides` and ledger totals.
2. **Likely:** Schedule global merge sweeps on a minute clock, batching multiple eligible markets and sweeping nearly the full common quantity. Evidence: 77.4% minute-second 5–10, median five conditions, max twelve, 89.8% ≥98% sweep. **Speculative implementation parameters:** scan every 60 seconds, merge floor 1 share, batch limit twelve; do not assert these are his source-code values.
3. **Verified / likely:** Recycle capital during active trading. 83.0% of MERGE dollars precede the last BUY, and approximately 94.9% of observed pair returns use merging. Larger series can recycle dozens of times; do not wait for map resolution as the default capital-release mechanism.
4. **Likely:** Inventory skew favors replenishing the minority side at large imbalances; taker trades participate heavily in repair. Evidence: 58.1% taker dollars buy minority, 2,500–5,000-share reducing/adding ratios of 1.86× CS2 and 2.13× Valorant. **Unverified:** hard cap, quote offsets, inventory risk-aversion coefficient.
5. **Verified executed boundary / likely eligibility rule:** Do not BUY above 0.95 in this observed policy. Cheap-token BUYs below 0.02 are allowed. This is asymmetric in BUY price, while economically giving both sides of the sibling market through complements.
6. **Likely:** Use discrete, game/liquidity-scaled size tiers; LoL maker median 10 shares, CS2 20, Valorant 76.6. Execution grouping scarcely changes median size, so full order clips remain unknown.
7. **Verified mixed taker behavior:** Do not model taker orders as pure flattening. About 41.9% of taker dollars are neutral/adding, and ≥2-cent price jumps precede 35.0% of all taker dollars. Exact predictive signal and taker trigger are unverified.
8. **Verified risk lesson:** Evaluate completed-market cash PnL and residual inventory, not just lifetime average pair price. 116 redeemed pair-cost>1 markets still win cash; 23 pair-cost<1 redeemed markets lose. Inventory repair after a price run can realize a loss even when the favored side ultimately wins.
9. **Verified forecasting lesson:** Use recent measured fee and gross-edge regimes. Latest API window: gross 2.23 cents/$, fees 0.94 cents/$, realized 1.30 cents/$, with observed paid rebates about 0.53 cents/activity BUY dollar. Neither 4-cent gross edge nor frozen API rebate totals should be assumed for a Dota deployment.

### What this would mean for Dota

**Given run evidence, not independently measured here:** Dota taker notional is approximately $12–30k per map (`briefs/sol-policy.md:17`), with typical best-level depth $37–44 and ~$375–392 within three cents (`00-context.md:28`). These are flow/depth constraints, not per-bot revenue.

**Verified observed turnover requirement:** Latest completed activity W40 buys $3.391M across 482 distinct markets, averaging **$7,034 per market/week**; the weekly cross-game average hides enormous variation. All-window median market cost is $844 CS2 map, $1,621 LoL map and $16,580 Valorant map. Sources: `activity_weekly.csv`, `market_size_capacity.csv`. W40 per-game/kind means are below (`final_stats.py:6`, `activity_weekly_game_kind.csv`).

| game | kind | cost | markets | cost_per_market |
| --- | --- | --- | --- | --- |
| CS2 | map | 1,340,631.53 | 201 | 6,669.81 |
| CS2 | series | 737,871.34 | 105 | 7,027.35 |
| LoL | map | 232,058.00 | 80 | 2,900.72 |
| LoL | series | 121,119.55 | 60 | 2,018.66 |
| Valorant | map | 653,437.59 | 24 | 27,226.57 |
| Valorant | series | 305,570.64 | 12 | 25,464.22 |

**Verified arithmetic / speculative flow share:** W40 LoL maps require $2,901 turnover per participating map, around 9.7%–24.2% of Dota's stated $12–30k taker notional. CS2 map mean $6,670 requires 22.2%–55.6%; the overall $7,034 mean requires 23.4%–58.6%; Valorant map mean $27,227 requires 90.8%–226.9%. These comparisons are total BUY turnover to total market taker flow. Maker BUYs compete for other takers' flow, while his own taker BUYs consume opposing maker liquidity; one cannot interpret the ratio as exact maker capture share. W40 CS2+LoL+Valorant market counts are summed across disjoint conditions, not event matches.

**Verified arithmetic:** Matching $3M/week at the W40 LoL-map mean requires roughly **1,034 traded maps/week**; at $7,034 overall mean roughly **427 markets/week**. Number of available Dota maps, per-market overlap, fees and fill quality determine feasibility. The G2 $204k series example is 6.8–17.0 times Dota's stated whole-map taker flow and cannot be a default Dota sizing target. Median LoL market size is a more credible first experiment than high-volume CS2/Valorant extremes, though Dota equivalence is unproven.

**Likely experiment design:** Start with passive BUY quotes on both outcomes, a recent-signal inventory skew, independent minority-side repair decisions, and an explicit asynchronous merge/cash ledger. Separate no-taker, inventory-repair-only, and repair-plus-jump taker modes; use the exact fee residual formula and test delayed/failed merge recovery. Use a 0.95 BUY eligibility ceiling as an observed benchmark rather than ignoring extreme-price risk. Price bands, merge cadence, inventory limits and lot sizes must be tuned to Dota liquidity, not copied as established facts.

**Speculative conservative starting scope:** A LoL-like 10–20-share maker clip is about $5–10 near mid-price, below Dota's stated $37–44 best-level depth. Benchmark minute scans, complete-pair sweeps, and bounded cash reserve with a simulated merge lag; vary eligibility rather than claiming the full target timer is known. For capital budgeting, include per-side resting-order collateral and plausible jump losses on top of peak concurrent holdings. The activity's modest mean active capital is not permission to run the strategy underfunded.

### Cross-check against sol-lol-micro

**Verified shared-result check at report writing:** `reports/sol-lol-micro.md` is WIP. It independently reports 131,530 LoL TRADE rows, 1,066 MERGEs, 270 REDEEMs, median merge size 515.978 and median sweep fraction 100%, agreeing with this ledger. Its LoL fee total $9,289.011 includes the lone SELL; this report's BUY-only LoL residual is $9,286.513, a $2.498 difference accounted for by that SELL. It reports median quantity-weighted pair age 422.9 seconds; that is distinct from this report's 65-second median LoL first-amount-crossing lag. Its stated inability to infer order size or a hard cap matches the boundary here. The independent book/markout results are still pending; no claim that inventory repair or price-chasing is profitable has been made from these activity categories.

## Open questions / what I could not verify

- Exact merge eligibility, Safe/gas batch limit, and why some small common pairs wait many minutes. The minute clock is strong evidence; “always merge every minute” is contradicted.
- Opening holdings/cost/value, full terminal position pagination, unseen transfers, and the September 22 quantity discrepancy. Eleven markets are flagged rather than silently repaired.
- True settled timestamps/winners for every market and a fully dust-free holdings valuation through time. Active-span and one-hour-tail capital grids are explicit approximations.
- Unfilled orders, cancellation/insert timing, posted quote widths, model/game-state inputs, submitted size tiers and hard inventory limits. No price-history API request was needed because the report makes no independent-midpoint claim.
- Causal profitability of minority-side takers or own-price-jump takers, and independent PnL by execution price bucket. Execution prices and aggregate allocation cannot identify these.
- Missing cumulative rebate updates after September 12: authoritative reason, earnings period of payouts, and bonus/pool allocation. Per-game rebate attribution is unavailable because activity rebates lack condition IDs.
- Distinct market counts and maker/taker shares before September 7, and whether edge changes reflect capacity, game mix, or a strategy/fee regime change. Older cumulative trade counts cannot substitute for activity IDs.
- Dota map supply, attainable flow capture and live order reserve requirements. The Dota flow inputs are supplied context, not remeasured by this agent.

## Files

All paths below are under `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/`. No input or sibling-code file was edited. Existing files were only read; outputs created by this agent were revised during the run.

Reproduce, sequentially, with these commands from the run directory:

```sh
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/analyze.py > work/sol-policy/run.log
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/supplement.py > work/sol-policy/supplement.log
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/quality.py > work/sol-policy/quality.log
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/final_stats.py > work/sol-policy/final_stats.log
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/write_report.py
/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python work/sol-policy/verify.py > work/sol-policy/verify.log
```

- `analyze.py`: core chronological reconstruction, merge features, exposure-conditioned inventory fill rates, taker categories, fee residuals, cash/basis ledger, ISO-week deltas and example selection. Outputs: `market_timeline.parquet` (~57 MB), `market_metrics.parquet`, `merge_metrics.parquet`, `state_exposure.parquet`, `analysis_summary.json`.
- `supplement.py`: inventory completeness, merge clock/batching, quantity/price extremes, pair-cost versus cash loss, cumulative rebate freeze, capacity correlations and worked phase/cash timelines. Outputs: `supplement_summary.json`, `worked_examples.json`, `capital_1min_valid.parquet`.
- `quality.py`: strictly-earlier-second taker sensitivity, corrected price-decile accounting and sizing boundaries, active-span/one-hour capital proxies, pre-redemption pairs and maker fee exceptions. Outputs: `quality_summary.json`, `price_bands_exact.csv`, `price_bands_closed_exact.csv`, `capital_1min_scoped.parquet`.
- `final_stats.py`: paired-return proportions, weekly game/kind capacity, market cost/exposure scales, exact brief capital definition, reference-example merge summaries, nominal price-decile sizing. Outputs: `final_stats.json`, `market_size_capacity.csv`, `activity_weekly_game_kind.csv`, `size_price_exact.csv`; adds requested signed cashflow column to `capital_1min_scoped.parquet`.
- Key CSVs: `merge_by_game_kind.csv`, `merge_transactions.csv`, `inventory_fill_rates.csv`, `taker_classes.csv`, `taker_by_direction.csv`, `size_game_role.csv`, `size_game_kind.csv`, `size_time.csv`, `terminal_price_pair.csv`, `pair_loss_comparison.csv`, `cash_pnl_all.csv`, `cash_pnl_redeemed_complete.csv`, `positions_status.csv`, `activity_weekly.csv`, `pnl_iso_week.csv`, `capital_scoped_weekly.csv`, `examples.csv`.
- Ten worked examples: each `<slug>_timeline.csv` and `<slug>_phases.csv`; `worked_examples.json` includes all MERGE/REDEEM events plus aggregate BUY phases. Two additional merge-policy comparison ledgers: `cs2-g2-prv-2026-10-06_timeline.csv`, `cs2-g2-prv-2026-10-06_merges.csv`, `lol-png1-red-2026-09-20-game2_timeline.csv`, `lol-png1-red-2026-09-20-game2_merges.csv`.
- Raw inputs: `data/poly_target/activity_compact.json`, `markets_summary.json`, `summary.json`, `user-pnl.json`, `positions.json`, `stats-now.json`. Existing role tags are used; `taker30.json` underlies those tags per the supplied context and the independent micro-agent validation.
- Companion evidence read: `reports/sol-lol-micro.md` (WIP at cross-check). No other agent's report was edited.
- `verify.py`: accounting and file checks; `verification.json` confirms 375,900 non-rebate ledger rows, 367,893 BUYs, 6,784 merges, monotonic market timelines, exact net/pair identities, agreement with supplied market costs/returns within $0.00001, 60-second grid spacing, and no scratch file above 200 MB. No product tests or backtests were run.
- `write_report.py`: renders these verified outputs and explicit inference boundaries into this report; leaves status WIP until the final verification step.
