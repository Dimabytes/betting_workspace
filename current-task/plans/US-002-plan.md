# US-002 Implementation Plan

Story: **US-002** — «kalshi_client: публичный REST»

Repo: `/root/work/dota_2_model` on `main`. Public REST wrapper only. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** wire WalletHost / matcher / prior (US-003 / US-005 / US-007). Do **not** add auth, V2 orders, or WebSocket (US-006).

---

## Binding decisions (feature.json / overlay / US-001 learnings)

Cross-ref `current-task/feature.json` FR-4, Resolved Questions, Non-Goals, and `docs/plans/kalshi-overlay.md` (Library, Kalshi prior, Fees, Config).

1. **`src/live_paper/kalshi_client.py` is the only production module that `import kalshi`.** `kalshi_config.py` stays SDK-free. Tests may import `kalshi` to build fakes / assert host factories. A source scan in the new test file must fail if any other `src/live_paper/*.py` grows a `kalshi` import.
2. **Do not call `AsyncKalshiClient.from_env()` or `KalshiClient.from_env()`.** US-001: the SDK also reads inline `KALSHI_PRIVATE_KEY` PEM. Construct `AsyncKalshiClient(config=...)` with no `key_id` / `private_key` / `private_key_path` / `auth`.
3. **Do not use sync `KalshiClient`.** HTTP must not block the event loop. Verified: `kalshi.async_client.AsyncKalshiClient` exists in 12.0.0 and `AsyncTransport.request` retries with `await asyncio.sleep`. No `asyncio.to_thread` workaround.
4. **Hosts come from SDK factories.** `KalshiConfig.production()` / `KalshiConfig.demo()`. Never concatenate `api.elections…`, `demo-api…`, or `external-api…` in our source. Overlay mentions `external-api.kalshi.com` as the “recommended” Trade API host; **SDK 12.0.0 does not use it** (see Verified SDK surface). We follow the SDK factories so a later pin picks up a host change.
5. **Public GET works without keys.** Observe-mode matcher (US-005) will use this client as-is. This story does not read `KALSHI_TRADING` or `load_kalshi_settings()`.
6. **`price_level_structure` + `price_ranges` only.** Do not read scalar `tick_size` even if a payload still carries it (`Market.model_config` is `extra="allow"`).
7. **Fees from series, not market.** `GET /series/{ticker}` + `GET /series/fee_changes`. Do not copy fee fields off a market object.
8. **Candles are live market candles, 1-minute.** `period_interval=1`. Do not call `series.event_candlesticks` (event-aggregated; overlay forbids it) and do not call `historical.candlesticks` (archived markets). US-007 chooses the horn−90 candle; this story only fetches and maps.
9. **Decimal in, Decimal out.** No `float()` on wrapper values. JSON `price_ranges` values are spec strings; coerce at the boundary with `Decimal` / `Decimal(str(x))` the same way `match_worker` / `paper_gateway` already do. Dataclass fields are `Decimal`, never `float`.
10. **Retries only for idempotent reads.** US-006 will add writes on this same class; they must not share the read-retry helper. SDK transport already refuses POST/DELETE retries except pre-wire `ConnectError` / `PoolTimeout`. We still own an explicit read-retry so mocked-SDK tests can see it, and we pin SDK `max_retries=0` so we do not double-retry.
11. **poly-maker is frozen.** Zero file changes there. No new dependency (`kalshi-sdk==12.0.0` already pinned).

---

## Verified SDK surface (kalshi-sdk==12.0.0, 2026-08-26)

Inspected `/root/work/dota_2_model/.venv/lib/python3.13/site-packages/kalshi/`. Import name is `kalshi`.

| Need | SDK symbol | Notes |
|---|---|---|
| Async client | `kalshi.AsyncKalshiClient` | `__init__(*, key_id=None, private_key_path=None, private_key=None, auth=None, config=None, demo=False, base_url=None, timeout=None, max_retries=None, transport=None)`. Omit every credential kwarg → `_auth is None`, `is_authenticated is False`. |
| Sync client | `kalshi.KalshiClient` | **Do not use.** |
| Env constructor | `AsyncKalshiClient.from_env` | **Do not use.** Reads `KALSHI_PRIVATE_KEY` / `KALSHI_PRIVATE_KEY_PATH` / `KALSHI_API_BASE_URL` / `KALSHI_DEMO`. |
| Config | `kalshi.KalshiConfig` | `KalshiConfig.production(**kwargs)`, `KalshiConfig.demo(**kwargs)`. |
| Production REST | `kalshi.config.PRODUCTION_BASE_URL` | `"https://api.elections.kalshi.com/trade-api/v2"` |
| Demo REST | `kalshi.config.DEMO_BASE_URL` | `"https://demo-api.kalshi.co/trade-api/v2"` |
| Known hosts | `KalshiConfig._KNOWN_HOSTS` | `api.elections.kalshi.com`, `demo-api.kalshi.co` only. Hand-building `external-api.kalshi.com` would fail host validation. |
| Open markets | `AsyncKalshiClient.markets.list_all` | `AsyncMarketsResource.list_all(*, status, series_ticker, limit, max_pages, cursor handled internally) -> AsyncIterator[Market]`. HTTP `GET /markets`. Cursor query param is `cursor`; empty/missing cursor ends the loop. Cursor replay raises `KalshiError`. **Not** `async def` — call it, then `async for`. |
| One page (do not use for the public method) | `markets.list` | Returns `Page[Market]` with `.items`, `.cursor`, `.has_next`. Wrapper should use `list_all`, not hand-paginate. |
| Market fields we copy | `kalshi.models.markets.Market` | `event_ticker: str`, `ticker: str`, `status: str`, `yes_sub_title: str`, `no_sub_title: str`, `price_level_structure: str`, `price_ranges: NullableList[dict[str, Any]]`. **No `series_ticker` field** — stamp it from the method argument. **No `tick_size` field** in the model; extra keys may still appear. |
| Price bands (wire) | OpenAPI `PriceRange` | Required string fields `start`, `end`, `step` (fixed-point dollars). SDK has not modelled this nested class yet (`list[dict]`). |
| Candles | `AsyncMarketsResource.candlesticks(series_ticker, ticker, *, start_ts, end_ts, period_interval, include_latest_before_start=None)` | HTTP `GET /series/{series_ticker}/markets/{ticker}/candlesticks`. `period_interval` enum: **1 / 60 / 1440** (minutes). Use **`1`**. Leave `include_latest_before_start` unset (synthetic prepend nulls OHLC; US-007 needs real yes bid/ask close). |
| Candle model | `kalshi.models.markets.Candlestick` | `end_period_ts: int`, `yes_bid: BidAskDistribution`, `yes_ask: BidAskDistribution`. Bid/ask OHLC `close` is `DollarDecimal` (`close_dollars` alias). |
| Series | `AsyncSeriesResource.get(series_ticker)` | HTTP `GET /series/{series_ticker}` → `Series`. `fee_type: str`, `fee_multiplier: MultiplierDecimal`. |
| Scheduled fees | `AsyncSeriesResource.fee_changes(*, series_ticker, show_historical)` | HTTP `GET /series/fee_changes` → `list[SeriesFeeChange]`. Fields: `id`, `series_ticker`, `fee_type`, `fee_multiplier`, `scheduled_ts: AwareDatetime`. Pass `series_ticker=`; omit `show_historical` (current + scheduled, not the archive). |
| Fee type labels (do not import) | `kalshi.models.series.FEE_TYPE_*` | `"quadratic"`, `"quadratic_with_maker_fees"`, `"flat"`. Keep `fee_type` as `str` for forward-compat. US-012 interprets. |
| Decimal aliases | `kalshi.types.DollarDecimal`, `MultiplierDecimal` | Runtime `Decimal` after pydantic coerce. Do not re-export these aliases. Convert with stdlib `Decimal`. |
| Errors | `kalshi.errors.KalshiError` and subclasses | Retry reads on `KalshiTimeoutError`, `KalshiServerError`, `KalshiRateLimitError`, `KalshiNetworkError`, `KalshiPoolExhaustedError`. Do not retry `KalshiNotFoundError`, `KalshiValidationError`, `KalshiAuthError`, `KalshiConflictError`. |
| Transport retry | `kalshi._base_client.RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}` | POST/DELETE are not retried on 5xx/timeout. Pre-wire `ConnectError`/`PoolTimeout` may retry any method (request never left). Pin **`max_retries=0`** on our `KalshiConfig` so US-006 writes are not transport-retried and so our helper is the only read retry. |
| Default retry knobs | `DEFAULT_MAX_RETRIES=3`, `retry_base_delay=0.5`, `retry_max_delay=30.0` | We replace with the bounded policy below, implemented in our helper (SDK retries off). |
| Auth-required (not this story) | `markets.orderbook`, `markets.bulk_orderbooks`, WS `.ws` | `_require_auth()` / `AuthRequiredError`. Ignore. |

**Capability gaps (none blocking US-002):**

- Async REST exists. No `to_thread`.
- Nested `PriceRange` is still `dict` in the SDK — we parse `start`/`end`/`step` ourselves.
- `get_candlesticks(ticker, range, 1-minute)` in the story omits `series_ticker`. The live SDK path **requires** it. Add `series_ticker` as a required argument (US-007 already has it from the bind). Do not guess the series from the ticker prefix.
- Overlay’s `external-api.kalshi.com` is not in SDK 12.0.0 known hosts. Not a gap if we use `KalshiConfig.production()` / `.demo()`.

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `shared.constants.api.HTTP_TIMEOUT_SECONDS` | `KalshiConfig.timeout`. Same 30s collect/live HTTP knob. |
| `Decimal(str(value))` in `match_worker.py` / `paper_gateway.py` | Boundary coerce for `price_ranges` dict values. |
| `tests/test_market_prior.py` fake-client pattern | Scripted SDK resource, no network. |
| `asyncio.run(...)` in live-paper tests | No pytest-asyncio plugin in this repo. |
| `live_paper.kalshi_config.KalshiSettings` | Do **not** load it here. Host is `demo: bool`, not trading mode. Observe/paper/live all hit **production** public REST; demo is US-014 smoke. |

`market_prior.py` is the prior-fetch *pattern* (thin HTTP wrapper, timeout constant, worker owns retry-on-None). Kalshi prior timing stays in US-007. This story is the SDK boundary that US-007 will call.

Do not use `tenacity` (`shared.utils.common` is sync collect-style). A 15-line async loop is smaller and testable.

---

## Design

### Outward types (frozen dataclasses only; no SDK models)

```python
@dataclass(frozen=True)
class KalshiPriceRange:
    """One valid-price band from market.price_ranges (start/end/step dollars)."""
    start: Decimal
    end: Decimal
    step: Decimal

@dataclass(frozen=True)
class KalshiOpenMarket:
    """One open market row after dropping SDK models."""
    event_ticker: str
    ticker: str
    status: str
    yes_sub_title: str
    no_sub_title: str
    price_level_structure: str
    price_ranges: tuple[KalshiPriceRange, ...]
    series_ticker: str

@dataclass(frozen=True)
class KalshiCandle:
    """One 1-minute market candle. Bid/ask close are YES prices."""
    end_period_ts: int
    yes_bid_close: Decimal
    yes_ask_close: Decimal

@dataclass(frozen=True)
class KalshiScheduledFeeChange:
    """One row from GET /series/fee_changes."""
    fee_type: str
    fee_multiplier: Decimal
    scheduled_ts: datetime  # timezone-aware, from SeriesFeeChange.scheduled_ts

@dataclass(frozen=True)
class KalshiSeriesFeeParams:
    """Current series fees plus scheduled changes. Not inferred from a market."""
    series_ticker: str
    fee_type: str
    fee_multiplier: Decimal
    scheduled_changes: tuple[KalshiScheduledFeeChange, ...]
```

`KalshiOpenMarket.series_ticker` is the query argument, not an SDK Market field. US-003 needs it on the bind result; stamping it here avoids a second lookup.

Do **not** merge scheduled fee changes into “effective now”. Series `fee_type` / `fee_multiplier` are current; the tuple is the schedule. US-012 picks the row whose `scheduled_ts` has fired.

`KalshiRestError(Exception)` wraps every SDK failure that escapes the helper (including exhausted retries). Downstream stories import this, not `kalshi.errors`.

### `src/live_paper/kalshi_client.py`

```python
class KalshiRestClient:
    """Public Kalshi REST. The only live-paper module that imports kalshi-sdk."""

    def __init__(self, sdk: AsyncKalshiClient) -> None: ...
    async def aclose(self) -> None: ...
    async def list_open_markets(self, series_ticker: str) -> tuple[KalshiOpenMarket, ...]: ...
    async def get_candlesticks(
        self, series_ticker: str, ticker: str, start_ts: int, end_ts: int
    ) -> tuple[KalshiCandle, ...]: ...
    async def get_series_fee_params(self, series_ticker: str) -> KalshiSeriesFeeParams: ...
```

Factory (required `demo`, no optionals):

```python
def open_kalshi_rest_client(demo: bool) -> KalshiRestClient:
    """Unauthenticated async client on SDK demo or production hosts."""
```

- `demo is True` → `KalshiConfig.demo(max_retries=0, timeout=HTTP_TIMEOUT_SECONDS)`
- `demo is False` → `KalshiConfig.production(max_retries=0, timeout=HTTP_TIMEOUT_SECONDS)`
- `AsyncKalshiClient(config=config)` only. No `demo=` **and** `config=` together (SDK raises on a split env).
- `__aenter__` / `__aexit__` call `aclose` → `await sdk.close()`.

**`list_open_markets`:** `await _await_read(fetch)` where `fetch` does `async for market in sdk.markets.list_all(status="open", series_ticker=series_ticker)` and maps each row. Do **not** pass `max_pages` (a cap that stops while `cursor` remains would hide a candidate). SDK cursor-loop detection stays as the runaway guard.

**`get_candlesticks`:** `_await_read` around `sdk.markets.candlesticks(series_ticker, ticker, start_ts=start_ts, end_ts=end_ts, period_interval=_MINUTE_PERIOD)`. `_MINUTE_PERIOD = 1`. Map `end_period_ts`, `yes_bid.close`, `yes_ask.close`. Do not map trade `price` OHLC (US-007 rejects last-trade-only).

**`get_series_fee_params`:** two reads, each through `_await_read`: `sdk.series.get(series_ticker)` then `sdk.series.fee_changes(series_ticker=series_ticker)`. Map `fee_type` / `fee_multiplier` / `scheduled_ts`. Do not read anything off a `Market`.

Mapper rules:

- Duck-type SDK objects (`getattr`); do not import `Market` / `Candlestick` / `Series`.
- `_decimal(value) -> Decimal`: `Decimal` as-is if finite; `str`/`int` via `Decimal(...)`; `float` only via `Decimal(str(value))` at this boundary; reject `bool` / `None`. Raise `KalshiRestError` on `InvalidOperation` or missing `start`/`end`/`step`.
- Never assign `tick_size`. Never `float(decimal)`.
- Empty `price_ranges` is a valid tuple `()` (US-003 will `off_grid`).

### Retry / backoff (reads only)

| Knob | Value |
|---|---|
| SDK `KalshiConfig.max_retries` | `0` (we own retries) |
| Attempts | original + 3 retries = 4 calls (`_READ_RETRIES = 3`) |
| Delay | `min(0.5 * 2 ** attempt, 8.0)` seconds, `await asyncio.sleep` |
| Retryable | `KalshiTimeoutError`, `KalshiServerError`, `KalshiRateLimitError`, `KalshiNetworkError`, `KalshiPoolExhaustedError` |
| Not retryable | `KalshiNotFoundError`, `KalshiValidationError`, `KalshiAuthError`, `KalshiConflictError`, and any other `KalshiError` |
| Writes | `_await_once` — one call, wrap errors, **no sleep**. US-006 must use this (or an equivalent) for place/cancel. |

```python
async def _await_read(operation: Callable[[], Awaitable[T]]) -> T: ...
async def _await_once(operation: Callable[[], Awaitable[T]]) -> T: ...
```

Both convert `KalshiError` → `KalshiRestError` (`from exc`). Tests monkeypatch `live_paper.kalshi_client.asyncio.sleep` to a no-op so retry tests do not wait.

`_await_read` re-invokes `operation()` each attempt so `list_all()` starts a new iterator (full list is idempotent GET).

ponytail: whole-list retry if page 2 fails, not per-page. Upgrade: retry the failed page only (SDK transport would do that if we re-enabled `max_retries`).

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Wrapper module

1. Create `src/live_paper/kalshi_client.py` as designed. All imports at module top, including `from kalshi import AsyncKalshiClient, KalshiConfig` and the `kalshi.errors` names listed above. One-/two-line docstrings on the public types and functions.
2. No URL strings. No `from_env`. No sync client. No `load_kalshi_settings`. No WalletHost import.

### Step 2 — Tests

1. Create `tests/test_live_paper_kalshi_client.py` (cases in Test plan).
2. Fakes are duck-typed objects (SimpleNamespace / tiny classes) with only the attributes the mapper reads. Do **not** construct full pydantic `Market` (dozens of required fields). `list_all` is a **plain** method returning an async iterator; `candlesticks` / `series.get` / `series.fee_changes` are `async def`.
3. Drive async with `asyncio.run`, matching `test_live_paper_wallet_host.py`.

### Step 3 — Quality gate

See Verification. `make lint-all` skips untracked files: `git add` the new Python files in `dota_2_model` before lint, then re-run.

### Step 4 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Message focuses on why: one SDK boundary so matcher/prior never see kalshi-sdk models.
- Set `userStories` US-002 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `current-task/progress.txt` and `learnings.txt` (SDK symbols, `list_all` not async def, `series_ticker` required for candles, `max_retries=0`, do not `from_env`).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Test plan

### New: `tests/test_live_paper_kalshi_client.py`

Patch `asyncio.sleep` on the wrapper module for retry cases. Inject a fake SDK into `KalshiRestClient(sdk)`.

| Test | Setup | Expect |
|---|---|---|
| pagination keeps the last-page ticker | `list_all` yields page1 `[A]` then page2 `[B]` as one async iterator (two logical pages, candidate B last) | both `A` and `B` in the tuple, order preserved, `series_ticker` stamped |
| `status="open"` and `series_ticker` forwarded | record `list_all` kwargs | `status=="open"`, `series_ticker` matches, `max_pages` not passed |
| candle Decimal mapping | SDK-like object with `end_period_ts=100`, `yes_bid.close=Decimal("0.52")`, `yes_ask.close=Decimal("0.54")` (stringy closes also fine) | `KalshiCandle` those Decimals, types are `Decimal`, `0.52` exact |
| candle call shape | record `candlesticks` kwargs | `period_interval==1`, `start_ts`/`end_ts` forwarded, `include_latest_before_start` not passed |
| fee Decimal mapping | `series.get` → `fee_type="quadratic"`, `fee_multiplier=Decimal("1")`; `fee_changes` → one row `quadratic_with_maker_fees`, `Decimal("0.25")`, aware `datetime` | `KalshiSeriesFeeParams` current + one scheduled change; multipliers are `Decimal` |
| fees do not read a market | fake `series` only; markets object would raise if touched | succeeds |
| `price_ranges` strings → Decimal | `[{"start": "0.00", "end": "1.00", "step": "0.01"}]` | one `KalshiPriceRange` with `Decimal("0.01")` step |
| no `tick_size` leak | fake market also has `tick_size="0.05"` | dataclass has no such field (`hasattr` false / not in `asdict`) |
| no float stored | walk dataclass values | no `float` instances |
| read retry then success | `list_all` raises `KalshiTimeoutError` twice, then yields one market; sleep patched | 3 calls, one market, sleep called twice |
| exhausted read retry | always `KalshiServerError` | `KalshiRestError`, 4 calls |
| 404 not retried | `KalshiNotFoundError` once | `KalshiRestError`, 1 call |
| non-idempotent not retried | `_await_once` wrapping a counter that raises `KalshiTimeoutError` | 1 call, `KalshiRestError` |
| factory is unauthenticated production/demo | `open_kalshi_rest_client(False)` / `(True)` then `aclose` | `sdk.is_authenticated is False`; `sdk._config.base_url == KalshiConfig.production().base_url` (resp. `.demo()`); our `.py` source contains neither `api.elections` nor `external-api.kalshi` |
| import fence | read `src/live_paper/*.py` | only `kalshi_client.py` contains `import kalshi` or `from kalshi` |

No browser. No live Kalshi HTTP. Do not add `kalshi` imports to other production files “to make typing easier”.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
make test
git add src/live_paper/kalshi_client.py tests/test_live_paper_kalshi_client.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. A local `pytest tests/test_live_paper_kalshi_client.py` is not sufficient as the only gate, but it is the right first check.

Do **not** run `make install` as a bare `uv sync` (US-001 learning: drops nautilus). The SDK pin is already in `[project]`.

---

## Risks / open points

1. **`get_candlesticks` needs `series_ticker`.** Story text omitted it; SDK 12.0.0 live path requires it. Plan treats it as required. Not a blocker.
2. **SDK production host is still `api.elections.kalshi.com`.** Overlay asked us not to hand-assemble that URL and named `external-api.kalshi.com` as recommended. Following `KalshiConfig.production()` satisfies both “don’t assemble” and “hosts from SDK config”. If Kalshi later ships a pin that switches hosts, we inherit it. Do not `allow_unknown_host=True`.
3. **`list_all` is a sync method returning `AsyncIterator`.** A fake that is itself `async def list_all` will break (`async for` on a coroutine). Tests must match the real shape.
4. **`Market.price_ranges` is untyped dict.** Malformed bands fail closed (`KalshiRestError`). Empty bands pass through.
5. **Constructor will change in US-006** when keys appear. Keep US-002 `__init__(sdk)` injection so auth can wrap a different `AsyncKalshiClient` later without rewriting tests. Do not add key args now.
6. **Daemon still does not start this client.** Intentional; US-005 / US-013 own boot. `KALSHI_TRADING=observe` will use production `demo=False`.
7. **Do not enable SDK `max_retries` and `_await_read` together.** Double backoff on a 500 would blow the matcher’s 60s cache budget.

No Figma. No poly-maker patch. No new dependency.
