# s4-impl — STEP-004

Status: FINAL

## What changed

Commit `7b1b6990` on `esports-trader` `main` (amended from `624fb55f`): `Deliver pair merges to the core as a Merged event through the outbox.`

- `src/strategy/types.py`. `Merged(now_ns, token_index, qty)` is the last member of `InboundEvent`.
- `src/strategy/lifecycle.py`. `apply_merged` calls `_credit_sell_inventory`.
- `src/strategy/engine.py`. `_apply_session_event` applies `Merged`. `_apply_fill_path` includes it in the existing session or-pattern.
- `src/strategy/scheduling.py`. `note_schedule` treats `Merged` as dirtying, same as `Fill`. `should_evaluate` is unchanged.
- `src/trader/session_core.py` (1112 lines). `LiveCore.note_merge` enqueues `Merged`.
- `src/trader/core_persistence.py` (965 lines). `read_merge_leg` returns `MergeLeg(token_id, qty)` from the ledger `size`.
- `src/trader/core_session_io.py`. `consume_core_outbox` notes a `merged` row before `fill_for_key`, drains, advances `last_outbox_seq`, and persists the snapshot. An unknown row still stops the cursor.
- `src/trader/core_trace_codec.py` (859 lines). `_decode_merged` is registered. Encoding stays the generic `jsonable`.
- `tests/test_strategy_two_sided.py`. Both legs drop, bids and recovery flags stay, the dirty window opens, and an oversize qty clamps to 0.
- `tests/test_core_trace.py`. `Merged(1, 0, 20.0)` is in `_sample_events()`.
- `tests/test_trader_core_persistence.py` (977 lines). After `apply_merge` and consume, core qty matches the store, recovery flags stay, and the cursor sits on the last merged row. A second consume is a no-op.
- `src/trader/core_recovery.py`. `accept_if_proven` replays a lagging outbox before it adopts ledger inventory. `tests/test_trader_core_recovery.py` covers a failed second snapshot.

`passes` for STEP-004 in `feature.json` is still false. Progress appended to `tasks/two-sided-live-b/progress.txt`. No commit in the workspace repo. No push.

## Deviations

- The plan and the implement skill say set `passes: true`. The brief says the orchestrator sets it after review. Left false.
- No other deviation. The plan matched the code.

## Commands

- Step tests (`test_strategy_two_sided.py`, `test_core_trace.py`, `test_trader_core_persistence.py`, `test_trader_session_core.py`): 161 passed, 2 warnings, 2.75s.
- Regression list from the plan: 510 passed, 1 failed, 2 warnings, 2 subtests passed, 300.25s. The failure is `test_user_stream_is_bound_on_the_host_run_path` (`WalletHost` has no `_mode` at `wallet_host.py:1238`). That file is not in this diff. STEP-003 already reported it.
- `git diff --exit-code -- tests/test_strategy_core.py tests/test_follow300_replay.py`: empty.
- `uv run ruff check --select C901 src/strategy/engine.py src/trader/core_session_io.py`: passed.
- `wc -l`: `core_persistence.py` 965, `core_trace_codec.py` 859, `test_trader_core_persistence.py` 977. Each is under 1000.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the 11 staged files: ruff check, ruff format, basedpyright passed. The commit hook passed the same checks.
- `git status --short` after the commit: only the two untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs.
- `make test` was not run. The plan leaves the full serial suite to STEP-010.

## Commit

`7b1b699099d8c1514391f534ade0c6a8d8b699ed`

## Review fixes

Amended the step commit to `7b1b6990`. `passes` is still false.

### s4-rev

- **major — recovery can apply an absolute ledger baseline and then subtract a merge leg still sitting in the outbox.** Fixed. `RecoveryCoordinator.accept_if_proven` now replays pending outbox rows for the two tokens before it reads the ledger and enqueues `RecoveryVerified`. If the cursor does not reach the head, or there is no session to persist the replay, it returns false and does not adopt the baseline. After a successful replay it checks `proof_blocks` again and persists the recovered inventory with the new cursor. A consume exception still propagates, so the baseline is not applied; the live recovery task already logs that and retries. `test_recovery_does_not_reapply_undelivered_merge` fails the second snapshot, runs recovery, retries consume, and checks core and checkpoint quantities stay `(30, 20)`.

### s4-com

- No findings. Nothing to change.

### Checks after the fix

- `tests/test_strategy_two_sided.py`, `test_core_trace.py`, `test_trader_core_persistence.py`, `test_trader_session_core.py`, `test_trader_core_recovery.py`, `test_trader_unsettled_buy_activation.py`, `test_trader_match_lifecycle.py`: 234 passed, 2 warnings, 3.70s.
- `uv run ruff check --select C901 src/trader/core_recovery.py`: passed.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the two staged files: ruff check, ruff format, basedpyright passed. The amend hook passed the same checks.
- `git status` after the amend: only the two untracked `validation_*ts-regress-*` dirs.

## Open issues

- `test_user_stream_is_bound_on_the_host_run_path` still fails on HEAD. This step does not touch `wallet_host.py`.
- STEP-006 must set `core.last_outbox_seq` to the outbox head before registration. A clean core starts at 0, and registration replays old fills and merges.
