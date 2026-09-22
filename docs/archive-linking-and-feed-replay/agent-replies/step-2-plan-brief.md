# Step 2 — write implementation plan only

You are the planner. Do not implement. Do not commit. Do not edit product code.

## Output contract (mandatory)

Write the FULL plan as Markdown to this exact path, overwriting if present:

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-2.md`

In the chat, reply with ONLY that path. No plan text in chat.

## Context

- Spec: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md`
- Code repo (cwd): `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`
- Progress: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
- Do NOT use `betting_workspace/current-task/` — that is a different feature.
- No feature.json. This is step 2 of the spec only.
- Steps 0 and 1 are already done on `main` (PGL removed; `src/archive_index/` exists).
- `../poly-maker` is frozen. Do not plan edits there.
- Follow `esports-trader/AGENTS.md` and ponytail: smallest diff after tracing the real flow.

## What step 2 is

Read the spec section **«Шаг 2. Dota-линковка и общий допуск датасетов»** plus «Порядок реализации» row 2 and the matching mandatory checks.

Goal: OpenDota stays an independent candidate source. After universe + OpenDota candidates, merge exact archive links into a shared `match_links` table with provenance. Admission rule: `(historical OpenDota + GRID) OR (admitted live archive)`. Keep STRATZ / timeline / prior / market / market-tape checks. Horn fallback is not proof of a usable feed. Late OpenDota link enriches an existing link, does not duplicate. Train lag stays 10s for both Dota models; no-XP is not Oddin-only. Do not fake `opendota_*` fields, OpenDota start time, or GRID clock onto archive rows.

Time contract: store confirmed horn, pauses, end, and provenance. Unknown pauses must not become `[]`. Fix `spawn = horn_at_utc - 90` double-counting pre-horn pauses. Direct horn vs map-load must be distinct. Winner is for outcome, not a model feature. Archive may supply time fields only where the record actually has them.

If timeline/STRATZ is insufficient: keep the recovered link with a refusal reason at the next stage. Do not treat a successful link as a ready dataset.

Readiness: report candidate → link → admission → catalog → split. Old admitted matches stay except agreed PGL-only removal and separately found errors/conflicts.

Out of scope for this step: applying schedules in backtest (3A), CLI/Makefile/mixed-model report (3B), changing train lag, new Oddin/PGL simulators, poly-maker.

## Plan quality bar

Trace the real collect flow end to end before proposing files. Name actual current modules/functions, not guesses from the spec. The spec file list is a hint; the live tree may have moved.

The plan must include:

1. Current flow (s02 → later stages, `common/window_ids.py`, catalog publish, Makefile) and where archive_index already gives admitted archives.
2. Concrete merge design for `match_links` + provenance. How archive-without-Explorer Steam IDs attach by exact condition ID. Conflict handling (condition/Steam/map/sides).
3. Admission gate change: OpenDota+GRID OR admitted live archive. Zero orders must not block. Bad feed must not bypass train filter. Same rule for all Dota splits.
4. Time/pause/horn contract: which consumers (`s06_publish_catalog.py`, `market_data/build_market_data.py`, `backtest/run.py::load_replay_lookups`) change in this step vs later.
5. Exact files to add/edit/delete. Reuse existing helpers. No extra abstractions.
6. Tests and commands: targeted pytest, ruff, basedpyright. Do not run the full suite during a live map. `/tmp` is tmpfs; pytest tmp is relocated by conftest.
7. Verification: candidate→link→admission→catalog→split report; old matches preserved; new links not claimed as ready datasets.
8. Risks / non-goals.

Be specific enough that an implementer can execute without asking you. Dig into the hard parts (time origin, pause empty-list trap, spawn double-count, identity uniqueness).
