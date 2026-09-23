# LoL vs Dota live tape analysis

## TL;DR

1. **LoL is near flat per dollar bought, while Dota is positive.** Since 2026-09-18, known-net LoL maps returned −0.145% of BUY notional before rebate (−$0.99 on $681.03); Dota returned +3.447% (+$324.70 on $9,418.62). The shared rebate-included LoL summary is +$2.27, still small beside Dota’s +$380.31 combined summary net.
2. **The live 30-second BUY markout has the opposite sign from the supplied backtest.** LoL is −0.86¢ (56 fills), versus backtest +0.51¢; Dota is +0.85¢ (278 fills), versus backtest −0.38¢. This is the clearest tape evidence that the LoL queue-fill simulation is too favorable, or that its live input/selection differs materially.
3. **The shared Dota source split is not a feed-source split.** Its helper labels archive IDs starting `grid-` as GRID and all other IDs as Steam. Local `match.json.feed_source` instead shows 46 Dota GRID maps and 51 Dota Oddin maps since 9/18, with no local Steam-source maps. One `grid-*` archive is actually `feed_source=oddin`; 50 numeric Oddin archives are classified as Steam by the helper.
4. **Dota’s positive result is concentrated in its Oddin-source tapes.** Since 9/18, Dota/Oddin returned +3.96% of known-map BUY notional and +1.21¢ at BUY +30s; Dota/GRID returned +2.41% and −0.46¢. LoL/GRID returned −0.145% and −0.86¢. Source mix explains part of the broad Dota advantage, but LoL still trails Dota GRID in this sample.
5. **LoL model signals do not rank subsequent 300-second moves in the latest window.** Pearson r is −0.121 across 12,830 paired rows; its equal-count prediction quartiles move from +4.10pp realized for the lowest predicted quartile to −1.44pp for the highest. Dota is also weak at r=+0.041.
6. **LoL does not have a high stale/missing-book share, but its entries are later and smaller.** Median first BUY is second 241 for LoL vs 155 for Dota; observed BUY quote notional median is $5 vs $60 for Dota GRID. LoL’s distinct signal-second gaps are 3s median / 12s P90; Dota Oddin is 1s / 1s, while Dota GRID is 4s / 14s.
7. **The local tape set is missing one Dota live map.** The shared summary lists `9012316577` with 22 fills, but `data/trader/9012316577/` is absent locally. The local Dota count is therefore 97 maps / 63 maps with fills against 98 / 64 in the shared summary.

## Method and scope

I filtered `data/trader/*/session.jsonl` to sessions whose `session_start.execution_mode` is `live`, then used `match.json.joined_at_utc` (UTC) for the two requested windows. I read the journals through the existing `scripts.measure_live_grid_cadence.read_jsonl` and `is_polymarket_signal` helpers. The script is read-only against `esports-trader`; all new files are under this analysis directory. The persisted schema defines the live-mode session start at `src/trader/archive_types.py:170-186`, quote size/price at `:224-247`, signal second and market fields at `:204-218`, fill exchange timestamp and game second at `:250-267`, and terminal cash/inventory at `:306-319`.

A fill notional is `price × size`, separately summed for BUY and SELL. Fills include `fill` and `late_fill`, deduplicated by `fill_key`. Per-map traded outcome is resolved by comparing the fill token ID to `match.json.market.yes_token_id` / `no_token_id`. Observed clip is each placed BUY quote’s `price × size`; tables use the pooled median and P90, not a configured clip value. First entry is the first BUY fill’s game second. Hold time FIFO-matches BUY shares to later SELL shares of the same token using `fill.ts_utc`; the per-map statistic is size-weighted over shares that were sold, so it excludes any unmatched open inventory.

Net is the last `session_end.net_cash + inventory_value`. If those are null or absent, I use the matching per-map shared-summary net; that fallback includes rebate, unlike the session-end calculation. `net / BUY` is summed known-map net divided by BUY notional from those same known-net maps. Maps with unknown terminal net are excluded from both numerator and denominator. This leaves 43/44 recent LoL maps and 90/97 local Dota maps with known net; one filled LoL map and several Dota maps have unknown terminal net.

Signal rows persist a game `second`, not a wall timestamp; fill rows persist the exchange `ts_utc` and game second. For markouts, I estimate a per-map offset as the median of `fill.ts_utc − horn_at_utc − fill.second`, map signal seconds to `horn_at_utc + offset + second`, and choose the first available traded-token mid at or after each fill’s actual wall time +10/+30/+60/+300s, within 10 estimated seconds of the target. The median offset is 8.56s for recent LoL and 9.06s for recent local Dota; median per-map absolute fill-offset deviation is 2.00s and 0.99s respectively. This is a proxy: pauses and long clock stalls can break the constant-offset assumption, and the tape cannot resolve exact signal arrival times.

BUY markout is `future token mid − fill price`; SELL markout is `fill price − future token mid`, so positive means a favorable move after execution for either side. For model calibration, `market_p_radiant` and `radiant_fair` are already radiant-oriented; I use `radiant_fair − market_p_radiant` and compare it with the radiant mid’s move at game second +300, using the first valid row at or after that second and within 10 seconds. Quartiles are equal-count bins sorted by predicted delta. Sequential rows are autocorrelated; the correlations describe association, not independent statistical evidence.

The main run was:

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
PYTHONPATH=src uv run python /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/live-luna/analyze_live_tapes.py > /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/live-luna/run_output.json
```

The code confirms the orientation: `match_worker.py:399-408` assigns YES or NO mid to Radiant according to `yes_is_radiant`, then normalizes the pair. In the current live sample, every fill token ID mapped to YES or NO; there were zero unrecognized fill tokens.

## Findings

### F1 — LoL lags Dota after normalizing by BUY notional

- **Stage:** live
- **Claim:** LoL is approximately flat in the recent window and materially negative over the full window; Dota is positive in both.
- **Evidence:** `work/live-luna/period_summary.json:2-345` and `:648-987`; the analysis command above writes these aggregates. `jq` over the two period objects printed:

  | Period | Game | Maps / with fills | Fills | BUY / SELL notional | Known-map net | BUY notional on known-net maps | Net / BUY | BUY quote notional median / P90 |
  |---|---|---:|---:|---:|---:|---:|---:|---:|
  | Since 2026-08-31 | LoL | 298 / 207 | 1,492 | $13,145.48 / $12,689.70 | −$439.05 (291 maps) | $12,688.14 | −3.460% | $10.00 / $30.00 |
  | Since 2026-08-31 | Dota | 281 / 189 | 1,457 | $32,848.91 / $33,553.65 | +$763.81 (267 maps) | $31,129.83 | +2.454% | $60.00 / $100.00 |
  | Since 2026-09-18 | LoL | 44 / 24 | 109 | $686.02 / $686.70 | −$0.99 (43 maps) | $681.03 | −0.145% | $5.00 / $20.00 |
  | Since 2026-09-18 | Dota | 97 / 63 | 540 | $10,161.19 / $10,557.74 | +$324.70 (90 maps) | $9,418.62 | +3.447% | $60.00 / $60.00 |

- **Mechanism:** Live results diverge even after normalizing for trade size. The full shared summary is rebate-inclusive: its helper prints LoL +$2.2667 and Dota +$89.3808 GRID / +$290.9279 in the bucket it calls Steam. My table uses terminal cash plus inventory, except for one shared-summary fallback per recent Dota set; it is therefore lower by rebate and has a slightly different known-net sample. The broad Dota average is also confounded by a source mix that is mostly Oddin in local tape metadata (F3). LoL remains below Dota GRID in the recent source-matched comparison.
- **Severity:** high. **Confidence:** verified observation; attribution to game/model versus feed remains open.
- **Next check:** Compare post-2026-09-21 LoL and Dota maps by model version, actual `feed_source`, league, and BUY notional; include rebates separately so it reconciles to the live summary.

### F2 — Live LoL BUYs lose value at 30 seconds, opposite the backtest markout

- **Stage:** live / backtest comparison
- **Claim:** The observed LoL BUY markout is adverse over the first minute; the Dota live markout is favorable. This reverses the backtest ordering supplied in `00-context.md:70-73`.
- **Evidence:** `period_summary.json:936-984` (LoL) and `:732-780` (Dota). Next-signal live markouts printed for since 2026-09-18:

  | Game | BUY +10s | BUY +30s | BUY +60s | BUY +300s | SELL +30s |
  |---|---:|---:|---:|---:|---:|
  | LoL | −1.56¢ (n=53) | **−0.86¢ (n=56)** | −0.37¢ (n=55) | +2.11¢ (n=60) | −1.73¢ (n=38) |
  | Dota | +0.32¢ (n=298) | **+0.85¢ (n=278)** | +1.22¢ (n=288) | +3.68¢ (n=287) | −1.16¢ (n=196) |

  The shared backtest reports LoL BUY +30s +0.51¢ and Dota −0.38¢. The tape metrics above use execution price to future mid, as in the stated markout comparison.
- **Mechanism:** The LoL playback queue model appears to fill buys before favorable price movement more often than live does, or it does not reproduce live LoL fill selection/timing. Dota source mix and the wall-time proxy can also affect the comparison. This is evidence of a live/backtest gap, not proof of which fill-model parameter causes it.
- **Severity:** high. **Confidence:** verified markout values; likely backtest optimism for LoL.
- **Next check:** Re-run the markout comparison with exact signal arrival timestamps if available and stratify by LoL league, model version, and fill token. Then inspect whether the playback queue model reproduces the live fill-conditioned 30-second distribution.

### F3 — Shared Dota “Steam” bucket misclassifies actual Oddin feed tapes

- **Stage:** live data attribution
- **Claim:** The shared helper does not classify by `match.json.feed_source`: `work/shared/agg.py:10-11` labels IDs starting `grid-` as GRID, IDs starting `oddin` as Oddin, and every other ID as Steam. `MatchMeta.feed_source` is an explicit `steam|grid|oddin` field (`src/trader/archive_types.py:71-98`).
- **Evidence:** The helper printed since 9/18 `grid: 47 maps / 24 filled / +$89.3808` and `steam: 51 / 40 / +$290.9279`. A `jq` cross-tab of local tapes by `match.json.feed_source` and archive-ID prefix printed `grid/grid-id=46 maps,23 filled,189 fills`; `oddin/grid-id=1,1,15`; `oddin/numeric-id=50,39,336`. There are no local Dota maps with `feed_source=steam`. Thus the shared `grid` bucket is 46 actual GRID maps plus one Oddin map with a `grid-*` ID; its non-grid bucket contains 50 known Oddin maps plus absent `9012316577` (whose source remains unknown). For example, `data/trader/9012123019/match.json:1` says `feed_source=oddin`, `steam_delay_s=900`, and an Oddin match ID; `data/trader/grid-3008657-m1/match.json:1` also says `feed_source=oddin`. The source-specific local results are `period_summary.json:812-840`: Dota/Oddin +$249.72 on $6,310.86 known-map BUY notional (+3.957%, BUY +30s +1.21¢); Dota/GRID +$74.99 on $3,107.75 (+2.413%, BUY +30s −0.46¢).
- **Mechanism:** Archive ID prefix can differ from the selected feed. Consequently the supplied `Dota Steam` result is not reliable evidence of a Steam-feed result; the positive Dota comparison is at least partly an Oddin-source result. The local source split shows LoL GRID still trailing Dota GRID, so source attribution alone does not explain the LoL/Dota GRID gap.
- **Severity:** high for source-level conclusions. **Confidence:** verified misclassification for local tapes; one missing Dota map prevents validating the whole shared count.
- **Next check:** Rebuild the source split from `match.json.feed_source`; keep archive ID type as a separate field. Reconcile the one absent archive before treating the 47/51 breakdown as a full source comparison.

### F4 — LoL live model deltas have no useful positive relationship with 300-second moves in the recent sample

- **Stage:** live model signal
- **Claim:** The LoL prediction/realization correlation is negative in the recent window, while Dota’s is near zero. Full-window correlations are also close to zero.
- **Evidence:** The calibration implementation is `work/live-luna/analyze_live_tapes.py:372-423`; results are in `period_summary.json:863-915` (recent LoL), `:656-697` (recent Dota), `:220-255` (full-window LoL), and `:10-45` (full-window Dota). Recent LoL: 12,830 paired model rows, mean prediction +0.34 percentage points, mean realized move +1.17pp, Pearson r=−0.121, direction agreement 52.8%. Recent Dota: 200,476 pairs, +0.27pp predicted, −0.51pp realized, r=+0.041, 58.5% direction agreement. Full-period r is +0.023 for LoL (80,390 pairs) and +0.048 for Dota (254,759 pairs).

  Recent equal-count quartiles, mean predicted delta → mean realized 300-second move:

  | Game | Q1 | Q2 | Q3 | Q4 |
  |---|---:|---:|---:|---:|
  | LoL | −2.43pp → +4.10pp | −0.51pp → +2.71pp | +1.04pp → −0.67pp | +3.25pp → −1.44pp |
  | Dota | −2.29pp → −1.34pp | −1.00pp → −0.75pp | +1.22pp → +0.27pp | +3.15pp → −0.23pp |

- **Mechanism:** Live LoL model edge is not ranking its subsequent market moves in this tape sample. The inverse quartiles are consistent with miscalibration or timing/selection mismatch; the current tape cannot identify whether the model, state, or market feed is responsible. The sequential rows are not independent, so the correlation should be treated as a diagnostic, not a significance test.
- **Severity:** high. **Confidence:** verified association under the stated game-second matching method; causal source is likely but not isolated.
- **Next check:** Repeat with true signal timestamps, then split by current production model (`20260921T095813Z`), tournament, and fill/non-fill decisions. Compare to the exact as-of and +300s wall-clock label used by training.

### F5 — Update cadence differs mainly between Oddin and GRID, not between the two GRID samples

- **Stage:** live feed
- **Claim:** LoL GRID signals have 3s median / 12s P90 gaps between distinct seconds. Dota GRID is 4s / 14s; Dota Oddin is 1s / 1s. First `reason=model` second is median/P90 79/90 for LoL and −58.5/71 for Dota. Stale, paused, and missing-book rows are low shares of all signals.
- **Evidence:** `period_summary.json:812-854` (source-specific cadence), `:784-810` (Dota reasons), `:988-1011` (LoL reasons), `:699-710` / `:906-917` (entry blocks), and `:722-723` / `:926-927` (first model seconds). Recent shares (stale / paused / missing_book) are LoL 0.173% / 0.141% / 0.084%; Dota 0.040% / 0.053% / 0.165%. Recent reason counts: LoL `model=18,657, finished=44, missing_prior=44, one_sided_book=289, missing_book=16, stale=33, paused=27, crossed_book=2`; Dota `model=232,872, pre_horn=33,167, finished=90, missing_prior=37, one_sided_book=3,244, missing_book=447, stale=107, paused=143, crossed_book=5, pair_out_of_tolerance=1`. Full entry-block histograms on model rows: LoL `{cutoff:11521, no_edge:4245, min_delta:1921, none:564, nw_velocity:196, missing_nw:128, min_price:42, max_price:40}`; Dota `{cutoff:152038, no_edge:49884, min_delta:16802, none:8578, nw_velocity:3595, missing_nw:786, min_price:550, max_price:519, wide_spread:105, recovery:14, winding_down:1}`. The corresponding per-map and full-window histograms remain in `per_map.json` / `period_summary.json`.
- **Mechanism:** LoL has much sparser updates than Dota Oddin, but its cadence is close to Dota GRID; stale/missing-book shares do not point to a broad dead-feed problem. The source mix matters more than the broad game-level cadence average.
- **Severity:** medium. **Confidence:** verified counts; contribution to PnL likely but unproven.
- **Next check:** Compare each GRID map’s quote/fill decision against its preceding observed signal gap and source lag. Persist or recover signal wall times before making tighter markout claims.

### F6 — LoL fills later and at smaller observed order notionals

- **Stage:** live quoting / execution
- **Claim:** In the recent sample, median first BUY is second 241 for LoL vs 155 for Dota; the median of each map’s size-weighted BUY-to-SELL duration is 147s vs 130s. Pooled placed BUY quote notionals have median $5 for LoL, $60 for Dota GRID, and $20 for Dota Oddin.
- **Evidence:** `per_map.csv` includes every map’s fill count, BUY/SELL notional, net source, yes/no token, first entry second, inferred quote clip, hold time, and signal gap. `period_summary.json:861-862` reports LoL quote notional median/P90 $5/$20; `:813-853` reports source-separated Dota/LoL clip medians, PnL, and cadence; `:924-929` and `:720-725` report first-entry and hold summaries; `jq -r '."since_2026-09-18" | ["lol","dota"][] as $g | .[$g] as $x | [$g,$x.first_entry_second_median,$x.map_hold_duration_median_s,$x.buy_quote_order_notional_median] | @tsv' work/live-luna/period_summary.json` printed `lol 241 147.397 4.999` and `dota 155 129.733 59.994`. Recent LoL fills span 13 YES-only, 10 NO-only, and 1 both-token map; Dota 32 YES-only, 22 NO-only, and 9 both-token maps; unknown token fills are zero.
- **Mechanism:** LoL has materially less BUY exposure per map and its first buys arrive later, shrinking the sample and changing which market moves the strategy experiences. This fits the clip history, but the tapes do not isolate whether low clip or late entry causes the PnL gap.
- **Severity:** medium. **Confidence:** verified observations; impact likely.
- **Next check:** Restrict to production-model maps from 2026-09-21 onward, then compare identical per-map BUY-notional buckets and entry-second bands.

### F7 — One Dota live tape is missing locally, and one filled LoL map has unknown terminal net

- **Stage:** live archive coverage
- **Claim:** The shared summary has 98 Dota maps / 64 with fills since 9/18; the local tape has 97 / 63. The missing shared row is `9012316577`, shown as live with 22 fills and no net. `data/trader/9012316577/` does not exist locally. Separately, LoL `grid-3002603-m2` has two fills but no finite terminal net in the local tape or shared summary, so it is excluded from LoL net/BUY.
- **Evidence:** `work/shared/dota_summary.txt:443` prints `9012316577 ... fills=22 ... net=n/a ... joined=2026-09-23T11:05:12Z`; `test -d data/trader/9012316577` failed. `work/shared/lol_summary.txt:284` prints `grid-3002603-m2 ... fills=2 ... net=n/a`. Local coverage/net counts are `period_summary.json:726-728` and `:930-932`.
- **Mechanism:** The missing Dota tape prevents checking its token side, clip, markout, feed source, and net exposure. The unknown LoL map contributes $5.00 of BUY notional not represented in the known-net denominator.
- **Severity:** medium. **Confidence:** verified.
- **Next check:** Obtain the missing Dota archive and terminal reconciliation for the LoL map before closing the recent per-map PnL ledger.

## LoL tournament mix and results

Groups below use the exact `match.json.tournament` value, so stages and EMEA Masters groups remain separate. Net and net/BUY use only maps with known terminal net; the full per-map source is `work/live-luna/league_lol_since_2026-08-31.csv` and the recent-window source is `work/live-luna/league_lol_since_2026-09-18.csv`. `period_summary.json:375-646` has the full-window rows and `:1014-1083` has the recent rows; the reducer groups on the exact tournament field at `analyze_live_tapes.py:493-511`.

Since 2026-09-18:

| Tournament | Maps / with fills | Fills | Known-map BUY | Net | Net / BUY |
|---|---:|---:|---:|---:|---:|
| EMEA Masters - Summer 2026 (Play-Ins: Group A) | 11 / 8 | 35 | $386.16 | −$10.14 | −2.63% |
| LEC - Summer 2026 (Playoffs: Playoffs) | 10 / 7 | 40 | $119.93 | +$4.43 | +3.70% |
| LCS - Split 3 2026 (Playoffs: Playoffs) | 9 / 7 | 25 | $74.95 | +$7.28 | +9.71% |
| EMEA Masters - Summer 2026 (Play-Ins: Group B) | 4 / 0 | 0 | $0.00 | $0.00 | — |
| EMEA Masters - Summer 2026 (Play-Ins: Group C) | 4 / 1 | 4 | $39.99 | −$0.48 | −1.20% |
| Hitpoint Masters - Summer 2026 (Playoffs: Playoffs) | 3 / 0 | 0 | $0.00 | $0.00 | — |
| EMEA Masters - Summer 2026 (Play-Ins: Group D) | 3 / 1 | 5 | $59.99 | −$2.07 | −3.45% |

Since 2026-08-31:

| Tournament | Maps / with fills | Fills | Known-map BUY | Net | Net / BUY |
|---|---:|---:|---:|---:|---:|
| LCK - Split 3 2026 (Regional Championship: Regional Championship) | 32 / 30 | 221 | $1,836.04 | +$21.40 | +1.17% |
| LEC - Summer 2026 (Playoffs: Playoffs) | 31 / 27 | 223 | $1,807.82 | −$34.46 | −1.91% |
| Road of Legends - Summer 2026 (Playoffs: Playoffs) | 23 / 15 | 124 | $958.95 | −$74.28 | −7.75% |
| Hitpoint Masters - Summer 2026 (Playoffs: Playoffs) | 23 / 10 | 88 | $734.66 | −$44.13 | −6.01% |
| NACL - Summer 2026 (Playoffs: Playoffs) | 22 / 17 | 155 | $1,006.69 | −$25.54 | −2.54% |
| Liga Portuguesa - Summer 2026 (Playoffs: Playoffs) | 19 / 14 | 88 | $458.78 | −$35.47 | −7.73% |
| Hitpoint Masters - Summer 2026 (Regular Season: Regular Season) | 19 / 6 | 24 | $216.24 | +$8.66 | +4.01% |
| LCS - Split 3 2026 (Playoffs: Playoffs) | 15 / 13 | 98 | $856.75 | −$3.20 | −0.37% |
| CBLOL - Split 2 2026 (Playoffs: Playoffs) | 12 / 10 | 73 | $773.07 | −$5.79 | −0.75% |
| EMEA Masters - Summer 2026 (Play-Ins: Group A) | 11 / 8 | 35 | $386.16 | −$10.14 | −2.63% |
| NLC - Summer 2026 (Playoffs: Playoffs) | 9 / 5 | 25 | $125.11 | −$11.61 | −9.28% |
| Esports Balkan League - Summer 2026 (Regional Finals: Regional Finals) | 9 / 5 | 55 | $154.81 | −$2.52 | −1.62% |
| LJL - Summer 2026 (Regional Championship: Regional Championship) | 8 / 4 | 16 | $64.56 | −$3.00 | −4.65% |
| La Ligue Française - Summer 2026 (Playoffs: Playoffs) | 8 / 7 | 80 | $1,156.47 | −$120.65 | −10.43% |
| Rift Legends - Summer 2026 (Round 2: Round 2) | 7 / 7 | 24 | $298.49 | −$59.41 | −19.90% |
| LCS - Split 3 2026 (Regular Season: Regular Season) | 6 / 4 | 31 | $130.32 | −$1.30 | −1.00% |
| LRN - Split 2 2026 (Playoffs: Playoffs) | 6 / 3 | 17 | $118.56 | −$15.75 | −13.29% |
| Circuito Desafiante - Split 2 2026 (Playoffs: Playoffs) | 6 / 6 | 16 | $120.82 | −$2.62 | −2.17% |
| LoL Italian Tournament - Summer 2026 (Playoffs: Playoffs) | 6 / 3 | 11 | $53.75 | −$3.15 | −5.86% |
| LES - Summer 2026 (Regional Finals: Regional Finals) | 5 / 4 | 36 | $425.57 | +$10.57 | +2.48% |
| EMEA Masters - Summer 2026 (Play-Ins: Group B) | 4 / 0 | 0 | $0.00 | $0.00 | — |
| EMEA Masters - Summer 2026 (Play-Ins: Group C) | 4 / 1 | 4 | $39.99 | −$0.48 | −1.20% |
| Prime League - Summer 2026 (Playoffs: Playoffs) | 3 / 3 | 23 | $196.77 | −$17.37 | −8.83% |
| Hellenic Legends League - Summer 2026 (Playoffs: Playoffs) | 3 / 2 | 13 | $601.50 | −$41.58 | −6.91% |
| EMEA Masters - Summer 2026 (Play-Ins: Group D) | 3 / 1 | 5 | $59.99 | −$2.07 | −3.45% |
| LRS - Split 2 2026 (Playoffs: Playoffs) | 2 / 1 | 2 | $6.30 | +$1.70 | +26.98% |
| TCL - Summer 2026 (Playoffs: Playoffs) | 2 / 1 | 5 | $99.97 | +$33.14 | +33.15% |

## Checked and OK

- **Outcome orientation and token mapping:** `src/trader/match_worker.py:399-408` normalizes the radiant mid using `yes_is_radiant`; the analysis maps each fill to the stored YES/NO token IDs. All fills in the analyzed LoL and Dota maps mapped to a token outcome; unknown count is zero. `match.json.market.yes_is_radiant` varies across maps, so using fill token IDs is necessary.
- **Negative Dota first-model seconds are permitted:** `src/trader/session_quoting.py:94-108` allows model decisions before horn from `MODEL_START_SECOND` up to second −1. The recent Dota first-model median of −58.5s therefore describes the model warm-up window, not a negative-time fill. The first BUY median is +155s.
- **LoL live coverage matches the shared map/fill count:** the local live tape has 44 LoL maps and 24 with fills since 9/18, matching `00-context.md:52-56`. Dota differs by one missing local tape as described in F7.
- **Feed failure labels are not frequent enough to explain the full gap by themselves:** latest stale, paused, and missing-book shares are each below 0.18% for both games. The update gaps are larger, but LoL GRID is close to Dota GRID and well behind Dota Oddin.

## Open questions for the owner

1. Should source reporting treat `match.json.feed_source` as authoritative and keep archive ID prefix only as a separate archive/linking field? The current shared split does not.
2. Is the intended live-vs-model comparison measured in wall-clock seconds or game seconds across pauses? The session signal journal lacks a wall timestamp, while fill records have one.
3. Should the PnL comparison include maker rebate? The requested `session_end` method omits it, while the shared summary includes it; the normalized result is more useful when both definitions are reported separately.
4. Can the recent analysis be rerun on only maps using the current production model after 2026-09-21? The requested windows include older production models and clip settings.

## Needs from VPS

- Sync `data/trader/9012316577/match.json` and `data/trader/9012316577/session.jsonl` to the local archive. The shared summary row at `work/shared/dota_summary.txt:443` shows a live Dota map joined 2026-09-23 11:05 UTC, 22 fills, and net `n/a`; the local directory is absent. Include any terminal cash/inventory reconciliation if it later becomes available.
- For LoL `grid-3002603-m2`, provide terminal cash/inventory reconciliation if the fill-ledger or wallet summary has one; local `session_end` and the copied shared summary both have net `n/a` despite two fills.

## Scripts and outputs

All created files are under `work/live-luna/`:

- `analyze_live_tapes.py` — read-only tape reduction and markout/calibration calculations.
- `period_summary.json`, `run_output.json` — aggregates for both date windows.
- `per_map.csv`, `per_map.json` — per-map fills, notionals, terminal net, clip estimates, YES/NO token, entry second, hold estimate, source, and signal health.
- `fill_markouts.csv` — fill-level +10/+30/+60/+300s markouts with target matching distance.
- `league_lol_since_2026-08-31.csv`, `league_lol_since_2026-09-18.csv` — per-tournament LoL results using the exact `match.json.tournament` value.
- `inspect_sample.py` — one-map schema inspection used during setup.

The aggregate run used the command in Method and scope. The shared source helper was reproduced with `uv run python work/shared/agg.py work/shared`; it returned Dota `grid=[47,24,+89.3808]` and `steam=[51,40,+290.9279]`, which prompted the `match.json.feed_source` validation in F3.
