# pnl-haiku — per-map PnL breakdown: legging, waves, tail
Status: FINAL

## Answer

- **Result.** The 10 maps settled at −$58.62 before rebate. The rebate estimate is +$13.09, so the total is −$45.53 with rebate. The telegram sum (−$42.07) marks the losing tails at their mid, not at 0. [verified]
- **Late second legs lost money. Fast ones made money.** Pairs whose second leg came within 10 s made +$10.45 on 1,308 shares (average pair cost 0.992). Pairs whose second leg came 10 s or later lost $43.33 on 2,731 shares (average pair cost 1.016). [verified]
- **The loss sits in late pairs above $1.** Late pairs with cost above $1 lost $125.92. Late pairs under $1 won $82.59. The net is −$43.33. [verified]
- **Late pairs cost $76.81 more than fast pairs.** This sums over the 7 maps that had fast pairs. It is an upper bound. A late second leg often fills when the price moves onto our bid, so part of the gap is selection, not slow execution. [verified number; selection reading likely]
- **Pairs lost $32.88. The unpaired tail lost $25.74.** Each map ends with unpaired shares: 171 in total, about 17 per map. They cost $26.53 and settled at $0.78. Only grid-3011820-m1 won its tail. [verified]
- **Most of the tail loss looks like bad luck, if the mid is right.** The tails formed at a mid between 0.095 and 0.335 on 9 of 10 maps. Using that mid as the win chance, the tails expected $34.27 and paid $0.78. The gap is −$33.49, about −1.4 standard deviations (sd $24.25). Tail entry itself beat the mid by $7.74. [verified arithmetic; luck reading likely]
- **Most fills came during a fall.** The fall windows (from the peak to the bottom of each 10-tick fall within 120 s) cover 46% of session time. Of 523 fills, 437 (84%) came inside such a window. The falling side took 4,744 of 8,247 bought shares (58%). [verified count; fills follow moves likely]
- **Wave losses are large on paper, but not realized.** Marking each fall's falling-side buys at the bottom gives a loss of $630.94. Adding the other side's buys in the same windows gives a net loss of $240.98. Both overlap the pair and tail results, so they do not add to −$58.62. The largest is grid-3011821-m1 NO: a gross loss of $71.04 on 162 shares. [verified]
- **Inventory was often one-sided while quoting.** While quoting (73% of session time), |YES − NO| was at least 10 for 53.5% of the time and at least 20 for 38.1%. Net never reached 30. The maximum was 27.5. The longest run at 20 or more was 1,158 s (grid-3011821-m2). [verified]
- **The brief's average method misplaces about $43.** It charges each unpaired share at the average price of all shares on that side: $70.12 in total. The lots we held cost $26.53. The average method shows pairs at +$10.71 instead of −$32.88. FIFO and LIFO agree within $3 on pairs (−$32.88 vs −$30.08). Use the lot split. [verified]

## Method

**Data (read-only, `work/data/`).**
- `trader_live_b/<match>/session.jsonl`: fill rows (523 in total), signal rows (mids, `entry_block`, positions), session_end (positions, `net_cash`, `inventory_value`, `equity`).
- `trader_live_b/<match>/match.json`: token ids, `yes_is_radiant`, `final.winner`, half-spread commit (via `session_start.git_commit`).
- `trader_live_b/wallet/live.db` (read-only): `fill_ledger` (CONFIRMED buys, MERGED rows), `token_cid`.
- `trader_live_b/wallet/engine_journal/live.jsonl`: `user_trade` rows, used for the trade-id cross-check.

**Scripts (stdlib only, run from `work/pnl-haiku/`).**
- `common.py`: loader. Reads the 10 maps, merges from `fill_ledger`, winner, half spread.
- `per_map.py`: accounting, FIFO legging, waves, inventory timeline, tail luck. Prints T1–T6 and the checks. Writes `results.json`. Output: `out_v3.txt`.
- `extras.py`: lot-convention sensitivity (FIFO, LIFO, average), group totals, late/fast totals, quoted-time totals. Output: `out_extras_v3.txt`.
- `xcheck.py`: fill counts against the ledger and the journal. Output: `xcheck_out.txt`.

**Definitions.**
- Units: prices in dollars per share. Shares are the fill sizes. "$" is USD.
- Pair (brief formula): pairs = min(total YES, total NO). Pair cost = avg YES + avg NO. Pair income = pairs × (1 − pair cost).
- Pair (lot, used for the main table): each fill closes the oldest open lot on the other side (FIFO). Wait = second-leg fill time − first-leg fill time. Pair profit = Σ q × (1 − first price − second price).
- Tail: unpaired lots at map end. Tail cost = Σ lot size × lot price. Tail settles at 1 if its side won, else 0.
- Tail formation: time of the earliest open lot. Market price = mid of the tail side at the signal nearest that time (either side).
- Tail luck: luck = tail × (settle − formation mid). Tail entry edge = tail × formation mid − tail cost. Tail PnL = entry edge + luck. Sum over maps: z = luck / sd, with sd = √Σ(tail² × mid × (1 − mid)). This treats each tail as one Bernoulli bet at its formation mid. It ignores mid error.
- Legging buckets: wait < 10 s, 10–60 s, 60–300 s, ≥ 300 s. "Late" = wait ≥ 10 s. "Loss on cost > 1" = Σ q × (cost − 1) for pairs with cost above 1.
- Wave episode: a side's mid is "falling" when it is at least 0.10 below the highest mid of that side in the previous 120 s. Contiguous falling samples form one episode. Start = that high (peak). End = the lowest mid inside the episode (bottom).
- Wave gross: falling-side fills in (start, end], each fill counted once. MTM = cost − shares × bottom mid. Wave net: all fills in the window (both sides), each once, marked at the nearest mids at the bottom time.
- Quoted time: signal interval where `entry_block == 'none'`. The state holds from one signal to the next, and fills split the interval.
- Inventory: net = YES shares − NO shares from fills. Time-weighted over the session.
- Rebate: 0.15 × 0.05 × size × p × (1 − p) per fill. Copied from `shared/utils/trading.py:54–63` (`maker_rebate_usdc`), not imported.
- Half spread: `git_commit` f0fe4d33 → 3 ticks, 76592d31 → 6 ticks. Read from the `session_start` row of all 10 maps. Six maps are at 3 ticks and four at 6.

**Assumptions.**
- Winner: `match.json` `final.winner` for 6 maps. For 4 Oddin maps `winner` is null. I took the winner from the last YES mid: 0.025 (9034789047, NO), 0.035 (9034957701, NO), 0.985 (9035220432, YES), 0.006 (9035318247, NO). [likely]
- Fill cost = price × size. The `net_cash` field on fill rows is a running balance, not a per-fill cost. Example: grid-3011820-m1 `session.jsonl:325` (NO fill, second 688) already shows the cash of the next fill, which is at `:327` (second 689). [verified]
- Merges are not in `session.jsonl`. I took them from `fill_ledger` MERGED rows (33 merge events, $4,038.15 of pairs). Each merge writes two rows, one per token.
- Mids: the nearest signal to the event time. The last signal before the event can be 3–4 s stale in a fast drop. Example: grid-3011820-m2 at 11:20:19 (the signal at 11:20:15 showed mid 0.225; the next one showed 0.110). Using last-before instead gives luck −$40.59 (z −1.6). The nearest rule gives −$33.49 (z −1.4).
- Thresholds: 10 ticks = 0.10 in price. Tick size changed to 0.001 mid-session on grid-3011820-m1 (`session.jsonl:1161`) and 9035220432 (`session.jsonl:4841`). I kept 0.10 in price for both.
- Quoted time uses only the entry state. `entry_block == 'none'` means no pull reason fired (`two_sided_quoting.py:37–79`, `session_core.py:140–170`). It does not prove a live bid on both sides.

**Checks.**
- Totals per side (all 10 maps): YES 4,110.00 shares for $2,226.63 (average 0.5418). NO 4,137.25 shares for $1,870.92 (average 0.4522). Per-map values are in T2.
- Cash identity: settled FIFO total − (session `net_cash` + tail settlement) is within 0.0001 on all 10 maps. Column "check vs cash" in T1.
- Session_end check: pair income + tail PnL = session_end `net_cash` + tail settlement. Using the session mark (`inventory_value`) instead of settlement, the sum is −$55.17 against settled −$58.62. The −$3.45 gap is the losing tails marked above 0.
- Merges: FIFO pair shares total 4,038.15 = ledger merged pairs 4,038.15 = cash from merges. Each map matches within 0.0001 (`out_v3.txt`, Checks).
- Ledger: session fills equal `fill_ledger` CONFIRMED rows per map by count, shares and dollars. 523 = 523, shares 8,247.25 (`xcheck_out.txt`).
- Journal: all 523 session trade ids appear in `user_trade`. The 2 extra unique ids (525 total) are the two FAILED trades in `fill_ledger` ($22.70). Those trades are not in the session. [verified]
- Rebate: equity + rebate matches the telegram context within $0.01 on all 10 maps. Total rebate 13.09 matches the context figure.
- Position check: fill-based net equals signal `pos_yes − pos_no` except for 24 signals on 9034789047 (12 while quoting, 12 in `recovery`), and 1 to 3 signals on 4 other maps (`out_v3.txt`, T5). The cash check still passes. [verified]

## Results

### T1. Settled PnL by component (FIFO lots, USD)

Winner marked `*` if inferred from the last mid. "Entry vs mid" = tail × formation mid − tail cost. "Luck" = tail × (settle − formation mid). Half = half spread in ticks.

| map | half | winner | pair income | legging loss (memo) | tail sh | tail cost | entry vs mid | luck | tail PnL | settled | rebate | settled + rebate | telegram ctx |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | 3 | YES | 4.78 | 4.72 | 0.78 | 0.67 | 0.00 | 0.11 | 0.11 | 4.89 | 1.05 | 5.94 | 5.93 |
| grid-3011820-m2 | 3 | NO | −17.90 | 24.42 | 7.42 | 0.86 | −0.05 | −0.82 | −0.86 | −18.77 | 2.12 | −16.65 | −16.50 |
| grid-3011820-m3 | 3 | YES | −1.80 | 3.00 | 26.66 | 5.27 | 1.00 | −6.27 | −5.27 | −7.07 | 0.36 | −6.71 | −6.52 |
| 9034789047 | 3 | NO* | 4.37 | 23.14 | 19.98 | 1.80 | 0.50 | −2.30 | −1.80 | 2.58 | 2.86 | 5.44 | 5.94 |
| 9034957701 | 3 | NO* | −1.23 | 14.60 | 21.44 | 3.19 | 0.35 | −3.54 | −3.19 | −4.41 | 0.97 | −3.44 | −2.69 |
| 9035220432 | 6 | YES* | 4.46 | 0.13 | 13.34 | 0.80 | 0.47 | −1.27 | −0.80 | 3.66 | 0.14 | 3.80 | 4.00 |
| 9035318247 | 6 | NO* | 1.20 | 0.40 | 22.22 | 4.67 | 2.78 | −7.44 | −4.67 | −3.47 | 0.24 | −3.23 | −3.12 |
| grid-3011821-m1 | 3 | YES | −30.98 | 47.39 | 25.81 | 3.97 | 1.71 | −5.68 | −3.97 | −34.95 | 4.77 | −30.18 | −29.79 |
| grid-3011821-m2 | 6 | YES | 1.90 | 5.44 | 25.90 | 3.90 | 1.02 | −4.92 | −3.90 | −1.99 | 0.32 | −1.67 | −0.76 |
| grid-3011822-m1 | 6 | YES | 2.32 | 2.67 | 7.40 | 1.41 | −0.04 | −1.37 | −1.41 | 0.91 | 0.26 | 1.18 | 1.44 |
| **Total** | | | **−32.88** | **125.92** | **170.96** | **26.53** | **7.74** | **−33.49** | **−25.74** | **−58.62** | **13.09** | **−45.53** | **−42.07** |

Pair income + tail PnL = settled on every row. Pair shares total 4,038.15 (all matched).

### T2. Brief's average method (memo, not used for the split)

Pair cost = avg YES + avg NO. Tail avg = average of all fills on the tail side. The last column is the lot cost per share of the tail lots.

| map | Y sh | N sh | avg YES | avg NO | pairs | pair cost | pair income (avg) | tail side | tail sh | tail avg (all fills) | tail PnL (avg) | settled | tail avg (lots) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | 419.20 | 418.41 | 0.7746 | 0.2141 | 418.41 | 0.9887 | 4.71 | YES | 0.78 | 0.7746 | 0.18 | 4.89 | 0.8600 |
| grid-3011820-m2 | 624.76 | 617.34 | 0.5360 | 0.4880 | 617.34 | 1.0240 | −14.79 | YES | 7.42 | 0.5360 | −3.98 | −18.77 | 0.1162 |
| grid-3011820-m3 | 100.00 | 126.66 | 0.6860 | 0.3037 | 100.00 | 0.9897 | 1.03 | NO | 26.66 | 0.3037 | −8.10 | −7.07 | 0.1975 |
| 9034789047 | 925.35 | 905.36 | 0.5890 | 0.3952 | 905.36 | 0.9842 | 14.35 | YES | 19.98 | 0.5890 | −11.77 | 2.58 | 0.0900 |
| 9034957701 | 293.45 | 272.01 | 0.4172 | 0.5662 | 272.01 | 0.9833 | 4.53 | YES | 21.44 | 0.4172 | −8.94 | −4.41 | 0.1486 |
| 9035220432 | 46.66 | 60.00 | 0.6429 | 0.2167 | 46.66 | 0.8595 | 6.55 | NO | 13.34 | 0.2167 | −2.89 | 3.66 | 0.0600 |
| 9035318247 | 82.22 | 60.00 | 0.4703 | 0.4133 | 60.00 | 0.8836 | 6.98 | YES | 22.22 | 0.4703 | −10.45 | −3.47 | 0.2100 |
| grid-3011821-m1 | 1418.25 | 1444.06 | 0.4352 | 0.5789 | 1418.25 | 1.0141 | −20.01 | NO | 25.81 | 0.5789 | −14.94 | −34.95 | 0.1538 |
| grid-3011821-m2 | 100.11 | 126.01 | 0.6850 | 0.2661 | 100.11 | 0.9511 | 4.90 | NO | 25.90 | 0.2661 | −6.89 | −1.99 | 0.1505 |
| grid-3011822-m1 | 100.00 | 107.40 | 0.7660 | 0.2094 | 100.00 | 0.9754 | 2.46 | NO | 7.40 | 0.2094 | −1.55 | 0.91 | 0.1900 |
| **Total** | | | | | | | **10.71** | | **170.96** | | **−69.34** | **−58.62** | |

Tail cost at average price = $70.12. Tail cost at lot price = $26.53.

### T3. Legging by second-leg wait (FIFO segments, all 10 maps)

| wait | segments | shares | avg pair cost | pair profit | loss on cost > 1 |
|---|---|---|---|---|---|
| < 10 s | 140 | 1,307.55 | 0.9920 | +10.45 | 23.80 |
| 10–60 s | 170 | 1,336.55 | 1.0244 | −32.55 | 64.04 |
| 1–5 min | 144 | 1,120.23 | 1.0137 | −15.31 | 51.40 |
| > 5 min | 32 | 273.82 | 0.9834 | +4.54 | 10.48 |
| **All** | 486 | 4,038.15 | 1.0081 | **−32.88** | 149.72 |

Late (≥ 10 s): 2,730.59 shares, average cost 1.0159, profit −43.33, loss on cost > 1 = 125.92, winning part = 82.59. Fast (< 10 s): 1,307.55 shares, average cost 0.9920, profit +10.45.

Per map (late = second leg ≥ 10 s):

| map | pair sh | pair profit | fast sh | fast avg cost | late sh | late avg cost | late profit | late loss (cost > 1) | late excess vs fast |
|---|---|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | 418.41 | 4.78 | 186.35 | 0.9868 | 232.06 | 0.9900 | 2.31 | 4.72 | 0.76 |
| grid-3011820-m2 | 617.34 | −17.90 | 198.96 | 0.9966 | 418.38 | 1.0444 | −18.59 | 24.42 | 20.03 |
| grid-3011820-m3 | 100.00 | −1.80 | 40.00 | 0.9900 | 60.00 | 1.0367 | −2.20 | 3.00 | 2.80 |
| 9034789047 | 905.36 | 4.37 | 273.30 | 0.9686 | 632.06 | 1.0067 | −4.21 | 23.14 | 24.07 |
| 9034957701 | 272.01 | −1.23 | 44.52 | 0.9985 | 227.49 | 1.0057 | −1.30 | 14.60 | 1.65 |
| 9035220432 | 46.66 | 4.46 | 0.00 | n/a | 46.66 | 0.9043 | 4.46 | 0.13 | n/a |
| 9035318247 | 60.00 | 1.20 | 0.00 | n/a | 60.00 | 0.9800 | 1.20 | 0.40 | n/a |
| grid-3011821-m1 | 1,418.25 | −30.98 | 544.42 | 1.0058 | 873.83 | 1.0318 | −27.81 | 47.39 | 22.72 |
| grid-3011821-m2 | 100.11 | 1.90 | 0.00 | n/a | 100.11 | 0.9810 | 1.90 | 5.44 | n/a |
| grid-3011822-m1 | 100.00 | 2.32 | 20.00 | 0.9289 | 80.00 | 0.9888 | 0.90 | 2.67 | 4.79 |
| **Total** | 4,038.15 | −32.88 | 1,307.55 | 0.9920 | 2,730.59 | 1.0159 | −43.33 | 125.92 | 76.81 |

### T4. Waves (falling side: ≥ 10 ticks in 120 s, USD)

| map | episodes | with fills | falling-side shares | gross loss (falling side, MTM) | largest episode gross loss | fills in windows (both sides) | net loss (both sides, MTM) |
|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | 8 | 6 | 400.68 | 30.99 | 18.04 | 42 | 12.06 |
| grid-3011820-m2 | 16 | 12 | 543.03 | 86.80 | 47.48 | 59 | 34.83 |
| grid-3011820-m3 | 6 | 4 | 106.66 | 7.80 | 3.20 | 8 | 4.20 |
| 9034789047 | 91 | 35 | 1,210.56 | 140.30 | 33.87 | 98 | 37.35 |
| 9034957701 | 65 | 19 | 388.05 | 44.59 | 13.68 | 31 | 39.02 |
| 9035220432 | 32 | 5 | 100.00 | 2.50 | 1.10 | 6 | 1.90 |
| 9035318247 | 59 | 5 | 102.22 | 7.64 | 4.27 | 8 | 4.74 |
| grid-3011821-m1 | 40 | 31 | 1,529.84 | 285.47 | 71.04 | 158 | 91.87 |
| grid-3011821-m2 | 18 | 7 | 175.70 | 15.52 | 7.57 | 15 | 7.06 |
| grid-3011822-m1 | 22 | 8 | 187.40 | 9.34 | 3.80 | 12 | 7.94 |
| **Total** | 357 | 132 | 4,744.13 | 630.94 | | 437 | 240.98 |

Largest gross episodes (loss in USD, positive = loss):
- grid-3011821-m1 NO: peak 0.775, bottom 0.015, 172 s, 13 fills, 162.34 shares at 0.4526 avg, gross loss 71.04.
- grid-3011820-m2 YES: peak 0.745, bottom 0.095, 205 s, 13 fills, 135.03 shares at 0.4466 avg, gross loss 47.48.
- grid-3011821-m1 YES: peak 0.755, bottom 0.305, 146 s, 12 fills, 154.54 shares at 0.5905 avg, gross loss 44.13.

Windows cover 45.7% of session time and hold 437 of 523 fills (83.6%). Per map, coverage runs from 19% (grid-3011820-m1) to 60% (9034789047). Fills inside windows per map: 42/57, 59/81, 8/12, 98/111, 31/38, 6/6, 8/8, 158/183, 15/15, 12/12.

### T5. Inventory |YES − NO| (time-weighted)

Quoted = `entry_block == 'none'`. "≥ 10 / ≥ 20 / ≥ 30" = share of quoted time. "All ≥ 20" = share of whole session time.

| map | session s | quoted share | quoted ≥ 10 | quoted ≥ 20 | quoted ≥ 30 | all ≥ 20 | longest ≥ 20 (s) | times hit 30 | max \|net\| | net at end | signals where pos ≠ fills |
|---|---|---|---|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | 2,663 | 0.590 | 0.637 | 0.549 | 0.000 | 0.328 | 654 | 0 | 27.47 | 0.78 | 0 |
| grid-3011820-m2 | 3,417 | 0.765 | 0.547 | 0.426 | 0.000 | 0.355 | 293 | 0 | 27.42 | 7.42 | 2 |
| grid-3011820-m3 | 2,049 | 0.598 | 0.631 | 0.631 | 0.000 | 0.669 | 980 | 0 | 26.66 | −26.66 | 0 |
| 9034789047 | 6,179 | 0.601 | 0.523 | 0.281 | 0.000 | 0.228 | 317 | 0 | 27.42 | 19.98 | 24 |
| 9034957701 | 5,432 | 0.816 | 0.662 | 0.509 | 0.000 | 0.471 | 1,129 | 0 | 27.40 | 21.44 | 3 |
| 9035220432 | 2,848 | 0.615 | 0.113 | 0.034 | 0.000 | 0.021 | 59 | 0 | 26.66 | −13.34 | 0 |
| 9035318247 | 3,156 | 0.635 | 0.309 | 0.309 | 0.000 | 0.322 | 670 | 0 | 26.66 | 22.22 | 1 |
| grid-3011821-m1 | 4,932 | 0.873 | 0.607 | 0.325 | 0.000 | 0.339 | 264 | 0 | 27.47 | −25.81 | 3 |
| grid-3011821-m2 | 3,425 | 0.828 | 0.573 | 0.565 | 0.000 | 0.570 | 1,158 | 0 | 27.40 | −25.90 | 0 |
| grid-3011822-m1 | 3,465 | 0.832 | 0.522 | 0.235 | 0.000 | 0.238 | 565 | 0 | 27.40 | −7.40 | 0 |
| **All (quoted-time weighted)** | 37,565 | 0.728 | **0.535** | **0.381** | **0.000** | | 1,158 | 0 | 27.47 | | |

### T6. Tail luck (formation mid = nearest signal to earliest open lot)

| map | tail side | tail sh | tail mid at formation | mid VW over lots | expected value | actual value | luck | tail avg cost (lots) |
|---|---|---|---|---|---|---|---|---|
| grid-3011820-m1 | YES | 0.78 | 0.860 | 0.860 | 0.67 | 0.78 | 0.11 | 0.860 |
| grid-3011820-m2 | YES | 7.42 | 0.110 | 0.110 | 0.82 | 0.00 | −0.82 | 0.116 |
| grid-3011820-m3 | NO | 26.66 | 0.235 | 0.205 | 6.27 | 0.00 | −6.27 | 0.198 |
| 9034789047 | YES | 19.98 | 0.115 | 0.115 | 2.30 | 0.00 | −2.30 | 0.090 |
| 9034957701 | YES | 21.44 | 0.165 | 0.173 | 3.54 | 0.00 | −3.54 | 0.149 |
| 9035220432 | NO | 13.34 | 0.095 | 0.095 | 1.27 | 0.00 | −1.27 | 0.060 |
| 9035318247 | YES | 22.22 | 0.335 | 0.247 | 7.44 | 0.00 | −7.44 | 0.210 |
| grid-3011821-m1 | NO | 25.81 | 0.220 | 0.220 | 5.68 | 0.00 | −5.68 | 0.154 |
| grid-3011821-m2 | NO | 25.90 | 0.190 | 0.190 | 4.92 | 0.00 | −4.92 | 0.151 |
| grid-3011822-m1 | NO | 7.40 | 0.185 | 0.185 | 1.37 | 0.00 | −1.37 | 0.190 |
| **Total** | | 170.96 | | | **34.27** | **0.78** | **−33.49** | |

z = −33.49 / 24.25 = −1.38. With last-before-signal mids instead, the total luck is −40.59 (z −1.58).

### X2. Half spread and feed (FIFO, ex-rebate)

| group | maps | fills | pair income | tail PnL | settled | rebate est | settled per fill |
|---|---|---|---|---|---|---|---|
| half 3 (commit f0fe4d33) | 6 | 482 | −42.76 | −14.97 | −57.74 | 12.13 | −0.120 |
| half 6 (commit 76592d31) | 4 | 41 | 9.89 | −10.77 | −0.88 | 0.96 | −0.022 |
| feed grid | 6 | 360 | −41.69 | −15.29 | −56.98 | 8.88 | −0.158 |
| feed oddin | 4 | 163 | 8.81 | −10.45 | −1.64 | 4.21 | −0.010 |

The half-spread and feed groups overlap. Half 6 is on 2 grid maps and 2 Oddin maps. Time of day also differs: the half-3 maps started between 09:23 and 14:10 UTC, and the half-6 maps between 15:24 and 17:46 UTC (`match.json` `horn_at_utc`).

### X1. Lot convention sensitivity (pairs and tail cost, USD)

| convention | pair profit | tail cost |
|---|---|---|
| FIFO (used) | −32.88 | 26.53 |
| LIFO | −30.08 | 29.33 |
| Average (brief formula) | +10.71 | 70.12 |

## Recommendations (ranked by expected $ per map, with confidence)

1. **Spread: test 6 ticks on the 3-tick volume.** The 6-tick maps settled −$0.022 per fill. The 3-tick maps settled −$0.120 per fill. At about 80 fills per map, a naive carry-over gives about +$7.8 per map (−$9.6 → −$1.8). Confidence: low. The sample is 4 maps, and time and feed overlap. Action: replay the same 10 maps at 6 ticks in the sim.
2. **Legging: fix the late second leg.** Late pairs cost $0.030 per share more than fast pairs (76.81 over 2,524 late shares on 7 maps). The upper bound is about $7.7 per map across all 10 maps. Confidence: medium that the gap is real; low that a fix captures it. Part of the gap is selection (the price moves onto our bid). Action: test time-based rules in the sim.
3. **Do not take the second leg at once.** Our maker bid sits one half spread below fair. A taker buy pays about the ask, half a tick above fair. The gap is about 0.035 per share at half 3 and 0.065 at half 6. Add the taker fee 0.05 × p × (1 − p): 0.009 to 0.013 per share for p 0.25 to 0.5 (`shared/utils/trading.py:48–51`). The total is more than the 0.030 late excess. Confidence: likely. Fair value is the book mid, which is an estimate.
4. **Waves: test a pause during a 10-tick fall within 120 s.** Of 523 fills, 437 sit inside a fall window. Size is unknown. The wave MTM is not realized, and a pause also removes rising-side buys that hedged the falls. Confidence: low. Action: replay the 357 episodes in the sim with quotes pulled.
5. **Tail: do not change the net cap on this evidence.** Tail entry beat the mid by $7.74 in total. The −$33.49 gap looks like variance (z −1.4). Cutting the cap also cuts the entry edge. Confidence: low to medium. Revisit if tails keep forming below 0.25 and keep losing.

## Refuted / open questions

- **Refuted: waves cost $631.** That figure marks falling-side buys at the fall bottom. It is not a realized loss. The realized total of all maps is −$58.62.
- **Refuted: the average method.** It puts the tail at $70.12 and the pairs at +$10.71. The lots we held cost $26.53. FIFO and LIFO agree within $3 on pairs.
- **Open: winner of four Oddin maps.** The calls come from the last mid (0.006 to 0.985). If one call is wrong, that map moves by up to the tail size (13.3 to 22.2 shares). Confidence in the calls: likely.
- **Open: day-number reconciliation.** The context gives 525 buys for $4,103.22 and 34 merges for $4,043.15. The ledger gives 523 confirmed buys for $4,097.55 and 33 merge events for $4,038.15. The two FAILED trades cost $22.70 and are not in the session. The $5.67 and $5.00 gaps are not explained here. The running map grid-3011822-m2 is not in the data.
- **Open: luck depends on the mid rule.** Nearest signal gives −$33.49 (z −1.4). Last-before-signal gives −$40.59 (z −1.6). I report the nearest rule.
- **Open: late second legs and price moves.** I did not test whether late second legs follow a move in the book. Speed and pick-off questions belong to the lat agent.
- **Open: half-spread result rests on 4 maps.** Time of day and feed overlap with spread.
- **Open: 24 position mismatches on 9034789047.** Signal positions and fill positions differ on 24 signals: 12 while quoting, 12 in `recovery`. The cash check still passes. I did not trace the cause.
- **Not tested: other wave thresholds.** I used one rule: 10 ticks in 120 s. Other thresholds would change the wave counts.
