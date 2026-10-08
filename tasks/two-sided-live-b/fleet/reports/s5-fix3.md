# s5-fix3 — close the last STEP-005 review items
Status: FINAL

## F1 — fixed

The absolute deadline now covers response-header reads on both the relayer and the Polygon RPC.

`requests.request(..., stream=True)` does not return until the status line and headers are complete, so the old watcher had no socket to shut down during that phase. A header byte every 50 ms never tripped the inter-read timeout, and the merge thread kept `engine._chain_lock`.

`_DeadlineAdapter` arms the urllib3 connection as soon as it is checked out. The connection's `getresponse` publishes `conn.sock` before it reads headers. `_call_until_deadline` shuts that socket down at `deadline_s` and joins the watcher, so the blocked read returns and the adapter thread exits while the wallet lock is still held. The same socket covers the body. Relayer nonce GET and submit POST use a private `requests.Session` with that adapter. `_DeadlineHTTPProvider` uses the same session and wraps every `make_request`, including receipt polls. Post-start failures stay `unknown` and keep a hash when one was already received. Prepare failures, including a nonce GET that dies before POST, stay `failed`. The provider session is closed when `merge_pairs_via_adapter` returns.

The TCP handshake is bounded by the connect timeout, which is the remaining budget at the start of that call. The socket does not exist until the handshake finishes, so the watcher cannot abort connect itself. Header and body reads are the phase that previously ran past the deadline.

`src/trader/pair_merge.py` is unchanged. `_merge_pairs` already holds `engine._chain_lock` until `merge_pairs_via_adapter` returns. The overrun was inside the HTTP call.

Tests, all on localhost, no external host:

- `test_slow_relayer_headers_stop_at_the_deadline` — POST status line trickled for 3 s, 0.25 s budget, returns `unknown` with an empty hash in under 1 s.
- `test_slow_rpc_headers_stop_at_the_deadline` — `_DeadlineHTTPProvider.make_request` against the same trickle raises in under 1 s.
- `test_slow_headers_finish_before_the_wallet_lock_opens` — real adapter under `PairMerger`, relayer-header pace and receipt-RPC pace. The chain lock is still held when the adapter returns, then released. Elapsed stays under 1.5 s with `MERGE_CALL_TIMEOUT_S` set to 0.5. The RPC pace keeps the submit hash.

## N2 — fixed

`_shutdown_socket` duplicates the live descriptor, calls `shutdown(SHUT_RDWR)` on the duplicate, and `close()`s it in `finally`. The original requests/web3 socket is left for its owner. If `socket(fileno=)` fails after `dup`, the new descriptor is closed immediately.

`test_deadline_shutdown_closes_the_duplicate_descriptor` runs the helper 24 times on socketpairs. The first 12 shut down with `OSError`. Every duplicate then fails `fstat`, the original descriptor still stats, and `/dev/fd` does not grow.

## Files

- `esports-trader/src/trader/ctf_merge.py`
- `esports-trader/tests/test_trader_ctf_merge.py`
- `esports-trader/tests/test_trader_pair_merge.py`
- `tasks/two-sided-live-b/progress.txt` (one note)

`feature.json` `passes` was not touched. No push.

## Checks

- `PYTEST_N` unset. `tests/test_trader_ctf_merge.py`, `tests/test_trader_pair_merge.py`, `tests/test_trader_two_sided_worker.py`: **84 passed, 1 warning, 4.97 s**. The warning is the existing websockets deprecation.
- `uv run python -m basedpyright`: **0 errors, 0 warnings, 0 notes**.
- Staged `make lint` (pre-commit ruff, basedpyright, whitespace): passed.

## Commit

`f605a6b987687e7893af2f9952d74963d1ff533d` on `esports-trader` `main`.

Stop merge HTTP at the absolute deadline during header reads.
