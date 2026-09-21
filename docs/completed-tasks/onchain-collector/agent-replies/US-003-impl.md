# US-003 implementation report — Planner, durable ledger, and day publication

Status: done. `yarn check` green; committed in `polymarket-collector` as
`675a368`. `current-task/feature.json` US-003 `passes: true`; progress.txt
appended. Not pushed.

## Files changed (polymarket-collector)

- `src/schema/onchain.ts` — `OnchainFillRowLine`/`OnchainFillRowLines` wire
  codec (the two bigint columns serialize as decimal strings) with
  `encodeRowLine`/`decodeRowLine`.
- `src/versions.ts` — `ONCHAIN_DECODER_VERSION = 1`.
- `src/schema/onchain-manifest.ts` — on-chain channel manifest schema:
  files[] (path/assetId/rows/min-max ts/bytes/sha256), coverage (chunks,
  tokensCovered, tokensExcluded with reason, controlBlocks, block bounds,
  finalizedHead), provenance (catalog fingerprint, decoder version, scans),
  `status: "ready"`. Compact `Manifest` still rejects `onchain_fills`.
- `src/onchain-catalog.ts` — conflict claim windows: contradictory or
  asymmetric sibling pairs are excluded from `entries` and recorded as
  `conflicts`; `couldTradeDuring`/`conflictCouldTradeDuring` conservatively
  include a token unless `closedAt` precedes the window or `startAt` is at/
  after its end (mid-window `closedAt` still counts — it could have traded
  earlier).
- `src/onchain-days.ts` — ready resolution now carries `controlBlocks`
  (from/to number+hash pairs agreed by both endpoints).
- `src/onchain-ledger.ts` — durable `OnchainLedgerStore` via
  `openOnchainLedger(stateDir)`: append-only `<stateDir>/ledger/<date>.jsonl`
  (write+fsync per line, unterminated tail dropped as torn), commits carry
  date/inclusive bounds/chainId/addresses/decoderVersion/catalogFingerprint/
  controlBlocks/finalizedHead/committedAtUs — including empty ranges;
  `isCommitted` gates on (range, fingerprint, decoderVersion); torn/corrupt
  lines fail loudly. `saveCatalogSnapshot`/`loadCatalogSnapshot` persist the
  fingerprint→payload mapping under `<stateDir>/catalogs/`; fingerprint is
  the canonical-JSON sha256 of the snapshot payload. `unlessCommitted`
  unchanged: skip iff the exact range is committed.
- `src/onchain-config.ts` — `ONCHAIN_START_DATE` (UtcDate decoded, real
  `utcDayBoundsUs` rejects e.g. 2026-02-30), `ARCHIVE_ROOT_DOTA`/`_LOL`,
  `ONCHAIN_STATE_DIR`, `ONCHAIN_CHUNK_BLOCKS` (default 10000, positive),
  `DUCKDB_MEMORY_LIMIT` (default 1024MB via the exported `config.ts` regex).
- `src/onchain-plan.ts` — `couldTradeTokens`, `coveredTokens` returning
  `{forDay, inChunk}` (per-chunk coverage unioned across fingerprints at the
  current decoder version — a day-level "scanned" flag is never trusted),
  `readOnchainManifest` (unreadable → warn + treated absent), and
  `listUnfinishedDays`: `[startDate, today)` ascending, never today, day
  complete iff both manifests cover their game's could-trade set, `blocked`
  when a conflict token could trade (diagnostic only — no RPC).
- `src/onchain-validate.ts` — `validateOnchainParquet`: DuckDB session rooted
  under `.onchain`, `read_parquet(..., hive_partitioning=false)`, exact
  column names/order + pinned DuckDB type tree, `asset_id` equality, window
  `[startUs,endUs)`, unique `(chain_id, contract_address, tx_hash, log_index,
  asset_id)`, non-decreasing `(block_number, transaction_index, log_index,
  contract_address, mirrored)`; returns rows/min/max/bytes/sha256.
- `src/onchain-publish.ts` — `publishGameDay`: per token, read every chunk's
  deterministic artifact (missing artifact on a committed chunk →
  `OnchainIntegrityError`), concatenate, wipe stale `.onchain/publish/<date>`,
  stage `<token>.parquet`, validate, mkdir partition, atomic rename; excluded
  tokens recorded as conflict/not_tradeable/not_covered; staging dir removed
  before the manifest — which is written last, atomically.
- `src/onchain-day.ts` — `splitChunks`, `scanChunk` (`fetchLogsRange` halving
  inside the chunk, catalog-filtered maker fills, per-day memoized block ts,
  canonical-JSON artifact per catalog entry — including empties — under
  `.onchain/chunks/<date>/<from>-<to>/`), `collectOnchainDay` (resolve →
  pending short-circuit / unsupported propagate → coverage → concurrency-4
  `unlessCommitted`-wrapped scans of only chunks some uncovered token needs →
  `markCommitted` after durable writes → coverage recheck (still-uncovered =
  integrity failure) → dota then lol publish, skipping a game whose manifest
  already covers the publishable set → `.onchain/chunks/<date>` removed only
  after both manifests), `runOnchainPass` (one catalog load + snapshot per
  pass, `listUnfinishedDays`, per-day `published|pending|blocked|unsupported|
  failed` isolation).
- `src/onchain.testing.ts` — promoted `chainResponder`/`scriptedRpc`/
  `fakeHttpClient`/`makeTestEndpoint`/`blockJson`/`rpcResult`/`rpcErrorBody`/
  `httpResponse`, `orderFilledLog`/`ordersMatchedLog`, `makeSidecar`,
  `writeSidecar`, `withDualArchiveRoots`, `makeOnchainRow`.

## Tests

42 new across `schema/onchain`, `schema/onchain-manifest`, `onchain-config`,
`onchain-ledger` (persistence, fingerprint/version scoping, torn tail,
snapshot round-trips), `onchain-plan` (ordering, completeness gates,
conflicts, per-chunk coverage), `onchain-publish` (integrity error on missing
artifact, exclusions, manifest-last, byte-stable republish), `onchain-day`
(end-to-end both games, pending, failed-chunk rerun fetches only uncommitted
ranges, `markCommitted` failure after append keeps durable commit + identical
ledger bytes, between-games crash rerun does zero `eth_getLogs` and leaves
the surviving manifest untouched, new-token rescan byte-identical for
unchanged tokens, committed hole proves no global cursor, unsupported day
isolation).

`yarn check`: typecheck 0 errors; Effect diagnostics 0 errors / 0 warnings /
5 messages (pre-existing); oxlint 0/0; vitest 47 files / 565 tests passed.
Pre-commit hook re-ran the check during `git commit` — green.

## Commit

`675a368` `feat: [US-003] - Planner, durable ledger, and day publication`
(23 files, +3539/−100). On `main`, not pushed.

## Leftover risks / notes for US-004+

- `runOnchainPass` is library-only — US-004 owns the process entry, lock,
  cadence loop, import command, and Compose wiring.
- `decoderVersion` is a parameter with `ONCHAIN_DECODER_VERSION` default; a
  future decoder bump rescans everything under the new version while old
  commits stay provenance (`scans` in the manifest lists fingerprints).
- Chunk coverage trusts persisted snapshots; a missing snapshot file reads
  as "no coverage" and a post-scan survivor fails as integrity loss — loud,
  never silently partial.
- Between-games reruns rely on the manifest-covering skip to keep surviving
  manifests byte-identical; a manifest deleted by hand after cleanup is
  integrity loss (artifacts already removed), by design.
- Chunk scan concurrency is fixed at 4 inside `collectOnchainDay`; expose a
  config knob only if the live day shows RPC pressure.
