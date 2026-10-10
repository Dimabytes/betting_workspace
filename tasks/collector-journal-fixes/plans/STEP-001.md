# STEP-001 plan: journal rotation switches the hour forward only, on the record's own time

Task: `betting_workspace/tasks/collector-journal-fixes/feature.json`, step `STEP-001` (priority 1).
Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`. Commit there and do not push.
Figma or design links: none.

## 1. The bug in one paragraph

`journalRotationLoop` (`src/journal.ts`, at the end of the file) sleeps until the next UTC hour with `Effect.sleep`. On Linux the timer fires 1–2 ms before the boundary. `rotateIfDue(nowAfterSleep)` sees the same hour and does nothing. The next iteration reads a fresh `now`, which is already past the boundary, so the loop sleeps until the *following* hour. The whole hour goes into the previous file. At midnight, the records for hour 00 of day D land in `events/D-1/23.jsonl.gz`. `replayDate(D)` never reads that file, and `replayDate(D-1)` drops those rows as another day's rows. This lost 10 days of hour 00 in production. There are three more defects:
- `rotateIfDue` accepts a backward move. It returns early only when the hour is equal.
- `rotatePartialFile` renames `.gz.tmp` over an existing `.gz` without checking. A backward move therefore silently replaces an hour that was already published.
- The first loop iteration sleeps before it checks anything. A startup that crosses a boundary leaves the old hour open for one more hour.

## 2. Design

The research (FINAL_REPORT §6.1, p2-linux-timer design (b), p2-fix-spec-review P2-FIX-1..6) settled on this design. Follow it exactly.

1. **`append` decides the hour.** It already holds the semaphore permit. Before it writes, it runs `while hourIndex(record.receivedAtUs) > state.hourIndex: stepHour()`. Each step moves exactly one hour, so an hour with no records still gets its own (empty) file. The trigger is the record's own `receivedAtUs` with no grace window. A grace window would put post-midnight records back into `D-1/23`.
2. **The journal never moves backward.** A record stamped before the current hour goes into the current file (forward spill; replay already reads `D/00` as the neighbour of `D-1`). `rotateIfDue(now)` with a `now` behind the state is a no-op and logs a WARN.
3. **The gzip runs off the lock through a queue.** `stepHour` only syncs the old partial, moves the state, and pushes the old path onto `pendingRef: Ref<ReadonlyArray<string>>`. `drain` gzips the queued paths one by one through `rotatePartialFile`, outside the semaphore. `rotateIfDue` always runs `drain` after its own step, even when the step was a no-op. The rotation loop is the fiber that calls `rotateIfDue`; `app.ts` already forks it with `Effect.forkChild`, so the drain runs in a forked fiber. `append` never gzips and never calls `rotateIfDue`: the semaphore is not reentrant, so that call would deadlock.
4. **`rotatePartialFile` never overwrites a `.gz`.** If `HH.jsonl.gz` already exists:
   - The partial exists and `sha256(partial) == sha256(gunzip(gz))`: this is an interrupted rotation (rename done, partial not yet removed). Remove the partial and return. Nothing is overwritten.
   - The partial does not exist: there is nothing to publish. Return.
   - The bytes differ: fail with `JournalError("rotation.gz_exists: <gzPath>")`. Keep both files.
5. **The loop rotates and sleeps from the same clock read.** Each iteration does: `now` → `rotateIfDue(now)` (forward step to the clock hour, then drain) → WARN if the queue is still non-empty → sleep until `nextUtcHourBoundary(now)`, measured from a fresh read and clamped at 0. An early wake therefore re-sleeps about 1 ms to the same boundary and cannot skip it. The first iteration rotates immediately at startup.
6. **Startup label check.** When `openJournal` sweeps a stale `.partial`, it compares the file's label `D/HH` with the hour of its first and last record. On a mismatch it logs a WARN.
7. **Logs.** Each step writes one line, `journal hour rotated` `{from, to, trigger: "loop"|"append", wakeLagMs}`. The level is INFO for `trigger=loop` and WARN for `trigger=append`, so one line covers both the per-rotation INFO and the append-switch WARN. Other WARNs: `rotation backward ignored {state, requested}`, `journal gzip queue not empty after rotation {pending}`, and `stale partial label differs from its records {path, label, firstHour, lastHour}`. Do not add a new `collector_event` type and do not touch `schemaVersion`.

## 3. Scope

In scope: `src/journal.ts`, `src/journal.test.ts`, contract §5.1 (`docs/polymarket_dota_archive_contracts.md`), and `docs/learnings.md`.

Out of scope. These belong to later steps, so do not do them here:
- Removing `scanJournalPath` from live rotation, hoisting the decoder, stamping `receivedAtUs` at `offer`, and sending a stale partial through `recoverPartial` (all STEP-002). `rotatePartialFile` keeps its scan in this step.
- The hour-coverage check in compaction (STEP-003).
- Any change to `app.ts`, `pipeline.ts`, `stream.ts`, `replay.ts`, or the compaction code.
- Do not add DaemonState fields.

Code rules: no new comments in code. Delete the comment in `append` that says rotation "replaces it from this same partial (crash-window self-healing, see rotatePartialFile)", because it is now false. Do not use `new Date`, `Date.parse`, `Date.now`, `setTimeout`, or `new Promise`: the project's Effect diagnostics flag them (`globalDate`, `globalTimers`, `newPromise`). Format hours with the existing `utcDateHour`.

## 4. `src/journal.ts` changes

### 4.1 Module-level helpers (put them next to `nextUtcHourBoundary`)

```ts
const hourIndexOf = (ms: number): number => Math.floor(ms / HOUR_MS)

const hourLabel = (ms: number): string =>
  Option.match(utcDateHour(Math.floor(ms)), {
    onNone: () => `invalid:${ms}`,
    onSome: ({ date, hour }) => `${date}/${hour}`
  })
```

### 4.2 `JournalScan` / `scanJournalPath`

Add `readonly firstRecord: Option.Option<JournalRecord>` to `JournalScan`. In `scanJournalPath`:
- Add `firstRecord: Option.none()` to the early return for a missing file.
- Add `let firstRecord = Option.none<JournalRecord>()`. Set it once, on the first decoded record.
- Return it.

`recoverPartial` returns `scan` unchanged, and the extra field is harmless there.

### 4.3 `rotatePartialFile`

Change the return type from `Option.Option<JournalRecord>` to `JournalScan` (`return scan` instead of `scan.lastRecord`). New order of operations:

1. `mkdir` of the directory, `exists(partial)`, and `syncFile` when it exists. These are unchanged.
2. `scanJournalPath(partialPath, "rotation")` and the unterminated-tail failure. Both are unchanged.
3. Compute `sourceHash` here, moved up from below: `exists ? sha256File(partialPath) : the empty-input hash`. The expression is the same as today.
4. **New guard.** Run `fs.exists(gzPath)` (map errors to stage `rotation.exists`). If it exists:
   ```ts
   if (exists) {
     const published = yield* sha256GunzipFile(gzPath)
     if (published.sha256 !== sourceHash.sha256 || published.bytes !== sourceHash.bytes) {
       return yield* new JournalError({ message: `rotation.gz_exists: ${gzPath}` })
     }
     yield* fs.remove(partialPath, { force: true }).pipe(Effect.mapError(mapJournalError("rotation.removePartial")))
   }
   return scan
   ```
5. Otherwise keep the existing steps: gzip to `.gz.tmp`, `sha256GunzipFile(gzTmpPath)` compared with `sourceHash`, `rename(tmp, gz)`, remove the partial, then `return scan`.

From here on the code never renames over an existing `.gz`.

### 4.4 `Journal` interface

Add `readonly pendingGzips: Effect.Effect<number>`. Make it an Effect-valued member, not a zero-argument function (see the learning "Zero-arg functions returning lazy Effects trip the lazyEffect diagnostic"). Leave every other member unchanged. Nothing outside `journal.ts` builds a `Journal` object (checked with grep), so no fake needs an update.

### 4.5 `JournalState`

Replace `date: string` and `hour: string` with `hourIndex: number`. Keep `partialPath`, `epoch`, `nextSequence`, `lastSyncMs`, and `lastSequence`. Only the old `rotateIfDue` read `date` and `hour`.

### 4.6 `openJournal`

- **Stale sweep loop.** The current code has `const tail = yield* rotatePartialFile(stale)`. Replace it with:
  ```ts
  const scan = yield* rotatePartialFile(stale)
  const label = `${path.basename(path.dirname(stale))}/${path.basename(stale).slice(0, -PARTIAL_SUFFIX.length)}`
  const recordHours = [scan.firstRecord, scan.lastRecord].flatMap((record) =>
    Option.isSome(record) ? [hourLabel(record.value.receivedAtUs / 1_000)] : []
  )
  if (recordHours.some((hour) => hour !== label)) {
    yield* Effect.logWarning("stale partial label differs from its records").pipe(
      Effect.annotateLogs({ path: stale, label, firstHour: recordHours[0], lastHour: recordHours[1] })
    )
  }
  if (Option.isSome(scan.lastRecord)) staleTail = scan.lastRecord
  ```
- **State init.** Use `hourIndex: hourIndexOf(nowMs)` instead of `date` and `hour`. The `currentPartial` computation stays as it is.
- **Queue.** Add `const pendingRef = yield* Ref.make<ReadonlyArray<string>>([])` after `stateRef`.
- **Closures.** Define these after `semaphore`. They close over `layout`, `stateRef`, and `pendingRef`:

```ts
const stepHour = (trigger: "loop" | "append", triggerMs: number) =>
  Effect.uninterruptible(
    Effect.gen(function* () {
      const fs2 = yield* FileSystem.FileSystem
      const path2 = yield* Path.Path
      const state = yield* Ref.get(stateRef)
      const toIndex = state.hourIndex + 1
      const next = utcDateHour(toIndex * HOUR_MS)
      if (Option.isNone(next)) {
        return yield* new JournalError({ message: `invalid journal hour: ${toIndex}` })
      }
      const exists = yield* fs2.exists(state.partialPath).pipe(Effect.mapError(mapJournalError("rotation.exists")))
      if (exists) {
        yield* syncFile(state.partialPath).pipe(Effect.mapError(mapJournalError("rotation.sync")))
      }
      const nextDirectory = path2.join(layout.events, next.value.date)
      yield* fs2.makeDirectory(nextDirectory, { recursive: true }).pipe(Effect.mapError(mapJournalError("rotation.mkdir")))
      yield* Ref.update(stateRef, (current: JournalState) => ({
        ...current,
        hourIndex: toIndex,
        partialPath: path2.join(nextDirectory, next.value.hour + PARTIAL_SUFFIX),
        lastSyncMs: triggerMs - SYNC_INTERVAL_MS
      }))
      yield* Ref.update(pendingRef, (queue) => [...queue, state.partialPath])
      const log = trigger === "append" ? Effect.logWarning : Effect.logInfo
      yield* log("journal hour rotated").pipe(
        Effect.annotateLogs({
          from: hourLabel(state.hourIndex * HOUR_MS),
          to: hourLabel(toIndex * HOUR_MS),
          trigger,
          wakeLagMs: triggerMs - toIndex * HOUR_MS
        })
      )
    })
  )

const advanceTo = (targetIndex: number, trigger: "loop" | "append", triggerMs: number) =>
  Effect.gen(function* () {
    while ((yield* Ref.get(stateRef)).hourIndex < targetIndex) {
      yield* stepHour(trigger, triggerMs)
    }
  })

const drain = Effect.gen(function* () {
  for (;;) {
    const next = (yield* Ref.get(pendingRef))[0]
    if (next === undefined) return
    yield* rotatePartialFile(next).pipe(
      Effect.ensuring(Ref.update(pendingRef, (queue) => queue.slice(1)))
    )
  }
})
```

Notes on these closures:
- `stepHour` does not create the next partial. It is created by the next append. A queued path that never got a record makes `rotatePartialFile` write an empty `.gz`, because the `sourceExists=false` branch already does that.
- The `stepHour` order is state update, then enqueue, inside `uninterruptible`. A partially applied step can therefore never queue the live file.
- `drain` removes the head after success *and* after failure (`ensuring`). A failure propagates and the rest of the queue waits for the next loop iteration. A failed partial stays on disk, so no data is lost.

**`append` changes.** The order matters. Read the state for `epoch`/`nextSequence` and build the candidate as today. Decode and serialize as today, so a bad record fails before any hour move. Then:
```ts
const receivedAtMs = Math.floor(receivedAtUs / 1_000)
yield* advanceTo(hourIndexOf(receivedAtMs), "append", receivedAtMs)
const state = yield* Ref.get(stateRef)
```
**Re-read the state after `advanceTo`.** The mkdir, the open/write, and the `lastSyncMs` check must use the *new* `state.partialPath` and `state.lastSyncMs`. The rest of `append` (reserve sequence, write, sync pacing, `lastSequence`) stays the same. Delete the comment block about "a sibling `.jsonl.gz` is never written to — rotation replaces it from this same partial".

**`rotateIfDue` new body.** The interface signature is unchanged:
```ts
rotateIfDue: (rotateNowMs) =>
  Effect.gen(function* () {
    yield* semaphore.withPermits(1)(
      Effect.gen(function* () {
        const state = yield* Ref.get(stateRef)
        const requested = hourIndexOf(rotateNowMs)
        if (requested < state.hourIndex) {
          yield* Effect.logWarning("rotation backward ignored").pipe(
            Effect.annotateLogs({ state: hourLabel(state.hourIndex * HOUR_MS), requested: hourLabel(rotateNowMs) })
          )
        }
        yield* advanceTo(requested, "loop", rotateNowMs)
      })
    )
    yield* drain
  }),
```
The old `invalid clock time` check inside `rotateIfDue` goes away. `stepHour` keeps an Option guard.

**Add** `pendingGzips: Ref.get(pendingRef).pipe(Effect.map((queue) => queue.length))` to the journal object.

`syncNow`/`shutdown` stay unchanged. Shutdown does not drain; the next startup sweep gzips any partial left behind. The `hour_recovered` / `unclean_restart_detected` appends at the end of `openJournal` stay unchanged. They now step the hour themselves if the stale sweep took long enough to cross a boundary, and that is correct.

### 4.7 `journalRotationLoop`

```ts
export const journalRotationLoop = Effect.fn("journalRotationLoop")(
  function* (journal: Journal): Effect.fn.Return<never, never, FileSystem.FileSystem | Path.Path> {
    return yield* Effect.forever(
      Effect.gen(function* () {
        const clock = yield* Clock.Clock
        const now = yield* clock.currentTimeMillis
        yield* journal.rotateIfDue(now).pipe(
          Effect.catchTag("JournalError", (error) =>
            Effect.logError("journal hour rotation failed").pipe(Effect.annotateLogs({ message: error.message }))
          )
        )
        const pending = yield* journal.pendingGzips
        if (pending > 0) {
          yield* Effect.logWarning("journal gzip queue not empty after rotation").pipe(Effect.annotateLogs({ pending }))
        }
        const afterMs = yield* clock.currentTimeMillis
        yield* Effect.sleep(Duration.millis(Math.max(0, nextUtcHourBoundary(now) - afterMs)))
      })
    )
  }
)
```
Why this cannot skip an hour:
- The rotation uses `now`, and the sleep target is the boundary after that same `now`. An early wake at `B-1ms` makes a no-op rotation and then a 1 ms sleep to `B`.
- If the clock crosses `B` during the iteration, the sleep clamps to 0 and the next iteration rotates.

## 5. Tests: `src/journal.test.ts`

Run: `yarn vitest run src/journal.test.ts`.

### 5.1 Existing tests to update

- **"self-heals the rename-before-remove crash window within the same hour".** Replace it with **"never replaces an existing gzip with different bytes"**:
  - Keep the same setup: a current-hour partial with one record, plus `gzipSync("stale-content\n")` at the matching `.gz`.
  - Run `openJournal`, then one `append`.
  - `const error = yield* Effect.flip(journal.rotateIfDue(nextUtcHourBoundary(now)))`. Expect `error.message` to contain `rotation.gz_exists`.
  - `gunzipSync(gz)` still equals `"stale-content\n"`.
  - The old partial still exists.
- **"uses the last stale partial…" and "reconciles stale partials…".** These need no change in assertions. Only the `rotatePartialFile` return shape changed internally.
- **All other existing tests** call `rotateIfDue(nextUtcHourBoundary(now))` and then expect the `.gz` immediately. They keep passing, because `rotateIfDue` drains. "keeps a corrupt partial…" still gets `rotation.decode` from the drain.

### 5.2 Shared helpers to add in `journal.test.ts` (test-only, no comments)

New imports:
- `Duration`, `Fiber` from `effect`
- `journalRotationLoop` from `./journal.ts`
- `captureLogs` from `./logger.testing.ts`
- `bookPayload` from `./pipeline.testing.ts`
- `collectReplayDate`, `firstFixtureMarket`, `targetStartMs`, `targetStartUs`, `date as targetDate`, `previousDate` from `./replay.testing.ts`. Do **not** import its `withArchiveRoot`, because the local one has the same name.
- `openCheckpointStore` from `./checkpoint.ts`

Fixed instants: `const HOUR = 3_600_000`, `const MINUTE = 60_000`, `const h19 = targetStartMs - 5 * HOUR` (2026-08-07T19:00Z). Then `h20 = h19 + HOUR`, `h21`, `h22`, and so on.

Fake clock. The loop needs separate control of `sleep` and `now`. `TestClock` cannot express an early wake: it advances time to the deadline *before* it wakes the sleeper.
```ts
const makeFakeClock = (startMs: number) => {
  const sleeps: Array<{ readonly target: number; readonly resume: (effect: Effect.Effect<void>) => void }> = []
  const time = { wall: startMs }
  const nanos = () => BigInt(time.wall) * 1_000_000n
  const clock: Clock.Clock = {
    currentTimeMillisUnsafe: () => time.wall,
    currentTimeMillis: Effect.sync(() => time.wall),
    currentTimeNanosUnsafe: nanos,
    currentTimeNanos: Effect.sync(nanos),
    monotonicTimeNanosUnsafe: nanos,
    monotonicTimeNanos: Effect.sync(nanos),
    sleep: (duration) =>
      Effect.callback<void>((resume) => {
        sleeps.push({ target: time.wall + Duration.toMillis(duration), resume })
      })
  }
  const release = () => { sleeps.shift()?.resume(Effect.void) }
  return { clock, time, sleeps, release }
}
```
Use it through a wrapper. The wrapper captures the *live* clock before it provides the fake one, so the wait helper never calls global timers:
```ts
const withFakeClock = <A, E, R>(
  startMs: number,
  body: (fake: ReturnType<typeof makeFakeClock>, parked: Effect.Effect<void>) => Effect.Effect<A, E, R>
) =>
  Effect.gen(function* () {
    const live = yield* Clock.Clock
    const fake = makeFakeClock(startMs)
    const parked = Effect.gen(function* () {
      for (let attempt = 0; attempt < 400; attempt += 1) {
        if (fake.sleeps.length === 1) return
        yield* live.sleep(Duration.millis(5))
      }
      return yield* Effect.die("rotation loop did not park")
    })
    return yield* body(fake, parked).pipe(Effect.provideService(Clock.Clock, fake.clock))
  })
```
Tests that use it are `it.live(...)`. The pattern looks like this:

```ts
it.live("…", () =>
  withFakeClock(h19 + 50 * MINUTE, (fake, parked) =>
    Effect.gen(function* () { … })
  ).pipe(Effect.provide(nodeServices)))
```
For log assertions, use `Effect.provide(Layer.mergeAll(nodeServices, capture.layer))` with `const capture = captureLogs()` defined before the test effect. Lines are logfmt: assert with `capture.messages.some((line) => line.includes("…"))`.

Small helpers:
- `appendAt(journal, fake, ms)`: set `fake.time.wall = ms`, then `journal.append("collector_event", { type: "collector_started", details: {} })`. The default `receivedAtUs` is the clock.
- `hourPath(root, ms, suffix)`: `${(yield* layoutFor(root)).events}/${date}/${hour}${suffix}`, using `Option.getOrThrow(utcDateHour(ms))`.
- `recordTimes(filePath)`: read the bytes, apply `gunzipSync` when the name ends with `GZ_SUFFIX`, decode with `TextDecoder`, run `decodeJournalLines`, and map to `receivedAtUs`.
- `partialsUnder(root)`: `fs.readDirectory(events, { recursive: true })`, filtered to names ending with `PARTIAL_SUFFIX`.

Common pattern in every loop test:
1. `const root = yield* withArchiveRoot()`.
2. `const journal = yield* openJournal(root)`.
3. `const loop = yield* Effect.forkChild(journalRotationLoop(journal))`, then `yield* parked`.
4. Drive the clock: set `fake.time.wall`, call `fake.release()`, then `yield* parked`.
5. End every test with `yield* Fiber.interrupt(loop)`, before the temp dir is removed.

Change `fake.time.wall` only while the loop is parked.

### 5.3 New tests (exact expectations)

1. **"rotates at the boundary after a 1 ms early wake instead of skipping the hour"**
   - Start at `h19+50m`. After `parked`, expect `fake.sleeps[0].target === h20`.
   - `appendAt(h19+55m)`.
   - `wall = h20 - 1`, `release`, `parked`. Expect `fake.sleeps[0].target === h20`: it re-slept to the same boundary. `journal.currentPath` is still the 19 partial, and `19.jsonl.gz` does not exist.
   - `wall = h20`, `release`, `parked`. Expect target `h21`.
   - `appendAt(h20+10m)`.
   - Expect: `recordTimes(19.gz) == [(h19+55m)*1000]`; `recordTimes(20.partial) == [(h20+10m)*1000]`; `partialsUnder(root) == [20.partial]`.
2. **"a record stamped after the boundary switches the file before the loop wakes"** (uses `captureLogs`)
   - Start at `h19+50m`, `parked`, `appendAt(h19+55m)`.
   - `wall = h20 + 2`. Append with explicit `{ receivedAtUs: (h20 + 2) * 1000 }`.
   - Expect: the current path is the 20 partial; `journal.pendingGzips === 1`; the 19 partial still exists; a log line contains both `level=WARN` and `trigger=append`.
   - Append with `{ receivedAtUs: (h20 - 1) * 1000 }`. This is an in-flight record from hour 19, and it must go into the 20 partial (forward spill).
   - `wall = h20 + 3`, `release`, `parked`.
   - Expect: `19.gz == [h19+55m]`; `20.partial == [(h20+2)*1000, (h20-1)*1000]`; `pendingGzips === 0`; next target `h21`; the only partial is the 20 partial.
3. **"late wakes of 0 to 5 ms rotate exactly once"**
   - `for (const lateMs of [0, 1, 2, 3, 4, 5])`, each run with its own root and fake clock: start at `h19+50m`, `parked`, `appendAt(h19+55m)`, `wall = h20 + lateMs`, `release`, `parked`, `appendAt(h20+10m)`.
   - Expect: `19.gz == [h19+55m]`, `20.partial == [h20+10m]`, target `h21`.
   - Wrap each iteration in `withFakeClock` or create a fresh fake per iteration.
4. **"a 3 hour pause publishes one gzip per hour, empty for hours without records"**
   - Start at `h19+50m`, `parked`, `appendAt(h19+55m)`.
   - `wall = h22 + 40m`, `release`, `parked`. Expect target `h23`.
   - `appendAt(h22+41m)`.
   - Expect: `19.gz == [h19+55m]`; `20.gz` and `21.gz` exist and `gunzipSync(...).byteLength === 0`; `22.partial == [h22+41m]`; the only partial is the 22 partial; `pendingGzips === 0`.
5. **"a backward clock step never reopens or replaces a published hour"** (uses `captureLogs`)
   - Start at `h19+50m`, `parked`, `appendAt(h19+55m)`.
   - `wall = h20`, `release`, `parked`. Read `before = fs.readFile(19.gz)`.
   - `appendAt(h20+5m)`, then `appendAt(h19+40m)`: the clock stepped back.
   - Call `yield* journal.rotateIfDue(h19 + 40 * MINUTE)` directly; the loop is parked.
   - `appendAt(h20+10m)`.
   - Expect:
     - the bytes of `19.gz` equal `before`;
     - no 19 partial exists;
     - `20.partial == [h20+5m, h19+40m, h20+10m]`;
     - the current path is still the 20 partial;
     - a log line contains `rotation backward ignored`.
   - Then `wall = h21`, `release`, `parked`. Expect `20.gz` to have those 3 records.
6. **"the first loop iteration rotates a journal opened just before the boundary"** (startup straddle)
   - Start at `h20 - 2_000`, then `openJournal`.
   - `fake.time.wall = h20 + 1_500`, fork the loop, `parked`.
   - Expect: the current path is the 20 partial; `19.gz` exists with 0 records; target `h21`.
   - `appendAt(h20 + 2_000)`. Expect `20.partial == [h20+2000]`, and it is the only partial.
7. **"a midnight early wake keeps hour 00 in the new day for replayDate"**
   - Start at `targetStartMs - 40m` (2026-08-07T23:20Z).
   - `const sidecar = yield* firstFixtureMarket(root)` and `const tokenId = sidecar.outcomes[0]!.tokenId`.
   - Open the journal, fork the loop, `parked`.
   - Book helper: `bookAt(ms)` sets `wall = ms`, then `journal.append("market_event", bookPayload(tokenId, sidecar.conditionId, { timestamp: String(ms) }), { receivedAtUs: ms * 1000 })`.
   - Sequence:
     - `bookAt(targetStartMs - 30m)`.
     - `wall = targetStartMs - 1`, `release`, `parked`. Expect target `targetStartMs`.
     - `bookAt(targetStartMs + 30m)`. The append switches to day `2026-08-08` hour 00.
     - `wall = targetStartMs + 30m + 1`, `release`, `parked`.
     - `wall = targetStartMs + HOUR`, `release`, `parked`.
     - `bookAt(targetStartMs + 90m)`.
     - `Fiber.interrupt(loop)`.
     - `yield* journal.rotateIfDue(targetStartMs + 24 * HOUR + 500)`, which closes every hour of 08-08.
   - Expect:
     - `events/2026-08-08/00.jsonl.gz` exists;
     - every `recordTimes(events/2026-08-07/23.jsonl.gz)` value is `< targetStartUs`;
     - no partial is left under `events/2026-08-07` or `events/2026-08-08`.
   - Then replay:
     - `const before = yield* collectReplayDate(root, previousDate, targetStartMs + 27 * HOUR)`. Expect `before.rows.book.length === 1`.
     - `if (Option.isSome(before.checkpoint)) yield* (yield* openCheckpointStore(root)).write(before.checkpoint.value)`.
     - `const day = yield* collectReplayDate(root, targetDate, targetStartMs + 27 * HOUR)`. Expect `day.rows.book.length === 2`. These are the 00:30 and 01:30 books; on HEAD this was 1.
8. **"never replaces an existing gzip with different bytes"**: the replacement of the old self-heal test (see 5.1).
9. **"finishes an interrupted rotation when the gzip already holds the partial bytes"**
   - Real clock, as in the existing stale tests. Take the previous hour from `currentPaths` and `utcDateHour(now - 3_600_000)`.
   - `writeLines(stalePath, [collectorRecord("ffffffff-ffff-4fff-8fff-ffffffffffff", 0)])`, then `const bytes = yield* fs.readFile(stalePath)`, then `writeFileSynced(gzPath, gzipSync(bytes))`.
   - Run `openJournal(root)`. It must succeed.
   - Expect: the stale partial is gone; the bytes of `gunzipSync(gz)` equal `bytes`; `journal.previousEpoch.epoch === "ffffffff-…"`.
10. **"warns when a stale partial's label differs from its records"** (uses `captureLogs`, real clock)
    - Previous-hour partial with `collectorRecord(…, 0)`. Its `receivedAtUs` is in 2024, so it does not match the label.
    - Partial two hours back with `{ ...collectorRecord(…, 0), receivedAtUs: (now - 2 * 3_600_000) * 1_000 }`. This one matches.
    - Run `openJournal`.
    - Expect exactly one captured line to contain `stale partial label differs from its records`, and that line contains the previous-hour partial path.

The case "an hour without records gives an empty `.gz`" is covered by tests 4 and 6.

## 6. Docs

### 6.1 Contract §5.1

File: `docs/polymarket_dota_archive_contracts.md`, the bullets under `### 5.1 Layout и durability`.

Replace the bullet "на границе часа: sync, close, gzip в `.gz.tmp`, проверка полного gunzip/JSONL, atomic rename, затем удаление `.partial`;" with:

```
- час записи — UTC-час её `receivedAtUs`; append переключает `.partial` только вперёд,
  по одному часу за шаг, пока час записи больше текущего; запись с `receivedAtUs`
  раньше текущего часа пишется в текущий файл (forward spill), назад journal не
  переключается;
- таймер на границе часа закрывает часы без записей тем же шагом вперёд; каждый
  UTC-час работы collector-а получает ровно один `HH.jsonl.gz`, пустой для часа без
  записей;
- закрытый `.partial`: sync, close, gzip в `.gz.tmp` вне append-лока, проверка полного
  gunzip/JSONL, atomic rename, затем удаление `.partial`;
- существующий `.gz` никогда не перезаписывается: если он хранит те же байты, что
  `.partial`, ротация только удаляет `.partial` (завершение прерванной ротации), иначе —
  ошибка `rotation.gz_exists`;
```

Keep "никогда не писать append в существующий `.gz`." STEP-002 changes the "проверка полного gunzip/JSONL" wording, so leave it here.

### 6.2 `docs/learnings.md`

Add a new entry at the top. Keep it to 8 lines or fewer, with no paths, commands or diffs:

```
### Journal hour follows the record, forward only
The hour switches inside append on the record's own receivedAtUs, one hour per step, never backward; a record stamped behind the current hour stays in the current file, which the midnight neighbour read already heals. The timer only closes idle hours and drains the gzip queue, and it rotates and sleeps from one clock read, so an early wake cannot skip a boundary. No grace window: any grace puts post-midnight records into the previous day's last file, which replay never reads. Rotation never replaces an existing gzip; matching bytes only finish an interrupted rotation. TestClock cannot test this: it moves time to the deadline before waking the sleeper, so an early wake is not expressible; use a custom Clock with separate sleep and now.
```

In the existing entry "Append-time existence guards can crash-loop a restart that should self-heal", replace its last sentence with:

"The structural invariant is that appends target only the current partial; rotation finishes that state by removing the partial when the existing `.gz` holds the same bytes, and fails rather than overwrite a different `.gz`. Recovery stays in startup/rotation, never in append."

## 7. Edge cases, concerns, tradeoffs

- **Other test suites.** Some suites move a `TestClock` across hours while the pipeline appends:
  - `checkpoint.test.ts` jumps +24 h and +48 h. Appends now step the journal 24–48 hours. Each step is cheap (exists + mkdir + log) and nothing drains there. These tests assert checkpoints, not journal files, so they still pass but log WARN lines.
  - `archive-acceptance.test.ts` calls `rotateIfDue` one day ahead. That now writes 23 empty `.gz` files for 2026-08-07 plus the real 06/23. Its later compaction of 2026-08-08 does not read 2026-08-07.
  - Run both suites explicitly (section 8).
- **WARN volume.** At about 1,000 records/s the first post-boundary record often beats the timer, so `trigger=append` WARNs will appear on a large share of boundaries. The feature asks for exactly that. If it proves noisy in production, downgrade it to INFO in a later change.
- **Backward clock step across a restart.** This is a known ceiling. If the process restarts while the wall clock is behind an hour that already has a `.gz`, then closing that hour later gives `rotation.gz_exists`. The loop logs the error, the partial stays on disk, and compaction skips that day. A further restart makes the `openJournal` sweep fail loudly. No data is lost or overwritten, but a human has to merge the two files. This needs a ≥1 h backward NTP step plus a restart, so it should be very rare.
- **Far-future `receivedAtUs`.** Only Clock-based stamps reach `append` (`pipeline.ts` and the default). A wildly wrong clock would step one empty hour per hour of skew. No cap was added; add one only if a non-Clock stamp source ever appears.
- **Drain failures.** A failed path is dropped from the queue but stays on disk; the rest waits for the next loop iteration, and the WARN shows the remaining count. That is the same "loud, never delete" stance as today.
- **Concurrency.** Only the rotation loop calls `rotateIfDue` in production, so a single drainer is guaranteed and no extra lock is needed. Tests call `rotateIfDue` only while the loop is parked or after it is interrupted.
- **Interrupt during gzip** (shutdown): `ensuring` drops the head, and the partial and `.gz.tmp` stay. The next startup sweep gzips the partial; the `.gz.tmp` is overwritten (existing test "overwrites stale gzip temp content during rotation").

## 8. Verification

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`:

1. `yarn vitest run src/journal.test.ts`: all tests pass, including the 10 new or rewritten ones.
2. `yarn vitest run src/archive-acceptance.test.ts src/checkpoint.test.ts src/pipeline.test.ts src/app.test.ts src/stream.test.ts src/disk.test.ts`: these suites use the journal through real code, and all must pass.
3. `yarn typecheck`, `yarn diagnostics` (fix any new warning from the new code, especially `globalDate`, `globalTimers`, `newPromise` and `lazyEffect`), and `yarn lint`.
4. `yarn check`: the full feedback loop must be green. `AGENTS.md` requires it.
5. `git diff --stat` touches only `src/journal.ts`, `src/journal.test.ts`, `docs/polymarket_dota_archive_contracts.md` and `docs/learnings.md`.
6. `git diff src/journal.ts` adds no new comments. The stale "rotation replaces it from this same partial" comment is gone, and there is no `fs.rename` to `gzPath` without the `fs.exists(gzPath)` guard before it.
7. Commit on `main` (do not push), for example: `fix: switch the journal hour on the record's own time, forward only`.

## 9. Done when

- `append` steps the hour by `receivedAtUs`, forward only, one hour per step, under the permit it already holds. Gzip runs only through `drain`.
- `rotateIfDue` refuses backward moves with a WARN and always drains.
- `rotatePartialFile` never overwrites an existing `.gz`: same bytes means it removes the partial, different bytes fail with `rotation.gz_exists`.
- The loop rotates first, then sleeps to the boundary of the same `now`.
- The stale-label WARN, the per-rotation log, and the non-empty-queue WARN are in place.
- The tests in section 5 pass, the contract §5.1 and the learnings are updated, and `yarn check` is green.
