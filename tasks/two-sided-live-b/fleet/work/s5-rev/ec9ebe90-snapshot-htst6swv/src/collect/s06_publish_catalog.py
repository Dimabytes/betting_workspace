"""Publish `match_catalog.parquet` — the single timing/provenance contract.

Admission: `(OpenDota-linked AND GRID-windowed) OR archive-attached`. Horn is
the archive's direct observation when an archive is attached, else derived
from the GRID spawn; pauses prefer OpenDota, fall back to the archive
schedule, and stay null (unknown) when neither exists — which drops the row.
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archive_index.schedule import load_archive_pauses
from collect.common.paths import (
    GRID_GAME_WINDOWS_PATH,
    MATCH_LINKS_PATH,
    PREGAME_QUOTES_PATH,
    STRATZ_MATCH_INDEX_PATH,
    UNIVERSE_PATH,
)
from collect.common.window_ids import admitted_link_mask
from shared.constants.dataset import VALIDATION_START_TIME
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.types.dataset import HornSource, PausesSource
from shared.types.opendota import OpenDotaPause
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_catalog import MATCH_CATALOG_COLUMNS
from shared.utils.match_time import get_horn_datetime, get_state_available_ts, parse_utc
from shared.utils.opendota import try_load_opendota_pauses
from shared.utils.parquet_io import write_parquet
from shared.utils.parsing import opt_float, opt_int, opt_str
from shared.utils.stratz import try_load_stratz_winners

logger = get_logger(__name__)

GAMMA_COLUMNS = (
    "market_slug",
    "seconds_delay",
    "market_closed_at",
    "token_id_0",
    "token_id_1",
)


def pauses_diverge(opendota: list[OpenDotaPause], archive: list[OpenDotaPause]) -> bool:
    """Count or total-duration disagreement between the two pause sources."""
    if len(opendota) != len(archive):
        return True
    return sum(pause["duration"] for pause in opendota) != sum(
        pause["duration"] for pause in archive
    )


def resolve_pauses(
    match_id: int,
    pauses_by_match: Mapping[int, list[OpenDotaPause]],
    archive_pauses_by_match: Mapping[int, list[OpenDotaPause] | None],
) -> tuple[list[OpenDotaPause] | None, PausesSource | None]:
    """OpenDota pauses first, else the archive schedule's; None stays unknown."""
    opendota = pauses_by_match.get(match_id)
    archive = archive_pauses_by_match.get(match_id)
    if opendota is not None:
        if archive is not None and pauses_diverge(opendota, archive):
            logger.warning(
                "pauses diverge match=%s opendota=%s archive=%s",
                match_id,
                opendota,
                archive,
            )
        return opendota, "opendota"
    if match_id in archive_pauses_by_match:
        return archive, "archive"
    return None, None


def derive_timing(
    frame: pd.DataFrame,
    pauses_by_match: Mapping[int, list[OpenDotaPause]],
    archive_pauses_by_match: Mapping[int, list[OpenDotaPause] | None],
) -> pd.DataFrame:
    """horn/horn_source/pauses_json/pauses_source/ended_at for every link row."""
    horn_at: list[str | None] = []
    horn_source: list[HornSource] = []
    pauses_json: list[str | None] = []
    pauses_source: list[PausesSource | None] = []
    ended_at: list[str | None] = []
    for row in frame.itertuples(index=False):
        match_id = opt_int(row.match_id)
        assert match_id is not None
        pauses, source = resolve_pauses(match_id, pauses_by_match, archive_pauses_by_match)
        archive_id = opt_str(row.archive_id)
        horn: str | None = None
        if archive_id is not None:
            horn_src: HornSource = "archive"
            horn = opt_str(row.archive_horn_at_utc)
        else:
            horn_src = "grid_derived"
            spawn_at = opt_str(row.spawn_at)
            if spawn_at is not None and pauses is not None:
                horn = get_horn_datetime(parse_utc(spawn_at), pauses).isoformat()
        duration = opt_float(row.duration)
        ended: str | None = None
        if horn is not None and pauses is not None and duration is not None:
            ended = get_state_available_ts(
                horn=parse_utc(horn), second=int(duration), pauses=pauses
            ).isoformat()
        horn_at.append(horn)
        horn_source.append(horn_src)
        pauses_json.append(json.dumps(pauses) if pauses is not None else None)
        pauses_source.append(source)
        ended_at.append(ended)
    frame["horn_at"] = horn_at
    frame["horn_source"] = horn_source
    frame["pauses_json"] = pauses_json
    frame["pauses_source"] = pauses_source
    frame["ended_at"] = ended_at
    return frame


def check_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Ordered completeness masks; a row enters the catalog when all are true."""
    spawn_at = pd.to_datetime(frame["spawn_at"], format="mixed", utc=True)
    horn_at = pd.to_datetime(frame["horn_at"], format="mixed", utc=True)
    anchor_at = spawn_at.fillna(horn_at)
    validation_start = pd.Timestamp(VALIDATION_START_TIME, unit="s", tz="UTC")
    validation_age = anchor_at >= validation_start
    return {
        "admission": frame["admitted"],
        "stratz": frame["stratz_status"].eq("usable"),
        "playback": frame["playback_available"].eq(True) | ~validation_age,
        "prior": frame["radiant_prior"].notna(),
        "ended_at": frame["ended_at"].notna(),
        "gamma": frame.loc[:, list(GAMMA_COLUMNS)].notna().all(axis=1),
        "winner": frame["radiant_win"].notna(),
        "winner_conflict": ~(
            frame["archive_winner"].notna()
            & frame["radiant_win"].notna()
            & ((frame["archive_winner"] == "radiant") != frame["radiant_win"].astype(bool))
        ),
    }


def first_failure_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Drop count per check; each row is attributed to its first failing mask."""
    failed = pd.Series(False, index=frame.index)
    counts: dict[str, int] = {}
    for name, mask in check_masks(frame).items():
        counts[name] = int((~mask & ~failed).sum())
        failed |= ~mask
    return counts


def keep_complete(frame: pd.DataFrame) -> pd.DataFrame:
    kept = pd.Series(True, index=frame.index)
    for mask in check_masks(frame).values():
        kept &= mask
    for name, count in first_failure_counts(frame).items():
        logger.info("catalog %s failed=%s", name, count)
    dropped = [int(match_id) for match_id in frame.loc[~kept, "match_id"]]
    logger.info("catalog kept=%s dropped=%s match_ids=%s", int(kept.sum()), len(dropped), dropped)
    return frame.loc[kept]


def assemble_catalog_frame(
    links: pd.DataFrame,
    grid: pd.DataFrame,
    stratz: pd.DataFrame,
    priors: pd.DataFrame,
    universe: pd.DataFrame,
    pauses_by_match: dict[int, list[OpenDotaPause]],
    archive_pauses_by_match: dict[int, list[OpenDotaPause] | None],
    winners: dict[int, bool],
) -> pd.DataFrame:
    """Every link row joined to its grid/stratz/prior/gamma/timing columns."""
    grid_cols = grid.loc[:, ["condition_id", "spawn_at"]]
    stratz_cols = stratz.loc[
        :,
        ["match_id", "status", "duration", "playback_available"],
    ].rename(columns={"status": "stratz_status"})
    prior_cols = priors.loc[:, ["match_id", "radiant_prior"]]
    universe_cols = universe.loc[:, ["conditionId", *GAMMA_COLUMNS]].rename(
        columns={"conditionId": "condition_id"}
    )

    frame = links.loc[
        :,
        [
            "match_id",
            "map_condition_id",
            "event_id",
            "radiant_token_index",
            "link_source",
            "archive_id",
            "archive_root",
            "archive_feed_source",
            "archive_horn_at_utc",
            "archive_winner",
            "schedule_fingerprint",
        ],
    ]
    frame["admitted"] = admitted_link_mask(frame, grid)
    frame = frame.rename(columns={"map_condition_id": "condition_id"})
    frame = frame.merge(grid_cols, on="condition_id", how="left")
    frame = frame.merge(stratz_cols, on="match_id", how="left")
    frame = frame.merge(prior_cols, on="match_id", how="left")
    frame = frame.merge(universe_cols, on="condition_id", how="left")
    frame = derive_timing(frame, pauses_by_match, archive_pauses_by_match)
    frame["radiant_win"] = [winners.get(int(match_id)) for match_id in frame["match_id"]]
    return frame


def build_match_catalog_frame(
    links: pd.DataFrame,
    grid: pd.DataFrame,
    stratz: pd.DataFrame,
    priors: pd.DataFrame,
    universe: pd.DataFrame,
    pauses_by_match: dict[int, list[OpenDotaPause]],
    archive_pauses_by_match: dict[int, list[OpenDotaPause] | None],
    winners: dict[int, bool],
) -> pd.DataFrame:
    frame = assemble_catalog_frame(
        links, grid, stratz, priors, universe, pauses_by_match, archive_pauses_by_match, winners
    )
    frame = keep_complete(frame).reset_index(drop=True)
    frame["radiant_win"] = frame["radiant_win"].astype(bool)
    frame = frame.assign(winner_source=cast(str, "stratz"))
    assert frame["horn_at"].notna().all()
    assert frame["radiant_win"].notna().all()
    return frame.loc[:, list(MATCH_CATALOG_COLUMNS)]


def publish_match_catalog() -> None:
    links = pd.read_parquet(MATCH_LINKS_PATH)
    grid = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
    stratz = pd.read_parquet(STRATZ_MATCH_INDEX_PATH)
    priors = pd.read_parquet(PREGAME_QUOTES_PATH)
    universe = pd.read_parquet(UNIVERSE_PATH)
    match_ids = tuple(int(match_id) for match_id in links["match_id"])
    pauses_by_match = try_load_opendota_pauses(match_ids)
    archive_pauses_by_match = load_archive_pauses(links)
    winners = try_load_stratz_winners(match_ids)
    logger.info("link_source counts: %s", links["link_source"].value_counts().to_dict())
    frame = build_match_catalog_frame(
        links, grid, stratz, priors, universe, pauses_by_match, archive_pauses_by_match, winners
    )
    assert not frame["match_id"].duplicated().any()
    write_parquet(frame, MATCH_CATALOG_PATH)


def main() -> None:
    setup_logging()
    publish_match_catalog()
    logger.info("saved: %s", MATCH_CATALOG_PATH)


if __name__ == "__main__":
    main()
