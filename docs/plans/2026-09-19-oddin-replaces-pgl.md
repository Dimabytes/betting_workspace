# Oddin вместо PGL: замеры, дизайн и план реализации

Дата: 2026-09-19, переработан после ревью в тот же день. Проект реализации: `../esports-trader` относительно корня `betting_workspace`. Исследован HEAD `2e5158ae`, ветка `main`.

Продуктовый код, модели и запущенные трейдеры в этой сессии не менялись. Обучение и бэктесты не запускались.

## 1. Решение

Заменить медленный PGL SSE-фид источником Oddin (публичный виджет через Bitsler). Выбор быстрого источника среди Steam, GRID и Oddin — один раз на карту, существующим picker'ом. Название в коде — `oddin`, не `odin`: так уже называются существующие модули.

Согласовано:

- XP остаётся признаком для Steam/GRID. Для Oddin — **вторая модель без XP**, обученная тем же пайплайном, из того же датасета и split, тем же `make train`. Никакого отдельного «семейства», отдельных prepare/split/publish.
- Train lag **10 для обеих моделей**. Отдельный lag15-датасет не делаем: `docs/experiments/lag-grid.md` и lag25-сравнение не показали эффекта от train lag.
- Задержку Oddin **замеряем одной пробой** на карту (не константа): у пользователя были матчи с задержкой больше 15 с.
- Clip Oddin `base_size_usdc = 5.0` через профиль `dota-oddin-map`, по образцу `dota-pgl-map`. Steam/GRID сохраняют `60.0`.
- Без paper-этапа и без отдельного recorder'а: когда готово — деплой между картами, live на $5, анализ по архивам трейдера.
- PGL как live-источник удаляется из кода полностью (6.6); пользователь сам следит, чтобы при рестарте на VPS не было открытого PGL pin. Чтение старых PGL-архивов viewer'ом сохраняется.

Главный вывод замеров: Oddin быстрее PGL примерно на 15–16 с. Но в книге есть движения **до** Oddin (7–11 с в двух эпизодах из трёх). 15 с сужает окно adverse selection, но не закрывает его. Ответ даст только live на $5 и markouts.

## 2. Что измерено

Все пути относительно `esports-trader/`.

### Входные данные

- `docs/live-pgl-bitsler-odin-logs/Aurora_odin`, `Aurora_pgl`: Pipsqueak+4 — Aurora, карта 2, Steam match `9006723487`, Oddin series `od:match:3211324`.
- `docs/live-pgl-bitsler-odin-logs/LGD_odin`, `LGD_pgl`: Yakult Brothers — LGD, карта 3, Steam match `9006715157`.
- `data/trader/9006723487/{match.json,pgl_state.jsonl,core_trace.jsonl,session.jsonl}`, то же для `9006715157`.
- `data/trader/grid-3007265-m2/grid_state.jsonl`, `grid-3007266-m3/grid_state.jsonl`: GRID delays.

Дополнительно прочитаны один HTTP-снимок и один WS-снимок публичного Oddin. WS получен `2026-09-19T17:16:31.004893Z`: `lastUpdatedAt=2026-09-19 17:16:15.866807591 +0000 UTC`, карта 2, `gameTime=2023`, `LIVE`, `VALID_DATA`, `minimal=false`, по пять игроков с каждой стороны.

### Метод

Терминальные timestamps переведены из Europe/Madrid (UTC+2) в UTC. Начальные HTTP-снимки исключены из статистики WS. Сравнивались: первое получение одинаковой игровой секунды (первый PGL seed исключён); первое получение одинакового перехода счёта; первое получение точно одинаковой пары `(radiant_nw, dire_nw)` с разницей игровых секунд ≤3; `received_at_utc − lastUpdatedAt` для каждого WS-снимка. Положительный выигрыш = Oddin пришёл раньше.

| Метрика | Aurora / Pipsqueak, карта 2 | LGD / Yakult, карта 3 |
|---|---:|---:|
| Принятые WS-снимки Oddin | 587 | 445 |
| Возраст `lastUpdatedAt`, медиана | 15.054 с | 15.076 с |
| Возраст `lastUpdatedAt`, p90 | 15.117 с | 15.121 с |
| Интервал между WS-снимками, медиана | 0.522 с | 0.514 с |
| Максимальный интервал | 1.208 с | 1.800 с |
| Выигрыш на одинаковой секунде, медиана | 15.268 с, n=263 | 14.970 с, n=196 |
| Выигрыш на одинаковом переходе счёта, медиана | 15.731 с, n=9 | 15.559 с, n=5 |
| Диапазон выигрыша на переходах счёта | 14.725–16.390 с | 15.187–16.034 с |
| Выигрыш на одинаковом NW, медиана | 16.375 с, n=179 | 15.927 с, n=129 |

Возраст `lastUpdatedAt` в этих двух картах стабилен (±0.07 с) — похоже на фиксированную задержку виджета. Но пользователь видел матчи с большей задержкой, поэтому в коде это измеряемая величина, не константа. `lastUpdatedAt` — возраст опубликованного снимка, не доказанный полный лаг от игрового сервера.

### Относительно книги

Wall time событий `core_trace.jsonl`: `opened_wall_s + (event.now_ns − opened_now_ns) / 1e9`. Сторона книги — из `header.limits.radiant_token_index`.

| Карта / событие | Книга | Oddin | PGL локально | Наблюдение |
|---|---|---|---|---|
| Aurora, R8:D8 → R9:D8 | 16:59:30.122: Radiant bid/ask 0.26/0.27 → 0.31/0.33 | 16:59:37.317 | 16:59:52.343 | Mid вырос на 5.5¢ за 7.2 с до Oddin |
| Aurora, R8:D7 → R8:D8 | 16:57:43.120: Radiant mid 0.365 → 0.345 | 16:57:54.737 | 16:58:10.265 | Падение на 2¢ за 11.6 с до Oddin |
| LGD, R7:D11 → R7:D12 | 16:59:11.127: Radiant mid 0.235 → 0.195 | 16:59:08.860 | 16:59:24.322 | Сдвиг книги через 2.3 с после Oddin |

Строки доказательств: `Aurora_odin` 1590 и 1660, `core_trace.jsonl` 28446; Oddin 735 / trace 25988; LGD Oddin 728 / trace 27903.

Это примеры временного опережения, не причинная привязка. Обе записи — после 13-й минуты при `buy_cutoff_second=480`; качество входов в первые восемь минут не измерено. Не переносить как факты: что весь рынок смотрит один платный фид; что на TI ни у кого не было быстрее.

## 3. Факты о существующем коде, на которые опирается план

- Watcher и протокол есть: `scripts/watch_bitsler_live.py`, `src/trader/oddin_feed.py` (598 строк: Bitsler discovery + HTTP/WS протокол + projection в одном файле), `oddin_crypto.py`, три test-файла. Расшифровку не переписывать.
- Сырые Oddin payload **не содержат level/XP**.
- `FEATURE_COLUMNS` (12) в `shared/utils/gbm.py` — общий для Dota и LoL: `lol/06_train_model.py` импортирует его же, `model_server._validate_meta` сравнивает любую модель с ним. Удалить XP «только у Доты» одной строкой нельзя.
- `model_server.ModelServer.predict_fair` строит строку по глобальному `FEATURE_COLUMNS`; `_validate_meta` уже читает `features` и `source_lag_seconds` из `model.json`.
- `host_resources.open_wallet_host` загружает `{game: load_production_model(profile)}` — одна модель на игру. `GameProfile.feed_profile_names` уже отображает `FeedSource.PGL → "dota-pgl-map"`; `strategy_profile_name(game, feed_source)` выбирает профиль после выбора фида.
- `train_model.main()` без флагов: `train_research` → `train_production`, оба на `FEATURE_COLUMNS`; флаги `--model-dir` и др. — экспериментальный каталог. `publish_model_dir` читает `live_dir/model.json` и упадёт при первой публикации нового каталога.
- Backtest принимает `--model-dir`, читает features из `model.json`, selection читает общий research split. Для второй модели на том же split этого достаточно.
- `source_picker.pick_source` берёт минимальный delay ≤61 с, tie-break `SOURCE_TIE_ORDER`. `feed_selection._collect_candidates` даёт PGL фиксированные `PGL_ASSUMED_DELAY_SECONDS=30`; `probe_grid_delay` читает delay одной пробой WS.
- `pgl_feed.estimated_horn_unix = received − 30 − clock`, замораживается на первом продвижении часов; горн используется только в `match_worker._maybe_start_prior` (`horn − 90 с` → цена рынка до карты).
- Стейлы: `PGL_FEED_STALE_SECONDS=15.0`, `GRID_FEED_STALE_SECONDS=16.0`.
- Терминальные снапшоты в `steam_feed.build_header_snapshot` и `grid_feed._terminal_snapshot` уже пишут `radiant_xp_adv=0`.
- Первый HTTP-снимок LGD был `map=2`, `t=−1:22`, возраст 5386 с при WS на карте 3. HTTP seed — только в архив.
- `oddin_feed._read_player` использует `or 0` — для торговых полей недопустимо.
- `currentMap` имеет `id`, `mapOrder`, `gameTime`; `homeTeam/awayTeam` имеют `faction`. Home ≠ Radiant.
- `previousMaps` содержит завершённые карты и `won`; per-player NW там были нулями при ненулевом team NW.
- `discovery.py` 994 строки; PGL-привязка живёт там (`_unique_pgl_hit`, `_with_pgl`, поля `pgl_channel_id/pgl_match_id/pgl_sides_match_steam` в `_SideSource`/`DiscoveredMatch`).

## 4. Что отброшено из первой редакции и почему

- Family `dota-oddin`, `shared/model_contracts.py`, `data/new_model/oddin/*`, `data/new_processed/oddin/lag15`, пять `oddin-*` таргетов, family-aware split/manifest — контракт модели это `model.json.features`, он уже есть.
- Train lag 15, lag10/lag20 sensitivity, «control10» — лишняя переменная без ожидаемого эффекта.
- `feed_latency.py`, `delay_basis`, медиана из трёх timestamps — одна проба как у GRID.
- Починка GRID scoreboard-vs-table (0 vs 8) — не влияет на выбор против Oddin (0<15 и 8<15). Отдельная задача.
- «Провенанс якоря времени» (6.4 старого плана) — горн нужен только для prior за 90 с до карты; делаем как PGL.
- Stale 3 с — хлопал бы на max-интервале 1.8 с и reconnect 3 с. Берём 15 с как PGL.
- `radiant_xp_adv: int | None` — расползлось бы по GameSnapshot/viewer/backtest. Guard один: no-XP модель не читает колонку, полная модель не загружается под Oddin.
- Recorder/analyzer шага 9, paper-этап — архивы трейдера и есть запись.

## 5. Модель: два бустера из одного пайплайна

### 5.1. Признаки и каталоги

`shared/utils/gbm.py`: рядом с `FEATURE_COLUMNS` добавить `NO_XP_FEATURE_COLUMNS` — тот же список без `radiant_xp_adv` (11). Fit helpers (сейчас `train[FEATURE_COLUMNS]` в двух местах и `features=list(FEATURE_COLUMNS)` в `build_ensemble_model_meta`) и `train_model.lagged_features` получают `features: Sequence[str]` обязательным аргументом. LoL передаёт `FEATURE_COLUMNS` и не меняется.

`shared/constants/paths.py`:

```text
data/new_model/research-noxp/      RESEARCH_NOXP_MODEL_DIR
data/new_model/production-noxp/    PRODUCTION_NOXP_MODEL_DIR
data/new_model/archive/research-noxp/, archive/production-noxp/
```

Существующие `research/`, `production/`, prepared datasets, split — без изменений. Parquet содержит XP как колонку; no-XP fit просто не выбирает её.

### 5.2. Обучение

`train_model.main()` без флагов: как сейчас research(12)→production(12), затем research-noxp(11)→production-noxp(11). Тот же `TRAINING_DATASET_PATH`/`VALIDATION_DATASET_PATH`/`RESEARCH_MODEL_SPLIT_PATH`/`PRODUCTION_*`, `TRAIN_LAG_SECONDS=10`. Число деревьев production-noxp — из early stopping research-noxp, как устроено для основной модели. `model.json` no-XP каталога записывает 11 features; никакого нового поля не нужно.

Экспериментальный путь (`--model-dir` + три флага) получает `--no-xp` для контролируемых прогонов; блок-лист `blocked` в `parse_args` дополняется двумя новыми live-каталогами.

`publish_model_dir`: если `live_dir` не существует — просто `staging_dir.rename(live_dir)`. Это единственная правка под первую публикацию.

### 5.3. Live

- `GameProfile` получает `feed_model_dirs: Mapping[FeedSource, Path]` рядом с `feed_profile_names`: для Dota `{ODDIN: PRODUCTION_NOXP_MODEL_DIR}`. `production_model_dir` остаётся моделью по умолчанию.
- `open_wallet_host` загружает модели по **имени strategy-профиля**: `dota-map → production`, `dota-oddin-map → production-noxp`, `lol-map → LoL production`. `WalletHost._models` ключуется этим же именем; воркер берёт модель по `strategy_profile_name(game, feed_source)` после `select_feed`. Отсутствие `production-noxp` при старте — ошибка загрузки только для Dota (как сегодня отсутствие `production`), не тихий fallback.
- `ModelServer` хранит `features` из `_ValidatedMeta` и строит строку по ним. `_validate_meta(meta, expected_features)` получает ожидаемый список от вызывающего (`FEATURE_COLUMNS` для `production`, `NO_XP_FEATURE_COLUMNS` для `production-noxp`); чужой список — `ModelContractError`, как сейчас. `_build_feature_values` требует finite XP только если `radiant_xp_adv` в `features`; проверка `set(values) == set(features)`.
- `GameSnapshot.radiant_xp_adv` остаётся `int`; Oddin пишет `0`, как уже делают терминальные снапшоты Steam/GRID. Эту колонку no-XP модель не читает.
- `match.json` уже хранит `ModelReference(name, trained_at)`; два каталога одного `make train` имеют разные `name`, проверка identity при resume работает без изменений.
- `config/trading.toml`: `[profiles.dota-oddin-map] base_size_usdc = 5.0` вместо `[profiles.dota-pgl-map]`, тот же комментарий-сателлит; комментарии в `[risk]` и `session_config.py` про сателлит переписать на Oddin.

### 5.4. Бэктест

Контроль уже есть: `data/backtests/dota_maker/validation_join_delta02_cut480_nw350_p35_pgl25-t10/seed0` — полная research-модель, execute 25, cadence 1, стандартный размер, 556 карт (см. `.learnings/pgl-lag25-model-comparison-20260919.md`). Нужен **один** новый прогон с теми же настройками, кроме модели и лага: `make backtest ARGS="--validation --model-dir data/new_model/research-noxp --name ...-oddin15-noxp"` с execution lag 15. Размер не менять на $5: бэктест сравнивает модели, а не предсказывает live PnL. Один seed (при cadence 1 сиды идентичны). Selection читает общий research split — для no-XP модели на том же split это корректно; manifest уже пишет `model_sha256` и features.

Оценка — paired per-map diff между двумя прогонами на общих картах, как в lag25-разборе. Это ответ на вопрос «Oddin на 15 с без XP против того, чем PGL торгуется сейчас». Сравнение no-XP против XP на одинаковом лаге — не нужно: эта модель не заменяет Steam/GRID.

## 6. Источник

### 6.1. Модули

`oddin_feed.py` разбить, остальные — новые. Границы ответственности, не по файлу на каждую сущность:

- `oddin_types.py`: `LiveMatch`, `WidgetConfig`, `PlayerTick`/`TeamTick`/`OddinTick`, `SocketFrame`, archive record.
- `oddin_client.py`: Bitsler `getMatch`/widget config, GraphQL HTTP snapshot, WS subscribe, reconnect; использует существующий `oddin_crypto`.
- `oddin_feed.py`: строгая projection payload → `OddinTick` и reducer `OddinTick → FeedEvent` (`OddinSnapshotReducer`, по образцу `PglSnapshotReducer`).
- `oddin_discovery.py`: сопоставление Bitsler-матча с уже известным Steam/GRID binding (аналог `_unique_pgl_hit`/`_with_pgl`), чтобы не растить `discovery.py`.
- `oddin_live_feed.py`: `OddinLiveFeed`, по образцу `PglLiveFeed`: socket lifetime, архив `oddin_state.jsonl` до reducer, yield `FeedEvent`.
- `oddin_archive.py`: replay архива тем же reducer для viewer.

`scripts/watch_bitsler_live.py` переводится на `oddin_client`/`oddin_feed`, чтобы диагностика и торговля читали одинаково.

### 6.2. Discovery и binding

`FeedSource.ODDIN = "oddin"`. В `_SideSource`/`DiscoveredMatch` — `oddin_match_id: str | None` (Bitsler/Oddin `od:match:N`) на месте сегодняшних `pgl_channel_id/pgl_match_id/pgl_sides_match_steam`, которые удаляются по 6.6.

Bitsler перечислять **один раз на discovery cycle** (как `_index_pgl_probes`), синхронные HTTP — не в async loop. Привязка: обе команды через существующий `orient_outcomes` с `TEAM_ALIASES` против имён Steam/GRID game; ровно одно совпадение, иначе не привязывать и логировать причину. Oddin присоединяется только к уже существующему каноническому Steam/GRID binding; archive ID остаётся `9006723487` / `grid-…-m…`. Oddin-only рынок без Steam/GRID identity — не в этом плане.

`match.json` schema v8: `oddin_match_id`, `oddin_delay_s`, `feed_source="oddin"` в pin. v3–v7 читаются как раньше. `feed_source=pgl` в старых файлах не переименовывать.

### 6.3. Проба задержки и выбор источника

`probe_oddin_delay(oddin_match_id, map_number) -> int | None` в `source_picker.py`, по образцу `probe_grid_delay`: открыть WS, взять первый снапшот с `VALID_DATA` и `currentMap.mapOrder == map_number`, вернуть `round(received_at − lastUpdatedAt)`. HTTP seed не используется. Таймаут — `GRID_FEED_STALE_SECONDS`, любой сбой → `None` (источник просто выпадает).

`_collect_candidates`: ветка PGL становится веткой Oddin — `FeedCandidate(ODDIN, oddin_delay_s)` когда `oddin_match_id` есть и проба вернула число. `SOURCE_TIE_ORDER = (STEAM, GRID, ODDIN)`. Гейт 61 с и «pin побеждает ranking» без изменений. `_PickedSource`/`_log_feed_selected`: `pgl_delay_s → oddin_delay_s`. Пробы GRID и Oddin для одной карты запускать конкурентно (`asyncio.gather`), отказ одной не блокирует другую.

Ожидаемый выбор на проверенных условиях: GRID 0/8 < Oddin ~15; Steam 10 < Oddin ~15; Oddin ~15 < Steam 60/900; Oddin 30 на «медленном» матче проигрывает Steam 10 и GRID, но выигрывает Steam 60.

### 6.4. Reducer

До обычного тика: `VALID_DATA`; `currentMap.mapOrder == expected`; две разные стороны `RADIANT`/`DIRE` по `faction`; ровно пять уникальных игроков на сторону; у каждого есть NW и deaths числом; валидный `lastUpdatedAt`. `None`/bool/отсутствие — reject с причиной в лог, не `0`. Неполный/minimal payload — не тик.

- `second = gameTime`; `paused = mapPaused`.
- team NW — сумма player NW (как PGL); `deaths_radiant/dire` — сумма deaths своей стороны; top features — существующий `build_top_player_features`; `radiant_xp_adv = 0`.
- `server_timestamp = lastUpdatedAt` в unix-секундах. Парсер принимает формат Go `2006-01-02 15:04:05.999999999 -0700 UTC`, не только ISO watcher'а.
- Горн: `horn = lastUpdatedAt_unix − gameTime`, замораживается на первом продвижении часов (как `estimated_horn_unix`). Позднее подключение после паузы сдвигает оценку на длину паузы — то же поведение, что у PGL сегодня; принимаем.
- Reject timestamps старее последнего принятого; точный дубликат не обновляет freshness. Несколько тиков в одну игровую секунду допустимы.
- Map finish: ожидаемый `currentMap.id` появился в `previousMaps` или `mapOrder` вырос, либо `matchStatus=FINISHED` → один терминальный `FeedEvent` с `phase=FINISHED` и нулевыми признаками (как `build_header_snapshot`), winner из `previousMaps.won` при непротиворечивых сторонах, иначе None. `matchStatus=FINISHED` для карт 1/2 BO3 не наступает — смена `currentMap` и есть конец карты. Disconnect / `complete` WS — не конец карты.

Stale: `ODDIN_FEED_STALE_SECONDS = 15.0`. Keepalive, seed, дубликат и невалидный payload не продлевают BUY. Reconnect: `RECONNECT_SECONDS=3`, `MAX_CONSECUTIVE_FAILURES=5`, как у PGL; повторная ошибка расшифровки — тоже failure, не retry storm.

### 6.5. Архив

`oddin_state.jsonl`: `{received_at_utc, event: snapshot|ws|reconnect, payload}` с **расшифрованным** payload (ротация ключа не должна делать архив нечитаемым), без token/ключей. HTTP seed архивируется с `event=snapshot`, но не редьюсится. Replay через `oddin_archive.py` тем же reducer даёт те же тики, orientation и finish.

### 6.6. Удаление PGL из live

PGL как торговый источник удаляется полностью в шаге 3. Пользователь сам гарантирует, что в момент рестарта на VPS нет открытого PGL pin; никакого переходного периода.

Удалить:

- `pgl_live_feed.py`, `pgl_display.py`, `scripts/watch_pgl_live.py`, `tests/test_watch_pgl_live.py`;
- сетевую часть `pgl_sse.py`: `pgl_client`, `iter_sse`, `sse_url`, `PglProbe`, `probe_from_game_state`, `probe_pgl_channels`, `MAX_CONSECUTIVE_FAILURES`/`RECONNECT_SECONDS`;
- в `discovery.py`: параметр `probe_pgl_channels`, `_CycleScan.pgl_by_match`, `_PglHit`, `_index_pgl_probes`, `_unique_pgl_hit`, `_with_pgl`, все ветки, которые их вызывают; в `wallet_host.py` — передачу `probe_pgl_channels`; в `tests/trader_discovery_fixtures.py` — параметр `probe_pgl_channels`, PGL-тесты в `test_trader_discovery.py`;
- в `feed_selection.py`: импорт `PglLiveFeed`, ветку PGL в `_build_feed`, `pgl_delay_s` в `_PickedSource`/логе, PGL в `_collect_candidates`; `PGL_ASSUMED_DELAY_SECONDS`, `PGL_FEED_STALE_SECONDS`;
- `SOURCE_TIE_ORDER` без PGL;
- `[profiles.dota-pgl-map]` в `config/trading.toml`, `FeedSource.PGL → "dota-pgl-map"` в `GameProfile`, тесты на них;
- поля `pgl_channel_id`, `pgl_match_id`, `pgl_sides_match_steam` в `_SideSource`/`DiscoveredMatch`, если после удаления привязки у них не остаётся читателей.

Оставить для viewer старых архивов (`data/trader/9006723487` и подобные): `FeedSource.PGL` в enum, `pgl_feed.py` (reducer, `estimated_horn_unix` как образец), парсинг payload в `pgl_sse.py` (`SseEvent`, `apply_event`, `as_map`, `PglCorruptUpdate`), `pgl_archive.py`, `pgl_types.py`, чтение `feed_source=pgl` и PGL-полей в `match_meta` для schema v3–v7. `feed_source=pgl` в существующих файлах не переименовывать.

Открытый PGL pin при рестарте — `CorruptFeedPin`/явный отказ новых входов с существующим cleanup, не fallback и не восстановление.

## 7. Порядок реализации

Каждый шаг оставляет проект рабочим и тестируемым отдельно.

### Шаг 1 — вторая модель

Файлы: `shared/utils/gbm.py`, `shared/constants/paths.py`, `shared/utils/model_registry.py`, `train_model/train_model.py`, `trader/model_server.py`, `trader/game_profile.py`, `trader/host_resources.py`, `trader/wallet_host.py` (ключ моделей), `config/trading.toml`, `session_config.py` (комментарии/сателлит).

Проверки: `make train` публикует четыре каталога, hashes `research/`/`production/` меняются только от переобучения, LoL train не меняется; `production-noxp/model.json` — ровно 11 features в порядке `NO_XP_FEATURE_COLUMNS`; `ModelServer` с production-noxp не требует XP и отклоняет 12-feature каталог под Oddin-профиль и наоборот; первая публикация без старого live dir. Tests: `test_gbm_catalog.py`, `test_gbm_member_pool.py`, `test_model_registry.py`, `test_train_model.py`, `test_lol_train_model.py`, `test_model_server.py`, `test_trader_host_resources.py`, `test_trader_session_config.py`, `test_dota_map_config.py`.

Завершение шага 1 — бэктест из 5.4: один прогон `research-noxp @ exec15`, paired per-map против существующего `pgl25-t10/seed0`, результат в `docs/experiments/oddin-no-xp/`. Он не ждёт фида; если no-XP модель на 15 с не лучше того, чем PGL торгуется сейчас, шаги 2–3 можно не начинать.

### Шаг 2 — Oddin modules, reducer, live feed, archive

Файлы: разбиение `oddin_feed.py` по 6.1, новые `oddin_types.py`, `oddin_client.py`, `oddin_live_feed.py`, `oddin_archive.py`; `live_feed.py` (`FeedSource.ODDIN`), `paths.py` (`ODDIN_STATE_ARCHIVE_FILENAME`), `archive_types.py`; watcher на общий клиент.

Fixtures из реальных payload после удаления token/персональных полей: stale HTTP map2 → WS map3 (LGD), потерянный игрок/NW/deaths, home=Dire, duplicate/out-of-order, same-second updates, pause/unpause, reconnect, смена `currentMap`, `previousMaps` с нулями, `matchStatus=FINISHED`, invalid data. Crypto — существующие test vectors, алгоритм не трогать.

Проверки: live→archive→replay parity; watcher работает. Tests: три существующих oddin test-файла + новые для reducer/archive.

### Шаг 3 — discovery, проба, picker, metadata, worker, удаление PGL

Файлы: `oddin_discovery.py`, `discovery.py` (Oddin вместо PGL-привязки), `bindings.py`, `source_picker.py`, `feed_selection.py`, `match_meta.py`, `match_worker.py`/`wallet_host.py` (выбор модели по профилю после `select_feed`), `game_profile.py`, `config/trading.toml`; удаления по 6.6. Удаление PGL и добавление Oddin делать в этом же шаге: Oddin занимает те же места в `discovery`/`feed_selection`, что PGL, и один диф проще двух.

Проверки: GRID 8 побеждает Oddin 15; Oddin 15 побеждает Steam 60/900; Steam 10 побеждает Oddin 15; проба без нужной карты/без `VALID_DATA` → нет кандидата; конфликт имён → нет привязки; pin побеждает ranking; одна condition — один worker; schema v3–v8 round trip; старый `match.json` с `feed_source=pgl` читается viewer'ом, а трейдером отклоняется как `CorruptFeedPin` без входов; Oddin-сессия использует production-noxp и записывает его identity; в `src/trader` не остаётся импортов удалённых PGL-символов (basedpyright). Tests: `test_source_picker.py`, `test_trader_discovery.py`, `test_trader_wallet_host.py`, `test_match_meta.py`, `test_trader_match_lifecycle.py`, новые Oddin discovery tests.

### Шаг 4 — viewer

`viewer/live_tape.py`, `viewer/types.py`: `source=oddin` через `oddin_archive`. XP-линия для Oddin — константный 0, это допустимо; Streamlit UI не переделывать. Tests: `test_live_inspect.py`.

### Шаг 5 — деплой

1. Деплой на VPS **между картами** по `vps-trader` skill, live, $5. Перезапуск не прятать в make-таргеты.
2. После ~20 торгованных Oddin-карт: buy markouts 5/15/30/60/300 из `session.jsonl`, book lead из `oddin_state.jsonl` + `core_trace.jsonl` (clean two-sided book, окно ±30 с, совместное движение bid/ask, не только mid на расширенном спреде; показывать распределение, не выбирать примеры). Сравнить с историей PGL-карт. Если короткие markouts отрицательны и книга систематически опережает — зафиксировать, что замена PGL проблему не решила; размер не увеличивать.

## 8. Общая проверка

- Прочитать актуальный `esports-trader/AGENTS.md`; работать на main, не пушить без просьбы, не менять frozen `poly-maker`.
- Watcher/crypto уже в `0bab6322`, clip5 PGL — в `147871d4`.
- Точечные tests каждого шага; после интеграции — model/discovery/metadata/session/viewer suites и basedpyright. Не запускать suite во время живой карты.
- Ruff/format только изменённых файлов, без unrelated cleanup.
- `uv run python` / Makefile targets; backtest tests требуют backtest group и sibling framework.
- Явно различать: «код готов», «бэктест завершён», «production-noxp опубликован», «трейдер перезапущен».

## 9. Передача в следующий контекст

> Прочитай AGENTS.md и `betting_workspace/docs/plans/2026-09-19-oddin-replaces-pgl.md`. Реализуй шаги 1–4: вторая Dota-модель без XP из того же пайплайна (`research-noxp`/`production-noxp`), `ModelServer` по `model.json.features`, модели по имени strategy-профиля, сразу бэктест `research-noxp @ exec15` против `pgl25-t10/seed0`; затем Oddin feed по образцу PGL с одной пробой задержки и профилем `dota-oddin-map` clip $5. Train lag 10. Не трогай Steam/GRID и старые архивы. Не запускай live и не перезапускай VPS.

### Ссылки

- Предыдущие заметки: `.learnings/pgl-source-delay-20260919.md`, `.learnings/pgl-lag25-model-comparison-20260919.md`, `.learnings/dota-training-filter-and-zero-lag-20260919.md`.
- LightGBM: число/порядок признаков контролируется нашим кодом; `predict_disable_shape_check` не является способом получить модель без XP.
- Временные артефакты замеров: `/private/tmp/analyze_oddin_capture.py`, `/private/tmp/oddin-capture-analysis.json`; существенные цифры и строки доказательств сохранены здесь.

## Прогресс

**Шаги 3 и 4 готовы.** Discovery/picker/metadata/worker: Oddin занял места PGL. Live PGL удалён. Viewer читает `source=oddin` из `oddin_state.jsonl`. Live/VPS не трогали. Шаг 5 (деплой) не начинался.

Что в коде (`esports-trader`, `main`): `oddin_discovery.py` (Bitsler list один раз за цикл, unique name bind через `orient_outcomes`). Oddin штампуется только на уже существующий Steam/GRID identity; archive id остаётся steam/`grid-…-m…`. `probe_oddin_delay` как GRID (первый WS `VALID_DATA` + нужный `mapOrder`, `round(received − lastUpdatedAt)`, таймаут `GRID_FEED_STALE_SECONDS`, без HTTP seed). GRID+Oddin пробы через `asyncio.gather`. `SOURCE_TIE_ORDER = (STEAM, GRID, ODDIN)`. `match.json` schema v8 (`oddin_match_id`, `oddin_delay_s`); v3–v7 читаются, включая leftover PGL. Pin `feed_source=pgl` → `CorruptFeedPin`. `dota-pgl-map` убран; сателлит только `dota-oddin-map` clip $5 → production-noxp. Viewer: `feed_source=="oddin"` через тот же reducer, XP-линия константный 0. Старые PGL-архивы viewer'ом читаются.

Удалено из live: `pgl_live_feed.py`, `pgl_display.py`, `scripts/watch_pgl_live.py`, `tests/test_watch_pgl_live.py`; сетевая часть `pgl_sse.py`. Для viewer архивов оставлены `FeedSource.PGL`, `pgl_feed.py`, payload-парс в `pgl_sse.py`, `pgl_archive.py`.

Проверки: 462 теста (source_picker / discovery / wallet_host / match_meta / match_lifecycle / live_inspect / game_profile / dota_map_config / session_config / pgl_feed / oddin_reducer / lol_discovery / cadence / host_resources / oddin_feed / watch_bitsler). GRID 8 > Oddin 15 > Steam 60/900; Steam 10 > Oddin 15; pin побеждает ranking; leftover pgl pin без входов; schema v8 round trip. ruff + basedpyright по изменённым файлам чистые.

**Шаг 2 готов.** Oddin split на client / types / reducer / live feed / archive. Watcher на общий клиент. Live/VPS не трогали. Шаг 3 не начинался.

Что в коде (`esports-trader`, `main`): `oddin_types.py`, `oddin_client.py` (Bitsler + GraphQL HTTP/WS), `oddin_feed.py` (строгая projection + `OddinSnapshotReducer`), `oddin_live_feed.py` (`FeedSource.ODDIN`, stale 15 с, архив `oddin_state.jsonl` до reducer, HTTP seed не редьюсится), `oddin_archive.py` (replay тем же reducer). `scripts/watch_bitsler_live.py` читает тот же клиент. Crypto не трогали. Discovery / picker / удаление PGL — шаг 3.

Проверки: 57 тестов (`test_oddin_feed`, `test_oddin_crypto`, `test_watch_bitsler_live`, `test_oddin_reducer`); live→archive→replay parity; stale HTTP map2 не тик для map3; missing NW/deaths не zero-fill; home=Dire; duplicate/out-of-order; same-second; pause; reconnect сохраняет горн; `currentMap`/`previousMaps`/`FINISHED` → один терминальный тик с нулевыми признаками. ruff + basedpyright по изменённым файлам чистые.

**Шаг 1 готов.** Код, тесты (328), `make train` (четыре каталога), бэктест — сделаны. Live/VPS не трогали.

Что в коде (`esports-trader`, `main`): `NO_XP_FEATURE_COLUMNS` (11), каталоги `research-noxp` / `production-noxp`, `make train` учит XP затем no-XP (lag 10), `ModelServer` строит строку по `model.json.features`, модели ключуются именем strategy-профиля. `dota-oddin-map` clip $5 → `production-noxp`. `dota-pgl-map` пока оставлен: иначе live PGL сломался бы до шага 3. `FeedSource.ODDIN` в enum уже есть.

Каталоги после `make train`: research/production `20260919T182811Z` / `20260919T182816Z` (12, lag 10); research-noxp/production-noxp `20260919T182833Z` / `20260919T182837Z` (11, lag 10). Первая публикация no-XP — rename, архива не было.

Бэктест шага 1 (5.4): `research-noxp @ exec15`, `--name oddin15-noxp`, cadence 1, seed 0, 10 шардов, тот же `pgl-lag-25` validation parquet. Контроль — существующий `pgl25-t10/seed0` (XP, exec 25). Разбор: `esports-trader/docs/experiments/oddin-no-xp.md`.

Коротко по цифрам: 556 общих карт, +$150 pre-rebate vs PGL@25, cluster 95% CI per-map **включает 0**, 183 лучше / 190 хуже, mid-third −$212, buy 300s 1.18¢ vs 1.61¢. Один terminated (`nautilus_zero_fill`) — total PnL кандидата не полностью доверенный. Гейт плана («если no-XP@15 не лучше текущего PGL — 2–3 можно не начинать»): **не доказано лучше**. Можно идти в шаг 2 только если сознательно принимаете «точка в плюс, доказательств нет».
