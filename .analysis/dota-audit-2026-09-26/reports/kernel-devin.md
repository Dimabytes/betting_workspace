# kernel-devin — strategy kernel and live/backtest adapter parity
Status: FINAL

Audit of `src/strategy/*` (the Follow300 strategy kernel), its two adapters
(`src/trader/*` live path, `src/backtest/*` Nautilus path), the poly-maker
engine seams that carry live execution, and the durable command/recovery
journal. Method: full source read of the kernel and adapters; frozen sibling
`../poly-maker` read for the engine loop this codebase wraps; tests inspected
(not run). Report is evidence-ranked: every finding carries severity
(S1–S4), confidence (verified / likely / speculative), and file:line.

## Executive summary

The kernel itself is in good shape: single-writer `step()` state machine,
deduplicated fills, conservative reservation math, and a real test suite that
pins live/backtest parity on a shared tape. No S1 defect found — nothing seen
that is silently losing money or corrupting data on the normal path today.

The serious findings are live-only and trigger-gated: two ways the kernel's
world model can wedge permanently within a map (unresolvable unknown fills
blocking all buys; venue-side order death never reported), one self-inflicted
latch trip (raw-vs-stripped anchor asymmetry), and a set of configuration
drifts that mean published backtest numbers do not measure the strategy as
deployed — most sharply LoL, which is backtested at 20× its live clip.

## Findings

### S2-1 (verified) — unresolvable unknown fills permanently block all buys; orphan venue orders keep filling

- `src/strategy/quoting.py:208-209` — `_buy_closed_reason` returns
  `"ownership_unresolved"` whenever `state.pending_ownership` is non-empty,
  blocking every new BUY.
- `src/strategy/lifecycle.py:667-677` — `apply_fill` for an unmapped order id
  credits inventory immediately (`_pend_fill` reason `"unknown"`,
  `lifecycle.py:577-595`) and parks the fill in `pending_ownership`.
- `src/trader/dust_sweep.py:223-250` — the only producer of
  `OwnershipResolved` in the codebase is the dust-sweep taker-sell binder.
  Nothing resolves pending ownership for fills whose order was placed by the
  strategy itself but never mapped (place response lost), placed during a
  restart gap, or placed outside the bot on the same wallet.
- `src/strategy/lifecycle.py:385-397` — `apply_recovery_verified` does not
  clear `pending_ownership`; `src/trader/core_persistence.py:162,420,527`
  checkpoints persist it, so the block survives restarts and recovery.
- Trigger path exists: `poly-maker/src/polymaker/execution/gateway.py:179-183`
  swallows place exceptions and returns `[]` even when the venue may have
  received the orders; `src/trader/session_core.py:583-594` then issues
  `SubmitTimeout` for every unmatched planned order. If the venue order is
  real, its fills arrive unmapped (`session_core.py:504-519` logs
  `"trader core unknown venue fill"`) and land in `pending_ownership` forever.
- Consequence: (a) buy side dead for the rest of that market session —
  silent, looks healthy in telemetry; (b) the orphan venue order is invisible
  to the core's cancel machinery (no `_core_to_venue` entry) so it keeps
  filling while the kernel sells each credited fill — a spread-paying churn
  loop on that token until the orphan exhausts.
- Backtest: unreachable — the adapter raises on unmapped fills
  (`src/backtest/strategy.py:366-373`).
- Remediation: resolve unknown pending fills via a venue order lookup
  (GET /order/{id} gives side/token/size — enough to mint `OwnershipResolved`),
  or expire `pending_ownership` entries whose credited inventory has since
  been sold/verified; at minimum add a periodic "orders the venue knows but
  the core does not" reconcile that either binds them or cancels them, so the
  churn loop cannot persist.

### S2-2 (verified mechanism / likely reachability) — venue-initiated order termination is never delivered to the core

- `src/trader/session_core.py:596-605` — `note_cancel` is the only producer
  of `CancelAck`/`CancelTimeout`, and it is called only from
  `worker.note_cancel_result`, wired exclusively to the response of a cancel
  the quoter itself sent (`src/trader/wallet_host.py:455-464`,
  `_durable_cancel`).
- `src/trader/wallet_store.py:844-846` — `WalletStateStore.on_order` forwards
  user-WS order events (including venue-side `CANCELED`/`CANCELLATION`, which
  the fork parser normalizes at
  `poly-maker/src/polymaker/userstream/parse.py:97-98`) to the fork's state
  tracker only. No path reaches `LiveCore`.
- `src/trader/engine_seams.py:544-563` — `_open_orders_with_proof` merges REST
  open orders with local ones for the *tracker*; nothing diffs core-mapped
  venue ids against the REST set to synthesize a terminal event for the core.
- `src/trader/session_core.py:721-760` — `_watch_stale_cancels` warns once per
  order past `STALE_CANCEL_SECONDS` and explicitly never retires it
  ("the venue is the only truth ... a fabricated ack would let a second SELL
  onto the book"). For an order that is still `live` (not canceling) the
  watchdog does not even fire.
- Consequence: an order terminated venue-side without an in-flight cancel
  (market halt/expiry, self-trade kill, post-only edge cases not caught at
  place time) stays `live`/`canceling` in kernel state forever.
  `rung_occupied` counts all statuses (`src/strategy/lifecycle.py:252-255`),
  so a dead BUY pins its rung — coverage silently lost for the map.
  `sell_occupied` includes canceling orders (`lifecycle.py:258-267`), so a
  dead SELL blocks the only exit slot — position stranded until map end or
  dust-sweep.
- Frequency is low mid-map but nonzero (Polymarket does cancel GTC orders on
  market pause/resolution; cancels that timed out after landing are the more
  common variant — those do self-heal via `_venue_cancels` resend +
  `_cancel_with_proof` treating "not found" as terminal,
  `engine_seams.py:518-541,624-633`). The unhealed case is termination with
  no cancel attempt outstanding.
- Remediation: in the periodic REST reconcile, for each core order whose
  venue id is absent from open orders and which is not pending its own
  place/cancel response, enqueue a synthetic terminal event after a proof
  window (age + no fill seen). Also alert on `live`-status orders missing
  from REST, not only `canceling` ones.

### S2-3 (mechanism verified, frequency likely) — anchor latch compares stripped book mid against a raw book mid; own quotes can trip it

- `src/trader/match_worker.py:407-427` — `_gate_pair` calls `read_raw_pair`
  on `engine.md.book()` — the raw venue book **including our own resting
  orders** — and normalizes the pair mids into `market_p_radiant`.
- `src/trader/match_worker.py:478-485` — that raw mid becomes
  `RawDeltaSignal.anchor_p`.
- `src/trader/session_core.py:262-311` — the kernel's `state.books` are built
  by `_stripped_token_book`, which subtracts our accepted orders' remaining
  size per price level (`_own_remaining`, `session_core.py:227-248`).
- `src/strategy/quoting.py:741-756` + `src/strategy/signals.py:61-62` —
  `_rebuild_latch` rejects the signal with `no_buy_reason="anchor"` when
  `|book_p - anchor_p| > 0.01`; `quoting.py:817-820` then cancels all BUYs.
- Effect: whenever one of our accepted orders sits at top-of-book — exactly
  the lonely-L0 situation the strategy otherwise manages with a 3s timer —
  the anchor (raw mid, includes our quote) diverges from `book_p` (stripped
  mid) by half the gap to the next foreign level. On thin books a ≥3-tick
  gap trips the 1¢ tolerance: all BUYs canceled, buy side blocked until the
  next signal. Since the trip also removes our orders, the *next* signal's
  anchor is clean, buys re-place, and the cycle can oscillate on every feed
  tick — losing queue priority and doubling message traffic each lap.
- Live-only asymmetry: in the backtest the anchor is the recorded dataset mid
  (`src/backtest/strategy.py:1135`) measured on the same book the kernel sees
  → the check behaves as designed (catches real book moves between decision
  and eval).
- Remediation: compute `anchor_p` from the same own-order-stripped book view
  the kernel uses (or keep the raw read for the model input but pass the
  stripped mid as `anchor_p`). The check's intent — "the book moved since the
  model decided" — survives stripping; the own-order channel does not.

### S2-4 (verified) — policy configuration drift makes published backtest economics not describe live

- Level sizing: live `level_usdc` is the profile's `base_size_usdc`
  (`src/trader/match_worker.py:350` → `open_core(level_usdc=...)`, policy
  built at `match_worker.py:981-985`). Live clips:
  `config/trading.toml:35` dota-map `$60`, `config/trading.toml:53`
  dota-oddin-map `$200`, `config/trading.toml:67` lol-map `$5`.
  Backtest pins `BACKTEST_LEVEL_USDC = {"dota": 100.0, "lol": 100.0}`
  (`src/backtest/run.py:210`, used at `run.py:413`).
  → LoL is backtested at 20× its live clip ($300 total ladder vs $15);
  Dota-map at 1.67× ($300 vs $180), Oddin at 0.5× ($300 vs $600). Ladder qty
  per rung scales linearly with `level_usdc` (`quoting.py:339`,
  `_buy_qty(policy, price, usdc=cash)`), but fill probability and queue-share
  do not — a $5 clip fills on a fraction of the depth that a $100 clip needs,
  so LoL ROI/fill figures are not transferable to the deployed size. The
  recorded LoL run's `required cash ~$397` vs live peak ~$15 illustrates the
  divergence.
- Post-game liquidation: `follow300_policy` hardcodes
  `sell_after_game_end=False` (`src/strategy/policy.py:75`); live never
  overrides it. Backtest sets it True for series runs
  (`src/backtest/run.py:2040` → `run.py:421-422`). Live does trade
  `series_winner` markets (`src/trader/session_binding.py:264-268` accepts
  the kind). → Series backtests measure a post-end liquidation behavior that
  live deliberately does not perform; live series positions ride to
  resolution while the backtest exits them.
- Cadence is clean: both sides read `debounce_ms`/`quoter_tick_s` from the
  same `config/trading.toml` via `read_engine_cadence`
  (`src/shared/utils/engine_cadence.py:9-28`; backtest `run.py:409-415`,
  live `match_worker.py:978-985`).
- Remediation: parameterize backtest level_usdc per deployed profile (or run
  each profile's own backtest), and either enable post-game selling live for
  series or run series backtests with it off. Record `level_usdc` and
  `sell_after_game_end` in run metadata (partially present already).

### S3-1 (verified) — backtest adapter never re-arms the core wake on OrderAccepted / OrderRejected

- `src/backtest/strategy.py:337-357` — `on_order_accepted` calls
  `self._drive(event=OrderAccepted(...))` and discards the output; no
  `_arm_wake(out.next_wake_ns)`. Every other dirtying event path arms the
  wake: book deltas (`strategy.py:328-331`), fills (`:391`), kill gates
  (`:482-483`), signal alerts (`:474`).
- `src/backtest/strategy.py:858-867` — `_drop_dead_order` (rejected/denied/
  expired) also drops the `OrderRejected` output without arming.
- Effect: the dirty window opened by an accept/reject evaluates at the next
  *pre-armed* wake — up to the 2s fallback — instead of ~100ms debounce as
  live does (`note_place_result` → `drain_apply` + `_wake_cid`,
  `match_worker.py:776-796`). Concretely: after a post_only rejection, live
  re-quotes ~100ms later; the backtest waits up to ~2s, so it systematically
  under-attempts retries in fast books and delays `lonely`/`sell_min_life`
  timers that anchor on `accepted_ns`. Direction is mostly conservative
  (slower = worse queue position, fewer fills) but it is a real parity skew
  on every batch accept.
- Remediation: `out = self._drive(...)` + `self._arm_wake(out.next_wake_ns)`
  in both handlers.

### S3-2 (verified) — backtest collateral can never bind; `no_cash` is dead code in simulation

- `src/backtest/run.py:205` — `ENGINE_STARTING_BALANCE = 1_000_000.0`, wired
  at `run.py:590` and `:643`.
- `src/backtest/strategy.py:1106-1115` — `_available_usdc` returns the venue
  account's free balance → always ~$1M → the `cash + 1e-12 < needed` check at
  `src/strategy/quoting.py:694-696` never fires; `block_reason="no_cash"`
  unreachable.
- Live `budget_from_orders` enforces real collateral minus triple
  reservations (`session_core.py:839-848`). → Any map whose peak reservation
  exceeds the real wallet diverges: live skips rungs, backtest fills them.
  The report's "required cash with reserves" is post-hoc telemetry, not a
  constraint the simulation actually experienced.
- Remediation: run constrained-cash variants (set engine balance to the real
  wallet figure) or at least gate go-live decisions on peak-reserved <
  deployed collateral.

### S3-3 (likely) — steam-fed matches have no faithful entry-staleness model in backtest

- Live steam feed: `src/trader/steam_live_feed.py:17` `stale_seconds=3.0`;
  Oddin 15.0 (`src/trader/oddin_feed.py:30`); GRID 16.0
  (`src/shared/constants/strategy.py:29`). Live kernel entry staleness =
  `feed.stale_seconds` (`wallet_host.py:1317-1324` → `match_worker.py:174-179`
  → `freshness.entry_stale_s` at `match_worker.py:994`).
- Backtest `entry_stale_seconds` maps only `oddin`→15, else→16
  (`src/backtest/feed_schedules.py:385-389`); `ScheduleBinding.feed_source`
  is `Literal["grid","oddin"]` (`feed_schedules.py:42`) — steam-bound matches
  fall to the GRID 16s value.
- Steam is a live-selectable source (`src/trader/feed_selection.py:42-67`,
  `pick_source`). A steam-fed live match goes entry-stale in 3s; its backtest
  counterpart would hold entries for 16s → 5× more entries admitted in
  simulation for those maps.
- Severity S3: impact proportional to how often steam is actually selected
  (fallback source); if steam matches are excluded from training/backtest
  selection this is moot — worth confirming before trusting aggregate numbers
  that mix feed sources.

### S3-4 (likely, narrow trigger) — durable BUY reservations leak for the session when a command sticks at `dispatch_started`

- `src/trader/core_persistence.py:33-40` — `_OPEN_BUY_WHERE` /
  `_RESERVED_BUY_SQL` (run by `reserved_buy_notional` at
  `core_persistence.py:746-748`) count rows with
  `dispatch_state IN ('prepared','dispatch_started','dispatched')`,
  `consumed=0`, `outcome=''`.
- `src/trader/core_execution.py:67-78` — `prepare_dispatch` writes
  `prepared` then `dispatch_started`; `record_dispatch_results`
  (`core_execution.py:81-97`) writes terminal outcomes.
- `src/trader/wallet_host.py:417-430` — `_durable_place` has no try/finally:
  an exception between `mark_dispatch_started` and `record_dispatch_results`
  (sqlite failure in `upsert_command`, process kill, or a raise from
  `_dispatch_places`) leaves `dispatch_started`/outcome='' rows that nothing
  mid-session clears: `retire_unsent` only retires `prepared`
  (`core_execution.py:230-237`), and `list_prepared_places`
  (`core_persistence.py:727+`) likewise only scans `prepared`.
- Normal HTTP failures do NOT leak: the fork's `place` catches everything and
  returns `[]` (`poly-maker/.../gateway.py:179-183`), and `[]` produces
  `outcome='unknown'` rows — excluded from the reservation. The leak needs a
  crash/sqlite fault inside the dispatch window; per-occurrence cost is the
  batch's notional subtracted from available budget for the rest of the
  session (`budget_from_orders`, `session_core.py:846-848`) → creeping
  `no_cash`. Startup recovery replays unresolved commands, so the leak clears
  on restart — mid-session it persists.
- Remediation: wrap `record_dispatch_results` in try/finally marking
  unresolved rows `outcome='unknown'` (they will be re-resolved by the
  venue-order reconcile if they actually landed), or extend `retire_unsent`
  to age out `dispatch_started` rows past a timeout.

### S4-1 (verified) — in-flight place batches are double-reserved for one REST RTT

- Kernel-side: a planned place becomes a `pending` RestingOrder counted by
  `reserved_buy_notional` — no status filter (`session_core.py:405-414`,
  `src/strategy/budget.py:9-14`).
- Durable-side: the same intent sits in a `dispatch_started`/`outcome=''`
  command row counted by `reserved_buy_notional(conn)` —
  `budget_from_orders` adds both (`session_core.py:846-848`).
- Window = `original_place` RTT (~100–500ms): any `finish_cycle` landing in
  it sees the batch notional twice → transiently under-quotes by one batch.
  Self-corrects when `record_dispatch_results` writes `accepted`/`unknown`
  (both excluded from the SQL predicate). Direction conservative. S4.

### S4-2 (verified) — backtest adapter re-implements the kernel's evaluate gate; mirror already missing two arms

- Kernel `should_evaluate` (`src/strategy/scheduling.py:121-134`) evaluates on
  `CancelAck | Recovery | RecoveryVerified | OwnershipResolved |
  halt-PermissionsUpdate | paused/ended ClockUpdate | Wake`.
- Backtest `_executes_plan` (`src/backtest/strategy.py:122-129`) covers
  `Wake | CancelAck | Recovery | RecoveryVerified | paused ClockUpdate |
  game_ended ClockUpdate` — `OwnershipResolved` and halt are absent. Both are
  unreachable in the adapter today (no unknown fills, no permissions input),
  so this is drift-prone duplication rather than a live bug. A future kernel
  trigger that *is* reachable in backtest would silently change semantics
  only live.
- Remediation: drive the adapter off `should_evaluate` (or share a helper)
  instead of re-listing event types.

### S4-3 (verified) — inert bookkeeping and dead code

- `src/backtest/strategy.py:751-754` — `RELEASE:` time alerts are armed with
  no callback and no `on_time_event`/`on_event` handler exists in the
  strategy; every submit schedules a timer that fires into nothing.
- `src/trader/core_recovery.py:299` — `durable_buy_reservation` has no
  callers (the SQL used is `reserved_buy_notional` in `core_persistence.py`).
- `src/trader/wallet_host.py:406-414` — `_warn_orphaned` is the only handling
  for place/cancel results that arrive after their worker is gone; the
  docstring itself flags that the core keeps the order `pending`/`canceling`
  and `sell_occupied` can hold the exit slot. Bounded (match over/rebound),
  but the warn is the only trace — worth a metric.
- `src/strategy/engine.py:50-51` — `SignalUpdate` overwrites `state.signal`
  unconditionally (no `received_ns` monotonicity check). Both adapters feed
  FIFO so it cannot reorder today; a future multi-source path could regress
  it silently.

## Checked and found acceptable (evidence)

- **Kernel event model**: single pure `step()` (`src/strategy/engine.py:121-135`),
  explicit dirty-window coalescing (`scheduling.py:42-63`), evaluation gated
  by `should_evaluate` (`scheduling.py:121-134`), wakes cover every timer —
  sell boundary, mid-spike cooloff, kill-gate expiry, lonely-L0, entry-stale
  (`scheduling.py:86-118`). Deterministic and replayable; `core_trace` +
  `replay_core_trace` compare replayed vs recorded `next_wake_ns`
  (`src/trader/replay_core_trace.py:185-215`).
- **Cadence parity**: live quoter wakes on dirty events then sleeps
  `debounce` (poly-maker `engine.py:318-340`), capped at the core's
  `next_wake_ns` by `install_core_quoter_wake`
  (`src/trader/engine_seams.py:1256-1270`); every live cycle ends in
  `Wake(forced=True)` (`session_core.py:680`). Backtest arms
  `dirty+debounce_ns` and fires the same forced Wake
  (`src/backtest/strategy.py:485-496, 637-644`). Equivalent.
- **Fill dedup, both layers**: kernel `seen_fill_ids` + pending dedup
  (`lifecycle.py:667-671`); store-level outbox `unacked_seq` dedup
  (`wallet_host.py:776-785`). Contract test
  `test_matched_then_confirmed_credits_once` pins it.
- **Unknown-fill crediting**: inventory is credited immediately but
  `credit_inventory=not pending.inventory_credited` on resolution prevents
  double-count (`lifecycle.py:719-724`); episode flags are fixed up at
  resolution (`lifecycle.py:632-635`). Correct — a confirmed ledger fill is
  real money regardless of attribution. (The resolution-path gap is
  S2-1, not the crediting.)
- **Cancel lifecycle**: `_venue_cancels` re-sends cancels for every
  still-`canceling` order each cycle (`session_core.py:819-836`);
  `CancelTimeout` → `status="unknown"` keeps the reservation and remains
  cancelable via `already_canceling` (`lifecycle.py:296-303, 353-357`);
  `_cancel_with_proof` accepts documented "not found / already canceled" as
  terminal (`engine_seams.py:518-541, 585-633`). Self-healing.
- **Undispatched places**: `_forget_unmapped`/`_forget_undispatched` reject
  pending orders that never got a venue id (`session_core.py:703-719,
  762-770`), preventing the `sell_occupied` deadlock the code documents at
  `lifecycle.py:258-266`.
- **Recovery**: inventory-vs-ledger mismatch detector every cycle
  (`session_core.py:621-636`); `Recovery` marks BUYs canceling and enters
  sell_only (`lifecycle.py:366-374`); `RecoveryVerified` requires matching
  generation and zero unresolved BUYs (`lifecycle.py:377-397`) — SELLs
  deliberately excluded (docstring `:378`); partial fills during recovery
  re-arm verification (`_reenter_recovery`, `lifecycle.py:409-416, 659-664`).
- **Budget model**: `budget_from_orders` = collateral − core reservations −
  durable-command reservations − unmapped wallet-store reservations
  (`session_core.py:839-848`); fill price equals reserved price basis so the
  collateral-cache staleness between `positions()` polls nets out (a fill
  drops reservation by exactly the collateral spent).
- **Order-book hygiene**: strategy books strip own accepted orders per level
  (`session_core.py:262-311`), skip not-yet-echoed accepts
  (`session_core.py:241-242`), and go unavailable when stripping empties a
  side (`:277-279`) — no self-quoting into own size. Book freshness uses the
  receipt-timestamp mapped wall→mono (`session_core.py:178-179, 305-306`);
  `is_fresh` rejects future stamps (`signals.py:39-43`). Raw book (with own
  orders) is used only for the model input/anchor — see S2-3.
- **Signal/lifecycle timing parity**: live `RawDeltaSignal.received_ns` is
  enqueue-time mono (`match_worker.py:481`); backtest is the feed ts
  (`strategy.py:1134`) — both mean "signal production time"; staleness
  semantics equivalent.
- **Latency model**: insert 85ms via Nautilus `StaticLatencyConfig`
  (`run.py:273-289`); cancels modeled by the strategy-owned 85ms timer +
  0ms venue (`strategy.py:821-836, 561-565, 592-602`) — matches live where
  the REST response is the ack. `NETWORK_LATENCY_MS=85.0` is recorded in run
  metadata.
- **Feed freshness parity**: live `entry_stale_s` = the feed's own
  `stale_seconds` (Oddin 15, GRID 16 — `match_worker.py:174-179`,
  `wallet_host.py:1323`); backtest `entry_stale_seconds` returns the same
  per-source constants (`feed_schedules.py:385-389`); exit staleness 45s both
  (`EXIT_FEED_STALE_SECONDS`, `run.py:453`, `match_worker.py:994`); book
  staleness `MAX_BOOK_AGE_SECONDS` both.
- **Feed-schedule integrity**: archived schedules bound by identity
  fingerprint with explicit mismatch exclusion, grid-v1 only as a stated
  fallback (`feed_schedules.py:147-230`) — no silent binding swaps.
- **Kill gate**: board-kill → token-side quote hold until scoreboard deaths
  catch up or `until_ns` elapses (`src/strategy/kill_gate.py:21-37`);
  live `_on_kill_tick` arms it with mono timestamps
  (`match_worker.py:451-471`); backtest replays recorded gate updates
  (`strategy.py:295-304, 476-483`).
- **Mid-spike**: drop-from-peak over a 10s lookback, cooloff 30s, watches
  exposed tokens (both while flat) (`src/strategy/mid_spike.py:39-97`);
  operates on stripped mids so own cancels cannot self-trigger it.
- **SELL path**: single-exit-slot design — one SELL order at a time,
  `sell_occupied` includes canceling (`lifecycle.py:258-267`); min-life 1s
  before reprice (`quoting.py:446-450`); exit-settle hold 10s after last buy
  (`quoting.py:453-468`, `lifecycle.py:274-289`); dust inventory skips SELL
  and keeps the episode (`quoting.py:493-503`).
- **Poly-maker seams, no fork edits**: place/cancel wrapped for durable
  journaling (`wallet_host.py:417-464`), strict REST proof
  (`engine_seams.py:518-563`), per-cid error-rate breaker replacing the
  fork's process-wide halt (`engine_seams.py:854-908`), shutdown latch on
  in-flight calls (`engine_seams.py:~250-330`), collateral snapshot on
  `positions()` reads, heartbeat boot grace (`engine_seams.py:485-515`).
  The fork's reconcile loop still iterates all tracked tokens including
  REST-absent ones (`poly-maker/src/polymaker/engine.py:570-611`,
  `by_token.get(tok, [])` at `:608`), and
  `make_esports_reconcile` returns the core's plan — checked: it does not
  drop fork safety work (positions/orders/heartbeat still run).
- **Test coverage**: `tests/test_adapter_contract.py` drives one synthetic
  tape through both adapters and asserts identical core state across 14
  scenarios (ladder open, partial fill + replace, settle→sell, race,
  cancel-timeout unknown, recovery round-trip, short budget);
  `tests/test_follow300_replay.py` replays recorded seed-0 maps against
  goldens and names drifted input fingerprints; ~19k lines of focused tests
  cover budget arithmetic, lifecycle, scheduling, kill-gate, mid-spike,
  checkpoint restore.

## Coverage gaps worth noting (not defects)

- No test exercises the S2-1/S2-2 wedges (unknown fill ownership resolution;
  venue-initiated termination) — they are unreachable in the current harness
  because the contract tape and the Nautilus adapter both refuse foreign
  fills/cancels. A tape extension (`TapeForeignCancel`, `TapeUnknownFill`)
  would cover both.
- Paper mode (`src/trader/paper_gateway.py`) was only skimmed — same core
  path, simulated fills; flagged for completeness.
- A second checkout `esports-trader-wt` exists; audit target is the main
  checkout on `main`.
