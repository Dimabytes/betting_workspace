"""Step 3B: --since-match cohorts, feed-plan exclusions, and the manifest feed map."""

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from catalog_fixtures import catalog_row
from feed_schedule_fixtures import (
    ArchiveLayout,
    build_dota_catalog,
    build_index_row,
    build_manifest_model_dir,
    build_schedule,
    build_schedule_plan,
    build_tick,
)
from feed_schedule_fixtures import layout as layout

import backtest.feed_schedules as feed_schedules
import backtest.run as backtest_run
from archive_index.schedule import ADMISSION_RULES_VERSION, EXTRACTION_RULES_VERSION
from backtest.context import ReplayWindow
from backtest.feed_schedules import (
    DotaArchiveJoin,
    FeedPlanResult,
    GridV1Plan,
    MatchFeedPlan,
    SchedulePlan,
    reject_schedule_flags,
    resolve_feed_plans,
    schedule_map_sha256,
)
from backtest.lol_inputs import select_lol_since_match_ids
from backtest.results import SignalProvenance, signal_provenance_map
from backtest.run import (
    DotaSelection,
    build_run_manifest,
    load_run_selection,
    manifest_for_selection,
    parse_args,
)
from backtest.selection import MarketSources, select_dota_since_match_ids
from shared.constants.paths import VALIDATION_DATASET_PATH
from shared.constants.strategy import (
    BACKTEST_DOTA_MAX_POSITION_LEVELS,
    BACKTEST_LOL_MAX_POSITION_LEVELS,
)
from shared.utils.gbm import model_identity_sha256
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row
from strategy.policy import follow300_policy


def test_parse_args_accepts_since_match_cohort(monkeypatch: pytest.MonkeyPatch) -> None:
    """--since-match takes a catalog match id and allows validation-run options."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--since-match",
            "8012345678",
            "--name",
            "sincerun",
            "--limit",
            "3",
            "--shard",
            "0/2",
            "--resume",
        ],
    )
    args = parse_args()
    assert args.since_match == 8012345678
    assert args.limit == 3
    assert args.shard is not None
    assert args.resume is True


def test_parse_args_since_match_requires_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cohort selector names its run dir like --validation does."""
    monkeypatch.setattr(sys, "argv", ["backtest", "--since-match", "1"])
    with pytest.raises(SystemExit, match="2"):
        parse_args()


def test_parse_args_since_match_excludes_other_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--since-match cannot combine with --match-id or --validation."""
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--since-match", "1", "--match-id", "2", "--name", "x"]
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--since-match", "1", "--validation", "--name", "x"]
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()


def _has_all_days(*_args: object) -> bool:
    """Pretend every market's Telonex days are local."""
    return True


def _has_days_unless_bad_token(
    token_ids: tuple[str, str], _window: ReplayWindow, _root: Path
) -> bool:
    """Local days exist except for markets carrying the 'bad' token id."""
    return "bad" not in token_ids


def _since_sources(
    rows: dict[int, Any],
    *,
    usable: frozenset[int],
) -> MarketSources:
    """MarketSources over the shared catalog fixture for since-selection tests."""
    return MarketSources(
        validation_match_ids=tuple(rows),
        catalog=MatchCatalog(
            {match_id: catalog_entry_from_row(row) for match_id, row in rows.items()}
        ),
        usable_signal_match_ids=usable,
    )


def test_select_dota_since_match_ids_returns_the_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cohort is every replayable catalog match at/after the anchor's start."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    rows = {
        1: catalog_row(1, start_time=100),
        2: catalog_row(2, start_time=200),
        3: catalog_row(3, start_time=300),
    }
    sources = _since_sources(rows, usable=frozenset({1, 2, 3}))

    assert select_dota_since_match_ids(sources, 2) == (2, 3)


def test_select_dota_since_match_ids_sorts_cohort_by_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cohort comes out chronological even when rows arrive unordered."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    rows = {
        3: catalog_row(3, start_time=200),
        1: catalog_row(1, start_time=100),
        4: catalog_row(4, start_time=400),
        2: catalog_row(2, start_time=300),
    }
    sources = _since_sources(rows, usable=frozenset({1, 2, 3, 4}))

    assert select_dota_since_match_ids(sources, 3) == (3, 2, 4)


def test_select_dota_since_match_ids_skips_unreplayable_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal and telonex gates apply to the cohort, not just the anchor."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_days_unless_bad_token)
    rows = {
        1: catalog_row(1, start_time=100),
        2: catalog_row(2, start_time=200),
        3: catalog_row(3, start_time=300, token_id_0="bad"),
        4: catalog_row(4, start_time=400),
        5: catalog_row(5, start_time=500),
    }
    sources = _since_sources(rows, usable=frozenset({1, 2, 3, 5}))

    assert select_dota_since_match_ids(sources, 2) == (2, 5)


def test_select_dota_since_match_ids_anchor_need_not_be_replayable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anchor only sets the time floor; an ineligible anchor still yields a cohort."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    rows = {
        1: catalog_row(1, start_time=100),
        2: catalog_row(2, start_time=200),
    }
    sources = _since_sources(rows, usable=frozenset({2}))

    assert select_dota_since_match_ids(sources, 1) == (2,)


def test_select_dota_since_match_ids_unknown_anchor_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An anchor outside the catalog cannot set a time floor."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    sources = _since_sources({1: catalog_row(1, start_time=100)}, usable=frozenset({1}))

    with pytest.raises(ValueError, match="--since-match 9"):
        select_dota_since_match_ids(sources, 9)


def test_select_dota_since_match_ids_empty_cohort_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A time floor that admits nothing is an error, not a silent empty run."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    sources = _since_sources({1: catalog_row(1, start_time=100)}, usable=frozenset())

    with pytest.raises(ValueError, match="no replayable matches"):
        select_dota_since_match_ids(sources, 1)


def test_select_lol_since_match_ids_orders_eligible_by_horn() -> None:
    """Whitelist-filtered eligible maps sort by signal start_time, not match id."""
    audit = pd.DataFrame(
        [
            {"match_id": 1, "eligible": True, "ok_quote_fraction": 1.0},
            {"match_id": 2, "eligible": True, "ok_quote_fraction": 1.0},
            {"match_id": 3, "eligible": False, "ok_quote_fraction": 0.0},
        ]
    )
    signal_rows = pd.DataFrame(
        [
            {"match_id": 2, "start_time": 200},
            {"match_id": 1, "start_time": 300},
            {"match_id": 3, "start_time": 400},
        ]
    )

    assert select_lol_since_match_ids(audit, signal_rows, 2) == (2, 1)


def test_select_lol_since_match_ids_anchor_needs_signal_rows() -> None:
    """The anchor's horn comes from its signal rows; none means no time floor."""
    audit = pd.DataFrame([{"match_id": 1, "eligible": True, "ok_quote_fraction": 1.0}])
    signal_rows = pd.DataFrame([{"match_id": 1, "start_time": 10}])

    with pytest.raises(ValueError, match="--since-match 7"):
        select_lol_since_match_ids(audit, signal_rows, 7)


def test_select_lol_since_match_ids_empty_cohort_raises() -> None:
    """No eligible map at/after the anchor is an error, not an empty run."""
    audit = pd.DataFrame([{"match_id": 1, "eligible": False, "ok_quote_fraction": 0.0}])
    signal_rows = pd.DataFrame([{"match_id": 1, "start_time": 10}])

    with pytest.raises(ValueError, match="no eligible matches"):
        select_lol_since_match_ids(audit, signal_rows, 1)


def _pin_dota_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sources: MarketSources
) -> list[int | None]:
    """Stub load_dota_selection to return the probe; record each match_id arg."""
    calls: list[int | None] = []

    def fake_load(
        match_id: int | None,
        _limit: int | None,
        *,
        match_ids: tuple[int, ...] | None = None,
        validation_dataset: Path = VALIDATION_DATASET_PATH,
    ) -> DotaSelection:
        calls.append(match_id)
        return DotaSelection(
            selected_ids=(1,),
            coverage=None,
            capture_root=tmp_path,
            report_root=tmp_path,
            signal_rows=pd.DataFrame(),
            sources=sources,
        )

    monkeypatch.setattr(backtest_run, "load_dota_selection", fake_load)
    return calls


def test_load_run_selection_since_match_pins_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dota since-cohort pins one probe load, like the LoL path."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    rows = {
        1: catalog_row(1, start_time=100),
        2: catalog_row(2, start_time=200),
        3: catalog_row(3, start_time=300),
    }
    calls = _pin_dota_load(monkeypatch, tmp_path, _since_sources(rows, usable=frozenset({1, 2, 3})))

    run = load_run_selection(
        game="dota",
        match_id=None,
        since_match=2,
        limit=None,
        allowed_event_ids=None,
    )

    assert calls == [None]
    assert run.selected_ids == (2, 3)
    assert run.coverage is None


def test_load_run_selection_since_match_applies_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--limit truncates the since-cohort after the pin."""
    monkeypatch.setattr("backtest.selection.has_local_telonex_days", _has_all_days)
    rows = {
        1: catalog_row(1, start_time=100),
        2: catalog_row(2, start_time=200),
        3: catalog_row(3, start_time=300),
    }
    _pin_dota_load(monkeypatch, tmp_path, _since_sources(rows, usable=frozenset({1, 2, 3})))

    run = load_run_selection(
        game="dota",
        match_id=None,
        since_match=2,
        limit=1,
        allowed_event_ids=None,
    )

    assert run.selected_ids == (2,)


def test_reject_schedule_flags_refuses_overrides_on_a_schedule_plan(tmp_path: Path) -> None:
    """Cadence/dataset knobs cannot apply to an archive-bound cohort."""
    plan = build_schedule_plan(
        7, build_schedule("grid-m7", 7, [build_tick(0, 5, 100)]), tmp_path, tmp_path
    )
    plans = {7: plan, 8: GridV1Plan(match_id=8, model_dir=tmp_path)}

    with pytest.raises(ValueError, match="do not apply"):
        reject_schedule_flags(
            plans, lag_seconds=None, cadence_mean_interval=None, validation_dataset=tmp_path
        )
    with pytest.raises(ValueError, match="do not apply"):
        reject_schedule_flags(
            plans, lag_seconds=30, cadence_mean_interval=None, validation_dataset=None
        )
    with pytest.raises(ValueError, match="do not apply"):
        reject_schedule_flags(
            plans, lag_seconds=None, cadence_mean_interval=5, validation_dataset=None
        )


def test_reject_schedule_flags_allows_overrides_on_grid_v1_only(tmp_path: Path) -> None:
    """The same knobs still work when no schedule plan is in the cohort."""
    plans = {8: GridV1Plan(match_id=8, model_dir=tmp_path)}

    reject_schedule_flags(
        plans, lag_seconds=30, cadence_mean_interval=5, validation_dataset=tmp_path
    )


def test_resolve_feed_plans_refuses_an_excluded_match_id(
    layout: ArchiveLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--match-id on an excluded match fails with the reason, not a silent skip."""
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
    monkeypatch.setattr(feed_schedules, "load_archive_index", lambda: index)

    with pytest.raises(ValueError, match="match 7: archive_unlinked"):
        resolve_feed_plans(DotaArchiveJoin(catalog), (7,), None, 7, model_override_noxp=None)


def test_resolve_feed_plans_returns_mixed_plans_and_exclusions(
    layout: ArchiveLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cohort keeps its bound plans and reports each exclusion separately."""
    schedule = build_schedule("grid-a1", 7, [build_tick(0, 5, 100)])
    layout.publish(schedule)
    index = pd.DataFrame(
        [
            build_index_row(
                "grid-a1",
                steam_match_id="7",
                condition_id="cond-7",
                schedule_fingerprint="fp-grid-a1",
            ),
            build_index_row(
                "grid-b2",
                steam_match_id="8",
                condition_id="cond-8",
                schedule_fingerprint="fp-grid-b2",
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
            9: catalog_entry_from_row(catalog_row(9, condition_id="cond-9")),
        }
    )
    monkeypatch.setattr(feed_schedules, "load_archive_index", lambda: index)

    feed = resolve_feed_plans(
        DotaArchiveJoin(catalog), (7, 8, 9), None, None, model_override_noxp=None
    )

    assert isinstance(feed.plans[7], SchedulePlan)
    assert isinstance(feed.plans[9], GridV1Plan)
    assert feed.plans.keys() == {7, 9}
    assert feed.exclusions == {8: "archive_unlinked"}


def _parquet_sha(_path: Path) -> str:
    return "sha"


def test_run_manifest_records_the_feed_map(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ordinary manifests pin the resolved plan map, exclusions, and model dirs."""
    model_dir = build_manifest_model_dir(tmp_path, "catalog")
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)
    plans = {1: GridV1Plan(match_id=1, model_dir=model_dir)}

    manifest = build_run_manifest(
        model_dir=model_dir,
        game="dota",
        signal_cadence_seed=0,
        run_policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(1,),
        whitelist_keys={},
        plans=plans,
        exclusions={9: "archive_unlinked", 3: "schedule_stale"},
        since_match=7,
        archives_only=False,
    )

    assert manifest["signal_source"] == "auto"
    assert manifest["backtest_policy"] == "current"
    assert "archive_policy_sha" not in manifest
    assert "archive_model_sha256" not in manifest
    assert manifest["schedule_map_sha256"] == schedule_map_sha256(plans)
    assert manifest["feed_schedule_rules_version"] == EXTRACTION_RULES_VERSION
    assert manifest["admission_rules_version"] == ADMISSION_RULES_VERSION
    assert manifest["archive_exclusions"] == {
        "3": "schedule_stale",
        "9": "archive_unlinked",
    }
    assert manifest["model_sha256_by_dir"] == {str(model_dir): model_identity_sha256(model_dir)}
    assert manifest["since_match"] == 7
    assert "game_features_sha256" not in manifest


def test_run_manifest_omits_since_match_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """since_match only appears when used; archive_exclusions is always present."""
    model_dir = build_manifest_model_dir(tmp_path, "catalog")
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)

    manifest = build_run_manifest(
        model_dir=model_dir,
        game="dota",
        signal_cadence_seed=0,
        run_policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(1,),
        whitelist_keys={},
        plans={1: GridV1Plan(match_id=1, model_dir=model_dir)},
        exclusions={},
        since_match=None,
        archives_only=False,
    )

    assert "since_match" not in manifest
    assert manifest["archive_exclusions"] == {}


def test_run_manifest_mixed_models_pin_dirs_and_game_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed feed/model plans hash each model dir once and pin game_features."""
    monkeypatch.setattr("backtest.run.read_framework_commit", lambda: "framework")
    monkeypatch.setattr("backtest.run.sha256_file", _parquet_sha)
    research = build_manifest_model_dir(tmp_path, "research")
    noxp = build_manifest_model_dir(tmp_path, "noxp")
    plans = {
        1: build_schedule_plan(
            1, build_schedule("grid-m1", 1, [build_tick(0, 5, 100)]), research, tmp_path
        ),
        2: build_schedule_plan(
            2,
            build_schedule("oddin-m2", 2, [build_tick(0, 5, 110)], feed_source="oddin"),
            noxp,
            tmp_path,
            feed_source="oddin",
        ),
        3: GridV1Plan(match_id=3, model_dir=research),
    }

    manifest = build_run_manifest(
        model_dir=research,
        game="dota",
        signal_cadence_seed=0,
        run_policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(1, 2, 3),
        whitelist_keys={},
        plans=plans,
        exclusions={4: "archive_unlinked"},
        since_match=None,
        archives_only=False,
    )

    assert manifest["signal_source"] == "auto"
    assert manifest["schedule_map_sha256"] == schedule_map_sha256(plans)
    assert manifest["model_sha256_by_dir"] == {
        str(noxp): model_identity_sha256(noxp),
        str(research): model_identity_sha256(research),
    }
    assert manifest["game_features_sha256"] == "sha"
    assert manifest["archive_exclusions"] == {"4": "archive_unlinked"}


def test_run_manifest_lol_schedule_plan_pins_game_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LoL schedule plan pins game_features.parquet; a grid-only run omits it."""
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)
    model_dir = build_manifest_model_dir(tmp_path, "lol")
    schedule = build_schedule("lol-m1", 1, [build_tick(0, 5, 100)], game="lol")
    plans = {1: build_schedule_plan(1, schedule, model_dir, tmp_path)}

    manifest = build_run_manifest(
        model_dir=model_dir,
        game="lol",
        signal_cadence_seed=0,
        run_policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        max_position_levels=BACKTEST_LOL_MAX_POSITION_LEVELS,
        selected_ids=(1,),
        whitelist_keys={},
        plans=plans,
        exclusions={},
        since_match=None,
        archives_only=False,
    )

    assert manifest["game"] == "lol"
    assert manifest["game_features_sha256"] == "sha"

    grid_only = build_run_manifest(
        model_dir=model_dir,
        game="lol",
        signal_cadence_seed=0,
        run_policy=follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0),
        max_position_levels=BACKTEST_LOL_MAX_POSITION_LEVELS,
        selected_ids=(1,),
        whitelist_keys={},
        plans={1: GridV1Plan(match_id=1, model_dir=model_dir)},
        exclusions={},
        since_match=None,
        archives_only=False,
    )
    assert "game_features_sha256" not in grid_only


def test_archives_only_manifest_equals_the_seed_expected_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A seed's expected archive fingerprint is the manifest an --archives-only run writes."""
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)
    model_dir = build_manifest_model_dir(tmp_path, "catalog")
    schedule = build_schedule("grid-m1", 101, [build_tick(0, 5, 100)])
    plans = {
        101: build_schedule_plan(101, schedule, model_dir, tmp_path),
        202: GridV1Plan(match_id=202, model_dir=model_dir),
    }
    exclusions = {303: "archive_unlinked"}
    policy = follow300_policy(level_usdc=100.0, debounce_ms=0, fallback_timer_s=1.0)

    def fingerprint(
        selected_ids: tuple[int, ...],
        feed_plans: Mapping[int, MatchFeedPlan],
        archives_only: bool,
    ) -> dict[str, Any]:
        return manifest_for_selection(
            selected_ids,
            FeedPlanResult(plans=dict(feed_plans), exclusions=exclusions),
            model_dir=model_dir,
            game="dota",
            signal_cadence_seed=0,
            run_policy=policy,
            max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
            league_whitelist=None,
            since_match=None,
            validation_dataset_path=VALIDATION_DATASET_PATH,
            backtest_lag_seconds=0,
            cadence_mean_interval=None,
            archives_only=archives_only,
        )

    produced = fingerprint((101,), {101: plans[101]}, True)
    expected = fingerprint((101, 202), plans, True)
    assert produced == expected
    assert produced["archives_only"] is True
    assert produced["selected_matches"] == 1
    assert "signal_cadence_seed" not in produced
    assert produced["archive_exclusions"] == {"303": "archive_unlinked"}
    seed = fingerprint((101, 202), plans, False)
    assert seed["selected_matches"] == 2
    assert seed["signal_cadence_seed"] == 0
    assert "archives_only" not in seed


def test_signal_provenance_names_schedule_feed_and_model(tmp_path: Path) -> None:
    """Result-row provenance carries the bound feed source and the model dir name."""
    schedule = build_schedule("oddin-m2", 2, [build_tick(0, 5, 100)], feed_source="oddin")
    plan = build_schedule_plan(2, schedule, tmp_path / "noxp", tmp_path, feed_source="oddin")
    provenance = signal_provenance_map({2: plan}, (2,))
    assert provenance[2] == SignalProvenance(
        signal_mode="schedule", feed_source="oddin", model_name="noxp"
    )
    grid = signal_provenance_map({3: GridV1Plan(match_id=3, model_dir=tmp_path / "research")}, (3,))
    assert grid[3] == SignalProvenance(signal_mode="grid_v1", feed_source="", model_name="research")
