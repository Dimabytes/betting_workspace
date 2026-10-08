import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from fetch_lol_item_tables import ddragon_versions_for_patches, write_item_table
from reconstruct_lol_networth import item_table_for_recording

from lol.networth import (
    DEFAULT_ITEM_CATALOG_DIR,
    ItemCatalog,
    ItemTable,
    MajorMinor,
    build_consumed_timeline,
    compact_ddragon_items,
    load_item_catalog,
    load_item_table,
    table_for_game_patch,
)


def details_frame(stamp: str, participant_one_items: list[int]) -> Mapping[str, object]:
    """Build one ten-participant details frame."""
    participants: list[dict[str, object]] = []
    for participant_id in range(1, 11):
        items = participant_one_items if participant_id == 1 else []
        participants.append({"participantId": participant_id, "items": items})
    return {"rfc460Timestamp": stamp, "participants": participants}


def compact_table(version: str, known: list[int], consumed: dict[int, int]) -> ItemTable:
    """One compact table for catalog files."""
    return ItemTable(version, frozenset(known), consumed)


def write_catalog(directory: Path, tables: list[ItemTable]) -> Path:
    """Write one json file per table under directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for table in tables:
        write_item_table(directory / f"{table.version}.json", table)
    return directory


def test_consumed_items_accumulate_by_participant() -> None:
    """A consumed-item drop spends only after 3s of stable absence."""
    table = ItemTable(
        version="16.16.1",
        known_item_ids=frozenset({1001, 2003}),
        consumed_item_gold={2003: 50},
    )
    timeline = build_consumed_timeline(
        [
            details_frame("2026-01-01T00:00:00.000Z", [1001, 2003]),
            details_frame("2026-01-01T00:00:01.000Z", [1001]),
            details_frame("2026-01-01T00:00:04.000Z", [1001]),
        ],
        table,
    )
    assert timeline.by_stamp["2026-01-01T00:00:00.000Z"].by_participant[1] == 0
    assert timeline.by_stamp["2026-01-01T00:00:01.000Z"].by_participant[1] == 0
    assert timeline.by_stamp["2026-01-01T00:00:04.000Z"].by_participant[1] == 50


def test_consumable_flicker_under_three_seconds_is_not_spent() -> None:
    """Gone then back inside 3s is shop flicker, not consumption."""
    table = ItemTable(
        version="16.16.1",
        known_item_ids=frozenset({2003}),
        consumed_item_gold={2003: 50},
    )
    timeline = build_consumed_timeline(
        [
            details_frame("2026-01-01T00:00:00.000Z", [2003]),
            details_frame("2026-01-01T00:00:01.000Z", []),
            details_frame("2026-01-01T00:00:02.000Z", [2003]),
        ],
        table,
    )
    assert timeline.by_stamp["2026-01-01T00:00:00.000Z"].by_participant[1] == 0
    assert timeline.by_stamp["2026-01-01T00:00:01.000Z"].by_participant[1] == 0
    assert timeline.by_stamp["2026-01-01T00:00:02.000Z"].by_participant[1] == 0


def test_consumable_partial_stack_return_before_three_seconds() -> None:
    """A stack that drops then partially returns inside 3s does not spend."""
    table = ItemTable(
        version="16.16.1",
        known_item_ids=frozenset({2003}),
        consumed_item_gold={2003: 50},
    )
    timeline = build_consumed_timeline(
        [
            details_frame("2026-01-01T00:00:00.000Z", [2003, 2003]),
            details_frame("2026-01-01T00:00:01.000Z", []),
            details_frame("2026-01-01T00:00:02.000Z", [2003]),
        ],
        table,
    )
    assert timeline.by_stamp["2026-01-01T00:00:02.000Z"].by_participant[1] == 0


def test_unknown_item_id_is_fatal() -> None:
    """An unknown item cannot be treated as non-consumed."""
    table = ItemTable("16.16.1", frozenset({2003}), {2003: 50})
    frames = [details_frame("2026-01-01T00:00:00.000Z", [999999])]
    with pytest.raises(ValueError, match="unknown item id 999999"):
        build_consumed_timeline(frames, table)


def test_compact_ddragon_items_keeps_ids_and_consumed_gold() -> None:
    """consumed is True takes gold.total; every id is known."""
    table = compact_ddragon_items(
        {
            "version": "16.16.1",
            "data": {
                "1001": {"gold": {"total": 300}},
                "2003": {"consumed": True, "gold": {"total": 50}},
                "2010": {"consumed": False, "gold": {"total": 50}},
            },
        }
    )
    assert table.version == "16.16.1"
    assert table.known_item_ids == frozenset({1001, 2003, 2010})
    assert table.consumed_item_gold == {2003: 50}


def test_game_patch_maps_to_matching_ddragon_not_prefix() -> None:
    """16.16.809.3269 is 16.16.1; 16.1 must not steal 16.16.1."""
    catalog = ItemCatalog(
        {
            "16.16.1": compact_table("16.16.1", [2003], {2003: 50}),
            "16.1.1": compact_table("16.1.1", [2003], {2003: 75}),
        }
    )
    assert table_for_game_patch(catalog, "16.16.809.3269").version == "16.16.1"
    assert table_for_game_patch(catalog, "16.1.736.4955").version == "16.1.1"
    assert table_for_game_patch(catalog, "16.1.736.4955").version != "16.16.1"


def test_table_for_game_patch_missing_is_fatal() -> None:
    """A game patch with no catalog coverage aborts instead of falling back."""
    catalog = ItemCatalog({"16.16.1": compact_table("16.16.1", [2003], {2003: 50})})
    with pytest.raises(ValueError, match="no item table for game patch"):
        table_for_game_patch(catalog, "15.8.1")


def test_load_item_catalog_rejects_duplicates_and_invalid_files(tmp_path: Path) -> None:
    """Duplicate version, duplicate major.minor, and invalid files are fatal."""
    write_catalog(
        tmp_path / "empty",
        [],
    )
    (tmp_path / "empty").mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="empty"):
        load_item_catalog(tmp_path / "empty")

    first = tmp_path / "dup"
    write_catalog(first, [compact_table("16.16.1", [1], {})])
    write_item_table(first / "copy.json", compact_table("16.16.1", [2], {}))
    with pytest.raises(ValueError, match="duplicate version"):
        load_item_catalog(first)

    two_builds = tmp_path / "line"
    write_catalog(
        two_builds,
        [compact_table("16.16.1", [1], {}), compact_table("16.16.2", [1], {})],
    )
    with pytest.raises(ValueError, match="two tables for"):
        load_item_catalog(two_builds)

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.json").write_text(
        '{"known_item_ids": [1], "consumed_item_gold": {}}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no version"):
        load_item_catalog(bad)


def test_load_item_table_rejects_invalid_payloads(tmp_path: Path) -> None:
    """Missing version, duplicates, boolean prices, and unknown consumed ids are fatal."""
    path = tmp_path / "items.json"
    path.write_text('{"known_item_ids": [1], "consumed_item_gold": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="no version"):
        load_item_table(path)
    path.write_text(
        '{"version": "x", "known_item_ids": [1, 1], "consumed_item_gold": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_item_table(path)
    path.write_text(
        '{"version": "x", "known_item_ids": [1], "consumed_item_gold": {"1": true}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid price"):
        load_item_table(path)
    path.write_text(
        '{"version": "x", "known_item_ids": [1], "consumed_item_gold": {"1": -1}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid price"):
        load_item_table(path)
    path.write_text(
        '{"version": "x", "known_item_ids": [1], "consumed_item_gold": {"2": 10}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a known item"):
        load_item_table(path)


def test_pinned_item_catalog_identity() -> None:
    """Catalog loads, versions are unique, potion 2003 is 50 on 16.17.1."""
    catalog = load_item_catalog(DEFAULT_ITEM_CATALOG_DIR)
    versions = list(catalog.tables)
    assert versions == list(dict.fromkeys(versions))
    table = catalog.tables["16.17.1"]
    assert table.consumed_item_gold[2003] == 50


def test_ddragon_version_pick_is_token_match_plus_latest() -> None:
    """Newest-first list: 16.1 is 16.1.1, never 16.16.1, and latest is always kept."""
    selected = ddragon_versions_for_patches(
        frozenset({MajorMinor(16, 16), MajorMinor(16, 1)}),
        ["16.17.1", "16.16.1", "16.10.1", "16.1.1", "15.8.1"],
    )
    assert selected[0] == "16.17.1"
    assert "16.16.1" in selected
    assert "16.1.1" in selected
    assert "16.10.1" not in selected


def test_ddragon_version_pick_skips_legacy_lolpatch_names() -> None:
    """Old lolpatch_7.20 strings in versions.json are not a parse abort."""
    selected = ddragon_versions_for_patches(
        frozenset({MajorMinor(16, 16)}),
        ["16.17.1", "lolpatch_7.20", "16.16.1"],
    )
    assert selected == ["16.17.1", "16.16.1"]


def test_reconstruct_uses_catalog_patch_not_latest(tmp_path: Path) -> None:
    """A 16.16 recording must not pick the 16.17 table sitting in the same catalog."""
    catalog_dir = write_catalog(
        tmp_path / "ddragon_items",
        [
            compact_table("16.17.1", [2003], {2003: 99}),
            compact_table("16.16.1", [2003], {2003: 50}),
        ],
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "livestats.jsonl").write_text(
        json.dumps(
            {
                "payload": {
                    "gameMetadata": {"patchVersion": "16.16.809.3269"},
                    "frames": [],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    table = item_table_for_recording(run_dir, load_item_catalog(catalog_dir))
    assert table.version == "16.16.1"
    assert table.consumed_item_gold[2003] == 50
