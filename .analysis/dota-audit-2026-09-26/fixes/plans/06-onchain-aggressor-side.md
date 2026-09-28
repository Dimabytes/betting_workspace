# План фикса п. 6: сторона агрессора onchain-сделок

Пункт отчёта: `../../REPORT.md`, S2, п. 6. Связан с планом 01 (части 2 и 4).
Источники: `../../reports/fill-grok-queue.md` Q3, `../../work/orchestrator/findings.md` (fill-devin),
`../../work/orchestrator/aggressor_check.py`, `../../work/fill-devin/aggressor_truth.py`.
Все пути кода ниже — в `esports-trader`, если не сказано иное.

## Решения (2026-09-28, с пользователем)

1. Фреймворк не меняем. Чекаут `../prediction-market-backtesting` чистый: `HEAD` = `c76e77a`, это upstream
   (merge PR #147). Отчёт ссылается на `04bc79fb`, на `HEAD` конвертер тот же.
2. Чиним в нашем мосте к фреймворку, `src/backtest/telonex_local.py`.
3. Флага нет. Фикс включён всегда. «До» — LIVE rebuild2, «после» — новый прогон.
4. Проверка: unit-тест, проверка mid, спот-бэктесты Dota и LoL, один `--validation` с seed 0.
5. Issue в upstream не пишем.

## Суть решения

`telonex_local.py` уже строит для фреймворка временное дерево файлов. Книги там переписываются
(`strip_own_book`), а `onchain_fills` подключаются симлинком. После фикса `onchain_fills` тоже
переписываются: в копию дневного файла добавляется колонка `side`.

- `side` = `taker_side`, если `taker_asset_id == asset_id`.
- Иначе `side` = противоположная сторона.

Фреймворк берёт первую найденную колонку из `side`, `taker_side`, `aggressor_side`, `trader_side`
(`telonex.py:3253-3254`). Колонку `side` он прочитает без правки своего кода. Сырые файлы не меняются.

Почему новая колонка, а не разворот `taker_side` в копии:
- `taker_side` в копии остаётся как в источнике. Наши скрипты анализа читают его и сами применяют правило.
- Если upstream починит загрузчик по `taker_asset_id`, развёрнутый `taker_side` развернётся второй раз.
  С колонкой `side` этого не будет, пока фреймворк читает `side` первой.

## Как это работает сейчас

1. Telonex кладёт одну сделку в файлы обоих токенов. Цена в файле — цена токена этого файла.
   `taker_side` и `maker_side` одинаковые в обоих файлах. На `8982107035` (04.09) это верно для
   1005 из 1005 пар строк: сумма цен = 1, `taker_side` совпадает.
2. Формат одинаковый у платного Telonex (20.07 `8905035315`, 05.08 `8930200713`) и у нашего коллектора
   (04.09 `8982015595`). Значит, коллектор пишет формат Telonex верно. Ошибка в чтении формата.
3. `_onchain_fill_trade_ticks_from_frame` (`telonex.py:3220-3293`) передаёт строку стороны в
   `telonex_aggressor_side` (`crates/core/src/telonex.rs:886-891`). Там `buy` → BUYER, `sell` → SELLER.
   `taker_asset_id` фреймворк не читает.
4. Локальный дневной файл фреймворк читает целиком, `pd.read_parquet(path)` (`telonex.py:2029`).
   Лишняя колонка доходит до загрузчика.
5. `link_market_tokens` (`telonex_local.py:144-150`) подключает папку `onchain_fills` токена симлинком целиком.
6. Фреймворк кэширует trade ticks по пути `<cache>/trade-ticks-v1/polymarket/onchain_fills/<slug>`,
   а не по содержимому. `clear_telonex_cache_for_markets` чистит только `book-deltas-v1`
   (`TELONEX_CACHE_VERSIONS`, `telonex_local.py:158`).

## Правило (проверено 2026-09-28)

Файл токена 0 карты `8982107035`, день 2026-09-04, 1005 строк:

| мейкер торгует | тейкер торгует | `mirrored` | строк | что это | агрессор на книге файла |
|---|---|---|---:|---|---|
| токен файла | второй токен | False | 402 | mint (buy/buy 391) или merge (sell/sell 11): тейкер бьёт в заявку мейкера на этой книге | против `taker_side` |
| токен файла | токен файла | False | 75 | прямая сделка на этой книге | `taker_side` |
| второй токен | токен файла | True | 333 | тейкер торгует токеном файла против заявки второго токена | `taker_side` |
| второй токен | второй токен | True | 195 | прямая сделка на книге второго токена, зеркало с ценой 1 − p | против `taker_side` |

Разворот нужен у 597 строк из 1005 (59.4%). Во всех четырёх классах работает одно правило:
разворот, если `taker_asset_id != asset_id`. Проверка mid из аудита с этим согласна. На строках с
разворотом mid идёт против `taker_side`: buy → вниз 267, вверх 69. На остальных mid идёт по `taker_side`:
buy → вверх 239, вниз 28.

Скан всех дней, которые трогает каталог: 3,291 карта, 6,814 дневных файлов, 4,637,762 строки.
- `taker_side` бывает только `buy` (3,788,446) и `sell` (849,316).
- Колонки `side` нет ни в одном файле.
- `asset_id` везде равен токену папки. Пустых `taker_asset_id` нет.
- Разворот нужен у 2,318,881 строки, ровно 50.0%. Каждая сделка лежит в двух файлах.
  Ровно в одном из них `taker_asset_id == asset_id`.

## Стоимость (замер 2026-09-28)

1. Переписываем только дни окна реплея, как книги. На весь каталог это 6,814 файлов, 735 MB.
   Весь `onchain_fills` на диске — 1.9 GB.
2. Запись 20 самых больших файлов (22.4 MB) заняла 0.21 с. Весь каталог — около 7 с.
3. Копия пишется один раз в `~/.cache/esports-trader/telonex-tree`. Сейчас там 14 GB книг.
   Следующие прогоны только ставят симлинк на готовую копию.
4. Копия пишется заново в двух случаях: сменился исходный файл (размер или mtime) или сменился код правила.
5. Место на диске: плюс не больше 0.75 GB.
6. Первый прогон после фикса вычищает `trade-ticks-v1` (сейчас 311 MB). Фреймворк строит его заново
   один раз. Время этой пересборки я не мерил.
7. Кэш книг фреймворка `book-deltas-v1` (12 GB) фикс трогать не должен. Поэтому чистка идёт по каналу
   (шаг 1, п. 4). Иначе первый прогон пересоберёт все книги.
8. `TELONEX_TREE_CACHE` нигде не выключен. Если его выключить, копии пишутся на каждом прогоне.

## Шаг 1. Код

1. Новый модуль `src/backtest/onchain_side.py`, рядом с `strip_own_book.py`. Одна функция:
   ```python
   def write_aggressor_side(*, source_path: Path, out_path: Path) -> None:
       table = pq.read_table(source_path)
       taker_side = table["taker_side"].cast(pa.string())
       unknown = set(pc.unique(taker_side).to_pylist()) - {"buy", "sell"}
       if unknown:
           raise ValueError(f"{source_path}: unexpected taker_side {sorted(unknown)}")
       own_token = pc.equal(
           table["taker_asset_id"].cast(pa.string()), table["asset_id"].cast(pa.string())
       )
       opposite = pc.if_else(pc.equal(taker_side, "buy"), "sell", "buy")
       side = pc.if_else(own_token, taker_side, opposite)
       pq.write_table(table.append_column("side", side), out_path)
   ```
   Отдельный модуль нужен для ключа кэша: хэш модуля меняется только при смене правила.
   Docstring модуля: формат Telonex и ссылка на `telonex.py` `_onchain_fill_trade_ticks_from_frame`.
2. `link_market_tokens`: вместо `_symlink_channel_dir(onchain_outcome, onchain_dir)` цикл по `days`,
   как у книг. Для каждого дня с файлом вызвать `_link_cached_day`. Ключ: `"onchain-side"`, хэш
   `onchain_side`, `_file_stamp(source)`. Так для всех карт, не только для карт с архивом.
3. `link_market_tokens` возвращает, какой канал пересобран: frozen dataclass `RebuiltChannels`
   с полями `books: bool` и `onchain: bool`. `create_telonex_source_tree` собирает два набора slug.
4. Чистка кэша фреймворка по каналу. `book_snapshot_full` → `book-deltas-v1`, `onchain_fills` → `trade-ticks-v1`.
   `clear_telonex_cache_for_markets` получает канал и удаляет только `<root>/<version>/polymarket/<channel>/<slug>`.
   `TELONEX_CACHE_VERSIONS` заменить этой парой.
5. `_rewrite_code_stamp` сделать общим: хэш файла данного модуля. Вызвать для `strip_own_book` и `onchain_side`.
6. Обновить docstring модуля `telonex_local.py` («Onchain fills stay symlinked») и комментарий
   у `TELONEX_CACHE_VERSIONS`.

Датасеты и `make prepare` фикс не трогает. Он действует только в момент прогона бэктеста.
LoL идёт через тот же `create_telonex_source_tree`, фикс его покрывает.

## Шаг 2. Тесты

1. Новый тест `write_aggressor_side`. Четыре строки, по одной на класс из таблицы правила.
   Ожидание: `side` = разворот, как есть, как есть, разворот. `taker_side` не изменился.
   Отдельно: `taker_side = "x"` → `ValueError`.
2. Обновить тесты в `tests/test_backtest_validation.py`:
   - `test_telonex_tree_links_both_tokens_where_the_framework_globs`: папка `onchain_fills/.../outcome_id=0`
     больше не симлинк. Дневной файл в ней есть и содержит колонку `side`.
   - `write_valid_capture_day`: строка onchain с `asset_id`, `taker_asset_id`, `taker_side`.
   - `test_telonex_tree_cache_skips_archive_parse_on_hit`: проверять поля `RebuiltChannels`.
   - `test_cache_eviction_keeps_markets_we_did_not_rewrite`: чистка канала `onchain_fills` не трогает
     `book-deltas-v1`, чистка книг не трогает `trade-ticks-v1`.
3. Таргетные тесты, потом `make test`. Не запускать во время лайв-карты.

## Шаг 3. Проверка на данных

1. Через кэш фреймворка. После спот-бэктеста `8982107035` прочитать
   `~/.cache/nautilus_trader/telonex/trade-ticks-v1/polymarket/onchain_fills/dota2-spirit1-ks-2026-09-04-game2/`.
   Сторона агрессора тиков должна совпасть с правилом на всех строках окна. Это проверяет, что
   фреймворк читает именно `side`.
2. Проверка mid: `../../work/orchestrator/aggressor_check.py` со стороной из `side`, а не `taker_side`.
   Ожидание: на обеих группах buy → mid вверх в большинстве (до фикса на группе с разворотом buy → вниз 267, вверх 69).
3. Вторая сверка: канал `trades/` уже пишет сторону по токену файла (`fill-grok-queue` Q3).
   `../../work/fill-devin/aggressor_truth.py`: `side` совпадает со стороной `trades/` на сопоставленных сделках.
4. Спот-бэктесты, те же карты, что у п. 2 и п. 10:
   - Dota `--match-id 9003856182` (до фикса 1 buy / 1 sell, +$0.05);
   - Dota `--match-id 8982107035`;
   - LoL `--match-id 117030752644841608` (до фикса 10 buy / 8 sell, −$8.58).
   Записать buy, sell и pnl до и после. Ожидание: исполнений BUY не меньше, чем до фикса.
5. Один `--validation` прогон, `--signal-cadence-seed 0`, `--name aggressor-side`. Сравнить с seed 0
   LIVE rebuild2 (`validation_join_delta02_x015_cut480_p4_rebuild2-20260927`):
   - $, число исполнений BUY и SELL, оборот;
   - отдельно `schedule` (было $1,739.37) и `grid_v1`.
6. `parity_per_clip.py <новый run dir>`. До фикса: лайв покупает в 2.04 раза больше, бэктест +$1,354.60
   против лайва +$1,310.45 на 90 картах. Записать новые цифры.

Критерий: шаги 3.1–3.3 совпадают с правилом. Знак влияния на $ и оборот записан в `../PROGRESS.md`.

## Шаг 4. После проверки

1. Коммит в `esports-trader` только после разрешения пользователя.
2. Делать ли новый прогон LIVE, решает пользователь.
3. В плане 01 вариант «п. 6» — это сравнение прогона шага 3.5 с LIVE rebuild2. Флаг не нужен.

## Не входит

- Код фреймворка и issue в upstream.
- Коллектор. Он пишет формат Telonex верно.
- Канал `trades/`. Там сторона уже по токену файла. Дерево его не подключает.
- Лайв. Он берёт исполнения из WS (`src/trader/fill_parsing.py`), не из `onchain_fills`.
- Наши скрипты анализа, которые читают `taker_side` (`docs/experiments/ghost-fill-audit/build.py`,
  `../../work/fill-grok/honest_queue.py`). Они применяют правило сами или остаются экспериментом.

## Риски

- Фикс держится на порядке колонок в `telonex.py:3253-3254`. При обновлении фреймворка проверить
  это место и повторить шаг 3.1.
- Если upstream сам начнёт разворачивать сторону по `taker_asset_id`, наша колонка `side` станет лишней.
  Тогда удалить `onchain_side.py` и вернуть симлинк.

## Готово, когда

- [ ] Шаг 1: код в `main` `esports-trader`.
- [ ] Шаг 2: новые и обновлённые тесты зелёные, `make test` без новых фейлов.
- [ ] Шаг 3.1–3.3: сторона тиков совпадает с правилом.
- [ ] Шаг 3.4: спот-бэктесты записаны.
- [ ] Шаг 3.5–3.6: таблица «до / после» записана в `../PROGRESS.md`.
