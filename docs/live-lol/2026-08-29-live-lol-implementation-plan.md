# LoL в live-paper: план реализации

Дата: 2026-08-29

Статус: готов к реализации.

Design-источники: `docs/live-lol/2026-08-28-live-lol-design.md` и
`docs/plans/lol-live.md`.

Этот файл заменяет design 2026-08-28 как рабочий документ. Design остаётся
историей решения.

## Результат

LoL торгует на Polymarket через GRID widget рядом с Dota. Dota остаётся в
live, LoL стартует в paper. Оператор переводит LoL в live одной строкой в
`.env` и пересозданием двух сервисов.

## Что изменилось против design 2026-08-28

Четыре решения приняты 2026-08-29. Каждое сокращает объём работы.

| Тема | Design 2026-08-28 | Этот план | Причина |
|---|---|---|---|
| Live-источник LoL | lolesports livestats | GRID widget | Замер в `dota_2_model/docs/experiments/lol-grid-widget.md`: livestats в live отстаёт на 55–60 с, окна моложе 60 с отдают 400. GRID на странице Polymarket отстаёт на 7–8 с. Рынок видит GRID. |
| Абстракция игры | `GameProvider`, `VenueProvider`, `MarketSession` | одна frozen dataclass `GameProfile` | GRID-путь для LoL совпадает с Dota. Разница — таблица XP, имена сторон, archive root, модель, профиль размера. Пять значений, не три интерфейса. |
| Переименование | `live_paper` -> `trader` во всех местах | не делаем | Трогает около 60 файлов и все тесты, поведение не меняет. Переименуем один файл конфига. |
| Kalshi для LoL | в первом деплое | отдельной стадией позже | Тикеры `KXLOLMAP` и `KXLOLGAME` не проверены. План `docs/plans/kalshi-live-paper-fixes.md` ещё не влит. |

Ещё три упрощения против design:

- `data/live_paper` не делим в Python. Два контейнера монтируют разные
  host-каталоги в один путь `/app/data/live_paper`. Ноль изменений в коде.
- Отдельный `stale_seconds` для LoL не нужен. Тот же socket, тот же cadence,
  тот же `GRID_FEED_STALE_SECONDS = 16.0`.
- Подкаталоги `matches/dota` и `matches/lol` не нужны. LoL match id всегда
  `grid-<series>-m<n>`, Dota Steam-матч всегда числовой. Коллизий нет.

## Почему GRID закрывает модель LoL

`data/lol/models/production/model.json` требует 12 фич. GRID widget отдаёт
каждую игровую фичу этого списка.

| Фича модели | Источник в GRID |
|---|---|
| `second` | scoreboard `currentSeconds` минус table `feed_delay` |
| `radiant_nw`, `dire_nw`, `radiant_nw_adv` | сумма `NetWorth` игроков стороны |
| `top1_nw_adv`, `radiant_top1_nw_ratio`, `dire_top1_nw_ratio` | `NetWorth` по игрокам |
| `deaths_radiant`, `deaths_dire` | сумма `Deaths` игроков стороны |
| `radiant_xp_adv` | `increaseLevel + 1` -> `LOL_LEVEL_XP` |
| `market_radiant_prior`, `market_p_radiant` | книга Polymarket, не игровой фид |

`model.json` пишет `xp_source: "level"`. Модель обучена на лестнице
`LOL_LEVEL_XP` по уровням. GRID отдаёт `increaseLevel`, из него получаем
уровень тем же способом, что и в Dota. Сырой `ExperiencePoints` из GRID не
используем: модель на нём не обучалась.

`state_source` модели равен `lolesports_livestats`. Мы подаём GRID. Это
осознанная замена источника. Dota уже работает так же: модель обучена на
STRATZ и OpenDota, а в live читает Steam или GRID.

## Известные ограничения

- `gridSeriesId` есть примерно у половины LoL-событий Polymarket. LPL часто
  без него. Рынки без `gridSeriesId` не торгуем. Это то же правило, что и в
  Dota: нет пригодного источника — пропускаем.
- Сходимость чисел GRID и livestats не измерена. Стадия 0 её проверяет на
  одном живом матче.
- LoL квотируем до `BUY_CUTOFF_SECOND = 540`, как Dota. Модель LoL обучена на
  секундах 0..540.

## Топология после реализации

```text
polymarket-collector
├── archive-dota  ── POLYMARKET_TAG_ID=102366 ── /var/lib/polymarket-dota-archive
├── compact-dota  ── POLYMARKET_TAG_ID=102366 ── /var/lib/polymarket-dota-archive
├── archive-lol   ── POLYMARKET_TAG_ID=65     ── /var/lib/polymarket-lol-archive
└── compact-lol   ── POLYMARKET_TAG_ID=65     ── /var/lib/polymarket-lol-archive

dota_2_model
├── trader-live   ── --mode live  ── ./data/live_paper_live:/app/data/live_paper
└── trader-paper  ── --mode paper ── ./data/live_paper_paper:/app/data/live_paper
```

Оба Trader монтируют оба архива read-only. `PK` и `BROWSER_ADDRESS` получает
только `trader-live`.

## Конфигурация оператора

```dotenv
DOTA_TRADING_MODE=live
LOL_TRADING_MODE=paper

DOTA_ARCHIVE_ROOT=/archive/dota
LOL_ARCHIVE_ROOT=/archive/lol

STEAM_KEYS=...
PK=...
BROWSER_ADDRESS=...
KALSHI_TRADING=paper
KALSHI_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=/root/secrets/kalshi/prod.key
```

`DOTA_TRADING_MODE` и `LOL_TRADING_MODE` принимают `off`, `paper`, `live`.
Процесс грузит только те игры, чей режим совпадает с его `--mode`.

`LIVE_TRADING` удаляем. Если переменная задана, процесс падает на старте.

## Стадии

Каждая стадия — один коммит. Каждая оставляет репозиторий рабочим. Dota не
меняет поведение до стадии 6.

---

### Стадия 0 — spike: запись живого LoL-фрейма GRID

Репозиторий: `../dota_2_model`.

Эта стадия — gate. Если payload LoL не разбирается текущими парсерами, план
меняется целиком. Делаем её первой.

Шаги:

1. Найти живой LoL-матч: `make run F=scripts/watch_lol_live.py`.
2. Запустить watcher на серию и сохранить сырые фреймы socket в файл.
3. Прогнать фреймы через `parse_frame`, `read_map_scoreboard`,
   `read_net_worth` из `src/shared/utils/grid_widgets.py`.
4. Проверить пять пунктов:
   - `infoText.text` сторон равен `BLUE` и `RED`;
   - имена групп совпадают с `GAME_STATE_GROUP` и `PLAYER_ENTITY_GROUP`;
   - строка игрока несёт `NetWorth`, `Kills`, `Deaths`, `KillAssistsGiven`;
   - `increaseLevel` появляется после первого level-up;
   - `entity.teamColor` имеет тот же вид, что в Dota.
5. Сохранить два payload (scoreboard и series_table) в
   `tests/lol_grid_widget_fixtures.py` рядом с `tests/grid_widget_fixtures.py`.

Проверка: новый тест разбирает LoL scoreboard и table, получает две стороны,
десять игроков, сумму net worth и сумму deaths.

Коммит: `Record a live LoL GRID widget payload as a parser fixture.`
Драйвер: без записанного LoL payload любой тест LoL-фида пишется вслепую.

Если пункт 4 не сходится: остановиться, доложить расхождение, план
пересматриваем.

---

### Стадия 1 — collector: две игры, четыре сервиса

Репозиторий: `../polymarket-collector`.

Изменения кода:

1. `src/config.ts`: `dotaTagId` -> `polymarketTagId`, `DOTA_TAG_ID` ->
   `POLYMARKET_TAG_ID`. Убрать default `"102366"`. Значение обязательно.
   Добавить в `validateSettings` проверку непустой строки.
2. `src/app.ts`: три места использования переименовать.
3. `src/discovery.ts:205`: текст ошибки `invalid Dota tag id` ->
   `invalid Polymarket tag id`.
4. `src/schema/primitives.ts:38`: обновить docstring.
5. `compose.yaml`: переименовать `archive` -> `archive-dota`,
   `compact` -> `compact-dota`. Добавить `archive-lol` и `compact-lol` с
   `POLYMARKET_TAG_ID: "65"` и bind на `/var/lib/polymarket-lol-archive`.

Классификация рынков не меняется. LoL на Polymarket использует те же
`moneyline` / `Match Winner` и `child_moneyline` / `Game N Winner`.
`gridSeriesId` читается из `eventMetadata` без привязки к игре.

Проверка: `yarn check` зелёный. Тест конфига падает при пустом
`POLYMARKET_TAG_ID`.

Коммит: `Make the collector tag id game-neutral and add the LoL pair of services.`
Драйвер: одна и та же программа собирает две игры, различие только в теге и
корне архива.

Деплой этой стадии безопасен сам по себе. Dota не меняется.

---

### Стадия 2 — GameProfile и конфиг размеров

Репозиторий: `../dota_2_model`. Поведение Dota не меняется.

1. Новый `src/live_paper/game_profile.py`:

   ```text
   @dataclass(frozen=True)
   class GameProfile:
       game: str                     # "dota" | "lol"
       archive_root_env: str         # DOTA_ARCHIVE_ROOT | LOL_ARCHIVE_ROOT
       mode_env: str                 # DOTA_TRADING_MODE | LOL_TRADING_MODE
       level_xp: tuple[int, ...]     # LEVEL_XP | LOL_LEVEL_XP
       side_0_text: str              # "RADIANT" | "BLUE"
       side_1_text: str              # "DIRE" | "RED"
       profile_name: str             # "dota-map" | "lol-map"
       production_model_dir: Path
       uses_steam: bool              # True | False
   ```

   Статический словарь `GAME_PROFILES: dict[str, GameProfile]`. Без registry-
   класса, без фабрик, без entry points.

2. `git mv config/dota-map.toml config/trading.toml`. Добавить
   `[profiles.lol-map]` с `base_size_usdc = 20.0` и остальными ключами по
   схеме. Остальные таблицы общие.

3. `src/live_paper/session_config.py`:
   - `TEMPLATE_PATH` -> `config/trading.toml`;
   - `read_template()` -> `read_template(games: tuple[GameProfile, ...])`;
   - требовать ровно те профили, которые нужны загруженным играм;
   - `ConfigTemplate.profile` -> `profiles: dict[str, dict[str, object]]`;
   - долларовые лимиты `[risk]` считать от суммы `base_size_usdc` всех
     загруженных игр;
   - `DOLLAR_MULTIPLES["profiles.dota-map"]` -> общий ключ `"profiles"`,
     применять к каждому профилю от его собственного `base_size_usdc`.

   Про сумму: `[risk]` — единственная таблица на процесс, потому что Engine
   один. Сумма даёт каждой игре её собственный запас. Пометить строку
   комментарием `ponytail:` с потолком: `max_market_notional_usdc` при сумме
   становится свободнее, чем нужно меньшей игре; настоящий per-market предел
   держит `q_max_usdc` из профиля игры.

4. `materialize_config_dir` выбирает профиль по игре матча.
   `materialize_wallet_config_dir` берёт профиль игры с наибольшим
   `base_size_usdc`.

Проверка: существующие тесты `session_config` проходят после правки путей.
Новый тест: два профиля в шаблоне дают `daily_loss_kill_usdc` от суммы
размеров, а `q_max_usdc` каждого профиля — от своего размера.

Коммит: `Add GameProfile and per-game size profiles in one trading.toml.`
Драйвер: размеры и таблицы XP различаются по играм, всё остальное общее.

---

### Стадия 3 — режим на процесс и два сервиса Trader

Репозиторий: `../dota_2_model`.

1. `src/live_paper/trading_mode.py`:
   - `execution_mode()` читает аргумент CLI, а не `LIVE_TRADING`;
   - новая `assigned_games(mode) -> tuple[GameProfile, ...]` читает
     `DOTA_TRADING_MODE` и `LOL_TRADING_MODE`, принимает только `off`,
     `paper`, `live`, возвращает игры своего режима;
   - новая `reject_legacy_env()` падает, если задан `LIVE_TRADING`.

2. `src/live_paper/orchestrator.py`: добавить обязательный
   `--mode live|paper`. Передать режим в `run_wallet_daemon`.

3. `src/live_paper/wallet_host.py`:
   - `open_wallet_host` принимает режим и список игр;
   - `require_live_wallet()` только при live;
   - `SteamClient` строится только если среди игр есть игра с `uses_steam`;
   - Kalshi подключается только если среди игр есть игра с Kalshi-серией;
   - процесс без назначенных игр логирует это и остаётся живым, ничего не
     делая.

4. `compose.yaml`: заменить сервис `live-paper` на `trader-live` и
   `trader-paper`.

   ```yaml
   trader-live:
     command: ["uv","run","--frozen","--no-dev","python","-m","live_paper.orchestrator","daemon","--mode","live"]
     volumes:
       - /var/lib/polymarket-dota-archive:/archive/dota:ro
       - /var/lib/polymarket-lol-archive:/archive/lol:ro
       - ./data/live_paper_live:/app/data/live_paper
   trader-paper:
     command: [... "--mode","paper"]
     volumes:
       - /var/lib/polymarket-dota-archive:/archive/dota:ro
       - /var/lib/polymarket-lol-archive:/archive/lol:ro
       - ./data/live_paper_paper:/app/data/live_paper
   ```

   `PK` и `BROWSER_ADDRESS` перечислить в `environment` только у
   `trader-live`. `env_file` для секретов кошелька больше не используем.

5. `Makefile`: `live-paper` -> `trader MODE=paper`.

Разные host-каталоги дают разные `wallet/live.db`, `wallet/kalshi.db`,
`kalshi.db.lock`, `engine_journal` и match journals. В Python пути не
меняются.

Проверка:
- `LIVE_TRADING=1` на старте даёт ошибку;
- `DOTA_TRADING_MODE=live LOL_TRADING_MODE=paper` даёт live-процессу только
  Dota, paper-процессу только LoL;
- неизвестное значение режима отвергается;
- процесс без игр стартует и не открывает Steam, Kalshi и кошелёк.

Коммит: `Route games to a live or paper process by per-game trading mode.`
Драйвер: paper-режим ставится глобальным monkey-patch на `ExecutionGateway`,
поэтому live и paper не могут жить в одном процессе.

Деплой этой стадии безопасен: `LOL_TRADING_MODE=off`, и Dota работает как
раньше в `trader-live`.

---

### Стадия 4 — discovery для LoL

Репозиторий: `../dota_2_model`.

1. `src/live_paper/collector_sidecars.py`: `load_archive_root()` ->
   `load_archive_root(profile)` читает `profile.archive_root_env`.

2. `src/live_paper/discovery.py`:
   - `MarketDiscovery.__init__` принимает `GameProfile` и
     `steam_client: SteamClient | None`;
   - при `steam_client is None` пропустить `_fetch_live_league_games` и
     `_fetch_server_ids`, оставить `games = ()` и пустые `server_ids`;
   - `_resolve_cycle` не трогаем: без Steam-кандидатов `_steam_source`
     возвращает None, и остаётся только `_grid_source`;
   - `_grid_source` читает стороны через `profile.side_0_text` и
     `profile.side_1_text`;
   - в строку лога цикла добавить `game=`.

3. `src/live_paper/bindings.py`: `DiscoveredMatch` получает поле `game`.

4. `src/live_paper/match_meta.py`: `match.json` получает поле `game`, версия
   схемы поднимается. Старые записи читаются как `dota`.

5. `src/live_paper/wallet_host.py` и `cadence.py`: один `MarketDiscovery` на
   игру, `poll_discoveries` обходит их все и склеивает результат одного
   цикла.

Проверка (фикстуры из стадии 0):
- LoL sidecar с `gridSeriesId` плюс LoL scoreboard дают один
  `DiscoveredMatch` с `game="lol"`, источником GRID, верным номером карты и
  ориентацией;
- LoL sidecar без `gridSeriesId` не даёт матча;
- две стороны с одинаковым `infoText` дают ноль матчей;
- Dota discovery не изменилась: существующие тесты проходят.

Коммит: `Discover LoL markets from the LoL archive through GRID only.`
Драйвер: у LoL нет Steam, а `_grid_source` уже полный GRID-путь.

LoL всё ещё не котирует: режим `off`.

---

### Стадия 5 — фид и модель LoL

Репозиторий: `../dota_2_model`.

1. `src/live_paper/grid_feed.py`:
   - `read_board_sides` принимает имена сторон вместо констант
     `RADIANT_SIDE` и `DIRE_SIDE`;
   - `_live_snapshot` считает XP через `xp_advantage(profile.level_xp, ...)`
     вместо `radiant_xp_advantage`;
   - `GridFrameReducer.__init__` принимает `GameProfile`.

2. `src/live_paper/grid_live_feed.py` и `feed_selection.py`: пробросить
   профиль. Для LoL `_pick_live_source` проверяет только GRID.

3. `src/live_paper/model_server.py`: `load_production_model()` ->
   `load_production_model(profile)`. LoL читает
   `data/lol/models/production/model.txt` и `model.json`.

4. `src/live_paper/wallet_host.py`: одна модель на игру, загружается на
   старте по числу назначенных игр.

5. `src/live_paper/match_worker.py`: выбирает модель и профиль по
   `discovered.game`.

6. `compose.yaml`: монтировать `./data/lol/models:/app/data/lol/models:ro`.

Новых констант нет. `GRID_FEED_STALE_SECONDS = 16.0` и
`BUY_CUTOFF_SECOND = 540` подходят обеим играм.

Проверка (фикстуры из стадии 0):
- LoL scoreboard плюс table дают `GameSnapshot` с верными `radiant_nw`,
  `dire_nw`, `radiant_nw_adv`, `deaths_*`, `top*`;
- `radiant_xp_adv` считается по `LOL_LEVEL_XP`, а не по `LEVEL_XP`;
- таблица без `increaseLevel` даёт всем игрокам уровень 1 и `radiant_xp_adv`
  ноль;
- модель LoL грузится и принимает 12 фич без ошибки контракта;
- Dota snapshot не изменился: существующие тесты `test_grid_feed` проходят.

Коммит: `Feed LoL GRID ticks into the LoL model.`
Драйвер: у GRID для LoL стороны BLUE и RED, а лестница XP своя.

---

### Стадия 6 — деплой и документация

Репозитории: `../dota_2_model`, `../polymarket-collector`,
`betting_workspace`.

Порядок на VPS:

1. Создать `/var/lib/polymarket-lol-archive`.
2. В `polymarket-collector` подтянуть код, задать `POLYMARKET_TAG_ID` в
   `.env` или в compose, `docker compose up -d`.
3. Дождаться свежих LoL sidecars и книг в новом архиве.
4. В `dota_2_model` подтянуть код. Задать `.env`:

   ```dotenv
   DOTA_TRADING_MODE=live
   LOL_TRADING_MODE=paper
   ```

5. `docker compose up -d --force-recreate`. `docker compose restart` не
   перечитывает `env_file` и здесь не подходит.
6. Проверить: Dota в live в `trader-live`, LoL в paper в `trader-paper`,
   реальных LoL-ордеров нет.

Документация в том же коммите:

- `../dota_2_model/AGENTS.md`: раздел Live paper, новые команды и режимы.
- `../dota_2_model/docs/live-paper.md`: GRID для LoL, два процесса, режимы.
- `.learnings/dota_2_model.md`: таблица «что перезапускать» под два сервиса,
  новые имена и каталоги.
- `.learnings/polymarket-collector.md`: четыре сервиса, `POLYMARKET_TAG_ID`.
- `.shared-skills/vps-live-paper/`: имена сервисов, два каталога данных,
  разрез по игре.

Коммит: `Deploy LoL paper next to live Dota and update the operator docs.`
Драйвер: имена сервисов и каталогов меняются в одном деплое, документация
идёт вместе с ними.

---

### Стадия 7 — Kalshi для LoL (позже, отдельная задача)

Не входит в первый деплой. Предусловия:

- план `docs/plans/kalshi-live-paper-fixes.md` влит;
- существование серий `KXLOLMAP` и `KXLOLGAME` проверено одним REST-запросом.

Объём:

- `series_ticker_from_kind(game, kind)` вместо жёстких `KXDOTA2MAP` и
  `KXDOTA2GAME`;
- `[kalshi]` в `config/trading.toml` становится `[kalshi.dota]` и
  `[kalshi.lol]` со своими `base_size_usd`;
- `stale` игрового фида отменяет входные заявки на обеих биржах одного матча.

Последний пункт — существующая асимметрия Dota, найденная в design
2026-08-28. Она чинится вместе с этой стадией, а не раньше.

## Переход LoL из paper в live

Между матчами:

1. `LOL_TRADING_MODE=live` в `.env`.
2. `docker compose up -d --force-recreate trader-live trader-paper`.
3. Проверить, что у `trader-paper` нет назначенных игр.
4. Проверить, что `trader-live` взял LoL с размером 20 USDC.

Paper-позиции, ордера и состояние счёта в live не переносятся. Они остаются
в `./data/live_paper_paper`.

Возврат: `LOL_TRADING_MODE=paper` или `off`, та же команда пересоздания.
Collectors при смене режима не трогаем.

## Порядок и зависимости

```text
Стадия 0 (gate)
   |
   +-- Стадия 1 (collector, независима)
   |
   Стадия 2 -> Стадия 3 -> Стадия 4 -> Стадия 5 -> Стадия 6
                                                      |
                                                   Стадия 7
```

Стадия 1 не зависит от стадий 2–5 и деплоится отдельно. Стадии 2–5 идут
строго по порядку. Деплой возможен после стадии 6.

## Правила работы

- Работа идёт в `main` каждого репозитория.
- `../poly-maker` не трогаем.
- После правки Python: `make lint-all` в `dota_2_model`, всё зелёное.
- После правки TypeScript: `yarn check` в `polymarket-collector`, всё
  зелёное.
- Каждая нетривиальная граница оставляет один запускаемый тест.
- Драйвер решения идёт в текст коммита.
