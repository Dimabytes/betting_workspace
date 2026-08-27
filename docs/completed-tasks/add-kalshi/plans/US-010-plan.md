# US-010 Implementation Plan

Story: **US-010** — «kalshi_session: boot-реконсиляция, ownership и периодический REST»

Repo: `/root/work/dota_2_model` on `main`. Extend `src/live_paper/kalshi_session.py` (boot REST reconcile, `client_order_id` namespace, periodic REST authority, unknown-write resolve) plus the smallest store/client hooks those paths need. Tests stay in `tests/test_live_paper_kalshi_session.py` (new cases) with one store helper test and one client 404 test. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** wire `WalletHost` / `open_wallet_host` / teardown (US-013). Do **not** implement `kalshi_executor` (US-012), `session.jsonl` venue (US-011), smoke (US-014). Do **not** add a generic `Venue` protocol. Do **not** `import kalshi` outside `kalshi_client.py`. Do **not** call Engine halt, `engine.gateway`, or `StrategyCell`. Do **not** market-flatten. Do **not** create Kalshi order-groups via `/portfolio/order_groups` (wrong tool: 15s contracts-limit buckets).

---

## Binding decisions (feature.json / overlay / US-001..009 learnings)

Cross-ref `current-task/feature.json` FR-9, FR-10, FR-12 (boot half only; teardown order is US-013), US-010 `changes`, Resolved Questions (paper fills share `kalshi.db`; code+commits in `dota_2_model`), Non-Goals (no shared sqlite, no economic RiskManager, no flatten, Engine/PM untouched), and `docs/plans/kalshi-overlay.md` §§ Live ownership and crash reconciliation, Kalshi local safe mode, Dual trading (wallet row).

1. **Session owns boot/reconcile. Host only calls it later.** `KalshiSession.boot()` / `reconcile()` / `resolve_unknown_write()` / `fence_strategy_orders()` are the US-013 API. This story does not construct a session from `WalletHost`, does not take the PM flock, does not start WS tasks, and does not await discovery. Tests never construct `WalletHost`.
2. **Flock is still the host's job.** `acquire_kalshi_lock` already exists on `kalshi_store`. `KalshiStore.__init__` must not take it. `boot()` must not take it (same-process second `LOCK_NB` on a new fd would look free on Linux and lie). Docstring: caller holds `KalshiLock` for the process lifetime. Boot tests do not acquire unless they are testing the lock itself (already covered in US-008).
3. **Not ready until boot checkpoint.** Today `__init__` stamps `_last_reconcile_at` and `_fence_proven=True`, so a snapshot can `can_publish_targets` with no REST. Add `_boot_ready: bool = False`. `can_publish_targets` / `write_allowed` are False until `boot()` writes a `kind="boot"` checkpoint and stamps `mark_reconcile_ok`. Existing book tests go through `_ready()`, which must `await boot()` on the empty fake REST and then `rest.calls.clear()` so US-009 call-order asserts stay valid.
4. **Ownership = subaccount + `d2m.` client_order_id prefix.** Plus `order_group_id` when the session has one. Manual/foreign REST rows are never cancelled and never `apply_order_update`'d (that insert would adopt them). Kalshi order-groups are a contracts-limit product; v1 does **not** `order_groups.create`. `_order_group_id: str | None = None`. `is_owned` still checks the field so US-012 can pass `session.order_group_id` (always `None` here).
5. **REST is authority. User WS is a low-latency ingest path.** `poll()` runs `reconcile()` on a timer. WS `KalshiFill` / `KalshiRestingOrder` ingest/update only when owned; a missed WS message is repaired by the next REST pass. Blind resend of place/cancel is forbidden (`place_event_order` still must not appear in `kalshi_session.py` except that **this story still must not call it** — executor places; session only resolves).
6. **Exchange fills are `source="live"`. Paper leftover is ignored in the REST compare.** `store.position(ticker)` mixes paper+live ledgers. Compare `position_from_source(ticker, "live")` to REST. Do not ingest REST/WS fills as `paper`. Do not invent a fill from place-ack `fill_count`.
7. **Store rows without `order_id` are not cancelled.** Resolve them with a `list_open_orders` scan by `client_order_id` first (no `client_order_id` query param on `orders.list_all`). Then cancel rows/REST orphans that have an `order_id`.
8. **poly-maker is frozen.** Zero file changes there. No new dependency. No new module unless the session file would otherwise grow a second unrelated type hierarchy — keep helpers in `kalshi_session.py` / `kalshi_store.py`.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `KalshiSession.__init__` | `_last_reconcile_at=now`, `_fence_proven=True`, `_reopen_needs_reconcile=False` → snapshot can publish | `_boot_ready=False` until `boot()` checkpoint. |
| Safe mode cancel | Store `open_orders()` with `order_id is not None` | **Unchanged** for book faults. Boot **adds** REST namespace orphans. |
| `mark_reconcile_ok` | Stamp only; nothing calls it on a timer | `poll` / `boot` / `reconcile` call it on success. |
| `apply_user_event` | Liveness only; does not ingest | Owned `KalshiFill` → `ingest_fill(..., "live")`. Owned `KalshiRestingOrder` → `apply_order_update(..., "live")`. |
| `KalshiStore.position` | Latest ledger row, paper+live | Unchanged. New `position_from_source`. |
| `apply_order_update` | Upsert by `client_order_id` (can insert) | Session calls it **only** for `is_owned`. |
| `list_open_orders` | `status="resting"`, all pages, **no** `client_order_id` filter | **Unchanged.** Session scans. |
| `get_order` | 404 → `KalshiRestError` (`KalshiNotFoundError` swallowed by `_await_read`) | `None` on 404. |
| `list_fills` / `list_positions` | All pages; positions are current market positions (settled live on `/settlements`) | Unchanged signatures. Session passes `min_ts` from checkpoint or `0`. |
| `WalletHost` | Public REST + observe; no Kalshi flock/session | **Unchanged** (US-013). |
| `kalshi_executor.py` | Missing | **Still missing** (US-012). |
| `[kalshi] reconcile_interval_s` | `20.0`; `private_ws_blind_s=15.0` | Do not edit TOML. `poll` uses `min(interval, blind)` so a healthy loop stamps before blindness. |

Do not copy:

- poly-maker `RiskManager` / flatten / day kill.
- `WalletHost` PM boot-scan (`BootScanTarget`) as the Kalshi loop.
- SDK `order_groups.create`.
- A second sqlite.

---

## Requirements traceability (US-010 `changes`)

| Change | Plan |
|---|---|
| Boot before discovery/quote: flock (host) → local snapshot → REST resting/fills/positions all pages → idempotent fill ingest with exact fees → cancel strategy-owned leftovers → prove absent → compare REST positions to live ledger → checkpoint → Kalshi ready | Design §§ 5–6 |
| Ownership: subaccount + deterministic `client_order_id` namespace (strategy, ticker, side, desired generation) + order-group id when available; no cancel of manual/foreign | Design §§ 2–3, 6 |
| Foreign / unexplained position delta: block Kalshi + alert; do not silently adopt | Design § 7 |
| Periodic REST (`reconcile_interval_s`) is authority; user WS is low-latency; REST repairs misses | Design §§ 8–9 |
| Timeout/disconnect after submit: REST by order/client identity; no blind resend | Design § 10 |
| Autotests: orphan resting cancelled and absent before ready; unexplained position → local halt; timeout place → exactly one order; restart between intent / REST ack / fill persist / cancel / fence | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `KalshiSession._cancel_owned` / `_fence` / `_enter_safe_mode` / `_owned_to_cancel` | Boot and `fence_strategy_orders` drain through the same cancel+fence. Do not copy a second fence loop. |
| `KalshiStore.open_orders` / `pending_intents` / `ingest_fill` / `apply_order_update` / `apply_place_ack` / `apply_unknown_write` / `apply_place_reject` / `write_cancel_intent` / `write_checkpoint` / `latest_checkpoint` | Boot/reconcile persistence. |
| `acquire_kalshi_lock` / `kalshi_lock_path` | US-013. Do not import `wallet_host`. |
| `KalshiRestClient.list_open_orders` / `list_fills` / `list_positions` / `get_order` / `cancel_event_order` | Only REST surface. No new list-by-client-id client method. |
| `KalshiUnknownWrite` | Input to `resolve_unknown_write`. Never retry the write. |
| `KalshiProfile.reconcile_interval_s` / `private_ws_blind_s` / `fence_timeout_s` | Injected profile. |
| `notify_in_background` | Unexplained-position uses the existing one-alert-per-label path (`_set_fault_label`). |
| `FENCE_POLL_SLEEP_S` + monotonic clock monkeypatch | Same as US-009. |
| `_FakeRest` in the session test | Grow `list_fills` / `list_positions` / `get_order` / `place_event_order` (place records + can raise `KalshiUnknownWrite`; default still `AssertionError` for book tests). |

Do not import `wallet_host`, `match_worker`, `session_engine`, `Engine`, `StrategyCell`, `kalshi_observe`, `kalshi`. Do not write `session.jsonl`. Do not open `paper.db`.

---

## Design

### 1. Types (`kalshi_session.py`)

Add one account-scoped fault. Do not add `foreign_position` as a second string — same halt.

```python
KalshiFault = Literal[
    "book_gap",
    "book_disconnect",
    "book_parse",
    "book_stale",
    "auth",
    "private_ws_blind",
    "fence_unproven",
    "unexplained_position",
]
```

`unexplained_position` is account-scoped (goes into `_ACCOUNT_FAULTS`). Alert: `live-paper kalshi: safe mode unexplained_position`. No secrets, no PEM, no order ids required.

Namespace constants (module-level, not a class hierarchy):

```python
CLIENT_ORDER_STRATEGY = "d2m"  # strategy token in the overlay namespace
```

```python
def kalshi_client_order_id(
    subaccount: int,
    ticker: str,
    book_side: BookSide,
    outcome_side: OutcomeSide,
    generation: int,
) -> str:
    """Deterministic idempotency key: d2m.{sub}.{outcome[0]}{book[0]}.{generation}.{ticker_key}."""
```

`ticker_key` is the raw ticker when the full id is `<= 64` chars, else `hashlib.blake2s(ticker.encode(), digest_size=8).hexdigest()` (ponytail: overlay wants ticker in the id; Kalshi ids are short — hash is the overflow path). Example: `d2m.0.yb.1.KXDOTA2MAP-1-AUR`.

```python
def is_kalshi_client_order_id(client_order_id: str, subaccount: int) -> bool:
    """True iff client_order_id starts with d2m.{subaccount}."""
```

Do not parse generation back out. Matching is prefix + optional order-group equality.

```python
def is_owned_resting(
    order: KalshiRestingOrder | KalshiOrderRow,
    subaccount: int,
    order_group_id: str | None,
) -> bool:
    """Strategy-owned: our subaccount (or row subaccount is None) and (prefix or matching group)."""
```

Subaccount on a REST row of `None` still counts if the list was requested with our subaccount (client already scoped the GET). If `row.subaccount` is an **int** and differs, not owned.

`KalshiSession.order_group_id` → `self._order_group_id` (`None` in v1). `next_client_order_id(ticker, book_side, outcome_side)` increments a per-ticker int starting at 1 and returns `kalshi_client_order_id(...)`. US-012 calls this before `write_place_intent`. This story's timeout test uses it.

No `float`. No `dict[str, Any]`. All new function arguments required (AGENTS.md).

### 2. Store hooks (`kalshi_store.py`)

Keep the schema. No migration. No `source` column on `position_ledger`.

```python
def position_from_source(self, ticker: str, source: KalshiSource) -> Decimal:
    """Signed YES position from fills of this source only. Paper leftover does not count."""

def live_positions(self) -> dict[str, Decimal]:
    """ticker → position_from_source(ticker, 'live') for every ticker that has a live fill."""

def order_by_order_id(self, order_id: str) -> KalshiOrderRow | None:
    """Public wrapper around _order_by_exchange_id."""
```

`position_from_source`: `SELECT outcome_side, book_side, count FROM fills WHERE ticker=? AND source=?`, sum `_position_delta`. Empty → `0`. Do not use `position_ledger.position_after` (it mixed sources on ingest).

Do not add `list_fills` on the store. Session already has REST fills.

### 3. Client hook (`kalshi_client.py`)

One behavior change:

```python
async def get_order(self, order_id: str) -> KalshiRestingOrder | None:
    """One order by exchange id. 404 is None (not found), not KalshiRestError."""
```

Inside the `fetch` coroutine, `except KalshiNotFoundError: return None` **before** `_await_read` turns every `KalshiError` into `KalshiRestError`. Import `KalshiNotFoundError` next to the other `kalshi.errors`. Do **not** add `client_order_id=` to `list_all`. Do **not** add `settlement_status` to `list_positions` (that query exists on FCM, not `GET /portfolio/positions`; current positions **are** the unsettled book — settled rows live on `/portfolio/settlements`, which this story does not call).

### 4. Session flags and publish gates

On init (in addition to US-009):

- `_boot_ready = False`
- `_order_group_id: str | None = None`
- `_desired_generation: dict[str, int] = {}`
- `_reconcile_running = False` (do not overlap boot/periodic/unknown-resolve)

`can_publish_targets`: require `_boot_ready` (in addition to today's checks).

`write_allowed`: require `_boot_ready`. Cancel/reconcile/boot paths do not go through `write_allowed`.

A snapshot without `boot()`: `book_pair` may still compute (US-009 book math tests); `can_publish_targets` stays False. That is the "before discovery and any quote" gate.

### 5. `boot()` sequence (strict order)

```python
async def boot(self) -> None:
    """REST-reconcile this process before any Kalshi quote. Caller holds KalshiLock."""
```

Idempotent: if `_boot_ready`, return. If `_fence_unproven`, return (cannot become ready).

Recordable via fake REST call log. **No** `place_event_order`.

1. **Local snapshot.** `pending_intents()`, `open_orders()`, `live_positions()`, `latest_checkpoint()`. Do not write yet.
2. **REST, all pages** (already whole-list in the client):
   - `resting = await rest.list_open_orders(subaccount)`
   - `min_ts = 0` if no checkpoint else `checkpoint.created_unix` (ponytail: dedicated subaccount; first boot is empty. Upgrade: bounded lookback if the subaccount ages.)
   - `fills = await rest.list_fills(subaccount, min_ts)`
   - `positions = await rest.list_positions(subaccount)`
   - Auth → `_enter_safe_mode("auth", None)`, leave `_boot_ready` False.
   - Other REST errors → `_enter_safe_mode("private_ws_blind", None)`, not ready.
3. **Resolve local pending/unknown place intents** (no resend): for each place intent in `pending`/`unknown`, `_bind_from_rest(client_order_id, resting, fills)`. See §10. Cancel intents stay until step 5.
4. **Bind owned REST rests.** For each `resting` row with `is_owned_resting(...)`: `apply_order_update(row, "live")`. If a matching place intent is still open, `apply_place_ack` from the REST counts (**do not** `ingest_fill` from `fill_count`).
5. **Ingest attributable fills.** For each REST fill, if `_fill_is_ours(fill)`: `ingest_fill(fill, "live")`. Exact `fee_cost`. Duplicates are `applied=False`. **Do not** ingest unattributed fills (that would silently adopt a foreign trade into the ledger).
   `_fill_is_ours`: `fill.client_order_id` matches prefix, **or** `order_by_order_id(fill.order_id)` is not None, **or** `order_by_client_id` hits. REST fills often have `client_order_id=None` (US-006) — that is why step 4 must run first.
6. **Cancel strategy-owned leftovers of the previous process.** Union of:
   - store `open_orders()` with `order_id is not None` (US-009 rule; skip `order_id is None`)
   - REST resting rows that `is_owned_resting` (namespace orphans never written to sqlite)
   For REST-only orphans: `apply_order_update` first so cancel has a store row, then `_cancel_owned`. Foreign REST rows: skip. Then `_fence` on the captured id set. Unproven → `_fence_unproven`, not ready, return.
7. **Compare positions.** See §7. Mismatch → `_enter_safe_mode("unexplained_position", None)`, not ready, **do not** `apply_order_update` foreign rows, **do not** ingest the leftover fills to make the numbers match, **do not** write a ready checkpoint.
8. **Checkpoint + ready.** `write_checkpoint("boot", int(time.time()))`. `_boot_ready = True`. `mark_reconcile_ok()`. Do not start a book generation here (host/`start_book` still owns that).

Paper and live run this same sequence. Paper with a read-only key that 403s cancel → existing `KalshiAuthFault` path (account `auth`, not ready). Tests use fake REST that can cancel.

### 6. Cancel scope: boot vs book-fault vs foreign

| Path | Cancel |
|---|---|
| Book gap/stale/disconnect/parse (US-009) | Store rows with `order_id` on that ticker |
| Auth / private blind (US-009) | All store rows with `order_id` |
| **Boot / `fence_strategy_orders`** | Store rows with `order_id` **plus** REST rows matching the namespace (any ticker) |
| REST row, prefix not `d2m.{sub}.`, group not ours | **Never** |

```python
async def fence_strategy_orders(self) -> bool:
    """Cancel strategy-owned resting and REST-fence. True if proven. US-013 teardown hook."""
```

Reuse `_cancel_owned` + `_fence`. Do not set `_boot_ready`. Do not flatten. Returns False on timeout (`_fence_unproven` already latches). US-013 still runs PM `cancel_all` after this returns either way — that wiring is not this story.

### 7. Position compare (no silent adopt)

Build `rest_map: dict[str, Decimal]` from `list_positions`, treating `position == 0` as absent (skip). Local map = `live_positions()`.

Halt (`unexplained_position`) iff there exists a ticker where `rest_map.get(ticker, 0) != 0` and `rest_map[ticker] != local.get(ticker, 0)`.

That covers:

- REST +5, live ledger 0 → **foreign or missing fills → halt** (the required test).
- REST +5, live +3 → unexplained delta → halt.
- REST +5, live +5 → OK.

Do **not** halt when REST is 0/absent and live ledger is non-zero. That is leftover after settlement (exchange drops the market from `/portfolio/positions`) **or** `source=paper` leftover already excluded by `live_positions`. Do not zero the ledger. Do not sell. ponytail: fail closed only on inventory the exchange still reports.

Exact `Decimal` equality. No epsilon.

### 8. Periodic REST (`poll`)

After the existing book-age / blindness work, if `_boot_ready` and not `_fence_unproven` and not `_safe_mode_running`:

```python
interval = min(self._profile.reconcile_interval_s, self._profile.private_ws_blind_s)
if now - self._last_reconcile_at >= interval:
    await self.reconcile()
```

ponytail: TOML ships `reconcile_interval_s=20` and `private_ws_blind_s=15`. Stamping only every 20s would trip blindness at 15s on a healthy loop. Do not edit `dota-map.toml`. Upgrade: raise the TOML interval once operators pick a real pair.

```python
async def reconcile(self) -> None:
    """REST authority pass: ingest owned fills/orders, compare positions, stamp or halt."""
```

Same REST trio as boot (fills `min_ts` from latest checkpoint). Bind owned rests. Ingest attributable fills. Position compare. On match: `write_checkpoint("periodic", ...)` + `mark_reconcile_ok()`. On mismatch: `_enter_safe_mode("unexplained_position", None)` (clears desired, blocks writes, cancels owned, fences). On REST/auth error: existing blind/auth safe mode.

**Do not cancel all owned rests on a healthy periodic pass** — that would cancel live quotes every 15s. Cancel of owned rests is boot, safe mode, and `fence_strategy_orders` only.

If `_reconcile_running`, skip (boot in flight). Keep it a bool, not a new asyncio.Lock around the event loop.

### 9. User WS (low latency)

`apply_user_event`:

| Event | Extra on top of US-009 |
|---|---|
| `KalshiFill` | If `_fill_is_ours`, `ingest_fill(fill, "live")`. Still sets `_user_ws_up`. |
| `KalshiRestingOrder` | If `is_owned_resting`, `apply_order_update(order, "live")`. |
| disconnect / auth / malformed | Unchanged (account safe mode). |

Unattributed WS fill: ignore ingest (REST will either attribute after binding an order id, or the position compare will halt). Do not reset book-age.

### 10. Unknown write after submit (no blind resend)

```python
async def resolve_unknown_write(self, unknown: KalshiUnknownWrite) -> None:
    """REST-resolve a place/cancel whose wire result was unknown. Never retries the write."""
```

Caller (US-012, and this story's tests) already persisted intent + `apply_unknown_write`. This method only reads REST.

**Place (`unknown.kind == "place"`, `order_id` is None):**

1. `resting = await list_open_orders(subaccount)`. Scan for `row.client_order_id == unknown.client_order_id`. **Do not** pass `client_order_id` into `list_all`.
2. Hit → `apply_order_update` + `apply_place_ack` from the row. Stop. Still exactly one exchange order.
3. Miss + `unknown.order_id` is not None → `get_order(order_id)` (`None` = 404). Resting/executed with our cid → bind as above.
4. Miss → `list_fills` since last checkpoint (or 0); if a fill's `client_order_id` matches, ingest + bind order_id from the fill, stop.
5. Still miss: **leave** intent/order `unknown`. Do **not** `apply_place_reject` on the first miss (the order may not be in the list yet). Do **not** call `place_event_order`. The next `reconcile()` retries the scan. If it later appears as an owned rest after we already quoted a new generation, boot/safe-mode cancel still owns leftovers; same-process late land is a documented risk for US-012 (must not bump generation until this returns a bound order or a later reconcile still shows absent **and** the operator/executor chooses a new id).

For the required test "timeout place → exactly one order": fake `place_event_order` raises `KalshiUnknownWrite` after recording one create; `list_open_orders` then returns that cid; `resolve_unknown_write` binds; create count stays 1.

**Cancel (`unknown.kind == "cancel"`):**

1. If `unknown.order_id` is set: `get_order` → `None` means gone → `apply_cancel_ack` with `reduced_by` = stored remaining (or `0` if no row). Present → leave `pending_cancel`/`unknown`; fence/reconcile will see it.
2. Else scan `list_open_orders` for the cid; absent → ack as cancelled; present → leave unknown.

Never `cancel_event_order` a second time from this method (US-008: unknown cancel must be REST-resolved without a second write). Safe-mode/boot cancel is a **new** intent, allowed.

### 11. What this story does not touch

- `WalletHost` / `open_wallet_host` / `teardown` / PM flock
- `kalshi_executor.py` / fee gate / V2 four-leg / paper book-touch fills
- `MatchWorker._compute_decision` / `predict_fair`
- `session.jsonl` schema 6
- `kalshi_observe.py` / matcher / prior
- Docker / `docs/live-paper.md` (US-015)
- `poly-maker`
- Creating or listing Kalshi order groups
- `GET /portfolio/settlements`

### 12. Forbidden imports / calls

`kalshi_session.py` must not mention: `Engine`, `engine.gateway`, `StrategyCell`, `Regime`, `polymaker`, `halt`, `place_event_order`. Tests grep the source (extend the US-009 grep). Session still does not place — tests that need a timeout place call `rest.place_event_order` themselves (or a tiny test helper), then `resolve_unknown_write`.

---

## State machine (text, boot overlay on US-009)

```
        KalshiSession()
                |
                v
        +------------------+
        | boot_pending     |  _boot_ready=False; can_publish=False
        | (no quotes)      |
        +--------+---------+
                 | boot()
                 |
      REST + ingest + cancel owned leftovers + fence
                 |
         +-------+--------+
         |                |
      proven           unproven / unexplained / auth
         |                |
         v                v
  +--------------+   +----------------------+
  | boot_ready   |   | blocked (safe mode)  |
  | then US-009  |   | unexplained_position |
  | book gens    |   | or fence_unproven    |
  +------+-------+   +----------------------+
         |
         | poll: min(reconcile_interval_s, private_ws_blind_s)
         v
  reconcile() stamps OR unexplained halt
```

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Store `position_from_source` / `live_positions` / `order_by_order_id`

Tests in `tests/test_live_paper_kalshi_store.py`: paper fill + live fill on the same ticker; `position()` is the mix; `position_from_source(..., "live")` ignores paper.

### Step 2 — Client `get_order` 404 → `None`

`tests/test_live_paper_kalshi_client.py`: fake `orders.get` raises `KalshiNotFoundError`; wrapper returns `None`; other `KalshiError` still `KalshiRestError`. No live HTTP.

### Step 3 — Namespace helpers + session gates

`kalshi_client_order_id` / `is_kalshi_client_order_id` / `is_owned_resting`. `_boot_ready`. `can_publish_targets` / `write_allowed` require it. `_ready()` in the session test: `await session.boot()` then `rest.calls.clear()` then start_book+snapshot.

### Step 4 — `boot()` / `reconcile()` / `fence_strategy_orders`

Strict order in §5. Reuse `_cancel_owned` + `_fence`. Grow `_FakeRest`. Wire `poll` timer with `min(interval, blind)`.

### Step 5 — WS ingest + `resolve_unknown_write`

`apply_user_event` ingest. Unknown place scans `list_open_orders`. No `place_event_order` in the session module.

### Step 6 — Tests

Cases below. `tmp_path` store. Fake REST. No live HTTP. `asyncio.run`, no pytest-asyncio. Existing US-009 tests must still pass after `_ready` boots.

### Step 7 — Quality gate

See Verification. `git add` new/edited files before `make lint-all`.

### Step 8 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: a crash must cancel our Kalshi leftovers and refuse to quote on a foreign/unexplained position; REST is the authority so a dropped user-WS message cannot desync the ledger.
- Set US-010 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (namespace prefix; no `list_all(client_order_id=)`; paper leftover ignored; REST fill attribution via order_id after bind; `min(interval, blind)`; flock not taken inside `boot`; not wired to WalletHost).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| Snapshot before `boot()` | `book_pair` may exist; `can_publish` False |
| Empty exchange, empty store | `boot` checkpoints and `_boot_ready` True |
| REST owned orphan (our prefix, not in store) | `apply_order_update`, cancel, fence empty, then ready |
| REST foreign rest (`manual-1`) | Not cancelled, not inserted; ready if positions match |
| REST +5 live ledger 0 | `unexplained_position`; not ready; no ledger write of those shares |
| Paper fill +5, REST 0 | `live_positions` 0; boot ready |
| Store open row, `order_id is None` | Not cancelled; scanned by cid; if still missing, left local |
| Place ack `fill_count=3` during bind | Counts on the order row only; ledger waits for `ingest_fill` |
| Duplicate REST/WS fill | `ingest_fill` False; ledger once |
| `list_open_orders` during boot always returns our id | Fence timeout; `_fence_unproven`; never `_boot_ready` |
| Second `boot()` | No-op |
| Periodic while a quote is resting | Ingest/compare/stamp; **no** cancel of that rest |
| User WS fill, ours | Ingest live; periodic REST is still the stamp |
| User WS fill, foreign cid | No ingest |
| `KalshiUnknownWrite` place, then list shows cid | Bind; create count stays 1 |
| `KalshiUnknownWrite` place, list empty | Stay `unknown`; no second place |
| `get_order` 404 on unknown cancel | Treat as cancelled |
| Restart: pending place intent, REST has cid | New `KalshiStore` same path; `boot` binds; no place call |
| Restart: fill in sqlite, REST same fill_id | Ingest False; positions match; ready |
| Restart: `pending_cancel` + REST still resting | Boot cancel+fence again (new process; `_fence_unproven` is in-memory) |
| Restart: crash after cancel ack, before fence proof | REST list empty; fence proven immediately; ready |
| Two tickers, foreign position on B | Account halt; A also not publish |
| `place_event_order` from session module | Forbidden |
| Import `Engine` / `kalshi` in session | Forbidden |

---

## Test plan

### Extend: `tests/test_live_paper_kalshi_session.py`

Helpers (add, do not fork a new test module):

- `_owned_cid(ticker, generation=1, book="bid", outcome="yes")` → `kalshi_client_order_id(0, ...)`.
- `_FakeRest`: `fills`, `positions`, `get_map` / `get_error`; `list_fills` / `list_positions` / `get_order`; `place_calls` + `place_error`. Default `place_event_order` still raises `AssertionError` so US-009 book tests keep the grep. Timeout test assigns a place impl that appends `place_event_order` and raises `KalshiUnknownWrite`.
- `_ready`: `asyncio.run(session.boot())`; `rest.calls.clear()`; then start_book+snapshot.
- One test that snapshots **without** boot and asserts `can_publish` False.

| Test | Setup | Expect |
|---|---|---|
| **Unready until boot** | `start_book` + snapshot, no `boot` | `can_publish` False |
| Empty boot then publish | Default fake; `_ready` | `_boot_ready`; checkpoint `kind=boot`; `can_publish` True |
| **Orphan resting cancelled before ready** | Fake `list_open_orders` returns owned cid+oid, then empty after cancel | `cancel_event_order` once for that oid; `list_open_orders` after cancel does not contain oid **before** `_boot_ready`; foreign cid in the same list never cancelled |
| Foreign rest left alone | REST `manual-1` + owned empty | `cancel_event_order` not called; boot ready |
| **Unexplained position → local halt** | REST `KalshiPosition(ticker, 5)`; live ledger 0 | fault `unexplained_position`; `can_publish` False after later snapshot; `store.position_from_source` still 0 (not adopted); one alert; no `place_event_order` |
| Paper leftover ignored | `ingest_fill(..., "paper")` +5; REST positions empty | boot ready; live position 0 |
| Matching live inventory | live fill +5; REST +5; no resting | boot ready; no cancel |
| **Timeout place → one order** | Test calls `write_place_intent` + fake place once (Unknown) + list returns that cid; `resolve_unknown_write` | `place_calls == 1`; store has `order_id`; status resting; a second `resolve` does not place |
| Unknown place, list empty | Same intent; list `()` | status stays `unknown`; `place_calls == 1` |
| **Restart: intent then REST** | Write pending place; `store.close()`; new store+session; REST shows cid | `boot` binds `order_id`; fake `place_event_order` never called |
| **Restart: fill persisted** | `ingest_fill` live; new session; REST same fill_id + matching position | ingest not doubled; cash/position unchanged; ready |
| **Restart: pending_cancel** | Resting in store; cancel intent; REST still lists oid; new session `boot` | one cancel in the **new** session; then fence empty → ready |
| **Restart: fence window** | REST owned oid never leaves (always_open) | `_fence_unproven`; not ready |
| Periodic repairs missed WS | `_ready`; REST later lists a new owned fill; jump monotonic by `min(interval,blind)`; `poll` | fill ingested; `mark_reconcile_ok` stamped; `can_publish` still True |
| Periodic unexplained | `_ready`; then REST position +9 vs ledger 0; `poll` | safe mode `unexplained_position`; desired cleared |
| WS fill ingest | `_ready`; `apply_user_event(owned fill)` | ledger moves once; duplicate REST same `fill_id` does not |
| `next_client_order_id` prefix | two calls | ids start with `d2m.0.`; generations differ; `is_owned_resting` True; `manual-1` False |
| US-009 regressions | existing gap/stale/blind/unproven | still pass (`_ready` boots+clears calls so `calls[0]==cancel` stays true) |
| No Engine | Read `kalshi_session.py` | No `Engine`, `gateway`, `StrategyCell`, `halt`, `import kalshi`, `place_event_order` |

Do not sleep real time. Do not hit Kalshi HTTP. Do not construct `WalletHost` / `MatchWorker`.

### Extend: `tests/test_live_paper_kalshi_store.py`

| Test | Expect |
|---|---|
| paper + live fills on one ticker | `position()` is the sum; `position_from_source(ticker, "live")` equals the live delta only |

### Extend: `tests/test_live_paper_kalshi_client.py`

| Test | Expect |
|---|---|
| `get_order` 404 | `KalshiNotFoundError` from fake `orders.get` → `None`; `get` called once |
| `get_order` other error | still `KalshiRestError` |
| `list_open_orders` kwargs | still no `client_order_id` (existing pagination test already locks this; do not regress) |

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_kalshi_session.py tests/test_live_paper_kalshi_store.py tests/test_live_paper_kalshi_client.py

make test

git add src/live_paper/kalshi_session.py src/live_paper/kalshi_store.py src/live_paper/kalshi_client.py \
  tests/test_live_paper_kalshi_session.py tests/test_live_paper_kalshi_store.py tests/test_live_paper_kalshi_client.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. **Startup with an orphan resting order** — owned REST rest is cancelled and proven absent **before** `_boot_ready` / `can_publish`.
2. **Unexplained position → local halt** — REST non-zero vs live ledger 0; not adopted; Kalshi blocked; PM/Engine unmentioned.
3. **Timeout place → exactly one order** — `KalshiUnknownWrite` then REST scan by cid; `place` once.
4. **Restart between intent / REST ack / fill persist / cancel / fence** — four cases in the table above.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **`reconcile_interval_s` (20) > `private_ws_blind_s` (15).** `min()` in `poll` is load-bearing. Do not "fix" TOML in this story.
2. **REST fills often have no `client_order_id`.** Bind owned rests (and pending intents via cid scan) **before** fill ingest. A fully filled unknown place that never appears as resting and whose REST fill has no cid will look like an unexplained position (fail closed). Do not add `status=executed` list in v1.
3. **Order groups.** Overlay says "when available". SDK groups are rolling contracts limits, not a crash cookie. v1 `order_group_id` is always `None`. Prefix survives restart; a new process UUID group would not.
4. **`boot()` does not flock.** Two daemons can still both quote Kalshi until US-013 calls `acquire_kalshi_lock` before `boot()`. Document in learnings. Do not "fix" by taking the lock inside `boot` (double-open looks free).
5. **`_fence_unproven` is process-lifetime.** A new process `boot()` retries cancel/fence. That is the crash recovery.
6. **Settlement leftover (REST 0, live ledger ≠ 0) does not halt.** Same spirit as ignoring `source=paper`. If the exchange still reports a size we cannot explain, we halt.
7. **`apply_order_update` insert is adopt.** Only call it for `is_owned_resting`. A bug here is silent inventory theft.
8. **Existing session tests** break if `_ready` does not `boot`+`calls.clear()`, or if `_boot_ready` is required and `_ready` skips boot. Do that in the same change as the flag.
9. **`get_order` 404 today is `KalshiRestError`.** Unknown-cancel would never prove absent without the `None` change.
10. **Session file size.** ~613 lines today. Boot/reconcile will grow it. Do not split a package. Do not dump logic into `kalshi_client.py`. Reuse `_cancel_owned` / `_fence`.
11. **`place_event_order` grep.** Timeout test must not add a session method that places. The test drives the fake REST.
12. **US-012 contract:** `next_client_order_id` → `write_place_intent` → `place_event_order` → on `KalshiUnknownWrite` call `resolve_unknown_write` (never a second place with a new id until REST says absent across a reconcile). This story only provides the functions.
13. **Assumption:** `list_positions` already returns unsettled market positions. We do not call FCM `settlement_status=unsettled`.
14. **Assumption:** best-effort `min_ts=checkpoint.created_unix` does not skip a fill that landed before the checkpoint but failed to ingest (ingest is idempotent; missing fill + REST position still halt). First boot uses `0`.

No Figma. No poly-maker patch. No new dependency.

## Implement-step notes

- `poll` runs periodic `reconcile()` *before* the `private_ws_blind` trip so `min(reconcile_interval_s, private_ws_blind_s)` can stamp. Putting it after blindness would fire at 15s and never reconcile. `test_overdue_reconcile_is_blind` now fails one REST list on the periodic pass (same alert).

