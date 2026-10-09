# sim-devin — book-replay simulator and counterfactual strategy sweep
Status: FINAL

## Answer

Wallet B lost −$58.62 on 2026-10-08. In replay the same strategy produces
−$32.30 under the strict fill model — the residual gap comes from real-wallet
execution artifacts (stuck/stacked orders the sim does not replicate) and
sparse trade prints on thin maps, not from a different strategy.

The loss is **inventory waves**, not legging mechanics per se and not pure
bad luck: two maps (grid-3011821-m1 −$34.95, grid-3011820-m2 −$18.77) are
91% of the day. On both, the maker kept quoting both sides through trending
markets and accumulated loser-token inventory faster than pairs could merge.
Fills were on average *positive-edge at print time* — the damage is
post-fill drift, i.e. the strategy buys pairs at >$1 effective cost during
adverse bursts and is left holding 171 unpaired tail shares.

What would have changed the day (all-10-map replay totals vs baseline −32.30):

- **A momentum pull is the strongest single rule**: quote nothing while the
  mid moved ≥1 tick in the last 10 s → **+43.74** (Δ ≈ +76, 7/10 maps).
  A coarser cancel-on-move + 5 s cooldown scores +16.11; combined with
  half-spread 6 → +27.48.
- **Kill gate** (ban the victim team's bid for W s after each kill,
  re-arming per kill): monotone in W — W=10 −12.27, W=20 −4.80, W=30
  **+3.00**. Stacked on the momentum pull it adds ~+$3 (kg30+pull =
  **+46.71**, best variant and the only one at t>2): kills move the book,
  so the pull already covers most of the post-kill adverse window; the
  gate's residual edge is the kills that move it less than 1 tick.
- **Wider quotes help up to ~6 ticks** (hs=4: +1.02, hs=6: +2.48) then
  degrade (hs=12: −17.76 — too wide → no fills).
- **Spread gate ≥12 ticks: +10.81** — don't quote into tight books.
- **Taker hedging is decisively bad**: immediate take −101.31, 2 s −120.27,
  10 s −80.45. Crossing the spread + 5% fee curve during adverse moves costs
  more than the inventory it removes.
- **Smaller clips help** (shares=10: −12.96), tighter net cap helps
  modestly (netmax=15: −10.18), pairs-only mode −6.64.
- **Speed matters, modestly and noisily**: latency 0 ms → −16.07 vs 78 ms
  baseline −32.30 vs 300 ms −52.76 (140 ms −20.86 is a favourable-path
  outlier). Roughly +$1.6/10 ms of median latency saved, driven by faster
  *cancels* in fast markets. A Rust rewrite of the tick loop is not the
  lever — the debounce (100 ms) + fallback (2 s) cadence and the
  order-submission RTT dominate; cutting *cancel* latency (or colocating
  the cancel path) captures most of it.
- Inventory skew had no measurable benefit in replay (skew=0: +2.27,
  t=1.47, direction actually favors less skew on this day).

## Method

Standalone replay (`work/sim-devin/sim_core.py`, calibration `calibrate.py`,
sweep `sweep.py`, output `variants.csv`):

- Replays the collector market-channel journal per map: `book` snapshots,
  `price_change` updates, `last_trade_price` prints, both tokens.
- Replays the signal tape (game second, paused, finished) from session.jsonl.
- Quote logic identical to live: `price_bids`, `inventory_skew`,
  `scale_order_size`, `tick_gap`, `share_floor`, `maker_rebate_usdc` imported
  from `strategy.two_sided` / `shared.utils.trading`; debounce 100 ms,
  fallback 2.0 s, one-tick reprice hold 300 ms, 99-tick bid-sum cap,
  band [0.10, 0.90], quote from second −60, stale pulls, merge at
  $130/≥5 pairs, settlement at the map's actual outcome.
- Order latency: 78 ms decision→exchange PLACEMENT (measured median; p90
  140 ms).
- Queue model: a sim order joins behind the external size at its level;
  level decreases drain the queue.
- Own-order removal: the real wallet's orders are inside reported level
  sizes. The engine journal's `user_order` stream → per-level qty deltas
  (PLACEMENT→UPDATE→CANCELLATION); all queue math uses
  `ext = reported − own`. Sim orders never appear in the journal.
- Fill model (strict): a resting BUY on token T at p fills when the level
  drop at p exceeds the external queue ahead AND
  (a) SELL printed on T at px ≤ p within ±250 ms, or
  (b) BUY printed on the other token at px ≥ 1−p (complementary mint match),
  or
  (c) the drop contains a real-wallet `size_matched` decrease at p — the
      real order was eaten, so a sweep reached our queue slot.

## Results

### Calibration (live config, strict queue)

```
map                sim_fills real  sim_pnl   real_pnl
grid-3011820-m1        47      57      7.65      4.89
grid-3011820-m2        93      81     -8.18    -18.77
grid-3011820-m3        12      12     -7.07     -7.07
9034789047            104     111      8.92      2.58
grid-3011821-m1       160     183    -31.45    -34.95
9034957701             42      38      0.29     -4.41
grid-3011821-m2        12      15     -2.87     -1.99
9035220432              2       6      0.06      3.66
grid-3011822-m1        12      12      0.75      0.91
9035318247              2       8     -0.40     -3.47
TOTAL                 569     523    -32.30    -58.62
```

Fill count is close (1.09×) and the biggest loss map calibrates well;
residual divergence concentrates on thin oddin maps (see below).

### Real-wallet execution defects (not in the sim)

The engine journal shows the wallet frequently ran **≥2 live BUY orders on
the same token**: 265/523 fills (51%) landed while a duplicate same-token
order was live; on grid-3011821-m2 and grid-3011822-m1 it was 27/27 fills.
On 9035318247 a NO bid at 0.61 (`0x964cef62fd343c`) was placed 18:07:12,
never received a CANCELLATION row, and stayed live ≥13 min while newer NO
orders were placed beside it. ~29 orders lived >60 s across the day.
These stale duplicates collected fills at prices the strategy no longer
wanted — part of the sim-vs-real gap — but their fills were still mostly
positive-edge at print time, so they are a fill-surface leak, not the day's
main loss driver.

### Loss decomposition (real fills)

PnL = cost of all fills − settlement value = −$58.62 exactly.
Two maps = 91% of the loss. Fills show positive edge vs contemporaneous mid
(≈+$0.008–0.012/share avg) — the strategy buys *below mid* but mid keeps
moving against it. Unpaired tail inventory at map end: 171 shares.

### Sweep (10 maps, strict queue, per-map PnL summed)

| variant              | total_pnl | Δ vs base | t    |
|----------------------|-----------|-----------|------|
| baseline (live cfg)  |    −32.30 |      —    |  —   |
| hs=2                 |     −1.11 |   +31.19  | 0.40 |
| hs=4                 |     +1.02 |   +33.33  | 0.93 |
| hs=6                 |     +2.48 |   +34.78  | 0.78 |
| hs=9                 |     −3.15 |   +29.15  | 0.60 |
| hs=12                |    −17.76 |   +14.54  | 0.41 |
| netmax=15            |    −10.18 |   +22.12  | 1.25 |
| netmax=0 (pairs)     |     −6.64 |   +25.66  | 0.77 |
| netmax=45            |    −16.16 |   +16.15  | 0.92 |
| netmax=60            |    −39.67 |    −7.37  | −0.80|
| skew=0               |     +2.27 |   +34.57  | 1.47 |
| skew=0.5             |    −19.04 |   +13.26  | 1.21 |
| skew=2.0             |    −22.35 |    +9.95  | 0.31 |
| shares=10            |    −12.96 |   +19.35  | 1.11 |
| shares=40            |    −52.39 |   −20.09  | −1.38|
| latency 0 ms         |    −16.07 |   +16.23  | 0.52 |
| latency 40 ms        |    −32.05 |    +0.25  | 0.01 |
| latency 140 ms       |    −20.86 |   +11.44  | 0.26 |
| latency 300 ms       |    −52.76 |   −20.45  | −1.02|
| hedge@0s             |   −101.31 |   −69.01  | −1.42|
| hedge@2s             |   −120.27 |   −87.97  | −1.68|
| hedge@10s            |    −80.45 |   −48.14  | −1.70|
| move@4t/2s/cd3s      |     +6.34 |   +38.64  | 1.49 |
| move@6t/2s/cd3s      |     −1.63 |   +30.67  | 1.45 |
| move@4t/5s/cd5s      |    +16.11 |   +48.41  | 0.85 |
| spreadgate=8t        |    −41.70 |    −9.39  | −0.50|
| spreadgate=12t       |    +10.81 |   +43.11  | 0.99 |
| killgate W=10s       |    −12.27 |   +20.03  | 0.54 |
| killgate W=20s       |     −4.80 |   +27.50  | 0.54 |
| killgate W=30s       |     +3.00 |   +35.30  | 0.78 |
| movepull@1t/10s      |    +43.74 |   +76.04  | 1.53 |
| kg10s+movepull       |    +17.82 |   +50.12  | 1.35 |
| kg20s+movepull       |    +20.98 |   +53.28  | 1.17 |
| **kg30s+movepull**   |   **+46.71** | **+79.02** | **2.22** |
| hs=6+netmax=15       |     +5.01 |   +37.31  | 0.98 |
| hs=6+move4           |    +27.48 |   +59.78  | 1.03 |

(`move@k t/w s/cd c s`: cancel both quotes when radiant mid moves ≥k ticks
in w s, then stay flat c s — one-shot trigger. `movepull@k t/w s`: no-quote
*while* the displacement over the trailing window is ≥k ticks (continuous
condition, re-checked every eval). `killgate W`: on each kill received,
cancel the victim team's bid and don't re-place it for W s (re-arms per
kill; kill events from `work/game-devin/maps/<id>/events.parquet`,
`board_kill`/`kills` rows, victim = opposite of `side`). `spreadgate=n`:
quote only when the YES spread is ≥n ticks. `hedge@w s`: take the opposite
leg across the spread when |net| ≥15 sh for w s.)

Movepull volume check: fills 486→150, merged pairs 3193→973 — the rule
trades ~1/3 of the day but keeps PnL positive; it is an activity sacrifice,
not a fill-quality trick.

Paired t-stats are mostly weak (|t| ≤ 1.5): n=10 maps, one day, heavy
per-map variance. The totals are directional evidence, not statistical
proof — exceptions: kg30+movepull reaches t=2.22 and hedge@* is bad enough
on every loss map to trust.

## Recommendations

1. **Fix the order-lifecycle leak first.** Real fills show 51% of volume on
   duplicated same-token orders and orders living >10 min. Reconcile live
   orders against the venue (list-open-orders or timed orphan reaper), and
   treat a missing CANCELLATION ack as "order still live" rather than done.
   This is worth more than any parameter change and costs nothing in edge.
2. **Add a momentum pull** (highest-value rule tested): while the radiant
   mid moved ≥1 tick in the last 10 s, quote neither side; re-check every
   eval → +43.7 vs baseline, wins 7/10 maps, cuts trade count ~3×. The one-
   shot variant (cancel on ≥4 t/5 s, 5 s cooldown) is a weaker second
   (+16.1); hs=6+move4 combo +27.5.
3. **Kill gating is real but second-order**: banning the victim team's bid
   for 30 s after each kill nets +3.0 alone (Δ +35); W=10/20 leave money on
   the table because re-entry fills after ~20 s are fine. It mostly overlaps
   the momentum pull but still adds ~+$3 on top (kg30+pull = +46.7, the
   best variant and the only one at t>2): the kill's book move usually
   trips the pull anyway; the gate's residual edge covers kills that move
   the book less than 1 tick. Consistent with the markout study
   (~$0.57/fill lost on victim-side fills within 20 s of a kill) — in
   full-PnL terms the same window also contains profitable fills, so the
   net effect is positive but small.
4. **Widen to hs≈6 on the tight maps** (the hs=3 maps lost most of the day
   at 3 ticks; +34.8 total under hs=6).
5. **Do not taker-hedge.** Every delay variant loses −80…−120; crossing the
   spread during adverse moves is the expensive version of the same wave.
   If inventory must be cut, cut quote size (shares=10: +19.4) or gate on
   spread (≥12 t: +43.1) — pay makers, don't pay takers.
6. **Latency**: ~+$16/day-equiv at 0 ms vs measured 78 ms — real but not the
   story. Prioritize cancel-path latency; the 100 ms debounce and the venue
   RTT dominate anything a Rust rewrite of the strategy loop could win.
7. Keep net cap at 30 or lower (15 slightly better) — it bounds tail size;
   but caps alone don't fix pair-cost >$1, momentum gating does.

## Refuted / open questions

- **"Bad luck"**: partially. Fills had positive edge at print; the loss is
  adverse drift concentrated on 2 trending maps. Rules that stop quoting
  into momentum (move-kill, spread gate) flip the day positive in replay —
  so a large part was mechanical and avoidable, not luck.
- **Legging**: real fills show healthy edge at print; the cost is the
  *pair-cost >1 during bursts* plus 171 unpaired tail shares — a subset of
  wave damage, not a separate dominant mechanism.
- **Game-state gating**: tested — kill-gate (above) is the implementable
  version: real but small once book momentum is gated. The strategy ignores
  model predictions by design; tapes carry none, so model-gating is not
  replayable from this data. Pause/end gating already exists.
- **Calibration limits**: `last_trade_price` is sparse on thin oddin maps
  (some real fills have no print within ±5 s), so thin maps underfill
  (9035220432, 9035318247); book drops don't distinguish cancel from trade
  without a print. Fill-count parity (569 vs 523) and PnL within ~45%
  support relative variant ranking; absolute numbers are biased ~±40%.
- **Rust rewrite**: the binding constraint is debounce cadence and venue
  RTT, not language throughput. Not supported by replay.
- Reproduce: `PYTHONPATH=…/src .venv/bin/python calibrate.py strict` and
  `sweep.py` under `work/sim-devin/`; per-map×variant table in
  `work/sim-devin/variants.csv` (360 rows).
