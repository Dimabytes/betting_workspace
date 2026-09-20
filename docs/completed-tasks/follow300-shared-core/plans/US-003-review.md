# US-003 review — backtest adapter and extraction identity

Scope: commit `7b2b65b` vs parent `95168c6`. Files: `src/backtest/strategy.py`, `src/backtest/extraction_identity.py`, `src/strategy/{engine,lifecycle,quoting}.py`, `tests/test_extraction_oracle.py`, `tests/test_strategy_core.py`. 1069 insertions, 1101 deletions.

Checks I ran myself:

- `pytest tests/test_extraction_oracle.py tests/test_backtest_maker.py tests/test_strategy_imports.py tests/test_strategy_core.py -k "not test_seed0_map_replay"` → 127 passed, 4 deselected.
- All four identity maps in **one** pytest process → 4 passed in 23.7s. The subprocess isolation works; the impl report's "never run four kernels in one process" caution is now handled by the code, not by the operator.
- `ruff check` clean, `basedpyright` 0 errors on `src/backtest/strategy.py`, `src/backtest/extraction_identity.py`, `src/strategy`, `tests/test_extraction_oracle.py`.

`src/backtest/strategy.py`: 1560 → 1216 lines. It did not cross the 1k line; it came down from above it and stopped short of the plan's own target.

## 1. Verdict

**REQUEST CHANGES.**

The step's stated bar is met: the adapter drives the core, four seed-0 maps match the US-001 goldens on the tape, and it is automated rather than hand-run. I verified that independently. The identity work is genuinely good — `first_tape_mismatch` is the right gate, and the subprocess isolation solves a real problem cleanly.

What blocks approval is what the port left standing. The adapter now keeps a second, parallel copy of position, orders, episode and rung state, and `_adopt_venue_into_core` writes that copy back **into** the core before every requote (F1). That inverts the ownership US-002 was built to establish, and the reverse leg exists to serve the legacy test harness, not production. Separately, US-003 re-introduced the cancel-accumulator that the US-002 review removed one commit ago, in a worse form: three snapshot points inside `quoting.py` plus a dedupe layer in `engine.py` (F2). That is a regression against a fix that is one commit old, and it happened because `cancel_reason` was changed to be overwritable — the model got worse so the plumbing had to get bigger.

## 2. Findings

### F1 — the adapter and the core both own the state, and the adapter wins every second

`strategy.py:1152-1216`. `_shim_from_core` copies 9 core fields plus `_buy_levels` out to adapter fields. `_adopt_venue_into_core` copies position, `last_buy_ns`, all `orders` (rebuilt from `_live`) and every `rung.live_id` (re-derived by scanning `level_index`) back in. `_evaluate` calls `_adopt_venue_into_core()` *first*, so on every 1s requote the adapter's view overwrites the core's before the Wake is stepped.

I traced who actually reads the shim fields in production:

| Field | Production reads | Test reads |
| --- | --- | --- |
| `_episode_counter` | **0** | 0 |
| `_winding_down` | **0** | 2 |
| `_episode_has_buy_fill` | **0** | 3 |
| `_last_buy_ns` | 1 — `_adopt_venue_into_core` only | 2 |
| `_position_qty` / `_position_cost_basis` | telemetry, and `_adopt_venue_into_core` | many |

So for position and `last_buy_ns` the adopt leg is a **pure round trip**: it reads back values `_shim_from_core` copied out of the core moments earlier. It reads as a venue-authoritative overwrite and is not one. `_episode_counter` is written and never read by anything, in either direction.

The reverse leg exists for the tests. `tests/test_backtest_maker.py` seeds scenarios by assigning `strategy._position_qty` / `_position_token_index` directly (570-571, 660-661, 715-716, 907-908, …) and by installing `_live` entries through `set_only_live`. `_adopt_venue_into_core` is the bridge that carries those pokes into the core. The impl report says as much, and also admits the bridge is deliberately partial — it does not carry `winding_down` / `has_buy_fill` / episode fields — which means there is no stated rule for which side owns which field. That is a half-applied sync with an undocumented boundary, and US-004 (live), US-006 (persistence) and US-007 all have to keep three representations agreeing: `StrategyState`, `_live`/`_submitted`/`_detached`, and the `_episode_*`/`_position_*` shim.

`_ensure_core_order_for_fill` (`strategy.py:1127-1151`) is the same problem one level down: when a fill arrives for an order the core does not have, the adapter **fabricates** a `RestingOrder` with a guessed `status="canceling"`, `accepted=True`, `cancel_reason=""`. Manufacturing core state from adapter bookkeeping is only necessary because the two mirrors can drift.

The move: give the harness a way to seed the **core** — a test helper that builds a `StrategyState` and the matching `_live` entries together — migrate the ~10 setup sites in `test_backtest_maker.py`, then delete `_adopt_venue_into_core`, `_ensure_core_order_for_fill`, and the four write-only shim fields. The plan asked for a one-directional shim ("copy `state.episode_id` / … onto the old `_episode_*` fields so `test_backtest_maker.py` does not need a rewrite"); the reverse direction was never in it. Keeping production code bidirectional to accommodate test setup is the wrong trade, and it is the one thing most likely to make US-004 painful.

While you are there: `_drive` calls `_shim_from_core()` on **every** event, and `_sync_inputs` fires four store-only `_drive` calls before the Wake. One `_evaluate` therefore runs 5 `step` calls and 5 full shim rebuilds. Rebuild the shim once, after the Wake.

### F2 — the cancel accumulator is back, plus a dedupe layer on top of it

The US-002 review removed the `prefix` / `extra` / `_merge_cancels` threading; `progress.txt` records the fix as "`Plan.cancels` is a step-entry diff of `canceling` ids" — one derivation, in `engine.py`. US-003 puts it back:

- `quoting.py:566-572` — new `_opened_cancels(prior, state)`.
- `quoting.py:589-593` and `597-601` — two `prior = frozenset(...)` / `_opened_cancels(...)` pairs inside `_quote_with_latch`, accumulated into `first_cancels` and concatenated onto the plan at `:618`.
- `quoting.py:694-699` — a third pair in `requote`, producing `session_cancels`, concatenated again.
- `engine.py:96-101` — the step-entry diff now has to **dedupe** against whatever those three produced: `first_ids = {...}`, `extra = tuple(c for c in opened if c.order_id not in first_ids)`.

So there are now two independent cancel-collection mechanisms running at once, and the second one exists to not double-report the first. That is exactly the shape the last review deleted, with a dedupe pass added.

The root cause is a model change, not a plumbing gap. `quoting.py:90-95` changed the guard from "skip anything already canceling" to:

```python
if _already_canceling(order) and order.cancel_reason == reason:
    continue
state = mark_canceling(state=state, order_id=order.order_id, reason=reason)
```

An order already canceling for a *different* reason now gets its `cancel_reason` overwritten. That is deliberate — `progress.txt`: "`cancel_request` is the **first** reason; `canceled`/`cancel_ack` are the **last**" — but it makes one field mean two different things at two different times, and the three snapshots exist only to recover the first meaning after the field has been clobbered.

Fix the model. `cancel_reason` should be first-write-wins, since that is what `cancel_request` identity needs; then the single step-entry diff in `engine.py` yields the correct first reason for free, and `_opened_cancels`, all three `prior=` snapshots, `first_cancels`, `session_cancels` and the `engine.py` dedupe all disappear. If the ack record genuinely needs the later reason, give `RestingOrder` a second field for it and let the adapter read that — do not overload one slot and then rebuild the lost half by diffing.

### F3 — `round(x, 2)` where `share_floor` is the canonical helper

`lifecycle.py:290`:

```python
partial = round(filled_qty, 2) < round(order.submitted_qty, 2)
```

The bug it fixes is real and well diagnosed (`40.0 + 91.58 == 131.57999999999998`). But `shared/utils/trading.py:16` already exists for exactly this, with the epsilon and a docstring that names the case:

```python
def share_floor(size: float) -> float:
    """Floor share size to two decimals without dropping a binary 0.01 remainder."""
    return math.floor(size * 100.0 + 1e-6) / 100.0
```

`quoting.py` already imports it. `round` has different edge behaviour (banker's rounding, and it rounds up as well as down), so this is not just a style difference — a fill of `submitted_qty - 0.004` now counts as complete. Use `share_floor` on both sides, or compare with the `1e-12`/epsilon convention the rest of the core uses. Right now the core has two different float-equality idioms for share quantities.

### F4 — `_venue_cancel_reason` re-creates `_no_target_reason`, and the "held position" rule now lives in both layers

`strategy.py:611-623` re-implements the held-position tail of `_no_target_reason` — the method the plan listed for deletion — mapping a core `BlockReason` to `exit_settle` / `fair` / `no_quote`. Placing the backtest-only reasons in the adapter matches the agreed design (`progress.txt`: "Backtest `exit_settle`/`no_quote` stay adapter telemetry"), so the *location* is right.

The problem is that the core now also branches on held position to pick a reason. `quoting.py:644` added `held_reason: BlockReason = latch_reason or "fair"`, used at `:648`, `:651`, and at `:654` as `held_reason if state.position.qty > 0 else reason`. So the core computes a held-position reason, hands it over as a string, and the adapter re-maps it against position and settling a second time — at three call sites (`_request_cancel`, `_record_block_if_idle`, `_shim_from_core:1214`). Two-stage reason mangling across a layer boundary, where either layer alone could own it.

Pick one owner. The adapter has `self._core`, so it can compute the whole held-position reason itself; then `held_reason` and the `position.qty > 0` branch come out of `_reconcile_choice` and the core is back to one reason per outcome.

Related, same area: `_HELD_NO_TARGET_REASONS` (`strategy.py:123`) is a `frozenset` of raw strings mirroring members of the core's `BlockReason` Literal, with no typing link. Add or remove a `BlockReason` member and this set goes stale silently. Type it as `frozenset[BlockReason]` so pyright checks the members.

### F5 — the identity test forks `run.py`'s selection loader

`tests/test_extraction_oracle.py:_seed0_replay_inputs` branches on game and hand-rolls the whole input load: picks `LOL_RESEARCH_MODEL_DIR` vs `RESEARCH_MODEL_DIR`, calls `load_lol_selection` / `load_dota_selection`, rebuilds the LoL lookups with the same five kwargs, and re-derives `lag_seconds = 0 if lol else BACKTEST_LAG_SECONDS`.

`run.py:940-967` already does this: `load_run_selection` returns a `RunSelection` with a `load_lookups` partial that hides the game fork, plus `capture_root`, `report_root`, `signal_rows`. The only pieces it does not carry are `model_path` and `lag_seconds`, and those are two lines already duplicated at `run.py:1183` and `run.py:1255`.

This is the worst possible place to fork a loader. If `load_run_selection` ever changes how lookups are built, the identity test keeps replaying the old way and reports "identity holds" for a pipeline production no longer runs. The test's whole value is that it exercises the real path.

The reason it was forked is visible in the signature: `load_run_selection(args: argparse.Namespace)`. A `Namespace` is an untyped bag — effectively the `dict[str, Any]` AGENTS.md bans — so the test could not call it without faking CLI args. Change it to `load_run_selection(*, game, match_id, limit)`, move `model_path` and `lag_seconds` onto `RunSelection`, and `_seed0_replay_inputs` collapses from ~40 lines to two calls with no game branch.

### F6 — `_latched` reconstructs `buy_targets` with logic that is not the core's

`strategy.py:798-828`. The property rebuilds `LatchedDecision.buy_targets` from rungs:

```python
token_index = self._core.episode_token_index or 0
buy_targets = tuple(... for rung in self._core.rungs if not rung.done and rung.price > 0)
```

`rung.price > 0` is not the core's admission rule — the core uses `_reject_buy` (legality, min price, spread, fair, cash). So roughly 20 assertions in `test_backtest_maker.py` (`_latched.buy_targets`, `.fair_radiant`, …) now check a reconstruction that can disagree with what the strategy actually decided. Those tests read as decision coverage and are not.

`episode_token_index or 0` is also the `int | None` falsy trap: `None` and `0` both yield `0`. It is harmless today only because the fallback happens to equal the falsy value. Write `if episode_token_index is None`.

Either point those tests at `EngineOutput.plan.places` (the actual decision), or make `_latched` report only the latch fields it can report faithfully and drop the synthetic `buy_targets`.

### F7 — dead code the plan asked to delete, kept alive by one test

`_can_afford` (`strategy.py:1114-1126`) has **zero** production call sites — the core owns budget now via `Budget.available_usdc` and the per-rung skip. It survives only because `tests/test_backtest_maker.py:1193-1200` calls it directly. The plan lists it in the delete set.

Delete the method and the test. A 13-line duplicate of a rule the core now owns, pinned by a test that exists to pin it, is exactly the debt this step was supposed to remove.

### F8 — `_age_signal` forges a timestamp to say "no signal"

`strategy.py:926-935`. When there is no model row, the adapter re-sends the previous signal with `received_ns` back-dated past `exit_stale_s` so `_sync_latch` drops the latch. The plan sanctioned this, and it works, but it is a fabricated event: the adapter states a falsehood about when data arrived because the event vocabulary has no way to say "the model has nothing right now".

US-005 replays the same event sequence through two adapters and US-006 persists it. A forged `received_ns` in that stream will be read as real. Add the honest event — `SignalCleared`, or allow `SignalUpdate.signal: RawDeltaSignal | None` — and let `_sync_latch` handle it directly. It is a smaller change now than after two more steps depend on the tape.

Same method's neighbour: `_sync_signal` maintains `_last_synced_feed_ts` **and** `_last_model_ts_ns` to suppress duplicate sends, while the core already suppresses same-tick rebuilds via `received_ns <= latch.fair_ts_ns`. Two dedup mechanisms for one condition.

### F9 — the adapter bypasses `step` to clear the books

`strategy.py:487`:

```python
if books is None:
    self._core = replace(self._core, books=None)
```

Every other input reaches the core through `_drive` → `step`. This one reaches around it and mutates `StrategyState` directly, for the one case the event types cannot express. Same root cause as F8 and the same fix: let `BookUpdate` carry `BookPair | None`. Then `_sync_inputs` is four uniform `_drive` calls with no special case, and `step` stays the only way state changes — which is the invariant US-005 and US-006 will rely on.

### F10 — `strategy.py` is still 1216 lines and 44 methods

The plan's own target was "well under 1000 lines if the delete is real", and the impl report lists the miss as deviation #1. The deletion was real (−344 net) but stopped at the Follow300 logic and left everything else in one class.

The plan forbids `src/backtest/adapter.py`, and that constraint is right — a second file named "adapter" next to `strategy.py` would not clarify anything. But telemetry is a clean seam and is not the adapter: `_record_no_quote`, `_record_order_lifecycle`, `_record_fill_row`, `_order_from_live`, `_quote_context`, `_latch_quote_context`, `_record_block_if_idle` are ~220 lines that take a snapshot and emit a record. Moved to `maker_telemetry.py` next to the existing `maker_orders.py`, plus the F1 shim removal (~30 lines), `_can_afford` (13) and `_latched` (30), that lands around 950 without touching a single decision path.

## 3. Nits

- **NIT** `tests/test_extraction_oracle.py:429` — the four-map test reports PnL drift with `print(..., flush=True)`. Under pytest capture that output only appears when the test **fails**, so on the passing path the "report, do not gate" requirement produces nothing. Use `warnings.warn` or `record_property` so a passing run still surfaces the drift.
- **NIT** `tests/test_extraction_oracle.py:428` — `del fill_count`. The parametrize carries 6/14/26/3 and the test discards them. Either assert `len(actual.fills) == fill_count` (cheap, and it makes the parameter mean something) or drop it from this parametrize.
- **NIT** `replay_seed0_identity` writes the child's result with `write_identity_golden` into `tmp_path`. It is not a golden; the name will read as "the test regenerates goldens" to the next person, in the one file where that must never happen. Rename the writer, or use the plain JSON dump.
- **NIT** `_replay_seed0_identity_inprocess` is reachable only through a generated source string and carries `# pyright: ignore[reportUnusedFunction]`. A module-level `__all__` entry or a direct `-m` entry point would let the type checker see the real edge instead of suppressing it.
- **NIT** `quoting.py:236` — `_join_reject_reason` picks its answer by scanning `for key in ("wide_spread", "min_price", "fair")`. The priority is a bare tuple of strings encoding the original's if-chain order, with no comment tying it to that order. One line naming why that precedence is what it is would save the next reader a trip to `git log`.
- **NIT** `strategy.py:475` — `_drive(*, event, execute: bool = True)`. `execute` is never passed by any caller. Dead optionality, and AGENTS.md asks for required arguments; drop the parameter.

## 4. What I would not change

- **`first_tape_mismatch`.** Sixteen lines, sits beside `first_identity_mismatch`, shares `_seq_mismatch`, and changes no golden JSON. It makes the gate say exactly what the step means by identity and leaves PnL reported. `test_first_tape_mismatch_skips_checks` pins both directions — that the tape ignores checks drift and that the old comparator still catches it. This is the best-designed part of the commit.
- **Subprocess isolation for the seed-0 replays.** Nautilus logging initialises once per process, so this is a real constraint, not a workaround for a bug. The child-process boundary is the honest fix, and it turns "run them one at a time by hand" into four passing parametrized cases — I confirmed all four run green in a single pytest process in 23.7s. That is the difference between US-003 being provable and being a claim.
- **`engine_fault: nautilus_zero_fill` detection.** Catching the `PositionOpened` / FLAT assertion and re-raising with an explicit label (`test_extraction_oracle.py:385-391`, plus the `stop_reason` check in `_identity_from_seed0_batch`) is exactly what the plan's diagnosis table asks for. It stops an engine fault from being misread as a quoting regression, which is the failure mode that would waste the most time.
- **Books do not requote.** `on_order_book_deltas` updates `_books` / `_book_ts_ns` and stops; `_REQUOTE_EVENTS = (Wake, CancelAck)`; no Wake is scheduled from `next_wake_ns`. Three separate places where a second clock could have crept in, and none of them did. That restraint is why the tape matches.
- **Not Waking after BUY fills, and Waking on a flattening SELL fill** (`strategy.py:323-324`). This is the subtlest piece of event mapping in the step — it decides whether leftover BUY cancels land at the fill timestamp or one tick later — and it is implemented as one guarded call in the fill handler rather than a mode flag threaded through `_drive`.
- **`test_two_decimal_fill_sum_completes_the_rung`.** The right test for F3's bug: it builds the exact `40.0 + 91.58` case at the lifecycle level, not through a replay. Keep it when you switch to `share_floor`.
- **Leaving `feature.json` `passes: false` and not recapturing the goldens.** The four maps were made to match by fixing the port, not by moving the bar. Three of the four fixes named in the impl report (2-decimal fill completion, join-level reject reason, first-reason cancel snapshot) are genuine port bugs found by the goldens doing their job.
