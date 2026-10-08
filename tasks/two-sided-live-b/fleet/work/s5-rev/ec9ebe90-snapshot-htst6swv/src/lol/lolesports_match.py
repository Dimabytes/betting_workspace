"""Pure matching of Polymarket LoL events to completed lolesports maps."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from lol.constants import (
    LOL_LEAGUE_ALIASES,
    LOL_LEAGUE_SLUGS,
    LOL_SERIES_START_WINDOW_SECONDS,
    REASON_ACCEPTED,
    REASON_BO_MISMATCH,
    REASON_LEAGUE_MISMATCH,
    REASON_MISSING_SCHEDULE,
    REASON_MISSING_SIDE,
    REASON_MISSING_TEAMS,
    REASON_MULTIPLE_ELIGIBLE_SERIES,
    REASON_NO_CANDIDATES_IN_WINDOW,
    REASON_NO_MARKET,
    REASON_ORIENTATION_AMBIGUOUS,
    REASON_SERIES_CLAIM_TIE,
    REASON_SERIES_CLAIMED_BY_RICHER_EVENT,
    REASON_UNRESOLVED_MARKET,
    REASON_UNSUPPORTED_FALLBACK,
)
from lol.types import (
    LolGameRow,
    LolLinkAssignment,
    LolLinkAuditRow,
    LolLinkRow,
    LolRadiantTokenIndex,
    LolUniverseMarketRow,
)
from shared.constants.lol import LOL_TEAM_ALIASES
from shared.utils.series_format import series_winner_covers_map
from shared.utils.team_names import normalize_team_name, pick_pair_orientation, score_team_name


@dataclass(frozen=True)
class LolTeam:
    """One lolesports series team."""

    team_id: str
    name: str
    code: str | None
    game_wins: int | None


@dataclass(frozen=True)
class LolSeries:
    """Completed maps grouped by esports match id."""

    match_id: str
    start_ts: int | None
    best_of: int | None
    league_slug: str | None
    league_name: str | None
    team_a: LolTeam
    team_b: LolTeam
    maps: tuple[LolGameRow, ...]


@dataclass(frozen=True)
class EventContext:
    """Polymarket event fields used for series matching and map assignment."""

    event_id: str
    team_a: str | None
    team_b: str | None
    best_of: int | None
    league: str | None
    scheduled_ts: int | None
    game_winners: Mapping[int, LolUniverseMarketRow]
    match_winner: LolUniverseMarketRow | None
    explicit_game_n_count: int


@dataclass(frozen=True)
class SeriesProposal:
    """One unique lolesports series tentatively claimed by a PM event."""

    event_id: str
    series: LolSeries
    explicit_game_n_count: int


@dataclass(frozen=True)
class Eligibility:
    """Eligible series for one PM event, plus the zero-eligible miss reason."""

    proposals: tuple[SeriesProposal, ...]
    miss_reason: str


@dataclass(frozen=True)
class EventDecision:
    """Event-grain matching result before map assignment."""

    event: EventContext
    reason: str
    candidate_count: int
    proposal: SeriesProposal | None
    duplicate_of: str | None


@dataclass(frozen=True)
class MapDecision:
    """Map-grain assignment and orientation result."""

    link: LolLinkRow | None
    reason: str


@dataclass(frozen=True)
class LinkResult:
    """Accepted links plus the full event/map audit."""

    links: tuple[LolLinkRow, ...]
    audit: tuple[LolLinkAuditRow, ...]


def score_lol_team_name(pm_name: str, series_name: str, series_code: str | None) -> float:
    """Score a Polymarket name against lolesports name/code using LoL aliases once."""
    observed = {series_name}
    if series_code:
        observed.add(series_code)
    return score_team_name(pm_name, observed, LOL_TEAM_ALIASES)


def canonicalize_league(value: str | None) -> str | None:
    """Map a league name or slug to a known slug, or None when unknown."""
    if value is None:
        return None
    key = normalize_team_name(value)
    if not key:
        return None
    aliased = LOL_LEAGUE_ALIASES.get(key)
    if aliased is not None:
        return aliased
    if key in LOL_LEAGUE_SLUGS:
        return key
    return None


def series_league_key(series: LolSeries) -> str | None:
    """Canonical league from slug, falling back to the display name."""
    from_slug = canonicalize_league(series.league_slug)
    if from_slug is not None:
        return from_slug
    return canonicalize_league(series.league_name)


def decider_score_is_tied(best_of: int, team_a_wins: int | None, team_b_wins: int | None) -> bool:
    """True when the final series score is a real BO1/BO3/BO5 last-map decider."""
    if team_a_wins is None or team_b_wins is None:
        return False
    max_wins = max(team_a_wins, team_b_wins)
    min_wins = min(team_a_wins, team_b_wins)
    return max_wins == best_of // 2 + 1 and min_wins == max_wins - 1


def optional_int(value: object) -> int | None:
    """Coerce a parquet/JSON number to int, preserving None."""
    if value is None:
        return None
    return int(cast(int, value))


def parse_outcome_labels(outcomes_json: str) -> list[str] | None:
    """Parse Gamma-order outcome labels; None when the payload is not two values."""
    try:
        parsed: object = json.loads(outcomes_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    items = cast(list[object], parsed)
    if len(items) != 2:
        return None
    return [str(item) for item in items]


def index_game_winners(rows: Sequence[LolUniverseMarketRow]) -> dict[int, LolUniverseMarketRow]:
    """Index Game N rows by game number, preferring an included row."""
    indexed: dict[int, LolUniverseMarketRow] = {}
    for row in rows:
        if row["contract_kind"] != "game_winner":
            continue
        number = optional_int(row["game_number"])
        if number is None:
            continue
        current = indexed.get(number)
        if current is None or (row["included"] and not current["included"]):
            indexed[number] = row
    return indexed


def pick_match_winner(rows: Sequence[LolUniverseMarketRow]) -> LolUniverseMarketRow | None:
    """Pick the Match Winner row, preferring an included one."""
    chosen: LolUniverseMarketRow | None = None
    for row in rows:
        if row["contract_kind"] != "match_winner":
            continue
        if chosen is None or (row["included"] and not chosen["included"]):
            chosen = row
    return chosen


def build_event_context(event_id: str, rows: Sequence[LolUniverseMarketRow]) -> EventContext:
    """Build matching context from every universe row of one event."""
    first = rows[0]
    game_winner_rows = [row for row in rows if row["contract_kind"] == "game_winner"]
    return EventContext(
        event_id=event_id,
        team_a=first["team_a"],
        team_b=first["team_b"],
        best_of=optional_int(first["best_of"]),
        league=first["league"],
        scheduled_ts=optional_int(first["scheduled_ts"]),
        game_winners=index_game_winners(rows),
        match_winner=pick_match_winner(rows),
        explicit_game_n_count=len(game_winner_rows),
    )


def group_series(game_rows: Sequence[LolGameRow]) -> list[LolSeries]:
    """Group completed maps by esports match id in game-number order."""
    grouped: dict[str, list[LolGameRow]] = {}
    for row in game_rows:
        grouped.setdefault(row["esports_match_id"], []).append(row)
    series_list: list[LolSeries] = []
    for match_id, maps in grouped.items():
        ordered = tuple(sorted(maps, key=lambda row: int(row["game_number"])))
        first = ordered[0]
        series_list.append(
            LolSeries(
                match_id=match_id,
                start_ts=optional_int(first["start_ts"]),
                best_of=optional_int(first["best_of"]),
                league_slug=first["league_slug"],
                league_name=first["league_name"],
                team_a=LolTeam(
                    first["team_a_id"],
                    first["team_a_name"],
                    first["team_a_code"],
                    optional_int(first["team_a_game_wins"]),
                ),
                team_b=LolTeam(
                    first["team_b_id"],
                    first["team_b_name"],
                    first["team_b_code"],
                    optional_int(first["team_b_game_wins"]),
                ),
                maps=ordered,
            )
        )
    return series_list


def time_in_window(event: EventContext, series: LolSeries) -> bool:
    """True when both starts exist and differ by at most four hours."""
    if event.scheduled_ts is None or series.start_ts is None:
        return False
    return abs(event.scheduled_ts - series.start_ts) <= LOL_SERIES_START_WINDOW_SECONDS


def match_series_names(event: EventContext, series: LolSeries) -> bool | None:
    """Orient PM teams onto series teams, or None when name scores fail."""
    if event.team_a is None or event.team_b is None:
        return None
    return pick_pair_orientation(
        score_lol_team_name(event.team_a, series.team_a.name, series.team_a.code),
        score_lol_team_name(event.team_b, series.team_b.name, series.team_b.code),
        score_lol_team_name(event.team_a, series.team_b.name, series.team_b.code),
        score_lol_team_name(event.team_b, series.team_a.name, series.team_a.code),
    )


def best_of_matches(event: EventContext, series: LolSeries) -> bool:
    """Apply the BO guard only when both sides know best-of."""
    if event.best_of is None or series.best_of is None:
        return True
    return event.best_of == series.best_of


def league_matches(event: EventContext, series: LolSeries) -> bool:
    """Apply the league guard only when both sides canonicalize to a known slug."""
    event_key = canonicalize_league(event.league)
    series_key = series_league_key(series)
    if event_key is None or series_key is None:
        return True
    return event_key == series_key


def collect_eligible_series(event: EventContext, series_list: Sequence[LolSeries]) -> Eligibility:
    """Return eligible series and the zero-eligible miss reason."""
    eligible: list[SeriesProposal] = []
    saw_league_mismatch = False
    saw_bo_mismatch = False
    for series in series_list:
        if not time_in_window(event, series):
            continue
        if match_series_names(event, series) is None:
            continue
        if not best_of_matches(event, series):
            saw_bo_mismatch = True
            continue
        if not league_matches(event, series):
            saw_league_mismatch = True
            continue
        eligible.append(SeriesProposal(event.event_id, series, event.explicit_game_n_count))
    if eligible:
        return Eligibility(tuple(eligible), REASON_ACCEPTED)
    if saw_league_mismatch:
        return Eligibility(tuple(eligible), REASON_LEAGUE_MISMATCH)
    if saw_bo_mismatch:
        return Eligibility(tuple(eligible), REASON_BO_MISMATCH)
    return Eligibility(tuple(eligible), REASON_NO_CANDIDATES_IN_WINDOW)


def decide_event(event: EventContext, series_list: Sequence[LolSeries]) -> EventDecision:
    """Match one PM event to zero, one, or many eligible lolesports series."""
    if event.team_a is None or event.team_b is None:
        return EventDecision(event, REASON_MISSING_TEAMS, 0, None, None)
    if event.scheduled_ts is None:
        return EventDecision(event, REASON_MISSING_SCHEDULE, 0, None, None)
    eligibility = collect_eligible_series(event, series_list)
    if len(eligibility.proposals) == 1:
        return EventDecision(event, REASON_ACCEPTED, 1, eligibility.proposals[0], None)
    if len(eligibility.proposals) > 1:
        return EventDecision(
            event, REASON_MULTIPLE_ELIGIBLE_SERIES, len(eligibility.proposals), None, None
        )
    return EventDecision(event, eligibility.miss_reason, 0, None, None)


def resolve_duplicate_events(decisions: Sequence[EventDecision]) -> list[EventDecision]:
    """Give a shared series to the unique PM event with more explicit Game N markets."""
    replaced = {decision.event.event_id: decision for decision in decisions}
    claims_by_series: dict[str, list[EventDecision]] = {}
    for decision in decisions:
        if decision.proposal is None:
            continue
        claims_by_series.setdefault(decision.proposal.series.match_id, []).append(decision)
    for claims in claims_by_series.values():
        if len(claims) == 1:
            continue
        max_n = max(claim.event.explicit_game_n_count for claim in claims)
        richest = [claim for claim in claims if claim.event.explicit_game_n_count == max_n]
        if len(richest) > 1:
            for claim in claims:
                replaced[claim.event.event_id] = EventDecision(
                    claim.event, REASON_SERIES_CLAIM_TIE, claim.candidate_count, None, None
                )
            continue
        winner = richest[0]
        for claim in claims:
            if claim.event.event_id == winner.event.event_id:
                continue
            replaced[claim.event.event_id] = EventDecision(
                claim.event,
                REASON_SERIES_CLAIMED_BY_RICHER_EVENT,
                claim.candidate_count,
                None,
                winner.event.event_id,
            )
    return [replaced[decision.event.event_id] for decision in decisions]


def excluded_market_reason(row: LolUniverseMarketRow) -> str:
    """Map an excluded universe row to a map-level audit reason."""
    if row["reason"] == REASON_UNRESOLVED_MARKET:
        return REASON_UNRESOLVED_MARKET
    return REASON_NO_MARKET


def orient_assigned(
    series: LolSeries,
    game: LolGameRow,
    market: LolUniverseMarketRow,
    assignment: LolLinkAssignment,
) -> MapDecision:
    """Orient assigned-market outcomes onto Blue/Red, or exclude the map."""
    condition_id = market["condition_id"]
    resolved_outcome = market["resolved_outcome"]
    resolved_index = optional_int(market["resolved_outcome_index"])
    if condition_id is None or resolved_outcome is None or resolved_index is None:
        return MapDecision(None, REASON_UNRESOLVED_MARKET)
    outcomes = parse_outcome_labels(market["outcomes_json"])
    if outcomes is None:
        return MapDecision(None, REASON_ORIENTATION_AMBIGUOUS)
    orientation = pick_pair_orientation(
        score_lol_team_name(outcomes[0], series.team_a.name, series.team_a.code),
        score_lol_team_name(outcomes[1], series.team_b.name, series.team_b.code),
        score_lol_team_name(outcomes[0], series.team_b.name, series.team_b.code),
        score_lol_team_name(outcomes[1], series.team_a.name, series.team_a.code),
    )
    if orientation is None:
        return MapDecision(None, REASON_ORIENTATION_AMBIGUOUS)
    outcome_0_id = series.team_a.team_id if orientation else series.team_b.team_id
    blue_id = game["blue_esports_team_id"]
    red_id = game["red_esports_team_id"]
    if {series.team_a.team_id, series.team_b.team_id} != {blue_id, red_id}:
        return MapDecision(None, REASON_MISSING_SIDE)
    radiant_index: LolRadiantTokenIndex
    if outcome_0_id == blue_id:
        radiant_index = 0
    elif outcome_0_id == red_id:
        radiant_index = 1
    else:
        return MapDecision(None, REASON_MISSING_SIDE)
    link: LolLinkRow = {
        "event_id": market["event_id"],
        "market_id": market["market_id"],
        "condition_id": condition_id,
        "outcomes_json": market["outcomes_json"],
        "clob_token_ids_json": market["clob_token_ids_json"],
        "esports_game_id": game["esports_game_id"],
        "esports_match_id": game["esports_match_id"],
        "game_number": int(game["game_number"]),
        "loading_anchor": game["loading_anchor"],
        "loading_anchor_ts": int(game["loading_anchor_ts"]),
        "radiant_token_index": radiant_index,
        "resolved_outcome": resolved_outcome,
        "resolved_outcome_index": resolved_index,
        "blue_esports_team_id": blue_id,
        "red_esports_team_id": red_id,
        "assignment": assignment,
    }
    return MapDecision(link, REASON_ACCEPTED)


def assign_map(event: EventContext, series: LolSeries, game: LolGameRow) -> MapDecision:
    """Assign Game N or a Match Winner decider, then orient Blue/Red."""
    number = int(game["game_number"])
    game_winner = event.game_winners.get(number)
    if game_winner is not None:
        if game_winner["included"]:
            return orient_assigned(series, game, game_winner, "game_winner")
        return MapDecision(None, excluded_market_reason(game_winner))
    match_winner = event.match_winner
    if match_winner is None:
        return MapDecision(None, REASON_NO_MARKET)
    if not match_winner["included"]:
        return MapDecision(None, excluded_market_reason(match_winner))
    best_of = series.best_of if series.best_of is not None else event.best_of
    if (
        best_of is not None
        and series_winner_covers_map(best_of, number, map_winner_exists=False)
        and decider_score_is_tied(best_of, series.team_a.game_wins, series.team_b.game_wins)
    ):
        return orient_assigned(series, game, match_winner, "match_winner_decider")
    return MapDecision(None, REASON_UNSUPPORTED_FALLBACK)


def included_event_ids(rows: Sequence[LolUniverseMarketRow]) -> list[str]:
    """Return event ids that have at least one included market, first-seen order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for row in rows:
        event_id = row["event_id"]
        if not row["included"] or event_id in seen:
            continue
        seen.add(event_id)
        ordered.append(event_id)
    return ordered


def link_events(
    universe_rows: Sequence[LolUniverseMarketRow], game_rows: Sequence[LolGameRow]
) -> LinkResult:
    """Uniquely match included PM events to completed lolesports maps."""
    rows_by_event: dict[str, list[LolUniverseMarketRow]] = {}
    for row in universe_rows:
        rows_by_event.setdefault(row["event_id"], []).append(row)
    series_list = group_series(game_rows)
    initial = [
        decide_event(build_event_context(event_id, rows_by_event[event_id]), series_list)
        for event_id in included_event_ids(universe_rows)
    ]
    decisions = resolve_duplicate_events(initial)
    links: list[LolLinkRow] = []
    audit: list[LolLinkAuditRow] = []
    for decision in decisions:
        match_id = decision.proposal.series.match_id if decision.proposal is not None else None
        audit.append(
            {
                "event_id": decision.event.event_id,
                "esports_match_id": match_id,
                "esports_game_id": None,
                "game_number": None,
                "scope": "event",
                "reason": decision.reason,
                "candidate_count": decision.candidate_count,
                "duplicate_of": decision.duplicate_of,
            }
        )
        if decision.proposal is None:
            continue
        series = decision.proposal.series
        for game in series.maps:
            mapped = assign_map(decision.event, series, game)
            audit.append(
                {
                    "event_id": decision.event.event_id,
                    "esports_match_id": series.match_id,
                    "esports_game_id": game["esports_game_id"],
                    "game_number": int(game["game_number"]),
                    "scope": "map",
                    "reason": mapped.reason,
                    "candidate_count": 0,
                    "duplicate_of": None,
                }
            )
            if mapped.link is not None:
                links.append(mapped.link)
    return LinkResult(tuple(links), tuple(audit))
