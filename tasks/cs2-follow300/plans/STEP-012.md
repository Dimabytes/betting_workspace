# STEP-012 — s11: обучение и метрики

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `96ddc206` (после XR-FIX).
Образец: `src/lol/06_train_model.py` (свой загрузчик, research → production) и `src/train_model/train_model.py` (Dota). Figma нет.

## 1. Что делает шаг

s11 читает датасет s10 (`data/cs2/datasets/`) и обучает три ансамбля на общем ядре `src/shared/utils/gbm.py`:

1. **research** — полная модель CS2, 22 признака, train → early stopping на validation. Пишется в `data/cs2/models/research/`.
2. **рыночная** — тот же ансамбль на трёх признаках рынка. Только метрики, на диск модель не пишется.
3. **production** — полная модель на train + validation с числом деревьев research. Пишется в `data/cs2/models/production/`.

Метрики на validation: MAE gain, dir_300, точность знака. Разрезы: все строки, по картам, по месяцам, по счёту лидера (0–6, 7–9, 10–12, овертайм). Рядом — цифры research-моделей Dota и LoL из их `model.json`.

Новые файлы: `src/cs2/s11_train_model.py`, `tests/test_cs2_train.py`. Правка: цель `cs2-train` в `Makefile`.

## 2. Что я проверил (факты)

1. **Ядро gbm** (`src/shared/utils/gbm.py`):
   - `LGB_PARAMS` (`:27`) — `regression_l1`, `learning_rate` 0,05, `num_leaves` 63, `min_data_in_leaf` 200. `ENSEMBLE_K` = 10.
   - `fit_research_members(train_frame, valid_X, valid_y, features)` (`:300`) и `fit_production_members(train_frame, boost_rounds, features)` (`:310`). Кадр `train_frame` должен иметь `match_id`, все `features`, `market_p_radiant` и `signal_market_p_radiant_300s`: цель — `build_price_delta_labels` (`:133`).
   - Пул — `ProcessPoolExecutor` с `spawn`, `max_workers=MEMBER_FIT_WORKERS` (`:44`, `:291`). `MEMBER_FIT_WORKERS = min(10, os.process_cpu_count())`. На этой машине 12 CPU → 10 процессов.
   - Каждый процесс получает весь `train_frame` через `initargs` (`:295`), группирует его по `match_id` в копии (`:271`, `:209`) и склеивает 90% карт в ещё одну копию (`:220`). У research ещё `valid_X` и `valid_y` внутри `ResearchMemberFit` (`:232`).
   - Подвыборка карт зависит от порядка первого появления `match_id` (`:216`). Порядок строк загрузчика должен быть стабильным.
2. **Датасет s10** (`src/cs2/s10_prepare_dataset.py`): контракт `Cs2DatasetRow` (`:57`), колонки `DATASET_COLUMNS` (`:97`), схема `dataset_schema()` (`:304`), запись `frame_to_table` (`:317`). Файлы `training.parquet`, `validation.parquet`, `production_training.parquet`, `split.parquet` (`:49–52`). Int-колонки без пропусков, float-колонки nullable (NaN → null в parquet). `start_time` = `ceil(spawn_at)`. `is_overtime` = раунд > 24 (`:602`).
3. **Фильтр строк на пробе 10.09** (`work/plan012/probe_loader.py`, файл `work/impl011/sep10/datasets/validation.parquet`): 14 869 строк. `ok` и не пауза — 8 623. Плюс цель есть — 6 399 строк, 5 карт из 6. Фильтр при чтении даёт ровно столько же, сколько маска pandas.
4. **`pd.read_parquet(..., filters=...)` зависает на выходе интерпретатора.** Чтение и печать проходят, потом процесс не завершается. Замер на том же файле (`.venv/bin/python -c`, сторож 15–20 с):
   - `pd.read_parquet` с двумя фильтрами: 5 зависаний из 8 запусков;
   - `pd.read_parquet` без фильтров: 0 из 6;
   - `pq.read_table` с фильтрами: 0 из 7;
   - `pq.ParquetDataset(path, filters=expr).read(columns=...)`: 0 из 6.

   План STEP-011 (риск 4) предлагал `pd.read_parquet(columns=..., filters=...)`. Его брать нельзя.
5. **Типы для basedpyright.** `pq.ParquetDataset(path, filters=expr).read(columns=...)` и выражение `pc.field(...)` проходят без ошибок. `Table.to_pandas` — «partially unknown». В репо это закрывают `# pyright: ignore[reportUnknownMemberType]` на строке (`src/backtest/telonex_local.py:127`). `pq.read_table` даёт ту же ошибку, его не берём.
6. **Порядок строк.** Синтетический файл 3 млн строк, 60 row groups, фильтр по трём колонкам: порядок `ParquetDataset.read` совпал с маской pandas в 3 из 3 запусков (`work/plan012/probe_order.py`).
7. **LightGBM 4.7, `_data_from_pandas`** (`lightgbm/basic.py:752`, `:792`): матрица берёт тип `np.result_type` всех колонок и float32. Есть хоть одна int64 или float64 → матрица float64. Только int16, bool и float32 → float32. Bool-колонки LightGBM принимает.
8. **Память на строку признаков** (проба 10.09): float64/int64 — 148 Б, после int → int16 и float → float32 — 52 Б. Колонки int на пробе: от −9 до 22.
9. **Память пула, замер** (`work/plan012/measure_rss.sh`, `probe_memory.py`): пик суммы RSS родителя и всех процессов, синтетический кадр с 22 признаками CS2:

   | Подгонка | Процессов | Строк | Пик RSS |
   | --- | ---: | ---: | ---: |
   | production, компактные типы | 10 | 1 млн | 5,2 ГБ |
   | production, компактные типы | 10 | 2 млн | 9,7 ГБ |
   | production, float64/int64 | 10 | 2 млн | 13,4 ГБ |
   | research, компактные типы | 10 | 1 млн train + 1 млн validation | 9,4 ГБ |
   | production, компактные типы | 3 | 1 млн | 3,2 ГБ |
   | production, компактные типы | 3 | 2 млн | 5,2 ГБ |
   | research, компактные типы | 3 | 1 млн + 1 млн | 4,3 ГБ |
   | production, компактные типы | 1 | 1 млн | 1,9 ГБ |
   | production, компактные типы | 1 | 2 млн | 2,6 ГБ |

   Наклон: 4,5 ГБ на 1 млн строк при 10 процессах, 2,0 ГБ при 3, 0,7 ГБ при 1. Research на T + V строках весит как production на T + V строках. На машине 32 ГБ RAM.
10. **`PYTHON_CPU_COUNT`.** Python 3.13.2 в `.venv` читает эту переменную. С `PYTHON_CPU_COUNT=3` `os.process_cpu_count()` = 3 и `MEMBER_FIT_WORKERS` = 3. Члены ансамбля байт в байт те же: sha `4e5b90f60b5852ba` и при 10, и при 3 процессах (`work/plan012/probe_workers_identical.py`). Время на 2 млн строк, 30 деревьев: 10,7 с при 10 процессах, 13,6 с при 3, 31,6 с при 1.
11. **Скорость на малых данных.** Настоящие подгонки на 30 картах × 80 с (train) и 20 × 80 (validation): research 3,8 с, рыночная 1,5 с, production 3,5 с (`work/plan012/probe_fit.py`). Предсказание по входу float64/int64 совпало с предсказанием по входу float32/int16: максимум разницы 0,0.
12. **Метрики LoL** (`src/lol/lol_validation_metrics.py`): `aggregate_points` (`:94`) даёт MAE gain, bias, dir_300 и их CI бутстрепом по кластерам `event_id`. `directional_markout` (`:64`): направление +1, если `model_p ≥ current`. `cluster_metric_totals` (`:70`), `bootstrap_series_cluster_ci` (`src/shared/utils/market_scenario_report.py:157`). На 1 млн точек: сборка точек 0,85 с, `aggregate_points` 1,0 с (`work/plan012/probe_aggregate_speed.py`).
13. **Точность знака** уже есть в цикле 2: `sign_accuracy_stats` (`scripts/cs2/train_models.py:375`). Доля строк с ненулевым движением, где знак прогноза совпал со знаком движения. Прогноз без движения (`model_p == current`) — промах. CI — тот же бутстреп по кластерам.
14. **Цифры Dota и LoL на диске** (в гите ветки):
    - `data/new_model/research/model.json` (`RESEARCH_MODEL_DIR`, `src/shared/constants/paths.py:42`): `mae_gain_300_cents` 0,299 [0,217; 0,385], `dir_300_cents` 1,890, `rows` 407 859, trees 72. Окно −60…479 (`FULL_WINDOW_SCENARIO_BUCKET`, `market_scenario_report.py:33`).
    - `data/lol/models/research/model.json` (`LOL_RESEARCH_MODEL_DIR`, `src/shared/constants/lol.py:60`): 0,247 [0,186; 0,311], 1,438, `rows` 928 151, trees 62. Окно 0…479.
    - CI для dir_300 и точности знака там нет: `ModelMetrics` (`src/shared/types/model.py`) их не хранит.
15. **Публикация каталога.** `staging_model_dir` (`src/shared/utils/model_registry.py:33`) создаёт `.next-<имя>`. `publish_model_dir` (`:42`) сам обрабатывает первую публикацию, обёртка LoL `publish_lol_model_dir` не нужна. `.gitignore`: `!data/cs2/models/` и `data/cs2/models/.next-*` (`:16–17`).
16. **Dota берёт `second` (время игры) признаком** (81 колонка `DOTA_XP_FEATURE_COLUMNS`). Список признаков CS2 в PLAN.md «Признаки игры» времени карты не содержит.

## 3. Решения

1. **Признаки — ровно список PLAN.md, 22 колонки, константа `CS2_FEATURE_COLUMNS` в s11:**
   - рынок (3): `market_p_radiant`, `market_radiant_prior`, `market_p_radiant_change_10s` — это и `CS2_MARKET_FEATURE_COLUMNS` рыночной модели;
   - scoreboard (15): `team_a_score`, `team_b_score`, `score_diff`, `round_number`, `half`, `team_a_ct`, `is_overtime`, `is_pistol_round`, `team_a_series_score`, `team_b_series_score`, `game_number`, `map_code`, `phase`, `phase_clock_s`, `bomb_planted`;
   - таблица (4): `team_a_alive`, `team_b_alive`, `team_a_loadout`, `team_b_loadout`.

   Не признаки: `match_id`, `event_id`, `start_time`, `second`, `state_ts_us`, `market_status`, `paused`, `signal_market_p_radiant_300s`. `map_code` — число, не категория: ядро не передаёт `categorical_feature`, и PLAN.md называет карту целым кодом.
2. **Загрузчик `load_cs2_dataset(path)`** (как `load_lol_dataset`):
   - чтение `pq.ParquetDataset(path, filters=TRAINABLE_ROWS).read(columns=LOAD_COLUMNS)`, потом `to_pandas()`;
   - `TRAINABLE_ROWS = (pc.field("market_status") == "ok") & ~pc.field("paused") & pc.field("signal_market_p_radiant_300s").is_valid()`;
   - `LOAD_COLUMNS` = `match_id`, `event_id`, `start_time`, 22 признака, `signal_market_p_radiant_300s`. Колонки фильтра в кадр не идут;
   - после чтения int-признаки → int16, float-признаки → float32, bool остаётся. Цель и `match_id` не трогаем;
   - 0 строк → `ValueError(f"no trainable rows in {path}")`. Так пилот без апрельских карт падает с понятной причиной.

   Без `pd.read_parquet(filters=...)` (факт 4). Компактные типы дают матрицу LightGBM float32 и −28% памяти пула (факт 9).
3. **В пул идёт только нужное.** `select_fit_columns(frame, features)` = `match_id` + признаки + цель. Для полной модели это 24 колонки, для рыночной — 5. `event_id` и `start_time` остаются в родителе для метрик.
4. **research и рыночная модель — одна функция `fit_research(train, validation, features)`** поверх `fit_research_members`: `valid_X = validation[features]`, `valid_y = build_price_delta_labels(validation)`. Early stopping и метрики — на одних и тех же строках validation. Окна покупки у CS2 нет, поэтому Dota-разделения «fit до карты, метрики до 480 с» нет.
5. **Прогноз — через `GbmPredictor` и `predict_future_prices`** (`gbm.py:72`, `:140`), как LoL. Это то же среднее членов, что увидит бэктест. Для рыночной модели `GbmPredictor` строится в памяти и на диск не пишется.
6. **Метрики.** Точки — `LolHoldoutPoint(event_id, second=0, current, future, model_p)` на каждую строку validation. На каждый срез:
   - `aggregate_points` даёт MAE gain, bias, dir_300 и их CI;
   - `measure_sign_accuracy(points)` — новая функция ~15 строк по определению цикла 2 (факт 13). CI — `cluster_metric_totals` + `bootstrap_series_cluster_ci`.

   Срезы:
   - `all` — все строки;
   - `map` — имя карты по обратной таблице `CS2_MAP_CODES`;
   - `month` — `YYYY-MM` UTC от `start_time`;
   - `leader` — `overtime`, если `is_overtime`; иначе по `max(team_a_score, team_b_score)`: `0-6`, `7-9`, `10-12`. Счёт — тот, что видит модель (scoreboard с задержкой 1 с).

   Окно CS2 в таблице — `-60-end`.
7. **Dota и LoL рядом.** `ReferenceModel(game, model_dir, window)` — frozen dataclass. Константа `REFERENCE_MODELS`: `("dota", RESEARCH_MODEL_DIR, FULL_WINDOW_SCENARIO_BUCKET.label)` и `("lol", LOL_RESEARCH_MODEL_DIR, f"{LOL_GRID_START_SECOND}-{BUY_CUTOFF_SECOND - 1}")`. s11 читает `metrics` из их `model.json` (`read_model_meta`) и пишет строку среза `all`: rows, MAE gain с CI, bias, dir_300. CI dir_300 и точность знака у них пустые (факт 14).
8. **Файлы.** Каталоги от `--models-dir` (по умолчанию `CS2_MODELS_DIR`):
   - `research/`: `member_00…09.txt`, `model.json` (`build_ensemble_model_meta` + `build_model_metrics` по срезу `all` полной модели), `split.parquet` (копия из датасета), `validation_metrics.csv`;
   - `production/`: члены, `model.json` с `metrics: null`, `split.parquet`;
   - архив: `archive/research/`, `archive/production/` — `publish_model_dir` с первой публикацией.

   `validation_metrics.csv` — строка на (модель, срез, корзину). Колонки: `model` (`cs2`, `cs2_market`, `dota`, `lol`), `slice`, `bucket`, `window`, `rows`, `maps`, `mae_gain_300_cents`, `mae_gain_300_ci_low_cents`, `mae_gain_300_ci_high_cents`, `model_bias_300_cents`, `dir_300_cents`, `dir_300_ci_low_cents`, `dir_300_ci_high_cents`, `sign_accuracy`, `sign_accuracy_ci_low`, `sign_accuracy_ci_high`, `sign_rows`. Строка — frozen dataclass `MetricRow`, запись — `pd.DataFrame([asdict(row) ...]).to_csv(index=False)`.
9. **`source_lag_seconds` = 0.** Задержки каналов (1 с и 10 с) уже внутри строк s10. Бэктест берёт строку секунды t как есть. Поле пишется только в summary бэктеста (`src/backtest/run.py:828`, `:840`) и в `model_identity_sha256`.
10. **production.** `boost_rounds` = `predictor.num_trees()` research, как Dota и LoL. `train_research` возвращает число и освобождает свои кадры до чтения `production_training.parquet`: train и validation в памяти вместе с production не держим.
11. **Печать** — `print_count` из `shared.utils.log`, как s10: строки и карты train, validation, production; деревья min / max / среднее для трёх ансамблей. Потом строка среза `all` для `cs2`, `cs2_market`, `dota`, `lol`: MAE gain [CI], dir_300 [CI], точность знака [CI]. По dir_300 полной модели STEP-015 решает, запускать ли бэктест.
12. **Число процессов пула не меняем в коде.** Ядро не трогаем. Рычаг — `PYTHON_CPU_COUNT=<n> make cs2-train` (факт 10): модели те же, меняется только память и время. Выбор n — в пилоте (раздел 8, риск 1).
13. **CLI** — typer, как s10: `--dataset-dir` (`CS2_DATASETS_DIR`), `--models-dir` (`CS2_MODELS_DIR`). Весь запуск — под `if __name__ == "__main__":`. Пул `spawn` импортирует скрипт как `__mp_main__` в каждом процессе: на уровне модуля не должно быть чтения данных и обучения. Мой первый замер без этой защиты рекурсивно запускал себя.

## 4. Изменения по файлам

### `src/cs2/s11_train_model.py` (новый, ~300–350 строк)

Первая строка — `# pyright: reportMissingTypeStubs=false`, как `gbm.py` и `train_model.py` (импорт `lightgbm`). Других file-level подавлений нет. Одно `# pyright: ignore[reportUnknownMemberType]` — на `to_pandas()`.

Константы: `CS2_FEATURE_COLUMNS`, `CS2_MARKET_FEATURE_COLUMNS`, `TARGET_COLUMN`, `LOAD_COLUMNS`, `TRAINABLE_ROWS`, `REFERENCE_MODELS`, `CS2_METRIC_WINDOW = "-60-end"`, `METRICS_NAME = "validation_metrics.csv"`.

Функции (имена — глаголы, все аргументы обязательные, кортежи из нескольких полей — frozen dataclass):

1. `load_cs2_dataset(path) -> pd.DataFrame` — решение 2.
2. `compact_features(frame) -> pd.DataFrame` — int16 / float32 по dtype колонки.
3. `select_fit_columns(frame, features) -> pd.DataFrame` — решение 3.
4. `fit_research(train, validation, features) -> list[lgb.Booster]` — решение 4.
5. `build_predictor(model_dir, features, boosters) -> GbmPredictor` и `predict_prices(predictor, frame) -> np.ndarray` (через `predict_future_prices`).
6. `build_holdout_points(validation, model_prices) -> list[LolHoldoutPoint]`.
7. `measure_sign_accuracy(points) -> SignAccuracy` (dataclass: `accuracy`, `ci`, `rows`).
8. `label_leader_buckets(frame) -> pd.Series` — `np.select` по `is_overtime` и счёту лидера.
9. `label_slices(validation) -> SliceLabels` (dataclass из трёх `pd.Series`: карта, месяц, лидер).
10. `measure_points(model, slice_name, bucket, points, maps) -> MetricRow`.
11. `measure_model(model, validation, model_prices) -> list[MetricRow]` — срез `all`, потом по группам `groupby(...).indices` каждого среза.
12. `read_reference_row(reference) -> MetricRow`.
13. `write_metrics_csv(rows, path)` и `print_metrics(rows)`.
14. `train_research(dataset_dir, models_dir, references) -> int` — загрузка train и validation, две подгонки, метрики, публикация `research/`, возврат числа деревьев.
15. `train_production(dataset_dir, models_dir, boost_rounds) -> None`.
16. `train_cs2_models(dataset_dir, models_dir, references) -> None` и `main`.

Импорты, не копии: `fit_research_members`, `fit_production_members`, `build_price_delta_labels`, `predict_future_prices`, `GbmPredictor`, `write_ensemble_members`, `build_ensemble_model_meta`, `build_model_metrics`, `HoldoutWindowStats` (gbm); `staging_model_dir`, `publish_model_dir`, `read_model_meta`, `write_model_meta` (model_registry); `LolHoldoutPoint`, `aggregate_points`, `cluster_metric_totals` (lol_validation_metrics); `bootstrap_series_cluster_ci`, `probability_to_cents`, `FULL_WINDOW_SCENARIO_BUCKET` (market_scenario_report); `CS2_MAP_CODES`, `CS2_DATASETS_DIR`, `CS2_MODELS_DIR` (cs2.constants); `TRAINING_NAME`, `VALIDATION_NAME`, `PRODUCTION_NAME`, `SPLIT_NAME` (s10); `RESEARCH_MODEL_DIR`, `LOL_RESEARCH_MODEL_DIR`, `LOL_GRID_START_SECOND`, `BUY_CUTOFF_SECOND`; `print_count`; `sha256_file`.

Комментариев в новом коде нет (правило ревью этой задачи).

### `Makefile`

```make
cs2-train:  ## CS2 s11 research + production ensembles and market-only metrics -> data/cs2/models (fewer member processes: PYTHON_CPU_COUNT=3)
	$(PY) src/cs2/s11_train_model.py
```

- `cs2-train` добавить в `.PHONY`.
- Без зависимости от `cs2-prepare`, как `train` и `lol-train`. Без `$(ARGS)` и без своей переменной: правило XR-FIX F3. `PYTHON_CPU_COUNT` make передаёт рецепту из окружения сам.
- В `cs2-collect` не входит.

### `tests/test_cs2_train.py` (новый)

Фикстуры пишут parquet схемой s10: кадр с `DATASET_COLUMNS` → `frame_to_table(frame, dataset_schema())` → `pq.write_table`. Так null в float-колонках настоящий, как у s10. `split.parquet` — колонки `match_id, event_id, start_time, split`. Эталонные каталоги Dota и LoL — `model.json` в `tmp_path` через `build_ensemble_model_meta` + `write_model_meta`. Значения фикстур из нескольких полей — frozen dataclass (урок STEP-005, -006, -010).

1. `test_loader_drops_not_ok_paused_and_empty_target` — файл: 2 строки `ok` без паузы с целью, по одной `wide_spread`, `missing_quote`, `paused` и с пустой целью. Остаются 2 строки в исходном порядке. Типы: float-признаки float32, int-признаки int16, bool — bool. NaN в `market_radiant_prior` сохраняется. Колонок `market_status` и `paused` в кадре нет. Файл без подходящих строк → `ValueError`.
2. `test_training_writes_models_and_metrics` — настоящие подгонки, без заглушек. Train: 30 карт × 80 с, validation: 20 карт × 80 с, production = train + validation. В validation 2 кода карт, 2 месяца, все четыре корзины лидера, часть строк `wide_spread` и `paused`. Цель зависит от `score_diff`, чтобы деревья росли. Проверки:
   - в `research/` и `production/` есть `model.json`, `member_00.txt…member_09.txt`, `split.parquet`; в `research/` есть `validation_metrics.csv`; `.next-*` не осталось;
   - `load_predictor` открывает оба каталога, `feature_names == tuple(CS2_FEATURE_COLUMNS)`;
   - все `member_trees` production равны `num_trees()` research;
   - `metrics` research не null, у production null; `source_lag_seconds` = 0;
   - в CSV модели `cs2`, `cs2_market`, `dota`, `lol`. У `cs2` и `cs2_market` срезы `all`, `map` (2 корзины), `month` (2), `leader` (4). `rows` среза `all` = строкам validation после фильтра. Строки `dota` и `lol` несут MAE gain и dir_300 из своих `model.json`.

   Время — около 10–15 с (факт 11).
3. `test_leader_buckets` — (6:3) → `0-6`, (7:0) → `7-9`, (9:9) → `7-9`, (12:10) → `10-12`, (12:12, овертайм) → `overtime`, (14:13, овертайм) → `overtime`.
4. `test_sign_accuracy_skips_flat_moves` — точки: рост и прогноз вверх (попадание), рост и прогноз вниз (промах), будущее = текущее (не считается), прогноз = текущее при движении (промах). Точность 1/3, `rows` = 3.

## 5. Крайние случаи

1. **0 строк train или validation.** `ValueError` с путём файла (решение 2). Пилот без апрельских карт падает здесь, как и ожидает feature.json.
2. **Карта без строк `ok`** (на пробе 184073 — 0 строк для s11) выпадает из обучения и метрик. Число карт печатается.
3. **NaN в признаках** — prior, изменение mid за 10 с, `phase_clock_s` до карты, loadout в заморозке. LightGBM их принимает, ничего не заполняем.
4. **Овертайм.** Корзина по `is_overtime`, а не по счёту. Строки до карты — раунд 1, счёт 0:0 → `0-6`. Счёт 13 в основной части невозможен: 13 заканчивает карту, а табло видит счёт с задержкой 1 с.
5. **Пустой срез.** Корзина без строк в CSV не попадает: `groupby` её не даёт.
6. **Повторный запуск.** Тот же датасет → те же члены (ядро детерминировано, порядок строк стабилен, факт 6). Старый каталог уходит в `archive/`.
7. **Порядок строк.** `match_id` в файлах s10 идёт в порядке каталога (`spawn_at`, `match_id`). Загрузчик порядок не меняет и не сортирует.

## 6. Проба в scratch (имплементер)

Папка `work/impl012/`. Полных данных нет: `data/cs2/datasets/` пустая.

1. Собрать `work/impl012/probe/datasets/`: `validation.parquet` и `split.parquet` — копии из `work/impl011/sep10/datasets/`. Тот же `validation.parquet` скопировать как `training.parquet` и `production_training.parquet`. Это smoke пути на настоящих колонках s10, не модель.
2. Запуск: `PYTHONPATH=src uv run python src/cs2/s11_train_model.py --dataset-dir ../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl012/probe/datasets --models-dir ../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl012/probe/models`.
3. Жду: train и validation по 6 399 строк и 5 карт; оба каталога открывает `load_predictor`; в CSV 4 модели; процесс завершился сам (код 0, без зависания на выходе).
4. Проверка ошибки: каталог, где `training.parquet` пустой (`work/impl011/sep10/datasets/training.parquet`, 0 строк) → `ValueError` с путём.
5. `data/cs2/models/` после пробы пустая, `git status` в worktree чистый.

## 7. Проверки

1. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q tests/test_cs2_train.py` — 4 теста проходят.
2. Полный набор без golden-тестов (правило пользователя): `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke` — 0 падений.
3. `uv run basedpyright` — 0 ошибок.
4. `uv run ruff check src/cs2/s11_train_model.py tests/test_cs2_train.py` и `uv run ruff format --check` на тех же файлах. C901 ≤ 10.
5. `make -n cs2-train` — одна строка `uv run python src/cs2/s11_train_model.py`.
6. Проба раздела 6.

## 8. Риски и открытые вопросы

1. **Память на полном прогоне (главный риск).** Пик ≈ 1,2 ГБ + наклон × P, где P — строки `production_training` после фильтра (= train + validation) в млн. Наклон: 4,5 ГБ (10 процессов), 2,0 ГБ (3), 0,7 ГБ (1) — факт 9. Оценка P — 10–17 млн (STEP-011: ~37 млн строк s10, на пробе 43% проходят фильтр).
   - 10 процессов: 46–78 ГБ — не влезает в 32 ГБ;
   - 3 процесса: 21–35 ГБ;
   - 1 процесс: 8–13 ГБ, время ~30 мин на ансамбль по моей оценке.

   **Для STEP-014 (пилот):** взять P из печати s11 на данных пилота, замерить пик суммы RSS дерева процессов (`work/plan012/measure_rss.sh` показывает способ), проверить наклон. **Для STEP-015:** перед s11 посчитать P из `production_training.parquet` и выбрать `PYTHON_CPU_COUNT`, чтобы пик ≤ 24 ГБ. Если не влезает и при 1 процессе — стоп и вопрос пользователю. Варианты для него: обучать на каждой k-й секунде (меняет модель) или машина с большей памятью.
2. **Точность знака Dota и LoL пустая.** «Те же метрики» по ним — только MAE gain и dir_300 из `model.json`. Чтобы получить точность знака, нужно гнать их validation через их загрузчики. Предлагаю, если пользователю это нужно, сделать разовый скрипт в отчёте STEP-015, а не в s11.
3. **Окна и периоды сравнения разные.** Dota: −60…479 с, validation до 11.10. LoL: 0…479 с, до 10.10. CS2: вся карта, до 17.10. В CSV колонка `window`. Прямое сравнение цифр — ориентир, не равенство.
4. **`second` не признак.** Dota берёт время игры, PLAN.md для CS2 — нет. Реальное время карты включает паузы до 606 с. Ход карты несут `round_number` и `phase_clock_s`. Добавить — одна строка в `CS2_FEATURE_COLUMNS`.
5. **Loadout и таймер фаз 1–3 зависят от сверки 4** (решение пользователя после пилота). Убрать loadout — две строки из `CS2_FEATURE_COLUMNS`.
6. **Цель в конце карты пустая** (вопрос пилоту из STEP-010). Строки ближе 300 с к концу карты в обучение и метрики не попадают. Корзина `10-12` описывает только длинные концовки.
7. **Early stopping на validation**, на том же периоде, что бэктест. Это принято в PLAN.md, как у Dota и LoL: бэктест немного оптимистичен.
8. **Типы на входе бэктеста.** Модель учится на float32/int16, а STEP-013 подаст строки s10 как есть (float64/int64). На пробе предсказания совпали (факт 11). Порог дерева лежит между двумя разными значениями train, поэтому округление float32 его не пересекает. STEP-013 может звать `compact_features` для полной гарантии.

## 9. Чего в шаге нет

- Бэктеста, сигнала и паузы +1 с — STEP-013. `source_lag_seconds` = 0 и выбор каталога (research для validation) решает STEP-013.
- Изменений `src/shared/utils/gbm.py`, `LGB_PARAMS`, Dota- и LoL-тренеров.
- Цели L2, истории признаков, cash, HP.
- Записи рыночной модели на диск.
- Прогона на полных данных и обучения на них: только тесты и smoke на 6 картах 10.09.
