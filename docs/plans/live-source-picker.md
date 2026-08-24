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
    content: "Shared widget parse used by grid_feed.py and watch_grid_live.py; GameSnapshot clock from frame delay; socket silence vs table age"
    status: pending
  - id: "step-5-source-picker"
    content: "Pick Steam vs GRID vs skip by the smallest delay under MAX_FEED_DELAY_SECONDS; GRID-only candidates when no server id; record feed_source"
    status: pending
  - id: "step-6-lag-grid-backtest"
    content: "Split train lag from eval lag in prepare; backtest the train-lag x feed-delay grid to set SOURCE_LAG and MAX_FEED_DELAY_SECONDS from data"
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

Табло присылает часы не каждую секунду, а когда что-то меняется. В кадре: «сейчас 22:32», штамп `occurredAt`. Если это было 5 с назад, живые часы = 22:32 + 5. Это локальный тик по возрасту штампа, не новая магия.

Золото в другом кадре, у него `delay=8`. Для модели: `second = (часы табло + возраст штампа) - delay`. `delay` берём **из кадра** (`feed_delay` уже парсится в `read_net_worth`), а не константой 8. Нетворд, XP (`increaseLevel+1`), смерти, top-1 — только из таблицы. Киллы с табло (delay 0) в фичи не мешать. Таблицы на этой карте нет — тика модели нет.

## Таймаут фида

Steam: 3 с без HTTP-тела. GRID: 12 с без любого кадра сокета (и повторы тоже считаются). Дальше как сейчас: модель выкл, entry BUY снять, SELL оставить; пришёл пакет — снова котировать. Порог в конструктор таймера, не одна константа на оба фида.

**У GRID две разные несвежести, и таймаут ловит только одну.** Сокет молчит —
это здоровье соединения, порог 12 с, считаем любой кадр. Но фичи приходят
только из `series_table`, а он пушит по изменению: p50 3.6 с, максимум 40 с
(замер в комментарии к `GRID_FEED_STALE_SECONDS`). Значит нетворд может быть
40-секундной давности, пока scoreboard шлёт кадры и лента выглядит живой.
Модельный гейт вешаем на возраст таблицы (`gold_age` в watcher уже считается),
12 с оставляем сокету.

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

- `PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + SOURCE_LAG)` уедет с 58 на 50. Это `window_seconds` в `results.py:225`. Колонка сдвинется без изменения качества модели — не сравнивать её со старыми прогонами.
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

- Сокет как сейчас: `delay=zero`, `series_scoreboard_v2` + `series_table`.
- `GameSnapshot`: часы как в секции выше, `delay` из кадра; `paused` = часы не тикают; `finished` = карта/серия finished.
- Таймаут 12 с на сырой кадр — здоровье сокета. Модельный гейт — на возрасте таблицы.
- Карта ≠ `map_number` → skip tick.
- Тесты на payload из [`test_watch_grid_live.py`](../dota_2_model/tests/test_watch_grid_live.py): `second = clock - delay`, XP из `increaseLevel`.

## Шаг 5 — picker

После линка sidecar↔Steam (или sidecar с одним только GRID id): правило из «Правило выбора ленты» → Steam / GRID / skip. В `match.json`: `feed_source`, `steam_delay_s` если был Steam, замеренная задержка. Стороны GRID сверить с `yes_is_radiant`; рассинхрон — не котировать тик.

Здесь же переделать `discovery.py:341-344`: матч без `server_steam_id` больше не
выбрасывается, а идёт в picker как кандидат только с GRID.

## Шаг 6 — подбор лага бэктестом (после picker)

Шаги 1–5 ставят лаг 10 и порог 61 без разбирательств. Этот шаг ставит оба числа
по данным. Живой сэмпл на это не годится: 30 матчей, se 290 bps, у каждого дня
своя модель. Валидация даёт 500+ матчей на один прогон.

Сейчас `SOURCE_LAG_SECONDS` — одно число на два джойна:
`prepare_dataset.py:353` (train: рынок на `state.second + lag`) и
`prepare_dataset.py:442` (validation: состояние на `market_second - lag`).
Развести их на два параметра — лаг обучения и лаг ленты. Правка маленькая и
даёт ровно тот эксперимент, который нужен.

Вживую никогда не было «обучили на 60, скормили 60». На 19–22.08 было «обучили
на 2, скормили 60». Сетка это и проверяет:

| лаг обучения | задержка ленты | что отвечает |
|---|---|---|
| 10 | 10 | совпадение: потолок качества |
| 10 | 60 | рассинхрон вверх: столько теряем на медленной лиге |
| 2 | 60 | что реально крутилось 19–22.08 |
| 2 | 10 | что реально крутилось 15.08 |

Из этой сетки выходят оба числа: `SOURCE_LAG_SECONDS` и
`MAX_FEED_DELAY_SECONDS`. Если качество падает плавно — порог 61 остаётся или
растёт. Если обрывается — порог опускается до обрыва.

## Вне скоупа

- Переключение фида в середине карты.
- Правки `poly-maker`.
- Отдельная модель на лаг 60.
- Второй проектор `state_to_parquet` для GRID-кадров.
