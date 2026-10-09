# Wallet B (live_b) post-mortem, 2026-10-08

## Goal

Wallet B runs the two-sided maker strategy on Polymarket Dota 2 map-winner markets (BLAST Slam, PARI Universe). Today it lost money. The owner wants to know:

1. Legging: we often hold one side and buy the second side late, at a bad price. How much did that cost, and how to stop it?
2. Waves: the market moves hard one way, we keep buying the falling token, and we sit on one side. How much did that cost?
3. Fast take: when one leg fills, take the other leg at once (taker), or after N seconds, or cut the skew/net cap. Does any of this make money?
4. Speed: does our execution speed matter? Python on the VPS vs a rewrite in Rust. Would faster cancels save money?
5. Anything else that would cut losses or make money.
6. Game data: we have live game state (GRID ~8 s delay, Oddin ~15 s delay) and a model. The strategy uses none of it now. Can game state help (pull quotes, widen, skew)?
7. Was today just bad luck?

The final goal is profit: understand the losses, and find a rule that makes money.

## Strategy (code on local `main`, same as the VPS)

Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (HEAD `94361587`; it contains VPS commits `76592d31` and `3041c390`).

- Constants: `src/strategy/two_sided.py`. ORDER_SHARES 20, NET_MAX_SHARES 30, SKEW_PER_SHARE 2e-4, BAND_HI 0.90, QUOTE_FROM_SECOND -60, REPRICE_HOLD_NS 300 ms, REPRICE_NOW_TICKS 2, MAX_BID_SUM_TICKS 99, HALF_SPREAD_TICKS 3 before `76592d31`, 6 after.
- Quote logic: `src/strategy/two_sided_quoting.py`. Fair = book mid (`core_book_p`), not the model. Two post-only BUY bids: YES at fair − half − skew, NO at (1 − fair) − half + skew. Skew = 2e-4 · 4p(1−p) · net (at net 30 that is only 0.6 tick). The side that grows |net| shrinks linearly to 0 at |net| = 30. No SELL ever. Bids are pulled on pause, game end, stale book/signal, band (mid > 0.90 or < 0.10), halt.
- Policy wiring: `src/strategy/policy.py` (`two_sided_policy`). Live worker: `src/trader/two_sided_worker.py:104-111` reads `config_b/trading.toml` `[engine]`: debounce 100 ms, fallback timer = `quoter_tick_s` 2.0 s (corrected; an earlier version of this file said 250 ms / 1 s).
- Merge: YES+NO pairs merge to $1 when held pair value ≥ $130 and ≥ 5 pairs; a final merge runs at map end. The unpaired tail settles at 0 or 1.
- Fees: taker fee per share = 0.05 · p · (1 − p) (`src/shared/utils/trading.py:7,48`). Makers get a rebate (same file, `REBATE_RATE`). Tick 0.01, min order 5 shares.
- Backtest of this strategy: `src/backtest/two_sided_strategy.py`, `src/backtest/two_sided.py`, run with `python -m backtest.run --validation --archives-only --strategy two-sided`. Prior sweep report: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/two-sided-live-b/fleet/reports/bt-sweep.md`. Prior run dirs: `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-{n30,n30-nq,h6-n30,h6-n30-nq,...}/_archive/` (`results.parquet`, `fills.parquet`, `summary.json`).
- Backtest per-map PnL (engine_pnl + maker_rebate, 307 maps): n30 half-spread 3 strict queue mean +4.37, median +3.37, p5 −7.82, 35% maps negative. h6 n30 strict: mean +4.21, p5 −7.55. (Orchestrator numbers, re-check them.)

## Today's live maps (10 traded maps)

| match_id | teams | feed | half spread | telegram net | fills |
|---|---|---|---|---:|---:|
| grid-3011820-m1 | LGD Gaming vs Team Yandex m1 | grid | 3 | +5.93 | 57 |
| grid-3011820-m2 | LGD Gaming vs Team Yandex m2 | grid | 3 | −16.50 | 81 |
| grid-3011820-m3 | LGD Gaming vs Team Yandex m3 | grid | 3 | −6.52 | 12 |
| 9034789047 | Team Synapse vs Blasterbl m1 | oddin | 3 | +5.94 | 111 |
| grid-3011821-m1 | Aurora vs 1win m1 | grid | 3 | −29.79 | 183 |
| 9034957701 | Team Synapse vs Blasterbl m2 | oddin | 3 | −2.69 | 38 |
| grid-3011821-m2 | Aurora vs 1win m2 | grid | 6 | −0.76 | 15 |
| 9035220432 | Blasterbl vs LEGION m1 | oddin | 6 | +4.00 | 6 |
| grid-3011822-m1 | PARIVISION vs Team Yandex m1 | grid | 6 | +1.44 | 12 |
| 9035318247 | LEGION vs Blasterbl m2 | oddin | 6 | −3.12 | 8 |

Half spread comes from `session_start.git_commit` (`f0fe4d33` = 3 ticks, `76592d31` = 6 ticks). Telegram net = realized + inventory mark + estimated rebate. Sum of telegram net: −42.08, of which estimated rebate +13.09. The Polymarket day number (cash + open marks, rebate not paid yet): −58.81 (525 buys $4103.22, 34 merges $4043.15). The map `grid-3011822-m2` was still running at download time and is not in the data.

## Data (local copies, read-only)

`D=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-08-liveb-postmortem/work/data`

- `$D/trader_live_b/<match_id>/session.jsonl`: kinds `session_start`, `signal` (each feed tick: book best bid/ask, `game_snapshot`, `recorded_at_utc`, `feed_received_at_utc`, `pos_yes`, `pos_no`), `quote` (placed/canceled orders, has `second` but no timestamp), `fill` (`token_id`, `price`, `size`, `is_maker`, `position_after`, `ts_utc`), `session_end`.
- `$D/trader_live_b/<match_id>/match.json`: teams, `market.yes_token_id`, `market.no_token_id`, `yes_is_radiant`, `horn_at_utc`, `final.winner`, `grid_delay_s`.
- `$D/trader_live_b/<match_id>/grid_state.jsonl` or `oddin_state.jsonl`: raw game feed frames with `received_at_utc`. Large; stream them.
- `$D/trader_live_b/wallet/engine_journal/live.jsonl` (29,641 lines): `orders_out` (our order decision, local `ts` in seconds), `user_order` (exchange order events PLACEMENT / UPDATE / CANCELLATION with exchange `timestamp` ms), `user_trade` (our trades). Covers all wallet B maps of the day.
- `$D/trader_live_b/wallet/live.db`: copy of wallet B sqlite (`fill_ledger` etc.). Open read-only (`sqlite3 'file:...?mode=ro'`).
- `$D/book_journal_20261008_liveb.jsonl.gz` (384 MB gz, 3.7 M lines): raw Polymarket market WebSocket events for the 10 condition ids, 2026-10-08 00:00–~18:40 UTC, from the collector on the same VPS. `kind=market_event` with `payload.type` in `book` (full snapshot), `price_change` (level updates: `price`, `side`, `size` = new size at that level, plus `bestBid`/`bestAsk`), `last_trade_price` (trades), `tick_size_change`. `receivedAtUs` = collector receive time (µs). Lines with `kind=catalog_event` are metadata; skip them. Our own resting orders are inside this book. Stream it with `gzip.open`; never load it whole into memory. Split it per market once into your work dir if you need repeated passes.
- Historical books for the backtest maps sit in the esports-trader archives that `backtest.run --archives-only` reads; find them from `src/backtest/run.py` if your brief needs them.

## Python

Use `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python` (pandas, pyarrow, duckdb, numpy are installed; polars is not). To import project code set `PYTHONPATH=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src`. Keep your scripts in `work/<name>/` and run them from there, not from `work/data`.

## Hard rules

- You may run with auto-approve, so no permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run: no `rm`, no `mv` onto existing files, no `git clean`/`reset --hard`/`checkout --`, no `kill`/`pkill` outside your own `work/<name>/` processes, no dropping/truncating files, dirs, rows, tables, or branches. If something is in the way, write beside it or stop and report.
- Read-only on every code repo (`esports-trader`, `poly-maker`, `polymarket-collector`, `prediction-market-backtesting`, `betting_workspace`). Write only to `work/<name>/` and your report. The owner may edit repos during the run; read `git show HEAD:<path>` if a file looks half-written.
- `work/data/` is shared and read-only. Do not write into it.
- No SSH, no access to the VPS or any production system, no orders, no Polymarket API writes. Public read-only HTTP is allowed only if your brief says so.
- No accounts, no purchases, no signups.
- Do not run `backtest.run` or other long jobs unless your brief allows it. If allowed, write output only under `work/<name>/`.
- Keep your work files under 3 GB in total.
- Every claim needs `file:line`, or a command plus its numbers. Mark each claim `verified`, `likely`, or `speculative`.
- Write the report in English, plain and short. Lead with the answer.
- Put `Status: FINAL` on line 2 of the report only when it is complete. Until then line 2 is `Status: WIP`.

## Report format

```
# <name> — <topic>
Status: WIP | FINAL

## Answer (5–10 bullets, numbers in each)
## Method (data, script paths, assumptions)
## Results (tables)
## Recommendations (ranked by expected $ per map, with confidence)
## Refuted / open questions
```

## Agents in this run

| name | model | topic |
|---|---|---|
| sim-grok | Cursor grok-4.7-high | Book-replay simulator, counterfactual strategy sweep (independent of sim-devin) |
| sim-devin | Devin SWE-2 Max | Book-replay simulator, counterfactual strategy sweep (independent of sim-grok) |
| lat-grok | Cursor grok-4.7-high | Execution speed, pick-offs, stale quotes |
| game-devin | Devin SWE-2 Max | Game state as a signal for the maker |
| pnl-haiku | Claude Haiku | Per-map PnL breakdown: legging, waves, tail |
| gap-haiku | Claude Haiku | Backtest vs live gap, and bad luck vs structure |

Do not read other agents' reports or work dirs unless your brief says so.
