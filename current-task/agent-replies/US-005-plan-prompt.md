# PLAN ONLY — US-005

You are the PLANNER. Fresh session. Do not implement.

## Skill

Read `/Users/dimabytes/.claude/skills/feature-json-implement-step/SKILL.md`. Planning only.

## Task

Plan **US-005** only.

Write:
- `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/plans/US-005.md`
- `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/current-task/agent-replies/US-005-plan.md` (10 lines)

Reply with only those two paths.

## Operator constraint (orchestrator will execute later)

The human will `git pull` on the VPS and start the `onchain` container (SSH password). The implementer must:
- Put all VPS bootstrap steps in a runbook the operator can follow after pull (commands, env vars, UID 10001 import, compose enable, ONCHAIN_START_DATE from import report)
- Do Mac-side code changes in `../esports-trader` (sync script, delete Python downloader, split telonex_onchain.py)
- Do collector code if still needed
- The orchestrator may rsync closed parquet Mac → VPS import staging via SSH if keys work; do not assume interactive password
- Do not `git push`. Do not `docker compose down`. Do not start VPS `onchain` yourself.

## US-005 acceptance

- Bootstrap on VPS: rsync inventory and closed parquet Mac → import staging with resume; do not overwrite conflicting files without hash compare; run import as UID 10001; enable compose `onchain`; set ONCHAIN_START_DATE from the import report
- Update `scripts/sync_collector_parquet.py` to copy `onchain_fills` from ready on-chain manifests, report coverage, replace a legacy file with a new full_day after ONCHAIN_START_DATE; `--ignore-existing` alone is not the on-chain policy; incomplete current UTC day stays excluded
- Delete `scripts/parity_onchain_rpc.py`, `scripts/sync_onchain_fills.py`, `tests/test_parity_onchain_rpc.py`, `tests/test_sync_onchain_fills.py` after moving required fixtures into the collector, and Makefile targets `parity-onchain-rpc` / `sync-onchain` plus help/phony
- Split `src/shared/utils/telonex_onchain.py`: move path/HTTP/parquet helpers still used by LoL book fetch, `map_build_cache.py`, `lol/replay.py`, `backtest/telonex_local.py`, and `shared/utils/telonex_tape.py`; delete the download-only on-chain collector. Keep onchain_fills readers, backtest availability checks, and the symlink bridge
- Applicable esports-trader tests for the changed consumers pass. Do not run the full trader suite during a live map
- VPS verification (compose onchain enabled, one production-like completed day, Mac sync from ready manifest) is planned as operator steps after git pull — write the exact commands, do not require the implementer to SSH with a password

Read feature.json, design spec, trader AGENTS.md, `telonex_onchain.py` callers, `sync_collector_parquet.py`.

poly-maker is FROZEN.

Do not implement.
