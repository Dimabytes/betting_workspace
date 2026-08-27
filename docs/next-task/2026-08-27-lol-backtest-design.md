# LoL maker backtest

Дата: 2026-08-27

Статус: design согласован, implementation plan ещё не создан

## Зависимость от текущего LoL pipeline

Этот design начинается только после полного завершения
`current-task/feature.json`. К этому моменту Stages 01–06 и финальный прогон уже
создали:

- связанный и разрешённый LoL universe;
- raw lolesports windows до игровой секунды 1200;
- полный архив Telonex `book_snapshot_full`;
- `training.parquet`, `validation.parquet`, `production_training.parquet`,
  `split.parquet` и audits;
- LoL research и production модели.

Этот документ не изменяет `current-task/feature.json` и
`current-task/2026-08-27-data-training-design.md`. Вторая часть расширяет уже
готовый продуктовый код.

## Результат

LoL research model проходит честный maker backtest на зафиксированном validation
split. Тот же запуск сравнивает B0 и S2 на одинаковых картах. Production model
можно прогнать на этих картах только как smoke test.

Backtest переиспользует Dota strategy, Nautilus replay, queue fill, latency,
unwind, sharding, resume и отчёты. LoL получает только свой data preparation и
свой input loader.

Поток данных:

```text
завершённые LoL Stages 01–06
        |
повторный Stage 02: books + trades + onchain_fills
        |
Stage 07: signals 0..899 + market seconds 0..1200 + eligibility audit
        |
общий src/backtest/run.py --game lol
        |
data/backtests/lol_maker/
```

## Scope

В scope входят:

1. Расширение существующего LoL Stage 02 двумя Telonex execution channels.
2. Подготовка validation-карт для replay.
3. Один общий Dota и LoL backtest runner.
4. Paired B0 и S2 evaluation.
5. Удержание незакрытой позиции после игровой секунды 1200 до известной
   резолюции.
6. Общий Dota report с отдельными LoL cutoff и tail PnL полями.

Не входят:

- LoL live trading или live-paper;
- LoL collector;
- новая стратегия или подбор её параметров;
- загрузка полного игрового хвоста после секунды 1200;
- public Polymarket trade fallback;
- provider framework, factories или plugin API;
- отдельный LoL verdict, status или автоматический quality gate;
- изменение существующих Dota результатов и Dota defaults.

## Граница переиспользования

Общая часть уже существует в `../dota_2_model/src/backtest`.

| Компонент | Dota | LoL |
|---|---|---|
| `run.py` | общий runner, Dota по умолчанию | тот же runner с `--game lol` |
| `strategy.py` | текущая maker strategy | без копии и без LoL subclass |
| Nautilus replay | L2 books и execution ticks | тот же replay |
| B0 и S2 | текущие arms | те же arms и defaults |
| queue, latency, cancel, reprice, unwind | текущая реализация | та же реализация |
| selection и signals | текущие Dota loaders | один тонкий `lol_inputs.py` |
| postprocess и report | текущие artifacts и metrics | те же artifacts плюс cutoff и tail PnL |

`src/backtest/run.py` получает `--game dota|lol`. Значение по умолчанию —
`dota`, поэтому существующий `make backtest` не меняется. Runner выбирает одну
из двух функций загрузки входов обычной веткой. Общий provider interface или
registry не нужен.

LoL pipeline уже ориентирует Blue и Red как Radiant и Dire. Backtest сохраняет
существующие имена полей и не переименовывает общий strategy contract.

## Stage 02 получает полный backtest archive

### Один downloader вместо нового execution stage

Вторая часть расширяет `src/lol/02_fetch_telonex_books.py`. Новый downloader не
создаётся. Имя entrypoint остаётся прежним, чтобы не менять завершённый pipeline
и его команды.

Stage 02 обрабатывает три фиксированных канала:

```python
CHANNELS = ("book_snapshot_full", "trades", "onchain_fills")
```

Скрипт повторно читает полный Telonex catalog и строит jobs для обоих tokens
всех поддержанных linked markets. Он использует channel-specific catalog
intervals.

При повторном запуске Stage 02:

1. Проверяет footer, required columns и `asset_id` уже скачанных books.
2. Пропускает каждый готовый book parquet.
3. Сначала скачивает обязательный `trades` fallback.
4. Затем скачивает доступные `onchain_fills`.
5. Публикует обновлённый audit после обработки всех jobs.

Stage 02 сохраняет все provider parquets без преобразования:

```text
data/lol/raw/telonex/polymarket/
├── book_snapshot_full/asset_id=<token>/YYYY-MM-DD.parquet
├── trades/asset_id=<token>/YYYY-MM-DD.parquet
└── onchain_fills/asset_id=<token>/YYYY-MM-DD.parquet
```

Скрипт переиспользует текущие retry, bounded backoff, `.partial`, parquet
validation и atomic replace. Он не получает `--channels`: набор каналов теперь
часть backtest data contract, а не пользовательская настройка.

`download_audit.parquet` получает поле `channel`. Остальные идентификаторы и
статусы остаются совместимыми с готовым Stage 02. Повторный запуск перестраивает
audit из catalog и проверенного локального состояния, поэтому отдельная миграция
старого book-only audit не нужна.

### Каналы имеют разные роли

`book_snapshot_full` восстанавливает L2 book. Execution ticks двигают позицию
нашей LIMIT-заявки в очереди.

Loader выбирает один execution channel для каждого token-day:

```text
onchain_fills -> trades
```

Loader не объединяет каналы. Это исключает двойной учёт одной сделки.

`onchain_fills` — предпочтительный источник. `trades` — обязательный fallback
для дней, где onchain parquet отсутствует, пуст или не покрывает окно. Именно
так текущий Dota backtest использует локальный Telonex archive.

`book_snapshot_full` и `trades` составляют минимальный локальный контракт.
Пропуск `onchain_fills` не исключает карту. Пропуск обязательного канала
исключает карту с явной причиной.

Runner устанавливает `TELONEX_DISABLE_POLYMARKET_TRADE_FALLBACK=1`. Replay не
обращается к public Polymarket API и не меняет данные между запусками.

## Stage 07 готовит только backtest inputs

Новый `src/lol/07_prepare_backtest.py` не обучает модель и не загружает данные
из сети. Он читает завершённые LoL artifacts и публикует:

```text
data/lol/processed/backtest/
├── signals.parquet
├── market_seconds.parquet
└── audit.parquet
```

Stage 07 обрабатывает только карты зафиксированного validation split. Execution
archive остаётся полным для всех поддержанных linked markets, пока платный
Telonex доступен.

### Signal timeline

`signals.parquet` хранит model-independent inputs. Model predictions в файл не
попадают, потому что runner выбирает research или production model во время
запуска.

Для каждой validation-карты Stage 07 создаёт signal timeline:

```text
second = 0, 1, 2, ..., 899
```

Rows `0..540` берутся прямо из готового `validation.parquet`. Это гарантирует
тот же feature order и те же значения, которые использовал research fit.

Rows `541..899` строятся теми же feature functions из raw lolesports frames.
Эти rows нужны только для работы strategy после BUY cutoff. Они не получают
training label и не меняют модель.

Каждый signal row хранит:

- `match_id` и PM `event_id`;
- pause-adjusted game second;
- точный wall timestamp;
- 12 model features в canonical order;
- market anchor и поля strategy, которые уже читает Dota signal path.

Если игра закончилась раньше, timeline заканчивается на настоящем game end.
Отсутствующий или несвежий игровой frame создаёт signal gap по тем же правилам,
что готовый dataset. Stage 07 не интерполирует features.

Stage 07 читает `source_lag_seconds` из research `model.json`. Production model
обязана иметь тот же lag. Runner отклоняет модель с другим lag вместо скрытого
пересчёта cache.

### Market-second cache

`market_seconds.parquet` хранит paired market midpoint для игровых секунд
`0..1200`. Он применяет существующие Dota freshness и pair-sum rules.

Cache нужен для 30-second и 300-second markout, drawdown и оценки открытой
позиции на границе 20 минут. Nautilus всё равно читает raw books и execution
ticks для самого replay.

### Eligibility audit

`audit.parquet` содержит одну строку на validation-карту. Строка хранит:

- `match_id`, `event_id`, condition ID, slug и оба token IDs;
- Blue to Radiant token orientation;
- известный resolved outcome;
- replay start, настоящий game end и wall timestamp секунды 1200;
- наличие обязательных raw channels;
- число signal rows и market seconds;
- `eligible` и одну стабильную exclusion reason.

Карта eligible, только если runner может воспроизвести её без сети и без
неизвестной резолюции. Stage 07 не скрывает исключённые карты.

LoL использует тот же двухчасовой pre-game replay lead, что Dota. Replay
заканчивается через одну минуту после настоящего game end или cutoff, чтобы
Nautilus обработал cancel latency и terminal events.

## Общий runner

### CLI

Существующий target сохраняет Dota default:

```bash
make backtest ARGS="--validation --model-dir data/new_model/research"
```

Новый target запускает paired LoL validation по умолчанию:

```bash
make lol-backtest ARGS="--validation --model-dir data/lol/models/research"
```

Production smoke использует тот же validation split:

```bash
make lol-backtest ARGS="--validation --model-dir data/lol/models/production --name production_smoke"
```

`make lol-backtest` передаёт `--game lol` и paired B0,S2 profile. Прямой CLI
сохраняет существующие `--match-id`, `--validation`, `--limit`, `--resume`,
`--shard`, `--merge-shards`, `--profile`, `--fill-model` и `--model-dir`.

### Strategy contract

LoL не получает свои strategy constants. Runner использует те же defaults, что
текущий Dota maker:

- BUY cutoff на игровой секунде 540;
- unwind через 300 секунд после первого BUY fill;
- те же B0 и S2 fair sources;
- join placement и queue fill;
- те же delta, price, net-worth velocity, order size, latency и settle gates.

Manifest записывает точные значения каждого запуска. Изменение strategy для
LoL требует отдельного исследования и не входит в этот design.

### Inputs остаются тонкими

`src/backtest/lol_inputs.py` читает Stage 07 outputs и строит существующие
`MarketContext`, signals и midpoint series. Dota продолжает читать свои текущие
selection, OpenDota pauses, GRID windows и caches.

Функции в `src/backtest/telonex_local.py` получают `capture_root` аргумент.
Default остаётся текущим Dota root. LoL передаёт
`data/lol/raw/telonex/polymarket`. Этого аргумента достаточно для общего
eligibility check и общего symlink tree. Отдельный LoL Telonex adapter не нужен.

После загрузки inputs обе игры проходят один код:

1. Создать локальное Telonex source tree.
2. Загрузить L2 books и первый доступный execution channel.
3. Запустить каждый arm через `DotaMakerStrategy`. Класс сохраняет имя, потому
   что его поведение не зависит от игры.
4. Собрать fills, quote events, match results и wallet path.
5. Построить общий summary.

Новый runner, LoL strategy subclass и provider registry не создаются.

## Replay заканчивается на 20-й минуте

Raw lolesports windows заканчиваются на игровой секунде 1200. Backtest не
докачивает полный игровой хвост.

Если карта заканчивается не позже секунды 1200, replay использует настоящий
game end и обычную резолюцию.

Если карта продолжается после секунды 1200, runner выполняет четыре действия:

1. Запрещает новые заявки на секунде 1200.
2. Отменяет активную заявку с обычной cancel latency.
3. Сохраняет оставшуюся позицию как forced hold.
4. Рассчитывает эту позицию по известному Gamma outcome.

B0 и S2 используют одно правило. Резолюция не доступна strategy до окончания
replay и применяется только в postprocess.

Postprocess ставит settlement на более поздний timestamp из cutoff и архивного
Gamma `closed_at`. Так shared wallet учитывает, когда деньги от forced hold
снова стали доступны. Book и execution events после cutoff в replay не
участвуют.

### Tail PnL не смешивается с наблюдаемым replay

Для каждого token остаток раскладывается на две части:

```text
pnl_at_cutoff = cash_before_rebate + open_quantity * fresh_mid_at_1200
tail_pnl      = open_quantity * (settlement_value - fresh_mid_at_1200)
settled_pnl   = pnl_at_cutoff + tail_pnl
```

Для выигравшего token `settlement_value = 1`, для проигравшего token — `0`.
Расчёт суммирует оба tokens, если позиция содержит оба.

`results.parquet` получает per-map cutoff inventory, cutoff midpoint,
`pnl_at_cutoff`, `tail_pnl` и settled PnL. `summary.json` отдельно показывает:

- число forced-hold карт;
- количество оставшихся shares;
- PnL на cutoff;
- tail PnL;
- settled PnL.

Stage 07 исключает продолжающуюся после секунды 1200 карту без свежего paired
midpoint на cutoff. Audit сохраняет причину. Такой пропуск нельзя маскировать
последней старой ценой.

## Отчёт отвечает, добавляет ли S2 ценность

LoL пишет artifacts только в:

```text
data/backtests/lol_maker/<run-name>/
```

Run сохраняет существующие файлы:

- `manifest.json`;
- `results.parquet`;
- `fills.parquet`;
- `quote_events.parquet`;
- `summary.json`.

Manifest дополнительно записывает игру, model path и hash, source lag,
validation split hash, Stage 07 input hashes, framework commit, execution source
priority и cutoff rule.

Основной показатель — quantity-weighted BUY 300-second markout. Summary
показывает B0, S2 и paired difference `S2 - B0` на одном наборе карт.

LoL переиспользует текущий Dota `match_id` bootstrap и общий `build_decision`
без отдельной ветки. Report показывает числовые estimates, confidence intervals
и PnL до maker rebate, но не превращает их в LoL-specific verdict или status.

Production model проходит тот же report path как smoke test. Production fit уже
видел validation-карты, поэтому его metrics проверяют только совместимость
model, signals, strategy и engine, а не holdout quality.

## Failure policy

Stage 02 продолжает независимые jobs после 404, исчерпанных retries и corrupt
local files. Он пишет каждый итог в audit. Ошибка обязательного job оставляет
run incomplete и возвращает ненулевой code. Пропуск необязательного
`onchain_fills` не делает полный run ошибочным, если `trades` доступен.

Stage 07 завершает работу до atomic publication при любом глобальном contract
нарушении:

- validation rows `0..540` не соответствуют готовому dataset;
- feature names или order не соответствуют model metadata;
- research и production model используют разные source lag;
- token orientation или outcome противоречат завершённым artifacts;
- split разделяет один PM event;
- один `match_id` встречается более одного раза.

Ошибки отдельных карт создают audit reasons. Если eligible-карт нет, Stage 07
возвращает ненулевой code.

Runner применяет ещё четыре проверки:

- B0 и S2 получают одинаковый ordered map set;
- resume manifest совпадает по игре, model, split, inputs и strategy parameters;
- model source lag совпадает со Stage 07 inputs;
- LoL outputs не используют Dota report directory.

Несовпадение останавливает run. Runner не объединяет несовместимые checkpoints.

## Tests

Fixture tests Stage 02 проверяют:

- готовые books проходят validation и не скачиваются повторно;
- отсутствующие `trades` и `onchain_fills` скачиваются;
- missing `trades` оставляет обязательный job incomplete;
- missing `onchain_fills` сохраняется как допустимый gap;
- `.partial`, retry и atomic replace работают для каждого канала;
- audit различает channels.

Fixture tests Stage 07 проверяют:

- pause-adjusted wall timestamps;
- точное сохранение validation rows `0..540`;
- inference-only rows `541..899`;
- границы signal 899 и market second 1200;
- ранний настоящий game end;
- stable eligibility reasons;
- отсутствие interpolation и outcome leakage.

Backtest tests проверяют:

- `--game` по умолчанию выбирает Dota;
- `make backtest` сохраняет текущую Dota команду;
- LoL inputs пишут только в `data/backtests/lol_maker`;
- B0 и S2 используют одинаковые карты;
- LoL report переиспользует Dota `match_id` bootstrap и общий `build_decision`;
- cancel на секунде 1200 и forced settlement дают правильный tail PnL;
- production model проходит тот же report path без отдельного status;
- один небольшой paired B0,S2 fixture проходит end to end.

CI не скачивает Telonex и не запускает полный historical replay. Реальный финальный
прогон выполняет:

1. Повторный Stage 02 с Telonex key.
2. Stage 07 на готовом validation split.
3. Paired research `make lol-backtest`.
4. Production smoke.
5. Релевантные LoL и Dota tests.
6. `make lint-all`.

## Отвергнутые варианты

### Отдельный LoL runner

Копия Dota runner быстро разойдётся по queue, latency, resume и report behavior.
Один `--game` dispatch сохраняет общую execution semantics.

### Provider framework

Две игры требуют двух input functions, а не registry, ABC или plugin system.
Новая абстракция не уменьшит текущий код.

### Новый Stage 07 downloader

Dota скачивал backtest channels одним Telonex downloader. LoL расширяет готовый
Stage 02 и использует его resume вместо второго сетевого entrypoint.

### Только `trades`

Backtest запустится без `onchain_fills`, но не повторит фактический Dota source
priority. LoL скачивает оба execution channels и сохраняет `trades` как полный
fallback.

### Полный игровой tail

Текущий raw lolesports contract заканчивается на секунде 1200. Forced settlement
даёт проверяемый V1 без новой исторической загрузки. Отчёт отдельно показывает
цену этого упрощения.

### Автоматический LoL verdict

Dota report уже показывает B0, S2, paired difference, confidence intervals и
PnL. Отдельный LoL status дублировал бы интерпретацию этих чисел, но не менял бы
exit code или публикацию модели. V1 оставляет итоговое решение пользователю.
