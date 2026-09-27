# data-luna — data quality, datasets, and provenance
Status: FINAL

## Summary

- **S2:** Backtest map selection still depends on book quality through market second 899, although new BUY entries stop at game second 479. The current split drops 92/717 validation maps (12.8%); using the generous last-buy quote cutoff of second 489, at least 19 exclusions depend on later observations. This conditions reported replay coverage on post-entry data.
- **S3:** Crossed top-of-book quotes are accepted as `ok` mids and future labels. Reproduced one strict cross in four sampled maps; the affected current mid and one 300-second label both became 0.33.
- **S3:** One of 11,070 research training labels targets four seconds after catalog game end. Its state is at second 480, just outside the BUY-entry window; the market moved from 0.395 to 0.01, so it is a real but isolated terminal jump in training.
- **S3:** Archived live signal records do not include the full 12-feature model input. Train-versus-validation quantiles are measurable, but feature-by-feature live drift cannot be measured from the retained decision records without rebuilding fused state.
- No S1 data defect was confirmed. Catalog keys, label reconstruction, side orientation, and retained NW fields checked cleanly. Market-data coverage is uneven by month, so the retained training cohort is smaller than the catalog.

## Findings table

| ID | Severity | Layer | Title | Confidence | Estimated impact |
|---|---|---|---|---|---|
| data-luna-F1 | S2 | Backtest selection | Backtest book-gap exclusion uses post-entry market data | Verified | 92/717 validation maps are excluded; at least 19/717 are excluded only because of post-entry gaps under a generous entry cutoff. PnL effect is unquantified. |
| data-luna-F2 | S3 | Market cache / labels | Crossed books can pass the cache’s good-quote gates | Verified | Rare in four sampled maps; one current mid and one forward label were based on a crossed pair. |
| data-luna-F3 | S3 | Dataset labels | One training target samples a post-game market price | Verified | One of 11,070 research labels; target is 4 seconds after game end and 38.5c from the current mid. |
| data-luna-F4 | S3 | Live observability | Live signal archives omit the model’s full feature vector | Verified | Prevents direct live/train feature-distribution comparisons and slows diagnosis of model-input drift. |

## Findings detail

### data-luna-F1 — Backtest book-gap exclusion uses post-entry market data

**Where:** `src/prepare_dataset/prepare_dataset.py:111-120,338-339`; `src/shared/constants/dataset.py:10-23`; `src/backtest/selection.py:53-68,96-118` at esports-trader HEAD `bbb28897`.

**What and mechanism:** `find_longest_book_gap` counts consecutive non-`ok` seconds from `MODEL_START_SECOND=-60` up to, but not including, `VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE=900`. A maximum gap over 120 seconds sets `backtest_book_gap_excluded`. The research split retains those maps for ML validation, but `select_validation_matches` excludes them from bulk backtests. The policy’s last possible BUY state is game second 479 (`game clock <480`); with the 10-second feature-to-market lag, the last buy quote is market second 489. The selection flag therefore inspects as much as 410 seconds of later market quality before deciding whether an entire map belongs in the backtest.

**Evidence:** Ran `work/data-luna/check_gap_filter.py` against `data/new_model/research/split.parquet`: 717 validation maps, 92 flagged (12.83%); by month June 28, July 9, August 21, September 34. `work/data-luna/gap_filter_timing.py` read the 92 flagged maps’ current market caches: the longest qualifying gap starts after second 489 on 23 maps, crosses second 489 on 33, and ends before it on 36. It also replayed each bad run only through market second 489 (a generous allowance for the state-second 479 cutoff plus 10-second join): 73 maps would still exceed the 120-second threshold in that prefix, while 19 would not. Thus at least 19 of the exclusions depend on later book data even with the generous entry window; some of the other 73 may also have later portions. The longest-gap length median is 310 seconds and 75th percentile is 527 seconds. No PnL magnitude is claimed.

**Impact:** The historical backtest sample is conditioned on realized book availability well past the entry period. If later illiquidity or missing capture correlates with map type or outcome, backtest coverage and PnL can be biased. The exclusion is documented as deliberate in `dataset.py`, but the result should be reported separately from an entry-window-only cohort.

**How to confirm or fix:** Recompute eligibility using only data available through the last possible entry, then publish results alongside full-replay availability. If later books are needed to value open positions, keep that as a separate exit/settlement coverage gate rather than silently making it a map-selection gate.

**History:** `git blame` attributes the helper to `da0d72537` (“Harden backtest validation and add GRID diagnostics,” 2026-08-24). The repository also contains the earlier `1e894b24` change titled “Drop the look-ahead tape filter”; the current book-gap flag is a later, distinct exclusion mechanism.

### data-luna-F2 — Crossed books can pass the cache’s good-quote gates

**Where:** `src/shared/utils/telonex_book.py:89-103,243-260`; `src/shared/utils/trading.py:40-42`; forward target lookup at `src/shared/utils/telonex_book.py:268-279`.

**What and mechanism:** Best bid and ask are selected independently, but `resolve_market_pair` checks staleness, missing sides, spread width, and complementary midpoint sum without rejecting `bid >= ask`. `spread_ticks` computes `(ask-bid)/0.01`, so a negative spread does not trip the maximum-width gate. If both complementary pairs are crossed, their arithmetic mids can still sum to one and pass pair normalization.

**Evidence:** Ran `work/data-luna/crossed_used.py` on four per-map raw-book samples spanning November 2025, July, August, and September 2026. For match `8995085405`, market second 1205 was `ok` with Radiant bid/ask `0.34/0.32`, Dire `0.68/0.66`, and `market_p_radiant=0.33`. At market second 905, the same strict-cross snapshot was used as the +300-second target and produced non-null label `0.33`. The other three sampled maps had no strict-cross current quote or non-null label; a July target had five locked (bid=ask) seconds, which are not strict crosses. The separate raw-snapshot samples also show crossed rows are uncommon, but these four maps do not establish a project-wide rate.

**Impact:** A crossed snapshot is not an executable two-sided market. When selected as the latest as-of quote, it can create a false current feature or a false future target and can influence both training and validation. This was rare in the small reproduction; no expected-loss estimate is available.

**How to confirm or fix:** Reject a side quote when its best bid is greater than or equal to its best ask before calculating mids, or explicitly quarantine equality separately if locked books are valid for this feed. Track the rejection count by raw-data source and month.

### data-luna-F3 — One training target samples a post-game market price

**Where:** `src/market_data/build_market_data.py:92-134`; `src/shared/utils/telonex_book.py:268-279`; `src/prepare_dataset/prepare_dataset.py:137-165`; window constants at `src/shared/constants/dataset.py:13-19`.

**What and mechanism:** The forward-label builder looks up the latest market pair at anchor time +300 seconds and does not cap that lookup at catalog `ended_at`. Training includes minute states through second 540. For one retained row, state second 480 plus the 10-second lag and 300-second horizon puts the target 4 seconds after game end; the market has already moved to the terminal price.

**Evidence:** `work/data-luna/postgame_labels.py` compared `state_ts_us + 300s` with catalog `ended_at` for all non-null training labels. It found 1/11,070 rows: match `8788845470`, state second 480, target 4 seconds post-end, current market probability 0.395, target 0.01, Radiant lost, absolute label movement 0.385. Validation has zero post-end labels through state second 540 (350,940 rows). The policy’s BUY window ends before state second 480, so this example cannot create an in-window BUY training target, but it remains a training sample and may affect late fair-value behavior.

**Impact:** This is an isolated post-game target leak in research training. At one row of 11,070 it is unlikely to move aggregate performance materially, but it gives the model a near-terminal price jump from a live state at the entry cutoff.

**How to confirm or fix:** Add a label validity check that compares target wall time with `ended_at` before retaining a training row. Keep a count and sample of rows rejected for this reason; rerun training only after confirming the intended use of state 480+.

### data-luna-F4 — Live signal archives omit the model’s full feature vector

**Where:** `src/trader/match_worker.py:473-493`; `src/trader/core_trace_codec.py:82-89`; `src/trader/session_journal.py:260-279`.

**What and mechanism:** The live core signal stores predicted delta, receipt time, anchor probability, and deaths. The trace codec allows only those five signal fields. Session signal records retain market quotes, prior, fair values, and decision state, but not radiant/dire net worth, XP advantage, top-one net-worth advantage, or top-one ratios. The feed-specific state archives exist, but the logged inference record does not preserve the fused 12-feature row that generated the prediction.

**Evidence:** Inspected successful signal rows in local Dota runs `grid-3007986-m3`, `9007700576`, and `grid-3007276-m1`; none carried the full feature vector. The persisted fields match the source schemas above. This prevents a direct, row-level live distribution comparison or reconstruction of the exact feature values seen by the model from the signal archive alone.

**Impact:** A live-only input shift can be mistaken for model, market, or execution drift. Debugging requires independently joining feed snapshots and reproducing the fusion path, which may not recover the exact state used at inference.

**How to confirm or fix:** Persist the normalized 12-feature input alongside each model signal, with model/version and source timestamps. If volume is a concern, retain a bounded sample plus per-feature summary counters, but keep enough records to reproduce a prediction.

## Architecture / performance / debuggability notes

1. **Coverage and cohort attrition.** The catalog has 3,207 maps: 2,485 before the validation cutoff and 722 after. The current market cache has 3,041 files (2,324 train and 717 validation maps). The research datasets contain 1,563 train maps and 717 validation maps; production minute training contains 2,225 maps. For train maps, the sampled minute-row retention breakdown is 161 with no cache, 503 with cache but no good current quote, 245 with current quotes but no usable 300-second label at sampled minutes, 13 with signal points but no final dataset map, and 1,563 retained. Month examples: October 2025 retains 6/58 catalog maps; January 2026 retains 110/360; February retains 359/551. This is market-data availability/quality attrition, not a mass of empty STRATZ net-worth fields. It preferentially retains markets with enough usable books, so model coverage should be described as the retained, tradable-data cohort.

   Validation contains 1,859,416 exact-second rows: 1,390,644 `ok` (74.8%), 344,826 `wide_spread` (18.5%), 103,060 `stale_quote` (5.5%), and 20,886 `missing_quote` (1.1%). No non-`ok` row has a current mid, and no `ok` row lacks one.

   In the `ok` validation path, market probability stays in [0.001, 0.999]. Between successive `ok` rows within each map, there are 2,074 changes over 10c, 166 over 25c, and 15 over 50c; a bad-status gap may separate successive rows. These are candidate event spikes, not proof of bad snapshots; this audit did not individually classify them. Midpoint bounds do not establish the exchange’s raw tick-size regime near the extremes.

2. **Train versus validation feature shift.** `work/data-luna/price_checks.py` compares research training rows with validation rows at the same minute-aligned state seconds (-60 through 540), retaining only validation rows with `ok` current prices and non-null labels. It compares 11,070 training rows with 5,804 validation rows. Quantiles are Q05 / median / Q95; `D` is the two-sample KS statistic (no p-values because repeated rows within maps are not independent).

   | Feature | Train Q05 / Q50 / Q95 | Validation Q05 / Q50 / Q95 | Mean shift (val−train) | KS D |
   |---|---|---|---:|---:|
   | Radiant NW advantage | -1,691 / 0 / 1,799 | -1,774 / 0 / 1,723 | -55.7 | 0.057 |
   | Radiant NW | 2,950 / 7,247.5 / 14,162.7 | 2,950 / 6,792.5 / 14,128.7 | -386.2 | 0.082 |
   | Dire NW | 2,950 / 7,256.5 / 14,103.6 | 2,950 / 6,784.5 / 14,227 | -330.5 | 0.081 |
   | Radiant XP advantage | -1,440 / 0 / 1,560 | -1,474 / 0 / 1,400 | -35.0 | 0.045 |
   | Radiant deaths | 0 / 1 / 6 | 0 / 1 / 6 | -0.11 | 0.052 |
   | Dire deaths | 0 / 1 / 6 | 0 / 1 / 6 | -0.14 | 0.064 |
   | Top-one NW advantage | -556 / 0 / 599 | -560 / 0 / 564 | -7.1 | 0.045 |
   | Radiant top-one NW ratio | 0.255 / 0.375 / 0.470 | 0.250 / 0.370 / 0.476 | -0.009 | 0.081 |
   | Dire top-one NW ratio | 0.255 / 0.377 / 0.471 | 0.250 / 0.370 / 0.471 | -0.010 | 0.081 |
   | Market prior | 0.234 / 0.495 / 0.755 | 0.185 / 0.480 / 0.795 | -0.007 | 0.086 |
   | Current market probability | 0.195 / 0.495 / 0.800 | 0.165 / 0.490 / 0.820 | -0.010 | 0.071 |

   The shifts are modest in absolute units but consistent across NW level, top-one ratios, and prior tails. These sets also come from different date ranges and train/validation state construction, so this is a cohort/source shift signal, not evidence of one broken feature transform. `second` is intentionally unlike: train uses minute support -60, 0, …, 540; validation uses exact market seconds through 6,648 (state second is market second minus 10). A full-row KS for `second` would mostly measure the designed sampling-window difference.

3. **Live comparison is not measurable from the retained signal rows.** The three local live records inspected lack raw model features (F3). No per-feature live quantiles or KS statistics are reported rather than inferring them from incomplete decision fields.

4. **Raw-book source transition is plausible but not fully attributable.** `work/data-luna/catalog_raw_metrics.txt` sampled one paired map per month. A November 2025 token file had 19,895 rows, 10,964 duplicate timestamp rows, all timestamps millisecond-rounded, and 12 ms median spacing between unique timestamps. A July 2026 sample had 88,113 rows, 16,192 duplicate rows, millisecond-rounded timestamps, and 8 ms median spacing. August had no duplicate timestamps, 12,314 non-millisecond timestamps among 107,519 rows, and 5 ms median spacing; September had no duplicates, 9,118 non-millisecond timestamps among 90,699 rows, also 5 ms median spacing. This is consistent with a raw-capture format/cadence change around the collector transition, but source identity is not tagged per row in the local Telonex-compatible Parquet tree. The sample cannot pin down the exact boundary or attribute all differences to the collector.

   Rebuilding the current market cache from raw books for 12 selected maps (one per month, Oct 2025–Sep 2026) matched stored status, current probability, and 30/300-second labels exactly: zero field differences across roughly 2.5k–11.5k compared values per map. This is good sample evidence, not a complete raw-store audit. The cache version hashes quality/horizon parameters only (`src/market_data/build_market_data.py:37-51`), and the builder skips existing per-map cache files (`:164-168`); a raw backfill alone will not refresh an existing cache. I found no mismatch in the 12 rebuilds.

5. **League/tier slice comparison is incomplete.** The local OpenDota `pro_matches` join reaches 2,189/3,207 catalog maps, but only 1,327/1,563 research train maps and 92/717 validation maps; validation coverage is largely older than the recent cohorts. Local live `match.json` uses GRID league IDs, which are a different identifier namespace. The available local joins therefore cannot support a reliable train-versus-validation-versus-live league/tier distribution comparison.

## Checked and OK

- **Catalog keys:** `work/data-luna/data_metrics.txt` reports 3,207 rows and zero duplicate `match_id`, `condition_id`, token-0, or token-1 values. Repeated event IDs are expected for multiple maps in a series. There are 67 catalog rows without GRID `spawn_at`; those use horn fallback for `start_time`.
- **Split boundary:** `VALIDATION_START_TIME=1,780,563,592` is used through `CatalogEntry.start_time` (`src/shared/utils/match_catalog.py:46-68`), which chooses GRID spawn when present and otherwise horn. No map lies within 90 seconds of the cutoff. The sole map within 900 seconds has no material horn/spawn residual; every >1-second residual is on an archive-horn row, and the closest such row is 7,557,578 seconds from the cutoff. Thus the observed archive/spawn timing differences do not change this train/validation split.
- **Horn/end conventions:** GRID-derived horn equals spawn + 90 seconds plus pre-horn OpenDota pauses. The 45 >1-second spawn/horn residuals all use archive horn timing; maximum residual is 449.229 seconds. `ended_at` is generated from horn + game duration + pauses in `src/collect/s06_publish_catalog.py:129-138`, so exact end residual is a consistency check against that construction, not independent proof of external end time.
- **Prior and orientation:** On 2,867 cached maps, prior versus first usable in-game mid has mean absolute difference 5.27c, median 2.47c, p95 24c, and mean signed difference +0.26c; first valid quote is at median game second 0. This shows movement after the pregame quote, not a persistent side offset. In 627/717 validation maps with a quote within the last game minute, 624 were on the same Radiant side as the STRATZ winner and 576 were within 5c of terminal. Three late quotes did not point to the eventual winner; those disagreements alone do not establish an orientation error. For `8992034384`, the clearest case (p=0.12 at game end despite a Radiant win), catalog winner, OpenDota team names, GRID archive team order, and trader `yes_is_radiant=true` agree.
- **Net-worth integrity:** Retained train rows have zero `radiant_nw==0` and zero `dire_nw==0`. The older-month drop pattern is mostly missing/poor market coverage; it does not look like empty NW values among retained rows.
- **Label timing and exactness:** Training uses state second `S`, current market row `S+10`, then a +300-second wall-clock target (`src/prepare_dataset/prepare_dataset.py:137-165`; `src/market_data/build_market_data.py:92-134`; `src/shared/utils/telonex_book.py:268-279`). Twelve monthly raw-book rebuilds reproduced cached current probabilities and 30/300-second labels exactly. Comparing against game-second row `M+300` is not valid because the label target is wall-clock +300 and pauses break game-second equivalence.
- **Post-game label check:** `work/data-luna/postgame_labels.py` compares exact target wall time (`state_ts_us + 300s`) with catalog `ended_at`. Training has 1/11,070 post-end labels (0.009%), detailed in F3. Validation’s full through-map table has 8,726/1,224,779 post-end labels (0.71%); 81.8% are within 5c of the terminal outcome. There are zero post-end labels in the validation model window through state second 540 (350,940 rows) and zero in the policy entry window through state second 479 (316,487 rows). Thus late terminal labels are present in full validation, but the entry window is clean and validation’s training/evaluation window is clean.
- **Current quote/status consistency:** The validation file has no non-`ok` current probability and no `ok` row missing a current probability. Sampled `market_p_radiant` values stay within [0.001, 0.999].

## Open questions / Needs from VPS

- No VPS request is required to complete this data-scope pass. The local trader archives are sufficient to verify that full feature vectors are not persisted, but not to calculate live feature distributions.
- A matched live feature-distribution report requires logging or reconstructing the actual fused 12-feature vector at inference, with model version, game second, and source timestamps.
- The four raw-book map samples and twelve cache rebuild samples do not establish project-wide crossed-book rates or prove the precise paid-Telonex-to-collector provenance boundary. A source-tagged raw manifest would make those checks reproducible.
- Extreme-price tick-size behavior was not exhaustively validated from raw tick metadata; the cached midpoint range is bounded, but it is not enough to identify tick-size regime changes.
- No tests, collection, training, model publishing, or full backtests were run. The esports-trader repo remained read-only; its pre-existing unrelated working-tree changes were left untouched.
