# s3-impl — STEP-003

Status: FINAL

## What changed

Commit `c779fa21` on `esports-trader` `main` (amended from `4a4018fb`): `Record pair merges in the wallet ledger as MERGED rows.`

- `src/trader/wallet_store.py` (999 lines). `apply_merge(*, tx_hash, token_ids, qty) -> bool` inserts two `MERGED` legs and two acked `merged` outbox rows in one commit, then drops both positions, stamps both chain floors, and adds `qty` to `_net_cash`. `ledger_position`, `ledger_net_cash`, and `ledger_net_cash_for_tokens` include `MERGED`. `fill_for_key` excludes `MERGED` in SQL. Repeat of the same tx returns False, including reversed token order. `hold_merge` / `release_merge` keep chain and REST size-down off those tokens until the caller releases them.
- `src/dashboard/summarize.py`. `cmd_wallet` cash sums `MATCHED`, `CONFIRMED`, and `MERGED`. `fill_rows` counts rows whose status is not `MERGED`.
- `tests/test_trader_wallet_store.py`. Six tests: sizes and cash, readers, reopen, FAILED replay, chain floor, `cmd_wallet`.

`passes` for STEP-003 in `feature.json` is still false. Progress appended to `tasks/two-sided-live-b/progress.txt`. No commit in the workspace repo. No push.

## Deviations

- The plan and the implement skill say set `passes: true`. The brief says the orchestrator sets it after review. Left false.
- Tests use a local `_merge_key` helper. Same key format as the plan (`merge:{tx}:{token_id}`).

## Commands

- `pytest tests/test_trader_wallet_store.py -q`: 49 passed.
- Ledger-reader files named in the plan: 524 passed, 1 failed. The failure is `test_user_stream_is_bound_on_the_host_run_path`.
- `python3 src/dashboard/summarize.py --self-check`: `self-check ok`.
- `wc -l src/trader/wallet_store.py`: 994.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the three staged files: ruff check, ruff format, basedpyright passed. The commit hook passed the same checks.
- `make test` (`PYTEST_N` unset): 2 failed, 2946 passed, 8 warnings, 428.09s.

## Open issues

- `apply_failed_fill`'s docstring still says the rebuild replays MATCHED and CONFIRMED only. `ledger_position` now also replays MERGED. The plan said not to edit that function. The FAILED-after-merge test covers the real replay.
- `test_user_stream_is_bound_on_the_host_run_path` raises `AttributeError: 'WalletHost' object has no attribute '_mode'` at `wallet_host.py:1238`. That line is on HEAD. This step does not touch `wallet_host.py`.
- `test_home_renders_all_sections` fails because no markdown contains `доступно`. `home_view.render_status` prints that only when `strip.available` is set. Home uses `summarize` for Berlin day bounds, not `cmd_wallet`. Re-ran the test alone: still failed. Not caused by this step.

## Review fixes

Amended the step commit to `c779fa21`. `passes` is still false.

### s3-rev

1. major — chain reconciliation can subtract a merge before `apply_merge`. **Fixed.** `hold_merge` marks both tokens. `rest_size_down_skip_reason` returns `"merge"` while held, so `_apply_chain_balance` and REST reconcile skip the size-down. `release_merge` clears it. `apply_merge` still subtracts from the store and still clamps. The hold is not released inside `apply_merge`: STEP-005 must hold before the relayer submit and release on every terminal outcome after core delivery. The hold is process memory; a restart drops it. Regression: `test_held_merge_blocks_chain_write_down` delivers the post-merge chain snapshot first and then requires cached and ledger YES=50, NO=0.

### s3-com

1. Delete the SUPERSEDED comment and reshape statuses into named sets. **Comment deleted. Set reshape skipped.** The comment sat in the hunk that added the merge constants and only restated that readers omit SUPERSEDED. Named sets would rewrite every ledger query to remove a sentence the filters already express, on a file that is 999 lines.

### Checks after the fix

- `pytest tests/test_trader_wallet_store.py tests/test_trader_engine_seams.py tests/test_trader_chain_balances.py -q` (`PYTEST_N` unset): 141 passed.
- `python3 src/dashboard/summarize.py --self-check`: `self-check ok`.
- `wc -l src/trader/wallet_store.py`: 999.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the staged files: ruff format reflowed the new test once; the second run passed, and the amend hook passed the same checks.
