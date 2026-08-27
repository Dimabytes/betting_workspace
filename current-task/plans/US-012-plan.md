# US-012 Implementation Plan

Story: **US-012** — «kalshi_executor: desired-order sync, комиссии и fee gate»

Repo: `/root/work/dota_2_model` on `main`. Create `src/live_paper/kalshi_executor.py` (desired-order sync, V2 four-leg mapping, fee math, fee gate, paper fills) plus the smallest `KalshiDesired` field fill in `kalshi_session.py` and the six session-test constructors that currently pass only a ticker. Tests live in new `tests/test_live_paper_kalshi_executor.py`. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** wire `MatchWorker` dual `predict_fair` / teardown (US-013). Do **not** mutate `evaluate_entry` or PM quoting (`session_quoting.py`, `paper_gateway.py`). Do **not** implement smoke (US-014). Do **not** add a generic `Venue` protocol. Do **not** `import kalshi` outside `kalshi_client.py`. Session still must not call `place_event_order` — only the executor places. Do **not** add `outcome_side` to `place_event_order`. Place-ack `fill_count` does not invent a fill. No economic RiskManager, no flatten.

---

## Binding decisions (feature.json / overlay / US-001..011 learnings)

Cross-ref `current-task/feature.json` FR-7 (executor consumes a Kalshi-only candidate; does not mix venue numbers), FR-8, FR-9, US-012 `changes`, Resolved Questions (paper fills share `kalshi.db` with venue-generated fill id; code+commits in `dota_2_model`), Non-Goals (no shared sqlite, no economic RiskManager, no flatten, Engine/PM untouched, no one-fair-two-venues), and `docs/plans/kalshi-overlay.md` §§ Dual trading (fees, fee gate, count), Library (`post_only=true`), Kalshi book (Decimal until the model call).

1. **Executor is the only place caller.** `KalshiSession` still has no `place_event_order`. US-013 will call `executor.sync(...)` after its own Kalshi `evaluate_entry`. This story does not construct `WalletHost` / `MatchWorker` and does not call `predict_fair`.
2. **Do not mutate `evaluate_entry`.** The fee gate, `MIN_ENTRY_PRICE` re-check, and count math sit *after* a Kalshi-native candidate. PM `EntryGateInputs` / `EntryDecision.token_id` never enter this module. US-013 maps `evaluate_entry` → `KalshiQuoteIntent`.
3. **V2 `side` is bid/ask; store keeps (outcome_side, book_side, yes_price).** `CreateOrderV2Request` has no `outcome_side`. Price on the wire is always YES dollars. Four-leg map in Design §4. Do not add `outcome_side=` to `place_event_order`.
4. **Every resting order is `post_only=True`.** A `KalshiRestError` (including post-only / 409 conflict) is a normal no-quote: `apply_place_reject`, never a second place with `post_only=False`. `place_event_order(..., post_only=False)` is forbidden in this module.
5. **Unknown write: intent already persisted → `apply_unknown_write` → `session.resolve_unknown_write`. Never a second `place`.** Do not call `next_client_order_id` again until that cid is bound or a later reconcile still shows it absent (US-010).
6. **Paper and live share `ingest_fill` + order remaining.** Paper mints `order_id` / `fill_id` and simulates the fill. Live never mints a fill from a place ack; WS/REST ingest (already US-010) is the live fill path. Executor refreshes desired remainder from `store.order_by_client_id(...).remaining_count`.
7. **Live `fee_cost` is the exchange value.** Paper `fee_cost` is `kalshi_maker_fee` from *current* series params. Local taker/maker functions never overwrite a stored live fee.
8. **poly-maker is frozen.** Zero file changes there. Do not import `PaperGateway` / `Engine` / `StrategyCell`. Copy the two paper rules (touch does not fill, one-sided book does not fill) as Decimal helpers against `KalshiBookPair`.

---

## Verified current code (2026-08-26)

| Item | Today | After this story |
|---|---|---|
| `kalshi_executor.py` | missing | new module: fees, gate, V2 map, sync, paper book fills |
| `KalshiDesired` | `ticker: str` only (slot flag) | ticker + cid + outcome/book + yes_price + count + reduce_only |
| `KalshiSession.write_allowed` / `can_publish_targets` / `next_client_order_id` / `resolve_unknown_write` | US-010 API | **Unchanged.** Executor is the caller. |
| `place_event_order` | `side: bid\|ask`, `post_only` forwarded, no `outcome_side` | **Unchanged.** Executor always passes `post_only=True`. |
| `evaluate_entry` / `MIN_ENTRY_PRICE` | PM float gates, 0.35 floor | **Unchanged.** Executor re-checks 0.35 on its Decimal candidate only. |
| `PaperGateway` | PM Engine; touch/one-sided; full-size; `order_id:fill` trade id | **Unchanged.** Kalshi paper is a sibling, not a subclass. |
| `KalshiStore.ingest_fill` | Decimal, exact `fee_cost`, remaining derived | **Unchanged.** Paper and live both call it. |
| `KalshiProfile.base_size_usd` | `float` on the template | Executor converts with `Decimal(str(...))` once. |
| `get_series_fee_params` | current + unmerged `scheduled_changes` | Executor picks the fired row (US-002 learning). |
| `MatchWorker._compute_decision` | PM only | **Unchanged** (US-013). |
| `WalletHost` | public REST + observe | **Unchanged** (US-013). |
| `session.jsonl` Kalshi writers | exist, unwired | **Unchanged.** Executor does not write the tape (US-013 after ingest). |

Do not copy:

- poly-maker `RiskManager` / flatten / day kill.
- `PaperGateway` / `ExecutionGateway`.
- `SignalDecision` / `EntryDecision` as Kalshi inputs (`token_id`, `market_p_radiant`).
- A second sqlite or a paper-only schema.
- Taker-fee haircut on a resting join (s2-join is maker).

---

## Requirements traceability (US-012 `changes`)

| Change | Plan |
|---|---|
| Create `kalshi_executor.py`: sync desired from Kalshi fair + `evaluate_entry` decision | Design §§ 1–3, 8 |
| V2 mapping: buy YES, buy NO, sell held YES, sell held NO; `post_only=true`; post-only reject is no-quote, no downgrade | Design §§ 4, 8 |
| Fees: taker `ceil_cent(0.07·qty·p·(1−p))`; maker 0 unless series has maker fees, else `ceil_cent(0.0175·qty·p·(1−p))`; `fee_type`/`fee_multiplier` from series params | Design § 5 |
| Fee gate after entry: `qty·(side_fair−entry) − maker_fee(entry) − maker_fee(projected_exit) >= 0`; projected exit = side fair on the grid; blocks Kalshi only | Design § 6 |
| Count: `base_size_usd / entry_price`, quantize down to 0.01, zero skip | Design § 7 |
| `MIN_ENTRY_PRICE = 0.35` on the Kalshi candidate | Design § 6 |
| Paper: touch / one-sided do not fill; fill at our limit; `kalshi.db` + simulated maker fee | Design § 9 |
| Live fee from fill as-is, including exits; no local replace | Design § 10 |
| Partial fill updates desired remainder and position; Decimal until serialize; float only at the model (not here) | Design §§ 8, 10 |
| Autotests in `tests/test_live_paper_kalshi_executor.py` | Test plan |
| `make test` / `make lint-all` | Verification |

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `KalshiSession.write_allowed` / `can_publish_targets` / `set_desired` / `clear_desired` / `desired` / `next_client_order_id` / `resolve_unknown_write` / `order_group_id` / `book_pair` | Sync gates and identity. Cancel of blocked rests stays in session safe-mode. |
| `KalshiStore.write_place_intent` / `apply_place_ack` / `apply_place_reject` / `apply_unknown_write` / `ingest_fill` / `write_cancel_intent` / `apply_cancel_ack` / `order_by_client_id` / `position_from_source` | Persistence. Intent before any live wire call. |
| `KalshiRestClient.place_event_order` / `cancel_event_order` | Live writes only. `post_only=True`, `time_in_force="good_till_canceled"`, `self_trade_prevention_type="maker"`. |
| `session.cancel` path (`write_cancel_intent` + `cancel_event_order` + unknown resolve) | When desired changes or becomes None, cancel the old rest through the same intent/ack/unknown sequence session already uses for owned ids. Do not copy a second fence. Smallest: executor calls `store.write_cancel_intent` then `rest.cancel_event_order` then ack/unknown — **or** reuse session's `_cancel_owned` if it is made package-private-callable. Prefer a new `KalshiSession.cancel_order(row)` one-liner that is the existing `_cancel_owned` body, rather than duplicating unknown-write handling. |
| `KalshiSeriesFeeParams` / `KalshiScheduledFeeChange` | Passed into `sync`. Do not GET series inside the tick unless tests need it — caller (US-013) / test fixture supplies params. |
| `KalshiBookPair` | Paper crossing + implied ask/bid. `None` pair = one-sided = no fill. |
| `KalshiFill` | Paper constructor. Live fills already mapped in the client. |
| `MIN_ENTRY_PRICE` constant | Import from `shared.constants.strategy`. Compare as `Decimal(str(MIN_ENTRY_PRICE))`. Do not edit the constant. |
| Session test `_FakeRest` / `_ready` | Copy a slimmer fake into the executor test (do not import private session-test helpers). |
| `notify_in_background` | Not this story (session already alerts safe mode). |

Do not import `wallet_host`, `match_worker`, `session_quoting.evaluate_entry`, `session_engine`, `Engine`, `StrategyCell`, `paper_gateway`, `kalshi`, `session_journal`. Do not open `paper.db`.

---

## Design

### 1. Types (`kalshi_executor.py`)

No `float`. No `dict[str, Any]`. No anonymous tuples. All new function arguments required (AGENTS.md).

```python
_CENT = Decimal("0.01")
_TAKER_RATE = Decimal("0.07")
_MAKER_RATE = Decimal("0.0175")
_FEE_TYPE_MAKER = "quadratic_with_maker_fees"
_COUNT_QUANTUM = Decimal("0.01")
```

```python
@dataclass(frozen=True)
class KalshiQuoteIntent:
    """Kalshi-native candidate. Never carries token_id or market_p_radiant."""

    ticker: str
    outcome_side: OutcomeSide  # yes = join YES, no = join NO
    entry_price: Decimal  # dollars of that outcome (YES$ or NO$)
    side_fair: Decimal  # same units as entry_price
```

`entry_price` is the join-bid of the chosen outcome (what `evaluate_entry` already floored on that side). `side_fair` is that side's fair. US-013 sets these from Kalshi numbers only.

```python
@dataclass(frozen=True)
class KalshiV2Place:
    """Wire fields for CreateOrderV2Request. side is bid/ask; price is YES dollars."""

    side: BookSide
    price: Decimal
    outcome_side: OutcomeSide
    book_side: BookSide
    yes_price: Decimal
    reduce_only: bool
```

### 2. Session `KalshiDesired` (fill the US-009 stub)

Replace the ticker-only stub:

```python
@dataclass(frozen=True)
class KalshiDesired:
    """Working quote the executor is syncing. Safe mode clears this."""

    ticker: str
    client_order_id: str
    outcome_side: OutcomeSide
    book_side: BookSide
    yes_price: Decimal
    count: Decimal
    reduce_only: bool
```

`set_desired` / `clear_desired` / `write_allowed` unchanged. Session tests that currently call `KalshiDesired(TICKER_A)` get a tiny `_desired(ticker)` helper with dummy cid/yes/bid/`Decimal("0.40")`/`Decimal("1")`/`False`. Behavior of those tests (slot present → cleared on safe mode) stays the same.

### 3. `KalshiExecutor`

```python
class KalshiExecutor:
    """Paper/live desired-order sync. The only production caller of place_event_order."""

    def __init__(
        self,
        session: KalshiSession,
        store: KalshiStore,
        rest: KalshiRestClient,
        profile: KalshiProfile,
        settings: KalshiSettings,
    ) -> None:
        ...
```

`settings.trading` is `paper` or `live` (session already requires that). Source for intents/fills: `"paper"` iff trading is paper, else `"live"`.

```python
    async def sync(
        self,
        intent: KalshiQuoteIntent | None,
        pair: KalshiBookPair | None,
        yes_fair: Decimal,
        no_fair: Decimal,
        fee_params: KalshiSeriesFeeParams,
        now: datetime,
    ) -> None:
        """Publish or clear one ticker's resting order. Does not call evaluate_entry."""
```

`intent is None` means the Kalshi `evaluate_entry` was blocked (or US-013 has no candidate). Position still drives an exit (Design §8). `now` is timezone-aware UTC for scheduled fee rows.

Do not take a journal. Do not call `predict_fair`.

### 4. V2 four-leg mapping

Kalshi's V2 request is one ticker + `side=bid|ask` + YES-dollar `price`. The YES/NO books on that ticker are complements (`yes_ask = 1 − no_bid`). Store rows keep economic `(outcome_side, book_side, yes_price)` from US-008.

| Strategy | outcome | book | reduce_only | V2 side | V2 price (`yes_price`) |
|---|---|---|---|---|---|
| buy YES | yes | bid | False | bid | `entry_price` |
| buy NO | no | bid | False | ask | `1 − entry_price` |
| sell held YES | yes | ask | True | ask | `exit_price` (YES$) |
| sell held NO | no | ask | True | bid | `1 − exit_price` (exit is NO$) |

```python
def v2_place_from_leg(
    outcome_side: OutcomeSide,
    book_side: BookSide,
    outcome_price: Decimal,
    reduce_only: bool,
) -> KalshiV2Place:
    """Map one economic leg onto V2 bid/ask + YES dollars."""
```

`outcome_price` is the dollars of that outcome (YES$ for yes legs, NO$ for no legs). `yes_price = outcome_price` on yes legs, `1 − outcome_price` on no legs. Tests lock all four rows: `place_event_order` kwargs `side`/`price` plus store `outcome_side`/`book_side`/`yes_price`.

Buy NO and sell-held YES are both V2 `ask`; they differ by `reduce_only` and by store `(outcome, book)`. Do not collapse them.

### 5. Fees

```python
def ceil_cent(value: Decimal) -> Decimal:
    """Round toward +inf onto a cent. Kalshi's documented fee rounding."""
    return value.quantize(_CENT, rounding=ROUND_CEILING)


def effective_series_fees(
    params: KalshiSeriesFeeParams, now: datetime
) -> tuple[str, Decimal]:
    """Current fee_type/multiplier, replaced by the latest scheduled row with scheduled_ts <= now."""
```

Pick the fired row with the greatest `scheduled_ts` still `<= now`. Naive sort is fine (a handful of rows). Unaware `now` is a test bug — require aware UTC.

```python
def kalshi_taker_fee(qty: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
    """ceil_cent(0.07 · multiplier · qty · p · (1−p))."""


def kalshi_maker_fee(
    qty: Decimal, price: Decimal, fee_type: str, multiplier: Decimal
) -> Decimal:
    """0 unless fee_type is quadratic_with_maker_fees, else ceil_cent(0.0175 · multiplier · qty · p · (1−p))."""
```

`p` is the outcome price (YES$ or NO$). `p·(1−p)` is symmetric, so YES vs NO does not change the cent at the locked 0.50 case. Clamp nothing that cannot occur: tests pass `p` in `(0, 1)`. `qty > 0`, `multiplier >= 0`.

Required lock: `p=Decimal("0.5")`, `qty=Decimal("1")`, `multiplier=Decimal("1")` → taker `Decimal("0.02")`, maker default (`fee_type="quadratic"`) `Decimal("0")`, maker listed (`quadratic_with_maker_fees`) `Decimal("0.01")`.

Taker exists for the unit test and for documentation. Resting s2-join never charges it. Do not apply taker on paper or on a live maker fill.

### 6. Fee gate + `MIN_ENTRY_PRICE`

After a non-None entry intent, before count:

1. If `entry_price < Decimal(str(MIN_ENTRY_PRICE))` → skip (no-quote). `evaluate_entry` already dropped this when US-013 calls it; the executor still checks so a direct/test candidate cannot sneak under 0.35.
2. `qty = contract_count(base_size_usd, entry_price)`. Zero → skip.
3. `projected_exit = floor_cent(side_fair)` (conservative maker exit on the 0.01 grid).
4. `edge = qty * (side_fair - entry_price) - maker_fee(qty, entry_price) - maker_fee(qty, projected_exit)`.
5. If `edge < 0` → skip. If series maker fee is 0, both fee terms are 0 and this is just non-negative raw edge.

```python
def kalshi_fee_gate_ok(
    qty: Decimal,
    entry_price: Decimal,
    side_fair: Decimal,
    fee_type: str,
    multiplier: Decimal,
) -> bool:
    """True iff qty*(side_fair-entry) covers maker fees on entry and projected exit."""
```

Do not write `EntryBlock`. Do not call `evaluate_entry`. A blocked Kalshi candidate does not change PM.

### 7. Count

```python
def contract_count(base_size_usd: Decimal, entry_price: Decimal) -> Decimal:
    """base_size_usd / entry_price, quantized down to 0.01 contracts. Zero if that floors to 0."""
    if entry_price <= 0:
        return Decimal("0")
    raw = base_size_usd / entry_price
    return raw.quantize(_COUNT_QUANTUM, rounding=ROUND_DOWN)
```

`base_size_usd = Decimal(str(profile.base_size_usd))` once in `__init__` (or once per `sync` — once in init is enough; profile is frozen). Exit count is `abs(position)` quantized down to 0.01, not `base_size_usd`. Zero remaining → skip.

### 8. Sync state machine

Position = `store.position_from_source(ticker, source)` (paper leftover does not affect live; live leftover does not affect paper).

```
sync(intent, pair, fairs, fees, now)
        |
        | not can_publish_targets → clear desired; return
        | (do not place; session owns cancel/fence if blocked)
        |
        v
   |position| >= 0.01 ?
        | yes → exit target: sell held side, reduce_only, join-ask
        |        sell YES: outcome=yes, book=ask, outcome_price=ceil_cent_grid(pair.yes_ask)
        |        sell NO:  outcome=no,  book=ask, outcome_price=ceil_cent_grid(pair.no_ask)
        |        skip if pair is None or not 0 < price < 1
        |        skip if price < side_fair of the held outcome (do not sell below fair)
        |        count = |pos| quantized down; 0 → skip
        | no  → entry target from intent
        |        intent None / MIN_ENTRY / count 0 / fee gate fail → target None
        v
   target is None → cancel our rest on this ticker if any; clear desired; return
        v
   existing desired matches target (outcome, book, yes_price, count, reduce_only)
        and store remaining == desired.count → return
        v
   mismatch with an open rest → cancel it (intent + one cancel + unknown resolve)
        then place the new target (new cid)
        v
   place:
        if not write_allowed: return
        cid = next_client_order_id(ticker, book_side, outcome_side)
        write_place_intent(..., yes_price, count, source, ...)
        if paper: mint order_id `paper-{cid}`, apply_place_ack(fill_count=0, remaining=count)
        if live:  place_event_order(..., side=v2.side, price=v2.price, post_only=True,
                                    reduce_only=v2.reduce_only, order_group_id=session.order_group_id)
                  ack → apply_place_ack (still no ingest from fill_count)
                  KalshiUnknownWrite → apply_unknown_write; resolve_unknown_write; no second place
                  KalshiRestError (post-only / 409 / validation) → apply_place_reject; clear desired
                  KalshiAuthFault / KalshiRateLimitFault → apply_place_reject; do not enter safe mode
                    (session owns that; US-013 can trip on the exception if it wants)
        set_desired(KalshiDesired(...))
```

**Generation / unknown:** `next_client_order_id` bumps `_desired_generation`. After `KalshiUnknownWrite`, this tick (and later ticks) must not mint another cid for that ticker until `resolve_unknown_write` has bound the order or a reconcile still shows the cid absent. Implement with an in-memory `_unknown_cid: dict[str, str]`: while set, `sync` only calls `resolve_unknown_write` and returns. When the store row is `resting`/`filled`/`rejected`, drop the latch. Do not invent a reject on the first empty list (US-010).

**Cancel of a replaced rest:** new cancel intent, one `cancel_event_order`, unknown → `resolve_unknown_write`. Never cancel then immediately place the same cid.

**`can_publish_targets` false:** clear desired only (in-memory slot). Resting live orders are session safe-mode's job, not a second cancel loop here.

### 9. Paper fills

Not a `PaperGateway`. No Engine. Called from `sync` after a successful paper place, and from an explicit `apply_paper_book(ticker, pair)` so tests can feed a later book without going through `sync` again.

Rules (same idea as `PaperGateway._is_crossed` / both-sides-valid):

- `pair is None` → no fill (one-sided / crossed / missing book).
- Touch does not fill: BUY (book=bid) fills iff opposite ask **strictly below** the outcome limit; SELL (book=ask) fills iff opposite bid **strictly above**.
  - buy YES at `P`: `pair.yes_ask < P`
  - sell YES at `P`: `pair.yes_bid > P`
  - buy NO at NO$ `N` (store `yes_price = 1−N`): `pair.no_ask < N`
  - sell NO at NO$ `N`: `pair.no_bid > N`
- Fill **price** is our limit (YES dollars on the store row), not the trigger.
- Fill **count** = `min(remaining, opposite_size)` quantized down to 0.01. Opposite size is the best-level size of the triggering side (`no_bid` size for a YES-ask cross, etc.). If we cannot read a size off `KalshiBookPair` (it has no sizes today), fill **full remaining** and mark a `ponytail:` comment: pair has no size, so paper is full-remainder like PM; upgrade when the book pair grows sizes. **Partial is still tested** via a direct `ingest_fill` of a smaller count (live and paper share that path). Prefer adding optional sizes on the pair **only if** a paper-partial-from-book test is otherwise impossible — do **not** grow `KalshiBookPair` unless the paper-from-book partial test needs it. Default: full remaining on a strict cross; partial coverage is `ingest_fill` of count 3 on initial 10.
- `fill_id` / `trade_id`: venue-generated `paper-{order_id}:{n}` (n from a per-executor counter). Same schema as live. `is_taker=False`. `fee_cost = kalshi_maker_fee(...)` with *current* fired series params. `source="paper"`.
- Then `store.ingest_fill`. Duplicate id → `applied=False`, no second ledger. Refresh desired count from remaining; 0 → clear desired.
- Do not call `place_event_order` in paper. Do not call `session.apply_user_event`.

### 10. Live fills and remainder

Executor does **not** ingest live fills. US-010 session already `ingest_fill(..., "live")` on owned WS/REST. Each `sync` (and `refresh_desired`) reads `order_by_client_id` / open orders:

- remaining > 0 → `set_desired` with `count=remaining` (partial)
- remaining 0 or status filled → `clear_desired`

Live `fee_cost` stays the sqlite value. `kalshi_maker_fee` is not written onto a live row. Exit legs are fills too; same rule.

Place ack `fill_count > 0`: counts on the order row only (`apply_place_ack` already maxes with ingested sum). Ledger waits for `ingest_fill`. Tests assert cash/position unchanged when ack says filled but no fill row exists.

### 11. Decimal / float

All executor math is `Decimal`. `KalshiProfile.base_size_usd` and `MIN_ENTRY_PRICE` become Decimal at the boundary via `Decimal(str(...))`. No `float()` in this module. Tests `_assert_no_float` on desired, fills, and V2 place kwargs.

### 12. What this story does not touch

- `MatchWorker._compute_decision` / second `predict_fair` / Engine attach (US-013)
- `WalletHost` boot flock / teardown fence / Telegram leftover (US-013)
- `session_quoting.evaluate_entry` / `build_dota_quotes` / `PaperGateway`
- `session_journal` Kalshi writers (US-013 calls `write_kalshi_fill` after ingest)
- `kalshi_observe.py` / matcher / prior
- `engine_seams.py`
- Docker / `docs/live-paper.md` Kalshi chapter (US-015)
- `poly-maker`
- Economic flatten, daily kill, exposure cap

### 13. Forbidden imports / calls

`kalshi_executor.py` must not mention: `Engine`, `engine.gateway`, `StrategyCell`, `evaluate_entry`, `PaperGateway`, `import kalshi`, `place_event_order(..., post_only=False)`. Tests grep the source.

`kalshi_session.py` must still grep-clean for `place_event_order`.

---

## State machine (text)

```
        can_publish? --no--> clear desired (session cancel/fence if blocked)
              |
             yes
              |
        position open? --yes--> exit target (reduce_only sell held)
              |                         |
             no                    fee/fair/pair skip --> no-quote
              |
        intent+MIN_ENTRY+count+fee_gate --> entry target
              |
        target None --> cancel rest, clear
              |
        match existing rest --> noop (refresh remaining)
              |
        unknown cid latch --> resolve_unknown_write only
              |
        cancel old (if any) then place
              |
         +---- paper ----+---- live ----+
         | mint paper id | V2 post_only |
         | book may fill | WS/REST fill |
         +-------+-------+------+-------+
                 |              |
                 v              v
            ingest_fill    (session ingest)
            remaining → desired.count
```

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Pure fee / count / V2 map / gate (no I/O)

`ceil_cent`, `effective_series_fees`, `kalshi_taker_fee`, `kalshi_maker_fee`, `contract_count`, `kalshi_fee_gate_ok`, `v2_place_from_leg`. Tests: the three locked fee numbers; gate block vs pass; four-leg table.

### Step 2 — Fill `KalshiDesired` + session-test helper

Update the six `KalshiDesired(TICKER_A)` sites. Existing US-009/010 tests must still pass.

### Step 3 — `KalshiExecutor.sync` live place/cancel/unknown/post-only

Fake REST. Intent before place. Unknown → resolve, `place_calls == 1`. Post-only `KalshiRestError` → reject, no downgrade. `write_allowed` false → no place. `can_publish_targets` false → no place.

### Step 4 — Paper place + paper book rules

No REST create. Touch / one-sided do not fill. Strict cross fills at our limit into `kalshi.db` with simulated maker fee and `source=paper`.

### Step 5 — Partial remainder + live fee-as-is

`ingest_fill` count 3 of 10 → desired 7, position ±3. Live fill with `fee_cost=Decimal("0.09")` (not equal to local maker) stays 0.09 on the row.

### Step 6 — Quality gate

See Verification. `git add` new files before `make lint-all` (`pre-commit` skips untracked).

### Step 7 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Why: Kalshi joins must be post-only with fee-aware size so a maker haircut cannot turn a positive `evaluate_entry` edge into a losing rest; paper and live must share sqlite remaining so a partial does not requote the filled size.
- Set US-012 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `progress.txt` / `learnings.txt` (V2 four-leg table; post-only never downgrades; unknown latch; paper does not call place; live fee as-is; fee gate does not touch `evaluate_entry`; not wired to MatchWorker).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Edge cases

| Case | Behavior |
|---|---|
| `can_publish_targets` false | no place; desired cleared |
| `write_allowed` false | no place |
| `intent is None`, flat | no-quote |
| `intent` under 0.35 | skip even if caller passed it |
| count floors to 0 | skip |
| fee gate negative (listed maker, tiny edge) | skip; `evaluate_entry` would have allowed |
| fee gate zero edge, maker 0 | pass (`>= 0`) |
| buy YES | V2 `bid` @ entry; store yes/bid |
| buy NO | V2 `ask` @ `1-entry`; store no/bid |
| sell held YES | V2 `ask` @ exit; `reduce_only`; store yes/ask |
| sell held NO | V2 `bid` @ `1-exit`; `reduce_only`; store no/ask |
| post-only `KalshiRestError` | reject; `post_only=True` on the one call; no retry |
| `KalshiUnknownWrite` | resolve; place once |
| unknown still empty list | stay latched; no second place |
| place ack `fill_count=5` | no fill row; cash 0 until ingest |
| paper touch (`yes_ask == limit`) | no fill |
| paper one-sided (`pair is None`) | no fill |
| paper strict cross | fill at our limit; maker fee from current series |
| paper `quadratic` series | `fee_cost=0` |
| paper listed series | simulated maker cents |
| live fill `fee_cost=0.09` | stored 0.09; local maker not applied |
| partial 3 of 10 | remaining 7; position ±3; desired.count 7 |
| position open + new entry intent | ignore entry; exit path only |
| exit price < held-side fair | no-quote (do not sell through fair) |
| `base_size_usd` float 10.0 | `Decimal("10.0")` via `str` |
| scheduled maker-fee row in the past | effective type is listed |
| scheduled row in the future | still current `quadratic` |
| session `place_event_order` | still forbidden |
| `evaluate_entry` source | not imported |
| Engine halt | forbidden |

---

## Test plan

New: `tests/test_live_paper_kalshi_executor.py`

Helpers:

- `_assert_no_float` (copy from store tests).
- `_store(tmp_path)`, `_profile()`, `_settings(trading)`, `_session` via existing `KalshiSession` + a `_FakeRest` that records `place_event_order` kwargs (`side`, `price`, `post_only`, `reduce_only`) and can raise `KalshiUnknownWrite` / `KalshiRestError`.
- `_ready(session, rest)`: `await boot()`; snapshot a two-sided book so `can_publish_targets` is True (reuse session-test snapshot shape, do not import the session test module).
- `_params(fee_type="quadratic", multiplier="1", changes=())`.
- `_intent(outcome="yes", entry="0.40", fair="0.50")`.

`asyncio.run`, no pytest-asyncio. `tmp_path` store. No live HTTP. No `WalletHost` / `MatchWorker`.

| Test | Setup | Expect |
|---|---|---|
| **taker 2 cents** | `kalshi_taker_fee(1, 0.5, 1)` | `Decimal("0.02")` |
| **maker default 0** | `kalshi_maker_fee(..., "quadratic", 1)` | `0` |
| **maker listed 1 cent** | `kalshi_maker_fee(..., "quadratic_with_maker_fees", 1)` | `Decimal("0.01")` |
| **fee gate blocks** | listed maker, `entry=0.40`, `fair=0.40` (zero raw edge) | `False` |
| **fee gate passes** | listed maker, wide edge (e.g. entry 0.40, fair 0.50, qty from $10) | `True`; default quadratic with the block-case numbers is `True` (fees 0) |
| **MIN_ENTRY 0.35** | intent entry `0.34` | no place |
| **count down to 0.01** | size 10, price 0.40 → 25.00; size 10, price 0.39 → 25.64 quantized down | skip when result is 0 |
| **V2 buy YES** | ready, intent yes 0.40 | `side=bid`, `price=0.40`, `post_only=True`, `reduce_only=False`; store yes/bid |
| **V2 buy NO** | intent no 0.40 | `side=ask`, `price=0.60`; store no/bid, yes_price 0.60 |
| **V2 sell held YES** | paper/live position +5 YES; pair yes_ask 0.55, yes_fair 0.50 | `side=ask`, `reduce_only=True`; store yes/ask |
| **V2 sell held NO** | position −5; pair no_ask 0.55 | `side=bid` at `1-0.55`; store no/ask |
| **post-only reject, no downgrade** | live; `place_event_order` raises `KalshiRestError("post only")` | one place, `post_only is True`; status rejected; second call kwargs never `False` |
| **unknown place → one order** | live; first place raises `KalshiUnknownWrite`; list then returns cid | `place_calls==1`; resting; second `sync` does not place |
| **place ack fill_count does not invent a fill** | ack `fill_count=3` | `fills` empty; `position_from_source` 0 |
| **paper touch** | paper rest buy YES 0.50; pair `yes_ask=0.50` | no fill |
| **paper one-sided** | `pair=None` (or derive rejects) | no fill |
| **paper strict cross** | buy YES 0.50; `yes_ask=0.49` two-sided | one paper fill at 0.50; `source=paper`; `is_taker=False`; fee matches current series maker |
| **paper listed maker fee** | same cross, `quadratic_with_maker_fees` | `fee_cost` is local maker, not 0 |
| **partial** | rest 10; `ingest_fill` count 3 | remaining 7; position ±3; desired.count 7 |
| **live fee as-is** | ingest live fill fee `0.09` while local maker is 0 | sqlite `0.09` |
| **write_allowed false** | trip safe mode | no `place_event_order` |
| **executor source has no Engine / evaluate_entry / post_only=False / import kalshi** | read `kalshi_executor.py` | grep clean |
| **session still has no place_event_order** | read `kalshi_session.py` | grep clean |

Existing session/store/client/quoting tests must still pass. `test_evaluate_entry_min_price_floor` unchanged (PM path).

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
uv run --group backtest python -m pytest tests/test_live_paper_kalshi_executor.py tests/test_live_paper_kalshi_session.py tests/test_live_paper_session_quoting.py

make test

git add src/live_paper/kalshi_executor.py src/live_paper/kalshi_session.py \
  tests/test_live_paper_kalshi_executor.py tests/test_live_paper_kalshi_session.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. Do **not** run `make install` as a bare `uv sync` (US-001: drops nautilus). No new dependency.

Story autotests that must exist and pass:

1. **p=0.5 qty=1 fees** — taker 2 cents, maker default 0, maker listed 1 cent.
2. **Fee gate blocks and passes** — listed series can reject a candidate `evaluate_entry` would allow; default series with the same numbers passes.
3. **V2 four legs** — buy YES, buy NO, sell held YES, sell held NO; wire `side` is bid/ask; store keeps outcome/book/yes_price.
4. **Post-only reject without downgrade** — one place with `post_only=True`; no retry with `False`.
5. **Paper rules** — touch no fill; one-sided no fill; strict cross fills at our limit into `kalshi.db`.
6. **Partial** — remainder and position update exactly.

No Figma. No poly-maker patch.

---

## Risks / assumptions

1. **Paper fill simulation vs live path sharing (load-bearing).** One `KalshiFill` + `ingest_fill` is the only ledger writer. Paper mints ids and `fee_cost` from series params, then ingest. Live never mints: session WS/REST ingest already happened; executor only rereads remaining. A shared `_on_resting_fill` that also called `ingest_fill` on live would double-apply when session already ingested (unique keys would no-op, but desired refresh must not assume `applied=True`). Keep paper ingest inside the executor; live ingest outside. Tests: paper cross creates a fill; live ack `fill_count` does not; live `ingest_fill` from the test (simulating session) moves the ledger once.
2. **Buy NO is V2 `ask` at `1 − entry`.** SDK `CreateOrderV2Request.side` is only bid/ask; price is YES dollars. Store still records `(no, bid, yes_price=1-entry)` so US-008 notional stays −entry. If the demo exchange later reports that rest as `(yes, ask)`, live ingest still has the same signed delta (−count) but **cash sign differs** (receive YES$ vs pay NO$). Fail closed in US-014 if a real demo fill comes back as the other pair: do not "fix" by rewriting store math here. Document in learnings.
3. **`KalshiBookPair` has no sizes.** Paper-from-book fills the full remainder on a strict cross (same optimism as PM `PaperGateway`). Partial is proven via a smaller `ingest_fill`, which is also the live partial path. Do not grow the pair in this story unless a reviewer demands book-sized paper partials.
4. **`evaluate_entry` already applies 0.35 and max-edge.** The executor gate is extra Kalshi-only haircut. US-013 must still call `evaluate_entry` on Kalshi bids/fairs and must not pass PM mids. This story cannot enforce that — tests construct `KalshiQuoteIntent` directly.
5. **Unknown-write latch vs desired generation.** `next_client_order_id` increments before place. A late land of an abandoned cid is a US-010 leftover-cancel problem, not a second place. The latch is the v1 ceiling (`ponytail:` in-memory; upgrade: persist unknown cid in sqlite, already on the intent row — **use the intent status `unknown` as the latch** instead of extra dict if it is cheaper). Prefer: if `pending_intents()` has a place `unknown` for this ticker, only resolve, do not place. No new dict then.
6. **Cancel-then-place races.** Smallest: await cancel ack/unknown-resolve before place on that ticker. Do not batch.
7. **Executor does not trip safe mode on auth.** Session owns `_enter_safe_mode`. US-013 may wrap `sync` and trip. Tests do not require an alert from the executor.
8. **Scheduled fee row vs multiplier.** Overlay rates 0.07 / 0.0175 are the base; `fee_multiplier` scales them. Locked tests use multiplier 1. A 0.25 multiplier is not a required story test; still implement the multiply so US-002's scheduled row is not dead data.
9. **`KalshiDesired` constructor churn.** Six session tests. Do it in the same change as the dataclass or those tests fail at collection.
10. **Journal gap until US-013.** Paper/live fills land in `kalshi.db` with no `session.jsonl` Kalshi row. Accepted (US-011 learning). Do not import the journal here.
11. **Assumption:** v1 one open round per ticker; nonzero position (by source) suppresses entry and only attempts a reduce-only exit. No unwind timer.
12. **Assumption:** `linear_cent` $0.01 is already enforced at bind (`off_grid`). Executor snaps with `Decimal("0.01")` and does not re-read `price_ranges`.
13. **Assumption:** `self_trade_prevention_type="maker"` and GTC are the resting defaults US-006 tests already use. Do not add IOC/FOK (that's US-014 smoke-only).
14. **Session file size.** Filling `KalshiDesired` is a few fields. Do not move boot/reconcile. New logic belongs in `kalshi_executor.py`.

No Figma. No poly-maker patch. No new dependency.

## Implement-step notes

- `sync` is the US-013 hook: after Kalshi `predict_fair` + `evaluate_entry`, map token choice → `KalshiQuoteIntent` (yes if the candidate is the YES outcome, no if NO; `entry_price=Decimal(str(entry.price))`; `side_fair` from that side's Kalshi fair) or pass `None`. Then `executor.sync(...)`. Then `write_kalshi_fill` for any new `fill_id`s.
- Paper `apply_paper_book` can be called from `sync` when trading is paper and a rest exists; US-013 can also call it when the Kalshi book updates between model ticks.
- Never pass `post_only=False`. Never call `place_event_order` from `kalshi_session.py`.
