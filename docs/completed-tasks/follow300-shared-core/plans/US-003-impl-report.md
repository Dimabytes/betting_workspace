# US-003 implement report

Continued existing WIP on `ladder-experiment`. Did not start over. Did not start US-004+. Did not set `feature.json` `passes: true`. Did not recapture US-001 goldens. Did not push. Did not commit parquet dumps or betting_workspace.

## SHA

`7b2b65bd90c54bcb57179358b195eae0d54eac49`

Message: `feat: [US-003] - Wire the backtest adapter and prove extraction identity`

Branch: `ladder-experiment` (ahead of origin by 10). Parent was `95168c6`.

## Files committed (esports-trader only)

| File | Change |
| --- | --- |
| `src/backtest/strategy.py` | Adapter: `_drive` / `_sync_inputs` / `_execute_plan`; deleted Follow300 quoting/episode/latch from this module |
| `src/backtest/extraction_identity.py` | `first_tape_mismatch` skips `checks` |
| `src/strategy/engine.py` | Keep first-reason `Plan.cancels`; append extras not already listed |
| `src/strategy/lifecycle.py` | Complete a fill when rounded-to-2-decimals qty meets submitted size |
| `src/strategy/quoting.py` | First-reason stale/anchor snapshot; empty BUY targets use join-level reject reason; follow_book gating kept |
| `tests/test_extraction_oracle.py` | Four-map pytest; subprocess isolation for seed-0 maps |
| `tests/test_strategy_core.py` | `test_two_decimal_fill_sum_completes_the_rung` |

Not committed: `data/backtests/*_extraction-oracle/` parquet dumps. `poly-maker` untouched. `feature.json` US-003 still `passes: false`.

## Four-map identity (each in its own process)

Goldens frozen at capture SHA `ef680f2`. Gate is `first_tape_mismatch` (fills + place/cancel including ts, ids, episode, level, reserve). PnL reported, not the sole gate.

| Map | Result | Notes |
| --- | --- | --- |
| dota `8837869969` (6 fills) | **PASS** | Earlier SELL-early `place_cancel[218]` gone after cutoff/game-end mapping + no BUY-fill Wake |
| dota `8911784562` (14 fills) | **PASS** | Was `place_cancel[290]` keep-0.74 vs golden cancel. L0 not `done` after -60 full fill because `40.0+91.58 == 131.57999999999998 < 131.58` |
| dota `8933879286` (26 fills) | **PASS** | Was `wide_spread` vs golden `min_price` on leftover BUY cancel. Empty targets now use join-level `_join_reject_reason`, not the first latched-rung skip |
| lol `115564793879469302` (3 fills) | **PASS** | Was `exit_settle` vs golden `stale_signal` on `cancel_request`. First-reason snapshot: request=`stale_signal`, canceled/ack=`exit_settle` |

Command shape (zsh-quoted, one process each):

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest \
  python -m pytest 'tests/test_extraction_oracle.py::test_seed0_map_replay_matches_extraction_golden[dota-8837869969-6]' -q --tb=line
```

Never ran all four Nautilus kernels in one process.

Re-verified 2026-09-06 19:20 on `7b2b65b` with no further code changes: compact 126 passed; all four maps PASS in separate processes in the order 8837869969 → 8911784562 → 8933879286 → lol 115564793879469302. No second commit (working tree clean except untracked parquet dumps).

## Other pytest

```
pytest tests/test_extraction_oracle.py tests/test_backtest_maker.py tests/test_strategy_core.py -q -k "not test_seed0_map_replay"
→ 126 passed, 4 deselected
```

`tests/test_strategy_imports.py`: 1 passed.

Did not run full pytest. Did not run 12-seed baseline.

## ruff / basedpyright

On touched files (plus core quoting/lifecycle/engine):

- `ruff check`: all checks passed
- `ruff format`: clean
- `basedpyright`: 0 errors, 0 warnings

Pre-commit hooks passed on commit (ruff, format, basedpyright).

## `passes`

US-003 `passes: false` (left false on purpose). US-001 and US-002 remain `true`.

## Deviations

- `src/backtest/strategy.py` is 1216 lines. Plan target was well under 1000 if the delete was total. Adapter still has Nautilus I/O, telemetry, id maps, `_adopt_venue_into_core` order rebuild (needed by compact harness `set_only_live` / cancel-ack-before-replace tests).
- `_adopt_venue_into_core` still rebuilds `orders`/`rungs` live_ids from `_live`. Does not copy `winding_down` / `has_buy_fill` / episode fields from the shim into core.
- `quoting._price_rungs`: `needs_price = follow_book` only. Restoring US-002 `price==0` / always-`begin_episode` breaks `test_grid_entry_gates_stay_closed_between_ticks` and `test_episode_survives_a_late_buy_fill_and_then_starts_a_new_one`.
- Core `fair_ts_ns` is still `signal.received_ns` (US-002). Did not switch to original carried-ts; four goldens passed without it.
- `_join_reject_reason` does not include original `_cash_blocked_a_buy` → `no_cash`. Four maps did not need it.
- `_replay_seed0_identity_inprocess` is kept for the child process and ignored by pyright (`reportUnusedFunction`); parent `replay_seed0_identity` always subprocesses.
- `BUY_LEVEL_USDC = 100.0` stays hardcoded (`extraction_policy(level_usdc=100.0)`). US-007 owns four clips.
- Unused `QuoteTarget` / `LatchedDecision` in `maker_orders.py` left this step.
- Do not schedule Wake from `EngineOutput.next_wake_ns`.
