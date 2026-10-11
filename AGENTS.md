## Workspace

This repo is `betting_workspace` — the AI workspace (skills, learnings). It holds no product code. Sibling project repos are checked out next to `betting_workspace/`, not inside it. To work on one, go up one level and into the project (e.g. from this repo's root run `cd ../esports-trader`).

Expected layout:

```
<parent>/
├─ betting_workspace/                 # this repo: .shared-skills/, .learnings/, AGENTS.md
├─ esports-trader/                    # collect / train / backtest / live+paper trader
├─ poly-maker/                        # fork of a Polymarket maker bot
├─ polymarket-collector/              # TypeScript daemon that archives Dota 2 markets (runs on the VPS)
└─ prediction-market-backtesting/     # Nautilus backtesting library: our private repo, upstream read-only
```

Five folders, four code projects. This workspace is agent instructions only.

Before any work on a sibling project (code, commands, tests, architecture) read the matching briefing (paths are relative to `betting_workspace/`):

`../esports-trader` -> `../esports-trader/AGENTS.md`

`../poly-maker` -> `.learnings/poly-maker.md`

`../polymarket-collector` -> `.learnings/polymarket-collector.md`

`../prediction-market-backtesting` -> `.learnings/prediction-market-backtesting.md`

## Git Rules

- Do not `git push` unless the user explicitly asks.
- All git operations (status, branch, pull, rebase, push, commit) run inside the sibling project repo you are changing.
- work on `main` by default, unless the user says otherwise.
- A git worktree of `esports-trader` is a second directory. Read `.learnings/worktree.md` before creating one or running a backtest from one. Sync its `.venv` once before sharded runs. Do not symlink `data/backtests`.
- `../prediction-market-backtesting`: `origin` is our private `Dimabytes/prediction-market-backtesting`, `upstream` is `evan-kolberg/prediction-market-backtesting`. Never push to `upstream`, never open PRs there.

## poly-maker is frozen

`../poly-maker` is our GitHub fork (`Dimabytes/poly-maker`) of `warproxxx/poly-maker`. Treat both as read-only.

Do not change `poly-maker`. This is a ban, not a preference.

- Do not edit files in `../poly-maker`.

## Investigations

One folder: `investigations/YYYY-MM-DD-<short-slug>/`.

That is the only place for an audit, postmortem, gap check, or any one-off analysis. Do not create a new top-level directory for one. Do not start a new one in `reports/`, `.analysis/`, `docs/analysis/`, `artifacts/`, `runs/`, or under `docs/experiments/` in a sibling repo. Those names are gitignored.

`investigations/` is gitignored. Do not `git add -f` it.

Inside the folder, `report.md` is the writeup. Scripts, dumps, and databases go in `work/`.

`.learnings/` is a short note a later session must follow. It is not the investigation.

`tasks/<name>/` is a `feature.json` implementation. It is not an investigation. Herdr scratch for a task lives in `tasks/<name>/orchestrate/` and is gitignored. Do not name that folder `fleet`, `run`, or `herdr-series`.
