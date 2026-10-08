# s7-impl — STEP-007

Status: FINAL

## What changed

Worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s7`, branch `two-sided-s7`, base `f605a6b9`. The main `esports-trader` checkout was not edited.

Commit `d9b6d3f05930c2a55a11920abea7aa87dfb6a2d7` (amended from `629657b0`):

`Pick the Dota strategy from DOTA_STRATEGY: two-sided worker, adapter merge, and a BLAST Slam-only title whitelist.`

12 files, +445 / −42. `passes` left false.

- `src/trader/trading_mode.py` — `DotaStrategy`, `read_dota_strategy()`, `require_two_sided_start(...)`.
- `src/trader/discovery.py` — `league_matches` replaces `is_league_blacklisted`. `MarketDiscovery` takes `title_whitelist`. One filter pass, blacklist then whitelist, logs `discovery skip reason=title_whitelist`.
- `src/trader/wallet_host.py` — required `strategy`. `select_title_whitelist`. `_pick_and_run` builds `TwoSidedWorker` or `MatchWorker` with the same six arguments. `install_adapter_merge` only for `two_sided`. `run()` passes `()` for follow300. For `two_sided`, tier names must be nonempty and every name must be `BLAST Slam`, or `run()` raises `TradingDisabled` before `engine.start()`.
- `src/trader/host_resources.py` — `read_dota_strategy()` once, right after `reject_legacy_env()`. Start check before `Engine(...)`. Log `trader wallet: strategy=%s signature_type=%d funder=%s` with funder = `browser_address`.
- Tests: mechanical arity updates, new cases in `test_trader_trading_mode.py`, `test_trader_discovery.py`, `test_trader_host_resources.py`, and new `tests/test_trader_two_sided_host.py`.

`TwoSidedWorker.__init__` already matches `MatchWorker` (six positional args plus optional `exit_timeout_seconds`). The call was not changed.

## Deviations

- Brief overrides the plan's "commit on main" and the skill's `passes: true`. Commit is on `two-sided-s7`. `feature.json` STEP-007 `passes` stays false. Decisions 1, 2, 5, 7, 8, and 11 are in `progress.txt` and in `feature.json` Resolved Questions.
- `Secrets(_env_file=None)` is `reportCallIssue` under basedpyright. The seam test uses `Secrets(PK="", BROWSER_ADDRESS="")`, the fallback the plan names. E7 has no `.env`.
- `lambda message: None` for `notify_in_background` is `reportUnknownArgumentType`. Replaced with `quiet_notify(message: str)`.
- Ruff format collapsed a few wraps the plan had split (`_drop_skipped_titles`, one discovery assert, the refusal test signature, the worker-args assert).
- `grep DOTA_STRATEGY src` is not only `trading_mode.py`. It also hits the empty-whitelist sentence in `wallet_host.py`. That sentence is the plan's text and the STEP-009 runbook string, so it stayed: `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist`.
- McCabe did not reach 11. No `_require_strategy_start` helper.

## Commands

`PYTEST_N` was unset for these runs (the shell had it as 10). Serial.

Step tests:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_trader_trading_mode.py tests/test_trader_host_resources.py \
  tests/test_trader_discovery.py tests/test_trader_two_sided_host.py \
  tests/test_trader_wallet_host.py -q --tb=line
```

12 failed, 220 passed, 1 warning, 2.82s. Every failure is `OddinFeedError: ODDIN_BRAND_TOKEN is not set` in existing `test_trader_wallet_host.py` `run()` tests:

- `test_start_error_still_runs_teardown`
- `test_run_scans_boot_before_discovery`
- `test_run_lol_paper_reaches_discovery_without_steam`
- `test_run_builds_one_discovery_per_assigned_game`
- `test_invalid_lol_whitelist_prevents_engine_start` (6)
- `test_lol_start_logs_whitelist_before_engine_start` (2)

`require_brand_token()` still runs before `MarketDiscovery(...)`. These tests need `.env`. They were not edited past the plan's mechanical whitelist recording. The new `test_two_sided_run_filters_discovery_by_the_clip_names` patches the token and `refresh_now` and passed.

After the two type fixes, a rerun of the new and host-resource files plus the league tests: 59 passed, and the same `test_run_builds_one_discovery_per_assigned_game` failure. That test never reaches the new whitelist assert in E7.

Regression:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_discovery_cadence.py tests/test_trader_lol_discovery.py tests/test_trader_orchestrator.py \
  tests/test_trader_engine_seams.py tests/test_trader_unsettled_buy_recovery.py \
  tests/test_trader_unsettled_buy_activation.py tests/test_trader_match_lifecycle.py \
  tests/test_trader_two_sided_worker.py tests/test_trader_pair_merge.py tests/test_trader_ctf_merge.py \
  tests/test_strategy_core.py tests/test_follow300_replay.py -q --tb=line
```

9 failed, 316 passed, 3 warnings, 51.05s.

- `test_user_stream_is_bound_on_the_host_run_path` — same missing `ODDIN_BRAND_TOKEN`.
- Eight `test_follow300_replay.py` seed0 cases — `FileNotFoundError` for `data/new_processed/dataset/validation_dataset.parquet` and `data/lol/processed/datasets/backtest_audit.parquet`. E7 has no `data/` symlinks. Not linked.

`PYTHONPATH=src uv run python -c "import trader.orchestrator"` printed nothing (`import ok`).

`uv run ruff check --select C901` on the four trader modules: all checks passed.

`uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.

`make lint` on the staged files: ruff check, ruff format, basedpyright, whitespace, large-files all passed. Commit hook ran the same hooks and passed.

`grep -rn is_league_blacklisted src scripts tests`: empty.

`git diff --exit-code` on `compose.yaml`, `config`, `orchestrator.py`, `match_worker.py`, `engine_seams.py`, `dust_sweep.py`, `src/strategy`: empty.

`git -C ../poly-maker status --short`: empty.

`git diff -U0 -- src` has no added comment or docstring lines. `tests/test_trader_two_sided_host.py` has only the `# pyright:` line.

## Commit

`d9b6d3f05930c2a55a11920abea7aa87dfb6a2d7` on `two-sided-s7` (amended; was `629657b0`). Working tree clean. Not pushed. Not on main.

## Open issues

- Orchestrator cherry-picks onto main. A STEP-006 review fix on main after `f605a6b9` is not in this branch.
- The 13 `run()` tests above stay red in E7 until `.env` is present. They are green on a checkout that has the brand token. Left unchanged.
- `test_follow300_replay` stays red in E7 until `data/new_processed` and `data/lol/processed` exist. Left unchanged.
- `test_run_builds_one_discovery_per_assigned_game` records `whitelist_by_game` but does not execute that assert here. follow300's `()` whitelist is covered by `test_select_title_whitelist_accepts_only_blast_slam`. The live two-sided discovery args are covered by `test_two_sided_run_filters_discovery_by_the_clip_names`.

## Review fixes

Amended `629657b0` to `d9b6d3f05930c2a55a11920abea7aa87dfb6a2d7` on `two-sided-s7`.

### s7-rev

- Major, non-BLAST clip tiers admitted: **fixed**. `select_title_whitelist` still builds the list from `[clips.dota].tiers`, then refuses unless every name is exactly `BLAST Slam`. Empty tiers keep the old text. follow300 still returns `()` and does not read the table. `test_select_title_whitelist_accepts_only_blast_slam` covers BLAST-only, BLAST+EPL, PARI-only, EPL-only, and empty.

### s7-com

1. `archive_root` comment and attribute: **fixed**. Nothing in `src/` read `WalletHost.archive_root`. Dropped the attribute and the three test writes. `FakeWalletHost.archive_root` is a different class and stays.
2. `_install_runtime_seams` docstring: **fixed**. Deleted.
3. `_pick_and_run` docstring: **fixed**. Deleted.
4. `_bare_host` docstring: **fixed**. Deleted.
5. `test_run_builds_one_discovery_per_assigned_game` docstring: **fixed**. Deleted.
6. `type: ignore[assignment]` on `steam_client`: **fixed**. `cast(SteamClient, SimpleNamespace())`, ignore removed.
7. `_patch_host_runtime_fakes` docstring: **fixed**. Deleted.
8. `_install_resource_fakes` docstring: **fixed**. Deleted.
9. `test_dota_live_opens_wallet_and_steam` docstring: **fixed**. Deleted.
10. `build_poll_target` docstring: **fixed**. Deleted.
11. `test_league_matches_reads_the_league_not_the_teams` docstring: **fixed**. Deleted.
12. `make_lol_discovery` docstring: **fixed**. Deleted.
13. `make_discovery` docstring: **fixed**. Deleted.

Kept, as the comment review's keep clause: the new test file's `# pyright:` header; `discover`, `open_wallet_host`, `run_wallet_daemon`, and `WalletHost.run` docstrings; `ResourceLog`'s class docstring.

### Checks

`PYTEST_N` unset. Step tests plus cadence and LoL discovery:

12 failed, 242 passed, 1 warning, 3.60s. The 12 failures are the same existing `test_trader_wallet_host.py` `run()` cases: `ODDIN_BRAND_TOKEN is not set`. Not changed.

`uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.

`uv run ruff check --select C901` on the four trader modules: passed.

`make lint` on the staged files: ruff check, ruff format, basedpyright, whitespace, large-files passed. The amend hook ran the same checks and passed.
