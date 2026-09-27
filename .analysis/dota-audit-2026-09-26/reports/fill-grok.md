# fill-grok — backtest execution and PnL accounting; settlement tail
Status: FINAL

## Summary

- The Dota LIVE settlement tail is real binary payout, not a swapped token or a mark hidden in cash flow. For every completed map in seeds 0–2, `engine_pnl − cash_flow` equals the catalog settlement of the shares still open (1 if that token won, else 0). Seed totals: engine $2,848.59 = cash −$2,685.26 + settlement $5,533.85 (seed 0); $3,338.76 = −$1,993.57 + $5,332.33 (seed 1); $3,320.89 = −$2,796.37 + $6,117.26 (seed 2).
- "39/39 winners" is the exit rule, on the ≥1 share cut of seed 0. Loser-token shares are sold in-game (sold fraction 1.0000; leftover 0.34 / 0.28 / 0.25 shares). The unsold book is the winner token. Reported `terminal_position > 0` is 41 / 39 / 39 rows: 40, 39, and 39 winners. The one reported loser is 0.10 dire shares on match 8922400300 (seed 0), end mid 0.005, payout 0.
- The sell that would exit those winners is dropped once `ceil(fair)` is 1.00. After the last SELL submit, the dominant `no_quote` reason is `fair`, and 90.7% of those seed-0 events have token fair > 0.99. Holding to $1 versus the last fresh mid is about $45–$56 per seed. The tail's profit versus entry cost is $1,449.82 / $1,473.71 / $1,585.99, about half of engine PnL, because the shares were bought near 0.75 and the book was already ~0.99.
- Markouts on this catalog do not use the outcome. All 3,282 / 3,356 / 3,225 fills have `reference_source` `mid`. None have reference 0 or 1. The settlement fallback in `postprocess.py` is live code and would leak the outcome into the metric on a map with no mids.
- Map-run accounting was not changed by the series terminal-bid branch. `engine_pnl` uses the framework binary leg PnL whenever `terminal_marks` is None, which is every non-series run. The wallet and the drawdown still keep one lot per match, so a two-token overlap is marked as one token. One seed-1 map overlapped by 4.21 shares.
- Re-checked against orchestrator N1. Rounded seed totals and the non-dust hold counts match (seed 0: 31 `grid_v1` + 4 `schedule/grid` + 0 Oddin). Of seed 0's $5,533.85 settlement, $5,220.39 is `grid_v1` holds and $313.04 is schedule/grid holds. N1's "every hold has a positive settlement part" is true for those 35 non-dust maps and false for dust match 8922400300, whose settlement part is 0.00. The last SELL is cancel-acked on both modes; it is not left resting under a price that ran through it. Three of the 31 `grid_v1` holds end on `stale_signal`, so that path does go stale.

## Findings table

| ID | Sev | Layer | Title | Confidence | Impact |
|---|---|---|---|---|---|
| fill-grok-F1 | S2 | backtest exit / PnL composition | Sell is dropped when ceil(fair) is 1.00, so leftover inventory is winners held to $1 | verified | ~half of engine PnL is this hold; vs the last fresh mid the extra is ~$45–$56/seed |
| fill-grok-F2 | S3 | wallet / drawdown | One open lot per match merges both tokens | verified | engine PnL unaffected; MTM/deposit wrong if both legs are open. One overlap of 4.21 shares |
| fill-grok-F3 | S3 | report | Terminal report and hold stats omit the settlement tail | verified | $1,450 of seed-0 profit is invisible in the hold section (p50 147s, closed trips only) |
| fill-grok-F4 | S3 | markout | Markout falls through to binary settlement when a map has no mid | verified (path); not hit on LIVE | $0 on this catalog; would put the outcome into buy/sell 300s |
| fill-grok-F5 | S4 | results row | `terminal_position` is the last fill's `position_after` on that token only | verified | sub-cent residue is settled by the engine and omitted from the reported inventory |

## Findings detail

### fill-grok-F1 — Sell dropped at 1.00; the tail is winners held to settlement

Where: `src/strategy/quoting.py:519-532` (`_choose_sell_target`, HEAD `bbb28897`). The lift onto fair is `6078a6087` (2026-09-13); the `< 1` reject is `6feb54e58` (2026-09-06). Cancel-on-game-end with no replacement is `src/backtest/strategy.py:637-639` and `src/strategy/quoting.py:960-962`.

What is wrong: a SELL is `max(ceil(ask), ceil(token fair))`, and a price that is not strictly inside (0, 1) makes the function return None. Once token fair is above 0.99, `ceil` is 1.00 and the whole sell is skipped, including a still-legal ask at 0.99. There is no fallback to the ask. `sell_after_game_end` is false on this manifest, so game end cancels resting orders and does not join the ask either.

Mechanism, measured on `data/backtests/dota_maker/LIVE` (symlink `validation_join_delta02_x015_cut480_p4_archive-s3-20260924`):

1. Cash identity. `results.cash_flow` is the sum of fill notionals (`results.py:197-200`). `engine_pnl` is the sum of the two framework leg PnLs (`results.py:221-223`), and every traded leg has `settlement_pnl_applied`. Walking fills per token and paying catalog `radiant_win` / `radiant_token_index` reproduces `engine_pnl − cash_flow` on every match (0 mismatches above $0.02). Seed aggregates match to the printed float:

| seed | engine_pnl | cash_flow | settlement remainder | walked catalog payout |
|---|---|---|---|---|
| 0 | 2848.58541305 | −2685.26355595 | 5533.84896900 | 5533.84896900 |
| 1 | 3338.76173591 | −1993.56791409 | 5332.32965000 | 5332.32965000 |
| 2 | 3320.88774906 | −2796.37374894 | 6117.26149800 | 6117.26149800 |

`summary.json` equals those sums, plus rebate and markout point estimates (section Checked). A wrong token index, a constant $1 for every leftover, or a terminal bid stuffed into cash flow would break this identity. Loser residue pays 0: seed 0 walked qty 5534.1906 versus payout 5533.8490, gap 0.34 shares.

2. Who is still open. Shares bought on the token that lost the map, versus the token that won:

| seed | winner bought | winner sold | winner open | winner sold frac | loser bought | loser sold | loser open | loser sold frac |
|---|---|---|---|---|---|---|---|---|
| 0 | 71452.36 | 65918.51 | 5533.85 | 0.9226 | 49222.42 | 49222.08 | 0.34 | 1.0000 |
| 1 | 71575.49 | 66243.16 | 5332.33 | 0.9255 | 49447.41 | 49447.14 | 0.28 | 1.0000 |
| 2 | 70551.31 | 64434.05 | 6117.26 | 0.9133 | 49023.96 | 49023.70 | 0.25 | 1.0000 |

Closed loser round-trips: 155 / 148 / 145. Median SELL was 2.0–2.5¢ above the token mid, at or above fair on 98.7% / 99.3% / 98.6% of them, median 2257 / 2346 / 2347 seconds before `game_ended_at`. Sells after `game_ended_at`: 0 fills, 0 quantity, all three seeds. Closed winner round-trips also exist (205 / 209 / 204), same shape (median exit 2.5¢ over the mid). The unsold book is what never got a later SELL.

3. Why the replacement never comes. On seed 0, the last SELL order on a reported terminal position ends as `cancel_ack` with reason kill 23, stale_book 12, stale_signal 3, game_end 1, and 2 positions never submitted a SELL. Seed 1: kill 24, stale_book 9, stale_signal 3, game_end 1, none 2. Seed 2: kill 23, stale_book 14, game_end 2. After that submit, seed-0 `no_quote` reasons are dominated by `fair` (28 of 39 maps). Of 16,395 `fair` no_quote events after the last SELL, 14,868 (90.7%) have token fair > 0.99, which ceils to 1.00 and is rejected at `quoting.py:529-530`.

4. Money. Profit of the ≥1 share holds versus their cost basis: $1,449.82 / $1,473.71 / $1,585.99 (50.9% / 44.1% / 47.8% of engine PnL). Average entry on seed 0 is 0.750. Last cached token mid, median, is 0.994. Settlement premium versus that mid is $176.5 / $174.9 / $76.0, of which maps whose market-seconds cache stops >30s before catalog duration contribute $131.1 / $131.6 / $19.5 (stale end mid, not the map-end book; worst is match 8843465364, end mid 0.595, gap 525s). On caches that reach the end, the premium is $45.4 / $43.3 / $56.5. That is the gap versus selling at the last fresh mid. The $5,534 remainder is payout cash (shares × 1), not profit.

The series-edge "39 positions, 5,533 shares, all won" is seed 0 with a ≥1 share cut: 39 positions, all catalog winners, qty 5533.39. Counting every `terminal_position > 0` adds the 0.10 loser and a 0.04 winner. Seeds 1–2 at ≥1 share are 38 and 38, all winners.

How to confirm or fix: in `_choose_sell_target`, if the lifted price is outside (0, 1), keep `ceil(ask)` when that price is inside (0, 1). Re-run one seed and the winner-open column should fall by about the fresh-cache premium (~$45), not by $5,500.

Position list. Every `results.terminal_position > 0` row. `tok` is the token index, `rti` is `radiant_token_index`, `won` is catalog settlement of that token. `entry_s` is the market-seconds game second of the lot's first buy. `last_sell` is the last submitted SELL price (blank if none), not a fill. `sell_s_before_end` is that submit versus catalog `game_ended_at` (negative means the submit is after the catalog end). `end_mid` is the token's last `market_status=ok` paired mid; `gap_s` is catalog duration minus that second. A large gap means the mid is not the map-end book.

| seed | match | slug | side | tok | rti | rad_win | qty | avg | entry_s | last_sell | sell_s_before_end | end_mid | end_s | gap_s | won |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 8843465364 | dota2-4iki-nande4-2026-06-08-game1 | radiant | 1 | 1 | True | 276.76 | 0.463 | 201 | 0.66 | 606 | 0.595 | 1181 | 525 | True |
| 0 | 8844308689 | dota2-vpp-z10-2026-06-08 | radiant | 1 | 1 | True | 406.19 | 0.697 | 16 | 0.99 | 62 | 0.990 | 1765 | 0 | True |
| 0 | 8885365825 | dota2-l1ga-ngx-2026-07-07-game1 | radiant | 1 | 1 | True | 120.48 | 0.830 | 380 | 0.99 | 380 | 0.989 | 2191 | 0 | True |
| 0 | 8885470551 | dota2-l1ga-ngx-2026-07-07-game2 | dire | 1 | 0 | False | 24.54 | 0.760 | 41 | 0.99 | 216 | 0.990 | 2209 | 0 | True |
| 0 | 8885928262 | dota2-ty-og-2026-07-07-game1 | dire | 0 | 1 | False | 48.77 | 0.800 | 313 | 0.99 | 1119 | 0.994 | 1570 | 0 | True |
| 0 | 8886638110 | dota2-poorra-bb4-2026-07-08-game2 | dire | 1 | 0 | False | 119.04 | 0.840 | 432 | 0.99 | 210 | 0.996 | 1808 | 0 | True |
| 0 | 8886640910 | dota2-flc-re-2026-07-08-game2 | dire | 0 | 1 | False | 123.45 | 0.810 | 207 | 0.98 | 305 | 0.996 | 2001 | 0 | True |
| 0 | 8886815251 | dota2-l1ga-playti-2026-07-08-game2 | radiant | 1 | 1 | True | 132.15 | 0.757 | 78 | 0.99 | 739 | 0.999 | 2252 | 0 | True |
| 0 | 8887260104 | dota2-ty-ic-2026-07-08-game1 | radiant | 0 | 0 | True | 1.20 | 0.820 | 391 |  |  | 0.990 | 1656 | 7 | True |
| 0 | 8887337672 | dota2-ty-ic-2026-07-08-game2 | radiant | 0 | 0 | True | 120.48 | 0.830 | 410 | 0.99 | 56 | 0.990 | 1957 | 13 | True |
| 0 | 8887432754 | dota2-vp-1win-2026-07-08-game2 | radiant | 1 | 1 | True | 138.88 | 0.720 | 28 | 0.99 | 755 | 0.999 | 1670 | 0 | True |
| 0 | 8888173515 | dota2-playti-lvlup1-2026-07-09-game1 | dire | 0 | 1 | False | 125.00 | 0.800 | 250 | 0.99 | 885 | 0.997 | 2523 | 0 | True |
| 0 | 8888279558 | dota2-playti-lvlup1-2026-07-09-game2 | radiant | 0 | 0 | True | 128.20 | 0.780 | 212 | 0.99 | 807 | 0.995 | 1886 | 3 | True |
| 0 | 8890162325 | dota2-1win-og-2026-07-10-game1 | dire | 0 | 1 | False | 119.04 | 0.840 | 221 | 0.98 | 835 | 0.999 | 2367 | 36 | True |
| 0 | 8890179830 | dota2-lgd-ty-2026-07-10-game1 | radiant | 1 | 1 | True | 270.45 | 0.739 | 303 | 0.99 | 953 | 0.992 | 1955 | 20 | True |
| 0 | 8893070561 | dota2-1win-ty-2026-07-12-game1 | dire | 1 | 0 | False | 127.64 | 0.764 | 373 | 0.99 | 390 | 0.996 | 1890 | 0 | True |
| 0 | 8899307453 | dota2-pari-re-2026-07-17-game1 | dire | 0 | 1 | False | 126.58 | 0.790 | 470 | 0.99 | 750 | 0.999 | 2183 | 2 | True |
| 0 | 8899420575 | dota2-pari-re-2026-07-17-game2 | radiant | 0 | 0 | True | 329.52 | 0.828 | 445 | 0.99 | 512 | 0.998 | 1836 | 0 | True |
| 0 | 8900771154 | dota2-ngx-bb4-2026-07-16-game1 | dire | 1 | 0 | False | 119.04 | 0.840 | 341 | 0.99 | 943 | 0.999 | 2115 | 2 | True |
| 0 | 8908178087 | dota2-nemiga-aion-2026-07-22-game2 | radiant | 0 | 0 | True | 63.69 | 0.840 | 137 | 0.98 | 315 | 0.997 | 2289 | 0 | True |
| 0 | 8908486051 | dota2-stx-spirit1-2026-07-22-game2 | radiant | 1 | 1 | True | 3.46 | 0.561 | 203 | 0.67 | 1655 | 0.935 | 3296 | 0 | True |
| 0 | 8910632054 | dota2-nemiga-ill-2026-07-23-game2 | radiant | 0 | 0 | True | 1.81 | 0.631 | 209 | 0.93 | 726 | 0.966 | 1997 | 17 | True |
| 0 | 8912622476 | dota2-ill-aion-2026-07-25-game2 | radiant | 0 | 0 | True | 72.16 | 0.758 | 254 | 0.82 | 140 | 0.975 | 2975 | 4 | True |
| 0 | 8914885598 | dota2-rearis-pckcp-2026-07-26-game2 | radiant | 0 | 0 | True | 250.00 | 0.795 | 419 | 0.99 | 632 | 0.995 | 1672 | 0 | True |
| 0 | 8916041863 | dota2-lvlup1-pckcp-2026-07-27-game1 | radiant | 0 | 0 | True | 24.10 | 0.679 | 235 | 0.99 | 254 | 0.995 | 1376 | 0 | True |
| 0 | 8917675823 | dota2-pr1-nemiga-2026-07-28 | radiant | 0 | 0 | True | 210.04 | 0.706 | 130 | 0.98 | 282 | 0.994 | 1520 | 0 | True |
| 0 | 8918176547 | dota2-pckcp-kw1-2026-07-28-game2 | dire | 0 | 1 | False | 131.57 | 0.710 | 309 | 0.99 | 74 | 0.992 | 1696 | 0 | True |
| 0 | 8919264561 | dota2-rearis-z10-2026-07-29-game1 | dire | 1 | 0 | False | 4.55 | 0.620 | 223 | 0.62 | 2875 | 0.990 | 2918 | 14 | True |
| 0 | 8922400300 | dota2-enjoy-glyph-2026-07-31-game2 | dire | 0 | 1 | True | 0.10 | 0.570 | 460 |  |  | 0.005 | 3086 | 0 | False |
| 0 | 8923899787 | dota2-enjoy-yb1-2026-08-01-game1 | radiant | 1 | 1 | True | 357.32 | 0.740 | 192 | 0.88 | 400 | 0.985 | 2320 | 30 | True |
| 0 | 8927539264 | dota2-vg-yb1-2026-08-03-game1 | radiant | 0 | 0 | True | 283.69 | 0.705 | 246 | 0.99 | 34 | 0.980 | 1914 | 6 | True |
| 0 | 8930615242 | dota2-playti-yb1-2026-08-05-game2 | radiant | 0 | 0 | True | 473.99 | 0.633 | 35 | 0.99 | 84 | 0.994 | 2265 | 0 | True |
| 0 | 8937822821 | dota2-z10-navi-2026-08-09-game2 | dire | 1 | 0 | False | 150.14 | 0.835 | 379 | 0.99 | 821 | 0.995 | 1814 | 0 | True |
| 0 | 8938855404 | dota2-ill-z10-2026-08-10-game2 | radiant | 1 | 1 | True | 138.00 | 0.725 | 233 | 0.95 | 324 | 0.910 | 1402 | 307 | True |
| 0 | 8973088430 | dota2-ks-spirit1-2026-08-29-game2 | radiant | 0 | 0 | True | 147.59 | 0.632 | -16 | 0.99 | 1 | 0.976 | 1251 | 0 | True |
| 0 | 8973613420 | dota2-clo-yb1-2026-08-30 | radiant | 1 | 1 | True | 50.86 | 0.819 | 115 | 0.99 | 673 | 0.996 | 1694 | 0 | True |
| 0 | 8982107035 | dota2-spirit1-ks-2026-09-04-game2 | radiant | 1 | 1 | True | 119.04 | 0.840 | 631 | 0.98 | -32 | 0.946 | 2555 | 15 | True |
| 0 | 8994679957 | dota2-xctn-yg-2026-09-12-game1 | radiant | 0 | 0 | True | 14.27 | 0.840 | 392 | 0.98 | 1043 | 0.995 | 1821 | 1 | True |
| 0 | 8994796331 | dota2-navi-z10-2026-09-12-game2 | dire | 0 | 1 | False | 0.04 | 0.790 | 276 | 0.86 | 930 | 0.985 | 1614 | 0 | True |
| 0 | 9004373048 | dota2-lynx-synaps-2026-09-18-game2 | radiant | 1 | 1 | True | 129.87 | 0.770 | 152 | 0.98 | 627 | 0.995 | 1775 | 0 | True |
| 0 | 9006987731 | dota2-kalmy-yes-2026-09-17-game2 | radiant | 1 | 1 | True | 49.82 | 0.720 | 141 | 0.91 | 367 | 0.870 | 805 | 550 | True |
| 1 | 8843465364 | dota2-4iki-nande4-2026-06-08-game1 | radiant | 1 | 1 | True | 284.23 | 0.463 | 201 | 0.64 | 663 | 0.595 | 1181 | 525 | True |
| 1 | 8844308689 | dota2-vpp-z10-2026-06-08 | radiant | 1 | 1 | True | 259.08 | 0.656 | 78 | 0.99 | 47 | 0.990 | 1765 | 0 | True |
| 1 | 8885470551 | dota2-l1ga-ngx-2026-07-07-game2 | dire | 1 | 0 | False | 380.88 | 0.758 | 41 | 0.98 | 210 | 0.990 | 2209 | 0 | True |
| 1 | 8885665054 | dota2-nem-pari-2026-07-07-game1 | dire | 1 | 0 | False | 12.20 | 0.830 | 295 | 0.98 | 255 | 0.985 | 2479 | 0 | True |
| 1 | 8885928262 | dota2-ty-og-2026-07-07-game1 | dire | 0 | 1 | False | 127.40 | 0.785 | 216 | 0.99 | 1108 | 0.994 | 1570 | 0 | True |
| 1 | 8886638110 | dota2-poorra-bb4-2026-07-08-game2 | dire | 1 | 0 | False | 119.04 | 0.840 | 432 | 0.99 | 212 | 0.996 | 1808 | 0 | True |
| 1 | 8886815251 | dota2-l1ga-playti-2026-07-08-game2 | radiant | 1 | 1 | True | 131.57 | 0.760 | 173 | 0.99 | 737 | 0.999 | 2252 | 0 | True |
| 1 | 8887100676 | dota2-vg-pari-2026-07-08-game1 | dire | 1 | 0 | False | 121.95 | 0.820 | 212 | 0.99 | 357 | 0.991 | 1858 | 10 | True |
| 1 | 8887260104 | dota2-ty-ic-2026-07-08-game1 | radiant | 0 | 0 | True | 1.20 | 0.820 | 391 |  |  | 0.990 | 1656 | 7 | True |
| 1 | 8887337672 | dota2-ty-ic-2026-07-08-game2 | radiant | 0 | 0 | True | 120.48 | 0.830 | 410 | 0.99 | 56 | 0.990 | 1957 | 13 | True |
| 1 | 8887432754 | dota2-vp-1win-2026-07-08-game2 | radiant | 1 | 1 | True | 138.88 | 0.720 | 28 | 0.99 | 757 | 0.999 | 1670 | 0 | True |
| 1 | 8888173515 | dota2-playti-lvlup1-2026-07-09-game1 | dire | 0 | 1 | False | 125.00 | 0.800 | 250 | 0.99 | 891 | 0.997 | 2523 | 0 | True |
| 1 | 8888279558 | dota2-playti-lvlup1-2026-07-09-game2 | radiant | 0 | 0 | True | 128.20 | 0.780 | 212 | 0.99 | 805 | 0.995 | 1886 | 3 | True |
| 1 | 8890036748 | dota2-pari-mouz-2026-07-10-game2 | dire | 0 | 1 | False | 40.00 | 0.800 | 136 | 0.99 | 702 | 0.996 | 1343 | 0 | True |
| 1 | 8890179830 | dota2-lgd-ty-2026-07-10-game1 | radiant | 1 | 1 | True | 193.03 | 0.728 | 183 | 0.99 | 953 | 0.992 | 1955 | 20 | True |
| 1 | 8893070561 | dota2-1win-ty-2026-07-12-game1 | dire | 1 | 0 | False | 128.32 | 0.760 | 385 | 0.99 | 277 | 0.996 | 1890 | 0 | True |
| 1 | 8899307453 | dota2-pari-re-2026-07-17-game1 | dire | 0 | 1 | False | 173.33 | 0.791 | 288 | 0.99 | 750 | 0.999 | 2183 | 2 | True |
| 1 | 8900771154 | dota2-ngx-bb4-2026-07-16-game1 | dire | 1 | 0 | False | 119.04 | 0.840 | 341 | 0.99 | 943 | 0.999 | 2115 | 2 | True |
| 1 | 8908178087 | dota2-nemiga-aion-2026-07-22-game2 | radiant | 0 | 0 | True | 63.09 | 0.840 | 137 | 0.98 | 326 | 0.997 | 2289 | 0 | True |
| 1 | 8912622476 | dota2-ill-aion-2026-07-25-game2 | radiant | 0 | 0 | True | 7.12 | 0.759 | 383 | 0.82 | 140 | 0.975 | 2975 | 4 | True |
| 1 | 8913726962 | dota2-lynx-kw1-2026-07-25-game2 | dire | 0 | 1 | False | 4.16 | 0.540 | 332 |  |  | 0.990 | 2996 | 0 | True |
| 1 | 8914885598 | dota2-rearis-pckcp-2026-07-26-game2 | radiant | 0 | 0 | True | 251.58 | 0.795 | 345 | 0.99 | 640 | 0.995 | 1672 | 0 | True |
| 1 | 8916041863 | dota2-lvlup1-pckcp-2026-07-27-game1 | radiant | 0 | 0 | True | 140.00 | 0.660 | 184 | 0.99 | 254 | 0.995 | 1376 | 0 | True |
| 1 | 8917675823 | dota2-pr1-nemiga-2026-07-28 | radiant | 0 | 0 | True | 184.03 | 0.709 | 103 | 0.99 | 285 | 0.994 | 1520 | 0 | True |
| 1 | 8918176547 | dota2-pckcp-kw1-2026-07-28-game2 | dire | 0 | 1 | False | 144.20 | 0.689 | 287 | 0.99 | 96 | 0.992 | 1696 | 0 | True |
| 1 | 8922137329 | dota2-vg-amaru-2026-07-31-game2 | radiant | 0 | 0 | True | 50.00 | 0.810 | 370 | 0.99 | 219 | 0.990 | 1393 | 95 | True |
| 1 | 8923899787 | dota2-enjoy-yb1-2026-08-01-game1 | radiant | 1 | 1 | True | 281.36 | 0.740 | 192 | 0.98 | 55 | 0.985 | 2320 | 30 | True |
| 1 | 8924616191 | dota2-rnx-xtreme-2026-08-01-game2 | radiant | 1 | 1 | True | 232.59 | 0.757 | 399 | 0.99 | 93 | 0.995 | 2010 | 0 | True |
| 1 | 8930615242 | dota2-playti-yb1-2026-08-05-game2 | radiant | 0 | 0 | True | 490.49 | 0.612 | 57 | 0.99 | 89 | 0.994 | 2265 | 0 | True |
| 1 | 8931981851 | dota2-rearis-nh-2026-08-06-game1 | dire | 1 | 0 | False | 59.99 | 0.759 | 469 | 0.99 | 588 | 0.994 | 1818 | 0 | True |
| 1 | 8937822821 | dota2-z10-navi-2026-08-09-game2 | dire | 1 | 0 | False | 68.49 | 0.835 | 379 | 0.99 | 821 | 0.995 | 1814 | 0 | True |
| 1 | 8938855404 | dota2-ill-z10-2026-08-10-game2 | radiant | 1 | 1 | True | 105.99 | 0.796 | 370 | 0.95 | 324 | 0.910 | 1402 | 307 | True |
| 1 | 8940643574 | dota2-yes-z10-2026-08-11-game1 | radiant | 1 | 1 | True | 27.61 | 0.509 | 399 | 0.97 | 342 | 0.999 | 1813 | 0 | True |
| 1 | 8973088430 | dota2-ks-spirit1-2026-08-29-game2 | radiant | 0 | 0 | True | 304.36 | 0.635 | 49 | 0.98 | 2 | 0.976 | 1251 | 0 | True |
| 1 | 8982107035 | dota2-spirit1-ks-2026-09-04-game2 | radiant | 1 | 1 | True | 119.04 | 0.840 | 631 | 0.98 | -32 | 0.946 | 2555 | 15 | True |
| 1 | 8994679957 | dota2-xctn-yg-2026-09-12-game1 | radiant | 0 | 0 | True | 14.27 | 0.840 | 392 | 0.98 | 1043 | 0.995 | 1821 | 1 | True |
| 1 | 8994796331 | dota2-navi-z10-2026-09-12-game2 | dire | 0 | 1 | False | 0.04 | 0.790 | 276 | 0.86 | 930 | 0.985 | 1614 | 0 | True |
| 1 | 9004373048 | dota2-lynx-synaps-2026-09-18-game2 | radiant | 1 | 1 | True | 129.87 | 0.770 | 152 | 0.98 | 627 | 0.995 | 1775 | 0 | True |
| 1 | 9006987731 | dota2-kalmy-yes-2026-09-17-game2 | radiant | 1 | 1 | True | 49.82 | 0.720 | 141 | 0.91 | 367 | 0.870 | 805 | 550 | True |
| 2 | 8844308689 | dota2-vpp-z10-2026-06-08 | radiant | 1 | 1 | True | 269.30 | 0.677 | 130 | 0.99 | 34 | 0.990 | 1765 | 0 | True |
| 2 | 8850534819 | dota2-ill-pr1-2026-06-13-game2 | dire | 1 | 0 | False | 85.00 | 0.800 | 469 | 0.99 | 323 | 0.995 | 1550 | 0 | True |
| 2 | 8885365825 | dota2-l1ga-ngx-2026-07-07-game1 | radiant | 1 | 1 | True | 120.48 | 0.830 | 380 | 0.98 | 385 | 0.989 | 2191 | 0 | True |
| 2 | 8885470551 | dota2-l1ga-ngx-2026-07-07-game2 | dire | 1 | 0 | False | 261.70 | 0.751 | 60 | 0.99 | 216 | 0.990 | 2209 | 0 | True |
| 2 | 8885928262 | dota2-ty-og-2026-07-07-game1 | dire | 0 | 1 | False | 48.77 | 0.800 | 313 | 0.99 | 1112 | 0.994 | 1570 | 0 | True |
| 2 | 8886638110 | dota2-poorra-bb4-2026-07-08-game2 | dire | 1 | 0 | False | 119.04 | 0.840 | 432 | 0.99 | 210 | 0.996 | 1808 | 0 | True |
| 2 | 8886815251 | dota2-l1ga-playti-2026-07-08-game2 | radiant | 1 | 1 | True | 132.15 | 0.757 | 78 | 0.99 | 739 | 0.999 | 2252 | 0 | True |
| 2 | 8887337672 | dota2-ty-ic-2026-07-08-game2 | radiant | 0 | 0 | True | 120.48 | 0.830 | 410 | 0.99 | 56 | 0.990 | 1957 | 13 | True |
| 2 | 8887432754 | dota2-vp-1win-2026-07-08-game2 | radiant | 1 | 1 | True | 275.86 | 0.725 | 28 | 0.98 | 763 | 0.999 | 1670 | 0 | True |
| 2 | 8888173515 | dota2-playti-lvlup1-2026-07-09-game1 | dire | 0 | 1 | False | 125.00 | 0.800 | 250 | 0.99 | 891 | 0.997 | 2523 | 0 | True |
| 2 | 8888279558 | dota2-playti-lvlup1-2026-07-09-game2 | radiant | 0 | 0 | True | 128.20 | 0.780 | 212 | 0.99 | 804 | 0.995 | 1886 | 3 | True |
| 2 | 8889615310 | dota2-l1ga-liquid-2026-07-10-game1 | radiant | 1 | 1 | True | 251.58 | 0.795 | 285 | 0.99 | 691 | 0.996 | 1695 | 0 | True |
| 2 | 8890162325 | dota2-1win-og-2026-07-10-game1 | dire | 0 | 1 | False | 119.04 | 0.840 | 221 | 0.99 | 897 | 0.999 | 2367 | 36 | True |
| 2 | 8890179830 | dota2-lgd-ty-2026-07-10-game1 | radiant | 1 | 1 | True | 275.86 | 0.725 | 357 | 0.99 | 953 | 0.992 | 1955 | 20 | True |
| 2 | 8893070561 | dota2-1win-ty-2026-07-12-game1 | dire | 1 | 0 | False | 131.57 | 0.760 | 385 | 0.99 | 373 | 0.996 | 1890 | 0 | True |
| 2 | 8899420575 | dota2-pari-re-2026-07-17-game2 | radiant | 0 | 0 | True | 267.81 | 0.828 | 445 | 0.99 | 513 | 0.998 | 1836 | 0 | True |
| 2 | 8905490238 | dota2-z10-nemiga-2026-07-20-game1 | radiant | 0 | 0 | True | 4.49 | 0.620 | 381 | 0.56 | 2357 | 0.990 | 2756 | 8 | True |
| 2 | 8908178087 | dota2-nemiga-aion-2026-07-22-game2 | radiant | 0 | 0 | True | 90.12 | 0.840 | 137 | 0.98 | 325 | 0.997 | 2289 | 0 | True |
| 2 | 8912622476 | dota2-ill-aion-2026-07-25-game2 | radiant | 0 | 0 | True | 66.31 | 0.759 | 383 | 0.99 | 1 | 0.975 | 2975 | 4 | True |
| 2 | 8914885598 | dota2-rearis-pckcp-2026-07-26-game2 | radiant | 0 | 0 | True | 125.00 | 0.800 | 345 | 0.99 | 642 | 0.995 | 1672 | 0 | True |
| 2 | 8916041863 | dota2-lvlup1-pckcp-2026-07-27-game1 | radiant | 0 | 0 | True | 151.76 | 0.659 | 184 | 0.99 | 254 | 0.995 | 1376 | 0 | True |
| 2 | 8918176547 | dota2-pckcp-kw1-2026-07-28-game2 | dire | 0 | 1 | False | 141.94 | 0.693 | 262 | 0.99 | 92 | 0.992 | 1696 | 0 | True |
| 2 | 8923899787 | dota2-enjoy-yb1-2026-08-01-game1 | radiant | 1 | 1 | True | 275.46 | 0.740 | 241 | 0.98 | 55 | 0.985 | 2320 | 30 | True |
| 2 | 8924616191 | dota2-rnx-xtreme-2026-08-01-game2 | radiant | 1 | 1 | True | 260.77 | 0.767 | 399 | 0.99 | 94 | 0.995 | 2010 | 0 | True |
| 2 | 8927539264 | dota2-vg-yb1-2026-08-03-game1 | radiant | 0 | 0 | True | 294.77 | 0.680 | 225 | 0.99 | 12 | 0.980 | 1914 | 6 | True |
| 2 | 8930615242 | dota2-playti-yb1-2026-08-05-game2 | radiant | 0 | 0 | True | 490.49 | 0.612 | 57 | 0.99 | 91 | 0.994 | 2265 | 0 | True |
| 2 | 8937631882 | dota2-ill-re-2026-08-09-game2 | radiant | 1 | 1 | True | 120.79 | 0.629 | 4 | 0.99 | 537 | 0.985 | 1879 | 0 | True |
| 2 | 8937822821 | dota2-z10-navi-2026-08-09-game2 | dire | 1 | 0 | False | 150.14 | 0.835 | 379 | 0.99 | 821 | 0.995 | 1814 | 0 | True |
| 2 | 8938855404 | dota2-ill-z10-2026-08-10-game2 | radiant | 1 | 1 | True | 143.89 | 0.695 | 233 | 0.95 | 331 | 0.910 | 1402 | 307 | True |
| 2 | 8964962329 | dota2-ic-fts-2026-08-25-game1 | dire | 0 | 1 | False | 117.82 | 0.749 | 28 | 0.99 | 349 | 0.992 | 1347 | 0 | True |
| 2 | 8970207176 | dota2-rearis-sb5-2026-08-28 | dire | 0 | 1 | False | 14.10 | 0.777 | 145 | 0.99 | 480 | 0.992 | 1944 | 8 | True |
| 2 | 8973088430 | dota2-ks-spirit1-2026-08-29-game2 | radiant | 0 | 0 | True | 414.29 | 0.648 | -16 | 0.98 | 2 | 0.976 | 1251 | 0 | True |
| 2 | 8973613420 | dota2-clo-yb1-2026-08-30 | radiant | 1 | 1 | True | 121.33 | 0.830 | 138 | 0.99 | 675 | 0.996 | 1694 | 0 | True |
| 2 | 8974053011 | dota2-navi-mouz-2026-08-30-game1 | dire | 0 | 1 | False | 88.49 | 0.840 | 185 | 0.99 | 535 | 0.994 | 2279 | 0 | True |
| 2 | 8982107035 | dota2-spirit1-ks-2026-09-04-game2 | radiant | 1 | 1 | True | 119.04 | 0.840 | 631 | 0.98 | -32 | 0.946 | 2555 | 15 | True |
| 2 | 8994679957 | dota2-xctn-yg-2026-09-12-game1 | radiant | 0 | 0 | True | 14.27 | 0.840 | 392 | 0.98 | 1043 | 0.995 | 1821 | 1 | True |
| 2 | 8994796331 | dota2-navi-z10-2026-09-12-game2 | dire | 0 | 1 | False | 0.04 | 0.790 | 276 | 0.86 | 930 | 0.985 | 1614 | 0 | True |
| 2 | 9004373048 | dota2-lynx-synaps-2026-09-18-game2 | radiant | 1 | 1 | True | 129.87 | 0.770 | 152 | 0.98 | 627 | 0.995 | 1775 | 0 | True |
| 2 | 9006987731 | dota2-kalmy-yes-2026-09-17-game2 | radiant | 1 | 1 | True | 49.82 | 0.720 | 141 | 0.91 | 367 | 0.870 | 805 | 550 | True |

### fill-grok-F2 — Wallet and drawdown keep one lot per match

Where: `src/backtest/wallet_path.py:100-120` and the same shape in `calculate_reserve_path` (`wallet_path.py:374-396`) and `DrawdownState.apply_fill` (`postprocess.py:218-229`). Introduced with the wallet tape, `0afe6fe30` (2026-08-22).

A match has one lot. A BUY on the other token adds to that quantity and overwrites `token_index`. A SELL subtracts from that quantity with no token check. Settlement then pays `settlement_value_for_token` of whichever index was written last, times the summed size (`wallet_path.py:129-140`).

Engine PnL does not use this path. It sums the two framework legs, and the per-token walk matches it, so the published engine number is not double-counted. The shared-wallet `required_cash`, MTM, and per-match drawdown are.

On these seeds the overlap is rare. Fills where both tokens were positive at once: 4 maps (seed 0), 4 (seed 1), 6 (seed 2). The only overlap above 0.01 shares on the smaller leg is seed 1 match 8940487898, peak min(qty) 4.21, both legs flat by the end (float residue). No map ended with two material legs. Impact on this catalog is a few dollars of MTM at most; the bug is still in the shared function.

How to fix: key the lot by `(match_id, token_index)`.

### fill-grok-F3 — The report hides the tail it just computed

Where: `summarize_holds` / `_closed_hold_seconds` (`postprocess.py:462-504`) count a hold only when quantity returns to 0. `format_terminal_report` (`report.py:180-214`, `217-276`) prints pnl, rebate, and that hold block. It does not print `cash_flow`, `settlement_remainder`, or terminal inventory. Those fields exist on the arm (`postprocess.py:647-652`) and in `summary.json`.

Seed 0 hold block: 357 closed trips, p50 147.0s, p90 597.6s. The 41 open positions, whose profit versus cost is $1,449.82, never enter that distribution. Someone reading the terminal report sees a 147s round trip and a $2,848.59 pnl, with no split between trades and settlement.

How to fix: add settlement remainder, terminal shares, and open-position count to the pnl block; report open holds separately from closed ones.

### fill-grok-F4 — Markout can use the future outcome; this catalog did not

Where: `_reference_at_horizon` (`postprocess.py:135-151`). If no mid exists at or before `fill_ts + h`, and there is no series terminal bid, the reference is `settlement_value_for_token` (0 or 1) and the source column is `settlement`. That value is the map outcome. It is then quantity-weighted into `summary.json` `markout` (`postprocess.py:412-451`), which is the number the terminal report prints. The assumption string (`postprocess.py:78-80`) documents this.

On LIVE every fill's 30s and 300s source is `mid` (3,282 / 3,356 / 3,225). Zero references are exactly 0 or 1. Fills whose horizon runs past the last cached mid — the as-of last mid, not the outcome — are 24 / 26 / 23 at 300s. Their quantity-weighted markout is −5.85¢ / −5.85¢ / −2.18¢, so they pull the headline 300s markout down, not up. Point estimates in `summary.json` match `sum(markout * qty) / sum(qty)` for all four side × horizon cells.

How to fix: if the source would be `settlement`, leave the markout null and drop the fill from the weighted mean. Do not substitute the outcome.

### fill-grok-F5 — Reported terminal size is the last fill on one token

Where: `results.py:203-206`, since `7c350d94f` (2026-08-12). `terminal_position` is `position_after` of the last fill in the match's fill list, and the token index is that fill's token only when `position_after > 0`.

The engine still settles both legs (F1 identity). What the row hides is sub-cent float residue: 108 / 115 / 110 matches where the walk ends at 0.001–0.02 shares and the row says 0. Those crumbs are inside the $5,533.85 payout and outside `terminal_inventory`. For every row with walked qty ≥ 0.5, reported qty matches the walk (max abs diff 0) and the reported side matches the catalog side.

The series branch (`results.py:235-240`, `861c142d`) marks `terminal_position * terminal bid` of that one token. Map runs pass `terminal_marks=None` (`run.py:2147-2170`) and do not take the branch. A series run with two open tokens would mark only the last fill's token. Not observed on this map catalog.

## Architecture / performance / debuggability notes

1. Engine PnL and the wallet tape are two settlers. The engine uses framework `realized_outcome` per leg (`install_settlement_compatibility` only rewrites expiration and zeroes the engine fee, `run.py:292-306`). The wallet uses catalog `radiant_win` (`wallet_path.py:138-140`). On this catalog they agree. A future Gamma-versus-catalog disagreement would move `engine_pnl` and leave `required_cash` on the catalog. Worth a one-line assert that remainder equals the catalog walk.
2. The terminal report is the decision surface and it omits the settlement split (F3). `summary.json` has the split. Half the pnl is invisible in the view people actually read.
3. Series changes in the shared files are gated on `terminal_marks is not None` (`861c142d`, 2026-09-26, after this LIVE run's 2026-09-24 parquet timestamps). Map `engine_pnl`, map markout (terminal bid stays None, so the old mid-or-settlement order remains), and map reserve timing (`game_end` / `market_close`) are the pre-series formulas. One map-path change: a missing mid series used to KeyError and now becomes an empty series (`postprocess.py` drawdown). No LIVE map lacked a cache.
4. `quote_events.parquet` is ~21 MB per seed and is rewritten as one file. Fine at this size. Not a money bug.

## Checked and OK

- Summary versus parquet, all three seeds, no field mismatch: `engine_pnl`, `cash_flow`, `settlement_remainder`, buy/sell fill counts, `bought_shares`, terminal inventory, dust count, completed/terminated/matches, `maker_rebate`, `taker_fee`, buy and sell turnover. Rebate recomputed with `maker_rebate_usdc` (`0.15 * 0.05 * qty * p * (1-p)`) matches every row (0 mismatches). `net_pnl = engine_pnl + maker_rebate − taker_fee`. `is_maker` is true on every fill; taker fee is 0. Markout point estimates match the quantity-weighted mean.
- Both legs are loaded and settled separately. Buying the NO token is not netted into YES. The identity sums each token's own 0/1 payoff, so there is no double count in engine PnL.
- No fill has `ts_ns` after catalog `game_ended_at`. Losers are not closed by a post-game book, and they are not closed by a synthetic bid inside cash flow.
- `settlement_applied` is true for every traded map. Fill timestamps are monotonic per match.
- Series edit of `results.py` / `postprocess.py` / `marks.py` / `wallet_path.py` does not change map-run PnL while `terminal_marks` is None. `series_run.py` is dirty in the working tree and was not used; this catalog is the map LIVE run.
- `strip_own_book.py` subtracts the live bot's own resting size from archive books before the queue model. It does not touch PnL accounting. `maker_orders.py` is order state, not settlement.

Commands (from `esports-trader`, `PYTHONPATH=src`):

```
uv run python $R/work/fill-grok/settle_tail.py
uv run python $R/work/fill-grok/tail_followup.py
```

Outputs: `work/fill-grok/seed_summaries.json`, `held_positions.csv`, `held_table.md`, `tail_followup.txt`.

## Open questions / Needs from VPS

- Match 8982107035 (all three seeds, 119.04 shares, avg 0.84) is `schedule/grid`, not `grid_v1`. Market-cache entry second 631 is past `buy_cutoff_second` 480, and a SELL submit is 32s after catalog `game_ended_at`. N4's early archive horn is the first explanation to check; this pass did not. No fill is after catalog game end.
- Live uses the same kernel, so the 1.00 sell refusal should happen live too. This pass did not read VPS sessions. No SSH requested.
- Caches with `gap_s` of 300–550 (8843465364, 8938855404, 9006987731) stop quoting the book long before catalog duration. Their `end_mid` is not the map-end book. Whether the real book later traded at 0.99 is a market-data question.
