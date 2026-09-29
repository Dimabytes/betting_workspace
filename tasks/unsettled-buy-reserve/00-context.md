# Orchestration context: unsettled-buy-reserve

Task: `tasks/unsettled-buy-reserve/feature.json` (this dir = run dir `R`). Code project: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (read its `AGENTS.md` before any work).
Workspace rules: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/AGENTS.md`.
Skills live in `/Users/dimabytes/.claude/skills/<skill>/SKILL.md`. Read the named SKILL.md fully and follow it.

Roles: planner (sol), implementer (grok-fast), step reviewer (grok-fast), comments reviewer (swe-high devin).

## Hard rules

- You may run with auto-approve. Never delete, overwrite, or destroy anything you did not create this run: no `git clean`/`reset --hard`/`checkout --`/`stash`, no `kill`/`pkill` outside your own processes, no dropping/truncating files, rows, tables, or branches.
- Only the implementer edits code in `esports-trader`. Planner and reviewers are read-only on code repos; they write only their plan/report file and `work/<name>/`.
- `../poly-maker` is frozen: never edit it.
- No `git push`. Implementer commits on `main` only if the implement skill says so.
- No SSH to prod/VPS. Run Python from the project venv.
- Every claim with `file:line`.
- Put `Status: FINAL` on line 2 of your report only when it is complete.

## Running tests

Run from `esports-trader/`:
`PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-<your-agent-name> -q tests/test_<file>.py`

- Always pass your own `--basetemp`: several agents run pytest at once.
- Full suite without the slow golden replays: `tests -k "not current_policy_smoke"`. Run the goldens only if the step changes replay/backtest/policy behaviour.
- Plain `make test` runs serial: always set `PYTEST_N=10`.
