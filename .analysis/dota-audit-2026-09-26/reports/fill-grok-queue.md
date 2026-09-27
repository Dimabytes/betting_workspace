# fill-grok-queue — honest queue vs backtest PnL
Status: FINAL

## Summary

Engine PnL on seeds 0–2 is $2,848.59, $3,338.76, and $3,320.89. Ignoring the fill snapshot as a cancel leaves $119.75, $39.79, and −$515.12. That rule is too strict. In the tape without our order, an ask can sit on our bid only if that bid level was consumed or cancelled in the same snapshot. Locked books, where both sides still have size at one price, are 0.077% of paid-Telonex two-sided snapshots and 0.039% of collector ones (`reports/data-luna-followup.md`). A resting order in the level would have been filled by a sweep that leaves the other side at our price. On the fill snapshot, 1,298 of 1,330 `touch_only` fills have zero size left on our side.

Keeping `valid_empty` + `valid_cleared` + `touch_only` leaves $2,293.74, $3,294.24, and $2,729.61. Adding the 19 `improve_touch` fills makes $2,327.08, $3,330.84, and $2,735.76. Counting the fill-snapshot size drop as a cancel leaves $2,411.30, $3,460.44, and $2,791.58. `grid_v1` stays positive on these columns. `schedule` is $727 or $806 against its engine $714.

`parity-luna-orders` already has the backtest filling matched GRID BUYs at 0.40× live (21 vs 50 first fills, hazard ratio 0.400, CI 0.237–0.613, 499 placements). Dropping `touch_only` removes still more backtest fills and moves the backtest further from live.

The engine's onchain loader takes `taker_side` verbatim. The true aggressor on a file is `taker_side` only when `taker_asset_id == asset_id`; otherwise it is the flip. On that rule, 1,196 of 9,863 fills missed a real decrement and 461 took a phantom one (20 fills had both).

## Findings

| id | sev | claim | status |
| --- | --- | --- | --- |
| Q1 | S2 | Strict queue (fill snapshot ignored) leaves ~$120 / $40 / −$515. Keeping `touch_only` leaves $2,294 / $3,294 / $2,730. Counting the fill-snapshot level drop as a cancel leaves $2,411 / $3,460 / $2,792. Engine is $2,849 / $3,339 / $3,321. | verified |
| Q2 | — | Snapshot gap median is under 10ms (mean 39ms paid Telonex, 26ms collector). Per-snapshot CLEAR does not happen: `telonex.rs:372-418` emits CLEAR only for the first in-window snapshot. | verified; queue-reset claim refuted |
| Q3 | S2 | Verbatim `taker_side` disagrees with `taker_asset_id == asset_id` on 1,637 fills: 1,196 missed decrements (3,712 prints, 560,771 shares) and 461 phantom decrements (910 prints, 296,080 shares). | verified |

## Findings detail

Replay is offline. No engine run. Script: `work/fill-grok/honest_queue.py`. Rows: `work/fill-grok/queue_fills.jsonl` (9,863). Windows are `results.horn_at` through `game_ended_at` for the 613 LIVE matches, both tokens (1,226 files, 0 missing). Era is the horn date: through 2026-08-07 paid Telonex (5,341 fills), from 2026-08-09 collector (4,522). No LIVE fill falls on 2026-08-08.

`fills.queue_ahead` is the join-side size at submit, not size that later traded. `strategy.py:1169` sets it from `volume_at_price` (`maker_orders.py:194`), and `strategy.py:1288` copies `submitted.context.queue_ahead` onto the fill. Every fill's price matched its submit price (0 reprices within half a cent), so that latched depth is the depth at the fill price. The clock starts at the quote-event `accepted` timestamp (join is 0 missing; accept lag was 85ms to 1.24s in the seed-0 probe).

Ahead falls two ways in the strict pass. Onchain prints after accept and at or before the fill, at our price, with the aggressor that hits our side, subtract size. A snapshot strictly before the fill, whose size at our price is below ahead, sets ahead to that size. A later add never raises ahead. The strict pass does not treat the snapshot at the fill itself as a cancel. The variant below does: if that snapshot's size on our side is below ahead, ahead falls to that size. Tape is onchain when that window has rows (9,565 fills), else the `trades/` channel (130), else none (168). `trades/` `side` is already the side of the file it sits in (same `trade_id` is buy at 0.66 on one token and sell at 0.34 on the other), so it is not flipped again.

Classes: `valid_empty` (nothing displayed at our price at submit), `valid_cleared` (ahead reached 0), `early` (a hitting print at the fill microsecond while ahead was still positive), `touch_only` (opposite touch on the fill snapshot, ahead still positive, and we were not improving the touch), `improve_touch` (that touch, and at accept a BUY was above the best bid or a SELL was below the best ask), `unexplained` (ahead still positive, no same-microsecond print and no touch). `unexplained` median remaining ahead/ahead0 is 1.0. Those fills are dropped.

### Q1 — money

Exit rule, first order, as specified. A fill is kept only on its own class. Kept buys add inventory. A kept sell closes kept inventory up to the shares still held; size beyond that is dropped. A sell that is not kept does not trade, and the leftover shares settle at 1 if that token won and 0 otherwise (`match_catalog` `radiant_win` / `radiant_token_index`). Dropped buys create no inventory, so the backtest sell that closed them pays no cash. Rebate is the fill's `maker_rebate`, scaled on a partial sell. It is not inside `engine_pnl`: rebuilding cash plus settlement from every fill matches the engine to the cent (seed 0 $2,848.59, seed 1 $3,338.76, seed 2 $3,320.89).

`strict` keeps `valid_empty` + `valid_cleared` only. `+ touch` also keeps `touch_only`. `+ improve` adds the 19 `improve_touch` fills. `fill-snap` keeps the strict set plus any fill whose own-side size on the fill snapshot is zero (1,298 `touch_only`, all 19 `improve_touch`, 8 `unexplained`, 3 `early`). Rebate is not in these pnl figures. On `+ touch` it is $358.32 / $360.61 / $361.84.

| seed | mode | engine | strict | + touch | + improve | fill-snap |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | grid_v1 | 2,134.51 | −401.48 | 1,566.82 | 1,596.16 | 1,605.35 |
| 0 | schedule | 714.08 | 521.23 | 726.92 | 730.92 | 805.95 |
| 0 | total | 2,848.59 | 119.75 | 2,293.74 | 2,327.08 | 2,411.30 |
| 1 | grid_v1 | 2,624.68 | −481.44 | 2,567.32 | 2,599.92 | 2,654.49 |
| 1 | schedule | 714.08 | 521.23 | 726.92 | 730.92 | 805.95 |
| 1 | total | 3,338.76 | 39.79 | 3,294.24 | 3,330.84 | 3,460.44 |
| 2 | grid_v1 | 2,606.81 | −1,036.35 | 2,002.69 | 2,004.84 | 1,985.63 |
| 2 | schedule | 714.08 | 521.23 | 726.92 | 730.92 | 805.95 |
| 2 | total | 3,320.89 | −515.12 | 2,729.61 | 2,735.76 | 2,791.58 |

Seed 1's fill-snap total, and every schedule fill-snap row, sit above the engine. A dropped sell of a token that won settles at 1 instead of the in-game sale, so leaving a bad exit out can raise pnl. The 32 `touch_only` fills that fill-snap does not keep still had size on our side (17 of them on both sides at our price). `early` fills are mostly not on a snapshot microsecond (148 of 154), so this variant does not rescue them.

`schedule` fills are the same on every seed (236 buys, 379 sells, engine cash $400.92). The seed-to-seed swing is all `grid_v1`.

Of 6,540 `valid_cleared` fills, 1,706 had trade volume at our price at least equal to ahead0. The other 4,834 reached zero only because the displayed size fell. Dropping those and keeping `valid_empty` plus trade-cleared fills:

| seed | trades-only pnl | grid_v1 | schedule |
| --- | --- | --- | --- |
| 0 | −110.99 | −385.62 | 274.63 |
| 1 | −101.31 | −375.94 | 274.63 |
| 2 | −361.02 | −635.64 | 274.63 |

Class counts, all seeds. Buy notional is price times quantity on BUYs. Markout is the quantity-weighted mean on BUYs.

| class | n | buy n | buy notional | rebate | buy markout 30s | buy markout 300s |
| --- | --- | --- | --- | --- | --- | --- |
| valid_cleared | 6,540 | 2,965 | 113,487 | 652.54 | −0.0010 | +0.0117 |
| valid_empty | 1,304 | 658 | 31,985 | 149.43 | −0.0030 | +0.0169 |
| touch_only | 1,330 | 874 | 65,811 | 296.37 | −0.0016 | +0.0251 |
| unexplained | 516 | 197 | 6,072 | 53.11 | +0.0174 | −0.0137 |
| early | 154 | 26 | 1,170 | 8.91 | +0.0348 | +0.0358 |
| improve_touch | 19 | 17 | 407 | 1.56 | −0.0317 | +0.0869 |

Buy notional by seed and class:

| seed | valid_cleared | valid_empty | touch_only | unexplained | early | improve_touch | buy turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 37,147 | 10,878 | 22,127 | 2,339 | 386 | 141 | 73,018 |
| 1 | 38,842 | 10,164 | 21,931 | 1,868 | 425 | 217 | 73,446 |
| 2 | 37,498 | 10,943 | 21,754 | 1,866 | 358 | 49 | 72,468 |

`touch_only` buys are about $22k of notional per seed, none of them price-improving, and their 300s markout (+0.025) is not worse than `valid_cleared` buys (+0.012). The strict column drops them. The fill snapshot says otherwise for 1,298 of 1,330: own-side size is zero, which is the sweep that leaves the other side at our price. `improve_touch` is 19 fills, and all 19 also show zero own-side size on that snapshot. Another 70 `valid_empty` and 205 `valid_cleared` were improving the touch and are already in the strict set.

Per seed, the same shape. Seed 0: `valid_cleared` 1,002 BUY / 1,155 SELL, `valid_empty` 227 / 226, `touch_only` 290 / 151, `unexplained` 76 / 98, `early` 10 / 40, `improve_touch` 6 / 1. Seed 1: 982 / 1,282, 209 / 192, 288 / 150, 62 / 131, 9 / 46, 5 / 0. Seed 2: 981 / 1,138, 222 / 228, 296 / 155, 59 / 90, 7 / 42, 6 / 1.

### Q2 — snapshot cadence

Positive gaps between `timestamp_us` inside the match window. Gaps of 0 are counted aside and are not in the median. The histogram bin is 10ms, so a median in the first bin is the 5ms midpoint: more than half of positive gaps are shorter than 10ms.

| era | positive gaps | zero gaps | median | p90 | mean |
| --- | --- | --- | --- | --- | --- |
| telonex | 49,001,136 | 9,061,284 | <10ms | ~65ms | 38.7ms |
| collector | 48,692,541 | 0 | <10ms | ~35ms | 26.5ms |

Median of per-token medians is in that first bin, both eras. 5,178 fills land on a snapshot microsecond.

This cadence does not zero the queue. `parquet_book_snapshot_diff_rows` (`telonex.rs:355-420`) sets `emitted_snapshot` false and emits CLEAR only on the first snapshot that falls inside the window (`append_snapshot_rows`, `telonex.rs:603-604`). Every later snapshot is an UPDATE where the new size is positive and a DELETE where it is not (`telonex.rs:399-403`). UPDATE and DELETE do not reset queue-ahead. The earlier reading, that the next snapshot after submit puts us first in line, is withdrawn.

### Q3 — verbatim taker_side

Collector `onchain-decode.ts:273-296` writes one economic fill into both token files. Price is complemented when the fill's token is the sibling. `taker_side` is not. `taker_asset_id` is this file's asset when `mirrored` is true and the sibling otherwise. So the aggressor on this file's book is `taker_side` when `taker_asset_id == asset_id`, and the flip otherwise. A sibling buy is a sell into this book. The orchestrator's mid check (taker asset ≠ file and `buy` → mid down 267 vs up 69, match 8982107035) is the same rule. This replay uses that rule.

The loader does not. `telonex.py:3253-3254` takes the first of `side`, `taker_side`, `aggressor_side`. `telonex.rs:844-846` passes that string to `telonex_aggressor_side` (`telonex.rs:886-891`), which maps `buy`/`sell` and never reads `taker_asset_id`.

Counted only on prints at our price with accept < ts ≤ fill. A missed decrement is a print the true side hits and verbatim `taker_side` does not. A phantom is the reverse.

| seed | fills with a miss | fills with a phantom | fills with both | fills with either |
| --- | --- | --- | --- | --- |
| 0 | 406 | 160 | 10 | 556 |
| 1 | 409 | 156 | 3 | 562 |
| 2 | 381 | 145 | 7 | 519 |
| all | 1,196 | 461 | 20 | 1,637 |

Prints: 3,712 missed, 910 phantom. Share volume: 560,771 missed, 296,080 phantom. The 130 `trades/`-channel fills and the 168 with no tape contribute none of this; their side flags match. This is the loader's side error on the onchain tape. It is not a per-snapshot queue reset.

### Backtest fill rate

No `session.jsonl` under `esports-trader`. Live rate was not checked. No SSH.

Backtest, from `results.live_order_seconds` (time with any order resting, not summed across rungs): seed 0 has 3,282 fills in 206,154s (one fill per 63s), seed 1 has 3,356 in 210,388s (63s), seed 2 has 3,225 in 205,983s (64s).

Per rung, order lifetime is accept → `cancel_ack` on that order, summed. `level_index` −1 is every SELL (seed 0: 1,671 sells and 1,671 fills at −1). Rungs 0–2 are the buy layers. Fills per 1,000 rung-seconds:

| seed | rung 0 | rung 1 | rung 2 | sells (level −1) |
| --- | --- | --- | --- | --- |
| 0 | 21.2 (1,108 / 52,150s) | 5.5 (372 / 67,049s) | 2.0 (131 / 66,761s) | 10.6 (1,671 / 158,264s) |
| 1 | 20.6 (1,080 / 52,552s) | 5.3 (360 / 68,430s) | 1.7 (115 / 67,193s) | 11.1 (1,801 / 162,493s) |
| 2 | 19.9 (1,081 / 54,202s) | 5.4 (365 / 67,131s) | 1.8 (125 / 68,114s) | 10.6 (1,654 / 156,361s) |

About 1,200 accepts per seed have no `cancel_ack` and are not in the denominator.

## How the queue was rebuilt

One pass per token over `book_snapshot_full` from horn to game end. Best bid is the max positive size, best ask the min, same as `parse_best_bid_ask`. Size at our cent is summed within half a tick (`TICK_SIZE` 0.01). States are kept only when the touch or a watched size changes. Onchain rows are deduped on `(tx_hash, log_index)`. Same-timestamp events apply the print first, then the size cap. The fill-snap column re-reads the snapshot at the fill microsecond (`work/fill-grok/fill_snap_variant.py`) and treats own-side size zero as a cancel.

## Checked and OK

- All-fills cash plus settlement equals `engine_pnl` on every seed (worst match delta 0). The counterfactual uses that identity.
- Submit and accept quote events join every fill (`order_id`, 0 missing).
- 1,226 token windows all had a book file.
- `trades/` fallback is 130 fills; 168 fills have no print tape and can clear only through an empty level or a size drop (90 of those 168 are `valid_cleared`).
- Price-improving touches were split from joins at the best bid. None of the 1,330 `touch_only` fills were improving.

## Open questions

- Live fill rate needs a local `session.jsonl`. There is not one in `esports-trader`.
- 516 `unexplained` fills still had ahead at the fill and were not an exact snapshot touch or an exact print. They are dropped. Block timestamps are whole seconds, so a real print can sit off the fill microsecond and miss the `early` bucket.
- The strict size cap still counts a level that vanished before the fill as cancels in front of us. 4,834 `valid_cleared` fills depend on that. Trades-only, which ignores those cancels, is −$111 / −$101 / −$361. That stricter cut moves the backtest further from the 0.40× live fill rate, not toward it.
