# US-009 Implementation Plan

Story: **US-009** — «kalshi_session: книга, readiness и локальный safe mode»

Repo: `/root/work/dota_2_model` on `main`. Add `src/live_paper/kalshi_session.py` (host-owned Kalshi book generations, YES/NO pair, readiness, local safe mode / cancel / fence) and `tests/test_live_paper_kalshi_session.py`. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** add methods to `kalshi_client.py` (already 1026 lines). Do **not** implement boot REST reconcile / `client_order_id` namespace (US-010). Do **not** implement desired-order sync from fair / fee gate / paper book rules (US-012). Do **not** wire `WalletHost` / `MatchWorker` (US-013). Do **not** bump `session.jsonl` (US-011). Do **not** `import kalshi` outside `kalshi_client.py`. Do **not** call Engine halt, `engine.gateway`, or `StrategyCell`. Do **not** market-flatten.

---

## Binding decisions (feature.json / overlay / US-001..008 learnings)

Cross-ref `current-task/feature.json` FR-6, FR-11, US-009 `changes`, Resolved Questions (Kalshi WS handshake is auth; paper needs a read key for the book; observe has no auth WS), Non-Goals (no economic RiskManager, no market-flatten, Engine/PM untouched, no generic Venue protocol), and `docs/plans/kalshi-overlay.md` §§ Kalshi book and mid, Kalshi local safe mode, Dual trading (operational safety row), Config `[kalshi]`.

1. **New module only.** All book-pair math, generation, readiness, and safe mode live in `kalshi_session.py`. `kalshi_client.py` already yields `KalshiBookSnapshot` / `KalshiBookDelta` / `KalshiSequenceGap` / `KalshiWsDisconnect` / `KalshiWsAuthFault` / `KalshiWsMalformed` and already drops a gapped delta. Session **consumes** those events. Do not add REST orderbook, resubscribe helpers, or extra WS methods to the client.
2. **Event-driven core; this story does not start WS tasks.** `WalletHost` (US-013) will own `subscribe_orderbook` / `subscribe_fills` / `subscribe_user_orders` tasks and forward frozen events. This story: `start_book` bumps a generation, `apply_book_event` / `apply_user_event` / `poll` advance state, `needs_resubscribe()` tells the host to cancel the book task and subscribe again (`send_initial_snapshot=True` is already inside `subscribe_orderbook`). Tests never construct `KalshiWsClient`.
3. **Book pair is implied YES/NO from two bid sides, Decimal only.** `yes_bid = best YES bid`, `yes_ask = 1 − best NO bid`, `no_bid = best NO bid`, `no_ask = 1 − best YES bid`. Side mids are the midpoint of that side's bid/ask. Orient **once** to P(Radiant) with the Kalshi bind's `yes_is_radiant` (not PM's). Valid pair: both bid sides present, finite prices, positive sizes, `yes_bid < yes_ask`. Invalid pair **blocks quoting on that ticker only** — it is not safe mode and does not cancel.
4. **Generation unready until a full snapshot is applied.** Deltas before that snapshot are ignored. After a gap/disconnect/parse/auth fault the generation is dead: later frames on that generation (including a sid-change snapshot the client may yield after `KalshiSequenceGap`) are ignored. Never quote the last cached book across a fault.
5. **Quiet ≠ stale.** Any applied snapshot **or** contiguous delta on the live generation resets the quiet timer. At `book_stale_s` of silence: stop publishing targets and request a fresh snapshot (`needs_resubscribe`). Do **not** enter safe mode on silence. A new snapshot (even identical levels) restores readiness. If no snapshot arrives within another `book_stale_s` of the refresh request → safe mode (`book_stale`). No new TOML knob.
6. **Private-WS blindness is a dead/auth-failed user connect or overdue REST reconcile, not “no fills”.** An idle but connected user stream is healthy. `mark_reconcile_ok()` stamps REST liveness (US-010 will call it on each periodic reconcile; this story only consumes the stamp). `live` requires the user stream up to publish; `paper` does not. Disconnect/auth on the user stream is an **account-level** fault (cancel every live ticker).
7. **Safe mode is venue-local, ordered, idempotent.** Clear desired → block new writes except cancel/reconcile → cancel strategy-owned resting (affected ticker, or **all** tickers if private-blind / auth / account-level) → REST-poll until those order ids are absent or `fence_timeout_s` → preserve store positions/ledger, **no** `place_event_order` → one Telegram alert per changed fault label → reopen only after proven fence + `start_book` + new snapshot + `mark_reconcile_ok`. Unproven fence latches Kalshi blocked until this session object is discarded (process/teardown). None of this imports Engine.
8. **Strategy-owned this story = rows in `kalshi.db` with a non-null `order_id`.** US-010 owns the `client_order_id` namespace and foreign-position halt. Do not cancel a REST resting order that is not in the store. Do not invent ownership heuristics.
9. **poly-maker is frozen.** Zero file changes there. No new dependency. No generic `Venue` protocol.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `kalshi_client.py` | Snapshot/delta/gap/disconnect/auth/malformed; `subscribe_orderbook(ticker)`; `cancel_event_order`; `list_open_orders`; **no** REST orderbook | **Unchanged.** Session imports the frozen event types and REST methods. |
| Client seq check | Same-sid skip → `KalshiSequenceGap` and **drop** the delta; new sid → gap **then** snapshot | Session treats the gap as a fault and **must ignore** the trailing snapshot until a new generation. |
| `KalshiWsMalformed` | Client **yields** and continues the generator | Session: parse fault → safe mode; do not keep quoting. |
| `kalshi_store.py` | Intents/orders/fills/ledgers; `open_orders()`; cancel ack/fail never DELETE; positions survive anything the session does | Session writes cancel intents and applies ack/fail/unknown. **Never** wipes fills/ledgers. |
| `KalshiProfile` | `book_stale_s=30`, `private_ws_blind_s=15`, `reconcile_interval_s=20`, `fence_timeout_s=20` | Read from the injected profile. `reconcile_interval_s` / `base_size_usd` unused here (US-010 / US-012). |
| `session_quoting.read_raw_pair` | PM two-token float books, `SignalReason` | **Do not call.** Kalshi pair is Decimal and implied from YES/NO bids. |
| `FreshnessWatchdog` | In `session_engine.py`, imports `Engine` | **Do not import.** `poll()` + `time.monotonic` (monkeypatch in tests). |
| `FaultReporter` | PM journal + deduped Telegram, schema 5 | **Do not use** (US-011 owns Kalshi tape). Alert via `notify_in_background` only. |
| `WalletHost` / `MatchWorker` | Public REST, observe bind, PM Engine | **Unchanged** (US-013). |
| `session.jsonl` | schema 5 | **Unchanged** (US-011). |

Do not copy:

- poly-maker `RiskManager` / day kill / flatten.
- `GatedRegimeMachine` / `StrategyCell.clear` as the “desired” mechanism.
- PM `RawBookPair` floats.

---

## Requirements traceability (US-009 `changes`)

| Change | Plan |
|---|---|
| `kalshi_session.py`; host-owned session; generation per book subscription | Design §§ 1–2 |
| Book pair from two bid sides; side mids; orient once to P(Radiant) | Design § 3 |
| Valid pair requires both bids, finite prices, positive sizes, `yes_bid < yes_ask`; else block Kalshi only | Design § 3 |
| Generation unready until full snapshot; deltas contiguous by seq; gap/disconnect/parse/auth → clear readiness + safe mode; never continue on stale/cached book | Design §§ 2, 4, 7 |
| `book_stale_s`: quiet ≠ stale; at threshold stop publishing + request snapshot; success (even unchanged) refreshes; failure → safe mode | Design § 5 |
| `private_ws_blind_s` = dead/auth-failed user connect or overdue REST reconcile, not absence of fills | Design § 6 |
| Safe mode idempotent, strict order: clear desired → block writes except cancel/reconcile → cancel strategy-owned (all tickers if private-blind / account-level) → REST-poll until absent or `fence_timeout_s` → preserve positions/ledger, no flatten → one alert per fault-state change → reconnect + new snapshot + reconcile before reopen | Design §§ 7–8 |
| Unproven fence blocks Kalshi until session end | Design § 7 |
| No Engine halt / `engine.gateway` / PM `StrategyCell` | Design § 10; source scan in tests |
| Autotests `tests/test_live_paper_kalshi_session.py`: unready until snapshot; gap/stale/blind → safe mode; step order + idempotency; unproven fence blocks to end | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `KalshiBookSnapshot`, `KalshiBookDelta`, `KalshiBookLevel`, `KalshiSequenceGap`, `KalshiWsDisconnect`, `KalshiWsAuthFault`, `KalshiWsMalformed`, `KalshiBookEvent`, `KalshiUserFillEvent`, `KalshiUserOrderEvent` | Event inputs. Import from `kalshi_client`. Never `import kalshi`. |
| `KalshiRestClient.cancel_event_order` / `list_open_orders` | Fence only. `place_event_order` must not appear in this file. |
| `KalshiCancelAck`, `KalshiUnknownWrite`, `KalshiAuthFault`, `KalshiRateLimitFault`, `KalshiRestError` | Cancel outcomes. Unknown → do not retry the write; REST list is the proof. |
| `KalshiStore.open_orders` / `write_cancel_intent` / `apply_cancel_ack` / `apply_cancel_fail` / `apply_unknown_write` / `position` / `cash` | Ownership list + durable cancel. Ledger readers only to assert survival. |
| `KalshiProfile` (`session_config`) | Timeouts. Inject; do not `read_template()` inside the session. |
| `KalshiSettings` | `trading` (`paper`/`live`) and `subaccount`. |
| `notify_in_background` | One alert per **changed** fault label. Patch at `live_paper.kalshi_session.notify_in_background` in tests (same as observe). |
| `time.monotonic` | Quiet / blind / fence clocks. Tests monkeypatch `live_paper.kalshi_session.time.monotonic` (US-003 cache pattern). |
| `tests/test_live_paper_kalshi_client.py` `_assert_no_float`, `_write_client` / `_RecordingOrders` shape | Copy helpers into the session test; do not import private test fns. |
| `Decimal`, `ONE = Decimal("1")` | Pair math. |

Do not import `wallet_host`, `match_worker`, `session_engine`, `Engine`, `StrategyCell`, `PaperGateway`, `WalletStateStore`, `kalshi_observe`, `kalshi`. Do not open `paper.db`. Do not write `session.jsonl`.

---

## Design

### 1. Types (`src/live_paper/kalshi_session.py`)

Thin module. Frozen values, one session class, no Venue protocol. All function arguments required (AGENTS.md).

```python
KalshiFault = Literal[
    "book_gap",
    "book_disconnect",
    "book_parse",
    "book_stale",
    "auth",
    "private_ws_blind",
    "fence_unproven",
]

FENCE_POLL_SLEEP_S = 0.5  # ponytail: busy-ish REST poll; upgrade: backoff / WS-ack
```

`book_gap` / `book_disconnect` / `book_parse` / `book_stale` are **ticker-scoped**. `auth` / `private_ws_blind` / `fence_unproven` are **account-scoped** (cancel every live ticker; `fence_unproven` never reopens).

```python
@dataclass(frozen=True)
class KalshiBookPair:
    """Implied YES/NO pair in dollars. radiant_mid is P(Radiant)."""

    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    yes_mid: Decimal
    no_mid: Decimal
    radiant_mid: Decimal


@dataclass(frozen=True)
class KalshiDesired:
    """Working quote slot. US-012 fills fields; this story only stores and clears it."""

    ticker: str
```

Do not use `dict[str, Any]`. Do not use `RawBookPair`. No `float` in this module.

### 2. `KalshiSession`

```python
class KalshiSession:
    def __init__(
        self,
        rest: KalshiRestClient,
        store: KalshiStore,
        profile: KalshiProfile,
        settings: KalshiSettings,
    ) -> None:
        """Process-wide Kalshi book + safety state. Does not open WS or take the flock."""
```

`settings.trading` must be `"paper"` or `"live"` (`ValueError` otherwise — observe never owns this object). `_source`: `"live"` if trading is live else `"paper"`. `_subaccount = settings.subaccount`.

On init:

- `_last_reconcile_at = time.monotonic()` so we do not immediately blind before US-010's first stamp.
- `_user_ws_up = False`.
- `_fence_unproven = False`.
- `_fault_label: str | None = None` (`None` = healthy).
- `_safe_mode_running = False`.
- `_books: dict[str, _TickerBook]` empty.
- `_desired: dict[str, KalshiDesired]` empty.
- `_needs_resubscribe: set[str]` empty.
- `_reopen_needs_reconcile = False` (set True after a proven fence).

Private `_TickerBook` (not exported; a frozen dataclass **or** a small mutable class — mutable is fine here, it is per-ticker runtime state, not a value we pass around):

- `generation: int` (starts at 0; `start_book` increments)
- `yes_is_radiant: bool`
- `yes_levels: dict[Decimal, Decimal]`
- `no_levels: dict[Decimal, Decimal]`
- `snapshot_applied: bool`
- `generation_live: bool` (False after a fault until `start_book`)
- `last_book_at: float` (monotonic)
- `refresh_started_at: float | None` (set when silence trips refresh)
- `writes_blocked: bool`

```python
def start_book(self, ticker: str, yes_is_radiant: bool) -> int:
    """Bump the ticker's book generation. Unready until the next snapshot. Returns generation."""
```

Clears levels, `snapshot_applied=False`, `generation_live=True`, `refresh_started_at=None`, drops ticker from `_needs_resubscribe`. Does **not** clear `_fence_unproven`. Does not touch the store.

```python
async def apply_book_event(self, event: KalshiBookEvent) -> None:
async def apply_user_event(self, event: KalshiFill | KalshiRestingOrder | KalshiWsDisconnect | KalshiWsAuthFault | KalshiWsMalformed) -> None:
async def poll(self) -> None:
```

`poll` is the only timer: book-age, snapshot-refresh timeout, private-WS blindness. US-013 will call it from the host loop; tests call it after moving `time.monotonic`.

```python
def set_desired(self, desired: KalshiDesired) -> None:
def clear_desired(self, ticker: str) -> None:
def desired(self, ticker: str) -> KalshiDesired | None:
def book_pair(self, ticker: str) -> KalshiBookPair | None:
def can_publish_targets(self, ticker: str) -> bool:
def write_allowed(self, ticker: str) -> bool:
def needs_resubscribe(self) -> tuple[str, ...]:
def mark_reconcile_ok(self) -> None:
def note_user_ws_up(self) -> None:
```

`set_desired`: if `not write_allowed(ticker)` or `_fence_unproven`, ignore a non-clear set (do not store). `clear_desired` always allowed.

`mark_reconcile_ok`: `_last_reconcile_at = time.monotonic()`; if `_reopen_needs_reconcile`, clear that flag (reopen gate). Does **not** by itself set `can_publish_targets` True.

`note_user_ws_up`: `_user_ws_up = True`. A subsequent user `KalshiFill` / `KalshiRestingOrder` also sets it. Idle connected stream stays up with no fills.

### 3. Pair derivation

Public so tests can hit it without a session:

```python
def derive_kalshi_book_pair(
    yes_levels: tuple[KalshiBookLevel, ...] | dict[Decimal, Decimal],
    no_levels: tuple[KalshiBookLevel, ...] | dict[Decimal, Decimal],
    yes_is_radiant: bool,
) -> KalshiBookPair | None:
    """Best YES bid + best NO bid → implied pair, or None if unusable."""
```

Best bid = **maximum price** among levels with `count > 0` and `price.is_finite()` and `count.is_finite()`. Missing side, non-finite, `count <= 0` only, or `yes_bid >= yes_ask` → `None`.

```
yes_ask = 1 - no_bid
no_ask = 1 - yes_bid
yes_mid = (yes_bid + yes_ask) / 2
no_mid = (no_bid + no_ask) / 2
radiant_mid = yes_mid if yes_is_radiant else (1 - yes_mid)
```

Do not require prices in `(0, 1)` beyond `yes_bid < yes_ask` (overlay does not). Do not invent a `yes + no == 1` check (US-007: do not invent dire = 1 − yes for a pair-sum). Do not call `read_raw_pair`.

`book_pair(ticker)` returns `None` when: unknown ticker, generation not live, snapshot not applied, or `derive_*` is `None`. It may still return the last valid pair during a **refresh wait** (`refresh_started_at` set) — callers must use `can_publish_targets`, not “pair is not None”. After a fault, levels are cleared so `book_pair` is `None`.

### 4. Book events and generation

`apply_book_event`:

| Event | Behavior |
|---|---|
| Snapshot, `generation_live`, ticker matches | Replace yes/no maps from levels (`count > 0` only). `snapshot_applied=True`. `last_book_at=now`. `refresh_started_at=None`. Drop from `_needs_resubscribe`. |
| Snapshot, generation dead or unknown ticker | Ignore. |
| Delta, `snapshot_applied` and live | `count = old + delta`; drop level if `count <= 0`. `last_book_at=now`. Session does **not** re-check seq (client already did). |
| Delta before snapshot or dead generation | Ignore. Do not apply to an empty/cached book. |
| `KalshiSequenceGap` | Ticker-scoped safe mode `book_gap`. Kill generation, clear levels. |
| `KalshiWsDisconnect` on the book stream | Ticker-scoped safe mode `book_disconnect`. |
| `KalshiWsAuthFault` | Account-scoped safe mode `auth`. |
| `KalshiWsMalformed` | Ticker-scoped safe mode `book_parse`. |

Gap then snapshot in one client stream (sid change): after the gap the generation is dead, so the trailing snapshot is ignored. Host must `start_book` (new generation) and subscribe again.

Kill generation: `generation_live=False`, `snapshot_applied=False`, empty maps, `refresh_started_at=None`, add ticker to `_needs_resubscribe`.

### 5. Book-age (`poll`)

Per live ticker with `snapshot_applied`:

- `now - last_book_at < book_stale_s` → do nothing. Deltas keep this true on a quiet-looking but live stream.
- Else if `refresh_started_at is None`: **refresh, not safe mode.** `can_publish_targets` becomes False. Add ticker to `_needs_resubscribe`. Set `refresh_started_at=now`. Do not clear levels yet (but do not publish them).
- Else if `now - refresh_started_at >= book_stale_s`: safe mode `book_stale`. Clear levels. Kill generation.

A `start_book` + snapshot while refreshing is success even if levels are identical.

`can_publish_targets(ticker)` is True iff **all**:

- not `_fence_unproven`
- ticker generation live and `snapshot_applied`
- `refresh_started_at is None`
- `derive_kalshi_book_pair` is not None
- ticker not `writes_blocked`
- if `settings.trading == "live"`: `_user_ws_up`
- not `_reopen_needs_reconcile`

`write_allowed(ticker)` is False when `_fence_unproven` or ticker `writes_blocked` or account-level block (`auth` / `private_ws_blind` / any in-flight account safe mode). Cancel/reconcile paths do not go through `write_allowed`.

### 6. Private WS and REST liveness

`apply_user_event`:

| Event | Behavior |
|---|---|
| `KalshiFill` / `KalshiRestingOrder` | `_user_ws_up = True`. Do **not** reset book age. Do not ingest fills here (US-010 / US-012). |
| `KalshiWsDisconnect` | `_user_ws_up = False`. Account-level safe mode `private_ws_blind`. |
| `KalshiWsAuthFault` | `_user_ws_up = False`. Account-level `auth`. |
| `KalshiWsMalformed` | Account-level `book_parse` is the wrong label — use `private_ws_blind` if you must pick one existing, **or** treat as `auth`-class account fault. Prefer a parse on the **user** stream as account-level `book_parse` only if the label stays in `KalshiFault`; simplest: account-level safe mode with fault `private_ws_blind` is wrong. **Use `auth` only for `KalshiWsAuthFault` / REST `KalshiAuthFault`.** Add **user-stream parse** to ticker-vs-account: it is account-level because we cannot trust private state. Put it under `book_parse` with **account scope** (cancel all tickers). Same `KalshiFault` string, wider cancel scope. |

`poll` blindness (do not fire if `_fence_unproven`):

- `live` and (not `_user_ws_up`) and `(now - session_started_at) >= private_ws_blind_s` and never had `_user_ws_up` this session → `private_ws_blind` (connect never came up). If the stream was up and then died, `apply_user_event(disconnect)` already entered safe mode immediately — do not wait the timer.
- `now - _last_reconcile_at >= private_ws_blind_s` → `private_ws_blind` (overdue REST). Init stamps `_last_reconcile_at`, so a book unit test that never waits 15s is safe.

Paper: skip the “user WS must be up to publish” gate. Paper still honors overdue REST reconcile (same timer) — US-010 will stamp it. Disconnect on a user stream, if the host forwards one in paper, is still account-level safe mode (defensive).

Absence of fills while `_user_ws_up` is **not** blind.

### 7. Safe mode order of operations

```python
async def _enter_safe_mode(self, fault: KalshiFault, ticker: str | None) -> None:
```

`ticker` is the affected book ticker; `None` with an account-level fault.

**Idempotency** (`_safe_mode_running` flag, not a long-held `asyncio.Lock` around REST):

1. If `_fence_unproven`: maybe update label + alert if the label changed; return. Never reopen.
2. If `_safe_mode_running`: if the **fault label** changed, alert once; do not start a second cancel/fence. Return.
3. If already in the same label and writes already blocked for the required scope and not running: return (second gap after fence completed proven, still waiting reopen — do not cancel twice). After a **proven** fence we are in recovering (`writes_blocked` still True until reopen conditions). A **new** fault then starts a fresh enter (cancel again). A **duplicate** same-label event is a no-op.

**Fault label** (the Telegram / identity key):

- ticker-scoped: `f"{fault}:{ticker}"` e.g. `book_gap:KXDOTA2MAP-1-AUR`
- account-scoped: `fault` e.g. `private_ws_blind`, `fence_unproven`

Alert when `_fault_label` **changes** (including `None` → X and X → `None` on full recovery). Message:

- enter: `live-paper kalshi: safe mode {label}`
- recover (label becomes `None`): `live-paper kalshi: recovered`

No secrets, no PEM, no order ids required. Do not journal (US-011).

**Strict order** (recordable via fakes: desired empty before first `cancel_event_order`):

1. **Clear desired.** Ticker-scoped: `clear_desired(ticker)`. Account-scoped: clear every ticker. Do this **before** any REST.
2. **Block writes.** Set `writes_blocked` on the affected ticker(s). Account-level sets it on every known ticker **and** a session-level `_account_block` so a ticker started later is also blocked.
3. **Cancel strategy-owned resting.** `owned = store.open_orders()` filtered to those with `order_id is not None`, and:
   - ticker-scoped: `order.ticker == ticker`
   - account-scoped (`auth`, `private_ws_blind`, or `ticker is None`): **all** such rows
   For each: `write_cancel_intent(...)` then `await rest.cancel_event_order(order_id, ticker, subaccount, client_order_id)`.
   - `KalshiCancelAck` → `apply_cancel_ack`
   - `KalshiUnknownWrite` → `apply_unknown_write` (**no retry**)
   - `KalshiRestError` / `KalshiRateLimitFault` → `apply_cancel_fail` (keep row; fence still polls)
   - `KalshiAuthFault` → `apply_cancel_fail`, and the fault label becomes `auth` (account-level) if it was not already
   Skip rows with `order_id is None` (cannot hit V2). They stay local; `write_allowed` is already False.
   **Never** `place_event_order`. **Never** reduce-only / IOC / FOK flatten.
4. **REST fence.** `started = time.monotonic()`. Loop:
   - `resting = await rest.list_open_orders(subaccount)`
   - Consider **our** ids: the `order_id`s we attempted to cancel (the filtered store set **as captured at step 3**, plus any of those still in `store.open_orders()`). Proven when **none** of those ids appear in `resting`.
   - Foreign REST rows (not in that id set) are ignored — they do not block the fence and are not cancelled.
   - If proven: break.
   - If `time.monotonic() - started >= profile.fence_timeout_s`: `_fence_unproven = True`, label `fence_unproven`, alert if changed, **return**. Kalshi stays blocked for the life of this object.
   - Else `await asyncio.sleep(FENCE_POLL_SLEEP_S)`.

   ponytail: `FENCE_POLL_SLEEP_S = 0.5` until timeout; upgrade: wait on user-order WS. Tests patch sleep to a no-op **and must move monotonic** on the unproven path or the loop never ends.

5. **Preserve ledger.** Do not `DELETE` fills, positions, cash, or orders. Tests assert `store.position` / `store.cash` unchanged across a safe-mode enter that cancelled an unfilled rest (unfilled cancel does not ingest a fill).
6. **Alert** if the label changed (step 1–4 may already have alerted on enter; do not double-send the same label).
7. **Recovering.** On proven fence: `_safe_mode_running = False`, `_reopen_needs_reconcile = True`, `_account_block` stays True until reopen, every affected ticker stays `writes_blocked`, add them to `_needs_resubscribe`. Host reconnects WS, `start_book`, applies a **new** snapshot, `mark_reconcile_ok`, `note_user_ws_up` (live). Then clear `_account_block` / `writes_blocked` for tickers that are generation-live with a snapshot. Helper:

```python
def try_reopen(self) -> None:
    """Clear write blocks when fence is proven and each blocked ticker has a fresh snapshot (+ live user WS, + reconcile stamp)."""
```

Call `try_reopen` at the end of snapshot apply, `mark_reconcile_ok`, and `note_user_ws_up`. If `_fence_unproven`, `try_reopen` is a no-op.

Do not auto-open a `KalshiWsClient`. Do not call `Engine`.

### 8. Cancel scope

| Fault | Cancel |
|---|---|
| `book_gap`, `book_disconnect`, `book_parse` (book stream), `book_stale` | That ticker's store `open_orders` with `order_id` |
| `auth`, `private_ws_blind`, user-stream parse | **All** tickers' store open orders with `order_id` |
| `fence_unproven` | Already fenced / blocked; no extra flatten |

Two maps in one process: a gap on A must not cancel B's orders. A blind user stream must cancel A and B.

### 9. What this story does not touch

- `WalletHost` / `open_wallet_host` / PM flock / Kalshi flock acquisition
- Boot REST reconcile, unexplained position, `client_order_id` namespace (US-010)
- `kalshi_executor` / fee gate / V2 four-leg / paper fill rules (US-012)
- `MatchWorker._compute_decision` / `predict_fair`
- `session.jsonl` schema 6 / Kalshi tape (US-011)
- `kalshi_client.py` (no REST book, no extra subscribe flags)
- Docker / `docs/live-paper.md` (US-015)
- `poly-maker`
- Periodic `reconcile_interval_s` loop (US-010 calls `mark_reconcile_ok`)

### 10. Forbidden imports / calls

`kalshi_session.py` must not mention: `Engine`, `engine.gateway`, `StrategyCell`, `Regime`, `polymaker`, `halt`. Tests grep the source. Safe mode is not Engine halt.

---

## State machine (text)

```
                    start_book(ticker)
                          |
                          v
              +-----------------------+
              | awaiting_snapshot     |  generation live, snapshot_applied=False
              | can_publish=False     |  deltas ignored
              +-----------+-----------+
                          | snapshot
                          v
              +-----------------------+     invalid pair
              | ready (if pair ok)    |------------------> awaiting_valid_book
              | can_publish=True*     |<-----------------  (still generation live;
              +--+--------+--------+--+     later delta      can_publish=False)
                 |        |        |
     poll silence|        | gap / disconnect / parse / auth / user-dead / REST overdue
     >= stale    |        |
                 v        v
        +-------------------+                    +------------------------+
        | refreshing        |  snapshot ok       | safe_mode (running)    |
        | can_publish=False |------------------> | 1 clear desired        |
        | needs_resubscribe |  (even unchanged)  | 2 block writes         |
        +--------+----------+                    | 3 cancel owned         |
                 | no snapshot in book_stale_s   | 4 REST fence           |
                 v                               +----+-------------+-----+
        safe_mode book_stale                          |             |
                                                      |proven       |timeout
                                                      v             v
                                           +----------------+  +------------------+
                                           | recovering     |  | blocked_until_end|
                                           | needs snapshot |  | fence_unproven   |
                                           | + reconcile    |  | can_publish=False|
                                           | try_reopen()   |  | no try_reopen    |
                                           +--------+-------+  +------------------+
                                                    |
                                                    v
                                                 ready*
```

\* `live` also requires `_user_ws_up`. After recovering, `_reopen_needs_reconcile` must be cleared by `mark_reconcile_ok`.

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Pair math

`KalshiBookPair`, `derive_kalshi_book_pair`. Decimal, no float. Tests can run without REST.

### Step 2 — Session skeleton + generation

`KalshiSession.__init__`, `start_book`, `apply_book_event` for snapshot/delta only (no safe mode yet), `book_pair`, `can_publish_targets`. Unready until snapshot. Invalid pair does not publish.

### Step 3 — Age + blindness `poll`

Quiet timer, `needs_resubscribe`, refresh timeout → will call `_enter_safe_mode` in step 4. User-up / `mark_reconcile_ok` / live gate. Monkeypatch `time.monotonic`.

### Step 4 — Safe mode

`set_desired` / `clear_desired`, `_enter_safe_mode` in the exact order, cancel + fence, alerts, `try_reopen`, `_fence_unproven` latch. Wire gap/disconnect/parse/auth/stale/blind to it.

### Step 5 — Tests

`tests/test_live_paper_kalshi_session.py` as below. `tmp_path` store. Fake REST SDK. No live HTTP. No `import kalshi` in the **session** module (the test may import `kalshi.errors` only if it builds a `KalshiRestClient` the same way as `test_live_paper_kalshi_client.py`; prefer a duck-typed rest object **cast** to `KalshiRestClient` to avoid `import kalshi` in this test file at all). `asyncio.run`, no pytest-asyncio.

### Step 6 — Quality gate

See Verification. `git add` new files before `make lint-all`.

### Step 7 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: a Kalshi book fault must stop Kalshi quoting and cancel/fence our resting orders without touching the Engine or flattening inventory.
- Set US-009 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (generation dead on gap ignores trailing snapshot; quiet ≠ stale; blindness is connect/reconcile not fills; unproven fence is process-lifetime; no client.py edits; not wired to WalletHost).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Delta before snapshot | Ignored; `can_publish` False |
| One-sided book (YES bids only) | `book_pair` None; **not** safe mode; wait for a delta |
| Crossed `yes_bid >= yes_ask` | Same: block quote only |
| Gap then sid-change snapshot on same stream | Safe mode on gap; snapshot ignored; needs `start_book` |
| Contiguous delta after ready | Pair updates; quiet timer resets; still ready |
| Silence for `book_stale_s − ε` | Still ready |
| Silence for `book_stale_s` | Refresh: `can_publish` False, `needs_resubscribe`, no cancel yet |
| Snapshot during refresh (same levels) | Ready again |
| No snapshot for another `book_stale_s` | Safe mode `book_stale` |
| Second gap while fence REST is in flight | No second cancel list; at most one extra alert if label changed |
| Second gap after proven fence, before reopen | Same label → no-op; still blocked until snapshot+reconcile |
| Unproven fence then a new snapshot | Still blocked; `try_reopen` no-op |
| `set_desired` while writes blocked | Ignored |
| Store position +5 YES, then gap | After fence, position still +5; no sell placed |
| Open order without `order_id` | No REST cancel; write still blocked |
| REST resting order not in store | Left alone; fence does not wait on it |
| Two tickers; gap on A | Cancel A only; B may still `can_publish` |
| Two tickers; user WS disconnect | Cancel A and B |
| Live, user WS never up, `poll` at t=`private_ws_blind_s` | Safe mode blind; paper would still publish if the book is ready |
| Live, user WS up, zero fills, `poll` | Not blind |
| `KalshiUnknownWrite` on cancel | No second cancel; fence REST decides |
| `cancel_event_order` raises `KalshiAuthFault` | Account-level `auth`; keep fencing until timeout or empty |
| `list_open_orders` raises | Treat as not-yet-proven; keep looping until `fence_timeout_s` then unproven |
| `place_event_order` from this module | Forbidden |
| Import `session_engine` / `Engine` | Forbidden |
| Adding to `kalshi_client.py` | Forbidden |

---

## Test plan

### New: `tests/test_live_paper_kalshi_session.py`

Helpers:

- `_profile(**overrides) -> KalshiProfile` with the five `[kalshi]` fields (use small timeouts in tests: `book_stale_s=10`, `private_ws_blind_s=5`, `fence_timeout_s=10`, keep `reconcile_interval_s=20`, `base_size_usd=10`).
- `_settings(trading="paper") -> KalshiSettings` (`key_id="id"`, `private_key_path="/dev/null"`, `subaccount=0`).
- `_store(tmp_path) -> KalshiStore` (close in fixture).
- `_clock` box + monkeypatch `live_paper.kalshi_session.time.monotonic`.
- `_session(...)` wiring rest/store/profile/settings.
- `_snapshot(ticker, sid, seq, yes: dict, no: dict) -> KalshiBookSnapshot` using `KalshiBookLevel`.
- `_delta(...) -> KalshiBookDelta`.
- `_resting_in_store(session)`: `write_place_intent` + `apply_place_ack` so cancel has an `order_id`.
- Fake REST: `list_open_orders` / `cancel_event_order` recording call order. Scripted sequence of resting tuples (empty = fenced). `place_event_order` raises `AssertionError` if called.
- Patch `notify_in_background` to a list.
- Patch `asyncio.sleep` to a no-op **or** to advance `_clock` by `FENCE_POLL_SLEEP_S`. Unproven tests **must** jump monotonic past `fence_timeout_s`.
- `_assert_no_float` copied locally.
- `TICKER_A`, `TICKER_B`.

| Test | Setup | Expect |
|---|---|---|
| **Unready until snapshot** | `start_book`; optional delta; no snapshot | `can_publish_targets` False; `book_pair` None |
| Snapshot then publish | Two-sided bids, `yes_is_radiant=True` | pair yes_bid/ask/no_* match formula; `radiant_mid == yes_mid`; `can_publish` True |
| **Orient once** | Same book, `yes_is_radiant=False` | `radiant_mid == 1 - yes_mid` |
| Delta before snapshot ignored | Delta then snapshot with different levels | Pair equals the **snapshot**, not snapshot+delta |
| Contiguous delta after snapshot | YES size change | Pair updates; still ready |
| One-sided / crossed | Missing NO bids, or `yes_bid + no_bid >= 1` | `can_publish` False; **no** alert; **no** cancel |
| **Gap → safe mode** | Ready + resting order; `KalshiSequenceGap` | desired cleared **before** cancel (fake cancel asserts `desired is None`); `can_publish` False; `book_pair` None; trailing snapshot ignored; `needs_resubscribe`; one alert; store position unchanged |
| Disconnect / malformed book | Ready; those events | Safe mode; generation dead |
| Auth on book | Ready on A and B with orders | Cancels **both** tickers |
| **Stale refresh then success** | Ready; jump monotonic by `book_stale_s`; `poll`; `start_book`; same snapshot | After `poll`: not safe mode, `can_publish` False, `needs_resubscribe`, no cancel. After new snapshot: `can_publish` True, one or zero alerts (none) |
| **Stale refresh then fail** | After refresh `poll`, jump another `book_stale_s`; `poll` | Safe mode `book_stale`; cancel runs |
| Quiet with deltas | Deltas every 1s for `book_stale_s + 1` of **clock** but last_book kept fresh | Still ready; no resubscribe |
| **Blind: user disconnect** | Live settings, `note_user_ws_up`, ready A+B; user `KalshiWsDisconnect` | Account cancel both; not because of zero fills |
| **Blind: not “no fills”** | Live, user up, no fill events, `poll` at t=100 | Still ready |
| **Blind: overdue reconcile** | `mark_reconcile_ok` at 0; jump `private_ws_blind_s`; `poll` | Safe mode `private_ws_blind` |
| Live never-up timer | Live, never `note_user_ws_up`, book ready, `poll` at `private_ws_blind_s` | Safe mode; paper equivalent still `can_publish` |
| **Step order** | Resting order; gap | Call log: `clear` (desired None) → `cancel_event_order` → `list_open_orders`. Alert once. |
| **Idempotent enter** | Gap twice (second during or after first fence) | `cancel_event_order` once per order; **one** alert for that label |
| Proven fence then reopen | Fence lists empty on first poll; `start_book` + snapshot + `mark_reconcile_ok` | `can_publish` True; recover alert once (`live-paper kalshi: recovered`) |
| **Unproven fence blocks to end** | `list_open_orders` always returns our id; monotonic jumps past `fence_timeout_s` | `_fence_unproven`; `can_publish` False after `start_book`+snapshot+`mark_reconcile_ok`; no `place_event_order`; further enter is no-op; label `fence_unproven` |
| Two tickers gap on A | Orders on A and B | Cancel A only; B still publish |
| Ledger survives | `ingest_fill` +5 then gap on an empty rest | `position`/`cash` unchanged |
| No Engine | Read `kalshi_session.py` | No `Engine`, `gateway`, `StrategyCell`, `halt`, `import kalshi`, `place_event_order` |
| Decimal only | Ready pair | `_assert_no_float` |

Do not sleep real time. Do not hit Kalshi HTTP. Do not construct `WalletHost` / `MatchWorker`.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_kalshi_session.py

make test

git add src/live_paper/kalshi_session.py tests/test_live_paper_kalshi_session.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. **Unready до снапшота** — `start_book` without a snapshot (and a delta without a snapshot) → `can_publish_targets` False.
2. **gap/stale/blind → safe mode** — sequence gap; failed book-age refresh; user-WS death **or** overdue `mark_reconcile_ok` → writes blocked, generation dead, cancel/fence path, not “no fills”.
3. **Порядок и идемпотентность шагов** — desired cleared before cancel; cancel before fence list; second identical fault does not double-cancel or double-alert.
4. **Недоказанный fence — блок до конца** — timeout with our id still resting → no reopen after a later healthy snapshot; session stays blocked.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **Sid-change snapshot after gap.** The client yields `KalshiSequenceGap` then the new snapshot. Treating the snapshot as recovery would quote a book we have not cancelled/fenced. Generation-dead ignore is load-bearing. Test it.
2. **Quiet vs stale.** Resetting the age timer on deltas is the overlay rule. If the implementer keys age off last **snapshot** only, a live stream still trips refresh every 30s — extra resubscribes, not a halt, but not what the story asked.
3. **Snapshot-refresh timeout reuses `book_stale_s`.** Overlay gives no second knob. Do not invent `book_refresh_timeout_s`.
4. **Fence loop vs frozen monotonic.** Unproven tests that patch `asyncio.sleep` to a no-op and never move `time.monotonic` hang forever. Jump the clock past `fence_timeout_s` **or** advance it inside the sleep fake.
5. **Unknown place (no `order_id`) left on the exchange.** US-009 will not cancel it. US-010 boot/timeout reconcile owns that. Document in learnings; do not scan all REST orders and cancel them (that would hit foreign/manual).
6. **Paper vs live user WS.** Overlay: private stream required in **live**. Book tests should use `paper` so they do not have to fake a user connect. Blindness-on-dead-connect tests use `live`.
7. **`reconcile_interval_s` is unused.** Blindness compares monotonic to the last `mark_reconcile_ok` stamp against `private_ws_blind_s`, not the reconcile interval. US-010 must actually call the stamp.
8. **Do not import `session_engine`.** `FreshnessWatchdog` would pull `Engine`. Copying the watchdog class is extra surface; `poll()` is enough.
9. **Do not add REST `get_orderbook` to the client.** Refresh = host resubscribe. Session only exposes `needs_resubscribe()`.
10. **Alert on recover is a fault-state change.** Implement it; the story says one alert per change, including back to healthy. If Telegram is too noisy later, that is a later product choice.
11. **`KalshiSession` is not in `WalletHost` yet.** Two daemons could still both quote Kalshi until US-013 takes the flock. Tests cover the state machine in isolation.
12. **User-stream parse fault** is account-level (cannot trust private state) even though the literal is `book_parse`. Prefer one `KalshiFault` set; encode scope from the stream, not a seventh string, unless a test needs a distinct label — then keep `book_parse` and cancel-all when `ticker is None`.
13. **Assumption:** `KalshiBookDelta.side` is `"yes"` / `"no"` (client tests). Unknown side → parse fault (kill generation), do not skip.
14. **Assumption:** best bid is max price with positive size. Do not use ask levels; the wire is bids-only.
15. **File size.** Keep one module. If it grows past ~600 lines, still do not split (no unrequested package). Do not “fix” that by stuffing logic into `kalshi_client.py`.

No Figma. No poly-maker patch. No new dependency.
