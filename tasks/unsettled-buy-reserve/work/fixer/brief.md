# Brief: fixer — make the esports-trader test suite green on main

Repo: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader, branch `main`, work in place (no worktree, no branch).
Read esports-trader/AGENTS.md first.

Known failures (they reproduce on 22f53724, before commit 54b7ce50; see ../reports/implementer-STEP-001.md lines 38-46):
1. 14 in tests/test_adapter_contract.py: `state.budget.account_cap_room_usdc inf != 1000000.0` after `TapeWake(now_ns=0)`.
2. tests/test_backtest_maker.py::test_shared_position_cap_stops_refills: `assert 0 < 0` at tests/test_backtest_maker.py:2355.
3. 5 golden smokes in tests/test_follow300_replay.py (current_policy_smoke) mismatch: `inputs changed: strategy_constants_sha256`.

Goal: every test passes. Find the root cause of each (git log / git bisect by reading, which commit broke it). Fix code if the code is wrong, fix the test/fixture if the behaviour change was intended. For the goldens: find which commit changed the strategy constants; if that change was intended, recapture the goldens the way the repo documents it (see git log for "recapture" commits) and say so in the report.

Rules:
- Another agent (implementer) works on the same repo for feature STEP-00x (strategy kernel, budget, checkpoint files). Do not touch or commit its uncommitted changes. Stage only your own files with explicit `git add <path>`; never `git add -A`/`.`, never stash/reset/checkout.
- One commit per root cause, message in repo style (see git log). No push.
- Tests: use the command from 00-context.md 'Running tests' with `-n 10 --basetemp=/tmp/pytest-fixer`. Final check: full suite `tests` including goldens.
