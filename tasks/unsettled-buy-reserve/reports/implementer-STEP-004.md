# Implementer report: STEP-004
Status: FINAL

## What landed

A venue-terminal BUY now opens an `unsettled_buys` row before the core marks it `gone`. `note_cancel` emits `CancelUnsettled` for an owned BUY, `CancelAck` for a SELL, and `CancelTimeout` when the cancel is false. `_own_remaining` skips `gone`, so the bid stays in the book. Checkpoint schema stays 3. `feature.json` `passes` was not set.

- `find_owned_order` (`src/trader/session_core.py:476`) returns the active order, then the retained record. `note_cancel` (`session_core.py:621`) warns on an unmapped venue (`session_core.py:625`), ignores a mapped id with no order (`session_core.py:628`), and routes false / BUY / SELL at `session_core.py:630`, `session_core.py:633`, and `session_core.py:636`.
- `_own_remaining` skips `gone` at `session_core.py:254`. Acceptance time, token, side, and leftover rules are unchanged.
- `get_unsettled_buy` (`src/trader/core_persistence.py:858`) reads one row, including a resolved one.
- `parse_buy_cancellation` (`src/trader/unsettled_buy_recovery.py:39`) requires BUY, a nonempty string id, `type=CANCELLATION`, and `status=CANCELED`. It ignores `size_matched`. `CANCELLED`, `MATCHED`, and a full `UPDATE` do not create a row.
- HTTP: `_owned_buy_cancels` (`src/trader/wallet_host.py:473`) captures submitted qty, price, token, and session before the await. `_commit_cancel_reserves` (`wallet_host.py:495`) writes outcomes and, on success, the rows in one `with conn`. `_durable_cancel` (`wallet_host.py:522`) still notifies the worker when the command list is empty. A false result persists `timeout` and inserts nothing. After the commit, `note_cancel_result` runs (`wallet_host.py:540`), then `_apply_unsettled_proofs(())` (`wallet_host.py:544`) so an already-CONFIRMED row can close. An insert failure rolls the outcome back and does not notify.
- WS: `_on_order_terminal` (`wallet_host.py:865`) returns immediately when the row is resolved. A missing row is created only for a cancellation, via `_open_cancelled_buy` (`wallet_host.py:890`), which requires a core BUY. Proof is applied only while that stored row is still open and unproven. An existing open row keeps the STEP-003 proof path, then `_deliver_cancelled_buy` (`wallet_host.py:922`) sends `note_cancel_result` when the BUY is still owned. `WalletUserStream._on_order` still removes the fork order before the host callback.

## Changed files

esports-trader:

- `src/trader/session_core.py`
- `src/trader/core_persistence.py`
- `src/trader/unsettled_buy_recovery.py`
- `src/trader/wallet_host.py`
- `tests/test_trader_unsettled_buy_activation.py` (new)
- `tests/test_trader_wallet_host.py`
- `tests/test_trader_session_core.py`
- `tests/test_trader_unsettled_buy_recovery.py`
- `tests/test_adapter_contract.py`
- `tests/adapter_contract_fixtures.py`

betting_workspace:

- `.shared-skills/vps-trader/SKILL.md` (false-alarm bullet at line 124; manual `qty=0` update at line 158)
- `.learnings/unsettled-buy-reserve-20260929.md` (new)
- `tasks/unsettled-buy-reserve/progress.txt`

## Commits

- esports-trader `main` `f6bcd785472934dab76c57156acb07b6e41f39df` — Insert an unsettled BUY when a cancel lands, and keep that bid out of the book.
- betting_workspace `main` `85165fee11a338969389704bf1aa4858326e31e8` — Document unsettled BUY triage and the grid-3008273 reserve incident.

Parent of the code commit was `c38dfa067e8fd1b4ea86b738ff0146f3a3dfafd5`. No push. `passes` remains false.

## Tests

From `esports-trader/`, basetemp `/tmp/pytest-impl4`. Goldens were not run. This step does not change replay, backtest `CancelAck`, or policy.

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl4 -q tests -k "not current_policy_smoke"
```

2591 passed, 80 warnings, 2 subtests passed in 23.22s.

That run was the production code that was committed. Ruff then reformatted tests only (`RUF005` unpacking and format). Those files were rerun:

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl4 -q tests/test_trader_session_core.py::test_cancel_ok_buy_goes_gone_and_sell_acks tests/test_trader_unsettled_buy_activation.py tests/test_trader_wallet_host.py::test_durable_cancel_keeps_ws_proof_and_survives_a_detached_core tests/test_adapter_contract.py
```

38 passed, 20 warnings in 4.56s.

```bash
uv run python -m basedpyright
```

0 errors, 0 warnings, 0 notes.

`git diff --check` was clean. `make lint` (staged pre-commit: ruff, format, basedpyright) passed, and the commit hook passed again.

## Limits

`sqlite3.Connection.commit` is read-only, so the atomicity test opens a second connection inside `note_cancel_result` and reads the canceled outcome and the row there, before the real core event. A successful live BUY cancel diverges from backtest `CancelAck` (rung held, no replacement). `tests/test_adapter_contract.py:47` stops parity at that row and checks each side. The live driver does not emit `CancelAck`. No deploy, no measured post-deploy latency, and no part-A readiness claim. The 41–46 ms WS-before-HTTP figure in the learning note is the 2026-09-29 journal comparison.
