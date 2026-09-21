# US-001 fix pass — review findings

Commit: `polymarket-collector` `a6a7ce4` — `fix: [US-001] - Typed decode errors and single onchain column spec` (6 files, +220/−259). `yarn check` green: typecheck, Effect diagnostics, oxlint, 492/492 tests. `feature.json` US-001 stays `passes: true`.

## Fixed (all four findings)

### 1. Decode failure channel is typed, not throws

- `parseRpcLog` now returns `Effect<RpcLog, DecodeError>` via `decodeRecord("RawRpcLog", RawRpcLog)` + `Effect.map` wire→`RpcLog` — the existing journal/sidecar pattern. ABI word slicing stays pure.
- `fillRowsForToken` returns `Effect<ReadonlyArray<OnchainFillRow>, OnchainDecodeError>` (new tagged error). Missing block timestamps are collected across all fills and fail once with the sorted block list — a coverage bug US-002/US-003 can `catchTag` instead of a fiber defect. No silent drops.

### 2. Decimal helpers

- `formatUnits` deleted. `amount`/`taker_fee` call `formatPlain(BigDecimal.make(raw, 6))` — normalize == Python rstrip, verified by row-level assertions ("0.19", "1") unchanged.
- `renderMicros` moved to `schema/decimal.ts` as `formatFixed6` (fixed 6-decimal, trailing zeros kept) with its own tests; `decimal.ts`'s "only place" comment now covers both renderers. `priceMicros` bigint half-even stays in decode.
- `block_timestamp_us` uses `MICROS_PER_SECOND`, no longer `USDC_UNITS`.

### 3. One spec generates the wire vocabulary

`schema/onchain.ts` now holds `ONCHAIN_SPEC` — a `keyof OnchainFillRow`-keyed record of `{sql, duck}` in declaration order — and derives `ONCHAIN_COLUMNS`, `ONCHAIN_TABLE_SQL`, `ONCHAIN_BIND_TYPES` from it. `satisfies Record<keyof OnchainFillRow, …>` makes a missing/extra/typo'd column a type error; SQL and bind order can no longer drift from the row type.

### 4. Single sort owner

Decoder's `rows.sort` deleted; rows emit in fill order and the writer's `COPY … ORDER BY` (already pinned by writer tests, `mirrored false` before `true` included) owns file order. The decoder sort test was replaced by the `OnchainDecodeError` coverage test; projection assertions unchanged.

## Skipped

Nothing substantive. Nits taken or consciously left:

- `decodeSync` on literal constants in tests stays sync (pre-existing `replay.test.ts` pattern; diagnostics reports them as suggestions only).
- `ONCHAIN_SPEC` as a keyed record rather than a positional array: `satisfies` enforces exact key coverage at compile time, which a tuple form cannot; order still pinned against the real Telonex parquet in tests.

## Files touched

`src/onchain-decode.ts`, `src/onchain-decode.test.ts`, `src/onchain-writer.ts`, `src/schema/onchain.ts`, `src/schema/decimal.ts`, `src/schema/decimal.test.ts`.
