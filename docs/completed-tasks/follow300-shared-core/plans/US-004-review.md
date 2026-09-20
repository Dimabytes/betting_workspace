# US-004 review — Wire live/paper to execute the core plan

Commit: `f73bd85` on `ladder-experiment`.
Tests: 198 passed (`test_trader_session_core`, `test_strategy_core`, `test_trader_session_quoting`,
`test_trader_match_lifecycle`, `test_trader_wallet_host`, `test_paper_gateway`).
ruff and basedpyright clean on the new files.

Verdict: **REQUEST CHANGES**. F1 and F4 are correctness. F2 and F3 are structural.

## F1 (blocker) — `note_placed` binds core ids to venue ids by list index

`src/trader/session_core.py:280` uses `zip(expected, placed, strict=False)` and treats the tail as
`SubmitTimeout`. Two real paths return a list that is short **in the middle**, not at the end:

1. `wrap_inventory_place_guard.place` (`src/trader/engine_seams.py:905`) re-filters droppable SELLs
   at send time, after `LiveCore` planned the batch. The store can change between plan and send.
2. `_parse_place_response` in the fork (`gateway.py:193`) calls `continue` on any item with no order
   id, so it skips a quote in place.

Result: the dropped order's core id binds to the next order's venue id. `_venue_cancels` then cancels
the wrong live order, and `note_fill` credits the wrong rung. This breaks the step's own acceptance
line: "The allowed batch is finalized before send so a filtered SELL does not quarantine a sibling BUY".
`test_place_short_mints_submit_timeout` encodes the tail-only assumption, so the suite hides it.

Fix: `OpenOrder` carries `token_id`, `side`, `price`, `size`. Match returned orders against
`batch.to_place[i].quote` on those fields; every unmatched planned id becomes `SubmitTimeout`.
Better code-judo: the guard must not drop what the core already planned. `LiveCore._placed_quote`
already calls `sell_is_droppable`. Make the finalized batch the single filter point and let
`wrap_inventory_place_guard` skip esports cids.

## F2 (blocker) — three copies of "which token do we hold"

- `session_core.held_token` (YES wins, threshold `min_size`)
- `LiveCore._position_mismatch` (larger wins, threshold `min_size`)
- `match_worker._lock_resume` (larger wins, threshold `> 0.0`)

One concept, three rules. Two of them drive Recovery, so a dust position takes one path in one place
and another path elsewhere. Collapse into one function in `session_core`:
`held_position(*, yes_size, no_size, min_size) -> HeldPosition(token_index, qty)`. All three call it.
`held_token` becomes `core.token_id(held.token_index)`.

## F3 (structural) — `make_core_quotes_adapter` is seven positional callbacks for one struct

`src/trader/session_core.py:432` and `src/trader/host_resources.py:67`. The factory takes seven
callables plus an error hook, all positional, only to fill one `LiveSources`. `host_resources` already
holds `engine`, `store` and `cache` in scope.

Delete the factory. Build `LiveSources` inline in `_bind_quotes_adapter` and call
`drive(core, inp, sources)`. That removes one indirection layer and seven closures.

Also: `sizes_for` returns an anonymous `tuple[float, float]`. AGENTS.md forbids that for our own
multi-field values. `_bind_quotes_adapter` has no return type annotation.

## F4 (correctness) — `_must_hold_sell` does not skip a canceling SELL

`src/strategy/quoting.py:339`. `_held_sell` returns the first SELL whatever its status. If halt or
`stale_book` already marked it `canceling`, and the block clears while the cancel is still unconfirmed,
the hold branch emits a target from that order. `_live_matches` rejects it (`_already_canceling`), so
`_place_missing` places a **second** SELL at the same price.

Fix: skip canceling orders in `_held_sell`. The hold branch also ignores `state.permissions.allow_sell`,
unlike the normal branch below it.

## F5 — private-attribute reach-through

- `src/trader/wallet_host.py:613`: `getattr(worker, "_core", None)`. A string read of a private field
  to dodge the type checker. Expose `MatchWorker.core` as a property, or pass the core into
  `register_worker`.
- `src/trader/match_worker.py:733`: `self._watchdog._entry_timeout_seconds` and `_exit_timeout_seconds`.
  Make those two public fields on `FreshnessWatchdog`.

## F6 — `PublishedFair` is an identity wrapper

After US-004 strips `entry_token_id` and `entry_price`, the dataclass holds one float, and
`StrategyCell` already exposes `yes_fair` as a property with `publish` / `clear`. Delete the class.
`StrategyCell.yes_fair: float | None` does the same job and removes one type, one import and one hop
in the worker and the journal.

## F7 — `entry_block_from_reason` rebuilds a dict per call

`src/trader/session_core.py:80`. Move the mapping to a module-level `Final`. The larger question: the
core speaks `BlockReason` (str literals) and the journal speaks `EntryBlock` (StrEnum) for the same
concept, and any unknown reason silently becomes `NO_EDGE`. The core is now the only producer of block
reasons. Write `BlockReason` into the journal and delete `EntryBlock`.

## F8 — `_live_matches` no longer uses `state`

`src/strategy/quoting.py:418`. The only use was `share_floor(state.position.qty)`. Drop the parameter
at the definition and the call sites.

## F9 — `install_collateral_snapshot` hides a network call inside `positions()`

`src/trader/engine_seams.py:373`. `positions()` now also fetches the collateral balance, sequentially.
The name does not say so. If it stays there, `asyncio.gather` the two awaits. Better: refresh the cache
where the REST snapshot is scheduled, not inside the positions seam.

## F10 (size) — `match_worker.py` is at 975 lines, +68 in this step

One step from the 1k line. US-006 adds persistence and recovery to the same class. The core-adapter
block is a cohesive unit: `open_core`, `_enqueue_core_signal`, `note_place_result`,
`note_cancel_result`, `note_core_fill`, `note_failed_fill`, `_lock_resume`. Move it next to `LiveCore`
before US-006, not after. `engine_seams.py` is already 1226 lines and grew again here.

## F11 (risk) — the core clock became the wall clock

`arrived_at = time.time()` and `time.time_ns()` now feed every core `now_ns`. Stale checks, SELL
min-life and the 10s settle all depend on an NTP-adjustable clock. The old code used `time.monotonic()`.
A backward step makes `now_ns - placed_ns` negative and holds a SELL past its min-life. The core takes
`now_ns` on every event, so feed it monotonic nanoseconds and keep the wall clock for the journal only.

## F12 (dead branch) — `make_esports_reconcile` falls back to the fork reconciler

`src/trader/session_core.py:481`. AGENTS.md: no branches for inputs that cannot occur today. Every
attached esports market registers a core. If a live cid without a core cannot happen, delete the
fallback plus the `original_reconcile` plumbing through `open_wallet_host`, `WalletHost.__init__` and
`close`. If it can happen, it needs a test.

## Smaller notes

- `sources_now_ns()` is a one-line wrapper over `time.time_ns()`, patched by name in tests. Inject a
  clock callable into `LiveCore` instead, or call `time.time_ns()` directly.
- `LiveCore.note_placed` raises `AssertionError` on a long venue list in the live hot path. Log and
  truncate; do not kill the quote cycle.
- `PAPER_START_USDC` reads the sink with `getattr(self._sink.state, "running_net_cash", 0.0)`. A
  `getattr` default hides whether the attribute exists. Type the sink state.
