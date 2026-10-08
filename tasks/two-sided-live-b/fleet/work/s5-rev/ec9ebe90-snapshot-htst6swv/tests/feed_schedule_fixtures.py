"""Shared fixtures for feed-schedule plan and since-match cohort tests."""

from pathlib import Path
from typing import Any, Literal

import pytest
from catalog_fixtures import catalog_row

import backtest.feed_schedules as feed_schedules
from archive_index.schedule import (
    ADMISSION_RULES_VERSION,
    EXTRACTION_RULES_VERSION,
    SCHEDULE_SCHEMA_VERSION,
    FeedSchedule,
    ScheduleIdentity,
    ScheduleKillGate,
    ScheduleStats,
    ScheduleTick,
    schedule_path_for,
    write_schedule,
)
from backtest.feed_schedules import ScheduleBinding, SchedulePlan
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row
from shared.utils.model_registry import write_model_meta

NS = 1_000_000_000


def build_tick(
    seq: int,
    game_second: int,
    received_s: int,
    *,
    paused: bool = False,
    terminal: bool = False,
    radiant_nw_adv: int = 0,
    radiant_nw: int = 0,
    dire_nw: int = 0,
    radiant_xp_adv: int = 0,
    deaths_radiant: int = 0,
    deaths_dire: int = 0,
    top1_nw_adv: int = 0,
    radiant_top1_nw_ratio: float = 0.0,
    dire_top1_nw_ratio: float = 0.0,
    top3_nw_adv: int = 0,
    radiant_top3_nw_ratio: float = 0.0,
    dire_top3_nw_ratio: float = 0.0,
) -> ScheduleTick:
    """One schedule tick at received_s unix seconds."""
    return ScheduleTick(
        seq=seq,
        received_at_utc="2026-09-20T10:00:00Z",
        received_ns=received_s * NS,
        game_second=game_second,
        phase="game",
        paused=paused,
        terminal=terminal,
        horn_unix_seconds=0,
        source_ts_unix=received_s,
        radiant_nw_adv=radiant_nw_adv,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=radiant_xp_adv,
        deaths_radiant=deaths_radiant,
        deaths_dire=deaths_dire,
        top1_nw_adv=top1_nw_adv,
        radiant_top1_nw_ratio=radiant_top1_nw_ratio,
        dire_top1_nw_ratio=dire_top1_nw_ratio,
        top3_nw_adv=top3_nw_adv,
        radiant_top3_nw_ratio=radiant_top3_nw_ratio,
        dire_top3_nw_ratio=dire_top3_nw_ratio,
    )


def build_schedule(
    archive_id: str,
    match_id: int,
    ticks: list[ScheduleTick],
    *,
    fingerprint: str | None = None,
    feed_source: str = "grid",
    condition_id: str | None = None,
    steam_match_id: str | None = None,
    game: str = "dota",
    kill_gates: tuple[ScheduleKillGate, ...] = (),
) -> FeedSchedule:
    """A persisted-schedule fixture; fingerprint defaults to fp-<archive_id>."""
    return FeedSchedule(
        schema_version=SCHEDULE_SCHEMA_VERSION,
        rules_version=EXTRACTION_RULES_VERSION,
        admission_rules_version=ADMISSION_RULES_VERSION,
        max_feed_delay_seconds=10,
        fingerprint=fingerprint if fingerprint is not None else f"fp-{archive_id}",
        identity=ScheduleIdentity(
            game=game,
            archive_id=archive_id,
            match_id=str(match_id),
            feed_source=feed_source,
            condition_id=condition_id if condition_id is not None else f"cond-{match_id}",
            market_slug="m",
            event_slug="e",
            event_id=None,
            map_number=1,
            steam_match_id=steam_match_id if steam_match_id is not None else str(match_id),
            yes_token_id="yes",
            no_token_id="no",
            yes_is_radiant=True,
            yes_token_index=None,
            joined_at_utc="2026-09-20T10:00:00Z",
            joined_at_second=0,
            horn_at_utc="2026-09-20T10:00:00Z",
        ),
        stats=ScheduleStats(
            tick_count=len(ticks),
            interruption_count=0,
            terminal=bool(ticks and ticks[-1].terminal),
            terminal_interrupted=False,
            first_received_at_utc=None,
            last_received_at_utc=None,
            first_game_second=None,
            last_game_second=None,
            window_ticks=0,
            warmup_ticks=0,
            paused_ticks=0,
            max_arrival_gap_seconds=None,
            delay_s=None,
            delay_evidence="meta",
        ),
        interruptions=(),
        kill_gates=kill_gates,
        match_json_sha256="x",
        feed_sha256="y",
        feed_file="grid_state.jsonl",
        feed_size=0,
        ticks=tuple(ticks),
    )


class ArchiveLayout:
    """tmp TRADER_DIR + index dir the feed resolver reads through."""

    def __init__(self, trader: Path, index_dir: Path) -> None:
        self.trader = trader
        self.index_dir = index_dir

    def publish(self, schedule: FeedSchedule, game: str = "dota") -> None:
        """Write a schedule JSON where the resolver looks for it."""
        path = schedule_path_for(self.index_dir, "trader", game, schedule.identity.archive_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_schedule(schedule, path)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ArchiveLayout:
    """Point the resolver's archive root map and index dir at tmp."""
    trader = tmp_path / "trader"
    index_dir = tmp_path / "index"
    monkeypatch.setattr(feed_schedules, "TRADER_DIR", trader)
    monkeypatch.setattr(feed_schedules, "ARCHIVE_INDEX_DIR", index_dir)
    return ArchiveLayout(trader=trader, index_dir=index_dir)


def build_index_row(
    archive_id: str,
    *,
    game: str = "dota",
    admission: str = "admitted",
    feed_source: str | None = "grid",
    steam_match_id: str | None = None,
    condition_id: str | None = None,
    schedule_fingerprint: str | None = None,
    meta_ok: bool = True,
) -> dict[str, Any]:
    """One archive_index row with the columns feed planning reads."""
    return {
        "archive_root": "trader",
        "archive_id": archive_id,
        "game": game,
        "meta_ok": meta_ok,
        "admission": admission,
        "feed_source": feed_source,
        "steam_match_id": steam_match_id,
        "condition_id": condition_id,
        "schedule_fingerprint": schedule_fingerprint,
    }


def build_dota_catalog(match_id: int, **row_kwargs: Any) -> MatchCatalog:
    """One-match catalog from the shared catalog fixture."""
    return MatchCatalog({match_id: catalog_entry_from_row(catalog_row(match_id, **row_kwargs))})


def build_schedule_plan(
    match_id: int,
    schedule: FeedSchedule,
    model_dir: Path,
    tmp_path: Path,
    feed_source: Literal["grid", "oddin"] = "grid",
) -> SchedulePlan:
    """A schedule plan bound to an in-memory schedule for signal tests."""
    binding = ScheduleBinding(
        archive_root="trader",
        archive_id=f"grid-m{match_id}",
        feed_source=feed_source,
        archive_dir=tmp_path,
        schedule_path=tmp_path / "s.json",
        schedule_fingerprint="fp",
        schedule=schedule,
    )
    return SchedulePlan(match_id=match_id, binding=binding, model_dir=model_dir)


def build_manifest_model_dir(tmp_path: Path, name: str) -> Path:
    """A model dir with full registry meta, for manifest identity hashing."""
    directory = tmp_path / name
    directory.mkdir()
    (directory / "member_00.txt").write_text("a")
    write_model_meta(
        {
            "name": name,
            "trained_at": "2026-09-11T00:00:00Z",
            "train_dataset_sha256": "0" * 64,
            "validation_dataset_sha256": "1" * 64,
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "source_lag_seconds": 0,
            "train_matches": 1,
            "metrics": None,
            "members": ["member_00.txt"],
            "member_trees": [1],
            "ensemble_arm": "default_boot",
            "ensemble_k": 1,
            "ensemble_sampling": "boot",
        },
        directory / "model.json",
    )
    return directory
