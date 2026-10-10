# STEP-004 plan: a held SELL moves to the ask when the fair goes stale (A5)

Code: `esports-trader` `main` at `e0422758` (clean `git status`). Paths below are relative to `esports-trader/` unless they start with `work/`. Planner scratch (a `git archive` copy of HEAD, patched; logs; mutation harness; tape captures; reference diffs): `betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-004/` (below: `work/plan-004/`). Reference diffs: `work/plan-004/step004_src.diff` and `work/plan-004/step004_tests.diff`. Both apply cleanly to `e0422758` (`git apply --check`).

No Figma links: the step has no `designReference`. `progress.txt` has STEP-001..003. None of them touches `src/strategy/quoting.py` latch code; STEP-001 changed `_sell_quantity` (`quoting.py:462-466`), which this step reuses unchanged.

## 0. Result in one paragraph

`_quote_v5_latch` (`src/strategy/quoting.py:791-825`) has two branches that call `_blocked`, which cancels every order and places nothing. One is the silent feed (`:800-801`, reason `stale_signal`). The other is a fair that is invalid or older than 45 s (`:814-816`, reason `anchor` or `stale_signal`). Both now go through one new private helper, `_sell_at_ask`. With no position (`total_qty(inventory) == 0`) it calls `_blocked`, as today. With a position it calls the existing `_reconcile_choice` with `fair=None`, `allow_buy=False` and the same reason. That cancels BUYs only and prices the SELL through `choose_targets → decide_sell(fair=None) → _join_ask_sell` (`quoting.py:527-550`, `:503-524`, `:469-479`), the same SELL code `_recovery_exit` uses (`:894-910`). Kill gate, `min_life`, `unconfirmed_hold` and settling therefore behave exactly as in recovery. Three docstrings or comments that say "pull the SELL" are corrected. Size: +18/−2 in `quoting.py`, +2/−2 in `match_worker.py`, +1/−1 in `shared/constants/strategy.py`; tests +165/−47 in 3 files, 14 new test items. No schema, config, backtest-adapter, two-sided or `poly-maker` change. Verified on a scratch copy:
- The target files: 308 passed (294 on HEAD with HEAD tests). With HEAD `quoting.py`, 12 of the new or rewritten items fail.
- The full suite without the golden replay: 3248 passed. The same 13 environment-only failures appear as on HEAD scratch.
- basedpyright reports 0 errors; ruff check and format are clean.
- Five mutations (HEAD source, each branch reverted, guard dropped, holds bypassed) are each caught (§4.5).
- Golden smoke tapes, STEP-004 alone (HEAD vs patched source, same scratch): 2 of 8 maps move, 6 are byte-identical (§5.2).

## 1. What happens today (evidence)

### 1.1 Two branches pull the SELL and place nothing

- Silent feed. `_sync_latch` drops the latch when the signal is missing or older than `exit_stale_s` (`quoting.py:760-761`). `_quote_v5_latch` then returns `_blocked(reason="stale_signal")` (`:800-801`). `_blocked` (`:100-105`) calls `_cancel_orders(buys_only=False)` (`:88-97`) and an empty plan.
  - Live: `_on_exit_feed_timeout` (`src/trader/match_worker.py:1079-1084`) enqueues `SignalUpdate(signal=None)` after `EXIT_FEED_STALE_SECONDS = 45.0` (`src/shared/constants/strategy.py:55`, armed at `match_worker.py:195-200`) and wakes the quoter.
  - Backtest: the signal simply ages past 45 s, or `_clear_signal` sends `signal=None` (`src/backtest/strategy.py:1131-1162`).
- Stale fair. Since STEP-010 of `live-a-audit-fixes`, a failed anchor keeps the previous `fair_ts_ns` (`quoting.py:704-719`). After 45 s of failures `fair_fresh` is False, and `:814-816` returns `_blocked(reason="anchor")`. The same line also catches `fair_valid=False`: a first signal that fails the anchor with no prior valid fair, for example after a feed gap dropped the latch.
- Recovery already does what the step asks. `_recovery_exit` cancels BUYs, takes `_recovery_fair` (None when the fair is invalid or stale, `:885-891`) and calls `decide_sell(fair=None)`. `decide_sell` returns `_join_ask_sell` (`:522-523`). Test: `test_recovery_joins_ask_without_fresh_signal_then_restores_fair` (`tests/test_strategy_recovery_quotes.py:134-160`).

### 1.2 The risk is real in the backtest

Smoke golden `tests/fixtures/follow300_changes/smoke/dota_8837869969_seed0.json`, `place_cancel` events 29 and 74: a 600-share SELL is pulled with `stale_signal`. The next SELL is submitted 599.04 s and 29.08 s later. The other 7 goldens have no such pull. Script and output: `work/plan-004/golden_stale_sells.py`, `golden_stale_sells.out`.

## 2. Design decisions

1. **Reuse `_reconcile_choice`, not a new SELL path.** `_reconcile_choice(fair=None, allow_buy=False, latch_reason=reason)` (`quoting.py:828-863`) already does everything the step needs:
   - `choose_targets` skips BUYs when `fair is None` (`:539`) and calls `decide_sell(fair=None)` (`:548`), which reaches `_join_ask_sell` (`:522-523`).
   - With SELL targets only, it cancels BUYs with the reason (`:858-859`), reconciles, and sets `block_reason` to the reason while a position is held (`:860-862`).
   - With no target and a position, it cancels BUYs only (`:853-856`).
   - Not `_recovery_exit` itself: it labels cancels and the plan `recovery`, resyncs the latch again and calls `end_episode_if_idle`, which `requote` already did (`:949`).
2. **No position: `_blocked` first, exactly as today.** `decide_sell` can return a held SELL even on zero inventory: `unconfirmed_hold` and `_min_life_hold` return `_held_sell_target` before any quantity check (`:516-521`). Without the guard, `_reconcile_choice` would keep that SELL. The guard is one line, mirrors `:805`, `:809` and `:854`, and keeps "без позиции — _blocked, как сейчас" literal. `test_kill_gate_inactive_without_signal` (`tests/test_kill_gate.py:258-273`, inventory 0 with two resting SELLs) is the test that fails without it (§4.5).
3. **Both branches, all three triggers.** The step names `anchor` and `stale_signal`. Line `:814-816` also fires on `fair_valid=False`. It goes to the ask too: there is no fair at all, `_recovery_fair` treats it the same way (`:887`), and otherwise a position would sit without a SELL whenever the feed returns but keeps failing the anchor. Consequence: `test_failed_anchor_without_a_valid_fair_places_no_sell` (`tests/test_strategy_anchor_age.py:67-79`) is a third test to rewrite, beyond the two the step names.
4. **Reasons and labels.** `block_reason` stays `stale_signal` / `anchor`. BUY cancels carry that reason. A resting SELL at another price gets a `reprice` cancel, and the ask SELL is placed only after that cancel is acked, because `sell_occupied` holds the slot (`src/strategy/lifecycle.py:260-275`). This is the same two-cycle reprice recovery already does. A SELL already at the ask is kept.
5. **No new wake boundary.** The move happens at the next evaluation, exactly when today's pull happens. Live wakes on the exit timeout (`_wake_cid`, `match_worker.py:1084`). The anchor path re-evaluates on every new signal and book row (dirtying events, `src/strategy/scheduling.py:46-68`) and on the fallback timer.
6. **Live layers do not block the SELL.**
   - `_on_exit_feed_timeout` also clears the cell. `cell.forced` makes the regime `REDUCE_ONLY` (`src/trader/session_engine.py:84-85`), which becomes `permissions.reduce_only` (`src/trader/session_core.py:253`). That only closes BUYs (`quoting.py:215-216`).
   - `_drop_sell` drops only SELLs the wallet cannot cover (`match_worker.py:1175-1187`).
7. **Scope stays narrow.** Other full pulls are not changed: `halt`, `paused`, `game_end` (`:933-938`), `stale_book` and `pair_tolerance` (`:877-881`). `mid_spike` already keeps SELLs (`:943-945`). B is untouched: `step` routes `TwoSidedPolicy` to `requote_two_sided` (`src/strategy/engine.py:137-140`). The backtest adapter needs no change: it drives the same `step` (`src/backtest/strategy.py:658`).
8. **Comments.** Three existing docstrings or comments become false and are edited in place (§3.2, §3.3). No new comment or docstring is added. The two rewritten backtest tests drop their docstrings, per the review config.

## 3. Changes

### 3.1 `src/strategy/quoting.py`

In `_quote_v5_latch`, replace both `_blocked` returns:

```python
    if not _signal_fresh(state, now_ns, state.freshness.exit_stale_s) or latch is None:
        return _sell_at_ask(state=state, policy=policy, now_ns=now_ns, reason="stale_signal")
    ...
    if not latch.fair_valid or not fair_fresh:
        reason = "anchor" if latch.no_buy_reason == "anchor" else "stale_signal"
        return _sell_at_ask(state=state, policy=policy, now_ns=now_ns, reason=reason)
```

New helper, placed between `_quote_v5_latch` and `_reconcile_choice` (patched lines `828-841`):

```python
def _sell_at_ask(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int, reason: BlockReason
) -> tuple[StrategyState, Plan]:
    if total_qty(state.inventory) == 0:
        return _blocked(state=state, reason=reason)
    return _reconcile_choice(
        state=state,
        policy=policy,
        now_ns=now_ns,
        fair=None,
        follow_book=False,
        allow_buy=False,
        latch_reason=reason,
    )
```

`follow_book` only feeds BUY targets (`_choose_buy_targets`), so `False` is inert here. No import changes: `BlockReason`, `Follow300Policy`, `Plan`, `total_qty` are already imported (`:24`, `:39-53`).

### 3.2 `src/trader/match_worker.py` (docstrings only)

- `:1080` `"""Feed silent long enough: drop fair so quoting pulls the SELL."""` → `"""Feed silent long enough: drop fair so quoting moves a held SELL to the ask."""`
- `:1074` (in `_on_entry_feed_timeout`) `here would also expire the v5 exit gate and drop the resting SELL early.` → `here would also expire the v5 exit gate and move the resting SELL to the ask early.`

### 3.3 `src/shared/constants/strategy.py` (comment only)

`:52` `# Pull a resting SELL only after this long with no yielded tick. Entry stays on` → `# Move a resting SELL to the ask only after this long with no yielded tick. Entry stays on`. The rest of the comment (`:53-54`) is unchanged; the line is 90 columns, ruff accepts it.

### 3.4 Not changed

`decide_sell`, `_join_ask_sell`, `_recovery_exit`, `_rebuild_latch`, freshness limits (`exit_stale_s=45`), anchor tolerance, the kill gate, scheduling, `LiveCore`, the backtest adapter, two-sided, checkpoint schema, `poly-maker`. Goldens (`tests/test_follow300_replay.py`) stay untouched until STEP-014.

## 4. Tests

Exact text: `work/plan-004/step004_tests.diff` (+165/−47). Strict basedpyright and ruff pass on all three files.

Map from the step's test bullets:

| Step bullet | Test |
|---|---|
| Rewrite the anchor-streak test | `test_failed_anchor_streak_moves_the_frozen_fair_sell_to_the_ask_past_exit_age` |
| Rewrite the c31 test | `test_stripped_book_anchor_failure_moves_sell_c31_to_the_ask_past_exit_age` |
| Position, 46 s of anchor failures: BUYs cancelled, SELL at join ask | c31 test, 46 s case |
| Feed silent 46 s with a position: SELL at the ask | `test_silent_feed_moves_the_sell_to_the_ask_then_restores_fair` |
| No position: `_blocked` in both cases | `test_stale_fair_without_a_position_cancels_every_order`, plus the existing `test_kill_gate_inactive_without_signal` |
| A working anchor returns: SELL priced from fair again | the 50 s step in the streak and silent-feed tests |
| Kill gate, min_life, unconfirmed_hold, settling as in recovery | `test_stale_fair_sell_keeps_the_recovery_holds` |

### 4.1 `tests/test_strategy_anchor_age.py` (5 → 19 items)

Imports gain `_buy_places` from `test_strategy_core` and `BookUpdate, KillGate, KillGateUpdate, KillWait, SignalUpdate` from `strategy.types`. New helpers:
- `_rest_sell_at_fair(*, recovery)`: valid fair 0.56, held YES 100, SELL `c0` 0.56 accepted at 0, optional `Recovery` at 1 s. This is the setup that used to sit inside the streak test.
- `_restore_fair(state, now_ns)`: a valid signal plus acks. Used for the "fair comes back" step.
- `_apply_hold(state, hold, now_ns)`: sets `accepted_ns = now − 0.5 s`, `unconfirmed_keys=("f0",)`, `last_buy_ns = now`, or a `KillGateUpdate` with a pending dire kill (sell-blocks token 0).

Items:
1. `test_repeated_failed_anchor_keeps_the_fair_age`: unchanged.
2. `test_failed_anchor_without_a_valid_fair_sells_at_the_ask[1|2]` (was `..._places_no_sell`). It holds NO 100 instead of YES, because a NO SELL priced off the fabricated fair 0.0 would be token fair 1.0, which means no SELL. So a SELL at the NO ask 0.50 can only come from the ask. Asserts `fair_valid False`, `fair_radiant 0.0`, SELL prices `(0.50,)`, `block_reason "anchor"`.
3. `test_failed_anchor_streak_moves_the_frozen_fair_sell_to_the_ask_past_exit_age[recovery False|True]`: the `pull_reason` / `sell_after_pull` parameters go away because both paths now match. At 20 s and 44 s there are no cancels. At 46 s cancels are `[("c0", "reprice")]`, and after the ack the SELL is at 0.52. At 50 s a valid signal brings `fair_ts_ns == 50 s` and the SELL back to 0.56.
4. `test_silent_feed_moves_the_sell_to_the_ask_then_restores_fair[missing_signal False|True]`: at 46 s the signal either ages out (backtest) or arrives as `SignalUpdate(signal=None)` (live `_on_exit_feed_timeout`). Asserts latch None, `block_reason "stale_signal"`, `[("c0", "reprice")]`, SELL 0.52 after the ack, then 0.56 at 50 s.
5. `test_stale_fair_without_a_position_cancels_every_order[stale_signal|anchor]`: three accepted BUYs, no inventory. At 46 s a failing anchor arrives, plus `signal=None` in the stale case. Every order is `canceling` with that reason, `places == ()`, and `block_reason` is that reason.
6. `test_stale_fair_sell_keeps_the_recovery_holds[recovery × hold]`, 10 items, each through the 46 s anchor path. After the acks:

| hold | kept | cancels | SELLs after acks |
|---|---|---|---|
| none | `[]` | `[("c0", "reprice")]` | `(0.52,)` |
| kill | `[]` | `[("c0", "kill")]` | `()` |
| min_life | `["c0"]` | `[]` | `(0.56,)` |
| unconfirmed | `["c0"]` | `[]` | `(0.56,)` |
| settling | `[]` | `[]` | `(0.56,)` |

   The recovery and non-recovery rows must be equal, which is the "как в recovery" check. The kill hold uses the anchor path, not the silent one: `kill_victims` needs a signal (`src/strategy/kill_gate.py:29-31`).

### 4.2 `tests/test_trader_session_core.py`

`test_stripped_book_anchor_failure_pulls_sell_c31_past_exit_age` (`:844-876`) becomes `..._moves_sell_c31_to_the_ask_past_exit_age` with a fourth parameter, `sells_after_acks`. At 44 s it keeps `["c31"]` and cancels the three BUYs with `anchor`; after the acks the SELL is still at `[0.64]`. At 46 s it keeps `[]` and cancels the three BUYs with `anchor` plus `("c31", "reprice")`; after the acks there is one SELL at `[0.60]`, the stripped NO ask (mirror of YES bid 0.40). The new tail acks every plan cancel with `CancelAck` (already imported, `:37`) and asserts the remaining orders are one SELL.

### 4.3 `tests/test_backtest_maker.py`

- `test_grid_exit_timeout_clears_latch_and_sell` (`:2005-2028`) → `test_grid_exit_timeout_clears_latch_and_keeps_the_sell_at_the_ask`. At 46 s the latch is None, the SELL from 3 s is at 0.56 (the radiant ask), nothing new is submitted, and `awaiting_cancel is False`. Today it is cancelled with `stale_signal`.
- `test_anchor_failure_does_not_sell_on_a_fabricated_fair` (`:2900-2919`) → `test_anchor_failure_without_a_valid_fair_sells_at_the_ask`. The setup is unchanged; it asserts exactly one order, `(RADIANT_ID, OrderSide.SELL, 0.14, 50.0)` (the radiant ask).

### 4.4 Existing tests that pin the change, unchanged

- `test_kill_gate_inactive_without_signal` (`tests/test_kill_gate.py:258-273`) pins the no-position guard.
- `test_recovery_joins_ask_without_fresh_signal_then_restores_fair` (`tests/test_strategy_recovery_quotes.py:134-160`) is the recovery twin of item 4.
- `test_extraction_oracle.py` still passes. Its map goldens are static; the compact scenarios last 1 s and never reach a 45 s stale fair.

### 4.5 Fail-before evidence (scratch)

`work/plan-004/mutate.py` swaps one variant into the patched `quoting.py` and runs the six target files (`test_strategy_anchor_age`, `test_trader_session_core`, `test_backtest_maker`, `test_kill_gate`, `test_strategy_recovery_quotes`, `test_strategy_core`), then restores. Output: `work/plan-004/mutate.out`.

| Variant | Failures |
|---|---|
| HEAD `quoting.py` | 12 (anchor-age 9, c31 46 s, both backtest tests) |
| guard dropped | 1 (`test_kill_gate_inactive_without_signal`) |
| `:801` back to `_blocked` | 3 (silent feed ×2, backtest exit timeout) |
| `:816` back to `_blocked` | 9 |
| ask path ignores kill, min_life, unconfirmed, settling | 8 (holds table, both recovery modes) |

## 5. Behavior changes and risks

1. **Live A.** A position no longer sits without a SELL. After 45 s of anchor failures, after 45 s of feed silence, or when a returning feed keeps failing the anchor, the SELL rests at the join ask and follows it. Recovery has done this for a long time. In the audit the longest anchor streak was 17.7 s (PLAN.md A5). Feed silence of 45 s with a position was not measured.
2. **Backtest (the core is shared).** STEP-004 alone, HEAD vs patched source on the same scratch, all 8 smoke maps seed 0 (`work/plan-004/capture_tapes.py`, `compare_tapes.out`, `divergence_8837869969.out`):
   - `dota_8837869969`: engine PnL 272.87 → 230.67 (−42.20). BUY fills 19 → 21, SELL fills 34 → 39. HEAD pulled the SELL of 600 shares bought at 0.50 at 38.6 s and held them unhedged until about 637 s. Patched sells them at the ask, 0.54 and 0.53, within 25 s, and gives up the later rise.
   - `dota_8933879286`: −61.96 → −57.46 (+4.50). BUY fills 12 → 13, SELL fills 23 → 24.
   - The other 6 maps are byte-identical.

   This is the "плата за гарантированный выход" in PLAN.md A5. Seed 0 on two maps says nothing about the average. The full validation is STEP-014. Goldens already drift since STEP-001 and stay ignored until then.
3. **The kill gate is inert during feed silence.** `kill_victims` returns empty when `state.signal is None` (`kill_gate.py:29-31`). A SELL at the ask can therefore rest on the killer token while a board kill is pending. Today the SELL is pulled, so there is no exposure. Recovery already has this gap. Upgrade path, out of scope: keep the last signal's death counts for the gate when the feed goes silent.
4. **Price.** The SELL joins the ask passively and never crosses the spread. It can sell below the true fair just before a rise (risk 2 shows it). `min_life` (1 s) bounds reprice churn, as in recovery.
5. **Dust.** With inventory above 0 but below `min_order_size`, `_join_ask_sell` returns None. A partially filled SELL over dust inventory then stays instead of being cancelled. This is the same rule as the normal fair path (`:853-856`) and `inventory_is_dust` (`lifecycle.py:256-257`).
6. **Old core traces.** A replay of a trace recorded before this change reports a plan mismatch at the first stale fair with a position. Replay at the trace's own commit stays exact. This is the same class as STEP-010's accepted latch digest mismatch.
7. **Naming.** `_reconcile_choice` calls its parameter `latch_reason`, but `stale_signal` is not a latch reason. The parameter is not renamed, to keep the diff small.

## 6. Verification

Run from `esports-trader/` on `main`. No live map running (AGENTS.md). Do not set `PYTEST_N` on the VPS.

```bash
# targeted (scratch: 308 passed in ~2.6 s; HEAD has 294)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_strategy_anchor_age.py tests/test_trader_session_core.py tests/test_backtest_maker.py \
  tests/test_kill_gate.py tests/test_strategy_recovery_quotes.py tests/test_strategy_core.py

# full suite without golden replay ("Tests pass" for STEP-001..013).
# scratch: 3248 passed + the 13 .env-only failures in 25 s; impl-003 saw 3247 passed in this checkout, so expect 3261 passed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors, 0 warnings, 0 notes)
uv run python -m basedpyright

# ruff check + format + repo hooks on the touched files
uv run python -m pre_commit run --files src/strategy/quoting.py src/trader/match_worker.py \
  src/shared/constants/strategy.py tests/test_strategy_anchor_age.py tests/test_trader_session_core.py \
  tests/test_backtest_maker.py

# no new comments: expect exactly one line, the edited constants comment (src/shared/constants/strategy.py:52)
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'
```

The 13 scratch-only failures (`test_trader_wallet_host.py` ×12, `test_trader_unsettled_buy_recovery.py::test_user_stream_is_bound_on_the_host_run_path`) need `.env`. They are identical on HEAD scratch (`work/plan-004/base.failed` = `patch.failed`) and pass in the real checkout.

Not run in this step: `tests/test_follow300_replay.py` (STEP-014 rewrites it), any backtest, training, or data rebuild. The tape comparison in §5.2 took 42 s on scratch and is evidence only; the implementer does not need it.

## 7. Commit

Paths: `src/strategy/quoting.py`, `src/trader/match_worker.py`, `src/shared/constants/strategy.py`, `tests/test_strategy_anchor_age.py`, `tests/test_trader_session_core.py`, `tests/test_backtest_maker.py`. Add them by name, not with `-A`.

Message, in the style of earlier steps: `fix: STEP-004 - move a held SELL to the ask when the fair goes stale`.

Do not push. Do not set `passes`.
