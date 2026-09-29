# stepreview STEP-004 — commit f6bcd785
Status: FINAL

Verdict: approve. No structural change requested.

Scope: `git show f6bcd785` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (10 files, +1131/−20). Review only; no code edited. Skill: `/Users/dimabytes/.claude/skills/feature-json-step-review/SKILL.md`. Workspace `review.instructions` is only the comments-review assignment (`.feature-json.config.json:16-20`); this pass is the maintainability review. The workspace notes commit is not in this diff.

## Findings

None.

## Why this shape holds

One lookup answers ownership. `find_owned_order` walks `_venue_to_core`, then the active order, then the retained record, and returns `RestingOrder | OrderRecord | None` (`src/trader/session_core.py:476-487`). Both types already carry `side`, `token_index`, `price`, and `submitted_qty` (`src/strategy/types.py:128-160`). Callers do not branch on the type and do not cast. Dropping an order keeps that record (`src/strategy/lifecycle.py:173-177`), so a fill that retires the BUY during the HTTP await is still a BUY.

`note_cancel` warns only when the venue id was never bound, skips a bound id with no order and no record, and then emits `CancelTimeout`, `CancelUnsettled`, or `CancelAck` (`src/trader/session_core.py:621-636`). A retired BUY cannot fall through to `CancelAck`. The kernel events are unchanged: `CancelAck` still frees the rung (`src/strategy/lifecycle.py:341-346`); `CancelUnsettled` only sets `gone` on a live BUY (`src/strategy/lifecycle.py:349-353`).

`_own_remaining` skips `gone` before the acceptance, token, side, and leftover checks (`src/trader/session_core.py:253-256`). There is no BUY-only flag in that loop. `gone` is the BUY terminal status; a SELL cancel still removes the order.

HTTP metadata is a frozen `_OwnedBuyCancel` taken before the await (`src/trader/wallet_host.py:464-492`). `_dispatch_cancels` still commits the prepared commands and returns before the network call (`src/trader/wallet_host.py:404-425`). After the await, `_commit_cancel_reserves` writes outcomes and, only when the cancel succeeded, the BUY rows inside one `with conn` (`src/trader/wallet_host.py:495-519`). `persist_cancel_outcome` and `insert_unsettled_buy` only execute (`src/trader/core_execution.py:214-223`, `src/trader/core_persistence.py:807-822`). A raised insert rolls the outcome back and never reaches `note_cancel_result` (`src/trader/wallet_host.py:538-540`). `INSERT OR IGNORE` leaves an existing proof, qty, and timestamps alone. The row uses `submitted_qty`, not the residual. False cancels persist `timeout` and insert nothing. The worker reference captured before the await is the one notified, including when the command tuple is empty.

The resolver runs after that commit, and only when a BUY insert was requested (`src/trader/wallet_host.py:543-544`, `src/trader/wallet_host.py:945-964`). That is the same close path as a confirmed fill. It is not a second writer.

The user-stream callback stays one read of the row. `parse_buy_cancellation` is the create predicate: BUY, a nonempty string id, `type=CANCELLATION`, `status=CANCELED`, and it does not look at `size_matched` (`src/trader/unsettled_buy_recovery.py:39-50`). `parse_terminal_buy_proof` is still the only quantity parser (`src/trader/unsettled_buy_recovery.py:53-63`). `_terminal_buy` is not the create check, because it also accepts `MATCHED` and a full `UPDATE` (`src/trader/unsettled_buy_recovery.py:119-129`). `CANCELLED` stays on the proof side only.

`get_unsettled_buy` is a primary-key read, so a resolved row is visible (`src/trader/core_persistence.py:858-865`). `_on_order_terminal` returns on a non-dict, on a message that is neither proof nor a cancellation, and on a resolved row (`src/trader/wallet_host.py:865-879`). An absent row is inserted only for a cancellation whose core order is a BUY (`src/trader/wallet_host.py:880-919`). Proof is applied in that same transaction only while the stored row is still open and unproven. `note_cancel_result` runs after the commit, then `_apply_unsettled_proofs(())` so a zero qty resolves without another fill. An existing open row keeps the STEP-003 proof helper, then `_deliver_cancelled_buy` re-sends the terminal event when the BUY is still owned (`src/trader/wallet_host.py:885-932`). Neither path calls the proof helper inside an open insert, and neither synthesizes `BuySettled`.

Worker lookup still falls through `engine.state.orders` to the core binding (`src/trader/wallet_host.py:345-371`). `WalletUserStream._on_order` can drop the fork order before the host runs; the insert does not need it to still be there. Checkpoint schema stays 3 (`src/trader/core_persistence.py:27`). This commit does not touch the codec, the kernel, or the ledger.

The adapter tapes stop sharing a diff at the first successful cancel (`tests/test_adapter_contract.py:47-67`). After that row the live order is `gone` and the backtest order is gone from the book, and the rung may be quoted again only on the backtest side. Continuing `assert_drivers_match` would require teaching the contract to ignore that divergence for the rest of the tape. The helper feeds the tail to each driver and the tests assert the live BUY stays `gone`. SELL success and `ok=False` still go through `play_tape`.

## File size

No file crosses 1000 lines in this commit. Already over 1000, and the additions stay in the modules that own the transaction and the harness:

- `wallet_host.py` 1503 → 1624. The new functions sit on `_durable_cancel` and `_on_order_terminal` (`src/trader/wallet_host.py:464-545`, `src/trader/wallet_host.py:865-932`).
- `session_core.py` 1084 → 1106. The lookup and the two branches are the methods that already route cancels and strip the book.
- `tests/test_trader_wallet_host.py` 2998 → 3157. The atomicity regression stays here (`tests/test_trader_wallet_host.py:3029-3090`) and reuses the activation rig.
- `tests/test_trader_session_core.py` 1379 → 1430.

`unsettled_buy_recovery.py` 156 → 175. `core_persistence.py` 935 → 945. New file `tests/test_trader_unsettled_buy_activation.py` is 667 lines. `tests/test_adapter_contract.py` 318 → 353.

## Considered and not requested

- Folding the WS insert into `_commit_cancel_reserves`. HTTP must commit command outcomes with the rows and must not prove. WS must prove inside the insert and has no command row. One helper would hide that split.
- A shared "owned BUY metadata" helper for the host and the callback. The two sites copy four fields (`src/trader/wallet_host.py:483-490`, `src/trader/wallet_host.py:901-909`). The transactions above them are the structure; another dataclass would not delete either path.
- Returning `str | None` from `parse_buy_cancellation` instead of `BuyCancellation`. The proof parser already returns a frozen value (`src/trader/unsettled_buy_recovery.py:28-31`). The one field is the venue id the create path is allowed to use. A bare string would not show that the other message fields were rejected.
- Extending `assert_drivers_match` so a `gone` BUY equals a freed backtest order. The following quotes differ because the rung stays occupied on the live side. The cut at the cancel row is the smaller contract (`tests/test_adapter_contract.py:55-61`).
- Moving `open_buy_rig` into a non-test module. `test_trader_wallet_host.py` imports it from the activation module the same way session tests already import `_md_book` from `test_trader_session_core`. The rig is the shared fixture, not a second harness.
- Renaming `test_durable_cancel_keeps_ws_proof_and_survives_a_detached_core`. The body credits a ledger fill and asserts `proven is False`. The name is the only mismatch.

## Approval bar

No new store, no second close path, and no kernel change. The book filter is one status check. The cancel router is one lookup and three events. HTTP outcomes and rows commit together before notify; WS proof commits before `CancelUnsettled`. File growth stays inside modules that were already past 1000 lines. This pass did not re-run the suite.
