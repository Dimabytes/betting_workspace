# Задание 5 — каталог и итоговая сверка (шаги 07, 12, 13)

Исходная ревизия: `efe54ac` (`main`, HEAD не двигался).
Исходное состояние рабочего дерева: незакоммиченные изменения заданий 1–4
(11 файлов `src/collect`, 6 изменённых тестов, 3 новых теста) плюс `docs/next_steps.md`.
Чужих изменений нет, ничего не откатывалось, коммитов не делалось.

Измененные этим заданием файлы:

- `src/collect/s06_publish_catalog.py`
- `tests/test_match_catalog.py`
- `src/collect/common/timestamps.py` — без изменений (обосновано ниже)
- `src/collect/common/paths.py` — без изменений (обосновано ниже)

---

## Шаг 07 — `s06_publish_catalog.py`

### Что удалено и почему это упрощает основной сценарий

1. Из `keep_complete` убраны предикаты `duration` и `grid`. Оба дублировали `ended_at`:
   `stamp_ended_at` кладёт `None`, если отсутствует хотя бы одно из `pauses`, `spawn_at`,
   `duration`, поэтому `ended_at.notna()` уже является пересечением всех трёх условий.
   Осталось 4 именованных проверки вместо 6.
2. Маска полноты Gamma выражена одним списком колонок `GAMMA_COLUMNS` и
   `.notna().all(axis=1)` вместо цепочки из пяти `&`. Тот же список теперь задаёт и выборку
   `universe_cols`, так что перечень обязательных Gamma-колонок существует в файле в одном месте
   (раньше он был продублирован в join-шаге и в маске).
3. `publish_match_catalog` больше не возвращает константу `MATCH_CATALOG_PATH`: единственный
   потребитель (`main`) использовал её только для лога и импортирует ту же константу сам.

Что сохранено дословно: `stratz_status == "usable"`, наличие prior, наличие Gamma, требование
`pauses` для вычисления `ended_at`, отсутствие требования playback, последовательные именованные
left-join-шаги, порядок выходных колонок `MATCH_CATALOG_COLUMNS`, `assert` на уникальность
`match_id`, лог отброшенных match ID.

### Какие гарантии позволили удалить условия

Единственная гарантия — производитель `ended_at` в том же файле: `stamp_ended_at` строит колонку
только когда `pauses is not None and not isna(spawn_at) and not isna(duration)`. Это локальная
проверяемая гарантия, а не предположение о внешнем API. Она закреплена новым параметризованным
тестом `test_ended_at_check_covers_duration_grid_and_pauses`, который по очереди убирает
`duration`, GRID-`spawn_at` и `pauses` у полностью валидной строки и требует пустой каталог.

### Метрики до/после (`ast`, база `efe54ac`)

| Файл | Строки | `ast.If` | `ast.IfExp` | comp-if | доп. операнды `BoolOp` |
|---|---:|---:|---:|---:|---:|
| `s06_publish_catalog.py` | 131 → 122 | 2 → 2 | 0 → 0 | 0 → 0 | 2 → 2 |

Счётчики `ast` здесь почти не двигаются, и это ожидаемо: удалённые предикаты были не `if`, а
элементами словаря масок и операторами `&` (`ast.BinOp`, не `BoolOp`). Содержательная метрика шага
другая: масок полноты 6 → 4, термов внутри маски Gamma 5 `&` → 1 выражение, дублирующихся списков
Gamma-колонок 2 → 1, возвращаемых значений `publish_match_catalog` 1 → 0. Diff: `+15 −24`.

Затронутые production-файлы суммарно: 131 → 122 (только `s06`).
Тест: `tests/test_match_catalog.py` 278 → 292 строки (снят `-9` за счёт удалённого дублирующего
лог-ожидания, `+23` за новый параметризованный тест).

### Изменение выходных схем и поведения

Схема `match_catalog.parquet` не изменилась. Изменился только лог: строки
`catalog duration failed=...` и `catalog grid failed=...` больше не печатаются (это те самые
дублирующие счётчики, удаление которых разрешено пунктом «Проверки» шага 07). `catalog kept=…
dropped=… match_ids=[…]` сохранён полностью. Поведение на некорректных входах не изменилось:
строка без duration/GRID/pauses по-прежнему отбрасывается, просто по одной причине `ended_at`
вместо трёх.

---

## Шаг 12 — `common/timestamps.py`

**Нулевой diff, обоснование.**

Все вызовы `require_ts` найдены и все находятся на границах ввода:

| Вызов | Граница |
|---|---|
| `s01_build_universe.py:348` `require_ts(as_of, "--as-of")` | CLI |
| `s01_build_universe.py:359` `require_ts(start_date, "--start-date")` | CLI |
| `s02_link_opendota.py:466` `require_ts(read_json(UNIVERSE_MANIFEST_PATH)["data_as_of"], …)` | manifest |
| `s04_fetch_grid_starts.py:355` `require_ts(node["startTimeScheduled"], …)` | GRID schedule |
| `s05a_fetch_prices_history.py:63` `require_ts(window["spawn_at"], "spawn_at")` | parquet GRID-окон |

Пять потребителей в четырёх модулях, ни один не является внутренним состоянием пайплайна. Модуль
жив, его единственное условие даёт понятную ошибку вместо `None`, копирование этой проверки в пять
мест сделало бы код длиннее. Условие для удаления из пункта 3 шага («не осталось ни одного
потребителя») не выполнено. Изменений нет, новые тесты по правилу шага не требуются.

---

## Шаг 13 — `common/paths.py` и итоговая сверка

### `paths.py`: нулевой diff

Проверено использование каждой из 15 констант в `src`, `scripts`, `tests`, `data/views.sql`:

| Константа | Потребителей |
|---|---:|
| `RAW_POLYMARKET_DOTA_PRICES_HISTORY_DIR` | 3 |
| `RAW_UNIVERSE_DIR` | 2 |
| `RAW_OPENDOTA_CANDIDATE_PAGES_DIR` | 3 |
| `UNIVERSE_DIR` | 2 |
| `UNIVERSE_PATH` | 10 |
| `UNIVERSE_MANIFEST_PATH` | 4 |
| `OPENDOTA_LINKS_PATH` | 14 |
| `OPENDOTA_LINK_AUDIT_PATH` | 3 |
| `STRATZ_MATCH_INDEX_PATH` | 8 |
| `GRID_GAME_WINDOWS_PATH` | 12 |
| `PREGAME_QUOTES_PATH` | 6 |
| `OPENDOTA_LINKS_DIR` | 0 |
| `NEW_STRATZ_MATCH_INDEX_DIR` | 0 |
| `GRID_GAME_WINDOWS_DIR` | 0 |
| `PREGAME_QUOTES_DIR` | 0 |

Четыре «нулевых» константы — это каталоги, а не артефакты: соответствующие директории продолжают
существовать и содержать публикуемые parquet. Проверено на `efe54ac`: они были без потребителей и
**до** начала плана, то есть это не мусор, оставшийся от шагов 01–12. Пункт 1 шага 13 разрешает
удалять только пути к артефактам, которые перестали существовать; таких нет. `OPENDOTA_LINK_AUDIT_PATH`
и его view сохранены (пункт 2). Diff нулевой, факт четырёх неиспользуемых dir-констант зафиксирован
как отдельная находка вне рамок этого плана.

### Сверка сетевых запросов и raw-writers с `efe54ac`

Сравнение сделано машинно, а не глазами: для восьми файлов, содержащих запросы и запись сырых
данных (`s01`, `s02`, `s04`, `s05`, `s05a`, `s05b`, `common/opendota_candidates.py`,
`common/stratz_client.py`) через `ast` извлечены все строковые литералы длиной > 40 символов и все
строковые модульные константы, затем множества сравнены с `efe54ac`.

Результат: **потерян ровно один литерал** —
`'duplicate or unsorted game numbers for event '` (сообщение удалённой в шаге 01 дублирующей
проверки `validate_links`). Ни один GraphQL-запрос, SQL-запрос, URL, имя поля или ключ ответа не
изменился и не исчез. В частности:

- OpenDota `explorer_sql` посимвольно совпадает и по-прежнему содержит
  `m.radiant_team_id, m.dire_team_id, m.series_id, m.series_type, m.radiant_win, …`;
  `series_id` остаётся в `OpenDotaCandidateRow` (`series_id: int | None`), в проверке схемы строк
  (`for field in ("series_id", "series_type")`) и, следовательно, в сохраняемых страницах.
- GRID `ALL_SERIES_QUERY` и `SERIES_STATE_QUERY` идентичны исходным, включая команды, турнир,
  формат, состояния, счёт и `duration`.
- STRATZ `RICH_QUERY` идентичен.
- Gamma-запросы в `s01` идентичны.

Отдельно сопоставлены все строки с вызовами записи (`write_json`/`write_text`/`json.dump`/`payload`)
в тех же файлах: ни один call-site записи сырого кеша не изменился. Изменились только читатели
(`payload.get("data")` → `payload.get("data", {}).get("seriesState")` в `s04`, снятие `parse_ts` в
`s05a`, снятие молчаливого `{}` в `opendota_candidates`), то есть фильтрации перед записью нигде не
добавилось.

В `stratz_client` два изменения на границе: убран `base_url=""` (ничего не задавал — все вызовы
передают абсолютный URL) и `variables or {}` → `variables` (единственный вызывающий передаёт явный
mapping). Тело запроса не изменилось.

### Обращения к удалённым колонкам

Прогрепаны `src`, `scripts`, `tests`, `data/views.sql` по каждому удалённому имени:
`market_id`, `event_slug`, `title`, `group_item_title`, `sports_market_type`, `scheduled_time`,
`outcomes_json`, `volume`, `active`, `closed`, `market_start_date`, `market_end_date`,
`discovery_sources`, `classification_method`, `exclusion_reason`, `clob_token_ids_json`,
`grid_duration_seconds`, `series_condition_id`.

- `classification_method`, `exclusion_reason`, `grid_duration_seconds`, `market_start_date`,
  `market_end_date`, `discovery_sources`, `title` — 0 вхождений во всём репозитории.
- `market_id`, `event_slug`, `group_item_title`, `scheduled_time`, `outcomes_json`,
  `clob_token_ids_json` — вхождения только в LoL-пайплайне (`src/lol`, `tests/test_lol_*`) и в
  `src/trader/*` (собственные sidecar-поля). Это отдельная колонка с тем же именем, dota-universe
  она не читает.
- `sports_market_type` — остался как имя переменной/параметра внутри `s01` и его теста, не как
  колонка parquet.
- `volume`, `active`, `closed`, `duration_seconds`, `series_condition_id` — совпадения по имени в
  несвязанном коде (`scripts/compare_backtests.py`, `src/trader`, `src/market_data`, локальная
  переменная в `s02`).

`data/views.sql` использует только `SELECT *` — явных списков колонок нет, править нечего;
изменение схем видно пользователю через views `universe` и `grid_game_windows`.

Временных compatibility-флагов не появилось: грепы `legacy|compat|fallback` по `src/collect` дают
единственное совпадение — локальную переменную `incompatible` в `s02`.

### Финальные метрики всех 13 файлов (`ast`, база `efe54ac`)

| Файл | Строки до/после | `if` | `IfExp` | comp-if | доп. операнды `BoolOp` |
|---|---:|---:|---:|---:|---:|
| `s01_build_universe.py` | 476 → 379 | 35 → 32 | 7 → 5 | 1 → 1 | 20 → 19 |
| `s02_link_opendota.py` | 601 → 491 | 42 → 36 | 4 → 4 | 5 → 4 | 11 → 5 |
| `s04_fetch_grid_starts.py` | 554 → 535 | 24 → 23 | 6 → 5 | 2 → 2 | 11 → 11 |
| `s05_fetch_opendota_matches.py` | 139 → 103 | 6 → 3 | 1 → 1 | 1 → 1 | 0 → 0 |
| `s05a_fetch_prices_history.py` | 272 → 241 | 12 → 11 | 0 → 0 | 0 → 0 | 4 → 2 |
| `s05b_fetch_stratz_matches.py` | 453 → 362 | 7 → 5 | 11 → 7 | 2 → 2 | 4 → 4 |
| `s06_publish_catalog.py` | 131 → 122 | 2 → 2 | 0 → 0 | 0 → 0 | 2 → 2 |
| `common/opendota_candidates.py` | 178 → 181 | 14 → 16 | 3 → 1 | 0 → 0 | 8 → 5 |
| `common/stratz_client.py` | 107 → 104 | 6 → 6 | 4 → 3 | 0 → 0 | 1 → 0 |
| `common/window_ids.py` | 12 → 12 | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| `common/catalog_types.py` | 93 → 72 | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| `common/timestamps.py` | 10 → 10 | 1 → 1 | 0 → 0 | 0 → 0 | 0 → 0 |
| `common/paths.py` | 18 → 18 | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| **Итого** | **3044 → 2630** | **149 → 135** | **36 → 26** | **11 → 10** | **61 → 48** |

Все условные конструкции суммарно: 257 → 219 (−15 %). Строки: −414 (−13.6 %).
Новых production-модулей не создано, ни одна строка не перенесена в другой файл.

### Проверки

Полный набор один раз, по общим правилам:

```
make test
→ 3 failed, 1938 passed, 24 warnings in 24.99s
```

Три падения — `tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
(dota 8837869969 / 8911784562 / 8933879286). Причина:
`FileNotFoundError: data/new_processed/market_seconds/va75c29ad/match_id=….parquet` — отсутствующие
локальные backtest-данные, не код. Те же три теста падают на чистом `efe54ac` в read-only
worktree с тем же сообщением. К `src/collect` они не относятся.

Форматирование/lint/типы: `uv run ruff format` (14 файлов без изменений),
`uv run ruff check --fix src/collect tests/` (чисто), `uv run python -m basedpyright`
(0 errors, 0 warnings, 0 notes на весь проект).

### Сравнение данных на одинаковом наборе сырых кешей

Метод: `git worktree add --detach <scratch>/base efe54ac`, каталоги `data/raw`,
`data/new_processed`, `data/grid`, `data/trader` подключены в worktree симлинками (чтение). Обе
ревизии прогнаны одним и тем же драйвером, который подменяет константы путей на временный каталог
до импорта стадий; ни одна стадия не писала в рабочие пути, сеть не задействована
(`--fetch*` везде выключен). STRATZ-индекс не пересобирался — обе стороны читали один и тот же
рабочий `stratz_match_index.parquet` (его схема не менялась).

Прогнано: `s01 --as-of 2026-09-04T21:38:02Z --output-dir <tmp>`, затем `s02`, `s04`, `s05a`,
`s06.publish_match_catalog()` — полная цепочка до каталога.

| Таблица | Строк old → new | Удалённые колонки | Расхождения по общим колонкам |
|---|---|---|---|
| `universe.parquet` | 70427 → 70427 | 16 (см. ниже) | `token_id_0`, `token_id_1`: 342 строки |
| `opendota_links.parquet` | 4737 → 4737 | нет | нет |
| `opendota_link_audit.parquet` | 2565 → 2565 | нет | нет |
| `grid_game_windows.parquet` | 3086 → 3086 | `grid_duration_seconds` | нет |
| `pregame_quotes.parquet` | 3058 → 3058 | нет | нет |
| `match_catalog.parquet` | 3040 → 3040 | нет | нет |

Единственное расхождение по данным — 342 строки universe, у которых раньше `token_id_0/1` были
пустыми при заполненном `clob_token_ids_json`. Проверено отдельно: новая пара `(token_id_0,
token_id_1)` совпадает с распарсенным старым `clob_token_ids_json` на **всех** 70427 строках
(0 расхождений), пустых `token_id_0` в новом universe не осталось. Это ровно тот эффект, который
шаг 02 объявил намеренным. Каталог при этом не вырос (3040 → 3040) — остальные Gamma-поля не дают
лишним строкам пройти.

Промежуточные счётчики стадий совпали покомпонентно: candidate maps 28788, complete series 12113,
accepted links 4737 / 2130 серий, audit `{ambiguous: 2, matched: 2130, no_candidates: 433}`,
linked maps 4737, grid started games 4192, matched GRID windows 3086, live/paper 0,
unmatched 1651, price targets 3086, quote rows 3058.

### Что осталось непроверенным и почему

- Все сетевые пути (`--fetch`, `--fetch-universe`, `--fetch-candidates`, STRATZ, OpenDota) — по
  прямому запрету задания. Покрыты только mocked-тестами предыдущих заданий.
- STRATZ-индекс не пересобирался полностью из кеша; задание 4 сверило его на выборке 200 из 3086 ID.
- Три backtest golden-теста не выполнялись из-за отсутствующих локальных `market_seconds`
  (падают одинаково на обеих ревизиях).
- Live/paper ветка `s04` на реальных данных не сработала (GRID покрывает все 3086 линков), она
  проверена только синтетическими `state.jsonl` в задании 3.
- Сравнение сделано на одном локальном наборе кешей на дату `2026-09-04T21:38:02Z`, не на всей
  истории архивов.
- 36 старых GRID state-кешей без `data.seriesState` остаются: проверка на их отсутствие сохранена
  (задание 3), refetch не запускался.

### Какие производные таблицы нужно пересобрать

1. **`data/new_processed/universe/universe.parquet` — обязательно.** Иначе `s05a` не увидит 342
   строки со старой формой токенов и `build_targets` упадёт.
2. `grid_game_windows.parquet` — желательно (уходит колонка `grid_duration_seconds`); обычный
   запуск не блокирует.
3. `pregame_quotes.parquet`, `match_catalog.parquet` — содержимое совпадает, пересоберутся сами
   штатным прогоном после п. 1.

Сырые кеши не трогать: они валидны и читаются новым кодом без переписывания.

### Замечания

Последовательность закрыта. Итоговый сводный отчёт — `final.md` рядом с этим файлом.
