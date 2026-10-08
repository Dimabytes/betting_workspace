# s5-rev — second verification of STEP-005 review fixes
Status: FINAL

## Verdict

Request changes. **F1 is partly resolved; F3 and N1 are resolved.** The deadline fix introduced **N2, a major descriptor leak**. No new blocker, nits, or unrelated findings are reported.

Scope is only the three still-open items from s5-rev-verify.md and new blocker/major defects introduced by this amendment. All product locations below refer to `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` at **ec9ebe90eaf62cb71c65308da3f9d3ef5ccfc95b**, compared with 2fe56a67. Read the implementer's `Review fixes 2` answers. Product source and tests were read with git show/diff and executed from a git-archive snapshot of that commit; the concurrently edited E working tree was not used as STEP-005 source.

## F1 — partly resolved

**Locations:** `src/trader/ctf_merge.py:77`, `:224`, `:282`, `:290`, `:306`; ownership remains at `src/trader/pair_merge.py:189`.

**Resolved portion:** Receipt waiting now receives `min(180s, remaining)`. A late POST hash is retained in an unknown outcome without starting receipt polling. Each RPC receives `min(10s, remaining)` and streaming mode, with automatic provider retries disabled. The relayer watcher interrupts a trickled response body once its Response is available. The receipt-budget, late-POST, per-RPC-budget and real localhost trickled-body tests pass. The actual adapter thread still owns the wallet lock and holds until it returns.

**Remaining issue — major:** The absolute deadline still does not cover response-header reads. `requests.request(..., stream=True)` returns only after headers have been read. Until line 290, the watcher's box remains None, so `_stop_reader` cannot interrupt the active socket. Sending header bytes more frequently than the read timeout lets this phase exceed the whole-call deadline. The RPC provider has the same gap: it forwards a remaining-time read timeout and streaming flag, but has no deadline abort around the header phase. The later deadline/body checks classify the eventual result safely, but do not bound when the worker returns. Because the merge keeps physical ownership, a slow header stream can monopolize the shared wallet lock and prevent quiesce from finishing beyond the intended 190 seconds.

**Concrete reproductions using the real installed HTTP stack:** A review-owned localhost server returned the nonce immediately, then trickled POST header bytes every 50 ms. With a 250 ms whole-call budget, the adapter returned unknown only after **1.380 s**. A direct call through the committed `_DeadlineHTTPProvider` against the same slow-header fixture raised TimeExhausted only after **1.417 s**, also with a 250 ms deadline. The server deliberately completed after 24 bytes; extending the trickle extends the overrun. Neither reproduction patches the HTTP request/parser or uses an external endpoint.

**Concrete remaining fix:** Enforce the absolute deadline while connection and headers are being read, as well as during body reading, for both relayer and RPC requests. Make the active transport/socket available to deadline enforcement before awaiting header parsing, or use a transport that supports interruption throughout those phases. Keep ownership until the physical operation stops, retain known hashes, and preserve the existing post-start unknown classification. Add real-transport slow-header tests for both paths that verify bounded elapsed time and operation completion before releasing the wallet lock. Do not restore cancellation that abandons a running adapter thread.

## F3 — resolved

**Locations:** `src/trader/pair_merge.py:40`, `:91`, `:100`, `:180`, `:211`; regression test `tests/test_trader_pair_merge.py:773`.

`merge_all` now raises `MergeAccountingError(tx_hash, qty)` if the known mined merge remains unbooked after the final attempt. The merger retains `_pending` and both holds. A later `merge_all`/schedule retries local booking, and the failure no longer masquerades as normal final completion.

The final-booking-failure test passed: code after the failed `await merge_all()` was not reached, the exception carried the known tx and 120-pair quantity, holds and cash remained intact, and a later final call booked successfully with **one adapter invocation and exactly one 120 cash credit**, then released holds. This resolves the reviewed STEP-005 completion contract and retained retry path. STEP-006's caller integration is outside this verification and was not read from its changing working tree.

**Concrete remaining fix:** None for F3 in this scope.

## N1 — resolved

**Locations:** `src/trader/pair_merge.py:91`, `:96`, `:108`; regression test `tests/test_trader_pair_merge.py:821`.

The final call records whether it started with pending accounting. After successful local booking, the same `merge_all` invocation makes the additional final balance-clamp/send decision. If booking remains unresolved, the typed F3 error stops that continuation. The existing eligibility checks still gate a new send.

The regression test passed for the reviewed interleaving: the earlier 120-pair mined merge failed initial booking; another 20 NO shares were bought, acknowledged and applied to the core; one final call repaired booking and submitted the additional **20,000,000** units. Adapter amounts were exactly **[120,000,000, 20,000,000]**, remaining NO was zero, pending accounting cleared, and holds released. The earlier transaction was not repeated.

**Concrete remaining fix:** None for N1.

## New major introduced by the fix

### N2 — major — Every deadline socket shutdown leaks its duplicated descriptor

**Locations:** `src/trader/ctf_merge.py:328`, `:337`.

`os.dup(fp.fileno())` creates a new descriptor owned by the temporary socket. The finally block calls `sock.detach()`, which removes the socket object's ownership and returns the descriptor without closing it. That returned descriptor is discarded. Closing the original requests Response does not close this duplicate, so every deadline body abort permanently adds an open descriptor to the process.

The helper is used for both nonce GET and submit POST. A nonce timeout is a prepare failure and remains retryable; unknown submits also accumulate across maps in the long-running service. Repeated aborts can therefore exhaust the daemon's descriptors and break unrelated feed, cancel and persistence operations. This is a process-wide resource failure introduced by the new abort mechanism.

**Concrete reproduction:** Called the committed shutdown helper 12 times with independent socketpairs and the same response/raw/fp shape the helper reads. Closed every original socket and file object, then checked each recorded duplicate with `os.fstat`. **All 12 duplicated descriptors remained open.** The reproduction closed its own leaked duplicates afterwards. It made no external connection.

**Concrete fix:** Close the duplicated socket in finally (`sock.close()`), or manage it with a context manager that closes it after shutdown. The original requests-owned socket can retain its current lifecycle. Add a repeated-abort test that verifies the duplicate is closed even when shutdown raises and that descriptor use does not grow.

## Verification and limits

- Personally ran the pinned commit's three step/dust test files serially: **71 passed, 1 warning, 1.53 s**. The warning concerned pytest assertion rewriting for an already-imported anyio module.
- Executed independent real-transport slow-header reproductions for relayer and RPC, and the descriptor-lifecycle reproduction. Harness: `fleet/work/s5-rev/verify2_transport_repros.py`; test runner: `fleet/work/s5-rev/verify2_run_tests.py`.
- The harness rejected external socket connect/connect_ex calls; **zero external connections were attempted**. HTTP fixtures bound only to localhost, using synthetic test credentials and transaction hashes. No *_B keys or E/.env were read, and no real order or transaction was sent.
- The initial snapshot run lacked pinned config needed by dust fixtures and could not bind its localhost test server in the filesystem sandbox. Added config from git archive at ec9ebe90 to review-owned scratch and reran with localhost binding allowed. The completed 71-test result above is the corrected run, not those setup failures.
- Read-only on E: no product edits, staging, commits, pushes, SSH, data changes, or cleanup of another agent's files. All review artifacts and test output stayed under `fleet/work/s5-rev/` and this report.
- F2/F4/F5 were not reopened; no full-suite, STEP-006 or unrelated maintainability audit was performed.

Decision without owner input: accept F3 and N1, keep F1 open for the demonstrated header-phase overrun, and require N2's descriptor ownership fix before STEP-005 passes.
