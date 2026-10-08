# s8-impl — STEP-008

Status: FINAL

## What changed

Commit `56af02ed` (`56af02eda843d4f13985442cb979449757b3197a`) on `esports-trader-s8` branch `two-sided-s8`, amended from `be0cc83c`: `Add wallet B: config_b and the live_b compose service for the two-sided trader.`

Parent is `2fe56a67`. Working tree clean after the commit. No commit in `betting_workspace`. No push. STEP-008 `passes` is still false.

- `config_b/trading.toml` (new). Copy of `config/trading.toml` with `signature_type = 3`, `account_cap_usdc = 100000.0`, dota and dota-oddin `base_size_usdc = 20.0`, and a single BLAST Slam tier at clip `20.0` in `[clips.dota]` and `[clips.dota-oddin]`. Comments state that 9 × $20 is map room and is not enforced. `[engine]` matches A. `[profiles.lol-map]` and `[clips.lol]` are unchanged copies.
- `compose.yaml`. Service `live_b` sits between `live` and `paper`. Same image recipe and `daemon --mode live` as `live`. `stop_grace_period: 240s`. Environment is the wallet-B mapping (`DOTA_STRATEGY: two_sided`, literal `DOTA_TRADING_MODE: live`, quoted `LOL_TRADING_MODE: "off"`, `${PK_WALLET_B}` and the other `*_B` keys). Volumes are the dota archive, `./data/trader_live_b`, `./data/new_model`, `./src`, `./config_b`, and `./.git`. No LoL mounts. `compress` also takes `--root /app/data/trader_live_b` and `./data/trader_live_b:/app/data/trader_live_b`.
- `tests/test_trader_compose.py`. `SERVICE_KEY` allows `_`. New `test_docker_compose_config_live_b_is_wallet_b` pins the environment dict, bind targets, read-only flags, grace ≥ `MERGE_CALL_TIMEOUT_S + FENCE_TIMEOUT_S + DRAIN_TIMEOUT_S` (190 + 20 + 5), and that no other service has `DOTA_STRATEGY` or a `_B}` reference. Existing service-set and compress asserts include `live_b`.
- `tests/test_trader_session_config.py`. `test_config_b_is_a_deposit_wallet_on_blast_slam` reads `config_b` through `read_template`: signature type 3, engine equal to A's file, cap 100000, clip 20 for a BLAST Slam title, one tier name.

`config/trading.toml` and the `live` block are byte-identical to `ac475db2`. `src/**` was not edited.

## Deviations

- Commit is on `two-sided-s8` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8`, not on `main`. The brief overrides the plan and the implement skill.
- `passes` left false. The brief overrides the implement skill.
- STEP-006 and STEP-007 are not in this worktree. Env names `DOTA_STRATEGY` / `two_sided` come from feature.json and `plans/STEP-007.md`. The whitelist source in that plan is the names in `[clips.dota].tiers`. The LoL guards were checked on parent `2fe56a67`, not on STEP-007's code: `discovery.py:195` builds `LolLeagueFilter` only for `profile.game == "lol"`, `wallet_host.py:841` returns when `"lol" not in self._archive_by_game`, `host_resources.py:230` builds `archive_by_game` from `assigned_games`, and `_load_strategy_models` (`host_resources.py:119`) loads only those games. `load_canonical_league_whitelist` runs from `LolLeagueFilter.__init__`, not at import. `config_b` is only `trading.toml`. `live_b` has no LoL mounts.
- `stop_grace_period` is `240s`, not the feature text's `200s`. That is the plan's decision 1, pinned by the grace test. Timeouts on this tree: `MERGE_CALL_TIMEOUT_S = 190.0` (`pair_merge.py:27`), `FENCE_TIMEOUT_S = 20.0` (`match_worker.py:129`), `DRAIN_TIMEOUT_S = 5.0` (`engine_seams.py:70`).
- No other code deviation. The `config/trading.toml` diff against `config_b/trading.toml` is only the hunks the plan lists.

## Commands

- `diff config/trading.toml config_b/trading.toml`: signature_type 2→3, the `[risk]` comment line, account_cap 30000.0→100000.0, the dota-map sizing comment and base_size 300.0→20.0, dota-oddin base_size 5.0→20.0, the whitelist comment, `[clips.dota]` tiers EPL 60 + BLAST 200 → BLAST 20, `[clips.dota-oddin]` PARI 140 → BLAST 20.
- `git diff ac475db2 -- config/trading.toml`: empty.
- `diff` of the `live:` body against `ac475db2:compose.yaml`: empty.
- `docker compose version`: v2.31.0-desktop.2.
- `docker compose config --services`: `live_b`, `paper`, `compress`, `live`. Stderr empty. No `.env` in the worktree, and `PK_WALLET_B` / `BROWSER_ADDRESS_B` / `POLY_BUILDER_*_B` / `TG_CHAT_ID_B` are unset in the process environment. The plan expected `variable is not set` warnings here. Compose 2.31.0-desktop.2 did not print them. The tests still passed.
- `docker compose config --no-interpolate --format json`: `stop_grace_period` `240s`, `DOTA_STRATEGY` `two_sided`, `PK` `${PK_WALLET_B}`, environment is a dict. Volume targets: `/app/.git`, `/app/config`, `/app/data/new_model`, `/app/data/trader` (rw), `/app/src`, `/archive/dota`. Compress command tail: `--root`, `/app/data/trader_paper`, `--root`, `/app/data/trader_live_b`.
- Serial pytest (`PYTEST_N` unset), `tests/test_trader_compose.py` `tests/test_dota_map_config.py` `tests/test_trader_session_config.py`: 46 passed, 1 warning, 1.53s. An earlier run with the inherited `PYTEST_N=10` also passed (46 passed, 24.24s) and is not the recorded result.
- `git diff -U0 -- tests/ | grep` for new `#` or `"""` lines: no matches.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the four staged files: ruff check, ruff format, basedpyright, trailing whitespace, end-of-file, check-yaml, check-toml, large-files all passed. The commit hook passed the same checks. Lint did not rewrite files.

## Open issues

- This commit is not on `main`. The orchestrator cherry-picks it.
- Nothing in this worktree reads `DOTA_STRATEGY`. `live_b` will not select the two-sided worker until STEP-007 is on the same tree.
- A blank `TG_CHAT_ID_B` does not fail closed. STEP-009's runbook has to check it. A blank `PK` / builder key fails closed at start.
- `240s` does not cover two maps each holding a threshold merge, or SIGTERM during the final `merge_all()`. Bids are already pulled. Restart-check SAFE is the guard.
- 9 × $20 = $180 is not a limit the two-sided core enforces.

## Review fixes

s8-rev: no findings. Nothing to change.

s8-com, all in `config_b/trading.toml`. Amended commit `56af02ed` (`56af02eda843d4f13985442cb979449757b3197a`). `config/trading.toml` and `src/**` were not touched.

1. `[risk]` comment (`config_b/trading.toml:12-14`). Fixed in part. Deleted the copied `session_config` narration. Kept one line: the two-sided core enforces neither cap. That sentence is plan decision 2, not a shorter copy of A's comment. MUST KILL (move `account_cap_usdc` into a `[session]` table) skipped: it changes `session_config._TEMPLATE_SCHEMA` and A's `config/trading.toml`.
2. `# fair value` on dota-map. Fixed. Deleted.
3. `# volatility` on dota-map. Fixed. Deleted.
4. Dota-map sizing comment. Banner `# sizing / inventory` deleted. The five Wallet B lines stay: feature.json requires comments that `base_size_usdc` 20 is the map unit, that 9 × $20 = $180, and that the order is `ORDER_SHARES` = 20 shares. MUST KILL (`base_size_usdc` single-sourced from the clip tier, or renamed) skipped: the pinned profile schema and `read_template` are shared. MUST KILL (enforce 9 × $20, or stop reporting it) skipped: a loss limit is a Non-Goal, and enforcing it is a two-sided core change. The comment is the disclosure, not a second knob.
5. `# regimes` on dota-map. Fixed. Deleted.
6. `# lifecycle / exits` on dota-map. Fixed. Deleted.
7. Oddin "Clip only…" comment. Fixed. Deleted. `_SATELLITE_SCHEMA` already rejects extra keys.
8. LoL banners (`# fair value`, `# volatility`, `# regimes`, `# lifecycle / exits`). Fixed. Deleted.
9. LoL sizing comment. Fixed. Deleted. `merge_min_size` and `q_max_usdc` are derived in `read_template`.
10. `[clips.dota]` comment. Fixed in part. Deleted the `choose_clip` restatement. Kept the line that these tier names are the title whitelist. That coupling is the feature's whitelist rule and is not visible from the TOML keys alone. MUST KILL (a second whitelist list, or a new structural source) skipped: STEP-007 already reads these tier names as the only list, and this step does not edit `src/`.
11. `[clips.dota-oddin]` satellite comment. Fixed. Deleted. `_satellite_clip_table` already requires tiers only.

Kept, as the comment review said: the `signature_type` enum legend, and the line that PK and BROWSER_ADDRESS stay in env.

Checks after the edit, serial, `PYTEST_N` unset:

- pytest `tests/test_trader_compose.py` `tests/test_dota_map_config.py` `tests/test_trader_session_config.py`: 46 passed, 1 warning, 1.70s.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on staged `config_b/trading.toml`: trailing whitespace, end-of-file, check-toml, large-files passed. ruff and basedpyright skipped (no Python files staged). The amend hook passed the same checks. Working tree clean.
