# Бэкфилл победителя Oddin

Дата: 2026-10-27. Источник проблемы: `investigations/2026-10-27-dota-pipeline-audit/report.md`, раздел «Что видно на ленте». Фикс в коде: esports-trader `7506bf9e` (редьюсер читает `currentMap.won`, closed-строка несёт `catalogScore`).

Старые архивы этот фикс не меняет: `final` уже записан. Скрипт `work/backfill_oddin_winner.py` дописывает только `final.winner`, если он `null`.

## Источники победителя

1. `replay`: `oddin_state.jsonl` через текущий `OddinSnapshotReducer` (`trader.oddin_archive.summarize`).
2. `next_map`: `previousMaps` в архиве следующей карты той же серии Oddin.
3. Нет ни того, ни другого — `unresolved`.

Если победитель не сходится с последним мидом книги из `session.jsonl`, строка помечается `skip_book_disagrees` и не пишется.

## Сухой прогон на локальной копии `data/trader` (синк 24–25 октября)

93 Oddin-карты с `final.winner = null`: 74 `write` (73 `replay`, 1 `next_map`), 19 `unresolved`, 0 `skip_book_disagrees`. Все 74 победителя совпадают со стороной книги. У 18 из 19 `unresolved` книга в конце решающая (≤ 0.025 или ≥ 0.965), у `9059530192` — 0.210.

## Запуск на VPS

```bash
cd /root/work/esports-trader
PYTHONPATH=src uv run python /root/work/betting_workspace/investigations/2026-10-27-oddin-winner-backfill/work/backfill_oddin_winner.py data/trader_live
# после проверки таблицы:
PYTHONPATH=src uv run python /root/work/betting_workspace/investigations/2026-10-27-oddin-winner-backfill/work/backfill_oddin_winner.py data/trader_live --write
```

Нужен checkout с `7506bf9e`. Запись атомарная (`match_meta._atomic_write_json`), меняет только `final.winner` и только пока он `null`.
