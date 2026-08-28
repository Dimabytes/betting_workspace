# Торговля LoL в Trader

Статус: черновик для итоговой проверки

Дата: 2026-08-28

## Назначение

Этот design добавляет League of Legends в торговый daemon после завершения
LoL training pipeline и принятия результата бэктеста. Первый запуск на VPS
оставляет Dota 2 в live и запускает LoL в paper. Затем оператор вручную
переводит LoL в live одной настройкой и пересоздаёт два сервиса Trader.

Design охватывает Polymarket и Kalshi. Polymarket остаётся основной площадкой
обнаружения матчей. После связывания Kalshi каждая площадка ведёт отдельную
торговую сессию для одного подтверждённого физического матча.

## Цели

- Одновременно держать Dota в live и LoL в paper на одном VPS.
- Переключать каждую игру между `off`, `paper` и `live` без изменения кода.
- Работать с Polymarket и Kalshi для каждой включённой игры.
- Сохранить одного реального writer и одного владельца risk state на аккаунт
  биржи.
- Переиспользовать готовые LoL parsers, matching, clock, feature functions и
  модель.
- Обобщить текущую Dota-реализацию небольшой provider-системой.
- Не менять существующее Dota discovery и выбор между Steam и GRID.
- Непрерывно собирать Polymarket-архивы обеих игр независимо от торгового
  режима.
- Оставить решение о переходе из paper в live ручным.

## Не входит в задачу

- Автоматический переход из paper в live.
- Собственный оркестратор контейнеров.
- Отдельный процесс для каждой комбинации игры, биржи и режима.
- Message bus или отдельный сервис игрового фида.
- Динамическая загрузка Python plugins.
- Запасной live-источник состояния LoL.
- Общий экономический риск или PnL между Polymarket и Kalshi.
- Новый observability stack, например Prometheus или Grafana.
- Изменения в замороженном репозитории `poly-maker`.
- Перезапись `old_docs` и старых journal records.
- Перенос paper-позиций, заявок или account state в live.

## Ответственность репозиториев

Будущая реализация меняет три репозитория:

| Репозиторий | Ответственность |
|---|---|
| `dota_2_model` | Trader, providers, модели, торговые сессии, state, tests и Compose |
| `polymarket-collector` | Общая конфигурация игр и четыре collector-сервиса |
| `betting_workspace` | Design, implementation plan, shared knowledge и VPS skill |

`poly-maker` остаётся read-only. `dota_2_model` продолжает использовать fork
как зависимость без изменений его файлов.

## Переименование LivePaper в Trader

Название `LivePaper` описывает выбор режима, а не назначение подсистемы.
Подсистема исполняет торговлю для разных игр и режимов, поэтому её активное
название становится `Trader`.

Реализация выполняет следующие переименования:

| Текущее имя | Новое имя |
|---|---|
| `src/live_paper` | `src/trader` |
| Compose service `live-paper` | `trader-live` и `trader-paper` |
| `docs/live-paper.md` | `docs/trading.md` |
| `data/live_paper` | `data/trader` |
| `make live-paper` | `make trader` |
| `.shared-skills/vps-live-paper` | `.shared-skills/vps-trader` |

Имена сервисов на VPS и operational skill меняются в одном deployment. До
этого deployment текущий `vps-live-paper` остаётся рабочим интерфейсом.

Файлы в `old_docs` не переименовываются. Существующие journal records сохраняют
записанные имена и версии схем.

## Топология на VPS

Один VPS запускает шесть сервисов в двух Compose projects:

```text
polymarket-collector
├── archive-dota  ──┐
├── compact-dota  ──┤── /var/lib/polymarket-dota-archive
├── archive-lol   ──┐
└── compact-lol   ──┤── /var/lib/polymarket-lol-archive

dota_2_model
├── trader-live   ── оба архива read-only ── реальные аккаунты
└── trader-paper  ── оба архива read-only ── симуляция исполнения
```

Оба Trader-сервиса используют один image. Compose фиксирует их режим через
команду запуска:

```yaml
trader-live:
  command: ["python", "-m", "trader", "--mode", "live"]

trader-paper:
  command: ["python", "-m", "trader", "--mode", "paper"]
```

Сервис загружает только игры, назначенные его режиму. Сервис без назначенных
игр остаётся healthy и ничего не делает. Credentials и общий boolean не могут
изменить режим процесса.

### Почему процессы сгруппированы по режиму

Один live-процесс владеет реальным аккаунтом и его общим risk state. Два
live-процесса для Dota и LoL независимо писали бы в один wallet, видели бы
разные позиции и могли бы обойти глобальный halt.

Разделение по биржам дублировало бы игровой фид или потребовало бы message bus.
Отдельный процесс на каждую комбинацию умножал бы state, locks, recovery и
deployment. Два процесса по режиму сохраняют one-writer boundary без новой
инфраструктуры.

## Конфигурация оператора

Оператор хранит один `.env`:

```dotenv
DOTA_TRADING_MODE=live
LOL_TRADING_MODE=paper

KALSHI_MODE=trade
KALSHI_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=/run/secrets/kalshi/prod.key
KALSHI_SUBACCOUNT=0

PK=...
BROWSER_ADDRESS=...
```

Переменные игр принимают точные lowercase-значения:

```text
DOTA_TRADING_MODE = off | paper | live
LOL_TRADING_MODE  = off | paper | live
```

`KALSHI_MODE` принимает три значения:

| Значение | Поведение |
|---|---|
| `off` | Не запускать Kalshi HTTP, WebSocket, matching и execution. |
| `observe` | Находить и записывать рынки без prior и заявок. |
| `trade` | Наследовать режим Trader, которому назначена игра. |

При `KALSHI_MODE=trade` сервис `trader-live` отправляет реальные Kalshi orders,
а `trader-paper` симулирует их. Правило одинаково для Dota и LoL.

Compose читает `.env` для подстановки, но не передаёт все значения в оба
контейнера. Только `trader-live` получает `PK` и `BROWSER_ADDRESS`. Kalshi
credentials и read-only PEM mount получают оба сервиса, потому что Kalshi
paper использует авторизованные market data. В `.env` хранится путь к PEM, а
не содержимое ключа.

Startup завершается ошибкой, если заданы `LIVE_TRADING` или
`KALSHI_TRADING`. Старые настройки не могут скрыто изменить новый routing.

### Профили игр

Размеры и параметры фида хранятся в `config/trader.toml`, а не в `.env`:

```toml
[games.dota]
polymarket_size_usdc = 50
kalshi_size_usd = 10

[games.lol]
polymarket_size_usdc = 20
kalshi_size_usd = 10

[games.lol.feed]
poll_seconds = 10
stale_seconds = 16
```

Размеры Dota остаются без изменений. Начальные размеры LoL равны 20 USDC на
Polymarket и 10 USD на Kalshi.

`games.lol.feed.stale_seconds` является калибруемой настройкой. Начальное
значение 16 совпадает с текущим GRID watchdog. Если paper-наблюдение покажет
частые ложные `stale`, оператор меняет значение на 30 и пересоздаёт Trader.

## Provider-система

Core разделяет игру, биржу и торговую сессию. Это убирает условия для
конкретной комбинации без отдельного plugin для каждой пары.

### `GameProvider`

Game provider отвечает за физический матч:

```text
discover_matches()
open_feed(match)
model_for(market)
```

`DotaProvider` содержит текущее Steam и GRID discovery. `LoLProvider` содержит
lolesports matching, catch-up, polling, clock и feature reduction.

### `VenueProvider`

Venue provider отвечает за одну биржу:

```text
discover_markets(match)
open_market(market, execution_mode)
```

`PolymarketProvider` читает collector sidecars и books. `KalshiProvider`
получает список рынков, связывает ticker, читает book и prior, а затем исполняет
или симулирует orders.

### `MarketSession`

Единица торговли имеет следующий вид:

```text
physical GameMatch + one VenueMarket = one MarketSession
```

Каждая `MarketSession` владеет своими данными:

- immutable market binding;
- book и prior биржи;
- выбор модели и отдельный model call;
- fair value;
- orders, fills, position и PnL;
- venue-specific failure state.

Polymarket и Kalshi не делят один fair. Каждая сессия вызывает модель со своим
midpoint и prior. Первая версия может использовать одну модель игры для обеих
бирж, но model call и результат остаются отдельными. Provider boundary также
разрешает отдельную проверенную модель для конкретной биржи или типа контракта.

Две сессии могут делить `GameFeed` только после доказанного связывания с одним
физическим матчем. Сессии разных матчей не делят игровое состояние.

### Статическая регистрация

Реализация использует typed registry в коде:

```text
game:  dota -> DotaProvider
game:  lol  -> LoLProvider

venue: polymarket -> PolymarketProvider
venue: kalshi     -> KalshiProvider
```

Registry выбирает providers. Правила игры и биржи находятся внутри providers.
Core не содержит веток вида `if game == "lol" and venue == "kalshi"`.

Реализация не использует Python entry points, filesystem discovery, runtime
plugin installation или factory для одного объекта.

## Polymarket запускает discovery

Core начинает работу со свежего и допустимого Polymarket sidecar. Матч, который
есть только на Kalshi, не запускает игровой фид и Trader session.

```text
eligible Polymarket sidecar
-> GameProvider доказывает физический матч
-> GameProvider закрепляет feed и orientation
-> запускается Polymarket MarketSession
-> KalshiProvider ищет совместимый рынок
-> каждая связанная MarketSession работает независимо
```

Polymarket управляет только допуском. После сохранения Kalshi binding проблема
с Polymarket sidecar или рынком останавливает только Polymarket session. Kalshi
продолжает работать с закреплённым игровым фидом.

## Dota discovery не меняется

`DotaProvider` сохраняет текущую последовательность:

1. Прочитать свежие и допустимые Polymarket sidecars.
2. Найти Steam candidates и проверить GRID candidates.
3. Отвергнуть источник без подходящей identity или с declared delay больше 61
   секунды.
4. Выбрать минимальный declared delay. При равенстве выбрать Steam.
5. Если подходящего источника нет, дождаться следующего discovery cycle.
6. Закрепить физический матч и источник на первом принятом feed event.
7. Не менять источник до конца карты.

Первое принятое событие может прийти до начала игры. Источник, найденный после
закрепления, не заменяет текущий. Reconnect и restart повторно открывают только
закреплённый источник.

Существующие правила рынков также сохраняются:

- `map_winner` требует точный номер карты.
- `series_winner` допустим только для best-of-one или равного счёта перед
  решающей картой.
- Явный `Game N Winner` имеет приоритет над `Match Winner` для этой карты.
- Неизвестный формат, конфликт identity и неоднозначные команды fail closed.

## LoL discovery и live state

`LoLProvider` использует lolesports для identity и live state. В первой версии
нет GRID, Riot spectator или коммерческого fallback.

Предыдущее live-исследование уже доказало доступность source и matching path:

- [LoL и Polymarket research handoff](../completed-tasks/lol-init/lol_research_handoff/RESEARCH.md)
- [live window client](../completed-tasks/lol-init/lol_research_handoff/scripts/livestats_client.py)
- [Polymarket-to-livestats matcher](../completed-tasks/lol-init/lol_research_handoff/scripts/pm_to_livestats.py)

Research scripts служат evidence и fixtures. Runtime не импортирует эти
scripts. Он переиспользует более строгие parsers, matching, clock, pause logic,
invariants и features из завершённого LoL pipeline.

### Связывание матча

Provider выполняет следующие действия:

1. Прочитать допустимый LoL sidecar из LoL collector root.
2. Сопоставить команды, лигу, время, best-of и номер карты с lolesports
   schedule и event details.
3. Отвергнуть ноль кандидатов, несколько кандидатов и конфликт orientation.
4. Выбрать `esportsGameId` точной карты.
5. Проверить, что текущая задержка источника не превышает 61 секунду.
6. Закрепить матч, карту, Blue и Red orientation, а также `esportsGameId` на
   первом принятом live event.

Если lolesports ещё не опубликовал `esportsGameId`, provider ждёт и повторяет
discovery. Матч без lolesports coverage не котируется.

Для LoL применяются те же экономические правила рынков. `series_winner`
допускается только для best-of-one или решающей карты при равном счёте. Явный
рынок карты имеет приоритет.

### Catch-up и restart

Livestats не отдаёт готовый pause-aware game clock. Provider восстанавливает
clock от spawn frame и временных разрывов.

При новом binding или mid-game restart provider:

1. Читает локальный raw window archive для `esportsGameId`.
2. Получает loading anchor, если anchor ещё не сохранён.
3. Получает только пропущенные 10-секундные окна до последнего полного окна.
4. Пропускает frames через готовые deduplication, spawn-clock, pause,
   invariant и feature functions.
5. Разрешает торговлю только после получения валидного текущего snapshot.

Provider записывает raw response до публикации trading event. После restart он
продолжает тот же архив и получает только недостающий tail. Новый clock и новый
архив для закреплённой карты не создаются.

### Polling и trading ticks

Provider передаёт явный `startingTime`, выровненный по 10-секундной сетке
lolesports. Запрос без `startingTime` возвращает первые frames игры. Runtime не
считает такой ответ текущим состоянием.

Каждый response содержит несколько raw frames. Reducer принимает все новые
frames, чтобы сохранить clock и invariant history. В торговлю provider
публикует только последний валидный snapshot нового окна.

```text
HTTP polling:       один запрос на 10-секундное окно
raw archive:        все полученные frames
trading interface:  один последний snapshot на новое окно
```

Provider не публикует десять старых trading ticks одной пачкой. Он также не
растягивает старые frames по одному на wall-clock секунду. Повторное окно и
response без нового frame не создают tick.

Фиды отличаются следующим образом:

| Feed | Transport | Trading ticks |
|---|---|---|
| Steam | HTTP polling раз в секунду | Примерно один snapshot в секунду |
| GRID | WebSocket push | Snapshot при изменении таблицы |
| lolesports | HTTP window раз в 10 секунд | Последний snapshot нового окна |

### Stale handling

LoL watchdog запускается после первого текущего snapshot. Он измеряет время с
последнего нового уникального trading tick. Успешный HTTP response без нового
frame не сбрасывает watchdog.

Начальный timeout равен 16 секундам. После timeout game session передаёт одно
`stale` событие всем связанным venue sessions:

Порог 61 секунда проверяет задержку источника при допуске. Порог 16 секунд
проверяет отсутствие новых ticks после закрепления источника.

```text
16 секунд без нового уникального LoL tick
-> запретить новые входы на Polymarket и Kalshi
-> отменить входные orders на обеих биржах
-> оставить risk-reducing exits
-> продолжить polling того же esportsGameId
```

Новый валидный snapshot возобновляет обычное котирование на обеих биржах.
Provider не меняет матч или источник при восстановлении.

Реализация устраняет текущую асимметрию. Существующий watchdog сразу очищает
Polymarket entries, но не отправляет немедленную stale-отмену в Kalshi. Общее
событие game session должно дойти до обеих `MarketSession`.

## Совместимые контракты Polymarket и Kalshi

`KalshiProvider` отображает игру и тип Polymarket market на Kalshi series:

| Игра | Polymarket kind | Kalshi series |
|---|---|---|
| Dota | `map_winner` | `KXDOTA2MAP` |
| Dota | `series_winner` | `KXDOTA2GAME` |
| LoL | `map_winner` | `KXLOLMAP` |
| LoL | `series_winner` | `KXLOLGAME` |

Binding требует точный contract kind. `map_winner` требует точный номер карты.
`series_winner` связывается только с победителем серии. Provider не связывает
map contract с series contract даже на решающей карте.

Orientation команд должна разрешиться ровно одним способом. Ноль кандидатов
даёт `none`. Несколько кандидатов дают `ambiguous`. Оба результата отключают
только Kalshi session. Polymarket продолжает работать.

Kalshi matching сохраняет текущий ограниченный поиск:

1. Выполнить один поиск после допуска Polymarket.
2. При результате `none` повторить поиск один раз на первом `IN_PROGRESS`.
3. Остановить поиск на текущем buy cutoff.

Постоянный Kalshi polling не добавляется.

## Collector-сервисы

Collector image становится настраиваемым по игре. Compose задаёт значения, и
режим Trader на них не влияет:

```text
archive-dota: GAME=dota, POLYMARKET_TAG_ID=102366
compact-dota: GAME=dota, POLYMARKET_TAG_ID=102366
archive-lol:  GAME=lol,  POLYMARKET_TAG_ID=65
compact-lol:  GAME=lol,  POLYMARKET_TAG_ID=65
```

Реализация заменяет Dota-specific `DOTA_TAG_ID` на
`POLYMARKET_TAG_ID`. Переменная `GAME` выбирает правила parsing title и market
kind. Обе игры сохраняют текущие sidecar concepts: `map_winner`,
`series_winner`, map number, outcomes, tokens, trade flags и optional GRID
metadata.

Отдельные roots уже определяют игру, поэтому sidecar schema не получает поле
`game` только для routing. Оба Trader-сервиса монтируют roots read-only:

```text
/archive/dota -> /var/lib/polymarket-dota-archive
/archive/lol  -> /var/lib/polymarket-lol-archive
```

Collectors работают независимо от `DOTA_TRADING_MODE` и
`LOL_TRADING_MODE`. Они продолжают работу во время restart и смены режима
Trader.

Collectors сохраняют Polymarket metadata и books. Live game state приходит из
`GameProvider`. Kalshi data читает `KalshiProvider` напрямую.

## State и locks

Writable state разделяется по режиму исполнения:

```text
data/trader/live/
├── wallet state
├── kalshi.db and kalshi.db.lock
└── matches/
    ├── dota/
    └── lol/

data/trader/paper/
├── wallet state
├── kalshi.db and kalshi.db.lock
└── matches/
    ├── dota/
    └── lol/
```

`trader-live` является единственным writer в `data/trader/live`.
`trader-paper` является единственным writer в `data/trader/paper`. Это убирает
текущий конфликт, при котором paper и live делили бы один `kalshi.db.lock`.

Внутри одного режима Dota и LoL используют общий account state и account-level
locks. Match journals хранятся в каталогах игры. Одинаковый condition ID или
match ID из разных игр не создаёт path collision.

При переводе LoL из paper в live его paper database и journals остаются в
paper root. Live-процесс начинает LoL с текущим live account state. Он не
импортирует simulated positions, fills, cash или open orders.

## Владение риском

Один Polymarket Engine на режим обслуживает все назначенные игры. Live Engine
владеет реальным wallet и общим risk state. Глобальный Polymarket halt
останавливает Polymarket sessions Dota и LoL.

Kalshi сохраняет отдельные venue state и risk. Один Kalshi runtime на режим
обслуживает обе игры этого режима. Design не объединяет exposure Polymarket и
Kalshi в одно число.

Ошибки ограничиваются минимальной областью:

- Ошибка Polymarket market или sidecar останавливает только эту Polymarket
  session.
- Ошибка Kalshi ticker останавливает только эту Kalshi session.
- Ошибка Kalshi account может остановить все Kalshi sessions, не останавливая
  Polymarket.
- Stale игрового фида запрещает входы на всех биржах этого физического матча.
- Глобальный venue halt действует на все sessions общего аккаунта биржи.

## Ручной rollout на VPS

Deployment не содержит автоматического promotion rule и обязательной
длительности paper. Решение принимает оператор.

### Запуск LoL в paper

1. Развернуть `archive-lol` и `compact-lol`.
2. Проверить появление свежих LoL sidecars и books.
3. Развернуть `trader-live` и `trader-paper` с настройками:

   ```dotenv
   DOTA_TRADING_MODE=live
   LOL_TRADING_MODE=paper
   KALSHI_MODE=trade
   ```

4. Проверить, что Dota осталась в live.
5. Проверить, что LoL только симулирует orders и fills на обеих биржах.
6. Наблюдать LoL до ручного решения.

### Перевод LoL в live

Смена выполняется между матчами:

1. Изменить одну строку в `.env`:

   ```dotenv
   LOL_TRADING_MODE=live
   ```

2. Пересоздать оба Trader-сервиса:

   ```bash
   docker compose up -d --force-recreate trader-live trader-paper
   ```

   `docker compose restart` не перечитывает environment и для этой операции не
   подходит.

3. Проверить отсутствие LoL assignment у `trader-paper`.
4. Проверить, что `trader-live` получил LoL и размеры 20 USDC и 10 USD.
5. Проверить, что реальные LoL orders появляются только после валидного
   текущего snapshot, binding, book, prior и model decision.

### Возврат LoL в paper или off

Между матчами установить `LOL_TRADING_MODE=paper` или
`LOL_TRADING_MODE=off`. Затем пересоздать оба Trader-сервиса той же командой.
Collectors продолжают работу во время смены режима.

## Мониторинг и VPS skill

Реализация переиспользует logs, JSONL journals, SQLite state и shared VPS skill.
Отдельный observability service не добавляется.

Structured records и operator summaries получают следующие dimensions:

- `game`;
- `venue`;
- `execution_mode`;
- identity физического матча;
- identity рынка биржи;
- feed source и возраст последнего tick.

Переименованный `vps-trader` поддерживает следующие проверки:

- здоровье двух Trader и четырёх collector services;
- активный routing Dota и LoL;
- возраст LoL tick, 10-секундный cadence и stale events;
- результаты Polymarket, lolesports и Kalshi matching;
- quotes, fills, positions и PnL по игре, бирже и режиму;
- отсутствие реальных LoL orders в paper;
- account halts, feed failures, retry exhaustion и unresolved fences.

Существующие alerts получают labels игры, биржи и режима. Alerts для start,
binding, stale, dead feed, exhausted session и account halt остаются
операционными сигналами. Они не принимают решение о переходе в live.

## Проверки реализации

Каждая нетривиальная граница оставляет один runnable check.

### Конфигурация и изоляция процессов

- Отклонить неверные game modes и `KALSHI_MODE`.
- Отклонить старые `LIVE_TRADING` и `KALSHI_TRADING`.
- Доказать, что Trader загружает только назначенные игры.
- Доказать отсутствие Polymarket live wallet secrets в paper-сервисе.
- Доказать разные writable roots и locks для live и paper.

### Providers и sessions

- Зарегистрировать Dota, LoL, Polymarket и Kalshi через static registry.
- Доказать, что один physical feed обслуживает две независимые venue sessions.
- Доказать отдельные book, prior, model call, fair и order state каждой биржи.
- Доказать, что ошибка одной биржи не останавливает уже связанную сессию другой.
- Сохранить Dota delay selection, first-event pin и запрет switch после pin.

### LoL feed

- Сопоставить PM и lolesports fixtures и отклонить ambiguity.
- Продолжить raw archive и получить только пропущенные windows.
- Восстановить spawn time и pauses после mid-game restart.
- Записать все raw frames, но опубликовать только последний валидный snapshot
  нового окна.
- Не публиковать tick и не сбрасывать watchdog для duplicate window.
- Создать `stale` после 16 секунд без нового уникального tick.
- Отменить входные orders Polymarket и Kalshi на `stale`.
- Возобновить обе venue sessions после нового валидного tick.
- Fail closed при несовместимой lolesports schema или invariant violation.

### Совместимость рынков

- Отобразить каждую игру и market kind на точный Kalshi series.
- Потребовать точный номер карты для map contracts.
- Отклонить cross-kind binding между map и series.
- Отклонить ноль и несколько Kalshi candidates без остановки Polymarket.
- Выполнить только первый Kalshi search и один retry на `IN_PROGRESS`.

### Collectors

- Запустить один image с Dota и LoL tag IDs.
- Изолировать Dota и LoL roots.
- Классифицировать LoL map и series markets в существующие sidecar kinds.
- Сохранить оба архива во время restart Trader.

### Приёмка paper на VPS

- Все шесть сервисов проходят обычный lifecycle матча.
- Dota live behavior и размеры не изменились.
- LoL получает один trading snapshot на полное новое window.
- LoL paper работает с обеими биржами без реальных LoL orders.
- Stale и recovery действуют на обе venue sessions одного матча.
- Journals и summaries разделяют игру, биржу и режим.
- Оператор переключает LoL между `paper`, `live` и `off` через `.env` и
  пересоздание двух Trader-сервисов.

## Риск внешнего источника

lolesports является публичным frontend API, а не контрактным data feed. API key,
schema, доступность окна или задержка могут измениться без уведомления.
`LoLProvider` проверяет trust boundaries, сохраняет raw responses и fail closed.
Runtime не угадывает матч, не создаёт вымышленные frames и не переключается на
source, для которого модель не обучалась.
