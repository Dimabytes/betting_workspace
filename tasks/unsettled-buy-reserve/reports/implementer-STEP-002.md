# Implementer report: STEP-002
Status: FINAL

## What landed

Durable BUY reserve, shared-budget inclusion, and a schema-3 checkpoint marker. No live insert, proof, or resolve call site.

- `unsettled_buys` is created next to `core_commands` (`src/trader/core_persistence.py:97`), with index `unsettled_buys_resolved_session` on `(resolved, session_id)` (`src/trader/core_persistence.py:108`).
- Row type `UnsettledBuy` (`src/trader/core_persistence.py:228`). Helpers do not commit: `insert_unsettled_buy` (`src/trader/core_persistence.py:807`), `prove_unsettled_buy` (`src/trader/core_persistence.py:825`), `resolve_settled_buys` (`src/trader/core_persistence.py:832`), `unsettled_buy_notional` (`src/trader/core_persistence.py:848`), `open_unsettled_buys` (`src/trader/core_persistence.py:858`), `resolved_unsettled_buys` (`src/trader/core_persistence.py:865`).
- Reserve is `max(0, qty - booked) * price`. Booked is BUY `MATCHED` and `CONFIRMED`. A row resolves when BUY `CONFIRMED` is at least `qty - HALF_SHARE_TICK`. `FAILED` and `SUPERSEDED` do not book. `resolve_settled_buys` aggregates the ledger once; EXPLAIN showed a per-row scan, so that shape was not kept. No new `fill_ledger` index.
- Account reserve adds `unsettled_buy_notional(conn, None)` (`src/trader/session_budget.py:48`). Map reserve adds `unsettled_buy_notional(conn, core.session_id)` (`src/trader/session_budget.py:63`).
- `UNSETTLED_CANCEL_REASON = "unsettled"` (`src/trader/core_persistence.py:30`). `snapshot_checkpoint` stores `gone` as `unknown` with that reason (`src/trader/core_persistence.py:413`) and does not mutate the in-memory order. `restore_orders` decodes that pair back to `gone` (`src/trader/core_persistence.py:348`). `_require_status` still rejects raw `gone` (`src/trader/core_persistence.py:342`). `CORE_SCHEMA_VERSION` stays 3.
- Rollback notes: `.shared-skills/vps-trader/SKILL.md:141`. Inspect `SELECT COUNT(*) FROM unsettled_buys WHERE resolved=0` on the live wallet before a separately authorized rollback.

Production search of `insert_unsettled_buy`, `prove_unsettled_buy`, and `resolve_settled_buys` finds definitions and tests only. `unsettled_buy_notional` is also called from `session_budget.py`.

## Changed files

esports-trader:

- `src/trader/core_persistence.py`
- `src/trader/session_budget.py`
- `tests/test_trader_core_persistence.py`
- `tests/test_trader_shared_budget.py`
- `tests/test_trader_core_recovery.py`

betting_workspace:

- `.shared-skills/vps-trader/SKILL.md`
- `tasks/unsettled-buy-reserve/feature.json` (`STEP-002` `passes` true; the uncommitted `STEP-001` `passes` true was in the same file)
- `tasks/unsettled-buy-reserve/progress.txt`

## Commits

- esports-trader `main` `9b2742b74093f51c6d8e0555c8a8cac9c1565783` — Hold a cancelled BUY's reserve until confirmed fills cover it.
- betting_workspace `main` `f81b9a7d4ba405314dfbd6c2e711afc6d440877e` — Document the unsettled BUY count to inspect before an old-code rollback.

Parent of the code commit was `fbcab230`. No push.

## Tests

From `esports-trader/`, basetemp `/tmp/pytest-impl2`. Goldens were not run.

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl2 -q tests/test_trader_core_persistence.py tests/test_trader_shared_budget.py tests/test_trader_core_recovery.py
```

59 passed.

```bash
PYTEST_N=10 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest --basetemp=/tmp/pytest-impl2 -q tests -k "not current_policy_smoke"
```

2532 passed, 80 warnings, 2 subtests passed in 22.54s.

```bash
uv run python -m basedpyright
```

0 errors. `make lint` on the staged esports-trader paths passed (ruff, format, basedpyright). The suite ran before a type annotation on the test helper `conn`; that annotation does not change behavior and was in the linted commit.

## Limits

Live HTTP/WS row creation, `BuySettled` delivery, `wait_ms`, and the incident note stay in later steps. No deploy.
