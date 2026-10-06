STEP-005 review
Status: FINAL

Scope: `git diff 4044b4bb7073d544aa15dfb8f7377e786c71eec8..879efa1d` in esports-trader. The diff adds zero `#` comments outside the suppressions listed below, no commented-out code, no TODO/FIXME markers, no file-level suppressions. The modified `src/dashboard/tails.py` contains no comments at all, before or after. Two module docstrings and three type suppressions are the entire inventory.

## Findings

1. `src/dashboard/game_state.py:1` — module docstring `"""Dashboard game-state projection: decision slice + archive-derived slices."""`. Pure file-purpose narration; the step plan explicitly banned narration docstrings and no keep clause covers it. DELETE.

2. `tests/dashboard_game_fixtures.py:1` — module docstring `"""Shared archive builders for dashboard game-state tests."""`. Same narration; the filename already says it. DELETE.

3. `tests/test_dashboard_game_state.py:891` — `original = TailCache._read_resolved  # pyright: ignore[reportPrivateUsage]`. reportPrivateUsage is a real encapsulation rule, not a faulty or style-only one, and the suppression is trivially avoidable: `getattr(TailCache, "_read_resolved")` yields the same unbound function for the spy with no flagged attribute access (the `monkeypatch.setattr` call below already uses the string form). DELETE. MUST KILL `TailCache._read_resolved` as a direct attribute access in the test — reshape: `original = getattr(TailCache, "_read_resolved")`, production code untouched.

4. `tests/test_dashboard_game_state.py:1046` — `summary.decision.second = 1  # type: ignore[misc]` inside `pytest.raises(FrozenInstanceError)`. The `misc` bucket catches assignment to frozen-dataclass fields — a correctness guard, not pedantry. The suppression is avoidable: `setattr(summary.decision, "second", 1)` raises the same FrozenInstanceError and checkers do not flag dynamic writes. DELETE. MUST KILL the literal assignment — reshape: `setattr(summary.decision, "second", 1)`.

5. `tests/test_dashboard_game_state.py:1051` — `player.net_worth = 1  # type: ignore[misc]`, same case in the same test. DELETE. MUST KILL — reshape: `setattr(player, "net_worth", 1)`.

## Skips (examined, keep clause applies or out of skill scope)

- `cast(list[object], raw.get(...) or [])` and similar casts in `src/dashboard/game_grid.py`, `src/dashboard/game_state.py`, and the test file — these are boundary narrowing over parsed JSON dicts, the ingestion-boundary pattern the plan sanctions; not comment/suppression findings.
- `src/dashboard/tails.py` refactor — the diff touches `_read_tail`, `read`, `read_records`, `_read_resolved`, `_read_gz_tail`; the file has zero comments and none were added or removed.
- Existing test conventions (`# type: ignore[method-assign]` et al. elsewhere in tests/) — outside this diff's scope.

## Notes

- basedpyright runs in strict mode (`pyrightconfig.json`), so these suppressions are live, not decorative. The repo's established test-suppression convention (many `type: ignore`/`pyright: ignore` in tests/) explains the author's instinct, but per the review contract a suppression that is avoidable in one line is meat, not convention.
- No MUST KILL flags on production symbols; the only suppressions sit in test code.
