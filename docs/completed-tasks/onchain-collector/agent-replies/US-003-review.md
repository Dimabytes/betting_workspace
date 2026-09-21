# US-003 review — Planner, durable ledger, and day publication

Commit `675a368` in `polymarket-collector`. US-002 contract holds: `OnchainRpc.run` stays `Effect<A, RpcError>`, skip is `unlessCommitted`, `markCommitted(range)` takes no receipt (scan context is bound by `forDay`). Compact `Manifest` is untouched. No file crosses 1k lines.

The pipeline shape is right: fingerprint-scoped JSONL ledger, no global cursor, manifest-last publish, per-day error isolation. Two defects will bite on real days.

---

## 1. Sort check is a string-joined key, not the tuple order it claims

`validateOnchainParquet` enforces “non-decreasing `(block_number, transaction_index, log_index, contract_address, mirrored)`” by `join("|")` and `<` on the resulting string:

```200:213:../polymarket-collector/src/onchain-validate.ts
                const orderKey = [
                  cellNumber(cells[COL_BLOCK_NUMBER]),
                  cells[COL_TX_INDEX],
                  cells[COL_LOG_INDEX],
                  cells[COL_CONTRACT],
                  cells[COL_MIRRORED] === true ? 1 : 0
                ].join("|")
                if (prevKey !== null && orderKey < prevKey) {
                  return yield* fail(
                    "row.order",
                    `row ${rows}: sort order regressed`
                  )
                }
```

That is not the writer’s `ORDER BY`. `"100|9|0|0x…|0"` vs `"100|10|0|0x…|0"` compares `"9"` to `"1"` and rejects a correctly sorted file. Same for `log_index` 9→10, which is normal once a block has two fills.

The compact validator compares the numeric field directly (`parsed.timestampUs <= previousTimestampUs`). Do the same here: keep the previous tuple and compare component-wise (numbers numerically, `contract_address` lexicographically, `mirrored` as 0/1). `join` is a set-membership trick, not an order.

This is untested in the direction that matters. Publish tests cover timestamp/asset/duplicate, not sort, and every happy-path file has one row. A two-row file with `transaction_index` 9 then 10 is the check that fails if this stays.

Until this is a real tuple compare, any token-day with ≥10 txs or logs in one block will fail validation after a correct DuckDB COPY and never publish.

---

## 2. `scanChunk` writes an artifact for every catalog entry, not the day’s publishable set

```138:157:../polymarket-collector/src/onchain-day.ts
  for (const entry of args.catalog.entries.values()) {
    const root = rootOf.get(entry.game)
    if (root === undefined) continue
    const rows = yield* fillRowsForToken(
      fills,
      entry,
      args.blockTsCache,
      matchedByTx
    )
    const file = path.join(
      root,
      ".onchain",
      "chunks",
      args.date,
      `${args.chunk.from}-${args.chunk.to}`,
      `asset_id=${entry.tokenId}.json`
    )
    yield* fs.makeDirectory(path.dirname(file), { recursive: true })
    yield* publishJsonFile(file, rows.map(encodeRowLine))
  }
```

Empty artifacts for tokens that *will* be published are the right integrity model (missing committed artifact → `OnchainIntegrityError`). Writing them for the entire historical catalog is not. `collectOnchainDay` already has `couldTrade` per game. `publishGameDay` only reads that set. Everything else is an atomic tmp+fsync+rename of `[]` per (token × chunk), then deleted after both manifests.

Pass the day’s publishable entries into `scanChunk`. Artifact paths, integrity, and publish then describe the same set. Closed markets from 2020 do not belong in `.onchain/chunks/<date>/`.

Do not “fix” this by skipping empty files for publishable tokens — that reopens the missing-artifact hole the integrity error exists to catch.

---

US-002 seam, ledger scoping, hole/no-cursor, crash-before-commit / after-commit / between-games, and new-token rescan are in the right modules. Fix the comparator before this ships; shrink the artifact set to the publishable tokens so the resume payload stays honest.
