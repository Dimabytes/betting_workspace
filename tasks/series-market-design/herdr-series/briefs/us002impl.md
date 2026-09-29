You are the implementer for one step only: US-002. Do not plan a new design. Do not start US-003 or US-004.

Read fully, in this order:

1. `/Users/dimabytes/.agents/skills/feature-json-implement-step/SKILL.md`
2. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json` (US-002 only)
3. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-002.md` (Status: FINAL — this is the spec)
4. `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/AGENTS.md`
5. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`

Work in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on branch `main`. HEAD at handoff is `3a804f01`. The tree was clean.

Implement exactly the files and tests in the plan. Reuse the helpers the plan names. No new dependency. No abstraction the plan does not ask for. `feature.json` wins if it disagrees with the design doc. The plan wins on file layout and signatures.

Do not edit `../poly-maker`, `../polymarket-collector`, onchain files (`telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`), live, discovery, `trading.toml`, or promote. Do not build the US-003 exit window, the post-map SELL flag, the LoL series runner, or any seed backtest.

Do not set `passes` in `feature.json`. The orchestrator sets that after review.

Append a short note to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt` using the progress format in the implement skill. Do not commit `betting_workspace`.

Commit only in `esports-trader`, and only if tests and typecheck pass. Message:

`feat: US-002 - Выборка LoL и прогон серии Dota`

Do not push. Do not commit broken code. Do not `git clean`, `reset --hard`, or `checkout --`.

Verification, from the esports-trader repo. Do not run `make test`, `scripts/run_seeds.sh`, or a real backtest:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_series_inputs.py tests/test_series_link.py tests/test_series_run.py tests/test_backtest_validation.py tests/test_backtest_maker.py tests/test_lol_backtest.py -q
uv run python -m basedpyright
uv run ruff check . && uv run ruff format --check .
```

Also the no-engine CLI smoke from the plan: `--market series` without a branch errors; `--market map --series-branch line` errors; `--market series --game lol --series-branch line` errors.

Scratch only under `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/work/us002impl/`.

When done, write the full report to:

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/reports/us002impl.md`

Line 1 is a title. Line 2 is exactly `Status: FINAL` only when implementation, tests, typecheck, and the commit are done. Until then line 2 is `Status: DRAFT`. Include the commit hash, the pytest/typecheck/ruff results, and any plan item you skipped and why.

Then reply in chat with only that report path.
