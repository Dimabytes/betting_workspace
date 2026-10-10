# STEP-004 plan: onchain sweep checks the ledger, coverage follows the could-trade window, `matchForFill` never borrows, catalog snapshots are written once and pruned

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-004` (priority 4).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Build on the current HEAD (`72cd6e9`, after STEP-001..003). Commit there. Do not push.
Figma or design links: none.

Before you start, read `polymarket-collector/AGENTS.md` and the top entries of `docs/learnings.md`, especially "Day commit markers are manifests; chunk coverage keys on range+fingerprint+version", "A sidecar with no start time is not a catalog row", "Gamma by-id fetch trails the list…" and "The taker token is the taker order's token from OrdersMatched".

Evidence (read-only, in `betting_workspace/runs/collector-bug-hunt-20261010/`): `FINAL_REPORT.md` §4.6 L-ONCHAIN-BRICK and §4.7, `reports/p2-onchain-recheck.md` (findings 1–3), `reports/p1-onchain-pipeline.md` (findings 1–3), `work/p2-onchain-recheck/repro-stale-manifest-sweep.mts`, `work/p2-onchain-recheck/matchforfill_demo.mjs`.

## 1. The problems

How it works today. Each pass loads the catalog, saves its snapshot as `<stateDir>/catalogs/<fingerprint>.json`, and lists unfinished days. For each day it splits the block range into chunks. `scanChunk` writes one artifact per **publishable** token (could trade that day) to `<game root>/.onchain/chunks/<date>/<from>-<to>/asset_id=<token>.json`, then commits the chunk in `<stateDir>/ledger/<date>.jsonl`. A commit stores the range, the catalog fingerprint and the decoder version. `publishGameDay` reads every artifact back. After both games' manifests are published, `collectOnchainDay` deletes `chunks/<date>`.

1. **The boot sweep deletes a rescan in progress** (L-ONCHAIN-BRICK 1). `sweepOnchainWork` (`src/onchain-app.ts:110-147`) deletes `.onchain/chunks/<date>` as soon as both onchain manifests exist (`:141`). A published day reopens when a new token becomes could-trade. Its rescan commits chunks under the new fingerprint. If the process restarts mid-rescan, the sweep deletes the artifacts of those committed chunks. Then `unlessCommitted` skips them, and `publishGameDay` fails `artifact.missing` on every pass, with zero RPC. Repro: `repro-stale-manifest-sweep.mts`.
2. **Coverage counts snapshot membership, not written artifacts** (L-ONCHAIN-BRICK 2). `coveredTokens` (`src/onchain-plan.ts:105-144`) counts a token as covered by a commit when the token is anywhere in that commit's snapshot (`:125-129`). A snapshot holds every catalog entry, and about 97 % of entries are closed. But the scan wrote artifacts only for tokens that could trade that day (`src/onchain-day.ts:259-263`). Now suppose a catalogued token's window widens later: its `closedAt` is removed or moved later, or its `startAt` moves earlier. The planner reopens the day, `coveredTokens` says the token is covered, `needed = []`, and publish fails `artifact.missing` at once, with no restart and no RPC.
3. **`matchForFill` borrows a foreign match** (`src/onchain-decode.ts:161-181`). If no `OrdersMatched` follows the fill in its (contract, tx), it returns the latest match (`fallback`). If the group has exactly one match, it returns it even when that match comes **before** the fill (`:168`). Either way the fill silently gets another match's `taker_*`. On V2 this cannot happen today (OrdersMatched is always last), but it would come back after any contract change. `matchforfill_demo.mjs` shows the bug with a single earlier match.
4. **`catalogs/` grows without bound** (§4.7). `saveCatalogSnapshot` (`src/onchain-ledger.ts:383-398`) rewrites the ~2.3 MB file on every pass, even when the fingerprint did not change, and nothing deletes snapshots. On the VPS that is 1,095 files and 2.08 GiB after 20 days, and only 26 fingerprints are referenced by ledger commits.

## 2. Design (decided; follow it)

1. **Sweep by ledger.** `sweepOnchainWork` gets a third parameter `commits: OnchainLedgerStore["commits"]`. For each `chunks/<date>` directory:
   - If either game's onchain manifest is missing or unreadable, keep the directory. This is unchanged.
   - Otherwise build `published` = the union of both manifests' `provenance.scans`. If the ledger has any commit for that date (any decoder version) whose `catalogFingerprint` is not in `published`, keep the directory. That commit belongs to a rescan the manifests do not include yet. Log INFO `onchain chunks kept` with `{root, date, reason: "rescan pending"}`.
   - If the ledger read fails, keep the directory and log the same INFO with `reason: "ledger unreadable"`. Never delete on doubt.
   - Otherwise delete the directory, as today.

   `onchainMain` opens the ledger once, right before the sweep loop, and passes `ledger.commits`. A state dir whose `ledger/` cannot be created now fails boot instead of every pass. That is acceptable: the line before it already creates the state dir.
2. **Coverage by the could-trade window in the commit's own snapshot.** `coveredTokens` gets `startUs` and `endUs`, the UTC bounds of the day. A snapshot contributes only the entries for which `couldTradeDuring(entry, startUs, endUs)` is true. That is exactly the set `scanChunk` wrote artifacts for under that fingerprint. `couldTradeDuring`'s window type widens back to `startAt: string | null`. Old snapshots still hold `startAt: null`, and the old code scanned those tokens as could-trade on every day, so they did get artifacts. `parseMicros(null)` already returns `null`, which means "included". No logic change is needed.
   - This does **not** reopen published days. `listUnfinishedDays` uses manifests, not `coveredTokens`. Only days that are already unfinished see the stricter rule, which is the fix.
3. **`matchForFill`.** The fill gets the `OrdersMatched` with the smallest `log_index` greater than the fill's `log_index` in the same (contract, tx), or `null`. Delete both the "latest" fallback and the single-match shortcut. `fillRowsForToken` already turns `null` into `unmatched`, which fails the chunk with `OnchainDecodeError "fills without OrdersMatched"`. That is the loud error the learnings require. Note that this goes past the spec's wording ("when there are several matches"). The single-match shortcut is the same bug: the bug-hunt demo used exactly one earlier match. Real V2 data and the parity fixture always put OrdersMatched after its fills, so decoder output does not change. **Do not bump `ONCHAIN_DECODER_VERSION`.**
4. **Snapshots are written once, and unreferenced ones are pruned.**
   - `saveCatalogSnapshot` writes only when `catalogs/<fingerprint>.json` does not exist yet. The write is atomic, so an existing file is complete.
   - New store method `pruneCatalogSnapshots(current)`:
     1. Read every `ledger/<date>.jsonl` through the existing `commits(date)` and collect the referenced fingerprints, plus `current`.
     2. Only then delete each `catalogs/<64-hex>.json` whose fingerprint is not in that set. Leave every other name alone: `publishBytesAtomic` leaves temp directories there after a crash.
     3. Return `{ files, bytes }` and log INFO `catalog snapshots pruned {files, bytes}` when `files > 0`.

     If any ledger day fails to read, the method fails **before deleting anything**.
   - `runOnchainPass` calls it once per pass, right after `saveCatalogSnapshot`, and catches any failure with WARN `catalog snapshot prune failed {message}`. A corrupt ledger day must not fail the whole pass; today it fails only its own day.
5. **No new comments.** In every function this step edits, delete the old JSDoc or inline comments that the change makes wrong, plus the narrating inline comments. Section 4 lists each one. Do not add comments anywhere, tests included.

## 3. Scope

In scope:
- `src/onchain-app.ts`, `src/onchain-plan.ts`, `src/onchain-day.ts`, `src/onchain-ledger.ts`, `src/onchain-decode.ts`, `src/onchain-catalog.ts` (type widening only)
- Tests: `src/onchain-app.test.ts`, `src/onchain-day.test.ts`, `src/onchain-plan.test.ts`, `src/onchain-ledger.test.ts`, `src/onchain-decode.test.ts`
- `docs/learnings.md`

Out of scope (do not do):
- Do not make `coveredTokens` check artifact files on disk (see §7, residual risk). Do not add a self-healing "delete orphaned commits" path. Do not edit ledger lines.
- Do not touch `publishGameDay`, `readChunkArtifact`, `listUnfinishedDays`, `scanChunk`, the RPC pool, `lagDays`, the dota→lol publish order, or the onchain validator.
- Do not bump `ONCHAIN_DECODER_VERSION`, and do not change any schema (`CatalogSnapshot`, `OnchainChunkCommit`, `OnchainManifest`).
- Do not add Telegram text for the sweep or the prune (logs only).
- Do not touch `docs/polymarket_dota_archive_contracts.md`: it has no onchain coverage or sweep rule. `docs/superpowers/specs/…` also stays unchanged.
- Do not edit or run `betting_workspace/runs/.../repro-stale-manifest-sweep.mts`. It calls the old two-argument sweep; the new test in §5.2 replaces it.

Code rules:
- No `new Date`, `Date.now`, `setTimeout` or `new Promise` in `src` (Effect diagnostics).
- No `!` non-null assertions in `src` (tests may use them, as they already do).
- Typed errors only. Keep every touched file under 1,000 lines. The largest is `src/onchain-day.test.ts`: 629 lines now, about 720 after this step.

## 4. Code changes

### 4.1 `src/onchain-catalog.ts`

In `couldTradeDuring` (`:234-244`), change the parameter type to `window: { readonly startAt: string | null; readonly closedAt: string | null }`. The body stays as it is. In its JSDoc (`:225-233`), delete only the sentence "`startAt` is always present." and keep the rest.

### 4.2 `src/onchain-plan.ts`

1. In the `ChunkCoverage` interface (`:90-96`), delete the now-wrong field doc on `inChunk` (`:94`, "Some commit of this chunk has `token` in its snapshot.").
2. Delete `coveredTokens`'s JSDoc (`:98-104`).
3. `coveredTokens` gets two new args, and the snapshot set keeps only could-trade entries:
   ```ts
   export const coveredTokens = Effect.fn("coveredTokens")(function* (args: {
     readonly chunks: ReadonlyArray<BlockRange>
     readonly commits: ReadonlyArray<OnchainChunkCommit>
     readonly decoderVersion: number
     readonly startUs: number
     readonly endUs: number
     readonly loadSnapshot: OnchainLedgerStore["loadCatalogSnapshot"]
   }): Effect.fn.Return<ChunkCoverage> {
     …
     for (const fingerprint of fingerprints) {
       const snapshot = yield* args.loadSnapshot(fingerprint)
       snapshots.set(
         fingerprint,
         Option.isSome(snapshot)
           ? new Set(
               snapshot.value.entries
                 .filter((entry) => couldTradeDuring(entry, args.startUs, args.endUs))
                 .map((entry) => entry.tokenId)
             )
           : new Set()
       )
     }
     …
   ```
   `couldTradeDuring` is already imported. Nothing else changes.

### 4.3 `src/onchain-day.ts` — `collectOnchainDay`

1. Pass the day bounds to both `coveredTokens` calls (`:265-270` and `:308-313`):
   ```ts
   startUs: bounds.startUs,
   endUs: bounds.endUs,
   ```
2. Delete the inline comments inside `collectOnchainDay`: `:259-260`, `:274-277`, `:305-307`, `:340-342`, `:361`, `:376-377`. The one at `:274-277` is wrong now. Keep the function's JSDoc.

### 4.4 `src/onchain-day.ts` — `runOnchainPass`

Right after `const fingerprint = yield* store.saveCatalogSnapshot(catalog)`:
```ts
yield* store.pruneCatalogSnapshots(fingerprint).pipe(
  Effect.catch((error) =>
    Effect.logWarning("catalog snapshot prune failed").pipe(
      Effect.annotateLogs({ message: error.message })
    )
  )
)
```

### 4.5 `src/onchain-ledger.ts`

1. Add a member to the `OnchainLedgerStore` interface (`:252-271`), with no doc comment:
   ```ts
   readonly pruneCatalogSnapshots: (
     current: Sha256Hex
   ) => Effect.Effect<{ readonly files: number; readonly bytes: number }, OnchainLedgerError>
   ```
2. Change the `saveCatalogSnapshot` body (`:391-396`) to:
   ```ts
   const fingerprint = yield* hashCatalogSnapshot(catalog)
   const file = path.join(catalogsDir, `${fingerprint}.json`)
   if (!(yield* fs.exists(file))) {
     yield* publishJsonFile(file, catalogSnapshotPayload(catalog))
   }
   return fingerprint
   ```
   Its error type is unchanged: `fs.exists` fails with `PlatformError`, which is already in the union.
3. Add `pruneCatalogSnapshots` inside `openOnchainLedger`, after `loadCatalogSnapshot`. `UtcDate`, `Sha256Hex` and `Schema` are already imported.
   ```ts
   const pruneCatalogSnapshots = Effect.fn("onchainLedger.pruneCatalogSnapshots")(
     function* (
       current: Sha256Hex
     ): Effect.fn.Return<{ readonly files: number; readonly bytes: number }, OnchainLedgerError> {
       const referenced = new Set<string>([current])
       const ledgerFiles = yield* fs.readDirectory(ledgerDir).pipe(
         Effect.mapError(mapLedgerError("prune.ledger"))
       )
       for (const name of ledgerFiles) {
         if (!name.endsWith(".jsonl")) continue
         const date = name.slice(0, -".jsonl".length)
         if (!Schema.is(UtcDate)(date)) continue
         for (const commit of yield* commits(date)) {
           referenced.add(commit.catalogFingerprint)
         }
       }
       const snapshotFiles = yield* fs.readDirectory(catalogsDir).pipe(
         Effect.mapError(mapLedgerError("prune.catalogs"))
       )
       let files = 0
       let bytes = 0
       for (const name of snapshotFiles) {
         if (!name.endsWith(".json")) continue
         const fingerprint = name.slice(0, -".json".length)
         if (!Schema.is(Sha256Hex)(fingerprint) || referenced.has(fingerprint)) continue
         const file = path.join(catalogsDir, name)
         const info = yield* fs.stat(file).pipe(Effect.mapError(mapLedgerError("prune.stat")))
         yield* fs.remove(file).pipe(Effect.mapError(mapLedgerError("prune.remove")))
         files += 1
         bytes += Number(info.size)
       }
       if (files > 0) {
         yield* Effect.logInfo("catalog snapshots pruned").pipe(
           Effect.annotateLogs({ files, bytes })
         )
       }
       return { files, bytes }
     }
   )
   ```
   Return it from the store: `return { forDay, commits, saveCatalogSnapshot, loadCatalogSnapshot, pruneCatalogSnapshots }`.
   The referenced set is built completely before the first delete, so a ledger day that fails to decode aborts with nothing deleted. This is deliberate; keep that order.

### 4.6 `src/onchain-decode.ts`

Replace `matchForFill` and its JSDoc (`:161-181`) with:
```ts
export const matchForFill = (
  fill: DecodedFill,
  byTx: ReadonlyMap<string, ReadonlyArray<MatchedOrder>>
): MatchedOrder | null => {
  let next: MatchedOrder | null = null
  for (const match of byTx.get(matchKey(fill.contract_address, fill.tx_hash)) ?? []) {
    if (match.log_index > fill.log_index && (next === null || match.log_index < next.log_index)) {
      next = match
    }
  }
  return next
}
```
Nothing else in the file changes.

### 4.7 `src/onchain-app.ts`

1. Import: `import { type OnchainLedgerError, type OnchainLedgerStore, openOnchainLedger } from "./onchain-ledger.ts"`.
2. Delete `sweepOnchainWork`'s JSDoc (`:105-109`). Keep the scratch-dir part of the body (`:114-132`) as it is, and replace the date loop (`:133-146`):
   ```ts
   export const sweepOnchainWork = Effect.fn("sweepOnchainWork")(function* (
     root: string,
     roots: ReadonlyArray<CatalogRoot>,
     commits: OnchainLedgerStore["commits"]
   ): Effect.fn.Return<void, never, FileSystem.FileSystem | Path.Path> {
     …scratch removal and chunksDir existence check unchanged…
     for (const entry of yield* fs.readDirectory(chunksDir).pipe(
       Effect.orElseSucceed((): Array<string> => [])
     )) {
       if (!Schema.is(UtcDate)(entry)) continue
       const manifests = Option.all(
         yield* Effect.forEach(roots, (catalogRoot) =>
           readOnchainManifest(catalogRoot.archiveRoot, entry)
         )
       )
       if (Option.isNone(manifests)) continue
       const published = new Set(
         manifests.value.flatMap((manifest) => manifest.provenance.scans)
       )
       const keepReason = yield* commits(entry).pipe(
         Effect.map((list) =>
           list.some((commit) => !published.has(commit.catalogFingerprint))
             ? "rescan pending"
             : undefined
         ),
         Effect.orElseSucceed(() => "ledger unreadable")
       )
       if (keepReason !== undefined) {
         yield* Effect.logInfo("onchain chunks kept").pipe(
           Effect.annotateLogs({ root, date: entry, reason: keepReason })
         )
         continue
       }
       yield* fs
         .remove(path.join(chunksDir, entry), { recursive: true, force: true })
         .pipe(Effect.ignore)
     }
   })
   ```
3. In `onchainMain`:
   - Add `OnchainLedgerError` to the declared error union: `ConfigurationError | PlatformError.PlatformError | LockError | DiskError | OnchainLedgerError`.
   - Delete the inline comment at `:416`.
   - Replace the sweep loop (`:427-429`) with:
     ```ts
     const ledger = yield* openOnchainLedger(settings.stateDir)
     for (const root of roots) {
       yield* sweepOnchainWork(root.archiveRoot, roots, ledger.commits)
     }
     ```

### 4.8 `docs/learnings.md`

Prepend this entry at the top, right under the `# polymarket-collector learnings` heading:

```
### Coverage credit is the artifact the scan wrote
A commit covers a token in its chunk only when that token could trade that day in the commit's own catalog snapshot, because the scan writes artifacts for exactly those tokens. Counting every snapshot member covered markets that were closed or not yet started at scan time, so a later widening of their window published straight into a missing artifact on every pass, with no rescan and no RPC. Boot keeps a day's chunk staging while the ledger holds a commit under a fingerprint the day's manifests do not list: that is a rescan in progress, and its committed chunks are never fetched again. Catalog snapshots are written once per fingerprint and pruned unless a commit or the current catalog uses them. A fill with no OrdersMatched after it in its contract and tx is unmatched; never borrow another match.
```

Fix two older sentences that this step makes wrong:
- In "A sidecar with no start time is not a catalog row", replace "`couldTradeDuring` therefore always sees a start time. Old ledger snapshots still decode a null start." with "Catalog rows therefore always carry a start time. Old ledger snapshots still hold null starts, and coverage reads them as could-trade, as their scans did."
- In "Gamma by-id fetch trails the list and rewrote fresh sidecars", replace the last sentence "Indexing drops `startAt: null` before a token is claimable — never inside `couldTradeDuring`, because membership in the catalog snapshot is what marks a token covered." with "Indexing drops `startAt: null` before a token is claimable — never inside `couldTradeDuring`, which must keep reading a null start in old snapshots as could-trade."

## 5. Tests

No network anywhere. All tests use the existing scripted RPC and temp directories. No comments in new or edited tests. Where an existing test you edit has inline `//` comments, delete them.

### 5.1 `src/onchain-plan.test.ts` (`describe("coveredTokens")`)

- Add `import { utcDayBoundsUs } from "./timestamps.ts"`. At the top of the describe block add `const { startUs, endUs } = Option.getOrThrow(utcDayBoundsUs(day("2026-08-08")))`.
- Add `startUs, endUs,` to the args of the three existing `coveredTokens` calls, and delete their inline comments. They keep passing: `snapshotWith` entries have `startAt: null`, which counts as could-trade.
- New test `"a snapshot token that could not trade that day is not covered"`:
  ```ts
  const fpA = "a".repeat(64)
  const [open] = snapshotWith(["111"]).entries
  const snapshot: CatalogSnapshot = {
    entries: [
      open!,
      { ...open!, tokenId: token("222"), closedAt: "2026-08-07T23:59:59Z" },
      { ...open!, tokenId: token("333"), startAt: "2026-08-09T00:00:00Z" }
    ],
    conflicts: []
  }
  const covered = yield* coveredTokens({
    chunks,
    commits: [commit(0, 9, fpA), commit(10, 19, fpA), commit(20, 29, fpA)],
    decoderVersion: 1,
    startUs,
    endUs,
    loadSnapshot: snapshotLoader(new Map([[fpA, snapshot]]))
  })
  expect(covered.forDay(token("111"))).toBe(true)
  expect(covered.forDay(token("222"))).toBe(false)
  expect(covered.forDay(token("333"))).toBe(false)
  ```

### 5.2 `src/onchain-day.test.ts`

Imports: add `Option` to the `effect` import, `import { sweepOnchainWork } from "./onchain-app.ts"`, and `import { readOnchainManifest } from "./onchain-plan.ts"`.

1. **`it.live("a restart mid-rescan keeps committed chunks and the day publishes")`.** This is the sweep repro.
   - Build a `respond` like the one in `"a failed chunk refetches only the uncommitted ranges on rerun"`: it fails `eth_getLogs` for exactly `CHUNKS[1]` while a `let failMiddle = false` flag is on. Then `const s = yield* setup({}, respond)`.
   - `runPass(s)` → `outcomes[0].tag === "published"`.
   - Write a new dota sidecar: `NEW_CONDITION`, slug `"dota2-team-e-team-f-game1"`, tokens `token("555")` and `token("666")`.
   - Set `failMiddle = true`, then `runPass(s)` → `"failed"`. Chunk 0 is now committed under the new fingerprint.
   - `const store = yield* openOnchainLedger(s.stateDir)`. For each of `[s.dotaRoot, s.lolRoot]`: `yield* sweepOnchainWork(root, s.roots, store.commits)`, then expect `fs.exists(path.join(root, ".onchain", "chunks", DATE))` to be `true`. (The old code deletes it, so this assertion fails on HEAD.)
   - Set `failMiddle = false`, `const mark = markCalls(s)`, then `runPass(s)` → `"published"`.
   - `getLogsSpans(callsSince(s, mark)).sort()` equals `[CHUNKS[1]!, CHUNKS[2]!].map((c) => `${c.from}-${c.to}`).sort()`. Chunk 0 is never fetched again.
2. **`it.live("a widened window rescans the token instead of failing on a missing artifact")`.**
   - `const s = yield* setup({})`, then `runPass(s)` → `"published"`. The closed lol market `777/888` gets no artifact.
   - Rewrite that market in `s.lolRoot` with the same fields but `closedAt: null`: `conditionId: Schema.decodeSync(ConditionId)(`0x${"ab".repeat(32)}`)`, `marketSlug: "lol-closed-market"`, outcomes `Old A`/`CLOSED_TOKEN` and `Old B`/`CLOSED_SIBLING`.
   - `const mark = markCalls(s)`, then `runPass(s)` → `"published"`. The old code fails with `artifact.missing`.
   - Spans since `mark` equal every chunk: `CHUNKS.map((c) => `${c.from}-${c.to}`).sort()`.
   - `Option.getOrThrow(yield* readOnchainManifest(s.lolRoot, DATE)).coverage.tokensCovered` contains `CLOSED_TOKEN`.
3. **`it.live("a pass prunes catalog snapshots nothing references")`.**
   - `const s = yield* setup({})`.
   - Write `path.join(s.stateDir, "catalogs", `${"0".repeat(64)}.json`)` with content `"{}"` (create the directory first).
   - `runPass(s)` → `"published"`. Expect the stray file to be gone.

### 5.3 `src/onchain-app.test.ts` (`describe("sweepOnchainWork")`)

- Add `const noCommits = () => Effect.succeed([])` in the describe block. Pass it as the third argument to the two existing `sweepOnchainWork` calls (`:215`, `:244`). Expectations stay the same: a published day with no stray commits is still removed.
- Import `OnchainLedgerError` from `./onchain-ledger.ts`.
- New `it.effect("keeps chunk state when the ledger cannot be read")`:
  - Create `.onchain/chunks/<PUBLISHED_DATE>` under `dotaRoot`, and write both manifests the way the first test does (`makeOnchainManifest`, `writeOnchainManifest`).
  - Call `sweepOnchainWork(dotaRoot, roots, () => Effect.fail(new OnchainLedgerError({ stage: "commits.decode", message: "corrupt" })))`.
  - Expect the chunk dir to still exist.

### 5.4 `src/onchain-decode.test.ts`

- `"groups OrdersMatched by (contract, tx), not tx alone"` (`:199-213`): change the two matched `logIndex` values `5 → 11` and `6 → 12`, so both follow the fill at 10. Expectations stay the same.
- Replace `"falls back to the latest match when none follows the fill"` (`:267-276`) with `"returns null when every match in the tx precedes the fill"`. Use a fill at `logIndex: 30`:
  - two matches at 8 and 12 in `testTx` → `toBeNull()`;
  - one match at 20 in `testTx` → `toBeNull()`.
- Keep `"returns null when no OrdersMatched exists in the same tx"`. Its single match at 99 still follows the fill.
- The fixture parity tests must stay green without edits. They prove real V2 logs always put OrdersMatched after their fills.

### 5.5 `src/onchain-ledger.test.ts`

1. `it.effect("saving an existing snapshot writes nothing")`:
   - `saveCatalogSnapshot(smallCatalog)`, then overwrite `path.join(dir, "catalogs", `${fingerprint}.json`)` with `"kept"`.
   - Save again: it returns the same fingerprint, and the file still reads `"kept"`.
2. `it.effect("prune keeps snapshots the ledger or the current catalog uses")`:
   - Use the helper `const closedOn = (closedAt: string) => indexSidecars([{ game: "dota", sidecar: makeSidecar({ closedAt }) }])` and save four catalogs, `fpA`..`fpD`, with closedAt `2026-08-0{1,2,3,4}T00:00:00Z`.
   - Commit `{ from: 1, to: 2 }` under `fpA` through `store.forDay(context({ catalogFingerprint: fpA }))`.
   - Commit `{ from: 3, to: 4 }` under `fpB` on `"2026-08-09"` through `context({ date: "2026-08-09" as UtcDate, catalogFingerprint: fpB })`.
   - Record `Number((yield* fs.stat(fileOf(fpD))).size)`, and create a directory `catalogs/leftover-temp`.
   - `expect(yield* store.pruneCatalogSnapshots(fpC)).toEqual({ files: 1, bytes: <fpD size> })`.
   - The files for `fpA`, `fpB` and `fpC` still exist, `fpD` is gone, and `leftover-temp` still exists.

## 6. Docs

Only `docs/learnings.md` (§4.8). The contract and the compose docs have no rule this step changes.

## 7. Edge cases, concerns, tradeoffs

- **Residual brick paths this step does not close. Report them to the orchestrator; do not fix them here.** Commits made before a publish keep crediting their tokens after the publish deletes `chunks/<date>`. A day re-planned later can still fail `artifact.missing` in two cases:
  - (a) its onchain manifest is lost or corrupt, so the day reopens with every token "covered" and no artifacts;
  - (b) a token could trade in a pre-publish commit's snapshot, then was excluded at publish because its window shrank between two passes, and later widened again.

  Both need an unusual sequence. The design treats a missing artifact behind a commit as integrity loss, not as a rescan trigger (`OnchainIntegrityError` doc). The complete fix is to also require the artifact file in `inChunk`. That turns every brick into a rescan, at the cost of one directory listing per chunk per coverage call. The owner chose the could-trade proxy, so leave it.
- **An abandoned rescan keeps its staging.** Suppose a day reopens under catalog B, partly rescans, and then the catalog changes back so the day is complete again. Then `chunks/<date>` stays until the day publishes again. This is required: B's commits still claim coverage, so deleting their artifacts would recreate the brick. The cost is one day's small JSON files and one INFO line per boot.
- **The sweep ignores decoder versions** (as the spec says). A commit at any version whose fingerprint is outside the manifests' `scans` keeps the staging. Old-version commits normally belong to days whose staging is already gone, so this does not leak in practice.
- **Prune reads the whole ledger every pass.** Today that is 3.6k lines and 2.4 MB. After a year it is roughly 65k lines and 45 MB, about 1–2 s of decode every 5 minutes. If that ever matters, prune only on passes that wrote a new snapshot. Do not do that now.
- **First pass after deploy** deletes about 1,070 files (~2 GiB) at once. That is fine, and STEP-007's "`catalogs/` does not grow" check expects it.
- **Skip-if-exists, not decode-on-save.** A bit-rotted current snapshot is no longer rewritten. Coverage then treats it as absent and the day fails `coverage` loudly without RPC, because its chunks are already committed under the current fingerprint. The next catalog change (dozens per day) heals it.
- **Prune never deletes snapshots of commits that are still readable**, but it does delete a snapshot that only a manifest's `versions.catalogFingerprint` names. Nothing reads snapshots by that field; `loadCatalogSnapshot` is called only with commit fingerprints.
- **Boot now opens the ledger.** If `<stateDir>/ledger` cannot be created, the service fails at boot (Docker restart loop, visible) instead of failing every pass.
- **`matchForFill` beyond the spec.** Removing the single-match shortcut is the root-cause fix the bug-hunt demo calls for. If some future contract emitted a fill after its only match, the day fails loudly (`fills without OrdersMatched`) instead of publishing wrong `taker_*`. That is the documented intent.

## 8. Verification

Run in `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`. No network, no VPS.

1. `yarn vitest run src/onchain-app.test.ts src/onchain-day.test.ts src/onchain-plan.test.ts src/onchain-ledger.test.ts src/onchain-decode.test.ts src/onchain-catalog.test.ts` (baseline at HEAD: 6 files, 67 tests, all green in about 3 s).
2. `yarn vitest run src/onchain-publish.test.ts src/onchain-repair.test.ts src/onchain-import.test.ts`
3. `yarn typecheck`
4. `yarn diagnostics`: 0 errors, and no new warnings in the touched files.
5. `yarn lint`
6. `yarn check` must exit 0. It also runs as the pre-commit hook.
7. Self-checks:
   - Write the §5.2 tests 1 and 2 before the `src` changes and run them once on HEAD. Test 1 must fail at the chunk-dir assertion, because the old sweep deletes it. Test 2 must fail with tag `failed`, because the old code hits `artifact.missing`. Then implement and make them pass. Do not use `git stash` or `git checkout` to get there.
   - `grep -n "fallback" src/onchain-decode.ts` prints nothing.
   - `grep -n "pruneCatalogSnapshots" src/onchain-day.ts src/onchain-ledger.ts` shows the call and the method.
   - `git diff -U0 -- src | grep '^+' | grep -E '//|/\*'` prints nothing (no new comments).
   - `wc -l src/onchain-*.ts`: every file is under 1,000 lines.

## 9. Done when

- The boot sweep keeps `.onchain/chunks/<date>` while the ledger has a commit whose fingerprint is outside both manifests' `scans`, or the ledger cannot be read, and logs INFO `onchain chunks kept` with the reason. A finished day's staging is still removed.
- `coveredTokens` credits a token only when it could trade that day in the commit's snapshot. A widened window rescans and publishes instead of failing `artifact.missing`.
- `matchForFill` returns the next match after the fill, or `null`. There is no fallback and no single-match shortcut.
- `saveCatalogSnapshot` does not rewrite an existing snapshot. `pruneCatalogSnapshots` runs once per pass, removes only unreferenced `<64-hex>.json` files, logs INFO `{files, bytes}`, and never fails the pass.
- `docs/learnings.md` has the new entry and the two corrected sentences.
- `yarn check` is green, and there is one commit on `main`. Suggested message: `fix: keep onchain rescans across restarts, cover only tokens a scan wrote, never borrow a match, write catalog snapshots once`. Not pushed. `feature.json` `passes` is left to the orchestrator; append to `progress.txt` as the implement skill says.
