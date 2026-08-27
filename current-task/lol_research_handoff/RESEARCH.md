# LoL × Polymarket research handoff

Дата среза: **2026-08-23**. Агент: Lol Researcher. Цель: бесплатные pro LoL ряды gold / XP / kills, стыковка с рынками Polymarket, потом свой лайв-коллектор.

В этой папке только описание и скрипты. Большие дампы (архив карт, HTML-вьюер) лежат отдельно, если они у тебя уже скачаны.

---

## Коротко: что чем закрывать

| Задача | Источник | Частота | XP | Бесплатно |
|---|---|---|---|---|
| История gold / XP / kills | Leaguepedia V5 Timeline | 60с кадры, киллы в мс | да | да, CC-BY-SA |
| Лайв gold / kills / level / CS / HP | `feed.lolesports.com/livestats` | ~1с внутри 10с окон, задержка 20–25с | **нет** | да, неофициальный сайт-фид |
| Лайв счёт / статус рынка | Gamma + `wss://sports-api.polymarket.com/ws` | событие (сменился счёт) | нет | да |
| Миникарта на странице PM | GRID widget по `gridSeriesId` | как у эфира | нет как данные | визуал, не API |
| Живые позиции / «быстрее эфира» | коммерческий GRID LoL | high-frequency, quote-only | XP не подтверждён | нет. Open Access = только CS2 + Dota 2 |

**Публичный потолок по скорости:** livestats ≈ официальный Twitch ≈ HUD Polymarket, все ~20–25с от реальной игры. Быстрее только платный GRID / портал команд / левые стримы.

---

## Контекст ресерча (что отбросили)

- Аналога OpenDota/Stratz для LoL нет. Riot Timeline (Match-v5) для ranked: gold/XP каждые 60с, pro-игры на `ESPORTSTMNT` / `LOLTMNT` в публичный Match-v5 обычно не попадают.
- GRID Open Access: CS2 + Dota 2. LoL / Valorant / R6 «will not work with your access level». Коммерческий LoL — заявка, без прайса. LPL часто без `gridSeriesId` на PM. LCK у Riot «media only».
- PandaScore: 0€ календарь, 400€ постматч без кадров, 1000€ Basic Live ~2с без XP. Не все академии/ERL. Для беттинга published stats нельзя.
- Oracle’s Elixir: 10/15/20 + конец — слишком редко.
- Replay / VOD OCR: pro `.rofl` нет, XP с оверлея не достать.
- lolesports старый WS `livestats.proxy` / `issueToken` мёртв. `details-iso` 404. `api.lolesports.com` DNS fail.

---

## История

### Как собирали

1. Список матчей PM: `GET https://gamma-api.polymarket.com/events?tag_id=65&limit=100&offset=…&order=startTime&ascending=false`  
   Тег `65` = league-of-legends. Оставляем только `LoL: A vs B (BOx)`, фьючерсы сезона выкидываем.
2. Карты из рынков: `Game N Winner` → `conditionId`, `condition_source=game_winner`. Решающая карта BO3/BO5 часто без своего Game N — берём series moneyline `Match Winner`, `condition_source=series_moneyline`.
3. Стыковка с Leaguepedia **не через Cargo MatchSchedule** (джойн ломался). Рабочая таблица: Cargo **ScoreboardGames** за месяц (`lp_sg_aug.json`).
4. Серия = `GameId` без хвоста `_N` (`…_Round 3_2_1` → `…_Round 3_2`). Карта N = N-я игра серии (`Gamename` или суффикс).
5. Таймлайн: страница `V5 data:{platform} {gameId}/Timeline` **с пробелом**, например `V5 data:LOLTMNT02 449945/Timeline`. Потом V4. Fandom `api.php` режет rate; CargoExport стабильнее для таблиц.

Скрипт: `scripts/build_pm_archive.py`  
Пути внутри захардкожены на `/workspace` (`OUT`, `CACHE_DIR`, входы `pm_lol_100_full.json`, `lp_sg_aug.json`). Перед запуском поменяй.

### Матчинг PM ↔ Leaguepedia

- Парс тайтла: `LoL: A vs B (BOx) - лига`.
- Нормализация имён: lowercase, без акцента, выкинуть `esports/gaming/team/…`, словарь `ALIASES`.
- `pair_score(A,B,Team1,Team2) = max(score(A,T1)+score(B,T2), score(A,T2)+score(B,T1))`.
- Окно даты ±60 часов от `gameStartTime` PM (иначе `endDate`/`startDate`).
- Порог `>= 1.15`, из кандидатов лучший скоринг, потом ближайшая дата, предпочтение сериям у которых уже есть игры.
- На выходе в JSON карты: `leaguepedia_match`, `leaguepedia_game.RiotPlatformGameId`, `timeline`.

Несыгранные карты BO5 после 3–0 остаются строками (рынок есть), таймлайна нет, `note = leaguepedia match found but this map not played / not listed`. Дата в вьюере: старт карты, иначе старт серии.

### Что в историческом JSON

`timeline.frame_interval_ms = 60000`

```
minutes[].players: participantId, totalGold, currentGold, xp, level, minionsKilled, jungleMinionsKilled
kills[]: timestamp_ms, killerId, victimId, assists
```

На минуте 0 у всех `totalGold=500`, `xp=0` — старт игры, не баг. На минуте 1 XP ещё часто 0: миньоны ~1:05, первый опыт после 1:20–1:40. К минуте 2 XP почти у всех.

Срез 2026-08-23: 100 ивентов, 362 строки карт, 173 с таймлайном.

---

## Лайв

### Что использовать

`scripts/livestats_client.py` и `scripts/pm_to_livestats.py`.

Discovery (нужен публичный ключ сайта lolesports.com):

```
GET https://esports-api.lolesports.com/persisted/gw/getLive?hl=en-US
GET …/getSchedule?hl=en-US&leagueId={id}
GET …/getLeagues?hl=en-US
GET …/getEventDetails?hl=en-US&id={matchId}
Header: x-api-key: 0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z
```

Это ключ фронта lolesports.com, он же в [vickz84259/lolesports-api-docs](https://vickz84259.github.io/lolesports-api-docs/). Не Riot Developer API. Для личного ресерча ок, продукт/реселл — серая зона ToS.

Фид (без ключа):

```
GET https://feed.lolesports.com/livestats/v1/window/{gameId}?startingTime=YYYY-MM-DDTHH:MM:SS.000Z
GET https://feed.lolesports.com/livestats/v1/details/{gameId}?startingTime=…
```

Правила, проверенные 2026-08-23:

- `startingTime` кратен 10 секундам.
- Окно `[T, T+10s]`, конец окна не моложе ~20с (`ahead of broadcast`).
- Без `startingTime` — первые ~10 кадров игры, не лайв.
- Практический полл: каждые 10с `T = floor(now_utc - 30s)` по 10с. Window + details.
- В 10с срезе десятки кадров, золото/HP прыгают примерно раз в секунду. Это не «один кадр в 10с».
- История сейчас живёт дни–месяцы (LEC апрель 2026 ещё отдавался). Не закладывайся: копи сам.

### Поля window

Команды: `totalGold`, `totalKills`, `towers`, `inhibitors`, `barons`, `dragons`.  
Игрок: `participantId`, `totalGold`, `level`, `kills`, `deaths`, `assists`, `creepScore`, `currentHealth`, `maxHealth`.  
Мета: `summonerName`, `championId`, `role`, `esportsPlayerId`.

### Поля details

Предметы, варды, KP, damage share, AD/AP/AS/armor/MR, руны, порядок способностей.  
**Нет:** `xp`, координат, herald/voidgrubs/atakhan.

Сэмплы одного кадра: `samples/livestats_window_one_frame.json`, `samples/livestats_details_one_frame.json`.

### Покрытие livestats

~47 лиг на `getLeagues`. Есть LCK/LEC/LPL/LCS/LCP, челленджеры, ERL (LFL, NLC, Prime, LES, Hitpoint, LPLOL, Circuito Desafiante, …).  
На Polymarket из сотни **не** будет фида у T4 вроде Equal eSports Cup, Nexus League. LPL на livestats есть, даже если на PM `gridSeriesId = null`.

---

## Матчинг PM ↔ livestats

Общего id нет. `gridSeriesId` и `pandascoreMatchId` Riot не понимает.

1. Тайтл `LoL: A vs B (BOx) - лига` + `eventMetadata.league`.
2. Лига → slug из таблицы в `pm_to_livestats.py` (`LFL` → `lfl`, `LPLOL` → `liga_portuguesa`, `NACL` → `nacl`).
3. Если `live`: `getLive`, тот же `pair_score >= 1.15`.
4. Иначе `getSchedule?leagueId=…`, команды + дата.
5. Карта N = `match.games[n].id` → это `gameId` для window/details.

Пример 2026-08-23: `lol-th-gx-2026-08-23` map 2 → `115548681803406125`.

`pandascoreMatchId` удобен только чтобы склеить PM sports WS (`gameId` там = pandascore). К livestats не ведёт.

---

## Polymarket / GRID (лайв-страница)

- HUD рисуется если `eventMetadata.gridSeriesId` не null. Лоадер: `https://cdn.grid.gg/widgets/latest/embed.js`, `grid.loadWidget({ scope: { type: "series", id } })`.
- В сотне ивентов `gridSeriesId` был у ~45–54. LPL часто null. Без id — только счёт, без миникарты.
- Публично: Gamma `score` вида `4-4|0-1|Bo3` и WS `wss://sports-api.polymarket.com/ws` — только статус/счёт, не золото.
- `api.grid.gg` GraphQL без контракта `UNAUTHENTICATED`. Open Access LoL не открывает.
- Замер на LEC TH vs GX: HUD (киллы/золото) ≈ livestats ≈ Twitch, ~20–25с. Строка `score` в Gamma отставала (писала 4–4, когда HUD и API уже были 8–5). Циферблат виджета прыгал, на него не равняться.

---

## Задержка (замеры 2026-08-23, LEC G2 GX vs TH)

- Livestats: свежий кадр на 20–25с позади стены. Окно моложе ~20с — 400.
- twitch.tv/lec: золото/счёт совпали со свежим кадром API. Стрим ↔ API ≈ 0–10с.
- HUD Polymarket: то же. Отдельной дыры нет.

Синхронизировали по золоту и киллам в одну секунду UTC, не по цифрам часов на оверлее (их легко прочитать неправильно).

---

## Скрипты

| Файл | Зачем |
|---|---|
| `scripts/build_pm_archive.py` | История: PM 100 ивентов + ScoreboardGames → JSON на карту + CSV + таймлайны V5 |
| `scripts/livestats_client.py` | Лайв: getLive / window / details |
| `scripts/pm_to_livestats.py` | Стык slug PM + номер карты → `gameId` livestats |

Зависимости: только stdlib (`urllib`, `json`, `csv`). Python 3.

```
python3 scripts/livestats_client.py
python3 scripts/pm_to_livestats.py lol-th-gx-2026-08-23 2
```

`build_pm_archive.py` ждёт локальные дампы:

- `pm_lol_100_full.json` — ивенты Gamma с рынками
- `lp_sg_aug.json` — ScoreboardGames
- опционально `lp_matchschedule_aug.json`

HTML-вьюер (`pm_lol_viewer.html`) был самодостаточный файл ~8MB: таблица как CSV, клик → минуты или JSON, сортировка по дате Madrid. Его нет в этом зипе из-за размера.

---

## Полезные URL

```
https://gamma-api.polymarket.com/events?tag_id=65&order=startTime&ascending=false
https://gamma-api.polymarket.com/events/slug/{slug}
https://gamma-api.polymarket.com/tags/slug/league-of-legends
https://docs.polymarket.com/market-data/websocket/sports

https://lol.fandom.com/wiki/Special:CargoExport
# timeline title: V5 data:LOLTMNT02 449945/Timeline

https://esports-api.lolesports.com/persisted/gw/getLive?hl=en-US
https://feed.lolesports.com/livestats/v1/window/{gameId}?startingTime=2026-08-23T16:28:00.000Z

https://grid.gg/open-access/
https://grid.helpjuice.com/client-help/open-access-quickstart
https://grid.gg/polymarket-partners-with-grid/
https://riotesportsdata.com/league-of-legends
```

---

также в html есть удобный просмотр 100 матчей с лол.
