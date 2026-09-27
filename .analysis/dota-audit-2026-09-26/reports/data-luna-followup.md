# data-luna — crossed and locked books: sample prevalence and LIVE replay impact
Status: FINAL

## Summary

- In a deterministic, month-stratified sample of 156 maps (13 per month, with August split across both source eras), strict bid-over-ask snapshots are rare but present: 5,962 of 12,965,116 two-sided token snapshots (0.0460%). Locked snapshots are more common: 8,556 (0.0660%).
- Of 216,758 sampled `ok` market seconds, 389 (0.1795%) use at least one crossed or locked as-of token quote: 38 strict-cross seconds and 351 lock seconds. Each strict-cross case has both complementary tokens crossed, and all 38 pass the pair-sum gate.
- The sampled training rows are unaffected (0/434 current mids, 0/434 labels). Validation has 235/100,828 `ok` current rows flagged (0.233%) and 330/88,627 available 300-second labels flagged (0.372%). Of 23,213 `ok` entry rows at market second ≤489, 15 are flagged (0.0646%): 3 strict crosses and 12 locks.
- LIVE replay shows strict-crossed books can overlap real BUY fills: on the unstripped `grid_v1` subset, 41 same-order fills followed a strict-crossed ask below the resting limit within 161ms, totaling $1,599.61. The aggregate replay also contains raw schedule-archive overlaps, which need the own-size-stripping caveat below.
- Counterfactual strict-only filtering changes 38 cached current market seconds; 10 lose `ok` status and 28 remaining prices move by at least 1¢. The broader `bid >= ask` filter changes all 389 flagged current seconds and drops 117 from `ok`, while also changing 525 cached 300-second values. Equality should not be treated as a strict cross.

## Scope and method

The sample selector takes 13 evenly spaced, raw-and-cache-available catalog maps per month from October 2025 through July 2026, 4 paid-Telonex plus 9 collector maps in August, and 13 collector maps in September. Era is assigned from the catalog anchor against the requested 2026-08-08 UTC boundary. Maps are processed sequentially; each read is limited to its own two token books and market window through the 300-second label horizon. This is a stratified sample, not a uniform random sample.

For each raw snapshot, the script applies the production usable-level rule and records strict `bid > ask` and locked `bid == ask` separately, with rates over two-sided snapshots. Market-second status and midpoint are recomputed with the production as-of lookup and gates; all sampled cache rows rebuilt exactly. Current validation time is its market second; training current time is state second +10s; each 300-second label is classified at current market time +300s. Entry rows use market second ≤489.

Source code confirms the current parser selects max usable bid and min usable ask independently, retains equality, and has no crossed-book check (`src/shared/utils/telonex_book.py:89–103`). The as-of lookup walks back through one-sided snapshots for at most five seconds (`src/shared/utils/telonex_book.py:221–240`); pair resolution applies the spread upper bound and 5¢ pair-sum tolerance but does not require positive spread (`src/shared/utils/telonex_book.py:243–260`). `build_market_data.py:92–133` writes current and +30/+300-second values; `prepare_dataset.py:115–170` and `192–220` join training and validation rows.

## Raw-book rate by month and source era

Raw snapshot rates below use two-sided token snapshots as the denominator. `Both-cross & pair-sum-pass` counts are market-second as-of pairs, divided by the `ok` market seconds for that month; the final column counts any `ok` second with a crossed or locked token quote. Monthly sample output: `work/data-luna/crossed_sample_summary.txt:4–17`.

| Month | Source era | Maps | Two-sided token snapshots | Strict crosses | Locked | Both-cross & pair-sum-pass seconds / `ok` seconds | Flagged `ok` seconds / `ok` seconds |
|---|---|---:|---:|---:|---:|---:|---:|
| 2025-10 | paid Telonex | 13 | 16,074 | 0 (0.0000%) | 0 (0.0000%) | 0 / 1,406 (0%) | 0 / 1,406 |
| 2025-11 | paid Telonex | 13 | 343,876 | 48 (0.0140%) | 18 (0.0052%) | 5 / 19,255 (0.0260%) | 5 / 19,255 |
| 2025-12 | paid Telonex | 13 | 169,326 | 178 (0.1051%) | 298 (0.1760%) | 0 / 12,409 (0%) | 23 / 12,409 |
| 2026-01 | paid Telonex | 13 | 87,162 | 110 (0.1262%) | 194 (0.2226%) | 5 / 5,442 (0.0919%) | 36 / 5,442 |
| 2026-02 | paid Telonex | 13 | 285,326 | 354 (0.1241%) | 570 (0.1998%) | 0 / 16,489 (0%) | 29 / 16,489 |
| 2026-03 | paid Telonex | 13 | 587,290 | 512 (0.0872%) | 1,058 (0.1801%) | 0 / 20,544 (0%) | 17 / 20,544 |
| 2026-04 | paid Telonex | 13 | 404,704 | 180 (0.0445%) | 332 (0.0820%) | 0 / 14,665 (0%) | 5 / 14,665 |
| 2026-05 | paid Telonex | 13 | 2,063,972 | 772 (0.0374%) | 1,228 (0.0595%) | 0 / 25,286 (0%) | 39 / 25,286 |
| 2026-06 | paid Telonex | 13 | 1,530,300 | 518 (0.0338%) | 916 (0.0599%) | 1 / 20,590 (0.0049%) | 31 / 20,590 |
| 2026-07 | paid Telonex | 13 | 3,124,340 | 1,172 (0.0375%) | 1,978 (0.0633%) | 5 / 32,373 (0.0154%) | 37 / 32,373 |
| 2026-08 | paid Telonex | 4 | 676,914 | 936 (0.1383%) | 548 (0.0810%) | 2 / 9,321 (0.0215%) | 28 / 9,321 |
| 2026-08 | collector | 9 | 1,935,390 | 582 (0.0301%) | 606 (0.0313%) | 0 / 15,913 (0%) | 25 / 15,913 |
| 2026-09 | collector | 13 | 1,740,442 | 600 (0.0345%) | 810 (0.0465%) | 20 / 23,065 (0.0867%) | 114 / 23,065 |

Era totals: paid-Telonex 4,780/9,289,284 strict crosses (0.0515%) and 7,140/9,289,284 locks (0.0769%); collector 1,182/3,675,832 strict crosses (0.0322%) and 1,416/3,675,832 locks (0.0385%). Both tokens were crossed and the pair-sum gate passed in 18/177,780 (0.0101%) paid-Telonex `ok` seconds and 20/38,978 (0.0513%) collector `ok` seconds. Current flagged `ok` seconds are 250/177,780 (0.1406%) paid-Telonex and 139/38,978 (0.3566%) collector. The sample contains 12,965,116 two-sided snapshots from 13,162,332 raw snapshots overall.

The 38 current strict-cross cases are market-second as-of pairs: both token quotes are crossed and the normalized pair-sum gate passes in every case. Counts are in the monthly and era pair columns above, with full breakdown in `work/data-luna/crossed_sample_summary.txt:4–17`. They are spread across November 2025, January 2026, June–August paid-Telonex, and September collector. The crossing spread is negative, so it is below the configured maximum-spread threshold; there is no separate `bid < ask` guard before a quote can be `ok`.

## Where crossed and locked quotes land

| Sample rows | Crossed or locked | Share | Breakdown |
|---|---:|---:|---|
| `ok` market seconds | 389 / 216,758 | 0.1795% | 38 crossed, 351 locked |
| Training current mid | 0 / 434 | 0% | — |
| Training 300-second label | 0 / 434 | 0% | — |
| Validation current, `ok` rows | 235 / 100,828 | 0.2331% | 28 crossed, 207 locked |
| Validation current, all rows | 235 / 139,797 | 0.1681% | Every flagged row is `ok` |
| Available validation 300-second labels | 330 / 88,627 | 0.3724% | 26 crossed, 304 locked |
| Entry window, `ok` rows (market second ≤489) | 15 / 23,213 | 0.0646% | 3 crossed, 12 locked |
| Entry window, all rows | 15 / 28,080 | 0.0534% | Every flagged row is `ok` |

All 15 flagged entry rows are in September collector maps. No sampled training row has a flagged current quote or label. Validation-row counts are restricted to sampled maps; they are not estimates of the full validation dataset without a sampling-weight adjustment. Totals and per-month splits are in `work/data-luna/crossed_sample_summary.txt:4–17`.

### Difference from nearest uncrossed snapshot

For each flagged `ok` current market second, I compare the normalized Radiant probability with the pair reconstructed from the nearest strict-uncrossed (`bid < ask`) snapshot in each token book, chosen independently by absolute time distance on either side of the target. This is a diagnostic comparison, not a ground-truth error: a nearby uncrossed quote may precede a real fast repricing.

Across the 389 flagged current seconds (778 flagged token observations), the absolute probability difference has median 1¢, p95 27¢, and maximum 42¢. Strict-cross cases have median 1.5¢, p95 40¢, maximum 40¢; locked cases have median 1¢, p95 24¢, maximum 42¢. The nearest uncrossed snapshot is 2.105s away at the median and 10.015s at p95.

One large case illustrates the limitation: map `8642367784`, market second 2575, caches a `0.99` Radiant probability from locked `0.99/0.99` and `0.01/0.01` quotes. The closest uncrossed pair is `0.57/0.43`, 2.213 seconds earlier, a 42¢ difference; subsequent raw records at the locked price are one-sided. The 42¢ delta is therefore not proof that the locked quote is bad—it may reflect a genuine fast repricing, and filtering equality would fall back to an older price.

## LIVE seed0 replay

`LIVE/seed0/manifest.json` records 613 selected matches and queue-position execution. `quote_events.parquet` has rows for 612 matches; 547 have accepted orders (40,289 accepted order intervals). `fills.parquet` contains 3,282 fills across 401 matches, $143,350.99 total fill notional. All 132 rejected order events carry a post-only “would have been a TAKER” rejection reason. Run-level totals and rejection reasons are saved in `work/data-luna/live_cross_summary.txt:1–9`.

The per-token raw windows contain 6,312,365 book snapshots while at least one order on that token is active. They include 1,521 strict-cross snapshots across 314 maps (0.0241% of these token snapshots) and 4,172 locked snapshots across 431 maps. Ask/bid comparisons below count snapshot–order pairs, since several orders can rest on the same token at one snapshot.

| Signal source in LIVE results | Maps with accepted orders | Token snapshots while resting | Strict crosses | Locked | Best ask below active BUY limit (pairs; strict-cross-book subset) |
|---|---:|---:|---:|---:|---:|
| `grid_v1` (unstripped) | 412 | 4,920,804 | 1,142 | 3,385 | 355,966 (259) |
| `schedule:grid` (archive book strip applies) | 123 | 1,263,304 | 352 | 660 | 31,789 (62) |
| `schedule:oddin` (archive book strip applies) | 12 | 128,257 | 27 | 127 | 1 (1) |
| **Total** | **547** | **6,312,365** | **1,521** | **4,172** | **387,756 (322)** |

For 367 fill rows, the nearest strict-cross snapshot on the same token was within ±2 seconds (294 unique orders, 41,417.06 shares, $25,532.09 notional); the crossed snapshot preceded 342 fills ($24,753.96), and followed 25 ($778.13). Among the 342 cross-before-fill rows, `queue_ahead` at submission was positive for 238 and zero for 104. In the unstripped `grid_v1` subset, 256 fills followed a same-token strict cross ($18,778.26). This ±2-second proximity alone is not causal attribution. Detailed joins are in `work/data-luna/live_cross_fill_timing_summary.txt:1–7`.

The narrower order-price check is stronger: 126 same-order BUY fills occurred after a snapshot where best ask was below that resting order’s limit and within 2 seconds (81 orders, 8,544.67 shares, $4,891.93 notional); all were within 316ms. There were 20 additional nearby fills where the fill preceded the ask-below snapshot. On unstripped `grid_v1` books, 98 of the after-snapshot fills (56 orders) total $3,259.32. Restricting to cases where the public top of book was itself strictly crossed leaves 68 after-snapshot fills (48 orders, 5,788.55 shares, $3,226.82); 41 of these were unstripped `grid_v1` fills (24 orders, 2,793.78 shares, $1,599.61). Those 41 followed the crossed ask by 0–161ms (median 1µs). The other 27 strict-cross overlaps are in schedule-bound maps and may change under the archive book strip.

`fills.parquet.queue_ahead` is the depth context captured at order submission, not the matching engine’s remaining queue at fill time (`src/backtest/telemetry.py:12–29`, `src/backtest/strategy.py:1260–1290`). For the 68 after-snapshot fills on a strictly crossed public book, it was positive at submission for 52 and zero for 16; in the 41 unstripped `grid_v1` subset it was positive for 29. Therefore it cannot establish that the queue was still positive when the crossed snapshot arrived. The order-price join is detailed in `work/data-luna/live_cross_fill_timing_summary.txt:15–21`.

### Framework matching behavior

The LIVE maker creates GTC post-only limit orders (`src/backtest/strategy.py:699–715`). At initial submission, Nautilus rejects a post-only limit if the current best ask is at or below a BUY limit (`nautilus_trader/backtest/engine.pyx:5297–5316`; `matching_core.pyx:400–411`). After acceptance, an L2 book delta is applied and the matching engine iterates (`engine.pyx:4212`, `4287`, `5722–5750`). A BUY is marketable when `best_ask <= limit` (`matching_core.pyx:303–308`, `400–407`); with the passive-book profile and liquidity consumption enabled, it reads crossed levels from the L2 book (`prediction_market_extensions/backtesting/data_sources/replay_adapters.py:564–573`, `_prediction_market_backtest.py:445–463`, `engine.pyx:6720–6725`). If queue-position tracking still has positive same-price volume ahead, `fill_limit_order` can return without filling; otherwise visible crossed ask liquidity can fill, and a maker BUY’s fill price is adjusted to its own limit (`engine.pyx:6521–6529`, `6811–6831`).

Thus a later crossed ask below a resting BUY limit is eligible for an immediate fill on that L2 update when current queue state allows it. The 41 unstripped strict-cross same-order fills above match that path closely. Queue-ahead-at-submit is not enough to tell which were queue-cleared; the stored telemetry has no match-time queue value.

The raw scan reads captured Telonex books. The replay source bridge rewrites book days for schedule-bound archives to subtract the bot’s own live resting size (`src/backtest/telonex_local.py:83–140`, `260–289`; `src/backtest/strip_own_book.py:257–281`). The table’s schedule counts are therefore pre-strip raw overlaps and are an upper bound on strict crosses actually seen by Nautilus; the `grid_v1` rows are not stripped. This is why the strongest direct replay comparison is the unstripped `grid_v1` subset.

## Counterfactual load filter

The counterfactual set is the 73 sampled maps with at least one baseline `ok` current crossed/locked quote or one flagged sampled validation 300-second label. For a filtered snapshot, both sides are made unusable while its timestamp is retained; the existing five-second as-of fallback and pair gates are then rerun. This models rejecting that snapshot at parse time without changing any project code. The current/label counts below are observed sample effects, not a full retraining or backtest rerun; exact output is preserved in `work/data-luna/crossed_filter_counterfactual_summary.txt:1–76`.

| Filter | Cached current values changed | Cached current `ok` → non-`ok` | Cached +300 values changed | Sample validation current values changed | Sample validation labels changed | Entry-window current changes |
|---|---:|---:|---:|---:|---:|---:|
| Reject strict crosses only (`bid > ask`) | 38; 28 by ≥1¢, max 27¢ | 10 | 36; 28 by ≥1¢, max 25¢ | 25; 4 lose `ok`, max price shift 24.5¢ | 19; max 19.5¢ | 3; 1 loses `ok`, max price shift 4¢ |
| Reject crosses and locks (`bid >= ask`) | 389; 160 by ≥1¢, max 95¢ | 117 | 525; 190 by ≥1¢, max 103¢ | 220; 40 lose `ok`, max price shift 61.55¢ | 299; max 66.9¢ | 15; 1 loses `ok`, max price shift 10.5¢ |

Strict-only filtering changes no current mid or label in the 434 sampled training rows. The `>=` filter also changes no sampled training row, but removes valid equality quotes in validation and increases `ok`-status loss and fallback price movement sharply. In the 23,213 `ok` entry rows, the strict-only case changes 3 current quotes (one becomes non-`ok`); rejecting equality as well changes all 15 flagged entry quotes (one becomes non-`ok`).

## Verdict

This is a real, low-frequency data-integrity issue, not merely noise. Add a strict-cross rejection (`bid > ask`) at the raw ingest points that feed both market-second construction and L2 replay. In the sample it changes 38 `ok` current seconds and 26 available validation labels; LIVE raw intervals also align with actual resting-order BUY fills, including 41 unstripped strict-cross same-order fills totaling $1,599.61. Do not reject `bid == ask` under the same rule: locks occur more often, can be internally consistent, and `bid >= ask` filtering changes 117 current cache rows from `ok` to non-`ok` and moves many retained values.

The data impact is small in ordinary training and entry rows (0/434 training rows, 3/23,213 `ok` entry seconds under strict-only filtering), but the matcher can execute on a later crossed ask. Applying the guard only to the market-seconds parser would leave the separate L2 replay path exposed; both paths need the same strict-cross policy. I did not modify code or rerun the backtest with filtered books.

## Evidence and limitations

- Sample selection (3,207 catalog entries and the month/era allocation): `work/data-luna/crossed_sample_selection_summary.txt:1–3`. Exact sample summaries and nearest-uncrossed diagnostics: `work/data-luna/crossed_sample_summary.txt:1–20`, `crossed_sample_per_map.csv`, and `crossed_sample_mid_errors.csv`; strict-only versus `>=` values: `crossed_filter_counterfactual_summary.txt:1–76` and `crossed_filter_counterfactual.csv`.
- Replay counts: `work/data-luna/live_cross_summary.txt`, `live_cross_per_map.csv`, `live_cross_events.csv`; fill-time joins were calculated by `work/data-luna/summarize_live_cross_forward.py` against `LIVE/seed0/fills.parquet` and `quote_events.parquet`.
- Replay source: Esports Trader `results.parquet` reports 464 `grid_v1`, 136 `schedule:grid`, and 13 `schedule:oddin` results. Among maps with accepted orders, raw source windows covered 412 `grid_v1`, 123 `schedule:grid`, and 12 `schedule:oddin` maps. Archive stripping limits comparison of schedule raw crosses with framework-visible crossings.
- Reproduction was run from `$E` using `nice -n 10 env UV_CACHE_DIR=/private/tmp/uv-cache-data-luna PYTHONPATH=src uv run --offline --no-sync python <scratch-script>`; the per-map scripts read one token-book window at a time. `nice` printed a permission warning but the scripts completed.
- Raw book reads are per map and sequential. The 156-map catalog sample is stratified and chronologically even-spaced, not randomized; reported rates describe only that sample.
- The nearest-uncrossed comparison is not an independent fair-price oracle. The ±2-second fill join is temporal association, not proof that each nearby raw event caused the fill; the close same-order joins and framework matcher behavior are supporting evidence.
- No product code, source data, model, or backtest artifact was changed. Scratch scripts and outputs remain under `work/data-luna/`.
