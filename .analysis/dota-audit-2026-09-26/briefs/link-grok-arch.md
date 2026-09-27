# Follow-up brief: link-grok — cross-cutting architecture review (money, speed, debuggability)

Report: `$R/reports/link-grok-arch.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.

The owner asked for an architectural review, not only bugs. Rank recommendations by impact on money,
decision quality, iteration speed, and debugging time. Keep bugs S1/S2 only if you find real ones.

## Look at

1. **Boundaries and duplication.** collect / prepare / train / backtest / strategy / trader / shared.
   Duplicated logic between Dota and LoL paths, between the live and backtest adapters of the kernel, and
   between readers of the same files. Where can the two copies drift (the class of bug fixed on one side only,
   e.g. the Oddin-only horn pin guard `deebb730` vs Steam/GRID — note N4b)?
2. **Giant files.** `src/backtest/run.py` (2223 lines), `trader/wallet_host.py` (1387),
   `backtest/strategy.py` (1339), `trader/engine_seams.py` (1299), `trader/match_worker.py` (1233),
   `strategy/quoting.py` (977). What do they mix, and what is the smallest split that pays off?
3. **Monkeypatching.** `engine_seams.py` patches the frozen poly-maker Engine; `run.py` patches Nautilus
   internals (`backtest._load_sims_async`, `_build_market_artifacts`). Fragility on upgrades; hidden coupling.
4. **Config spread.** Constants in `src/shared/constants/*`, `config/trading.toml`, manifests, `model.json`,
   live `core_trace` policy headers. Single source of truth? Can live and backtest silently diverge?
5. **Caches and identity.** market_seconds (hash of constants only — N3), game_features, backtest caches
   (`_backtest_cache`), model identity hashes. Where can stale artifacts leak into results?
6. **Observability and debugging.** What would the owner need to answer "why did live lose on map X" in 5
   minutes? Logs, `session.jsonl`, `core_trace`, viewers (`src/viewer`, `src/backtest/inspect`), reports.
   What is missing or misleading (e.g. fill-grok-F3: terminal report hides the settlement tail)?
7. **Performance.** Backtest wall time per run (post-processing, parquet rewrites), market-data build,
   prepare, live event loop latency. Cheap wins.
8. **Tests.** Critical money paths without tests (read `tests/`, do not run).
9. **Dead code and leftovers.** Unused modules/scripts, old experiment knobs, stale docs (`docs/as-is.md`).

Deliver a ranked list (top 15) with `file:line` evidence and a one-line "smallest change that fixes it".
