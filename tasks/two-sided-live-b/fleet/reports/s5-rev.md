# s5-rev — STEP-005 strict maintainability and correctness review
Status: FINAL

## Verdict

Request changes. **Five findings: one blocker and four major.** The modules are sensibly separated and all existing step tests pass, but transaction supervision and phase classification do not yet meet the live-money contract. Do not mark STEP-005 passing until these findings are fixed and covered by the regression cases below.

## Scope and method

- Code repo E: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`.
- Reviewed exactly `git diff 7b1b6990..da5ce546`. HEAD during inspection was `da5ce5461f183d174f80a6e27d72f5d9b8627970`. All finding locations are relative to E and refer to that commit.
- Read the shared context and brief fully; W/AGENTS.md, E/AGENTS.md, W/.learnings/poly-maker.md, the complete STEP-005 plan, feature.json including Resolved Questions, and s5-impl.md.
- Applied `feature-json-step-review/SKILL.md` and every `review.instructions` entry in W/.feature-json.config.json. E has no additional nested AGENTS.md or feature-json configuration.
- Inspected wallet holds, chain freshness and divergence protection, apply_merge, core outbox consumption/recovery, worker quiesce, collateral reads, and the exact installed relayer/signing SDK sources. This is a code review of the pinned implementation; no external documentation assumptions were needed.
- Report only. No edits to E, staging, commits, pushes, SSH, daemon operation, wallet inspection, live orders, or transactions. Test scratch and uv cache are under `fleet/work/s5-rev/`.

## Findings

### F1 — blocker — The coroutine owns the lock, but the transaction thread outlives it

**Location:** `src/trader/pair_merge.py:141`; related `:63`, `:74`, and `:128`.

**Problem:** `wait_for(asyncio.to_thread(...))` does not stop a thread already running. On timeout, `_call_adapter` returns unknown and `_merge_pairs` releases both token holds and `_chain_lock`, although that thread may still be obtaining a nonce, posting the signed batch, or waiting for its receipt. Consequently the wallet is no longer serialized against the physical transaction. A delayed nonce GET can even finish and initiate its POST after the logical attempt has ended. The relayer's installed HTTP helper supplies no request timeout.

Cancellation is worse: `cancel()` cancels the task, the same finally block releases the hold and lock, and `wait_inflight()` suppresses CancelledError. The map stays enabled and emits no unknown alert. Its `_task` now appears finished, so another call can start while the first thread is still active. This directly violates the no-retry rule when the outcome after submission is unknown. Even when shutdown has already marked the map quiesced, it still loses the original result and wallet ownership.

**Reproduced offline:** A first adapter stub was gated in a thread. With a 30 ms logical timeout, the observed state was `thread_active=True`, `held=False`, `wallet_lock_held=False`, `map_disabled=True`; a second map's adapter then ran before the first thread ended. In a separate cancellation case, the observed state was `map_disabled=False`, both holds released, the wallet lock released, and zero alerts. Calling schedule again started a second adapter while the original thread remained active. All gates were released and the test-owned threads completed.

**Concrete fix:** Give the physical merge one supervised operation owner. Shield its lifetime from waiter cancellation, make caller cancellation stop further scheduling rather than discard an active transaction, and keep wallet serialization and token protection until that operation has actually completed its necessary accounting. Use bounded, deadline-aware network calls and check expiry inside the thread before POST, so a timed-out prepare cannot submit later. If an outcome is lost after submission could have begun, disable that map and alert as unknown. A logical timeout must not expose the wallet as idle while the physical submitter remains active. Add gated-thread tests for timeout, explicit cancel, cancelled waiters, late pre-POST completion, and a competing map; verify no overlapping submit and no unclassified cancellation.

### F2 — major — Eligibility is checked before queueing, then ignored at the actual send boundary

**Location:** `src/trader/pair_merge.py:79`; related `:84`, `:102`, and `:119`.

**Problem:** Both entry points check mutable state before waiting for the shared wallet lock. `_merge_pairs` never checks it again after acquiring the lock or after its awaited balance read. Recovery or unacked MATCHED can therefore appear while a threshold merge is queued, yet the call still submits.

The final path can violate the stronger unknown rule: `merge_all()` checks `_disabled` once, then waits for the lock. If the already-running threshold merge returns unknown, it sets `_disabled=True` and releases the lock. The queued final merge acquires that lock and submits anyway. The current unknown test calls merge_all only after the prior operation has finished, so it misses this interleaving. STEP-006's planned wait before final merge lowers its likelihood in that caller; it does not make the new public merger contract correct.

**Reproduced offline:** While a scheduled merge waited on `_chain_lock`, the fake host entered recovery and gained an unacked MATCHED; the adapter still received `150000000` units with both flags true. In another reproduction, a final merge was queued behind a threshold merge returning unknown. Its second call observed `map_disabled_at_call=True` and still received `150000000` units.

**Concrete fix:** Centralize eligibility at the locked send boundary and revalidate after the awaited balance read, immediately before hold/submission. Reuse the threshold predicate for normal attempts. For final attempts, explicitly override only the intended threshold, quiesce and pause rules; the disabled rule must remain absolute. Have merge_all join the tracked in-flight operation before deciding whether a final call is allowed, and use the same operation ownership for both entry points. Add both queued interleavings above, including unknown followed by a queued final call, as regression tests.

### F3 — major — A known mined merge is silently lost when local booking fails

**Location:** `src/trader/pair_merge.py:148`; related `:122`, `:129`, and `:76`.

**Problem:** A receipt with status 1 is already a known success, but `store.apply_merge` is outside the booking error handling. If SQLite raises, the task exits, its hold is released, and wait_inflight suppresses the exception. No merge accounting row, cash credit, wake, collateral refresh, or failure alert follows. The map remains enabled and a subsequent schedule can replace the failed task. The known tx hash and amount are not retained in a pending booking state. Chain reconciliation can repair token sizes, but it cannot reconstruct the missing MERGED cash credit.

**Reproduced offline:** The real test WalletStateStore received a fake mined success for `120000000` units, while apply_merge raised `sqlite3.OperationalError('database is locked')`. After wait_inflight returned, `_disabled=False`, `alerts=[]`, holds were released, positions remained 150 YES / 120 NO, and the task held an OperationalError. Its adapter result carried the success tx, but the exception path discarded it.

**Concrete fix:** Retain a known successful outcome, tx hash and quantity until idempotent apply_merge completes. On a booking error, alert with that tx and prevent another on-chain attempt for the map; retry only local booking through the supervised owner or an explicit pending-booking state. Keep the relevant recovery protection until ledger/core delivery is settled. Do not relabel a known mined success as a failed chain transaction or resubmit it. Add a test where the first local booking fails and a later booking succeeds: exactly one adapter call, exactly one cash credit and outbox pair, and a visible accounting-error alert.

### F4 — major — Builder authentication signing still crosses the supposed prepare/POST boundary

**Location:** `src/trader/ctf_merge.py:139`; related `:144`.

**Problem:** The split correctly moves EIP-712 transaction signing before POST, but RelayClient._post_request also generates builder authentication headers before its HTTP call. Exceptions from this local HMAC preparation are ordinary exceptions, not necessarily RelayerClientException. They hit the generic post handler and become unknown, permanently disabling merges for the map despite zero submission. Resolved Questions explicitly classify a failure before POST as failed.

The installed signing SDK validates builder credential strings for non-emptiness at construction, then base64-decodes the secret only when generating headers. Thus this is a reachable path even with nonempty credentials and the planned startup has_builder_creds check.

**Reproduced offline:** Used an explicitly supplied throwaway PK, wallet and URLs, with nonempty builder secret `abcde`, a fake nonce, and a guarded actual POST function. Result: `MergeOutcome(status='unknown', tx_hash='', reason='post: Error')`; actual POST call count: **0**.

**Concrete fix:** Generate and validate the builder headers in the prepare phase alongside calldata, nonce and EIP-712 signing. Only the actual HTTP POST belongs inside the post-start exception boundary. Preserve the SDK's header generation and transport contract without changing the frozen fork. Add tests for malformed builder secret and header-generation exceptions, asserting failed and no POST; retain a separate post-start transport exception test asserting unknown.

### F5 — major — HTTP status alone is treated as proof of an explicit rejection

**Location:** `src/trader/ctf_merge.py:165`.

**Problem:** Every 4xx response is classified failed, regardless of whether it contains a parsed relayer error body. The feature's Resolved Questions require that error body to prove explicit rejection after POST. The plan also says “4xx with a body”; the implementation omits the body condition completely. A bodyless timeout/error reply or intermediary HTML response does not establish that the relayer rejected the signed operation, yet failed allows retries after 30/60/120/300 seconds.

Conversely, the classifier ignores a recognized parsed rejection outside the 4xx range. The plan's blanket 5xx→unknown decision is conservative for gateway errors, but differs from the feature when the response actually supplies an explicit relayer rejection. The governing contract distinguishes rejection evidence from uncertainty, not just HTTP classes.

**Reproduced offline:** Constructed actual requests.Response / RelayerApiException objects. Empty HTTP 408 and empty HTTP 429 both produced failed; HTTP 403 containing only `<html>gateway error</html>` also produced failed. HTTP 500 with parsed `{"error":"quota exceeded"}` produced unknown. The installed exception class preserves either parsed JSON or raw response text in error_msg, so the classifier can distinguish these cases but currently does not.

**Concrete fix:** Classify by evidence: recognize an explicit parsed relayer rejection such as the quota/signature error shape, and classify unrecognized, empty or intermediary responses after POST as unknown. Keep arbitrary gateway/server failures conservative; a status class or any nonempty text alone is not a rejection proof. Apply the same explicit-error recognition when decoding the returned response body rather than assuming the status class captures every error. Add tests for empty 408/429, HTML 403, recognized quota/signature bodies, and a parsed explicit error response without a tx hash. Confirm ambiguous responses disable both normal and final merging.

## Failure-phase audit

The governing Resolved Questions classify failures by whether submission could have happened: before POST is failed; an explicit parsed relayer rejection or receipt status 0 is failed; other post-start uncertainty, missing hash, and receipt wait exceptions/timeouts are unknown.

| Phase/path | Current behavior | Assessment |
| --- | --- | --- |
| Missing builder credentials, malformed calldata, nonce GET exception, EIP-712 signing exception | failed, no POST | Correct for the covered prepare phase. |
| Local builder-header HMAC exception | unknown despite no POST | F4. Header signing is not fully in prepare. |
| RelayerClientException for missing generated headers | failed | Correct for the pinned client's pre-request exception. |
| Recognized 429 quota / 401 signature JSON error | failed | Correct for these tested explicit rejections. |
| Empty or unrecognized 4xx response | failed | F5. Rejection is not proven. |
| Recognized parsed quota/rejection body outside 4xx | unknown | F5. Explicit rejection evidence is ignored. |
| 5xx gateway error, transport timeout/error, generic post-start exception | unknown | Conservative treatment of ambiguous submission is correct. |
| Response without transactionHash or non-dict response | unknown | Correct for an ambiguous response; explicit error-body recognition needs F5. |
| Receipt timeout, RPC exception, missing receipt status | unknown, preserves tx hash | Correct in the adapter function. |
| Receipt status 0 | failed, preserves tx hash | Correct. |
| Receipt status 1 | merged, preserves tx hash | Correct at the adapter boundary; booking failure needs F3. |
| Whole-call wait_for timeout | unknown, then releases hold/lock while thread runs | Classification is conservative, but operation ownership is unsafe: F1. |
| Cancellation after the thread starts | No classified outcome or alert; hold/lock released | F1. |
| Unexpected adapter task exception | Hold released; waiter suppresses it; map stays enabled | The existing exception test endorses this behavior. The supervised completion/error boundary in F1 must handle it conservatively when submission could have begun. |
| Success already booked under the same tx | apply_merge returns False; consume/wake still happen; no duplicate success Telegram | Correct. |
| Core outbox consumption throws after store booking | Logs, leaves merge booked, wakes core | Consistent with the STEP-004 recovery handoff. It is distinct from the unhandled store-booking failure in F3. |
| Allowance refresh error / unavailable collateral read | Logs allowance error; gateway returns zero on read failure; positive-only cache update | Existing accounting stays booked; conservative cache handling is reasonable. |

## Hold/release audit

- No fresh chain balance or amount below minimum: returns before hold; correct.
- After the chain clamp, hold_merge protects both tokens before entering the adapter call.
- Normal merged/failed/unknown outcome: finally releases both tokens. On success, apply_merge and the attempted core consume occur synchronously before release, with no await between them. This prevents the proven-merge double-write-down race on the ordinary success path.
- Core consume exception: the attempted consume sees the hold, booking remains committed, and finally releases it; covered by the existing test and the recovery contract.
- Known success followed by apply_merge failure: hold is released despite missing accounting; F3.
- Logical timeout/cancellation: finally executes, but the physical adapter thread is still running; releasing the hold/lock is not an end-to-end completion guarantee; F1.
- The hold is recognized by rest_size_down_skip_reason, used by both REST reconciliation and chain divergence write-down. Its protective effect therefore depends on the lifetime corrected in F1/F3.

## Maintainability assessment

- Good decomposition: chain submission belongs in ctf_merge, scheduling/accounting in pair_merge. No feature conditionals were scattered into existing Follow300 paths in this diff.
- File sizes are healthy: ctf_merge 173 lines, pair_merge 204, new tests 250/480; merge_probe shrank from 314 to 303. No file crosses 1,000 lines.
- The structural problem is ownership, not excessive file size. `_task` describes only scheduled attempts; merge_all bypasses it; eligibility is external to execution; and finally cleans up a coroutine rather than a completed transaction. A single supervised merge operation shared by threshold and final entry points is the concrete simplification behind F1–F3. It removes split lifecycle semantics and gives state checks, protection, outcome handling and booking one owner.
- The prepare/post split is useful, but F4 shows it stops at a library method boundary rather than the actual side-effect boundary. Move the boundary instead of adding more exception-type guesses.
- The new modules and test files add no narrative comments or docstrings. Only the listed pyright directives appear. The script's existing docstring is unchanged. This follows review.instructions; no comments finding.

## Sizes, SELL paths, cancellations and service A

- Scheduled thresholds use store size × average price, held value ≥130, and minimum store pair size ≥5.
- Send amount is floor(min(store YES, store NO, fresh chain YES, fresh chain NO) ×1e6). Final minimum is 10,000 base units, i.e. 0.01 pair. These calculations match STEP-005; no price/size formula defect found in the ordinary path.
- The batch targets the unchanged collateral adapter, uses pUSD, ZERO32, partition [1,2], amount_raw and value 0. The fake venue test decodes the signed call and checks these fields.
- The diff creates no SELL order path and changes no BUY placement/cancel path. Missing quiesce/worker integration and slot sweep behavior belong to STEP-006 and were not treated as unimplemented STEP-005 findings.
- install_adapter_merge changes only the supplied engine instance. This diff does not call it in WalletHost, alter engine seams, change config/trading.toml or compose, or change Follow300 policy/worker code. Service A activation remains untouched in this scope. The implementer reports 441 neighbor tests passing; I did not rerun that entire neighbor list.
- The merge_probe send implementation is unchanged; this diff only relocates/imports constants. I did not execute any probe wallet-read or send command.

## Test transaction isolation

- ctf_merge tests provide an explicit throwaway key, throwaway wallet, builder credentials and localhost port-9 RPC/relayer URLs. get_nonce, _post_request and wait_for_transaction_receipt are replaced before the merge call. Signing/calldata creation is local.
- The engine seam test replaces ExecutionGateway with PaperGateway before engine construction, then invokes only the installed no-op merge method. Flipping engine.paper in that test does not create a live gateway.
- PairMerger tests use a fake engine whose cfg is an object, a fake gateway/chain read, and a replaced merge_pairs_via_adapter. No real relayer path is callable there. Telegram is covered by the existing autouse stub or local recorder.
- The serial verification additionally denied socket.socket.connect and connect_ex before invoking pytest. It passed with no network attempt. Review reproductions likewise guarded networking, used fake adapters/receipts or nonce/POST seams, and never used *_B keys.
- The existing test_adapter_exception_releases_the_hold checks an enabled map after an exception. That test needs its expectation changed with F1; a passing unsafe expectation is not transaction-safety evidence. Timeout tests also need to verify physical thread ownership rather than only hold release after the coroutine exits.

## Validation and limitations

**Personally executed:**

1. Serial, socket-guarded `test_trader_ctf_merge.py`, `test_trader_pair_merge.py`, `test_trader_dust_sweep.py`: **50 passed, 1 warning, 1.96 s**. PYTEST_N was removed, bytecode writing disabled, pytest cache provider disabled, and basetemp placed under `fleet/work/s5-rev/`. An earlier invocation inherited xdist and also passed 50 tests; the serial run is the relevant result.
2. `uv run --no-sync ruff check --no-cache` on all five changed files: passed.
3. `uv run --no-sync ruff format --check --no-cache` on all five changed files: all five already formatted.
4. `uv run --no-sync ruff check --no-cache --select C901` on both new production modules: passed.
5. `uv run --no-sync python -m basedpyright`: **0 errors, 0 warnings, 0 notes**.
6. Offline fault/interleaving reproductions for F1–F5, including actual installed HMAC/header generation, actual relayer exception parsing and real WalletStateStore booking failure.
7. Final E git status matches initial status: only the two pre-existing untracked ts-regress backtest directories. No code or tracked files changed by this review.

All Python executions ran through E's project `uv run` with no sync and offline mode. No full suite, live relayer/RPC verification, deployment, or real transaction was performed. The passing checks establish baseline behavior and type/lint health; the additional reproductions establish the untested failures above.

Decision without owner input: treat the Resolved Questions and transaction ownership as the approval gate, even where the plan or existing tests currently endorse cleanup after a still-running thread. Report the concrete defects and fixes; make no implementation changes in this report-only assignment.
