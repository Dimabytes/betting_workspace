# STEP-011 — s10: датасет 1 Гц

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `55528aac`.
Образец: `src/prepare_dataset/prepare_dataset.py` (Dota) и `src/lol/05_prepare_dataset.py` (раскладка файлов LoL). Figma нет.

## 1. Что делает шаг

s10 строит строку на каждую секунду карты каталога: от `spawn_at` − 60 с до `ended_at`. Сетка секунд — та же, что у s09: секунда `k` — это секунда Unix `ceil(spawn_at) + k`. В строке:

- ключи: `match_id`, `event_id`, `start_time`, `second`, `state_ts_us`;
- рынок из s09: статус секунды, mid, prior, изменение mid за 10 с, цель через 300 с;
- признаки scoreboard с задержкой 1 с;
- признаки таблицы и схемы карты с задержкой 10 с;
- флаг паузы.

Файл хранит все секунды: не `ok`, паузы и строки с пустой целью. Строки для обучения отбирает загрузчик s11 (STEP-012). Бэктест (STEP-013) берёт из `validation.parquet` сигнал и паузу на каждую секунду.

Выход — `data/cs2/datasets/`: `training.parquet`, `validation.parquet`, `production_training.parquet`, `split.parquet`. Цель Makefile — `cs2-prepare`. Тесты — `tests/test_cs2_dataset.py`.

## 2. Что я проверил (факты)

1. **Сетка s09** (`src/cs2/s09_build_market_data.py`): `compute_zero_second` = `ceil(spawn_at)` (`:143`), `compute_last_second` = `floor(ended_at)` − zero (`:147`), строки от `MODEL_START_SECOND` = −60 до last (`assemble_rows`), путь `build_cache_path(output_dir, match_id)` = `v<CACHE_VERSION>/match_id=<id>.parquet` (`:139`). Колонки — `MARKET_SECOND_COLUMNS` (`:64`): `match_id, condition_id, event_id, second, state_ts_us, market_status, market_p_radiant, market_p_radiant_change_10s, signal_market_p_radiant_300s, market_radiant_prior`. Prior None хранится как NaN. Текущий `CACHE_VERSION` = `f2207759`.
2. **Выжимка s05** — папка `data/cs2/bo3/round_state/<bo3_game_id>/` (`write_map_summary`, `s05_fetch_bo3_replays.py:330`):
   - `seconds.parquet` (`Cs2ReplaySecondRow`, `s05_replay_contract.py:29`): `second` (секунда Unix), `round_number`, `phase`, `timer_s`, флаги бомбы, `paused`;
   - `players.parquet` (`Cs2ReplayPlayerRow`, `:41`): `second`, `player_id`, `side` (`CT`/`T`), `is_alive`, `health`, `armor`, `money`;
   - строка на секунду s — последний кадр с временем ≤ s (`pick_second_frames`, `s05_parse_replay.py:261`, `bisect_right − 1`);
   - секунды раунда — от `ceil(start)` до `ceil(next_start)` − 1, у последнего раунда до `floor(конец сегмента)` (`list_round_seconds`, `:248`). Секунды идут без дыр.
3. **Флаг паузы s05** (`is_pause_second`, `s05_parse_replay.py:139`): фаза 0 и таймер < 0, или фаза 3 в раунде 12, 24, 24 + 3k (`is_halftime_round`, `:133`). Это ровно правило пауз из PLAN.md.
4. **Часы раундов** — `data/cs2/bo3/round_clock.parquet` (`Cs2RoundClockRow`, `s05_replay_contract.py:76`): на раунд `bo3_game_id, round_number, started_at, freeze_end_at, planted_at, won_at, ended_at, team_a_side (CT/T), team_a_won`. У последнего раунда `won_at` = конец сегмента GRID (`find_round_clock`, `s05_parse_replay.py:426`).
5. **Данные карты** — `data/cs2/bo3/replay_maps.parquet` (`Cs2ReplayMapRow`, `:83`): `game_number`, `map_name`, `team_a_series_score`, `team_b_series_score` (счёт серии до карты, `series_score` в `s05_fetch_bo3_replays.py:228`), `status`.
6. **Loadout bo3** — `read_equipment(raw_dir, match_id, bo3_game_id)` в `s05_check_live.py:612` даёт `dict[EquipmentKey(round_number, side), int | None]` из `raw/bo3/games/<bo3_match_id>.json`, поле `game_round_team_clans[].equipment_value`. `bo3_match_id` — в `data/cs2/bo3/links.parquet` (`Cs2Bo3LinkRow`, `s04_link_bo3.py:124`). `require_match_id` (`s05_check_live.py:662`) ищет линейно по списку: на 15 тыс. карт это O(n²), его не берём.
7. **Каталог** — `read_catalog_rows` (`s06_publish_catalog.py:189`), `Cs2CatalogRow` (`:67`). Карта попадает в каталог только с известным кодом карты (`CS2_MAP_CODES`, `constants.py:44`; `unknown_map` в `s06:254`).
8. **Split** — `assign_split` (`s07_link_readiness.py:67`): `spawn_at` ≥ `VALIDATION_START_TIME` → validation, иначе train. Порог целый, поэтому `spawn_at ≥ V` ⇔ `ceil(spawn_at) ≥ V`.
9. **Раскладка LoL** (`src/lol/05_prepare_dataset.py:706–775`): `training.parquet`, `validation.parquet`, `production_training.parquet` (= train + validation), `split.parquet`. Dota пишет то же под другими именами (`write_datasets`, `prepare_dataset.py:282`).
10. **Ядро gbm** берёт цель как `signal_market_p_radiant_300s − market_p_radiant` (`gbm.py:133`) и `match_id` как int (`gbm.py:209`). Имена рынка и цели Dota сохраняем.
11. **Проба на данных 10.09** (scratch, `work/plan011/probe1.py`, `probe2.py`, `probe3.py`, `proto.py`; входы — выходы проб STEP-006, -008, -009, -010):
    - во всех 6 картах `spawn_at` = `started_at` раунда 1, первая секунда выжимки = `ceil(spawn_at)`, последняя = `floor(ended_at)`. Сетка s09 и выжимка совпадают секунда в секунду;
    - первая секунда каждого раунда — фаза 0, таймер 19,02–20,00 с. В фазе 0 `(s − started_at раунда) − (20 − timer_s)` лежит в [−0,10; +0,12] с (p01–p99). Значит, «секунд с начала заморозки» = `s − started_at`;
    - **тренер в данных игроков.** В 4 из 6 карт у одной или обеих сторон 6 id. Лишний id жив ровно в секунды фазы 0: доля живых 0,2344 = 719 / 3068 секунд заморозки (184058), 0,2494 (184073), 0,2394 (184057). В заморозке выходит 6 живых. В 185135 у одного id 30 с нет стороны, там 4 живых в заморозке;
    - `team_side` в bo3 — `CT`/`T`, как `team_a_side` в часах раундов. Пропусков `equipment_value` в 6 картах нет, значения 1 500–33 700;
    - прототип логики раздела 3 на 6 картах:

      | match_id | строк | до карты (1 с / 10 с) | пауза | счёт в последней строке | строк для s11 (ok, не пауза, цель есть) |
      | --- | ---: | --- | ---: | --- | ---: |
      | 183178 | 1 895 | 61 / 70 | 216 | 1:12 | 979 |
      | 184058 | 3 128 | 61 / 70 | 306 | 12:8 | 575 |
      | 184073 | 2 273 | 61 / 70 | 160 | 7:12 | 0 |
      | 185135 | 3 347 | 61 / 70 | 290 | 12:11 | 2 502 |
      | 183177 | 2 470 | 61 / 70 | 334 | 12:5 | 1 689 |
      | 184057 | 1 756 | 61 / 70 | 114 | 12:2 | 654 |

      Итого 14 869 строк, 6 399 строк для s11. Последний раунд в счёт не входит: его `won_at` = конец карты, позже последней секунды − 1. Время — 0,01–0,02 с на карту, включая чтение JSON bo3. Parquet — 18,4 Б на строку, в памяти pandas — 299 Б на строку.

## 3. Решения

1. **Одна сетка секунд с s09.** Строки датасета = строки файла s09 карты, один в один. Секунда Unix `t = zero + second`. Свою сетку s10 не строит.
2. **Состояние с задержкой d** = строка выжимки с наибольшим `second ≤ t − d` (`pd.merge_asof`, `direction="backward"`). Строка выжимки уже хранит последний кадр ≤ своей секунды, а секунды выжимки идут без дыр, поэтому это ровно «последний кадр ≤ t − d».
   - scoreboard: d = 1 с;
   - таблица и схема карты: d = 10 с;
   - флаг паузы: d = 0. Это пауза обучения. Сдвиг +1 с для бэктеста делает STEP-013.
3. **Таблица состояний на секунду выжимки.** Сначала s10 считает признаки на каждой секунде выжимки, потом делает три `merge_asof` к сетке рынка. Таблица строится из `seconds.parquet`, `players.parquet`, часов раундов карты и loadout bo3:
   - **сторона A** — `team_a_side` раунда строки → `team_a_ct` (bool);
   - **счёт A и B** на секунду s — число выигранных раундов с `won_at ≤ s` (`np.searchsorted` по отсортированным временам побед A и B). Кадр победы попадает в строку s тогда и только тогда, когда `won_at ≤ s`;
   - **фаза** (int): −1 до карты, 0 заморозка, 1 раунд, 2 бомба установлена, 3 после победы. Если `won_at ≤ s`, фаза = 3, иначе фаза кадра. Так установка после победы (0→1→3→2) — фаза 3, а не бомба;
   - **бомба установлена** = фаза 2 после этой правки. Колонка дублирует фазу, но её требует feature.json;
   - **`phase_clock_s`** (float): в фазе 0 — `s − started_at раунда` (растёт и в паузе: 20 → 35 при таймауте), в фазах 1–3 — `timer_s` кадра (после установки это таймер бомбы). Константа «20 с заморозки» не нужна;
   - **номер раунда, половина, овертайм, пистолетный раунд** — из `round_number`: половина = 1 до 12-го, 2 до 24-го, дальше 3 + (r − 25) // 3; овертайм = r > 24; пистолетный = r ∈ {1, 13};
   - **живых у A и B** — число `is_alive` по стороне игрока в секунде, сторона A — по раунду строки. Значение обрезается сверху до 5 (`CS2_TEAM_SIZE`): тренер жив только в заморозке и давал бы 6. Ниже 5 не правим: 4 в заморозке бывает и в live при игре вчетвером;
   - **loadout A и B** — `equipment_value` раунда строки по стороне команды, если фаза строки ≠ 0. В фазе 0 — NaN. После `merge_asof` с d = 10 значение появляется ровно в конце заморозки + 10 с и держится до заморозки следующего раунда + 10 с. Это и есть «loadout пустой до конца заморозки + 10 с».
4. **Строки до карты.** Если для `t − d` нет строки выжимки, группа берёт значения «до карты»:
   - scoreboard: фаза −1, счёт 0:0, разница 0, `phase_clock_s` NaN, бомбы нет. Раунд, половина, овертайм, пистолетный и сторона A — значения раунда 1 (табло до старта показывает раунд 1 и стороны после вето);
   - таблица: живых 5 и 5, loadout NaN;
   - пауза: нет.
   Счёт серии, номер карты и код карты известны до карты и стоят во всех строках.
5. **Колонки рынка и цели — имена s09/Dota**: `market_status`, `market_p_radiant`, `market_p_radiant_change_10s`, `market_radiant_prior`, `signal_market_p_radiant_300s`. Признаки игры — `team_a_*` / `team_b_*`. Исход карты (`team_a_win`) в датасет не идёт: его нет в feature.json, бэктест берёт его из каталога.
6. **Карты без prior остаются**, prior = NaN. Dota такие карты выкидывает (`exclude_missing_priors`, `prepare_dataset.py:123`). Здесь я оставляю: feature.json велит хранить все секунды, LightGBM работает с NaN, а у такой карты (184073) и так почти нет секунд `ok` (31 из 2 273). s10 печатает число таких карт. Если оркестратор хочет как в Dota — это одна проверка в цикле.
7. **Карта без файла s09 пропускается** (s09 не пишет файл при `no_books` и `books_pending`). s10 печатает их число и id. Файл s09 с другим `condition_id` или другим набором секунд → `RuntimeError("match <id> rerun cs2-market-data")`. Каталог без строки `replay_maps` со статусом `ok`, без часов раундов или без папки выжимки → `RuntimeError("match <id> rerun cs2-replays")`. Нет `bo3_match_id` в связях → `RuntimeError("match <id> rerun cs2-bo3")`. Так же строго, как s06 и s09.
8. **Файлы, как у LoL:** `training.parquet` (все строки карт train), `validation.parquet` (все строки карт validation), `production_training.parquet` (train + validation), `split.parquet` (строка на карту: `match_id, event_id, start_time, split`). Split — `assign_split` из s07 на `spawn_at` каталога, чтобы правило было одно. Отдельных «артефактов бэктеста» нет: `validation.parquet` уже несёт сигнал, статус и паузу на каждую секунду, остальное STEP-013 берёт из каталога.
9. **Запись потоком.** Полный датасет — около 37 млн строк (15 тыс. карт × ~2 500 с). В памяти pandas это ~11 ГБ, на диске ~0,7 ГБ на копию. s10 пишет через `pyarrow.parquet.ParquetWriter` с фиксированной схемой: копит кадры карт до ~1 млн строк и пишет одну row group. Файлы пишутся в `<имя>.tmp` и заменяются через `replace` в конце прогона, как `write_parquet`.
10. **Без пула процессов.** Прототип — 0,02 с на карту, 15 тыс. карт — ~5 минут. Карты идут в порядке каталога (`spawn_at`, `match_id`), файлы при повторном прогоне выходят байт в байт. Если пилот покажет больше 30 минут, добавить `ProcessPoolExecutor.map`, как в s09.
11. **Задержки — константы модуля s10**: `SCOREBOARD_DELAY_SECONDS = 1`, `TABLE_DELAY_SECONDS = 10`, `PRE_MAP_PHASE = -1`, `CS2_TEAM_SIZE = 5`. STEP-013 импортирует `SCOREBOARD_DELAY_SECONDS` для сдвига паузы.

## 4. Изменения по файлам

### `src/cs2/s10_prepare_dataset.py` (новый, ~300 строк)

Контракт строки — `Cs2DatasetRow(TypedDict)`, список колонок — `DATASET_COLUMNS = list(Cs2DatasetRow.__annotations__)`, как `SECOND_COLUMNS` в `s05_replay_contract.py`. s11 импортирует колонки отсюда.

| Колонка | Тип | Группа |
| --- | --- | --- |
| `match_id`, `start_time`, `second`, `state_ts_us` | int64 | ключ |
| `event_id` | string | ключ |
| `market_status` | string | статус секунды |
| `market_p_radiant`, `market_p_radiant_change_10s`, `market_radiant_prior` | float64 | рынок |
| `signal_market_p_radiant_300s` | float64 | цель |
| `paused` | bool | пауза, d = 0 |
| `team_a_score`, `team_b_score`, `score_diff`, `round_number`, `half`, `team_a_series_score`, `team_b_series_score`, `game_number`, `map_code`, `phase` | int64 | scoreboard, d = 1 |
| `team_a_ct`, `is_overtime`, `is_pistol_round`, `bomb_planted` | bool | scoreboard, d = 1 |
| `phase_clock_s` | float64 | scoreboard, d = 1 |
| `team_a_alive`, `team_b_alive` | int64 | таблица, d = 10 |
| `team_a_loadout`, `team_b_loadout` | float64 | таблица, d = 10 |

`start_time` = `ceil(spawn_at)`, как `start_time` у Dota и LoL (разрезы s11 по месяцам).

Функции (имена — глаголы; кортежи из нескольких полей — frozen dataclass):

1. `read_ok_replay_maps(bo3_dir) -> dict[int, ReplayMapMeta]` — строки `replay_maps.parquet` со статусом `ok`: `game_number`, `map_code` (`CS2_MAP_CODES[map_name]`), счёт серии.
2. `read_bo3_match_ids(bo3_dir) -> dict[int, int]` — `bo3_game_id → bo3_match_id` из связанных строк `links.parquet`, один проход.
3. `read_round_clocks(bo3_dir) -> dict[int, pd.DataFrame]` — `round_clock.parquet`, колонки `bo3_game_id, round_number, started_at, won_at, team_a_side, team_a_won`, группы по карте.
4. `read_market_seconds(market_dir, row) -> pd.DataFrame | None` и `check_market_seconds(frame, row)` — нет файла → None; другой `condition_id` или секунды ≠ `range(-60, last + 1)` → `RuntimeError`.
5. `build_state_frame(seconds, players, rounds, equipment) -> pd.DataFrame` — таблица состояний на секунду выжимки (решение 3). Внутри мелкие функции: `count_alive`, `attach_loadout`, `normalize_phase`, `count_score`, `number_half`. Каждая проще порога ruff C901 = 10.
6. `lookup_delayed(times, state, delay, columns) -> pd.DataFrame` — `merge_asof` по `t − delay`.
7. `fill_pre_map(...)` — значения «до карты» (решение 4).
8. `build_map_frame(job: MapJob) -> pd.DataFrame` — одна карта: файл s09 + три поиска + постоянные поля карты, типы через `apply_table_dtypes` (`s03_fetch_grid_rounds.py:395`). `MapJob` — frozen dataclass: строка каталога, мета карты, часы раундов, пути выжимки и JSON bo3, путь файла s09.
9. `DatasetWriters` (или функции `open_writers` / `append_frame` / `close_writers`) — три `ParquetWriter` (`training`, `validation`, `production_training`) с буфером до ~1 млн строк, `.tmp` → `replace`.
10. `build_split_frame(rows) -> pd.DataFrame` — `split.parquet` через `assign_split`.
11. `build_cs2_dataset(catalog_dir, market_dir, bo3_dir, bo3_raw_dir, output_dir) -> DatasetSummary` и `print_summary`: `maps`, `train_maps`, `validation_maps`, `no_market_seconds` с id, `missing_prior`, строки по трём файлам.
12. `main` (typer, как s09): `--catalog-dir` (`CS2_CATALOG_DIR`), `--market-dir` (`CS2_MARKET_SECONDS_DIR`), `--bo3-dir` (`CS2_BO3_DIR`: `replay_maps`, `round_clock`, `round_state`, `links`), `--bo3-raw-dir` (`CS2_RAW_BO3_DIR`), `--output-dir` (`CS2_DATASETS_DIR`). Код выхода 0. Пропуск карт без s09 — не ошибка: s09 уже вернул 1 на `books_pending`, и `make` остановился раньше.

Импорты, не копии: `read_catalog_rows`, `Cs2CatalogRow` (s06); `build_cache_path`, `compute_zero_second`, `compute_last_second` (s09); `read_equipment`, `EquipmentKey` (s05_check_live); `apply_table_dtypes`, `parse_utc` (s03); `assign_split` (s07); `CS2_*` пути и `CS2_MAP_CODES` (constants); `MODEL_START_SECOND`, `VALIDATION_START_TIME` (shared).

Комментариев в новом коде нет (правило ревью этого task). Одно исключение по правилу ponytail — строка `# ponytail: serial loop, add ProcessPoolExecutor.map if the pilot shows > 30 min` у цикла по картам.

### `Makefile`

```make
cs2-prepare: cs2-market-data  ## CS2 s10 1 Hz dataset: training / validation / production_training / split
	$(PY) src/cs2/s10_prepare_dataset.py
```

- `cs2-prepare` добавить в `.PHONY`.
- `$(ARGS)` сюда не передаём: `cs2-market-data` уже передаёт `ARGS` в s09, а у s09 и s10 разный смысл `--output-dir`. Пробы зовут скрипт напрямую.
- Один prerequisite под `make -j` безопасен: рецепт ждёт prerequisite. Проблема STEP-008 была в порядке между соседними prerequisites.
- В `cs2-collect` не входит, как `cs2-books` и `cs2-market-data`.

### `tests/test_cs2_dataset.py` (новый)

Фикстура — одна синтетическая карта на ~40 секунд в `tmp_path`, два раунда (12 и 13, смена сторон) или три, если удобнее. Писать через рабочие функции, без ручных parquet:

- выжимка — `write_map_summary` (s05) из `MapSummary`;
- часы раундов и карта — `clock_frame` и `maps_frame` из s05 (`s05_fetch_bo3_replays.py:592`, `:604`);
- связи — кадр с `LINK_COLUMNS` (s04), как `write_links` в `tests/test_cs2_check_live.py:309`;
- JSON bo3 — `write_match_games` (s04) с `equipment_value` на раунд и сторону;
- каталог — `catalog_frame` (s06); файл s09 — `build_market_seconds_frame` + `write_parquet` в `build_cache_path`.

Значения фикстур, которые несут несколько полей, — frozen dataclass (урок STEP-005, -006, -010).

Тесты из feature.json:

1. `test_scoreboard_lags_one_second_and_table_ten` — победа A с `won_at` внутри секунды S: строка t = S ещё со старым счётом, t = S + 1 — с новым. Смерть игрока B в секунде K: `team_b_alive` падает в t = K + 10, не в K + 9.
2. `test_rows_before_first_frame_are_pre_map` — секунды −60…0: фаза −1, счёт 0:0, `phase_clock_s` NaN, бомбы нет; секунды −60…9: живых 5 и 5, loadout NaN; `paused` False.
3. `test_freeze_clock_counts_up_through_pause` — заморозка с таймером 20 → −10: `phase_clock_s` = 30 в нужной строке, не −10; `paused` True в секунде t без задержки.
4. `test_team_a_orientation_follows_side_switch` — A — CT в раунде 12 и T в раунде 13: `team_a_ct` меняется; `team_a_alive` и `team_a_loadout` берутся по стороне T в раунде 13 (loadout A = `equipment_value` стороны T). Шестой id с `is_alive` в заморозке не даёт 6 живых.
5. `test_paused_and_not_ok_seconds_stay` — секунда `wide_spread`, секунда паузы и секунда с пустой целью есть в `validation.parquet`; строк ровно last + 61.

Ещё два коротких теста на прогон:

6. `test_run_splits_maps_and_skips_missing_market` — две карты по обе стороны `VALIDATION_START_TIME`, третья без файла s09: train 1, validation 1, production 2, `split.parquet` 2 строки, `no_market_seconds` = [id третьей].
7. `test_stale_market_seconds_raise` — файл s09 с другим `condition_id` → `RuntimeError` с `rerun cs2-market-data`.

## 5. Крайние случаи

1. **Раунд 1 начался позже `spawn_at`.** Строк «до карты» больше 61: их число задаёт `merge_asof`, а не константа.
2. **Последний раунд.** Фазы 3 в кадрах нет, `won_at` = конец карты. Счёт последней строки не включает победный раунд. Это верно для d = 1: табло увидит счёт после конца карты.
3. **Установка после победы (0→1→3→2).** Фаза 3, `bomb_planted` False. Таймер этих секунд — таймер бомбы из реплея. Что live видит в эти ~5 с, я не знаю (16 раундов из 814).
4. **Тренер.** Живых обрезаем до 5 (раздел 2, п. 11).
5. **Id без стороны.** В заморозке бывает 4 живых (185135, 30 с). Не правим: признак врёт на 1 в редких секундах заморозки.
6. **`equipment_value` = None или раунда нет в JSON bo3.** Loadout NaN. Раунды bo3 и GRID совпадают по отпечатку s04, так что второе не ожидается.
7. **Паузы.** Секунды паузы остаются, `paused` = флаг выжимки в секунде t. Перерыв в фазе 3 раунда 12 и пауза в заморозке раунда 13 оба попадают в флаг.
8. **Карта без prior.** Остаётся, prior NaN (решение 6).
9. **Граница split.** `spawn_at` = `VALIDATION_START_TIME` → validation (≥, как в s07).
10. **Пустой каталог или все карты без s09.** Четыре файла с правильной схемой и 0 строк.
11. **Повторный прогон.** Тот же порядок карт и тот же буфер → те же байты. Проба проверяет `cmp`.

## 6. Проба в scratch (имплементер)

Папка `work/impl011/`. Входы — готовые выходы проб 10.09:

- каталог: `work/impl008/sep10/catalog`;
- секунды рынка: `work/impl010/sep10` (`vf2207759` = текущий `CACHE_VERSION`);
- `bo3`: собрать `work/impl011/sep10/bo3/`: копии `links.parquet` и `replay_maps.parquet` из `work/impl008/sep10/bo3/`, копия `round_clock.parquet` и симлинк `round_state` на `work/impl006/bo3/`;
- сырые bo3: `work/impl005/bo3_raw`;
- выход: `work/impl011/sep10/datasets`.

Ожидаю цифры таблицы раздела 2, п. 11: 6 карт, все validation, train 0; 14 869 строк в `validation.parquet` и `production_training.parquet`, 0 в `training.parquet`; паузы 216 / 306 / 160 / 290 / 334 / 114; 6 399 строк с `ok`, без паузы и с целью. Второй прогон — `cmp` всех четырёх файлов. Настоящая `data/cs2/datasets/` остаётся пустой: каждый запуск пробы передаёт все пять `--*-dir`.

## 7. Проверки

1. `uv run pytest tests/test_cs2_dataset.py -q` — все тесты проходят.
2. `make test` — прошлый итог плюс новые тесты. Известные 2 падения `lol_*` follow300 smoke (нет `data/lol` в worktree) и иногда `dota_9007700576` под `PYTEST_N=8` — не новые.
3. `uv run basedpyright` — 0 ошибок.
4. `uv run ruff check src/cs2/s10_prepare_dataset.py tests/test_cs2_dataset.py` и `uv run ruff format --check` на тех же файлах.
5. `make -n cs2-prepare` — сначала s09, потом s10.
6. Проба раздела 6: цифры совпали, второй прогон байт в байт, `data/cs2/datasets/` пустая.

## 8. Риски и открытые вопросы

1. **Сверка 4 решает судьбу двух признаков.** Loadout и таймер фаз 1–3 зависят от решения пользователя после пилота. s10 пишет обе колонки. Убрать loadout — правка списка признаков s11. Заменить таймер на «секунд с начала фазы» — правка одной функции s10 и перезапуск `cs2-prepare`.
2. **Строки до карты.** Сторона и раунд 1 до старта карты взяты из раунда 1. Если live до старта сторон не знает, бэктест видит на 60 с больше, чем live. Влияние малое: до старта рынок стоит около prior.
3. **Карты без prior** остаются (решение 6), Dota их выкидывает. Число таких карт даст пилот.
4. **Память s11.** `training.parquet` — десятки миллионов строк. Загрузчик STEP-012 должен читать только нужные колонки и фильтр `ok`, не на паузе, цель есть (`pd.read_parquet(columns=..., filters=...)`), иначе ~11 ГБ.
5. **Скорость на холодном диске.** 0,02 с на карту замерены на тёплом кэше 6 карт. Время на всём каталоге даст пилот (решение 10).
6. **Шум id игроков.** Тренер и id без стороны задают число живых в заморозке. В live число живых считает GRID и таких артефактов нет. Обрезка до 5 закрывает тренера, id без стороны остаётся шумом.

## 9. Чего в шаге нет

- Обучения, загрузчика и списка признаков модели — STEP-012.
- Сдвига паузы +1 с и сигнала бэктеста — STEP-013.
- Истории признаков, cash, HP, kill gate.
- Изменений s05–s09 и общего ядра `shared/utils/gbm.py`.
- Прогона на полных данных: только тесты и проба на 6 картах 10.09.
