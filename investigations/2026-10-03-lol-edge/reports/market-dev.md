# market-dev: LoL backtest realism and PnL concentration
Status: FINAL
## Verdict (≤10 lines)
The LoL backtest is realistic: 198/200 sampled BUY fills reproduce through the
real Nautilus matching engine replayed from raw Telonex book deltas + onchain
trades (L2_MBP, queue_position, liquidity_consumption, trade_execution). The
two misses are post_only rejections at a boundary instant, not phantom fills.
LoL's problem is not the simulator — it is strategy-level adverse selection:
the top 5% of maps earn 184–224% of net PnL (3 seeds) while the bottom 95%
loses ~1× net, bleeding in the trade leg on positions unwound at ~14% loss,
and the per-map edge decays through the window (buy 300s markout 3.4c→1.2c)
exactly when Dota's accelerates (→5.5c; Sept = ~half of Dota's total PnL).

## Findings

### 1. Fill model is verified, not broken

Method: `engine_replay_batch.py` feeds the exact `telonex_parquet_book_snapshot_diff_rows`
delta stream + onchain trade ticks (corrected mirrored-aggressor `side`
semantics) into `OrderMatchingEngine` with the run's own config
(`FillModel()`, `PolymarketFeeModel`, `queue_position=True`,
`liquidity_consumption=True`, `trade_execution=True`, L2_MBP), submits a
post_only GTC limit BUY at the order's accept timestamp, and applies engine
events. n=200 stratified fills (50/month Jun-Sep + targeted cases), seed0
histfix run:

| class (analytic label) | n | engine reproduced |
|---|---:|---:|
| confirmed (prints cleared recorded queue) | 92 | 91 |
| delete_then_print/cross | 59 | 58 |
| through | 22 | 22 |
| print_only_partial_queue | 24 | 24 |
| front_of_queue | 2 | 2 |
| unexplained | 1 | 1 |
| **total** | **200** | **198** |

- The two failures (`order_status=REJECTED`) are post_only rejections where the
  single-order replay sees a one-event-staler book than the real accept instant
  — boundary artifacts, not tape-vs-sim divergence.
- Example validated: order `O-…-21` @0.65 — engine seeded queue ~88, sell
  prints 63+144+1252+50+198+286 cleared it, filled 197.71 at the exact print
  where the run recorded its fill.
- Earlier analytic labels like `print_only_partial_queue` were an artifact of
  comparing raw-snapshot level size to prints: the engine seeds queue_ahead
  from its own diff-book state at accept (which differs modestly from the
  nearest raw snapshot: `queue_ahead_rec == raw level` within ±1 share in only
  ~73.5% of sampled fills; understates in ~18.5%, overstates in ~8%). The
  engine's own
  seeded queue is what gets consumed — verified by reproduction.
- Order-of-operations subtlety that makes the mechanism work: on a SELLER
  print at price p, the engine transiently sets ask=p, so a resting BUY at p
  is marketable for that tick; `determine_trade_fill_qty` then gates by the
  tracked queue (per-order ahead, decremented by same-price aggressor
  opposite-side prints; cleared on level DELETE/CLEAR; excess converts to
  fillable size).

### 2. LoL PnL is far more concentrated than Dota's

Per-map engine_pnl, seed0 (3-seed ranges in parens — concentration is stable
to *worse* across seeds):

| | top1% | top5% | top10% | top25% | top50% |
|---|---:|---:|---:|---:|---:|
| LoL | 59% (59–75) | **184%** (184–224) | 261% (261–316) | 321% | 324% |
| Dota | 32% (32) | **93%** (90–93) | 131% (128–131) | 164% | 167% |

LoL's top 5% of maps earn ~2× the total; the bottom 95% is net-negative in
every month (and on every seed). Dota's bottom 95% is roughly breakeven (and
positive in September).

### 3. The bleed is a stable trade-leg loss on losers, worst in Aug-Sep

Losing maps fully unwind (pooled settle_cash ≈ 0; only ~5/362 carried a losing
position to settlement). Losses are (buy cost − sell proceeds): the strategy
recovers ~85.6% of buy cost on losers.

| month | LoL bot-95% PnL | LoL deep<-500 sum | Dota bot-95% PnL | Dota deep<-500 |
|---|---:|---:|---:|---:|
| 06 | −873 | −935 | +503 | −707 |
| 07 | −2,819 | −7,010 | −1,728 | −1,702 |
| 08 | −10,645 | −8,505 | −433 | −2,688 |
| 09 | −5,866 | −8,040 | **+5,391** | −520 |

Pooled: LoL losers −58.1k on 362 maps vs Dota losers −22.9k on 213 maps
(seed0). LoL has both more losing maps (45% of traded) and deeper tails
(worst −1913 vs −957; CVaR5% −597 vs −389).

### 4. Edge decays through the window for LoL, spikes for Dota

Quantity-weighted buy markout_300s by fill month (seed0):

| month | LoL wavg | LoL median | Dota wavg | Dota median |
|---|---:|---:|---:|---:|
| 06 | 3.43c | 4.50c | 4.74c | 6.00c |
| 07 | 2.15c | 3.50c | 2.18c | 3.50c |
| 08 | 2.14c | 3.50c | 2.58c | 1.00c |
| 09 | **1.17c** | 3.50c | **5.45c** | 5.50c |

PnL per map (engine_pnl/maps, 3-seed range): LoL 22.9–25.2 → 10.8–14.3 →
12.2–16.0; Dota 30–45 → 24–37 → **61.6–62.8** (Sept ≈ 52% of Dota's total).
Dota's September is The-International-adjacent flow; LoL Worlds (Oct–Nov)
is outside the validation window. The median LoL buy markout never drops —
the decay is entirely in the left tail (big fills going wrong).

### 5. Supporting numbers

- Book depth at entry: median queue_ahead 44 vs median fill qty 24; p75 level
  510 vs clip ≈$300. 60.5% of sampled fills had queue > fill size — the queue
  is usually the binding constraint, not raw depth.
- LoL tape is *denser* than Dota (~58k vs ~13k book snapshots/day sampled) —
  no evidence of a LoL-specific stale-book problem.
- Maker rebate = $4,426 of $30,346 net (14.6%) on s0 vs Dota $2,408 of $36,542
  (6.6%). Halving the rebate costs ~7% of LoL net — modest sensitivity.
- Capital: required_cash_with_reserves ≈ $2.8–3.4k, peak_reserved ≈ $3.9k,
  maps_at_once 4; roi_with_rebate 19.9% vs Dota 12.8% — LoL is more
  capital-efficient per deployed dollar but higher-variance.
- Sell exits are correct: sells are maker-join best-ask damage control
  (sell_30s markout −0.55c); a taker-exit counterfactual costs mean −16.2
  (LoL) / −53.5 (Dota) per episode — maker exits are not the problem.
- Fill rate 7.5% of submitted orders (13,271 BUY fills / 110,836 submits);
  post_only rejection rate 0.19% — normal for a join-style re-quoting maker.
- min_equity −44.8: per-match equity dipped below zero on s0 — capital
  headroom is thin at peak concurrency.

## What I ruled out

- **Phantom/sim-only fills** — earlier analytic classes
  (`print_only_partial_queue`, `unexplained`) all reproduce in the real engine.
  The earlier "unreproducible" verdicts came from replay-harness defects
  (fixed-point raw units ×100 vs ×1e9, aggressor labels computed pre-filter,
  and fills invisible without an `ExecEngine.process` event consumer).
- **Taker or synthetic liquidity** — `fill_model_mode=passive_book` uses only
  replayed book levels + real prints; no FillModel liquidity is injected.
- **Mirrored onchain fills corrupting queue accounting** — `onchain_side.py`
  writes a flipped `side` for sibling-token rows; the loader and forensics both
  use the corrected aggressor.
- **Queue resets every snapshot** — only the per-file first snapshot emits
  CLEAR; later snapshots are UPDATE/DELETE/ADD deltas; no flag-32 rows.
- **Price-key jitter causing implicit DELETE+ADD** — sampled price keys are
  stable 2-decimal strings.
- **strip_own_book as the explanation for odd fills** — unexplained-class
  fills were mostly `grid_v1` (unstripped) maps; engine replay reproduces them
  anyway.
- **Token/instrument miswire** — sibling-token replay is also consistent;
  `token_id_0/1` ordering matches the audit.
- **Book staleness as LoL-specific cause** — LoL snapshot cadence is higher
  than Dota's.
- **Maker exits as the leak** — taker-exit counterfactual is decisively worse.
- **Engine version drift** — nautilus_trader 1.226.0 is pinned; same wheel as
  the run.

## Proposed experiments

Ranked by expected PnL impact; each lists the exact confirmation test.

1. **Cut the bottom-95% trade-leg bleed — worth 84–124% of net PnL if
   eliminated (≈1× net across seeds).** The bleed is adverse selection: buys
   that decay unwind at ~14% haircut (loser sell-proceeds/buy-cost = 85.6%).
   a. Entry-quality floor: rerun histfix config with `min_delta` 0.02→0.03 and
      0.04 (or delta ≥ 1.5×spread). Confirm: bottom-95% monthly engine_pnl
      flips toward ≥0 while top-5% contribution retains ≥60% of baseline.
   b. Per-map adaptive stop: stop quoting buys on a map after 3 consecutive
      buy fills with markout_30s < 0 (per-episode kill). Confirm: loser
      count/severity drop in Aug–Sep without losing >$500 winners.
   c. Clip test: $300→$150 on all buys. Confirm: bottom-95% bleed halves; if
      net drops proportionally the bleed is volume-scaled, if it drops less
      the tail buys are disproportionately adverse.
2. **Re-check the Sept asymmetry (explains most of the LoL-vs-Dota gap).**
   Extend LoL validation through Oct–Nov 2026 (LoL Worlds play-ins/group
   stage) — if LoL per-map PnL spikes like Dota's Sept (TI), the gap is
   event-flow, not structural; if it stays ~15/map the edge is decaying and
   retraining cadence is the fix. Confirm: pnl_per_map by month on the
   extended window, plus buy markout_300s weighted mean.
3. **Per-map stop-loss.** Losers buy the same median quantity as winners
   (~1.18k shares), so the tail is not an over-sizing problem — it is an
   unwatched-position problem: deep<-500 maps cost −7.0k to −8.5k/month
   Jul–Sep while median loser severity is only ~−60. Add a hard per-map stop:
   force-unwind the episode when map-level marked-to-mid PnL < −300.
   Confirm: deep<-500 monthly sum shrinks ≥50% while >$500 winner count
   stays within ±10% (winners rarely transit −300 marked PnL — check that
   share of winners' min marked PnL below −300 is <5% first).
4. **Rebate robustness.** Rerun with maker rebate halved and zeroed.
   Confirm: net_pnl −7%/−15% respectively; if worse, the strategy's join
   placement needs to widen (post at −1 tick) to compensate.
5. **Capital headroom.** min_equity hit −44.8 on s0 — the reserve model can
   over-deploy at 4 concurrent maps. Confirm by stress run with
   maps_at_once=3: peak_reserved drops ~25%, ROI per reserved dollar should
   rise; reject if net drops >10%.

## Scripts

All under
`betting_workspace/investigations/2026-10-03-lol-edge/work/market-dev/`:

- `engine_replay_batch.py` — the decisive test: replays each sampled fill's
  exact book-delta + onchain-trade stream through a real
  `OrderMatchingEngine` (same config as the run) and records engine fills vs
  recorded fills. Output: `engine_replay_lol_s0.parquet`.
- `engine_replay.py` — single-fill version with verbose per-tick logging
  (used to develop/debug the harness).
- `fill_forensics.py` — per-fill book/print forensics → `forensics_lol_s0.parquet`
- `fill_window_scan.py` — full-window snapshot scan producing `label2` classes
  → `forensics2_lol_s0.parquet`
- `queue_replay.py` — standalone queue-accounting replay (superseded by the
  engine replay; its "unreproducible" results were tape-labeling artifacts)
- `concentration.py` — per-map PnL concentration (positive-mass Gini,
  top-x% share) LoL vs Dota
- `bigwins_lossdecomp.py` — monthly big-win/deep-loss counts and
  buy-cost/sell-proceeds/settlement decomposition of losers vs winners
- `sell_chase.py`, `sellside.py` — maker-chase vs taker-exit counterfactual
  per sell episode → `chase_*.parquet`
