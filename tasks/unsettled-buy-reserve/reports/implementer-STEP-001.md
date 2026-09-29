# STEP-001 implementer report
Status: FINAL

## What landed

A BUY that is terminal at the venue stays `gone` on its rung until credited fills catch the proof. Live producers are not wired.

- `OrderStatus` includes `gone` (`src/strategy/types.py:7`). `CancelUnsettled` and `BuySettled` are frozen events and members of `InboundEvent` (`src/strategy/types.py:405`, `src/strategy/types.py:411`, `src/strategy/types.py:466`).
- `apply_cancel_unsettled` turns a found BUY into `gone` through `_replace_order` and leaves missing, SELL, and already-gone orders unchanged (`src/strategy/lifecycle.py:349`).
- `apply_buy_settled` retires a live, canceling, unknown, or gone BUY when `filled_qty` is not below `matched_qty - HALF_SHARE_TICK`, then drops the active order, frees the rung, and ends an idle episode (`src/strategy/lifecycle.py:356`). Pending BUYs are unchanged (`src/strategy/lifecycle.py:360`).
- `gone` stays an active BUY (`src/strategy/lifecycle.py:205`), is skipped by `mark_canceling`, `apply_cancel_timeout`, and `_mark_restored` (`src/strategy/lifecycle.py:304`, `src/strategy/lifecycle.py:370`, `src/strategy/lifecycle.py:376`), and is still outside `has_unresolved_orders` (`src/strategy/lifecycle.py:395`).
- Quoting rejects `gone` before price matching (`src/strategy/quoting.py:534`). Kernel reserve skips it (`src/strategy/budget.py:19`). Entry-stale wake ignores it (`src/strategy/scheduling.py:84`). Both events evaluate immediately (`src/strategy/scheduling.py:133`, `src/strategy/engine.py:98`).
- Trace decoders require the exact fields and are registered by class name (`src/trader/core_trace_codec.py:620`, `src/trader/core_trace_codec.py:628`, `src/trader/core_trace_codec.py:731`).
- `HALF_SHARE_TICK = 0.005` moved to `shared.utils.trading` (`src/shared/utils/trading.py:16`). `wallet_store` imports it (`src/trader/wallet_store.py:17`), so `engine_seams` still imports the name from `wallet_store`.

## Changed files

Commit `54b7ce50` on `esports-trader` `main` (`Hold a venue-terminal BUY on its rung until the credited fill matches the proof.`):

- `src/strategy/types.py`
- `src/strategy/lifecycle.py`
- `src/strategy/budget.py`
- `src/strategy/quoting.py`
- `src/strategy/engine.py`
- `src/strategy/scheduling.py`
- `src/trader/core_trace_codec.py`
- `src/trader/wallet_store.py`
- `src/shared/utils/trading.py`
- `tests/test_strategy_late_fills.py`
- `tests/test_strategy_budget.py`
- `tests/test_strategy_scheduling.py`
- `tests/test_core_trace.py`

`feature.json` STEP-001 `passes` is true. No `poly-maker` edits, no schema bump, no live insert of `unsettled_buys`.

## Tests

Commands from `esports-trader`, with `PYTHONPATH=src:scripts:../prediction-market-backtesting` and `uv run --group backtest --with pytest-xdist python -m pytest -n 6 --dist worksteal -q`.

1. Focused kernel and codec: `tests/test_strategy_late_fills.py tests/test_strategy_budget.py tests/test_strategy_scheduling.py tests/test_strategy_core.py tests/test_core_trace.py` — 137 passed.
2. Adapter, oracle, and seed-0 smokes: `tests/test_adapter_contract.py tests/test_extraction_oracle.py tests/test_follow300_replay.py` — 16 passed, 19 failed. Oracle cases passed. Fourteen adapter failures are `state.budget.account_cap_room_usdc inf != 1000000.0` after `TapeWake(now_ns=0)`. Five smokes mismatch the golden tape and report `inputs changed: strategy_constants_sha256`.
3. Full suite except the smoke name: `tests -k "not current_policy_smoke"` — 2493 passed, 2 subtests passed, 15 failed. The 15 are the same 14 adapter failures plus `tests/test_backtest_maker.py::test_shared_position_cap_stops_refills` (`assert 0 < 0` at `tests/test_backtest_maker.py:2355`).

Those 15 failures reproduce on `22f53724` (HEAD before this commit): `test_ladder_open` and `test_shared_position_cap_stops_refills` fail with the same assertions. `src/shared/constants/strategy.py` is not in `54b7ce50`, so the smoke hash drift is not from this step. Goldens were not edited.

`uv run python -m basedpyright`: 0 errors. Staged `make lint` passed, and the commit hook passed the same checks.

## Handoff for STEP-002

Checkpoint restore still rejects `gone` (`src/trader/core_persistence.py:314`). Do not produce live `CancelUnsettled` until the durable row exists and the checkpoint maps `gone` back. Kernel reserve is already zero for `gone` (`src/strategy/budget.py:19`), so the table has to carry the money.
