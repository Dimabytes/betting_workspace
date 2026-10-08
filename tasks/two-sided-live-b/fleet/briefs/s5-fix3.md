# Brief: s5-fix3 — close the last STEP-005 review items

STEP-005 (merge through the pUSD adapter) is committed and was reviewed three times. Two items remain open. You fix only these.

1. Read `R/reports/s5-rev-verify2.md` fully: F1 (partly resolved) and N2 (major: every deadline socket shutdown leaks its duplicated descriptor). Context: `R/reports/s5-rev.md`, `R/reports/s5-rev-verify.md`, `R/reports/s5-impl.md`.
2. Code: `src/trader/ctf_merge.py`, `src/trader/pair_merge.py` and their tests in E main (HEAD now includes STEP-006 `77a85364` and STEP-009; do not edit other files). Reviewers are reading E right now: keep the change small.
3. Fix what is right (you decide; give the concrete reason for any skip). Add a test per fix. No network, never use `*_B` keys, never send a real transaction.
4. Run the merge tests (`tests/test_trader_ctf_merge.py`, `tests/test_trader_pair_merge.py`, `tests/test_trader_two_sided_worker.py`) with `PYTEST_N` unset, basedpyright, and `make lint` on staged.
5. One NEW commit in E on main (do not amend: earlier commits are not yours). Do not touch feature.json `passes`. Append one short note to `W/tasks/two-sided-live-b/progress.txt`.

Report `R/reports/s5-fix3.md`: each item -> fixed / skipped with reason, files, checks, commit hash.
