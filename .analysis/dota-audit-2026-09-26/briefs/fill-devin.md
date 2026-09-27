# Brief: fill-devin — backtest execution realism in the Nautilus framework; settlement tail

Report: `$R/reports/fill-devin.md`. Work dir: `$R/work/fill-devin/`.
fill-grok works on the accounting side of the same area. Think independently.

## Scope (read line by line)

- `../prediction-market-backtesting` at the pinned commit: matching engine, L2 book replay, queue-position
  fill model (`fill_model=queue`), latency model, how trades (onchain fills) drive fills, fees, expiration.
- `$E` usage: `src/backtest/run.py` (latency config near line 282, `rewrite_replay_instrument`,
  `install_settlement_compatibility`, replay boundaries), `strategy.py` (order submit/cancel),
  `maker_orders.py`, `strip_own_book.py`, `quote_store.py`, `telonex_local.py` (book replay input), `marks.py`,
  `postprocess.py`.
- Data: LIVE catalog seeds (`fills.parquet`, `quote_events.parquet`, `results.parquet`), books and onchain
  fills for a few maps.

## Questions

1. **Maker fill mechanics.** How does a BUY at the best bid get filled: queue position at join, how the queue
   advances (trades at our price? book size drops?), partial fills, trade-through. Can a fill come from a trade
   that happened before our order was placed (timestamp order, same-second batching, snapshot granularity)?
   Can our own resting size be counted as liquidity that others consume, or our own orders be double counted
   in the book (`strip_own_book.py`)?
2. **Latency.** `StaticLatencyConfig(insert=85 ms, update=85 ms, cancel=0.0)` while live cancels take real
   time; live quoter tick 2 s and debounce 100 ms. Can the backtest cancel "instantly" before adverse fills in
   a way live cannot? Estimate on seed 0 how many fills a realistic cancel latency would add or remove (use
   quote_events timing vs trades).
3. **Book data granularity.** Snapshot frequency (paid Telonex vs collector), what the engine does between
   snapshots, onchain fill timestamps (block time) vs book timestamps, ordering when a trade time is earlier
   than the snapshot that should contain it.
4. **Settlement tail (lead B in context).** 39/39 held-to-end positions won (Dota seed 0). Find the mechanism:
   when the replay ends (`boundary_ns`, `market_closed_at` vs `game_ended_at`); which orders are live at the
   end; can SELLs fill after game end in the replay; is settlement mapped to the right token
   (`radiant_token_index`); are losers closed through post-game books that live would not see; are winners and
   losers marked differently (settlement vs `terminal_bid`)? For each held-to-end position, show the token
   price path and our SELL price over the last minutes.
5. **Fees and delays.** Engine taker fee is 0 and rebates are added in postprocess. Are they right against
   Polymarket's current fee schedule for sports markets? Are there taker fills (marketable orders) that should
   pay a fee? Is `secondsDelay=1` for marketable orders on sports markets modeled?
6. **Venue rules.** Min order size, tick-size changes near the extremes, 2-decimal share rounding: modeled as
   in live?
7. **Capital.** `engine_starting_balance=1e6`: can the backtest hold exposure that live caps would forbid?
8. **Speed.** Where backtest time goes today (compare with
   `$W/.learnings/esports-trader-backtest-performance-2026-09-07.md`).

## Allowed

Read LIVE backtest outputs, books and onchain fills for a few maps. Small replays of 1–3 maps through project
code are allowed if each takes under ~10 minutes. No full backtest.
