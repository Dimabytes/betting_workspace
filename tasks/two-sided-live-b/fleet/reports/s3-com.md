# s3-com — comment review of STEP-003 (b86cdcdc..4a4018fb)
Status: FINAL

## Scope

- Diff reviewed: `git -C esports-trader diff b86cdcdc..4a4018fb` (one commit, `4a4018fb Record pair merges in the wallet ledger as MERGED rows.`).
- Files: `src/dashboard/summarize.py`, `src/trader/wallet_store.py`, `tests/test_trader_wallet_store.py`.
- Per brief: comments/suppressions the diff adds or touches, plus comments inside functions the diff refactors (`ledger_position`, `apply_merge`, `ledger_net_cash`, `ledger_net_cash_for_tokens`, `fill_for_key`, `_insert_merge_leg`, `_subtract_merged_shares`, `_format_merge_key`, `cmd_wallet`).

## Findings

1. `src/trader/wallet_store.py:54-56` — pre-existing comment inside the touched hunk (lines 55-56 are literal context lines of `@@ -55,6 +55,9 @@`; the diff splices `_LEDGER_MERGED`, `_MERGE_SIDE`, `_MERGE_LEG_PRICE` directly beneath it):

   ```python
   # A fill the exchange snapshot already contained when we wrote the position
   # down. Applying it would subtract the same shares twice, so the row is kept
   # for the audit trail with no position, cash, or outbox effect.
   _LEDGER_SUPERSEDED = "SUPERSEDED"
   ```

   Non-obvious behavior of *our own* ledger design, narrated in prose. No keep clause applies: not a license header, not a foreign dependency/platform/protocol constraint, not a public API doc contract, no issue/RFC link. The "kept with no effect" semantics are already expressed in code by every reader filtering `status IN (MATCHED, CONFIRMED, MERGED)` — the comment restates it.

   **DELETE** the comment. **MUST KILL** `_LEDGER_SUPERSEDED` — reshape so membership says it: centralize the status semantics in named sets (e.g. `_POSITION_REPLAY_STATUSES` / `_CASH_STATUSES`) so "SUPERSEDED has no effect" reads from which set it belongs to, rather than from a sermon above one constant. Side note: the diff makes the smell worse — the sibling `MERGED` status lands in the same block with zero prose (correctly), leaving SUPERSEDED as the only status that needs an essay.

## Compliant removals (not findings)

The diff deletes five narration docstrings while refactoring — exactly what `review.instructions` demands:

- `ledger_position` — `"""Replay MATCHED and CONFIRMED fills in outbox order. sqlite is not an input."""` (removed; refactored for the MERGED branch)
- `running_net_cash` — `"""In-memory MATCHED+CONFIRMED cash, restored once from the ledger at open."""`
- `ledger_net_cash` — `"""Signed cash of MATCHED and CONFIRMED fills. FAILED rows contribute 0."""`
- `ledger_net_cash_for_tokens` — `"""Signed cash of MATCHED and CONFIRMED fills on this market's tokens."""`
- `fill_for_key` — `"""Rebuild the Fill recorded under this ledger key, if any."""`

## Skips

- All new code (`apply_merge`, `_insert_merge_leg`, `_subtract_merged_shares`, `_format_merge_key`, `cmd_wallet` SQL changes, all new tests) contains **zero added comments, docstrings, or suppressions**. Verified by grepping added lines for `#`, `"""`, `noqa`, `type: ignore`, `pragma`, `pyright`, `pylint`, `fmt:`, `prettier-ignore` — no hits.
- Other docstrings in `wallet_store.py` (e.g. `restore_ledger_position`, `_stamp_settle`, `pending_outbox`, `matched_keys_for_tokens`) are unchanged context outside the refactored functions — out of scope.
