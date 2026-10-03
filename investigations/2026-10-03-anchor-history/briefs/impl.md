# Brief: impl

Implement the CODE parts of both plans in esports-trader, in order.

## Part 1 — `plans/01-prior-anchor.md`
- todos `anchor-fn` and `test` only: inverse of `get_horn_datetime` in `src/shared/utils/match_time.py`; the no-spawn branch in `load_anchors` (`src/collect/s05a_fetch_prices_history.py`) with pauses resolved in the same order as `s06 resolve_pauses` (OpenDota first, else the archive schedule's pauses; read schedules only for rows without spawn and without an OpenDota file; pauses unknown → no anchor); the unit test.
- Skip todos `rebuild-quotes` and `prepare-train` (orchestrator runs them).
- Commit locally on `main` (no push). One commit for Part 1.

## Part 2 — `plans/02-history-policy.md`
- Steps 1–4 (shared code, live, backtest, training code). Include all tests the plan lists and recapture the golden files `tests/fixtures/follow300_changes/smoke/` with the repo's existing recapture path (find it; do not invent one).
- Skip step 5 (rebuild + A/B) — orchestrator runs it.
- Verification items 1–3 of the plan "Проверка" section: run them (pytest on the listed files; `cut_sweep.py` — if you cannot find it, write a small read-only equivalent under `work/impl/`; the LGD–Xtreme `grid-3011816-m3` check). Report the numbers.
- Commit locally on `main` (no push). One or more commits for Part 2.

## If the plan contradicts the code
Read the code, pick the smallest change that keeps the plan's intent, and write the decision in the report section "Deviations".

## Report (`reports/impl.md`)
Line 1 title, line 2 `Status: FINAL` when done. Sections: Commits (hash + subject), Files changed, What each plan item became (file:line), Tests run (command + pass/fail counts), Verification numbers, Deviations, Open questions.
