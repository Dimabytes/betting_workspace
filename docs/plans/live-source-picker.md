## <!-- b459dc8b-88a9-4889-b3be-77aeed1f75eb -->

todos:

- id: "step-1-lag-poll-train"
  content: "SOURCE_LAG=10, poll out of model.json into backtest manifest, split Steam/backtest poll constants, prepare+train+validation backtest"
  status: pending
- id: "step-2-steam-delay-field"
  content: "Keep stream_delay_s from GetLiveLeagueGames on DiscoveredMatch, record the observed lag too; fix live-paper.md lag claim"
  status: pending
- id: "step-3-feed-protocol"
  content: "LiveFeed Protocol around GameSnapshot; horn on the event; source-neutral state flags; per-source archive; MatchWorker + feed timeout argument; Steam wrapper"
  status: pending
- id: "step-4-grid-feed"
  content: "Shared widget parse used by grid_feed.py and watch_grid_live.py; second = clock + stamp age - frame delay; 12s timeout on changed series_table frames, which also catches pauses"
  status: pending
- id: "step-5-source-picker"
  content: "Pick Steam vs GRID vs skip by the smallest delay under MAX_FEED_DELAY_SECONDS; GRID-only candidates when no server id; record feed_source"
  status: pending

---

## Discovery: один цикл, не два

Сейчас нет отдельного «GRID discovery». Раз в 60 с один проход:

1. Сидкары коллектора (рынки Polymarket). В сидкаре уже лежит `gridSeriesId`, если Gamma его прислала. Может быть `null`.
2. Steam `GetLiveLeagueGames` + имена команд → `match_id`, номер карты (`wins+1`), `server_steam_id`, и поле `stream_delay_s` (сейчас парсер его выкидывает).

GRID не ищет матчи. Это сокет по уже известному `gridSeriesId`, когда picker выбрал GRID как ленту.

Если в этом цикле `gridSeriesId` ещё null, GRID взять нельзя. Следующий цикл через 60 с — Gamma могла дописать id.

Номер карты: Steam `current_map` должен совпасть с `mapNumber` сидкаря (Game 3 рынок есть заранее, линк только когда играют третью). На GRID живая карта = `activeGameIndex+1` / `series_table.sequenceNumber`. Не совпало с нашим map N — кадр выкинуть.

**`server_steam_id` не приходит из `GetLiveLeagueGames`.** Он идёт из
`GetTopLiveGame` (top-10) или OpenDota. `discovery.py:341-344` сейчас выбрасывает
матч целиком: `no server_steam_id ... condition skipped`. Значит Steam доступен
не «когда есть линк», а когда линк **и** server id. Вне топ-10 это редко.
Этот `skip` надо переделать в «кандидат только с GRID», иначе picker до матча не
доживёт.

## Правило выбора ленты

Задержке Steam **верим** `stream_delay_s`. Сами не меряем. Задержка GRID — поле `delay` у `series_table` (8 с).

Один раз на аттач, в середине не переключаем.

1. Собрать доступные источники: Steam, если есть линк с GetLiveLeagueGames **и** `server_steam_id`; GRID, если в сидкаре есть `gridSeriesId`.
2. Ни одного → skip.
3. Ровно один → берём его, если его задержка **<= `MAX_FEED_DELAY_SECONDS`**. Иначе skip всего матча.
4. Оба → берём тот, у кого задержка меньше. Если у победителя всё равно **> `MAX_FEED_DELAY_SECONDS`** → skip матча.

Примеры: Steam 10 и GRID 8 → GRID. Steam 60, GRID нет → Steam. Steam 900 и GRID 8 → GRID. Steam 900, GRID нет → skip.

`MAX_FEED_DELAY_SECONDS = 61` в `src/shared/constants/strategy.py`.

Тик под лаг модели не задерживаем.

## Часы GRID

Часы в сокете есть **в одном месте**: `gameClock` в кадре `series_scoreboard_v2`
(`currentSeconds`, `occurredAt`, `isTicking`, `publishDelay`). Второго источника
времени нет.

Кадр `series_table` времени не несёт вообще — ни часов, ни штампа, только
`sequenceNumber` и строки игроков. Поэтому нетворд сам себя не датирует.

Фичи — нетворд, XP (`increaseLevel+1`), смерти, top-1 — только из таблицы. Киллы
с табло (delay 0) в фичи не мешать. Таблицы на этой карте нет — тика модели нет.
`delay` берём из кадра (`feed_delay` уже парсится в `read_net_worth`), а не
константой 8.

### Чем датируем нетворд: замерено

Замер 24.08 на серии 2995964, 279 живых тиков с таблицей, 15.5 минут одной
карты. Колонка `clock_lag` из `watch_grid_live.py`:

| `clock_lag` | значение              |
| ----------- | --------------------- |
| min         | 1.2 с                 |
| p50         | 3.4 с                 |
| p90         | 23.3 с                |
| max         | 60.2 с                |
| старше 10 с | 42 из 279 тиков (15%) |

Самый долгий провал: с 14:10:10 (lag 1.3) до 14:11:09 (lag 60.2). Минуту сырые
часы стояли на 2212, статус держался `live`, таблица обновлялась.

Точность двух правил против реального времени, те же 280 тиков:

| правило                                 | отклонение                                      |
| --------------------------------------- | ----------------------------------------------- |
| A: `second = currentSeconds` как пришло | min −58.2 с, p50 −1.3 с, sd 12.1                |
| B: `currentSeconds` + возраст штампа    | min −1.6 с, p50 −0.1 с, max +0.8 с, **sd 0.40** |

На 155 обновлениях сырых часов правило B ни разу не ошиблось хуже чем на 2.1 с —
даже сразу после минуты молчания. Часы тикают секунда в секунду, их публикуют
рывками: лаг растёт, потом приезжает пачка кадров и лаг падает.

**Решение: `second = currentSeconds + возраст штампа − delay`.** Только пока
`isTicking`. Источник часов один — GRID; наши часы работают секундомером от
штампа, а не вторым источником времени.

Брать `currentSeconds` как пришло нельзя: ошибка до 58 секунд, и на каждом тике
своя.

Осторожно с колонкой `t=` в логе watcher: это **уже досчитанные** часы
(`live_clock_seconds`), а не сырой `currentSeconds`. Сырое значение — это
`t − clock_lag`. На этом легко посчитать возраст дважды.

### Гейт на возраст часов не нужен

Отдельный порог на возраст штампа часов стоил бы 15% тиков (при 12 с остаётся
85%), и покупал бы только страховку от паузы внутри провала. Он не нужен по двум
причинам.

Первая: досчёт точен до 2 секунд даже после минуты молчания, пока игра идёт.
Мерить нечего.

Вторая: паузу ловит гейт на таблице. На паузе игра стоит — золото не капает,
уровни не растут, киллов нет. Значит таблица не меняется, её кадры не приходят, и
12-секундный порог на таблицу срабатывает сам. Пауза закрыта тем же порогом, что
и несвежие фичи.

Из этого одно требование к реализации: таймер сбрасывается на кадре таблицы **с
изменившимся содержимым**, а не на любом приходе. Иначе GRID повторит тот же
кадр на паузе, и гейт промолчит. Watcher уже выбрасывает кадры без нового
содержимого — вопрос только в том, куда воткнуть сброс таймера.

## Таймаут фида

Steam: 3 с без HTTP-тела. GRID: 12 с на возраст входов модели. Дальше как сейчас: модель выкл, entry BUY снять, SELL оставить; пришёл пакет — снова котировать. Порог в конструктор таймера, не одна константа на оба фида.

Источник у матча один: либо Steam, либо GRID. Вместе они не работают.

У GRID одно соединение, но два типа кадров. Это не два фида, это один сокет:

| кадр                   | что несёт               | задержка | как часто                                      |
| ---------------------- | ----------------------- | -------- | ---------------------------------------------- |
| `series_scoreboard_v2` | часы, киллы, стороны    | 0 с      | по изменению, `clock_lag` p50 3.4 с, макс 60 с |
| `series_table`         | нетворд, уровни игроков | 8 с      | по изменению, p50 3.6 с, макс 40 с             |

Фичи модели идут только из таблицы. Из табло берём часы, и их возраст безопасен:
досчёт точен до 2 с даже после минуты молчания (секция «Часы GRID»).

**Таймер один, и он считает возраст последнего кадра `series_table` с
изменившимся содержимым.** Порог 12 с. Почему хватает одного:

| что происходит                  | таймер по таблице | таймер по любому кадру | что правильно          |
| ------------------------------- | ----------------- | ---------------------- | ---------------------- |
| всё живо                        | молчит            | молчит                 | котируем               |
| сокет умер                      | сработал          | сработал               | не котировать          |
| табло шлёт, таблица молчит 40 с | сработал          | молчит                 | не котировать          |
| пауза в игре                    | сработал          | молчит                 | не котировать          |
| таблица шлёт, часы молчат 60 с  | молчит            | молчит                 | котируем, досчёт точен |

Третья строка — та, которую нельзя пропускать: киллы капают, лента выглядит
живой, а нетворд 40-секундной давности.

Четвёртая строка — почему отдельный детектор паузы не нужен. На паузе золото не
капает, уровни не растут, киллов нет. Таблица не меняется, кадры не приходят, и
тот же порог срабатывает. Это и требует считать возраст по кадру **с новым
содержимым**: повтор того же кадра на паузе не должен сбрасывать таймер.

Вторая строка — почему не пишем второго сторожа на тишину сокета: оба типа кадров
едут по одному соединению, поэтому смерть сокета ловится тем же порогом.

На провале уходим в reduce-only, потом возвращаемся. Это та же механика, что у
Steam на 3 с. Крутить — одну константу.

Для сравнения, как это устроено в Steam: поля паузы там нет вообще.
`steam_feed.py:105` выводит её из роста `timestamp - game_time`, а
`session_quoting.py:286` возвращает `SignalReason.PAUSED`. У GRID поле есть
(`isTicking`), но приезжает оно только с кадром табло, то есть с задержкой до
минуты. Поэтому на нём одном паузу не держим.

Полл не фича модели. Сейчас [`model_server.py`](../dota_2_model/src/live_paper/model_server.py) не грузит модель, если `poll_interval_seconds` в `model.json` не равен коду. Поле убрать. Три константы:

| кто        | константа                            | роль                                                      |
| ---------- | ------------------------------------ | --------------------------------------------------------- |
| Steam live | `STEAM_POLL_INTERVAL_SECONDS = 1`    | сон GetRealtimeStats                                      |
| backtest   | `BACKTEST_POLL_INTERVAL_SECONDS = 1` | `second % poll == 0`, `MAX_SIGNAL_AGE`, **manifest.json** |
| GRID       | нет полла                            | сокет; `GRID_FEED_STALE_SECONDS = 12`                     |

`POLL_INTERVAL_SECONDS` из [`dataset.py`](../dota_2_model/src/shared/constants/dataset.py) удалить.
`GRID_FEED_STALE_SECONDS = 12.0` **уже есть** в `src/shared/constants/strategy.py`, заводить не надо.

---

## Шаг 1 — лаг 10, полл не в модели, train, backtest

- `SOURCE_LAG_SECONDS = 10` в [`dataset.py`](../dota_2_model/src/shared/constants/dataset.py).
- Убрать `poll_interval_seconds` из [`ModelMeta`](../dota_2_model/src/shared/types/model.py), [`train_model.py`](../dota_2_model/src/train_model/train_model.py), проверки в [`model_server.py`](../dota_2_model/src/live_paper/model_server.py).
- `BACKTEST_POLL_INTERVAL_SECONDS` в backtest; в [`build_run_manifest`](../dota_2_model/src/backtest/run.py); [`signals.py`](../dota_2_model/src/backtest/signals.py) читает её.
- `STEAM_POLL_INTERVAL_SECONDS` у Steam-фида.
- В `build_run_manifest` положить и `source_lag_seconds`. Полл не менялся ни разу, лаг меняется сейчас.
- Тесты: `test_model_server`, `test_train_model` (строки 119, 530), `test_model_registry` (строка 22), prepare/lag.
- Конец шага: `make prepare`, train, **прогнать validation-бэктест**. Демон не поднимать на модели с лагом 2.

Что тянет за собой смена лага:

- `PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + SOURCE_LAG)` уедет с 58 на 50: было `-(-60 + 2)`, станет `-(-60 + 10)`. Пересчитается само, править нечего. Но это `window_seconds` в `results.py:225`, то есть колонка в сводке бэктеста сдвинется на 8 секунд без изменения качества модели. Со старыми прогонами её не сравнивать.
- `make market-data` **не** перегонять. Ключ кэша — только параметры quote-engine (`NETWORK_LATENCY_MS`, `TRADE_SIZE`, `MAX_BOOK_AGE_SECONDS`, `PAIR_SUM_TOLERANCE`, `MARKOUT_TAIL_SECONDS`). Лага там нет. Хватает `make prepare`.
- Старые `model.json` не сломаются: `read_model_meta` — обычный `cast`, лишний ключ `poll_interval_seconds` в архивных файлах читается как раньше.
- База для сравнения — сегодняшняя production-модель `20260824T152512Z` на лаге 2. Сравнивать через `scripts/compare_backtests.py`.

## Шаг 2 — `stream_delay_s` в discovery

- `_parse_league_game` в [`discovery.py`](../dota_2_model/src/live_paper/discovery.py) сейчас выкидывает `stream_delay_s`. Протащить в `_UsableLeagueGame` / `DiscoveredMatch`. Значению верить, лаг тика не считать.
- Починить [`docs/live-paper.md`](../dota_2_model/docs/live-paper.md): строка 12 утверждает обратное. Valve **применяет** `stream_delay_s` к GetRealtimeStats — замер в секции «Замеры». Заодно поправить, что задержка меняется по дням внутри одной лиги.
- Писать в `match.json` **два** числа: объявленную задержку (`steam_delay_s`) и замеренную (медиана `received_at_utc - (start_timestamp + timestamp)` по тикам `game_state = 5`). Объявленное поле и реальность могут разойтись, а решение picker будем судить по замеру. Для GRID замер — медианный возраст кадра таблицы.
- Скрипты замера лежат в истории задачи (`scan.py`, `agg.py`, `norm.py`). Через месяц тот же расчёт даст n, которого сегодня нет.

## Шаг 3 — общий фид

Не ABC. `Protocol` + текущий [`GameSnapshot`](../dota_2_model/src/live_paper/steam_feed.py):

```python
class FeedEvent:  # snapshot, received_at_utc, source, horn
class LiveFeed(Protocol):
    def ticks(self) -> AsyncIterator[FeedEvent]: ...
```

Steam — обёртка над `follow_realtime_stats_async`. `MatchWorker.run` ест `LiveFeed`. Таймаут фида — аргумент (Steam: 3 с).

Третий источник потом: новая реализация `LiveFeed` + ветка picker.

Три места, где текущий `GameSnapshot` привязан к Steam:

1. **Гонг идёт из сырого payload.** `match_worker.py:391` зовёт
   `horn_unix_seconds(event.payload["match"])`, `_maybe_pin_horn` — `pin_horn_from_event`.
   У GRID гонг считается иначе: `occurredAt - clock`. Значит гонг — поле события,
   а не то, что выковыривают из payload.
2. **`game_state` — чисто Steam** (4/5/6/7/8). Его читают гейт котирования
   (`session_quoting.py:271-273`), `match_worker.py:215,389`,
   `match_meta.py:139,154,298`, `state_to_parquet.py:198`. У GRID такого поля нет.
   Нужны нейтральные флаги (`pre_horn`, `finished`), а разбор Steam-состояний —
   один раз в `steam_feed.py`. Это правка гейта котирования, самое опасное место
   шага.
3. **Архив.** `StateWriter` пишет `payload` как `SteamRealtimeStats`, а
   `state_to_parquet.py:171-223` читает `payload["match"]`, `game_state`,
   `buildings`, `resolve_teams`. GRID-кадры туда не лезут, `make live-parquet`
   на GRID-матче сломается. Решение: Steam пишет сырой payload в `state.jsonl`
   как сейчас, GRID пишет свои кадры в отдельный файл.

## Шаг 4 — GRID → GameSnapshot, один разбор на двоих

Разбор виджета (parse frame, scoreboard, table, часы, XP) — **один модуль**. Его импортируют и `src/live_paper/grid_feed.py` (лента), и [`watch_grid_live.py`](../dota_2_model/scripts/watch_grid_live.py) (принтер). Не копировать.

Код уже почти весь лежит в watcher: `parse_frame`, `read_net_worth`,
`clock_age_seconds`, `live_clock_seconds`, `GridSocketWatch`. Шаг — это вынос
модуля, не новый код.

- Сокет как сейчас: `delay=zero`, `series_scoreboard_v2` + `series_table`.
- `GameSnapshot`: `second = currentSeconds + возраст часов − delay` (замер в секции «Часы GRID»), `delay` из кадра; `paused` = часы не тикают; `finished` = карта/серия finished.
- Порог 12 с на возраст последнего кадра таблицы **с изменившимся содержимым**. `GridSocketWatch.note_raw()` в watcher зовётся на каждый кадр — при выносе в модуль сбрасывать таймер только на новом содержимом таблицы. От этого зависит детект паузы.
- Карта ≠ `map_number` → skip tick.
- Тесты на payload из [`test_watch_grid_live.py`](../dota_2_model/tests/test_watch_grid_live.py): `second` по формуле выше, XP из `increaseLevel`, просроченная таблица гасит тик, повтор того же кадра таймер не сбрасывает.

## Шаг 5 — picker

После линка sidecar↔Steam (или sidecar с одним только GRID id): правило из «Правило выбора ленты» → Steam / GRID / skip. В `match.json`: `feed_source`, `steam_delay_s` если был Steam, замеренная задержка. Стороны GRID сверить с `yes_is_radiant`; рассинхрон — не котировать тик.

Здесь же переделать `discovery.py:341-344`: матч без `server_steam_id` больше не
выбрасывается, а идёт в picker как кандидат только с GRID.

## Вне скоупа

- Переключение фида в середине карты.
- Правки `poly-maker`.
- Отдельная модель на лаг 60.
- Второй проектор `state_to_parquet` для GRID-кадров.
