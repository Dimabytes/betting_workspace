You are the implementer for one step only: US-003. Do not plan a new design. Do not start US-004.

Read fully, in this order:

1. `/Users/dimabytes/.agents/skills/feature-json-implement-step/SKILL.md`
2. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/feature.json` (US-003 only)
3. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003.md` (Status: FINAL — this is the spec)
4. `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/AGENTS.md`
5. `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`

Work in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on branch `main`. HEAD at handoff is `185fdf2a`. The tree was clean.

Implement exactly the files and tests in the plan. Reuse `series_link`, the LoL cohort in `series_inputs`, `series_run.assemble_series_replay`, and `load_series_c`. No new dependency. No abstraction the plan does not ask for. The plan wins on file layout.

Do not edit `../poly-maker`, `../polymarket-collector`, onchain files (`telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`), live, discovery, `trading.toml`, or promote. Do not run seed backtests, `scripts/run_seeds.sh`, or `compare_backtests.py`.

Do not set `passes` in `feature.json`. The orchestrator sets that after review.

Append a short note to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/progress.txt`. Do not commit `betting_workspace`.

Commit only in `esports-trader`, and only if tests and typecheck pass. Message:

`feat: US-003 - Прогон серии LoL и выход после конца карты`

Do not push. Do not commit broken code. Do not `git clean`, `reset --hard`, or `checkout --`. Do not amend `185fdf2a`.

Verification, from the esports-trader repo. Do not run `make test` or the full suite (a live map may be open):

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_series_run.py tests/test_series_inputs.py tests/test_backtest_maker.py tests/test_lol_backtest.py tests/test_backtest.py -q
uv run python -m basedpyright
uv run ruff check . && uv run ruff format --check .
```

Known baseline, not yours to fix: `test_since_match` failures about `ok_quote_fraction`, and the trading-clip config failures. Do not expand the diff to chase them.

Scratch only under `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/work/us003impl/`.

When done, write the full report to:

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/herdr-series/reports/us003impl.md`

Line 1 is a title. Line 2 is exactly `Status: FINAL` only when implementation, tests, typecheck, and the commit are done. Until then line 2 is `Status: DRAFT`. Include the commit hash, the pytest/typecheck/ruff results, and any plan item you skipped and why.

Then reply in chat with only that report path.
