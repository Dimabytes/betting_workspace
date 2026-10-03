# Run 2026-10-03: prior anchor + history policy (code only)

Code repo: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader (branch `main`).
Run dir: /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-03-anchor-history

Two plans, implemented in this order:
1. `plans/01-prior-anchor.md` — fallback prior anchor (horn − 90 − pause) in `load_anchors`.
2. `plans/02-history-policy.md` — one HistoryPolicy for train/backtest/live (Dota + LoL).

Scope of this run: CODE + UNIT TESTS ONLY. The orchestrator runs every data/train/backtest step later.

Agents:
- `impl` (Devin swe-2-max): implements both plans, then fixes review findings.
- `review` (Cursor grok-4.7-high): reviews the implementation.

## Hard rules (all agents)
- You run with auto-approve — no permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create this run: no `rm` of existing files, no `mv` onto existing files, no `git clean` / `git reset --hard` / `git checkout --` / `git stash`, no `git push`, no `kill`/`pkill` outside your own processes, no dropping/truncating data files, dirs, or branches.
- Never touch `data/` contents (no deletes, no rewrites of parquet/json/caches). Exception: golden files under `tests/fixtures/` that the plan says to recapture.
- Do NOT run: `make prices`, `make catalog`, `make prepare`, `make train`, `make lol-train`, `make lol-prepare`, `s05a_fetch_prices_history.py`, any backtest, any training. No SSH to the VPS. No deploy. No trader restart.
- Allowed: reading anything, editing code + tests in esports-trader, running targeted `uv run pytest <files>` (use `PYTEST_N=8` if you want speed), running the plan's small offline check scripts if they only read data.
- Do not edit `../poly-maker` (frozen).
- Follow `esports-trader/AGENTS.md` agent rules (action-verb function names, required args, frozen dataclasses instead of tuples/dicts, no narrating comments, no dead branches).
- Python only through `uv run` from the esports-trader root.
- Every claim in a report with `file:line` / command + output numbers.
- Put `Status: FINAL` on line 2 of your report only when it is complete.
