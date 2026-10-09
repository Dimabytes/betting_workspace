# sim-grok — book-replay simulator and strategy sweep
Status: FINAL

## Answer
- The replay does not reproduce today's loss, so the sweep dollars are not a forecast. Strict-queue sim PnL for the live config is **+$14.62** across 10 maps. The same settlement on the real fills is **−$45.53** (telegram was −$42.08). Tolerance I set before trusting a sweep: share count within 35% of live and |PnL error| ≤ $8 on at least 7 maps. Two maps meet it. `verified` on the totals, `likely` on the cause (fill model, below).
- One map does calibrate. LGD–Yandex m1 strict PnL is $5.95 vs live $5.94, with shares 18% high. Aurora–1win m1 is the miss that matters: sim +$0.01 vs live −$30.18, on about 1.9× the shares. `verified`
- Place latency, measured here from `orders_out` local time to PLACEMENT exchange time: **median 77.8 ms, p90 139 ms, n=10,626** (53 unmatched). That matches the other agent's 78 / 140. Debounce is 100 ms and the fallback timer is 2.0 s (`config_b/trading.toml` lines 7–8, read in `two_sided_worker.py` 105–110). `verified`
- Taker-hedge loses in both queue modes on every map that traded. Flattening as soon as |net| ≥ 5 (walk the ask, pay the fee) is **−$437 strict / −$439 free** (−$44 per map). Waiting 5 s is still **−$207 / −$184**, paired p = 0.018 and 0.028. Fees are only part of it ($79 of the 5-second loss). `verified` inside the sim. Live would hedge less often because live fills less, so the dollar loss is overstated; the sign is the result. `likely`
- The only gain that is positive in both queue modes and under p=0.10 on both is **pairs only** (after one leg fills, quote only the other): **+$3.0 per map strict (p=0.069), +$5.5 free (p=0.048)**. Six and seven maps are up. The range on strict is −$4.70 to +$9.41, not one map. `verified` as a sim result. Not verified as live P&L, because the sim never takes today's −$46.
- Killing the victim's bid for 10/20/30 s does not clear a paired test. W=20 is +$38 strict / +$49 free, p=0.35 / 0.20, and Aurora is +$32 of the strict total. Adding "pull both bids while the mid moved ≥ 1 tick in 10 s" still has p>0.4, and it deletes most of the fills. The 0.57-per-fill markout does not show up as map PnL here. `verified` in the sim, `speculative` as a live rule.
- Nothing else holds in both queue modes with p<0.10. Tighter half-spread (1 tick) loses on the strict queue (p=0.015) and is noise on the free queue (p=0.94), so it is not a result. Stronger skew, wave-cancels, and smaller clip size do not make money in both modes.

## Method
- Tape: `work/data/book_journal_20261008_liveb.jsonl.gz`, split per map by `work/sim-grok/prepare.py` into `work/sim-grok/cache/<match_id>.npz`. Bids and asks are rebuilt from `book` snapshots and `price_change`. Trades are `last_trade_price`.
- Quotes are not retyped. Each decision calls `two_sided_pull_reason`, `desired_bids`, and `hold_one_tick_moves` (`esports-trader/src/strategy/two_sided_quoting.py`). Fair is `core_book_p`. Pulls used: pause and game-end from `session.jsonl` (`game_snapshot.paused`, phase `finished`), band, quote start at game second −60, stale/missing book, pair sum outside 0.05. Pre-match and pre-horn are not treated as paused unless the feed says so.
- Clock: decide at most every 100 ms, and on a pause/end flag change. Place and cancel take effect 78 ms later (the measured median, 77,767 µs). A bid that would cross the ask is rejected.
- Fills: a resting BUY at price p fills on a same-token SELL at ≤ p, or a BUY of the other token at ≥ 1−p, and only if our price is between the touch and that print. Strict queue: size ahead when we join, trades take the front, cancels shrink the queue in proportion (not all cancels are assumed to be ahead of us). A print strictly through our level fills the rest of the order. Free queue: first exact print fills `min(remaining, trade size)`, ignoring the queue. Through still fills the rest.
- Our resting size is taken out of the level using per-order PLACEMENT / UPDATE / CANCELLATION in `wallet/engine_journal/live.jsonl`. The tape still contains the prints that filled the real bot, so a sim quote at the same price can fill on our own historical prints. Quotes at a different price do not. That biases calibration toward the live prices and biases other variants by an unknown amount.
- PnL is cash, not markout: −(price × shares) + $1 per YES+NO pair + the unpaired tail at 0 or 1 + maker rebate − taker fee. Rebate and fee use `maker_rebate_usdc` and `calculate_taker_fee_per_share` in `src/shared/utils/trading.py`. Grid winners come from `match.json`. Oddin winners come from the last signal mid (YES won only if that mid ≥ 0.5). LEGION–Blaster m2's last mid is 0.006, so the YES tail loses; that settlement is −$3.23 vs telegram −$3.12.
- Hedge variant: when |radiant − dire| has stayed ≥ X for T seconds, buy enough of the short token at the ask to flatten, walking every level, at decision+78 ms. Kill variant: `work/game-devin/maps/<id>/events.parquet`, kinds `board_kill` and `kills`. `side` is the killer; the other side is the victim. `r`/`d` map through `yes_is_radiant`. Cancel the victim bid at receive time + 78 ms and do not replace it for W seconds; a new kill resets W. Momentum: while |mid now − mid 10 s ago| ≥ 1 tick, pull both bids.
- Sweep is one change at a time versus that map's live half-spread (3 or 6), net 30, skew 2e-4, size 20. Both queue modes. Paired t is `scipy.stats.ttest_rel` over the 10 maps. A result is called real only if the total moves the same way in both modes; p-values are reported beside that. Script: `work/sim-grok/sim.py`. Rows: `work/sim-grok/variants.csv` (1,360 = 68 variants × 2 modes × 10 maps).

## Results

### Calibration (strict queue vs real fills)

| map | sim YES | live YES | sim NO | live NO | sim PnL | live PnL | error |
|---|---:|---:|---:|---:|---:|---:|---:|
| LGD–Yandex m1 | 492 | 419 | 508 | 418 | +5.95 | +5.94 | +0.01 |
| LGD–Yandex m2 | 1018 | 625 | 1014 | 617 | +1.38 | −16.65 | +18.03 |
| LGD–Yandex m3 | 102 | 100 | 100 | 127 | +0.70 | −6.71 | +7.41 |
| Synapse–Blaster m1 | 1324 | 925 | 1311 | 905 | −7.42 | +5.44 | −12.86 |
| Aurora–1win m1 | 2693 | 1418 | 2719 | 1444 | +0.01 | −30.18 | +30.19 |
| Synapse–Blaster m2 | 805 | 294 | 783 | 272 | −4.76 | −3.44 | −1.32 |
| Aurora–1win m2 | 80 | 100 | 106 | 126 | +2.90 | −1.67 | +4.57 |
| Blaster–LEGION m1 | 0 | 47 | 0 | 60 | 0.00 | +3.80 | −3.80 |
| PARI–Yandex m1 | 40 | 100 | 42 | 107 | +3.04 | +1.18 | +1.86 |
| LEGION–Blaster m2 | 0 | 82 | 20 | 60 | +12.83 | −3.23 | +16.06 |
| **total** | | | | | **+14.62** | **−45.53** | **+60.15** |

Free-queue base total is +$12.64. Busy maps over-fill; the two quiet Oddin maps under-fill (0 and 20 shares vs 107 and 142). `verified`

### What agrees in both queue modes

Deltas are versus the live config inside the sim, summed over 10 maps. p is the paired test. n+ is how many maps improved.

| variant | strict Δ | free Δ | $/map strict | p strict | p free | n+ strict | strict range |
|---|---:|---:|---:|---:|---:|---:|---|
| net max 0 (pairs only) | +29.75 | +55.45 | +2.97 | 0.069 | 0.048 | 6 | −4.70 .. +9.41 |
| net max 10 | +12.88 | +50.16 | +1.29 | 0.26 | 0.018 | 5 | −4.70 .. +5.63 |
| quote only if spread ≥ 4 | +38.58 | +38.60 | +3.86 | 0.29 | 0.30 | 5 | −12.83 .. +19.60 |
| quote only if spread ≥ 3 | +34.48 | +57.23 | +3.45 | 0.10 | 0.015 | 6 | −5.46 .. +14.50 |
| kill victim 20s | +37.96 | +49.24 | +3.80 | 0.35 | 0.20 | 4 | −6.98 .. +32.33 |
| kill victim 30s | +35.60 | +42.63 | +3.56 | 0.37 | 0.23 | 4 | −6.43 .. +27.72 |
| kill 20s + mid-move pull | +31.01 | +25.35 | +3.10 | 0.42 | 0.63 | 6 | −12.83 .. +24.47 |
| half spread 1 tick | −278.72 | +8.65 | −27.87 | 0.015 | 0.94 | 0 | disagrees across modes |
| half spread 10 | −27.62 | −25.63 | −2.76 | 0.18 | 0.18 | 3 | −14.44 .. +5.88 |
| skew ×25 | −52.64 | −56.25 | −5.26 | 0.056 | 0.18 | 2 | −16.26 .. +6.95 |
| size 10 | −22.68 | −17.49 | −2.27 | 0.16 | 0.42 | 2 | −9.67 .. +7.64 |
| size 5 | +0.00 | +9.19 | 0.00 | 1.00 | 0.72 | 4 | −9.63 .. +9.17 |
| hedge \|net\|≥5, T=0 | −436.95 | −439.00 | −43.69 | 0.065 | 0.072 | 0 | −213.54 .. 0 |
| hedge \|net\|≥5, T=5 | −207.32 | −184.30 | −20.73 | 0.018 | 0.028 | 0 | (all traded maps down) |
| hedge \|net\|≥5, T=60 | −68.53 | −33.68 | −6.85 | 0.024 | 0.14 | 1 | −19.56 .. +5.00 |
| wave K=2 W=3s cool 30s | +38.45 | +33.58 | +3.84 | 0.53 | 0.56 | 4 | −15.2 .. +39.5 |

No wave (K, W, cooldown) triple has p<0.10 in both modes. No skew multiple is positive in both modes. `verified`

Hedge T=0, strict, buys **16,270 shares as taker** and pays **$173** in fees. The rest of the −$437 is the price of walking the ask. Aurora is −$214 of that. The other nine maps are still about −$223. `verified`

### Best 3 by dollars (positive in both modes)

These are the three largest `min(strict Δ, free Δ)`. They are not significant. Per-map sim PnL, strict / free.

**Spread ≥ 4 ticks** (strict total +$52.20 vs base +$14.62). Aurora is +$19.60 of the +$38.58 delta. LEGION–Blaster m2 is −$12.83 because the filter skips the one winning fill.

| map | strict | free | Δ strict |
|---|---:|---:|---:|
| LGD–Yandex m1 | +10.10 | +9.37 | +4.16 |
| LGD–Yandex m2 | −8.31 | −3.79 | −9.69 |
| LGD–Yandex m3 | 0.00 | −2.58 | −0.70 |
| Synapse–Blaster m1 | +10.34 | +5.54 | +17.76 |
| Aurora–1win m1 | +19.61 | +29.16 | +19.60 |
| Synapse–Blaster m2 | +9.01 | +3.43 | +13.78 |
| Aurora–1win m2 | +10.40 | +8.06 | +7.50 |
| Blaster–LEGION m1 | 0.00 | 0.00 | 0.00 |
| PARI–Yandex m1 | +2.05 | +2.05 | −0.98 |
| LEGION–Blaster m2 | 0.00 | 0.00 | −12.83 |

**Kill victim bid 20 s** (strict +$52.58). Aurora +$32.33 and LGD m2 +$17.01. Without Aurora the strict delta is +$5.63.

| map | strict | free | Δ strict |
|---|---:|---:|---:|
| LGD–Yandex m1 | +3.66 | +5.90 | −2.28 |
| LGD–Yandex m2 | +18.39 | +18.33 | +17.01 |
| LGD–Yandex m3 | −1.21 | −3.06 | −1.91 |
| Synapse–Blaster m1 | −14.40 | −17.33 | −6.98 |
| Aurora–1win m1 | +32.34 | +36.30 | +32.33 |
| Synapse–Blaster m2 | −0.22 | +9.28 | +4.54 |
| Aurora–1win m2 | +4.30 | +2.74 | +1.40 |
| Blaster–LEGION m1 | 0.00 | 0.00 | 0.00 |
| PARI–Yandex m1 | −3.11 | −3.11 | −6.15 |
| LEGION–Blaster m2 | +12.83 | +12.83 | 0.00 |

**Kill victim bid 30 s** is the same shape (strict Δ +$35.60, Aurora +$27.72, LGD m2 +$23.45, p=0.37). W=10 is smaller (+$21 / +$30, p=0.33 / 0.18).

### Kill gate plus the 1-tick / 10 s pull

Full-map PnL, not markout. Strict Δ vs base:

| rule | total Δ strict | total Δ free | p strict | p free | what it does to fills |
|---|---:|---:|---:|---:|---|
| kill 10s | +21.10 | +30.38 | 0.33 | 0.18 | modest cut |
| kill 20s | +37.96 | +49.24 | 0.35 | 0.20 | Aurora and LGD m2 only |
| kill 30s | +35.60 | +42.63 | 0.37 | 0.23 | same two maps |
| kill 10s + momentum | +31.75 | +23.86 | 0.40 | 0.64 | Aurora **−$12**; most maps drop to ~20 fills |
| kill 20s + momentum | +31.01 | +25.35 | 0.42 | 0.63 | same |
| kill 30s + momentum | +30.11 | +19.73 | 0.44 | 0.72 | same |

The momentum pull is on almost whenever the mid is moving, which is most of a map. It is not a small overlay on the kill rule. `verified`

### Pairs only, per map (the one result with p<0.10 in both modes)

| map | strict PnL | free PnL | Δ strict | Δ free |
|---|---:|---:|---:|---:|
| LGD–Yandex m1 | +8.73 | +14.92 | +2.78 | +2.85 |
| LGD–Yandex m2 | +8.23 | +13.46 | +6.85 | +12.42 |
| LGD–Yandex m3 | +1.76 | +0.10 | +1.06 | +5.11 |
| Synapse–Blaster m1 | +1.99 | +8.20 | +9.41 | +24.33 |
| Aurora–1win m1 | +7.97 | +8.13 | +7.96 | +1.47 |
| Synapse–Blaster m2 | +1.96 | +5.68 | +6.72 | +6.91 |
| Aurora–1win m2 | −1.80 | +2.06 | −4.70 | +2.69 |
| Blaster–LEGION m1 | 0.00 | 0.00 | 0.00 | 0.00 |
| PARI–Yandex m1 | +2.71 | +2.71 | −0.33 | −0.33 |
| LEGION–Blaster m2 | +12.83 | +12.83 | 0.00 | 0.00 |

## Recommendations
Ranked by what this replay says you would make per map. Confidence is about the sign surviving both fill models and the calibration gap, not about the cents.

1. **Do not taker-hedge the missing leg.** Immediate flatten is about −$44 per map here; a 5 s wait is about −$21; a 60 s wait is about −$7 and is significant only on the strict queue (free p=0.14). Confidence high that lifting the ask to get flat loses money. The sim hedges too many shares, so do not budget −$44 as the live number.
2. **Quote only the missing leg after a fill (net max 0).** About +$3 per map strict, +$5.50 free. Confidence low-medium. It is the only positive rule with paired p<0.10 in both modes, and the per-map deltas sit between −$5 and +$9. It does not recover the −$46 the sim failed to model.
3. **Leave the kill gate off until the fill model matches live losses.** Point estimate +$2 to +$4 per map, confidence low. W=20 and W=30 are two maps. The momentum pull on top does not fix that.
4. **Do not tighten the half spread to 1–2 ticks, and do not multiply skew by 5–25.** Half-spread 1 loses in the strict model and is a coin flip in the free model, so it is not established, but there is no version that makes money in both. Wider (8–10 ticks) is mildly negative and not significant.
5. **Spread filter and wave-cancel are not established.** Spread ≥ 3 is the least bad of them (strict p=0.10, free p=0.015) and still fails the both-modes p<0.10 bar. Wave settings move a few dollars with p>0.5.

## Refuted / open questions
- Refuted inside this replay: fast taker hedge makes money; stronger skew makes money; a 1-tick half spread makes money on the strict queue. `verified` in the sim only.
- Not established: kill-gate, momentum pull, wave cancel, spread filter, clip size 5 or 10, half spread 4–10. Same-sign totals exist for some of them. Paired tests do not.
- Open: the sim's day is +$15 and the real day is −$46. Until a fill rule reproduces Aurora −$30 and LGD m2 −$17, a rule that "makes $3" here can still lose live. The likely hole is who gets the toxic print: we join the back and let cancels shrink the queue, and a complement print through our price fills the whole order. That fills too often on busy books and misses the quiet ones. `likely`
- Open: hedge of one clip (5 or 20 shares) instead of flattening the whole net. This run walks the book until flat, as specified. A smaller take would lose less and might still lose.
- Open: Blaster–LEGION m1 had 6 live fills and 0 sim fills. The tape has trades, and orders were resting, but no print met the touch-to-price test. That map cannot tell variants apart.
