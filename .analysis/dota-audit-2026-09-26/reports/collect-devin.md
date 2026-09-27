# collect-devin — collect fetchers, parsers, game-state data, raw-data provenance
Status: FINAL

## Summary

- **S2 — live GRID tables can carry a 6th roster row** (registered substitute) that `team_net_worth` sums into `radiant_nw`/`dire_nw`: verified on grid-3007267-m3 (all 691 tables show `team 53864: 6 rows`, the extra pinned at `nw=600, level=1, deaths=0`), and 17/553 trader archives (~3%) show a persistent extra-player side. Related: ~13% of sampled archives contain 1–2 degenerate all-zero table ticks. Train features (STRATZ, always exactly 10 players) never see either → live inputs drift off the trained distribution, invisible to backtest. Scope note: the summing code lives in `src/trader` (feed-devin), the collect-side evidence and train/live skew belongs here.
- **S2 — GRID `series_state` cache is write-once and holds mid-series snapshots forever**: `pending_state_ids` refetches only missing files; `load_grid_games` never checks `finished`. 7/2542 cached series are unfinished (61 unfinished games); a mid-game `clock.currentSeconds` can't equal the final OpenDota duration → those maps can never match → admission fails. Commit `db23b945` made "file exists = cache contract" explicit.
- **S2 — exact-duration GRID matching loses ~35% of clocked links**: 1763/4950 links with `grid_clock_seconds` never resolve (1351 have no GRID game inside `[start, start+1800]`, 412 have an in-window game whose cached clock ≠ duration). All 1763 fail the admission gate; they reach the catalog only if archive-attached.
- **S2 — `prepare_dataset` crashes on catalog matches with no Telonex tape**: `try_build_one_market_cache` silently returns when a book dir is missing; `load_match_inputs` then raises `FileNotFoundError`. Today 166 catalog matches (all train-era) have no book dirs under any cache version — a fresh dataset build dies on the first one.
- **S3 — the −60s (pre-horn) minute row is silently missing for 76% of the dataset**: 2437/2490 non-validation catalog matches have `player.playbackData = None`; `build_minute_states` needs playback NW only for second <0 and skips that row. Validation matches (717) all have it → train/validation row coverage differs systematically.
- **S3 — pregame prior mixes two anchor definitions**: spawn-anchored for GRID-windowed rows (≈90s + pre-horn pauses pre-horn; observed horn−spawn median 90s, p75 118s, max 1281s) vs horn-anchored for archive-only rows. Boundary-bar semantics of Polymarket `prices-history` (`t < anchor` on a ~60s-spaced series) let at most ~59s of post-anchor trading leak into archive-horn priors. 4 degenerate priors (0.9995/0.0005) and 3 priors stale >600s pass the gates.
- **S3 — STRATZ schema is frozen per cache file**: `select_pending_match_ids` only fetches missing files; 13,734 legacy `stratz_rich_v2` caches (no `stats`/playback) are dead weight, and a v2-cached match entering admission would crash `read_cache_summary` via `player["stats"]` KeyError.
- **S3 — STRATZ unusable drops are biased to long games**: 24 unusable (missing_leads 11, flat_edge 8, reset_tail 5); unusable p50 duration 3512s vs usable 2328s; league-clustered (18866×3, 19696×4, 19944×5, 20279×2).
- **OK — the exact-second NW reconstruction is causal and validated**: 367 (match,second) checks on 3 validation matches match an independent recompute; minute marks equal `networthPerMinute` at every mark. Market as-of reads never touch future snapshots.
- **OK — clock model is internally consistent**: STRATZ/OpenDota `duration`, event `time`, and GRID `clock.currentSeconds` are the same in-game clock (0 = horn); `startDateTime`/`start_time` is draft/lobby start (~791s before spawn, median), used only as a match-window anchor. OpenDota pauses verified consistent with wall-clock availability on real archives.

## Findings table

| ID | Sev | Layer | Title | Confidence | Impact |
|---|---|---|---|---|---|
| collect-devin-F1 | S2 | live feature def (train/serve skew) | GRID roster substitute row inflates one team's NW by ~+600 on ~3% of archived maps; related: ~1-2 degenerate all-zero table ticks on ~13% of archives | verified | live features wrong on phantom maps; invisible to STRATZ-based backtests |
| collect-devin-F2 | S2 | collect/cache | `series_state/*.json` never refetched; unfinished-series snapshots cached permanently | verified | maps inside stale series can never match → permanent catalog loss |
| collect-devin-F3 | S2 | collect/matching | exact `clock.currentSeconds == duration` matching + ±8h scheduled-start prefilter loses 1763/4950 links | verified | ~35% of OpenDota-linked maps never reach catalog without an archive |
| collect-devin-F4 | S2 | prepare_dataset | silent skip in market build → hard `FileNotFoundError` crash in dataset build (166 matches today) | verified | dataset build blocked until caches/manual cleanup; reason invisible |
| collect-devin-F5 | S3 | collect/STRATZ gate | unusable rules drop long games preferentially (p50 3512s vs 2328s), league-clustered | verified | small (0.7%) but duration-biased training loss |
| collect-devin-F6 | S3 | collect/prior | mixed anchor (spawn vs horn) + boundary-bar semantics + degenerate/stale priors pass gates | verified | prior feature inconsistent across sources; ≤~59s post-horn leak possible on archive rows |
| collect-devin-F7 | S3 | collect/STRATZ cache | cache profile never refreshed; 13.7k dead v2 files; stale v2 file for a newly-admitted match crashes the index build | verified | latent crash + permanent frozen `unusable` verdicts |
| collect-devin-F8 | S3 | prepare_dataset | `playback_available` checks match-level `playbackData`, but exact-second build needs per-player gold events; −60s row silently dropped on 2437 matches | verified | systematic missing pre-horn row in 76% of dataset; wrong-field gate is a latent crash |
| collect-devin-F9 | S3 | clock alignment | ~1s skew between STRATZ event clock and GRID live clock (live ≈ STRATZ−1 on 4/5 maps) | likely | ±1s feature parity noise; bounded |
| collect-devin-F10 | S4 | collect/pauses | `pauses_diverge` counts `time<-90` draft pauses OpenDota records but downstream ignores → warning noise; times themselves never compared | verified | log noise; semantic agreement unverified per-pause |
| collect-devin-F11 | S4 | artifacts | three `market_seconds` cache versions on disk (~455MB), no cleanup; 166 catalog matches have no cache under any version | verified | provenance confusion, disk waste |
| collect-devin-F12 | S4 | artifacts | dead raw dirs unreferenced by code: `events/` 295MB, `markets/` 14MB, `stratz_matching/` 9MB, `stratz_series/` empty | verified | leftover noise |
| collect-devin-F13 | S4 | collect/GRID windows | window rows persist only `{condition_id, spawn_at}` — no series/game id → wrong attaches unauditable | verified | debuggability |
| collect-devin-F14 | S4 | collect/GRID | `match_game` raises on >1 exact candidate — whole stage crashes on ambiguity | speculative | rare, but crashes are worse than skips |
| collect-devin-F15 | S4 | collect/GRID | series prefilter uses `startTimeScheduled` ±8h and index ±2d — postponed series silently lose coverage | likely | contributes to the 1351 no-window unmatched |
| collect-devin-F16 | S4 | collect/s05 | failed OpenDota fetches leave no marker; permanently-404 matches retried every run | verified | wasted quota, bounded 1900/day |
| collect-devin-F17 | S4 | collect/s05b | RICH_QUERY fetches ~25 event arrays/player + match playback; pipeline uses 4 → ~1.4GB cache mostly unused | verified | fetch time (2.5s×19k ≈ 13.5h) + disk; `firstBloodTime`/`towerDeaths` entirely unread |

## Findings detail

### collect-devin-F1 — GRID roster substitute row inflates one team's NW by ~+600 (S2, verified)

**Where**: `src/trader/grid_widgets.py:301-303` (`team_net_worth` sums every row with `team_id`), consumed by `src/trader/grid_feed.py:168-175` (`_live_snapshot`). Train counterpart: `src/prepare_dataset/stratz_seconds.py:184-196` sums exactly the 10 `match["players"]`.

**What**: GRID `series_table` player rows are keyed to the team roster, not to in-game entities. When a series roster carries a substitute, the table emits a 6th row per side pinned at `nw=600, level=1, deaths=0` for the whole map.

**Evidence**: `phantom_over_time.py` on `data/trader/grid-3007267-m3` — all 691 tables report `{'53864': 6, '58869': 5}`; `per_player_diff.py` shows side NWs at second 1488: GRID `[600, 4423, 4739, 10119, 14157, 18917]` vs STRATZ dire `[4426, 4742, 10123, 14159, 18919]` (real players match ±4). `phantom_all.py` over all 553 grid archives (first 40 tables each): 17 archives with a persistent ≥6-player side — e.g. `grid-2987018-{m1,m2,m4,m5}`, `grid-2996948-{m3,m4,m5}`, `grid-3007246-{m1,m2}`, `grid-3007267-m3`.

Note: the earlier hypothesis "disconnected players show as nw=600" is **refuted** — the disconnected radiant player on 9003856182 reports true NW in GRID (14368 vs STRATZ 14370 at s=1488). The phantom is a *substitute roster slot*, on the *other* team. STRATZ `leaverStatus=DISCONNECTED` appears on 2114 players / 1351 catalog matches (42%) and is harmless for features.

**Impact**: on phantom maps live `dire_nw`/`radiant_nw` and `nw_adv` are biased by exactly +600 (phantom's constant NW); `top1_nw_ratio` denominators shift +600; `xp_adv`/`deaths` unaffected (level 1 → 0 XP, 0 deaths). Constant offset all map — pushes live `nw_adv` off the STRATZ-trained distribution on ~3% of maps. Backtests can't see it: game-feature rows come from STRATZ (10 players). Also breaks GRID↔Steam parity: Steam `teams[].net_worth` is pre-aggregated and wouldn't include the roster slot, so feed choice changes the feature.

**Confirm/fix**: cap each side to 5 rows (or join rows to the scoreboard's player entities) in `team_net_worth`/`_live_snapshot`. Confirm by counting `len(players)>10` per table on the live feed.

**Related, same fix**: transient full-table degenerates — a table collapsing to a single bogus row (`nw=0`, junk team id like `55414`) — occur mid-stream in ~8/60 sampled archives (`grid-3005971-m2` table#582, `grid-3002533-m2` tables#354/358, `grid-2996030-m1` table#434). Since `_live_snapshot` filters by real `team_id`s, that tick emits an all-zero snapshot (`nw_adv=0`, `xp_adv=0`, `deaths=0`, top=0) — a degenerate inference tick indistinguishable from a real state. Requiring `len(radiant)==5 and len(dire)==5` before emitting would drop both failure modes.

### collect-devin-F2 — `series_state/*.json` never refetched; unfinished-series snapshots cached permanently (S2, verified)

**Where**: `src/collect/s04_fetch_grid_starts.py:385-401` (`pending_state_ids` = file-exists only), `404-428` (`load_grid_games` uses every game record, ignores `finished`), `438-449` (`match_game` reads `clock.currentSeconds`).

**What**: a series fetched while still running is cached forever with mid-game state. `currentSeconds`/`duration` for an unfinished game are the *live* values at fetch time, not final values — they can't equal an OpenDota `duration`, so maps in such series can never match.

**Evidence**: `series_state/2.json`: series `finished: false`, `updatedAt 2026-08-06T14:39:21Z`, game 2 `duration: PT3.4S`, `clock.currentSeconds: -84`. Scan of all 2542 files: **7 series unfinished, containing 61 unfinished games**. Commit `db23b945` ("Trust our own pipeline files: existence is the cache contract") established the policy; matcher itself from `01792958`.

**Impact**: permanent coverage loss for matches in stale series (subset of F3's unmatched); also a subtle wrong-data risk — a mid-game `currentSeconds` equal to *some other* link's duration could attach the wrong game (F14/F15; no observed instance: all 3187 windows have unique `spawn_at` and plausible spawn−start deltas 67–1053s).

**Confirm/fix**: refetch when `!finished` or `updatedAt` is recent; require `game.finished` (or use `game.duration` as the final clock) before the game may match.

### collect-devin-F3 — exact-duration GRID matching + scheduled-start prefilter loses ~35% of clocked links (S2, verified)

**Where**: `src/collect/s04_fetch_grid_starts.py:438-449` (`match_game`: exact `clock_seconds == grid_clock_seconds` AND `0 <= spawn_ts − match_start_time <= 1800`), `367-378` (`series_ids_near_links` ±8h on `startTimeScheduled`), `301-307` (`index_window` ±2d).

**Evidence**: `grid_unmatched.py`: 4950 links have `grid_clock_seconds`; 3187 matched; **1763 unmatched = 1351 no GRID game in spawn window + 412 in-window game with clock ≠ duration**. Downstream: `catalog_drops.py` first-failure `admission` = 1746 (the 1763 minus 17 archive-attached survivors).

**Mechanism**: (a) GRID series coverage gaps — series absent from the index or filtered out by the ±8h `startTimeScheduled` window (postponed matches) or ±2d index bounds; (b) stale mid-game `currentSeconds` (F2); (c) genuine clock-vs-duration definition differences (GRID clock may stop before OpenDota's `duration`); (d) drafts longer than 30min pushed `spawn − match_start_time` past 1800 (`match_start_time` is draft/lobby start — median spawn delta 791s, max observed 1053s).

**Impact**: each unmatched link fails `admitted_link_mask` (`window_ids.py:6-10`) unless archive-attached → the map never reaches the catalog → permanent dataset coverage loss, silently. This is the single largest drop stage: 1746/5046 links.

**Confirm/fix**: store `game_id`/`series_id` per window (F13), refetch unfinished states (F2), and consider near-tolerance matching (e.g. |clock−duration|≤N with tie-break by team ids) — evaluate on the 412 mismatches first.

### collect-devin-F4 — silent market-build skip → hard crash in `prepare_dataset` (S2, verified)

**Where**: `src/market_data/build_market_data.py:139-143` (`try_build_one_market_cache` returns silently when `build_market_second_rows` returns `None` — missing/empty book), `src/prepare_dataset/prepare_dataset.py:295-300` + `325` (`load_match_inputs` → `pd.read_parquet` on a path that may not exist, outside any guard).

**Evidence**: today: CACHE_VERSION `9c88adc2`; 166/3207 catalog matches have **no** `market_seconds` cache under *any* version dir (`v59b14c69`, `v9c88adc2`, `va75c29ad`); all 166 are train-era; spot-checked 6 — none have `data/raw/telonex/book_snapshot_full/asset_id=*` dirs. Verified live: `load_match_inputs(8494551495, catalog)` → `FileNotFoundError: .../v9c88adc2/match_id=8494551495`.

**Impact**: any full `prepare_dataset` run crashes on the first no-tape match (a catalog row looks complete — prior present, stratz usable — but has no book). The silent skip hides *why* the match has no market data (no collector coverage vs thin tape). Related commit `081fb493` removed the catalog gate on Telonex tapes, moving the failure downstream to a crash.

**Confirm/fix**: in `load_match_inputs`, treat a missing cache as "no market rows" (empty list) — `build_minute_rows`/`join_validation_rows` already skip seconds without market rows; and log the skip with the missing-book reason in `try_build_one_market_cache`.

### collect-devin-F5 — STRATZ unusable gate is duration- and league-biased (S3, verified)

**Where**: `src/shared/utils/stratz.py:86-100` (`stratz_match_unusable_reason`), applied in `src/collect/s05b_fetch_stratz_matches.py:188` → `stratz_status` → catalog `stratz` mask (`s06_publish_catalog.py:156`).

**Evidence**: `stratz_match_index`: 3300 admitted → usable 3276 / unusable 24 (`missing_leads` 11, `flat_edge` 8, `reset_tail` 5). Durations: unusable p50 3512s vs usable p50 2328s (p90 6451 vs 3285). Leagues: 18866×3, 19101×2, 19696×4, 19944×5, 20279×2 — clustered. Two rows (`8980994942`, `8984703068`) are null-match "missing_leads". The flat-edge heuristic comment ("real matches show longest prefix 3") is consistent with leads[0]≡0 semantics (see Clock answer below) — false-positive surface is small but nonzero.

**Impact**: 0.7% of admitted matches dropped; skews toward longer matches (parse resets/flat tails happen more in long games) → mild underrepresentation of long-game dynamics in training.

**Confirm/fix**: emit per-reason counts in the index (already in `reason` column — good), and add a duration/league bias note to the funnel report so drift is visible.

### collect-devin-F6 — pregame prior: mixed anchors, boundary-bar leak surface, degenerate quotes pass (S3, verified)

**Where**: `src/collect/s05a_fetch_prices_history.py:56-74` (`load_anchors`: spawn preferred, else archive horn), `src/shared/utils/price_history.py:48-57` (`last_pre_anchor_quote` takes `t < anchor`).

**What**: two different market states are collected under one feature name. Spawn-anchored priors are ≥90s pre-horn plus any pre-horn pauses (`horn − spawn`: median 90s, p75 118s, max 1281s on catalog rows); horn-anchored priors are ~0–60s pre-horn. Mixed into `radiant_prior` without a marker of which anchor was used (derivable: `anchor_ts` is stored — good).

**Evidence**: `funnel.py` on `pregame_quotes.parquet` (3272 rows): staleness median 31s, p75 45s, **max 4178s** (3 rows >600s); pair_gap p75 3s max 58s; **4 degenerate priors** at 0.9995/0.0005 (match_ids 8675672180, 8744253246, 8924126326, 8982330603 — e.g. a market already resolved in traders' minds pre-horn). Prices-history `t` values are ~60s-spaced and phase-locked to the request window (verified: `t % 60` is not 0) — so each `p` is a sample at `t`; if Polymarket's `p` is instead a per-interval bucket close, a bar stamped `t < anchor` can contain trades up to ~59s post-anchor — only material for horn-anchored rows (≤~59s of post-horn price action enters the "pregame" prior).

**Impact**: prior consistency is a feature-distribution issue (train/serve on the same definition, so no skew per se), but the two anchors differ by 1–5min of market drift; degenerate priors at 0/1 are near-certain label leaks of *market* belief (not outcome), and stale priors encode hours-old prices.

**Confirm/fix**: pick one anchor (horn is the better semantic: market's pregame state). Cap quote staleness (e.g. ≤600s). Flag or drop `prior ∈ {≈0, ≈1}` rows — check whether their markets actually resolved (possible link/condition mixups).

### collect-devin-F7 — STRATZ cache never refreshes; 13.7k dead v2 files; latent crash on stale profiles (S3, verified)

**Where**: `src/collect/s05b_fetch_stratz_matches.py:213-219` (`select_pending_match_ids`: file-exists only), `199-202` (`read_cache_summary` → `stratz_match_unusable_reason`), `src/shared/utils/dota_levels.py:39-42` (`player["stats"]["level"]` hard-indexes).

**Evidence**: 19,372 cache files: `stratz_rich_v2` 13,734 / `stratz_rich_v3` 5,638. All 3300 admitted matches are v3; all 3207 catalog matches v3+stats (verified by full scan). A v2 file for a newly-admitted match would be *skipped by fetch* (exists) then **crash** `scan_cache` — `is_level_timeline_monotonic` does `player["stats"]["level"]` → KeyError — because `stratz_match_unusable_reason` never expects a missing `stats` sub-object. Also `unusable` verdicts are permanent: a replay re-parsed later by STRATZ stays "unusable" forever (cache exists → never refetched).

**Impact**: ~71% of the STRATZ cache on disk is dead weight (v2, non-admitted ids from an older iteration); a profile/schema upgrade or a re-parse can't propagate without `--force`; one stale v2 on a new id crashes the index rebuild. Fetched span observed: 2026-07-25 → 2026-09-24.

**Confirm/fix**: gate `select_pending_match_ids` on `cache_profile == STRATZ_CACHE_PROFILE` (and optionally re-summary for `unusable`), or handle missing `stats` in `is_level_timeline_monotonic`.

### collect-devin-F8 — `playback_available` gates the wrong field; −60s row silently missing for 76% of the dataset (S3, verified)

**Where**: `src/collect/s05b_fetch_stratz_matches.py:193` (`playback_available = bool(match.get("playbackData"))` — match-level field), `src/prepare_dataset/stratz_seconds.py:146-159` (`_playback_networths` needs per-player `playbackData.playerUpdateGoldEvents`), `162-172` (`_minute_networths` uses playback only for second<0 → `continue` skips the row), `227-247` (`collect_player_playbacks` raises `ValueError` when `playbackData` is None), `s06_publish_catalog.py:157` (playback mask only for validation-age rows).

**Evidence**: catalog scan: validation 717/717 have full per-player gold events; non-validation 2437/2490 have `player.playbackData = None` (53 have them). On 8494551495/8494660292/8510883608, `build_minute_states` returns seconds `[0..540]` — **the −60s row is silently absent**. `build_exact_second_states` raises `ValueError("player playbackData is missing")` on the same matches.

**Mechanism**: (a) for minute states, `second=-60` is the only <0 row and the only one needing playback → it silently vanishes on no-playback matches → train rows for old matches lack the pre-horn state while validation rows keep it; (b) the catalog gate tests match-level `playbackData`, so a match with match-level playback but missing *player-level* events would be admitted for validation and then crash `build_exact_second_states` — latent, zero observed cases today.

**Impact**: systematic missingness at exactly one second for ~76% of the dataset; also a hard capability ceiling — exact-second features (and any backtest replay needing them) exist for only 770/3207 catalog matches.

**Confirm/fix**: make `playback_available` reflect `all(player.playbackData.playerUpdateGoldEvents)` and emit the −60 row from `networthPerMinute[0]`-equivalent or skip uniformly for all splits.

### collect-devin-F9 — ~1s skew between STRATZ event clock and GRID live clock (S3, likely)

**Where**: `stratz_seconds.py` (event `time` domain) vs `grid_feed.py:181` (`second = live_clock_seconds(board, age) − table.feed_delay`).

**Evidence**: `clock_shift.py` compared STRATZ-reconstructed NW trajectory vs archived GRID tables on 5 maps: best alignment at shift −1s (live GRID second = STRATZ second −1) on 4/5 maps with median |Δnw_adv| ~34–38 gold; the 5th (9003856182) resolves fully only via the phantom row (F1) after accounting for it.

**Mechanism**: likely composition rounding — board clock extrapolated with `round(age)` plus an integer `feed_delay` subtraction, vs STRATZ's replay timestamps. Bounded ~±1s.

**Impact**: feature parity noise only — a second's NW drift (~30–60 gold) is small relative to feature scale; no label leakage.

**Confirm/fix**: none needed; note for feed-devin's alignment tests (a ±1s tolerance absorbs it).

### collect-devin-F10 — `pauses_diverge` noise + no per-pause time comparison (S4, verified)

**Where**: `src/collect/s06_publish_catalog.py:72-78` compares only count and total duration; `src/shared/utils/match_time.py:44-49` ignores pauses with `time < -90`.

**Evidence**: 15 archive+opendota dual-source matches; 4 flagged divergent — all ±1s rounding at edges, plus one real semantic diff: match 9008125103 has an OpenDota pause at `time=-695` (draft phase) which the archive clamps to `-90`. Downstream ignores `time<-90` anyway, so outcomes agree — but the warning fires on a non-issue, and genuinely *different-time* pauses with equal count+sum would pass silently.

**Impact**: log noise + theoretical silent divergence. The divergent-pause count is not persisted.

### collect-devin-F11 — market_seconds version litter; no cleanup; some matches have no tape at all (S4, verified)

**Evidence**: `data/new_processed/market_seconds/` holds `v59b14c69` (96MB), `v9c88adc2` (130MB, current), `va75c29ad` (229MB) — stale versions are never garbage-collected; 166 catalog matches have no cache under any version (F4). `CACHE_VERSION` mixes unrelated knobs (`MAX_ENTRY_SPREAD_TICKS`, `30`) so a trading-threshold change rebuilds all dataset caches.

### collect-devin-F12 — dead raw dirs (S4, verified)

`data/raw/polymarket_dota/events/` (295MB, Jul 8), `markets/` (14MB, Jul 8), `stratz_matching/` (9MB, Jul 9), `stratz_series/` (empty) — zero code references (`grep` over `src/` and Makefile). Also `data/raw/stratz_matches` v2 subset (F7). Deleting or documenting them reduces provenance confusion.

### collect-devin-F13 — GRID window rows keep no provenance (S4, verified)

`src/collect/common/catalog_types.py` `GridGameWindowRow = {condition_id, spawn_at}` — the matched `game_id`/`series_id` is discarded at `s04:452-460`. A wrong attach would be invisible in the artifact; join-back requires re-running the matcher. Fix: persist `series_id`+`game_id` in the parquet.

### collect-devin-F14 — `match_game` crashes on ambiguous candidates (S4, speculative)

`s04:444-448` raises `ValueError` when >1 game matches exactly. No observed instance; with stale caches (F2) the odds rise. A crash is the wrong failure mode — log-and-skip loses one map; crash loses the whole run's output.

### collect-devin-F15 — series prefilters lose postponed matches silently (S4, likely)

`s04:367-378` selects series whose `startTimeScheduled` is within ±8h of some link's `match_start_time`; `index_window` bounds scheduled time to match_start ±2d. A series postponed >8h (or >2d) is never fetched — indistinguishable in output from a true GRID coverage gap. Contributes to the 1351 no-window unmatched; the count is only visible as `unmatched_links`.

### collect-devin-F16 — OpenDota fetch failures leave no marker (S4, verified)

`s05:69-90`: failed fetches print a line and leave no cache file → retried next run forever for permanently-failing ids (private/deleted matches 404). Bounded by the 1900/day budget; a `fetched_ok:false` marker would stop the re-spend. Cache size: 5744 files vs 3300 currently-admitted ids — the surplus is dead weight from earlier admission sets (same class as F7/F12).

### collect-devin-F17 — STRATZ RICH_QUERY is ~6× wider than needed (S4, verified)

`s05b:43-147` fetches ~25 event arrays per player (positions, health, attributes, battle, abilities, inventories, damage, heals, cs, runes, tower damage…) plus match-level `playbackData`. The pipeline reads: `stats.networthPerMinute`, `stats.level`, `stats.deathEvents`, `playerUpdateGoldEvents`, plus `didRadiantWin`/`durationSeconds`/`startDateTime`/team fields. `firstBloodTime` and `towerDeaths` are fetched and never read anywhere. 19,372 files = 1.4GB compressed; fetch cost 2.5s × N requests + STRATZ quota. Trimming the query speeds every future backfill and shrinks the cache ~5-10×.

## Architecture / performance / debuggability notes

Ranked:

1. **"File exists = cached = correct" is the dominant fragility pattern.** GRID `series_state` (F2), STRATZ profiles/verdicts (F7), prices-history windows (no revalidation of the stored `startTs/endTs` vs current anchors — `cache_covers_window` handles the anchor case, ok), market caches (F11: version-keyed, good). Two classes share one fix: store a completeness/freshness flag in the payload and refetch when incomplete.
2. **Silent-skip + hard-crash adjacency.** Market build silently skips no-book matches (F4); minute-state builder silently drops second −60 (F8); `match_game` crashes on ambiguity (F14); `scan_cache` would crash on a stale v2 (F7). The pipeline alternates between invisible drops and fatal exceptions — neither produces a per-match reason artifact. A `match_status.parquet` (admission verdict + reason + source) would make every drop auditable.
3. **No provenance joins.** Window rows lack series/game ids (F13); catalog has `horn_source`/`pauses_source` (good) but no `prior_anchor_kind` or `winner` field provenance beyond `winner_source="stratz"` constant.
4. **Duplicate but divergent bookkeeping.** `match_links` has `sort_ts` (used for ordering) while `match_catalog` has `start_time`; the ±8h/±2d prefilters key off scheduled vs actual times without cross-checking delays.
5. **Performance**: `scan_cache` re-reads ~3300 gzipped multi-MB JSON files per s05b run to rebuild the index; `try_load_opendota_pauses`/`try_load_stratz_winners` re-parse ~5k caches per s06 run; `phantom`-style issues are undetectable without raw archive replay. Reasonable today; will hurt at 10×.
6. **The market build's `CACHE_BUSTING_PARAMETERS` mixes dataset semantics with a trading threshold** (`MAX_ENTRY_SPREAD_TICKS`) — changing a live-trading constant invalidates all 3207 dataset caches. Split the bust key.

## Required questions

### Q1 — Clock definitions

- **Second 0 = horn** (first creep-contact / game clock 0:00). STRATZ event `time`, `stats.*` arrays, `radiantNetworthLeads[i]` (index i ↔ second `60·(i−1)`; index 0 ↔ −60s), OpenDota `duration`/`pauses[].time`, and GRID `clock.currentSeconds` all share this clock. Verified empirically: per-player NW matched GRID tables to ±4 gold at known wall times on 9003856182, and the built minute asserts hold on all playback matches.
- **GRID `startedAt` (spawn)** = map load / start of the −90s countdown (verified: `2.json` shows `clock.currentSeconds=-84` ~9.5s after `startedAt`). `s06` derives horn = `spawn + 90s + pauses in [−90,0)`. Archive-attached rows use the archive's observed horn (cross-checked in s03 against the schedule identity horn — `excluded_horn_inconsistent` rejections exist).
- **STRATZ/OpenDota `startDateTime`/`start_time` = draft/lobby start**, NOT spawn: median spawn−start 791s (min 67, max 1053) on 3187 windowed links; 9003856182: `startDateTime`=22:20:08 vs archive horn 22:34:57 (+889s). `endDateTime` is **synthetic**: `endDateTime == startDateTime + durationSeconds` exactly (1789683608+1791=1789685399) — it ignores draft and pauses, so `start+`-based wall math is wrong by ~15min. Only used for the GRID match window — safe but mislabeled (`MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS`).
- **STRATZ does not include pauses in event times** — game-clock frozen during pause, same as live. `get_state_available_ts`/`calculate_game_second` handle pause→wall shifts correctly (verified by ±2s table alignment); pauses with `time < -90` (draft-phase pauses, e.g. `time=-695` on 9008125103) are correctly ignored by the wall-clock math but counted in `pauses_diverge` warnings.
- **Consistency with live feeds**: Steam `game_time` is the same in-game clock (pauses freeze it); GRID board clock + `feed_delay` matches within ~1s (F9); Oddin not deeply verified (feed-devin scope).

### Q2 — Look-ahead in reconstruction

- **NW**: `build_exact_second_states` walks `playerUpdateGoldEvents` consuming only `event.time <= second` (cursor) → strictly causal. For minute states, `networthPerMinute[s//60]` — a point sample at the mark, not interpolated.
- **XP**: level-at-second via `bisect_right(level_seconds)` → last level-up ≤ s; XP = `LEVEL_XP[level]` cumulative table (level-quantized, ignores in-level progress — same approximation as live, which also reads level).
- **Deaths**: `bisect_right(death_times, second)` → counts events with `time <= s`. Death *timestamps* are known exactly (post-game data) — using them is correct since the death is observable at its own second.
- **top-1**: `build_top_player_features` on the same per-player NWs.
- **Proof**: `lookahead_proof.py` — 3 random validation matches, 367 (match,second) checks; cursor output == independent non-cursor recompute everywhere; minute marks equal `networthPerMinute` at all 139 marks. No input with `t > s` can enter state s: the only time-indexed inputs are gold events (`<=` guard), level times (`bisect_right`), and death times (`bisect_right`). `networthPerMinute`/`leads` are per-minute aggregates consumed only at their own mark (assert enforced: `assert_minute_consistency` raises on any inconsistency at `second % 60 == 0`).

### Q3 — Feature definitions vs live

| Feature | Train (STRATZ) | Live GRID | Live Steam | Live Oddin | Skew |
|---|---|---|---|---|---|
| `radiant_nw`,`dire_nw`,`nw_adv` | Σ per-player `playerUpdateGoldEvents.networth` ≤ s | Σ table rows with `team_id` | `teams[].net_worth` (pre-aggregated) | feed feed (not audited) | **GRID phantom roster +600 on ~3% of maps (F1)**; ±~1s timing noise; ±4 gold sampling |
| `radiant_xp_adv` | `Σ LEVEL_XP[level_at_second]` | `Σ xp_advantage(levels)` same table | same (levels) | same | none structural; ±1 level on level-up boundary seconds |
| `deaths_radiant/dire` | count `deathEvents.time ≤ s` | Σ `Deaths` counter | Σ `death_count` | — | none (matched exactly on compared maps) |
| `top1_nw_adv`, `top1_nw_ratio` | max and max/(rest) over 10 NWs | same over ≤6+ rows | same over 5 | — | phantom never tops 600→only `rest` +600 (F1) |
| `market_radiant_prior` | last pre-anchor aligned pair (F6) | — | — | — | anchor mixed spawn/horn |
| `radiant_win` (label) | `didRadiantWin` | — | — | — | gated by `winner_conflict` (1 caught: 8996785570) |

STRATZ `networth` in gold events == GRID `NetWorth` == Steam `net_worth` (verified ±4 gold on 9003856182). Gold items/buyback/neutral/courier are all inside `networth` for all three sources — consistent. STRATZ `leaverStatus` does **not** alter reported NW (DC'd players keep real values — verified).

### Q4 — Orientation

- `isRadiant`/`didRadiantWin` come from STRATZ (`players[].isRadiant`, `match.didRadiantWin`); `radiant_token_index` is produced in s02 (`0 if game.radiant_team_id == proposal.names.team_a_id`) — orientation rides on the linker's team-id match (link-devin scope); s05a uses it to pick the radiant token for the prior; market_data uses it to pick the radiant book. Cross-check exists: `winner_conflict` mask compares `archive_winner` vs `didRadiantWin` — caught 1 real conflict (8996785570, dropped).
- Remakes/abandoned: catalog min duration 750s (12.5min), 12 games <15min — no ultra-short remakes in the dataset; no explicit remake gate, but the `usable`+`winner`+`ended_at` masks shape them out. Reconnect gaps: 42% of matches have ≥1 `DISCONNECTED` player — normal, values verified unaffected.

### Q5/Q9 — Drops

Catalog funnel (recomputed live on current artifacts, `catalog_drops.py`):

| first-failure | count |
|---|---|
| admission (no GRID window, no archive) | 1746 |
| stratz unusable / missing | 24 |
| playback (validation-age only) | 42 |
| prior missing | 27 |
| ended_at / gamma / winner / winner_conflict | 0 / 0 / 0 / 0 (winner_conflict any-row: 1) |

kept 3207 / dropped 1839. Reasons are **log-visible only** (s06 logs first-failure counts + dropped match_ids; nothing persists a per-match reason column). Bias: admission loss is by construction GRID-coverage-dependent (tournaments GRID misses = the 1351 no-window — can't split coverage vs prefilter cheaply); stratz unusable biased long (F5); playback drops are all validation-era archives without STRATZ playback; prior drops are thin/dead markets.

### Q6 — Pregame prior (collection rule, exact)

Anchor per condition: **GRID `spawn_at` when a window exists, else `archive_horn_at_utc`** (`s05a:56-74`). Fetch window = `[anchor − 21600, anchor]` at `fidelity=1` per token (`s05a:147-158`). Prior = `last_aligned_pre_anchor_pair`: latest point with `t < anchor` per token; if the two legs' quote stamps differ >30s, both rewind to `min(t)+1`; then `normalize_pair_mids` requires `r+d` within ±0.05 of 1.0 → `r/(r+d)`. Orientation via `radiant_token_index` (s02). In-game price entry: none possible for spawn anchors (spawn ≪ horn); for horn anchors the chosen `t` is strictly < horn, but if `p` is a bucket-close value the bucket may span ~59s post-horn — semantics need confirmation (Open question). Missing/pair-failed → row absent → catalog `prior` mask drops it (27).

### Q7 — GRID starts

Taken per game: `startedAt` (→ `spawn_at`) and `clock.currentSeconds` (match key only, not persisted). `series_state/*.json` is a **permanent cache of whatever the API said at fetch time** — including `finished:false` snapshots (F2: 7 files, 61 unfinished games). The series index pages are keyed by window bounds and reused verbatim.

### Q8 — Provenance / schema drift / leftovers

- Fetch stamps exist: STRATZ `fetched_at` 2026-07-25 → 2026-09-24; OpenDota `fetched_at` Jul 2026-era; prices-history `fetched_at` Jul→Sep 2026; series_state has only internal `updatedAt` + file mtime (no explicit fetch stamp — minor gap).
- Fetch-code history: matcher introduced `01792958` (Aug 6, replaced name-matching that put ~1% on wrong games); cache-existence contract `db23b945` (Sep 4); stage split `c4daa108`/`4ef2715e`; `de273edd` archive-linking admission; `081fb493` dropped the catalog Telonex-tape gate (→ F4 crash surface).
- Schema drift: STRATZ v2 (13,734 files, no `stats`/`playbackData` fields read today) vs v3 (5,638); all catalog/admitted matches are v3. No player-count drift (all catalog 10v5v5), no zero-NW/truncation cases found in catalog scans; gold events absent for 76% of non-validation matches (F8). Duplicate players: none observed.
- Leftovers (F12) + stale market cache versions (F11).
- Sample-span check: catalog covers 2025-10 → 2026-09 (index month counts 60→283→555→470→…→235).

## Checked and OK

- **Exact-second causality**: cursor + `bisect_right` everywhere; 367-point independent recompute identical; minute marks equal `networthPerMinute` on all checked marks (build-time assert enforces it globally).
- **Market as-of reads**: `find_asof_quote` (`telonex_book.py:210-240`) uses `bisect_right(ts, target)−1` — strictly past quotes; 5s age cap; one-sided books walked back within the cap only. Future signals go through `lookup_market_p_after(anchor+horizon)` — intended label direction, correct.
- **Pair/spread gates**: `resolve_market_pair` enforces age, presence, `MAX_ENTRY_SPREAD_TICKS`, `PAIR_SUM_TOLERANCE` before emitting `market_p`.
- **Catalog gates**: `winner_conflict` caught a real archive/STRATZ disagreement (8996785570); all 3207 rows have `ended_at`, pauses, winner; `horn−spawn` for archive-attached rows median 89.4s vs model 90s — the `HORN_OFFSET` model checks out against independently-observed horns.
- **Pause handling**: OpenDota pauses verified consistent with archive schedules (11/15 identical; 4 differ only by ±1s edge rounding or the benign `time<-90` draft-pause case); `get_state_available_ts`/`calculate_game_second` are proper inverses with pause-frozen reads.
- **s05b**: atomic gz writes (tmp+rename), bounded retries with backoff, 429 Retry-After handling, per-match index with `reason` column — the only stage that persists per-item failure reasons.
- **s06**: `first_failure_counts` attribution is fair (each drop counted once, first failing mask), `admission` semantics documented and consistent with s07's report.
- **No clock-sign confusion**: `MODEL_START_SECOND=-60` consistent across `build_minute_states`/`build_exact_second_states`/`build_market_second_rows`/`get_state_available_ts` (raises for `<-60`).

## Open questions / Needs from VPS

1. **Live feed selection per traded map**: which source (Steam/GRID/Oddin) won per map historically — determines real exposure to the phantom-roster bug (F1). Answerable from trader archives/`session.jsonl` on the VPS.
2. **Polymarket `prices-history` semantics**: is `p` a point sample at `t` or a bucket aggregate? Determines whether the horn-anchored prior can contain ≤~59s of post-horn trades (F6). One API probe or doc check settles it.
3. **GRID coverage split**: of the 1351 no-window links, how many are series GRID never covered vs postponed outside the ±8h/±2d prefilters (F15)? Needs a fresh `allSeries` query by tournament/team — or a manual look at 20 no-window match ids.
4. **Steam `GetRealtimeStats` for disconnected/roster players**: does the Steam payload ever carry non-player roster rows? If yes the phantom issue generalizes; if it's GRID-widget-only, the fix is a GRID-side row filter.
5. **`didRadiantWin` on abandoned/remade maps**: whether STRATZ reports a winner for remake-first-attempts and whether the market resolved the same way — the one caught conflict (8996785570) suggests residual risk at the boundary of `winner_conflict`.
