# stepreview STEP-003 — commit c72792c9
Status: FINAL

Verdict: approve. No structural change requested.

Scope: `git show c72792c9` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (7 files, +1477/−7). Review only; no code edited. Skill: `/Users/dimabytes/.claude/skills/feature-json-step-review/SKILL.md`. Workspace `review.instructions` is only the comments-review assignment (`.feature-json.config.json:16-20`); this pass is the maintainability review.

## Findings

None.

## Why this shape holds

Proof parsing and the trades read are a module that returns values. `WalletHost` is the only writer, and it commits before it wakes. `LiveCore` only repeats `BuySettled` and records when a cancel started. Nothing in this commit inserts a row, emits `CancelUnsettled`, or filters `gone` out of the book.

`unsettled_buy_recovery.py` is 156 lines. `parse_terminal_buy_proof` returns `BuyExecutionProof | None` (`src/trader/unsettled_buy_recovery.py:28-44`). Empty, blank, boolean, negative, and non-finite `size_matched` are `None` because `_nonnegative_qty` rejects them before `clob_number` accepts a zero (`src/trader/unsettled_buy_recovery.py:113-118`, `src/trader/trade_backfill.py:45-56`). A cancellation, a `MATCHED` status, or `size_matched` covering a positive `original_size` is the terminal check (`src/trader/unsettled_buy_recovery.py:100-110`). `collect_rest_buy_proofs` groups unproven rows at least 60 seconds old, one `get_trades` per token, `after = int(oldest) - 7200` (`src/trader/unsettled_buy_recovery.py:47-80`). A missing client, a thrown read, a non-list, or a trade that is not a dict of maker dicts proves nothing for that token (`src/trader/unsettled_buy_recovery.py:65-94`, `src/trader/unsettled_buy_recovery.py:129-146`). A well-formed empty list sums to zero. A bad `matched_amount` taints that order id only (`src/trader/unsettled_buy_recovery.py:147-155`). The summed field is `maker_orders[].matched_amount` via `maker_order_id_of`, not trade size (`src/trader/fill_parsing.py:46-55`). The lagging-zero ceiling is the `ponytail` comment on the constants (`src/trader/unsettled_buy_recovery.py:21-25`).

The user-stream hook is the existing `_on_order`. It calls `super()` and then passes the same object (`src/trader/engine_seams.py:150-153`). The default is a no-op (`src/trader/engine_seams.py:63-66`, `src/trader/engine_seams.py:148`). The host binds after `Engine.start`, next to the other post-start binds (`src/trader/wallet_host.py:1068-1071`, `src/trader/wallet_host.py:796-800`). The handler proves an open row or returns. It does not insert (`src/trader/wallet_host.py:802-811`).

One helper proves and resolves inside `with conn`, which commits before `_wake_unsettled_sessions` (`src/trader/wallet_host.py:824-852`). `prove_unsettled_buy` still refuses resolved rows (`src/trader/core_persistence.py:825-828`). A second proof in the same batch is skipped by `applied`. `_dispatch_fill` calls `_resolve_confirmed_buy` before the `seq is None` return, so an already-acked replay still resolves, and a `MATCHED` ledger row does not (`src/trader/wallet_host.py:813-822`, `src/trader/wallet_host.py:878-885`). The pre-check is the gate: without it, every terminal message and every confirmed fill would run the global resolver. Confirmed resolution passes an empty proof tuple through that same helper, so there is not a second close path.

The positions wrapper gained one `try` after missed-fill backfill and before `original_positions` and `drain_outbox` (`src/trader/engine_seams.py:489-501`). Paper still skips both recovery calls (`src/trader/engine_seams.py:490`). Either exception is logged and does not skip the snapshot. The sweep reads open rows, awaits trades, then applies. The apply rereads and skips a row that became proven while the await was in flight (`src/trader/wallet_host.py:854-865`, `src/trader/wallet_host.py:832-835`). No write transaction is held across `gateway._io`. Backfill of that reply runs on the event-loop side of the await, per token, before proofs return (`src/trader/unsettled_buy_recovery.py:85-96`, `src/trader/trade_backfill.py:117-123`).

The alert is a stable `(key, message)` on rows still open after that commit, age strictly greater than 600 seconds (`src/trader/wallet_host.py:868-876`). `wrap_alert_transitions` already drops a repeat of the same pair (`src/trader/engine_seams.py:707-718`). Alerting does not write the row.

`LiveSources.settled_buys` is required (`src/trader/session_core.py:122`). `live_sources` calls `resolved_unsettled_buys` only when some active order is a BUY with a venue binding; otherwise the map is `{}` (`src/trader/session_core.py:923-929`, `src/trader/session_core.py:978`). `sync_inputs` appends `BuySettled` after budget and permissions, including `0.0`, and the next cycle builds the list again from whatever BUY is still active (`src/trader/session_core.py:616-647`). No delivered-id set.

The cancel clock is not `_canceling_since`. That map is the watchdog, and `already_canceling` is false for `gone` (`src/strategy/lifecycle.py:292-299`). `BuyCancelClock` is the named pair the project rule wants instead of a bare tuple (`src/trader/session_core.py:91-96`). `capture` stores the values and `revert` puts them back (`src/trader/session_core.py:473-495`). `apply` snapshots orders, steps, then diffs (`src/trader/session_core.py:666-670`). The diff arms once on entering `already_canceling` or on `CancelUnsettled` into `gone`, logs `gone` once, and logs `wait_ms` only when `BuySettled` removes the order or a `Fill` removes a `gone` BUY (`src/trader/session_core.py:682-734`). A removal with no clock logs `wait_ms=unavailable origin=restored`. Clocks for orders no longer active are dropped in the same method.

## File size

`session_core.py` moves from 983 lines to 1084. `tests/test_trader_session_core.py` moves from 996 to 1380. Both cross 1000. Waived.

The clock has to sit on `LiveCore` because `CoreMemory` is the revert set for `apply_and_persist` (`src/trader/session_core.py:100-109`, `src/trader/session_core.py:483-494`). `BuySettled` has to be built in `sync_inputs`, which is the only live input list (`src/trader/session_core.py:616-629`). Moving those methods to a new module would take the dict and the input list away from the object that applies them. The new methods are contiguous under `apply` (`src/trader/session_core.py:631-734`).

The new session tests use `_core`, `_prime`, `_sources`, and `_accept_first_buy` in that file (`tests/test_trader_session_core.py:91`, `tests/test_trader_session_core.py:133`, `tests/test_trader_session_core.py:182`, `tests/test_trader_session_core.py:1063`). A second test module would import a test module or copy the adapter harness. The new cases are the tail of the same `LiveCore` suite (`tests/test_trader_session_core.py:1094-1379`).

Already over 1000, and this commit does not make them the home of the parser: `wallet_host.py` 1402 → 1503, `engine_seams.py` 1321 → 1341. The host addition is the transaction, the wake, and the alert (`src/trader/wallet_host.py:796-876`). The seam addition is the callback field and one `try` (`src/trader/engine_seams.py:148-153`, `src/trader/engine_seams.py:495-498`). New files: `unsettled_buy_recovery.py` 156, `tests/test_trader_unsettled_buy_recovery.py` 707. `tests/test_trader_live_execution.py` gains the required `settled_buys={}` argument only.

## Considered and not requested

- A host type to hold the six new methods. They coordinate `self.store`, `_worker_by_cid`, and `engine._wake_cid`. The parser and the trades read already left `wallet_host.py`. A second object would wrap that coordination.
- Reusing `_canceling_since` as the wait clock. `finish_cycle` drops an id once `already_canceling` is false, and `gone` is not that status (`src/trader/session_core.py:805-838`, `src/strategy/lifecycle.py:292-299`). The measured interval would end when the order becomes `gone`, which is the start of the wait, not the removal.
- Having `collect_rest_buy_proofs` return trades and letting the host call `backfill_trades`. There is one caller (`src/trader/wallet_host.py:858-864`). A second return value would not remove the backfill.
- A narrower pyright header on the new module. The file copies the `engine_seams` unknown-member suppression because it calls `gateway._client` and `get_trades` (`src/trader/unsettled_buy_recovery.py:3-5`, `src/trader/engine_seams.py:4-5`). The proof values themselves are a frozen dataclass, not `dict[str, Any]`.
- Deleting the `CANCELLED` spelling. Order status in this path is `CANCELED`. `CANCELLED` is the databet match set (`src/trader/databet_types.py:7`). It is an extra member of one condition (`src/trader/unsettled_buy_recovery.py:103`), not a second control-flow path.
- A distinct log origin for a missing clock that is not a checkpoint restore. `apply_buy_settled` can also drop a live BUY that never entered `already_canceling` (`src/strategy/lifecycle.py:356-365`). That order has no cancel start either, so `wait_ms` stays unavailable (`src/trader/session_core.py:720-727`). A second origin string would not add a clock.

## Approval bar

No second store, no second close path, no insert, and no new branch in `note_cancel` or `_own_remaining`. The file-size crossings stay in the modules that already own the revert set and the `LiveCore` harness. The parser is not inlined into `wallet_host.py` or `engine_seams.py`. This pass did not re-run the suite.
