# Brief: fill-grok — backtest execution and PnL accounting code; settlement tail with exact numbers

Report: `$R/reports/fill-grok.md`. Work dir: `$R/work/fill-grok/`.
fill-devin works on the framework/fill-model side of the same area. Think independently.

## Scope (read line by line)

`src/backtest/{strategy.py, maker_orders.py, postprocess.py, marks.py, results.py, report.py,
report_capital.py, report_types.py, report_io.py, wallet_path.py, telemetry.py, quote_store.py,
strip_own_book.py}` and the parts of `run.py` that finalize results. Data: the LIVE catalog
(`$E/data/backtests/dota_maker/LIVE/seed{0,1,2}`) and `data/new_processed/match_catalog`.

## Questions

1. **Settlement tail (lead B).** Reproduce "engine PnL = cash flow + settlement" for Dota LIVE seeds 0–2. List
   every held-to-end position: map, token, qty, average price, entry second, last SELL price and time, book at
   the end, `radiant_win`, token mapping. Then find the mechanism. Is it the policy (SELL rests at fair above
   the market; winners run away from it) or a bug? Candidate bugs: settlement value with the wrong token index
   for some maps; SELLs of losers filled against post-game books; losers closed by a `terminal_bid` mark hidden
   inside cash flow; a filter on which positions count as "held". Show the counterfactual: how losers exit.
2. **Accounting.** Cash flow per fill, rebate formula, markouts (`fill_ts + h` on the paired token mid; "past
   the mids, the leg's closing bid, else settlement"). Can a markout use the settlement value (the future
   outcome) and inflate markout metrics that drive decisions? MTM drawdown, required cash. Check that
   `summary.json` equals the sums of `results.parquet` / `fills.parquet`.
3. **Paired legs.** Both YES and NO legs are loaded. Is buying NO treated independently of YES? Are positions on
   both legs netted/merged like on Polymarket? Any double counting?
4. **Misleading metrics.** Metrics computed on a biased subset (e.g. markouts that drop maps without mids), or
   report labels that do not match the math.
5. **New series code.** `src/backtest/series_run.py` is being edited by the owner right now (uncommitted);
   skip its WIP. But check that series changes in shared functions (`results.py`, `postprocess.py`, `marks.py`)
   did not change map-run accounting.

Deliver exact numbers with the commands that produced them.
