# STEP-009 — s08: книги Telonex для каталога

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `03f82f3f`. Figma-ссылок в шаге нет.

## 1. Что делает шаг

- Новый `src/cs2/s08_fetch_telonex_books.py` качает три канала Telonex — `book_snapshot_full`, `trades`, `onchain_fills` — для двух токенов каждой строки каталога CS2. Качает только дни UTC окна карты.
- Файлы ложатся в `data/cs2/raw/telonex/polymarket/<channel>/asset_id=<token>/<day>.parquet`. Это та же раскладка, что у Dota и LoL. Её читают `load_token_book` (s09) и `backtest/telonex_local.py` (бэктест).
- Готовый файл повторно не качается.
- В конце s08 печатает число файлов и ГБ по месяцам и каналам. Ещё он пишет аудит: строка на каждую клетку «канал × токен × день».
- `src/cs2/constants.py` получает пути Telonex. `src/cs2/s06_publish_catalog.py` получает читатель каталога. `Makefile` получает цель `cs2-books`.
- Новый тест `tests/test_cs2_books.py`.

`src/lol/` в шаге не меняется.

## 2. Что я проверил (факты)

Скрипты проб лежат в `tasks/cs2-follow300/orchestrate/work/plan009/` (`probe.py`, `catalog_probe.py`, `slice_probe.py`, `schemas.py`). Данные проб — там же. Реальные `data/` не тронуты.

1. **Загрузчик LoL качает только книги.** `src/lol/02_fetch_telonex_books.py`:
   - `download_one` (`:482`) жёстко берёт `TELONEX_BOOK_CHANNEL` (`:484`, `:493`), `job_audit_row` (`:379`) — тоже (`:384`). Для `trades` и `onchain_fills` его взять как есть нельзя.
   - Общие части без канала: `read_api_key` (`:131`), `telonex_http_client` (`:198`), `http_stream` (`:110`), `write_stream_body` (`:211`), `raise_if_fatal_status` (`:235`), `download_catalog_file` (`:245`), `slice_catalog` (`:279`), `expand_dates` (`:182`).
   - `refresh_catalog` (`:332`) удаляет полный каталог в `finally` (`:343`) и отдаёт только даты книги (`CatalogMarket`, `:72`).
   - Модуль с цифрой в имени. Его импортируют через `import_module` и `Protocol`: так уже делают `src/lol/livestats_frames.py:43–59` и `tests/test_lol_fetch_telonex_books.py:69`.
2. **Dota и LoL берут `trades` и `onchain_fills` не из API Telonex, а с нашего коллектора на VPS** (`scripts/sync_collector_parquet.py:3`, `:40`). Для CS2 коллектора нет. Значит, s08 — первый код, который качает эти два канала из API.
3. **Схемы API = схемы коллектора** (проба 18.10, токен `cs2-wbt-havu-2026-10-17-game1`, день 2026-10-16, плюс файлы Dota 2026-03-19 в `esports-trader/data/raw/telonex/polymarket`):
   - `book_snapshot_full` — 9 колонок, совпадают один в один;
   - `trades` — 12 колонок, совпадают один в один; `trade_id` = hash транзакции;
   - `onchain_fills` — все 20 колонок коллектора плюс 6 новых: `contract_address`, `taker_order_hash`, `taker_fee`, `builder`, `metadata`, `outcome_id`.

   Бэктест читает колонки по имени: `onchain_side.py:30–36`, `onchain_retime.py:48`, `:184`. Он переписывает день целиком (`onchain_retime.py:210–220`), лишние колонки проходят насквозь. Фреймворк тоже ищет колонки по имени (`prediction-market-backtesting/.../data_sources/telonex.py:3369`).
4. **Ответ на день без файла — 404 с JSON** `{"detail":"File not found: ..."}`. Проба 18.10 на 2026-10-17: `book_snapshot_full` 404, `trades` 404, `onchain_fills` 200 (506 строк). На 2026-10-10 (до создания рынка) все три — 404.
5. **Каталог Telonex знает, какие дни есть.** `GET /v1/datasets/polymarket/markets` без ключа: 3 139 МБ, 5 100 852 строки, 42 с. У рынка есть `<channel>_from` и `<channel>_to` для каждого канала, строки `YYYY-MM-DD`.
   - `_to` — исключающая граница. У рынка выше `book_snapshot_full_to` = `2026-10-17` — и файл 10-17 даёт 404. `onchain_fills_to` = `2026-10-18` — и файл 10-17 есть.
   - Граница публикации канала (максимум `_to` по всему каталогу, 18.10): книги и trades — `2026-10-17`, onchain_fills — `2026-10-18`. Книги и trades выходят на день позже onchain_fills.
   - У рынков, созданных 17.10, `book_snapshot_full_from` и `trades_from` пустые: ни одного опубликованного дня ещё нет.
   - Токены 12 карт scratch-каталога 17.10 совпали с `asset_id_0/1` каталога Telonex и по набору, и по порядку (12 из 12).
6. **Цена каталога.** `slice_catalog` из LoL режет полный каталог до 12 рынков за 2,0 с. Максимум трёх колонок `_to` по всем 5,1 млн строк — 0,2 с.
7. **Каналы у рынков CS2 по месяцам** (полный каталог 18.10, рынки с вопросом «Counter-Strike:» и исходом):
   - книги есть у 100% рынков с февраля 2026; в ноябре 2025 — 96,6%; в октябре 2025 — 31,9%;
   - `trades` и `onchain_fills` в июне–октябре 2026 есть у 90,7–99,6% рынков. 3–4% рынков validation не имеют ни одной сделки.
8. **Окно рынка в Dota.** `build_market_second_rows` (`src/market_data/build_market_data.py:105–107`): начало — `DOTA_PRIOR_ANCHOR_SECOND − DOTA_PRIOR_WINDOW_SECONDS` = −85 − 61 = −146 с; конец — последняя секунда + `MODEL_TARGET_HORIZON_SECONDS` = 300 с (`src/shared/constants/dataset.py:13–15`).
9. **Окно бэктеста Dota** — от горна − 2 мин до конца карты + 1 мин (`src/backtest/context.py:12–13`, `:24–32`). Окно s08 (−146 с … +300 с) его покрывает.
10. **Что бэктест требует от дисков** (`src/backtest/telonex_local.py:153–187`): для обоих токенов — файл книги и файл `onchain_fills` на каждый день окна; файл `trades` — на каждый день, где в `onchain_fills` есть строки.
11. **s09 читает книги** через `load_token_book(token_id, start_us, end_us, telonex_root)` (`src/shared/utils/telonex_book.py:505`): все файлы `<root>/book_snapshot_full/asset_id=<token>/*.parquet`, строки внутри окна.
12. **Каталог CS2** (`Cs2CatalogRow`, `src/cs2/s06_publish_catalog.py:66–79`; scratch `work/impl008/oct17/catalog/match_catalog.parquet`): `spawn_at` и `ended_at` — строки вида `2026-10-17T08:00:18.984Z`; `match_id` и `team_a_token_index` — int64; `condition_id` уникален (`:431–438`). Читателя каталога в коде ещё нет.
13. **Аудит LoL** (`data/lol/processed/telonex/download_audit.parquet`): `skipped_valid` 101 527, `downloaded` 16 137, `http_404` 2 248, `missing_interval` 105. 404 внутри интервала каталога бывает.

## 3. Решения

1. **Загрузчик LoL берём через `import_module`, код LoL не меняем.** В s08 — `Protocol` `LolTelonexStage` с восемью функциями из п. 2.1: `read_api_key`, `telonex_http_client`, `http_stream`, `write_stream_body`, `raise_if_fatal_status`, `download_catalog_file`, `slice_catalog`, `expand_dates`. Модуль: `LOL_TELONEX = cast(LolTelonexStage, cast(object, import_module("lol.02_fetch_telonex_books")))`, как `FETCH` в `livestats_frames.py:59`. Перенос этих функций в `shared` сломал бы подмену `http_stream` в тестах LoL (`tests/test_lol_fetch_telonex_books.py:283`, `:654`).
2. **Свой цикл загрузки одного дня одного канала** (`download_channel_day`), по образцу `download_one` + `apply_download_status` LoL, но с каналом в задании:
   - цель уже есть → `skipped_valid`, без запроса;
   - `<day>.parquet.partial` удаляется перед попыткой;
   - 200 → `write_stream_body` в `.partial`, `validate_target` с колонками канала, атомарная замена → `downloaded`. Файл не прошёл проверку → новая попытка;
   - 404 → `http_404`;
   - 429 и 5xx, `TransportError`, `TimeoutException` → пауза `retry_delay` и новая попытка, до `TELONEX_MAX_ATTEMPTS` = 6. Потом `retries_exhausted`;
   - 401, 403, прочие 4xx → `FatalDownloadError` из `raise_if_fatal_status`. Прогон останавливается, аудит не пишется.
3. **Один клиент httpx на прогон.** `main` создаёт его через `LOL_TELONEX.telonex_http_client()` (лимит 20 соединений) и передаёт в функции. Пул — `ThreadPoolExecutor(TELONEX_DOWNLOAD_WORKERS)` = 20. Тесты передают `httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)`. Отдельная точка подмены не нужна.
4. **Окно карты** — `compute_map_days(row)`: начало = `parse_utc(spawn_at)` + (`DOTA_PRIOR_ANCHOR_SECOND` − `DOTA_PRIOR_WINDOW_SECONDS`) с; конец = `parse_utc(ended_at)` + `MODEL_TARGET_HORIZON_SECONDS` с. Дни — даты UTC от начала до конца включительно, через `LOL_TELONEX.expand_dates(start_day, end_day + 1 день)`. Числа −146 и 300 в коде не пишем, берём константы.
5. **Каталог Telonex на каждый прогон** (`load_telonex_catalog`):
   1. `download_catalog_file(client, full_path)`, `full_path` = `<telonex_dir>/markets.full.parquet`;
   2. граница публикации каждого канала — максимум `<channel>_to` по всему файлу, проход батчами по трём колонкам;
   3. `slice_catalog(full_path, <telonex_dir>/catalog.parquet, condition_ids)`;
   4. в `finally` удалить `markets.full.parquet` и `markets.full.parquet.partial`;
   5. прочитать срез: на рынок — `asset_id_0`, `asset_id_1` и интервал `[from, to)` каждого канала. Дубль `market_id` → `RuntimeError`.

   `refresh_catalog` LoL не подходит: он удаляет полный файл до того, как мы посчитаем границу, и не отдаёт интервалы `trades` и `onchain_fills`.
6. **Решение по клетке «канал × токен × день»** (`decide_cell_reason`), по порядку:
   1. рынка нет в срезе → `missing_from_catalog`;
   2. набор токенов каталога CS2 ≠ набор `asset_id_0/1` → `token_id_mismatch`;
   3. день ≥ границы публикации канала → `not_published`. Telonex этот день ещё не выложил;
   4. день вне `[from, to)` канала (или `from` пустой) → `no_telonex_data`. У Telonex файла нет и не будет;
   5. иначе — задание на загрузку.

   Пункты 1–4 не шлют запросов. Так повторный прогон не тратит запросы на известные пустые дни.
7. **Аудит — строка на каждую клетку**, frozen dataclass `Cs2TelonexAuditRow`: `match_id`, `condition_id`, `channel`, `asset_id`, `day`, `reason`, `bytes`. `bytes` — размер файла на диске для `downloaded` и `skipped_valid`, иначе 0. Причины — `Literal`: `downloaded`, `skipped_valid`, `http_404`, `retries_exhausted`, `not_published`, `no_telonex_data`, `missing_from_catalog`, `token_id_mismatch`. Значения общих причин — как у LoL (`src/lol/constants.py:116–121`). Файл — `data/cs2/raw/telonex/download_audit.parquet`, перезаписывается каждым прогоном.
8. **Код выхода 1** — если есть `not_published` или `retries_exhausted`. Их чинит повторный прогон: позже или после сбоя сети. `http_404`, `no_telonex_data`, `missing_from_catalog`, `token_id_mismatch` — факты о данных Telonex. Они идут в печать, но код выхода 0. Аудит пишется до выхода.
9. **Колонки для проверки файла** (`CHANNEL_REQUIRED_COLUMNS`, словарь канал → frozenset):
   - `book_snapshot_full` — `TELONEX_BOOK_REQUIRED_COLUMNS` из `src/lol/constants.py:95`;
   - `trades` — `timestamp_us`, `asset_id`, `trade_id`, `price`, `size`, `side`;
   - `onchain_fills` — `block_timestamp_us`, `tx_hash`, `asset_id`, `sibling_asset_id`, `maker_asset_id`, `maker_side`, `taker_side`, `amount`, `price`.

   Это колонки, которые читают бэктест и `validate_target`. Файлы храним байт в байт, как отдал Telonex.
10. **Порядок заданий** — новые дни первыми, как `job_date_newest_first` LoL. Прогресс — строка на каждые 500 заданий, как в LoL.
11. **Печать в конце** (`print_books_summary`):
    - граница публикации трёх каналов;
    - число карт, число заданий;
    - по каналу — число клеток каждой причины;
    - по месяцу дня файла и каналу — число файлов и ГБ (`downloaded` + `skipped_valid`), строки отсортированы по месяцу и каналу.
12. **Читатель каталога в s06** — `read_catalog_rows(path) -> list[Cs2CatalogRow]`. Ему место рядом с `Cs2CatalogRow`: его же возьмут s09, s10, s11 и бэктест. Поля — явным чтением, как `linked_series_from_row` (`src/cs2/s04_link_bo3.py:204`).
13. **Без `--fetch`, `--workers`, `--limit`.** s08 — только сеть, повторный прогон дешёвый. Параметры CLI: `--catalog-dir` (по умолчанию `CS2_CATALOG_DIR`), `--telonex-dir` (по умолчанию `CS2_RAW_TELONEX_DIR`). Пробы передают оба.

## 4. Изменения по файлам

### `src/cs2/constants.py`

```python
CS2_RAW_TELONEX_DIR = CS2_RAW_DIR / "telonex"
CS2_TELONEX_ROOT = CS2_RAW_TELONEX_DIR / TELONEX_EXCHANGE
CS2_TELONEX_CATALOG_PATH = CS2_RAW_TELONEX_DIR / "catalog.parquet"
CS2_TELONEX_AUDIT_PATH = CS2_RAW_TELONEX_DIR / "download_audit.parquet"
```

`TELONEX_EXCHANGE` — из `shared.constants.telonex`. `CS2_TELONEX_ROOT` — корень раскладки: его передадут s09 (`telonex_root`) и бэктест (`capture_root`). В коде s08 корень — `telonex_dir / TELONEX_EXCHANGE`, чтобы `--telonex-dir` двигал всё вместе.

### `src/cs2/s06_publish_catalog.py`

- `read_catalog_rows(path: Path) -> list[Cs2CatalogRow]`: `replace_nulls_with_none(pd.read_parquet(path))`, записи, `Cs2CatalogRow` с явными полями.

### `src/cs2/s08_fetch_telonex_books.py` (новый, ~300 строк)

Типы (frozen dataclass, `Literal`):

- `TelonexChannel` — `Literal["book_snapshot_full", "trades", "onchain_fills"]`; `TELONEX_CHANNELS` — кортеж трёх значений. Значения равны константам `shared/constants/telonex.py`.
- `Cs2TelonexReason` — причины из решения 7.
- `ChannelInterval(start: str, end: str)` — `[from, to)`, пустая строка = нет дней.
- `TelonexMarket(condition_id, asset_id_0, asset_id_1, intervals: dict[TelonexChannel, ChannelInterval])`.
- `TelonexCatalog(markets: dict[str, TelonexMarket], frontier: dict[TelonexChannel, str])`.
- `TelonexJob(match_id, condition_id, channel, asset_id, day, target: Path)`.
- `PlannedCells(jobs: tuple[TelonexJob, ...], settled: tuple[Cs2TelonexAuditRow, ...])`.
- `Cs2TelonexAuditRow` — решение 7.
- `SizeLine(month, channel, files, gigabytes)`.

Функции:

- `compute_map_days(row: Cs2CatalogRow) -> tuple[str, ...]` — решение 4.
- `read_channel_frontier(full_path: Path) -> dict[TelonexChannel, str]` — максимум `_to`.
- `load_telonex_markets(slice_path: Path) -> dict[str, TelonexMarket]`.
- `load_telonex_catalog(client, telonex_dir, condition_ids) -> TelonexCatalog` — решение 5.
- `decide_cell_reason(market, row, channel, day, frontier) -> Cs2TelonexReason | None` — решение 6, `None` = качать.
- `plan_cells(rows, catalog, telonex_root) -> PlannedCells` — цели через `asset_channel_dir(telonex_root, channel, token) / f"{day}.parquet"`.
- `download_channel_day(client, api_key, job) -> Cs2TelonexAuditRow` — решение 2. URL — `download_url(channel, day)`, заголовок `Authorization: Bearer <key>`, параметр `asset_id`.
- `download_jobs(client, api_key, jobs) -> list[Cs2TelonexAuditRow]` — пул, `as_completed`, при исключении `shutdown(cancel_futures=True)` и дальше вверх.
- `summarize_sizes(rows) -> list[SizeLine]`.
- `has_pending_cells(rows) -> bool` — решение 8.
- `print_books_summary(rows, catalog, map_count, job_count)`.
- `fetch_cs2_books(client, api_key, catalog_dir, telonex_dir) -> list[Cs2TelonexAuditRow]` — каталог CS2 → каталог Telonex → план → загрузка. Возвращает все строки аудита.
- `main(catalog_dir, telonex_dir)`: `load_dotenv()`, ключ, клиент, `fetch_cs2_books`, `write_parquet` аудита, печать, `typer.Exit(1)` при `has_pending_cells`.

Ключ API не попадает ни в печать, ни в аудит, ни в текст исключений.

### `Makefile`

```make
cs2-books:  ## CS2 s08 Telonex book_snapshot_full, trades, onchain_fills for catalog maps (exit 1 = rerun later)
	$(PY) src/cs2/s08_fetch_telonex_books.py $(ARGS)
```

`cs2-books` — в `.PHONY`. В `cs2-collect` не входит: feature.json держит там только s01–s07.

### `tests/test_cs2_books.py` (новый)

Без сети. Фикстуры — строки `Cs2CatalogRow`, parquet каталога Telonex в памяти (колонки `market_id`, `asset_id_0`, `asset_id_1`, `<channel>_from/_to` трёх каналов) и parquet канала с колонками из решения 9 и `asset_id` из запроса. `MockTransport` пишет каждый запрос в список.

1. `test_map_days_take_both_days_near_midnight` (parametrize):
   - карта 12:00–12:50 → один день;
   - `spawn_at` 00:01:00 → два дня: начало окна 23:58:34 прошлого дня;
   - `spawn_at` 23:58, `ended_at` 00:40 → два дня;
   - `ended_at` 23:57:00 → два дня: конец окна 00:02:00.
2. `test_catalog_row_plans_two_tokens_three_channels` — одна строка, один день, интервалы покрывают день → 6 заданий: 3 канала × 2 токена. Цели — `<root>/<channel>/asset_id=<token>/<day>.parquet`.
3. `test_fetch_writes_files_then_skips_them` — первый прогон: 6 `downloaded`, 6 файлов, в каждом `asset_id` своего токена. Второй прогон: 6 `skipped_valid`, ни одного запроса к `/downloads/`. `markets.full.parquet` после прогона нет.
4. `test_unpublished_and_empty_days_send_no_request` — день = граница книг → `not_published`; день до `trades_from` → `no_telonex_data`; `onchain_fills` отвечает 404 → `http_404`. Запросов к книгам и trades нет. `has_pending_cells` = True из-за `not_published`; без него — False.
5. `test_server_error_retries_then_downloads` — 503, потом 200, `time.sleep` подменён → `downloaded`.
6. `test_forbidden_stops_run` — 403 → `FatalDownloadError`.
7. `test_sizes_group_by_month_and_channel` — `summarize_sizes` на строках двух месяцев и двух каналов: файлы и ГБ по группам, `http_404` не считается.
8. В `tests/test_cs2_catalog.py` — `read_catalog_rows` читает то, что записал `catalog_frame`: те же строки, `match_id` — `int`, `team_a_win` — `bool`.

## 5. Крайние случаи

- **Карта около полуночи** — оба дня, оба токена, все три канала.
- **Старт карты в 00:00–00:02:26 UTC** — прошлый день целиком ради 146 с prior. Цена — один лишний файл книги на токен.
- **День не опубликован** (книги и trades выходят на день позже onchain_fills) → `not_published`, код 1, повторить позже.
- **День без сделок или до создания рынка** → `no_telonex_data`, запроса нет.
- **404 внутри интервала** → `http_404`, код выхода не меняет. Повторный прогон спросит этот день снова: файла на диске нет.
- **Рынка нет в каталоге Telonex или другие токены** → причина на каждую клетку этой карты.
- **Битый ответ или чужой `asset_id`** → новая попытка, потом `retries_exhausted`.
- **Прогон убит посреди записи** → на диске остаётся только `.partial`. Цель появляется только атомарной заменой, значит, существующая цель полная. Следующая попытка удаляет `.partial`.
- **Файл с 0 строк** — валиден (`validate_target`, `src/shared/utils/telonex_capture.py:70–71`), идёт как `downloaded`.
- **Редирект на presigned-ссылку** — клиент LoL идёт по нему сам (`follow_redirects=True`).

## 6. Проба в scratch

Папка — `tasks/cs2-follow300/orchestrate/work/impl009/`. Реальные `data/cs2/raw/telonex/` не создавать. Каждый прогон качает полный каталог Telonex (~3,1 ГБ, ~45 с) во временный файл и удаляет его.

1. Каталог 10.09 (`work/impl008/sep10/catalog`, 6 карт):
   `PYTHONPATH=src uv run python src/cs2/s08_fetch_telonex_books.py --catalog-dir <.../impl008/sep10/catalog> --telonex-dir <.../impl009/sep10>`.
   Ждём код 0, все три канала по 2 токена, без `not_published`. Записать в отчёт печать целиком.
2. Повторить тот же запуск. Ждём: 0 `downloaded`, все прежние `downloaded` стали `skipped_valid`, файлы не изменились (`stat` mtime).
3. Проверить чтение файлов кодом следующих шагов на 6 картах:
   - `load_token_book(token, start_us, end_us, telonex_root=<impl009/sep10>/polymarket)` — книга не пустая у обоих токенов;
   - `has_local_telonex_days((tok0, tok1), ReplayWindow(spawn_at − 120 с, ended_at + 60 с), <root>)` из `backtest.telonex_local` — число `True` из 6. Если есть `False`, назвать день и канал.
4. Каталог 17.10 (`work/impl008/oct17/catalog`, 12 карт) в `<impl009/oct17>`. На 18.10 граница книг была 2026-10-17. Если Telonex ещё не выложил 17.10, ждём `not_published` у книг и trades и код 1. Если выложил — код 0. Записать, что вышло, и границы из печати.
5. После проб `ls <impl009>/*/markets.full.parquet*` — пусто. `ls ../esports-trader/data/cs2/raw/` — папки `telonex` нет.

## 7. Проверки

1. `uv run python -m pytest tests/test_cs2_books.py tests/test_cs2_catalog.py -q` — всё зелёное.
2. `make test` — падают только две известные `lol_*` follow300 smoke (нет `data/lol` в worktree). Флейк `smoke[dota_9007700576]` — перезапустить его одного.
3. `uv run python -m basedpyright` — 0 ошибок.
4. `uv run python -m ruff check src/cs2 tests/test_cs2_books.py tests/test_cs2_catalog.py` и `uv run python -m ruff format --check` на тех же файлах. C901 ≤ 10.
5. `make help | grep cs2-books` — цель есть.
6. Проба из раздела 6, цифры — в progress.txt.
7. `git diff --stat main...HEAD -- src/lol` — пусто: LoL не тронут. Один коммит в `cs2-signal`, `git status` чистый.

## 8. Риски и открытые вопросы

1. **Бэктест (STEP-013) выкинет карты без `onchain_fills`.** `has_local_telonex_days` требует файл `onchain_fills` на каждый день окна у обоих токенов (`src/backtest/telonex_local.py:153–173`). У Telonex нет файла на день без сделок. 3–4% рынков validation не имеют ни одной сделки (п. 2.7). Пишет ли коллектор Dota пустые файлы на такие дни, я не проверял. s08 пустых файлов не выдумывает и даёт `no_telonex_data`. STEP-013 решает, как считать такой день; пилот даёт число карт.
2. **Задержка публикации.** Книги и trades выходят на день позже onchain_fills. Полный прогон (STEP-015) должен запускать s08 после того, как граница книг пройдёт последний день каталога. Код 1 и `not_published` в печати это показывают.
3. **Схема `onchain_fills` из API шире коллектора на 6 колонок.** Код бэктеста и фреймворк читают по имени (п. 2.3). Риск низкий, его подтвердит бэктест пилота.
4. **Полный каталог Telonex — 3,1 ГБ** и растёт. Каждый прогон качает его заново (42 с 18.10) и держит на диске до среза. Нужно ~3–4 ГБ свободного места сверх книг.
5. **404 внутри интервала считаю окончательным** (код 0), в отличие от LoL (там код 1). У LoL таких 2 248 из 120 017 клеток. Если пилот покажет много `http_404` у книг CS2, вернуться к этому.
6. **Время полного прогона я не замерял.** Оценка: 15–20 тыс. карт × 2 токена × 3 канала × ~1,05 дня ≈ 100–130 тыс. запросов при 20 потоках, книги ~73 ГБ (оценка ±50%). Цифры даст пилот.
7. **Окно бэктеста STEP-013 должно лежать внутри окна s08** (`spawn_at` − 146 с … `ended_at` + 300 с). Иначе у карт около полуночи не хватит дня.

## 9. Чего в шаге нет

- Проверки диска перед загрузкой: это правило оператора в STEP-015.
- Удаления книг train после s09.
- Каналов `quotes`, `book_snapshot_5`, `book_snapshot_25`.
- Отсечки `trades` по дням без сделок: качаем каждый день интервала.
- Правок `src/lol/`.
- s09 и всего ниже.
