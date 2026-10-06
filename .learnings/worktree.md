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

`data/backtests` не ссылать. Прогон пишется в `data/backtests` этого worktree, рядом со своим `LIVE`. В `esports-trader/data/backtests` его не будет. Уйти в основной каталог прогон не удаляет. `git worktree remove` удаляет папку worktree вместе с прогоном.

Датасеты (`data/new_processed`) в гите нет, и в этом worktree это ссылка на основной чекаут. Train их читает оттуда же. Пересборка датасета из worktree перезаписывает те же файлы. Модель (`data/new_model`, `data/lol/models`) в гите и это отдельная копия worktree: train публикует в неё, основной чекаут не трогает. `data/lol/processed` и `data/lol/raw` в гите нет и ссылкой не сделаны.

`LIVE` в worktree — копия из гита. `quote_events.parquet` в гит не входит, он только в основном чекауте. Для `compare_backtests.py` он не нужен.

## Кэш книг

Кэш один на машину: `~/.cache/esports-trader/telonex-tree` и `~/.cache/nautilus_trader/telonex`. Ключ считается по настоящему пути файла, не по пути worktree. Это есть на ветке `sell-clip` (`src/backtest/telonex_local.py`). Worktree без этой правки на архивных картах не попадает в кэш, сносит его и заново читает стаканы. 12 шардов так упираются в память.

## uv

У worktree свой `.venv`. `.venv` основного чекаута он не видит. До шардов один раз:

```bash
uv sync --group backtest
```

Потом `scripts/run_seeds.sh`. Не запускать 12 шардов в worktree, где `.venv` ещё нет: они одновременно создают его и один падает.
