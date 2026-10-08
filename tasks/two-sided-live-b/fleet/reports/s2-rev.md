# s2-rev — STEP-002 maintainability and correctness review
Status: FINAL

Scope: `esports-trader` diff `8dadf747..1d380527`, ending at `1d380527aebad8b68fd66147b9eabd4cd7745880`. Report-only review under `feature-json-step-review` and every line of workspace `review.instructions`.

Read fully: shared context, review brief, both repositories' AGENTS.md, review skill/config, STEP-002 plan, implementation report, STEP-002 requirements and surrounding resolved questions, and the full scoped diff. Traced the unchanged lifecycle, budget, scheduling, signal normalization, backtest repricing, and live cancellation/settlement seams. No nested AGENTS.md exists under the reviewed source/tests.

Disposition: **request changes**. Two correctness findings: one major, one minor. No blocker, structural maintainability finding, or cosmetic nit.

## Findings

### 1. major — `src/strategy/two_sided_quoting.py:220` — replacement placement ignores the opposite unresolved bid's price

`_reconcile_bids` passes all missing targets directly to `place_missing`. Its occupancy check only protects the candidate's own `level_index`. The opposite bid can still be canceling or unknown, so the shared price math's ≤0.99 cap on the desired pair does not constrain the old/new pair during replacement. This is a gap in FR-1's pair-price invariant and in the atomicity of a two-token transition.

Reproduced with fresh books, a fresh clock, zero inventory, and the factory policy:

1. Fair 0.15 opens token 0 at 0.12 × 20 and token 1 at 0.82 × 20.
2. At T0 + 1 second, fair 0.85 produces both `reprice` cancels.
3. Acknowledge token 0 alone. The plan immediately places token 0 at 0.82 × 20, while token 1 remains at 0.82 with status `canceling`. Their state prices sum to **1.64**.
4. The live path has the same result: token 1 `CancelTimeout` → `unknown`, token 0 `CancelUnsettled` → `gone`, then token 0 `BuySettled(matched_qty=0)` → a new 0.82 BUY while token 1 is still unknown at 0.82 with cancel reason `reprice`.

The existing tests only move fair by one or two ticks, which hides this violation behind the six-tick combined spread. A post-only rejection can prevent a crossed replacement from resting when the venue still exposes the opposite bid. That backstop does not make the core plan obey the pair cap or resolve an opposite BUY whose terminal/fill outcome is still unknown. This finding does not claim the reproduction bypasses venue post-only enforcement or demonstrates an executed loss.

**Concrete fix:** add a two-sided placement eligibility check before `place_missing`. Compare each candidate against the opposite BUY exposure still retained in `state.orders`, conservatively including cancellation/settlement uncertainty until it is resolved. Defer the candidate if its price plus the opposite order's price exceeds `policy.max_bid_sum_ticks * policy.tick`; the simpler conservative alternative is to wait for both affected token slots to settle before replacing either. Keep this rule in the two-sided module, leaving Follow300 placement unchanged. Existing immediate evaluation on `CancelAck`/`BuySettled` can release the deferred candidate. Add event-chain regression tests for the large move and asymmetric cancel timeout, in both orientations, asserting no unsafe replacement is emitted and safe quoting resumes after the opposite order resolves.

### 2. minor — `src/strategy/two_sided_quoting.py:47` — the no-feed clock sentinel can be considered fresh

The initial live clock uses `now_ns=0` to mean no feed tick has arrived. `_clock_pull_reason` only calls `is_fresh`, whose implementation accepts age ≤ `entry_stale_s` and does not distinguish the sentinel. With event time 10 seconds, the initial zero clock, fresh even books, and the default 16-second freshness window, a forced Wake emits two 0.47 × 20 BUYs with an empty block reason. The plan explicitly says that no bids may appear before the first feed tick.

Ordinary long machine uptime hides this bug. It remains a concrete boundary error when the monotonic epoch is recent; the core should not depend on uptime to distinguish missing clock data.

**Concrete fix:** in the two-sided clock gate, treat `clock.now_ns == 0` as `stale_signal` before accepting freshness. Keep the existing reason precedence and leave the shared `is_fresh` behavior unchanged for Follow300. Add a test starting from the actual zero-clock initial state with fresh books and a Wake inside the freshness window: no places before the first nonzero `ClockUpdate`, then normal two-sided quotes after it.

## Maintainability assessment

- The new quoting module is cohesive and 236 lines. The price/skew/size math remains shared with the backtest; pull gates, hold logic, and reconciliation have distinct responsibilities. The required per-token hold state and typed `HeldBids` result are explicit.
- No file crosses 1,000 lines. `quoting.py` decreases from 951 to 949 lines; `lifecycle.py` grows from 792 to 793; the test file is 558 lines. There is no unjustified decomposition blocker.
- Policy dispatch is confined to engine and deadline calculation. The shared quoting changes are mechanical helper exposure and removal of an unused parameter. No scattered two-sided branches are added to Follow300 business logic.
- The local reconciler has a concrete justification: Follow300's partial-fill dust rule must not suppress two-sided reprice cancels. Shared placement still owns IDs, reservations, and order occupancy. The missing pair eligibility rule in finding 1 belongs at this two-sided boundary, rather than adding feature checks to shared placement.
- The grouped pull checks preserve the specified precedence without a generic dispatch mechanism. No obvious thin-wrapper, cast/Any, dead fallback, or duplication finding warrants a separate comment.
- The new module contains its one-line module docstring, allowed by the implementation plan. The diff adds no inline explanatory comments or suppression flags. Existing scheduling comments remain in the unchanged Follow300 boundary logic. No comments-related finding under `review.instructions`.

## Scope and plan decisions

- Accepted the documented split between pure math and quoting to avoid the policy import cycle and keep the existing quoting file below 1,000 lines.
- Accepted the documented local reconciliation and `share_floor` rounding decisions; rounding down cannot increase the submitted quantity above the calculated size.
- Treated hold clocks as transient per the explicit plan, with B checkpoint/trace integration excluded. Did not reopen that documented feature-text deviation as an additional finding; Follow300 codec behavior is unchanged.
- LiveCore policy widening, B budget integration, dust exclusion, journal mapping for `band`, and restart wiring remain assigned to later steps. Their absence is not a STEP-002 finding.
- No SELL placement path is added. Both token orientations, skew direction, full-fill replenishment, pull precedence, one-tick hold deadline, two-tick repricing, and recovery resumption are covered by the passing focused tests. A direct replay also confirmed that a 1-share partial fill does not prevent 2-tick repricing cancels.
- The scoped diff leaves `tests/test_strategy_core.py`, `tests/test_follow300_replay.py`, `src/trader`, config, and compose unchanged. Follow300 scheduling retains the same deadline calculations after extracting the cadence helper.

## Verification

- HEAD is `1d380527aebad8b68fd66147b9eabd4cd7745880`; the reviewed source/test files match the scoped commit.
- 395 focused tests passed in 3.52 seconds (strategy two-sided/core/scheduling/recovery/late-fills/budget/imports, kill gate, mid-spike, trader recovery/session/persistence/state-report/engine seams, core trace). Two pre-existing NumPy timedelta deprecation warnings.
- Inline read-only event replays reproduced both findings, including the live `CancelUnsettled`/`BuySettled` path with the opposite cancel timing out. They exercised the real `engine.step` and factory policy; no production function was patched.
- `uv run --no-sync --offline --group backtest python -B -m backtest.two_sided` printed `two_sided ok` and exited 0.
- `git diff --check 8dadf747..1d380527` passed. Scoped comparison of the A test files, trader, config, and compose was empty.
- Plain `uv run` initially failed opening the default uv cache under the sandbox. Checks used a task-owned cache, `uv run --no-sync --offline`, Python bytecode disabled, pytest cache disabled, and a task-owned pytest temporary directory. No environment/dependency changes were made.

Focused test command (run inside E):

```text
env -u PYTEST_N \
  UV_CACHE_DIR=R/work/s2-rev/uv-cache PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --no-sync --offline --group backtest python -B -m pytest \
  -p no:cacheprovider --basetemp=R/work/s2-rev/pytest-step2 \
  tests/test_strategy_two_sided.py tests/test_strategy_core.py \
  tests/test_strategy_scheduling.py tests/test_strategy_recovery_quotes.py \
  tests/test_strategy_late_fills.py tests/test_strategy_budget.py \
  tests/test_strategy_imports.py tests/test_kill_gate.py tests/test_mid_spike.py \
  tests/test_trader_core_recovery.py tests/test_trader_session_core.py \
  tests/test_trader_core_persistence.py tests/test_trader_core_state_report.py \
  tests/test_trader_engine_seams.py tests/test_core_trace.py -q
```

`R` in the displayed command is an abbreviation for the absolute fleet directory; actual tool execution used absolute paths. The implementation report separately records the unchanged eight-map Follow300 replay, whole-project typecheck, and staged lint as passing. Those expensive/redundant checks and the full suite were not rerun for this report-only review.

No source edits, staging, commits, live actions, or external communications. Final git status still shows only the two pre-existing untracked before/after backtest directories, left untouched. Only this report and task-owned test/cache scratch were written.
