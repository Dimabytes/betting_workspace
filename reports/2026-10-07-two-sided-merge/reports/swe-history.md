# swe-history — trader 0x6e2c full history, scaling vs edge, similar wallets, Dota capacity
Status: FINAL

Work dir: `$R/work/swe-history/`. Wallet = `0x6e2c0e9474af7d720e5aba2a5b0bb6f723b7787c`
(profile `0x99a093771ad58bcfc3023cd75566415f`, pseudonym "Flaky-Integer").

## Summary

- **He has NEVER traded Dota 2.** 0 Dota rows in 758,739 activity rows + 226,062 taker fills covering his entire life on Polymarket (2026-01-07 → 2026-10-07). Not a single fill. (`weekly.py`, grep over `activity_history.json`+`activity_compact.json`+`taker_history.json`+`taker30.json`) — verified
- Three eras: **probe** (Jan 7–Feb 1, trad sports, ~$36k buy), **crypto 15-min fee farm** (Feb 20–Apr 19, 104k trades on "Bitcoin/Ethereum Up or Down", +$27.4k `fees_refunded` lifetime, all accrued here), **esports buy-both+merge** (Jul 24→now). 96-day dormancy between era 1 and era 2.
- Buy-both+merge is not new: he merged within 30 min of his first ever trade (Clippers–Knicks, Jan 7). Crypto era bought both outcomes in 89% of its markets but exited via REDEEM (1,002) more than MERGE (214). Esports era: first esports trade Jul 24 10:04 UTC (CS2 magic–3DMAX); first esports MERGE Jul 30 16:13 — the same day his SELLs stopped (18,086 sells Jul 24–30, then ~25 total in 9 weeks).
- Ramp was fast: $175k buy week 1 (3 days), $1.6M week 2, $3.6M+ by week 3. No long paper phase.
- Maker/taker: 35–52% taker by records during Aug ramp → 16–26% now. He has become *more passive* while scaling 25x.
- No capacity signature: gross edge (`trade_pnl`/`volume_usdc`, weekly diff of cumulative user-pnl) sits at 2.3–4.1c/$ from $150k/wk up to $4.4M/wk; no downtrend.
- **Activity feed misses ~$14M of his era-2 return flow** (Aug–early-Sep: $16.5M CS2 buys vs $3.5M merge+redeem+sell in feed, while `unrealized_pnl`≈0 and `realized_pnl` kept climbing). Redemptions before ~Sep 7 are under-indexed; do NOT compute PnL from activity cash flow — use `/v2/user-pnl` or `/v2/positions`. — verified
- 25/98 sampled wallets (his top counterparties + monthly-volume leaderboard) run the same buy-only+merge pattern; 19 of them trade Dota (one is ~93% Dota). The strategy is a known recipe with real competition already on Dota books. — verified
- Polymarket volume Sep 1–Oct 6 (gamma, moneyline markets only): CS2 $289M, LoL $323M, **Dota $161M**, Val $30M. Dota is ~64% of CS2 — not a small pond. His 30d buy-cost share: CS2 ~2.9%, LoL ~0.6%, Val ~5%, Dota 0%.
- Dota moneyline is tournament-spiky: $7–20M/wk baseline, $70M in W39 (BLAST Slam + PGL Wallachia). Median Dota map market = $69k lifetime volume (fattest tail of the four games).

## Findings

### 1. Full history

Fetch: `fetch_history.py` — `data-api /v2/activity?user=..&limit=500&end=<cursor>` (end is
inclusive, verified), cursor = min ts − 1, <=4 req/s. 884 pages, 382,778 rows, back to
2026-01-07 17:16 UTC (join_date 2026-01-07 16:28 per `stats-now.json`). Combined with the
existing 30d file: **758,739 rows** total; vs `/v2/user-pnl` cumulative `trade_count`
790,136 (trade count there includes legs the activity feed folds differently — same order).

Per-ISO-week activity table (script `weekly.py` → `weekly_activity.json`; buy cost = Σ
`usdc_size` of BUY rows; map/series split by title regex "Map N|Game N" vs "BO3/BO5"):

| week | trades | buy | sell | buy_cost$ | merges | merge$ | redeem$ | cs2$ | lol$ | val$ | other$ | map fills | series fills |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W02 (Jan 5–11) | 297 | 297 | 0 | 2,141 | 6 | 121 | 118 | 0 | 0 | 0 | 2,141 | 0 | 0 |
| W03 | 563 | 514 | 49 | 9,680 | 20 | 1,095 | 1,112 | 0 | 0 | 0 | 9,680 | 0 | 0 |
| W04 | 775 | 663 | 112 | 13,699 | 26 | 1,442 | 2,005 | 0 | 0 | 0 | 13,699 | 0 | 0 |
| W05 | 583 | 539 | 44 | 9,279 | 16 | 794 | 487 | 0 | 0 | 0 | 9,279 | 0 | 0 |
| W08 (Feb 16+) | 11,781 | 11,775 | 6 | 34,343 | 5 | 916 | 4,145 | 0 | 0 | 0 | 34,343 | 0 | 0 |
| W09 | 29,947 | 29,947 | 0 | 147,645 | 121 | 28,634 | 22,576 | 0 | 0 | 0 | 147,645 | 0 | 0 |
| W10 | 7,023 | 7,023 | 0 | 50,502 | 53 | 23,528 | 3,858 | 0 | 0 | 0 | 50,502 | 0 | 0 |
| W11 | 8,678 | 8,673 | 5 | 41,599 | 15 | 2,426 | 6,312 | 0 | 0 | 0 | 41,599 | 0 | 0 |
| W12 | 16,734 | 16,692 | 42 | 70,129 | 8 | 3,279 | 7,429 | 0 | 0 | 0 | 70,129 | 0 | 0 |
| W13 | 15,233 | 15,221 | 12 | 49,385 | 7 | 1,521 | 6,434 | 0 | 0 | 0 | 49,385 | 0 | 0 |
| W14 | 11,725 | 11,721 | 4 | 47,557 | 2 | 460 | 5,142 | 0 | 0 | 0 | 47,557 | 0 | 0 |
| W15 | 2,169 | 2,134 | 35 | 8,428 | 3 | 296 | 993 | 0 | 0 | 0 | 8,428 | 0 | 0 |
| W16 | 855 | 773 | 82 | 3,143 | 0 | 0 | 54 | 0 | 0 | 0 | 3,143 | 0 | 0 |
| W17–W29 | ~0 | | | | | | | | | | | | |
| W30 (Jul 20–26) | 18,090 | 7,392 | 10,698 | 175,279 | 0 | 0 | 13,043 | 175,279 | 0 | 0 | 0 | 3,599 | 3,793 |
| W31 | 38,913 | 31,525 | 7,388 | 1,642,154 | 400 | 264,791 | 36,380 | 1,642,144 | 0 | 0 | 10 | 16,276 | 15,245 |
| W32 | 19,439 | 19,439 | 0 | 510,482 | 155 | 78,613 | 30,906 | 506,683 | 3,799 | 0 | 0 | 9,630 | 9,809 |
| W33 | 39,034 | 39,032 | 2 | 3,648,134 | 500 | 550,681 | 46,238 | 3,648,134 | 0 | 0 | 0 | 18,656 | 20,376 |
| W34 | 47,476 | 47,476 | 0 | 4,191,813 | 505 | 623,039 | 292,572 | 3,995,202 | 10,889 | 185,722 | 0 | 25,881 | 21,595 |
| W35 | 44,378 | 44,377 | 1 | 3,245,958 | 352 | 269,029 | 276,016 | 3,106,853 | 17,418 | 121,687 | 0 | 24,936 | 19,441 |
| W36 | 63,392 | 63,392 | 0 | 3,090,146 | 351 | 161,827 | 431,617 | 2,840,880 | 146,915 | 102,351 | 0 | 39,051 | 24,341 |
| W37 | 96,452 | 96,452 | 0 | 1,093,327 | 990 | 802,786 | 299,567 | 771,397 | 321,930 | 0 | 0 | 57,145 | 39,307 |
| W38 | 50,998 | 50,988 | 10 | 784,293 | 550 | 719,073 | 78,463 | 455,349 | 328,944 | 0 | 0 | 30,516 | 20,472 |
| W39 | 90,503 | 90,503 | 0 | 1,641,311 | 1,842 | 1,571,973 | 80,948 | 1,020,228 | 355,195 | 265,888 | 0 | 47,444 | 43,059 |
| W40 | 104,129 | 104,123 | 6 | 3,390,689 | 2,201 | 3,313,939 | 133,873 | 2,078,503 | 353,178 | 959,008 | 0 | 60,191 | 43,932 |
| W41 (2d) | 26,498 | 26,492 | 6 | 2,835,600 | 1,202 | 2,832,335 | 21,387 | 2,683,852 | 57,712 | 94,036 | 0 | 16,357 | 10,135 |

Notes on the table:
- "other$" in W02–W16 = trad sports (Jan) and crypto up/down (Feb–Apr); W22 row is one stray MLB trade.
- `role` is populated on TRADE rows only from ~Sep 7 (W37+); earlier weeks' maker/taker split comes from `/v2/trades?taker_only=true` (`fetch_taker.py` → `taker_history.json`, 144,666 rows pre-window; cross-validated against `role` for W37+ — matches within ~1%).

Taker share of his trade records per week (taker fills / all trade rows):
W02–W05 9–30% (probe); **W08–W16 25–52% (crypto era)**; **W30–W36 36–52% (esports ramp)**;
W37 24%, W38 22%, W39 26%, W40 19%, W41 16%. Trend: aggressive during entry, passive at scale.

**Dota 2: zero fills, all-time.** Searched `title` for `dota` (case-insensitive) across all
758,739 activity rows and all 226,062 taker-side trades: 0 hits. He has never touched Dota.

### 2. Strategy eras

| era | dates | markets | trades | buy $ | exits | edge |
|---|---|---|---|---|---|---|
| 0 probe | Jan 7 – Feb 1 | NBA/NHL/tennis/NCAAB (783 mkts) | 2,217 | $36k | 68 merges, $3.7k redeems, 205 sells | realized −$355 |
| 1 crypto farm | Feb 20 – Apr 19 | BTC/ETH "Up or Down" 15-min (5,391 mkts) | 104,146 | $531k | 214 merges, 1,002 redeems | trade_pnl +$2.3k, realized −$167; **fees_refunded +$27.4k** |
| dormancy | Apr 19 – Jul 24 | — (1 stray trade May 27) | ~0 | — | — | — |
| 2 esports | Jul 24 – now | CS2/LoL/Val map+series | 639,302 | ~$26M | merge-dominant; redeems weekly | realized +$496.2k |

Timeline details (all verified against `activity_history.json`):
- First TRADE 2026-01-07 17:16 UTC, "Clippers vs. Knicks" — 48 min after join. First MERGE
  17:45 same day (30 min later). The pair-arb mechanics were there from minute one.
- Era 1 = fee-refund farm: 99.9% of trades on crypto 15-min up/down, both outcomes in 4,807/5,391
  markets, redeem-heavy exit (redeem$ > merge$), taker 25–52%. Trade PnL ≈ breakeven; the money
  was the `fees_refunded` program ($27,371 lifetime, all accrued W08–W17, frozen since — matches
  `stats-now.json` `fees_refunded=27371.02`). When refunds ended (~Apr 19) he stopped.
- Restart: first esports trade 2026-07-24 10:04 UTC (CS2 magic vs 3DMAX BO3). Week 1 = buys AND
  18,086 sells (~$276k proceeds) — a sell-on-spike variant. First esports MERGE 2026-07-30 16:13
  (3DMAX vs MOUZ, 1,906 shares); sells stop the same day. From Aug 3 (W32) he is merge-only
  (25 sells total in the 9 weeks since).
- Game onboarding: CS2 only Jul 24 → LoL starts ~Aug 3 (W32 $3.8k) → Valorant ~Aug 17
  (W34 $186k first week). Map+series fills are roughly 55/45 by count throughout era 2.
- Ramp: $175k (3 days) → $1.6M → $0.5M dip → $3.6M buy/wk; multi-million within 2.5 weeks.

### 3. Scaling vs edge

Source: `user-pnl.json` 1h cumulative points → weekly diffs (`pnl_weekly.json`; verified
monotonic in volume/trade_count; `realized_pnl` cumulative lands at $495,685 = stats-now).

| week | trades | volume_usdc | trade_pnl | fees_paid | realized | edge c/$ | net c/$* |
|---|---|---|---|---|---|---|---|
| W30 | 9,611 | 149k | 4,311 | −1,638 | 2,673 | 2.89 | 1.84 |
| W31 | 48,235 | 1,628k | 55,537 | −20,675 | 34,909 | 3.41 | 2.31 |
| W32 | 26,403 | 1,033k | 33,296 | −14,029 | 19,166 | 3.22 | 2.37 |
| W33 | 36,659 | 3,140k | 120,545 | −39,477 | 81,067 | 3.84 | 2.94 |
| W34 | 54,606 | 4,354k | 114,096 | −59,504 | 54,785 | 2.62 | 1.79 |
| W35 | 52,373 | 3,828k | 127,828 | −50,157 | 75,917 | 3.34 | 2.56 |
| W36 | 67,677 | 3,346k | 136,061 | −45,509 | 91,986 | 4.07 | 3.25 |
| W37 | 115,174 | 1,641k | 38,886 | −16,134 | 22,717 | 2.37 | 2.33 |
| W38 | 51,023 | 775k | 18,315 | −6,495 | 12,844 | 2.36 | 1.53 |
| W39 | 89,259 | 1,604k | 36,522 | −16,592 | 19,143 | 2.28 | 1.24 |
| W40 | 104,301 | 3,356k | 83,584 | −30,465 | 53,253 | 2.49 | 1.58 |
| W41 (2d) | 27,076 | 2,791k | 53,459 | −25,734 | 27,754 | 1.92 | 0.99 |

\* net = trade_pnl + fees_paid + maker_rebate + taker_rebate; rebate fields stop accruing after
Oct 4 (cumulative flat at mkr $20,109 / tkr $77,517) — likely payout/reporting lag, so late-week
net is understated.

Reading: edge per $ has NO downtrend vs volume — 25x volume growth (149k→3.4–4.4M/wk) with
gross edge pinned at ~2.3–4.1c/$; Pearson corr(weekly volume, edge) = **+0.43** (n=13 era-2
weeks, mean edge 2.74c/$ ± 0.84) — if anything, edge is *higher* in big-volume weeks
(big-tournament flow is better flow). What DID change with scale is taker share (down) and
merge share of exits (up). The constraint looks like opportunity supply (matches/week), not his
own price impact — consistent with a take-what-books-give quoter rather than a size-pusher.

Per-game edge, net cash basis (30d window only — the feed is complete there;
`activity_compact.json`, merge+redeem+sell−buy):

| game | buy $ | merge+redeem+sell $ | net cash | net c/$ |
|---|---|---|---|---|
| CS2 | 7.00M | 7.08M | +78.0k | **1.11** |
| LoL | 1.42M | 1.44M | +20.4k | **1.44** |
| Val | 1.32M | 1.34M | +21.8k | **1.65** |
| total | 9.74M | 9.86M | +120.2k | 1.23 |

Cross-check: `user-pnl` realized for Sep 7–Oct 7 ≈ $135.7k — same ballpark (cash-flow net
excludes ~$52k of MAKER/TAKER_REBATE rows, which carry no title; adding them back gives ~1.8c/$
all-in). Valorant monetizes best per $, CS2 worst — yet he concentrates on CS2 because that's
where the volume is. Note: `/v2/positions` per-position `realized_pnl` exists but is NOT
additive (residual-position basis; sums to $1.92M ≫ account realized $495.7k because merged
quantities drop out of position rows). Only its per-market shape is meaningful: worst single
position −$19.3k (Falcons–NaVi map2 YES residual), best +$29.5k (Vitality–Spirit map2 Spirit
residual) — both EWC-tier CS2.

### 4. Similar wallets

Method: top-30 `maker` counterparties when he is taker + top-30 `taker` counterparties when he is
maker in `lol_chain.json` (146,814 fills), plus data-api leaderboard top-50 by month VOLUME
(`leaderboard_month_volume.json`; he is rank #43 with 19.7M "volume" — note this volume metric is
in SHARES, ~2× his $9.7M buy cost at avg price ≈0.5, consistent with `user-volume.json`
volume=59.56M vs volume_usdc=28.2M ≈ 2.1×. The leaderboard `pnl` column shows **−$3.39M for
him**, i.e. it does not credit merge/redeem extraction; do not use leaderboard pnl for this
strategy). Classification = last 500 activity rows
(`classify_wallets.py` → `wallet_classification.json`): "pattern" = 0 sells AND ≥1 merge AND
esports present.

**25/98 wallets match the buy-only + merge pattern; 19 of the 25 have Dota fills in their last
500 rows.** Same-recipe bots are already on Dota books:

| wallet | src | fills w/ him | buy/sell | merges | merge$ | games (of last 500) | both-outcome mkts |
|---|---|---|---|---|---|---|---|
| 0x758dac51 | maker cp | 2,435 | 365/0 | 130 | 5,750 | val 112, oth 170, dota 32, cs2 51 | 21/91 |
| 0x6891589732 | maker cp | 906 | 331/0 | 154 | 2,916 | dota 37, oth 104, val 92, cs2 98 | 21/94 |
| 0x893575c7d9 | maker cp | 854 | 481/0 | 14 | 11,923 | val 287, cs2 168 | 7/24 |
| 0xda770326bd | maker cp | 763 | 253/5 | 5 | 20 | oth 102, cs2 97, dota 19 | 25/67 |
| 0x10d79cc72d | maker+taker cp | 634+1,612 | 387/0 | 85 | 15,834 | cs2 242, lol 145 | 25/30 |
| 0xb877962274 | maker cp | 611 | 433/0 | 63 | 11,897 | lol 331, **dota 102** | 10/10 |
| 0x2e3c40fa47 | maker cp | 533 | 402/0 | 88 | 1,657 | cs2 295, **dota 64**, lol 43 | 17/21 |
| 0xfe787d2da7 | maker cp + **lb rank #1** | 509 | 484/0 | 10 | 76,926 | oth 243, val 162, cs2 79 | 12/35 |
| 0xc9255b8d5e | maker cp | 493 | 399/0 | 81 | 8,940 | cs2 193, **dota 116**, lol 86 | 18/26 |
| 0xe01069845e | maker cp | 413 | 463/0 | 1 | 110 | **dota 463** | 26/33 |
| 0x47138dc1ee | maker cp | 403 | 445/0 | 31 | 2,607 | cs2 189, lol 132, val 71, dota 53 | 26/48 |
| 0x904ed7c782 | maker cp | ~390 | 371/0 | 96 | 1,154 | cs2 176, lol 139, **dota 56** | 26/33 |
| 0xf1ee4842fa | taker cp | 941 | 436/0 | 38 | 10,137 | lol 205, cs2 99, **dota 73** | 27/55 |
| 0xa24e51e917 | lb top50 | — | 411/0 | 86 | 6,417 | val 210, cs2 105 | 14/18 |
| 0xf1f2b3c6c5 | lb top50 | — | 452/0 | 47 | 15,916 | cs2 91, val 85, **dota 39** | 15/22 |

Leaderboard top-50 (month vol): only ~5 show the pattern in their last 500 rows, mostly on
'other' sports — the recipe is used broadly on sports, not esports-specific. His top taker-side
counterparty `0x10ef48324b` (4,590 fills) is a different animal: LoL scalper, 169 sells, no
merges — directional, not pair-arb.

Dota already has dedicated pair-arb bots (0xe01069845e is ~93% Dota by recent rows;
0x1d24e4fa94 327 dota + 0 in others among last 500). Two-sided quoting on Dota is contested.

### 5. Dota vs other games on Polymarket

Method: `fetch_gamma.py` → `gamma_events.json`. `gamma-api /events?tag_id=&closed=true&
end_date_min=2026-09-01&end_date_max=2026-10-06` (offset paging; tag ids: dota 102366, cs2
100780, lol 65, val 101672 — resolved from event `tags[]`). Event `markets[]` carries
`volumeNum` + `sportsMarketType` (`moneyline`=series, `child_moneyline`=map/game winner, rest=
props). **Units verified: gamma `volumeNum` equals on-chain USDC notional exactly** — LoL SR–JDG
game1 market: archive on-chain fills Σ(amount·price)=295,962.36 vs gamma 295,962.36 (script in
shell history; uses `data/lol/processed/universe/markets.parquet` for token ids).

Closed in window (endDate 2026-09-01..10-05):

| game | events | moneyline mkts (map+ser) | map vol | series vol | prop vol | median map-mkt vol |
|---|---|---|---|---|---|---|
| CS2 | 1,688 | 4,621 | $103.7M | $185.1M | $16.6M | $5.7k |
| LoL | 384 | 1,219 | $210.8M | $112.6M | $12.2M | $39.8k |
| **Dota** | **250** | **759** | **$111.2M** | **$49.4M** | **$2.4M** | **$69.1k** |
| Val | 126 | 397 | $10.1M | $19.9M | $2.1M | $1.6k |

Weekly moneyline volume ($M map / $M series):

| week | dota map | dota ser | cs2 map | cs2 ser | lol map | lol ser | val map | val ser |
|---|---|---|---|---|---|---|---|---|
| W36 | 5.7 | 1.5 | 18.7 | 31.0 | 72.0 | 28.1 | 1.4 | 2.2 |
| W37 | 7.4 | 2.7 | 25.5 | 50.2 | 54.3 | 23.5 | 0.1 | 0.4 |
| W38 | 13.4 | 7.0 | 14.9 | 31.7 | 44.1 | 17.5 | 0.1 | 0.4 |
| W39 | 51.7 | 19.1 | 15.4 | 25.8 | 11.6 | 18.9 | 2.4 | 5.5 |
| W40 | 31.5 | 18.6 | 22.1 | 34.6 | 20.9 | 22.1 | 6.2 | 11.4 |
| W41* | 1.5 | 0.5 | 7.2 | 11.9 | 7.9 | 2.4 | — | — |

\* W41 = Oct 5 only (window ends Oct 6 00:00).

Top-10 Dota markets by volume (all BLAST Slam / PGL Wallachia — late-Sept/early-Oct majors):

| vol | kind | market |
|---|---|---|
| $2.05M | series | BetBoom vs OG — BLAST Slam Group C (Sep 30) |
| $1.84M | series | Spirit vs Yandex — BLAST Slam Seeding (Oct 3) |
| $1.78M | series | NaVi vs LGD — PGL Wallachia Playoffs (Sep 24) |
| $1.76M | series | Aurora vs Liquid — BLAST Slam Group D (Oct 1) |
| $1.60M | series | LGD vs Xtreme — BLAST Slam Group A (Oct 2) |
| $1.56M | series | Conventus Stellarum vs 1win — PGL Group (Sep 21) |
| $1.51M | series | LGD vs NaVi — PGL Playoffs (Sep 27) |
| $1.47M | series | LGD vs GamerLegion — BLAST Slam LCQ (Oct 3) |
| $1.45M | series | 1win vs NaVi — BLAST Slam Group B (Sep 29) |
| $1.44M | map | 1win vs NaVi — Game 2 Winner (Sep 29) |

Trader's share of moneyline volume (his 30d buy cost vs game volume Sep 7–Oct 6 window ≈
W37–W41): **CS2 $7.0M/$239M ≈ 2.9%**, **LoL $1.42M/$223M ≈ 0.6%**, **Val $1.32M/$26.5M ≈ 5.0%**,
**Dota $0/$153M = 0%**.

Capacity read: Dota moneyline volume is ~64% of CS2's in the same window — the same order of
magnitude he already harvests at ~3% share. But Dota is *lumpy*: baseline $10–20M/wk, spiking to
$50–70M during BLAST/PGL weeks, and each match is one ~$70k-median market (vs CS2's $5.7k median —
Dota matches are bigger but rarer). A maker quoting both sides at even 1% of Dota moneyline
volume sees ~$150–300k/wk of buys at baseline, ~$500k+ in event weeks. At his observed 2.3–4c/$
gross edge that's ~$4–12k/wk gross before competition costs — real but not transformative; and
unlike CS2/LoL, Dota books already host dedicated pair-arb bots (§4), so realized edge/$ will
likely sit below his cross-game average.

### 6. Profile page

`https://polymarket.com/@0x99a093771ad58bcfc3023cd75566415f` (agent-browser session
`swe-history`; redirected to `/es/` locale; screenshots `profile.png`, `profile_activity.png`):
- Joined "ene 2026" ✓ (Jan 2026). Bio: "With a big enough neural net, and enough signals, you can
  predict anything." Pseudonym "Flaky-Integer", "Platinum" tier badge. 5.0K views.
- Header: **PnL total +$825.2K** (= `trade_pnl` $824k — the site displays gross trade PnL, NOT
  realized-after-fees $495.7k), **Volumen total 59.6M** (shares; stats-now `volume`=59.56M),
  Valor de posiciones $3,385.81, Mayor ganancia $9,765.32, Mercados operados 10,343.
- Positions tab shows live inventory in real time — current rows visible: e.g. ENJOY 790.5sh
  @52.7¢ (mark 81¢, +53%) AND ILLYRIANS 810.8sh @46.4¢ (mark 19¢, −59%) in the same market —
  the unmerged-residual signature of the pair strategy, on display publicly.
- Activity tab ("Actividad") streams his fills live ("hace 1 minuto" = 1-min-old buys) — no login
  needed. Both tabs are usable for near-real-time monitoring.

## Open questions / what I could not verify

- **The ~$14M return-flow gap**: Jul 24–Sep 6 he bought ≈$16.5M of CS2 while the activity feed
  shows only ≈$3.5M of merges+redeems+sells; positions now ≈$3.4k and realized=+$495k, so the
  balance came back through a channel `/v2/activity` does not index for that period (bulk
  settlement redemptions via `redeemPositions`, or ERC1155 transfers out; not negRisk — checked,
  the big EWC markets are negRisk=false). Per-market example: `cs2-fut-mouz-2026-08-21` series
  (cid `0xcff0a01d…`) — 46,933+44,749 shares bought, all-time feed shows ZERO merges and ONE
  redeem of 1,774 shares; ~90k shares exited invisibly. Era 1 shows the same gap ($531k crypto
  buys vs ~$118k merge+redeem+sell in feed). This makes activity-based cash-flow PnL wrong for
  any historical window; weekly edge numbers above use `/v2/user-pnl` (correct) instead. — verified
  gap exists; mechanism (unindexed redeems vs transfers-out) unresolved.
- Whether leaderboard `pnl` (−$3.39M for him) is a computed-position artifact — consistent with
  merge extraction being invisible to it, but not proven. — speculative
- `role` field absent on TRADE activity rows before ~Sep 7 — API limitation; reconstructed via
  `/v2/trades?taker_only`. — verified it matches post-Sep-7 `role` within ~1%.
- `/v2/positions` is capped at one residual row per (condition, token) — `positions_all.json`
  (2,045 rows) covers only tokens that still exist; it cannot reconstruct per-market PnL for
  fully-merged markets. — verified

## Files

- `fetch_history.py` → `activity_history.json.gz` (382,778 rows, Jan 7–Sep 7; gzipped, `weekly.py` auto-reads .gz)
- `fetch_taker.py` → `taker_history.json` (144,666 taker fills pre-Sep-7)
- `weekly.py` → `weekly_activity.json` (per-week activity aggregates)
- `pnl_weekly.json` (weekly diffs of cumulative user-pnl)
- `counterparties.json` (top-30 makers/takers vs him), `classify_wallets.py` →
  `wallet_classification.json` (98 wallets), `leaderboard_month_volume.json`,
  `leaderboard_month_pnl.json`
- `fetch_gamma.py` → `gamma_events.json` (2,548 events with embedded markets, 4 games)
- `fetch_positions.py` → `positions_all.json`
- `profile.png`, `profile_activity.png`
