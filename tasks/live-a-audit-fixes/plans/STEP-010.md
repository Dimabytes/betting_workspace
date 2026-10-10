# STEP-010 plan: RISK-04 keep the fair age on a failed anchor

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `35f8b6d1`. Paths are relative to that worktree unless they start with `runs/` or `orchestrate/`.

Evidence: `orchestrate/work/plan-step-010/`. Each probe ran on `35f8b6d1` with the worktree untouched (`git status` empty after every run).
- `fix_plugin.py` is the planned `_rebuild_latch` change, installed in memory for pytest.
- `diag_plugin.py` is the planned `LiveCore._log_anchor`, installed in memory.
- `test_proto_anchor_age.py` and `test_proto_session_anchor.py` hold the exact planned test code.
- `red_on_35f8b6d1.txt`: 6 failed, 3 passed without the patch. `suite_with_patch.txt`: full suite with both patches and the new tests.

## 0. Result

The failed-anchor branch of `_rebuild_latch` keeps the previous `fair_radiant` and `fair_valid`, but stamps `fair_ts_ns` with the rejected signal (`src/strategy/quoting.py:714`). The fix takes `fair_ts_ns` from the previous latch. Nothing else in the strategy changes. The existing exit-age gates then work as documented: after 45 s the frozen fair is no longer usable.

The diag gets one new line, `anchor`, written by `LiveCore.apply` when a new signal rebuilds the latch.

Size: `src/strategy/quoting.py` +1/−1, `src/trader/session_core.py` about +20. Tests: one new file `tests/test_strategy_anchor_age.py` (5 items) and 4 new items in `tests/test_trader_session_core.py`, plus a helper extraction there. No schema, config, checkpoint, backtest, B, or `poly-maker` change.

## 1. What is broken today (evidence)

1. `src/strategy/quoting.py:704-719`. On `not passes_anchor(...)` the latch gets `fair_radiant=previous.fair_radiant`, `fair_valid=previous.fair_valid`, but `fair_ts_ns=signal.source_received_ns` (the rejected signal).
2. Two readers use `fair_ts_ns` as the age of the fair:
   - `_quote_v5_latch` (`:810-815`): `is_fresh(latch.fair_ts_ns, exit_stale_s)`; if not fresh, `_blocked(reason="anchor")` cancels every order.
   - `_recovery_fair` (`:884-890`): returns `None` when the fair is not fresh, so the recovery SELL joins the ask.
   - The backtest reads it the same way (`src/backtest/strategy.py:885-895`). `core_state_report.py:159-166` prints the age.
3. Audit repro (`runs/live-a-money-audit-20261009/work/orchestrator/anchor-age-independent.json`): fair 0.7, 12 rejected signals over 120 s, recorded age 0 s, `is_fresh(..., 45)` True. With the original timestamp it is False.
4. My probe on `35f8b6d1` (`probe_anchor_age.py`): valid fair 0.56 at t=0, held YES 100, SELL `c0 0.56`. Rejected signals every 10 s. HEAD keeps `c0` and age 0 s through 120 s. With the fix, age grows by 10 s per step, and at 50 s the plan cancels `c0` with reason `anchor`.
5. The deployed design doc already wants this: `docs/experiments/buy-ladder-v2/progress.md:98-102` ("keeps the last anchored fair with its own model timestamp … A SELL may use that carried fair only while it is inside the exit signal age"). No doc change.
6. Live impact today: no anchor streak in 104 traces passed 17.7 s (`runs/live-a-money-audit-20261009/reports/a2-dev-pickoff.md`, F4). So no recorded live session would change its plan.

## 2. Design decisions

1. **One field, one branch.** Only `fair_ts_ns` in the failed branch changes. `signal_ts_ns=signal.received_ns` stays: `_sync_latch` (`:762`) needs it to see that this signal was already processed. `predicted_delta`, `anchor_p`, `book_p_radiant` stay from the rejected signal. Only `_reanchor_latch` reads `predicted_delta`, and it never runs on an `anchor` latch (`reanchor_boundary_ns`, `:435-440`). So the frozen fair is never recomputed from the rejected delta.
2. **No previous latch.** `fair_ts_ns` stays the rejected signal's source time, `fair_valid=False`, `fair_radiant=0.0`, as today. A chain of failures from no latch carries the first rejected time forward. That time gates nothing: every reader checks `fair_valid` first (`:813`, `:886`, `src/backtest/strategy.py:887`). No fair of 0 or 1 is used, and no SELL is placed. This is the explicit no-fair case from the step; it needs no extra branch. Test 2 in 5.1 pins it.
3. **Successful anchor.** The success branch (`:720-735`) already stamps `fair_ts_ns=signal.source_received_ns` of the new signal. Unchanged; test 3 in 5.1 pins it.
4. **Exit policy unchanged.** After 45 s:
   - normal path: `_blocked(reason="anchor")` cancels the SELL and places nothing until a valid anchor or a stale signal;
   - recovery path: the SELL is repriced to the ask (fair `None`).
   Both are the current documented policy. I do not change the kill, anchor, cutoff, or SELL floor rules.
5. **Restore.** The checkpoint has no latch (`StructuralCheckpoint`, `src/trader/core_persistence.py:158-180`; `apply_checkpoint` keeps the in-memory `latch`, `:529-570`). A restarted core starts with `latch=None`, so an old fair never survives a restart. The restart test in 5.2 pins this; no code change.
6. **Diag `anchor` line.** In `LiveCore.apply` (`src/trader/session_core.py:617-631`), the only place the live core calls `step`. Log only when a new signal rebuilt the latch: the latch exists and either there was no latch before or `signal_ts_ns` changed. Re-anchors (every `LATCH_REANCHOR_SECONDS = 0.25` s, `src/shared/constants/strategy.py:46`) do not change `signal_ts_ns` and are not logged. So the volume is one line per model signal per A market. Fields: `yes_token`, `accepted`, `fair`, `fair_valid`, `fair_ts_ns` (source time of the kept fair), `age_s`, `signal_ts_ns` (the signal just processed), `book_p`, `anchor_p`. `log_event` adds `in_seq`, `wall_ns`, `mono_ns`.
7. **B.** B runs `LiveCore` with `TwoSidedPolicy` (`src/trader/two_sided_worker.py:106`), and `step` routes it to `requote_two_sided` (`src/strategy/engine.py:138-139`). That path never sets `state.latch`, so `_log_anchor` returns at once and `_rebuild_latch` is never called. No B change.
8. **No comments, no docstrings** in new code (review config). The new test file needs `# pyright: reportPrivateUsage=false`, like `tests/test_strategy_recovery_quotes.py:1`, because it imports `_rebuild_latch` and private test helpers.

## 3. Change: `src/strategy/quoting.py`

In `_rebuild_latch`, failed branch (`:704-719`). Add one named variable after `previous = state.latch` and use it:

```python
    if not passes_anchor(book_p=book_p, anchor_p=signal.anchor_p):
        previous = state.latch
        fair_ts_ns = signal.source_received_ns if previous is None else previous.fair_ts_ns
        return replace(
            state,
            latch=Latch(
                fair_radiant=previous.fair_radiant if previous is not None else 0.0,
                predicted_delta=signal.predicted_delta,
                anchor_p=signal.anchor_p,
                book_p_radiant=book_p,
                fair_valid=previous.fair_valid if previous is not None else False,
                fair_ts_ns=fair_ts_ns,
                signal_ts_ns=signal.received_ns,
                anchored_ns=now_ns,
                no_buy_reason="anchor",
            ),
        )
```

Do not inline the conditional in the `Latch(...)` call. At 100 columns, ruff splits it over three lines (I checked with `ruff format`).

## 4. Change: `src/trader/session_core.py`

1. Add `Latch,` to the `strategy.types` import, between `InboundEvent,` (`:35`) and `LimitsUpdate,` (`:36`).
2. In `apply` (`:617-631`):

```python
    def apply(self, event: InboundEvent) -> Plan:
        prior = {order.order_id: order for order in self._state.orders}
        prior_latch = self._state.latch
        out = step(state=self._state, policy=self._policy, event=event)
        self._state = out.state
        self._observe_buy_lifetime(prior, event)
        self._log_anchor(prior_latch, event.now_ns)
        self.next_wake_ns = out.next_wake_ns
        ...
```

3. New method right after `apply`:

```python
    def _log_anchor(self, prior: Latch | None, now_ns: int) -> None:
        latch = self._state.latch
        if latch is None or (prior is not None and latch.signal_ts_ns == prior.signal_ts_ns):
            return
        log_event(
            "anchor",
            yes_token=self._yes,
            accepted=latch.no_buy_reason == "",
            fair=latch.fair_radiant,
            fair_valid=latch.fair_valid,
            fair_ts_ns=latch.fair_ts_ns,
            age_s=(now_ns - latch.fair_ts_ns) / 1e9,
            signal_ts_ns=latch.signal_ts_ns,
            book_p=latch.book_p_radiant,
            anchor_p=latch.anchor_p,
        )
```

`ruff format` leaves this block unchanged. The file grows from 1067 to about 1087 lines. It is already over 1k; the method follows the `_observe_buy_lifetime` pattern next to it.

## 5. Tests

Write the tests first and run them on HEAD (red), then apply sections 3-4 (green). The exact code is in `orchestrate/work/plan-step-010/test_proto_*.py`. Strict basedpyright on both proto files: 0 errors.

### 5.1 New file `tests/test_strategy_anchor_age.py` (5 items)

Copy `test_proto_anchor_age.py` as is. It imports helpers from `test_strategy_core` and `_ack_reprices` from `test_strategy_recovery_quotes`, the same pattern as `tests/test_strategy_recovery_quotes.py:6-18`.

1. `test_repeated_failed_anchor_keeps_the_fair_age`: the `verify_anchor_age.py` regression without the evidence trace (only the latch matters, so `_idle()` replaces the trace header). Latch fair 0.7 at t=0; 12 rejected signals (anchor 0.3, book_p 0.5) from 10 s to 120 s through `_rebuild_latch`. Asserts: `fair_radiant == 0.7`, `fair_valid`, `fair_ts_ns == 0`, `signal_ts_ns == 120 * NS`, `no_buy_reason == "anchor"`, and `is_fresh(now, fair_ts_ns, 45.0)` is False.
2. `test_failed_anchor_without_a_valid_fair_places_no_sell[1|2]`: no latch, held YES 100, one or two rejected signals. Each wake: `fair_valid is False`, `fair_radiant == 0.0`, `plan.places == ()`, `block_reason == "anchor"`. Passes on HEAD too; it guards the no-fair case.
3. `test_failed_anchor_streak_pulls_the_frozen_fair_sell_past_exit_age[recovery False|True]`: valid fair at 0, held YES 100, SELL `c0 0.56` accepted. Optional `Recovery` at 1 s. Rejected signals at 20 s and 44 s: `fair_ts_ns == 0`, no cancels. At 46 s: cancels `[("c0", "anchor")]` (normal) or `[("c0", "reprice")]` (recovery). After the acks: no SELL (normal) or SELL at the ask `0.52` (recovery). Valid signal at 50 s: `fair_ts_ns == 50 * NS`, `fair_valid`, `no_buy_reason == ""`, one SELL at `0.56`.

### 5.2 `tests/test_trader_session_core.py` (4 new items, 1 refactor)

1. Extract the seq 7367 setup of `test_mirror_strip_cancels_sell_c31_at_seq7367` (`:765-834`) into module helpers placed right above it: `_SEQ7367_SIGNAL`, `_seq7367_state()`, `_seq7367_books(*, orders, now_ns)`. The existing test calls them with `now_ns=4158096557584606` and keeps every assert. Code: `test_proto_session_anchor.py`.
2. Add `_signal_at(*, now_ns, anchor_p)` (delta 0.05) next to `_prime` (`:206`), and let `_prime` use it with `anchor_p=0.51`.
3. `test_stripped_book_anchor_failure_pulls_sell_c31_past_exit_age[44|46]` (STEP-009 integration), right after the seq 7367 test. The books are stripped of c23/c26/c29 and their mirror, `core_book_p == 0.42`. A rejected signal (anchor 0.38) 1 s before the wake. At age 44 s: keep `["c31"]`, cancel the 3 BUYs with `anchor`. At 46 s: keep `[]`, cancel the 3 BUYs and `c31`, all with `anchor`. Both cases: `fair_ts_ns == _SEQ7367_SIGNAL.source_received_ns`, no places.
4. `test_anchor_diag_logs_the_kept_fair_age`, after `test_position_mismatch_logs_core_residue_and_store` (it reuses `CapturedEvent`, `:1684`). The `anchor` rows are `(accepted, fair_ts_ns, age_s)` = `(True, 0, 0.0)` for the prime, `(False, 0, 10.0)` for the rejected signal at 10 s, and `(True, 20 * NS, 0.0)` for the valid one at 20 s. A cycle at 10.5 s with no new signal adds no row. Row 2 also has `fair ≈ 0.56`, `fair_valid True`, `anchor_p 0.60`, `signal_ts_ns 10 * NS`.
5. `test_restart_does_not_carry_the_old_fair_into_a_failed_anchor`: export and restore a primed core into a fresh `_core()`. The latch is `None`. A rejected signal gives `fair_valid False`, `fair_radiant 0.0`, `no_buy_reason "anchor"`. Passes on HEAD too.

`NS` is defined at `:2076`. The tests read it at run time, so the position is fine.

### 5.3 Red / green (measured)

- HEAD `35f8b6d1`, new tests only: 6 failed, 3 passed (`red_on_35f8b6d1.txt`). Failing: test 1, both cases of test 3, both cases of the c31 test, the diag test.
- With both patches: 9 passed.
- Full suite without goldens, with both patches and the new tests: `3209 passed, 72 warnings, 2 subtests passed in 30.51s`. That includes 1 plugin self-check, so the real expected count is 3199 + 9 = **3208**. No existing test needed a change.

## 6. Risks and limits

1. **Live behavior.** With a held position and a failed-anchor streak more than 45 s past the last valid fair, the frozen-fair SELL is canceled with `anchor` (normal) or repriced to the ask (recovery). Today it rests with no time bound. The longest live streak in the audit was 17.7 s, so no recorded session changes.
2. **Backtest goldens.** The backtest runs the same `step` (`src/backtest/strategy.py:79`). A backtest map with such a streak changes. Goldens are excluded by the user and are not run here. If a golden fails later, this fix is the expected cause.
3. **Old core traces.** The latch digest includes `fair_ts_ns` (`src/trader/core_trace_codec.py:230-236`, `:262-264`). A replay of a pre-fix trace with current code reports a `latch` digest mismatch at the first failed anchor after a valid fair. Replay at the trace's own commit stays exact. The Resolved Questions accept this.
4. **Reverted cycle.** If a cycle is reverted after a persist failure, its `anchor` line stays in the diag, and the rebuild after the revert logs a second line. Diag only; no state effect.
5. **Not changed:** `_reanchor_latch`, freshness limits (`exit_stale_s=45`), anchor tolerance, SELL floor, checkpoint schema, `diagnostics.toml`, B, backtest code, `poly-maker`.

## 7. Verification

Run from the worktree.

1. Red: add the tests from section 5 before the fix and run:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_strategy_anchor_age.py tests/test_trader_session_core.py -q --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Expect the 6 new failures listed in 5.3 and nothing else.
2. Green: apply sections 3-4 and run the same command, plus `tests/test_strategy_core.py tests/test_strategy_recovery_quotes.py tests/test_kill_gate.py tests/test_backtest_maker.py tests/test_core_trace.py`. All pass.
3. Full suite without goldens, required before the commit:
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Expect 3208 passed. STEP-009 saw `test_protective_events_on_bad_yes_book` fail under xdist on a subset (`PaperGateway requires paper=True`). If it fails, rerun it serially and report both results.
4. Typecheck: `uv run python -m basedpyright`. Expect 0 errors.
5. Lint: `uv run python -m pre_commit run --files src/strategy/quoting.py src/trader/session_core.py tests/test_strategy_anchor_age.py tests/test_trader_session_core.py`.
6. Do not run `tests/test_extraction_oracle.py` or `tests/test_follow300_replay.py`. No backtest, no VPS, no push.
7. Report: the red and green counts, the full-suite line, and `git diff --shortstat` against `35f8b6d1`.
