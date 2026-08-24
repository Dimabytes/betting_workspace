<!-- b459dc8b-88a9-4889-b3be-77aeed1f75eb -->
---
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
    content: "Measure GRID clock_lag first, then pick clock rule A or B; shared widget parse used by grid_feed.py and watch_grid_live.py; 12s timeout on series_table frames only"
    status: pending
  - id: "step-5-source-picker"
    content: "Pick Steam vs GRID vs skip by the smallest delay under MAX_FEED_DELAY_SECONDS; GRID-only candidates when no server id; record feed_source"
    status: pending
  - id: "step-6-lag-grid-backtest"
    content: "Split SOURCE_LAG (train) from FEED_DELAY (eval) in prepare; backtest the 5-cell train-lag x feed-delay grid overnight to set SOURCE_LAG and MAX_FEED_DELAY_SECONDS from data"
    status: pending
isProject: false
---

# Live Steam/GRID picker + lag 10

## Замеры, на которых стоит план

Считал по архивам `data/live_paper/*/state.jsonl`: `received_at_utc` минус
`start_timestamp + timestamp`, только `game_state = 5`.

| дни | лига | замеренная задержка, с |
|---|---|---|
| 15.08 | 19719 | 10.0–10.7 |
| 19.08–22.08 | 19719 | 58.8–61.2 |
| 22.08 | 19944 | 899–901 |

Разброс внутри матча — меньше секунды. Valve **применяет** `stream_delay_s` к
GetRealtimeStats. Одна и та же лига 19719 шла с задержкой 10 пятнадцатого и 60
девятнадцатого: задержка меняется по дням внутри турнира. Читаем поле на каждый
матч и никогда не предполагаем.

PnL по бакетам задержки, нормированный на BUY-нотионал (иначе бакеты
несравнимы: 15.08 позиции были 5–10 USDC, 20–22.08 — 400–1500 USDC):

| задержка | матчей с филлами | нотионал | PnL | edge | t |
|---|---|---|---|---|---|
| 10 с | 7 | 50 | +0.3 | +62 bps | 0.27 |
| 60 с | 30 | 14 018 | +368.8 | +263 bps | 0.67 |
| 900 с | 2 | 200 | −91.4 | −4568 bps | −0.84 |

Модель училась на лаге 2 и на задержке 60 заработала. Деградации нет. Но и
доказательства нет: sd по матчам 1590 bps, se 290 bps, то есть 0.67 сигмы.
Бакеты различаются не только лагом — у каждого дня своя модель, свой размер,
своя версия демона. Единственный матч с полной потерей позиции (−100 USDC на
100 нотионала) лежит в бакете 900 с.

Отсюда два решения плана: задержку минимизируем, порог отсечения ставим по
данным, а не по теории. Точный подбор лага — шаг 6, после того как picker
заработает.

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

`MAX_FEED_DELAY_SECONDS = 61` в `src/shared/constants/strategy.py`. Причина в
комментарии рядом с константой, не в чьей-то голове: на 10 и 60 секундах живые
матчи не в убытке, на 900 лежит единственный полный слив позиции, а между 61 и
900 у нас нет ни одного матча. Порог двигаем после шага 6.

Берём меньшую задержку, а не ближайшую к обученному лагу. Свежесть данных
важнее совпадения с лагом обучения: модель на лаге 2 отработала задержку 60 без
потерь, а единственный слив пришёл на самой большой задержке. Тик под лаг
модели не задерживаем.

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

### Чем датируем нетворд: замерить перед шагом 4

Два варианта, третьего нет:

| вариант | правило | цена |
|---|---|---|
| A | `second = currentSeconds` как пришло | занижаем секунду на столько, сколько назад табло опубликовало часы |
| B | `second = currentSeconds + (приход таблицы − приход табло) − delay` | наш секундомер между двумя приходами; дырка: пауза началась, а табло молчит |

Источник часов в обоих один — GRID. В варианте B наши часы работают
секундомером между двумя приходами, а не вторым источником времени.

Замер решает выбор, а не рассуждение. `watch_grid_live.py` уже печатает колонку
`clock_lag` — это и есть слагаемое секундомера. Запустить на живой серии,
посмотреть распределение.

- Табло шлёт часы примерно раз в секунду → берём A, `live_clock_seconds` удаляем.
- Табло правда отстаёт на секунды → берём B, дырку с паузой закрываем `isTicking`.

В единственном записанном кадре `publishDelay: 1`. Утверждение «`currentSeconds`
может сидеть на десять секунд позади» живёт только в докстринге
`live_clock_seconds`, систематически его никто не мерил. Замер стоит двадцать
минут и снимает вопрос — так же, как замер задержки Steam снял вопрос про
`stream_delay_s`.

## Таймаут фида

Steam: 3 с без HTTP-тела. GRID: 12 с без кадра `series_table`. Дальше как сейчас: модель выкл, entry BUY снять, SELL оставить; пришёл пакет — снова котировать. Порог в конструктор таймера, не одна константа на оба фида.

Источник у матча один: либо Steam, либо GRID. Вместе они не работают.

У GRID одно соединение, но два типа кадров. Это не два фида, это один сокет:

| кадр | что несёт | задержка | как часто |
|---|---|---|---|
| `series_scoreboard_v2` | часы, киллы, стороны | 0 с | по изменению |
| `series_table` | нетворд, уровни игроков | 8 с | по изменению, p50 3.6 с, макс 40 с |

Фичи модели идут только из таблицы. Из табло берём только часы.

**Таймер один, и он считает возраст последнего кадра `series_table`.** Кадры
табло в него не входят. Почему хватает одного:

| что происходит | таймер по таблице | таймер по любому кадру | что правильно |
|---|---|---|---|
| всё живо | молчит | молчит | котируем |
| сокет умер | сработал | сработал | не котировать |
| сокет жив, табло шлёт, таблица молчит 40 с | сработал | молчит | не котировать |
| сокет жив, таблица шлёт, табло молчит | молчит | молчит | котируем |

Третья строка — та, которую нельзя пропускать: киллы капают, лента выглядит
живой, а нетворд 40-секундной давности. Вторая строка — почему второй сторож
лишний: таблица едет по тому же сокету, поэтому смерть соединения ловится тем же
таймером. Таймер по таблице ловит всё, что ловит таймер по любому кадру, плюс
третью строку. Второго не пишем.

Четвёртая строка зависит от замера часов (секция «Часы GRID»). Если табло шлёт
часы раз в секунду, молчание табло само по себе значит остановку, и гейт на него
не нужен. Если табло отстаёт на секунды, гейт вешаем и на возраст штампа часов —
тем же порогом, той же константой. Решаем после замера, не сейчас.

`GRID_FEED_STALE_SECONDS = 12.0` под таблицу и калиброван (замер по gold-кадрам:
p50 3.6 с, максимум 40 с). На длинной паузе таблицы гейт сработает и мы уйдём в
reduce-only, потом вернёмся. Это безопасная сторона, и это ровно то же
поведение, что у Steam на 3 с. Крутить — одну константу.

Полл не фича модели. Сейчас [`model_server.py`](../dota_2_model/src/live_paper/model_server.py) не грузит модель, если `poll_interval_seconds` в `model.json` не равен коду. Поле убрать. Три константы:

| кто | константа | роль |
|---|---|---|
| Steam live | `STEAM_POLL_INTERVAL_SECONDS = 1` | сон GetRealtimeStats |
| backtest | `BACKTEST_POLL_INTERVAL_SECONDS = 1` | `second % poll == 0`, `MAX_SIGNAL_AGE`, **manifest.json** |
| GRID | нет полла | сокет; `GRID_FEED_STALE_SECONDS = 12` |

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
   как сейчас, GRID пишет свои кадры в отдельный файл, `live-parquet` GRID-матчи
   пропускает. Второй проектор — не в этом плане.

## Шаг 4 — GRID → GameSnapshot, один разбор на двоих

Разбор виджета (parse frame, scoreboard, table, часы, XP) — **один модуль**. Его импортируют и `src/live_paper/grid_feed.py` (лента), и [`watch_grid_live.py`](../dota_2_model/scripts/watch_grid_live.py) (принтер). Не копировать.

Код уже почти весь лежит в watcher: `parse_frame`, `read_net_worth`,
`clock_age_seconds`, `live_clock_seconds`, `GridSocketWatch`. Шаг — это вынос
модуля, не новый код.

- **Сначала замер часов.** Запустить `watch_grid_live.py` на живой серии, снять распределение `clock_lag`. Вариант A или B из секции «Часы GRID» — по замеру. В варианте A `live_clock_seconds` и `clock_age_seconds` уходят из модуля вообще.
- Сокет как сейчас: `delay=zero`, `series_scoreboard_v2` + `series_table`.
- `GameSnapshot`: часы по выбранному варианту, `delay` из кадра; `paused` = часы не тикают; `finished` = карта/серия finished.
- Таймаут 12 с считает кадры `series_table`, не любые. `GridSocketWatch.note_raw()` в watcher зовётся на каждый кадр — при выносе в модуль дёргать его только на таблице.
- Карта ≠ `map_number` → skip tick.
- Тесты на payload из [`test_watch_grid_live.py`](../dota_2_model/tests/test_watch_grid_live.py): `second` по выбранному варианту, XP из `increaseLevel`.

## Шаг 5 — picker

После линка sidecar↔Steam (или sidecar с одним только GRID id): правило из «Правило выбора ленты» → Steam / GRID / skip. В `match.json`: `feed_source`, `steam_delay_s` если был Steam, замеренная задержка. Стороны GRID сверить с `yes_is_radiant`; рассинхрон — не котировать тик.

Здесь же переделать `discovery.py:341-344`: матч без `server_steam_id` больше не
выбрасывается, а идёт в picker как кандидат только с GRID.

## Шаг 6 — подбор лага бэктестом (после picker)

Шаги 1–5 ставят лаг 10 и порог 61 без разбирательств. Этот шаг ставит оба числа
по данным. Живой сэмпл на это не годится: 30 матчей, se 290 bps, у каждого дня
своя модель. Валидация даёт 500+ матчей на один прогон.

Сейчас `SOURCE_LAG_SECONDS` — одно число на две стороны. Развести на два:

- `SOURCE_LAG_SECONDS` — лаг обучения. Одно место: `prepare_dataset.py:353`, рынок на `state.second + lag`.
- `FEED_DELAY_SECONDS` — задержка ленты. Два места: `prepare_dataset.py:442` (`lagged_second = market_second - lag`) и `train_model.py:82` (`lagged_source_features`, его же импортирует `signals.py`).

Вживую никогда не было «обучили на 60, скормили 60». На 19–22.08 было «обучили
на 2, скормили 60». Сетка это и проверяет:

| лаг обучения | задержка ленты | что отвечает |
|---|---|---|
| 10 | 10 | совпадение: потолок качества |
| 10 | 60 | рассинхрон вверх: столько теряем на медленной лиге |
| 2 | 60 | что реально крутилось 19–22.08 |
| 2 | 10 | что реально крутилось 15.08 |
| 0 | 10 | нулевая база: сколько даёт сам лаг обучения |

Из этой сетки выходят оба числа: `SOURCE_LAG_SECONDS` и
`MAX_FEED_DELAY_SECONDS`. Если качество падает плавно — порог 61 остаётся или
растёт. Если обрывается — порог опускается до обрыва.

### Рецепт на ночь

Клетки идут **по очереди**, одна за другой. Никаких трёх `prepare` вперёд и
никакого жонглирования именами parquet. Один круг — одна клетка:

```
make prepare   ->   make train   ->   backtest (5 шардов + merge)
```

Порядок клеток такой, чтобы лаг обучения менялся как можно реже:

| круг | лаг обучения | задержка ленты | train |
|---|---|---|---|
| 1 | 0 | 10 | да |
| 2 | 2 | 10 | да |
| 3 | 2 | 60 | нет, модель круга 2 |
| 4 | 10 | 10 | да |
| 5 | 10 | 60 | нет, модель круга 4 |

`make prepare` пишет и train-parquet, и validation-parquet за один проход.
Поэтому каждый круг начинается с него. Когда меняется только задержка ленты
(круги 3 и 5), train-parquet выходит байт в байт тот же, и `make train` можно
пропустить: модель была бы идентичной, только с новым именем.

Правила круга:

- Пять шардов бэктеста запускать вместе, не по очереди. Это правило проекта.
- `--name` своё на каждый круг, обе цифры в имени: `lag10_feed60`. Иначе каталоги схлопнутся.
- Сравнивать через `scripts/compare_backtests.py`. Смотреть и на PnL, и на число сделок: если рассинхрон убивает не край, а количество входов, это другой вывод.
- `window_seconds` между кругами не сравнивать: `PREHORN_LEAD_SECONDS` зависит от лага обучения и в каждой клетке свой (60, 58, 50).

Три train и пять бэктестов по пять шардов. При 20–30 минутах на бэктест это ночь.

## Вне скоупа

- Переключение фида в середине карты.
- Правки `poly-maker`.
- Отдельная модель на лаг 60.
- Второй проектор `state_to_parquet` для GRID-кадров.
