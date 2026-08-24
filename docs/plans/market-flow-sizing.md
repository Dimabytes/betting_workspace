# Фильтр мёртвых рынков по потоку тейкеров

## Зачем

Размер входа — одно плоское число. `_entry_quotes` берёт
`profile.base_size_usdc` из `config/dota-map.toml` и превращает его в
`round(base / price, 2)` шейров на любом рынке. Путь входа не читает ни оборот,
ни глубину, ни размеры из стакана, и игнорирует `inp.risk_size_scale`.

На мёртвом рынке это даёт два исхода. 24 августа матч `8962372120` получил 16
заявок по `$700` и не исполнил ничего. Матч `8962592819` при обороте рынка `$561`
за всё окно получил одно исполнение на 87 шейров из 769 запрошенных одним
ордером, то есть 11%: ограничителем был контрагент, а не наш размер.

Ставка потока тейкеров (окно 1200 секунд, доллары в минуту): `$164`–`$639` на
рынках 24 августа против `$3,243`–`$42,219` на матчах 20–23 августа. Разница до
250 раз, и уровень турнира её не объясняет — все три матча 24 августа шли в одном
турнире, league `19944`, `EPL Masters 2 Play-In`, а ставки на них различались в
несколько раз.

Размер ещё и расходится между live и backtest: live читает `base_size_usdc` из
TOML, backtest читает `BASE_SIZE_USDC` из `src/shared/constants/strategy.py`.

**Задача — фильтр мёртвых рынков и ничего больше.** Правило не предсказывает
долю исполнения и не пытается. Замер на 46 клипах: корреляция потока с долей
исполнения `0.12` на окне 60 секунд и `-0.12` на окне 600 секунд, то есть шум.
Долю исполнения задаёт потолок клипа, а не поток.

## Правило

```
rate_per_min = taker_notional_in_window / seconds_covered * 60
clip         = LIQ_FLOW_MULT_PER_MIN * rate_per_min
clip         = min(clip, MAX_CLIP_USDC)
if rate неизвестна или clip < MIN_CLIP_USDC:  EntryBlock.THIN_MARKET
```

- `LIQ_FLOW_WINDOW_SECONDS = 1200`, `LIQ_FLOW_MULT_PER_MIN = 0.05`,
  `MIN_CLIP_USDC = 10.0`, `MAX_CLIP_USDC = 200.0`.
- Пол `$10` — это ставка `$200`/мин. Потолок `$200` — `$4,000`/мин.
- Только вход. Выход остаётся как есть: один SELL всей позиции по `ceil(ask)`,
  `share_floor` до двух знаков, unwind через 300 секунд
  (`session_quoting.py:157-184`).
- Проверка на архиве: на окне 1200 клип внутри матча плоский, переключений
  skip/торгуем ноль на всех проверенных матчах.

Замер по 61 тику на матч (секунды -60..540, шаг 10), окно 1200, `mult = 0.05`:

| Матч | ставка мин, $/мин | клип мин | skip при поле `$10` | при `$25` | при `$50` |
|---|---|---|---|---|---|
| 8962592819 (24 авг) | `188` | `$9` | 19/61 | 61/61 | 61/61 |
| 8962372120 (24 авг) | `164` | `$8` | 2/61 | 59/61 | 61/61 |
| 8962736746 (24 авг) | `351` | `$18` | 0/61 | 61/61 | 61/61 |
| 8962458373 (24 авг) | `601` | `$30` | 0/61 | 0/61 | 61/61 |
| 8956780728 (20 авг) | `3,243` | `$162` | 0/61 | 0/61 | 0/61 |
| 8958607830 (22 авг) | `4,966` | `$248` | 0/61 | 0/61 | 0/61 |
| 8956821666 (20 авг) | `5,358` | `$268` | 0/61 | 0/61 | 0/61 |
| 8955934230 (20 авг) | `6,827` | `$341` | 0/61 | 0/61 | 0/61 |

Пол — единственная ручка. Живые матчи её не касаются ни при одном значении из
таблицы: их минимальный клип `$162`. Значение `$10` означает, что мёртвые рынки
мы торгуем клипами `$8`–`$32`, а пропускаем только то, что ниже `$200`/мин.

Уменьшение клипа на мёртвом рынке почти не уменьшает нашу экспозицию: исполнение
там ограничено контрагентом, а не нашим ордером (замер выше: 87 шейров из 769
запрошенных). Значит пол определяет, сколько рынков мы трогаем, а не сколько
берём на каждом. Решение по полу примем по замеру центов на шейр в разрезе
`flow_rate_per_min`, который пишет журнал.

### Потолок

Потолок задаёт, регулятор это или выключатель. Замер медианного клипа:

| Группа | ставка p50, $/мин | клип при cap 200 | клип при cap 700 |
|---|---|---|---|
| 24 авг, четыре матча | `296`–`639` | `$15`–`$32` | `$15`–`$32` |
| 20–22 авг | `7,482`–`16,241` | `$200` | `$374`–`$700` |
| TI 22–23 авг | `19,993`–`42,219` | `$200` | `$700` |

При `$200` всё живое стоит на потолке. При `$700` средние матчи получают
`$374`–`$554`, TI — полные `$700`. Бэктест в шаге 8 прогнать на обоих потолках, а
не только с правилом и без.

## Источник данных

Один GET на рынок, без авторизации и без пагинации:

```
GET https://data-api.polymarket.com/trades?market=<conditionId>&limit=500
```

Проверено с этой машины:

- Одна строка на одну сделку. 462 строки — 462 уникальных `transactionHash`, ни
  одного дубля, поэтому сумма `price * size` даёт честные доллары. Зеркал, как в
  `parquet/trades`, здесь нет.
- `limit` упирается в 500. На тонком рынке 462 строки покрыли всю его жизнь за
  4.2 часа. На рынке масштаба TI 500 строк покрыли 1,428 секунд и `$275,888`.
- Работает и на закрытых рынках.
- Фильтра по времени нет: параметр `after` игнорируется. Режем на своей стороне
  по полю `timestamp` (секунды). Резать только нижнюю границу: часы Data API
  могут идти впереди локальных, и строки с `ts > now` терялись бы зря.
- `takerOnly` по умолчанию `true`, и это нужное поведение. С `takerOnly=false`
  добавляются мейкерские строки и страница уходит в прошлое меньше.
- Нужные поля строки: `timestamp`, `price`, `size`, `asset`, `conditionId`.
- Хост `data-api.polymarket.com` — третий, помимо Gamma и CLOB. `AGENTS.md`
  предупреждает про VPN для Gamma и CLOB; этот отвечает с VPS напрямую.
  Константа `POLYMARKET_DATA_API` уже есть в `src/shared/constants/api.py:5`.

Плюс этого источника: зависимости от attach, от Steam и от драфта нет.
`conditionId` известен из sidecar за часы до матча, поэтому окно всегда полное,
включая первый тик после рестарта в середине матча.

Лимиты запросов неизвестны. Кеш на 60 секунд по `conditionId`, живых рынков 1–3,
значит 1–3 запроса в минуту.

## Факты о коде, которые нужны при реализации

- `EntryGateInputs` и `evaluate_entry` — `src/live_paper/session_quoting.py:38`
  и `:85`. Размер считает `_entry_quotes` на `:187-199` через
  `buy_share_quantity(base_size_usdc=inp.profile.base_size_usdc, price=price)`.
- `StrategyState` (`src/live_paper/session_types.py:52-58`) — единственный канал
  от решения к квотингу. Сегодня в нём нет размера.
- `_compute_decision` (`src/live_paper/match_worker.py:291-383`) синхронная,
  вызывается из `_on_event`. Сетевого вызова внутри быть не должно.
- Образец фоновой задачи: `_maybe_start_prior` / `_load_prior`
  (`match_worker.py:385-409`) плюс `asyncio.to_thread`. Отмена задачи — рядом с
  `self._prior_task.cancel()` на `:174-176`.
- `refresh_sidecar` (`match_worker.py:582`) хост вызывает раз в 60 секунд на
  каждый прикреплённый CID, но её отказ уходит в permanent safe mode. Поток
  туда не вешать.
- `StrategyProfile` в poly-maker объявлен с `extra="forbid"`, а
  `_TEMPLATE_SCHEMA` (`src/live_paper/session_config.py:85-133`) требует точный
  набор ключей. Новые ключи в `[profiles.dota-map]` не добавлять.
- `PaperGateway` исполняет весь ордер по его лимиту
  (`src/live_paper/paper_gateway.py:231-239`), поэтому paper не покажет эффект
  от выбора размера. Backtest покажет: `src/backtest/run.py:428` задаёт
  `ExecutionModelConfig(queue_position=True, ...)` с частичными исполнениями.
- Переиспользовать: `shared.utils.common.http_client`,
  `shared.utils.log.suppress_http_url_logging` (образец —
  `src/live_paper/market_prior.py`), `shared.utils.strict_json`,
  `shared.utils.trading.buy_share_quantity`.

## Что сделать

### 1. Новый модуль `src/live_paper/market_flow.py`

Образец по структуре и по стилю — `src/live_paper/market_prior.py`.

- `FlowRate` — frozen dataclass: `rate_per_min: float`, `seconds_covered: float`,
  `prints: int`.
- `fetch_flow_rate(condition_id, now_unix) -> FlowRate | None` — один GET, разбор
  через `strict_json`, затем эта логика:

  ```
  if not rows:                       # рынок не торговался
      rate = 0.0
  else:
      in_window = [r for r in rows if r.ts >= now_unix - LIQ_FLOW_WINDOW_SECONDS]
      notional  = sum(r.price * r.size for r in in_window)
      if len(rows) < LIQ_FLOW_PAGE_LIMIT:
          covered = LIQ_FLOW_WINDOW_SECONDS
      else:
          covered = max(1.0, min(LIQ_FLOW_WINDOW_SECONDS,
                                 now_unix - min(r.ts for r in rows)))
      rate = notional / covered * 60
  ```

  Различение идёт **по числу строк, а не по времени**. Короткая страница значит,
  что раньше сделок не было, поэтому окно накрыто целиком и делить надо на все
  1200 секунд. Иначе тонкий рынок с одной вспышкой выглядел бы живым. Полная
  страница из 500 строк значит, что нас могли обрезать, и тогда делим на реально
  накрытый отрезок. Обрезка требует больше 25 принтов в минуту; TI дал 500 строк
  за 1,428 секунд, то есть 21 в минуту, и ещё влезает. На рынке живее TI ставка
  выходит завышенной, но такой рынок и так стоит на потолке.

  Порога `min_covered_seconds` **нет специально**: сработать он мог бы только на
  полной странице короче минуты, то есть на рынке с 500+ принтами в минуту — на
  самом ликвидном из возможных, где блокировать вход абсурдно. Вместо порога —
  `max(1.0, ...)`, и пусть ставка уходит в потолок.

  Возврат `None` только на отказе HTTP или разбора. Функция не бросает.
- `choose_clip_usdc(rate_per_min) -> ClipDecision` — чистая функция правила,
  несёт `ClipReason` (`floor`, `flow`, `cap`), чтобы журнал говорил, какая
  граница сработала.

### 2. Константы в `src/shared/constants/strategy.py`

Одно определение для live и backtest. `BASE_SIZE_USDC` удалить.

```
MIN_CLIP_USDC            = 10.0
MAX_CLIP_USDC            = 200.0
LIQ_FLOW_WINDOW_SECONDS  = 1200
LIQ_FLOW_MULT_PER_MIN    = 0.05
LIQ_FLOW_REFRESH_SECONDS = 60.0
LIQ_FLOW_MAX_AGE_SECONDS = 180.0
LIQ_FLOW_PAGE_LIMIT      = 500
```

`src/backtest/run.py:123`: `CASH_PER_MATCH = MAX_CLIP_USDC`. Строки `339` и `499`
перестают подставлять `BASE_SIZE_USDC` в профиль.

### 3. Кеш и обновление в `match_worker.py`

- Атрибуты `self._flow: FlowRate | None = None` и
  `self._flow_at: float | None = None` (monotonic момент удачной выборки).
- Задача `self._flow_task`, запускается при attach рядом с `_prior_task`. Это
  **цикл**, а не одноразовая задача: каждые `LIQ_FLOW_REFRESH_SECONDS` делает
  `await asyncio.to_thread(fetch_flow_rate, cid, time.time())`, при непустом
  результате пишет `_flow` и `_flow_at`, при `None` логирует тип и оставляет
  прежнее значение. Отказ уходит в следующий цикл сам, поэтому баг prior
  (см. шаг 5) здесь не воспроизводится по построению. Отмена — рядом с
  `self._prior_task.cancel()` на `:174-176`.
- `_compute_decision` только читает атрибуты, без await и без I/O. Если
  `_flow` пустой или `monotonic() - _flow_at > LIQ_FLOW_MAX_AGE_SECONDS`, ставка
  считается неизвестной и вход блокируется как `THIN_MARKET`.

### 4. Проводка

- `session_types.py`: `StrategyState` получает `entry_size_usdc: float`,
  `EntryBlock` получает `THIN_MARKET`.
- `session_quoting.py`: `EntryGateInputs` получает `flow_rate_per_min: float`
  (отрицательное или NaN означает «неизвестна»). `evaluate_entry` вызывает
  `choose_clip_usdc` после существующих гейтов и кладёт клип в `EntryDecision`.
  `_entry_quotes` читает `state.entry_size_usdc` и больше не смотрит на
  `inp.profile`.
- `match_worker.py`: `_on_event` пишет `entry_size_usdc` в опубликованный
  `StrategyState`.
- `session_journal.py`: `schema_version` до 6. Запись `signal` получает
  `ts_utc`, `flow_rate_per_min`, `flow_seconds_covered`, `flow_age_seconds`,
  `clip_usdc`, `clip_reason`. Запись `quote` получает `ts_utc`. `first_start`
  принимает 1–6, resume схемы 1–5 не перезаписывает версию.
- `src/backtest/strategy.py`: то же правило в `_choose_buy_target`, ставка по
  ленте трейдов за то же окно, те же константы. Второй копии чисел не делать.

### 5. Починка retry для prior

`_load_prior` (`match_worker.py:394-409`) пишет `self._prior = prior` даже когда
`fetch_market_prior` возвращает `None`, а `_prior_task` чистит только в ветке
`except`. После этого `_maybe_start_prior` (`:387`) видит `_prior_task is not
None` и не повторяет попытку никогда. Одна мягкая ошибка (`pair_broken`,
`missing_quote`) убивает торговлю на весь матч: матч `8962458373` записал
`pair_broken` один раз, затем 958 тиков `missing_prior` и ноль quote.

Чистить `_prior_task` при результате `None`, чтобы следующее событие повторило
попытку.

### 6. `config/dota-map.toml`

Ключ `base_size_usdc` оставить: его требуют `StrategyProfile` и
`_TEMPLATE_SCHEMA`. Наш путь входа его больше не читает.

- Поставить `base_size_usdc = 200.0`, равным `MAX_CLIP_USDC`. Детектор свипа в
  fork (`engine.py:290`) масштабируется от него.
- Комментарий: ключ нужен только fork, размер клипа задают `MIN_CLIP_USDC` и
  `MAX_CLIP_USDC`.
- `q_max_usdc` привести к новому масштабу.

### 7. Новый скрипт `scripts/replay_flow_filter.py`

Проверка перед включением, а не подгонка. Для каждого прошедшего матча считает
ставку по тому же окну и печатает, на каких тиках правило дало бы skip и какой
клип. Так видно, какие матчи мы потеряли бы.

Два требования.

- Поток брать из сырого журнала коллектора `events/<date>/<HH>.jsonl.gz`
  (`payload.type == "last_trade_price"`, фильтр по `payload.market`), а не через
  Data API: у него нет фильтра по времени и страница не дотянется до старых
  матчей. Не брать `parquet/trades` без фильтра
  `asset_id == origin_asset_id` — там принты зеркалированы в обе партиции
  токенов, и сумма `price * size` даёт шейры вместо долларов.
- Хорн выводить из `state.jsonl`: первый снапшот с `game_state == 5` и
  `game_time >= 0` даёт `received_at_utc - game_time`. **Поле `horn_at_utc` в
  `match.json` для этого не годится**: оно считается на первом событии, а сессия
  прикрепляется на драфте, где `game_time` — положительные часы драфта. Ошибка
  от 61 до 950 секунд на 52 матчах из 56.

### 8. Порядок выпуска

1. Выложить всё, но правило держать выключенным: клип и ставку писать в журнал,
   котировать по-прежнему одним числом. Плюс починку prior.
2. Прогнать `scripts/replay_flow_filter.py` и сверить с живым журналом за сутки.
3. Включить правило вторым `docker compose restart`.

### 9. Доки в том же изменении

- `docs/live-paper.md` строка ~634: лимиты указаны как `$100` вход, `$400` день,
  `$800` инвентарь. В `[risk]` стоят `daily_loss_kill_usdc = 2800`,
  `max_total_exposure_usdc = 5600`, `max_market_notional_usdc = 2800`,
  `max_event_group_loss_usdc = 2800`.
- `docs/live-paper.md` строка ~757: «both size $100». После изменения оба читают
  `MIN_CLIP_USDC` и `MAX_CLIP_USDC`.
- Секция «Size change» в `docs/live-paper.md` и в
  `betting_workspace/.shared-skills/vps-live-paper/SKILL.md` говорит менять
  `base_size_usdc`. Заменить на `MIN_CLIP_USDC` / `MAX_CLIP_USDC` и отметить, что
  `base_size_usdc` остался только для fork.
- Добавить: новые константы, поля схемы 6 журнала, Data API как третий хост,
  правило retry для prior, и то, что `match.json` `horn_at_utc` нельзя брать как
  якорь для оффлайн-анализа.

## Файлы

| Файл | Изменение |
|---|---|
| `src/live_paper/market_flow.py` | новый: `FlowRate`, `fetch_flow_rate`, `choose_clip_usdc` |
| `src/shared/constants/strategy.py` | `MIN_CLIP_USDC`, `MAX_CLIP_USDC`, `LIQ_*`; удалить `BASE_SIZE_USDC` |
| `src/live_paper/session_types.py` | `StrategyState.entry_size_usdc`, `EntryBlock.THIN_MARKET` |
| `src/live_paper/session_quoting.py` | `EntryGateInputs.flow_rate_per_min`, клип в `evaluate_entry`, `_entry_quotes` читает клип |
| `src/live_paper/match_worker.py` | `_flow_task` цикл, кеш, публикация клипа, починка retry для prior |
| `src/live_paper/session_journal.py` | схема 6, `ts_utc`, поля потока и клипа |
| `src/backtest/run.py` | `CASH_PER_MATCH = MAX_CLIP_USDC`, убрать подстановку `BASE_SIZE_USDC` |
| `src/backtest/strategy.py` | то же правило в `_choose_buy_target` |
| `config/dota-map.toml` | `base_size_usdc = 200.0` с комментарием, `q_max_usdc` под новый масштаб |
| `scripts/replay_flow_filter.py` | новый скрипт проверки |
| `docs/live-paper.md` | размеры, лимиты, новые поля, Data API, ловушка `horn_at_utc` |
| `betting_workspace/.shared-skills/vps-live-paper/SKILL.md` | секция «Size change» |
| `tests/test_live_paper_market_flow.py` | новый |
| `tests/test_live_paper_session_quoting.py` | случаи клипа и `THIN_MARKET` |
| `tests/test_live_paper_session_journal.py` | круг схемы 6, resume 1–5 |
| `tests/test_live_paper_match_lifecycle.py` | цикл `_flow_task`, устаревшая ставка, retry для prior |

## Проверка

1. `make lint-all` без замечаний. Сначала `git add` новых файлов, иначе хук их
   пропустит.
2. `uv run pytest`. Новые тесты:
   - `fetch_flow_rate` на замоканном httpx: нормальная страница, пустая
     страница, HTTP 500, битый JSON, страница шире окна (режем по нижней
     границе), строки с `ts > now` не теряются, короткая страница делится на
     полное окно, полная страница из 500 строк делится на накрытый отрезок,
     полная страница за одну секунду не блокирует вход. Реального сетевого
     запроса в тестах быть не должно.
   - `choose_clip_usdc`: блок по минимуму, зажим по максимуму, нулевая ставка,
     неизвестная ставка.
   - `_flow_task`: отказ не затирает прежнее значение и цикл повторяет попытку;
     ставка старше `LIQ_FLOW_MAX_AGE_SECONDS` блокирует вход.
   - круг схемы 6 журнала и resume 1–5, retry для prior.
3. `uv run python scripts/replay_flow_filter.py` — какие матчи и тики правило
   отсекло бы на истории.
4. Backtest, правило выключено против включённого. Пять шардов параллельно,
   затем merge:
   ```
   make backtest ARGS="--validation --shard N/5 --name <stamp>"   # N = 0..4
   make backtest ARGS="--validation --merge-shards 5 --name <stamp>"
   ```
   Сравнить PnL на шейр и markout в центах.
5. Live: `docker compose restart live-paper`. На следующем матче проверить, что
   `session.jsonl` несёт `ts_utc`, `flow_rate_per_min`, `clip_usdc`,
   `clip_reason`, и что `entry_block=thin_market` появляется на мёртвом рынке и
   не появляется на тир-1. Живой матч — `summarize.py --live`.

## Вне задачи

- Долю исполнения не предсказываем. Телеметрии по стакану (`queue_ahead`,
  глубина у касания) в этом изменении нет.
- Выход не менять.
- `poly-maker` не менять (заморожен).
- Поле `horn_at_utc` в `match.json` не переписывать. Это отдельный дефект;
  скрипт проверки обходит его своим якорем.
- Схему sidecar коллектора не менять: подъём `MarketSidecar` сломает
  `collector_sidecars.py:159-161`. Если сеть в горячем пути начнёт мешать,
  следующий шаг — новый файл `metadata/flow/<conditionId>.json` от коллектора,
  но это отдельная задача во втором репозитории.
- Модель, существующие гейты входа и лимиты `[risk]` не менять.
