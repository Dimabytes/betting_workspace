# Worktree для esports-trader

Второй каталог со своей веткой. Основной `esports-trader` остаётся на `main`.

```bash
git -C ../esports-trader worktree add -b <branch> ../esports-trader-<name> HEAD
cd ../esports-trader-<name>
```

## Данные

`data/raw`, `data/trader`, `data/new_processed`, `data/archive_index` в гите нет. В новом worktree их нет. Ссылки на основной чекаут:

```bash
MAIN=../esports-trader
for name in raw trader new_processed archive_index grid; do
  [ -e "data/$name" ] || ln -s "$PWD/$MAIN/data/$name" "data/$name"
done
```

`data/backtests` в гите нет, кроме `market_terms.json`. Всю папку не ссылать. Прогон пишется в `data/backtests` этого worktree. В `esports-trader/data/backtests` его не будет. Уйти в основной каталог прогон не удаляет. `git worktree remove` удаляет папку worktree вместе с прогоном.

`scripts/run_12_seeds.sh` сравнивает прогон с `data/backtests/<game>_maker/LIVE`. Ссылки на `LIVE` основного чекаута:

```bash
for link in dota_maker/LIVE dota_maker/LIVE_NOXP lol_maker/LIVE; do
  mkdir -p "data/backtests/$(dirname $link)"
  [ -e "data/backtests/$link" ] || ln -s "$PWD/$MAIN/data/backtests/$link" "data/backtests/$link"
done
```

Датасеты (`data/new_processed`) в гите нет, и в этом worktree это ссылка на основной чекаут. Train их читает оттуда же. Пересборка датасета из worktree перезаписывает те же файлы. Модель (`data/new_model`, `data/lol/models`) в гите и это отдельная копия worktree: train публикует в неё, основной чекаут не трогает. `data/lol/processed` и `data/lol/raw` в гите нет и ссылкой не сделаны.

`quote_events.parquet` для `compare_backtests.py` не нужен.

Ветка без коммита `0094b4df` (12.10.2026) несёт старый `.gitignore`. В ней прогоны видны как untracked. Не делать там `git add -A`, пока ветка не взяла `main`.

## Кэш книг

Кэш один на машину: `~/.cache/esports-trader/telonex-tree` и `~/.cache/nautilus_trader/telonex`. Ключ считается по настоящему пути файла, не по пути worktree. Это есть на ветке `sell-clip` (`src/backtest/telonex_local.py`). Worktree без этой правки на архивных картах не попадает в кэш, сносит его и заново читает стаканы. 12 шардов так упираются в память.

## uv

У worktree свой `.venv`. `.venv` основного чекаута он не видит. До шардов один раз:

```bash
uv sync --group backtest
```

Потом `scripts/run_seeds.sh`. Не запускать 12 шардов в worktree, где `.venv` ещё нет: они одновременно создают его и один падает.
