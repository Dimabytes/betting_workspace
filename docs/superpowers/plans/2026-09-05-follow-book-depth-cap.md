# LFOLLOW600: быстрый эксперимент с размером по книге

> **Для агента в новом контексте:** прочитай этот документ целиком, реализуй описанное изменение только в backtest, выполни проверки и два новых прогона, затем напиши сравнительный отчёт. Пользователь выбрал небольшой первый эксперимент; не расширяй его до всей старой матрицы.

**Goal:** проверить, сохраняет ли трёхуровневый LFOLLOW600 прибыль при меньшей потребности в капитале, если ограничивать размер новых BUY чужой глубиной на их цене.

**Architecture:** цены и жизненный цикл остаются от исправленного buy-ladder-v2. Единственное новое торговое правило — ограничение количества при создании BUY. Уже стоящие ордера сохраняют количество и очередь при изменении глубины и при переassignении ступени.

**Tech Stack:** Python, существующий Dota maker backtest на Nautilus, pandas, pytest, `uv`.

**Spec:** самодостаточная спецификация находится в разделах 1–5 этого документа; шаги исполнения — в разделах 6–9. Предыдущая переписка для реализации не нужна.

## 1. Рабочая среда и границы

- Этот файл находится в `betting_workspace` — репозитории инструкций, без продуктового кода.
- Код: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`.
- Перед командами по проекту прочитать `esports-trader/AGENTS.md`. Работать на `main`, если пользователь не направит иначе.
- Все git-команды выполнять внутри изменяемого репозитория. Сначала проверить состояние дерева; чужие изменения не сбрасывать и не включать в свои коммиты.
- Python запускать через `uv run python`; для Nautilus требуется `--group backtest` и существующий `PYTHONPATH`.
- `poly-maker` заморожен: не изменять. Не менять также framework, live/paper, VPS, модель, данные и `config/trading.toml`.
- Никаких новых зависимостей, сетевых данных или переобучения.
- В этом документе только план. Код и новые прогоны ещё не выполнены.
- Подагенты для исполнения не обязательны. Если пользователь просит прежнюю конфигурацию делегирования: навык `herdr`, Cursor `--model cursor-grok-4.6-high`. Не подменять её другой моделью; сначала проверить `HERDR_ENV` по навыку.

## 2. Почему проверяем именно это

Сверено 2026-09-05 по сохранённым `summary.json`, `fills.parquet`, `results.parquet`. Одинаковые seeds **0, 1, 2**, 454 карты:

| Контроль | Net, $ | Engine, $ | Rebate, $ | Требуемый капитал с резервами, $ |
|---|---:|---:|---:|---:|
| LFOLLOW300 | 1096.94 | 587.79 | 509.16 | 895.68 |
| LFOLLOW600 | 1554.37 | 655.29 | 899.08 | 2190.92 |

У LFOLLOW600 есть 12 seeds: net $1470.36, engine $585.17, rebate $885.19, капитал с резервами $2116.45. **В этом эксперименте контроль берётся только на seeds 0–2**, поскольку новые варианты запускаем на трёх seeds.

У LFOLLOW600 на seeds 0–2 примерно 61.6% BUY-оборота приходится на ордера, первоначально поставленные на L0, 23.8% на L1 и 14.6% на L2. Уровень на момент fill может отличаться из-за reattach. Это описательная статистика, не готовые оптимальные веса.

Цель — сохранить абсолютную прибыль с меньшим капиталом. Просто рост `net / capital` при сильном падении net не считается успехом.

Важно: `base_size_usdc=600` — номинальный бюджет набора, не общий лимит покупок за эпизод. Частичные исполнения и повторные постановки способны дать накопленные покупки больше $600. Здесь эту семантику не меняем.

## 3. Матрица: только два новых полных прогона

Во всех вариантах: parallel, follow, continue после первого fill, 3 ступени, шаг 1 тик.

| ID | Размер | Правило | Источник |
|---|---:|---|---|
| F600 | $600 | нынешние $200 на ступень | готовый LFOLLOW600, seeds 0–2 |
| D600-K1 | максимум $600 | новая заявка ≤ 1 × чужой объём на её цене, максимум $200 | новый прогон, seeds 0–2 |
| D600-K2 | максимум $600 | новая заявка ≤ 2 × чужой объём на её цене, максимум $200 | новый прогон, seeds 0–2 |
| F300 | $300 | нынешние $100 на ступень | готовый LFOLLOW300, seeds 0–2 |

`K1`: если чужих заявок на цене $100, наша заявка максимум $100, итоговая глубина удваивается. `K2`: наша заявка максимум $200, итоговая глубина утраивается.

F300 нужен как контроль обычного уменьшения размера: адаптация должна дать пользу сверх простого снижения клипа.

**Не входят:** адаптивный F300, новые прогоны legacy B, P100, добор до 12 seeds, пирамида, четвёртая ступень, объём принтов, EWMA глубины, toxicity, flow, inventory taper, новый выход, лимит кошелька. Не добавлять их автоматически даже после хорошего результата.

## 4. Точное правило размера

### 4.1. Входные величины

Для нового BUY на цене `p`:

- `B = base_size_usdc / layers`, здесь $200;
- `D` — чужое число shares в bid-книге **точно на цене p** выбранного токена;
- `k` — 1 или 2;
- сырое количество `q = min(B / p, k * D)`;
- округлить q вниз до допустимой точности quantity (в текущем backtest 0.01 share);
- если после округления q меньше `MIN_ORDER_SIZE` (сейчас 5 shares), ордер не ставить;
- остаток бюджета оставить свободным. Не переносить его на соседние ступени, не поднимать до минимума за счёт нарушения cap.

Пример при B=$200: чужая глубина на трёх ценах в долларовом выражении $80/$40/$20. K1 выставляет $80/$40/$20, K2 — $160/$80/$40, с поправкой на округление количества. Оба варианта могут использовать существенно меньше $600.

Пустой уровень D=0 означает отсутствие новой заявки. Это сознательная часть гипотезы: потенциальных уровней три, фактически работающих может быть меньше.

Неизвестная глубина (`nan`), отрицательная глубина или отсутствие нужного L2-доступа — ошибка проверки данных/реализации, а не нулевая глубина. Для нового режима обнаружить её на smoke и остановить ошибочный прогон с понятной причиной; не превращать незаметно все BUY в ноль. Старый fixed-режим не менять.

### 4.2. Чья это книга

Использовать существующий `volume_at_price(book, p, is_buy=True)` из `src/backtest/maker_orders.py`. Проверить тестом, содержит ли используемая replay-книга синтетические ордера стратегии: снимок внешней глубины до и после собственной постановки должен объяснять источник D.

Если replay-книга содержит только внешнюю историческую ликвидность, брать её напрямую — **не вычитать свои заявки второй раз**. Если собственные заявки включены, исключать только действительно отражённый в книге собственный остаток на том же токене, стороне и цене. Не гадать по аналогии с live L2; зафиксировать проверенную семантику в отчёте. Перед чтением framework прочитать briefing `betting_workspace/.learnings/prediction-market-backtesting.md`.

### 4.3. Когда применяется cap

**Только при новой постановке.** Читать текущую книгу в момент решения о submit; не использовать будущую глубину на момент приёма/исполнения и не брать устаревшую глубину из давнего model latch.

- Изменение D при той же законной цене не вызывает cancel/replace.
- Уже стоящий BUY сначала матчится/переиспользуется нынешним reconciliation; уменьшение D до нуля не удаляет такой target.
- При reattach живой ордер меняет номер ступени, сохраняя order id, цену, размер, накопленные fills и очередь.
- После естественной отмены по существующим правилам следующая новая заявка получает новый cap.
- Частичный fill не вызывает дозаполнение до $200 или до нового cap.
- Полностью исполненная ступень остаётся завершённой по текущему lifecycle; cap не открывает её заново.
- Gate по fair/anchor/staleness/cutoff и существующая логика отмен продолжают действовать без изменений.

Это **ограничение при постановке**, а не обещание непрерывно держать долю текущей книги ≤k. Такая оговорка обязательна в отчёте. Она позволяет первым тестом проверить уменьшение размера без отдельного эксперимента с потерей очереди.

### 4.4. Деньги и исполнение

- Проверять `_can_afford` по реально рассчитанному количеству. Старый предварительный тест на полные $200 не должен блокировать доступную уменьшенную заявку.
- Для уже живого законного BUY не пересчитывать доступность так, будто размещаем его заново.
- `SubmittedOrder.quantity`, `LiveOrder`, `BuyLevel.full_qty`, submit telemetry и fill telemetry должны описывать фактическое количество ордера, не номинальные $200.
- Canceling BUY продолжает резервировать весь оставшийся notional до cancel ack.
- Не считать резерв как notional × вероятность fill. Все реально выставленные shares могут исполниться.
- SELL, fees, rebate, settlement и queue fill model не меняются.

## 5. Воспроизводимость и файлы

Рабочий каталог далее — корень `esports-trader`.

Прочитать перед изменением:

- `docs/experiments/buy-ladder-v2/protocol.md`;
- `docs/experiments/buy-ladder-v2/progress.md`, особенно §19;
- `docs/experiments/buy-ladder-v2/commands.md`;
- `src/backtest/strategy.py`: `_layer_budget`, `_choose_buy_targets`, `_reconcile_targets`, `_live_matches`, `_submit_target`, `_reattach_level`;
- `src/backtest/maker_orders.py`: `volume_at_price`, `QuoteTarget`, live/submitted/level state;
- `src/backtest/run.py`: `MakerQuotePolicy`, CLI, strategy config, manifest и shard merge;
- `src/backtest/wallet_path.py`, `src/backtest/postprocess.py`;
- существующие тесты `tests/test_backtest_maker.py` и `tests/test_backtest.py`.

`buy-ladder-v2/report.md` описывает старый v1; не использовать его вывод «drop for live» как результат текущего sizing-теста.

Контрольные корни:

```text
data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow600
data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300
```

Pinned model: `data/backtests/dota_maker/ladder-v2-inputs/model`.
SHA256 `model.txt`: `9f54106a5bee03a8ab8a1bfdb4a6403246b319864f9a91fd103b2dc9ef7f3ec2`.
Universe: `docs/experiments/buy-ladder-v2/baseline/match_ids.txt`, 454 карты.
Остальные хэши — в предыдущем protocol. Не использовать текущий `LIVE` symlink как контроль.

Общие параметры из контрольных manifests: queue; cutoff 540; `max_abs_nw_delta_30=350`; min abs delta 0.01; min entry price 0.35; latency 85 ms. Перед запуском проверить совпадение реально разрешённых параметров: defaults могли измениться после написания документа.

Планируемые изменения:

| Файл | Назначение |
|---|---|
| Новый `src/backtest/buy_sizing.py` | небольшая чистая функция расчёта capped quantity |
| `src/backtest/strategy.py` | параметры политики и применение размера при submit с сохранением reconciliation |
| `src/backtest/run.py` | CLI → policy → config → manifest; согласованность shards/resume |
| Новый `tests/test_backtest_buy_sizing.py` | арифметика cap, минимум и округление |
| `tests/test_backtest_maker.py` | реальные lifecycle-сценарии уменьшенных BUY |
| `tests/test_backtest.py` | прохождение новых параметров и идентичность manifest-политики |
| Новый `scripts/analyze_buy_depth_cap.py` | небольшой анализ четырёх вариантов из существующих parquet/summary |
| Новый `docs/experiments/buy-depth-cap/report.md` | команды, проверки, таблица, вывод и ограничения |
| Новый `docs/experiments/buy-depth-cap/summary.csv` | численная таблица для повторного чтения |

Не рефакторить весь большой `strategy.py`. Использовать имеющиеся readers/types. Изменения других файлов допустимы только если они нужны для согласованного существующего пути конфигурации или сериализации, с объяснением в отчёте.

## 6. Реализация и проверки

### Шаг A. Сохранить контроль поведения

- [ ] Снять git status/diff и хэши исходников, данных, модели в новый каталог эксперимента, не изменяя старые результаты.
- [ ] Проверить готовые F600/F300 по seeds 0–2: те же 454 карты, нет дубликатов и early termination, присутствуют четыре основных артефакта и `quote_events.parquet`.
- [ ] До изменения кода выполнить smoke F600 на 10 картах, seed0, с отдельным именем `depth-cap-fixed-before`. Команда из раздела 7 без нового флага. Сохранить его результаты для проверки неизменности fixed.

### Шаг B. Чистый расчёт количества

Интерфейс новой функции (все аргументы обязательны):

```python
from decimal import Decimal, ROUND_DOWN
from math import isfinite


def calculate_depth_capped_quantity(
    *,
    level_budget_usdc: float,
    price: float,
    external_depth_shares: float,
    depth_multiple: float,
    min_order_shares: float,
) -> float:
    if not isfinite(external_depth_shares) or external_depth_shares < 0:
        raise ValueError("BUY depth must be known, finite and non-negative")
    budget = Decimal(str(level_budget_usdc))
    order_price = Decimal(str(price))
    depth = Decimal(str(external_depth_shares))
    multiple = Decimal(str(depth_multiple))
    minimum = Decimal(str(min_order_shares))
    budget_quantity = budget / order_price
    depth_quantity = multiple * depth
    quantity = min(budget_quantity, depth_quantity)
    rounded = quantity.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return float(rounded) if rounded >= minimum else 0.0
```

Цена приходит из существующего gate законности BUY, положительные budget/multiple — из валидированной конфигурации. Если фактическая точность инструмента отличается от 0.01 share, использовать её явно и обновить тесты/описание до прогонов; не округлять количество повторно вверх при создании venue order.

- [ ] Добавить параметризованные тесты с конкретными ожидаемыми значениями:

```python
import pytest

from backtest.buy_sizing import calculate_depth_capped_quantity


@pytest.mark.parametrize(
    ("depth", "multiple", "expected"),
    [(100.0, 1.0, 100.0), (100.0, 2.0, 200.0),
     (1000.0, 2.0, 400.0), (0.0, 1.0, 0.0),
     (4.99, 1.0, 0.0), (5.0, 1.0, 5.0),
     (5.019, 1.0, 5.01)],
)
def test_cap_respects_depth_budget_and_minimum(depth, multiple, expected):
    result = calculate_depth_capped_quantity(
        level_budget_usdc=200.0,
        price=0.5,
        external_depth_shares=depth,
        depth_multiple=multiple,
        min_order_shares=5.0,
    )
    assert result == expected
```

- [ ] Добавить случай budget=$200, price=0.49, D=10000, k=1 → 408.16 shares, notional ≤$200, и случаи `nan`/−1 depth → `ValueError`.
- [ ] Сначала получить ожидаемое падение новых тестов, затем реализовать функцию и получить PASS. При оформлении тестов использовать принятые в проекте аннотации типов.

### Шаг C. Подключение к стратегии

- [ ] Добавить `--buy-depth-cap-multiple`: положительное конечное число; отсутствие флага означает старый fixed. В policy/config представлять отсутствие как `None`; не использовать k=0 как неоднозначное «выключено».
- [ ] Добавить manifest-поля `buy_depth_cap_multiple` и `buy_size_policy` (`fixed-v1` или `depth-at-submit-v1`). Не переименовывать прежнюю ladder lifecycle policy. Новые поля должны доходить до всех shards и участвовать в проверке resume/merge.
- [ ] Применять новую функцию только к новым BUY при включённом флаге. Сохранить старую арифметику и округление для fixed.
- [ ] Устранить предварительную проверку доступности полного клипа именно в новом режиме; не отфильтровывать ценовой target из-за cap до попытки сохранить стоящий ордер.
- [ ] Передать реальное количество во все order/state/telemetry поля, перечисленные в §4.4. При новом submit заморозить фактический D в `SubmittedOrder.context.queue_ahead` для последующей диагностики fills.
- [ ] Для нулевого/subminimum нового количества не посылать ордер на venue. Записать различимую причину `depth_cap` через существующий механизм no-quote, без новых событий отмены и без безграничного лога на каждый book tick.
- [ ] Проверить сценарии на существующем strategy harness: D упало после submit — прежний id и остаток сохраняются; D=0 не отменяет живой ордер; естественный reprice использует свежий D; reattach сохраняет id/количество и последующий full fill закрывает правильную ступень; partial fill не дозаполняется; canceling резерв освобождается лишь после ack; хватает денег на capped BUY, но не на $200 — capped BUY ставится; SELL не меняется.
- [ ] Проверить тестом источник внешнего D по §4.2. При задержке отправки cap всё равно использует только доступный на decision time снимок.
- [ ] CLI-тесты: no flag → fixed; 1/2 доходят до strategy config/manifest; 0/отрицательное/nan/inf отклоняются; merge/resume с различающимся cap не смешивает результаты.

Проверки после реализации:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_backtest_buy_sizing.py tests/test_backtest_maker.py tests/test_backtest.py tests/test_backtest_postprocess.py
uv run python -m ruff check src/backtest/buy_sizing.py src/backtest/strategy.py src/backtest/run.py tests/test_backtest_buy_sizing.py tests/test_backtest_maker.py tests/test_backtest.py scripts/analyze_buy_depth_cap.py
```

Команду lint запускать после создания analysis script; существующие несвязанные ошибки отделить от новых. Не расширять задачу до их ремонта.

- [ ] Повторить fixed smoke после изменений с именем `depth-cap-fixed-after`; сравнить с before все содержательные results/fills/quote events, кроме имён запуска, новых полей manifest и времени выполнения. Траектория fixed должна совпасть.
- [ ] Если контроль не совпал, сначала выяснить причину. При изменившемся относительно архивов торговом коде пересчитать F600/F300 под новыми именами на seeds 0–2; не сравнивать разные реализации и не перезаписывать архив. Это условный дополнительный контроль, не новая стратегия.

## 7. Команды новых прогонов

Эти команды исполнять **после реализации нового CLI-флага**. Названия выходов не переиспользовать для другой политики.

Smoke до изменения кода:

```bash
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python src/backtest/run.py --game dota --validation --limit 10 --signal-cadence-seed 0 --name depth-cap-fixed-before --layers 3 --layer-step-ticks 1 --base-size-usdc 600 --buy-lifecycle parallel --ladder-reprice-mode follow --buy-after-first-fill continue --model-dir data/backtests/dota_maker/ladder-v2-inputs/model
```

После изменения повторить ту же команду с `--name depth-cap-fixed-after`. Для K1 и K2 выполнить по smoke на тех же 10 картах: имена `depth-cap-k1-smoke` / `depth-cap-k2-smoke`, дополнительно `--buy-depth-cap-multiple 1` / `2`. Проверить не прибыль, а исполнение механики, quantity/minimum, отсутствие ошибок и наличие артефактов.

Затем **последовательно**, максимум 12 backtest-процессов суммарно, с учётом уже работающих пользовательских задач:

```bash
SEEDS=3 SHARDS=4 scripts/run_seeds.sh dota depth-cap-f600-k1 --layers 3 --layer-step-ticks 1 --base-size-usdc 600 --buy-lifecycle parallel --ladder-reprice-mode follow --buy-after-first-fill continue --buy-depth-cap-multiple 1 --model-dir data/backtests/dota_maker/ladder-v2-inputs/model
SEEDS=3 SHARDS=4 scripts/run_seeds.sh dota depth-cap-f600-k2 --layers 3 --layer-step-ticks 1 --base-size-usdc 600 --buy-lifecycle parallel --ladder-reprice-mode follow --buy-after-first-fill continue --buy-depth-cap-multiple 2 --model-dir data/backtests/dota_maker/ladder-v2-inputs/model
```

Не останавливать чужие процессы. Записать exit status обоих драйверов. Дополнительно проверить логи каждого seed/shard и полноту выходов: один bare `wait` в текущем shell-драйвере не доказывает успех всех фоновых детей.

После каждого варианта выполнить audit. Для K1:

```bash
PYTHONPATH=src uv run python scripts/audit_buy_ladder_v2.py data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_depth-cap-f600-k1 --seeds 3 --universe docs/experiments/buy-ladder-v2/baseline/match_ids.txt
```

Для K2 заменить в пути `k1` на `k2`. Отдельно проверить наличие и читаемость `quote_events.parquet` во всех шести seed-каталогах: существующий audit не включает этот файл в обязательные.

Автоматическую печать сравнения с `LIVE` от `run_seeds.sh` не использовать как основной результат.

## 8. Анализ: четыре строки и объяснение результата

- [ ] Создать небольшой `scripts/analyze_buy_depth_cap.py`. Читать только seeds `0, 1, 2` явно, не первые три пути из лексикографического glob (`seed10` иначе попадёт раньше `seed2`). Проверить одинаковый universe до агрегации.
- [ ] Параметры CLI: `--fixed600`, `--fixed300`, `--k1`, `--k2` (пути к корням), `--output-dir` (каталог отчёта). Seeds первого теста фиксированы 0–2. Не создавать общий исследовательский framework.
- [ ] Сформировать `summary.csv` и `report.md` с четырьмя строками F600, K1, K2, F300: net, engine, rebate, BUY turnover, required_cash, required_cash_with_reserves, required_cash_with_reserves_at_close, peak_reserved, net/capital, медиана исполненного BUY-ордера, количество BUY submit и BUY-ордеров с исполнением.
- [ ] Net/капитал считать как отношение средних по seeds, рядом показывать сами средние и значения по каждому seed. Не брать старый `roi_with_rebate`, у него другой знаменатель. Медиану BUY брать по агрегированным исполнениям одного order id, не по отдельным partial fill.
- [ ] Отдельно дать Δnet, Δengine, Δrebate и процент изменения капитала относительно F600 и F300. Не выдавать разницу двух максимумов `with_reserves - required_cash` за точный состав капитала в момент пика.
- [ ] BUY markout на 30/300 секунд считать с весами quantity. Долю BUY-оборота по уровню показать по `submit_level_index`; отметить, что reattach меняет текущий `level_index`.
- [ ] Проверить на fills нового режима: `submitted_quantity <= k * queue_ahead` и `price * submitted_quantity <= 200` с небольшим численным допуском. Эти проверки охватывают исполненные ордера; покрытие неисполненных обеспечивают strategy tests и submit telemetry, не утверждать обратное.
- [ ] Для концентрации: получить net каждой карты = engine PnL из results + rebate её fills; усреднить seeds по `match_id`; взять **5 лучших и 5 худших из 454 карт**. Не считать 1362 строки независимыми картами. Если общий net ≤0, процентную долю хвостов не интерпретировать, показать доллары.
- [ ] Показать paired Δnet по трём хронологическим частям 151/151/152, отсортированным по `horn_at`, и результат без пяти лучших карт каждого варианта как sensitivity, не как торгуемую стратегию.

Команда анализа после создания скрипта:

```bash
PYTHONPATH=src uv run python scripts/analyze_buy_depth_cap.py --fixed600 data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow600 --fixed300 data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300 --k1 data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_depth-cap-f600-k1 --k2 data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_depth-cap-f600-k2 --output-dir docs/experiments/buy-depth-cap
```

Для первого быстрого скрининга использовать заранее зафиксированный ориентир:

- **Цель достигнута на point estimate:** net не меньше 99% F600, а средний required_cash_with_reserves не больше 80% F600.
- На исходных контролях это net ≥$1538.83 и капитал ≤$1752.74. При необходимом пересчёте контролей применять проценты к новым числам.
- **Компромисс:** net ниже этого порога, капитал снизился; явно показать цену экономии и сравнение с F300. Не назвать такой результат сохранением прибыли.
- **Не подтвердилось в этой форме:** значимой для цели экономии нет, прибыль потеряна без выигрыша относительно F300, либо cap почти никогда не ограничивал размер.

Это критерий отбора следующей гипотезы по трём seeds, **не статистическое подтверждение сохранения прибыли**. Не утверждать значимость и не переносить старые p-values/интервалы. Новые настройки исследовательские; для решения о live нужны отдельные повторения/новое временное окно.

Не обещать фактический live-депозит из среднего исторического минимума: расчёт использует существующие допущения settlement. Не выдавать эту проверку за моделирование реакции других участников на нашу видимую заявку.

## 9. Что вернуть пользователю и где остановиться

- [ ] В отчёте указать точные команды, hashes/состояние кода, все выходные пути, exit statuses, выполненные tests/audit и фактическую семантику глубины.
- [ ] Ответить простыми словами: сколько прибыли сохранили; сколько капитала освободили; чем результат лучше/хуже обычного F300; какие исполнения/уровни потеряли; насколько выигрыш зависит от rebate и лучших карт.
- [ ] Отметить, что отсутствие resize при изменении глубины — намеренное свойство этого теста. Отрицательный результат не опровергает любые стратегии по книге, а проверяет именно cap-at-submit.
- [ ] Передать пользователю ссылку на отчёт и таблицу, затем остановиться. Не запускать автоматически следующую сетку коэффициентов, 12 seeds, пирамиды или изменения live.

Авторизация в новом контексте при загрузке этого документа: реализовать этот backtest-эксперимент, проверить, выполнить указанные прогоны и представить результаты. Отдельные рутинные подтверждения между этими шагами не нужны.
