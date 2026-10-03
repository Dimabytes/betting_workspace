# Board-lead step 0: mid move from board receipt

### lol — 11745 events, 405 matches

Event curve, signed move in killer direction (c):

| offset | mean | median | p25 | p75 | n |
|---|---|---|---|---|---|
| +0s | 1.76 | 0.50 | -0.50 | 3.00 | 10632 |
| +0.5s | 2.92 | 1.00 | -0.00 | 5.00 | 10636 |
| +1s | 3.83 | 2.00 | 0.00 | 6.50 | 10634 |
| +2s | 4.46 | 2.50 | 0.00 | 7.50 | 10603 |
| +3s | 4.77 | 2.50 | -0.00 | 8.00 | 10587 |
| +4s | 4.91 | 2.67 | 0.00 | 8.00 | 10574 |
| +5s | 5.02 | 2.80 | 0.00 | 8.50 | 10535 |
| +6s | 5.11 | 3.00 | 0.00 | 8.50 | 10480 |
| +8s | 5.23 | 3.00 | 0.00 | 8.61 | 10388 |
| +10s | 5.32 | 3.00 | -0.00 | 9.00 | 10280 |
| +12s | 5.37 | 3.00 | 0.00 | 9.00 | 10168 |
| +15s | 5.49 | 3.00 | 0.00 | 9.50 | 10052 |
| +20s | 5.64 | 3.00 | 0.00 | 9.50 | 9904 |
| +30s | 5.73 | 3.50 | 0.00 | 10.00 | 9650 |
| +45s | 5.68 | 3.50 | 0.00 | 10.50 | 9315 |
| +60s | 5.66 | 3.50 | 0.00 | 10.69 | 9074 |
| +90s | 5.57 | 3.50 | -0.50 | 11.00 | 8914 |
| +120s | 5.55 | 3.50 | -0.95 | 12.00 | 8774 |
| +180s | 5.60 | 4.00 | -1.50 | 13.50 | 8208 |
| +240s | 5.68 | 4.00 | -2.50 | 14.50 | 7747 |
| +300s | 5.68 | 4.05 | -3.40 | 15.50 | 7336 |
| table_rx | 5.29 | 3.00 | -0.00 | 9.00 | 10272 |

Share of +300s move: board_rx agg 27.2% / median 2.6% (n=7060); table_rx agg 93.4% / median 24.2% (n=7036)
Remainder board_rx->+120s: mean 4.03c median 2.50c p25 -1.35 p75 9.50 | ->+300s: mean 4.14c median 3.00c

| month | n | mean rem +120c | agg share@board | med table lag s |
|---|---|---|---|---|
| 2026-08 | 236 | 4.78 | 18.9% | 8.3 |
| 2026-09 | 10932 | 3.98 | 27.8% | 8.3 |
| 2026-10 | 577 | 4.85 | 21.5% | 8.3 |

Winner token @board_rx: median spread 2.00c, <=2c in 40.2% of 10757
+1s: winner bid lift median 0.00c, <=0.5c in 51.5% | winner ask lift 1.00c | loser ask drop 0.00c
+2s: winner bid lift median 1.00c, <=0.5c in 45.7% | winner ask lift 1.00c | loser ask drop 1.00c
+3s: winner bid lift median 1.00c, <=0.5c in 43.1% | winner ask lift 1.00c | loser ask drop 1.00c
Board->table lag: median 8.3s p90 8.5s | table never reached in 2.3% of events

### dota — 14616 events, 294 matches

Event curve, signed move in killer direction (c):

| offset | mean | median | p25 | p75 | n |
|---|---|---|---|---|---|
| +0s | 0.65 | 0.00 | -0.50 | 1.50 | 13242 |
| +0.5s | 0.89 | 0.00 | -0.50 | 1.50 | 13276 |
| +1s | 1.14 | 0.25 | -0.40 | 2.00 | 13277 |
| +2s | 1.40 | 0.50 | -0.05 | 2.50 | 13290 |
| +3s | 1.64 | 0.50 | 0.00 | 3.00 | 13279 |
| +4s | 1.84 | 0.50 | 0.00 | 3.00 | 13266 |
| +5s | 1.98 | 0.87 | 0.00 | 3.50 | 13236 |
| +6s | 2.10 | 1.00 | 0.00 | 3.50 | 13207 |
| +8s | 2.31 | 1.00 | 0.00 | 4.00 | 13150 |
| +10s | 2.47 | 1.00 | 0.00 | 4.07 | 13103 |
| +12s | 2.62 | 1.15 | -0.00 | 4.50 | 13079 |
| +15s | 2.81 | 1.50 | -0.00 | 5.00 | 13008 |
| +20s | 3.00 | 1.50 | 0.00 | 5.50 | 12836 |
| +30s | 3.31 | 2.00 | 0.00 | 6.00 | 12557 |
| +45s | 3.47 | 2.00 | -0.05 | 6.50 | 12384 |
| +60s | 3.61 | 2.50 | -0.45 | 7.00 | 12178 |
| +90s | 3.73 | 2.50 | -0.55 | 7.50 | 11922 |
| +120s | 3.64 | 2.50 | -1.00 | 8.50 | 11778 |
| +180s | 3.69 | 2.50 | -2.00 | 9.50 | 11401 |
| +240s | 3.72 | 2.50 | -3.00 | 10.00 | 11072 |
| +300s | 3.83 | 3.00 | -3.50 | 11.00 | 10726 |
| table_rx | 2.31 | 1.00 | 0.00 | 4.00 | 13100 |

Share of +300s move: board_rx agg 15.2% / median 0.0% (n=10249); table_rx agg 60.0% / median 9.6% (n=10262)
Remainder board_rx->+120s: mean 3.04c median 2.00c p25 -1.50 p75 7.50 | ->+300s: mean 3.27c median 2.60c

| month | n | mean rem +120c | agg share@board | med table lag s |
|---|---|---|---|---|
| 2026-08 | 853 | 3.50 | 15.7% | 8.3 |
| 2026-09 | 13128 | 3.05 | 14.5% | 8.3 |
| 2026-10 | 635 | 2.24 | 31.3% | 8.3 |

Winner token @board_rx: median spread 3.00c, <=2c in 29.5% of 13584
+1s: winner bid lift median 0.00c, <=0.5c in 76.0% | winner ask lift 0.00c | loser ask drop 0.00c
+2s: winner bid lift median 0.00c, <=0.5c in 67.9% | winner ask lift 0.00c | loser ask drop 0.00c
+3s: winner bid lift median 0.00c, <=0.5c in 62.6% | winner ask lift 0.00c | loser ask drop 0.00c
Board->table lag: median 8.3s p90 8.5s | table never reached in 1.3% of events

## Verdict
- lol: remainder +120s mean 4.03c (needs >= 2c), agg share@board 27.2% (needs <30%) -> GO
- dota: remainder +120s mean 3.04c (needs >= 1c), agg share@board 15.2% (needs <30%) -> GO

## Frozen-model sensitivity to one naked death (validation subsample)
- lol: n=26366 rows / 60 matches | deaths_radiant+1: mean 0.13c |delta| 0.15c | deaths_dire+1: mean -0.36c |delta| 0.38c
- dota: n=26392 rows / 60 matches | deaths_radiant+1: mean 0.02c |delta| 0.02c | deaths_dire+1: mean -0.01c |delta| 0.01c
