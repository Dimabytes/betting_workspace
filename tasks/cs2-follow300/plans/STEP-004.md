# STEP-004 — s03: раунды GRID

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, один коммит.
Figma и дизайна нет.

## 1. Что делает шаг

s03 читает связи s02 (`data/cs2/links/grid_links.parquet`, строки `status == "matched"`) и для каждой связанной серии берёт Series State GRID: карты, счёт, победителя и сегменты раундов.

Выход:

- `data/cs2/raw/grid/<series_id>.json` — сырой ответ Series State, как его вернул GRID;
- `data/cs2/grid/rounds.parquet` — строка на сегмент раунда;
- `data/cs2/grid/games.parquet` — строка на карту.

Команда A в обеих таблицах — команда `team_a_grid_id` из s02. Это та же команда, к которой привязан `team_a_token_index` (решение 1).

## 2. Что я проверил (факты)

Скрипты и пробы лежат в `tasks/cs2-follow300/orchestrate/work/plan004/`. К GRID я отправил 3 запроса 18.10: 1 `allSeries` и 2 Series State (`probe.py`).

1. **Кэш Series State циклов 1–2** (`data/cs2/grid/series_state/`, 2 267 файлов, 144 МБ, запрос со всеми полями). Скрипты `scan_cache.py`, `scan_tail.py`:
   - 2 214 серий со state, 52 — ответ `{"data": {}}` (http 200, без `errors`, ключа `seriesState` нет). У 118 серий state есть, а карт 0;
   - 4 883 карты. У 167 карт нет сегментов. Номера карт — 1…n без дыр, id карт уникальны, у всех карт есть `startedAt` и `map.name`;
   - названия карт без префикса `de_`: `dust2` 1 007, `mirage` 852, `ancient` 804, `nuke` 647, `inferno` 603, `anubis` 458, `cache` 455, `overpass` 57;
   - у всех сегментов `type = "round"`, номера сегментов — 1…n по порядку, сторона — только `counter-terrorists` / `terrorists`;
   - порядок и id команд в `games.teams` и `segments.teams` всегда равны `seriesState.teams`; у каждой серии ровно 2 команды;
   - 4 654 карты из 4 716 с сегментами «чистые»: все сегменты `finished`, у каждого ровно один победитель, число побед в раундах = счёт карты. У всех 4 654 конец последнего сегмента = `startedAt` карты + `duration` карты, до 0,01 с;
   - 62 карты (1,3%) «грязные», признаки пересекаются: у 16 плох только последний сегмент (не `finished` или без победителя); у 46 незавершённые сегменты есть и до последнего; у 29 победы в раундах ≠ счёт; ещё 3 сегмента с двумя победителями. Пример: `2974920` карта 1 — 13:11 сходится, но последний сегмент не `finished` и длится 1,6 с. У 16 карт с плохим последним сегментом он кончается вместе с картой: GRID считает его частью карты;
   - 1 сегмент без `startedAt`: `2962522` карта 1, раунд 1 `started: false`, `duration: PT0S`. GRID не присылает ключ, если значение пустое.
2. **Порядок команд `allSeries` ≠ Series State.** 3 дня `allSeries` из пробы STEP-003 (`work/impl003/grid_series/`, `work/plan003/allseries_*.json`): 205 серий, 78 из них есть в старом кэше. id команд совпали у 78 из 78. Порядок совпал у 77. У `2992177` порядок обратный: `allSeries` — `100 Thieves`, `BBL`, Series State — `BBL`, `100 Thieves`. Свежий запрос 18.10 (`state_2992177.json`) это подтвердил. s02 называет командой A `teams[0]` из `allSeries` (`src/cs2/s02_link_grid.py:242`). Если s03 возьмёт `teams[0]` Series State, у таких серий команда A и токен перевернутся.
3. **Короткий запрос работает на train-периоде.** Серия `2866404` (2025-11-02, BO3): запрос из раздела 4.2 вернул 2 карты (`ancient` 13:1, `nuke` 13:8) и 14 и 21 сегмент. Ответ ~11 КБ, с отступами `write_json` ~24 КБ.
4. **Карта без сегментов имеет победителя.** `2992177` карта 1 (`ancient`): `duration: PT0S`, стороны `none`, счёт 13:5, `won` у `BBL`, сегментов 0.
5. **`gridSeriesId` в Gamma — только с 2026-03-10** (`data/cs2/universe/events.parquet`, 2 686 событий). Серии train-периода s03 получает только через связи `allseries`.
6. **Связи s02 для живой проверки.** `work/impl003/links/grid_links.parquet` (2026-09-10): 49 `matched` (26 `allseries`, 23 `gamma`). 23 из этих серий есть в старом кэше: на них можно сверить новые таблицы со старыми.
7. **Лимит GRID.** Старый `scripts/cs2/fetch_grid_rounds.py:29` шёл с паузой 1,05 с и скачал 2 268 серий. В итоге 52 ошибки, все `seriesState null`, ни одной по лимиту. Повторы скрипт не логировал, поэтому число попаданий в лимит неизвестно. s02 и Dota ждут 3,2 с (`GRID_REQUEST_SLEEP_SECONDS`, `src/collect/s04_fetch_grid_starts.py:38`). Живой трейдер ключом GRID не пользуется: он читает сокет виджетов без ключа (`src/trader/grid_widgets.py:18`). Context7 документации GRID не знает.
8. **Dota-код, который подходит без правок** (`src/collect/s04_fetch_grid_starts.py`):
   - `grid_token` (`:208`), `grid_graphql` (`:215`): 5 попыток, ждёт `retry-after` и `rateLimitResetsIn`;
   - `GRID_SERIES_STATE_API` (`:31`);
   - `iso_duration_seconds` (`:53`): понимает `PT2H2M42.468043471S` и `PT0S`.
9. **Что из Dota не подходит:** `SERIES_STATE_QUERY` (`:263`) без сегментов и карты; типы `GridGameNode`, `GridGameTeam` (`:121-139`) держат сторону `radiant` / `dire`. `parse_ts` (`src/shared/utils/parsing.py:25`) режет время до целых секунд.
10. **Старые файлы в `data/cs2/grid/`:** `grid_games.parquet`, `grid_rounds.parquet`, `series_state/`, `fetch_summary.json`, `type_probe.json`. Новые имена `games.parquet` и `rounds.parquet` с ними не совпадают. `data/cs2/raw/` сейчас содержит только `gamma`.

## 3. Решения

1. **Команда A — по id из s02, не по позиции.** s03 берёт `team_a_grid_id` и `team_b_grid_id` из строки связи и ищет эти id в `teams` Series State. Колонки `team_a_*` и `team_b_*` в обеих таблицах — по этим id. Факт 2: у 1 из 78 серий порядок обратный. Это отличие от текста шага («teams[0] и teams[1]»): PLAN.md называет командой A `teams[0]` серии GRID, и s02 уже закрепил это id из `allSeries`. Так `team_a_token_index`, `team_a_win` и признаки игры смотрят на одну команду.
2. **Статус серии.** Набор id команд Series State ≠ `{team_a_grid_id, team_b_grid_id}` — статус `grid_team_mismatch`, строк нет. Ответ без `seriesState` — `no_series_state`. State без карт — `no_games`. Остальное — `ok`. Статусы s03 печатает в stdout, в таблицу не пишет: s07 считает воронку по `games.parquet` и связям s02.
3. **s03 пишет факты GRID и не чистит карты.** «Грязные» карты (факт 1, 62 из 4 716) остаются в таблицах как есть: колонки `finished` и `won` у каждой команды это показывают. Отсеивают их s04 (отпечаток не совпадёт с bo3), s05 (сверки 1–3) и s06 (итог). Так s07 видит, на каком шаге карта выпала.
4. **`ended_at` карты — конец сегмента с наибольшим номером:** `startedAt` сегмента + длительность. У карты без сегментов или с последним сегментом без `startedAt` — `None`. У «чистых» карт это равно концу карты (факт 1). У «грязных» значение бывает мусорным, но такие карты отсекают сверки s04/s05.
5. **Время — строки ISO, как `spawn_at` / `ended_at` у Dota** (`src/shared/types/dataset.py:25-26`). `started_at` — строка GRID без изменений. `ended_at` s03 считает через `datetime` и пишет `isoformat(timespec="milliseconds")` с `Z`: тот же вид, что у GRID. Длительность — `duration_s` (float, `iso_duration_seconds`).
6. **Сторона — `CT` / `T`**, как `post_winner_side` у bo3. Словарь `{"counter-terrorists": "CT", "terrorists": "T"}`. Другое значение в сегменте — `RuntimeError`.
7. **Структурные инварианты — `RuntimeError`**, как проверки в s01/s02: тип сегмента не `round`; id команд карты или сегмента не равны id команд серии; длительность не разбирается; дубль ключа таблицы. В 4 883 картах таких нет. Сборка таблиц идёт из кэша без сети, поэтому падение стоит только правки и перезапуска без `--fetch`.
8. **Запрос — свой, короткий** (раздел 4.2). Он берёт только поля, которые нужны s03–s06: стороны, победы, счёт, время, карта. Сырой ответ лежит в кэше, поэтому новые колонки потом добавляются без новых запросов. Новое поле в запросе — это перекачка всех серий.
9. **Кэш.** Файл `data/cs2/raw/grid/<series_id>.json` — ответ GRID как есть (`write_json`, атомарно). Серия окончательна, если в файле `data.seriesState.finished == True`. С `--fetch` s03 качает отсутствующие и неокончательные серии: ответы без state, незаконченные серии, будущие серии. Без `--fetch` нет файла — `FileNotFoundError` с подсказкой про `--fetch`, как у s02 (`src/cs2/s02_link_grid.py:255-261`).
10. **Пауза 1,05 с после запроса** — константа `SERIES_STATE_SLEEP_SECONDS` в модуле s03. Причина — факт 7: с этой паузой прошли 2 268 серий подряд. `grid_graphql` сам ждёт сброса лимита. С ~6 000 серий выйдет ~2,5 ч против ~6 ч с паузой 3,2 с. Если пилот увидит ожидания лимита, ставим 3,2 с.
11. **Старый кэш циклов 1–2 не используем.** Он в другом формате (`{received_at_utc, response, …}`) и с другим запросом. Перенос сэкономил бы ~2 200 запросов (~1 ч), но смешал бы два вида сырых файлов. Старые файлы s03 не трогает.
12. **Какие серии.** Все `series_id` строк `matched` из `grid_links.parquet`, по возрастанию id. Уникальность `series_id` среди `matched` гарантирует s02 (`validate_link_table`, `src/cs2/s02_link_grid.py:593`). У строки `matched` без id команд — `RuntimeError`.
13. **Повторное использование.** Импорт без копий: `grid_token`, `grid_graphql`, `GRID_SERIES_STATE_API`, `iso_duration_seconds` из `collect.s04_fetch_grid_starts`; `Cs2GridLinkRow` из `cs2.s02_link_grid`; `read_json`, `write_json`, `write_parquet`, `replace_nulls_with_none` из `shared.utils`.

## 4. Изменения

### 4.1 `src/cs2/constants.py`

Добавить:

```python
CS2_RAW_GRID_STATE_DIR = CS2_RAW_DIR / "grid"
CS2_GRID_GAMES_PATH = CS2_GRID_DIR / "games.parquet"
CS2_GRID_ROUNDS_PATH = CS2_GRID_DIR / "rounds.parquet"
```

### 4.2 `src/cs2/s03_fetch_grid_rounds.py` (новый, ~250 строк)

Стиль — как `src/cs2/s02_link_grid.py`: typer `main`, `TypedDict` для строк parquet, frozen dataclass для своих значений, без комментариев.

**Запрос:**

```python
SERIES_STATE_QUERY = """
query SeriesState($id: ID!) {
  seriesState(id: $id) {
    id started finished startedAt format
    teams { id name score won }
    games {
      id sequenceNumber started finished startedAt duration
      map { name }
      teams { id name side won score }
      segments {
        id type sequenceNumber started finished startedAt duration
        teams { id side won }
      }
    }
  }
}
"""
SERIES_STATE_SLEEP_SECONDS = 1.05
```

**Типы ответа** (свои, Dota-типы держат `radiant` / `dire`). GRID не присылает пустые ключи (факт 1), поэтому `startedAt` сегмента и `seriesState` — `NotRequired`:

```python
class Cs2SegmentTeam(TypedDict):
    id: str
    side: str
    won: bool

class Cs2Segment(TypedDict):
    id: str
    type: str
    sequenceNumber: int
    started: bool
    finished: bool
    startedAt: NotRequired[str]
    duration: str
    teams: list[Cs2SegmentTeam]

class Cs2GameMap(TypedDict):
    name: str

class Cs2GameTeam(TypedDict):
    id: str
    name: str
    side: str
    won: bool
    score: int

class Cs2Game(TypedDict):
    id: str
    sequenceNumber: int
    started: bool
    finished: bool
    startedAt: str
    duration: str
    map: Cs2GameMap
    teams: list[Cs2GameTeam]
    segments: list[Cs2Segment]

class Cs2SeriesTeam(TypedDict):
    id: str
    name: str
    score: int
    won: bool

class Cs2SeriesState(TypedDict):
    id: str
    started: bool
    finished: bool
    startedAt: NotRequired[str]
    format: str
    teams: list[Cs2SeriesTeam]
    games: list[Cs2Game]

class Cs2SeriesStateData(TypedDict):
    seriesState: NotRequired[Cs2SeriesState | None]

class Cs2SeriesStateResponse(TypedDict):
    data: Cs2SeriesStateData
```

`startedAt` карты — обязательный ключ: он есть у всех 4 883 карт. Нет ключа — `KeyError` при сборке, это видно сразу.

**Строки parquet:**

```python
Cs2Side = Literal["CT", "T"]
Cs2SeriesStatus = Literal["ok", "no_series_state", "no_games", "grid_team_mismatch"]

class Cs2GridRoundRow(TypedDict):
    series_id: str
    game_id: str
    game_number: int
    map_name: str
    round_number: int
    started_at: str | None
    duration_s: float
    finished: bool
    team_a_side: Cs2Side
    team_a_won: bool
    team_b_side: Cs2Side
    team_b_won: bool

class Cs2GridGameRow(TypedDict):
    series_id: str
    game_id: str
    game_number: int
    map_name: str
    started_at: str
    ended_at: str | None
    finished: bool
    team_a_id: str
    team_a_name: str
    team_a_score: int
    team_a_won: bool
    team_b_id: str
    team_b_name: str
    team_b_score: int
    team_b_won: bool
```

`ROUND_COLUMNS` и `GAME_COLUMNS` — из `__annotations__`, как `LINK_COLUMNS` в s02.

**Свои значения:**

```python
@dataclass(frozen=True)
class SeriesLink:
    series_id: str
    team_a_id: str
    team_b_id: str

@dataclass(frozen=True)
class GridTables:
    games: pd.DataFrame
    rounds: pd.DataFrame
    statuses: Counter[Cs2SeriesStatus]
    unfinished_series: int
```

**Функции** (имена — глаголы):

1. `load_series_links(path: Path) -> list[SeriesLink]` — строки `matched`, сортировка по `series_id`. Пустой `team_a_grid_id` / `team_b_grid_id` у `matched` — `RuntimeError`.
2. `build_state_path(raw_dir: Path, series_id: str) -> Path` — `raw_dir / f"{series_id}.json"`.
3. `read_state_file(path: Path) -> Cs2SeriesStateResponse` — `cast(..., read_json(path))`.
4. `is_state_final(path: Path) -> bool` — файл есть, state есть и `finished is True`.
5. `refresh_series_states(raw_dir: Path, series_ids: list[str]) -> None` — цикл как `refresh_series_index` в s02: окончательные пропускает, токен берёт один раз при первом запросе, после каждого запроса `write_json` и `time.sleep(SERIES_STATE_SLEEP_SECONDS)`. Печать каждые 100 запросов и в конце: `series fetched: N`, `series cached: M`.
6. `require_series_state(raw_dir: Path, series_id: str) -> Cs2SeriesState | None` — нет файла — `FileNotFoundError(f"missing Series State {series_id} at {path}; rerun with --fetch")`; нет state — `None`.
7. `parse_utc(value: str) -> datetime` и `format_utc(moment: datetime) -> str` — `fromisoformat` с заменой `Z`; `isoformat(timespec="milliseconds")` с `Z`.
8. `parse_duration(value: str) -> float` — `iso_duration_seconds`, `None` → `RuntimeError`.
9. `map_side(value: str) -> Cs2Side` — словарь, неизвестное значение → `RuntimeError`.
10. `require_team_ids(expected: list[str], found: list[str], where: str) -> None` — `sorted(found) != sorted(expected)` → `RuntimeError(where)`.
11. `compute_ended_at(segments: list[Cs2Segment]) -> str | None` — сегмент с наибольшим `sequenceNumber`; нет сегментов или нет `startedAt` → `None`; иначе `format_utc(parse_utc(startedAt) + timedelta(seconds=parse_duration(duration)))`.
12. `build_round_row(link, game, segment) -> Cs2GridRoundRow` — тип `round` или `RuntimeError`; команды сегмента по id.
13. `build_game_row(link, game) -> Cs2GridGameRow` — команды карты по id, `ended_at` из п. 11.
14. `decide_series_status(link, state) -> Cs2SeriesStatus` — правила решения 2.
15. `build_grid_tables(raw_dir: Path, links: list[SeriesLink]) -> GridTables` — по каждой связи: state → статус; при `ok` проверка id команд у каждой карты и сегмента (п. 10), строки карт и раундов. `pd.DataFrame(rows, columns=...)`, сортировка: карты по `series_id, game_number`, раунды по `series_id, game_number, round_number`.
16. `validate_grid_tables(tables: GridTables) -> None` — уникальны `game_id`, пара `series_id, game_number`, пара `game_id, round_number`. Иначе `RuntimeError`.
17. `print_grid_summary(tables: GridTables, link_count: int, games_path: Path, rounds_path: Path) -> None` — печать: связанных серий; статусы (`Counter`); незаконченных серий; карт; карт без раундов (`ended_at` пуст); раундов; пути.
18. `main(fetch, links_dir, raw_dir, output_dir)` — опции typer как в s02:
    - `--fetch` (по умолчанию `False`);
    - `--links-dir` = `CS2_LINKS_DIR`, файл `CS2_GRID_LINKS_PATH.name`;
    - `--raw-dir` = `CS2_RAW_GRID_STATE_DIR`;
    - `--output-dir` = `CS2_GRID_DIR`, файлы `CS2_GRID_GAMES_PATH.name` и `CS2_GRID_ROUNDS_PATH.name`.

    Порядок: связи → при `--fetch` `refresh_series_states` → `build_grid_tables` → `validate_grid_tables` → `write_parquet` двух таблиц → печать.

Колонки `game_number`, `round_number` и счёт — `int64`, флаги — `bool`. Пустые значения бывают только в `rounds.started_at` и `games.ended_at`.

### 4.3 `Makefile`

- В `.PHONY` добавить `cs2-rounds` (строка 16, после `cs2-link`).
- Цель после `cs2-link`:

```make
cs2-rounds:  ## CS2 s03 GRID Series State rounds of linked series (--fetch non-final series)
	$(PY) src/cs2/s03_fetch_grid_rounds.py --fetch $(ARGS)
```

### 4.4 `tests/test_cs2_grid_rounds.py` (новый)

Без сети. Помощники строят словари Series State в памяти: `make_segment(number, started_at, duration, team_a_side, team_a_won, finished)` и т. п., все аргументы обязательные. Фикстура — серия `3000001`, связь: команда A `100 / Alpha`, B `200 / Beta`. В Series State порядок обратный: `[Beta, Alpha]`.

- Карта 1, `mirage`, `startedAt` `2026-09-09T15:00:00.000Z`. 24 сегмента подряд по 100 с, последний — `PT1M54.72S`. Alpha — CT в раундах 1–12 и T в 13–24. Счёт Alpha 13, Beta 11, `won` у Alpha.
- Карта 2, `nuke`, без сегментов, `duration: PT0S`, стороны `none`, Beta 13:5 (как `2992177`, факт 4).

Тесты:

1. **Число раундов** (`main(fetch=False)` на `tmp_path`): у карты 1 — 24 строки, номера 1…24; у карты 2 строк нет.
2. **Стороны после раунда 12:** `team_a_side` — `CT` в раундах 1–12 и `T` в 13–24; `team_b_side` — всегда противоположная.
3. **Конец последнего сегмента:** `ended_at` карты 1 = `2026-09-09T15:40:14.720Z` (15:00:00 + 23 × 100 с + 114,72 с). У карты 2 `ended_at` пуст. `started_at` = `startedAt` карты.
4. **Команда A по id, а не по порядку Series State:** `team_a_id = "100"`, `team_a_name = "Alpha"`, `team_a_score = 13`, `team_a_won = True` на карте 1; `team_b_won = True` на карте 2.
5. **Статусы:** файл `{"data": {}}` → `no_series_state`; state с `teams` `100` и `300` при связи `100`/`200` → `grid_team_mismatch`; state с `games: []` → `no_games`. Строк у этих серий нет (`build_grid_tables`).
6. **`is_state_final`:** `finished: true` → `True`; `finished: false` → `False`; `{"data": {}}` → `False`; нет файла → `False`.
7. **Окончательная серия не качается повторно:** `monkeypatch` подменяет `grid_graphql`, `grid_token` и `time.sleep` в модуле s03. Две серии: одна окончательная в кэше, другая нет. `refresh_series_states` зовёт `grid_graphql` один раз, с id второй серии, и пишет её файл.
8. **Нет файла без `--fetch`** → `FileNotFoundError`.
9. **Неизвестная сторона сегмента** (`none` в сегменте) → `RuntimeError`.
10. **Колонки:** порядок колонок обеих таблиц = `ROUND_COLUMNS` / `GAME_COLUMNS`; `game_number`, `round_number` — целые.

## 5. Крайние случаи

- **Порядок команд `allSeries` и Series State разный** (1 из 78) — решение 1, тест 4.
- **id команд Series State не совпали со связью** — `grid_team_mismatch`, строк нет. В 78 сравнениях таких нет.
- **Ответ `{"data": {}}`** (52 из 2 268) — `no_series_state`. При следующем `--fetch` s03 спросит снова.
- **State без карт** (118 из 2 214) — `no_games`.
- **Карта без сегментов** (167 из 4 883) — строка карты есть, `ended_at` пуст, раундов нет. Победитель и счёт есть (факт 4).
- **Сегмент без `startedAt`** (1 случай) — `started_at` пуст. Если это последний сегмент, `ended_at` пуст.
- **Незавершённый сегмент, сегмент без победителя или с двумя победителями** — пишем как есть (`finished`, `team_a_won`, `team_b_won`). Решают s04 и s05.
- **Плохой последний сегмент** (16 карт: не `finished` или без победителя) — его конец попадает в `ended_at`. Такую карту отсекают сверки s04/s05 (решение 4).
- **Незаконченная серия** (идёт сейчас или в будущем) — строки по текущему state, флаг `finished` у карты. При следующем `--fetch` серия качается снова.
- **Серия с перезапуском `--fetch` после обрыва** — файлы пишутся по одному и атомарно. Повторный запуск продолжает с первой неокончательной серии.
- **Длительность с наносекундами** (`PT2H2M42.468043471S`) — `iso_duration_seconds` даёт float, `timedelta` округляет до микросекунд, `ended_at` пишется до миллисекунд.

## 6. Проверка

Команды из корня `esports-trader-cs2`:

1. `uv run ruff format src/cs2 tests/test_cs2_grid_rounds.py` и `uv run ruff check src/cs2 tests/test_cs2_grid_rounds.py` — без ошибок.
2. `uv run python -m basedpyright` (весь проект, как pre-commit) — 0 errors.
3. `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s03_fetch_grid_rounds.py tests/test_cs2_grid_rounds.py Makefile` — passed.
4. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_grid_rounds.py tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
5. `PYTEST_N=8 make test` — допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol`, см. progress.txt).
6. **Короткий живой прогон** на связях пробы STEP-003 (49 серий, ~49 запросов, ~1,5 мин). Всё пишется в scratch, реальные `data/cs2/raw/grid` и `data/cs2/grid` не трогать:

   ```bash
   S=../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl004
   PYTHONPATH=src uv run python src/cs2/s03_fetch_grid_rounds.py --fetch \
     --links-dir ../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl003/links \
     --raw-dir $S/grid_raw --output-dir $S/grid
   ```

   Ожидание: 49 связанных серий, `series fetched: 49`. Повторный запуск с `--fetch` качает только неокончательные серии (по 2026-09-10 ожидаю 0). Запуск без `--fetch` даёт те же таблицы.
7. **Сверка со старым кэшем** на 23 общих сериях (факт 6). Старая таблица `data/cs2/grid/grid_rounds.parquet` ориентирована по `teams[0]` Series State (`scripts/cs2/fetch_grid_rounds.py:624`), поэтому стороны сравниваем по id команды:

   ```python
   import pandas as pd
   S = "../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl004/grid"
   side = {"counter-terrorists": "CT", "terrorists": "T"}
   new_r = pd.read_parquet(f"{S}/rounds.parquet")
   new_g = pd.read_parquet(f"{S}/games.parquet")
   old_r = pd.read_parquet("data/cs2/grid/grid_rounds.parquet")
   old_g = pd.read_parquet("data/cs2/grid/grid_games.parquet")[["series_id", "game_number", "team_a_id"]]
   games = new_g.merge(old_g.rename(columns={"team_a_id": "old_a"}), on=["series_id", "game_number"])
   rounds = new_r.merge(old_r, on=["series_id", "game_number", "round_number"], suffixes=("", "_old"))
   rounds = rounds.merge(games[["series_id", "game_number", "team_a_id", "old_a"]], on=["series_id", "game_number"])
   same = rounds.team_a_id == rounds.old_a
   old_side = rounds.side_a.map(side).where(same, rounds.side_b.map(side))
   old_won = rounds.a_won.where(same, rounds.b_won)
   print(rounds.series_id.nunique(), len(rounds))
   print((rounds.started_at == rounds.started_at_utc).mean(), ((rounds.duration_s - rounds.duration_s_old).abs() < 1e-6).mean())
   print((rounds.team_a_side == old_side).mean(), (rounds.team_a_won == old_won).mean())
   ended = new_g.merge(pd.read_parquet("data/cs2/grid/grid_games.parquet")[["series_id", "game_number", "ended_at_utc"]], on=["series_id", "game_number"])
   ended = ended[ended.ended_at.notna()]
   gap = (pd.to_datetime(ended.ended_at, format="ISO8601") - pd.to_datetime(ended.ended_at_utc, format="ISO8601")).dt.total_seconds().abs()
   print(len(ended), (gap <= 0.01).mean(), ended.loc[gap > 0.01, ["series_id", "game_number"]].values.tolist())
   ```

   Ожидание: 23 серии; все четыре доли = 1,0; `ended_at` отличается от старого конца карты (`startedAt` + `duration` карты) не больше чем на 0,01 с. Исключение — только «грязные» карты из факта 1, их надо назвать в отчёте. Старые `series_id` — строки (`object`). Новые тоже должны быть строками. Любое другое расхождение — ошибка в коде, её надо разобрать до коммита.
8. Полный прогон s03 на весь период — не в этом шаге. Это ~6 000 серий × ~1,5 с ≈ 2,5 ч. Его делает пилот (STEP-014) после s02 на весь период.

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s03_fetch_grid_rounds.py`, `tests/test_cs2_grid_rounds.py`, `Makefile`. Сообщение — в стиле `b7cf0c44`, например «Fetch GRID rounds for linked CS2 series.», тело 2–3 строки, последняя — `Co-Authored-By: <модель> <noreply@...>`. Без `git push`. `passes` в `feature.json` не менять: это делает оркестратор после ревью.

## 8. Риски и открытые вопросы

- **Отличие от текста шага.** Шаг говорит «id и имена teams[0] и teams[1]». План ориентирует команды по `team_a_grid_id` s02 (решение 1). Иначе у ~1% серий (1 из 78) команда A и токен перевернулись бы, и `team_a_win` в каталоге был бы ложным. Для s04–s06: колонки `team_a_*` уже ориентированы, позиционный порядок Series State нигде не нужен.
- **Передать в STEP-005 и STEP-006.** s03 не чистит карты (решение 3). Отпечаток s04 должен считать раунд без ровно одного победителя несовпадением. s05 должна понимать, что `ended_at` и длительность последнего сегмента у части «грязных» карт (62 из 4 716, 1,3%) мусорные: сверка 1 их отсекает. s06 берёт `team_a_win` из `games.team_a_won`, только если победитель ровно один.
- **Пауза 1,05 с.** Лимит GRID для Series State неизвестен. `grid_graphql` ждёт сброса и повторяет 5 раз. Если лимит держится дольше, s03 падает. Повторный запуск продолжает с кэша. Запасной вариант — 3,2 с (~6 ч на весь период).
- **Постоянная ошибка GraphQL у одной серии** остановит каждый запуск `--fetch` на ней: `grid_graphql` бросает `RuntimeError` после 5 попыток. В 2 268 запросах цикла 1 таких ошибок не было (только пустой state). Если пилот на это наткнётся, нужна отдельная обработка.
- **Строгие проверки** (решение 7) могут уронить сборку на данных train-периода, которых в старом кэше нет (он с июня 2026). Сборка идёт из кэша, поэтому правка стоит перезапуска без сети. На серии 2025-11-02 структура та же (факт 3).
- **Повторные запросы пустых серий.** Серии без state и будущие серии s03 спрашивает при каждом `--fetch`. По циклу 1 это ~2% серий: при ~6 000 сериях ~120 запросов, ~3 мин.
- **Связь с Dota-кодом и s02.** s03 импортирует из `collect.s04_fetch_grid_starts` и `cs2.s02_link_grid`. Правка их сигнатур сломает s03. Это поймают basedpyright и новые тесты.
- Я не запускал новый код: фаза плана не пишет код.
