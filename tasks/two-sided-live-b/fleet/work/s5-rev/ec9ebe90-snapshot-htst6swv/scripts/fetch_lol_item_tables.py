"""Download compact Data Dragon item tables for every patch in games.parquet.

Prepare never hits the network. Re-run this script when a new patch appears.

    make run F=scripts/fetch_lol_item_tables.py
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import httpx
import pandas as pd

from lol.constants import LOL_GAMES_PATH
from lol.networth import (
    DEFAULT_ITEM_CATALOG_DIR,
    ItemTable,
    MajorMinor,
    compact_ddragon_items,
    parse_major_minor,
)
from shared.utils.log import get_logger, setup_logging

logger = get_logger(__name__)

DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_ITEMS = "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json"


def patches_from_games(games_path: Path) -> frozenset[MajorMinor]:
    """Unique major.minor lines from games.parquet patch_version."""
    frame = pd.read_parquet(games_path, columns=["patch_version"])
    lines: set[MajorMinor] = set()
    for value in frame["patch_version"].tolist():
        if not isinstance(value, str) or not value:
            raise ValueError("games.parquet has a missing patch_version")
        lines.add(parse_major_minor(value))
    if not lines:
        raise ValueError(f"games.parquet has no patch_version values: {games_path}")
    return frozenset(lines)


def ddragon_versions_for_patches(
    patches: frozenset[MajorMinor], versions: Sequence[str]
) -> list[str]:
    """Newest Data Dragon build per needed X.Y, plus versions[0] for live GRID."""
    if not versions:
        raise ValueError("Data Dragon versions list is empty")
    selected: list[str] = []
    remaining = set(patches)
    latest = versions[0]
    selected.append(latest)
    remaining.discard(parse_major_minor(latest))
    for version in versions:
        try:
            line = parse_major_minor(version)
        except ValueError:
            continue
        if line not in remaining:
            continue
        selected.append(version)
        remaining.remove(line)
    if remaining:
        missing = ", ".join(
            f"{line.major}.{line.minor}"
            for line in sorted(remaining, key=lambda item: (item.major, item.minor))
        )
        raise ValueError(f"no Data Dragon version for patches: {missing}")
    return selected


def write_item_table(path: Path, table: ItemTable) -> None:
    """Write one compact table; keys of consumed_item_gold stay strings."""
    payload = {
        "version": table.version,
        "known_item_ids": sorted(table.known_item_ids),
        "consumed_item_gold": {
            str(item_id): price for item_id, price in sorted(table.consumed_item_gold.items())
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fetch_item_tables(games_path: Path, output_dir: Path) -> list[Path]:
    """GET versions.json and each needed item.json; write compact files in place."""
    patches = patches_from_games(games_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with httpx.Client(timeout=30.0) as client:
        versions_response = client.get(DDRAGON_VERSIONS)
        versions_response.raise_for_status()
        versions_raw: object = versions_response.json()
        if not isinstance(versions_raw, list):
            raise ValueError("Data Dragon versions.json is not a string list")
        versions: list[str] = []
        for raw_version in cast(list[object], versions_raw):
            if not isinstance(raw_version, str):
                raise ValueError("Data Dragon versions.json is not a string list")
            versions.append(raw_version)
        selected = ddragon_versions_for_patches(patches, versions)
        for version in selected:
            items_response = client.get(DDRAGON_ITEMS.format(version=version))
            items_response.raise_for_status()
            payload: object = items_response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"Data Dragon item.json for {version} is not an object")
            table = compact_ddragon_items(cast(dict[str, object], payload))
            path = output_dir / f"{table.version}.json"
            write_item_table(path, table)
            written.append(path)
            logger.info(
                "wrote %s (%d ids, %d consumed)",
                path.name,
                len(table.known_item_ids),
                len(table.consumed_item_gold),
            )
    return written


def main(argv: list[str]) -> int:
    """Download compact item tables for games.parquet patches plus latest Dragon."""
    setup_logging()
    parser = argparse.ArgumentParser(prog="fetch-lol-item-tables")
    parser.add_argument("--games-path", type=Path, default=LOL_GAMES_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ITEM_CATALOG_DIR)
    args = parser.parse_args(argv)
    games_path = cast(Path, args.games_path)
    output_dir = cast(Path, args.output_dir)
    written = fetch_item_tables(games_path, output_dir)
    logger.info("wrote %d item tables -> %s", len(written), output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
