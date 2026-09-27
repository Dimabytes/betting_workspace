# Follow-up brief: fill-grok — market data ingestion and provenance (books, trades, onchain fills)

Report: `$R/reports/fill-grok-books.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.
Read `$R/work/shared/notes-from-orchestrator.md` (N3: market cache builds only when the file is missing).

Every dataset row, label, and backtest fill depends on the book data. Nobody has audited how it is captured.

## Scope

- `../polymarket-collector` (TypeScript, read-only): read `AGENTS.md` and
  `docs/polymarket_dota_archive_contracts.md` first. How book snapshots are captured (WS subscription,
  snapshot vs deltas, what `book_snapshot_full` contains), the timestamp field (exchange time vs local
  receive), reconnect/gap handling, dedupe, tick-size changes, compaction to parquet (`compact-*`,
  `COMPACTION_HOUR_UTC`), onchain fills (block time, missed blocks, reorgs), metadata sidecars.
- `$E/scripts/sync_collector_parquet.py`, `scripts/rsync_pull.py`: books come over `--ignore-existing`.
  **Main lead:** a day file pulled before the VPS finished writing/compacting it stays truncated on the Mac
  forever, and `build_market_data` never rebuilds an existing cache (N3). Check the "today's file is skipped"
  logic vs UTC day boundaries and compaction timing. Measure it: compare local day-file sizes/row counts/max
  timestamps with what a complete day should have (e.g. last snapshot near 23:59 UTC; gaps at the end of the
  day; days whose local file ends early). List affected days and maps (catalog maps whose market window falls
  in a truncated tail).
- `$E/data/raw/telonex/polymarket/{book_snapshot_full,trades,onchain_fills,_control,_backtest_cache}`: which
  days come from paid Telonex vs our collector (the provenance boundary), and whether semantics change across
  it (timestamp base, snapshot frequency, depth, one-sided rows). Readers:
  `src/shared/utils/telonex_book.py` (`parse_best_bid_ask`, `load_token_book`), `telonex_capture.py`,
  `src/backtest/telonex_local.py` (L2 replay input for Nautilus). Look for level-sorting bugs (best bid = max
  price?), string/float parsing, duplicate timestamps, time zones.
- Per-map reads only; never scan the 52 GB tree whole (list files + read parquet metadata instead).

Deliver: a provenance table (date ranges × source × semantics), the truncated-day list with affected maps,
and findings with severity.
