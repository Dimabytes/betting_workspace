"""Dota model feature contracts: the 81/70-column catalogs and their history math.

Current snapshot levels come first in each list, then per-minute
game_change_{1..5}m_* and game_total_5m_* derived against already-received
snapshots, then logit_market_p_radiant / market_vs_prior from the current
market quote. GRID catalogs then append pending deaths and death ages. The history lookup is governed by
the model's HistoryPolicy: the tape drops snapshots before `start_second`,
lag targets below `start_second` resolve to NaN, and each remaining pivot is
the nearest taped snapshot before or after `second - k*60` within
`max_pivot_gap_seconds` — never a zero substitute. trainer, backtest
signals, and the live model server all build vectors through this module;
history_feature_block is the single implementation of the pivot/cascade math.
"""

import bisect
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from shared.types.dataset import DotaSnapshotFeatures
from shared.utils.board_features import (
    BOARD_DEATH_AGE_CAP_SECONDS,
    BOARD_FEATURE_COLUMNS,
    BOARD_LEAD_SECONDS,
)
from shared.utils.top_players import (
    TOP_PLAYER_FIELD_NAMES,
    TopPlayerFeatures,
    top_player_feature_values,
)

DOTA_LAG_MINUTES = (1, 2, 3, 4, 5)
DOTA_LAG_OFFSETS_SECONDS = tuple(minute * 60 for minute in DOTA_LAG_MINUTES)
DOTA_HISTORY_WINDOW_SECONDS = DOTA_LAG_OFFSETS_SECONDS[-1]


DOTA_BASE_HISTORY_FIELDS = (
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "top1_nw_adv",
    "radiant_top1_nw_ratio",
    "dire_top1_nw_ratio",
)
DOTA_TOP3_FIELDS = (
    "top3_nw_adv",
    "radiant_top3_nw_ratio",
    "dire_top3_nw_ratio",
)
# Tape levels in this fixed order feed every history lookup.
DOTA_HISTORY_FIELDS = (*DOTA_BASE_HISTORY_FIELDS, *DOTA_TOP3_FIELDS)

LOGIT_CLIP_LOW = 0.001
LOGIT_CLIP_HIGH = 0.999


def history_column_names(fields: Sequence[str]) -> list[str]:
    """game_change_{1..5}m_<field> minute-outer/field-inner, then totals."""
    return [
        *(f"game_change_{minute}m_{field}" for minute in DOTA_LAG_MINUTES for field in fields),
        *(f"game_total_5m_{field}" for field in fields),
    ]


DOTA_HISTORY_COLUMN_NAMES = history_column_names(DOTA_HISTORY_FIELDS)
# Tape-derived columns — the only catalog inputs allowed to carry NaN.
DOTA_HISTORY_FEATURE_NAMES = frozenset(DOTA_HISTORY_COLUMN_NAMES)


@dataclass(frozen=True)
class HistoryPolicy:
    """How one model's history tape is built and which tape gaps drop a tick.

    `start_second` clips both the tape and the lag targets below it (NaN).
    `max_pivot_gap_seconds` bounds the distance to the nearest usable pivot.
    `drop_gap_ticks` decides whether a decision tick whose required pivot is
    missing is invalid (no new signal) or just carries NaN history columns.
    """

    start_second: int
    max_pivot_gap_seconds: float
    drop_gap_ticks: bool


# Dota GRID/Steam and LoL GRID: the first full minute after the feed starts,
# a 30s pivot reach, and gaps drop the tick.
GRID_HISTORY_POLICY = HistoryPolicy(
    start_second=60, max_pivot_gap_seconds=30.0, drop_gap_ticks=True
)
# Oddin's per-second tape starts at -60; its pivots are always near-exact, so
# nothing is dropped.
ODDIN_HISTORY_POLICY = HistoryPolicy(
    start_second=-60, max_pivot_gap_seconds=15.0, drop_gap_ticks=False
)
DOTA_XP_FEATURE_COLUMNS: list[str] = [
    "second",
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "radiant_top1_nw_ratio",
    "dire_top1_nw_ratio",
    "market_radiant_prior",
    *history_column_names(DOTA_BASE_HISTORY_FIELDS),
    *DOTA_TOP3_FIELDS,
    *history_column_names(DOTA_TOP3_FIELDS),
    "logit_market_p_radiant",
    "market_vs_prior",
    "market_p_radiant",
    *BOARD_FEATURE_COLUMNS,
]

DOTA_NOXP_FEATURE_COLUMNS: list[str] = [
    column
    for column in DOTA_XP_FEATURE_COLUMNS
    if column not in BOARD_FEATURE_COLUMNS
    and column != "radiant_xp_adv"
    and not column.endswith("_radiant_xp_adv")
]


def catalog_has_board(features: Sequence[str]) -> bool:
    """True when the catalog includes the scoreboard pending-death columns."""
    return BOARD_FEATURE_COLUMNS[0] in features


def history_policy_for_columns(feature_columns: Sequence[str]) -> HistoryPolicy:
    """Select the received-history policy for the canonical XP or no-XP catalog."""
    columns = tuple(feature_columns)
    if columns == tuple(DOTA_XP_FEATURE_COLUMNS):
        return GRID_HISTORY_POLICY
    if columns == tuple(DOTA_NOXP_FEATURE_COLUMNS):
        return ODDIN_HISTORY_POLICY
    raise ValueError("model features do not match a canonical XP or no-XP catalog")


def history_levels(source: Mapping[str, float]) -> tuple[float, ...]:
    """One row's tape levels in DOTA_HISTORY_FIELDS order."""
    return tuple(float(source[field]) for field in DOTA_HISTORY_FIELDS)


class SnapshotState(Protocol):
    """The state fields every source exposes under the catalog's names.

    GameSnapshot (live), ExactSecondState (Dota prepare), FrameFeatures
    (LoL prepare), and the diagnostic scripts' Sample all satisfy this.
    Read-only properties so frozen dataclasses qualify.
    """

    @property
    def radiant_nw_adv(self) -> int: ...
    @property
    def radiant_nw(self) -> int: ...
    @property
    def dire_nw(self) -> int: ...
    @property
    def radiant_xp_adv(self) -> int: ...
    @property
    def deaths_radiant(self) -> int: ...
    @property
    def deaths_dire(self) -> int: ...
    @property
    def top(self) -> TopPlayerFeatures: ...


# Tape fields living directly on the state object; the rest live on state.top.
_STATE_TAPE_FIELDS = tuple(
    field for field in DOTA_HISTORY_FIELDS if field not in TOP_PLAYER_FIELD_NAMES
)


def snapshot_history_levels(state: SnapshotState) -> tuple[float, ...]:
    """One state's tape levels in DOTA_HISTORY_FIELDS order."""
    flat = top_player_feature_values(state.top)
    flat.update({field: getattr(state, field) for field in _STATE_TAPE_FIELDS})
    return history_levels(flat)


def snapshot_features(state: SnapshotState) -> DotaSnapshotFeatures:
    """The shared 12-field snapshot block of one state reading."""
    top = state.top
    return DotaSnapshotFeatures(
        radiant_nw_adv=state.radiant_nw_adv,
        radiant_nw=state.radiant_nw,
        dire_nw=state.dire_nw,
        radiant_xp_adv=state.radiant_xp_adv,
        deaths_radiant=state.deaths_radiant,
        deaths_dire=state.deaths_dire,
        top1_nw_adv=top.top1_nw_adv,
        radiant_top1_nw_ratio=top.radiant_top1_nw_ratio,
        dire_top1_nw_ratio=top.dire_top1_nw_ratio,
        top3_nw_adv=top.top3_nw_adv,
        radiant_top3_nw_ratio=top.radiant_top3_nw_ratio,
        dire_top3_nw_ratio=top.dire_top3_nw_ratio,
    )


def market_derived_values(market_p_radiant: float, market_radiant_prior: float) -> dict[str, float]:
    """The two market-derived model inputs for one decision."""
    clipped = min(max(market_p_radiant, LOGIT_CLIP_LOW), LOGIT_CLIP_HIGH)
    return {
        "logit_market_p_radiant": math.log(clipped / (1.0 - clipped)),
        "market_vs_prior": market_p_radiant - market_radiant_prior,
    }


def market_derived_columns(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """The two market-derived columns over a decision frame's mid and prior."""
    clipped = frame["market_p_radiant"].clip(LOGIT_CLIP_LOW, LOGIT_CLIP_HIGH)
    return {
        "logit_market_p_radiant": pd.Series(np.log(clipped / (1.0 - clipped)), index=frame.index),
        "market_vs_prior": frame["market_p_radiant"] - frame["market_radiant_prior"],
    }


def _nearest_pivot_indices(
    sorted_seconds: np.ndarray, targets: np.ndarray, max_gap_seconds: float
) -> np.ndarray:
    """Index into sorted_seconds of the nearest taped second per target, or -1.

    The nearest row before or after the target wins; ties keep the earlier
    one. A pivot after the target is still a received snapshot — the feed
    tape only promises arrival order, not one row per second.
    """
    count = sorted_seconds.size
    if count == 0:
        return np.full(targets.shape, -1)
    # Clip folds the empty-side cases into the same-index tie, which breaks to
    # the earlier side — the only candidate left.
    before = np.clip(np.searchsorted(sorted_seconds, targets, side="right") - 1, 0, count - 1)
    after = np.clip(np.searchsorted(sorted_seconds, targets), 0, count - 1)
    before_dist = np.abs(targets - sorted_seconds[before])
    after_dist = np.abs(sorted_seconds[after] - targets)
    index = np.where(before_dist <= after_dist, before, after)
    return np.where(np.minimum(before_dist, after_dist) <= max_gap_seconds, index, -1)


def history_feature_block(
    tape_seconds: np.ndarray,
    tape_levels: np.ndarray,
    decision_seconds: np.ndarray,
    decision_levels: np.ndarray,
    start_second: int,
    max_pivot_gap_seconds: float,
) -> dict[str, np.ndarray]:
    """Vectorized 60-column history block for one match's second-sorted tape.

    Tape rows before start_second never participate. Each pivot is the taped
    second nearest its target within the gap budget; targets below
    start_second and pivots beyond the budget stay NaN, and a missing pivot
    cascades NaN through the remaining minute diffs of that field. The 5m
    total compares the current level against the -300s pivot.
    """
    rows = len(decision_seconds)
    tape_seconds = np.asarray(tape_seconds, dtype=np.int64)
    usable = np.flatnonzero(tape_seconds >= start_second)
    order = usable[np.argsort(tape_seconds[usable], kind="stable")]
    sorted_seconds = tape_seconds[order]
    sorted_levels = np.asarray(tape_levels, dtype=np.float64)[order]
    field_count = len(DOTA_HISTORY_FIELDS)
    pivots = np.full((rows, len(DOTA_LAG_MINUTES), field_count), np.nan)
    if rows and sorted_seconds.size:
        decision_seconds = np.asarray(decision_seconds, dtype=np.int64)
        for lag_index, offset in enumerate(DOTA_LAG_OFFSETS_SECONDS):
            targets = decision_seconds - offset
            eligible = np.flatnonzero(targets >= start_second)
            if not eligible.size:
                continue
            indices = _nearest_pivot_indices(
                sorted_seconds, targets[eligible], max_pivot_gap_seconds
            )
            found = indices >= 0
            if not found.any():
                continue
            pivots[eligible[found], lag_index, :] = sorted_levels[indices[found]]
    columns: dict[str, np.ndarray] = {}
    for field_index, field in enumerate(DOTA_HISTORY_FIELDS):
        previous = decision_levels[:, field_index]
        for lag_index, minute in enumerate(DOTA_LAG_MINUTES):
            columns[f"game_change_{minute}m_{field}"] = previous - pivots[:, lag_index, field_index]
            previous = pivots[:, lag_index, field_index]
        columns[f"game_total_5m_{field}"] = (
            decision_levels[:, field_index] - pivots[:, -1, field_index]
        )
    return columns


class SnapshotHistory:
    """Per-match tape of received game levels under one HistoryPolicy.

    Seconds below `policy.start_second` never join the tape. derived()
    resolves pivots through history_feature_block on the recorded buffer —
    the same math as stored-tape vectorization: the taped second nearest the
    target wins within the pivot gap, an equal second resolves to the most
    recent record. Entries older than the newest second minus (300s + the
    pivot gap) are evicted, so the pivot just before the five-minute edge
    survives until its last lookup.
    """

    def __init__(self, history_policy: HistoryPolicy) -> None:
        self._policy = history_policy
        self._seconds: list[int] = []
        self._levels: list[tuple[float, ...]] = []
        # The feed's first recorded second survives eviction: it is the warmup
        # boundary for check_required_lags, not the moving tape head.
        self._first_second: int | None = None

    def record(self, second: int, levels: Sequence[float]) -> None:
        """Insert one snapshot's levels in DOTA_HISTORY_FIELDS order.

        A repeat tick at an already-taped second overwrites it: the pivot at
        that second resolves to the latest received levels either way, so
        keeping the older copy only grows the buffer while the game clock
        stands still.
        """
        second = int(second)
        if second < self._policy.start_second:
            return
        index = bisect.bisect_right(self._seconds, second)
        if index and self._seconds[index - 1] == second:
            self._levels[index - 1] = tuple(float(value) for value in levels)
            return
        if self._first_second is None:
            self._first_second = second
        self._seconds.insert(index, second)
        self._levels.insert(index, tuple(float(value) for value in levels))
        floor = self._seconds[-1] - (
            DOTA_HISTORY_WINDOW_SECONDS + self._policy.max_pivot_gap_seconds
        )
        cut = bisect.bisect_left(self._seconds, floor)
        if cut:
            del self._seconds[:cut]
            del self._levels[:cut]

    def check_required_lags(self, second: int) -> bool:
        """False when a required lag target lands in a tape gap.

        Only checked when `drop_gap_ticks` is on. Targets below
        `start_second` resolve to NaN by contract, and a target before the
        feed's first recorded second is warmup — both keep the tick valid. A
        target at or past that first second with no pivot inside the gap
        marks the tick invalid; its snapshot stays on the tape.
        """
        if not self._policy.drop_gap_ticks or self._first_second is None:
            return True
        targets = np.asarray(
            [int(second) - offset for offset in DOTA_LAG_OFFSETS_SECONDS], dtype=np.int64
        )
        required = targets >= max(self._policy.start_second, self._first_second)
        if not required.any():
            return True
        pivots = _nearest_pivot_indices(
            np.asarray(self._seconds, dtype=np.int64),
            targets[required],
            self._policy.max_pivot_gap_seconds,
        )
        return bool((pivots >= 0).all())

    def derived(self, second: int, current_levels: Sequence[float]) -> dict[str, float]:
        """The 60 history columns for one decision at `second`."""
        block = history_feature_block(
            np.asarray(self._seconds, dtype=np.int64),
            np.asarray(self._levels, dtype=np.float64),
            np.asarray([int(second)], dtype=np.int64),
            np.asarray([[float(value) for value in current_levels]], dtype=np.float64),
            self._policy.start_second,
            self._policy.max_pivot_gap_seconds,
        )
        return {name: float(column[0]) for name, column in block.items()}


def _walk_tape(
    seconds: Sequence[int],
    levels: np.ndarray,
    record_mask: Sequence[bool],
    history_policy: HistoryPolicy,
) -> Iterator[tuple[int, SnapshotHistory]]:
    """Yield (position, tape) after each position's record step, in tape order."""
    tape = SnapshotHistory(history_policy)
    for position in range(len(seconds)):
        if record_mask[position]:
            tape.record(int(seconds[position]), levels[position])
        yield position, tape


def replay_tape_history(
    seconds: Sequence[int],
    levels: np.ndarray,
    record_mask: Sequence[bool],
    decision_positions: Sequence[int],
    history_policy: HistoryPolicy,
) -> pd.DataFrame:
    """Replay one tape through SnapshotHistory; one derived row per decision position.

    A recording tick joins the tape before a decision on the same position
    resolves — a live tick's pivots include itself, a terminal (non-recording)
    decision tick sees the tape without itself. decision_positions ascend;
    output rows follow that order.
    """
    decisions = frozenset(int(position) for position in decision_positions)
    blocks: list[dict[str, float]] = []
    for position, tape in _walk_tape(seconds, levels, record_mask, history_policy):
        if position in decisions:
            blocks.append(tape.derived(int(seconds[position]), levels[position]))
    return pd.DataFrame(blocks, columns=pd.Index(DOTA_HISTORY_COLUMN_NAMES))


def attach_history_features(
    frame: pd.DataFrame,
    history: pd.DataFrame,
    *,
    key_seconds: pd.Series,
    start_second: int,
) -> pd.DataFrame:
    """Return frame + the 60 derived history columns; NaN where a lag lacks a pivot.

    history holds the full per-match tape: match_id, game_second, and the
    DOTA_HISTORY_FIELDS levels — a complete per-second artifact, so a pivot
    must land on the exact lag second. Tape rows and lag targets below
    start_second are excluded under the policy. key_seconds is each frame
    row's game second, index-aligned with frame (minute rows: second;
    validation rows: second - lag). A frame match absent from the tape is a
    mismatch between artifacts, not a legitimate all-NaN row — it raises.
    """
    derived = pd.DataFrame(np.nan, index=frame.index, columns=list(DOTA_HISTORY_COLUMN_NAMES))
    if frame.empty:
        return pd.concat([frame, derived], axis=1)
    field_names = list(DOTA_HISTORY_FIELDS)
    history_groups = {match_id: part for match_id, part in history.groupby("match_id", sort=False)}
    for match_id, part in frame.groupby("match_id", sort=False):
        tape = history_groups.get(match_id)
        if tape is None:
            raise ValueError(f"match {match_id}: missing from history tape")
        block = history_feature_block(
            tape["game_second"].to_numpy(dtype=np.int64),
            tape[field_names].to_numpy(dtype=np.float64),
            key_seconds.loc[part.index].to_numpy(dtype=np.int64),
            part[field_names].to_numpy(dtype=np.float64),
            start_second,
            0.0,
        )
        block_frame = pd.DataFrame(block)
        derived.loc[part.index, block_frame.columns] = block_frame.to_numpy()
    return pd.concat([frame, derived], axis=1)


def _capped_tape_death_age_s(
    tape_seconds: np.ndarray,
    tape_deaths: np.ndarray,
    targets: np.ndarray,
) -> np.ndarray:
    """Seconds from each target back to the last deaths increase, capped.

    An increase is a tape row whose deaths exceed the previous tape row.
    Targets at or before the first increase — and increases older than the
    cap — read as BOARD_DEATH_AGE_CAP_SECONDS.
    """
    ages = np.full(targets.shape, BOARD_DEATH_AGE_CAP_SECONDS, dtype=np.float64)
    if tape_seconds.size < 2:
        return ages
    increment_seconds = tape_seconds[1:][tape_deaths[1:] > tape_deaths[:-1]]
    if increment_seconds.size == 0:
        return ages
    positions = np.searchsorted(increment_seconds, targets, side="right") - 1
    known = positions >= 0
    ages[known] = np.minimum(
        targets[known] - increment_seconds[positions[known]],
        BOARD_DEATH_AGE_CAP_SECONDS,
    )
    return ages


def attach_board_deaths(
    frame: pd.DataFrame,
    board_tape: pd.DataFrame,
    *,
    key_seconds: pd.Series,
) -> pd.DataFrame:
    """Rewrite deaths_* to the board clock and attach pending_* plus death age.

    board_tape is the per-second game_features artifact (match_id, game_second,
    deaths_*). Board-implied deaths for a decision keyed at second K are the
    tape deaths at the last taped second <= K + BOARD_LEAD_SECONDS — the board
    shows every kill the table will only print later. pending_deaths_* is the
    board/table gap, clipped at 0 (board stalls can lag the table).
    board_death_age_*_s is seconds from that same target back to the side's
    latest death on the tape, capped; it keeps counting after pending returns
    to 0.
    """
    board_radiant = np.zeros(len(frame), dtype=np.float64)
    board_dire = np.zeros(len(frame), dtype=np.float64)
    age_radiant = np.full(len(frame), BOARD_DEATH_AGE_CAP_SECONDS, dtype=np.float64)
    age_dire = np.full(len(frame), BOARD_DEATH_AGE_CAP_SECONDS, dtype=np.float64)
    tape_groups = {match_id: part for match_id, part in board_tape.groupby("match_id", sort=False)}
    for match_id, part in frame.groupby("match_id", sort=False):
        tape = tape_groups.get(match_id)
        if tape is None or tape.empty:
            raise ValueError(f"match {match_id}: missing from board deaths tape")
        order = np.argsort(tape["game_second"].to_numpy(dtype=np.int64), kind="stable")
        tape_seconds = tape["game_second"].to_numpy(dtype=np.int64)[order]
        tape_radiant = tape["deaths_radiant"].to_numpy(dtype=np.float64)[order]
        tape_dire = tape["deaths_dire"].to_numpy(dtype=np.float64)[order]
        targets = key_seconds.loc[part.index].to_numpy(dtype=np.int64) + BOARD_LEAD_SECONDS
        indices = np.clip(
            np.searchsorted(tape_seconds, targets, side="right") - 1, 0, len(order) - 1
        )
        indexer = frame.index.get_indexer(part.index)
        board_radiant[indexer] = tape_radiant[indices]
        board_dire[indexer] = tape_dire[indices]
        age_radiant[indexer] = _capped_tape_death_age_s(tape_seconds, tape_radiant, targets)
        age_dire[indexer] = _capped_tape_death_age_s(tape_seconds, tape_dire, targets)
    pending_radiant = np.clip(
        board_radiant - frame["deaths_radiant"].to_numpy(dtype=np.float64), 0, None
    )
    pending_dire = np.clip(board_dire - frame["deaths_dire"].to_numpy(dtype=np.float64), 0, None)
    return frame.assign(
        deaths_radiant=board_radiant,
        deaths_dire=board_dire,
        pending_deaths_radiant=pending_radiant,
        pending_deaths_dire=pending_dire,
        board_death_age_radiant_s=age_radiant,
        board_death_age_dire_s=age_dire,
    )


def attach_catalog_features(
    frame: pd.DataFrame,
    tape: pd.DataFrame,
    *,
    key_seconds: pd.Series,
    start_second: int,
    board_tape: pd.DataFrame | None,
) -> pd.DataFrame:
    """Attach the 60 tape-derived history columns plus the market transforms and the source-specific scoreboard view.

    The one attach path for both trainers and backtest scripts: the tape is the
    complete per-match artifact, so a lag with no exact-second pivot resolves
    to NaN rather than a zero substitute. start_second is the model's
    HistoryPolicy field — 60 for GRID tapes, -60 for Oddin's. board_tape is the
    per-second tape the board catalog reads deaths, pending, and death age from;
    None keeps the table-clock catalog unchanged.
    """
    enriched = attach_history_features(
        frame,
        tape,
        key_seconds=key_seconds,
        start_second=start_second,
    )
    if board_tape is not None:
        enriched = attach_board_deaths(enriched, board_tape, key_seconds=key_seconds)
    return enriched.assign(**market_derived_columns(enriched))
