# STEP-005 plan: the onchain service reconciles WS trades with onchain fills and flags silent feed stalls

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-005` (priority 5).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Build on the current HEAD (`9c9184e`, after STEP-001..004). Commit there. Do not push.
Figma or design links: none.

Before you start, read `polymarket-collector/AGENTS.md` and the top entries of `docs/learnings.md`.

Evidence (read-only, in `betting_workspace/runs/collector-bug-hunt-20261010/`): `FINAL_REPORT.md` §4.2 L-STALL, `reports/p2-ws-vs-onchain.md`.

## 1. The problem

On 2026-10-08 the WS feed went silent for about 2 minutes (16:45–16:47 UTC). 51 Dota trades and 2 LoL trades that settled onchain never reached the journal. The day manifest still says `quality: "complete"`, `processGaps: []`. Nothing inside the WS archive can tell a quiet market from a dead feed. The only independent signal is the chain.

The join is simple and proven (p2-ws-vs-onchain): WS `trades.trade_id` is the settlement tx hash, and it equals onchain `onchain_fills.tx_hash`.
- `A` = distinct `trade_id` in the day's WS `trades`.
- `B` = distinct `tx_hash` in the day's `onchain_fills`.
- `B \ A` = onchain txs with no WS trade.

Scattered single misses (about 0.06 % a day) come from upstream ticker coalescing and are not a feed failure. A stall shows up as a dense cluster of misses.

**The planner already ran the exact SQL from §4.2 on the real 2026-10-08 evidence, using the real manifests' file lists.** It reproduced the bug-hunt numbers:
- Dota: `wsTrades=35574`, `onchainTxs=35643`, `unmatched=74`, one window `16:45:29–16:47:35` with 51 misses.
- LoL: `15848 / 15856 / 8`, no window.
- Each game-day took about 80 ms.

Keep the SQL text as given.

## 2. Design (decided; follow it)

1. **New module `src/onchain-reconcile.ts`** with one exported pass function, `reconcileDays(roots)`. It never fails: every error is logged and the date is skipped. Inside the module, `reconcileDay` runs the DuckDB join for one (game, date) and writes the report.
2. **When a date is reconciled.** For each root (dota, then lol), list `<archiveRoot>/manifests/onchain/*.json` and take the stems that are a `UtcDate`, in ascending order. Skip a date when any of these holds:
   - `manifests/reconcile/<date>.json` already exists. It is never recomputed; deleting the file is how an operator forces a recompute.
   - the WS manifest `manifests/<date>.json` is absent (`readManifest` returns `None`). Its schema allows only `status: "complete"`, so a readable manifest is a complete one.
   - `readManifest` fails (corrupt file). Log WARN `onchain reconcile skipped {game, date, message}` and go on.
   - `readOnchainManifest` returns `None`. Its schema allows only `status: "ready"`.

   At most `RECONCILE_DAYS_PER_PASS = 7` attempts per game per pass. An attempt is a date with both manifests, whether the attempt succeeds or fails.
3. **Which files are read.** Read the file lists **from the two manifests**, not a glob:
   - WS: `manifest.parquet` entries with `channel === "trades"`.
   - onchain: `manifest.files`.

   Every `path` is relative to the archive root. Why not a glob:
   - DuckDB fails on a glob that matches nothing. A complete WS day with zero trades and onchain fills (the strongest stall signal) must still work.
   - A glob would also pick up stale partitions that the manifest no longer lists.

   An empty list becomes a typed empty subquery. Always pass `hive_partitioning=false`: the path holds `asset_id=…`, and the files have an `asset_id` column.
4. **Metrics.**
   - `wsTrades` = `count(DISTINCT trade_id)`.
   - `onchainTxs` = `count(DISTINCT tx_hash)`. Mirrored fills share a tx, so this counts the tx once.
   - Misses: per onchain tx without a WS `trade_id`, take `min(block_timestamp_us)`, sorted ascending.
   - `unmatchedOnchain` = number of misses.
   - `unmatchedRatio` = `unmatched / onchainTxs`, or `0` when `onchainTxs = 0`.
   - WS trades without an onchain pair (reverted txs, `A \ B`) are not reported.
5. **Windows.** Walk the sorted misses. A miss joins the current window when it is at most `120_000_000` µs after the window's last miss; otherwise it starts a new window. Keep only windows with at least 5 misses. `fromUs` and `toUs` are the first and last miss of the window.
6. **Output.** Decode the report strictly with a new schema `OnchainReconcile` (`src/schema/onchain-reconcile.ts`). Then write it atomically with `publishJsonFile` to `<archiveRoot>/manifests/reconcile/<date>.json` (`mkdir -p` the directory first). The WS manifest and its `status`/`quality` are not touched.
7. **Wiring in `runOnchainTick`.** Run the reconcile after `runOnchainPass` returns, every tick, even when the pass itself failed. Add the reports to `OnchainPassReport.reconciles`. **Also send the Telegram message when `reconciles.length > 0`.** Without that, the reports would almost never reach Telegram: the onchain day usually publishes on a different pass than the WS compaction, so the reconcile normally runs on a pass that published nothing and whose failure signature did not change.
8. **Telegram.** `formatOnchainPass` adds one line per reconcile, after the `day=` lines and before the `endpoint=` lines: `reconcile=<game>/<date> unmatched=<unmatchedOnchain>/<onchainTxs> windows=<n>`. When `n > 0`, the line starts with `STALL ` (a space follows). A reconcile failure is logged only, never sent.
9. **No RPC, no new env/config, no new dependency.** DuckDB comes from the existing `openDuckDbSession`, with `workRootName: ".onchain"` (the compact process sweeps `.compaction`, so the onchain service must not use it). The onchain process already provides `DuckDbSettings`.
10. **No new comments anywhere, tests included.** Delete the old comments in the symbols you edit (§4.4, §4.5).

## 3. Scope

In scope:
- New: `src/schema/onchain-reconcile.ts`, `src/onchain-reconcile.ts`, `src/onchain-reconcile.test.ts`
- Edit: `src/onchain-app.ts`, `src/telegram.ts`, `src/onchain.testing.ts`, `src/onchain-app.test.ts`
- Docs: `docs/polymarket_dota_archive_contracts.md` (new §13.1), `docs/learnings.md`, `docs/docker-compose.md` (one sentence)

Out of scope (do not do):
- Do not write anything into the WS manifest, and do not change `Manifest`, `OnchainManifest` or any `schemaVersion`.
- Do not recompute an existing reconcile file automatically (no mtime or generatedAt comparison).
- Do not add a log-based "offered" stall detector, book-hash checks, or RPC receipt checks for reverted txs.
- Do not add config/env knobs for 120 s / 5 / 7. They are module constants.
- Do not touch `runOnchainPass`, `listUnfinishedDays`, the publish path, compaction, or `esports-trader`.

Code rules (Effect diagnostics and lint run in `yarn check`):
- No `new Date`, `Date.now`, `JSON.parse` (use `Schema.fromJsonString`), `setTimeout` or `new Promise` in `src`.
- `catchUnfailableEffect` is an error: put `Effect.catch` only on effects that can fail.
- `verbatimModuleSyntax` is on: type-only imports use `import type` / `type`.
- No `!` non-null assertions in non-test `src`.
- Typed errors only. Every file stays under 1,000 lines.

## 4. Code changes

### 4.1 `src/schema/onchain-reconcile.ts` (new)

```ts
import { Schema } from "effect"
import { Microseconds, NonNegativeInt, SchemaVersion, UtcDate } from "./primitives.ts"

export const StallWindow = Schema.Struct({
  fromUs: Microseconds,
  toUs: Microseconds,
  unmatched: NonNegativeInt
})

export const OnchainReconcile = Schema.Struct({
  schemaVersion: SchemaVersion,
  date: UtcDate,
  game: Schema.String,
  wsTrades: NonNegativeInt,
  onchainTxs: NonNegativeInt,
  unmatchedOnchain: NonNegativeInt,
  unmatchedRatio: Schema.Number,
  stallWindows: Schema.Array(StallWindow)
})

export type StallWindow = typeof StallWindow.Type
export type OnchainReconcile = typeof OnchainReconcile.Type
```

The name avoids "reconcile" collisions with the stream's subscription reconcile in `stream.ts`.

### 4.2 `src/onchain-reconcile.ts` (new)

Use this structure. The SQL is verified; keep it as written.

```ts
import { Crypto, Effect, FileSystem, Option, Path, Schema } from "effect"
import { readManifest } from "./compaction.ts"
import { DuckDbSettings, openDuckDbSession, queryRows, sqlString } from "./duckdb-session.ts"
import { publishJsonFile } from "./fs/atomic.ts"
import type { CatalogRoot } from "./onchain-catalog.ts"
import { readOnchainManifest } from "./onchain-plan.ts"
import { decodeStrict } from "./schema/decode.ts"
import type { Manifest } from "./schema/manifest.ts"
import type { OnchainManifest } from "./schema/onchain-manifest.ts"
import { OnchainReconcile } from "./schema/onchain-reconcile.ts"
import { UtcDate } from "./schema/primitives.ts"

export const RECONCILE_DAYS_PER_PASS = 7
const STALL_GAP_US = 120_000_000
const STALL_MIN_UNMATCHED = 5

export class OnchainReconcileError extends Schema.TaggedError<OnchainReconcileError>()(
  "OnchainReconcileError",
  { stage: Schema.String, message: Schema.String }
) {}

const mapReconcileError =
  (stage: string) =>
  (error: unknown): OnchainReconcileError =>
    new OnchainReconcileError({
      stage,
      message: `${stage}: ${error instanceof Error ? error.message : String(error)}`
    })

const parquetSource = (files: ReadonlyArray<string>, empty: string): string =>
  files.length === 0
    ? `(SELECT ${empty} WHERE false)`
    : `read_parquet([${files.map((file) => `'${sqlString(file)}'`).join(", ")}], hive_partitioning=false)`

const stallWindowsOf = (unmatchedUs: ReadonlyArray<number>) => {
  const windows: Array<{ fromUs: number; toUs: number; unmatched: number }> = []
  for (const us of unmatchedUs) {
    const last = windows.at(-1)
    if (last !== undefined && us - last.toUs <= STALL_GAP_US) {
      last.toUs = us
      last.unmatched += 1
    } else {
      windows.push({ fromUs: us, toUs: us, unmatched: 1 })
    }
  }
  return windows.filter((window) => window.unmatched >= STALL_MIN_UNMATCHED)
}
```

`reconcileDay` (not exported) is an `Effect.fn("reconcileDay")` with these args:

```ts
{
  game: string
  archiveRoot: string
  date: UtcDate
  file: string
  trades: Manifest
  fills: OnchainManifest
}
```

It returns `Effect.fn.Return<OnchainReconcile, OnchainReconcileError, Crypto.Crypto | FileSystem.FileSystem | Path.Path | DuckDbSettings>`. Body:
1. Build the two sources with `path.join(args.archiveRoot, file.path)`:
   - `trades = parquetSource(args.trades.parquet.filter((file) => file.channel === "trades").map(...), "NULL::VARCHAR AS trade_id")`
   - `fills = parquetSource(args.fills.files.map(...), "NULL::VARCHAR AS tx_hash, NULL::BIGINT AS block_timestamp_us")`
2. Inside `Effect.scoped(Effect.gen(...))`, open the session:

   ```ts
   openDuckDbSession({
     spanName: "reconcileDay.openDuckDbSession",
     archiveRoot: args.archiveRoot,
     workRootName: ".onchain",
     mapError: mapReconcileError
   })
   ```

   Then run two `queryRows` calls with `mapReconcileError`:
   - stage `"count"`: `SELECT (SELECT count(DISTINCT trade_id) FROM ${trades}), (SELECT count(DISTINCT tx_hash) FROM ${fills})`
   - stage `"unmatched"`: `SELECT min(block_timestamp_us) AS block_us FROM ${fills} AS fill WHERE NOT EXISTS (SELECT 1 FROM ${trades} AS trade WHERE trade.trade_id = fill.tx_hash) GROUP BY tx_hash ORDER BY block_us`

   Return both row sets out of the scope, so the session closes before the file write.
3. DuckDB JSON returns BIGINT as **strings**. Convert with `Number(...)`:
   - `wsTrades = Number(counts[0]?.[0] ?? 0)`
   - `onchainTxs = Number(counts[0]?.[1] ?? 0)`
   - `unmatchedUs = misses.map((row) => Number(row[0]))`

   Microseconds are below 2^53, so this is exact.
4. Build the report:

   ```ts
   decodeStrict("OnchainReconcile", OnchainReconcile)({
     schemaVersion: 1,
     date: args.date,
     game: args.game,
     wsTrades,
     onchainTxs,
     unmatchedOnchain: unmatchedUs.length,
     unmatchedRatio: onchainTxs === 0 ? 0 : unmatchedUs.length / onchainTxs,
     stallWindows: stallWindowsOf(unmatchedUs)
   })
   ```

   Map its error with `mapReconcileError("report.decode")`.
5. `fs.makeDirectory(path.dirname(args.file), { recursive: true })` with `mapReconcileError("report.mkdir")`. Then `publishJsonFile(args.file, report)` with `mapReconcileError("report.publish")`. Return `report`.

`reconcileDays` (exported) is an `Effect.fn("reconcileDays")` that takes `roots: ReadonlyArray<CatalogRoot>` and returns `Effect.fn.Return<ReadonlyArray<OnchainReconcile>, never, Crypto.Crypto | FileSystem.FileSystem | Path.Path | DuckDbSettings>`:

```ts
const reports: Array<OnchainReconcile> = []
for (const root of roots) {
  const names = yield* fs.readDirectory(path.join(root.archiveRoot, "manifests", "onchain")).pipe(
    Effect.orElseSucceed((): Array<string> => [])
  )
  const dates = names
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.slice(0, -".json".length))
    .filter(Schema.is(UtcDate))
    .sort()
  let attempted = 0
  for (const date of dates) {
    if (attempted >= RECONCILE_DAYS_PER_PASS) break
    const file = path.join(root.archiveRoot, "manifests", "reconcile", `${date}.json`)
    if (yield* fs.exists(file).pipe(Effect.orElseSucceed(() => false))) continue
    const trades = yield* readManifest(root.archiveRoot, date).pipe(
      Effect.catch((error) =>
        Effect.logWarning("onchain reconcile skipped").pipe(
          Effect.annotateLogs({ game: root.game, date, message: error.message }),
          Effect.as(Option.none<Manifest>())
        )
      )
    )
    if (Option.isNone(trades)) continue
    const fills = yield* readOnchainManifest(root.archiveRoot, date)
    if (Option.isNone(fills)) continue
    attempted += 1
    const report = yield* reconcileDay({ game: root.game, archiveRoot: root.archiveRoot, date, file, trades: trades.value, fills: fills.value }).pipe(
      Effect.map(Option.some),
      Effect.catch((error) =>
        Effect.logWarning("onchain reconcile failed").pipe(
          Effect.annotateLogs({ game: root.game, date, message: error.message }),
          Effect.as(Option.none<OnchainReconcile>())
        )
      )
    )
    if (Option.isSome(report)) reports.push(report.value)
  }
}
return reports
```

`readManifest` is the existing reader in `src/compaction.ts` (strict decode, `CompactionError`). Reuse it; do not write a second WS manifest reader. Importing `compaction.ts` into the onchain module graph is fine: it imports nothing from `onchain-*`. If `Schema.is(UtcDate)` does not narrow inside `.filter`, use `.filter((stem): stem is UtcDate => Schema.is(UtcDate)(stem))`.

### 4.3 `src/onchain.testing.ts`: one shared fixture helper

Add `writeReconcileDay`. Both `onchain-reconcile.test.ts` and `onchain-app.test.ts` use it.

```ts
export const writeReconcileDay = Effect.fn("onchainTestWriteReconcileDay")(function* (
  archiveRoot: string,
  game: string,
  date: UtcDate,
  day: {
    readonly tradeIds: ReadonlyArray<string>
    readonly fills: ReadonlyArray<{ readonly tx: string; readonly us: number }>
    readonly ws?: boolean
    readonly onchain?: boolean
  }
): Effect.fn.Return<void, never, Crypto.Crypto | FileSystem.FileSystem | Path.Path | DuckDbSettings>
```

Behaviour:
- `tradesPath = \`parquet/trades/asset_id=${testToken}/${date}.parquet\``. `fillsPath = \`parquet/onchain_fills/asset_id=${testToken}/${date}.parquet\``.
- If `tradeIds` is non-empty, write `tradesPath`. First `mkdir -p` its directory; DuckDB `COPY` does not create directories. Then run `Effect.scoped(readSql(archiveRoot, sql))` from `./parquet.testing.ts`, with this `sql`: `COPY (SELECT * FROM (VALUES ${tradeIds.map((id) => \`('${id}')\`).join(", ")}) AS t(trade_id)) TO '${absPath}' (FORMAT PARQUET)`.
- If `fills` is non-empty, write `fillsPath` the same way with `SELECT * FROM (VALUES ${fills.map((f) => \`('${f.tx}', ${f.us}::BIGINT)\`).join(", ")}) AS t(tx_hash, block_timestamp_us)`.

  These minimal two- and one-column files are enough, because reconcile reads only these columns.
- Unless `ws === false`, publish a WS manifest to `manifests/${date}.json`. `mkdir -p` first, then `publishJsonFile(file, Schema.decodeSync(Manifest)({...}))` with this content:
  - `schemaVersion: 1`, `date`, `status: "complete"`, `quality: "complete"`, `generatedAtUs: 1_700_000_000_000_000`.
  - `versions: { collector: "test", node: "test", polymarketClient: "test", duckdb: "test", contract: 1, reducer: 1 }`.
  - `networkGapObservability: "not_exposed_by_sdk"`, `journal: { records: 0, files: [] }`.
  - `parquet`: when `tradeIds` is non-empty, one entry: `{ channel: "trades", assetId: testToken, path: tradesPath, rows: tradeIds.length, minTimestampUs: 1_700_000_000_000_000, maxTimestampUs: 1_700_000_000_000_000, bytes: 100, sha256: "0".repeat(64), logicalSha256: "0".repeat(64) }`. Otherwise `[]`.
  - `diagnostics`: `processGaps: []` and the seven counters `bookResets`, `preSnapshotDeltas`, `sourceTimestampFallbacks`, `clampedTimestamps`, `syntheticTradeIds`, `skippedParquetRows`, `unknownMarketTypes`, all `0`.
- Unless `onchain === false`: `writeOnchainManifest(archiveRoot, makeOnchainManifest(game, date, [testToken], fills.length > 0 ? [{ assetId: testToken, path: fillsPath, rows: fills.length }] : []))`.
- End every failing step with `Effect.orDie`, as the existing helpers do.
- New imports: `Crypto` (from `effect`), `type DuckDbSettings` from `./duckdb-session.ts`, `readSql` from `./parquet.testing.ts`, and `Manifest` from `./schema/manifest.ts`.

### 4.4 `src/onchain-app.ts`

1. Import `reconcileDays` from `./onchain-reconcile.ts` and `type OnchainReconcile` from `./schema/onchain-reconcile.ts`.
2. In `OnchainPassReport`, add `readonly reconciles: ReadonlyArray<OnchainReconcile>` after `fills`. You are editing this type, so delete its comments: the type JSDoc (`:78`) and the field docs (`:83`, `:85`, `:88`). Leave `OnchainEndpointReport` alone.
3. In `runOnchainTick`, right after the `if (errorMessage !== undefined) { … logError … }` block (`:282-286`), add `const reconciles = yield* reconcileDays(args.roots)`. Put `reconciles` into the `report` literal after `fills`.
4. Change the notify condition (`:389-392`) to:

   ```ts
   outcomes.some((outcome) => outcome.tag === "published") ||
   reconciles.length > 0 ||
   signature !== previousSignature
   ```

5. Delete the comments in `runOnchainTick`, which this step edits:
   - the JSDoc (`:225-229`);
   - the `lastFullDay walks …` block (`:300-302`);
   - the `Notify on every published day …` block (`:373-375`). It is wrong after this change.

   Also delete the module banner (`:51-58`). It narrates the tick as "disk gate → runOnchainPass → report", which is no longer the whole tick. Do not touch other comments in the file.

### 4.5 `src/telegram.ts`: `formatOnchainPass`

After the `for (const outcome of report.outcomes)` loop and before the endpoints loop, add:

```ts
for (const reconcile of report.reconciles) {
  const windows = reconcile.stallWindows.length
  lines.push(
    `${windows > 0 ? "STALL " : ""}reconcile=${reconcile.game}/${reconcile.date} unmatched=${reconcile.unmatchedOnchain}/${reconcile.onchainTxs} windows=${windows}`
  )
}
```

Delete the JSDoc `/** Plain-text summary of one on-chain pass for Telegram. */` above `formatOnchainPass` (`:104`).

## 5. Tests

All tests are offline and use temp archive roots. Layers: `NodeFileSystem.layer`, `NodePath.layer`, `NodeCrypto.layer`, `duckDbTestSettings`. This is the `nodeServices` set from `onchain-app.test.ts`. Use `it.effect(..., () => Effect.gen(...).pipe(Effect.scoped, Effect.provide(...)))` and `withDualArchiveRoots()`. Let `DATE = 2026-08-08`, `startUs = Option.getOrThrow(utcDayBoundsUs(DATE)).startUs`, and `S = 1_000_000` (one second in µs). Tx strings can be short (`"0xa1"`); the files are plain VARCHAR.

### 5.1 `src/onchain-reconcile.test.ts` (new, `describe("reconcileDays")`)

1. **matching sets → zero unmatched, no windows, file written.** Dota:
   - `tradeIds ["0xa1","0xa2","0xa3"]`;
   - fills `0xa1@+1s`, `0xa1@+1s` (mirrored duplicate), `0xa2@+2s`, `0xa3@+3s`.

   `reconcileDays(roots)` must equal `[{ schemaVersion: 1, date: DATE, game: "dota", wsTrades: 3, onchainTxs: 3, unmatchedOnchain: 0, unmatchedRatio: 0, stallWindows: [] }]`. The lol root has no manifests. Read `<dotaRoot>/manifests/reconcile/2026-08-08.json` and decode it with `decodeRecord("OnchainReconcile", Schema.fromJsonString(OnchainReconcile))` (from `./schema/decode.ts`). It must equal the returned report.
2. **seven misses, five inside 90 s → one window of 5.** Dota:
   - `tradeIds ["0xm1","0xm2","0xreverted"]`.
   - Fills:
     - `0xm1@+1h` and `0xm2@+4h` (matched);
     - `0xs1@+2h` and `0xs2@+20h` (scattered);
     - `0xu0..0xu4` at `burst + {0,20,45,70,90}s`, where `burst = startUs + 12h`.

   Expect `wsTrades 3`, `onchainTxs 9`, `unmatchedOnchain 7`, `unmatchedRatio` `toBeCloseTo(7 / 9)`, and `stallWindows` equal to `[{ fromUs: burst, toUs: burst + 90 * S, unmatched: 5 }]`.
3. **an existing report is not recomputed.** Use the setup of test 1. After the first `reconcileDays`, read the report bytes. The second `reconcileDays(roots)` must return `[]`, and the bytes must be identical.
4. **no reconcile without both manifests.**
   - Dota: `writeReconcileDay(..., { tradeIds: ["0xa1"], fills: [0xa1@+1s], ws: false })`.
   - LoL: the same with `onchain: false`.

   `reconcileDays` returns `[]`, and `manifests/reconcile/2026-08-08.json` does not exist in either root.
5. **at most `RECONCILE_DAYS_PER_PASS` dates per game per pass, oldest first.** On dota, write `RECONCILE_DAYS_PER_PASS + 1` days, `2026-08-01 … 2026-08-08`:
   - every day but the last: `tradeIds: []`, `fills: []`, so both manifests list no files;
   - the last day: `tradeIds: []` and fills `0xz1@+1h`, `0xz2@+5h`.

   The first call returns 7 reports. Their dates are `2026-08-01 … 2026-08-07` in order, each `wsTrades 0, onchainTxs 0, unmatchedOnchain 0, unmatchedRatio 0, stallWindows []`. The second call returns exactly `[{ date: 2026-08-08, wsTrades: 0, onchainTxs: 2, unmatchedOnchain: 2, unmatchedRatio: 1, stallWindows: [] }]`; compare the fields that matter. This test covers both empty-source branches.

### 5.2 `src/onchain-app.test.ts` (`describe("runOnchainTick")`, one new case)

`"reconciles days with both manifests once and notifies"`. Copy the setup of `"a pass-level failure reports once per signature; recovery notifies again"`, minus the `ledger` blocker file:
- `rootsOf(yield* withDualArchiveRoots())`, `writeGameSidecars`;
- a `stateDir` made with `makeDirectory`;
- `scriptedRpc(chainResponder({ tsOf: () => 0, head: 0 }))`;
- `makePool` with two test endpoints;
- `notifySpy()`, `makeOnchainRuntimeState`;
- `diskWarnGib: 1, diskStopGib: 0`, `startDate: DATE`;
- provide `Layer.mergeAll(nodeServices, DiskProbeLive)`.

The TestClock sits in 1970, so the pass plans nothing.

Fixtures:
- dota `DATE`: `tradeIds ["0xm1"]`; fills `0xm1@+10h`, then `0xu0..0xu4` at `+12h + {0,20,40,60,80}s`.
- lol `DATE`: `tradeIds ["0xl1"]`; fills `0xl1@+10h`.

First tick:
- `report.error` is undefined.
- `report.reconciles.map((r) => [r.game, r.date, r.unmatchedOnchain, r.stallWindows.length])` equals `[["dota", DATE, 5, 1], ["lol", DATE, 0, 0]]`.
- `spy.texts` has length 1, and its lines (`split("\n")`) contain exactly `STALL reconcile=dota/2026-08-08 unmatched=5/6 windows=1` and `reconcile=lol/2026-08-08 unmatched=0/1 windows=0`.
- `calls` has length 0 (no RPC).

Second tick: `reconciles` is `[]` and `spy.texts` still has length 1.

Existing tests need no change. They never write a WS manifest, so `reconciles` stays empty, and the notify rule is the same as before for them.

## 6. Docs

### 6.1 `docs/polymarket_dota_archive_contracts.md`

Insert a new subsection at the end of §13, right before `## 14. Process contract`:

````markdown
### 13.1 Сверка WS↔onchain

Путь (в archive root игры):

```text
manifests/reconcile/<YYYY-MM-DD>.json
```

Файл пишет onchain-сервис, а не compact. Он не входит в `status`/`quality` WS-дня и не меняет
`manifests/<date>.json`. Сверка запускается для даты, у которой есть ready-манифест
`manifests/onchain/<date>.json` и complete-манифест `manifests/<date>.json`, а файла сверки
ещё нет. Даты идут по возрастанию, за проход не больше семи дат на игру. Обращений к RPC нет.
Существующий файл не пересчитывается. Чтобы пересчитать день (например, после
перекомпакции), файл удаляют, и следующий проход записывает его заново.

```json
{
  "schemaVersion": 1,
  "date": "2026-10-08",
  "game": "dota",
  "wsTrades": 35574,
  "onchainTxs": 35643,
  "unmatchedOnchain": 74,
  "unmatchedRatio": 0.002076,
  "stallWindows": [{"fromUs": 1791477929000000, "toUs": 1791478055000000, "unmatched": 51}]
}
```

- `wsTrades` — distinct `trade_id` в файлах `trades` из `parquet[]` WS-манифеста.
- `onchainTxs` — distinct `tx_hash` в файлах из `files[]` onchain-манифеста.
- `unmatchedOnchain` — `tx_hash`, которых нет среди `trade_id`.
- `unmatchedRatio` — `unmatchedOnchain / onchainTxs`; при `onchainTxs = 0` равен 0.
- `stallWindows` — промахи, отсортированные по `block_timestamp_us` (минимум по tx) и
  сгруппированные так, что соседние промахи отстоят друг от друга не больше чем на 120 с. В
  список попадают только окна с ≥ 5 промахами. `fromUs`/`toUs` — первый и последний промах окна.

Окно означает тихий провал фида. В Telegram-отчёте `onchain pass` строка такой сверки начинается
с `STALL`. Рассеянные одиночные промахи (≈0,06 % сделок в день, коалесценция `last_trade_price`
в бёрстах) в окна не попадают. WS-сделки без onchain-пары (реверченные расчётные tx) не считаются.
````

### 6.2 `docs/learnings.md`: prepend at the top, under the `# polymarket-collector learnings` title

```markdown
### Only the chain can see a silent feed stall
A stream that goes quiet looks exactly like a quiet market: the day manifest stays complete and the journal has nothing to flag. Once both day manifests exist, the onchain service joins the day's WS trade ids with onchain tx hashes and reports onchain transactions that have no WS trade. It groups those misses into windows where neighbours are at most two minutes apart, and five or more misses in one window is a stall. Single scattered misses are upstream ticker coalescing and stay below the bar. The report lives beside the day manifest and never touches its status or quality, because the onchain day publishes on a different pass than compaction. It is written once; delete it to recompute.
```

### 6.3 `docs/docker-compose.md`

In the onchain paragraph, after the sentence that ends `…plus \`manifests/onchain/<date>.json\` into both archive roots.` (`:80-82`), add one sentence:

`Once a day also has its WS manifest, the pass writes \`manifests/reconcile/<date>.json\` (WS trades vs onchain txs, no RPC); delete that file to recompute it.`

## 7. Edge cases, concerns, tradeoffs

- **Notify gating is the real trap** (§2.7). Without `reconciles.length > 0` in the condition, the reports would be logged but almost never sent.
- **Empty inputs.** `read_parquet([])` and a glob with no match both fail in DuckDB. The typed empty subquery handles three cases:
  - a WS day with no trades files (all onchain txs count as unmatched, which is the strongest stall signal);
  - an onchain day with no files;
  - both empty.
- **Midnight split.** WS `trades` are dated by match time, onchain by block time, about 2.2 s later. A tx matched at 23:59:58 can land in WS D and onchain D+1, so it shows as a miss in D+1 at 00:00. That is a few per day at most, below the 5-in-a-window bar.
- **Bursty coalescing can produce a false STALL.** The bug hunt saw 4 coalesced misses within 0.15 s on one healthy burst. A 5th would trip the bar. The spec fixes the threshold at 5; if it gets noisy, raise it or require a minimum window length. Do not tune now.
- **Days with real downtime** (`processGaps`, a lost hour 00) show STALL windows too. That is correct: the trades really are missing.
- **First deploy backlog.**
  - Onchain manifests go back to the legacy import (before the WS archive). Those dates have no WS manifest and are skipped with two `stat` calls each per pass.
  - Dates with both manifests (about 60 Dota and 40 LoL) are reconciled 7 per game per pass. That is about 9 passes (about 45 min) and about 9 Telegram messages, each with at most 14 reconcile lines.
  - Keep `RECONCILE_DAYS_PER_PASS` small: the Telegram message limit is 4096 characters.
  - Each game-day costs about 80 ms on real data.
- **Recovered days (for STEP-008; tell the orchestrator).** The 10 days with a lost hour 00 will get reconcile reports at the STEP-007 deploy, before the recovery. Those reports flag hour 00 as STALL. After the STEP-008 recompaction, delete `manifests/reconcile/<D>.json` for those 10 dates so the next pass recomputes them against the recovered trades. Today STEP-008 says to keep them, and that was written assuming the onchain side alone mattered.
- **A permanently failing date** (corrupt Parquet) is retried every pass, logs WARN `onchain reconcile failed` each time, and uses one of the 7 slots. Seven such dates would starve newer dates. That is acceptable: it is loud and needs an operator anyway.
- **Republished onchain days are not reconciled again** (the file exists). Delete the file to force a recompute. No automatic invalidation, by design.
- **Concurrency with compaction.** The WS manifest is published last and atomically, so a readable manifest means its Parquet files are already in place. During a recompaction the manifest is moved away first (STEP-008), so reconcile does not start on that date. An open fd keeps reading the old file across an atomic rename.
- **`manifests/reconcile/` beside WS manifests is harmless.** `hasArchiveDayBefore` takes `entry.slice(0, 10)`, and "reconcile" is not a date. The scheduler reads `events/`. The esports-trader sync globs only `manifests/onchain/*.json`.
- **DuckDB scratch** lives in `<archiveRoot>/.onchain/<uuid>`. It is removed when the scope closes, and any leftovers are removed by the boot `sweepOnchainWork`.

## 8. Verification

Run in `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`. No network, no VPS.

1. `yarn vitest run src/onchain-reconcile.test.ts src/onchain-app.test.ts src/telegram.test.ts`. At HEAD, `onchain-app` + `telegram` are 17 tests and pass in about 1 s.
2. Regression: `yarn vitest run src/onchain-day.test.ts src/onchain-plan.test.ts src/compaction.test.ts src/compaction-scheduler.test.ts`
3. `yarn typecheck`
4. `yarn diagnostics`: 0 errors, and no new warnings in the touched files.
5. `yarn lint`
6. `yarn check` must exit 0. It is also the pre-commit hook and runs in the Docker build.
7. Optional: `yarn build` compiles the new module into `dist/` (gitignored).
8. Self-checks:
   - `grep -nE '//|/\*' src/onchain-reconcile.ts src/onchain-reconcile.test.ts src/schema/onchain-reconcile.ts` prints nothing.
   - `git diff -U0 -- src | grep '^+' | grep -E '//|/\*'` prints nothing (no new comments in edited files).
   - `grep -n "reconciles.length > 0" src/onchain-app.ts` shows the notify condition.
   - `grep -n "hive_partitioning=false" src/onchain-reconcile.ts` shows the source helper.
   - `wc -l src/onchain-app.ts src/onchain-app.test.ts src/onchain.testing.ts`: every file is under 1,000 lines.

## 9. Done when

- `src/onchain-reconcile.ts` meets all of these:
  - It reconciles each game-date that has a ready onchain manifest, a complete WS manifest and no `manifests/reconcile/<date>.json`.
  - It goes oldest first, at most 7 per game per pass.
  - It writes the `OnchainReconcile` JSON atomically.
  - It never makes an RPC call and never fails the pass.
- The counts and windows match §2.4–2.5. Five misses within a 90 s span make exactly one window of 5; matching sets give 0 and no windows.
- `runOnchainTick` carries `reconciles` in the report and notifies when any reconcile ran. Telegram shows one `reconcile=` line per report, prefixed `STALL ` when a window exists.
- The contract has §13.1. `docs/learnings.md` has the new top entry. `docs/docker-compose.md` has the one sentence.
- `yarn check` is green, and there is one commit on `main`. Suggested message: `feat: reconcile WS trades with onchain fills and flag silent feed stalls`. Not pushed. `feature.json` `passes` is left to the orchestrator; append to `progress.txt` as the implement skill says. In it, mention the STEP-008 note from §7 (delete the reconcile files of the 10 recovered dates after the recompaction).
