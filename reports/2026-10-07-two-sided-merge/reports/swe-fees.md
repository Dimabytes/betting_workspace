# swe-fees — Polymarket fee / rebate / merge mechanics + API verification of trader 0x6e2c
Status: FINAL

All amounts USD. "verified" = reproduced on live API / on-chain data during this run (2026-10-07).

## Summary

- **Taker fee (sports incl. Dota/CS2/LoL/Valorant): `fee = C × 0.05 × p × (1−p)`, USDC/pUSD-denominated, charged on buys (added to cost) and deducted on sells.** Verified to the cent on 81,396 of the trader's taker fills: `usdc_size − price·size` / `0.05·size·p·(1−p)` = 1.0000 at p10/p50/p90. Fee is *truncated* to 5 decimals per fill (docs: "rounded to 5 decimal places"; observed floor behavior, e.g. 0.02422875 → 0.02422). Makers pay zero on both sides (verified: maker fills' `usdc_size = p·q` exactly).
- **Maker rebate = 15% of the fee-equivalent of your own maker fills, paid once daily ~00:45 UTC in pUSD, $1 minimum.** Verified: trader's daily MAKER_REBATE payment / his previous-UTC-day maker fee base = 0.150 on essentially every day (28/30 days). Docs' per-market pooled formula `rebate = your_fee_equiv / total_fee_equiv × 15% × total_taker_fees` collapses to 15% × own fee-equivalent because every fill pairs one taker with one maker at the same fee base — **so our backtest's per-fill rebate model is correct, and "other makers do not dilute it" is true in practice.**
- **TAKER_REBATE = the Polymarket Tiers program** (live since 2026-05-28): a % of your own taker fees back, tiered by trailing-30-day weighted volume: Bronze 3% … Platinum 32% ($1M wV) … Diamond 44% ($4M) … Obsidian 50% ($10M), sports weight 1.0, paid daily ~00:10 UTC in pUSD, $1 min. Verified: his daily rebate / prior-day taker fees = **0.440 for Sep 8–11 fees, then 0.320 from Sep 12 on** (Diamond→Platinum downgrade). The one-off **$7,500 on Sep 8 = the Diamond level-up bonus**.
- **Merge**: batched `Safe.execTransaction` → adapter `0xa238cbeb…` → `CtfCollateralAdapter.mergePositions` → returns USDC.e which the adapter auto-wraps to **pUSD minted straight to the user's Safe in the same tx**. Immediately usable as CLOB collateral. His: 6,784 MERGE rows / 1,218 txs (≈5.6 markets batched per tx), min $1, p50 $531. data-api `MERGE` row = one market's merge leg (usdc_size = pairs merged).
- **Liquidity rewards on esports: none funded.** `rewardsMinSize=50`/`rewardsMaxSpread=4.5` exist on all esports markets but `clobRewards` is absent or ~$0.001/day — real rewards live on politics ($70–$1,053/day observed). Explains his reward_income $56 all-time.
- **PnL endpoint field semantics verified**: `maker_rebate`/`taker_rebate`/`fees_refunded`/`wallet_income` are **frozen since ~Sep 12** in `/v2/user-stats` and `/v2/user-pnl` (realized_pnl, fees, volume keep updating). `wallet_income = maker_rebate + taker_rebate + reward_income` (excludes fees_refunded). `volume` = shares (face $1); `volume_usdc` = USDC notional incl. fees.
- **Fee history**: fee-free → `sports_fees_v2` (rate 0.03, rebate 25%) ~Mar 2026 → `sports_fees_v3` (rate 0.05, rebate 15%) Jul 10 2026 (per-market in `market_terms.json`: 156 v2 / 692 v3 on the Jun–Oct cohort). Feb–Apr `fees_refunded` $27.4k is *not* a 1:1 refund of his own fees (he paid only $2.26k then) — likely a pilot-era maker-side fee credit; exact program name unverified.
- **Leaderboard**: all-time rank #400 PnL / #376 VOL; #12 by volume this week — but windowed PnL is an artifact (week −$153k, month −$3.39M) because merge/redeem proceeds aren't credited the way position PnL expects; only the all-time row is meaningful (≈ realized_pnl).
- **Collateral is pUSD** (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`, 6dp, USDC.e-backed): rebates, merges, rewards all pay/mint pUSD. His wallet holds ~37.3k pUSD, 0 USDC.e.
- Corrections to our backtest assumptions are in §7 — the big ones: **tick on Dota map markets is mostly 0.001 (not 0.01); fee schedule is per-market and was 0.03/25% for Mar–Jul 2026 data; taker rebate (32–50%) is a real, large, untracked income stream for a two-sided strategy that takes liquidity.**

## Findings

### 1. Fee schedule (verified)

Official: https://docs.polymarket.com/trading/fees and https://docs.polymarket.com/programs/maker-rebates (fetched 2026-10-07).

`fee = C × feeRate × p × (1 − p)`, symmetric in p, peak at p=0.5 ($1.25 per 100 sh at rate 0.05). Category table: Sports rate 0.05 / maker rebate 15%; Crypto 0.07/20%; Politics & Finance & Tech & Mentions 0.04/25%; Economics/Culture/Weather/Other 0.05/25%; Geopolitics fee-free. Makers never pay. Fee precision: 5 decimals, min 0.00001.

| leg | who pays/receives | formula | when | evidence |
|---|---|---|---|---|
| taker buy | taker pays | `fee = 0.05·q·p·(1−p)` added to `p·q` cost | at match | docs + exact on 81,396 fills (`analyze_activity.py`) |
| taker sell | taker pays | `fee = 0.05·q·p·(1−p)` deducted from `p·q` proceeds | at match | docs + sell fills |
| maker (any side) | pays 0 | — | — | maker fills `usdc_size = p·q` exactly |
| maker rebate | maker receives | `0.15 × 0.05·q·p·(1−p)` per own maker fill | daily ~00:45 UTC, pUSD, $1 min | docs + ratio 0.150 daily (`analyze_daily.py`) |
| taker rebate | taker receives | `tier% × 0.05·q·p·(1−p)` per own taker fill; tier = 3/8/18/32/44/50% by 30d weighted volume | daily ~00:10 UTC, pUSD, $1 min | docs + ratio 0.44→0.32 daily (`analyze_taker_rebate.py`) |
| merge | — | 1 YES + 1 NO → 1 pUSD; gas ~$0.05/batch | anytime pre-resolution | on-chain tx `0x0846a280…` |
| split | — | 1 pUSD → 1 YES + 1 NO | anytime | docs /trading/ctf/split |

Gamma `markets[]` fields on live markets (fetched via `https://gamma-api.polymarket.com/events?slug=…`, 2026-10-07):

| market (live) | slug | feesEnabled | feeSchedule | tick | minSize | negRisk | rewards |
|---|---|---|---|---|---|---|---|
| Dota Game 1 | dota2-synaps-legion1-2026-10-07-game1 | true | {exponent:1, rate:0.05, takerOnly:true, rebateRate:0.15} | 0.01 | 5 | false | min50/maxSpread4.5, no clobRewards |
| CS2 Map 2 | cs2-furia-9z-2026-10-07-game2 | true | same | 0.01 | 5 | false | same |
| LoL BO5 series | lol-est-kbm-2026-10-06 | true | same | 0.001 | 5 | false | same |

Notes:
- `feeSchedule.exponent: 1` is new vs the doc formula — exponent 1 ⇒ `p·(1−p)`; field exists for future asymmetric curves. `makerBaseFee`/`takerBaseFee` are a constant `1000` everywhere — legacy fields superseded by `feeSchedule`.
- Tick varies per market even within one event (CS2 game1 closed was 0.001, game2 live is 0.01; series market 0.001). Our archived Dota map markets: **4,820 at tick 0.001 vs 210 at 0.01** — mostly mill-tick (script `work/swe-fees` gamma dump scan). Series markets: 2,834 at 0.001 vs 238 at 0.01.
- `secondsDelay: 1` on every live esports market observed (Dota map/series, CS2 map/series, LoL map/series — `gamma_event_*.json`). 1-second in-play matching delay on incoming orders; our postprocess already notes it is not applied to post-only (maker) orders — it only matters for the taker legs of a two-sided strategy.
- Empirical on the trader's own fills: buy fee charged in USDC on top (`usdc_size = p·q + fee`), sell fee deducted from proceeds (`usdc_size = p·q − fee`), maker fills zero-fee both directions.

### 1b. Fee history (verified archive + docs; transition boundary likely)

| era | feeType (Gamma `feeType`) | rate | maker rebate | window (evidence) |
|---|---|---|---|---|
| fee-free | `none`/absent | 0 | — | through ~2026-02-17 (archive: no feesEnabled on earlier markets) |
| `sports_fees_v2` | sports fees on | 0.03 | 0.25 | markets from ~2026-03-10 through ~Jul 2026 (archive `tag_102366` dump; first v2 Dota market 2026-03-21 `dota2-l1ga-pi-2026-03-21`) |
| `sports_fees_v3` | sports fees current | 0.05 | 0.15 | dominant for markets trading Jul 10 2026 onward (per-market `market_terms.json` cohort: 156 v2 / 692 v3 for Jun–Oct matches) |

- Public timeline (web sources, 2026-10-07): sports taker fees introduced in selected categories ~2026-02-18 (rate 0.03, maker rebate 25%); all-categories fee expansion ~2026-03-30; sports schedule updated **2026-07-10** to 0.05/15%.
- Caveat (likely, not fully resolved): the archive is a snapshot — `feeType`/`feeSchedule` on a market record reflects the state at fetch time. The earliest v3-tagged archived market is dated 2026-05-20 (`dota2-tundra-xtreme-2026-05-22-game1-kill-over-45pt5`, a totals market), i.e. *before* the publicized Jul 10 switch — so either v3 rolled out to some categories early, or labels were touched on old records. For the backtest the per-market `market_terms.json` (captured 2026-10-02) is the right source: 156 of 848 Jun–Oct markets carry v2 terms (0.03/0.25), all of them Jun–Jul markets. Trading-window fees on those almost certainly were 0.03/0.25.
- `fees_refunded` (Feb–Apr, $27,371.03): not a 1:1 refund of his own taker fees — he paid only ≈$2,260 in that window (`user-pnl.json` increments). Likely the pilot-era maker-side fee credit (maker's fills credited with the taker fee they generated, the precursor of the 15% maker-rebate program) — the magnitude is consistent with him being mostly a maker then. Speculative on the exact program name; see Open questions.
- The 2026-04-28 "V2" exchange upgrade (https://help.polymarket.com/en/articles/14762452, fetched 2026-10-07) switched collateral USDC.e → pUSD and cleared all order books — approximately coincides with the last `fees_refunded` increment (~Apr 20).

### 2. Maker rebate (verified)

Docs: 15% of the market's daily taker-fee pool, distributed per market pro-rata to each maker's `fee_equivalent = C × 0.05 × p(1−p)`; paid daily in pUSD, $1 minimum.

Empirical (script `analyze_daily.py` on `activity_compact.json`): MAKER_REBATE rows land ~00:45 UTC daily; `paid(d) / Σ0.05·q·p(1−p) of his maker fills on day d−1` = **0.150 on 28/30 days** (boundary days off by partial-window effects). Sum implied $15,188.66 vs paid $14,906.37 (98.2%; the gap = Oct 7 fills not yet paid).

Pool vs per-fill equivalence: since every fill pairs exactly one taker fee with one maker fee-equivalent of identical size, Σ(maker fee-equiv) = Σ(taker fees) per market ⇒ each maker's share of the pool = his own fee-equiv share ⇒ rebate = 15% × own fee base exactly. Our per-fill model is right.

### 3. Taker rebate — Polymarket Tiers (verified)

Docs https://docs.polymarket.com/programs/taker-rebates: live since 2026-05-28; tier by trailing 30d *weighted* volume `wV = size_USD × (1−entry_price) × category_weight` (sports weight 1.0 — note the (1−p) upside multiplier favors cheap-side buys); tiers 3/8/18/32/44/50% of taker fees; daily ~midnight UTC payout in pUSD, $1 min; one-time level-up bonuses $10/$50/$250/$1.5k/$7.5k/$25k; applies to trades going forward only; tier recalcs daily, drops after grace period.

Empirical (`analyze_taker_rebate.py` + `.out.txt`): his daily TAKER_REBATE payment on day d / his taker fees on day d−1 = **0.440 on the Sep 9–12 payouts, exactly 0.320 on every payout Sep 13 → Oct 7** (`rebate 717.24 / fee 1630.10 = 0.440` on Sep 12; `734.83 / 2296.37 = 0.320` on Sep 13). The Sep 8 payment included **$7,500 = Diamond level-up bonus** + $426.61 regular rebate (≈50% of Sep-7 fees $848, or ≈44% of ~$970 if the first payout covered a partial window — first-day boundary is ambiguous; steady state is unambiguous). First `taker_rebate` increments in the pnl series appear Jul 27. No $25k Obsidian bonus observed.

Tier behavior inferred: he held Diamond (44%) early Sep, dropped to Platinum (32%) ~Sep 12 — consistent with the 30d rolling wV decaying under $4M at the daily recalc. At his volume (~$4.75M taker/30d), tier realistically sits Platinum–Diamond; at $10M+ wV Obsidian (50% of fees back) becomes relevant to sizing.

### 4. Merge mechanics (verified on-chain)

Path observed for his merges (tx `0x0846a280…fce9`, receipt via polygon public RPC):
Safe proxy `0x6e2c…` `execTransaction` (selector 0x6a761202), relayed by EOA `0xfa98256f…` (664k nonce — relayer or his hot key), inner target batch adapter `0xa238cbeb…` → per market: `CtfCollateralAdapter 0xAdA100Db00Ca00073811820692005400218FcE1f` → CTF `0x4D97DCd97…` mergePositions burns YES+NO → USDC.e out → deposited into pUSD contract `0xC011a7E…` (vault `0xc417fd8e…`) → **pUSD minted to his Safe** (Transfer 0x0→him). All in one tx: 8 market merges, 1.45M gas, 324 gwei ⇒ ~0.47 POL (~$0.05–0.10) paid by the relaying EOA.

- data-api `type: MERGE` row = one market's leg of a batched tx; `usdc_size` = `size` = pairs merged (1 YES + 1 NO → $1). His merges: 6,784 rows over 1,218 txs; min row $1.00; p50 $531; max $42.6k; spread through the day, multiple times per market (avg 3.7 merge rows/market) → continuous capital recycling, not end-of-match.
- Docs https://docs.polymarket.com/trading/positions/manage + /trading/ctf/split: split = pUSD → YES+NO (adapter, atomic); merge = reverse; redeem post-resolution; negRisk markets use `NegRiskCtfCollateralAdapter 0xadA2005600Dec949baf300f4C6120000bDB6eAab` (esports markets are negRisk=false → standard adapter). Gasless relayer path exists for UI users (Relayer/Builder keys); self-run = pay own Polygon gas (~$0.05/merge batch).
- Split exists and is usable by makers: useful if a strategy wants to sell one side without buying the other (we would not need it — buy-and-merge never requires split; split is how *makers* manufacture both legs at $1 to sell into rich bids).
- Merged pUSD is spendable on the CLOB immediately (same-block settlement; the balance sits in the Safe).
- pUSD = the collateral token since the **2026-04-28 V2 exchange upgrade** (https://docs.polymarket.com/concepts/pusd + https://help.polymarket.com/en/articles/14762452, fetched 2026-10-07): ERC-20, 6 decimals, 1:1 USDC-backed via `CollateralOnramp`/`CollateralOfframp`, vault `0xc417fd8e…`. Rebates, merges, rewards all settle in pUSD; withdraw unwraps to native USDC via Uniswap v3 (<10bp enforced).

### 5. Liquidity rewards (verified)

- Program exists on non-esports markets: politics markets observed with `clobRewards.rewardsDailyRate` $70–$1,053/day (USDC `0x2791bca1…` asset) and `holdingRewardsEnabled: true` on some.
- Esports markets: `rewardsMinSize=50`/`rewardsMaxSpread=4.5` present but `clobRewards` absent or `rewardsDailyRate: 0.001` ($0.001/day placeholder) — effectively zero. His all-time `reward_income` = $56.09 (4 increments Aug 2–31, max $50.75 — probably earned on a non-esports market like the "Bitcoin Up or Down" position still in his /positions).
- ⇒ "not modeled" in our backtest is correct and inconsequential.

### 6. API verification of the trader's numbers (all fetched fresh 2026-10-07 ~12:44–13:25 UTC)

| field | stored (11:44 UTC) | fresh (12:44 UTC) | semantics |
|---|---|---|---|
| realized_pnl | 495,685.49 | 495,892.80 | settled market PnL, live |
| trade_pnl | 824,176.24 | 825,160.68 | gross position PnL before fees |
| fees_paid | −328,668.71 | −329,105.83 | net taker fees paid (live) |
| fees | −356,039.74 | −356,476.85 | gross taker fees pre-refund; `fees_paid = fees + fees_refunded` (−356,039.74 + 27,371.03 = −328,668.71) |
| maker_rebate | 20,109.20 | 20,109.20 | **frozen ~Sep 12** |
| taker_rebate | 77,516.60 | 77,516.60 | **frozen ~Sep 12** |
| wallet_income | 97,681.89 | 97,681.89 | = maker + taker + reward income (excl. fees_refunded) |
| volume | 59.44M | 59.61M | shares (face value) |
| volume_usdc | 28.14M | 28.23M | USDC notional incl. fees |
| trade_count | 790,136 | 791,203 | fill rows (+1,067 in 1h — still trading hard) |
| positions | — | 500 rows (limit) | 483 REDEEMABLE + 17 OPEN; 10 mergeable rows = 5 markets holding both sides |

- `/v2/user-pnl` series: rebate increments mirror activity rows lagged ~1 day (activity Sep 8 $7,926.60 → series Sep 9 +$7,926.60) and **stop after Sep 12** while payments continue — endpoint/indexer accounting stopped, not the program (activity feed shows daily rows through Oct 7). True all-time maker+taker rebate ≈ 20,109 + (Sep 12→Oct 7 payments ≈ $14.6k) + 77,517 + (≈$23.9k) ≈ **$136k**, vs the endpoint's $97.7k.
- Leaderboard (`data-api.polymarket.com/v1/leaderboard?timePeriod=…&orderBy=…&user=…`): all-time PNL rank **#400** ($497.3k), VOL rank #376; week: VOL #12 ($11.77M) but PnL −$153k; month: VOL #43 ($19.7M), PnL −$3.39M. Windowed PnL is artifact-garbage for a buy-merge wallet (merges/redeems aren't credited as proceeds in the windowed math); only the all-time row ≈ realized_pnl.
- Profile `join_date` 1767803306 = **2026-01-07**; profile name `0x99a093771ad58bcfc3023cd75566415f`; `trades`=10,343 (profile counter, ≈ markets traded, ≠ fill count 791k).
- Positions flags: `mergeable:true` appears on both legs when holding both sides (e.g. FURIA/9z Map2: 2,002.8 YES @0.9072 + 2,219.6 NO @0.0951 ≈ 2,003 pairs mergeable); `redeemable:true` on settled winners awaiting redeem (483 rows — he leaves winners unredeemed). `entry_fees_usdc` is tracked per position ($744 total open entries). Note `/positions` (non-versioned) returns a different camelCase schema (`size`, `avgPrice`, `entryFeesUsdc`) — use `/v2/positions` for `mergeable`/`redeemable`/`current_size`.
- `volume` = shares (face $1), verified: his 7d traded shares 11,717,345 ≈ leaderboard week `vol` 11,766,783 (edge = my UTC window vs theirs). `volume_usdc` = USDC notional of fills.
- Profile page (`polymarket.com/@0x99a093771ad58bcfc3023cd75566415f`, via agent-browser 2026-10-07, rendered at `/es/` locale): displays **PnL +$825.2K** (= `trade_pnl` 825,160.68, gross — *not* realized), **volume 59.6M**, joined Jan 2026, largest win $9,765.32, 10,343 markets, and a **Platinum tier badge** — consistent with the measured 32% taker-rebate rate in effect since Sep 13.

### 7. Our live assumptions (postprocess.py:1-108) — verdict

| assumption | verdict | evidence |
|---|---|---|
| taker fee 0.05 × qty × p(1−p) | **confirmed** | docs + exact match on 81k taker fills (ratio 1.0000); truncation to 5dp per fill (immaterial: ≤$5e-6/fill) |
| maker rebate 0.15 × that on maker fills | **confirmed** | docs + daily payout = 0.150 × prior-day base exactly |
| daily payout, $1 min | **confirmed** | docs ($1 pUSD min); his rows ~00:45 UTC daily |
| "other makers don't dilute it" | **confirmed in effect** | pooled-per-market formula collapses to 15% × own base (see §2) |
| liquidity rewards not modeled | **confirmed safe** | no funded clobRewards on esports |
| **missing: taker rebate 32–50% on taker fills** | **gap, not a bug** | Tiers program — big upside for a two-sided bot that also takes; at $4M wV = 44% of fees back |
| tick 0.01 | **wrong-ish** | Dota map markets are mostly 0.001 tick (4820/5030 in archive); new live ones can be 0.01 — read `orderPriceMinTickSize` per market |
| constant fee params | **time-varying** | sports_fees_v2 = 0.03/25% for Mar 10–Jul 10 2026 markets; v3 = 0.05/15% since. `market_terms.json` already carries per-match terms (156 v2 / 692 v3 on the Jun–Oct cohort — every v2 market is Jun–Jul) and `run.py:491-500`/`:1063-1074` feeds it into fill enrichment via `research_fees.py:8-15`; `postprocess.py:179-189` prefers per-market fees when present. Verify the backtest run actually loads it |
| — | **stale label bug** | `scripts/check_gamma_trading_terms.py` EXPECTED_TERMS demands `fee_type="sports_fees_v2"` with rate 0.05/0.15 — but the archive encodes 0.05/0.15 markets as `sports_fees_v3` (v2 = 0.03/0.25). The substance (0.05/0.15) is right; the label will mismatch current dumps — rename expectation or drop the fee_type string from the equality |

## Open questions / what I could not verify

- Whether his merges ride Polymarket's free relayer or his own EOA (`0xfa98256f…` pays ~324 gwei gas; nonce 664k). Either way ≈ $0.05–0.10 per batch — negligible. No verified global min-per-merge or rate limit found; observed min row is exactly $1 (probably the CTF share-unit floor, not a documented limit).
- Whether the UI's merge path differs for Magic/email wallets vs browser-Safe users (docs describe the Relayer/Builder gasless path generally; I verified only his on-chain path).
- Exact Polymarket Tiers grace-period length for tier downgrades (docs say "a short grace period").
- Exact `fees_refunded` program semantics (Feb–Apr): refund exceeds his own fee outlay ~12× in that window, so it is not a reimbursement of his taker fees — most likely a pilot-era maker-side fee credit, but I found no public doc naming the program.
- Whether archived `feeType`/`feeSchedule` on markets resolved before Jul 10 were later rewritten (a May-20 market already carries the v3 label) — for Jun–Jul-window backtests trust `market_terms.json` per-market values, not the label timeline.
- Whether `exponent` in feeSchedule changes the formula anywhere (all observed = 1 ⇒ p(1−p)).
- Why the pnl indexer froze `maker_rebate`/`taker_rebate`/`wallet_income` fields ~Sep 12 (payments continue daily — verified in activity feed through Oct 7).

## Files

- `work/swe-fees/analyze_activity.py` + `.out.txt` — fee-formula reconstruction on 81,396 taker fills, maker-fill zero-fee check, merge-row stats (6,784 rows / 1,218 txs / p50 $531).
- `work/swe-fees/analyze_daily.py` + `.out.txt` — maker-rebate daily payout vs prior-day maker fee base (ratio 0.150); pnl-series freeze ~Sep 12.
- `work/swe-fees/analyze_taker_rebate.py` + `.out.txt` — TAKER_REBATE vs prior-day taker fees per day (0.440 Sep 9–12, 0.320 Sep 13→Oct 7), $7,500 bonus row.
- `work/swe-fees/stats-now-fresh.json`, `user-volume-fresh.json` — fresh `/v2/user-stats` and `/v2/user-volume` payloads (2026-10-07 ~12:44 UTC).
- `work/swe-fees/positions-fresh.json` (non-versioned, camelCase), `positions-v2-fresh.json` (v2 schema; 500 rows; 10 mergeable / 483 redeemable / 17 open).
- `work/swe-fees/gamma_event_{dota2-synaps-legion1-2026-10-07,cs2-furia-9z-2026-10-07,lol-est-kbm-2026-10-06}.json` — live-market feeSchedule/rewards/secondsDelay snapshots.
- Key ad-hoc commands (numbers quoted in report): Polygon RPC `eth_call balanceOf` on `0x6e2c…` → pUSD 37.3k, USDC.e 0; `eth_getTransactionReceipt`/`getTransactionByHash` on merge tx `0x0846a280…fce9` (1.45M gas, 324 gwei, relayed by `0xfa98256f…`, pUSD minted to Safe); `MAKER_REBATE`/`TAKER_REBATE` tx receipt decode → batch distributor paying pUSD `0xC011a7E…` to ~400 wallets/tx; gamma dump `feeType`/`feeSchedule` scan of `esports-trader/data/raw/polymarket_dota/universe/events/tag_102366_closed/*.json.gz` (v2: 3,205 markets @0.03/0.25; v3: 2,494 @0.05/0.15); tick scan over `universe.parquet` (map markets 4,820 @0.001 vs 210 @0.01; series 2,834 vs 238); `market_terms.json` cohort join vs `match_catalog.parquet` (848 matched; v2 markets all Jun–Jul).
