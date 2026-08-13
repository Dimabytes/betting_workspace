# betting_workspace

The AI workspace for the three betting project repos. Holds shared agent rules, skills, and per-project learnings. No product code lives here.

## Layout

The three project repos are checked out as **siblings** of `betting_workspace/`, not inside it.

```
<parent>/
├─ betting_workspace/                 # this repo
├─ dota_2_model/                      # own full training / data / model pipeline
├─ poly-maker/                        # fork of a Polymarket maker bot
└─ prediction-market-backtesting/     # local source of the Nautilus backtesting library
```

Each project is its own git repository; all real code work happens inside them. From `betting_workspace/`, reach a project with `cd ../dota_2_model` (etc.).

## What's here

- `.shared-skills/` — single source of truth for AI skills. `.claude/skills` and `.agents/skills` are symlinks.
- `.learnings/` — per-project knowledge (`dota_2_model.md`, `poly-maker.md`, `prediction-market-backtesting.md`). Agents read the matching file before working on a sibling. Keep them updated as patterns and gotchas emerge.
