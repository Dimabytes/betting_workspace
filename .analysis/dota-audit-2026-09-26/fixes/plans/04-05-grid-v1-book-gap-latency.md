# План фикса п. 4, п. 5 и задержки заявок из п. 1

Пункты отчёта: `../../REPORT.md`, S2, п. 4 и п. 5; S1, п. 1 (б) и (в) — задержка и `secondsDelay`.
Связан с планом 01 (части 2, 4, 5) и планом 06 (п. 6 чинится параллельно).
Источники: `../../reports/time-grok.md` F2, F3; `../../reports/signal-devin.md` S2 (grid-v1);
`../../reports/live-devin.md` F6; `../../reports/fill-devin.md` (`secondsDelay`); `../../reports/parity-luna-orders.md` п. 6.
Скрипты замеров: `../scripts/latency_hops.py`, `../scripts/book_gap_window.py`.
Все пути кода ниже — в `esports-trader`, если не сказано иное.

## Решения (2026-09-28)

С пользователем, подтверждено при реализации:
1. Задержка — две постоянные, без случайности: вставка 175 мс, отмена 60 мс.
   Почему не 340 мс — раздел «Задержка».
2. П. 5: фильтр по дыре стакана удалён целиком. Почему не окно 480 — раздел «Отбор карт».
3. П. 4 отложен. `grid-v1` остаётся как есть, для Dota и для LoL. Код шага 1 не менялся.

П. 6 идёт планом 06 и в этот коммит не входит.

Код задержки и п. 5 закоммичен в `esports-trader` `643bcd7b`.
Прогон `--validation` не делался: он сравнивает итог с базой п. 6, а seeds 1–2 п. 6 ещё не готовы.

## Задержка: замер (2026-09-28)

`../scripts/latency_hops.py` читает `core_trace` всех живых сессий Dota. Заявку и отмену он связывает
по `order_id` ядра, а не по «последней заявке», как `work/live-devin/latency.py`. Все интервалы — по
монотонным часам ядра в одном процессе, сдвига часов нет.

28 сессий после деплоя 24.09, мс:

| Участок | n | p10 | p50 | p90 | p99 | mean |
|---|---:|---:|---:|---:|---:|---:|
| `places` → `OrderAccepted` | 2,028 | 82 | 173 | 226 | 448 | 167 |
| — только GRID | 368 | 82 | 134 | 205 | 390 | 146 |
| — только Oddin | 1,660 | 82 | 174 | 228 | 469 | 172 |
| `cancels` → `CancelAck` | 1,906 | 56 | 62 | 88 | 359 | 74 |
| кадр фида → `SignalUpdate`, GRID | 3,842 | 5 | 7 | 10 | 16 | 8 |
| кадр фида → `SignalUpdate`, Oddin | 76,597 | 3 | 4 | 5 | 8 | 4 |
| книга WS → `BookUpdate` | 390,959 | 2 | 30 | 103 | 1,065 | 122 |

232 сессии до деплоя: вставка p50 146 мс, отмена p50 70 мс.

Что значат участки:
- Вставка — от плана ядра до ответа на POST. Внутри: запись в sqlite и fsync (`_durable_place`) и путь до биржи и обратно.
- Отмена — то же для DELETE. Отмена почти в 3 раза быстрее вставки.
- Кадр фида → ядро — 4–7 мс. Это нижняя оценка: я беру ближайший кадр до сигнала.
- Книга → ядро — возраст самого свежего сообщения WS в момент цикла. p90 103 мс — это
  `debounce` форка 100 мс (`poly-maker/src/polymaker/engine.py:333`).
- Правки заявки на бирже нет. Перенос цены — отмена и новая заявка. `plan.moves` — перенос уровня
  внутри ядра, на биржу он не уходит (`src/strategy/quoting.py:629-663`).

### Почему не 340 мс

340 = 166 + 174 (`live-devin` F6). 166 мс — это «сигнал → заявка» внутри ядра: `debounce_ms` 100 мс
и таймеры пробуждения. Бэктест гоняет то же ядро с той же политикой. Эти 166 мс в нём уже есть:
`BookUpdate` идёт с `execute=False`, план исполняется на `Wake` через `debounce_ms`
(`src/backtest/strategy.py:313-327`). В лайве тот же `debounce` делает форк, а ядро получает
`Wake(forced)` в том же цикле. Прибавить 166 мс — посчитать `debounce` дважды.

Бэктесту не хватает только сети. Книга в бэктесте идёт по времени биржи (`timestamp_us` Telonex,
`prediction-market-backtesting/.../telonex.py:3133`). Лайв теряет путь книги от биржи к нам и путь
заявки от нас к бирже. Вместе это примерно один круг до биржи. Поэтому:
- вставка = p50 круга POST, 173 → 175 мс;
- отмена = p50 круга DELETE, 62 → 60 мс.

Сейчас обе задержки 85 мс (`NETWORK_LATENCY_MS`, `src/shared/constants/strategy.py:18`). После правки
вставка медленнее в 2 раза, отмена почти не меняется.

Одно число на GRID и Oddin: разница p50 40 мс, это разные сессии и часы. Хвост (p99 448 и 359 мс,
зависания отмен на минуты) постоянное число не описывает. Это известный потолок.

### `secondsDelay` к нашим заявкам не относится

Отчёт (п. 1 (в), `fill-devin`) считает, что биржа держит новую заявку 1 с до очереди. Проверка: время
исполнения по бирже (`ts_utc` в `session.jsonl`) минус время плана заявки по часам хоста.

| Сессии | Исполнений | < 100 мс | < 200 мс | < 300 мс | < 500 мс | < 1000 мс |
|---|---:|---:|---:|---:|---:|---:|
| после деплоя | 476 | 0 | 4 | 13 | 42 | 107 |
| до деплоя | 2,515 | 0 | 15 | 35 | 110 | 347 |

454 исполнения пришли раньше 1 с после плана, 19 — раньше 200 мс. Значит, post-only заявка стоит в
книге раньше 1 с. Docstring `build_order_latency` прав. Вариант «очередь через 1 с после accept» из
плана 01, часть 2, отпадает. Кода по `secondsDelay` нет.

## Отбор карт (п. 5): замер (2026-09-28)

`../scripts/book_gap_window.py` пересчитывает `find_longest_book_gap` на текущем кэше `market_seconds`.
В валидации 798 карт, флаг стоит у 94. У 14 из них есть живой архив, то есть это карты `schedule`.

Чем заполнена самая длинная дыра (окно −60..899):

| Статус | С архивом | Без архива |
|---|---:|---:|
| `wide_spread` | 9 | 61 |
| `stale_quote` | 3 | 15 |
| `missing_quote` | 2 | 4 |

- 70 из 94 карт выпали из-за `wide_spread`. Это состояние рынка, а не дыра данных. Лайв его тоже видит
  и не входит по своему гейту `max_entry_spread_ticks` = 6. Фильтр выкидывает неликвидные карты целиком.
- Окно −60..479 возвращает 22 из 94. Из 14 карт с архивом — только 6.
- Окно 480 не убирает взгляд в будущее. Дыра на секунде 400 всё ещё снимает сделки на секунде 100.
- Решение «по данным на тот момент» — это посекундный гейт. Он в ядре уже есть: `book_stale_s` = 5 с,
  `max_entry_spread_ticks`. Поэтому рекомендую удалить фильтр, а не сузить окно.

## П. 4: что даёт и что стоит

LIVE rebuild2 (`validation_join_delta02_x015_cut480_p4_rebuild2-20260927`), `engine_pnl`:

| Игра | `grid_v1`, карт | `grid_v1`, $ по seed 0 / 1 / 2 | `schedule`, карт | `schedule`, $ на всех seed |
|---|---:|---|---:|---:|
| Dota | 464 | 1,913.33 / 2,451.22 / 2,630.32 | 227 | 1,739.37 |
| LoL | 940 | 5,294.78 / 4,999.53 / 4,988.05 | 244 | 1,299.90 |

- `schedule` детерминирован. Без `grid_v1` бэктест даёт одно число на один прогон, seeds не нужны.
- Пропадают и другие находки про `grid_v1`: 10 с вместо 8 с у GRID (`time-grok` F5), часы серий (F7),
  31 из 35 позиций до расчёта (отчёт, п. 4).
- Цена: Dota 691 → 227 карт (+ до 14 из п. 5), LoL 1,184 → 244. CI станет шире. Остаются только карты
  с живым архивом, это около 5 карт Dota в день.

## Порядок

1. Ждать п. 6 в `main` и его `--validation` прогон. Это база для всех шагов ниже.
2. Три коммита, по одному на фикс: п. 4, потом п. 5, потом задержка. После каждого — один прогон.
3. Так каждое изменение видно отдельно. `schedule` детерминирован, поэтому хватает одного seed.

## Шаг 1. П. 4: убрать `grid-v1`

1. `src/backtest/feed_schedules.py`: удалить `GridV1Plan`. `MatchFeedPlan` = `SchedulePlan`.
   `_resolve_dota_match` и `_resolve_lol_match` для карты без архива возвращают исключение `no_archive`.
2. `src/backtest/signals.py`: удалить путь `grid-v1`. Это `CadenceBand`, `SignalTiming`, `LIVE_GRID_TIMING`,
   `mean_interval_seconds`, `grid_v1_unit_interval`, `grid_v1_scoreboard_delay_seconds`,
   `cadence_bands_for_game`, `select_cadence_rows`, `_priced_decision_rows`, `_grid_v1_kill_gates`,
   `_shifted_feed`, `build_match_signals`. Перед удалением проверить grep, что `schedule` их не зовёт.
   `resolve_plan_dataset_path` отдаёт только game features.
3. `src/backtest/run.py`: убрать ветки `isinstance(plan, SchedulePlan)` (строки 427, 464, 757, 1682).
   Удалить ветку `observed_clock = None` (493-507) и чтение пауз, если его больше никто не читает.
4. `src/backtest/strategy.py:1036-1043`: `observed_clock` всегда есть, ветку без ленты удалить.
5. `src/backtest/replay_inputs.py`, `src/backtest/results.py`: убрать `grid_v1` из `kind` и `signal_mode`.
6. Smoke-набор `tests/fixtures/follow300_changes/smoke/`: 5 карт `grid_v1` заменить на карты `schedule`
   и перезаписать goldens (`scripts/capture_follow300_smoke.py`).
7. Seed-обвязку не трогаем: `--signal-cadence-seed`, каталоги `seedN`, `scripts/run_seeds.sh`. Это около
   15 файлов и фикстуры. Флаг станет пустым. Удаление — в п. 32.
8. `docs/rebuild-order.md`, шаги 6–7: один прогон `SEEDS=1`, `promote-backtest ARGS="--seeds 1"`.
   В `docs/next_steps.md:250` отметить, что сделано.

Проверка шага: прогон `--validation` Dota и LoL. Результат по каждой карте `schedule` (`engine_pnl`,
`buy_fills`, `buy_quantity`) равен базе. В `coverage` карты без архива ушли в `no_archive`.

## Шаг 2. П. 5: убрать фильтр по дыре стакана

1. `src/prepare_dataset/prepare_dataset.py`: удалить `find_longest_book_gap` (111-120), `gap_excluded`
   (333, 338-339) и поле `backtest_book_gap_excluded` (248).
2. `src/shared/constants/dataset.py:10-12`: удалить `MAX_BOOK_GAP_SECONDS` и комментарий.
3. `src/backtest/selection.py`: удалить `book_gap_excluded_match_ids`, проверку колонки (53-68), гейт (100)
   и счётчик в `ValidationCoverage`. Тот же счётчик — в `src/backtest/run.py:661` и `src/backtest/lol_inputs.py:106`.
4. `src/shared/types/dataset.py:96`: удалить поле из типа строки split.
5. Пересборка данных не нужна. Колонка в текущем `split.parquet` лишняя, её никто не читает.
   Следующий `make prepare` и `make train` её не запишут.

Проверка шага: прогон `--validation` Dota. 227 карт шага 1 не изменились. Добавились до 14 карт.
На картах с `missing_quote` (`8974786650`, `9007061808`) ядро в дыре стоит с `book_stale`, реплей не падает.

## Шаг 3. Задержка

1. `src/shared/constants/strategy.py:17-18`: вместо `NETWORK_LATENCY_MS` две константы:
   ```python
   # p50 live POST / DELETE round trip, 28 Dota sessions after 24.09 (audit fixes/scripts/latency_hops.py).
   # ponytail: one number per op; p99 (448 / 359 ms) and wedged cancels are not modeled.
   ORDER_INSERT_LATENCY_MS = 175.0
   ORDER_CANCEL_LATENCY_MS = 60.0
   ```
2. `build_order_latency` (`src/backtest/run.py:264-280`): `insert_latency_ms` и `update_latency_ms` —
   вставка; `cancel_latency_ms` модели остаётся 0; отложенная отмена стратегии — `ORDER_CANCEL_LATENCY_MS`.
   В docstring записать результат проверки `secondsDelay`.
3. Манифест (`src/backtest/run.py:727`): вместо `network_latency_ms` — `insert_latency_ms` и `cancel_latency_ms`.
   Перед правкой grep ключа в `scripts/` и `src/backtest/inspect/`.
4. `src/backtest/postprocess.py:67-68`: текст допущения — новые числа, `secondsDelay` проверен и не применяется.
5. LoL берёт те же числа: та же биржа и тот же хост.

Проверка шага:
1. В `quote_events` бэктеста `accepted` − `submitted` = 175 мс, `canceled` − `cancel_request` = 60 мс.
2. Прогон `--validation` Dota и LoL. Таблица против шага 2: исполнения, оборот BUY, $ при клипе $100.
3. `../scripts/parity_per_clip.py` на новом прогоне: отношение покупок лайв/бэктест (было 2.04).
4. Проверка допущения про `debounce`: на когорте 28 сессий сравнить p50 «сигнал → `submitted`» в бэктесте
   и «`SignalUpdate` → `places`» в лайве. Если разница больше 50 мс, прибавить её к вставке.

## Шаг 4. Тесты

1. `tests/test_backtest.py:153-162`: новые числа.
2. Тесты гонки исполнения и отмены (`tests/test_backtest_maker.py`) завязаны на 85 мс — поправить времена.
3. Тесты `grid_v1` удалить: `tests/test_backtest_signals.py`, `tests/test_feed_schedules.py`,
   `tests/test_backtest_validation.py`, `tests/test_lol_backtest.py` и другие по grep `grid_v1|GridV1Plan`.
4. Тесты фильтра дыры удалить: grep `book_gap`.
5. `make test` до и после. Предсуществующие фейлы (golden drift `test_follow300_replay`, `ok_quote_fraction`)
   сверить с чистым `main`.

## Шаг 5. После проверки

1. Последний прогон шага 3 — новый LIVE для Dota и LoL (`make promote-backtest`).
2. `../PROGRESS.md`: строки 4, 5 и заметка у строки 1.
3. План 01: части 4 и 5 идут по новому LIVE, только `schedule`.

## Не входит

- Seed-обвязка и каталоги `seedN` — п. 32.
- Дыры данных Telonex (`missing_quote`) — п. 29.
- Задержка по участкам внутри лайва (fsync, sqlite, `debounce` форка) — это ускорение лайва, не бэктеста.
- Реплей живых заявок через движок — план 01, часть 2. Константы задержки ему не нужны.
- Лаг Oddin 15 с (п. 14).

## Риски

- Без `grid_v1` бэктест меньше в 3 раза для Dota и почти в 5 раз для LoL. Решения по правилам станут
  шумнее. Карты `schedule` прибавляются примерно по 5 в день.
- Вставка 175 мс — это круг, а не путь в одну сторону. Если путь книги к нам короче пути заявки к бирже,
  константа пессимистична. Проверка шага 3.4 и часть 3 плана 01 (класс «время») это покажут.
- Возврат карт с долгим `wide_spread` добавит карт без сделок. Это честно: лайв на них тоже стоит.

## Готово, когда

- [ ] Шаг 1: отложен. `grid_v1` остаётся, пока пользователь не скажет убрать.
- [ ] Шаг 2: фильтр удалён в коде. Осталось: `--validation`, 227 карт `schedule` не изменились,
  возвращённые карты прошли реплей, коммит в `main`.
- [ ] Шаг 3: вставка 175 мс и отмена 60 мс стоят в коде. Осталось: таблица «до/после»
  в `../PROGRESS.md` после `--validation`, коммит в `main`.
- [ ] Шаг 5: новый LIVE для Dota и LoL.
