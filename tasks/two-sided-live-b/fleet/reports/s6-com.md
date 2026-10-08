# s6-com — comment review of STEP-006
Status: FINAL

Scope: `git -C E diff a58d19c9..77a85364` — commit 77a85364 "Run two-sided maps in a
TwoSidedWorker: book fair, clean restart, final merge." Reviewed only comments and
suppressions the diff adds or touches, plus comments inside functions the diff
refactors (`MatchWorker.open_core`, `MatchWorker._quote_pm`, `DustSweeper._blocked`,
`two_sided_policy`, `PairMerger` merge surface). Report only; nothing edited.

## Findings

1. `src/trader/session_core.py:1` — module docstring touched by the diff:
   `"""Live/paper adapter: one market's strategy core, inbound queue, and venue plan."""`
   **DELETE.** Narration of the module's role, not a public API contract — the module
   name `session_core` and the class names already say it. The diff reworded it
   (Follow300 → strategy core), which is the moment to remove it, not to polish it.

2. `src/trader/session_core.py:351` — class docstring touched by the diff:
   `"""One market's strategy core: inbound queue, id map, plan for this cycle."""`
   **DELETE.** Narration that restates the class's own fields (`_inbound`, the id map,
   `take_plan`). No contract, invariant, or external-dependency surprise is stated.
   Same situation: the diff edited the wording instead of dropping it.

## Skips (keep clause)

- `src/trader/two_sided_worker.py:1` — `# pyright: reportPrivateUsage=false` (new).
  Keep: `reportPrivateUsage` is a style/encapsulation rule, not a correctness or
  safety rule, and this codebase has deliberately opted out of it — the identical
  header already sits on ~15 `src/trader` modules (`match_worker.py`,
  `pair_merge.py`, `dust_sweep.py`, `session_core.py`, `core_session_io.py`, …).
  The package treats `_` names as package-internal seams by design; the subclass
  reaches `MatchWorker` internals the same way every sibling module does. Enforcing
  a different boundary is a repo-wide convention decision, not a STEP-006 defect.

- `tests/test_trader_two_sided_worker.py:1` — `# pyright: reportPrivateUsage=false`
  (new). Keep, same clause, stronger for tests: ~40 test files carry it because
  tests legitimately poke internals (`worker._dust`, `core._pending`,
  `worker._quiesce`). The rule is pedantic in test code.

- `src/trader/session_types.py:18` — `"""Per-snapshot decision label. A member IS its
  journal wire string."""`. Keep: it states a real contract (the enum member's value
  is the wire format), and the diff only added `BOOK = "book"` / `BAND = "band"`
  members without touching the docstring — context line only, not in findings scope.

- `src/trader/session_core.py:3` — `# pyright: reportPrivateUsage=false`. Pre-existing
  context line in the diff hunk, unchanged. Out of scope; same keep clause as above
  regardless.

- `src/trader/pair_merge.py:91-92` — `async def sweep(self, *, force: bool) -> None:
  del force`. A silent no-op stub so `PairMerger` satisfies the `DustSweeper.sweep`
  call shape. Code, not a comment — no comment finding. (Whether a stub deserves to
  exist is a design question for the code reviewer, not this pass.)

## Removed by the diff (correct, no action)

- `src/strategy/policy.py` — deleted `"""Two-sided maker. Adapters pass cadence; the
  quote knobs are the module constants."""` narration on `two_sided_policy`.
- `src/trader/match_worker.py` — deleted `"""Publish the PM cell and wake the quoter.
  Returns early if journal write fails."""` on `_quote_pm` and `"""Build this
  market's Follow300 core. Attach and tests call this."""` on `open_core`.

## Notes outside the flags

- New `src/trader/two_sided_worker.py` (153 lines) and
  `tests/test_trader_two_sided_worker.py` (533 lines) carry zero comments/docstrings
  beyond the pyright header — clean.
- Refactored bodies (`open_core`, `_quote_pm`, `_write_decision`, `_core_limits`,
  `_core_freshness`, `_adopt_core`, `_open_trace`, `DustSweeper._blocked`) contain no
  inner comments in the post-diff source.
- Out of scope but observed: `MatchWorker.handle_event`'s docstring ("join-bid cell")
  and `_on_event`'s ("Publish the model cell") are now inherited by `TwoSidedWorker`,
  which publishes a book fair, not a model cell — stale wording, but untouched by the
  diff. `tests/trader_session_fixtures.py:573` docstring says "One MatchWorker"
  while the fixture now accepts `worker_class` — context line only.
