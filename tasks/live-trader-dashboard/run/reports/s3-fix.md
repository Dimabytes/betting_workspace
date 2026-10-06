74ed1ec9
Status: FINAL

# STEP-003 review fixes

Commit `74ed1ec9` on `main` in esports-trader (not pushed). 5 files changed:
`src/dashboard/live_hub.py` (1027 → 762 lines), `src/dashboard/balance.py`
(391 → 303), new `src/dashboard/hub_types.py` (405), new
`src/dashboard/collateral.py` (95), `tests/test_dashboard_live_hub.py`
(+3 tests, private pokes removed where pure functions now exist).

## Findings

### s3-review.md #1 — blocker: field-bag model, 1027-line live_hub.py — FIXED

- Each lane now stores one frozen slice at its apply site: `_apply_disk`
  stores `WalletFacts` via `wallet_facts()` (+ the reserve it computes),
  `_apply_catalog`/subs loop store `SubscriptionFacts`, the logs loop stores
  `LogsFacts`, and the day lane stores `DayState(result, snapshot)` via
  `merge_day_state()`. `_build_snapshot` assembles stored slices + tracker
  snapshot + book tuple; it no longer re-flattens per-source fields.
- `LiveHub.__init__` drops ~15 parallel published-field attributes
  (`_day_*`×9, `_subs_*`×5 minus error, `_log_*`×4, `_wallet_read_at`,
  `_subs_applied_at`, `_subs_markets`, `_subs_missing`, `_subs_conflicts`).
- `desired_subscriptions(map_views, token_cids)`, `merge_day_state`,
  `wallet_facts`, `classify_map_views`, `tail_paths`, `reserve_snapshot`,
  `run_day_job`, `fail_day_state`, `merge_service_logs`, `fail_service_logs`
  are pure module functions in `hub_types.py` beside the slices.
- `PRODUCTION_TIMING.catalog_s = catalog.CATALOG_REFRESH_S` — one discovery
  constant; the lane no longer wakes at 30 s for a catalog that refreshes at
  60 s.
- Day/subs tests now call the pure functions directly (no `hub._map_views`,
  `_wallet_snap`, `_cfg`, `_apply_day`, `_publish` pokes); the desired-subs
  test no longer constructs a hub at all.
- `reportPrivateUsage=false` stays on the test file: tests still drive
  `HubBooks` internals (`_connect_and_listen`, `_ws`), `_acquire_hub`/
  `_swap_hub`, `hub._apply_disk`/`_tail_views`, and `helpers._http_client` —
  same keep-clause as the comments review.

### s3-review.md #2 — major: reduce outside the try kills lanes — FIXED

- `_disk_loop`, `_catalog_loop`, `_subs_loop`, `_day_loop`, `_logs_loop` now
  wrap submit+reduce in one catch-sleep-continue: a failure records the
  source error (`_disk_error`+merged wallet facts, `_catalog_error`,
  `_subs_error`, `fail_day_state`, `fail_service_logs`), publishes, and keeps
  the lane running.
- `_balance_loop` wraps `finish_request`; a reduce failure logs and calls
  the new `BalanceTracker.abort_request()` so the stuck inflight flag can
  never wedge the lane.

### s3-review.md #3 — major: unbounded caches — FIXED

- `_tail_views` is rebuilt per disk pass
  (`{item.path: item.view for item in result.tails if item.view is not None}`),
  so journals that leave the top-64 no longer feed `classify_entry` stale
  views. Covered by `test_tail_views_rebuilt_per_disk_pass`.
- `BalanceTracker` caps `_pending` at `PENDING_LIMIT = 64` (evicts oldest on
  insert, newest kept) and exposes a cumulative `pending_dropped` on
  `BalanceSnapshot`. Covered by `test_tracker_pending_evidence_capped`.

### s3-review.md #4 — major: balance client boundary — FIXED

- `load_balance_account` builds `WalletConfig(**section)` when `[wallet]` is
  a dict (`WalletConfig()` otherwise); a malformed section raises pydantic
  `ValidationError`, which `default_hub_config` converts into
  `account_error="invalid wallet config: <Type>"` instead of guessed host/
  signature fallbacks. Covered by a `pytest.raises(ValidationError)` case.
- `build_balance_client`, `CollateralReader`, `BalanceClient` protocol,
  `_status_code`, `_retry_after_s` moved to `collateral.py`, which owns the
  `reportMissingTypeStubs` pragma. `balance.py` lost the pragma and all SDK
  imports.
- Partial disagreement: the `cast(AssetType, AssetType.COLLATERAL)` is NOT an
  identity cast — `AssetType` is a plain class whose members are `str`
  literals, so without it basedpyright reports `Literal['COLLATERAL']` not
  assignable to `AssetType`. Kept (inside `collateral.py`).

### s3-review.md #5 — minor: factory params on public registry — FIXED

- `get_live_hub()` / `replace_live_hub()` are zero-argument and only start
  `LiveHub(default_hub_config())`. The factory-taking logic moved to
  internal `_acquire_hub(factory)` / `_swap_hub(factory)`, which the
  singleton test now calls.

### s3-comments.md #1 — `# type: ignore[index]` — FIXED

- Replaced the deliberately type-invalid assignment inside
  `pytest.raises(TypeError)` with
  `assert isinstance(snap.accrual_per_game, MappingProxyType)`. No
  suppressions remain in the diff beyond the two previously-reviewed
  file-level pyright pragmas.

## Validation

```
PYTHONPATH=src:scripts uv run python -m pytest tests/test_dashboard_live_hub.py tests/test_dashboard.py -q
    75 passed in ~2.4s   (72 prior + pending-cap + tail-rebuild + day funder-gate)

uv run python -m ruff check src/dashboard tests/test_dashboard_live_hub.py tests/test_dashboard.py
    All checks passed!

uv run python -m ruff format --check src/dashboard tests/test_dashboard_live_hub.py tests/test_dashboard.py
    14 files already formatted

uv run python -m basedpyright
    0 errors, 0 warnings, 0 notes
```

Pre-commit hooks (ruff check/format, basedpyright, whitespace/yaml/toml/
large-file checks) passed during the commit. `feature.json` untouched;
`passes` not set. progress.txt not committed.
