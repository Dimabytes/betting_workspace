# US-011 Implementation Plan

Story: **US-011** — «session.jsonl схема 6: venue-дискриминатор»

Repo: `/root/work/dota_2_model` on `main`. Bump `session.jsonl` to schema 6, stamp `venue` on PM tape rows, add Kalshi-named writers that never touch `market_p_radiant` / `market_radiant_prior`, and gate Kalshi fills on a durable `kalshi.db` row. Tests stay in `tests/test_live_paper_session_journal.py` (new cases) plus two small store getters in `tests/test_live_paper_kalshi_store.py`. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** implement dual `MatchWorker` trading / teardown Telegram leftover (US-013). Do **not** implement `kalshi_executor` (US-012). Do **not** implement smoke (US-014). Do **not** add a generic `Venue` protocol. Do **not** add `kind=kalshi_bind`. Do **not** `import kalshi` outside `kalshi_client.py`.

---

## Binding decisions (feature.json / overlay / US-001..010 learnings)

Cross-ref `current-task/feature.json` FR-13, FR-14 (Telegram leftover is US-013), US-011 `changes`, Resolved Questions (`tick_size_change` already exists; venue on signal/quote/fill/trading_error/session_end; `session_start` carries schema 6; no `kalshi_bind`; code+commits in `dota_2_model`), Non-Goals (no shared sqlite, no mix of venue numbers, no second match folder, Engine/PM untouched), and `docs/plans/kalshi-overlay.md` § Journal and alerts.

1. **Schema lives on `session_start`. Venue lives on the trade rows.** Fresh `write_start` writes `schema_version=6` with the same keys as schema 3/4/5 (`execution_mode` included). `session_start` does **not** grow a `venue` field. `tick_size_change` does **not** grow a `venue` field. New signal/quote/fill/trading_error/session_end rows always include `venue`. Schema 1–5 rows that omit `venue` read as `"polymarket"`.
2. **Resume does not rewrite provenance.** Same as 3/4 vs 5: a schema-5 (or 1–4) file keeps its first-line version. Later appends from this code stamp `venue`. Readers key off the **row**, not the start version. Do not mix Kalshi numbers into a PM row just because the file started at 5.
3. **PM writers stamp `venue="polymarket"` inside existing methods.** `MatchWorker.note_fill` / `write_quote` / `_journal.write_signal` / `FaultReporter` / `write_end` keep their signatures. Do not thread a `venue=` argument through the worker (that is US-013 dual-trade). Default is the method body, not an optional arg on a new API.
4. **Kalshi writers are new methods that do not accept `SignalDecision` / `OpenOrder` / `Fill`.** Those types carry PM `market_p_radiant` / `token_id`. Kalshi tape takes ticker + Decimal fields and serializes them as JSON strings. The PM keys `market_p_radiant` and `market_radiant_prior` are **absent** on Kalshi rows (not null, not zero, not the Kalshi mid).
5. **Kalshi fill is a view of `kalshi.db`, not of the WS object.** `write_kalshi_fill` looks up `fill_id` in sqlite, requires a stored `fee_cost`, and appends at most one tape row per `fill_id`. It does **not** call `ingest_fill` (US-010 already ingest on REST/WS). Duplicate WS/REST → `ingest_fill.applied=False` and a second `write_kalshi_fill` is a no-op. Crash between ingest and tape: a later `write_kalshi_fill(fill_id)` still writes, because the db row exists.
6. **Kalshi `session_end` is a second `kind=session_end` row, not a mutation of the PM cash block.** PM `write_end` stays USDC leftover/IMV/equity. Kalshi end is leftover YES/NO, ticker net cash, book inventory mark, exact fees, reconcile flags. Telegram leftover line is US-013; this story only persists the JSON. Do not sum USD and USDC in one field.
7. **No `kalshi_bind` row.** Bind is `match.json` + Telegram (US-005). Tests grep `session_journal.py` for `kalshi_bind` and assert writers never emit that kind.
8. **poly-maker is frozen.** Zero file changes there. No new dependency. No new module — writers stay in `session_journal.py`. Store getters stay in `kalshi_store.py`. Do not import `KalshiSession` from the journal (file is already ~974 lines; wiring is US-013).

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `SESSION_SCHEMA_VERSION` | `5`; accepted `{1,2,3,4,5}` | `6`; accepted `{1,2,3,4,5,6}` |
| `write_start` keys | schema 3/4/5 = `_SESSION_START_KEYS_V3` | schema 6 uses the same set. No `venue`. |
| `write_signal` / `write_quote` / `write_fill` / `write_error` / `write_end` | no `venue` | stamp `venue: "polymarket"` |
| `write_tick_change` | no venue | **Unchanged** (resolved: not in the venue set) |
| `scan_open_round` | every `kind=fill` must have PM `token_id` | skip `venue=kalshi`; missing venue = PM |
| `KalshiStore.ingest_fill` | idempotent on `fill_id` and `(trade_id, order_id)` | **Unchanged.** Journal follows `applied`/lookup, does not ingest. |
| `KalshiStore.fill(fill_id)` | private `_fill_id_exists` only | public row lookup for the tape |
| `KalshiStore.cash` / `fees_paid` | process-wide | unchanged. New **ticker-scoped** readers for the per-match tape |
| `KalshiSession` ingest | WS/REST → sqlite, no journal | **Unchanged** (US-013 owns the journal handle) |
| `MatchWorker` / `WalletHost` | PM tape only | **Unchanged** (US-013 calls Kalshi writers) |
| `kalshi_executor.py` | missing | **Still missing** (US-012) |
| `docs/live-paper.md` journal bullets | schema 5, accept 1–5 | fact-fix to 6 / 1–6 only. Full Kalshi section is US-015 |

Do not copy:

- poly-maker `RiskManager` / Engine journal.
- A generic `Venue` protocol / ABC.
- `SignalDecision` reused for Kalshi (it has `market_p_radiant`).
- `kind=kalshi_bind`.
- Telegram `notify_session_finished` Kalshi line (US-013).

---

## Requirements traceability (US-011 `changes`)

| Change | Plan |
|---|---|
| `SESSION_SCHEMA_VERSION` → 6; `ACCEPTED_SESSION_SCHEMA_VERSIONS` = 1–6 | Design § 1 |
| signal/quote/fill/trading_error/session_end carry `venue: polymarket \| kalshi`; schema 1–5 without `venue` read as polymarket | Design §§ 1–3, 8 |
| Kalshi rows use venue-named prior/mid/fair/order/fee; Kalshi numbers never land in `market_p_radiant` / `market_radiant_prior`; TypedDicts extended | Design §§ 2–4 |
| Kalshi fill on the tape only after exchange fill id + exact fee are durable in `kalshi.db`; WS/REST duplicate ≠ second tape row | Design § 5 |
| Kalshi `session_end` durably records leftover YES/NO, net cash, book inventory mark, fees, reconcile state; Telegram is a view | Design § 6 |
| No `kalshi_bind` record | Design § 7; tests grep |
| Autotests: resume 1–5 as PM; schema 6 keeps venue fields separate; fill only after durable db; durable session_end | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `SessionJournal.write` / `FsyncedJsonlWriter` / `sanitize_nonfinite` | All new rows still compact JSONL, write→flush→fsync. Convert `Decimal` to `str` **before** `write` (`json.dumps` cannot encode Decimal). |
| `write_start` / `first_start` / `_SESSION_START_KEYS_V3` | Extend the `{3,4,5}` set to `{3,4,5,6}`. Do not add keys. |
| `scan_open_round` | One extra skip: `record_venue(record) == "kalshi"`. Do not parse Kalshi `ticker` as a PM token. |
| `FaultReporter.report` → `write_error` | Stays PM. New `write_kalshi_error` is journal-only (US-009 already Telegram-alerts safe mode). |
| `KalshiStore.ingest_fill` / `_fill_id_exists` / `position` / `cash` / `fees_paid` / `latest_checkpoint` / `open_orders` | Fill tape reads sqlite. Do not ingest from the journal. |
| `KalshiFill` / `KalshiBookPair` field names | Tape field names: `kalshi_radiant_prior`, `kalshi_radiant_mid`, `kalshi_radiant_fair`, `yes_price`, `count`, `fee_cost`. Import `KalshiBookPair` only if a test builds one — the writer takes `Decimal` primitives so `session_journal` does not import `kalshi_session`. |
| `tests/test_live_paper_session_journal.py` helpers | `read_session_records`, `_write_fill`, `build_discovered`. Extend; do not fork a new test module unless the file would double. |
| `tests/test_live_paper_kalshi_store.py` `_fill()` / `_store(tmp_path)` | Reuse in journal tests via the public store API, or copy the `KalshiFill(...)` constructor locally. Do not import private test fns. |

Do not import `wallet_host`, `match_worker`, `Engine`, `StrategyCell`, `kalshi_observe`, `kalshi`. Do not open `paper.db`. Do not call `notify_session_finished`.

---

## Design

### 1. Schema bump (`session_journal.py`)

```python
SESSION_SCHEMA_VERSION = 6
ACCEPTED_SESSION_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6})
_START_WITH_EXECUTION_MODE = frozenset({3, 4, 5, 6})
```

Replace the two `{3, 4, 5}` branches in `first_start` with `_START_WITH_EXECUTION_MODE`. Docstring: schema 1/2 pin paper; 3–6 carry `execution_mode`; resume of 1–5 does not rewrite 6.

```python
SessionVenue = Literal["polymarket", "kalshi"]

def record_venue(record: dict[str, object]) -> SessionVenue:
    """Missing/null venue is polymarket (schema 1–5). Anything else is TradingDisabled at the call site that needs it."""
```

`record_venue` used by `scan_open_round` and tests. Unknown string (`"binance"`) → treat as corrupt fill tape in `scan_open_round` (same `TradingDisabled` as a foreign token). Writers only emit the two literals.

No `float`. No `dict[str, Any]`. New functions: all arguments required (AGENTS.md).

### 2. TypedDicts (`src/shared/types/live_paper.py`)

Keep existing PM shapes. Add `venue: NotRequired[Literal["polymarket"]]` on:

- `SessionSignalRecord`
- `SessionQuoteRecord`
- `SessionFillRecord`
- `SessionTradingErrorRecord`
- `SessionEndRecord`

`NotRequired` is the schema 1–5 on-disk shape. Schema 6 **writes** always include it.

Add Kalshi-only TypedDicts (JSON strings for every Decimal). They do **not** declare `market_p_radiant` / `market_radiant_prior` / `token_id`.

```python
class KalshiSessionSignalRecord(TypedDict):
    """Schema 6 Kalshi signal. Prior/mid/fair are Kalshi-named Decimal strings."""

    kind: Literal["signal"]
    venue: Literal["kalshi"]
    second: int
    ticker: str
    kalshi_yes_bid: str | None
    kalshi_yes_ask: str | None
    kalshi_yes_mid: str | None
    kalshi_no_bid: str | None
    kalshi_no_ask: str | None
    kalshi_no_mid: str | None
    kalshi_radiant_mid: str | None
    kalshi_radiant_prior: str | None
    kalshi_radiant_fair: str | None
    kalshi_yes_fair: str | None
    reason: str
    entry_block: str


class KalshiSessionPlacedQuote(TypedDict):
    """One Kalshi desired/resting line. Not a PM token_id."""

    client_order_id: str
    outcome_side: Literal["yes", "no"]
    book_side: Literal["bid", "ask"]
    yes_price: str
    count: str


class KalshiSessionQuoteRecord(TypedDict):
    kind: Literal["quote"]
    venue: Literal["kalshi"]
    ticker: str
    placed: list[KalshiSessionPlacedQuote]
    canceled: list[str]
    second: int


class KalshiSessionFillRecord(TypedDict):
    """One Kalshi fill copied off kalshi.db after ingest."""

    kind: Literal["fill"]
    venue: Literal["kalshi"]
    ticker: str
    fill_id: str
    trade_id: str
    order_id: str
    outcome_side: str
    book_side: str
    yes_price: str
    count: str
    fee_cost: str
    is_taker: bool
    position_after: str
    net_cash: str
    source: Literal["paper", "live"]
    second: int
    ts_utc: str


class KalshiReconcileState(TypedDict):
    """Durable Kalshi fence/boot snapshot. Telegram is a view of this."""

    boot_ready: bool
    fence_unproven: bool
    fault: str | None
    checkpoint_kind: Literal["boot", "periodic"] | None
    open_order_count: int


class KalshiSessionEndRecord(TypedDict):
    kind: Literal["session_end"]
    venue: Literal["kalshi"]
    ticker: str
    leftover_yes: str
    leftover_no: str
    net_cash: str
    inventory_value: str | None
    fees_paid: str
    reconcile: KalshiReconcileState
```

`SessionTradingErrorRecord` stays one type (same keys; `venue` discriminates). Extend the `SessionRecord` union with the Kalshi signal/quote/fill/end TypedDicts.

### 3. PM writers (default venue)

Inside the existing methods, after building the dict, set `"venue": "polymarket"`. Signatures unchanged.

`scan_open_round`: after `kind == "fill"`, if `record_venue(record) == "kalshi"`: `continue`. Then require `token_id` as today. A schema-6 PM fill still has `token_id` + `venue=polymarket`.

### 4. Kalshi writers (`session_journal.py`)

Frozen tape values (not TypedDicts) so the worker/US-013 does not pass a dict:

```python
@dataclass(frozen=True)
class KalshiSignalTape:
    """Kalshi-only signal inputs. Never carries market_p_radiant."""

    second: int
    ticker: str
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    yes_mid: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    no_mid: Decimal | None
    radiant_mid: Decimal | None
    radiant_prior: Decimal | None
    radiant_fair: Decimal | None
    yes_fair: Decimal | None
    reason: str
    entry_block: str


@dataclass(frozen=True)
class KalshiPlacedTape:
    """One Kalshi quote line."""

    client_order_id: str
    outcome_side: Literal["yes", "no"]
    book_side: Literal["bid", "ask"]
    yes_price: Decimal
    count: Decimal


@dataclass(frozen=True)
class KalshiSessionEndSnapshot:
    """Leftover YES/NO, ticker cash, book mark, fees, reconcile. Not PM equity."""

    ticker: str
    leftover_yes: Decimal
    leftover_no: Decimal
    net_cash: Decimal
    inventory_value: Decimal | None
    fees_paid: Decimal
    boot_ready: bool
    fence_unproven: bool
    fault: str | None
    checkpoint_kind: Literal["boot", "periodic"] | None
    open_order_count: int
```

```python
def _decimal_json(value: Decimal | None) -> str | None:
    """JSON string for a Kalshi Decimal, or null."""
```

`write_kalshi_signal(tape: KalshiSignalTape) -> None` — builds `KalshiSessionSignalRecord`. Assert in tests that `"market_p_radiant" not in row` and `"market_radiant_prior" not in row`.

`write_kalshi_quote(ticker: str, placed: tuple[KalshiPlacedTape, ...], canceled: tuple[str, ...], second: int) -> None` — no `fv_source`, no `OpenOrder`.

`write_kalshi_error(phase: str, error_type: str) -> None` — same body as `write_error` with `venue="kalshi"`. Do not change `FaultReporter` (PM phases + existing Telegram).

`write_kalshi_end(snapshot: KalshiSessionEndSnapshot) -> None` — one `kind=session_end` row. A file may contain a Kalshi end **and** a PM end. Readers (US-013 Telegram, later reports) filter by `venue`.

Helper for US-013 (and this story's tests). Lives next to the writers so `kalshi_session.py` does not grow:

```python
def kalshi_end_snapshot(
    store: KalshiStore,
    ticker: str,
    yes_mid: Decimal | None,
    no_mid: Decimal | None,
    boot_ready: bool,
    fence_unproven: bool,
    fault: str | None,
) -> KalshiSessionEndSnapshot:
    """Split signed YES position into leftover YES/NO; mark with book mids; ticker cash + fees."""
```

- `pos = store.position(ticker)` (paper+live leftover on this ticker; this is session-end inventory, not the REST live-only compare).
- `leftover_yes = pos if pos > 0 else 0`; `leftover_no = -pos if pos < 0 else 0`.
- `net_cash = store.cash_for_ticker(ticker)`.
- `fees_paid = store.fees_paid_for_ticker(ticker)`.
- `inventory_value = leftover_yes * yes_mid + leftover_no * no_mid` when **both** mids are not None; else `None` (unready book — same spirit as PM nonfinite → null cash block).
- `checkpoint_kind` from `store.latest_checkpoint()` or None; `open_order_count` = count of `store.open_orders()` with that ticker.

ponytail: signed YES cannot be long both legs; upgrade if Kalshi ever settles into two positive counts.

### 5. Kalshi fill after sqlite (`write_kalshi_fill`)

```python
def write_kalshi_fill(
    self,
    store: KalshiStore,
    fill_id: str,
    second: int,
    ts_utc: str,
) -> bool:
    """Append one Kalshi fill iff kalshi.db has fill_id+fee and the tape does not. True if written."""
```

Order:

1. `row = store.fill_row(fill_id)` → `None` means not durable → return `False`, write nothing.
2. `row.fee_cost` is `Decimal` (column is `NOT NULL`; ingest already rejected negative). No extra fee recompute.
3. If `self._kalshi_fill_ids()` contains `fill_id` → return `False`.
4. Append `KalshiSessionFillRecord` using sqlite fields + `store.position(row.ticker)` as `position_after` (latest, matching ingest ledger) and `store.cash_for_ticker(row.ticker)` as `net_cash`.
5. Add `fill_id` to the in-memory set. Return `True`.

`_kalshi_fill_ids()`: lazy `set[str]`. First call scans the jsonl for `kind=fill` and `record_venue==kalshi` and a nonempty `fill_id`. ponytail: O(n) per match file; upgrade to a sidecar index if the tape grows past one map.

Do **not** call `ingest_fill`. US-010 session/US-012 executor ingest first; this method is the tape. Tests: ingest then write → one row; write without ingest → zero rows; ingest once, `write_kalshi_fill` twice → one row; two `KalshiFill` objects with the same `fill_id` (WS then REST) → ingest second is `applied=False`, one tape row.

### 6. Store hooks (`kalshi_store.py`)

Keep the schema. No migration.

```python
@dataclass(frozen=True)
class KalshiFillRow:
    """One fills table row plus the position_ledger position_after for that fill_id."""

    fill_id: str
    trade_id: str
    order_id: str
    client_order_id: str | None
    ticker: str
    count: Decimal
    yes_price: Decimal
    fee_cost: Decimal
    outcome_side: str
    book_side: str
    is_taker: bool
    source: KalshiSource
    position_after: Decimal
```

```python
def fill_row(self, fill_id: str) -> KalshiFillRow | None:
    """Durable fill + post-fill position, or None if fill_id is not in kalshi.db."""

def cash_for_ticker(self, ticker: str) -> Decimal:
    """Sum of cash_delta for this ticker. Process-wide cash() is the running total."""

def fees_paid_for_ticker(self, ticker: str) -> Decimal:
    """Sum of fills.fee_cost for this ticker. Exact stored fees, not series params."""
```

`fill_row`: `SELECT fills.*, position_ledger.position_after FROM fills JOIN position_ledger USING (fill_id)`. Missing ledger (should not happen if ingest is atomic) → `None` (fail closed, no tape).

Do not change `cash()` / `fees_paid()` / `ingest_fill`.

### 7. No `kalshi_bind`

No new `kind`. `write_start` / observe path stay as US-005. Test: ` "kalshi_bind" not in Path("src/live_paper/session_journal.py").read_text()`.

### 8. What this story does not touch

- `MatchWorker._compute_decision` / second `predict_fair` / `note_fill` Kalshi branch (US-013)
- `WalletHost` boot flock / teardown fence order / `notify_session_finished` Kalshi line (US-013)
- `kalshi_session.py` ingest, boot, safe mode (already writes sqlite; do not pass a journal in)
- `kalshi_executor.py` (US-012)
- `kalshi_observe.py` (bind Telegram already exists; do not journal it)
- `session_quoting.py` (no journal writes today)
- Docker / full `docs/live-paper.md` Kalshi section (US-015). **Do** patch the two existing “schema_version 5 / accept 1–5” facts so they are not stale (AGENTS.md: update when a fact becomes wrong).
- `poly-maker`

### 9. Forbidden imports / calls

`session_journal.py` must not mention: `Engine`, `engine.gateway`, `StrategyCell`, `kalshi_session`, `import kalshi`, `kind=kalshi_bind`. It **may** import `KalshiStore` / `KalshiFillRow` from `kalshi_store` for `write_kalshi_fill` / `kalshi_end_snapshot`. Tests grep `kalshi_bind` and `market_p_radiant` assignment inside `write_kalshi_signal`.

---

## State machine (text, tape overlay)

```
        fresh archive
              |
              v
     write_start schema=6 (no venue)
              |
              +-- PM tick --> write_signal/quote/fill/error/end  venue=polymarket
              |
              +-- Kalshi (US-013 later)
                    ingest_fill(kalshi.db)     # US-010, not this story
                    write_kalshi_fill(fill_id) # no-op unless row exists and tape lacks id
                    write_kalshi_end(snapshot) # leftover/cash/mark/fees/reconcile
                    write_end(PM snapshot)     # unchanged USDC row

     resume schema 1–5
              |
              first_start accepts; does not rewrite line 1
              missing venue on old rows => polymarket
              new appends stamp venue
```

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — TypedDicts + schema constant + `first_start`

`live_paper.py` types. `SESSION_SCHEMA_VERSION = 6`. `_START_WITH_EXECUTION_MODE`. `record_venue`. Update the two tests that assert `== 5`. Add schema-5 resume-does-not-rewrite-6 (copy the schema-4 test).

### Step 2 — PM writers stamp `venue=polymarket`; `scan_open_round` skips Kalshi

Existing fill/signal/error tests keep passing with an extra key. Add: schema 1–5 fill without `venue` still opens a PM round; a Kalshi fill on the same file is ignored by `scan_open_round`.

### Step 3 — Store `fill_row` / `cash_for_ticker` / `fees_paid_for_ticker`

Tests in `test_live_paper_kalshi_store.py`: two tickers; process-wide `cash()` is the sum; ticker cash/fees isolate; missing `fill_id` → `None`.

### Step 4 — Kalshi writers + `kalshi_end_snapshot` + `write_kalshi_fill` gate

Decimal → str. Fill refuses before ingest. Duplicate `fill_id` refuses. `session_end` payload as in §4/§6.

### Step 5 — Tests

Cases below. `tmp_path` journal + `tmp_path` store. No live HTTP. No `WalletHost` / `MatchWorker`. `asyncio` not required.

### Step 6 — Quality gate

See Verification. `git add` new/edited files before `make lint-all`.

### Step 7 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: one tape must keep PM and Kalshi numbers in separate fields so a reader can never treat Kalshi mid as `market_p_radiant`; fills exist on the tape only after sqlite has the exchange id and fee.
- Set US-011 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (venue per row not per file; schema 1–5 default PM; Kalshi writers do not take `SignalDecision`; fill tape keyed by sqlite `fill_id`; two `session_end` rows; not wired to MatchWorker).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Fresh journal | `session_start.schema_version == 6`; no `venue` on that row |
| Resume schema 1/2 | paper mode; no rewrite; new fills have `venue=polymarket` |
| Resume schema 3/4/5 | `execution_mode` kept; first line unchanged |
| Schema 5 fill without `venue` | `record_venue` → polymarket; `scan_open_round` uses `token_id` |
| Kalshi fill on a PM round scan | skipped; PM sizes unchanged |
| Unknown `venue` on a fill | `TradingDisabled` (corrupt tape) |
| `write_kalshi_signal` | keys `market_p_radiant` / `market_radiant_prior` absent |
| Kalshi prior/mid equal to a PM number by chance | still only on `kalshi_*` keys |
| `write_kalshi_fill` before ingest | `False`; file unchanged |
| Ingest then write | `True`; `fee_cost` matches sqlite TEXT |
| Second write, same `fill_id` | `False`; still one tape row |
| WS fill then REST fill, same `fill_id` | store ingest once; tape once |
| Same trade, different `fill_id` colliding on `(trade_id, order_id)` | store `applied=False`; tape has the first id only |
| Crash: ingest persisted, journal not | new `SessionJournal` + `write_kalshi_fill` writes the missing row |
| `write_kalshi_end` with `yes_mid is None` | `inventory_value` JSON null; leftover/cash/fees still written |
| Long YES +5 | leftover_yes=`5`, leftover_no=`0` |
| Long NO (signed −3) | leftover_yes=`0`, leftover_no=`3` |
| Fees on ticker A, fill on B | `fees_paid` on B's end snapshot is B only |
| PM `write_end` after Kalshi end | two `session_end` rows; PM row has token positions, Kalshi row has ticker leftover |
| `tick_size_change` | no `venue` key |
| `FaultReporter.report` | still `venue=polymarket` |
| `kind=kalshi_bind` | never written |
| `MatchWorker` dual predict_fair | forbidden this story |
| Import `kalshi_session` from journal | forbidden |
| `place_event_order` / Engine halt | forbidden |

---

## Test plan

### Extend: `tests/test_live_paper_session_journal.py`

Helpers (add, do not fork a new test module):

- `_kalshi_store(tmp_path) -> KalshiStore` (close at end of test).
- `_ingest(store, fill_id="f1", ticker=..., fee=Decimal("0.02"))` using a local `KalshiFill` (copy constructor from the store test; do not import `_fill`).
- `_tape_keys(row) -> set[str]`.

| Test | Setup | Expect |
|---|---|---|
| **Fresh start is schema 6** | `write_start` | `schema_version==6`; no `venue` on start; `execution_mode` present |
| **Resume 1–5 as PM** | rewrite first line to 1,2,3,4,5 (pop `execution_mode` on 1–2); `first_start`; append `write_fill` | accepted; first line version unchanged; new fill `venue=="polymarket"`; `scan_open_round` works on a schema-5 fill **without** `venue` |
| Schema 5 resume does not rewrite 6 | copy schema-4 test with `= 5` | first line stays 5 |
| PM signal has venue, not Kalshi keys | existing `write_signal` path (build a tiny `SignalDecision` or write via journal method) | `venue=="polymarket"`; has `market_p_radiant` key (null ok) |
| **Schema 6 keeps venue fields separate** | `write_kalshi_signal` with prior `0.41` and mid `0.55`; also `write_signal` PM with different numbers | Kalshi row has `kalshi_radiant_prior=="0.41"`, **no** `market_p_radiant` / `market_radiant_prior`; PM row does not have `kalshi_radiant_prior` |
| Kalshi quote has no `token_id` | `write_kalshi_quote` | placed line has `client_order_id` / `yes_price` strings; no `token_id` |
| **Fill only after durable kalshi.db** | `write_kalshi_fill` before ingest | returns False; no `kind=fill` Kalshi row |
| Fill after ingest | ingest fee `0.02` then write | True; `fill_id`, `fee_cost=="0.02"`, `venue==kalshi` |
| **WS/REST duplicate** | ingest once; `write_kalshi_fill` twice; second ingest `applied=False` then write again | exactly one Kalshi fill row |
| Crash gap | ingest; close journal without writing; new `SessionJournal` same path; `write_kalshi_fill` | one tape row appears |
| `scan_open_round` ignores Kalshi fill | PM BUY then Kalshi fill with a non-token `ticker` | round still PM; no `TradingDisabled` |
| **Durable session_end** | ingest +5 YES; `kalshi_end_snapshot(..., yes_mid=0.4, no_mid=0.6, boot_ready=True, fence_unproven=False, fault=None)` then `write_kalshi_end` | leftover_yes `"5"`, leftover_no `"0"`, net_cash ticker-scoped, `inventory_value` = `5*0.4`, `fees_paid` from db, `reconcile.boot_ready` True, `fence_unproven` False |
| session_end null mark | mids `None` | `inventory_value` is JSON null; leftover still present |
| Two session_ends | Kalshi end then PM `write_end` | two `kind=session_end`; venues differ; PM `positions` is token map; Kalshi has `ticker` leftover |
| `write_kalshi_error` | | `venue==kalshi`; `FaultReporter` still polymarket |
| No `kalshi_bind` | grep source + write every Kalshi method | no such kind |
| Existing provenance / fsync / FaultReporter / open-round tests | | still pass (`venue` extra key on PM rows) |

Do not sleep real time. Do not hit Kalshi HTTP. Do not construct `WalletHost` / `MatchWorker`.

### Extend: `tests/test_live_paper_kalshi_store.py`

| Test | Expect |
|---|---|
| `fill_row` missing | `None` |
| `fill_row` after ingest | `fill_id`, `fee_cost`, `position_after` match ledger |
| two tickers cash/fees | `cash_for_ticker(A)` ignores B; `cash()` is still the process total |

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_session_journal.py tests/test_live_paper_kalshi_store.py

make test

git add src/live_paper/session_journal.py src/live_paper/kalshi_store.py \
  src/shared/types/live_paper.py tests/test_live_paper_session_journal.py \
  tests/test_live_paper_kalshi_store.py docs/live-paper.md
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. **Resume schemas 1–5 as PM** — missing `venue` reads polymarket; provenance version not rewritten; `scan_open_round` still works.
2. **Schema 6 keeps venue fields separate** — Kalshi prior/mid/fair only on `kalshi_*` keys; PM row keeps `market_p_radiant` / `market_radiant_prior`; Kalshi numbers never appear in those two keys.
3. **Fill only after durable `kalshi.db`** — no ingest → no tape; ingest then write → one row with stored `fee_cost`; duplicate write / duplicate ingest → still one row.
4. **Durable Kalshi `session_end`** — leftover YES/NO, ticker net cash, book mark (or null), fees, reconcile flags on a `venue=kalshi` `session_end` row.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **Kalshi fill / session_end writers land without US-013.** Yes. They are `SessionJournal` methods + `kalshi_end_snapshot(store, ...)`. Tests call them with `tmp_path`. `KalshiSession` keeps ingesting to sqlite only. Until US-013, live maps will have Kalshi fills in `kalshi.db` (once the executor exists) and **no** Kalshi tape rows — accepted. US-013 must call `write_kalshi_fill` after each successful ingest (and once on reconcile for the crash gap) and `write_kalshi_end` before/beside PM `write_end`.
2. **Two `session_end` rows.** Overlay wants a durable Kalshi leftover record and today's PM cash block. US-013 Telegram must pick `venue=kalshi` rather than “the last session_end”. Document in learnings.
3. **`json.dumps` and `Decimal`.** Must stringify before `write`. Forgetting this fails `make test` at dump time, not at Kalshi.
4. **Process-wide `cash()` vs per-match tape.** Using `cash()` on session_end would mix ticker A and B. `cash_for_ticker` is load-bearing.
5. **`scan_open_round` + Kalshi fills.** If the skip is missing, a Kalshi row without `token_id` disables PM trading (`TradingDisabled`). Test it.
6. **Resume schema vs row venue.** Provenance 5 + new `venue` keys is the same pattern as provenance 3 + `fill_key`. Do not bump the first line.
7. **Do not pass `SignalDecision` into Kalshi writers.** That type is how Kalshi mid would leak into `market_p_radiant`.
8. **Do not hook `KalshiSession.apply_user_event` to the journal.** The session has no `SessionJournal` and must not grow one here (1k-line file; host owns the match archive).
9. **WS `fill_id` vs REST `fill_id`.** US-006: not proven equal. Tape identity is whatever sqlite accepted as PK. Unique `(trade_id, order_id)` already collapses a REST/WS pair that shares those two. If they differ on all three keys, two db rows and two tape rows — fail closed, do not invent a merge.
10. **`docs/live-paper.md`.** Two bullets still say schema 5. Patch those facts. Do not write the US-015 Kalshi chapter.
11. **Assumption:** leftover YES/NO from a signed YES position is enough for v1 (one binary ticker per map).
12. **Assumption:** book inventory mark uses the last known YES/NO mids passed in by the caller. This story does not read `KalshiSession.book_pair` (no import). US-013 passes `book_pair.yes_mid` / `no_mid` or `None`.
13. **`session_journal.py` size.** ~431 lines today. Kalshi writers + snapshot helper will grow it. Do not split a package. Do not dump types into `kalshi_client.py`.

No Figma. No poly-maker patch. No new dependency.

## Implement-step notes

- `record_venue` is the only reader default. Writers never omit `venue` on the five kinds.
- `write_kalshi_fill` reads fee from sqlite, never from the in-memory `KalshiFill`, so a stale WS object cannot disagree with the ledger.
- US-013 contract: after `ingest_fill` (or on reconcile, for every `fill_row` of the match ticker), `journal.write_kalshi_fill(store, fill_id, second, ts_utc)`; on Steam-final, `write_kalshi_end(kalshi_end_snapshot(...))` then existing PM `write_end`. Telegram leftover line is a view of that snapshot, not a second calculation.
