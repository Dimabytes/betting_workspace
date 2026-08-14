## Workspace

This repo is `betting_workspace` — the AI workspace (skills, learnings). It holds no product code. Sibling project repos are checked out next to `betting_workspace/`, not inside it. To work on one, go up one level and into the project (e.g. from this repo's root run `cd ../dota_2_model`).

Expected layout:

```
<parent>/
├─ betting_workspace/                 # this repo: .shared-skills/, .learnings/, AGENTS.md
├─ dota_2_model/                      # own full training / data / model pipeline
├─ poly-maker/                        # fork of a Polymarket maker bot
├─ polymarket-collector/              # TypeScript daemon that archives Dota 2 markets (runs on the VPS)
└─ prediction-market-backtesting/     # local source checkout of the Nautilus backtesting library
```

Five folders, four code projects. This workspace is agent instructions only.

Before any work on a sibling project (code, commands, tests, architecture) read the matching learnings file (paths are relative to `betting_workspace/`):

`../dota_2_model` -> `.learnings/dota_2_model.md`

`../poly-maker` -> `.learnings/poly-maker.md`

`../polymarket-collector` -> `.learnings/polymarket-collector.md`

`../prediction-market-backtesting` -> `.learnings/prediction-market-backtesting.md`

## Git Rules

- All git operations (status, branch, pull, rebase, push, commit) run inside the sibling project repo you are changing.
- work on `main` by default, unless the user says otherwise.
