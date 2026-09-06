# US-002 review — dependency-free Follow300 core

Scope: commit `6feb54e` vs parent `a847856`. Files: `src/strategy/{types,policy,signals,lifecycle,quoting,engine}.py`, `tests/test_strategy_imports.py`, `tests/test_strategy_core.py`. 2170 added lines, no deletions, no file over 1000 lines (largest: `quoting.py` at 763).

Checks I ran: `pytest tests/test_strategy_imports.py tests/test_strategy_core.py` → 20 passed. `ruff check`, `ruff format --check`, `basedpyright` → clean.

## 1. Verdict

**REQUEST CHANGES.**

The package boundary is right, the import isolation is real, and the state model is honest. Two things block approval. First, `begin_episode` runs inside `_place_missing` and wipes rung prices that `_open_buy_targets` computed one call earlier, which silently kills rungs 1 and 2 (F1 — I reproduced it). Second, the step's headline rule — a working SELL is cancelled on the next requote inside the 10s settle — is not tested; the assertion sits behind an `if` that is always false (F4).

Beyond those, the port carried over the backtest's parallel block-reason derivation (F2) and grew a cancel-accumulator threaded through five functions (F3). US-002 exists to build the clean core that US-003 deletes the backtest quoting into. Porting the incidental complexity along with the behavior spends the one chance to drop it.

## 2. Findings

### F1 — `begin_episode` inside `_place_missing` wipes computed rung prices (structural, real behavior loss)

`quoting.py:445-448`. `_open_buy_targets` prices every rung via `_price_rungs`, then filters. `_place_missing` later calls `begin_episode`, which calls `fresh_rungs` and resets every `rung.price` to `0.0`. `occupy_buy` restores the price only for rungs that actually got an order. Any rung skipped for cash or legality is left at `price=0.0`.

Reproduced:

```
_prime(usdc=one_rung + 1.0)
places:            [(0, 0.5)]
rungs after place: [(0, 0.5, 'c0'), (1, 0.0, None), (2, 0.0, None)]
second Wake:       places [] , block_reason ''
rungs2:            [(0, 0.5), (1, 0.0), (2, 0.0)]
```

On the second Wake `follow_book` is `False` (no fill yet, no new signal tick), so `_price_rungs` returns early and rungs 1 and 2 stay at `0.0`. `_legal_buy(price=0.0)` rejects them. They are dead until a newer `received_ns` arrives or a fill flips `has_buy_fill`. `block_reason` is empty, so nothing reports it.

The root cause is layering, not the reset itself: "which episode am I in" is decided as a side effect of "I am about to place my first order". Decide the episode in `_choose_buy_targets`, before `_price_rungs` runs, and let `_place_missing` become a pure target-to-`PlaceOrder` translator that never touches `rungs` except through `occupy_buy`. That removes the double write of `rung.price` and the `if not state.rungs: replace(...)` guard at `quoting.py:446-447` at the same time.

Add a test: prime with budget for one rung, Wake twice with the same signal, assert the two unfunded rungs keep their ladder prices.

### F2 — the block-reason path re-derives every entry gate a second time

`quoting.py:576-661` (`_spread_blocked`, `_floor_blocked`, `_flat_no_buy_reason`, `_no_target_reason`) plus the `cash_blocked: bool` threaded back out of `_open_buy_targets` → `_choose_buy_targets` → `choose_targets` → `_reconcile_choice`.

Every gate now has two implementations:

| Gate | Decides | Explains |
| --- | --- | --- |
| min price | `buy_price_legal` (`signals.py:87`) | `_floor_blocked` |
| spread | `buy_price_legal` (`signals.py:89`) | `_spread_blocked` |
| cash | `_open_buy_targets:270` | `cash_blocked` flag returned up three frames |
| nw / min_delta | `_buy_is_closed` + `pick_episode_token` | `_flat_no_buy_reason` |

`_spread_blocked` and `_floor_blocked` are the same eight-line loop; they differ in one line. Both re-implement `buy_price_legal`'s internals in reverse and will go stale the moment that function changes, with no failing test.

I checked whether the freeze forces this. It does not. Per `progress.txt`, US-001 identity is fills, place/cancel kinds, episode/level, and reserved notional — and explicitly not `no_quote`. `CancelOrder.reason` is inside the identity; `Plan.block_reason` is not. So the reason strings are free to be restructured now.

The code-judo move: have the per-rung decision return a reason instead of a bool. One pass over the rungs yields both the targets and, when no target survives, the first reason that killed one:

```python
def _rung_verdict(...) -> QuoteTarget | str   # target, or the reason it was rejected
```

`_spread_blocked`, `_floor_blocked`, `_flat_no_buy_reason`, and the `cash_blocked` plumbing all disappear, and the reason can no longer disagree with the decision.

This also collapses the second budget check. Cash is enforced twice today with different semantics: `_open_buy_targets:270` against the untouched snapshot, `_place_missing:455-458` against a running balance. `progress.txt` records this as a deliberate learning. It only exists because the first pass has to set `cash_blocked`. Fix F2 and the snapshot check goes away.

Note while you are here: `_no_target_reason` emits `exit_settle` and `no_quote`, neither of which is in the plan's reason list. Faithful to `backtest/strategy.py:1475-1493`, but decide whether the core's vocabulary is the plan's or the backtest's, and write it down.

### F3 — the `prefix` cancel accumulator threads through five functions

`requote` → `_prefix_cancels` → `_quote_after_gates` → `_quote_with_latch` → `_reconcile_choice`, each taking or producing a `prefix: tuple[CancelOrder, ...]`, plus `extra`, plus `_merge_cancels` (dedupe by id) and `_with_cancels`. `_merge_cancels` is called six times; four call sites merge into a merge.

The state already knows. `mark_canceling` writes `status="canceling"` and `cancel_reason` on the order. So the plan's `cancels` tuple is derivable at the end of `requote`: the orders that moved into `canceling` during this pass. Snapshot the canceling ids at entry, diff at exit, build `cancels` once.

That deletes the `prefix` and `extra` parameters from four signatures, both merge helpers, and the dedupe — the dedupe only exists because the same cancel can be appended twice by two layers.

### F4 — the settle SELL-cancel rule is not tested

`tests/test_strategy_core.py:428-433`:

```python
sold_ids = [order.order_id for order in pre.orders if order.side == "SELL"]
if sold_ids:
    assert any(cancel.order_id in sold_ids for cancel in early.cancels)
```

`sold_ids` is always empty. Settling starts at the BUY fill, and no SELL can exist before that fill, so no SELL is ever resting when the early Wake runs. I confirmed it: `SELL orders present in pre: []`, `early cancels: ()`. The branch is dead and the test asserts only that no SELL was *placed*.

This is the extraction SELL table — the rule the plan calls out as the difference from live, and the reason `debounce_ms` and SELL min-life are excluded from this step. It needs a real test: reach a state with a live SELL (fill, Wake past 10s to place the SELL, accept it), then a new BUY fill to restart the settle, then Wake and assert that SELL is cancelled. Delete the `if`.

### F5 — policy identity constants forked from the backtest with no shared home

`policy.py:17-21` declares `POLICY_VERSION = "follow300-v1"`, `LEVEL_COUNT = 3`, `STEP_TICKS = 1` as fresh literals. The plan names `backtest/strategy.py:92,93,95` as the source. Those are `BUY_LEVEL_COUNT`, `BUY_LEVEL_STEP_TICKS`, `BUY_POLICY_VERSION`, and the core cannot import `backtest`.

So the fork was forced, but the resolution was not. The ladder shape now has two sources of truth for the numbers US-001 froze, and a change to either side breaks US-003 identity with nothing failing. Move all three to `shared/constants/strategy.py` — `policy.py` already imports nine constants from there, and `backtest/strategy.py` can import them back. `QUOTE_GRID` already shows the pattern works.

### F6 — `Recovery.restored_buy_ids` is accepted and ignored

`lifecycle.py:205-215`. `apply_recovery` reads `verified_qty` and `verified_token_index` and never touches `restored_buy_ids`. `progress.txt` states this outright: "Recovery does not mark ids itself. `sell_only` + requote unmatched-cancels BUY."

`test_recovery_is_sell_only_and_cancels_restored_buy` passes for a reason unrelated to its name — it would pass with `restored_buy_ids=()`, because `sell_only` cancels every BUY regardless.

An adapter will pass ids and watch them vanish. Either mark them `canceling` in `apply_recovery` (the plan's wording: "listed BUY ids → `canceling`"), or drop the field from `Recovery` and rename the test. AGENTS.md bans branches for inputs that cannot occur; an input the code never reads is the same problem one step further along.

### F7 — three required parameters exist only to be deleted

- `apply_fill(*, state, policy, event)` — `lifecycle.py:269` `del policy`
- `_buy_is_closed(*, state, policy, now_ns)` — `quoting.py:192` `del now_ns`
- `end_episode_if_idle(*, state, min_order_size)` — `lifecycle.py:125` `del min_order_size`, and all four call sites pass `state.limits.min_order_size` from the same `state` they already hand over

AGENTS.md says make arguments required. It does not say invent them. `del` to silence the unused-argument lint is the tell. Drop all three; `apply_fill` then needs no `policy` in `engine.py:59`.

Related, same file: `begin_episode` (`lifecycle.py:72`) uses `fresh_rungs(level_count=len(state.rungs) or 3)`. A magic `3` papering over "I do not know the level count here" — precisely the silent fallback that hides an unclear invariant. `policy.level_count` owns this. Pass it in. (F1 makes the call site an obvious place to do that.)

### F8 — bare tuples carrying a naked bool

AGENTS.md: no anonymous `tuple[A, B]` for our own multi-field values.

- `_open_buy_targets`, `_choose_buy_targets`, `choose_targets` all return `tuple[StrategyState, tuple[QuoteTarget, ...], bool]`. The reader has to know the third slot is `cash_blocked`.
- `_sync_latch` returns `tuple[StrategyState, bool]`, where the bool is `rebuilt`, later read as `follow_book`.

The pervasive `tuple[StrategyState, Plan]` is a defensible functional idiom; a third positional `bool` is not. F2 deletes `cash_blocked` outright. For `_sync_latch`, return a small frozen result with a named field.

Also: `Plan.block_reason: str` and `CancelOrder.reason: str` are open strings over a closed set the plan enumerates. A `Literal[...]` alias in `types.py` would have caught `exit_settle` and `no_quote` drifting in (F2) at typecheck time and costs one line.

### F9 — dead code shipped in the first commit

- `types.py:11` `BookLevel` — zero references anywhere.
- `signals.py:49` `abs_nw_delta_30` — zero callers. `nw_delta_30` arrives on `RawDeltaSignal`; the core never walks a per-second map. The plan listed it; nothing needs it.
- `types.py:85` `level_moves` — written by `_reattach` and `_place_missing`, never read.

Delete them. US-003 can add back what it needs.

## 3. Nits

- **NIT** `quoting.py:106-108` — `_blocked` returns `empty_plan(reason=reason) if not cancels else replace(empty_plan(reason=reason), cancels=cancels)`. Both arms are one `Plan(keep=(), moves=(), cancels=cancels, places=(), block_reason=reason)`. The `replace(empty_plan(reason=...), cancels=...)` shape repeats five times in the file; a `blocked_plan(*, reason, cancels)` constructor removes all of them.
- **NIT** `signals.py:114` — `token_book(books, token_index)` is `books.tokens[token_index]` behind a keyword-only wrapper, and it lives in the module that owns the fair-value math. Inline it.
- **NIT** `BookPair.tokens` — every reader (`token_book`, `book_p_radiant`, `_price_rungs`) assumes tuple position equals `TokenBook.token_index`, and nothing enforces it. Either drop `token_index` from `TokenBook` as redundant, or add a `__post_init__` check on `BookPair`. Silent mis-indexing here would pick the wrong token with no error.
- **NIT** `quoting.py:60` — `empty_plan(*, reason: str = "")` carries a default argument against the AGENTS rule, in a codebase where every other new signature is strict.
- **NIT** `tests/test_strategy_core.py:184, 235, 483` — trailing `del state` to quiet an unused-variable lint. Use `_` at the unpack instead.
- **NIT** `test_equal_edge_prefers_token_index_zero` hand-builds a `StrategyState` with `replace` and calls `pick_episode_token` directly. That is the only reason the function is public. Fine for now; if the tie-break can be driven through `step`, prefer that and make it private.

## 4. What I would not change

- **The module split.** `types` / `policy` / `signals` / `lifecycle` / `quoting` / `engine` maps onto real seams, not onto file-size targets. `signals.py` at 115 lines and `engine.py` at 90 are the right size for what they own. `quoting.py` at 763 is the only one worth watching, and F2 plus F3 take a large bite out of it — do not split it before doing those, or you will just spread the same complexity across two files.
- **`engine.py`.** `step` is 6 lines, `_store` and `_apply_fill_path` are flat `match` dispatches with no logic in them. This is exactly the "separate orchestration from business logic" outcome the rubric asks for. Leave it alone.
- **Frozen dataclasses with `replace` throughout, no `dict[str, Any]`, no bare tuples in the type model, keyword-only arguments.** The state model is legible and every transition is a value. This is the part that makes the rest fixable.
- **The subprocess import-isolation test.** Patching `builtins.open`, `socket.socket`, `time.time`, and `time.time_ns` before import, in a child process because `tests/conftest.py` imports `trader.notify`, is the right shape. It is the guarantee US-004 through US-007 depend on and it is cheap.
- **`now_ns` as an event field everywhere, never a clock read.** Not negotiable for replay determinism, and it is done consistently.
- **The occupancy model** — cancel request does not free a rung, timeouts land in `unknown` and hold it, only ack/reject/fill free it. `rung_occupied`, `_free_rung`, and `_already_canceling` implement this cleanly, and `test_cancel_then_fill_race`, `test_cancel_timeout_keeps_unknown_occupancy`, and `test_submit_timeout_does_not_drop_pending` pin it properly. This is the subtlest requirement in the step and it is the best-handled one.
- **Not creating `__init__.py`.** The pre-commit `no-init-py` hook bans it; importing from submodules is correct here, even though the plan asked for a package export surface.
