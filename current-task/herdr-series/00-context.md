# Series-market herdr run

Goal: drive `current-task/feature.json` (esports-trader series market, backtest only) one user step at a time. This run dir is scratch for Herdr agents. The code repo is `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`.

## Hard rules

You run with auto-approve. No permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run: no `rm`, no `mv` onto existing files, no `git clean` / `reset --hard` / `checkout --`, no `kill` / `pkill` outside your own `work/<name>/` processes, no dropping or truncating files, dirs, rows, tables, or branches. If something is in the way, write beside it or stop and report.

- Do not edit `../poly-maker` or `../polymarket-collector`.
- Do not edit esports-trader onchain files: `telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`.
- No SSH to prod. No accounts, no money, no signups.
- Browser only via `agent-browser --session <your-name>`. Never `agent-browser close --all`.
- No live trading, no `trading.toml` profile edits, no promote, no discovery.
- `feature.json` wins where it disagrees with the design doc.
- Every claim in a plan needs `file:line` or a command you actually ran. Mark `verified` / `likely` / `speculative`.
- Put `Status: FINAL` on line 2 of the deliverable only when it is complete. Until then line 2 is `Status: DRAFT`.

## Agent list

- `us001plan` — plan US-001 only. Read-only on esports-trader. Write the plan, nothing else.
