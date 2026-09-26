# Live fill loss after GRID kills

**verified.** On live LoL, the fills that land while the scoreboard already shows a kill and the `series_table` does not yet are 259 of 1,492 Polymarket fills (17%) and **31% of the negative 30-second markout dollars** (−$327 of −$1,059). The sharp part is the first 3 seconds after the scoreboard frame: qty-weighted markout **−4.30¢**, against **−0.08¢** when no kill was seen in the last 60 seconds. The 3–11s bin is milder (−2.80¢) and only partly blind, because the table arrives a median **8.30s** after the scoreboard, not 11s.

Cancelling resting orders on the victim's token for 12s after that scoreboard frame would have dropped 169 LoL fills. Of the 149 with a 30s mid, 93 were losers (−$171) and 56 were winners (+$44). Net 30s markout of that set is **−$127**. The same rule on Dota GRID is a smaller per-fill effect and, at 60s, gives back more than it saves.

Markout here is the traded token's later session mid minus the fill price for a BUY, and the reverse for a SELL. Dollars are that signed gap times size. That is a mark-to-mid, not settlement PnL. `net_cash` on the fill row is the running cash balance and is not split by kill.

## What "the window" actually is

`series_scoreboard_v2` carries the kill count and is the fast frame. `series_table` carries deaths, net worth, and XP, and it is the only frame that emits a model snapshot (`src/trader/grid_widgets.py:14-15`, `src/trader/grid_feed.py:256-287`). Lag-devin measured scoreboard delivery at median 2.77s after the event and table delivery at median 10.98s (`reports/lag-devin.md` F1). The gap between those two receipts, measured again here on the kill increments themselves:

| game | scoreboard kills | matched to a table death | table receipt − scoreboard receipt |
|---|---:|---:|---|
| LoL | 6,574 | 5,891 | p10 8.09s, **median 8.30s**, p90 8.52s |
| Dota GRID | 8,471 | 7,663 | p10 8.09s, **median 8.30s**, p90 8.50s |

A kill is one increment of a side's scoreboard `score` on the pinned map (`grid_widgets.py:127`, `read_map_scoreboard` at `:178`). The other side is the victim. The table time is the first later `series_table` frame whose victim-side death sum has increased (`grid_widgets.py:239`, `read_net_worth` at `:245`), within 40s. BLUE is radiant for LoL and RADIANT is radiant for Dota (`game_profile.py:47,65`; `radiant_id = side_0.team_id` at `grid_feed.py:89`). The victim's token follows `match.json` `yes_is_radiant`.

So "scoreboard knows, model does not" is about **0–8.3s after the scoreboard receipt**, not a full 3–11s. The 11s figure is event-to-table. Bins below are still the ones requested, measured from scoreboard receipt. `table_unseen` is the count whose death had not yet arrived on the table.

## LoL live fills

207 maps with at least one Polymarket fill, **1,492 fills**, all `execution_mode=live`. 13 Kalshi rows in those journals were skipped. 158 maps aligned signal seconds as an exact window of the replayed GRID events; 49 were greedy. Markouts use `yes_mid` / `no_mid` on the aligned signal whose GRID receipt is at or after fill time + horizon, within 15s (`session_journal.py:260-282` has no signal timestamp; `fill.ts_utc` is the exchange trade time, `session_journal.py:325`).

Qty-weighted markout. "Loss $" is the sum of negative (markout × size) at 30s. Total 30s markout is **−$452**. Gross negative markout is **−$1,059**; gross positive is +$607.

| class (after scoreboard receipt) | fills | BUY notional share | SELL notional share | 10s | 30s | 60s | 30s loss $ | share of 30s loss $ | table not yet in |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0–3s | 198 | 9.9% ($1,302) | 16.3% ($2,065) | −3.91¢ | **−4.30¢** | −4.33¢ | −271 | 26% | 195 / 198 |
| 3–11s | 107 | 3.2% ($425) | 8.9% ($1,133) | −2.28¢ | −2.80¢ | −1.45¢ | −115 | 11% | 64 / 107 |
| 11–30s | 125 | 4.4% ($585) | 11.9% ($1,514) | −1.28¢ | −0.67¢ | −0.26¢ | −83 | 8% | 6 / 125 |
| 30–60s | 190 | 12.6% ($1,656) | 11.2% ($1,422) | −0.22¢ | −0.12¢ | −0.76¢ | −79 | 7% | 9 / 190 |
| no kill in 60s | 785 | 64.7% ($8,502) | 43.8% ($5,553) | −0.09¢ | **−0.08¢** | −0.49¢ | −359 | 34% | — |
| event already, scoreboard not yet | 87 | 5.1% ($675) | 7.9% ($1,003) | −4.84¢ | **−4.64¢** | −4.94¢ | −152 | 14% | 87 / 87 |

BUY notional across all classes is $13,145. SELL notional is $12,690. Almost every fill is maker (the 0–3s bin is 197/198).

The blind slice (0–3s or 3–11s, and the table death has not arrived): **259 fills, 30s markout −$245, of which −$327 is the negative part (31% of all negative 30s dollars)**. Inside it, 0–3s is −4.36¢ (n=172 with a 30s mid) and the still-blind part of 3–11s is −1.87¢ (n=60). Once the table has arrived, the 3–11s fills that remain are not this slice.

Buys of the team that just died, 0–3s: **84 fills, 30s markout −5.35¢** (−$97, 56 losers and 19 winners among those with a mid). That is the clean "we were buying the token of the team that died" set. It does not use inventory. "Held" uses `position_after ∓ size` on that token (`match_worker.py:690` writes engine size after the fill). A file-order running sum disagrees with `position_after` on 757 fills, because some rows are journaled out of trade-time order (example `grid-2964619-m1`, a 20:14:00 sell written after a 20:14:01 sell). Hurt counts in the table use the engine before-size when the fill is on the victim token.

Restricting to the 158 exact signal alignments leaves the 0–3s markout at −3.52¢ (158 fills). Same sign, slightly smaller.

The pre-scoreboard bin is the ~2.8s wire delay: the kill's `occurredAt` is already before the fill, and the scoreboard frame has not been received. Those 87 fills are as bad as the 0–3s bin (−4.64¢) and a cancel-on-scoreboard rule cannot see them yet.

## Cancel the victim token for 12 seconds

Rule: after each scoreboard kill, treat every fill of the victim's token in the next 12s as cancelled. 12s covers the 8.3s table lag plus a few seconds. This is every resting order on that token, buys and sells.

| | LoL | Dota GRID |
|---|---:|---:|
| fills cancelled | 169 (107 BUY $1,468, 62 SELL $916) | 182 (116 BUY $5,075, 66 SELL $2,609) |
| of which table had not arrived | 137 | 153 |
| 30s mids | 149 | 155 |
| losers avoided (30s) | **93 fills, −$171** | 73 fills, −$293 |
| winners given up (30s) | **56 fills, +$44** | 82 fills, +$151 |
| net 30s markout of the set | **−$127** | −$143 |
| net 10s | −$108 | −$99 |
| net 60s | **−$104** | **+$18** |

On LoL the cancelled set is still a loss at 60s, so the 12s pause does not only dodge a 10-second wiggle. On Dota the 60s markout of the same set is positive: the rule avoids 30s pain and gives up fills that were fine a minute later. LoL's post-kill fills do not recover that way (−4.33¢ at 60s in the 0–3s bin).

Relative to LoL's −$452 total 30s markout, dropping this set would have left about −$325. Relative to the −$1,059 of gross negative markout, the rule catches $171 (16%). It does not touch the 87 pre-scoreboard fills (−$152 of loss) or the 785 fills with no kill in 60s (−$359 of loss, mostly offset by +$342 of winners).

## Dota GRID, same bins

152 live maps, `feed_source=grid`, **1,117 Polymarket fills**. Total 30s markout **−$229** (gross negative −$1,314, gross positive +$1,085).

| class | fills | BUY notional share | SELL notional share | 30s | 60s | 30s loss $ | share of 30s loss $ |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0–3s | 174 | 13.8% ($3,601) | 13.1% ($3,490) | −1.65¢ | −0.05¢ | −259 | 20% |
| 3–11s | 154 | 12.5% ($3,268) | 20.2% ($5,352) | −0.91¢ | −0.39¢ | −278 | 21% |
| 11–30s | 191 | 13.4% | 20.1% | −0.16¢ | −0.91¢ | −222 | 17% |
| 30–60s | 223 | 19.0% | 18.8% | +0.58¢ | −0.61¢ | −168 | 13% |
| no kill in 60s | 318 | 37.9% ($9,897) | 22.1% | −0.07¢ | +0.29¢ | −299 | 23% |
| event already, scoreboard not yet | 57 | 3.3% | 5.7% | +0.16¢ | +0.55¢ | −89 | 7% |

Dota also loses money on fills in the first 11s after a scoreboard kill, and that blind slice is 282 fills and 32% of negative 30s dollars. Two differences from LoL: the 0–3s gap is −1.65¢ rather than −4.30¢, and by 60s the 0–3s bin is back to −0.05¢. The pre-scoreboard bin is not a LoL-style loss. That matches lag-devin's market-speed result (LoL half-move ~3.6s, Dota ~9.6s): LoL's book has already repriced when these fills print, Dota's often has not.

## Limits

Rows are repeated fills on 207 LoL maps. The −4.30¢ versus −0.08¢ gap is large relative to the no-kill bin; the exact-alignment cut (−3.52¢) agrees. **verified** for the bin means, the 8.30s table lag, and the 12s cancel counts. **likely** that a live cancel would match this counterfactual: it assumes every victim-token fill in the 12s was a resting order that could have been pulled, and that pulling it would not have changed later quotes. 167 of 169 LoL cancelled fills are maker, so they were resting.

11% of scoreboard kills (683 / 6,574) had no table death inside 40s, so `table_unseen` can mean "no matching death" rather than "not yet". The lag distribution of the matched ones is tight (p10–p90 is 0.4s wide), and 195 of 198 fills in the 0–3s bin are unseen, which is what an 8.3s lag predicts.

No settlement PnL is attributed. The dollar figures are mark-to-mid.

## Command

```
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader && PYTHONPATH=src uv run python \
  /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok/kill_fills.py
```

Output: `work/clock-grok/kill_fills.json`. 534 maps queued, 175 with no live fills, 0 errors. Printed `lol maps 207 fills 1492` and `dota maps 152 fills 1117`.
