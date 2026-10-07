# LoL fill-level reconstruction of 0x6e2c: two-sided executions, directional takers and batched merges
Status: FINAL

## Summary

- **Verified:** Reconstructed all 131,530 LoL activity fills across 500 markets into `fills.parquet`; 128,038 (97.35%) match the supplied on-chain records. 17,271/17,271 taker classifications match `taker30.json`. See §§0, 8.
- **Verified:** Both-token midpoints exist at 129,537 BUY fills. Metadata ticks support the placement histogram for 71,405 fresh, chain-matched maker fills across 272 markets. The modal placement is at the bid; a uniform touch-minus-one-tick rule is unsupported. See §1.
- **Likely:** Maker fills occur on both outcomes in 39.0% / 53.5% / 79.3% of active 30s / 60s / 300s windows. Median paired **median-price** spread is about 2c in a minute; the minimum-price estimator reports 5c and overstates a stationary quote spread. See §2.
- **Likely:** Takers are a mixture of hedging and alpha: 45.8% reduce absolute share skew; 52.8% target the underweight token. Positive-60s-markout skew-adders are 31.6% of observable taker records, while nonpositive-markout skew-reducers are 23.4%. See §3.
- **Verified book-relative statistic:** Maker / taker +60s gross markouts are 0.345c / 1.245c per share; taker net after entry fee is 0.295c. Maker early adverse selection is visible at 10–30s. See §3.
- **Likely:** Excess-token inventory value is usually modest: fill-weighted p50 / p95 / p99 = $41 / $266 / $479. Observed skew exceeds $1,000, so $500 is not a hard cap. Median peak unreturned capital is $1,124. See §4.
- **Likely:** Merges sweep essentially all available pairs, rather than merge immediately on every opposing fill: median size 516 pairs; median quantity-weighted pair age 7.0 min. 1,066 condition-level merges share just 348 transactions, and 75.3% occur at seconds 6–9 of a minute. See §5.
- **Likely:** The trader remains active late: 3,212 maker fills at prices ≤0.10 or ≥0.90 occur in the final five game minutes; last fill precedes the finished-clock anchor by a median 41.1s. See §6.
- **Likely, activity arithmetic verified:** LoL BUY cash cost $1,416,958.98; MERGE cash $1,181,717.95; REDEEM cash $255,574.40; SELL cash $97.03; cash net **+$20,430.39**. All 500 market ledgers match `markets_summary.json` within $9.1e-12. See §7.
- **Likely:** For the 478 verified-winner markets, exact economic PnL after fees is **+$19,673.29**: maker +$8,884.19, taker +$10,789.10. Missing winners prevent a complete settlement attribution for the remaining 22 markets. See §7.
- **Verified:** LoL taker fees are $9,289.01; every activity row agrees with the stated fee formula within $0.0001. Rebates are not allocated by market in this report. See §7.

## Findings

### 0. Scope, join and confidence

Read-only analysis of the supplied 2026-10-07 snapshot. The population is the 30-day LoL activity subset, including map and series contracts, and the LoL local full-depth archive. No trading, production access, backtests or code-repository changes were performed.

**Verified methods:** `work/sol-lol-micro/reconstruct.py:23` reads the latest snapshot at or before each query, including a predecessor carried across archive-day boundaries. It records each token's best bid, ask, midpoint, best bid depth, displayed bid depth at the activity fill price, and snapshot age. An empty bid or ask yields an unavailable midpoint. There is no interpolation of prices. Queries cover t, t−1/2/10/30/60s and t+10/30/60/300s. Depth on the sibling token is evaluated at the same numeric fill price; use the `own_*` projection for placement.

**Verified methods:** `work/sol-lol-micro/reconstruct.py:67` interpolates monotonic live clock anchors where possible; extrapolation is capped at 120s. `clock_age` and `clock_status` are retained. Time-profile rows use live anchors with age ≤120s and a known finished-clock anchor. Inventory starts at zero at the activity-window boundary, then applies BUY, SELL, MERGE and winning-token REDEEM. No reconstructed market has a materially negative token balance. This checks arithmetic consistency, not absence of an unobserved opening position.

Confidence labels distinguish measured data from inferred policy. **Verified** book-relative statistics below use chain-matched fills. Any full-activity result using unmatched fills—inventory, window pairing, time profile or PnL—is labeled **likely** as a reconstruction even when the arithmetic itself is verified. Submission times, cancelled orders, original order quantities and trader fair values are unobserved. A chain block timestamp is not a pre-execution book timestamp.

| Coverage | Count |
| --- | --- |
| Activity markets / fills | 500 / 131,530 |
| Markets with files for both tokens (any archive day) | 494 |
| Markets with both midpoints at at least one BUY fill | 493 |
| Clock-associated markets / with both-token file directories | 289 / 289 |
| Known finished-clock anchors | 285 |
| Both-midpoint BUY fills / fresh own book ≤5s | 129,537 / 125,660 |
| Activity rows sharing a timestamp | 66.56% |
| Own snapshot age p50 / p99 | 0.012s / 64979.873s |

Source: `work/sol-lol-micro/analyze.py:88`; `results.json.coverage`. File-directory availability does not imply coverage of the fill date, or a valid two-sided midpoint. The observed archive counts supersede the preliminary counts in `00-context.md`. Carrying the last book across a missing archive day creates some ages of many hours; fresh-book placement/markouts exclude these. All-midpoint inventory and raw entry-capture figures retain them and are less reliable.

### 1. Placement, depth, sizing and refresh

**Verified book-relative statistic:** The primary cents histogram uses fresh own-token books (age ≤5s), available midpoints on both tokens, and corroborated maker BUYs. The sign is `fill_price − best_bid`: positive means the execution price exceeds the recorded bid. This is frequent after the executed order has disappeared from the book.

| Execution vs recorded best bid | At t (%) | At t−1s (%) | At t−2s (%) |
| --- | --- | --- | --- |
| below >=2c | 11.51 | 9.28 | 11.55 |
| below 1-2c | 11.12 | 9.89 | 12.82 |
| below <1c | 0.30 | 0.30 | 0.44 |
| at bid | 28.19 | 30.65 | 39.65 |
| above <1c | 0.58 | 0.79 | 0.62 |
| above 1-2c | 22.17 | 25.30 | 18.21 |
| above >=2c | 26.13 | 23.77 | 16.70 |

**Verified metadata-tick subset:** 71,405 fills / 272 markets, with session tick sizes from `meta_by_condition.json` (1c and 0.1c). Tiny off-tick deviations from activity/on-chain notional rounding are kept rather than silently snapped.

| Execution vs recorded best bid | At t (%) | At t−1s (%) | At t−2s (%) |
| --- | --- | --- | --- |
| below >=2 ticks | 12.60 | 10.24 | 12.66 |
| below 1-2 ticks | 11.05 | 9.85 | 12.54 |
| below <1 tick | 0.14 | 0.14 | 0.22 |
| at bid | 27.35 | 29.88 | 38.72 |
| above <1 tick | 0.32 | 0.46 | 0.36 |
| above 1-2 ticks | 21.56 | 24.65 | 18.03 |
| above >=2 ticks | 26.97 | 24.75 | 17.45 |

Source: `work/sol-lol-micro/analyze.py:89`, `work/sol-lol-micro/analyze.py:108`; `placement.csv`, `placement_ticks.csv`. **Likely inference:** Touch placement is common, but neither the t histogram nor the two-second sensitivity identifies a universal resting distance. It would be wrong to copy a touch-minus-one-tick rule from these fills.

**Verified book-relative statistic:** On 106,024 chain-matched maker fills with both books fresh ≤5s, `1 − own fill price − opposite token best bid` is p10 -1.00c / p50 1.00c / p90 5.00c. This combines his executed own price with the market's opposite bid, not necessarily his own opposite quote. Source: `work/sol-lol-micro/analyze.py:107`.

| Depth proxy | At t | At t−1s | At t−2s |
| --- | --- | --- | --- |
| Fill price present on displayed bid ladder (%) | 47.89 | 46.53 | 60.86 |
| Median fill / displayed depth at fill price | 0.21 | 0.21 | 0.25 |
| Fill equals displayed depth, conditional on price present (%) | 12.61 | 9.78 | 13.21 |

**Verified:** The median maker fill discount to the recorded own midpoint is 0.50c; p10 / p90 are -2.50c / 3.50c. Depth is aggregated across makers. Equality with a fill does not establish sole ownership, and price absence or fill/depth >1 warns against reading a block-time snapshot as the precise pre-execution queue.

**Verified order-hash evidence:** 96,078 maker order groups have executions, of which 11,023 are seen more than once. Median executed quantity per order is 12 shares ($5.60); p90 is 42.26 shares. For repeated orders, first-to-last observed fill duration is p50 1s / p90 12s / p99 108.8s. These are executed-size and resting-lifetime lower bounds. Common executed order totals are 8, 10 and 18 shares. No cancellation/refresh interval is identified. Source: `work/sol-lol-micro/auxiliary.py:25`; `maker_orders.parquet`.

### 2. Two-sidedness and effective spread

**Likely reconstruction:** Fixed UTC-aligned windows are counted only when at least one maker BUY occurs. Both-token fill windows do not establish simultaneous quotes; absence of a fill does not establish absence of a quote. All maker activity BUYs are used here, including unmatched rows; book coverage is not required for the window counts. Price/volatility stratification requires fresh book observations, and game-phase stratification requires fresh live clock anchors.

| Window | Active windows | Both tokens | Both (%) | Median spread from minimum prices (c) | Median spread from median prices (c) | Median VWAP spread (c) |
| --- | --- | --- | --- | --- | --- | --- |
| 30s | 22,051 | 8,609 | 39.04 | 4.00 | 2.00 | 2.00 |
| 60s | 14,225 | 7,614 | 53.53 | 5.00 | 2.00 | 2.00 |
| 300s | 4,246 | 3,368 | 79.32 | 12.00 | 3.00 | 2.61 |

For one-minute windows, median `min(YES price)+min(NO price)` is **$0.950**; using the median fill price of each outcome gives **$0.980**. Picking the lowest price on both sides increasingly captures temporal volatility: the apparent minimum-price spread rises from 4c at 30s to 12c at 300s, while the median-price spread stays near 2–3c. These minima are not an attainable quote or a realized pair return for all shares. Source: `work/sol-lol-micro/analyze.py:117`; `minute_pairs.parquet`.

**Likely short-window cross-check:** Pair each maker fill with the latest preceding opposite-token maker fill, including same-second fills. These pairs are reused and not quantity matched.

| Maximum gap | Fills paired | Share of maker fills (%) | Median implied spread (c) |
| --- | --- | --- | --- |
| 1s | 10,925 | 9.56 | 4.00 |
| 5s | 28,306 | 24.77 | 4.00 |
| 10s | 44,513 | 38.96 | 3.00 |
| 30s | 74,774 | 65.44 | 3.00 |
| 60s | 91,005 | 79.65 | 4.00 |

Source: `work/sol-lol-micro/analyze.py:143`; `maker_pairs.parquet`. At a one-second gap, the median effective spread is about 4c, providing stronger evidence of short-term two-sided execution than five-minute minima.

| Game phase | Paired minutes | Median-price spread (c) | Minimum-price spread (c) |
| --- | --- | --- | --- |
| <8m | 994 | 1.50 | 4.50 |
| 8-20m | 1808 | 2.00 | 6.00 |
| >=20m | 1601 | 2.50 | 7.00 |

| YES midpoint band | Paired minutes | Median-price spread (c) | Minimum-price spread (c) |
| --- | --- | --- | --- |
| (-0.001, 0.2] | 967 | 2.50 | 5.00 |
| (0.2, 0.4] | 1558 | 2.00 | 6.00 |
| (0.4, 0.6] | 1755 | 2.00 | 6.00 |
| (0.6, 0.8] | 1728 | 2.00 | 6.00 |
| (0.8, 1.0] | 1247 | 2.00 | 4.00 |

| Median \|own midpoint change over 60s\| | Paired minutes | Median-price spread (c) | Minimum-price spread (c) |
| --- | --- | --- | --- |
| (-1.0, 0.01] | 545 | 1.50 | 2.00 |
| (0.01, 0.03] | 2055 | 2.00 | 4.00 |
| (0.03, 0.06] | 2025 | 2.00 | 6.00 |
| (0.06, 1.0] | 2630 | 2.50 | 9.00 |

**Likely inference:** Median execution spreads widen modestly with game age and recent movement, but show little broad midpoint dependence. The volatility proxy is absolute 60s change, not realized variance. Minimum-price spreads are especially sensitive to volatility. Source: `work/sol-lol-micro/analyze.py:137`; `results.json.pair_by_*`.

### 3. Taker triggers, inventory and markouts

**Verified roles:** All 17,271 taker activity rows occur in `taker30.json`; on-chain matching corroborates 17,040 of them. Maker/taker wallet addresses in `lol_chain.json` agree with the target wallet for all 111,020 maker and 35,794 taker chain rows. Activity taker rows can aggregate several counterparty executions. Source: `work/sol-lol-micro/reconstruct.py:163`, `work/sol-lol-micro/auxiliary.py:21`.

**Likely inventory reconstruction:** Net share skew is YES shares minus NO shares immediately before the fill. Dollar skew is the signed midpoint value of the excess token: positive skew × YES mid, negative skew × NO mid. It is not total capital committed or a beta/delta measure. `reduces_absolute_skew` tests the full after-fill absolute net, including overshoots; `targets_underweight` only tests which token was bought.

| Taker sample | Rows | Median \|net\| shares | Median \|net\| dollars | Targets underweight (%) | Reduces \|net\| (%) |
| --- | --- | --- | --- | --- | --- |
| All usable-book activity | 17087 | 72.54 | 29.58 | 52.74 | 45.67 |
| Fresh own book + chain match | 16436 | 72.56 | 29.74 | 52.83 | 45.84 |
| Same, unique timestamp only | 8383 | 65.04 | 27.09 | 52.25 | 45.59 |

Source: `work/sol-lol-micro/reconstruct.py:196`, `work/sol-lol-micro/analyze.py:168`. Same-second source ordering is not transaction log order; the unique-timestamp result remains similar. Inventory histories still include unmatched earlier fills.

| Prior horizon | Median own-mid move (c) | Quantity-weighted move (c) | Move positive (%) |
| --- | --- | --- | --- |
| 10s | 1.50 | 3.35 | 62.86 |
| 30s | 1.65 | 2.50 | 60.00 |
| 60s | 1.50 | 3.00 | 58.08 |

**Likely inference:** Takers more often buy the token whose recorded midpoint has recently risen. That is consistent with momentum/fast-information execution, though there is no feed or trader-fair-value join to prove a trigger. Source: `work/sol-lol-micro/analyze.py:183`.

**Verified book-relative markouts:** Gross markout is `(future midpoint − entry price) × shares`; net subtracts the entry cash fee. Entry own-book age ≤5s, both midpoints available, chain match; target snapshot age ≤30s. These are midpoint marks, not executable liquidation proceeds. Rows and quantity vary by horizon, so dollar totals must not be compared as the same cohort.

| Role | Horizon | Rows | Shares | Gross c/share | Net c/share | Gross dollars | Post-fill midpoint drift dollars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MAKER | 10s | 105,073 | 1,705,117 | 0.081 | 0.081 | 1,385.91 | -2,992.60 |
| MAKER | 30s | 103,699 | 1,680,418 | 0.169 | 0.169 | 2,833.18 | -1,734.13 |
| MAKER | 60s | 102,243 | 1,655,545 | 0.345 | 0.345 | 5,711.20 | 1,036.25 |
| MAKER | 300s | 90,819 | 1,456,339 | 0.463 | 0.463 | 6,738.45 | 2,898.42 |
| TAKER | 10s | 16,265 | 919,062 | 0.868 | -0.073 | 7,980.59 | 4,513.34 |
| TAKER | 30s | 16,002 | 891,206 | 1.003 | 0.055 | 8,938.69 | 5,701.36 |
| TAKER | 60s | 15,707 | 870,913 | 1.245 | 0.295 | 10,847.16 | 8,121.77 |
| TAKER | 300s | 13,886 | 720,561 | 1.155 | 0.171 | 8,322.56 | 5,249.65 |

Source: `work/sol-lol-micro/analyze.py:160`; `results.json.markouts`. Negative post-fill drift is adverse selection; positive drift is favorable. The unique-timestamp sensitivity gives maker / taker +60s gross markouts 0.323c / 2.274c per share.

**Likely behavior classification:** Observable 60s taker sample = 15,707 rows and 870,913 shares. Positive gross markout is an ex-post diagnostic, not evidence that the trader knew the future.

| Reduces \|net\|? | 60s gross markout | Rows | Records (%) | Shares (%) | Gross c/share | Net c/share |
| --- | --- | --- | --- | --- | --- | --- |
| No | Zero/negative | 3,573 | 22.75 | 23.81 | -8.32 | -9.31 |
| No | Positive | 4,957 | 31.56 | 36.19 | 9.99 | 9.03 |
| Yes | Zero/negative | 3,668 | 23.35 | 20.73 | -9.45 | -10.35 |
| Yes | Positive | 3,509 | 22.34 | 19.27 | 8.15 | 7.21 |

Source: `work/sol-lol-micro/analyze.py:172`. The most hedge-like cell is skew reduction with nonpositive markout; the most snipe-like cell is skew addition with positive markout. The other two cells show that profitable hedging and unsuccessful directional trades coexist. A sole flattening threshold cannot explain the taker leg.

### 4. Inventory policy and size response

**Likely reconstruction:** Fill-weighted distributions are not time-weighted distributions. Per-market peaks include before- and after-fill share inventory and available midpoint values. Price moves between fills can create larger dollar peaks; no claim of a continuous dollar maximum is made.

| Metric | p50 | p90 | p99 | Observed max |
| --- | --- | --- | --- | --- |
| \|net\| shares at fills | 83.54 | 332.54 | 853.14 | 13,283.99 |
| Excess-token value at fills ($) | 41.10 | 181.00 | 478.73 | 1,773.18 |
| Peak \|net\| shares per market | 213.95 | 785.42 | 1,527.42 | 13,283.99 |
| After-fill peak excess value per market ($) | 129.07 | 431.37 | 949.59 | 1,766.90 |
| Peak unreturned capital per market ($) | 1,123.67 | 4,685.51 | 9,444.10 | 23,253.41 |
| Maker execution size (shares) | 10.00 | 40.00 | 80.00 | 100.00 |
| Taker execution size (shares) | 28.30 | 130.00 | 452.08 | 12,792.00 |

Source: `work/sol-lol-micro/analyze.py:187`; `markets.parquet`. Excess dollar-value peaks are available for 494 markets; share peaks and cash capital are available for all 500. The observed share outlier can carry low dollar value when it is a cheap losing token.

| \|excess value\| band | Maker adding fills | Maker reducing-side fills | Reducing-side fraction (%) | Median adding qty | Median reducing-side qty |
| --- | --- | --- | --- | --- | --- |
| (-0.001, 50.0] | 29764 | 31594 | 51.49 | 9.89 | 8.08 |
| (50.0, 100.0] | 9564 | 15071 | 61.18 | 12.00 | 10.00 |
| (100.0, 250.0] | 7181 | 12517 | 63.54 | 20.00 | 18.00 |
| (250.0, 500.0] | 1922 | 3870 | 66.82 | 40.00 | 40.00 |
| (500.0, 1000.0] | 155 | 713 | 82.14 | 50.00 | 45.00 |
| (1000.0, 2500.0] | 0 | 99 | 100.00 | — | 45.00 |

Here “reducing side” means the underweight token; a large fill can overshoot. Source: `work/sol-lol-micro/analyze.py:188`. **Likely inference:** The observed maker flow becomes increasingly biased toward the underweight side as dollar skew rises, consistent with inventory skew. Filled quantities do not shrink uniformly on the adding side, and high-volume markets also have larger inventory and child sizes. These cross-market aggregates cannot identify quote-size control or prove a cancellation gate. An operational skew scale of a few hundred dollars is supported; a particular max-skew parameter is **unverified**. The $1,000–$2,500 band contains few observations and no adding-side maker fills, which is suggestive rather than proof of a $1,000 gate.

### 5. Merge policy and settlement carry

**Likely reconstruction, verified arithmetic:** A FIFO ledger timestamps increments in matched pairs and consumes them at each MERGE. All 1,066 merges are fully accounted for; maximum unaccounted pairs is 4.1e-12. Oldest pair age is a lower bound on how long some collateral waited; weighted age represents the merged quantity, and newest age is the delay after the last consumed pair became available. This is more informative than timing from the first opposing BUY alone.

| Merge metric | p50 | p90 | p99 | Max |
| --- | --- | --- | --- | --- |
| Pairs merged | 515.98 | 2,934.78 | 7,044.55 | 23,182.77 |
| Pairs available just before | 528.16 | 3,053.04 | 7,219.11 | 23,182.77 |
| Oldest consumed pair age (s) | 816.50 | 3,099.50 | 12,398.35 | 37,680.00 |
| Quantity-weighted pair age (s) | 422.93 | 2,626.36 | 9,103.17 | 37,155.80 |
| Newest consumed pair age (s) | 65.00 | 2,136.50 | 8,500.10 | 36,540.00 |
| Lag from most recent trade (s) | 39.50 | 2,033.00 | 8,485.15 | 33,319.00 |

Median sweep fraction = 100.00%; p10 = 98.89%. 68.86% of merges precede that market's final fill. Of 651 merges with a known map-end anchor, 467 (71.7%) occur before map end. Source: `work/sol-lol-micro/analyze.py:19`, `work/sol-lol-micro/auxiliary.py:39`; `merge_fifo.parquet`, `merges.parquet`.

**Likely scheduled batching:** 348 distinct transactions cover 1066 market-level operations; median 2 markets per transaction and p90 6. 75.29% of distinct transactions land at seconds 6–9 of a minute. The median same-market merge gap is 927s, while the p10 is 182.9s. This supports a minute-aligned sweep/check process with variable execution frequency. A universal minimum threshold is unsupported: minimum merge is 2.5 pairs; 95 operations merge fewer than 50 pairs. Source: `work/sol-lol-micro/auxiliary.py:16`; `merge_daily.csv`.

| LoL contract | Markets | Total BUY cost | Median market cost | Median merges/market | Median peak capital | Cash net |
| --- | --- | --- | --- | --- | --- | --- |
| Series | 171 | $405,241.81 | $1,415.12 | 2 | $851.19 | $7,624.83 |
| Map | 329 | $1,011,717.17 | $1,621.31 | 1 | $1,318.82 | $12,805.56 |

**Likely explanation for fewer LoL merges:** The all-LoL median is 2, but the map-only median is 1; many maps are small and short, and merges are transaction-batched. Comparing these with the supplied $204k CS2 G2 series example confounds volume, duration and contract type. A distinct LoL merging algorithm is not established. Source: `work/sol-lol-micro/analyze.py:241`; comparison example: `00-context.md`, “Example: CS2 G2 vs PARIVISION”.

LoL REDEEM cash is $255,574.40, or 18.04% of BUY cost in aggregate. Per-market redeem/cost p50 is 0.43%, p75 56.96% and p90 101.32%. Zero-payout losing tokens do not produce redeem cash; redemption cash is not a measurement of all terminal shares. See §7 for missing-winner carry bounds.

### 6. Game-time profile and map end

**Likely clock reconstruction:** The game-time sample contains 78,185 fills from live anchors (age ≤120s) in markets with a known finished clock. Previous `timed_trades.json` has 61,018 rows; 61,018 are joined for comparison. Median / p90 / p99 absolute game-second difference is 0.18s / 2.23s / 29.42s; 2581 joined rows differ by >10s. In that tail the median signed difference is -19.67s: interpolation gives less game time than the previous timestamps during slower anchor progression. This is a method-sensitivity check, not verification against ground truth. The interpolation, sample extension and freshness rule mean this is not the same population as the previous timing summary. Source: `work/sol-lol-micro/auxiliary.py:42`.

| Phase | Maker fills | Taker fills | Combined share (%) | Maker cost | Taker cost |
| --- | --- | --- | --- | --- | --- |
| 0-8m | 13512 | 1295 | 18.94 | $107,224.03 | $43,538.44 |
| 8-20m | 26831 | 3937 | 39.35 | $216,257.01 | $101,353.57 |
| >=20m | 28126 | 4484 | 41.71 | $226,803.37 | $183,892.07 |

| Game minute | Maps still playing | Maker fills/map/min | Taker fills/map/min | Paired median spread (c) | Paired windows |
| --- | --- | --- | --- | --- | --- |
| 1 | 285 | 3.96 | 0.10 | 1.00 | 90 |
| 5 | 285 | 7.69 | 0.81 | 1.50 | 151 |
| 10 | 285 | 7.04 | 0.96 | 1.50 | 139 |
| 15 | 285 | 7.87 | 1.28 | 2.00 | 157 |
| 20 | 283 | 7.46 | 1.19 | 2.00 | 137 |
| 25 | 234 | 8.21 | 1.65 | 2.50 | 119 |
| 30 | 144 | 9.44 | 1.10 | 2.75 | 66 |
| 35 | 61 | 11.48 | 1.72 | 4.25 | 36 |
| 40 | 16 | 23.69 | 2.88 | 3.00 | 12 |

Rates are counts per finished-clock map still playing at the minute's start, including maps in which the trader has no fill then. Late-minute denominators shrink and inflate sampling noise. Paired spread rows use clock-associated maker windows and include full-activity unmatched fills; the detailed files retain sample counts. Source: `work/sol-lol-micro/analyze.py:203`; `time_profile.csv`, `paired_spread_by_game_minute.csv`.

**Likely:** The last fill is a median 41.1s before the first finished-clock anchor (p90 514.0s). The median map end game time is 30.13min and median last fill game time is 28.67min; these separate medians should not be subtracted to infer a median lag. Of 12,557 fills during the final five game minutes, 3,613 are at price ≤0.10 or ≥0.90, including 3,212 maker fills across 226 markets. Actual fills therefore continue near the extremes late in maps, though fills do not prove continuous quoting. The finished-anchor timestamp is a feed observation, not a ground-truth millisecond Nexus destruction timestamp.

### 7. PnL decomposition, fees and reconciliation

**Likely chain-level interpretation; verified activity ledger:** BUY cost includes taker cash fees. Merge and redeem cash are taken directly from activity. No rebate, fee refund or gas is allocated here.

| Cash item | LoL total |
| --- | --- |
| BUY cost | −$1,416,958.98 |
| SELL proceeds | +$97.03 |
| MERGE proceeds | +$1,181,717.95 |
| REDEEM proceeds | +$255,574.40 |
| Cash net | +$20,430.39 |
| Reference markets_summary cash_net | +$20,430.39 |
| Maximum per-market reconciliation error | $9.1e-12 |

Maker BUY cost is $888,300.04 (62.69%), taker BUY cost $528,658.94 (37.31%). Across all available last snapshots, raw entry capture `Σ(mid − price) × shares` is $6,423.45 for makers and $3,023.07 for takers. This includes stale carried books and is not a reliable whole-population spread estimate or realized PnL; use the fresh matched cohorts in §3 for execution quality. Source: `work/sol-lol-micro/reconstruct.py:215`, `work/sol-lol-micro/analyze.py:220`; `markets.parquet`.

**Verified fee arithmetic:** BUY fee residual is `usdc_size − price × size`; the one SELL uses `price × size − usdc_size`. Total = $9,289.011059, versus model $9,289.055484; maximum row error $0.00001000, with 100% within $0.0001. Maker residual total is $1.2e-07. The small formula difference is accumulated micro-rounding. Source: `work/sol-lol-micro/analyze.py:245`. This verifies the stated sports-fee formula in these historical rows; it is not a general statement about future fee schedules.

**Likely exact settlement attribution for verified winners:** Winner labels come from the local universe, supplemented by positive REDEEM outcome indices; disagreement is rejected by the reconstruction. For 478 markets, assign each trade its terminal token payout (1 for winner, 0 for loser), and subtract entry/exit fee residuals. Merged pairs are implicitly worth $1, so this equals cash plus unpaid terminal token value without double counting merges.

| Role | Resolved fills | Entry midpoint capture | Settlement drift after entry mid | Gross without a valid entry mid | Gross settlement PnL | Fees | Net settlement PnL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MAKER | 111,261 | 4,840.47 | 4,076.75 | -33.02 | 8,884.19 | 0.00 | 8,884.19 |
| TAKER | 16,958 | 3,340.51 | 15,132.58 | 1,398.77 | 19,871.87 | 9,082.77 | 10,789.10 |

All dollar amounts. The decomposition is additive: entry capture + subsequent settlement drift + unpriced-fill gross − fees. Exact resolved-market identity error is at most $1.2e-11. Gross maker / taker terminal markouts are 0.480c / 2.019c per share; taker net is 1.096c. Source: `work/sol-lol-micro/analyze.py:221`, `work/sol-lol-micro/analyze.py:231`.

The +60s maker markout and midpoint drift in §3 isolate short-horizon execution quality. They are overlapping measures: capture plus drift equals markout on that horizon's cohort. They must not be added again to terminal maker or taker PnL. Maker +60s drift is favorable in the full matched sample, while +10/+30s drift is adverse. The taker leg contributes $10,789.10 net in resolved markets, more than the maker leg, so LoL profitability is not explained by passive spread capture alone.

**Likely valuation bounds:** 22 markets lack a verified winner; 4 lack a usable remaining-inventory midpoint. Cash plus the available midpoint values is $20,924.15, **excluding the value of unmarked shares**, not a complete mark. A terminal valuation bounded by the smaller/larger remaining token balances gives whole-LoL economic PnL **[$20,430.39, $22,565.90]**, assuming zero opening inventory and the supplied complete cash events. Known-winner settlement PnL is $19,673.29. The `economic_pnl` column remains null where a midpoint valuation cannot be made; summing only its non-null rows is a partial-market total, not whole-LoL PnL. The positions snapshot has $0 LoL current value in its returned rows, but absence from that payload does not establish the unknown winners or prove zero holdings. Source: `work/sol-lol-micro/analyze.py:66`, `work/sol-lol-micro/analyze.py:236`.

The supplied `user-pnl.json` and `stats-now.json` are wallet-wide across games and dates; `user-pnl.json.data.source_fidelity` is `1d` despite requested `1h` points. They provide no LoL or per-condition attribution, so an exact equality with this LoL 30-day subset is not a valid comparison. `markets_summary.json` is the available exact same-population reconciliation. Maker/taker rebates and refunds cannot be assigned to these markets from the supplied activity snapshots with confidence. Source: `work/sol-lol-micro/auxiliary.py:19`; `data/poly_target/stats-now.json:1`.

Largest markets, with activity-ledger cash PnL and observed skew:

| Market slug | Fills | BUY cost | Cash net | Fees | After-fill peak \|net\| shares | After-fill peak excess value |
| --- | --- | --- | --- | --- | --- | --- |
| lol-tl2-fly-2026-09-20-game3 | 770 | $25,897.68 | $648.76 | $107.51 | 1,883 | $1,241.13 |
| lol-c9-ly-2026-10-03-game2 | 669 | $23,318.41 | $-70.64 | $202.77 | 1,448 | $1,107.41 |
| lol-tl2-ly-2026-10-04-game3 | 737 | $21,300.62 | $1,876.34 | $74.13 | 13,284 | $1,766.90 |
| lol-fly-sr-2026-09-25-game2 | 478 | $18,955.87 | $-114.81 | $149.77 | 1,648 | $917.43 |
| lol-ly-c9-2026-09-19-game2 | 977 | $18,937.64 | $231.92 | $116.24 | 944 | $676.46 |
| lol-c9-tl2-2026-09-27-game4 | 770 | $17,259.33 | $-248.88 | $112.22 | 1,401 | $438.38 |
| lol-los-lll-2026-09-27-game4 | 674 | $16,655.01 | $-82.75 | $109.72 | 1,260 | $732.04 |
| lol-sr-sen-2026-09-18-game4 | 628 | $16,129.55 | $88.58 | $122.34 | 1,460 | $624.31 |
| lol-ap-piv-2026-09-22-game1 | 519 | $16,074.80 | $-24.15 | $166.98 | 1,284 | $980.30 |
| lol-su-vdn-2026-09-23-game1 | 545 | $15,894.99 | $-992.73 | $156.47 | 1,231 | $453.98 |
| lol-ly-c9-2026-09-19 | 631 | $14,940.80 | $412.29 | $111.56 | 922 | $529.70 |
| lol-ap-piv-2026-09-22 | 709 | $14,647.25 | $77.33 | $160.45 | 888 | $377.67 |

The full 500-market table, including individual cash reconciliation, terminal role attribution, carry bounds, capital, timing and match rate, is `work/sol-lol-micro/markets.parquet`. `top_markets.csv` and `losing_markets.csv` are convenience extracts; use cash or verified-winner PnL rather than an unmarked market's null economic estimate.

### 8. Our books and fills versus his executions

**Verified matching:** Match by condition, transaction hash, token and role, then either exact normalized price/quantity (rounded to five decimals) or agreement between activity and chain aggregate quantity/notional within $0.0001 / 0.0001 shares. Several activity rows can exist in one transaction; a taker activity row can represent several fills. This is an execution/group match, not a claim that activity has one row per on-chain log. Maker rows require him as maker and taker rows require him as taker in the supplied normalized `lol_chain.json`.

| Activity role | Matched | Total | Match rate |
| --- | --- | --- | --- |
| MAKER | 110,998 | 114,259 | 97.15% |
| TAKER | 17,040 | 17,271 | 98.66% |

Source: `work/sol-lol-micro/reconstruct.py:114`, `work/sol-lol-micro/reconstruct.py:170`; `chain_match_daily.csv`. The supplied chain source covers 491 markets and 146,814 normalized rows. Its wall-time extent is 2026-09-07 16:04:40+00:00 through 2026-10-06 23:59:58+00:00. Unmatched fills are retained with `chain_matched=false`; they are not silently discarded or assumed fictitious. No separate replay of every local on-chain parquet file was needed because the brief explicitly permits verification against `lol_chain.json`.

Book presence and chain matching are separate checks. A chain match verifies executed role/price/size, not that a particular book snapshot preceded execution. The recorded snapshot may already reflect the trade, and aggregate L2 cannot reveal the wallet's queue position.

## Open questions / what I could not verify

- The trader's original posted size, unfilled quotes, order submission/cancel times, refresh timer, queue position, fair-value feed and model are absent. No reliable “alone at touch”, deterministic tick placement or cancellation threshold can be recovered.
- Same-second order is unknown for 66.56% of activity rows. Unique-timestamp sensitivity reduces this problem but changes the sample.
- 22 winner labels remain unverified, concentrated in recent archive coverage. Carry bounds replace a fabricated complete settlement PnL.
- Clock-anchor latency, pauses and long anchor gaps limit exact game-second and end-event alignment. Time-profile freshness filters make its denominator differ from the old 61,018-fill study.
- A skew scale can be measured, but a hard dollar/share cap cannot. Increasing underweight-side fills may reflect quote skew, competition, price paths or fill selection; observed execution size is not posted size.
- Minute-aligned merge batching is strongly supported; the actual scheduler interval, minimum economic size, gas policy and whether batches include other games cannot be identified from LoL rows alone.
- Midpoint markouts omit executable spread, depth, future taker exit fee and market impact. Terminal role attribution is additive accounting, not causal isolation of two independent strategies.
- PnL excludes allocated rebates/refunds and gas, and assumes no unrecorded opening inventory. Full-activity conclusions including unmatched fills remain likely rather than chain-complete verification.

## Files

Scripts that produce every reported number (Python: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python`):

1. `work/sol-lol-micro/reconstruct.py` → `fills.parquet`, `markets.parquet`, `merges.parquet`; run with `python work/sol-lol-micro/reconstruct.py`.
2. `work/sol-lol-micro/analyze.py` → enriched fills/markets, `results.json`, `merge_fifo.parquet`, `minute_pairs.parquet`, `maker_pairs.parquet`, placement and time CSVs; run after reconstruction.
3. `work/sol-lol-micro/auxiliary.py` → `auxiliary.json`, `maker_orders.parquet`, merge-day and chain-day CSVs; run after analysis.
4. `work/sol-lol-micro/write_report.py` → this Markdown report from the saved statistics; line 2 is finalized only after verification.
5. `work/sol-lol-micro/verify.py` → `verification.json`, `verify.log`; checks source-match multiplicity, cash/terminal accounting, per-market markout additivity, snapshot chronology, artifact sizes, source references and Markdown table structure.

Outputs: `fills.parquet` (one row per activity fill), `markets.parquet` (500 rows, including separate matched maker/taker 60s capture, drift and markout columns), `merges.parquet`, `merge_fifo.parquet`, `maker_orders.parquet`, `maker_pairs.parquet`, `minute_pairs.parquet`, `placement.csv`, `placement_ticks.csv`, `time_profile.csv`, `paired_spread_by_game_minute.csv`, `chain_match_daily.csv`, `merge_daily.csv`, `top_markets.csv`, `losing_markets.csv`, `results.json`, `auxiliary.json`, `verification.json`. Execution logs are `reconstruct.log`, `analyze.log`, `auxiliary.log`, `verify.log`. `markets.partial.parquet` is only a progress checkpoint; use `markets.parquet` for final values.

Source data: `data/poly_target/{activity_compact.json,lol_clocks.json,lol_chain.json,taker30.json,timed_trades.json,markets_summary.json,meta_by_condition.json,positions.json,user-pnl.json,stats-now.json}`; sibling repo `data/lol/raw/telonex/polymarket/book_snapshot_full/` and `data/lol/processed/universe/markets.parquet`. All source data and repositories remain read-only.

## Reconstructed rulebook

1. **Likely: Maintain two-sided execution capacity throughout the map.** Both-token maker fills occur in 53.5% of active minutes and 79.3% of active five-minute windows (§2). This does not prove every instant has both quotes.
2. **Likely: Work near the touch using small child orders; do not assume touch−1 tick.** At t−2s, touch is the modal metadata-tick bin (38.7%). Typical maker execution is 10 shares; order-hash executed-size lower bound is 12 shares. Exact submission placement and refresh interval remain unknown (§1).
3. **Likely: Seek an effective paired median-price spread around 2c, widening modestly during later/volatile play.** Minute paired spreads are about 1.5c before minute 8 and 2.5c after minute 20; ≤1s opposite executions imply a median 4c spread. These are distinct execution estimators, not an identified quoting equation (§2).
4. **Likely: Bias maker flow toward the underweight token as skew grows, while allowing directional carry.** Fill-level |excess value| p95 is $266 and p99 $479; observed values exceed $1,000. A hard cap or shrink/pull rule is not identified (§4).
5. **Likely: Use takers for both inventory management and positive expected price movement.** Only 45.8% reduce |share skew|; median pre-taker excess value is $30, not a huge universal liquidation threshold. Taker +60s fee-net markout is 0.295c/share, and known-winner taker terminal net is 1.096c/share (§3, §7). The exact information or momentum trigger is unknown.
6. **Likely: Sweep matched inventory in scheduled cross-market batches.** Median sweep is 100% of available pairs, median quantity-weighted wait 7.0min, with 75.3% of transactions minute-aligned at seconds 6–9. Minimum observed merge is 2.5 pairs, so a universal 500-pair gate is contradicted (§5).
7. **Likely: Keep trading late and near the extremes, and redeem terminal residuals.** 3,212 extreme-price maker fills occur in the last five game minutes; median final fill is 41.1s before the observed finished anchor. Redemption cash is 18.0% of BUY cost (§6, §7).
8. **Verified accounting requirement: Price execution edge after fees and avoid double counting merge proceeds.** The historical fee is `0.05 × shares × p × (1−p)` for takers; maker fees are zero in the activity arithmetic. Merge cash is capital return, while realized return is total cash proceeds minus purchase cost plus remaining terminal value. Maker spread capture alone does not explain the LoL PnL (§7).
