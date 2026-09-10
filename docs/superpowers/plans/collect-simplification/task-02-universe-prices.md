# Задание 2 — universe и цены (шаги 02, 04, 11)

Исходная ревизия: `efe54ac` (`main`). Рабочее дерево до начала: незакоммиченные изменения задания 1
(`src/collect/s02_link_opendota.py`, `src/collect/common/opendota_candidates.py`,
`src/collect/common/window_ids.py`, `tests/test_link_opendota.py`, новый `tests/test_window_ids.py`)
— не тронуты. Посторонняя правка `docs/next_steps.md` (заметка пользователя) появилась в дереве во
время работы и оставлена как есть.

Измененные файлы (все в `esports-trader`, не закоммичены):

- `src/collect/s01_build_universe.py`
- `src/collect/s05a_fetch_prices_history.py`
- `src/collect/common/catalog_types.py`
- `tests/test_link_opendota.py` (фикстура контракта)
- `tests/test_prices_history.py` (добавлены тесты загрузчика)
- `tests/test_build_universe.py` (новый)

## Общие метрики (через `ast`)

| Файл | Строки до/после | `ast.If` | `ast.IfExp` | comprehension-if | доп. операнды `BoolOp` |
|---|---:|---:|---:|---:|---:|
| `s01_build_universe.py` | 476 → 379 | 35 → 32 | 7 → 5 | 1 → 1 | 20 → 19 |
| `s05a_fetch_prices_history.py` | 272 → 241 | 12 → 11 | 0 → 0 | 0 → 0 | 4 → 2 |
| `common/catalog_types.py` | 93 → 73 | 0 | 0 | 0 | 0 |
| **Итого production** | **841 → 693** | **47 → 43** | **7 → 5** | **1 → 1** | **24 → 21** |

Новых production-модулей нет, ничего не вынесено в отдельные файлы. Основная экономия — не в
условиях, а в удаленных полях, dataclass-ах результата и мертвых преобразователях:
удалено 16 колонок universe, 2 dataclass-а (`DiscoveredEvent`, `InventoryClassification`),
1 dataclass телеметрии (`PregameQuotes`), 3 счетчика причин пропуска, 2 alias-типа
(`MarketClassificationMethod`, `MarketExclusionReason`), 3 функции (`parse_volume`, `iso_ts`,
`read_token_ids`).

Тесты: `tests/test_prices_history.py` 54 → 231 строк, новый `tests/test_build_universe.py` 150 строк,
`tests/test_link_opendota.py` — только сокращение фикстуры контракта.

## Шаг 02 — `s01_build_universe.py`

**Что удалено и почему**

- `ContractClassification.classification_method` и alias `MarketClassificationMethod`: значение
  писалось в universe и нигде не читалось. После удаления четыре ветки классификации схлопнулись:
  две ветки `series_winner` (`moneyline` и matchup-вопрос) отличались только методом и объединены в
  одно условие `has_match_teams and (sports_type == "moneyline" or MATCH_RE.search(question))`;
  две ветки `other` также стали одинаковыми. Приоритеты правил сохранены в прежнем порядке:
  Game N / child_moneyline → исключение scoreline → series-winner → other.
- `InventoryClassification` и `exclusion_reason`: причина однозначно выводилась из статуса
  (`series_linking_required` ⇔ `series_requires_linking`, `excluded` ⇔ `unsupported_contract_kind`).
  `classify_inventory` возвращает `MarketInventoryStatus`; правила candidate/series/excluded не
  изменились.
- `DiscoveredEvent`: после удаления колонки `discovery_sources` wrapper нес только сам `Event`.
  `load_discovered_events` возвращает `list[Event]`. Provenance сырых страниц (поле `source` в
  gzip-снимке) не тронут, `canonical_json` остался — он пишет raw snapshot.
- `parse_volume`, `iso_ts`, `read_token_ids` — потеряли последних вызывающих.
- Колонки universe (16 штук): `market_id`, `event_slug`, `title`, `group_item_title`,
  `sports_market_type`, `classification_method`, `scheduled_time`, `outcomes_json`,
  `clob_token_ids_json`, `volume`, `active`, `closed`, `market_start_date`, `market_end_date`,
  `discovery_sources`, `exclusion_reason`.

**Проверка потребителей на актуальном HEAD**

`grep` по `src`, `scripts`, `tests`, `data/views.sql`: у dota-universe ровно три читателя —
`s02_link_opendota.py`, `s05a_fetch_prices_history.py`, `s06_publish_catalog.py`. Ни одна удаленная
колонка ими не читается. Совпадения имен в `src/trader/*` и `tests/test_lol_*` относятся к другим
таблицам (LoL universe, collector sidecars) и не используют `MarketContractRow`.
`clob_token_ids_json` читался только `s05a` — переведен в этом же шаге (см. ниже).

**Сохранено:** `event_title`, `parse_status`, `team_a/b`, `best_of`, `game_number`, `scheduled_ts`,
`conditionId`, `event_id`, `contract_kind`, `inventory_status` (набор, который читает
`load_event_contexts` по отчету задания 1), плюс Gamma-поля каталога `market_slug`,
`seconds_delay`, `market_closed_at`, `token_id_0/1`. Fallback названий команд (structured → parsed)
и формата (`BO` из title → `Bo` из score) не тронут.

**Токены**

`token_id_0/1` теперь заполняются прямо из `market.outcomes.yes/no.token_id`, независимо от
`parse_market`. Смысл старого контракта сохранен: если одного токена нет, обе колонки пустые
(полной пары нет). Порядок совпадает со старым `clob_token_ids_json`: `parse_market` строит
`token_ids=(yes_token, no_token)`, то есть тот же `[yes, no]`, что писал `read_token_ids`.
Остальные Gamma-поля (`market_slug`, `seconds_delay`, `market_closed_at`) по-прежнему заполняются
только при успешном `parse_market`.

## Шаг 04 — `s05a_fetch_prices_history.py`

- `load_universe_clob_by_outcome` → `load_universe_token_pairs`: читает три колонки
  (`conditionId`, `token_id_0`, `token_id_1`) через `pd.read_parquet(columns=...)` + `dropna()`.
  `json_list` и разбор JSON удалены. `dropna()` — единая защита и от `None`, и от `NaN`, поэтому
  строки `"None"`/`"nan"` появиться не могут: неполная пара просто не попадает в словарь.
- Ошибка при отсутствии пары стала точнее: вместо «имеет N clob-токенов, ожидалось 2» —
  `"{condition_id} has no complete token pair in the universe"`. Связанный рынок без пары
  по-прежнему приводит к `ValueError`, а не к тихому пропуску.
- `PregameQuotes` удален: `collect_quotes` возвращает `list[PregameQuoteRow]`. Удалены три счетчика
  (`without_cached_history`, `without_pre_anchor_quote`, `without_consistent_pair`) и три
  `print_count` в `main`. Все три условия пропуска сохранены как есть.
- `parse_ts` в `cache_covers_window` удален: проверены все 9664 локальных raw price-кеша —
  `startTs`/`endTs` везде `int`, и `write_payload` пишет `target.window_start_ts` /
  `target.anchor_ts`, то есть только int. Сравнение стало прямым.
- Сохранены: проверка покрытия окна (включая повторную загрузку при сдвинувшемся anchor, потому что
  сравниваются границы, а не существование файла), диагностические цены и timestamps в
  `PregameQuoteRow`, число целей и прогресс загрузки, атомарная запись кеша.

## Шаг 11 — `common/catalog_types.py`

- `MarketContractRow` приведен к фактическому writer/reader: 32 поля → 16.
- Удалены alias-ы `MarketClassificationMethod` и `MarketExclusionReason` (потребителей не осталось).
- `CONTRACT_COLUMNS` строится из `__annotations__`; порядок колонок нового parquet проверен на
  реальной пересборке и совпадает с TypedDict.
- `GridGameWindowRow.grid_duration_seconds` **оставлен** — это шаг 03 (GRID), он идет после этого
  задания. Прочие GRID-поля тоже не трогались.
- `PregameQuoteRow`, `OpenDotaLinkRow`, `OpenDotaLinkAuditRow`, статусы и причины audit сохранены
  без изменений.

## Изменение выходных схем и поведения на некорректных входах

- `universe.parquet`: 32 → 16 колонок. Удалены `market_id`, `event_slug`, `title`,
  `group_item_title`, `sports_market_type`, `classification_method`, `scheduled_time`,
  `outcomes_json`, `clob_token_ids_json`, `volume`, `active`, `closed`, `market_start_date`,
  `market_end_date`, `discovery_sources`, `exclusion_reason`. View `universe` в `data/views.sql`
  использует `SELECT *`, поэтому его пользовательская схема сужается; сам файл views менять не
  нужно.
- `token_id_0/1` намеренно заполняются шире: у 342 строк локального universe, где раньше была
  только JSON-пара, теперь заполнены отдельные колонки (значения совпадают с JSON).
- `opendota_links.parquet`, `opendota_link_audit.parquet`, `pregame_quotes.parquet`,
  `grid_game_windows.parquet` и `match_catalog.parquet` — схемы не изменились.
- stdout `s05a`: удалены три строки `targets_without_*`. Остались `grid_price_targets`,
  `targets_needing_fetch`, `pregame_quote_rows`, `saved:` и сообщения загрузки.
- Некорректные входы: связанный condition без полной пары токенов по-прежнему ошибка (текст
  сообщения изменен). Нецелые `startTs/endTs` в raw price-кеше больше не дают «окно не покрыто», а
  вызовут `TypeError` — таких кешей локально нет и writer их не создает.

## Проверки: команды, результаты, сравнение данных

```sh
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -n 0 \
  tests/test_link_opendota.py tests/test_prices_history.py tests/test_match_catalog.py \
  tests/test_grid_starts.py tests/test_build_universe.py tests/test_window_ids.py \
  tests/test_fetch_stratz.py
```

Результат: **74 passed** (целевая четверка из задания — 41 passed).

Новые тесты:

- `tests/test_build_universe.py` (17 тестов): child_moneyline без номера игры, `Game N Winner` без
  sports type, приоритет scoreline над moneyline, series-winner только при наличии команд и
  moneyline/matchup-вопроса, таблица `classify_inventory` (map_winner, BO1 series, BO3 series,
  other), токены при неполных Gamma-полях, половина пары → обе колонки пустые, полный Gamma-рынок.
- `tests/test_prices_history.py` (+11 тестов): пропуск контракта с половиной пары, ориентация пары
  по `radiant_token_index`, ошибка для связанного рынка без пары, границы `cache_covers_window`
  (включая точное равенство краев и `None`), `collect_quotes` — точка ровно на anchor игнорируется,
  отсутствующая сторона кеша, несогласованная сумма, только-anchor-котировка.

Сравнение данных на реальных локальных кешах (без публикации, во временный каталог):

- universe пересобран из raw Gamma cache до и после: 70427 строк в обоих случаях; все сохраненные
  колонки, кроме токенов, совпадают побитово (`DataFrame.equals` = True).
- Токены: ровно 342 строки изменились, все — из пустых в заполненные; значения совпадают с
  `clob_token_ids_json[0]/[1]` старого файла. Ни одна из этих 342 строк не имеет полного набора
  Gamma-полей (`market_slug`/`seconds_delay`/`market_closed_at` — 0 из 342), поэтому маска
  полноты в `s06.keep_complete` не пропустит их в каталог.
- `load_event_contexts` на старом и новом universe: 2565 контекстов, объекты равны.
- `s05a`: `build_targets` — 3086 целей, список объектов идентичен старому; `collect_quotes` — 3058
  строк, `DataFrame.equals` = True.
- `s06.build_match_catalog_frame` на новых priors/universe: 3040 строк, значения всех колонок
  совпадают. Единственное расхождение — dtype `playback_available` (`bool` против `object`), и оно
  возникает из-за parquet-roundtrip эталона, а не из-за изменений: попарное сравнение значений дает
  0 различий.

Форматирование и статический анализ: `uv run ruff check` / `ruff format` по измененным файлам —
чисто; `uv run python -m basedpyright` (whole-project, как в hook) — **0 errors, 0 warnings**.

Сетевой сбор, `make collect-dry-run` и запись рабочих parquet не запускались.

## Что осталось непроверенным

- Полный `make test` по правилам плана не запускался; прогонялись целевые модули и соседние
  collect-тесты.
- Сетевой путь `s05a --fetch` не проверялся против реального CLOB (загрузка не менялась, кроме
  сравнения границ кеша).
- Гарантия «raw price cache всегда содержит целые `startTs/endTs`» проверена на 9664 локальных
  файлах и по коду writer, а не на исторических архивах вне этой машины.
- Классификация проверена на синтетических Gamma-рынках и на полной пересборке локального universe
  (совпадение `contract_kind`/`inventory_status`/`game_number` со старой версией на 70427 строках),
  но не против внешнего эталона Polymarket.

## Какие производные таблицы нужно пересобрать

- **`new_processed/universe/universe.parquet` — обязательно пересобрать из raw Gamma cache**
  (`s01_build_universe --as-of <stamp>` без `--fetch-universe`) перед первым обычным запуском
  `s05a`/`s06`: старый файл содержит 342 строки с пустыми `token_id_0/1`, и новый читатель
  `load_universe_token_pairs` их не увидит (для одной такой строки, входящей и в links, и в GRID
  windows, `build_targets` упадет с `ValueError`). Legacy-fallback на `clob_token_ids_json`
  намеренно не добавлен.
- `pregame_quotes.parquet` и `match_catalog.parquet` пересобирать не обязательно (содержимое
  совпадает), но они и так пересоберутся штатным прогоном после universe.
- Сырые кеши (Gamma pages, price history) не трогались и остаются валидными.

## Замечания следующему заданию

- Шаг 03 (GRID) остается заданию 3: `grid_duration_seconds` в `GridGameWindowRow` и
  `duration_seconds` в `GridGame` сознательно не тронуты; типы `catalog_types.py` в остальном уже
  сверены с writer/reader, так что шагу 11 после GRID останется только удалить GRID-поля.
- `MarketContractRow` теперь минимален: любое новое поле universe придется добавлять вместе с
  потребителем.
- `collect_quotes` возвращает список строк — не восстанавливать объект со счетчиками.
- Токены universe хранятся в одной форме (`token_id_0/1`); `clob_token_ids_json` в dota-пайплайне
  больше нет (в LoL-пайплайне своя колонка с тем же именем — она не тронута).
