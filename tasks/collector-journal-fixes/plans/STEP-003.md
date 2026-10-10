# STEP-003 plan: compactor counts `sameDate` drops, checks hour coverage 00..23, logs silent skips, and requires the D-1 checkpoint

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-003` (priority 3).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Build on the current HEAD (`d48993e`, after STEP-001 and STEP-002). Commit there. Do not push.
Figma or design links: none.

Before you start, read `polymarket-collector/AGENTS.md`, the top entries of `docs/learnings.md`, and contract §9, §10, §12, §12.1, §13 and §15 in `docs/polymarket_dota_archive_contracts.md`.

## 1. The problems

1. **`sameDate` drops are counted nowhere.** `replay.ts` builds a row and then keeps it only if `sameDate(timestampUs, date)` (`bookRowsOf` at `:519-522`, trades loop in `foldRecord` at `:666-671`). Anything else disappears without a trace. When midnight rotation was skipped, every hour-00 record of day D sat in `events/D-1/23.jsonl.gz`. `replayDate(D-1)` folded those records and dropped their rows here, and `replayDate(D)` never read the file. That lost 3.15 M records (about 6 M rows) over 10 days, and every manifest still said `complete`.
2. **Missing hourly files leave no trace.** `qualityOf` (`compaction.ts:52-59`) reads only `skippedParquetRows` and `processGaps`. Since STEP-001, a running collector leaves exactly one `HH.jsonl.gz` for every UTC hour, empty or not. So a missing hour now means downtime or a bug, but the manifest does not show it.
3. **The scheduler skips some days without saying so.** In `compactMissingDays` (`compaction-scheduler.ts:112-156`), a day directory with no `*.jsonl.gz` hits a bare `continue` (`:137`) and logs nothing on any pass. The other two skips do log, but with different messages: `.partial` present (`:131-136`) and unreadable directory (`:116-124`).
4. **A day without `checkpoints/D-1.json` publishes cold, and that is final** (L-SEEDLESS). `replayDate` reads the seed with `checkpointStore.read(previousDate)` (`replay.ts:746-754`) and gets `None` when the file is absent. It then folds from empty books, so every delta before a token's next `book` becomes a `preSnapshotDelta` and is dropped. `qualityOf` ignores that and returns `complete`. The scheduler never revisits a day that has a manifest, so healing D-1 later never fixes D. Repro: `runs/collector-bug-hunt-20261010/work/p2-seedless-scheduler/repro/src/p2-seedless.test.ts`, test "SEEDLESS end-to-end" (PASS1/PASS2). D-1 fails, D publishes `complete` with 2 rows instead of 3, and D stays wrong after D-1 heals. A corrupt checkpoint has the same cascade: X+1 fails loudly, and X+2 silently publishes cold.

## 2. Design (decided; follow it)

1. **`rowsOutsideDate` counts day-phase rows only.** A row is counted when it was built from a record in one of D's own files (`phase === "day"`) and its timestamp is not in UTC D. Rows built from the neighbour `D+1/00` file are never counted: dropping D+1 rows there is the normal case, and D+1's own replay publishes them. The unit is the **row**: one trade record with metadata produces two mirrored trade rows, so it counts as 2. The counter is informational and does not change `quality`. On a normal day it is small but not zero, for two reasons. First, a record received just after midnight can carry a source timestamp from D-1 (that row is published by D-1's neighbour read). Second, resync `book`s can carry a stale timestamp (D-STALE-BOOK, 9–120 per day across a day boundary). A lost hour shows up as hundreds of thousands to millions.
2. **Two optional diagnostics fields.**
   - `diagnostics.rowsOutsideDate: { book, trades }` is always written by replay.
   - `diagnostics.missingHours: string[]` is always written by `buildManifest`. It lists the hours `00..23` with no `HH.jsonl.gz` in `journal.files`, in ascending order.

   Both use `Schema.optionalKey`, so manifests written before this step (which do not have them) still decode, including through `readManifest` with `onExcessProperty: "error"`. `schemaVersion` stays 1.
3. **Quality.** Precedence: `with_skips` (skipped rows) first; then `with_known_gaps` when `processGaps` is non-empty **or** `missingHours` is non-empty; otherwise `complete`. A non-empty `missingHours` also logs a WARN `day compaction found missing journal hours` with `{date, missingHours: "05,06"}`.
4. **Seed guard.** It lives in `compactDate`, before any replay or staging. `replayDate` stays the pure replay function and does not change.
   - If `checkpoints/<D-1>.json` exists: continue. A corrupt checkpoint still fails later at `stage=replay`, as it does today.
   - If it is absent and some earlier archive day exists: fail with `CompactionError({ stage: "seed.missing", message: "seed.missing: checkpoints/<D-1>.json is absent and the archive has an earlier day" })`. An earlier archive day is an entry in `events/` or in `manifests/` whose first 10 characters are a valid `UtcDate` strictly less than D.
   - Otherwise (D is the archive's first day): continue cold, as before.

   The scheduler's existing failure path does the rest: WARN `day compaction failed`, Telegram `compaction failed` with `stage=seed.missing`, and `lastFailureStage`. The day stays without a manifest and is retried on every pass until D-1's checkpoint exists. The guard checks existence only and does not decode the file.
5. **One skip message.** All three skip paths in `compactMissingDays` log `Effect.logWarning("compaction skipped")` with `{date, reason}`, where `reason` is `"unreadable"`, `"unclosed"` or `"no_journal"`. They log on every pass. The unreadable path also keeps `message: error.message`. Candidate dates are strictly before today, so any `.partial` in such a directory is never the current hour; the `unclosed` reason covers "a `.partial` that is not the current hour". Nobody outside this repo greps the old message texts (checked: workspace skills, learnings, docs).
6. **Telegram.** `formatCompactionOk` appends two lines at the end: `rowsOutsideDate=<book>/<trades>` and `missingHours=<05,06>`. When a field is absent (an old manifest) or the list is empty, the line prints `none`.

## 3. Scope

In scope:
- `src/replay.ts`, `src/schema/manifest.ts`, `src/compaction.ts`, `src/compaction-scheduler.ts`, `src/telegram.ts`
- `src/replay.testing.ts` (one helper)
- Tests: `src/replay.test.ts`, `src/compaction.test.ts`, `src/compaction-scheduler.test.ts`, `src/telegram.test.ts`, `src/schema/manifest.test.ts`
- `docs/polymarket_dota_archive_contracts.md` (§12, §12.1, §13, §15) and `docs/learnings.md`

Out of scope (do not do):
- Do not change `checkpoint.ts`. The guard needs only an existence check, and `compaction.ts` already owns the stage-carrying `CompactionError`.
- Do not count reducer/trade rejections. Do not change `preSnapshotDeltas`, `unknownMarketTypes` or the `attributionUs` gate. Do not add a `seed_missing` process gap.
- Do not cross-check a manifest's inner `date` against its file name. Do not send Telegram for skipped days (WARN log only). Do not change the order of `publishDay`.
- Do not make the scheduler compact dates that have no `events/<date>` directory (see §7, whole-day outage).
- Do not add a `collector_event` type. Do not touch `schemaVersion`, `schema/decode.ts`, or `replayDate`'s seed read.
- Compaction performance is a non-goal. Leave the decode-before-filter in `bookRowsOf`/`tradeRowOf` as it is.

Code rules:
- New code must not add comments (no `//`, no JSDoc on new exports, no comments in tests). When you edit a function, do not carry over or add narrating comments.
- Do not use `new Date`, `Date.now`, `Date.parse`, `setTimeout` or `new Promise` in `src`. Effect diagnostics flag them.
- Keep every touched file under 1,000 lines. `replay.ts` is 903 now; the change adds about 10.

## 4. Code changes

### 4.1 `src/schema/manifest.ts`

Add this above `ManifestDiagnostics` (no doc comment):
```ts
const JournalHour = Schema.String.check(Schema.isPattern(/^(?:[01][0-9]|2[0-3])$/))
```
Extend `ManifestDiagnostics` with two optional keys after `unknownMarketTypes`:
```ts
  rowsOutsideDate: Schema.optionalKey(
    Schema.Struct({ book: NonNegativeInt, trades: NonNegativeInt })
  ),
  missingHours: Schema.optionalKey(Schema.Array(JournalHour))
```
`NonNegativeInt` is already imported. Nothing else in the file changes.

### 4.2 `src/replay.ts`

1. **Export `previousUtcDate`** (`:100-101`): change `const previousUtcDate` to `export const previousUtcDate`. `compaction.ts` reuses it.
2. **`FoldState`** (`:425-433`): add `readonly rowsOutsideDate: { book: number; trades: number }`. In `emptyFold` (`:435-446`) add `rowsOutsideDate: { book: 0, trades: 0 }`.
3. **`bookRowsOf`** (`:496-525`): add a fourth parameter `phase: "day" | "neighbor"` and replace the filter with:
   ```ts
   if (sameDate(snapshot.timestampUs, date)) {
     rows.push(row)
   } else if (phase === "day") {
     fold.rowsOutsideDate.book += 1
   }
   ```
   Pass `phase` at both call sites in `foldRecord` (`book` at `:622` and `price_change` at `:637`): `bookRowsOf(fold, result.snapshots, date, phase)`.
4. **Trades loop in `foldRecord`** (`:666-672`):
   ```ts
   for (const trade of result.records) {
     outputTimestamps.push(trade.timestampUs)
     const row = yield* tradeRowOf(trade)
     if (sameDate(trade.timestampUs, date)) {
       batchTrades.push(row)
     } else if (phase === "day") {
       fold.rowsOutsideDate.trades += 1
     }
   }
   ```
5. **`diagnosticsOf`** (`:712-725`): add `rowsOutsideDate: { ...fold.rowsOutsideDate }` to the object that is decoded.

Nothing else in `replay.ts` changes. In particular, `replayDate` still reads the seed exactly as before.

### 4.3 `src/compaction.ts`

**Imports.**
- `import { GZ_SUFFIX } from "./journal.ts"`
- `import { previousUtcDate, replayDate, type ReplayOutput } from "./replay.ts"` (extends the existing import)
- `import { UtcDate, type UtcDate as UtcDateType } from "./schema/primitives.ts"` (replaces the type-only import)

**`qualityOf`** (`:52-59`):
```ts
export const qualityOf = (
  diagnostics: ManifestDiagnosticsType
): ManifestQuality =>
  diagnostics.skippedParquetRows > 0
    ? "with_skips"
    : diagnostics.processGaps.length > 0 || (diagnostics.missingHours ?? []).length > 0
      ? "with_known_gaps"
      : "complete"
```
Keep its existing one-line JSDoc as it is.

**Hour coverage helpers** (module level, next to `qualityOf`):
```ts
const DAY_HOURS = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, "0"))

const missingHoursOf = (fileNames: ReadonlyArray<string>): Array<string> =>
  DAY_HOURS.filter((hour) => !fileNames.includes(`${hour}${GZ_SUFFIX}`))
```

**`buildManifest`** (`:120-145`): compute the diagnostics once and use them for both `quality` and `diagnostics`:
```ts
    const path = yield* Path.Path
    const diagnostics = {
      ...replay.diagnostics,
      missingHours: missingHoursOf(replay.journalFiles.map((file) => path.basename(file)))
    }
    const candidate = {
      ...
      quality: qualityOf(diagnostics),
      ...
      diagnostics
    }
```
`replay.journalFiles` already holds only the target day's `*.jsonl.gz` paths (`replay.ts:157-160`), so `.gz.tmp`, `.partial` and `.jsonl.dropped` files never count as present.

**Seed guard** (new, above `compactDate`):
```ts
const hasArchiveDayBefore = Effect.fn("compaction.hasArchiveDayBefore")(
  function* (
    directory: string,
    date: UtcDateType
  ): Effect.fn.Return<boolean, CompactionError, FileSystem.FileSystem> {
    const fs = yield* FileSystem.FileSystem
    const exists = yield* fs.exists(directory).pipe(
      Effect.mapError(mapCompactionError("seed.scan"))
    )
    if (!exists) return false
    const entries = yield* fs.readDirectory(directory).pipe(
      Effect.mapError(mapCompactionError("seed.scan"))
    )
    return entries.some((entry) => {
      const day = entry.slice(0, 10)
      return Schema.is(UtcDate)(day) && day < date
    })
  }
)

const requireSeed = Effect.fn("compaction.requireSeed")(
  function* (
    archiveRoot: string,
    date: UtcDateType
  ): Effect.fn.Return<void, CompactionError, FileSystem.FileSystem | Path.Path> {
    const previous = previousUtcDate(date)
    if (Option.isNone(previous)) return
    const fs = yield* FileSystem.FileSystem
    const path = yield* Path.Path
    const layout = yield* layoutFor(archiveRoot)
    const seedPath = path.join(layout.checkpoints, `${previous.value}.json`)
    const seedExists = yield* fs.exists(seedPath).pipe(
      Effect.mapError(mapCompactionError("seed.scan"))
    )
    if (seedExists) return
    const earlierEvents = yield* hasArchiveDayBefore(layout.events, date)
    const earlierManifests = yield* hasArchiveDayBefore(layout.manifests, date)
    if (!earlierEvents && !earlierManifests) return
    return yield* compactionError(
      "seed.missing",
      `checkpoints/${previous.value}.json is absent and the archive has an earlier day`
    )
  }
)
```
Why `entry.slice(0, 10)` works:
- `events/` entries are `YYYY-MM-DD` directories.
- `manifests/` entries are `YYYY-MM-DD.json` files, plus the `onchain/` (and later `reconcile/`) subdirectories. Those fail `UtcDate`, so they are ignored.
- Junk such as `.DS_Store` also fails `UtcDate`. That matters because `"." < "2"` would otherwise count as an earlier day.

**`compactDate`** (`:180-235`): right after the `day compaction started` log, before `Effect.scoped(...)`:
```ts
    yield* requireSeed(archiveRoot, date)
```
After `const manifest = yield* buildManifest(...)`:
```ts
    const missingHours = manifest.diagnostics.missingHours ?? []
    if (missingHours.length > 0) {
      yield* Effect.logWarning("day compaction found missing journal hours").pipe(
        Effect.annotateLogs({ date, missingHours: missingHours.join(",") })
      )
    }
```
Leave the `day compacted` INFO log as it is.

### 4.4 `src/compaction-scheduler.ts`

Inside `compactMissingDays` (`:112-137`):
- Unreadable directory (`:116-124`): change the message to `"compaction skipped"` and the annotations to `{ date, reason: "unreadable", message: error.message }`. Drop `stage: "scan.day"`.
- `.partial` present (`:131-136`): `Effect.logWarning("compaction skipped").pipe(Effect.annotateLogs({ date, reason: "unclosed" }))`, then `continue`.
- No `*.jsonl.gz` (`:137`): replace the bare `continue` with
  ```ts
  if (!names.some((entry) => entry.endsWith(GZ_SUFFIX))) {
    yield* Effect.logWarning("compaction skipped").pipe(
      Effect.annotateLogs({ date, reason: "no_journal" })
    )
    continue
  }
  ```
Nothing else changes. The `seed.missing` failure goes through the existing `onFailure` branch (`:161-184`).

### 4.5 `src/telegram.ts`

In `formatCompactionOk` (`:30-51`):
```ts
  const d = manifest.diagnostics
  const outside = d.rowsOutsideDate
  const missingHours = d.missingHours ?? []
  return [
    ...the existing lines unchanged...,
    `unknownMarketTypes=${d.unknownMarketTypes}`,
    `rowsOutsideDate=${outside === undefined ? "none" : `${outside.book}/${outside.trades}`}`,
    `missingHours=${missingHours.length === 0 ? "none" : missingHours.join(",")}`
  ].join("\n")
```

### 4.6 `src/replay.testing.ts`

Add one helper (after `dayPath`):
```ts
export const writeEmptyHours = Effect.fn("replayTestWriteEmptyHours")(function* (
  root: string,
  except: ReadonlyArray<string>
) {
  for (let hour = 0; hour < 24; hour++) {
    const name = String(hour).padStart(2, "0")
    if (!except.includes(name)) yield* writeJournal(yield* dayPath(root, name), [], true)
  }
})
```
An empty gzip is a legal closed hour, and replay reads it as zero records.

## 5. Tests

Run the whole suite. The guard and the coverage rule change what several existing tests expect. The updates below are the complete list found by reading every `compactDate`/`compactMissingDays` caller. `archive-acceptance`, `compaction-streaming` and `app.test` assert only `status` or that a manifest exists, and none of them has an earlier day, so they need no change.

### 5.1 `src/replay.test.ts`: new test

`"counts day-file rows outside the date per channel and ignores the neighbor"`:
- `firstFixtureMarket(root)`, then `tokenId = sidecar.outcomes[0]!.tokenId`.
- `dayPath(root, "12")` (gzip), records:
  1. `catalog(0)`
  2. `record("market_event", book(sidecar.conditionId, tokenId, String(targetStartMs - 1_000)), 1)`, a book dated D-1
  3. `record("market_event", trade(sidecar.conditionId, tokenId, String(targetStartMs + 86_405_000), null), 2, neighborNextDayReceivedAtUs, epoch)`, a D+1 trade sitting inside D's file (the lost-midnight shape)
- `neighborPartialPath(root)` (not gzip): `record("market_event", trade(sidecar.conditionId, tokenId, String(targetStartMs + 86_406_000), null), 3, neighborNextDayReceivedAtUs, epoch)`
- `collectReplayDate(root, date, targetStartMs + 600_000)`
- Expect `rows.book` length 0, `rows.trades` length 0, and `diagnostics.rowsOutsideDate` `toEqual({ book: 1, trades: 2 })`. The trade counts as 2 because the trade and its mirror share one effective timestamp (contract §9). If the run shows a different trade count, check the mirroring before you change the expectation.

### 5.2 `src/compaction.test.ts`

Imports to add: `captureLogs` from `./logger.testing.ts`; `previousDate` and `writeEmptyHours` from `./replay.testing.ts`.

Update existing tests:
1. `"publishes validated Parquet, checkpoint, and a complete manifest"` (`:119`): after writing `happyRecords`, add `yield* writeEmptyHours(root, ["12"])`. Replace `const journalFile = manifest.journal.files[0]` with `manifest.journal.files.find((file) => file.path === \`events/${date}/12.jsonl.gz\`)`. The `quality` stays `complete`, and `journal.records` stays 4.
2. `"publishes an empty first day without a checkpoint"` (`:373`): expect `quality` `"with_known_gaps"` and `diagnostics.missingHours` `toHaveLength(24)`. Keep `processGaps` `[]` and the file assertions.
3. `"publishes the first archive day without a seed gap"` (`:389`):
   - After writing the day file, add `yield* writeEmptyHours(root, ["12"])`.
   - Also write a **later** day: `writeJournal(\`${layout.events}/2026-08-09/12.jsonl.gz\`, [], true)`. Move `const layout = yield* layoutFor(root)` above it. This shows the guard ignores later days.
   - `quality` stays `"complete"`, and `processGaps` stays `[]`.

New tests:
4. `"marks a day with a missing journal hour with_known_gaps"`:
   - `preparedArchive()` (seeded); `happyRecords` at hour `12`; `writeEmptyHours(root, ["05", "12"])`.
   - `const logs = captureLogs()`, then `compactDate(root, date, targetStartMs + 600_000).pipe(Effect.provide(logs.layer))`.
   - Expect `quality` `"with_known_gaps"`, `diagnostics.missingHours` `["05"]`, `diagnostics.processGaps` `[]`, and `diagnostics.rowsOutsideDate` `{ book: 0, trades: 0 }`.
   - Expect a log line that includes `level=Warn`, `date=${date}` and `missingHours=05`.
   - Then `writeJournal(yield* dayPath(root, "05"), [], true)` and compact again: `quality` `"complete"`, `missingHours` `[]`.
5. `"fails seed.missing when an earlier archive day exists without the previous checkpoint"`: two roots, written plainly with no loop.
   - Root A: `preparedArchive(false)`; write `\`${layout.events}/2026-08-06/12.jsonl.gz\`` with `[record("collector_event", { type: "collector_started", details: {} }, 0)]` (gzip); write `happyRecords` at `dayPath(root, "12")`. Then `Effect.flip(compactDate(...))`. Expect:
     - an instance of `CompactionError`, with `stage` `"seed.missing"`;
     - a `message` that contains `${previousDate}.json`;
     - `manifests/${date}.json` does not exist;
     - `fs.readDirectory(layout.parquet)` is `[]` (nothing was staged).
   - Root B: the same, but the earlier day is only `writeJson(\`${layout.manifests}/2026-08-01.json\`, {})` (no earlier events directory). Expect `stage` `"seed.missing"`.

The spec case "no earlier days, so publication goes through" is covered by test 3 above and by pass 2 of §5.3 test 2.

### 5.3 `src/compaction-scheduler.test.ts`

Imports to add: from `./replay.testing.ts`, add `book`, `date`, `dayPath`, `firstFixtureMarket`, `previousDate`, `priceChange`, `record` and `targetStartUs`; from `./schema/primitives.ts`, add `Microseconds`.

1. `"compacts missing closed days oldest-first and skips complete manifests"` (`:231`):
   - Change `existingDate` to `"2026-08-05"`. 08-06 can no longer be compacted before 08-05, because its seed would be missing.
   - Update the two `2026-08-06.json` paths to `2026-08-05.json`, and `expect(existing.date)` to `"2026-08-05"`.
   - The expected `started` list becomes `["2026-08-06", "2026-08-07", "2026-08-08"]`. `compacted` stays 3.
2. **Replace** `"isolates a failed day and keeps compacting the rest"` (`:258`) with the PASS1/PASS2 port, `"holds a day after a failed day until its checkpoint exists, then publishes it seeded"`:
   - Setup: `withArchiveRoot`, `ensureArchiveLayout`, `sidecar = firstFixtureMarket(root)`, `tokenId = sidecar.outcomes[0]!.tokenId`.
   - Make D-1 corrupt: `previousDayFile = path.join(layout.events, previousDate, \`12${GZ_SUFFIX}\`)`; `fs.makeDirectory(dirname, { recursive: true })`; `writeFileSynced(previousDayFile, new Uint8Array([1, 2, 3]))`.
   - Day D at `dayPath(root, "12")` (gzip), records:
     - `priceChange(…, String(targetStartMs + 10_000))` at seq 7
     - `book(…, String(targetStartMs + 20_000))` at seq 8
     - `priceChange(…, String(targetStartMs + 30_000))` at seq 9
   - Pass 1, with `captureLogs`: `compactMissingDays(root, catchUpNowMs, "dota")` returns 0, and `manifestExists(root, date)` is false. Expect these log lines:
     - `day compaction failed`, `date=${previousDate}`, `stage=replay`
     - `day compaction failed`, `date=${date}`, `stage=seed.missing`
   - Heal D-1: `writeJournal(previousDayFile, [record("market_event", book(…, String(targetStartMs - 1_800_000)), 6, Schema.decodeSync(Microseconds)(targetStartUs - 1_800_000_000))], true)`.
   - Pass 2: `compactMissingDays(...)` returns 2. `readManifest(root, date)` is `Some`, with:
     - `diagnostics.preSnapshotDeltas` 0;
     - `book_snapshot_full` rows summed over `parquet` equal 3. A cold replay would give 2 rows and 1 pre-snapshot delta, which is the P2_PASS1 result.
3. `"skips open, current, future, and empty journal days"` (`:310`): rebuild the fixture so the closed day comes first. Otherwise 08-07 would now fail with `seed.missing` behind the unclosed 08-05.
   - `writeClosedDay(root, "2026-08-05")`: compacts, as the first day.
   - `events/2026-08-06/` holding only `12${GZ_SUFFIX}.tmp` (`fs.makeDirectory(dir, { recursive: true })`, then `writeFileSynced`, bytes `[1]`): reason `no_journal`.
   - `events/2026-08-07/12${PARTIAL_SUFFIX}` (make the directory first, bytes `[1]`): reason `unclosed`.
   - `writeClosedDay` for `2026-08-09` and `2026-08-10`: future days, not candidates.
   - Run the pass **twice** with the same `logs.layer`: expect `1`, then `0`.
   - Manifests: 08-05 exists; 08-06, 08-07, 08-09 and 08-10 do not.
   - Count the lines that include `level=Warn`, `compaction skipped`, `date=<day>` and `reason=<reason>`. Expect exactly 2 for `("2026-08-06", "no_journal")` and 2 for `("2026-08-07", "unclosed")`. That is one per pass.
   - Keep the `emptyRoot` / `noEventsRoot` tail as it is.
   - Rename the test to `"skips open, current, future, and empty journal days with a warning on every pass"`.
4. `"serializes guarded catch-up calls with one semaphore"` needs no change. Four consecutive days compact in one pass, and each seeds the next.

### 5.4 `src/telegram.test.ts`

- `"formats a successful compaction with manifest stats"`: `exampleManifest` has no new fields (old shape), so append `"rowsOutsideDate=none"` and `"missingHours=none"` to the expected array.
- Add to the same test, or a new one:
  ```ts
  const covered = {
    ...exampleManifest,
    diagnostics: {
      ...exampleManifest.diagnostics,
      rowsOutsideDate: { book: 7, trades: 8 },
      missingHours: ["00", "05"]
    }
  }
  expect(formatCompactionOk(covered, "dota").split("\n").slice(-2)).toEqual([
    "rowsOutsideDate=7/8",
    "missingHours=00,05"
  ])
  ```

### 5.5 `src/schema/manifest.test.ts`

- Keep `example` unchanged. It is the old shape, and `"decodes the contract 13 example verbatim"` already proves that old manifests decode.
- New `it("decodes optional rowsOutsideDate and missingHours", …)`: decode `{ ...example, diagnostics: { ...example.diagnostics, rowsOutsideDate: { book: 1, trades: 2 }, missingHours: ["05"] } }` and check both fields.
- New `it.effect("rejects an hour outside 00..23", …)`: `missingHours: ["24"]` fails through `asDecodeError(decodeStrict("Manifest", Manifest)(…))` with `error.schema` `"Manifest"`.

## 6. Docs

### 6.1 Contract `docs/polymarket_dota_archive_contracts.md`

- **§12, step 1** (`:664`), replace with:
  ```
  1. взять checkpoint `D-1`. Если его нет, а в архиве есть более ранний день
     (каталог `events/<date>` или `manifests/<date>.json` с датой `< D`), compaction `D`
     падает с `stage=seed.missing` (structured log и Telegram `compaction failed`) и
     повторяется следующим catch-up, пока checkpoint `D-1` не появится; без seed
     стартует только первый день архива;
  ```
- **§12, step 6**, replace with:
  ```
  6. отфильтровать rows по UTC `D`; строки из файлов `events/D/*` с timestamp вне `D`
     считаются в `diagnostics.rowsOutsideDate` (строки соседнего `D+1/00` не считаются);
  ```
- **§12.1**, add item 7 after item 6:
  ```
  7. день с `.partial`, без единого `HH.jsonl.gz` или с нечитаемым каталогом
     пропускается с WARN `compaction skipped {date, reason}` (`unclosed`, `no_journal`,
     `unreadable`) на каждом проходе;
  ```
- **§13**: leave the JSON "Минимальная форма" unchanged. The new fields are optional, and `schema/manifest.test.ts` decodes that example verbatim. After the JSON block, add:
  ```
  Optional diagnostics (manifests до их появления их не содержат и декодируются):

  - `rowsOutsideDate: {"book": n, "trades": n}` — строки, построенные из записей файлов
    `events/D/*` (без соседнего `D+1/00`), чья timestamp вне UTC `D`; в партицию `D` они не
    попадают. Штатно — десятки–сотни (source timestamp прошлого дня в начале часа `00`,
    resync-`book` с устаревшей timestamp); сотни тысяч и больше означают, что в файлах `D`
    лежат записи другого дня. Одна сделка с metadata — две строки (mirror).
  - `missingHours: ["05", ...]` — часы `00..23`, для которых в `journal.files` нет
    `HH.jsonl.gz`.
  ```
  Change the `with_known_gaps` bullet to: `` - `with_known_gaps` — был process downtime или `missingHours` не пуст; ``
- **§15** table: add the row below after "Compaction/validation error":
  `| Нет \`checkpoints/D-1.json\`, а в архиве есть более ранний день | Compaction \`D\` падает \`seed.missing\`; повторить следующим catch-up. Если collector не работал весь день \`D-1\` (нет \`events/D-1\`), положить пустой gzip \`events/D-1/00.jsonl.gz\`: \`D-1\` скомпактится от seed \`D-2\` как день простоя |`

### 6.2 `docs/learnings.md`

Prepend this right under the `# polymarket-collector learnings` title, above "Live rotation proves bytes; startup proves records". Keep it to 8 lines or fewer, with no paths or commands:
```
### A day waits for its predecessor's checkpoint
Without the previous day's checkpoint, replay starts from empty books, drops every delta before a token's next snapshot, and the day used to publish as complete for good; healing the previous day later never revisited it. Compaction now fails such a day with seed.missing and retries each pass; only the archive's first day starts cold. A missing hourly gzip marks the day with_known_gaps, because a running collector leaves one per hour, empty or not. Rows built from the day's own files but dated another day are counted per channel: tens to hundreds on a normal day, millions when a midnight hour lands in the wrong file. That one number would have shown the lost hours on the first day.
```

## 7. Edge cases, concerns, tradeoffs

- **STEP-008's verification expects values this design cannot produce. Tell the orchestrator.** STEP-008 checks `quality=complete` and an empty `missingHours` for the 10 recovered days D, and `rowsOutsideDate = 0` for each D-1. Neither holds:
  - **Missing hours.** Rotation skipped about 9 % of all hour boundaries, not only midnight. Those hours' records sit inside the previous hour's file, so the hour's own file is missing. The current manifests of those days have 20–22 journal files (`runs/collector-bug-hunt-20261010/evidence/manifests-summary.jsonl`). After the re-split adds `00`, most still lack 1–3 hours, so they publish `with_known_gaps` with those hours in `missingHours`. No data is lost; the label marks the old bug.
  - **Rows outside date.** D-1's `rowsOutsideDate` drops from about a million to the normal noise floor (tens to hundreds), not to 0.

  Suggested STEP-008 checks:
  - `journal.files` contains `00.jsonl.gz`;
  - `missingHours` does not contain `"00"`;
  - D-1's `rowsOutsideDate` is at least 1,000 times smaller than the records listed for that day in `lost-hour00-records.txt`.

  Nobody downstream reads WS manifest `quality` (checked `esports-trader`: its sync reads only `manifests/onchain/`).
- **A whole-day collector outage now blocks compaction until someone fixes it.** If the collector is down for all of D-1, `events/D-1` never exists. Then D fails with `seed.missing` on every pass, and every later day fails behind it. This is the owner's decision ("fail until D-1 appears"). It is loud: one Telegram started+failed pair per blocked day per pass. The unblock recipe is in the new §15 row: an empty `events/D-1/00.jsonl.gz` lets the scheduler compact D-1 from D-2's seed as a downtime day, which writes checkpoint D-1. Do not automate this in this step.
- **A corrupt checkpoint X.** X+1 still fails at `stage=replay` (the existence check passes, the decode fails). X+2 now fails with `seed.missing` instead of publishing cold, so the old cascade is closed.
- **The first archive day.** It has no earlier `events/` or `manifests/` entry, so it starts cold as before. Its collector starts mid-day, so it has missing hours and publishes `with_known_gaps`. That is correct: there is no data before the start.
- **An empty first day writes no checkpoint.** That happens only when the day has neither records nor a seed. Boot appends `collector_started` right after `openJournal`, so a real first day always has a record. A crash between those two calls is the only way in. Accept it; the §15 recipe does not apply there, because the day has no seed either.
- **Checkpoint without manifest.** A crash between the checkpoint write and the manifest write leaves `checkpoints/D-1.json` present but D-1 unpublished. D may compact first in that case; this is deterministic and already accepted (p2-seedless P2-SCHED-5).
- **Junk in `events/` or `manifests/`.** Validating `entry.slice(0, 10)` against `UtcDate` ignores `.DS_Store`, `tmp-*`, `onchain/` and `reconcile/`. A stray `2026-08-01-old.json` would count as an earlier day. That is harmless: it can only make the guard stricter.
- **Telegram volume.** `compaction started` is still sent before the guard runs, so a blocked day sends a started+failed pair. Do not reorder; the existing failure path is what the owner asked for.
- **Determinism.** Both new fields come from the journal and the file list, never from the clock, so repeated compactions still give equal manifests (`"produces identical manifests…"` stays green).
- **Scale gate.** `src/compaction-scale.test.ts` (not part of `yarn check`) runs on an operator copy. If that copy lacks `checkpoints/<D-1>.json` while holding earlier days, it now fails with `seed.missing`. Copy the checkpoint along with the day.

## 8. Verification

Run in `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`. No network, no VPS.

1. `yarn vitest run src/replay.test.ts src/compaction.test.ts src/compaction-scheduler.test.ts src/telegram.test.ts src/schema/manifest.test.ts`
2. `yarn vitest run src/archive-acceptance.test.ts src/compaction-streaming.test.ts src/app.test.ts src/checkpoint.test.ts`
3. `yarn typecheck`
4. `yarn diagnostics` (Effect diagnostics: 0 errors, no new warnings in touched files)
5. `yarn lint`
6. `yarn check`: must exit 0 (it also runs as the pre-commit hook)
7. Self-checks:
   - `grep -n "compaction skipped an\|day compaction failed" src/compaction-scheduler.ts` shows only the `day compaction failed` line.
   - `grep -n "seed.missing" src/compaction.ts` shows the guard.
   - `git diff -U0 -- src | grep '^+' | grep -E '//|/\*'` prints nothing (no new comments).
   - `wc -l src/replay.ts src/compaction.ts src/compaction.test.ts src/compaction-scheduler.test.ts src/replay.test.ts`: all under 1,000.

## 9. Done when

- Replay counts day-phase rows dropped by `sameDate` per channel into `diagnostics.rowsOutsideDate`. Neighbour drops are not counted.
- Every new manifest carries `missingHours`. A non-empty list gives `with_known_gaps` (unless `with_skips` applies) and a WARN with the date and hours.
- Old manifests without the new fields still decode through `readManifest`.
- `compaction ok` in Telegram ends with `rowsOutsideDate=…` and `missingHours=…`.
- Every skipped candidate day logs WARN `compaction skipped {date, reason}` on every pass.
- A day without `checkpoints/D-1.json` fails with `stage=seed.missing` whenever an earlier archive day exists. Nothing is staged or published, and the day publishes seeded once D-1 heals. The archive's first day still compacts cold.
- Contract §12, §12.1, §13 and §15 and `docs/learnings.md` are updated.
- `yarn check` is green, and there is one commit on `main`. Suggested message: `fix: hold a day without its predecessor's checkpoint, count rows outside the date, flag missing hours`. Not pushed. `feature.json` `passes` is left for the orchestrator.
