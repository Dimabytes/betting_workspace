# Step 3A — thermo-nuclear maintainability review

Scope: commit `199705d4` (`feat: [step 3A] - features by game-second and archive schedule`) on `esports-trader` `main` only. Unrelated US-005 on-chain collector commits are out of scope. Rubric: feature-json-step-review / thermo-nuclear (abstractions, 1k-line files, spaghetti-condition growth, code-judo). `poly-maker` is frozen and was not touched. This review does not implement fixes.

Bar: do not approve because it works. Approve only if there is no structural regression, no obvious judo that would delete a layer, no file pushed past 1k, no ad-hoc branching, no leaky contract, no duplicated canonical helper.

---

## Verdict

**Do not approve as-is.** The feed-plan module, the no-silent-fallback rule, and `game_features.parquet` keyed by actual game second are the right architecture. The wiring then stuffed observed clock into `MatchSignals`, duplicated the existing as-of mid helper, and dumped ~230 lines of orchestration into a file that was already 1806 lines. Those are design problems, not nits.

What is already good, and should survive any rewrite:

- `feed_schedules.py` owns match → schedule | grid_v1 | exclusion. Nothing in between silently becomes synthetic cadence.
- Dota features are a separate artifact keyed by `game_second`, not a re-key of lagged validation rows. LoL reuses `validation.parquet.second`. That is the spec'd data-model split and it landed.
- Decisions recompute from research / research-noxp / lol research by `plan.model_dir`, batched per directory. Schedule fingerprints do not move when the model does.
- Market anchors are causal (as-of arrival). Missing feature rows fail closed. Stale ticks stay on the feed tape and drop out of decisions.
- `telonex_local.create_telonex_source_tree` takes the bound archive dir so Oddin (non-`grid-*`) still strips own-book. One precedence line, right layer.
- New production module stays under 1k (`feed_schedules.py` 290). `signals.py` 329→509. Tests cover the exclusion matrix, causal anchors, and observed `_clock_at`.

---

## 1. Structural code-quality regressions

### 1.1 `MatchSignals` is now a clock bus. Every old producer pays a three-tuple tax for a tape it does not have.

The schedule path needs observed `game_second` / `paused` / `terminal` aligned with feed arrivals. Those belong on the strategy clock, next to `buy_cutoff_ns` / `game_end_ns`. This commit put them on `MatchSignals` as three required tuples, empty on grid-v1 and live-archive:

```82:96:src/backtest/signals.py
@dataclass(frozen=True)
class MatchSignals:
    """Feed ticks plus model decisions: deltas/prices exist only on decisions.

    The feed_* tapes carry the observed clock state for archive-schedule
    replays; they stay empty on the synthetic and live-archive paths.
    """

    feed_timestamps_ns: tuple[int, ...]
    timestamps_ns: tuple[int, ...]
    predicted_deltas: tuple[float, ...]
    dataset_market_ps: tuple[float, ...]
    feed_game_seconds: tuple[int, ...]
    feed_paused: tuple[bool, ...]
    feed_terminal: tuple[bool, ...]
```

Empty tuple is then a mode flag. `_clock_at` and `build_strategy_configs` both branch on `if match_signals.feed_game_seconds`. `DotaMakerStrategy.__init__` copies the three fields back onto a `MatchSignals` it builds from config. `DotaMakerConfig.__post_init__` still only checks signal/delta/anchor lengths — the new tapes are unaligned by contract.

The tax is scattered: `live_archives.py`, every `MatchSignals(...)` in tests, and `docs/experiments/ensemble/stage2.py` (still constructed with four fields; the constructor is now invalid). Grid-v1 never observes a clock. It should not know these fields exist.

This is feature logic leaking into the shared signal type. Step 2's review called out `run.py` staying at 1806 because catalog timing moved *out* of backtest. 3A glues clock *into* the signal object so `build_strategy_configs` does not need `plans`. That convenience is the regression.

**Remedy:** one optional observed-clock value on `DotaMakerConfig` only, e.g. `ObservedClockTape | None` (seconds/paused/terminal, same length as `feed_timestamps_ns`, enforced in `__post_init__`). `build_strategy_configs` takes `plans` (it already has `kernels`) and reads `plan.binding.schedule.ticks` for cutoff / game-end / the tape. `MatchSignals` stays decisions + feed timestamps. Grid-v1 and live-archive constructors do not change. `_clock_at` branches on `None`, not on an empty tuple that must stay in lockstep with two siblings.

Do not add `feed_phase`. `GameClock` has no such field.

### 1.2 `run.py` grew 1806 → 2034. The new orchestration has a canonical home and was left in the CLI godfile.

Step 2 explicitly did not enlarge this file. 3A adds `ArchiveJoinInput`, `_load_game_feature_rows`, `_resolve_run_signals` schedule dispatch, `_schedule_archive_dirs`, `_signal_provenance` / `_signal_provenance_map`, `_reject_schedule_flags`, `_read_run_archive_headers`, `_resolve_feed_plans`, plus `plans` / `schedule_archives` / `provenance` threaded through `warm_replay_cache`, `replay_matches`, `build_current_kernels`, `build_run_kernels`. Extracting `build_parser` to dodge C901 is not decomposition. It is rearranging the same file so ruff will accept 2k lines.

`_load_game_feature_rows` is dataset I/O for schedule signals. `_resolve_feed_plans` is the CLI adapter over `resolve_*_feed_plans`. `_schedule_archive_dirs` is a one-liner on `MatchFeedPlan`. None of that is run-loop logic.

**Remedy:** move feature-row loading next to `build_schedule_match_signals` (`signals.py`). Move `_resolve_feed_plans`, `_reject_schedule_flags`, and `_schedule_archive_dirs` into `feed_schedules.py`. `main()` keeps: auto vs live-archive, log exclusions, `replace(selection, selected_ids=...)`, pass `feed.plans` into kernels / signals / telonex. Target: this commit's net line count in `run.py` is a small dispatch, not +228.

Do not create `backtest/feed_run.py`. Two modules already own these concepts.

---

## 2. Missed code-judo

### 2.1 `MatchFeedPlan.mode` + `binding: ScheduleBinding | None` is two fields for one fact.

```48:55:src/backtest/feed_schedules.py
@dataclass(frozen=True)
class MatchFeedPlan:
    """How one match's feed events arrive, and which model predicts on them."""

    match_id: int
    mode: FeedMode
    binding: ScheduleBinding | None
    model_dir: Path
```

`mode == "schedule"` iff `binding is not None`. The type does not say that, so three sites re-assert it:

- `build_schedule_match_signals` raises `schedule plan without binding`
- `_resolve_run_signals` raises the same while copying `binding.schedule` into a redundant `schedules` map
- `_schedule_archive_dirs` filters `binding is not None`

`_bind_schedule` and `_resolve_dota_match` return `tuple[ScheduleBinding | None, str | None]` — the same dual-optional, now as an anonymous tuple, which this repo forbids for its own multi-field values. `(None, None)` means grid-v1, `(None, reason)` means exclude, `(binding, None)` means schedule.

**Remedy:** a union, not a flag.

```python
@dataclass(frozen=True)
class SchedulePlan:
    match_id: int
    binding: ScheduleBinding
    model_dir: Path

@dataclass(frozen=True)
class GridV1Plan:
    match_id: int
    model_dir: Path

MatchFeedPlan = SchedulePlan | GridV1Plan
```

Resolve helpers return `ScheduleBinding | str | None` is still a union soup. Return `SchedulePlan | GridV1Plan` or an exclusion reason — one outcome, named. Then `build_schedule_match_signals` takes `Mapping[int, SchedulePlan]` and drops the `schedules=` argument (`plan.binding.schedule` is already there). The "schedule without binding" raises disappear because they cannot be typed.

Do not add a third dataclass for exclusions. `FeedPlanResult.exclusions: dict[int, str]` is enough.

### 2.2 `MarketAnchorTape` / `anchor_at` duplicate `MidSeries` / `lookup_reference_mid`.

```99:117:src/backtest/signals.py
@dataclass(frozen=True)
class MarketAnchorTape:
    """Causal P(radiant) anchors: the ok-market mid tape a tick may read."""

    timestamps_ns: tuple[int, ...]
    p_radiant: tuple[float, ...]


def market_anchor_tape(series: MidSeries) -> MarketAnchorTape:
    """Reframe the shared ok-mid series as the schedule anchor tape."""
    return MarketAnchorTape(timestamps_ns=series.timestamps_ns, p_radiant=series.market_ps)


def anchor_at(tape: MarketAnchorTape, now_ns: int) -> float | None:
    """Latest anchor mid at or before now_ns; None when the tape starts later."""
    index = bisect_right(tape.timestamps_ns, now_ns) - 1
    if index < 0:
        return None
    return tape.p_radiant[index]
```

`marks.lookup_reference_mid` is the same bisect, on the same two tuples, already documented as "latest ok mid at or before target_ns". `run.py` then does `{mid: market_anchor_tape(mids[mid]) for mid in schedule_ids}` — a rename wrapper over lookups the replay already holds.

The plan asked for a tape type. The codebase already had it. `test_anchor_at_is_strictly_causal` retests the canonical helper.

**Remedy:** delete `MarketAnchorTape`, `market_anchor_tape`, and `anchor_at`. `build_schedule_match_signals(..., mids: Mapping[int, MidSeries])` and `lookup_reference_mid`. Keep the schedule-signal tests that already prove causal anchors on real ticks.

### 2.3 `GameFeatureRow` hand-copies `StateFeatures` minus `radiant_win`. So does `build_game_feature_rows`.

The plan wrote `class GameFeatureRow(StateFeatures)`. That would have put `radiant_win` on the artifact, which the same plan forbids. The impl duplicated nine snapshot fields instead of splitting the typed block once.

`state_features(state)` already exists in `prepare_dataset.py`. `build_game_feature_rows` lists every field again off `ExactSecondState`. A new STRATZ column will land on validation rows and silently miss `game_features.parquet` until `predict_model_deltas` blows up on a missing model.json column.

**Remedy:** a `SnapshotFeatures` TypedDict (today's `StateFeatures` without `radiant_win`). `StateFeatures` adds `radiant_win`. `GameFeatureRow` adds `match_id`, `game_second`, `market_radiant_prior`. `build_game_feature_rows` is `GameFeatureRow(match_id=..., game_second=state.second, **{k: v for k, v in state_features(state).items() if k != "radiant_win"}, ...)` only until the split exists — after the split, `state_features` returns `SnapshotFeatures` and the comprehension dies.

Do not inherit `StateFeatures` and drop the column at write time. The parquet schema is the contract.

### 2.4 Dota and LoL resolution share a candidate funnel and do not share a function.

Both: filter `_archive_rows`, sort by `(archive_root, archive_id)`, admitted → `_bind_schedule`, else first candidate → `archive:<admission>`, else grid-v1. Dota adds the catalog-link path and the unlinked-admitted exclusion. LoL adds `duplicate_of:` filtering and condition_id join. The duplicated loop is the "no candidate / refused / admitted" ladder, not the join keys.

**Remedy:** `_finish_candidates(candidates) -> _ArchiveRow | str | None` (admitted row, exclusion reason, or unbound). Both resolvers call `_bind_schedule` on the admitted row. Do not unify the join keys into a generic "entity matcher." Dota catalog-link vs LoL audit condition_id are different inputs; the funnel after candidates exist is the same.

---

## 3. Spaghetti / branching complexity

The resolver itself is in the right file. This is not "random ifs bolted onto `selection.py`." The remaining spaghetti is **overloaded optionals** and **two severity channels for one bind failure**.

Identity mismatch **raises** (`steam_match_id` / `condition_id`). Missing file, unreadable JSON, unknown archive root, fingerprint mismatch all **exclude** as `schedule_missing` / `schedule_fingerprint_mismatch`. A 400-match validation run dies because one schedule JSON has the wrong steam id; a missing file on the sibling match only drops that id. `test_schedule_identity_mismatch_raises` cements the harsher channel.

Corrupt identity is not more special than a fingerprint mismatch. Both mean "this archive cannot be this match."

**Remedy:** one exclusion reason, e.g. `schedule_identity_mismatch`, from the same `_bind_schedule` (or a tiny `_assert_identity` that returns the reason). Keep the raise only for programmer errors (calling schedule-build without a `SchedulePlan`). Update the test.

Asymmetry to flatten, not to grow:

- `_resolve_feed_plans` `assert join.audit` / `assert join.catalog` — see 4.1
- `_clock_at` / `build_strategy_configs` empty-tape branches — see 1.1
- `build_current_kernels`: `plans.get` then `binding is not None` then `entry_stale_seconds`, else `GRID_FEED_STALE_SECONDS`. After 2.1 this is `isinstance(plan, SchedulePlan)` vs grid-v1. Live-archive / `--policy archive` still skip plans entirely; that three-way dispatch is 3B's deletion. Do not invent a mode enum to paper over 3B.

`recovery_stale_mask` + `grid_exit_age_seconds(entry_stale_seconds(binding))` on the schedule path is the live rule, not a one-off. Leave it.

---

## 4. Boundary / type-contract problems

### 4.1 `ArchiveJoinInput` is a tagged union pretending to be two optionals.

```230:250:src/backtest/run.py
@dataclass(frozen=True)
class ArchiveJoinInput:
    """Catalog (dota) or audit (lol) rows the feed-plan resolver joins through."""

    catalog: MatchCatalog | None
    audit: pd.DataFrame | None


@dataclass(frozen=True)
class RunSelection:
    ...
    archive_join: ArchiveJoinInput | None
```

`load_run_selection` already has `dota.sources.catalog` and `lol.audit`. It wraps one of them, nulls the other, and nulls the whole object for `--live-since`. `_resolve_feed_plans` then asserts the field the game needs. That is the `check_condition_attach` → `Rejection | str | None` smell from step 2, in a new coat.

**Remedy:** `DotaArchiveJoin(catalog)` | `LolArchiveJoin(audit)` | `None`. Or skip the wrapper: `resolve_dota_feed_plans` / `resolve_lol_feed_plans` already take the concrete input; `load_run_selection` can leave catalog/audit on a 2-variant selection long enough for `main` to call the matching resolver. Do not store `catalog=None, audit=lol.audit`.

`--model-dir` must still win at resolve time (after CLI parse). That is why resolution stays in `main`, not inside `load_run_selection`. The join handle is what is over-optional, not the timing.

### 4.2 Anonymous bind tuples.

`_bind_schedule` → `tuple[ScheduleBinding | None, str | None]`. `_resolve_dota_match` the same. AGENTS.md: no anonymous tuples for our own multi-field values. Finding 2.1 deletes them. Do not "fix" this by naming `BindResult(binding, reason)` — that preserves the dual-optional.

### 4.3 `build_run_manifest(schedule_map_sha256: str | None)`.

Optional payload keys, then a side-effect block that also stamps `feed_schedule_rules_version` / `admission_rules_version`. Fine for resume-assert (absent key on old live-archive manifests). Do not start stamping those versions when `auto_mode` is false — they would lie. Leave the `if schedule_map_sha256 is not None` once 1.2 moves the call site; it is not a new abstraction.

`signal_source="auto"` replacing `"grid_v1"` is the correct resume break.

### 4.4 `create_telonex_source_tree(..., schedule_archives)` required `Mapping`.

Right. `or _archive_for_context` is the Oddin strip fix. Callers passing `{}` on the old path are honest. Do not make the argument optional.

---

## 5. File-size and decomposition

| File | Before | After | Note |
|---|---|---|---|
| `backtest/run.py` | 1806 | 2034 | Already over 1k; this diff is the unjustified growth. Finding 1.2. |
| `backtest/strategy.py` | 1209 | 1225 | Already over 1k; `_clock_at` belongs here. Do not split the strategy file for 16 lines. |
| `backtest/signals.py` | 329 | 509 | Under 1k. After deleting `MarketAnchorTape` and clock fields, this shrinks. |
| `backtest/feed_schedules.py` | — | 290 | New; right module; do not split for sport. |
| `prepare_dataset.py` | ~340 | 372 | Emission is small and in the right loop. |
| `tests/test_feed_schedules.py` | — | 874 | Under 1k. Mixes resolver + `build_schedule_match_signals` (plan put the latter in `test_backtest_signals.py`). Split only if 1.1/2.2 move the signal tests with the code. |
| `tests/test_backtest_maker.py` | 2868 | 2923 | Already a monster; two `_clock_at` tests are the right file. Do not extract a test package in this step. |

The 1k rule's hard blocker is "under 1k → over 1k." No file in this commit does that. Growing the 1806-line CLI by +228 when the new code has a named home is still the decomposition miss the rubric treats as a blocker.

`strategy.py` stays. Clock logic is 16 lines in `_clock_at`. Packing the tape (1.1) does not require a new strategy module.

---

## 6. Modularity and abstraction

Earning their keep:

- `ScheduleBinding`, `FeedPlanResult`, `DatasetReadinessError`
- `GameFeatureRow` as a distinct artifact (once 2.3 shares the snapshot block)
- `SignalProvenance` on result rows (`signal_mode` / `model_name`) — named pair, two constructors, AGENTS.md-correct
- `entry_stale_seconds` reading live constants (`ODDIN_FEED_STALE_SECONDS` / `GRID_FEED_STALE_SECONDS`)
- `schedule_map_sha256` for resume
- `schedule_archives` on the Telonex source tree

Not earning a new type:

- `MarketAnchorTape` (2.2)
- `ArchiveJoinInput` as dual-optional (4.1)
- `ScheduleDecision` — this one *does* earn it (match, ts, row position, anchor, model_dir). Keep it. Do not go back to an anonymous tuple for the batch-predict pass.
- A generic "feed mode engine" / experimental-mode framework. The plan forbade it. Do not add one while cleaning 2.1.

`_resolve_run_signals` still owns live-archive vs auto. That three-way stay until 3B deletes `--policy archive` / `--live-since`. Do not extract a `SignalSource` strategy object for two releases of coexistence.

`build_schedule_match_signals` grouping predictions by `model_dir` is the right batch boundary. Leave it. Per-match `load_predictor` would be the worse design.

Feature I/O in `run.py` is the layer leak (1.2). LoL rename `second → game_second` belongs next to that loader, not inlined in `main`.

---

## 7. Legibility and maintainability (only what is left)

- `_reject_schedule_flags` calls `build_parser().error(...)` and rebuilds the entire CLI parser to print one message. `parse_args` already has a `parser` in hand; raise `ValueError` like the `--match-id` exclusion, or pass the parser down. Nit.
- `_load_game_feature_rows` for LoL returns the full `signal_rows` renamed, unfiltered by `schedule_ids`. Dota filters. Harmless (later groupby keys on requested ids) and inconsistent. Filter both, in the moved loader.
- `int(match_id)` at every turn in `resolve_*_feed_plans` while `selected_ids` is already `Sequence[int]`. Nit.
- Unknown `archive_root` and unreadable JSON both become `schedule_missing`. Acceptable: operators rerun `make archive-index`. Do not add `schedule_unreadable` unless the funnel is lying.
- Impl report claimed `_bind_schedule` checks schema/rules versions and game/source identity. The code checks file + fingerprint; identity is a caller raise; versions ride inside the fingerprint. That is enough. Do not add redundant version compares in 3A.
- `docs/experiments/ensemble/stage2.py` `MatchSignals(...)` was not updated. Evidence for 1.1, not a reason to keep the three empty tuples.

Cutoff / terminal handling in `build_strategy_configs` (first tick with `game_second >= cutoff`, else last tick; last terminal else catalog `game_ended_at`) is the spec. Leave the behavior; after 1.1 it reads off the schedule ticks instead of the signal object.

`archive_parity` still keys off `live_since or policy == "archive"`. Plan J allowed that. Do not flip it in a maintainability pass; 3B owns mixed-path deletion.

---

## What not to do

- Do not edit `poly-maker`.
- Do not start 3B (`--policy` / `live_archives` / mixed-model report) as a side effect of this cleanup.
- Do not introduce an experimental-mode framework, a `FeedModeEngine`, or per-source `--model-dir`.
- Do not re-key validation parquet or change train lag.
- Do not silent-fallback incomplete archives to grid-v1.
- Do not add `phase` to `GameClock`.
- Do not split `strategy.py` or `test_backtest_maker.py` in this step.
- Do not keep `MarketAnchorTape` "because the plan named it" when `MidSeries` already is that tape.

---

## Required follow-up (blockers) vs optional (nits)

**Blockers — change these before calling step 3A done:**

1. Observed clock is a strategy/config tape, not three required fields on `MatchSignals`. Optional `ObservedClockTape` on `DotaMakerConfig`; `build_strategy_configs` reads `plans`; old `MatchSignals` constructors stop growing. Align tape lengths in `__post_init__`.
2. Move 3A orchestration out of `run.py` into `feed_schedules.py` / `signals.py`. This commit must not be the one that takes a 1806-line CLI to 2034.
3. `MatchFeedPlan` as `SchedulePlan | GridV1Plan` (or equivalent). Delete `mode` + nullable `binding`, the anonymous bind tuples, the `schedules=` extra map, and the "schedule without binding" raises.
4. Delete `MarketAnchorTape` / `anchor_at` / `market_anchor_tape`. Use `MidSeries` + `lookup_reference_mid`.

**Should-fix in the same pass if the diff is already open:**

5. Split `SnapshotFeatures` out of `StateFeatures`; build `GameFeatureRow` from `state_features`, do not copy nine fields.
6. `ArchiveJoinInput` as a real 2-variant (or no wrapper), not `catalog | None` plus `audit | None` plus asserts.
7. Identity mismatch is an exclusion, same channel as fingerprint/missing file.
8. Shared `_finish_candidates` for the admitted / refused / unbound ladder.

**Nits — do not block on these:**

9. `build_parser().error` rebuild, LoL feature-frame unfiltered rename, redundant `int(match_id)`, ensemble `stage2.py` constructor (dies with 1.1).

The no-silent-fallback resolver, `game_features.parquet`, causal anchors, per-model batching, and Oddin archive-dir strip are the parts to keep while doing 1–4. Do not restructure those to make `run.py` prettier.
