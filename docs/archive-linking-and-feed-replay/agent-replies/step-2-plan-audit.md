# Step 2 plan audit — Dota linking and shared dataset admission

Audited against `plans/step-2.md` (re-read in full) and the «Шаг 2» section of
`esports-trader/docs/superpowers/specs/2026-09-20-archive-linking-and-feed-replay-design.md`.
Commits under audit: `de273edd` (implementation) + `0e40b87c` (audit fix, see below).
Repo: `esports-trader` on `main`, not pushed. `poly-maker` untouched (`git status` clean).

## Headline findings

1. **One real defect found and fixed this session** (`0e40b87c`):
   `archive_index/index.parquet` stores `steam_match_id` in an object/string
   column; `load_candidates()` called `opt_int()`, which only accepts numeric
   scalars, so all 94 steam-carrying archives were dropped to
   `no_steam_no_link` and the merge reported **`new_link: 0`**. The `0` was a
   code bug, not a data fact. Fix: `opt_str()` + digit check in
   `s03_merge_archive_links.py:119-120`, plus regression test
   `test_load_candidates_parses_string_steam_match_id`. Post-fix merge:
   `attach_by_condition 113, new_link 93, excluded_steam_condition_conflict 2,
   no_steam_no_link 2` — reconciles exactly with the plan's preview
   (94 steam-carrying non-attached = 93 new + 1 conflict `grid-3004579-m2`;
   the predicted `grid-3007246-m2`/`grid-3007988-m2` are the 2 no-steam rows).
2. **`make archive-index` was rerun** after the earlier `opt_int` NumPy fix:
   `archives=511 admitted=485 schedules=485`; Dota: `admitted 210`,
   `duplicate_of:9007118998 1`, `feed:no_window_updates 10`,
   `record:no_terminal 9`, `universe_game_number` non-null on 180/210
   (map audit is live again), `identity_status: all ok`, `steam_match_id`
   non-null 206. No new map/identity refusals — matching the plan's "~0
   expected" risk note.
3. **Full data flow rerun** after the fix: GRID (`skipped_archive_links: 93`),
   OpenDota (`to_fetch=95, saved=95, failed=0`), prices
   (`pregame_quote_rows: 3263`), STRATZ (`index_usable=3266/3291`, ~3
   `missing_leads` unusable), catalog rebuilt: **3237 rows**
   (horn_source: 3041 grid_derived / 196 archive; 97 archive-only rows with
   `spawn_at=null`). Funnel (`make link-report`) is self-consistent end to end.
4. **Catalog preservation verified by actual comparison**: every `match_id`
   in the old-built artifacts is present in the new catalog —
   `split.parquet` 1710/1710, `training_dataset` 1152/1152,
   `validation_dataset` 558/558, **zero missing**. The 15 pure
   live_paper-fallback losses (see §4 below) are absent from all three
   artifacts, so there is no evidence they were ever cataloged; the old
   catalog parquet itself was overwritten, so the split/dataset comparison
   is the strongest available preservation check.
5. **One winner conflict surfaced, not hidden**: `grid-3006421-m2` /
   match `8996785570` — archive `winner=radiant` vs STRATZ
   `didRadiantWin=false` → dropped by the `winner_conflict` check and logged
   (`catalog winner_conflict failed=1`). Exactly the behavior §8 requires.

## Section 1 — Current flow traced (plan §1)

| Item | Status | Evidence |
|---|---|---|
| OpenDota stays an independent candidate source | DONE | s02 untouched; `merge_archive_links` uses `opendota_links` rows as the base and only attaches/creates (`s03:516-518`). |
| `universe -> link -> link-archive -> grid -> opendota -> prices -> stratz -> catalog` | DONE | `Makefile:71` `collect: universe link link-archive grid opendota prices stratz catalog`; `collect-dry-run` includes s03 between s02 and s04. |
| `opt_int`/`opt_float` NumPy fix (dead map audit root cause) | DONE | `shared/utils/parsing.py` accepts `np.integer`/`np.floating`; committed in `de273edd`. Post-fix `universe_game_number` populated on 180/210 admitted rows. |
| `make archive-index` rerun after the fix | DONE | Output above; rerun performed this session before the corrected merge. |

## Section 2 — Merge design `match_links` (plan §2)

| Item | Status | Evidence |
|---|---|---|
| New stage `s03_merge_archive_links.py`; `--index` override; fails hard on missing index | DONE | `main()` takes `--index` (`s03:544-548`); `publish_match_links` raises `SystemExit` when absent (`s03:510-511`). |
| `MATCH_LINKS_PATH`/`MATCH_LINK_AUDIT_PATH` constants | DONE | `src/collect/common/paths.py`. |
| `MatchLinkRow`/`MatchLinkAuditRow` TypedDicts with all spec'd columns | DONE | `src/collect/common/catalog_types.py`; column lists derived from the TypedDicts (`s03:48-49`). |
| `match_id`/`sort_ts`/`archive_steam_match_id` written Int64 | DONE | `INT64_COLUMNS` cast before write (`s03:50-57,520-521`). |
| Resolution enum incl. all 9 values | DONE | Implemented and tested; real run produced `attach_by_condition 113, new_link 93, excluded_steam_condition_conflict 2, no_steam_no_link 2` (no `attach_by_match`/`excluded_map_mismatch`/`excluded_series_rule`/`excluded_horn_inconsistent`/`excluded_winner_mismatch` occurred in the data). |
| Rule 1 exact condition attach + map check + steam-mismatch flag + winner cross-check + horn sanity | DONE | `_attach`/`resolve` helpers in s03; tests `test_attach_by_exact_condition_sets_provenance`, `test_steam_mismatch_attaches_with_flag_when_winner_agrees`, `test_steam_mismatch_excluded_when_winner_disagrees`, `test_condition_attach_map_mismatch_excluded`, `test_horn_inconsistent_excluded`. Real data: 2 flagged `archive_steam_mismatch` (`grid-2996018-m1`, `grid-2996031-m3`); the third predicted case (`grid-2996008-m2`) resolved as `excluded_steam_condition_conflict` because its stale steam stamp points at a different link row — the plan's own collision rule, correct. |
| Rule 2 match attach (`archive_market_differs`) | DONE | `attach_by_match` path + `identity_conflict="archive_market_differs"` (`s03:457`); tested. 0 occurrences in real data — preview predicted none. |
| Rule 3 new link row (map_winner / series-decider / conflicts) | DONE | `series_winner_covers_map` gate (`s03:385`), canonical condition casing, `radiant_token_index` from `yes_token_index`/`yes_is_radiant`, `sort_ts` = archive horn unix. Tests cover create, series decider, both exclusions, and slot collision. Real: 93 `new_link`, all `opendota_*`/`match_start_time`/`grid_clock_seconds` null (verified). |
| `no_steam_no_link` audit-only | DONE | Real: `grid-3007246-m2`, `grid-3007988-m2` — the two archives the plan named. |
| `excluded_steam_condition_conflict` | DONE | Real: `grid-2996008-m2` (condition vs steam point at different rows), `grid-3004579-m2` (row already carries `grid-3004579-m1`). Both have explicit audit `detail` strings. |
| Validation mirrors `s02.validate_links` + archive uniqueness + no-faked-fields + monotonic start_time | DONE | `validate_match_links` (`s03:485-506`); `test_archive_only_row_with_fabricated_fields_fails_validation`. |
| Late OpenDota enrichment merges instead of duplicating | DONE | Base rows are OpenDota links; a later-linked condition/match attaches via rules 1–2. Structural — covered by attach tests. |
| Non-admitted archives never iterated; index refusals printed | DONE | `load_candidates` filters `admission == "admitted"` and returns refusal counts (`s03:106-112`); funnel prints them. |
| `0 new_link` original result explained | DONE | String-typed `steam_match_id` rejected by `opt_int` — code bug, fixed (`0e40b87c`), regression test added. |

## Section 3 — Admission gate (plan §3)

| Item | Status | Evidence |
|---|---|---|
| `load_admitted_match_ids()` = window OR archive-attached, ordered by `sort_ts` | DONE | `collect/common/window_ids.py`; `test_window_ids.py` rewritten for the OR rule. |
| s05/s05b consume admitted worklist | DONE | Both call `load_admitted_match_ids()`; s05 fetched all 95 missing ids, s05b all 93 recovered. |
| Refused archives can't bypass the filter | DONE | Only `admission=="admitted"` rows merge; audit-only rows never reach `match_links`; admission = `archive_id` non-null. |
| s05a anchors = GRID spawn ∪ archive horn for windowless rows | DONE | `load_anchors` (`s05a:56-74`): spawn first, else `archive_horn_at_utc`. |
| s06 counts linked-but-not-admitted as `admission failed` | DONE | `check_masks` first check `spawn_at.notna() \| archive_id.notna()` (`s06:148`); log: `catalog admission failed=1677`. |
| No split-specific admission added | DONE | `prepare_dataset/` untouched by `de273edd` (commit stat). |
| Makefile `link-archive` + `collect`/`collect-dry-run` ordering | DONE | `Makefile:50,71` and dry-run list. |

## Section 4 — Time / pause / horn contract (plan §4)

| Item | Status | Evidence |
|---|---|---|
| `MatchCatalogRow` gains `spawn_at (nullable)`, `horn_at`, `horn_source`, `pauses_json`, `pauses_source`, `ended_at`, `radiant_win`, `winner_source`, `archive_*`, `schedule_fingerprint` | DONE | `shared/types/dataset.py`; catalog parquet carries all columns (verified by read). |
| `ended_at = get_state_available_ts(horn, duration, pauses)` on both paths | DONE | `s06:128-131`. |
| OpenDota+GRID: `grid_derived` horn from spawn+90+pre-horn pauses | DONE | `s06:122-125`. |
| Archive-attached: direct `archive_horn_at_utc`, `horn_source="archive"` preferred even when a GRID window exists | DONE | `s06:118-120` — archive branch wins whenever `archive_id` set. Real: 196 archive-horn rows, 99 of which also have `spawn_at` from GRID. |
| `spawn_at` = GRID `startedAt` only; never `horn - 90` | DONE | spawn comes solely from `grid_game_windows`; archive-only rows keep `spawn_at=null` (97 such catalog rows). |
| Pauses: OpenDota preferred, archive schedule fallback, unknown stays unknown | DONE | `resolve_pauses` (`s06:79-98`); real: all 97 archive-only catalog rows use `pauses_source="opendota"` (the recovered caches provided them). |
| Unknown pauses → null `pauses_json` → null `ended_at` → catalog drop, never `[]` | DONE | `pauses_json` written only when `pauses is not None` (`s06:134`); `ended_at` requires pauses (`s06:128`); `CatalogEntry` strictly parses `pauses_json` (null rejected). No `or []` coercion anywhere in the path. |
| `pauses_from_schedule`: run extraction, first-paused-tick `time`, bracketing `duration`, `None` on unobserved boundary, `[]` only for run-free record | DONE | `archive_index/schedule.py`; `test_archive_pauses.py` covers all four cases. |
| `pauses diverge` cross-check logged, non-fatal | DONE | `pauses_diverge` + warning (`s06:70-94`). Real run: 3 warnings (`9007618656`, `9007700576`, `9008125103`), all ±1 s boundary-reconstruction diffs — benign. |
| **Spawn double-count truly gone** | DONE | `grep` on s04: no `live_paper_map_load_unix`, `steam_state_map_load_unix`, `LIVE_PAPER_SOURCE`, `STATE_ARCHIVE_FILENAME`, `TRADER_DIR`, `MATCH_META_FILENAME`, `iter_archive_records`, `match_archive_dir`, `HORN_OFFSET_SECONDS`, no `horn - 90` branch. `HORN_OFFSET_SECONDS` survives only inside `match_time.py`'s legitimate `get_horn_datetime`/`get_paused_seconds_before`. GRID windows mean only GRID `startedAt`. Post-horn pause shifts `ended_at` not `horn_at` (test in `test_match_time.py`). |
| Fallback-loss cohort surfaced, not hidden | DONE | 24 links lost windows after the fallback deletion; 9 recovered via admitted archives; **15 pure losses**: `8992284302, 8992390628, 9005874165, 9005881535, 9005944925, 9005981988, 9006010487, 9006060043, 9006097685, 9006147431, 9007118998, 9007720090, 9007874144, 9007989894, 9008297395`. None appear in `split`/`training_dataset`/`validation_dataset` — no evidence any were previously cataloged (the old catalog parquet was overwritten; this is stated as an artifact-based check, not a definitive historical diff). Loss is visible via `admission failed` + funnel. |

### Consumer changes (plan §4 table)

| Item | Status | Evidence |
|---|---|---|
| s06: contract build, winner from STRATZ + archive cross-check, all old checks kept | DONE | `check_masks` (admission/stratz/prior/ended_at/gamma/winner/winner_conflict); `assert frame["horn_at"].notna().all()` (`s06:235`). Real: `winner failed=0`, `winner_conflict failed=1` (`grid-3006421-m2`, archive=radiant vs stratz=dire — excluded and logged, per §8). |
| `CatalogEntry`: `horn_at`, nullable `spawn_at`, strict `pauses`, `radiant_win`, archive provenance, `anchor_at`, sort by `(start_time, match_id)` | DONE | `shared/utils/match_catalog.py`. |
| `build_market_data`: `row.horn_at`/`row.pauses` direct; opendota-pauses plumbing dropped | DONE | `resolve_catalog_row` (`build_market_data.py:66-78`); no `try_load_opendota_pauses` in the file. |
| `selection.build_market_context`: catalog fields; `map_load_at=row.anchor_at`; pauses/winner params dropped | DONE | `backtest/selection.py`; `test_backtest_validation.py` updated. |
| `run.load_replay_lookups`: pauses + winner from catalog, no OpenDota file required | DONE | `backtest/run.py`. |
| `report_capital`: `radiant_win` from catalog | DONE | `backtest/report_capital.py`. |
| `get_game_ended_at` deleted (single caller inlined) | DONE | `grep` finds zero references; `match_time.py` no longer defines it. |
| `telonex_tape.match_passes_tape_buckets` uses `entry.anchor_at` | DONE | `shared/utils/telonex_tape.py`; `test_telonex_tape.py` updated. |
| STRATZ `didRadiantWin` reader (no new fetch) | DONE | `shared/utils/stratz.py` (+15 lines in commit). |

### Explicitly not changed (scope boundaries)

| Item | Status | Evidence |
|---|---|---|
| No step-3A schedule replay / arrival times | DONE | `grep` for `schedule_replay`/`replay_feed`/`--policy` in `src/collect`, `src/backtest`: nothing added. `strategy._clock_at` untouched (not in commit stat). |
| No step-3B CLI/policy cleanup | DONE | No `--policy`/`--live-since` changes in the commit. |
| `TRAIN_LAG_SECONDS` still 10; LoL lag unchanged | DONE | `dataset.py:20 TRAIN_LAG_SECONDS = 10`; no `src/lol` files in commit. |
| No-XP not narrowed to Oddin/feed source | DONE | `NO_XP_FEATURE_COLUMNS`/`gbm.py` untouched; no feed-source filter in s06 or `match_catalog.py` (grepped). |
| `prepare_dataset` needs no code change | DONE | Not in commit stat. |
| No new dependencies/abstractions beyond plan's | DONE | `pyproject.toml`/`uv.lock` not in commit stat; new code = 2 TypedDicts + 2 schedule helpers + merge stage + report. |
| `poly-maker` untouched | DONE | `git status` clean in `../poly-maker`. |

## Section 5 — Exact file list

Every row of the **Add** table exists: `s03_merge_archive_links.py`,
`s07_link_readiness.py`, `tests/test_merge_archive_links.py`,
`tests/test_archive_pauses.py`. — DONE.

Every row of the **Edit** table changed in `de273edd` (commit stat verified):
`parsing.py`, `common/paths.py`, `common/catalog_types.py`,
`common/window_ids.py`, `s04`, `s05`, `s05b`, `s05a`, `s06`,
`types/dataset.py`, `utils/match_catalog.py`, `utils/match_time.py`,
`utils/stratz.py`, `archive_index/schedule.py`,
`market_data/build_market_data.py`, `backtest/selection.py`,
`backtest/run.py`, `backtest/report_capital.py`, `utils/telonex_tape.py`,
`Makefile`, `test_window_ids.py`, `test_match_catalog.py` +
`catalog_fixtures.py`. — DONE.

Every row of the **Delete** table confirmed absent by grep (s04 fallback
helpers/branch; `get_game_ended_at`). — DONE.

`STAGE_PATH_NAMES` includes `MATCH_LINKS_PATH`/`MATCH_LINK_AUDIT_PATH`
(`test_match_catalog.py:17-25`) — DONE.

## Section 6 — Required focused checks

| Check | Status | Evidence |
|---|---|---|
| attach by exact condition (incl. no-steam archive) | DONE | `test_attach_by_exact_condition_sets_provenance`, `test_attach_by_condition_accepts_no_steam_archive`; real: 2 no-steam attached (`grid-2996037-m1`, `grid-3005969-m2`). |
| new link row with all `opendota_*` null | DONE | `test_new_link_from_steam_only_archive`; real: all 93 verified null. |
| `no_steam_no_link` audit row | DONE | test + 2 real rows. |
| steam mismatch attach/exclude; map mismatch; series rule | DONE | tests for each; real: 2 flagged attaches, 2 conflicts. |
| `pauses_from_schedule` never `[]` for unobserved pause | DONE | `test_archive_pauses.py` (leading pause / open-at-end → `None`). |
| catalog: archive-admitted row without window but with stratz+prior+pauses enters; without pauses drops with reason | DONE | `test_match_catalog.py` archive-row cases; real: 97 archive-only rows in catalog, `ended_at failed=0` among admitted (drops attributable to stratz 25 / prior 27 / admission 1677). |
| window_ids: archive-only link in worklist | DONE | `test_window_ids.py`; real: `skipped_archive_links: 93` in s04, admitted count 3291 > windowed 3187. |

## Section 7 — Verification bullets

| Bullet | Status | Evidence |
|---|---|---|
| Funnel prints candidate→link→admission→catalog→split with refusal reasons | DONE | `make link-report` output: 5155 candidates → 4875 links → 4968 match_links (4762/93/113) → 3291 admitted → 3237 catalog → split 1152/558; index refusals and audit resolutions printed. |
| Expected first-run values (~114 attach / ~94 new / 2 no-steam / 3 flagged / 0 map mismatches) | DONE | Post-fix: 113 attach / 93 new / 2 no-steam / 2 flagged + 1 steam-condition conflict (`grid-2996008-m2`, the third predicted mismatch, resolves under the plan's own collision rule) / 0 map mismatches. Deviations fully explained. |
| Catalog delta: previously-cataloged ids preserved; only allowed losses listed | DONE | 0 losses vs split/train/validation artifacts (100% presence); 15 fallback-only ids listed above, absent from all artifacts. |
| New archive-only rows enter catalog only after usable fetches; failures keep link + drop reason | DONE | 93 new links → 90 STRATZ-usable → 97 archive-only catalog rows (includes attached-no-window rows); `missing_leads` unusables dropped with reason in s06 log/funnel. |
| `pauses diverge` warnings reviewed | DONE | 3 warnings, all ±1 s boundary diffs. |
| Market cache sanity: `state_ts_us == horn + second + post-horn pauses` | DONE | Freshly built cache for `8974647733` (archive-horn row): exact match at seconds 0/300/2259. Note: pre-existing caches (e.g. Sep-5 build) were written under the pre-step-2 sub-second horn and differ by ≤ ~0.1 s — stale-file artifact, converges on rebuild; immaterial vs the 60 s book-age tolerance. One archive-only row (`8975647643`) has no telonex books in window → no cache, correct `None` handling. |
| `grep` confirmations | DONE | `TRAIN_LAG_SECONDS = 10`; no no-XP-by-feed-source; no `--policy` additions. |

## Section 8 — Risks and non-goals

- Identity conflicts real: confirmed — 2 flagged attaches + 2 conflicts +
  1 downstream winner_conflict drop, all individually readable in
  `match_link_audit.parquet` / s06 log. HANDLED.
- `opt_int` fix re-activating map audit: confirmed — rerun produced 0 new
  refusals. HANDLED.
- Fallback-deletion cohort shrinkage: confirmed — 15 ids listed, none in
  datasets. HANDLED.
- Pause reconstruction fidelity: `None`-on-unobserved implemented and
  tested; divergence check live (3 benign warnings). HANDLED.
- Series-contract archives: rule implemented + tested; `grid-3004579-m2`
  shows the slot-collision path in real data. HANDLED.
- `start_time` semantic shift for archive rows: `anchor_at` documented;
  `VALIDATION_START_TIME` predates all archives. ACCEPTED per plan.
- Non-goals: no 3A/3B/poly-maker/LoL/lag/session.json/live-prediction
  changes — all verified above.

## Checks run this session

```text
PYTHONPATH=src uv run python -m pytest tests/test_merge_archive_links.py \
  tests/test_archive_pauses.py tests/test_window_ids.py tests/test_match_catalog.py -x -q
# 42 passed
make lint            # all hooks pass (staged files)
uv run basedpyright  # 0 errors, 0 warnings, 0 notes
```

Full suite was not re-run this session (user instruction: no suite during a
live map; earlier session reached 490 passing before the data-flow rerun).

## Skipped / deferred items

None in step-2 scope. Deliberately not done (out of scope): full-suite rerun
(live-map rule), `make market-data` for all 97 new rows (not required by the
plan; single-row sanity performed instead — remaining caches build on the
next scheduled run).

## Fixes applied during this audit

- `0e40b87c` — `load_candidates` parses string-typed `steam_match_id`
  (`opt_str` + `isdigit`), plus regression test
  `test_load_candidates_parses_string_steam_match_id`. This was a genuine
  step-2 defect (dead create-path for all 94 steam-carrying archives).
- Data flow rerun end-to-end with the corrected merge (archive-index →
  link-archive → grid → opendota → prices → stratz → catalog → link-report).
