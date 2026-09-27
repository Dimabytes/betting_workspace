# План фикса п. 3: лайв Oddin теряет сессию после деплоя

Пункт отчёта: `../../REPORT.md`, S1, п. 3. Отчёт агента: `../../reports/sun-devin.md`, F1, F2, F6.
Все пути кода ниже — в `esports-trader`. Код сверен с `main` на `e6fce7ee`.

## Суть решения

Три правки:

1. При перезапуске закреплённый источник Oddin берёт `oddin_match_id` из `match.json`, а не из discovery.
2. Проверка перепривязки не сравнивает `tick_size` и `min_order_size`. Эти поля рынка меняются по ходу карты.
3. SIGTERM запускает штатный `teardown`: заявки снимаются до выхода процесса.

`final`, `execution_cleanup.json` и `session_end` на SIGTERM не пишем. Причины — в разделе «Что из отчёта не делаем».

## Что на самом деле случилось 26.09

Лог: `../../work/sun-devin/live.log`.

Две разные ошибки. Отчёт склеил их в одну цепочку.

1. `CorruptFeedPin`. После рестарта `match.json` закрепляет `feed_source=oddin`.
   `feed_selection.select_feed` видит закрепление и строит фид из `handoff.oddin_match_id`.
   Discovery первые ~40 с отдаёт `oddin_match_id=None`: каталог Disir ещё пуст.
   `_build_feed` бросает `CorruptFeedPin`. Хотя id уже лежит в `match.json` (поле `oddin_match_id`, схема 8).
   - `9016905513`: boot 12:46:28, emit без id 12:46:48, сбой 12:47:18. Перезапуск 12:48:18 прошёл.
   - `9017026154`: boot 14:27:58, сбой 14:28:29. Перезапуск 14:29:29 уже с id.
2. `refusing to rebind`. Перезапуск `9017026154` в 14:29:29 получил id и открыл сессию.
   На первом тике `write_match_start` отказал: `binds different match/market/model fields ('market',)`.
   Причина: `tick_size` рынка сменился 0.01 → 0.001 в 14:03 (запись `tick_size_change` в ленте).
   `write_match_start` сравнивает весь блок `market`, включая `tick_size` и `min_order_size`.
   Эта ошибка не зависит от Disir. Любой рестарт после смены тика на карте убивает её сессию навсегда.

Супервизор уже не сравнивает эти поля: `wallet_host._static_identity` их не включает.
Сравнивает только `match_meta.write_match_start`.

Почему после рестарта нет `session_end`: в процессе нет обработчика SIGTERM.
`uv run` пересылает SIGTERM в Python (проверил по документации uv). Python по умолчанию сразу завершается.
`finally` в `WalletHost.run` не выполняется, `teardown` не запускается.
Заявки снимает только `cancel_all` при следующем старте движка.

## Шаг 1. Закреплённый Oddin берёт id из архива

1. `src/trader/match_meta.py`: заменить `pinned_feed_source` на `read_feed_pin(match_id) -> FeedPin | None`.
   `FeedPin` — frozen dataclass с полями `source` и `oddin_match_id`. Поведение при ошибке чтения то же: `None`.
2. `src/trader/feed_selection.py` `select_feed`, ветка закрепления:
   ```python
   pin = read_feed_pin(handoff.match_id)
   if pin is not None:
       source = FeedSource(pin.source)
       pinned = replace(handoff, oddin_match_id=pin.oddin_match_id)
       _log_feed_selected(pinned, source, pinned.steam_delay_s, None, None)
       return FeedChoice(_build_feed(steam_client, pinned, source), pinned)
   ```
   Id архива побеждает id discovery. Архив привязан к одному матчу Oddin, discovery может мигать.
   `oddin_match_id` в `DiscoveredMatch` имеет `compare=False`. Проверка перепривязки его не видит.
3. `CorruptFeedPin` остаётся для архива Oddin с `oddin_match_id = null`. Это настоящая порча.
4. Обновить docstring `select_feed` и `read_finalized_match` (там упоминается `pinned_feed_source`).

## Шаг 2. Перепривязка не сравнивает изменяемые поля рынка

1. `src/trader/match_meta.py` `write_match_start`: перенести `tick_size` и `min_order_size` из архива
   в `resume_start`, как уже сделано для `server_steam_id` и `sides`:
   ```python
   # Steam server id, GRID/Steam labels and the sidecar tick/min size can move after the first tick.
   market = replace(
       start.market,
       tick_size=existing.market.tick_size,
       min_order_size=existing.market.min_order_size,
   )
   resume_start = replace(
       start, server_steam_id=existing.server_steam_id, sides=existing.sides, market=market
   )
   ```
2. `match.json` сохраняет тик первого запуска. Воркер берёт текущий тик из сайдкара
   (`match_worker._apply_sidecar_update`), не из `match.json`.
3. Остальные поля проверки не трогаем. `model`, `condition_id`, токены, `yes_is_radiant`, `map_number` —
   это личность архива. Отчёт предлагал «только по id». Это убрало бы проверку модели: одна карта = одна модель.

## Шаг 3. Штатная остановка на SIGTERM

1. `src/trader/orchestrator.py` `run_daemon`: поставить обработчик.
   ```python
   task = asyncio.current_task()
   assert task is not None
   asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
   await run_wallet_daemon(git_commit, mode)
   ```
   Отмена доходит до `WalletHost.run` → `finally` → `teardown`: воркеры отменяются, `_quiesce(False)`
   снимает заявки и ждёт fence, потом `cancel_all` и `engine.shutdown`.
2. `main`: `asyncio.run` бросит `CancelledError` после штатной остановки. Обернуть в `suppress`, выход с кодом 0.
3. `compose.yaml`: `stop_grace_period: 30s` → `60s` только у `live`. `paper` не трогаем. Худший случай `teardown`:
   fence 20 с (`FENCE_TIMEOUT_S`) + drain 5 с (`DRAIN_TIMEOUT_S`) + `cancel_all` + `shutdown`. 30 с мало.
4. `final`, `execution_cleanup.json`, `session_end` при этом не пишутся. Так и должно быть.

## Шаг 4. Тесты

1. `tests/test_trader_wallet_host.py` (там уже тесты `select_feed`):
   - архив с `feed_source=oddin` и id, `handoff.oddin_match_id=None` → `OddinLiveFeed` с id архива,
     `choice.handoff.oddin_match_id` = id архива;
   - архив Oddin с `oddin_match_id=null` → `CorruptFeedPin`.
2. `tests/test_match_meta.py`:
   - новый тест: повторный `write_match_start` со сменой `tick_size` и `min_order_size` проходит,
     `match.json` не меняется;
   - `test_conflicting_immutable_start_input_raises` с `condition_id` продолжает падать. Не трогать.
   - заменить вызовы `pinned_feed_source` на `read_feed_pin`.
3. `tests/test_trader_orchestrator.py`: подменить `run_wallet_daemon` корутиной, которая ждёт вечно и
   ставит флаг в `finally`. Через `loop.call_later` послать себе SIGTERM. Проверить флаг.
4. `make test` целиком. Не запускать во время лайв-карты.

## Шаг 5. Деплой и проверка

1. Деплой только после разрешения пользователя. Деплоим только `live`: `docker compose up -d live`
   (подхватит новый `stop_grace_period`), без `paper` и без `docker compose restart` на все сервисы.
2. `paper` не передеплоиваем и не рестартуем. Он монтирует тот же `./src`: новый код он возьмёт,
   только если сам перезапустится.
3. Этот деплой идёт ещё старым кодом остановки. Рестарт только без лайв-карт и без открытых позиций
   (`summarize.py --restart-check`).
4. Штатная остановка проверяется на следующем рестарте `live`, уже с новым кодом. В логе перед новым boot
   должны быть строки остановки (`cancel_all_sent`, остановка движка). Рестарт укладывается в 60 с без SIGKILL.
5. Лайв: после рестарта при живой карте Oddin в логе нет `CorruptFeedPin` и `refusing to rebind`.
   Сессия дописывает `session_end` с `terminal_reason=finished`.
   Проверять на естественном рестарте. Специально рвать карту с позицией не надо.
6. Раз в неделю: `grep -cE 'CorruptFeedPin|refusing to rebind'` по `live.log` = 0.

## Что из отчёта не делаем

- **`final` и `execution_cleanup.json` на SIGTERM.** Оба значат «карта закончена».
  После старта `WalletHost._start_or_defer` видит `own_final` / `own_cleanup` и не возобновляет карту.
  Это та же брошенная карта, только с аккуратной пометкой.
- **`session_end` на SIGTERM.** `session_start` пишется один раз на архив, продолжение дописывает тот же файл.
  `summarize.py` считает любой `session_end` закрытием (`live = end is None`).
  Возобновлённая живая карта покажется законченной, и `restart-check` скажет SAFE во время карты.
  После шагов 1–2 убитая сессия возобновляется и сама пишет `session_end finished`.
  Если нужна метка для разбора, делать отдельный `kind` (например, `session_stop`), который `summarize.py`
  не считает концом.
- **Ждать первый скан Disir после старта.** После шага 1 возобновление не зависит от Disir.
  Новая карта в первые ~40 с без id Oddin: у рынка только-Oddin нет кандидата ≤ 61 с
  (`MAX_FEED_DELAY_SECONDS`, у Steam задержка 900 с). Будет `waiting_for_feed`, через 30 с придёт Oddin.
  Если есть GRID, карта закрепится на GRID. Потеря — только когда карта начинается в эти ~40 с
  и Oddin выиграл бы сравнение задержек.
- **Разделить ошибки запуска на повторяемые и постоянные** (`MAX_CRASH_RESTARTS`). После шагов 1–2
  обе постоянные ошибки из лога уходят.

## Не входит в фикс

- Смена модели посреди карты по-прежнему даёт `refusing to rebind`. Это правило «одна карта = одна модель».
  Правило деплоя: модель не менять при живой карте.
- F7 из `sun-devin`: неизвестная перезапись `9017026154/match.json` в 14:35. Отдельный вопрос.
- Id заявок в `placed[]` (п. 11, п. 30).
- Старые брошенные сессии: разбор ниже, правка не нужна.

## Старые брошенные сессии

Брошенная сессия: в `session.jsonl` нет `session_end`, в `match.json` нет `final`, нет `execution_cleanup.json`.
Их 14 у Dota (`../../work/sun-devin/archive_scan.txt`).

- Деньги. Остаток позиции у всех ≤ 0.009 акции (`sun-devin`, «Checked and OK»).
  Висящих заявок нет: каждый старт движка шлёт `cancel_all`, после 26.09 стартов было несколько.
- Отчёт PnL. `summarize.py` уже помечает их `ORPHAN` на лету: нет конца, нет `final`, нет cleanup,
  файлы не менялись 15 мин. В `sum_net` по картам они не входят. PnL кошелька (`polymarket_today`) их включает.
  Пример: около −$24.8 у `9017026154` есть в кошельке и нет в сумме по картам.
- Данные. `archive_index` индексирует их как обычно (`has_final=False`, `winner` и `duration_seconds` пустые).
  `s03` связывает их по horn. Победителя каталог берёт не из архива. Тики обрываются в момент рестарта.
  Я не проверял, как режим schedule бэктеста проигрывает такой обрезанный фид.
- Компрессия. `compress_archives` без `execution_cleanup.json` их не сжимает. Это только место на диске.

Вывод: помечать в файлах не нужно. Метка `ORPHAN` в `summarize.py` уже есть, деньги и заявки чистые.

## Готово, когда

- [ ] Шаги 1–4: код и тесты в `main`, `make test` зелёный.
- [ ] Шаг 5.1: `live` задеплоен, `paper` не тронут.
- [ ] Шаг 5.4: следующий рестарт `live` остановился штатно, в логе есть строки остановки.
- [ ] Шаг 5.5: первый лайв-рестарт при карте Oddin прошёл без `CorruptFeedPin` и `refusing to rebind`.
