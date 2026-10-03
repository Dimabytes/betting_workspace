# План: история Dota и LoL без дыр, бэктест как лайв

## Context

1 октября модели получили 60 исторических признаков (лаги 1–5 минут). В лайве прогноз дёргается: точка «N минут назад» ищется с допуском 16 с, а GRID присылает таблицу редко и с дырами. Признак то есть, то `NaN`. Обучение идёт на полной ленте, где такого нет.

Замер на архиве (`data/archive_index/schedules`, окно до 479 с):

| | Dota GRID, 295 карт | LoL GRID, 405 карт | Dota Oddin, 71 карта |
|---|---|---|---|
| Первый игровой тик | медиана +48 с (p10 +8, p90 +61) | медиана +4 с | каждую секунду с −90 с |
| Начало непрерывного фида | медиана +55 с | медиана +76 с (p10 +61, p90 +80) | −90 с |
| Невалидных тиков сейчас (последний до цели, 16 с) | 35,6% | 55,5% | 0% |
| Невалидных тиков по новому правилу | 0,01% | 0,06% | — |
| Средняя ошибка опорной точки по новому правилу | 4,1 с | 2,5 с | — |

У LoL 259 из 405 карт имеют дыру на старте: тик на +4 с, потом тишина до +75 с (медиана).

## Правило истории (одно для обучения, бэктеста и лайва)

Для модели задана политика истории `HistoryPolicy` (frozen dataclass): `start_second`, `max_pivot_gap_seconds`, `drop_gap_ticks`.

1. Лента истории не берёт снимки с секундой меньше `start_second`.
2. Цель лага `t − 60k` меньше `start_second` → `NaN`.
3. Иначе опорная точка — ближайший полученный снимок до или после цели, в пределах `max_pivot_gap_seconds`.
4. Точки нет, и цель раньше первого записанного снимка → `NaN`. Это прогрев, тик валиден.
5. Точки нет, и цель не раньше первого записанного снимка → тик невалиден, если `drop_gap_ticks`. Невалидный тик не даёт нового прогноза. Сырой снимок остаётся в ленте истории.

| Модель | Фиды | `start_second` | `max_pivot_gap_seconds` | `drop_gap_ticks` |
|---|---|---|---|---|
| Dota `production` / `research` (77) | GRID, Steam | 60 | 30 | да |
| Dota `production-noxp` / `research-noxp` (70) | Oddin | −60 | 15 | нет |
| LoL `lol-map` | GRID | 60 | 30 | да |

- 60 — первая целая минута после старта фида. Минутная лента обучения Dota не позволяет взять другое число между 0 и 60.
- Oddin: правило и обрезку не применяем. `start_second = −60` — начало его обучающей ленты. Поиск «ближайший» на посекундной ленте Oddin даёт те же точки, что сейчас.
- Торговлю не запрещаем. Модель торгует с первого игрового тика. До появления истории все 60 признаков — `NaN`, как в строках обучения на 0 и 60 с.

## Шаги

### 1. Общий код истории — `src/shared/utils/dota_features.py`
- `HistoryPolicy` и три константы политик из таблицы выше: `GRID_HISTORY_POLICY` (Dota GRID/Steam и LoL), `ODDIN_HISTORY_POLICY`.
- `history_feature_block` (:172): новый аргумент `start_second`. Строки ленты раньше `start_second` не участвуют. Цели раньше `start_second` дают `NaN`. Поиск ближайшего снимка с двух сторон от цели вместо «последний не позже цели».
- `attach_history_features` (:288) и `attach_catalog_features` (:324) передают `start_second` дальше.
- `SnapshotHistory` (:214) принимает `HistoryPolicy`. `record` не пишет снимки раньше `start_second` и запоминает первую записанную секунду. Новый метод `check_required_lags(second) -> bool` реализует пункты 4–5 правила. При `drop_gap_ticks = False` он всегда возвращает `True`.
- `replay_tape_history` (:263) принимает `HistoryPolicy`.
- Тесты в `tests/test_dota_features.py`: обрезка до `start_second`; ближайший снимок после цели; прогрев валиден; дыра после старта невалидна; после нового тика снова валиден; посекундная лента даёт те же точки, что раньше.

### 2. Лайв — `src/trader/match_worker.py`, `src/trader/game_profile.py`
- Политика задаётся у профиля модели в `game_profile.py`: `dota-map` → `GRID_HISTORY_POLICY`, `dota-oddin-map` → `ODDIN_HISTORY_POLICY`, `lol-map` → `GRID_HISTORY_POLICY`. `SnapshotHistory` (match_worker.py:189) создаётся с политикой профиля вместо `feed_timeout_seconds`.
- `_on_event` (:421): после `_maybe_start_prior` и `_maybe_verify_recovery`, до `self._watchdog.consume()`. Если тик не `finished` и `check_required_lags` вернул `False`: записать строку `history_gap` в журнал и выйти.
- Результат: нет `SignalUpdate`, нет публикации в cell, watchdog не перезапускается. BUY снимается через 16 с, SELL через 45 с от последнего валидного тика.
- Тест в `tests/test_trader_match_lifecycle.py` рядом с `test_feed_timeout_keeps_fair_and_sell_until_exit_timeout` (:902): тик с дырой истории не трогает SELL и не перезапускает watchdog.

### 3. Бэктест = лайв — `src/backtest/signals.py`, `src/backtest/feed_schedules.py`, `src/backtest/strategy.py`
- Политика выбирается вместе с моделью: `RESEARCH_MODEL_DIR` и LoL → `GRID_HISTORY_POLICY`, `RESEARCH_NOXP_MODEL_DIR` → `ODDIN_HISTORY_POLICY`. Её получают `_schedule_tape_history` (signals.py:648) и `_attach_decision_features` (:381) вместо `entry_stale_seconds` и `timing.max_age_seconds`.
- Невалидный тик остаётся в ленте истории и убирается из тиков фида и решений. `_sync_signal` (strategy.py:1109) не вызывает для него `_clear_signal`. `recovery_stale_mask` (signals.py:334, :696) считает разрыв от последнего валидного тика.
- Первый тик grid-v1: Dota +48 с (медиана первого игрового тика), LoL +76 с (медиана начала непрерывного фида). Константы `DOTA_GRID_V1_FIRST_TICK_SECOND = 48` и `LOL_GRID_V1_FIRST_TICK_SECOND = 76` рядом с `DOTA_GRID_V1_BANDS` (:94). Строки со снимком раньше первого тика убираются до `select_cadence_rows` (:534) и `_grid_v1_kill_gates` (:575).
- Старт архивной карты как в лайве. Первый тик фида никогда не решение: в лайве он только подключает (match_worker.py:235–246). Разрыв от первого тика не делает следующий тик устаревшим: в лайве watchdog до первого `consume()` не взведён.
- Тесты: `tests/test_backtest_signals.py` (первый тик grid-v1 по игре), `tests/test_feed_schedules.py` (невалидный тик не сбрасывает сигнал; старт архивной карты), `tests/test_backtest_maker.py`. Перезаписать golden-файлы `tests/fixtures/follow300_changes/smoke/`.

### 4. Обучение
- Dota, `src/train_model/train_model.py` (:195–207, :300–305): `attach_catalog_features` получает `start_second` политики пары каталогов. Пара с XP → 60. Пара no-XP → −60: её модели не меняются.
- LoL, `src/lol/06_train_model.py` `load_lol_dataset` (:129): `start_second = 60`.
- Допуск в обучении не меняется: ленты полные, точки точные.
- Обучение остаётся детерминированным, случайности нет.
- `make prepare` и `make lol-prepare` не нужны: ленты те же, обрезка идёт при расчёте признаков.

### 5. Пересборка и A/B — `docs/rebuild-order.md`, шаги 5–7
- `make train`, `make lol-train`.
- Новый бэктест, 3 seed, прогоны по одному:
  - Dota: исправленная hist77; oldcat12 (файлы `data/new_model/archive/research/20260930T180848Z` и `archive/research-noxp/20260930T180911Z` кладём в `data/new_model/research*` на время прогона, как в A/B 2 октября); текущая hist77.
  - LoL: исправленная модель; текущая LIVE-модель `20261002T190131Z`.
- Сравнить чистый PnL, PnL без rebate, CVaR5, худшую карту, капитал. Отдельно — только архивные карты с реальными тиками.
- Решение о выкатке за пользователем. Если oldcat12 лучше исправленной hist77 — исторические признаки Dota убираем отдельной задачей.

### Лайв до A/B
- Ничего не выкатываем и трейдер не перезапускаем.
- Код коммитим в `../esports-trader` на `main` локально, без push.

### Не делаем
- Отдельную Steam-модель: Steam использует Dota-модель 77 и её политику.
- Контрольные сценарии +40/+60, случайное расписание GRID в обучении, интерполяцию.

## Известный предел
В лайве опорная точка в среднем отстоит от цели на 4,1 с (Dota) и 2,5 с (LoL), в обучении — точная. Бэктест эту ошибку уже содержит: grid-v1 прореживает посекундную ленту, архив использует реальные тики.

## Проверка
1. `uv run pytest tests/test_dota_features.py tests/test_feed_schedules.py tests/test_backtest_signals.py tests/test_backtest_maker.py tests/test_trader_match_lifecycle.py tests/test_follow300_replay.py` — не во время лайв-карты.
2. Скрипт `cut_sweep.py` из scratchpad на новом коде: невалидных тиков Dota GRID ≈ 0,01%, LoL ≈ 0,06%.
3. Архив LGD–Xtreme `grid-3011816-m3` через новый `SnapshotHistory`: на 144/147/188 с история не переключается в `NaN`.
4. A/B из шага 5.
