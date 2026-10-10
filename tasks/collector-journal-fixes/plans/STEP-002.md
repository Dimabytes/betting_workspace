# STEP-002 plan: no decode scan in live rotation, `receivedAtUs` at offer, decoder built once, stale partial through `recoverPartial`

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-002` (priority 2).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Build on the current HEAD (`f252f96`, after STEP-001). Commit there. Do not push.
Figma or design links: none.

Before you start, read `polymarket-collector/AGENTS.md`, `docs/learnings.md` (the top entries), and contract §5.1–5.2 in `docs/polymarket_dota_archive_contracts.md`.

## 1. The problems

1. **Live rotation decodes every line again.** `rotatePartialFile` (`src/journal.ts:376`) starts with `scanJournalPath(partialPath, "rotation")` (`:179-255`). That scan splits each line, builds a new `TextDecoder`, and strictly decodes the whole `JournalRecord`. It is about 57 % of rotation cost and runs on the event loop, so the consumer slows to about 50 events/s for 20–50 s after each busy hour boundary. Every record was already strictly decoded before `append` wrote it, and the sha256 round trip (`sha256(partial) == sha256(gunzip(gz))`) already proves the gzip holds the same bytes. The scan adds no safety.
2. **The decoder is rebuilt on every line.** `decodeJournalLine` (`:152-160`) creates a new `Schema.fromJsonString(JournalRecord)` on every call. A new AST defeats the schema parser's memoization, so decoding takes 19.9 µs per line instead of 9.9 µs. The test helper `decodeJournalLines` (`src/journal.testing.ts`) has the same problem.
3. **`receivedAtUs` is stamped at dequeue.** `pipeline.ts:270` reads the `Clock` when `consumeOne` takes the event from the queue, so any consumer delay shows up as `local_timestamp_us` lag in Parquet. Contract §5.2 says the stamp is taken "при получении", that is, when the frame is received. The producer offers into the queue at `stream.ts:244`.
4. **A torn stale partial crash-loops the collector.** `openJournal` sends every stale `.partial` (any hour other than the current one) through `rotatePartialFile` (`:500-519`). Its scan fails with `rotation.verify: unterminated tail` on a torn tail, or with `rotation.decode` on an undecodable last line. The error stops the process, Docker restarts it, and it fails again in a loop. The same torn tail in the *current* hour partial is handled by `recoverPartial` (`:341-358`). Contract §5.1 says that on startup an unfinished byte tail is saved to a diagnostic and dropped.

## 2. Design (decided; follow it)

1. **Live rotation checks bytes, not records.** `rotatePartialFile` no longer scans or decodes anything. It keeps the existing sync, sha256 of the partial, gzip to `.gz.tmp`, sha256 of the gunzipped tmp, the existing-gz guard, the atomic rename, and removal of the partial. One new check runs before the gzip: the partial ends with `\n`. An empty or missing partial passes. If the check fails, the error is `JournalError("rotation.verify: unterminated tail in <path>")` and nothing is published. `rotatePartialFile` returns `void`.
2. **One module-level decoder.** `export const decodeJournalLine = decodeRecord("JournalRecord", Schema.fromJsonString(JournalRecord), { onExcessProperty: "error" })`, defined once in `journal.ts`. `recoverPartial` and `journal.testing.ts` both use it. Also hoist the fatal `TextDecoder` to module level. A fatal `TextDecoder.decode(bytes)` call without `{stream: true}` keeps no state between calls, so one instance is safe to reuse. Do not change `schema/decode.ts`. `decodeUnknownEffect` memoizes the parser per AST, so a hoisted schema is enough.
3. **`recoverPartial` is the only decode scan, and it tolerates exactly the last line.** It streams the file, decodes each complete newline-terminated line, and counts the bytes it keeps.
   - The first line that fails UTF-8 or schema decode is set aside as `rejected`.
   - If another complete line follows a rejected line, recovery fails with that line's own error (`recover.decode: …` or `recover.utf8: …`). The file stays untouched, as it does today for a corrupt record.
   - At the end, `dropped = rejected line (if any) + unterminated byte tail`. When `dropped` is not empty:
     1. Append `dropped` to the diagnostic file `<same dir>/<HH>.jsonl.dropped` (open flag `"a"`).
     2. fsync that file.
     3. Truncate the partial to the kept bytes.
     4. Log WARN `journal partial tail dropped` with `{path, droppedPath, droppedBytes, droppedLines}`.
   - The diagnostic is written before the truncate, so a crash between the two steps cannot lose the dropped bytes. A second recovery of the same hour appends to the same file and replaces nothing.
   - `droppedLines` is 1 when a complete line was rejected, otherwise 0.
   - `truncatedBytes` (returned as before, and reported in `hour_recovered`) is now `dropped.length`, so it includes a rejected last line.
4. **Diagnostic file location.** `events/<date>/<HH>.jsonl.dropped`, next to the partial. `export const DROPPED_SUFFIX = ".jsonl.dropped"` sits next to `PARTIAL_SUFFIX`/`GZ_SUFFIX`. Nothing else reads it: replay, compaction, and the scheduler filter strictly by `.jsonl.gz` / `.jsonl.partial` (checked in `replay.ts:150-158`, `compaction-scheduler.ts:131-137`, `journal.ts` `listPartials`). Do not add a new top-level archive directory, and do not add a new `collector_event` type.
5. **The stale sweep uses recovery first, then the live rotation path.** In `openJournal`, for each stale partial: `const recovered = yield* recoverPartial(stale)`, then `yield* rotatePartialFile(stale)`. The label check and `staleTail` from STEP-001 read `recovered.firstRecord` / `recovered.lastRecord`.
6. **Stamp at offer.** `stream.ts` exports:
   ```ts
   export interface ReceivedMarketEvent {
     readonly receivedAtUs: number
     readonly payload: MarketEventPayloadType
   }
   ```
   - The manager queue carries `ReceivedMarketEvent`.
   - `routeEvent` reads `receivedAtUsFromClock` immediately before `Queue.offer(events, { receivedAtUs, payload })`.
   - `pipeline.ts` consumes `ReceivedMarketEvent` and uses the carried `receivedAtUs` for the journal append, `lastEventDate`, `lastEventAtUs` and the reducers. It no longer reads the `Clock` per event. `openedAtUs` still uses `receivedAtUsFromClock`.

## 3. Scope

In scope:
- `src/journal.ts`, `src/journal.testing.ts`, `src/journal.test.ts`
- `src/stream.ts`, `src/pipeline.ts`, `src/pipeline.testing.ts`
- `src/stream.test.ts`, `src/pipeline.test.ts`, `src/checkpoint.test.ts`, `src/archive-acceptance.test.ts` (type follow-ups only)
- `docs/polymarket_dota_archive_contracts.md` (§5.1, §5.2, §15) and `docs/learnings.md`

Out of scope (do not do):
- `replay.ts` also rebuilds `Schema.fromJsonString(JournalRecord)` per line. Compaction performance is a feature non-goal, so leave it.
- Do not keep the partial's fd open between appends. Do not drop the per-append `makeDirectory`. Do not move work to `worker_threads`. Do not drain the queue on SIGTERM.
- Do not add a per-file `catchTag` around the stale sweep. A corrupt line *before* the last line must still stop startup.
- No compaction or scheduler changes (STEP-003). No `app.ts` changes; the types flow through unchanged.
- Do not touch `schemaVersion`, the `CollectorEventType` union, or `schema/decode.ts`.

Code rules:
- New code must not add comments.
- When you rewrite a function, delete its old inline comments and its now-stale JSDoc: `scanJournalPath`'s JSDoc goes with the function; replace `recoverPartial`'s JSDoc ("The file is left untouched when a complete record is corrupt…") with nothing; delete the `//` comments inside `routeEvent`'s `!offered` branch and inside `consumeOne`.
- Do not use `new Date`, `Date.now`, `Date.parse`, `setTimeout`, or `new Promise` in `src`. Effect diagnostics flag them.
- Keep `src/journal.test.ts` under 1,000 lines. It is 925 now. Merge the scenarios as described in §5.

## 4. Code changes

### 4.1 `src/journal.ts`

**Imports.**
- Add `Result` to the `effect` import.
- Remove `DecodeError` from the `./schema/decode.ts` import; only `decodeRecord` stays. Lint fails on unused imports.

**Constants.** Next to the suffixes:
```ts
export const DROPPED_SUFFIX = ".jsonl.dropped"
```

**Decoder (replace `decodeJournalLine` at `:152-160`).**
```ts
export const decodeJournalLine = decodeRecord(
  "JournalRecord",
  Schema.fromJsonString(JournalRecord),
  { onExcessProperty: "error" }
)

const utf8 = new TextDecoder("utf-8", { fatal: true })

const decodeLineBytes = (
  bytes: Uint8Array<ArrayBufferLike>
): Effect.Effect<JournalRecord, JournalError> =>
  Effect.try({
    try: () => utf8.decode(bytes),
    catch: (error) => new JournalError({ message: `recover.utf8: ${errorMessage(error)}` })
  }).pipe(
    Effect.flatMap((line) =>
      decodeJournalLine(line).pipe(Effect.mapError(mapJournalError("recover.decode")))
    )
  )
```

**Delete** `interface JournalScan` and `scanJournalPath` (`:179-255`) completely. Keep `concatBytes` and `indexOfNewline`.

**Replace `RecoveryResult` and `recoverPartial` (`:330-358`).**
```ts
interface RecoveryResult {
  readonly recordsRecovered: number
  readonly truncatedBytes: number
  readonly firstRecord: Option.Option<JournalRecord>
  readonly lastRecord: Option.Option<JournalRecord>
}

const recoverPartial = Effect.fn("recoverPartial")(
  function* (
    partialPath: string
  ): Effect.fn.Return<RecoveryResult, JournalError, FileSystem.FileSystem> {
    const fs = yield* FileSystem.FileSystem
    let pending: Uint8Array<ArrayBufferLike> = new Uint8Array(0)
    let keptBytes = 0
    let recordsRecovered = 0
    let firstRecord = Option.none<JournalRecord>()
    let lastRecord = Option.none<JournalRecord>()
    let rejected = Option.none<{
      readonly line: Uint8Array<ArrayBufferLike>
      readonly error: JournalError
    }>()

    yield* fs.stream(partialPath).pipe(
      Stream.runForEach((chunk) =>
        Effect.gen(function* () {
          pending = concatBytes(pending, chunk)
          for (;;) {
            const newline = indexOfNewline(pending)
            if (newline < 0) break
            if (Option.isSome(rejected)) return yield* rejected.value.error
            const line = pending.subarray(0, newline + 1)
            pending = pending.slice(newline + 1)
            const decoded = yield* Effect.result(decodeLineBytes(line.subarray(0, newline)))
            if (Result.isFailure(decoded)) {
              rejected = Option.some({ line, error: decoded.failure })
              continue
            }
            keptBytes += line.length
            recordsRecovered += 1
            if (Option.isNone(firstRecord)) firstRecord = Option.some(decoded.success)
            lastRecord = Option.some(decoded.success)
          }
        })
      ),
      Effect.mapError((error) =>
        Schema.is(JournalError)(error) ? error : mapJournalError("recover.read")(error)
      )
    )

    const dropped = Option.isSome(rejected)
      ? concatBytes(rejected.value.line, pending)
      : pending
    if (dropped.length > 0) {
      const droppedPath = partialPath.slice(0, -PARTIAL_SUFFIX.length) + DROPPED_SUFFIX
      yield* fs
        .writeFile(droppedPath, dropped, { flag: "a" })
        .pipe(Effect.mapError(mapJournalError("recover.dropped")))
      yield* syncFile(droppedPath).pipe(Effect.mapError(mapJournalError("recover.dropped")))
      yield* fs
        .truncate(partialPath, keptBytes)
        .pipe(Effect.mapError(mapJournalError("recover.truncate")))
      yield* Effect.logWarning("journal partial tail dropped").pipe(
        Effect.annotateLogs({
          path: partialPath,
          droppedPath,
          droppedBytes: dropped.length,
          droppedLines: Option.isSome(rejected) ? 1 : 0
        })
      )
    }
    return { recordsRecovered, truncatedBytes: dropped.length, firstRecord, lastRecord }
  }
)
```
Notes:
- `line` is a `subarray` of the old `pending` buffer. That is safe because `pending` is reassigned to a fresh `slice`, and `concatBytes` always allocates a new array. Nothing writes into the old buffer.
- The rejected-line check runs only after a *newline* is found. That is what makes "a corrupt line followed by another complete line" fatal, while the corrupt line plus an unterminated tail is dropped.
- The old `stat` call is no longer needed: truncate to `keptBytes`.
- If TS cannot narrow `rejected` inside the closure, keep it as `Option` and narrow locally with `Option.isSome(rejected)` right before each `.value` access, as shown. Do not switch to `!` assertions.

**New helper (above `rotatePartialFile`).**
```ts
const endsWithNewline = Effect.fn("endsWithNewline")(
  function* (
    filePath: string,
    size: number
  ): Effect.fn.Return<boolean, PlatformError.PlatformError, FileSystem.FileSystem> {
    if (size === 0) return true
    const fs = yield* FileSystem.FileSystem
    const last = yield* Effect.scoped(
      Effect.gen(function* () {
        const file = yield* fs.open(filePath, { flag: "r" })
        yield* file.seek(size - 1, "start")
        return yield* file.readAlloc(1)
      })
    )
    return Option.isSome(last) && last.value[0] === 0x0a
  }
)
```
(`File.seek(offset, "start")` and `File.readAlloc(n): Effect<Option<Uint8Array>>` exist in the vendored `repos/effect/packages/effect/src/FileSystem.ts:1042-1047`.)

**`rotatePartialFile` (`:376-454`).**
- Return type: `Effect.fn.Return<void, JournalError, FileSystem.FileSystem | Path.Path>`.
- Delete the `scanJournalPath(partialPath, "rotation")` call and its `truncatedBytes > 0` failure block.
- Right after `sourceHash` is computed, add:
  ```ts
  const terminated = yield* endsWithNewline(partialPath, sourceHash.bytes).pipe(
    Effect.mapError(mapJournalError("rotation.verify"))
  )
  if (!terminated) {
    return yield* new JournalError({
      message: `rotation.verify: unterminated tail in ${partialPath}`
    })
  }
  ```
  When the partial does not exist, `sourceHash.bytes` is 0, so the check passes without opening a file.
- In the `gzExists` branch, `return scan` becomes `return`. Delete the final `return scan`.
- Leave everything else unchanged: mkdir, sync, the gz_exists guard, gzip, the gunzip hash compare, rename, and remove.

**`openJournal` stale sweep (`:500-519`).**
```ts
for (const stale of partials.filter((partial) => partial !== currentPartial)) {
  const recovered = yield* recoverPartial(stale)
  yield* rotatePartialFile(stale)
  const label = …unchanged…
  const recordHours = [recovered.firstRecord, recovered.lastRecord].flatMap(…unchanged…)
  …unchanged warn…
  if (Option.isSome(recovered.lastRecord)) staleTail = recovered.lastRecord
}
```
The current-hour block (`:521-533`) does not change. It already calls `recoverPartial(currentPartial)` and reads `recordsRecovered`, `truncatedBytes` and `lastRecord`.

`drain` does not change; it ignores the return value of `rotatePartialFile`.

### 4.2 `src/journal.testing.ts`
Replace the body so it reuses the hoisted decoder:
```ts
import { Effect } from "effect"
import { decodeJournalLine } from "./journal.ts"
import type { DecodeError } from "./schema/decode.ts"
import type { JournalRecord } from "./schema/journal.ts"
```
In the loop, use `decoded.push(yield* decodeJournalLine(line))`. Keep the function name, signature and existing JSDoc. The helper is now strict about excess properties. Every file it reads is written by `append` or by `collectorRecord` fixtures, which carry exactly the envelope fields, so no test should change because of that.

### 4.3 `src/stream.ts`
- Add the `ReceivedMarketEvent` interface (§2.6) and export it, near `StreamCounters`.
- `MarketStreamManager.events: Queue.Dequeue<ReceivedMarketEvent>`.
- `const events = yield* Queue.unbounded<ReceivedMarketEvent>()` (`:164`).
- `routeEvent` (`:244-251`):
  ```ts
  const receivedAtUs = yield* receivedAtUsFromClock
  const offered = yield* Queue.offer(events, { receivedAtUs, payload })
  if (!offered) {
    yield* Effect.logWarning("market stream queue closed")
    return
  }
  yield* incrementCounter("offered")
  ```
  Delete the two-line `//` comment in that branch. `receivedAtUsFromClock` is already imported.

### 4.4 `src/pipeline.ts`
- `import type { ReceivedMarketEvent } from "./stream.ts"`.
- `openLivePipeline` parameter becomes `events: Queue.Dequeue<ReceivedMarketEvent, Cause.Done>`. If `MarketEventPayload` is still used elsewhere in the file (the `applyReducerEvent` generic, `consumeOne`'s switch), keep that import.
- `consumeOne`:
  ```ts
  const consumeOne = Effect.fn("livePipeline.consumeOne")(
    function* (
      { receivedAtUs, payload }: ReceivedMarketEvent
    ): Effect.fn.Return<void, JournalError, FileSystem.FileSystem | Path.Path | Crypto.Crypto> {
      yield* Effect.uninterruptible(
        journal.append("market_event", payload, { receivedAtUs })
      )
      yield* Ref.set(lastEventDate, utcDateOfUs(receivedAtUs))
      yield* Ref.set(lastEventAtUs, Option.some(receivedAtUs))
      switch (payload.type) { …unchanged… }
    }
  )
  ```
  Delete the two `//` comment blocks that were in `consumeOne`. `receivedAtUsFromClock` stays imported for `openedAtUs`. `run` does not change (`Stream.fromQueue(events).pipe(Stream.runForEach(consumeOne))`).
- Do not touch `app.ts`. `stream.events` already flows into `openLivePipeline`, and only the element type changes.

### 4.5 `src/pipeline.testing.ts`
- Queue type: `Queue.unbounded<ReceivedMarketEvent, Cause.Done>()`. `runEvents`'s `queue` parameter type becomes `Queue.Queue<ReceivedMarketEvent, Cause.Done>`, and its `events` stays `ReadonlyArray<MarketEventPayloadType>`.
- Add an exported helper that does what production does, stamping at offer:
  ```ts
  export const offerEvent = Effect.fn("pipelineOfferEvent")(function* (
    queue: Queue.Queue<ReceivedMarketEvent, Cause.Done>,
    payload: MarketEventPayloadType
  ) {
    yield* Queue.offer(queue, { receivedAtUs: yield* receivedAtUsFromClock, payload })
  })
  ```
- `runEvents` uses it: `for (const event of events) yield* offerEvent(queue, event)`.
- Imports: `type ReceivedMarketEvent` from `./stream.ts`, `receivedAtUsFromClock` from `./timestamps.ts`.

### 4.6 Test call sites that only need the new element type
- `src/pipeline.test.ts`: change `Queue.offer(queue, X)` / `Queue.offer(fixture.queue, X)` at `:167`, `:362-365`, `:456`, `:461-466` to `offerEvent(queue, X)` / `offerEvent(fixture.queue, X)`. Import `offerEvent` from `./pipeline.testing.ts`.
- `src/checkpoint.test.ts`: change `:312`, `:406` and `:408` to `offerEvent(queue, …)`, and import it. Under the TestClock, the midnight-race test (`:395-420`) now stamps its two books 08-08 12:00 and 08-09 12:00, which is deterministic. The expectation (no checkpoint) still holds.
- `src/archive-acceptance.test.ts`: change `:117` to `initialEvents.map((event) => event.payload.type)`, and `:167-170` to `reconnectBook.payload.type` / `reconnectBook.payload.bids`. The `Queue.offer(queue, event)` calls pass through and do not change.
- `src/stream.test.ts`: read `.payload` from each `Queue.take(manager.events)`:
  - `:143` `const event = (yield* Queue.take(manager.events)).payload`
  - `:256` `firstEvent`, `:264` `nextEvent`, and `:428` `afterReconnect`: same pattern
  - `:425-426`: `(yield* Queue.take(manager.events)).payload.type`
  - `:464`: `events.push((yield* Queue.take(manager.events)).payload)`
  - Then run `yarn typecheck` and fix any remaining spot it reports the same way.

## 5. Tests

### 5.1 `src/journal.test.ts`: update existing tests
Import `DROPPED_SUFFIX` from `./journal.ts`.

1. **`recovers complete records, truncates a byte tail, and records hour_recovered` (`:295`).** Keep every assertion. Add one: `expect(yield* fs.readFileString(partialPath.slice(0, -PARTIAL_SUFFIX.length) + DROPPED_SUFFIX)).toBe('{"torn":')`.
2. **`drops an invalid JSON tail but rejects a corrupt complete record` (`:332`).** Rename it to `drops an invalid JSON tail but rejects a corrupt record before the last line`.
   - The first half stays as it is.
   - In the second half, the partial must hold the corrupt line *followed by* a valid record: `bytes = encode(corrupt + "\n" + (yield* serializeCanonicalJson(record)) + "\n")`.
   - Expect a `JournalError` whose message contains `recover.decode`, and the partial bytes unchanged. That assertion already exists.
3. **Replace `keeps a corrupt partial and does not publish gzip` (`:392-406`)** with one test, `live rotation publishes bytes without decoding them and refuses a torn tail`:
   - Open the journal, then write `{"schemaVersion":2}\n` to `partialPath`. That line is a valid newline-terminated line that `decodeJournalLine` rejects.
   - `yield* journal.rotateIfDue(nextUtcHourBoundary(now))` succeeds. The `.gz` gunzips to exactly those bytes, and the partial is gone. **This is the "rotation runs without the decoder (counter = 0)" check.** If any strict decode were still on the rotation path, this rotation would fail. Do not add a production counter or a `vi.spyOn` of a module binding: ESM spies do not see internal references.
   - Then, in the same test, write `{"schemaVersion":2}` with no newline to the *new* current partial (`yield* journal.currentPath`). `Effect.flip(journal.rotateIfDue(nextUtcHourBoundary(now) + 3_600_000))` is a `JournalError` whose message contains `rotation.verify`. That partial's bytes are unchanged, and its `.gz` does not exist.

### 5.2 `src/journal.test.ts`: new test (one test, two stale partials)
`it.live("recovers stale partials with a torn tail or an undecodable last line into gzip and a dropped file", …)`. Use `captureLogs()` and provide `Layer.mergeAll(nodeServices, capture.layer)`, as in the existing label-warning test at `:899`.

Setup, relative to `const { layout, now } = yield* currentPaths(root)`:
- `tornPath` = the previous hour (`now - 3_600_000`) partial. `writeLines(tornPath, [collectorRecord(epochA, 0), collectorRecord(epochA, 1)], '{"torn":')`.
- `badPath` = the hour two back (`now - 2 * 3_600_000`) partial. `writeLines(badPath, [collectorRecord(epochB, 0), corrupt])`, where `corrupt = { ...collectorRecord(epochB, 1), schemaVersion: 2 }`. Compute `corruptLine = (yield* serializeCanonicalJson(corrupt)) + "\n"`.
- Use fixed UUIDv4-shaped epochs, like the other tests (for example `"12121212-1212-4121-8121-121212121212"` and `"13131313-1313-4131-8131-131313131313"`).

Act: `const journal = yield* openJournal(root)`. It must **not** fail.

Assert:
- Neither stale `.partial` exists. Both `.gz` files exist.
- `yield* decodeJournalLines(gunzipSync(yield* fs.readFile(tornGz)).toString())` has 2 records, and `badGz` decodes to 1 record (sequence 0).
- `fs.readFileString(tornBase + DROPPED_SUFFIX)` is `'{"torn":'`, and `fs.readFileString(badBase + DROPPED_SUFFIX)` is `corruptLine`.
- `capture.messages` has a line with `journal partial tail dropped` and `droppedBytes=8`, and another with `journal partial tail dropped` and `droppedLines=1`.
- `journal.previousEpoch.epoch` is `epochA`. The newest stale partial is processed last because `listPartials` sorts by path.

The fixture records use `receivedAtUs` from 2024, so the STEP-001 "label differs" WARN also fires. That is expected; filter by message text.

### 5.3 `src/stream.test.ts`: stamp at offer
Add a local helper:
```ts
const awaitOffered = (manager: MarketStreamManager, count: number) =>
  Effect.gen(function* () {
    for (let attempt = 0; attempt < 400; attempt += 1) {
      if ((yield* manager.currentState).counters.offered >= count) return
      yield* TestClock.withLive(Effect.sleep(Duration.millis(5)))
    }
    return yield* Effect.die(`market stream did not offer ${count} events`)
  })
```
Test: `it.effect("stamps receivedAtUs when a frame is offered, not when it is taken", …)`. Model it on the existing `it.effect` test at `:289`, and use `Effect.provide(nodeServices)`.
- `const t1 = 1_786_150_000_000`; `yield* TestClock.setTime(t1)`.
- Set up as in the test at `:395`: root, fake gamma, fake stream, `openForTest`, poll, first sidecar, `token`, `key`.
- `fakeStream.setEvents(key, [sdkBook(token, sidecar.conditionId)])`, then `yield* manager.reconcile(fakeStream.service.createClient(), poll)`, then `yield* awaitOffered(manager, 1)`.
- `yield* TestClock.setTime(t1 + 5_000)`, then `fakeStream.emit(key, sdkBook(token, sidecar.conditionId, { bids: [{ price: "0.2", size: "3" }] }))`, then `yield* awaitOffered(manager, 2)`.
- `yield* TestClock.setTime(t1 + 60_000)`. Only then take both events: `const first = yield* Queue.take(manager.events)` and `const second = …`.
- `expect([first.receivedAtUs, second.receivedAtUs]).toEqual([t1 * 1_000, (t1 + 5_000) * 1_000])`, and `expect(first.payload.type).toBe("book")`.

### 5.4 `src/pipeline.test.ts`: the consumer uses the carried stamp
`it.live("journals the receivedAtUs carried by each queued event, not the dequeue time", …)`:
- `openFixture(root)`; `market = \`0x${"c".repeat(64)}\``; `token = tokenId("13131313131313131313")`.
- `const first = fetchedAt()` and `const second = first + 1_000`. Both are far in the past relative to the live clock, so the forward-only journal keeps them in the current file.
- `Queue.offer(queue, { receivedAtUs: first, payload: bookPayload(token, market) })`, the same with `second`, then `Queue.end(queue)`, then `yield* pipeline.run`.
- `(yield* readMarketRecords(journal)).map((record) => record.receivedAtUs)` equals `[first, second]`, and `(yield* pipeline.currentState).lastEventAtUs` equals `Option.some(second)`.

## 6. Docs

### 6.1 Contract `docs/polymarket_dota_archive_contracts.md`
- §5.1 layout code block: add the line `events/<UTC YYYY-MM-DD>/<UTC HH>.jsonl.dropped`.
- §5.1, replace the bullet
  `- закрытый .partial: sync, close, gzip в .gz.tmp вне append-лока, проверка полного gunzip/JSONL, atomic rename, затем удаление .partial;`
  with:
  ```
  - закрытый `.partial`: sync, close, gzip в `.gz.tmp` вне append-лока, проверка: файл
    заканчивается `\n` и `sha256(.partial) == sha256(gunzip(.gz.tmp))`, atomic rename, затем
    удаление `.partial`; построчный декод на живой ротации не выполняется — каждая запись
    строго декодирована перед append;
  ```
- §5.1 "На startup" block, replace it with:
  ```
  На startup — для каждого `.partial`, текущего и любого прошлого часа:

  1. читать только complete newline-terminated records;
  2. незавершённый byte tail и недекодируемую последнюю строку дописать в
     `events/<UTC YYYY-MM-DD>/<UTC HH>.jsonl.dropped` и отбросить (WARN с числом байт и
     строк); недекодируемая строка не в конце файла — ошибка, файл не меняется;
  3. replay-ить валидные records;
  4. `.partial` текущего часа — продолжить append в него; `.partial` прошлого часа —
     gzip тем же путём, что и живая ротация.
  ```
- §5.2 invariant `receivedAtUs`: change it to
  `- \`receivedAtUs\` — Unix UTC microseconds локального wall clock при получении: штамп ставится, когда кадр кладётся в очередь live-потребителя, и переносится вместе с кадром; задержка потребителя его не меняет;`
- §15 table row: `| Повреждённый tail \`.partial\` (любого часа) | Сохранить полные строки; tail и недекодируемую последнюю строку — в \`.jsonl.dropped\`; stale \`.partial\` затем gzip |`

### 6.2 `docs/learnings.md`
Prepend this right under the `# polymarket-collector learnings` title, above "Journal hour follows the record, forward only". Keep it to 8 lines or fewer, with no paths or commands:
```
### Live rotation proves bytes; startup proves records
Every record is strictly decoded before append, so live rotation only checks the trailing newline and the sha256 round trip of partial against gunzip. The per-line decode scan there was about half of each rotation on the event loop and throttled the consumer for tens of seconds after busy boundaries. Decoding stays at startup only, for the current and every stale partial: a torn tail or an undecodable last line goes to a dropped file beside the hour, and a bad line before the end still stops startup. receivedAtUs is stamped when the stream puts a frame on the consumer queue, so a consumer stall cannot move local time. Build schema decoders once at module level, never per line.
```
Also edit the last sentence of the older entry "Journal rotation must not build one giant UTF-8 string". Change "…for recover and rotation verify." to "…during startup recovery." Rotation no longer decodes lines.

## 7. Edge cases, concerns, tradeoffs

- **A stale partial that is only torn bytes.** Everything is dropped, the partial is truncated to 0 bytes, and `rotatePartialFile` publishes an empty `.gz`. That empty file is legal and keeps hour coverage complete for STEP-003.
- **An interrupted rotation (the `.gz` holds the same bytes as the partial).** `recoverPartial` finds nothing to drop, and `rotatePartialFile` takes the gz-exists branch and removes the partial. The existing test `finishes an interrupted rotation…` must stay green.
- **A torn partial whose `.gz` already exists.** Truncation makes the bytes differ, so `rotation.gz_exists` fails startup. This cannot happen through normal operation, because a gz is only produced from a newline-terminated partial. Accept it; do not add handling.
- **A corrupt line in the middle of a stale partial.** Startup still fails, by design. That data may be real, and the feature tolerates only the last line. The remaining crash loop for this case is a known ceiling.
- **Ordering across producers.** Each market has its own producer fiber. A fiber could yield between the clock read and `Queue.offer`, which would put two stamps µs out of order in the journal. Journal order is `(processEpoch, sequence)`, rotation spills earlier stamps forward, and contract §9 clamps the effective time. Microsecond inversions are harmless. Do not add a lock.
- **`truncatedBytes` meaning.** In `journal.recovery` and the `hour_recovered` details it now includes a dropped undecodable last line. The field names and shape do not change. `observability.ts` and `app.ts` read it unchanged.
- **Memory.** `dropped` holds at most one line plus the tail. The quadratic `pending.slice` cost on a very long newline-free region is pre-existing and stays.
- **The current partial gets the same treatment.** A current-hour partial whose last complete line is undecodable is now recovered instead of failing. That matches contract §15.
- **Why there is no spy.** The behavioural test in §5.1 item 3 is the decoder-free proof. It fails if anyone puts a decode back into rotation. A spy on `JSON.parse` would be flaky, because vitest and logger internals share the process.

## 8. Verification

Run in `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`. No network, no VPS.

1. `yarn vitest run src/journal.test.ts`
2. `yarn vitest run src/stream.test.ts src/pipeline.test.ts src/checkpoint.test.ts src/archive-acceptance.test.ts src/app.test.ts src/replay.test.ts src/compaction-scheduler.test.ts src/disk.test.ts`
3. `yarn typecheck`
4. `yarn diagnostics` (Effect diagnostics; 0 errors and no new warnings in touched files)
5. `yarn lint`
6. `yarn check`: must exit 0 (it also runs as the pre-commit hook)
7. Self-checks:
   - `grep -n "scanJournalPath\|rotation.decode" src/journal.ts` prints nothing.
   - `grep -n "receivedAtUsFromClock" src/pipeline.ts` shows only the `openedAtUs` use.
   - `git diff -U0 -- src | grep '^+' | grep -E '//|/\*'` prints nothing (no new comments).
   - `wc -l src/journal.ts src/journal.test.ts`: both under 1,000.

## 9. Done when

- Live rotation has no decode or UTF-8 scan. A torn current partial fails with `rotation.verify` and is not published. An undecodable but newline-terminated partial is published byte-for-byte.
- `decodeJournalLine` is a single module-level decoder, used by `recoverPartial` and `journal.testing.ts`.
- A stale partial with a torn tail, or with an undecodable last line, produces a `.gz` plus a `.jsonl.dropped` file and a WARN. Startup continues.
- Queue items carry `receivedAtUs` stamped at offer, and the pipeline journals that stamp.
- Contract §5.1, §5.2 and §15 and `docs/learnings.md` are updated.
- `yarn check` is green, and there is one commit on `main` (suggested message: `fix: rotate without a decode scan, stamp frames at offer, recover torn stale partials`). Not pushed. `feature.json` `passes` is left for the orchestrator.
