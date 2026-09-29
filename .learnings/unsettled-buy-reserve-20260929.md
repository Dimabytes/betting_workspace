# Unsettled BUY reserve, 2026-09-29

Part B keeps a venue-terminal BUY's money in `unsettled_buys` and its rung in the core as `gone` until BUY `CONFIRMED` covers the row. These notes are the incident and the API facts the reserve is built on. They are not a deploy record, not a measured post-deploy latency, and not a statement that part A is ready.

## Incident

Map `grid-3008273-m1` (LoL, Anubis Gaming vs TLN Pirates) on VPS `sun`, old code `8f0f3cbc` (`follow300-v6`). Result: 32 YES around 0.625, a manual sell of 31.99 at 0.12, about −$16.1.

From `core_trace.jsonl`, live logs, and the collector archive:

1. Fourth purchase. At 18:07:05–17 the market WS and the user WS went quiet. Rungs c3/c6/c7 filled at 18:07:12. The core canceled them as stale at 18:07:13. The venue answered `not found`, the core applied `CancelAck`, and that freed the rung, the reserve, and the episode. At 18:07:21 a new ladder c8–c10 was up. The c3/c6/c7 fills arrived via backfill at 18:07:22.
2. Could not sell. At 18:08:38–46 the venue rejected SELL at 0.60 five times with `crosses book`. The trader's book at 18:08:38.475 still showed ask 0.60 × 31.99, which was our SELL c30, already canceled at 18:08:38.351. The market WS did not deliver the removal. Other messages kept moving `local_ts` (18:08:38.887, 18:08:43.034), so the 5-second `stale_book` gate did not fire. The core treated its own ghost as a foreign ask and joined 0.60. The collector saw the real 0.60/0.62 at 18:08:48.6. This is the canceled-order ghost. It is not a claim that the market feed is repaired.
3. Halt. 6 errors on about 23 places, `error_rate` 0.26. The halt canceled the SELL as well. No exit for the rest of the map.

The chosen fix is maker-only exits, and a specific order's reserve held until that order's execution is accounted for.

## What holds what

- Money lives in SQLite `unsettled_buys`. It survives the worker and the restart. The account and map budgets read it. Ending the worker or finishing the map does not release the row.
- The rung lives in the core. Status `gone` occupies the level until `BuySettled`.
- Reserve is `max(0, qty - booked) * price`. Booked is BUY `MATCHED` and `CONFIRMED`. `MATCHED` lowers the reserve and does not resolve a positive qty. The row resolves when BUY `CONFIRMED` covers `qty` within half a share. `FAILED` puts the reserve back. `SUPERSEDED` is in neither sum, so it credits 0 and the reserve stays.
- Empty, malformed, negative, boolean, or non-finite `size_matched` is not proof of zero. Numeric or string zero is proof and resolves immediately. HTTP cancel, including `not found`, is not execution proof.
- Checkpoint schema stays 3. `gone` is stored as `unknown` with cancel reason `unsettled`. A restored cancel has no clock, so the log is `wait_ms=unavailable origin=restored`.
- Alert `unsettled_buy:<first 8 of venue_id>` does not release the money. Age is strictly over 600 seconds.

## API facts from the 2026-09-29 read-only check

These are from that investigation, not from this implementation run.

- `not found` proves the order is off the book. It does not prove zero execution.
- `get_order` returned None for filled, canceled, and nonexistent orders. REST shows live orders only. None is not execution proof.
- `get_trades` maker `matched_amount` matched user-WS `size_matched` on 14,180 BUY cancels: 413 partial, 13,767 zero, no discrepancies. On this map, c3 = 8.06, c8 = 8.19, c9 = 0.
- That comparison was of final states. It did not measure how long the trades API lags immediately after a match.
- On this map's journal, user-WS `CANCELLATION` preceded the HTTP ack by 41–46 ms (c9/c10 18:07:27.797 vs 27.843, c15 18:07:59.548 vs 59.589, c30 18:08:38.351 vs 38.392). A zero fill then closes on the next wake. Without the WS proof the row waits at least 60 seconds plus one reconcile, and the rung and the cash stay occupied the whole time.

## Deploy and rollback

Steps 001–004 deploy together, and only on a separate user command. Do not treat this file as that command.

Before a separately authorized rollback, `SELECT COUNT(*) FROM unsettled_buys WHERE resolved=0` on the live wallet. A non-zero count means wait for reconcile, or accept the old cancel behavior for those orders. Old code ignores the money rows. An `unknown` order with cancel reason `unsettled` is still waiting. Old replay cannot decode `CancelUnsettled` or `BuySettled`. Checkpoint load still works.

After a real deploy, check the trace for `CancelUnsettled` / `gone` and `BuySettled`, that open-row counts and reserve fall after reconcile, and the `wait_ms` distribution plus the delay from cancel to the next place on the same rung. Report restored timings on their own.

A hand update is not a timeout workaround:

```sql
UPDATE unsettled_buys SET resolved=1, qty=0 WHERE venue_id='<venue>';
```

Run it only after that order's on-chain activity is CONFIRMED and the credited size is 0. `SUPERSEDED` credited 0, so qty is 0. A cancel timeout is not a reason.
