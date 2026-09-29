# stepreview STEP-002 — commit 9b2742b7
Status: FINAL

Verdict: approve. No structural change requested.

Scope: `git show 9b2742b7` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (5 files, +951/−10). Review only; no code edited. Skill: `/Users/dimabytes/.claude/skills/feature-json-step-review/SKILL.md`. Workspace `review.instructions` is only the comments-review assignment (`.feature-json.config.json:16-20`); this pass is the maintainability review.

## Findings

None.

## Why this shape holds

The durable row is a wallet fact, so it lives on the wallet connection next to `core_commands`, not in a second store and not in the kernel. `_CORE_SCHEMA` creates `unsettled_buys` and `(resolved, session_id)` in the same script `migrate_core_schema` already runs (`src/trader/core_persistence.py:97-109`, `src/trader/wallet_store.py:146-149`). `UnsettledBuy` is the same frozen row type as `CoreCommand` (`src/trader/core_persistence.py:208-237`). The six helpers execute and return; none of them commit (`src/trader/core_persistence.py:807-871`). `insert_unsettled_buy`, `prove_unsettled_buy`, and `resolve_settled_buys` have no production caller. `unsettled_buy_notional` is called only from the two reserve totals (`src/trader/session_budget.py:48`, `src/trader/session_budget.py:63`).

Reserve is one aggregate, not a per-row probe and not a write into `fill_ledger`. Booked size is `SUM(size)` for `side='BUY'` and `MATCHED`/`CONFIRMED`, grouped by `maker_order_id`, left-joined to unresolved rows. The floor is inside the `SUM`, so one overfill cannot pay for another row (`src/trader/core_persistence.py:792-804`). Resolution is the same shape with `CONFIRMED` only and `final >= qty - HALF_SHARE_TICK`, and `RETURNING` plus a sort is what makes a repeat call return `()` (`src/trader/core_persistence.py:832-845`). `MATCHED` becomes `CONFIRMED` or `FAILED` by updating the existing ledger row (`src/trader/wallet_store.py:311-317`, `src/trader/wallet_store.py:375`), so the join does not need a second status table. `HALF_SHARE_TICK` is imported from `shared.utils.trading` (`src/trader/core_persistence.py:13`, `src/shared/utils/trading.py:16`); `wallet_store` already imports `core_persistence` (`src/trader/wallet_store.py:148`), so the constant cannot come back through that module.

Budget stays a sum of the sources it already had. `_account_reserved` and `_map_reserved` each add one call (`src/trader/session_budget.py:43-68`). `None` is the all-sessions total, including a row whose core is gone; the session argument is this map only. Gone orders are already outside `reserved_buy_notional` on the core, and a venue the core still owns is already excluded from the store-order term (`src/trader/session_budget.py:49-52`, `src/trader/session_budget.py:64-67`). The new term does not grow a conditional in quoting, caps, or the ledger writers.

The checkpoint marker is a boundary codec, not a fifth status in the old validator. Snapshot writes `unknown` and `UNSETTLED_CANCEL_REASON` only when the in-memory status is `gone`, and it builds a new `OrderCheckpoint` (`src/trader/core_persistence.py:413-418`). Restore maps that pair back through `_decode_checkpoint_status` (`src/trader/core_persistence.py:348-351`, `src/trader/core_persistence.py:479`). `_require_status` still accepts only pending, live, canceling, and unknown (`src/trader/core_persistence.py:342-345`). `CORE_SCHEMA_VERSION` stays 3 (`src/trader/core_persistence.py:27`). One production constant, `UNSETTLED_CANCEL_REASON = "unsettled"` (`src/trader/core_persistence.py:30`). It is not a `BlockReason` (`src/strategy/types.py:10-38`).

## File size

No file crosses 1000 lines. After the commit: `core_persistence.py` 935 (was 803), `session_budget.py` 82, `test_trader_core_persistence.py` 919 (was 458), `test_trader_shared_budget.py` 569 (was 325), `test_trader_core_recovery.py` 518 (was 420).

## Considered and not requested

- A new module for the six helpers. The table has to be created inside `_CORE_SCHEMA` (`src/trader/core_persistence.py:44-110`), and the helpers take the same connection the migration just prepared. A second module would split one schema string from the only code that writes it. 935 lines is still under the split line.
- Replacing `session_id: str | None` with a pair of functions. The contract is one function, and `None` means every session (`src/trader/core_persistence.py:848-855`). The `AND session_id=?` append is the same shape as `reserved_buy_notional_for_session` (`src/trader/core_persistence.py:787-788`). Two wrappers would not delete the filter.
- A SQL builder shared by notional and resolution. Booked is `MATCHED` plus `CONFIRMED` (`src/trader/core_persistence.py:800`). Final is `CONFIRMED` only (`src/trader/core_persistence.py:839`). Each predicate is used once. A builder would add a mechanism for two different sentences.
- An index on `fill_ledger.maker_order_id`. The join aggregates the ledger once per call (`src/trader/core_persistence.py:798-802`, `src/trader/core_persistence.py:837-840`). That is not a correlated scan per unsettled row. The plan allows an index only when that repeated scan shows up. `fill_ledger` has no other index today (`src/trader/wallet_store.py:60-74`). Adding one here is a second migration this step does not need.
- Caching residual notional on the row so budget becomes a plain `SUM`. Every ledger status change would have to update `unsettled_buys`. Those writers stay untouched (`src/trader/wallet_store.py:266-375`). Read-time `max(0, qty - booked) * price` is the smaller model.
- A helper that returns the stored status and reason together. Both ternaries use `order.status == "gone"` (`src/trader/core_persistence.py:413-418`). A helper would not remove a branch.
- A copied pre-step `restore_orders` for the old-code test. The plan allows patching the new decoder seam back to `_require_status`. `_decode_checkpoint_status` is that seam, and `apply_checkpoint` calls `restore_orders` in the same module (`src/trader/core_persistence.py:527-554`). The test patches that function and still restores inventory, the episode, and `sell_only` (`tests/test_trader_core_persistence.py:888-919`). Copying the rest of `restore_orders` would duplicate the body that did not change.

## Approval bar

No structural regression, no second store, no file pushed over 1000 lines, no new branch in the ledger writers or in `_require_status`, no wrapper around the budget sum. Insert, proof, and resolution stay uncalled until a later step. Tests cover the plan's ledger, budget, checkpoint, and recovery cases in the existing modules; this pass did not re-run them.
