# LoL historical data and model training design

Дата: 2026-08-27

Статус: утверждённый дизайн, реализация ещё не начата.

Ревизия 5.

## 1. Цель

Добавить в dota_2_model отдельный исследовательский pipeline для League of
Legends, который:

- максимально покрывает исторические LoL-рынки Polymarket без ручного rescue
  отдельных сотен матчей;
- собирает архив Gamma, lolesports livestats и Telonex order books;
- строит отдельные LoL train и validation datasets с теми же 12 model features,
  что и Dota;
- обучается и валидируется на посекундных строках, а production fit использует
  ту же плотность;
- обучает отдельные research и production LightGBM-модели LoL;
- не меняет существующее поведение Dota.

LoL и Dota никогда не смешиваются в одном датасете или одной модели.

## 2. Scope

В scope входят:

1. Universe исторических LoL-рынков Polymarket, включая резолюцию.
2. Архив book_snapshot_full из Telonex.
3. Матчинг Polymarket series к сериям lolesports и загрузка сырых livestats
   windows.
4. Построение посекундных train, validation и production datasets.
5. Research fit, validation metrics и production fit.

Не входят:

- backtest;
- live и live-paper;
- изменение polymarket-collector на VPS;
- торговая логика и исполнение;
- прямые champion/draft features;
- Leaguepedia, Cargo, Riot V5 и V4 как зависимости пайплайна;
- интерполяция и forward-fill игровых features между настоящими кадрами;
- субсекундные rows;
- rows дальше second 540, хотя windows качаются до second 1200;
- общий provider framework;
- trades и onchain fills Telonex;
- ручные per-match overrides и сложный rescue ради небольшого остатка матчей.

Будущий collector LoL сможет продолжить тот же локальный формат книг, но его
изменения проектируются отдельно.

## 3. Pipeline и каталоги

Весь LoL-specific код находится в src/lol:

    src/lol/
    ├── 01_build_universe.py
    ├── 02_fetch_telonex_books.py
    ├── 03_link_lolesports.py
    ├── 04_fetch_lolesports.py
    ├── 05_prepare_dataset.py
    └── 06_train_model.py

Общие, реально переиспользуемые функции остаются в src/shared или в текущем
train_model module. Одноразовые helpers остаются рядом со своим stage; заранее
строить interfaces, factories или provider abstractions не нужно.

Все LoL-данные изолированы:

    data/lol/
    ├── raw/
    ├── processed/
    └── models/

Полный canonical порядок stages после решения GO:

    01_build_universe
        -> 02_fetch_telonex_books
        -> 03_link_lolesports
        -> 04_fetch_lolesports
        -> 05_prepare_dataset
        -> 06_train_model

Первый implementation и real-data run имеют жёсткую границу:

1. До решения GO реализуются только 01, 03 и 04. Файлов 02, 05 и 06 ещё нет.
2. Бесплатные источники полностью запускаются в порядке 01 -> 03 -> 04. Человек
   проверяет coverage и audits universe, linking и livestats download, после
   чего работа останавливается для ручного решения GO/NO-GO.
3. Только после явного GO пишутся 02, 05 и 06. Downloader 02 сначала полностью
   тестируется на fixtures; месяц Telonex покупается, когда он готов сразу
   начать реальную загрузку. Затем выполняются 02 -> 05 -> 06.

Отдельного pre-Telonex prepare, частичного датасета или fixture-only trainer
нет. До GO проверяются полнота и целостность бесплатных источников, но точное
число пригодных ML rows становится известно только после полного Stage 05.

После этого Telonex остаётся вторым canonical stage. Ограниченная по времени
подписка не должна зависеть от качества будущих повторных matching runs.

Архив livestats не протухает: окна открываются минимум на 318 дней назад
(раздел 14.1). Поэтому линк идёт до загрузки, и качаются только те карты,
которые реально нужны.

## 4. Stage 01: Polymarket universe

01_build_universe.py загружает полный open и closed архив Gamma по LoL tag 65 и
кэширует исходные страницы в data/lol/raw/polymarket/gamma.

Один markets.parquet хранит по одному рынку:

- Polymarket event ID и slug;
- condition ID;
- question, group item title и sports market type;
- outcomes и CLOB token IDs в исходном порядке;
- PM team names;
- game number;
- BO;
- league metadata;
- scheduled time;
- market start/end timestamps;
- resolved outcome;
- contract classification;
- inclusion/exclusion reason.

Выход:

    data/lol/processed/universe/markets.parquet

### 4.1 Market contracts

Поддерживаются:

- явный Game N Winner;
- Match Winner только как допустимый decider fallback.

Правила fallback:

- BO1: Match Winner может представлять Game 1;
- BO3: Match Winner может представлять только реально сыгранную Game 3;
- BO5: Match Winner может представлять только реально сыгранную Game 5;
- перед decider счёт серии должен быть равным;
- явный Game N Winner всегда приоритетнее Match Winner;
- BO2 не поддерживается;
- series-only BO3/BO5 events без единого явного Game N market исключаются, как
  в Dota pipeline.

Рынок обязан иметь ровно два различных непустых token IDs и два outcomes.
Unsupported и malformed markets сохраняются в universe с причиной исключения.

### 4.2 Резолюция

Победитель карты берётся из резолюции самого рынка Polymarket. Это единственный
источник `radiant_win`, и он же — та истина, по которой платит рынок.

Резолюция читается из Gamma: у закрытого рынка ровно один outcome имеет цену 1,
второй 0. Любой другой вариант — обе цены не 0 и не 1, открытый рынок,
незавершённый UMA — исключает карту с причиной `unresolved_market`.

Для Match Winner decider fallback резолюция серии совпадает с победителем этой
карты по построению: decider — последняя сыгранная карта серии.

Leaguepedia `WinTeam` как перекрёстная сверка не используется: это вернуло бы
Cargo в зависимости. Такую сверку можно провести разово, офлайн, вне пайплайна.

## 5. Stage 02: Telonex archive

Stage 02 относится ко второй фазе реализации. До ручного GO по audits Stages
01, 03 и 04 файл `02_fetch_telonex_books.py` не создаётся. После GO downloader
сначала реализуется и полностью тестируется на fixtures; платная подписка
оформляется только когда код готов сразу начать реальную загрузку.

02_fetch_telonex_books.py использует TELONEX_API_KEY через Bearer authorization.
Ключ читается из environment и никогда не записывается в repo или artifacts.

Stage читает поддержанные markets из universe и загружает Telonex catalog:

    GET /v1/datasets/polymarket/markets

Полный catalog читается потоково. Сохраняется только срез нужных condition IDs;
большой временный полный parquet удаляется после успешного slice. Для каждого
рынка проверяется совпадение двух token IDs Gamma и Telonex.

Catalog задаёт точные inclusive/exclusive интервалы:

- book_snapshot_full_from;
- book_snapshot_full_to.

Для обоих токенов скачивается каждый доступный UTC day:

    GET /v1/downloads/polymarket/book_snapshot_full/YYYY-MM-DD
        ?asset_id=<token_id>

Скачиваются все catalog dates всех подходящих LoL winner markets, даже если
последующий matching их не свяжет.

Исходные provider parquets остаются без преобразования:

    data/lol/raw/telonex/polymarket/
    └── book_snapshot_full/
        └── asset_id=<token_id>/
            └── YYYY-MM-DD.parquet

Processed control artifacts:

    data/lol/processed/telonex/
    ├── catalog.parquet
    └── download_audit.parquet

Downloader возобновляем без SQLite:

- существующий parquet пропускается только после проверки footer, обязательных
  columns и asset_id;
- новый файл пишется в .partial, валидируется и атомарно заменяет target;
- network errors, HTTP 429 и 5xx повторяются с bounded backoff;
- HTTP 401/403 немедленно останавливают run;
- HTTP 404 и исчерпанные retries записываются в audit, остальные jobs
  продолжаются;
- incomplete run завершается ненулевым exit code и безопасно перезапускается.

API-механика переиспользуется из scripts/compare_collector_to_telonex.py.
Удалённый из main большой fetch_telonex_dota_winners.py используется только как
источник уже проверенных catalog fields и endpoint semantics. Его SQLite
planner, три channels, quarantine и rescue machinery не восстанавливаются.

## 6. Stage 03: lolesports link

03_link_lolesports.py перечисляет серии lolesports и связывает их с events
Polymarket.

### 6.1 Источник

Перечисление идёт через esports-api с публичным ключом фронта lolesports.com:

    GET https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US
        [&pageToken=<pages.older>]
    GET https://esports-api.lolesports.com/persisted/gw/getEventDetails
        ?hl=en-US&id=<matchId>
    header x-api-key: 0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z

Ключ вшит в JS сайта и публичен. Он хранится константой в
src/shared/constants/lol.py, а не в environment. Смена ключа проявится как
HTTP 403 и остановит run.

Кадры состояния идут с отдельного фида, ключа он не требует:

    GET https://feed.lolesports.com/livestats/v1/window/<esportsGameId>
    GET https://feed.lolesports.com/livestats/v1/window/<esportsGameId>
        ?startingTime=YYYY-MM-DDTHH:MM:SS.000Z

`startingTime` кратен десяти секундам, окно покрывает [T, T+10s]. Запрос без
`startingTime` возвращает первые десять кадров игры, включая старые матчи
(раздел 14.3).

Endpoint `details` не используется: `window.participants[].totalGold` побайтово
равен `details.participants[].totalGoldEarned` (раздел 14.2), а остальные поля
`details` модели не нужны.

### 6.2 Перечисление

Stage листает getSchedule по курсору `pages.older` до начала диапазона Gamma
universe и кэширует страницы:

    data/lol/raw/lolesports/schedule/

Для каждого event с `type == "match"` и `state == "completed"` загружается
getEventDetails и кэшируется:

    data/lol/raw/lolesports/events/

`data.event.match` даёт `strategy.count` (BO), `teams[]` с `id`, `name`, `code`
и `result.gameWins`, и `games[]` с `number`, `id`, `state` и `teams[].side`
(blue/red) с `esportsTeamId`.

Per-map победителя в getEventDetails нет: `games[]` содержит только `number`,
`id`, `state`, `teams`, `vods`. Победитель берётся из резолюции рынка
(раздел 4.2).

Для каждой game с `state == "completed"` делается один запрос window без
`startingTime`. Минимальный `rfc460Timestamp` этих кадров — loading anchor,
с которого начинается загрузка окон. Ответ кэшируется:

    data/lol/raw/lolesports/anchors/<esportsGameId>.json

Выход перечисления:

    data/lol/processed/lolesports/games.parquet

Колонки: esportsGameId, esportsMatchId, league slug, scheduled start, BO, map
number, названия и коды команд, blue esportsTeamId, red esportsTeamId, loading
anchor, patch version.

### 6.3 Series matching

Polymarket event и серия lolesports должны пройти все применимые проверки:

1. Обе команды совпадают с учётом возможного swap сторон.
2. Средний team-name score не ниже 0.82.
3. Score каждой отдельной команды не ниже 0.72.
4. Разница PM scheduled time и `startTime` серии не больше 4 часов.
5. Если BO известно с обеих сторон, оно совпадает.
6. Если league с обеих сторон нормализуется в известное canonical значение,
   значения совпадают.

Если BO или league неизвестны с одной стороны, это не уменьшает coverage.

Матчер, нормализация имён и alias map переиспользуются из Dota-версии. Alias map
для LoL небольшой и постоянный, per-match overrides не допускаются.

Если проходят несколько кандидатов lolesports, event считается ambiguous:
ближайший по времени автоматически не выбирается.

Если несколько PM events претендуют на одну серию:

- выигрывает event с большим числом явных Game N markets;
- при равенстве все претенденты остаются ambiguous.

После series match каждой сыгранной карте назначается market:

1. явный Game N Winner по `games[].number`;
2. иначе разрешённый Match Winner decider fallback из раздела 4.1;
3. иначе карта остаётся без market.

### 6.4 Orientation

Ориентация PM outcomes к сторонам карты выводится так: имя команды из outcome
сопоставляется с именем команды lolesports тем же матчером, полученный
`esportsTeamId` ищется в `games[].teams[].side`.

Внутренний shared schema сохраняет Dota names:

- Radiant = Blue;
- Dire = Red;
- market_p_radiant = P(Blue);
- radiant_win = Blue won.

Неоднозначная ориентация исключает карту.

Выход:

    data/lol/processed/lolesports_links/
    ├── links.parquet
    └── audit.parquet

links.parquet содержит одну строку на принятую сыгранную карту: PM event ID,
condition ID, token IDs, esportsGameId, esportsMatchId, map number, loading
anchor, ориентация, резолюция. audit.parquet сохраняет unmatched, ambiguous,
league mismatch, unsupported fallback, unresolved_market и другие стабильные
причины.

## 7. Stage 04: lolesports windows

04_fetch_lolesports.py качает сырые окна для каждой принятой карты, и для train,
и для validation.

### 7.1 План запросов

Загрузка идёт от loading anchor, округлённого вниз до границы десяти секунд, с
шагом десять секунд.

Цикл управляется **игровым временем, а не числом окон**. Он останавливается,
когда самый свежий кадр достигает игровой секунды LOL_FETCH_END_SECOND (1200),
либо когда пройденная настенная длина превышает LOL_FETCH_MAX_WALL_SECONDS
(3600). Второй предел — предохранитель, а не рабочий режим.

Фиксированное число окон здесь неверно. Пауза встречается в 63% карт, самая
длинная в выборке — 498.9 секунды, и паузы часто начинаются в первые полминуты
(раздел 14.8). Карта с паузой 499 секунд на девятой секунде растягивает окно
0..1200 по стене почти до получаса.

Rows датасета доходят только до second 540. Окна до 1200 качаются, чтобы
расширить окно модели позже без повторной загрузки.

### 7.2 Хранение

    data/lol/raw/lolesports/windows/<esportsGameId>.jsonl.gz

Одна строка — один сырой ответ window, в порядке запросов. Payload не
преобразуется. Дедуп кадров по `rfc460Timestamp` делает читатель в prepare.

Формат выбран из-за объёма: gzip по проводу 2.24 КБ на окно против 68 КБ
распакованного JSON, и один файл на карту вместо десятков тысяч мелких файлов
(раздел 14.6).

### 7.3 Возобновляемость и ошибки

Политика та же, что у Telonex stage 02:

- существующий файл читается, берётся последний кадр, докачка идёт с него;
- новый файл пишется в .partial, проверяется и атомарно заменяет target;
- network errors, HTTP 429 и 5xx повторяются с bounded backoff;
- HTTP 401/403 немедленно останавливают run;
- HTTP 404, пустое тело и исчерпанные retries записываются в audit, остальные
  jobs продолжаются;
- incomplete run завершается ненулевым exit code и безопасно перезапускается.

LOL_MAX_CONCURRENCY равен 8. На 16 одновременных запросах измерено 30 запросов
в секунду и ни одного 429 примерно на 300 запросах, поэтому 8 — запас вдвое
(раздел 14.6).

Пустое тело при HTTP 200 встречается на окнах вне игры и обрабатывается как
отсутствие кадров, а не как ошибка парсинга.

Control artifact:

    data/lol/processed/lolesports/download_audit.parquet

Audit содержит по одной строке на карту: число сохранённых window responses и
уникальных кадров, максимальную достигнутую игровую секунду, complete flag и
стабильную причину ошибки. Вместе с universe и linking audits он служит
основанием ручного GO/NO-GO до покупки Telonex.

## 8. LoL clock и паузы

Оба факта этого раздела измерены против V5 и держатся на нём, но сам V5 в
пайплайне не участвует.

### 8.1 Clock zero

LoL game clock начинается с 0:00. Draft и loading происходят до clock zero.

Clock zero — это первый кадр, у которого хотя бы у одного игрока `totalGold`
больше нуля:

    spawn_frame     = первый кадр с ненулевым totalGold
    game_start_wall = spawn_frame.rfc460Timestamp

На 31 карте этот кадр отличается от V5 `gameStartTimestamp` не более чем на
**4 миллисекунды**, медиана 3 мс (раздел 14.4). На всех 31 у каждого из десяти
игроков на этом кадре ровно 500 золота — это стартовое золото LoL и обязательный
инвариант.

Loading anchor — первый кадр игры вообще — опережает clock zero на 2.54..5.52
секунды. Он служит только точкой старта загрузки и якорем не является.

Если кадра спавна в первых 90 секундах после anchor нет, карта исключается с
причиной `no_spawn_frame`.

### 8.2 Паузы

Во время паузы фид не присылает кадров вообще. Пауза — это разрыв между двумя
соседними кадрами после clock zero длиннее LOL_PAUSE_MIN_GAP_SECONDS (5 секунд):

    pause_start = timestamp последнего кадра перед разрывом
    pause_end   = timestamp первого кадра после разрыва
    duration    = pause_end - pause_start

На 20 картах обе границы совпали с `PAUSE_START.realTimestamp` и
`PAUSE_END.realTimestamp` из V5 **с точностью 0 мс** (раздел 14.7). Причина в
том, что оба источника читают одни и те же часы игрового сервера.

Порог 5 секунд обоснован с обеих сторон:

- обычный разрыв между кадрами: максимум 1.87 секунды на 103 116 разрывах,
  p99 равен 1.00 секунды;
- самая короткая пауза в выборке из 30 пауз — 13.8 секунды.

Отсчёт разрывов обязан начинаться с кадра спавна. Разрыв loading -> спавн
составляет 2.54..5.52 секунды и при отсчёте от anchor даёт ложную паузу.

Отдельного выброса кадров внутри паузы не нужно: их не существует.

Известный потолок: настоящий обрыв фида длиннее пяти секунд неотличим от паузы
и сдвинет `second` у более поздних строк. На 103 116 измеренных разрывах такого
не встретилось.

### 8.3 Игровое время кадра

    game_time = frame_wall
              - game_start_wall
              - sum(durations пауз, полностью завершившихся раньше frame_wall)

Обратное направление в датасете не нужно: рынок join'ится по настенному времени
кадра напрямую.

## 9. Stage 05: Dataset preparation

05_prepare_dataset.py соединяет links, сырые окна livestats и книги Telonex.

Stage имеет один full режим и реализуется только после решения GO. Он требует
локальные catalog и books Stage 02; отсутствие обязательных inputs завершает
run до публикации outputs.

Full prepare читает links и livestats windows, выполняет clock, pause и
invariant checks, строит игровые rows, делает market join и атомарно публикует
datasets, split и audit. Отдельного partial/preflight режима и постоянного
`game_states.parquet` нет.

### 9.1 Чтение кадров

Кадры карты читаются из `.jsonl.gz`, дедуплицируются по `rfc460Timestamp` и
сортируются. Дальше вычисляются clock zero и паузы по разделу 8, и каждому кадру
присваивается игровое время.

### 9.2 Сетки строк

Train, validation и production используют одну сетку:

    second = 0, 1, 2, ..., 540

Для секунды S берётся последний кадр с игровым временем не позже S и возрастом
не больше LOL_FRAME_MAX_AGE_SECONDS (2 секунды). Нет такого кадра —
выбрасывается одна строка. Порог 2 секунды выбран по измеренному максимальному
не-паузному разрыву 1.87 секунды.

В `second` записывается S. В `state_wall_time` — фактический `rfc460Timestamp`
выбранного кадра.

Полная карта даёт до 541 строки, в 54.1 раза больше прежнего минутного дизайна.
Окно у всех карт фиксировано на 0..540, поэтому длинные карты не получают
дополнительный вес. Число usable rows после market join может различаться из-за
пропусков books; v1 не добавляет per-map weights. Их рассматривают только если
первый validation покажет реальный перекос или overfit.

Переключателя между 1 и 60 секундами нет. Плотность 1 Hz — часть LoL model
contract, а не runtime option.

Субсекундных строк нет. Выбор «последний кадр не позже S» даёт точное состояние
на секунду S, а промежуточные кадры почти дубликаты соседних: измерено 3.81
кадра в секунду при 1.07 модельно значимых изменения состояния в секунду
(раздел 14.9).

### 9.3 Уровни и XP

`radiant_xp_adv` считается из уровней. Настоящего `xp` в livestats нет, и он не
нужен.

Так же устроена Dota: `src/shared/utils/dota_levels.py` считает XP из level и в
train, и в validation, и в live, а `radiantExperienceLeads` из STRATZ не
используется.

Таблица порогов:

    # Cumulative XP to be at each level, 1..18. Derived once, offline, from 24
    # Leaguepedia V5 timelines (7180 participant frames): every value falls
    # inside the observed (max xp at level-1, min xp at level] bracket.
    LOL_LEVEL_XP: tuple[int, ...] = (
        0, 280, 660, 1140, 1720, 2400, 3180, 4060, 5040,
        6120, 7300, 8580, 9960, 11440, 13020, 14700, 16480, 18360,
    )

Живёт в src/shared/constants/lol.py. Скобки, из которых она выведена, — в
разделе 14.10. Тест сверяет константу с закоммиченной фикстурой V5, сети не
требует.

Level cap в текущем патче выше 18: 7 из 24 проверенных игр доходят до уровня 19
или 20. Пороги ниже 18 при этом не менялись, а в первые десять минут в выборке
встречаются только уровни 1..9. Окно модели cap не задевает. `cumulative_xp`
падает на уровне выше таблицы; ветки под cap не будет, потому что в rows такой
уровень невозможен.

Арифметика выносится в src/shared/utils/level_xp.py:

    cumulative_xp(level_xp: tuple[int, ...], level: int) -> int
    xp_advantage(level_xp, first_levels, second_levels) -> int

`dota_levels.py` сохраняет свои сигнатуры и делегирует туда с `LEVEL_XP`. Ни
один вызов в Dota не меняется. LoL вызывает те же функции с `LOL_LEVEL_XP`.

### 9.4 Features

Все фичи берутся из одного кадра livestats:

    radiant_nw_adv        = blueTeam.totalGold - redTeam.totalGold
    radiant_nw            = blueTeam.totalGold
    dire_nw               = redTeam.totalGold
    radiant_xp_adv        = xp_advantage(LOL_LEVEL_XP, blue levels, red levels)
    deaths_radiant        = sum(blue participants[].deaths)
    deaths_dire           = sum(red participants[].deaths)
    top1_nw_adv           = max(blue gold) - max(red gold)
    radiant_top1_nw_ratio = max(blue gold) / radiant_nw
    dire_top1_nw_ratio    = max(red gold) / dire_nw

`totalGold` — заработанное золото, не тратимое (раздел 14.2). Состав сторон
берётся из `blueTeam` и `redTeam` кадра, гадать по диапазонам participantId не
нужно.

### 9.5 Market prior

market_radiant_prior оценивает рынок после draft, прямо перед началом clock:

- окно равно [game_start_wall - 61 seconds, game_start_wall);
- anchor строгий: snapshot в game_start_wall не допускается;
- выбирается самый поздний валидный paired midpoint внутри окна;
- обе token books two-sided;
- обе стороны не старше 61 секунды относительно anchor;
- сумма midpoint должна находиться в 1 ± 0.05;
- итог равен blue_mid / (blue_mid + red_mid).

Фолбека старше 61 секунды нет. Missing prior исключает всю карту.

### 9.6 Инварианты livestats

Это замена перекрёстной сверке с V5. Проверяются все кадры карты в пределах
скачанного диапазона:

1. на кадре спавна у каждого из десяти игроков ровно 500 золота;
2. `totalGold` каждого игрока не убывает;
3. `team.totalGold` равен сумме по пяти игрокам;
4. `team.totalKills` равен сумме `deaths` игроков другой команды;
5. `level` каждого игрока не убывает и не меньше 1;
6. `deaths` каждого игрока не убывают;
7. в кадре ровно пять игроков в `blueTeam` и пять в `redTeam`.

Пункты 3 и 4 проверены на 746 кадрах без единого нарушения, пункт 1 — на 31
карте (раздел 14.11).

Разрыв между кадрами инвариантом не является. Разрыв больше
LOL_PAUSE_MIN_GAP_SECONDS — это пауза. Разрыв между LOL_FRAME_MAX_AGE_SECONDS и
порогом паузы стоит отдельных строк через гейт возраста раздела 9.2 и
записывается в audit счётчиком, но карту не исключает. Измеренный максимум
не-паузного разрыва — 1.87 секунды, так что эта зона на практике пуста.

Пункт 2 — главный сторож семантики. Если `totalGold` когда-нибудь станет
тратимым золотом, монотонность сломается на первой же покупке предмета, на
каждой карте.

Нарушение инварианта исключает карту с причиной `livestats_invariant_violation`.
Доля исключённых печатается. Больше MAX_LIVESTATS_INVARIANT_MAPS (5 карт) — run
падает: это означает смену семантики поля, а не пропуск данных.

### 9.7 Current midpoint и label

LOL_SOURCE_LAG_SECONDS фиксирован в v1 на 25 секундах. Это исследовательская
оценка задержки публичного LoL state feed, а не Riot SLA; значение явно
записывается в model metadata.

Для каждого frame:

    current_market_time = state_wall_time + 25 seconds
    target_market_time  = current_market_time + 300 seconds

85 ms execution/network latency Dota здесь не применяется: dataset моделирует
наблюдаемый market midpoint, а не fill.

Current и target используют существующую Telonex as-of семантику:

- только последний snapshot не позже target timestamp;
- обе книги two-sided;
- snapshot age не больше 5 секунд;
- midpoint pair в 1 ± 0.05;
- P(Blue) нормализуется по двум токенам.

Плохой current или target удаляет только эту row. Missing prior, no_spawn_frame,
unresolved_market, нарушенный инвариант, неоднозначная ориентация или ноль
usable rows удаляют всю карту.

Target column:

    signal_market_p_radiant_300s

Training label:

    signal_market_p_radiant_300s - market_p_radiant

### 9.8 Dataset schema

training.parquet, validation.parquet и production_training.parquet содержат
ровно shared DatasetRow columns:

    match_id
    start_time
    second
    radiant_win
    radiant_nw_adv
    radiant_nw
    dire_nw
    radiant_xp_adv
    deaths_radiant
    deaths_dire
    top1_nw_adv
    radiant_top1_nw_ratio
    dire_top1_nw_ratio
    market_radiant_prior
    market_p_radiant
    signal_market_p_radiant_300s

Схема одна на все три файла и совпадает с Dota. Все datasets имеют одинаковую
плотность: до 541 посекундной строки на карту.

match_id равен `int(esportsGameId)`. Хешировать нечего: id lolesports —
восемнадцатизначное десятичное число, оно положительно и помещается в 63 бита.
Коллизии невозможны по построению, и id остаётся читаемым в логах. Prepare
проверяет уникальность.

start_time равен floor(game_start_wall / 1000).

Выход:

    data/lol/processed/datasets/
    ├── training.parquet
    ├── validation.parquet
    ├── production_training.parquet
    ├── split.parquet
    └── audit.parquet

training.parquet и validation.parquet идут в research fit.
production_training.parquet — посекундные строки всех принятых карт, включая
validation-карты; его читает production fit (раздел 10.4).

Dataset parquets не получают LoL-only metadata columns. split/audit сохраняют
PM event ID, esportsGameId, teams, map number, source timestamps, row counts,
паузы и exclusion reasons.

### 9.9 Split

Используется существующий точный Dota cutoff:

    VALIDATION_START_TIME = 1780563592
    2026-06-04T08:59:52Z

Split driver — start_time первой сыгранной карты PM event:

- event before cutoff целиком относится к train;
- event at or after cutoff целиком относится к validation.

Все карты одной PM series всегда находятся в одном split. Дубликаты PM events
уже разрешены или исключены matching stage.

## 10. Stage 06: Model training

LoL обучает отдельную модель через src/lol/06_train_model.py.

LightGBM fit не копируется. Текущий src/train_model/train_model.py предоставляет
общие вызываемые функции для:

- feature selection;
- price-delta labels;
- research fit с early stopping;
- fixed-tree production fit;
- clipped future-price prediction;
- model metadata и atomic publication.

Текущий Dota entrypoint остаётся default и сохраняет существующие paths,
wide ValidationDatasetRow, 10-second lag и validation_scenarios.csv.

### 10.1 Shared model contract

Обе игры используют те же 12 feature names и порядок:

    second
    radiant_nw_adv
    radiant_nw
    dire_nw
    radiant_xp_adv
    deaths_radiant
    deaths_dire
    top1_nw_adv
    radiant_top1_nw_ratio
    dire_top1_nw_ratio
    market_radiant_prior
    market_p_radiant

Начальные LightGBM параметры совпадают с Dota:

    objective          = regression_l1
    metric             = l1
    learning_rate      = 0.05
    num_leaves         = 63
    min_data_in_leaf   = 200
    max_boost_rounds   = 3000
    early_stopping     = 100

Отдельного hyperparameter search framework нет. Его добавляют только после
первого validation результата, если baseline показывает конкретную проблему.
Начальный baseline не использует веса строк и не масштабирует
`min_data_in_leaf` наугад из-за более плотной сетки.

### 10.2 Research

Research fit читает training.parquet и validation.parquet.

LoL validation содержит state second и midpoint на state + 25 seconds, поэтому
validation features используются напрямую. Dota-only lagged_source_features к
LoL не применяется.

model.json сохраняет:

- те же feature names;
- source_lag_seconds = 25;
- train_grid_seconds = 1;
- xp_source = "level";
- state_source = "lolesports_livestats";
- dataset hashes;
- число train maps и validation maps;
- selected tree count;
- holdout metrics.

LoL validation позволяет посчитать:

- no-move MAE at 300 seconds;
- model MAE at 300 seconds;
- MAE gain;
- model bias;
- directional 300-second markout;
- 95% bootstrap confidence intervals.

Bootstrap resamples целые PM events через match_id -> event_id mapping из
split.parquet. Посекундные rows внутри карты автокоррелированы и независимой
информации не добавляют, поэтому ресемплится event, а не строка.
validation_metrics.csv содержит full-window и per-minute агрегаты; секунды
складываются в минутные корзины. Execution, VWAP и остальные markout horizons не
синтезируются.

### 10.3 Измерение лага источника

scripts/measure_lol_source_lag.py считает кросс-корреляцию между скачками
`deaths` в кадрах livestats и движениями midpoint из книг Telonex, и печатает
argmax по кандидатам лага.

Скрипт не входит в pipeline и ничего не меняет. Если измеренный лаг расходится с
25 секундами больше чем на 5 секунд, константа правится и датасеты
пересобираются. Измеренное значение попадает в model.json.

### 10.4 Production

Production run:

    uv run python src/lol/06_train_model.py --production

Он:

1. требует существующий research model;
2. читает selected tree count из research model.json;
3. читает production_training.parquet;
4. обучает ровно это число trees без holdout;
5. публикует отдельный LoL production model.

production_training.parquet — посекундные rows всех принятых карт, и train, и
validation. Дополнительного взвешивания rows нет.

Так устроена Dota: `train_production` читает отдельный
PRODUCTION_TRAINING_DATASET_PATH. LoL сохраняет тот же вызов, но подаёт свой
посекундный parquet.

Production dataset fingerprint — sha256 самого production_training.parquet.

Артефакты:

    data/lol/models/
    ├── research/
    │   ├── model.txt
    │   ├── model.json
    │   ├── split.parquet
    │   └── validation_metrics.csv
    ├── production/
    │   ├── model.txt
    │   ├── model.json
    │   └── split.parquet
    └── archive/

Первая publication создаёт live model directory; последующие атомарно
архивируют предыдущую пару model.txt/model.json, как Dota.

## 11. Audit и failure policy

Coverage измеряется на каждом пересечении:

    Gamma supported market
        ∩ resolved market
        ∩ accepted lolesports series/map
        ∩ usable livestats windows
        ∩ Telonex prior/current/target books

Каждый stage печатает totals и сохраняет стабильные причины потерь. Не должно
быть silent drops.

До покупки Telonex ручной checkpoint читает outputs Stages 01, 03 и 04:
universe inclusion reasons, linking coverage, download completeness и
целостность raw windows. Он не запускает Stage 05 и не заявляет точное число
пригодных ML rows. После загрузки books Stage 05 пишет итоговый audit с clock,
pause, invariant и market coverage. Отсутствующие обязательные books завершают
Stage 05 до публикации datasets, поэтому Stage 06 нельзя случайно запустить как
успешное обучение без Telonex.

Fatal errors:

- invalid credentials;
- HTTP 403 от esports-api: публичный ключ фронта сменился;
- изменение top-level Telonex catalog, Gamma или livestats schema, из-за
  которого stage не может безопасно классифицировать rows;
- duplicate primary keys;
- доля карт с livestats_invariant_violation выше MAX_LIVESTATS_INVARIANT_MAPS;
- невозможность атомарно опубликовать output.

Ожидаемые coverage misses записываются в audit и не останавливают весь run:

- unmatched или ambiguous series;
- unsupported market;
- unresolved_market;
- no_livestats: у карты нет ни одного кадра;
- no_spawn_frame;
- неоднозначная ориентация;
- отдельное пропущенное окно livestats;
- missing Telonex asset/day;
- missing prior;
- stale/inconsistent current или target row.

Все processed parquet outputs пишутся через temporary sibling и atomic replace.
Raw caches никогда не перезаписываются частичным response.

## 12. Verification

Тесты сети не требуют. Фикстуры — маленькие обрезанные JSON, закоммиченные в
репозиторий.

Роль V5 здесь одна: он использован **один раз, офлайн**, чтобы записать в тесты
эталонные значения — clock zero, границы пауз и таблицу уровней. Дальше тесты
ловят регрессии нашего кода, а не изменения живого фида. За живой фид отвечают
инварианты раздела 9.6, которые гоняются на каждом прогоне.

Фикстуры:

- кадры livestats карты 115548681803406125 вокруг clock zero и вокруг паузы
  16:20:14.058Z..16:22:48.045Z, плюс эталон из V5 LOLTMNT05_220139:
  gameStartTimestamp 1787501889880 и пауза 153.987 секунды;
- 24 V5 timeline в урезанном виде — только пары (level, xp) — для теста
  таблицы LOL_LEVEL_XP;
- маленькие срезы Gamma, getSchedule, getEventDetails и Telonex.

Минимальные обязательные tests:

1. Universe classification: Game N, BO1/BO3/BO5 decider fallback, BO2 и
   series-only exclusion.
2. Резолюция: ровно один outcome с ценой 1, любой другой случай исключает карту.
3. Matching: swap teams, aliases, thresholds, ±4 hours, conditional league
   guard, ambiguity и duplicate PM event rule.
4. Orientation: outcome -> имя команды -> esportsTeamId -> side, неоднозначность
   исключает карту.
5. Clock zero: кадр спавна найден, все десять игроков по 500 золота, отсутствие
   кадра спавна исключает карту.
6. Паузы: разрыв больше 5 секунд после спавна опознан как пауза с точными
   границами; разрыв loading -> спавн паузой не считается; игровое время после
   паузы сдвинуто на её длительность.
7. Level XP table: вывод скобок из фикстуры и проверка, что каждое значение
   LOL_LEVEL_XP лежит внутри своей скобки; уровень выше таблицы поднимает
   ошибку.
8. level_xp module: dota_levels сохраняет прежние результаты после делегирования;
   xp_advantage на LOL_LEVEL_XP считает ожидаемую разницу.
9. Fetch: цикл идёт по игровому времени и доходит до second 1200 через паузу;
   предохранитель по настенному времени срабатывает; докачка с последнего кадра;
   пустое тело при HTTP 200 не ломает парсер; 401/403 останавливают run.
10. Prepare и сетка: единственный full path требует локальные catalog и books;
    отсутствие inputs не создаёт пустых outputs; train, validation и production
    используют `0..540` с шагом 1, правило «последний кадр не позже S» и гейт
    возраста 2 секунды.
11. Features: gold, level-derived XP, deaths, top1 ratios из одного кадра.
12. Инварианты: каждый из семи пунктов раздела 9.6 отдельно; порог падения
    run.
13. Market timestamps: actual frame timestamp, +25 seconds и +300 seconds.
14. Prior: strict anchor, 61-second boundary, two-sided and pair gates.
15. Current/target: 5-second boundary, no future reads и row-only drops.
16. Split: целая PM event series не пересекает cutoff.
17. Dataset schema: все три LoL dataset files в точности соответствуют
    DatasetRow; match_id равен int(esportsGameId) и уникален.
18. Trainer: feature order, direct LoL validation seconds, source lag 25,
    `train_grid_seconds = 1`, research tree reuse и production fit на
    production_training.parquet.
19. Dota regression: существующие prepare/train tests и default CLI проходят
    без изменения артефактов и lag semantics.

После Python edits обязательны targeted pytest, полный релевантный suite и
make lint-all.

## 13. Success criteria

Работа считается завершённой, когда:

- pipeline полностью перезапускается из raw caches;
- реальный запуск 01 -> 03 -> 04 собирает все доступные бесплатные данные,
  публикует universe/link/download audits и останавливает работу перед ручным
  GO/NO-GO;
- Stages 02, 05 и 06 не реализуются до GO; partial prepare и отдельного
  pre-Telonex trainer нет;
- платная подписка оформляется только после готовности downloader 02;
- Telonex и lolesports downloaders возобновляются после interruption;
- coverage и причины каждого drop воспроизводимы;
- LoL train, validation и production datasets имеют сетку 1 Hz, а train и
  validation — ровно DatasetRow schema и только настоящие кадры livestats;
- radiant_xp_adv во всех трёх dataset files считается из уровней по одной
  таблице;
- инварианты livestats проходят на всех принятых картах;
- ни одна PM series не пересекает cutoff;
- LPL и LDL присутствуют в датасете;
- research model публикует holdout metrics;
- production model использует research tree count и посекундные rows всех карт;
- Dota tests и существующие Dota paths/outputs остаются неизменными;
- backtest, live и VPS collector не затронуты.

## 14. Измерения

Проверено 2026-08-27. Числа здесь — основания решений выше. Повторять эти пробы
не нужно.

### 14.1 Глубина архива livestats

Одна проба на месяц, окно на 25-й минуте первой карты. LEC, LCK, LPL: 24 пробы
за 2026-01 .. 2026-08, все вернули HTTP 200 с настоящими кадрами. Самые старые:
LEC 2026-01-17 (221 день), LCK 2026-01-14 (224 дня), LPL 2026-01-14 (224 дня).

Ещё три пробы на 316..318 дней: NACL 2025-10-12, EMEA Masters 2025-10-12,
Worlds 2025-10-14 — все HTTP 200 с кадрами. Расписание листается и глубже;
перебор остановлен вручную.

Вывод: архив не протухает на горизонте месяцев. Широкая упреждающая закачка не
нужна, линк может идти до загрузки.

### 14.2 Семантика totalGold

`window.participants[].totalGold` равен `details.participants[].totalGoldEarned`
на 43 кадрах × 10 игроков одного матча, без единого расхождения. На 214 кадрах
за пять минут середины игры ни у одного игрока значение не уменьшилось, хотя
покупки в этом окне были. `team.totalGold` равен сумме по пяти игрокам.

Вывод: это заработанное золото, та же величина, что V5 `totalGold`.

### 14.3 Якорь одним запросом

`window/<esportsGameId>` без `startingTime` возвращает первые десять кадров
игры. Проверено на матчах возрастом 3, 81 и 83 дня: у всех первый кадр имеет
`totalGold = 0` и `maxHealth = 0`, то есть loading. Поиск старта перебором не
нужен.

### 14.4 Clock zero из livestats

31 карта LEC и LCK, 2026-08-15 .. 2026-08-27, связана с картами V5 через Cargo
и сверена по времени:

    spawn - gameStartTimestamp: min +2 ms, median +3 ms, max +4 ms
    loading anchor lead:        min 2.54 s, max 5.52 s
    spawn-frame gold vectors:   1 distinct -> все десять игроков по 500

Вывод: кадр спавна и есть clock zero. Якорь V5 не нужен.

### 14.5 Общего id нет

`window` возвращает `esportsGameId` и `esportsMatchId` (18-значные id
lolesports). Схема Leaguepedia ScoreboardGames содержит 69 полей; id-поля —
GameId, MatchId, RiotPlatformGameId, RiotPlatformId, RiotGameId, RiotHash,
RiotVersion. Поля lolesports среди них нет. Линк для измерений 14.4 и 14.7
строился по времени: `0 <= gameStartTimestamp - loading anchor <= 300 s` плюс
совпадение номера карты.

### 14.6 Скорость и объём

120 некэшированных окон, concurrency 16: 30 запросов в секунду, HTTP 200 на
всех, ни одного 429 примерно на 300 запросах. p50 285 мс, p95 1.7 с. gzip по
проводу 2.24 КБ на окно, распакованный JSON 68 КБ, в среднем 40.7 кадра на
окно. Кадров на окно наблюдалось от 10 до 73.

Масштаб: за validation-окно 2026-06-04 .. 2026-08-28 lolesports знает 1058
сыгранных серий и 2315 сыгранных карт по всем 47 лигам. При 120 окнах на карту,
то есть на карте без пауз, это около 280 тысяч запросов, 2.6 часа при 30 rps и
630 МБ в gzip. Паузы удлиняют настенный диапазон и добавляют окон: карта с
паузой 499 секунд требует примерно на 50 окон больше. Реально качается только
подмножество с рынками Polymarket.

### 14.7 Паузы из livestats

Карта 115548681803406125 против V5 LOLTMNT05_220139, 883 кадра за 8 минут:

    разрывов длиннее 3 с: 1
      16:20:14.058Z -> 16:22:48.045Z   153.987 s
    V5 PAUSE_START 16:20:14.058Z, PAUSE_END 16:22:48.045Z, 153.987 s
    расхождение: начало +0.000 s, конец +0.000 s, длительность +0.000 s

Пакетная проверка, 20 связанных карт LEC и LCK, порог 5 секунд, отсчёт от кадра
спавна:

    agreement: 20/20 maps
    non-pause frame gaps: max 1.87 s, p99 1.00 s, n=103 116
    boundary error on matched pauses: max start 0 ms, max end 0 ms

Первый прогон с порогом 3 секунды и отсчётом от loading anchor давал ложные
паузы 3.1..3.3 секунды в 5 картах из 7. Это был разрыв loading -> спавн.

### 14.8 Как часто бывают паузы

24 V5 timeline:

- пауза есть в 15 картах из 24, это 63%;
- всего 30 пауз, 27 начинаются в первые 1200 секунд;
- самая короткая 13.8 с, самая длинная 498.9 с;
- много пауз стартует в первые полминуты: на секундах 2, 3, 3, 6, 9, 14, 20, 26.

Отсюда порог 5 секунд (шум 1.87 с, минимальная пауза 13.8 с) и требование
вести загрузку по игровому времени, а не по числу окон.

### 14.9 Плотность информации

Непрерывные 60 секунд середины игры, 227 кадров: 3.81 кадра в секунду. Модельно
значимых изменений состояния (gold, level, deaths, CS по всем десяти игрокам) —
64 за 60 секунд, то есть 1.07 в секунду. Внутри одной настенной секунды 1..3
различных состояния, медиана 2. Медианный интервал между кадрами 0.10..0.23
секунды.

Вывод: сетка целых секунд не теряет состояние, только промежуточные почти
дублирующие строки.

### 14.10 Скобки таблицы уровней

24 Timeline из Leaguepedia, 7180 participant-frames, из них 2400 в первых десяти
минутах. Первые десять минут дают только уровни 1..9.

| Уровень | max xp при L-1 | min xp при L | Значение LOL_LEVEL_XP |
| ------- | -------------- | ------------ | --------------------- |
| 1       | —              | 0            | 0                     |
| 2       | 272            | 280          | 280                   |
| 3       | 654            | 660          | 660                   |
| 4       | 1139           | 1142         | 1140                  |
| 5       | 1719           | 1720         | 1720                  |
| 6       | 2389           | 2405         | 2400                  |
| 7       | 3175           | 3180         | 3180                  |
| 8       | 4051           | 4066         | 4060                  |
| 9       | 4966           | 5570         | 5040                  |

Уровни 10..18 проверены на кадрах всей игры; там скобки тоже содержат значения
таблицы. Ни одного промаха на 18 уровнях.

7 игр из 24 доходят до уровня 19 или 20, значит level cap в текущем патче выше 18. Пороги ниже 18 при этом те же.

### 14.11 Инварианты livestats

746 кадров из кэша:

    team.totalGold != sum(participants):        0
    team.totalKills != sum(opponent deaths):    0

Кадр спавна: у всех десяти игроков ровно 500 золота, на всех 31 карте из
раздела 14.4.

### 14.12 Почему V5 не годится как зависимость

Доля карт Leaguepedia с непустым RiotPlatformGameId, то есть доступных в V5:

| Лига    | validation-окно    | 24 месяца            |
| ------- | ------------------ | -------------------- |
| LCK     | 236/236 = 100%     | 2838/2838 = 100%     |
| LEC     | 97/97 = 100%       | 643/643 = 100%       |
| LCS     | 59/59 = 100%       | 224/224 = 100%       |
| LCP     | 87/87 = 100%       | 676/676 = 100%       |
| MSI     | 91/91 = 100%       | 189/189 = 100%       |
| Worlds  | —                  | 186/186 = 100%       |
| **LPL** | **55/247 = 22.3%** | **264/1721 = 15.3%** |

Пропадают целыми сплитами: LPL 2026 Split 3 — 167 из 167, LPL 2025 Split 2 —
259 из 259, LDL 2025 Split 1 — 222 из 222. За 24 месяца это 3152 карты, почти
все китайские. Riot не публикует постгейм-JSON китайского турнирного реалма.

Всего строк ScoreboardGames: 3 281 за validation-окно, 26 289 за 24 месяца,
136 119 за всю историю.

### 14.13 Cargo: почему он не нужен даже как индекс

Анонимный `cargoquery` на lol.fandom.com не проходит: 20 попыток за 94 секунды,
все `ratelimited`, отказ за 0.3 секунды, при этом `action=query` отвечал каждый
раз. `Special:CargoExport` через index.php закрыт Cloudflare.

Залогиненный аккаунт видит объявленный лимит
`cargo-query: user 60 hits / 60 seconds, ip 5 hits / 60 seconds`, но burst не
работает: 25 запросов подряд дали 1 успех и 24 отказа. Измеренный интервал
восстановления: 2, 2, 56, 2, 2 секунды. Рабочий темп — один запрос в секунду.

Cargo рабочий инструмент, и креды лежат в `.env` как
LEAGUEPEDIA_USERNAME и LEAGUEPEDIA_BOT_PASSWORD. Он остаётся полезен для разовых
офлайн-сверок и для сборки фикстур, но зависимостью пайплайна не является.

### 14.14 Альтернативные вики

`wiki.leagueoflegends.com` — игровая вики, MediaWiki 1.45.3, API на
`/en-us/api.php`. Cargo нет, киберспортивных namespaces нет. Бесполезна.

`liquipedia.net/leagueoflegends` — MediaWiki 1.43.9 с расширением LiquipediaDB,
не Cargo. Namespaces `Match` и `Data` есть, постгейм-JSON от Riot нет.
Структурный доступ только через api.liquipedia.net с ключом.

Leaguepedia — единственный хост данных V5: 70 namespaces, среди них `V4 data`,
`V4 metadata`, `V5 data`, `V5 metadata`, и 91 таблица Cargo, включая
`PostgameJsonMetadata`, `MatchScheduleGame` и `ScoreboardGames`. Namespace
`V5 metadata` содержит 63 576 страниц и листается анонимно за 128 запросов и 35
секунд.

### 14.15 Прочее

Страницы V5 содержат сам объект Riot `info`, без обёртки
`{"metadata": ..., "info": ...}`. `frameInterval` равен 60000. Служебный
`PAUSE_END` с timestamp 0 имеет realTimestamp на 90 мс раньше
gameStartTimestamp. Это важно только для сборки фикстур.

Cargo возвращает имена полей с пробелами: `DateTime UTC`, `N GameInMatch`,
`Gamelength Number`, плюс ключ `DateTime UTC__precision`. RiotPlatformGameId
приходит с underscore (`LOLTMNT05_220135`), а страница называется через пробел
(`V5 data:LOLTMNT05 220135`).

`index.php?action=raw` на lol.fandom.com отдаёт HTTP 403; контент читается через
`api.php?action=query&prop=revisions&rvprop=content`. Один Timeline весит около
900 КБ.
