# US-003 fix report — review pass

Status: done. Both review findings were real; both applied. `yarn check`
green (567 tests); committed in `polymarket-collector` as `4ebca5b`.
`feature.json` US-003 stays `passes: true`; progress.txt appended. Not pushed.

## Finding 1 — sort check compared a joined string, not the tuple — APPLIED

`validateOnchainParquet` built `[block_number, tx_index, log_index,
contract, mirrored].join("|")` and compared with `<`. `"100|9|…"` vs
`"100|10|…"` compares `"9"` to `"1"` → any token-day with ≥10 txs or logs in
one block would fail validation after a correct `COPY ORDER BY` and never
publish.

Fix (`src/onchain-validate.ts`): extracted `onchainOrderLess` — a strict
lexicographic compare over a typed 5-tuple `[number, number, number, string,
number]` (numbers numerically, contract lexicographically, mirrored as 0/1)
and used it in the stream loop instead of the joined key. The join stays only
where it belongs — the set-membership duplicate key.

Tests (`src/onchain-publish.test.ts`):
- `two rows in one block with tx indexes 9 then 10 publish fine` — the
  reviewer's exact false-positive case through `publishGameDay`.
- `onchainOrderLess compares the tuple component-wise` — direct unit test,
  both directions, tie-break fields, equality.

A negative end-to-end case (unsorted staged file → `row.order` fail) is not
reachable through `publishGameDay` — the writer's `COPY ORDER BY` always
sorts — which is why the comparator is exported and pinned directly.

## Finding 2 — artifacts written for the whole catalog — APPLIED

`scanChunk` wrote one canonical-JSON artifact per catalog *entry*, so closed
historical markets got dead `[]` files per (token × chunk). `publishGameDay`
only reads the day's publishable set.

Fix (`src/onchain-day.ts`): `scanChunk` now takes
`entries: ReadonlyArray<CatalogEntry>` — the day's publishable entries,
computed once in `collectOnchainDay` as `catalog.entries` filtered by the
could-trade union. The `wanted` set for `collectMakerFills` is the publishable
tokenIds plus their siblingTokenIds (sibling fills produce mirrored rows).
Every publishable token still gets an artifact per scanned chunk — empty
ones included — so the committed-chunk-missing-artifact integrity error is
unchanged.

Test (`src/onchain-day.test.ts`): `setup` now also writes a closed lol market
(`closedAt` in the past → non-publishable). The between-games crash test
asserts, while chunk staging survives: `asset_id=333.json` exists and
`asset_id=777.json` does not — artifact set equals publish set.

## Verification

`yarn check`: typecheck 0 errors; diagnostics 0 errors / 5 pre-existing
suggestions; oxlint 0/0; vitest 47 files / 567 tests (publish 8, day 9).
Pre-commit hook re-ran it during `git commit` — green.

## Commit

`4ebca5b` `fix: [US-003] - Tuple sort check; publishable-only chunk artifacts`
(4 files, +123/−21). On `main`, not pushed.
