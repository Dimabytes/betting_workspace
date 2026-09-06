# US-003 review-fix report

Parent: `7b2b65b`. Fix SHA: `5c658f732550effb9c5f6e024b35c2476968a3bd` (`5c658f7`).
Message: `fix: [US-003] - address review findings`
Branch: `ladder-experiment` (ahead of origin by 11). Did not push. Did not recapture goldens. Did not set `feature.json` `passes: true`. Did not start US-004. poly-maker untouched.

## Fixed

| ID | What |
| --- | --- |
| F2 | `cancel_reason` is first-write-wins. `RestingOrder.ack_reason` is last. Deleted `_opened_cancels`, `first_cancels`, `session_cancels`, engine first_ids dedupe. `Plan.cancels` is the step-entry diff of `canceling` ids using `cancel_reason`. Shim copies remapped `ack_reason` onto `live.cancel_reason`. |
| F3 | `apply_fill` uses `share_floor` both sides. Kept `test_two_decimal_fill_sum_completes_the_rung`. |
| F4 partial | `_HELD_NO_TARGET_REASONS: frozenset[BlockReason]`. Adapter remap kept. |
| F5 | `load_run_selection(*, game, match_id, limit)`; `model_path` and `lag_seconds` on `RunSelection`. Identity loader no longer forks the CLI path. |
| F6 partial | `episode_token_index is None` instead of `or 0`. |
| F7 | Deleted `_can_afford` and `test_can_afford_rejects_an_off_grid_price`. |
| F8 | `SignalUpdate.signal` may be `None`. `_clear_signal` sends `signal=None`. Dropped `_last_model_ts_ns`. |
| F9 | `BookUpdate.books` may be `None`. `_sync_inputs` always `_drive(BookUpdate(...))`. |
| F1 partial | `_shim_from_core` skipped for Clock/Book/Signal/Budget. Fill/Accept/Reject/Wake/CancelAck still shim. |
| NIT | `warnings.warn` for ungated PnL; assert `len(actual.fills) == fill_count`; `write_identity_json` (alias `write_identity_golden` kept); `__all__` for `_replay_seed0_identity_inprocess`. |

## Skipped

| ID | Why |
| --- | --- |
| F1 full | Plan: do not rewrite `test_backtest_maker.py` wholesale. `_adopt_venue_into_core` / `_ensure_core_order_for_fill` still carry `set_only_live` pokes into the core. Deleting them without a core-seed helper would break compact harness tests. |
| F4 full | Do not pull `exit_settle`/`no_quote` into core `BlockReason`. F2 already splits first vs last; adapter remap stays the telemetry owner. Core `held_reason` still picks which gate string to write. |
| F6 full | Synthetic `_latched.buy_targets` stays. Dropping it would rewrite ~20 compact assertions this step was told not to wholesale-rewrite. |
| F10 | Mechanical 220-line telemetry extract. Not a decision bug. 1k-line target was listed as a miss, not a correctness blocker. |
| NIT `_drive(execute=)` | Reviewer said unused. Wrong: `on_order_canceled` calls `_drive(..., execute=False)` then detach then `_execute_plan`. |
| NIT `_join_reject_reason` comment | AGENTS.md: no narrating comments. |

## Checks

- Compact: `pytest tests/test_extraction_oracle.py tests/test_backtest_maker.py tests/test_strategy_core.py -q -k "not test_seed0_map_replay"` → **125 passed**, 4 deselected (was 126; deleted `_can_afford` test).
- Identity (subprocess isolation, goldens frozen at `ef680f2`): dota `8837869969`, `8911784562`, `8933879286`, lol `115564793879469302` all **PASS**.
- `ruff check` + `basedpyright` on touched files: **0 errors**.
- Pre-commit (ruff format/check, basedpyright): passed on `5c658f7`.
