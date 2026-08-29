# LoL в live-paper: план реализации

Дата: 2026-08-29, обновлён 2026-08-30.

Статус: готов к реализации после отдельного переобучения на GRID-золоте.

Цифры по GRID — в `docs/live-lol/2026-08-29-grid-source-verification.md`.
Сырые записи — в `docs/live-lol/recordings/`.

## Результат

LoL торгует на Polymarket через GRID widget рядом с Dota. Dota остаётся в
live, LoL стартует в paper. Оператор переводит LoL в live одной строкой в
`.env` и пересозданием двух сервисов.

## Не входит в этот план

Перед стадиями 0–7, отдельно, сейчас уже идёт:

1. Докачать livestats windows до конца карты (все секунды).
2. Докачать livestats `details` для тех же карт.
3. Переобучить модель на золоте `totalGold − consumed`. Это GRID
   `NetWorth` (`Money + LoadoutValue`). На CBLOL реконструкция сходится с
   GRID до 0.2–0.5%.

К старту этого плана production-модель лежит в
`data/lol/models/production/` и обучена на том золоте, которое придёт с
GRID. Live-путь читает GRID `NetWorth` как есть. Реконструкция на live не
нужна.

## Что уже проверено

Все три проверки GRID закрыты 2026-08-29. Новых замеров перед стартом
стадий не нужно.

| Вопрос | Результат | Правка в этом плане |
|---|---|---|
| Часы GRID и livestats | `grid_second = livestats_second + 6.7`, разброс 1.7 с на 10 смертях. Меньше, чем `source_lag_seconds = 10` | нет |
| 12 фич модели | смерти совпадают; `xp_adv` медиана 0; сырой GRID `NetWorth` ниже livestats `totalGold` на 4–5% | золото закрывается переобучением выше, не стадиями 0–7 |
| Доля `gridSeriesId` | 21 день: 61% map-рынков, **73% объёма**. Без id в основном LPL | нет: рынки без `gridSeriesId` не торгуем |
| Парсер GRID | LoL разбирается без правок. Стороны `BLUE`/`RED`. `delay = 8` | стадия 0 — фикстуры из готовых записей, не новый spike |
| Livestats в live | окна моложе 60 с отдают 400; отставание 55–60 с | источник — GRID, не livestats |
| Формула top1 | модель LoL: `top1 / сумма`; live Dota: `top1 / (сумма − top1)`. На спавне 0.20 против 0.25 | стадия 5: LoL считает `top1 / сумма` |

GRID на странице Polymarket отстаёт на 7–8 с. Рынок видит GRID.

## Как устроено

Одна frozen dataclass `GameProfile`. GRID-путь для LoL совпадает с Dota.
Разница — таблица XP, имена сторон, archive root, модель, профиль размера и
gold-velocity gate. Dota сохраняет предел 350 золота за 30 секунд. LoL не
применяет этот gate.

Переименование `live_paper` → `trader` во всех файлах не делаем. Меняем
имена двух compose-сервисов и одного файла конфига.

Kalshi для LoL — стадия 7, не первый деплой.

`data/live_paper` в Python не делим. Два контейнера монтируют разные
host-каталоги в `/app/data/live_paper`.

Отдельный `stale_seconds` для LoL не нужен: тот же socket, тот же cadence,
`GRID_FEED_STALE_SECONDS = 16.0`. На CBLOL GRID отдавал сэмпл раз в 4.3 с.

Подкаталоги `matches/dota` и `matches/lol` не нужны. LoL match id всегда
`grid-<series>-m<n>`, Dota Steam-матч всегда числовой.

## Почему GRID закрывает модель LoL

`data/lol/models/production/model.json` требует 12 фич. GRID widget отдаёт
каждую игровую фичу этого списка.

| Фича модели | Источник в GRID |
|---|---|
| `second` | scoreboard `currentSeconds` минус table `feed_delay` |
| `radiant_nw`, `dire_nw`, `radiant_nw_adv` | сумма `NetWorth` игроков стороны |
| `top1_nw_adv`, `radiant_top1_nw_ratio`, `dire_top1_nw_ratio` | `NetWorth` по игрокам, ratio как `top1 / сумма` |
| `deaths_radiant`, `deaths_dire` | сумма `Deaths` игроков стороны |
| `radiant_xp_adv` | `increaseLevel + 1` -> `LOL_LEVEL_XP` |
| `market_radiant_prior`, `market_p_radiant` | книга Polymarket, не игровой фид |

`model.json` пишет `xp_source: "level"`. Модель обучена на лестнице
`LOL_LEVEL_XP` по уровням. GRID отдаёт `increaseLevel`, из него получаем
уровень тем же способом, что и в Dota. Сырой `ExperiencePoints` из GRID не
используем: модель на нём не обучалась.

После переобучения фичи золота считаются из GRID `NetWorth`. Сырой
livestats `totalGold` в live не используем.

## Известные ограничения

- `gridSeriesId` есть у 61% map-рынков и 73% объёма за 21 день. LPL часто
  без него. Рынки без `gridSeriesId` не торгуем. Это то же правило, что и в
  Dota: нет пригодного источника — пропускаем.
- GRID не шлёт `increaseLevel`, пока на карте не случился первый level-up.
  До этого всем игрокам уровень 1 и `xp_adv` ноль. Это верно до первого
  level-up.
- LoL квотируем до `BUY_CUTOFF_SECOND = 540`, как Dota. Модель LoL обучена
  на секундах 0..540.

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

### Стадия 0 — фикстуры из живой записи GRID

Репозиторий: `../dota_2_model`.

Gate пройден 2026-08-29. Парсер разбирает LoL без правок. Живые payload
лежат в `docs/live-lol/recordings/` (CBLOL `lol-fxw7-los-2026-08-29`, LEC
`lol-navi-gx-2026-08-29`). Новый spike не нужен.

Шаги:

1. Вырезать из записей два payload (scoreboard и series_table) в
   `tests/lol_grid_widget_fixtures.py` рядом с `tests/grid_widget_fixtures.py`.
2. Прогнать их через `parse_frame`, `read_map_scoreboard`, `read_net_worth`
   из `src/shared/utils/grid_widgets.py`.
3. Зафиксировать в тесте уже проверенное:
   - `infoText.text` сторон равен `BLUE` и `RED`;
   - имена групп совпадают с `GAME_STATE_GROUP` и `PLAYER_ENTITY_GROUP`;
   - строка игрока несёт `NetWorth`, `Kills`, `Deaths`, `KillAssistsGiven`;
   - `increaseLevel` появляется после первого level-up;
   - `entity.teamColor` имеет тот же вид, что в Dota.

Проверка: новый тест разбирает LoL scoreboard и table, получает две стороны,
десять игроков, сумму net worth и сумму deaths.

Коммит: `Add LoL GRID widget parser fixtures from the 2026-08-29 recordings.`
Драйвер: без LoL payload любой тест LoL-фида пишется вслепую.

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
       max_abs_nw_delta_30: float | None  # 350.0 | None
   ```

   Статический словарь `GAME_PROFILES: dict[str, GameProfile]`. Без registry-
   класса, без фабрик, без entry points. Dota получает
   `max_abs_nw_delta_30 = 350.0`. LoL получает `None`, которое полностью
   отключает gold-velocity gate без фиктивного большого порога.

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
   - LoL считает `radiant_top1_nw_ratio` и `dire_top1_nw_ratio` как
     `top1 / сумма` (`lol/livestats_frames.py:396`). Dota-путь не трогаем:
     он остаётся `top1 / (сумма − top1)` в `build_top_player_features`.
     `build_top_player_features` получает правило игры, или LoL-ветка
     считает ratio своей функцией.

2. `src/live_paper/grid_live_feed.py` и `feed_selection.py`: пробросить
   профиль. Для LoL `_pick_live_source` проверяет только GRID.

3. `src/live_paper/model_server.py`: `load_production_model()` ->
   `load_production_model(profile)`. LoL читает
   `data/lol/models/production/model.txt` и `model.json`.

4. `src/live_paper/wallet_host.py`: одна модель на игру, загружается на
   старте по числу назначенных игр.

5. `src/live_paper/match_worker.py`: выбирает модель и профиль по
   `discovered.game`.

6. `src/live_paper/session_quoting.py`: добавить
   `max_abs_nw_delta_30: float | None` в `EntryGateInputs`. Все callers
   передают значение из `GameProfile`. При `None` функция `evaluate_entry`
   пропускает gold-velocity gate. Остальные entry gates остаются общими для
   Dota и LoL.

7. `compose.yaml`: монтировать `./data/lol/models:/app/data/lol/models:ro`.

Новых констант нет. `GRID_FEED_STALE_SECONDS = 16.0` и
`BUY_CUTOFF_SECOND = 540` подходят обеим играм.

Проверка (фикстуры из стадии 0):
- LoL scoreboard плюс table дают `GameSnapshot` с верными `radiant_nw`,
  `dire_nw`, `radiant_nw_adv`, `deaths_*`, `top*`;
- `radiant_xp_adv` считается по `LOL_LEVEL_XP`, а не по `LEVEL_XP`;
- таблица без `increaseLevel` даёт всем игрокам уровень 1 и `radiant_xp_adv`
  ноль;
- на спавне с равным золотом LoL `radiant_top1_nw_ratio` равен 0.20
  (`top1 / сумма`), не 0.25 (`top1 / (сумма − top1)`);
- LoL не блокирует вход при изменении перевеса больше 350 золота за 30 секунд;
- Dota по-прежнему блокирует вход при изменении перевеса от 350 золота;
- модель LoL грузится и принимает 12 фич без ошибки контракта;
- Dota snapshot не изменился: существующие тесты `test_grid_feed` проходят.

Коммит: `Feed LoL GRID ticks into the LoL model.`
Драйвер: у GRID для LoL стороны BLUE и RED, лестница XP своя, top1 ratio
считается как в обучении.

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

Последний пункт — существующая асимметрия Dota: watchdog сразу очищает
Polymarket entries, но не отправляет немедленную stale-отмену в Kalshi.
Чинится вместе с этой стадией, а не раньше.

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
Стадия 0 (фикстуры)
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
