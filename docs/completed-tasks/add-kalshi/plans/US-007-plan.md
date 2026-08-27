# US-007 Implementation Plan

Story: **US-007** — «kalshi_prior: map-load P(Radiant) из свечей»

Repo: `/root/work/dota_2_model` on `main`. Add `src/live_paper/kalshi_prior.py` (map-load P(Radiant) from 1-minute market candles, same `horn−90` anchor as Polymarket) and start the fetch from `KalshiObserve` once a ticker is bound **and** a horn-clock event has supplied horn Unix. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** add `kalshi_store` / `kalshi_session` / `kalshi_executor` (US-008+). Do **not** call `predict_fair` with Kalshi numbers (US-013). Do **not** bump `session.jsonl` (US-011). Do **not** import `kalshi` outside `kalshi_client.py`. Do **not** start auth WS.

---

## Binding decisions (feature.json / overlay / US-001..006 learnings)

Cross-ref `current-task/feature.json` FR-5, US-007 `changes`, Resolved Questions (`predict_fair(snapshot, market_p_radiant, market_radiant_prior)` — Kalshi mid/prior go in those same args **later**, US-013), Non-Goals, and `docs/plans/kalshi-overlay.md` §§ Kalshi prior, Two fairs one booster, When to search.

1. **Same anchor as PM.** `anchor_ts = horn_unix_seconds - HORN_OFFSET_SECONDS` (`90`, `shared.utils.match_time`). Do not invent a second offset. Do not use draft/`PRE_MATCH` clocks as the horn Unix (same as `pin_horn_from_event` / PM `_maybe_start_prior`).
2. **Public REST only.** Call existing `KalshiRestClient.get_candlesticks(series_ticker, ticker, start_ts, end_ts)`. Do not open `open_kalshi_auth_client`. Do not subscribe to the book. Observe already has production public REST on the host cache.
3. **Start once both inputs exist, never await in the tick loop.** Inputs: `kalshi.reason=matched` (unique ticker + `yes_is_radiant` from `KalshiBinding`, **not** PM `discovered.market.yes_is_radiant`) **and** first `HORN_CLOCK` tick in `{PRE_HORN, IN_PROGRESS}` (gives `event.horn_unix_seconds`). Either can arrive first. `asyncio.create_task`; `on_first_event` / `on_tick` / bind completion must not `await` the GET.
4. **Wire on `KalshiObserve`, not `MatchWorker._on_event`.** The first feed event `continue`s without `handle_event`, so PM prior only starts on tick 2. Kalshi prior must not inherit that. Cutoff already lives on observe (`_cutoff`); prior must respect it. `off_grid` / `none` / `ambiguous` / `error` / `off` start no fetch.
5. **Select the latest fully closed two-sided YES candle with `end_period_ts <= anchor`.** Midpoint of `yes_bid_close` and `yes_ask_close` is the YES prior; orient **once** to P(Radiant). Inclusive `<=` is the overlay rule (PM CLOB history is strictly-before — do not “fix” Kalshi to match). Reject: candle after the anchor, last-trade without both bid and ask close, event-aggregated candles.
6. **Missing history is Kalshi-only.** Empty window / no valid candle / `KalshiRestError` → Kalshi prior stays `None` (`missing_prior` for US-013). Do **not** write `SignalReason.MISSING_PRIOR`, do **not** clear `MatchWorker._prior`, do **not** skip PM `predict_fair`. Retry with bounded exponential backoff while spawn/model window can still use a prior. Never restart `resolve_kalshi_match`.
7. **Decimal until the model call.** Store `Decimal`. No `float()` in `kalshi_prior.py`. US-013 will pass `float(prior)` into `predict_fair`. Do not call the model in this story.
8. **Skip incomplete candles inside `get_candlesticks`, do not fail the whole GET.** Today `_map_candle` uses `_decimal` on `close`; a null YES bid/ask close raises `KalshiRestError` and drops the window. One last-trade bar must not wipe a valid earlier bar. Tiny client change: `_try_map_candle` → skip. Do not fall back to trade OHLC. Do not call `series.event_candlesticks` or `historical.candlesticks`.
9. **Reuse the 6h trailing window.** `start_ts = anchor_ts - QUOTE_TRAILING_SECONDS`, `end_ts = anchor_ts`. Same bound as `market_prior.py`. Leave `include_latest_before_start` unset (US-002).
10. **poly-maker is frozen.** Zero file changes there. No new dependency.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `market_prior.py` | Sync CLOB `/prices-history` on two PM tokens; `float \| None`; worker `to_thread` | **Unchanged.** Do not call it from Kalshi. |
| `MatchWorker._maybe_start_prior` / `_load_prior` / `_prior` | PM only; first-event branch skips it; HTTP error clears task (retry next tick); `None` return **latches** missing for the map | **Unchanged.** Kalshi must not latch missing; Kalshi must not share `_prior`. |
| `get_candlesticks` | 1-minute `markets.candlesticks`; required Decimal bid/ask close; null close fails the GET | Skip unmappable rows; still Decimal; still no event/historical path |
| `KalshiCandle` | `end_period_ts`, `yes_bid_close`, `yes_ask_close` | Unchanged (incomplete rows never become this type) |
| `KalshiObserve` | resolve, one `none` retry, cutoff, Telegram bind | + horn Unix, matched binding, prior task, `radiant_prior` |
| `KalshiOpenMarketCache` | `_client` private | Public `client` property so observe can GET candles without a 4th constructor arg |
| `WalletHost` | unauthenticated public REST for observe/paper/live | **Unchanged.** US-013 swaps paper/live to auth. |
| `predict_fair` / `_compute_decision` | PM mid + PM prior only | **Unchanged** (US-013) |
| `session.jsonl` | schema 5, no `venue` | **Unchanged** (US-011) |

PM retry vs Kalshi retry (do not copy blindly):

| | PM `_load_prior` | Kalshi (this story) |
|---|---|---|
| HTTP exception | clear task; next tick retries | log; sleep backoff; retry in the same task |
| Empty / no valid quote | latch `_prior is None` for the map | do **not** latch; retry while window open |
| Success | latch float | latch `Decimal` on observe |
| Threading | `asyncio.to_thread` (sync httpx) | direct `await` (already async REST) |

---

## Requirements traceability (US-007 `changes`)

| Change | Plan |
|---|---|
| `kalshi_prior.py`; `anchor_ts = horn_unix − HORN_OFFSET_SECONDS (90)` | Design § 1 |
| Fetch starts when ticker bound **and** first horn-clock event gave horn Unix; async; feed not blocked | Design § 4; start from observe, not `_on_event` |
| 1-minute market candles, bounded range to the anchor, via `kalshi_client` | Design § 2 |
| Latest fully closed candle `end_period_ts <= anchor` with valid YES bid **and** YES ask close; midpoint = YES-prior; orient once to P(Radiant) | Design § 3 |
| Reject candle after anchor, last-trade without two-sided quote, event-aggregated candles | Design §§ 2–3; client skip; source scan |
| Empty/unavailable → Kalshi `missing_prior` only; bounded backoff while model window open; matching not restarted; PM untouched | Design §§ 4–5 |
| Autotests `tests/test_live_paper_kalshi_prior.py`: `yes_is_radiant=false`, no history, candle after anchor rejected, PM tokens unused | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `HORN_OFFSET_SECONDS` | Anchor. Same import as `match_worker.py`. |
| `QUOTE_TRAILING_SECONDS` | `start_ts`. Do not add a Kalshi-specific window constant. |
| `KalshiRestClient.get_candlesticks` | The only HTTP. Already `_await_read` + `period_interval=1`. |
| `KalshiBinding.ticker` / `series_ticker` / `yes_is_radiant` | Fetch identity. Not PM token ids, not PM `yes_is_radiant`. |
| `KalshiObserve._accept` / `_on_clock` / `_apply_cutoff` / `close` | Start, horn latch, cutoff cancel, teardown cancel. |
| `KalshiOpenMarketCache` | Holds the REST client the GET needs. |
| `HORN_CLOCK_PHASES` / `MatchPhase.PRE_HORN` / `IN_PROGRESS` | Same gate as PM `_maybe_start_prior` (not `FINISHED`, not `PRE_MATCH`). |
| `MODEL_START_SECOND` / `MODEL_SIGNAL_END_SECOND` / `BUY_CUTOFF_SECOND` | Retry-until-closed; cutoff already on observe. |
| `_await_read` backoff `min(0.5 * 2**attempt, 8.0)` | Copy the same numbers for **task-level** retry (empty window is not an SDK retry). Do not import the private client constants. |
| `tests/test_market_prior.py` / `test_live_paper_kalshi_match.py` | Fake client, `asyncio.run`, no pytest-asyncio, no live HTTP. |
| `build_attached_worker` / `test_live_paper_kalshi_observe.py` | Worker-level “PM still quotes” and “fetch does not block first tick”. |

Do not import `fetch_market_prior`. Do not import `last_aligned_pre_anchor_pair` (two-token CLOB helper). Do not import `normalize_pair_mids` (Kalshi is one YES mid; inventing `dire = 1 - yes` makes the pair-sum check a no-op). Do not import `kalshi`.

---

## Design

### 1. `src/live_paper/kalshi_prior.py`

Thin module. Two functions, all arguments required (AGENTS.md). No class.

```python
def kalshi_prior_anchor(horn_unix_seconds: int) -> int:
    """Map-load anchor: horn Unix minus the 90s spawn offset."""
    return horn_unix_seconds - HORN_OFFSET_SECONDS


def select_kalshi_prior(
    candles: tuple[KalshiCandle, ...],
    anchor_ts: int,
    yes_is_radiant: bool,
) -> Decimal | None:
    """Latest fully closed two-sided YES candle at or before anchor, as P(Radiant)."""


async def fetch_kalshi_prior(
    client: KalshiRestClient,
    series_ticker: str,
    ticker: str,
    anchor_ts: int,
    yes_is_radiant: bool,
) -> Decimal | None:
    """GET 1-minute market candles over the trailing window and select P(Radiant)."""
```

`fetch_kalshi_prior`:

1. `start_ts = anchor_ts - QUOTE_TRAILING_SECONDS`
2. `candles = await client.get_candlesticks(series_ticker, ticker, start_ts, anchor_ts)`
3. `return select_kalshi_prior(candles, anchor_ts, yes_is_radiant)`

Do not pass `include_latest_before_start`. Do not call any other client method. `KalshiRestError` propagates (observe retries). Empty tuple → `None`.

### 2. Selection and orientation (exactly once)

A candle is usable when **all** of:

- `end_period_ts <= anchor_ts` (strict reject of anything after the anchor, even if the API over-returns)
- `0 <= yes_bid_close < yes_ask_close <= 1` (two-sided quote; equal/crossed/out-of-range is not a book)

Among usable candles, take the one with the greatest `end_period_ts`. One ticker has one bar per minute; no extra tie-break.

```python
yes_mid = (candle.yes_bid_close + candle.yes_ask_close) / Decimal("2")
if yes_is_radiant:
    return yes_mid
return Decimal("1") - yes_mid
```

That return value **is** `market_radiant_prior` for the future Kalshi `predict_fair` call. Do not store YES-mid separately. Do not re-orient in observe or the worker.

Do not read trade OHLC, `price.close`, volume, or open/high/low. `KalshiCandle` does not carry them.

Event-aggregated: never requested. `get_candlesticks` is `GET /series/{series}/markets/{ticker}/candlesticks`. Source of `kalshi_prior.py` and `kalshi_client.py` must not contain `event_candlesticks` or `historical.candlesticks`. No runtime filter for a payload we never fetch.

### 3. Client: skip last-trade / null bid-ask close

In `get_candlesticks` mapping loop, replace unconditional `_map_candle` with `_try_map_candle(row) -> KalshiCandle | None`:

- missing `yes_bid` / `yes_ask` / `close is None` → `None` (last-trade without a two-sided quote)
- `end_period_ts` not a real int → `None` (do not fail the GET)
- otherwise existing `_map_candle` / `_decimal` / `_unix_ts`

Keep `KalshiCandle` fields required. Incomplete rows never enter `select_kalshi_prior`.

Do **not** enable `include_latest_before_start` (synthetic prepend with null OHLC — the thing we are skipping).

### 4. When the fetch starts (`KalshiObserve`)

Add:

- `_matched_binding: KalshiBinding | None`
- `_horn_unix: int | None`
- `_prior: Decimal | None`  # success only; missing stays None
- `_prior_task: asyncio.Task[None] | None`
- `_clock_phase` / `_clock_second` from the latest tick (for the retry window)

Public for US-013 (no extra protocol):

```python
@property
def radiant_prior(self) -> Decimal | None:
    """P(Radiant) from Kalshi candles, or None until a valid bar is selected."""
```

`KalshiOpenMarketCache.client` → the injected `KalshiRestClient` (one `@property`, keep `_client` as the storage name).

`_maybe_start_prior` (name on observe; do not collide with `MatchWorker._maybe_start_prior`):

Start a task iff **all** of:

- `_prior is None` (no success yet)
- `_prior_task is None`
- not `_cutoff`
- `_matched_binding is not None`
- `_horn_unix is not None`
- `_cache is not None`
- latest clock is `PRE_HORN` or `IN_PROGRESS` with `second <= MODEL_SIGNAL_END_SECOND`

Call it from:

1. `_on_clock` after recording a `PRE_HORN` / `IN_PROGRESS` horn Unix (first tick **and** later ticks)
2. `_accept` after a successful `matched` persist (`_matched_binding = result.binding`)

Do **not** start on `PRE_MATCH` (draft clock; horn Unix is not the map-load horn). Do **not** start on `FINISHED`. Do **not** start on `off_grid` even though a binding exists. Do **not** start when `cache is None` (tests that inject a matched Task without a cache stay silent — that is also `KALSHI_TRADING=off` / no client).

`create_task` name: `kalshi-prior:{match_id}`. Never `await` it in `_on_clock` / `_accept` / `on_first_event` / `on_tick`.

Cutoff (`_apply_cutoff`): cancel `_prior_task` if running. Already-matched files are not cutoff (existing rule); a prior already latched stays.

`close()`: cancel `_prior_task` next to the resolve/watch cancel.

### 5. Retry (Kalshi only; does not restart matching)

`_load_prior` is a loop inside the one task (GRID ticks are sparse; waiting for the next `on_tick` can miss the −60 open):

```python
delay = 0.5  # same base as _await_read
while True:
    if self._cutoff or not self._prior_window_open():
        return
    try:
        prior = await fetch_kalshi_prior(
            self._cache.client,
            binding.series_ticker,
            binding.ticker,
            kalshi_prior_anchor(self._horn_unix),
            binding.yes_is_radiant,
        )
    except asyncio.CancelledError:
        raise
    except KalshiRestError:
        prior = None
    except Exception as exc:
        logger.warning("live-paper kalshi prior failed: %s", type(exc).__name__)
        prior = None
    if prior is not None:
        self._prior = prior
        logger.info("live-paper kalshi prior %s", prior)
        return
    logger.warning("live-paper kalshi prior failed: missing_quote")
    await asyncio.sleep(delay)
    delay = min(delay * 2, 8.0)
```

`_prior_window_open`: `PRE_HORN` (spawn **and** model pre-horn) **or** `IN_PROGRESS` with `second <= MODEL_SIGNAL_END_SECOND`. That is “until the model window has closed”, including retries that start at join −84 so the first −60 tick can be ready. Stop once the window cannot use a prior.

`finally: self._prior_task = None` so a later eligible tick can restart if the task died without a latch. Success (`_prior is not None`) blocks a second start.

The loop must **not** call `resolve_kalshi_match` / `_start_retry`. Matcher HTTP count stays at the observe-story value.

Do not write Kalshi prior into `session.jsonl` or `match.json`. Log only.

### 6. Observe vs paper/live

US-007 lists the start condition with no mode gate. Candles are a public GET. `KalshiObserve` already runs in observe/paper/live whenever a cache exists (`KALSHI_TRADING != off`).

Overlay / US-013 say observe creates no prior/execution tasks. Interpretation for **this** story: start the public GET in observe; do **not** feed the number into `predict_fair` / executor (those are US-013). Gating to `paper`/`live` would need a new constructor arg (`KalshiSettings`) and would leave observe maps with no way to see the prior before the 28th. Do not add that arg.

US-013 still owns: second `predict_fair`, Kalshi `missing_prior` as a Kalshi-only buy block, skipping Kalshi quoting in observe.

### 7. What this story does not touch

- `MatchWorker._compute_decision` / `_prior` / `_maybe_start_prior` / `fetch_market_prior`
- `predict_fair` arguments (Resolved Question is a US-013 contract)
- `session.jsonl` schema 6, `venue`, Kalshi-named prior fields
- `kalshi.db`, book WS, executor, fee gate
- `WalletHost` auth factory switch
- Docker / operator how-to
- `poly-maker`
- Re-resolving a ticker because candles are empty

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Client: skip incomplete candles

`_try_map_candle` + skip in `get_candlesticks`. Keep `_map_candle` for the success path. Existing Decimal mapping test still passes. Add one skip test in `tests/test_live_paper_kalshi_client.py`.

### Step 2 — `kalshi_prior.py`

`kalshi_prior_anchor`, `select_kalshi_prior`, `fetch_kalshi_prior`. Docstrings 1–2 lines. `Decimal("2")` / `Decimal("1")`. No float. No `import kalshi`.

### Step 3 — Observe wiring

`KalshiOpenMarketCache.client`. Observe fields + `_maybe_start_prior` + `_load_prior` + cancel on cutoff/close. Record horn Unix only from `PRE_HORN` / `IN_PROGRESS`.

### Step 4 — Observe-test fakes

`tests/test_live_paper_kalshi_observe.py` `_FakeClient` must grow `async def get_candlesticks(...)` returning `()` (otherwise the two retry tests that reach `matched` + a horn-clock tick explode with `AttributeError` and would otherwise retry-loop). Empty candles + cancel on `_stop` → `close()` is enough; do not let the loop block the test.

### Step 5 — Tests

`tests/test_live_paper_kalshi_prior.py` as below. Patch `asyncio.sleep` in retry tests (same trick as client read-retry).

### Step 6 — Quality gate

See Verification. `git add` new files before `make lint-all`.

### Step 7 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: Kalshi fair can later call `predict_fair` with a Kalshi map-load prior that shares the horn−90 anchor and never sees PM tokens.
- Set US-007 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (inclusive `<=` vs PM strictly-before; skip null bid/ask in `get_candlesticks`; start from observe not `_on_event`; retry loop does not latch None; Kalshi `yes_is_radiant`; no `predict_fair` yet).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Resolve still pending, first tick `PRE_HORN` | Record horn Unix; do not GET; start when `_accept` commits `matched` |
| `matched` first, first tick still `PRE_MATCH` | Binding stored; **no** GET until a later `PRE_HORN`/`IN_PROGRESS` |
| First tick already `PRE_HORN` and Task already `matched` | Start on `on_first_event` (not on `handle_event`) |
| `yes_is_radiant=False`, YES mid 0.42 | P(Radiant) `Decimal("0.58")` |
| Candle `end_period_ts == anchor` | **Keep** (inclusive) |
| Candle `end_period_ts == anchor + 1` | Skip; if it was the only bar → `None` |
| Null `yes_bid.close`, trade OHLC present | Skip that row; earlier valid bar still wins |
| `bid == ask` or `bid > ask` | Skip (not two-sided) |
| Empty tuple / all skipped | `None`; retry; PM `_prior` unchanged |
| `KalshiRestError` | Retry; do not raise into the feed loop |
| `off_grid` with a ticker | No GET |
| `none` / `ambiguous` / `error` / `off` | No GET |
| `IN_PROGRESS` `second >= 540` before bind | Cutoff; no GET; in-flight resolve cancelled (existing) |
| Already `matched`, then later cutoff would not apply | Prior stays (existing “matched stays”) |
| `KALSHI_TRADING=off` / `cache is None` | No GET |
| Worker first-event `continue` | Prior still starts (observe path) |
| Feed tick while GET in flight | Tick handler returns; archive/PM attach unaffected |
| Retry while resolve Task exists | Zero extra `list_open_markets` calls |
| Window closed (`FINISHED` / `second > MODEL_SIGNAL_END_SECOND`) | Leave the loop; no latch |
| `close()` / worker `finally` | Cancel prior task |
| Two `_maybe_start_prior` calls | Second is a no-op while `_prior_task` is set |
| PM missing prior on the same map | PM path unchanged; Kalshi path independent |
| `float` on the dataclass | Forbidden |

---

## Test plan

### New: `tests/test_live_paper_kalshi_prior.py`

No live Kalshi HTTP. No `import kalshi`. Fake client: `async def get_candlesticks` recording `(series, ticker, start_ts, end_ts)` and returning a scripted tuple. `asyncio.run`.

Helper: `_candle(end_period_ts, bid, ask) -> KalshiCandle` with `Decimal` strings.

| Test | Setup | Expect |
|---|---|---|
| **Orientation `yes_is_radiant=False`** | One valid candle, bid `0.40` ask `0.44` (YES mid `0.42`) | `select_kalshi_prior(...) == Decimal("0.58")`. Same via `fetch_kalshi_prior`. Type is `Decimal`. |
| Orientation true | Same candle, `yes_is_radiant=True` | `Decimal("0.42")` |
| **No history** | `get_candlesticks` → `()` | `fetch_kalshi_prior` is `None` |
| **Candle after anchor rejected** | Bars at `anchor-60` (valid) and `anchor+60` (valid quotes) | Selected bar is the pre-anchor one; after-anchor unused |
| Only after-anchor | Single bar `anchor+1` | `None` |
| Inclusive at-anchor | Bar `end_period_ts == anchor` | Selected |
| Last-trade skipped then earlier bar used | Client returns a null-bid SDK row **and** an older two-sided row (drive through `get_candlesticks` or through `select` with only the mapped survivor) | Older two-sided wins; trade OHLC never becomes the prior |
| Crossed / locked bar skipped | `bid >= ask` at a later ts plus an earlier valid bar | Earlier valid bar |
| Window args | `fetch_kalshi_prior(..., anchor_ts=150)` | `start_ts == 150 - QUOTE_TRAILING_SECONDS`, `end_ts == 150`, `series_ticker`/`ticker` forwarded |
| **PM tokens nowhere** | Read `kalshi_prior.py` (and the new observe prior methods) | No `yes_token_id`, `no_token_id`, `fetch_market_prior`, `prices-history`, `TOKEN0`/`TOKEN1`. `import kalshi` / `from kalshi` absent. No `event_candlesticks` / `historical.candlesticks`. |
| No float | Walk returned `Decimal` | same `_assert_no_float` idea |
| Anchor helper | `kalshi_prior_anchor(1000)` | `910` |

Worker / observe integration (same file or a short extra class; reuse `build_attached_worker` / `build_event` / `HORN_UNIX_SECONDS`):

| Test | Setup | Expect |
|---|---|---|
| Starts after bind **and** horn-clock, not on `PRE_MATCH` | Matched Task + `PRE_MATCH` tick, then `PRE_HORN` | `get_candlesticks` call count 0 then 1; anchor is `horn_unix - 90` |
| Bind-later still starts | Gated resolve + first `PRE_HORN` tick, then release `matched` | GET starts after bind without a third tick (mirror observe hook B) |
| First tick does not await a slow GET | `get_candlesticks` waits on an Event | attach / `match.json` pending→matched happen while the Event is still unset (feed not blocked) |
| Cutoff starts no GET | `IN_PROGRESS` at 540 + gated matched | `get_candlesticks` never called |
| Empty history retries, matcher does not | Matched + `PRE_HORN` + empty candles; patch sleep | ≥2 `get_candlesticks` calls; `list_open_markets` still 0 if resolve was injected |
| **PM untouched** | Attached worker, PM `_prior=0.5`, Kalshi fetch returns `None` | After a model-window tick: `worker._prior == 0.5`, journal `market_radiant_prior` is the PM value, `SignalReason.MISSING_PRIOR` is **not** forced by Kalshi; `radiant_prior` on observe is `None` |
| `off_grid` | Persist/accept off_grid with ticker | no GET |

Patch `asyncio.sleep` in retry tests so they do not wait 0.5s.

### Extend: `tests/test_live_paper_kalshi_client.py`

| Test | Setup | Expect |
|---|---|---|
| Null YES bid/ask close is skipped | Mix: one row `yes_bid=None`, one valid | Only the valid `KalshiCandle`; GET does not raise |
| Call shape unchanged | existing `test_candle_call_shape` | still `period_interval==1`, no `include_latest_before_start` |

### Extend: `tests/test_live_paper_kalshi_observe.py`

Add `get_candlesticks` on `_FakeClient` (return `()`, record calls). Existing retry tests must still pass with the prior task cancelled in `close()`.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest \
  tests/test_live_paper_kalshi_prior.py \
  tests/test_live_paper_kalshi_observe.py \
  tests/test_live_paper_kalshi_client.py \
  tests/test_live_paper_match_lifecycle.py \
  tests/test_market_prior.py

make test

git add src/live_paper/kalshi_prior.py \
        src/live_paper/kalshi_observe.py \
        src/live_paper/kalshi_match.py \
        src/live_paper/kalshi_client.py \
        tests/test_live_paper_kalshi_prior.py \
        tests/test_live_paper_kalshi_observe.py \
        tests/test_live_paper_kalshi_client.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. Ориентация при `yes_is_radiant=false` — YES mid 0.42 → P(Radiant) 0.58, `Decimal`.
2. Отсутствие истории — empty candles → `None` (Kalshi missing only).
3. Свеча после якоря отвергнута — later `end_period_ts` unused; earlier valid bar kept.
4. PM-токены нигде не участвуют — no CLOB token ids / `fetch_market_prior` in the Kalshi prior path; PM `_prior` unchanged when Kalshi history is empty.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **PM first-tick skip.** If the implementer hooks Kalshi prior into `MatchWorker._on_event` / `_maybe_start_prior`, a map whose first yielded tick is already −15 may never start the GET until tick 2 — and overlay wants it in flight for the first model-eligible tick. Observe is the right seam.
2. **Inclusive `<=` vs PM strictly-before.** Different on purpose. Do not “align” them.
3. **Null bid/ask close on the live wire is assumed real.** If every production bar always has both closes, the skip is a no-op. If `_map_candle` stays strict, one hole loses the whole window — that fails the last-trade reject rule.
4. **Observe GET vs overlay “observe creates no prior tasks”.** This plan starts the public GET in observe and does not call the model. US-013 must not treat observe `radiant_prior` as permission to quote. If product later wants observe silent, add a settings flag then — not a second prior module.
5. **Retry loop + observe tests.** Without `get_candlesticks` on `_FakeClient` and cancel in `close()`, matched+horn-clock tests hang or traceback. Do Step 4 in the same PR as Step 3.
6. **`KalshiBinding.yes_is_radiant` can differ from PM.** Using `discovered.market.yes_is_radiant` silently mis-orients. Tests must pass Kalshi’s flag.
7. **Do not latch `None` like PM.** Overlay explicitly retries while the window is open. Copying `_prior = prior` after a `None` return would freeze Kalshi missing for the map.
8. **`MODEL_SIGNAL_END_SECOND` is 899, `BUY_CUTOFF_SECOND` is 540.** Cutoff still blocks *starting* prior (no bind). After a bind, keep retrying until the model window cannot use the number, not only until 540.
9. **US-013 will `float(radiant_prior)` at the model boundary.** Keeping `Decimal` here is the contract. Do not pre-convert “to be helpful”.
10. **Assumption:** one GET over 6h of 1-minute bars is small enough; no extra pagination API (SDK returns the list).
11. **Assumption:** `end_period_ts` is Unix seconds of the **close** of the minute. We do not convert from ms. If a dry-run shows ms, that is a client mapping bug (US-002 `_unix_ts` already requires `int`).
12. **PEM / auth.** Unused. A prior test that constructs `open_kalshi_auth_client` is out of scope.

No Figma. No poly-maker patch. No new dependency.
