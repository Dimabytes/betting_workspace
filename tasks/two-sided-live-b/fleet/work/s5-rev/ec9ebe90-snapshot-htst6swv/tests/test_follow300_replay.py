"""Current Follow300 policy smoke: eight seed-0 maps on the published model catalogs."""

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from backtest.extraction_identity import first_tape_mismatch, load_identity_golden
from backtest.feed_schedules import GridV1Plan, assert_archive_binding
from backtest.replay_inputs import (
    SMOKE_INPUTS_FILENAME,
    SMOKE_MAPS,
    GridFeedInputs,
    ModelInputs,
    ScheduleFeedInputs,
    SmokeInputs,
    SmokeMap,
    collect_smoke_inputs,
    inputs_drift,
    load_smoke_inputs,
)
from backtest.seed0_replay import replay_seed0_identity
from shared.utils.json_io import write_json

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "follow300_changes" / "smoke"


def _smoke_path(smoke: SmokeMap) -> Path:
    return FIXTURES / f"{smoke.game}_{smoke.match_id}_seed0.json"


def _drift_note() -> str:
    """Which recorded input moved since the capture; only read on a red tape."""
    drift = inputs_drift(
        collect_smoke_inputs(), load_smoke_inputs(FIXTURES / SMOKE_INPUTS_FILENAME)
    )
    if not drift:
        return "inputs unchanged: the strategy code moved"
    return f"inputs changed: {', '.join(drift)}"


@pytest.mark.parametrize(
    "smoke",
    SMOKE_MAPS,
    ids=lambda smoke: f"{smoke.game}_{smoke.match_id}",
)
def test_seed0_map_replay_matches_current_policy_smoke(smoke: SmokeMap, tmp_path: Path) -> None:
    actual = replay_seed0_identity(
        game=smoke.game,
        match_id=smoke.match_id,
        archive_id=smoke.archive_id,
        report_dir=tmp_path,
    )
    expected = load_identity_golden(_smoke_path(smoke))
    assert actual.fills
    assert actual.place_cancel
    mismatch = first_tape_mismatch(actual, expected)
    assert mismatch == "", f"{mismatch}\n{_drift_note()}"


def _grid() -> GridFeedInputs:
    return GridFeedInputs(
        kind="grid_v1",
        match_id=8837869969,
        game="dota",
        model=ModelInputs(name="20260921T085651Z", sha256="a"),
        signals_sha256="b",
    )


def _schedule() -> ScheduleFeedInputs:
    return ScheduleFeedInputs(
        kind="schedule",
        match_id=9007208887,
        game="dota",
        model=ModelInputs(name="20260919T182833Z", sha256="e"),
        features_sha256="c",
        schedule_sha256="d",
    )


def test_inputs_drift_names_every_moved_fingerprint() -> None:
    """Drift names changed fields and a map that is present on only one side."""
    grid = _grid()
    schedule = _schedule()
    current = SmokeInputs(strategy_constants_sha256="s", feeds=(grid, schedule))
    assert inputs_drift(current, current) == ()

    changed = SmokeInputs(
        strategy_constants_sha256="s",
        feeds=(
            replace(grid, model=replace(grid.model, name="20260915T105750Z")),
            replace(schedule, schedule_sha256="rebuilt"),
        ),
    )
    assert inputs_drift(current, changed) == (
        "dota.8837869969.model.name",
        "dota.9007208887.schedule_sha256",
    )
    assert inputs_drift(current, SmokeInputs(strategy_constants_sha256="s", feeds=(grid,))) == (
        "dota.9007208887",
    )


def test_load_smoke_inputs_roundtrips_and_names_a_missing_field(tmp_path: Path) -> None:
    current = SmokeInputs(strategy_constants_sha256="s", feeds=(_grid(), _schedule()))
    path = tmp_path / "inputs.json"
    write_json(path, asdict(current))
    assert load_smoke_inputs(path) == current

    write_json(path, {"feeds": []})
    with pytest.raises(ValueError, match="strategy_constants_sha256"):
        load_smoke_inputs(path)


def test_assert_archive_binding_rejects_a_grid_plan_for_an_archive_map() -> None:
    plan = GridV1Plan(match_id=9007208887, model_dir=Path("research"))
    with pytest.raises(AssertionError, match="grid-v1"):
        assert_archive_binding(plan, archive_id="9007208887")
