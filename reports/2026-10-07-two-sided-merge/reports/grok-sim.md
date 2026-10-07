# Experiment zero: two-sided quoter on 30 Dota map tapes

Status: FINAL

## Summary

- Sample is 30 map-winner markets, 2026-08-01 through 2026-10-03: 6 BLAST Slam main-stage, 6 EPL Masters playoffs, 3 European Pro League playoffs, 5 PGL Wallachia, 5 Games of the Future, 5 Winline. EWC's main event is not in this catalog window. The run cap is 30 maps; the brief asked for about 40.
- Median map has **$113k** on-chain taker notional and **1,274** fills (mean $152k / 1,901, including one empty fill file). **5/30** maps are under $5k. Four of those are Winline ($0.7k–$3.0k). The fifth, PlayTime vs Team Cobra on 2026-09-20, has books but a 0-row on-chain file.
- Flow is mostly taker buys of both tokens (median about $40k YES and $29k NO per map). Direct sells are ~$4–5k median per side. By phase, **40%** of taker notional is after minute 20, **22%** is pre-horn, **19%** each in 0–8 and 8–20.
- Touch spread, time-weighted, median across maps **2.6 ticks** (mean 4.9). The book is one-sided **2.2%** of the time and crossed essentially never. Snapshot-median size at the best bid is **~$28** (YES); the time-weighted mean is ~$260 because a few thick moments dominate.
- A 5-tick move inside 10 seconds hits **1.7%** of windows in minutes 0–8, **5.7%** in 8–20, and **8.5%** after minute 20 (median across maps). p90 absolute 10-second mid move is 3.0 / 3.8 / 4.7 cents in those phases.
- Top makers are two-sided. **90%** of maker notional has fills on both tokens; **48%** is buy-both with zero sells in this sample. `0x893575c7…` (3.1% of maker notional here) shows the merge pattern in its latest 2,000 activity rows: 1,859 BUY, 0 SELL, 120 MERGE, on Valorant/CS. Two other top Dota makers in that same recent window buy and redeem, and barely merge.
- Our wallet on this sample is `0x941aa5589961e33c54365a27a3223c916e6a24d9` (likely: price/size/time match; the `fill_key` hash is not the settlement tx). Rank 43 of 1,466 makers, **0.49%** of maker notional, and it does sell. Recent activity: 1,360 BUY / 629 SELL / 0 MERGE in 2,000 rows.
- Chosen quote: **3 ticks back, skew 0.0002 per share, inventory cap 100, size 20, no taker flatten.** Base queue: **+$343 on $12,465 bought (2.75¢ per $1), median map +$6.68, worst −$54, 7/30 maps negative, peak capital p50 $34 / p95 $73.** Same knobs, last-in-queue: **+$219 on $10,914 (2.01¢ per $1), worst still −$54.** No config keeps the worst map above −$50.
- Quoting 1 tick back is about flat (+$12, 0.03¢ per $1) and loses money in the last-in-queue model (−$87). The 3-tick offset is what survives adverse selection. Rebates are $39 of the $343; the rest is merge edge. Most dollars of PnL are after minute 20 because that is where the fills are; the edge per dollar is fatter before minute 20 (~4–5¢ vs ~1.8¢).

## Findings

### Sample

Selection is `work/grok-sim/select_maps.py`. One map per series (earliest map of that event title), spread across the bucket's date range, duration 15–70 minutes, both tokens have book and on-chain day files. YES is the radiant token (`radiant_token_index`). Winner names are STRATZ; they sometimes disagree with the Polymarket team strings (Level UP / Kalmychata, Yakult / Invictus). Settlement uses `radiant_win`, not the name.

| id | tier | bucket | teams (Polymarket) | day | min | winner (STRATZ) | on-chain $ | fills | WS $ |
|---:|---|---|---|---|---:|---|---:|---:|---:|
| 0 | big | blast | 1win vs Natus Vincere | 09-29 | 44 | Natus Vincere | 493,691 | 4,968 | — |
| 1 | big | blast | Team Liquid vs Yakult Brothers | 09-29 | 36 | Team Liquid | 173,007 | 1,152 | — |
| 2 | big | blast | Team Nemesis vs Natus Vincere | 09-30 | 32 | Natus Vincere | 219,707 | 1,139 | — |
| 3 | big | blast | MOUZ vs OG | 10-01 | 60 | OG | 309,022 | 6,841 | — |
| 4 | big | blast | GamerLegion vs Team Liquid | 10-02 | 41 | GamerLegion | 259,757 | 3,655 | — |
| 5 | big | blast | Team Spirit vs Team Yandex | 10-03 | 37 | Team Yandex | 413,348 | 3,863 | — |
| 6 | big | epl masters PO | Power Rangers vs Yellow Submarine | 08-09 | 41 | Yellow Submarine | 24,268 | 610 | 16,572 |
| 7 | big | epl masters PO | Ilbirs vs Zero Tenacity | 08-10 | 49 | Zero Tenacity | 48,289 | 1,213 | 48,252 |
| 8 | big | epl masters PO | Level UP vs Rune Eaters | 08-12 | 48 | Kalmychata | 60,885 | 1,341 | 60,725 |
| 9 | big | epl masters PO | MOUZ vs Team Synapse | 09-07 | 67 | Team Synapse | 80,531 | 2,060 | 78,858 |
| 10 | big | epl masters PO | MOUZ vs GamerLegion | 09-08 | 48 | GamerLegion | 82,123 | 2,058 | 81,402 |
| 11 | big | epl masters PO | Natus Vincere vs Klim Sani4 | 09-10 | 50 | Natus Vincere | 156,953 | 2,595 | 147,061 |
| 12 | big | epl PO | Nemiga vs Kalmychata | 09-21 | 43 | kalmychata | 12,690 | 538 | — |
| 13 | big | epl PO | Team Synapse vs Kalmychata | 09-22 | 37 | kalmychata | 18,330 | 631 | — |
| 14 | big | epl PO | Team Synapse vs Yellow Submarine | 09-25 | 36 | Yellow Submarine | 8,773 | 500 | — |
| 15 | tier2 | pgl | PlayTime vs Team Cobra | 09-20 | 65 | PlayTime | 0 | 0 | — |
| 16 | tier2 | pgl | Conventus Stellarum vs 1win | 09-21 | 37 | 1w | 242,006 | 2,796 | — |
| 17 | tier2 | pgl | Pipsqueak+4 vs Team Cobra | 09-22 | 36 | Pipsqueak + 4 | 143,639 | 1,045 | — |
| 18 | tier2 | pgl | Natus Vincere vs LGD Gaming | 09-24 | 34 | LGD Gaming | 315,034 | 2,186 | — |
| 19 | tier2 | pgl | Team Yandex vs Natus Vincere | 09-27 | 44 | Team Yandex | 517,610 | 6,173 | — |
| 20 | tier2 | gotf | Midas Club vs Team Resilience | 08-01 | 49 | Midas Club | 64,132 | 828 | 68,246 |
| 21 | tier2 | gotf | L1ga Team vs LGD Gaming | 08-01 | 54 | LGD.Pinghu | 173,624 | 2,485 | 171,964 |
| 22 | tier2 | gotf | REKONIX vs Yakult Brothers | 08-02 | 58 | REKONIX | 207,240 | 1,974 | 204,342 |
| 23 | tier2 | gotf | Rune Eaters vs LGD Gaming | 08-03 | 34 | LGD.Pinghu | 162,614 | 1,335 | 161,919 |
| 24 | tier2 | gotf | PlayTime vs Yakult Brothers | 08-05 | 63 | PlayTime | 347,951 | 4,286 | 344,790 |
| 25 | tier2 | winline | Recrent Club vs Daxak Club | 09-10 | 57 | Daxak Club | 1,038 | 51 | 1,038 |
| 26 | tier2 | winline | Stray Club vs Rostik999 Club | 09-11 | 48 | Stray Club | 7,584 | 408 | 7,565 |
| 27 | tier2 | winline | Daxak Club vs NS Club | 09-12 | 36 | Daxak Club | 700 | 37 | 701 |
| 28 | tier2 | winline | Rostik999 Club vs YBN Club | 09-12 | 32 | Rostik999 Club | 2,972 | 149 | 2,870 |
| 29 | tier2 | winline | Rostikfacekid Club vs Cooman Club | 09-13 | 26 | Rostikfacekid Club | 1,161 | 100 | 1,151 |

Median on-chain notional by bucket: BLAST **$284k**, PGL **$242k**, Games of the Future **$174k**, EPL Masters playoffs **$71k**, European Pro League playoffs **$13k**, Winline **$1.2k**. "Big" in this brief is not uniformly liquid. BLAST is. EPL playoffs are closer to a thin regional than to BLAST.

Map 15: both token on-chain files for 2026-09-20 exist and contain 0 rows (`build_tapes.py` print and a direct `ParquetFile.metadata.num_rows` check). Books for that day are large (139,230 snapshots each token). The $0 is an archive gap. verified.

### Part A1 — flow by game minute

Window is horn − 300s through game end + 60s. Each economic trade is counted once, from the radiant token's on-chain file. Prices on the two token files sum to 1.0 (checked on a BLAST map, 1,803/1,803 joins). Taker notional is shares times the price of the token the taker traded.

The taker-intent rule was checked against WS `origin_asset_id` + `side` on 333 unique price/size joins (PlayTime map, 2026-08-02, ±2s, one candidate only): **331/333** agree. verified.

| | median $ / map | mean $ / map |
|---|---:|---:|
| taker buy YES | 39,662 | 68,631 |
| taker buy NO | 29,036 | 68,773 |
| taker sell YES | 4,098 | 9,116 |
| taker sell NO | 4,795 | 10,331 |

Median on-chain notional in a single game minute stays in a band of roughly **$0.8k–$2.2k** from minute −1 through minute 30 (examples: min −1 $1,729, min 0 $2,197, min 10 $847, min 20 $1,361, min 30 $1,843, min 40 $602). It does not all arrive in one phase.

Share of taker notional by phase (sum across maps): pre-horn **22%**, 0–8 **19%**, 8–20 **19%**, 20+ **40%**. Median map: $10.8k / $16.2k / $20.4k / $27.9k in those four buckets.

WS prints exist for 16 maps (horn on or before 2026-09-17). Median WS notional **$64,485**. Median on-chain/WS ratio on those 16 is **1.01** (mean 1.04, p10 1.00, p90 1.05). On this sample the two tapes match. The 1.5–2.5× gap quoted for other windows does not show up here. verified.

Maps under $5k total: **5/30 (17%)**. Excluding the empty file, **4/29**, all Winline.

### Part A2 — book

Time-weighted over the same window. Gaps longer than 60s are capped so a dead feed cannot dominate; `max_gap_s` is stored per map in `map_stats.parquet`. Tick assumed 0.01. "Within 3 ticks" is 3 cents.

| | YES, median of maps | YES, mean of maps | NO, median of maps |
|---|---:|---:|---:|
| spread, ticks | 2.60 | 4.85 | 2.60 |
| fraction one-sided | 0.022 | 0.027 | 0.022 |
| fraction crossed | ~0 | 0.0001 | ~0 |
| best bid, time-weighted mean $ | 260 | 412 | 349 |
| best ask, time-weighted mean $ | 317 | — | 289 |
| depth within 3¢, bid, mean $ | 1,133 | 1,958 | 1,425 |
| distinct touch-price changes / min | 69 | 82 | 69 |

The time-weighted mean is a fat-tailed object. Across all 30 YES books, the **median snapshot** of best-bid dollars (not time-weighted) has a cross-map median of **$28** (p10 $12, p90 $204). That sits next to the ~$37–44 figure from the narrower model-window study. The $260 number is the mean, pulled by thick episodes. Both are in `build_tapes.py` / a post-pass over `tapes/*.npz`. verified.

YES and NO spreads match to the reported precision. Combined books are rarely locked: a one-sided token for 2% of the time is the real outage, not crossed quotes.

### Part A3 — who provides liquidity

Maker notional uses the maker's own token and price, one row per trade. Total maker notional **$4.48M**, against **$4.55M** taker notional. The two ledgers are the same trades priced from each side. verified.

1,466 maker addresses. **90%** of notional trades both tokens of a market. **48%** of notional is buy-both and zero sells inside this sample (423 addresses). Sells are 19% of maker notional, concentrated in a minority of wallets.

Median distance of a maker buy from the 1-second mid is **+0.5 tick** (bought half a cent under mid). p10 is −2.5 ticks, p90 is +4.0 ticks. The mid is last-observation-carried-forward up to 2 seconds, so this is coarse. verified as a distribution, not as a queue position.

Top 15 by maker notional on these 30 maps:

| maker | $ | share | fills | maps | med shares | both tokens | buy both, no sell |
|---|---:|---:|---:|---:|---:|---|---|
| 0x893575c7d99542163c6b6e8a0fe5af0b6d217daa | 139,543 | 3.1% | 1,584 | 16 | 107 | yes | yes |
| 0x59229282121256ed2d629ce5adf0e04cc7fb3388 | 129,689 | 2.9% | 92 | 6 | 67 | yes | yes |
| 0x3413c803c3a6efc8d963afbce2dcee48d3738ff2 | 125,690 | 2.8% | 479 | 14 | 93 | yes | yes |
| 0xfe787d2da716d60e8acff57fb87eb13cd4d10319 | 118,920 | 2.7% | 1,204 | 15 | 33 | yes | yes |
| 0x2e3c40fa47b27c676ddd573064162f57d51508ba | 115,705 | 2.6% | 1,670 | 27 | 74 | yes | yes |
| 0x4aec70021891ea712aaf3e2dd76c30f6b09a4ce9 | 115,379 | 2.6% | 538 | 15 | 62 | yes | yes |
| 0x7c37b52eb226bcb9411375ec48b0169663fb6aeb | 104,077 | 2.3% | 204 | 4 | 140 | yes | yes |
| 0xb35f674af4603c9602dfbf39564087e94897cf4c | 99,800 | 2.2% | 331 | 6 | 489 | yes | no |
| 0xe59f2ab1b26b403f95b9df6e18ea66b395010d08 | 79,191 | 1.8% | 254 | 7 | 83 | yes | no |
| 0x15508cacc0af5bdb52874a315b156a9d7a645481 | 75,463 | 1.7% | 378 | 11 | 108 | yes | yes |
| 0xd06c49e1c86f970bc5c48f91169a607e9b03cdaf | 72,820 | 1.6% | 409 | 19 | 104 | yes | no |
| 0xbed3644bcdaab7d8bfa582bbf9ffb7ef9598fbe2 | 64,402 | 1.4% | 278 | 6 | 153 | yes | no |
| 0x758dac51ba3cc9a246a79787da9df11c5f425d9e | 61,816 | 1.4% | 2,127 | 18 | 31 | yes | no |
| 0xb26fa000b3b281ba582bebc3457e12f0b8219536 | 60,487 | 1.4% | 251 | 8 | 116 | yes | no |
| 0x08c263965a9cbec5e83ac1d6f14b9bbc957bc023 | 59,560 | 1.3% | 199 | 5 | 150 | yes | no |

Activity API, fetched 2026-10-07, four pages × 500 via `cursor` (`https://data-api.polymarket.com/v2/activity`). This is the latest 2,000 rows, not history.

| wallet | TRADE | sides | MERGE | REDEEM | what the titles are |
|---|---:|---|---:|---:|---|
| 0x893575c7… | 1,859 | 1,859 BUY, 0 SELL | 120 | 21 | Valorant / CS |
| 0x59229282… | 1,928 | 1,926 BUY, 2 SELL | 0 | 66 | Dota |
| 0x3413c803… | 1,954 | 1,940 BUY, 14 SELL | 0 | 30 | Dota |
| 0x941aa558… (ours) | 1,989 | 1,360 BUY, 629 SELL | 0 | 4 | Dota |

verified for that recent window. `0x893575c7…` is running the buy-and-merge pattern right now, on Valorant and CS. On these Dota maps it bought both tokens and did not sell (16 maps, $140k). Whether those Dota pairs were merged is not in the latest 2,000 rows. likely, not verified. The two busiest recent Dota makers buy both sides and redeem; they are not showing merges in that window. So Dota liquidity is full of two-sided buyers, and the merge habit is confirmed for one of them on other games, not for the Dota-native names in the last 2,000 rows.

Our address: `session.jsonl` `fill_key` values (132 distinct hashes) overlap **0** on-chain `tx_hash` values. Matching ±5s, size within 2%, price within 1.1¢ on either token, the hits concentrate on `0x941aa5589961e33c54365a27a3223c916e6a24d9`. That address is 0.49% of maker notional here ($21,848, 172 fills, 13 maps), with $8,729 of sells. The API row above is the same wallet and does sell. likely the identification, verified the sell-and-no-merge behavior.

### Part A4 — jump risk

YES mid, 1-second grid, last good mid carried at most 2 seconds. Absolute forward change. Windows start in the phase. Numbers are the median across the 30 maps of each map's own quantile. verified.

| phase | 10s p50 | 10s p90 | 10s p99 | 10s max-of-medians | 10s rate of ≥5 ticks | 30s p90 | 60s p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| pre-horn | 0.25¢ | 2.0¢ | 5.5¢ | 6.3¢ | 2.0% | 4.0¢ | 6.0¢ |
| 0–8 min | 0.75¢ | 3.0¢ | 5.5¢ | 6.5¢ | 1.7% | 5.5¢ | 7.0¢ |
| 8–20 | 1.0¢ | 3.8¢ | 8.7¢ | 11.5¢ | 5.7% | 6.7¢ | 8.8¢ |
| 20+ | 1.0¢ | 4.7¢ | 11.6¢ | 17.5¢ | 8.5% | 7.5¢ | 10.5¢ |

The largest single 10-second move in the sample is 58.5¢ after minute 20. A resting bid 1 tick under mid is inside a routine 10-second jump after minute 8. A bid 3 ticks under mid is outside the median move and inside the p90 after minute 20. That is the adverse-selection budget the quoter is spending.

### Part B — simulator

`sim.py`. Self-check: both sides fill 20 shares at 0.48 against an empty queue and merge to `+$0.8749` including the rebate; a queue of 10 plus a 25-share print fills 15; the pessimistic model ignores that print and fills 20 only on a print at 0.47. It passes.

Rules, as run:

- Mid is the radiant touch midpoint when the book is two-sided and not crossed. Otherwise no new quote.
- `bid_yes = mid − h·0.01 − g·(yes−no)`, `bid_no = (1−mid) − h·0.01 + g·(yes−no)`, snapped to 0.01. Pulled if the bid would cross that token's ask, or if it leaves [0.03, 0.97], or if mid leaves that band.
- The side that increases `|net|` is pulled when `|net| > N_max`.
- Requote when mid moves by ≥1 tick or 2 seconds have passed. Cancel is live for 60ms more; the replacement is live 175ms after the decision and goes to the back of the queue.
- Base fill: on-chain prints that hit this token's bid (maker bought this token, or sold the other — same rule that matched WS aggressor side at 331/333) consume size at our price. We fill once traded size exceeds the size that was already on the level. A print strictly below our price fills whatever is left.
- If our price is below the touch and not among the 24 stored bid levels, the queue is treated as unknown and only a through-print fills. That case is conservative.
- Pessimistic: through-prints only.
- Sticky (extra, not the spec): do not cancel a side whose snapped price did not change.
- Merge `M=20` pairs whenever both inventories allow it, returning $1 per pair. Leftovers settle at `radiant_win`. Maker rebate `0.15·0.05·p·(1−p)·qty`. Taker flatten, when on, buys the short side at the ask in clips of `s` once `|net| > N_max`. It does not see ask size.
- Chain timestamps are whole seconds (1,803/1,803 on the probe file). The 175ms / 60ms latencies only matter across a second boundary.

Grid: h ∈ {1,2,3}, g ∈ {0, 0.0002, 0.0005}, N_max ∈ {100, 300}, s ∈ {20, 50}, taker off/on, base and pessimistic, plus sticky×base. 216 configs × 30 maps. `sim_results.parquet`, aggregated in `sim_grid.parquet`.

No config has a worst map better than −$50. The tightest floor that any config clears is −$100.

Chosen by PnL per dollar among base-queue configs with worst map ≥ −$100:

**h = 3, g = 0.0002, N_max = 100, s = 20, taker flatten off.**

| | base queue | last in queue (same knobs) |
|---|---:|---:|
| PnL, 30 maps | +$343 | +$219 |
| $ bought | 12,465 | 10,914 |
| PnL per $ bought | 2.75¢ | 2.01¢ |
| median map | +$6.68 | |
| worst map | −$54 | −$54 |
| maps negative | 7/30 (23%) | 9/30 (30%) |
| fills | 1,491 | |
| maker share | 100% | 100% |
| rebates | $39 | |
| fees | $0 | |
| peak capital p50 / p95 | $34 / $73 | |
| PnL big / tier-2 | +$239 / +$104 | |

Same knobs, size 50, base queue, is the highest total in the grid that is still near the best rate: **+$813 on $32,319 (2.52¢ per $1), worst −$86, 5/30 negative, peak p95 $125.** Pessimistic twin: **+$694 on $28,895 (2.40¢), worst −$86.** Taker flatten does not improve these rows; it adds fees and a slightly worse rate.

Half-spread sensitivity at g=0.0002, N=100, s=20, taker off:

| h | base PnL | base ¢/$ | base worst | base % neg | pessimistic PnL | pessimistic ¢/$ |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | +$12 | 0.03 | −$54 | 37% | −$87 | −0.25 |
| 2 | +$278 | 1.24 | −$53 | 33% | +$175 | 0.92 |
| 3 | +$343 | 2.75 | −$54 | 23% | +$219 | 2.01 |

Buying less, further from the mid, is the whole result. One tick back trades a lot ($43k bought) and gives the edge away.

Sticky, base queue, best rate: h=3, g=0, N=100, s=50, **+$737, 2.92¢ per $1, worst −$85.** Keeping the queue when the price does not change does not change the conclusion. The 2-second cancel is not what creates the PnL.

Where the $343 comes from (FIFO lot phase, plus rebates of $39 on top):

| phase | $ bought | FIFO PnL | ¢ per $ bought |
|---|---:|---:|---:|
| pre-horn | 102 | +3 | 3.0 |
| 0–8 | 1,081 | +53 | 4.9 |
| 8–20 | 2,223 | +89 | 4.0 |
| 20+ | 9,059 | +160 | 1.8 |

Late game is 73% of the buys and about half of the FIFO PnL. The rate is better early, when jumps are smaller. Big-tier maps contribute +$239 of +$343; tier-2 contributes +$104, and the worst map is tier-2.

Per map, chosen config (base queue):

| id | teams | PnL | $ bought | fills | pairs merged | peak $ | 20+ FIFO |
|---:|---|---:|---:|---:|---:|---:|---:|
| 28 | Rostik999 vs YBN | −54.06 | 155 | 19 | 100 | 61 | −42 |
| 26 | Stray vs Rostik999 | −17.10 | 394 | 50 | 360 | 57 | −28 |
| 12 | Nemiga vs Kalmychata | −16.98 | 298 | 40 | 280 | 39 | −27 |
| 27 | Daxak vs NS | −8.94 | 9 | 3 | 0 | 9 | −6 |
| 7 | Ilbirs vs Zero Tenacity | −5.13 | 226 | 29 | 220 | 33 | −8 |
| 17 | Pipsqueak+4 vs Cobra | −1.47 | 62 | 9 | 60 | 24 | 0 |
| 1 | Liquid vs Yakult | −0.76 | 21 | 3 | 20 | 19 | 0 |
| 15 | PlayTime vs Cobra | 0 | 0 | 0 | 0 | 0 | 0 |
| 25 | Recrent vs Daxak | +0.24 | 60 | 7 | 60 | 29 | −0.1 |
| 22 | REKONIX vs Yakult | +0.37 | 662 | 69 | 660 | 48 | −8 |
| 13 | Synapse vs Kalmychata | +0.87 | 207 | 27 | 200 | 39 | −2 |
| 2 | Nemesis vs NaVi | +1.21 | 19 | 1 | 0 | 19 | +1 |
| 6 | Power Rangers vs YS | +1.30 | 179 | 23 | 180 | 43 | −6 |
| 23 | Rune Eaters vs LGD | +3.85 | 237 | 30 | 240 | 30 | −6 |
| 14 | Synapse vs YS | +4.73 | 166 | 22 | 160 | 32 | +0.4 |
| 18 | NaVi vs LGD | +8.63 | 52 | 7 | 60 | 29 | 0 |
| 5 | Spirit vs Yandex | +10.46 | 351 | 35 | 340 | 28 | +5 |
| 8 | Level UP vs Rune Eaters | +14.60 | 560 | 71 | 560 | 28 | +7 |
| 16 | Conventus vs 1win | +15.69 | 225 | 24 | 240 | 28 | +4 |
| 11 | NaVi vs Klim | +15.83 | 1,007 | 126 | 1,000 | 57 | +3 |
| 20 | Midas vs Resilience | +16.34 | 301 | 33 | 300 | 39 | +7 |
| 4 | GamerLegion vs Liquid | +18.44 | 641 | 72 | 600 | 46 | +7 |
| 29 | Rostikfacekid vs Cooman | +23.63 | 167 | 22 | 160 | 96 | +8 |
| 21 | L1ga vs LGD | +31.79 | 771 | 90 | 780 | 31 | +27 |
| 0 | 1win vs NaVi | +33.82 | 756 | 93 | 780 | 31 | +23 |
| 24 | PlayTime vs Yakult | +36.81 | 886 | 95 | 860 | 82 | +30 |
| 9 | MOUZ vs Synapse | +37.60 | 1,103 | 138 | 1,120 | 53 | +29 |
| 10 | MOUZ vs GamerLegion | +39.75 | 484 | 62 | 500 | 40 | +31 |
| 19 | Yandex vs NaVi | +47.87 | 970 | 108 | 960 | 52 | +37 |
| 3 | MOUZ vs OG | +83.60 | 1,501 | 183 | 1,560 | 34 | +74 |

Three examples, radiant mid plotted in `work/grok-sim/plots/map{3,18,28}_mid.png` (market mid, not our inventory):

- **MOUZ vs OG (map 3), best.** 60 minutes, $309k of taker flow. The quoter buys $1,501, merges 1,560 pairs, peak capital $34, PnL +$84. $74 of the FIFO PnL is after minute 20.
- **NaVi vs LGD (map 18), near the median.** $315k of market flow, and we only get 7 fills / $52 bought / +$8.63. A $20 clip 3 cents back simply does not trade much, even on a liquid map.
- **Rostik999 vs YBN (map 28), worst.** Market flow is $3.0k. We buy $155, merge only 100 shares, peak outlay $61, and lose $54, of which $42 is after minute 20. Thin book, inventory held through a jump.

### Assumptions

1. Tick is 0.01. Some live Dota maps are 0.001. Our quotes still snap to 0.01, as the brief says. A 0.001 book can have size between our cent and the next one; if that level is inside the stored ladder we do not count it as our queue, and a print 0.1¢ under us is a through-fill.
2. On-chain time is 1 second. Book time is microseconds. A print can be applied up to about a second early relative to the book move it caused. That is optimistic for "were we already live?" The pessimistic column is the check, and it stays positive at h=3.
3. No market impact and no reaction from the makers in Part A3. We are a ghost. At s=20 this is a small lie (about $10 notional against a ~$28 median touch). At s=50 it is a larger one. Nothing above 50 shares was run.
4. Through-prints fill our entire remaining size. That is right if the seller walked the book. Inside a 1-second bucket it can mix two unrelated prints and over-fill.
5. Historical books do not contain our order, so size-ahead is everyone else's size. Correct for a counterfactual.
6. Merge is free and instant at 20 pairs. No gas, no delay, no failed transaction.
7. Rebate is `0.15 * 0.05 * p * (1−p) * qty` on our maker fills only. No liquidity rewards. Maker fee is zero.
8. Taker flatten, in the rows where it is on, lifts the best ask without checking its size.
9. Game end and `radiant_win` come from the match catalog. A wrong winner flips settlement on unmerged leftovers. Merged pairs do not depend on the winner.
10. The 2-second replace resets queue position, per the brief. The sticky column shows the edge is still there if we only replace when the price changes.

What would make this optimistic even after the pessimistic column: competition (other bids at the same price), the 1-second clock, and impact if the clip grows. What would make it pessimistic: the 24-level ladder fallback, resetting the queue every 2 seconds, and refusing to quote a one-sided book.

## Open questions / what I could not verify

- Whether `0x893575c7…` merges on Dota specifically. The latest 2,000 activity rows are Valorant/CS. Paging back through their Dota months was not practical at a polite rate.
- Our `fill_key` hash. It does not join `tx_hash`. The wallet id is from price/size/time, not from a tx.
- Map 15's missing fills. Books are present; both on-chain files are empty. I did not reconstruct that map from another source.
- Snapshot median of depth *within 3 cents*. I only stored the time-weighted mean (~$1.1k). The touch median ($28) shows the mean is tail-heavy, so the 3-cent mean is probably high versus a median too.
- A model-fair offset. Not run. The pure-book quote is the result above.
- Clips above 50 shares, and any size that is a real fraction of the $28–$260 touch.

## Files

Scripts, all under `work/grok-sim/`, run with `esports-trader/.venv/bin/python`:

- `select_maps.py` — the 30 markets
- `build_tapes.py` — books, fills, flow, jumps, tapes
- `sim.py` — grid, includes the self-check
- `summarize.py` — Part A aggregates
- `summarize_sim.py` — grid aggregates and the floor search
- `our_wallet.py` — session fill join

Outputs:

- `selected_maps.parquet`, `map_stats.parquet`, `flow_minutes.parquet`, `jumps.parquet`, `fills.parquet`, `makers.parquet`
- `summary_numbers.json`, `sim_results.parquet`, `sim_grid.parquet`, `sim_summary.json`
- `tapes/<map_id>.npz`, `sim_parts/<map_id>.parquet`
- `plots/map3_mid.png`, `plots/map18_mid.png`, `plots/map28_mid.png`
