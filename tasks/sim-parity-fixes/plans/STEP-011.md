# STEP-011 plan: strip own orders like live `books_from_md` (B5)

Repo `esports-trader` (C) on `main`, HEAD `74546402`. All line numbers are at that commit.

## 0. Result

After this step, the archive book the sim sees is stripped by the same rules as live `books_from_md`:

- Our size is matched to a book level by the exact venue price. The key is `round(price / 0.0001)`, not the cent.
- Our order is also subtracted from the mirror on the other token at `1 − price`. An own BUY on one token removes an ask on the other token. An own SELL removes a bid.
- A level whose foreign remainder rounds to `<= 0` at 6 decimals is dropped, so it can never be the best level. A kept level carries that rounded foreign size.
- A two-sided run strips only wallet B's own trace, mirror included. Wallet A's orders in the book stay foreign. This rule already exists (`resolve_strip_archives`). The step adds the missing test.

Only `src/backtest/strip_own_book.py` changes in `src`. The live trader is not touched. Follow300 Dota, follow300 LoL, and two-sided all use this one strip.

## 1. Evidence

### 1.1 Live (what the sim copies)

`src/trader/book_strip.py`:

- `_FINEST_VENUE_TICK = 0.0001` and `_VENUE_SIZE_DECIMALS = 6` (`:14-15`). `_price_key` is `round(price / _FINEST_VENUE_TICK)` (`:18-19`).
- `_own_qty` (`:122-155`) builds own size per price key for one token and one side. An order on the other token counts as a mirror: `mirror = order.token_index != token_index`, it is skipped when `(order.side == side) == mirror` (`:132-134`), and its key is `_price_key(1.0 - order.price)` (`:135`). So bids of a token subtract own BUYs on that token and own SELLs on the other token. Asks subtract own SELLs on that token and own BUYs on the other token. Size is `submitted_qty − filled_qty` (`:136`), summed per key (`:139`).
- `_best_foreign` (`:170-189`) walks levels best-first. `foreign = round(size − own, 6)` (`:177-179`). The first level with `foreign > 0.0` is the best level, and its size is `foreign` (`:187-188`).
- The own orders are `core.state.orders` (`src/trader/session_core.py:953-964`), the core's active orders. One `books_from_md` call serves A and B: `core_books` is called by the quote adapter (`src/trader/host_resources.py:100`) and by `MatchWorker` (`src/trader/match_worker.py:507`). B inherits it (`investigations/2026-10-10-sim-followup/reports/port.md:117`).
- The live tests that pin these rules are in `tests/test_trader_session_core.py`: exact price `:403-441` and `:443-449`, float dust `:474-577`, mirror on both books `:619-733`.

### 1.2 The sim today

`src/backtest/strip_own_book.py`:

- `_PRICE_TICK = 0.01` (`:23`), used by `_price_key` (`:46-48`). On a 0.001-tick market, an own BUY at 0.38 shares key 38 with a level at 0.385 or 0.384. `_strip_levels` pops the key at the first level that has it (`:239`), so the wrong level is cut and the own level stays.
- `_subtract_map` (`:246-255`) skips every order on the other token (`:251-252`). The mirror stays in the book as foreign.
- `_strip_levels` (`:219-243`) keeps a level when `size − cut > 1e-9` (`:240-242`) and writes `format(new_size, ".10g")`. A remainder in `(1e-9, 5e-7)` stays as a level that live skips. A kept 1e-6 remainder is written as `1.00000014e-06`, not the `1e-06` live reports. `.10g` also drops the 6th decimal for sizes ≥ 10 000.
- `strip_book_frame` (`:258-282`) rebuilds resting from the trace for each row, then calls `_subtract_map` and `_strip_levels` for bids (`side="BUY"`) and asks (`side="SELL"`) (`:276-280`).
- `resting_by_token_at` (`:201-216`) uses the same `_price_key`. Only tests call it (`rg resting_by_token_at src` finds just its definition).

### 1.3 B-only is already in place, and untested

- `resolve_strip_archives` (`src/backtest/run.py:2208-2219`): two-sided → `own_archive_dirs(plans, args.own_archive_root)`, follow300 → `schedule_archive_dirs(plans)`.
- `own_archive_dirs` (`src/backtest/feed_schedules.py:389-404`) returns `<root>/<A archive dir name>` when that dir exists. With no root it returns `{}`.
- `create_telonex_source_tree` (`src/backtest/telonex_local.py:484-514`) strips one archive per match and skips the strip when that dir has no `core_trace.jsonl[.gz]` (`:500-505`).
- `link_market_tokens` (`:189-239`) strips both tokens' book days with the same event tuple (`resting(archive)`, `:203-208`). So the mirror can be done inside `_subtract_map` with no loader change.
- `rg "resolve_strip_archives|own_archive_dirs|schedule_archive_dirs" tests` finds nothing.
- B traces work with the strip. They come from STEP-005's `TwoSidedTraceHeader`, whose clock is read by `clock_offset_ns` (`tests/test_core_trace.py:804`), or from `scripts/journal_to_core_trace.py`. Both write the shared `Plan` shape (`src/strategy/types.py:510-527`, `src/trader/core_trace.py:108-118`).

### 1.4 Nothing else in the adapters needs a change

- The sim's own orders never enter the data book. The core sees the best levels of the stripped Nautilus book: `_snapshot_books` (`src/backtest/strategy.py:1291-1322`) is fed from `_apply_received_book` (`:412-426`).
- `TwoSidedCoreStrategy._evaluate` (`src/backtest/two_sided_core.py:171-178`) reads the same stripped YES book for its skip. See §5.
- So "the book view in the adapters" from PLAN.md B5 is fully the strip. `strategy.py` and `two_sided_core.py` do not change.

### 1.5 Real-data check (read-only, 3 short queries)

Archive `data/trader/grid-3005975-m2` (match 8996565323), as used by `test_archive_entry3_bid066_strips_to_foreign`:

- At `accept_us = 1789286149340232`, `resting_by_token_at` gives `{0: {('BUY', 66): 45.45, ('BUY', 65): 46.15, ('BUY', 64): 46.87}}`. All of these are A's BUYs on token 0. Nothing rests on token 1.
- Token 1 raw row at `1789286149339000`: asks `0.34 → 101.43`, `0.35 → 46.15`, `0.36 → 46.87`. These are the mirrors of the token 0 bids. 0.35 and 0.36 are entirely A's size.
- The test returns early today. It checks `(arch / "core_trace.jsonl").is_file()` (`tests/test_strip_own_book.py:100`), but the archive holds only `core_trace.jsonl.gz`. Both Telonex book days exist locally.

## 2. Code changes (`src/backtest/strip_own_book.py` only)

### 2.1 Exact venue price

- `:23`: replace `_PRICE_TICK = 0.01` with `_FINEST_VENUE_TICK = 0.0001`, the same name and value as `book_strip.py:14`.
- `_price_key` (`:46-48`): divide by `_FINEST_VENUE_TICK`. Delete its docstring ("0.01 Polymarket prices" becomes wrong).
- Do not import the private names from `trader.book_strip`, and do not move them to `src/shared`. That would touch a live file for two constants. The parity test in §3.2 fails if the two modules drift.

### 2.2 Mirror on the other token

`_subtract_map` (`:246-255`), signature unchanged:

- An order on this token keeps key `(side, _price_key(price))`.
- An order on the other token gets key `(opposite side, _price_key(1.0 - price))`. A BUY becomes a SELL-side cut (an ask) and a SELL becomes a BUY-side cut (a bid). This is the same as `book_strip.py:132-135`.
- Quantities still sum per key, so a native SELL at 0.42 on token 0 and a mirrored BUY at 0.58 on token 1 cut one ask level together (live case `native-and-mirror-one-level`).
- One small module mapping for the opposite side (for example `_MIRROR_SIDE = {"BUY": "SELL", "SELL": "BUY"}`) keeps this to one readable line. `1 − 0.26` is `0.74` within float noise; `round(... / 0.0001)` gives 7400.
- Delete the `strip_book_frame` docstring (`:261`, "this token's live resting size"). It becomes wrong, and the name says enough.

### 2.3 Dust level is not best

`_strip_levels` (`:219-243`):

- Add `_VENUE_SIZE_DECIMALS = 6` next to the tick (same name as `book_strip.py:15`).
- Replace `new_size = size − cut` / `if new_size > 1e-9` with `foreign = round(size − cut, _VENUE_SIZE_DECIMALS)` and keep the level only when `foreign > 0.0`.
- Write the kept size as `str(foreign)`, not `format(new_size, ".10g")`. The value is already rounded to 6 decimals, so the shortest repr is exact, and it matches the `foreign` live reports. It costs no extra code. The framework reads book level sizes with Rust `parse::<f64>` (`prediction-market-backtesting/crates/core/src/telonex.rs:764-772`), which accepts `1e-06`.
- Price strings stay as `str(item["price"])`.

### 2.4 Not changed

- `resolve_strip_archives`, `own_archive_dirs`, `schedule_archive_dirs`, `telonex_local.py`, `run.py`, the adapters: no change (§1.3, §1.4).
- `_apply_resting_op` keeps its `left <= 1e-9` pop and `iter_resting_events` keeps its events. See §4 for the two places where the resting model still differs from live `state.orders`.
- No new comments. The module docstring (`:1-11`) and the `telonex_local.py` docstring (`:9-10`) stay true as written.

## 3. Tests

### 3.1 Existing tests to update (the key unit changes from cents to 0.0001)

- `tests/test_strip_own_book.py:21-23` `test_price_key_ticks`: `_price_key(0.66) == 6600`, `_price_key(0.64) == 6400`. Add `_price_key(0.385) == 3850`.
- `:93` `test_fill_reduces_resting`: key `("BUY", 6600)`.
- `:154` `test_cancel_unsettled_removes_buy`: key `("BUY", 4700)`.
- `tests/test_journal_to_core_trace.py:75-76, :78, :82`: keys `("BUY", 4200)`, `("BUY", 5200)`, `("BUY", 5100)`.
- `tests/test_strip_streamed.py`: no change expected. Its events are token 0 BUYs only, and its asserted sizes (55.98, 101.43) are unchanged.

### 3.2 New tests in `tests/test_strip_own_book.py`

Move the inner `event(...)` helper out of `test_cancel_unsettled_removes_buy` (`:123-133`) to module level, with a place-dict helper next to it, so the archive test below can write traces. Add one helper that builds a one-row book frame at `timestamp_us = 2_000` from `(price, size)` tuples, and one that reads levels back as floats.

1. `test_live_buy_strips_its_bid_and_the_mirrored_ask` (the feature's example). Accept event: BUY token 0, 0.26, qty 100.
   - Token 0 frame: bids `(0.26, 100), (0.25, 50)`. After the strip only `0.25 → 50` is left.
   - Token 1 frame: asks `(0.74, 100), (0.75, 50)`, bids `(0.72, 60)`. After the strip asks are only `0.75 → 50`, and bids are unchanged.
   - Fails today: token 1 is never cut.
2. `test_strip_cuts_only_the_exact_venue_price`. BUY token 0, 0.38, qty 100. Bids `(0.385, 40), (0.384, 25), (0.38, 100), (0.379, 30)`. After the strip: `(0.385, 40), (0.384, 25), (0.379, 30)`.
   - Fails today: `round(0.385 / 0.01) == 38` (checked in Python), so the 0.385 level is cut instead.
3. `test_strip_drops_a_level_left_with_sub_micro_dust`, parametrized. Accept SELL token 0, 0.60, qty 1758.8, plus a fill of 1603.66, so the remainder is `155.13999999999987`. Asks start with `(0.60, size), (0.62, 49.99)`.
   - `size = 155.1400004`: the remainder is `4.0e-07`, which rounds to 0. 0.60 is gone and the best ask is 0.62. Fails today (kept, because `4e-7 > 1e-9`).
   - `size = 155.140001`: 0.60 is kept with `float(size) == 1e-06`. Fails today (written `1.00000014e-06`).
4. `test_stripped_book_p_matches_live_books_from_md`, parametrized. For each case, build the same YES levels and the NO complement, like `_mirrored_books` in `tests/test_trader_session_core.py:598-606` (`round(1 − p, 4)`, bids ↔ asks).
   - Live side: polymaker `OrderBook(tick_size=0.01)` + `apply_snapshot`, then `trader.book_strip.books_from_md` with `RestingOrder`s (`accepted=True`, `status="live"`, `accepted_ns=-1_000_000_000_000`, as the live helper at `:295-322` does).
   - Sim side: the same orders as strip events (accept at 1_000 with `qty`, and one fill of `filled` at 1_500 when `filled > 0`, so the remainder is `qty − filled`, as live computes it). Then `strip_book_frame` on both token frames.
   - Assert per token: (bid, bid_size, ask, ask_size) of the sim's best levels (max bid, min ask) equal the live `TokenBook`. Assert `strategy.signals.book_p_radiant(books=..., radiant_token_index=0, tolerance=PAIR_SUM_TOLERANCE)` is equal for both pairs (`PAIR_SUM_TOLERANCE` from `shared.utils.telonex_book`).
   - Cases. Each fails today on at least one rule:
     - `yes-buy`: YES bids `(0.26, 100), (0.25, 50)`, asks `(0.28, 60), (0.29, 40)`; own BUY t0 0.26 × 100.
     - `no-sell-partial`: YES bids `(0.40, 100), (0.39, 50)`, asks `(0.42, 60), (0.43, 40)`; own SELL t1 0.60 × 100, filled 40.
     - `native-and-mirror-one-level`: same levels; own BUY t0 0.40 × 60 and SELL t1 0.60 × 40.
     - `exact-venue-price`: YES bids `(0.385, 40), (0.384, 25), (0.38, 100), (0.379, 30)`, asks `(0.40, 50)`; own BUY t0 0.38 × 100.
     - `sub-micro-dust`: YES bids `(0.519, 155.1400004), (0.518, 12.5)`, asks `(0.53, 20)`; own SELL t1 0.481 × 1758.8, filled 1603.66.
     - `one-micro-share-stays`: same, with bid `(0.519, 155.140001)`.
5. `test_two_sided_strips_wallet_b_and_its_mirror_not_wallet_a`.
   - A archive `tmp/trader/grid-m7`: trace with A's BUY t0 0.41 × 50 accepted. B root `tmp/trader_b`, B archive `tmp/trader_b/grid-m7`: trace with B's BUY t0 0.40 × 30 accepted.
   - The plan: `feed_schedule_fixtures.build_schedule_plan(7, build_schedule("grid-m7", 7, [build_tick(0, 0, 0)]), model_dir=tmp, tmp_path=<A dir>)`.
   - `backtest.run.resolve_strip_archives(argparse.Namespace(strategy="two-sided", own_archive_root=<B root>), {7: plan}) == {7: <B dir>}`. The same call with `strategy="follow300"` returns `{7: <A dir>}`.
   - Strip with `load_resting_events(<B dir>)`. Token 0 bids `(0.41, 50), (0.40, 30)` become `(0.41, 50)` only. Token 1 asks `(0.59, 50), (0.60, 30)` become `(0.59, 50)` only.
   - A's level and A's mirror stay. B's level and B's mirror go.
6. Re-enable `test_archive_entry3_bid066_strips_to_foreign` (`:98-117`).
   - Change the guard at `:100` to `resolve_jsonl(arch / "core_trace.jsonl").is_file()` (`shared.utils.jsonl_io`).
   - Keep the token 0 assertion (bid 0.66 → 55.98 ± 0.01; nothing rests on token 1, so the mirror does not move it).
   - Add the same window for `token_ids[1]`. In the last stripped row at or before `accept_us`, ask 0.34 is `≈ 55.98` (101.43 − 45.45), and asks 0.35 and 0.36 are absent (§1.5). If 0.35 or 0.36 is present at that row, check `resting_by_token_at` at that row's timestamp before changing the assertion.
   - Locally this is the only test of the strip on real Telonex data. Without the data it still returns early.

Every rule has at least one test that fails on today's code:

- tick: tests 2 and 4 `exact-venue-price`;
- mirror: tests 1, 4, 5, and 6;
- dust: tests 3 and 4 `sub-micro-dust` / `one-micro-share-stays`;
- B-only: test 5. It fails if two-sided resolves A's dir.

## 4. Edge cases and remaining differences (not changed in this step)

- **Core-closed remainder.** Live drops an order from `state.orders` when a fill leaves `share_floor(remaining) <= 0` (`src/strategy/lifecycle.py:686-711`, `_complete_buy_order` `:602-610`). After that, `books_from_md` no longer subtracts it. The trace has no event for that close, so the sim keeps subtracting a remainder below 0.01 share until a later remove event. With traces from the fixed core, no such event may ever come.
  - Ceiling: a phantom cut of < 0.01 share at one price.
  - Upgrade path: pop in `_apply_resting_op` when `share_floor(left) <= 0.0`.
  - Left out because it is not in the feature list, and the venue behaviour of a resting sub-cent remainder is unverified.
- **Accept versus snapshot time.** Live skips an order accepted after the snapshot (`book_strip.py:163-164`). The sim starts resting at the place time on purpose, because Telonex prints our size before `OrderAccepted` (`strip_own_book.py:125-127`). Unchanged.
- **Canceling and gone.** Live keeps cutting a `canceling` order and stops at `gone`. The sim removes an order on `CancelAck` / `CancelUnsettled` / `OrderRejected` (`:175-178`), which is the same moment in the trace.
- **Float paths.** The sim subtracts fills one by one, and live subtracts `submitted − filled`. Results differ by about 1e-13. The 6-decimal round absorbs this except at an exact 5e-7 tie. The parity test uses one fill to match live exactly.
- **Token order.** The mirror assumes the trace's `token_index` follows `context.token_ids` order. The native strip already assumes this (`telonex_local.py:210`), and there are only two tokens.
- **Rows with no own order** pass through unchanged, as today. Venue sizes are whole micro-shares, so a raw level never rounds to 0.

## 5. Risks

- **Every archive replay moves.** Mirrors and exact ticks change the stripped book on archive maps for follow300 Dota, follow300 LoL, and two-sided. The goldens in `tests/test_follow300_replay.py` already fail until STEP-014. On B's day, B's own bid was best in 25 of 4862 placements (`port.md:123`), so the share change may be small. The mirror ask can matter more (§1.5: token 1 ask depth 0.35/0.36 was entirely A's).
- **Re-strip cost on the next archive run.** The tree-cache key includes the hash of `strip_own_book.py` (`telonex_local.py:292-301`), so the first archive run after this step re-strips every archive book day. Strip batches are 20 000 rows (`:60`, `:81-109`). The old cached parquet files stay in `~/.cache/esports-trader/telonex-tree`. This step does not delete them. Cleaning that cache needs the user's approval. STEP-013/014 pay the time.
- **Two-sided skip reads the stripped YES book** (`two_sided_core.py:171-178`), while poly-maker's `_recompute_locked` sees the raw book. If B's own mirror is the only YES ask, the sim now skips a cycle that live runs. This is rare and existed before for native levels. The feature keeps the two-sided skip as a non-goal.
- **Size strings.** `str(foreign)` can emit `1e-06`. The framework parses with `parse::<f64>` (§2.3). Test 3 pins the value.

## 6. Verification commands

Run from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md). Do not set `PYTEST_N` on the VPS. No backtest, training, or data rebuild is needed for this step.

```bash
# targeted: strip, its readers, and the live strip it must match
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_strip_own_book.py tests/test_strip_streamed.py tests/test_journal_to_core_trace.py \
  tests/test_core_trace.py tests/test_backtest_validation.py tests/test_feed_schedules.py \
  tests/test_trader_session_core.py

# the new and changed tests by name (expect all selected to pass; test 6 runs only with local data)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_strip_own_book.py tests/test_journal_to_core_trace.py \
  -k "price_key or fill_reduces or cancel_unsettled or mirrored_ask or exact_venue_price or sub_micro_dust or matches_live_books_from_md or wallet_b or bid066 or core_trace_feeds"

# full suite without the golden replay: expect 0 failed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck: expect 0 errors
uv run python -m basedpyright

# ruff + repo hooks on the touched files
uv run python -m pre_commit run --files src/backtest/strip_own_book.py \
  tests/test_strip_own_book.py tests/test_journal_to_core_trace.py

# leftovers: expect no output
rg -n "_PRICE_TICK|\.10g|1e-9" src/backtest/strip_own_book.py
```

Confirm by hand: `git diff --stat` touches only `src/backtest/strip_own_book.py`, `tests/test_strip_own_book.py`, and `tests/test_journal_to_core_trace.py`. Nothing under `src/trader` or `src/strategy` changes.

## 7. Commit and progress

- Commit only the three paths above (`git add <paths>`). Message in the run's style, for example `fix: STEP-011 - strip own orders by the exact venue price and from the mirrored book, and drop dust levels`.
- Do not push.
- Append a STEP-011 entry to `tasks/sim-parity-fixes/progress.txt` with the commit SHA and `passes` left false. Include the test counts, and whether test 6 ran (local data present) or returned early.
