# Step 2 — thermo-nuclear maintainability review

Scope: commits `de273edd` (`feat: [step 2] - Dota linking and shared dataset admission`) and `0e40b87c` (`fix: [step 2] - parse string steam_match_id from archive index`) on `esports-trader` `main`, not on `origin/main`. Rubric: feature-json-step-review / thermo-nuclear (abstractions, 1k-line files, spaghetti-condition growth, code-judo). `poly-maker` is frozen and was not touched. This review does not implement fixes.

Bar: do not approve because it works. Approve only if there is no structural regression, no obvious judo that would delete a layer, no file pushed past 1k, no ad-hoc branching, no leaky contract, no duplicated canonical helper.

---

## Verdict

**Do not approve as-is.** The catalog time contract and the live-paper deletion are the right architecture. The merge module still encodes candidate identity as branch-local special cases, and the index→int steam-id boundary is a comment plus an inline parser rather than a typed edge. Those are design problems, not nits.

What is already good, and should survive any rewrite:

- GRID windows now mean `startedAt` only. `live_paper_map_load_unix` / `horn - 90` spawn fabrication is gone from `s04`. That is the spec'd judo and it landed.
- Horn, pauses, winner, and archive binding live on the catalog row. Backtest / market-data / capital settlement stop re-deriving them from OpenDota files. `build_market_context(sources, match_id)` is the shape this codebase should have had.
- `pauses_from_schedule` treats an unobserved boundary as `None`, not `[]`. That invariant is tested and is the correct unknown-vs-empty distinction.
- New files stay under 1k (`s03` 555, `s06` 122→265, `s04` 551→513, `schedule.py` 467→523). `backtest/run.py` is already 1806 and this diff only shrinks `load_replay_lookups`. No 1k-line explosion to waive.

---

## 1. Structural code-quality regressions

### 1.1 Horn consistency is a condition-attach special case. Catalog treats every attached archive horn as gospel.

`s06.derive_timing` prefers the archive horn whenever `archive_id` is set — including `attach_by_match` and `new_link`:

```118:125:src/collect/s06_publish_catalog.py
        if archive_id is not None:
            horn_src: HornSource = "archive"
            horn = opt_str(row.archive_horn_at_utc)
        else:
            horn_src = "grid_derived"
            spawn_at = opt_str(row.spawn_at)
            if spawn_at is not None and pauses is not None:
                horn = get_horn_datetime(parse_utc(spawn_at), pauses).isoformat()
```

The merge only proves index horn == schedule identity horn on the exact-condition path (`check_condition_attach` → `_horn_consistent`). Match-attach never checks it. New-link only checks that the index horn parses.

That is a structural leak between layers. The catalog's time contract assumes "attached archive ⇒ trustworthy horn." The merge does not establish that invariant for two of three resolutions. Pauses still come from the published schedule. A match-attached archive whose index horn disagrees with the schedule will publish `horn_source="archive"` from the index and `pauses_source="archive"` from the schedule — mixed clocks on one row.

This is not "the plan only listed the check under rule 1, so it is fine." Rule 1 is where the check was written; the catalog change is what made the check a candidate-level invariant. Checking it in one branch and ignoring it in the siblings is spaghetti that will poison the one contract this step exists to create.

**Remedy:** validate horn at candidate load, once. `load_candidates` already opens the schedule identity for every admitted archive. If index horn and schedule identity horn disagree (or either is missing/unparseable), the candidate is `excluded_horn_inconsistent` and never enters `merge_archive_links`. Then `_horn_consistent` disappears from `check_condition_attach`, match-attach and new-link inherit the same guarantee, and `derive_timing` can keep preferring archive horn without a hidden asterisk.

Do not add a fourth horn check inside s06. That would scatter the same special case again.

### 1.2 The steam-id dtype was a boundary the merge did not own — and the fix still does not own it.

`ScheduleIdentity.steam_match_id` is `str | None`. `match.json` stores it as a nullable string. The index parquet is an object/string column. `MatchLinkRow.match_id` / `archive_steam_match_id` are ints. That conversion is the merge's job.

`de273edd` called `opt_int` on it. `opt_int` rejects strings. Every steam-carrying archive became `steam_match_id=None` → 0 `new_link` instead of ~93. `0e40b87c` patched the symptom inline:

```118:120:src/collect/s03_merge_archive_links.py
        # the index stores steam_match_id as a string column
        steam_raw = opt_str(row.steam_match_id)
        steam_match_id = int(steam_raw) if steam_raw is not None and steam_raw.isdigit() else None
```

The regression test is necessary and should stay. The parser is still a comment in `load_candidates`. The same logic already exists as `backtest/live_archives.py::_steam_match_id` (int or digit-string → int | None). Two parsers, two layers, one identifier.

**Remedy:** one function next to the type that is actually a string — `parsing.py` or `archive_index` — and call it from `load_candidates` and `live_archives`. Do not teach `opt_int` to accept digit strings; that helper is for numeric cells, and steam ids are identifiers that happen to look like numbers. Do not leave a `# the index stores steam_match_id as a string column` apology in the merge.

This is the canonical-helper / wrong-layer blocker in the rubric. The follow-up commit made the pipeline correct; it did not make the boundary explicit.

---

## 2. Missed code-judo

### 2.1 `check_condition_attach` returns `Rejection | str | None`. `check_match_attach` already shows the simpler shape.

Condition-attach overloaded one function with three meanings: refuse, attach with a flag, attach clean. The merge loop then does `isinstance(verdict, Rejection)` and treats a leftover `str` as `identity_conflict`. Match-attach returns `Rejection | None` and the caller sets `"archive_market_differs"`. That is the pattern that should exist once.

The judo is deletion, not a new result type (the plan already forbade extra abstractions, and a third dataclass here would not earn its keep):

1. A rejection-only checker for map / already-attached / winner-disagree (and horn, until finding 1.1 lifts it).
2. A separate one-liner for the steam-mismatch flag, same as the match-attach caller already does for `archive_market_differs`.

The union goes away. The two attach paths read the same. `Rejection` stays as the one extra type that is pulling its weight.

### 2.2 Drop accounting is two models for one funnel.

`keep_complete` logs **total** failures per mask (a row that fails admission also increments `ended_at` / `gamma`). `s07.report_catalog_drops` walks masks in order and counts **first** failure. `test_dropped_rows_are_logged_with_their_match_ids` cements the total-count logs (`admission failed=2` and `ended_at failed=2` for the same two rows).

The plan asked s06 to count admission failed as distinct from stratz/prior/ended/gamma — first-failure language. Operators comparing `make catalog` to `make link-report` will not get the same numbers.

**Remedy:** one helper next to `check_masks` that returns first-failure counts. `keep_complete` and s07 both print that. Update the test. Delete the Python `for position / for name` loop in s07 — it is re-deriving a function that belongs beside the masks.

Do not write a drop-reason parquet. The shared function is enough.

### 2.3 Candidate identity should be finished before the merge loop.

`load_candidates` already reduces the index to `ArchiveCandidate`. It still leaves feed_source as `cast(ArchiveFeedSource, str(...))`, steam-id parsing as an inline, and horn as a sidecar `schedule_horn_at_utc` that only one later branch consults.

The judo: `ArchiveCandidate` is either mergeable or it is already an audit rejection. Horn-inconsistent / untyped feed / unparseable steam become load-time outcomes. `merge_archive_links` then only dispatches attach-by-condition / attach-by-match / new-slot. That is the spec's numbered algorithm without identity branches stuffed into step 1.

`schedule_identity_horn` can die in the same move if load uses the identity block (it already does) and records the verdict there. Keep `read_schedule` for ticks; do not parse full tick arrays just to compare two ISO strings.

---

## 3. Spaghetti / branching complexity

The merge algorithm itself is in the right file. This is not "random ifs bolted onto s06." The remaining spaghetti is **overloaded resolutions** and **asymmetric sibling paths**.

`excluded_steam_condition_conflict` is three situations:

- condition and steam point at different existing rows
- the row already carries another archive
- `(event_id, game_number)` is already taken by a new-link

The plan listed one name. The audit `detail` string is what makes the funnel honest (`grid-2996008-m2` vs `grid-3004579-m2` in the real run). That is acceptable only if the detail stays required and the report prints it. Do not add new resolution literals unless the funnel actually needs to split the count. Do not keep stuffing more collisions into this bucket without a detail.

Asymmetry to flatten (with 1.1 / 2.1), not to grow:

- horn check: condition-attach only
- winner-vs-OpenDota: condition-attach steam-mismatch only (catalog later checks archive-vs-STRATZ — that second check is the right layer; keep it)
- `archive_condition_id` set only on match-attach, in the caller, after `_attach`

`_attach` mutating a `TypedDict` in place, with `by_condition` / `by_match` holding the same dict identity, is how attach works without copying. Leave it. Immutable copies would be a refactor that moves complexity around.

`_new_link_row` `assert`s `yes_token_index` / `yes_is_radiant` after `resolve_new_slot` never checked them. A missing token index crashes the stage instead of becoming an audit row. Either reject in `resolve_new_slot` or stop pretending those fields are optional on `ArchiveCandidate` for the create path. Do not leave asserts as the admission gate.

---

## 4. Boundary / type-contract problems

- **`MatchLinkRow` vs index string ids.** Covered in 1.2. The TypedDict is fine; the parser is not.
- **`check_condition_attach` union.** Covered in 2.1.
- **`cast(ArchiveFeedSource, str(row.feed_source))`.** If the index ever emits a third source, the merge silently lies. Fail at load (unknown feed → skip/refuse), or type the index column. A cast is not a contract.
- **Admission predicate copied three ways, not quite equal:**
  - `window_ids` / s07: `map_condition_id in windows OR archive_id.notna()`
  - s06 `check_masks["admission"]`: `spawn_at.notna() | archive_id.notna()`
  After the grid merge those are equivalent *if* every window row has `spawn_at`. That is true of `GridGameWindowRow` today. It is still two formulations of the same gate. Put the mask on the assembled frame (s06 already has it) and have the fetch worklist use the same boolean over links+windows. Do not let s05/s05b and the catalog disagree later because someone added a window without `spawn_at`.
- **`MatchCatalogRow.pauses_json: str | None` vs `catalog_entry_from_row` raising on null.** Published rows cannot have null pauses (`ended_at` requires them). The optional is the assemble-frame leaking into the published schema. Acceptable if every reader goes through `CatalogEntry`. Do not start using `pauses_json` as optional in consumers.
- **`read_schedule` is `Foo(**payload["bar"])`.** Fine as the inverse of `asdict` for an artifact this repo writes. Do not reuse that pattern for foreign JSON.

`opt_int` / `opt_float` accepting NumPy scalars is the correct fix for the dead map audit. That part of `parsing.py` is in the right layer.

---

## 5. File-size and decomposition

No file in this diff crosses 1000 lines because of the work.

| File | Before | After | Note |
|---|---|---|---|
| `s03_merge_archive_links.py` | — | 555 | New; one stage; do not split for sport |
| `s06_publish_catalog.py` | 122 | 265 | Growth is `assemble` / `check_masks` / `derive_timing`; justified |
| `s04_fetch_grid_starts.py` | 551 | 513 | Net deletion; GRID GraphQL types were already here |
| `archive_index/schedule.py` | 467 | 523 | `read_schedule` + `pauses_from_schedule` |
| `s07_link_readiness.py` | — | 189 | Report CLI |
| `backtest/run.py` | 1806 | 1806 | Already over 1k; this diff only removes OpenDota plumbing |

Do not extract s03 into a package. Do not split s04's GRID types in this step. If `schedule.py` keeps growing in 3A, that is when ticks/pauses/IO split — not now.

---

## 6. Modularity and abstraction

Earning their keep:

- `ArchiveCandidate`, `EventInventory`, `Rejection`
- `MatchLinkRow` / `MatchLinkAuditRow`
- `read_schedule` + `pauses_from_schedule`
- `CatalogEntry.anchor_at` / nullable `spawn_at`
- s07 importing `assemble_catalog_frame` / `check_masks` / `load_archive_pauses` from s06 (same drop rules, no second catalog builder)

Not earning a new type: an `AttachDecision` ADT, a drop-reason parquet, a pause policy object, a generic "resolution engine."

s07 as a separate stage is what the plan asked for. Coupling it to s06 internals is the correct reuse, not a layer leak. Collect scripts importing each other is already the house style (`sys.path.insert` and all).

`try_load_stratz_winners` belongs in `stratz.py`. Winner-as-settlement, not a feature: preserved.

Consumers (`selection.build_market_context`, `run.load_replay_lookups`, `report_capital`, `telonex_tape`, `build_market_data`) now read the catalog. That is the modularity win of the step. Do not reintroduce per-match OpenDota opens on those paths.

---

## 7. Legibility and maintainability (only what is left)

- `s04` prints `skipped_archive_links` for every row missing OpenDota timing, including a corrupt OpenDota row. Rename to `skipped_links_without_grid_clock` or count archive-only separately. Nit.
- `s05a.load_anchors` uses `isinstance(..., str)` instead of `opt_str`. Same helper the rest of collect uses. Nit.
- `catalog_fixtures.catalog_row` grew a spawn / `started_at` / `start_time` / `archive_id` default maze. Tests only; do not "fix" it with a builder class. If it gets another branch, split GRID vs archive fixtures instead.
- `pauses_from_schedule` while-loop is the spec. `groupby` would not delete a concept. Leave it.
- Sequential schedule-identity reads in `load_candidates` are fine at ~200 archives. Do not parallelize.

---

## What not to do

- Do not edit `poly-maker`.
- Do not invent new resolution literals unless the funnel is actually lying.
- Do not put schedule replay, `--policy`, or train-lag changes in a follow-up disguised as this cleanup.
- Do not "simplify" by fabricating spawn from horn. That producer is deleted on purpose.
- Do not expand `opt_int` into a universal cell decoder to hide the steam-id string.

---

## Required follow-up (blockers) vs optional (nits)

**Blockers — change these before calling step 2 done:**

1. Horn consistency is a property of an admitted archive, not of `attach_by_condition`. Enforce it at candidate load so every attach/create path, and therefore every catalog `horn_source="archive"` row, shares it.
2. Own the steam-id string→int conversion in one named helper at the index/identity boundary; call it from s03 (and fold `live_archives._steam_match_id` into it). Keep the regression test.
3. Collapse `check_condition_attach`'s `Rejection | str | None` into the rejection-only shape `check_match_attach` already uses.

**Should-fix in the same pass if the diff is already open:**

4. First-failure drop counts shared by s06 logs and s07.
5. One admission boolean for the fetch worklist and the catalog mask.
6. Create-path token index: reject or require, do not `assert`.

**Nits — do not block on these:**

7. `skipped_archive_links` naming, `opt_str` in s05a anchors, fixture default maze.

The live-paper deletion, the catalog contract, pause unknown-vs-empty, and the consumer simplifications are the parts to keep while doing 1–3. Do not restructure those to make the merge prettier.
