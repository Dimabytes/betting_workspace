"""Readiness funnel: candidate -> link -> admission -> catalog -> split.

Read-only report over the stage parquets; each section skips itself when the
upstream artifact is absent. New archive-created links are candidates for
admission, not ready matches — the funnel is what keeps that distinction
visible.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archive_index.schedule import load_archive_pauses
from collect.common.paths import (
    GRID_GAME_WINDOWS_PATH,
    MATCH_LINK_AUDIT_PATH,
    MATCH_LINKS_PATH,
    OPENDOTA_LINKS_PATH,
    PREGAME_QUOTES_PATH,
    STRATZ_MATCH_INDEX_PATH,
    UNIVERSE_PATH,
)
from collect.common.window_ids import admitted_link_mask
from collect.s06_publish_catalog import (
    assemble_catalog_frame,
    first_failure_counts,
)
from shared.constants.paths import (
    ARCHIVE_INDEX_DIR,
    MATCH_CATALOG_PATH,
    RESEARCH_MODEL_SPLIT_PATH,
)
from shared.utils.opendota import try_load_opendota_pauses
from shared.utils.stratz import try_load_stratz_winners

INDEX_PATH = ARCHIVE_INDEX_DIR / "index.parquet"

CATALOG_INPUTS = (
    MATCH_LINKS_PATH,
    GRID_GAME_WINDOWS_PATH,
    STRATZ_MATCH_INDEX_PATH,
    PREGAME_QUOTES_PATH,
    UNIVERSE_PATH,
)


def report_archive_index() -> None:
    """Archives scanned/admitted plus the index's own refusal funnel."""
    if not INDEX_PATH.is_file():
        print("archives scanned / admitted    (missing data/archive_index/index.parquet)")
        return
    index = pd.read_parquet(INDEX_PATH)
    dota = index[index["game"] == "dota"] if "game" in index.columns else index
    admitted = int(dota["admission"].eq("admitted").sum())
    print(f"archives scanned / admitted    {len(dota)} / {admitted}")
    refusals = dota.loc[dota["admission"] != "admitted", "admission"].value_counts()
    for reason, count in sorted(refusals.items()):
        print(f"  index refusal {reason}: {int(count)}")


def report_match_links() -> None:
    """match_links totals, per-source split, and the merge audit funnel."""
    links = pd.read_parquet(MATCH_LINKS_PATH)
    sources = links["link_source"].value_counts()
    print(
        f"match_links                    {len(links)}"
        f"  (opendota {int(sources.get('opendota', 0))}"
        f" | archive {int(sources.get('archive', 0))}"
        f" | both {int(sources.get('opendota+archive', 0))})"
    )
    if not MATCH_LINK_AUDIT_PATH.is_file():
        return
    audit = pd.read_parquet(MATCH_LINK_AUDIT_PATH)
    new_links = int(audit["resolution"].eq("new_link").sum())
    print(f"  new links from archives      {new_links}  <- candidates, NOT ready matches")
    counts = audit["resolution"].value_counts()
    summary = ", ".join(f"{name} {int(count)}" for name, count in sorted(counts.items()))
    print(f"  audit: {summary}")


def report_admitted(links: pd.DataFrame) -> int:
    """`(window OR archive)` count — the dataset admission gate."""
    windows = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
    count = int(admitted_link_mask(links, windows).sum())
    print(f"admitted (window OR archive)   {count}")
    return count


def report_catalog_drops() -> None:
    """First-failing check per non-catalog link row, attributed like s06."""
    if not all(path.is_file() for path in CATALOG_INPUTS):
        print("  drop reasons: (catalog inputs incomplete)")
        return
    links = pd.read_parquet(MATCH_LINKS_PATH)
    grid = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
    stratz = pd.read_parquet(STRATZ_MATCH_INDEX_PATH)
    priors = pd.read_parquet(PREGAME_QUOTES_PATH)
    universe = pd.read_parquet(UNIVERSE_PATH)
    match_ids = tuple(int(match_id) for match_id in links["match_id"])
    frame = assemble_catalog_frame(
        links,
        grid,
        stratz,
        priors,
        universe,
        try_load_opendota_pauses(match_ids),
        load_archive_pauses(links),
        try_load_stratz_winners(match_ids),
    )
    reasons = first_failure_counts(frame)
    kept = len(frame) - sum(reasons.values())
    detail = ", ".join(f"{name} {count}" for name, count in reasons.items())
    print(f"  drop reasons: {detail}  (kept {kept})")


def report_split() -> None:
    """Research split sizes when the split artifact exists."""
    if not RESEARCH_MODEL_SPLIT_PATH.is_file():
        print("split: (missing research split)")
        return
    split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH, columns=["split"])
    counts = split["split"].value_counts()
    print(
        f"split: train {int(counts.get('train', 0))}"
        f" | validation {int(counts.get('validation', 0))}"
    )


def report_funnel() -> None:
    if UNIVERSE_PATH.is_file():
        universe = pd.read_parquet(UNIVERSE_PATH, columns=["inventory_status"])
        candidates = int(universe["inventory_status"].eq("candidate").sum())
        print(f"universe candidates            {candidates}")
    else:
        print("universe candidates            (missing universe.parquet)")

    if OPENDOTA_LINKS_PATH.is_file():
        print(f"opendota links                 {len(pd.read_parquet(OPENDOTA_LINKS_PATH))}")
    else:
        print("opendota links                 (missing opendota_links.parquet)")

    report_archive_index()

    if not MATCH_LINKS_PATH.is_file():
        print("match_links                    (missing — run make link-archive)")
        return
    report_match_links()

    links = pd.read_parquet(MATCH_LINKS_PATH)
    if GRID_GAME_WINDOWS_PATH.is_file():
        report_admitted(links)
    else:
        print("admitted (window OR archive)   (missing grid_game_windows.parquet)")

    if not MATCH_CATALOG_PATH.is_file():
        print("catalog rows                   (missing — run make catalog)")
    else:
        catalog = pd.read_parquet(MATCH_CATALOG_PATH)
        horn_counts = catalog["horn_source"].value_counts()
        attached = int(catalog["archive_id"].notna().sum())
        horn_detail = ", ".join(
            f"{name} {int(count)}" for name, count in sorted(horn_counts.items())
        )
        print(
            f"catalog rows                   {len(catalog)}"
            f"  ({horn_detail} | archive-attached {attached})"
        )
        report_catalog_drops()

    report_split()


def main() -> None:
    report_funnel()


if __name__ == "__main__":
    main()
