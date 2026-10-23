# STEP-013 — бэктест CS2: `--game cs2`

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `29421348` (после фикса STEP-012).
Образец: LoL-ветка бэктеста (`src/backtest/lol_inputs.py`, ветки `game == "lol"` в `src/backtest/run.py`). Figma нет.
Пробы планировщика: `orchestrate/work/plan013/` (`probe_seconds_delay.py`, `probe_onchain_days.py`, `probe_book_rows.py`).

## 1. Что делает шаг

`python -m backtest.run --game cs2 …` гоняет тот же Follow300 (`DotaMakerStrategy`) на книгах Telonex карт CS2 из validation:

1. Выборка: карты `split == "validation"` из `data/cs2/datasets/split.parquet`, у которых есть строка каталога, строка s10 со статусом `ok`, все дни Telonex на диске и книга не тоньше порога фреймворка.
2. Фид: одна строка s10 = один тик фида, 1 Гц, задержка 0. Решение модели — на каждом тике, где нет паузы и живая L2-книга даёт mid.
3. Пауза: колонка `paused` из s10, сдвинутая на +1 с, идёт в `ObservedClockTape`. Пауза снимает заявки (`_blocked(reason="paused")`), позиция остаётся.
4. Kill gate нет. BUY разрешён до конца карты. Позиции рассчитываются по `team_a_win` в конце карты.
5. `summary.json` получает разрез `by_link_source` (gamma / allseries) и строку `map_end_300s` (сделки последних 300 с карты, для всех игр).
6. `scripts/run_seeds.sh cs2 <name>` и `make cs2-backtest` пишут в `data/backtests/cs2_maker/`.

Новые файлы: `src/backtest/cs2_inputs.py`, `tests/test_cs2_backtest.py`. Правки: `run.py`, `signals.py`, `strategy.py`, `context.py`, `postprocess.py`, `report_types.py`, `report_capital.py`, `telonex_local.py`, `paths.py`, `shared/constants/strategy.py`, `cs2/constants.py`, `lol_inputs.py`, `selection.py`, `scripts/run_seeds.sh`, `Makefile`, 6 тестов с `MarketContext(`.

## 2. Что я проверил (факты)

1. **Точки входа в `run.py`:**
   - `Game = Literal["dota", "lol"]` и `BACKTEST_LEVEL_USDC` — `:236–237`.
   - `RunSelection` (`:279–291`) несёт ids, coverage, `capture_root`, `report_root`, `signal_rows`, `load_lookups`, `shard_weights`, `model_dir`, `lag_seconds`, `archive_join`. Конструкторы только в `run.py:1597` и `:1631`.
   - `build_strategy_configs` (`:487`). Ветка без расписания (`:526–543`): `buy_cutoff_ns = get_state_available_ts(horn, policy.buy_cutoff_second, pauses)`, `game_end_ns = game_ended_at`, `observed_clock = None`.
   - `run_batch` (`:591`) требует одну задержку `seconds_delay` на батч. `MIN_BOOK_EVENTS = 25` (`:222`), `MAX_MATCHES_PER_BATCH = 1`.
   - `build_run_manifest` (`:746`): общий словарь + payload Dota или LoL. `"buy_cutoff_second"` и `"signal_contract"` лежат в общем словаре (`:786`, `:801`).
   - `format_validation_run_name` (`:879`) пишет `_cut{policy.buy_cutoff_second}`. `build_report_dir` (`:894`): 1 вызов в `src`, 7 в `tests/test_backtest.py:980–1043`.
   - CLI: `--game choices=("dota","lol")` (`:1390`). Проверки `args.game == "lol"` в `_pin_cli_model_dir` (`:1320`), `_pin_dota_experiment_flags` (`:1349`), `_check_match_ids_file` (`:1531`).
   - `load_run_selection` (`:1575`), `resolve_run_signals` (`:2041`), `main` (`:2115`). `main` зовёт `resolve_feed_plans(selection.archive_join, …)` (`:2156`). Карта без решений получает `EMPTY_SIGNAL_TAPE_STOP_REASON` (`:2293–2313`).
2. **Стратегия.**
   - `ObservedClockTape(game_seconds, paused, terminal)` — `strategy.py:182`. `DotaMakerConfig.__post_init__` требует длину ленты = длине `feed_timestamps_ns` (`:242–249`).
   - Каждый тик фида ставит будильник `SIGNAL:` (`:353–366`). Будильник даёт `ClockUpdate` с `_clock_at` (`_sync_inputs`, `:888–889`).
   - `_clock_at` с лентой берёт `bisect_right(feed_timestamps_ns, now) - 1` (`:1271`). `game_ended = terminal or now >= game_end_ns` (`:1278`).
   - `ClockUpdate` с паузой исполняет план сразу (`:149–152`). `requote` при паузе возвращает `_blocked(reason="paused")` (`src/strategy/quoting.py:955–956`).
   - `on_start` падает на пустом `signal_timestamps_ns` (`:341–344`). Поэтому карта без решений обязана уйти в путь пустой ленты.
3. **Генератор сигналов Dota не годится для CS2.** `replay_received_snapshots` берёт уровни Dota через `history_levels` (`signal_replay.py:85`) и смерти из `values["deaths_radiant"]` (`:137`). Решение в нём — `live_pair_mid(decision_ns, view_delay 10 мс, PAIR_SUM_TOLERANCE)` (`:141`). `live_pair_mid` (`shared/utils/live_pair_mid.py:41`) не режет широкий спред.
4. **`select_cadence_rows`** (`signals.py:301`) при `mean_interval_seconds == 1` только сортирует по `(match_id, second)` (`:314`). `BacktestGame = Literal["dota","lol"]` (`signals.py:67`).
5. **Модель CS2 (s11).**
   - 22 признака: `CS2_MARKET_FEATURE_COLUMNS` (3 рыночных, `s11_train_model.py:57`) + 19 игровых (`:62–83`).
   - `compact_column`: bool как есть, int → int16, иначе float32 (`:201–206`). `compact_features` (`:209`).
   - `source_lag_seconds=0` (`:537`). Research пишется в `models_dir / "research"` (`:563`).
   - `predict_model_deltas` сверяет имена признаков модели с переданным списком (`signals.py:250–257`).
6. **Рынок в обучении** (s09). Mid секунды = `resolve_market_pair` (`telonex_book.py:564`) со стопами по возрасту, спреду (`:572`) и паре. Изменение за 10 с — только если обе секунды `ok` (`s09_build_market_data.py:194–201`). Ноль часов s10 = `compute_zero_second` = `ceil(spawn_at)` (`s09…:149`). Последняя секунда = `compute_last_second` = `floor(ended_at) − zero` (`:153–155`).
7. **seconds_delay (F7).** `probe_seconds_delay.py` на `data/cs2/universe/markets.parquet` (120 508 строк):
   - `map_winner`: 1 с — 9 710, 3 с — 9 702, null — 104;
   - `series_winner`: 3 с — 6 214, 1 с — 5 806, null — 66.

   У всех 170 рынков с null-задержкой `market_slug` = null, `market_closed_at` = null и победитель не задан. s06 выкидывает такой рынок как `market_unresolved` (`s06_publish_catalog.py:266–269`). На каталоге 10.09 после join null-задержек 0 из 6.
8. **Корень Telonex (F7).** `selection._has_replay_days` проверяет диск в `RAW_TELONEX_POLYMARKET_DIR` Dota (`selection.py:64–69`). CS2 лежит в `CS2_TELONEX_ROOT` (`cs2/constants.py:20`).
9. **onchain_fills без сделок (риск STEP-009).**
   - `has_local_telonex_days` (`telonex_local.py:153`) требует файл книги и файл onchain на каждый день окна. Файл trades нужен только для дня, где в onchain есть сделки.
   - `probe_onchain_days.py`, первые 1000 токенов в каждом корне:

     | Корень | Дни книги | Нет файла onchain | Файлов onchain с 0 строк |
     | --- | ---: | ---: | ---: |
     | Dota `esports-trader/data/raw/telonex/polymarket` | 2 133 | 0 | 664 |
     | LoL `esports-trader/data/lol/raw/telonex/polymarket` | 4 715 | 0 | 2 279 |
     | CS2 10.09 `work/impl009/sep10/polymarket` | 12 | 0 | 0 |

   - Вывод: Telonex отдаёт пустой файл onchain на день без сделок. s08 сохраняет его, так же как LoL-загрузчик, чьи помощники s08 использует. Файла нет только для дня вне интервала канала (`no_telonex_data`), для 404 или для ячейки в ожидании. Заметка impl009 «дни без сделок файла не имеют» (progress.txt:170) — вывод из кода, а не замер.
10. **Тонкая книга роняет весь прогон.**
    - Фреймворк пропускает рынок с числом событий книги < `min_book_events` (`replay_adapters.py:186–192`). Событие — одна запись `OrderBookDeltas`, то есть одна строка снимка (`:161–162`).
    - Затем `build_maker_match_results` бросает `ValueError("framework returned no results for matches …")` (`results.py:324–332`). `--resume` упадёт на той же карте снова.
    - `probe_book_rows.py`, строки снимков в окне `spawn − 2 мин … ended + 1 мин`, на 10.09: от 11 339 до 213 693 на токен. Самая тонкая карта — 184073 (31 секунда `ok` в s10).
11. **Расчёт позиций.**
    - Движок закрывает позицию по `realized_outcome` Polymarket (`_result_policies.py:429–439`). Срок инструмента = `market_closed_at` (`run.py:373–379`).
    - s06 выкидывает карту, если `team_a_win` GRID ≠ победителю bo3 или рынка (`s06_publish_catalog.py:288–290`, `outcome_mismatch`). Значит, исход Polymarket = `team_a_win` на каждой карте каталога.
    - Наш отчёт берёт исход из `context.radiant_win` через `settlement_value_for_token` (`marks.py:58`). Резерв кошелька уже считает `settlement_at="game_end"` (`postprocess.py:619`).
12. **Сводка.**
    - `summarize_arm` (`postprocess.py:571`) режет сделки до id переданных результатов (`_clean_fills_for_results`, `:493–498`). Счётчики котировок режет по `clean_ids` (`:631`).
    - `build_summary_payload` (`:722`). `SummaryPayload.coverage` — `NotRequired` (`report_types.py:187`).
    - `report_seeds.py` читает только нужные ключи `arms[0]` (`:452`, `:519`). Новый ключ его не ломает.
    - `read_winning_tokens` (`report_capital.py:120–137`): ветка `lol`, иначе каталог Dota. Для CS2 он упадёт на `catalog[match_id]`.
13. **`run_seeds.sh`** принимает только `dota|lol` (`:11`, `:23`). Он всегда запускает этап архива `--archives-only` (`:132–133`). Без архивов `require_schedule_cohort` падает. `load_seed_archive` без `_archive/DONE` даёт пустой архив (`run.py:1932–1936`), значит этап семян без архива работает.
14. **`MarketContext(`**: 8 вызовов. Два в `src` (`lol_inputs.py`, `selection.py`), шесть в тестах (`test_feed_schedules`, `test_decision_books`, `test_backtest`, `test_lol_backtest`, `test_backtest_validation`, `test_backtest_postprocess`).
15. **Константы.** `BACKTEST_MAX_POSITION_LEVELS` и `REANCHOR_DRIFT_K` знают только dota/lol (`shared/constants/strategy.py:36–46`). `follow300_policy` берёт `REANCHOR_DRIFT_K[game]` (`strategy/policy.py:77`). `buy_cutoff_second = BUY_CUTOFF_SECOND` = 480 (`:67`, `shared/constants/strategy.py:6`).

## 3. Решения

### 3.1. Четыре заметки для STEP-013

1. **F7, seconds_delay.** `cs2_inputs` берёт `condition_id → int(seconds_delay)` из `CS2_UNIVERSE_MARKETS_PATH` для строк каталога. Нет строки или null → `RuntimeError(f"market {condition_id}: no seconds_delay in {path}; rerun cs2-universe and cs2-catalog")`. s06 не меняю. Факт 7: в каталог null-задержка не попадает, ошибка ловит только рассинхрон файлов.
2. **F7, корень Telonex.** Выборка CS2 живёт в `cs2_inputs` и сама зовёт `has_local_telonex_days(tokens, window, CS2_TELONEX_ROOT)`. `selection.py` меняю только ради нового поля `MarketContext`.
3. **Риск STEP-009, onchain_fills.** Правило не меняю: `has_local_telonex_days` по `calculate_availability_window(map_load_at=spawn_at, game_ended_at=ended_at)`. Основание — факт 9: день без сделок даёт пустой файл, а не пропуск. Отказ считается в `coverage.without_local_telonex`. Лог пишет первые 20 id с причиной `missing_days`. Пилот (STEP-014) сообщает число. Если отказов > 1% карт validation — решение пилота: аудит s08 по этим ячейкам.
4. **STEP-012, dtype.** Кадр решений проходит `compact_features` из s11 перед `predict_model_deltas(model_dir, CS2_FEATURE_COLUMNS, frame)`. Тогда входы модели в бэктесте имеют те же int16/float32/bool, что в обучении. `build_cs2_match_signals` требует `model.json["source_lag_seconds"] == 0`, иначе `ValueError`. Модель по умолчанию — `CS2_RESEARCH_MODEL_DIR = CS2_MODELS_DIR / "research"` (новая константа в `cs2/constants.py`).
5. **STEP-010, PnL последних 300 с.** Новый ключ `map_end_300s` в `ArmSummary` для всех игр: `fills`, `buy_fills`, `buy_turnover`, `settlement_pnl`. В срез входит сделка с `ts_ns >= datetime_to_ns(context.game_ended_at) − 300 с` (`MODEL_TARGET_HORIZON_SECONDS`, `shared/constants/dataset.py:15`).
   - `v = settlement_value_for_token(token_index, radiant_token_index, radiant_win)`.
   - BUY: `q·(v − p)`. SELL: `q·(p − v)`. Без ребейта и комиссии.
   - Сумма этих членов по всем сделкам карты = PnL карты после расчёта. Срез — точная доля, а не оценка.

### 3.2. Сигнал

1. **Тик фида** = каждая строка s10 карты: `feed_ns = state_ts_us · 1000`, задержка 0. Первая строка — секунда −60. Фильтра `GRID_V1_FIRST_TICK_SECOND` нет. Строки идут через `select_cadence_rows(rows, game="cs2", timing=SignalTiming(seed, GRID_FEED_MAX_AGE_SECONDS, mean_interval_seconds=1), lag_seconds=0)`. `BacktestGame` расширяю до `"cs2"`.
2. **Лента часов.** `game_seconds[i] = second[i]`. `paused[i] = paused_s10[i − 1]`, `paused[0] = False` (сдвиг `CS2_PAUSE_SHIFT_SECONDS = 1`). `terminal` — все False. Секунды карты обязаны идти подряд с шагом 1, иначе `ValueError`: сдвиг на одну строку = сдвиг на 1 с только при шаге 1.
3. **Решение** на тике i есть, если `paused[i]` (сдвинутый) = False и `anchor = live_pair_mid(radiant, dire, decision_ns=feed_ns, view_delay_ns=BOOK_VIEW_DELAY_MS·10⁶, pair_sum_tolerance=PAIR_SUM_TOLERANCE)` не None. Это контракт Dota: решение и `market_p_radiant` берутся из живой книги.
4. **Признаки решения:**
   - `market_p_radiant` = `anchor`;
   - `market_p_radiant_change_10s` = `anchor − live_pair_mid(feed_ns − 10 с)`, NaN, если второй mid None (`MID_CHANGE_SECONDS` из s09);
   - `market_radiant_prior` — из s10, одно значение на карту. NaN допустим (s10 оставляет карты без prior). Проверка «одно значение» — без учёта NaN: `_require_constant_prior` из `signals.py:454` не годится, он сравнивает через `==`;
   - 19 игровых признаков — из строки s10 этой секунды. s10 уже применил задержки каналов (табло 1 с, таблица 10 с).
5. **`MatchSignals`:**
   - `timestamps_ns` = `source_timestamps_ns` = ns решений;
   - `dataset_market_ps` = `anchor`;
   - `deaths_radiant` = `deaths_dire` = 0 на каждое решение;
   - `kill_gates = ()`, `board_tick_ns = ()`, `clear_timestamps_ns = ()`;
   - новое поле `observed_clock` = лента.
6. **Прогноз** — один вызов на всё множество решений шарда: кадр 22 колонок → `compact_features` → `predict_model_deltas`.
7. **Отклонение от буквы шага.** В шаге сказано «сигнал — строка s10 каждой секунды». Я беру из s10 тик, часы, паузу, prior и игровые признаки. Mid и изменение за 10 с беру из живой книги на момент тика. Причины:
   - так решает Dota (факт 3);
   - live-адаптер CS2 считает mid и изменение по своей книге;
   - s10 даёт mid только на секундах `ok`, а живой гейт решает сам.

   Разница с обучением: в обучении изменение за 10 с — NaN при широком спреде, здесь это число. Это риск 3.

### 3.3. Пауза, отсечка, конец карты

1. `horn_at` = `datetime.fromtimestamp(compute_zero_second(row), UTC)`, `game_ended_at` = `parse_utc(ended_at)`.
2. `buy_cutoff_second` карты = `compute_last_second(row, zero) + 1` = `floor(ended_at) − ceil(spawn_at) + 1`. Последняя секунда ленты меньше отсечки, поэтому гейт `_cutoff` не срабатывает ни на одной секунде карты. Буква шага — «ended_at − spawn_at в секундах». `+1` закрывает край, когда `ended_at − ceil(spawn_at)` — целое число.
3. Отсечку на карту несёт новое поле `RunSelection.buy_cutoff_seconds: Mapping[int, int]` (Dota и LoL — `{}`). `main` после `resolve_run_policies` делает `replace(policy, buy_cutoff_second=…)` по этому словарю. Сигнатура `resolve_run_policies` не меняется (6 вызовов).
4. `pauses_by_match[id] = []`. Тогда `buy_cutoff_ns = horn + cutoff ≥ floor(ended)+1 > game_end_ns`. Будильник `GAME_END` срабатывает первым и снимает всё. BUY разрешён до конца карты.
5. `build_strategy_configs`, ветка без расписания: `observed_clock = match_signals.observed_clock` вместо `None`. У Dota и LoL grid-v1 там `None`, их поведение не меняется.
6. Имя прогона: `build_report_dir` и `format_validation_run_name` получают обязательный `cut_token: str`. `main` передаёт `"end"` для cs2 и `str(run_policy.buy_cutoff_second)` для остальных. Имя: `validation_join_delta…_cutend_p…_<name>`. В манифесте cs2 `"buy_cutoff_second": "map_end"`.

### 3.4. Выборка и данные

1. **Ids validation** — `split.parquet` s10, `split == "validation"`, порядок `(start_time, match_id)`.
2. **Coverage** (`ValidationCoverage`, поля не меняю):
   - `without_map_market` — id нет в каталоге;
   - `without_signal_rows` — в s10 нет ни одной строки `ok` (иначе `load_lol_mid_series` бросит исключение);
   - `without_local_telonex` — `has_local_telonex_days` = False **или** книга тонкая (п. 3);
   - `archive_excluded` = 0.

   Лог пишет число и первые 20 id по причинам `missing_days` и `thin_book`.
3. **Тонкая книга.** Новый помощник `count_window_book_rows(token_id, window, capture_root) -> int` в `telonex_local.py` рядом с `book_rows_in_window` (`:191`). Он читает только колонку `timestamp_us` дневных файлов книги и считает строки внутри `calculate_replay_window(horn_at, game_ended_at)`. Карта тонкая, если у любого токена строк < `MIN_BOOK_EVENTS`. `run.py` передаёт `MIN_BOOK_EVENTS` в `load_cs2_selection`: обратный импорт из `run.py` дал бы цикл. Сумма двух чисел — вес шарда, второй раз диск не читается.
4. **Строки сигнала** — `validation.parquet`, только нужные колонки: `match_id`, `second`, `state_ts_us`, `market_status`, `market_p_radiant`, `market_radiant_prior`, `paused` и 19 игровых. Чтение — `pq.ParquetDataset(path).read(columns=…)` + `frame_from_table` из s11. Без `filters` в `pd.read_parquet`: урок STEP-012, он зависает. 19 игровых колонок сжимаю через `compact_column`. `market_p_radiant` остаётся float64, потому что из него строятся mid для markout.
5. **Lookups:**
   - `MarketContext` из строки каталога + задержка:
     - `radiant_token_index = team_a_token_index`, `radiant_win = team_a_win`;
     - `market_slug`, `market_closed_at` (через `parse_utc`), `link_source = grid_link_source`;
     - окна — через `calculate_replay_window` и `calculate_clock_end`;
   - `pauses = []`;
   - `mids = load_lol_mid_series(ids, signal_rows)`. Помощник общий, переносить его не нужно.
6. **`RunSelection` cs2:**
   - `capture_root = CS2_TELONEX_ROOT`, `report_root = CS2_MAKER_BACKTESTS_DIR` (новая константа `BACKTESTS_DIR / "cs2_maker"` в `backtest/paths.py`);
   - `model_dir = CS2_RESEARCH_MODEL_DIR`, `lag_seconds = 0`;
   - `archive_join = None`, `buy_cutoff_seconds` — по карте.
7. **`--match-id X`**: X обязан быть подходящей картой validation, иначе `ValueError` (как у LoL). `--limit N` — первые N подходящих.

### 3.5. Планы фида

У CS2 нет архивов. `RunSelection.archive_join` становится `ArchiveJoinInput | None`. Для `None` `main` строит `FeedPlanResult(plans={id: GridV1Plan(id, model_dir)}, exclusions={})` без чтения индекса архивов. `model_dir` = `args.model_dir` или `selection.model_dir`. Помощник `build_grid_v1_feed_plans(ids, model_dir)` кладу в `cs2_inputs.py`.

### 3.6. Разрез по grid_link_source

`MarketContext` получает обязательное поле `link_source: str`. Dota и LoL пишут `""`, CS2 — `grid_link_source` каталога. `build_summary_payload` добавляет `payload["by_link_source"] = {label: summarize_arm(results_of_label, …)}` для каждой непустой метки. `SummaryPayload.by_link_source: NotRequired[dict[str, ArmSummary]]`. Для Dota и LoL ключа нет.

Альтернатива — провести словарь когорт через `ReplayLookups`, `replay_matches`, `write_finished_summary`, `finalize_from_checkpoint` и `build_summary_payload`. Это больше мест вызова, чем 8 конструкторов `MarketContext`.

### 3.7. CLI, манифест, скрипты

1. `--game choices=("dota", "lol", "cs2")`.
2. Для cs2 `parser.error` на: `--since-match`, `--match-ids-file`, `--model-dir-noxp`, `--lag-seconds`, `--validation-dataset`, `--cadence-mean-interval`, `--archives-only`. Проверки `== "lol"` в `_pin_cli_model_dir`, `_pin_dota_experiment_flags`, `_check_match_ids_file` становятся `!= "dota"`. `--model-dir` у cs2 проверяется через `require_catalog_features(dir, CS2_FEATURE_COLUMNS, "--model-dir")`.
3. Константы: `BACKTEST_LEVEL_USDC["cs2"] = 300.0`, `BACKTEST_MAX_POSITION_LEVELS["cs2"] = BACKTEST_DOTA_MAX_POSITION_LEVELS`, `REANCHOR_DRIFT_K["cs2"] = DOTA_REANCHOR_DRIFT_K`.
4. Манифест cs2: `**shared` плюс:
   - `"game": "cs2"`, `"buy_cutoff_second": "map_end"`, `"signal_contract": CS2_SIGNAL_CONTRACT` (`"cs2-s10-l2-v1"`);
   - `"source_lag_seconds"`, `"signal_interval_seconds": 1`, `"pause_shift_seconds": 1`;
   - sha256 каталога, `markets.parquet`, `validation.parquet`, `split.parquet`;
   - `"selected_matches"` и `"selected_matches_sha256"`.
5. `report_capital.read_winning_tokens`: ветка `cs2` читает каталог CS2 через `read_catalog_rows`. Токен победителя = `team_a_token_index`, если `team_a_win`, иначе `1 − team_a_token_index`.
6. `scripts/run_seeds.sh`: принимает `cs2`, для cs2 пропускает этап архива. Семена у cs2 меняют только `latency_seed`: сетка 1 Гц не зависит от семени. Строку про это пишу в справку Makefile, не в код.
7. `Makefile`: цель `cs2-backtest` с переменной `CS2_BACKTEST_ARGS` (правило XR-FIX F3: CS2-цели не берут `ARGS`):

   ```make
   cs2-backtest:  ## CS2 Follow300 backtest on CS2 Telonex books: CS2_BACKTEST_ARGS="--validation --name <suffix> [--limit 2] [--resume] [--shard i/n] [--merge-shards N]" | "--match-id N"
   	PYTHONPATH=src:$(BACKTEST_FRAMEWORK) uv run --group backtest \
   	  python -m backtest.run --game cs2 $(CS2_BACKTEST_ARGS)
   ```

   Плюс `cs2-backtest` в `.PHONY`.

## 4. Изменения по файлам

### `src/backtest/cs2_inputs.py` (новый, ~300 строк)

Без комментариев, docstring на модуль и функции — как в `lol_inputs.py`.

- Константы: `CS2_SIGNAL_CONTRACT`, `CS2_PAUSE_SHIFT_SECONDS = 1`, `CS2_SIGNAL_INTERVAL_SECONDS = 1`, `CS2_GAME_FEATURE_COLUMNS = CS2_FEATURE_COLUMNS[len(CS2_MARKET_FEATURE_COLUMNS):]`, `CS2_SIGNAL_COLUMNS`.
- `@dataclass(frozen=True) class Cs2Selection`: `selected_ids`, `coverage`, `signal_rows`, `catalog: Mapping[int, Cs2CatalogRow]`, `seconds_delays: Mapping[int, int]`, `buy_cutoff_seconds: Mapping[int, int]`, `book_rows: Mapping[int, int]`.
- `read_cs2_seconds_delays(rows, markets_path) -> dict[int, int]` — 3.1 п. 1.
- `read_cs2_validation_ids(split_path) -> tuple[int, ...]`.
- `read_cs2_signal_rows(path) -> pd.DataFrame` — 3.4 п. 4.
- `build_cs2_market_context(row, seconds_delay) -> MarketContext`.
- `compute_cs2_buy_cutoff_second(row) -> int` — 3.3 п. 2.
- `load_cs2_selection(*, match_id, limit, min_book_events) -> Cs2Selection` — coverage и порядок.
- `load_cs2_replay_lookups(match_ids, selection) -> ReplayLookups`.
- `build_grid_v1_feed_plans(match_ids, model_dir) -> FeedPlanResult`.
- `build_cs2_clock_tape(seconds, paused) -> ObservedClockTape` — сдвиг и проверка шага.
- `build_cs2_match_signals(match_ids, signal_rows, model_dir, load_pair_books) -> dict[int, MatchSignals]`:
  1. проверить `source_lag_seconds == 0`;
  2. по каждой карте: лента, решения и строки признаков;
  3. один прогноз на всё;
  4. разложить дельты по картам.

  Промежуточные данные — `@dataclass(frozen=True) class Cs2Decision(match_id, decision_ns, anchor)` и список строк признаков. Без кортежей и `dict[str, Any]`.

### `src/backtest/run.py`

1. `Game = Literal["dota", "lol", "cs2"]`. `BACKTEST_LEVEL_USDC` + `"cs2": 300.0`.
2. `RunSelection`: `archive_join: ArchiveJoinInput | None`, новое поле `buy_cutoff_seconds: Mapping[int, int]`. Dota и LoL передают `{}`.
3. `load_run_selection`: ветка `game == "cs2"` → `load_cs2_selection(match_id=…, limit=…, min_book_events=MIN_BOOK_EVENTS)`. `load_lookups = partial(load_cs2_replay_lookups, selection=cs2)`, `shard_weights` = веса из `book_rows`.
4. `main`:
   - планы фида по 3.5;
   - per-map `buy_cutoff_second` по 3.3 п. 3;
   - `cut_token` для `build_report_dir`.
5. `resolve_run_signals`: для `game == "cs2"` → `build_cs2_match_signals(plan_match_ids, signal_rows, model_dir, load_pair_books)`.
6. `build_strategy_configs`: `observed_clock = match_signals.observed_clock` в ветке без расписания.
7. `build_run_manifest`: ветка cs2 по 3.7 п. 4.
8. CLI по 3.7 п. 1–2. `format_validation_run_name` и `build_report_dir` получают `cut_token`.

### `src/backtest/signals.py`

- `BacktestGame = Literal["dota", "lol", "cs2"]`.
- Сюда переезжает `ObservedClockTape` из `strategy.py`: `strategy.py` импортирует `MatchSignals` из `signals.py`, обратный импорт дал бы цикл. `strategy.py` импортирует ленту обратно, имя `strategy.ObservedClockTape` остаётся.
- `MatchSignals.observed_clock: ObservedClockTape | None = None`. Значение по умолчанию — как у соседнего `clear_timestamps_ns`: генераторы Dota/LoL не трогаю.

### `src/backtest/context.py`, `lol_inputs.py`, `selection.py`

`MarketContext.link_source: str`. Dota и LoL пишут `link_source=""`. Шесть тестов с `MarketContext(` получают `link_source=""`.

### `src/backtest/telonex_local.py`

`count_window_book_rows(token_id, window, capture_root) -> int` — 3.4 п. 3.

### `src/backtest/postprocess.py`, `report_types.py`

- `MapEndSlice(TypedDict)`: `fills: int`, `buy_fills: int`, `buy_turnover: float`, `settlement_pnl: float`. `ArmSummary.map_end_300s: MapEndSlice`.
- `summarize_arm` считает срез по чистым сделкам и `contexts`.
- `build_summary_payload` пишет `by_link_source`. `SummaryPayload.by_link_source: NotRequired[dict[str, ArmSummary]]`.

### `src/backtest/report_capital.py`, `src/backtest/paths.py`, `src/cs2/constants.py`, `src/shared/constants/strategy.py`

По 3.7 п. 3, 5 и 3.4 п. 6, 3.1 п. 4.

### `scripts/run_seeds.sh`

- Строка Usage и проверка: `<dota|lol|cs2>`.
- `[[ "$GAME" != "cs2" ]] &&` перед вызовом этапа архива (`:132–133`).

### `Makefile`

Цель `cs2-backtest` и `.PHONY` по 3.7 п. 7.

### `tests/test_cs2_backtest.py` (новый)

Синтетика: 2 карты, по 120 секунд s10 (−60…59), пауза на секундах 10–14 у первой карты, книги — `pair_books_from_mids` (`tests/pair_books.py:18`). Модель — заглушка `predict_model_deltas` через `monkeypatch` (настоящая LightGBM-модель в тесте не нужна).

1. `test_tape_shifts_pause_by_one_second` — `paused` ленты = s10, сдвинутый на 1. Первая строка не на паузе. Дыра в секундах → `ValueError`.
2. `test_paused_tick_cancels_resting_orders_and_keeps_position` — `build_maker_harness` (`tests/test_backtest_maker.py:307`) с лентой: тик паузы → `_blocked("paused")`, заявки сняты, позиция та же. Образец — `test_pause_clock_cancels_resting_orders` (`:1039`).
3. `test_no_decision_on_shifted_pause_or_missing_book` — решений нет на секундах 11–15 (сдвиг) и там, где `live_pair_mid` = None.
4. `test_buy_allowed_until_map_end` — конфиг из `build_strategy_configs`: `buy_cutoff_ns > game_end_ns`. На последнем тике `_clock_at(...).game_second < policy.buy_cutoff_second`. `requote` на последнем тике не даёт `cutoff`.
5. `test_kill_gate_never_opens` — `kill_gates == ()`, все `deaths_* == 0`, `board_tick_ns == ()`.
6. `test_settlement_follows_team_a_win` — контекст из строки каталога: `radiant_win == team_a_win`, `radiant_token_index == team_a_token_index`. `read_winning_tokens` для манифеста cs2 даёт токен A при победе A и токен B иначе.
7. `test_map_end_300s_slice` — 3 сделки (за 400, 200 и 10 с до конца): срез берёт 2. `settlement_pnl` = сумма `q·(v − p)` / `q·(p − v)` руками.
8. `test_seconds_delay_join_raises_on_missing` — нет `condition_id` / null → `RuntimeError`.
9. `test_features_reach_model_compact` — заглушка прогноза видит int16/float32/bool и 22 колонки в порядке `CS2_FEATURE_COLUMNS`. `market_p_radiant` = `anchor`, изменение за 10 с = разность двух `live_pair_mid`.
10. `test_by_link_source_cut` — `build_summary_payload` с двумя метками даёт два `ArmSummary` с правильными `matches`. У Dota-контекстов (`""`) ключа нет.
11. `test_thin_book_is_excluded` — 24 строки в окне → карта в `without_local_telonex`, 25 → подходит.

Тест в `tests/test_cs2_makefile.py`: `make -s cs2-backtest PY=echo CS2_BACKTEST_ARGS=X ARGS=Y` → в команде `--game cs2 X`, нет `Y`. Тест в `tests/test_run_seeds_sh.py`: скрипт принимает `cs2` и для него не зовёт этап архива (grep по тексту, как соседние тесты).

## 5. Крайние случаи

1. **Карта без решений** (вся на паузе или книга не даёт mid). Лента `timestamps_ns` пустая → путь пустой ленты (`run.py:2293–2313`) → `terminated_early`, не падение. При `--match-id` этот путь выключен (`run.py:2295`), прогон падает в `on_start` — как у Dota.
2. **Prior = NaN** (карта 184073 на 10.09). LightGBM принимает NaN. Проверка «одно значение на карту» не падает на NaN.
3. **Секунды до старта (−60…−1).** s11 учился на них (фильтр s11 не режет `second`). Книга есть с `horn − 2 мин`, mid за −70 с тоже есть.
4. **Тик после `game_end_ns`** — будильник не ставится (`strategy.py:360`). Последняя секунда s10 = `floor(ended)` ≤ `ended`, поэтому она ставится.
5. **Пауза на последней секунде** — `GAME_END` снимает всё в любом случае.
6. **Задержка 1 и 3 с в одном прогоне** — `split_into_batches` группирует по задержке, батч = 1 карта.
7. **Широкий спред.** Решение есть, гейт котировки `wide_spread` блокирует. Позиция держится, как у Dota.
8. **Две карты одной серии** — разные `condition_id`, разные рынки, независимы.
9. **`--resume` после смены каталога, датасета или universe** — sha в манифесте → отказ.
10. **Отсечка cs2 в имени прогона** — `cutend`. Dota и LoL сохраняют `cut480`, тесты `test_backtest.py:980–1043` проверяют это.

## 6. Проба в scratch (имплементер)

Короткий прогон на 1 карте 10.09, без записи в `data/`:

1. Скрипт `orchestrate/work/impl013/smoke.py` подменяет пути `cs2_inputs` (каталог `work/impl008/sep10/catalog/`, Telonex `work/impl009/sep10/polymarket`, датасет `work/impl011/sep10/datasets`, модель `work/impl012/probe/models/research`, отчёт — `work/impl013/backtests`). Потом он зовёт `backtest.run.main()` с `--game cs2 --match-id 183178`.
2. `TELONEX_CACHE_ROOT` указывает в `work/impl013/telonex_cache`.
3. Сообщить: число тиков, решений, секунд паузы, заявок, сделок; `map_end_300s`; `by_link_source`; время. Смоук-модель обучена на том же файле, поэтому PnL не значим.
4. Если смоук потребует сети (Gamma metadata фреймворка), записать это и не обходить.

## 7. Проверки

1. Новые тесты: `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q tests/test_cs2_backtest.py tests/test_cs2_makefile.py tests/test_run_seeds_sh.py`.
2. Затронутые тесты: `tests/test_backtest.py`, `tests/test_backtest_maker.py`, `tests/test_backtest_postprocess.py`, `tests/test_backtest_validation.py`, `tests/test_lol_backtest.py`, `tests/test_feed_schedules.py`, `tests/test_decision_books.py`.
3. Полный набор без golden: `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`. До шага: 3360 passed, 0 failed (progress.txt, STEP-012). Ожидаю 3360 + новые, 0 failed.
4. Типы: `basedpyright` — 0 ошибок.
5. Линт: `make lint` (ruff) — 0 замечаний по изменённым файлам.
6. `make -s cs2-backtest PY=echo CS2_BACKTEST_ARGS="--validation --name x"` печатает команду с `--game cs2`.
7. `bash -n scripts/run_seeds.sh`.
8. Смоук из раздела 6.
9. `git status` в воркдереве: изменены только файлы из раздела 4. `data/backtests/` не тронут.

## 8. Риски и открытые вопросы

1. **Оптимизм ≤ 1 с в конце карты.** Live видит финальный раунд табло на 1 с позже, а бэктест держит BUY до `ended_at`. Сделки последних 300 с идут отдельной строкой `map_end_300s`. Вопрос пилота из STEP-010 (отсечка или цель = исход для конца карты) остаётся пользователю.
2. **Дни без файла onchain.** Факт 9 говорит, что пропуска нет, но CS2 полного прогона s08 ещё нет. Пилот сообщает `without_local_telonex` и id. Порог решения — 1% карт validation.
3. **Признак изменения за 10 с.** В обучении он NaN при широком спреде на любом конце, в бэктесте — число из живой книги. Модель видит значения вне своей обучающей маски. Пилот сравнивает долю решений с не-`ok` секундой s10 (≤ 10 строк кода в отчёте пилота).
4. **Порог `MIN_BOOK_EVENTS`.** Отбор по строкам снимков в окне совпадает со счётом фреймворка, только если одна строка = одна запись `OrderBookDeltas`. Если фреймворк всё же пропустит карту, прогон упадёт по `results.py:332`. Тогда фикс — сравнить счёт на этой карте.
5. **Время выборки.** Счёт строк в окне читает колонку `timestamp_us` 2 токенов × 1–2 дня на карту на каждом шарде. На ~2000 картах validation моя оценка — до минуты на процесс. Я это не мерил.
6. **Память шарда.** Строки сигнала validation ≈ 80 Б/строка после сжатия. Число строк validation неизвестно до s10 на полных данных. `main` удаляет `selection` до реплея (`run.py:2318`).
7. **Сеть фреймворка.** Загрузчик фреймворка ходит в Gamma за метаданными рынка и для Dota (комментарий и `REPLAY_LOAD_WORKERS`, `run.py:2123–2129`). Для рынков CS2 это тот же путь. Смоук покажет, отдаёт ли Gamma данные по slug CS2.
8. **Семена.** У cs2 сетка 1 Гц, семя меняет только задержки исполнения. Разброс семян у cs2 меньше, чем у Dota. Это не ошибка, но в отчёте пилота это нужно сказать.

## 9. Чего в шаге нет

- Полного прогона бэктеста: это STEP-014/015.
- Kill gate, табло-событий и `board_tick` для CS2.
- Колонки по `grid_link_source` в `report_seeds.py`: разрез лежит в `summary.json`, отчёт STEP-015 читает его оттуда.
- Live-адаптера CS2.
- Правок s01–s11 и `poly-maker`.
