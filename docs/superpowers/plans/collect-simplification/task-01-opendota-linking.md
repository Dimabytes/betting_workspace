# Задание 1 — связывание OpenDota (шаги 01, 08, 10)

Исходная ревизия: `efe54ac` (`main`). Рабочее дерево до начала: чистое, кроме неотслеживаемого
`betting_workspace/docs/superpowers/plans/2026-09-09-collect-simplification.md`. Чужих изменений в
`esports-trader` не было.

Измененные файлы (все в `esports-trader`, не закоммичены):

- `src/collect/s02_link_opendota.py`
- `src/collect/common/opendota_candidates.py`
- `src/collect/common/window_ids.py`
- `tests/test_link_opendota.py`
- `tests/test_window_ids.py` (новый)

## Общие метрики (через `ast`)

| Файл | Строки до | Строки после | `ast.If` до/после | `ast.IfExp` до/после | comprehension-if до/после | доп. операнды `BoolOp` до/после |
|---|---:|---:|---:|---:|---:|---:|
| `s02_link_opendota.py` | 601 | 491 | 42 / 36 | 4 / 4 | 5 / 4 | 11 / 5 |
| `common/opendota_candidates.py` | 178 | 181 | 14 / 16 | 3 / 1 | 0 / 0 | 8 / 5 |
| `common/window_ids.py` | 12 | 12 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| **Итого production** | **791** | **684** | **56 / 52** | **7 / 5** | **5 / 4** | **19 / 10** |

Новых production-модулей не появилось, ничего не вынесено в отдельные файлы. Рост `If` в
`opendota_candidates.py` на 2 — это сознательная замена двух молчаливых `IfExp`-заглушек на два
явных `raise` (шаг 08, пункт 3); суммарное число условных конструкций там не выросло
(14+3 = 17 до, 16+1 = 17 после), а составных `and/or` стало на 3 меньше.

`opendota_candidates.py` вырос на 3 строки: `series_id` сохранен в сборе (см. «Поправка»), а два
молчаливых `IfExp` заменены явными `raise`.

Тесты: `tests/test_link_opendota.py` 639 → 740 строк (часть прироста — фикстура задания 2) (добавлено 6 targeted-тестов, удалены проверки
счетчиков), новый `tests/test_window_ids.py` — 49 строк.

## Шаг 01 — `s02_link_opendota.py`

**Что удалено**

- `SeriesBuildResult` целиком и счетчики `incomplete_candidates`, `rejected_short_series`,
  `invalid_format_maps`, `unresolved_maps`, `raw_id_split_series`, `raw_id_merged_series`.
  `build_matchup_series` возвращает `list[OpenDotaSeries]`, `build_open_dota_series` —
  `tuple[OpenDotaSeries, ...]`. Само отбрасывание коротких/неполных/некорректных серий сохранено:
  состояние просто сбрасывается без учета.
- Вся цепочка raw series ID: поле `OpenDotaMap.raw_series_id`, поле `OpenDotaSeries.raw_series_ids`,
  нормализация `raw_series_id <= 0 -> None` в `convert_candidate_row`, сбор `frozenset` при
  закрытии серии и обход `raw_id_owners` в `build_open_dota_series`. Она обслуживала только
  удаленные счетчики.
- Промежуточное состояние `current_best_of` (формат берется из `current_maps[0].best_of`) и
  `current_has_short_map` (короткие карты проверяются один раз при закрытии серии через `any`).
  Из четырех мест сброса состояния осталось два поля вместо четырех.
- В `build_event_context` — `RuntimeError` о «потерянных обязательных полях»; в
  `load_event_contexts` — фильтр `team_a is None or team_b is None or scheduled_ts is None`.
- В `validate_links` — `has_unique_sorted_game_numbers` и вызывающая проверка.
- Из `main` — шесть строк печати удаленных счетчиков.

**Гарантии, позволившие удалить условия**

- `parse_status` в `s01_build_universe.py` (`parse_status(team_a, team_b, bo, start_ts)`) равен
  `"ok"` только когда `team_a` и `team_b` непустые строки, `bo` и `scheduled_ts` не `None`. Фильтр
  `parse_status != "ok"` в `load_event_contexts` уже стоит, поэтому повторные проверки были мертвы.
  Вместо runtime-assert оставлено сужение типов через `cast` в единственной точке конструирования
  `EventContext`.
- `best_of` дополнительно сужается фильтром `contract["best_of"] not in SUPPORTED_BEST_OF`
  (`None` в это множество не входит), поэтому `cast(BestOf, ...)` корректен.
- Уникальность пары `event_id/game_number` проверяется в `validate_links` выше по коду, а группа
  сортируется по `game_number` перед проверкой, поэтому `game_numbers == sorted(set(game_numbers))`
  всегда истинно. Проверка хронологии карт и уникальность `match_id`/`map_condition_id` сохранены.

**Fallback series-winner**

`build_event_context` теперь сам кладет series-winner в `map_condition_ids` под номером решающей
карты (`series_winner_covers_map(best_of, best_of, map_winner_exists=best_of in map_condition_ids)`),
а `build_links` только делает `map_condition_ids.get(game_number)`. Порядок сохранен:

- проверка наличия настоящих map-кандидатов (`if not map_condition_ids: return None`) выполняется
  **до** добавления fallback, поэтому события только с series-winner по-прежнему исключаются;
- явный Game N Winner приоритетнее (`map_winner_exists` = наличие настоящего рынка на решающей
  карте, ключ не перетирается);
- BO2 fallback не получает (`DECIDER_BEST_OF = {1, 3, 5}`);
- при sweep непройденная карта не публикуется, потому что `build_links` идет по фактически
  сыгранным картам.

`EventContext.series_condition_id` удалено, `build_event_context` возвращает `EventContext | None`,
а `load_event_contexts` вместо отдельного финального фильтра отбрасывает `None`.

## Шаг 08 — `common/opendota_candidates.py`

- `series_id` **сохранен** в SQL-запросе, `OpenDotaCandidateRow`, проверке входных строк и
  записываемых страницах — поле архивируется на будущее, хотя связывание его не читает
  (см. «Поправка» ниже). Сбор полей не сокращен.
- Ранее записанные страницы читаются без миграции: `load_opendota_candidate_rows` выбирает нужные
  ключи и не валидирует множество полей; строгая валидация применяется только к свежим сетевым
  строкам. Покрыто тестом `test_previously_cached_candidate_pages_still_load`.
- `not isinstance(value, int) or isinstance(value, bool)` заменено на `type(value) is not int` для
  `match_id`, `start_time`, `leagueid`, `radiant_team_id`, `dire_team_id`, `duration`, `series_id`,
  `series_type`.
  Положительность ID и допустимость `duration == 0` сохранены.
- `explorer_sql` больше не превращает некорректный payload или отсутствующий `rows` в пустой список:
  не-объект и не-список дают `RuntimeError`. Настоящий пустой `rows` остается валидным концом
  выборки (курсор завершится штатно).
- Не тронуты: проверка схемы сетевых строк, границы `start/end` при чтении, дедупликация по
  `match_id`, продвижение курсора с ошибкой на непродвижении, пауза между запросами, staging
  directory, поиск start boundary и SQL-временные ограничения.

Строка схемы `OPENDOTA_CANDIDATE_PAGE_SCHEMA` оставлена как `..._v3`: значение нигде не читается и
не валидируется, бумажный bump потребовал бы миграции существующих страниц.

## Шаг 10 — `common/window_ids.py`

- Удалены `dropna()`, `unique()` и `na_position="last"`.
- Основание: `s02` публикует `match_id` только из `OpenDotaLinkRow` (всегда int), а `validate_links`
  падает при `links["match_id"].duplicated().any()` и при пустом результате; `match_start_time`
  берется из того же обязательного поля. Фильтрация по `condition_id` строк не добавляет.
- Сохранены пересечение с GRID windows, порядок `match_start_time`, затем `match_id`, и приведение
  к `int64`.

## Изменение выходных схем и поведения на некорректных входах

- Parquet-схемы **не изменились**: `opendota_links.parquet` (9 колонок) и
  `opendota_link_audit.parquet` (7 колонок) идентичны прежним. Ни одна колонка не удалена, `data/views.sql`
  трогать не нужно.
- Схема сырых JSON-страниц кандидатов **не изменилась**: 11 полей, включая `series_id`.
- Изменена диагностика stdout `s02`: удалены шесть строк со счетчиками
  (`incomplete candidates`, `short-map series rejected`, `invalid-format maps`, `unresolved maps`,
  `built series spanning raw ids`, `raw ids spanning built series`). Остались `candidate maps`,
  `complete series`, `accepted links`, `audit statuses` и обе строки `saved:`.
- Некорректный ответ OpenDota explorer теперь приводит к явной ошибке вместо тихого пустого списка.
  Проверено тестом, что при этом старый каталог страниц остается нетронутым (staging directory не
  подменяется).

## Проверки

Целевые тесты (из корня `esports-trader`):

```sh
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -n 0 \
  tests/test_link_opendota.py tests/test_window_ids.py tests/test_fetch_stratz.py \
  tests/test_match_catalog.py tests/test_grid_starts.py tests/test_prices_history.py
```

Результат: **46 passed**. (`tests/test_link_opendota.py` + `tests/test_window_ids.py` +
`tests/test_fetch_stratz.py` отдельно: 25 passed.)

Изменения в тестах:

- `build_map` потерял аргумент `raw_series_id`; сценарий «игнорирования raw series ID» сохранен и
  переименован в `test_series_builder_splits_one_matchup_by_start_gap` — те же реальные данные
  (один matchup, два BO3 с большим разрывом), проверка сборки по фактическим признакам, без
  счетчиков.
- `test_series_builder_closes_all_supported_formats` вместо `unresolved_maps == 0` проверяет, что
  все 11 карт вошли в собранные серии.
- `test_series_builder_rejects_short_invalid_incomplete_and_large_gap` проверяет пустой результат.
- `test_load_event_contexts_keeps_only_required_fields` проверяет fallback прямо в
  `map_condition_ids` (`bo3 -> {1: map-1, 2: map-2, 3: series-3}`).
- Новые targeted-тесты: bool вместо int, отсутствующее поле, некорректный explorer-ответ, настоящий
  пустой `rows`, отсутствие подмены каталога при ошибке refresh, сохранение `series_id` в новой
  raw-странице (`test_new_candidate_pages_archive_series_id`), чтение ранее закешированной страницы.
- `tests/test_window_ids.py`: порядок при совпадающих `match_start_time` и пустое пересечение.

Сравнение данных на реальном локальном корпусе (145 страниц кандидатов, universe 70427 строк,
без публикации, только в памяти/во временный каталог):

- `maps=28788 series=12113 contexts=2565 links=4737 audit=2565` — идентично до и после.
- `links.parquet` и `audit.parquet` (4737×9 и 2565×7) сравнены через `DataFrame.equals`: **True**.
- Список `load_grid_window_match_ids()` на рабочих таблицах — побайтово идентичен.

Форматирование и статический анализ: `uv run ruff check` / `ruff format` по измененным файлам —
чисто; `uv run python -m basedpyright` (whole-project, как в hook) — **0 errors**.

Сетевой сбор, `make collect-dry-run` и запись рабочих parquet не запускались.

## Что осталось непроверенным

- Сетевой путь `refresh_opendota_candidate_pages` против реального OpenDota (новый SQL без
  `series_id`, новая ошибка explorer) — проверен только на mocked HTTP.
- Полный `make test` по правилам плана не запускался; прогонялись целевые модули + соседние
  collect-тесты (`match_catalog`, `grid_starts`, `prices_history`).
- Гарантия «`parse_status == ok` ⇒ команды/BO/время заполнены» проверена по коду производителя
  `s01_build_universe.py`, а не по всему историческому universe.

## Какие производные таблицы нужно пересобрать

Ни одной. Схемы `opendota_links.parquet` и `opendota_link_audit.parquet` не изменились, содержимое
совпадает с эталоном; сырые страницы кандидатов остаются валидными и по-прежнему содержат
`series_id`. Refetch не требуется и не запускался.

## Замечания следующему заданию

- `EventContext` больше не хранит `series_condition_id`; любой шаг, желающий видеть series-winner
  отдельно, должен смотреть на `map_condition_ids[best_of]`.
- `build_open_dota_series` возвращает кортеж серий, а не объект: не восстанавливать `.series`.
- В `catalog_types.py` (шаг 11) по этому заданию удалять нечего: изменения затронули только
  локальные dataclass-ы `s02` и TypedDict кандидатов.
- Шаг 02 удаляет поля universe; `load_event_contexts` сейчас читает `parse_status`,
  `inventory_status`, `best_of`, `event_id`, `event_title`, `team_a`, `team_b`, `scheduled_ts`,
  `game_number`, `conditionId`, `contract_kind` — этот набор нужно сохранить.
- Эталоны для финальной сверки лежат в scratchpad сессии
  (`.../scratchpad/before/{links,audit}.parquet`, `window_ids.txt`) и не переживут сессию: при
  необходимости шаг 13 пересоберет их из read-only checkout `efe54ac`.

## Поправка (после обновления плана)

Пользователь добавил в шапку плана правило: сырые данные собираются с запасом, полный состав
запросов и архивируемых полей сохраняется, даже если поле сейчас никто не читает; упрощаются только
обработка, внутренние объекты и производные таблицы. Шаги 01 (пункт 6) и 08 (пункт 1) переписаны.

Что сделано по поправке в `src/collect/common/opendota_candidates.py`:

- `series_id` возвращен в `build_opendota_candidate_query` (`m.series_id`), в `OpenDotaCandidateRow`,
  в проверку `validate_opendota_candidate_row` (общий цикл `("series_id", "series_type")` с точной
  проверкой типа) и, следовательно, в записываемые страницы: `OPENDOTA_CANDIDATE_COLUMNS` строится
  из `__annotations__`.
- Обработка `series_id` в связывании **не возвращена**: `OpenDotaMap.raw_series_id`,
  `OpenDotaSeries.raw_series_ids`, нормализация неположительного ID, сбор множеств, обход владельцев
  и счетчики `raw_id_split_series`/`raw_id_merged_series` остаются удаленными. `convert_candidate_row`
  просто не читает это поле.
- Тесты: во всех inline-строках кандидатов вернулось поле `series_id`; добавлен
  `test_new_candidate_pages_archive_series_id` (mocked explorer, запись страницы во временный
  каталог, проверка `rows[0]["series_id"] == 99` и наличия `m.series_id` в SQL); тест чтения ранее
  закешированных страниц сохранен под именем `test_previously_cached_candidate_pages_still_load`.
- Refetch и перезапись сырых архивов не выполнялись; страниц, записанных временно сокращенным
  сборщиком, не существует — сокращенная версия сборщика в сеть не ходила.

Проверки после поправки: `tests/test_link_opendota.py` + `tests/test_window_ids.py` +
`tests/test_fetch_stratz.py` — 26 passed; вместе с `test_match_catalog`, `test_grid_starts`,
`test_prices_history`, `test_build_universe` (файлы задания 2, лежащие в том же дереве) — 75 passed;
`ruff check`/`ruff format` по измененным файлам чисто, `basedpyright` — 0 errors. Повторное
сравнение с эталоном на реальном корпусе: `links` и `audit` по-прежнему `equals == True`
(`maps=28788 series=12113 contexts=2565 links=4737 audit=2565`).

Незакоммиченные изменения задания 2 (`s01_build_universe.py`, `s05a_fetch_prices_history.py`,
`common/catalog_types.py`, `tests/test_build_universe.py`, `tests/test_prices_history.py`, фикстура
в `tests/test_link_opendota.py`, `docs/next_steps.md`) не трогались.
