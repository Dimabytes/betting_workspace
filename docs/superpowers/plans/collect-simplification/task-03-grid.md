# Задание 3 — GRID (шаг 03 + GRID-часть шага 11)

Исходная ревизия: `efe54ac` (`main`). Рабочее дерево до начала: незакоммиченные изменения заданий 1 и 2
(`docs/next_steps.md`, `src/collect/common/catalog_types.py`, `src/collect/common/opendota_candidates.py`,
`src/collect/common/window_ids.py`, `src/collect/s01_build_universe.py`, `src/collect/s02_link_opendota.py`,
`src/collect/s05a_fetch_prices_history.py`, `tests/test_link_opendota.py`, `tests/test_prices_history.py`,
новые `tests/test_build_universe.py`, `tests/test_window_ids.py`) — не тронуты.
`src/collect/s04_fetch_grid_starts.py` до начала был в исходном состоянии.

Измененные файлы (все в `esports-trader`, не закоммичены):

- `src/collect/s04_fetch_grid_starts.py`
- `src/collect/common/catalog_types.py` (только `GridGameWindowRow`)
- `tests/test_grid_starts.py`
- `tests/test_match_catalog.py`

## Что удалено и почему это упрощает основной сценарий

- `GridGameWindowRow.grid_duration_seconds` — производная колонка окна. Каталог использует STRATZ
  duration (`s06` читает из GRID только `condition_id` и `spawn_at`), GRID duration не участвовал ни в
  одном вычислении. Вместе с колонкой ушли: разбор `iso_duration_seconds(game["duration"])` при
  загрузке игр, поле `GridGame.duration_seconds`, `raise` «has no parseable duration» в `build_window` и
  подмена duration через `float(link.grid_clock_seconds)` в live/paper-ветке.
- `build_window` и `build_live_paper_window` — после удаления duration оба сводились к двухполевому
  словарю; тела перенесены в `resolve_window`, цепочка вызовов стала на два уровня короче.
  `resolve_window` по-прежнему сначала пробует GRID, потом live/paper.
- `GridGame.series_id` — не читался никем, кроме конструктора (проверено `rg` по `src`, `scripts`,
  `tests`, `data/views.sql`). Вместе с ним ушло обращение к `state["id"]` в `load_grid_games`.
- В `load_grid_games` двухступенчатая распаковка `data`/`seriesState` (`if`-выражение + `if`) заменена на
  один `payload.get("data", {}).get("seriesState")` и одну проверку `is None`. Сама проверка **сохранена**
  (см. ниже про старые кеши), `seriesState` в `GridSeriesStateData` помечен `NotRequired`, потому что
  такие файлы реально существуют.

## Что сохранено полностью

- **`ALL_SERIES_QUERY` и `SERIES_STATE_QUERY` не изменены ни на символ** (`git diff` по файлу не содержит
  ни одной строки внутри запросов): команды, турнир, формат, состояния серии, счет, `won`, `kills`,
  duration серии и игры, `clock`, `paused`, `sequenceNumber`.
- Типы, описывающие сырые ответы, не сокращены: `GridSeriesNode`, `GridSeriesFormat`, `GridTournament`,
  `GridTeamBaseInfo`, `GridSeriesTeamRef`, `GridSeriesTeam`, `GridGameTeam`, `GridGameNode`
  (включая `duration`, `score`, `kills`, `won`), `GridSeriesState`, `GridClock`. Единственная правка типов
  raw-данных — `seriesState: NotRequired[GridSeriesState | None]`, что уточняет реальный формат кешей,
  а не сокращает набор полей.
- `write_json(out, payload)` пишет ответ целиком; фильтрации перед записью нет и не появилось.
- `iso_duration_seconds` оставлен — он нужен для `rateLimitResetsIn` в `grid_graphql`.
- Точное совпадение clock, окно `MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS`, ошибка «multiple exact GRID
  games», приоритет GRID над live/paper, fallback на ранний live/paper tick (`SPAWN_GAME_STATE`,
  `SPAWN_GAME_TIME_MAX`, `HORN_OFFSET_SECONDS`), `--fetch`/`--force`, `pending_state_ids`,
  `GRID_REQUEST_SLEEP_SECONDS`, обработка 429 и retry — без изменений.

## Какие гарантии позволили удалить условия

- Удаление `raise ... has no parseable duration` обосновано не гарантией входа, а удалением потребителя:
  значение больше не попадает ни в одну колонку. Это единственное изменение поведения на некорректном
  входе (см. ниже).
- Проверку наличия `seriesState` **удалять было нельзя**: в локальном кеше
  `data/raw/polymarket_dota/grid_game_starts/series_state` 35 файлов из 2487 содержат ровно `{"data": {}}`
  (и 1 из 61 в `data/archive/2026-07-28/deleted_tree/...`). Их записал более старый writer; текущий
  `fetch_series_states` такие ответы пропускает. Проверка сохранена, поведение «пропустить файл» — тоже.
- Пропуск игры без пригодного `startedAt` сохранен как есть (`spawn_at is None or spawn_ts is None`).
- Пропуск отсутствующего файла state сохранен.

## Метрики до/после (через `ast`, база — `efe54ac`)

| Файл | Строки до/после | `ast.If` | `ast.IfExp` | comprehension-if | доп. операнды `BoolOp` |
|---|---:|---:|---:|---:|---:|
| `s04_fetch_grid_starts.py` | 554 → 535 | 24 → 23 | 6 → 5 | 2 → 2 | 11 → 11 |
| `common/catalog_types.py` | 93 → 72 (в этом задании 73 → 72) | 0 | 0 | 0 | 0 |
| **Итого production** | **647 → 607** | **24 → 23** | **6 → 5** | **2 → 2** | **11 → 11** |

Экономия строк небольшая и намеренно: примерно 300 строк файла — это GraphQL-запросы и TypedDict сырых
ответов, которые по правилу «сырые данные с запасом» сохранены целиком. Убрано: 2 функции
(`build_window`, `build_live_paper_window`), 2 поля внутреннего `GridGame` (`series_id`,
`duration_seconds`), 1 колонка производной таблицы (`grid_duration_seconds`), 1 `raise` на некорректный
duration, 1 повторный разбор ISO-duration на каждую игру.

Тесты: `tests/test_grid_starts.py` 212 → 302 строк (+2 теста), `tests/test_match_catalog.py` 358 → 278
(удален вырожденный тест, см. ниже).

## Изменение выходных схем и поведения на некорректных входах

- `new_processed/grid_game_starts/grid_game_windows.parquet`: 3 → 2 колонки, удалена
  `grid_duration_seconds`. View `grid_game_windows` в `data/views.sql` использует `SELECT *`, поэтому
  пользовательская схема сужается; сам `views.sql` менять не нужно. Потребители в коде читают только
  `condition_id`/`spawn_at` (`s05a.load_anchors`, `s06.build_match_catalog_frame`), поэтому и старый
  3-колоночный parquet, и новый 2-колоночный они читают одинаково.
- `match_catalog.parquet` — схема не изменилась (GRID duration в каталог никогда не попадал).
- Сырые кеши (`series_index/*.json`, `series_state/*.json`) не переписаны и остаются валидными; формат
  записи не изменился.
- Явное изменение поведения: GRID-игра с непарсящимся `duration` больше не приводит к `ValueError` при
  построении окна — поле удалено из результата. Само значение продолжает запрашиваться и сохраняться в
  raw-кеше.

## Проверки: команды, результаты, сравнение данных

```sh
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -n 0 \
  tests/test_grid_starts.py tests/test_match_catalog.py
```

Результат: **18 passed**. Расширенный прогон соседних collect-тестов
(`+ test_prices_history.py test_link_opendota.py test_build_universe.py test_window_ids.py`):
**73 passed**.

Новые тесты в `tests/test_grid_starts.py`:

- `test_series_state_cache_keeps_fields_unused_by_the_window` — mocked ответ `grid_graphql` с полным
  `seriesState`; проверяет, что записанный файл побитово равен ответу и что в нем остались поля, не
  участвующие в построении окна: `duration` серии (`PT50M`), `duration` игры (`PT36M54S`), счет серии
  (`[2, 1]`), имена команд игры. Дальше тот же кеш читается `load_grid_games` — окно строится по
  `clock.currentSeconds` и `startedAt`.
- `test_state_cache_without_series_state_is_skipped` — старый кеш `{"data": {}}` читается без исключения и
  пропускается (регрессия на 35 реальных файлов).

Удален `test_catalog_keeps_maps_when_grid_duration_disagrees`: он проверял, что каталог не смотрит на
`grid_duration_seconds`; после удаления колонки два его входных ряда стали идентичными, а утверждение —
вырожденным. Утверждение `assert "grid_duration_seconds" not in frame.columns` в
`test_catalog_keeps_complete_maps_and_drops_the_rest` тоже удалено по той же причине.

Сравнение на реальном локальном кеше (без публикации): пересборка окон из
`series_index`/`series_state` новым кодом дала **3086 строк, 4192 GRID-игры, все 3086 попаданий по
источнику `grid`**; `condition_id` и `spawn_at` совпадают с рабочим
`grid_game_windows.parquet` построчно и по порядку (`DataFrame.equals` = True на двух общих колонках).

Форматирование и статический анализ: `uv run ruff format` / `ruff check` по измененным файлам — чисто;
`uv run python -m basedpyright` (whole-project, как в hook) — **0 errors, 0 warnings**.

Сетевой сбор, `make collect-dry-run`, `--fetch` и запись рабочих parquet не запускались.

## Что осталось непроверенным

- Полный `make test` по правилам плана не запускался.
- Сетевые пути `--fetch` (индекс и state) не проверялись против реального GRID API; `grid_graphql`,
  пагинация и rate-limit не менялись, mocked-проверка покрывает только запись state-кеша.
- Ветка live/paper проверена только синтетическими `state.jsonl` (в локальном наборе GRID покрывает все
  3086 линков, поэтому реального live/paper-совпадения на данных нет).
- Гарантия «в state-кеше всегда есть `data.seriesState`» **не** установлена: 36 файлов ее нарушают, поэтому
  проверка оставлена.

## Какие производные таблицы нужно пересобрать

- `new_processed/grid_game_starts/grid_game_windows.parquet` — пересобрать при удобном случае, чтобы файл
  соответствовал новой схеме (`s04_fetch_grid_starts` без `--fetch`, только из локальных кешей). Это
  **не блокирует** обычный запуск: `s05a` и `s06` читают из него только `condition_id`/`spawn_at` и
  работают со старым 3-колоночным файлом без изменений.
- Обязательная пересборка из задания 2 остается в силе: `universe.parquet` перед первым запуском
  `s05a`/`s06`.
- Сырые GRID-кеши не трогались.

## Замечания следующему заданию

- Шаг 11 (`catalog_types.py`) по GRID закрыт: `GridGameWindowRow` = `condition_id`, `spawn_at`.
  Остальные типы GRID в `s04` — описание сырых ответов, их сокращать нельзя.
- `GridSeriesStateData.seriesState` теперь `NotRequired`: это факт о старых кешах, не косметика.
- В `s04` осталось ~300 строк запросов и raw-TypedDict — не считать их целью сокращения в шаге 13 при
  снятии итоговых метрик.
- Шагу 04 (`s05a`) ничего не требуется: `load_anchors` читает `spawn_at` и уже совместим.
