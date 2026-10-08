# s4-rev — STEP-004 strict maintainability review
Status: FINAL

Scope: `esports-trader` diff `c779fa21..624fb55f` (11 files; 186 insertions, 26 deletions). Report only; no product code, staging, or commits.

Read: shared context, review brief, workspace and project `AGENTS.md`, `feature-json-step-review/SKILL.md`, review configuration, implementation plan, and implementer report. The review configuration also prohibits new comments and asks that refactored functions shed existing comments where applicable.

## Progress

- Read the complete commit diff. No new comments or docstrings appear in the additions.
- Reviewed the inventory reducer, scheduling, checkpoint transaction, outbox callers, and absolute recovery baseline.
- Independently reproduced a merge double-debit after an outbox consume failure followed by `RecoveryVerified`, using SQLite `:memory:` and the real `LiveCore`/`RecoveryCoordinator`. No repository files were written by the probe.
- The implementer reports one regression failure in unchanged `wallet_host.py`; it was not independently reproduced by this review.

## Verdict

Request changes: one **major correctness finding**, below. No additional structural maintainability findings, blockers, minors, or nits. The happy path is small and follows the plan, but the documented recovery fallback can permanently corrupt the new merge accounting.

## Findings

### 1. major — `src/trader/core_session_io.py:119` (checkpoint boundary at `:108`) and `src/strategy/lifecycle.py:704`: recovery can absorb an undelivered merge, which the cursor later subtracts again

Problem: each merge leg is applied and checkpointed separately. If persistence fails on the second leg, `core.revert(memory)` correctly leaves the cursor before that leg. However, the supported fallback to absolute ledger recovery includes **both** legs already committed by `store.apply_merge`. `RecoveryCoordinator.accept_if_proven` (`src/trader/core_recovery.py:201`) overwrites inventory without advancing the outbox cursor. The next successful consume therefore applies the remaining `Merged` delta a second time. The incorrect inventory is then persisted with the cursor at the head, so another consume cannot repair it. `_position_mismatch` (`src/trader/session_core.py:678`) also returns immediately in the resulting `sell_only` state. This is a defect in the new merge path's integration with the existing recovery boundary, rather than a claim that the old per-row loop first appeared in this commit.

Reproduction on `624fb55f`, entirely in memory:

1. Start core and ledger at `(50, 40)` with the confirmed-fill outbox cursor at 2.
2. Merge 20 pairs: ledger becomes `(30, 20)` and adds merge rows 3 and 4.
3. Inject an exception before the second call to `persist_core_snapshot`. Core becomes `(30, 40)`, cursor 3.
4. Apply `Recovery`, then the real `RecoveryCoordinator.accept_if_proven(rest_yes=30, rest_no=20)`. Core becomes `(30, 20)` with cursor still 3.
5. Retry `consume_core_outbox`. Core and saved checkpoint become `(30, 0)`, cursor 4; ledger remains `(30, 20)`. Flags are `sell_only=True`, `recovery_pending=False`.

Independently checked the quote impact through `desired_bids` with the existing two-sided test state and fair 0.50:

- Correct `(30, 20)`: token 0 bids **16 shares at 0.47**; token 1 bids **20 at 0.47**.
- Incorrect `(30, 0)`: token 0 bids **8 shares at 0.46**; token 1 bids **20 at 0.48**.

Concrete fix: reconcile the outbox before adopting an absolute ledger baseline. In the recovery acceptance path, drain all pending rows through the observed outbox head **before** reading/applying ledger inventory; require that the cursor actually reaches that head, abort verification if consumption fails or stops on an unreadable row, recheck ownership/unsettled proof after the drain, and persist the recovered inventory with that cursor. Add a regression that injects a failure after the first leg, runs ledger recovery, retries consume, and asserts both core quantities and the saved checkpoint remain `(30, 20)`. Applying both merge legs in one batch improves atomicity but alone does not fix a failure before that batch's checkpoint.

The plan's claim that a later `RecoveryVerified` simply overwrites the merged inventory is valid only when all merge rows have already been consumed. Its documented consume-error fallback needs this reconciliation boundary.

## Maintainability assessment

- `Merged` is a frozen, explicit event with named fields. No loose dictionaries, casts, new optional arguments, or feature switches were introduced.
- The existing dispatcher handles the event in the canonical lifecycle layer. Extending the session-event pattern keeps the established structure; the change does not add scattered policy checks.
- Reusing `_credit_sell_inventory` preserves proportional cost removal, clamps excessive quantity to zero, and retains `last_buy_ns` while shares remain. It also debits Follow300 rung lots and can set `winding_down`; these extra effects are inactive for two-sided state (`rungs=()`, `episode_id=0`). Under the stated B-only merge invariant this reuse is justified and creates no SELL orders. The helper is unchanged, so ordinary Follow300 SELL accounting is unaffected.
- `read_merge_leg` belongs with the existing core outbox SQL and returns a typed `MergeLeg`. Its non-optional row assumption is supported by the `outbox_after` join. The new event is checked before `fill_for_key`, which deliberately excludes MERGED rows.
- Extracting `_note_outbox_item` separates event decoding/enqueueing from checkpoint/cursor orchestration. `_note_merge_leg` earns its separation by owning the distinct ledger-to-merge mapping; no larger abstraction or generic registry is needed for this change.
- No changed file crosses from below 1,000 lines to above it. `session_core.py` was already 1,108 lines and becomes 1,112 through one import and the three-line enqueue method; this narrow addition does not justify a broad decomposition in this step. `core_persistence.py` is 965, `core_trace_codec.py` 859, and `test_trader_core_persistence.py` 977 lines.
- No added comments or docstrings. The refactored outbox function contained none. `note_schedule` retains its existing docstring after a single event-union extension, as the plan requests.
- The meaningful structural simplification needed here is the inventory-baseline/cursor reconciliation boundary in finding 1. Merely batching the two merge legs would leave the baseline conflict unresolved.

## Correctness and A regression assessment

- Both merge legs subtract the full ledger quantity independently; positive leftovers remove cost proportionally, zero/oversize subtraction clears cost and buy time.
- A standalone `Merged` does not change orders, `sell_only`, `recovery_pending`, or `recovery_generation`; it marks scheduling dirty without immediately evaluating or emitting places/cancels. Those properties are covered by the new two-sided test and independently executed here.
- Trace encoding already uses the generic event serializer. The new decoder enforces exact keys and the existing numeric types; codec roundtrip and completeness tests pass with `Merged` included. `DIGEST_GROUPS` is unchanged.
- For A's reachable `matched`, `confirmed`, and `failed` rows, the extracted function preserves the prior fill parsing, BUY/SELL mapping, enqueue order, cursor update, and snapshot call. A never writes merged rows. The deliberate exception-path difference is that a failure while reading a fill now calls `core.revert`, potentially adding a revert trace record; it does not change successful trading behavior.
- No changed prices, sizes, cancellation logic, or SELL path were found outside finding 1. The diff does not touch `config/trading.toml`, `compose.yaml`, or the A test files `test_strategy_core.py` and `test_follow300_replay.py`.

## Independent validation

All Python ran through the existing E environment with `uv run --no-sync --offline --no-cache python -B`, `PYTHONDONTWRITEBYTECODE=1`, and the project PYTHONPATH. No `.env` credentials, network operations, VPS commands, live trader, or merge transaction were used. SQLite probes used `:memory:`. Pytest's cache provider was disabled, and only tests without filesystem fixtures were selected.

- **106 passed, 2 existing Nautilus/NumPy deprecation warnings, 2.10 s**: all of `test_strategy_two_sided.py`, `test_strategy_core.py`, and `test_strategy_scheduling.py`; trace codec roundtrip/completeness; five focused session-core fill/recovery/mismatch tests.
- **2 passed as direct test-function calls**: `test_outbox_consume_advances_cursor_only_after_apply` and `test_outbox_consume_applies_merge_without_recovery`, substituting SQLite `:memory:` for their `WalletStateStore(tmp_path / "w.db")` constructors. This validates the actual added assertions without writing test databases, but does not replace file-backed durability testing.
- **Failure-path probe reproduced finding 1** with the real store, core, recovery coordinator, outbox consumer, and saved checkpoint. The injected failure was on the second persistence call before database mutation; no transaction-corruption assumption is required.
- **Quote-impact probe confirmed** the concrete prices and quantities above through `desired_bids`.
- `uv run --no-sync --offline --no-cache ruff check --no-cache --select C901 src/strategy/engine.py src/trader/core_session_io.py`: passed.
- `git diff --check c779fa21..624fb55f`: passed.
- Scoped diff for A golden tests and service configuration: empty. Full commit diff inspection confirms no digest-group changes.
- E remains at `624fb55f` with no tracked changes. Its two pre-existing untracked backtest directories are unchanged in `git status --short`.

## Limits and handoff

- This was report-only. No source edits, staging, commits, feature status changes, or pushes were made.
- The complete serial suite, file-backed persistence tests, whole-project typecheck, and Follow300 archive replay were not rerun. The implementer reports typecheck/lint success and one regression failure, `test_user_stream_is_bound_on_the_host_run_path`, due to missing `WalletHost._mode` in unchanged `wallet_host.py`. That unrelated failure is not independently reproduced here and is not charged to this diff.
- STEP-006 still needs the clean-core outbox-head initialization and policy/checkpoint widening already called out by the plan. Startup head initialization alone does not fix finding 1, which arises during an in-process consume failure and recovery.
- Keep STEP-004 unapproved until finding 1 is fixed and its failure/recovery/retry regression passes.
