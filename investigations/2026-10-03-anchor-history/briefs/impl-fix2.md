# Brief: impl fix round 2 — backtest crash

The orchestrator ran the Dota validation backtest on the new code. Two of six shards crashed in signal building:

```
src/backtest/signals.py:595 build_match_signals
ValueError: validation dataset has no usable signal rows for matches [8852555586]   (shard 1/6, seed 0)
ValueError: validation dataset has no usable signal rows for matches [8971371061]   (shard 3/6, seed 0)
```

Command (from esports-trader root):
`PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run --game dota --validation --name <x> --shard 1/6 --signal-cadence-seed 0 --model-dir data/experiments/hist-policy-20261003/model --model-dir-noxp data/new_model/research-noxp`
Full logs: `esports-trader/data/backtests/dota_maker/_logs/histfix-20261003/seed0/shard_{1,3}.log`.

Before the change, these matches ran (LIVE run `data/backtests/dota_maker/LIVE` has 810 selected matches). Most likely the new grid-v1 first-tick cut (`DOTA_GRID_V1_FIRST_TICK_SECOND=48`), the connect-only first tick, or history-invalid removal leaves zero rows for some maps.

Do:
1. Find the root cause for both matches (look at their validation rows).
2. Fix it the way live would behave: a map whose feed never gives a decision tick simply trades nothing (it must not crash the run, and must not silently drop the map from the universe in a way that changes the comparable match set — prefer keeping it with zero decisions; if that is impossible, say why and exclude it with an explicit, logged reason like the existing exclusions).
3. Check the LoL path for the same failure (`LOL_GRID_V1_FIRST_TICK_SECOND=76`).
4. Add one test that fails on the old code. Run the targeted tests + `pre-commit run --files`. Then smoke-run the exact command above with `--shard 1/6` and `--shard 3/6` using `--limit 5` or the narrowest flag that includes those matches, OR a small script that calls `build_match_signals` for those match ids; report the result. Do not run full backtests.
5. Commit locally on `main`. Clean tree.

Append `## Fix round 2` to `reports/impl.md` (line 2 `Status: WIP` while working, `Status: FINAL` when done).
