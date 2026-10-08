# s2-com — comment review of STEP-002
Status: FINAL

Scope: `git -C E diff 8dadf747..1d380527` ("Quote two-sided bids in the strategy core behind TwoSidedPolicy"). Reviewed: comments/suppressions added or touched by the diff, and comments inside functions the diff refactors.

Method: extracted the full diff to `work/s2-com/step002.diff`, grepped added lines for comments/suppressions, then read the full bodies of every function the diff touches or refactors at `1d380527` (`armed_deadline_ns`, `_requote`, `step`, `books_unusable`, `place_missing`, `reconcile`, `_quote_after_gates`, `_recovery_exit`, `_should_reprice`, plus the new `two_sided_quoting.py` and the touched test module).

No suppressions were added (no `noqa`, `type: ignore`, `pragma`, etc.; `pytest.mark.parametrize` is a mark, not a suppression). The new module `src/strategy/two_sided_quoting.py` contains zero inline comments.

## Findings

1. `src/strategy/scheduling.py:124` — `# Already past the boundary: wake now so the action lands before the next wipe.` (pre-existing, inside `armed_deadline_ns`, refactored this diff). Narrates `if boundary <= now_ns: return now_ns`; the "next wipe" justification is unverifiable sermon. **DELETE.** Code already says it: a past deadline returns `now_ns`.

2. `src/strategy/scheduling.py:129-130` — `# Requote can return before _sync_latch (pause, kill gate, mid_spike), leaving / # the re-anchor boundary behind; a past one must not busy-wake, only tighten.` (pre-existing, inside refactored `armed_deadline_ns`). Two-line justification = confession. The real information is an asymmetry the code hides: entries in `boundaries` wake when past, `reanchor` only tightens (`now_ns < reanchor < deadline`). **DELETE** + **MUST KILL** the reanchor block in `armed_deadline_ns` — reshape so the two policies are named in code, e.g. iterate `(boundary, wake_if_past)` pairs, or split `waking_boundaries`/`tightening_only` tuples so `reanchor` visibly joins the second.

3. `src/backtest/two_sided_strategy.py:363` — `"""Hold a one-tick move. A two-tick move, or a one-tick move that lasts, cancels."""` (pre-existing docstring inside `_should_reprice`, whose body this diff edits). Private-method docstring; not a public API contract. The rule it states is now owned by the tested shared path `strategy.two_sided_quoting.hold_one_tick_moves` — the docstring is a second, drifting source of truth for the same policy. **DELETE.** Named constants `REPRICE_NOW_TICKS`/`REPRICE_HOLD_NS`/`tick_gap` carry the semantics already.

4. `tests/test_strategy_two_sided.py:1` — `"""Self-check values of the two-sided quote math, imported from strategy.two_sided."""` (context line in the diff's first hunk; unchanged but touched). Now stale: this diff adds ~470 lines of engine-level `step()` tests, so the file is no longer "self-check values of quote math." **DELETE** — wrong docstring is worse than none; the filename already scopes it.

## Skips (kept)

- `src/strategy/two_sided_quoting.py:1` — `"""Two-sided maker quotes in the core: pull reasons, bid targets, one-tick hold."""` — KEEP. Module-level docstring stating the module's contract; matches the one-line module docstring on every sibling in `src/strategy/` (`engine.py`, `quoting.py`, `scheduling.py`, `types.py`, `two_sided.py`, …). Doc-comment-on-module convention, one line, no narration.

## Out of scope (seen, not reviewed)

- `src/strategy/quoting.py:943` `# Pull BUYs and skip new quotes; leave resting SELLs alone (no join-down).` — inside `requote`, which this diff does not modify (it only renames `requote`'s callees). Pre-existing; left for whoever next refactors `requote`.
- `src/strategy/quoting.py:291`, docstrings on `_cancel_kill_exposed` / `_advance_delta_gate`, and module docstrings of `two_sided.py`, `types.py`, `lifecycle.py`, `engine.py` — untouched by this diff.
