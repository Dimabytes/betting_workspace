**US-006: требуется доработка.** Ревью от 2026-09-06 по `feature-json-step-review`.

Проверен `esports-trader`, ветка `ladder-experiment`, diff `1f13a64..d8fcdb0`: 20 файлов, +2509/−128 строк. В начале ревью изменения были незакоммичены; во время проверки появился коммит `d8fcdb0`. Требования сверены с `feature.json`, планом US-006 и проектным AGENTS.md. Код проекта и отметки выполнения не менялись.

Найдено пять P1 и два P2. Основной дефект структуры: SQLite-модель, очередь core и gateway пока не соединены одной последовательностью применения и фиксации событий. Поэтому наличие новых таблиц и зелёных unit-тестов не даёт заявленных гарантий восстановления.

1. **[P1] Durable execution не подключён к реальной отправке ордеров.**

   Места: [core_execution.py:59](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/core_execution.py:59), [wallet_host.py:493](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/wallet_host.py:493), [wallet_host.py:514](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/wallet_host.py:514).

   `persist_prepared_places`, `persist_prepared_cancels`, `mark_dispatch_started`, сохранение outcomes и `consume_command` не вызываются производственным путём отправки. Из нового execution-модуля host подключает только обёртку, удаляющую prepared-команды. Реальный `place` по-прежнему сначала делает `await original_place(...)`, затем сообщает результат worker. Таблица команд при обычной торговле не получает намерения перед POST; вычисление hash тоже остаётся вне gateway.

   Если биржа приняла ордер, а процесс умер до callback, у рестарта нет предусмотренной этим шагом durable-идентичности запроса. Сохранение binding после ответа этот интервал не закрывает. Сам `retire_unsent` также меняет только SQL и не сообщает core, что его pending-order доказанно не отправлен.

   Исправление: подключить один исполнитель окончательного batch к gateway: подготовить подписанный ордер → зафиксировать checkpoint/command/hash → отправить именно его → записать outcome → применить событие core и consumed marker атомарно. Случай known-not-sent должен завершать pending-order в core. Проверять этот путь через установленную gateway-обёртку, а не прямой вызов SQL-helper.

2. **[P1] Курсор outbox фиксируется раньше применения fill; после сбоя событие теряется для core.**

   Места: [wallet_host.py:562](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/wallet_host.py:562), [match_worker.py:623](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:623), [match_worker.py:834](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:834).

   `note_core_fill` только ставит Fill в deque и сохраняет старый `_state`. После этого `_consume_core_outbox` увеличивает `last_outbox_seq` и ещё раз сохраняет тот же неприменённый state. Экспорт checkpoint не включает deque. Сбой до следующего drain оставляет durable-курсор после события, которого нет в `seen_fill_ids`, позиции и ownership core.

   Воспроизведено на настоящем `WalletStateStore`: после consumer и reopen `last_outbox_seq=1`, `seen_fill_ids=()`, позиция core нулевая, запрос outbox после курсора пустой. Ledger при этом содержит fill. Это потеря доставки в core, а не повторное списание денег.

   Исправление: один владелец транзакции должен вычислить следующий state, записать его вместе с bindings и курсором, затем опубликовать результат в памяти. При ошибке записи сохранить прежний курсор и прежнее состояние; не подтверждать постановку в deque как обработку события.

3. **[P1] Новая карта тоже безусловно переходит в постоянный sell-only.**

   Места: [match_worker.py:315](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:315), [match_worker.py:504](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:504).

   Каждый успешный `_try_attach` вызывает `_restore_open_round`. Теперь тот всегда вызывает `_begin_recovery`, даже при новом пустом журнале, отсутствии checkpoint, ордеров и inventory. Общая lifecycle-логика навсегда выставляет `sell_only=True`. Функция `classify_session`, различающая fresh/recovery, в attach не используется.

   Воспроизведено с пустой SQLite и `JournalRound(0, 0, None)`: после штатного restore-вызова новая сессия уже sell-only. Это блокирует BUY и на новых картах, прямо нарушая критерий US-006.

   Исправление: классифицировать сессию до регистрации worker и replay outbox; учитывать существование журнала до записи нового session_start. Только recovery-сессии получают durable-lock. Новый condition должен пройти реальный attach-тест с последующим BUY.

4. **[P1] Recovery не продолжает проверку после cancel/settlement/fill.**

   Места: [match_worker.py:531](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:531), [match_worker.py:536](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:536), [match_worker.py:615](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:615).

   Единственный вызов `_maybe_verify_recovery` находится в `_begin_recovery`. Если при входе были незавершённые ордера или MATCHED, проверка возвращается. Cancel callbacks лишь enqueue+checkpoint+wake; дальнейшие циклы не запускают проверку. `RecoveryCoordinator` нигде не создаётся. Аналогично зависает recovery после FAILED или нового fill, который снова устанавливает pending.

   Воспроизведено: восстановленный BUY → recovery → успешный cancel callback → штатный цикл core. Ордеров больше нет, pending остаётся; инструментированная проверка ни разу не вызвана. Следовательно, обычный сценарий рестарта с ордерами не доходит до verified SELL, а после продажи первого токена нет рабочего продолжения для второго.

   Исправление: координатор должен продолжать drain/proof на изменениях command/ledger revision, cancel outcomes и settlement, повторять временно неуспешные чтения и выдавать `RecoveryVerified` текущего поколения через общий исполнитель событий.

5. **[P1] RecoveryVerified создаётся без доказательства inventory и settlement.**

   Места: [match_worker.py:544](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:544), [core_recovery.py:201](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/core_recovery.py:201), [wallet_store.py:462](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/wallet_store.py:462).

   Активная реализация проверяет только core.orders и `has_unacked_matched`, затем берёт размеры из SQLite и очищает барьер. Нет вызова `prove_token_balances`, сверки обеих token balances после await, разрешения durable unknown-команд или подтверждения settlement по ledger. Журнальный ack ошибочно влияет на возможность признать MATCHED завершённым, хотя добавлен подходящий `matched_keys_for_tokens`.

   Воспроизведено: ledger содержит MATCHED, его outbox-строка acked, core.orders пуст. `_begin_recovery` устанавливает `recovery_pending=False` и принимает 10 shares как verified без единого обращения к venue. Это ещё не доказывает фактическую отправку SELL: дальнейшие permissions могут его остановить. Но обязательный recovery-барьер уже снят без требуемых оснований.

   Исправление: разрешать settlement по durable ledger status независимо от journal ack; завершать ордера по строгому доказательству; сверять оба баланса с проверкой revision после чтения. Недоступные данные оставляют pending, а не превращаются в успешную проверку.

6. **[P2] MatchWorker вырос с 973 до 1090 строк, пока выделенный recovery-слой остаётся неиспользуемым.**

   Места: [match_worker.py:504](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:504), [match_worker.py:789](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/match_worker.py:789), [core_recovery.py:100](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/core_recovery.py:100).

   Это структурная регрессия: worker теперь сам выбирает recovery inventory, проверяет барьер, читает таблицы, перебирает приватный ID-map core и коммитит приватное соединение store. Параллельно новый модуль содержит альтернативные classification/selection/verification-функции без подключения. Проверка незавершённых ордеров повторяет `lifecycle.has_unresolved_orders`; JSON-примитивы повторяют существующий `trader.strict_json`. Проверки `_wallet_store(object)` дают тестовым fake-host возможность молча пропустить persistence.

   До принятия нужна декомпозиция. Упрощение здесь — убрать дублирующие пути: оставить worker только orchestration жизненного цикла матча; один coordinator владеет recovery, один транзакционный consumer — checkpoint/cursor/bindings, gateway executor — сетевыми попытками. Тестам этого шага дать настоящий временный WalletStateStore. Это удалит разрозненные commits, выборы inventory и проверки возможностей store, а не просто распределит их по файлам.

7. **[P2] Acceptance-тесты не выполняют сценарии, заявленные в названиях.**

   Места: [test_trader_core_execution.py:40](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/tests/test_trader_core_execution.py:40), [test_trader_core_execution.py:185](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/tests/test_trader_core_execution.py:185), [test_adapter_contract.py:256](/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/tests/test_adapter_contract.py:256).

   `test_subprocess_crash_keeps_persisted_hash` не создаёт subprocess и fake venue: он делает commit, штатный close и reopen. `test_db_failure_before_dispatch_does_not_post` вообще не вызывает gateway — локальный `posted` зависит лишь от исключения SQL-helper. `test_recovery_verified_exit_then_flat` заканчивается на верификации 40 shares на третьей секунде: до конца settle, выхода SELL и flat он не доходит.

   Поэтому зелёные тесты не проверяют сеть до/после commit, atomic consumption, реальный attach новой карты и продолжение recovery. Нужны интеграционные сценарии через production seams, durable fake venue и реальный обрыв процесса после принятия POST; tape следует довести до SELL fill, повторной проверки и flat. Обычные codec/reopen unit-тесты полезны, но не заменяют эти проверки.

**Выполненные проверки.** Профильный набор из плана US-006: **361 passed**, две сторонние DeprecationWarning от Nautilus. Строгий `basedpyright`: **0 errors, 0 warnings**. Ruff lint и format check прошли; форматтер проверил 339 файлов. `git diff --check` прошёл. Полный `make test` и четыре extraction replay самостоятельно в этом ревью не запускались; запись об их успехе в progress относится к реализации, а не к моим проверкам.

Четыре дополнительные проверки ожидаемого поведения завершились **4 failed**, подтвердив пункты 2–5. Скрипт: [test_us006_review.py](/private/tmp/test_us006_review.py). Это изолированные воспроизведения production-методов на временной SQLite с минимальным host; они не объявляются полноценным process-crash тестом. Запуск из esports-trader:

```bash
UV_CACHE_DIR=/private/tmp/us006-uv-cache PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:scripts:tests:../prediction-market-backtesting \
uv run --no-sync python -m pytest -p no:cacheprovider \
  /private/tmp/test_us006_review.py -q --tb=short
```

**Рекомендация:** US-006 пока не принимать. В первую очередь соединить executor, транзакционный consumer и recovery coordinator, затем повторить проверку через реальный attach и crash/restart fixtures. Исправления только typecheck не устранят перечисленные дефекты.
