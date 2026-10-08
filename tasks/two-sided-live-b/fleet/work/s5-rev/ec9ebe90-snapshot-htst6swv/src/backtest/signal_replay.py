"""Causal model decisions on received table snapshots and scoreboard events."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TypeGuard

import numpy as np

from backtest.feed_schedules import grid_exit_age_seconds, walk_feed_ticks
from backtest.marks import MidSeries, lookup_reference_mid
from shared.utils.board_features import BOARD_REACTION_SECONDS, BoardFeatures, BoardHistory
from shared.utils.dota_features import HistoryPolicy, history_levels
from shared.utils.match_time import NS_PER_SECOND
from strategy.types import KillGateUpdate


@dataclass(frozen=True)
class ReceivedSnapshot:
    received_ns: int
    second: int
    has_features: bool
    levels: dict[str, float]
    terminal: bool
    paused: bool


@dataclass(frozen=True)
class _ReplaySnapshot:
    snapshot: ReceivedSnapshot
    valid: bool
    stale: bool
    history: dict[str, float]


class _EventKind(IntEnum):
    # At equal ns a board receipt is applied before a table receipt, and board decisions come last.
    BOARD_RECEIPT = 0
    TABLE_RECEIPT = 1
    BOARD_DECISION = 2


@dataclass(frozen=True, order=True)
class _ReplayEvent:
    now_ns: int
    kind: _EventKind
    position: int


@dataclass(frozen=True)
class ModelDecision:
    match_id: int
    model_dir: Path
    received_ns: int
    snapshot: ReceivedSnapshot
    history: dict[str, float]
    anchor_p: float
    prior: float
    board: BoardFeatures
    board_tick: bool


@dataclass(frozen=True)
class SignalReplay:
    feed_timestamps_ns: tuple[int, ...]
    decisions: tuple[ModelDecision, ...]


def replay_received_snapshots(
    match_id: int,
    model_dir: Path,
    snapshots: Sequence[ReceivedSnapshot],
    gates: Sequence[KillGateUpdate],
    series: MidSeries,
    history_policy: HistoryPolicy,
    entry_age_seconds: float,
    prior: float,
) -> SignalReplay:
    levels = np.asarray([history_levels(tick.levels) for tick in snapshots], dtype=np.float64)
    received: list[_ReplaySnapshot] = []
    feed_ns: list[int] = []
    for tick in walk_feed_ticks(
        [snapshot.second for snapshot in snapshots],
        levels,
        [not snapshot.terminal for snapshot in snapshots],
        [snapshot.received_ns for snapshot in snapshots],
        grid_exit_age_seconds(entry_age_seconds),
        history_policy,
    ):
        snapshot = snapshots[tick.position]
        received.append(
            _ReplaySnapshot(
                snapshot,
                tick.valid,
                tick.stale,
                tick.derived(snapshot.second, levels[tick.position]),
            )
        )
        if tick.valid:
            feed_ns.append(snapshot.received_ns)

    board = BoardHistory()
    decisions: list[ModelDecision] = []
    latest: _ReplaySnapshot | None = None
    reaction_ns = round(BOARD_REACTION_SECONDS * NS_PER_SECOND)
    events = [
        *[
            _ReplayEvent(tick.snapshot.received_ns, _EventKind.TABLE_RECEIPT, index)
            for index, tick in enumerate(received)
        ],
        *[
            _ReplayEvent(gate.now_ns, _EventKind.BOARD_RECEIPT, index)
            for index, gate in enumerate(gates)
        ],
        *[
            _ReplayEvent(gate.now_ns + reaction_ns, _EventKind.BOARD_DECISION, index)
            for index, gate in enumerate(gates)
        ],
    ]
    for event in sorted(events):
        now_ns, kind, index = event.now_ns, event.kind, event.position
        if kind == _EventKind.BOARD_RECEIPT:
            gate = gates[index].gate
            board.record_board(now_ns, gate.radiant.awaited_deaths, gate.dire.awaited_deaths)
            continue
        is_board = kind == _EventKind.BOARD_DECISION
        if not is_board:
            latest = received[index]
            values = latest.snapshot.levels
            board.record_table(now_ns, int(values["deaths_radiant"]), int(values["deaths_dire"]))
        if not _can_decide(latest, is_board, now_ns, entry_age_seconds):
            continue
        snapshot = latest.snapshot
        anchor = lookup_reference_mid(series, now_ns)
        if anchor is None:
            continue
        values = snapshot.levels
        decisions.append(
            ModelDecision(
                match_id=match_id,
                model_dir=model_dir,
                received_ns=now_ns,
                snapshot=snapshot,
                history=latest.history,
                anchor_p=anchor,
                prior=prior,
                board=board.derive(
                    now_ns, int(values["deaths_radiant"]), int(values["deaths_dire"])
                ),
                board_tick=is_board,
            )
        )
    return SignalReplay(tuple(feed_ns), tuple(decisions))


def _can_decide(
    latest: _ReplaySnapshot | None, is_board: bool, now_ns: int, entry_age_seconds: float
) -> TypeGuard[_ReplaySnapshot]:
    if latest is None or not latest.valid or latest.stale:
        return False
    snapshot = latest.snapshot
    if not snapshot.has_features or snapshot.paused:
        return False
    age_seconds = (now_ns - snapshot.received_ns) / NS_PER_SECOND
    return not is_board or (not snapshot.terminal and age_seconds <= entry_age_seconds)
