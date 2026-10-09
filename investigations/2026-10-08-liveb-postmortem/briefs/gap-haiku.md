# Brief: gap-haiku — backtest vs live gap, bad luck vs structure

## Question

The backtest of this exact strategy (307 maps) showed about +$4 per map. Live today showed about −$4 to −$6 per map over 10 maps. Is this bad luck, or does the backtest miss something?

## Task

1. Backtest distribution. Read `results.parquet` and `fills.parquet` of `esports-trader/data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-full-n30/_archive/`, `...ts-full-n30-nq/`, `...ts-full-h6-n30/`, `...ts-full-h6-n30-nq/`. Per-map PnL = `engine_pnl + maker_rebate`. Report mean, median, sd, p5, p25, share negative, and fills per map, pairs per map, tail per map.
2. Luck test. Draw 10 maps at random from the backtest (bootstrap 100,000 times; 6 from h3 and 4 from h6 to match today). How often is the 10-map sum ≤ today's sum? Use both today numbers: telegram sum −42.08 (with estimated rebate) and Polymarket −58.81 (no rebate). Same test on "number of maps below the backtest p5".
3. Structural gaps. Compare live today vs the backtest, per map: fills per map, fill size, share of fills that are on the losing side of the final result, average markout 30 s after a fill (live: use `signal` mids in `session.jsonl`; backtest: use `fills.parquet` plus whatever price series the backtest stores; if it stores none, say so), pair cost, tail size. Which numbers differ most?
4. Read how the backtest fills orders (`src/backtest/two_sided_strategy.py`, `src/backtest/run.py`, and the Nautilus side in `/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting` if needed): queue position, latency, trade-through rule, whether our own orders change the book. List each assumption that is more optimistic than live.
5. Market selection. The backtest maps come from many leagues; live B trades only BLAST Slam and PARI Universe. If the backtest data has league or tier info, compare per-map PnL for these two leagues (or similar tier) vs the rest.

## Output

Report: `$R/reports/gap-haiku.md`. Scripts: `$R/work/gap-haiku/`. Do not run `backtest.run`. Read existing outputs only.
