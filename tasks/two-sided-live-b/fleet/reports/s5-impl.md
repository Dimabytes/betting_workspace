# s5-impl — STEP-005
Status: FINAL

## What changed

Commit `ec9ebe90` on `esports-trader` `main` (amended from `2fe56a67`, itself amended from `da5ce546`): `Merge YES+NO pairs through the pUSD adapter with phase-classified outcomes.`

- `src/trader/ctf_merge.py` (173 lines). `merge_pairs_via_adapter` signs a deposit-wallet batch, posts it, and waits for a receipt. Every end is `merged`, `failed`, or `unknown`. `install_adapter_merge` replaces `engine._maybe_merge` with a no-op. Adapter constants moved here from `scripts/merge_probe.py`.
- `src/trader/pair_merge.py` (204 lines). `PairMerger.schedule` starts a threshold merge. `merge_all` is the final merge. Under `engine._chain_lock` it holds both tokens, calls the adapter, books a success through `apply_merge` and `consume_core_outbox`, then releases the hold. Failed pauses. Unknown disables the map. A merged outcome refreshes collateral after the lock.
- `scripts/merge_probe.py`. Imports `CTF_COLLATERAL_ADAPTER`, `PUSD`, `ADAPTER_ABI`, `ZERO32`, `PARTITION`, and `SHARE_BASE_UNITS` from `trader.ctf_merge`. The send path is unchanged.
- `tests/test_trader_ctf_merge.py`. Fake relayer and web3. No network.
- `tests/test_trader_pair_merge.py`. Fake adapter. Includes the hold across submit and the release after consume, failure, unknown, timeout, and a raised exception.

`passes` for STEP-005 in `feature.json` is still false. Progress appended to `tasks/two-sided-live-b/progress.txt`. No commit in the workspace repo. No push. No relayer call, no RPC, no `*_B` keys.

## Deviations

- The plan and the implement skill say set `passes: true`. The brief says the orchestrator sets it after review. Left false.
- STEP-003 added `hold_merge` / `release_merge` after the plan. The hold starts immediately before the adapter call. It is released after booking, or kept when a mined merge has not been booked yet. It is not taken when the chain read is stale or the amount is below the minimum.
- STEP-004's `accept_if_proven` already replays a lagging outbox before it reads the ledger. This step does not change that path. `PairMerger` still delivers through `consume_core_outbox`.

## Commands

- Step tests (`test_trader_ctf_merge.py`, `test_trader_pair_merge.py`, `test_trader_dust_sweep.py`): 50 passed (18 + 20 + 12), 10 warnings, 3.52s.
- Neighbor list from the plan: 441 passed, 20 warnings, 225.71s.
- `PYTHONPATH=src uv run python scripts/merge_probe.py --help`: usage printed, exit 0.
- Comment grep on `ctf_merge.py` and `pair_merge.py`, excluding `# pyright:`: no matches.
- `uv run ruff check --select C901 src/trader/ctf_merge.py src/trader/pair_merge.py`: passed.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the 5 staged files: ruff check, ruff format, basedpyright passed. The commit hook passed the same checks.
- `git status --short` after the commit: only the two untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.
- `make test` was not run. The plan leaves the full serial suite to STEP-010.

## Commit

`ec9ebe90eaf62cb71c65308da3f9d3ef5ccfc95b` (amended from `2fe56a67`)

## Review fixes

s5-com had no findings. Nothing to change.

### F1 — fixed

The adapter thread is awaited under `engine._chain_lock` until it returns. `wait_for` no longer abandons it. `wait_inflight` uses `asyncio.shield`, so cancelling the waiter does not cancel the merge. `cancel()` sets a flag that blocks a new submit and does not cancel the running task. Nonce GET and the submit POST go through `_relay_http` with the remaining deadline as the requests timeout. After the deadline, and before POST, the call returns `failed` and does not submit. Each Polygon RPC has a 10s request timeout. An exception from the thread, once the thread has ended, disables the map and alerts unknown. Tests: gated thread plus a second map (`active max == 1`), cancel while gated, cancelled waiter, expired deadline, and late prepare.

The wallet lock is released when the thread has returned. A failed local booking keeps the token hold, not the lock, because the chain transaction is already finished. Another map may use the relayer. This map cannot submit again until the booking retry clears the hold.

### F2 — fixed

`schedule` and `merge_all` share `_start`. `merge_all` waits for the in-flight task before it decides. Eligibility is checked again under the lock, and again after `read_fresh_balances`, immediately before the hold. A final merge skips only the $130 / 5-pair threshold, quiesce, and pause. Disabled, cancel, paper mode, recovery, and an unacked MATCHED still block it. Tests: recovery plus unacked while queued on the lock, recovery flipped during the balance read, unknown then a queued `merge_all`, and `merge_all` during recovery and with an unacked MATCHED.

### F3 — fixed

A mined outcome is stored as `_pending` until `apply_merge` returns. On `OperationalError` the map stays enabled for a local retry only, the hold stays, and Telegram gets `trader merge accounting: ... tx ...`. The next `schedule` books that pending merge and does not call the adapter. The test sees one adapter call, one cash credit of 120, two merged outbox rows, the accounting alert, then the success alert.

### F4 — fixed

`_builder_headers` runs in the prepare `try`, before POST. Only `_relay_http` is inside the post-start handler. A malformed builder secret (`abcde`) and a header-generation exception are `failed` with an empty POST body. A `RelayerApiException` with `status_code is None` from the POST is still `unknown`.

### F5 — fixed

`failed` after POST requires a parsed object with a non-empty string `error`. Empty 408, empty 429, and HTML 403 are `unknown`. A 500 body `{"error": "quota exceeded"}` and a 200 body `{"error": "invalid signature"}` with no tx hash are `failed`. Quota 429 and signature 401 JSON stay `failed`. The existing unknown test still shows that an unknown outcome blocks both `schedule` and `merge_all`.

## Checks after the fix

- Step tests (`test_trader_ctf_merge.py`, `test_trader_pair_merge.py`, `test_trader_dust_sweep.py`): 65 passed, 10 warnings, 3.50s.
- Comment grep on the two new modules, excluding `# pyright:`: no matches.
- `uv run ruff check --select C901 src/trader/ctf_merge.py src/trader/pair_merge.py`: passed.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the four staged files: ruff check, ruff format, basedpyright passed. The amend hook passed the same checks.
- `git status` after the amend: only the two untracked `validation_*ts-regress-*` dirs.
- Neighbor pytest was not rerun. Those files are unchanged.

## Open issues

- `test_user_stream_is_bound_on_the_host_run_path` was already failing before this step (`WalletHost` has no `_mode`). This step does not touch `wallet_host.py` and did not rerun that test.
- STEP-006 still has to plug `PairMerger` into `TwoSidedWorker`: slot type plus a no-op `sweep`, `merge_all` after the fence and before the Telegram snapshot, `zero_token_sizes`, and `unregister_worker`, and `wait_inflight` before cancel. `merge_all` raises `MergeAccountingError` while a mined merge is unbooked. That caller must not snapshot, zero, or unregister until a later `merge_all` books it.

## Review fixes 2

Verification `s5-rev-verify.md` at `2fe56a67`. F2, F4, and F5 were already resolved there. Left unchanged.

### F1 — fixed

The 190s deadline is absolute for the whole call, including the receipt wait. After POST, a deadline that has already passed returns `unknown` and keeps the tx hash. It does not start receipt polling. The receipt wait is `min(180s, remaining)`. Each Polygon RPC uses `min(10s, remaining)` and `stream=True`, so one response cannot reset the budget by trickling. The relayer read does the same: a daemon watcher shuts the socket down at the deadline, and the merge thread waits until that read returns. `wait_for` is still not used. Tests: receipt budget about 0.5s rather than 180s, late POST keeps the hash and skips the receipt, each RPC timeout stays within the remaining budget, and a local trickled body returns `unknown` in under 1.5s.

### F2 — skipped

The verification marked this resolved. No remaining defect.

### F3 — fixed

`merge_all` raises `MergeAccountingError` with `tx_hash` and `qty` when a mined merge is still unbooked. The hold and `_pending` stay on the merger. The next `merge_all` or `schedule` retries `apply_merge` only. The test sets a cleanup flag only after `merge_all` returns; the flag stays false, then a second `merge_all` books the same tx: one adapter call, one 120 cash credit, holds released.

### F4 — skipped

The verification marked this resolved. No remaining defect.

### F5 — skipped

The verification marked this resolved. No remaining defect.

### N1 — fixed

After a pending booking succeeds, the same `merge_all` continues into the final balance clamp and send. If that booking is still unresolved, it raises `MergeAccountingError` and does not send. Disabled and unknown still block a new submit. The test starts from a failed book of 120 pairs, acknowledges 20 more NO in the store and the core, then one `merge_all` submits `20000000` and does not repeat `120000000`.

## Checks after review fixes 2

- Step tests (`test_trader_ctf_merge.py`, `test_trader_pair_merge.py`, `test_trader_dust_sweep.py`), serial, `PYTEST_N` unset: 71 passed, 1 warning, 2.82s.
- Comment grep on the two new modules, excluding `# pyright:`: no matches.
- `uv run ruff check --select C901 src/trader/ctf_merge.py src/trader/pair_merge.py`: passed.
- `uv run python -m basedpyright` on those two modules and their tests: 0 errors, 0 warnings, 0 notes.
- `make lint` on the four staged files: ruff check, ruff format, basedpyright passed. The amend hook passed the same checks.
- Commit `ec9ebe90eaf62cb71c65308da3f9d3ef5ccfc95b`.
- `git status` after the amend: only the two untracked `validation_*ts-regress-*` dirs.
- Neighbor pytest was not rerun. Those files are unchanged.
