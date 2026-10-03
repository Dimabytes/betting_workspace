# Brief: review

Review the implementation of both plans by agent `impl`. Read `reports/impl.md` first, then the commits it lists (`git -C <esports-trader> show <hash>`), and the plans `plans/01-prior-anchor.md`, `plans/02-history-policy.md`.

You are read-only on the code repo. Do not edit or commit. You may run targeted `uv run pytest <files>` and small read-only scripts under `work/review/`.

Check:
1. Correctness vs plan: each plan item for code/tests (Part 1 todos anchor-fn/test; Part 2 steps 1–4) is implemented and matches the plan's rule exactly (history rule 1–5, policy table values, live `_on_event` ordering, backtest invalid-tick handling, first-tick constants 48/76, archive start behaviour, train start_second 60 / −60, LoL start_second 60, anchor = horn − 90 − pause in [−90,0), unknown pauses → no anchor).
2. Train/backtest/live parity: the same HistoryPolicy reaches all three paths; no path still uses the old 16 s "last before target" lookup for GRID; Oddin output unchanged.
3. Bugs: off-by-one at boundaries (`start_second` inclusive?, gap `<=` vs `<`), NaN/valid confusion, first recorded second bookkeeping, watchdog/SELL behavior on gap ticks, recovery_stale_mask from last valid tick.
4. Tests: do they actually fail if the logic breaks? Missing cases the plan lists.
5. Maintainability per `esports-trader/AGENTS.md` rules: action-verb names, required args, frozen dataclasses not tuples/dicts, no narrating comments, no dead branches, no giant functions grown with spaghetti conditions, simpler equivalent code.

## Report (`reports/review.md`)
Line 2 `Status: FINAL` when done. A numbered list of findings, most severe first. Each: severity (bug / plan-mismatch / test-gap / maintainability / nit), file:line, what is wrong, concrete failure scenario, suggested fix. If there are no findings for a category, say so.

## Extra checks from the orchestrator (high priority)
- Backtest `--model-dir` / `--model-dir-noxp` overrides (`src/backtest/run.py` `_pin_cli_model_dir`): the HistoryPolicy must follow the catalog role (XP catalog → GRID policy, no-XP catalog → Oddin policy), not the literal path `RESEARCH_MODEL_DIR`. We will backtest experiment catalogs under `data/experiments/...` and an old 12-feature catalog through these flags.
- A catalog without the 60 history columns (old 12-feature catalog, archive `data/new_model/archive/research/20260930T180848Z`) must not lose ticks to `drop_gap_ticks`: no history features → no gap-invalid ticks. Check both backtest and live.
- Training with `--model-dir` (experiment path, `src/train_model/train_model.py` parse_args) must get the same `start_second` as the default research path (XP 60, no-XP −60).
