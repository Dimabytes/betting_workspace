# План замены Bitsler каталогом Oddin Disir

Дата: 26 сентября 2026 года. Проект: `/root/work/esports-trader`, ветка `main`. Код пишем прямо на VPS.

Статус: пробник каталога прогнан 26.09.2026 в 11:53 UTC (раздел 2). Код каталога не начат. SHA для отката: `72506ba50409f09913f6606064575a3dee372d77`.

Прошлая попытка через Stake и Bright Data лежит в `stake-brightdata-archive.md` в этой папке. Там же расчёты бюджета, все вызовы и запасные варианты.

## 1. Результат, который нужен

Трейдер сам находит Oddin ID текущих и будущих матчей Dota 2 и работает после закрытия Bitsler. Каталог, токен и переподключение идут через Disir. Stake, Bright Data и домашний компьютер не нужны. Бюджет $0.

```text
VPS → Disir dota2TournamentInfo(ID) раз в час → каталог Dota-турниров в памяти демона
VPS → Disir dota2TournamentInfo(активные) раз в минуту → открытые матчи od:match:N
Polymarket + Steam/GRID → сопоставление команд → od:match:N → Disir HTTP + WS, как сейчас
```

## 2. Что проверено 26.09.2026 с VPS

### Disir

- Disir — сервер виджетов Oddin.gg: табло, миникарта, статистика турниров. Oddin поставляет киберспорт для Stake и Bitsler.
- Адрес: `https://api-disir.oddin.gg/main/disir/query`. Заголовки: `X-Api-Key` с токеном оператора, `Origin: https://disir.oddin.gg`.
- Все запросы шли с токеном Stake из `ODDIN_BRAND_TOKEN` в `.env`.
- Документации нет. Схему я прочитал через GraphQL introspection.
- За день Disir принял около 900 запросов. Отказов по лимиту не было.

### Запросы верхнего уровня

| Запрос | Результат |
|---|---|
| `sports` | работает: 49 видов спорта, Dota 2 — `c3BvcnQvb2Q6c3BvcnQ6Mg==`, то есть base64 от `sport/od:sport:2` |
| `tournaments(sportId, dateFrom, dateTo)` | `internal error` на всех проверенных форматах. Не использовать |
| `upcomingMatches(tournamentId, count)` | `internal error`. Не использовать |
| `dota2TournamentInfo(tournamentId)` | работает. Основа каталога |
| `dota2ScoreboardData(matchId)` | работает, это текущий HTTP-снимок трейдера |

### `dota2TournamentInfo`

- ID турнира: base64 от `tournament/od:tournament:N`. Голый `od:tournament:N` Disir отклоняет: `Invalid input argument: tournamentID`.
- Поля турнира: `tournament { id name startTimestamp endTimestamp }`.
- Поля матча в `matches`: `id plannedStartTimestamp startTimestamp endTimestamp isClosed homeTeam { id name } awayTeam { id name } homeScore awayScore bestOfType`.
- `matches.id` — это base64 от `match/od:match:N`.

Состояние матча читается по трём полям. Проверено на PGL Wallachia Season 9, BB Streamers Battle 15 и BLAST SLAM VIII:

| Состояние | `startTimestamp` | `endTimestamp` | `isClosed` |
|---|---|---|---|
| не начался | `null` | `null` | `false` |
| идёт | есть | `null` | `false` |
| закончен | есть | есть | `true` |

### ID турниров

- ID растут подряд. Скан 14400–15099 (700 ID, пауза 0.15 с) занял около двух минут.
- Скан нашёл 42 записи Dota. 8 из них 26.09 ещё не закончились.
- На ID без Dota-турнира Disir отвечает `null`. `Not found` пришёл на 405 ID, в том числе внутри диапазона.
- Последний ID с ответом не `Not found` — 15007.
- Один турнир бывает под несколькими ID. Это стадии: PGL Wallachia Season 9 — 14906–14909, EPL World Series: Southeast Asia Season 18 — 14997–14998. У части стадий нет матчей.

Dota-турниры, которые 26.09 ещё не закончились:

| ID | Турнир | Даты | Матчей | Открытых |
|---|---|---|---:|---:|
| 14906 | PGL Wallachia Season 9 | 19.09–27.09 | 38 | 2 |
| 14907 | PGL Wallachia Season 9 | 19.09–27.09 | 0 | 0 |
| 14908 | PGL Wallachia Season 9 | 19.09–27.09 | 0 | 0 |
| 14909 | PGL Wallachia Season 9 | 19.09–27.09 | 6 | 0 |
| 14921 | BB Streamers Battle 15 | 21.09–03.10 | 22 | 2 |
| 14994 | BLAST SLAM VIII | 29.09–11.10 | 8 | 8 |
| 14997 | EPL World Series: Southeast Asia Season 18 | 24.09–07.10 | 1 | 0 |
| 14998 | EPL World Series: Southeast Asia Season 18 | 24.09–07.10 | 1 | 1 |

### Сверка с рынками 26.09

- Xtreme Gaming vs GamerLegion: `od:match:3232753`, закончен 2-0. Это матч рынка `dota2-xtreme-gl-2026-09-26-game2`.
- Aurora Gaming vs Natus Vincere: `od:match:3232752`, идёт, 0-1.
- BLAST SLAM VIII начнётся 29.09, но все 8 матчей уже имеют ID. Пример: Team Spirit vs Team Nemesis — `od:match:3239531`.

### HTTP-снимок

- `dota2ScoreboardData` отдал снимок законченного матча `od:match:3232748` (GamerLegion vs 1win, `FINISHED`).
- На `od:match:3232749` (Team Nemesis vs Natus Vincere, закончен 25.09) снимок ответил `Not found`. Значит, `Not found` в снимке не означает, что матча нет.

### Что ещё не проверено

1. WebSocket-подписка и `probe_oddin_delay` с токеном Stake. HTTP-снимок с ним работает.
2. Становится ли ID с ответом `Not found` турниром позже. Ответ не нужен до старта: запас вниз в скане (раздел 4) стоит около 200 запросов в час и закрывает оба исхода.
3. Покрытие: все ли Dota-события Polymarket есть у Oddin. Проверяется на paper после перехода, а не заранее.
4. Попадают ли в `dota2TournamentInfo` турниры вида спорта Dota 2 Duels (`od:sport:39`).

### Сверка каталогов, 26.09.2026 11:53 UTC

`scripts/probe_disir_catalog.py` один раз прошёл окно 14700–15107. Пауза 0.15 с, стоп после 100 `Not found` подряд. `frontier` снова 15007. Ответы: 22 турнира Dota, 138 `null`, 248 `Not found`, ошибок нет. Очищенные тела: `esports-trader/tests/fixtures/disir_tournament_info.json` (все 22 турнира, один `null`, один `Not found`). Токена в файле нет.

Идущих матчей не было: у всех открытых карточек `startTimestamp` пустой. WS и задержка с токеном Stake на живой карте в этом запуске не снимались.

Открытые матчи Disir (`isClosed=false` у турниров, чей `endTimestamp` плюс 1 день ещё не наступил) против `list_live_matches()` Bitsler.

Только Disir. Bitsler эти карточки не отдал:

| ID | Матч | Турнир |
|---|---|---|
| `od:match:3232751` | LGD Gaming vs Team Yandex | PGL Wallachia Season 9, план 12:05 UTC |
| `od:match:3232750` | Natus Vincere vs Xtreme Gaming | PGL Wallachia Season 9, план 16:00 UTC |
| `od:match:3232520` | Team Daxak vs Team YBN | BB Streamers Battle 15, план 12:00 UTC |
| `od:match:3232521` | Team Rostik vs Team avice | BB Streamers Battle 15, план 15:00 UTC |

Только Bitsler. В `dota2TournamentInfo` эти матчи уже закрыты:

| ID | Bitsler | Disir |
|---|---|---|
| `od:match:3232748` | `live` 0:0, GamerLegion vs 1win | закрыт 2:1, конец 25.09 14:22 UTC |
| `od:match:3232747` | `not_started` 0:0, Xtreme Gaming vs LGD Gaming | закрыт 0:2, конец 25.09 17:14 UTC |
| `od:match:3232746` | `not_started` 0:0, Aurora Gaming vs Team Yandex | закрыт 1:2, конец 25.09 20:02 UTC |

Восемь матчей BLAST SLAM VIII совпали по id и home/away. Счёт Disir `null`, Bitsler `0:0`: матчи не начались, это не разные карточки.

Утренний открытый матч турнира 14998, Xipto Esports vs Ivory `od:match:3242293`, к 11:53 уже закрыт 2:0.

## 3. Код, который меняется

| Файл | Сейчас | После |
|---|---|---|
| `src/trader/oddin_client.py` | Bitsler `breadcrumbs`, `getMatch`, `list_live_matches`, `BITSLER_BRAND_TOKEN`, `bitsler_client` | Только Disir. `widget_config` берёт токен из `ODDIN_BRAND_TOKEN`. `bitsler_client` переименовать в `disir_client` |
| `src/trader/oddin_catalog.py` — новый | — | Запросы `dota2TournamentInfo`, разбор турниров и матчей, скан ID, фоновый цикл каталога в памяти |
| `src/trader/oddin_types.py` | `Tournament`, `MatchCard`, `Breadcrumbs`, `LiveMatch` в форме Bitsler | Frozen dataclass `OddinTournament` и `OddinMatch`. Состояние матча считается по трём полям из раздела 2 |
| `src/trader/oddin_discovery.py` | `list_oddin_live_matches` обходит Bitsler | Отдаёт открытые матчи из памяти каталога. Сети в discovery нет |
| `src/trader/discovery.py` | `Callable[[], Sequence[LiveMatch]]` | Тот же callable с `OddinMatch` |
| `src/trader/wallet_host.py` | передаёт `list_oddin_live_matches` | Запускает asyncio-task каталога рядом с остальными фоновыми задачами демона и передаёт его метод чтения |
| `src/trader/oddin_live_feed.py` | после WS `complete` вызывает `fetch_match` у Bitsler | `fetch_match` удаляется без замены. `complete` без тиков расходует попытку, лимит попыток завершает фид |
| `src/trader/source_picker.py` | `widget_config` с токеном Bitsler | Без правок: токен приходит через `widget_config` |
| `compose.yaml` | токена нет | `ODDIN_BRAND_TOKEN` в `environment` у `live` и `paper` |
| `scripts/watch_bitsler_live.py` | выбор матча через Bitsler | `scripts/watch_oddin_live.py`: выбор из каталога Disir или по прямому ID |
| `scripts/watch_bitsler_lol.py` | Bitsler `breadcrumbs` для LoL | Только прямой Oddin ID. Каталог LoL в эту работу не входит |

Затронутые тесты: `tests/test_trader_discovery.py`, `tests/trader_discovery_fixtures.py`, `tests/test_discovery_cadence.py`, `tests/test_oddin_feed.py`, `tests/test_oddin_reducer.py`, `tests/test_source_picker.py`, `tests/test_trader_match_lifecycle.py`, `tests/test_trader_wallet_host.py`, `tests/test_watch_bitsler_live.py`, `tests/test_watch_bitsler_lol.py`.

## 4. Каталог

Один фоновый asyncio-task в демоне. Он один ходит в Disir за каталогом, держит результат в памяти и отдаёт его discovery без сети. Discovery работает в `asyncio.to_thread` (`src/trader/cadence.py`), поэтому читает из памяти неизменяемый кортеж: присваивание кортежа атомарно, lock не нужен.

### Скан турниров

1. Раз в час скан проверяет окно ID от `frontier - 200` до `frontier + 100`. `frontier` — последний ID с ответом не `Not found`.
2. Запас вниз закрывает пункт 2 из «Что ещё не проверено» и остаётся, пока это не проверено на живом каталоге.
3. Пауза между запросами скана — 0.15 с. Окно из 300 ID занимает около 45 с.
4. Если скан упал, каталог остаётся прежним. Следующая попытка через 5 минут.
5. Каталог хранит Dota-турниры, у которых `endTimestamp` плюс 1 день ещё не наступил. Старые записи скан удаляет.

### Обновление матчей

1. Раз в 60 с task перечитывает `dota2TournamentInfo` всех турниров, у которых сейчас от `startTimestamp - 1 день` до `endTimestamp + 1 день`. Ответ содержит и турнир, и `matches`, отдельного запроса на матчи нет.
2. Запросы параллельно, timeout 5 с, как `probe_playing` сейчас.
3. Упавший турнир оставляет в памяти прошлый список матчей. В лог идёт возраст этого списка. Пустой список вместо рабочего не подставлять.
4. В сопоставление идут матчи с `isClosed = false`: и начавшиеся, и будущие.

### Старт демона

- При старте task сканирует вверх от `SEED_TOURNAMENT_ID = 14900`, пока не встретит 100 ID `Not found` подряд. Последний ID с ответом становится `frontier`, дальше обычное расписание. Ниже 14900 живых турниров 26.09 нет (таблица в разделе 2). Сегодня старт занимает около 45 с; через месяцы окно от константы вырастет, тогда константу поднять. Всё время старта каталог пуст и discovery работает как при недоступном Bitsler сегодня: только Steam/GRID.
- Каталог на диск не пишется. Рестарты идут в безопасном окне, а Disir в момент рестарта недоступен редко. Если это станет проблемой, файл добавить отдельно.

Нагрузка: при 8 активных турнирах это около 480 запросов в час плюс скан, всего не больше 800. Нынешний обход Bitsler делает больше. Пока есть подходящие рынки, каждые 30 с он вызывает `breadcrumbs`, запрос на каждый турнир и `getMatch` на каждую карточку.

## 5. Сопоставление матчей

- Логику `unique_oddin_match_id` не менять. Команды сопоставляет `orient_outcomes` с текущими aliases из `shared/utils/team_names.py`.
- Если под пару команд подходят несколько открытых матчей, как сейчас выбирать тот, чей снимок Disir уже идёт (`snapshot_is_playing`).
- Home/away у Oddin — не Radiant/Dire. Ориентацию карты по-прежнему даёт Steam/GRID.
- Перед допуском фида по-прежнему проверять номер карты и задержку. Порог 61 секунда в `source_picker.py` не менять.

## 6. Токен и переподключение

1. `widget_config` читает `ODDIN_BRAND_TOKEN` через `env_value`. Константу `BITSLER_BRAND_TOKEN` удалить.
2. Если токена нет, трейдер падает на старте с понятной ошибкой. Запасного токена нет.
3. В контейнере файл `.env` не смонтирован, поэтому `env_value` его не найдёт. Токен надо передать через `environment` в `compose.yaml` для `live` и `paper`:

   ```yaml
       environment:
         ODDIN_BRAND_TOKEN: ${ODDIN_BRAND_TOKEN}
   ```

   Это первый коммит работы (шаг 0). Его применяют `up -d --force-recreate` в ближайшем безопасном окне, ещё до правок Python. Тогда переключение на Disir — обычный `docker compose restart`.

4. После WS `complete` фид переподключается без проверки карточки. Вызов `fetch_match` и класс `_NeedReconnect` из `oddin_live_feed.py` удаляются. Проверка `isClosed` через `dota2TournamentInfo` не делается: для неё пришлось бы протащить ID турнира через `bindings.py`, `archive_types.py`, `match_meta.py` (схема архива) и `feed_selection.py` ради одного вызова.
5. Сейчас `_NeedReconnect` не расходует лимит `MAX_CONSECUTIVE_FAILURES`, и цикл `complete` без данных может идти бесконечно. После правки `complete` без единого тика расходует одну попытку, любой тик сбрасывает счётчик. Закончившийся матч даёт 5 пустых переподключений по 3 с, и фид останавливается сам.
6. Timeout, пустой снимок или `Not found` в снимке не означают конец матча. Они расходуют попытку, как сейчас.
7. Расшифровку, модель без XP, формат архивов, выбор фида и размеры ставок не менять.

## 7. Порядок реализации

Ничего не ждёт сутки. Каждый шаг — отдельный коммит, проверка на paper идёт вместо долгого сравнения каталогов.

### Шаг 0. Токен в compose

Добавить `ODDIN_BRAND_TOKEN` в `environment` у `live` и `paper`. Применить `up -d --force-recreate live paper` в безопасном окне. Python не меняется, поведение трейдера не меняется.

### Шаг A. Пробник и fixtures

1. Добавить `scripts/probe_disir_catalog.py`. Он сканирует окно ID, печатает активные Dota-турниры и открытые матчи. Одноразовый запуск, без цикла.
2. Пробник проверяет WS и задержку с токеном Stake на одном-двух идущих матчах.
3. Пробник сохраняет очищенные ответы в `tests/fixtures/disir_tournament_info.json`. Токен в fixture не попадает.
4. Пока Bitsler отвечает, пробник один раз печатает рядом открытые матчи Disir и текущий `list_live_matches()` Bitsler. Расхождения записать в этот файл, раздел 2.
5. Удалить `scripts/probe_stake_catalog.py` (в git не закоммичен) и `data/oddin_catalog/`. Нужные данные из них уже в архиве этой папки.

### Шаг B. Типы и разбор

Добавить `OddinTournament`, `OddinMatch` и разбор `dota2TournamentInfo` в `oddin_catalog.py`. Тесты разбора: три состояния матча, base64-ID, пустые `matches`, `null` и `Not found` в ответе турнира, GraphQL `errors` при HTTP 200.

### Шаг C. Каталог в памяти и discovery

Реализовать фоновый task из раздела 4, подключить его в `wallet_host.py`, заменить `list_oddin_live_matches` и тип `LiveMatch`. Тесты с управляемыми часами и подменённым HTTP:

- старт от `SEED_TOURNAMENT_ID` до 100 `Not found` подряд;
- движение `frontier`;
- `Not found` внутри окна;
- ошибка скана и ошибка турнира не портят память, discovery получает прошлый список;
- удаление законченных турниров;
- discovery не делает сетевых вызовов и не ждёт task;
- одна пара команд в двух открытых матчах решается через `snapshot_is_playing`.

### Шаг D. Токен и переподключение

Изменить `widget_config` и `oddin_live_feed.py`. Тесты: обрыв сокета, `complete` после тиков переподключается со сброшенным счётчиком, `complete` без тиков расходует попытку, пять пустых `complete` останавливают фид, отсутствие токена валит старт.

### Шаг E. Переход

Раздел 8: paper, одна карта, live.

### Шаг F. Скрипты и очистка

1. Переименовать `watch_bitsler_live.py` в `watch_oddin_live.py`.
2. Перевести `watch_bitsler_lol.py` на прямой Oddin ID.
3. Обновить импорты, docstrings и тесты watcher-скриптов.
4. В действующем коде не должно остаться сетевых обращений к `bitsler.com` и импортов Bitsler-функций. Старые логи и документы с Bitsler не трогать.
5. Шаг косметический, переход на VPS его не ждёт.

### Обязательные проверки

- Запускать Python через `uv run python`. Для тестов нужен `PYTHONPATH=src:scripts:../prediction-market-backtesting`, как в Makefile.
- Во время живой карты тесты не запускать. Запускать только затронутые файлы с `PYTEST_XDIST_AUTO_NUM_WORKERS=1`.
- ruff check, ruff format. basedpyright запускать как `.venv/bin/python -m basedpyright`: скрипт `.venv/bin/basedpyright` ссылается на несуществующий `/root/work/dota_2_model/.venv/bin/python`.
- Интеграционный тест запрещает HTTP и WS к доменам Bitsler и проходит путь: новый рынок → матч из Disir → выбор фида → переподключение.
- `poly-maker` не менять. `git push` не делать.

## 8. Переход на VPS

`live` и `paper` — отдельные сервисы, поэтому paper переключается первым и служит проверкой в бою. Все рестарты только в безопасном окне:

```bash
python3 /root/work/betting_workspace/.shared-skills/vps-trader/scripts/summarize.py --restart-check
# restart_check UNSAFE → остановиться и ждать. SAFE → продолжать.
cd /root/work/esports-trader
```

1. Шаг 0 уже применён: `docker compose exec paper env | grep ODDIN_BRAND_TOKEN` показывает переменную. Если нет — `up -d --force-recreate live paper`, `restart` переменную не применит.
2. `docker compose restart paper`, затем `docker compose logs --since 5m paper`.
3. На paper проверить одну живую карту: открытые матчи в логе discovery, выбранный фид Oddin, корректный net worth, задержка не больше 61 секунды с токеном Stake, переподключение после `complete`, ноль запросов к Bitsler.
4. Если карта прошла — `docker compose restart live` и та же проверка на live.
5. `compose down` не использовать. `--build` без смены зависимостей не делать.

План отката: в безопасном окне вернуть `main` на SHA из шапки и выполнить `docker compose restart live paper` (переменная в `environment` лишней не мешает). После закрытия Bitsler откат вернёт только режим без Oddin: матчи без Steam/GRID будут пропущены. Порог 61 секунда ради ставок не ослаблять.

## 9. Критерии готовности

- [ ] Каталог Disir находит идущие и будущие матчи Dota без Bitsler.
- [ ] Токен Stake работает для HTTP-снимка, WS и `probe_oddin_delay`.
- [ ] Найден новый турнир, которого не было в каталоге при запуске.
- [ ] При недоступном Bitsler работают discovery, игровой поток и переподключение.
- [ ] Ошибка Disir не стирает каталог в памяти и последние удачные списки матчей.
- [ ] Discovery Dota и LoL не делает сетевых вызовов к каталогу и не ждёт фоновый task.
- [ ] Цикл `complete` без данных ограничен лимитом попыток.
- [ ] Логи различают ошибку скана, ошибку списка матчей, отсутствие матча, неоднозначную привязку, отсутствие данных и задержку выше порога.
- [ ] Дыры покрытия Oddin относительно Polymarket записаны в лог и описаны.
- [ ] Paper прошёл одну живую карту на Disir до переключения live.

## 10. Риски

- Disir не документирован. Oddin может изменить схему или закрыть эти запросы. Запасные варианты описаны в `stake-brightdata-archive.md`.
- Токен принадлежит Stake. Если Stake сменит токен, нужен новый токен оператора.
- Смысл ответа `Not found` для ID турниров пока не ясен. Запас вниз в скане закрывает этот риск.
- Каталог живёт в памяти: рестарт при недоступном Disir даёт пустой каталог до первого удачного скана. Сегодня недоступный Bitsler даёт то же самое. Если это будет мешать, добавить файл в `TRADER_DIR`.
- Без проверки `isClosed` фид закончившегося матча делает до пяти пустых переподключений (около 15 с) перед остановкой. Ставок это не касается: терминальный тик и Polymarket закрывают матч раньше.
