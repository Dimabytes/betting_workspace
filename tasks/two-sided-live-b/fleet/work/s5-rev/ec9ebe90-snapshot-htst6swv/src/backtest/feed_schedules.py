"""Per-match feed planning: admitted archive schedule or seeded grid-v1 cadence.

A default backtest run resolves each selected match against the archive index:
a linked, admitted archive replays its persisted FeedSchedule; a match with no
archive keeps grid-v1; anything in between is an explicit exclusion, never a
silent fallback.
"""

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from archive_index.schedule import FeedSchedule, read_schedule, schedule_path_for
from shared.constants.lol import LOL_RESEARCH_MODEL_DIR
from shared.constants.paths import (
    ARCHIVE_INDEX_DIR,
    RESEARCH_MODEL_DIR,
    RESEARCH_NOXP_MODEL_DIR,
    TRADER_DIR,
)
from shared.constants.strategy import EXIT_FEED_STALE_SECONDS, GRID_FEED_STALE_SECONDS
from shared.utils.dota_features import HistoryPolicy, SnapshotHistory, history_levels
from shared.utils.log import get_logger
from shared.utils.match_catalog import CatalogEntry, MatchCatalog
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.parsing import opt_str
from trader.oddin_feed import ODDIN_FEED_STALE_SECONDS

logger = get_logger(__name__)

DUPLICATE_PREFIX = "duplicate_of:"


@dataclass(frozen=True)
class ScheduleBinding:
    """The admitted archive a match replays, resolved down to real paths."""

    archive_root: str
    archive_id: str
    feed_source: Literal["grid", "oddin"]
    archive_dir: Path
    schedule_path: Path
    schedule_fingerprint: str
    schedule: FeedSchedule


@dataclass(frozen=True)
class SchedulePlan:
    """A match replaying an admitted archive's persisted FeedSchedule."""

    match_id: int
    binding: ScheduleBinding
    model_dir: Path


@dataclass(frozen=True)
class GridV1Plan:
    """A match with no usable archive; the seeded grid-v1 cadence replays."""

    match_id: int
    model_dir: Path


MatchFeedPlan = SchedulePlan | GridV1Plan


@dataclass(frozen=True)
class DotaArchiveJoin:
    """Catalog rows the Dota feed-plan resolver joins through."""

    catalog: MatchCatalog


@dataclass(frozen=True)
class LolArchiveJoin:
    """Audit rows the LoL feed-plan resolver joins through (condition_id)."""

    audit: pd.DataFrame


ArchiveJoinInput = DotaArchiveJoin | LolArchiveJoin


@dataclass(frozen=True)
class FeedPlanResult:
    """Resolved plans plus the matches excluded, with a stable reason each."""

    plans: dict[int, MatchFeedPlan]
    exclusions: dict[int, str]


@dataclass(frozen=True)
class _ArchiveRow:
    """The index columns feed planning reads, normalized to plain values."""

    archive_root: str
    archive_id: str
    admission: str
    feed_source: Literal["grid", "oddin"]
    steam_match_id: str | None
    condition_id: str | None
    schedule_fingerprint: str | None


def _archive_roots() -> dict[str, Path]:
    """Root label -> directory, the same map `archive_index --root` defaults to."""
    return {"trader": TRADER_DIR}


def load_archive_index() -> pd.DataFrame:
    """Read the archive index; require `make archive-index` to have run."""
    path = ARCHIVE_INDEX_DIR / "index.parquet"
    if not path.is_file():
        raise ValueError(f"archive index missing: {path}; run make archive-index first")
    return pd.read_parquet(path)


def _bound_feed_source(raw: object) -> Literal["grid", "oddin"]:
    """The index's bound feed; anything but oddin replays as a grid archive."""
    return "oddin" if opt_str(raw) == "oddin" else "grid"


def _archive_rows(index: pd.DataFrame, game: str) -> list[_ArchiveRow]:
    """Identified archives of one game; meta-less rows can never join a match."""
    mask = index["meta_ok"].astype(bool) & (index["game"].astype(str) == game)
    return [
        _ArchiveRow(
            archive_root=str(row.archive_root),
            archive_id=str(row.archive_id),
            admission=str(row.admission),
            feed_source=_bound_feed_source(row.feed_source),
            steam_match_id=opt_str(row.steam_match_id),
            condition_id=opt_str(row.condition_id),
            schedule_fingerprint=opt_str(row.schedule_fingerprint),
        )
        for row in index[mask].itertuples(index=False)
    ]


def _bind_schedule(
    *,
    game: str,
    row: _ArchiveRow,
    expected_fingerprint: str,
) -> ScheduleBinding | str:
    """Read + fingerprint-check the schedule; a failure is an exclusion reason."""
    archive_root_dir = _archive_roots().get(row.archive_root)
    if archive_root_dir is None:
        return "schedule_missing"
    path = schedule_path_for(ARCHIVE_INDEX_DIR, row.archive_root, game, row.archive_id)
    if not path.is_file():
        return "schedule_missing"
    try:
        schedule = read_schedule(path)
    except (OSError, ValueError, KeyError, TypeError):
        return "schedule_missing"
    if schedule.fingerprint != expected_fingerprint:
        return "schedule_fingerprint_mismatch"
    return ScheduleBinding(
        archive_root=row.archive_root,
        archive_id=row.archive_id,
        feed_source=row.feed_source,
        archive_dir=archive_root_dir / row.archive_id,
        schedule_path=path,
        schedule_fingerprint=expected_fingerprint,
        schedule=schedule,
    )


def _finish_candidates(candidates: Sequence[_ArchiveRow]) -> _ArchiveRow | str | None:
    """The admitted row, else the first candidate's admission as reason, else unbound."""
    for row in candidates:
        if row.admission == "admitted":
            return row
    if candidates:
        return f"archive:{candidates[0].admission}"
    return None


@dataclass(frozen=True)
class DotaModelDirs:
    """XP catalog for GRID, no-XP catalog for Oddin. Both are always set."""

    xp: Path
    noxp: Path


def dota_model_dirs(xp: Path | None, noxp: Path | None) -> DotaModelDirs:
    """Research defaults, or both overrides. One override without the other is refused."""
    if (xp is None) != (noxp is None):
        raise ValueError("dota model override requires both an XP catalog and a no-XP catalog")
    return DotaModelDirs(
        RESEARCH_MODEL_DIR if xp is None else xp,
        RESEARCH_NOXP_MODEL_DIR if noxp is None else noxp,
    )


def _dota_model_dir(feed_source: str, models: DotaModelDirs) -> Path:
    """GRID reads the XP catalog. Oddin reads the no-XP catalog."""
    return models.noxp if feed_source == "oddin" else models.xp


def _resolve_dota_match(
    match_id: int,
    entry: CatalogEntry,
    rows: Sequence[_ArchiveRow],
    by_archive: Mapping[tuple[str, str], _ArchiveRow],
    models: DotaModelDirs,
) -> MatchFeedPlan | str:
    """Feed plan for one Dota match, or its exclusion reason."""
    grid = GridV1Plan(match_id=match_id, model_dir=_dota_model_dir("grid", models))
    if not entry.archive_id:
        steam = str(match_id)
        condition = entry.condition_id.lower()
        candidates = sorted(
            (
                row
                for row in rows
                if row.steam_match_id == steam
                or (row.condition_id is not None and row.condition_id.lower() == condition)
            ),
            key=lambda row: (row.archive_root, row.archive_id),
        )
        outcome = _finish_candidates(candidates)
        if isinstance(outcome, _ArchiveRow):
            return "archive_unlinked"
        return outcome if outcome is not None else grid
    row = by_archive.get((entry.archive_root or "", entry.archive_id))
    if row is None:
        return "schedule_missing"
    if row.admission != "admitted":
        return f"archive:{row.admission}"
    index_fingerprint = row.schedule_fingerprint or ""
    if index_fingerprint != (entry.schedule_fingerprint or ""):
        return "schedule_stale"
    bound = _bind_schedule(game="dota", row=row, expected_fingerprint=index_fingerprint)
    if isinstance(bound, str):
        return bound
    if bound.schedule.identity.steam_match_id != str(match_id):
        return "schedule_identity_mismatch"
    return SchedulePlan(
        match_id=match_id,
        binding=bound,
        model_dir=_dota_model_dir(bound.feed_source, models),
    )


def resolve_dota_feed_plans(
    selected_ids: Sequence[int],
    catalog: MatchCatalog,
    index: pd.DataFrame,
    model_override: Path | None,
    *,
    model_override_noxp: Path | None,
) -> FeedPlanResult:
    """Feed plan per selected Dota match; exclusions carry stable reasons."""
    models = dota_model_dirs(model_override, model_override_noxp)
    rows = _archive_rows(index, "dota")
    by_archive = {(row.archive_root, row.archive_id): row for row in rows}
    plans: dict[int, MatchFeedPlan] = {}
    exclusions: dict[int, str] = {}
    for match_id in selected_ids:
        outcome = _resolve_dota_match(match_id, catalog[match_id], rows, by_archive, models)
        if isinstance(outcome, str):
            exclusions[match_id] = outcome
        else:
            plans[match_id] = outcome
    return FeedPlanResult(plans=plans, exclusions=exclusions)


def _resolve_lol_match(
    match_id: int,
    condition: str,
    rows: Sequence[_ArchiveRow],
    model_dir: Path,
) -> MatchFeedPlan | str:
    """Feed plan for one LoL match, or its exclusion reason."""
    candidates = sorted(
        (
            row
            for row in rows
            if row.condition_id is not None
            and row.condition_id.lower() == condition.lower()
            and not row.admission.startswith(DUPLICATE_PREFIX)
        ),
        key=lambda row: (row.archive_root, row.archive_id),
    )
    outcome = _finish_candidates(candidates)
    if outcome is None or isinstance(outcome, str):
        return (
            outcome if outcome is not None else GridV1Plan(match_id=match_id, model_dir=model_dir)
        )
    bound = _bind_schedule(
        game="lol", row=outcome, expected_fingerprint=outcome.schedule_fingerprint or ""
    )
    if isinstance(bound, str):
        return bound
    if bound.schedule.identity.condition_id.lower() != condition.lower():
        return "schedule_identity_mismatch"
    return SchedulePlan(match_id=match_id, binding=bound, model_dir=model_dir)


def resolve_lol_feed_plans(
    selected_ids: Sequence[int],
    audit: pd.DataFrame,
    index: pd.DataFrame,
    model_override: Path | None,
) -> FeedPlanResult:
    """Feed plan per selected LoL match; join runs on audit condition_id."""
    rows = _archive_rows(index, "lol")
    condition_by_match = {
        int(row["match_id"]): str(row["condition_id"]) for _, row in audit.iterrows()
    }
    model_dir = model_override or LOL_RESEARCH_MODEL_DIR
    plans: dict[int, MatchFeedPlan] = {}
    exclusions: dict[int, str] = {}
    for match_id in selected_ids:
        outcome = _resolve_lol_match(match_id, condition_by_match[match_id], rows, model_dir)
        if isinstance(outcome, str):
            exclusions[match_id] = outcome
        else:
            plans[match_id] = outcome
    return FeedPlanResult(plans=plans, exclusions=exclusions)


def resolve_feed_plans(
    join: ArchiveJoinInput,
    selected_ids: Sequence[int],
    model_override: Path | None,
    match_id: int | None,
    *,
    model_override_noxp: Path | None,
) -> FeedPlanResult:
    """Default-run feed resolution: log exclusions, refuse an excluded --match-id."""
    index = load_archive_index()
    if isinstance(join, DotaArchiveJoin):
        feed = resolve_dota_feed_plans(
            selected_ids,
            join.catalog,
            index,
            model_override,
            model_override_noxp=model_override_noxp,
        )
    else:
        if model_override_noxp is not None:
            raise ValueError("--model-dir-noxp is dota-only")
        feed = resolve_lol_feed_plans(selected_ids, join.audit, index, model_override)
    for excluded, reason in sorted(feed.exclusions.items()):
        logger.warning("archive exclusion: match %s - %s", excluded, reason)
    if match_id is not None and match_id in feed.exclusions:
        raise ValueError(f"match {match_id}: {feed.exclusions[match_id]}")
    return feed


def reject_schedule_flags(
    plans: Mapping[int, MatchFeedPlan],
    *,
    lag_seconds: int | None,
    cadence_mean_interval: int | None,
    validation_dataset: Path | None,
) -> None:
    """Cadence/dataset overrides are meaningless on archive schedules; refuse them."""
    first = next((plan.match_id for plan in plans.values() if isinstance(plan, SchedulePlan)), None)
    if first is not None and (
        lag_seconds is not None
        or cadence_mean_interval is not None
        or validation_dataset is not None
    ):
        raise ValueError(
            "--lag-seconds/--cadence-mean-interval/--validation-dataset do not apply "
            f"to archive schedules (schedule match {first})"
        )


def schedule_archive_dirs(plans: Mapping[int, MatchFeedPlan]) -> dict[int, Path]:
    """Real archive dir per schedule-bound match, for own-book stripping."""
    return {
        plan.match_id: plan.binding.archive_dir
        for plan in plans.values()
        if isinstance(plan, SchedulePlan)
    }


def assert_archive_binding(plan: MatchFeedPlan, *, archive_id: str) -> None:
    """The caller names the archive this plan must bind.

    A lost link has to fail here. Falling through to grid-v1 would replay the
    wrong model and still look like a successful run.
    """
    bound = plan.binding.archive_id if isinstance(plan, SchedulePlan) else ""
    if bound != archive_id:
        raise AssertionError(
            f"match {plan.match_id}: feed plan binds {bound or 'grid-v1'}, "
            f"expected {archive_id or 'grid-v1'}"
        )


def schedule_map_sha256(plans: Mapping[int, MatchFeedPlan]) -> str:
    """Schedule-identity digest of the resolved plans; model dirs hash separately.

    Lines are `{match_id}|{mode}|{schedule_fingerprint}` — rebuilding the model
    or dataset leaves this digest unchanged; a fingerprint change does not.
    """
    lines = sorted(
        f"{plan.match_id}|{'schedule' if isinstance(plan, SchedulePlan) else 'grid_v1'}|"
        f"{plan.binding.schedule_fingerprint if isinstance(plan, SchedulePlan) else ''}"
        for plan in plans.values()
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def entry_stale_seconds(binding: ScheduleBinding) -> float:
    """Feed-entry freshness budget for the bound source; same constants as live."""
    if binding.feed_source == "oddin":
        return ODDIN_FEED_STALE_SECONDS
    return GRID_FEED_STALE_SECONDS


def grid_exit_age_seconds(entry_age_seconds: float) -> float:
    """SELL-pull and recovery-stale age: live exit timeout, never below the entry age."""
    return max(entry_age_seconds, EXIT_FEED_STALE_SECONDS)


@dataclass(frozen=True)
class FeedTick:
    """One tape position replayed through the feed, in receive order.

    `valid` is the policy's history check (always True for non-recording
    ticks); `stale` marks ticks that never decide: history-invalid, the
    connect tick, or a gap past the age budget. `derived()` resolves the 60
    history columns against this position's tape lazily — call it before
    advancing the iterator.
    """

    position: int
    valid: bool
    stale: bool
    _tape: SnapshotHistory = field(repr=False, compare=False)

    def derived(self, second: int, current_levels: Sequence[float]) -> dict[str, float]:
        """The 60 history columns as-of this position's tape."""
        return self._tape.derived(second, current_levels)


def walk_feed_ticks(
    seconds: Sequence[int],
    levels: np.ndarray,
    record_mask: Sequence[bool],
    received_ns: Sequence[int],
    stale_age_seconds: float,
    history_policy: HistoryPolicy,
) -> Iterator[FeedTick]:
    """Replay one tape, yielding each position's feed status in receive order.

    A recording position snapshots first, then checks required lags. The first
    surviving tick only connects — live never consults the watchdog before the
    first consume — so it arrives stale, and the next tick stays fresh however
    long connect took; later gaps measure from the last surviving tick.
    History-invalid and stale ticks keep their snapshots on the tape.
    """
    tape = SnapshotHistory(history_policy)
    feed_count = 0
    last_consumed_ns = 0
    for position in range(len(seconds)):
        valid = True
        if record_mask[position]:
            tape.record(int(seconds[position]), levels[position])
            valid = tape.check_required_lags(int(seconds[position]))
        if not valid:
            yield FeedTick(position, False, True, tape)
            continue
        stale = True
        if feed_count == 1:
            # The second surviving tick is the first watchdog consume: always
            # fresh, and the reference every later gap measures from.
            last_consumed_ns = received_ns[position]
            stale = False
        elif feed_count > 1:
            gap_seconds = (received_ns[position] - last_consumed_ns) / NS_PER_SECOND
            last_consumed_ns = received_ns[position]
            stale = gap_seconds > stale_age_seconds
        feed_count += 1
        yield FeedTick(position, valid, stale, tape)


def schedule_feed_positions(plan: SchedulePlan, history_policy: HistoryPolicy) -> tuple[int, ...]:
    """Positions that made the feed in a schedule replay under the tape policy.

    Ticks record their snapshot row unless terminal; received_ns is the tick's
    own archive receive clock. Positions index `plan.binding.schedule.ticks`.
    """
    ticks = plan.binding.schedule.ticks
    return tuple(
        feed_tick.position
        for feed_tick in walk_feed_ticks(
            [tick.game_second for tick in ticks],
            np.asarray([history_levels(asdict(tick)) for tick in ticks], dtype=np.float64),
            [not tick.terminal for tick in ticks],
            [tick.received_ns for tick in ticks],
            grid_exit_age_seconds(entry_stale_seconds(plan.binding)),
            history_policy,
        )
        if feed_tick.valid
    )
