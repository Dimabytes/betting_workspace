# Step 3B — review-fix report

Scope: fixes applied to `esports-trader` `main` on top of `85eccfc8`
(`feat: [step 3B] - ordinary backtest launch and mixed-model report`),
per `step-3b-review-fix-brief.md` and the Grok 4.6 review in
`step-3b-review.md`.

Commit: `25734ed0` — `refactor: [step 3B] - review fixes: shared replay gates, typed feed_source, test split` (13 files, +899/-713).

No push performed. `--policy`/`--live-since` not revived. `poly-maker`,
`polymarket-collector`, `current-task/`, and on-chain scripts untouched.

---

## Blockers — both done

### B1. Test modules recede under 1k (review §5, blocker 1)

Extracted two files:

- `tests/test_since_match.py` (531 lines) — the 3B cohort surface:
  - `--since-match` CLI parse tests (accept, requires `--name`, excludes
    other selectors)
  - Dota selector tests (tail, gates, anchor-ineligible, unknown anchor,
    empty cohort) + new out-of-order catalog fixture
  - LoL selector tests (horn ordering, anchor needs rows, empty cohort)
  - `load_run_selection` probe-pin tests (selected_ids + limit)
  - `reject_schedule_flags` schedule-vs-grid-v1 cases
  - `resolve_feed_plans` match-id refusal and mixed cohort tests
  - manifest feed-map tests and the provenance naming test
- `tests/feed_schedule_fixtures.py` (210 lines) — shared builders
  (`build_tick`, `build_schedule`, `build_index_row`,
  `build_dota_catalog`, `build_schedule_plan`,
  `build_manifest_model_dir`, `ArchiveLayout`, `layout`) used by both
  `test_feed_schedules.py` and `test_since_match.py`, following the
  repo's `*_fixtures.py` convention. The `layout` fixture is imported
  with the `as` re-export idiom so pytest registers it and ruff sees an
  intentional re-export.

Result: `test_backtest.py` 1223 → 975, `test_feed_schedules.py`
1108 → 794. Both under the 1k hard line.

### B2. Dota `--since-match` loads once and pins (review 2.1, blocker 2)

`load_run_selection` Dota arm now mirrors LoL exactly:

```python
probe = load_dota_selection(None, None, validation_dataset=validation_dataset)
since_ids = select_dota_since_match_ids(probe.sources, since_match)
if limit is not None:
    since_ids = since_ids[:limit]
dota = replace(probe, selected_ids=since_ids, coverage=None)
```

One validation-parquet/catalog load instead of two; the selector stays
a pure function pinned after the load. `since_match` is not threaded
into `load_dota_selection`.

## Should-fixes — all applied

### 2.2 Shared replay gates

`selection.py` gains `_has_replay_days(row)` (availability-window +
`has_local_telonex_days`) and `_dota_replayable(sources, match_id, row)`
(the three-gate conjunction). `select_validation_matches` keeps its
reason buckets but calls `_has_replay_days` for the last gate;
`select_dota_since_match_ids` uses `_dota_replayable`.

### 2.3 + 4.1 feed_source de-overloaded, Literal-typed

- `SignalProvenance.feed_source` and `MakerMatchResult.feed_source` are
  `Literal["grid", "oddin", ""]`. Grid-v1 rows carry `""` (unbound)
  instead of a second spelling of `signal_mode`.
- `ScheduleBinding.feed_source` and `_ArchiveRow.feed_source` are
  `Literal["grid", "oddin"]`; `_bound_feed_source` normalizes at index
  parse (matches the `s03_merge_archive_links` writer contract of
  exactly grid|oddin).
- `MakerMatchResult.signal_group` is a derived `@property`
  (`signal_mode` when feed unbound, else `signal_mode:feed_source`) —
  the single label formatter. `summarize_arm` only counts; the
  schedule-vs-grid_v1 special case is gone. No third parquet column
  (properties are not dataclass fields).

### §3 plans[match_id] typed dispatch

`plans.get(context.match_id)` → `plans[context.match_id]` in
`build_kernels` (renamed from `build_current_kernels` — the
`current_` prefix had no archive counterpart left) and in
`build_strategy_configs`. A missing key is now a bug, not a silent
grid default; `isinstance(plan, SchedulePlan)` remains the dispatch.
The `test_extraction_oracle` in-process replay was passing `plans={}`
to both `run_batch` and `build_kernels` — it now passes a real
`GridV1Plan` for the match.

### 4.2 `_signal_rows` reads required keys

`arm["signal_groups"]`, `arm["model_groups"]`, and
`manifest["archive_exclusions"]` are read directly; the
`.get(..., {})` / `isinstance` / `cast` soft-schema shim is gone
(manifest parameter retyped to `Mapping[str, Any]`). Three postprocess
manifest fixtures gained `"archive_exclusions": {}` to match the
always-emitted manifest contract.

### 4.3 Explicit cohort sort

`select_dota_since_match_ids` now does
`cohort.sort(key=lambda mid: (sources.catalog[mid].start_time, mid))`
— the `(start_time, match_id)` contract LoL has, with no dependency on
`MatchCatalog`'s internal ordering. New
`test_select_dota_since_match_ids_sorts_cohort_by_start_time` inserts
catalog rows out of chronological order.

### Plan-G test gaps

- `test_reject_schedule_flags_refuses_overrides_on_a_schedule_plan`
  (all three knobs) and
  `test_reject_schedule_flags_allows_overrides_on_grid_v1_only`.
- `test_load_run_selection_since_match_pins_ids` (asserts one probe
  load, pinned ids, `coverage=None`) and
  `test_load_run_selection_since_match_applies_limit`.

## Nits — applied

- `--limit` help: "first N eligible cohort matches".
- `build_current_kernels` → `build_kernels` rename (def + 3 call
  sites).
- `test_run_manifest_omits_since_match_without_the_flag` docstring now
  says `since_match` appears only when used while `archive_exclusions`
  is always present — the assertion was already right.
- `backtest_policy`/`signal_source` fossils left alone per review.

## Not done (per review's do-not list)

- No `run.py` split, no `test_backtest_postprocess.py` split, no
  `live_archives.py` move, no `SinceMatchSelection`/`SignalGroup` types,
  no `signal_group` parquet column, no tripwire removal, no silent
  archive→grid fallback.

## Verification

- `pytest` on the touched surface — `test_since_match.py` (22),
  `test_feed_schedules.py`, `test_backtest.py`,
  `test_backtest_validation.py`, `test_backtest_postprocess.py`,
  `test_lol_backtest.py`, `test_live_archives.py`,
  `test_extraction_oracle.py`, `test_backtest_signals.py` —
  237 + 24 passed.
- `ruff check`, `ruff format`, `basedpyright` (0 errors) clean;
  `make lint` pre-commit hooks all pass.
- `backtest.run --help` verified.
- `test_follow300_replay` seed0 golden: fails with the identical
  pre-existing drift (`fills length 5 != 6`) documented at clean HEAD —
  the subprocess replay runs end-to-end through `plans[match_id]`
  (proving the oracle's real-plan fix), and the mismatch is the known
  local-data drift, not a new error.
EOF