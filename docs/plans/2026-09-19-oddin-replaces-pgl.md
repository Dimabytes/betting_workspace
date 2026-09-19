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
- PGL уходит из кандидатов новых сессий. Чтение старых PGL-архивов сохраняется.

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
- `config/trading.toml`: `[profiles.dota-oddin-map] base_size_usdc = 5.0` с тем же комментарием-сателлитом, что у `dota-pgl-map`; комментарии в `[risk]` и `session_config.py` про сателлит обновить.

### 5.4. Бэктест

Существующий `make backtest ARGS="--validation --model-dir data/new_model/research-noxp --name noxp-exec15 ..."` с execution lag 15, cadence 1, одним seed (при cadence 1 сиды идентичны), clip $5 и реальным `min_order_size`. Selection читает общий research split — для no-XP модели, обученной на том же split, это корректно. Manifest уже пишет `model_sha256` и features.

Правильный контроль для решения «Oddin лучше PGL»: **research-noxp @ execute15 против research @ execute30** на тех же картах (PGL сегодня торгуется полной моделью на 30 с). Сравнение no-XP против XP на одинаковом execute15 показывает лишь цену отсутствия XP — эта модель не заменяет Steam/GRID, поэтому небольшая просадка там не блокер. Оценка — paired per-map diff, как в `.learnings/pgl-lag25-model-comparison-20260919.md`.

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

`FeedSource.ODDIN = "oddin"`. В `_SideSource`/`DiscoveredMatch` — `oddin_match_id: str | None` (Bitsler/Oddin `od:match:N`), зеркально `pgl_channel_id`; PGL-поля остаются для чтения старых `match.json`.

Bitsler перечислять **один раз на discovery cycle** (как `_index_pgl_probes`), синхронные HTTP — не в async loop. Привязка: обе команды через существующий `orient_outcomes` с `TEAM_ALIASES` против имён Steam/GRID game; ровно одно совпадение, иначе не привязывать и логировать причину. Oddin присоединяется только к уже существующему каноническому Steam/GRID binding; archive ID остаётся `9006723487` / `grid-…-m…`. Oddin-only рынок без Steam/GRID identity — не в этом плане.

`match.json` schema v8: `oddin_match_id`, `oddin_delay_s`, `feed_source="oddin"` в pin. v3–v7 читаются как раньше. `feed_source=pgl` в старых файлах не переименовывать.

### 6.3. Проба задержки и выбор источника

`probe_oddin_delay(oddin_match_id, map_number) -> int | None` в `source_picker.py`, по образцу `probe_grid_delay`: открыть WS, взять первый снапшот с `VALID_DATA` и `currentMap.mapOrder == map_number`, вернуть `round(received_at − lastUpdatedAt)`. HTTP seed не используется. Таймаут — `GRID_FEED_STALE_SECONDS`, любой сбой → `None` (источник просто выпадает).

`_collect_candidates`: убрать ветку PGL, добавить `FeedCandidate(ODDIN, oddin_delay_s)` когда `oddin_match_id` есть и проба вернула число. `SOURCE_TIE_ORDER = (STEAM, GRID, ODDIN)`. Гейт 61 с и «pin побеждает ranking» без изменений. `_PickedSource`/`_log_feed_selected`: `pgl_delay_s → oddin_delay_s`.

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

### 6.6. Вывод PGL из live

- `_collect_candidates` без PGL → новых PGL-сессий нет.
- Ветка PGL в `_build_feed`, `PglLiveFeed`, `pgl_sse`, `dota-pgl-map` остаются **на один деплой**, чтобы возможный незакрытый PGL pin на VPS доиграл штатно. Удалить их отдельным коммитом после деплоя, когда на VPS нет открытого PGL pin. `pgl_feed` reducer, `pgl_archive`, `pgl_types` остаются для viewer старых архивов.

## 7. Порядок реализации

Каждый шаг оставляет проект рабочим и тестируемым отдельно.

### Шаг 1 — вторая модель

Файлы: `shared/utils/gbm.py`, `shared/constants/paths.py`, `shared/utils/model_registry.py`, `train_model/train_model.py`, `trader/model_server.py`, `trader/game_profile.py`, `trader/host_resources.py`, `trader/wallet_host.py` (ключ моделей), `config/trading.toml`, `session_config.py` (комментарии/сателлит).

Проверки: `make train` публикует четыре каталога, hashes `research/`/`production/` меняются только от переобучения, LoL train не меняется; `production-noxp/model.json` — ровно 11 features в порядке `NO_XP_FEATURE_COLUMNS`; `ModelServer` с production-noxp не требует XP и отклоняет 12-feature каталог под Oddin-профиль и наоборот; первая публикация без старого live dir. Tests: `test_gbm_catalog.py`, `test_gbm_member_pool.py`, `test_model_registry.py`, `test_train_model.py`, `test_lol_train_model.py`, `test_model_server.py`, `test_trader_host_resources.py`, `test_trader_session_config.py`, `test_dota_map_config.py`.

После шага 1 сразу доступен бэктест из 5.4 — он не ждёт фида.

### Шаг 2 — Oddin modules, reducer, live feed, archive

Файлы: разбиение `oddin_feed.py` по 6.1, новые `oddin_types.py`, `oddin_client.py`, `oddin_live_feed.py`, `oddin_archive.py`; `live_feed.py` (`FeedSource.ODDIN`), `paths.py` (`ODDIN_STATE_ARCHIVE_FILENAME`), `archive_types.py`; watcher на общий клиент.

Fixtures из реальных payload после удаления token/персональных полей: stale HTTP map2 → WS map3 (LGD), потерянный игрок/NW/deaths, home=Dire, duplicate/out-of-order, same-second updates, pause/unpause, reconnect, смена `currentMap`, `previousMaps` с нулями, `matchStatus=FINISHED`, invalid data. Crypto — существующие test vectors, алгоритм не трогать.

Проверки: live→archive→replay parity; watcher работает. Tests: три существующих oddin test-файла + новые для reducer/archive.

### Шаг 3 — discovery, проба, picker, metadata, worker

Файлы: `oddin_discovery.py`, `discovery.py` (минимальные seams), `bindings.py`, `source_picker.py`, `feed_selection.py`, `match_meta.py`, `match_worker.py`/`wallet_host.py` (выбор модели по профилю после `select_feed`).

Проверки: GRID 8 побеждает Oddin 15; Oddin 15 побеждает Steam 60/900; Steam 10 побеждает Oddin 15; проба без нужной карты/без `VALID_DATA` → нет кандидата; конфликт имён → нет привязки; pin побеждает ranking; PGL не кандидат; одна condition — один worker; schema v3–v8 round trip; legacy PGL pin строится как раньше; Oddin-сессия использует production-noxp и записывает его identity. Tests: `test_source_picker.py`, `test_trader_discovery.py`, `test_trader_wallet_host.py`, `test_match_meta.py`, `test_trader_match_lifecycle.py`, новые Oddin discovery tests.

### Шаг 4 — viewer

`viewer/live_tape.py`, `viewer/types.py`: `source=oddin` через `oddin_archive`. XP-линия для Oddin — константный 0, это допустимо; Streamlit UI не переделывать. Tests: `test_live_inspect.py`.

### Шаг 5 — бэктест и деплой

1. Бэктест по 5.4: `noxp @ exec15` vs `full @ exec30`, clip $5, paired per-map. Результат — в `docs/experiments/oddin-no-xp/`. Это информация для пользователя, не автоматический гейт.
2. Деплой на VPS **между картами** по `vps-trader` skill, live, $5. Перезапуск не прятать в make-таргеты.
3. После ~20 торгованных Oddin-карт: buy markouts 5/15/30/60/300 из `session.jsonl`, book lead из `oddin_state.jsonl` + `core_trace.jsonl` (clean two-sided book, окно ±30 с, совместное движение bid/ask, не только mid на расширенном спреде; показывать распределение, не выбирать примеры). Сравнить с историей PGL-карт. Если короткие markouts отрицательны и книга систематически опережает — зафиксировать, что замена PGL проблему не решила; размер не увеличивать.

## 8. Общая проверка

- Прочитать актуальный `esports-trader/AGENTS.md`; работать на main, не пушить без просьбы, не менять frozen `poly-maker`.
- Watcher/crypto уже в `0bab6322`, clip5 PGL — в `147871d4`.
- Точечные tests каждого шага; после интеграции — model/discovery/metadata/session/viewer suites и basedpyright. Не запускать suite во время живой карты.
- Ruff/format только изменённых файлов, без unrelated cleanup.
- `uv run python` / Makefile targets; backtest tests требуют backtest group и sibling framework.
- Явно различать: «код готов», «бэктест завершён», «production-noxp опубликован», «трейдер перезапущен».

## 9. Передача в следующий контекст

> Прочитай AGENTS.md и `betting_workspace/docs/plans/2026-09-19-oddin-replaces-pgl.md`. Реализуй шаги 1–4: вторая Dota-модель без XP из того же пайплайна (`research-noxp`/`production-noxp`), `ModelServer` по `model.json.features`, модели по имени strategy-профиля; затем Oddin feed по образцу PGL с одной пробой задержки и профилем `dota-oddin-map` clip $5. Train lag 10. Не трогай Steam/GRID и старые архивы. Не запускай live и не перезапускай VPS.

### Ссылки

- Предыдущие заметки: `.learnings/pgl-source-delay-20260919.md`, `.learnings/pgl-lag25-model-comparison-20260919.md`, `.learnings/dota-training-filter-and-zero-lag-20260919.md`.
- LightGBM: число/порядок признаков контролируется нашим кодом; `predict_disable_shape_check` не является способом получить модель без XP.
- Временные артефакты замеров: `/private/tmp/analyze_oddin_capture.py`, `/private/tmp/oddin-capture-analysis.json`; существенные цифры и строки доказательств сохранены здесь.
