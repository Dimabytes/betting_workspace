# STEP-012 plan: two-sided merge delay and the 9 × $20 map cap (B6)

Repo `esports-trader` (C) on `main`, HEAD `b9904591`. All line numbers are at that commit.

## 0. Result

After this step a two-sided backtest merges pairs the way live wallet B does:

- On a feed tick, the merge starts only when held cost is at least `MERGE_AT_HELD_USDC` (130), at least `MERGE_MIN_PAIRS` (5) pairs are held, and the last fill the core heard on either token is at least `BUY_CONFIRM_MS` (7.25 s) old.
- The pairs counted at the start leave the core `MERGE_CALL_MS = 3_000` ms later, and the quoter wakes then.
- Only one merge is in flight at a time.
- A two-sided run defaults to `level_usdc = 20`, so the map cap is 9 × $20 like live B. `--level-usdc` still overrides it. Follow300 stays at `BACKTEST_LEVEL_USDC = 300`.

Files in `src`: `src/backtest/two_sided_core.py`, `src/backtest/live_latency.py`, `src/backtest/run.py`. Tests: `tests/test_backtest_two_sided_core.py`, `tests/test_backtest.py`. Follow300 code paths and the live trader do not change.

## 1. Evidence

### 1.1 Live B (what the sim copies)

- `PairMerger._threshold_ready` (`src/trader/pair_merge.py:122-127`): not paused, `_held_enough`, and `not store.has_unacked_matched({yes, no})`.
- `_held_enough` (`pair_merge.py:145-150`): `yes.size * yes.avg_price + no.size * no.avg_price >= MERGE_AT_HELD_USDC` and `min(sizes) >= MERGE_MIN_PAIRS`.
- `has_unacked_matched` (`src/trader/wallet_store.py:330-342`): true while any MATCHED fill on either token is not yet CONFIRMED. The sim already models MATCHED → CONFIRMED as `BUY_CONFIRM_MS` after the heard fill. `_ack_venue_cancel` uses `self._last_fill_ns[core_id] + BUY_CONFIRM_MS` (`src/backtest/strategy.py:1491-1497`, settle at `:1496`).
- `schedule()` runs on every feed event (`src/trader/match_worker.py:270-272`) and after a SELL fill (`:976-978`). Two-sided never sells, so only feed events matter. A running task makes `schedule()` a no-op (`pair_merge.py:107-109`).
- `_merge_pairs` (`pair_merge.py:152-180`) reads balances, then computes `amount_raw` once from positions at that moment (`:161`, `_amount_raw` `:295-302`). The tx carries that amount. `_book_pending` applies `pending.qty`, the start amount (`:213-220`), then wakes the quoter with `engine._wake_cid(self._cid)` (`:241`).
- Timing in `investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md:437`, from 33 merges in `live.db`: last fill → CONFIRMED 7.1 s (5.5–8.8 s), CONFIRMED → merge p50 3.4–5.4 s, threshold → merge p50 13.5 s. One outlier: 72 s on 9034882975. PLAN.md A8 traced it to a stale relayer nonce plus a 30 s pause, and STEP-003 fixed that.

### 1.2 The investigation model

`MergeDelayMixin` (`investigations/2026-10-08-liveb-postmortem/sim/replica/replica_repo.py:618-668`):

- Sets `_last_map_fill_ns = event.ts_event` in `on_order_filled`, using venue time (`:630-632`).
- `_merge_pairs` (`:639-651`) returns if a merge is in flight or the threshold is not met. It also returns if `now − last fill < BUY_CONFIRM_MS`. Otherwise it arms `MERGE_DONE:{match_id}:{now_ns}` at `now + MERGE_CALL_NS` (env `MERGE_CALL_MS`, default 3000).
- `_on_merge_done` (`:653-662`) calls `super()._merge_pairs(now_ns)`. That re-checks the threshold and merges the pairs held at the end of the call (its own `ponytail:` note says so). It also has a `_finished` guard and collects the `merge_delays_ns` and `MERGE_LOG` diagnostics.
- With `--level-usdc 20` this model gave `xf` 7560 shares against live's 7543 on 7 maps (`SIMULATOR-PLAN.md:423-430`).

### 1.3 The product today

- `TwoSidedCoreStrategy._on_signal_alert` (`src/backtest/two_sided_core.py:92-108`) calls `self._merge_pairs(now_ns)` at `:107`, then `self._wake_quoter(now_ns)`.
- `_merge_pairs` (`two_sided_core.py:123-154`) merges immediately. It drives `Merged` for both tokens, calls `_arm_wake`, and records a `kind="merge"` quote event. Its docstring says "the chain delay is not modeled" (`:124`).
- `_last_fill_ns: dict[str, int]` (`strategy.py:300`) is keyed by core order id. `_hear_fill` sets it to the heard time, print + `USER_WS_DELAY_MS` (`strategy.py:512`). It is never pruned, so its max value is the last fill the core heard on the map.
- `_finished` is never true for two-sided. Its only setter is `_mark_trading_done` (`strategy.py:864-873`), and two-sided overrides that as a no-op (`two_sided_core.py:180-181`).
- Two-sided never sells. `two_sided_quoting.py` plans no SELL, and live B refuses SELLs with `drop_sell=_refuse_sell` (`src/trader/two_sided_worker.py:131`). So per-token inventory only grows between merges.
- Level resolution: `level_usdc = BACKTEST_LEVEL_USDC[game] if args.level_usdc is None else args.level_usdc` (`src/backtest/run.py:2301`), with `BACKTEST_LEVEL_USDC = {"dota": 300.0, "lol": 300.0}` (`run.py:250`). From there it flows into `resolve_run_policies` (`:2303-2308`), then `build_kernels`, then `build_two_sided_kernel`, which reads `cadence.level_usdc` (`two_sided_core.py:50-71`). It also reaches the manifest `layer_usdc` (`run.py:808`), which is part of the resume fingerprint (`src/backtest/results.py:454-470`).
- The cap is `max_position_levels * level_usdc − held_cost − reserved` (`src/strategy/budget.py:34`). `BACKTEST_DOTA_MAX_POSITION_LEVELS = 9` (`src/shared/constants/strategy.py:29`). Today a two-sided run's cap is 9 × 300. At 20 it is $180, and a merge at $130 is what frees room.

## 2. Changes

### 2.1 `src/backtest/live_latency.py`

Add `MERGE_CALL_MS = 3_000` after `BUY_CONFIRM_MS` (`:17`). It is a plain constant: no env read like the mixin had.

Add one `ponytail:` line on it naming the ceiling: the call is a fixed 3 s; live CONFIRMED → merge runs p50 3.4–5.4 s with a tail (failed relayer post, stale chain read, `_chain_lock`). The upgrade path is a draw from the measured merge-timing distribution. Add no other comments.

### 2.2 `src/backtest/two_sided_core.py`

- Imports: `partial` from `functools`, `TimeEvent` (already imported, `:11`), `BUY_CONFIRM_MS` and `MERGE_CALL_MS` from `backtest.live_latency`, and `NS_PER_MS` from `strategy.scheduling`.
- Add a module constant `MERGE_DONE_PREFIX = "MERGE_DONE:"`. Tests fire the alert through it, like `QUOTER_PREFIX` and `PLACE_AT_PREFIX` in `strategy.py:140-144`.
- `__init__` (`:81-87`): add `self._merge_inflight = False`.
- `_merge_pairs(self, now_ns)` (`:123-154`). Keep the name: `_on_signal_alert` calls it, and the investigation scripts override it to switch merges off (`replica_repo.py:322-323`, `strategy_check/live_net.py:56-57`). Its new body:
  1. If `_merge_inflight`, return.
  2. Compute `pairs` and `held_usdc` as today (`:125-127`). If below the threshold, return.
  3. Take `max(self._last_fill_ns.values(), default=0)` as the last heard fill. If `now_ns −` that `< BUY_CONFIRM_MS * NS_PER_MS`, return. Check the threshold first, so the max scan runs only when a merge is possible.
  4. Set `_merge_inflight = True`. Arm `self.clock.set_time_alert_ns(name=f"{MERGE_DONE_PREFIX}{self._config.match_id}:{now_ns}", alert_time_ns=now_ns + MERGE_CALL_MS * NS_PER_MS, callback=partial(self._on_merge_done, pairs))`. `partial` follows `strategy.py:703-707` (`_on_delayed_place`). It carries the start pairs without an `Optional` field and without a dead `None` branch.
  - Delete the docstring at `:124`. It becomes false, and `_on_signal_alert`'s docstring already says the merge check runs on each feed tick.
- New `_on_merge_done(self, pairs: float, event: TimeEvent) -> None`:
  1. `now_ns = int(event.ts_event)` and `_merge_inflight = False`.
  2. Move today's body from `:130-154` here with `qty=pairs`: `Merged` for both tokens, `_arm_wake`, and the `record_quote_event(kind="merge", ts_ns=now_ns, quantity=pairs, …)`.
  3. `self._wake_quoter(now_ns)`, like live `_wake_cid` (`pair_merge.py:241`). Without it, freed cap room waits for the next book row or feed tick.
  - Do not re-check the threshold and do not add a `_finished` guard. Live applies the merged tx no matter what. `_finished` cannot be true here (§1.3).
- Drop the mixin's `merge_delays_ns`, `_merge_ready_ns`, and `MERGE_LOG`. They are investigation diagnostics.

`build_two_sided_kernel` (`:50-71`) does not change. It keeps passing `kernel.policy.level_usdc`, which is 20 once §2.3 resolves the default.

### 2.3 `src/backtest/run.py`

- Add `BACKTEST_TWO_SIDED_LEVEL_USDC = 20.0` next to `BACKTEST_LEVEL_USDC` (`:250`).
- Add `resolve_level_usdc(game: Game, *, two_sided: bool, override: float | None) -> float` right after `resolve_max_position_levels` (`:253-256`), in the same shape:
  - an override wins;
  - otherwise two-sided returns `BACKTEST_TWO_SIDED_LEVEL_USDC`;
  - otherwise `BACKTEST_LEVEL_USDC[game]`.
- `main` (`:2301`): `level_usdc = resolve_level_usdc(game, two_sided=args.strategy == "two-sided", override=args.level_usdc)`.
- `--level-usdc` help (`:1472-1477`): name both defaults, `BACKTEST_LEVEL_USDC` for `--game` and 20 with `--strategy two-sided`.
- `_stamp_strategy` (`:1636-1654`): add `"merge_call_ms": MERGE_CALL_MS` next to `"quoter_recompute_ms"`. A two-sided run started before this step then refuses `--resume` and shard merges, instead of mixing instant and delayed merges. Import `MERGE_CALL_MS` in the `backtest.live_latency` block (`:64-72`).

Why not set 20 inside `build_two_sided_kernel` (the literal wording of feature.json and port.md option 5):

- The manifest `layer_usdc` and the resume fingerprint read the run policy (`run.py:808`), so they would still say 300.
- `--level-usdc` could not override it without a new `TwoSidedSettings` field.

Resolving once where `--level-usdc` is read keeps one source of truth. The kernel, the budget, and the manifest all see 20.

## 3. Edge cases

- **No fill yet.** `default=0` keeps the gate open, since real timestamps are epoch ns, far above 7.25 s. Today's test merges with no fills at all, and that stays true.
- **Fills keep landing less than 7.25 s apart.** The merge waits. Held cost climbs to the $180 cap, `position_cap` stops new bids, fills stop, and the next feed tick at least 7.25 s after the last fill starts the merge. This is the live window the step exists for.
- **A fill lands during the 3 s call.** It does not join this merge, as in live `_PendingMerge.qty`. Inventory only grows between merges (§1.3), so `Merged(qty=pairs)` never takes more than is held. `_credit_sell_inventory` (`src/strategy/lifecycle.py:518-540`) prices the merged shares at the average cost at done time, as live `apply_merge` does.
- **A feed tick during the call.** `_merge_inflight` blocks it, and no second alert is armed. Alert names stay unique because `now_ns` grows and only one alert is in flight.
- **A fill younger than 7.25 s but not heard yet** (venue fill, WS print still ahead). The gate cannot see it, and neither can live `has_unacked_matched` before `user_trade`. This is the same clock as `_ack_venue_cancel`.
- **Game end.** A merge that starts on the last feed tick can finish up to 3 s after `game_end_ns`, or not at all if the engine stops first. Either way pairs pay $1 by merge or by resolution: `results.py:213-224` subtracts `merge_shares` from leftover settlement. No guard is needed.
- **Follow300.** `DotaMakerStrategy` has no `_merge_pairs`. A follow300 run's level is still `BACKTEST_LEVEL_USDC[game]`.

## 4. Tests

In `tests/test_backtest_two_sided_core.py`, import `MERGE_DONE_PREFIX` from `backtest.two_sided_core` and `MERGE_CALL_MS` from `backtest.live_latency`.

1. **Rewrite `test_merge_at_held_usdc_and_pairs` (`:464-481`).** Use ticks at 0, 1 s, and 2 s.
   - Small inventory (held $120): a feed tick at 0 arms no `MERGE_DONE_PREFIX` alert, and inventory is unchanged.
   - Held inventory 200 @ $80 + 230 @ $92: a feed tick at 1 s arms exactly one `MERGE_DONE_PREFIX` alert at `T0 + 1 s + MERGE_CALL_MS * MS`. Inventory is still `[200, 230]`, and there are no `merge` quote events yet.
   - A feed tick at 2 s still leaves exactly one such alert (in-flight guard).
   - Bump token 0 to 260 @ $104, as if a fill landed during the call. Spy on `strategy._wake_quoter` with `monkeypatch.setattr` to record `now_ns`.
   - `fire_due_alerts(strategy, prefix=MERGE_DONE_PREFIX, until_ns=T0 + 1 s + MERGE_CALL_MS * MS)`.
   - Then inventory is `[60.0, 30.0]`, the merge events are `[(T0 + 1 s + MERGE_CALL_MS * MS, 200.0)]`, and the spy recorded the done time.
2. **New `test_merge_waits_until_the_last_heard_fill_confirms`.**
   - Reuse the sequence of `test_partly_filled_cancel_settles_when_its_fills_confirm` (`:297-307`): `_feed_tick(0)`, `_cycle(100 ms)`, `accept_all(T0 + 200 ms)`, then `fill_order(yes, 5, 0.44, is_buy=True, ts_event=T0 + 500 ms)`.
   - Set the held inventory from test 1 with `replace`.
   - Let `heard = T0 + 500 ms + USER_WS_DELAY_MS * MS`.
   - A feed tick at `heard + BUY_CONFIRM_MS * MS − 1 ms` arms no `MERGE_DONE_PREFIX` alert.
   - A feed tick at `heard + BUY_CONFIRM_MS * MS` arms one, due `MERGE_CALL_MS * MS` later.
   - This pins the heard-fill clock and the `>=` edge, the same edge `_ack_venue_cancel` settles on.
3. **New `test_resolve_level_usdc_defaults_two_sided_to_the_live_b_clip`** in `tests/test_backtest.py`, next to `test_resolve_max_position_levels_follows_the_game` (`:548-554`):
   - `resolve_level_usdc("dota", two_sided=False, override=None) == BACKTEST_LEVEL_USDC["dota"]` (300);
   - `two_sided=True, override=None` gives `BACKTEST_TWO_SIDED_LEVEL_USDC` (20.0);
   - `override=35.0` gives 35.0 for both.

Unchanged and expected green:

- `test_backtest_feeds_the_core_like_live` (`:594-620`): its tape never reaches $130, so no merge is armed.
- Every follow300 test.

## 5. Out of scope

- Live `pair_merge.py`, `wallet_store.py`, and anything in `src/trader`.
- Merge failures, pauses, `_chain_lock`, the final `merge_all` at game end, and the report header showing the two-sided cap (`report.py:113-145` shows `size_shares`, not `layer_usdc`).
- Investigation scripts. STEP-013 owns `live_net.py` and the runs.
- Any backtest run. The B ensemble and `xf` comparison are STEP-013.
- The golden follow300 replay. It stays until STEP-014; ignore `tests/test_follow300_replay.py`.

## 6. Risks and hard parts

- **Two deliberate differences from the mixin.** The product merges the pairs held at the start, not the end, and gates on the heard fill (print + 15 ms), not venue `ts_event`. Both follow live. They can move STEP-013's numbers slightly against the `xf` 7560 reference. The first matters only when a fill lands inside a 3 s call right after a 7.25 s quiet gap.
- **Stacking hazard for STEP-013.** `MergeDelayMixin` on top of the new class would add a second 3 s delay. Its `_on_merge_done` would shadow the product's and re-enter `_merge_pairs` forever, so merges never apply. `MergeNoticeHoldsStrategy` and `MergeSyncStrategy` (`replica_repo.py:671-676`) must not be used after this step. STEP-013 already says "product code without mixins". The `_merge_pairs → return` overrides in `LivePositionMixin` and `LiveNetStrategy` keep working, because `_merge_pairs` is still the feed-tick entry.
- **Known ceiling.** The fixed 3 s call has no tail (see the `ponytail:` line in §2.1). Record it in progress.txt too.
- **`max` over `_last_fill_ns`.** It is O(filled orders) per feed tick and runs only above the threshold. That is negligible at hundreds of orders and a few hundred ticks per map.

## 7. Verification commands

Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md), and do not set `PYTEST_N` on the VPS. This step needs no backtest, training, or data rebuild.

```bash
# targeted: two-sided core and the run CLI/manifest
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_two_sided_core.py tests/test_backtest.py

# the new and changed tests by name (expect 3 selected, all pass)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_backtest_two_sided_core.py tests/test_backtest.py \
  -k "merge_at_held_usdc_and_pairs or last_heard_fill_confirms or resolve_level_usdc"

# full suite without the golden replay: expect 0 failed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck: expect 0 errors
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/two_sided_core.py src/backtest/live_latency.py \
  src/backtest/run.py tests/test_backtest_two_sided_core.py tests/test_backtest.py

# leftovers: first expect no output; second expect hits only in live_latency.py, two_sided_core.py, run.py
rg -n "chain delay is not modeled|MERGE_LOG|merge_delays_ns" src
rg -n "MERGE_CALL_MS" src
```

Commit only the five touched paths with `git add <paths>`. Use the message `fix: STEP-012 - delay two-sided merges until fills confirm and default the two-sided rung to $20`. Then append the STEP-012 entry to `W/tasks/sim-parity-fixes/progress.txt`: the commit SHA, the test counts and times, and the ceiling from §6. Leave `passes` false.
