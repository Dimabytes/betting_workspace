"""Step 3A: per-match feed plans, schedule bindings, and schedule-driven signals."""

# pyright: reportPrivateUsage=false

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from catalog_fixtures import catalog_row
from feed_schedule_fixtures import (
    ArchiveLayout,
    build_dota_catalog,
    build_index_row,
    build_schedule,
    build_schedule_plan,
    build_tick,
)
from feed_schedule_fixtures import layout as layout
from test_backtest_maker import build_maker_config
from test_backtest_signals import build_signal_frame

from archive_index.schedule import ScheduleKillGate, ScheduleKillWait
from backtest.context import MarketContext
from backtest.feed_schedules import (
    GridV1Plan,
    ScheduleBinding,
    SchedulePlan,
    entry_stale_seconds,
    resolve_dota_feed_plans,
    resolve_lol_feed_plans,
    schedule_map_sha256,
)
from backtest.marks import MidSeries
from backtest.run import build_strategy_configs
from backtest.signals import (
    DatasetReadinessError,
    MatchSignals,
    SignalTiming,
    build_match_signals,
    build_schedule_match_signals,
    find_signal_asof,
    require_catalog_features,
)
from backtest.strategy import DotaMakerStrategy, MatchKernelConfig
from shared.constants.lol import LOL_RESEARCH_MODEL_DIR
from shared.constants.paths import RESEARCH_MODEL_DIR, RESEARCH_NOXP_MODEL_DIR
from shared.constants.strategy import (
    BACKTEST_DOTA_MAX_POSITION_LEVELS,
    EXIT_FEED_STALE_SECONDS,
    GRID_FEED_STALE_SECONDS,
    MIN_ORDER_SIZE,
    QUOTE_GRID,
)
from shared.types.dataset import DotaGameFeatureRow
from shared.utils.board_features import BOARD_REACTION_SECONDS
from shared.utils.dota_features import (
    BOARD_DEATH_AGE_CAP_SECONDS,
    DOTA_HISTORY_FEATURE_NAMES,
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
)
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row
from shared.utils.telonex_book import MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE
from strategy.policy import follow300_policy
from strategy.types import FreshnessLimits, MarketLimits
from trader.oddin_feed import ODDIN_FEED_STALE_SECONDS

NS = 1_000_000_000


def test_linked_admitted_archive_binds_a_schedule_plan(
    layout: ArchiveLayout,
) -> None:
    """A linked+admitted index row resolves to a schedule plan with real paths."""
    schedule = build_schedule("grid-a1", 7, [build_tick(0, 5, 100), build_tick(1, 8, 104)])
    layout.publish(schedule)
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-grid-a1",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    plan = result.plans[7]
    assert result.exclusions == {}
    assert isinstance(plan, SchedulePlan)
    binding = plan.binding
    assert binding.archive_dir == layout.trader / "grid-a1"
    assert binding.schedule_fingerprint == "fp-grid-a1"
    assert [tick.game_second for tick in binding.schedule.ticks] == [5, 8]
    assert plan.model_dir == RESEARCH_MODEL_DIR


def test_oddin_binding_selects_noxp_model_and_oddin_stale(
    layout: ArchiveLayout,
) -> None:
    """An Oddin-sourced archive predicts with research-noxp and the 15s budget."""
    schedule = build_schedule("oddin-b2", 8, [build_tick(0, 5, 100)], feed_source="oddin")
    layout.publish(schedule)
    index = pd.DataFrame(
        [
            build_index_row(
                "oddin-b2",
                feed_source="oddin",
                steam_match_id="8",
                condition_id="cond-8",
                schedule_fingerprint="fp-oddin-b2",
            )
        ]
    )
    catalog = build_dota_catalog(
        8,
        condition_id="cond-8",
        archive_id="oddin-b2",
        archive_root="trader",
        schedule_fingerprint="fp-oddin-b2",
    )

    result = resolve_dota_feed_plans((8,), catalog, index, None, model_override_noxp=None)

    plan = result.plans[8]
    assert isinstance(plan, SchedulePlan)
    assert plan.model_dir == RESEARCH_NOXP_MODEL_DIR
    assert entry_stale_seconds(plan.binding) == ODDIN_FEED_STALE_SECONDS


def test_match_without_archive_keeps_grid_v1(layout: ArchiveLayout) -> None:
    """No index rows near the match: seeded grid-v1 cadence and the base model."""
    index = pd.DataFrame([build_index_row("grid-zz", steam_match_id="99", condition_id="cond-99")])
    catalog = build_dota_catalog(7, condition_id="cond-7")

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    plan = result.plans[7]
    assert result.exclusions == {}
    assert isinstance(plan, GridV1Plan)
    assert plan.model_dir == RESEARCH_MODEL_DIR


def test_unlinked_admitted_archive_excludes_the_match(layout: ArchiveLayout) -> None:
    """An admitted archive naming this match but unlinked in the catalog excludes."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(7, condition_id="cond-7")

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert 7 not in result.plans
    assert result.exclusions == {7: "archive_unlinked"}


def test_linked_non_admitted_archive_excludes_with_reason(layout: ArchiveLayout) -> None:
    """A linked archive that failed admission excludes with its stable reason."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                admission="excluded:incomplete_feed",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-grid-a1",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "archive:excluded:incomplete_feed"}


def test_unlinked_non_admitted_candidate_excludes_with_reason(
    layout: ArchiveLayout,
) -> None:
    """Nearby archives that all failed admission exclude with the first reason."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                admission="excluded:low_coverage",
                steam_match_id="7",
                condition_id="cond-7",
            )
        ]
    )
    catalog = build_dota_catalog(7, condition_id="cond-7")

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "archive:excluded:low_coverage"}


def test_stale_catalog_fingerprint_excludes(layout: ArchiveLayout) -> None:
    """Catalog fingerprint that trails the index marks the link stale."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-new",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-old",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "schedule_stale"}


def test_missing_schedule_file_excludes(layout: ArchiveLayout) -> None:
    """An admitted index row without its schedule JSON is schedule_missing."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-grid-a1",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "schedule_missing"}


def test_index_file_fingerprint_mismatch_excludes(layout: ArchiveLayout) -> None:
    """A schedule file that no longer matches the index fingerprint is corrupt."""
    schedule = build_schedule("grid-a1", 7, [build_tick(0, 5, 100)], fingerprint="fp-other")
    layout.publish(schedule)
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-grid-a1",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "schedule_fingerprint_mismatch"}


def test_schedule_identity_mismatch_excludes(layout: ArchiveLayout) -> None:
    """A schedule bound to a different steam match id excludes, not crashes."""
    schedule = build_schedule("grid-a1", 7, [build_tick(0, 5, 100)], steam_match_id="999")
    layout.publish(schedule)
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="999",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            )
        ]
    )
    catalog = build_dota_catalog(
        7,
        condition_id="cond-7",
        archive_id="grid-a1",
        archive_root="trader",
        schedule_fingerprint="fp-grid-a1",
    )

    result = resolve_dota_feed_plans((7,), catalog, index, None, model_override_noxp=None)

    assert result.exclusions == {7: "schedule_identity_mismatch"}


def test_lol_schedule_identity_mismatch_excludes(layout: ArchiveLayout) -> None:
    """A LoL schedule whose condition_id differs from the audit row excludes."""
    schedule = build_schedule(
        "grid-l1", 21, [build_tick(0, 5, 100)], condition_id="0xZZ", game="lol"
    )
    layout.publish(schedule, game="lol")
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-l1",
                game="lol",
                condition_id="0xc1",
                schedule_fingerprint="fp-grid-l1",
            )
        ]
    )
    audit = pd.DataFrame([{"match_id": 21, "condition_id": "0xC1"}])

    result = resolve_lol_feed_plans((21,), audit, index, None)

    assert result.exclusions == {21: "schedule_identity_mismatch"}


def test_model_override_sends_oddin_to_the_noxp_catalog(layout: ArchiveLayout) -> None:
    """GRID and grid-v1 take the XP catalog. Oddin takes the no-XP catalog."""
    grid = build_schedule("grid-a1", 7, [build_tick(0, 5, 100)])
    oddin = build_schedule("oddin-b2", 9, [build_tick(0, 5, 100)], feed_source="oddin")
    layout.publish(grid)
    layout.publish(oddin)
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            ),
            build_index_row(
                "oddin-b2",
                feed_source="oddin",
                steam_match_id="9",
                condition_id="cond-9",
                schedule_fingerprint="fp-oddin-b2",
            ),
        ]
    )
    catalog = MatchCatalog(
        {
            7: catalog_entry_from_row(
                catalog_row(
                    7,
                    condition_id="cond-7",
                    archive_id="grid-a1",
                    archive_root="trader",
                    schedule_fingerprint="fp-grid-a1",
                )
            ),
            8: catalog_entry_from_row(catalog_row(8, condition_id="cond-8")),
            9: catalog_entry_from_row(
                catalog_row(
                    9,
                    condition_id="cond-9",
                    archive_id="oddin-b2",
                    archive_root="trader",
                    schedule_fingerprint="fp-oddin-b2",
                )
            ),
        }
    )
    xp = Path("/models/experiment")
    noxp = Path("/models/experiment-noxp")

    with pytest.raises(ValueError, match="no-XP"):
        resolve_dota_feed_plans((7,), catalog, index, xp, model_override_noxp=None)

    result = resolve_dota_feed_plans((7, 8, 9), catalog, index, xp, model_override_noxp=noxp)

    assert result.plans[7].model_dir == xp
    assert result.plans[8].model_dir == xp
    assert result.plans[9].model_dir == noxp


def test_require_catalog_features_rejects_a_swapped_catalog(tmp_path: Path) -> None:
    """An XP catalog cannot stand in for the Oddin no-XP catalog."""
    catalog = tmp_path / "model"
    catalog.mkdir()
    (catalog / "model.json").write_text(
        json.dumps({"features": DOTA_XP_FEATURE_COLUMNS}), encoding="utf-8"
    )

    require_catalog_features(catalog, DOTA_XP_FEATURE_COLUMNS, "--model-dir")
    with pytest.raises(ValueError, match="model-dir-noxp"):
        require_catalog_features(catalog, DOTA_NOXP_FEATURE_COLUMNS, "--model-dir-noxp")


def _write_historyless_catalog(path: Path, *, xp: bool) -> Path:
    """A model.json with the old research feature list: zero history columns."""
    path.mkdir()
    features = [
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
        "market_p_radiant",
    ]
    if not xp:
        features.remove("radiant_xp_adv")
    assert DOTA_HISTORY_FEATURE_NAMES.isdisjoint(features)
    (path / "model.json").write_text(json.dumps({"features": features}), encoding="utf-8")
    return path


def test_require_catalog_features_rejects_historyless_catalogs_on_both_xp_sides(
    tmp_path: Path,
) -> None:
    """Neither old 12-column XP nor 11-column no-XP catalogs satisfy the current contract."""
    xp = _write_historyless_catalog(tmp_path / "oldcat12", xp=True)
    noxp = _write_historyless_catalog(tmp_path / "oldcat11", xp=False)

    with pytest.raises(ValueError, match="81-column"):
        require_catalog_features(xp, DOTA_XP_FEATURE_COLUMNS, "--model-dir")
    with pytest.raises(ValueError, match="70-column"):
        require_catalog_features(noxp, DOTA_NOXP_FEATURE_COLUMNS, "--model-dir-noxp")
    with pytest.raises(ValueError, match="model-dir-noxp"):
        require_catalog_features(xp, DOTA_NOXP_FEATURE_COLUMNS, "--model-dir-noxp")
    with pytest.raises(ValueError, match="--model-dir is"):
        require_catalog_features(noxp, DOTA_XP_FEATURE_COLUMNS, "--model-dir")


def test_require_catalog_features_still_rejects_a_partial_history_catalog(
    tmp_path: Path,
) -> None:
    """A catalog with some but not all history columns is neither exact nor historyless."""
    catalog = tmp_path / "partial"
    catalog.mkdir()
    features = list(DOTA_XP_FEATURE_COLUMNS[:20])
    assert not DOTA_HISTORY_FEATURE_NAMES.isdisjoint(features)
    (catalog / "model.json").write_text(json.dumps({"features": features}), encoding="utf-8")

    with pytest.raises(ValueError, match="--model-dir is"):
        require_catalog_features(catalog, DOTA_XP_FEATURE_COLUMNS, "--model-dir")


def test_lol_plan_binds_by_audit_condition_id(layout: ArchiveLayout) -> None:
    """LoL joins on audit condition_id and predicts with the LoL research model."""
    schedule = build_schedule(
        "grid-l1", 21, [build_tick(0, 5, 100)], condition_id="0xC1", game="lol"
    )
    layout.publish(schedule, game="lol")
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-l1",
                game="lol",
                condition_id="0xc1",
                schedule_fingerprint="fp-grid-l1",
            )
        ]
    )
    audit = pd.DataFrame([{"match_id": 21, "condition_id": "0xC1"}])

    result = resolve_lol_feed_plans((21,), audit, index, None)

    plan = result.plans[21]
    assert isinstance(plan, SchedulePlan)
    assert plan.model_dir == LOL_RESEARCH_MODEL_DIR
    assert plan.binding.archive_dir == layout.trader / "grid-l1"


def test_lol_duplicate_archives_do_not_block_admission(layout: ArchiveLayout) -> None:
    """duplicate_of: rows are skipped so the admitted copy can bind."""
    schedule = build_schedule(
        "grid-l1", 21, [build_tick(0, 5, 100)], condition_id="0xC1", game="lol"
    )
    layout.publish(schedule, game="lol")
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-l0", game="lol", admission="duplicate_of:grid-l1", condition_id="0xc1"
            ),
            build_index_row(
                "grid-l1",
                game="lol",
                condition_id="0xc1",
                schedule_fingerprint="fp-grid-l1",
            ),
        ]
    )
    audit = pd.DataFrame([{"match_id": 21, "condition_id": "0xC1"}])

    result = resolve_lol_feed_plans((21,), audit, index, None)

    assert isinstance(result.plans[21], SchedulePlan)
    assert result.exclusions == {}


def test_lol_without_candidate_keeps_grid_v1(layout: ArchiveLayout) -> None:
    """No index row for the condition: grid-v1 with the LoL model."""
    index = pd.DataFrame([build_index_row("grid-l9", game="lol", condition_id="0xOTHER")])
    audit = pd.DataFrame([{"match_id": 21, "condition_id": "0xC1"}])

    result = resolve_lol_feed_plans((21,), audit, index, None)

    plan = result.plans[21]
    assert isinstance(plan, GridV1Plan)
    assert plan.model_dir == LOL_RESEARCH_MODEL_DIR


def test_lol_non_admitted_candidate_excludes(layout: ArchiveLayout) -> None:
    """A LoL archive that failed admission excludes with its reason."""
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-l1",
                game="lol",
                admission="excluded:no_schedule",
                condition_id="0xc1",
            )
        ]
    )
    audit = pd.DataFrame([{"match_id": 21, "condition_id": "0xC1"}])

    result = resolve_lol_feed_plans((21,), audit, index, None)

    assert result.exclusions == {21: "archive:excluded:no_schedule"}


def test_schedule_map_sha256_tracks_mode_and_fingerprint_not_model(tmp_path: Path) -> None:
    """The digest moves on mode/fingerprint changes; model dirs hash separately."""
    binding = ScheduleBinding(
        archive_root="trader",
        archive_id="grid-a1",
        feed_source="grid",
        archive_dir=tmp_path / "grid-a1",
        schedule_path=tmp_path / "s.json",
        schedule_fingerprint="fp-1",
        schedule=build_schedule("grid-a1", 7, [build_tick(0, 5, 100)]),
    )
    schedule_plan = SchedulePlan(match_id=7, binding=binding, model_dir=RESEARCH_MODEL_DIR)
    grid_plan = GridV1Plan(match_id=8, model_dir=RESEARCH_MODEL_DIR)
    base = schedule_map_sha256({7: schedule_plan, 8: grid_plan})

    assert schedule_map_sha256({7: schedule_plan, 8: grid_plan}) == base
    changed_model = schedule_map_sha256(
        {
            7: SchedulePlan(
                match_id=7,
                binding=binding,
                model_dir=RESEARCH_NOXP_MODEL_DIR,
            ),
            8: GridV1Plan(match_id=8, model_dir=RESEARCH_NOXP_MODEL_DIR),
        }
    )
    assert changed_model == base
    changed_mode = schedule_map_sha256(
        {
            7: GridV1Plan(match_id=7, model_dir=RESEARCH_MODEL_DIR),
            8: grid_plan,
        }
    )
    assert changed_mode != base
    other_binding = ScheduleBinding(
        archive_root="trader",
        archive_id="grid-a1",
        feed_source="grid",
        archive_dir=tmp_path / "grid-a1",
        schedule_path=tmp_path / "s.json",
        schedule_fingerprint="fp-2",
        schedule=binding.schedule,
    )
    changed_fp = schedule_map_sha256(
        {
            7: SchedulePlan(
                match_id=7,
                binding=other_binding,
                model_dir=RESEARCH_MODEL_DIR,
            ),
            8: grid_plan,
        }
    )
    assert changed_fp != base


def test_entry_stale_seconds_by_feed_source(tmp_path: Path) -> None:
    """Grid binds the 16s budget; Oddin the 15s one."""
    base = ScheduleBinding(
        archive_root="trader",
        archive_id="a",
        feed_source="grid",
        archive_dir=tmp_path,
        schedule_path=tmp_path / "s.json",
        schedule_fingerprint="fp",
        schedule=build_schedule("a", 1, [build_tick(0, 0, 1)]),
    )
    assert entry_stale_seconds(base) == GRID_FEED_STALE_SECONDS
    oddin = ScheduleBinding(
        archive_root="trader",
        archive_id="b",
        feed_source="oddin",
        archive_dir=tmp_path,
        schedule_path=tmp_path / "s.json",
        schedule_fingerprint="fp",
        schedule=build_schedule("b", 1, [build_tick(0, 0, 1)]),
    )
    assert entry_stale_seconds(oddin) == ODDIN_FEED_STALE_SECONDS


def _model_dir(tmp_path: Path, name: str, features: list[str] | None = None) -> Path:
    """Write a catalog dir whose model.json lists the training feature order."""
    directory = tmp_path / name
    directory.mkdir()
    (directory / "member_00.txt").write_text("unused")
    (directory / "model.json").write_text(
        json.dumps(
            {
                "features": list(DOTA_XP_FEATURE_COLUMNS if features is None else features),
                "members": ["member_00.txt"],
                "member_trees": [1],
            }
        )
    )
    return directory


def _feature_frame(match_id: int, seconds: tuple[int, ...]) -> pd.DataFrame:
    """game_features.parquet-shaped rows: game_second keys plus model inputs."""
    return pd.DataFrame(
        [
            DotaGameFeatureRow(
                match_id=match_id,
                game_second=second,
                radiant_nw_adv=second,
                radiant_nw=0,
                dire_nw=0,
                radiant_xp_adv=0,
                deaths_radiant=0,
                deaths_dire=0,
                top1_nw_adv=0,
                radiant_top1_nw_ratio=0.0,
                dire_top1_nw_ratio=0.0,
                top3_nw_adv=0,
                radiant_top3_nw_ratio=0.0,
                dire_top3_nw_ratio=0.0,
                market_radiant_prior=0.11,
            )
            for second in seconds
        ]
    )


def _fake_predictor(monkeypatch: pytest.MonkeyPatch, delta: float) -> list[dict[str, Any]]:
    """Patch predict_model_deltas; record every (model_dir, features) call."""
    calls: list[dict[str, Any]] = []

    def fake_predict(model_dir: Path, feature_columns: Any, features: pd.DataFrame) -> list[float]:
        calls.append(
            {
                "model_dir": model_dir,
                "columns": list(feature_columns),
                "features": features.copy(),
            }
        )
        return [delta] * len(features)

    monkeypatch.setattr("backtest.signals.predict_model_deltas", fake_predict)
    return calls


def test_schedule_signals_use_causal_anchor_and_tick_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decisions fire at tick received_ns with the last mid at/before arrival."""
    calls = _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 5, 100, radiant_nw_adv=111),
            build_tick(1, 8, 115, radiant_nw_adv=222),
            build_tick(2, 9, 130, radiant_nw_adv=333),
        ],
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 8, 9))
    mids = MidSeries(
        timestamps_ns=(105 * NS, 140 * NS),
        market_ps=(0.55, 0.70),
    )

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    # Tick 0 has no earlier anchor and stays feed-only; ticks 1-2 decide.
    assert signals.timestamps_ns == (115 * NS, 130 * NS)
    assert signals.dataset_market_ps == (0.55, 0.55)
    assert signals.predicted_deltas == (0.02, 0.02)
    features_seen = pd.concat(call["features"] for call in calls)
    assert features_seen["market_p_radiant"].tolist() == [0.55, 0.55]
    # The dataset prior stays a feature; it never stands in for the anchor.
    assert features_seen["market_radiant_prior"].tolist() == [0.11, 0.11]
    assert features_seen["second"].tolist() == [8, 9]
    # Game state is the tick snapshot, not the livestats row at the label second.
    assert features_seen["radiant_nw_adv"].tolist() == [222, 333]


def test_schedule_signals_missing_feature_rows_is_readiness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bound match absent from game_features fails the dataset, not the match."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule("grid-m1", 1, [build_tick(0, 5, 100), build_tick(1, 7, 110)])
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(2, (5,))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    with pytest.raises(DatasetReadinessError, match="no feature rows"):
        build_schedule_match_signals({1: plan}, features, {1: mids})


def test_schedule_signals_skip_ticks_outside_feature_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warmup and post-game ticks can never hold features; they stay feed-only."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, -84, 100),
            build_tick(1, 5, 130),
            build_tick(2, 7, 135),
            build_tick(3, 9, 140),
            build_tick(4, 2138, 145),
        ],
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 9))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    assert signals.feed_timestamps_ns == (100 * NS, 130 * NS, 135 * NS, 140 * NS, 145 * NS)
    assert signals.timestamps_ns == (130 * NS, 140 * NS)
    assert signals.predicted_deltas == (0.02, 0.02)


def test_schedule_signals_preserve_the_feed_tape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """feed_timestamps_ns mirrors every schedule tick, decided or not."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 5, 100),
            build_tick(1, 6, 105, paused=True),
            build_tick(2, 9, 130, terminal=True),
        ],
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 6, 9))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    assert signals.feed_timestamps_ns == (100 * NS, 105 * NS, 130 * NS)


def test_strategy_configs_bind_observed_clock_frombuild_schedule_plan(tmp_path: Path) -> None:
    """A schedule plan hands the strategy the tick clock, not the LoL grid-v1 lag."""
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 5, 100),
            build_tick(1, 6, 105, paused=True),
            build_tick(2, 9, 130, terminal=True),
        ],
    )
    plan = build_schedule_plan(1, schedule, _model_dir(tmp_path, "research"), tmp_path)
    signals = MatchSignals(
        feed_timestamps_ns=tuple(tick.received_ns for tick in schedule.ticks),
        timestamps_ns=(),
        source_timestamps_ns=(),
        predicted_deltas=(),
        dataset_market_ps=(),
        deaths_radiant=(),
        deaths_dire=(),
        kill_gates=(),
        board_tick_ns=(),
    )
    horn = datetime(2026, 9, 20, 10, tzinfo=UTC)
    context = MarketContext(
        match_id=1,
        condition_id="cond-1",
        event_id="e",
        market_slug="m",
        token_ids=("111", "222"),
        radiant_token_index=0,
        radiant_win=True,
        seconds_delay=0,
        horn_at=horn,
        game_ended_at=horn,
        market_closed_at=horn,
        replay_start=horn,
        replay_end=horn,
        clock_end=horn,
    )
    kernel = MatchKernelConfig(
        policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        limits=MarketLimits(
            min_order_size=MIN_ORDER_SIZE,
            tick_size=QUOTE_GRID,
            pair_sum_tolerance=PAIR_SUM_TOLERANCE,
            radiant_token_index=0,
        ),
        freshness=FreshnessLimits(
            book_stale_s=MAX_BOOK_AGE_SECONDS,
            entry_stale_s=GRID_FEED_STALE_SECONDS,
            exit_stale_s=EXIT_FEED_STALE_SECONDS,
        ),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
    )

    configs = build_strategy_configs(
        game="lol",
        contexts=(context,),
        signals={1: signals},
        pauses_by_match={1: []},
        order_latency_ns=0,
        cancel_latency_ns=0,
        kernels={1: kernel},
        plans={1: plan},
    )

    config = configs[0]["config"]
    tape = config["observed_clock"]
    assert tape is not None
    assert tape.game_seconds == (5, 6, 9)
    assert tape.paused == (False, True, False)
    assert tape.terminal == (False, False, True)
    # No tick reaches the 540s cutoff, so the cutoff clamps to the last tick.
    assert config["buy_cutoff_ns"] == 130 * NS
    # The terminal tick's arrival, not the catalog's game_ended_at, ends the game.
    assert config["game_end_ns"] == 130 * NS


def test_lol_schedule_signals_replay_tick_tape_into_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lol-map catalog receives real history columns replayed from schedule ticks."""
    calls = _fake_predictor(monkeypatch, 0.02)
    model_dir = tmp_path / "lol-research"
    model_dir.mkdir()
    (model_dir / "member_00.txt").write_text("unused")
    (model_dir / "model.json").write_text(
        json.dumps(
            {
                "features": list(DOTA_XP_FEATURE_COLUMNS),
                "members": ["member_00.txt"],
                "member_trees": [1],
            }
        )
    )
    seconds = tuple(range(60, 361, 60))
    ticks = [
        build_tick(index, second, 100 + index, radiant_nw_adv=second * 10)
        for index, second in enumerate(seconds)
    ]
    schedule = build_schedule("grid-l1", 1, ticks, game="lol")
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, seconds)
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    build_schedule_match_signals({1: plan}, features, {1: mids})

    seen = pd.concat(call["features"] for call in calls)
    assert list(seen.columns) == list(DOTA_XP_FEATURE_COLUMNS)
    row = seen.loc[seen["second"] == 360].iloc[0]
    assert row["game_total_5m_radiant_nw_adv"] == pytest.approx(3000)
    assert row["game_change_1m_radiant_nw_adv"] == pytest.approx(600)
    assert row["logit_market_p_radiant"] == pytest.approx(0.0)
    assert row["market_vs_prior"] == pytest.approx(0.5 - 0.11)


def test_schedule_signals_drop_recovery_stale_ticks_from_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connect gap arms nothing: the second tick is the first watchdog
    consume and decides however late it arrives; a later >45s gap is feed-only."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 5, 100),
            build_tick(1, 60, 200),
            build_tick(2, 120, 246),
        ],
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 60, 120))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    assert signals.feed_timestamps_ns == (100 * NS, 200 * NS, 246 * NS)
    assert signals.timestamps_ns == (200 * NS,)


def test_schedule_history_gap_tick_does_not_clear_the_prior_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A history-invalid tick leaves the feed, so a book time past its arrival
    still resolves the previous decision — the strategy never syncs a clear."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 60, 100),
            build_tick(1, 120, 110),
            build_tick(2, 500, 200),
        ],
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (60, 120, 500))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    assert signals.feed_timestamps_ns == (100 * NS, 110 * NS)
    assert signals.timestamps_ns == (110 * NS,)
    strategy = DotaMakerStrategy(
        build_maker_config(
            signal_timestamps_ns=signals.timestamps_ns,
            predicted_deltas=signals.predicted_deltas,
            dataset_market_ps=signals.dataset_market_ps,
            deaths_radiant=signals.deaths_radiant,
            deaths_dire=signals.deaths_dire,
            feed_timestamps_ns=signals.feed_timestamps_ns,
        )
    )
    strategy._sync_signal(110 * NS)
    assert strategy._core.signal is not None
    strategy._sync_signal(250 * NS)
    assert strategy._core.signal is not None


def test_schedule_signals_batch_predictions_per_model_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two models produce two predict calls, each with its own model_dir."""
    calls = _fake_predictor(monkeypatch, 0.02)
    research = _model_dir(tmp_path, "research")
    noxp = _model_dir(tmp_path, "research-noxp")
    ticks = [build_tick(0, 5, 100), build_tick(1, 8, 105)]
    first = build_schedule("grid-m1", 1, ticks)
    second = build_schedule("oddin-m2", 2, ticks, feed_source="oddin")
    plans = {
        1: build_schedule_plan(1, first, research, tmp_path),
        2: build_schedule_plan(2, second, noxp, tmp_path, feed_source="oddin"),
    }
    features = pd.concat([_feature_frame(1, (5, 8)), _feature_frame(2, (5, 8))])
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals(plans, features, {1: mids, 2: mids})

    assert sorted(call["model_dir"].name for call in calls) == [
        "research",
        "research-noxp",
    ]
    assert signals[1].timestamps_ns == (105 * NS,)
    assert signals[2].timestamps_ns == (105 * NS,)


def test_schedule_signals_reject_decreasing_received_ns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-monotone schedule is corrupt data, not a reorder opportunity."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    schedule = build_schedule("grid-m1", 1, [build_tick(0, 5, 200), build_tick(1, 8, 100)])
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 8))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    with pytest.raises(ValueError, match="not non-decreasing"):
        build_schedule_match_signals({1: plan}, features, {1: mids})


def test_schedule_signals_carry_kill_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schedule kill markers reach the strategy as KillGateUpdate events."""
    _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "research")
    gate = ScheduleKillGate(
        received_ns=110 * NS,
        radiant=ScheduleKillWait(awaited_deaths=4, until_ns=110 * NS),
        dire=ScheduleKillWait(awaited_deaths=3, until_ns=120 * NS),
    )
    schedule = build_schedule(
        "grid-m1",
        1,
        [build_tick(0, 5, 100), build_tick(1, 8, 115)],
        kill_gates=(gate,),
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (5, 8))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    assert len(signals.kill_gates) == 1
    update = signals.kill_gates[0]
    assert update.now_ns == 110 * NS
    assert update.gate.dire.awaited_deaths == 3
    assert update.gate.dire.until_ns == 120 * NS
    assert update.gate.radiant.awaited_deaths == 4
    assert update.gate.radiant.until_ns == 110 * NS


def test_schedule_board_ticks_add_decision_and_pending_deaths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted kill gate emits a decision at receipt + reaction; board-
    implied deaths and the pending gap ride the model rows until the table
    catches up."""
    calls = _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "board", features=list(DOTA_XP_FEATURE_COLUMNS))
    gate = ScheduleKillGate(
        received_ns=110 * NS,
        radiant=ScheduleKillWait(awaited_deaths=1, until_ns=120 * NS),
        dire=ScheduleKillWait(awaited_deaths=0, until_ns=110 * NS),
    )
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 3, 90),
            build_tick(1, 5, 100),
            build_tick(2, 8, 115),
            build_tick(3, 20, 130, deaths_radiant=1),
        ],
        kill_gates=(gate,),
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (3, 5, 8, 20))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    signals = build_schedule_match_signals({1: plan}, features, {1: mids})[1]

    board_ns = 110 * NS + round(BOARD_REACTION_SECONDS * NS)
    assert signals.board_tick_ns == (board_ns,)
    # The first tick connects; the board decision snapshots the received second-5
    # state and sees the kill the table prints at second 20.
    assert signals.timestamps_ns == (100 * NS, board_ns, 115 * NS, 130 * NS)
    # The strategy signal reports each tick's table deaths: the gate holds
    # until the kill lands at second 20, board-implied counts stay model-only.
    assert signals.deaths_radiant == (0, 0, 0, 1)
    frame = calls[0]["features"]
    assert frame["deaths_radiant"].tolist() == [0, 1, 1, 1]
    assert frame["pending_deaths_radiant"].tolist() == [0, 1, 1, 0]
    assert frame["second"].tolist() == [5, 5, 8, 20]


def test_schedule_board_ticks_age_from_the_kill_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Death age is seconds since the gate receipt: the board tick is one
    reaction later, and a later table tick keeps counting after pending is 0."""
    calls = _fake_predictor(monkeypatch, 0.02)
    model_dir = _model_dir(tmp_path, "board-age", features=list(DOTA_XP_FEATURE_COLUMNS))
    gate = ScheduleKillGate(
        received_ns=110 * NS,
        radiant=ScheduleKillWait(awaited_deaths=1, until_ns=120 * NS),
        dire=ScheduleKillWait(awaited_deaths=0, until_ns=110 * NS),
    )
    schedule = build_schedule(
        "grid-m1",
        1,
        [
            build_tick(0, 3, 90),
            build_tick(1, 5, 100),
            build_tick(2, 8, 115),
            build_tick(3, 20, 130, deaths_radiant=1),
        ],
        kill_gates=(gate,),
    )
    plan = build_schedule_plan(1, schedule, model_dir, tmp_path)
    features = _feature_frame(1, (3, 5, 8, 20))
    mids = MidSeries(timestamps_ns=(50 * NS,), market_ps=(0.5,))

    build_schedule_match_signals({1: plan}, features, {1: mids})

    board_ns = 110 * NS + round(BOARD_REACTION_SECONDS * NS)
    frame = calls[0]["features"]
    assert frame["board_death_age_radiant_s"].tolist() == pytest.approx(
        [BOARD_DEATH_AGE_CAP_SECONDS, (board_ns - 110 * NS) / NS, 5.0, 20.0]
    )
    assert frame["board_death_age_dire_s"].tolist() == [BOARD_DEATH_AGE_CAP_SECONDS] * 4
    assert frame["pending_deaths_radiant"].tolist() == [0, 1, 1, 0]


def test_synthetic_board_reuses_received_gold_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_predictor(monkeypatch, 0.02)
    model = _model_dir(tmp_path, "xp")
    rows = build_signal_frame(seconds=(58, 67, 74, 80))
    rows["radiant_nw_adv"] = [0, 100, 200, 300]
    rows.loc[rows["second"] == 80, "deaths_radiant"] = 1

    def cadence_draw(**kwargs: object) -> float:
        return 0.0 if kwargs["second"] == 67 else 0.99

    def scoreboard_delay(**kwargs: object) -> float:
        return 1.5

    monkeypatch.setattr("backtest.signals.grid_v1_unit_interval", cadence_draw)
    monkeypatch.setattr("backtest.signals.grid_v1_scoreboard_delay_seconds", scoreboard_delay)
    board_ns = round((72.5 + BOARD_REACTION_SECONDS) * NS)
    mids = {1: MidSeries(timestamps_ns=(0, board_ns), market_ps=(0.5, 0.6))}
    signals = build_match_signals((1,), rows, model, 10, "dota", SignalTiming(0, 16.0), mids)[1]
    assert signals.feed_timestamps_ns == (59 * NS, 68 * NS, 81 * NS)
    assert signals.timestamps_ns == (68 * NS, board_ns, 81 * NS)
    assert signals.board_tick_ns == (board_ns,)
    frame = calls[0]["features"]
    assert frame["radiant_nw_adv"].tolist() == [100, 100, 300]
    assert frame["second"].tolist() == [57, 57, 70]
    assert frame["pending_deaths_radiant"].tolist() == [0, 1, 0]
    assert frame["market_p_radiant"].tolist() == [0.5, 0.6, 0.6]
    assert signals.deaths_radiant == (0, 0, 1)
    stopped = replace(
        signals,
        feed_timestamps_ns=signals.feed_timestamps_ns[:2],
        timestamps_ns=signals.timestamps_ns[:2],
        predicted_deltas=signals.predicted_deltas[:2],
        dataset_market_ps=signals.dataset_market_ps[:2],
    )
    assert find_signal_asof(stopped, 83 * NS, 16.0) is not None
    assert find_signal_asof(stopped, 85 * NS, 16.0) is None


@pytest.mark.parametrize(
    "blocked_source", ["aged", "history_gap", "connect", "recovery", "paused", "terminal"]
)
def test_archive_board_cannot_revive_unusable_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_source: str
) -> None:
    _fake_predictor(monkeypatch, 0.02)
    model = _model_dir(tmp_path, "xp")
    ticks = [build_tick(0, 60, 100), build_tick(1, 65, 105)]
    board_at = 180 if blocked_source == "aged" else 111
    if blocked_source == "history_gap":
        ticks.append(build_tick(2, 200, 110))
    if blocked_source == "connect":
        ticks = ticks[:1]
    if blocked_source == "recovery":
        ticks.append(build_tick(2, 110, 170))
        board_at = 171
    if blocked_source == "paused":
        ticks[-1] = replace(ticks[-1], paused=True)
    if blocked_source == "terminal":
        ticks[-1] = replace(ticks[-1], terminal=True)
    gate = ScheduleKillGate(
        board_at * NS, ScheduleKillWait(1, (board_at + 10) * NS), ScheduleKillWait(0, board_at * NS)
    )
    schedule = build_schedule("grid-m1", 1, ticks, kill_gates=(gate,))
    plan = build_schedule_plan(1, schedule, model, tmp_path)
    features = _feature_frame(1, tuple(tick.game_second for tick in ticks))
    series = MidSeries(timestamps_ns=(0,), market_ps=(0.5,))
    signals = build_schedule_match_signals({1: plan}, features, {1: series})[1]
    assert signals.board_tick_ns == ()
