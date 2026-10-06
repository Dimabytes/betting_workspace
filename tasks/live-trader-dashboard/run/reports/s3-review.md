STEP-003 review
Status: FINAL

Scope: `git diff e4c659d494a096d2543d902a99dfeff54b04a09c..782c0056` in esports-trader. `market_books.py` is the right fork boundary. `wallet.py` checkpoint retention and the fill-boundary flag are the right shape. `reserve.py` and `logs.py` are one-line integrations. The problems are the hub's structure, its unbounded source caches, and the balance client's hand-rolled config boundary.

## Findings

1. `src/dashboard/live_hub.py:353` — blocker

   `live_hub.py` is 1027 lines. `LiveHub` alone is about 630 of them and stamps 58 instance attributes in `__init__`. This file is the process lifecycle, five lane loops, the disk and catalog workers, the day job, the published DTOs, and the snapshot projector. A new file crossing 1000 lines is a decomposition problem. Moving the dataclass block into `hub_types.py` and leaving the class as it is only relocates the same model.

   The model is a field bag that `_build_snapshot` (`live_hub.py:922`) re-flattens on every publish, including every coalesced book tick. Day state is nine attributes (`_day_result`, `_day_attempt_at`, `_day_error`, `_positions`, `_payout_known`, `_payout_ts`, `_accrual`, `_accrual_at`, `_accrual_error`) copied into a 14-field `DaySnapshot`. Subscriptions are five attributes copied into `SubscriptionFacts`, which already exists. Logs are four attributes copied into `LogsFacts`. Wallet sessions are copied field-by-field into `SessionFacts` on that same path.

   Write the frozen slice at the apply site. `_apply_day` should store one `DaySnapshot`. The subs loop should store one `SubscriptionFacts`. The logs loop should store one `LogsFacts`. `_apply_disk` should store one `WalletFacts` (and the reserve it already computes). `_build_snapshot` then assembles those slices with the balance tracker snapshot and the book tuple. That deletes the parallel field set. `desired_subscriptions(map_views, token_cids)` and the day merge belong as pure functions beside those slices, not as methods that close over `self`.

   `tests/test_dashboard_live_hub.py` is 1271 lines and opens with `reportPrivateUsage=false` because the tests construct a full hub (five executors, neither started nor closed) and assign `_map_views`, `_wallet_snap`, and `_cfg` to reach `_desired_subs` (`tests/test_dashboard_live_hub.py:840`) and `_apply_day` (`tests/test_dashboard_live_hub.py:934`). Once those are pure functions, those tests stop poking private fields and the file-level private-usage suppression goes away. Split the test module only after that, if it is still over 1000 lines.

   Two smaller cuts fall out of the same split. `PRODUCTION_TIMING.catalog_s` is 30s (`live_hub.py:71`) while `MatchCatalog` still refreshes on `CATALOG_REFRESH_S` (60s) and the hub does not pass `refresh_s`. Discovery cadence should be that one constant. Keep the five lane loops. A shared job runner would recreate the generic scheduler this step already ruled out.

2. `src/dashboard/live_hub.py:595` — major

   Each lane catches the worker submit and then runs the reduce outside that `try`. `_disk_loop` (`live_hub.py:595`), `_catalog_loop` (`live_hub.py:614`), `_balance_loop` (`live_hub.py:661`, `finish_request` at `live_hub.py:671`), `_day_loop` (`live_hub.py:694`), and `_logs_loop` (`live_hub.py:716`) all do this. The first exception in `_apply_disk`, `_reclassify`, `_apply_day`, `finish_request`, or the log reduce kills that task. `_task_done` records `_source_fatal` and does not restart the lane, so a process-lifetime hub keeps running with a dead source.

   Put the reduce in the same catch-sleep-continue the submit already has. Record the source error, publish, and keep the loop. Do this inside each loop. Their cadences are not the same (`has_more` yield, balance wake, log backoff, funder gate).

3. `src/dashboard/live_hub.py:768` — major

   `_tail_views` is patched per path and never rebuilt. `TAIL_TRACK_LIMIT` only limits one read. A journal that leaves the top 64 stays in the dict, and `_reclassify` (`live_hub.py:786`) still feeds that stale `TailView` to `classify_entry`. Replace the dict from the current read:

   `self._tail_views = {item.path: item.view for item in result.tails if item.view is not None}`

   The same lifetime hole is `BalanceTracker._pending` (`src/dashboard/balance.py:90`, appended in `_apply` at `balance.py:151` and only trimmed in `finish_request` at `balance.py:216`). A failed balance reader never trims it, and every hub publish copies the whole tuple into `BalanceSnapshot.pending`. Cap it in the tracker (newest N, plus a dropped count on the snapshot). `_awaiting` is already bounded by unresolved MATCHED keys. The pending evidence list is not.

4. `src/dashboard/balance.py:356` — major

   `load_balance_account` re-parses `[wallet]` with an isinstance ladder and silent fallbacks to `WalletConfig()` defaults. `WalletConfig` is already that model (`chain_id`, `signature_type`, `clob_host`). Build it from the wallet table (`WalletConfig(**section)` when the table is a dict, otherwise `WalletConfig()`). A malformed table should surface as `account_error`, not a guessed host or signature type. Leave `Config.load` alone: it reads `config.toml`, loads strategy and markets, and pulls secrets.

   The same module mixes that client with the pure tracker, so the file starts with `# pyright: reportMissingTypeStubs=false` and then casts through the untyped SDK. `cast(AssetType, AssetType.COLLATERAL)` at `balance.py:336` is an identity cast. `cast(_BalanceClient, ClobClient(...))` at `balance.py:261` exists only because the constructor is untyped. Move `build_balance_client` / `CollateralReader` into a small module that owns the stub pragma and the one cast. The tracker, `parse_collateral_usdc`, and `load_balance_account` do not need it.

5. `src/dashboard/live_hub.py:995` — minor

   `get_live_hub` and `replace_live_hub` take an optional `factory`. The process getter is the STEP-004 handoff and its production contract is zero arguments. Tests already build `LiveHub(config)` directly. Keep the registry helper internal, and let the singleton test call that helper. The public functions should only start `LiveHub(default_hub_config())`.
