# two-sided-live-b: shared context for every agent

## Goal

Implement `tasks/two-sided-live-b/feature.json` step by step. One step per agent. The owner is asleep. Nobody answers questions: decide yourself, write the decision and the reason in your report.

## Paths

- Workspace (task files, skills): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace` (W)
- Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (E)
- Task folder: `W/tasks/two-sided-live-b/` — `feature.json`, `progress.txt`, `plans/<STEP-ID>.md`
- Run dir: `W/tasks/two-sided-live-b/fleet/` (R) — `reports/<your name>.md`, scratch in `work/<your name>/`
- Skills: `~/.agents/skills/<skill-name>/SKILL.md`. Read the skill file the brief names and follow it.

Before any work read `W/AGENTS.md` and `E/AGENTS.md`. Follow them. Run Python in E through `uv run` (project venv).

## Hard rules

- You run with auto-approve. No permission prompt stops you. Never delete, overwrite, or destroy anything you did not create this run: no `git clean`, `git reset --hard`, `git checkout --`, `git stash drop`, `git push`, no `rm` of files you did not create, no `kill`/`pkill` outside your own processes, no dropping or truncating data, dirs, tables, or branches. If something is in the way, stop and report.
- Work on `main` in E. Commits only when your brief says so. Never `git push`. Never rewrite history (no amend of commits you did not make, no rebase).
- `../poly-maker` is frozen. Never edit it.
- No SSH to the VPS. No live trading, no orders, no on-chain transactions. Never use the `*_B` keys from E/.env and never send a merge. `docker compose config` is allowed; `docker compose up`/`run`/`build` is not.
- Only one implementer works at a time. The repo state is yours, but do not touch `data/` beyond what a backtest writes under `data/backtests`.
- Keep work files under ~200 MB each.
- Write the report to the file, not to chat. Update it as you go. When the report is complete, make its line 2 exactly: `Status: FINAL`. Then reply with only the report path.
- Never print secret values. Do not `cat`/`grep` `.env`. Plain `docker compose config` inlines `env_file` values (private keys) into its output: always use `docker compose config --no-interpolate`, `--services`, or `--format json | jq` that selects only key names or the specific non-secret fields you need.
