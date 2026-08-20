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

## poly-maker is frozen

`../poly-maker` is our GitHub fork (`Dimabytes/poly-maker`) of `warproxxx/poly-maker`. Treat both as read-only.

Do not change `poly-maker`. This is a ban, not a preference.

- Do not edit files in `../poly-maker`.
- Do not commit, push, rebase, or open a pull request in `../poly-maker`.
- Do not send patches or pull requests to `warproxxx/poly-maker`.
- Do not put `poly-maker` file edits, refactors, or version bumps into a plan.
- Do not "fix the Engine at the root" in `poly-maker`. Keep `dota_2_model` as the only place that patches Engine classes and methods. Patch before or after `Engine()`, as `engine_seams.py` already does.

You may tell the user that a `poly-maker` change would help. Stop after that sentence. Do not plan it. Do not implement it. Wait until the user says in this conversation that `poly-maker` may change.
