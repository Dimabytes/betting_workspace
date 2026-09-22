# Step 2 — implementation report

Dota linking and shared dataset admission. Implemented per `plans/step-2.md`.

Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`.
Commit: `de273edd` — `feat: [step 2] - Dota linking and shared dataset admission` (34 files, +2144/-519). Not pushed.

## What was built

### s03 — merge admitted archives into `match_links` (`src/collect/s03_merge_archive_links.py`, new)

- Base = `opendota_links.parquet` rows stamped `link_source="opendota"`; OpenDota fields untouched.
- Every `admission=="admitted"` Dota index row resolves to exactly one outcome, recorded in `match_link_audit.parquet`:
  - `attach_by_condition` — archive condition (case-insensitive) hits a link row; provenance stamped on it. Steam mismatch is tolerated only when the OpenDota winner does not contradict the archive winner (`identity_conflict="archive_steam_mismatch"`); a winner contradiction is `excluded_winner_mismatch`.
  - `attach_by_match` — no condition hit but `steam_match_id` hits a link row; `archive_condition_id` records the archive's own market (`identity_conflict="archive_market_differs"`).
  - `new_link` — no hit either way but the archive has a steam id and usable horn: creates an archive-only row (`link_source="archive"`) with all OpenDota/GRID fields null.
  - Rejections: `excluded_steam_condition_conflict` (condition and steam point at different rows / slot taken / row already carries an archive), `excluded_map_mismatch`, `excluded_series_rule` (series contract that is not the decider map per `series_winner_covers_map`), `excluded_horn_inconsistent` (index horn != published schedule identity horn, or horn unusable on the create path), `no_steam_no_link`.
- Candidates processed map_winner-first then by archive_id, so a later run attaches to an earlier archive-created row instead of duplicating it.
- `validate_match_links` enforces unique `(event_id, game_number)`, `match_id`, `map_condition_id`, `archive_id`, monotonic ordering within an event, and fails archive-created rows carrying fabricated OpenDota fields.

### Schedule pauses (`src/archive_index/schedule.py`)

- `read_schedule(path) -> FeedSchedule` and `pauses_from_schedule(schedule) -> list[OpenDotaPause] | None`.
- Consecutive paused ticks collapse into one pause; `time` = game_second of the first paused tick; duration from bracketing server timestamps. Returns `None` when a boundary is unobserved (record opens paused or ends mid-pause); `[]` only for a complete pause-free record.

### Admission (`src/collect/common/window_ids.py`)

- `load_admitted_match_ids()`: `map_condition_id` in GRID windows OR `archive_id` non-null, ordered by `sort_ts`, `match_id`. Consumed by s05 and s05b.
- s05a anchors on the GRID spawn when present, else the archive horn (`archive_horn_at_utc`).
- s04 reads `match_links`, skips rows without OpenDota timing (`skipped_archive_links`), and writes GRID windows only — the live-paper/`horn - 90` spawn fallback is deleted (`live_paper_map_load_unix` gone, `resolve_window` returns a window or None).

### Catalog timing contract (s06, `dataset.py`, `match_catalog.py`)

`MatchCatalogRow` / `CatalogEntry` now carry:

- `spawn_at` — GRID `startedAt`; `None` on archive-only rows (horn is never converted into a fake spawn).
- `horn_at` + `horn_source` (`"archive"` = the archive match.json horn verbatim; `"grid_derived"` = `get_horn_datetime(spawn, pauses)`).
- `pauses_json` + `pauses_source` (`"opendota"` preferred, else `"archive"` schedule reconstruction; `null` = unknown → row dropped).
- `ended_at` = `get_state_available_ts(horn, duration, pauses)`.
- `radiant_win` + `winner_source="stratz"` (`didRadiantWin` via `try_load_stratz_winners`); archive-vs-STRATZ winner conflicts drop the row (`winner_conflict` check).
- `archive_id`, `archive_root`, `archive_feed_source`, `schedule_fingerprint`.
- `CatalogEntry.anchor_at` = spawn when known else horn — used by availability checks.
- `catalog_entry_from_row` raises on null `pauses_json` instead of silently coercing to `[]`.
- `get_game_ended_at` deleted; `opt_int`/`opt_float` now handle NumPy scalars.

s06 splits into `assemble_catalog_frame` / `check_masks` / `keep_complete` / `build_match_catalog_frame`; the ordered masks (admission → stratz → prior → ended_at → gamma → winner → winner_conflict) are reused by s07 for first-failure attribution, and dropped match_ids are logged.

### Consumers

- `build_market_context` takes horn/pauses/winner from the catalog entry; replay window from `ended_at`.
- `load_replay_lookups` reads `catalog[m].pauses` — no more per-match `get_opendota_match` in the backtest path.
- `select_validation_matches` availability uses `anchor_at`.
- `report_capital` settles from `catalog[match_id].radiant_win`/`radiant_token_index`.
- `telonex_tape` uses `entry.anchor_at`; `build_market_data` resolves catalog rows for the market-second cache.
- `s07_link_readiness.py` (new, `make link-report`): universe → opendota links → archive index → match_links → admission → catalog → split funnel with per-reason drop counts.

### Pipeline

`make collect` is now `universe link link-archive grid opendota prices stratz catalog`; `collect-dry-run` covers s02/s03/s04/s05a/s05b from cache; `link-archive` and `link-report` targets added.

## Data flow (run on real data)

- s03: 4875 base links; 210 admitted archives → 114 `attach_by_condition`, 96 `no_steam_no_link`, **0 archive-created links**; split 4761 opendota / 114 both.
- s04: 3187 GRID windows, 1688 unmatched, 0 skipped archive links.
- s05a: 3198 targets → 3168 pregame quote rows. s05b: 3176/3198 usable STRATZ.
- s06: **kept 3147, dropped 1728**; `winner_conflict` 0; two non-fatal `pauses diverge` warnings on overlap rows (±1s pause-boundary differences between OpenDota and schedule reconstruction — expected).
- s07 funnel: catalog 3147 = 3040 `grid_derived` + 107 `archive` horn; drops attributed admission 1677 / stratz 22 / prior 29 / ended_at 0 / gamma 0 / winner 0.

## Verification

- `PYTHONPATH=src uv run python -m pytest tests/test_merge_archive_links.py tests/test_archive_pauses.py tests/test_window_ids.py tests/test_match_catalog.py` — **41 passed**.
- Wider touched-area run earlier: 170 passed (grid_starts, fetch_opendota/stratz, market_data, telonex_tape, match_time, backtest_validation, prepare_dataset, live_archives).
- `make lint` (ruff check + ruff format + basedpyright whole-project): **all pass**.
- `TRAIN_LAG_SECONDS` still 10; no feed-source XP filter; no `--policy`/`--live-since` usage added; `poly-maker` untouched; no Step 3A/3B scope.
- Invariants exercised by tests: unique keys, monotonic event ordering, no fabricated OpenDota fields on archive rows, archive-only pauses unknown → dropped, diverge warning fires.

## Notes / caveats

- On this dataset zero archives created new links: every admitted archive either attached by condition (114) or lacked both a steam id and a link target (96). The `new_link` path is covered by unit tests, not by live data.
- `test_follow300_replay` seed-0 golden for dota-8837869969 was observed drifting (5 vs 6 fills) after the on-disk catalog rebuild. All catalog timing fields for that match equal the old derivation (horn/ended_at/pauses/winner identical), so the delta comes from refreshed upstream stage data (re-linked links, refetched STRATZ index incl. 2 new network fetches), not the timing contract. Not investigated further per instruction; flagging for the orchestrator.
- `pauses diverge` warnings are informational; OpenDota remains the preferred pause source on overlap rows.
