# STEP-011 plan: RISK-03 read the collateral balance by its 6-decimal contract

Code: worktree `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-live-a-audit-fixes`, branch `live-a-audit-fixes`, base HEAD `2f4629ab`. Paths are relative to that worktree unless they start with `../poly-maker/`, `runs/` or `orchestrate/`.

Evidence: `orchestrate/work/plan-step-011/`. Every probe ran on `2f4629ab` with the worktree untouched (`git status --short` empty after every run).
- `probe_units.py`: fork heuristic vs the existing contract parser, value by value.
- `probe_budget.py`: the live seam plus BUY sizing, before and after.
- `planned/src/`, `planned/tests/`: the full planned files. `planned_src.diff` and `planned_tests.diff` are the exact diffs against `2f4629ab`.
- `test_proto_*.py`, `fix_plugin.py`: the first red/green harness (in-memory patch).
- `red_on_2f4629ab.txt`, `suite_planned_src.txt`, `release_regression_planned.txt`: measured results.
- `pyrightconfig.json`: strict basedpyright over the planned files and tests. Result: 0 errors.

## 0. Result

Live A reads its USDC balance through the frozen fork's `ExecutionGateway.collateral_balance`, which divides by 1e6 only when the value is above 1e6. The fix replaces that method on the live gateway, inside the existing `install_strict_rest` seam, with the parser the dashboard already uses (`parse_collateral_usdc`). The parser moves from `src/dashboard/balance.py` to `src/shared/utils/trading.py`, unchanged. A bad reply gives 0.0 and BUY stops with `no_cash`. One diag row `collateral` records the raw reply and the USDC value.

Size: src +36/−21 over 4 files (17 lines are the move). Tests +103/−23 over 3 files: the parser test moves to `tests/test_trading.py` with new boundary cases, and one 7-case integration test goes into `tests/test_trader_shared_budget.py`. No config, schema, checkpoint, backtest, B feature, or `poly-maker` change.

## 1. What is broken today (evidence)

1. `../poly-maker/src/polymaker/execution/gateway.py:399-409`: `v = float(ba[k]); return v / 1e6 if v > 1e6 else v` over the keys `balance`, `collateral`, `amount`. A non-dict reply gives 0.0.
2. Live callers in esports-trader:
   - `install_collateral_snapshot` (`src/trader/engine_seams.py:464-476`) writes `CollateralCache.value` on every REST `positions()` read: once at `engine.start()` (`../poly-maker/src/polymaker/engine.py:246`) and every reconcile (`config/trading.toml:10`, `reconcile_interval_s = 20.0`).
   - `PairMerger._refresh_collateral` (`src/trader/pair_merge.py:281-292`, B merges) writes the same cache when the value is above 0.
   - The cache is the `cash_usdc` of every core's budget (`src/trader/session_budget.py:23-40`).
3. `probe_units.py` on HEAD (fork value → contract value):

   | raw `balance` | fork | contract |
   | --- | --- | --- |
   | `"0"` | 0.0 | 0.0 |
   | `"1"` | 1.0 | 0.000001 |
   | `"500000"` | 500000.0 | 0.5 |
   | `"999999"` | 999999.0 | 0.999999 |
   | `"1000000"` | 1000000.0 | 1.0 |
   | `"1000001"` | 1.000001 | 1.000001 |
   | `"5000123456"` | 5000.123456 | 5000.123456 |
   | `"1.5"` | 1.5 | ValueError (malformed) |
   | `"nan"` / `"inf"` | nan / inf | ValueError (malformed) |
   | `{"collateral": "500000"}` | 500000.0 | ValueError (missing) |

4. `probe_budget.py` (fork seam + `test_trader_shared_budget` Dota core): `"500000"` gives cache 500000.0 and 3 BUY rungs; `"inf"` gives cache `inf` and 3 BUY rungs. With the fix both give 0 BUYs and `last_block == "no_cash"`. `"1000000000"` gives 1000.0 and 3 rungs before and after.
5. Live impact: none recorded. The wallet held about $5k, trace `cash_usdc` minimum 3572.99 (`runs/live-a-money-audit-20261009/reports/a2-dev-guards.md:75`). The venue rejects unfunded posts.

## 2. Contract of the installed client and API

1. `py-clob-client-v2==1.0.2` (`uv.lock:2358-2359`). `ClobClient.get_balance_allowance` (`.venv/lib/python3.13/site-packages/py_clob_client_v2/client.py:687-695`) returns `helpers.request` output as is: `resp.json()`, or `resp.text` when a 200 body is not JSON (`py_clob_client_v2/http_helpers/helpers.py:80-84`). A non-200 raises; the fork `balance_allowance` catches it and returns `{}` (`../poly-maker/src/polymaker/execution/gateway.py:500-517`).
2. `COLLATERAL_TOKEN_DECIMALS = 6` (`py_clob_client_v2/constants.py:35`, `config.py:3`).
3. API reference for `GET /balance-allowance`: `balance` is a string, "Balance amount in fixed-math with 6 decimals" (polytest.io mirror of the CLOB API reference). The CLOB V2 client guide (agentbets.ai) reads it as `int(result["balance"]) / 1e6`. The docs.polymarket.com page shows `"balance": "1000.00"` as a placeholder example. It contradicts the field definition, and the Rust client types it as `Decimal`, which does not settle units.
4. This repo already depends on that contract: `parse_collateral_usdc` (`src/dashboard/balance.py:264-278`, commit `782c0056`, 2026-10-06) reads `balance` as integer base units and rejects decimal strings. `tests/test_dashboard_live_hub.py:472-490` pins 0, 1, 500000, 1000000.
5. No raw live reply is in the audit evidence (`zgrep` for `balance-allowance` and `"balance"` in `evidence/live-docker.log.gz` and `evidence/engine-journal-oct5-9.jsonl.gz`: no rows). STEP-001 `http` diag records the raw body of every CLOB call except `/auth/` (`src/trader/diag.py:287-330`), so the first live run records it.

## 3. Design decisions

1. **Reuse the parser, move it to `shared`.** `trader` never imports `dashboard`; `dashboard` imports `trader` (`src/dashboard/catalog.py:13`, `src/dashboard/live_hub.py:64-65`, and `dashboard/balance.py` → `dashboard/wallet.py:6` → `trader.core_persistence`). `src/shared` is the parser layer (`AGENTS.md`: "src/shared — paths, parsers, types"). `src/shared/utils/trading.py` already holds the Polymarket unit primitives (`share_floor`, `HALF_SHARE_TICK`) and has no heavy imports. The function body stays byte for byte.
2. **Fix once where every live caller routes.** `install_strict_rest` (`src/trader/engine_seams.py:442-461`) replaces `gateway.collateral_balance`. Both live callers look the attribute up at call time, so `install_collateral_snapshot` (installed one line earlier, `src/trader/wallet_host.py:807-808`) and `PairMerger._refresh_collateral` both get the contract value. The seam already skips paper (`engine_seams.py:444-445`), and its docstring is "Make live REST reads conservative". Seams install in `WalletHost.__init__` (`wallet_host.py:739`), before `engine.start()` (`wallet_host.py:1291`) makes the first `positions()` call.
   - Rejected: a call to a new helper at the two call sites. It needs a paper branch in `install_collateral_snapshot`, because that seam also runs for paper.
3. **A bad reply gives 0.0.** The fork gives 0.0 for `{}` and unparsable values today. With the fix, nonfinite, decimal, negative, missing, and non-dict replies give 0.0 too. `install_collateral_snapshot` then stores 0.0 and BUY stops (`no_cash`). `PairMerger` keeps the previous value (`balance > 0.0`). A raw value never becomes buying power. `logger.warning("trader collateral unreadable: %s", exc)` names the reason in the docker log.
4. **Diag row `collateral`.** Fields: `response` (the raw object) and `usdc`. The whole response is logged, not `response.get("balance")`, because the client can return a `str` body (item 2.1). `log_event` is a no-op unless live A diagnostics are on (`src/trader/diag.py:120-123`). Volume: one row per `positions()` read, about 1 per 20 s. The `http` row of the same call also has the raw body.
5. **Dropped fork behavior.** The `collateral` and `amount` fallback keys and float strings. The contract has only an integer `balance` string (`AGENTS.md`: no branches for inputs that cannot occur).
6. **B.** B maps run in the same `WalletHost`, and `PairMerger` uses `self._host.engine.gateway`. For raw above 1e6 the value is identical: `float(v) / 1e6` and `int / 1_000_000` are both correctly rounded for integers below 2^53 (table rows `"1000001"`, `"5000123456"`). Only raw at or below 1e6, nonfinite, and malformed replies change. No B feature change.
7. **Unchanged:** `poly-maker` (frozen), `PaperGateway.collateral_balance` (paper is dead, `AGENTS.md`), `CollateralCache`, `budget_from_orders`, `map_budget`, config, schema. `../poly-maker/src/polymaker/moneydoctor.py:71,121` and `doctor.py:174-184` keep the heuristic; they are offline CLIs, not live paths.
8. **No comments, no new docstrings** in new code (review config). The moved function keeps its current form (it has no docstring).

## 4. Code changes

Exact diff: `orchestrate/work/plan-step-011/planned_src.diff`. ruff check and ruff format pass on all four files.

### 4.1 `src/shared/utils/trading.py`

1. Docstring line 1: `"""Polymarket fee primitives, collateral units, and the 0.01 quote-grid snap."""`.
2. Add `from collections.abc import Mapping` and `from typing import cast` after `import math`.
3. Add `BASE_UNITS_PER_USDC = 1_000_000` after `REBATE_RATE = 0.15`.
4. Append `parse_collateral_usdc` at the end of the file, copied unchanged from `src/dashboard/balance.py:264-278`.

### 4.2 `src/dashboard/balance.py`

1. Delete `BASE_UNITS_PER_USDC = 1_000_000` (`:15`) and `parse_collateral_usdc` (`:264-278`).
2. Import line 2 becomes `from collections.abc import Callable` (`Mapping` is no longer used; `cast` stays, `:291`).

### 4.3 `src/dashboard/collateral.py`

Line 10 becomes two lines:

```python
from dashboard.balance import BalanceAccount, BalanceRead
from shared.utils.trading import parse_collateral_usdc
```

### 4.4 `src/trader/engine_seams.py`

1. Add `from shared.utils.trading import parse_collateral_usdc` after `from shared.utils.log import get_logger` (`:34`).
2. In `install_strict_rest`, after `open_orders` (`:456-457`), and one assignment after `gateway.open_orders = open_orders` (`:461`):

```python
    async def collateral_balance() -> float:
        response = await gateway.balance_allowance()
        try:
            usdc = parse_collateral_usdc(response)
        except ValueError as exc:
            logger.warning("trader collateral unreadable: %s", exc)
            usdc = 0.0
        log_event("collateral", response=response, usdc=usdc)
        return usdc

    gateway.cancel = cancel
    gateway.cancel_asset = cancel_asset
    gateway.open_orders = open_orders
    gateway.collateral_balance = collateral_balance
```

`ruff check --select C901` passes for the grown function. The file grows from 1457 to 1469 lines; it is already over 1k, and the change stays inside the existing live REST seam.

## 5. Tests

Exact diff: `orchestrate/work/plan-step-011/planned_tests.diff`. ruff check, ruff format, and strict basedpyright pass on all three files.

### 5.1 Move the parser test to `tests/test_trading.py`

1. Delete `test_parse_collateral_converts_and_rejects` from `tests/test_dashboard_live_hub.py:472-490`, and `parse_collateral_usdc,` from its import (`:27`).
2. Add `parse_collateral_usdc` to the `shared.utils.trading` import in `tests/test_trading.py` and append the test with these additions:
   - cases add `"1000001": 1.000001` (now 0, 1, 500000, 1000000, 1000001, 125500000, plus int 3000000);
   - bad inputs add `{"balance": "inf"}` and `{"balance": float("inf")}` (next to `{}`, `""`, `"  "`, `"-5"`, `"abc"`, `"1.5"`, `nan`, `True`, a raw string, `None`).

### 5.2 Integration test in `tests/test_trader_shared_budget.py`

Add `_live_engine(response)` and `test_live_collateral_reads_balance_in_micro_units` at the end of the file. New imports: `asyncio`, `functools.partial`, `types.SimpleNamespace`, `typing.Any, cast`, `polymaker.execution.gateway.ExecutionGateway`, `CapturedEvent` from `test_trader_session_core` (`:1727-1730`, the file already imports from there), and `install_collateral_snapshot, install_strict_rest` from `trader.engine_seams`.

1. `_live_engine` builds a live fake: `positions` returns `{}`, `balance_allowance` returns the case response, the cancel paths raise if called, and `collateral_balance` is the real fork method bound to the fake (`partial(ExecutionGateway.collateral_balance, cast(ExecutionGateway, gateway))`). So the test is red against the real heuristic.
2. The test installs `install_collateral_snapshot` then `install_strict_rest` (production order), runs `gateway.positions()`, and quotes the Dota core with `_pair()` / `_quote()` / `_buy_quotes()`.
3. Cases `(response, cash, buys)`:
   - `{"balance": "500000"}` → 0.5, 0 BUYs
   - `{"balance": "1000000"}` → 1.0, 0 BUYs
   - `{"balance": "1000000000"}` → 1000.0, 3 BUYs
   - `{"balance": "inf"}` → 0.0, 0 BUYs
   - `{"balance": "1.5"}` → 0.0, 0 BUYs
   - `{}` → 0.0, 0 BUYs
   - `"<html>bad gateway</html>"` → 0.0, 0 BUYs
4. Asserts: `cache.value == approx(cash)`, BUY count, `dota.last_block == ("" if buys else "no_cash")`, and exactly one diag event `CapturedEvent(stage="collateral", fields={"response": response, "usdc": cash})`. `log_event` is captured with `monkeypatch.setattr("trader.engine_seams.log_event", capture)`. Close the store at the end, like the other tests in the file.

### 5.3 Red / green (measured)

- HEAD `2f4629ab` (`red_on_2f4629ab.txt`): the integration test fails 7 of 7 (500000.0, 1000000.0, `inf`, 1.5 cache values; for the other 3 no diag row). The unit test fails on import (`cannot import name 'parse_collateral_usdc' from 'shared.utils.trading'`).
- Planned files (`planned/src` first on `PYTHONPATH`, planned test copies instead of the 3 originals): the 8 new items pass.
- Full suite without goldens on the planned files: `3215 passed, 72 warnings, 2 subtests passed in 27.25s` (`suite_planned_src.txt`). That is 3208 + 7 integration cases; the unit test only moves. No other test changes.

## 6. Release regression and the progress note

The step also asks for the shared regression of the live paths of STEP-001..011 and a release note.

1. Release set (measured on the planned files: `642 passed, 8 warnings, 2 subtests passed in 5.34s`, `release_regression_planned.txt`):
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_diag.py tests/test_trader_durable_place.py tests/test_trader_sell_venue_cancel.py tests/test_trader_buy_terminal_proof.py tests/test_trader_unsettled_buy_recovery.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_core_persistence.py tests/test_trader_book_guard.py tests/test_trader_wallet_store.py tests/test_trader_session_core.py tests/test_strategy_core.py tests/test_strategy_anchor_age.py tests/test_trader_wallet_host.py tests/test_trader_host_resources.py tests/test_trader_live_execution.py tests/test_trader_engine_seams.py tests/test_trader_pair_merge.py tests/test_trading.py tests/test_trader_shared_budget.py tests/test_dashboard_live_hub.py -q`
   - dispatch → terminal → settlement: `test_trader_durable_place.py` (STEP-002), `test_trader_sell_venue_cancel.py` (STEP-003), `test_trader_buy_terminal_proof.py`, `test_trader_unsettled_buy_recovery.py`, `test_trader_core_persistence.py` (STEP-004).
   - bad book → cancel: `test_trader_book_guard.py` (STEP-005).
   - dust and qty compare: `test_trader_wallet_store.py`, `test_trader_session_core.py` (STEP-006).
   - exact / numeric / mirror strip → fair freshness: `test_trader_session_core.py`, `test_strategy_core.py`, `test_strategy_anchor_age.py` (STEP-007..010).
   - diag: `test_trader_diag.py` (STEP-001). Collateral: the last five files (STEP-011).
   These files are all inside the full suite. The separate run gives the release line in the note.
2. Append a STEP-011 entry to `tasks/live-a-audit-fixes/progress.txt` in the usual format, then a release block with:
   - the commit hash, the full-suite line, the release-set line, and basedpyright 0 errors;
   - versions: Python 3.13.2 (`.venv`), `py-clob-client-v2` 1.0.2, `poly-maker` fork at `4f32103591c9582ccd012bdf10f77d86e5879444` (unchanged), branch base `6787bf3f`;
   - not run: goldens (`tests/test_extraction_oracle.py`, `tests/test_follow300_replay.py`), backtests, simulator, VPS, deploy, restart;
   - known limits from earlier entries: STEP-001 gaps (positions raw body not captured, VPS free disk unconfirmed, 7-day age is only `backupCount`); review-all-005 F3 (boot retire runs once) and F4 (terminal before the POST reply dropped) accepted; STEP-003 one-cycle delay after a venue cancel plus a partial fill (`d7332da5`); STEP-006 missed BUY smaller than residue plus 0.005 is not detected; STEP-007 a sub-cent book can move a 0.01 join one cent toward the touch, backtest strip still uses 0.01; STEP-009 `test_protective_events_on_bad_yes_book` failed under xdist on a subset; STEP-010 goldens can differ where a failed-anchor streak passes 45 s; STEP-011 items in section 7.
   - Do not set `passes`; the orchestrator does that.

## 7. Risks and limits

1. **Unseen live reply.** I did not see a raw live `/balance-allowance` reply. If the venue sends a decimal string, the cache becomes 0.0, BUY stops with `no_cash`, and the docker log shows `trader collateral unreadable: collateral balance malformed` every reconcile. This fails closed. The diag `collateral` and `http` rows show the raw reply.
2. **Transient read failure.** A failed read gives 0.0 and blocks BUY until the next good read (about 20 s). The fork does the same today for `{}`. Not changed.
3. **Huge digit strings.** A `balance` with 309 to 4300 digits makes `base / 1_000_000` raise `OverflowError`, which is not caught. A wallet cannot hold that amount; no branch for it.
4. **Fork CLIs.** `moneydoctor` and `doctor` keep the heuristic. They are not part of live trading and the fork is frozen.
5. **Goldens and backtest.** The backtest does not read this balance. No golden change is expected. Goldens are not run.

## 8. Verification

Run from the worktree.

1. Red: add the tests from section 5 first. Run the two files one at a time, because a collection error stops the whole session:
   - `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_shared_budget.py -q`: expect 7 failures, all `test_live_collateral_reads_balance_in_micro_units`.
   - `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trading.py -q`: expect a collection error, `cannot import name 'parse_collateral_usdc' from 'shared.utils.trading'`.
2. Green: apply section 4 and run `tests/test_trading.py tests/test_trader_shared_budget.py tests/test_dashboard_live_hub.py tests/test_trader_engine_seams.py tests/test_trader_pair_merge.py` with the same prefix. All pass.
3. Full suite without goldens, required before the commit:
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests --ignore=tests/test_extraction_oracle.py --ignore=tests/test_follow300_replay.py`
   Expect 3215 passed. If `test_protective_events_on_bad_yes_book` fails under xdist, rerun it serially and report both results.
4. Release set from section 6.1. Expect 642 passed.
5. Typecheck: `uv run python -m basedpyright`. Expect 0 errors.
6. Lint: `uv run python -m pre_commit run --files src/shared/utils/trading.py src/dashboard/balance.py src/dashboard/collateral.py src/trader/engine_seams.py tests/test_trading.py tests/test_trader_shared_budget.py tests/test_dashboard_live_hub.py`.
7. `grep -rn "parse_collateral_usdc\|BASE_UNITS_PER_USDC" src tests`: the definitions only in `src/shared/utils/trading.py`; users in `dashboard/collateral.py`, `trader/engine_seams.py`, `tests/test_trading.py`.
8. Do not run the goldens. No backtest, no simulator, no VPS, no push, no edit in `../poly-maker`.
9. Report: red and green counts, the full-suite line, the release-set line, and `git diff --shortstat 2f4629ab`.
