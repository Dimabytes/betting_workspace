# Brief: bt-devin — backtest realism

Your name: `bt-devin`. Report: `$R/reports/bt-devin.md`. Work dir: `$R/work/bt-devin/`.

Goal: explain why the LoL backtest is so profitable and whether live can realize it.
Read all backtest artifacts you need. Do not launch new backtests. If a backtest on the
live-traded maps is needed, write the exact command and the data it needs.

1. The positive LoL BUY markout at 30 s (+0.51¢ vs Dota −0.38¢). Decompose by game second,
   time since the last game event, league, price level, and fill origin. Check the queue
   fill model (`fill_model=queue`, `execution_priority=[onchain_fills]`): can our BUY fill
   when the real market traded at our price only later or earlier? Do fills need a
   trade-through or only a touch? Can our fill happen on trades that were caused by the same
   game event our signal reacts to, but before live could have placed the order?
2. Replay timing, from the backtest side: use `quote_events.parquet` and `fills.parquet` to
   measure the delay between the livestats state wall time and our order placement. Compare
   with live `session.jsonl` (signal `second` vs quote/fill `ts_utc`, `match.json` `horn_at_utc`).
3. Size: backtest `base_size_usdc=300` (3 layers x $100) vs live $5–$20 LoL, $60 Dota.
   How does size change fill probability, markout, PnL per $?
4. Universe: league mix and time period of the 945 validation maps vs live maps. Per-league
   backtest PnL per map (seed mean) → expected live PnL for the actual live league mix since
   2026-09-18 and since 2026-08-31 at the live clip. Give a confidence interval from the
   per-map distribution. Is the live result inside it?
5. `$E/data/backtests/lol_maker/livecohort_*`: what these runs are and their results. Do any
   LIVE-catalog maps overlap live-traded maps (market slug / date)? If yes, compare per-map
   backtest vs live fills and PnL.
