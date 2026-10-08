# s7-com — comment review of STEP-007
Status: FINAL

Scope: `git -C E diff f605a6b9..629657b0` (STEP-007). Comments and suppressions this diff adds or touches, plus comments inside functions this diff refactors. Report only; nothing edited.

Line numbers are at commit `629657b0`.

## Findings

1. `src/trader/wallet_host.py:702-703` — inside `__init__` (refactored: gained `strategy` param and `self._strategy`):
   ```
   # First-game alias for tests and leftover callers. Sidecar refresh
   # groups workers by game and scans that game's archive.
   self.archive_root = archive_by_game[games[0].game]
   ```
   Confession comment for a dead alias, and stale: nothing in `src/` ever reads `self.archive_root` — `_sidecar_refresh_loop` passes `self._archive_by_game` per game (`wallet_host.py:1305`). Only writers exist: this line plus tests (`test_trader_wallet_host.py:465,2357,2393`). **DELETE** the comment; **MUST KILL** `WalletHost.archive_root` — drop the attribute and the three test writes (those tests already set `_archive_by_game`, so nothing else is needed).

2. `src/trader/wallet_host.py:773` — docstring of `_install_runtime_seams` (refactored: gained the `install_adapter_merge` block):
   `"""No-Gamma resolution, ledger cash, latch, book readiness, paper MDS, no FV patch."""`
   Table-of-contents narration of a private method; "no FV patch" maps to no call in the body (stale or unverifiable). The install/wrap call names already self-describe. **DELETE**.

3. `src/trader/wallet_host.py:1574-1577` — docstring of `_pick_and_run` (refactored: gained the `worker_class` dispatch):
   `"""Pick a feed, announce once, and run MatchWorker until it returns. / No usable source yet is not a crash: mark the wait so the next discovery retries."""`
   Narration, now stale — the worker can be `TwoSidedWorker`. The retry-on-no-feed behavior is visible in `record.waiting_for_feed = True` + `_log_skip`. **DELETE**.

4. `tests/test_trader_wallet_host.py:444` — docstring of `_bare_host` (touched: gained `_strategy`/`clip_tables` lines):
   `"""WalletHost with maps only; skips Engine seam install."""` — narrates what the 20-line body shows. **DELETE**.

5. `tests/test_trader_wallet_host.py:2365` — docstring of `test_run_builds_one_discovery_per_assigned_game` (refactored: gained `whitelist_by_game`):
   `"""Dota gets the process Steam client and the live blacklist; LoL gets neither."""` — narrates the asserts; now incomplete (doesn't mention the whitelist the diff added). **DELETE**.

6. `tests/test_trader_wallet_host.py:2390` — `host.steam_client = steam  # type: ignore[assignment]` inside the refactored test above. `assignment` catches real type bugs; the suppression exists only because `steam` is a bare `SimpleNamespace`. **DELETE** the suppression; **MUST KILL** the untyped double — type `steam` as a `SteamClient`-shaped fake (or `cast(SteamClient, ...)`) so the assignment checks clean.

7. `tests/test_trader_host_resources.py:79` — docstring of `_patch_host_runtime_fakes` (refactored: `fake_init` body changed):
   `"""Stub flock, config dir, archive, model, and WalletHost lifecycle."""` — narration. **DELETE**.

8. `tests/test_trader_host_resources.py:139` — docstring of `_install_resource_fakes` (refactored: cfg moved to `_use_wallet_cfg`):
   `"""Stub IO so run_wallet_daemon records calls without opening credentials."""` — narration; the `monkeypatch.setattr` list shows it. **DELETE**.

9. `tests/test_trader_host_resources.py:167` — docstring of `test_dota_live_opens_wallet_and_steam` (touched: gained `host_strategies` assert):
   `"""Live Dota takes the CLOB wallet, Steam, and Dota risk clips."""` — restates the asserts. **DELETE**.

10. `tests/test_discovery_cadence.py:61` — docstring of `build_poll_target` (touched: gained the `title_whitelist` arg):
    `"""Build one discovery whose discover returns one scripted match per call."""` — narration of a 10-line fixture. **DELETE**.

11. `tests/test_trader_discovery.py:75` — docstring of `test_league_matches_reads_the_league_not_the_teams` (renamed + body extended):
    `"""The needle matches the `(BOx) - league` suffix; a team name is not a hit."""` — documents our own code's behavior; the renamed function (`league_matches`) and the test name already carry it. **DELETE**.

12. `tests/test_trader_lol_discovery.py:76` — docstring of `make_lol_discovery` (touched: gained the `title_whitelist` arg):
    `"""LoL discovery with no Steam client."""` — narrates the `None` in the call it wraps. **DELETE**.

13. `tests/trader_discovery_fixtures.py:318` — docstring of `make_discovery` (touched: gained `title_whitelist` param):
    `"""Build one MarketDiscovery on the fake API and one test Steam key."""` — narrates the fixture assembly visible in the body. **DELETE**.

## Skips (keep clause)

- `tests/test_trader_two_sided_host.py:1` — `# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false`. **KEEP**: suppression of rules that are pedantic for test-internal access — the file pokes `host._strategy`, `host._mode`, `host._pick_and_run`, imports `_bare_host`/`_skip_fork_merge`, and uses an `addfinalizer` lambda. Identical header already in `tests/test_trader_engine_seams.py:5`; it is the repo convention for tests that drive internals. All three suppressed rules are exercised by real lines in the file (no dead suppression).
- `src/trader/discovery.py:199` — `discover` docstring `"""Run one synchronous discovery cycle and return the linked matches."""`. **KEEP**: doc comment defining a public API contract — "synchronous" is a real contract property of `MarketDiscovery.discover` for its callers.
- `src/trader/host_resources.py:141` — `open_wallet_host` docstring. **KEEP**: public-function contract (flock acquisition, journal check, host construction).
- `src/trader/host_resources.py:235` — `run_wallet_daemon` docstring. **KEEP**: public-function contract ("idle when no games" is behavioral contract).
- `src/trader/wallet_host.py:1231` — `run` docstring `"""Start the Engine, fence leftover finals, then attach matches from discovery."""`. **KEEP**: public lifecycle-method contract.
- `tests/test_trader_host_resources.py:24` — `ResourceLog` class docstring `"""Records which host-resource calls ran."""`. Borderline scope — the docstring sits on the class, not inside the touched `__init__`; kept clause: docstring on the recorder's contract. (If counted in scope, it is narration like findings 7-9.)

## Deleted-by-diff (good, no action)

- The old `is_league_blacklisted` docstring (`(BOx) - <league>` suffix note) was removed with the rename to `league_matches`, and `_drop_blacklisted`'s docstring went with `_drop_skipped_titles`. New functions (`league_matches`, `_title_skip_reason`, `read_dota_strategy`, `require_two_sided_start`, `select_title_whitelist`) and all new tests carry zero comments — diff adds no narration.

## Notes

- No commented-out code, banners, or workaround sermons were added. The only new suppression is the test-file pyright header (skip above).
- `src/trader/host_resources.py:178-183` logs `cfg.secrets.browser_address` (funder) at INFO — a public address, not a secret; not a comment issue, noted only because it prints a wallet identity.
