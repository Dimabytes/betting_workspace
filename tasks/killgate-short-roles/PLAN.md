# План части 2: kill gate, короткие горизонты, роли в модели 300 с

## Контекст

Идея пользователя: смерть кэри или мидера важнее смерти саппорта. Часть 1 (`investigations/2026-10-13-role-features/report.md`) показала:

- рынок отыгрывает ролевую премию за 30 с, на 300 с ролевой разницы нет;
- кто умер, модель узнаёт из таблицы GRID с задержкой 8 с;
- модель 300 с уже различает смерть кора и саппорта через нетворс;
- роли можно распределить детерминированно: «история игрока + герой» угадывает 95.8% игроков.

Проверяем бэктестом четыре гипотезы:

- **E1** — kill gate держит блок дольше после таблицы. Вариант: только после смерти героя из топ-2 команды по нетворсу.
- **E2a** — короткая модель 30/60 с как фильтр времени поверх модели 300 с.
- **E2b** — короткая модель вместо модели 300 с.
- **E3** — история смертей в модели 300 с: без ролей и по позициям.

Решения пользователя:

- сначала пишем весь код, потом запускаем всё одной очередью;
- в очереди нет ручных решений: идут все плечи, разбор — после очереди;
- топ-2 в E1 берём из STRATZ. Это оптимистичная версия. Лайв-детектор по таблицам GRID — потом, если плечо выиграет.

Живая торговля и `main` не меняются до решения пользователя. Реализация идёт по `tasks/killgate-short-roles/feature.json`.

## Этапы

1. **Этап 1 — код и входы.** Весь код и тесты. Таблицы, обучение моделей, проверки. Этап кончается, когда у очереди есть все входы.
2. **Этап 2 — очередь и подтверждение.** Один скрипт гонит 12 плеч подряд на сиде 0, потом сиды 1–2 базы. После каждого плеча сразу пишет сравнение. Около 7–9 ч. Потом плечи с плюсом идут на сиды 1–2: около 25 мин на плечо, архив уже посчитан. Сводка — в `progress.txt`. Это последний шаг `feature.json`.
3. **После задачи — разбор.** Отчёт и решение по каждому плечу.

## Плечи

| Плечо | Что меняет | Флаги | Карты | С чем сравниваем |
|---|---|---|---|---|
| `e1-base` | ничего: ручки выключены, есть правка A | — | все | LIVE |
| `e1-h10` | блок держится 10 с после таблицы | `--kill-post-table-hold-s 10` | все | `e1-base` |
| `e1-h20` | то же, 20 с | `--kill-post-table-hold-s 20` | все | `e1-base` |
| `e1-h20top2-stratz` | 20 с, только после смерти топ-2 | `--kill-post-table-hold-s 20 --kill-hold-top2 data/experiments/kill-top2/top2_deaths.parquet` | все | `e1-base`, `e1-h20` |
| `e3-deaths` | +4 колонки истории смертей команды | `--model-dir …/e3-deaths-xp --model-dir-noxp …/e3-deaths-noxp` | все | `e1-base` |
| `e3-roles` | +4 и +43 колонки по позициям | `--model-dir …/e3-roles-xp --model-dir-noxp …/e3-roles-noxp` | все | `e1-base`, `e3-deaths` |
| `e2a-buy-h30`, `e2a-buy-h60` | BUY закрыт, пока короткая модель против | `--short-model-dir …/short-hH-xp --short-model-dir-noxp …/short-hH-noxp --short-horizon-seconds H --short-veto-delta 0.005` | test | `e1-base` |
| `e2a-buysell-h30`, `e2a-buysell-h60` | плюс SELL ждёт, пока короткая модель за рост | те же и `--short-sell-hold-delta 0.005` | test | `e2a-buy-hH` |
| `e2b-h30`, `e2b-h60` | короткая модель вместо 300 с | `--model-dir …/short-hH-xp --model-dir-noxp …/short-hH-noxp --min-abs-delta g --exit-abs-delta x` | test | `e1-base` |

`…` — это `data/new_model/experiments`.

- **test** — 452 карты validation со стартом от D = 1787942975 (2026-08-28 18:49Z). На 451 карте до D учатся короткие модели.
- Плечо на картах test сравниваем с `e1-base` по общим картам. Это честно: случайность карты зависит только от (seed, match_id) (`src/backtest/strategy.py:297`, `src/backtest/signals.py:261-276`).
- Похожее уже проигрывало: метка 120 с в Dota (`docs/experiments/label-horizon-120.md`), метка 60 с в LoL (`docs/experiments/lol-horizon-train.md`). Поэтому E2b стоит в конце очереди.

## Общие правила

1. Worktree `../esports-trader-exp`, ветка `exp-part2`, по `.learnings/worktree.md`. Ссылки `data/{raw,trader,new_processed,archive_index,grid}` и LIVE. `data/backtests` остаётся локальным. `data/new_model` — копия worktree в гите, обучение пишет в неё. Один раз `uv sync --group backtest`.
2. Новые ручки по умолчанию выключены. С выключенными ручками код ведёт себя как раньше. Коммиты раздельные: в `main` потом уйдёт только выигравшее.
3. Модель — research. Production не берём: она видела 872 из 903 карт validation.
4. Ручки бэктеста по умолчанию: delta 0.02, xdelta 0.015, cut 480, minpx 0.45. Папки: `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_<плечо>/`.
5. Запуск плеча: `SEEDS=1 SHARDS=4 ARCHIVE_SHARDS=4 scripts/run_seeds.sh dota <плечо> [флаги]`.
   - Машина: 12 ядер, 32 GB. Пик бэктеста 20–28 GB, поэтому плечи идут строго по одному.
   - Время по логам `rebuild-1012`: архив 31 мин, один сид 12 мин.
6. `run_seeds.sh` в конце сравнивает с LIVE и выходит с кодом 1 из-за разницы манифестов. Это ожидаемо. Успех плеча — файл `seed0/summary.json`.
7. Обучение и бэктесты не пересекаются (`docs/rebuild-order.md:36`). Пик `make train` 15–20 GB.
8. В worktree не запускаем `make prepare` и `make market-data`: там `data/new_processed` — ссылка на основной чекаут. `make train` — только с `--model-dir data/new_model/experiments/<имя>`. Новые данные — в `data/experiments/<имя>/`.
9. Тесты не гоняем во время живой карты (`esports-trader/AGENTS.md:23`). Перед тестами спрашиваем пользователя. `PYTEST_N=8` — только локально.
10. Папка разбора: `$INV = investigations/2026-10-13-killgate-short-roles/`. Скрипты — в `$INV/work/`, запуск из worktree через `make run F=<абсолютный путь>`. Отчёт — `$INV/report.md`. Заметки keep/drop — в `esports-trader/docs/experiments/` после решения.
11. Критерий успеха на 3 сидах, как у kill gate:
    - Δ net PnL > 0 на каждом сиде;
    - парные t-test и Wilcoxon по матчам, p < 0.05;
    - одинаковый знак в когортах `--signal-mode grid_v1` и `--signal-mode schedule --feed-source grid`;
    - худший матч не хуже базы.
    Отдельно смотрим объём покупок, число торгуемых карт, loss rate, markout BUY/SELL на 300 с.

## Этап 1. Код и входы

### 1. Worktree и `TARGET` в `scripts/run_seeds.sh`

- Переменная `TARGET`, по умолчанию `--validation`. Она заменяет жёсткий `--validation` в строках `:73, :80, :87, :103`.
- Плечи E2 идут с `TARGET="--since-match <anchor>"` (`src/backtest/run.py:1418-1429`, отбор `src/backtest/selection.py:122-139`).
- Имя папки остаётся `validation_…_<плечо>` (`run.py:918-954`), поэтому поиск `_archive/DONE` и папок сидов в `run_seeds.sh` работает. `--archives-only` с `--since-match` парсер не запрещает (`run.py:1550-1572`).

### 2. Правка A: паритет grid-v1

- `_grid_v1_kill_gates` (`src/backtest/signals.py:400-423`) при убийстве на одной стороне сбрасывает ожидание другой. Лайв его сохраняет (`src/trader/grid_feed.py:317-325, 347-355`). Без правки удержание в драках не сработает.
- Переносим логику лайва в функцию `_next_wait`.
- Тест рядом с `tests/test_backtest_signals.py:633`: два убийства с разных сторон в пределах 10 с.
- Правка изредка меняет входы модели через `BoardHistory.record_board` (`src/backtest/signal_replay.py:123-126`).

### 3. Ручка B: удержание после таблицы

1. `KILL_GATE_POST_TABLE_HOLD_S = 0.0` в `src/shared/constants/strategy.py`. Поле `kill_gate_post_table_hold_s` в `Follow300Policy` и в обеих фабриках (`src/strategy/policy.py:39-113`).
2. `KillCatchUp(radiant_ns: int | None, dire_ns: int | None)` в `src/strategy/types.py`. Поле `StrategyState.kill_caught_up`, инициализация в `src/strategy/lifecycle.py:72`. В `KillWait` и `digest_state` (`src/trader/core_trace_codec.py:257-285`) не кладём: иначе старые трассы перестанут совпадать.
3. Отметка ставится только на переходе: смерти в прошлом сигнале < awaited ≤ смерти в новом, и `now < until_ns`. Новое убийство на той же стороне отметку не сбрасывает.
4. `_side_gated` (`src/strategy/kill_gate.py:23-24`) = `(now < until and deaths < awaited) or (stamp is not None and now < stamp + N)`. `kill_victims`, `kill_exposure`, `kill_gate_boundary_ns` получают policy. Граница пробуждения включает `stamp + N`. Вызовы: `src/strategy/quoting.py:536, 922, 956`, `src/strategy/scheduling.py:120`.
5. `src/strategy/engine.py:58-59`: `SignalUpdate` обновляет `kill_caught_up`.
6. Кодек трасс: ключ в `_POLICY_KEYS` (`core_trace_codec.py:107-130`), `setdefault(..., 0.0)` для старых трасс.
7. Бэктест: флаг `--kill-post-table-hold-s` (после `src/backtest/run.py:1505`, проверка `_reject_negative`), `resolve_run_policies` (`run.py:443-463`, вызов `:2314`, `None` из `src/backtest/seed0_replay.py:143`), ключ манифеста, `EXPECTED_MANIFEST_DIFFS` (`scripts/compare_backtests.py:49-81`). Образец: `docs/experiments/sell-gate-held-delta.diff`.
8. Тесты в `tests/test_kill_gate.py`: обновить вызовы `:243, :252, :255, :361`. Новые случаи: снятие на `stamp + N`; нет отметки после `until`; повторный сигнал не двигает отметку; отметка переживает `KillGateUpdate` другой стороны; N = 0 работает как сейчас. Плюс `tests/test_core_trace.py`, `tests/test_backtest.py:1025`.
9. Архивы не переизвлекаем: снятие гейта считается из смертей в тиках.

### 4. Топ-2 по данным STRATZ

1. `$INV/work/build_kill_top2.py` пишет `data/experiments/kill-top2/top2_deaths.parquet`: `match_id, is_radiant, death_index, is_top2`.
   - `death_index` — номер смерти стороны по времени: 1, 2, …
   - `is_top2` — жертва входила в топ-2 своей команды по нетворсу за секунду до смерти.
   - Смерти — из `player_death_times` (`src/shared/utils/stratz.py:22`). Нетворс на секунду t−1 — из `collect_player_playbacks` (`src/prepare_dataset/stratz_seconds.py:227`), без playback — `networthPerMinute`.
2. `RawDeltaSignal.top2_deaths_radiant/dire: int = 0` (`src/strategy/types.py:62-69`): сколько из первых k смертей стороны — смерти топ-2. Здесь k — смерти стороны в этом сигнале. Ключи в `_SIGNAL_KEYS` кодека с `setdefault`.
3. Связь по номеру смерти, а не по часам. Так не нужно сводить часы STRATZ и таблицы. Если в сигнале смертей больше, чем в таблице, берём последнее значение.
4. `_assemble_match_signals` (`src/backtest/signals.py:434-486`) заполняет счётчик по `match_id` строки решения (`ModelDecision`, `src/backtest/signal_replay.py:52-61`). Проброс: `MatchSignals`, `resolve_run_signals` (флаг `--kill-hold-top2 PATH`), `DotaMakerConfig`, `_sync_signal` (`src/backtest/strategy.py:1381-1388`).
5. Поле политики `kill_gate_hold_top2_only`. Если оно включено, отметка из п. 3.3 ставится, только если `top2_deaths_<side>` вырос на этом переходе.

### 5. Канал доп. фич для модели 300 с

Копии датасетов не подходят: бэктест отклоняет `--validation-dataset`, когда есть архивные карты (`src/backtest/feed_schedules.py:360-377`). Поэтому одна таблица и три точки подключения.

1. `src/shared/utils/dota_features.py`: `EXPERIMENT_EXTRA_FEATURE_COLUMNS`. `canonical_feature_columns()` отрезает разрешённые доп. колонки в конце списка, её зовёт `history_policy_for_columns` (`:135-142`). `attach_extra_features(frame, extras, key_seconds)`, пропуски → NaN.
2. `src/train_model/train_model.py`: флаг `--extra-features PATH`. Список фич = базовый + доп. (`:397`). Присоединение к обучающим строкам по `second`, к строкам валидации — по `second − lag`. Копия таблицы — в папку модели. Assert: в доп. колонках нет NaN.
3. `src/backtest/signals.py`: проверка каталога по каноническому префиксу (`require_catalog_features`, `:210-213`). `_assemble_match_signals` читает `model_dir/extra_features.parquet` и присоединяет по `second` строки решения. Этот `second` равен M − 10 на grid-v1 (`:531`) и игровой секунде тика на архивных картах (`:592`).
4. Хэши входов:
   - `model_identity_sha256` (`src/shared/utils/gbm.py:484-502`) хэширует членов ансамбля, список фич и лаг. Добавляем SHA `extra_features.parquet`, если файл лежит в папке модели. У старых моделей хэш не меняется.
   - Новые ключи манифеста (`src/backtest/run.py:785-860`): `kill_top2_sha256`, `short_model_sha256` (XP и no-XP), `short_horizon_s`, `short_veto_delta`, `short_sell_hold_delta`, ручки удержания. Проверку `_archive` (`src/backtest/shared_archive.py:117-129`) и `--resume` они проходят сами: `assert_manifest_matches` (`src/backtest/results.py:454-466`) сравнивает все ключи. Те же ключи — в `EXPECTED_MANIFEST_DIFFS`.

### 6. Входы E3: роли и таблицы смертей

**Что модель видит про смерти сейчас** (`src/shared/utils/dota_features.py:100-119`, `src/shared/utils/board_features.py:11-14`): `deaths_radiant/dire` с начала карты, `pending_deaths_*`, `board_death_age_*_s` (максимум 30 с). Истории смертей нет: исторические колонки (`dota_features.py:36-67`) есть только у 10 полей нетворса и опыта.

**Роли** — `$INV/work/assign_roles.py`, метод из части 1 (`investigations/2026-10-13-role-features/work/orchestrator/baseline_assign.py`):

1. Позиции прошлых матчей — из STRATZ (`investigations/2026-10-13-role-features/work/orchestrator/stratz_players.parquet`), только матчи, которые начались раньше текущего.
2. Приор игрока: частоты его позиций в последних 30 матчах, сглаживание +0.5.
3. Приор героя: частоты позиций героя за последние 60 дней, сглаживание +1.
4. Стоимость = −(log приора игрока + log приора героя). Пятёрку расставляет `scipy.optimize.linear_sum_assignment`. Если у игрока нет истории, его приор равномерный.
5. Строки команды сортируем по `player_slot`: одинаковые входы дают одинаковый ответ.

Выход: `data/experiments/roles/assigned_positions.parquet` (`match_id, account_id, hero_id, is_radiant, position`). Метки STRATZ идут только в отчёт о точности: по игроку, по позиции, пятёрками, «кор против саппорта». Отдельно для обучающих карт и validation. В кэше STRATZ дыра 2025-02..2025-09, у ранних карт точность будет ниже.

**Таблицы** — `$INV/work/build_extras.py`. Ключи — объединение ключей `game_history_minutes` и `game_features`. Смерть входит в фичу, если её время ≤ s. Нетворс игрока на секунду s — курсор playback, иначе `networthPerMinute[s//60]` (`nw_at` из `investigations/2026-10-13-role-features/work/astra-value/features.py`).

- `data/experiments/e3/deaths.parquet` — 4 колонки: `deaths_1m_{radiant,dire}`, `deaths_5m_{radiant,dire}`.
- `data/experiments/e3/roles.parquet` — те же 4 и ещё 43:

| Колонки | Сколько |
|---|---|
| `pos{1..5}_deaths_{radiant,dire}` — смерти позиции с начала карты | 10 |
| `pos{1..5}_deaths_1m_{radiant,dire}` — за последние 60 с | 10 |
| `pos{1..5}_deaths_5m_{radiant,dire}` — за последние 300 с | 10 |
| `pos{1..5}_nw_adv` — нетворс позиции Radiant минус та же позиция Dire | 5 |
| `cores_deaths_{1m,5m}_{radiant,dire}` (pos1–3), `supports_deaths_{1m,5m}_{radiant,dire}` (pos4–5) | 8 |

Из STRATZ не берём: `networth_end`, итоговые kills/deaths/last hits, `duration_s`, `radiant_win`.

### 7. Входы E2: метки и короткие модели

1. `$INV/work/label_60s.py`: self-join строк validation по (match_id, `state_ts_us` + 60e6) → `market_p_radiant`. Тот же join на +30e6 должен бит в бит дать `signal_market_p_radiant_30s`.
2. `$INV/work/train_short.py --horizon {30,60} [--no-xp]`:
   - фичи как в `train_model.py:215-222`, `second −= 10`;
   - обучение на 451 карте до D, early stopping — на последних 20% этих карт;
   - в колонку `signal_market_p_radiant_300s` (её ждёт `ResearchMemberFit`) пишем **будущую цену** mid(M+h), а не Δ. `build_price_delta_labels` (`src/shared/utils/gbm.py:133-137`) сам вычитает текущую цену;
   - `fit_research_members`, публикация через `build_ensemble_model_meta(source_lag_seconds=10)` в `data/new_model/experiments/short-h{30,60}-{xp,noxp}`, файл `test_anchor.txt` — первая карта с D.
3. `$INV/work/eval_short.py`, только карты test. Это справка для отчёта, не фильтр:
   - **F1** — выигрыш MAE против прогноза «цена не изменится», CI по матчам (`bootstrap_series_cluster_ci`, `src/shared/utils/market_scenario_report.py:157`), вся карта и M < 480;
   - **F2** — выигрыш против `c·pred300`, c подбираем на картах train;
   - **F3** — правило BUY из п. 8 при v = 0.5¢: средние side·y_h и side·y300 в закрытых и открытых секундах, CI по матчам; доля закрытых секунд;
   - квантили |pred| и доля нулевых меток;
   - пороги E2b в `$INV/work/e2/gates.json`: g и x — квантили |pred_h| на секундах test с M < 480, при которых гейт открыт ту же долю времени, что у research при 2¢ и 1.5¢ (метод `docs/experiments/lol-horizon-scaled-delta.md:19-21`).
4. Все четыре короткие модели обучаются в этом шаге: они лёгкие. Если модель почти всегда даёт |pred| < 0.1¢, её переобучаем с objective `huber`.

### 8. Канал короткой модели: E2a

Шаблон проброса второй модели — `44516897`, плюс `cc689e0c` (кодек) и `10d95344` (whitelist сравнения). Переносим вручную, без cherry-pick: код старше рефакторинга `signal_replay`.

1. `src/strategy/types.py`: причина блока `"short_horizon"`; `RawDeltaSignal.short_delta`, `short_expiry_ns`, по умолчанию None. `Latch` не трогаем.
2. Поля политики `short_veto_delta` и `short_sell_hold_delta`, по умолчанию None. Флаги `--short-veto-delta`, `--short-sell-hold-delta`.
3. BUY: в `_buy_closed_reason` (`src/strategy/quoting.py:206-229`) после проверки `min_delta`. Если `short_delta` есть и `sign(predicted_delta) · short_delta < −short_veto_delta`, вернуть `short_horizon`. Закрытый BUY снимает стоящие BUY (`quoting.py:869-873`). Срок прогноза для BUY не проверяем: BUY требует сигнал моложе 16 с.
4. SELL: токен входит в `KillExposure.sell_blocked` (`src/strategy/kill_gate.py:42-56`), пока `short_sell_hold_delta` задан, `now < short_expiry_ns` и side · short_delta > `short_sell_hold_delta`. Здесь side = +1 для токена Radiant и −1 для Dire. Блок работает так же, как SELL-блок kill gate, включая SELL на join ask. Граница пробуждения включает `short_expiry_ns`, пока блок действует.
5. Кодек: ключи в `_SIGNAL_KEYS` и `_POLICY_KEYS` с `setdefault` None (`core_trace_codec.py:87-96, 107-130, 342-363`).
6. `src/backtest/signals.py`: `MatchSignals.short_deltas`. `_assemble_match_signals` считает короткую модель на тех же строках решений, XP/no-XP через `catalog_has_board`.
7. `src/backtest/run.py`: флаги `--short-model-dir`, `--short-model-dir-noxp`, `--short-horizon-seconds`, проверка каталога, конфиг follow300, ключи манифеста. `src/backtest/strategy.py`: конфиг и `_sync_signal`, срок = `signal_ts + h`.
8. Снятые фильтром BUY видны в `quote_events.parquet` с причиной `short_horizon` (`src/backtest/quote_store.py:217`).

### 9. Скрипт очереди `$INV/work/queue.sh`

1. Плечи строго по одному, в порядке таблицы Этапа 2. Для каждого: `SEEDS=1 SHARDS=4 ARCHIVE_SHARDS=4 [TARGET=…] scripts/run_seeds.sh dota <плечо> <флаги>`.
2. Готовое плечо (`seed0/summary.json` есть) пропускается. Если очередь оборвалась, повторный запуск продолжает с места обрыва.
3. Упавшее плечо не останавливает очередь. Строка в `$INV/work/queue/status.tsv`: плечо, старт, конец, ok/fail. Лог — `$INV/work/queue/<плечо>.log`.
4. После каждого плеча — его сравнения в `$INV/work/compare/`: `compare_backtests.py <база> <плечо> --allow-drift`, плюс разрезы `--signal-mode grid_v1` и `--signal-mode schedule --feed-source grid`. Команда: `PYTHONPATH=src:../prediction-market-backtesting:scripts uv run --group backtest python scripts/compare_backtests.py …`.
5. g и x для E2b скрипт читает из `$INV/work/e2/gates.json`, якорь — из `test_anchor.txt`.
6. `DRY=1` печатает очередь без запуска.
7. Запуск — из worktree, отдельно от сессии агента: `nohup caffeinate -i bash $INV/work/queue.sh >> $INV/work/queue/queue.log 2>&1 &`. `caffeinate` не даёт Mac уснуть. Крышку ноутбука не закрываем, питание подключено.

### Проверки перед очередью

1. Юнит-тесты каждого коммита, когда нет живой карты.
2. «Без изменений»: ручки выключены, одна архивная карта (`--match-id X`), результат равен LIVE сид 0. С прогона LIVE в `main` нет коммитов в `src/strategy`, `src/backtest`, `src/shared`.
3. Смоук: каждый новый флаг на одной карте отрабатывает и меняет манифест.
4. Хэши: меняю строку в таблице — старый `_archive` не принимается.
5. Таблицы E3:
   - сумма нетворса по позициям равна нетворсу команды в датасете, сумма смертей равна `deaths_*`;
   - после присоединения нет NaN;
   - повторный `assign_roles.py` даёт тот же файл бит в бит.
6. Доступность на решениях: сумма смертей по позициям ≤ смертей стороны в таблице, которую видит решение. Долю нарушений пишем в отчёт. Это оптимистичная версия, нарушения не чиним.
7. Точность ролей против меток STRATZ — в отчёт, по периодам.
8. Метки E2: join на +30 с бит в бит равен `signal_market_p_radiant_30s`. На 100 строках цель обучения равна будущей цене минус текущая.
9. Контроль обучения: research без доп. колонок, переобученный, совпадает с `data/new_model/research` по SHA256 файлов `member_*.txt`. Если нет, в очередь добавляется плечо `e3-control` перед `e3-deaths`.
10. Обучение E3 по одному: 2 плеча × XP/no-XP через `make train ARGS="--lag-seconds 10 --training-dataset data/new_processed/dataset/training_dataset.parquet --validation-dataset data/new_processed/dataset/validation_dataset.parquet --model-dir data/new_model/experiments/<плечо>-{xp,noxp} [--no-xp] --extra-features <parquet>"`. Смотрим `mae_gain` и `dir_300` в `model.json`.

## Этап 2. Очередь

| # | Плечо | Карты | Мин |
|---|---|---|---|
| 1 | `e1-base` | все | 43 |
| 2 | `e1-h20` | все | 43 |
| 3 | `e1-h10` | все | 43 |
| 4 | `e1-h20top2-stratz` | все | 43 |
| 5 | `e3-deaths` | все | 43 |
| 6 | `e3-roles` | все | 43 |
| 7 | `e2a-buy-h30` | test | 25–40 |
| 8 | `e2a-buy-h60` | test | 25–40 |
| 9 | `e2a-buysell-h30` | test | 25–40 |
| 10 | `e2a-buysell-h60` | test | 25–40 |
| 11 | `e2b-h30` | test | 25–40 |
| 12 | `e2b-h60` | test | 25–40 |
| 13 | `e1-base`, сиды 1 и 2 по очереди (`SEED_LIST="1"`, потом `"2"`) | все | 24 |

Итого 7–9 ч. Важное идёт первым: если очередь оборвётся, E1 и E3 уже посчитаны.

Сравнения после каждого плеча:

- каждое плечо против `e1-base`; плечи test — по общим картам;
- пары: `e1-h20top2-stratz` против `e1-h20`, `e3-roles` против `e3-deaths`, `e2a-buysell-hH` против `e2a-buy-hH`;
- `e1-base` против LIVE (`…_p45_rebuild-1012`, 886 карт): в разрезе `--signal-mode schedule` должно быть полное совпадение, разрез `grid_v1` показывает эффект правки A.

## Подтверждение и разбор

1. Подтверждение входит в шаг запуска. База к этому моменту уже есть на 3 сидах. Для плеч с Δ net PnL > 0 против `e1-base` на сиде 0 запускаем сиды 1–2 по одному: около 25 мин на плечо. Сравнения пересчитываем на всех общих сидах. Сводку пишем в `progress.txt`.
2. После задачи читаем `$INV/work/queue/status.tsv`, `$INV/work/compare/` и сводку. Пишем `$INV/report.md`: таблица плеч против базы, плюс точность ролей, доля нарушений доступности, F1–F3, `mae_gain`.
3. Решение по критерию из общих правил, п. 11:

| Итог на 3 сидах | Действие |
|---|---|
| `e1-h10` или `e1-h20` выиграл | PR в `main` с этим N, заметка keep |
| `e1-h20top2-stratz` лучше `e1-h20` | делаем лайв-детектор топ-2 по таблицам GRID и проверяем его на архивах |
| `e3-roles` лучше базы и `e3-deaths` | роли помогают; дальше лайв-часть ролей |
| `e3-deaths` лучше базы, `e3-roles` не лучше | в лайв идут 4 командные колонки, роли не нужны |
| `e2a-*` выиграл | план лайва второй модели: `host_resources.py`, `game_profile.py`, `model_server.py`, `match_worker.py`, кодек |
| `e2b-*` выиграл | перепроверка, потом отдельное решение |
| плечо не выиграло | заметка drop с цифрами |

## Чего не делаем

- Лайв-детектор топ-2 по таблицам GRID и переизвлечение архивного индекса — потом, если выиграет `e1-h20top2-stratz`.
- Лайв-часть ролей (таблица «ник → аккаунт», распределение до хорна) — потом, если выиграет `e3-roles`.
- Не чиним нарушения доступности доп. фич, только меряем.
- Не делаем плечо с ролевыми колонками в короткой модели.
- Не меняем `main`, живую торговлю, LoL, production-модель.
