c5e22507
Status: FINAL

## Findings (s6-review.md)

1. `match_trace.py:135` — fixed. Deleted `AdvisoryPermissions`, `_permissions`, `Fold.permissions`, `AdvisoryTrace.permissions`, and the unread `account_cap_room_usdc`/`cash_usdc` budget fields; `kind == "reset"` now clears `fold.budget`, a later `BudgetUpdate` may set it again. New `test_advisory_reset_clears_budget` covers both orders.
2. `hub_types.py:351` — fixed. `_session_facts` builds `orders` in one loop via `_order_facts` and derives `sells` from it (`side == "SELL"`, `remaining_qty > 0`, `held_qty` from `checkpoint.inventory[token_index]`); `_sell_facts` deleted, `SessionFacts.sells` shape unchanged.
3. `match_state.py:482` — fixed. `SellOrderFacts.observation(session_key)` (hub_types) is the single `SellOrderFacts → SellObservation` constructor, now used by `SellWatcher.observe`, `_exit_findings`, `_now_facts`, and `MatchObserver._observe_sells`. `_now_facts` feeds `explain_exit` `max(session.held)` — the same held as `_exit_findings` — with wallet leg sum only as the no-session fallback; `MatchObserver.observe` takes `session` and wedges on `session.sells` (same filtered set SellWatcher sees); `sell_held_map` deleted.
4. `match_view.py:266` — fixed. `BookPanel.fresh: FreshnessState` set in `_book_panel` via new `_book_fresh` (no book → `no_data`, disconnected → `stale`, not initialized/ready → `updating`, else `fresh`); `render_book` passes the literal, caption printed separately.
5. `match_page.py:1` — fixed. File-level `reportUnknownMemberType=false` removed; single inline `# pyright: ignore[reportUnknownMemberType]` on `fig.update_layout` (the only diagnostic, matching the reviewer's verified count).
6. `home_diag.py:464` — fixed. The `"эта карта · "` source-rewrite pass deleted; selected-map findings already carry the only session/map in scope.

## Findings (s6-comments.md)

1. `match_page.py:1` pyright header — fixed (same as review #5; the MUST KILL reshape applied verbatim).

## Verification

- `uv run ruff check src/dashboard/ tests/test_dashboard_match.py tests/test_dashboard_home.py tests/test_dashboard_app.py` → All checks passed.
- `uv run basedpyright src/dashboard/` → 0 errors, 0 warnings, 0 notes.
- `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_dashboard_match.py tests/test_dashboard_app.py tests/test_dashboard_home.py tests/test_dashboard.py tests/test_dashboard_game_state.py -x -q` → 125 passed.
- Full suite `pytest tests/ -x -q` → 2681 passed, 2 failed — both pre-existing on this checkout and outside the diff: `test_trader_session_config.py::test_read_template_satellite_clips_default_to_profile_clip` (clip-tier config drift, fails with my changes stashed) and `test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke[dota_8837869969]` (model/signals sha256 drift vs golden). No dashboard or STEP-006 code involved.
- `feature.json` untouched (`passes: false`), no push.

## Files changed (c5e22507, +113/−223)

`src/dashboard/match_trace.py`, `src/dashboard/hub_types.py`, `src/dashboard/match_state.py`, `src/dashboard/match_view.py`, `src/dashboard/match_page.py`, `src/dashboard/home_diag.py`, `tests/test_dashboard_match.py`.
