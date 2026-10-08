"""Seed-0 Follow300 replay of one map, through the same feed resolution a real run uses.

Nautilus logging inits once per process, so `replay_seed0_identity` runs the
engine in a child. The in-process entry is what that child calls.
"""

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.context import MarketContext, ReplayLookups
from backtest.extraction_identity import (
    ReplayIdentity,
    checks_from_result,
    identity_from_records,
    load_identity_golden,
)
from backtest.feed_schedules import (
    ArchiveJoinInput,
    MatchFeedPlan,
    assert_archive_binding,
    resolve_feed_plans,
    schedule_archive_dirs,
)
from backtest.lol_inputs import NAUTILUS_ZERO_FILL_STOP_REASON
from backtest.report_types import ReplayInstrumentResult
from backtest.results import build_maker_match_results, signal_provenance_map
from backtest.run import (
    BACKTEST_LEVEL_USDC,
    build_kernels,
    load_run_selection,
    resolve_max_position_levels,
    resolve_run_policies,
    resolve_run_signals,
    run_batch,
)
from backtest.signals import BacktestGame
from backtest.telemetry import MakerRecords
from backtest.telonex_local import create_telonex_source_tree

_REPO = Path(__file__).resolve().parents[2]
_CHILD_PYTHONPATH = os.pathsep.join(
    (
        str(_REPO / "src"),
        str(_REPO / "scripts"),
        str(_REPO.parent / "prediction-market-backtesting"),
    )
)


@dataclass(frozen=True)
class _Seed0ReplayInputs:
    model_dir: Path
    lookups: ReplayLookups
    lag_seconds: int
    signal_rows: pd.DataFrame
    capture_root: Path
    archive_join: ArchiveJoinInput


def _load_seed0_replay_inputs(game: BacktestGame, match_id: int) -> _Seed0ReplayInputs:
    """Load selection, lookups, and model path for one seed-0 identity replay."""
    selection = load_run_selection(
        game=game,
        match_id=match_id,
        since_match=None,
        limit=None,
        allowed_event_ids=None,
    )
    if not (selection.model_dir / "model.json").is_file():
        raise FileNotFoundError(selection.model_dir)
    if not selection.capture_root.is_dir():
        raise FileNotFoundError(selection.capture_root)
    lookups = selection.load_lookups(selection.selected_ids)
    return _Seed0ReplayInputs(
        model_dir=selection.model_dir,
        lookups=lookups,
        lag_seconds=selection.lag_seconds,
        signal_rows=selection.signal_rows,
        capture_root=selection.capture_root,
        archive_join=selection.archive_join,
    )


def _identity_from_seed0_batch(
    game: BacktestGame,
    match_id: int,
    context: MarketContext,
    framework_results: Sequence[ReplayInstrumentResult],
    records: MakerRecords,
    plans: Mapping[int, MatchFeedPlan],
) -> ReplayIdentity:
    for result in framework_results:
        if result.get("stop_reason") == NAUTILUS_ZERO_FILL_STOP_REASON:
            raise AssertionError(f"engine_fault: nautilus_zero_fill match_id={match_id}")
    batch_results = build_maker_match_results(
        (context,),
        framework_results,
        records.fills,
        records.quote_events,
        placement="join",
        fill_model="queue",
        uptimes={uptime.match_id: uptime.live_order_seconds for uptime in records.uptimes},
        provenance=signal_provenance_map(plans, (match_id,)),
    )
    if len(batch_results) != 1:
        raise ValueError(f"expected one match result, got {len(batch_results)}")
    return identity_from_records(
        match_id=match_id,
        game=game,
        seed=0,
        scenario="seed0_postcleanup",
        checks=checks_from_result(batch_results[0]),
        fills=records.fills,
        quote_events=records.quote_events,
    )


def replay_seed0_identity_inprocess(
    *, game: BacktestGame, match_id: int, archive_id: str
) -> ReplayIdentity:
    """One Nautilus engine in this process. Caller must isolate: logging inits once."""
    inputs = _load_seed0_replay_inputs(game, match_id)
    context = inputs.lookups.context_by_match.get(match_id)
    if context is None:
        raise FileNotFoundError(f"no market context for match {match_id}")
    feed = resolve_feed_plans(
        inputs.archive_join, (match_id,), None, match_id, model_override_noxp=None
    )
    assert_archive_binding(feed.plans[match_id], archive_id=archive_id)
    signals = resolve_run_signals(
        signal_cadence_seed=0,
        cadence_mean_interval=None,
        game=game,
        plan_match_ids=(match_id,),
        signal_rows=inputs.signal_rows,
        model_dir=inputs.model_dir,
        selection_lag_seconds=inputs.lag_seconds,
        plans=feed.plans,
        mids=inputs.lookups.mids,
    )
    policies = resolve_run_policies(
        level_usdc=BACKTEST_LEVEL_USDC[game],
        match_ids=(match_id,),
        min_abs_delta=None,
        exit_abs_delta=None,
    )
    with create_telonex_source_tree(
        (context,), inputs.capture_root, schedule_archive_dirs(feed.plans)
    ) as source_root:
        try:
            framework_results, records = run_batch(
                game=game,
                contexts=(context,),
                signals=signals,
                pauses_by_match=inputs.lookups.pauses_by_match,
                source_root=source_root,
                plans=feed.plans,
                kernels=build_kernels(
                    contexts=(context,),
                    policies=policies,
                    plans=feed.plans,
                    max_position_levels=resolve_max_position_levels(game, None),
                ),
            )
        except AssertionError as exc:
            text = str(exc)
            if "PositionOpened" in text or "FLAT" in text:
                raise AssertionError(
                    f"engine_fault: nautilus_zero_fill match_id={match_id}: {exc}"
                ) from exc
            raise
    return _identity_from_seed0_batch(
        game, match_id, context, framework_results, records, feed.plans
    )


def replay_seed0_identity(
    *, game: BacktestGame, match_id: int, archive_id: str, report_dir: Path
) -> ReplayIdentity:
    """Seed-0 replay in a child process: Nautilus logging cannot init twice in one pytest."""
    out = report_dir / "identity.json"
    env = os.environ.copy()
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _CHILD_PYTHONPATH if not prior else os.pathsep.join((_CHILD_PYTHONPATH, prior))
    )
    probe = (
        "from pathlib import Path\n"
        "from backtest.seed0_replay import replay_seed0_identity_inprocess\n"
        "from backtest.extraction_identity import write_identity_json\n"
        "identity = replay_seed0_identity_inprocess("
        f"game={game!r}, match_id={match_id}, archive_id={archive_id!r})\n"
        f"write_identity_json(path=Path({str(out)!r}), identity=identity)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return load_identity_golden(out)
