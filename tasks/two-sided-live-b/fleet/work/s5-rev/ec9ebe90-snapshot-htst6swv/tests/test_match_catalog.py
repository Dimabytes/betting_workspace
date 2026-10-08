"""Join collect stage tables into the match catalog and keep stage paths private."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from catalog_fixtures import catalog_row

from collect.s06_publish_catalog import build_match_catalog_frame
from shared.types.opendota import OpenDotaPause
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row, load_match_catalog

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
STAGE_PATH_NAMES = (
    "OPENDOTA_LINKS_PATH",
    "MATCH_LINKS_PATH",
    "MATCH_LINK_AUDIT_PATH",
    "GRID_GAME_WINDOWS_PATH",
    "STRATZ_MATCH_INDEX_PATH",
    "PREGAME_QUOTES_PATH",
    "UNIVERSE_PATH",
)

EMPTY_GRID = pd.DataFrame(columns=["condition_id", "spawn_at"])
EMPTY_STRATZ = pd.DataFrame(
    columns=["match_id", "status", "start_time", "duration", "playback_available"]
)
EMPTY_PRIORS = pd.DataFrame(columns=["match_id", "radiant_prior"])
EMPTY_UNIVERSE = pd.DataFrame(
    columns=[
        "conditionId",
        "market_slug",
        "seconds_delay",
        "market_closed_at",
        "token_id_0",
        "token_id_1",
    ]
)

NO_PAUSES: dict[int, list[OpenDotaPause]] = {}
NO_ARCHIVE_PAUSES: dict[int, list[OpenDotaPause] | None] = {}
NO_WINNERS: dict[int, bool] = {}
WINNERS = {1: True, 2: True, 5: True, 6: True, 7: True, 9: True, 11: True}

GAMMA_ROW = {
    "market_slug": "slug",
    "seconds_delay": 3,
    "market_closed_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
    "token_id_0": "yes",
    "token_id_1": "no",
}


def link_row(
    match_id: int,
    condition_id: str,
    event_id: str | None = None,
    *,
    radiant_token_index: int = 0,
    link_source: str = "opendota",
    archive_id: str | None = None,
    archive_root: str | None = None,
    archive_feed_source: str | None = None,
    archive_horn_at_utc: str | None = None,
    archive_winner: str | None = None,
    schedule_fingerprint: str | None = None,
) -> dict[str, object]:
    """The columns `build_match_catalog_frame` selects from match_links."""
    return {
        "match_id": match_id,
        "map_condition_id": condition_id,
        "event_id": event_id if event_id is not None else f"event-{match_id}",
        "radiant_token_index": radiant_token_index,
        "link_source": link_source,
        "archive_id": archive_id,
        "archive_root": archive_root,
        "archive_feed_source": archive_feed_source,
        "archive_horn_at_utc": archive_horn_at_utc,
        "archive_winner": archive_winner,
        "schedule_fingerprint": schedule_fingerprint,
    }


def universe_frame(condition_id: str, slug: str) -> pd.DataFrame:
    return pd.DataFrame([{"conditionId": condition_id, **GAMMA_ROW, "market_slug": slug}])


def stratz_frame(match_id: int, *, duration: int | None = 100) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": match_id,
                "status": "usable",
                "start_time": 10,
                "duration": duration,
                "playback_available": True,
            }
        ]
    )


def priors_frame(match_id: int, prior: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame([{"match_id": match_id, "radiant_prior": prior}])


def test_stage_parquet_paths_are_collect_private() -> None:
    """Downstream src/ and scripts/ must not import collect stage parquet paths."""
    leaked: list[str] = []
    for root in (SRC_ROOT, SCRIPTS_ROOT):
        for path in root.rglob("*.py"):
            if root == SRC_ROOT:
                relative = path.relative_to(SRC_ROOT)
                if "collect" in relative.parts:
                    continue
            text = path.read_text(encoding="utf-8")
            for name in STAGE_PATH_NAMES:
                if re.search(rf"\b{name}\b", text):
                    leaked.append(f"{path.relative_to(root.parent)}:{name}")
    assert leaked == []


def test_catalog_keeps_complete_maps_and_drops_the_rest() -> None:
    """Usable STRATZ + prior + clocks + GRID + Gamma stay; a linked map missing those is dropped."""
    links = pd.DataFrame(
        [
            link_row(1, "cond-1", "event-1", radiant_token_index=1),
            link_row(2, "cond-2", "event-2"),
        ]
    )
    grid = pd.DataFrame(
        [
            {
                "condition_id": "cond-1",
                "spawn_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    stratz = stratz_frame(1)
    priors = pd.DataFrame([{"match_id": 1, "radiant_prior": 0.61}])
    universe = universe_frame("cond-1", "slug-1")

    frame = build_match_catalog_frame(
        links, grid, stratz, priors, universe, {1: []}, NO_ARCHIVE_PAUSES, WINNERS
    )
    assert list(frame["match_id"]) == [1]
    row = frame.iloc[0]
    assert row["condition_id"] == "cond-1"
    assert row["radiant_prior"] == 0.61
    assert row["market_slug"] == "slug-1"
    assert row["spawn_at"] == "2026-01-01T00:00:00Z"
    assert row["horn_at"] == "2026-01-01T00:01:30+00:00"
    assert row["horn_source"] == "grid_derived"
    assert row["ended_at"] == "2026-01-01T00:03:10+00:00"
    assert row["pauses_json"] == "[]"
    assert row["pauses_source"] == "opendota"
    assert bool(row["radiant_win"]) is True
    assert row["winner_source"] == "stratz"
    assert "start_time" not in frame.columns
    assert "grid_game_started_at" not in frame.columns
    assert "gamma_available" not in frame.columns
    assert "opendota_available" not in frame.columns


def test_catalog_drops_incomplete_grid_and_gamma() -> None:
    """A usable prior-backed map without GRID window or archive does not enter the catalog."""
    links = pd.DataFrame([link_row(9, "cond-9", "event-9")])
    stratz = pd.DataFrame(
        [
            {
                "match_id": 9,
                "status": "usable",
                "start_time": 10,
                "duration": 100,
                "playback_available": False,
            }
        ]
    )
    priors = priors_frame(9)
    frame = build_match_catalog_frame(
        links, EMPTY_GRID, stratz, priors, EMPTY_UNIVERSE, NO_PAUSES, NO_ARCHIVE_PAUSES, WINNERS
    )
    assert frame.empty


@pytest.mark.parametrize("missing", ["duration", "spawn_at", "pauses"])
def test_ended_at_check_covers_duration_grid_and_pauses(missing: str) -> None:
    """`ended_at` is empty unless duration, horn path and pauses are all present."""
    links = pd.DataFrame([link_row(7, "cond-7", "event-7")])
    grid = pd.DataFrame([{"condition_id": "cond-7", "spawn_at": "2026-01-01T00:00:00Z"}])
    stratz = stratz_frame(7)
    priors = priors_frame(7)
    universe = universe_frame("cond-7", "slug-7")
    pauses: dict[int, list[OpenDotaPause]] = {7: []}
    if missing == "duration":
        stratz = stratz.assign(duration=None)
    elif missing == "spawn_at":
        grid = EMPTY_GRID
    else:
        pauses = NO_PAUSES

    frame = build_match_catalog_frame(
        links, grid, stratz, priors, universe, pauses, NO_ARCHIVE_PAUSES, WINNERS
    )
    assert frame.empty


def test_archive_only_row_enters_with_archive_pauses() -> None:
    """An archive-admitted row without GRID window or OpenDota pauses uses its own timing."""
    links = pd.DataFrame(
        [
            link_row(
                11,
                "cond-11",
                "event-11",
                link_source="archive",
                archive_id="grid-1-m1",
                archive_root="trader",
                archive_feed_source="grid",
                archive_horn_at_utc="2026-01-01T00:01:30+00:00",
                archive_winner="radiant",
                schedule_fingerprint="fp-1",
            )
        ]
    )
    archive_pauses: dict[int, list[OpenDotaPause] | None] = {11: [{"time": 60, "duration": 20}]}

    frame = build_match_catalog_frame(
        links,
        EMPTY_GRID,
        stratz_frame(11, duration=100),
        priors_frame(11),
        universe_frame("cond-11", "slug-11"),
        NO_PAUSES,
        archive_pauses,
        WINNERS,
    )

    assert list(frame["match_id"]) == [11]
    row = frame.iloc[0]
    assert pd.isna(row["spawn_at"])
    assert row["horn_at"] == "2026-01-01T00:01:30+00:00"
    assert row["horn_source"] == "archive"
    assert row["pauses_source"] == "archive"
    assert row["pauses_json"] == '[{"time": 60, "duration": 20}]'
    assert row["ended_at"] == "2026-01-01T00:03:30+00:00"
    assert row["archive_id"] == "grid-1-m1"
    assert row["schedule_fingerprint"] == "fp-1"


def test_archive_row_without_any_pauses_drops() -> None:
    """An archive row whose pauses are unknown (unobserved boundary) loses ended_at."""
    links = pd.DataFrame(
        [
            link_row(
                11,
                "cond-11",
                "event-11",
                link_source="archive",
                archive_id="grid-1-m1",
                archive_root="trader",
                archive_feed_source="grid",
                archive_horn_at_utc="2026-01-01T00:01:30+00:00",
                archive_winner="radiant",
                schedule_fingerprint="fp-1",
            )
        ]
    )

    frame = build_match_catalog_frame(
        links,
        EMPTY_GRID,
        stratz_frame(11),
        priors_frame(11),
        universe_frame("cond-11", "slug-11"),
        NO_PAUSES,
        {11: None},
        WINNERS,
    )
    assert frame.empty


def test_archive_attached_row_prefers_archive_horn_over_derived() -> None:
    """An opendota+archive row takes the direct archive horn, not spawn + 90."""
    links = pd.DataFrame(
        [
            link_row(
                1,
                "cond-1",
                "event-1",
                link_source="opendota+archive",
                archive_id="grid-2-m1",
                archive_root="trader",
                archive_feed_source="grid",
                archive_horn_at_utc="2026-01-01T00:02:00+00:00",
                archive_winner="radiant",
                schedule_fingerprint="fp-2",
            )
        ]
    )
    grid = pd.DataFrame([{"condition_id": "cond-1", "spawn_at": "2026-01-01T00:00:00Z"}])

    frame = build_match_catalog_frame(
        links,
        grid,
        stratz_frame(1),
        priors_frame(1),
        universe_frame("cond-1", "slug-1"),
        {1: []},
        {1: []},
        WINNERS,
    )
    row = frame.iloc[0]
    assert row["spawn_at"] == "2026-01-01T00:00:00Z"
    assert row["horn_at"] == "2026-01-01T00:02:00+00:00"
    assert row["horn_source"] == "archive"
    assert row["pauses_source"] == "opendota"


def test_archive_winner_conflict_drops_the_row() -> None:
    """Archive winner contradicting STRATZ drops the row under winner_conflict."""
    links = pd.DataFrame(
        [
            link_row(
                11,
                "cond-11",
                "event-11",
                link_source="archive",
                archive_id="grid-3-m1",
                archive_root="trader",
                archive_feed_source="grid",
                archive_horn_at_utc="2026-01-01T00:01:30+00:00",
                archive_winner="dire",
                schedule_fingerprint="fp-3",
            )
        ]
    )

    frame = build_match_catalog_frame(
        links,
        EMPTY_GRID,
        stratz_frame(11),
        priors_frame(11),
        universe_frame("cond-11", "slug-11"),
        NO_PAUSES,
        {11: []},
        {11: True},
    )
    assert frame.empty


def test_catalog_drops_validation_age_rows_without_stratz_playback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """playback_available=False drops a validation-age row; a train-age row survives."""
    links = pd.DataFrame([link_row(9, "cond-9", "event-9"), link_row(8, "cond-8", "event-8")])
    grid = pd.DataFrame(
        [
            {"condition_id": "cond-9", "spawn_at": "2026-07-01T00:00:00Z"},
            {"condition_id": "cond-8", "spawn_at": "2026-01-01T00:00:00Z"},
        ]
    )
    stratz = pd.concat(
        [
            stratz_frame(9).assign(playback_available=False),
            stratz_frame(8).assign(playback_available=False),
        ]
    )
    priors = pd.concat([priors_frame(9), priors_frame(8)])
    universe = pd.concat([universe_frame("cond-9", "slug-9"), universe_frame("cond-8", "slug-8")])

    with caplog.at_level("INFO"):
        frame = build_match_catalog_frame(
            links,
            grid,
            stratz,
            priors,
            universe,
            {9: [], 8: []},
            NO_ARCHIVE_PAUSES,
            {9: True, 8: True},
        )

    assert list(frame["match_id"]) == [8]
    assert "catalog playback failed=1" in caplog.text


def test_load_match_catalog_round_trip(tmp_path: Path) -> None:
    """Published catalog parquet loads as typed rows with required GRID and Gamma."""
    links = pd.DataFrame([link_row(9, "cond-9", "event-9")])
    grid = pd.DataFrame(
        [
            {
                "condition_id": "cond-9",
                "spawn_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    stratz = pd.DataFrame(
        [
            {
                "match_id": 9,
                "status": "usable",
                "start_time": 10,
                "duration": 100,
                "playback_available": True,
            }
        ]
    )
    priors = priors_frame(9)
    universe = universe_frame("cond-9", "slug-9")
    frame = build_match_catalog_frame(
        links,
        grid,
        stratz,
        priors,
        universe,
        {9: [{"time": 10, "duration": 5}]},
        NO_ARCHIVE_PAUSES,
        WINNERS,
    )
    path = tmp_path / "match_catalog.parquet"
    frame.to_parquet(path, index=False)
    catalog = load_match_catalog(path)
    assert list(catalog) == [9]
    row = catalog[9]
    assert row.start_time == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    assert row.duration == 100
    assert row.radiant_prior == 0.5
    assert row.spawn_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert row.anchor_at == row.spawn_at
    assert row.horn_at == datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)
    assert row.pauses == [{"time": 10, "duration": 5}]
    assert row.ended_at == datetime(2026, 1, 1, 0, 3, 15, tzinfo=UTC)
    assert row.radiant_win is True
    assert row.gamma.slug == "slug-9"
    assert row.gamma.token_ids == ("yes", "no")


def test_archive_entry_anchors_on_horn_when_spawn_absent(tmp_path: Path) -> None:
    """A catalog row without spawn parses with anchor_at == horn_at."""
    horn = datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            catalog_row(
                9,
                horn_at=horn,
                archive_id="grid-9-m1",
                archive_root="trader",
                archive_feed_source="grid",
                schedule_fingerprint="fp-9",
                duration=100,
            )
        ]
    )
    path = tmp_path / "match_catalog.parquet"
    frame.to_parquet(path, index=False)
    row = load_match_catalog(path)[9]
    assert row.spawn_at is None
    assert row.anchor_at == horn
    assert row.start_time == int(horn.timestamp())
    assert row.archive_id == "grid-9-m1"


def test_load_match_catalog_orders_by_start_time(tmp_path: Path) -> None:
    rows = pd.DataFrame(
        [
            catalog_row(3, start_time=300),
            catalog_row(1, start_time=100),
            catalog_row(2, start_time=100),
        ]
    )
    path = tmp_path / "match_catalog.parquet"
    rows.to_parquet(path, index=False)

    assert list(load_match_catalog(path)) == [1, 2, 3]


def test_match_catalog_orders_unsorted_input() -> None:
    catalog = MatchCatalog(
        {
            3: catalog_entry_from_row(catalog_row(3, start_time=300)),
            1: catalog_entry_from_row(catalog_row(1, start_time=100)),
            2: catalog_entry_from_row(catalog_row(2, start_time=100)),
        }
    )
    assert list(catalog) == [1, 2, 3]


def test_dropped_rows_are_logged_with_their_match_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every dropped match id reaches the log; counts attribute the first failing check."""
    links = pd.DataFrame(
        [
            link_row(5, "cond-5", "event-5"),
            link_row(
                6,
                "cond-6",
                "event-6",
                link_source="archive",
                archive_id="arch-6",
                archive_root="trader",
                archive_horn_at_utc="2026-01-01T00:01:30+00:00",
            ),
        ]
    )
    stratz = pd.DataFrame(
        [
            {
                "match_id": 5,
                "status": "failed",
                "start_time": 10,
                "duration": 100,
                "playback_available": True,
            },
            {
                "match_id": 6,
                "status": "failed",
                "start_time": 10,
                "duration": 100,
                "playback_available": True,
            },
        ]
    )
    priors = priors_frame(6)

    with caplog.at_level("INFO"):
        frame = build_match_catalog_frame(
            links,
            EMPTY_GRID,
            stratz,
            priors,
            EMPTY_UNIVERSE,
            NO_PAUSES,
            NO_ARCHIVE_PAUSES,
            WINNERS,
        )

    assert frame.empty
    assert "catalog admission failed=1" in caplog.text
    assert "catalog stratz failed=1" in caplog.text
    assert "catalog ended_at failed=0" in caplog.text
    assert "catalog kept=0 dropped=2 match_ids=[5, 6]" in caplog.text
