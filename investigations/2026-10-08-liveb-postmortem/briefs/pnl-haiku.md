# Brief: pnl-haiku — per-map PnL breakdown: legging, waves, tail

## Question

Where did wallet B lose money today, map by map? The owner suspects two causes: (1) legging: we hold one side and buy the second side late at a bad price; (2) waves: the price runs one way, we keep buying the falling token and sit one-sided.

## Task

Use `fill` rows in `$D/trader_live_b/<match>/session.jsonl` (cross-check counts with `user_trade` in `$D/trader_live_b/wallet/engine_journal/live.jsonl`), `match.json` (`yes_token_id`, `no_token_id`, `yes_is_radiant`, `final.winner`), and `session_end`. For the mid price at any time, use `signal` rows (`yes_mid`, `no_mid`, `recorded_at_utc`) in the same file.

For each of the 10 maps:

1. Totals: shares and $ bought per side, average price per side, pairs (min of the two sides), pair cost = avg YES price + avg NO price, pair profit = pairs × (1 − pair cost), unpaired tail shares and which side, tail cost, tail settlement value (winner pays $1), tail PnL. Check: pair profit + tail PnL ≈ realized + inventory value in `session_end`. Show the rebate estimate separately.
2. Inventory timeline: net = YES shares − NO shares after each fill. Report: % of quoted time with |net| ≥ 10, ≥ 20, ≥ 30; the longest stretch at |net| ≥ 20 (seconds); number of times net hit the 30 cap.
3. Legging cost. Match fills FIFO: each fill opens or closes a "leg". For each completed pair: time between the first leg and the matching second leg; pair cost. Bucket by wait (< 10 s, 10–60 s, 1–5 min, > 5 min) and show count, average pair cost, and total pair profit per bucket. If pairs completed late cost more than $1, this is the legging loss; give the $.
4. Wave cost. Find episodes where one side's mid fell ≥ 10 ticks within 120 s. Sum the shares we bought on the falling side during the episode, their average price, and their mark-to-market at the episode end. Total $ lost in waves per map.
5. Classify each map's result into: pair income, legging loss, wave loss, tail settlement luck (tail side won or lost), rebate. One table, 10 rows plus a total. Mark which maps ran at half spread 3 vs 6.
6. Luck check for the tail: for each map, at the moment the tail formed, what was the market price of the tail side? Expected tail value = shares × that price. Compare to the actual settlement. The difference is settlement luck.

## Output

Report: `$R/reports/pnl-haiku.md`. Scripts: `$R/work/pnl-haiku/`. Keep the scripts simple; print every number you put in the report from a script.
