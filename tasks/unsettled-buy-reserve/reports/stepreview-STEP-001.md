# stepreview STEP-001 — commit 54b7ce50
Status: FINAL

Verdict: approve. No structural change requested.

Scope: `git show 54b7ce50` in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (13 files, +597/−14). Review only; no code edited. Skill: `/Users/dimabytes/.claude/skills/feature-json-step-review/SKILL.md`. Workspace `review.instructions` is only the comments-review assignment (`.feature-json.config.json:16-20`); this pass is the maintainability review.

## Findings

None.

## Why this shape holds

`gone` is a fifth `OrderStatus`, not a second order collection (`src/strategy/types.py:7`). That is the smaller model. A side table of unsettled orders would force every occupancy, episode, and requote scan to look in two places; the plan already forbids that collection (`plans/STEP-001.md:16`). Keeping the order in `state.orders` and refusing the transitions that would unstick it is the direct extension of the existing machine.

The new transitions sit next to the cancel path they mirror:

- `apply_cancel_unsettled` rewrites status through `_replace_order` and returns the same state for a missing id, a SELL, or an order already `gone` (`src/strategy/lifecycle.py:349-353`). The record stays non-terminal because `_replace_order` writes `terminal=False` (`src/strategy/lifecycle.py:165-170`).
- `apply_buy_settled` retires with the same `_drop_active` / `_free_rung` / `end_episode_if_idle` sequence as `apply_cancel_ack` (`src/strategy/lifecycle.py:356-365`, `src/strategy/lifecycle.py:341-346`). The wait is one comparison against `HALF_SHARE_TICK` (`src/strategy/lifecycle.py:362`). Extracting a two-line retire helper would be a wrapper.

Call sites that must not treat `gone` as a live quote each already owned that decision:

- `mark_canceling`, `apply_cancel_timeout`, and `_mark_restored` return the order unchanged (`src/strategy/lifecycle.py:304`, `src/strategy/lifecycle.py:370`, `src/strategy/lifecycle.py:376`). Kill, halt, cutoff, and reprice all cancel through `mark_canceling` (`src/strategy/quoting.py:88-96`, `src/strategy/quoting.py:108-119`, `src/strategy/quoting.py:600-603`), so one guard covers those paths. `already_canceling` stays false for `gone` (`src/strategy/lifecycle.py:292-299`), which is required: `_venue_cancels` retries every `already_canceling` order (`src/trader/session_core.py:821-828`).
- `_live_matches` rejects `gone` before price matching (`src/strategy/quoting.py:533-534`). That check has to live there; the unmatched loop only runs after a match fails.
- `reserve_buy_notional` drops `gone` and keeps the overfill floor (`src/strategy/budget.py:14-20`).
- `entry_stale_boundary_ns` no longer treats `gone` as an order with a pending stale-entry cancel (`src/strategy/scheduling.py:83-85`).
- `has_unresolved_orders` still lists the four venue-open statuses, so `gone` does not block `RecoveryVerified` (`src/strategy/lifecycle.py:392-397`).
- `_active_buy` includes `gone`, so the episode stays open (`src/strategy/lifecycle.py:203-207`).

Engine and scheduling dispatch are the existing match arms, not a new bus. `CancelUnsettled` and `BuySettled` apply in `_apply_fill_path` beside `CancelAck` (`src/strategy/engine.py:96-103`) and evaluate immediately beside it (`src/strategy/scheduling.py:129-139`). Decoders are the same `require_exact_keys` shape as `CancelTimeout`, registered by class name (`src/trader/core_trace_codec.py:620-633`, `src/trader/core_trace_codec.py:731-732`). Encoding stays the generic dataclass path.

`HALF_SHARE_TICK` moved to `shared.utils.trading` (`src/shared/utils/trading.py:16`). `wallet_store` imports it (`src/trader/wallet_store.py:17`), so the `engine_seams` import of that name still resolves. Lifecycle does not import trader. That is the dependency fix, not a new tolerance.

## File size

No file crosses 1000 lines. After the commit: `lifecycle.py` 771, `quoting.py` 931, `core_trace_codec.py` 837, `types.py` 519, `test_strategy_late_fills.py` 658. `quoting.py` was already near the line and this commit adds one condition (`src/strategy/quoting.py:534`).

## Considered and not requested

- Collapsing `_active_buy`'s status tuple. The tuple is now every `OrderStatus` (`src/strategy/lifecycle.py:205`), so it means the same as `side == "BUY"` for any order still in `state.orders`. Leaving the explicit list matches the surrounding predicates and the plan's "include gone" instruction (`plans/STEP-001.md:43`). A collapse would not delete a branch that changes behavior.
- A shared status-set module. The predicates are not copies: episode hold includes `gone` (`src/strategy/lifecycle.py:205`), venue-open excludes it (`src/strategy/lifecycle.py:395`), settlement excludes `pending` (`src/strategy/lifecycle.py:360`), reserve excludes `gone` (`src/strategy/budget.py:19`), stale-entry excludes `canceling` and `gone` (`src/strategy/scheduling.py:84`). One frozenset would hide those differences.
- Guarding `apply_accepted` (`src/strategy/lifecycle.py:315-323`) and `apply_submit_timeout` (`src/strategy/lifecycle.py:334-338`) so they cannot rewrite `gone`. `apply_accepted` already keeps `canceling` sticky, and a late accept or submit-timeout would put a `gone` BUY back into reserve, matching, and `already_canceling`. That guard is a new special case, and this step has no producer that can deliver it. `note_placed` enqueues `OrderAccepted` or `SubmitTimeout` in the same call that binds the venue id (`src/trader/session_core.py:576-587`); `note_cancel` still enqueues `CancelAck`, not `CancelUnsettled` (`src/trader/session_core.py:589-598`). The queue is FIFO, so accept lands before any later cancel. `SubmitTimeout` is the unmatched-place path, which never receives a venue id. The plan says to add a contrary-order branch only with a reachable sequence (`plans/STEP-001.md:88`). Adding one now is another `gone` if, not a simpler machine. STEP-004's producer has to keep that order: do not enqueue `CancelUnsettled` ahead of an already queued `OrderAccepted` for the same id.
- Changing `apply_cancel_ack`. It still retires whatever order it finds (`src/strategy/lifecycle.py:341-346`). Backtest still sends `CancelAck`. A `CancelAck` after `CancelUnsettled` would free the rung early. That is a producer contract for later steps, not a second retirement path to invent here (`plans/STEP-001.md:50`).
- Checkpoint restore still rejects `gone` (`src/trader/core_persistence.py:314-316`). That incompatibility is STEP-002, and the implementer handoff already names it (`reports/implementer-STEP-001.md:48-50`).

## Approval bar

No structural regression, no second abstraction, no file pushed over 1000 lines, no feature logic in a shared path that did not already own the decision, no new wrapper. The `gone` early returns are the mutators that would otherwise change the order. Tests cover the plan's cases in the existing late-fill, budget, scheduling, and trace modules; this pass did not re-run them.
