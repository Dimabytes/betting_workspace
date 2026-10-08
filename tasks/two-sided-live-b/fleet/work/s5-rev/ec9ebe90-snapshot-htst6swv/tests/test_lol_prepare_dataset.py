"""Synthetic livestats + Telonex fixtures for LoL Stage 05. No network, no data/lol."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
import pytest
import typer
from telonex_fixtures import TelonexRawLevels, write_token_book, write_token_tape
from typer.testing import CliRunner

from lol.constants import (
    LOL_CASHBACK_UNDO_MAX_GOLD,
    LOL_DATASET_COLUMNS,
    LOL_PREPARE_END_SECOND,
    LOL_PREPARE_WORKERS,
    LOL_SPAWN_SEARCH_SECONDS,
    LOL_TARGET_HORIZON_SECONDS,
    LOL_VALIDATION_START_TIME,
    REASON_ABORTED_FEED,
    REASON_ACCEPTED,
    REASON_LIVESTATS_INVARIANT_VIOLATION,
    REASON_MISSING_BOOKS,
    REASON_MISSING_PRIOR,
    REASON_NEGATIVE_NET_WORTH,
    REASON_NO_DETAILS,
    REASON_NO_LIVESTATS,
    REASON_NO_PATCH_VERSION,
    REASON_NO_SPAWN_FRAME,
    REASON_ZERO_LABELED_ROWS,
    REASON_ZERO_USABLE_FRAMES,
    REASON_ZERO_USABLE_ROWS,
)
from lol.livestats_frames import (
    FrameFeatures,
    GridRow,
    LivestatsDrop,
    LivestatsOk,
    prepare_map_livestats_until,
)
from lol.networth import ItemCatalog, ItemTable
from lol.replay import book_load_window, replay_bounds
from lol.types import LolDatasetRow, LolLinkAssignment, LolLinkRow, LolRadiantTokenIndex
from shared.constants.lol import LOL_SOURCE_LAG_SECONDS
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.types.dataset import DotaGameFeatureRow
from shared.utils.telonex_book import US_PER_SECOND, TokenBook
from shared.utils.top_players import TopPlayerFeatures

OUTPUT_NAMES = (
    "training.parquet",
    "validation.parquet",
    "production_training.parquet",
    "split.parquet",
    "audit.parquet",
    "market_seconds.parquet",
    "backtest_audit.parquet",
    "game_features.parquet",
)
TOK_R = "tok-radiant"
TOK_D = "tok-dire"
SPAWN_TS = 1_700_000_000
ANCHOR_TS = SPAWN_TS - 4


class FetchModule(Protocol):
    """Stage 04 gzip helpers used to write synthetic archives."""

    def write_gzip_jsonl(self, path: Path, payloads: Sequence[object]) -> None:
        """Write raw window objects as gzip JSONL."""
        ...

    def archive_path(self, windows_dir: Path, esports_game_id: str) -> Path:
        """Gzip JSONL path for one map."""
        ...


class PrepareModule(Protocol):
    """Importable Stage 05 surface."""

    def prepare_dataset(
        self,
        links_path: Path,
        windows_dir: Path,
        details_dir: Path,
        item_catalog_dir: Path,
        telonex_root: Path,
        output_dir: Path,
        workers: int,
        cache_dir: Path,
    ) -> None:
        """Build datasets from local livestats and books."""
        ...

    def build_game_feature_rows(
        self,
        livestats: LivestatsOk,
        prior: float,
    ) -> tuple[DotaGameFeatureRow, ...]:
        """One exact-second model-input row per grid slot."""
        ...

    def main(
        self,
        workers: int = LOL_PREPARE_WORKERS,
    ) -> None:
        """Typer entrypoint."""
        ...


def load_fetch() -> FetchModule:
    """Import Stage 04 through its typed test surface."""
    return cast(FetchModule, cast(object, import_module("lol.04_fetch_lolesports")))


def load_prepare() -> PrepareModule:
    """Import Stage 05 through its typed test surface."""
    return cast(PrepareModule, cast(object, import_module("lol.05_prepare_dataset")))


def stamp_at(seconds: float) -> str:
    """RFC460 timestamp at a unix seconds float."""
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def participants(gold: int, level: int, deaths: int, start_id: int) -> list[dict[str, object]]:
    """Five players with sequential participantId values and spawn-like extras."""
    return [
        {
            "participantId": start_id + offset,
            "totalGold": gold,
            "level": level,
            "deaths": deaths,
            "kills": 0,
            "creepScore": 0,
            "currentHealth": 1000,
            "maxHealth": 1000,
        }
        for offset in range(5)
    ]


def team_side(gold: int, kills: int, people: list[dict[str, object]]) -> dict[str, object]:
    """One blueTeam/redTeam object."""
    return {"totalGold": gold, "totalKills": kills, "dragons": [], "participants": people}


def frame_at(
    wall: float,
    blue_gold: int,
    red_gold: int,
    blue_level: int,
    red_level: int,
    blue_deaths: int,
    red_deaths: int,
) -> dict[str, object]:
    """One livestats frame with matching team totals."""
    blue = participants(blue_gold, blue_level, blue_deaths, 1)
    red = participants(red_gold, red_level, red_deaths, 6)
    return {
        "rfc460Timestamp": stamp_at(wall),
        "blueTeam": team_side(blue_gold * 5, red_deaths * 5, blue),
        "redTeam": team_side(red_gold * 5, blue_deaths * 5, red),
    }


def spawn_frame(wall: float) -> dict[str, object]:
    """Clock-zero frame: ten players at 500 gold, level 1, no deaths."""
    return frame_at(wall, 500, 500, 1, 1, 0, 0)


def progress_frame(wall: float) -> dict[str, object]:
    """Post-spawn frame with gold progress so the map is not aborted_feed."""
    return frame_at(wall, 600, 500, 1, 1, 0, 0)


def played_frames(spawn: float) -> list[dict[str, object]]:
    """Spawn plus one gold-progress frame."""
    return [spawn_frame(spawn), progress_frame(spawn + 1.0)]


def one_bucket_frames(spawn: float) -> list[dict[str, object]]:
    """Wall span of one full 5-minute tape bucket, with gold progress so the tail is not a pause."""
    return [
        spawn_frame(spawn),
        progress_frame(spawn + 1.0),
        frame_at(spawn + 300.0, 700, 500, 1, 1, 0, 0),
    ]


def write_min_tape(root: Path, spawn_times_us: Sequence[int]) -> None:
    """Write one onchain fill per spawn so the fills channel exists."""
    write_token_tape(root, token_id=TOK_R, timestamps_us=list(spawn_times_us))


def fixture_roles(start_id: int) -> list[dict[str, object]]:
    """Five unique roles with sequential participantId values."""
    names = ("top", "jungle", "mid", "bottom", "support")
    return [{"participantId": start_id + offset, "role": names[offset]} for offset in range(5)]


def standard_game_metadata() -> dict[str, object]:
    """gameMetadata with a patch and five unique roles per side."""
    return {
        "patchVersion": "16.16.1",
        "blueTeamMetadata": {"participantMetadata": fixture_roles(1)},
        "redTeamMetadata": {"participantMetadata": fixture_roles(6)},
    }


def fixture_item_table() -> ItemTable:
    """One-table catalog matching standard_game_metadata patchVersion."""
    return ItemTable("16.16.1", frozenset({1001, 2003}), {2003: 50})


def write_item_catalog(directory: Path, tables: Sequence[ItemTable]) -> Path:
    """Write compact Data Dragon tables for Stage 05 tests."""
    directory.mkdir(parents=True, exist_ok=True)
    for table in tables:
        payload = {
            "version": table.version,
            "known_item_ids": sorted(table.known_item_ids),
            "consumed_item_gold": {
                str(item_id): price for item_id, price in sorted(table.consumed_item_gold.items())
            },
        }
        (directory / f"{table.version}.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def window_body(game_id: str, frames: list[dict[str, object]]) -> dict[str, object]:
    """Raw provider window object."""
    return {
        "esportsGameId": game_id,
        "gameMetadata": standard_game_metadata(),
        "frames": frames,
    }


def make_link(
    esports_game_id: str,
    event_id: str,
    game_number: int,
    loading_anchor_ts: int,
    resolved_outcome: str,
    resolved_outcome_index: int,
) -> LolLinkRow:
    """One accepted links.parquet row."""
    radiant: LolRadiantTokenIndex = 0
    assignment: LolLinkAssignment = "game_winner"
    return {
        "event_id": event_id,
        "market_id": f"{event_id}-m{game_number}",
        "condition_id": f"cid-{esports_game_id}",
        "outcomes_json": '["T1","Gen.G"]',
        "clob_token_ids_json": json.dumps([TOK_R, TOK_D]),
        "esports_game_id": esports_game_id,
        "esports_match_id": f"match-{event_id}",
        "game_number": game_number,
        "loading_anchor": stamp_at(float(loading_anchor_ts)),
        "loading_anchor_ts": loading_anchor_ts,
        "radiant_token_index": radiant,
        "resolved_outcome": resolved_outcome,
        "resolved_outcome_index": resolved_outcome_index,
        "blue_esports_team_id": "blue-1",
        "red_esports_team_id": "red-1",
        "assignment": assignment,
    }


def accepted_link(esports_game_id: str, event_id: str, game_number: int) -> LolLinkRow:
    """Resolved Game N winner with spawn near SPAWN_TS."""
    return make_link(esports_game_id, event_id, game_number, ANCHOR_TS, "T1", 0)


def map_livestats(link: LolLinkRow, windows: Path) -> LivestatsOk | LivestatsDrop:
    """Stage 05 livestats grid through the production prepare end second."""
    table = fixture_item_table()
    catalog = ItemCatalog({table.version: table})
    return prepare_map_livestats_until(
        link,
        windows,
        windows.parent / "details",
        catalog,
        LOL_PREPARE_END_SECOND,
    )


def write_links(path: Path, rows: list[LolLinkRow]) -> None:
    """Write a tiny links.parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def write_catalog(path: Path) -> None:
    """Write a readable catalog parquet (contents unused per map)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"market_id": pd.Series(dtype="str")}).to_parquet(path, index=False)


def two_sided(mid: float) -> tuple[TelonexRawLevels, TelonexRawLevels]:
    """Best bid/ask around a mid."""
    return [{"price": mid - 0.01, "size": 10.0}], [{"price": mid + 0.01, "size": 10.0}]


def write_pair_books(
    root: Path,
    snapshots: list[tuple[int, float, float]],
) -> None:
    """Write radiant/dire books at the given (timestamp_us, radiant_mid, dire_mid)."""
    radiant_rows: list[tuple[int, TelonexRawLevels, TelonexRawLevels]] = []
    dire_rows: list[tuple[int, TelonexRawLevels, TelonexRawLevels]] = []
    for stamp, radiant_mid, dire_mid in snapshots:
        radiant_bid, radiant_ask = two_sided(radiant_mid)
        dire_bid, dire_ask = two_sided(dire_mid)
        radiant_rows.append((stamp, radiant_bid, radiant_ask))
        dire_rows.append((stamp, dire_bid, dire_ask))
    write_token_book(root, token_id=TOK_R, rows=radiant_rows, day="2026-01-01")
    write_token_book(root, token_id=TOK_D, rows=dire_rows, day="2026-01-01")


def standard_books(root: Path, spawn_us: int) -> None:
    """Prior at spawn-1s. Current mid is the +11s join; the 300s label is 11s after that."""
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    label_us = lag_us + LOL_TARGET_HORIZON_SECONDS * US_PER_SECOND
    write_pair_books(
        root,
        [
            (spawn_us - US_PER_SECOND, 0.60, 0.40),
            (spawn_us, 0.60, 0.40),
            (spawn_us + lag_us, 0.60, 0.40),
            (spawn_us + LOL_TARGET_HORIZON_SECONDS * US_PER_SECOND, 0.70, 0.30),
            (spawn_us + label_us, 0.70, 0.30),
        ],
    )


def details_body(frames: list[dict[str, object]]) -> dict[str, object]:
    """Details payload with empty inventories matching each window stamp."""
    details_frames: list[dict[str, object]] = []
    for frame in frames:
        stamp = frame["rfc460Timestamp"]
        details_frames.append(
            {
                "rfc460Timestamp": stamp,
                "participants": [
                    {"participantId": participant_id, "items": []}
                    for participant_id in range(1, 11)
                ],
            }
        )
    return {"frames": details_frames}


def write_archive(windows_dir: Path, game_id: str, frames: list[dict[str, object]]) -> None:
    """Write one gzip JSONL window archive and a matching empty-item details archive."""
    fetch = load_fetch()
    windows_dir.mkdir(parents=True, exist_ok=True)
    fetch.write_gzip_jsonl(fetch.archive_path(windows_dir, game_id), [window_body(game_id, frames)])
    details_dir = windows_dir.parent / "details"
    details_dir.mkdir(parents=True, exist_ok=True)
    fetch.write_gzip_jsonl(fetch.archive_path(details_dir, game_id), [details_body(frames)])


def output_files(output_dir: Path) -> list[Path]:
    """The seven Stage 05 parquet paths."""
    return [output_dir / name for name in OUTPUT_NAMES]


def run_prepare(
    tmp_path: Path,
    links: list[LolLinkRow],
    windows_dir: Path,
    telonex_root: Path,
) -> Path:
    """Write links and run Stage 05 into tmp_path/out."""
    links_path = tmp_path / "links.parquet"
    output_dir = tmp_path / "out"
    write_links(links_path, links)
    telonex_root.mkdir(parents=True, exist_ok=True)
    (telonex_root / "book_snapshot_full").mkdir(parents=True, exist_ok=True)
    load_prepare().prepare_dataset(
        links_path,
        windows_dir,
        windows_dir.parent / "details",
        write_item_catalog(tmp_path / "ddragon_items", [fixture_item_table()]),
        telonex_root,
        output_dir,
        LOL_PREPARE_WORKERS,
        tmp_path / "map_builds",
    )
    return output_dir


def read_parquet(path: Path) -> pd.DataFrame:
    """Load one published parquet."""
    return pd.read_parquet(path)


def test_missing_details_drops_the_map(tmp_path: Path) -> None:
    """A map with windows but no details archive is skipped, not a prepare abort."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    details_dir = windows.parent / "details"
    load_fetch().archive_path(details_dir, "1001").unlink()
    telonex = tmp_path / "telonex"
    (telonex / "book_snapshot_full").mkdir(parents=True)
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    audit = read_parquet(out / "audit.parquet")
    assert audit.iloc[0]["reason"] == REASON_NO_DETAILS
    assert bool(audit.iloc[0]["included"]) is False
    assert read_parquet(out / "production_training.parquet").empty


def test_cli_rejects_preflight() -> None:
    """Stage 05 has no --preflight flag."""
    app = typer.Typer()
    app.command()(load_prepare().main)
    result = CliRunner().invoke(app, ["--preflight"])
    assert result.exit_code != 0
    assert "preflight" in result.output.lower() or "no such option" in result.output.lower()


def test_spawn_is_clock_zero(tmp_path: Path) -> None:
    """First gold frame is game_time 0 and all ten players have 500 gold."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            frame_at(float(ANCHOR_TS), 0, 0, 1, 1, 0, 0),
            spawn_frame(float(SPAWN_TS)),
            progress_frame(float(SPAWN_TS) + 1.0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.start_time == SPAWN_TS
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[0].features.radiant_nw == 2500
    assert by_second[0].features.dire_nw == 2500


def test_no_spawn_frame_all_zero_gold(tmp_path: Path) -> None:
    """All-zero gold drops the map with no_spawn_frame."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", [frame_at(float(SPAWN_TS), 0, 0, 1, 1, 0, 0)])
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert not isinstance(result, LivestatsOk)
    assert result.reason == REASON_NO_SPAWN_FRAME


def test_late_spawn_after_loading_anchor_is_kept(tmp_path: Path) -> None:
    """A spawn 91s after the loading anchor is still inside the search window."""
    windows = tmp_path / "windows"
    late = float(ANCHOR_TS + 91)
    write_archive(windows, "1001", played_frames(late))
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.start_time == int(late)


def test_spawn_beyond_search_window_is_no_spawn_frame(tmp_path: Path) -> None:
    """A spawn-shaped frame after the loading-anchor search window is ignored."""
    windows = tmp_path / "windows"
    late = float(ANCHOR_TS + LOL_SPAWN_SEARCH_SECONDS + 1)
    write_archive(windows, "1001", played_frames(late))
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_NO_SPAWN_FRAME


def test_spawn_shaped_loading_frame_before_anchor_is_ignored(tmp_path: Path) -> None:
    """A 500/lvl1/deaths0 frame before the loading anchor is not clock zero."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(ANCHOR_TS) - 60.0),
            spawn_frame(float(SPAWN_TS)),
            progress_frame(float(SPAWN_TS) + 1.0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.start_time == SPAWN_TS


def set_participant_hp(frame: dict[str, object], hp: int) -> dict[str, object]:
    """Set currentHealth on every participant in the frame."""
    for side in ("blueTeam", "redTeam"):
        team = frame[side]
        assert isinstance(team, dict)
        people = cast(dict[str, object], team)["participants"]
        assert isinstance(people, list)
        for raw in cast(list[object], people):
            assert isinstance(raw, dict)
            cast(dict[str, object], raw)["currentHealth"] = hp
    return frame


def test_frozen_gap_is_pause(tmp_path: Path) -> None:
    """Loading→spawn is not a pause; a 20s frozen post-spawn gap is."""
    windows = tmp_path / "windows"
    loading = float(SPAWN_TS - 6)
    mid = float(SPAWN_TS + 1)
    post_pause = float(SPAWN_TS + 21)
    write_archive(
        windows,
        "1001",
        [
            frame_at(loading, 0, 0, 1, 1, 0, 0),
            spawn_frame(float(SPAWN_TS)),
            frame_at(mid, 700, 500, 1, 1, 0, 0),
            frame_at(post_pause, 700, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.pause_count == 1
    assert result.pause_seconds == pytest.approx(20.0)
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[0].features.radiant_nw == 2500
    assert by_second[1].features.radiant_nw == 3500


def test_gold_progress_gap_is_not_pause(tmp_path: Path) -> None:
    """A 20s hole where gold moved is a feed hole: later seconds are not shifted."""
    windows = tmp_path / "windows"
    loading = float(SPAWN_TS - 6)
    mid = float(SPAWN_TS + 1)
    post_hole = float(SPAWN_TS + 21)
    write_archive(
        windows,
        "1001",
        [
            frame_at(loading, 0, 0, 1, 1, 0, 0),
            spawn_frame(float(SPAWN_TS)),
            frame_at(mid, 600, 500, 1, 1, 0, 0),
            frame_at(post_hole, 700, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.pause_count == 0
    assert result.pause_seconds == pytest.approx(0.0)
    seconds = {row.second for row in result.grid_rows}
    assert 3 in seconds
    assert 4 not in seconds
    assert 20 not in seconds
    assert 21 in seconds
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[1].features.radiant_nw == 3000
    assert by_second[21].features.radiant_nw == 3500


def test_hp_progress_gap_is_not_pause(tmp_path: Path) -> None:
    """Gold and kills frozen but HP moved: early-game hole, not a pause."""
    windows = tmp_path / "windows"
    loading = float(SPAWN_TS - 6)
    mid = float(SPAWN_TS + 1)
    post_hole = float(SPAWN_TS + 21)
    write_archive(
        windows,
        "1001",
        [
            frame_at(loading, 0, 0, 1, 1, 0, 0),
            spawn_frame(float(SPAWN_TS)),
            frame_at(mid, 500, 500, 1, 1, 0, 0),
            set_participant_hp(frame_at(post_hole, 500, 500, 1, 1, 0, 0), 900),
            progress_frame(post_hole + 1.0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.pause_count == 0
    assert 21 in {row.second for row in result.grid_rows}


def test_grid_latest_frame_not_later_than_second(tmp_path: Path) -> None:
    """Frames at 0.0 and 1.4: S=0 and S=1 use 0.0; S=2 uses 1.4 (age 0.6)."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 1.4, 600, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[0].features.radiant_nw == 2500
    assert by_second[1].features.radiant_nw == 2500
    assert by_second[2].features.radiant_nw == 3000


def test_age_gate_drops_seconds_not_map(tmp_path: Path) -> None:
    """A 3.5s hole under the pause threshold drops S=3 only."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 3.5, 600, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    seconds = {row.second for row in result.grid_rows}
    assert 2 in seconds
    assert 3 not in seconds
    assert 4 in seconds
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[2].features.radiant_nw == 2500
    assert by_second[4].features.radiant_nw == 3000


def test_features_from_selected_frame(tmp_path: Path) -> None:
    """Gold, deaths, top1 ratio, and XP come from the selected frame; ratio is max/team."""
    windows = tmp_path / "windows"
    later = frame_at(float(SPAWN_TS) + 1.0, 700, 500, 3, 2, 1, 0)
    people = participants(700, 3, 1, 1)
    people[0]["totalGold"] = 1100
    people[1]["totalGold"] = 600
    people[2]["totalGold"] = 600
    people[3]["totalGold"] = 600
    people[4]["totalGold"] = 600
    later["blueTeam"] = team_side(3500, 0, people)
    write_archive(
        windows,
        "1001",
        [spawn_frame(float(SPAWN_TS)), later],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    feat = {row.second: row.features for row in result.grid_rows}[1]
    assert feat.radiant_nw == 3500
    assert feat.dire_nw == 2500
    assert feat.radiant_nw_adv == 1000
    assert feat.deaths_radiant == 5
    assert feat.deaths_dire == 0
    assert feat.top.top1_nw_adv == 1100 - 500
    assert feat.top.radiant_top1_nw_ratio == pytest.approx(1100 / 3500)
    assert feat.top.dire_top1_nw_ratio == pytest.approx(500 / 2500)
    assert feat.top.top3_nw_adv == 1100 + 600 + 600 - 1500
    assert feat.top.radiant_top3_nw_ratio == pytest.approx((1100 + 600 + 600) / 3500)
    assert feat.top.dire_top3_nw_ratio == pytest.approx(1500 / 2500)
    assert feat.radiant_xp_adv == 5 * (660 - 280)


def test_consumed_item_corrects_team_and_top1_features(tmp_path: Path) -> None:
    """Consuming a potion lowers Blue net worth while raw totalGold stays 500."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    pending = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    spent = frame_at(float(SPAWN_TS) + 4.0, 500, 500, 1, 1, 0, 0)
    progressed = progress_frame(float(SPAWN_TS) + 5.0)
    write_archive(windows, "1001", [spawn, pending, spent, progressed])
    fetch = load_fetch()
    spawn_stamp = stamp_at(float(SPAWN_TS))
    pending_stamp = stamp_at(float(SPAWN_TS) + 1.0)
    spent_stamp = stamp_at(float(SPAWN_TS) + 4.0)
    progressed_stamp = stamp_at(float(SPAWN_TS) + 5.0)
    with_potion: list[dict[str, object]] = [
        {"participantId": participant_id, "items": [2003] if participant_id == 1 else []}
        for participant_id in range(1, 11)
    ]
    empty: list[dict[str, object]] = [
        {"participantId": participant_id, "items": []} for participant_id in range(1, 11)
    ]
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows.parent / "details", "1001"),
        [
            {
                "frames": [
                    {"rfc460Timestamp": spawn_stamp, "participants": with_potion},
                    {"rfc460Timestamp": pending_stamp, "participants": empty},
                    {"rfc460Timestamp": spent_stamp, "participants": empty},
                    {"rfc460Timestamp": progressed_stamp, "participants": empty},
                ]
            }
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    feat = {row.second: row.features for row in result.grid_rows}[4]
    assert feat.radiant_nw == 2450
    assert feat.dire_nw == 2500
    assert feat.radiant_nw_adv == -50
    assert feat.top.top1_nw_adv == 0
    assert feat.top.radiant_top1_nw_ratio == pytest.approx(500 / 2450)
    assert feat.top.dire_top1_nw_ratio == pytest.approx(500 / 2500)
    assert feat.top.top3_nw_adv == 0
    assert feat.top.radiant_top3_nw_ratio == pytest.approx(1500 / 2450)
    assert feat.top.dire_top3_nw_ratio == pytest.approx(1500 / 2500)


def test_missing_patch_version_drops_the_map(tmp_path: Path) -> None:
    """No gameMetadata.patchVersion is a per-map drop, same family as no_details."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    fetch = load_fetch()
    path = fetch.archive_path(windows, "1001")
    body = window_body("1001", [spawn_frame(float(SPAWN_TS))])
    del cast(dict[str, object], body["gameMetadata"])["patchVersion"]
    fetch.write_gzip_jsonl(path, [body])
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_NO_PATCH_VERSION


def test_conflicting_patch_version_drops_the_map(tmp_path: Path) -> None:
    """Two envelopes with different patchVersion values drop the map."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    later = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    write_archive(windows, "1001", [spawn, later])
    fetch = load_fetch()
    first = window_body("1001", [spawn])
    second = window_body("1001", [later])
    cast(dict[str, object], second["gameMetadata"])["patchVersion"] = "15.8.1"
    fetch.write_gzip_jsonl(fetch.archive_path(windows, "1001"), [first, second])
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_NO_PATCH_VERSION


def test_two_patches_price_the_same_consumed_item_differently(tmp_path: Path) -> None:
    """Same potion drop reconstructs different net worth when patches disagree on price."""
    cheap = ItemTable("16.16.1", frozenset({1001, 2003}), {2003: 50})
    expensive = ItemTable("15.8.1", frozenset({1001, 2003}), {2003: 150})
    catalog = ItemCatalog({cheap.version: cheap, expensive.version: expensive})
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    pending = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    spent = frame_at(float(SPAWN_TS) + 4.0, 500, 500, 1, 1, 0, 0)
    progressed = progress_frame(float(SPAWN_TS) + 5.0)
    frames = [spawn, pending, spent, progressed]
    write_archive(windows, "1001", frames)
    write_archive(windows, "1002", frames)
    fetch = load_fetch()
    body_15 = window_body("1002", frames)
    cast(dict[str, object], body_15["gameMetadata"])["patchVersion"] = "15.8.1"
    fetch.write_gzip_jsonl(fetch.archive_path(windows, "1002"), [body_15])
    spawn_stamp = stamp_at(float(SPAWN_TS))
    pending_stamp = stamp_at(float(SPAWN_TS) + 1.0)
    spent_stamp = stamp_at(float(SPAWN_TS) + 4.0)
    progressed_stamp = stamp_at(float(SPAWN_TS) + 5.0)
    with_potion: list[dict[str, object]] = [
        {"participantId": participant_id, "items": [2003] if participant_id == 1 else []}
        for participant_id in range(1, 11)
    ]
    empty: list[dict[str, object]] = [
        {"participantId": participant_id, "items": []} for participant_id in range(1, 11)
    ]
    details = [
        {
            "frames": [
                {"rfc460Timestamp": spawn_stamp, "participants": with_potion},
                {"rfc460Timestamp": pending_stamp, "participants": empty},
                {"rfc460Timestamp": spent_stamp, "participants": empty},
                {"rfc460Timestamp": progressed_stamp, "participants": empty},
            ]
        }
    ]
    fetch.write_gzip_jsonl(fetch.archive_path(windows.parent / "details", "1001"), details)
    fetch.write_gzip_jsonl(fetch.archive_path(windows.parent / "details", "1002"), details)
    first = prepare_map_livestats_until(
        accepted_link("1001", "e1", 1),
        windows,
        windows.parent / "details",
        catalog,
        LOL_PREPARE_END_SECOND,
    )
    second = prepare_map_livestats_until(
        accepted_link("1002", "e1", 2),
        windows,
        windows.parent / "details",
        catalog,
        LOL_PREPARE_END_SECOND,
    )
    assert isinstance(first, LivestatsOk)
    assert isinstance(second, LivestatsOk)
    cheap_nw = {row.second: row.features for row in first.grid_rows}[4].radiant_nw
    expensive_nw = {row.second: row.features for row in second.grid_rows}[4].radiant_nw
    assert cheap_nw == 2450
    assert expensive_nw == 2350


def test_window_details_stamp_mismatch_skips_the_frame(tmp_path: Path) -> None:
    """A window stamp missing from details is skipped; surrounding frames stay."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    later = [progress_frame(float(SPAWN_TS) + offset) for offset in range(1, 5)]
    extra = progress_frame(float(SPAWN_TS) + 5.0)
    write_archive(windows, "1001", [spawn, *later, extra])
    fetch = load_fetch()
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows.parent / "details", "1001"),
        [details_body([spawn, *later])],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.skipped_stamp_rows == 1
    assert result.skipped_invariant_rows == 0


def test_extra_details_stamps_keep_the_map(tmp_path: Path) -> None:
    """Details may have extra frames; the map is kept when window stamps are present."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    later = progress_frame(float(SPAWN_TS) + 1.0)
    extra = progress_frame(float(SPAWN_TS) + 2.0)
    write_archive(windows, "1001", [spawn, later])
    fetch = load_fetch()
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows.parent / "details", "1001"),
        [details_body([spawn, later, extra])],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)


def test_all_stamps_missing_from_details_is_zero_usable_frames(tmp_path: Path) -> None:
    """Every window stamp missing from details drops as zero_usable_frames."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    fetch = load_fetch()
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows.parent / "details", "1001"),
        [{"frames": []}],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_ZERO_USABLE_FRAMES


def test_skipped_frame_fraction_above_threshold_drops_the_map(tmp_path: Path) -> None:
    """A gold rollback that skips most later frames drops the map."""
    windows = tmp_path / "windows"
    spawn = float(SPAWN_TS)
    frames = [spawn_frame(spawn), progress_frame(spawn + 1.0)]
    for offset in range(2, 8):
        frames.append(frame_at(spawn + offset, 400, 500, 1, 1, 0, 0))
    write_archive(windows, "1001", frames)
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_LIVESTATS_INVARIANT_VIOLATION
    assert result.invariant_rule == 2
    assert result.skipped_invariant_rows == 6


def test_negative_net_worth_drops_the_map(tmp_path: Path) -> None:
    """Consumed gold above raw totalGold drops the map, not the whole prepare."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    pending = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    spent = frame_at(float(SPAWN_TS) + 4.0, 500, 500, 1, 1, 0, 0)
    write_archive(windows, "1001", [spawn, pending, spent])
    fetch = load_fetch()
    spawn_stamp = stamp_at(float(SPAWN_TS))
    pending_stamp = stamp_at(float(SPAWN_TS) + 1.0)
    spent_stamp = stamp_at(float(SPAWN_TS) + 4.0)
    spawn_participants: list[dict[str, object]] = [
        {"participantId": participant_id, "items": [2003] * 11 if participant_id == 1 else []}
        for participant_id in range(1, 11)
    ]
    empty: list[dict[str, object]] = [
        {"participantId": participant_id, "items": []} for participant_id in range(1, 11)
    ]
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows.parent / "details", "1001"),
        [
            {
                "frames": [
                    {"rfc460Timestamp": spawn_stamp, "participants": spawn_participants},
                    {"rfc460Timestamp": pending_stamp, "participants": empty},
                    {"rfc460Timestamp": spent_stamp, "participants": empty},
                ]
            }
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_NEGATIVE_NET_WORTH


def test_participant_id_outside_1_to_10_is_no_spawn_frame(tmp_path: Path) -> None:
    """Blue participantId 99 cannot be a spawn frame."""
    windows = tmp_path / "windows"
    frame = spawn_frame(float(SPAWN_TS))
    blue = cast(dict[str, object], frame["blueTeam"])
    people = cast(list[dict[str, object]], blue["participants"])
    people[0]["participantId"] = 99
    write_archive(windows, "1001", [frame])
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_NO_SPAWN_FRAME


def _mutate_spawn(rule: int) -> dict[str, object]:
    """One spawn frame that fails exactly one invariant rule."""
    frame = spawn_frame(float(SPAWN_TS))
    blue = cast(dict[str, object], frame["blueTeam"])
    people = cast(list[dict[str, object]], blue["participants"])
    if rule == 1:
        people[0]["totalGold"] = 501
        blue["totalGold"] = 2501
        return frame
    if rule == 3:
        blue["totalGold"] = 2501
        return frame
    if rule == 4:
        blue["totalKills"] = 1
        return frame
    if rule == 5:
        people[0]["level"] = 21
        return frame
    if rule == 6:
        people[0]["deaths"] = -1
        red = cast(dict[str, object], frame["redTeam"])
        red["totalKills"] = -1
        return frame
    if rule == 7:
        blue["participants"] = people[:4]
        blue["totalGold"] = 2000
        return frame
    return frame


def test_each_invariant_drops_the_map(tmp_path: Path) -> None:
    """Spawn-only archives that never yield a valid post-spawn frame are dropped."""
    windows = tmp_path / "windows"
    for rule in (1, 5, 6, 7):
        write_archive(windows, f"10{rule}", [_mutate_spawn(rule)])
        result = map_livestats(accepted_link(f"10{rule}", "e1", rule), windows)
        assert not isinstance(result, LivestatsOk)
        assert result.reason == REASON_NO_SPAWN_FRAME
    for rule in (3, 4):
        write_archive(windows, f"10{rule}", [_mutate_spawn(rule)])
        result = map_livestats(accepted_link(f"10{rule}", "e1", rule), windows)
        assert not isinstance(result, LivestatsOk)
        assert result.reason == REASON_LIVESTATS_INVARIANT_VIOLATION
        assert result.invariant_rule == rule


def test_isolated_zero_frame_keeps_the_map(tmp_path: Path) -> None:
    """A glitch frame is skipped; surrounding spawn-then-progress frames stay."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 1.0, 0, 0, 1, 1, 0, 0),
            progress_frame(float(SPAWN_TS) + 2.0),
            progress_frame(float(SPAWN_TS) + 3.0),
            progress_frame(float(SPAWN_TS) + 4.0),
            progress_frame(float(SPAWN_TS) + 5.0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.skipped_invariant_rows == 1
    by_second = {row.second: row for row in result.grid_rows}
    assert by_second[0].features.radiant_nw == 2500
    assert by_second[2].features.radiant_nw == 3000


def test_rollback_without_recovery_is_aborted_feed(tmp_path: Path) -> None:
    """Skipping every post-spawn glitch leaves only spawn stats: aborted_feed."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 1.0, 400, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_ABORTED_FEED


def test_aborted_feed_drops_spawn_only_archive(tmp_path: Path) -> None:
    """A map that never leaves 10x500 / lvl 1 / deaths 0 is aborted_feed."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 17.0, 500, 500, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_ABORTED_FEED


def test_cashback_undo_one_player_keeps_the_map(tmp_path: Path) -> None:
    """One player losing at most the Cash Back refund is not a feed rollback."""
    windows = tmp_path / "windows"
    later = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    blue = cast(dict[str, object], later["blueTeam"])
    people = cast(list[dict[str, object]], blue["participants"])
    people[0]["totalGold"] = 500 - LOL_CASHBACK_UNDO_MAX_GOLD
    blue["totalGold"] = 2500 - LOL_CASHBACK_UNDO_MAX_GOLD
    write_archive(
        windows,
        "1001",
        [spawn_frame(float(SPAWN_TS)), later, progress_frame(float(SPAWN_TS) + 2.0)],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)


def test_one_player_gold_drop_above_cashback_is_skipped(tmp_path: Path) -> None:
    """A single-player drop bigger than Cash Back is skipped; later progress keeps the map."""
    windows = tmp_path / "windows"
    later = frame_at(float(SPAWN_TS) + 1.0, 500, 500, 1, 1, 0, 0)
    blue = cast(dict[str, object], later["blueTeam"])
    people = cast(list[dict[str, object]], blue["participants"])
    people[0]["totalGold"] = 500 - (LOL_CASHBACK_UNDO_MAX_GOLD + 1)
    blue["totalGold"] = 2500 - (LOL_CASHBACK_UNDO_MAX_GOLD + 1)
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            later,
            progress_frame(float(SPAWN_TS) + 2.0),
            progress_frame(float(SPAWN_TS) + 3.0),
            progress_frame(float(SPAWN_TS) + 4.0),
            progress_frame(float(SPAWN_TS) + 5.0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)


def test_all_ten_cashback_sized_drop_is_skipped(tmp_path: Path) -> None:
    """All ten players dropping is skipped; leftover spawn stats are aborted_feed."""
    windows = tmp_path / "windows"
    dipped = 500 - LOL_CASHBACK_UNDO_MAX_GOLD
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(float(SPAWN_TS)),
            frame_at(float(SPAWN_TS) + 1.0, dipped, dipped, 1, 1, 0, 0),
        ],
    )
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsDrop)
    assert result.reason == REASON_ABORTED_FEED


def test_gold_drop_after_prepare_end_keeps_the_map(tmp_path: Path) -> None:
    """A totalGold dip after the prepare window is not an invariant drop."""
    windows = tmp_path / "windows"
    spawn = spawn_frame(float(SPAWN_TS))
    mid = frame_at(float(SPAWN_TS) + 1.0, 600, 600, 1, 1, 0, 0)
    late = frame_at(float(SPAWN_TS) + float(LOL_PREPARE_END_SECOND + 1), 500, 600, 1, 1, 0, 0)
    write_archive(windows, "1001", [spawn, mid, late])
    table = fixture_item_table()
    catalog = ItemCatalog({table.version: table})
    result = prepare_map_livestats_until(
        accepted_link("1001", "e1", 1),
        windows,
        windows.parent / "details",
        catalog,
        LOL_PREPARE_END_SECOND,
    )
    assert isinstance(result, LivestatsOk)
    assert result.last_game_time <= LOL_PREPARE_END_SECOND


def test_second_prepare_replaces_datasets(tmp_path: Path) -> None:
    """A second prepare with the same inputs replaces datasets."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    first = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    assert (first / "audit.parquet").is_file()
    second = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    assert (second / "audit.parquet").is_file()


def test_prior_window_boundaries(tmp_path: Path) -> None:
    """Spawn snapshot excluded; spawn-61s included; 1ms earlier excluded."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    link = accepted_link("1001", "e1", 1)
    spawn_us = SPAWN_TS * US_PER_SECOND
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    current_target = [
        (spawn_us, 0.60, 0.40),
        (spawn_us + lag_us, 0.60, 0.40),
        (spawn_us + 300 * US_PER_SECOND, 0.70, 0.30),
        (spawn_us + lag_us + 300 * US_PER_SECOND, 0.70, 0.30),
    ]
    telonex = tmp_path / "telonex-spawn"
    write_pair_books(telonex, current_target)
    write_min_tape(telonex, [spawn_us])
    out = run_prepare(tmp_path / "a", [link], windows, telonex)
    audit = read_parquet(out / "audit.parquet")
    assert audit.iloc[0]["reason"] == REASON_MISSING_PRIOR

    telonex_in = tmp_path / "telonex-in"
    write_pair_books(telonex_in, [(spawn_us - 61 * US_PER_SECOND, 0.60, 0.40), *current_target])
    write_min_tape(telonex_in, [spawn_us])
    out_in = run_prepare(tmp_path / "b", [link], windows, telonex_in)
    assert read_parquet(out_in / "audit.parquet").iloc[0]["reason"] == REASON_ACCEPTED

    telonex_out = tmp_path / "telonex-out"
    write_pair_books(
        telonex_out,
        [(spawn_us - 61 * US_PER_SECOND - 1000, 0.60, 0.40), *current_target],
    )
    out_out = run_prepare(tmp_path / "c", [link], windows, telonex_out)
    assert read_parquet(out_out / "audit.parquet").iloc[0]["reason"] == REASON_MISSING_PRIOR


def test_prior_one_sided_and_pair_sum(tmp_path: Path) -> None:
    """One-sided prior or pair-sum 1.06 drops the whole map."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    link = accepted_link("1001", "e1", 1)
    spawn_us = SPAWN_TS * US_PER_SECOND
    current_target = [
        (spawn_us, 0.60, 0.40),
        (spawn_us + 300 * US_PER_SECOND, 0.70, 0.30),
    ]
    telonex = tmp_path / "telonex"
    write_token_book(
        telonex,
        token_id=TOK_R,
        day="2026-01-01",
        rows=[
            (spawn_us - US_PER_SECOND, [{"price": 0.59, "size": 10.0}], []),
            (spawn_us, *two_sided(0.60)),
            (spawn_us + 300 * US_PER_SECOND, *two_sided(0.70)),
        ],
    )
    write_token_book(
        telonex,
        token_id=TOK_D,
        day="2026-01-01",
        rows=[
            (spawn_us - US_PER_SECOND, *two_sided(0.40)),
            (spawn_us, *two_sided(0.40)),
            (spawn_us + 300 * US_PER_SECOND, *two_sided(0.30)),
        ],
    )
    out = run_prepare(tmp_path / "one-sided", [link], windows, telonex)
    assert read_parquet(out / "audit.parquet").iloc[0]["reason"] == REASON_MISSING_PRIOR

    telonex_sum = tmp_path / "telonex-sum"
    write_pair_books(telonex_sum, [(spawn_us - US_PER_SECOND, 0.53, 0.53), *current_target])
    out_sum = run_prepare(tmp_path / "pair-sum", [link], windows, telonex_sum)
    assert read_parquet(out_sum / "audit.parquet").iloc[0]["reason"] == REASON_MISSING_PRIOR


def test_current_target_future_and_age(tmp_path: Path) -> None:
    """A book after the +11s join is ignored; age 6s drops that row; the next second stays."""
    windows = tmp_path / "windows"
    write_archive(
        windows,
        "1001",
        one_bucket_frames(float(SPAWN_TS)),
    )
    spawn_us = SPAWN_TS * US_PER_SECOND
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    write_pair_books(
        telonex,
        [
            (spawn_us - 6 * US_PER_SECOND, 0.60, 0.40),
            (spawn_us + lag_us - 6 * US_PER_SECOND, 0.60, 0.40),
            (spawn_us + lag_us + US_PER_SECOND, 0.60, 0.40),
            (spawn_us + lag_us + 300 * US_PER_SECOND, 0.70, 0.30),
            (spawn_us + lag_us + 301 * US_PER_SECOND, 0.70, 0.30),
        ],
    )
    write_min_tape(telonex, [spawn_us])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    rows = read_parquet(out / "production_training.parquet")
    seconds = set(int(value) for value in rows["second"])
    assert 0 not in seconds
    assert 1 in seconds
    audit = read_parquet(out / "audit.parquet").iloc[0]
    assert int(audit["skipped_market_rows"]) >= 1
    assert int(audit["dataset_rows"]) >= 1


def test_current_mid_ignores_quote_ten_seconds_ahead(tmp_path: Path) -> None:
    """Second 0 uses the +11s book. A print after that join is not the mid."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    spawn_us = SPAWN_TS * US_PER_SECOND
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    write_pair_books(
        telonex,
        [
            (spawn_us - US_PER_SECOND, 0.40, 0.60),
            (spawn_us + lag_us, 0.50, 0.50),
            (spawn_us + lag_us + US_PER_SECOND, 0.90, 0.10),
            (spawn_us + lag_us + 300 * US_PER_SECOND, 0.70, 0.30),
        ],
    )
    write_min_tape(telonex, [spawn_us])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    rows = read_parquet(out / "training.parquet")
    zero = rows.loc[rows["second"] == 0]
    assert len(zero) == 1
    assert abs(float(zero["market_p_radiant"].iloc[0]) - 0.50) < 1e-9


def test_label_columns_and_no_label(tmp_path: Path) -> None:
    """signal - market_p is the 300s delta; parquet has no label column."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    frame = read_parquet(out / "training.parquet")
    assert "label" not in frame.columns
    labeled = frame.loc[frame["signal_market_p_radiant_300s"].notna()]
    delta = labeled["signal_market_p_radiant_300s"] - labeled["market_p_radiant"]
    assert not labeled.empty
    assert all(abs(float(value) - 0.10) < 1e-9 for value in delta)
    assert "signal_market_p_radiant_10s" not in frame.columns
    assert "d_market_p_radiant_30" not in frame.columns
    assert "radiant_top_level" not in frame.columns
    assert not (tmp_path / "out" / "game_states.parquet").exists()


def test_labels_after_540_reach_training_and_production(tmp_path: Path) -> None:
    """A +300s label past second 540 lands in both datasets; train maps reach game_features."""
    windows = tmp_path / "windows"
    spawn = float(SPAWN_TS)
    write_archive(
        windows,
        "1001",
        [
            spawn_frame(spawn),
            progress_frame(spawn + 1.0),
            frame_at(spawn + 600.0, 800, 500, 1, 1, 0, 0),
            frame_at(spawn + 900.0, 900, 500, 1, 1, 0, 0),
        ],
    )
    spawn_us = SPAWN_TS * US_PER_SECOND
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    write_pair_books(
        telonex,
        [
            (spawn_us - US_PER_SECOND, 0.60, 0.40),
            (spawn_us + lag_us, 0.60, 0.40),
            (spawn_us + lag_us + 300 * US_PER_SECOND, 0.70, 0.30),
            (spawn_us + lag_us + 600 * US_PER_SECOND, 0.65, 0.35),
            (spawn_us + lag_us + 900 * US_PER_SECOND, 0.80, 0.20),
        ],
    )
    write_min_tape(telonex, [spawn_us])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    label = "signal_market_p_radiant_300s"
    production = read_parquet(out / "production_training.parquet")
    assert (production["second"] > BUY_CUTOFF_SECOND).any()
    labeled_late = production.loc[
        (production["second"] > BUY_CUTOFF_SECOND) & production[label].notna()
    ]
    assert not labeled_late.empty
    assert 600 in set(int(value) for value in labeled_late["second"])
    training = read_parquet(out / "training.parquet")
    assert ((training["second"] > BUY_CUTOFF_SECOND) & training[label].notna()).any()
    features = read_parquet(out / "game_features.parquet")
    assert (features["game_second"] > BUY_CUTOFF_SECOND).any()


def test_event_level_split_does_not_split_series(tmp_path: Path) -> None:
    """Two maps of one event straddling the cutoff both go to train."""
    windows = tmp_path / "windows"
    early = LOL_VALIDATION_START_TIME - 100
    late = LOL_VALIDATION_START_TIME + 100
    write_archive(windows, "4001", one_bucket_frames(float(early)))
    write_archive(windows, "4002", one_bucket_frames(float(late)))
    telonex = tmp_path / "telonex"
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    label_us = lag_us + 300 * US_PER_SECOND
    write_pair_books(
        telonex,
        [
            (early * US_PER_SECOND - US_PER_SECOND, 0.60, 0.40),
            (early * US_PER_SECOND, 0.60, 0.40),
            (early * US_PER_SECOND + lag_us, 0.60, 0.40),
            (early * US_PER_SECOND + 300 * US_PER_SECOND, 0.70, 0.30),
            (early * US_PER_SECOND + label_us, 0.70, 0.30),
            (late * US_PER_SECOND - US_PER_SECOND, 0.60, 0.40),
            (late * US_PER_SECOND, 0.60, 0.40),
            (late * US_PER_SECOND + lag_us, 0.60, 0.40),
            (late * US_PER_SECOND + 300 * US_PER_SECOND, 0.70, 0.30),
            (late * US_PER_SECOND + label_us, 0.70, 0.30),
        ],
    )
    write_min_tape(telonex, [early * US_PER_SECOND, late * US_PER_SECOND])
    links = [
        make_link("4001", "same-event", 1, early - 4, "T1", 0),
        make_link("4002", "same-event", 2, late - 4, "T1", 0),
    ]
    out = run_prepare(tmp_path, links, windows, telonex)
    split = read_parquet(out / "split.parquet")
    assert set(split["split"]) == {"train"}
    assert len(split) == 2
    assert read_parquet(out / "validation.parquet").empty
    assert not read_parquet(out / "training.parquet").empty


def test_success_publishes_seven_files_without_tmp(tmp_path: Path) -> None:
    """A successful run writes exactly the seven outputs and no .tmp siblings."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    for path in output_files(out):
        assert path.is_file()
        assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert not (out / "game_states.parquet").exists()


def test_schema_density_and_match_id(tmp_path: Path) -> None:
    """All three dataset files match DatasetRow; match_id is unique int(esportsGameId)."""
    windows = tmp_path / "windows"
    write_archive(windows, "115548681803406125", one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    link = accepted_link("115548681803406125", "e1", 1)
    out = run_prepare(tmp_path, [link], windows, telonex)
    expected = LOL_DATASET_COLUMNS
    # Inherited fields annotate before the row's own keys; the parquet column
    # order lives in LOL_DATASET_COLUMNS, so compare names, not order.
    assert set(LolDatasetRow.__annotations__) == set(expected)
    assert len(LolDatasetRow.__annotations__) == len(expected)
    for name in ("training.parquet", "validation.parquet", "production_training.parquet"):
        frame = read_parquet(out / name)
        assert list(frame.columns) == expected
        if frame.empty:
            continue
        assert frame["second"].between(0, LOL_PREPARE_END_SECOND).all()
        counts = frame.groupby("match_id").size()
        assert int(counts.max()) <= LOL_PREPARE_END_SECOND + 1
    production = read_parquet(out / "production_training.parquet")
    match_ids = set(int(value) for value in production["match_id"])
    assert match_ids == {115548681803406125}


def test_prepare_prints_progress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Thread-pool prepare logs prepared: done/total before publishing."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    write_archive(windows, "1002", one_bucket_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    standard_books(telonex, SPAWN_TS * US_PER_SECOND)
    write_min_tape(telonex, [SPAWN_TS * US_PER_SECOND])
    run_prepare(
        tmp_path,
        [accepted_link("1001", "e1", 1), accepted_link("1002", "e2", 1)],
        windows,
        telonex,
    )
    captured = capsys.readouterr()
    assert "prepared: 2/2 accepted=" in captured.out


def test_empty_archive_is_no_livestats(tmp_path: Path) -> None:
    """Empty gzip archive drops the map as no_livestats."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", [])
    telonex = tmp_path / "telonex"
    (telonex / "book_snapshot_full").mkdir(parents=True)
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    audit = read_parquet(out / "audit.parquet")
    assert audit.iloc[0]["reason"] == REASON_NO_LIVESTATS
    assert bool(audit.iloc[0]["included"]) is False


def test_missing_books(tmp_path: Path) -> None:
    """Empty token partitions are a map-level drop."""
    windows = tmp_path / "windows"
    write_archive(windows, "1002", played_frames(float(SPAWN_TS)))
    telonex = tmp_path / "telonex"
    (telonex / "book_snapshot_full").mkdir(parents=True)
    out = run_prepare(tmp_path, [accepted_link("1002", "e2", 1)], windows, telonex)
    reasons = set(read_parquet(out / "audit.parquet")["reason"])
    assert reasons == {REASON_MISSING_BOOKS}
    assert read_parquet(out / "production_training.parquet").empty


def test_zero_usable_rows_without_current_target(tmp_path: Path) -> None:
    """Prior present but unusable current/target drops the map with zero_usable_rows."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    spawn_us = SPAWN_TS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    write_pair_books(telonex, [(spawn_us - 10 * US_PER_SECOND, 0.60, 0.40)])
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    audit = read_parquet(out / "audit.parquet")
    assert audit.iloc[0]["reason"] == REASON_ZERO_USABLE_ROWS
    assert int(audit.iloc[0]["skipped_market_rows"]) >= 1


def test_missing_roles_still_clocks_the_map(tmp_path: Path) -> None:
    """Livestats without gameMetadata roles still produce a spawn-clocked grid."""
    windows = tmp_path / "windows"
    windows.mkdir(parents=True)
    fetch = load_fetch()
    frames = played_frames(float(SPAWN_TS))
    fetch.write_gzip_jsonl(
        fetch.archive_path(windows, "1001"),
        [
            {
                "esportsGameId": "1001",
                "gameMetadata": {"patchVersion": "16.16.1"},
                "frames": frames,
            }
        ],
    )
    details_dir = windows.parent / "details"
    details_dir.mkdir(parents=True)
    fetch.write_gzip_jsonl(fetch.archive_path(details_dir, "1001"), [details_body(frames)])
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    assert result.grid_rows[0].features.radiant_nw == 2500


def test_current_without_300s_label_is_zero_labeled_rows(tmp_path: Path) -> None:
    """Train maps with no 300s label are dropped instead of padding the train count."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", played_frames(float(SPAWN_TS)))
    spawn_us = SPAWN_TS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    write_pair_books(
        telonex,
        [
            (spawn_us - US_PER_SECOND, 0.60, 0.40),
            (spawn_us, 0.60, 0.40),
            (spawn_us + lag_us, 0.60, 0.40),
        ],
    )
    out = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    audit = read_parquet(out / "audit.parquet").iloc[0]
    assert audit["reason"] == REASON_ZERO_LABELED_ROWS
    assert bool(audit["included"]) is False
    assert read_parquet(out / "training.parquet").empty


def test_training_parquet_has_only_the_300_second_label(tmp_path: Path) -> None:
    """Canonical Stage 05 publishes no alternate target columns."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    spawn_us = SPAWN_TS * US_PER_SECOND
    telonex = tmp_path / "telonex"
    lag_us = LOL_SOURCE_LAG_SECONDS * US_PER_SECOND
    write_pair_books(
        telonex,
        [
            (spawn_us - US_PER_SECOND, 0.60, 0.40),
            (spawn_us, 0.60, 0.40),
            (spawn_us + lag_us, 0.60, 0.40),
            (spawn_us + 300 * US_PER_SECOND, 0.70, 0.30),
            (spawn_us + lag_us + 300 * US_PER_SECOND, 0.70, 0.30),
        ],
    )
    write_min_tape(telonex, [spawn_us])
    output = run_prepare(tmp_path, [accepted_link("1001", "e1", 1)], windows, telonex)
    training = read_parquet(output / "training.parquet")

    labels = [column for column in training.columns if column.startswith("signal_market_")]
    assert labels == ["signal_market_p_radiant_300s"]


def test_stage05_cli_exposes_only_workers() -> None:
    """Stage 05 hides source paths and experimental label controls."""
    app = typer.Typer()
    app.command()(load_prepare().main)
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--workers" in result.output
    for removed in (
        "--accept-audit",
        "--links-path",
        "--output-dir",
        "--label-horizons",
        "--label-end-second",
    ):
        assert removed not in result.output


def test_build_game_feature_rows_keyed_by_game_second(tmp_path: Path) -> None:
    """Every grid slot becomes a model-input row; no outcome or market price."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", one_bucket_frames(float(SPAWN_TS)))
    result = map_livestats(accepted_link("1001", "e1", 1), windows)
    assert isinstance(result, LivestatsOk)
    rows = load_prepare().build_game_feature_rows(result, 0.61)
    assert [row["game_second"] for row in rows] == [slot.second for slot in result.grid_rows]
    assert {row["market_radiant_prior"] for row in rows} == {0.61}
    assert set(rows[0]) == set(DotaGameFeatureRow.__annotations__)


def test_game_features_keep_seconds_without_a_book(tmp_path: Path) -> None:
    """A grid second with no fresh quote lands in game_features but not validation."""
    windows = tmp_path / "windows"
    spawn_ts = LOL_VALIDATION_START_TIME + 100
    frames = [
        *one_bucket_frames(float(spawn_ts)),
        frame_at(float(spawn_ts) + 600.0, 800, 500, 1, 1, 0, 0),
    ]
    write_archive(windows, "1001", frames)
    spawn_us = spawn_ts * US_PER_SECOND
    telonex = tmp_path / "telonex"
    write_pair_books(
        telonex,
        [
            (spawn_us - US_PER_SECOND, 0.60, 0.40),
            (spawn_us, 0.60, 0.40),
            (spawn_us + LOL_SOURCE_LAG_SECONDS * US_PER_SECOND, 0.60, 0.40),
        ],
    )
    write_min_tape(telonex, [spawn_us, spawn_us + 300 * US_PER_SECOND])
    write_token_tape(telonex, token_id=TOK_D, timestamps_us=[spawn_us])
    day = datetime.fromtimestamp(spawn_ts, tz=UTC).date().isoformat()
    for path in telonex.rglob("2026-01-01.parquet"):
        path.rename(path.with_name(f"{day}.parquet"))
    out = run_prepare(
        tmp_path, [make_link("1001", "e1", 1, spawn_ts - 4, "T1", 0)], windows, telonex
    )
    audit = read_parquet(out / "audit.parquet").iloc[0]
    assert audit["reason"] == REASON_ACCEPTED
    assert bool(audit["included"]) is True
    features = read_parquet(out / "game_features.parquet")
    validation = read_parquet(out / "validation.parquet")
    assert {int(value) for value in features["game_second"]} == {
        0,
        1,
        2,
        3,
        300,
        301,
        302,
        600,
        601,
        602,
    }
    assert {int(value) for value in validation["second"]} == {0, 1, 2, 3}
    assert list(features.columns) == list(DotaGameFeatureRow.__annotations__)
    production = read_parquet(out / "production_training.parquet")
    assert set(production["second"]) == {0, 1, 2, 3}
    assert set(features["game_second"]) - set(production["second"]) == {
        300,
        301,
        302,
        600,
        601,
        602,
    }
    assert read_parquet(out / "training.parquet").empty


def test_join_market_rows_uses_source_lag_for_current_and_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current mid is +11s and the 300s label is +311s; the frame second stays."""
    offsets: list[int] = []

    def fake_lookup(
        _radiant: object, _dire: object, state_wall_us: int, offset_seconds: int
    ) -> float:
        assert state_wall_us == 5_000_000
        offsets.append(offset_seconds)
        return 0.4

    prepare = import_module("lol.05_prepare_dataset")
    monkeypatch.setattr(prepare, "lookup_market_p_after", fake_lookup)
    top = TopPlayerFeatures(
        top1_nw_adv=0,
        radiant_top1_nw_ratio=0.5,
        dire_top1_nw_ratio=0.5,
        top3_nw_adv=0,
        radiant_top3_nw_ratio=0.5,
        dire_top3_nw_ratio=0.5,
    )
    features = FrameFeatures(1, 1, 0, 0, 0, 0, top)
    slot = GridRow(second=10, state_wall_us=5_000_000, features=features)
    livestats = LivestatsOk(
        match_id=1,
        start_time=1,
        spawn_wall_seconds=1.0,
        spawn_us=1_000_000,
        pause_count=0,
        pause_seconds=0.0,
        frame_count=1,
        grid_rows=(slot,),
        skipped_age_rows=0,
        skipped_invariant_rows=0,
        skipped_stamp_rows=0,
        last_game_time=10.0,
        last_frame_wall_seconds=11.0,
        pauses=(),
    )
    link = cast(
        LolLinkRow,
        {"event_id": "e", "resolved_outcome_index": 0, "radiant_token_index": 0},
    )
    empty = TokenBook(timestamps_us=(), bids=(), asks=())
    joined = prepare.join_market_rows(livestats, link, 0.5, empty, empty)
    assert offsets == [
        LOL_SOURCE_LAG_SECONDS,
        LOL_SOURCE_LAG_SECONDS + LOL_TARGET_HORIZON_SECONDS,
    ]
    assert joined.rows[0]["second"] == 10
    assert joined.rows[0]["state_ts_us"] == 5_000_000
    window = book_load_window(livestats, replay_bounds(livestats), livestats.grid_rows)
    assert (
        window.end_us
        >= slot.state_wall_us
        + (LOL_TARGET_HORIZON_SECONDS + LOL_SOURCE_LAG_SECONDS) * US_PER_SECOND
    )
