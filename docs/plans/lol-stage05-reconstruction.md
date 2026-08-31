# LoL Stage 05: план фикса после ревью 90d3c92 + 5829eeb

todos:

- id: "phase-1-audit-table"
  content: "Вынести таблицу аудита в src/lol/stage05_audit.py, заменить TypedDict+cast на Counter, переставить порядок в accept_prepare_audit"
  status: completed
- id: "phase-2-map-rules"
  content: "Скип вместо дропа для window stamp, счётчики пропущенных кадров с порогом, отдельная причина для пустой карты, граница поиска spawn, чистка apply_split_eligibility"
  status: completed
- id: "phase-3-rerun"
  content: "Один прогон prepare, зафиксировать реальный stage05_audit.json, сверить с ожидаемыми числами"
  status: completed
- id: "phase-4-ab"
  content: "train + backtest на новых данных, sensitivity по ok_quote_fraction, замер sign flip при offset +5 и +7"
  status: pending

---

## Контекст

Два коммита уже в `main`:

- `90d3c92` — закоммиченная таблица аудита вместо потолка `MAX_LIVESTATS_INVARIANT_MAPS`, исключение Cash Back.
- `5829eeb` — трёхсекундное окно отсутствия расходника, поиск spawn по форме 10×500/lvl 1/deaths 0 без якоря, details как надмножество window, скип битых кадров вместо дропа карты, `aborted_feed`, раздельная eligibility для train и validation.

Ревью нашло 13 пунктов. Ниже они разложены на четыре фазы. Каждая фаза — один коммит.

Два пункта закрыты без правок:

- **Пункт 7 (Cash Back).** Исключение остаётся. После `5829eeb` оно решает «оставить кадр или пропустить», а не «спасти карту». Правится только комментарий у `LOL_CASHBACK_UNDO_MAX_GOLD`, чтобы текст не описывал старый мир. Уходит в фазу 2.
- **Проверка, что `datasets_last` собран текущим кодом.** Принимаем как известную проблему. Не проверяем.

Один пункт решён против исходного анализа:

- **`negative_net_worth` остаётся дропом карты.** `cumulative` в `build_consumed_timeline` монотонный и общий на всю карту. Одно фантомное списание смещает вниз все последующие кадры, а не один. Отрицательный net worth — это кадр, где смещение случайно превысило золото. Скипнуть его значит выбросить симптом и оставить заражённые кадры. После фикса расходников счётчик обязан уйти в ноль, и проверка становится сигнализацией.

---

## Фаза 1 — таблица аудита и публикация

Структурная чистка. Состав карт не меняется. Меняется схема `stage05_audit.json` и порядок операций при `--accept-audit`.

### 1.1 Вынести модуль (пункт 13)

`src/lol/05_prepare_dataset.py` вырос с 1010 до 1233 строк. Блок счётчиков самодостаточен и не зависит от сборки карт. Переносим в `src/lol/stage05_audit.py`:

`zero_audit_counts`, `audit_count`, `set_audit_count`, `add_audit_count`, `require_additive_audit_counts`, `print_audit_counts`, `print_audit_count_diff`, `write_audit_json`, `load_audit_json`, `audit_counts_from_builds`, `audit_counts_from_parquets`, `copy_dataset_parquets`, `accept_prepare_audit`.

`audit_counts_from_builds` берёт `MapBuild` и `ValidationReplay`. Чтобы не тащить импорт по кругу, передавай в модуль уже готовые последовательности причин и сплитов, а не сами builds. Ориентир: `05_prepare_dataset.py` возвращается примерно к 1000 строк.

`tests/test_lol_prepare_dataset.py` — 1520 строк. Отдели тесты аудита в `tests/test_lol_stage05_audit.py`.

### 1.2 Counter вместо TypedDict с приведениями (пункт 9)

Ни одно поле `LolStage05AuditCounts` не читается по литеральному ключу. Вместо этого четыре функции доступа и три `cast(dict[str, int], ...)`, которые обходят собственный тип. Проверок типа он не даёт.

Берём `Counter[str]` для подсчёта и `dict[str, int]` для JSON. Исчезают `zero_audit_counts`, `audit_count`, `set_audit_count`, `add_audit_count` и все `cast`. Сравнение, печать и diff работают на dict напрямую.

`AUDIT_COUNT_KEYS = tuple(LolStage05AuditCounts.__annotations__)` завязывает порядок ключей в файле на порядок полей в TypedDict. Перестановка полей молча меняет формат. Кладём явный кортеж ключей в `src/shared/constants/lol.py`.

`LolStage05AuditCounts` после этого не нужен. Удаляем из `src/shared/types/lol.py`.

### 1.3 Убрать тавтологии и мёртвые ветки (пункт 10)

- `validation != eligible` в `require_additive_audit_counts` не может сработать: после `apply_split_eligibility` все не-accepted валидационные карты дропнуты до сборки replay. Ключ `eligible` дублирует `validation`. Убираем ключ и проверку.
- `else: raise RuntimeError(f"unknown backtest reason ...")` в `audit_counts_from_builds` и `audit_counts_from_parquets` недостижим по той же причине. Убираем.
- `DROP_REASON_KEYS` смешивает prepare-причины и backtest-причины в одном кортеже. Имя врёт. Переименовываем в `AUDIT_DROP_KEYS` и пишем в комментарии, что после `apply_split_eligibility` backtest-причина становится prepare-причиной.

### 1.4 Порядок в `accept_prepare_audit` (пункт 2)

Сейчас копирование идёт до проверки. Если проверка падает, `datasets/` уже перезаписан, а JSON не записан. Проверяется прямо сейчас: на текущем `datasets_last` выйдет `validation=1450` против `eligible=1436`, и функция бросит `RuntimeError` уже после подмены.

Переставляем три строки:

```python
counts = audit_counts_from_parquets(last_dir)   # 1. считаем
require_additive_audit_counts(counts)            # 2. проверяем
print_audit_counts(counts)
copy_dataset_parquets(last_dir, output_dir)      # 3. копируем
write_audit_json(audit_json, counts)             # 4. фиксируем
```

Сначала всё, что может упасть. Потом всё, что меняет диск. Временная директория с `os.replace` не нужна: если процесс умрёт на середине копирования, `datasets_last` цел и JSON не записан, достаточно перезапустить. JSON на диске становится маркером того, что копирование дошло до конца.

### 1.5 Вернуть проверку дублей на место (пункт 11)

`validate_unique_accepted_maps` переехал за `apply_split_eligibility`. Теперь дубликат `match_id`, у которого одну копию дропнули по eligibility, не поднимет ошибку. Возвращаем вызов перед `apply_split_eligibility`.

### 1.6 Сохранять диагностику отброшенных карт (пункт 12)

`apply_split_eligibility` ставит `backtest_audit=None`, и 14 карт исчезают из `backtest_audit.parquet` (1450 строк против 1436). Причина остаётся в `audit.parquet`, но `has_books`, `has_trades`, `ok_quote_fraction` и границы реплея пропадают. Это ровно те поля, по которым понятно, почему карта не прошла.

Обнуляем `rows` и `market_rows`, `backtest_audit` оставляем. `collect_validation_replay` фильтрует по `build.included`, поэтому в replay такие карты всё равно не попадут.

**Проверка фазы:** `make lint-all` и `make test` зелёные. Прогон данных не запускаем.

---

## Фаза 2 — правила отбора карт

Меняет состав и числа. Прогон после фазы 3, не здесь.

### 2.1 Скип кадра для пропущенного window-стампа (пункт 3)

В `validate_timed_frames` ветка `stamp not in consumed.by_stamp` возвращает `REASON_WINDOW_DETAILS_MISMATCH` и убивает карту с одного кадра. Consumed-таймлайн строится по details-кадрам отдельно, поэтому пропущенный window-кадр ничего не портит: в этой секунде нет строки, сетка возьмёт предыдущий кадр при возрасте до 2 секунд. Ошибка локальная. Скипаем кадр.

`negative_net_worth` в той же функции остаётся `return`. Обоснование выше.

### 2.2 Считать пропущенные кадры и ввести порог (пункт 4)

Сейчас число скипнутых кадров не попадает никуда. Карта с 90% битых кадров выглядит как чистая. Поле `invariant_rule` заполняется только у полностью дропнутых карт.

Добавляем в `LolPrepareAuditRow`:

- `skipped_invariant_rows` — кадры, скипнутые по правилу или ошибке парсинга;
- `skipped_stamp_rows` — кадры, скипнутые из-за отсутствия details-стампа.

Порог нужен не для отчётности. `previous` двигается только на валидных кадрах, поэтому постоянная просадка золота бесконечно валит правило 2, все последующие кадры скипаются, и карта тихо принимается с одним префиксом. Дропаем карту, когда доля скипнутых кадров выше порога. Стартовое значение подбери по фазе 3; ориентир — 20%.

### 2.3 Отдельная причина для пустой карты (пункт 5)

```python
if not validated:
    if last_rule is None:
        return 1
```

Единица здесь значит «валидных кадров не осталось», а не «нарушено правило 1». В аудите это не различить. Заводим `REASON_ZERO_USABLE_FRAMES` в `src/shared/constants/lol.py`, добавляем ключ в таблицу аудита.

### 2.4 Граница поиска spawn (пункт 6)

`5829eeb` снял `LOL_SPAWN_SEARCH_SECONDS` целиком. Анализ говорил «расширить поиск», и это разные вещи.

Якорь делал две работы. Первую — отсекал ложные spawn — теперь закрывает точная проверка формы 10×500/lvl 1/deaths 0. Вторая ушла: гарантия, что нулевая секунда игрового времени близка к `loading_anchor_ts`. `find_spawn_index` берёт **первый** кадр нужной формы, а в LoL вся первая минута и весь загрузочный экран выглядят как 500/lvl 1/deaths 0. Архив, начатый за минуту до старта, сдвинет все `second`, лаг 10 и метку 300s на минуту.

Остаточный риск — архив с отменённой попыткой и рестартом. `find_spawn_index` возьмёт spawn первой попытки, последний кадр будет нормальным лейтгеймом, и `aborted_feed` промолчит.

Порядок действий:

1. Скриптом по архивам собрать распределение `spawn_wall_seconds − loading_anchor_ts` на тех картах, что раньше падали в `no_spawn_frame` (по анализу их 181).
2. Поставить `LOL_SPAWN_SEARCH_SECONDS` по данным. Ориентир 15–30 минут: ловит рестарт серии и ранний архив, не режет нормальные карты.

Скрипт читает архивы и не требует полного prepare, поэтому фазу 3 он не блокирует.

### 2.5 Чистка `apply_split_eligibility` (пункт 8)

50 строк, три блока `replace`, две мёртвые ветки.

`event_starts.get(...)` не вернёт `None`: `event_start_times` строится по тем же builds, а ветка `build.start_time is None` отработала выше. `audit is None` тоже недостижимо: каждый included build получает `backtest_audit` в конце `build_one_map`. Фолбэк `REASON_MISSING_REQUIRED_CHANNEL if audit is None` мёртвый и при этом врёт про причину. AGENTS.md запрещает ветки для невозможных входов.

Сворачиваем в две функции:

```python
def eligibility_drop_reason(build: MapBuild, split: LolPrepareSplit) -> str | None:
    """Причина дропа после сплита, или None."""
    if split == "train":
        return None if labeled_row_count(build) else REASON_ZERO_LABELED_ROWS
    reason = build.backtest_audit["reason"]
    return None if reason == REASON_ACCEPTED else reason


def drop_build(build: MapBuild, reason: str) -> MapBuild:
    """Карта без строк, с причиной дропа."""
    return replace(build, included=False, reason=reason, rows=(), market_rows=())
```

`backtest_audit` в `drop_build` не обнуляем — см. 1.6.

### 2.6 Комментарий Cash Back (пункт 7)

Комментарий у `LOL_CASHBACK_UNDO_MAX_GOLD` писался, когда исключение спасало карту от дропа. Сейчас оно выбирает между «оставить кадр» и «пропустить кадр». Одна строка.

**Проверка фазы:** `make lint-all` и `make test` зелёные, новые тесты на скип стампа, на порог доли скипнутых кадров и на `REASON_ZERO_USABLE_FRAMES`.

---

## Фаза 3 — прогон и честная таблица

Один воспроизводимый прогон. Коммит содержит новый `stage05_audit.json` и правки `docs/domain.md`.

### 3.1 Почему это обязательно

Текущий `src/lol/stage05_audit.json` записан 31 авг 11:35. Parquet-ы в `data/lol/processed/datasets*` — от 30 авг 23:45, то есть до обоих коммитов. В `backtest_audit.parquet` нет колонки `ok_quote_fraction`, которую добавил `5829eeb`.

Числа в JSON — это старые значения из parquet минус 14: `accepted` 4095→4081, `validation` 1450→1436. Остальное не тронуто. При изменившихся правилах spawn, скипе кадров и subset-проверке эти счётчики не могут остаться прежними.

### 3.2 Порядок

```bash
uv run python src/lol/05_prepare_dataset.py            # упадёт на несовпадении таблицы, напечатает diff
uv run python src/lol/05_prepare_dataset.py --accept-audit
```

Первый запуск пишет `datasets_last/` и печатает построчный diff старых и новых счётчиков. Второй публикует и записывает JSON.

### 3.3 Что сверить с анализом

Это ориентиры из твоего разбора, не гарантии. Расхождение — повод разбираться, а не подгонять.

| Ключ | Было | Ожидание | Откуда |
| --- | --- | --- | --- |
| `no_spawn_frame` | 186 | около 5 | 181 карта проходит при расширенном поиске |
| `livestats_invariant_violation` | 239 | близко к 0 | карта больше не умирает от кадра; остаются 1 старт с мидгейма и то, что срежет порог 2.2 |
| `window_details_mismatch` | 30 | около 1 | 29 закрыты subset-проверкой |
| `negative_net_worth` | 58 | 0 | трёхсекундное окно отсутствия расходника |
| `aborted_feed` | нет ключа | не меньше 1 | карта 115654384752392981 |
| `zero_labeled_rows` | нет ключа | около 33 | train-карты без размеченных строк |
| `zero_usable_frames` | нет ключа | смотреть | новый ключ из 2.3 |
| `accepted` | 4095 | около 4300 | плюс 181 + 29 + 58, минус 33 + 14 + aborted |
| `validation` | 1450 | 1436 плюс новые | минус 14 `missing_required_channel` |

Отдельно проверь расхождение по validation. Анализ говорит, что в DIR300 реально участвуют 1439 карт, а `1450 − 14 = 1436`. Три карты не сходятся. Найди их до фазы 4.

### 3.4 Что ещё поменяется без изменения счётчиков

Среди принятых карт 665 имеют хотя бы одно ложное исчезновение расходника, 132 — несколько, 85 — повтор у одного игрока, 14 занижены на 335–500 золота уже в первую минуту. Эти карты остаются приняты, но их фичи меняются. Поэтому обучающие данные едут даже там, где счётчик карт стоит на месте. Это причина фазы 4.

### 3.5 Обновить документацию

`docs/domain.md` уже описывает subset-проверку и скип кадров. Дополни: новые причины `zero_usable_frames`, порог доли скипнутых кадров, возвращённая граница поиска spawn, поля `skipped_invariant_rows` и `skipped_stamp_rows`, снятый ключ `eligible`.

---

## Фаза 4 — A/B и отчёт

### 4.1 Честное сравнение

Текущая research-модель обучена на parquet с другим SHA и другим числом матчей. Нынешний P&L нельзя связать с текущим состоянием кода. Нужен один сквозной прогон: prepare → train → backtest, на одинаковых картах, строках и split.

Сравнение — старый `totalGold` против нового net worth. Одна пара, не десять.

### 4.2 Покрытие котировок

`ok_quote_fraction` уже в `backtest_audit.parquet`. Четыре карты дают меньше 10% пригодных котировок, две — меньше 5%, остальное `stale_quote`.

Автоматически не дропаем: это может быть настоящая неликвидность, и live тоже не торговал бы. Делаем sensitivity-отчёт с этими картами и без них.

### 4.3 Sign flip относительно GRID

На единственной dual-feed записи: сырой `totalGold` — 16.0%, реконструированный net worth при offset +7 — 5.8%, тот же ряд при offset +5 — 1.5%, снятие одного ложного исчезновения расходника — 4.5%.

То есть 5.8% смешивают формулу, flicker инвентаря и одну-две секунды рассогласования часов. По одной карте универсальную поправку не вывести. При преимуществе GRID от 500 золота ошибок знака на этой записи нет вообще.

Замер после фикса расходников повтори на offset +5 и +7. Если разрыв между ними сохранится, нужна вторая dual-feed запись, а не подбор константы.

### 4.4 Запись результата

Заметка в `docs/experiments/` с именем run-директории, строка в индекс. Числа экспериментов в `AGENTS.md` не копируем.

---

## Порядок коммитов

1. `refactor: LoL stage 05 audit table and publish order` — фаза 1.
2. `feat: LoL stage 05 map rules — skip local glitches, bound spawn search` — фаза 2.
3. `data: regenerate LoL stage 05 audit from a real prepare run` — фаза 3.
4. `docs: LoL net worth A/B and quote coverage sensitivity` — фаза 4.

Фазы 1 и 2 не требуют прогона данных. Фазу 3 запускай только после того, как обе прошли `make lint-all` и `make test`.
