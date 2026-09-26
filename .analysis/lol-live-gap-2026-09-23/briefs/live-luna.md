# Brief: live-luna — live tapes, LoL vs Dota

Your name: `live-luna`. Report: `$R/reports/live-luna.md`. Work dir: `$R/work/live-luna/`.

Goal: quantify how LoL live trading differs from Dota live trading, from the live tapes only.
Data: `$E/data/trader/*/` (`match.json` `game`, `session.jsonl`). Use `execution_mode=live`
(from `session_start`). Periods: since 2026-08-31, and separately since 2026-09-18.

1. Per map: fills, BUY notional, SELL notional, net (`session_end` `net_cash` +
   `inventory_value`, or the per-map net in `$R/work/shared/*_summary.txt`), clip (infer from
   order size × price), traded token (yes/no via `match.json` `market.yes_token_id`), entry
   second, hold time. PnL per $ of BUY notional, LoL vs Dota.
2. Markout per fill: mid of the traded token at +10 s, +30 s, +60 s, +300 s after the fill
   (from later `signal` rows `yes_mid` / `no_mid`, matched by wall time; `fill.ts_utc` is
   wall time; signal rows carry game `second` only, so map second → wall time with the
   fill rows or `match.json` `horn_at_utc`; state how you did it). BUY and SELL separately.
   Compare with the backtest: LoL BUY +0.51¢ @30 s, Dota BUY −0.38¢ @30 s.
3. Model vs market: on `reason=model` rows, predicted delta = `radiant_fair − market_p_radiant`
   (check orientation). Realized = radiant mid 300 s later minus now. Binned calibration and
   correlation, LoL vs Dota. If LoL live predictions do not relate to realized moves, say so.
4. Feed health: histograms of `reason` and `entry_block` per game; share of `stale`, `paused`,
   `missing_book`; first `reason=model` second per map; gaps between consecutive signal seconds.
5. League mix (`match.json` `tournament`) and per-league live results for LoL.
