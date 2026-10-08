# s6-impl — STEP-006
Status: FINAL

## What changed

Commit `77a85364` on `esports-trader` `main` (`77a85364db2a82bee4ced7e5a3d3a26a64d72b8c`): `Run two-sided maps in a TwoSidedWorker: book fair, clean restart, final merge.`

- `src/trader/two_sided_worker.py` (153 lines). `TwoSidedWorker` quotes YES fair from the book inside the feed window, skips the model and the history-gap drop, opens a clean `TwoSidedPolicy` core with `trace=None` and `last_outbox_seq` at the outbox head, and puts `PairMerger` in the `_dust` slot. `drop_sell` refuses every SELL.
- `src/trader/match_worker.py` (1391 lines). Extracted `_core_limits`, `_core_freshness`, `_adopt_core`, `_write_decision`, and `_open_trace`. The `_dust` slot is `DustSweeper | PairMerger`. Follow300 behavior is unchanged.
- `src/strategy/policy.py`. `TwoSidedPolicy` gained `level_usdc` and `sell_min_life_s`. `two_sided_policy` takes `level_usdc` and bakes `sell_min_life_s=0.0`.
- `src/trader/session_core.py` (1113 lines). `LiveCore.policy` is `Policy`. `"band"` maps to `EntryBlock.BAND`.
- `src/trader/session_types.py`. `SignalReason.BOOK` and `EntryBlock.BAND`.
- `src/trader/dust_sweep.py`. `_blocked` asserts `Follow300Policy` before `is_settling`.
- `src/trader/pair_merge.py`. No-op `sweep` so `_quiesce` can call the slot.
- `tests/test_trader_two_sided_worker.py` (533 lines). Book window, history gap, chain restart, late pre-restart fill, final merge order, and the unbooked-merge retry.
- `tests/trader_session_fixtures.py`. `build_attached_worker` takes `worker_class` and `yes_is_radiant`, both defaulted.
- `tests/test_strategy_two_sided.py`. Factory calls pass `level_usdc=20.0` and assert `sell_min_life_s == 0.0`.

`passes` for STEP-006 in `feature.json` is still false. Progress appended to `tasks/two-sided-live-b/progress.txt`. No commit in the workspace repo. No push. No network, no `*_B` keys. `compose.yaml`, `config_b/trading.toml`, and `src/dashboard/summarize.py` were not edited. The two untracked `data/backtests/dota_maker/validation_*ts-regress-*` dirs were not staged.

## Deviations

- The plan's `_finish_final` catches every exception and still snapshots, zeros, and unregisters. Real `merge_all` (`ec9ebe90`) raises `MergeAccountingError(tx_hash, qty)` while a mined merge is unbooked. A later `merge_all` retries `apply_merge` only and does not submit again. The worker retries with backoff 1s, 2s, 4s, then 8s capped, alerts `trader final merge unbooked`, and does not call `end_snapshot`, `zero_token_sizes`, or `unregister_worker` until `merge_all` returns. The hold stays inside `PairMerger`. `CancelledError` propagates. Any other exception is logged and finalization continues, which is the plan's sqlite path now that booking failures are `MergeAccountingError`.
- The plan's mid-merge cancel test expects the in-flight merge to book nothing. `cancel()` already only blocks a new submit. That contract is `test_cancel_does_not_drop_the_active_merge`. The new test was not added.
- `release_merge` is already in the `finally` of `_merge_pairs`. No wrap was added.
- The plan and the implement skill say set `passes: true`. The brief says the orchestrator sets it after review. Left false.

## Commands

`PYTEST_N` was unset for these runs. Serial pytest.

- Step tests (`test_trader_two_sided_worker.py` 20, `test_trader_pair_merge.py`, `test_trader_ctf_merge.py`, `test_strategy_two_sided.py`): 116 passed, 1 warning, 3.15s.
- Existing match-worker neighbors (`test_trader_match_lifecycle.py`, `test_board_signal_contract.py`, `test_trader_wallet_sidecar.py`, `test_lol_prior.py`, `test_trader_dust_sweep.py`, `test_core_trace.py`, `test_trader_session_quoting.py`, `test_trader_unsettled_buy_activation.py`, `test_trader_wallet_host.py`, `test_trader_live_execution.py`): 280 passed, 2 subtests passed, 3 warnings, 8.29s.
- LiveCore / budget / persistence / Follow300 replay: 360 passed, 3 warnings, 294.69s.
- `python -m backtest.two_sided`: `two_sided ok`.
- `git diff --exit-code` on `tests/test_strategy_core.py`, `tests/test_follow300_replay.py`, `tests/fixtures`, `tests/test_trader_match_lifecycle.py`, `tests/test_trader_dust_sweep.py`, `tests/test_core_trace.py`: empty. `git status --short tests/fixtures`: empty.
- Comment grep on `two_sided_worker.py` and `test_trader_two_sided_worker.py`, excluding `# pyright:`: no matches.
- `uv run ruff check --select C901` on `two_sided_worker.py`, `match_worker.py`, `dust_sweep.py`, `pair_merge.py`: passed.
- Sizes: `match_worker.py` 1391, `session_core.py` 1113, `two_sided_worker.py` 153, `test_trader_two_sided_worker.py` 533.
- `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes.
- `make lint` on the 10 staged files: ruff check, ruff format, basedpyright passed. The commit hook passed the same checks.

## Commit

`77a85364db2a82bee4ced7e5a3d3a26a64d72b8c` on `main`. Not pushed.

## Open issues

- A permanent `apply_merge` failure during the final merge retries until the process dies. `stop_grace` is 240s, so SIGKILL can land first. The hold and the pending receipt are process memory and die with it. Nothing retries them after that.
- Nothing in production constructs `TwoSidedWorker` yet. STEP-007 does.
- A late fill of a pre-restart order pulls both bids for the rest of that map (`ownership_unresolved`). Accepted fail-safe. The test documents it.

## Review fixes

Commit `5ed8a8a3` (`5ed8a8a3c98116c68b562e07b9b5d570fab3c87a`) on `main`, on top of `f605a6b9`. Not an amend. Not pushed.

- **s6-rev major, clean restart reuses order ids.** Fixed. `open_core` reads `next_order_seq` and every `cN` id in the checkpoint orders, checkpoint records, bindings, and commands, then sets the clean core's counter to that floor before `_adopt_core`. A bad checkpoint (`CoreSchemaError`) is ignored and the bindings and commands are still scanned. Inventory and resting orders stay unrestored. `test_clean_restart_keeps_old_order_prices_and_mints_new_ids` seeds a checkpoint counter of 2 plus commands and bindings for `c0`, `c1`, and `c7`, restarts, and checks the new core and the recovery checkpoint both start at 8, the new quotes are `c8` and `c9`, the old command prices stay 0.28 / 0.66 / 0.11, the new command prices match the submitted quotes, and the old venue bindings are not replaced.
- **s6-rev major, a submitted final merge can outlive the worker.** Fixed. `TwoSidedWorker._quiesce` waits on `PairMerger.wait_inflight` when `_quiesced` is already set, so `run`'s shielded second call drains a merge that the unshielded final path was cancelled out of. It does not call `merge_all` again and it does not snapshot, zero, or unregister on that path. `test_cancel_waits_for_the_submitted_merge_before_shutdown` uses a real `PairMerger` and a gated adapter. Cancelling during a threshold merge books 150/120 down to 30/0 before shutdown observes the store. Cancelling during the final merge books 12/10 down to 2/0, and the worker stays registered.
- **s6-rev minor, `dict[str, Any]` holders and anonymous tuples.** Fixed. The worker tests now use frozen dataclasses (`_Attached`, `_Restart`, `_FinalWatch`, `_FinishedNote`, `_Backoff`, `_CancelCase`, `_ShutdownMark`). Merger callbacks are installed after the watch exists. The retry counter is `nonlocal`.
- **s6-com, `session_core.py` module docstring.** Fixed. Deleted.
- **s6-com, `LiveCore` class docstring.** Fixed. Deleted.
- **s6-com skips.** Left. The `# pyright:` pragmas and the `SignalReason` docstring stay. `MatchWorker.handle_event` / `_on_event` docstrings and the fixture line "One MatchWorker" were out of scope for that review.

`PYTEST_N` was unset. `tests/test_trader_two_sided_worker.py`, `tests/test_trader_match_lifecycle.py`, and `tests/test_follow300_replay.py`: 70 passed, 3 warnings, 310.34s. `uv run python -m basedpyright`: 0 errors, 0 warnings, 0 notes. `make lint` on the 4 staged files passed (ruff check, ruff format, basedpyright). The commit hook passed the same checks. `passes` for STEP-006 is still false.
