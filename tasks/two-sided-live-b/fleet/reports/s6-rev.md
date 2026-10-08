# s6-rev — STEP-006 maintainability and correctness review
Status: FINAL

## Scope and rules

- Review range: `a58d19c9..77a85364` in `esports-trader`.
- Read shared context, agent brief, both project instructions, `feature-json-step-review/SKILL.md`, and the workspace review configuration. Applying strict structural review and the no-new-comments rule.
- Report only: no product edits, staging, commits, network, VPS, trading, or secret access.
- Focus: Follow300 extraction compatibility, final merge after the proven fence and before snapshot/zero/unregister, restart with the outbox cursor at head, and B BUY-only behavior.

## Verdict

Changes required: **2 major findings, 1 minor finding; no blocker or nit findings.** The successful quote/recovery/finalization paths and A regressions pass, but the clean B core does not preserve durable execution identity, and B's merger lifetime is not owned through cancellation. Resolve the two major findings before approving STEP-006 for live routing.

## Completed review

- Read the full STEP-006 plan, feature requirements, implementer report and all ten changed files. Traced surrounding lifecycle, merge, recovery, durable execution and outbox code.
- Current checkout is `2ebea5e6`, one commit after the review target. `git diff 77a85364..HEAD` is empty for the reviewed production files and worker fixtures/tests. Validation uses those unchanged files; findings remain scoped to `a58d19c9..77a85364`.
- Reproduced both stale financial metadata and overwritten bindings across a clean B restart.
- Reproduced the inherited finalizer's merger path returning after cancellation while its final merge task is still running with both merge holds active.

## Findings

### 1. Major — clean restart reuses durable execution identities

Location: `src/trader/two_sided_worker.py:73` (new clean `LiveCore`), `src/trader/two_sided_worker.py:87` (adoption under the unchanged condition/session ID).

The new core resets `next_order_seq` and `_cycle`, while persistent `core_commands` and `core_order_bindings` still belong to the same session. IDs restart at `c0`, `c1`, and command keys can again be `<cid>:1:c0`, `<cid>:1:c1`. The durable command upsert updates outcome/hash/venue fields but deliberately does not replace price/quantity; reused keys therefore retain the previous order's financial metadata. Binding keys `(session_id, core_order_id)` also collide. This is separate from the deliberately accepted unknown late-fill fail-safe.

The surrounding identity contracts are `strategy/lifecycle.py:759`, `trader/core_execution.py:132`, `trader/core_persistence.py:725`, and `trader/core_persistence.py:661`. The checkpoint written by recovery must not replace the old counter before the restart's new identity range is established.

Concrete fix: keep the strategy/inventory core clean, but preserve monotonic execution identity across launches. Seed `next_order_seq` above every persisted order ID in the checkpoint, bindings and commands before publishing the core or persisting recovery, or give every new launch a durable unique order namespace. Order IDs must remain unique within the condition/session; unique order IDs also prevent command collisions when cycle restarts. Add a restart regression with real existing commands and bindings, changed book prices after restart, and checks that both generations remain distinct and their stored financial metadata matches the submitted quotes.

Validation: a fake-only local reproduction shows new quotes `(0.48, 20)` and `(0.46, 20)` using command keys whose persisted prices remain `(0.28, 0.66)`. The old `old-c0`/`old-c1` bindings are replaced by `new-c0`/`new-c1`. Existing restart tests seed fills and a merge but no prior core command/binding rows.

Reproduction used the real worker `open_core`, quote cycle, `prepare_dispatch`, dispatch-result persistence and core snapshot persistence on a disposable SQLite store. The first book pair had YES mid 0.31 / NO mid 0.69; after restarting the core, it had YES mid 0.51 / NO mid 0.49. Observed output:

```text
before: 0xCOND:1:c0 price=0.28 qty=20; 0xCOND:1:c1 price=0.66 qty=20
old bindings: c0 -> old-c0; c1 -> old-c1
new quotes: c0 price=0.48 qty=20; c1 price=0.46 qty=20
after DB: 0xCOND:1:c0 price=0.28 qty=20; 0xCOND:1:c1 price=0.66 qty=20
new bindings: c0 -> new-c0; c1 -> new-c1
```

### 2. Major — cancellation lets a submitted final merge outlive the worker and wallet store

Location: `src/trader/two_sided_worker.py:129` (immediate cancellation propagation), `src/trader/two_sided_worker.py:61` (the inherited lifecycle now owns a `PairMerger`).

`PairMerger.wait_inflight()` shields the merge task, so cancelling the match task during `merge_all()` cancels the caller while the submitted merge keeps running. `_await_final_merge` immediately re-raises cancellation. The inherited `run()` finalizer then calls `_quiesce(False)`, which returns immediately because `_quiesced` was set by the original finalization. Its final `PairMerger.cancel()` only sets a flag and neither drains nor cancels the active merge. `WalletHost.teardown()` gathers the completed match task and can close the engine/store while that merge is still waiting for its receipt or booking its result. This can lose the accounting of a successful merge during an ordinary shutdown; the old DustSweeper cancellation behavior does not establish a safe contract for the new slot.

Concrete fix: give B an owned, tracked shutdown/finalization task, or a B-specific finalizer that always drains an already-submitted merger under shield before allowing the match task to finish, including when `_quiesced` is already true. Keep the wallet store alive through receipt classification and successful booking. Do not start another merge or zero/unregister a known unbooked result to make cancellation finish. Add a regression that starts the real PairMerger behind a controlled fake adapter, cancels the worker after submit, and verifies worker/host teardown cannot finish or close the store before the adapter outcome is booked or preserved for recovery. Cover cancellation while waiting for a threshold merge as well as the final merge.

Validation: an actual TwoSidedWorker plus PairMerger on a disposable SQLite store, with the adapter held on an asyncio event, returned from the exact inherited merger-related finalizer actions after cancellation. At that point the merge task was still running, both holds were active, and the worker was still registered. Releasing the adapter and adding an external `wait_inflight()` afterward booked the merge and reduced positions from `12/10` to `2/0`; the existing finalizer did not perform that wait.

Relevant existing call sites: `match_worker.py:273` calls the second quiesce then `cancel`; `match_worker.py:1323` skips that quiesce because it was already started; `pair_merge.py:79` only latches cancellation; `pair_merge.py:85` shields the active task; `wallet_host.py:1608` gathers match tasks before closing resources. Observed output:

```text
worker_finally_returned=True
merger_still_running=True
holds_still_active=True True
worker_still_registered=True
booking_only_after_later_external_wait=2.0 0.0
```

The new cancellation test at `tests/test_trader_two_sided_worker.py:475` supplies a merger callback that immediately raises `MergeAccountingError`; it never exercises cancellation with the actual merger child task waiting for a submitted transaction, so its passing result does not cover this lifetime gap.

### 3. Minor — new test harness uses untyped state holders and anonymous multi-field results

Location: `tests/test_trader_two_sided_worker.py:333` (helper contract), `tests/test_trader_two_sided_worker.py:398` (representative `dict[str, Any]` holder; repeated at 443, 481 and 514).

The new helpers return anonymous tuples containing `Any`, and final-merge callbacks communicate through mutable string-key dictionaries such as `holder["store"]`, `holder["calls"]` and `holder["n"]`. The project explicitly forbids `dict[str, Any]` and anonymous multi-field tuples for its own data. These are controlled test state, not an external untyped boundary. The callbacks are supplied before the worker/store exist, which creates the extra dictionary wiring and obscures what a scenario owns.

Concrete fix: return a typed frozen dataclass (for example `FinalWatch` with `worker: TwoSidedWorker`, `store: WalletStateStore`, `calls`, and `finished`) from the setup helper. Construct the scenario first, then install the merger callback using those named fields; keep an attempt counter as a local `nonlocal` variable where needed. Use named typed results for the new attachment/restart helpers too. This removes the holder dictionaries and most new `Any` plumbing without changing assertions or existing A fixtures.

## Required focus outcomes

- **Follow300/service A extraction: no findings.** `_core_limits` and `_core_freshness` preserve all original arguments and constants. `_write_decision` preserves journal-error handling, cell clearing, early return and wake order. `_adopt_core` assigns the cell before snapshot load instead of afterward, but both execute synchronously without an intervening await or cell reader. A still builds `Follow300Policy`, restores its checkpoint and opens the same trace. The DustSweeper type assertion holds for A. Existing A test/golden files are unchanged in the review range and the selected regression tests passed.
- **Final-merge happy-path ordering: no findings.** The inherited path clears the cell, drains threshold work, stops quoting, cancels orders and proves the fence before invoking B `_finish_final`. B awaits `merge_all` before the base `end_snapshot`, Telegram snapshot, zeroing, unregister and detach. The new order test verifies this and includes merge cash in the final result. An unproven fence does not call final merge. The cancellation lifetime problem is finding 2.
- **Outbox cursor placement: no findings in the cursor ordering itself.** B advances the fresh core cursor to the existing outbox head before restore/recovery and worker registration. This attach sequence has no intervening await. Old fill/merge events are skipped, inventory arrives through verified Recovery, and the restart test verifies the quantities and BUY quotes. Persistent execution counters are a different invariant and fail as described in finding 1.
- **B SELL boundary: no findings.** The replacement `_dust` object has a no-op `sweep`; it does not invoke the FAK SELL gateway path. The core's venue quote conversion applies `_refuse_sell` before dispatch, and the two-sided strategy produces BUY targets. New tests verify the guard, BUY quotes and absence of finalization market orders. Both orientations and the feed quote window are tested, as is history-gap delivery without invoking the model.
- **Structure and size:** the new worker is cohesive at 153 lines. `match_worker.py` was already 1,374 lines and becomes 1,391; `session_core.py` was already 1,112 and becomes 1,113. No file crosses from below to above 1,000 lines. The extraction hooks earn their use in the subclass; a broad A-module split would exceed this step. The new test module is 533 lines, but its explicit state should replace the untyped holder structure in finding 3.
- **Review configuration:** no new explanatory comments or docstrings occur in the new worker or test module; each has only the existing-style pyright file directive. The extracted A `open_core` and `_quote_pm` docstrings were removed. No product edits were made during review.

## Decisions and scope limits

- The report-only brief overrides the skill's preference to fix code. All findings include a concrete fix; none was implemented, staged or committed.
- Accepted plan behavior is not reported as a regression: unknown pre-restart fills halt B through `ownership_unresolved`; match-meta PnL is captured before final merge while the Telegram snapshot is captured after it; a known unbooked merge blocks zeroing/final summary; no automatic redeem is added.
- The implementer correctly strengthened the planned generic exception handling by retrying `MergeAccountingError` instead of zeroing a known unbooked merge. The retry does not solve cancellation ownership (finding 2). The report's separate warning about a permanent booking failure and process-memory-only pending receipt is acknowledged; that persistence design originates before this review range and was not counted as an additional STEP-006 finding.
- Production host selection of `TwoSidedWorker` is STEP-007; it is not wired in this commit. The findings concern the new worker when invoked and must be resolved before that routing is enabled.

## Validation

- Serial targeted tests passed: 182 passed, 3 dependency deprecation warnings, 187.21 seconds. Bytecode and pytest caches were disabled; temporary files were isolated under `/private/tmp/s6-rev-pytest-20261008-a`.
- Reproductions use only fake worker/engine helpers and disposable SQLite databases under `/private/tmp`; no network or live credentials.
- Exact targeted test command, run in E:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_N=0 \
PYTHONPATH=src:scripts:../prediction-market-backtesting \
uv run --no-sync --offline --no-cache --group backtest python -B -m pytest \
  -p no:cacheprovider \
  tests/test_trader_two_sided_worker.py \
  tests/test_trader_pair_merge.py \
  tests/test_strategy_two_sided.py \
  tests/test_trader_match_lifecycle.py \
  tests/test_trader_dust_sweep.py \
  tests/test_core_trace.py \
  tests/test_follow300_replay.py \
  -q --basetemp=/private/tmp/s6-rev-pytest-20261008-a
```

- Ruff McCabe check passed for `two_sided_worker.py`, `match_worker.py`, `dust_sweep.py`, and `pair_merge.py`, using `uv run --no-sync --offline --no-cache python -B -m ruff check --no-cache --select C901 ...`.
- Review-range diff is empty for `test_strategy_core.py`, `test_follow300_replay.py`, `tests/fixtures`, `test_trader_match_lifecycle.py`, `test_trader_dust_sweep.py`, and `test_core_trace.py`.
- Git status before and after review has only the same two pre-existing untracked `validation_*ts-regress-*` backtest directories. No tracked E file changed. The full suite, broad typecheck and other implementer checks were not rerun; their results remain the implementer's reported evidence, not independent review verification.
