# US-008 Implementation Plan

Story: **US-008** — «kalshi_store: sqlite intents/orders/fills/ledger»

Repo: `/root/work/dota_2_model` on `main`. Add `src/live_paper/kalshi_store.py` (durable Kalshi intents/orders/fills/position+cash ledger/checkpoints in a **separate** sqlite from the Polymarket wallet) and `tests/test_live_paper_kalshi_store.py`. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** add `kalshi_session` / `kalshi_executor` (US-009+). Do **not** wire the store into `WalletHost` (US-013). Do **not** bump `session.jsonl` (US-011). Do **not** `import kalshi` outside `kalshi_client.py`. Do **not** start auth WS. Do **not** touch `WalletStateStore` / `paper.db` / `live.db`.

---

## Binding decisions (feature.json / overlay / US-001..007 learnings)

Cross-ref `current-task/feature.json` FR-9, FR-10, US-008 `changes`, Resolved Questions («Paper-филлы Kalshi пишутся в kalshi.db той же схемой, что live, с venue-generated fill id»; «Один общий sqlite для двух венью» is a **non-goal**), Non-Goals, and `docs/plans/kalshi-overlay.md` §§ Dual trading (wallet row), Live ownership and crash reconciliation, Kalshi local safe mode (failed cancel), Journal (fill durable before tape).

1. **Separate file from PM, one Kalshi file for paper and live.** Path is `LIVE_PAPER_WALLET_DIR / "kalshi.db"` → `data/live_paper/wallet/kalshi.db`. PM stays on `paper.db` / `live.db`. Do not merge venues. Paper and live Kalshi share this **schema** and this **filename**. Tag every order/fill `source` as `"paper"` or `"live"` so US-010 REST compare can ignore paper rows (otherwise leftover paper positions look like unexplained live inventory).
2. **Flock a sibling lock file, not the db.** Story: exclusive flock on a **separate** lock file, mirroring `WalletLock` / `acquire_wallet_lock`. Path: `data/live_paper/wallet/kalshi.db.lock`. Do **not** flock `kalshi.db` itself (sqlite owns that file). Do **not** import `wallet_host` (circular once US-013 constructs the store from the host). Copy the ~15-line non-blocking `fcntl.LOCK_EX | LOCK_NB` helper into `kalshi_store.py` as `KalshiLock` / `KalshiLockHeld` / `acquire_kalshi_lock`. Store open does **not** take the flock; the host will (US-013), tests take it only in the flock case.
3. **Intent before the wire, always.** `write_place_intent` / `write_cancel_intent` persist first. The store never calls REST/WS. A later `apply_place_ack` / `apply_cancel_ack` / `apply_unknown_write` / `apply_place_reject` / `apply_cancel_fail` advances status. Failed cancel **never** `DELETE`s an orders row.
4. **Fill identity is `KalshiFill.fill_id`.** That is the exchange fill id (REST `fill_id`; WS mapper already falls back to `trade_id` — US-006). Idempotent ingest: PK `fills.fill_id`. Secondary unique `(trade_id, order_id)` so a WS row keyed by `trade_id` and a later REST row with a different `fill_id` but the same trade+order do not double-apply the ledger. Exact `fee_cost` is stored as given; never recompute from series fee params.
5. **Ledger moves only on a newly ingested fill.** Position and cash are append-only rows keyed by `fill_id`. Order `remaining_count` is **derived**: `initial_count - fill_count - canceled_count` with `fill_count = max(ack/update fill_count, sum(ingested fill counts))`. Out-of-order WS remaining/fill_count must not increase remaining or decrease fill_count, and must not apply a fill twice.
6. **Decimal in, TEXT out. No REAL, no float.** Prices, counts, fees, position, cash are `Decimal` on the Python API and canonical TEXT (`format(d, "f")`) in sqlite. Reject `float`. `created_unix` is `int`. Copy PM `fill_ledger` REAL columns and this story is wrong.
7. **Paper fills are `KalshiFill` with a caller-generated `fill_id`.** Same `ingest_fill`. No paper-only table. The store does not mint ids (US-012 will; tests use `paper-…` strings).
8. **This story is the store only.** No boot reconcile loop, no fence, no desired-order sync, no journal row, no Telegram, no `WalletHost` lock acquisition next to the PM flock.
9. **poly-maker is frozen.** Zero file changes there. No new dependency.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| PM wallet sqlite | `wallet_db_path` → `paper.db` / `live.db`; flock **on the db file**; `WalletStateStore` float REAL ledger | **Unchanged.** |
| `WalletLock` / `acquire_wallet_lock` | `fcntl` exclusive NB on the PM db path; `WalletLockHeld` | **Unchanged.** Kalshi copies the pattern onto `kalshi.db.lock`. |
| `KalshiFill` | `fill_id`, `trade_id`, `order_id`, `ticker`, `count`, `yes_price`, `fee_cost`, `outcome_side`, `book_side`, `client_order_id`, `is_taker` — all Decimal where numeric | The ingest input. Do not add fields. |
| `KalshiPlaceAck` / `KalshiCancelAck` / `KalshiRestingOrder` / `KalshiUnknownWrite` | Frozen client types | Ack/update/unknown inputs. |
| `PaperGateway._build_fill` | PM `trade_id=f"{order.order_id}:fill"` into **PM** `WalletStateStore` | **Unchanged.** Kalshi paper fills do not go there. |
| `WalletHost.open_wallet_host` | PM flock + Engine; public Kalshi REST | **Unchanged** (US-013 opens `KalshiStore` + Kalshi flock). |
| `session.jsonl` | schema 5 | **Unchanged** (US-011). |
| `kalshi.db` | missing | Created on first `KalshiStore(path)` with the schema below. |

Do not copy:

- `fill_outbox` / MATCHED→CONFIRMED / FAILED rebuild (CLOB settlement). Kalshi fill is final when ingested.
- `wallet_day` / daily kill (Kalshi non-goal).
- `OversizedSellError` / Engine halt. Exchange fills apply even if local position would go signed-negative; US-010 compares to REST.
- `WalletStateStore.apply_*` signatures.

---

## Requirements traceability (US-008 `changes`)

| Change | Plan |
|---|---|
| `kalshi_store.py`; `data/live_paper/wallet/kalshi.db`; tables intents, orders, fills, position/cash ledger, checkpoints | Design §§ 1–3 |
| Exclusive flock on a **separate** lock file, pattern of `WalletLock` / `acquire_wallet_lock` | Design § 4 |
| Intent written before network place/cancel; clear exchange response advances order state | Design § 5 |
| Idempotent fill ingest by exchange fill id with exact fee; dup WS/REST and out-of-order never second row / second ledger move | Design §§ 6–7 |
| Partial fills update remaining + position exactly; prices/counts Decimal/scaled-int (TEXT Decimal, no float) | Design §§ 3, 6, 7 |
| Failed cancel never deletes an order from `kalshi.db` | Design § 5 |
| Paper fills same schema, venue-generated fill id; no paper schema | Design § 8 |
| Autotests `tests/test_live_paper_kalshi_store.py`: duplicate + out-of-order fill, intent restore after restart, position+fee ledger, two-process flock | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `LIVE_PAPER_WALLET_DIR` | `KALSHI_DB_PATH = LIVE_PAPER_WALLET_DIR / "kalshi.db"`; lock sibling `.lock`. |
| `WalletLock` / `acquire_wallet_lock` | **Copy the flock mechanics**, do not import `wallet_host`. Same `path.parent.mkdir` + `touch` + `os.open` + `LOCK_EX\|LOCK_NB` + `BlockingIOError` → held. |
| `BUSY_TIMEOUT_MS = 30_000` | Duplicate the number in `kalshi_store` (do not import `wallet_store` — it pulls polymaker). `PRAGMA busy_timeout`. |
| `KalshiFill`, `KalshiPlaceAck`, `KalshiCancelAck`, `KalshiRestingOrder`, `KalshiUnknownWrite` | Only ingest types. Import from `kalshi_client`. Never `import kalshi`. |
| `sqlite3` + `sqlite3.Row` | stdlib. `executescript` schema, `with self._conn:` for one-commit apply. |
| `tests/test_live_paper_wallet_host.py::test_second_flock_on_wallet_db_raises` | In-process flock shape. Story also wants **two processes** — add a `subprocess` child (see test plan). |
| `tests/test_live_paper_kalshi_client.py::_assert_no_float` | Copy the helper into the store test (do not import a private test fn). |
| `format(decimal, "f")` / `Decimal(text)` | DB codec. |

Do not import `WalletStateStore`, `PaperGateway`, `fill_key`, `Engine`, `engine.gateway`. Do not open `paper.db`. Do not write `session.jsonl`.

---

## Design

### 1. Paths and types (`src/live_paper/kalshi_store.py`)

Thin module. One class, flock helpers, frozen row dataclasses. All function arguments required (AGENTS.md). `order_group_id: str | None` is a required argument that may be `None`.

```python
KALSHI_DB_NAME = "kalshi.db"
KALSHI_LOCK_NAME = "kalshi.db.lock"
BUSY_TIMEOUT_MS = 30_000


def kalshi_db_path() -> Path:
    """Process-wide Kalshi sqlite: data/live_paper/wallet/kalshi.db."""
    return LIVE_PAPER_WALLET_DIR / KALSHI_DB_NAME


def kalshi_lock_path() -> Path:
    """Sibling flock file. Not the sqlite file."""
    return LIVE_PAPER_WALLET_DIR / KALSHI_LOCK_NAME
```

`KalshiSource = Literal["paper", "live"]`.
`IntentKind = Literal["place", "cancel"]`.
`IntentStatus = Literal["pending", "acked", "unknown", "rejected"]`.
`OrderStatus = Literal["pending_place", "unknown", "resting", "pending_cancel", "canceled", "rejected", "filled"]`.

Frozen rows (no `dict[str, Any]`, no anonymous tuples):

- `KalshiIntentRow` — `intent_id`, `kind`, `client_order_id`, `order_id` (`str | None`), `ticker`, `book_side`, `outcome_side`, `yes_price`, `count`, `status`, `source`, `created_unix`
- `KalshiOrderRow` — `client_order_id`, `order_id` (`str | None`), `ticker`, `book_side`, `outcome_side`, `yes_price`, `initial_count`, `fill_count`, `canceled_count`, `remaining_count`, `status`, `order_group_id`, `subaccount`, `source`
- `KalshiCheckpoint` — `seq`, `kind` (`"boot"` \| `"periodic"`), `created_unix`, `open_order_count`, `fill_count`, `cash`
- `FillIngest` — `applied: bool` (True iff new fill row **and** ledger moved)

### 2. Schema DDL

`CREATE TABLE IF NOT EXISTS` only. No migrations. No JSON blobs.

```sql
CREATE TABLE IF NOT EXISTS intents (
    intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    order_id TEXT,
    ticker TEXT NOT NULL,
    book_side TEXT,
    outcome_side TEXT,
    yes_price TEXT,
    count TEXT,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_unix INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS intents_one_place
    ON intents(client_order_id) WHERE kind = 'place';

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    order_id TEXT UNIQUE,
    ticker TEXT NOT NULL,
    book_side TEXT NOT NULL,
    outcome_side TEXT NOT NULL,
    yes_price TEXT NOT NULL,
    initial_count TEXT NOT NULL,
    fill_count TEXT NOT NULL,
    canceled_count TEXT NOT NULL,
    remaining_count TEXT NOT NULL,
    status TEXT NOT NULL,
    order_group_id TEXT,
    subaccount INTEGER,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    client_order_id TEXT,
    ticker TEXT NOT NULL,
    count TEXT NOT NULL,
    yes_price TEXT NOT NULL,
    fee_cost TEXT NOT NULL,
    outcome_side TEXT NOT NULL,
    book_side TEXT NOT NULL,
    is_taker INTEGER NOT NULL,
    source TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fills_trade_order
    ON fills(trade_id, order_id);

CREATE TABLE IF NOT EXISTS position_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    delta TEXT NOT NULL,
    position_after TEXT NOT NULL,
    fee_cost TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    notional TEXT NOT NULL,
    fee_cost TEXT NOT NULL,
    cash_delta TEXT NOT NULL,
    cash_after TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    created_unix INTEGER NOT NULL,
    open_order_count INTEGER NOT NULL,
    fill_count INTEGER NOT NULL,
    cash TEXT NOT NULL
);
```

Codec: `_decimal_to_db(value: Decimal) -> str` = `format(value, "f")`; `_decimal_from_db(text: str) -> Decimal`. `TypeError` if the API value is not `Decimal`. Empty `fill_id` / non-positive `count` / negative `fee_cost` → `ValueError`, no write.

Do not add `fill_outbox`, `wallet_day`, `positions` (PM names). Derived position is the latest `position_ledger.position_after` per ticker (zero if none). Derived cash is the latest `cash_ledger.cash_after` (zero if none).

### 3. `KalshiStore`

```python
class KalshiStore:
    def __init__(self, db_path: Path) -> None:
        """Open (or create) kalshi.db, apply schema, busy_timeout=30s."""

    def close(self) -> None:
        """Close the sqlite connection."""
```

`__init__`: `db_path.parent.mkdir(parents=True, exist_ok=True)`, `sqlite3.connect(db_path)`, `row_factory=sqlite3.Row`, `PRAGMA busy_timeout=30000`, `executescript`. No WAL unless already default — one writer, flock is process-level. Default sqlite journal is enough.

Do not take the flock here.

### 4. Flock (separate lock file)

```python
class KalshiLockHeld(Exception):
    """Another process already holds the Kalshi sqlite lock file."""


class KalshiLock:
    """Exclusive flock on kalshi.db.lock, held for the process lifetime."""

    def close(self) -> None:
        """Unlock and close the fd."""


def acquire_kalshi_lock(path: Path) -> KalshiLock:
    """Non-blocking exclusive flock. Raises KalshiLockHeld if busy."""
```

Copy `acquire_wallet_lock` body. `path` is the **lock** path. Tests pass `tmp_path / "kalshi.db.lock"`. Production helper `kalshi_lock_path()` is `…/kalshi.db.lock`.

### 5. Intents and order lifecycle

**Place** (caller writes this, **then** `place_event_order`):

```python
def write_place_intent(
    self,
    client_order_id: str,
    ticker: str,
    book_side: Literal["bid", "ask"],
    outcome_side: Literal["yes", "no"],
    yes_price: Decimal,
    count: Decimal,
    subaccount: int,
    order_group_id: str | None,
    source: KalshiSource,
    created_unix: int,
) -> None:
```

One transaction: INSERT intent `kind=place` `status=pending`; INSERT orders row `status=pending_place`, `order_id=NULL`, `fill_count=0`, `canceled_count=0`, `remaining_count=initial`. Duplicate place `client_order_id`: no-op if the existing place intent is still `pending`/`unknown` with the same ticker/side/price/count; `ValueError` if payload differs. Do not hit the network.

```python
def apply_place_ack(self, ack: KalshiPlaceAck) -> None:
def apply_place_reject(self, client_order_id: str) -> None:
def apply_unknown_write(self, unknown: KalshiUnknownWrite) -> None:
```

- Ack: intent → `acked`; set `orders.order_id`; `fill_count = max(stored, ack.fill_count)`; recompute remaining; status `filled` if remaining 0 else `resting`. **Do not insert fills** (ack has no fill id / exact per-fill fee).
- Reject (clear 4xx, not unknown): intent → `rejected`; order status `rejected`; **keep the row**.
- Unknown (`KalshiUnknownWrite` after submit): intent → `unknown`; order status `unknown`; do not invent `order_id`; do not retry. US-010 scans REST.

**Cancel** (caller writes this, **then** `cancel_event_order`):

```python
def write_cancel_intent(
    self,
    client_order_id: str,
    order_id: str,
    ticker: str,
    source: KalshiSource,
    created_unix: int,
) -> None:
```

INSERT cancel intent `pending`; set order `pending_cancel` if it exists. Missing order: still insert the intent (boot may cancel a REST-only orphan after `apply_order_update` — if the row is still missing, intent alone is durable). Never DELETE.

```python
def apply_cancel_ack(self, ack: KalshiCancelAck) -> None:
def apply_cancel_fail(self, client_order_id: str, order_id: str) -> None:
```

- Ack: intent → `acked`; `canceled_count = max(stored, ack.reduced_by)`; recompute remaining; status `canceled` if remaining 0 else `resting`. Duplicate ack for an already-acked cancel intent: no-op.
- Fail: intent → `rejected`; order status **back** to `resting` if remaining > 0 else `filled`. **No DELETE. No remaining change.**

Status is monotonic except the explicit pending_cancel → resting/filled on fail:

`pending_place < unknown < resting < pending_cancel < {canceled, filled, rejected}`.

Never terminal → open. Never `DELETE FROM orders`.

```python
def apply_order_update(self, order: KalshiRestingOrder, source: KalshiSource) -> None:
```

Upsert by `client_order_id` (insert if boot REST shows a resting we already owned). Monotonic `fill_count`. Ignore a remaining that is **higher** than derived. Used by US-010; implement it here so out-of-order tests have a handle.

```python
def pending_intents(self) -> tuple[KalshiIntentRow, ...]:
def order_by_client_id(self, client_order_id: str) -> KalshiOrderRow | None:
def open_orders(self) -> tuple[KalshiOrderRow, ...]:  # non-terminal
```

Restart test: close, new `KalshiStore(same path)`, `pending_intents()` still has the place row, order still `pending_place`, ledgers empty.

### 6. Fill ingest (idempotency)

```python
def ingest_fill(self, fill: KalshiFill, source: KalshiSource) -> FillIngest:
```

One transaction:

1. Empty `fill_id` / `count <= 0` / `fee_cost < 0` → raise, no write.
2. `SELECT fill_id FROM fills WHERE fill_id=?` → already present: return `FillIngest(False)`.
3. `SELECT fill_id FROM fills WHERE trade_id=? AND order_id=?` → same trade on this order already stored (WS `fill_id=trade_id` then REST real `fill_id`): return `FillIngest(False)`. Do not update the PK.
4. INSERT `fills` (exact `fee_cost` TEXT).
5. If an orders row exists (`order_id` or `client_order_id`): `fill_count = max(stored, sum of this order's fill counts)`; recompute remaining; `filled` if remaining 0 else keep `resting`/`pending_cancel`.
6. If no orders row: still insert fill + ledger (REST backfill). Do not invent `initial_count`.
7. Append `position_ledger` + `cash_ledger` (see §7).
8. Commit. Return `FillIngest(True)`.

IntegrityError on either unique key → treat as duplicate, rollback that insert, `applied=False`. Same for a second call in the same process.

`source` on the fill row is `"paper"` or `"live"`. Do not store `"ws"` vs `"rest"` — both are live exchange fills; idempotency is the id, not the transport.

### 7. Position and cash math (signed YES contracts)

`yes_price` on `KalshiFill` is always the YES dollar price. `fee_cost` is always a positive cost, subtracted from cash. Never replace it with `ceil_cent(0.07·qty·p·(1-p))`.

Position delta (`delta`) by `(outcome_side, book_side)`:

| outcome | book | meaning | delta |
|---|---|---|---|
| yes | bid | buy YES | `+count` |
| yes | ask | sell YES | `-count` |
| no | bid | buy NO | `-count` |
| no | ask | sell NO | `+count` |

Notional (cash before fee):

| outcome | book | notional |
|---|---|---|
| yes | bid | `-(yes_price * count)` |
| yes | ask | `+(yes_price * count)` |
| no | bid | `-((1 - yes_price) * count)` |
| no | ask | `+((1 - yes_price) * count)` |

`cash_delta = notional - fee_cost`.

`position_after = previous(ticker) + delta`. `cash_after = previous_cash + cash_delta`.

```python
def position(self, ticker: str) -> Decimal:
def cash(self) -> Decimal:
def fees_paid(self) -> Decimal:  # sum of fills.fee_cost
```

Partial: two fills 3 then 2 on `initial=10` → remaining 5, position +5 (buy YES), two ledger rows, fees summed. Reverse ingest order must end at the same remaining/position/cash/fees.

Unknown `(outcome_side, book_side)` pair → `ValueError`, transaction aborted.

### 8. Paper fills (same schema)

`ingest_fill(fill, source="paper")` with `fill.fill_id` like `paper-a1b2-1` (caller-generated, nonempty, unique). `trade_id` may equal `fill_id`. Fee is whatever the caller put on `KalshiFill` (US-012 will simulate maker fee; this story just persists it). Assert the row lands in `fills` / both ledgers, not in `paper.db`.

`write_place_intent(..., source="paper")` is valid: paper still records intent before the simulated place so a restart can see the desired order. No second schema.

### 9. Checkpoints

```python
def write_checkpoint(self, kind: Literal["boot", "periodic"], created_unix: int) -> KalshiCheckpoint:
def latest_checkpoint(self) -> KalshiCheckpoint | None:
```

Snapshot `open_order_count`, `fill_count`, `cash()` inside the method (caller cannot pass a stale cash). US-010 writes boot/periodic; this story only persists/reads. Do not mark Kalshi ready (no session).

### 10. What this story does not touch

- `WalletHost` / `open_wallet_host` / PM `acquire_wallet_lock`
- `kalshi_session` / safe mode / fence / boot REST
- `kalshi_executor` / fee gate / paper book rules / V2 four-leg mapping
- `session.jsonl` schema 6 / fill tape
- `MatchWorker._compute_decision` / `predict_fair`
- `wallet_store.py` / `paper_gateway.py`
- Docker / `docs/live-paper.md` (US-015)
- `poly-maker`
- Generating `client_order_id` namespace (US-010)

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — `kalshi_store.py` skeleton

Paths, flock helpers, Decimal codec, schema `executescript`, `KalshiStore.__init__` / `close`.

### Step 2 — Intents + orders

`write_place_intent`, `apply_place_ack` / reject / unknown, `write_cancel_intent`, `apply_cancel_ack` / fail, `apply_order_update`, readers. Remaining derived. No DELETE.

### Step 3 — `ingest_fill` + ledgers

PK + `(trade_id, order_id)` unique, one transaction, position/cash math, `FillIngest`.

### Step 4 — Checkpoints

`write_checkpoint` / `latest_checkpoint`.

### Step 5 — Tests

`tests/test_live_paper_kalshi_store.py` as below. `tmp_path` dbs only. No live HTTP. No `import kalshi`. No pytest-asyncio.

### Step 6 — Quality gate

See Verification. `git add` new files before `make lint-all`.

### Step 7 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: Kalshi paper and live can share one durable schema so a restart and a duplicate WS/REST fill cannot move position or fees twice.
- Set US-008 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (separate lock file not the db; fill PK + `(trade_id, order_id)`; ledger-only-on-new-fill; remaining derived; TEXT Decimal; failed cancel keeps the row; paper `source=` same tables; store not wired to WalletHost).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Place intent, crash before ack, reopen | pending place intent + `pending_place` order; cash 0; no fills |
| Place ack with `fill_count>0` and no fill object | order fill_count advances; **ledger unchanged** until `ingest_fill` |
| Fill before place ack, `client_order_id` present | ingest + ledger; attach to the pending order; set `order_id` from the fill if still NULL |
| Fill with no local order | fill + ledger persist; no invented order row |
| Same `fill_id` twice (WS then REST) | second `applied=False`; one ledger seq |
| WS `fill_id=trade_id`, REST new `fill_id`, same `(trade_id, order_id)` | second `applied=False`; first row kept |
| Partial F2 then F1 (out of order) | both apply; remaining = initial − (F1+F2); position/cash/fees match in-order |
| Order update remaining=0 then fill of that size | fill applies once; remaining stays 0 (not negative) |
| Order update fill_count=0 after fills ingested | fill_count stays at the fill sum |
| `apply_cancel_fail` | order still in db; status resting/filled; remaining unchanged |
| Second `apply_cancel_ack` | no second `canceled_count` add |
| Duplicate `write_place_intent` same payload | no-op |
| Duplicate place id, different price | `ValueError` |
| Paper fill `fill_id="paper-…"` | same tables, `source=paper` |
| `fee_cost` 0.02 vs theoretical taker 0.02 at p=0.5 qty=1 | stored 0.02 regardless of formula |
| Buy NO at `yes_price=0.40` count=1 fee=0.01 | delta −1; notional −0.60; cash_delta −0.61 |
| `float` price/count/fee | `TypeError` |
| Empty `fill_id` | `ValueError` |
| Two processes on `kalshi.db.lock` | second `KalshiLockHeld` |
| Two `KalshiStore` on one tmp db without flock | allowed in tests (sqlite busy_timeout); daemon will flock |
| Opening `paper.db` / writing PM `fill_ledger` | never |
| `import kalshi` in the store | forbidden |
| WalletHost teardown order | not this story |

---

## Test plan

### New: `tests/test_live_paper_kalshi_store.py`

Helpers: `_store(tmp_path) -> KalshiStore`; `_fill(**overrides) -> KalshiFill` with Decimal strings; `_place_intent(store, **overrides)` wrapping required args. `source="live"` unless the paper test. Close the store at the end of each test (or try/finally).

| Test | Setup | Expect |
|---|---|---|
| **Duplicate fill** | ingest F once, ingest F again (same `fill_id`) | first `applied=True`; second `False`; one `fills` row; position/cash/fees unchanged on second |
| REST dup after WS same `fill_id` | ingest, ingest | same as above |
| **WS then REST different fill_id, same trade+order** | fill_id `t1` then fill_id `f-rest` with `trade_id=t1` | second `applied=False`; ledger once |
| **Out-of-order partials** | initial 10; ingest count 2 then count 3 (or reverse) | remaining 5; position +5 (buy YES); fees sum; same finals either order |
| Out-of-order **order update** then fill | `apply_order_update` fill_count=5 remaining=5, then ingest count=5 | one ledger apply; remaining 5 not 0-then-negative; fill_count 5 not 10 |
| Stale update after fills | ingest 5, then update fill_count=0 remaining=10 | remaining stays 5 |
| **Intent survives restart** | `write_place_intent`; `close`; new store on same path | `pending_intents()` length 1, status pending; order `pending_place`; `cash()==0`; no fills |
| Ack after restart | reopen, `apply_place_ack` | order_id set; status resting; still no ledger |
| **Position + fee ledger** | buy YES 10 @ 0.40 fee 0.01; sell YES 4 @ 0.50 fee 0; buy NO 2 @ yes 0.40 fee 0.01 | positions and cash as Design §7; `fees_paid()==0.02`; types `Decimal`; `_assert_no_float` on rows |
| Fee not recomputed | ingest fee_cost `0.03` at p=0.5 qty=1 | stored `0.03`, not 0.02 |
| **Failed cancel keeps order** | place+ack, `write_cancel_intent`, `apply_cancel_fail` | orders row still there; not canceled; remaining unchanged |
| Successful cancel then fail message | ack cancel reduced_by=remaining, then fail | stays canceled, still present |
| Paper fill same schema | `ingest_fill(..., source="paper")` with `fill_id="paper-ns-1"` | row in `fills` with that id; ledgers moved; `source=paper` |
| Place intent does not import/call REST | read `kalshi_store.py` | no `place_event_order`, `cancel_event_order`, `import kalshi`, `from kalshi` |
| No PM sqlite | read source | no `paper.db`, `live.db`, `WalletStateStore`, `fill_ledger` |
| **Two-process flock** | parent `acquire_kalshi_lock(lock)`; `subprocess` child same path | child exits 2 on `KalshiLockHeld`; after parent `close`, a new acquire succeeds |
| In-process flock | same as wallet host test | second `acquire_kalshi_lock` raises `KalshiLockHeld` |
| Default paths | `kalshi_db_path()` / `kalshi_lock_path()` | end with `wallet/kalshi.db` and `wallet/kalshi.db.lock`; lock ≠ db |
| Checkpoint | two fills, `write_checkpoint("boot", ts)` | `latest_checkpoint().fill_count==2`; cash matches `cash()` |

Subprocess flock (must be two OS processes, not two threads):

```python
proc = subprocess.run(
    [sys.executable, "-c", child_src],
    env=os.environ.copy(),  # inherits PYTHONPATH from `make test`
    capture_output=True,
    text=True,
    check=False,
)
assert proc.returncode == 2, proc.stderr
```

Child: import `acquire_kalshi_lock`, `KalshiLockHeld`; `try: acquire_kalshi_lock(Path(sys.argv[1])); except KalshiLockHeld: raise SystemExit(2)`.

Do not sleep. Do not hit Kalshi HTTP. Do not construct `WalletHost`.

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_kalshi_store.py

make test

git add src/live_paper/kalshi_store.py tests/test_live_paper_kalshi_store.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. Дубль и out-of-order филл — same `fill_id` (and WS/REST `trade_id+order_id` mismatch) does not move the ledger twice; partials ingested in reverse order match in-order remaining/position/fees; stale remaining/fill_count cannot undo fills.
2. Восстановление intent после рестарта — place intent + `pending_place` order still there on a new `KalshiStore` of the same file; no phantom fill.
3. Леджер позиций и комиссий — YES/NO buy/sell deltas and exact `fee_cost` cash, `Decimal` only.
4. Flock двух процессов — second OS process gets `KalshiLockHeld` on `kalshi.db.lock`.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **WS `fill_id` vs REST `fill_id`.** US-006: WS fill often has no `fill_id`; mapper uses `trade_id`. If those strings differ on the wire, PK-only ingest would double-apply. The `(trade_id, order_id)` unique index is the safety net. If production ever emits two real fills that share trade+order, drop that unique index — until then keep it.
2. **Place ack `fill_count` without fill objects.** Ledger waits for `ingest_fill`. Quoting that reads `remaining_count` may see a reduced remainder before position moves. Prefer that over inventing a fill id.
3. **Paper and live share one file.** `source` is mandatory so US-010 does not treat paper inventory as an unexplained live position. Flipping `KALSHI_TRADING` from paper to live on the VPS without ignoring `source=paper` (or wiping the file) is a US-010 footgun — document in learnings, do not add a second db name (story says `kalshi.db`).
4. **Do not import `wallet_host` for `WalletLock`.** US-013 will import the store from the host. Copy the flock.
5. **Do not flock `kalshi.db`.** Overlay says “acquire the kalshi.db flock”; the story overrides: separate lock file. Implement the story.
6. **NO notional is `1 - yes_price`.** Using `yes_price` for a NO buy would desync from Kalshi's signed position. Tests must include a NO leg.
7. **No outbox.** Crash between `ingest_fill` and US-011 journal is a later story. Returning `FillIngest.applied` is the hook.
8. **sqlite `Row` + pyright strict.** If execute/fetch noise fights basedpyright, a file-level `reportUnknownVariableType=false` (same as `wallet_store.py`) is acceptable. Do not weaken the Decimal API types.
9. **Assumption:** `fee_cost` on the wire is a non-negative Decimal dollar amount. Sub-cent fees are stored exactly as TEXT, not cents-rounded.
10. **Assumption:** one fill per `(trade_id, order_id)` for our account. Partial fills are separate trades.
11. **`WalletHost` still only flocks `paper.db`/`live.db`.** Two daemons could both open `kalshi.db` until US-013. Tests cover Kalshi flock in isolation.
12. **PEM / auth / WS.** Unused. A store test that constructs `open_kalshi_auth_client` is out of scope.

No Figma. No poly-maker patch. No new dependency.
