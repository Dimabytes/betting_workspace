# План замены Bitsler каталогом Oddin Disir

Дата: 26 сентября 2026 года. Проект: `/root/work/esports-trader`, ветка `main`. Код пишем прямо на VPS.

Статус: ручная проверка Disir выполнена 26.09.2026 (раздел 2). Код не начат. SHA для отката: `72506ba50409f09913f6606064575a3dee372d77`.

Прошлая попытка через Stake и Bright Data лежит в `stake-brightdata-archive.md` в этой папке. Там же расчёты бюджета, все вызовы и запасные варианты.

## 1. Результат, который нужен

Трейдер сам находит Oddin ID текущих и будущих матчей Dota 2 и работает после закрытия Bitsler. Каталог, токен и переподключение идут через Disir. Stake, Bright Data и домашний компьютер не нужны. Бюджет $0.

```text
VPS → Disir dota2TournamentInfo(ID) раз в час → реестр Dota-турниров на диске
VPS → Disir dota2TournamentInfo.matches        → открытые матчи od:match:N
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
2. Становится ли ID с ответом `Not found` турниром позже.
3. Покрытие: все ли Dota-события Polymarket есть у Oddin.
4. Попадают ли в `dota2TournamentInfo` турниры вида спорта Dota 2 Duels (`od:sport:39`).

## 3. Код, который меняется

| Файл | Сейчас | После |
|---|---|---|
| `src/trader/oddin_client.py` | Bitsler `breadcrumbs`, `getMatch`, `list_live_matches`, `BITSLER_BRAND_TOKEN`, `bitsler_client` | Только Disir. `widget_config` берёт токен из `ODDIN_BRAND_TOKEN`. `bitsler_client` переименовать в `disir_client` |
| `src/trader/oddin_catalog.py` — новый | — | Запросы `dota2TournamentInfo`, разбор турниров и матчей, скан ID |
| `src/trader/oddin_tournament_registry.py` — новый | — | Реестр Dota-турниров и граница скана на диске, атомарная запись |
| `src/trader/oddin_types.py` | `Tournament`, `MatchCard`, `Breadcrumbs`, `LiveMatch` в форме Bitsler | Frozen dataclass `OddinTournament` и `OddinMatch`. Состояние матча считается по трём полям из раздела 2 |
| `src/trader/oddin_discovery.py` | `list_oddin_live_matches` обходит Bitsler | Читает реестр и отдаёт открытые матчи активных турниров |
| `src/trader/discovery.py` | `Callable[[], Sequence[LiveMatch]]` | Тот же callable с `OddinMatch` |
| `src/trader/wallet_host.py` | передаёт `list_oddin_live_matches` | Запускает и останавливает фоновый скан турниров |
| `src/trader/oddin_live_feed.py` | после WS `complete` вызывает `fetch_match` у Bitsler | Читает `isClosed` матча из `dota2TournamentInfo.matches` |
| `src/trader/source_picker.py` | `widget_config` с токеном Bitsler | Без правок: токен приходит через `widget_config` |
| `scripts/watch_bitsler_live.py` | выбор матча через Bitsler | `scripts/watch_oddin_live.py`: выбор из каталога Disir или по прямому ID |
| `scripts/watch_bitsler_lol.py` | Bitsler `breadcrumbs` для LoL | Только прямой Oddin ID. Каталог LoL в эту работу не входит |

Затронутые тесты: `tests/test_trader_discovery.py`, `tests/trader_discovery_fixtures.py`, `tests/test_discovery_cadence.py`, `tests/test_oddin_feed.py`, `tests/test_oddin_reducer.py`, `tests/test_source_picker.py`, `tests/test_trader_match_lifecycle.py`, `tests/test_trader_wallet_host.py`, `tests/test_watch_bitsler_live.py`, `tests/test_watch_bitsler_lol.py`.

## 4. Каталог

### Скан турниров

1. Скан работает в фоне и не блокирует 30-секундный цикл discovery из `src/trader/cadence.py`.
2. Раз в час скан проверяет окно ID от `frontier - 200` до `frontier + 100`. `frontier` — последний ID с ответом не `Not found`.
3. Запас вниз нужен, пока не ясен пункт 2 из «Что ещё не проверено». Если ID с `Not found` никогда не становятся турнирами, запас вниз убрать.
4. Пауза между запросами скана — 0.15 с. Час работы — не больше 300 запросов.
5. Если скан упал, реестр остаётся прежним. Следующая попытка через 5 минут.
6. При каждом скане перечитываются и известные активные турниры: дата окончания может сдвинуться.
7. Реестр хранит Dota-турниры, у которых `endTimestamp` плюс 1 день ещё не наступил. Старые записи скан удаляет.

### Холодный старт

- Если реестра нет, первый скан идёт от константы `SEED_TOURNAMENT_ID = 14400`. Он занимает около двух минут. Всё это время каталог пуст.
- Реестр лежит в `TRADER_DIR / "oddin_tournaments.json"`. На хосте это `data/trader_live/oddin_tournaments.json` и `data/trader_paper/oddin_tournaments.json`. Новый volume не нужен.

### Матчи в цикле discovery

1. Discovery берёт из реестра турниры, у которых сейчас от `startTimestamp - 1 день` до `endTimestamp + 1 день`.
2. Для каждого такого турнира discovery читает `matches`: параллельно, timeout 5 с, как `probe_playing` сейчас.
3. Ответ по турниру живёт 60 с. За это время discovery повторно Disir не спрашивает.
4. Если запрос по турниру упал, discovery берёт последний удачный ответ и пишет в лог его возраст. Пустой список вместо рабочего не подставлять.
5. В сопоставление идут матчи с `isClosed = false`: и начавшиеся, и будущие.

Нагрузка: при 8 активных турнирах это около 480 запросов в час плюс скан. Нынешний обход Bitsler делает больше. Пока есть подходящие рынки, каждые 30 с он вызывает `breadcrumbs`, запрос на каждый турнир и `getMatch` на каждую карточку.

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

4. После WS `complete` фид читает свой матч из `dota2TournamentInfo.matches`. Для этого `OddinMatch` хранит ID турнира. Если `isClosed = true`, фид завершается. Иначе фид переподключается.
5. Сейчас `_NeedReconnect` в `oddin_live_feed.py` не расходует лимит `MAX_CONSECUTIVE_FAILURES`. Так цикл `complete` без данных может идти бесконечно. `complete` без единого тика должен расходовать одну попытку.
6. Timeout, пустой снимок или `Not found` в снимке не означают конец матча.
7. Расшифровку, модель без XP, формат архивов, выбор фида и размеры ставок не менять.

## 7. Порядок реализации

### Шаг A. Пробник и fixtures

1. Добавить `scripts/probe_disir_catalog.py`. Он сканирует окно ID, печатает активные Dota-турниры и открытые матчи.
2. Пробник проверяет WS и задержку с токеном Stake на одном-двух идущих матчах.
3. Пробник сохраняет очищенные ответы в `tests/fixtures/disir_tournament_info.json`. Токен в fixture не попадает.
4. Пока Bitsler отвечает, пробник раз в 30 минут сравнивает оба каталога в течение суток. Каждое расхождение он пишет в лог.
5. Через сутки повторить скан того же окна. Так ответим на вопрос про `Not found`.
6. Удалить `scripts/probe_stake_catalog.py` и `data/oddin_catalog/`. Нужные данные из них уже в архиве этой папки.

### Шаг B. Типы и клиент каталога

Добавить `OddinTournament`, `OddinMatch` и `oddin_catalog.py`. Тесты разбора: три состояния матча, base64-ID, пустые `matches`, `null` и `Not found` в ответе турнира, GraphQL `errors` при HTTP 200.

### Шаг C. Реестр и скан

Реализовать реестр и скан из раздела 4. Тесты с управляемыми часами и подменённым HTTP:

- холодный старт от `SEED_TOURNAMENT_ID`;
- движение `frontier`;
- `Not found` внутри окна;
- ошибка скана не портит реестр;
- атомарная запись;
- удаление законченных турниров.

### Шаг D. Discovery

Заменить `list_oddin_live_matches` и тип `LiveMatch`. Регрессионные тесты:

- скан не задерживает цикл discovery Dota и LoL;
- ответ по турниру живёт 60 с;
- упавший турнир отдаёт последний удачный список;
- одна пара команд в двух открытых матчах решается через `snapshot_is_playing`.

### Шаг E. Токен и переподключение

Изменить `widget_config`, `oddin_live_feed.py` и `compose.yaml`. Тесты: обрыв сокета, `complete` с открытым матчем, `complete` с закрытым матчем, `complete` без тиков расходует попытку, неверный токен.

### Шаг F. Скрипты и очистка

1. Переименовать `watch_bitsler_live.py` в `watch_oddin_live.py`.
2. Перевести `watch_bitsler_lol.py` на прямой Oddin ID.
3. Обновить импорты, docstrings и тесты watcher-скриптов.
4. В действующем коде не должно остаться сетевых обращений к `bitsler.com` и импортов Bitsler-функций. Старые логи и документы с Bitsler не трогать.

### Обязательные проверки

- Запускать Python через `uv run python`. Для тестов нужен `PYTHONPATH=src:scripts:../prediction-market-backtesting`, как в Makefile.
- Во время живой карты тесты не запускать. Запускать только затронутые файлы с `PYTEST_XDIST_AUTO_NUM_WORKERS=1`.
- ruff check, ruff format. basedpyright запускать как `.venv/bin/python -m basedpyright`: скрипт `.venv/bin/basedpyright` ссылается на несуществующий `/root/work/dota_2_model/.venv/bin/python`.
- Интеграционный тест запрещает HTTP и WS к доменам Bitsler и проходит путь: новый рынок → матч из Disir → выбор фида → переподключение.
- `poly-maker` не менять. `git push` не делать.

## 8. Переход на VPS

1. До перехода: пробник из шага A сутки сравнивает каталоги без расхождений, которые мы не понимаем.
2. Проверить одну живую карту: корректный net worth и задержка не больше 61 секунды с токеном Stake.
3. Переход только в безопасном окне:

   ```bash
   python3 /root/work/betting_workspace/.shared-skills/vps-trader/scripts/summarize.py --restart-check
   # restart_check UNSAFE → остановиться и ждать. SAFE → продолжать.
   cd /root/work/esports-trader
   # проверить, что в .env есть ODDIN_BRAND_TOKEN
   docker compose up -d --force-recreate live paper
   docker compose logs --since 5m live
   ```

4. Первый переход требует `up -d --force-recreate`: меняется `environment` в `compose.yaml`. `docker compose restart` переменную не применит. Дальнейшие правки только Python-кода — обычный `docker compose restart live`.
5. `compose down` не использовать. `--build` без смены зависимостей не делать.
6. После перехода проверить: реестр турниров на диске, открытые матчи в логе discovery, выбранный фид, архив тиков, переподключение и отсутствие запросов к Bitsler.

План отката: в безопасном окне вернуть `main` на SHA из шапки и выполнить `docker compose up -d --force-recreate live paper`. После закрытия Bitsler откат вернёт только режим без Oddin: матчи без Steam/GRID будут пропущены. Порог 61 секунда ради ставок не ослаблять.

## 9. Критерии готовности

- [ ] Каталог Disir находит идущие и будущие матчи Dota без Bitsler.
- [ ] Токен Stake работает для HTTP-снимка, WS и `probe_oddin_delay`.
- [ ] Найден новый турнир, которого не было в реестре при запуске.
- [ ] При недоступном Bitsler работают discovery, игровой поток и переподключение.
- [ ] Ошибка Disir и рестарт не стирают реестр и последние удачные списки матчей.
- [ ] Скан турниров не задерживает discovery Dota и LoL.
- [ ] Цикл `complete` без данных ограничен лимитом попыток.
- [ ] Логи различают ошибку скана, ошибку списка матчей, отсутствие матча, неоднозначную привязку, отсутствие данных и задержку выше порога.
- [ ] Дыры покрытия Oddin относительно Polymarket записаны в лог и описаны.

## 10. Риски

- Disir не документирован. Oddin может изменить схему или закрыть эти запросы. Запасные варианты описаны в `stake-brightdata-archive.md`.
- Токен принадлежит Stake. Если Stake сменит токен, нужен новый токен оператора.
- Смысл ответа `Not found` для ID турниров пока не ясен. Запас вниз в скане закрывает этот риск.
