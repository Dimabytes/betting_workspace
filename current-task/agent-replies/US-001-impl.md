# US-001 — Catalog, ABI decode, and ONCHAIN_SCHEMA parquet

Status: done. `yarn check` green (typecheck, Effect diagnostics, oxlint, 491/491 tests). `feature.json` US-001 `passes: true`.

## Commit

- `polymarket-collector` `6467487` — `feat: [US-001] - Catalog, ABI decode, and ONCHAIN_SCHEMA parquet` (13 files, +2816/−11). Not pushed.

## Files changed (polymarket-collector)

Added:

- `src/schema/onchain.ts` — `ONCHAIN_COLUMNS` (26 cols, Telonex order) + `OnchainFillRow` (bigint for int64, number for int32/uint8, boolean `mirrored`, strings elsewhere; token ids are decimal strings, never JS numbers).
- `src/onchain-catalog.ts` — reads `metadata/markets/<condition_id>.json` from both game archive roots via `readMarketSidecars`; token → game/condition/outcome-index/sibling index; conflicting tokens excluded with diagnostics (not fatal); `couldTradeDuring(startUs,endUs)` includes sidecars without observation timestamps.
- `src/onchain-decode.ts` — raw `eth_getLogs` parse + ABI decode of `OrderFilled`/`OrdersMatched` for CTF Exchange V2 and Neg-risk V2; OrdersMatched lookup keyed by `(contract_address, tx_hash)` (NUL-separated key); taker-aggregate fills dropped per emitting contract; own + mirrored rows; price = bigint six-decimal half-even with fixed trailing zeroes; amount/fee strip trailing zeroes; missing OrdersMatched → zero bytes32 taker order hash, taker side = fill side.
- `src/onchain-writer.ts` — `writeOnchainFills(archiveRoot, rows, outputPath)`: one (token, day) SNAPPY Parquet per call via DuckDB appender/data chunks; ORDER BY `block_number, transaction_index, log_index, contract_address, mirrored`; no PARTITION_BY; session workdir `.onchain`; typed `OnchainWriterError`.
- `src/onchain.testing.ts` — log/row/sidecar fixture builders + `withDualArchiveRoots`.
- Tests: `src/onchain-catalog.test.ts`, `src/onchain-decode.test.ts`, `src/onchain-writer.test.ts`.
- `test/fixtures/onchain/` — `telonex-8rows.parquet`, `telonex-empty.parquet` (real Telonex files), `getlogs-94039500.json` (real Alchemy capture; verified byte-parity against the Python decoder beforehand).

Changed:

- `src/duckdb-session.ts` — optional `workRootName` (default `.compaction`); on-chain passes `.onchain` so compactor sweeps cannot touch it.
- `docs/learnings.md` — prepended entry: Parquet parity is physical types + converted_type, not logical_type (PyArrow writes `logical_type` StringType/IntType; this DuckDB build writes `converted_type` UTF8/UINT_8/INT_*; readers honor both).

Not touched: `src/schema/manifest.ts`, `compose.yaml`, `src/config.ts`, `src/main.ts`, `src/parquet-writer.ts`, `repos/`.

## Tests

New coverage: catalog conflicts as diagnostics + trading-window edges; OrderFilled/OrdersMatched decode incl. cross-contract tx-key isolation, taker-aggregate drop, mirrored-row projection, half-even price ties, missing-match fallback, 77-digit token ids via bigint; writer round-trip, empty-file schema, sort order, and schema parity against both real Telonex fixtures (physical types + DESCRIBE readback). The real-fixture decode test reproduces the 4 rows Python produced from the captured transaction.

## Notes / leftover risks

- DuckDB-written files differ from PyArrow only in schema annotation flavor (`converted_type` vs `logical_type`); physical types and reader-visible types are identical. Recorded in learnings.
- `yarn diagnostics` reports 3 info-level `schemaSyncInEffect` suggestions in `onchain-decode.test.ts` (test-only `decodeSync` on constants); 0 errors/warnings.
- `yarn prepare` had to run once after fresh `yarn install` to patch oxlint (effecttsgo plugin); environment quirk, not a code issue.
- US-002+ untouched: no RPC client, ledger, publication, compose, or import code added.
