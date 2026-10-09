# gap-haiku — backtest vs live gap, bad luck vs structure
Status: FINAL

## Answer
- Bad luck does not explain today. The 10-map sum of −42.08 (with estimated rebate) falls at or below the backtest value in 0.09% of draws (6 h3 maps + 4 h6 maps, n30 queue, 100,000 bootstrap draws). Against the Polymarket figure (−58.81, no rebate) the share is 0.03%. Verified: `02_luck_test.py`.
- Most of the gap sits in the h3 cell (6 maps). Live h3 settled PnL is −9.62 per map. The backtest (queue) is +4.37 per map. A 6-map bootstrap gives P = 0.05%. Verified: `09_significance.py`.
- The h3 gap is two parts. Pair edge is −9.5 per map (live −1.70 vs backtest +7.78). Directional residual is −4.5 per map. Per share, pair edge is 92% of the h3 gap: −0.135 vs +1.187 cents per share, against a total gap of −1.43 cents per share. Verified: `11_per_share.py`.
- Pair cost is the mechanism. Live h3 pair cost is 0.997 (median 0.989). Backtest is 0.971 (median 0.972). The live YES and NO fills land at different mids (journal, quantity-weighted: YES mid 0.543, NO mid 0.479; sum 1.02). Verified: `05_compare.py`, `07_journal_markout.py`.
- The h6 gap (4 maps, −4.4 per map) is all directional. Pair edge matches (live +5.22 vs backtest +5.31 per map). Residual differs by −4.3 per map. Net exposure on the winner is −10.1 cents per share live vs −2.6 backtest. P = 0.15 with 4 maps, so not significant. Verified: `11_per_share.py`.
- The 30-second markout is the sharpest gap. Live h3 fills lose 1.1 cents per share in 30 s (raw journal book, quantity-weighted, 477 fills). Backtest h3 fills gain +0.9 cents (17,492 fills, `fills.parquet` `markout_30s`). Live h6 is +2.8 vs backtest +2.5. Verified: `08_markout_summary.py`.
- Our own resting orders do not cause the gap. Raw and stripped journal mids differ by 0.01 cents at 30 s. Verified: `08_markout_summary.py`.
- Fill count is not the main gap. Live h3 fills 80 per map. Backtest queue 57, no-queue 80. The no-queue backtest matches the count but earns +10.7 per map, against −9.6 live. The gap is in which fills arrive and where, not how many. Verified: `01_bt_distribution.py`, `05_compare.py`.
- Speed is not the gap. Live insert latency is 78 ms median (p90 140 ms). Backtest assumes 175 ms, so the backtest is the slower one. Live cancel latency cannot be measured from the logs. Verified: `10_insert_latency.py`.
- Market mix is not the gap. Of 307 backtest maps, 66 carry a BLAST Slam title. Their h3 mean is +5.28 per map vs +5.42 for the rest. No backtest map carries a PARI label. Verified: `06_league.py`.

## Method
- Data (all read-only):
  - Backtest: four archive folders under `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-{n30,n30-nq,h6-n30,h6-n30-nq}/_archive/` (`results.parquet`, `fills.parquet`, `summary.json`, `manifest.json`).
  - Live: `work/data/trader_live_b/<match>/` (`session.jsonl`, `match.json`), `wallet/engine_journal/live.jsonl`, `work/data/book_journal_20261008_liveb.jsonl.gz`.
  - Dataset for backtest winners: `esports-trader/data/new_processed/dataset/validation_dataset.parquet` (`radiant_win`).
  - Tournament titles: `esports-trader/data/new_processed/universe/universe.parquet` (`event_title`).
- Per-map backtest PnL = `engine_pnl` + sum of `fills.maker_rebate`. This equals `summary.json` `net_pnl` for all four variants (1655.18; 3715.77; 1370.77; 2049.12). Verified: `01_bt_distribution.py`.
- Backtest winner token: `radiant_win` from the dataset. The radiant token index comes from `results.terminal_side` where set (256 of 307 maps on h3 queue). Otherwise it is inferred from fill prices vs fair. The inference agrees with terminal sides on 100% (h3) and 99.5% (h6) of maps. Verified: `04_bt_maps.py`.
- Live settled PnL per map = shares bought × outcome − cost (no merge data needed; a merged pair and a held pair pay the same). This matches `session_end.net_cash` where no tail is left. The largest difference is 0.78 USDC on grid-3011820-m1. It equals the 0.784 unpaired YES shares that pay out there, because `net_cash` excludes the tail. Verified: `03_live_maps.py`.
- Live winners: `match.json` for 6 grid maps. Four Oddin maps have `final.winner = null`. For those, I used the last Polymarket YES mid (0.006 to 0.035, or 0.985). The rule (last mid > 0.5) matches `match.json` on 6 of 6 grid maps. Verified: `12_checks.py`.
- Journal rebuild: 3,747,720 lines streamed once. Level book rebuilt for the 10 markets. Our top-of-book matches the event's own `bestBid`/`bestAsk` in 7,267,550 of 7,360,714 checks (98.7%). Own orders come from `user_order` events (PLACEMENT/UPDATE/CANCELLATION). Markouts at t+30 s and t+300 s use the raw mid and the own-stripped mid. Verified: `07_journal_markout.py`.
- Signal-based markouts use `session.jsonl` mids (about 7 s apart), as-of lookup. These are the brief's required method. They run about 0.3 cents less negative than the journal numbers for h3.
- Bootstrap: 100,000 draws. Draws use 6 maps from the h3 pool and 4 from the h6 pool, with replacement. Pooled per-share ratios use sum(metric) / sum(shares). Verified: `02_luck_test.py`, `09_significance.py`, `11_per_share.py`.
- Assumptions in the test: maps are independent draws from the backtest spread. The 10 live maps are one day. The backtest is an unbiased reference for live maps. The last assumption is the one this report tests.
- Scripts are in `work/gap-haiku/` (`01` to `12`). Output tables are parquet files in the same folder.

## Results

### Table 1. Backtest per-map distribution (307 maps each)
| variant | mean incl. rebate | median | sd | p5 | p25 | share < 0 | mean engine only | fills/map | shares/map | leftover shares/map |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| h3 n30 queue | 5.39 | 3.88 | 11.15 | −7.58 | −1.89 | 31.9% | 4.37 | 57.0 | 655 | 23.0 |
| h3 n30 no-queue | 12.10 | 7.71 | 18.31 | −6.42 | −0.06 | 25.1% | 10.71 | 80.0 | 893 | 25.2 |
| h6 n30 queue | 4.47 | 2.55 | 8.66 | −7.40 | 0.00 | 23.1% | 4.21 | 14.2 | 162 | 18.3 |
| h6 n30 no-queue | 6.67 | 4.63 | 11.10 | −6.92 | 0.00 | 23.5% | 6.33 | 19.6 | 218 | 19.1 |

Note: `00-context.md` labels "+4.37 mean, median +3.37, p5 −7.82, 35% negative" as "engine + rebate". Those are engine-only numbers. With rebate they are +5.39, +3.88, −7.58, 32%. Verified: `01_bt_distribution.py`.

### Table 2. Luck test (6 h3 + 4 h6 backtest draws, 100,000 bootstrap)
| backtest pool | live comparator | backtest E[sum] | sd | P(draw ≤ live) | maps below p5 (live vs P(count ≥ live)) |
|---|---|---:|---:|---:|---|
| queue, incl. rebate | −42.08 (telegram) | +49.9 | 32.1 | 0.09% | 2 vs 9.6% |
| queue, engine only | −58.81 (Polymarket) | +43.0 | 30.8 | 0.03% | — |
| no-queue, incl. rebate | −42.08 (telegram) | +99.1 | 49.8 | 0.01% | 3 vs 1.3% |

Per-map live settled PnL (no rebate): grid-3011820-m1 +4.89, m2 −18.77, m3 −7.07, 9034789047 +2.58, grid-3011821-m1 −34.95, 9034957701 −4.41, grid-3011821-m2 −1.99, 9035220432 +3.66, grid-3011822-m1 +0.91, 9035318247 −3.47. Sum −58.62, against the Polymarket −58.81. The Polymarket day figure is settled cash, not marks.

### Table 3. Live vs backtest per map (cell means)
| metric | live h3 (6) | bt h3 queue | bt h3 no-queue | live h6 (4) | bt h6 queue | bt h6 no-queue |
|---|---:|---:|---:|---:|---:|---:|
| fills/map | 80.3 | 57.0 | 80.0 | 10.3 | 14.2 | 19.6 |
| shares bought/map | 1,261 | 655 | 893 | 171 | 162 | 218 |
| mean fill size (shares) | 16.0 | 12.2 | 11.7 | 17.0 | 12.4 | 12.0 |
| pair cost (mean, two-sided maps) | 0.997 | 0.971 | 0.966 | 0.917 | 0.923 | 0.916 |
| pairs/map (min of YES, NO) | 622 | 319 | 439 | 77 | 74 | 102 |
| pair edge/map (pairs × (1 − pair cost)) | −1.70 | +7.78 | +13.91 | +5.22 | +5.31 | +7.63 |
| directional residual/map | −7.93 | −3.41 | −3.25 | −5.45 | −1.10 | −1.30 |
| PnL/map (no rebate) | −9.62 | +4.37 | +10.71 | −0.22 | +4.21 | +6.33 |
| unpaired tail at end (shares/map) | 17.0 | 23.0 | 25.2 | 17.2 | 18.3 | 19.1 |
| losing-side share of shares | 0.515 | 0.530 | 0.527 | 0.554 | 0.526 | 0.537 |
| 30 s markout, cents/share | −1.14 (journal) | +0.88 | +1.32 (per-map mean) | +2.78 (journal) | +2.46 | +3.21 (per-map mean) |

Pair edge and residual sum to the PnL per map (exact decomposition: PnL = min(Y,N) × (1 − pY − pN) + excess shares × (settlement − average cost)). Backtest per-map means use all 307 maps (one-sided and empty maps count as 0). Verified: `05_compare.py`, `09_significance.py`, `11_per_share.py`.

Live h3 fill-time mids (journal, t0, quantity-weighted): YES mid 0.543 vs fill price 0.532 (edge +1.05 cents); NO mid 0.479 vs 0.470 (+0.93 cents). Live h6: YES +3.96 cents (19 fills), NO +3.06 cents (22 fills). Quote design is 3 ticks (h3) and 6 ticks (h6). Backtest fill-time mids are not stored, so the same figure cannot be computed for the backtest. Verified: `07_journal_markout.py`, `08_markout_summary.py`.

Live h3 per-map 30 s markout (journal, cents/share): grid-3011820-m1 +0.1, m2 −1.1, m3 −0.6, 9034789047 −0.1, grid-3011821-m1 −2.0, 9034957701 −2.6. Five of six are negative. Leave-one-out pooled h3 markout stays between −0.6 and −1.5 cents, so one map does not drive it. Verified: `12_checks.py`.

### Table 4. Pooled per-share economics (cents per share; live vs backtest; P = bootstrap share of backtest draws ≤ live)
| cell | backtest | metric | live | backtest | P |
|---|---|---|---:|---:|---:|
| h3 | queue | PnL / share | −0.76 | +0.67 | 0.03 |
| h3 | queue | pair edge / share | −0.14 | +1.19 | 0.02 |
| h3 | queue | residual / share | −0.63 | −0.52 | 0.41 |
| h3 | queue | net on winner / share | −1.33 | −1.60 | 0.61 |
| h3 | no-queue | PnL / share | −0.76 | +1.20 | 0.005 |
| h6 | queue | PnL / share | −0.13 | +2.60 | 0.15 |
| h6 | queue | pair edge / share | +3.06 | +3.28 | 0.41 |
| h6 | queue | residual / share | −3.19 | −0.68 | 0.14 |
| h6 | queue | net on winner / share | −10.09 | −2.57 | 0.15 |

Net on winner per share is not worse on h3. It is worse on h6, with 4 maps. Verified: `11_per_share.py`.

### Table 5. Markouts (quantity-weighted, cents per share)
| horizon | live h3 journal | live h3 signal | bt h3 queue | live h6 journal | bt h6 queue |
|---|---:|---:|---:|---:|---:|
| 30 s | −1.14 (477 fills) | −0.83 | +0.88 (17,492) | +2.78 (41) | +2.46 (4,348) |
| 300 s | −0.71 | −1.21 | +0.67 | +5.5 (37) | +2.60 |

The journal and signal numbers differ by up to 0.4 cents. Signal mids are coarser and as-of. In 15.5% of live fills (quantity-weighted 15%), our fill price equals the raw best bid just before the fill. Verified: `08_markout_summary.py`.

### Table 6. Backtest assumptions vs live (code and data checks)
| # | assumption | backtest | live | effect on backtest PnL | evidence |
|---|---|---|---|---|---|
| 1 | Fill rule | Nautilus `queue_position=True`; fills need traded volume at our price after the queue ahead clears | Fills come from real sells at our price; queue unknown | Count conservative (57 vs 80 fills). Selection optimistic (no-queue +10.7 vs live −9.6). | `docs/execution-modeling.md:108-121`; `run.py` flag `--no-queue-position` |
| 2 | Queue ahead | Volume at price at submit (`volume_at_price`), stored in context; Nautilus snapshots at accept | Not logged | Unknown | `two_sided_strategy.py:446`; docs `:113-117` |
| 3 | Fill size | Partial fills by trade volume; mean 12 shares | Mean 16; 55% of fills are full 20-share orders | Shares conservative (655 vs 1,261 per h3 map) | `05_compare.py` |
| 4 | Insert latency | 175 ms fixed | Median 78 ms, p90 140 ms, p99 715 ms (n = 10,405) | Conservative | `src/shared/constants/strategy.py:19`; `10_insert_latency.py` |
| 5 | Cancel latency | 60 ms, applied at cancel release | Not logged (no decision time for cancels) | Unknown | `strategy.py:20`; `two_sided_strategy.py:626` |
| 6 | Signal age gate | None. Only a missing-sample check; `_signal` returns the latest sample at any age | Gated on `entry_stale_s` | Optimistic in principle. Live stale rows: 15 of 30,332 (0.05%), so immaterial today | `two_sided_strategy.py:400, 535-541`; `two_sided_quoting.py:48-50`; row counts from `work/data/trader_live_b/*/session.jsonl` |
| 7 | Book staleness | 5 s | 5 s | None | `telonex_book.py:18`; `two_sided_strategy.py:551` |
| 8 | Quote cadence | debounce 100 ms, fallback 2.0 s | Same file read (100 ms, 2.0 s) | None | `config/trading.toml:7-8`; `two_sided_worker.py:104-111` |
| 9 | Own orders in the book | Archive book. Own-order strip only for maps with a live archive | Live fair is book mid including own orders | Not material. Raw vs stripped markout differs by 0.01 cents | `strip_own_book.py:1-10`; `telonex_local.py:180`; `07_journal_markout.py` |
| 10 | Trade side | Telonex `onchain_fills` with aggressor fix | Live fills from user channel | Likely fine. Not verified against live fills | `onchain_side.py:1-12`; `docs:127-129` |
| 11 | Fill-time fair | Quote-time fair stored in `fills.fair` (not fill-time) | — | Cannot test fill-time edge | `two_sided_strategy.py:239` |
| 12 | Fill-time mid | Not stored. Only 30 s and 300 s references | — | Cannot test pair-cost timing | `postprocess.py:142-155, 183-216` |
| 13 | Rebate | 0.0075 × qty × p(1−p) (0.15 × 5% and 25% × 3% give the same) | Telegram estimate uses the same formula; sums to +13.09 over 10 maps | None | `postprocess.py:190-193`; `shared/utils/trading.py:58-63`; `data/backtests/market_terms.json` (v3 0.15/0.05, v2 0.25/0.03) |
| 14 | Merges | Cash credit at 20 shares (`merge_min_shares`) | Live merges at $130 and 5 pairs (`00-context.md:24`, not re-verified) | None (value-neutral) | `run.py` `_two_sided_settings` |
| 15 | Settlement | `radiant_win` | `match.json` (6 maps); last mid (4 maps, rule checked 6/6) | None | `03_live_maps.py`; `12_checks.py` |

### Table 7. Tournament label (backtest, from universe titles)
| variant | group | maps | mean incl. rebate | median | share < 0 |
|---|---|---:|---:|---:|---:|
| h3 queue | BLAST Slam | 66 | 5.28 | 2.94 | 31.8% |
| h3 queue | other | 241 | 5.42 | 4.20 | 32.0% |
| h6 queue | BLAST Slam | 66 | 5.35 | 2.40 | 21.2% |
| h6 queue | other | 241 | 4.22 | 2.59 | 23.7% |

No backtest map has a PARI label. Live labels: five grid maps are "BLAST Slam Playoffs" (universe title). The four Oddin maps have no universe title. Verified: `06_league.py`.

## Recommendations
Ranked by expected dollars per map. Confidence in brackets.

1. Do not size up on the backtest's +4.4 per map. It overstates h3 by about 14 per map against live (9.6 lost vs 4.4 gained). Fix the fill selection first. Expected $: diagnostic. [High]
2. Pair leg control. The h3 pair edge gap is −9.5 per map. Closing a third of it is worth about +3 per map on h3. Full closure is +9.5. The gap comes from where YES and NO fills land in time, which points at legging. Pair with the legging and fast-take analysis. [Low to medium: 6 maps, cause not isolated.]
3. Directional exposure on h6. The residual gap is −4.3 per map on 4 maps (P = 0.14). Check whether the net cap (30 shares) binds on h6 maps before changing anything. Expected $: up to +4 per map on h6. [Low]
4. Replay the 10 live maps through the backtest with fill-time mids and quote ages logged. If the replay gives about −10 per map, the fill model is the cause. If it gives about +4, the gap is in execution (cancels, queue, timing). Needs `backtest.run`, which this brief does not allow. [High value, owner to run]
5. Log the fill-time mid, cancel decision time, and queue ahead in the live trader. Live cancel latency and fill-time edge are the two numbers missing for items 2 and 4. [High value]
6. Speed (Rust rewrite). This gap does not point to speed. Live insert is faster than the backtest assumes. Cancel is unmeasured. Expected $ from speed in this gap: not shown. [Medium]
7. Add an age check on the model sample in `two_sided_strategy.py:400`. Live stale rows are 0.05%, so the effect is about 0. [High]

## Refuted / open questions
- Refuted: the 10-map sum is bad luck. P = 0.09% (queue, incl. rebate). The count of maps below p5 is not unusual (2 of 10, P = 9.6%). The gap is in the mean, not the tail.
- Refuted: our own orders move the mid. Stripped and raw journal markouts match to 0.01 cents.
- Refuted: rebate is the gap. Both use 0.0075 × qty × p(1−p). Live rebate estimate is +13.09 across 10 maps.
- Refuted: speed is the gap. Insert latency is 78 ms median live vs 175 ms in the backtest.
- Refuted: league mix is the gap. BLAST-labelled maps are not worse than the rest (h3 5.28 vs 5.42).
- Refuted: "debounce 250 ms, fallback 1 s" for live (listed in the brief's context). The live path reads `config/trading.toml` (debounce 100 ms, quoter tick 2.0 s), the same as the backtest. Verified: `two_sided_worker.py:104-111`, `config/trading.toml:7-8`.
- Open: backtest fill-time mid and quote age are not stored. The fill-time edge (live 1.0 cents vs 3 designed) and the YES/NO timing asymmetry cannot be compared with the backtest.
- Open: live cancel latency. There is no cancel decision timestamp in the logs. The 60 ms assumption is untested.
- Open: telegram −42.08 vs settled −58.62 + estimated rebate 13.09 = −45.53. The 3.45 difference is unexplained (likely inventory marks in telegram). Not verified.
- Open: grid-3011822-m2 is not in the data (still running at download). The day figure may include it.
- Open: four Oddin maps have no universe title. PARI Universe does not appear in the backtest, so that comparison is impossible.
- Open: live fee type for these markets is not verified. Both fee types in `market_terms.json` give the same rebate per share.
- Open: archive book update frequency vs live WebSocket was not compared. Mid cadence in the markout could differ.
- Speculative: the h3 gap comes from fills that arrive while the mid moves toward our bid (adverse selection in the live queue). The journal data fits this (fill-time edge 1 cent, 30 s markout −1.1 cents), but the backtest cannot confirm it because it does not store fill-time mids.
