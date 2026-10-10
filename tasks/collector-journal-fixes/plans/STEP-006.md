# STEP-006 plan: `journal-resplit` CLI moves a lost hour 00 out of `D-1/23.jsonl.gz` into `D/00.jsonl.gz`

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-006` (priority 6).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Build on the current HEAD (`d87388f`, after STEP-001..005). Commit there. Do not push.
Figma or design links: none.

Before you start, read `polymarket-collector/AGENTS.md` and the top entries of `docs/learnings.md`.

Evidence (read-only, in `betting_workspace/runs/collector-bug-hunt-20261010/`): `FINAL_REPORT.md` §4.1 and §6.2 (variant A), `work/p2-fix-spec-review/nextday-00.txt`, `evidence/lost-hour00-records.txt`, `evidence/journal-sample/`.

STEP-007/008 run later on the VPS. They use this CLI through `docker compose run --rm --no-deps archive-<game> node dist/journal-resplit-main.js …`. So the CLI must build into `dist/` and must be documented for that use.

## 1. The problem

Before STEP-001, the rotation loop sometimes skipped a whole hour. When it skipped midnight, the records of hour 00 of day `D` went into `events/<D-1>/23.jsonl.gz`. `events/<D>/00.jsonl.gz` was never written.
- Replay of `D` reads only `events/<D>/*.jsonl.gz`, so it never sees them.
- Replay of `D-1` reads them but drops their rows (`sameDate`).

This lost 10 hours: Dota D ∈ {08-15, 09-04, 09-15, 09-20, 09-23}, LoL D ∈ {09-07, 09-10, 09-11, 09-14, 10-01}. The data is still on disk, as the tail of `D-1/23.jsonl.gz`. `lost-hour00-records.txt` counts that tail per date, for example Dota 08-15 = 45 270 records. Those counts are what the CLI must report as `tailRecords`.

The fix is an offline, one-shot cut of that file. It touches no network and no checkpoints. STEP-008 then moves the two manifests aside, and the compactor rebuilds `D-1`, then `D`.

## 2. Design (decided; follow it)

1. **New module `src/journal-resplit.ts`.** It exports one function, `resplitJournalHour(args)`, and the `ResplitReport` type. It handles one pair, `D-1 → D`, per call.
2. **New entry point `src/journal-resplit-main.ts`.** It is built after `src/onchain-repair-main.ts` and `src/onchain-import-main.ts`, and takes these arguments: `--root <archive-root> --date <YYYY-MM-DD> [--dry-run] [--backup-dir <path>]`. When `--backup-dir` is not given, it uses `<root>/.recovery/<DateTime.formatIso(now)>`. `yarn build` emits `dist/journal-resplit-main.js`, and the Dockerfile already copies `dist/`. No Dockerfile, compose or package.json change.
3. **Paths.** Let `startUs` be `utcDayBoundsUs(D).startUs` and `prev` be `utcDateOfUs(startUs - 1)`. Both helpers are in `src/timestamps.ts`. Then:
   - `source = events/<prev>/23.jsonl.gz`
   - `target = events/<D>/00.jsonl.gz`
   - `backupPath = <backupDir>/events/<prev>/23.jsonl.gz`, computed as `path.join(backupDir, path.relative(archiveRoot, source))`
   - `headTmp = events/<prev>/23.jsonl.gz.tmp`
   - `tailTmp = events/<D>/00.jsonl.gz.tmp`

   Both temp names use `GZ_TMP_SUFFIX`.
4. **Preconditions.** Each failure is a `JournalError` whose message starts with `resplit.precondition:`. All of them run before anything is written, in dry run too:
   - `source` exists;
   - `target` and `events/<D>/00.jsonl.partial` do not exist;
   - `backupPath` does not exist. A backup is never overwritten.
   - The scan (item 5) finds at least one tail record.
5. **Scan pass (read-only).** Stream `gunzip(source)` and split it into byte lines, each with its trailing `\n`. For each line:
   - The last byte must be `0x0a`. If it is not, fail with `resplit.scan:`. Only the final line can lack it, because it is the leftover at end of stream.
   - Decode only `receivedAtUs`, with a module-level decoder: `Schema.fromJsonString(Schema.Struct({ receivedAtUs: Microseconds }))`. Extra keys are ignored. If decoding fails, fail with `resplit.decode: line N:`.
   - Until the first record with `receivedAtUs ≥ startUs`, the line is **head**.
   - From that record on, every line is **tail**, and it must satisfy `startUs ≤ receivedAtUs < startUs + 3_600_000_000`. If not, fail with `resplit.precondition: line N …`. This one check makes sure the tail is a contiguous suffix that ends before `D 01:00`.

   The scan counts records and bytes for head and tail, and records the tail's first and last `receivedAtUs`. It also fails when `tailRecords === 0`.

   Never decode a whole record and never re-serialize anything: a line's bytes go out exactly as they came in.
6. **Dry run.** Return the report, with `verifiedSha256: null` and `backupPath: null`. Take no lock and create no file or directory.
7. **Real run, under the compactor lock.** Wrap steps a–f in `Effect.acquireUseRelease(acquireProcessLock(archiveRoot, COMPACT_LOCK_FILE_NAME), …, (lock) => lock.release)`, the same way `onchain-repair.ts` takes its lock. The compact service holds `.compactor.lock` for its whole life. So the CLI fails with a `LockError` while compact runs, and STEP-008 has to stop it first. The collector is not locked: it keeps running and never touches past hours.

   a. `mkdir -p` the parent directory of `target`.
   b. Write `headTmp` as gzip of `lines(source).pipe(Stream.take(headRecords))`.
   c. Write `tailTmp` as gzip of `lines(source).pipe(Stream.drop(headRecords))`.
   d. Verify. `sha256GunzipFiles([headTmp, tailTmp])` must equal `sha256GunzipFiles([source])`, in both `sha256` and `bytes`. If it does not, fail with `resplit.verify:`.
   e. Copy `source` to `backupPath` (`mkdir -p` its parent first). Then check that `sha256File(backupPath)` equals `sha256File(source)`. If not, fail with `resplit.backup:`.
   f. Rename `tailTmp → target` first, then `headTmp → source`.

   If any step a–f fails, remove both temp files (`force: true`, errors ignored), then fail.

   The rename order matters. Once `D/00` exists, the archive is consistent for compaction even if the process dies before the second rename: replay of `D-1` drops the duplicated tail as rows outside the date, and replay of `D` reads `D/00`.
8. **Report and output.** `ResplitReport` is flat:
   - `source` and `target`;
   - `headRecords` and `headBytes`;
   - `tailRecords` and `tailBytes`;
   - `tailFirstReceivedAtUs` and `tailLastReceivedAtUs`;
   - `verifiedSha256: string | null`, the sha256 of gunzip(original), which equals gunzip(head) ++ gunzip(tail);
   - `backupPath: string | null`.

   The main logs it once with the JSON logger. The message is `journal resplit dry run` or `journal resplit done`, annotated with `{ date, dryRun, ...report }`. Any failure exits non-zero through `runMain`.
9. **Reuse the journal's gzip code; do not copy it.** `src/journal.ts` already has the gzip, gunzip and sha256 pipelines inline. Generalize them as described in §4.1 and use them from the new module.
10. **No new comments anywhere, tests included.** Delete the JSDoc of the two `journal.ts` functions you refactor.

## 3. Scope

In scope:
- New: `src/journal-resplit.ts`, `src/journal-resplit-main.ts`, `src/journal-resplit.test.ts`
- Edit: `src/journal.ts` (export and generalize 3 helpers, §4.1)
- Docs: `docs/docker-compose.md` (new section), `docs/polymarket_dota_archive_contracts.md` (§5.1 bullet + §22 sentence), `docs/learnings.md` (one entry on top)

Out of scope (do not do):
- Do not touch checkpoints, manifests, replay, compaction, or the onchain code. Moving manifests and recompacting is STEP-008's manual runbook.
- Do not loop over several dates in one call, auto-detect lost hours, or add an "undo" or "resume" mode.
- Do not handle any other broken layout (two-hour files, a label one hour behind). The precondition check rejects them, and that is enough.
- Do not put the real-sample smoke into the vitest suite. The Docker build runs `yarn check`, and the sample lives outside the repo (see §6).
- No new dependency, env var, or config.

Code rules (`yarn check` runs Effect diagnostics and lint):
- No `new Date`, `Date.now`, `JSON.parse` (use `Schema.fromJsonString`), `setTimeout` or `new Promise` in `src`.
- `verbatimModuleSyntax` is on: type-only imports use `import type` / `type`.
- No `!` non-null assertions in non-test `src`.
- Build schema decoders once at module level, never per line.
- `catchUnfailableEffect` is an error: use `Effect.ignore` or `Effect.catch` only on effects that can fail.

## 4. Code changes

### 4.1 `src/journal.ts`: export three helpers (behaviour of rotation unchanged)

1. Change `const mapJournalError =` to `export const mapJournalError =`.
2. Replace the private `gzipFileStreaming(sourcePath, destPath, sourceExists)` and its JSDoc with an exported helper that gzips any byte stream:

```ts
export const gzipToFile = <E, R>(
  source: Stream.Stream<Uint8Array, E, R>,
  destPath: string
): Effect.Effect<void, JournalError, FileSystem.FileSystem | R> =>
  Effect.gen(function* () {
    const fs = yield* FileSystem.FileSystem
    yield* source.pipe(
      Stream.pipeThroughChannelOrFail(
        NodeStream.fromDuplex({
          evaluate: () => createGzip(),
          onError: (error) => new JournalError({ message: `gzip: ${errorMessage(error)}` }),
          endOnDone: true
        })
      ),
      Stream.run(fs.sink(destPath, { flag: "w" })),
      Effect.mapError(gzipError)
    )
    yield* syncFile(destPath).pipe(Effect.mapError(mapJournalError("gzip.sync")))
  })
```

Call site in `rotatePartialFile`: `yield* gzipToFile(exists ? fs.stream(partialPath) : Stream.empty, gzTmpPath)`. If TypeScript cannot infer `E` from that union, annotate the source as `Stream.Stream<Uint8Array, PlatformError.PlatformError>`.

3. Replace the private `sha256GunzipFile(gzPath)` and its JSDoc with two exports:

```ts
export const gunzipFile = (
  gzPath: string
): Stream.Stream<Uint8Array, JournalError, FileSystem.FileSystem> =>
  Stream.unwrap(
    Effect.map(Effect.service(FileSystem.FileSystem), (fs) =>
      fs.stream(gzPath).pipe(
        Stream.pipeThroughChannelOrFail(
          NodeStream.fromDuplex({
            evaluate: () => createGunzip(),
            onError: (error) =>
              new JournalError({ message: `gunzip: ${gzPath}: ${errorMessage(error)}` }),
            endOnDone: true
          })
        ),
        Stream.mapError((error) =>
          Schema.is(JournalError)(error)
            ? error
            : new JournalError({ message: `gunzip: ${gzPath}: ${error.message}` })
        )
      )
    )
  )

export const sha256GunzipFiles = Effect.fn("sha256GunzipFiles")(
  function* (
    gzPaths: ReadonlyArray<string>
  ): Effect.fn.Return<
    { readonly sha256: string; readonly bytes: number },
    JournalError,
    FileSystem.FileSystem
  > {
    const hash = createHash("sha256")
    let bytes = 0
    for (const gzPath of gzPaths) {
      yield* gunzipFile(gzPath).pipe(
        Stream.runForEach((chunk) =>
          Effect.sync(() => {
            hash.update(chunk)
            bytes += chunk.byteLength
          })
        )
      )
    }
    return { sha256: Encoding.encodeHex(hash.digest()), bytes }
  }
)
```

The two call sites in `rotatePartialFile` become `sha256GunzipFiles([gzPath])` and `sha256GunzipFiles([gzTmpPath])`.

The gunzip error text changes from `rotation.verify: gunzip: …` to `gunzip: <path>: …`. No test asserts that text. The only `rotation.verify` assertion is about the unterminated tail, and that message stays. `Effect.service` and `Stream.unwrap` exist in the installed effect `4.0.0-beta.105`.

### 4.2 `src/journal-resplit.ts` (new)

Imports:
- `Effect, FileSystem, Option, Path, Schema, Stream` from `effect`;
- `acquireProcessLock`, `type LockError` from `./fs/lock.ts`;
- `COMPACT_LOCK_FILE_NAME`, `layoutFor` from `./fs/layout.ts`;
- `sha256File` from `./hash.ts`;
- `GZ_SUFFIX`, `GZ_TMP_SUFFIX`, `PARTIAL_SUFFIX`, `JournalError`, `gunzipFile`, `gzipToFile`, `mapJournalError`, `sha256GunzipFiles` from `./journal.ts`;
- `decodeRecord` from `./schema/decode.ts`;
- `Microseconds`, `type UtcDate` from `./schema/primitives.ts`;
- `utcDateOfUs`, `utcDayBoundsUs` from `./timestamps.ts`.

Shape (reference implementation; keep it this small):

```ts
const HOUR_US = 3_600_000_000

const decodeReceivedAt = decodeRecord(
  "JournalReceivedAt",
  Schema.fromJsonString(Schema.Struct({ receivedAtUs: Microseconds }))
)

const utf8 = new TextDecoder()

export interface ResplitReport {
  readonly source: string
  readonly target: string
  readonly headRecords: number
  readonly headBytes: number
  readonly tailRecords: number
  readonly tailBytes: number
  readonly tailFirstReceivedAtUs: number
  readonly tailLastReceivedAtUs: number
  readonly verifiedSha256: string | null
  readonly backupPath: string | null
}

type Scan = Pick<ResplitReport, "headRecords" | "headBytes" | "tailRecords" | "tailBytes" | "tailFirstReceivedAtUs" | "tailLastReceivedAtUs">

const emptyScan: Scan = { headRecords: 0, headBytes: 0, tailRecords: 0, tailBytes: 0, tailFirstReceivedAtUs: 0, tailLastReceivedAtUs: 0 }

const splitLines = <E, R>(bytes: Stream.Stream<Uint8Array, E, R>): Stream.Stream<Uint8Array, E, R> =>
  Stream.mapAccum(
    bytes,
    (): Uint8Array => new Uint8Array(0),
    (pending, chunk) => {
      const lines: Array<Uint8Array> = []
      let rest: Uint8Array = Buffer.concat([pending, chunk])
      for (let newline = rest.indexOf(0x0a); newline >= 0; newline = rest.indexOf(0x0a)) {
        lines.push(rest.subarray(0, newline + 1))
        rest = rest.subarray(newline + 1)
      }
      return [rest, lines] as const
    },
    { onHalt: (pending) => (pending.length > 0 ? [pending] : []) }
  )

const scanLine = (startUs: number) => (scan: Scan, line: Uint8Array) =>
  Effect.gen(function* () {
    const lineNumber = scan.headRecords + scan.tailRecords + 1
    if (line[line.length - 1] !== 0x0a) {
      return yield* new JournalError({ message: `resplit.scan: line ${lineNumber} has no trailing newline` })
    }
    const { receivedAtUs } = yield* decodeReceivedAt(utf8.decode(line)).pipe(
      Effect.mapError(mapJournalError(`resplit.decode: line ${lineNumber}`))
    )
    if (scan.tailRecords === 0 && receivedAtUs < startUs) {
      return { ...scan, headRecords: scan.headRecords + 1, headBytes: scan.headBytes + line.length }
    }
    if (receivedAtUs < startUs || receivedAtUs >= startUs + HOUR_US) {
      return yield* new JournalError({
        message: `resplit.precondition: line ${lineNumber} receivedAtUs ${receivedAtUs} breaks the hour-00 tail [${startUs}, ${startUs + HOUR_US})`
      })
    }
    return {
      ...scan,
      tailRecords: scan.tailRecords + 1,
      tailBytes: scan.tailBytes + line.length,
      tailFirstReceivedAtUs: scan.tailRecords === 0 ? receivedAtUs : scan.tailFirstReceivedAtUs,
      tailLastReceivedAtUs: receivedAtUs
    }
  })
```

`publishResplit(source, target, headRecords, backupPath)` is a private `Effect.fn` that returns the verified sha256. It runs §2.7 steps a–f, with `Effect.onError(() => Effect.all([fs.remove(headTmp, { force: true }), fs.remove(tailTmp, { force: true })]).pipe(Effect.ignore))` on the whole sequence:
- Build the line stream once: `const lines = splitLines(gunzipFile(source))`. A Stream is a description, so each `take`, `drop` or run re-reads the file. `mapAccum`'s initial state is a `LazyArg`, so every run starts empty.
- Map platform errors with `mapJournalError("resplit.mkdir" | "resplit.backup" | "resplit.publish")`. Map `sha256File` errors with `mapJournalError("resplit.backup")`.

`resplitJournalHour = Effect.fn("resplitJournalHour")(function* (args: { readonly archiveRoot: string; readonly date: UtcDate; readonly dryRun: boolean; readonly backupDir: string }): Effect.fn.Return<ResplitReport, JournalError | LockError, FileSystem.FileSystem | Path.Path>`:
1. Compute `bounds = utcDayBoundsUs(args.date)` and `prev = Option.flatMap(bounds, (b) => utcDateOfUs(b.startUs - 1))`. If either is None, fail with `resplit.date: <date> is not a real UTC day`.
2. Build the paths from `layoutFor(args.archiveRoot).events` (§2.3). Then check the preconditions of §2.4 with `fs.exists`, mapping its errors through `mapJournalError("resplit.exists")`.
3. `scan = yield* gunzipFile(source).pipe(splitLines, Stream.runFoldEffect(() => emptyScan, scanLine(bounds.value.startUs)))`. Then, if `scan.tailRecords === 0`, fail with `resplit.precondition: <source> has no record at or after <date>T00:00:00Z`.
4. Set `report = { source, target, ...scan, verifiedSha256: null, backupPath: null }`. If `args.dryRun`, return it.
5. Otherwise return `yield* Effect.acquireUseRelease(acquireProcessLock(args.archiveRoot, COMPACT_LOCK_FILE_NAME), () => publishResplit(...).pipe(Effect.map((verifiedSha256) => ({ ...report, verifiedSha256, backupPath }))), (lock) => lock.release)`.

Fallback, only if needed: if `Stream.take` stopping the gunzip duplex early raises an error, write the head with `Stream.zipWithIndex` + `Stream.filter(([, i]) => i < headRecords)` + `Stream.map(([line]) => line)`. Do not switch for any other reason.

### 4.3 `src/journal-resplit-main.ts` (new)

Mirror `src/onchain-repair-main.ts`:
- Imports: `NodeFileSystem`, `NodePath`, `runMain` from deep `@effect/platform-node/*` paths; `parseArgs` from `node:util`.
- Layer: `Layer.mergeAll(NodeFileSystem.layer, NodePath.layer, Logger.layer([Logger.withConsoleLog(Logger.formatJson)]))`. No NodeCrypto, no DuckDB.

```ts
const usage = "usage: journal-resplit --root <archive-root> --date <YYYY-MM-DD> [--dry-run] [--backup-dir <path>]"
```

Steps:
1. Call `parseArgs({ args: process.argv.slice(2), strict: true, options: { root: { type: "string" }, date: { type: "string" }, "dry-run": { type: "boolean" }, "backup-dir": { type: "string" } } })` inside `Effect.try`. On failure, return `ConfigurationError` with the message `invalid arguments: …\n${usage}`.
2. If `root` or `date` is undefined, fail with `ConfigurationError("--root and --date are required\n" + usage)`.
3. Decode `date` with `Schema.decodeEffect(UtcDate)`. On failure, return `ConfigurationError` with the message `--date must be YYYY-MM-DD: …`.
4. `backupDir = args.values["backup-dir"] ?? path.join(root, ".recovery", DateTime.formatIso(yield* DateTime.now))`.
5. `report = yield* resplitJournalHour({ archiveRoot: root, date, dryRun, backupDir })`.
6. `yield* Effect.logInfo(dryRun ? "journal resplit dry run" : "journal resplit done").pipe(Effect.annotateLogs({ date, dryRun, ...report }))`.

## 5. Tests: `src/journal-resplit.test.ts` (new, `describe("resplitJournalHour")`)

Setup:
- Layer: `Layer.mergeAll(NodeFileSystem.layer, NodePath.layer)`.
- Every test is `it.live(..., () => Effect.gen(...).pipe(Effect.scoped, Effect.provide(nodeServices)))`. Use `it.live` because the lock and the file IO are real.
- From `./replay.testing.ts`, reuse `withArchiveRoot`, `writeJournal(filePath, records, true, tail?)`, `catalog(sequence, receivedAtUs)`, `date` (2026-08-08), `previousDate` (2026-08-07) and `targetStartUs` (2026-08-08T00:00Z in µs).
- Let `M = 60_000_000` (one minute in µs).

Local helpers, without comments:
- `fixture(receivedAt: ReadonlyArray<number>, tail = "")`:
  - writes `<events>/${previousDate}/23.jsonl.gz` with `receivedAt.map((us, i) => catalog(i, us))` and `tail`;
  - returns `{ root, source, target, backupDir: path.join(root, ".recovery", "t"), original: yield* fs.readFile(source), run: (dryRun) => resplitJournalHour({ archiveRoot: root, date, dryRun, backupDir }) }`.
- `expectUntouched(f)`:
  - the bytes of `source` equal `original`;
  - `target` does not exist, and neither does `<root>/.recovery`;
  - no entry of `fs.readDirectory(root, { recursive: true })` ends with `GZ_TMP_SUFFIX`;
  - `<root>/.compactor.lock` does not exist.
- `valid = [targetStartUs - 30 * M, targetStartUs - 10 * M, targetStartUs - 1, targetStartUs, targetStartUs + 5 * M, targetStartUs + 59 * M]`

Cases:
1. `"moves the next-day tail of D-1/23 into D/00 byte for byte"`: run `fixture(valid)` with `dryRun: false`, then check:
   - The report matches `{ headRecords: 3, tailRecords: 3, tailFirstReceivedAtUs: targetStartUs, tailLastReceivedAtUs: targetStartUs + 59 * M, backupPath: <backupDir>/events/2026-08-07/23.jsonl.gz }`.
   - `Buffer.concat([gunzipSync(new 23), gunzipSync(new 00)])` equals `gunzipSync(original)`.
   - `report.headBytes + report.tailBytes` equals the length of `gunzipSync(original)`.
   - `report.verifiedSha256` equals the sha256 hex of `gunzipSync(original)` (`createHash` from `node:crypto`).
   - `decodeJournalLines` (from `./journal.testing.ts`) succeeds on both halves. The head `receivedAtUs` list is `valid.slice(0, 3)` and the tail list is `valid.slice(3)`.
   - The backup bytes equal `original`.
   - There are no `.gz.tmp` files and no `.compactor.lock`.
2. `"refuses a file with no record at or after midnight and changes nothing"`: `fixture([targetStartUs - 2 * M, targetStartUs - M])`. `Effect.flip(f.run(false))` is a `JournalError` whose message contains `resplit.precondition` and `no record at or after`. `expectUntouched(f)` passes. `f.run(true)` fails the same way.
3. `"refuses when D already has an hour-00 gzip or partial"`: for each `suffix` of `[GZ_SUFFIX, PARTIAL_SUFFIX]`:
   - make a fresh `fixture(valid)`;
   - write an empty `<events>/${date}/00${suffix}` with `writeJournal(path, [], suffix === GZ_SUFFIX)`;
   - `run(false)` fails with a message that contains `already exists`;
   - the bytes of `source` equal `original`.
4. `"refuses a tail that is not one contiguous suffix inside hour 00, or an unterminated last line"`: three fixtures. Each `run(false)` fails, and `expectUntouched` passes for each.
   - `[targetStartUs - M, targetStartUs + M, targetStartUs - 1]` fails with `resplit.precondition`.
   - `[targetStartUs - M, targetStartUs + M, targetStartUs + 60 * M]` fails with `resplit.precondition`.
   - `fixture(valid, '{"schemaVersion":1')` fails with `resplit.scan`.
5. `"dry run reports both parts and creates no files"`: `fixture(valid)`.
   - Take `before = (yield* fs.readDirectory(root, { recursive: true })).sort()`.
   - Run with `dryRun: true`. The report has `headRecords 3`, `tailRecords 3`, `verifiedSha256: null` and `backupPath: null`.
   - `after`, taken the same way, equals `before`, and the bytes of `source` equal `original`.
6. `"refuses to write while the compactor holds its lock"`: `fixture(valid)`. Run `Effect.acquireUseRelease(acquireProcessLock(root, COMPACT_LOCK_FILE_NAME), () => f.run(false).pipe(Effect.result), (lock) => lock.release)`. Copy this pattern from `onchain-repair.test.ts`, "fails while another process holds the onchain lock". The result is a failure. Check the bytes of `source` and that `target` is absent (skip the lock-file check in this one).

Existing `journal.test.ts` must stay green unchanged. It covers the refactored rotation helpers, including the test "rotates and verifies gzip output, then never appends to the gzip" and the test that publishes an empty gzip.

## 6. Docs

### 6.1 `docs/docker-compose.md`: new section right before `## Upgrade from the old single-process image`

````markdown
## Re-split a lost hour 00

When a midnight rotation was skipped, the records of hour 00 of day `D` sit at the
end of `events/<D-1>/23.jsonl.gz` and `events/<D>/00.jsonl.gz` does not exist. The
one-shot `journal-resplit` command in the same image moves that tail into
`D/00`. Stop the game's compact service first: the command takes
`.compactor.lock` and fails while compact holds it. Collect keeps running.

```sh
docker compose stop compact-dota
docker compose run --rm --no-deps archive-dota \
  node dist/journal-resplit-main.js --root /data --date 2026-09-23 --dry-run
docker compose run --rm --no-deps archive-dota \
  node dist/journal-resplit-main.js --root /data --date 2026-09-23
```

Use `archive-lol` (and `compact-lol`) for LoL; `/data` is that service's archive
root. One call handles one `D-1 → D` pair. It refuses when `D/00` already exists,
when `D-1/23` has no record with `receivedAtUs` at or after `D 00:00`, and when
those records are not one contiguous suffix that ends before `D 01:00`. Every
check runs before anything in `events/` changes, and a failed check exits
non-zero. `--dry-run` prints the record and byte counts of both parts and the
first and last `receivedAtUs` of the tail, and writes nothing. A real run writes
both parts as `.gz.tmp` files and publishes them only when
`gunzip(23) ++ gunzip(00)` has the same sha256 as `gunzip` of the original. It
prints the same fields plus `verifiedSha256` and `backupPath`. Without
`--backup-dir`, the original is kept at
`/var/lib/polymarket-<game>-archive/.recovery/<timestamp>/events/<D-1>/23.jsonl.gz`.
Each run needs free disk for one more copy of that file and for both parts.

The checkpoint of `D-1` still ends after the old tail. Before compact starts
again, move `manifests/<D-1>.json` and `manifests/<D>.json` aside. Then
`docker compose up -d compact-dota`: startup catch-up recompacts the days in
ascending date order.
````

### 6.2 `docs/polymarket_dota_archive_contracts.md`

§5.1 (around line 245): right after the bullet that begins `- существующий \`.gz\` никогда не перезаписывается`, add:

```markdown
- единственное исключение — офлайн controlled migration `journal-resplit` (§22) при
  остановленном compact: если ротация полуночи была пропущена, хвост
  `D-1/23.jsonl.gz` с `receivedAtUs ≥ D 00:00` переносится в новый `D/00.jsonl.gz`.
  Хвост — непрерывный суффикс внутри часа 00, `D/00.*` до этого не существует; обе
  части пишутся через `.gz.tmp` и публикуются rename-ом, только если
  `sha256(gunzip(23) ++ gunzip(00)) == sha256(gunzip(оригинал))`; оригинал
  сохраняется в `<ARCHIVE_ROOT>/.recovery/<ts>/events/D-1/23.jsonl.gz`.
```

§22: the last paragraph ends with `без явного controlled migration.` (around line 1235; the feature.json reference to lines 1157–1159 is stale). Append:

```markdown
Для journal такой migration один — `journal-resplit` (§5.1, запуск — в
`docs/docker-compose.md`); после него манифесты `D-1` и `D` убираются, и оба дня
перекомпактируются по возрастанию дат.
```

### 6.3 `docs/learnings.md`: prepend at the top, under the `# polymarket-collector learnings` title

```markdown
### A skipped midnight is healed by splitting the previous hour 23
When the midnight rotation was skipped, hour 00 of a day sits at the end of the previous day's hour-23 gzip: replay of the day never opens that file and replay of the previous day drops those rows. The resplit command cuts that gzip at the first record stamped at or after midnight and publishes the tail as the day's hour 00, only when both halves gunzip back to the original bytes; the original is kept outside the journal tree. It refuses when hour 00 already exists, when the tail is not one contiguous run inside hour 00, and while the compactor holds its lock. The previous day's checkpoint still ends after the old tail, so both days are recompacted in date order.
```

## 7. Edge cases, concerns, tradeoffs

- **Checkpoint order (STEP-008's job, documented here).** `checkpoints/<D-1>.json` was built from the old `23`, tail included. Its journal position is therefore past the records that now live in `D/00`. If `D` were replayed with that seed, it would fail loudly at `order.position`. Recompacting `D-1` first rewrites the checkpoint, ending at the last head record. LoL 09-10 is both a `D` and a `D-1`, which is why ascending order is mandatory. The CLI does not touch checkpoints or manifests.
- **Split key.** The split key is `receivedAtUs`, not the source timestamp. This matches the live rotation rule (contract §5.1). A row in `D/00` whose effective timestamp falls before midnight is still picked up by `D-1`'s neighbour read of `D/00`.
- **Crash between the two renames.** `D/00` is published and `D-1/23` still holds the tail; both are consistent for replay. A re-run fails with `already exists`. An operator can finish the job by hand by renaming `23.jsonl.gz.tmp`, if it is still there. A crash before the first rename leaves `events/` unchanged. At worst, `.gz.tmp` leftovers remain, and replay ignores them because their names do not end in `.jsonl.gz`.
- **Disk.** The ten source files total about 1.7 GB (Dota ~0.4 GB, LoL ~1.27 GB; the largest is LoL 09-09/23 at 352 MB). Each run temporarily adds about two copies of its file (temp parts plus the backup). The collect service stops writing below 5 GiB free, so STEP-008 should check `df` first.
- **Runtime.** The sample `dota-journal-2026-10-08-15.jsonl.gz` is 54 MB gzipped and 518 MB raw, with 645k lines. The largest pair (LoL, about 352 MB gzipped, about 3.4 GB raw, about 4M lines) should take minutes:
  - gunzip passes over the original: scan, head, tail, and verify;
  - one gunzip pass over the parts;
  - one gzip pass over all bytes;
  - a per-line `receivedAtUs` decode.

  Memory stays flat, because nothing buffers the file.
- **The lock covers the writes only.** The scan reads before the lock is taken. Nothing else writes past journal hours: the collector writes only the current hour, and compact never writes `events/`.
- **What the verify proves.** `take(n)`/`drop(n)` over the same deterministic line stream, followed by a byte-for-byte hash of the concatenation, proves that nothing was lost, duplicated or reordered. The scan alone decides where the cut is.
- **Tail limits.** A head can be empty, and `D-1/23` then becomes an empty gzip, which is legal. A tail cannot be empty.

## 8. Verification

Run in `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`:

```sh
yarn vitest run src/journal-resplit.test.ts src/journal.test.ts
yarn check
yarn build && test -f dist/journal-resplit-main.js
node dist/journal-resplit-main.js; echo "exit=$?"
```

The last command must print the usage error and exit non-zero.

Real-sample smoke (manual, not in the suite). It must fail the no-tail precondition and change nothing. Record the dry-run wall time in progress.txt as the scan-speed estimate for the VPS:

```sh
SMOKE=$(mktemp -d)
mkdir -p "$SMOKE/events/2026-10-08"
cp /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/runs/collector-bug-hunt-20261010/evidence/journal-sample/dota-journal-2026-10-08-15.jsonl.gz "$SMOKE/events/2026-10-08/23.jsonl.gz"
shasum -a 256 "$SMOKE/events/2026-10-08/23.jsonl.gz"
time node dist/journal-resplit-main.js --root "$SMOKE" --date 2026-10-09 --dry-run; echo "exit=$?"
node dist/journal-resplit-main.js --root "$SMOKE" --date 2026-10-09; echo "exit=$?"
shasum -a 256 "$SMOKE/events/2026-10-08/23.jsonl.gz"
find "$SMOKE" | sort
rm -rf "$SMOKE"
```

Expected:
- Both runs exit non-zero, and the message contains `has no record at or after 2026-10-09T00:00:00Z`.
- The hash is unchanged.
- `find` lists only `events/2026-10-08/23.jsonl.gz` and its directories: no `.recovery`, no `events/2026-10-09`, no `.compactor.lock`.

Optional, only if Docker is available locally (skip otherwise): `docker build -t polymarket-collector:resplit-check . && docker run --rm polymarket-collector:resplit-check node dist/journal-resplit-main.js` prints the usage error. This shows the image ships the entry point.

## 9. Done when

- The three new files exist, and `journal.ts` exports `gzipToFile`, `gunzipFile`, `sha256GunzipFiles` and `mapJournalError`, with rotation behaviour unchanged.
- The six tests in §5 pass, `journal.test.ts` passes unchanged, and `yarn check` is green.
- `yarn build` emits `dist/journal-resplit-main.js`, and the smoke in §8 behaves as described.
- The docs in §6 are updated. No new comments appear in code or tests.
- One commit on `main` in polymarket-collector (for example `feat: journal-resplit moves a lost hour 00 out of the previous hour 23`), not pushed.
