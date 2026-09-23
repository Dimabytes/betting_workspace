# Brief: clock-grok — independent look-ahead audit

Your name: `clock-grok`. Report: `$R/reports/clock-grok.md`. Work dir: `$R/work/clock-grok/`.

Two other agents work on timing (code audit and lag measurement). Work independently.

Goal: find any look-ahead (information from after the moment live could act) or
look-behind anywhere in LoL training or backtest compared with live.

1. From first principles, on one wall-clock timeline, decide which is right for live:
   training current mid at `state_wall_us + 0` (current code after `332e1c17`) or at
   `state_wall_us + ~10 s` (before). Pin down the semantics of livestats `rfc460Timestamp`,
   GRID `occurredAt` and `received_at_utc`, and the moment the live bot reads the book.
2. Measure one or two maps by hand, end to end: a LoL live map from 2026-09-14..09-20 that
   also has livestats locally. Event by event (kills), with timestamps from both sources and
   the Polymarket mid around each kill (`session.jsonl` signal rows).
3. Check the backtest's LoL signal timing and the prior for any future information. Check
   the `second` alignment (spawn offset between livestats and GRID).
4. Estimate how much of the LoL backtest profit can come from timing optimism: e.g., what an
   extra 8–10 s of delay does to the backtest BUY markout at 30 s and to PnL.
