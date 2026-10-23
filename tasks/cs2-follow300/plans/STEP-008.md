# STEP-008 — s06 каталог, s07 воронка, цели Makefile collect

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `d5952a0f`. Figma-ссылок в шаге нет.

## 1. Что делает шаг

- `src/cs2/s06_publish_catalog.py` пишет `data/cs2/catalog/match_catalog.parquet`: строка на торгуемую карту, поля-пары Dota. Ещё он пишет `data/cs2/catalog/catalog_audit.parquet`: строка на каждую карту GRID из связей s04 и её статус — `cataloged` или первая причина отказа.
- `src/cs2/s07_link_readiness.py` только читает и печатает в stdout воронку по месяцам и причины отказов на каждом шаге.
- `src/cs2/constants.py` получает пути каталога и таблицу `CS2_MAP_CODES`.
- `Makefile` получает цели `cs2-catalog`, `cs2-link-report`, `cs2-collect`.
- Новый тест `tests/test_cs2_catalog.py`.

Всё ниже s06 (s08–s10, бэктест) читает только каталог и кэши s05.

## 2. Что я проверил (факты)

Скрипты проб — одноразовые `uv run python -` в этой сессии, ничего не записано на диск, кроме этого плана.

1. **Образец Dota.** `src/collect/s06_publish_catalog.py:129` — `check_masks` по порядку, `first_failure_counts` (`:155`) относит каждую строку к первой провалившейся проверке. `src/collect/s07_link_readiness.py` — только чтение, печать в stdout. `MatchCatalogRow` (`src/shared/types/dataset.py:8`) — это `TypedDict`, а не dataclass. Типизированный объект строки у Dota — frozen dataclass `CatalogEntry` (`src/shared/utils/match_catalog.py:19`).
2. **Входы s06 — только выходы шагов s01–s05:**
   - `data/cs2/universe/markets.parquet` (`Cs2UniverseMarketRow`, `src/cs2/s01_build_universe.py:57`): `token_id_0/1`, `market_slug`, `market_closed_at`, `winning_token_index`;
   - `data/cs2/links/grid_links.parquet` (`Cs2GridLinkRow`, `src/cs2/s02_link_grid.py:58`): строка на каждое событие universe, `status`, `grid_link_source`, `team_a_token_index`, `start_time`;
   - `data/cs2/grid/games.parquet` (`Cs2GridGameRow`, `src/cs2/s03_fetch_grid_rounds.py:145`): `started_at`, `ended_at`, `team_a_won`, `team_b_won`, `team_a_score`, `team_b_score`;
   - `data/cs2/bo3/links.parquet` (`Cs2Bo3LinkRow`, `src/cs2/s04_link_bo3.py:124`): строка на каждую карту GRID связанных серий, `status`, `condition_id`, `bo3_game_id`, `map_name` (уже нормализовано), `team_a_bo3_clan`, `bo3_winner_clan`;
   - `data/cs2/bo3/replay_maps.parquet` (`Cs2ReplayMapRow`, `src/cs2/s05_replay_contract.py:83`): строка на каждую связанную карту, `status`.
3. **«Есть рынок» уже решает s04.** `decide_bo3_link` (`s04_link_bo3.py:597`) первым делом даёт `excluded_series_rule`, если у карты нет `condition_id`. `build_map_markets` (`:214`) ставит рынок серии только на карту с номером best-of. Карта 2 BO3 без «Map 2 Winner» в s04 получает `excluded_series_rule`, карта 3 — `condition_id` рынка серии.
4. **Статус s05 `ok` значит: выжимка всех раундов есть и сверки 1–3 пройдены.** `fetch_map` (`s05_fetch_bo3_replays.py:405`) пишет выжимку только после всех раундов. `decide_map_status` (`s05_parse_replay.py:561`) даёт `ok` только после `not_mr12`, `replay_grid_mismatch`, `pause_in_round`, `round_clock_incomplete`. `replay_maps` содержит строку на каждую связанную карту s04 (`build_replay_tables`, `:613`).
5. **Рынок без исхода** (весь universe 18.10, `load_discovered_events` по сырым страницам): 31 602 рынка карт и серий. С исходом — 28 901. Закрыты ровно 0.5 / 0.5 (отмена) — 2 531. Открыты — 170. Других цен нет. У всех 28 901 рынков с исходом есть `token_id_0/1`, `market_slug`, `market_closed_at`. У открытых рынков `market_closed_at` пустой (170 из 170).
6. **Итог GRID** (старый `data/cs2/grid/grid_games.parquet`, 4 882 законченных карты, июнь–октябрь 2026):
   - флаги победы: `(False, True)` 2 268, `(True, False)` 2 611, `(False, False)` 3;
   - счёт, который не может закончить карту MR12 с овертаймом MR3, — у 52 карт: 0:1, 8:2, 7:7, 15:9, 16:11, 11:26, победа команды с меньшим счётом (1:13 у победителя A). Это технические поражения, брошенные карты и шум GRID.
7. **Названия карт.** Старый GRID: dust2 1 007, mirage 852, ancient 804, nuke 647, inferno 603, anubis 458, cache 455, overpass 57. bo3: те же с префиксом `de_`. Данных о названиях карт за октябрь 2025 – май 2026 у нас нет.
8. **Проба на scratch-цепочке 17.10** (`work/impl007`: universe, links, grid, bo3 links, replay_maps). bo3 links: 27 строк (linked 22, no_bo3_map 5). replay_maps: ok 12, replay_grid_mismatch 10. У всех 12 карт `ok` победитель GRID = победитель bo3 = исход рынка, счёт законный, названия из таблицы. Ожидание s06: 12 строк каталога, все `gamma`.
9. **Проба на scratch-цепочке 10.09** (`impl003` links и universe, `impl004` grid и `grid_raw`, `impl005` bo3 links, `impl006` replay_maps). 90 событий, matched 49, серий с картами 41. 8 серий без карт, у всех 8 есть сырой файл (s03: no_series_state 7, grid_team_mismatch 1). bo3: linked 57, no_bo3_map 40, grid_rounds_incomplete 5. replay: ok 6, replay_not_fetched 51. Связанные карты s04 = карты replay_maps. У 6 карт `ok` итог сходится: 3 `gamma`, 3 `allseries`.

## 3. Решения

1. **База s06 — все строки `bo3/links.parquet`**, как Dota s06 проверяет каждую строку связей. Статус карты — первая провалившаяся проверка в таком порядке:
   1. статус s04, если он не `linked` (`excluded_series_rule`, `grid_rounds_incomplete`, `no_bo3_map`, `ambiguous_bo3`);
   2. статус s05, если он не `ok` (`not_mr12`, `replay_grid_mismatch`, `pause_in_round`, `round_clock_incomplete`, `replay_missing`, `replay_not_fetched`);
   3. `unknown_map` — `map_name` нет в `CS2_MAP_CODES`;
   4. `grid_no_winner` — у карты GRID не ровно один флаг победы;
   5. `irregular_score` — счёт победителя GRID не законный конец карты (правило ниже). Сюда попадают техническое поражение и брошенная карта;
   6. `bo3_no_winner` — пусто `team_a_bo3_clan` или `bo3_winner_clan`;
   7. `market_unresolved` — у рынка нет исхода и `market_closed_at` пустой (рынок открыт);
   8. `market_canceled` — у рынка нет исхода, рынок закрыт (отмена 0.5 / 0.5);
   9. `outcome_mismatch` — победа A по GRID, по bo3 и по рынку не совпадают;
   10. иначе `cataloged`.

   Так аудит s06 — единый источник причин по картам, а тест «рынок серии на карте 2 BO3» проходит через s06. Порядок 4–6 стоит до рынка: у брошенной карты рынок часто отменён, и причина в аудите — сама карта.
2. **Законный конец карты** — `is_regulation_score(winner, loser)`:
   - `winner == 13` и `loser <= 11`;
   - или `winner > 13`, `(winner - 13) % 3 == 0` и `2 <= winner - loser <= 4` (овертайм MR3: 16:12…16:14, 19:15…19:17).

   Формат MR12 с овертаймом MR3 уже проверяет сверка 3 (`not_mr12`). Счёт GRID у связанной карты равен числу раундов: так требует отпечаток s04 (`build_grid_fingerprint`, `:267`).
3. **Победа A:**
   - GRID — `team_a_won` (у s05-ok карты `team_a_*` уже ориентированы по `team_a_grid_id`, решение STEP-004);
   - bo3 — `bo3_winner_clan == team_a_bo3_clan`. Если имя победителя записано иначе, выйдет «победа B» и `outcome_mismatch`. Ошибка имени даёт только отказ, а не неверную строку каталога;
   - рынок — `winning_token_index == team_a_token_index` из s02. Для рынка серии на решающей карте и в BO1 победитель серии = победитель карты.

   `team_a_win` в каталоге — из GRID (PLAN, «Строка каталога»).
4. **Строка каталога — frozen dataclass `Cs2CatalogRow`**, как сказано в feature.json. Поля по порядку feature.json: `match_id` (= `bo3_game_id`), `condition_id`, `event_id`, `token_id_0`, `token_id_1`, `market_slug`, `market_closed_at`, `team_a_token_index`, `spawn_at` (= `games.started_at`), `ended_at` (= `games.ended_at`), `team_a_win`, `grid_link_source`. Время — ISO-текст, как в `MatchCatalogRow`. `CATALOG_COLUMNS = [field.name for field in fields(Cs2CatalogRow)]`. Читателя каталога в объекты в этом шаге нет: s07 читает таблицу pandas. Читатель появится в STEP-009, когда s08 он понадобится.
5. **Строка аудита — frozen dataclass `Cs2CatalogAuditRow`** с полями `game_id`, `event_id`, `bo3_game_id` (может быть пустым), `map_name`, `status`. Тип статуса — `Cs2CatalogAuditStatus = Cs2Bo3LinkStatus | Cs2ReplayStatus | Cs2CatalogStatus`, где `Cs2CatalogStatus` — свои статусы s06 (п. 1, шаги 3–10).
6. **Устаревшие входы — ошибка, а не причина.** Если у строки s04 `linked` нет строки в `replay_maps`, s06 падает с `RuntimeError("... rerun cs2-replays")`. Так же при отсутствии события в `grid_links` (status `matched`), карты в `games` или рынка в `markets`. Случаев, которых сегодня не бывает, нет и в причинах: пустой `ended_at`, `market_slug` или `market_closed_at` у карты, прошедшей все проверки, — `RuntimeError`.
7. **`CS2_MAP_CODES`** в `src/cs2/constants.py`, ключ — нормализованное имя (`normalize_map_name`, s04):
   `ancient 1, anubis 2, cache 3, dust2 4, inferno 5, mirage 6, nuke 7, overpass 8, train 9, vertigo 10`.
   Первые восемь есть в данных июня–октября. `train` и `vertigo` — карты пула CS2 2025 года: лишняя строка ничего не меняет, а пропуск выкинул бы карты train-периода. Коды не перенумеровывать: s10 пишет их в признак.
8. **Воронка s07** — месяц = `start_time` события из `grid_links` (`YYYY-MM`, UTC), для всех строк, включая карты. Так карта не меняет месяц между шагами. Строки воронки (единица — в названии):
   1. `universe events` — все строки `grid_links`;
   2. `GRID matched events` — `status == matched`;
   3. `GRID series with games` — matched-событие, чей `series_id` есть в `games`;
   4. `GRID maps` — все строки аудита s06;
   5. `maps with market` — без `excluded_series_rule`;
   6. `bo3 linked maps` — без остальных статусов s04;
   7. `replay ok maps` — без статусов s05;
   8. `catalog maps` — строки каталога;
   9. `catalog gamma maps` — строки каталога с `grid_link_source == gamma`;
   10. `train maps`, `validation maps` — строки каталога, split по `spawn_at`: `>= VALIDATION_START_TIME` — validation, как в Dota (`check_masks`, `src/collect/s06_publish_catalog.py:135`).

   Вторая таблица — причины с префиксом шага: `s02 <status>` (не matched), `s03 <status>`, `s04 <status>`, `s05 <status>`, `s06 <status>`. Обе таблицы — `pd.crosstab` по месяцам плюс колонка `total`. В первой все строки идут всегда, даже с нулями. Во второй — только причины, которые встретились.
9. **Причины s03 в s07.** s03 печатает статусы серий, но в файл их не пишет. s07 берёт matched-серии без строк в `games` и для каждой зовёт `decide_series_status(series_link_from_row(row), require_series_state(raw_dir, series_id))` из s03. Это читает только сырые файлы выпавших серий (10.09 — 8 файлов). s03 не меняем.
10. **CLI** — typer, как s02–s05: папки входов и выхода опциями с путями по умолчанию из `constants.py`. Так пробы идут в scratch, не трогая `data/cs2`.

## 4. Изменения по файлам

### `src/cs2/constants.py`

```python
CS2_MATCH_CATALOG_PATH = CS2_CATALOG_DIR / "match_catalog.parquet"
CS2_CATALOG_AUDIT_PATH = CS2_CATALOG_DIR / "catalog_audit.parquet"
CS2_MAP_CODES: dict[str, int] = {"ancient": 1, ..., "vertigo": 10}
```

### `src/cs2/s06_publish_catalog.py` (новый, ~200 строк)

Импорт, а не копия:

- `Cs2UniverseMarketRow` (s01); `Cs2GridLinkRow`, `GridLinkSource` (s02);
- `Cs2GridGameRow`, `apply_table_dtypes` (s03);
- `Cs2Bo3LinkRow`, `Cs2Bo3LinkStatus`, `read_game_rows` (s04);
- `Cs2ReplayMapRow`, `Cs2ReplayStatus` (s05_replay_contract);
- `replace_nulls_with_none`, `write_parquet` (`shared.utils.parquet_io`).

Состав:

- `Cs2CatalogStatus` (Literal), `CATALOG_STATUS_NAMES` (tuple в порядке п. 3.1), `Cs2CatalogAuditStatus`.
- `Cs2CatalogRow`, `Cs2CatalogAuditRow` — frozen dataclass.
- `MapContext` — frozen dataclass: `link: Cs2Bo3LinkRow`, `replay: Cs2ReplayMapRow | None`, `game: Cs2GridGameRow`, `event: Cs2GridLinkRow`, `market: Cs2UniverseMarketRow | None`. Рынок и реплей пустые только у строк s04 не `linked`.
- `CatalogTables` — frozen dataclass: `catalog: pd.DataFrame`, `audit: pd.DataFrame`.
- `is_regulation_score(winner: int, loser: int) -> bool`.
- `read_grid_team_a_win(game) -> bool | None` — `None`, если флаги не «ровно один».
- `read_bo3_team_a_win(link) -> bool | None` — `None`, если пусто одно из имён.
- `decide_catalog_status(context: MapContext) -> Cs2CatalogAuditStatus` — порядок п. 3.1.
- `build_catalog_row(context: MapContext) -> Cs2CatalogRow` — зовётся только для `cataloged`.
- `build_map_contexts(markets, grid_links, games, bo3_links, replay_maps) -> list[MapContext]` — словари по ключам и проверки п. 3.6.
- `build_catalog_tables(markets, grid_links, games, bo3_links, replay_maps) -> CatalogTables` — чистая функция на DataFrame, её зовут тесты. В конце: `match_id` и `condition_id` каталога уникальны, иначе `RuntimeError`. Каталог пуст — `RuntimeError("catalog is empty")`, как `validate_link_table` в s02 и s04.
- dtypes через `apply_table_dtypes`: `match_id`, `team_a_token_index` — int64; `team_a_win` — bool; остальное — string. В аудите `bo3_game_id` — `Int64`. Сортировка каталога — по `spawn_at`, `match_id`; аудита — по `event_id`, `game_id`.
- `print_catalog_summary(tables, output_dir)`: число карт GRID, счёт каждого статуса аудита в порядке s04 → s05 → s06, имена `unknown_map` с числом карт, строк каталога, из них `gamma`, пути.
- `main(universe_dir, links_dir, grid_dir, bo3_dir, output_dir)`: читает пять таблиц, строит, пишет `match_catalog.parquet` и `catalog_audit.parquet`, печатает итог.

Чтение строк — тот же приём, что в s04 и s05: `cast(list[X], replace_nulls_with_none(frame).to_dict(orient="records"))`. У `grid_links` берём только `status == matched`.

### `src/cs2/s07_link_readiness.py` (новый, ~150 строк)

- `FunnelStage` — frozen dataclass `line: str`, `statuses: tuple[str, ...]`. `MAP_STAGES` — четыре шага карт по п. 3.8 (5–8). Статусы берём из `STATUS_NAMES` s04 (кроме `linked`), `STATUS_NAMES` s05 (кроме `ok`) и `CATALOG_STATUS_NAMES` s06 (кроме `cataloged`).
- `read_event_months(grid_links) -> pd.Series` — `event_id` → `YYYY-MM`.
- `read_series_drop_statuses(grid_links, games, raw_dir) -> pd.Series` — п. 3.9.
- `assign_split(spawn_at: pd.Series) -> pd.Series` — `train` / `validation` по `VALIDATION_START_TIME`.
- `count_by_month(lines: pd.Series, months: pd.Series, order: list[str]) -> pd.DataFrame` — `pd.crosstab`, `reindex(order, fill_value=0)` для воронки, колонка `total`.
- `report_funnel(links_dir, grid_dir, raw_grid_dir, catalog_dir) -> None` — печатает две таблицы `to_string()`.
- `main` — typer с этими четырьмя опциями.

### `Makefile`

После `cs2-replays`:

```make
cs2-catalog:  ## CS2 s06 match_catalog.parquet + catalog_audit.parquet (no network)
	$(PY) src/cs2/s06_publish_catalog.py

cs2-link-report:  ## CS2 s07 funnel universe -> GRID -> rounds -> bo3 -> replays -> catalog -> split, by month
	$(PY) src/cs2/s07_link_readiness.py

cs2-collect: cs2-universe cs2-link cs2-rounds cs2-bo3 cs2-replays cs2-catalog cs2-link-report  ## CS2 s01-s07
```

Добавить `cs2-catalog cs2-link-report cs2-collect` в `.PHONY`. `cs2-collect` идёт с `--fetch` у s01–s05, как их цели. Если s05 вышел с кодом 1 (429/403), make останавливается до каталога — так и надо.

### `tests/test_cs2_catalog.py` (новый)

Фикстуры — строки `TypedDict` s01–s05 в памяти, функции-конструкторы, как `market_row` и `matched_link` в `tests/test_cs2_link_bo3.py`. Одно BO3-событие `E1`, `gamma`, команда A = токен 0. Рынки: «Map 1 Winner» и рынок серии, «Map 2 Winner» нет. Три карты GRID, A выигрывает 1 и 3, B — 2.

1. `test_series_market_skips_map_two_and_covers_map_three` — `condition_id` строк s04 берём из `build_map_markets(3, markets)`: карта 2 без рынка (`excluded_series_rule`), карта 3 — рынок серии. Каталог — карты 1 и 3, у карты 3 `condition_id` рынка серии, `team_a_win` True. В аудите у карты 2 — `excluded_series_rule`.
2. `test_outcome_mismatch_is_dropped` — у рынка карты 1 `winning_token_index = 1`, а GRID и bo3 дают победу A. Карты 1 нет в каталоге, в аудите `outcome_mismatch`.
3. `test_unknown_map_is_dropped` — `map_name = "dust3"`: `unknown_map`.
4. `test_regulation_score` (parametrize): (13, 0) да, (13, 11) да, (13, 12) нет, (16, 12) да, (16, 14) да, (16, 15) нет, (16, 11) нет, (19, 17) да, (8, 2) нет, (14, 12) нет.
5. `test_canceled_market_is_dropped` — закрытый рынок без исхода: `market_canceled`; открытый (`market_closed_at` пустой): `market_unresolved`.
6. `test_split_by_validation_start` — `assign_split` из s07: `VALIDATION_START_TIME − 1` с → `train`, ровно `VALIDATION_START_TIME` → `validation`.
7. `test_linked_map_without_replay_row_raises` — строка s04 `linked` без строки `replay_maps`: `RuntimeError`.

## 5. Крайние случаи

- **Рынок серии на картах 1–2** не попадает в каталог: s04 не ставит его туда (`build_map_markets`), карта получает `excluded_series_rule`.
- **BO1** — одна карта с номером 1 = best-of, рынок серии её покрывает (`series_winner_covers_map`). Отдельного кода нет.
- **BO5 с «Map 3/4 Winner»** — рынок серии только на карте 5, если у неё нет своего рынка.
- **Отмена на Polymarket** — 0.5 / 0.5, `market_canceled`. 2 531 рынок в universe, большая часть — несыгранные карты (у них нет строки GRID). Сыгранную карту с отменённым рынком s06 покажет в аудите.
- **Техническое поражение** с сыгранными раундами (8:2, победа команды с меньшим счётом) — `irregular_score`. Без раундов такая карта не доходит до s06: s04 даёт `grid_rounds_incomplete` или `no_bo3_map`.
- **Ничья 12:12 без овертайма** (бывает в лигах) — оба флага GRID False, `grid_no_winner`.
- **Пилот с `--window`** — universe урезан, все входы s06 согласованы между собой. Выход s06 пишется поверх старого: каталог всегда отвечает текущим входам.
- **s05 ещё качает** — карты без выжимки имеют статус `replay_not_fetched`, s06 их не берёт. Повторный s06 после загрузки добавит их.
- **Порядок колонок** каталога — порядок полей dataclass; s08–s10 читают по именам.

## 6. Проба в scratch

Только чтение `work/impl00*`, выход — в `work/impl008/`. В `data/cs2/catalog` проба не пишет.

1. 17.10:
   ```sh
   W=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/cs2-follow300/orchestrate/work
   PYTHONPATH=src uv run python src/cs2/s06_publish_catalog.py --universe-dir $W/impl007/universe --links-dir $W/impl007/links --grid-dir $W/impl007/grid --bo3-dir $W/impl007/bo3 --output-dir $W/impl008/oct17/catalog
   PYTHONPATH=src uv run python src/cs2/s07_link_readiness.py --links-dir $W/impl007/links --grid-dir $W/impl007/grid --raw-grid-dir $W/impl007/grid_raw --catalog-dir $W/impl008/oct17/catalog
   ```
   Ожидание: аудит 27 строк — `no_bo3_map` 5, `replay_grid_mismatch` 10, `cataloged` 12. Каталог 12 строк, все `gamma`, все validation, месяц `2026-10`. Воронка: events 12, matched 12, series with games 12, GRID maps 27, with market 27, bo3 linked 22, replay ok 12, catalog 12.
2. 10.09: s06 читает `links.parquet` и `replay_maps.parquet` из одной папки. Скопировать `impl005/bo3/links.parquet` и `impl006/bo3/replay_maps.parquet` в `work/impl008/sep10/bo3/`. Входы: universe и links из `impl003`, grid из `impl004/grid`, raw grid из `impl004/grid_raw`. Ожидание: каталог 6 строк (`gamma` 3, `allseries` 3), все validation, месяц `2026-09`. В причинах s03: `no_series_state` 7, `grid_team_mismatch` 1. s04: `no_bo3_map` 40, `grid_rounds_incomplete` 5. s05: `replay_not_fetched` 51.
3. Второй запуск s06 на тех же входах — `cmp` обоих parquet с первым: файлы совпадают.

Если цифры другие — разобраться и записать причину в отчёт implement.

## 7. Проверки

1. `uv run ruff format src/cs2 tests/test_cs2_catalog.py` и `uv run ruff check src/cs2 tests/test_cs2_catalog.py` — без ошибок.
2. `uv run python -m basedpyright` — 0 errors.
3. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_catalog.py tests/test_cs2_check_live.py tests/test_cs2_replays.py tests/test_cs2_link_bo3.py tests/test_cs2_grid_rounds.py tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
4. `make test` — ожидание по progress.txt: всё проходит, кроме двух известных `lol_*` follow300 smoke (нет `data/lol` в worktree). Возможен известный флейк `smoke[dota_9007700576]`: перезапустить его отдельно.
5. `make help | grep cs2-` — видны `cs2-catalog`, `cs2-link-report`, `cs2-collect`. `make -n cs2-collect` печатает семь команд s01–s07 по порядку.
6. Проба раздела 6 — цифры совпали.
7. `git status` в worktree: изменены только `Makefile`, `src/cs2/constants.py`, новые `src/cs2/s06_publish_catalog.py`, `src/cs2/s07_link_readiness.py`, `tests/test_cs2_catalog.py`. `data/cs2/catalog/` пуст. Один коммит.

## 8. Риски и открытые вопросы

- **frozen dataclass против TypedDict.** feature.json просит frozen dataclass. `MatchCatalogRow` Dota и все строки parquet s01–s05 — `TypedDict`. Я выбрал dataclass по feature.json. Если оркестратор хочет единый стиль с s01–s05, замена — три строки (`CATALOG_COLUMNS` и сборка таблицы).
- **`CS2_MAP_CODES` за train-период не проверен.** Названий карт за октябрь 2025 – май 2026 в данных нет. s06 печатает имена `unknown_map`, s04 — «map names only in grid». Пилот (STEP-014) увидит новые имена; добавление строки в таблицу — правка бага пилота.
- **`irregular_score`** — новое правило, в feature.json его нет как имени причины. Оно закрывает «техническое поражение» из feature.json. На старых данных GRID оно ловит 52 карты из 4 882 до фильтров s04–s05; сколько дойдёт до s06, покажет пилот.
- **bo3 победитель по имени.** `bo3_winner_clan` сравниваем с `team_a_bo3_clan` по строке. Расхождение имени даст `outcome_mismatch`, а не неверную строку. Число таких карт покажет аудит пилота.
- **Месяц воронки — по старту события**, а не карты. Карта 2 события в 23:50 31-го числа идёт в месяц события. На split это не влияет: split — по `spawn_at`.
- **s07 читает сырые Series State** выпавших серий. Если сырого файла нет (s03 не запускали с `--fetch`), s07 падает `FileNotFoundError` от `require_series_state`. В цепочке `cs2-collect` s03 идёт раньше, файл есть.

## 9. Чего в шаге нет

- Читателя каталога в объекты для s08–s10 (STEP-009).
- Полей Dota про архивы, horn, STRATZ, паузы, `seconds_delay`; номера карты, best-of, названия, счёта серии (они в `replay_maps.parquet`); id GRID (`grid_game_id`, `series_id` — в связях s02 и s04); `contract_kind` (в universe). Как в PLAN.md, «Строка каталога».
- Правок s01–s05 и общего кода Dota.
- Запуска на настоящих `data/cs2` (пилот, STEP-014).
