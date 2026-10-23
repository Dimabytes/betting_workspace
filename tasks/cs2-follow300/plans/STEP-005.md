# STEP-005 — s04: карта GRID ↔ карта bo3 по отпечатку раундов

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, один коммит.
Figma и дизайна нет.

## 1. Что делает шаг

s04 находит карту bo3 для каждой торгуемой карты GRID без имён команд. Ключ — отпечаток: название карты и строка сторон-победителей раундов (`C` / `T`).

Вход — только выходы s01–s03:

- `data/cs2/links/grid_links.parquet` (s02): строки `status == "matched"` — `event_id`, `series_id`, `best_of`;
- `data/cs2/universe/markets.parquet` (s01): рынки события;
- `data/cs2/grid/games.parquet` и `data/cs2/grid/rounds.parquet` (s03).

Выход:

- `data/cs2/raw/bo3/matches/<YYYY-MM-DD>.json` — список матчей bo3 за день UTC, ответы API как есть;
- `data/cs2/raw/bo3/games/<match_id>.json` — карты и раунды матча bo3, ответ API как есть;
- `data/cs2/bo3/links.parquet` — строка на карту GRID связанной серии: связь или причина отказа;
- аудит причин в stdout.

Ещё s04 даёт функцию «рынок → карта GRID» (`build_map_markets`). Её результат (`event_id`, `condition_id`) s04 пишет в `links.parquet`.

## 2. Что я проверил (факты)

Скрипты лежат в `tasks/cs2-follow300/orchestrate/work/plan005/`. К `api.bo3.gg` я отправил 18.10 два запроса списка и пять запросов карт (`probe_list.py`, `probe_games.py`, `probe_latency.py`, `offline_fp.py`).

1. **Фильтр списка `[gte]` API молча игнорирует.** Запрос `filter[matches.start_date][gte]=2026-10-05…` + `[lt]=2026-10-06…` вернул `total.count = 79 396`, первые матчи — 2020 год. С `[gt]` + `[lt]` за 2026-10-05 пришло 24 матча: тир C — 13, S — 8, B — 3; статусы `finished` и `defwin`. Работает только `[gt]`. Загрузчик `scripts/cs2/fetch_bo3.py:346` тоже берёт `gt` = граница − 1 с.
2. **Список без `with=teams,tournament`** отдаёт `id`, `tier`, `start_date`, `end_date`, `status`, `team1_id`, `team2_id`, без объектов команд. Имена s04 не нужны.
3. **Карты матча** — `GET /games?with=game_rounds&filter[games.match_id][eq]=<id>`, один запрос на матч. Ответ с раундами — 50–67 КБ, 0,1–0,3 с на запрос. Матч тира C `131237`: 2 карты, `rounds_count` 18 и 21, `game_rounds = []`. Матч `defwin` `131141`: 0 карт.
4. **Старый сырой кэш циклов 1–2** (`data/cs2/bo3/matches/`, 2 948 файлов, тиры S/A/B, июнь–октябрь 2026):
   - `game_rounds` — всегда список: пустой у 433 карт, с раундами у 4 890;
   - `winner_clan_side` — только `CT` и `T`;
   - в раунде 1 у 4 883 карт есть кланы `CT` и `T` с именем, у 3 карт список кланов пуст;
   - `number` — всегда целое; `total.count` ≤ числа результатов на странице всегда;
   - номера раундов идут 1…n без дыр у 4 886 карт из 4 890 (`data/cs2/bo3/rounds.parquet`).
5. **id команды в раундах API нет.** У клана раунда есть только `team_side` и `clan_name` (`game_round_team_clans`). Колонку `ct_team_id` старого `rounds.parquet` скрипт выводил по именам (`link_clans_to_teams`, `scripts/cs2/fetch_bo3.py:731`). 2D-реплей несёт `team_counter_terrorists.clan_name` и `team_terrorists.clan_name` (`data/cs2/bo3/replays/173713/1.json`). Значит, команда в bo3 — это `clan_name` внутри одной карты.
6. **Названия карт.** s03 (проба STEP-004, `work/impl004/grid/games.parquet`, 2026-09-10, 102 карты) пишет `dust2` и `default-dust2`: у 39 карт из 16 серий префикс `default-`. В старом кэше GRID префикса `default-` нет. bo3: `de_dust2`, одна карта `cache`, 17 карт без названия (`data/cs2/bo3/maps.parquet`).
7. **`begin_at` карты bo3 минус `start_date` её матча** (5 293 карты, `maps.parquet` × `matches.parquet`): минимум −0,5 ч, медиана 0,84 ч, p99,9 4,11 ч, максимум 12,12 ч. При окне ±4 ч кандидат лежит в матче со `start_date` в [t − 16,2 ч, t + 4,5 ч].
8. **Офлайн-проверка отпечатка** (`offline_fp.py`): таблицы s03 за 2026-09-10 против старого кэша bo3 (только тиры S/A/B):
   - отпечаток есть у 97 карт GRID, 5 карт «грязные»;
   - связь — 41, `no_bo3_map` — 56, `ambiguous_bo3` — 0;
   - пример: `2992184` карта 2 `mirage` = карта bo3 `185162`, строки совпали;
   - два промаха проверены вживую (`129075`, `129074`, тир C): карты bo3 есть, `game_rounds` пуст;
   - из 39 карт `default-` после нормализации связались 17.
9. **Все 102 карты пробы 2026-09-10 торгуются** по правилу «рынок → карта» (`traded_maps.py`): `excluded_series_rule` — 0.
10. **Рынки universe** (`data/cs2/universe/markets.parquet`): у 12 086 из 12 087 событий с торгуемыми рынками ровно один рынок серии, у 1 — ни одного. Дублей «Map N Winner» в событии — 0. `map_number` > `best_of` — 0.
11. **Объём bo3** (`investigations/2026-10-17-cs2-bo3-coverage/work/bo3_matches.parquet`): 16 715 матчей с 2025-10-10 по 2026-10-17. Тиры: C — 9 635, B — 5 776, S — 851, A — 453. `defwin` — 1 580. По месяцам 680–1 923. Это ~17 000 запросов карт: 30–85 мин подряд при 0,1–0,3 с на запрос.
12. **Код для повторного использования:**
    - `get_json` (`src/shared/utils/http.py:21`): tenacity, 5 попыток, ожидание 1–30 с, на 429 спит `retry-after`;
    - `cs2.s02_link_grid`: `read_traded_markets` (`:300`), `utc_midnight` (`:153`), `is_day_final` (`:157`), `parse_fetched_at` (`:192`);
    - `cs2.s03_fetch_grid_rounds`: `parse_utc` (`:267`), `format_utc` (`:271`), `apply_table_dtypes` (`:395`), типы `Cs2GridGameRow`, `Cs2GridRoundRow`;
    - `series_winner_covers_map` (`src/shared/utils/series_format.py:16`);
    - образец правила — `build_event_context` Dota (`src/collect/s02_link_opendota.py:217`).
13. **`scripts/` не на пути импорта целей make.** `Makefile:3` ставит `PYTHONPATH=src`, `scripts` добавляет только `make test`. Поэтому s04 не импортирует `scripts/cs2/fetch_bo3.py`. Заголовки запроса s04 копирует из `scripts/cs2/fetch_bo3.py:27`.
14. **`data/cs2/bo3/` уже занята** файлами циклов 1–2: `maps.parquet`, `rounds.parquet`, `matches.parquet`, `matches/`, `list/`, `replays/` (2,1 ГБ). Файла `links.parquet` там нет. `data/cs2/raw/` содержит только `gamma`.

## 3. Решения

1. **Функция «рынок → карта GRID»** — `build_map_markets(best_of, markets) -> dict[int, Cs2UniverseMarketRow]` в s04, как `build_event_context` Dota:
   - «Map N Winner» с 1 ≤ N ≤ best_of — карта N;
   - рынок серии — карта с номером best_of, если `series_winner_covers_map(best_of, best_of, map_winner_exists=…)`;
   - других карт рынок серии не получает.

   Карта GRID связанной серии, номера которой нет в словаре, получает статус `excluded_series_rule`. Это карты 1–2 BO3 с одним рынком серии и 1 событие без рынка серии (факт 10).
2. **s04 пишет результат функции.** В строке связи есть `event_id` и `condition_id`. s05 и s06 читают их из `links.parquet` и не считают правило заново. Функция остаётся публичной: s06 может её импортировать, если ей нужна строка рынка. Шаг говорит «эту же функцию зовут s05 и s06». Колонка — тот же результат той же функции, правило одно.
3. **Какие карты GRID получают строку.** Каждая карта `games.parquet` серии из строки `matched` s02. Несыгранной карты в GRID нет, строки для неё нет, как в Dota. Сопоставление с bo3 идёт только для карт с рынком.
4. **Отпечаток GRID.** Он есть, только если:
   - карта `finished`;
   - номера раундов идут 1…n без дыр;
   - каждый раунд `finished`, и победитель ровно один (`team_a_won != team_b_won`);
   - n = `team_a_score` + `team_b_score`.

   Иначе статус `grid_rounds_incomplete`. Сторона победителя раунда: `team_a_side`, если выиграла команда A, иначе `team_b_side`. `CT` → `C`, `T` → `T`. Так s04 выполняет передачу из плана STEP-004: раунд без ровно одного победителя — не отпечаток.
5. **Отпечаток bo3.** Карта bo3 — кандидат, только если `game_rounds` не пуст, номера раундов 1…n без дыр, у каждого раунда `winner_clan_side` — `CT` или `T`, есть `map_name` и `begin_at`. Иначе карта не кандидат; s04 считает такие карты в stdout.
6. **Нормализация названия:** `name.lower().removeprefix("default-").removeprefix("de_")` для GRID и bo3 (факт 6). В `links.parquet` идёт нормализованное название: s05 возьмёт его в мету карты.
7. **Связь.** Ключ — frozen dataclass `FingerprintKey(map_name, fingerprint)`. Кандидаты — карты bo3 с тем же ключом и |`begin_at` − `started_at`| ≤ 4 ч, граница включена. Один кандидат — `linked`. Ноль — `no_bo3_map`. Два и больше — `ambiguous_bo3`, `candidate_count` = их число.
8. **Одна карта bo3 у двух карт GRID** — обе получают `ambiguous_bo3`. Причина: `match_id` каталога = `bo3_game_id` и должен быть уникальным (PLAN.md, «Ключ карты»). Так бывает, если в GRID две серии на один реальный матч.
9. **Команды без имён.** `team_a_bo3_clan` — `clan_name` клана bo3 на стороне команды A GRID в раунде 1 (`team_a_side` раунда 1 из `rounds.parquet`). Имена между источниками s04 не сравнивает. id команды в API нет (факт 5), поэтому «`ct_team_id` bo3» из шага — это клан стороны CT в раунде 1. Если кланов в раунде 1 нет (3 из 4 886), значение пустое.
10. **Победитель bo3 для s06.** s04 пишет `bo3_winner_clan` — `winner_clan_name` карты bo3, как есть. s06 сравнит его с `team_a_bo3_clan`. s04 по этим колонкам карты не отсеивает. Так s06 не читает сырые файлы bo3.
11. **Список матчей — по дням UTC**, как кэш `allSeries` в s02:
    - параметры: `filter[matches.discipline_id][eq]=1`, `filter[matches.status][in]=finished,defwin`, `filter[matches.start_date][gt]` = полночь − 1 с, `filter[matches.start_date][lt]` = следующая полночь, `sort=start_date`, `page[limit]=100`, `page[offset]` до `total.count`;
    - без фильтра тира: нужны все тиры;
    - без `with`: имена не нужны;
    - если `start_date` хоть одного матча вне [полночь − 1 с, следующая полночь) — `RuntimeError` «bo3 list filter ignored» (факт 1).

    Отличие от шага: шаг называет загрузчик `fetch_bo3.py` с одним длинным списком. Деление по дням даёт кэш с окончательностью, как в s02, и маленькие окна пилота.
12. **Какие дни.** Дни UTC, которые пересекают [t − 17 ч, t + 17 ч] для `started_at` каждой карты GRID с рынком и отпечатком. 17 ч = окно 4 ч + максимум 12,12 ч из факта 7, с запасом. Окно симметричное, чтобы была одна константа. Справа оно шире нужного на ~12 ч: это до одного лишнего дня на краю.
13. **Какие матчи.** s04 качает карты всех матчей из файлов этих дней. Шаг говорит «матч в пределах ±4 ч хотя бы одной карты GRID». В полном периоде карты GRID есть почти каждый день, поэтому фильтр по матчу сэкономил бы мало и добавил бы код. Правило ±4 ч s04 применяет к `begin_at` карты (решение 7).
14. **Карты матча.** Один запрос: `with=game_rounds`, `filter[games.match_id][eq]=<id>`, `page[limit]=50`, `sort=number`. Если `total.count` больше числа результатов — `RuntimeError`.
15. **Кэш.**
    - Файл дня: `{"day", "fetched_at", "pages"}`. Файл матча: `{"match_id", "fetched_at", "page"}`. Запись — `write_json`, атомарно.
    - Файл окончательный, если `fetched_at` ≥ полночь дня + 48 ч (`is_day_final` из s02). Для матча день — дата его `start_date`.
    - С `--fetch` s04 качает отсутствующие и неокончательные файлы. Окончательный файл повторно не качается.
    - Без `--fetch` нет файла — `FileNotFoundError` с подсказкой про `--fetch`, как в s02 и s03.
16. **HTTP.** Свой `httpx.Client(headers=BO3_HEADERS, timeout=60)` и общий `get_json`. Запросы идут подряд, без пауз. Старый загрузчик шёл 4 потоками и скачал 2 948 матчей без бана. Ответ не 200 после 5 попыток — s04 падает. Повторный запуск продолжает с кэша. На 403 (Cloudflare) решает пользователь, как в правиле s05.
17. **Старый кэш циклов 1–2 не используем.** Он другого формата (`received_at_utc`, `pages`, `game_side_results`) и только тиры S/A/B. s04 его не трогает.
18. **Структурные инварианты — `RuntimeError`**, как в s02 и s03: дубль `game_id` в таблице; дубль `bo3_game_id` у `linked`; ни одной строки `linked`.

## 4. Изменения

### 4.1 `src/cs2/constants.py`

Добавить:

```python
CS2_RAW_BO3_DIR = CS2_RAW_DIR / "bo3"
CS2_BO3_LINKS_PATH = CS2_BO3_DIR / "links.parquet"
```

Подпапки `matches/` и `games/` s04 строит от `raw_dir` сам.

### 4.2 `src/cs2/s04_link_bo3.py` (новый, ~400 строк)

Стиль — как `src/cs2/s02_link_grid.py` и `src/cs2/s03_fetch_grid_rounds.py`: typer `main`, `TypedDict` для сырых ответов и строк parquet, frozen dataclass для своих значений, глаголы в именах функций, все аргументы обязательные, без комментариев.

**Импорт без копий:** `Cs2UniverseMarketRow` (s01); `Cs2GridLinkRow`, `read_traded_markets`, `utc_midnight`, `is_day_final`, `parse_fetched_at` (s02); `Cs2GridGameRow`, `Cs2GridRoundRow`, `parse_utc`, `format_utc`, `apply_table_dtypes` (s03); `get_json`; `read_json`, `write_json`; `write_parquet`, `replace_nulls_with_none`; `series_winner_covers_map`.

**Константы:**

```python
BO3_API_ROOT = "https://api.bo3.gg/api/v1"
BO3_HEADERS = {...}  # копия scripts/cs2/fetch_bo3.py:27
BO3_LIST_PAGE_LIMIT = 100
BO3_GAMES_PAGE_LIMIT = 50
BO3_MAP_START_WINDOW_SECONDS = 4 * 60 * 60
BO3_DAY_MARGIN_SECONDS = 17 * 60 * 60
BO3_REQUEST_TIMEOUT_SECONDS = 60
SIDE_LETTERS: dict[str, str] = {"CT": "C", "T": "T"}
```

**Сырые ответы bo3 — `TypedDict` только с нужными ключами** (лишние ключи в JSON остаются, `cast` их не трогает):

```python
class Bo3Total(TypedDict):
    count: int

class Bo3Match(TypedDict):
    id: int
    tier: str | None
    start_date: str
    status: str

class Bo3MatchesPage(TypedDict):
    total: Bo3Total
    results: list[Bo3Match]

class Bo3RoundClan(TypedDict):
    team_side: str
    clan_name: str

class Bo3Round(TypedDict):
    round_number: int
    winner_clan_side: str | None
    game_round_team_clans: list[Bo3RoundClan]

class Bo3Game(TypedDict):
    id: int
    match_id: int
    number: int
    map_name: str | None
    begin_at: str | None
    winner_clan_name: str | None
    game_rounds: list[Bo3Round]

class Bo3GamesPage(TypedDict):
    total: Bo3Total
    results: list[Bo3Game]

class Bo3MatchDay(TypedDict):
    day: str
    fetched_at: str
    pages: list[Bo3MatchesPage]

class Bo3MatchGames(TypedDict):
    match_id: int
    fetched_at: str
    page: Bo3GamesPage
```

Типы полей сверить с одним свежим ответом при реализации (факт 4 — старый кэш тех же эндпоинтов).

**Строка таблицы:**

```python
Cs2Bo3LinkStatus = Literal[
    "linked",
    "no_bo3_map",
    "ambiguous_bo3",
    "grid_rounds_incomplete",
    "excluded_series_rule",
]

class Cs2Bo3LinkRow(TypedDict):
    game_id: str              # UUID карты GRID
    series_id: str
    event_id: str
    condition_id: str | None  # None только у excluded_series_rule
    game_number: int
    map_name: str             # нормализованное
    status: Cs2Bo3LinkStatus
    candidate_count: int
    bo3_game_id: int | None
    bo3_match_id: int | None
    bo3_tier: str | None
    team_a_bo3_clan: str | None
    bo3_winner_clan: str | None
```

Комментарии в блоке выше — только для плана, в код их не писать. Целые с пустыми значениями — `Int64`, как `LINK_INTEGER_COLUMNS` в s02. Порядок строк — `series_id`, `game_number`.

**Свои значения:**

```python
@dataclass(frozen=True)
class LinkedSeries:
    event_id: str
    series_id: str
    best_of: int

@dataclass(frozen=True)
class FingerprintKey:
    map_name: str
    fingerprint: str

@dataclass(frozen=True)
class GridMap:
    game: Cs2GridGameRow
    rounds: list[Cs2GridRoundRow]
    event_id: str
    condition_id: str | None
    map_name: str
    fingerprint: str | None

@dataclass(frozen=True)
class Bo3Map:
    bo3_game_id: int
    bo3_match_id: int
    tier: str | None
    key: FingerprintKey
    begin_at: datetime
    ct_clan: str | None
    t_clan: str | None
    winner_clan: str | None

@dataclass(frozen=True)
class Bo3Pool:
    by_key: dict[FingerprintKey, list[Bo3Map]]
    matches: int
    maps: int
    maps_without_fingerprint: int

@dataclass(frozen=True)
class LinkDecision:
    status: Cs2Bo3LinkStatus
    candidate_count: int
    bo3_map: Bo3Map | None
```

**Функции (по порядку потока):**

```python
def load_linked_series(path: Path) -> list[LinkedSeries]
    # строки status == "matched"; best_of None → RuntimeError

def build_map_markets(
    best_of: int, markets: list[Cs2UniverseMarketRow]
) -> dict[int, Cs2UniverseMarketRow]:
    map_markets = {
        market["map_number"]: market
        for market in markets
        if market["contract_kind"] == "map_winner"
        and market["map_number"] is not None
        and 1 <= market["map_number"] <= best_of
    }
    series_markets = [m for m in markets if m["contract_kind"] == "series_winner"]
    covers_decider = series_winner_covers_map(
        best_of, best_of, map_winner_exists=best_of in map_markets
    )
    if series_markets and covers_decider:
        map_markets[best_of] = series_markets[0]
    return map_markets

def normalize_map_name(name: str) -> str

def build_fingerprint(round_numbers: list[int], winner_sides: list[str | None]) -> str | None
    # None: пусто, номера не 1…n, сторона не CT/T

def build_grid_fingerprint(game: Cs2GridGameRow, rounds: list[Cs2GridRoundRow]) -> str | None
    # решение 4

def collect_grid_maps(
    links: list[LinkedSeries],
    markets_by_event: dict[str, list[Cs2UniverseMarketRow]],
    games: list[Cs2GridGameRow],
    rounds_by_game: dict[str, list[Cs2GridRoundRow]],
) -> list[GridMap]

def list_bo3_days(starts: list[datetime]) -> list[date]
    # дни, пересекающие [t − 17 ч, t + 17 ч]

def fetch_match_day(client: httpx.Client, day: date) -> list[Bo3MatchesPage]
def require_day_results(day: date, page: Bo3MatchesPage) -> None
def refresh_match_days(client: httpx.Client, raw_dir: Path, days: list[date], now: datetime) -> None
def load_bo3_matches(raw_dir: Path, days: list[date]) -> list[Bo3Match]
    # нет файла → FileNotFoundError; dict по id снимает дубли на стыке дней

def fetch_match_games(client: httpx.Client, match_id: int) -> Bo3GamesPage
def refresh_match_games(
    client: httpx.Client, raw_dir: Path, matches: list[Bo3Match], now: datetime
) -> None
    # печать прогресса каждые 500 матчей и итог fetched/cached

def build_bo3_map(match: Bo3Match, game: Bo3Game) -> Bo3Map | None
def load_bo3_pool(raw_dir: Path, matches: list[Bo3Match]) -> Bo3Pool

def find_bo3_candidates(pool: Bo3Pool, grid_map: GridMap) -> list[Bo3Map]
def decide_bo3_link(grid_map: GridMap, pool: Bo3Pool) -> LinkDecision
    # condition_id None → excluded_series_rule; fingerprint None → grid_rounds_incomplete;
    # 0 / 1 / ≥2 кандидата
def release_shared_bo3_maps(decisions: dict[str, LinkDecision]) -> dict[str, LinkDecision]
    # решение 8; ключ — game_id GRID

def pick_team_a_clan(grid_map: GridMap, bo3_map: Bo3Map) -> str | None
def build_link_row(grid_map: GridMap, decision: LinkDecision) -> Cs2Bo3LinkRow
def build_link_table(grid_maps: list[GridMap], pool: Bo3Pool) -> pd.DataFrame
def validate_link_table(frame: pd.DataFrame) -> None
def print_link_summary(frame: pd.DataFrame, pool: Bo3Pool, day_count: int, output: Path) -> None
```

Комментарии `#` в блоке — пояснения плана, в код не идут.

**`main`:**

```python
def main(
    fetch: Annotated[bool, typer.Option("--fetch", help="Fetch missing and non-final bo3 days and matches.")] = False,
    links_dir: Annotated[Path, typer.Option(help="s02 links dir.")] = CS2_LINKS_DIR,
    universe_dir: Annotated[Path, typer.Option(help="s01 universe dir.")] = CS2_UNIVERSE_DIR,
    grid_dir: Annotated[Path, typer.Option(help="s03 GRID tables dir.")] = CS2_GRID_DIR,
    raw_dir: Annotated[Path, typer.Option(help="bo3 raw cache dir.")] = CS2_RAW_BO3_DIR,
    output_dir: Annotated[Path, typer.Option(help="Published bo3 links dir.")] = CS2_BO3_DIR,
) -> None:
```

Поток:

1. Связи s02, рынки (`read_traded_markets`), `games.parquet` и `rounds.parquet` (`replace_nulls_with_none` → записи `Cs2GridGameRow` / `Cs2GridRoundRow`).
2. `collect_grid_maps`.
3. Дни — по картам с `condition_id` и отпечатком.
4. Если `--fetch`: клиент bo3, `refresh_match_days`, `load_bo3_matches`, `refresh_match_games`.
5. `load_bo3_matches`, `load_bo3_pool` — из кэша.
6. `build_link_table`, `validate_link_table`, `write_parquet(output_dir / CS2_BO3_LINKS_PATH.name)`, `print_link_summary`.

**stdout (аудит):**

```
grid maps: N
excluded_series_rule: N
grid_rounds_incomplete: N
no_bo3_map: N
ambiguous_bo3: N
linked: N
linked by tier: {'a': …, 'b': …, 'c': …, 's': …}
bo3 days: N
bo3 matches: N
bo3 maps: N
bo3 maps without fingerprint: N
map names only in grid: [...]
saved: <path>
```

Строка `map names only in grid` — нормализованные названия карт GRID с отпечатком, которых нет ни у одной карты bo3. Это ловит новый вид имени, как `default-` (факт 6). Ещё `refresh_*` печатают `bo3 days fetched/cached` и `bo3 matches fetched/cached`.

### 4.3 `Makefile`

Добавить `cs2-bo3` в `.PHONY` и цель после `cs2-rounds`:

```make
cs2-bo3:  ## CS2 s04 GRID map <-> bo3 map by round fingerprint (--fetch bo3 days and matches)
	$(PY) src/cs2/s04_link_bo3.py --fetch $(ARGS)
```

### 4.4 `tests/test_cs2_link_bo3.py` (новый)

Фикстуры в `tmp_path`:

- связи s02 — `LINK_COLUMNS` из s02;
- рынки — `MARKET_COLUMNS` из s01;
- `games.parquet` и `rounds.parquet` — через `games_frame` и `rounds_frame` из s03;
- сырые файлы дня и матча bo3 — `write_json`.

Помощники: `make_grid_rounds(game_id, fingerprint, team_a_first_side)` со сменой сторон после раунда 12; `make_bo3_game(game_id, match_id, map_name, begin_at, fingerprint, ct_clan, t_clan)`. Каждый тест — без сети.

Тесты из шага:

1. `test_exact_fingerprint_links` — `main` без `--fetch`: `linked`, `bo3_game_id`, `bo3_match_id`, `bo3_tier`, `candidate_count == 1`.
2. `test_one_round_difference_is_no_bo3_map`.
3. `test_two_same_fingerprints_in_window_are_ambiguous` — `ambiguous_bo3`, `candidate_count == 2`.
4. `test_window_is_four_hours_inclusive` — `begin_at` = `started_at` + 4 ч → `linked`; + 4 ч 1 с → `no_bo3_map`.
5. `test_team_a_clan_follows_round_one_side` — команда A на T в раунде 1 → клан стороны T; на CT → клан CT.
6. `test_series_market_covers_decider` — `build_map_markets(3, [map 1, map 2, series])` → {1, 2, 3}, карта 3 — рынок серии; BO5 с «Map 1–4» → 5 — рынок серии; BO3 только с рынком серии → карты 1–2 GRID `excluded_series_rule`, карта 3 `linked`.

Дополнительно — ветки, которые добавляет план:

7. `test_map_names_normalize` (parametrize): `default-dust2`, `de_dust2`, `Dust2` → `dust2`.
8. `test_bo3_map_without_rounds_is_not_candidate` — `game_rounds = []` → `no_bo3_map`.
9. `test_incomplete_grid_rounds` (parametrize): незавершённый раунд; два победителя; пропуск номера; n ≠ счёт → `grid_rounds_incomplete`.
10. `test_shared_bo3_map_makes_both_ambiguous` — решение 8.
11. `test_list_outside_day_raises` — `monkeypatch` на `cs2.s04_link_bo3.get_json`, матч 2020 года в ответе → `RuntimeError`.
12. `test_refresh_skips_final_files` — окончательный день и матч не качаются, неокончательные качаются; счёт вызовов `get_json`.
13. `test_missing_cache_requires_fetch` — `FileNotFoundError`.
14. `test_bo3_days_cover_17_hours` — `started_at` 2026-10-05T10:00Z → дни 10-04, 10-05, 10-06.

## 5. Крайние случаи

- **Карта bo3 без раундов в API** (тир C, факт 3) — не кандидат. Карта GRID без другого кандидата — `no_bo3_map`.
- **Матч `defwin`** — 0 карт, один лишний запрос. Карты сыгранного `defwin` остаются кандидатами.
- **Карта bo3 без `begin_at` или `map_name`** (30 и 17 в старом кэше) — не кандидат.
- **Раунд 1 без кланов** (3 из 4 886) — связь есть, `team_a_bo3_clan` пуст. s06 отсеет карту на сверке победителя.
- **Смена имени клана внутри карты** (2 карты из 4 887 в старом кэше) — s04 берёт имя из раунда 1. Совпадёт ли `bo3_winner_clan`, решает s06.
- **«Грязная» карта GRID** (62 из 4 716 в старом кэше: незавершённый сегмент, нет победителя, два победителя, победы ≠ счёт) — `grid_rounds_incomplete`.
- **Карта GRID без сегментов** (167 из 4 883) — n = 0, `grid_rounds_incomplete`.
- **Овертайм** — номера раундов bo3 идут дальше 24 без дыр (`ov_index` 1 у 4 226 строк старого кэша). Отпечаток включает овертайм.
- **Матч на стыке дней** — `gt` = полночь − 1 с может дать матч в двух файлах дней. `load_bo3_matches` собирает `dict` по `id`.
- **Больше 100 матчей за день** — пагинация по `offset` до `total.count`.
- **Матч `current` во время запроса** — его нет в списке. День ещё не окончательный, следующий `--fetch` его спросит.
- **Раунды bo3 появились позже 48 ч** — окончательный файл не перекачивается, карта остаётся без кандидата (риски).
- **Обрыв `--fetch`** — файлы пишутся по одному и атомарно. Повторный запуск продолжает с кэша.

## 6. Проверка

Команды из корня `esports-trader-cs2`:

1. `uv run ruff format src/cs2 tests/test_cs2_link_bo3.py` и `uv run ruff check src/cs2 tests/test_cs2_link_bo3.py` — без ошибок.
2. `uv run python -m basedpyright` (весь проект, как pre-commit) — 0 errors.
3. `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s04_link_bo3.py tests/test_cs2_link_bo3.py Makefile` — passed.
4. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_link_bo3.py tests/test_cs2_grid_rounds.py tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
5. `PYTEST_N=8 make test` — допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol`) и известный флейк `smoke[dota_9007700576]` (см. `progress.txt`). Флейк перепроверить отдельно.
6. **Короткий живой прогон** на пробе 2026-09-10: связи STEP-003 (`work/impl003/links`, 49 `matched`) и таблицы STEP-004 (`work/impl004/grid`, 102 карты). Universe — настоящий `data/cs2/universe`, только чтение. Сырые файлы и таблица — в scratch, настоящие `data/cs2/raw/bo3` и `data/cs2/bo3` не трогать:

   ```bash
   W=../betting_workspace/tasks/cs2-follow300/orchestrate/work
   PYTHONPATH=src uv run python src/cs2/s04_link_bo3.py --fetch \
     --links-dir $W/impl003/links --grid-dir $W/impl004/grid \
     --raw-dir $W/impl005/bo3_raw --output-dir $W/impl005/bo3
   ```

   Ожидание:
   - карты GRID стартуют 2026-09-10T02:03Z … 2026-09-11T01:37Z, поэтому дни — 09-09, 09-10, 09-11: 3–6 запросов списка и ~150 запросов карт, ~1 мин;
   - `grid maps: 102`, `excluded_series_rule: 0` (факт 9), `grid_rounds_incomplete: 5` (факт 8);
   - `linked` ≥ 41: без тира C офлайн-проверка дала 41 (факт 8);
   - `ambiguous_bo3: 0`;
   - `map names only in grid: []`.

   Если число другое, причину надо назвать в отчёте.
7. **Повторы.** Второй запуск с `--fetch` — `fetched: 0` у дней и матчей: 18.10 позже 11.09 + 48 ч. Запуск без `--fetch` даёт ту же таблицу (`pd.testing.assert_frame_equal`).
8. **Сверка со старой парой серий** (`investigations/2026-10-17-cs2-bo3-coverage/work/grid_series_vs_bo3.parquet`, пары по именам; у 20 серий пробы есть `bo3_match_id`, у 10 из них матч в старом кэше S/A/B):

   ```python
   import pandas as pd
   W = "../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl005"
   links = pd.read_parquet(f"{W}/bo3/links.parquet")
   old = pd.read_parquet("../betting_workspace/investigations/2026-10-17-cs2-bo3-coverage/work/grid_series_vs_bo3.parquet")
   old = old[old.matched & old.bo3_match_id.notna()].assign(series_id=lambda f: f.series_id.astype(str))
   linked = links[links.status == "linked"]
   both = linked.merge(old[["series_id", "bo3_match_id"]], on="series_id", suffixes=("", "_old"))
   print(len(both), (both.bo3_match_id == both.bo3_match_id_old).mean())
   print(linked.groupby("series_id").bo3_match_id.nunique().gt(1).sum())
   print(linked.team_a_bo3_clan.isna().sum(), linked.bo3_winner_clan.isna().sum())
   ```

   Ожидание: доля совпадений = 1,0. Серий, чьи карты связались с разными матчами bo3, — 0. Пустых кланов — 0 или названы поимённо. Любое расхождение надо разобрать до коммита.
9. Полный прогон s04 на весь период — не в этом шаге. Это ~17 000 запросов, 30–85 мин. Его делает пилот (STEP-014) после s03 на весь период.

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s04_link_bo3.py`, `tests/test_cs2_link_bo3.py`, `Makefile`. Сообщение — в стиле `9474ef89`, например «Link CS2 GRID maps to bo3 maps by round fingerprint.», тело 2–3 строки, последняя — `Co-Authored-By: <модель> <noreply@...>`. Без `git push`. `passes` в `feature.json` не менять: это делает оркестратор после ревью.

## 8. Риски и открытые вопросы

- **Отличия от текста шага** (все — в решениях):
  - список матчей по дням, а не одним длинным списком (решение 11);
  - карты всех матчей покрытых дней, а не фильтр матча ±4 ч (решение 13);
  - «`ct_team_id` bo3» = клан стороны CT в раунде 1: id команды в API нет (решение 9);
  - лишние колонки `event_id`, `condition_id`, `game_number`, `map_name`, `status`, `candidate_count`, `team_a_bo3_clan`, `bo3_winner_clan` (решения 2, 9, 10);
  - s05 и s06 читают результат функции из таблицы (решение 2).
- **Покрытие тира C.** У части карт bo3 тира C нет раундов в API (факт 3, замер покрытия: 67–71% карт тира C с раундами). Такие карты GRID получают `no_bo3_map`, даже если реплей есть. stdout не делит `no_bo3_map` на «карта bo3 без раундов» и «отпечаток не совпал». Если пилоту это нужно, это одна строка аудита.
- **Дубли bo3 в тире C не проверены.** Уникальность отпечатка в окне ±4 ч замерена на старом кэше S/A/B (4 659 карт, 0 дублей). Число `ambiguous_bo3` даст пилот.
- **Бан Cloudflare.** s04 делает ~17 000 запросов к `api.bo3.gg` до загрузки реплеев s05. На 429 `get_json` ждёт `retry-after`. На 403 s04 падает после 5 попыток, дальше решает пользователь (туннель через `sun`).
- **Окончательность через 48 ч.** У 1 599 из 16 715 матчей bo3 `parsed_status = waiting`. Если bo3 дописывает раунды позже 48 ч после дня матча, окончательный файл их не получит. Шаг требует «повторно не качаются». Если пилот увидит такие матчи, нужен флаг перекачки.
- **Новые виды названий карт.** `default-` нашёлся только в новых данных (факт 6). В train-периоде могут быть другие виды. Строка `map names only in grid` в stdout их покажет.
- **Размер кэша.** ~17 000 файлов матчей по ~50–65 КБ в ответе, с отступами `write_json` больше: грубо 1–1,5 ГБ. Свободно 203 ГБ (PLAN.md).
- **Связь с s01–s03.** s04 импортирует из `cs2.s01_build_universe`, `cs2.s02_link_grid`, `cs2.s03_fetch_grid_rounds`. Правка их сигнатур сломает s04. Это поймают basedpyright и тесты.
- **Передать в STEP-006 и STEP-008.** Строки `linked` — это торгуемые карты с парой bo3. s05 берёт `bo3_game_id`, число раундов — из `rounds.parquet` GRID, мету — из `map_name` и `games.parquet`. `team_a_bo3_clan` ориентирует реплей: в нём есть `team_counter_terrorists.clan_name`. s06 сверяет `bo3_winner_clan == team_a_bo3_clan` с `team_a_won` GRID и с исходом рынка.
- Я не запускал новый код: фаза плана не пишет код.
