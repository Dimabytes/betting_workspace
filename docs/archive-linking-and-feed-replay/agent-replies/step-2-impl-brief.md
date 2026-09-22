# Step 2 — implement now

You are the implementer. Follow the plan. Do not expand into steps 3A/3B.

## Output contract (mandatory)

1. Implement step 2 in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main`.
2. Run the plan's targeted tests + `make lint` + `uv run basedpyright`. Do NOT run the full pytest suite. Do not run heavy VPS work. Do not restart live traders.
3. APPEND (never replace) to:
   `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`
   Format:

   ## [Date/Time] - step-2

   - What was implemented
   - **Learnings for future iterations:** bullets

4. Commit in esports-trader only, message: `feat: [step 2] - Dota linking and shared dataset admission`
   Do not push. Do not touch `betting_workspace/current-task/` (different feature). Do not edit `../poly-maker`.
5. Write a completion report to:
   `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/agent-replies/step-2-impl.md`
   Then reply in chat with ONLY that path.

## Plan (source of truth)

`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/plans/step-2.md`

Spec: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` section «Шаг 2».

Also read `esports-trader/AGENTS.md`. Ponytail: smallest diff after tracing the real flow. Reuse existing helpers. No extra abstractions. No `dict[str, Any]`. Named frozen dataclasses instead of anonymous tuples. Required function args. Action-verb function names.

## Hard constraints from the spec

- OpenDota stays independent. Merge archive links into shared `match_links` with provenance. Do not fake `opendota_*`, OpenDota start time, or GRID clock onto archive rows.
- Admission: `(historical OpenDota + GRID) OR (admitted live archive)`. Zero orders do not block. Bad feed does not bypass train filter. Same rule all Dota splits.
- Late OpenDota link enriches existing link, does not duplicate.
- Train lag stays 10s for both Dota models. No-XP is not Oddin-only.
- Unknown pauses must not become `[]`. Fix spawn = horn-90 double-count. Direct horn vs map-load distinct.
- Successful link ≠ ready dataset. Keep refusal reasons at later stages.
- Do not apply schedules in backtest (3A). Do not change CLI/policy flags (3B).

## If you cannot finish a heavy data rebuild

Ship the code + tests + funnel command. Log what data rebuilds you skipped and why. Still commit working code.
