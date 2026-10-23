# STEP-007 — Сверка 4: реплей против live-записей GRID 17.10

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `ab6ae4ef`. Figma-ссылок в шаге нет.

## 1. Что делает шаг

Новый скрипт `src/cs2/s05_check_live.py` только читает. Он берёт live-записи GRID 17.10 (`data/cs2/live/cs2-*-2026-10-17/grid.jsonl`) и выжимку s05 тех же карт. По 12 пунктам он сравнивает, что видел live, с тем, что даёт реплей:

- старт заморозки, конец заморозки, установку бомбы;
- таймер в фазах 1, 2 и 3;
- начало и конец перерыва, начало и конец паузы в заморозке;
- смерти;
- loadout.

По каждому пункту скрипт печатает число сравнений, совпадений и квантили разницы и пишет то же в `data/cs2/bo3/live_check.json`. Порогов «сходится» нет: цифры смотрит пользователь на остановке после пилота (Resolved Questions). Каталог и статусы карт сверка 4 не меняет.

Сейчас `data/cs2/links/` пуст, `data/cs2/bo3/round_state/` нет, `live_check.json` нет. Настоящий прогон будет в пилоте (STEP-014). В этом шаге — код, тесты на вырезке и проба в scratch (раздел 6).

## 2. Что я проверил (факты)

Скрипты — в `tasks/cs2-follow300/orchestrate/work/plan007/`. К API ушло 5 запросов: список матчей bo3 за 17.10 и игры матча 131671 (`bo3_probe.py`), два файла реплея. Остальное — локальные записи.

1. **Записи.** 12 папок `cs2-*-2026-10-17`. Набор каналов один на запись: обычный — `ence-pha`, `fal2-bb3`, `furia-aur1`, `offcho-rstr`, `wbt-havu`; `integrity_safe_*` — `9inea-got1`, `bbp-don`, `eace-rgg`, `hon1-titanf`, `lavked-skr2`, `revise-ill4`; `mibr-100t` — пустой `grid.jsonl`. `parse_frame` (`src/trader/grid_widgets.py:108`) снимает префикс `SERVICE_PREFIX` (`:25`), дальше оба набора разбираются одним кодом.
2. **Карты с live-кадрами** (`disconnects.py`): 18. Обычный набор — 8 (ence 2, fal2 3, furia 2, wbt 1 — только карта 3), урезанный — 10 (9inea 1 — только карта 2, bbp 2, eace 3, hon1 2, lavked 2). У `offcho`, `revise`, `mibr` live-карт нет. Маркеры `connect`/`disconnect` потока `grid` внутри live-карт: bbp-don 1, fal2-bb3 4, lavked-skr2 4 (два обрыва по 3 с в fal2 и в lavked). Остальные маркеры — до и после карт.
3. **Все 12 событий есть в `data/cs2/universe/events.parquet`**, у 11 есть `grid_series_id` (у `mibr-100t` нет). 17.10 стартуют 38 событий, все BO3. Поле `seriesId` есть в каждом кадре scoreboard (`"3006816"` у FURIA): связь записи с `series_id` s02 не требует `target.json`.
4. **Scoreboard** (`dump_shapes.py`, FURIA 15:23:46). Тип — `ScoreboardPayload` (`src/trader/grid_widget_types.py:94`), форма та же, что у Dota. У карты: `mapName` (`"dust2"`), `status`, `teams[].score` (раунды), `teams[].infoText.text` (`"CT"`/`"T"`), `gameClock.currentSeconds` (целое), `isTicking`, `tickingBackwards` (`true`), `color`, `occurredAt`. Активная карта — `games[activeGameIndex]`; индекс бывает вне списка (FURIA 6 кадров).
5. **Установка бомбы** (`sb_trace.py`, FURIA 14:01:26). Первый красный кадр — `color = "error.0"`, `currentSeconds` 79 (старое значение). Через 6 мс — кадр 40, тоже `error.0`. Цвет `error.0` есть и в урезанном наборе (`hon1-titanf`: 69 кадров).
6. **`occurredAt` не обновляется на кадре «20 с».** FURIA 14:02:12.662: таймер 20, `occurredAt` 14:02:12.311 — как у трёх кадров до него. FURIA 14:25:05.928: таймер 20, `occurredAt` 14:25:05.435 — как у трёх кадров с таймером 56. Время по `occurredAt` для старта заморозки неверно.
7. **Таймер live в фазе 3 не совпадает с реплеем по определению.** Live продолжает часы раунда (FURIA 14:25:01.040 счёт 9:4 при 61 → 14:25:05.613 при 56) или стоит на 0 после взрыва (14:02:07 → 14:02:12.6). Реплей в фазе 3: 5 → 0 или 0 → −6,9 (PLAN, «Фазы таймера»).
8. **Кадры «20 с»** (`freeze_rule.py`, 11 записей, окно = карта + сумма счёта). Из окон с кадром «115 с»: в 316 один кадр «20 с» до «115 с», в 31 — от 2 до 7, в 2 — ни одного. Без кадра «115 с» — 22 окна (почти все — последнее окно карты). Примеры:
   - первый кадр «20 с» бывает отсчётом фазы 3, а не сбросом: 9inea карта 2, окно 13 — красный 27 → красный 20 через 6,81 с → белый 20 через 7,05 с;
   - старт карты с перезапусками: eace карта 3, окно 0 — «20 с» через 0,40, 95,47 и 98,61 с;
   - перезапуски заморозки в технической паузе: bbp-don карта 2, окно 6 — «20 с» через 7,18, 183,70, 570,60, 573,93 с;
   - тактический таймаут: 9inea карта 2, окно 4 — 26, 30, 20, 30 в пределах 0,1 с.
9. **Таймаут FURIA, карта 2, раунд 7** (счёт 4:2, `sb_trace.py`). Счёт 4:2 — 15:12:25.895 (таймер 0). Первый «20 с» — 15:12:31.392. Кадр «2 с» — 15:12:48.690, кадр «30 с» — 15:12:48.745. Таймер 0 — 15:13:18.562, `isTicking = false` — 15:13:19.219. `isTicking = true` — 15:14:14.594, второй «20 с» — 15:14:14.788, «115 с» — 15:14:38.737. Правило плана даёт старт паузы 15:12:51.392. Ноль от кадра «30 с» — 15:13:18.745, от второго «20 с» + 20 с — 15:14:34.788. Перед окном есть кадр со счётом 4:1 (15:12:20.862) — граница вырезки фикстуры.
10. **Таблица** (`table_outline.py`, `table_groups.py`, `freeze_groups.py`). Группы `Series`, `Game`, `Round`, `Freezetime`, иногда `Timeout`. В `Game` ровно одно состояние, `sequenceNumber` = номер текущей карты; в `Round` — одно, `sequenceNumber` = номер раунда карты. Строки `Team Summary`: в `Game` — `Side` (`terrorists`/`counter-terrorists`), `LoadoutValue` (сумма команды); в `Round` — `Side`, `Deaths` (смерти команды за раунд). `Freezetime` → `General` → `Finished`. В урезанном наборе структура та же (`hon1-titanf` 11:50:25). Тип внешнего контейнера — `SeriesTablePayload` (`grid_widget_types.py:159`); строки там дотовские (`TableRow` с `NetWorth`), поэтому строки CS2 нужно описать своими TypedDict.
11. **Loadout в кадре конца заморозки** (`loadout_cmp.py`, FURIA, обе карты, bo3 `bo3_games_131671.json`). Кадр — первый кадр таблицы раунда, где `Freezetime.Finished` стал `true`. Раунд 14 карты 1: кадр 14:25:33.648 — CT 17 300, T 1 500; через 8 с (14:25:41.662) CT 19 700; bo3 `equipment_value` CT 20 200, T 1 500. По 88 парам «команда-раунд»:

    | Кадр live | Точно | p50 \|разн.\| | p90 | max |
    | --- | ---: | ---: | ---: | ---: |
    | первый с `Finished = true` | 47 | 0 $ | 900 $ | 3 400 $ |
    | максимум за раунд | 44 | 50 $ | 900 $ | 5 900 $ |
    | второй с `Finished = true` | 2 | 2 200 $ | 5 200 $ | 10 100 $ |

    Первый кадр конца заморозки — верный выбор. `LoadoutValue` падает на каждой смерти (FURIA раунд 13: 2 400 → 1 550 → 1 050 → 850 → 0).
12. **bo3 для 17.10 уже есть.** Матч FURIA–Aurora — 131671, тир S; карты 189516 (dust2, 21 раунд) и 189515 (inferno, 23 раунда). `files.bo3.gg/round_maps/189516/1.json` и `189515/23.json` — 200, ~2 МБ. В `game_round_team_clans[]` есть `team_side` и `equipment_value`. `Bo3RoundClan` в s04 (`src/cs2/s04_link_bo3.py:86`) знает только `team_side` и `clan_name`. В старой таблице `data/cs2/bo3/rounds.parquet` `equipment_value` пуст у 28 из 105 139 раундов.
13. **Прототип правил окон** (`window_rules.py`, правила решений 3–5): полных окон 365, с парой «20 с» + «115 с» — 346, установок — 157, пауз в заморозке — 113, перерывов — 21, неполных окон — 4, выброшено из-за обрыва — 2.
14. **Что можно переиспользовать:** `parse_frame`, `SCOREBOARD_SERVICE`, `TABLE_SERVICE`, `SERVICE_PREFIX`, `LIVE_STATUS` (`grid_widgets.py`); `ScoreboardPayload`, `SeriesTablePayload`, `TableEntity` (`grid_widget_types.py`); `is_halftime_round` (`s05_parse_replay.py:133`); `linked_bo3_rows` (`s05_fetch_bo3_replays.py:174`), `read_summary_rounds` (`:528`); `normalize_map_name` (`s04_link_bo3.py:236`), `require_match_games` (`:513`); `parse_utc` (`s03_fetch_grid_rounds.py:267`); `write_json` (`shared/utils/json_io.py:17`); `replace_nulls_with_none` (`shared/utils/parquet_io.py:15`). Из `scripts/cs2/inspect_live.py` берём только правила: отбрасывать кадр с тем же payload, что у прошлого кадра этого канала (`:1169`), маркеры `connect`/`disconnect` (`:1117`). Импортировать `scripts/` нельзя.

## 3. Решения

1. **Время live — `received_at_utc`** (факт 6). Разница live − реплей включает задержку приёма (медиана 0,16–0,22 с по PLAN) и зависания scoreboard. Это то, что увидит трейдер, и именно это проверяют задержки признаков 1 с и 10 с. Часы записи сходятся с часами GRID: задержка приёма от `occurredAt` — 0,16–0,22 с.
2. **Сравниваем только карты со статусом `ok`** в `replay_maps.parquet`. Карты с другим статусом не идут в обучение. Их скрипт печатает и пишет в JSON со статусом, но не сравнивает. Live-карта без строки в `replay_maps` получает статус `no_replay_map`.
3. **Окно раунда (scoreboard).** Кадры активной карты со статусом `live` и двумя командами группируем по ключу (номер карты, сумма счёта k). Окно k — раунд k + 1. Номер карты = `activeGameIndex` + 1. Окно полное, если:
   - k ≥ 1 и в записи есть окно (карта, k − 1), то есть смену счёта live видел;
   - k = 0 и в окне есть кадр «20 с».
   Окно с маркером `disconnect` (поток `grid`) между его первым и последним кадром выбрасываем. Неполные и выброшенные окна не дают записей ни live, ни реплея: такие раунды запись не покрыла.
4. **События live в полном окне:**
   - конец заморозки — первый кадр с `currentSeconds` = 115;
   - старт заморозки — первый кадр с `currentSeconds` = 20 до конца заморозки (решение пользователя: «кадр старта заморозки (таймер 20 с)»; первый, а не последний — факты 8, 9);
   - установка — первый кадр с `color = "error.0"` после конца заморозки. Красный цвет в начале окна — бомба прошлого раунда (факт 8, 9inea окно 4), он не считается;
   - без кадра «115 с» все три события окна — пустые.
5. **Паузы.** Live:
   - перерыв — окно k, если `is_halftime_round(k)`: начало — первый кадр окна (смена счёта), конец — старт заморозки;
   - пауза в заморозке — начало = старт заморозки + 20 с, конец = конец заморозки, если конец заморозки позже начала.

   Реплей — серии подряд идущих секунд `paused = true` в `seconds.parquet`. Перерыв — фаза 3 раунда k, пауза в заморозке — фаза 0 раунда k + 1. Начало серии — первая секунда, конец — последняя секунда + 1. Берём целые секунды флага, а не моменты: обучение и бэктест (со сдвигом +1 с) работают именно с ними. Пары — по (карта, раунд, вид паузы).
6. **Таймер.** Запись — каждый кадр scoreboard сравниваемой карты (все окна, без отбора по полноте). Пусть t — время кадра, s = floor(t). Нужны строки реплея на s и s + 1 с тем же раундом и той же фазой из {1, 2, 3}, обе не `paused`. Иначе кадр пропускаем: это граница фазы или пауза. Таймер реплея в момент t = `timer_s(s)` − (t − s), ошибка ≤ 0,125 с (8 кадров в секунду). Live — `currentSeconds`. Пункты: `timer_phase_1`, `timer_phase_2`, `timer_phase_3`: решение «показание таймера или секунды с начала фазы» пользователь принимает по каждой фазе. Фазу 0 не сравниваем (PLAN).
7. **Смерти — из таблицы, группа `Round`, в обоих наборах.** Схему карты не берём: таблица есть в обоих наборах, а признак «живых» в live тоже идёт с кадров таблицы на смертях. Смерть live — рост `Deaths` стороны между соседними кадрами одного раунда, по одной записи на единицу роста, время — время кадра. Падение `Deaths` не считаем. Реплей — `kills.parquet` того же раунда по `victim_side`, по времени `at`. i-я смерть live стороны X ↔ i-е убийство реплея с жертвой X. Лишние смерти с одной стороны — записи без пары.
8. **Loadout.** Кадр — первый кадр таблицы раунда N, где `Freezetime.Finished = true` (факт 11). Значение — `LoadoutValue` строки `Team Summary` группы `Game` по `Side`. Реплей — `equipment_value` раунда N со стороной `team_side` из кэша s04 `raw/bo3/games/<bo3_match_id>.json` (`require_match_games`), `bo3_match_id` — из `links.parquet` s04. Пустой `equipment_value` — запись без значения реплея.
9. **Полный раунд таблицы:** первый кадр таблицы этого раунда показывает 0 смертей у обеих сторон, и между первым и последним кадром раунда нет маркера `disconnect`. Иначе раунд не даёт записей смертей и loadout.
10. **Сводка.** Запись сравнения — (пункт, набор каналов, значение live или пусто, значение реплея или пусто). Строка сводки — по пункту и набору: `normal`, `integrity_safe`, `all`. Поля:
    - `comparisons` — все записи;
    - `both` — с обоими значениями;
    - `matches` — с обоими значениями и |разница| ≤ допуска;
    - `live_missing`, `replay_missing`;
    - `p50`, `p90`, `p99`, `max` — по |разнице| записей `both`; пусто, если `both` = 0;
    - `median_signed` — медиана разницы со знаком (live − реплей): видно смещение часов.

    Допуски только подписывают «совпадения», решения они не принимают. Scoreboard-пункты и паузы — 1 с (задержка признаков scoreboard и сдвиг паузы в бэктесте). Смерти — 10 с (задержка признаков таблицы). Loadout — 0 $.
11. **Связь записи с выжимкой.** Карты записи — номера карт из кадров scoreboard и таблицы. `seriesId` из scoreboard + номер карты → строка `replay_maps.parquet` (`series_id`, `game_number`). Если у карты есть кадры scoreboard и `normalize_map_name(mapName)` не равно `map_name` строки — `RuntimeError`: связь неверна, это ошибка кода, а не данных. `map_name` в выходе — из `replay_maps` (пусто без строки).
12. **Только 17.10.** Константа `LIVE_RECORDING_GLOB = "cs2-*-2026-10-17/grid.jsonl"`, как в тексте шага. Папка `event_study` под шаблон не попадает.
13. **Тип equipment — свой TypedDict в скрипте**, `cast` у чтения. s04 не трогаем: его код поле не читает.
14. **Цели Makefile нет.** В списке целей PLAN её нет; пилот запускает скрипт напрямую. Добавить можно потом одной строкой.

## 4. Изменения

### 4.1 `src/cs2/constants.py`

```python
CS2_LIVE_DIR = CS2_DATA_DIR / "live"
CS2_LIVE_CHECK_PATH = CS2_BO3_DIR / "live_check.json"
```

### 4.2 `src/cs2/s05_check_live.py` (новый, ~600 строк)

Если файл выйдет больше 700 строк — разбор кадров (TypedDict таблицы, `read_recording` и читатели кадров) уходит в `src/cs2/s05_live_frames.py`. Комментариев не писать (`.feature-json.config.json`, review).

Константы: `LIVE_RECORDING_GLOB`, `FREEZE_SECONDS = 20` (и значение кадра старта, и длина заморозки до паузы), `FREEZE_END_SECONDS = 115`, `PLANT_COLOR = "error.0"`, `TABLE_SIDES = {"terrorists": "T", "counter-terrorists": "CT"}`, `QUANTILES = (0.5, 0.9, 0.99)`. Литералы: `ChannelSet = Literal["normal", "integrity_safe"]`, `CheckItem` (12 пунктов в порядке печати: `freeze_start`, `freeze_end`, `plant`, `timer_phase_1`, `timer_phase_2`, `timer_phase_3`, `break_start`, `break_end`, `freeze_pause_start`, `freeze_pause_end`, `deaths`, `loadout`), `ITEM_TOLERANCES: dict[CheckItem, float]`, `ITEM_UNITS` (`"s"` или `"usd"`).

Типы внешнего JSON (TypedDict, `cast` после `json.loads`):

- scoreboard — `ScoreboardPayload` как есть;
- таблица — внешний контейнер `SeriesTablePayload`, строки — `cast` по имени группы в свои типы: `Cs2TextCell` (`value: str`), `Cs2IntCell` (`value: int`), `Cs2FlagCell` (`value: bool`), `Cs2GameSummaryRow` (`Side`, `LoadoutValue`), `Cs2RoundSummaryRow` (`Side`, `Deaths`), `Cs2GeneralRow` (`Finished`);
- bo3 — `Bo3ClanEquipment` (`team_side: str`, `equipment_value: int | None`).

Свои значения — frozen dataclass:

- `ScoreboardFrame` (`received_at` эпоха, `map_number`, `map_name`, `score_sum`, `current_seconds`, `color`);
- `SideValues` (`ct: int`, `t: int`);
- `TableFrame` (`received_at`, `map_number`, `round_number`, `freeze_finished`, `deaths: SideValues`, `loadout: SideValues`);
- `LiveRecording` (`name`, `channel_set`, `series_id`, `scoreboard`, `table`, `disconnects` — кортежи);
- `RoundWindow` (`map_number`, `score_sum`, `frames: tuple[ScoreboardFrame, ...]`);
- `LiveRound` (`map_number`, `round_number`, `opened_at`, `freeze_start`, `freeze_end`, `plant` — эпоха или `None`);
- `ReplayMap` (`bo3_game_id`, строки `seconds`, `rounds`, `kills`, `equipment` по (раунд, сторона));
- `Comparison` (`item`, `channel_set`, `live: float | None`, `replay: float | None`).

Строки выхода (TypedDict): `Cs2LiveCheckMapRow` (`recording`, `channel_set`, `series_id`, `game_number`, `map_name`, `bo3_game_id: int | None`, `replay_status`, `compared`, `live_rounds`, `table_rounds`), `Cs2LiveCheckItemRow` (`item`, `channel_set`, `unit`, `tolerance`, `comparisons`, `both`, `matches`, `live_missing`, `replay_missing`, `p50`, `p90`, `p99`, `max`, `median_signed`), `Cs2LiveCheckPayload` (`maps`, `items`).

Функции (глаголы, все аргументы обязательные):

- `read_recording(path) -> LiveRecording | None` — один проход по строкам. Маркер `disconnect` потока `grid` → время в `disconnects`. Кадр → `parse_frame`; только `SCOREBOARD_SERVICE` и `TABLE_SERVICE`; пустой payload и payload, равный прошлому payload этого канала, пропускаем. Набор каналов — по первой строке кадра: `service` начинается с `SERVICE_PREFIX`. Пустая запись → `None`. `seriesId` у всех кадров scoreboard один, иначе `RuntimeError`.
- `read_scoreboard_frame(payload, received_at) -> ScoreboardFrame | None` — активная карта; `None`, если индекс вне списка, статус не `LIVE_STATUS` или команд не две.
- `read_table_frame(payload, received_at) -> TableFrame | None` — `None` без групп `Game` и `Round`.
- `group_round_windows(recording) -> list[RoundWindow]` — только полные окна без обрыва (решение 3); `find_live_round(window) -> LiveRound` — решение 4.
- `read_replay_map(replay_dir, raw_dir, row, match_id) -> ReplayMap` — `seconds.parquet`, `kills.parquet` и `read_summary_rounds` из `round_state/<bo3_game_id>/`; equipment — из `require_match_games`.
- `compare_events(...)`, `compare_pauses(...)`, `compare_timer(...)`, `compare_deaths(...)`, `compare_loadout(...)` → `list[Comparison]` — решения 4–9.
- `summarize_item(comparisons, item, channel_set) -> Cs2LiveCheckItemRow` — решение 10, `numpy.quantile` по |разнице|, числа округлить до 3 знаков.
- `build_live_check(live_dir, bo3_links_dir, raw_dir, replay_dir) -> Cs2LiveCheckPayload`.
- `print_live_check(payload, output) -> None` — строка на карту и строка на (пункт, набор), в конце `saved: <path>`.
- `main(...)`.

CLI (typer, как s05): `--live-dir` (`CS2_LIVE_DIR`), `--bo3-links-dir` (`links.parquet` s04, `CS2_BO3_DIR`), `--raw-dir` (кэш s04, `CS2_RAW_BO3_DIR`), `--replay-dir` (`replay_maps.parquet` и `round_state/` s05, `CS2_BO3_DIR`), `--output` (`CS2_LIVE_CHECK_PATH`). Скрипт пишет только `--output` через `write_json`.

### 4.3 Фикстура `tests/fixtures/cs2/live_furia_cuts.jsonl`

Две вырезки из `data/cs2/live/cs2-furia-aur1-2026-10-17/grid.jsonl` в одном файле, в порядке времени. Скрипт вырезки — в `orchestrate/work/impl007/`, в репозиторий не идёт.

1. Таблица, карта 1, раунды 13–14: строки `series_table` с 14:25:13.970 по 14:25:41.662 (последний кадр раунда 13, начало раунда 14 с 0:0 смертей, кадр конца заморозки 14:25:33.648, кадр 14:25:41.662 со смертью CT и CT 19 700).
2. Scoreboard, карта 2, раунд 7: строки `series_scoreboard_v2` с 15:12:20.862 по 15:14:38.873 (факт 9). Сюда входит кадр со счётом 4:1, поэтому окно 6 полное.

Правила: `received_at_utc`, обёртку кадра (`service`, `delay`, `scope`, `data[0].data`) и значения не менять. Из payload убрать то, что скрипт не читает: `momentum`, `doughnut`, переводы, `logoUrl`, строки `Player`, лишние ячейки. Если тест упал на `KeyError` — вернуть нужное поле. Цель — ≤ 30 КБ.

### 4.4 `tests/test_cs2_check_live.py` (новый)

Помощники: чтение фикстуры через `read_recording`; синтетические строки `seconds`/`rounds`/`kills` выжимки; маленький построитель строк scoreboard для теста 2; таблицы s04/s05 в `tmp_path`.

1. `test_freeze_pause_starts_twenty_seconds_after_freeze_start` (обязательный тест шага). Вырезка 2 + синтетическая выжимка раунда 7: `started_at` 15:12:31.200, фаза 0, таймер 20 − (s − старт), `paused` при таймере < 0, конец заморозки 15:14:38.600. Проверки:
   - старт заморозки live = 15:12:31.392 (первый «20 с», не второй 15:14:14.788);
   - начало паузы live = 15:12:51.392, а не 15:13:18.745 (кадр «30 с» + 30 с) и не 15:14:34.788;
   - конец паузы live = 15:14:38.737;
   - запись `freeze_pause_start`: live − реплей = 15:12:51.392 − 15:12:52 = −0,608 с.
2. `test_break_and_plant_rules` — синтетические кадры: смена счёта на сумму 12, кадр «20 с» через 80 с → перерыв [смена счёта, «20 с»]; красный кадр в начале окна — не установка; первый красный после «115 с» — установка; окно без кадра суммы k − 1 — ни одной записи.
3. `test_loadout_and_deaths_from_table_cut` — вырезка 1 + equipment bo3 раунда 14 (CT 20 200, T 1 500) + одно синтетическое убийство CT в раунде 14. Проверки: loadout live CT = 17 300 (кадр 14:25:33.648, не 19 700), запись CT −2 900 $, запись T 0 $; смерть CT live = 14:25:41.662 и встаёт в пару с убийством реплея. Раунд 13 в вырезке неполный (первый кадр — 5:3 смертей): записей по нему нет.
4. `test_item_summary` — несколько записей: `both`, `matches` по допуску, `live_missing`, `replay_missing`, квантили, `median_signed`, строка набора `all` = сумма двух наборов, пустые квантили при `both` = 0.
5. `test_main_writes_live_check` — `tmp_path` с копией фикстуры в `cs2-furia-aur1-2026-10-17/grid.jsonl`, строкой `replay_maps` (карта 2 `ok`, карта 1 `replay_missing`), `links.parquet`, кэшем игр bo3, выжимкой раунда 7. `main` пишет JSON: карта 2 `compared = true`; карта 1 (в вырезке у неё только кадры таблицы) — статус `replay_missing`, `compared = false`; второй запуск даёт тот же файл байт в байт.

## 5. Крайние случаи

- **Запись началась посреди карты** (wbt-havu карта 3, 9inea карта 2) — первое окно неполное и выпадает (решение 3).
- **Кадр «20 с» пропал** при зависании scoreboard (p95 4,8 / 8,2 с) — старт заморозки и паузы пустые, реплей есть → `live_missing`.
- **Несколько кадров «20 с»** — берём первый. Отсчёт фазы 3 до сброса даёт ошибку ≤ 0,5 с в найденных случаях. Перезапуски на старте карты (eace карта 3: 95 с) дают выбросы в p99/max у раунда 1. Это свойство live-правила, сверка его и показывает.
- **Таймаут (кадр «30 с»)** и `isTicking = false` на начало паузы не влияют (решение 4).
- **Пауза меньше секунды** (заморозка live 20,3 с) — live-пауза есть, у реплея может не быть ни одной секунды → `replay_missing`. Так ведёт себя live-правило.
- **Последнее окно карты** (после последней смены счёта) — раунда k + 1 в реплее нет, записей нет.
- **Обрыв сокета** внутри окна или раунда таблицы — окно и раунд выпадают (решения 3, 9).
- **`activeGameIndex` вне списка** или карта не `live` — кадр пропускаем.
- **Перерыв в овертайме** — суммы 24, 27, 30… через `is_halftime_round`.
- **`equipment_value` пуст** — `replay_missing` у loadout.
- **Карта со статусом не `ok`** — в списке карт, без сравнений.
- **Неверная связь карты** (номер карты GRID ≠ номер в scoreboard) — `RuntimeError` по имени карты (решение 11).
- **Смерть без убийцы** (`killer_side` пуст) — жертва есть, запись считается.

## 6. Проверка

Команды из корня `esports-trader-cs2`.

1. `uv run ruff format src/cs2 tests/test_cs2_check_live.py` и `uv run ruff check src/cs2 tests/test_cs2_check_live.py` — без ошибок.
2. `uv run python -m basedpyright` — 0 errors.
3. `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s05_check_live.py tests/test_cs2_check_live.py tests/fixtures/cs2/live_furia_cuts.jsonl` — passed.
4. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_check_live.py tests/test_cs2_replays.py tests/test_cs2_link_bo3.py tests/test_cs2_grid_rounds.py tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
5. `PYTEST_N=8 make test` — допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol`) и флейк `smoke[dota_9007700576]` (перепроверить отдельно), см. `progress.txt`.
6. **Проба на 17.10, только scratch.** Цепочка s01–s05 на записанных событиях, затем сверка. Настоящие `data/cs2/*` только читаем.

   ```bash
   W=../betting_workspace/tasks/cs2-follow300/orchestrate/work/impl007
   mkdir -p $W
   PYTHONPATH=src uv run python src/cs2/s01_build_universe.py --as-of 2026-10-18T00:36:29Z \
     --window 2026-10-17:2026-10-17 --output-dir $W/universe
   PYTHONPATH=src uv run python $W/keep_recorded_events.py $W/universe
   PYTHONPATH=src uv run python src/cs2/s02_link_grid.py --fetch \
     --universe-dir $W/universe --raw-dir $W/grid_series --output-dir $W/links
   PYTHONPATH=src uv run python src/cs2/s03_fetch_grid_rounds.py --fetch \
     --links-dir $W/links --raw-dir $W/grid_raw --output-dir $W/grid
   PYTHONPATH=src uv run python src/cs2/s04_link_bo3.py --fetch --links-dir $W/links \
     --universe-dir $W/universe --grid-dir $W/grid --raw-dir $W/bo3_raw --output-dir $W/bo3
   PYTHONPATH=src uv run python src/cs2/s05_fetch_bo3_replays.py --fetch --workers 8 \
     --links-dir $W/links --grid-dir $W/grid --bo3-links-dir $W/bo3 --output-dir $W/bo3
   PYTHONPATH=src uv run python src/cs2/s05_check_live.py --bo3-links-dir $W/bo3 \
     --raw-dir $W/bo3_raw --replay-dir $W/bo3 --output $W/live_check.json
   ```

   `keep_recorded_events.py` — scratch-скрипт. Он оставляет в `events.parquet` и `markets.parquet` только `event_id` из `data/cs2/live/cs2-*-2026-10-17/target.json` (12 событий, факт 3) и пишет через `write_parquet`. Без него s04–s05 качали бы все 38 событий 17.10 (~90 карт, ~5 ГБ). С ним — ~25 карт, ~1,5 ГБ, ~1 мин при 30 МБ/с. s04 запрашивает списки дней 16–18.10 (поле ±17 ч) и игры их матчей; по моей оценке — 100–150 запросов к API bo3 (в списке 17.10 сейчас 43 матча).

   Ожидание:
   - s02: 11 событий с `gridSeriesId` связаны;
   - s05: карты FURIA 189516 и 189515 — `ok`;
   - сверка: до 18 сравниваемых карт (обычный набор ≤ 8, урезанный ≤ 10);
   - loadout в `normal` — около половины точных (прототип на FURIA: 47 из 88, p50 0 $, p90 900 $, max 3 400 $);
   - если привязка часов верна: `freeze_start`, `freeze_end`, `plant` — p50 около +0,2 с (задержка приёма);
   - `deaths` — p50 около 8–9 с (задержка таблицы 8,4–8,6 с);
   - `timer_phase_3` — большие разницы по определению (факт 7).

   Цифры не оценивать: таблицу целиком — в отчёт, решение — за пользователем. Если пришёл 429/403 — остановиться и сообщить, не повторять.
7. **Повтор:** тот же запуск `s05_check_live.py` ещё раз — `cmp` двух JSON без разницы.
8. **Ничего не записано в настоящие данные:** после пробы `test ! -e data/cs2/bo3/live_check.json`, `ls data/cs2/links` — пусто, `data/cs2/bo3/round_state` нет (как до шага).

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s05_check_live.py` (и `s05_live_frames.py`, если разделили), `tests/test_cs2_check_live.py`, `tests/fixtures/cs2/live_furia_cuts.jsonl`. Сообщение — в стиле `ab6ae4ef`, например «Compare bo3 replays with the GRID live recordings of 17.10.», тело 2–3 строки, последняя — `Co-Authored-By: <модель> <noreply@...>`. Без `git push`. `passes` в `feature.json` не менять.

## 8. Риски и открытые вопросы

- **Отличия от текста шага** (оркестратору решить):
  - смерти — только из таблицы, без схемы карты (решение 7);
  - сравниваем только карты `ok`, а не все карты с выжимкой (решение 2);
  - паузы сравниваем по границам (4 пункта), а не посекундно одной цифрой (решение 5);
  - таймер — три пункта, по фазе (решение 6);
  - появился допуск «совпадения» на пункт (решение 10).
- **Время по `received_at`.** Если часы машины записи уходили, все разницы сдвинуты. Задержка от `occurredAt` (0,16–0,22 с) говорит, что сдвига больше 0,2 с нет.
- **Правило «первый кадр 20 с»** на старте карты ошибается на десятки секунд (факт 8, eace). Это вопрос к live-адаптеру: решение по нему — на остановке после пилота.
- **Loadout bo3 ≠ кадр конца заморозки.** `equipment_value` bo3 взят в другой момент (закупка идёт и после конца заморозки): на FURIA точно 47 из 88. Решение «брать loadout или нет» — за пользователем.
- **Мало данных:** 18 карт, ~346 раундов, 8 карт обычного набора. p99 на 50–150 записях — почти максимум.
- **Проба качает ~1,5 ГБ с bo3.gg** через Cloudflare. При 429/403 s05 останавливается сам, пилот это учтёт.
- **Дни bo3 17.10 станут окончательными только 2026-10-19 00:00 UTC.** Для scratch-пробы это не важно, пилот перекачает.
- **Передать в STEP-011 (s10):** кадр конца заморозки в live — первый кадр таблицы с `Freezetime.Finished = true`; `LoadoutValue` дальше падает на смертях. Таймер фазы 3 в live — часы раунда или 0, не таймер реплея.
- Я не писал код и не делал коммитов: это фаза плана.
