# Implementer report: STEP-003
Status: FINAL

## What landed

Existing `unsettled_buys` rows are proved and resolved. `LiveSources` delivers `BuySettled` until the core drops the order. Cancel-to-retirement logs `wait_ms`. No live insert, no `note_cancel` activation, no `_own_remaining` filter. Checkpoint schema stays 3. `feature.json` `passes` was not set.

- REST proof lives in `src/trader/unsettled_buy_recovery.py`. `REST_PROOF_MIN_AGE_S = 60` (line 23), `ORDER_LIFETIME_S = 7200` (line 24), `UNSETTLED_ALERT_AGE_S = 600` (line 25). `parse_terminal_buy_proof` (line 34) accepts an explicit BUY with a cancellation marker, `MATCHED`, or `size_matched` covering a positive `original_size`. `collect_rest_buy_proofs` (line 54) groups eligible rows by token, reads `get_trades` through `gateway._io`, sums that venue's maker `matched_amount`, and backfills only a structurally valid reply. A missing client, exception, non-list, or malformed trade proves nothing for that token. One lagging trades read can prove zero; that ceiling is the `ponytail` comment at line 21.
- `WalletHost._bind_order_terminal` (line 796) runs after `engine.start()`, next to `pin_engine_identity` and `bind_user_fill_address`, before `_boot_scan` (line 1071). `getattr(self.engine, "user", None)` leaves engines without a user stream unbound. `WalletUserStream._on_order` (engine_seams.py:150) calls `super()` then `on_order_terminal` with the same raw message. A missing open row is ignored and does not insert or emit `CancelUnsettled`.
- `_apply_unsettled_proofs` (wallet_host.py:824) proves and resolves inside the connection, then wakes. `_dispatch_fill` calls `_resolve_confirmed_buy` (line 813) before the `seq is None` return, so MATCHED to CONFIRMED and an already-acked replay both close a matching BUY. The ledger must already be `CONFIRMED`.
- `install_rest_fill_recovery` (engine_seams.py:472) now takes `reconcile_unsettled_buys`. Live positions try missed fills, then the sweep, then the original positions read and outbox drain. Either failure is logged and does not skip the rest. Paper skips both recovery calls.
- `LiveSources.settled_buys` is required (session_core.py:122). `_settled_buys` (line 923) queries only when an active BUY has a venue binding. `sync_inputs` appends `BuySettled` after budget and permissions, including `0.0`. It repeats every cycle until the order leaves active orders.
- `_observe_buy_lifetime` (line 682) logs `trader core buy gone id=%s` when a BUY becomes gone (line 695). It logs `trader core buy settled id=%s wait_ms=%s cause=%s` only when `BuySettled` removes the order (`cause=buy_settled`) or a full `Fill` removes a gone BUY (`cause=fill`). Clocks arm once on entering `already_canceling` or on a venue `CancelUnsettled` into gone. They are in `CoreMemory` and revert with a failed `apply_and_persist`. A restored order with no clock logs `wait_ms=unavailable origin=restored`.
- Alert key `unsettled_buy:{venue_id[:8]}`, message `venue {venue_id} session {session_id} reserve remains held` (wallet_host.py:873). Age must be strictly greater than 600. A row resolved on this pass is not alerted.

## Changed files

esports-trader:

- `src/trader/unsettled_buy_recovery.py` (new)
- `src/trader/wallet_host.py`
- `src/trader/engine_seams.py`
- `src/trader/session_core.py`
- `tests/test_trader_unsettled_buy_recovery.py` (new)
- `tests/test_trader_session_core.py`
- `tests/test_trader_live_execution.py`

betting_workspace:

- `tasks/unsettled-buy-reserve/progress.txt`

## Commits

- esports-trader `main` `c72792c9752b180068485618690b5886e1b38c36` — Resolve recorded unsettled BUYs and repeat BuySettled until the core drops them.
- betting_workspace `main` `adb5450aeb52a9729b7109c9546ec5423d3b8bb3` — Record STEP-003 progress for unsettled BUY recovery.

Parent of the code commit was `021fcc1043d7719326d54d00dc00d9d584ad21a4`. No push. `passes` remains false.

## Tests

From `esports-trader/`, basetemp `/tmp/pytest-impl3`. Goldens were not run.

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl3 -q tests/test_trader_unsettled_buy_recovery.py tests/test_trader_wallet_host.py tests/test_trader_engine_seams.py tests/test_trader_session_core.py tests/test_trader_live_execution.py tests/test_trader_trade_backfill.py tests/test_trader_core_persistence.py tests/test_trader_core_recovery.py tests/test_strategy_late_fills.py tests/test_core_trace.py
```

358 passed, 20 warnings, 2 subtests passed in 5.98s.

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl3 -q tests -k "not current_policy_smoke"
```

2561 passed, 80 warnings, 2 subtests passed in 22.47s.

```bash
uv run python -m basedpyright
```

0 errors, 0 warnings, 0 notes.

`git diff --check` was clean. Staged `make lint` failed once because ruff-format inserted a blank line in `tests/test_trader_unsettled_buy_recovery.py`. That line was re-staged and `make lint` then passed (ruff, format, basedpyright). The blank line does not change behavior, so the suite was not rerun after it.

## Limits

HTTP and WS still do not call `insert_unsettled_buy`. `note_cancel` does not emit `CancelUnsettled`. `_own_remaining` is unchanged. One trades read older than 60 seconds can prove zero. A checkpoint-restored cancel has no clock, so `wait_ms` is `unavailable`. No deploy.
