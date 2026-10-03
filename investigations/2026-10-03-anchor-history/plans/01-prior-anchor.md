<!-- b8ac9744-7ff1-4384-bc38-e9c99459c9f7 -->
---
todos:
  - id: "anchor-fn"
    content: "Функция horn − 90 − пауза и ветка load_anchors без спавна"
    status: pending
  - id: "test"
    content: "Тест: пауза в [−90, 0) вычитается, пауза до −90 нет, пустой список даёт −90"
    status: pending
  - id: "rebuild-quotes"
    content: "s05a без --fetch, make catalog, сверка якорей и prior"
    status: pending
  - id: "prepare-train"
    content: "make prepare и make train; кэш market_seconds и лайв не трогать"
    status: pending
isProject: false
---
# Запасной якорь приора

Один момент для всех карт: котировка, когда игровые часы на −90. Если есть GRID `startedAt`, он уже этот момент и остаётся как есть. Если окна нет, тот же момент считается из horn архива. Сейчас [s05a_fetch_prices_history.py](esports-trader/src/collect/s05a_fetch_prices_history.py) в этой ветке пишет голый `archive_horn_at_utc`.

Лайв (`match_worker._maybe_start_prior`: `horn − 90` с первого тика) не меняется.

## Код

В [match_time.py](esports-trader/src/shared/utils/match_time.py) одна функция — обратная к `get_horn_datetime`:

`horn_unix − HORN_OFFSET_SECONDS − get_paused_seconds_before(pauses, 0)`

Пауза с `time < −90` уже отбрасывается внутри `get_paused_seconds_before`. Пустой список пауз даёт ровно −90.

В `load_anchors` ветка без спавна:

- Паузы в том же порядке, что [s06 `resolve_pauses`](esports-trader/src/collect/s06_publish_catalog.py): OpenDota (`try_load_opendota_pauses`), иначе паузы schedule этого архива (`pauses_from_schedule`). Читать schedule только у строк без спавна и без файла OpenDota, не у всех архивов.
- Паузы неизвестны (`None`) — якорь не ставить. Голый horn больше не используется.
- Карты со спавном не читают паузы и не меняют якорь.

Тест на эту функцию: пауза 120 с на −40 вычитается, пауза до −90 нет, пустой список даёт −90. Существующие тесты `collect_quotes` не трогать.

## Данные

Кэш prices-history уже покрывает новый якорь (окно 6 часов, сдвиг 90–449 с). `make prices` не запускать: цель с `--fetch`, и `cache_covers_window` из-за сдвига `startTs` перекачает эти рынки зря.

1. Прогнать `s05a_fetch_prices_history.py` без `--fetch`.
2. `make catalog`. `horn_at` / `ended_at` не меняются, кэш `market_seconds` не удалять.
3. Сверить `pregame_quotes`: у строк со спавном `anchor_ts` и `radiant_prior` те же; у строк без спавна якорь меньше ровно на 90 + пауза в [−90, 0). Если из каталога выпадет больше нескольких карт (пара на новом якоре не сошлась), остановиться.
4. `make prepare`. `market-data` пропустит готовые кэши; перепишутся датасеты, в них `market_radiant_prior` и `market_vs_prior`.
5. Переобучить боевую модель (`make train`). Research-train этих карт не содержит, сентябрьский бэктест изменится, потому что фича prior на 163 картах валидации станет другой. Деплой на VPS в этот план не входит.
