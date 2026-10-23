# STEP-003 — s02: событие Gamma ↔ серия GRID

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, один коммит.
Figma и дизайна нет.

## 1. Что делает шаг

s02 читает universe из s01 и привязывает каждое событие Polymarket к серии GRID:

- есть `gridSeriesId` в Gamma — берёт его (`grid_link_source = gamma`);
- нет — ищет серию в `allSeries` GRID с `titleId` 28 по окну ±4 ч, best-of и паре имён (`grid_link_source = allseries`).

Для каждой связи s02 пишет `team_a_token_index`: индекс токена, исход которого — команда `teams[0]` серии GRID.

Выход:

- `data/cs2/raw/grid_series/<YYYY-MM-DD>.json` — сырые страницы `allSeries` за день UTC;
- `data/cs2/links/grid_links.parquet` — строка на каждое событие universe: связь или статус отказа.

## 2. Что я проверил (факты)

Пробы лежат в `tasks/cs2-follow300/orchestrate/work/plan003/`: 4 запроса `allSeries` и 2 запроса Series State к GRID 18.10.

1. **Формат `allSeries` для CS2** (`probe_allseries.py`, `probe_day.py`). Запрос Dota `ALL_SERIES_QUERY` (`src/collect/s04_fetch_grid_starts.py:246`) с `titleId: "28"` работает. `format.name` — `best-of-1` / `best-of-3`, `nameShortened` — `Bo1` / `Bo3`. `best_of_from_grid_format` (`src/shared/utils/series_format.py:21`) понимает только `name`. День 2026-09-10 — 69 серий на 2 страницах по 50. Полдня 2026-10-17 — 20 серий на 1 странице. У всех 89 серий ровно 2 команды.
2. **Порядок и id команд.** 11 серий 2026-10-17 есть и в `allSeries`, и в кэше Series State циклов 1–2 (`data/cs2/grid/series_state/`). У всех 11 id команд совпадают, порядок `teams` одинаковый. Значит, id команды из Central Data — тот же id, что в Series State.
3. **Заглушки TBD.** 3 из 69 серий 2026-09-10 — `Flyquest` / `TYLOO` / `DEPO` против `TBD-2` (квалификации PGL). Series State по двум из них вернул `{"data": {}}`: серии не играли. Имена таких серий ни с чем не сходятся, отдельный фильтр не нужен.
4. **Тестовые серии.** В тот же день есть турнир `GRID-TEST`: `CS2-1` против `CS2-2`, BO1. С реальными именами не сходится, фильтр не нужен.
5. **Симуляция правил s02 на 2026-09-10** (`simulate_day.py`, события со стартом 04:00–20:00, чтобы окно ±4 ч лежало внутри дня, пустая таблица псевдонимов):
   - `gamma`: 22 события. 21 — `orient_outcomes` ориентирует пару, и поиск по `allSeries` находит тот же id, что Gamma (21 из 21). 1 — `NIP` против `Ninjas in Pyjamas`: ориентация `None`, это `token_team_unresolved`. Нужен псевдоним.
   - `allseries`: 57 событий. 19 — ровно один кандидат, все 19 пар имён верные на глаз, 6 из них в обратном порядке (`team_a_token_index = 1`). 38 — ноль кандидатов: турниры, которых нет в GRID (Roman Imperium Cup, Exort Fiesta, PGL Oceania с заглушкой TBD и т. п.). Неоднозначных нет.
   - 40 связей (21 + 19) указывают на 40 разных серий: споров за серию в этот день нет.
6. **Universe s01** (`data/cs2/universe/`, `data_as_of = 2026-10-18T00:36:29Z`):
   - 12 094 события, у 12 087 есть рынки `map_winner` или `series_winner`, у 7 — нет;
   - best-of: BO3 — 10 133, BO1 — 1 865, BO5 — 88, пусто — 7, BO2 — 1;
   - у BO1 0 рынков `map_winner` и 1 865 рынков серии. Значит, BO1 торгуется только через рынок серии;
   - `map_winner` по N: BO3 — карты 1 и 2 (по 9 599), BO5 — карты 1–4 (82, 82, 82, 70);
   - **порядок исходов внутри события одинаковый**: у 0 из 12 087 событий больше одной пары `(outcome_0, outcome_1)` среди рынков `map_winner`/`series_winner`. Одного `team_a_token_index` на событие достаточно;
   - имена исходов не всегда равны `team_a`/`team_b` события: 156 событий (366 рынков). В старых событиях `teams` Gamma — другая команда (`GodGenesis` против исхода `Gaimin Gladiators`), в новых — пробелы по краям (`Back to Back `);
   - `gridSeriesId` повторяется у 2 пар событий: `2970381` (Entropy–Honvéd, два разных турнира) и `2972804` (одни и те же команды, старт 00:00 и 07:00);
   - 65 событий со стартом после `data_as_of`.
7. **Dota-код, который подходит без правок:**
   - `grid_graphql`, `grid_token`, `ALL_SERIES_QUERY`, `GRID_CENTRAL_DATA_API`, `GRID_REQUEST_SLEEP_SECONDS = 3.2`, типы `GridAllSeriesResponse`, `GridSeriesNode` — `src/collect/s04_fetch_grid_starts.py:30,38,84,110,208,215,246`;
   - `PM_SERIES_START_WINDOW_SECONDS = 4 * 60 * 60` — `src/collect/s02_link_opendota.py:50`;
   - `orient_outcomes`, `PAIR_SCORE_MIN = 0.82`, `SIDE_SCORE_MIN = 0.72` — `src/shared/utils/team_names.py:7-8,145`;
   - `DECIDER_BEST_OF`, `series_winner_covers_map`, `best_of_from_grid_format` — `src/shared/utils/series_format.py:8,16,21`.
8. **Что из Dota не подходит как есть:**
   - `fetch_series_index` (`s04_fetch_grid_starts.py:315`) жёстко берёт `GRID_DOTA_TITLE_ID` и папку Dota;
   - `resolve_duplicate_events` (`s02_link_opendota.py:349`) типизирован под `SeriesProposal` OpenDota (`series.maps[0].match_id`);
   - `build_event_context` (`s02_link_opendota.py:217`) отбрасывает событие без «Map N Winner» до того, как добавляет рынок серии (`:234` раньше `:244`). Для CS2 это выкинуло бы все 1 865 BO1.

## 3. Решения

1. **Имена — только из исходов рынка.** И фильтр кандидата, и ориентация токенов — один вызов `orient_outcomes(outcome_0, outcome_1, teams[0].name, teams[1].name, CS2_TEAM_ALIASES)`. Не `None` — кандидат проходит, `True` → `team_a_token_index = 0`, `False` → `1`. `orient_outcomes` внутри зовёт `pick_pair_orientation`: это те же пороги 0,82 / 0,72 и запрет равных сумм, что у `match_series_names` Dota. Причина: исходы — это то, к чему привязан токен, а `team_a`/`team_b` события расходятся с ними у 156 событий (факт 6). Один источник имён — нет случая «кандидат прошёл по именам события, а токен не ориентировался».
2. **Источник `gamma` без окна и best-of.** Как в PLAN.md: «ошибочный id поймают шаги 2 и 6». s02 берёт серию по id из индекса дней и проверяет только ориентацию имён. Нет серии в индексе — `grid_series_not_indexed`. Ориентация `None` — `token_team_unresolved`.
3. **Индекс `allSeries` по дням UTC, а не одним окном, как в Dota.** Файл `data/cs2/raw/grid_series/<YYYY-MM-DD>.json` = `{"day", "fetched_at", "pages"}`. s02 качает только дни, которые задевает окно `[start − 4 ч, start + 4 ч]` хотя бы одного события. Причина: пилот гоняет s01 с двумя окнами (апрель и октябрь). Одно окно `[min, max]` скачало бы апрель–октябрь целиком и перекачивало бы всё при каждом новом `--as-of`. Дни переиспользуются между полным прогоном и пилотом.
4. **День окончательный**, если `fetched_at ≥ начало дня + 48 ч`. С `--fetch` s02 перекачивает отсутствующие и неокончательные дни. Без `--fetch` работает только с кэшем: нет файла дня — `FileNotFoundError` с подсказкой про `--fetch`. Причина: серии публикуются и переносятся до конца дня. День, скачанный в пилоте 17.10, иначе остался бы без поздних серий.
5. **Запрос дня**: фильтр Dota (`titleId`, `startTimeScheduled {gte, lte}`, `workflowStatuses: ["PUBLISHED"]`), `gte` = 00:00 дня, `lte` = 00:00 следующего дня, `first: 50`, пауза `GRID_REQUEST_SLEEP_SECONDS` после каждой страницы. Серия ровно на полуночи попадёт в два дня, при загрузке s02 убирает дубли по id.
6. **Кандидаты `allseries`**: `|startTimeScheduled − start_time| ≤ PM_SERIES_START_WINDOW_SECONDS` (граница 4 ч включительно, как `> …` в Dota `:313`), `best_of_from_grid_format(format.name) == best_of` события, `orient_outcomes` не `None`. Окно ищет `bisect` по отсортированным стартам серий. Перебор всех пар — до ~12 тыс. × ~15 тыс., это лишние минуты.
7. **Какие события участвуют.**
   - best-of не в `DECIDER_BEST_OF` (1, 3, 5) — `unsupported_best_of` (8 событий: пусто и BO2). У GRID формат BO2 не разбирается, и s04 без best-of не найдёт карту рынка серии.
   - `map_market_count = 0` — `no_map_market`. `map_market_count` = число `map_winner` с `1 ≤ N ≤ best_of` плюс 1, если есть рынок серии и `series_winner_covers_map(best_of, best_of, map_winner_exists=best_of in N)`. Так BO1 с одним рынком серии получает 1 и участвует (отличие от Dota, факт 8).
   - Это же число решает спор двух событий за одну серию: «больше рынков карт», как `len(map_condition_ids)` у Dota, где решающая карта через рынок серии тоже входит.
8. **Статус — одна колонка `status`**, без отдельной `reason`: `matched`, `no_candidates`, `ambiguous`, `duplicate`, `token_team_unresolved`, `grid_series_not_indexed`, `unsupported_best_of`, `no_map_market`. `ambiguous` от двух кандидатов и `ambiguous` от ничьей в споре различает `candidate_count`: 2+ против 1. s07 строит воронку прямо по `status`.
9. **Спор двух событий за серию** — копия логики `resolve_duplicate_events` Dota на типах CS2. Спорят только события со статусом `matched` из обоих источников. Ключ — `series_id`. Больше `map_market_count` — побеждает, проигравший получает `duplicate` и `duplicate_of = <event_id победителя>`. Ничья — все спорщики получают `ambiguous`.
10. **Одна таблица на событие, а не две, как в Dota** (links + audit). `grid_links.parquet` — строка на каждое событие universe. У `matched` заполнены поля связи, у остальных они пустые. s03 берёт строки `status == "matched"`. `grid_link_source` заполнен у всех строк: `gamma`, если у события есть `gridSeriesId`, иначе `allseries`. Так s07 делит и отказы по источнику.
11. **В строке связи — id команд GRID**, не только индекс токена: `team_a_grid_id`, `team_b_grid_id` и имена. Команда A — `teams[0]` узла `allSeries`. Порядок `teams` в Series State совпал в 11 из 11 (факт 2), но s06 должен считать `team_a_win` по id, а не по позиции. Это передаётся в STEP-004 и STEP-008 (раздел 8).
12. **Псевдонимы.** `CS2_TEAM_ALIASES: dict[str, tuple[str, ...]] = {}` в `src/cs2/constants.py`, пустая, как требует шаг. Первый кандидат на строку уже виден: `"nip": ("ninjas in pyjamas",)` (факт 5). Его добавит пилот по аудиту, не этот шаг.
13. **Повторное использование.** Импорт из Dota, без копий и без правок Dota-кода, как s01 (`src/cs2/s01_build_universe.py:15`). Своё в s02 — только цикл дней (Dota-функция жёстко держит `titleId` 2) и спор за серию (другие типы).
14. **`team_a_token_index` на событие, а не на рынок.** Порядок исходов внутри события одинаковый (факт 6). s02 проверяет это при загрузке: две разные пары исходов у одного события — `RuntimeError`, как проверки инвариантов в s01 (`s01_build_universe.py:313-320`).

## 4. Изменения

### 4.1 `src/cs2/constants.py`

Добавить:

```python
CS2_RAW_GRID_SERIES_DIR = CS2_RAW_DIR / "grid_series"
CS2_GRID_LINKS_PATH = CS2_LINKS_DIR / "grid_links.parquet"

CS2_GRID_TITLE_ID = "28"
CS2_TEAM_ALIASES: dict[str, tuple[str, ...]] = {}
```

### 4.2 `src/cs2/s02_link_grid.py` (новый, ~250 строк)

Импорты:

```python
from collect.s02_link_opendota import PM_SERIES_START_WINDOW_SECONDS
from collect.s04_fetch_grid_starts import (
    ALL_SERIES_QUERY,
    GRID_CENTRAL_DATA_API,
    GRID_REQUEST_SLEEP_SECONDS,
    GridAllSeriesResponse,
    GridSeriesNode,
    grid_graphql,
    grid_token,
)
from cs2.constants import (
    CS2_GRID_LINKS_PATH, CS2_GRID_TITLE_ID, CS2_LINKS_DIR, CS2_RAW_GRID_SERIES_DIR,
    CS2_TEAM_ALIASES, CS2_UNIVERSE_DIR, CS2_UNIVERSE_EVENTS_PATH, CS2_UNIVERSE_MARKETS_PATH,
)
from shared.utils.json_io import read_json, write_json
from shared.utils.parquet_io import replace_nulls_with_none, write_parquet
from shared.utils.parsing import parse_ts
from shared.utils.series_format import DECIDER_BEST_OF, best_of_from_grid_format, series_winner_covers_map
from shared.utils.team_names import orient_outcomes
```

Типы (стиль s01: `TypedDict` для строки parquet, frozen dataclass для своих значений):

```python
Cs2GridLinkStatus = Literal[
    "matched", "no_candidates", "ambiguous", "duplicate",
    "token_team_unresolved", "grid_series_not_indexed",
    "unsupported_best_of", "no_map_market",
]
GridLinkSource = Literal["gamma", "allseries"]
TeamATokenIndex = Literal[0, 1]

class Cs2GridLinkRow(TypedDict):
    event_id: str
    start_time: str
    best_of: int | None
    grid_link_source: GridLinkSource
    status: Cs2GridLinkStatus
    candidate_count: int
    duplicate_of: str | None
    series_id: str | None
    series_start_scheduled: str | None
    team_a_token_index: int | None
    team_a_grid_id: str | None
    team_b_grid_id: str | None
    team_a_grid_name: str | None
    team_b_grid_name: str | None

class Cs2SeriesIndexDay(TypedDict):
    day: str
    fetched_at: str
    pages: list[GridAllSeriesResponse]

@dataclass(frozen=True)
class GridSeries:
    series_id: str
    start_ts: int
    start_scheduled: str
    best_of: int | None
    team_a_grid_id: str
    team_a_grid_name: str
    team_b_grid_id: str
    team_b_grid_name: str

@dataclass(frozen=True)
class SeriesIndex:
    by_id: dict[str, GridSeries]
    by_start: list[GridSeries]
    starts: list[int]

@dataclass(frozen=True)
class EventContext:
    event_id: str
    start_time: str
    start_ts: int
    best_of: int | None
    grid_series_id: str | None
    outcome_0: str | None
    outcome_1: str | None
    map_market_count: int

@dataclass(frozen=True)
class SeriesClaim:
    series: GridSeries
    team_a_token_index: TeamATokenIndex

@dataclass(frozen=True)
class LinkDecision:
    status: Cs2GridLinkStatus
    candidate_count: int
    claim: SeriesClaim | None
    duplicate_of: str | None
```

`outcome_0/1` равны `None` только у событий без рынков карт и серии. Такие события получают `no_map_market` раньше, чем код читает исходы. Если basedpyright требует сужения типа, сузить в одном месте: в `orient_series` через ранний `RuntimeError`.

Функции (имена — глаголы, все аргументы обязательные):

1. `list_index_days(events: list[EventContext]) -> list[date]` — дни UTC от `start_ts − PM_SERIES_START_WINDOW_SECONDS` до `start_ts + PM_SERIES_START_WINDOW_SECONDS` каждого события, без повторов, по порядку.
2. `build_day_path(raw_dir: Path, day: date) -> Path` — `raw_dir / f"{day.isoformat()}.json"`.
3. `is_day_final(day: date, fetched_at: datetime) -> bool` — `fetched_at >= полночь(day) + 2 дня`.
4. `fetch_day_pages(token: str, day: date) -> list[GridAllSeriesResponse]` — цикл страниц, как `fetch_series_index` Dota (`s04_fetch_grid_starts.py:315-344`), с фильтром из решения 5 и `CS2_GRID_TITLE_ID`. Пауза `GRID_REQUEST_SLEEP_SECONDS` после каждой страницы.
5. `refresh_series_index(raw_dir: Path, days: list[date], now: datetime) -> None` — для каждого дня: файл есть и `is_day_final` — пропуск; иначе `fetch_day_pages` и `write_json(Cs2SeriesIndexDay)`. `write_json` пишет через `.tmp` и `replace`. Печать: сколько дней скачано, сколько взято из кэша.
6. `parse_series_node(node: GridSeriesNode) -> GridSeries | None` — `None`, если команд не 2. `best_of = best_of_from_grid_format(node["format"]["name"])`, `start_ts` через `parse_ts`.
7. `load_series_index(raw_dir: Path, days: list[date]) -> SeriesIndex` — читает файлы дней, разбирает узлы, убирает дубли по id, сортирует по `start_ts`.
8. `count_map_markets(best_of: int, map_numbers: list[int], has_series_market: bool) -> int` — решение 7.
9. `load_event_contexts(events: pd.DataFrame, markets: pd.DataFrame) -> list[EventContext]` — рынки `map_winner`/`series_winner` по событию. Пара исходов: одна на событие, иначе `RuntimeError` (решение 14). `map_market_count` через `count_map_markets`, у неподдержанного best-of — 0. Перед `to_dict` прогнать `replace_nulls_with_none`, как тест s01.
10. `orient_series(event: EventContext, series: GridSeries) -> SeriesClaim | None` — решение 1.
11. `find_window_series(index: SeriesIndex, start_ts: int) -> list[GridSeries]` — `bisect_left` / `bisect_right` по `index.starts` для `start_ts ± PM_SERIES_START_WINDOW_SECONDS`.
12. `decide_event_link(event: EventContext, index: SeriesIndex) -> LinkDecision`:

```python
if event.best_of not in DECIDER_BEST_OF:
    return LinkDecision("unsupported_best_of", 0, None, None)
if event.map_market_count == 0:
    return LinkDecision("no_map_market", 0, None, None)
if event.grid_series_id is not None:
    series = index.by_id.get(event.grid_series_id)
    if series is None:
        return LinkDecision("grid_series_not_indexed", 0, None, None)
    claim = orient_series(event, series)
    if claim is None:
        return LinkDecision("token_team_unresolved", 1, None, None)
    return LinkDecision("matched", 1, claim, None)
claims = [
    claim
    for series in find_window_series(index, event.start_ts)
    if series.best_of == event.best_of
    and (claim := orient_series(event, series)) is not None
]
if not claims:
    return LinkDecision("no_candidates", 0, None, None)
if len(claims) > 1:
    return LinkDecision("ambiguous", len(claims), None, None)
return LinkDecision("matched", 1, claims[0], None)
```

(Код выше — набросок логики. Реализатор пишет его в стиле файла: именованные промежуточные переменные, именованные аргументы dataclass, без моржа, если ruff/читаемость против.)

13. `resolve_duplicate_events(events: dict[str, EventContext], decisions: dict[str, LinkDecision]) -> dict[str, LinkDecision]` — решение 9. Проигравший сохраняет `candidate_count`, `claim = None`.
14. `build_link_row(event: EventContext, decision: LinkDecision) -> Cs2GridLinkRow` — поля связи из `decision.claim`, иначе `None`. `grid_link_source` по решению 10.
15. `build_link_table(events: list[EventContext], index: SeriesIndex) -> pd.DataFrame` — решения по всем событиям → `resolve_duplicate_events` → строки. Колонки `best_of`, `team_a_token_index` приводит к `Int64` (как `EVENT_INTEGER_COLUMNS` в s01). Сортировка по `start_time`, `event_id`. Проверки: `event_id` уникален; у `matched` `series_id` уникален — иначе `RuntimeError`; нет ни одного `matched` — `RuntimeError("no GRID links were accepted")`, как `validate_links` Dota (`s02_link_opendota.py:455`).
16. `main(...)` — typer, как s01:

```python
def main(
    fetch: Annotated[bool, typer.Option("--fetch", help="Fetch missing and non-final allSeries days.")] = False,
    universe_dir: Annotated[Path, typer.Option(help="s01 universe dir.")] = CS2_UNIVERSE_DIR,
    raw_dir: Annotated[Path, typer.Option(help="allSeries day cache dir.")] = CS2_RAW_GRID_SERIES_DIR,
    output_dir: Annotated[Path, typer.Option(help="Published links dir.")] = CS2_LINKS_DIR,
) -> None:
```

Порядок: прочитать `events.parquet` и `markets.parquet` из `universe_dir` → `load_event_contexts` → `list_index_days` → при `--fetch` `refresh_series_index(raw_dir, days, datetime.now(UTC))` (токен `grid_token()` только при `--fetch`) → `load_series_index` → `build_link_table` → `write_parquet(output_dir / CS2_GRID_LINKS_PATH.name)`.

Печать: число событий, дней индекса, серий в индексе; таблица `status × grid_link_source` (`pd.crosstab`); число связанных серий; путь.

### 4.3 `Makefile`

- В `.PHONY` добавить `cs2-link`.
- Цель после `cs2-universe`:

```make
cs2-link:  ## CS2 s02 Gamma event <-> GRID series (--fetch allSeries days, titleId 28)
	$(PY) src/cs2/s02_link_grid.py --fetch $(ARGS)
```

### 4.4 `tests/test_cs2_link_grid.py` (новый)

Без сети. Помощники строят `GridSeries`, `EventContext` и `SeriesIndex` в памяти. Один тест `main` пишет фикстуру universe и файлы дней в `tmp_path`.

1. `gamma`: `gridSeriesId` есть в индексе, исходы в обратном порядке к `teams` GRID → `matched`, `grid_link_source = gamma`, `team_a_token_index = 1`, `team_a_grid_id` = id `teams[0]`.
2. `gamma`: id нет в индексе → `grid_series_not_indexed`.
3. `gamma`: имена не ориентируются → `token_team_unresolved` (шаг требует именно этот тест).
4. `allseries`: один кандидат → `matched`, `team_a_token_index = 0`.
5. Два кандидата с теми же командами в окне → `ambiguous`, `candidate_count = 2`.
6. Ноль кандидатов → `no_candidates`.
7. Окно: серия на `start + 4 ч` проходит, на `start + 4 ч + 1 с` — нет.
8. Другой best-of (BO1 против BO3 события) → кандидат отсекается.
9. Спор: два события на одну серию, у одного `map_market_count = 3`, у другого 2 → первый `matched`, второй `duplicate` с `duplicate_of`. Равные числа → оба `ambiguous`.
10. `count_map_markets`: BO1 только с рынком серии → 1; BO3 с картами 1, 2 и рынком серии → 3; BO3 только с картами 1, 2 → 2.
11. `unsupported_best_of` для best-of `None` и 2; `no_map_market` для события без рынков карт и серии.
12. `is_day_final`: `fetched_at` = полночь + 47:59:59 → `False`, полночь + 48 ч → `True`. `list_index_days`: событие в 02:00 задевает предыдущий день.
13. `parse_series_node`: узел с одной командой → `None`; `best-of-3` → 3.
14. `main` на `tmp_path` (`fetch=False`): строка на каждое событие, `team_a_token_index` и `best_of` типа `Int64`, у `matched` заполнены `series_id` и id команд.

## 5. Крайние случаи

- **Рынок серии в BO1 без «Map 1 Winner».** Событие участвует: `map_market_count = 1` (решение 7).
- **Событие без рынков карт и серии** (7 событий) — `no_map_market`, исходы не читаются.
- **BO2 и пустой best-of** — `unsupported_best_of`, даже при `gridSeriesId`.
- **Заглушка TBD и `GRID-TEST`** — обычные кандидаты, имена не проходят. Кода для них нет.
- **Серия на полуночи** — в двух файлах дней, при загрузке одна.
- **Событие после `data_as_of`** — дни будущего тоже качаются, серии там запланированы. Отдельного правила нет. День не окончательный и перекачивается при следующем `--fetch`.
- **Один `gridSeriesId` у двух событий** (2 случая) — спор за серию. `2970381` при равных рынках даст два `ambiguous`, и правильное событие тоже выпадет. Так требует шаг.
- **Событие `gamma` и событие `allseries` на одну серию** — тот же спор. На 2026-09-10 поиск по именам нашёл id Gamma в 21 из 21, поэтому такие споры редки.
- **Перенос серии в GRID дальше 4 ч от старта Gamma** — для `allseries` это `no_candidates`. Для `gamma` серия найдётся, если её день попал в индекс. Иначе `grid_series_not_indexed`.
- **Диакритика.** `normalize_team_name` выбрасывает `é` и `ö` (`Honvéd` → `honv d`). Обе стороны проходят одну функцию, поэтому одинаковое написание сходится. `Sökerorg` против `Sokerorg` даёт 0,875 > 0,72.
- **Пробелы в исходах** (`Back to Back `) — `normalize_team_name` их убирает.

## 6. Проверка

Команды из корня `esports-trader-cs2`:

1. `uv run ruff format src/cs2 tests/test_cs2_link_grid.py` и `uv run ruff check src/cs2 tests/test_cs2_link_grid.py` — без ошибок.
2. `uv run python -m basedpyright` (весь проект, как pre-commit) — 0 errors.
3. `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s02_link_grid.py tests/test_cs2_link_grid.py Makefile` — passed.
4. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
5. `PYTEST_N=8 make test` — допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol`, см. progress.txt).
6. **Короткий живой прогон на одном дне** (~6 запросов к GRID, всё в scratch, реальные `data/cs2/links` и `data/cs2/universe` не трогать):

   ```bash
   S=../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl003
   PYTHONPATH=src uv run python src/cs2/s01_build_universe.py --as-of 2026-10-18T00:36:29Z \
     --window 2026-09-10:2026-09-10 --output-dir $S/universe
   PYTHONPATH=src uv run python src/cs2/s02_link_grid.py --fetch \
     --universe-dir $S/universe --raw-dir $S/grid_series --output-dir $S/links
   ```

   s01 без `--fetch-universe` только читает кэш `data/cs2/raw/gamma`. Ожидание для событий со стартом 04:00–20:00 — цифры симуляции (факт 5): `gamma` — 21 `matched` и 1 `token_team_unresolved` (NIP), `allseries` — 19 `matched` и 38 `no_candidates`. События 00:00–04:00 и 20:00–24:00 добавят строки сверх этого. Расхождение в этой части — ошибка в коде, её надо разобрать до коммита. Повторный запуск без `--fetch` даёт ту же таблицу.
7. Полный прогон s02 на весь период — не в этом шаге. Это ~375 дней × 1–2 страницы × 3,2 с ≈ 30 мин запросов. Его делает пилот (STEP-014).

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s02_link_grid.py`, `tests/test_cs2_link_grid.py`, `Makefile`. Сообщение — в стиле `8b8f0c2a`, например «Link CS2 Gamma events to GRID series.», тело 2–3 строки, последняя — `Co-Authored-By: <модель> <noreply@...>`. Без `git push`. `passes` в `feature.json` не менять: это делает оркестратор после ревью.

## 8. Риски и открытые вопросы

- **Ориентация команды A в следующих шагах.** s02 определяет команду A как `teams[0]` узла `allSeries` и пишет её id. s03 (STEP-004) пишет `teams[0]`/`teams[1]` из Series State. Порядок совпал в 11 из 11, но это не гарантия. План STEP-008 должен считать `team_a_win` по `team_a_grid_id`, а не по позиции. Карту, где этого id нет среди команд Series State, s06 должен отбрасывать с причиной.
- **Псевдонимы.** Таблица пустая. На одном дне пропал хороший `gamma`-матч `NIP` / `Ninjas in Pyjamas`. Пилот должен посмотреть строки `token_team_unresolved` и `no_candidates` с близкими именами и добавить строки в `CS2_TEAM_ALIASES`.
- **Доля `allseries` мала.** На 2026-09-10 связались 19 из 57 событий без `gridSeriesId`. Остальные — турниры, которых нет в GRID. Это не ошибка s02, но популяция каталога будет в основном из событий с `gridSeriesId`. Точную цифру по месяцам даст s07.
- **`grid_series_not_indexed`.** Если GRID запланировал серию `gamma` в другой день, чем окно события, s02 её не найдёт. Запасного запроса `series(id)` в Central Data нет (YAGNI). Если пилот покажет заметное число таких строк, добавить запрос по id только для них.
- **Лимиты GRID.** Полный индекс — ~500–600 запросов с паузой 3,2 с. `grid_graphql` повторяет запрос 5 раз и ждёт сброса лимита. Длинная пауза GRID растянет пилот, но данные не испортит: дни пишутся по одному.
- **Связь с Dota-кодом.** s02 импортирует из `collect.s04_fetch_grid_starts` и `collect.s02_link_opendota`. Правка их сигнатур сломает CS2. Это поймают basedpyright и `tests/test_cs2_link_grid.py`.
- **`startTimeScheduled` — план, а не факт.** Окно ±4 ч считается от двух плановых времён, как у Dota (там от старта карты OpenDota). На 19 связях 2026-09-10 разница была от −100 до +15 мин.
- Я не запускал новый код: фаза плана не пишет код.
