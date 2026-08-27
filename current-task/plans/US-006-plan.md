# US-006 Implementation Plan

Story: **US-006** — «kalshi_client: auth REST и WebSocket»

Repo: `/root/work/dota_2_model` on `main`. Extend `src/live_paper/kalshi_client.py` (still the only production module that `import kalshi`). Auth REST (V2 event-orders + portfolio) and a thin WS wrapper that emits frozen events. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** add `kalshi_prior` / `kalshi_store` / `kalshi_session` / `kalshi_executor` (US-007+). Do **not** start auth WS from `WalletHost` (US-013). Do **not** import `kalshi` outside `kalshi_client.py`.

---

## Binding decisions (feature.json / overlay / US-001..005 learnings)

Cross-ref `current-task/feature.json` FR-4, FR-9, US-006 `changes`, Resolved Questions (WS handshake is auth-required; observe is public REST only; paper needs a read-scoped key; live needs read+write; `kalshi-sdk==12.0.0`), Non-Goals, and `docs/plans/kalshi-overlay.md` §§ Library, Live ownership, Kalshi book and mid.

1. **Same module, same `KalshiRestClient(sdk)` injection.** Do not rename the class. Do not add a second production file. Public REST methods stay. Writes and portfolio land on this class. WS is a small `KalshiWsClient` constructed from one captured `sdk.ws`.
2. **Do not `from_env()`. Do not pass PEM bytes.** Auth factory: `AsyncKalshiClient(key_id=..., private_key_path=..., config=...)`. SDK `KalshiAuth.from_key_path` reads the file once. Observe keeps `open_kalshi_rest_client` (no credentials). `KALSHI_PRIVATE_KEY` must never be consulted.
3. **V2 only for writes.** `orders.create_v2` / `orders.cancel_v2` → `POST|DELETE /portfolio/events/orders`. Do not call a non-v2 create. The four-leg YES/NO mapping (buy YES / buy NO / sell held YES / sell held NO) is US-012; this story is one V2 place with `side="bid"|"ask"`.
4. **`client_order_id` is caller-supplied.** Required on `CreateOrderV2Request`. Do not generate ids here (US-010 owns the namespace).
5. **Writes never retry.** `_await_write` (one call, no sleep). Timeout / network after submit → `KalshiUnknownWrite`, not `KalshiRestError`. Downstream (US-010) reconciles by REST. Blind resend is forbidden even inside this wrapper.
6. **Portfolio reads retry like public GET.** `orders.list_all` / `portfolio.fills_all` / `portfolio.positions_all` / `portfolio.balance` go through `_await_read`. `list_all` is a plain method returning `AsyncIterator` — same fake rule as `markets.list_all`.
7. **WS handshake is RSA-PSS on the HTTP upgrade.** SDK `ConnectionManager._build_auth_headers` signs `GET` + `urlparse(ws_base_url).path`. There is no keyless orderbook WS. Observe does not call `open_ws`.
8. **Pin `ws_max_retries=0`.** SDK otherwise reconnects 10 times and silently resubscribes. US-009 owns reconnect + a new book generation. REST `max_retries=0` stays.
9. **Do not use `KalshiWebSocket.orderbook()`.** That yields a mutating SDK `Orderbook`. Subscribe `orderbook_delta` with `send_initial_snapshot=True` (SDK default), copy snapshot/delta into frozen dataclasses immediately, surface seq.
10. **poly-maker is frozen.** Zero file changes there. No new dependency.

---

## Verified SDK surface (kalshi-sdk==12.0.0, 2026-08-26)

Inspected `/root/work/dota_2_model/.venv/lib/python3.13/site-packages/kalshi/`. Import name is `kalshi`.

### Auth / client

| Need | SDK symbol | Notes |
|---|---|---|
| Signer | `kalshi.auth.KalshiAuth.from_key_path(key_id, path)` | Reads PEM once, RSA-PSS SHA256. OpenSSH PEM is rejected. Do not call `from_pem` / `from_env`. |
| Async client | `AsyncKalshiClient(key_id=, private_key_path=, config=)` | `_auth_owned=True`; `close()` shuts the sign executor. `private_key=` is the inline-PEM path — **never pass it**. |
| Unauthenticated | existing `open_kalshi_rest_client` | `_auth is None`; `sdk.ws` raises `AuthRequiredError`. |
| Hosts | `KalshiConfig.production()` / `.demo()` | REST `https://api.elections.kalshi.com/trade-api/v2`, WS `wss://api.elections.kalshi.com/trade-api/ws/v2` (demo: `demo-api.kalshi.co`). Overlay’s `external-api.kalshi.com` is **not** a known host. Do not assemble URLs. |
| Auth flag | `sdk.is_authenticated` | Public. |
| WS property | `sdk.ws` | **New `KalshiWebSocket` every access.** Capture once. Requires auth. |

### V2 orders (writes)

| Need | SDK symbol | Notes |
|---|---|---|
| Place | `await sdk.orders.create_v2(request=CreateOrderV2Request(...))` | `POST /portfolio/events/orders`. Model-only: you must construct `CreateOrderV2Request`. |
| Request fields | `kalshi.models.orders.CreateOrderV2Request` | Required: `ticker`, `client_order_id`, `side` (`"bid"`/`"ask"`), `count` (`FixedPointCount` = `Decimal`), `price` (`OrderPrice` = `Decimal`, non-negative, $0.0001 tick), `time_in_force` (`fill_or_kill`/`good_till_canceled`/`immediate_or_cancel`), `self_trade_prevention_type` (`taker_at_cross`/`maker`). Optional we **pass as required wrapper args**: `post_only: bool`, `subaccount: int`, `reduce_only: bool`, `order_group_id: str \| None`. Omit `expiration_time`, `exchange_index`. `extra="forbid"`. |
| Place ack | `CreateOrderV2Response` | `order_id`, `fill_count`, `remaining_count`, `ts_ms`, `client_order_id`, `average_fill_price`, `average_fee_paid`. |
| Cancel | `await sdk.orders.cancel_v2(order_id, subaccount=, market_ticker=)` | `DELETE /portfolio/events/orders/{id}`. `market_ticker` required only when `exchange_index=-1`; we omit `exchange_index` and still pass `market_ticker` + `subaccount` so the cancel is unambiguous. |
| Cancel ack | `CancelOrderV2Response` | `order_id`, `reduced_by`, `ts_ms`, `client_order_id`. |
| Do not call | V1 `POST/DELETE /portfolio/orders` | Removed from the SDK. Source must not grow a non-v2 create. |
| Idempotency | `client_order_id` | SDK keeps it required. `KalshiConflictError` is 409 duplicate. |
| Price wire | `OrderPrice` | `model_dump(..., mode="json")` serializes Decimal as string. Do not `float()`. |
| Amend / decrease / batch | exist | **Out of scope.** s2-join is place + cancel. |

### Portfolio (reads)

| Need | SDK symbol | Notes |
|---|---|---|
| Open orders | `sdk.orders.list_all(status="resting", subaccount=)` | Plain method → `AsyncIterator[Order]`. HTTP `GET /portfolio/orders`. **No `client_order_id` query param.** After an unknown place, US-010 scans this list. Do not pass `max_pages`. |
| One order | `await sdk.orders.get(order_id)` | `GET /portfolio/orders/{id}`. 404 → `KalshiNotFoundError`. |
| Fills | `sdk.portfolio.fills_all(subaccount=, min_ts=)` | **Not** `orders.fills_all` (deprecated). Plain method → `AsyncIterator[Fill]`. |
| Positions | `sdk.portfolio.positions_all(subaccount=)` | Yields `MarketPosition` (`position_fp`). Event aggregates are not concatenated; skip them. |
| Balance | `await sdk.portfolio.balance(subaccount=)` | Use `balance_dollars` (`Decimal`). Integer cents `balance` is legacy — do not expose it. |
| REST `Order` | `client_order_id`, `order_id`, `ticker`, `status`, `outcome_side`, `book_side`, `yes_price`, `remaining_count`, `fill_count`, `initial_count`, `subaccount`, `order_group_id` | Duck-type with `getattr`, same as markets. |
| REST `Fill` | `fill_id`, `trade_id`, `order_id`, `ticker`/`market_ticker`, `count`, `yes_price`, `fee_cost`, `outcome_side`, `book_side` | **No `client_order_id` on REST Fill.** WS fill has it. Durable id for US-008 is REST `fill_id`; WS mapper uses `trade_id` as `fill_id` when `fill_id` is absent. |

### WebSocket

| Need | SDK symbol | Notes |
|---|---|---|
| Client | `kalshi.ws.client.KalshiWebSocket` | `async with ws.connect() as session`. |
| Handshake | `ConnectionManager._open_socket` | RSA-PSS headers on the upgrade. Close code **4001** (and 4xxx) is permanent — no reconnect, `KalshiConnectionError`. |
| Book | `await session.subscribe_orderbook_delta(tickers=[ticker])` | Channel `orderbook_delta`, `send_initial_snapshot=True`, overflow **ERROR**. Yields `OrderbookSnapshotMessage \| OrderbookDeltaMessage`. |
| Snapshot | `type="orderbook_snapshot"`, `sid`, `seq`, `msg.yes` / `msg.no` | `dict[Decimal, Decimal]` **owned by OrderbookManager — mutates on later deltas.** Copy immediately. |
| Delta | `type="orderbook_delta"`, `sid`, `seq`, `msg.side` (`yes`/`no`), `msg.price`, `msg.delta` | |
| User fills | `await session.subscribe_fill()` | Channel `fill`. `FillMessage.msg.trade_id`, `order_id`, `market_ticker`, `yes_price`, `count`, `fee_cost`, `client_order_id`, `outcome_side`, `book_side`. Seq optional. |
| User orders | `await session.subscribe_user_orders()` | Channel `user_orders`. Type on the wire is `"user_order"`. Payload has `order_id`, `ticker`, `status`, `client_order_id`, counts, `yes_price`, `order_group_id`. |
| Seq tracker | `SequenceTracker` | SDK drops gapped frames and resubscribes. `KalshiSequenceGapError` is raised **only if resubscribe fails**. A successful silent resync arrives as a new snapshot (new `sid`, `seq=1`). Our wrapper must still emit a gap event on sid change / seq discontinuity. |
| Do not use | `session.orderbook(ticker)` | Mutating `Orderbook`; `KalshiOrderbookUnavailableError` between teardown and snapshot. US-009 owns the pair. |

### Errors (map these, do not leak `kalshi.errors` downstream)

| SDK | After this story |
|---|---|
| `KalshiTimeoutError` on place/cancel | `KalshiUnknownWrite` (may have committed; SDK docstring says query `client_order_id`) |
| `KalshiNetworkError` on place/cancel | `KalshiUnknownWrite` (conservative: ConnectError vs ReadError both become `KalshiNetworkError` with `max_retries=0`; do not inspect `__cause__` to retry) |
| `KalshiPoolExhaustedError` | `KalshiRestError` — never reached the wire, **not** unknown. Wrapper still does not retry. |
| `KalshiAuthError` / `AuthRequiredError` | `KalshiAuthFault` |
| `KalshiRateLimitError` | `KalshiRateLimitFault` |
| `KalshiConflictError` (409) | `KalshiRestError` — known duplicate `client_order_id`, not unknown, not retried. US-010 lists orders. |
| `KalshiNotFoundError` / `KalshiValidationError` / other `KalshiError` | `KalshiRestError` |
| pydantic `ValidationError` building `CreateOrderV2Request` | `KalshiRestError` (never left the process) |
| `KalshiSequenceGapError` | WS event `KalshiSequenceGap` |
| `KalshiConnectionError` with `4001` / permanent 4xxx | WS event `KalshiWsAuthFault` |
| other `KalshiConnectionError` / `KalshiWebSocketError` | WS event `KalshiWsDisconnect` |

Public GET still wraps everything in `KalshiRestError` via `_await_read`. Do not change that.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `kalshi_client.py` | Public REST only; `_await_once` unused (pyright ignore) | + auth factory, V2 place/cancel, portfolio, `open_ws` |
| `open_kalshi_rest_client` | Unauthenticated, `max_retries=0` | Unchanged contract. Also set `ws_max_retries=0` so configs match. |
| `open_kalshi_auth_client` | missing | `key_id` + `private_key_path`; same hosts; `is_authenticated is True` |
| `WalletHost` | observe/paper/live all use unauthenticated public REST | **Unchanged.** US-013 swaps paper/live to the auth factory and starts WS. |
| `kalshi_match` / observe | Public `list_open_markets` | Unchanged |
| Book / prior / executor / `kalshi.db` | none | still none |

---

## Requirements traceability (US-006 `changes`)

| Change | Plan |
|---|---|
| API key id + RSA-PSS PEM; key read from file once; contents not logged | Auth factory; SDK `from_key_path`; no `private_key=`; PEM-absence test |
| V2 event-order place with `post_only`, `client_order_id`, Decimal price, fixed-point count; cancel; no legacy YES/NO write | Design §§ 3–4; source scan |
| `client_order_id` is idempotency key; unknown write is not retried; typed unknown for reconcilation | `_await_write` → `KalshiUnknownWrite` |
| Portfolio: open orders, fills, positions, balance by subaccount, paginated | Design § 5 |
| Orderbook WS: auth handshake, snapshot + delta with seq, frozen events; user WS: fills + order updates | Design § 6 |
| Seq gap, disconnect, auth error, rate limit, malformed → typed events/errors | Design §§ 2, 6 |
| Autotests on mocked SDK: V2 place/cancel mapping, snapshot/delta+seq, unknown without retry, auth error outward | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `KalshiRestClient.__init__(sdk)` | Keep. Tests keep injecting SimpleNamespace fakes. |
| `_await_read` / `_decimal` / `_text` | Portfolio mapping. Writes use a new `_await_write`, not `_await_read`. |
| `_await_once` | Replace with `_await_write` (needs `client_order_id` / kind). Delete the pyright ignore. Existing test `test_non_idempotent_is_not_retried` targets `_await_once` — retarget it at `place_event_order`. |
| `open_kalshi_rest_client` / `abandon_kalshi_client` | Observe + host cleanup. Auth client uses the same `aclose` → `sdk.close()`. |
| `HTTP_TIMEOUT_SECONDS` | Both factories. |
| `tests/test_live_paper_kalshi_client.py` `_client` / `_ScriptedMarkets` / `_assert_no_float` | Extend `_client` with `orders=` / `portfolio=` / `ws=`. New cases in the **same file** (one module → one test file). |
| `asyncio.run` | No pytest-asyncio. |

Do not import `kalshi` in `kalshi_config.py`, `wallet_host.py`, `kalshi_match.py`, or `kalshi_observe.py`. Do not load PEM in `KalshiSettings`.

---

## Design

### 1. Outward types (frozen dataclasses; no SDK models)

Keep the US-002 dataclasses. Add:

```python
@dataclass(frozen=True)
class KalshiPlaceAck:
    """Clear V2 place response. Not used when the write result is unknown."""
    order_id: str
    client_order_id: str
    fill_count: Decimal
    remaining_count: Decimal
    ts_ms: int
    average_fill_price: Decimal | None
    average_fee_paid: Decimal | None

@dataclass(frozen=True)
class KalshiCancelAck:
    """Clear V2 cancel response."""
    order_id: str
    client_order_id: str | None
    reduced_by: Decimal
    ts_ms: int

@dataclass(frozen=True)
class KalshiRestingOrder:
    """One row from GET /portfolio/orders, SDK Order dropped."""
    order_id: str
    ticker: str
    client_order_id: str
    status: str
    outcome_side: str  # "yes" | "no"
    book_side: str     # "bid" | "ask"
    yes_price: Decimal
    remaining_count: Decimal
    fill_count: Decimal
    initial_count: Decimal
    subaccount: int | None
    order_group_id: str | None

@dataclass(frozen=True)
class KalshiFill:
    """REST or WS fill. fill_id is the exchange identity US-008 will ingest."""
    fill_id: str
    trade_id: str
    order_id: str
    ticker: str
    count: Decimal
    yes_price: Decimal
    fee_cost: Decimal
    outcome_side: str
    book_side: str
    client_order_id: str | None
    is_taker: bool

@dataclass(frozen=True)
class KalshiPosition:
    """One market position. Null wire position becomes Decimal('0')."""
    ticker: str
    position: Decimal
    fees_paid: Decimal | None

@dataclass(frozen=True)
class KalshiBalance:
    """Subaccount cash from balance_dollars, not integer cents."""
    balance: Decimal

@dataclass(frozen=True)
class KalshiBookLevel:
    """One price → size row copied off a snapshot."""
    price: Decimal
    count: Decimal

@dataclass(frozen=True)
class KalshiBookSnapshot:
    ticker: str
    sid: int
    seq: int
    yes_levels: tuple[KalshiBookLevel, ...]
    no_levels: tuple[KalshiBookLevel, ...]

@dataclass(frozen=True)
class KalshiBookDelta:
    ticker: str
    sid: int
    seq: int
    side: str  # "yes" | "no"
    price: Decimal
    delta: Decimal

@dataclass(frozen=True)
class KalshiSequenceGap:
    """sid/seq discontinuity or SDK KalshiSequenceGapError. US-009 enters safe mode."""
    sid: int
    expected: int
    received: int

@dataclass(frozen=True)
class KalshiWsDisconnect:
    reason: str

@dataclass(frozen=True)
class KalshiWsAuthFault:
    reason: str

@dataclass(frozen=True)
class KalshiWsMalformed:
    reason: str
```

Book stream type (module-level alias is fine; not an anonymous tuple):

`KalshiBookEvent = KalshiBookSnapshot | KalshiBookDelta | KalshiSequenceGap | KalshiWsDisconnect | KalshiWsAuthFault | KalshiWsMalformed`

User stream: `KalshiFill | KalshiRestingOrder | KalshiWsDisconnect | KalshiWsAuthFault | KalshiWsMalformed`

Do **not** put `float` on any of these. `KalshiRateLimitError.retry_after` is a float — **omit it** from `KalshiRateLimitFault` (US-009 does not sleep on it; it safe-modes).

### 2. Exceptions (downstream never imports `kalshi.errors`)

```python
class KalshiUnknownWrite(Exception):
    """Place/cancel left the wire with no response. Do not retry. Reconcile via REST."""
    # attributes: kind: Literal["place", "cancel"], client_order_id: str, order_id: str | None

class KalshiAuthFault(Exception):
    """401/403 REST or WS upgrade close 4001. Safe-mode input. No PEM in the message."""

class KalshiRateLimitFault(Exception):
    """429. Writes: not retried. Reads still go through _await_read."""
```

Keep `KalshiRestError` for mapping failures, 404, 400, 409, pool-exhausted, and exhausted read retries.

`KalshiUnknownWrite.__init__` takes `kind`, `client_order_id`, `order_id` (required; `None` on place timeout). `__str__` is a short sentence with kind + client_order_id, never PEM, never key id.

### 3. Auth factory

```python
def open_kalshi_auth_client(demo: bool, key_id: str, private_key_path: str) -> KalshiRestClient:
    """Authenticated async client. PEM is read by the SDK from the path once."""
```

- `KalshiConfig.demo|production(max_retries=0, ws_max_retries=0, timeout=HTTP_TIMEOUT_SECONDS)`
- `AsyncKalshiClient(key_id=key_id, private_key_path=private_key_path, config=config)` only
- Catch `KalshiAuthError` → `KalshiAuthFault` (missing file, bad PEM, OpenSSH format)
- Empty `key_id` is SDK `ValueError` — wrap as `KalshiAuthFault`
- No URL strings, no `from_env`, no `private_key=`

`open_kalshi_rest_client`: add `ws_max_retries=0` next to existing `max_retries=0`. Still unauthenticated.

`open_ws(self) -> KalshiWsClient`:

```python
if not self._sdk.is_authenticated:
    raise KalshiAuthFault("WebSocket requires authentication")
return KalshiWsClient(self._sdk.ws)  # capture once
```

Do not reach into `sdk._auth` to pass `on_error=`. Iterator exceptions cover disconnect / auth / gap. WS `type=error` frames without `on_error` are SDK-logged; REST is the authority (US-010). ponytail: no custom `on_error` until a dry-run shows we miss a halt.

### 4. Place / cancel

All arguments required (no defaults). `order_group_id` is `str | None`.

```python
async def place_event_order(
    self,
    ticker: str,
    client_order_id: str,
    side: Literal["bid", "ask"],
    count: Decimal,
    price: Decimal,
    time_in_force: Literal["fill_or_kill", "good_till_canceled", "immediate_or_cancel"],
    self_trade_prevention_type: Literal["taker_at_cross", "maker"],
    post_only: bool,
    subaccount: int,
    reduce_only: bool,
    order_group_id: str | None,
) -> KalshiPlaceAck: ...

async def cancel_event_order(
    self,
    order_id: str,
    ticker: str,
    subaccount: int,
    client_order_id: str,
) -> KalshiCancelAck: ...
```

`cancel_event_order` takes `client_order_id` so a timeout can fill `KalshiUnknownWrite.client_order_id` without a second lookup. Pass it through to the exception only; the SDK cancel path does not send it.

Place body:

1. Build `CreateOrderV2Request(...)` inside this module (`from kalshi.models.orders import CreateOrderV2Request` at module top). Pydantic `ValidationError` → `KalshiRestError`.
2. `await _await_write(lambda: self._sdk.orders.create_v2(request=request), kind="place", client_order_id=..., order_id=None)`
3. Map the ack with `_decimal` / `_text`. Missing `client_order_id` on the response: use the request’s id.

Cancel: `await self._sdk.orders.cancel_v2(order_id, subaccount=subaccount, market_ticker=ticker)` through `_await_write`.

`_await_write`: **no loop, no sleep.** Branch on SDK error class as in the table above. `KalshiUnknownWrite` is the only timeout/network outcome.

US-012 will pass `post_only=True`, `time_in_force="good_till_canceled"`, `self_trade_prevention_type="maker"`. This story does not hard-code those; tests pass them explicitly. A test that `post_only=False` is forwarded exists so we do not silently force True (smoke US-014 uses IOC/FOK without post-only).

### 5. Portfolio

```python
async def list_open_orders(self, subaccount: int) -> tuple[KalshiRestingOrder, ...]: ...
async def get_order(self, order_id: str) -> KalshiRestingOrder: ...
async def list_fills(self, subaccount: int, min_ts: int) -> tuple[KalshiFill, ...]: ...
async def list_positions(self, subaccount: int) -> tuple[KalshiPosition, ...]: ...
async def get_balance(self, subaccount: int) -> KalshiBalance: ...
```

`min_ts` is required on `list_fills` (AGENTS.md: no optionals). Tests use `0`. US-010 will pass a checkpoint.

Pagination: same ponytail as `list_open_markets` — whole-list retry via `_await_read`, do not pass `max_pages`.

```python
async def fetch() -> tuple[KalshiRestingOrder, ...]:
    rows = []
    async for order in self._sdk.orders.list_all(status="resting", subaccount=subaccount):
        rows.append(_map_resting_order(order))
    return tuple(rows)
return await _await_read(fetch)
```

`get_order` is a single GET through `_await_read`. Needed when WS/cancel already has an `order_id`. Lookup-by-`client_order_id` is **not** in the SDK; do not fake it with a hidden scan. US-010 will `list_open_orders` and match.

Position `position is None` → `Decimal("0")`. Missing `ticker` fails closed (`KalshiRestError`).

### 6. `KalshiWsClient`

Duck-typed `ws` for tests (object with `connect()`, `subscribe_orderbook_delta`, `subscribe_fill`, `subscribe_user_orders`).

```python
class KalshiWsClient:
    """One captured SDK WebSocket. Frozen events only; SDK models stay inside."""

    def __init__(self, ws: object) -> None: ...
    async def __aenter__(self) -> KalshiWsClient: ...  # ws.connect() context
    async def __aexit__(...) -> None: ...
    async def subscribe_orderbook(self, ticker: str) -> AsyncIterator[KalshiBookEvent]: ...
    async def subscribe_fills(self) -> AsyncIterator[KalshiFill | KalshiWsDisconnect | KalshiWsAuthFault | KalshiWsMalformed]: ...
    async def subscribe_user_orders(self) -> AsyncIterator[KalshiRestingOrder | KalshiWsDisconnect | KalshiWsAuthFault | KalshiWsMalformed]: ...
```

`__aenter__` calls `await self._ws.connect().__aenter__()` and stores the context manager. Do not call `_start`. Subscribe methods require an entered session (SDK raises `RuntimeError` otherwise); tests enter.

**Orderbook mapping** (flatten; no nested ladders):

1. `stream = await self._ws.subscribe_orderbook_delta(tickers=[ticker])`
2. `async for raw in stream:`
   - `msg_type = getattr(raw, "type", None)`
   - unknown / missing `sid`/`seq` → yield `KalshiWsMalformed`, continue (do not crash the generator)
   - snapshot: copy `msg.yes` / `msg.no` items into sorted tuples of `KalshiBookLevel` **now**
   - delta: copy `price` / `delta` / `side`
3. Contiguity: remember `last_sid`, `last_seq`.
   - First snapshot/delta: emit it, store sid/seq.
   - Same sid and `seq == last + 1`: emit, advance.
   - Same sid and `seq != last + 1`: yield `KalshiSequenceGap`, **do not emit the gapped delta**.
   - Different sid (silent SDK resync): yield `KalshiSequenceGap`, **then emit the snapshot** so US-009 can start a new generation after safe mode. Do not emit a delta that arrives with a new sid and no snapshot.
4. `except KalshiSequenceGapError`: yield `KalshiSequenceGap` (use `exc.sid` / `last_seq` / `exc.next_seq`; missing attrs → `sid=0`, `expected=0`, `received=0` is worse than getattr with `or 0` — require the SDK fields, fall back to last_sid/last_seq+1).
5. `except KalshiConnectionError`: if `"4001"` in `str(exc)` or `"permanent code 4"` in `str(exc)` → `KalshiWsAuthFault`; else `KalshiWsDisconnect`. Then return (end the stream).
6. `except KalshiAuthError`: `KalshiWsAuthFault`, return.
7. `except KalshiWebSocketError`: `KalshiWsDisconnect`, return.

Do not rebuild a local book. Do not call `OrderbookManager.get`.

**User streams:** map `type=="fill"` / `type=="user_order"`; same disconnect/auth wrapping. No seq contiguity (SDK: seq optional). REST remains the fill authority.

Malformed SDK frames that the recv loop already skips never reach us. We still treat unparseable *delivered* objects as `KalshiWsMalformed`. Seq rollback inside the SDK can produce a later gap event — that is the halt signal.

### 7. Secrets

- Never log / `raise` / `__repr__` PEM bytes or `BEGIN PRIVATE KEY`.
- `KalshiSettings` already has path, not bytes — do not add a pem field.
- Auth exception messages: SDK already interpolates the **path**, not the file body. Do not append `key_id`.
- `KalshiRestClient` default repr is the object id; do not dump `sdk._auth`.
- Source must still contain neither `api.elections` nor `external-api.kalshi` (existing factory test).

### 8. What this story does not touch

- `wallet_host.py` factory switch (US-013: paper/live → `open_kalshi_auth_client`, then `open_ws`)
- `kalshi_prior` / `kalshi_store` / `kalshi_session` / `kalshi_executor`
- Four-leg V2 mapping, fee gate, paper fills
- `session.jsonl` schema 6
- Docker / operator how-to (US-015)
- `poly-maker`
- Generating `client_order_id`
- SDK `max_retries` / `ws_max_retries` other than pinning 0

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Types, `_await_write`, auth factory

1. Add dataclasses + `KalshiUnknownWrite` / `KalshiAuthFault` / `KalshiRateLimitFault`.
2. `_await_write` as designed. Remove `_await_once` (or keep it as a one-line caller of `_await_write` with dummy ids — prefer delete and retarget the test).
3. `open_kalshi_auth_client`. `ws_max_retries=0` on both factories.
4. `KalshiRestClient.open_ws`.

### Step 2 — V2 place/cancel + portfolio

1. `place_event_order` / `cancel_event_order`.
2. `list_open_orders` / `get_order` / `list_fills` / `list_positions` / `get_balance`.
3. Duck-typed mappers. `CreateOrderV2Request` is the one SDK model we construct.

### Step 3 — WS wrapper

1. `KalshiWsClient` context + three subscribe methods.
2. Copy snapshot dicts immediately. Seq / sid rules as Design § 6.

### Step 4 — Tests

Extend `tests/test_live_paper_kalshi_client.py`. Fakes: `list_all` is **not** `async def`; `create_v2` / `cancel_v2` / `balance` / `get` are `async def`; WS `subscribe_*` are `async def` returning an async iterator. `asyncio.run` a small consumer.

### Step 5 — Quality gate

See Verification. `git add` before `make lint-all`.

### Step 6 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: session/executor can place, cancel, read the book and the account without importing kalshi-sdk, and a hung write cannot duplicate itself inside the wrapper.
- Set US-006 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (real SDK method names, `ws_max_retries=0`, unknown vs pool-exhausted, snapshot dicts mutate, no `client_order_id` list filter, WalletHost still unauthenticated).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Observe / unauthenticated `open_ws` | `KalshiAuthFault` |
| Missing PEM file | Factory raises `KalshiAuthFault` before any HTTP |
| Place `KalshiTimeoutError` | `KalshiUnknownWrite(kind="place")`, **1** SDK call |
| Place `KalshiNetworkError` | same unknown, 1 call |
| Place `KalshiPoolExhaustedError` | `KalshiRestError`, 1 call, not unknown |
| Place `KalshiAuthError` | `KalshiAuthFault`, 1 call |
| Place `KalshiRateLimitError` | `KalshiRateLimitFault`, 1 call |
| Place 409 `KalshiConflictError` | `KalshiRestError`, 1 call — not unknown |
| Cancel timeout | `KalshiUnknownWrite(kind="cancel", order_id=...)` |
| Invalid price tick at request build | `KalshiRestError`, 0 HTTP |
| `post_only=False` | Forwarded. Do not coerce True |
| Empty `price_ranges` on public list | unchanged |
| Snapshot then delta seq 1,2 | two events, frozen levels |
| Snapshot seq 1, delta seq 3 | `KalshiSequenceGap`, delta dropped |
| Snapshot sid=1 then snapshot sid=2 | gap event, then second snapshot |
| SDK `KalshiSequenceGapError` on the iterator | gap event, stream may end |
| WS close 4001 | `KalshiWsAuthFault`, stream ends |
| WS close 1006 / reconnect exhausted | `KalshiWsDisconnect` (`ws_max_retries=0` → no silent resume) |
| Delivered object with no `type` | `KalshiWsMalformed`, continue |
| REST fill without `client_order_id` | `None` |
| WS fill without `fill_id` | `fill_id = trade_id` |
| Position `position=None` | `Decimal("0")` |
| `list_all` fake written as `async def` | will break — same US-002 trap |
| PEM in logs | factory + exception + repr tests must not contain `BEGIN` |
| Two `open_ws` calls | two SDK `KalshiWebSocket` instances (property). Capture the returned `KalshiWsClient`. |

---

## Test plan

### Extend: `tests/test_live_paper_kalshi_client.py`

Keep existing public-REST tests. Patch sleep is still only for reads.

Helper: `_write_client(orders=..., portfolio=...)` on top of `_client`. Fake `create_v2` records the `CreateOrderV2Request` (tests may `from kalshi.models.orders import CreateOrderV2Request` / `from kalshi.errors import ...`).

| Test | Setup | Expect |
|---|---|---|
| **V2 place mapping** | `create_v2` records `request` | `CreateOrderV2Request`: `ticker`, `client_order_id`, `side=="bid"`, `post_only is True`, `count`/`price` are `Decimal`, `subaccount`, `time_in_force`, STP. Ack fields mapped. **No float** |
| **V2 cancel mapping** | `cancel_v2` records args | positional `order_id`; kwargs `subaccount`, `market_ticker`. Ack `reduced_by` Decimal |
| Place does not call a non-v2 create | fake `orders.create` raises if touched | only `create_v2` |
| Source has no legacy write path | read `kalshi_client.py` | contains `create_v2` / `cancel_v2` / `"/portfolio/events/orders"`; does not contain a `orders.create(` without `_v2` |
| **Unknown place, no retry** | `create_v2` always `KalshiTimeoutError` | `KalshiUnknownWrite`, `kind=="place"`, same `client_order_id`, **calls == 1**, no `asyncio.sleep` |
| Unknown cancel, no retry | `cancel_v2` raises `KalshiNetworkError` | `KalshiUnknownWrite`, `kind=="cancel"`, `order_id` set, calls == 1 |
| Pool exhausted is not unknown | `KalshiPoolExhaustedError` once | `KalshiRestError`, not `KalshiUnknownWrite`, calls == 1 |
| **Auth error outward (REST)** | `create_v2` raises `KalshiAuthError("denied")` | `KalshiAuthFault`, message has no `BEGIN`, calls == 1 |
| Rate limit outward | `KalshiRateLimitError` | `KalshiRateLimitFault`, calls == 1 |
| 409 is not unknown | `KalshiConflictError` | `KalshiRestError`, calls == 1 |
| Open orders pagination | `list_all` yields A then B | both kept, `status=="resting"` forwarded, `max_pages` absent |
| Fills / positions / balance Decimal | scripted SDK objects | `KalshiFill.fill_id`, `KalshiPosition.position` Decimal, `KalshiBalance.balance` from `balance_dollars` not cents |
| Fees/portfolio do not use V1 `orders.fills` | `orders.fills` boom | `portfolio.fills_all` used |
| **Snapshot + delta seq** | fake WS yields snapshot seq=1 yes `{0.55: 10}` then delta seq=2 `yes` `0.55` `-2` | events: snapshot with copied levels, then delta; after mutating the source dict, snapshot levels unchanged |
| Seq gap drops delta | snapshot seq=1, delta seq=3 | `KalshiSequenceGap(expected=2, received=3)` and **no** `KalshiBookDelta` |
| Sid change emits gap then snapshot | snapshot sid=1 seq=1, snapshot sid=2 seq=1 | gap, then second snapshot |
| WS auth close | iterator raises `KalshiConnectionError("... permanent code 4001: ...")` | one `KalshiWsAuthFault`, then StopAsyncIteration |
| User fill mapping | `type="fill"` payload with `trade_id`, `fee_cost` | `KalshiFill.fill_id==trade_id`, fee Decimal, `client_order_id` set |
| Auth factory | tmp_path PKCS8 PEM via `cryptography`, `open_kalshi_auth_client(False, "kid", path)` | `sdk.is_authenticated is True`; `max_retries==0`; `ws_max_retries==0`; `repr(client)` / a forced `KalshiAuthFault` str do not contain PEM body; `aclose` |
| Unauthenticated `open_ws` | `open_kalshi_rest_client(False)` | `KalshiAuthFault` |
| Import fence | existing test | still only `kalshi_client.py` |
| No float on new dataclasses | walk place ack + book snapshot | `_assert_no_float` |

No live Kalshi HTTP. No browser. Generate the test PEM in `tmp_path`; do not check a fixture PEM into git.

`test_non_idempotent_is_not_retried`: rewrite against `place_event_order` (the production path). Do not keep a test that only calls a private helper.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_kalshi_client.py tests/test_live_paper_kalshi_observe.py

make test

git add src/live_paper/kalshi_client.py tests/test_live_paper_kalshi_client.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass (mocked SDK only):

1. V2 маппинг place/cancel — `create_v2`/`cancel_v2` kwargs and Decimal ack; legacy write path unused.
2. Разбор snapshot/delta и seq — copied frozen levels; contiguous 1 then 2; gap on a skip.
3. Unknown-результат без повтора — timeout/network after submit raises `KalshiUnknownWrite` with exactly one SDK call.
4. Auth-ошибка наружу — REST `KalshiAuthError` → `KalshiAuthFault`; WS 4001 → `KalshiWsAuthFault`.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **`sdk.ws` is a new instance every time.** `open_ws` must capture it. Two `open_ws()` calls are two sockets. US-013 should store one `KalshiWsClient` on the host.
2. **SDK seq gap is often silent.** Resubscribe success does not raise `KalshiSequenceGapError`; we infer it from sid/seq. If the implementer only catches the SDK exception, US-009 will keep quoting a stale book. The wrapper-level contiguity check is mandatory.
3. **`ws_max_retries=0` is load-bearing.** Leaving the default 10 would hide disconnects from US-009. Pin it on the `KalshiConfig` we pass in, not by subclassing `KalshiWebSocket`.
4. **No `client_order_id` GET.** Unknown place cannot be resolved inside this module. That is US-010’s `list_open_orders` scan. Do not add a hidden O(n) helper “to be helpful”.
5. **REST fill id vs WS `trade_id`.** Not proven equal on the wire. Mapper documents the fallback. US-008 / demo smoke (US-014) must confirm before live. If they differ, US-008 ingest key is REST `fill_id` and WS is a hint until REST confirms.
6. **WalletHost stays on the public factory.** paper/live today can construct an unauthenticated client (keys are required by `load_kalshi_settings` but not passed into the SDK yet). First private call would `AuthRequiredError`. US-013 wires this. Do not “fix” it here and drag Engine tests into the story.
7. **`CreateOrderV2Request.side` is bid/ask, not yes/no.** US-012 maps the four legs onto ticker + bid/ask. Do not add `outcome_side` to the place method — the request model does not have it.
8. **Snapshot dicts mutate.** A test that keeps the SDK payload and inspects it later will lie. Copy first.
9. **Malformed frames skipped inside the SDK recv loop never become `KalshiWsMalformed`.** We only see objects that were delivered to the iterator. Halt on gap/disconnect/auth is enough for v1.
10. **Assumption:** one `KalshiWsClient` will later multiplex orderbook + fill + user_orders on the same `connect()` session (SDK supports multiple `subscribe_*` after enter). This story only provides the three methods.
11. **Assumption:** `reduce_only` / `order_group_id` are plumbed now so US-014 smoke and US-010 ownership do not have to reopen the request model.
12. **PEM passphrase.** SDK supports `password=` / `KALSHI_PRIVATE_KEY_PASSPHRASE`. We do not. Unencrypted PKCS8 only, matching “mount the PEM read-only”. Encrypted keys fail at factory with `KalshiAuthFault`.

No Figma. No poly-maker patch. No new dependency.
