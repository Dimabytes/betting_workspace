# Betting Workspace

Это рабочее пространство агентов: навыки, памятки по соседним репозиториям, задачи и исследования. Код продуктов находится в соседних каталогах.

```text
<parent>/
├─ betting_workspace/                 # инструкции, .shared-skills/, .learnings/, tasks/, investigations/
├─ esports-trader/                    # сбор, обучение, бэктест и live-трейдер: Dota 2, LoL, CS2
├─ poly-maker/                        # замороженный форк Polymarket maker bot
├─ polymarket-collector/              # архив рынков Dota 2, LoL и CS2 на VPS
└─ prediction-market-backtesting/     # приватный Nautilus-фреймворк; upstream только для чтения
```

## Project briefings

Before working on a sibling project, read its briefing. Paths are relative to this repo:

- `../esports-trader` → `../esports-trader/AGENTS.md`.
- `../poly-maker` → `.learnings/poly-maker.md`.
- `../polymarket-collector` → `.learnings/polymarket-collector.md`.
- `../prediction-market-backtesting` → `.learnings/prediction-market-backtesting.md`.

## Git rules

- Do not `git push` unless the user explicitly asks.
- Run git operations inside the repo being changed.
- Work on `main` by default, unless the user says otherwise.
- Before creating an esports-trader worktree or running a backtest from one, read `../esports-trader/docs/worktree.md`. Sync its `.venv` once before sharded runs. Do not symlink `data/backtests`.
- `../prediction-market-backtesting`: `origin` is our private `Dimabytes/prediction-market-backtesting`; `upstream` is `evan-kolberg/prediction-market-backtesting`. Never push to `upstream` or open PRs there.
- `../poly-maker` is frozen. Do not edit it. Both our fork and its upstream are read-only.

## Documentation rules

- Put every audit, postmortem, experiment report, keep/drop decision and other one-off analysis in `investigations/YYYY-MM-DD-<slug>/` in this repo. Do not create them in sibling repos or under `reports/`, `.analysis/`, `docs/analysis/`, `docs/experiments/`, `docs/superpowers/`, `artifacts/` or `runs/`.
- Use `report.md` for the writeup and `work/` for scripts, dumps, databases and supporting files. `investigations/` is gitignored; never use `git add -f` on it.
- Keep current esports-trader guides in `../esports-trader/docs/`. Keep only the context needed in every session in `AGENTS.md`; link to detailed procedures.
- `.learnings/` contains briefings for the other sibling projects, not esports-trader reports. Propose useful additions or corrections to the user; create, edit, move or delete a learning only after explicit user approval. Do not update it automatically after a task or investigation.
- `tasks/<name>/` holds a `feature.json` implementation. Herdr scratch belongs in `tasks/<name>/orchestrate/` (gitignored), never `fleet/`, `run/` or `herdr-series/`.
