# Orchestrator notes

## Own checks

### Weekly PnL of the trader (user-pnl.json, deltas of cumulative fields, ISO weeks)

Strategy starts week of 2026-07-26. Since Aug 2: realized $13k..$92k/week, volume_usdc $0.8M..$4.4M/week.
gross trade_pnl / volume = 3.5-4.0 c/$; fees_paid / volume = 1.5-1.9 c/$ (fees eat ~40% of gross). Rebates on top.
Weeks Sep 13 .. Oct 7 realized: 22.7 + 12.8 + 19.1 + 53.3 + 27.8 = ~$136k (+ part of the Sep 6 week).

### Approx cash PnL per game from markets_summary.json (merge + redeem - cost; ignores leftover positions and 22 sells)

| game, kind | n | cost $M | cash pnl $k | c/$ | leftover shares med | merged/pairs | dur med min | fills med | merges med | pair_avg med | share pair_avg>1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CS2 map | 1009 | 4.06 | 40.3 | 0.99 | 84 | 0.97 | 37 | 72 | 2 | 0.976 | 0.34 |
| CS2 series | 536 | 2.94 | 32.8 | 1.12 | 67 | 0.97 | 105 | 113 | 3 | 0.985 | 0.30 |
| LoL map | 329 | 1.01 | 12.7 | 1.26 | 36 | 0.83 | 27 | 208 | 1 | 0.977 | 0.27 |
| LoL series | 171 | 0.41 | 7.6 | 1.88 | 29 | 0.82 | 74 | 240 | 2 | 0.976 | 0.12 |
| Val map | 43 | 0.89 | 15.7 | 1.75 | 168 | 0.97 | 48 | 287 | 4 | 0.984 | 0.23 |
| Val series | 22 | 0.43 | 6.2 | 1.45 | 265 | 0.96 | 117 | 217 | 8 | 0.979 | 0.23 |

Total ~ $115k on $9.74M = 1.2 c/$ net of fees, before rebates ($52k = 0.5 c/$). => ~1.7 c/$ all-in, ~$165k / 30 days.
Our Follow300 live: ~3 c/$ on $112k per 5 weeks. His per-$ edge is LOWER than ours; his volume is ~100x ours. The game is flow capture + capital recycling, not a better signal.

### Risk profile (user-pnl.json daily realized deltas since 2026-07-28; activity_compact for capital)

- 72 days, 66 active (volume > $50k). Daily realized: mean $6.8k, median $3.5k, std $7.5k, min -$1,111, max $31.0k. Negative days 3/66.
- Cumulative realized $490.7k; max drawdown of the daily cumulative series: -$1,111 (0.2% of the cumulative). Daily mean/std 0.90 -> ~17 annualized.
- PnL per $ volume by day: p10 0.38c, p50 1.66c, p90 3.22c.
- Capital at risk = sum over in-window markets of max(0, cost - merged - redeemed), 1-min grid, 2,046 markets started inside the window: p50 $35k, p90 $57k, max $105k (2026-10-03). Early-window daily peaks $18-30k are the cleaner working-capital estimate; the later drift includes losing-side leftovers that never redeem (realized losses, not capital at risk).
- Per market (cost > $1k, n=1,147): peak unreturned capital p50 $1.8k, p90 $6.5k; turnover/peak p50 1.7x, p90 4.0x; cash PnL mean +$92, std $455, 32% negative, min -$5.7k; PnL / peak capital median +2.9%, p5 -13%.
- Read: ~$160k/month on ~$30-60k working capital with a $1.1k max drawdown. The edge per $ of volume is small; the edge per $ of capital and per unit of risk is enormous. This is the real reason to copy the mechanism: hedged round trips, tiny residual, flow-scaled.

### Re-checks of agent numbers

- grok-live Dota daily taker notional: my independent recompute (universe map_winner tokens, onchain_fills, mirrored=false, dedup tx/log/order) gives 2026-09-21 $6,032,753 vs agent $6,020,521; 2026-09-23 $3,064,435 vs $3,032,401. **Verified** (within 1%). `amount` is shares (row: amount 5 @ 0.49).
- grok-live "six-figure depth at the best level" on tier-1: **refuted wording**. spread_sample.json: tier-1 maps (BLAST Slam VIII Oct 1-3, NAVI/Liquid/LGD) have a 1c touch with $150-900 at best level; tier-2 maps 2-6c with $5-100. Median over 30 maps $120/$115 is right.

- sol-policy merge clock: my recount 6,784 merges / 1,218 tx, 79.6% of merge *events* at minute-second 5-10 (agent: 77.4% of *transactions*), batch p50/p90/max 5/11/12, BUY max price exactly 0.95 (2,087 fills), none above. **Verified.**
- Dota per-map taker notional, Sep 2026 (my duckdb, 452 maps, $47.24M): median $31k, mean $105k, p75 $104k, p90 $355k, max $920k (aur1-navi 09-26 g1). Top 20% of maps = 75% of flow; 181 maps >= $50k. The "$12-30k per map" in 00-context is a median-of-small-sample figure, superseded. Saved /tmp/dota_sep_market_notional.parquet.

- swe-theory: Feil & Nendel 2026 arXiv:2607.17991 **exists** (fetched abstract, title matches). Aggressor side in onchain_fills Sep 20-29 (non-mirrored, taker_side): buy 85% by count / 75.5% by $ (agent: ~84% of volume) — directionally **verified**, dollar share lower. Stale inputs in its §3 capacity paragraph (30 makers, $40 depth, 3t spread) come from 00-context; grok-live's fresh numbers (44-70 makers, $120 median best depth, 1-2c spread) supersede them → on tier-1 maps h is forced to 1t, where its own table needs f ≥ 0.4-0.6.

- swe-fees "tick on Dota map markets is mostly 0.001 (4,820/5,030 archived)": **refuted as stated**. Archived tick reflects the end-of-life state (Polymarket switches to 0.001 when price >0.96 / <0.04; closed markets sit at 0.999). Book check: aur1-navi 09-26 g1 mid-band (0.04-0.96) bid levels 95.6% on the cent grid, 4.4% sub-cent (stale levels from extreme phases); kalmy-lynx 09-23 g2 100% cent. In-play tick = 0.01. Live code should still read `orderPriceMinTickSize` per market.
- swe-fees pUSD finding cross-checked with poly-maker/merge.py (read-only): `_merge_safe` for non-negRisk calls CTF `mergePositions(USDC_COLLATERAL=0x2791…USDC.e)` directly → post-V2 (2026-04-28) that returns USDC.e to the Safe, which is **not** CLOB collateral (pUSD 0xC011a7E…). Trader's path: `CtfCollateralAdapter 0xAdA100Db00Ca00073811820692005400218FcE1f` merge+wrap in one tx. Live fix must redirect the merge target from esports-trader (wrap `_build_merge_call`/`_merge_safe` before Engine()); needs CTF setApprovalForAll(adapter) on the Safe + a manual $5 test. Confirms grok-live's BLOCKED flag with the exact reason.
- Taker rebate = Polymarket Tiers (3/8/18/32/44/50% of taker fees by 30d weighted volume). Trader at 44% → 32% since Sep 13 (Platinum badge on profile). We would be Bronze (3%): his taker legs cost him ~0.05·0.68 = 3.4% effective, ours 4.85%. Argues maker-only for our v1; tiers are a volume moat.

- sol-lol-micro markouts recomputed from its fills.parquet with the same cohort filters (BUY, chain-matched, own book ≤5s, both mids, target ≤30s): MAKER +10/30/60/300s = 0.081/0.169/0.345/0.463 c/share; TAKER 0.868/1.003/1.245/1.155 — **exact match**. Role split of LoL BUY cost $888,300 maker / $528,659 taker matches. Settlement attribution (478 known winners): maker +$8,884 (1.0 c/$), taker +$10,789 net of fees (2.0 c/$) → his taker leg is informed (buys token whose mid rose in prior 10-60 s, 63% positive prior move). Maker leg = entry capture ~0.5c below mid, adverse drift at 10-30 s, favorable by 60 s.

- grok-sim grid re-read from sim_grid.parquet: chosen h=3,g=2e-4,N=100,s=20 base +$342.97 / $12,465 / 2.75c, worst −$54.06, 23% neg; pessimistic +$219.48 / 2.01c; s=50 +$813 / 2.52c; h=1 +$11.8 (base) / −$87 (pess). **Verified.** Extra: at h=1 the phase split is 0–8 min **+$88**, 8–20 −$6, 20+ **−$200** on $42.7k bought → tight quoting is fine early (1.7% jump rate) and bleeds late (8.5%). Phase/vol-dependent h and model-fair offset were not run — first thing for the in-repo backtest.

- swe-history "gamma volumeNum equals on-chain USDC notional exactly": **refuted for Dota**. Joined 451 Sep Dota map markets by conditionId: Gamma $91.8M vs my on-chain taker notional $47.0M, ratio median 1.83 (p10 1.60, p90 2.27). Gamma volume ≈ share/face units (both legs of mint-matches), not USDC. So swe-history's $161M Dota / $289M CS2 / $323M LoL are inflated ~1.9× but comparable across games; use on-chain figures for capacity ($47.2M Dota maps in Sep). Everything else in swe-history is history/API work I accept: zero Dota fills all-time, era 1 = crypto fee-refund farm, era 2 ramp $175k→$3.6M/wk in 2.5 weeks, SELLs stopped Jul 30 when merging started, 25/98 similar wallets (19 on Dota), activity feed under-indexes pre-Sep-7 redemptions (~$14M gap) → never compute PnL from activity cash flow for old windows.

- Experiment zero-b (mine): extended `work/grok-sim/sim.py` with backward-compatible cfg keys `h_mid`, `h_late`, `c_jump` (jump = |Δmid| ≥ 5 ticks within 10 s → +c_jump for 120 s); runner `work/orchestrator/phase_h.py` (main / extra). Flat controls reproduce grok-sim's grid to 1e-6. Best total: phase 1/2/3 + jump+1 → s=20 +$461 / 3.16c / worst −$49 / 17% neg; s=50 +$1,173 / 3.16c; pessimistic s=50 +$998 / 3.07c (vs h3 flat +$813 / +$694). h1 + jump+3 ≈ same dollars with more volume. Jump adder, not phase, is the lever. Results: `phase_h_results_{main,extra}.parquet`, `phase_h_summary_*.csv`.

- Control B gap (mine, `reports/control-b-gap.md`): sim.py got ablation flags `floor_px`, `fair_pair`, `band_hi`, `band_both`, `shrink_add`, `nautilus_fill`, `through_cap`, plus `fill_log`; base grid unchanged (self_check ok, 16-map base = 261.68). Nautilus fill semantics alone: 262 → 105 (through-print blocked while displayed queue > 0; qty capped at print size). Price-priority cap only: 240 (sim's full-fill optimism ≈ $20). Band one-sided: leftover −15 → −61. Engine-only: order life 0.34 s median, 29% cancel-before-accept, 15% same-price resubmits, 42% of ±1-tick reprices revert, ~1,000 s/map no-order gaps, stale_signal 8% of time at k=0. FIFO replay of engine fills reproduces engine_pnl + rebate (27.23).

- Control B2 (user's re-run after fixes 1–4, `twosided-control-b2`): +$89.72 + $20.09 rebate = $109.81 on $6,168, 1,033 fills, mean qty 12.5. Per-map corr with sim nautilus_fill 0.89 / truth 0.88. Churn: 16,508 orders (was 27,719), median rest 1.19 s, cancel-before-accept 0%, same-price resubmits 825, ±1-tick reprices 9,546, no-order gap 588 s/map. no_quote band 9,627 s (both-leg [0.10,0.90] pulls ~25% of time), stale_signal 117 s (was 3,364). 71% of fills after min 20. markout_30s +1.1 t mean (YES −0.5, NO +2.7). Board fills ≤10 s: 29%, 300 s markout −0.19¢ → kill-gate target. Sim `front_queue` (= queue_position=False) gives $360 / $8,975 on these maps: bracket for the engine.

- Wave 1 (user's 72 runs, 20 maps, `validation_join_delta02_x015_cut480_p45_ts-h{h}-k{k}-j{j}-kill{on,off}-q{on,off}`): aggregated to `wave1_cells.parquet` / `wave1_permap.parquet` / `wave1_buymap.parquet`. Marginal means strict queue: h 1/2/3 → $77/$267/$255 at 0.5/2.6/4.0¢; k 0/0.5/1 → $115/$256/$228 with 9.8/3.2/4.3 neg maps; jump 0/1 → $160/$240 (volume −37%); kill off/on → $190/$210 (but top cells: kill on −$10…+$4, free queue −$35). Best-map share 11–12% in all top cells (no outlier). PnL split (strict, h3 kill off, leftover cost at side avg price): k=0 pairs +$302 / leftover −$116; k=0.5 pairs +$92 / leftover +$237; k=1 pairs −$10 / leftover +$352; free queue k=0.5 pairs +$316 / leftover +$222. Leftover shares that pay $1: 21% at k=0, 83–86% at k>0. Paired tests: k0.5−k0 +$143 t 1.42 (15/20); k1−k0.5 +$14 t 0.20; h2j1−h3 −$3 t −0.16; kill on−off +$4 t 0.22 (h3) / −$10 t −0.64 (h2j1).

- Full archive runs (user, 307 maps): strict +$1,682 / free +$3,827; by Sep flow quartile small maps lose. $300-clip control: −$30.9k, all leftover. Follow300 same maps +$17,690, per-map corr −0.02. Scripts: `decompose_two_sided.py` (pairs/leftover table), runbook `RUNBOOK-wave2.md`.
- Merge probe (`esports-trader/scripts/merge_probe.py`, read-only run 2026-10-07 23:10 CET): Safe `0x941Aa…24D9` holds 5,406.67 pUSD, `CtfCollateralAdapter` already approved on CTF (`isApprovedForAll` True). poly-maker's `_merge_safe` targets CTF directly with USDC.e — wrong contract for pUSD markets; the probe routes `adapter.mergePositions(pUSD, 0, cond, [1,2], amt)` through the same Safe signing by overriding `_inner_merge_call` on the instance (no poly-maker edit). Blocker for `--send`: owner EOA `0xB01846C8c38333153158d5dF0BAEF06494ec8E7b` has **0 POL**; gas at 256 gwei × 600k gas × 2 ≈ 0.31 POL max. Target market for the test: `cs2-isg-meiano-2026-10-07` (CTF v1, tick 0.01, min 5; CS2 so the live Dota bot's ledger is untouched).

### Key open questions for the plan

1. Dota flow capacity: total taker notional per map / per week (grok-sim A, grok-live B, swe-history 5).
2. Is his taker leg informed (positive markout) or hedging (sol-lol-micro 3)?
3. His effective quoted spread and placement (sol-lol-micro 1-2).
4. Merge threshold / cadence (sol-policy 1).
5. Sim: does naive two-sided MM make money on Dota tapes at all (grok-sim B)?
6. Live blockers: Safe merge, dual inventory, plan shape (grok-live A).

## Fleet

Tab w1:tKM. Panes in panes.txt. Launched 2026-10-07 ~15:25 local. Watchers running (DONE when `Status: FINAL`).
