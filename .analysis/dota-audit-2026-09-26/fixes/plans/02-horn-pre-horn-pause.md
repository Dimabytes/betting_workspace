# План фикса п. 2: horn из лайв-архива раньше настоящего

Пункт отчёта: `../../REPORT.md`, S1, п. 2. Реестр: `../../work/orchestrator/findings.md`, O1.
Все пути кода ниже — в `esports-trader`.

## Суть решения

Одно правило horn для всех источников: horn закрепляется по первому тику с часами > 0.
Каталог берёт horn архива из тиков schedule, а не из `match.json`.
Так чинятся и новые архивы, и старые, и старые архивы Oddin до `deebb730`. `match.json` не переписываем.

## Как неверный horn попадает в данные

1. Лайв. `src/trader/live_feed.py:102` `horn_is_pinnable` пускает GRID/Steam на первом тике фазы `PRE_HORN`.
   Horn тика GRID = `occurred_at − clock_seconds` (`grid_feed.py:113`). Пауза на −47 длиной 449 с даёт horn
   на 449 с раньше.
2. `match_meta.pin_horn_from_event` пишет этот horn в `match.json`. `finalize_match` перезаписывает его
   `trusted_horn` из `grid_archive.summarize` → `first_event_horn_iso`. Это то же правило, тот же тик.
3. Steam: `steam_archive.py:109-121` `_first_trusted_horn_iso` — вторая копия правила, без проверки часов.
   Steam-архивов сейчас 0, но копия та же.
4. Индекс архивов. `archive_index/index.py:508` кладёт `meta["horn_at_utc"]` в строку индекса.
   `archive_index/schedule.py:502` кладёт его же в `identity.horn_at_utc`.
5. `collect/s03_merge_archive_links.py:143-171` сверяет horn индекса с horn schedule и пишет
   `archive_horn_at_utc` в `match_links`.
6. Два читателя `archive_horn_at_utc`:
   - `collect/s06_publish_catalog.py:119-127` — horn каталога для всех карт с архивом;
   - `collect/s05a_fetch_prices_history.py:71` — якорь приора, если у карты нет спавна GRID.
7. Каталог → кэш `market_seconds` → датасеты → модель и бэктест.

Важный факт: тики schedule уже несут верный horn. Индекс проигрывает фид через текущие редьюсеры
(`schedule.py:275-339`). Редьюсер Oddin уже ждёт `game_time > 0`. Значит, верный horn можно получить из
тиков без правки сырых архивов.

Почему не `grid_derived` в `s06`. Он есть только у карт со спавном GRID. У 4 карт Oddin
(9007208887, 9007618767, 9007618656, 9007700576) спавна нет. `s05a` тоже читает horn архива.
Правка источника чинит всех трёх читателей одной правкой.

## Шаг 1. Одно правило в лайве

1. `src/trader/live_feed.py` `horn_is_pinnable`: убрать исключение для Oddin.
   ```python
   return event.snapshot.phase in HORN_CLOCK_PHASES and event.snapshot.second > 0
   ```
   Переписать docstring: правило одно для всех источников и почему.
2. `src/trader/steam_archive.py` `_horn_iso_from_match`: добавить условие `match["game_time"] > 0`.
3. Без правки чинятся: `match_meta.pin_horn_from_event`, `match_worker._maybe_pin_horn`,
   `grid_archive.summarize`, `oddin_archive.summarize`, `finalize_match`.
4. `write_match_start` пишет horn первого тика как временное значение. Не трогаем: pin и finalize
   перезапишут его.

Про GRID: `snapshot.second` отстаёт от часов на задержку таблицы. Из `second > 0` следует, что часы > 0.
Пин придёт на несколько секунд позже, но horn тика считается по `clock_seconds` борда и остаётся точным.

## Шаг 2. Horn архива в индексе из тиков

1. `src/archive_index/schedule.py` `extract_schedule`: `identity.horn_at_utc` брать по правилу шага 1 из
   событий фида, а не из `meta["horn_at_utc"]`. Вызвать `first_event_horn_iso` на событиях внутри
   `_extract_grid` / `_extract_oddin`. Второй копии правила не писать.
2. `EXTRACTION_RULES_VERSION`: `feed-schedule-v4` → `feed-schedule-v5`. Индекс переизвлечёт все schedule
   (`index.py:319`).
3. `src/archive_index/index.py`: колонку `horn_at_utc` строки брать из schedule, не из `match.json`.
   Убрать её из `_meta_columns`. Добавить в поля schedule, которые `_cached_schedule_fields` переносит
   из прошлой строки (`_SCHEDULE_ROW_KEYS`).
4. Если ни один тик не прошёл правило, horn = `None`. `s03` отправит строку в `excluded_horn_inconsistent`,
   как сейчас при пустом horn.
5. `s03_merge_archive_links.py:159`: сравнение horn индекса с horn schedule станет всегда равным.
   Оставить только проверку на пустой horn. Сравнение удалить.
6. `s06` и `s05a` код не меняют.

## Шаг 3. Тесты

1. `tests/test_match_meta.py`: параметризовать `test_oddin_pin_horn_ignores_nonpositive_clock` по GRID и
   Oddin. Случай GRID: тик `PRE_HORN` на −47 с ранним horn → пина нет. `second = 0` → пина нет.
   `second = 1` → horn = настоящий.
2. Тест `grid_archive.summarize`: архив с паузой до horn → `trusted_horn` = horn после паузы.
   Сейчас ни один тест не закрепляет сохранённый horn (`link-grok-arch` п. 11).
3. Тест `steam_archive._first_trusted_horn_iso`: тот же случай.
4. `tests/test_feed_schedules.py`: `identity.horn_at_utc` берётся из тиков. Фикстура с неверным horn в
   `match.json` должна дать horn из тиков.
5. `tests/test_archive_index.py`: horn строки индекса = horn schedule.
6. `make test` целиком. Не запускать во время лайв-карты.

## Шаг 4. Деплой лайва

1. Деплой только после разрешения пользователя.
2. Рестарт только без лайв-карт и без открытых позиций. Рестарт открывает окно потери сессии Oddin (п. 3).
3. Проверка: следующая законченная карта с паузой до horn. Horn в `match.json` = horn первого тика с
   часами > 0. Для карты GRID он совпадает с `grid_derived` с точностью до задержки фида (~7.6 с).

## Шаг 5. Пересборка данных

1. Сохранить копию `data/new_processed/match_catalog/match_catalog.parquet` для сравнения.
2. `make archive-index`. Все schedule переизвлекутся под `feed-schedule-v5`.
3. `make link-archive`, `make prices`, `make catalog`.
   `make prices` сам дозапросит окна, которые кэш не покрывает (`cache_covers_window`).
4. Сравнить старый и новый каталог по `match_id`: `horn_at`, `ended_at`, `horn_source`.
   - Ожидание: ~58 карт со сдвигом на длительность паузы до horn и 3–4 карты Oddin со сдвигом 14–17 мин.
   - У остальных карт возможен сдвиг на 1–2 с (дрожание horn тика).
   - Если сдвинутых карт с Δ > 20 с намного больше или меньше ~61, остановиться и разобраться.
   - Посчитать карты без спавна GRID, у которых сдвинулся якорь приора в `s05a`.
5. Удалить файлы кэша `market_seconds_cache_path(match_id)` для всех `match_id`, у которых изменился
   `horn_at` или `ended_at`. Кэш не видит horn (п. 12), `build_market_data` пропускает готовые файлы.
6. `make prepare`. Цель сначала пересоберёт удалённые кэши (`market-data`), потом датасеты.

## Шаг 6. Переобучение и бэктест

1. `make train`. Записать MAE gain и directional markout research-модели. Было 0.243¢ и 1.618¢.
   Без 57 карт было 0.218¢ и 1.511¢. Ожидание: метрика падает. Это честный уровень, не регресс.
2. `make backtest ARGS="--validation --name horn-fix"`. Сравнить PnL с текущим прогоном.
3. Боевая модель обучена на тех же данных (55 карт, 475 строк, 2.8%, плюс строки карт Oddin).
   Выкатывать новую боевую модель или нет — решает пользователь после сравнения.

## Шаг 7. Регрессионная проверка

Скрипты лежат в `../../work/orchestrator/`. Запускать из корня `esports-trader`: они читают `data/...`
относительными путями.

1. `horn_event_study_archive.py`. Было: карты с паузой до horn реагируют на убийства через +62.5 с,
   контрольные — через 0 с. Ожидание: обе группы около 0 с.
2. `horn_check.py` (horn архива против `grid_derived` на картах, где есть оба). Было: 29 карт GRID с
   |Δ| > 60 с. Ожидание: ни одной карты с |Δ| > 60 с. Остаток около задержки фида GRID (~7.6 с).
3. Выбросы, которые останутся, записать отдельно. Кандидат — поздний вход после пауз в игре:
   horn первого тика тогда сдвинут на эти паузы. Это отдельная проблема, в этом фиксе её не чиним.

## Не входит в фикс

- П. 12: ключ кэша `market_seconds` по строке каталога. Здесь только ручное удаление файлов.
- Якорь приора для карт без спавна (`feed-devin` F8): `s05a` берёт horn без −90 с, лайв — horn первого
  тика − 90 с. Фикс сдвинет этот якорь к настоящему horn. Число карт считаем в шаге 5.
- Якорь приора в лайве (`match_worker.py:553`). Не трогаем: для карт со спавном он верный (`parity-luna` F1).
- `match.json` старых архивов остаётся с ранним horn. Вьюер (`src/viewer/*`) показывает для них ранний horn.
- LoL: `horn_is_pinnable` общий, лайв-архивы LoL получат то же правило. Датасет LoL horn архива не читает.

## Готово, когда

- [ ] Шаги 1–3: код и тесты в `main`, `make test` зелёный.
- [ ] Шаг 4: лайв задеплоен, первая карта с паузой до horn проверена.
- [ ] Шаг 5: каталог пересобран, дифф совпал с ожиданием, кэши и датасеты пересобраны.
- [ ] Шаг 6: research-модель и бэктест пересобраны, цифры записаны в `../PROGRESS.md`.
- [ ] Шаг 7: event study около 0 с, `horn_check.py` без |Δ| > 60 с.
