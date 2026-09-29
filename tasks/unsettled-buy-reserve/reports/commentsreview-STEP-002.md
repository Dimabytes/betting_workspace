# Comments review: STEP-002 — commit 9b2742b7 (esports-trader)
Status: FINAL

Scope: diff of `9b2742b74093f51c6d8e0555c8a8cac9c1565783` (`src/trader/core_persistence.py`, `src/trader/session_budget.py`, `tests/test_trader_core_persistence.py`, `tests/test_trader_core_recovery.py`, `tests/test_trader_shared_budget.py`). Skill: `/Users/dimabytes/.claude/skills/feature-json-no-comments-review/SKILL.md`. Review only; nothing edited.

## Findings

1. `tests/test_trader_core_persistence.py:798` — `status=status,  # type: ignore[arg-type]` inside the `_resting` helper (`tests/test_trader_core_persistence.py:788`).
   - DELETE the suppression. `reportArgumentType` is a real correctness rule; it fires only because the helper declares `status: str` while `RestingOrder.status` is `OrderStatus` (`src/strategy/types.py:137`, `Literal["pending","live","canceling","unknown","gone"]` at `src/strategy/types.py:7`).
   - MUST KILL the `status: str` annotation on `_resting` (`tests/test_trader_core_persistence.py:788`): reannotate the parameter as `status: OrderStatus`. All five call sites pass valid `OrderStatus` literals ("pending", "live", "canceling", "unknown" — `tests/test_trader_core_persistence.py:860-864`), and "gone" is already legal in the Literal, so no call site or production code changes are needed.

## Skips

- `# pyright: reportPrivateUsage=false` — file-level suppression at `tests/test_trader_core_persistence.py:3`, `tests/test_trader_shared_budget.py:3`, `tests/test_trader_core_recovery.py:3`. Keep clause: lint suppression whose rule is style-only — `reportPrivateUsage` enforces naming encapsulation, and this suite deliberately white-boxes `store._conn`, `core._state`, `_require_status` (e.g. `_require_status` import at `tests/test_trader_core_persistence.py:39`, `store._conn` at `tests/test_trader_core_persistence.py:127`, `core._state`/`dota._state` at `tests/test_trader_core_recovery.py:489` and `tests/test_trader_shared_budget.py:386`); that is the codebase's established test convention. Also pre-existing: present in parent commit `9b2742b7^` in all three files; it appears in this diff only as unchanged context. The commit does add more private usage under its cover, but no new suppression was introduced.
- Module docstrings at `tests/test_trader_core_persistence.py:1`, `tests/test_trader_shared_budget.py:1`, `tests/test_trader_core_recovery.py:1` (e.g. `"""SQLite checkpoints, bindings, and independent outbox cursors."""`) — one-line module descriptions; doc-comment class, pre-existing context.
- `_CORE_SCHEMA` SQL block (`src/trader/core_persistence.py:97-109` added lines) — DDL string content, not prose.
- No other comments, docstrings, `noqa`/`fmt`/lint suppressions, banners, or commented-out code were added anywhere in the diff. Production changes in `core_persistence.py` and `session_budget.py` carry zero comments.

## Notes

- The betting_workspace notes commit `f81b9a7d` (`.shared-skills/vps-trader/SKILL.md`) is outside the scoped code commit and is documentation, not code comments.
