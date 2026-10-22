# STEP-006 — s05: реплеи, часы раундов, выжимка 1 Гц, сверки 1–3

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `593f08a4`. Figma-ссылок в шаге нет.

## 1. Что делает шаг

s05 берёт торгуемые карты со связью bo3 (`status = linked` в `data/cs2/bo3/links.parquet` от s04). Для каждой карты он:

1. проверяет по GRID формат MR12 (сверка 3) — до загрузки;
2. качает `files.bo3.gg/round_maps/<bo3_game_id>/<round>.json` всех раундов, `--workers N` запросов одновременно;
3. разбирает каждый раунд в памяти: перенос фазы, время кадра UTC, часы раунда, счётчик остановок таймера (сверка 2);
4. пишет выжимку карты (секунды, игроки по секундам, убийства, раунды) и выбрасывает сырые байты;
5. при каждом запуске собирает из выжимок `round_clock.parquet` и таблицу статусов карт `replay_maps.parquet` (сверки 1–3, мета карты).

s06 (STEP-008) возьмёт в каталог только карты со статусом `ok`. s10 (STEP-011) и сверка 4 (STEP-007) читают выжимку.

## 2. Что я проверил (факты)

Скрипты — в `tasks/cs2-follow300/orchestrate/work/plan006/`. К `files.bo3.gg` ушло 4 запроса (`probe_codes.py`). Остальное — локальные файлы: 43 карты, 834 раунда реплеев циклов 1–2 в `esports-trader-cs2/data/cs2/bo3/replays/` (симлинк на основной чекаут).

1. **Коды ответа `files.bo3.gg`** (`probe_codes.py`): есть файл — 200, `content-length` 2 465 928 у `173713/1.json`. Нет раунда (`173713/99.json`) и нет карты (`999999999/1.json`) — **404**, страница nginx через Cloudflare, `cf-cache-status: DYNAMIC`. С `Accept-Encoding: gzip, br` ответ не сжат (2 026 182 байта). Значит, 403 — это не «нет файла», а настоящий сигнал бана. Правило шага «первый 403 → стоп» безопасно.
2. **Структура файла раунда** (`probe_round.py`, `173713`, 21 раунд):
   - ключи документа: `match`, `states`, `kills`, `shots`, `effects`, `team_counter_terrorists`, `team_terrorists`; `match.tick_rate` = 64;
   - в каждом кадре есть `ingame_tick`, `timer`, `bomb`, `players`, `grenades`, `infernos`;
   - `timer.time_remaining` (наносекунды) есть во всех 1 049 кадрах раунда 1; `timer.phase` — только в 109 кадрах. Ключ фазы приходит каждые 10 кадров и при смене фазы (`inspect_edges2.py`: кадры 0, 10, 20 … 160);
   - первый кадр каждого из 834 раундов несёт фазу 0 (`check_phases.py`) и полное состояние: у 10 игроков есть `id`, `team`, `is_alive`, `health`, `armor`, `money`; у бомбы — все четыре флага;
   - дальше кадры — патчи: `bomb: {}`, в `players` 20 мест, из них 10 `null`, у остальных только изменённые поля плюс `id`/`name`/`steam_id_64`;
   - `ingame_tick` идёт через файлы раундов без сброса: последний тик раунда 1 — 8 386, первый тик раунда 2 — 8 397. Шаг внутри файла 8 тиков (иногда 7), между файлами 8–12 тиков;
   - **ключи `kills` — номер кадра в файле, а не тик.** В раунде 21 (780 кадров) есть ключ `779`. Тик убийства = `states[int(key)].ingame_tick`;
   - `killer_team`/`victim_team`: 2 = T, 3 = CT. У одной смерти из 134 на `178747` `killer_team` = 0 (смерть без убийцы);
   - заголовок (`match.current_phase`, `team_*.score`) — состояние на конец файла, в расчёт не идёт.
3. **Формы фаз** (`check_phases.py`, 834 раунда, фаза с переносом вперёд):

   | Раунд | Форма | Раундов |
   | --- | --- | ---: |
   | середина | 0→1→2→3 | 406 |
   | середина | 0→1→3 | 372 |
   | середина | 0→1→3→2 | 16 |
   | середина | 0→1→2 (нет фазы 3) | 1 (`179581` р. 20) |
   | середина | 0→2→3 (нет фазы 1) | 1 (`186311` р. 1) |
   | последний | 0→1 | 19 |
   | последний | 0→1→2 | 19 |

   - `179581` р. 20: последний кадр — фаза 2, таймер 33,1 с, флаги бомбы ложны; следующий кадр — уже заморозка раунда 21. Победы в кадрах нет.
   - `186311` р. 1: фаза 0 с таймером −60,8 с, в следующем кадре сразу фаза 2, `is_planted = true`. Конца заморозки в кадрах нет.
4. **Сверка 2 на 834 раундах**: остановка таймера в фазах 1–2 — один раз, `187524` р. 8, кадр 1 299: фаза 1, таймер −27,28 с → 114,91 с (раунд перезапустили, `inspect_edges.py`). Сверка снимет эту карту как `pause_in_round`, как и задумано.
5. **Сверка 1 на 17 картах** с GRID из старых таблиц (`check_durations.py`: карта bo3 ↔ карта GRID по отпечатку ±4 ч; у 21 карты GRID в старых таблицах нет, у 5 нет раундов bo3). Разница «интервал реплея − длительность сегмента GRID», секунды:

   | Интервал реплея | Раунды | p1 | p10 | p50 | p90 | p99 | > 0,5 с |
   | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
   | середина, (первый тик след. раунда − первый тик) / 64 | 341 | −0,61 | −0,08 | −0,003 | +0,08 | +0,19 | 7 |
   | середина, (последний тик − первый тик) / 64 | 341 | −0,73 | −0,21 | −0,13 | −0,05 | +0,06 | 7 |
   | последний, (последний тик − первый тик) / 64 | 17 | −0,23 | −0,20 | −0,12 | −0,05 | +0,94 | 1 |

   Все 8 превышений — на одной карте `189861`: ±0,8…1,2 с через раунд (граница сегментов GRID сдвинута). Остальные 16 карт проходят с запасом. Итог на этих 43 картах: 4 карты снимаются (`189861` сверкой 1, `187524` сверкой 2, `179581` и `186311` из-за дыр в часах).
6. **Сверка 3 на старых таблицах GRID** (4 716 карт, правило «смена ровно после 12 и 27 + 6k»): `not_mr12` — 23 карты, как в PLAN.md.
7. **`started_at` сегмента GRID** (старые таблицы, 100 824 раунда): пуст у 1 раунда, и тот не `finished`. Отпечаток s04 требует, чтобы все раунды были `finished` (`build_grid_fingerprint`, `s04_link_bo3.py:265`). Значит, у карты `linked` пустого `started_at` не бывает. В пробе STEP-005 (`work/impl004/grid`, 57 карт `linked`, 1 241 раунд) пустых — 0.
8. **Скорость разбора** (`proto_parse.py`, 3 карты по 54–65 МБ): `json.loads` — 6 мс/МБ, обход кадров — 1 мс/МБ, всего ~7 мс CPU на МБ. При 58 МБ/с (замер 16 запросов) это 0,4 с CPU в секунду. Потолок одного ядра под GIL — около 140 МБ/с. `orjson` даёт 4 мс/МБ, но он стоит в окружении только транзитивно, поэтому беру stdlib `json`.
9. **Память**: файл раунда 2,7 МБ (медиана) → 10 МБ объектов Python; самый большой 10,2 МБ (5 819 кадров, `178976` р. 31) → 34 МБ, пик 45 МБ. При 64 потоках — ~1 ГБ обычно, до ~3 ГБ в худшем случае.
10. **Размер выжимки** (`proto_parse.py`): секунды — 75–89 КБ, игроки по секундам — 39–46 КБ, убийства — 4 КБ. Итого ~140 КБ на карту (3 227 секунд, 32 440 строк игроков, 129 убийств у `173713`). Это в 7 раз меньше оценки PLAN.md (~1 МБ).
11. **Прокси.** httpx 0.28.1. `Client` берёт прокси из окружения, только если `transport` не передан: `allow_env_proxies = trust_env and transport is None` (`.venv/…/httpx/_client.py:685`). `ALL_PROXY` → `all://` (`httpx/_utils.py:40`). Схема `socks5h` поддержана (`httpx/_transports/default.py:187`). `socksio` 1.0.0 стоит в окружении через зависимость `polymaker` (`uv tree --invert --package socksio`). Документация httpx 0.28.1 (context7 `/encode/httpx/0.28.1`): `ALL_PROXY` задаёт прокси для всех запросов, SOCKS требует `socksio`.
12. **Общий `get_json` не годится** (`src/shared/utils/http.py:22`): tenacity, 5 попыток, на 429 спит `retry-after` и пробует снова. Для реплеев это нарушает правило «после первого 429 — стоп».
13. **Код для повторного использования:**
    - `cs2.s04_link_bo3`: `BO3_HEADERS` (`:37`), `BO3_REQUEST_TIMEOUT_SECONDS` (`:50`), `Cs2Bo3LinkRow` (`:124`), `load_linked_series` (`:195`, даёт `best_of`), `read_game_rows` (`:730`), `read_rounds_by_game` (`:735`);
    - `cs2.s03_fetch_grid_rounds`: `Cs2Side` (`:48`), `Cs2GridRoundRow` (`:130`), `Cs2GridGameRow` (`:145`), `parse_utc` (`:267`), `format_utc` (`:271`), `apply_table_dtypes` (`:395`);
    - `shared.utils.parquet_io`: `write_parquet` (`:8`), `replace_nulls_with_none`;
    - `scripts/cs2/fetch_bo3_replays.py` — только образец (URL `:16`, коды команд 2/3 `:18`). `scripts/` не на пути импорта целей make (learning STEP-005), поэтому s05 его не импортирует.
14. **Вход для живой пробы есть:** `work/impl005/bo3/links.parquet` — 57 `linked` (S 17, A 17, B 7, C 16), карты 2026-09-10 03:12–19:28 UTC; `work/impl004/grid`, `work/impl003/links`.
15. **Equipment value для STEP-007 и s10 есть в кэше s04:** `data/cs2/raw/bo3/games/<match_id>.json`, раунд → `game_round_team_clans[]` с `clan_name`, `equipment_value`, `enemy_equipment_value`. `round_map_data` пуст у всех 5 709 раундов пробы, признаком наличия реплея он не служит.

## 3. Решения

1. **Какие карты.** Строки `links.parquet` s04 со `status = linked`. Раунды — строки `rounds.parquet` s03 этой карты (`game_id`), номера 1…N, N = число строк. Отпечаток s04 уже гарантирует N = счёт и полные раунды.
2. **Сверка 3 — до загрузки.** Она нужна только GRID. Смена стороны команды A между раундами r и r + 1 должна быть ровно при r = 12 или r ∈ {27, 33, 39, …} (r ≥ 27 и (r − 27) % 6 = 0). Иначе статус `not_mr12`, реплей не качается. Экономия — ~23 карты × ~59 МБ.
3. **Порядок и объём загрузки.** Карты без готовой выжимки и не `not_mr12`, по `started_at` карты GRID от новых к старым: сначала validation (популяция бэктеста), у новых карт выше покрытие реплеями. `--limit N` — не больше N карт за запуск (0 — все), как `--limit` в Dota s05b (`src/collect/s05b_fetch_stratz_matches.py:331`). Без `--limit` пилот не сможет замерить 8, 16, 32 и 64 запроса на разных картах: готовые карты пропускаются. Порядок пользователь может поменять после пилота — это одна строка сортировки.
4. **Параллельность.** `ThreadPoolExecutor(max_workers=workers)`. Задача — одна карта, её раунды идут по порядку. Значит, в полёте не больше `workers` запросов. Один общий `httpx.Client(headers=BO3_HEADERS, timeout=BO3_REQUEST_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers), follow_redirects=True)`. **Без `transport=`**: иначе httpx игнорирует `ALL_PROXY` (факт 11). Потоки, а не процессы: CPU ~0,4 с на секунду загрузки (факт 8). `--workers` по умолчанию 8 — замер 18.10 и лимит туннеля.
5. **Ответы.** `client.get(url)` без повторов:
   - 200 → `json.loads(response.content)` → разбор раунда;
   - 404 → строка лога, карта `replay_missing`, остальные раунды этой карты не запрашиваются;
   - 429 или 403 → строка лога, `stop.set()`, карта брошена;
   - любой другой код или `httpx.TransportError` → строка лога (код или класс ошибки), карта брошена, загрузка остальных идёт дальше.

   Брошенная карта не пишет выжимку. Следующий запуск с `--fetch` спросит её снова. Повторы внутри запуска не делаю: на 20 тысячах карт повторный запуск дешевле и не долбит сервер. Пилот покажет долю сбоев.
6. **Бан.** `stop: threading.Event` — параметр функции загрузки. Задача карты проверяет `stop` только на входе: новая карта после стопа не начинается, начатая докачивает свои раунды (кроме карты, где пришёл сам 429/403). Если основной цикл ловит исключение (ошибка разбора, Ctrl+C), он сначала ставит `stop`, потом пробрасывает: иначе выход из `with ThreadPoolExecutor` дождётся всех оставшихся карт. После пула s05 всё равно публикует таблицы, затем `raise typer.Exit(code=1)`, если `stop` стоит.
7. **Лог.** `data/cs2/bo3/replay_fetch_log.jsonl`, строка на каждый ответ не 200 и на каждую ошибку транспорта: `bo3_game_id`, `round_number`, `status_code` (`int | None`), `error` (`str | None`), `at` (UTC). frozen dataclass, запись под `threading.Lock`. Имя не `fetch_log.jsonl`: такой файл циклов 1–2 уже лежит в `data/cs2/bo3/`.
8. **Сырые байты не пишутся на диск.** Ответ разбирается в памяти и сразу отпускается. Удалять нечего, на диске остаётся только выжимка. Это строже текста шага («удаляются сразу после выжимки»), и меньше кода.
9. **Выжимка карты — папка `data/cs2/bo3/round_state/<bo3_game_id>/`**, четыре parquet-файла (отличие от `<bo3_game_id>.parquet` в шаге: у таблиц разный шаг строк — секунда, игрок × секунда, убийство, раунд):
   - `seconds.parquet` — `second` (int, эпоха UTC), `round_number`, `phase`, `timer_s`, `is_planted`, `is_defusing`, `is_defused`, `is_explode`, `paused`;
   - `players.parquet` — `second`, `player_id`, `side` (`CT`/`T`), `is_alive`, `health`, `armor`, `money`;
   - `kills.parquet` — `round_number`, `tick`, `at` (ISO мс), `killer_side` (`CT`/`T`/пусто), `victim_side`;
   - `rounds.parquet` — `round_number`, `first_tick`, `last_tick`, `started_at`, `freeze_end_at`, `planted_at`, `won_at`, `ended_at` (ISO мс, `format_utc`), `grid_duration_s`, `replay_interval_s`, `timer_stalls`.

   Запись атомарная: файлы пишутся во временную папку `round_state/.<bo3_game_id>.*` (`tempfile.mkdtemp`), потом папка переименовывается в `round_state/<bo3_game_id>/`. «Готовая выжимка» = такая папка есть. Повторно карта не качается. Выжимку получает каждая карта, у которой скачаны все раунды, даже если сверки 1–2 её снимут: иначе снятые карты качались бы при каждом запуске.

   Таблицы-строки — TypedDict, колонки из `__annotations__`, типы через `apply_table_dtypes`, как в s03/s04.
10. **Время кадра:** `at = parse_utc(started_at раунда r в GRID).timestamp() + (tick − первый тик файла) / match.tick_rate`. `tick_rate` читается из заголовка файла (сейчас всегда 64).
11. **Секунды выжимки.** Раунд r владеет секундами `[ceil(start_r), ceil(start_{r+1}))`, последний раунд — `[ceil(start_N), floor(end)]`, где `start_r` — `started_at` раунда r в GRID, `end` — `started_at + duration` последнего раунда. Значение секунды s — последний кадр файла раунда r со временем ≤ s (`bisect_right(ats, s) − 1`; первый кадр раунда имеет `at = start_r`, поэтому кадр всегда есть). Кадры раунда r позже `ceil(start_{r+1})` (реплей длиннее сегмента, до 0,5 с) в секунды не идут. Так секунды делятся по границам GRID без пересечений, а s10 получает «последний кадр ≤ t − d» простым сдвигом строки на целое d.
12. **Перенос фазы.** Фаза первого кадра файла обязательна (`states[0]["timer"]["phase"]`), дальше `timer.get("phase", прежняя)`. Таймер = `time_remaining / 1e9`. Бомба и игроки — состояние первого кадра плюс патчи по `id` игрока; `null` в `players` пропускается. В секунды идут игроки с `team` 2 или 3. Состояние сбрасывается на каждом файле раунда.
13. **Часы раунда** (`rounds.parquet`):
    - `started_at` — `started_at` сегмента GRID; `ended_at` — `started_at + duration_s` сегмента GRID;
    - `freeze_end_at` — первый кадр, где фаза стала 1 из 0;
    - `won_at` — первый кадр, где фаза стала 3 из 1 или 2. У последнего раунда карты `won_at` = `ended_at`;
    - `planted_at` — первый кадр с фазой 2 раньше кадра победы (у последнего раунда — первый кадр с фазой 2). Форма 0→1→3→2 даёт пусто;
    - чего нет — пусто.
14. **Флаг паузы на секунду:** `phase == 0 and timer_s < 0`, или `phase == 3` и раунд 12 или 24 + 3k (r ≥ 24 и (r − 24) % 3 = 0). Чистая функция `is_pause_second(phase, timer_s, round_number)`.
15. **Сверка 1.** `replay_interval_s` = (первый тик раунда r + 1 − первый тик раунда r) / tick_rate; у последнего раунда — (последний тик − первый тик) / tick_rate. Этот вариант точнее: медиана −0,003 с против −0,13 с (факт 5). Карта — `replay_grid_mismatch`, если хоть у одного раунда |`replay_interval_s` − `grid_duration_s`| > 0,5 с.
16. **Сверка 2.** `timer_stalls` раунда — число пар соседних кадров, где обе фазы (с переносом) в {1, 2}, пара — не переход 1 → 2, и таймер не убыл (`timer_i >= timer_{i−1}`). Считается по сырым кадрам 8 Гц при разборе: из секунд 1 Гц её не посчитать. Карта — `pause_in_round`, если сумма > 0.
17. **Новый статус `round_clock_incomplete`** (в тексте шага его нет): у раунда середины нет `freeze_end_at` или `won_at`, у последнего раунда нет `freeze_end_at`. Без этих моментов s10 не знает конец заморозки (а от него loadout), а `won_at` нужен часам. В 43 картах это 2 карты (факт 3). Альтернатива — слить с `replay_grid_mismatch`; я оставил отдельную причину, чтобы аудит пилота показывал её числом.
18. **Статус карты** (`replay_maps.parquet`, пересчёт при каждом запуске, по порядку):
    1. `not_mr12` — сверка 3;
    2. есть выжимка → `replay_grid_mismatch` (сверка 1) → `pause_in_round` (сверка 2) → `round_clock_incomplete` → `ok`;
    3. выжимки нет → `replay_missing`, если в логе есть 404 этой карты, иначе `replay_not_fetched` (не качали, 429/403, 5xx, обрыв).
19. **`replay_maps.parquet` — строка на карту `linked` s04:** `bo3_game_id`, `game_id`, `series_id`, `event_id`, `bo3_tier`, `game_number`, `best_of`, `map_name`, `team_a_series_score`, `team_b_series_score`, `status`, `round_count`, `max_duration_diff_s`, `timer_stalls`. **Мета карты лежит здесь, а не в выжимке** (отличие от шага): она из GRID и s02, не из реплея, и пересчитывается без перекачки. Номер — `game_number` s04, название — `map_name` s04 (нормализованное), best-of — `load_linked_series` по `series_id`, счёт серии — победы команд A и B на картах той же серии с меньшим `game_number` в `games.parquet` s03. s06 берёт статус отсюда, s10 — мету.
20. **`round_clock.parquet`** — склейка `rounds.parquet` всех выжимок плюс `bo3_game_id`, `game_id`, `team_a_side`, `team_a_won` раунда из GRID. Две последние колонки ничего не стоят и дают s10 сторону и счёт без второго чтения GRID.
21. **Без `--fetch`** s05 не ходит в сеть: только пересобирает `round_clock.parquet` и `replay_maps.parquet` из выжимок и лога. Как `--fetch` в s02–s04. Цель Makefile передаёт `--fetch`.
22. **stdout:** число карт `linked`, счёт по статусам, `ok` по тирам, строки с кодами из лога. При `--fetch`: каждые 25 карт и в конце — карт сделано/всего, сохранено, 404, сбоев, МБ, секунд, МБ/с. МБ/с нужен пилоту для выбора параллельности.

## 4. Изменения

### 4.1 `src/cs2/constants.py`

```python
CS2_ROUND_STATE_DIR = CS2_BO3_DIR / "round_state"
CS2_ROUND_CLOCK_PATH = CS2_BO3_DIR / "round_clock.parquet"
CS2_REPLAY_MAPS_PATH = CS2_BO3_DIR / "replay_maps.parquet"
CS2_REPLAY_LOG_PATH = CS2_BO3_DIR / "replay_fetch_log.jsonl"
```

### 4.2 `src/cs2/s05_fetch_bo3_replays.py` (новый, ~600 строк)

Константы: `REPLAY_ROOT = "https://files.bo3.gg/round_maps"`, `TEAM_SIDES = {2: "T", 3: "CT"}`, `MAX_ROUND_DIFF_SECONDS = 0.5`, `PROGRESS_EVERY_MAPS = 25`, `Cs2ReplayStatus` (Literal из 7 значений решения 18) и кортеж `STATUS_NAMES` для печати.

Типы внешнего JSON (TypedDict, `cast` после `json.loads`, как `Bo3Game` в s04): `ReplayDocument` (`match`, `states`, `kills`), `ReplayHeader` (`tick_rate`), `ReplayState` (`ingame_tick`, `timer`, `bomb`, `players: list[ReplayPlayer | None]`), `ReplayTimer` (`time_remaining`, `phase: NotRequired[int]`), `ReplayBomb` (total=False: четыре флага), `ReplayPlayer` (`id`, остальное `NotRequired`), `ReplayKill` (`killer_team`, `victim_team`).

Строки таблиц (TypedDict): `Cs2ReplaySecondRow`, `Cs2ReplayPlayerRow`, `Cs2ReplayKillRow`, `Cs2ReplayRoundRow`, `Cs2RoundClockRow`, `Cs2ReplayMapRow`.

frozen dataclass: `GridRoundClock` (`round_number`, `start` эпоха, `duration_s`, `started_at`), `ReplayJob` (`bo3_game_id`, `rounds: tuple[GridRoundClock, ...]`, `started_at` карты для сортировки), `ParsedRound` (строки секунд, игроков, убийств, строка раунда без `replay_interval_s`, `first_tick`), `MapSummary` (четыре списка строк), `ReplayLogLine`, `FetchTally` (сохранено, 404, сбоев, пропущено после стопа, байт, секунд), `ReplayTables` (кадры `round_clock` и `replay_maps`). Для исхода карты — `Literal["saved", "missing", "failed", "stopped", "skipped"]`.

Функции (глаголы, все аргументы обязательные):

- `load_map_inputs(links_dir, grid_dir, bo3_links_dir) -> list[MapInput]` — строки `linked` s04 + раунды и игры s03 + `best_of` s02. `MapInput` — frozen dataclass: строка связи, раунды GRID, мета.
- `find_mr12_violation(rounds) -> bool` — сверка 3 по `team_a_side`.
- `is_pause_second(phase, timer_s, round_number) -> bool`, `is_halftime_round(round_number) -> bool`, `is_side_switch_round(round_number) -> bool`.
- `build_job(map_input) -> ReplayJob`; `list_pending_jobs(inputs, state_dir, limit) -> list[ReplayJob]` — без готовой папки, без `not_mr12`, от новых к старым, срез `limit`.
- `parse_round(document, grid_round, next_start, is_last) -> ParsedRound` — перенос фазы, время кадра, выбор кадра на секунду, строки игроков, убийства, часы, `timer_stalls`. Внутри — маленькие функции: `compute_frame_times`, `pick_second_frames`, `apply_player_patches`, `find_round_clock`, `count_timer_stalls`, `build_kill_rows`.
- `build_map_summary(job, parsed_rounds) -> MapSummary` — `replay_interval_s` по решению 15.
- `write_map_summary(state_dir, bo3_game_id, summary) -> None` — временная папка + rename.
- `fetch_map(client, job, state_dir, log, stop) -> MapOutcome` — раунды по порядку, коды по решению 5.
- `run_map_job(client, job, state_dir, log, stop) -> MapOutcome` — `skipped`, если `stop` уже стоит, иначе `fetch_map`.
- `fetch_jobs(client, jobs, workers, state_dir, log_path, stop) -> FetchTally` — пул, прогресс, `stop` на исключении.
- `build_replay_client(workers) -> httpx.Client` — один на запуск; тесты передают свой клиент с `httpx.MockTransport` прямо в `fetch_jobs`.
- `read_missing_game_ids(log_path) -> set[int]` — карты с 404 в логе.
- `decide_map_status(map_input, rounds_frame | None, missing_ids) -> Cs2ReplayStatus`.
- `build_replay_tables(inputs, state_dir, log_path) -> ReplayTables` — `round_clock` и `replay_maps`.
- `print_replay_summary(...)`, `main(...)`.

CLI (typer, как s04): `--fetch`, `--workers` (`min=1`, по умолчанию 8), `--limit` (`min=0`, по умолчанию 0), `--links-dir` (s02), `--grid-dir` (s03), `--bo3-links-dir` (вход s04, по умолчанию `CS2_BO3_DIR`), `--output-dir` (выход: `round_state/`, две таблицы, лог; по умолчанию `CS2_BO3_DIR`). Отдельные вход и выход нужны тестам и пробе: настоящий `data/cs2/bo3` в этом шаге не трогаем.

### 4.3 `Makefile`

```make
cs2-replays:  ## CS2 s05 bo3 2D replays -> round clock + 1 Hz state (--fetch; ARGS="--workers 16 --limit 40")
	$(PY) src/cs2/s05_fetch_bo3_replays.py --fetch $(ARGS)
```

И `cs2-replays` в `.PHONY`. Туннель — без правок кода: `ALL_PROXY=socks5h://127.0.0.1:18080 make cs2-replays ARGS="--workers 8"`.

### 4.4 Фикстура `tests/fixtures/cs2/bo3_round_173713_11.json`

Источник — настоящий файл `data/cs2/bo3/replays/173713/11.json` (1 497 кадров, тики 87 583 … 99 548). В нём есть всё нужное: заморозка с паузой (таймер до −42,27 с), конец заморозки на кадре 498 (тик 91 566, +62,234375 с), установка на кадре 1 258 (тик 97 644, +157,203125 с), победа на кадре 1 457 (тик 99 236, +182,078125 с), 7 убийств.

Правила вырезки (скрипт — в `orchestrate/work/impl006/`, в репозиторий не идёт):

- оставить кадр 0, каждый 16-й кадр, каждый кадр смены фазы и кадр перед ним (497, 498, 1 257, 1 258, 1 456, 1 457), кадры убийств (1 033, 1 144, 1 166, 1 175, 1 375, 1 381, 1 384), последний кадр 1 496;
- у кадра оставить только `ingame_tick`, `timer` (ключ `phase` — только если он был в исходном кадре или фаза сменилась с прошлого оставленного кадра), `bomb`, `players`;
- кадр 0 — полное состояние 10 игроков (`id`, `team`, `is_alive`, `health`, `armor`, `money`) и четыре флага бомбы. Дальше — патчи относительно прошлого оставленного кадра: только изменённые поля плюс `id`; без изменений — `null`. Так фикстура проверяет и перенос, и `null`;
- `kills` — ключи перенумеровать в новые номера кадров; `match` — только `tick_rate`.

Цель — ≤ 80 КБ. Тики и таймеры — настоящие, поэтому моменты выше в фикстуре точные.

### 4.5 `tests/test_cs2_replays.py` (новый)

Помощники: загрузка фикстуры; копия раунда со сдвигом тиков (карта из нескольких раундов); строки GRID с `started_at` `2026-10-05T20:00:00.000Z` и длительностью = интервал реплея ± d; таблицы s02/s03/s04 в `tmp_path`; `httpx.MockTransport`.

1. `test_phase_carries_forward` — кадр без `phase` берёт прошлую фазу; первый кадр без фазы → ошибка.
2. `test_round_clock_from_fixture` — `freeze_end_at` = старт + 62,234 с, `planted_at` = + 157,203 с, `won_at` = + 182,078 с.
3. `test_frame_time_anchors_to_grid_start` — время кадра = `started_at` + (тик − 87 583) / 64; убийства в `kills.parquet` с этим временем и сторонами `CT`/`T`.
4. `test_seconds_take_last_frame` — секунда старт + 62 — фаза 0, таймер < 0, `paused`; секунда старт + 63 — фаза 1, таймер 114,969 с; число секунд = ceil(start_{r+1}) − ceil(start_r); последний раунд — до floor(end) включительно.
5. `test_pause_flag` — пауза в заморозке (фикстура, таймер < 0); все секунды фазы 3 у раунда 12 — пауза, у раунда 11 — нет; `is_pause_second` на 24, 27, 30 — да, на 25, 26 — нет; фаза 1 с таймером < 0 — нет.
6. `test_last_round_wins_at_grid_end` — фикстура, обрезанная на кадре 1 456 и поставленная последним раундом: `won_at` = конец сегмента GRID, `planted_at` есть.
7. `test_plant_after_win_is_not_plant` — фикстура с фазой 2 после фазы 3 (0→1→3→2): `planted_at` пуст.
8. `test_round_mismatch_over_half_second` (сверка 1) — длительность GRID = интервал + 0,4 с → `ok`; + 0,6 с → `replay_grid_mismatch`; то же для последнего раунда.
9. `test_timer_stall_is_pause_in_round` (сверка 2) — кадр фазы 1 с таймером прошлого кадра → `pause_in_round`; настоящий переход 1 → 2 (таймер 40 с) — не остановка.
10. `test_not_mr12` (сверка 3) — смена после 12, 27 и 33 → нет; смена после 24 → `not_mr12`; нет смены после 12 → `not_mr12`. Карта `not_mr12` не запрашивается.
11. `test_missing_round_clock` — раунд середины без фазы 3 → `round_clock_incomplete`.
12. `test_404_marks_replay_missing` — 404 на раунде 1 карты A: у A нет папки, лог с кодом 404, статус `replay_missing`; карта B сохранена; `stop` не стоит.
13. `test_429_stops_new_maps` (и параметр 403) — `workers=1`, карты A, B: 429 на раунде 2 карты A → у A нет папки, B не запрошена, `stop` стоит, `main` выходит с `typer.Exit(code=1)` после записи таблиц.
14. `test_started_map_finishes_after_stop` — обработчик ставит `stop` во время раунда 1 карты A (как будто 429 пришёл в другом потоке): A докачивает раунд 2 и сохраняется, B не запрошена.
15. `test_ready_summary_is_not_fetched` — карта с папкой выжимки не запрашивается (обработчик падает при запросе); `main` без `--fetch` даёт ту же `replay_maps.parquet`.
16. `test_series_score_meta` — карта 3 BO3 после 1:1 → счёт серии 1:1, `best_of` 3.

## 5. Крайние случаи

- **Реплея нет** (тир C, ~29% карт) — 404 на раунде 1, один запрос на карту. Каждый `--fetch` спрашивает такие карты снова: недавние реплеи bo3 выкладывает с задержкой. Стоимость — один запрос на карту.
- **404 на раунде середины** — карта `replay_missing`, скачанные раунды этой карты пропадают (в памяти). Редко.
- **Ошибка разбора** (форма JSON не та) — исключение, `stop`, начатые карты докачиваются, таблицы не публикуются, код не 0. Отдельного статуса нет: форму проверили на 834 раундах; при новой форме нужен разбор человеком.
- **Обрыв процесса** — готовые папки целы (rename атомарный), временные `.<id>.*` остаются мусором и в расчёт не идут. Повторный запуск продолжает.
- **Раунд перезапустили** (`187524` р. 8) — `pause_in_round`.
- **Граница сегментов GRID сдвинута** (`189861`) — `replay_grid_mismatch`.
- **Длинная заморозка** (до 606 с) и перерыв в заморозке раунда 13 — секунды с таймером < 0 получают `paused`.
- **Овертайм** — пауза в фазе 3 раундов 24, 27, 30…; смена сторон после 27, 33, 39…
- **Смерть без убийцы** (`killer_team` 0) — `killer_side` пуст.
- **Игрок-замена** (новый `id` в середине карты) — строки игроков ключуются по `id`, ширина таблицы не меняется.
- **Реплей длиннее сегмента GRID** (до 0,5 с) — лишние кадры не попадают в секунды следующего раунда (решение 11).
- **429 у одной карты, пока другие качаются** — начатые карты докачиваются, новые не берутся, код выхода 1.

## 6. Проверка

Команды из корня `esports-trader-cs2`.

1. `uv run ruff format src/cs2 tests/test_cs2_replays.py` и `uv run ruff check src/cs2 tests/test_cs2_replays.py` — без ошибок.
2. `uv run python -m basedpyright` — 0 errors.
3. `uv run python -m pre_commit run --files src/cs2/constants.py src/cs2/s05_fetch_bo3_replays.py tests/test_cs2_replays.py tests/fixtures/cs2/bo3_round_173713_11.json Makefile` — passed.
4. `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_cs2_replays.py tests/test_cs2_link_bo3.py tests/test_cs2_grid_rounds.py tests/test_cs2_link_grid.py tests/test_cs2_universe.py -q` — все проходят.
5. `PYTEST_N=8 make test` — допустимы только 2 известных падения `test_follow300_replay` lol_* (нет `data/lol`) и флейк `smoke[dota_9007700576]` (перепроверить отдельно), см. `progress.txt`.
6. **Офлайн-прогон разбора на 43 локальных картах** (без сети, скрипт в `orchestrate/work/impl006/`): вызвать `parse_round` и `build_map_summary` модуля на файлах `data/cs2/bo3/replays/<id>/<n>.json`. Для 17 карт с GRID взять старые таблицы (сопоставление — как в `orchestrate/work/plan006/check_durations.py`), для остальных — длительности из самих тиков. Ожидание:
   - 834 раунда разобраны без исключений;
   - `round_clock_incomplete` — ровно `179581` (р. 20) и `186311` (р. 1);
   - `timer_stalls` > 0 — ровно `187524` (р. 8);
   - из 17 карт с GRID: 16 `ok`, `189861` — `replay_grid_mismatch`, максимум |разницы| ≈ 1,25 с;
   - размер папки выжимки — 100–200 КБ на карту;
   - секунд у карты ≈ её длительность (прототип `proto_parse.py` дал 3 227 у `173713`), строк игроков = 10 × секунд.

   Любое другое число — причину назвать в отчёте.
7. **Короткая живая проба** — вход из STEP-003…STEP-005, выход только в scratch:

   ```bash
   W=../betting_workspace/tasks/cs2-follow300/orchestrate/work
   PYTHONPATH=src uv run python src/cs2/s05_fetch_bo3_replays.py --fetch --limit 6 --workers 8 \
     --links-dir $W/impl003/links --grid-dir $W/impl004/grid \
     --bo3-links-dir $W/impl005/bo3 --output-dir $W/impl006/bo3
   ```

   Ожидание: 57 карт `linked`; 6 карт — самые поздние по старту (2026-09-10 вечер); ~150 запросов, ~350 МБ, 30–40 МБ/с; код выхода 0; на диске только 6 папок по 100–200 КБ (`du -sh $W/impl006/bo3/round_state`); остальные 51 — `replay_not_fetched` (или `not_mr12`). Если пришёл 429/403 — остановиться и сообщить, не повторять.
8. **Повтор без сети:** тот же запуск без `--fetch` и без `--limit` — ноль запросов, `replay_maps.parquet` и `round_clock.parquet` равны первому запуску (`pd.testing.assert_frame_equal`).
9. **Прокси без VPS:** `ALL_PROXY=socks5h://127.0.0.1:9 PYTHONPATH=src uv run python src/cs2/s05_fetch_bo3_replays.py --fetch --limit 1 …` в отдельный scratch-выход. Ожидание: в логе строка с ошибкой транспорта (`ProxyError` или `ConnectError`), карта `replay_not_fetched`, ни одного запроса мимо прокси. Это доказывает, что `ALL_PROXY` работает без правок кода. Туннель через `sun` в этом шаге не поднимать: VPS трогать нельзя.
10. Полную загрузку не запускать: её делает пилот (STEP-014) с замером 8/16/32/64.

## 7. Коммит

Один коммит на `cs2-signal`: `src/cs2/constants.py`, `src/cs2/s05_fetch_bo3_replays.py`, `tests/test_cs2_replays.py`, `tests/fixtures/cs2/bo3_round_173713_11.json`, `Makefile`. Сообщение — в стиле `add1b94d`, например «Fetch bo3 replays into CS2 round clocks and 1 Hz state.», тело 2–3 строки, последняя — `Co-Authored-By: <модель> <noreply@...>`. Без `git push`. `passes` в `feature.json` не менять.

## 8. Риски и открытые вопросы

- **Отличия от текста шага** (все — в решениях, оркестратору решить):
  - выжимка — папка `round_state/<bo3_game_id>/` из четырёх файлов, а не один `<bo3_game_id>.parquet` (решение 9);
  - сырые байты не касаются диска, удалять нечего (решение 8);
  - мета карты — в `replay_maps.parquet`, а не в выжимке (решение 19);
  - новая выходная таблица `replay_maps.parquet` и статусы `replay_missing`, `replay_not_fetched`, `round_clock_incomplete` (решения 17–19);
  - флаги `--fetch`, `--limit` и раздельные `--bo3-links-dir` / `--output-dir` (решения 3, 21).
- **Потери на сверках выше ожидания.** На 43 картах S/A/B снимаются 4 (9%): сверка 1 — 1 из 17 карт с GRID, сверка 2 — 1 из 43, дыры в часах — 2 из 43. Плюс `not_mr12` ~0,5%. Сверки задал пользователь, я их не ослабляю. Настоящую долю даст пилот; если она большая, решает пользователь.
- **GIL.** Разбор ~7 мс CPU на МБ, потолок одного ядра ~140 МБ/с. Если пилот увидит, что МБ/с не растёт от 32 к 64 потокам, причина может быть в GIL, а не в bo3.gg. Тогда — пул процессов, это отдельная правка.
- **Память при 64 потоках** — ~1 ГБ обычно, до ~3 ГБ в худшем случае (факт 9).
- **`socksio` стоит транзитивно** (через `polymaker`). Если зависимость уйдёт, SOCKS-прокси упадёт с ImportError при создании клиента. Проверка 9 это поймает.
- **Без повторов внутри запуска** — 5xx и обрывы оставляют карты `replay_not_fetched`. Полный прогон после себя нужно запустить ещё раз с `--fetch`, он докачает только дыры.
- **Повторные 404.** Каждый `--fetch` снова спрашивает карты без реплея: ~16% карт × 1 запрос. Окончательность 404 не храню: недавние реплеи появляются позже.
- **Публикация читает все выжимки** при каждом запуске: ~20 000 маленьких parquet, по грубой оценке 1–2 мин.
- **Передать в STEP-007 и STEP-011:**
  - секунда s в `seconds.parquet` — последний кадр ≤ s; признак с задержкой d — строка s − d;
  - `round_clock.parquet`: `freeze_end_at`, `won_at`, `planted_at`, `team_a_side`, `team_a_won`; секунды с начала заморозки = s − `started_at` раунда;
  - equipment value раунда — в кэше s04 (`raw/bo3/games/<match_id>.json` → `game_round_team_clans[].equipment_value`), s05 его не выносит;
  - s06 берёт карты со `status = ok` из `replay_maps.parquet`.
- Я не писал код и не делал коммитов: это фаза плана.
