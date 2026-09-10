# Задание 4 — загрузчики матчей (шаги 05, 06, 09)

Исходная ревизия: `efe54ac` (HEAD не двигался, коммитов нет).
Исходное состояние рабочего дерева: незакоммиченные изменения заданий 1–3
(`s01`, `s02`, `s04`, `s05a`, `common/catalog_types.py`, `common/opendota_candidates.py`,
`common/window_ids.py`, `docs/next_steps.md`, тесты + два новых тестовых модуля). Ничего из них не
откатывалось и не редактировалось.

Измененные файлы этого задания:

- `src/collect/s05b_fetch_stratz_matches.py` (шаг 05)
- `src/collect/s05_fetch_opendota_matches.py` (шаг 06)
- `src/collect/common/stratz_client.py` (шаг 09)
- `tests/test_fetch_stratz.py`, `tests/test_stratz_client.py` (обновлены + новые сценарии)
- `tests/test_fetch_opendota.py` (новый: покрытия у шага 06 не было вообще)

Сводные метрики (`ast`: `If` + `IfExp` + условия comprehension + лишние операнды `BoolOp`):

| Файл | Строки до/после | Условия до/после |
|---|---:|---:|
| `s05b_fetch_stratz_matches.py` | 453 → 362 | 24 → 18 |
| `s05_fetch_opendota_matches.py` | 139 → 103 | 8 → 5 |
| `common/stratz_client.py` | 107 → 104 | 11 → 9 |
| **итого production** | **699 → 569** | **43 → 32** |

Тестовая обвязка выросла: `tests/test_fetch_stratz.py` 43 → 206, `tests/test_stratz_client.py`
49 → 88, `tests/test_fetch_opendota.py` 0 → 123. Рост целиком в сценариях (mocked сеть, кеш во
временном каталоге), не в helpers.

---

## Шаг 05 — `s05b_fetch_stratz_matches.py`

### Что удалено и почему это упрощает основной сценарий

- `FetchOutcome` (summary + `request_seconds` + `processing_seconds` + `retried`).
  `fetch_and_cache_match` теперь возвращает `CacheSummary` напрямую: три `time.monotonic()`-замера и
  их перенос через объект исчезли, функция читается как «запросить → записать → описать».
- `FetchRunResult` (`ok`/`unusable`/`failed`/`retried_total`) и все четыре счетчика цикла.
  `fetch_pending_matches` теперь возвращает `None`; итог печатается по индексу
  (`index_usable=<usable>/<total_linked>`), который и так пересчитывается в `write_index`.
- Списки `req_times`/`proc_times`/`sleep_times`, `average`, `format_hms`, `CHECKPOINT_EVERY`,
  `run_started`, `elapsed`, `eta_seconds`, `pct`, расчет `req_per_hour` и вся checkpoint-сводка каждые
  25 матчей. Вместе с ними ушли `eta=` из стартового сообщения и `elapsed/eta` из построчного
  прогресса.
- `MatchFetch.retried` и `dataclasses.replace`: `fetch_match_with_retry` больше не считает общий
  `retried`, а просто `return fetch_match(...)`. Локальные счетчики `server_errors` и `rate_limits`,
  которые управляют лимитами повторов, и печать каждой попытки повтора сохранены без изменений.
- Отдельный nullable `outcome: FetchOutcome | None` + `failure = ""` + повторная развилка
  `if outcome is None`. Успех обрабатывается внутри `try`, ошибки — в `except`.

### Какие гарантии позволили удалить условия

- Ветка `if outcome is None` дублировала уже произошедший `except`: единственный способ получить
  `None` — попадание в `except (StratzError, httpx.TransportError, OSError)`. Перенос печати в
  соответствующие блоки — тождественное преобразование, порядок «печать → расчет sleep → sleep»
  сохранен.
- `retried_total` и все тайминги были read-only для печати и ни на что не влияли (grep по `src`,
  `scripts`, `tests`, `data/views.sql` — потребителей нет).
- `ok`/`unusable`/`failed` — производные от индекса и от строк прогресса; `usable_total` из
  `write_index` дает то же покрытие.

### Сохранено дословно

`RICH_QUERY`, `STRATZ_CACHE_PROFILE = "stratz_rich_v3"`, `StratzCacheEntry` вместе с
`response_bytes` и `graphql_errors`, `build_cache_entry`, `write_cache_atomic` (tmp + `replace`),
`CacheSummary` и схема `stratz_match_index.parquet`, `summarize_match`, `read_cache_summary`,
`scan_cache`, `select_pending_match_ids` (`force`/`limit`), `start_to_start_sleep`, 403 → `SystemExit`
с прежним текстом, отдельные лимиты `SERVER_ERROR_MAX_RETRIES=4` / `RATE_LIMIT_MAX_RETRIES=1`,
экспоненциальный backoff, `Retry-After` с fallback `RATE_LIMIT_DEFAULT_WAIT`, `timeout=90`,
поведение unusable-кеша (записывается и индексируется как `status="unusable"` с `reason`).

Пустая очередь: ранний выход в `main` сохранен без изменений — `write_index(summaries)` и выход до
`fetch_pending_matches`, то есть `stratz_client()` и `stratz_token()` не вызываются. Есть новый тест
на это.

### Изменение выходных схем и поведения на некорректных входах

Схема parquet не менялась (`match_id`, `status`, `reason`, `playback_available`, `start_time`,
`duration`) — `data/views.sql: stratz_match_index` затронут не был. Изменен только текст stdout:

- было `[12/500 2.4%] match=X status=ok playback=yes request=1.20s processing=0.30s sleep=1.00s elapsed=… eta=…`
  стало `[12/500] match=X status=usable reason=None playback=True`;
- было `[12/500 2.4%] match=X status=failed error=… elapsed=… eta=…`
  стало `[12/500] match=X status=error error=…`;
- checkpoint-строки каждые 25 матчей больше нет;
- финальная строка: `done: index_usable=N/M saved=<path>`.

Причина непригодности и текст ошибки конкретного запроса из вывода не исчезли. Поведение на
некорректных входах не изменилось.

---

## Шаг 06 — `s05_fetch_opendota_matches.py`

### Что удалено и почему

- `CacheCoverage` и `cache_coverage`: полный проход по всем 3086 линкованным ID с распаковкой
  каждого gzip и чтением `["version"]` выполнялся только ради двух печатаемых чисел
  (`opendota_cached`, `opendota_parsed`). Другим полным чтением не заменен: итоговые счетчики теперь
  берутся из уже известных длин (`len(match_ids)`, `len(to_fetch)`, `failed`). Вместе с ним ушел
  импорт `get_opendota_match`.
- `started`, расчет `rate = (fetched+failed)/elapsed*60` и ветка `if fetched % 50 == 0`.
- `FetchResult`: `fetch_one` теперь возвращает `str | None` (текст ошибки либо `None`), а match ID
  вызывающий код и так знает — в последовательном режиме из цикла, в параллельном из
  `match_id_by_future`. Это убрало dataclass, `nonlocal`-замыкание `note` и обертку `if to_fetch:`.
- Вложенный `note`/`nonlocal fetched, failed` заменен одной модульной функцией `report_failure`,
  общей для обоих режимов: печатает `failed OpenDota match <id>: <error>` и возвращает `1`/`0`.
- `RAW_OPENDOTA_MATCHES_DIR.mkdir(...)` в `main` и импорт константы: `write_raw_payload` сам делает
  `out.parent.mkdir(parents=True, exist_ok=True)` перед записью.

### Какие гарантии позволили удалить

- `opendota_match_cache_path(id).parent` — всегда `RAW_OPENDOTA_MATCHES_DIR` (одна строка в
  `shared/utils/opendota.py`), поэтому mkdir в `main` был дублем.
- `fetched`/`failed` вычислялись из `to_fetch`: успех = `len(to_fetch) - failed`, потому что
  `fetch_one` перехватывает `Exception` и всегда возвращает результат ровно один раз на ID.
- Внешняя обертка `if to_fetch:` защищала только от создания `http_client()` на пустой очереди;
  `http_client()` — локальный `httpx.Client` без токенов и сетевых обращений, так что ветка была
  лишней. Проверено тестом `budget=0` (0 запросов, каталог кеша не создается).

### Сохранено

Budget (`[: max(0, budget)]`), отбор только отсутствующих файлов, оба режима workers, `time.sleep(sleep)`
после каждого последовательного запроса (в параллельном режиме — как раньше, не применяется),
продолжение после ошибки отдельного матча, атомарная запись через `.tmp` + `replace`,
`opendota_params()` и стартовая строка `to_fetch=… workers=… budget=… api_key=…`.
Скорости free/paid режимов не унифицированы.

### Изменение вывода

`print_count("opendota_cached", …)` и `print_count("opendota_parsed", …)` удалены (это и был отчет,
требовавший распаковки всего корпуса). `requested_this_run` переименован в `saved_this_run` и теперь
означает именно сохраненные матчи (раньше это же число печаталось под именем «requested»).
`matches_in_corpus` и `failed_this_run` сохранены. Файловых схем шаг не меняет.

---

## Шаг 09 — `common/stratz_client.py`

- `StratzServerError.status_code` и его `__init__` удалены (на актуальном HEAD поле нигде не
  читалось; grep по `src`/`scripts`/`tests`). Код HTTP остался в сообщении: `f"STRATZ HTTP {status}"`.
- `stratz_client(timeout: float)` — обязательный аргумент; nullable fallback на
  `api.HTTP_TIMEOUT_SECONDS` и импорт `shared.constants.api` удалены. Единственный вызов
  (`s05b`, `timeout=90`) не менялся, то есть эффективный timeout прежний.
- `base_url=""` удален: httpx трактует пустой base_url как отсутствующий, а `stratz_graphql`
  всегда постит абсолютный `STRATZ_GRAPHQL_API`.
- `variables` в `stratz_graphql` стал обязательным `Mapping[str, Any]`, `variables or {}` убран.
  Вызов без переменных передает `{}` (в тестах).
- Нормализация GraphQL `data`/`errors` **не тронута** (пункт 4 плана): `isinstance`-проверки на
  внешней границе и раздельная передача `errors` в `build_cache_entry` сохранены, ошибка по-прежнему
  не кешируется как успешный матч. Классы `StratzAuthError` / `StratzForbiddenError` /
  `StratzRateLimitError` (с `retry_after`) / `StratzServerError`, обязательный User-Agent,
  `follow_redirects=False` и парсинг `Retry-After` сохранены.

---

## Проверки: команды и результаты

Все прогоны из корня `esports-trader` c
`PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -n 0`.

- целевые: `tests/test_fetch_stratz.py tests/test_stratz_client.py tests/test_match_catalog.py tests/test_fetch_opendota.py` → **25 passed**;
- вместе с деревом заданий 1–3 (`test_link_opendota`, `test_grid_starts`, `test_prices_history`,
  `test_build_universe`, `test_window_ids`) → **92 passed**;
- `uv run ruff format` / `uv run ruff check` по всем шести измененным файлам — чисто;
- `uv run python -m basedpyright` (whole-project, как в hook) — **0 errors, 0 warnings, 0 notes**.

Сравнение данных: строки индекса, собранные `scan_cache` из реального локального кеша
(`data/raw/stratz_matches`, 19144 файла) на первых 200 линкованных match ID, до и после изменений
идентичны (`asdict`-дампы, `==` → True, 200/200 с кешем). Полный проход по 19144 gzip-архивам с
`playbackData` не выполнялся — дорого, а `summarize_match`/`read_cache_summary`/`scan_cache`/
`write_index` в этом задании не менялись ни на строку. `RICH_QUERY`, профиль кеша и `response_bytes`
сверены с исходной версией — без изменений.

### Новые тесты

`tests/test_fetch_stratz.py` (mocked `stratz_graphql`, cache dir и index path в `tmp_path`,
`stratz_client` подменен на `nullcontext(Mock())`):

- `test_usable_and_unusable_matches_are_both_indexed` — usable и `match=None` оба попадают в
  summaries и на диск; unusable сохраняет `reason`;
- `test_one_failing_match_does_not_stop_the_run` — `StratzError` на первом матче: печатается
  `status=error`, файл не создан, второй матч обработан;
- `test_forbidden_aborts_the_whole_run` — 403 → `SystemExit`, второй матч не запрашивался;
- `test_server_errors_retry_then_give_up` — ровно `SERVER_ERROR_MAX_RETRIES + 1` попытки, матч не
  попал в индекс;
- `test_rate_limit_retries_once_then_gives_up` — ровно `RATE_LIMIT_MAX_RETRIES + 1` попытки;
- `test_empty_queue_rebuilds_the_index_without_a_client` — `main()` на пустом списке ID пишет индекс,
  а подменный `stratz_client` бросает `AssertionError` при вызове (то есть не вызывается);
- прежние тесты `select_pending_match_ids` (force/limit) и `start_to_start_sleep` («медленный
  запрос») сохранены.

`tests/test_stratz_client.py`: добавлены `test_transport_statuses_map_to_their_error_classes`
(403/503/418) и `test_retry_after_is_parsed_and_tolerates_garbage` (`"12"` → 12.0, `"soon"` → None,
отсутствующий заголовок → None). Существующий transport-error тест обновлен под обязательные
`variables`.

`tests/test_fetch_opendota.py` (новый; mocked `get_json`, cache path в `tmp_path`):

- `test_only_missing_files_are_requested` — существующий файл не перезапрашивается, новый кеш
  содержит полный ответ в `data`;
- `test_budget_zero_requests_nothing` — 0 запросов, каталог не создан;
- `test_one_failing_match_does_not_stop_the_run` — ошибка второго из трех: остальные сохранены,
  напечатано `failed OpenDota match 2: …`, счетчики `saved_this_run: 2` / `failed_this_run: 1`;
- `test_parallel_mode_saves_every_match` — `workers=4`: тот же worklist, ошибка отнесена к своему ID;
- `test_failed_write_leaves_no_cache_file` — сбой внутри атомарной записи (несериализуемый payload)
  не оставляет итогового файла.

## Что осталось непроверенным и почему

- Сетевые пути не запускались вовсе (запрет задания): реальные 403/429/5xx STRATZ, реальный
  OpenDota, `--force`, `--limit`, параллельный режим против живого API.
- Полный `make test` не запускался (запрет задания); индекс `stratz_match_index.parquet` и
  OpenDota-кеши не перезаписывались.
- Равенство индекса подтверждено на выборке 200 из 3086 линкованных ID, а не на всем корпусе.
- Реальный start-to-start interval во времени не измерялся: `time.sleep` в тестах подменен,
  проверена только чистая функция `start_to_start_sleep` и порядок вызовов.
- `stratz_client` с новым обязательным timeout ни разу не создавал реального `httpx.Client` с
  токеном (в тестах он подменен); проверено только то, что единственный вызов передает `timeout=90`
  и что пустая очередь его не вызывает.

## Какие производные таблицы нужно пересобрать

Ничего дополнительно к предыдущим заданиям. Схема `stratz_match_index.parquet` не изменилась,
`s06_publish_catalog` читает те же колонки. Обязательная пересборка
`new_processed/universe/universe.parquet` из raw Gamma cache (задание 2) остается в силе, а
`grid_game_windows.parquet` желательно пересобрать (задание 3) — оба пункта этим заданием не
затронуты. Сырые кеши STRATZ и OpenDota не трогались.

## Замечания следующему шагу (07)

- `s06_publish_catalog` читает из индекса `match_id`, `status`, `reason`, `start_time`, `duration`,
  `playback_available` — эти колонки не менялись, шаг 07 может опираться на прежнюю схему.
- `write_index` возвращает число usable-строк и это единственный источник итоговой сводки
  загрузчика; не восстанавливать счетчики цикла.
- Шаг 09 закрыт полностью, шаг 05 больше не требует ничего от клиента. Оставшийся `MatchFetch`
  (`match` + `errors`) нужен, чтобы не возвращать анонимный tuple, — не удалять.
- Шаг 12: `common/timestamps.py` этим заданием не затрагивался.
- Шаг 13: в `s05b` осталось ~105 строк `RICH_QUERY` — не считать их целью сокращения при снятии
  итоговых метрик.
