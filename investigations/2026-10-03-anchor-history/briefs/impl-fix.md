# Brief: impl fix round

Read `reports/review.md` in full. Decide what to fix (you own the decision); fix in esports-trader, commit locally on `main` (no push). Same hard rules as `00-context.md`.

Orchestrator additions (please do these too):
A. Plan 02 "Проверка" items 2 and 3 are missing from your report. Run them now (read-only): invalid-tick share on Dota GRID / LoL GRID archives under the new rule (target ≈ 0.01% / 0.06%), and the LGD–Xtreme `grid-3011816-m3` archive through the new `SnapshotHistory` (history must not flip to NaN at 144/147/188 s). Write a small read-only script under `work/impl/` if no ready one exists. Report the numbers.
B. Review finding 2: the orchestrator WILL backtest the old 12-column XP + 11-column no-XP catalogs (`data/new_model/archive/research/20260930T180848Z`, `data/new_model/archive/research-noxp/20260930T180911Z`) through `--model-dir` / `--model-dir-noxp`. Make that path work (pin accepts them on the matching flag) and make those catalogs never drop gap ticks.
C. Add an experiment output path to the LoL trainer (`src/lol/06_train_model.py`, `make lol-train`): a `--model-dir <dir>` flag that trains and publishes ONLY the research model into `<dir>` (no production, no touch of `data/lol/models/research` or its archive). Mirror the Dota trainer's `--model-dir` guard (refuse the live research/production dirs). One small test.
D. Run the full suite once at the end (`PYTHONPATH=src:scripts:../prediction-market-backtesting PYTEST_N=8 uv run pytest -q`) and pre-commit on changed files.

Report: append a section `## Fix round` to `reports/impl.md`: per finding fixed/skipped + why, new commits, A numbers, suite result. Then set line 2 to `Status: FINAL` again (set it to `Status: WIP` first while you work).
