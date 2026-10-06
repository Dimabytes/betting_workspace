# STEP-001 — Расширить существующую запись signal

## Цель и границы

Добавить в уже существующую строку `kind=signal` точное время записи, происхождение принятого игрового снимка, сам нормализованный снимок и результат текущего вызова модели. Следующий шаг сможет читать доказанные входы решения без восстановления по mtime или повторного inference.

План относится только к STEP-001, priority 1, `passes: false`. Все шесть шагов ещё pending; `progress.txt` в папке задачи отсутствует. Это исходное состояние, а не основание создавать фиктивный журнал прогресса. `plan.instructions` в `.feature-json.config.json` пуст.

Прочитаны `feature.json`, весь `plan.md`, workspace/code AGENTS.md и заданный `feature-json-create-step-plan/SKILL.md`. В `designReference` нет ссылок Figma. UI, зависимости dashboard, база, стратегия, размещение/отмена ордеров, checkpoint, денежный учёт и deployment относятся к последующим шагам или исключены из задачи. `poly-maker` остаётся read-only. Этот документ — план; код и коммиты при планировании не создаются.

## Проверенное состояние кода

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, HEAD `7f93781d39e04d1ce80fcd0eaf2aca3b988c48c3`; рабочее дерево чистое при чтении.

| Место | Текущее поведение и следствие для изменения |
| --- | --- |
| `src/trader/live_feed.py` | Замороженные `FeedEvent` и `GameSnapshot` уже содержат нужные snapshot, `source: FeedSource` и `received_at_utc: str`. Live-источники: GRID/Oddin. Новый сбор данных не нужен. |
| `src/shared/utils/top_players.py` | `TopPlayerFeatures` уже содержит все шесть требуемых полей `top`; их значения надо копировать, не пересчитывать. |
| `src/trader/session_types.py:SignalDecision` | Уже хранит `snapshot` и `arrived_at`, но не feed metadata и не raw delta. `ModelFair` содержит только Radiant/YES fair. |
| `src/trader/match_worker.py:_latch_fair` | Единственный вызов `predict_fair`; результат содержит `raw_delta` и clipped `fair`. Delta сейчас пишется в `_last_raw_delta`, который сохраняется после skip/error. |
| `match_worker.py:_compute_decision` | Получает исходный `FeedEvent`, вызывает `_latch_fair`, возвращает `SignalDecision`; здесь доступны provenance и текущий результат. |
| `match_worker.py:_quote_pm` | Публикует cell, ставит core signal, через `replace` добавляет preview `entry_block`, пишет один signal и будит quoter. Порядок сохраняется. |
| `match_worker.py:_quote_board` | Повторно передаёт сохранённый `_BoardSource.event`; обновляет настенное время решения, но сохраняет `source.received_ns`. Механизм уже сохраняет исходную таблицу. |
| `src/trader/session_journal.py:write_signal` | Строит typed record и один раз вызывает `self.write(record)`. `SESSION_SCHEMA_VERSION=7` описывает provenance. |
| `src/trader/archive_paths.py:FsyncedJsonlWriter` | Одна compact JSON-строка, один write, flush и fsync на существующую запись; `sanitize_nonfinite` рекурсивно обрабатывает вложенные значения. Менять writer не требуется. |
| `src/trader/archive_types.py:SessionSignalRecord` | TypedDict; существующие `venue`, `exit_state`, `pos_yes`, `pos_no` уже `NotRequired`. Новые поля должны следовать этому подходу. |
| `src/viewer/live_tape.py` | Сигналы читаются через `.get` существующих ключей, без проверки exact key set; лишние ключи не ломают legacy tape. Текущий viewer выводит собственный fair-minus-market delta, менять его в этом шаге не надо. |

`tests/trader_session_fixtures.py` предоставляет offline `build_attached_worker`, `build_event`, `FakeModelServer` с `calls`/`fair_args`/`board_args`, и `read_session_records`. Engine в этих тестах не запускает сеть. `tests/test_trader_match_lifecycle.py` уже проверяет одну модельную оценку на table и reuse таблицы после board; эти проверки следует сохранить и запустить вместе с новыми.

## Контракт записи

Все новые ключи присутствуют в новых signal, включая явный `raw_delta: null` при отсутствии успешного расчёта. В старых строках все шесть ключей могут отсутствовать.

| Поле | Источник и тип |
| --- | --- |
| `recorded_at_utc` | UTC timestamp, сформированный внутри `write_signal` непосредственно при построении записи; ISO-8601 с микросекундами и `Z`. Это не `arrived_at`, server timestamp или receipt. |
| `feed_source` | `event.source`, существующий `FeedSource`; JSON `grid`/`oddin`. Не брать из статической metadata вместо события. |
| `feed_received_at_utc` | Точная исходная строка `event.received_at_utc`, без повторного форматирования/замены на now. |
| `game_snapshot` | Явная ограниченная проекция того же `decision.snapshot`, который участвовал в расчёте/гейтах. |
| `model_evaluated` | `true` при успешном возвращённом прогнозе этого решения, `false` при skip или `ModelPredictionError`. Сам факт попытки вызова не означает `true`. |
| `raw_delta` | `prediction.raw_delta` текущего успеха, иначе `None`. Ноль — валидный расчёт; не использовать truthiness. Не менять знак по YES/NO. |

`game_snapshot` содержит ровно `second: int`, `server_timestamp: int`, `phase` (wire value существующего `MatchPhase`), `paused: bool`, `radiant_nw_adv: int`, `radiant_nw: int`, `dire_nw: int`, `radiant_xp_adv: int`, `deaths_radiant: int`, `deaths_dire: int`, `top`. В `top`: `top1_nw_adv: int`, `radiant_top1_nw_ratio: float`, `dire_top1_nw_ratio: float`, `top3_nw_adv: int`, `radiant_top3_nw_ratio: float`, `dire_top3_nw_ratio: float`.

Существующий верхний `second` сохраняется и равен `game_snapshot.second`. История, игроки, scoreboard pending deaths и модельный вектор 70/81 признаков в запись не добавляются. В NoXP-снимке сохраняется исходное значение XP; смысл «не используется этой моделью» относится к последующей сводке, не к изменению source snapshot.

## Порядок реализации

### 1. Добавить типы без расширения торгового контракта

- В `session_types.py` добавить в `SignalDecision` обязательные поля `feed_source: FeedSource`, `feed_received_at_utc: str`, `model_evaluated: bool`, `raw_delta: float | None`. Существующий `snapshot` уже передаёт игровую часть исходного `FeedEvent`, дублировать полный event в dataclass не нужно.
- Добавить обязательный `raw_delta: float` в `ModelFair`, чтобы переносить успешный результат вместе с fair через существующий `_LatchedFair`. Альтернатива с отдельным optional delta в `_LatchedFair` создаёт лишние комбинации «нет fair, есть delta»; предпочесть поле успешного результата.
- В `archive_types.py` добавить два именованных вложенных TypedDict для игрового снимка и top. Использовать явные поля с конкретными типами, существующие `MatchPhase`/`FeedSource` допустимы как StrEnum wire-типы. Не вводить `dict[str, Any]`, anonymous tuple или универсальный codec.
- В `SessionSignalRecord` все шесть новых ключей объявить `NotRequired[...]`; `raw_delta` дополнительно допускает `None`. Ключ `model_evaluated` optional по присутствию, но если присутствует — bool. Остальные новые scalar поля не требуют nullable fallback для новых записей.
- Обновить единственный ручной constructor `SignalDecision` в `_pm_signal` (`tests/test_trader_session_journal.py`) с явными значениями. Не добавлять defaults в production dataclass ради старого test helper.

### 2. Провести текущий результат через решение

- В успешной ветке `_latch_fair` взять `prediction.raw_delta` из уже полученного результата и передать в `ModelFair` вместе с Radiant/YES fair. Существующую запись `_last_raw_delta` оставить в том же месте: core сейчас использует её в `_enqueue_core_signal`; переподключение исполнения не входит в этот шаг.
- Не добавлять второй `predict_fair`, сброс shared cache, повторную модельную проекцию, изменения exception handling или торговых гейтов. Skip/error возвращают прежний `_LatchedFair(None, reason)`.
- В `_compute_decision` заполнить source/receipt непосредственно из `event`. Определить `model_evaluated = model_fair is not None`; взять `model_fair.raw_delta` при успехе, иначе `None`. Для читаемости использовать именованные промежуточные значения.
- Все существующие reason/fair/position/exit поля оставить с прежним смыслом. `replace(decision, entry_block=...)` автоматически сохраняет новые поля.
- Не менять `_quote_board`, `_BoardSource`, stale guard, watchdog и source ns. Новый timestamp журнала снимается отдельно; повторное решение получает исходный receipt таблицы. Board reaction — существующий повторный inference и существующий signal; запрет дополнительных вызовов относится к добавлению диагностики, а не к удалению этой реакции.

### 3. Сериализовать в существующий signal

- В `write_signal` сформировать UTC-время через `datetime.now(UTC)` с микросекундами и `Z` по существующему формату проекта. Не переиспользовать fill-specific helper с неверным доменным названием, не добавлять новую публичную clock API ради одного вызова.
- Построить typed nested dict из `decision.snapshot` и `snapshot.top`, с явным whitelist полей. Это фиксирует контракт и избегает `asdict` с глубокой копией и автоматическим добавлением будущих полей snapshot.
- Добавить ровно шесть ключей в существующий `SessionSignalRecord` и оставить единственный `self.write(record)`. Не менять `FsyncedJsonlWriter`, его санитизацию, flush/fsync и обработку journal fault в `_quote_pm`.
- Сохранить `SESSION_SCHEMA_VERSION=7` и accepted provenance versions: сигнал расширяется optional-полями, а формат/валидация `session_start` не меняются. Resumed архив вправе содержать старые и новые signal рядом, без переписывания исторических строк.
- Не исправлять legacy viewer delta, не менять графики и не добавлять reader dashboard в этом шаге. Истинный `raw_delta` может отличаться от `radiant_fair - market_p_radiant` из-за clipping — это причина логировать результат явно.

### 4. Добавить целевые регрессии

`tests/test_trader_session_journal.py`:

1. Создать несимметричный snapshot со всеми ненулевыми полями top; сравнить весь вложенный объект с источником, exact key sets и типы JSON enum/bool/numbers. Параметризовать GRID/Oddin. Все существующие поля проверить на сохранение.
2. Зафиксировать journal clock на известном UTC позже receipt. Проверить точные разные `recorded_at_utc` и `feed_received_at_utc`, parseability через существующий `parse_utc`; отдельно задать несвязанный `arrived_at`, чтобы тест ловил подстановку неверного clock. Не полагаться на sleep или случайно разные реальные timestamps.
3. Через recording handle и monkeypatched fsync проверить один signal = одна строка, один `write`, `flush`, `fileno`, `fsync`; считать только вызов `write_signal` после подготовки journal, без startup/close. Прежняя durability остаётся одной операцией на запись.
4. Проверить успешный нулевой raw delta (`model_evaluated=true`, `raw_delta=0.0`) и false/null при пропуске. Проверить, что последующая ошибка не меняет число signal произвольной новой диагностической строкой.
5. Сформировать legacy `SessionSignalRecord` без всех новых ключей как typed literal с существующими обязательными полями, прочитать через используемый JSON object reader; значения `.get` отсутствуют, не становятся нулём/now. Добавить рядом новый signal и проверить чтение смешанного архива. Для runtime совместимости использовать существующий reader `viewer.live_tape` (локально доступную `_reduce_journal` с подходящим clock/tokens) либо public `load_live_tapes` на минимальном fixture из `test_live_inspect.py`; не писать свой «parser», который лишь повторит `json.loads`. Типы optional дополнительно проверит whole-project basedpyright.

`tests/test_board_signal_contract.py`:

1. Использовать `build_attached_worker`, offline ready token books и детерминированные clocks. Выполнить один table `_on_event` с удовлетворёнными history lags, затем один управляемый `_quote_board`. Подобрать existing fixture pattern из `test_live_board_uses_received_snapshot_without_rearming_watchdog`.
2. Проверить обе реальные journal строки: один и тот же source, исходный receipt и game snapshot; разные текущие record times; successful model flags и соответствующие raw deltas. Менять model result между вызовами, чтобы доказать принадлежность delta именно текущему вызову.
3. До/после: ровно один model call/один signal для table, ровно ещё один call/один signal для допустимой board реакции. Сохранить source ns/watchdog generation. Aged table, очищенный source после history gap и отсутствующий source не создают дополнительных call/signal. Отменять собственные board timers в тесте, чтобы callback не исполнился второй раз вне управляемого вызова.
4. Последовательность success → skip → success → `ModelPredictionError` на одном worker. Параметризовать реальные skip-гейты: stale, missing book/prior, pre-match/pre-horn вне model window, paused и finished. Проверить reason, false/null и отсутствие второго model call на gated decision. Не рассчитывать journal delta из cache даже если `_last_raw_delta` всё ещё хранит успешное значение. Ошибка модели даёт один attempted call и один обычный model_error signal.
5. Успешный расчёт при BUY entry block (например cutoff/min_delta) остаётся `model_evaluated=true`: отсутствие входа не равно пропуску модели. Использовать существующее поведение core preview, не новую стратегию.
6. Проверить `yes_is_radiant=false` и прогноз с clipping: raw delta берётся без изменения знака и без восстановления из clipped fair. Успех с raw delta 0 отдельно исключает truthiness bug. Охватить Dota GRID, Dota Oddin и LoL GRID для одинакового нейтрального контракта; NoXP не требует новых model features.

Новые тесты — конкретные проверки observable contract, без mirrored implementation тестов каждой строки. Существующие fake helper'ы переиспользовать; если нужен success-then-error predictor, предпочесть небольшой typed scripted fake/monkeypatch без изменения production API и без новых suppression/comment annotations. Именованные frozen dataclass нужны для новых собственных многочастных данных; не копировать старые `Any`/tuple conventions в новый код.

## Verification и стоимость

Все команды из `esports-trader`, Python только через project venv. На этой машине по контексту нет live map. В будущей реализации перед suite снова убедиться в этом условии; VPS/SSH не нужны. Не запускать engine/feed/gateway, использовать только временные архивы/SQLite fixture и fake model/books.

1. Целевые новые тесты и существующие инварианты:

   ```sh
   PYTEST_N=0 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_trader_session_journal.py tests/test_board_signal_contract.py tests/test_trader_session_quoting.py tests/test_trader_match_lifecycle.py tests/test_live_inspect.py
   ```

   `--group backtest` нужен уже существующему import `backtest.run` в board tests; это тестовая зависимость, не новая зависимость live/dashboard. Новые проверки обязаны пройти; дополнительный whole suite — только при отсутствии live map и если обнаружена причина расширить проверку.

2. Whole-project strict typecheck соответствует repo hook:

   ```sh
   uv run python -m basedpyright
   ```

3. Read-only lint/format check для изменённых файлов (добавить фактические helper файлы, если менялись):

   ```sh
   uv run python -m ruff check src/trader/session_types.py src/trader/archive_types.py src/trader/match_worker.py src/trader/session_journal.py tests/test_trader_session_journal.py tests/test_board_signal_contract.py
   uv run python -m ruff format --check src/trader/session_types.py src/trader/archive_types.py src/trader/match_worker.py src/trader/session_journal.py tests/test_trader_session_journal.py tests/test_board_signal_contract.py
   git diff --check
   ```

4. Локальное измерение размера и задержки, отдельный scratch benchmark (не production exporter и не timing assertion в pytest). Сравнить старую проекцию и расширенную на одинаковом детерминированном потоке representative snapshots: модельный успех, skip/error, GRID/Oddin/LoL, разные размеры цифр, ненулевые top ratios. Использовать компактную JSON-сериализацию и существующий `sanitize_nonfinite`; для end-to-end — настоящий writer с fsync исключительно в новых временных файлах.
5. Зафиксировать число samples, warmup/repetitions, старые/новые bytes per line (mean/max и прирост), serialization p50/p95 и journal write p50/p95, размеры файлов и одинаковое число writes/flush/fsync. Прогноз extra disk = measured extra bytes × фактический/заданный rates table+board signal, явно указать rate и среду. Не выводить VPS performance из ноутбука; без произвольного абсолютного latency budget, которого в spec нет. Существенную регрессию исследовать/устранить до закрытия шага; shared disk fsync noise отличать от projection/serialization cost.
6. Review diff: только оговорённые production четыре файла плюс целевые tests/минимально нужные fixtures; нет смены условий торговли, новых model calls, journal records, HTTP/WS, checkpoint/db schemas или изменённого writer. `feature.json`, progress и commit оформляет отдельная implementation/orchestration роль, не planner.

Browser/curl проверка здесь неприменима: STEP-001 не создаёт экран или HTTP endpoint. Приёмка полностью локальная. VPS upload, restart, tmux, accounts и реальные заявки исключены.

## Риски, решения и готовность

- Основной риск — stale delta после успешного решения: устраняется локальным результатом `ModelFair`, проверяется последовательностями на одном worker. Не исправлять execution cache в рамках диагностического изменения.
- Повторная board оценка может иметь новое время решения и новое Δ на старой таблице; это намеренный контракт, доказываемый двумя раздельными timestamps. Receipt не заменяется на время kill tick или callback.
- Skip/model error не равны history gap: `_on_event` сейчас при history gap пишет только `history_gap` и выходит; STEP-001 не добавляет туда signal. Неожиданная trading exception обрабатывается прежним fault path, искусственный signal не создаётся.
- Raw delta остаётся Radiant/model oriented и unclipped, даже если YES противоположен или fair упёрся в границу. Нулевой прогноз — известное значение; отсутствие — null/отсутствующий ключ старой строки.
- `entry_block` остаётся предварительной BUY причиной до wake quoter. Snapshot position/exit поля не становятся актуальным статусом ордеров или объяснением отсутствия SELL.
- UTC timestamp снимается при формировании record, а не доказывает момент fsync или биржевого подтверждения. Wall clocks могут иметь одинаковые значения/коррекции; обязательство — независимые источники времени, не принудительное искусственное различие каждого реального timestamp.
- Explicit nested projection увеличит строки и синхронную стоимость записи; benchmark обязательный измеримый артефакт реализации. Полный model vector/raw payload запрещены.
- Backward compatibility достигается optional fields и additive payload без provenance bump/rewrite. Legacy reader не обязан отображать новые данные в этом шаге.

Шаг готов к закрытию, когда контракт всех шести полей подтверждён, legacy/mixed archives читаются, успех/skip/error и board freshness регрессии проходят, model/write/flush/fsync counts прежние, замер стоимости приложен, lint/typecheck/tests проходят и diff не меняет торговое поведение.
