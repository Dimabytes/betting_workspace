# Step 3B — thermo-nuclear maintainability review

Scope: commit `85eccfc8` (`feat: [step 3B] - ordinary backtest launch and mixed-model report`) on `esports-trader` `main` only. Unrelated on-chain collector work is out of scope. Rubric: feature-json-step-review / thermo-nuclear (abstractions, 1k-line files, spaghetti-condition growth, code-judo). `poly-maker` is frozen and was not touched. This review does not implement fixes.

Bar: do not approve because it works. Approve only if there is no structural regression, no obvious judo that would delete a layer, no file pushed past 1k, no ad-hoc branching, no leaky contract, no duplicated canonical helper.

---

## Verdict

**Production wiring is the cleanup 3A promised. Do not call the step done.** `--policy` / `--live-since` / core_trace `SignalUpdate` are gone from the ordinary path. `resolve_feed_plans` is unconditional. `run.py` shrank 1940 → 1805. That is the right deletion.

Two test files this commit owns crossed the 1k hard line (971 → 1223, 949 → 1108). Dota `--since-match` loads catalog + validation parquet twice, while LoL already has the one-load "probe then pin" pattern. `feed_source="grid_v1"` overloads a schedule-binding field and forces a special-case label in `summarize_arm`. Those are design leftovers, not nits.

What is already good, and should survive any follow-up:

- Ordinary launch is one path. `FeedPlanResult` is always filled. No `auto_mode`, no empty-plan live branch, no `archive_parity`.
- `--since-match` is a cohort filter on catalog / horn time, not an archive mtime walk. Anchor need not be eligible. Empty cohort raises.
- `schedule_map_sha256` hashes `{match_id}|{mode}|{fingerprint}` only. Model identity lives in `model_sha256_by_dir`. Dataset rebuild does not move the schedule key.
- `live_archives.py` is inspector-only. `trader.replay_core_trace` / `make parity` are untouched.
- Own-book strip is schedule-bound archives only. The `core_trace.jsonl` missing guard stayed, with a ponytail ceiling.
- Terminal `SIGNALS` reads cohort composition from result rows and exclusion reasons from the manifest. No `summary.json` dump.

---

## 1. Structural code-quality regressions

No new layer, no new mode enum, no clock bus, no silent grid-v1 fallback. Net structure is a regression in the good direction: `PolicyMode`, `ArchiveHeader`, `build_archive_kernels`, `build_run_kernels`, `build_live_indexes`, `_lol_live_indexes`, `use_live_signals`, and the mtime `--live-since` resolver are deleted.

The structural miss is that the new selector was wired as a second load instead of a pin on the load that already exists. That is 2.1, not a new abstraction to invent.

---

## 2. Missed code-judo

### 2.1 Dota `--since-match` loads sources twice. LoL already shows the one-load move.

```1314:1322:src/backtest/run.py
    if since_match is not None:
        signal_rows = load_usable_signal_rows(validation_dataset)
        sources = load_market_sources(signal_rows)
        since_ids = select_dota_since_match_ids(sources, since_match)
        if limit is not None:
            since_ids = since_ids[:limit]
        dota = load_dota_selection(
            None, None, match_ids=since_ids, validation_dataset=validation_dataset
        )
```

`load_dota_selection` immediately re-reads the same validation parquet, split, and catalog. LoL does not do this: it `load_lol_selection`s once, then `replace(probe, selected_ids=..., coverage=None)`.

The plan's snippet wrote the double load. The snippet is a sketch of the pin, not a reason to pay the I/O twice. This is avoidable orchestration (rule 7), and it is the path with no test (plan G asked for `load_run_selection` since-match → `selected_ids`; that test does not exist).

**Remedy:** match LoL.

```python
if since_match is not None:
    dota = load_dota_selection(None, None, validation_dataset=validation_dataset)
    since_ids = select_dota_since_match_ids(dota.sources, since_match)
    if limit is not None:
        since_ids = since_ids[:limit]
    dota = replace(dota, selected_ids=since_ids, coverage=None)
```

Do not add `since_match=` onto `load_dota_selection`. That function is already a three-way dispatcher (`match_ids` / `match_id` / validation). Pinning after the load keeps the selector a pure function.

The duplicated `if limit is not None: since_ids = since_ids[:limit]` in both game arms can stay. One slice at the CLI boundary is clearer than burying `--limit` inside two selectors.

### 2.2 Replayability gates are copied, not shared.

`select_validation_matches` and `select_dota_since_match_ids` both require: in `usable_signal_match_ids`, not in `book_gap_excluded_match_ids`, `has_local_telonex_days` over `calculate_availability_window(anchor_at, ended_at)`. The since-selector must not call `select_validation_matches` (different universe: full catalog vs validation split). It also must not re-type the three gates.

**Remedy:** one predicate in `selection.py`, e.g. `_dota_replayable(sources, match_id, row) -> bool`. Validation keeps its reason buckets. Since-match keeps its time floor and empty-cohort raise. Do not invent a coverage object for the since path — `coverage=None` is correct.

### 2.3 Group labels are a display concern jammed into `summarize_arm` because `feed_source` was overloaded.

```73:78:src/backtest/results.py
    return SignalProvenance(
        signal_mode="grid_v1",
        feed_source="grid_v1",
        model_name=plan.model_dir.name,
    )
```

```582:588:src/backtest/postprocess.py
        label = (
            result.feed_source
            if result.signal_mode == "grid_v1"
            else f"{result.signal_mode}:{result.feed_source}"
        )
        signal_groups[label] = signal_groups.get(label, 0) + 1
        model_groups[result.model_name] = model_groups.get(result.model_name, 0) + 1
```

On a `SchedulePlan`, `feed_source` is the bound feed (`grid` / `oddin`). On `GridV1Plan` it is the mode name stuffed into the same field so the report can print `grid_v1` instead of `grid_v1:grid_v1`. The if exists because the field does two jobs.

**Remedy:** keep `feed_source` as the bound feed. Synthetic plans do not have one — `""` or omit, not a second spelling of `signal_mode`. Put the report label on `SignalProvenance` as a derived property (or format it once in `_signal_rows`). `summarize_arm` counts; it does not know about schedule vs grid_v1.

Do not add a third result column `signal_group`. Two fields plus a formatter beat three stored spellings of the same pair.

---

## 3. Spaghetti / branching complexity

Net branching went down. `main()` no longer forks on `auto_mode` / `policy_mode` / `live_since`. `reject_schedule_flags` growing an OR of three CLI knobs is the right place for that check, not a random if in `parse_args`.

What is still a leftover special case, now that the live path is gone:

```413:418:src/backtest/run.py
        plan = plans.get(context.match_id)
        entry_stale = (
            entry_stale_seconds(plan.binding)
            if isinstance(plan, SchedulePlan)
            else GRID_FEED_STALE_SECONDS
        )
```

After 3B every replayed match has a plan. `.get` then "not a SchedulePlan" silently uses grid stale — including a missing key. That was honest when `--policy archive` passed `plans={}`. It is a silent fallback now.

**Remedy:** `plan = plans[context.match_id]`. `isinstance(plan, SchedulePlan)` vs `GridV1Plan` is the typed dispatch, not a missing-plan default.

`load_run_selection`'s two `if since_match is not None` arms are the feature, not spaghetti, once 2.1 makes them isomorphic pins.

---

## 4. Boundary / type-contract problems

### 4.1 `feed_source: str` on results is untyped and overloaded.

`ScheduleBinding.feed_source` is `grid` / `oddin`. `MakerMatchResult.feed_source` accepts those plus `grid_v1`. `signal_mode` is already `"schedule" | "grid_v1"`. Finding 2.3 deletes the collision. If the field stays, it should be a `Literal`, not another free `str` next to `signal_mode: str`.

### 4.2 `_signal_rows` treats required TypedDict keys as optional JSON.

```160:178:src/backtest/report.py
def _signal_rows(arm: ArmPayload, manifest: Mapping[str, object]) -> list[str]:
    """Cohort composition: feed-source and model counts plus exclusion reasons."""
    rows = ["SIGNALS"]
    for label, count in sorted(arm.get("signal_groups", {}).items()):
        rows.append(_format_kv(label, str(count)))
    raw_exclusions = manifest.get("archive_exclusions")
    reason_counts: dict[str, int] = {}
    if isinstance(raw_exclusions, Mapping):
        for reason in cast(Mapping[str, str], raw_exclusions).values():
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
```

`ArmSummary` now requires `signal_groups` / `model_groups`. New manifests always emit `archive_exclusions` (`{}` when empty). `.get(..., {})` and `isinstance` + `cast` paper over that contract so the terminal report can limp on a half-written payload.

**Remedy:** `arm["signal_groups"]`, `arm["model_groups"]`, `manifest["archive_exclusions"]` as `Mapping[str, str]`. Resume already refuses a foreign manifest. Do not keep a second, softer schema in the printer.

### 4.3 Dota since-order depends on `MatchCatalog` iteration.

`MatchCatalog.__init__` sorts by `(start_time, match_id)`. `select_dota_since_match_ids` documents that and does not sort. `select_lol_since_match_ids` sorts explicitly, and its test inserts ids out of horn order. The Dota tests insert 1,2,3 in time order, so they would not catch a catalog that stopped sorting.

**Remedy:** `cohort.sort(key=lambda mid: (sources.catalog[mid].start_time, mid))` — one line, same contract as LoL, no dependency on a Mapping subclass's iteration. Add one out-of-order catalog fixture. Do not "fix" this by reaching into `MatchCatalog._by_match_id`.

### 4.4 Constant resume fossils.

`"backtest_policy": "current"` and `"signal_source": "auto"` are frozen so old `"archive"` / `"live_archive"` manifests fail resume. That is the spec. They are not a 3B design to grow. Do not add a third constant. A later step can drop the keys once resume no longer needs the tripwire.

---

## 5. File-size and decomposition

| File | Before | After | Note |
|---|---|---|---|
| `backtest/run.py` | 1940 | 1805 | Already over 1k; this diff shrinks it. 3A's +228 is being paid back. Do not split `run.py` in this step. |
| `backtest/live_archives.py` | 450 | 142 | Right shrink. Leave it in `backtest/` — `inspect/tail.py` is the remaining caller. Do not move it as a 3B side quest. |
| `backtest/feed_schedules.py` | 354 | 369 | Under 1k. `reject_schedule_flags` + digest change belong here. |
| `backtest/selection.py` | 154 | 181 | Under 1k. Since-selector is the right file. |
| `backtest/lol_inputs.py` | 280 | 305 | Under 1k. Same. |
| `backtest/telonex_local.py` | 329 | 292 | Fallback lookup deleted. Good. |
| `backtest/results.py` | 441 | 445 | Provenance simplified. |
| `backtest/postprocess.py` | 710 | 722 | Group counts. Fine. |
| `backtest/report.py` | 254 | 278 | `_signal_rows`. Fine. |
| `tests/test_backtest.py` | **971** | **1223** | **Crossed 1k.** |
| `tests/test_feed_schedules.py` | **949** | **1108** | **Crossed 1k.** |
| `tests/test_backtest_postprocess.py` | 1204 | 1283 | Already over; SIGNALS tests belong here. Do not split this file for 81 lines. |
| `tests/test_live_archives.py` | ~330 | ~170 | Dead-path tests deleted. Good. |

The 1k rule's hard blocker is "under 1k → over 1k." 3A's review used that sentence and noted `test_feed_schedules.py` was still under. This commit is the one that pushed both test modules over.

**Remedy:** extract `tests/test_since_match.py` (CLI parse, Dota/LoL selectors, `load_run_selection` pin, `reject_schedule_flags` on `--validation-dataset`). Move the mixed-model manifest + `signal_provenance_map` assertions with the since/manifest tests, not deeper into `test_feed_schedules.py`. Target: both files recede under 1k. Do not create `tests/backtest/` as a package for this.

`run.py` stays. Deleting the live path was the decomposition. Extracting `build_parser` to dodge a line count is not.

`build_current_kernels` can drop the `current_` prefix now that `build_archive_kernels` is gone. Rename is optional; do not leave a `build_run_kernels` dispatcher "for symmetry."

---

## 6. Modularity and abstraction

Earning their keep:

- `select_dota_since_match_ids` / `select_lol_since_match_ids` — pure, game-specific, right files
- `reject_schedule_flags(..., validation_dataset=)` — source-agnostic knobs stay off schedule cohorts
- `model_sha256_by_dir` — mixed-model resume pin the old `model_sha256` (primary dir only) missed
- `archive_exclusions` always present, sorted, string keys
- `signal_provenance(plan)` — required plan, no `ArchiveHeader` fallback
- Telonex `schedule_archives` as the only strip input

Not earning a new type:

- A `SinceMatchSelection` dataclass. A tuple of ids plus `coverage=None` is enough.
- A `SignalGroup` enum. Derived label (2.3), not a stored mode.
- Moving inspector helpers out of `live_archives.py` in this step.

`_apply_feed_exclusions` building an `excluded` intersection only to format the all-excluded error is fine. Keep counting `len(feed.exclusions)` for coverage: `resolve_feed_plans` only excludes from `selected_ids`.

---

## 7. Legibility and maintainability (only what is left)

- `--limit` help still says "first N eligible validation markets." The flag now also truncates a since-cohort. Update the help string.
- `build_order_latency` still returns `cancel_latency_ms=0.0` on the Nautilus config and a separate `cancel_latency_ns` RTT for the strategy. Pre-existing. 3B only deleted `archive_parity`. Leave it.
- Plan G asked for `reject_schedule_flags` to refuse `--validation-dataset` on a schedule cohort and allow it on grid-v1-only. There is no test of `reject_schedule_flags` at all. Ponytail: non-trivial logic leaves one check. Add the two cases when extracting `test_since_match.py`.
- Plan G asked for `load_run_selection` since-match → `selected_ids` + exclusions via `_apply_feed_exclusions`. Missing. That test would have made 2.1 obvious.
- Dota since tests never insert catalog rows out of chronological order (4.3).
- `test_run_manifest_omits_since_match_without_the_flag` docstring says "since_match and a non-empty exclusions map only appear when used." Empty `archive_exclusions` **does** appear (`{}`). The docstring is wrong; the assertion is right.

---

## What not to do

- Do not edit `poly-maker`.
- Do not revive `--policy` / `--live-since` / core_trace `SignalUpdate` on the ordinary path.
- Do not silent-fallback excluded archives to grid-v1.
- Do not split `run.py` or `test_backtest_postprocess.py` in this step.
- Do not move `live_archives.py` into `inspect/` as a drive-by.
- Do not put `since_match` inside `load_dota_selection` / `load_lol_selection`.
- Do not add `signal_group` as a parquet column.
- Do not drop `backtest_policy` / `signal_source` tripwires in 3B.
- Do not run prepare / train / full-suite / live backtests to "prove" this review.

---

## Required follow-up (blockers) vs optional (nits)

**Blockers — change these before calling step 3B done:**

1. `tests/test_backtest.py` and `tests/test_feed_schedules.py` recede under 1k. Extract `tests/test_since_match.py` (selectors, CLI, `load_run_selection` pin, `reject_schedule_flags` dataset reject, mixed-model manifest / provenance if that is what keeps `test_feed_schedules.py` over).
2. Dota `--since-match` loads sources once and pins, same as LoL (2.1).

**Should-fix in the same pass if the diff is already open:**

3. Share `_dota_replayable` between validation and since-match (2.2).
4. Stop stuffing `feed_source="grid_v1"`. Derived report label; `summarize_arm` just counts (2.3).
5. `plans[context.match_id]` in `build_current_kernels` (section 3).
6. `_signal_rows` reads required keys, no `.get` / `isinstance` / `cast` (4.2).
7. Explicit `(start_time, match_id)` sort on the Dota since-selector + one out-of-order test (4.3).
8. The two missing plan-G checks: `reject_schedule_flags(validation_dataset=...)` schedule vs grid-v1-only; `load_run_selection` since-match selected_ids.

**Nits — do not block on these:**

9. `--limit` help text, `build_current_kernels` rename, wrong manifest-test docstring, `backtest_policy` / `signal_source` fossils (leave until a resume cleanup).

The unconditional feed-plan path, catalog-time cohort, schedule digest without `model_dir`, inspector-only `live_archives.py`, and mixed-model `SIGNALS` section are the parts to keep while doing 1–2. Do not reintroduce a policy/live-archive fork to make the since-selector prettier.
