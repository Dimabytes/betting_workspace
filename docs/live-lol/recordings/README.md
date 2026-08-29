# Сырые записи: GRID widget против lolesports livestats

Дата записи: 2026-08-29.

Эти файлы — сырой вход к закрытому результату в
`../2026-08-29-grid-source-verification.md`. Лежат здесь, чтобы числа
можно было перепроверить без живого матча.

## Что записано

| Каталог | Матч | GRID series | esportsGameId |
|---|---|---|---|
| `lol-fxw7-los-2026-08-29-20260829T180601Z` | CBLOL, Fluxo W7M против LOS, карта 1 | 2973268 | 115565671526402982 |
| `lol-navi-gx-2026-08-29-20260829T180548Z` | LEC, Natus Vincere против GIANTX, карта 2 | 2966907 | 115548681803406157 |

Каждый каталог содержит:

- `grid.jsonl.gz` — сырые фреймы widget socket. Одна строка на фрейм:
  `received_at_utc` и `frame` как строка.
- `livestats.jsonl.gz` — сырые ответы окна livestats. Одна строка на запрос:
  `received_at_utc`, `starting_time` и `payload`.
- `target.json` — что именно записано.

Каталог CBLOL дополнительно несёт вход реконструкции net worth:

- `details.jsonl.gz` — исторические кадры `details` того же матча, по одному
  на `rfc460Timestamp`.
- `ddragon_items.json` — таблица предметов Data Dragon (патч 16.17.1): цены и
  флаг `consumed`.

Записывал `dota_2_model/scripts/record_lol_dual_feed.py`, по 12 минут на матч.

CBLOL несёт основной результат: около 700 секунд пересечения и 10 смертей.
LEC даёт только 70 секунд игры и ноль смертей, но подтверждает обработку пауз.

## Как перепроверить

Из `../dota_2_model`:

```bash
make run F=scripts/compare_lol_grid_livestats.py \
  ARGS="../betting_workspace/docs/live-lol/recordings/lol-fxw7-los-2026-08-29-20260829T180601Z"
```

Скрипт читает и `.jsonl`, и `.jsonl.gz`.

Вывод обоих матчей на момент записи лежит в `comparator-output.txt`.

Реконструкция GRID-эквивалентного net worth из истории lolesports (сдвиг +7 —
это оценка по смертям из компаратора):

```bash
make run F=scripts/reconstruct_lol_networth.py \
  ARGS="../betting_workspace/docs/live-lol/recordings/lol-fxw7-los-2026-08-29-20260829T180601Z --offset 7"
```

`details.jsonl.gz` и `ddragon_items.json` уже лежат рядом, поэтому запуск
оффлайновый; на новой записи скрипт сам достанет и закэширует оба файла.

## Что должно получиться

| Проверка | Ожидание на CBLOL |
|---|---|
| Стороны GRID | `BLUE`, `RED` |
| `feed_delay` таблицы GRID | ровно 8 |
| Паузы livestats | одна, 32.0 с |
| Сдвиг часов по смертям | +6.7 с, n=10, разброс 1.7 |
| Сдвиг по кривой золота | +22 с — заведомо хуже, кривая пологая |
| Смерти | совпадают точно |
| `blue_nw`, `red_nw` | GRID ниже на 4.7% и 6.0% |
| `nw_adv` | расхождение 145, это 32% сигнала |
| `xp_adv` | медиана 0 |
| Реконструкция `totalGold − consumed` | ratio к GRID 1.002 (blue) и 0.997 (red), перевороты знака `nw_adv` падают с 16% до 5.8% |

На LEC ожидается пауза 99.5 с и меньше трёх общих смертей, поэтому скрипт
там честно откатывается на оценку по золоту.

## Границы этих данных

- Два матча, две лиги, один день.
- GRID отдаёт сэмпл примерно раз в 4.3 секунды, livestats около 3 Гц. Часть
  разброса — разрежённость GRID.
- Обе карты записаны не с самого начала: CBLOL с 53-й секунды по часам GRID,
  LEC с −8.
- Отношение сырого net worth 0.95 подтверждено на одной карте.
  Реконструкция `totalGold − consumed` на той же карте даёт ratio 1.00.
