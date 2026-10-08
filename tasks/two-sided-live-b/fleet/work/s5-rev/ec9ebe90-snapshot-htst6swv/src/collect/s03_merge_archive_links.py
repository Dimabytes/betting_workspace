"""Merge admitted archive identities into the shared `match_links` table.

Base is the OpenDota link table (unchanged, `link_source="opendota"`). Every
`admission == "admitted"` Dota index row then either attaches its provenance
onto an existing link or creates an archive-only link row whose OpenDota
fields stay null. Archive-created links are candidates for admission, not
ready dataset rows — later stages decide completeness.
"""

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

import pandas as pd
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archive_index.index import INDEX_FILENAME
from collect.common.catalog_types import (
    ArchiveFeedSource,
    ArchiveWinner,
    MatchLinkAuditRow,
    MatchLinkResolution,
    MatchLinkRow,
    OpenDotaLinkRow,
)
from collect.common.paths import (
    MATCH_LINK_AUDIT_PATH,
    MATCH_LINKS_PATH,
    OPENDOTA_LINKS_PATH,
    UNIVERSE_PATH,
)
from shared.constants.paths import ARCHIVE_INDEX_DIR
from shared.types.opendota import RadiantTokenIndex
from shared.utils.log import print_count
from shared.utils.opendota import get_opendota_match
from shared.utils.parquet_io import write_parquet
from shared.utils.parsing import (
    opt_bool,
    opt_int,
    opt_str,
    parse_steam_match_id,
    parse_ts,
)
from shared.utils.series_format import series_winner_covers_map

INDEX_PATH = ARCHIVE_INDEX_DIR / INDEX_FILENAME

MATCH_LINK_COLUMNS: list[str] = list(MatchLinkRow.__annotations__)
AUDIT_COLUMNS: list[str] = list(MatchLinkAuditRow.__annotations__)
INT64_COLUMNS = (
    "match_id",
    "sort_ts",
    "archive_steam_match_id",
    "archive_map_number",
    "archive_joined_at_second",
    "archive_duration_seconds",
)


@dataclass(frozen=True)
class ArchiveCandidate:
    """One admitted archive's link-relevant fields, pre-resolved off the index."""

    archive_id: str
    archive_root: str
    feed_source: ArchiveFeedSource
    condition_id: str
    event_id: str
    contract_kind: str
    map_number: int | None
    steam_match_id: int | None
    joined_at_second: int | None
    horn_at_utc: str
    horn_unix: int
    winner: ArchiveWinner | None
    duration_seconds: int | None
    yes_token_index: int | None
    yes_is_radiant: bool | None
    schedule_fingerprint: str | None


@dataclass(frozen=True)
class EventInventory:
    """What a universe event sells: best-of, candidate map contracts, id casing."""

    best_of: int | None
    candidate_map_numbers: frozenset[int]


@dataclass(frozen=True)
class NewLinkSpec:
    """Identity of an archive-created link row, fields already narrowed at the gate."""

    match_id: int
    game_number: int
    condition_id: str
    radiant_token_index: RadiantTokenIndex


def load_candidates(
    index_path: Path,
) -> tuple[list[ArchiveCandidate], list[MatchLinkAuditRow], dict[str, int]]:
    """Admitted Dota index rows as mergeable candidates.

    Rows whose identity is unusable — unknown feed source, or a missing or
    unparseable horn — become audit rejections here, before the merge loop;
    every surviving candidate carries a horn the catalog can trust.
    """
    frame = pd.read_parquet(index_path)
    dota = frame[frame["game"] == "dota"] if "game" in frame.columns else frame
    refusals = {
        str(reason): int(count)
        for reason, count in dota.loc[dota["admission"] != "admitted", "admission"]
        .value_counts()
        .items()
    }
    candidates: list[ArchiveCandidate] = []
    rejected: list[MatchLinkAuditRow] = []
    for row in dota.loc[dota["admission"] == "admitted"].itertuples(index=False):
        archive_root = str(row.archive_root)
        archive_id = str(row.archive_id)
        condition_id = str(row.condition_id)
        event_id = str(row.event_id)
        feed_source = opt_str(row.feed_source)
        horn_at_utc = opt_str(row.horn_at_utc)
        horn_unix = parse_ts(horn_at_utc) if horn_at_utc is not None else None
        if feed_source not in ("grid", "oddin"):
            rejected.append(
                MatchLinkAuditRow(
                    archive_id=archive_id,
                    archive_root=archive_root,
                    condition_id=condition_id,
                    event_id=event_id,
                    feed_source=str(row.feed_source),
                    resolution="excluded_incomplete_identity",
                    detail=f"unknown feed_source {row.feed_source!r}",
                )
            )
            continue
        if horn_at_utc is None or horn_unix is None:
            rejected.append(
                MatchLinkAuditRow(
                    archive_id=archive_id,
                    archive_root=archive_root,
                    condition_id=condition_id,
                    event_id=event_id,
                    feed_source=feed_source,
                    resolution="excluded_horn_missing",
                    detail="index horn missing or unparseable",
                )
            )
            continue
        winner = opt_str(row.winner)
        candidates.append(
            ArchiveCandidate(
                archive_id=archive_id,
                archive_root=archive_root,
                feed_source=feed_source,
                condition_id=condition_id,
                event_id=event_id,
                contract_kind=str(row.contract_kind),
                map_number=opt_int(row.map_number),
                steam_match_id=parse_steam_match_id(row.steam_match_id),
                joined_at_second=opt_int(row.joined_at_second),
                horn_at_utc=horn_at_utc,
                horn_unix=horn_unix,
                winner=winner if winner in ("radiant", "dire") else None,
                duration_seconds=opt_int(row.duration_seconds),
                yes_token_index=opt_int(row.yes_token_index),
                yes_is_radiant=opt_bool(row.yes_is_radiant),
                schedule_fingerprint=opt_str(row.schedule_fingerprint),
            )
        )
    return candidates, rejected, refusals


def load_event_inventories(universe_path: Path) -> dict[str, EventInventory]:
    """Per event: the best-of and which map numbers trade as candidate markets."""
    frame = pd.read_parquet(
        universe_path,
        columns=["event_id", "best_of", "game_number", "contract_kind", "inventory_status"],
    )
    best_of_by_event: dict[str, int] = {}
    maps_by_event: dict[str, set[int]] = {}
    for row in frame.itertuples(index=False):
        event_id = str(row.event_id)
        best_of = opt_int(row.best_of)
        if best_of is not None:
            best_of_by_event[event_id] = best_of
        if row.contract_kind != "map_winner" or row.inventory_status != "candidate":
            continue
        game_number = opt_int(row.game_number)
        if game_number is not None:
            maps_by_event.setdefault(event_id, set()).add(game_number)
    return {
        event_id: EventInventory(
            best_of=best_of_by_event.get(event_id),
            candidate_map_numbers=frozenset(maps_by_event.get(event_id, set())),
        )
        for event_id in set(frame["event_id"].astype(str))
    }


def load_canonical_conditions(universe_path: Path) -> dict[str, str]:
    """Lowercase condition id -> the case the universe wrote it in."""
    frame = pd.read_parquet(universe_path, columns=["conditionId"])
    canonical: dict[str, str] = {}
    for raw in frame["conditionId"]:
        stored = str(raw)
        canonical.setdefault(stored.lower(), stored)
    return canonical


def opendota_radiant_win(match_id: int) -> bool | None:
    """The linked match's OpenDota outcome, or None when uncached."""
    try:
        return bool(get_opendota_match(match_id)["radiant_win"])
    except FileNotFoundError:
        return None


def _base_row(link: OpenDotaLinkRow) -> MatchLinkRow:
    return MatchLinkRow(
        event_id=link["event_id"],
        game_number=link["game_number"],
        match_id=link["match_id"],
        map_condition_id=link["map_condition_id"],
        radiant_token_index=link["radiant_token_index"],
        match_start_time=link["match_start_time"],
        grid_clock_seconds=link["grid_clock_seconds"],
        opendota_radiant_name=link["opendota_radiant_name"],
        opendota_dire_name=link["opendota_dire_name"],
        link_source="opendota",
        sort_ts=link["match_start_time"],
        archive_id=None,
        archive_root=None,
        archive_feed_source=None,
        archive_condition_id=None,
        archive_steam_match_id=None,
        archive_map_number=None,
        archive_joined_at_second=None,
        archive_horn_at_utc=None,
        archive_winner=None,
        archive_duration_seconds=None,
        schedule_fingerprint=None,
        identity_conflict=None,
    )


def _attach(row: MatchLinkRow, candidate: ArchiveCandidate, conflict: str | None) -> None:
    """Stamp archive provenance onto an existing OpenDota row."""
    row["link_source"] = "opendota+archive"
    row["archive_id"] = candidate.archive_id
    row["archive_root"] = candidate.archive_root
    row["archive_feed_source"] = candidate.feed_source
    row["archive_steam_match_id"] = candidate.steam_match_id
    row["archive_map_number"] = candidate.map_number
    row["archive_joined_at_second"] = candidate.joined_at_second
    row["archive_horn_at_utc"] = candidate.horn_at_utc
    row["archive_winner"] = candidate.winner
    row["archive_duration_seconds"] = candidate.duration_seconds
    row["schedule_fingerprint"] = candidate.schedule_fingerprint
    row["identity_conflict"] = conflict


def _new_link_row(candidate: ArchiveCandidate, spec: NewLinkSpec) -> MatchLinkRow:
    """An archive-created link: the archive's market identity, no OpenDota fields."""
    return MatchLinkRow(
        event_id=candidate.event_id,
        game_number=spec.game_number,
        match_id=spec.match_id,
        map_condition_id=spec.condition_id,
        radiant_token_index=spec.radiant_token_index,
        match_start_time=None,
        grid_clock_seconds=None,
        opendota_radiant_name=None,
        opendota_dire_name=None,
        link_source="archive",
        sort_ts=candidate.horn_unix,
        archive_id=candidate.archive_id,
        archive_root=candidate.archive_root,
        archive_feed_source=candidate.feed_source,
        archive_condition_id=None,
        archive_steam_match_id=candidate.steam_match_id,
        archive_map_number=candidate.map_number,
        archive_joined_at_second=candidate.joined_at_second,
        archive_horn_at_utc=candidate.horn_at_utc,
        archive_winner=candidate.winner,
        archive_duration_seconds=candidate.duration_seconds,
        schedule_fingerprint=candidate.schedule_fingerprint,
        identity_conflict=None,
    )


def _audit(
    candidate: ArchiveCandidate, resolution: MatchLinkResolution, detail: str
) -> MatchLinkAuditRow:
    return MatchLinkAuditRow(
        archive_id=candidate.archive_id,
        archive_root=candidate.archive_root,
        condition_id=candidate.condition_id,
        event_id=candidate.event_id,
        feed_source=candidate.feed_source,
        resolution=resolution,
        detail=detail,
    )


def _winner_conflict(
    candidate: ArchiveCandidate, link: MatchLinkRow, radiant_win_of: Callable[[int], bool | None]
) -> bool | None:
    """True when the archive outcome contradicts the linked match's OpenDota cache.

    None when the archive has no winner stamp or the OpenDota file is absent —
    nothing to contradict.
    """
    if candidate.winner is None:
        return None
    opendota_win = radiant_win_of(int(link["match_id"]))
    if opendota_win is None:
        return None
    return (candidate.winner == "radiant") != opendota_win


@dataclass(frozen=True)
class Rejection:
    """Why one archive did not attach or create a link row."""

    resolution: MatchLinkResolution
    detail: str


def check_condition_attach(
    candidate: ArchiveCandidate,
    cond_hit: MatchLinkRow,
    steam_differs: bool,
    radiant_win_of: Callable[[int], bool | None],
) -> Rejection | None:
    """Rejection to drop the archive, or None to attach."""
    if cond_hit["archive_id"] is not None:
        return Rejection(
            "excluded_steam_condition_conflict",
            f"row already carries archive {cond_hit['archive_id']}",
        )
    if candidate.map_number is not None and candidate.map_number != cond_hit["game_number"]:
        return Rejection(
            "excluded_map_mismatch",
            f"archive map {candidate.map_number} != link game {cond_hit['game_number']}",
        )
    if steam_differs and _winner_conflict(candidate, cond_hit, radiant_win_of):
        return Rejection(
            "excluded_winner_mismatch",
            "archive winner disagrees with the linked match",
        )
    return None


def check_match_attach(candidate: ArchiveCandidate, match_hit: MatchLinkRow) -> Rejection | None:
    """Reject an archive that disagrees with its steam-matched link row."""
    if match_hit["archive_id"] is not None:
        return Rejection(
            "excluded_steam_condition_conflict",
            f"row already carries archive {match_hit['archive_id']}",
        )
    if candidate.map_number is not None and candidate.map_number != match_hit["game_number"]:
        return Rejection(
            "excluded_map_mismatch",
            f"archive map {candidate.map_number} != link game {match_hit['game_number']}",
        )
    return None


def resolve_new_slot(
    candidate: ArchiveCandidate,
    inventories: Mapping[str, EventInventory],
    canonical_conditions: Mapping[str, str],
) -> NewLinkSpec | Rejection:
    """The created row's identity, or a rejection when the archive cannot mint one."""
    if candidate.steam_match_id is None:
        return Rejection("no_steam_no_link", "no condition hit and no steam id to create a link")
    condition_id = canonical_conditions.get(candidate.condition_id.lower(), candidate.condition_id)
    if candidate.contract_kind == "map_winner":
        if candidate.map_number is None:
            return Rejection("excluded_map_mismatch", "archive map_number unknown")
        game_number = candidate.map_number
    elif candidate.contract_kind == "series_winner":
        inventory = inventories.get(candidate.event_id)
        if (
            inventory is None
            or inventory.best_of is None
            or candidate.map_number is None
            or not series_winner_covers_map(
                inventory.best_of,
                candidate.map_number,
                map_winner_exists=candidate.map_number in inventory.candidate_map_numbers,
            )
        ):
            return Rejection(
                "excluded_series_rule", "series contract is not the decider map market"
            )
        game_number = inventory.best_of
    else:
        return Rejection(
            "excluded_series_rule",
            f"contract_kind {candidate.contract_kind!r} cannot create a map link",
        )
    if candidate.yes_token_index is None or candidate.yes_is_radiant is None:
        return Rejection(
            "excluded_incomplete_identity",
            "archive lacks yes_token_index/yes_is_radiant to place the radiant side",
        )
    radiant_index: RadiantTokenIndex = cast(
        RadiantTokenIndex,
        candidate.yes_token_index if candidate.yes_is_radiant else 1 - candidate.yes_token_index,
    )
    return NewLinkSpec(
        match_id=candidate.steam_match_id,
        game_number=game_number,
        condition_id=condition_id,
        radiant_token_index=radiant_index,
    )


def merge_archive_links(
    opendota_links: pd.DataFrame,
    candidates: list[ArchiveCandidate],
    inventories: Mapping[str, EventInventory],
    canonical_conditions: Mapping[str, str],
    radiant_win_of: Callable[[int], bool | None],
) -> tuple[list[MatchLinkRow], list[MatchLinkAuditRow]]:
    """Attach or create link rows for every admitted archive; one archive once."""
    rows = [
        _base_row(link) for link in cast(list[OpenDotaLinkRow], opendota_links.to_dict("records"))
    ]
    by_condition = {row["map_condition_id"].lower(): row for row in rows}
    by_match = {int(row["match_id"]): row for row in rows}
    slots = {(row["event_id"], row["game_number"]) for row in rows}
    audit: list[MatchLinkAuditRow] = []

    ordered = sorted(
        candidates,
        key=lambda candidate: (candidate.contract_kind != "map_winner", candidate.archive_id),
    )
    for candidate in ordered:
        condition_key = candidate.condition_id.lower()
        cond_hit = by_condition.get(condition_key)
        match_hit = (
            by_match.get(candidate.steam_match_id) if candidate.steam_match_id is not None else None
        )
        if cond_hit is not None and match_hit is not None and cond_hit is not match_hit:
            audit.append(
                _audit(
                    candidate,
                    "excluded_steam_condition_conflict",
                    "condition and steam point at different link rows",
                )
            )
            continue

        if cond_hit is not None:
            steam_differs = (
                candidate.steam_match_id is not None
                and candidate.steam_match_id != int(cond_hit["match_id"])
            )
            verdict = check_condition_attach(candidate, cond_hit, steam_differs, radiant_win_of)
            if verdict is not None:
                audit.append(_audit(candidate, verdict.resolution, verdict.detail))
                continue
            _attach(cond_hit, candidate, "archive_steam_mismatch" if steam_differs else None)
            audit.append(_audit(candidate, "attach_by_condition", ""))
            continue

        if match_hit is not None:
            verdict = check_match_attach(candidate, match_hit)
            if verdict is not None:
                audit.append(_audit(candidate, verdict.resolution, verdict.detail))
                continue
            _attach(match_hit, candidate, "archive_market_differs")
            match_hit["archive_condition_id"] = canonical_conditions.get(
                condition_key, candidate.condition_id
            )
            audit.append(_audit(candidate, "attach_by_match", ""))
            continue

        new_slot = resolve_new_slot(candidate, inventories, canonical_conditions)
        if isinstance(new_slot, Rejection):
            audit.append(_audit(candidate, new_slot.resolution, new_slot.detail))
            continue
        slot = (candidate.event_id, new_slot.game_number)
        if slot in slots:
            audit.append(
                _audit(
                    candidate,
                    "excluded_steam_condition_conflict",
                    f"(event_id, game_number) {slot} already linked",
                )
            )
            continue
        row = _new_link_row(candidate, new_slot)
        rows.append(row)
        slots.add(slot)
        by_condition[new_slot.condition_id.lower()] = row
        by_match[int(row["match_id"])] = row
        audit.append(_audit(candidate, "new_link", ""))

    return rows, audit


def validate_match_links(links: pd.DataFrame) -> None:
    if links.empty:
        raise RuntimeError("match_links is empty")
    if links.duplicated(["event_id", "game_number"]).any():
        raise RuntimeError("match_links duplicates event_id/game_number")
    if links["match_id"].dropna().duplicated().any():
        raise RuntimeError("match_links reuses match_id")
    if links["map_condition_id"].duplicated().any():
        raise RuntimeError("match_links reuses map_condition_id")
    if links["archive_id"].dropna().duplicated().any():
        raise RuntimeError("an archive_id attaches to more than one link row")
    archive_only = links[links["link_source"] == "archive"]
    faked = archive_only.loc[
        :,
        ["match_start_time", "grid_clock_seconds", "opendota_radiant_name", "opendota_dire_name"],
    ].notna()
    if faked.any().any():
        raise RuntimeError("archive-created rows carry fabricated OpenDota fields")
    for event_id, group in links.groupby("event_id"):
        timed = group.dropna(subset=["match_start_time"]).sort_values("game_number")
        if not timed["match_start_time"].is_monotonic_increasing:
            raise RuntimeError(f"non-monotonic map order for event {event_id}")


def publish_match_links(index_path: Path) -> None:
    if not index_path.is_file():
        raise SystemExit(f"missing {index_path}; run `make archive-index` first")
    links_frame = pd.read_parquet(OPENDOTA_LINKS_PATH)
    candidates, load_rejections, refusals = load_candidates(index_path)
    inventories = load_event_inventories(UNIVERSE_PATH)
    canonical = load_canonical_conditions(UNIVERSE_PATH)
    rows, audit = merge_archive_links(
        links_frame, candidates, inventories, canonical, opendota_radiant_win
    )
    audit = load_rejections + audit
    frame = pd.DataFrame(rows, columns=MATCH_LINK_COLUMNS)
    for column in INT64_COLUMNS:
        frame[column] = frame[column].astype("Int64")
    audit_frame = pd.DataFrame(audit, columns=AUDIT_COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(["sort_ts", "event_id", "game_number"]).reset_index(drop=True)
    audit_frame = audit_frame.sort_values("archive_id").reset_index(drop=True)
    validate_match_links(frame)
    write_parquet(frame, MATCH_LINKS_PATH)
    write_parquet(audit_frame, MATCH_LINK_AUDIT_PATH)

    print_count("opendota_base_links", len(links_frame))
    print_count("admitted_archives", len(candidates))
    for reason, count in sorted(refusals.items()):
        print(f"index refusal {reason}: {count}", flush=True)
    for resolution, count in sorted(audit_frame["resolution"].value_counts().items()):
        print(f"audit {resolution}: {int(count)}", flush=True)
    for source, count in sorted(frame["link_source"].value_counts().items()):
        print(f"link_source {source}: {int(count)}", flush=True)
    print_count("match_links", len(frame))
    print("archive-created links are candidates, not ready matches", flush=True)
    print(f"saved: {MATCH_LINKS_PATH}")
    print(f"saved: {MATCH_LINK_AUDIT_PATH}")


def main(
    index: Annotated[
        Path, typer.Option("--index", help="Archive index.parquet path override.")
    ] = INDEX_PATH,
) -> None:
    publish_match_links(index)


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Merge admitted archive links into match_links.parquet.")(main)
    app()
