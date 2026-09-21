# US-001 review — Catalog, ABI decode, and ONCHAIN_SCHEMA parquet

Commit `6467487` in `polymarket-collector`. Catalog / writer / DuckDB session isolation are the right shape. Decode-vs-Telonex tests and the dual-archive catalog are doing the job the step asked for. Approval still fails: the decode path does not use this repo’s error and decimal boundaries, and the 26-column wire format is declared four times.

## 1. Decode’s failure channel is throws. US-002 cannot attach typed invalid-payload / coverage errors to it.

`parseRpcLog` uses `Schema.decodeUnknownSync` and throws `SchemaError`. `fillRowsForToken` throws a bare `Error` for a missing block timestamp. Everything else in this module is total (`null` for short ABI, skip for zero shares).

That is the wrong boundary in this codebase:

- `schema/decode.ts` already exists so external JSON becomes `DecodeError`, not a fiber defect.
- AGENTS.md / the on-chain design require tagged errors and `catchTag`, not ambient throws.
- US-002’s typed invalid-payload error and US-003’s coverage failure have nowhere to land. Every `Effect.gen` caller will wrap this in `Effect.try` or let it become a defect.

`decodeRecord("RawRpcLog", RawRpcLog)` plus a pure wire→`RpcLog` map is the existing pattern (journal, checkpoint, metadata sidecars). Keep arithmetic and ABI word slicing pure; lift the RPC JSON gate.

For the missing timestamp: do not throw `Error` out of an otherwise-pure projector. Either `Effect.fail` a small `OnchainDecodeError` or return `{ rows, missingBlocks }` so the planner can fail the chunk as coverage. Silent drop is still wrong; an untyped throw is also wrong.

## 2. `formatUnits` is a second numeric-string builder. `decimal.ts` already forbids that.

`src/schema/decimal.ts` is explicit: `formatPlain` is the only place in `src/` allowed to hand-build a numeric string. The US-001 plan already named the reuse: amount/fee = `formatPlain(BigDecimal.make(raw, 6))` (same `rstrip` as Python). The implementation instead added `formatUnits` next to the ABI decoder.

That is avoidable canonical-helper duplication. Delete `formatUnits`. Call `formatPlain`.

Keep the bigint half-even path for price — `formatPlain` strips trailing zeros and Telonex keeps `"0.760000"`. That renderer can stay, but:

- Do not multiply `block_timestamp_us` by `USDC_UNITS`. That constant is a money scale. Timestamp conversion sharing it means a USDC decimal change silently corrupts time. Use a `MICROS_PER_SECOND` (or `1_000_000n` at the timestamp line).
- If `renderMicros` stays in decode, the `decimal.ts` “only place” comment is now a lie. Either acknowledge a second wire renderer or put fixed-scale formatting next to `formatPlain`. Do not leave two undocumented numeric regimes.

## 3. The 26-column vocabulary is four parallel lists. SQL/bind order can drift with no type error.

Source of truth today:

- `OnchainFillRow` field types
- `ONCHAIN_COLUMNS` names
- `ONCHAIN_TABLE_SQL`
- `ONCHAIN_BIND_TYPES`

`valuesOf` is `ONCHAIN_COLUMNS.map((c) => row[c])` — that part cannot reorder relative to the interface. The CREATE TABLE and DuckDB bind arrays are positional and independent. A swapped `log_index` / `transaction_index` in SQL or bind types still typechecks.

One spec (name + SQL type + DuckDB type) should generate the column list, `CREATE TABLE`, and bind array. `OnchainFillRow` stays the named wire row. That is the code-judo move: three lists disappear, Telonex order becomes one declaration.

`schema/parquet.ts` uses Schema tuples because those rows are also the RQ-6 JSON array. On-chain rows are not. A named spec is the better model here — but only if it is actually one spec, not four copies of 26.

## 4. `fillRowsForToken` sorts; `COPY` sorts again. The spec gave order to the writer.

Acceptance and the writer comment both say file order is `ORDER BY block_number, transaction_index, log_index, contract_address, mirrored`. The decoder still implements the same comparator and decode tests assert it.

Drop the in-memory sort. Writer tests already pin COPY order (including `mirrored false` before `true`). Decoder tests should pin field projection, not a second sort implementation that will diverge the next time someone edits one side.

---

No 1k-line files. `workRootName` on `openDuckDbSession` is the right shared-path change (default keeps compact callers untouched; on-chain must not live under `.compaction`). Catalog correctly reuses `readMarketSidecars`, treats conflicts as data, and does not last-write-wins like `sidecarsByToken` in replay/pipeline/validator. Dual-contract match key and per-emitter taker-aggregate drop are the right model.

Fix 1–3 before US-002 builds RPC on top of a throwing decoder and a duplicated decimal path.
