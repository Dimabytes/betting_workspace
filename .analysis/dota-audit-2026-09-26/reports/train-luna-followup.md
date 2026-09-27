# Train-luna follow-up: future-book selection and F2 money side
Status: FINAL

## Scope and method

This report follows the assigned checks in `briefs/train-luna-followup.md`. I inspected cached research/validation rows and existing Dota LIVE backtest artifacts only; no data collection, model publication, or backtest execution is part of this analysis. Scratch analysis code and outputs are kept under `work/train-luna/`. The requested `data/backtests/dota_maker/LIVE` directory is a symlink to `validation_join_delta02_x015_cut480_p4_archive-s3-20260924`, the directory analyzed below.

## Findings

### 1. Future-book quality row selection

**Finding: the +300-second book-quality gate removes a measurable, future-selected subset.** The row builder first requires the current market second to be OK and then drops the row when `signal_market_p_radiant_300s` is null (`src/prepare_dataset/prepare_dataset.py:137-165`). The market builder assigns null when the +300 pair fails age, spread, or complement-pair gates (`src/market_data/build_market_data.py:113-135`; `src/shared/utils/telonex_book.py:243-279`).

I reconstructed candidate source rows from the cached market seconds and Stratz states for 2,324 pre-cutoff maps and 717 validation maps. All maps built without state-data errors. For each dropped row I reloaded only that map’s relevant raw book time window and evaluated the pair at the exact row timestamp +300 seconds; stale/missing cases got a relaxed label from the first OK second in the next 0–30 seconds. Wide-spread rows use their same-time two-sided midpoint normalized without the spread/pair gates. Scratch outputs are `work/train-luna/minute_candidate_rows.parquet`, `validation_fixed_candidate_rows.parquet`, `future_book_dropped_labels.parquet`, and `future_book_selection.json`.

| Split / row grain | Candidate rows with current quote OK | Kept +300 labels | Dropped only at future label | Drop share | Future status of dropped rows |
|---|---:|---:|---:|---:|---|
| Research train, minute rows | 13,459 | 11,070 | 2,389 | 17.75% | 1,885 wide; 470 stale; 34 missing |
| Validation, exact seconds −60..599 | 382,760 | 342,444 | 40,316 | 10.53% | 33,588 wide; 6,374 stale; 354 missing |
| Production, combined minute rows | 19,933 | 16,874 | 3,059 | 15.35% | 2,455 wide; 561 stale; 43 missing |

As a share of all current-OK rows, the wide/stale/missing failures were 14.01%/3.49%/0.25% in research train, 8.77%/1.67%/0.09% in validation, and 12.32%/2.81%/0.22% in production. Thus wide spread is the main failure status in each split; there were no inconsistent-pair failures among reconstructed drops.

The larger map-level filter is visible too: among maps with at least one current-OK candidate, 253/1,816 research-train maps, 24/706 validation maps, and 276/2,501 production-minute maps have **zero** kept labels. The stored research split has 1,563 train maps and production split has 2,225 maps, matching the maps with at least one kept row in each minute pool. Validation still holds 717 maps, but only 682 have a labeled current-OK row. Separately, current-book failures are excluded before this label-only comparison: 9,829 research-train, 83,290 validation-window, and 11,242 production-minute candidate rows.

Drop rates rose later in the scored window: research-train 15.3% at game seconds 0–119, 18.1% at 120–299, 18.6% at 300–479, and 18.6% at 480–599; validation 8.4%, 9.9%, 11.3%, and 13.8% in the same buckets. Rows with current Radiant probability ≥0.85 had higher label-drop rates than the middle market ranges: 27.7% train, 15.5% validation, and 23.2% production, versus 17–18%, 9.8–10.6%, and 14.7–15.4% in the 0.15–0.85 bands. The low-price extreme (<0.15) also had elevated rates: 22.7%, 14.3%, and 19.0%. High `|radiant_nw_adv|` rows are sparse (only 244–393 candidates at 3k–10k; no ≥10k row in the minute pools); validation’s 3k–10k drop rate was 15.9% versus 10.4% below 3k. Favorite correctness, defined by the current favorite matching the eventual map winner, had little separation: train 17.4% vs 18.5%, validation 11.1% vs 9.4%, production 15.2% vs 15.6% for correct vs wrong favorite.

Among rows with a recovered relaxed label, dropped rows have slightly larger moves in each source pool. Research train: median absolute move 7.5¢ vs 6.5¢ kept; p90 19.0¢ vs 17.5¢; `|move|≥10¢` 36.1% vs 31.2%. Validation: 7.0¢ vs 6.0¢ median; p90 17.0¢ vs 16.5¢; `|move|≥10¢` 32.1% vs 29.9%. Production: 7.0¢ vs 6.5¢ median; p90 18.5¢ vs 17.0¢; `|move|≥10¢` 34.7% vs 30.5%. The train `|move|≥10¢` gap is +4.86 percentage points (95% event-cluster CI +2.50 to +7.39); production-minute gap is +4.15 points (CI +2.09 to +6.24). Validation’s +2.18-point gap is not distinguishable from zero (CI −0.47 to +4.93). Mean absolute move is +0.73¢ higher for dropped train rows (CI +0.37 to +1.10), +0.67¢ in production (CI +0.36 to +0.98), and +0.29¢ in validation (CI −0.11 to +0.70). Relaxed labels cover 2,202/2,389 dropped train rows (92.2%), 38,951/40,316 validation rows (96.6%), and 2,849/3,059 production rows (93.1%). Only 4 production-minute current-OK rows had a +300 target after catalog game end, 3 of them dropped, so map end is not the main failure mechanism in this scoring window. Wide spread dominates the future-failure counts.

The next table gives each regime’s dropped-row count and future status counts as `dropped/current-OK (drop %); wide/stale/missing`. No ≥10k `|radiant_nw_adv|` candidate appears in these fixed minute/exact-second pools.

| Regime / bucket | Research train | Validation | Production |
|---|---|---|---|
| Game second, prehorn [−60,0) | 0/46 (0.0%); 0/0/0 | 2,881/34,737 (8.3%); 2,257/624/0 | 44/612 (7.2%); 35/9/0 |
| Game second 0–119 | 414/2,698 (15.3%); 316/93/5 | 5,914/70,686 (8.4%); 5,077/794/43 | 510/3,886 (13.1%); 397/107/6 |
| Game second 120–299 | 743/4,100 (18.1%); 593/144/6 | 10,563/106,378 (9.9%); 8,787/1,684/92 | 916/5,875 (15.6%); 743/165/8 |
| Game second 300–479 | 751/4,033 (18.6%); 606/131/14 | 12,020/106,314 (11.3%); 9,989/1,923/108 | 948/5,808 (16.3%); 773/158/17 |
| Game second 480–599 | 481/2,582 (18.6%); 370/102/9 | 8,938/64,645 (13.8%); 7,478/1,349/111 | 641/3,752 (17.1%); 507/122/12 |
| `|NW adv| <3k` | 2,340/13,215 (17.7%); 1,862/448/30 | 38,499/371,307 (10.4%); 32,419/5,773/307 | 2,992/19,540 (15.3%); 2,420/533/39 |
| `|NW adv| 3k–<10k` | 49/244 (20.1%); 23/22/4 | 1,817/11,453 (15.9%); 1,169/601/47 | 67/393 (17.0%); 35/28/4 |
| Current `market_p` <0.15 | 75/330 (22.7%); 30/43/2 | 2,386/16,743 (14.3%); 1,112/1,169/105 | 113/596 (19.0%); 48/60/5 |
| Current `market_p` 0.15–<0.50 | 1,095/6,385 (17.1%); 899/183/13 | 17,446/178,851 (9.8%); 15,347/1,938/161 | 1,382/9,432 (14.7%); 1,155/210/17 |
| Current `market_p` 0.50–<0.85 | 1,128/6,416 (17.6%); 921/197/10 | 18,469/174,124 (10.6%); 16,054/2,402/13 | 1,438/9,363 (15.4%); 1,202/226/10 |
| Current `market_p` ≥0.85 | 91/328 (27.7%); 35/47/9 | 2,015/13,042 (15.5%); 1,075/865/75 | 126/542 (23.2%); 50/65/11 |
| Current favorite correct | 1,595/9,175 (17.4%); 1,202/364/29 | 29,035/262,189 (11.1%); 23,487/5,238/310 | 2,074/13,610 (15.2%); 1,596/441/37 |
| Current favorite wrong | 794/4,284 (18.5%); 683/106/5 | 11,281/120,571 (9.4%); 10,101/1,136/44 | 985/6,323 (15.6%); 859/120/6 |

_Status counts in that table are in `work/train-luna/future_book_selection_regimes.json`; event-cluster intervals and paired fits are reproducible from `future_book_selection_stats.py` and `future_book_small_fits.py`._

**Metrics selection.** Research early stopping and saved validation metrics are conditional on current status OK and a populated +300 label: `select_validation_prediction_frame` and `select_validation_fit_frame` implement those filters (`src/train_model/train_model.py:98-123`), and metric construction receives the labeled future rows (`src/train_model/train_model.py:206-211`; `src/shared/utils/market_scenario_report.py:201-228`). The full-window metadata counts 382,760 usable current rows but only 342,444 with a 300-second target. Strategy backtest map results are not filtered by this label; fill-level `markout_300s` is null when its future reference is unavailable. The paired sensitivity below scores the base and relaxed-label fits on the latest validation maps.

**Small-fit sensitivity.** I fitted five paired LightGBM members (indices 0, 2, 4, 6, 8) with their published fixed tree counts and identical 90%-map samples. The augmented members add 1,835 recovered relaxed-label rows from the 1,563 maps already in research training; five members are a deliberately small sensitivity fit, not a replacement catalog. On the latest 40% of current-OK validation maps (283 maps, 116,006 exact-label rows), the paired base fit gained 0.291¢ MAE over no-move and the relaxed-label fit gained 0.290¢. The paired change was −0.0009¢ (95% event-cluster CI −0.0178¢ to +0.0156¢). The published 10-member research model gained +0.301¢ on the same rows.

For the large-delta calibration check, I fixed the subset at rows where the paired base fit predicted `|Δ̂|≥5¢` (1,776 rows). Its mean predicted absolute move was 5.36¢ against 10.54¢ realized; adding relaxed training labels reduced mean predicted absolute delta by 0.255¢ on the same rows (event-cluster 95% CI −0.370¢ to −0.153¢), moving farther from the realized move magnitude. On the broader latest-slice sensitivity using exact labels plus recovered relaxed labels (137,221 rows / 279 maps), MAE-gain change was +0.0022¢ (95% CI −0.0145¢ to +0.0190¢). The fixed-base large subset had mean predicted absolute move 5.37¢ versus 10.29¢ realized; the augmented fit again reduced predicted magnitude by 0.285¢ (95% CI −0.399¢ to −0.176¢). Thus these small fits show no meaningful change in overall MAE gain and a slight worsening of absolute scale for large predictions. They do not establish a new model-quality estimate because both the small sample and relaxed target retain market-quality and variable-horizon limits.

### 2. Money side of F2

**Finding: the replay shows a favorable hold path in both price buckets; it does not show fair-bound SELLs settling losing inventory.** I used the three existing LIVE artifacts under `data/backtests/dota_maker/LIVE/seed{0,1,2}` (the symlink target named above). For each submitted SELL quote where `price=ceil(token fair)`, I loaded the raw Telonex token book at the event timestamp and retained it only when `ceil(fair)>ceil(best ask)`. This matches the sell-target rule (`src/strategy/quoting.py:519-536`); quote events carry the submitted fair, token index, price, quantity and order ID (`src/backtest/telemetry.py:47-70`; emitted at `src/backtest/strategy.py:755-774`).

All 39,427 ceil-fair candidate rows had a fresh two-sided raw quote. The raw spread exactly matched the submitted event spread on 39,367 rows; median and p95 absolute spread difference were 0, maximum 3¢. Fair-bound-over-ask criteria selected 11,303–11,474 submitted quotes per seed, across 390–394 maps. Most rows are repeated reprices, so I also report the first qualifying quote per position episode and price bucket.

| Seed | Submitted SELL events | `price=ceil(fair)` candidates | Fair bound above ask | Held-token mid <0.85 | Held-token mid ≥0.85 |
|---:|---:|---:|---:|---:|---:|
| 0 | 17,811 | 13,068 | 11,343 | 9,279 | 2,064 |
| 1 | 18,383 | 13,344 | 11,474 | 9,498 | 1,976 |
| 2 | 17,887 | 13,015 | 11,303 | 9,267 | 2,036 |

For each seed/bucket, the table below tracks the open inventory represented by its first qualifying quote in each `(match, token, episode, price bucket)`. “Direct fill” means a later fill of that exact submitted order; “later SELL VWAP” allocates all later same-token SELL fills to the initial tracked shares FIFO. The marked counterfactual assumes every tracked share sold immediately at the raw best ask; it is a gross comparison before fees and without queue or fill-probability modeling.

| Seed | Held-token price | Episodes | Direct fair-order fills (median price) | Later SELL VWAP median | Held to settlement, win/loss | Actual later value − ask counterfactual |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | <0.85 | 530 | 23 (62¢) | 64¢ | 33 / 0 | +$1,449; +1.88¢/share |
| 0 | ≥0.85 | 86 | 1 (92¢) | 87.48¢ | 36 / 0 | +$327; +2.48¢/share |
| 1 | <0.85 | 518 | 17 (61¢) | 64.46¢ | 30 / 0 | +$1,462; +1.82¢/share |
| 1 | ≥0.85 | 84 | 1 (92¢) | 89¢ | 35 / 0 | +$408; +3.05¢/share |
| 2 | <0.85 | 513 | 20 (66¢) | 65¢ | 32 / 0 | +$1,867; +2.37¢/share |
| 2 | ≥0.85 | 88 | 1 (92¢) | 88.80¢ | 37 / 0 | +$549; +3.91¢/share |

The exact fair-bound order itself rarely filled in the high-price bucket; most of those positions were repriced and later sold, or remained through settlement. In all three seeds, every tracked high-price position that reached settlement held the winning token. No tracked episode settled as a loser. The gross later value exceeded the ask-at-trigger value in every seed and bucket. In this replay, high-probability fair bias appears to have held winners rather than delayed liquidation of losers. This is observational: actual sell-at-ask execution is not guaranteed, and the backtest did not replay the alternative policy.

**N1 reconciliation.** Terminal inventory in `results.parquet` matches N1’s held-map counts: seed 0 had 41 maps with inventory (35 non-dust), seed 1 had 39 (36 non-dust), and seed 2 had 39 (37 non-dust). Every non-dust terminal token was a winner. Seed 0 has one additional 0.1-share losing dust remainder; its settlement part is $0. The all-map `engine_pnl`, `cash_flow`, and settlement-part sums round to N1’s figures (seed 0: $2,848.59 / −$2,685.26 / $5,533.85; seed 1: $3,338.76 / −$1,993.57 / $5,332.33; seed 2: $3,320.89 / −$2,796.37 / $6,117.26). This small dust exception explains the distinction between N1’s “all held maps are winners” shorthand and the raw terminal index check.

**Limitations.** The tracked exposure is `min(position before quote, submitted SELL quantity)`. Later SELL fills are assigned FIFO to those shares, and any remainder is valued at terminal settlement. Price-bucket samples overlap when the same inventory episode crosses 0.85, so bucket dollar totals must not be added. Fractional unaccounted dust was under 0.23 shares per seed/bucket. The result is a replay diagnostic, not causal PnL attributable to the fair correction. The ask counterfactual assumes an immediate full fill at best ask and ignores queue, fill probability, and fees. The underlying research evaluation set was also used for early stopping in this model family, as recorded in `reports/train-luna.md` F1.

### 3. Horn bug impact on research metrics

I filtered the catalog to `horn_source=archive` and a positive-duration OpenDota pause with `−90≤time<0`, matching the pause included in the horn calculation. This reproduces the N4 validation set exactly: 57 maps contribute 28,353 labeled rows to the research metric window. I scored the same published research model and removed those maps without retraining.

| Metric | Full labeled validation | Excluding 57 maps | Change (clean − full) | Paired event-cluster 95% CI |
|---|---:|---:|---:|---:|
| MAE gain over no-move | 0.243¢ | 0.218¢ | −0.025¢ | −0.057¢ to +0.004¢ |
| Directional markout | 1.618¢ | 1.511¢ | −0.107¢ | −0.225¢ to −0.001¢ |

The MAE-gain shift is within noise. Directional markout drops by a small amount and its paired interval only narrowly excludes zero, so this is a borderline effect rather than a large change. The cleaned sample has 314,091 rows / 625 maps, compared with 342,444 rows / 682 maps overall. Calculations use the metric’s current-OK, labeled rows in market seconds `[−60,600)` and 2,000 event-series bootstrap replicates (`work/train-luna/horn_exclusion_metrics.py`; metric row selection in `src/train_model/train_model.py:98-123`).

## Overall assessment

The future-book gate removes 10.5–17.8% of current-OK candidate rows across the three pools, mostly for wide spreads, and the recovered dropped rows have a somewhat heavier large-move tail in research train and production. The five-member sensitivity found no measurable overall MAE change, while large predicted deltas became slightly smaller. For F2, the replay’s fair-bound holds settled only winning inventory and beat the immediate-ask gross mark in every bucket, but that counterfactual is not executable PnL evidence. Removing the archive-horn maps barely changed MAE gain; directional markout fell by a small, borderline amount.

## Limitations

The future-label status reconstruction is from cached historical books and cached match states. Relaxed labels are proxies: wide/inconsistent targets use a same-time two-sided midpoint after removing the spread/pair gate, while stale/missing targets use the first valid quote within 30 seconds after the intended +300-second timestamp. That second rule changes the horizon, and neither rule establishes the unobserved gated target. The dropped-versus-kept move comparison is therefore descriptive and may partly reflect different label quality or timing. The event-cluster bootstrap resamples event IDs over rows; it does not correct for systematic source or catalog selection. Regime rates are also descriptive, with rows across buckets and within an event not independent.

The paired fit is a small offline sensitivity: five of ten ensemble members, fixed published tree counts and map samples, and relaxed labels added only to maps already represented in research training. It is not a full retrain or an independent forward test. Its validation slice uses the latest 40% of current-OK maps; the strict-label score remains subject to the label gate, and the relaxed-label score inherits the proxy limitations above. The result supports only the narrow conclusion that this fit did not materially change aggregate MAE gain and slightly reduced large predicted deltas.

The F2 comparison is a replay diagnostic. “Sell at ask” assumes a full immediate fill at the observed best ask; actual queue position, available depth, fees, and fill probability are not modeled. Later sells are allocated FIFO to tracked shares; held inventory is valued at settlement. The price buckets overlap when an episode crosses 0.85 and must not be summed. The replay uses the same validation maps involved in research-model selection, so its position path is in-sample and does not estimate live causal PnL.

The horn comparison removes the 57 identified archive-horn maps from a fixed published model without retraining; it quantifies their contribution to the current metric, not performance after a corrected data rebuild. Analysis commands used the product checkout with `uv run --offline --no-sync` and an audit-local UV cache. `nice -n 10` emitted an operation-not-permitted warning in this environment, but the offline analysis jobs completed. No tests, full backtest, publication, network access, or VPS action were performed.
