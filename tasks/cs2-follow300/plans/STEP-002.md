# STEP-002 — s01: вселенная CS2-событий Gamma

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, один коммит.
Figma и дизайна нет.

## 1. Что делает шаг

s01 строит две таблицы CS2 из Gamma:

- `data/cs2/universe/events.parquet` — строка на событие;
- `data/cs2/universe/markets.parquet` — строка на рынок события;
- `data/cs2/universe/universe_manifest.json` — `data_as_of` и окна `--window`.

Сырые страницы keyset лежат в `data/cs2/raw/gamma/events/<source>/page_NNNNN.json.gz`. Это раскладка Dota. Её же читает `index_gamma_markets(events_dir)` (`src/shared/utils/polymarket.py:175`), через неё бэктест LoL берёт `seconds_delay` (`src/backtest/lol_inputs.py:295`). STEP-013 сделает так же для CS2.

## 2. Что я проверил (факты)

Данные: страницы Gamma циклов 1–2 в `data/cs2/polymarket/gamma_raw/` (closed walk с 2026-03-03, 8 737 событий) и 4 запроса к Gamma 18.10 (scratch: `tasks/cs2-follow300/orchestrate/work/plan002/`).

1. **Тег.** У CS2-событий тег `{"id": "100780", "slug": "counter-strike-2"}`. Запрос `tag_id=100780` даёт те же события, что `tag_slug=counter-strike-2` из `scripts/cs2/fetch_gamma.py`, плюс более старые.
2. **`limit` keyset.** На `limit=500` Gamma отдаёт 100 событий. Страница — 0,5–2,5 МБ JSON.
3. **Ранний хвост тега мал.** Первая страница `tag_id=100780&closed=true` покрывает `startDate` от 2024-10-09 до 2025-09-30, всего 100 событий. Запрос с `start_date_max=2025-07-13` вернул 1 событие. Опережение «since − 90 дней» из `fetch_gamma.py` экономит одну-две страницы. Поэтому s01 идёт по тегу целиком, как Dota: фильтр `start_date_min` не нужен.
4. **SDK.** `Event.parse_response_list` разобрал все 92 страницы циклов: 9 128 событий, 107 732 рынка из 107 732 (`work/plan002/sdk_parse_probe.py`). Значит, Dota-чтение `read_polymarket_universe_page` подходит.
5. **Правила Dota на CS2 (март–октябрь 2026, 8 594 событий матчей).**
   - `child_moneyline` — 14 450 рынков. У всех 14 450 в вопросе есть «Map N Winner». У других рынков этой строки нет. N: 1 и 2 — по 7 163, 3 и 4 — по 62 (только BO5).
   - Рынок серии: `moneyline`, вопрос `Counter-Strike: A vs B (BO3) - <лига>` — 8 593. Ни у одного события нет двух рынков серии.
   - Три `moneyline` события BO2 (`Benched gods to win 2-0?`, `... Draw (1-1)?`) правило счёта `SCORELINE_RE` отправляет в `other`.
   - Best-of: в заголовке `(BO3)` — 8 594, в `score` (`000-000|0-0|Bo3`) — 8 436. Расходятся у 3: заголовок BO1/BO2, а `score` Bo3.
6. **Октябрь 2025 другой (живой запрос, 334 события матчей).**
   - `teams` пустой у всех 334: имена команд есть только в заголовке.
   - `gridSeriesId` нет ни у одного.
   - 168 рынков серии без `sportsMarketType`, вопрос `Counter-Strike: A vs B` без `(BO3)`.
   - 9 событий без best-of и в заголовке, и в `score`.
   - Вывод: нужны оба запасных пути Dota — имена из заголовка и `MATCH_RE` по вопросу.
7. **Исход рынка.** `outcomePrices` закрытых `moneyline` и `child_moneyline` (март–октябрь 2026):
   - `["1","0"]` — 11 655, `["0","1"]` — 9 408;
   - `["0.5","0.5"]` — 1 982: отмена события (`period` `0/3`) или карты;
   - один рынок `umaResolutionStatus = proposed` с `["0","1"]`.
8. **Метки исходов.** Метки токенов совпадают с `teams` по порядку у 22 998 рынков `moneyline` и `child_moneyline` из 23 046. У 48 написание другое. Поэтому метки токенов пишем на каждый рынок: s02 ориентирует токены через `orient_outcomes` по ним.
9. **Ожидание для smoke-прогона.** Циклы 1–2, `startTime` 01.06–16.10.2026: 5 081 событие, 5 076 рынков серии (4 684 с победителем), 8 396 рынков карт (7 758 с победителем), `gridSeriesId` у 2 250 (44%).
10. **Пакет `cs2`.** `src/cs2` и `scripts/cs2` — два namespace-пакета с одним именем. Я проверил это на копии в scratch: Python и basedpyright сливают оба (`from cs2.constants import …` и `import cs2_io` в `scripts/cs2` работают).
11. **typer 0.27.1.** `Annotated[list[str], typer.Option("--window", default_factory=list)]` без флага даёт `[]`, с двумя флагами — список из двух. basedpyright strict: 0 ошибок.

## 3. Решения

1. **Код Dota берём импортом, не копией.** Из `src/collect/s01_build_universe.py` импортируем общие функции без тега Dota: `fetch_query_pages`, `best_of`, `read_structured_teams`, `clean_text`, `iso_datetime`, `build_as_of_stamp`, `SCORELINE_RE`. Пример импорта шага из шага уже есть: `src/collect/s07_link_readiness.py:26`. Код Dota и LoL не меняем.
2. **Запросы — как Dota:** `tag_id=100780`, `closed=true` и `closed=false`, без `start_date_min` (факт 3). Источники называются `tag_100780_closed` и `tag_100780_open`.
3. **Фильтр по дате — офлайн, при сборке из сырых страниц.** Событие проходит, если `startTime` ≥ 2025-10-11 00:00 UTC и, при флаге `--window`, попадает в одно из окон. `--window` не сокращает загрузку: тег всё равно идёт целиком (~150–200 страниц, 1–3 мин).
4. **`--window START:END`.** Обе даты — дни UTC включительно: окно `[START 00:00, END+1 00:00)`. Пилот пишет `--window 2026-10-01:2026-10-17 --window 2026-04-01:2026-04-07`. Ошибка формата или START > END — `typer.BadParameter` до загрузки.
5. **`contract_kind`** — порядок правил `classify_contract` Dota, регулярки CS2:
   1. «Map N Winner» в вопросе → `map_winner`, `map_number` = N;
   2. счёт (`SCORELINE_RE`) → `other`;
   3. у события есть обе команды и (`sportsMarketType == "moneyline"` или вопрос совпал с `MATCH_RE`) → `series_winner`, `map_number` пустой;
   4. иначе → `other`.

   Отличие от Dota: ветки «`child_moneyline` без номера» нет. По факту 5 это мёртвый вход, а так у каждого `map_winner` есть N.
6. **У рынка серии нет номера карты, даже в BO1.** Dota ставит BO1 `game_number = 1` (`src/collect/s01_build_universe.py:293`). Для CS2 это лишнее: s02 и s06 решают через `series_winner_covers_map(best_of, best_of, map_winner_exists=…)` (`src/shared/utils/series_format.py:16`), и он уже покрывает BO1.
7. **Исход рынка** — `winning_token_index`:
   - 0 — рынок закрыт и цены ровно `1` / `0`;
   - 1 — рынок закрыт и цены ровно `0` / `1`;
   - пусто — в остальных случаях: открыт, `0.5`/`0.5`, нет цен.

   Правило — как `resolved_outcome_index` LoL (`src/lol/01_build_universe.py:442`) плюс проверка `closed`. Тот модуль нельзя импортировать: имя начинается с цифры. Поэтому пишем свою функцию на 8 строк. `umaResolutionStatus` не читаем: LoL его тоже не читает, а случай `proposed` один.
8. **`market_slug`, `seconds_delay`, `market_closed_at`** — только из `parse_market` (`src/shared/utils/polymarket.py:58`), всё или ничего, как Dota. Токены — только парой, как Dota.
9. **Имена колонок рынка — как в каталоге Dota** (`MatchCatalogRow`, `src/shared/types/dataset.py:8`): `condition_id`, `token_id_0`, `token_id_1`, `market_slug`, `market_closed_at`, `seconds_delay`. s06 переносит их без переименования.
10. **Проверки Dota остаются:** уникальный `condition_id`; не больше одного `series_winner` на событие; одно событие в двух источниках → `RuntimeError`; пустая таблица рынков → `RuntimeError`.
11. **Типы строк** — `TypedDict` в самом `s01_build_universe.py`, как `MarketContractRow` у Dota. s02 импортирует их оттуда. Отдельный `types.py` в `src/cs2` нельзя: при запуске файла из `src/cs2` он закрывает stdlib `types` (см. `scripts/run_lol_stage.py:1`).
12. **Без комментариев и docstring в новом коде**, без `# noqa` и `# pyright: ignore` (правило пользователя в `.feature-json.config.json`). Смысл несут имена.

## 4. Изменения

### 4.1 `src/cs2/constants.py` (новый)

```python
from shared.constants.paths import DATA_DIR

CS2_DATA_DIR = DATA_DIR / "cs2"
CS2_UNIVERSE_DIR = CS2_DATA_DIR / "universe"
CS2_LINKS_DIR = CS2_DATA_DIR / "links"
CS2_RAW_DIR = CS2_DATA_DIR / "raw"
CS2_GRID_DIR = CS2_DATA_DIR / "grid"
CS2_BO3_DIR = CS2_DATA_DIR / "bo3"
CS2_CATALOG_DIR = CS2_DATA_DIR / "catalog"
CS2_MARKET_SECONDS_DIR = CS2_DATA_DIR / "market_seconds"
CS2_DATASETS_DIR = CS2_DATA_DIR / "datasets"
CS2_MODELS_DIR = CS2_DATA_DIR / "models"

CS2_RAW_GAMMA_DIR = CS2_RAW_DIR / "gamma"
CS2_UNIVERSE_EVENTS_PATH = CS2_UNIVERSE_DIR / "events.parquet"
CS2_UNIVERSE_MARKETS_PATH = CS2_UNIVERSE_DIR / "markets.parquet"
CS2_UNIVERSE_MANIFEST_PATH = CS2_UNIVERSE_DIR / "universe_manifest.json"

CS2_GAMMA_TAG_ID = 100780
CS2_START_TIME = 1_760_140_800
```

- `DATA_DIR` — `src/shared/constants/paths.py:6` (корень репо / `data`). Новый корень не считаем.
- `CS2_START_TIME` = 2025-10-11 00:00:00 UTC. Формат — как `VALIDATION_START_TIME` в `src/shared/constants/dataset.py:3`. Я проверил: `datetime(2025,10,11,tzinfo=UTC).timestamp()` = 1760140800.
- Папки, которые s01 не читает (`links` … `models`), заданы по требованию шага: их возьмут STEP-003…STEP-012.
- В worktree `data/cs2/<name>` — симлинки на `../esports-trader/data/cs2/<name>`, `models` — настоящая папка (STEP-001). Код это не учитывает: пути одинаковые.

### 4.2 `src/cs2/s01_build_universe.py` (новый, ~200 строк)

Набросок. Порядок и имена обязательны, мелочи — на усмотрение исполнителя.

```python
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
import typer
from polymarket.models.gamma.event import Event
from polymarket.models.gamma.market import Market

from collect.common.catalog_types import MarketContractKind
from collect.s01_build_universe import (
    SCORELINE_RE,
    best_of,
    build_as_of_stamp,
    clean_text,
    fetch_query_pages,
    iso_datetime,
    read_structured_teams,
)
from cs2.constants import (
    CS2_GAMMA_TAG_ID,
    CS2_RAW_GAMMA_DIR,
    CS2_START_TIME,
    CS2_UNIVERSE_DIR,
    CS2_UNIVERSE_EVENTS_PATH,
    CS2_UNIVERSE_MANIFEST_PATH,
    CS2_UNIVERSE_MARKETS_PATH,
)
from shared.types.polymarket import GammaDiscoveryFilters
from shared.utils.filesystem import staging_directory
from shared.utils.json_io import write_json
from shared.utils.parquet_io import write_parquet
from shared.utils.polymarket import parse_market, read_polymarket_universe_page

MATCH_RE = re.compile(
    r"Counter-Strike\s*:\s*(?P<team_a>.+?)\s+v(?:s\.?|ersus)\s+"
    r"(?P<team_b>.+?)(?=\s*\(\s*BO\s*\d+\s*\)|\s+-\s+|$)",
    re.IGNORECASE,
)
MAP_WINNER_RE = re.compile(r"\bMap\s*(?P<map_number>\d+)\s+Winner\b", re.IGNORECASE)
```

Типы:

- `Cs2UniverseEventRow(TypedDict)`: `event_id: str`, `title: str | None`, `start_time: str` (ISO-8601 `…Z`), `best_of: int | None`, `team_a: str | None`, `team_b: str | None`, `grid_series_id: str | None`.
- `Cs2UniverseMarketRow(TypedDict)`: `condition_id: str`, `event_id: str`, `market_slug: str | None`, `contract_kind: MarketContractKind`, `map_number: int | None`, `token_id_0: str | None`, `token_id_1: str | None`, `outcome_0: str`, `outcome_1: str`, `seconds_delay: int | None`, `market_closed_at: str | None`, `winning_token_index: int | None`.
- `EVENT_COLUMNS = list(Cs2UniverseEventRow.__annotations__)`, `MARKET_COLUMNS` — так же.
- frozen dataclasses:
  - `DiscoveryQuery(source: str, filters: GammaDiscoveryFilters)`, как у LoL (`src/lol/01_build_universe.py:62`);
  - `StartWindow(start_ts: int, stop_ts: int)`, `stop_ts` не входит в окно;
  - `DatedEvent(event: Event, start_time: datetime)` — снимает `None` со `startTime` один раз, в фильтре;
  - `TeamPair(team_a: str | None, team_b: str | None)`;
  - `ContractClassification(contract_kind: MarketContractKind, map_number: int | None)`;
  - `UniverseTables(events: pd.DataFrame, markets: pd.DataFrame)`.

Функции:

1. `build_discovery_queries() -> list[DiscoveryQuery]` — две записи: `tag_100780_closed` с `{"tag_id": CS2_GAMMA_TAG_ID, "closed": "true"}` и `tag_100780_open` с `"false"`.
2. `refresh_raw_snapshot(raw_dir: Path, as_of: str) -> None` — копия логики Dota `refresh_raw_snapshot` (`src/collect/s01_build_universe.py:177`): `staging_directory(raw_dir, ".events.tmp-")`, для каждого запроса `fetch_query_pages(temp_root, query.source, query.filters, as_of)` и `print(f"complete {source}: {count} pages")`, потом `shutil.rmtree(raw_dir / "events", ignore_errors=True)` и `replace`. Удаляется только папка `events`, которую s01 сам создаёт.
3. `parse_window(value: str) -> StartWindow`:
   - `value.partition(":")`, затем `date.fromisoformat` для обеих частей;
   - нет `:`, `ValueError` или конец раньше начала → `typer.BadParameter(f"--window must be YYYY-MM-DD:YYYY-MM-DD, got {value}")`;
   - `start_ts` — полночь UTC дня START, `stop_ts` — полночь UTC дня END + 1.
4. `keep_start(start_ts: int, windows: list[StartWindow]) -> bool` — `start_ts >= CS2_START_TIME` и (`not windows` или `any(window.start_ts <= start_ts < window.stop_ts …)`).
5. `load_discovered_events(raw_dir: Path, windows: list[StartWindow]) -> list[DatedEvent]`:
   - страницы `sorted((raw_dir / "events" / query.source).glob("page_*.json.gz"))` по обоим запросам; ни одной → `FileNotFoundError(raw_dir / "events")`;
   - события — через `read_polymarket_universe_page(path).events`;
   - нет `schedule.start_time` или `keep_start` ложно → пропуск;
   - `event.id` уже был → `RuntimeError(f"duplicate event {event_id} across discovery sources")`, как Dota;
   - результат отсортирован по `(start_time, event.id)`.
6. `read_event_teams(event: Event) -> TeamPair` — сначала `read_structured_teams(event)` Dota; если не две команды, то `MATCH_RE.search(event.title or "")` и `clean_text` групп; нет совпадения → `TeamPair(None, None)`.
7. `classify_contract(market: Market, has_match_teams: bool) -> ContractClassification` — правила из решения 5. `question = clean_text(market.question) or ""`, `sports_type = (market.sports.sports_market_type or "").lower()`.
8. `read_winning_token_index(market: Market) -> int | None` — решение 7:
   ```python
   if not market.state.closed:
       return None
   prices = (market.outcomes.yes.price, market.outcomes.no.price)
   if prices == (Decimal(1), Decimal(0)):
       return 0
   if prices == (Decimal(0), Decimal(1)):
       return 1
   return None
   ```
9. `read_grid_series_id(event: Event) -> str | None` — `(event.metadata or {}).get("gridSeriesId")`. Значение — непустая `str`, иначе `None`.
10. `build_event_row(dated: DatedEvent, teams: TeamPair) -> Cs2UniverseEventRow` — `start_time` = `dated.start_time.astimezone(UTC).isoformat().replace("+00:00", "Z")` (или `iso_datetime` Dota; тогда тип `str | None`, и нужна проверка — выбрать первое).
11. `build_market_row(event_id: str, condition_id: str, market: Market, classification: ContractClassification) -> Cs2UniverseMarketRow`:
    - токены — как Dota `build_frames` (`src/collect/s01_build_universe.py:295-299`): пишем только полную пару;
    - `gamma = parse_market(market)`; `market_slug`, `seconds_delay`, `market_closed_at` — из `gamma` или все три `None`;
    - `outcome_0 = market.outcomes.yes.label`, `outcome_1 = market.outcomes.no.label`;
    - `winning_token_index = read_winning_token_index(market)`.
12. `build_universe_tables(events: list[DatedEvent]) -> UniverseTables`:
    - на каждое событие — строка события, `has_match_teams = teams.team_a is not None and teams.team_b is not None`;
    - рынок без `condition_id` → пропуск, как Dota;
    - `pd.DataFrame(rows, columns=…)`; `best_of`, `map_number`, `seconds_delay`, `winning_token_index` → `"Int64"`;
    - пустые рынки → `RuntimeError("CS2 universe has no markets")`;
    - дубль `condition_id` → `RuntimeError`;
    - больше одного `series_winner` на событие → `RuntimeError`, текст как у Dota (`src/collect/s01_build_universe.py:335-340`).
13. `main(...)`:
    ```python
    def main(
        as_of: Annotated[str, typer.Option(help="UTC stamp for this universe snapshot.")],
        window: Annotated[
            list[str],
            typer.Option("--window", default_factory=list, help="START:END UTC days, both inclusive. Repeatable."),
        ],
        fetch_universe: Annotated[bool, typer.Option("--fetch-universe")] = False,
        raw_dir: Annotated[Path, typer.Option(help="Raw Gamma cache dir.")] = CS2_RAW_GAMMA_DIR,
        output_dir: Annotated[Path, typer.Option(help="Published universe dir.")] = CS2_UNIVERSE_DIR,
    ) -> None:
    ```
    1. `as_of_stamp = build_as_of_stamp(as_of)`, `windows = [parse_window(value) for value in window]` — до любой загрузки.
    2. `if fetch_universe: refresh_raw_snapshot(raw_dir, as_of_stamp)`.
    3. `load_discovered_events` → `build_universe_tables`.
    4. `write_parquet` в `output_dir / CS2_UNIVERSE_EVENTS_PATH.name` и `output_dir / CS2_UNIVERSE_MARKETS_PATH.name`.
    5. `write_json(output_dir / CS2_UNIVERSE_MANIFEST_PATH.name, {"data_as_of": as_of_stamp, "windows": window})`. Окна в манифесте нужны: так видно, что universe неполный.
    6. Печать: `events`, `markets`, `map_winner`, `series_winner`, `events_with_grid_series_id`, `markets_with_winner`, `saved`.

    Запуск — `typer.Typer()` + `app.command(help="Build the CS2 Polymarket universe.")(main)`, как Dota (`src/collect/s01_build_universe.py:375-378`).

`--raw-dir` и `--output-dir` — как у Dota. Тест передаёт в них `tmp_path`. Пилот может с ними писать полный universe отдельно от оконного.

Без `sys.path.insert`: Makefile экспортирует `PYTHONPATH=src` (`Makefile:3`). Ручной запуск — `PYTHONPATH=src uv run python src/cs2/s01_build_universe.py …`.

### 4.3 `Makefile`

1. В `.PHONY` (строки 11–15) добавить `cs2-universe`.
2. После `lol-collect` (строка 126) — цель:
   ```make
   cs2-universe:  ## CS2 s01 Gamma universe (--fetch-universe): ARGS="--window 2026-10-01:2026-10-17 …"
   	$(PY) src/cs2/s01_build_universe.py --fetch-universe --as-of "$(AS_OF)" $(ARGS)
   ```
   Строк-комментариев не добавлять, только `##`-help.

### 4.4 `tests/test_cs2_universe.py` (новый)

Без сети. Фикстура — словари в форме сырого Gamma, как в `tests/test_lol_build_universe.py:57-107`: `outcomes`, `outcomePrices`, `clobTokenIds` — JSON-строки. Страница пишется через Dota `write_gzip_json(page_path(raw_dir, "tag_100780_closed", 0), {"events": [...]})` из `collect.s01_build_universe`. Затем тест вызывает `main(as_of="2026-10-18T00:00:00Z", window=…, fetch_universe=False, raw_dir=…, output_dir=…)` и читает parquet. Строки — через `replace_nulls_with_none` (`src/shared/utils/parquet_io.py:15`) и `cast(list[Cs2UniverseMarketRow], frame.to_dict(orient="records"))`, как `src/collect/s02_link_opendota.py:262`.

Фикстура — два события:

- **E1** `Counter-Strike: Alpha vs Beta (BO3) - Test League`. `startTime` `2026-10-17T23:00:00Z`, `teams` Alpha/Beta (с `id`), `eventMetadata.gridSeriesId = "2900001"`, `score` `0-0|2-1|Bo3`. Рынки:
  - c1 `Counter-Strike: Alpha vs Beta - Map 1 Winner`, `child_moneyline`, закрыт, цены `["0","1"]`, есть `slug`, `closedTime`, `secondsDelay`;
  - c2 `Counter-Strike: Alpha vs Beta (BO3) - Test League`, `moneyline`, закрыт, цены `["0.5","0.5"]`;
  - c3 `Games Total: O/U 2.5`, `totals`.
- **E2** — формат октября 2025: `Counter-Strike: Gamma vs Delta`, `startTime` `2026-10-18T00:30:00Z`, без `teams`, без `eventMetadata`, без `score`. Рынок c4 — тот же вопрос, без `sportsMarketType`, закрыт, цены `["1","0"]`.

Тесты:

1. `test_markets_get_kind_map_number_and_outcome` — без `--window`:
   - c1 → `map_winner`, `map_number` 1, `winning_token_index` 1;
   - c2 → `series_winner`, `map_number` пустой, `winning_token_index` пустой;
   - c3 → `other`;
   - c4 → `series_winner`, `winning_token_index` 0;
   - E1 `grid_series_id` `"2900001"`, `best_of` 3; E2 `team_a`/`team_b` = Gamma/Delta (из заголовка), `best_of` пустой.
2. `test_window_keeps_only_events_inside_it` — `window=["2026-10-01:2026-10-17"]`:
   - в events только E1: 23:00 последнего дня окна — внутри, 00:30 следующего дня — снаружи;
   - в markets нет c4;
   - манифест: `windows == ["2026-10-01:2026-10-17"]`.

Тест на `parse_window` с ошибкой не нужен: это одна проверка `typer.BadParameter`.

## 5. Крайние случаи

| Случай | Что делает s01 |
| --- | --- |
| Событие без `startTime` (фьючерсы, «Will G2 make a roster move») | пропуск, как Dota |
| Событие без двух команд и без `vs` в заголовке | рынки → `other` |
| Нет best-of ни в заголовке, ни в `score` (9 из 334 в октябре 2025) | `best_of` пустой; решает s02 |
| Заголовок и `score` расходятся по best-of (3 из 8 594) | берём заголовок, как Dota; s02 отбросит несовпадение с GRID |
| BO2 (`Draw (1-1)?`, `to win 2-0?`) | `other` по `SCORELINE_RE`; рынков карт BO2 нет в каталоге: `best_of_from_grid_format` не знает BO2 |
| `0.5`/`0.5` (отмена) или рынок открыт | `winning_token_index` пустой; s06 отбрасывает и пишет причину |
| Неполная пара токенов | `token_id_0`/`token_id_1` пустые, как Dota |
| Рынок закрылся без `closedTime`/`secondsDelay` | `market_slug`, `seconds_delay`, `market_closed_at` пустые, как Dota |
| Окна перекрываются | событие одно: `any()` |
| Окно вне 2025-10-11… или без событий | пустые рынки → `RuntimeError` |
| Событие закрылось между closed и open walk | его нет в снимке (так же у Dota). Для прошлых дат не важно; для свежего дня — перезапустить s01 |
| Событие в обоих walk | `RuntimeError`, как Dota |
| Два события на одну серию (`-dh2`, `-more-markets`) | s01 пишет оба; это правило s02 `resolve_duplicate_events` |

## 6. Проверка

Все команды — из `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`.

1. Формат и lint: `uv run ruff format src/cs2 tests/test_cs2_universe.py && uv run ruff check src/cs2 tests/test_cs2_universe.py`. Затем хук коммита на этих файлах: `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s01_build_universe.py tests/test_cs2_universe.py Makefile`.
2. Типы (весь проект, как pre-commit): `uv run python -m basedpyright` → 0 errors.
3. Комментарии: `grep -n "#\|\"\"\"" src/cs2/*.py tests/test_cs2_universe.py` → пусто. `# noqa` и `# pyright: ignore` запрещены.
4. Тесты шага и Dota-универса:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_universe.py tests/test_build_universe.py -q`.
5. Весь набор: `make test`. Допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol/raw`, см. `progress.txt`, STEP-001). Любое другое падение — исправить.
6. Help: `make help | grep cs2-universe`, `PYTHONPATH=src uv run python src/cs2/s01_build_universe.py --help` показывает `--window`.
7. Ошибка окна: `PYTHONPATH=src uv run python src/cs2/s01_build_universe.py --as-of 2026-10-18T00:00:00Z --window 2026-10-17:2026-10-01` → выход с ошибкой `--window …`, без сетевых запросов.
8. **Smoke на настоящем Gamma (разрешён планом).** Только открытый API Gamma, ~150–200 GET, ~20–40 МБ gzip в `data/cs2/raw/gamma/`. Папки `data/cs2/raw` и `data/cs2/universe` создал STEP-001, они пустые; s01 пишет только в свои `raw/gamma/events` и `universe/`. Старые `data/cs2/polymarket/` не трогать.
   1. `make cs2-universe AS_OF=<now>` — весь период.
   2. Записать в отчёт по месяцам: события, `series_winner`, `map_winner` по N, доля `winning_token_index`, доля `grid_series_id`.
   3. Офлайн-сверка, без `--fetch-universe`, вывод в свою папку `orchestrate/work/<agent>/`: `PYTHONPATH=src uv run python src/cs2/s01_build_universe.py --as-of <тот же> --window 2026-06-01:2026-10-16 --output-dir <work>/universe_jun_oct`. Ожидание по факту 9, ± несколько процентов: ~5 081 событие, ~5 076 `series_winner`, ~8 396 `map_winner`, `gridSeriesId` ~44%. В `data/cs2/universe` остаётся полный universe из шага 1.
   4. Если s01 упал на правиле Dota (два `series_winner`, дубль события): не ослаблять проверку молча. Записать событие и вопросы рынков в отчёт и остановиться.

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s01_build_universe.py`, `tests/test_cs2_universe.py`, `Makefile`. Данные в гит не идут (`data/cs2/*` в `.gitignore`, коммит `9208b671`). Сообщение по образцу последних коммитов, в конце `Co-Authored-By: <модель> <noreply@…>`. Без `git push`.

## 8. Риски и открытые вопросы

- **Импорт из шага Dota.** Если Dota изменит сигнатуру `fetch_query_pages` или `best_of`, сломается и CS2. Basedpyright и `tests/test_cs2_universe.py` это поймают. Копия кода (как у LoL) дала бы третью копию keyset-загрузки.
- **`limit` в сохранённом запросе.** `fetch_query_pages` пишет в страницу `limit: 500` (константа Dota), а Gamma отдаёт 100. Это только метаданные страницы.
- **Ранние месяцы.** В октябре 2025 нет `teams` и `gridSeriesId`, у части событий нет best-of. s01 это пропускает; s02 найдёт меньше серий. Цифры даст smoke (п. 6.8) и воронка s07.
- **Правило «Map N Winner» без `child_moneyline`.** Если Gamma сменит текст вопроса, рынок уйдёт в `other` молча. Сигнал — падение числа `map_winner` в выводе s01 и в s07.
- **`umaResolutionStatus = proposed`.** Цены `0`/`1` при `proposed` мы считаем исходом (1 рынок). Неверный исход отсечёт сверка s06: победитель GRID = bo3 = Polymarket.
