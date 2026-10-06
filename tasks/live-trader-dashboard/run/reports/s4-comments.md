STEP-004 review
Status: FINAL

Scope: `git diff 74ed1ec..68ca7867` in esports-trader (17 files, ~4k added lines) plus betting_workspace commit `6f1b6a5`. Every comment/docstring/suppression added or touched by the diff was enumerated; the added-line census is complete (no `#`/`"""`/`'''`/`noqa`/`type: ignore`/`pragma`/`pyright: ignore` occurrences outside the items below, and no comments inside the `_HTML`/`_CSS`/`_JS` component blocks). No comments were removed, and no pre-existing comments sit inside refactored hunks.

## Findings

1. `esports-trader/src/dashboard/app.py:1` — `"""Read-only Streamlit home screen over the process-wide LiveHub."""` — DELETE. Narration of the file's role, not an API contract. No sibling dashboard module (live_hub, catalog, hub_types, logs, diagnostics, market_books, reserve, tails, balance, wallet) carries a module docstring, and project rules ban narration docstrings.

2. `esports-trader/src/dashboard/fresh_view.py:1` — `"""Aria-live freshness status dot: one declared CCv2 component, text-only updates."""` — DELETE. Same narration class; the filename and `render_fresh` already say it.

3. `esports-trader/src/dashboard/home_view.py:1` — `"""Streamlit rendering for the home screen; consumes pure home DTOs."""` — DELETE. Same narration class.

4. `esports-trader/tests/test_dashboard_app.py:2` — `"""AppTest coverage for the Streamlit home screen with an injected fixture hub."""` — DELETE. Narration; the test names and imports say it.

5. `esports-trader/tests/dashboard_fixture.py:2-6` — four-line module docstring ("Shared offline world… fakes that fail closed… Never calls default_hub_config.") — DELETE. Narration of what the module does. The only non-obvious clause (fakes fail closed, never default_hub_config) is already enforced in code — the fake callbacks raise on unexpected input — and is mandated by the plan; prose adds nothing a reader can't see in `_fixture_fetch`/`_fixture_runner`.

6. `esports-trader/tests/dashboard_fixture.py:100` — `FixtureState` docstring `"""Mutable fixture answers: tests and the browser driver change these."""` — DELETE. Narrates the deliberate non-`frozen` `@dataclass`; the missing `frozen=True` already shows mutability, and the class name + call sites show who mutates it.

7. `esports-trader/tests/dashboard_fixture.py:1` — `# pyright: reportPrivateUsage=false` — DELETE. Dead suppression: the file accesses no private names from other modules. Its `_signal`/`_write_match`/`_write_journal`/`_write_trace`/`_write_sidecar`/`_fixture_*` are same-module, which the rule does not flag.

8. `esports-trader/tests/test_dashboard_home.py:1` — `# pyright: reportPrivateUsage=false` — DELETE. Dead suppression: every `_foo(...)` call in the file is one of its own module-level helpers; nothing private is imported or reached through another module.

9. `esports-trader/tests/test_dashboard_app.py:1` — `# pyright: reportPrivateUsage=false` — DELETE the suppression; MUST KILL `dashboard_fixture._write_match` (dashboard_fixture.py:155) and `dashboard_fixture._write_journal` (dashboard_fixture.py:194) — rename to `write_match`/`write_journal`. These two functions are the fixture's cross-module API for tests (called as `fx._write_match`/`fx._write_journal` at test_dashboard_app.py:93,101); naming them private and then file-disabling the encapsulation rule is backwards. Renaming removes the only two real violations in the file and the directive goes entirely. `reportPrivateUsage` guards real encapsulation, so blanketing the file to keep an underscore is not justified.

## Skips (keep-clause verdicts)

- `esports-trader/src/dashboard/home_view.py:26-27` — `# streamlit dataframe overloads leak Unknown into strict basedpyright.` + `_dataframe: Callable[..., DeltaGenerator] = cast(Any, st).dataframe` — KEEP, external-dependency clause. Verified live today against the installed streamlit: a bare `st.dataframe(...)` call raises `reportUnknownMemberType` under the project's strict basedpyright because the overloads embed `Array[Unknown]`. We cannot reshape streamlit's stubs, and the `Any` is confined to the single member access — the result is immediately re-narrowed to `Callable[..., DeltaGenerator]`. The comment is a factual one-liner naming the dependency gotcha, not a sermon.

- `esports-trader/Makefile` — `dashboard:  ## Read-only live trader dashboard on 127.0.0.1:8501` — KEEP. The `##` text is data consumed by the existing `help` recipe, matching `inspect-live:`/`inspect-backtest:` convention; functional help text, not a comment.

- `betting_workspace` commit `6f1b6a5` — adds one prose line to `.shared-skills/vps-trader/SKILL.md` documenting `make dashboard` + SSH tunnel. Documentation file, no code comments or suppressions. Nothing to flag.
