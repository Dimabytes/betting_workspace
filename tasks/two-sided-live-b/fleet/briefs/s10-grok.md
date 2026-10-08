# Brief: s10-grok — STEP-010 release gate (alone, no separate review)

You are the only agent for STEP-010. Work in the main E checkout on `main` (no worktree). Nobody else edits E or W while you run.

1. Read `~/.agents/skills/feature-json-implement-step/SKILL.md` and follow it. Task slug: `two-sided-live-b`. Step: `STEP-010`. No plan file: write your short plan at the top of the report. Read `progress.txt` and `R/work/orchestrator/notes.md` (orchestrator log with known issues).
2. Do every item in the step's `changes`. The gate must end green. If a check fails, find the root cause and fix it with the smallest correct change, one commit per fix on E main (never amend or rebase others' commits). Service A (Follow300) behavior must not change: any fix in shared code needs `tests/test_follow300_replay.py` and `tests/test_strategy_core.py` green.

## Known items to handle

- Shell has `PYTEST_N=10`. The full suite must run serially: `env -u PYTEST_N ...`. Record test count and time.
- Reported failing before or during this feature (verify on HEAD, fix root cause): `test_user_stream_is_bound_on_the_host_run_path` (`WalletHost` has no `_mode`, `wallet_host.py`), `test_home_renders_all_sections` (`доступно` missing). Some `tests/test_trader_wallet_host.py` `run()` tests call Disir with `ODDIN_BRAND_TOKEN` from `.env`; E main has `.env`, so they should pass here. Do not print `.env` values.
- STEP-004 changed shared `src/trader/core_recovery.py` (`accept_if_proven` replays pending outbox rows first). Prove A regression: replay goldens and recovery tests green.
- Regression backtest: same command as `ts-regress-before` (see `R/reports/s1-grok.md` and progress.txt), `--name ts-regress-final`; compare fills/results/quote_events with `pyarrow.Table.equals` against `ts-regress-before`.
- `docker compose config`: use `--services`, and `docker compose config --no-interpolate --format json | jq` selecting only `services.live_b.environment.DOTA_STRATEGY`, the volume sources, and `stop_grace_period`. Never print interpolated output (it contains private keys).
- "Block live in compose.yaml and config/trading.toml not changed": compare against `ac475db2` (the commit before STEP-001).
- `../poly-maker`: `git -C ../poly-maker status --short` empty.
- Untracked `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_ts-regress-{before,after,final}`: `data/backtests` is tracked in this repo (see `git log -- data/backtests` and E/AGENTS.md for the convention). Follow the convention to make `git status` clean (commit them as a record if that is the convention; never delete them).
- Runbook re-check: `W/.shared-skills/vps-trader/SKILL.md` (commit `7fee213`) copied strings from plans. Compare with the final code and fix the SKILL.md text where it differs: start log line `trader wallet: strategy=… signature_type=… funder=…`; the two_sided refusals (STEP-007 review fix: the whitelist must be exactly `BLAST Slam`, check the real refusal text); `discovery skip reason=title_whitelist`; restart late-fill behavior; `stop_grace_period` 240 s. Also `W/.shared-skills/vps-trader/log-map.md` says wallet cash is MATCHED+CONFIRMED; for B it also counts MERGED — fix that line.
- W commit: commit `.shared-skills/vps-trader/*` fixes, `tasks/two-sided-live-b/` (whole folder), `.feature-json.config.json`, `.herdr-fleet.config.json` in W on main (owner approved). Before committing `tasks/two-sided-live-b/fleet/work/`, check sizes (`du -sh`); keep files under the large-file hook limit.
- Set `passes: true` for STEP-010 in feature.json only when every check is green. `progress.txt` must end with the owner action list: `git push` in E and in W, then the B runbook in `.shared-skills/vps-trader/SKILL.md`.
- No push. No docker up/build/run. No SSH.

Report `R/reports/s10-grok.md`: plan, every check with the exact command and result (pytest count/time, basedpyright, ruff, self-check, regression comparison, compose checks, diffs, git status of E/W/poly-maker, `git log origin/main..main` for E and W), fixes made with commit hashes, SKILL.md corrections, open issues for the owner.
