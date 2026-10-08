# s3-rev — STEP-003 strict maintainability review
Status: FINAL

Scope: `esports-trader` diff `b86cdcdc..4a4018fb`. Report-only review; no product or workspace source edits.

Read the shared context, brief, both AGENTS.md files, feature-json-step-review skill, every `review.instructions` entry, feature spec, STEP-003 plan, and implementer report. Reviewed the complete diff and the surrounding wallet, core-outbox, dashboard, chain-reconciliation, and frozen dependency code. No source edits, staging, commits, network calls, wallet-key use, or live operations.

Verdict: **one major correctness finding; resolve it before live activation.** No separate maintainability findings.

## Findings

### 1. major — `src/trader/wallet_store.py:499`: prevent reconciliation from applying an in-flight merge before the ledger does

`apply_merge` subtracts `qty` from whatever position the cache currently holds. The independent chain-divergence task can already have accepted the post-merge balance while receipt polling is still running. That task does not take `engine._chain_lock`; it checks ordinary fill inflight/settling/live-SELL guards, none of which marks a pending merge. Stamping the chain floor at line 512 happens after the damage.

Reproduced through the real `_apply_chain_balance` helper: confirmed holdings YES=200 @ 0.40 and NO=150 @ 0.55 ($162.50 held; net=50), then a fresh block containing a successful merge of 150 pairs. Reconciliation writes YES=50 and NO=0. When receipt polling returns, `apply_merge(..., qty=150)` writes YES=0 and NO=0. `ledger_position(YES)` correctly reports 50. The cache and persisted positions therefore lose the 50-share unpaired tail. Restart loads that wrong position row until chain reconciliation repairs it. Wallet inventory marks, later merge thresholds, and the end-of-map leftover/PnL snapshot can consequently be wrong; `MatchWorker.end_snapshot` reads these cached positions directly.

**Concrete fix:** establish a per-token merge/reconciliation guard before submitting the external transaction and keep it active through `apply_merge` and core delivery. Have chain position reconciliation respect that guard, with release on every terminal outcome. Alternatively, make the divergence read-and-apply operation share the merge lock for the whole receipt/apply sequence. Coordinate this boundary with STEP-005; its stated nonce lock alone is insufficient. Add a regression that delivers a post-merge chain snapshot before the receipt result and requires cached and ledger YES=50, NO=0 after success. Preserve crediting the actual on-chain quantity and the existing clamp for genuinely insufficient holdings.

This is dormant in STEP-003 because production does not call `apply_merge` yet, but must be resolved before B activates. No service A behavior change is required for the fix.

Supporting paths: `src/trader/engine_seams.py:803` starts the independent read, `src/trader/engine_seams.py:879` uses fill/SELL guards, and `src/trader/engine_seams.py:889` writes down the position. `poly-maker/src/polymaker/engine.py:619` runs divergence from its reconciliation task without the nonce lock used by merge transactions. The fixture uses a 100-second-old fill, so the existing 10-second settling guard cannot mask the defect.

## Maintainability assessment

- The feature stays in the canonical wallet ledger layer. The subtraction helper is shared between live application and replay, so the new semantics do not fork between those paths. A merge is kept separate from the BUY/SELL `Fill` domain.
- The new replay status branch is required by the different operation; it does not scatter strategy checks through unrelated flows. The merge writer owns its two ledger/outbox inserts and uses the existing position writer.
- Both legs, both positions, and both outbox rows commit together; memory and cash change after the successful commit. The second-leg fault-injection check confirms database rollback and unchanged memory. There is no unnecessary async orchestration inside this synchronous operation.
- The helpers have concrete required arguments, no casts or `Any`, and no unnecessary optionality. The homogeneous token pair follows the project's existing token-pair convention.
- `wallet_store.py` grows from 927 to 994 lines. It does not cross the skill's 1,000-line threshold, and the additions remain cohesive. Replacing the small merge-specific writer with a generic ledger framework would add concepts without a clear simplification in this diff.
- No new comments, docstrings, or suppressions are added. Changed functions remove their old docstrings, as instructed. The stale `apply_failed_fill` docstring noted by the implementer is outside the modified function bodies and is not treated as a new comment violation.

## Correctness checks and planned seams

- Merge legs use `side='MERGE'`, `status='MERGED'`, full merged quantity, cash `+0.5 * qty`, `is_maker=0`, and no maker order ID. The wallet credits `+qty` once. Remaining positions retain their average price; exhausted positions reset it to zero.
- The transaction makes first-leg idempotency sufficient for the two-leg event. Repeats, reversed token order, and repeat after reopen preserve ledger, outbox, positions, and cash.
- `ledger_position` replays the merge in outbox order. FAILED-fill reconstruction now retains the merge reduction. Reopen restores merge cash and reduced durable positions.
- `fill_for_key` excludes MERGED before parsing `Side`. MATCHED-key, unacked-MATCHED, inflight, and last-fill readers skip these rows. Journal outbox readers skip the acked merge events; the core outbox still sees both legs.
- The core cursor currently stops at the first merge row because `fill_for_key` returns `None`. This is the explicit STEP-004 handoff, with no production caller in STEP-003; it is not an additional finding. STEP-004 must handle `event='merged'` before trying the fill reader.
- Both wallet cash queries and `cmd_wallet` include merge cash; `cmd_wallet` excludes merge rows from the fill count. BUY-only booked/unsettled queries cannot count the merge legs as trades.
- No changes to order placement, cancellation, sizes/prices in strategy formulas, SELL paths, Follow300, or service A configuration appear in the diff. Existing A ledgers contain no MERGED rows, so the added status filters preserve their previous query results.

## Verification

- The requested diff changes exactly three files; HEAD is `4a4018fb` on `main`. Only the two existing untracked backtest output directories are present.
- Wallet tests: 49 passed. The first run inherited `PYTEST_N` from the environment; all subsequent pytest runs explicitly removed it and ran serially.
- Serial focused regression: 240 passed (`wallet_store`, `engine_seams`, `chain_balances`, `core_persistence`, `session_core`, `core_recovery`).
- Scratch characterization checks: 3 passed. They reproduce the double subtraction, prove rollback of both ledger/outbox/position legs and memory when the second insert fails, and prove idempotency after restart with reversed token order. The race test deliberately asserts the observed wrong cache value; it is evidence of the defect, not a correctness pass.
- `summarize.py --self-check`: `self-check ok`.
- Ruff check on all three changed files: passed. Ruff formatting check: all three already formatted.
- Whole-project basedpyright: 0 errors, 0 warnings, 0 notes.
- `git diff b86cdcdc..4a4018fb --check` passes.
- Final git status still contains only the same two existing untracked backtest directories. No source changes were made by this review.

All Python verification ran from E through `uv run --offline --no-sync python -B`, with `PYTHONDONTWRITEBYTECODE=1`, the uv cache under `work/s3-rev`, and pytest's cache provider disabled and base temp under `work/s3-rev`. Subsequent pytest runs used `env -u PYTEST_N`.

Reproduction file: `R/work/s3-rev/test_step003_review.py`. Exact command:

```sh
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
review_dir=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/two-sided-live-b/fleet/work/s3-rev
env -u PYTEST_N PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR="$review_dir/uv-cache" PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --offline --no-sync python -B -m pytest -p no:cacheprovider \
  --basetemp="$review_dir/pytest-review" "$review_dir/test_step003_review.py" -q -s
```

The key output is `race reproduced: cached YES=0, ledger YES=50, authoritative YES=50`.

The implementer's full-suite report lists failures in `test_user_stream_is_bound_on_the_host_run_path` (the fixture omits `_mode`) and `test_home_renders_all_sections` (the expected available-cash text is absent). Their source/test paths are unchanged by this diff. They are not new STEP-003 findings; this reviewer did not rerun the full suite or independently baseline those two failures. The 240 focused tests and three scratch checks above were independently run.
