# STEP-010 plan: cleanup after the external review of STEP-001..006

Code repo: `/Users/dimabytes/work/polymarket/dota_2_bot/polymarket-collector`, branch `main`, base `6490b6b`.
Range the review covered: `34d3ae4..6490b6b`.

The source review is copied in full at the bottom. The orchestrator checked its claims against the code at `6490b6b`. The decisions below override the review where they differ.

## Hard constraints

- Behavior does not change. On-disk formats do not change: manifest JSON, journal files, reconcile reports, checkpoint files.
- Error stages and messages that tests or the contract name stay the same (for example `read.gunzip`, `rotation.verify`, `rotation.gz_exists`, `seed.missing`).
- Existing tests keep passing without weakening their assertions. You may move tests between files. You may rewrite a test only when the API it used is removed (for example `pendingGzips`), and the new test must check the same behavior.
- No file under `src/` ends above 1000 lines.
- New code has no comments. When you refactor a function, remove its old comments.
- `yarn check` exits 0 at the end. Run `yarn build` once too.
- Commit on `main` in small commits (one per numbered item below, or a few grouped). Do not push.

## Decisions per review item

1. **One line splitter. DO.** Note: the `concatBytes`/`indexOfNewline` copies in `journal.ts` and `replay.ts` predate this range; the third copy in `journal-resplit.ts` is new. The fix still applies.
   - Add one `journalLines(path)` stream in `src/journal.ts`: gunzip when the path ends in `.gz`, raw otherwise, then split on `\n`. An unterminated tail comes out as the last element without `\n`.
   - `recoverPartial`, `replay.ts` (its record reader) and `journal-resplit.ts` all use it. Delete both `concatBytes`/`indexOfNewline` copies, the second gunzip pipeline in `replay.ts`, and the per-line `Schema.fromJsonString(JournalRecord)` in `replay.ts`. `replay.ts` uses the module-level `decodeJournalLine`.
   - One UTF-8 decoder policy: `fatal: true` everywhere, including resplit.
   - Keep `replay.ts` strict-tail behavior and its error stages exactly.
2. **Manifest optionality. DO.** `missingHours` and `rowsOutsideDate` use `Schema.withDecodingDefaultKey` (exists in installed `effect` 4.0.0-beta.105, `node_modules/effect/src/Schema.ts:5864`). Defaults: `[]` and `{ book: 0, trades: 0 }`. Remove the `?? []` and `=== undefined` branches in `compaction.ts` and `telegram.ts`. Telegram prints the values directly. An old manifest without the keys must still decode; add or keep a test for that. `unmatchedRatio` in `src/schema/onchain-reconcile.ts` becomes `Schema.Finite`.
3. **`phase` only in `foldRecord`. DO.** `bookRowsOf` returns `{ rows, outside }` and does not mutate `fold`. The single `if (phase === "day")` counter update lives in `foldRecord`. Same for the trade counter path if it fits the same shape.
4. **`journal.ts` cleanup. DO, with these limits.**
   - Remove `fs2`/`path2` re-yields; use `fs`/`path` from the closure.
   - `rotateIfDue`: after logging "rotation backward ignored", return explicitly.
   - Move the stale-partial label-mismatch check into `warnIfLabelMismatch(stale, recovered)`.
   - Failed gzip: keep the current plan behavior (STEP-001). The failed head leaves the queue, its partial stays on disk, and startup recovery publishes it after a restart. Make that explicit with a test whose name states it. Do not add a retry.
   - Remove `pendingGzips` from the public `Journal` interface and the duplicate "journal gzip queue not empty" warning. Tests check files on disk instead.
   - File size: move the rotation tests into `src/journal-rotation.test.ts` if `src/journal.test.ts` would pass 1000 lines. Extracting `journal-rotation.ts` is your call: do it only if it deletes code or clearly shortens `openJournal`. Do not add a new `HourCursor` type unless it removes code.
5. **`onchain-reconcile.ts`. DO, and keep the d87388f semantics exactly.** Missing onchain-manifest directory = empty, no log. Any other directory-read error = WARN `onchain reconcile skipped` and skip that game. `exists` error other than NotFound = same WARN and skip that date; an existing report is never overwritten. One small skip-with-warning helper replaces the repeated `catch -> logWarning -> Option.none` blocks. Replace the magic `"NULL::VARCHAR AS trade_id"` argument: either skip the query when the file list is empty, or keep named constants next to the SQL. The three regression tests from d87388f must pass unchanged.
6. **Date arithmetic. DO.** Move `previousUtcDate`/`nextUtcDate` to `src/timestamps.ts`. `compaction.ts`, `replay.ts` and `journal-resplit.ts` use them. `hasArchiveDayBefore` filters `manifests/` entries through the same valid-`UtcDate` predicate that `candidateDates` in `compaction-scheduler.ts` uses (share it, do not copy it).
7. **`journal-resplit.ts`. DO.** Remove sentinel zeros from the scan type (use `Option` for "no tail yet"). Check `dryRun` once (`const run = args.dryRun ? operate : withCompactLock(operate)` or equivalent; the P1 fix from 6490b6b must stay: a real run inspects under the lock). Use the `Effect.fn(name)(gen, ...pipeables)` form in `publishResplit`. One hour constant shared with `journal.ts`. Computing the original sha during the scan is optional.
8. **Small items.**
   - `sweepOnchainWork`: `keepReason` becomes `Option<string>`. DO.
   - `errorMessage`: export one helper and use it in the files this range touched (`journal.ts`, `journal-resplit-main.ts`, `onchain-reconcile.ts`). Replacing the other copies in the repo is optional.
   - `pruneCatalogSnapshots` cost ceiling: do NOT add a code comment (project no-comments rule). Add one line to `docs/learnings.md` instead: prune reads every ledger file each pass, O(days); index by fingerprint if it gets slow.

## Checks

- `yarn vitest run` on the touched suites, then `yarn check`, then `yarn build`.
- `git diff 6490b6b..HEAD -- src | grep '^+' | grep -E '//|/\*'` prints nothing.
- `wc -l src/*.ts src/**/*.ts | sort -n | tail` shows nothing above 1000.

## Source review (verbatim)

12 коммитов, 42 файла, +2912/−482. Содержательно это пять фич: ротация журнала по времени записи, снятие decode-скана с ротации, seed/missing-hours в compaction, onchain coverage/reconcile, и утилита  journal-resplit . Поведенчески всё выглядит разумно и покрыто тестами. Структурно — есть два блокера и несколько сильных рекомендаций.

Блокеры

1. Теперь в репо три построчных байтовых сплиттера, два gunzip-пайплайна и декодер, который всё ещё собирается на каждую строку

  •  journal.ts  —  concatBytes  +  indexOfNewline  + ручной цикл в  recoverPartial  journal.ts:174-189
  •  replay.ts  — те же две функции скопированы 1:1, плюс свой  createGunzip  duplex, плюс  handleLine  зовёт  decodeRecord("JournalRecord", Schema.fromJsonString(JournalRecord), …)  на каждой строке replay.ts:205-286
  •  journal-resplit.ts  — третья, лучшая версия:  splitLines  через  Stream.mapAccum  +  Buffer.concat / indexOf  journal-resplit.ts:60-76

При этом PR сам экспортировал из  journal.ts  канонические  gunzipFile  и  decodeJournalLine  (собирается один раз на модуль) и записал в learnings «Build schema decoders once at module level, never per line» — но  replay.ts  на них не переведён. Это именно «bespoke helper where the codebase already has a canonical one», причём добавленный этим же диапазоном коммитов.

Code judo: один  journalLines(path): Stream<Uint8Array, JournalError, FileSystem>  в  journal.ts  (gunzip если  .gz , иначе raw; затем  splitLines ; незавершённый хвост выходит последним элементом без  \n ). Тогда:

  •  recoverPartial  =  runFoldEffect  по этому стриму (считает  keptBytes , ловит первую rejected-строку);
  •  forEachJournalRecord  в  replay.ts  =  runForEach  с  decodeJournalLine ,  strictTail  — проверка последнего элемента;
  •  resplit  уже так и написан.

Уходят  concatBytes ×2,  indexOfNewline ×2, второй gunzip-duplex, per-line decoder, два  TextDecoder  с разной fatal-семантикой ( journal.ts  —  fatal: true ,  journal-resplit.ts  — нет). Минус ~70 строк и минус три места, где можно по-разному сломать разбор хвоста.

2.  optionalKey  в манифесте размазал  ?? []  /  === undefined  по потребителям

 missingHours  и  rowsOutsideDate  объявлены  Schema.optionalKey  ради старых манифестов на диске manifest.ts:36-41. В результате:

  •  compaction.ts :  (diagnostics.missingHours ?? []).length  в  qualityOf  и ещё раз  manifest.diagnostics.missingHours ?? []  в  compactDate ;
  •  telegram.ts :  d.missingHours ?? []  и ветка  outside === undefined ? "none" : … .

Effect v4 даёт ровно для этого  Schema.withDecodingDefaultKey  (есть в  repos/effect/.../Schema.ts:5867 ). Старый манифест декодируется с  [] / {book:0,trades:0} , тип становится обязательным, все четыре fallback'а исчезают. Заодно диагностика жалуется на  unmatchedRatio: Schema.Number  в  schema/onchain-reconcile.ts:17  — должно быть  Schema.Finite .

Сильные рекомендации (не блокеры, но просьба сделать)

3.  replay.ts : параметр  phase  протащен в  bookRowsOf  только ради счётчика

 bookRowsOf(fold, snapshots, date, phase)  и  else if (phase === "day") fold.rowsOutsideDate.book += 1  в двух местах replay.ts:523-527.  foldRecord  уже знает  phase . Пусть  bookRowsOf  возвращает чистый  outside: number  рядом с  rows , а единственное  if (phase === "day")  живёт в  foldRecord . Никакого нового параметра, никакой мутации  fold  изнутри хелпера.

4.  journal.ts :  openJournal  вырос до ~300 строк и держит стейт-машину в замыканиях

Файл 803 строки — порог 1k не перейдён, но  journal.test.ts  уже 974; следующая фича его перевалит. Конкретно:

  • Блок проверки «label partial'а не совпадает с его записями» journal.ts:503-516 — edge-case диагностика посреди startup-цикла. Вынести в  warnIfLabelMismatch(stale, recovered) .
  •  stepHour  заново yield'ит  FileSystem / Path  как  fs2 / path2 , хотя  fs / path  уже в замыкании. Старый  append  так делал — новый код копирует.
  •  rotateIfDue : «rotation backward ignored» логирует и проваливается дальше в  advanceTo  (который no-op). Читается как забытый  return .
  •  drain :  Effect.ensuring(queue.slice(1))  снимает из очереди и упавший gzip. Он не будет повторён до рестарта; «journal gzip queue not empty» в цикле ловит только остаток очереди. Либо оставлять упавший элемент (retry через час), либо явно зафиксировать, что ошибка gzip = ждать рестарта. Сейчас инвариант неясен.
  •  pendingGzips  в публичном  Journal  существует только ради этого предупреждения и тестов. Ошибка gzip уже залогирована как «journal hour rotation failed» — второе предупреждение дублирует. Убрать из интерфейса; тесты могут смотреть на файлы.

Предлагаю вынести  stepHour / advanceTo / drain  +  pendingRef  в маленький модуль  journal-rotation.ts  с явным  HourCursor , а тесты ротации — в  journal-rotation.test.ts , пока оба файла не перевалили 1k.

5.  onchain-reconcile.ts :  reconcileDays  — четыре копии одного  catch → logWarning → Option.none

onchain-reconcile.ts:143-186

  •  fs.exists  обёрнут в  Option<boolean>  через  map(Option.some)/catch(...)  — рядом в  readOnchainManifest  та же задача решена одной строкой  orElseSucceed . Здесь  fs.exists(file).pipe(Effect.orElseSucceed(() => true))  + лог даёт ту же семантику «не смогли проверить — пропускаем».
  • Один хелпер  skipped = (what) => <A, E extends {message}>(eff) => eff.pipe(Effect.map(Option.some), Effect.catch(log(what) → Option.none))  убирает три из четырёх блоков.
  •  parquetSource(files, "NULL::VARCHAR AS trade_id")  — форма пустой таблицы передаётся магической строкой на каждый вызов. Если список пуст, проще либо не строить запрос вовсе, либо держать две константы рядом с SQL.

6. Арифметика дат протекла из  replay.ts  в  compaction.ts , и «предыдущий день» теперь считается тремя способами

  •  compaction.ts  импортирует  previousUtcDate  из  replay.ts , хотя владелец календарной арифметики —  timestamps.ts  ( utcDateOfUs ,  utcDayBoundsUs ).
  •  journal-resplit.ts  считает предыдущий день как  utcDateOfUs(day.startUs - 1) .
  •  hasArchiveDayBefore  в  compaction.ts  делает  entry.slice(0, 10)  по каталогу  manifests/ , где лежат и  onchain/ ,  reconcile/  — работает, но это неявное знание о форме имён;  candidateDates  в  compaction-scheduler.ts  уже делает «entries → валидные UtcDate до даты X».

Перенести  previousUtcDate / nextUtcDate  в  timestamps.ts , использовать их в resplit, и фильтровать  manifests/  через общий предикат.

7.  journal-resplit.ts  — мелочи, но их много для 220 строк

  •  Scan = Pick<ResplitReport, …>  +  emptyScan  с  tailFirstReceivedAtUs: 0  — сентинельные нули «хвоста ещё нет» живут в типе отчёта.
  •  args.dryRun  проверяется дважды: внутри  operate  и снаружи ради лока.  const run = args.dryRun ? operate : withCompactLock(operate)  и одна проверка.
  •  publishResplit :  Effect.fn  → генератор →  return yield* Effect.gen(...).pipe(onError) .  Effect.fn  принимает pipeable-хвост:  Effect.fn("publishResplit")(function*(){…}, Effect.onError(…)) .
  •  HOUR_US  здесь,  HOUR_MS  в  journal.ts .
  • Файл читается 4 раза (scan, head, tail, sha оригинала). Для one-shot допустимо, но scan уже прошёл весь файл — sha можно снять там же.

8. Мелочи

  •  onchain-app.ts   sweepOnchainWork :  keepReason: string | undefined  как tri-state. Либо  Option<string> , либо  boolean  + лог причины внутри.
  •  onchain-ledger.ts   pruneCatalogSnapshots  читает все  *.jsonl  ledger'а каждые 5 минут — O(дней). Пока дёшево; стоит  ponytail:  пометка про потолок.
  •  errorMessage(error)  ( instanceof Error ? … ) теперь написан инлайн в  onchain-reconcile.ts  и  journal-resplit-main.ts , при том что в  journal.ts  он есть. Один экспорт в  fs/atomic.ts  или  schema/decode.ts .

Что сделано хорошо

  •  matchForFill  в  onchain-decode.ts  — реальное удаление: убрана fallback-ветка, 20 строк → 8.
  • Снятие per-line decode-скана с живой ротации ( endsWithNewline  + sha round-trip) — правильное упрощение инварианта: «live доказывает байты, startup доказывает записи».
  •  ReceivedMarketEvent  —  receivedAtUs  стампится при  offer , а не в консьюмере; типизированный контракт между  stream.ts  и  pipeline.ts .
  •  decodeJournalLine  на уровне модуля;  gzipToFile / gunzipFile / sha256GunzipFiles  вынесены и переиспользованы в resplit.
  • Коммиты «remove narrating comments» — комментарии в новом коде действительно почти отсутствуют.

Вердикт

Не аппрув в текущем виде. Пункты 1 и 2 — это ровно «duplicates an existing helper / unnecessary optionality churn» из approval bar, и оба закрываются уменьшением кода, а не его перекладыванием. Пункты 3–6 — сильная просьба сделать в том же заходе, пока контекст свеж; 7–8 — по желанию.
