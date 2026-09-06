You are the IMPLEMENT agent for US-003. Continue the existing uncommitted WIP. Do not start over. Do not start US-004+. Do NOT set feature.json `passes: true`.

## Skills
1. `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md` EXCEPT leave US-003 `passes: false`. Append progress.txt. Commit esports-trader.
2. Ponytail full: `/Users/dimabytes/.claude/plugins/cache/ponytail/ponytail/4.8.4/skills/ponytail/SKILL.md`
3. `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/AGENTS.md`
4. poly-maker FROZEN. No edits under `/Users/dimabytes/work/polymarket/dota_2_bot/poly-maker`.

## Plan
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003.md`

Product: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `ladder-experiment` (HEAD `95168c6`). WIP already modified: `src/backtest/strategy.py`, `extraction_identity.py`, `src/strategy/quoting.py`, `tests/test_extraction_oracle.py`. Subprocess isolation for seed-0 maps is already in the test file. Keep it. Never run 4 Nautilus kernels in one process (`logging system was already initialized`).

## Known remaining identity delta (map 8837869969)
Fills match. Counts match (73 submitted / 71 accepted / 67 canceled). PnL matches.
First mismatch was `place_cancel[218]`: actual SELL submit `ts_ns=1780565948867000081` vs golden `1780565949612000000` (~745ms early, same price 0.78 qty 358.09). Extra `+81` ns looks like 1ns cancel-send seq leaking into submit ts, or a Wake-on-fill/cutoff placing SELL one timer tick early.
Plan already warned: cutoff/game-end must not full-evaluate (would place SELL early); SELL-to-flat Wake-at-fill for BUY cancels; BUY fills must NOT requote.

## How to iterate (cheap)
- Compact tests: `pytest tests/test_extraction_oracle.py tests/test_backtest_maker.py tests/test_strategy_core.py -q -k "not test_seed0_map_replay"`
- One map, one process: `pytest tests/test_extraction_oracle.py::test_seed0_map_replay_matches_extraction_golden[dota-8837869969-6] -q --tb=line`
- Other three maps only after that one matches, each in its own process.
- Do not recapture/overwrite US-001 goldens. Do not `git stash`. Do not run full pytest.

## Git
Commit in esports-trader when identity tests pass: `feat: [US-003] - Wire the backtest adapter and prove extraction identity`
Do not push. Do not skip hooks. Do not commit parquet dumps or betting_workspace.

## Progress.txt
APPEND only in betting_workspace `current-task/progress.txt`.

## Return
Write the complete report to `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-003-impl-report.md` then reply with only that path.
Include SHA, files, four-map results, other pytest, ruff/basedpyright, passes still false, deviations.
