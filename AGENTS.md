## Workspace

This repo is `betting_workspace` — the AI workspace (skills, learnings). It holds no product code. Sibling project repos are checked out next to `betting_workspace/`, not inside it. To work on one, go up one level and into the project (e.g. from this repo's root run `cd ../esports-trader`).

Expected layout:

```
<parent>/
├─ betting_workspace/                 # this repo: .shared-skills/, .learnings/, AGENTS.md
├─ esports-trader/                    # collect / train / backtest / live+paper trader
├─ poly-maker/                        # fork of a Polymarket maker bot
├─ polymarket-collector/              # TypeScript daemon that archives Dota 2 markets (runs on the VPS)
└─ prediction-market-backtesting/     # local source checkout of the Nautilus backtesting library
```

Five folders, four code projects. This workspace is agent instructions only.

Before any work on a sibling project (code, commands, tests, architecture) read the matching briefing (paths are relative to `betting_workspace/`):

`../esports-trader` -> `../esports-trader/AGENTS.md`

`../poly-maker` -> `.learnings/poly-maker.md`

`../polymarket-collector` -> `.learnings/polymarket-collector.md`

`../prediction-market-backtesting` -> `.learnings/prediction-market-backtesting.md`

## Git Rules

- All git operations (status, branch, pull, rebase, push, commit) run inside the sibling project repo you are changing.
- work on `main` by default, unless the user says otherwise.

## poly-maker is frozen

`../poly-maker` is our GitHub fork (`Dimabytes/poly-maker`) of `warproxxx/poly-maker`. Treat both as read-only.

Do not change `poly-maker`. This is a ban, not a preference.

- Do not edit files in `../poly-maker`.
