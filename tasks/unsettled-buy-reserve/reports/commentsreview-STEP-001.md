# commentsreview STEP-001 — commit 54b7ce50
Status: FINAL

Scope: `git show 54b7ce50` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (13 files, +597/−14). Per `/Users/dimabytes/.claude/skills/feature-json-no-comments-review/SKILL.md`: report every comment, suppression, and MUST KILL flag in the scoped diff. Review only; no code edited.

## Method

Scanned every hunk of the commit for `#`, docstrings (`"""`), and suppression tokens (`type: ignore`, `pyright`, `noqa`, `pragma`, `pylint`, `eslint`, `ts-ignore`). Result: one relocated inline comment, zero new comments across +597 lines, zero added docstrings, zero added suppressions, zero commented-out corpses, zero banners.

## Findings

None. No comment or suppression earns DELETE or MUST KILL.

## Skips

1. `src/shared/utils/trading.py:16` — `HALF_SHARE_TICK = 0.005  # half a CLOB share tick: float residue, not a short SELL`
   - Verdict: KEEP. Keep clause: non-obvious behavior forced by an external protocol we cannot reshape. The value is tied to the Polymarket CLOB's 0.01-share quantization (`share_floor` floors to two decimals, `src/shared/utils/trading.py:19-21`) and to IEEE-754 residue; without the note, `0.005` reads as an invented epsilon. "not a short SELL" documents why the oversell checks (`src/trader/wallet_store.py:850`, `src/trader/wallet_store.py:857`) treat a ≤0.005 excess as dust rather than a genuine short.
   - Context: this is a pre-existing comment moved verbatim from `src/trader/wallet_store.py` (the `-` line at old line 25) to its new shared home — the planned supporting relocation for STEP-001. No new prose was authored.

2. `tests/test_strategy_late_fills.py:3` — `# pyright: reportPrivateUsage=false`
   - Context line only; unchanged by this commit. Pre-existing codebase convention: 41 test files carry the same header to share underscore fixture helpers across test modules. `reportPrivateUsage` is an encapsulation/style diagnostic; on shared test fixtures it is pedantic (style-only suppression clause). Not flagged.

## Clean areas

- `src/strategy/types.py`, `lifecycle.py`, `budget.py`, `quoting.py`, `engine.py`, `scheduling.py`, `src/trader/core_trace_codec.py`, `src/trader/wallet_store.py`, `src/shared/utils/trading.py`: all `+` lines are comment-free.
- All added test lines in `tests/test_strategy_late_fills.py`, `test_strategy_scheduling.py`, `test_strategy_budget.py`, `test_core_trace.py` are comment-free; test intent is carried by names (`test_buy_settled_below_proof_waits`, etc.).
- Project rule "Don't add comments that narrate the code" (`../esports-trader/AGENTS.md`) is satisfied: the step introduced zero new comments.
