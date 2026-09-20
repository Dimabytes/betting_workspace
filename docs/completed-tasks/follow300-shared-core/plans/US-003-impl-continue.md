Continue. You went idle after map 8911784562 failed. Do not stop.

1. Read the failure: `pytest tests/test_extraction_oracle.py::test_seed0_map_replay_matches_extraction_golden[dota-8911784562-14] -q --tb=short` (one process).
2. Fix the adapter/core timing so identity matches. Compact tests first (`-k "not test_seed0_map_replay"`).
3. Then one map per process in this order: 8837869969, 8911784562, 8933879286, lol 115564793879469302.
4. Never 4 maps in one interpreter.
5. Do not recapture goldens. Do not git stash.
6. When the four maps pass (or you can prove remaining drift is a labeled Nautilus engine fault), commit esports-trader, append progress.txt, write `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003-impl-report.md`, leave feature.json US-003 `passes: false`.
7. Reply with only that report path.
