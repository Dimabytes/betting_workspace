# s5-rev — verification of STEP-005 review fixes
Status: FINAL

## Verdict

Request changes. **F2, F4 and F5 are resolved. F1 and F3 are partly resolved.** One additional major defect was introduced in the final-merge flow. No new nits or unrelated findings were reviewed.

All locations below are in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, at commit `2fe56a6784a506b5e77f91501a3c9c71e17e9291`.

## F1 — partly

**Locations:** `src/trader/pair_merge.py:72`, `:78`, `:134`, `:173`; `src/trader/ctf_merge.py:174`, `:194`, `:216`.

**Verified improvements:** cancel now blocks future sends without cancelling the running transaction. wait_inflight shields the merge from waiter cancellation. The wallet lock and token holds cover the actual adapter thread until it returns, and unexpected adapter exceptions disable/alert the map. The added gated-thread, competing-map, cancel and cancelled-waiter tests pass. Prepare and POST now receive a remaining-time value, and expired/late prepare does not post. This fixes the original abandoned-thread overlap on those public paths.

**Remaining issue — major:** Removing the outer wait_for lost the planned 190-second whole-call bound. deadline_s is not applied to receipt waiting: after POST, the code always starts a fresh 180-second wait. A POST taking 150 seconds can therefore be followed by another 180 seconds under the wallet lock. A response arriving after deadline still starts receipt polling rather than returning unknown with its hash. The 10-second RPC request timeout does not correct this independent 180-second phase budget.

The transport's scalar requests timeout is also not an absolute response deadline: the installed requests adapter assigns the same value separately to connect and read, and urllib3 documents that read timeout measures intervals between reads. Thus forwarding “remaining time” is an improvement over no timeout, but does not establish a bounded end-to-end operation. With the outer bound removed, a slow response can keep the wallet and quiesce waiter occupied beyond the stated budget. This is a major regression introduced while repairing F1, not a reason to restore thread abandonment.

**Offline reproduction:** With a 100 ms operation budget and immediate fake GET/POST, receipt polling received `timeout=180.0` with approximately 94 ms left. A second fake POST returned its hash after the operation deadline; polling still received `timeout=180.0` with approximately −35 ms left and returned merged. The first case does not depend on the fake HTTP call overrunning its own timeout. All receipt/HTTP operations were fake and socket connections were forbidden.

**Concrete remaining fix:** Carry the absolute deadline through receipt polling. When a post-start operation has exhausted its budget, return unknown and retain any obtained tx hash. Limit receipt waiting to the remaining budget and each RPC to the smaller of its request cap and remaining time. Enforce the deadline through the actual HTTP response/read lifecycle rather than treating a scalar connect/read timeout as total elapsed time. Keep transaction ownership until the physical operation is finished; do not reintroduce wait_for cancellation of a running thread. Add late-POST/receipt-budget tests and a test of the bounded transport path, not only a patched _relay_http helper.

## F2 — resolved

**Locations:** `src/trader/pair_merge.py:84`, `:89`, `:120`, `:137`, `:140`.

Both entry points use the tracked task. merge_all first joins the existing operation. Eligibility is checked under the wallet lock and again after the balance await, immediately before hold/send. Final eligibility preserves disabled, cancelled, live mode, recovery and unacked-MATCHED checks while overriding the intended threshold/quiesce/pause checks.

The new queued recovery/unacked, recovery-during-balance-read, queued-final-after-unknown and final-settled-position tests pass. The original forbidden queued submission is no longer present.

**Concrete remaining fix:** None for F2. The distinct pending-booking/final-flow regression is recorded as N1 below.

## F3 — partly

**Locations:** `src/trader/pair_merge.py:84`, `:92`, `:164`, `:183`, `:195`.

**Verified improvements:** A known mined outcome becomes _pending before booking. apply_merge exceptions retain the tx/quantity, retain both token holds, and produce an accounting alert with the tx. A subsequent schedule performs local booking rather than another adapter call. The added retry test passes: one adapter call, one 120 cash credit, two merged outbox rows, and the accounting alert followed by success. The original silent loss on an ordinary transient booking error is fixed.

**Remaining issue — major:** Final completion still reports normal return while a known mined merge is unbooked. _book_pending returns False, but _finish_pending and merge_all do not expose unresolved accounting to their caller or ensure another retry. If booking fails in the final merge, no later feed tick is promised: the required next operations are the final PnL snapshot, zero_token_sizes and worker removal. The pending receipt exists only on this merger object; letting that owner be discarded loses the known-success booking obligation and leaves its store holds behind. The alert and in-memory retention do not complete the end-of-map handoff.

**Offline reproduction:** Called merge_all directly with a fake mined success for 120 pairs and apply_merge consistently raising OperationalError. merge_all returned normally. Afterwards, `_pending=_PendingMerge(tx_hash='0xmerge', qty=120.0)`, both holds remained, `_task.done()=True`, one accounting alert existed, and zero merged rows existed. There was no active booking retry. This verifies the final-return contract itself; it does not assume STEP-006 has already been implemented.

**Concrete remaining fix:** Make final accounting completion explicit. While a mined receipt is pending, finalization must retain/retry its operation owner and must not advance to final snapshot, zeroing or detach. Provide a typed unresolved-accounting result or exception plus a defined retained retry/handoff path; if the worker may exit, the known tx/quantity must survive that handoff. Retry only apply_merge, never the on-chain transaction. Add a final-booking-failure test that proves cleanup cannot proceed until the known merge is booked, followed by a successful local retry with exactly one adapter call and one credit.

## F4 — resolved

**Locations:** `src/trader/ctf_merge.py:63`, `:78`, `:158`, `:173`, `:178`.

Builder authentication headers are now generated inside prepare. The POST exception boundary contains the actual HTTP operation, and the deadline is checked before starting it. The malformed `abcde` secret and header-generation exception tests return failed without POST; post-start transport failure still returns unknown. The malformed-secret case exercises the actual installed header generation; the header-exception case injects a local preparation error at that boundary.

**Concrete remaining fix:** None for F4.

## F5 — resolved

**Locations:** `src/trader/ctf_merge.py:233`, `:245`, `:252`.

Explicit failure now requires a parsed object with a nonempty string error. Empty 408/429 and HTML 403 become unknown. Recognized quota/signature bodies remain failed, including quota on 500 and an error object on 200 without a hash. An HTTP-200 response with a hash continues through receipt classification. The amended response matrix and unknown-disables-normal-and-final behavior pass.

**Concrete remaining fix:** None for F5 within the original finding and specified response contract.

## New major introduced by the fix

### N1 — major — A final call can repair pending booking and skip the actual final merge

**Locations:** `src/trader/pair_merge.py:84`, `:92`, `:164`.

**Problem:** When _pending exists, _start(final=True) starts only _finish_pending and returns. merge_all waits for that task and then returns; it never evaluates/sends the remaining final pairs. Extra BUY fills can arrive during a threshold merge or before quiesce cancels the bids. After the old mined merge is finally booked, those new shares can form another pair tail. A single final call now omits that tail even when all eligibility checks pass. This path was introduced by the pending-booking fix; the original merge_all went directly to a fresh final balance clamp/send.

**Offline reproduction:** Started with 150 YES / 120 NO. The threshold adapter returned mined for 120 pairs, and the first booking failed. Added and acknowledged a further 20 NO shares in both the store and core, with recovery cleared and no unacked MATCHED, then marked quiesced and called merge_all once. It repaired the old booking and returned with:

```text
adapter amounts: [120000000]
remaining pairs: 20.0
recovery_pending: False
unacked MATCHED: False
pending: None
holds released: True
```

A second merge_all call then submitted the omitted `20000000` units and removed that pair tail. The second call confirms the first call's omission was not caused by an eligibility or minimum-amount block.

**Concrete fix:** Define the final operation as “settle any known pending booking, then read/clamp/send the remaining eligible pairs.” After successful pending booking, continue the same public merge_all invocation through the final send decision. If accounting remains unresolved, take the explicit retained failure path required by F3 instead. Preserve the disabled/unknown guards from F2. Add the acknowledged-fill/pending-booking interleaving above, asserting one merge_all invocation handles the final tail and never repeats the earlier transaction.

## Verification and scope limits

- Inspected the implementer's Review fixes section and both `git diff da5ce546 2fe56a67` and full step scope `7b1b6990..2fe56a67`. The amend changes only the two merge modules and their two tests; the constant-only probe change is unchanged from the first review.
- Personally ran the step/dust tests serially with PYTEST_N removed, socket connect/connect_ex denied, bytecode/cache output disabled in E, offline uv, and scratch under fleet/work/s5-rev: **65 passed, 1 warning, 2.15 s**.
- Personally ran offline reproductions for the remaining F1 deadline behavior, F3 final unresolved booking, and N1 final pair-tail omission, using fake adapter/HTTP/receipt seams and real test WalletStateStore/core objects. Test-owned scratch was created and cleaned only by this run.
- Inspected the installed requests/urllib3 timeout implementation and web3 receipt-wait implementation to verify the elapsed-time limitation. No external API behavior was guessed or tested live.
- No code edits, staging, commits, pushes, SSH, wallet reads, *_B keys, real orders or real transactions. E git status remains unchanged: the two pre-existing untracked ts-regress backtest directories only.
- No full-suite or unrelated maintainability review was performed. The earlier review remains the source for findings outside this narrowly requested verification.

Decision without owner input: accept the demonstrated F2/F4/F5 fixes; keep F1/F3 open for the specific remaining paths above, and require the new N1 final-flow defect to be fixed before STEP-005 passes.
