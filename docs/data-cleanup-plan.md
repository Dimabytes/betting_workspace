# План чистки `data/` в esports-trader

## Статус

- Фаза A (код): в `main` — A1 `8a96ac6d` + `fecf0e53`, A2 `6eba7f42`, A3/A4 `4896643b`.
- Фаза B (Mac): локальная сторона, с VPS не проверить — см. у исполнителя фазы B.
- Фаза C (VPS): **выполнена 2026-09-22**. `compress`-сервис поднят и жуёт бэклог
  `trader_live`/`trader_paper` (dry-run: 795+11 кандидатов, 0 конфликтов; пробный
  пакет — gz побайтово равен, читатели работают). `data/live_paper` разово:
  7.7G → 1.4G (162 gz, `gzip -t` чисто, `session.jsonl` не тронут).
  `/var/lib/polymarket-onchain-state/import-staging/` удалён (4.2G; перед
  удалением сверено: 84 620 staged = 84 620 `copied` в bootstrap-report 1:1,
  сэмпл 30 файлов sha256 staging == archive == report). `import/` оставлен.
- Осталось: после первого `sync_trader.py` на Mac проверить, что `.gz`
  докачиваются и `.jsonl` дедупятся шагом A4.

Дата: 2026-09-21. Репо: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`.
`data/` = 289G. Цель после всех фаз: ~185–190G (пол — это `book_snapshot_full`, 159G платных сырых стаканов Telonex; его не трогаем).

## Прогресс

- **A1 — DONE (commit `8a96ac6d`, 2026-09-22).** `compact_quote_events` возвращает `QuoteCompaction` (строки, пропущенные parts, неучтённые файлы); `write_finished_summary` удаляет `quote_event_parts/` после гейта (summary записан, results/fills/quote_events по метаданным равны объединённым входам, все parts учтены); `merge_shard_run` удаляет `shard_*ofN` после гейта на завершённость писателей шардов. Fault-строки (`nautilus_zero_fill`) исключены из ожидаемых parts. Тесты: `tests/test_backtest_validation.py` 54/54, полный сьют 2357 passed.
- **Разовая проверка старых прогонов + B1 — DONE (commit `fecf0e53`, 2026-09-22).** `scripts/cleanup_backtest_intermediates.py` (parallel `--jobs`, прогресс/ETA, dry-run по умолчанию, `--apply` перепроверяет перед удалением, `--min-age-hours` настраивает гейт свежести). Сверка — пер-матчевый отпечаток (count, sum ts_ns) каждого part/fills против итогового parquet. Прогнано `--apply` дважды (второй раз `--min-age-hours 0` по подтверждению пользователя — свежие прогоны не инспектируются): **удалены интермедиаты у 676 прогонов, освобождено ~28.6 GiB; `data/backtests` 50G → 21G**. Осталось ~58 скипнутых: битые/незавершённые прогоны, 3 с `cmp_*.csv`, 2 корруптных parquet — трогать не стали. Логи: `/var/tmp/bti-real-dryrun2.log`, `/var/tmp/bti-real-apply.log`, `/var/tmp/bti-real-apply2.log`. Затем по решению пользователя **все 58 скипнутых диp удалены целиком** (включая `research-val882` с несмерженным реплеем и мёртвые чекпоинты `horn-start`/`e4-n3`/`oldmodel-horn`/`b0_s2`/`sb-f300`) + 9 опустевших родительских run-диp — ещё **3.33 GiB**. Итого по B1: **~32 GiB; `data/backtests` 50G → 18G**; оставшиеся parts/шарды лежат только под LIVE-таргетами активных прогонов.
- **A2 — DONE (commit `6eba7f42`, 2026-09-22).** `src/shared/utils/jsonl_io.py`: `resolve_jsonl`, `open_maybe_gz` + `sha256_maybe_gz` (хэш контента, не контейнера — иначе `schedule_fingerprint` менялся бы после gzip и матчи выпадали `schedule_stale`) и `decompressed_size` (stat / gzip ISIZE). Проверки существования/stat: `telonex_local` (гейт core_trace + `_archive_stamp`), `archive_read.iter_json_objects` (покрывает `game_state_replay` и journal-чтения `live_tape`), `live_tape._parse_core_trace`, `schedule.extract_schedule` (resolve, stat с читаемого пути, gzip-ошибки → `feed_corrupt`, `feed_file` = логическое имя, `feed_size` = размер контента), `index` (детект голых директорий + `_resolve_schedule` с content-size для кэша). Читатели: `grid_archive`, `oddin_archive`, `archive_paths.iter_archive_records` (steam `state.jsonl`), `strip_own_book` ×3, `replay_core_trace._drive`, `core_state_report` (stat на resolved-пути), `docs/experiments/order-outcomes/build.py`. `s04_fetch_grid_starts` НЕ читает `state.jsonl` (это кэш GRID `series_state/*.json`) — не тронут; `live_archives`/session-читатели не тронуты. Проверка на копиях: `extract_schedule` байт-в-байт идентичен plain/gz на oddin+grid архивах, steam читатели совпадают, `replay_trace`/`resting_events`/viewer-реплеи идентичны. Тесты: `tests/test_jsonl_io.py` 5/5, полный сьют 2363 passed.
- **A3/A4 — DONE (commit `4896643b`, 2026-09-22).** `src/trader/compress_archives.py`: whitelist имён (`grid_state`, `oddin_state`, `state`, `core_trace`; `session.jsonl` никогда), гейт «`execution_cleanup.json` + все whitelist-файлы старше 48ч» (fresh-файл откладывает всю диp; возраст plain, иначе gz), `FileIdentity` (dev/ino/size/mtime_ns/ctime_ns), tmp.gz → полное прочтение + побайтовое сравнение → `os.replace` → ре-проверка → удаление; published `.gz` никогда не перезаписывается, plain+gz пара → dedup только при равенстве контента и неизменности; `session.jsonl.gz` = конфликт-сигнал. Общий flock `.compress_archive.lock` на archive-root (`trader.process_lock`); dry-run лок не трёт. CLI: `--root` (repeatable), `--once`, `--dry-run`, `--no-marker` (только с --once/--dry-run — исторические корни), `--min-age-hours`, `--interval-seconds`; демон = hourly loop. `scripts/sync_trader.py`: flock на весь rsync+dedup, dedup после успешного rsync (`compress_new=False`), при ошибке rsync/--dry-run ничего не удаляет; теперь требует `PYTHONPATH=src` (как остальные скрипты с src-импортами). `compose.yaml`: сервис `compress` с `/app/data/trader_live` + `/app/data/trader_paper`. Проверки на копиях реальных архивов (grid 8974185539, live_paper 8948155771, detached 9007118998): dry-run без изменений, сжатие с идентичным контентом, повторный запуск no-op, re-pull → dedup, конфликт/битый gz → оба файла, stale tmp чистится, fresh-файл откладывает dir, session никогда, held-lock пропускает root, CLI rc. Тесты: `tests/test_compress_archives.py` 22 + `test_sync_trader.py` +3 (rsync-fail/dry-run не дедупят) + `test_trader_compose.py` обновлён под третий сервис, полный сьют 2388 passed.
- **Малый реальный пакет + B2–B8 — DONE (2026-09-22, на `main`, коммитов нет — только data/).** `data/` 277G → **211G**. Пакет: 3 match-dir из `data/trader` через symlink-root (`8974185539`, `grid-3010154-m2`, `9007208887`; 256M, полный алгоритм **3.0s ≈ 84 MiB/s**), snapshot читателей до/после (sha256 контента, rows, `extract_schedule`, `replay_trace`, `resting_events`, `clock_offset_ns`, viewer replay/tapes) — FAILURES 0, `session.jsonl`/маркер/match.json не тронуты. **B2**: `data/trader` `--once` — 788 файлов, −42.97GiB (53G→9.9G), fresh_dirs=65, no_marker_dirs=114 пропущены по гейтам (plain whitelist-файлов осталось 161 — только в пропущенных диp). **B3**: `--no-marker --once` — `live_paper` 162 файла −6.4GiB (7.6G→1.4G), `live_paper_live` 104 файла −7.1GiB (7.3G→0.4G); писателей нет, файлов <48ч нет. **B6-gzip**: `archive/2026-09-20_pgl_removal/trader_archives` — 11 `core_trace` −0.33GiB (576M→246M); `pgl_state.jsonl` вне whitelist — остался plain. **B4**: `docs/experiments/ensemble/{cache,cache_kcurve,cache_lol}` −1.73G. **B5**: `market_seconds/v*` кроме текущего `va75c29ad` −1.33G (5 версий). **B6-delete**: `archive/2026-07-28` 2.6G + `2026-08-06_pre_rebuild` 1.8G. **B7**: `lol/processed/datasets_{last,late,late_last,roles,horizons}` −2.65G (ссылки только в docs/experiments, продакшн-код их не читает). **B8**: `_logs/*.log` старше 48ч → gzip пофайлово, 1173 файла (dota 1.0G→226M, lol 1.7G→636M); 288 свежих логов активных прогонов не тронуты. **B9 пропущен по решению пользователя** — старые прогоны бэктестов не удаляем. Конфликтов/ошибок за все прогоны: 0; `.gz.tmp` и `session.jsonl.gz` не осталось; `gzip -t` спот-чеки чистые.
- Дальше по порядку: фаза C (VPS: доставить читатели+компрессор, dry-run, малый пакет, сервис `compress`, разовый gzip `live_paper` ~7.7G, удаление `import-staging` ~4.2G после перепроверки).

## Контекст, который нужен исполнителю

- `data/trader/<match>/` — архивы live-матчей, синкаются с VPS через `scripts/sync_trader.py` (rsync pull, никогда не удаляет). Используются для обучения/бэктестов — НЕ удалять, только gzip.
- Условие автоматического сжатия: есть `execution_cleanup.json` и все существующие файлы из белого списка A3 не менялись минимум 48 часов (по `mtime`). `session.jsonl` в расчёт возраста не входит. Без маркера директорию не трогать, кроме явно перечисленных ниже закрытых исторических корней.
- `execution_cleanup.json` подтверждает торговую очистку, но сейчас появляется до закрытия всех писателей. Согласованное упрощение: добавляем запас 48 часов, не вводим новый маркер и не меняем завершение матча в трейдере. Это практическая защита, а не строгая гарантия отсутствия открытого писателя. Текущий код не запускает завершённый матч повторно; перед удалением оригинала дополнительно проверяем его неизменность.
- В `data/backtests/{dota,lol}_maker/<run>/` каждый прогон содержит `seed*/shard_*ofN/` (per-shard чекпоинты) и `quote_event_parts/` (per-match куски ленты квот). После merge они продублированы в `results/fills/quote_events.parquet` на уровне прогона и нужны только для `--resume`. Пользователь никогда не resume'ит завершённые прогоны — всегда запускает новый.
- `data/backtests/*_ maker/_logs/<run>/seedN/shard_M.log` — stdout шардов (пишет `scripts/run_seeds.sh`). Нужны только во время прогона и сразу после для дебага. В авто-чистку НЕ включать.
- `data/new_processed/market_seconds/v<hash>/` — per-match кэш; версия = `sha256(cache-параметров)[:8]` из `src/market_data/build_market_data.py:CACHE_VERSION`. Код читает только текущий хэш-диp.
- Текущий активный dataset LoL: `data/lol/processed/datasets` (+ `map_builds`). Остальные `datasets_*` — старые генерации, ссылок в коде нет.
- `data/live_paper/` (до 30 авг) и `data/live_paper_live/` (до 2 сен) — закрытые эпохи paper-трейдинга. Ничем не читаются, контент никуда не скопирован.
- `data/archive/` — ручные снапшоты-карантины.
- gzip .jsonl даёт ~19x (замерено: 76M → 4M).
- `session.jsonl` НЕ сжимаем нигде. `src/trader/session_journal.py:403 append_late_fill` дописывает `late_fill` в закрытый архив. Зовут её `src/trader/wallet_host.py:748` и `scripts/backfill_trader_fills.py:427`. `ArchivedMarketIndex` (`src/trader/archived_markets.py:77`) не имеет окна по времени — late fill приходит в архив любой давности. Сжатие + дедуп удалили бы такую строку. Цена решения нулевая: все `session.jsonl` вместе = 0.2G из 63G (замер 2026-09-21). Ради сравнения: `grid_state.jsonl` = 49.8G, `core_trace.jsonl` = 9.3G, `oddin_state.jsonl` = 2.2G, `state.jsonl` = 1.8G.
- Порядок для gzip: A2 → проверки A2 → A3/A4 → проверки на копиях → небольшой реальный пакет → B2/B3 → C. Сжать архивы до патча читателей и проверки общего компрессора нельзя. A1/B1 идут независимо от gzip, но B1 только после проверки завершённости и сохранности прогонов.

## Фаза A — код в esports-trader (работаем на main)

### A1. Авто-удаление интермедиатов бэктеста после финализации

Проверено 2026-09-21: `merge_shard_run` зовёт `finalize_from_checkpoint` → `write_finished_summary` (`src/backtest/run.py:994`), а тот первым делом зовёт `compact_quote_events`. На диске 541 из 541 завершённых seed-директорий имеют `quote_events.parquet`. Само наличие файла не доказывает полноту: текущая компактация пропускает отсутствующие parts и выполняется до записи summary.

В `src/backtest/run.py`:
- в `merge_shard_run` после успешного `finalize_from_checkpoint` — `shutil.rmtree` каждого `shard_*ofN` в `report_dir` (список уже есть в `merge_shard_checkpoints`);
- в конце финализации прогона (после `compact_quote_events` и записи `summary.json`) — удалить `parts_dir(report_dir)` (`quote_event_parts/`).
- **Гейт на удаление parts и шардов:** успешная финализация и запись `summary.json`, все ожидаемые матчи учтены, итоговые `results.parquet`, `fills.parquet`, `quote_events.parquet` соответствуют объединённым входам, писатели шардов завершились. Проверять при merge/компактации, перед удалением; наличие или ненулевой размер Parquet сами по себе недостаточны.
- Не добавлять повторный бэктест или полный повторный проход по ленте: использовать уже загруженные results/fills, учитывать ожидаемые parts и записанные строки во время текущего merge, сверить число строк и схему по метаданным итоговых Parquet. Отсутствующий ожидаемый part или неучтённый part в удаляемом каталоге блокирует очистку. Корректный результат с нулём событий допустим; отсутствие parts само по себе не даёт разрешения удалять шарды.
- Краш до финализации оставляет шарды/parts на месте → `--resume` продолжает работать для незавершённых прогонов. Завершённый прогон resume'ить нельзя будет — ок, такой сценарий не используется.
- Вьюер ленты переживает удаление parts: `src/backtest/inspect/tape.py:165 load_quote_events` читает part первым, а при его отсутствии фильтрует `quote_events.parquet` по `match_id`. Медленнее, но работает.
- Не трогать `_logs` — они нужны для дебага свежего прогона.

### A2. Хелпер `resolve_jsonl` / `open_maybe_gz` + патч читателей и проверок существования

Два хелпера в `src/shared/utils/jsonl_io.py`:

- `resolve_jsonl(path) -> Path` — если plain `path` существует, отдаёт его; иначе существующий `path + ".gz"`; если нет обоих, возвращает `path` для обычной обработки отсутствия файла. Уже переданный путь `.gz` не получает второй суффикс.
- `open_maybe_gz(path)` — открывает `resolve_jsonl(path)`; `.gz` через `gzip.open`, иначе обычный `open`. Возвращает текстовый поток.

Оба файла могут остаться после прерывания компрессора или повторного скачивания plain-файла синком. В этом случае читатель предпочитает plain, а дедуп A4 удаляет его только после проверки равенства содержимого.

Патчить надо не только `open()`. Есть места, где код проверяет наличие файла и **молча пропускает работу**. Они не падают — они тихо отдают неверный результат. Это главный риск всей фазы.

#### A2.1 Проверки существования и `stat` (делать первыми)

| Место | Что сейчас | Что сломает gzip |
|---|---|---|
| `src/backtest/telonex_local.py:275` | `not (archive / "core_trace.jsonl").is_file()` → `archive = None`, только `warning` | strip_own_book не отработает. Свои ордера останутся в стакане. Цифры бэктеста поедут молча. |
| `src/backtest/telonex_local.py:174` | `_archive_stamp` строит ключ кэша через `path.is_file()`, иначе `f"{path}|-"` | Все сжатые архивы дадут `|-`. Ключ перестанет реагировать на изменение файла. |
| `src/viewer/archive_read.py:42-47` | `iter_json_objects` глотает `OSError` и возвращает пустой итератор | Вьюер покажет пустую ленту вместо ошибки. Патч этого одного места закрывает `game_state_replay.py` и `live_tape.py`. |
| `src/viewer/live_tape.py:454` | `_parse_core_trace`: `not path.is_file()` → `None` | Пропадёт весь lifecycle ордеров, без сообщения. |
| `src/archive_index/schedule.py:375` | `not feed_path.is_file()` → `ScheduleExtractionError("feed_missing")` | Падает громко — но всё равно надо править. |
| `src/archive_index/schedule.py:379` | `feed_path.stat()` для детекта дописывания | Надо снимать `stat` с того же пути, который реально читаем. |

`src/backtest/live_archives.py:97` и `:125` гейтят `session.jsonl`. Так как `session.jsonl` мы не сжимаем, их не трогаем.

#### A2.2 Читатели

`session.jsonl` из таблицы исключён — он остаётся plain (см. раздел «Контекст»). Значит `scripts/backfill_trader_fills.py` и `scripts/measure_live_grid_cadence.py` править не надо вообще.

| Файл в архиве | Читатели |
|---|---|
| `grid_state.jsonl` | `src/archive_index/schedule.py`, `src/trader/grid_archive.py`, `src/viewer/game_state_replay.py` |
| `oddin_state.jsonl` | `src/archive_index/schedule.py`, `src/trader/oddin_archive.py`, `src/viewer/game_state_replay.py` |
| `state.jsonl` | `src/archive_index/schedule.py`, `src/trader/grid_archive.py`, `src/trader/oddin_archive.py`, `src/trader/steam_archive.py`, `src/viewer/game_state_replay.py`, `src/collect/s04_fetch_grid_starts.py` |
| `core_trace.jsonl` | `src/backtest/strip_own_book.py` (строки 80, 102, 128), `src/backtest/telonex_local.py`, `src/trader/replay_core_trace.py`, `src/viewer/live_tape.py` |

Писателей НЕ трогаем (`grid_live_feed.py`, `oddin_live_feed.py`, `steam_live_feed.py`, `state_writer.py`, `core_trace.py`, `session_journal.py`) — они продолжают писать plain `.jsonl`, компрессор сожмёт после финала.

Перед патчем перепроверить список через `rg`: искать не только имена файлов, но и использования `GRID_STATE_ARCHIVE_FILENAME`, `ODDIN_STATE_ARCHIVE_FILENAME`, `STATE_ARCHIVE_FILENAME`, `CORE_TRACE_FILENAME` и построенных из них путей. Проверить `open(`, `Path.open`, `is_file(`, `exists(`, `stat(`, `glob(` у читателей; писателей не менять.

### A3. Компрессор-сервис (новый демон, живёт на VPS)

Новый `src/trader/compress_archives.py`:
- цикл: `scan()` → `sleep(3600)` (как compact-сервисы коллектора — демон с внутренним расписанием, без cron);
- тот же модуль поддерживает `--once` для разового запуска и `--dry-run` для списка кандидатов без изменений файлов;
- `scan()`: обрабатывать только директории с `execution_cleanup.json`, где все существующие файлы из белого списка не менялись минимум 48 часов. Проверять возраст plain-файла, а если остался только gzip — его `mtime`. Свежий файл откладывает обработку всей match-директории. `session.jsonl`, маркер и служебные временные файлы не участвуют в расчёте возраста;
- для каждого оригинала сохранить идентичность файла и его `size`, `mtime_ns`, `ctime_ns`; писать в `f.jsonl.gz.tmp` → полностью прочитать gzip с проверкой целостности и побайтово сравнить распакованное содержимое с оригиналом → убедиться, что оригинал не изменился → atomic `rename` в `f.jsonl.gz` → повторно проверить неизменность оригинала → удалить оригинал. Если файл изменился или проверка не прошла, оригинал сохранить и сообщить причину. Если gzip уже опубликован, при конфликте оставить оба файла;
- `.gz` уже существует и оригинал тоже: проверить целостность gzip, равенство распакованного содержимого оригиналу и неизменность оригинала за время сравнения. Только после этого удалить plain. При различии/повреждении оставить оба и записать конфликт в лог; автоматически не перезаписывать `.gz`. Одного `gzip -t` недостаточно;
- общий код сравнения/удаления используется и A4. Компрессор и синк на одной машине используют общую блокировку для одного archive-root; синк держит её на весь rsync-проход и дедуп. Это предотвращает локальную замену файла синком во время сжатия. Трейдер эту блокировку не использует — для него действует условие «маркер + 48 часов»;
- **Белый список имён, а не `*.jsonl`:** `grid_state.jsonl`, `core_trace.jsonl`, `oddin_state.jsonl`, `state.jsonl`. `session.jsonl` пропускать всегда. Глоб по `*.jsonl` подхватил бы его и убил бы late fill (см. «Контекст»). Белый список заодно защищает от новых имён файлов, которые появятся позже.
- Корни берутся из env/аргумента — на VPS это `data/trader_live` и `data/trader_paper`; при реализации явно передать оба корня сервису.
- Для перечисленных закрытых исторических корней B3, B6 и C предусмотреть разовый режим без маркера после проверки отсутствия писателей. Он сохраняет белый список, 48 часов и все проверки содержимого; демон не использует этот режим.
- Логировать сводку: сжато N файлов, освобождено X, пропущено по возрасту/маркеру, обнаружено конфликтов.

В `compose.yaml` — третий сервис рядом с `live`/`paper`:

```yaml
compress:
  build: { context: .., dockerfile: esports-trader/Dockerfile }
  init: true
  restart: unless-stopped
  command: ["uv", "run", "--frozen", "--no-dev", "python", "-m", "trader.compress_archives"]
  volumes:
    - ./data/trader_live:/app/data/trader_live
    - ./data/trader_paper:/app/data/trader_paper
    - ./src:/app/src:ro
```

(имена маунтов сверить с тем, что реально на VPS: `sync_trader.py` тянет `trader_live/`; в command/env добавить оба корня согласно реализованному интерфейсу). Замер gzip 76M ≈ 1–3 сек ядра не включает повторное чтение для сравнения. Время и I/O полного алгоритма измерить на первом пакете; бэклог обрабатывать последовательно.

### A4. Дедуп в `scripts/sync_trader.py`

После успешного rsync-прохода: для каждой локальной match-директории, где лежат оба `X.jsonl` и `X.jsonl.gz`, применить условия «маркер + 48 часов» и общий алгоритм сравнения/дедупа A3. Удалять plain только при равенстве содержимого и неизменности оригинала. При конфликте оставить оба и сообщить. Rsync никогда не удаляет, поэтому remote-side gzip иначе оставил бы дубль навсегда. При ошибке rsync или `--dry-run` ничего не удалять.

Тот же белый список имён, что в A3. `session.jsonl` не дедупим — его `.gz` не должно существовать вообще, и если он вдруг появился, это сигнал ошибки, а не повод удалять plain-файл.

`scripts/sync_trader.py` сам jsonl не читает — `scan_archive` считает покрытие по `match.json`. Отчёт после синка A4 не сломает.

## Фаза B — локальные разовые чистки (Mac)

Каждую группу — с `du` до/после. Удаления показывать списком перед исполнением.

Пункт 1 независим от gzip, но требует проверяющего прохода по условиям A1. Все пункты с JSONL gzip требуют A2, A3/A4 и успешных проверок на копиях. Сначала обработать небольшой реальный пакет и проверить чтение, затем остальной объём.

1. **Интермедиаты бэктестов (~28G)**: в `data/backtests/` составить список завершённых, не работающих сейчас прогонов. Отдельным разовым проходом проверить сохранность удаляемых parts/шардов в итоговых results/fills/quote_events и наличие успешного summary. Для старых прогонов сравнение содержимого может потребовать чтения итоговых файлов — это разовая проверка, не накладные расходы каждого будущего merge. Удалять `seed*/shard_*of*` и `quote_event_parts/` только из прошедшего проверку списка; сомнительные прогоны пропустить. Наличие Parquet у 541 seed-директории не заменяет эту проверку. Реализация: `esports-trader/scripts/cleanup_backtest_intermediates.py` — dry-run печатает список и причины скипов, `--apply` удаляет после повторной проверки. Сверка содержимого — пер-матчевый отпечаток (count, sum ts_ns) в каждом part/fills против итогового parquet; гейты — summary.json, результаты шардов ⊆ родительских, 48 часов без изменений, без посторонних файлов, симлинки LIVE исключены.
2. **gzip терминальных jsonl в `data/trader` (~48G → ~2.5G)**: общий код A3, белый список и условие «`execution_cleanup.json` + 48 часов без изменений». `session.jsonl` НЕ сжимать. Только после A2, A3/A4 и проверок.
3. **gzip `data/live_paper/` + `data/live_paper_live/`** (~15G → ~0.8G): эпохи закрыты. После проверки отсутствия писателей использовать исторический разовый режим A3: без маркера, но с 48 часами, белым списком и сравнением содержимого. `session.jsonl` пропускаем.
4. **Удалить `docs/experiments/ensemble/{cache,cache_kcurve,cache_lol}`** (~1.7G) — регенерируемые кэши, пользователь подтвердил.
5. **Удалить старые `market_seconds/v*`** (~1.3G): сначала вычислить текущий `CACHE_VERSION` (`PYTHONPATH=src uv run python -c "from market_data.build_market_data import CACHE_VERSION; print(CACHE_VERSION)"`), удалить все `v*`, кроме текущего (на момент анализа — `va75c29ad`).
6. **`data/archive/`**: удалить `2026-07-28/` (2.6G) и `2026-08-06_pre_rebuild/` (1.8G). В `2026-09-20_pgl_removal/` — gzip только файлов из белого списка A3 в историческом разовом режиме после проверки отсутствия писателей (576M → ~40M), каталог оставить. До истечения 48 часов файлы пропускать; `session.jsonl` не сжимать.
7. **`data/lol/processed/datasets_{last,late,late_last,roles,horizons}`** (~2.6G): удалить — код читает только `processed/datasets` и `map_builds`.
8. **`data/backtests/{dota,lol}_maker/_logs` старых прогонов** (~2G): gzip старых логов или удалить логи прогонов, которые снесём в п.9.
9. **Старые прогоны бэктестов** — отдельный список кандидатов по возрасту (344 директории, LIVE = `research-val934-0919` на момент анализа) — показать пользователю, он выбирает.

## Фаза C — VPS

- После локальных проверок доставить на VPS поддержку чтения gzip и код компрессора. Проверить `--dry-run` и небольшой реальный пакет на VPS, затем запустить сервис `compress` из A3 (`docker compose up -d compress`). Он сожмёт остальной бэклог за первые проходы, только при выполнении условия «маркер + 48 часов».
- Разово gzip `data/live_paper` на VPS (~7.7G → ~0.4G): мёртвая pre-rollout tape, демон не нужен. Исторический разовый режим A3 без маркера — 30/166 dirs без `execution_cleanup.json` (tape старше маркера). Сохранить 48 часов, белый список без `session.jsonl` и проверку равенства содержимого. Перед запуском убедиться, что в `live_paper` на VPS никто не пишет.
- Удалить `/var/lib/polymarket-onchain-state/import-staging/` (~4.2G): после `accept` это чистый дубль `parquet/onchain_fills` (accept копирует, staged не удаляет). По `import/bootstrap-report.json` от 2026-09-21: 84 620 файлов `copied`, rejected=0, conflict=0 — уникальных байт нет. `import/` (инвентори + отчёт, 59M) оставить как запись миграции. Само не растёт — дневной сборщик пишет напрямую в архивы.
- После первого синка локально убедиться: `.gz` докачиваются, старые `.jsonl` дедупятся шагом A4.

## Не трогаем

- `data/*/raw/telonex/*/book_snapshot_full` (159G), `onchain_fills`, `trades` — платное сырьё, уже отфильтровано по universe при скачивании.
- `data/raw/telonex/polymarket/_backtest_cache` (3.3G) — кэш стаканов, нужен для скорости (пользователь подтвердил).
- `data/experiments/` (830M) — оставить пока (пользователь).
- `data/lol/raw/lolesports`, `stratz_matches`, `opendota_matches`, `pro_matches` — сырьё API.
- `data/new_model/`, `data/new_processed/dataset/`, `data/new_processed/market_seconds/va75c29ad`.
- Все writers jsonl — пишут plain, как сейчас.
- `session.jsonl` — нигде не сжимаем: ни демоном, ни разовыми прогонами, ни на VPS.

## Порядок работ

1. A1 → проверки A1 → разовая проверка старых прогонов → B1. Независимы от gzip.
2. A2 — хелперы, проверки существования (A2.1), читатели (A2.2).
3. Проверки A2 (ниже). Без них дальше не идти.
4. A3, A4 — общий компрессор, демон и дедуп в синке. Проверки на копиях, включая ошибки и повторный запуск.
5. Небольшой реальный пакет на Mac → проверка чтения и времени работы → B2, B3 и gzip из B6. B4–B9 выполнять с указанными для них условиями.
6. Фаза C — читатели на VPS, небольшой пакет, затем сервис и остальная чистка.

## Проверки

- После A1: прогнать существующий backtest-сьют/один seed на малом наборе — мердж проходит, `summary.json` пишется, шарды исчезают, `quote_events.parquet` на месте.
- После A1: открыть `backtest/inspect/tape.py` на прогоне без parts — лента читается из `quote_events.parquet`.
- После A1: отсутствующий part, неполный merge или ошибка финализации не удаляют исходники; корректные пустые квоты не считаются повреждением. Замерить добавленное время проверок на малом прогоне, не добавлять повторного чтения всей ленты в обычную финализацию. Не запускать suite во время live-карты.
- После A2: взять копии архивов Steam, GRID и Oddin и сравнить результаты до/после сжатия для применимых путей: `archive_index` schedule extraction, `telonex_local` strip_own_book, viewer replay, `replay_core_trace`. Проверять на непустых исходных данных. Одинаковый результат обязателен; отсутствие падения не является проверкой.
- После A2: через `rg` повторно проверить читателей по именам файлов, константам и использующим их путям — не осталось неподдерживающих gzip `open`, проверок наличия, `stat` или `glob`. Проверить выбор plain, когда существуют оба файла.
- После A3/A4: прогон на копиях проверяет целостность и равенство содержимого, atomic rename, пропуск директорий без маркера, файлов моложе 48 часов и `session.jsonl`. Свежий `session.jsonl` не блокирует сжатие старых файлов из белого списка.
- Проверить повреждённый gzip, различающиеся plain/gzip, изменение оригинала во время обработки, прерывание до/после публикации gzip и повторный запуск. При ошибках оригинал не теряется; конфликтующие копии остаются. `--dry-run` не меняет и не удаляет файлы; неуспешный rsync не запускает дедуп. Проверить общую блокировку синка и компрессора.
- После первого реального пакета: повторить проверки читателей и замерить время полного сжатия со сравнением. Затем обрабатывать остальной объём.
- После B: `du -sh data/` — ориентир ~185–190G после всех выбранных удалений и созревания архивов. Файлы моложе 48 часов и конфликты остаются, поэтому это не обязательный порог успешности.

## Не проверено

Пункты фазы C про VPS взяты из анализа и не перепроверены на самой машине: `import-staging` как чистый дубль после `accept`, и 30/166 директорий `live_paper` без `execution_cleanup.json`. Перепроверить на VPS перед удалением.
