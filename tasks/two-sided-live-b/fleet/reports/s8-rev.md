# s8-rev — STEP-008 review
Status: FINAL

## Scope and method

Report-only review of `git -C E diff 2fe56a67..be0cc83c`. Reviewed commit `be0cc83cc822b1fd575ab5aae6579f04a7ef5915` on branch `two-sided-s8`, with committed files read through `git show` and the clean `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-s8` worktree. The brief explicitly excludes the absent STEP-006/007 implementation from findings.

Read the shared context, agent brief, workspace and project instructions, `feature-json-step-review/SKILL.md`, all `review.instructions` in `W/.feature-json.config.json`, STEP-008 requirements, the STEP-008 plan, and `reports/s8-impl.md`. Applied the strict maintainability standards and also checked wallet-B isolation, configuration sizing, shutdown grace, LoL suppression, compression, and unchanged service A.

## Verdict and findings

**no findings** — no blocker, major, minor, or nit established in the scoped diff. STEP-008 passes this maintainability and correctness review. This is approval of the configuration/test commit within the brief's scope, not evidence that the isolated worktree can already run the complete two-sided trader.

## Maintainability assessment

- The four-file diff stays in the canonical configuration and existing configuration-test layers. It introduces no product Python source changes, strategy-specific branches in shared runtime flows, new optional parameters, casts, loose `Any` contracts, or anonymous multi-field tuples.
- `config_b/trading.toml` remains an ordinary independent template. Sharing YAML anchors or building a new configuration-generation layer would add indirection and require touching service A; neither is warranted for this step.
- `_binds_by_target` (`tests/test_trader_compose.py:275`) earns its place by letting the test express the complete mount contract by destination. Its `object`/dictionary boundary is external Compose JSON, not a new loosely typed application model.
- The exact environment contract and typed `ClipTable`/`ClipTier` expectations test wallet routing and tournament sizing directly. They do not replace canonical template validation or clip selection with test-only business logic.
- The underscore-safe service regex fixes the existing parser so that B is no longer absorbed into A's text block.
- File sizes remain small: `compose.yaml` 173 lines, B template 106 lines, compose tests 248 → 321 lines, session-config tests 458 → 477 lines. No file crosses the 1,000-line threshold, and no extraction would materially simplify this implementation.
- The added Python test lines contain no comments, docstrings, or suppressions. The TOML comments are explicitly required by STEP-008 and its plan; treating them as prohibited would contradict the requested configuration documentation. Existing comments/suppressions in the test files were not expanded.

## Correctness checks

| Area | Evidence and result |
| --- | --- |
| Wallet and strategy environment | `compose.yaml:71` maps `PK`, `BROWSER_ADDRESS`, the three builder credentials, and Telegram chat to the respective B placeholders. Dota mode is literal `live`, LoL mode is quoted `off`, and strategy is `two_sided`. The exact environment test passes; other services contain neither `DOTA_STRATEGY` nor B-placeholder references. |
| Process parity | Parsed Compose confirms B's `build`, `init`, `restart`, daemon command, and logging equal A's corresponding fields. B still runs `daemon --mode live`. |
| Configuration and state isolation | `compose.yaml:86` provides exactly the six required binds. Only `/app/data/trader` is writable. Its host source is `data/trader_live_b`; `/app/config` is bound to `config_b`. `wallet_db_path` resolves beneath the mounted trader tree, so B's `wallet/live.db` and lock are isolated from A. |
| Configuration values | Parsed B TOML equals parsed A TOML with exactly the specified changes: signature type 3, account cap 100000.0, both Dota base sizes 20.0, and the sole BLAST Slam tier at 20.0 in both Dota clip tables. All other values match A. `read_template` validates the file and resolves GRID and Oddin clips as expected. |
| Order size | The new comments correctly distinguish the profile/clip's dollar unit from `ORDER_SHARES = 20` in `strategy/two_sided.py:7`. No order-size or pricing formulas are changed by this diff. |
| Engine cadence | B's parsed `[engine]` equals A's. The `/app/config` bind also covers the cadence reader's template path, preserving backtest/live cadence parity. |
| LoL suppression | On the reviewed tree, `assigned_games('live')` admits Dota only with the supplied modes; archive and model loading iterate assigned games. Discovery constructs `LolLeagueFilter` only for LoL, and `_sync_lol_book_watch` returns before reading LoL resources when there is no LoL archive assignment. The canonical whitelist loader is called from the filter constructor, not at import. No extra LoL file or bind is needed by this configuration. |
| Blank-wallet fallback | The Dockerfile's ignore file excludes the repository `.env`, and B has no `env_file` or `.env` bind. A blank B wallet setting therefore cannot obtain A's key through the repository dotenv fallback in this image. No secret values were read or printed. |
| Shutdown grace | `compose.yaml:70` gives 240 seconds. The test checks the static minimum against merge call timeout 190 + fence 20 + drain 5 = 215 seconds. This matches the plan's deliberate increase from the feature text's 200 seconds. |
| Compression | `compose.yaml:161` adds the B root argument and `compose.yaml:168` its writable bind while retaining both existing roots. The existing compressor skips `wallet`, requires cleanup/age eligibility for compression, and only processes its explicit feed-file whitelist; session journals and wallet state are not compressed. |
| Service A / Follow300 | Independent comparisons show `config/trading.toml`, the complete `live` block, and the complete `paper` block byte-identical to parent `2fe56a67`. No `src/**` diff exists. This step introduces no SELL path or change to prices, cancels, or Follow300 logic. |

## Independent validation

Executed in the committed `esports-trader-s8` worktree:

1. Targeted serial pytest through `uv run --offline --no-sync --no-env-file python -B -m pytest` with `PYTHONPATH=src:scripts:../prediction-market-backtesting`: `tests/test_trader_compose.py`, `tests/test_dota_map_config.py`, and `tests/test_trader_session_config.py` — **46 passed, 1 warning, 2.07 seconds**. The warning is the installed `websockets.legacy` deprecation, unrelated to the diff.
2. Independent Python assertions against Git blobs and `docker compose config --no-interpolate --format json` — **passed**: A/paper byte equality, B's exact parsed TOML differences, process/build/logging parity, the four-service set, 240-second grace, and the six B mount targets. Only non-secret results were printed.
3. `git diff --check 2fe56a67..be0cc83c` — **passed**.
4. `git diff --exit-code be0cc83c -- compose.yaml config_b/trading.toml tests/test_trader_compose.py tests/test_trader_session_config.py` — **passed**. Worktree HEAD is the reviewed commit; final `git status --short` is empty.

Pytest cache was disabled, bytecode writing was disabled, and uv cache / temporary test files were directed to this review's unique scratch directory, `R/work/s8-rev/validation.D5A4Ki`. No files in E or its worktree were edited, staged, or committed. No full suite, lint hooks, backtests, container build/start, SSH, trading, merge submission, or network API probes were run. The implementer's basedpyright and staged-lint results are recorded in `s8-impl.md`; I did not rerun them.

## Accepted decisions and review limits

- Use the supplied worktree/branch and leave the implementation unchanged, as the brief requires. No main-branch switch or cherry-pick was attempted.
- Accept 240-second grace because it is the plan's explicit decision and improves the single-merge shutdown allowance. The timeout test is a static floor, not a proof of a hard total shutdown bound. The plan explicitly accepts that two serialized threshold merges or cancellation during the final merge require the STEP-009 restart guard; these are not new findings against this commit.
- Accept the copied unused LoL template tables and the retained Dota default clip of 5.0 as planned. STEP-007 owns the nonempty title whitelist and worker/startup checks. Per brief, the absence of STEP-006/007 from this isolated tree is not a defect in STEP-008; combined startup and worker behavior remain outside the validation performed here.
- Accept the documented distinction that 9 × $20 map room is not enforced by the two-sided core. Introducing a new cap would change strategy behavior outside this step's scope. The $20 profile/clip values do not redefine the 20-share order constant.
- Plain B-variable interpolation preserves A's Compose operations when B settings are absent. The plan already assigns the blank Telegram-chat check to STEP-009; the reviewed commit does not claim it fails closed.
