# Pre-Telonex audit (US-004)

Date: 2026-08-27
Host: macbookDmitrii.local
Gamma VPN: unknown (Stage 01 already complete on this host; precheck not re-run)
Commands: `01 --fetch` → `01` replay (cache only) → `03 --fetch` → `04` (still running in another session; `download_audit.parquet` not written yet)
Snapshot: 2026-08-27T17:05:51Z

All counts below are from the parquet / gzip files on disk. Nothing is estimated. Stage 04 is in progress; window file counts are a point-in-time snapshot and will change until the run finishes. Final downloaded / failed / `complete` flags belong in the US-008 final report.

## GO gate

The paid phase (Telonex) **has been approved**. User GO given 2026-08-27; Telonex subscription purchased.

Stage 04 was still completing at report time (`download_audit.parquet` absent; 2816 / 4992 published window archives). Stages 02 / 05 / 06 files do not exist yet:

```
src/lol/01_build_universe.py
src/lol/03_link_lolesports.py
src/lol/04_fetch_lolesports.py
src/lol/lolesports_match.py
```

No ML row counts: `market_radiant_prior` / `market_p_radiant` / 300-second target require Telonex books (Stage 05).

---

## 1. Stage 01 — Polymarket universe

Source: `data/lol/processed/universe/markets.parquet`. CLI (`US-004-01.log`) and cache replay (`US-004-01-replay.log`) print identical totals; both match parquet.

| | n |
|---|---:|
| Gamma events (open+closed tag 65) | 3403 |
| Markets (all rows) | 78327 |
| Included = supported **and** resolved (`included=True`) | 7731 |
| Included PM events | 2949 |
| `resolved_outcome` non-null | 7731 |
| `unresolved_market` | 402 |
| Reason counts sum == markets | 78327 = 78327 |

Included markets are exactly `game_winner` + `match_winner_decider` after a strict 1/0 Gamma resolution. `unresolved_market` is excluded.

**Included by reason**

| reason | n |
|---|---:|
| game_winner | 4783 |
| match_winner_decider | 2948 |
| **included total** | **7731** |

**Excluded by reason** (no silent remainder)

| reason | n |
|---|---:|
| unsupported_contract | 69938 |
| unresolved_market | 402 |
| unsupported_bo2 | 192 |
| series_only | 32 |
| malformed_tokens | 24 |
| missing_best_of | 8 |
| **excluded total** | **70596** |

Included `scheduled_ts` range: 2025-10-05 16:00 UTC → 2026-08-27 08:00 UTC.

Gamma cache: 35 pages (`tag_65_closed` 33 + `tag_65_open` 2).

---

## 2. Stage 03 — lolesports link

Sources: `games.parquet`, `links.parquet`, `lolesports_links/audit.parquet`. CLI (`US-004-03.log`) matches parquet: `games: 11859`, `pm_events: 2949`, `accepted_links: 4992`. Printed `accepted: 7371` = 2379 event-scope + 4992 map-scope.

| grain | n |
|---|---:|
| Completed schedule maps in `games.parquet` | 11859 |
| lolesports series in games | 5268 |
| getSchedule pages / event-detail files / anchors | 69 / 5317 / 11859 |
| PM events attempted (event-scope audit = included events) | 2949 |
| **Series accepted** | **2379** |
| **Series ambiguous** (`multiple_eligible_series`, `series_claim_tie`) | **0** |
| **Series unmatched** | **570** |
| **Maps accepted** (`links.parquet`) | **4992** |
| Map-scope `reason=accepted` | 4992 (equals links) |
| Maps ambiguous (`orientation_ambiguous`) | 87 |
| Maps unmatched (map-scope `reason != accepted`) | 109 |
| Events with no map-scope rows | 570 (all `no_candidates_in_window`) |

Link assignment on accepted maps: `game_winner` 3735, `match_winner_decider` 1257.

**Event-scope reasons** (one row per included PM event)

| reason | n | bucket |
|---|---:|---|
| accepted | 2379 | accepted |
| no_candidates_in_window | 570 | unmatched |
| multiple_eligible_series | 0 | ambiguous |
| series_claim_tie | 0 | ambiguous |

**Map-scope reasons** (only events that received a unique series)

| reason | n | bucket |
|---|---:|---|
| accepted | 4992 | accepted |
| orientation_ambiguous | 87 | ambiguous |
| unsupported_fallback | 12 | unmatched |
| unresolved_market | 10 | unmatched |

---

## 3. Stage 04 — livestats windows

**Run still in progress.** `data/lol/processed/lolesports/download_audit.parquet` **does not exist** (written at process end). Failed / `complete` / `wall_time_limit` / `http_404` / `empty_body` / `retries_exhausted` counts are therefore unknown. Final numbers land in the US-008 report.

Source of progress: file counts under `data/lol/raw/lolesports/windows/` at **2026-08-27T17:05:51Z**.

| | n |
|---|---:|
| Accepted maps (download jobs) | 4992 |
| Published `*.jsonl.gz` | **2816** |
| In-flight `*.partial` | 127 |
| `*.tmp` | 0 |
| Links still without a published archive | 2176 |
| Published / accepted | 56.4% |
| Window files whose id is not in `links.parquet` | 0 |

Spot-check (10 archives: first 3, last 2, 5 random): every file was valid gzip JSONL; `esportsGameId` matched the filename; top-level keys `esportsGameId`, `esportsMatchId`, `frames`, `gameMetadata`; each sampled `frames[0]` had `blueTeam`, `gameState`, `redTeam`, `rfc460Timestamp`. Prefix line counts ~121–167 window objects per file.

Download order looks chronological: at a 2813-file snapshot a few seconds earlier, 2025Q4 and 2026Q1 linked maps were fully published; 2026Q2 was partial; 2026Q3 had none.

| quarter | linked maps | published jsonl.gz (≈17:05Z) |
|---|---:|---:|
| 2025Q4 | 75 | 75 |
| 2026Q1 | 1576 | 1576 |
| 2026Q2 | 1827 | 1162 |
| 2026Q3 | 1514 | 0 |

---

## 4. Coverage by league and calendar split

Split proxy = UTC calendar quarter of `games.start_ts`. League key = `league_slug` (fallback `league_name`). Coverage below is **linked maps** (stable). Downloaded-by-league is omitted: Stage 04 is mid-run.

**Linked maps by quarter**

| quarter | linked maps |
|---|---:|
| 2025Q4 | 75 |
| 2026Q1 | 1576 |
| 2026Q2 | 1827 |
| 2026Q3 | 1514 |
| **total** | **4992** |

**Linked maps by month**

| month | n | month | n |
|---|---:|---|---:|
| 2025-10 | 65 | 2026-04 | 780 |
| 2025-11 | 10 | 2026-05 | 805 |
| 2026-01 | 508 | 2026-06 | 242 |
| 2026-02 | 674 | 2026-07 | 540 |
| 2026-03 | 394 | 2026-08 | 974 |

**Linked maps vs all completed schedule maps, by `league_key`**

| league_key | games | linked | unlinked |
|---|---:|---:|---:|
| lpl | 1126 | 502 | 624 |
| lck | 825 | 384 | 441 |
| lck_challengers_league | 794 | 214 | 580 |
| emea_masters | 646 | 264 | 382 |
| lec | 497 | 335 | 162 |
| lcp | 467 | 276 | 191 |
| lfl | 462 | 238 | 224 |
| arabian_league | 443 | 164 | 279 |
| primeleague | 440 | 233 | 207 |
| nacl | 419 | 201 | 218 |
| hitpoint_masters | 380 | 169 | 211 |
| cd | 357 | 128 | 229 |
| roadoflegends | 340 | 213 | 127 |
| turkiye-sampiyonluk-ligi | 321 | 188 | 133 |
| les | 318 | 170 | 148 |
| esports_balkan_league | 313 | 113 | 200 |
| rift_legends | 304 | 90 | 214 |
| nlc | 290 | 104 | 186 |
| hellenic_legends_league | 287 | 22 | 265 |
| south_regional_league | 279 | 15 | 264 |
| ljl-japan | 262 | 80 | 182 |
| north_regional_league | 253 | 34 | 219 |
| liga_portuguesa | 248 | 96 | 152 |
| lit | 241 | 152 | 89 |
| pcs | 239 | 0 | 239 |
| cblol-brazil | 225 | 165 | 60 |
| lcs | 203 | 144 | 59 |
| vcs | 176 | 0 | 176 |
| msi | 151 | 59 | 92 |
| lta_s | 136 | 0 | 136 |
| lta_n | 135 | 0 | 135 |
| worlds | 84 | 72 | 12 |
| kespa_cup | 61 | 61 | 0 |
| ewc_lol | 51 | 49 | 2 |
| first_stand | 45 | 45 | 0 |
| lta_cross | 22 | 0 | 22 |
| americas_cup | 19 | 12 | 7 |
| **total** | **11859** | **4992** | **6867** |

Leagues with games but **zero** links (matching loss, not a missing download): `pcs` (239), `vcs` (176), `lta_s` (136), `lta_n` (135), `lta_cross` (22). `ldl` is not in this table — it never appears in `games.parquet` (see §5).

**Linked maps by league_key × quarter**

| league_key | 2025Q4 | 2026Q1 | 2026Q2 | 2026Q3 |
|---|---:|---:|---:|---:|
| americas_cup | | 12 | | |
| arabian_league | | 114 | | 50 |
| cblol-brazil | | 55 | 72 | 38 |
| cd | 3 | 7 | 61 | 57 |
| emea_masters | | 151 | 113 | |
| esports_balkan_league | | 61 | 4 | 48 |
| ewc_lol | | | | 49 |
| first_stand | | 45 | | |
| hellenic_legends_league | | 12 | 8 | 2 |
| hitpoint_masters | | 98 | 38 | 33 |
| kespa_cup | | | | 61 |
| lck | | 56 | 224 | 104 |
| lck_challengers_league | | 61 | 97 | 56 |
| lcp | | 102 | 101 | 73 |
| lcs | | 44 | 62 | 38 |
| lec | | 124 | 122 | 89 |
| les | | 14 | 79 | 77 |
| lfl | | 86 | 82 | 70 |
| liga_portuguesa | | 23 | 26 | 47 |
| lit | | 64 | 62 | 26 |
| ljl-japan | | 20 | 47 | 13 |
| lpl | | 163 | 192 | 147 |
| msi | | | 6 | 53 |
| nacl | | 13 | 127 | 61 |
| nlc | | 54 | 2 | 48 |
| north_regional_league | | | | 34 |
| primeleague | | 45 | 95 | 93 |
| rift_legends | | 24 | 42 | 24 |
| roadoflegends | | 71 | 79 | 63 |
| south_regional_league | | | | 15 |
| turkiye-sampiyonluk-ligi | | 57 | 86 | 45 |
| worlds | 72 | | | |

**PM included markets by `universe.league`** (title-suffix; not the lolesports slug). Top rows; 352 have null/empty league.

| universe.league | included markets |
|---|---:|
| LPL | 562 |
| LCK | 463 |
| LCK Challengers League | 437 |
| Esports World Cup | 358 |
| (none) | 352 |
| EMEA Masters | 348 |
| LEC | 333 |
| Prime League 1st Division | 305 |
| North American Challengers League | 269 |
| LCP | 244 |
| LES | 233 |
| CBLOL | 230 |
| LCS | 215 |
| LFL | 213 |
| Rift Legends | 193 |
| TCL | 192 |
| Road Of Legends | 189 |
| Circuito Desafiante | 186 |
| Hitpoint Masters | 142 |
| NLC | 134 |
| LJL | 126 |
| LPLOL | 112 |
| EBL | 110 |
| LIT | 108 |
| Arabian League | 103 |
| Mid-Season Invitational | 87 |
| LPL Group Ascend | 87 |
| LCP Regular Season | 84 |
| LCK Cup Group Stage | 77 |
| Arabian League Group Stage | 75 |
| KeSPA Cup | 68 |
| Asia Masters | 67 |
| LEC Versus Regular Season | 66 |
| LCK Challengers League Kickoff Group Stage | 65 |
| Hitpoint Masters Group Stage | 59 |
| First Stand | 56 |
| LRN | 46 |
| LPL Group Perseverance | 39 |
| LCS Lock In Group Stage | 36 |
| EBL Regular Season | 36 |
| LIT Playoffs | 35 |
| LPL Group Nirvana | 33 |
| HLL | 29 |
| LPL Knights Rivals | 28 |
| CBLOL Cup Regular Season | 28 |
| *remaining 38 labels* | 399 |
| **included total** | **7731** |

`LPLOL` is Liga Portuguesa (slug `liga_portuguesa`), not LPL. Word-boundary `\blpl\b` does not match it.

---

## 5. LPL / LDL

`LOL_LEAGUE_SLUGS` includes both `lpl` and `ldl`. Presence is decided from parquet / schedule cache, not from that constant.

### LPL — present

| | n |
|---|---:|
| `games.parquet` slug/name `lpl` | 1126 maps / 420 series |
| Linked LPL maps | 502 |
| Distinct PM events among those links | 192 |
| PM included markets whose `league` matches `\blpl\b` | 749 (230 events) |
| Published LPL window archives at 17:05Z | 278 |

PM included LPL labels: LPL 562, LPL Group Ascend 87, LPL Group Perseverance 39, LPL Group Nirvana 33, LPL Knights Rivals 28.

getSchedule cache: slug `lpl` on 437 schedule events (of 69 pages). Linked 502 / 1126 LPL games: the 624 unlinked LPL maps are matching loss (no unique PM event), not a missing download.

### LDL — absent

Zero everywhere. First matching explanation: **no Polymarket included (or excluded) market is tagged LDL, and getSchedule in the Gamma window never yielded an `ldl` league slug or name.**

| check | n |
|---|---:|
| `games.league_slug` / `league_name` matching `\bldl\b` | 0 / 0 |
| Linked LDL maps | 0 |
| Published LDL window archives | 0 |
| PM included `league` matching `\bldl\b` | 0 |
| PM **all** markets `league` matching `\bldl\b` | 0 |
| PM `league` containing `ldl` / developmental / academy / youth | 0 |
| getSchedule pages with league blob containing `ldl` | 0 |
| getSchedule events with slug `ldl` | 0 |
| Distinct schedule slugs | 38 (no `ldl`) |

This is a coverage hole in both Gamma tag 65 and the lolesports schedule for the universe time range. Not a Stage 04 failure and not a Riot V5/Cargo issue.

---

## 6. Stable loss reasons (funnel)

Do not read these as predicted Stage 05 row counts.

```
Gamma markets                         78327
  excluded (universe reasons)         70596
    unsupported_contract              69938
    unresolved_market                   402
    unsupported_bo2                     192
    series_only                          32
    malformed_tokens                     24
    missing_best_of                       8
  included/resolved markets            7731
    game_winner                        4783
    match_winner_decider               2948
  included PM events                   2949
  series accepted                      2379
  series ambiguous                        0
  series unmatched                      570  (all no_candidates_in_window)
  maps assigned (links)                4992
  maps dropped (map-scope)              109
    orientation_ambiguous                87
    unsupported_fallback                 12
    unresolved_market                    10
  windows complete / failed               unknown (download_audit.parquet not written)
  windows published so far             2816  (in progress; US-008)
```

Every dropped count uses a `REASON_*` string from `src/shared/constants/lol.py`. No unknown reason appeared in either parquet.

---

## 7. Integrity checks

| check | result |
|---|---|
| `markets.parquet` readable, columns = `LolUniverseMarketRow` | pass |
| `games` / `links` / `audit` columns = matching TypedDicts | pass |
| duplicate `condition_id` / non-null `market_id` | none |
| duplicate `games.esports_game_id` | none |
| duplicate `links.esports_game_id` | none |
| duplicate `links (event_id, game_number)` | none |
| every link `esports_game_id` ∈ games | pass |
| `len(links) == map-scope accepted` | 4992 = 4992 |
| event-scope rows == included events | 2949 = 2949 |
| Stage 01 CLI == parquet == cache replay | pass |
| Stage 03 CLI == parquet | pass |
| Stage 03 cache-only replay log | not present in `current-task/logs/` (not re-run this session) |
| Gamma page schema (`/events/keyset`, tag 65, limit 500) | pass (5 pages sampled) |
| getSchedule page `data.schedule.events` | pass |
| one event-details gzip has match/games | pass (`113470922758018585.json.gz`, 5 games) |
| one anchor json has `esportsGameId` + `frames` | pass (10 frames) |
| window JSONL gzip schema (10 files) | pass |
| Stage 04 complete flags / `download_audit` uniqueness | **N/A — file missing** |
| Stage 04 skip-HTTP replay | **not run; Stage 04 still in progress** |
| `src/lol/02_fetch_telonex_books.py` / `05_prepare_dataset.py` / `06_train_model.py` | absent |

---

## 8. Evidence for the reviewer

| artifact | path |
|---|---|
| This report | `betting_workspace/current-task/pre_telonex_audit.md` |
| Stage 01 log | `current-task/logs/US-004-01.log` |
| Stage 01 replay | `current-task/logs/US-004-01-replay.log` (totals identical) |
| Stage 03 log | `current-task/logs/US-004-03.log` |
| Stage 04 log | not in `current-task/logs/` (run is in another session) |
| Universe | `dota_2_model/data/lol/processed/universe/markets.parquet` |
| Games / links / link audit | `.../lolesports/games.parquet`, `.../lolesports_links/{links,audit}.parquet` |
| Windows | `dota_2_model/data/lol/raw/lolesports/windows/{esportsGameId}.jsonl.gz` |
| Download audit | **missing** (expected until Stage 04 exits) |

---

## Appendix — pandas snippets used

Run from `dota_2_model` with `PYTHONPATH=src` (`uv run python`). Numbers in the report are the stdout of these blocks (plus `Path.glob` file counts for Stage 04).

### A1. Universe

```python
import pandas as pd
from shared.constants.lol import LOL_UNIVERSE_PATH

m = pd.read_parquet(LOL_UNIVERSE_PATH)
inc = m[m["included"] == True]
print("markets_total", len(m))
print("events_total", m["event_id"].nunique())
print("supported_resolved_markets", len(inc))
print("supported_resolved_events", inc["event_id"].nunique())
print("included_by_reason")
print(inc["reason"].value_counts().sort_index().to_string())
print("excluded_by_reason")
print(m.loc[m["included"] != True, "reason"].value_counts().sort_index().to_string())
print("unresolved_market", int((m["reason"] == "unresolved_market").sum()))
print("resolved_label_nonnull", int(m["resolved_outcome"].notna().sum()))
```

### A2. Linking

```python
import pandas as pd
from shared.constants.lol import LOL_GAMES_PATH, LOL_LINKS_PATH, LOL_LINK_AUDIT_PATH

games = pd.read_parquet(LOL_GAMES_PATH)
links = pd.read_parquet(LOL_LINKS_PATH)
audit = pd.read_parquet(LOL_LINK_AUDIT_PATH)
ev = audit[audit["scope"] == "event"]
mp = audit[audit["scope"] == "map"]
AMBIGUOUS_EVENT = {"multiple_eligible_series", "series_claim_tie"}
ACCEPTED = "accepted"
print("lolesports_completed_schedule_maps_in_games", len(games))
print("lolesports_series_in_games", games["esports_match_id"].nunique())
print("pm_events_attempted", len(ev))
print("series_accepted", int((ev["reason"] == ACCEPTED).sum()))
print("series_ambiguous", int(ev["reason"].isin(AMBIGUOUS_EVENT).sum()))
print("series_unmatched", int((~ev["reason"].isin({ACCEPTED}) & ~ev["reason"].isin(AMBIGUOUS_EVENT)).sum()))
print("event_reason_counts")
print(ev["reason"].value_counts().sort_index().to_string())
print("maps_accepted_links", len(links), "map_scope_accepted", int((mp["reason"] == ACCEPTED).sum()))
print("maps_ambiguous_orientation", int((mp["reason"] == "orientation_ambiguous").sum()))
print("maps_unmatched", int((mp["reason"] != ACCEPTED).sum()))
print("map_reason_counts")
print(mp["reason"].value_counts().sort_index().to_string())
has_map = ev["event_id"].isin(set(mp["event_id"]))
print("events_without_map_rows", int((~has_map).sum()))
```

### A3. Windows (audit missing — file counts)

```python
from pathlib import Path
import pandas as pd
from shared.constants.lol import LOL_DOWNLOAD_AUDIT_PATH, LOL_WINDOWS_DIR, LOL_LINKS_PATH

print("download_audit_exists", LOL_DOWNLOAD_AUDIT_PATH.exists())
files = list(LOL_WINDOWS_DIR.glob("*.jsonl.gz"))
partials = list(LOL_WINDOWS_DIR.glob("*.partial"))
links = pd.read_parquet(LOL_LINKS_PATH)
file_ids = {p.name.replace(".jsonl.gz", "") for p in files}
link_ids = set(links["esports_game_id"].astype(str))
print("jsonl_gz_files", len(files))
print("partial_files", len(partials))
print("window_files_in_links", len(file_ids & link_ids))
print("links_without_window_file", len(link_ids - file_ids))
```

`LOL_DOWNLOAD_AUDIT_PATH.exists()` was `False`. The plan’s `pd.read_parquet(LOL_DOWNLOAD_AUDIT_PATH)` block was not executed.

### A4. Coverage by league and quarter (linked maps)

```python
import pandas as pd
from shared.constants.lol import LOL_GAMES_PATH, LOL_LINKS_PATH, LOL_UNIVERSE_PATH

games = pd.read_parquet(LOL_GAMES_PATH)
links = pd.read_parquet(LOL_LINKS_PATH)
markets = pd.read_parquet(LOL_UNIVERSE_PATH)
g = games.copy()
g["start_ts"] = pd.to_numeric(g["start_ts"], errors="coerce")
start = pd.to_datetime(g["start_ts"], unit="s", utc=True)
g["quarter"] = start.dt.year.astype("Int64").astype(str) + "Q" + start.dt.quarter.astype("Int64").astype(str)
g["month"] = start.dt.strftime("%Y-%m")
g["league_key"] = g["league_slug"].fillna(g["league_name"]).fillna("(none)")
linked = g.merge(links[["esports_game_id"]], on="esports_game_id", how="left", indicator=True)
linked["linked"] = linked["_merge"] == "both"
print(g.groupby("league_key").size().sort_values(ascending=False).to_string())
print(linked[linked["linked"]].groupby("league_key").size().sort_values(ascending=False).to_string())
print(linked[linked["linked"]].groupby("quarter").size().sort_index().to_string())
print(linked[linked["linked"]].groupby("month").size().sort_index().to_string())
print(linked[linked["linked"]].groupby(["league_key", "quarter"]).size().to_string())
inc = markets[markets["included"] == True]
print(inc["league"].fillna("(none)").value_counts().to_string())
```

The plan’s merge onto `download_audit.parquet` was skipped (file missing). Published-window × quarter used `jsonl.gz` filenames ∩ `links.esports_game_id` instead.

### A5. LPL / LDL

```python
import pandas as pd
from shared.constants.lol import LOL_GAMES_PATH, LOL_LINKS_PATH, LOL_UNIVERSE_PATH

def is_cn(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.lower()
    return s.str.contains(r"\blpl\b", regex=True) | s.str.contains(r"\bldl\b", regex=True)

def has_ldl(s):
    return s.fillna("").astype(str).str.lower().str.contains(r"\bldl\b", regex=True)

def has_lpl(s):
    return s.fillna("").astype(str).str.lower().str.contains(r"\blpl\b", regex=True)

games = pd.read_parquet(LOL_GAMES_PATH)
links = pd.read_parquet(LOL_LINKS_PATH)
markets = pd.read_parquet(LOL_UNIVERSE_PATH)
inc = markets[markets["included"] == True]
cn_games = games[is_cn(games["league_slug"]) | is_cn(games["league_name"])]
cn_links = links[links["esports_game_id"].isin(cn_games["esports_game_id"])]
print("pm_included_markets_league_lpl_ldl", int(is_cn(inc["league"]).sum()),
      "events", inc.loc[is_cn(inc["league"]), "event_id"].nunique())
print("games_lpl_ldl", len(cn_games))
print(cn_games["league_slug"].fillna("(none)").value_counts().to_string() if len(cn_games) else "(none)")
print("linked_lpl_ldl", len(cn_links))
print("games_slug_ldl", int(has_ldl(games["league_slug"]).sum()))
print("pm_all_league_ldl", int(has_ldl(markets["league"]).sum()))
print(inc.loc[has_lpl(inc["league"]), "league"].value_counts().to_string())
```

Schedule LDL check: walk `data/lol/raw/lolesports/schedule/page_*.json.gz`, count `league.slug == "ldl"` and `"ldl"` in the league JSON (both 0).
