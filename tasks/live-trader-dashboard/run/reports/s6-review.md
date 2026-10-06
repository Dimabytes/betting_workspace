STEP-006 review
Status: FINAL

Scope: `git diff 7e69b435dec45d289df805c43310d9a5cf8f34d7..777bad68` in esports-trader. The page split is the right one: `app.py` stays a router, the projection is pure, chart and journal stay behind open expanders, and `SessionFacts.orders` is published from the wallet reducer. The fold, the order projection, and the exit line each keep a second copy of a model that already exists.

## Findings

1. `src/dashboard/match_trace.py:135` — major

   The advisory fold keeps a permissions machine and two budget fields the page never reads, and a `reset` row leaves the previous cap in place.

   `AdvisoryPermissions` (`match_trace.py:23`) is filled from `PermissionsUpdate`, cleared on revert (`match_trace.py:106`), and stored on `AdvisoryTrace.permissions`. `account_cap_room_usdc` and `cash_usdc` are parsed onto `AdvisoryBudget` (`match_trace.py:64`). `_map_limit_text` (`match_state.py:550`) reads `cap_room_usdc`, `seq`, and `wall_ts`. No other dashboard module reads `.permissions`, `.cash_usdc`, or `.account_cap_room_usdc`.

   `_fold_record` handles `truncated`, `revert`, and `event`. `kind == "reset"` falls through. `write_reset` (`src/trader/core_trace.py:117`) and `_on_reset` (`src/trader/replay_core_trace.py:351`) replace core state from the checkpoint, so a `BudgetUpdate` earlier in the same tail is the pre-reset estimate. After a reset with no later `BudgetUpdate`, `fold_advisory` still returns that cap.

   Delete `AdvisoryPermissions`, `_permissions`, `AdvisoryTrace.permissions`, and the two unused budget fields. On `kind == "reset"`, set `fold.budget` to None. A later `BudgetUpdate` in the tail may set it again. A `truncated` row still clears the budget. Add a fold test: budget then reset and no later update yields `budget is None`; budget, reset, later update yields the later cap.

2. `src/dashboard/hub_types.py:351` — major

   `_session_facts` walks `checkpoint.orders` twice. `_sell_facts` (`hub_types.py:256`) keeps SELL rows with `submitted_qty - filled_qty > 0`. `_order_facts` (`hub_types.py:290`) keeps every row with `max(0, submitted_qty - filled_qty)`. Both resolve the token from `token_index` and the venue from `(session_id, order_id)`.

   Build `orders` in that one loop. Derive `sells` from it: `side == "SELL"`, `remaining_qty > 0`, `held_qty` from `checkpoint.inventory[token_index]`. Delete `_sell_facts`. `SessionFacts.sells` stays, so home and `SellWatcher` keep their field.

3. `src/dashboard/match_state.py:482` — major

   The "сейчас" line rebuilds the exit inputs, and the held quantity is a different rule from the diagnostic line.

   `_exit_findings` (`home_diag.py:202`) passes `max` of checkpoint `session.held` into `explain_exit`. `_now_facts` sums wallet `leg.size` across YES and NO (`match_state.py:482`). Dust is `held_qty < min_order_size` (`diagnostics.py:194`), so the sum of two tokens changes the label. `SellObservation` is constructed here (`match_state.py:486`), again in `MatchObserver._observe_sells` (`match_state.py:709`), and twice in `home_diag.py` (`168`, `206`).

   One function, `SellOrderFacts` to `SellObservation`, used at all four sites. `_now_facts` passes the same held quantity `_exit_findings` uses. The position table keeps the wallet sizes. `MatchObserver` keeps the per-status age map for every order. Its wedge loop should call `observe_sell` on `session.sells` through that helper. Delete `sell_held_map`.

4. `src/dashboard/match_view.py:266` — major

   `render_book` chooses the freshness dot by comparing `panel.state` to the captions `"live"` and `"socket отключён"`. Those strings come from `_book_state` (`match_state.py:307`). `"ждём book"`, `"ждём book после reconnect"`, and `"нет данных"` all become `"updating"` because they fail the two comparisons. A caption edit moves the dot.

   Store a `Literal["fresh", "stale", "updating", "absent"]` on `BookPanel`, set in `_book_panel` from `connected`, `initialized`, and `ready` (no book → `absent`). `render_book` passes that literal to `render_fresh` and prints the caption separately.

5. `src/dashboard/match_page.py:1` — minor

   The file starts with `# pyright: reportUnknownMemberType=false`. `home_view.py:21` narrows the one streamlit member that is untyped (`cast(Any, st).dataframe`). This directive turns the check off for the whole page module. Remove it. If one streamlit member is unknown, cast that member the same way.

6. `src/dashboard/home_diag.py:464` — minor

   `build_match_diagnostics` already limits session findings to the selected session and map findings to `_map_finding(view)`. It then rewrites any finding whose `source` is `"session journal"`, `"live.db core_sessions"`, or `"decision tail"` to `"эта карта · " + source`. A model-error finding uses `str(decision.path)` as its source (`home_diag.py:386`), so the prefix skips it. Renaming one of the three literals drops the prefix with no type error.

   Delete that pass. The selected session and map are already the only ones in the list. If the headline must carry "эта карта", pass that source into `_map_finding` and `_session_findings` when the finding is created.
