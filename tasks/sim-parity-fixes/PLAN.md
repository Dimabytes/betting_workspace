# План: бэктест повторяет live после починки, плюс баги live из разбора B

Дата: 2026-10-10. План для нового контекста: выполнять по шагам, по порядку.

## Контекст

- Расследование: `betting_workspace/investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md`.
  Там история, цифры и скрипты. Прочитать перед шагами бэктеста.
- Ресерч 2026-10-10, отчёты: `betting_workspace/runs/2026-10-10-sim-followup/reports/`
  (`latency.md`, `livebugs-hold.md`, `livebugs-other.md`, `port.md`, `scope.md`, `fw-fork.md`, `fw-crash.md`, `stratz.md`).
  Каждый шаг ниже ссылается на свой отчёт: там `file:line`, цифры и тесты.
- Код:
  - `esports-trader` `main`: всё запушено 2026-10-10 (`3d2394e1`), включая фиксы аудита live A.
  - `esports-trader-cadence`: worktree, ветка `two-sided-live-cadence`, не закоммичено. Цикл poly-maker, задержки HTTP, CONFIRMED, книга +10 мс.
  - Фиксы аудита live A (задача `betting_workspace/tasks/live-a-audit-fixes/feature.json`, STEP-001..011) уже в `main`.
  - `prediction-market-backtesting`: ветка `local-day-native`, локальные коммиты `df17b59`, `e2cfebb`, форка нет.
  - Код расследования (не в продукте): `.../2026-10-08-liveb-postmortem/sim/replica/` (`replica_repo.py`, `tape_align.py`).
- Правила: `esports-trader/AGENTS.md`, `betting_workspace/AGENTS.md`. Не пушить без просьбы. poly-maker не трогать.
  `py_clob_client*` не трогать.

Принцип: бэктест моделирует live **после** починки (фиксы аудита live A в `main` и часть A ниже), а не live 2026-10-08 с его багами.
Окна баговых удержаний из `holds.json` в продукт не переносятся.

Порядок: сначала live (часть A), потому что ядро общее и правки размера и пыли сами попадут в бэктест.
Потом бэктест (часть B). Один прогон и один промоут в конце.

## Часть A. Live

База: `main`, фиксы аудита live A уже в нём. Эти шаги трогают те же файлы (`wallet_host.py`, `unsettled_buy_recovery.py`, `lifecycle.py`, `host_resources.py`).

### A1. Урезание размера py_clob_client

Отчёт: `livebugs-hold.md`, раздел «Урезание».

- Баг: `py_clob_client_v2` подписывает `round_down(size, 2)` = `floor(size*100)/100` во float. `17.08*100 = 1707.9999999999998` → 17.07.
  Ядро помнит 17.08, на бирже 17.07. 236 из 12,424 BUY B за 2026-10-08. После полной заливки остаток 0.01 держит токен.
  `live-a-audit-fixes` STEP-004 сокращает простой до CONFIRMED, но размер на бирже всё равно на 0.01 меньше.
- Правка: функция рядом с `share_floor` (`src/shared/utils/trading.py`), которая даёт ровно тот размер, что подпишет клиент.
  Использовать там, где размер уходит на биржу: `buy_share_quantity`, `_leg_target` (`src/strategy/two_sided_quoting.py`),
  количество SELL в `src/strategy/quoting.py`. `share_floor` не менять.
- Тест: для всех размеров с шагом 0.01 до 1000 новая функция равна `round_down` клиента и стабильна при повторе;
  `17.08 → 17.07`; `20.0 → 20.0`; размер ноги two-sided и BUY follow300 на «плохом» центе равен подписанному.
- Затрагивает Dota, LoL и two-sided: код ядра общий.

### A2. Пыль: остаток меньше цента не держит BUY

Отчёт: `livebugs-hold.md`, раздел «Пыль».

- Баг: заливки 8.81356 + 11.18 на ордер 20 дают `share_floor(19.99356) = 19.99 < 20`. Ядро считает ордер частично
  залитым и держит ступень. STEP-004 сокращает это до CONFIRMED, STEP-006 это не трогает.
- Правка: `src/strategy/lifecycle.py` (`_apply_known_fill`): ордер частичный, только если `share_floor(submitted − filled) >= 0.01`.
- Тест: 8.81356 + 11.18 на 20 снимают BUY. 19.99 на 20 не снимает. 17.07 на 17.08 после A1 не возникает.

### A3. REST-конфликт: CONFIRMED побеждает, если нет MATCHED

Источник: остаток STEP-004 `live-a-audit-fixes` («held REST conflict», `plans/STEP-004.md` §5.5), разбор 2026-10-10.

- Баг: строка `unsettled_buys` открыта на запрошенный объём (пример: 215.38), в `fill_ledger` CONFIRMED 17.02,
  WS-терминала не было, REST trades даёт 0. `_apply_unsettled_proofs` (`src/trader/wallet_host.py:1018`) отбрасывает
  proof ниже CONFIRMED (`reason="below_confirmed"`, `continue`). Строка висит навсегда: она в sqlite, рестарт не помогает.
- Вред: SELL не блокирует. Строка держит ступень и резерв `(qty − confirmed) × price` в `unsettled_buy_notional(conn, None)`.
  Это общий резерв кошелька (`src/trader/session_budget.py:44-49`): около $60–100 account cap на случай, до ручного SQL.
  Через 600 с алерт (`UNSETTLED_ALERT_AGE_S`).
- Частота: в аудите 1 из 63 строк, что ждали дольше 60 с (8332 строки за 4 дня). Около 1 раза в 4–5 дней.
- Правка: на ветке `below_confirmed` вместо `continue` проверить леджер. Если у этого venue id нет BUY-строк в статусе
  MATCHED (все CONFIRMED или FAILED), доказать строку на `confirmed`: `prove_unsettled_buy(conn, venue_id, confirmed)`.
  Если MATCHED есть, оставить как сейчас и ждать следующего reconcile (20 с). Хелпер для MATCHED — рядом с
  `read_confirmed_buy_qty` (`src/trader/core_persistence.py:861`). Около 6 строк плюс хелпер.
- Почему безопасно: CONFIRMED — это on-chain, он сильнее REST trades. Доверие то же, что к положительной REST-сумме
  с одного чтения. Ноль из REST и так проходит только со второго чтения. Новых ожиданий и REST-вызовов нет.
- Тест (`tests/test_trader_buy_terminal_proof.py`): REST 0, CONFIRMED 17.02, MATCHED нет → строка доказана на 17.02
  и закрывается, резерв освобождён. Тот же случай с одной MATCHED-строкой → строка открыта и `ignored below_confirmed`;
  после её CONFIRMED строка доказана.

### A4. Book guard: fail-closed при исключении ядра

Источник: остаток STEP-005 `live-a-audit-fixes` («no fail-closed path on a core exception in the guard»).

- Баг: на обычном пути `quotes_adapter` ловит исключение ядра и отдаёт пустой план, а `esports_reconcile` снимает
  все ордера (`src/trader/host_resources.py:114-116`). Guard `_install_book_guard` (`host_resources.py:144`) зовёт
  `run_core_cycle` и `core.take_plan()` без `try`. Исключение уходит в `_quoter` форка → `quoter_error` → повтор
  через 0.5 с. Ордера стоят на бирже, пока книга YES плохая.
- Частота: `construct_quotes fault` и `quoter_error` в логах VPS 0 раз за всё время. Это страховка, а не частый баг.
- Правка: `try/except Exception` вокруг `run_core_cycle` и `take_plan` в guard. В `except`: `_quote_fault(type(exc).__name__)`,
  отменить все ордера карты через `engine.gateway.cancel` по `engine.state.orders_for(meta.yes.token_id)` и
  `orders_for(meta.no.token_id)`, записать diag `book_guard` с `guard=fault` и типом ошибки. Дальше та же обработка `ok`,
  что уже есть в guard. Около 8 строк. Путь без исключения не меняется. Ожиданий и REST-вызовов не добавляет.
- Тест (`tests/test_trader_book_guard.py`): плохая книга YES, `run_core_cycle` бросает → `gateway.cancel` получил все
  ордера обоих токенов, `place` не вызван, `_quoter` не видит исключения.

### A5. Позиция без свежего fair: SELL на ask, а не отмена

Источник: STEP-010 `live-a-audit-fixes`, разбор 2026-10-10. Риск «купил и не продаёт».

- Сейчас при открытой позиции ядро снимает SELL и ничего не ставит в двух случаях:
  1. Сигналы модели приходят, но каждый не проходит проверку якоря дольше 45 с (`EXIT_FEED_STALE_SECONDS`).
     Fair протух → `_blocked("anchor")` (`src/strategy/quoting.py:816`). Так стало после STEP-010.
     До него SELL висел по старому fair, который считался свежим, и мог стоять далеко от рынка.
  2. Фид модели молчит 45 с: `_on_exit_feed_timeout` (`src/trader/match_worker.py:1079`) шлёт `signal=None`
     → `_blocked("stale_signal")` (`quoting.py:801`). Так было задумано и до ветки («drop fair so quoting pulls the SELL»).
  SELL возвращается, только когда придёт рабочий якорь или сигнал. В recovery (`sell_only`) уже иначе:
  `_recovery_exit` → `decide_sell(fair=None)` → `_join_ask_sell` (`quoting.py:894`, `:522`, `:469`).
- Частота: в аудите самая длинная серия провалов якоря 17.7 с. Молчание фида 45 с при открытой позиции не мерили.
- Правка: в `_quote_v5_latch` (`quoting.py:791`) для обоих случаев: если позиция > 0, снять только BUY и поставить
  SELL через `decide_sell(fair=None)`, как в recovery. Без позиции — `_blocked`, как сейчас. Около 10 строк.
  Docstring `_on_exit_feed_timeout` поправить: он больше не снимает SELL, а переводит его на ask.
- Цена выхода: SELL встаёт пассивно на лучший ask и не пересекает спред. Без fair бот может продать ниже
  справедливой цены, если рынок вот-вот пойдёт вверх. Это плата за гарантированный выход.
  Kill-гейт, `min_life`, `unconfirmed_hold` и settling живут внутри `decide_sell` и работают как в recovery.
- Бэктест: ядро общее (`src/backtest/strategy.py` → `strategy.engine.step`), поэтому правка попадёт туда сама.
  Сценарий в бэктесте реальный: те же записанные сигналы и книги, провалы якоря там тоже есть.
  Золотые follow300 сдвинутся там, где fair протухает при открытой позиции; их перезаписывает B9.
- B не задет: у two-sided нет latch.
- Тесты: позиция есть, провалы якоря 46 с → BUY сняты, SELL на join ask. STEP-010 тесты
  `test_failed_anchor_streak_pulls_the_frozen_fair_sell_past_exit_age` и
  `test_stripped_book_anchor_failure_pulls_sell_c31_past_exit_age` переписать: на 46 с SELL переезжает на ask,
  а не снимается. Фид молчит 46 с при позиции → SELL на ask. Без позиции → `_blocked`, как сейчас.
  Пришёл рабочий якорь → SELL снова считается от fair.

### A6. Потерянный WS CANCELLATION

Отчёт: `livebugs-hold.md`, раздел «Потерянный CANCELLATION».

- Баг: после успешной HTTP-отмены WS-кадр CANCELLATION иногда не приходит. Строку `unsettled_buys` закрывает только REST
  не раньше 60 с (`REST_PROOF_MIN_AGE_S`), а после STEP-004 ноль принимается только со второго чтения (ещё +20 с).
  За 2026-10-08 у B: 8 окон, 13 ордеров, 62–80 с без бида. User WS не переигрывает дыры. Почему он молчал, не выяснено.
- Правка (предложение ресерча, проверить на ревью): для строки, которую создала успешная HTTP-отмена, не ждать 60 с.
  `matched_qty` брать из локального `fill_ledger` (MATCHED и CONFIRMED). Ноль принимать, только если этого id нет в
  `open_orders`, который цикл reconcile уже читает. После успешной отмены сразу звать `_reconcile_now`.
  Файлы: `src/trader/unsettled_buy_recovery.py`, `src/trader/wallet_host.py`, около 50–80 строк.
- Риск: снимок `open_orders` отстал и не видит живой ордер → ступень отпускается рано → второй бид. Ревью обязательно.
- Связь с `live-a-audit-fixes`: строить поверх STEP-004 (`ca6284f0`), он правил те же `wallet_host.py` и
  `unsettled_buy_recovery.py`. Не ослаблять его правила: ноль из REST только со второго чтения, proof ниже CONFIRMED отбрасывается
  (кроме правила A3: нет MATCHED → proof на CONFIRMED).
  С откатом `d7332da5` (SELL в `finish_cycle`, `session_core.py`) это не связано, но риск того же класса: два настоящих
  ордера на бирже. На ревью отдельно проверить сценарий «ордер ещё жив, а снимок `open_orders` его не видит».
- Тест (`tests/test_trader_unsettled_buy_recovery.py`): строка младше 60 с, ордера нет в книге, в леджере 0 → proof 0.
  В леджере 17.07 → proof 17.07, даже если `get_trades` пуст.

### A7. Логи для разбора

Отчёт: `livebugs-other.md`, раздел 3.

1. B пишет `core_trace.jsonl`: прокинуть уже открытый `CoreTrace` в `LiveCore` в `src/trader/two_sided_worker.py`
   (сейчас `_open_trace` возвращает None, около 10 строк). Заголовок follow300 для two-sided не подходит:
   нужен свой header в `core_trace.py` / `core_trace_codec.py` (около 40 строк), если реплей будет читать заголовок.
   Размер: около 100 MB на карту, потолок 256 MB.
2. `cancels_out` в журнал движка: в `_durable_cancel` (`wallet_host.py`) до HTTP писать venue id, core id, `cancel_reason`
   и время решения ядра. Добавить kind в `_JOURNAL_KINDS` (`engine_seams.py`). Около 20 строк. Пишут оба кошелька.
3. Сырой `reprice_since_ns` в строку события trace (`core_trace.py` `write_event`), около 5 строк.
   Без него момент старта удержания 300 мс по trace не восстановить.

Проверка: после деплоя на одной карте B есть trace, строки `cancels_out` с причинами и `reprice_since_ns`.

### A8. Merge: повтор сразу при устаревшем nonce relayer

Проверено по логам VPS (`docker compose logs live_b`, 2026-10-08):
- 9034882975 (`grid-3011821-m1`), merge через 72 с после порога. В 14:46:00.748 первая попытка упала:
  `merge failed ... pause=30s tx=- reason=post: relayer 400: {'error': 'batch nonce 34 does not match on-chain nonce 35'}`.
  Пауза 30 с, затем новые заливки закрыли гейт `has_unacked_matched` до CONFIRMED в 14:46:32, merge прошёл в 14:46:38.
- Общий `_chain_lock` ни при чём. За день это единственный провал из 35 merge.
- `_fetch_nonce` (`src/trader/ctf_merge.py`) берёт nonce у relayer, а relayer отдал старый.

Правка: если relayer ответил «nonce does not match on-chain nonce», сразу запросить nonce ещё раз и повторить
один раз, без паузы 30 с (`MERGE_PAUSES_S` в `pair_merge.py`). Около 15–20 строк. Тест на ответ relayer с этой ошибкой.

## Часть B. Бэктест

База: `main` после части A. Ветку `two-sided-live-cadence` переносить в `main` по шагам ниже, не целиком.
Отчёт для B3–B8: `port.md`, таблицы и «Порядок порта».

### B1. Фреймворк: своя приватная репа, пин и проверка падений

Отчёты: `fw-fork.md`, `fw-crash.md`.

1. Своя репа. GitHub не даёт сделать приватный форк публичного репо, поэтому это новая приватная репа, а не «fork».
   В апстрим PR не отправляем.
   - `gh auth switch -u Dimabytes` (сейчас активен `di-payflow`), затем
     `gh repo create Dimabytes/prediction-market-backtesting --private`.
   - В чекауте `/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting`:
     `git remote rename origin upstream`, `git remote add origin git@github.com:Dimabytes/prediction-market-backtesting.git`,
     `git push -u origin local-day-native`, `git push origin v4.1-alpha`, `git push origin --tags`.
   - Проверить: `git remote -v` (origin = наша репа, upstream = evan-kolberg), `git status -sb` трекает `origin/local-day-native`.
   - `.so` в git не входит (`*.so` в `.gitignore`). На новой машине собирать `maturin`, команда в learning.
   - Обновить `betting_workspace/.learnings/prediction-market-backtesting.md` и `betting_workspace/AGENTS.md`:
     фреймворк — наша приватная репа, апстрим только для чтения.
2. Пин. `esports-trader/Makefile:9`: `BACKTEST_FRAMEWORK_COMMIT` = `e2cfebb` (сейчас `c76e77a`, из-за этого падает
   `make backtest-install`). Коммит в `esports-trader` не пушить (см. «Push»).
3. Падения Nautilus (`NAUTILUS_ZERO_FILL_SKIP_IDS`, `src/backtest/lol_inputs.py:37-71`, 10 карт: 3 Dota, 7 LoL;
   плюс `dota2-ty-pari-2026-07-18-game2`, его нет в списке).
   - Причина падения: fill меньше 0.0000005 шера после закрытой позиции, `assert side != FLAT` в Nautilus 1.226.
   - Гипотеза (проверено арифметикой, не прогоном): старая float-формула raw (`c76e77a`) давала размеры уровней книги
     мимо точного raw в 6.6% значений, до 8192 единиц, в половине случаев ниже точного. Сделка точного размера
     оставляла «избыток» в тысячи raw → такой fill. `e2cfebb` считает raw целыми.
   - Шаг: прогнать каждую карту из списка и ty-pari через `--match-id` на текущем фреймворке, зёрна 0–2, той же игры.
     Если ни одна не падает, удалить список и `NAUTILUS_ZERO_FILL_STOP_REASON`.
     Если падает, остановиться и доложить: дальше только правка Nautilus `engine.pyx` `_open_position` и своя сборка колеса.

### B2. Лента v3

- Логику `sim/replica/tape_align.py` перенести в `src/backtest/onchain_retime.py`: сделка свипа ставится на строку
  книги, которая её показала. Исходное время WS-принта сохранить отдельной колонкой: B3 читает его.
- Эффект на B (SIMULATOR-PLAN): ложные заливки свежих ордеров 41 → 23 шера на зерно, точные совпадения 722 → 785 шеров.
- Общий код: меняет follow300 Dota и LoL.
- Тесты рядом с `tests/test_onchain_retime.py`.

### B3. Ядро узнаёт о заливке по времени WS-принта

- Перенести `FillNoticeMixin` (`replica_repo.py:401-461`) в общий адаптер (`src/backtest/strategy.py`, `on_order_filled`).
  Сделка в книге остаётся на своём времени, ядро получает `Fill` в момент WS-принта. Отмена уже залитого ордера ждёт того же момента.
- Тест: заливка в книге раньше принта, новый бид не раньше принта.

### B4. Цикл котировки live в общий адаптер

Из незакоммиченного diff `esports-trader-cadence` поднять в `DotaMakerStrategy` (оба продукта):

- цикл poly-maker: событие → debounce 100 мс → цикл; HTTP блокирует цикл (`_wake_quoter`, `_run_cycle`, `_arm_wake`);
- задержка постановки — розыгрыш из таблицы live `PLACE_LATENCY_MS` (решение → PLACEMENT: p50 78 мс, p90 136);
  движку Nautilus insert = 45 мс, остаток адаптер держит до `submit`; ответ HTTP `RESPONSE_MS = 32`;
- книга стратегии на `BOOK_VIEW_DELAY_MS = 10` позже биржи;
- отмена BUY: `CancelUnsettled`, затем `BuySettled`; без заливки по WS-событию, с заливкой на CONFIRMED (`BUY_CONFIRM_MS = 7250`).
  Сейчас follow300-адаптер отпускает ступень сразу на `CancelAck` (`strategy.py:1242-1254`), а live — нет (коммит `54b7ce50`, так задумано).
  У кошелька A: пустая отмена → `BuySettled` p50 143 мс, с заливкой p50 6.2 с;
- длительность расчёта цикла: 10 мс у two-sided, около 6 мс у follow300 (интервалы `Wake` A 105–108 мс);
- follow300: алерты сигнала и kill gate будят `_wake_quoter`;
- follow300: плохая книга YES доходит до ядра (отмены уходят, постановок нет), как live A после STEP-005.
  Two-sided оставляет пропуск цикла: у B live этого гарда нет (`host_resources.py` ставит его только follow300).
- `ORDER_INSERT_LATENCY_MS = 175` убрать из пути обоих продуктов. Флаг `--buy-settle-ms` удалить.

Проверка: тесты ветки (`test_backtest_two_sided_core.py`), новые тесты на debounce follow300. У follow300-сима
распределение `CancelUnsettled → BuySettled` близко к A (p50 около 143 мс без заливки).

### B5. Strip своих ордеров как в live после STEP-007..009

- `src/backtest/strip_own_book.py` и вид книги в адаптерах привести к `book_strip.py` в `main` (STEP-007..009):
  ключ цены 0.0001; свой ордер вычитается и из зеркала на соседнем токене по `1 − price`; уровень с остатком-пылью не лучший.
- Для архива B вычитается только B, включая его зеркало. A в книге B остаётся чужим.
- Откуда разрыв: свои ордера сима в книгу данных Nautilus не попадают, ядро сима их и так не видит (`_snapshot_books`
  берёт лучшие цены из `self._books`). Расходится архив: в нём лежат настоящие ордера live и их зеркала. Strip сейчас
  убирает ордер только со своей стороны своего токена, зеркало на другом токене остаётся «чужим» и сдвигает `book_p`.
- Тест: ордер live BUY 0.26 на токене 0 убирает и бид 0.26 токена 0, и аск 0.74 токена 1; `book_p` после strip равен
  `book_p` из `books_from_md` live на тех же уровнях.

### B6. Two-sided: merge с задержкой и лимит карты

- `MergeDelayMixin` (`replica_repo.py:618-668`) в `TwoSidedCoreStrategy._merge_pairs`: merge стартует, когда последняя
  заливка карты старше 7.25 с, пары уходят через 3 с. Расширить `test_merge_at_held_usdc_and_pairs`.
- `level_usdc` по умолчанию 20 для two-sided (лимит карты 9 × $20, как live B); `--level-usdc` переопределяет.
  Follow300 остаётся 300.

### B7. Скрипты расследования после слияния

- `sim/strategy_check/live_net.py`: после заливки live звать `_wake_quoter`, а не `_arm_wake`. В новом адаптере
  `_arm_wake` только запоминает дедлайн, и цикл не просыпается.
- `sim/exec_replay/journal_replay.py`: прогнать один сегмент и сверить, что 63 из 63 ордеров live совпадают.
- Скрипты остаются в `betting_workspace`, в продукт не идут: точный повтор (`LiveTimingMixin`), окна `holds.json`,
  позиция live, трассы, jitter, kill-wake, venue-confirm, own-strip.

### B8. Проверка на B

- Свободный ансамбль (8 зёрен) на 6 сегментах и на 7 картах того же дня, на продуктовом коде без обёрток.
  Ориентир расследования: 6 сегментов `fn` 1128 ± 30 шеров против live 1048.4; 7 карт `xf` 7560 против 7543.
  Новые цифры будут другими: live 2026-10-08 работал с багами из части A, а бэктест теперь моделирует live без них.
  Записать результат в SIMULATOR-PLAN.md.
- Реплей ордеров live (`run_exec_fixed.py`) на новом коде: 1048.4 шера, все залитые ордера совпали.

### B9. Один прогон и промоут

- Полная валидация follow300 Dota и LoL (обычные зёрна), two-sided по выбору.
- Перезаписать золотые follow300 smoke. `make test` и pre-commit зелёные.
- Промоут `LIVE` для Dota и LoL один раз. Старые прогоны `rawfix`, `tapefix` удалить после промоута.

## Часть C. Чистка

- `SIMULATOR-PLAN.md`: убрать устаревшие разделы внизу («Итог в трёх строках», «Правка в esports-trader (сделана,
  не закоммичена)», «Шаги до…»). Тейп по принту давно закоммичен (`4773d0b1`). Оставить ссылку на этот план.
- Записать в SIMULATOR-PLAN.md находки по логам VPS (2026-10-10):
  - merge 72 с — устаревший nonce relayer (шаг A8);
  - 15:06:35: live решил снять бид 0.26 в 35.373, на границе удержания. HTTP-отмена шла 670 мс (решение → ответ;
    за день p50 76, p90 134, p99 702 мс, 11,906 отмен B), ордер ушёл из книги в 35.853. Это хвост задержки, а не
    недостающая механика. В остальных 7 «спасениях» отмена шла 60–90 мс.
  - В `docker compose logs live_b` строка `trader core buy settled ... wait_ms` даёт время решения об отмене BUY
    (`ts − wait_ms`), строка `buy gone` — время HTTP-ответа. Логи ротируются (5 × 10 MB), поэтому шаг A7 всё равно нужен.
- Удалить экспорт сессии: `prediction-market-backtesting/2026-10-09-203242-local-command-caveat…txt`.
  Экспорт в `betting_workspace` удалён 2026-10-10.
- SIMULATOR-PLAN.md закоммичен (`e0dd1cb`). Скрипты `sim/` в git не идут: `investigations` в `.gitignore` по решению владельца.
- Worktree `esports-trader-cadence` удалить после B4 (всё нужное перенесено).

## Push

- `prediction-market-backtesting` — в свою приватную репу (B1).
- `esports-trader` `main` (всё из этого плана) — не пушить, пока владелец не скажет.

## Не делаем

- Зависание на 18 минут после отказов биржи (9034789047, 12:25–12:43) — решение владельца.
- Коллектор `taker_asset_id` — владелец уже починил. Бэктест берёт сторону по мейкеру и от этого поля не зависит.
- Окна баговых удержаний live в бэктесте.
