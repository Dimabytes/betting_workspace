STEP-003 review
Status: FINAL

Scope: `git diff e4c659d494a096d2543d902a99dfeff54b04a09c..782c0056` in esports-trader (8 files, +3007/-21). The diff adds exactly three comment/suppression artifacts and no docstrings, narration comments, banners, commented-out code, or TODO-style markers. A grep over the full diff for `#`, `"""`, `'''`, `noqa`, `pylint`, `mypy`, `pragma`, `TODO`, `FIXME`, `NOTE`, `HACK` confirms this. Existing comments touched by the diff: none found in the `wallet.py`, `logs.py`, `reserve.py`, or `test_dashboard.py` hunks.

## Findings

1. tests/test_dashboard_live_hub.py:953 — `snap.day.accrual_per_game["lol"] = 9.0  # type: ignore[index]`

   The `index` rule catches real correctness bugs: `accrual_per_game` is declared `Mapping[str, float]` (src/dashboard/live_hub.py:117) and publishing a mutable-looking write through it would be an actual bug in production code. The test suppresses the check on a line whose entire purpose is to be type-invalid, inside `pytest.raises(TypeError)`, to prove the published mapping is immutable at runtime. The intent is good but the suppression is avoidable: the runtime value is constructed as `MappingProxyType` (src/dashboard/live_hub.py:955), so `assert isinstance(snap.day.accrual_per_game, MappingProxyType)` proves the same immutability contract with zero suppression and no deliberately-ill-typed statement.

   Verdict: DELETE the `# type: ignore[index]`. MUST KILL: the type-invalid assignment `snap.day.accrual_per_game["lol"] = 9.0` inside `pytest.raises(TypeError)` — reshape to an `isinstance(..., MappingProxyType)` assertion (or equivalent type-correct immutability check).

## Skips (kept, with the clause that saved them)

1. src/dashboard/balance.py:1 — `# pyright: reportMissingTypeStubs=false`
   Keep. `reportMissingTypeStubs` is a coverage/pedantic diagnostic, not a bug-catcher: it fires because the external dependency `py_clob_client_v2` ships no type stubs, which we cannot reshape. Keep clause: lint suppression whose rule is pedantic/style-only, applied to a foreign-dependency limitation.

2. tests/test_dashboard_live_hub.py:1 — `# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false`
   Keep. `reportMissingTypeStubs` — same clause as above (untyped third-party imports `py_clob_client_v2.http_helpers`, `polymaker.*`). `reportPrivateUsage` is an encapsulation-hygiene rule, not a correctness/safety check, and the step plan mandates deterministic tests through deliberately-private injected internals (`hub._apply_day`, `hub._publish`, `_day_job`, `hub._cfg`). Keep clause: pedantic/style-only lint suppression, scoped to test code that legitimately crosses the encapsulation boundary.

No other comments or suppressions exist in the scoped diff. One finding, two skips.
