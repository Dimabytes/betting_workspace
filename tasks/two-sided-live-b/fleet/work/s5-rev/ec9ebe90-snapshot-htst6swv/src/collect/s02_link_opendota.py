import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, TypeGuard, cast

import pandas as pd
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect.common.catalog_types import (
    MarketContractRow,
    OpenDotaLinkAuditRow,
    OpenDotaLinkReason,
    OpenDotaLinkRow,
    OpenDotaLinkStatus,
)
from collect.common.opendota_candidates import (
    OpenDotaCandidateRow,
    load_opendota_candidate_rows,
    refresh_opendota_candidate_pages,
)
from collect.common.paths import (
    OPENDOTA_LINK_AUDIT_PATH,
    OPENDOTA_LINKS_PATH,
    RAW_OPENDOTA_CANDIDATE_PAGES_DIR,
    UNIVERSE_MANIFEST_PATH,
    UNIVERSE_PATH,
)
from collect.common.timestamps import require_ts
from shared.types.opendota import RadiantTokenIndex
from shared.utils.json_io import read_json
from shared.utils.parquet_io import write_parquet
from shared.utils.series_format import SERIES_TYPE_TO_BEST_OF, series_winner_covers_map
from shared.utils.team_names import (
    TEAM_ALIASES,
    normalize_team_name,
    pick_pair_orientation,
    score_team_name,
)

BestOf = Literal[1, 2, 3, 5]

DEFAULT_START_TS = int(datetime(2025, 7, 1, tzinfo=UTC).timestamp())
SUPPORTED_BEST_OF = frozenset(SERIES_TYPE_TO_BEST_OF.values())
MAX_MAP_START_GAP_SECONDS = 4 * 60 * 60
PM_SERIES_START_WINDOW_SECONDS = 4 * 60 * 60
MIN_CONFIDENT_MAP_DURATION_SECONDS = 10 * 60
LINK_COLUMNS: list[str] = list(OpenDotaLinkRow.__annotations__)
AUDIT_COLUMNS: list[str] = list(OpenDotaLinkAuditRow.__annotations__)


def is_supported_best_of(value: int | None) -> TypeGuard[BestOf]:
    return value in SUPPORTED_BEST_OF


@dataclass(frozen=True)
class MatchupKey:
    league_id: int
    lower_team_id: int
    upper_team_id: int


@dataclass(frozen=True)
class OpenDotaMap:
    match_id: int
    start_time: int
    duration: int
    league_id: int
    radiant_team_id: int
    dire_team_id: int
    radiant_name: str | None
    dire_name: str | None
    best_of: BestOf | None
    radiant_win: bool


@dataclass(frozen=True)
class OpenDotaSeries:
    matchup: MatchupKey
    best_of: BestOf
    maps: tuple[OpenDotaMap, ...]


@dataclass(frozen=True)
class EventContext:
    event_id: str
    event_title: str | None
    team_a: str
    team_b: str
    best_of: BestOf
    scheduled_ts: int
    map_condition_ids: Mapping[int, str]


@dataclass(frozen=True)
class NameMatch:
    team_a_id: int
    team_b_id: int


@dataclass(frozen=True)
class SeriesProposal:
    event: EventContext
    series: OpenDotaSeries
    names: NameMatch


@dataclass(frozen=True)
class LinkDecision:
    status: OpenDotaLinkStatus
    reason: OpenDotaLinkReason | None
    candidate_count: int
    proposal: SeriesProposal | None
    duplicate_of: str | None


def convert_candidate_row(row: OpenDotaCandidateRow) -> OpenDotaMap:
    series_type = row["series_type"]
    best_of = SERIES_TYPE_TO_BEST_OF.get(series_type) if series_type is not None else None
    return OpenDotaMap(
        match_id=row["match_id"],
        start_time=row["start_time"],
        duration=row["duration"],
        league_id=row["leagueid"],
        radiant_team_id=row["radiant_team_id"],
        dire_team_id=row["dire_team_id"],
        radiant_name=row["radiant_name"],
        dire_name=row["dire_name"],
        best_of=best_of,
        radiant_win=row["radiant_win"],
    )


def load_open_dota_maps(raw_dir: Path, start_ts: int, end_ts: int) -> list[OpenDotaMap]:
    rows = load_opendota_candidate_rows(raw_dir, start_ts, end_ts)
    return [convert_candidate_row(row) for row in rows]


def build_matchup_key(game: OpenDotaMap) -> MatchupKey:
    lower_team_id = min(game.radiant_team_id, game.dire_team_id)
    upper_team_id = max(game.radiant_team_id, game.dire_team_id)
    return MatchupKey(
        league_id=game.league_id,
        lower_team_id=lower_team_id,
        upper_team_id=upper_team_id,
    )


def get_winning_team_id(game: OpenDotaMap) -> int:
    return game.radiant_team_id if game.radiant_win else game.dire_team_id


def is_series_complete(best_of: BestOf, map_count: int, wins: dict[int, int]) -> bool:
    if best_of == 1:
        return map_count == 1
    if best_of == 2:
        return map_count == 2
    wins_required = 2 if best_of == 3 else 3
    return max(wins.values(), default=0) >= wins_required


def build_matchup_series(matchup: MatchupKey, bucket: list[OpenDotaMap]) -> list[OpenDotaSeries]:
    bucket.sort(key=lambda game: (game.start_time, game.match_id))
    complete: list[OpenDotaSeries] = []
    current_maps: list[OpenDotaMap] = []
    current_wins: dict[int, int] = {}

    for game in bucket:
        best_of = game.best_of
        if best_of is None:
            current_maps = []
            current_wins = {}
            continue

        if current_maps:
            gap = game.start_time - current_maps[-1].start_time
            incompatible = best_of != current_maps[0].best_of or gap > MAX_MAP_START_GAP_SECONDS
            if incompatible:
                current_maps = []
                current_wins = {}

        current_maps.append(game)
        winner_id = get_winning_team_id(game)
        current_wins[winner_id] = current_wins.get(winner_id, 0) + 1

        if not is_series_complete(best_of, len(current_maps), current_wins):
            continue
        has_short_map = any(
            candidate.duration < MIN_CONFIDENT_MAP_DURATION_SECONDS for candidate in current_maps
        )
        if not has_short_map:
            complete.append(
                OpenDotaSeries(matchup=matchup, best_of=best_of, maps=tuple(current_maps))
            )
        current_maps = []
        current_wins = {}

    return complete


def build_open_dota_series(maps: list[OpenDotaMap]) -> tuple[OpenDotaSeries, ...]:
    maps_by_matchup: dict[MatchupKey, list[OpenDotaMap]] = defaultdict(list)
    for game in maps:
        maps_by_matchup[build_matchup_key(game)].append(game)

    complete: list[OpenDotaSeries] = []
    for matchup, bucket in maps_by_matchup.items():
        complete.extend(build_matchup_series(matchup, bucket))
    complete.sort(key=lambda series: (series.maps[0].start_time, series.maps[0].match_id))
    return tuple(complete)


def build_event_context(contracts: list[MarketContractRow]) -> EventContext | None:
    first = contracts[0]
    team_a = first["team_a"]
    team_b = first["team_b"]
    best_of = first["best_of"]
    scheduled_ts = first["scheduled_ts"]
    if team_a is None or team_b is None or scheduled_ts is None:
        return None
    if not is_supported_best_of(best_of):
        return None
    map_condition_ids = {
        contract["game_number"]: contract["conditionId"]
        for contract in contracts
        if contract["inventory_status"] == "candidate"
        and contract["game_number"] is not None
        and 1 <= contract["game_number"] <= best_of
    }
    if not map_condition_ids:
        return None
    series_condition_id = next(
        (
            contract["conditionId"]
            for contract in contracts
            if contract["contract_kind"] == "series_winner"
        ),
        None,
    )
    decider_covered = series_winner_covers_map(
        best_of, best_of, map_winner_exists=best_of in map_condition_ids
    )
    if series_condition_id is not None and decider_covered:
        map_condition_ids[best_of] = series_condition_id
    return EventContext(
        event_id=first["event_id"],
        event_title=first["event_title"],
        team_a=team_a,
        team_b=team_b,
        best_of=best_of,
        scheduled_ts=scheduled_ts,
        map_condition_ids=map_condition_ids,
    )


def load_event_contexts(path: Path) -> list[EventContext]:
    frame = pd.read_parquet(path)
    contracts = cast(list[MarketContractRow], frame.to_dict(orient="records"))
    contracts_by_event: dict[str, list[MarketContractRow]] = defaultdict(list)
    for contract in contracts:
        if contract["parse_status"] != "ok" or contract["inventory_status"] == "excluded":
            continue
        if not is_supported_best_of(contract["best_of"]):
            continue
        contracts_by_event[contract["event_id"]].append(contract)
    contexts = [build_event_context(rows) for rows in contracts_by_event.values()]
    return [context for context in contexts if context is not None]


def collect_series_team_names(series: OpenDotaSeries) -> dict[int, set[str]]:
    names: dict[int, set[str]] = {
        series.matchup.lower_team_id: set(),
        series.matchup.upper_team_id: set(),
    }
    for game in series.maps:
        radiant_name = normalize_team_name(game.radiant_name)
        dire_name = normalize_team_name(game.dire_name)
        if radiant_name:
            names[game.radiant_team_id].add(radiant_name)
        if dire_name:
            names[game.dire_team_id].add(dire_name)
    return names


def match_series_names(event: EventContext, series: OpenDotaSeries) -> NameMatch | None:
    names = collect_series_team_names(series)
    lower_id = series.matchup.lower_team_id
    upper_id = series.matchup.upper_team_id
    forward_a = score_team_name(event.team_a, names[lower_id], TEAM_ALIASES)
    forward_b = score_team_name(event.team_b, names[upper_id], TEAM_ALIASES)
    reverse_a = score_team_name(event.team_a, names[upper_id], TEAM_ALIASES)
    reverse_b = score_team_name(event.team_b, names[lower_id], TEAM_ALIASES)
    orientation = pick_pair_orientation(forward_a, forward_b, reverse_a, reverse_b)
    if orientation is None:
        return None
    if orientation:
        return NameMatch(team_a_id=lower_id, team_b_id=upper_id)
    return NameMatch(team_a_id=upper_id, team_b_id=lower_id)


def scan_windowed_series(
    event: EventContext, all_series: tuple[OpenDotaSeries, ...]
) -> list[SeriesProposal]:
    proposals: list[SeriesProposal] = []
    for series in all_series:
        if series.best_of != event.best_of:
            continue
        start_delta = abs(series.maps[0].start_time - event.scheduled_ts)
        if start_delta > PM_SERIES_START_WINDOW_SECONDS:
            continue
        names = match_series_names(event, series)
        if names is None:
            continue
        proposals.append(SeriesProposal(event=event, series=series, names=names))
    return proposals


def decide_event_link(event: EventContext, all_series: tuple[OpenDotaSeries, ...]) -> LinkDecision:
    proposals = scan_windowed_series(event, all_series)
    if not proposals:
        return LinkDecision(
            status="no_candidates",
            reason="no_complete_series_in_window",
            candidate_count=0,
            proposal=None,
            duplicate_of=None,
        )
    if len(proposals) > 1:
        return LinkDecision(
            status="ambiguous",
            reason="multiple_complete_series",
            candidate_count=len(proposals),
            proposal=None,
            duplicate_of=None,
        )
    return LinkDecision(
        status="matched",
        reason=None,
        candidate_count=1,
        proposal=proposals[0],
        duplicate_of=None,
    )


def resolve_duplicate_events(decisions: dict[str, LinkDecision]) -> dict[str, LinkDecision]:
    resolved = dict(decisions)
    claims_by_series: dict[int, list[SeriesProposal]] = defaultdict(list)
    for decision in decisions.values():
        if decision.proposal is not None:
            claims_by_series[decision.proposal.series.maps[0].match_id].append(decision.proposal)

    for claims in claims_by_series.values():
        if len(claims) == 1:
            continue
        richest_count = max(len(claim.event.map_condition_ids) for claim in claims)
        richest = [claim for claim in claims if len(claim.event.map_condition_ids) == richest_count]
        if len(richest) > 1:
            for claim in claims:
                previous = decisions[claim.event.event_id]
                resolved[claim.event.event_id] = LinkDecision(
                    status="ambiguous",
                    reason="series_claim_tie",
                    candidate_count=previous.candidate_count,
                    proposal=None,
                    duplicate_of=None,
                )
            continue
        winner = richest[0]
        for claim in claims:
            if claim.event.event_id == winner.event.event_id:
                continue
            previous = decisions[claim.event.event_id]
            resolved[claim.event.event_id] = LinkDecision(
                status="duplicate",
                reason="series_claimed_by_richer_event",
                candidate_count=previous.candidate_count,
                proposal=None,
                duplicate_of=winner.event.event_id,
            )
    return resolved


def build_link_row(
    proposal: SeriesProposal, game: OpenDotaMap, game_number: int, map_condition_id: str
) -> OpenDotaLinkRow:
    radiant_token_index: RadiantTokenIndex = (
        0 if game.radiant_team_id == proposal.names.team_a_id else 1
    )
    return {
        "event_id": proposal.event.event_id,
        "game_number": game_number,
        "match_id": game.match_id,
        "map_condition_id": map_condition_id,
        "match_start_time": game.start_time,
        "grid_clock_seconds": game.duration,
        "radiant_token_index": radiant_token_index,
        "opendota_radiant_name": game.radiant_name,
        "opendota_dire_name": game.dire_name,
    }


def build_audit_row(
    event: EventContext, decision: LinkDecision, accepted_link_count: int
) -> OpenDotaLinkAuditRow:
    return {
        "event_id": event.event_id,
        "event_title": event.event_title,
        "match_status": decision.status,
        "ambiguity_reason": decision.reason,
        "candidate_count": decision.candidate_count,
        "accepted_link_count": accepted_link_count,
        "duplicate_of": decision.duplicate_of,
    }


def build_links(
    contexts: list[EventContext], all_series: tuple[OpenDotaSeries, ...]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    initial = {event.event_id: decide_event_link(event, all_series) for event in contexts}
    decisions = resolve_duplicate_events(initial)
    links: list[OpenDotaLinkRow] = []
    audit: list[OpenDotaLinkAuditRow] = []
    for event in contexts:
        decision = decisions[event.event_id]
        accepted_count = 0
        if decision.status == "matched":
            proposal = decision.proposal
            if proposal is None:
                raise RuntimeError(f"matched event {event.event_id} has no series proposal")
            for game_number, game in enumerate(proposal.series.maps, start=1):
                map_condition_id = proposal.event.map_condition_ids.get(game_number)
                if map_condition_id is None:
                    continue
                links.append(build_link_row(proposal, game, game_number, map_condition_id))
                accepted_count += 1
        audit.append(build_audit_row(event, decision, accepted_count))

    link_records = cast(list[dict[str, object]], links)
    audit_records = cast(list[dict[str, object]], audit)
    links_frame = pd.DataFrame(link_records, columns=LINK_COLUMNS)
    audit_frame = pd.DataFrame(audit_records, columns=AUDIT_COLUMNS)
    if not links_frame.empty:
        links_frame = links_frame.sort_values(
            ["match_start_time", "event_id", "game_number"]
        ).reset_index(drop=True)
    audit_frame = audit_frame.sort_values("event_id").reset_index(drop=True)
    return links_frame, audit_frame


def validate_links(links: pd.DataFrame) -> None:
    if links.empty:
        raise RuntimeError("no OpenDota links were accepted")
    if links.duplicated(["event_id", "game_number"]).any():
        raise RuntimeError("accepted links duplicate event_id/game_number")
    if links["match_id"].duplicated().any():
        raise RuntimeError("accepted links reuse match_id")
    if links["map_condition_id"].duplicated().any():
        raise RuntimeError("accepted links reuse map_condition_id")
    for event_id, group in links.groupby("event_id"):
        ordered = group.sort_values("game_number")
        if not ordered["match_start_time"].is_monotonic_increasing:
            raise RuntimeError(f"non-monotonic map order for event {event_id}")


def publish_outputs(links: pd.DataFrame, audit: pd.DataFrame) -> None:
    write_parquet(links, OPENDOTA_LINKS_PATH)
    write_parquet(audit, OPENDOTA_LINK_AUDIT_PATH)


def main(
    fetch_candidates: Annotated[bool, typer.Option("--fetch-candidates")] = False,
) -> None:
    end_ts = require_ts(read_json(UNIVERSE_MANIFEST_PATH)["data_as_of"], "data_as_of")
    if fetch_candidates:
        refresh_opendota_candidate_pages(RAW_OPENDOTA_CANDIDATE_PAGES_DIR, DEFAULT_START_TS, end_ts)

    maps = load_open_dota_maps(RAW_OPENDOTA_CANDIDATE_PAGES_DIR, DEFAULT_START_TS, end_ts)
    opendota_series = build_open_dota_series(maps)
    contexts = load_event_contexts(UNIVERSE_PATH)
    links, audit = build_links(contexts, opendota_series)
    validate_links(links)
    if (pd.to_numeric(links["match_start_time"], errors="coerce") >= end_ts).any():
        raise RuntimeError("OpenDota links contain a match at or after the frozen cutoff")

    status_counts = audit["match_status"].value_counts().sort_index().to_dict()
    print(f"candidate maps: {len(maps)}")
    print(f"complete series: {len(opendota_series)}")
    print(f"accepted links: {len(links)} across {links['event_id'].nunique()} series")
    print(f"audit statuses: {status_counts}")
    publish_outputs(links, audit)
    print(f"saved: {OPENDOTA_LINKS_PATH}")
    print(f"saved: {OPENDOTA_LINK_AUDIT_PATH}")


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Build strict rolling Polymarket -> OpenDota links.")(main)
    app()
