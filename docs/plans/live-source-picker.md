<!-- b459dc8b-88a9-4889-b3be-77aeed1f75eb -->
---
todos:
  - id: "step-1-lag-poll-train"
    content: "SOURCE_LAG=10, poll out of model.json into backtest manifest, split Steam/backtest poll constants, prepare+train+validation backtest"
    status: pending
  - id: "step-2-steam-delay-field"
    content: "Keep stream_delay_s from GetLiveLeagueGames on DiscoveredMatch; trust it; fix live-paper.md lag claim"
    status: pending
  - id: "step-3-feed-protocol"
    content: "LiveFeed Protocol around GameSnapshot; MatchWorker + feed timeout argument; Steam wrapper"
    status: pending
  - id: "step-4-grid-feed"
    content: "Shared widget parse used by grid_feed.py and watch_grid_live.py; GameSnapshot clock-8; 12s timeout"
    status: pending
  - id: "step-5-source-picker"
    content: "Pick Steam vs GRID vs skip by the simple delay rule; record feed_source"
    status: pending
isProject: false
---

# Live Steam/GRID picker + lag 10

## Discovery: один цикл, не два

Сейчас нет отдельного «GRID discovery». Раз в 60 с один проход:

1. Сидкары коллектора (рынки Polymarket). В сидкаре уже лежит `gridSeriesId`, если Gamma его прислала. Может быть `null`.
2. Steam `GetLiveLeagueGames` + имена команд → `match_id`, номер карты (`wins+1`), `server_steam_id`, и поле `stream_delay_s` (сейчас парсер его выкидывает).

GRID не ищет матчи. Это сокет по уже известному `gridSeriesId`, когда picker выбрал GRID как ленту.

Если в этом цикле `gridSeriesId` ещё null, GRID взять нельзя. Следующий цикл через 60 с — Gamma могла дописать id.

Номер карты: Steam `current_map` должен совпасть с `mapNumber` сидкаря (Game 3 рынок есть заранее, линк только когда играют третью). На GRID живая карта = `activeGameIndex+1` / `series_table.sequenceNumber`. Не совпало с нашим map N — кадр выкинуть.

## Правило выбора ленты

Задержке Steam **верим** `stream_delay_s`. Сами не меряем. Задержка GRID — поле `delay` у `series_table` (8 с).

Один раз на аттач, в середине не переключаем.

1. Собрать доступные источники: Steam, если линк с GetLiveLeagueGames есть; GRID, если в сидкаре есть `gridSeriesId`.
2. Ни одного → skip.
3. Ровно один → берём его, если его задержка **<= 61**. Иначе skip всего матча.
4. Оба → берём тот, у кого задержка меньше. Если у победителя всё равно **> 61** → skip матча.

Примеры: Steam 10 и GRID 8 → GRID. Steam 60, GRID нет → Steam. Steam 900 и GRID 8 → GRID. Steam 900, GRID нет → skip.

## Часы GRID

Табло присылает часы не каждую секунду, а когда что-то меняется. В кадре: «сейчас 22:32», штамп `occurredAt`. Если это было 5 с назад, живые часы = 22:32 + 5. Это локальный тик по возрасту штампа, не новая магия.

Золото в другом кадре, у него `delay=8`. Для модели: `second = (часы табло + возраст штампа) - 8`. Нетвард, XP (`increaseLevel+1`), смерти, top-1 — только из таблицы. Киллы с табло (delay 0) в фичи не мешать. Таблицы на этой карте нет — тика модели нет.

## Таймаут фида

Steam: 3 с без HTTP-тела. GRID: 12 с без любого кадра сокета (и повторы тоже считаются). Дальше как сейчас: модель выкл, entry BUY снять, SELL оставить; пришёл пакет — снова котировать. Порог в конструктор таймера, не одна константа на оба фида.

Полл не фича модели. Сейчас [`model_server.py`](../dota_2_model/src/live_paper/model_server.py) не грузит модель, если `poll_interval_seconds` в `model.json` не равен коду. Поле убрать. Три константы:

| кто | константа | роль |
|---|---|---|
| Steam live | `STEAM_POLL_INTERVAL_SECONDS = 1` | сон GetRealtimeStats |
| backtest | `BACKTEST_POLL_INTERVAL_SECONDS = 1` | `second % poll == 0`, `MAX_SIGNAL_AGE`, **manifest.json** |
| GRID | нет полла | сокет; `GRID_FEED_STALE_SECONDS = 12` |

`POLL_INTERVAL_SECONDS` из [`dataset.py`](../dota_2_model/src/shared/constants/dataset.py) удалить.

---

## Шаг 1 — лаг 10, полл не в модели, train, backtest

- `SOURCE_LAG_SECONDS = 10` в [`dataset.py`](../dota_2_model/src/shared/constants/dataset.py).
- Убрать `poll_interval_seconds` из [`ModelMeta`](../dota_2_model/src/shared/types/model.py), [`train_model.py`](../dota_2_model/src/train_model/train_model.py), проверки в [`model_server.py`](../dota_2_model/src/live_paper/model_server.py).
- `BACKTEST_POLL_INTERVAL_SECONDS` в backtest; в [`build_run_manifest`](../dota_2_model/src/backtest/run.py); [`signals.py`](../dota_2_model/src/backtest/signals.py) читает её.
- `STEAM_POLL_INTERVAL_SECONDS` у Steam-фида.
- Тесты: `test_model_server`, `test_train_model`, prepare/lag.
- Конец шага: `make prepare`, train, **прогнать validation-бэктест**. Демон не поднимать на модели с лагом 2.

## Шаг 2 — `stream_delay_s` в discovery

- `_parse_league_game` в [`discovery.py`](../dota_2_model/src/live_paper/discovery.py) сейчас выкидывает `stream_delay_s`. Протащить в `_UsableLeagueGame` / `DiscoveredMatch`. Значению верить, лаг тика не считать.
- Починить [`docs/live-paper.md`](../dota_2_model/docs/live-paper.md): Valve **применяет** `stream_delay_s` к GetRealtimeStats.

## Шаг 3 — общий фид

Не ABC. `Protocol` + текущий [`GameSnapshot`](../dota_2_model/src/live_paper/steam_feed.py):

```python
class FeedEvent:  # snapshot, received_at_utc, source
class LiveFeed(Protocol):
    def ticks(self) -> AsyncIterator[FeedEvent]: ...
```

Steam — обёртка над `follow_realtime_stats_async`. `MatchWorker.run` ест `LiveFeed`. Таймаут фида — аргумент (Steam: 3 с). Архив Steam: сырой payload в `state.jsonl`.

Третий источник потом: новая реализация `LiveFeed` + ветка picker.

## Шаг 4 — GRID → GameSnapshot, один разбор на двоих

Разбор виджета (parse frame, scoreboard, table, часы, XP) — **один модуль**. Его импортируют и `src/live_paper/grid_feed.py` (лента), и [`watch_grid_live.py`](../dota_2_model/scripts/watch_grid_live.py) (принтер). Не копировать.

- Сокет как сейчас: `delay=zero`, `series_scoreboard_v2` + `series_table`.
- `GameSnapshot`: часы как в секции выше; `paused` = часы не тикают; `finished` = карта/серия finished.
- Таймаут 12 с на сырой кадр.
- Карта ≠ `map_number` → skip tick.
- Тесты на payload из [`test_watch_grid_live.py`](../dota_2_model/tests/test_watch_grid_live.py): `second = clock - 8`, XP из `increaseLevel`.

## Шаг 5 — picker

После линка sidecar↔Steam (или sidecar с одним только GRID id): правило из «Правило выбора ленты» → Steam / GRID / skip. В `match.json`: `feed_source`, `steam_delay_s` если был Steam. Стороны GRID сверить с `yes_is_radiant`; рассинхрон — не котировать тик.

## Вне скоупа

- Переключение фида в середине карты.
- Правки `poly-maker`.
- Отдельная модель на лаг 60.
