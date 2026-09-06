You are the same US-003 IMPLEMENT agent, resumed after Herdr review. The parent does not decide what to fix — you decide. Fix what you agree with; skip nits you disagree with; note why in progress.txt. Then commit in esports-trader if you changed code. Do NOT set feature.json `passes: true`. Do not start US-004.

Read in full: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003-review.md
Plan: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003.md
HEAD: 7b2b65b on ladder-experiment.

poly-maker frozen. Ponytail + esports-trader/AGENTS.md.

After fixes:
- Compact pytest: tests/test_extraction_oracle.py tests/test_backtest_maker.py tests/test_strategy_core.py -k "not test_seed0_map_replay"
- Identity: one map per process (subprocess isolation already in the test). If you touch quoting/lifecycle/engine/adapter, re-run all four maps in separate processes. Do not recapture goldens. Do not git stash.
- ruff + basedpyright on touched files
- Commit: `fix: [US-003] - address review findings`  Do not push.
- APPEND progress.txt. Leave passes false.
- Write /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003-fix-report.md with SHA, what you fixed vs skipped, checks. Last chat line: only that path.
