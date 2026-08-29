# Fix Kalshi live-paper safety and lifecycle

Status: ready for implementation.

Target repository: `../dota_2_model`, branch `main`.

Source review: live-paper changes from 2026-08-24 11:30 through
2026-08-29 11:30 CEST, commits `312d5aed..7453ecb5`.

This plan fixes all seven findings from that review. Update this file if an
implementation decision changes.

## Required behavior

Keep the Polymarket, Steam, and GRID behavior unchanged. Do not change bet
sizes, the CLI, TOML settings, or the existing JSONL and SQLite file formats.

Use these trading rules:

- If fair is unavailable, hold an existing position for both YES and NO. Do
  not place an exit order.
- If the Kalshi book is missing, one-sided, or crossed, cancel every working
  order on that ticker.
- If a fence is unproven, block the entire Kalshi account until the process
  restarts.
- Write `canceled` to the journal only after a cancel ACK, successful
  resolution of an unknown cancel, or a proven fence.
- Never let Kalshi I/O delay Steam or GRID consumption or Polymarket quoting.
- Do not edit `../poly-maker`.

## Add one execution actor

Add `src/live_paper/kalshi_execution_actor.py` with one process-wide
`KalshiExecutionActor`.

The actor must:

- Accept a quote without waiting for network I/O.
- Keep only the latest pending decision for each ticker.
- Serialize all Kalshi place and cancel operations.
- Process feed-timeout, ticker-stop, and account-teardown commands before
  ordinary quotes.
- Never interrupt a REST write that has reached the wire. When that write
  finishes, process the pending cancel or fence before the next quote.
- Select pending tickers fairly, such as with round-robin selection.
- Catch errors per command and continue running.

Give `KalshiRuntime` these internal methods:

```python
def start_ticker(
    ticker: str,
    yes_is_radiant: bool,
    series_ticker: str,
) -> None

def submit_quote(
    ticker: str,
    decision: KalshiTickDecision,
    now: datetime,
) -> bool

def request_feed_timeout(
    ticker: str,
    second: int,
) -> None

def drain_execution_updates(
    ticker: str,
) -> tuple[KalshiExecutionUpdate, ...]

async def stop_ticker_and_fence(
    ticker: str,
    second: int,
) -> KalshiStopResult
```

`submit_quote` returns `False` only when the ticker is closing or retired. It
must not call REST or create one task per tick.

## Serialize actor and session work

Create one `asyncio.Lock` when the Kalshi components are wired. Pass the lock
to the actor and the session as a required dependency.

Hold the lock around:

- `executor.sync`;
- terminal user-order and fill ingestion;
- REST reconciliation;
- safe-mode cancel and fence work;
- feed-timeout fencing;
- per-ticker stop and fence work;
- account teardown fencing.

Block writes synchronously when safe mode trips, before waiting for the lock.
Do not await the safe runner from code that already owns the lock. Schedule the
runner there, then let `poll` or teardown await it outside the lock.

Add a regression test for the sequence unknown write, auth fault, and safe
mode. The sequence must not deadlock.

## Fetch fees without blocking control commands

Start a shared fee-fetch task for `series_ticker` from `start_ticker`.

- Do not make the actor await the fee task while a control command is ready.
- Keep the latest quote pending until fees arrive.
- Let feed timeout and stop run without fees.
- Cache a successful result by series.
- Remove a failed task from the cache and emit `KalshiErrorUpdate`.
- Allow the next decision to start another bounded fetch.

Boot must continue to fence leftover orders before any ticker starts.

## Define execution updates

Add frozen dataclasses for execution results:

```python
@dataclass(frozen=True)
class KalshiPlacedUpdate:
    ticker: str
    second: int
    desired: KalshiDesired


@dataclass(frozen=True)
class KalshiCanceledUpdate:
    ticker: str
    second: int
    client_order_id: str


@dataclass(frozen=True)
class KalshiErrorUpdate:
    ticker: str
    second: int
    error_type: str


KalshiExecutionUpdate = (
    KalshiPlacedUpdate
    | KalshiCanceledUpdate
    | KalshiErrorUpdate
)


@dataclass(frozen=True)
class KalshiStopResult:
    ticker: str
    fence_state: FenceState
    final_pair: KalshiBookPair | None
    error_type: str | None
```

Keep updates in a process-owned buffer per ticker. Record the latest model
second at each submit. Safe-mode and stop actions use that second.

Apply these event rules:

- Emit `KalshiPlacedUpdate` only after a paper ACK, a live REST ACK, or
  successful resolution of an unknown place.
- Emit `KalshiCanceledUpdate` only after a confirmed cancellation.
- Keep the working order after a failed cancel. Emit only an error update.
- Do not report a fill or an external terminal order as a strategy cancel.
- Group updates for the same second into the existing Kalshi `quote` JSONL
  record. Do not change the record schema.
- Continue to write fills from `kalshi.db` with the existing durable cursor.

## Implement in this order

### 1. Add failing regression tests

Create `tests/test_live_paper_kalshi_execution_actor.py`. Extend the existing
runtime, session, store, executor, and match-lifecycle tests.

Before changing production code, cover these failures:

1. Block a REST place, then deliver another Steam or GRID event. The second
   event must still update the Polymarket state.
2. Trigger feed timeout during place. After the place returns, the actor must
   cancel the new order and prove the fence.
3. Stop a ticker during place. Stop must wait for place, cancel it, fence it,
   and retire the ticker in that order.
4. Make cancel fail. The journal must not contain `canceled`, and the working
   order must remain visible.
5. Make a stop fence unproven. Every subsequent Kalshi place must be blocked.
6. Retire ticker A, start ticker B, trip an account fault, then reconnect B.
   Ticker A must not participate in reopen.
7. Apply WS `canceled`, `executed`, and partial-fill-then-canceled events. Each
   local order must reach the correct terminal state.
8. Give the executor an invalid book while an order rests. It must cancel the
   order.
9. Give a blocked decision to long YES and long NO positions. Neither side
   may create an exit.
10. Submit several decisions while REST is busy. The actor must run the
    current decision and the latest pending decision, but it must not drop or
    reorder a control command.

### 2. Make SQLite the working-order source of truth

Remove `KalshiSession._desired`, `set_desired`, `clear_desired`, and
`MatchWorker._DesiredStamp`.

Add a `KalshiStore` query for the working order by ticker and source:

- `resting` and `pending_cancel` occupy the slot;
- `pending_place` and `unknown` block another place until reconciliation;
- terminal statuses do not occupy the slot;
- more than one open order for a ticker is a safety fault.

Keep `KalshiDesired` as a read-only view built from `KalshiOrderRow`. Do not
clear working-order state when safe mode trips. If cancel fails, restore the
store row to `resting`, so the order stays visible.

Rename `_desired_generation` to `_client_order_generation`. It generates
client order IDs and does not represent working-order state.

### 3. Apply terminal order observations

Replace `KalshiRestingOrder` with `KalshiOrderObservation`.

```python
KalshiWireOrderStatus = Literal[
    "resting",
    "canceled",
    "executed",
]
```

Make the REST and WS parsers validate the status. Treat an unknown status as a
typed REST or WS fault and fail closed.

Apply observations to the store as follows:

- `resting` keeps the wire `remaining_count` and a monotonic `fill_count`;
- `canceled` maps to local `canceled` with `remaining_count=0`;
- `executed` maps to local `filled` with `remaining_count=0`;
- no observation creates a cash or position entry without a fill event;
- `closed_unknown` represents a REST-proven absence without a terminal
  payload. It closes the execution slot but is not a confirmed cancellation;
- counts remain nonnegative and never decrease.

Run periodic reconciliation in this order:

1. Load a complete snapshot of open orders, fills, and positions.
2. Resolve pending and unknown intents.
3. Ingest fills.
4. Ingest strategy-owned open orders.
5. For each acknowledged local open order missing from the snapshot, call
   `get_order`.
6. Apply the terminal observation. If the API authoritatively reports no
   order, apply `closed_unknown`.
7. If REST or auth fails, keep the local open state and enter safe mode.
8. Compare positions, then write the checkpoint.

An external terminal observation frees the working slot. It does not create a
strategy `canceled` record.

### 4. Fix executor policy

Change the decision type:

```python
class KalshiTickDecision:
    yes_fair: Decimal | None
    no_fair: Decimal | None
```

Make `_blocked` and `lift_kalshi_tick` use `None`, not `0` and `1`.
`_exit_target` returns `None` if the held side has no fair. A later valid fair
restores the existing reduce-only exit behavior.

Return a typed result from `KalshiExecutor.sync`. Use this sequence:

1. Resolve pending and unknown intents.
2. Read the occupying order from the store.
3. If the ticker, book, or session cannot publish, cancel the occupying order
   and do not place a replacement.
4. If no target exists, apply the same cancel behavior.
5. If the order matches the target, do nothing.
6. Otherwise, confirm the old cancel before placing the replacement.
7. If cancel fails or remains unresolved, keep the slot and do not place.
8. Store the place ACK before emitting `KalshiPlacedUpdate`.

A missing, one-sided, or crossed book always enters the cancellation branch.
Replace the existing test that expects no cancel for an invalid pair.

### 5. Separate the feed loop from Kalshi execution

Remove `await runtime.quote` from the feed loop.

Add `src/live_paper/kalshi_overlay.py` for match-level Kalshi work:

- market resolution and binding;
- decision calculation;
- signal journal writes;
- nonblocking actor submission;
- execution-update draining;
- feed-timeout requests;
- stop and fence;
- the final Kalshi snapshot.

Keep only facade calls in `MatchWorker`. Before a new signal, drain completed
execution updates. Submit the new decision without `await`. During quiesce,
await the typed stop result before closing the journal.

A feed-timeout command must:

1. Discard a pending quote that is not newer than the timeout.
2. Wait for a write that has already reached the wire.
3. Cancel all strategy-owned orders for that ticker.
4. Prove their absence with a scoped REST fence.
5. Process a newer fresh decision only after the fence succeeds.
6. Block the full account if the fence is unproven.

Ticker stop must:

1. Mark the ticker as closing and reject new submissions.
2. Delete its pending quote.
3. Wait for the current actor command.
4. Resolve pending and unknown places.
5. Cancel every strategy-owned order for the ticker.
6. Run a scoped REST fence.
7. Capture the final book pair.
8. Retire the orderbook subscription.
9. Call `session.retire_ticker` in `finally`.

`session.retire_ticker` removes only transient state:

- the book and its generation;
- resubscribe requests;
- ticker-level write blocks;
- client-order generation;
- runtime tasks and orientation;
- an unused fee reference.

Keep orders, fills, cash, and positions in SQLite. If the fence is unproven,
keep the account-wide block after removing the book.

Start Kalshi stop as a task, then start Polymarket cancel and fence work
without waiting for Kalshi. Await both before closing the shared journal. A
Kalshi error returns an unproven `KalshiStopResult`; it must not skip or delay
the start of Polymarket cleanup.

### 6. Split the oversized modules

Add `src/live_paper/kalshi_books.py`. Move these responsibilities out of
`kalshi_session.py`:

- `_TickerBook`;
- snapshot, delta, and generation state;
- book-pair derivation;
- stale and unready detection;
- active ticker retirement.

Keep account safety, ownership, reconciliation, and fencing in the session.

Move the pure decision functions from `kalshi_runtime.py` to
`src/live_paper/kalshi_decision.py`.

After the move:

- keep `match_worker.py` below 900 lines;
- keep `kalshi_session.py` below 800 lines;
- keep every production Python file below 1000 lines;
- remove old forwarding wrappers that have no caller.

Update `../dota_2_model/docs/live-paper.md` with the new actor, coalescing,
feed-timeout fence, ticker retirement, missing-fair hold, invalid-book cancel,
terminal reconciliation, and journal semantics.

## Test the result

The finished implementation must cover these cases:

- Blocked Kalshi fee, read, or place calls do not delay the next feed event.
- Feed timeout during place ends in proven absence or an account-wide block.
- A newer fresh decision runs only after the timeout fence.
- Polymarket cleanup starts while Kalshi stop waits for REST.
- Safe mode sees and cancels a place that finishes after the trip.
- A retired ticker is absent from active, reopen, and resubscribe collections.
- A cancel ACK, unknown-cancel resolution, or fence creates one
  `KalshiCanceledUpdate`.
- A cancel failure creates no canceled update.
- External cancellation closes the store row without becoming a strategy
  cancel.
- Executed and partially filled orders update order state without fabricated
  cash entries.
- A missing REST order cannot remain a phantom `resting` order.
- Missing fair produces the same hold behavior for long YES and long NO.
- Invalid books cancel both entry bids and reduce-only asks.
- Paper and live use the same actor and lifecycle path.
- Repeated stop and teardown calls are idempotent.
- Actor execution and safe-mode execution cannot send concurrent writes.
- One failed actor command does not stop later commands.

From `../dota_2_model`, run the targeted suite first:

```bash
rtk env UV_CACHE_DIR=/tmp/codex-uv-cache PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --frozen --group backtest python -m pytest -q -p no:cacheprovider \
  tests/test_live_paper_kalshi_*.py \
  tests/test_live_paper_match_lifecycle.py \
  tests/test_live_paper_wallet_host.py \
  tests/test_kalshi_execution_smoke.py
```

Then run the full checks:

```bash
rtk make test
rtk make lint-all
rtk git diff --check
rtk wc -l src/live_paper/*.py
```

Before `make lint-all`, add new files to the Git index or run Ruff and
basedpyright directly on them. The repository's pre-commit command skips
untracked files.

The work is complete when:

- all tests pass;
- Ruff format, Ruff check, and basedpyright pass;
- no production source file exceeds 1000 lines;
- a failed cancel never produces a journal `canceled` entry;
- a proven feed-timeout or stop fence leaves no strategy-owned open order for
  the ticker in either the store or REST;
- an unproven fence blocks every later Kalshi place in the process.

## Preserve compatibility and defer rollout

- Keep the SQLite DDL unchanged. Existing `kalshi.db` files must open without
  migration. The new `closed_unknown` value uses the existing TEXT column.
- Keep the JSONL record shape unchanged. Only the accuracy of `placed` and
  `canceled` changes.
- Do not change `dota-map.toml`, environment variables, order sizes, or
  `KALSHI_TRADING`.
- Do not pull or restart the VPS as part of the code change without a separate
  request.
- For an approved rollout, keep the current trading mode and restart the
  container because Python files changed. Check for `fence_unproven`, actor
  command failures, a growing backlog, and open orders after completed maps.
