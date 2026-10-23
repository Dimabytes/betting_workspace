# Исходный план CS2 live

Источник: `/Users/dimabytes/.claude/plans/pasted-content-id-c7bf-foamy-wilkes.md`.
Снимок сохранён 2026-10-23 для деталей парсинга и ожидаемых значений replay.
Актуальные требования и порядок выполнения находятся в `feature.json` рядом.

Последующие решения пользователя имеют приоритет над текстом ниже:

- Без суток обкатки и ожидания полной карты: пакет приёмочных проверок до часа, желательно быстрее.
- При неполной истории выбираем простое решение: новые BUY закрыты до следующей карты, восстановления истории нет.
- Onchain CS2 начинается с 2026-10-23 UTC включительно. Это заменяет согласие на повторный проход: ранние даты не требуют CS2-манифестов.

Уточнения по прочитанному коду:

- Текущий record-only пишет фид, но не рассчитывает сигналы: приёмка сигналов использует replay и интеграционные тесты без исполнения.
- Регистрация CS2-профиля идёт после создания его зависимостей.
- Проверенный LIVE-манифест находится в `data/backtests/cs2_maker/LIVE/seed0/manifest.json`.
- Время terminal scoreboard и последнего END различается: тесты берут фактические occurredAt кадров, не противоречивую краткую формулировку исходника.
- Расписание и доступность рынков ниже — сведения исходного документа, не новая проверка внешних источников.

---

# CS2 в лайв: коллектор, потом трейдер, потом дашборд

## Контекст

24.10.2026 стартует PGL Masters Bucharest 2026. Пользователь хочет раундовую стратегию CS2 из бэктеста в лайв на VPS `sun` с клипом $5. Три этапа: коллектор архивирует книги CS2, трейдер торгует CS2, дашборд показывает CS2.

Требования пользователя:

1. **Dota и LoL не деградируют.** Их торговля и PnL сейчас устраивают. Любое изменение общего кода доказывается, а не обещается (раздел «Гарантия Dota/LoL»).
2. **Последовательно, два трека:** коллектор, затем трейдер (дашборд — хвост трейдера). Без параллельных worktree.
3. **Без хаков.** Третья игра с другим типом стратегии — это паттерн, а не россыпь `if game == "cs2"`. Сначала выделяем швы чистым рефактором, потом CS2 становится второй реализацией каждого шва.

Что есть сейчас (проверено по коду):

- **Коллектор** (`../polymarket-collector`) знает Dota (tag 102366) и LoL (tag 65). Фильтр карт принимает только `Game N Winner` (`src/selection.ts:15`); у CS2 — `Map 1 Winner` / `Map 2 Winner`. Списки игр зашиты в `compact`, `onchain`, `ship`.
- **Трейдер** (`../esports-trader/src/trader`): `GAME_PROFILES` = {dota, lol}; стратегия жёстко `FOLLOW300_SIDES` (`src/strategy/engine.py:141`); парсер GRID-таблицы падает на CS2 (`KeyError 'NetWorth'`); модельный сервер MOBA-only; `GameProfile` несёт MOBA-поля (`level_xp`, `side_0_text`, `side_1_text`), которые для CS2 бессмысленны. Раундовая стратегия (`ROUND_SIDES`, `RoundSignal`) лежит в `src/backtest/cs2_round_sides.py`; признаки, cash-оценка и инференс — в `src/cs2/s11_*.py`, считаются офлайн из реплеев bo3.gg.
- **Дашборд** не падает на `game="cs2"`, но подписи Dota, CS2 уходит в «Other», `summarize.py --game` не принимает cs2.
- **Стратегия LIVE** (`data/backtests/cs2_maker/LIVE` → `validation_join_delta01_cutend_p45_d01-size900`): порог 1c (вход и выход), ступень $900, 3 ступени, кап 6, вход 0.45–0.85, SELL по `max(ask, m0 + up)`, round-lots, pistol-ban, overtime delta 0, покупки только от call (`end_real + 9`) до live-старта раунда, cutoff = конец карты. Net +$14 790 на 636 картах, один seed.
- **PGL Masters Bucharest 2026** не в `config/cs2_league_whitelist.json`. На Gamma рынков матчей ещё нет (только фьючерс `pgl-masters-bucharest-2026-winner`); суффикс титула узнаем утром. GRID (`allSeries`, titleId 28) турнир покрывает: 24.10 серии `3018121`–`3018128` (FUT vs TYLOO и Aurora vs The MongolZ 07:00 UTC, Spirit vs BetBoom и MOUZ vs Liquid 10:00, Legacy vs Nemiga и Vitality vs 9z 13:00, G2 vs NaVi и FURIA vs DENDELE 16:00), все BO3; 25–26.10 серии `TBD`. Точную дату конца турнира смотреть на Liquipedia (мне сайт закрыт).

Решения пользователя: турнир PGL Masters Bucharest 2026; добавить в whitelist; kashrock не используем.

## Сроки (честно)

Последовательный чистый путь: коллектор 2–3 ч с раскаткой; рефактор швов с доказательством 4–6 ч; CS2-реализации 8–12 ч с паритетом на записи; дашборд 0.5 ч; раскатка и проверка 1 ч. Первые карты PGL 24.10 в 07:00 UTC. Первые ордера CS2 реально 25.10, с отдельным днём обкатки рефактора на Dota/LoL — 26.10. Турнир многодневный, это не потеря.

## Границы и риски

- Парсер CS2-кадров и раундовые признаки из GRID никогда не сверялись с офлайн-признаками из реплеев. Единственная запись — `data/cs2/live/cs2-damaju-hachae-2026-10-21/` (только `integrity_safe_*`); её серии нет в каталоге, паритет — против выверенных ожиданий с ленты.
- Белый список бэктеста не покрывает завтрашний турнир: PnL теста ни о чём не говорит, тест проверяет проводку.
- 57% CS2-событий Gamma без `gridSeriesId`; без него серия не найдётся (Steam у CS2 нет). Поиск по именам через GRID `allSeries` (как `s02_link_grid.py`) — не в v1.

---

## Гарантия Dota/LoL

Принцип: сначала рефактор без изменения поведения (шаг 2A), доказанный дифференциально, затем CS2 как новые файлы и новые реализации швов (шаг 2B). Dota/LoL проходят через новый код только в 2A.

Что в 2A меняется на пути Dota/LoL и почему это чистый перенос:

| Место | Изменение | Поведение Dota/LoL |
|---|---|---|
| `src/trader/game_profile.py` | MOBA-поля уходят в `MobaGridFeed`, каталоги в `Follow300Strategy`; значения те же | те же |
| `src/trader/grid_feed.py` | `GridFrameReducer(spec)` вместо `(profile)`; `if profile.game == "lol"` → `spec.top_builder` | те же функции, тот же порядок |
| `src/trader/match_worker.py` | `_latched_fair`, `_on_kill_tick`, `BoardHistory`, `SnapshotHistory` переезжают в `Follow300Signals`; воркер зовёт протокол | тот же код, другой файл |
| `src/trader/session_core.py` | `LiveCore(..., sides)`; `step` → `step_with_sides(sides=FOLLOW300_SIDES)` | `step` сам так делает сегодня |
| `src/trader/host_resources.py` | `profile.strategy.load_models()` | тот же `load_model` на те же каталоги |
| `src/trader/discovery.py`, `feed_selection.py`, `source_picker.py` | `profile.feed.read_board_sides` / `probe_delay` / `build_reducer`, `profile.league_whitelist_path` | те же функции |
| `src/strategy/policy.py` | `BUY_CUTOFF_SECOND_BY_GAME[game]`, dota/lol = 480 | то же число |
| `src/trader/lol_league_filter.py` → `league_filter.py` | путь whitelist из профиля | тот же файл, тот же разбор |

Доказательство (ворота на merge 2A, все четыре):

1. `make test` зелёный, `basedpyright` 0, goldens follow300 smoke и extraction-oracle без изменений.
2. **Реплей сохранённых core-трейсов**: `replay_core_trace --policy current` на завершённых live-сессиях Dota и LoL из `data/trader` (синхронизировать `scripts/sync_trader.py`) — планы, wake и digest совпадают с записанными на 100% (та же методика, что в `investigations/2026-10-22-vps-rollout-audit`: 8 сессий, 324 491 событие, 0 расхождений). Скрипты аудита в `work/orchestrator/` переиспользовать.
3. **Дифференциал GRID-редьюсера**: прогнать `grid_state.jsonl` последних сессий Dota и LoL (≥ 10 000 кадров) через старый и новый `GridFrameReducer` — тики равны побайтно (как `work/orchestrator/grid-differential.json`).
4. Загрузка трёх продакшн-моделей (Dota GRID 81, Oddin 74, LoL 81) штатным путём, те же `ModelReference`.

Численные константы Dota/LoL не меняются; тест закрепляет `follow300_policy(game=g)` для dota и lol: `buy_cutoff_second == 480`, `min_abs_delta`, `exit_abs_delta`, `min_entry_price`, `reanchor_drift_k`, `mid_spike_cooloff_s`.

Раскатка: шаг 2A можно выкатить отдельно и дать Dota/LoL сутки на нём до включения CS2 (рекомендую, если сроки позволяют). Любой шаг отката: `git checkout <prev>` + `docker compose restart live` (код примонтирован), CS2 выключается `CS2_TRADING_MODE=off` + `up -d --force-recreate live`.

Коллектор: сервисы `archive-dota` и `archive-lol` не пересоздаются и код сбора не меняется. Меняются `compact`, `onchain`, `ship` (третий корень). Ворота: `yarn check` зелёный, после пересоздания первый проход каждого сервиса в логах без ошибок, `compact` в 03:00 UTC прошёл Dota → LoL → CS2.

---

## Порядок работ

Этап 1 (коллектор, раскатка сразу) → 2A (швы, доказательство, при возможности раскатка) → 2B (CS2-реализации) → 2C (паритет на записи, CLI) → Этап 3 (дашборд) → 2D (раскатка CS2). Один чекаут `../esports-trader`, ветка `main`, по одному коммиту на шаг.

---

## Этап 1. Коллектор: `archive-cs2`

Репо `../polymarket-collector`, `main`. Проверка: `yarn check`.

### 1.1 Фильтр рынков карт

- `src/selection.ts:15`: `MAP_WINNER_TITLE_PATTERN = /^(?:Game|Map) ([1-9][0-9]*) Winner$/`; `mapNumber = Number(match[1])` без изменений.
- `src/selection.test.ts:53-67`: `Map 1 Winner` → 1, `Map 2 Winner` → 2, `map 1 winner` → отказ.
- `docs/polymarket_dota_archive_contracts.md` §3.2 (строки 86-113): дописать `Map N Winner`.

### 1.2 Сервис сбора

- `compose.yaml`: блок `archive-lol` (27-50) копируется как `archive-cs2`: `POLYMARKET_TAG_ID: "100780"`, bind `/var/lib/polymarket-cs2-archive` → `/data`, тот же `image`, без `build`.
- `src/telegram.ts:31-36` `gameLabel`: `"100780" → "cs2"`.
- `src/docker.test.ts`: счётчики `init`/`restart`/`stop_grace_period` → 6, `oom_score_adj` → 3, `- type: bind` → 13, блок `archive-cs2` по образцу 128-215.

### 1.3 compact, onchain, ship: третий корень

Один паттерн: третье поле `cs2ArchiveRoot` из `ARCHIVE_ROOT_CS2` и третий элемент `{ game: "cs2", archiveRoot }` в каждом списке. Порядок Dota, LoL, CS2.

- `src/config.ts:22-30, 62-64`; `src/app.ts:251-254`.
- `src/onchain-config.ts:21-22, 47-48, 68-69, ~164`; `src/onchain-app.ts:78, 275-284, 407, 419-422`; `src/onchain-plan.ts:54` и `src/onchain-day.ts:68` `GAMES = ["dota", "lol", "cs2"] as const`; `src/telegram.ts:118-119`; CLI `src/onchain-import.ts:146-176`, `src/onchain-import-main.ts:25, 69-72, 127-130`, `src/onchain-repair-main.ts:32-35`.
- `src/ship-config.ts:11-12, 32-33`, `src/ship-app.ts:129-139`.
- `compose.yaml`: `compact`, `onchain`, `ship` получают `ARCHIVE_ROOT_CS2: /data-cs2` и bind `/var/lib/polymarket-cs2-archive:/data-cs2`.
- Тесты со списком игр (расширить до трёх): `config.test.ts:32,40,78-80,231,250,334-335`, `app.test.ts:75-86,562,649`, `compaction-scheduler.test.ts:399,434,441`, `onchain.testing.ts:187-200`, `onchain-app.test.ts:71,111,132,332-337,483,505-525`, `onchain-config.test.ts:16`, `onchain-day.test.ts:113`, `onchain-plan.test.ts:167-210`, `onchain-import.test.ts:75,146,233-366`, `onchain-catalog.test.ts`, `onchain-reconcile.test.ts`, `ship-config.test.ts:7,44`, `ship-app.test.ts:14`, `ship.test.ts:148`, `ship-alerts.test.ts:18,68`, `telegram.test.ts:75,146-147`.

Ловушки:
- `onchain-plan.ts:169-193`: игра без `manifests/onchain/<date>.json` даёт `allReady=false` → все дни от `ONCHAIN_START_DATE` снова `work`. Прочитать `docs/learnings.md:15-16, 58-59`; принять один проход пересчёта (RPC только на новые дни) или отложить cs2 в `onchain` на день (книги и трейды от этого не зависят).
- `ship` пишет в `cs2/` на NAS, куда пишет `s08_fetch_telonex_books.py` (`esports-trader/AGENTS.md:19`). Решение: включить `ship` для cs2, `s08` после этого не запускать на дни, ушедшие с VPS; записать в `AGENTS.md` и `.learnings/polymarket-collector.md`.

### 1.4 Раскатка (`ssh sun`, `/root/work/polymarket-collector`)

```bash
sudo mkdir -p /var/lib/polymarket-cs2-archive && sudo chown 10001:10001 /var/lib/polymarket-cs2-archive
git pull
docker compose build archive-dota
docker compose up -d archive-cs2
docker compose up -d --force-recreate --no-deps compact onchain ship   # compact вне 03:00 UTC; onchain — когда нет import
docker compose logs --since 5m archive-cs2 compact onchain ship
```

Проверка: через 2–3 минуты `ls /var/lib/polymarket-cs2-archive/metadata/markets | wc -l` > 0, в сайдкарах `"marketKind":"map_winner"` с `mapNumber` 1 и 2; Telegram `game=cs2`; `archive-dota`/`archive-lol` не перезапускались.

---

## Этап 2. Трейдер

Репо `../esports-trader`, `main` (локально `a2407635`, VPS `edbc547f`, между ними нет кода трейдера). Проверка: `make test`, `basedpyright`, `ruff`.

### 2A. Швы: игра = идентичность + фид + стратегия (поведение Dota/LoL не меняется)

Паттерн. `GameProfile` оставляет только идентичность, а поведение задают два объекта с двумя реализациями каждый. Никаких `if game ==` в трейдере; числовые константы остаются в таблицах `*_BY_GAME` (уже принятый стиль `src/shared/constants/strategy.py`).

```python
@dataclass(frozen=True)
class GameProfile:
    game: str
    archive_root_env: str
    mode_env: str
    uses_steam: bool
    aliases: Mapping[str, tuple[str, ...]]
    league_whitelist_path: Path | None       # dota None, lol LOL_LEAGUE_WHITELIST_PATH, cs2 CS2_LEAGUE_WHITELIST_PATH
    feed: GridFeedSpec                        # кадры GRID → тики
    strategy: StrategySpec                    # тики → сигнал ядра, стороны котирования, модели
```

- `GridFeedSpec` (Protocol, `src/trader/grid_feed_spec.py`): `services: tuple[str, ...]`; `read_board_sides(board, outcome_0_name, outcome_1_name, aliases) -> BoardSideRead`; `probe_delay(series_id, map_number) -> int | None`; `build_reducer(map_number, outcome_0_name, outcome_1_name, aliases) -> WidgetReducer`. `WidgetReducer` (Protocol): `reduce_frame(frame, now) -> FeedEvent | KillTick | RoundTick | None`, `note_feed_gap()`.
  - 2A-реализация `MobaGridFeed(level_xp, side_0_text, side_1_text, top_builder, services)` — перенос из `GameProfile` и `grid_feed.py:194` (`build_top_player_features` для Dota, `..._over_total` для LoL). `GridFrameReducer.__init__(map_number, o0, o1, spec: MobaGridFeed)`. `read_board_sides` и `probe_grid_delay` как есть, за методом.
- `StrategySpec` (Protocol, `src/trader/strategy_spec.py`): `quote_sides: QuoteSides`; `max_position_levels: int`; `profile_names() -> tuple[str, ...]` (для `session_config`/clips); `load_models() -> Mapping[str, LoadedModel]`; `model_for(feed_source) -> LoadedModel` и `profile_name_for(feed_source)` (замена `strategy_catalog`/`strategy_profile_name`); `build_signals(model, feed_source, ...) -> SignalSource`.
  - 2A-реализация `Follow300Strategy(primary: StrategyCatalog, satellites: Mapping[FeedSource, StrategyCatalog])` — перенос `strategy_catalog(s)`, `_load_strategy_models`, `LIVE_MAX_POSITION_LEVELS[game]`, `FOLLOW300_SIDES`.
- `SignalSource` (Protocol, там же): `on_feed_event(event, market_p_radiant, prior, reason, now_ns) -> SignalOutcome` (сигнал для ядра или причина); `on_tick(tick, now_ns) -> None` (дополнительные тики: `KillTick` у MOBA). 
  - 2A-реализация `Follow300Signals(model: ModelServer, history: SnapshotHistory, board: BoardHistory, yes_is_radiant)` — чистый перенос `_latched_fair`, `_on_kill_tick`, `_board`, `_history` из `match_worker.py` (:195-215, :525-560). Воркер держит `self._signals: SignalSource`.
- `session_core.py`: `LiveCore(..., sides: QuoteSides)`; `apply` (:592) и `preview_block_reason` (:323, :326) → `step_with_sides(sides=self._sides)`; `open_core` передаёт `profile.strategy.quote_sides`. `core_trace`/`replay_core_trace`/`core_state_report`: стороны по `GAME_PROFILES[header.game].strategy.quote_sides` (`core_trace.py:264` уже пишет игру).
- `league_filter.py` (переименование `lol_league_filter.py`): `LeagueFilter(game, archive_root, whitelist_path)`; `discovery.py:192` создаёт фильтр, если `profile.league_whitelist_path` не None; `wallet_host.py:866` (book watch LoL) — `LeagueFilter("lol", root, LOL_LEAGUE_WHITELIST_PATH)`.
- `discovery._grid_source` (:511), `feed_selection` (:92-122), `source_picker` — через `profile.feed`. `grid_live_feed.py`: `GridLiveFeed(series_id, services, match_id, reducer: WidgetReducer)`; `past_cutoff` от `BUY_CUTOFF_SECOND_BY_GAME[game]`; `replay_grid_records` принимает `WidgetReducer`.
- `src/shared/constants/strategy.py`: `BUY_CUTOFF_SECOND_BY_GAME = {"dota": 480, "lol": 480}`; `src/strategy/policy.py:70` читает по игре. `LIVE_MAX_POSITION_LEVELS` переезжает в `Follow300Strategy` (или остаётся таблицей — оставить, меньше диф).
- Дашборд (`game_grid.py:228,247`, `game_state.py:343`, `match_state.py:192`) читает `profile.feed` для сторон и подписей; `live_hub._sidecar_roots` без изменений.
- Тесты 2A: все существующие проходят; `tests/test_trader_game_profile.py` проверяет новые поля; `tests/adapter_contract_fixtures.py:506` и `test_trader_session_core.py` (17 мест `LiveCore(`) получают `sides=FOLLOW300_SIDES`; `test_core_trace.py` реплей по игре; тест констант Dota/LoL из «Гарантии».
- Ворота merge 2A: четыре доказательства из «Гарантии Dota/LoL».

### 2B. CS2 как вторая реализация швов

#### 2B.1 Константы, профиль, конфиг, compose

- `src/shared/constants/strategy.py`: `CS2_MIN_ABS_DELTA = CS2_EXIT_ABS_DELTA = 0.01` (промоутнутый LIVE-манифест 1c/1c; было 0.0075; комментарий со ссылкой на манифест); `LIVE_MAX_POSITION_LEVELS["cs2"] = 6`; `CS2_BUY_CUTOFF_SECOND = 10**9`, `BUY_CUTOFF_SECOND_BY_GAME["cs2"]`. Конец карты закрывает покупки через `GameClock.game_ended` → «game_end».
- `GAME_PROFILES["cs2"] = GameProfile(game="cs2", archive_root_env="CS2_ARCHIVE_ROOT", mode_env="CS2_TRADING_MODE", uses_steam=False, aliases=CS2_TEAM_ALIASES, league_whitelist_path=CS2_LEAGUE_WHITELIST_PATH, feed=Cs2GridFeed(), strategy=RoundStrategy(CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR))`.
- `config/trading.toml`: `[profiles.cs2-map]` — 15 ключей как у `lol-map`, `base_size_usdc = 5.0`; `[clips.cs2] default = 5.0, tiers = [], events = []`.
- `compose.yaml` `live`: env `CS2_ARCHIVE_ROOT: /archive/cs2`, `CS2_TRADING_MODE: ${CS2_TRADING_MODE}`; volumes `/var/lib/polymarket-cs2-archive:/archive/cs2:ro`, `./data/cs2/models:/app/data/cs2/models:ro`. `dashboard`: `CS2_ARCHIVE_ROOT` и mount архива. Локальный `.env`: `CS2_TRADING_MODE=off`.
- `config/cs2_league_whitelist.json`: `"PGL Masters Bucharest"` в `leagues`, alias стадий (`... Group Stage`, `... Playoffs`, варианты с `2026`); точный суффикс титула `(BOx) - ...` сверить утром по Gamma (`is_allowed_league` — только точное имя или alias).
- Тесты: `test_trader_game_profile.py` (набор `{dota, lol, cs2}`), `test_trader_trading_mode.py` (`CS2_TRADING_MODE` во всех env-словарях, кейсы live и missing), `test_trader_compose.py` (`SHARED_ENV_KEYS`, mounts), `test_trader_session_config.py` (`(cs2,)` → `{"cs2-map"}`, clip 5.0), policy cs2: cutoff `CS2_BUY_CUTOFF_SECOND`, `min_abs_delta == 0.01`.

#### 2B.2 Стратегия: `RoundStrategy`, `ROUND_SIDES` в `src/strategy`

- `git mv src/backtest/cs2_round_sides.py src/strategy/round_sides.py`; `RoundSignal` → `src/strategy/types.py` рядом с `RawDeltaSignal`. Импорты: `src/backtest/strategy.py:46`, `src/backtest/cs2_run.py:12`, `tests/test_cs2_reports.py:14`, `tests/test_cs2_round_sides.py:21`.
- `src/cs2/round_rules.py` (новый): `RoundCall`, `apply_round_rules`, `CLOSED_ENTRY_LEADER_SCORE`, `CLOSED_ENTRY_DELTA` переезжают из `src/backtest/cs2_round.py` (трейдер не импортирует `backtest`: тот тянет `archive_index`); `backtest/cs2_round.py` реимпортирует.
- `RoundStrategy(round_dir, outcome_dir)`: `quote_sides = ROUND_SIDES`, `max_position_levels = 6`, `profile_names() = ("cs2-map",)`, `load_models()` → `{"cs2-map": load_cs2_round_models(...)}`, `build_signals(...)` → `RoundSignals`.
- `Cs2RoundModels(delta: RoundModel, outcome: RoundModel, model_reference: ModelReference)`; `load_cs2_round_models(round_dir, outcome_dir)` в `src/trader/cs2_models.py`: `read_meta`/`validate_meta` из `model_server.py` (сделать публичными; признаки `FEATURES`/`OUTCOME_FEATURES`, lag 0), `load_round_model` под `_SilencedStderr`; `ValueError` → `ModelContractError`, `OSError` → `ModelLoadError`, лог `STARTUP_BLOCKED_PREFIX` + notify как у `load_model`. `LoadedModel = ModelServer | Cs2RoundModels`; `core_trace.model_file_sha` берёт каталог из `profile.strategy`.
- Окна покупок в ядре без нового inbound-события: `LiveCore.buys_open: bool = True`; `run_core_cycle` (:969) → `sidecar_usable = core.sidecar_usable and core.buys_open`. Воркер: `close_round_buys()` (флаг False, `_schedule_entry_cancel()` снимает BUY, wake) и `open_round_buys()`. Зеркало бэктеста (`PermissionsUpdate(allow_buy=False)` + отмена BUY, `backtest/strategy.py:959-975`); в журнале `entry_block=no_edge`. `WalletStateStore.block_buy` не переиспользовать (это «on-chain excess»).
- Трейс: `core_trace_codec.py:91` `_ROUND_SIGNAL_KEYS = _SIGNAL_KEYS | {"up_radiant", "up_dire", "call_ns"}`; `decode_signal` (:340) возвращает `RoundSignal` на этом наборе (кодирование автоматическое через `fields()`).
- Тесты: `test_cs2_round_sides.py` новые пути; `test_trader_session_core.py` `buys_open=False` → `allow_buy False`, `block_reason == "fair"`, висящий BUY снимается на следующем цикле; `test_core_trace.py` round-trip `RoundSignal` и реплей cs2-трейса с `ROUND_SIDES`; `test_trader_core_state_report.py`.

#### 2B.3 Фид: `Cs2GridFeed`, парсер, редьюсер

Что показала запись `data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl` (карта 3, Nuke, только `integrity_safe_*`):

- Scoreboard (delay 0, кадр на событие, `occurredAt` — часы): `games[i].mapName` в нижнем регистре («nuke») = ключ `CS2_MAP_CODES`; `teams[].infoText.text` = `T`/`CT`, меняется в первом кадре заморозки раунда 13; id команд постоянны; `teams[].score` = раунды, `series.teams[].score` = карты. Номер раунда = `score_a + score_b + 1` (проверено на 681 кадре против `momentum`). END = первый кадр с ростом суммы счёта, в нём же победитель. Live-старт = первый кадр с `currentSeconds == 115`. Заморозка = `currentSeconds == 20` и `color == "white"` после END (таймер бомбы после элиминации идёт до 0 с `color == "error.0"` и тоже показывает 20). Бомба = `color == "error.0"`. Пауза = `isTicking == false`. Конец карты = `status == "finished"` через 1 с после последнего END. На старте карты борд 2 с мигает между `activeGameIndex` 1 и 2; `read_map_scoreboard(payload, 3)` уже даёт None, пока карт две. Между картами борд молчит 60+ с.
- Table (delay 8, кадр на событие; финальный кадр раунда через 7.6–8.8 с после END на всех 21 раундах): группы `Series`, `Game` (seq = карта), `Round` (seq = раунд), `Freezetime`, `Timeout`; кадры карт 2 и 3 ~2 с перемешаны — фильтр `Game.sequenceNumber == map_number`. `Game` Team Summary: `Score`, `Side`, `LoadoutValue`, `Kills`, `Deaths`, `plantBomb`/`defuseBomb`/`explodeBomb` (NotRequired). `Round` General: `Duration` (`PT1M17.294S`), `Finished`; Team Summary: `Kills`, `Deaths`, `Side`, `WinType` (`opponentEliminated`/`bombDefused`/`bombExploded`/`timeExpired`, только у победителя), `plantBomb`; игроки: `CurrentHealth` (живые = `> 0`). `Freezetime` General `Finished`: первый кадр `True` для раунда N несёт loadout после закупки. Денег нет: cash оцениваем, как офлайн.
- Офлайн-квантование (сверено `build_rounds` на 3 картах каталога): `decision_s = ceil(won_s) + 9`; `surv_*` и `prev_kills_*` — состояние на `end_real − 1` (последняя жертва элиминации ещё жива, последний килл не считается; cash добавляет его как «поздний»). Открывающие половину раунды (1, 13, 25, 28…) решают на `fs_sec + 1` по финальному состоянию прошлого раунда. `m0` NaN на тонкой книге → delta 0. `market_radiant_prior` часто NaN, модель это видела.

Файлы:

- `src/trader/grid_widget_types.py`: `TextCell`, `BoolCell`, `Cs2TeamRow`, `Cs2PlayerRow`, `Cs2GeneralRow`.
- `src/trader/grid_widgets.py`: `Scoreboard` + `map_name: str`, `clock_color: str` (из `_scoreboard_at_index`; MOBA игнорирует).
- `src/trader/cs2_widgets.py` (новый, только парсинг): `Cs2TeamLoadout`, `Cs2RoundTeam(team_id, kills, win_type, planted, alive)`, `Cs2RoundTable(number, duration_s, freeze_finished, teams)`, `Cs2Table(game_number, feed_delay, loadouts, round)`, `read_cs2_table(payload, feed_delay)`, `parse_duration_seconds(text)`, `WIN_TYPE_EXPLODED`, `WIN_TYPE_DEFUSED`.
- `src/trader/cs2_feed.py` (новый): `Cs2GridFeed` (реализация `GridFeedSpec`: `services = REDUCED_SERVICES`, `read_board_sides` = по порядку команд `teams[0]`/`teams[1]` через `orient_outcomes` с `radiant_id = teams[0].team_id`, pending при `len(teams) != 2`; `probe_delay` = `publish_delay` борда через `_probe_first(series_id, SCOREBOARD_SERVICE, ...)`; `build_reducer` → `Cs2FrameReducer`). `Cs2FrameReducer(map_number, o0, o1, aliases)`: `reduce_frame`, `note_feed_gap`, атрибуты `sides`, `header: MapHeader`, `rounds: tuple[RoundFacts, ...]`, `board`. Ориентация один раз на первом live-борде (`GridOrientationError` при промахе имён; `CS2_MAP_CODES[map_name]` падает на неизвестной карте — fail loud), дальше команды по id, не по индексу. Автомат: первый live-борд → `map_live_unix`; скачок суммы счёта → END; `cur == 115` → live-старт; белый `cur == 20` после END → заморозка; по таблице (только своя карта): первый `Freezetime.Finished` → loadout, кадры `Round` → выборки `(duration, kills, alive)`, первый `WinType != ""` → финал, «seen» = последняя выборка с `duration ≤ final − 1`.
- Тики: `FeedEvent` с нулевым MOBA-`GameSnapshot` (`second` от `map_live_unix`, `phase` IN_PROGRESS/FINISHED, `paused` = `not isTicking`, остальное 0, `prior_anchor = PriorAnchor(map_live_unix − 85)`) — журнал, horn, prior (Dota-ветка `_maybe_start_prior` → `read_book_prior` по журналу CS2-архива), watchdog и дашборд без изменений; плюс `RoundLiveTick(round_number, live_unix)` и `RoundDecisionDue(round_number, due_unix)`. `due_unix = unix(END.occurredAt) + 10` (≈ офлайн `ceil + 9`); открывающий половину — `freeze_unix + 2`. Due отдаётся, когда есть и END (или заморозка открывающего), и финальный кадр таблицы прошлого раунда; call после live-старта следующего раунда пропускается.
- Watchdog: scoreboard CS2 событийный, в заморозке и перерыве молчит минутами → `CS2_FEED_STALE_SECONDS = 600` для таймаута входа фида (иначе STALE → `signal=None` → `round_exit_fair` None → SELL уезжает к ask в паузе). Здоровье фида — по сокету.

#### 2B.4 Сигналы: `RoundSignals`, признаки, модели

- `src/cs2/s11_round_frame.py`: `_attach_streaks` → `attach_streaks`, `_fill_missing_pistol` → `fill_missing_pistol`. `src/cs2/s11_round_features.py`: `_match_kill_features` → `build_match_kill_features(part, kills, present, planted)` (`attach_kill_features` остаётся дисковой обёрткой). `src/cs2/s11_cash_estimate.py`: `_estimate_match` → `estimate_match_cash(facts)` (`attach_cash_estimate` — обёртка). `attach_team_form` не трогать: live пишет `FORM_COLUMNS` как NaN. Остальная цепочка уже чистая.
- `src/trader/cs2_rounds.py` (новый, pandas, без сокетов): `MapHeader(map_code, game_number, series_score_a, series_score_b)`; `RoundCallInputs(round_number, decision_unix, score_a, score_b, team_a_ct, m0, prior)`; `build_round_frame(header, rounds, call) -> pd.DataFrame` — строки = завершённые раунды + строка call, колонки как у `build_rounds`, затем `attach_round_features` → `fill_missing_pistol` → `attach_streaks` → `attach_loss_levels` → `attach_survivors` (заглушка `sec = decision_s`) → `build_match_kill_features` (синтетический `MatchKills`: seen-килы `ceil_s = end − 1`, поздние `end + 1`) → `estimate_match_cash` → `FORM_COLUMNS` NaN → `add_derived`. Ни одной новой формулы. `predict_round_call(models, frame) -> RoundCall`: `predict_round_deltas`, `predict_round_rises`, `apply_round_rules` на последней строке.
- `RoundSignals` (реализация `SignalSource`, в `cs2_rounds.py` или `cs2_signals.py`): `on_feed_event` переотдаёт текущий `RoundSignal` с якорем call (`anchor_p = m0` call) и свежими `received_ns`; пауза не обнуляет сигнал (`ROUND_SIDES.quote_through_pause=True`); `on_tick(RoundDecisionDue)` → `call_later(max(0, due − now))` → `m0` = radiant-цена книги, `prior` = latched или NaN (не блокировать `MISSING_PRIOR`: офлайн учился на NaN) → `build_round_frame` → `predict_round_call` → `RoundSignal(predicted_delta, received_ns, source_received_ns, anchor_p, deaths 0/0, up_radiant = up_a, up_dire = up_b, call_ns = due·1e9)` → в ядро и `open_round_buys()`; `on_tick(RoundLiveTick)` → `close_round_buys()`; 1 Гц heartbeat (таймер как `_board_handles`) переотдаёт сигнал, пока борд молчит. Конец карты → `FINISHED` → «game_end».
- Журнал: CS2 пишет те же `signal`/`quote`/`fill` через нулевой `GameSnapshot`; разбор — по `core_trace.jsonl` (`RoundSignal`) и `grid_state.jsonl`.

### 2C. Паритет на записи и CLI

- `src/trader/replay_cs2_rounds.py` (новый): `main(tape_dir, map_number, outcome_0, outcome_1)` — читает `grid.jsonl` (пропуская `marker`), гонит `Cs2FrameReducer` через `replay_grid_records`, на каждом `RoundDecisionDue` строит фрейм (`m0` из `books.jsonl` через `trader.book_prior.replay_token_books`, если формат совпадает, иначе NaN с пометкой; prior через `lookup_strict_prior` на `map_live − 85`), печатает строку `FEATURES`, `model_delta`, `pred_delta`, `up_a`, `up_b` и леджер.
- Тесты: `tests/cs2_grid_widget_fixtures.py` (урезанные реальные payload + билдеры); `test_cs2_widgets.py`; `test_cs2_feed.py` (ориентация, `prior_anchor == stamp − 85`, `RoundLiveTick`, due в обоих порядках END/финал, открывающий раунд `(13, freeze + 2)`, `cur == 20 error.0` не заморозка, скачок счёта на 2 → нет due, «seen», чужая карта, пауза, finished и feed-gone → терминальный тик); `test_cs2_rounds.py` ((1) паритет: синтетические факты раундов 1–14 → `build_round_frame` против офлайн `build_rounds` на 1 Гц фрейме из тех же фактов, `add_derived(...)[FEATURES]` равны построчно; (2) `predict_round_call`: пистолетка → ±1e-4, овертайм → 0, NaN `m0` → 0/0/0, лидер 12 → ±1e-4); `test_cs2_models.py` (загрузка, contract/load errors); `test_cs2_tape_replay.py` (skip без ленты): карта 3, 21 раунд; победители по id 56753 в R1–4, 6, 7, 14, 15, иначе 59048; win type R1/2/6/9/10/13/14/17/18/21 eliminated, R3/4/7/16/19/20 exploded, R8/11/12/15 defused, R5 time; финальные килы (a,b) R1 5/4, R2 5/2, R3 4/2, R4 4/3, R5 3/4, R6 5/4, R7 3/1, R8 4/4, R9 0/5, R10 1/5, R11 1/5, R12 2/5, R13 3/5, R14 5/2, R15 4/4, R16 1/4, R17 4/5, R18 2/5, R19 1/3, R20 1/3, R21 3/5; loadout R1 3900/4000, R2 19200/1900, R3 23600/24250, R12 26200/30850, R13 4000/4250 (team_a CT с R13), R21 26800/27750; END R1 21:14:18Z … R12 21:39:58Z, R13 21:41:49Z, R21 21:58:37Z, due = END + 10, R13 due = 21:40:19 + 2; `RoundLiveTick` R1 21:13:21; конец карты 21:58:37; cash известен с R2.

Риски пути данных: лаг таблицы p90 8.9 с против due END+10 закрыт ожиданием финального кадра, зависание > ~25 с теряет call раунда; если GRID закрывает сокет после 120 с тишины, `GridLiveFeed.ticks()` завершает фид на чистом закрытии — проверить 5-минутным idle-подключением до первой карты (исправление в 2 строки затронуло бы MOBA, делать только по результату); аннулированный раунд рассинхронит номер по сумме счёта; GRID `LoadoutValue` ≠ bo3 equipment (p90 $700, принято в `cs2-cash-estimate.md`).

### 2D. Раскатка CS2 (`ssh sun`, `/root/work/esports-trader`)

Порядок: архив CS2 уже пишет (Этап 1) → код и `.env` вместе (с профилем `cs2` любой процесс без `CS2_TRADING_MODE` падает `TradingDisabled`) → пересоздать `live` → перезапустить `dashboard`.

1. `ls /var/lib/polymarket-cs2-archive/metadata/markets | head`; mtime свежие; `events/` есть.
2. Утром: `curl -s 'https://gamma-api.polymarket.com/events?tag_id=100780&active=true&closed=false' | jq -r '.[] | [.title, .eventMetadata.league, .eventMetadata.gridSeriesId] | @tsv' | grep -i pgl` → alias в whitelist по суффиксу; `gridSeriesId` заполнен (без него торговли не будет).
3. `python3 src/dashboard/summarize.py --restart-check` → `SAFE`.
4. `git pull`; в `.env` добавить `CS2_TRADING_MODE=live`.
5. `docker compose up -d --force-recreate live`; `docker compose restart dashboard`.
6. `docker compose logs --since 5m live`: `trader assigned: mode=live games=dota,lol,cs2`; нет `startup blocked`; `cs2 event ... league=...` допущен (если `record-only` — alias неверный); `trader launch: match_id=grid-30181xx-m1`; `feed_selected: feed_source=grid`; `session started` с CS2; `RoundSignal` в `core_trace.jsonl` раз в раунд; `trader core buy` на ступени $5.
7. После первой сессии `model.sha256` в заголовке трейса = `7eba748b…` из LIVE-манифеста.

Сухой прогон без денег есть бесплатно: событие вне whitelist идёт `record-only` (`LeagueFilter`: пишет `grid_state.jsonl`, исполнение не подключает). Первый блок карт можно пройти с невключённым alias: в логах видны discovery, фид, round call, `RoundSignal` без ордеров; затем включить alias. Проверить, что record-only гоняет фид и сигнал (у LoL так).

Откат: `CS2_TRADING_MODE=off` + `up -d --force-recreate live`; Dota/LoL не трогаются.

---

## Этап 3. Дашборд

Минимум, чтобы CS2 отображался и считался.

- `src/dashboard/home_view.py:401`: `_GAME_LABEL["cs2"] = "CS2"`.
- `src/dashboard/summarize.py:1712`: `choices=("dota", "lol", "cs2")`.
- Подписи сторон и профиль — из `GAME_PROFILES["cs2"].feed` (2A); `live_hub._sidecar_roots` подхватит `CS2_ARCHIVE_ROOT`.
- Слейт «сегодня» заработает от сайдкаров `map_winner` после 1.1.
- PnL/история: CS2 в бакет `other` (`home_lists._place`, `pnl_chart._game_buckets`), общий PnL включает. Колонки «PnL CS2» — позже, если тест оставят.
- `game_grid.py`: MOBA-таблица игроков на CS2 даст заметку, не падение. Раундовый вид — вне объёма.
- Тест: `tests/test_dashboard_home.py` `_game_label("cs2") == "CS2"`. VPS: `docker compose restart dashboard`.

---

## Проверка end-to-end

1. Локально: `make test`; `uv run python -m trader.replay_cs2_rounds data/cs2/live/cs2-damaju-hachae-2026-10-21 3 Damajuana "Hacha Esports"` — 21 раунд, due/лоадауты/килы/победители как в 2C, `pred_delta` конечные, пистолетки ±1e-4.
2. Гарантия Dota/LoL: четыре ворота из раздела выше закрыты до merge 2A; после раскатки 2A (если отдельно) сутки логов Dota/LoL без новых `WARNING`/`ERROR` и с обычными `session finished`.
3. Коллектор на VPS: сайдкары CS2 с `map_winner` 1/2, Telegram `game=cs2`, `compact` в 03:00 UTC прошёл по трём играм, `archive-dota`/`archive-lol` не перезапускались.
4. Трейдер на VPS после 2D: строки из 2D.6; в `grid_state.jsonl` кадры обоих сервисов; в `core_trace.jsonl` `game=cs2`, `SignalUpdate` с `up_radiant`/`up_dire`/`call_ns`, `entry_block` между live-стартом и следующим call; первый BUY на $5 при `|delta| ≥ 1c` и цене 0.45–0.85; SELL на `m0 + up`; `session finished` с `net`.
5. Дашборд: `summarize.py --today --game cs2`; главная показывает серию PGL в слейте и карту CS2 с подписью «CS2».
