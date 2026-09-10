# Итоговый отчёт: упрощение `src/collect`

План: [`2026-09-09-collect-simplification.md`](../2026-09-09-collect-simplification.md).
Исходная ревизия: `efe54ac`. Все 13 шагов выполнены пятью заданиями; изменения не закоммичены.

Отчёты заданий: [01](task-01-opendota-linking.md) · [02](task-02-universe-prices.md) ·
[03](task-03-grid.md) · [04](task-04-match-fetchers.md) · [05](task-05-catalog-final.md).

## Сводные метрики (`ast`, все 13 файлов)

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

- Строки: **−414 (−13.6 %)**. Условные конструкции всех видов: **257 → 219 (−15 %)**.
- Новых production-модулей нет, ни одна строка не перенесена в другой файл ради метрики.
- Три файла — нулевой diff: `window_ids.py` (проверено, что убирать нечего),
  `timestamps.py` (5 живых потребителей на границах ввода), `paths.py` (ни один артефакт не исчез).
- `opendota_candidates.py` вырос на 3 строки сознательно: восстановлен сбор `series_id` и два
  молчаливых `IfExp`-заглушки заменены явными `raise`.

### Откуда взялась экономия

| Источник | Строк | Пример |
|---|---:|---|
| Удалённая логика и повторное состояние | ≈ 150 | `build_matchup_series` без `current_best_of`/`current_has_short_map`, дублирующие проверки `parse_status`, повторные обходы кеша |
| Удалённая телеметрия и статистика загрузчиков | ≈ 140 | `FetchRunResult`, `FetchOutcome`, `CacheCoverage`, ETA/скорость/проценты, `average`, `format_hms` |
| Удалённые типы, поля и классы результатов | ≈ 125 | `SeriesBuildResult`, `PregameQuotes`, `InventoryClassification`, `DiscoveredEvent`, 16 колонок universe, `catalog_types.py` −21 |

Удалено 6 классов-результатов (`SeriesBuildResult`, `FetchRunResult`, `FetchOutcome`,
`CacheCoverage`, `PregameQuotes`, `InventoryClassification`), 12 диагностических счётчиков и
17 полей производных таблиц/внутренних объектов.

## Изменённые диагностические схемы

`data/views.sql` использует только `SELECT *`, поэтому изменения схем сразу видны пользователю в
views. Явных списков колонок в SQL нет — править нечего.

**View `universe` — удалено 16 колонок** (`universe.parquet`, 32 → 16):
`market_id`, `event_slug`, `title`, `group_item_title`, `sports_market_type`, `scheduled_time`,
`outcomes_json`, `volume`, `active`, `closed`, `market_start_date`, `market_end_date`,
`discovery_sources`, `classification_method`, `exclusion_reason`, `clob_token_ids_json`.

Осталось 16: `conditionId`, `event_id`, `event_title`, `contract_kind`, `team_a`, `team_b`,
`best_of`, `game_number`, `scheduled_ts`, `parse_status`, `market_slug`, `seconds_delay`,
`market_closed_at`, `token_id_0`, `token_id_1`, `inventory_status`.
Пара токенов теперь хранится в одной форме — только `token_id_0/1`.

**View `grid_game_windows` — удалена 1 колонка:** `grid_duration_seconds`.
Осталось `condition_id`, `spawn_at`.

**Без изменений схемы:** `opendota_links`, `opendota_links_audit` (audit сохранён целиком, включая
причины отказа и статусы), `pregame_quotes` (диагностические цены и timestamps сохранены),
`stratz_match_index`, `match_catalog`.

**Изменения логов (не схем):** удалены счётчики `targets_without_cached_history` /
`targets_without_pre_anchor_quote` / `targets_without_consistent_pair` (`s05a`),
`unresolved maps` / `built series spanning raw ids` / `raw ids spanning built series` и остальные
счётчики сборки серий (`s02`), подробные checkpoint-сводки, ETA и скорость (`s05b`, `s05`),
`catalog duration failed` / `catalog grid failed` (`s06`). Сообщения об ошибках конкретных запросов
и список отброшенных match ID сохранены везде.

## Сырые данные: ничего не потеряно

Требование пользователя «сырые данные собираются с запасом на будущее» проверено машинно в шаге 13:
для всех восьми файлов с запросами и записью сырых данных через `ast` извлечены все строковые
литералы > 40 символов и все модульные строковые константы, множества сопоставлены с `efe54ac`.

Расхождение ровно одно — сообщение удалённой дублирующей проверки
`'duplicate or unsorted game numbers for event '`. Все GraphQL/SQL/URL идентичны:

- OpenDota `explorer_sql` — посимвольно совпадает, `series_id` запрашивается, описан в
  `OpenDotaCandidateRow`, проверяется и сохраняется в новых страницах;
- GRID `ALL_SERIES_QUERY` и `SERIES_STATE_QUERY` — полный исходный состав, включая команды,
  турнир, формат, состояния, счёт и `duration`;
- STRATZ `RICH_QUERY`, Gamma-запросы `s01` — идентичны.

Ни один raw-writer не изменился: изменились только читатели. Фильтрации полей перед записью не
добавлено нигде. Старые сырые кеши читаются без переписывания (в частности, `GridSeriesStateData.seriesState`
стал `NotRequired`, потому что 36 локальных state-файлов его не содержат).

## Результаты тестов

Полный набор запущен один раз, в конце последовательности:

```
make test
→ 3 failed, 1938 passed, 24 warnings in 24.99s
```

Три падения — `tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
(dota 8837869969 / 8911784562 / 8933879286), причина
`FileNotFoundError: data/new_processed/market_seconds/va75c29ad/match_id=….parquet`: локально
отсутствуют backtest-данные. Те же три теста падают на чистом `efe54ac` — регрессии нет,
к `src/collect` они не относятся.

Форматирование, lint и типы чисты: `ruff format` (без изменений), `ruff check --fix`,
`basedpyright` — 0 errors, 0 warnings, 0 notes по всему проекту.

Тестов collect стало заметно больше: `tests/test_link_opendota.py` +297/−…, `test_prices_history.py`
+177, `test_stratz_client.py` +43, плюс три новых модуля — `test_build_universe.py` (150),
`test_fetch_opendota.py` (123), `test_window_ids.py` (49).

## Сравнение данных на одинаковом наборе сырых кешей

`efe54ac` развёрнут отдельным read-only worktree, каталоги данных подключены симлинками. Обе
ревизии прогнаны одним драйвером, подменяющим константы путей на временный каталог до импорта
стадий: ни одна стадия не писала в рабочие пути, сеть не задействована. Прогон полной цепочки
`s01 → s02 → s04 → s05a → s06` на срезе `2026-09-04T21:38:02Z`.

| Таблица | Строк old → new | Удалённые колонки | Расхождения по общим колонкам |
|---|---|---|---|
| `universe.parquet` | 70427 → 70427 | 16 | `token_id_0/1` — 342 строки (намеренно) |
| `opendota_links.parquet` | 4737 → 4737 | нет | **нет** |
| `opendota_link_audit.parquet` | 2565 → 2565 | нет | **нет** |
| `grid_game_windows.parquet` | 3086 → 3086 | `grid_duration_seconds` | **нет** |
| `pregame_quotes.parquet` | 3058 → 3058 | нет | **нет** |
| `match_catalog.parquet` | 3040 → 3040 | нет | **нет** |

Единственное расхождение — те самые 342 строки universe, где раньше отдельные token ID были
пустыми при заполненном `clob_token_ids_json`. Новая пара совпадает с распарсенным старым JSON на
всех 70427 строках; пустых `token_id_0` в новом universe не осталось. Каталог при этом не вырос
(3040 → 3040): остальные Gamma-поля не пускают лишние строки.

Промежуточные счётчики стадий совпали покомпонентно: candidate maps 28788, complete series 12113,
accepted links 4737 в 2130 сериях, audit `{ambiguous: 2, matched: 2130, no_candidates: 433}`,
GRID started games 4192, matched GRID windows 3086, live/paper 0, unmatched 1651,
price targets 3086, quote rows 3058.

## Таблицы, которые нужно пересобрать

Перед первым обычным запуском пайплайна, **из существующих кешей, без сети**:

1. **`data/new_processed/universe/universe.parquet` — обязательно.**
   `uv run python src/collect/s01_build_universe.py --as-of <stamp>` (без `--fetch-universe`).
   Иначе новый `s05a` не увидит 342 строки со старой формой токенов и `build_targets` упадёт с
   `ValueError`. Постоянный legacy-fallback на `clob_token_ids_json` намеренно не добавлен.
2. `data/new_processed/grid_game_starts/grid_game_windows.parquet` — желательно, чтобы файл
   соответствовал новой схеме без `grid_duration_seconds`. Обычный запуск не блокирует: `s05a` и
   `s06` читают из него только `condition_id` и `spawn_at`.
3. `pregame_quotes.parquet` и `match_catalog.parquet` пересоберутся сами штатным прогоном после п. 1;
   их содержимое совпадает с прежним.

`stratz_match_index.parquet` и все сырые кеши (Gamma, OpenDota candidate pages, OpenDota matches,
GRID index/state, price history, STRATZ) пересобирать не нужно и переписывать нельзя.

## Оставшиеся ограничения

- **Сеть не проверялась ни разу** — запрет плана. Реальные `--fetch*`, 403/429/5xx STRATZ, живой
  OpenDota, живой GRID, живой CLOB покрыты только mocked-тестами.
- STRATZ-индекс сверен на выборке 200 из 3086 линкованных ID, а не на всём корпусе.
- Три backtest golden-теста не выполнялись: локально нет `market_seconds` (одинаково на обеих
  ревизиях).
- Ветка live/paper в `s04` на реальных данных не сработала — GRID покрывает все 3086 линков;
  проверена только синтетическими `state.jsonl`.
- 36 старых GRID state-кешей не содержат `data.seriesState`; проверка на это сохранена, refetch не
  запускался.
- Сравнение сделано на одном локальном срезе кешей (`2026-09-04T21:38:02Z`), не на всей истории
  архивов и не против внешнего эталона Polymarket.
- Гарантия «raw price cache всегда содержит целые `startTs/endTs`» проверена на 9664 локальных
  файлах и по коду writer, но не на архивах вне этой машины.
- В `paths.py` остались четыре dir-константы без потребителей (`OPENDOTA_LINKS_DIR`,
  `NEW_STRATZ_MATCH_INDEX_DIR`, `GRID_GAME_WINDOWS_DIR`, `PREGAME_QUOTES_DIR`). Они были мёртвыми и
  до начала плана, каталоги существуют, поэтому по правилу шага 13 не удалялись.
- В `s04` осталось ≈ 300 строк GraphQL-запросов и raw-TypedDict, в `s05b` ≈ 105 строк `RICH_QUERY`:
  это описание сохраняемых сырых данных, сокращать их план запрещает.
- Изменения не закоммичены.
