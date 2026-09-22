# Step 2 review fix — response to `step-2-review.md`

Commit: `0d1ae767 refactor: [step 2] - close merge boundary leaks from review`
on `esports-trader` `main`, not pushed. `poly-maker` untouched.

All three blockers fixed, all three should-fixes fixed, two of three nits
fixed (the third — the `catalog_fixtures.catalog_row` default maze — the
review itself says leave it; it only recommends splitting fixtures *if*
another branch appears).

## Blockers

### 1. Horn consistency is now a load-time candidate gate — FIXED

`load_candidates` (`s03_merge_archive_links.py:101`) now rejects an admitted
index row before it reaches the merge when:

- `feed_source` is not `"grid"`/`"oddin"` → `excluded_incomplete_identity`
  (new `MatchLinkResolution` literal; the old `cast(ArchiveFeedSource, ...)`
  is gone — after the guard pyright narrows the Literal itself),
- the index horn is missing/unparseable, or differs from the published
  schedule's `identity.horn_at_utc` → `excluded_horn_inconsistent`.

Rejections become audit rows inside `load_candidates` (it now returns
`(candidates, rejected, refusals)`); `publish_match_links` prepends them to
the merge audit so every admitted archive still has exactly one audit row.
`ArchiveCandidate` carries `horn_at_utc: str` + `horn_unix: int` (both
proven at load) instead of the `schedule_horn_at_utc` sidecar;
`_horn_consistent` and the check inside `check_condition_attach` are
deleted. `derive_timing` keeps preferring archive horn — now without the
asterisk.

Evidence on real data: `make link-archive` rerun after the refactor prints
the identical funnel (`attach_by_condition 113, new_link 93,
excluded_steam_condition_conflict 2, no_steam_no_link 2`, 4968 links) —
all 210 admitted archives pass the load-time horn gate, matching the index's
`identity_status: ok`.

### 2. One steam-id parser at the boundary — FIXED

`parse_steam_match_id(value) -> int | None` added to
`shared/utils/parsing.py` (int/np.integer or digit-string → int).
`s03.load_candidates` calls it; `backtest/live_archives.py::_steam_match_id`
is deleted and `resolve_archive_match_id` calls the shared helper directly
(the review's two-parsers-one-identifier fold). `opt_int` was not widened.
The regression test `test_load_candidates_parses_string_steam_match_id`
stays.

### 3. `check_condition_attach` collapsed to `Rejection | None` — FIXED

Signature is now `(candidate, cond_hit, steam_differs, radiant_win_of) ->
Rejection | None`; the caller computes `steam_differs` once and passes the
`"archive_steam_mismatch" if steam_differs else None` flag to `_attach` —
same shape as the match-attach path's `"archive_market_differs"` one-liner.
The union return and the `isinstance(verdict, Rejection)` dispatch are gone.

## Should-fixes

### 4. First-failure drop counts shared by s06 and s07 — FIXED

`first_failure_counts(frame)` lives next to `check_masks` in s06 and is the
single counter: `keep_complete` logs it, `s07.report_catalog_drops` prints
it (the per-row position loop is deleted). Real run — both outputs now
identical: `admission 1677, stratz 25, prior 27, ended_at 0, gamma 1,
winner 0, winner_conflict 1`. `test_dropped_rows_are_logged_with_their_match_ids`
was updated and strengthened: one row fails `admission`, an archive-admitted
row fails `stratz`, so attribution past the first mask is actually
exercised.

### 5. One admission boolean — FIXED

`admitted_link_mask(links, windows)` in `collect/common/window_ids.py` is
the single formulation (`map_condition_id in windows OR archive_id
non-null`). `load_admitted_match_ids` calls it; `assemble_catalog_frame`
computes `frame["admitted"]` with it (before the `map_condition_id`→
`condition_id` rename) and `check_masks["admission"]` reads that column —
the catalog gate is now literally the worklist boolean; s07's
`report_admitted` calls it too. The temporary column is dropped by the
`MATCH_CATALOG_COLUMNS` select, so the published schema is unchanged.

### 6. Create-path token fields reject instead of assert — FIXED

`resolve_new_slot` now returns `NewLinkSpec | Rejection`. After the
contract-kind dispatch it refuses `yes_token_index`/`yes_is_radiant` gaps
with `excluded_incomplete_identity` ("archive lacks yes_token_index/
yes_is_radiant to place the radiant side") and returns the narrowed
identity (`match_id`, `game_number`, `condition_id`,
`radiant_token_index`). `_new_link_row(candidate, spec)` has zero asserts;
the series-inventory `assert` was also flattened into the guard.
Justification for the one new literal: emitting an existing literal would
make the funnel lie about the cause, which is exactly the review's stated
bar for adding one.

## Nits

- `skipped_archive_links` → `skipped_links_without_grid_clock` (s04). Done.
- `isinstance(..., str)` → `opt_str` in `s05a.load_anchors`. Done.
- Fixture default maze: left as-is per the review's own guidance.

## Deliberately not done

- No schedule replay, `--policy`/`--live-since` changes, train-lag changes,
  or `poly-maker` edits (3A/3B/frozen boundaries respected).
- `excluded_steam_condition_conflict` keeps its three collision cases with
  required `detail` strings — the funnel distinguishes them by detail, no
  literal split (review §3 guidance).
- `attach_by_match` remains 0 in real data — expected; the path is tested.

## Collateral fix found while running the affected batch

`tests/test_prices_history.py` still monkeypatched `OPENDOTA_LINKS_PATH` on
s05a — stale since Step 2 moved the stage to `MATCH_LINKS_PATH`
(`AttributeError` at fixture setup). Repointed; the written frame already
carries the columns `build_targets` reads.

## Verification

```text
PYTHONPATH=src uv run python -m pytest \
  tests/test_merge_archive_links.py tests/test_match_catalog.py \
  tests/test_window_ids.py tests/test_archive_pauses.py   # 43 passed

PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --group backtest python -m pytest \
  tests/test_merge_archive_links.py tests/test_archive_pauses.py \
  tests/test_window_ids.py tests/test_match_catalog.py \
  tests/test_live_archives.py tests/test_prices_history.py \
  tests/test_grid_starts.py tests/test_fetch_opendota.py \
  tests/test_fetch_stratz.py tests/test_backtest_validation.py \
  tests/test_telonex_tape.py tests/test_market_data.py \
  tests/test_match_time.py                                 # 161 passed

make lint            # ruff check/format + basedpyright + hooks: all pass
uv run basedpyright  # 0 errors, 0 warnings, 0 notes
```

Real-data regression: `s03` merge output identical (4968 links, same
audit funnel), `s06` catalog identical (3237 rows; 196 archive /
3041 grid_derived), `s07` funnel identical — the refactor changed
structure, not outcomes.
