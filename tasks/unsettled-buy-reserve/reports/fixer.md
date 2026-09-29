# Fixer report
Status: FINAL

Two intended policy commits left stale pins. Both are fixed on `esports-trader` `main`. The full suite is green.

## 1. Adapter contract: account room `inf` vs `1_000_000`

`eedcfdaa` ("keep the account cap out of the fork") makes the backtest fork pass an unbounded account room (`src/backtest/strategy.py:265`, `src/backtest/strategy.py:1102`) and starts comparing that field (`tests/adapter_contract_fixtures.py:258`). The live harness still passed `account_cap_usdc=1_000_000.0`, a stand-in added in `6329eb8c` so the new required argument would not bind. After `TapeWake`, backtest state was `inf` and live state was `1000000.0`. That is the 14 `tests/test_adapter_contract.py` failures, and it already failed on `22f53724`.

The harness now passes `float("inf")` (`tests/adapter_contract_fixtures.py:693`), the same room the backtest fork supplies. Production live still computes a real account room in `budget_from_orders` (`src/trader/session_budget.py:35`).

Commit `f24b5d72` — `Match the adapter contract's account room to the backtest fork.`

## 2. Map cap is 6 rungs; the refill test and the smokes still said 8

`6329eb8c` set `MAX_POSITION_LEVELS = 6` (`src/shared/constants/strategy.py:31`). The previous value was 8, from `be734a1c`. It is the only edit to `src/shared/constants/strategy.py` since the last smoke capture, `1ae07dbd`.

`test_shared_position_cap_stops_refills` still required a partial third ladder (`0 < len < BUY_LEVEL_COUNT`). Two ladders of 3 fill a 6-rung cap exactly, so the third wake submits nothing: `assert 0 < 0` at the old line 2355. The test now expects that empty wake and a cap room below one clip (`tests/test_backtest_maker.py:2318`, `tests/test_backtest_maker.py:2354`, `tests/test_backtest_maker.py:2355`).

The same constant change moved `strategy_constants_sha256`, which is why the five red smokes reported `inputs changed: strategy_constants_sha256`. Feeds were unchanged. Recaptured with `scripts/capture_follow300_smoke.py` (the script the repo uses after a deliberate policy change). `tests/fixtures/follow300_changes/smoke/inputs.json:87` is now `d4a3ffaefe545b53318a2c89e42f31d613191a3bd7628466706b340d354920db` (was `53f9ef5de375e2d5b8ac5801ba76b8ac1678aa2b6d54bd99d1566cae7617bd7e`). Tapes that moved: `dota_8837869969`, `dota_9007700576`, `dota_9015175653`, `lol_115564793879469302`, `lol_116634566264113530`. The other three seed-0 tapes already matched the replay.

Commit `117c61d4` — `Pin the 6-rung map cap in the refill test and the follow300 smokes.`

## Tests

From `esports-trader`, `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest --with pytest-xdist python -m pytest -n 10 --dist worksteal --basetemp=/tmp/pytest-fixer -q`.

- `tests/test_adapter_contract.py` and `tests/test_backtest_maker.py::test_shared_position_cap_stops_refills`: 15 passed.
- `tests/test_follow300_replay.py`: 11 passed.
- `tests` (full suite, goldens included): 2516 passed, 2 subtests passed, exit 0, 235s.

No push. `poly-maker` was not edited. The other agent's `fbcab230` (`AGENTS.md`, `pyproject.toml`, `tests/conftest.py`, `uv.lock`) was left alone; those bytes were already in the tree the full suite ran against.
