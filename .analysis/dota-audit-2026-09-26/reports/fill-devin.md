# fill-devin — backtest execution realism in Nautilus; settlement tail
Status: FINAL

Scope: Nautilus 1.226.0 matching-engine behavior under `fill_model=queue`, Telonex L2
replay conversion, onchain-fill trade ticks, latency model, fees, venue rules, capital,
settlement tail, performance. Product: `esports-trader` @ `bbb2889` —
`/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`. Framework:
`prediction-market-backtesting` pinned `c76e77af00ef53472a9da8f66dae7fdd2d3e5928` —
`/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting`. Nautilus
engine source read from the 1.226.0 matching-engine .rs files mirrored in
`work/fill-devin/nautilus-src/` (`matching_engine_engine.rs`, `matching_core_mod.rs`,
`src_exchange.rs`, `models_latency.rs`).

Primary dataset evidence: seed-0 LIVE validation run (613 maps,
`data/backtests/dota_maker/LIVE/seed0/`, fills parquet + quote_events) and full-file
inspection of `dota2-spirit1-ks-2026-09-04-game2` (match 8982107035) plus an 11-map
sample for the aggressor-inversion rate.

---

## Findings

### S1 — ~50% of onchain-fill TradeTicks carry inverted aggressor side

Every `onchain_fills` row is written into BOTH token day files (all 795 rows shared
tx_hash+log_index on the probed map; `price_0 + price_1 == 1` on every paired row).
The row's `taker_side` describes the taker's action on `taker_asset_id`, not on the
file's book. When `taker_asset_id != asset_id` of the file (the taker traded the
sibling token, or a buyer↔buyer complement match), the correct aggressor on this
book's view is the OPPOSITE side.

- `telonex.py:3253` picks `("side","taker_side","aggressor_side","trader_side")` →
  `taker_side`; `telonex.py:3283` → `telonex_onchain_fill_trade_rows`;
  `crates/core/src/telonex.rs:844-893` maps the string verbatim to
  Buyer/Seller/NoAggressor. `taker_asset_id`, `asset_id`, `maker_asset_id`, and the
  `mirrored` flag are never consulted.
- Rates: probed map token0 502/795 = 63% wrong, token1 293/795 = 37% wrong; an
  11-map × 2-leg sample of raw files gives per-leg inversion shares 0%–94%,
  ~50% aggregate (the two legs' shares sum to ~1 because every event is dual-filed).
- Independent confirmation, probed map:
  - vs `trades` channel (per-asset side, real-time stamps): `taker_asset == file`
    rows agree 67/73; `taker_asset != file` rows invert (146 buy→sell, 22 sell→buy
    on token0; mirrored counts on token1).
  - Book-shrink check: for `taker_asset != file` rows, the OPPOSITE book side shrank
    more often than the emitted side (token0 207 vs 56; token1 140 vs 81).
- Engine consequences (`matching_engine_engine.rs`):
  - `decrement_queue_on_trade` (447-537): a Buyer tick decrements only SELL orders'
    `queue_ahead`, Seller only BUY. Inverted ticks starve the actually-hit side and
    grant phantom decrements to the untouched side — both errors on every bad tick.
  - `process_trade_tick` (1527-1586) force-sets `core.ask`/`core.bid` to the trade
    price for matching during `iterate`. Inverted Buyer forces `bid` up → SELLs at
    ≤ trade price become fillable; inverted Seller forces `ask` down → BUYs fillable.
    This is the direct phantom-fill path.
  - `seed_trade_consumption` (335-393) writes to the wrong consumption ledger
    (`ask_consumption` vs `bid_consumption`), corrupting later book-side fill sizing.
- Introduced by framework commit `04bc79fb866dbb18846b3d4ae77d9b6caa8ee287`
  (2026-05-01, "add telonex trades fallback for execution ticks"); still present at
  pinned HEAD `c76e77af`. Affects every Telonex book replay, all seeds.
- Sign of PnL bias is not established here (phantom fills inflate, missed decrements
  deflate); the mechanism corrupts the primary fill path — trade ticks are THE way
  bid-joined maker orders fill — so this is S1 on mechanism + breadth. Companion
  quantification is being run offline (fill-grok queue-impact report).
- Fix sketch (not applied): per-row `aggressor = taker_side if taker_asset_id ==
  file.asset_id else flip(taker_side)`; verified consistent for all four observed
  match shapes (normal token-USDC legs and token↔token complement matches).

### S1/S2 — Trade ticks are stamped at block-seal time with zero ingest latency

`onchain_fills` has only `block_timestamp_us` (whole-second; ~3 fills/block on the
probed map). The converter sets `ts_event = block_ts + min(occurrence,999)ns` and
`ts_init = ts_event` (`telonex.rs:838-846, 870-871`).

- Real-time reference (`trades` channel `timestamp_us`): lag p10 −8.5s,
  p50 +1.95s, p90 +14.5s — i.e., fills replay ~2s late on median, some up to ~15s.
- `seed_trade_consumption` is guarded: `book.ts_last > trade.ts_event → skip`
  (line 348). `decrement_queue_on_trade` has NO equivalent guard — a fill whose
  consumption was already visible in a later book snapshot still decrements
  `queue_ahead` a second time → double-decrement → premature fills. Combined with
  `cap_queue_ahead` on every UPDATE delta (level-size clamp, lines 624-670, invoked
  at 1011-1017), the queue ledger is noisy in both directions.
- `core.last` is set from these late ticks (line 1509). The `crossed` escape in
  `determine_trade_fill_qty` (557-570) reads `core.last` and persists between
  events: once a stale/wrong-priced trade prints through an order's price, that
  order's queue gate stays defeated for subsequent book-side fill attempts until
  another trade resets `last`. Amplifies both the inversion and the timestamp skew.
- The realtime `trades` channel exists on disk (per-asset `side`, µs timestamps)
  but is never loaded — replay trades come only from `onchain_fills`
  (`replay_adapters.py:1912-1915` → `_load_trade_ticks` → onchain fills loader).
- Gamma `secondsDelay=1` (archived terms; `check_gamma_trading_terms.py`) is not
  modeled anywhere — sim orders post 85ms after decision; live, the venue holds a
  new order for 1s before it joins the queue. This systematically overstates queue
  priority: sim `snapshot_queue_position` reads the level ~1s earlier than a real
  order would arrive, missing ~1s of queue accrual in front of us.
- Within-block ordering (`ts+occurrence ns`) preserves parquet row order — sane,
  unverified against true log order; second-order issue.

### S2 — Own-order stripping only removes the same-token copy; the mirrored copy survives

Polymarket books are complementary views: verified empirically that
`book(token0).bids[p].size == book(token1).asks[1−p].size` level-for-level on the
probed map. The live bot's resting buy on token0 therefore appears BOTH in token0's
bids and in token1's asks.

`strip_own_book.py` subtracts our resting size only on `token_index == order's
token` (`_subtract_map` 245-254, applied per file in `strip_book_frame` 257-281;
wired at `telonex_local.py:113-142` only when an archive exists). The mirrored copy
of our token0 order remains as phantom depth in token1's book:

- A sim SELL on token1 queues behind our own mirrored buy (phantom `queue_ahead`,
  conservative underfill).
- A sim BUY on token1 can fill against ask depth that is actually our own order
  (self-match phantom fill).
- It also distorts the decrement accounting on that leg.

Applies to the 149 schedule/archive-bound maps of 613 in seed 0 (grid_v1 maps have
no archive and no strip; if the live bot traded them anyway, the doppelganger is
present on both legs there too — unverified). Severity S3→S2 boundary; kept at S2
because it touches queue accounting on every stripped map, though our sizes (~100-300
shares) are small relative to typical level sizes.

### S3 — Queue-position bookkeeping: what actually clears and when

Correction to the orchestrator note: `clear_all_queue_positions` fires only when a
delta carries `flags & 32` or `action == Clear` (`process_order_book_deltas`
1056-1061). The Telonex converter emits `BOOK_ACTION_CLEAR` ONLY for the first
in-window snapshot per `parquet_book_snapshot_diff_rows` call (`emitted_snapshot`
gate, `telonex.rs:358,372-383`); subsequent snapshot rows emit UPDATE/DELETE diffs
only. So queue state is NOT reset per snapshot — it persists and churns through:

- `cap_queue_ahead` on every UPDATE at our price: clamps `queue_ahead ≤ level size`.
  Level-shrink → partial decrement; then the (block-late) trade decrements again —
  the double-count above.
- `clear_queue_on_delete` (606-622): DELETE of our level sets `queue_ahead := 0`
  and `cap_queue_ahead` never re-raises it (only lowers). On a delete+re-add churn
  (common at 6ms book cadence) our order is permanently promoted to front-of-queue
  → optimistic fill bias.
- `snapshot_queue_position` (406-444) sets `queue_ahead =` FULL visible size at our
  price at accept time — correct only if the order is last at the level (true for
  a fresh join), but any later growth of the level does NOT raise `queue_ahead`
  (cap only lowers), so the model cannot represent "orders queued BEHIND us" — a
  one-sided approximation that errs toward under-estimating competition over time.
- The first-snapshot-per-part pattern means queue clears also depend on parquet
  partitioning, not on data content — fragile coupling.

None of these individually is S1, but they make `queue_ahead` an unreliable
estimate even before the aggressor bug. With aggressor fixed, `cap_queue_ahead`
still applies the level shrink once, so the trade decrement should be suppressed
when the book already reflects the fill — the `book.ts_last > trade.ts_event`
guard that `seed_trade_consumption` already has is the right pattern.

### S3 — `gate_seconds` are event counts, not seconds

`results.py:159-163` `_count_gate_seconds` still counts `no_quote` events as "1 Hz
seconds". Since the cadence moved to event-driven (100ms coalescing + 2s fallback),
the gate_*_seconds fields in summary.json are event counts mislabeled as durations
(e.g., 19,763 "seconds" on one map). Telemetry-only; misleads anyone reading gate
breakdowns. (Documented earlier in `.learnings/esports-trader-backtest-performance`.)

### S3/S4 — Cancel timing: single-leg model, no round trip

- Inserts: engine `insert_latency_ms=85` via `StaticLatencyConfig` (run.py:282-285)
  — submit→accept p50/p90 ≈ 85ms (seed-0 probe: 40,289 accepted of 40,421
  submitted; fills-before-accept = 0 — no phantom early fills).
- Cancels: `cancel_latency_ms=0` at the engine; the strategy itself delays the
  request by `cancel_latency_ns=85ms` via a clock alert (`strategy.py:821-836`).
  Observed cancel request→ack p50 ≈ 85ms, max ≈ 170ms.
- Net: cancel takes ~85ms from decision, i.e., models the outbound leg only; the
  ack reaches the strategy ~85ms earlier than a real round trip (~170ms). Material
  only where the strategy acts on the ack (re-quoting) — bounded: at most ~0.4-0.8%
  of cancels would have caught a contra-side trade at our price inside +60/+200ms
  of extra delay (trades-channel probe: 142/36850 at +60ms, 291 at +200ms), and
  most would still be blocked by queue-ahead.
- In-flight fills during the cancel window are realistic and DO occur: 280/3282
  seed-0 fills land while a cancel is pending — correct behavior, correctly modeled.

### S4 — Venue rules: mostly right, two gaps

- Tick: hardcoded `TICK_SIZE=0.01` (maker_orders.py:21) with non-crossing rounding
  helpers (`round_buy_price` floors, `round_sell_price` ceils; `is_on_tick_grid`).
  Polymarket 0.01 tick markets only — a 0.001-tick market would silently misprice.
- Min size: `MIN_ORDER_SIZE=5` enforced strategy-side (`strategy.py:801,808` gate
  sells; buy sizing floors shares). The engine instrument does NOT enforce min qty;
  dust positions (<5) are flagged in results (`dust_position`, results.py:210).
  Sim can post sizes the venue would reject only via paths that skip the gate —
  none found; residual = strategy enforcement only.
- `secondsDelay=1`: unmodeled (see S1/S2 entry); live-sidecar `min_order_size` can
  change on the fly while the sim constant is frozen (postprocess note, line ~97).

### S4 — Instrument/resolution metadata comes from Gamma at replay-load time

`PolymarketTelonexBookDataLoader.from_market_slug` → `PolymarketDataLoader
.from_market_slug` (`loaders.py:773-865`) fetches market details from Gamma for
every replay: instrument metadata, outcome, and the `winner` flags that drive
`realized_outcome`. A disk metadata cache (`loaders.py:131-260`) with TTL —
longer for closed markets — makes re-runs mostly offline-capable and usually
stable, but TTL expiry or a changed Gamma payload can alter instrument metadata
between nominally identical runs (non-hermetic replay), and a hard failure mid-run
silently degrades settlement to mark-to-market (warning only). Per-leg outcome
mapping is verified correct.

### Settlement tail — verified mechanically sound; all-winners is expected, not a bug

- `install_settlement_compatibility` rewrites each leg's `expiration_ns =
  market_closed_at` and `taker_fee="0"` (run.py:292-306). No `InstrumentClose` is
  emitted → positions stay open to engine end (native engine expiration would have
  force-filled at settlement price — deliberately bypassed).
- `ReplayEndBoundary` (run.py:309-324, appended to sim[0]) advances the clock to
  `max(clock_end) = market_closed_at + 1min`, so `simulated_through ≥
  settlement_observable_ns` and `apply_binary_settlement_pnl`
  (`_result_policies.py:388-446`) marks residual positions at `realized_outcome`.
  Both failure modes (missing `simulated_through`, window ends before observability)
  degrade to warnings + mark-to-market — fail-safe, not silent.
- `market_closed_at > game_ended_at` on all 613 seed-0 maps (min +1,898s) → the
  `validate_order` expiration rejection never fires mid-game in this cohort.
- Per-leg settlement math (`compute_binary_settlement_pnl`,
  `backtest_utils.py:356-398`): cash + outcome·open_qty, per-token `realized_outcome`
  from the leg's own Gamma winner flag → no yes/no mixup (the `contract_side`
  default-"yes" fallback is harmless here because outcome is already per-leg).
- `results.py:197-240`: `cash_flow` = raw fill tape; `engine_pnl` = Σ leg pnl
  (settlement-adjusted); `terminal_position` from last fill's `position_after`.
  Series mode can substitute terminal bids (`marks.py`) — not used in LIVE runs.
- Tail mechanism (orchestrator N1 numbers confirmed consistent): buys ladder in
  early; `decide_sell` (`quoting.py:537-560`) — `is_settling` blocks all sells for
  10s after `game_ended_at` (`exit_settle_seconds=10`), then `_choose_sell_target`
  posts at `max(join_ask, ceil(fair))`. Winner: fair→1 → parked at 1.00, never
  lifted → settles $1. Loser: fair collapses → joins the ask → exits. So held
  positions are winner-selected BY POLICY; 41/41 held being winners (seed 0) is the
  expected outcome of this design, not a settlement artifact.
- Freshness interaction worth flagging: when the feed goes stale,
  `_fair_at` returns NaN → `decide_sell(fair=None)` → `_join_ask_sell` — sells at
  the ask with no fair floor (quoting.py:551-554). Schedule-mode feeds stale at
  45s (orchestrator: grid_v1 never stales) — so archive maps dump winners at
  market instead of parking to settlement. Mechanically consistent with the mode
  table (31/41 held are grid_v1) — but it means schedule-mode settlement-tail PnL
  understates what a live feed would produce, and if a synthetic grid feed keeps
  emitting post-game-end "fresh" fair, grid_v1's park-to-$1 may overstate realism
  in the other direction. Mode-dependent bias, not a bug.
- The book replay ends at `game_ended_at + 1min` (`context.py:26-33`); the
  post-game market life (minutes-hours to resolution) is deliberately not replayed.
  Settlement-at-$1 vs selling into the post-game ask (~0.97-0.995) is real money —
  the choice is defensible, documented as the strategy's intent.

### Fees — correct per archived terms, with caveats

- Engine taker fee zeroed (`rewrite_replay_instrument`); postprocess applies the
  archived sports fee: rebate = 0.15 × 0.05 × qty × p×(1−p) on maker fills
  (`postprocess.py`). All 3,282 seed-0 fills are maker, fee=0; mean rebate $0.119,
  max $1.28. `post_only=True` + POST_ONLY rejects prevent taker fills.
- Framework `PolymarketFeeModel` is bypassed (it would apply the generic schedule
  — e.g., 25% rebates — not the sports 15%). Intentional, documented.
- Liquidity Rewards are NOT modeled — conservative (PnL understated). Rebate is not
  cash at the next buy (UTC-day payout, ≥$1 threshold) — noted in ASSUMPTIONS.
- `secondsDelay=1` unmodeled is the biggest fee-adjacent gap (folded into S1/S2).

### Capital — fake balance is documented and non-binding

`ENGINE_STARTING_BALANCE=$1M` is explicit: batch-isolation, not modeled capital
(run.py:200-205). Per-episode spend is policy-capped (layers × layer_usdc =
3 × $100 = $300/match). `wallet_path` derives real capital from the fill tape:
seed-0 `required_cash` ≈ $346, `peak_reserved` ≈ $400, `maps_at_once` = 3 — the
$1M balance never binds, and no fill is capital-gated. Live-side insufficient-funds
rejects are outside scope; documented.

### Performance — known hotspot already mitigated; residual profile

- `.learnings/esports-trader-backtest-performance-2026-09-07`: 84% of wall time was
  post-framework quote-event serialization (O(N²) parquet rewrites, ~2.5M events).
  Now fixed: `quote_store.py` writes per-match Arrow parts, one compaction pass at
  end (lines 88-171). Observed run pace ~3s/match worker-side on a later ladder run
  (`wall_seconds=1346` / 454 maps), vs ~30.7s/match total before the fix.
- `MAX_MATCHES_PER_BATCH=1` (run.py:196-198) — per-match engine spin-up measured
  <0.25s overhead vs batch 8; dominant cost remains parquet load/diff (dense
  ~6ms-median book cadence ⇒ ~400k+ snapshot rows/map/token leg).
- `REPLAY_LOAD_WORKERS=8` configured but capped by replay_count=2 per batch —
  effective fan-out is 2 (noted in the learning; minor).
- Memory: prior footprints 2.9-4.8 GiB/seed process; parts-based store reduces the
  growing-list accumulation.

---

## Verified-correct areas

- Order insert latency is real (85ms), cancels are delayed before hitting the venue,
  in-flight fills during pending cancels occur and are counted correctly.
- No fills before accept; post_only enforced; no taker fills leak in (0/3282).
- Merge ordering is stable: `(ts_event, kind, ts_init, source_order)` with book
  before trade at identical ts (crates/core/src/merge.rs:13-19,103-140) — with
  microsecond book stamps vs second-stamped trades, true same-ts collisions are rare;
  the misordering is upstream (block-seal delay), not in the merge.
- `fill_limit_inside_spread` defaults false → no inside-spread fills; fills require
  a true cross or trade-driven queue clear (matching_core_mod.rs:315-366).
- `assert_every_leg_loaded` refuses partial-leg replays (run.py:337-346).
- Settlement: per-leg outcomes, correct direction, fail-safe warnings, no double
  counting between engine fill PnL and postprocess settlement (verified
  decomposition: engine_pnl = cash_flow + settlement_part on seeds 0-2).
- Strip-cache invalidation: keyed by source stat, archive file stats, and a hash of
  `strip_own_book.py` itself (telonex_local.py:120-140, 162-178).
- Quote-event store rewrite removed the O(N²) write path; telemetry on disk is
  consistent (schema-verified compaction).
- Books are exact complementary mirrors (token0 bid@p ≡ token1 ask@(1−p)) — feeds
  both engines correctly modulo the aggressor flag.

## Confidence and open items

- S1 aggressor inversion: certain on mechanism, rate, and persistence since 04bc79f;
  impact sign pending fill-grok quantification. Re-run of seed 0 with the one-line
  flip fix would settle the PnL delta.
- Block-timestamp effects: lag distribution measured vs `trades` channel; the
  double-decrement and `core.last` leaks are code-proven; combined impact unquantified.
- Sibling-mirror strip gap: mechanism proven on one map's paired books; frequency of
  material queue distortion not measured.
- Not exhaustively checked: whether any seed-0 map's onchain_fills are empty
  (eligibility requires the file present, not non-empty) and the LoL path, which
  shares the same converter bugs.
