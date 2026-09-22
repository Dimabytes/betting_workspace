# Step 2 — Dota linking and shared dataset admission

Spec: `docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md` (Шаг 2).
Scope: merge exact archive links into a shared `match_links` table, make the dataset
admission rule `(historical OpenDota + GRID) OR (admitted live archive)`, and give the
catalog an explicit time contract (horn / pauses / end + provenance). No schedule replay
in backtest (3A), no CLI/policy cleanup (3B), no train-lag change, no new simulators,
no `poly-maker` changes.

All work happens in `esports-trader` on `main`. Do not run the suite during a live map.
`/tmp` is tmpfs; conftest relocates pytest tmp to `/var/tmp/pytest-esports-trader`.

---

## 1. Current flow (traced, post step-1)

### Collect pipeline (`Makefile`)

```text
universe -> link -> grid -> opendota -> prices -> stratz -> catalog
s01        s02     s04     s05        s05a      s05b      s06
collect-dry-run: s02 s04 s05a s05b          (no network)
```

- `src/collect/s01_build_universe.py` → `UNIVERSE_PATH`
  (`data/new_processed/universe/universe.parquet`). Columns include `conditionId`,
  `event_id`, `contract_kind` (`map_winner`/`series_winner`/`other`), `team_a`,
  `team_b`, `best_of` (Int64), `game_number` (Int64), `scheduled_ts`, `market_slug`,
  `seconds_delay`, `market_closed_at`, `token_id_0/1`, `inventory_status`
  (`candidate` 5155, `series_linking_required` 2804, `excluded` 65113),
  `parse_status`. 73,072 rows total.
- `src/collect/s02_link_opendota.py` — independent OpenDota candidate source.
  Loads candidate pages, builds complete OpenDota series, matches series to event
  contexts by best-of + `scheduled_ts` window + team names, writes
  `OPENDOTA_LINKS_PATH` (`opendota_links.parquet`) + `opendota_link_audit.parquet`.
  `OpenDotaLinkRow` = `event_id, game_number, match_id, map_condition_id,
  match_start_time, grid_clock_seconds, radiant_token_index,
  opendota_radiant_name, opendota_dire_name`.
  `build_event_context` already applies the series-winner decider rule:
  `map_condition_ids[best_of] = series_condition_id` only when
  `series_winner_covers_map(best_of, best_of, map_winner_exists=...)` — i.e. the
  series contract stands in for the decider map **only when that map has no
  map_winner market**. Validation: unique `(event_id, game_number)`, `match_id`,
  `map_condition_id`; monotonic `match_start_time` per event.
  Note: OpenDota `start_time` is ~match-registration time (observed median
  `horn_at_utc - match_start_time` ≈ 841 s), not horn and not spawn. It is used
  only as a loose anchor (s04 ±30 min GRID match window, fetch ordering).
- `src/collect/s04_fetch_grid_starts.py` — reads `OPENDOTA_LINKS_PATH`
  (`load_linked_maps`), fetches GRID series index + `seriesState`, matches each
  link to a GRID game by `clock.currentSeconds == grid_clock_seconds` and
  `0 <= spawn_ts - match_start_time <= 1800`. Writes `GRID_GAME_WINDOWS_PATH`
  (`grid_game_windows.parquet`: `condition_id, spawn_at` where `spawn_at` = GRID
  `games[].startedAt` = map load). Fallback `live_paper_map_load_unix`: reads a
  legacy `state.jsonl` spawn, else `match.json horn_at_utc - 90` — **this is the
  `spawn = horn - 90` producer the spec orders fixed** (s06 then recomputes
  `horn = spawn + 90 + pre-horn pauses`, double-counting pre-horn pauses).
  Index data shows zero steam-source archives, so the `state.jsonl` branch is
  dead; the `horn - 90` branch fires for Oddin/GRID dirs named by steam id.
- `src/collect/s05_fetch_opendota_matches.py` — fetches `/matches/{id}` for
  `load_grid_window_match_ids()` (windowed links only), cache
  `data/raw/opendota_matches/match_<id>.json.gz` (5,640 files; `pauses` key is
  always present, `[]` = parsed-and-none).
- `src/collect/s05a_fetch_prices_history.py` — anchors =
  `{condition_id: spawn_at ts}` from GRID windows; targets = links having an
  anchor; writes `pregame_quotes.parquet` (`radiant_prior` = last aligned pair
  mid before anchor).
- `src/collect/s05b_fetch_stratz_matches.py` — fetches STRATZ rich match for the
  same `load_grid_window_match_ids()` worklist; writes `stratz_match_index.parquet`
  (`status usable|unusable`, `playback_available`, `duration`).
- `src/collect/s06_publish_catalog.py` — joins links + grid windows + stratz
  index + priors + universe gamma columns; computes `ended_at` via
  `get_game_ended_at(spawn_at, duration, pauses)` with OpenDota pauses
  (`try_load_opendota_pauses`); `keep_complete` drops rows failing
  stratz/prior/ended_at/gamma. Writes `MATCH_CATALOG_PATH`. Current catalog:
  3,160 rows (from 4,875 links and 3,210 grid windows).
- `src/collect/common/window_ids.py::load_grid_window_match_ids` — the shared
  fetch worklist: links whose `map_condition_id` is in grid windows, ordered by
  `match_start_time`. Used by s05 and s05b. **This is where the OpenDota+GRID
  leg of the admission rule currently lives.**
- `test_match_catalog.py::test_stage_parquet_paths_are_collect_private` —
  stage parquet path constants must stay `collect/` private. The new
  `match_links` paths follow the same rule (add them to `STAGE_PATH_NAMES`).

### Dataset / downstream

- `prepare_dataset/prepare_dataset.py` — selects catalog entries passing
  `match_passes_tape_buckets(entry, RAW_TELONEX_POLYMARKET_DIR)` (tape buckets
  over `entry.spawn_us → entry.ended_us`), splits train/validation at
  `VALIDATION_START_TIME`, requires market caches (`market_seconds_cache_path`)
  within `MAX_TRAIN_HARD_MISSES`/`MAX_VALIDATION_HARD_MISSES`, requires
  `playback_available` for validation, builds minute rows (train) and exact-second
  rows (validation) from STRATZ via `build_minute_states` /
  `build_exact_second_states`. `TRAIN_LAG_SECONDS = 10` for both the normal and
  the no-XP dataset (no-XP = same admitted cohort minus `radiant_xp_adv`;
  `NO_XP_FEATURE_COLUMNS` in `shared/utils/gbm.py`). `radiant_win` is stored in
  rows but is **not** a feature column — already outcome-only.
- `market_data/build_market_data.py` — per catalog row:
  `horn = get_horn_datetime(row.spawn_at, pauses)` where pauses come from
  `try_load_opendota_pauses`; per-second `state_ts_us` via
  `get_state_available_ts(horn, second, pauses)`; reads Telonex books causally.
- `backtest/selection.py::build_market_context` — recomputes
  `horn_at = get_horn_datetime(row.spawn_at, pauses)`;
  `calculate_availability_window(map_load_at=row.spawn_at, game_ended_at=...)`.
- `backtest/run.py::load_replay_lookups` — per match reads
  `get_opendota_match(match_id)` for `pauses` and `radiant_win`. Also
  `backtest/report_capital.py` reads `radiant_win` from the OpenDota file.
- `shared/utils/match_catalog.py` — `CatalogEntry` (`spawn_at`, `ended_at`,
  `start_time = spawn_at ts`, gamma…); `MatchCatalog` sorts by
  `(spawn_at, match_id)`.
- `shared/utils/match_time.py` — `HORN_OFFSET_SECONDS = 90`,
  `get_horn_datetime`, `get_game_ended_at`, `get_state_available_ts`
  (`horn + second + post-horn pauses`), `calculate_game_second`,
  `get_paused_seconds_before` (pre-horn = `time >= -90 and time < second`).

### Where `archive_index` already gives admitted archives

`src/archive_index/` (step 1): `index.py` scans roots, audits identity against
the universe (`audit_identity`), resolves duplicates/conflicts, extracts
schedules (`schedule.py::extract_schedule` — ticks carry `seq, received_at_utc,
received_ns, game_second, phase, paused, terminal, horn_unix_seconds,
source_ts_unix`; interruptions list separately), and publishes
`data/archive_index/index.parquet` + `schedules/<root>/<game>/<archive_id>.json`.
Index row fields (per archive): `archive_root, archive_id, match_id, feed_source,
schema_version, steam_match_id, map_number, joined_at_utc, joined_at_second,
horn_at_utc, condition_id, market_slug, yes_token_id, no_token_id,
yes_is_radiant, team_radiant, team_dire, has_final, winner, duration_seconds,
event_id, contract_kind, universe_game_number, yes_token_index, identity_status,
feed_status, record_status, admission, schedule_published, schedule_path,
schedule_fingerprint, schedule_rules_version, feed_sha256, delay_s,
delay_evidence`, plus `ScheduleStats` fields. Admission requires identity ok +
feed ok + record ok; orders/fills/PnL are never consulted (zero-order archives
admit by construction). Current data: 230 Dota rows → **210 admitted**
(200 GRID, 10 Oddin; 206 with `steam_match_id`, 4 without); 20 refused
(`feed:no_window_updates` 10, `record:no_terminal` 9, `duplicate_of` 1).

Measured merge preview (read-only analysis, current artifacts):

- 114 admitted archives have `condition_id` already in `opendota_links` → attach.
- 96 admitted archives have `condition_id` not in links → 94 have `steam_match_id`
  (new link candidates), 2 do not (`grid-3007246-m2`, `grid-3007988-m2`).
- Of the 4 no-steam admitted archives, 2 have a linked condition
  (`grid-2996037-m1`, `grid-3005969-m2` → exact condition attach, the
  "archive without Explorer" case).
- 3 archives have `steam_match_id` ≠ the link's `match_id` for the same
  condition (`grid-2996008-m2`, `grid-2996018-m1`, `grid-2996031-m3`). All three
  agree with the linked match on `winner` and `duration_seconds` → same game,
  stale steam stamp in `match.json`. These are the explainable conflicts.
- `map_number` equals link `game_number` in all 114 attachable cases.
- 0 of the 94 recoverable steam ids are currently in the OpenDota or STRATZ
  caches — s05/s05b will fetch them once they enter the worklist.

**Bug found during tracing (fix in this step):**
`shared/utils/parsing.py::opt_int` rejects `np.integer` (only `int|float`), so
`archive_index/universe.py::load_dota_universe` produces `game_number=None` for
every market (`universe.parquet` stores `game_number` as Int64 → `itertuples`
yields `np.int64`/`NA`). Consequence: the `map_mismatch` audit in
`audit_identity` is silently dead for Dota — `universe_game_number` is None in
all 230 index rows today. Fix `opt_int` to accept `np.integer` (and `opt_float`
to accept `np.floating` for symmetry), then re-run `make archive-index` before
the merge so the map audit actually runs. Schedule fingerprints are unaffected
(`compute_fingerprint` does not consume `universe_game_number`).

---

## 2. Concrete merge design — `match_links`

### New stage: `src/collect/s03_merge_archive_links.py`

Inputs: `OPENDOTA_LINKS_PATH`, archive index (`ARCHIVE_INDEX_DIR/index.parquet`,
CLI `--index` override), `UNIVERSE_PATH` (canonical `conditionId` case,
`best_of`, contract inventory per event). Outputs:

- `MATCH_LINKS_PATH` = `data/new_processed/match_links/match_links.parquet`
- `MATCH_LINK_AUDIT_PATH` = `data/new_processed/match_links/match_link_audit.parquet`
- constants in `collect/common/paths.py`; row TypedDicts in
  `collect/common/catalog_types.py` (next to `OpenDotaLinkRow`).

Fails hard if `index.parquet` is missing — `make archive-index` is a documented
prerequisite, not a Makefile dependency (archives live outside the fetch flow).

### `MatchLinkRow` (one row per linked market/map)

All `OpenDotaLinkRow` fields, with the OpenDota-only fields nullable:

```text
event_id, game_number, match_id, map_condition_id, radiant_token_index,
match_start_time        (int | None — OpenDota only, never fabricated)
grid_clock_seconds      (int | None — OpenDota only)
opendota_radiant_name   (str | None)
opendota_dire_name      (str | None)
link_source             ("opendota" | "archive" | "opendota+archive")
sort_ts                 (int — match_start_time else archive horn unix)
archive_id              (str | None)
archive_root            (str | None — schedule path label)
archive_feed_source     ("grid" | "oddin" | None)
archive_condition_id    (str | None — only when != map_condition_id)
archive_steam_match_id  (int | None)
archive_map_number      (int | None)
archive_joined_at_second(int | None)
archive_horn_at_utc     (str | None — direct archive horn)
archive_winner          ("radiant" | "dire" | None)
archive_duration_seconds(int | None)
schedule_fingerprint    (str | None)
identity_conflict       (str | None — see below)
```

Write `match_id`, `archive_steam_match_id`, `sort_ts` as Int64 (nullable) —
never let pandas coerce to float.

### `MatchLinkAuditRow` — one row per admitted archive

```text
archive_id, archive_root, condition_id, event_id, feed_source,
resolution, detail
```

`resolution` ∈ `attach_by_condition` | `attach_by_match` | `new_link` |
`no_steam_no_link` | `excluded_winner_mismatch` | `excluded_map_mismatch` |
`excluded_series_rule` | `excluded_horn_inconsistent` |
`excluded_steam_condition_conflict`. Non-admitted archives are not iterated at
all — the funnel prints the index's own refusal counts.

### Merge algorithm

Base = `opendota_links` rows (unchanged values, `link_source="opendota"`,
`sort_ts=match_start_time`). Then iterate admitted Dota index rows in
deterministic order (sort by `archive_id`; when several archives would create a
row for the same `(event_id, game_number)`, prefer `contract_kind="map_winner"`
over `series_winner`, then `archive_id`):

1. **Exact condition attach.** `index.condition_id.lower()` matches a row's
   `map_condition_id.lower()` → attach archive columns to that row
   (`link_source` → `"opendota+archive"`). Checks, each recorded on the row /
   audit:
   - `archive_map_number != link.game_number` → `excluded_map_mismatch`
     (do not attach).
   - `archive.steam_match_id != link.match_id` → `identity_conflict =
     "archive_steam_mismatch"`; keep the link's `match_id` (the archive's feed
     is bound to this market; its steam stamp is untrusted metadata). Cross-check
     `archive_winner` vs the linked match's cached OpenDota `radiant_win` when
     the file exists: disagreement → `excluded_winner_mismatch` (the archive
     recorded a different game than the link claims). Agreement or unknown →
     attach with the flag set. The 3 known cases all agree → they attach,
     flagged, and the report explains them.
   - Horn sanity: `schedule.horn_unix_seconds`/`identity.horn_at_utc` vs
     `index.horn_at_utc` must agree (read the published schedule's identity
     block — cheap). Disagreement → `excluded_horn_inconsistent`.
2. **Match attach (market differs).** No condition hit, but
   `steam_match_id` equals an existing row's `match_id` → verify
   `map_number == game_number` (else `excluded_map_mismatch`), then attach with
   `archive_condition_id` set to the archive's own condition and
   `identity_conflict="archive_market_differs"`. The archive still counts for
   admission: its schedule describes the same game's clock (e.g. series market
   recorded while the link row carries the map market).
3. **New link row.** No condition hit and no match hit:
   - `steam_match_id` absent → `no_steam_no_link` audit row, no link created
     (per spec: a Dota archive without Steam ID can only attach to a known
     match by exact condition id). Today: `grid-3007246-m2`, `grid-3007988-m2`.
   - `contract_kind == "map_winner"` → row with `game_number =
     archive.map_number`, `map_condition_id` = archive's condition (canonical
     case from the universe row), `match_id = steam_match_id`,
     `radiant_token_index = yes_token_index if yes_is_radiant else
     1 - yes_token_index`, `sort_ts` = archive horn unix, all `opendota_*`/
     `match_start_time`/`grid_clock_seconds` = null, `link_source="archive"`.
   - `contract_kind == "series_winner"` → allowed only when
     `series_winner_covers_map(best_of, archive.map_number,
     map_winner_exists)` holds for the universe rows of `archive.event_id`
     (`best_of` from universe; `map_winner_exists` = a candidate map contract
     exists for that `game_number`). On success the new row carries the
     **series** condition with `game_number = best_of` (the decider). Else
     `excluded_series_rule`. If the series went the distance on a different map
     than `best_of` implies, or a map market already exists for the decider, the
     archive still attaches via rule 2 when the match is linked.
   - `condition_id` or `steam_match_id` colliding across different rows
     (condition says row A, steam says row B) → `excluded_steam_condition_conflict`.
4. **Late OpenDota enrichment** is automatic: OpenDota rows are the base, so a
   later `make link` that newly links a condition/match an archive created
   earlier results in one merged row (`attach_by_condition`/`attach_by_match`
   on the next collect run) — never a duplicate. The row keeps the OpenDota
   market identity; the archive's differing condition is preserved in
   `archive_condition_id`.

### Validation (mirrors `s02.validate_links`)

- unique `(event_id, game_number)`, `match_id`, `map_condition_id`;
- monotonic `match_start_time` within event over rows where it is non-null;
- every `archive_id` is unique across rows (one archive attaches once);
- `link_source == "archive"` ⇒ `match_start_time`/`grid_clock_seconds`/
  `opendota_*` are all null (assert no faked fields).

---

## 3. Admission gate

Rule: **admitted = (OpenDota-linked AND GRID-windowed) OR archive-attached.**

- `collect/common/window_ids.py`: replace `load_grid_window_match_ids` with
  `load_admitted_match_ids()`: read `MATCH_LINKS_PATH` + `GRID_GAME_WINDOWS_PATH`;
  admitted = `map_condition_id` in windows OR `archive_id` non-null; order by
  `sort_ts`. Since only `admission == "admitted"` archives are merged, a set
  `archive_id` IS archive admission — feed/record verdicts were already
  enforced by the index. A bad-feed archive can never bypass the training
  filter because it is never attached (`excluded_*`/`no_steam_no_link` rows are
  audit-only).
- `s05` and `s05b` consume `load_admitted_match_ids()`. For archive-only rows
  this is exactly the spec's "use the existing detail fetch after recovering the
  Steam id" — `/matches/{id}` works even when Explorer never surfaced the match.
  Fetch failures leave the cache file absent; the link stays and the next stage
  reports the refusal (see §5).
- `s05a`: anchors = `{condition_id: spawn_at ts}` from grid windows ∪
  `{condition_id: archive horn ts}` for archive-attached rows lacking a window
  (the archive horn is a truthful pre-game anchor for the prior; document the
  provenance difference — it shifts the quote anchor by ~90 s + unknown
  pre-horn pauses, still strictly pre-game-end). Targets iterate admitted
  links; the existing complete-token-pair check stays.
- `s06` publishes the catalog from admitted links only (all `match_links` rows
  are admitted by construction — every row has either a GRID window or an
  attached archive; verify and log the count of `link_source` values plus a
  `no admission path` count that must be zero — compute it as
  `link_source=="opendota" & condition not in windows` … actually keep it
  honest: `match_links` can contain OpenDota rows with no window and no archive
  — they are linked-but-not-admitted; s06's per-check logging must count them
  as `admission failed`, distinct from stratz/prior/ended/gamma failures).
- Same rule for all splits by construction: research train, validation and
  production training all select from `match_catalog` + market caches; no
  split-specific admission exists and none is added. Zero orders/fills never
  appear in any admission path.
- The Makefile gets `link-archive` between `link` and `grid`:
  `collect: universe link link-archive grid opendota prices stratz catalog`;
  add `s03` to `collect-dry-run` between s02 and s04.

---

## 4. Time / pause / horn contract

The catalog row becomes the single time contract. `MatchCatalogRow` changes
(`shared/types/dataset.py`), appended columns:

```text
spawn_at          -> now str | None   (GRID games[].startedAt only; real map-load,
                                       absent on archive rows — horn != map-load)
horn_at           str                 (required; the confirmed horn instant)
horn_source       "grid_derived" | "archive"
pauses_json       str | None          (JSON list of {"time","duration"}; null = UNKNOWN,
                                       never a substitute empty list)
pauses_source     "opendota" | "archive" | None
radiant_win       bool                (outcome/settlement only; not a feature)
winner_source     "stratz"
archive_id        str | None
archive_root      str | None
archive_feed_source str | None
schedule_fingerprint str | None       (step-3A schedule binding; link != replay use)
```

`ended_at` stays derived: `get_state_available_ts(horn=horn_at,
second=duration, pauses=pauses)` — identical math for both paths, provenance
carried by `horn_source`/`pauses_source` (a separate `ended_source` column
would restate the same facts — not added).

### Derivation per path

- **OpenDota+GRID row**: `spawn_at` = GRID `startedAt`; `horn_at` =
  `get_horn_datetime(spawn_at, pauses)` (spawn + 90 s + pre-horn pauses),
  `horn_source="grid_derived"`; pauses from OpenDota cache
  (`pauses_source="opendota"`).
- **Archive-attached row**: `horn_at` = `match.json horn_at_utc` carried on the
  link (`archive_horn_at_utc`), `horn_source="archive"` — a direct feed
  observation, preferred over the derived horn whenever an admitted archive is
  attached (even when a GRID window also exists). `spawn_at` = GRID
  `startedAt` if the row also has a window, else null — never `horn - 90`.
- **Pauses** per row: OpenDota pauses if the match file exists; else derive
  from the archive's published schedule (`pauses_source="archive"`); else
  `pauses_json=null` → `ended_at` null → catalog drop with reason.

### Archive-derived pauses

`src/archive_index/schedule.py`: add `read_schedule(path) -> FeedSchedule`
(typed reader for the published JSON) and
`pauses_from_schedule(schedule) -> list[OpenDotaPause] | None`:

- Walk ticks; a maximal run of `paused=True` ticks is one pause.
- `time` = `game_second` of the first paused tick.
- `duration` = `source_ts_unix(first tick after the run) −
  source_ts_unix(last tick before the run)` — server stamps bracket the frozen
  clock, so it is correct even across a receive gap.
- Return `None` (unknown, not `[]`) when a boundary is unobserved: first tick
  already paused (pause started before recording) or a run open at record end.
  `pauses=[]` is emitted only for a run-free record — "observed: no pauses".
- Sanity cross-check in s06 logging (non-fatal): when both sources exist,
  compare counts/sums and log `pauses diverge` warnings — real data makes this
  check meaningful on the ~114 overlap rows.

### Spawn double-count fix

Delete the ambiguous producer instead of patching it: s04 loses
`live_paper_map_load_unix`, `steam_state_map_load_unix`, the `LIVE_PAPER_SOURCE`
branch of `resolve_window`, and the now-unused imports
(`STATE_ARCHIVE_FILENAME`, `TRADER_DIR`, `MATCH_META_FILENAME`,
`iter_archive_records`, `match_archive_dir`, `HORN_OFFSET_SECONDS`). GRID
windows then mean exactly "GRID `startedAt`". The horn contract above absorbs
the fallback's honest use (archive rows get their own horn); its dishonest use
(fabricating `spawn` to be re-paused later) is gone. Matches whose only window
was a live_paper fallback and which have no admitted archive will drop out of
the catalog — expected consequence, surfaced as `admission failed` in the s06
log and counted in the readiness report (see §7); measure the count on first
run and report it rather than hiding it.

### Consumer changes — this step vs later

Changed **now** (needed for archive-admitted rows to flow correctly):

- `s06_publish_catalog.py` — build the contract above; read `MATCH_LINKS_PATH`;
  `pauses` per row via opendota → archive fallback; `radiant_win` from STRATZ
  (`didRadiantWin`; add a light `stratz_radiant_win`/`try_load_stratz_winners`
  helper in `shared/utils/stratz.py` reading the gz cache — `didRadiantWin` is
  inside `data.match`, no new fetch); archive-vs-STRATZ winner disagreement →
  drop with `winner_conflict` logged; keep all existing checks (stratz usable,
  prior, ended_at, gamma) and add `winner` + `horn` non-null assertions.
- `shared/utils/match_catalog.py` — `CatalogEntry` gains `horn_at`,
  `spawn_at: datetime | None`, `pauses: list[OpenDotaPause]` (parsed, never
  None for catalog rows), `radiant_win`, `archive_id`/`archive_root`/
  `archive_feed_source`/`schedule_fingerprint`, and
  `anchor_at = spawn_at or horn_at` (earliest confirmed pre-game anchor —
  honest for both paths). `start_time = int(anchor_at.timestamp())`
  (identical values for existing rows; archive rows get their true horn).
  `MatchCatalog` sort key → `(start_time, match_id)`.
- `market_data/build_market_data.py` — `resolve_catalog_row` uses
  `row.horn_at` and `row.pauses` directly; drop `try_load_opendota_pauses` and
  the `pauses_by_match` plumbing (`load_market_data_sources` no longer loads
  pauses). Per-second `state_ts_us` math unchanged — no extra shift is
  introduced (the `(arrival, game_second)` semantics belong to 3A).
- `backtest/selection.py::build_market_context` — `horn_at=row.horn_at`,
  `radiant_win=row.radiant_win`, pauses from `row.pauses`; drop the
  `pauses`/`radiant_win` parameters and the `get_horn_datetime` call.
  `calculate_availability_window` receives `map_load_at=row.anchor_at` (same
  values for old rows; archive rows window from the horn — strictly narrower
  than a map-load superset, which only ever helps the day check).
- `backtest/run.py::load_replay_lookups` — stop requiring an OpenDota match
  file: `pauses_by_match[match_id]` and `context_by_match` come from the
  catalog row (`build_market_context(sources, match_id)` after the signature
  change). Keep `ReplayLookups.pauses_by_match` (consumers still use it) but
  sourced from the catalog.
- `backtest/report_capital.py` — `radiant_win` from the catalog instead of
  `get_opendota_match`.
- `shared/utils/match_time.py` — delete `get_game_ended_at` once s06 calls
  `get_state_available_ts(horn=…)` directly (its only caller). Keep
  `get_horn_datetime` (still derives GRID horn in s06).
- `shared/utils/telonex_tape.py::match_passes_tape_buckets` — read
  `entry.anchor_at` instead of `entry.spawn_at` for the window start.

Explicitly **not** changed now (step 3A/3B boundaries):

- `backtest/run.py` keeps building market contexts and seeded cadence as
  today — no schedule-driven arrival times, no `--policy` removal, no
  mixed-model selection (3A/3B).
- `backtest/strategy._clock_at` keeps substituting synthetic clock fields —
  archive observed clocks land in 3A.
- No second lag anywhere: dataset prep still emits `second-10 ↔ market second`
  pairs and `TRAIN_LAG_SECONDS = 10` for both Dota models; LoL keeps
  `LOL_SOURCE_LAG_SECONDS = 10`. The `(arrival=12:03:17, game_second=180)` →
  features@180 + market@arrival check is a 3A acceptance test; step 2 must
  simply not introduce another offset — storing `horn_at`/`pauses` explicitly
  is what makes that true.
- `prepare_dataset` needs no code change beyond what `CatalogEntry` provides
  (it already goes through `entry`). No-XP is not Oddin-only: the no-XP model
  trains on the same admitted cohort via `NO_XP_FEATURE_COLUMNS` — unchanged.

---

## 5. Exact file list

### Add

| File | Purpose |
|---|---|
| `src/collect/s03_merge_archive_links.py` | Merge stage per §2; writes match_links + audit, prints funnel counts and the explicit "new links are candidates, not ready matches" line |
| `src/collect/s07_link_readiness.py` | Readiness funnel report (§7); prints candidate → link → admission → catalog → split counts from the stage parquets; tolerates missing later artifacts |
| `tests/test_merge_archive_links.py` | Merge rules: attach by condition, attach by match (market differs), new link, no-steam-no-link, steam/winner/map conflicts, series-decider rule, uniqueness |
| `tests/test_archive_pauses.py` | `pauses_from_schedule`: run extraction, `[time<0]` pre-horn pause, unterminated/leading pause → None (unknown ≠ `[]`), empty record → `[]` |

### Edit

| File | Change |
|---|---|
| `src/shared/utils/parsing.py` | `opt_int` accepts `np.integer`; `opt_float` accepts `np.floating` (root cause of the dead map audit) |
| `src/collect/common/paths.py` | `MATCH_LINKS_DIR`, `MATCH_LINKS_PATH`, `MATCH_LINK_AUDIT_PATH` |
| `src/collect/common/catalog_types.py` | `MatchLinkRow`, `MatchLinkAuditRow` TypedDicts |
| `src/collect/common/window_ids.py` | `load_grid_window_match_ids` → `load_admitted_match_ids` (window OR archive) |
| `src/collect/s04_fetch_grid_starts.py` | Read `MATCH_LINKS_PATH`; use only rows with `match_start_time`/`grid_clock_seconds` non-null for GRID matching (log `skipped_archive_links`); delete live_paper fallback |
| `src/collect/s05_fetch_opendota_matches.py` | worklist = `load_admitted_match_ids()` |
| `src/collect/s05b_fetch_stratz_matches.py` | same |
| `src/collect/s05a_fetch_prices_history.py` | anchors += archive horn for windowless archive rows; targets over admitted links |
| `src/collect/s06_publish_catalog.py` | §4 contract; new columns; admission logging; winner from STRATZ + archive cross-check |
| `src/shared/types/dataset.py` | `MatchCatalogRow` extended (§4); update the docstring (`spawn_at` = real map-load, nullable) |
| `src/shared/utils/match_catalog.py` | `CatalogEntry` fields + `anchor_at`; sort by `(start_time, match_id)` |
| `src/shared/utils/match_time.py` | delete `get_game_ended_at` |
| `src/shared/utils/stratz.py` | light `didRadiantWin` reader |
| `src/archive_index/schedule.py` | `read_schedule`, `pauses_from_schedule` |
| `src/market_data/build_market_data.py` | horn/pauses from `CatalogEntry`; drop opendota pauses load |
| `src/backtest/selection.py` | context from catalog fields; `map_load_at=row.anchor_at` |
| `src/backtest/run.py` | `load_replay_lookups` sources pauses/winner from catalog |
| `src/backtest/report_capital.py` | `radiant_win` from catalog |
| `src/shared/utils/telonex_tape.py` | window start = `entry.anchor_at` |
| `Makefile` | `link-archive`, `link-report` targets; `collect`/`collect-dry-run` ordering |
| `tests/test_window_ids.py` | rewrite for the admission worklist (window OR archive; sort_ts order) |
| `tests/test_match_catalog.py` + `tests/catalog_fixtures.py` | new required columns; archive-row cases (no spawn, archive horn, archive pauses); add `MATCH_LINKS_PATH`/`MATCH_LINK_AUDIT_PATH` to `STAGE_PATH_NAMES` |

### Delete

- `s04`: `live_paper_map_load_unix`, `steam_state_map_load_unix`,
  `LIVE_PAPER_SOURCE`, the fallback branch in `resolve_window`.
- `shared/utils/match_time.py::get_game_ended_at` (single caller inlined into
  `get_state_available_ts` in s06).

No new dependencies; no abstractions beyond the two row TypedDicts and the two
schedule helpers.

---

## 6. Tests and commands

Targeted only (never the full suite during a live map):

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# focused tests
PYTHONPATH=src uv run python -m pytest \
  tests/test_merge_archive_links.py \
  tests/test_archive_pauses.py \
  tests/test_window_ids.py \
  tests/test_match_catalog.py

# lint + types (pre-commit hook runs ruff; basedpyright directly)
make lint
uv run basedpyright

# data flow (archive index first so universe_game_number is populated)
make archive-index
make link-archive          # s03: merge -> match_links + audit + funnel print
make grid                  # s04 (cached unless --fetch)
# s05/s05b fetch the ~94 recovered ids (needs network budget)
make opendota stratz prices
make catalog
make link-report           # funnel
```

The s03 merge itself is pure-parquet work — it is exercised in tests with small
synthetic link/index/universe frames written to tmp_path (conftest already
redirects tmp to `/var/tmp/pytest-esports-trader`).

Required focused checks (each is one test or a reported counter):

- attach by exact condition (incl. no-steam archive onto an existing link);
- new link row from steam-only archive with all `opendota_*` null;
- `no_steam_no_link` audit row;
- steam mismatch attaches with flag (winner agrees) / excludes (winner
  disagrees); map mismatch excluded; series-decider rule honored;
- `pauses_from_schedule` never returns `[]` for an unobserved pause;
- catalog: archive-admitted row without grid window but with stratz+prior+
  opendota-or-archive pauses enters; same row without any pauses source drops
  with reason logged;
- window_ids: link without window but with archive is in the worklist.

---

## 7. Verification

### Funnel — `make link-report` (s07)

Prints, per cohort:

```text
universe candidates            N
opendota links                 N
archives scanned / admitted    N / N   (index refusals by reason)
match_links                    N  (opendota N | archive N | both N)
  new links from archives      N  <- candidates, NOT ready matches
  audit: attach_by_condition N, attach_by_match N, new_link N,
         no_steam_no_link N, excluded_* N
admitted (window OR archive)   N
catalog rows                   N  (by horn_source / link_source)
  drop reasons: admission N, stratz N, prior N, ended_at N, gamma N, winner N
split: train N | validation N  (from research split when present)
```

This is the spec's "кандидат → связь → допуск → каталог → split" gate and the
direct guard against reporting new links as ready datasets.

### Expected values on first real run (from current artifacts)

- 210 admitted Dota archives; ~114 attach by condition (incl. 2 no-steam),
  ~94 create new links, 2 `no_steam_no_link`, 3 flagged `archive_steam_mismatch`,
  0 expected `map_mismatch`/`sides` conflicts — recount after the `opt_int`
  fix + `make archive-index` rerun (the map audit is currently dead).
- Catalog delta: every previously-cataloged `match_id` must remain present
  (same `spawn_at`, `ended_at`, `radiant_prior`, gamma fields — diff the old
  and new parquets on the shared columns); the only allowed losses are
  PGL-only rows (already gone in step 0) and live_paper-fallback-only rows
  without an admitted archive — list them explicitly in the run log.
- New archive-only rows appear in the catalog only after s05/s05b actually
  return usable OpenDota/STRATZ payloads; rows that don't get there keep their
  link with the drop reason in the s06 log and the funnel — not silently
  absent, not "ready".
- `pauses diverge` warnings reviewed on overlap rows (archive-derived vs
  OpenDota pauses).
- Market cache sanity: for one archive-admitted catalog row,
  `market_seconds_cache_path` exists and `state_ts_us` equals
  `horn_at + second + post-horn pauses` computed from `pauses_json` — no
  second offset.
- `grep` confirmation: `TRAIN_LAG_SECONDS` still 10; no no-XP filter by feed
  source; no `--policy` usage added.

---

## 8. Risks and non-goals

### Risks

- **Identity conflicts are real, not hypothetical**: 3 steam mismatches
  already exist (same game by winner+duration → stale stamp, attach+flag).
  If a winner-disagreeing case appears, it is excluded and must be read in the
  audit — never silently dropped into a count.
- **`opt_int` fix re-activates the map audit**: re-running `archive-index`
  could newly flag `map_mismatch`/`identity` refusals, shrinking the admitted
  set. Expected ~0; any nonzero count needs explanation before proceeding.
- **Cohort shrinkage from deleting the `horn−90` fallback**: links that only
  ever had a live_paper window and no admitted archive leave the catalog.
  This is the spec'd fix; the funnel makes the loss visible. If the count is
  large, surface it to the user rather than quietly absorbing it.
- **Pauses reconstruction fidelity**: archive-derived pauses depend on
  `paused` flags surviving feed gaps; boundary-unobserved cases correctly
  return `None` and cost the row its `ended_at` (drop, not fake). The
  divergence cross-check on overlap rows is the empirical guard.
- **Series-contract archives**: `series_winner` rows only link under the
  existing decider rule; archives bound to the series market during a
  non-decider map attach via match only when the map itself is linked — the
  audit shows them either way.
- **`start_time` semantic shift for archive rows** (horn vs map-load):
  bounded ~2 min, only affects ordering and a fixed past split boundary
  (`VALIDATION_START_TIME` ≈ 2026-06-05 predates all archives) — documented,
  accepted.
- **API budget**: ~94 recovered ids need OpenDota + STRATZ fetches; OpenDota's
  free-tier budget flag (`--budget`) still applies.
- Index `schedule_path` is absolute; catalog stores `archive_root` +
  `archive_id` so consumers rebuild paths via `schedule_path_for` rather than
  trusting stored strings.

### Non-goals (explicitly out of scope)

- Applying schedules in backtest, archive-driven arrival times, per-tick
  observed clocks — step 3A.
- `--policy archive|current`/`--live-since` removal, mixed-model report,
  Makefile backtest flags — step 3B.
- Changing train lag (both Dota models stay at 10 s; LoL stays at 10 s).
- New Oddin or PGL simulators / profiles.
- Any `poly-maker` change (frozen).
- `session.json` support; treating `session.jsonl` as a required input.
- Saved live predictions / live feature values as dataset substitutes.
- Winner as a model feature (it is outcome/settlement only).
- Treating a successful link as a ready dataset; linking is candidate
  recovery, admission is the OR-gate, readiness is catalog+split.
- Removing or redesigning old backtest policy modes; building a new
  experimental mode system.
- LoL linking changes (LoL keeps its existing universe/link pipeline; the
  schedule mechanism parity is step 3).
