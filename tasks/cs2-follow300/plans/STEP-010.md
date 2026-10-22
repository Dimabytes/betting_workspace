# STEP-010 — s09: секунды рынка

Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, ветка `cs2-signal`, HEAD `9db066a9`.
Образец: `src/market_data/build_market_data.py` (Dota). Figma нет.

## 1. Что делает шаг

s09 строит секунды рынка для каждой карты каталога s06. Одна строка на секунду от `spawn_at` − 60 с до `ended_at`. В строке:

- статус секунды и pair mid команды A по `resolve_market_pair` (порог спреда 6 тиков);
- prior — последний двусторонний mid в окне перед стартом карты;
- изменение mid за 10 с;
- цель — pair mid через 300 с реального времени UTC.

Файл на карту: `data/cs2/market_seconds/v<hash>/match_id=<id>.parquet`. Цель Makefile — `cs2-market-data`. Тесты — `tests/test_cs2_market_seconds.py`.

## 2. Что я проверил (факты)

1. **Dota s09** (`src/market_data/build_market_data.py`):
   - версия кэша — sha256 от кортежа констант, 8 символов (`:40–50`); путь `MARKET_SECONDS_DIR / v<hash> / match_id=<id>.parquet` (`:55–56`);
   - книги грузятся одним окном: от prior − 61 с до последней секунды + 300 с (`:105–114`);
   - нет книги у одного из токенов — файл не пишется (`:116–118`, `:148–152`);
   - строка: `resolve_market_pair(find_asof_quote(...))` на секунду, цель `lookup_market_p_after(..., 300)`, prior `lookup_strict_prior` (`:119–144`);
   - готовый файл не пересобирается (`:176`), сборка — `ProcessPoolExecutor` на `os.cpu_count()` процессов (`:155–170`).
2. **Функции книг** (`src/shared/utils/telonex_book.py`): `load_token_book` (`:505`), `find_asof_quote` (`:531`, котировка старше `MAX_BOOK_AGE_SECONDS` = 5 с — `stale`), `resolve_market_pair` (`:564`), `lookup_market_p_after` (`:589`), `lookup_strict_prior` (`:109`, окно `[anchor − window, anchor)`). Все берём как есть.
3. **Константы** (`src/shared/constants/dataset.py:11–15`): `MODEL_START_SECOND` = −60, `DOTA_PRIOR_ANCHOR_SECOND` = −85, `DOTA_PRIOR_WINDOW_SECONDS` = 61, `MODEL_TARGET_HORIZON_SECONDS` = 300.
4. **Каталог CS2** (`src/cs2/s06_publish_catalog.py:67–79`, `read_catalog_rows` `:189`): `match_id`, `condition_id`, `event_id`, `token_id_0`, `token_id_1`, `team_a_token_index`, `spawn_at`, `ended_at` (ISO, миллисекунды, например `2026-09-10T17:58:26.737Z`).
5. **Окно книг s08** (`src/cs2/s08_fetch_telonex_books.py:262–269`): дни UTC от `spawn_at` − 146 с до `ended_at` + 300 с.
6. **Аудит s08** — `data/cs2/raw/telonex/download_audit.parquet`, строка на канал × токен × день, колонки `match_id, condition_id, channel, asset_id, day, reason, bytes` (`:107`). `PENDING_REASONS` = `not_published`, `retries_exhausted` (`:88`): этот день ещё придёт.
7. **Выжимка s05** хранит секунды как целые секунды Unix: `start = parse_utc(started_at).timestamp()` (`s05_fetch_bo3_replays.py:265`), секунды раунда от `ceil(start)` (`s05_parse_replay.py:248–257`). Строка выжимки на секунду s — последний кадр с временем ≤ s.
8. **Общее ядро** считает цель как `signal_market_p_radiant_300s − market_p_radiant` (`src/shared/utils/gbm.py:133–137`). Имена Dota сохраняем.
9. `write_parquet` пишет через `.tmp` и `replace` (`src/shared/utils/parquet_io.py:8–12`). Оборванный файл не появится.
10. **Проба на книгах 10.09** (scratch, `work/plan010/probe/`, логика из раздела 3 на функциях выше):

    | match_id | строк | ok | wide_spread | stale | missing | prior | change пусто | цель пуста | цель пуста в последних 300 с | время |
    | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
    | 183178 | 1 895 | 1 488 | 270 | 78 | 59 | 0.465 | 595 | 543 | 300 | 0.49 с |
    | 184058 | 3 128 | 1 263 | 1 119 | 739 | 7 | 0.6 | 2 262 | 1 865 | 300 | 0.15 с |
    | 184073 | 2 273 | 31 | 1 312 | 869 | 61 | None | 2 267 | 2 236 | 294 | 0.08 с |
    | 185135 | 3 347 | 3 192 | 138 | 17 | 0 | 0.325 | 294 | 436 | 295 | 1.83 с |
    | 183177 | 2 470 | 2 371 | 78 | 14 | 7 | 0.39 | 170 | 375 | 300 | 1.33 с |
    | 184057 | 1 756 | 1 159 | 267 | 258 | 72 | 0.695 | 788 | 660 | 300 | 0.51 с |

    После конца карты книга одностороння или молчит. От `ended_at` до +600 с на всех 6 картах статус `missing_quote` или `stale_quote`. Исключение — по 6 секунд `ok` у 184073 и 185135 в первые 60 с (`work/plan010/probe/sep10-tail.txt`). Снимки идут ещё 39–57 с после `market_closed_at`. Поэтому цель пуста почти во всех последних 300 с каждой карты.
11. **Аудит scratch:** `impl009/sep10` — 12 клеток книг `skipped_valid`, 6 карт. `impl009/oct17` — 24 клетки книг `not_published`, 12 карт.

## 3. Решения

1. **Сетка секунд — целые секунды Unix.** `zero_s` = `ceil(spawn_at)`. Строка `second` = s − `zero_s`, `state_ts_us` = s × 1 000 000. Секунды от −60 до `floor(ended_at)` − `zero_s` включительно.
   - Это ровно секунды t с `spawn_at` − 60 ≤ t ≤ `ended_at`.
   - Выжимка s05 стоит на тех же целых секундах (факт 7). s10 берёт состояние с задержкой d из строки выжимки t − d, без лишнего сдвига. Сетка от `spawn_at` с миллисекундами дала бы лишние 0–1 с задержки.
   - Последняя секунда `floor(ended_at)` совпадает с последней секундой выжимки.
2. **Mid команды A.** `books[team_a_token_index]` — книга команды A, второй токен — команда B. Их передаём в `resolve_market_pair` на места `radiant` и `dire`, как Dota `radiant_token_index` (`build_market_data.py:115–116`).
3. **Prior.** Якорь — `state_ts_us` секунды `DOTA_PRIOR_ANCHOR_SECOND` (−85), окно `DOTA_PRIOR_WINDOW_SECONDS` (61), `lookup_strict_prior`. prior = None остаётся в файле. Что делать с такой картой, решает s10 (Dota и LoL такие карты выкидывают).
4. **Изменение mid за 10 с.** `MID_CHANGE_SECONDS = 10`. Значение = mid(t) − mid(t − 10). Если статус t или t − 10 не `ok`, значение None.
   - Статус считаем для секунд от −70, а не от −60. Строки −60…−51 тоже получают значение. Секунды −70…−61 в файл не пишем.
   - Live считает это по своей книге в любую секунду, поэтому пустое начало было бы ложным.
5. **Цель.** `lookup_market_p_after(team_a_book, team_b_book, state_ts_us, MODEL_TARGET_HORIZON_SECONDS)`. Выплатой не заменяем. Отдельной отсечки по `market_closed_at` нет: после конца карты книга сама даёт `missing_quote` или `stale_quote` (факт 10).
6. **Окно загрузки книг** — от якоря prior − 61 с до последней секунды + 300 с, как Dota. Оно лежит внутри окна дней s08: `ceil(spawn_at)` − 146 ≥ `spawn_at` − 146, `floor(ended_at)` + 300 ≤ `ended_at` + 300.
7. **Колонки** (TypedDict `Cs2MarketSecondRow` в s09):

   | Колонка | Тип | Что |
   | --- | --- | --- |
   | `match_id` | int64 | `bo3_game_id` |
   | `condition_id`, `event_id` | string | из каталога |
   | `second` | int64 | секунда от `zero_s` |
   | `state_ts_us` | int64 | целая секунда Unix × 10⁶ |
   | `market_status` | string | `MarketQuoteStatus` |
   | `market_p_radiant` | float64 | mid команды A, NaN если не `ok` |
   | `market_p_radiant_change_10s` | float64 | изменение mid за 10 с |
   | `signal_market_p_radiant_300s` | float64 | цель |
   | `market_radiant_prior` | float64 | prior, одно значение на карту |

   `signal_market_p_radiant_30s` Dota не пишем. Его читают только метрики Dota (`src/train_model/market_metrics.py:65`), в шаге его нет. Типы — через `apply_table_dtypes` из s03. Пустой float — NaN, так схема одинакова у всех файлов.
8. **Ворота по аудиту s08.** Перед сборкой s09 читает строки `channel == "book_snapshot_full"` из аудита.
   - Нет строк с этим `match_id` и `condition_id` → `RuntimeError` с текстом «rerun cs2-books». Каталог новее аудита.
   - Есть клетка с `reason` из `PENDING_REASONS` → карта `books_pending`, файла нет. Иначе s09 закэшировал бы частичные книги навсегда.
   - Остальные причины окончательные. Карту строим.
9. **Кэш.**
   - `CACHE_VERSION` = sha256 от `repr` кортежа: `MAX_BOOK_AGE_SECONDS`, `PAIR_SUM_TOLERANCE`, `MAX_ENTRY_SPREAD_TICKS`, `MID_CHANGE_SECONDS`, `MODEL_TARGET_HORIZON_SECONDS`, `MODEL_START_SECOND`, `DOTA_PRIOR_ANCHOR_SECOND`, `DOTA_PRIOR_WINDOW_SECONDS`, `tuple(MARKET_SECOND_COLUMNS)`, первые 8 символов.
   - Это кортеж Dota, плюс окно изменения mid и список колонок: смена схемы тоже меняет версию.
   - Входы карты в хэш не идут. Строка каталога — сыгранная серия, она не меняется. Неполные книги закрывает решение 8.
   - Готовый файл не пересобираем, как Dota.
10. **Карта без книги.** `load_token_book` вернул None у одного из токенов → `no_books`, файла нет. Это окончательно, код выхода 0. Номера карт печатаем.
11. **Процессы — как Dota:** `ProcessPoolExecutor(max_workers=min(os.cpu_count(), число карт))`. Проба: 0,08–1,83 с на карту в одном процессе.
12. **Код выхода 1**, если есть `books_pending`, как у s08: «перезапусти позже». Готовые карты при этом записаны.
13. **CLI** — typer, как s06 и s08: `--catalog-dir` (по умолчанию `CS2_CATALOG_DIR`), `--telonex-dir` (`CS2_RAW_TELONEX_DIR`), `--output-dir` (`CS2_MARKET_SECONDS_DIR`). Корень книг — `telonex_dir / TELONEX_EXCHANGE`, аудит — `telonex_dir / CS2_TELONEX_AUDIT_PATH.name`, как в s08.

## 4. Изменения по файлам

### `src/cs2/s09_build_market_data.py` (новый, ~200 строк)

Импорты: `read_catalog_rows`, `Cs2CatalogRow` из s06; `PENDING_REASONS` из s08; `apply_table_dtypes` из s03; функции книг и `US_PER_SECOND`, `MAX_BOOK_AGE_SECONDS`, `PAIR_SUM_TOLERANCE` из `shared.utils.telonex_book`; `MAX_ENTRY_SPREAD_TICKS`; константы dataset; `MarketQuoteStatus`; `parse_utc`; `write_parquet`; `print_count`; `TELONEX_EXCHANGE`; пути из `cs2.constants`.

Имена ниже — предложение. Функции — глаголы, без комментариев.

- `MID_CHANGE_SECONDS = 10`, `class Cs2MarketSecondRow(TypedDict)`, `MARKET_SECOND_COLUMNS`, списки int / float / text колонок, `CACHE_BUSTING_PARAMETERS`, `CACHE_VERSION`.
- `MarketSecondsOutcome = Literal["built", "no_books"]`.
- `@dataclass(frozen=True) MarketSecondsJob`: `row: Cs2CatalogRow`, `telonex_root: Path`, `cache_path: Path`.
- `@dataclass(frozen=True) TeamBooks`: `team_a: TokenBook`, `team_b: TokenBook`.
- `@dataclass(frozen=True) MapBookAudit`: `condition_id: str`, `reasons: frozenset[str]`.
- `@dataclass(frozen=True) MapOutcome`: `match_id: int`, `outcome: MarketSecondsOutcome`.
- `@dataclass(frozen=True) PlannedMaps`: `jobs`, `cached: int`, `books_pending: tuple[int, ...]`.
- `@dataclass(frozen=True) MarketSecondsSummary`: `maps`, `cached`, `built`, `books_pending: tuple[int, ...]`, `no_books: tuple[int, ...]`.
- `build_cache_path(output_dir, match_id) -> Path` — `output_dir / f"v{CACHE_VERSION}" / f"match_id={match_id}.parquet"`. s10 возьмёт её же.
- `compute_zero_second(row) -> int` — `math.ceil(parse_utc(row.spawn_at).timestamp())`.
- `compute_last_second(row, zero_s) -> int` — `math.floor(parse_utc(row.ended_at).timestamp()) - zero_s`.
- `load_team_books(row, telonex_root, start_us, end_us) -> TeamBooks | None`.
- `compute_mid_change(current, earlier) -> float | None`.
- `build_market_second_rows(job) -> list[Cs2MarketSecondRow] | None` — решения 1–6. Словарь `second → MarketPairQuote` на `range(MODEL_START_SECOND - MID_CHANGE_SECONDS, last + 1)`, строки на `range(MODEL_START_SECOND, last + 1)`.
- `build_market_seconds_frame(rows) -> pd.DataFrame` — колонки по `MARKET_SECOND_COLUMNS` и `apply_table_dtypes`.
- `build_one_map(job) -> MapOutcome` — строит и пишет `write_parquet`, или `no_books`.
- `read_book_audit(audit_path) -> dict[int, MapBookAudit]` — файла нет → `RuntimeError` «rerun cs2-books».
- `has_pending_books(row, audits) -> bool` — True, если есть pending-клетка; нет строк или другой `condition_id` → `RuntimeError` с `match_id` и «rerun cs2-books».
- `plan_maps(rows, audits, telonex_root, output_dir) -> PlannedMaps` — порядок: аудит, потом готовый файл, потом задание.
- `run_jobs(jobs) -> list[MapOutcome]` — пул процессов, прогресс `market_seconds_progress done/total` каждые 500 карт и в конце, как `print_progress` в s08.
- `build_cs2_market_seconds(catalog_dir, telonex_dir, output_dir) -> MarketSecondsSummary`.
- `print_summary(summary)` — `print_count` для `maps`, `cached`, `built`, `books_pending`, `no_books`. Для двух последних печатает и номера карт.
- `main(...)` — typer. `books_pending` не пуст → `typer.Exit(code=1)`.
- `if __name__ == "__main__":` — как в s08.

C901 ≤ 10 на функцию.

### `Makefile`

- В `.PHONY` (`:16–17`) добавить `cs2-market-data`.
- После `cs2-books` (`:151–152`):

  ```make
  cs2-market-data:  ## CS2 s09 market seconds per catalog map: status, team A mid, prior, 10 s mid change, 300 s target (exit 1 = books pending)
  	$(PY) src/cs2/s09_build_market_data.py $(ARGS)
  ```

- В `cs2-collect` не добавлять. `cs2-prepare` с зависимостью от `cs2-market-data` — это STEP-011.

### `tests/test_cs2_market_seconds.py` (новый)

Книги пишет `write_token_book` из `tests/telonex_fixtures.py:30`. Каталог — `Cs2CatalogRow` и `catalog_frame` из s06. Аудит — `Cs2TelonexAuditRow` и `write_audit` из s08. Карта: `spawn_at` = `2026-09-10T18:00:00.400Z`, `ended_at` = `2026-09-10T18:00:30.200Z` → `zero_s` = 18:00:01, секунды −60…29, 90 строк. Хелпер пишет двусторонний снимок на каждую целую секунду в заданном диапазоне.

1. `test_mid_is_team_a_price` — токен 0: bid/ask 0.59/0.61, токен 1: 0.39/0.41. При `team_a_token_index` = 1 mid, prior и цель ≈ 0.40; при 0 — ≈ 0.60. Ещё проверить: первая строка `second` = −60, `state_ts_us` = (`zero_s` − 60) × 10⁶; последняя `second` = 29.
2. `test_mid_change_is_empty_when_ten_seconds_ago_is_not_ok` — в секунду 5 спред 6 тиков у одного токена. Строка 5: статус `wide_spread`, mid и изменение None. Строка 15: изменение None. Строка 16: изменение = mid(16) − mid(6). Строка −60: изменение есть, потому что секунда −70 `ok`.
3. `test_target_is_empty_after_market_close` — двусторонние снимки до секунды 300, односторонние (только bids) 301…315, дальше снимков нет. `find_asof_quote` берёт двусторонний снимок не старше 5 с. Поэтому цель есть при t ≤ 5 (снимок 300), None при t ≥ 6: 306…320 — `missing_quote`, дальше — `stale_quote`.
4. `test_prior_reads_window_before_anchor` — снимки −146…−86 с mid команды A 0.30, с −85 — 0.50. prior = 0.30. Второй случай: снимков до −85 нет. prior = None, а 90 строк всё равно есть.
5. `test_run_skips_pending_and_cached_maps` — две карты: аудит `skipped_valid` и `not_published`. Первый запуск: `built` 1, `books_pending` (вторая), файл только у первой. Второй запуск: `cached` 1, mtime файла не изменился.
6. `test_run_rejects_catalog_newer_than_audit` — карта без строк аудита → `RuntimeError` с «rerun cs2-books».
7. `test_missing_book_writes_no_file` — нет папки одного токена → `no_books`, файла нет.

Тесты 1–4 и 7 вызывают `build_market_second_rows` или `build_one_map` напрямую, без пула. Тесты 5–6 идут через `build_cs2_market_seconds`, с пулом, как `test_market_data.py`.

## 5. Крайние случаи

- **Карта около полуночи.** Книги двух дней лежат в одной папке токена, `load_token_book` читает все `*.parquet` и режет по окну.
- **Один день `not_published`, другой скачан** (пилот, 16–17.10). Карта `books_pending`, файла нет, код 1. После s08 следующий запуск её построит.
- **`http_404`, `no_telonex_data`, `missing_from_catalog`, `token_id_mismatch` у книг.** Окончательные. Карта строится с тем, что есть, или даёт `no_books`.
- **Неликвидная карта** (184073: 31 `ok` из 2 273, prior None). Файл пишем целиком. Отбор — в s10 и s11.
- **Одинаковые метки времени снимков** — порядок держит `telonex_book` (`_sort_columns`, стабильная сортировка).
- **`spawn_at` ровно на целой секунде** — `ceil` её не сдвигает, секунда 0 = `spawn_at`.
- **Каталог убрал карту.** Старый файл остаётся в `v<hash>/` и не мешает: s10 читает по строкам каталога.
- **Прерванный запуск.** `write_parquet` атомарен, недописанных файлов нет. Следующий запуск достроит остальное.

## 6. Проба в scratch

Папка `work/impl010/`. Реальные `data/cs2/` не трогать: везде `--catalog-dir`, `--telonex-dir`, `--output-dir`.

1. 10.09: `uv run python src/cs2/s09_build_market_data.py --catalog-dir <work>/impl008/sep10/catalog --telonex-dir <work>/impl009/sep10 --output-dir <work>/impl010/sep10`.
   Ждём: код 0, `maps: 6`, `built: 6`, `books_pending: 0`, `no_books: 0`. Число строк и статусов по картам — как в таблице факта 10. prior у 184073 пустой.
2. Повторить. Ждём `cached: 6`, `built: 0`, mtime файлов прежние.
3. 17.10: `--catalog-dir <work>/impl008/oct17/catalog --telonex-dir <work>/impl009/oct17 --output-dir <work>/impl010/oct17`. Ждём: код 1, `books_pending: 12`, файлов нет.
4. `ls ../esports-trader/data/cs2/market_seconds/` — пусто, как до пробы.

## 7. Проверки

1. `uv run python -m pytest tests/test_cs2_market_seconds.py -q` — всё зелёное.
2. `make test` — падают только две известные `lol_*` follow300 smoke (нет `data/lol` в worktree). Флейк `smoke[dota_9007700576]` — перезапустить его одного.
3. `uv run python -m basedpyright` — 0 ошибок.
4. `uv run python -m ruff check src/cs2 tests/test_cs2_market_seconds.py` и `uv run python -m ruff format --check` на тех же файлах. C901 ≤ 10.
5. `make help | grep cs2-market-data` — цель есть.
6. Проба из раздела 6, цифры — в progress.txt.
7. Один коммит в `cs2-signal`, `git status` чистый. `src/market_data/` и `src/shared/` не тронуты: `git diff --stat HEAD~1 -- src/market_data src/shared` пусто.

## 8. Риски и открытые вопросы

1. **Цель пуста в конце каждой карты, а бэктест торгует там без отсечки.** На 6 картах 10.09 цель пуста в 294–300 из последних 300 с (факт 10). s11 почти не увидит строк, где карта кончается раньше чем через 300 с. При позднем счёте в обучение идут только карты, которые шли ещё 300+ с. Там отстающая команда чаще отыгрывала раунды. Модель может выучить «цена уходит от лидера» и в конце карты покупать отстающего. Dota этого не видит: у неё отсечка покупок 480 с. Частично это закрывает фильтр цены входа 45–85¢. План это правило фиксирует (PLAN.md, «Отсечки покупок нет»), шаг его не меняет. Предлагаю в отчёт пилота и STEP-015 строку: PnL сделок в последние 300 с карты отдельно.
2. **Пилот 1–17.10.** Книги 17.10 на 18.10 не вышли. Карты с окном на 17.10 дадут `books_pending` и код 1. `make cs2-prepare` (зависит от `cs2-market-data`) остановится. Оператор ждёт выхода книг или запускает s10 сам.
3. **Версия кэша не видит смену логики** без смены константы или колонки, как у Dota. Кто меняет логику s09, добавляет значение в кортеж (у Dota для этого литерал `30`).
4. **prior = None** остаётся в файле (184073). Dota и LoL такие карты выкидывают. Решает STEP-011.
5. **Время полного прогона** я не замерял. Оценка: ~1 с на карту в одном процессе (книги лета тяжелее, до 214 тыс. снимков на токен в окне 185135), на 8–10 процессах 12 тыс. карт — 20–30 мин.

## 9. Чего в шаге нет

- `signal_market_p_radiant_30s` и других горизонтов.
- Манифеста s09 с причиной на карту: `no_books` и `books_pending` только в печати. Добавить, если s10 понадобится причина на карту.
- Отсечки цели по `market_closed_at`.
- Правок `src/market_data/`, `src/shared/`, `src/lol/`.
- s10 и всего ниже.
