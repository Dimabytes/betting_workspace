"""Build the LoL Polymarket universe from Gamma tag 65.

Gamma responses are cached first; ``markets.parquet`` is rebuilt offline from
that cache. Classification walks raw market dicts so SDK drops cannot hide rows.
"""

import gzip
import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, cast

import typer
from polymarket.errors import UnexpectedResponseError
from polymarket.models.gamma.event import Event
from polymarket.models.gamma.market import Market

from lol.constants import (
    LOL_GAMMA_LIMIT,
    LOL_GAMMA_TAG_ID,
    REASON_GAME_WINNER,
    REASON_MALFORMED_TOKENS,
    REASON_MATCH_WINNER_DECIDER,
    REASON_MISSING_BEST_OF,
    REASON_SERIES_ONLY,
    REASON_UNRESOLVED_MARKET,
    REASON_UNSUPPORTED_BO2,
    REASON_UNSUPPORTED_CONTRACT,
)
from lol.parquet_io import write_parquet_rows
from lol.types import LolContractKind, LolUniverseMarketRow
from shared.constants.api import POLYMARKET_GAMMA_API
from shared.constants.lol import LOL_RAW_GAMMA_DIR, LOL_UNIVERSE_PATH
from shared.types.polymarket import GammaDiscoveryFilters, GammaKeysetQuery, UniverseEventsPage
from shared.utils.filesystem import staging_directory
from shared.utils.http import get_json, http_client
from shared.utils.json_io import read_gzip_json
from shared.utils.log import print_count
from shared.utils.lol_leagues import parse_event_league
from shared.utils.parsing import json_list, parse_ts
from shared.utils.series_format import DECIDER_BEST_OF

GAME_WINNER_RE = re.compile(r"\bGame\s*(?P<game_number>\d+)\s+Winner\b", re.IGNORECASE)
MATCH_WINNER_RE = re.compile(r"\bMatch\s+Winner\b", re.IGNORECASE)
BO_TITLE_RE = re.compile(r"\(BO\s*(\d+)\)", re.IGNORECASE)
SCORE_BO_RE = re.compile(r"Bo(\d+)")
TITLE_TEAMS_RE = re.compile(
    r"LoL\s*:\s*(?P<team_a>.+?)\s+v(?:s\.?|ersus)\s+"
    r"(?P<team_b>.+?)(?=\s*\(\s*BO\s*\d+\s*\)|\s+-\s+|$)",
    re.IGNORECASE,
)
ROW_COLUMNS: list[str] = list(LolUniverseMarketRow.__annotations__)
INTEGER_COLUMNS = ["game_number", "best_of", "scheduled_ts", "resolved_outcome_index"]


@dataclass(frozen=True)
class DiscoveryQuery:
    """One open or closed Gamma keyset chain."""

    source: str
    filters: GammaDiscoveryFilters


@dataclass(frozen=True)
class TeamPair:
    """Two PM team names, or missing sides."""

    team_a: str | None
    team_b: str | None


@dataclass(frozen=True)
class EventFacts:
    """Event-level fields shared by every market on the event."""

    event_id: str
    event_slug: str | None
    team_a: str | None
    team_b: str | None
    best_of: int | None
    league: str | None
    scheduled_time: str | None
    scheduled_ts: int | None
    has_game_winner: bool


@dataclass(frozen=True)
class ContractKindResult:
    """Game N vs Match Winner vs other, plus Game N number when present."""

    contract_kind: LolContractKind
    game_number: int | None


@dataclass(frozen=True)
class InclusionDecision:
    """Whether the row is in-universe and why."""

    included: bool
    reason: str


@dataclass(frozen=True)
class ParsedMarket:
    """Identity, tokens, outcomes, and prices from SDK parse or raw fallback."""

    market_id: str | None
    condition_id: str | None
    question: str | None
    group_item_title: str | None
    sports_market_type: str | None
    outcomes: list[str]
    token_ids: list[str]
    yes_price: Decimal | None
    no_price: Decimal | None
    market_start_time: str | None
    market_end_time: str | None
    game_start_time: str | None
    game_start_ts: int | None


def clean_text(value: object) -> str | None:
    """Collapse whitespace and return None for missing or empty text."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def iso_datetime(value: datetime | None) -> str | None:
    """Canonical UTC Z stamp from one SDK timestamp."""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_ts(value: datetime | None) -> int | None:
    """Unix seconds from one SDK timestamp."""
    if value is None:
        return None
    return int(value.timestamp())


def iso_from_raw(value: object) -> str | None:
    """UTC Z stamp from a raw Gamma timestamp field."""
    ts = parse_ts(value)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def encode_json(value: object) -> str:
    """Stable JSON for cache pages and parquet list columns."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def read_string_list(value: object) -> list[str]:
    """Parse a Gamma JSON string list into Python strings."""
    return [str(item) for item in json_list(value)]


def parse_decimal_or_none(value: object) -> Decimal | None:
    """Parse one Gamma price, or None when missing or invalid."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def page_path(raw_dir: Path, source: str, page_number: int) -> Path:
    """Build the gzip path for one cached Gamma keyset page."""
    return raw_dir / "events" / source / f"page_{page_number:05d}.json.gz"


def write_gzip_json(path: Path, payload: UniverseEventsPage) -> None:
    """Atomically write one universe cache page with stable gzip metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
    ):
        zipped.write(encode_json(payload).encode("utf-8"))
    tmp.replace(path)


def discovery_queries() -> list[DiscoveryQuery]:
    """Build Gamma keyset queries for LoL tag 65 (closed, then open)."""
    queries: list[DiscoveryQuery] = []
    for closed in (True, False):
        state = "closed" if closed else "open"
        filters: GammaDiscoveryFilters = {
            "tag_id": LOL_GAMMA_TAG_ID,
            "closed": str(closed).lower(),
        }
        queries.append(DiscoveryQuery(f"tag_{LOL_GAMMA_TAG_ID}_{state}", filters))
    return queries


def parse_keyset_page(
    response: object,
    source: str,
    expected_query: GammaKeysetQuery,
    cursor: str | None,
) -> UniverseEventsPage:
    """Validate one Gamma keyset HTTP body and wrap it as a cache page."""
    if not isinstance(response, dict):
        raise RuntimeError(f"Gamma keyset returned a non-object for {source}")
    body = cast(dict[str, object], response)
    events_raw = body.get("events")
    if not isinstance(events_raw, list):
        raise RuntimeError(f"Gamma keyset response has no events list for {source}")
    next_cursor_raw = body.get("next_cursor")
    if next_cursor_raw is not None and not isinstance(next_cursor_raw, str):
        raise RuntimeError(f"Gamma keyset next_cursor is not a string for {source}")
    return {
        "endpoint": "/events/keyset",
        "source": source,
        "query": expected_query,
        "request_cursor": cursor,
        "next_cursor": next_cursor_raw,
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "events": cast(list[object], events_raw),
    }


def fetch_query_pages(raw_dir: Path, query: DiscoveryQuery) -> int:
    """Fetch one cursor chain into an empty staging directory."""
    page_number = 0
    cursor: str | None = None
    seen_cursors: set[str] = set()
    with http_client() as client:
        while True:
            path = page_path(raw_dir, query.source, page_number)
            expected_query: GammaKeysetQuery = {"limit": LOL_GAMMA_LIMIT, **query.filters}
            response = get_json(
                client,
                f"{POLYMARKET_GAMMA_API}/events/keyset",
                {**expected_query, "after_cursor": cursor},
            )
            payload = parse_keyset_page(response, query.source, expected_query, cursor)
            write_gzip_json(path, payload)
            events = payload["events"]
            print(f"saved {query.source} page {page_number}: {len(events)} events", flush=True)
            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                return page_number + 1
            if next_cursor in seen_cursors or next_cursor == cursor:
                raise RuntimeError(
                    f"Gamma returned a repeated cursor for {query.source} page {page_number}"
                )
            if not events:
                raise RuntimeError(
                    f"Gamma returned next_cursor with an empty page for {query.source}"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            page_number += 1


def refresh_raw_snapshot(raw_dir: Path) -> None:
    events_dir = raw_dir / "events"
    with staging_directory(raw_dir, ".events.tmp-") as temp_root:
        for query in discovery_queries():
            count = fetch_query_pages(temp_root, query)
            print(f"complete {query.source}: {count} pages", flush=True)
        shutil.rmtree(events_dir, ignore_errors=True)
        (temp_root / "events").replace(events_dir)


def discovery_page_paths(raw_dir: Path) -> list[Path]:
    """List cached Gamma pages for the LoL discovery queries only."""
    paths: list[Path] = []
    for query in discovery_queries():
        paths.extend(sorted((raw_dir / "events" / query.source).glob("page_*.json.gz")))
    return paths


def load_cached_events(raw_dir: Path) -> list[object]:
    paths = discovery_page_paths(raw_dir)
    if not paths:
        raise FileNotFoundError(raw_dir / "events")
    events: list[object] = []
    for path in paths:
        events.extend(read_gzip_json(path)["events"])
    return events


def classify_contract_kind(
    question: str | None, sports_market_type: str | None
) -> ContractKindResult:
    """Classify a market as game_winner, match_winner, or other."""
    text = question or ""
    sports_type = (sports_market_type or "").lower()
    game_match = GAME_WINNER_RE.search(text)
    if sports_type == "child_moneyline" or game_match:
        game_number = int(game_match.group("game_number")) if game_match else None
        return ContractKindResult("game_winner", game_number)
    if sports_type == "moneyline" or MATCH_WINNER_RE.search(text):
        return ContractKindResult("match_winner", None)
    return ContractKindResult("other", None)


def event_has_game_winner(raw_event: dict[str, object]) -> bool:
    """True when the event JSON lists at least one Game N Winner market."""
    raw_markets = raw_event.get("markets")
    if not isinstance(raw_markets, list):
        return False
    for raw_market in cast(list[object], raw_markets):
        if not isinstance(raw_market, dict):
            continue
        market = cast(dict[str, object], raw_market)
        kind = classify_contract_kind(
            clean_text(market.get("question")),
            clean_text(market.get("sportsMarketType")),
        )
        if kind.contract_kind == "game_winner":
            return True
    return False


def read_structured_teams(event: Event) -> TeamPair:
    """Read the two structured team names Gamma lists, or missing sides."""
    names = [name for team in event.sports.teams if (name := clean_text(team.name))]
    if len(names) != 2:
        return TeamPair(None, None)
    return TeamPair(names[0], names[1])


def parse_title_teams(title: str | None) -> TeamPair:
    """Parse two team names from a LoL match title."""
    match = TITLE_TEAMS_RE.search(title or "")
    if not match:
        return TeamPair(None, None)
    return TeamPair(clean_text(match.group("team_a")), clean_text(match.group("team_b")))


def parse_best_of(event: Event) -> int | None:
    """Parse best-of from event title or score text."""
    match = BO_TITLE_RE.search(event.title or "")
    if match:
        return int(match.group(1))
    score_match = SCORE_BO_RE.search(event.sports.score or "")
    if score_match:
        return int(score_match.group(1))
    return None


def parse_league(event: Event) -> str | None:
    """Read league from eventMetadata, else the title suffix after (BOx) -."""
    return parse_event_league(event.metadata, event.title)


def read_event_facts(event: Event, raw_event: dict[str, object]) -> EventFacts:
    """Compute event-level facts once, then classify each market."""
    structured = read_structured_teams(event)
    parsed = parse_title_teams(clean_text(event.title))
    teams = structured if structured.team_a and structured.team_b else parsed
    scheduled_ts = utc_ts(event.schedule.start_time)
    return EventFacts(
        event_id=str(event.id),
        event_slug=clean_text(event.slug),
        team_a=teams.team_a,
        team_b=teams.team_b,
        best_of=parse_best_of(event),
        league=parse_league(event),
        scheduled_time=iso_datetime(event.schedule.start_time),
        scheduled_ts=scheduled_ts,
        has_game_winner=event_has_game_winner(raw_event),
    )


def parse_raw_market(raw_market: dict[str, object]) -> ParsedMarket:
    """Parse one Gamma market via the SDK, falling back to raw fields on failure."""
    try:
        market = Market.parse_response(raw_market)
    except UnexpectedResponseError:
        market = None
    if market is None:
        prices = read_string_list(raw_market.get("outcomePrices"))
        return ParsedMarket(
            market_id=clean_text(raw_market.get("id")),
            condition_id=clean_text(raw_market.get("conditionId")),
            question=clean_text(raw_market.get("question")),
            group_item_title=clean_text(raw_market.get("groupItemTitle")),
            sports_market_type=clean_text(raw_market.get("sportsMarketType")),
            outcomes=read_string_list(raw_market.get("outcomes")),
            token_ids=read_string_list(raw_market.get("clobTokenIds")),
            yes_price=parse_decimal_or_none(prices[0] if prices else None),
            no_price=parse_decimal_or_none(prices[1] if len(prices) > 1 else None),
            market_start_time=iso_from_raw(raw_market.get("startDate")),
            market_end_time=iso_from_raw(raw_market.get("endDate")),
            game_start_time=iso_from_raw(raw_market.get("gameStartTime")),
            game_start_ts=parse_ts(raw_market.get("gameStartTime")),
        )
    yes_token = market.outcomes.yes.token_id
    no_token = market.outcomes.no.token_id
    return ParsedMarket(
        market_id=clean_text(market.id),
        condition_id=clean_text(market.condition_id),
        question=clean_text(market.question),
        group_item_title=clean_text(market.group_item_title),
        sports_market_type=clean_text(market.sports.sports_market_type),
        outcomes=[market.outcomes.yes.label, market.outcomes.no.label],
        token_ids=[yes_token or "", no_token or ""],
        yes_price=market.outcomes.yes.price,
        no_price=market.outcomes.no.price,
        market_start_time=iso_datetime(market.state.start_date),
        market_end_time=iso_datetime(market.state.end_date),
        game_start_time=iso_datetime(market.sports.game_start_time),
        game_start_ts=utc_ts(market.sports.game_start_time),
    )


def tokens_and_outcomes_are_valid(token_ids: list[str], outcomes: list[str]) -> bool:
    """True when there are exactly two distinct nonempty tokens and two labels."""
    if len(token_ids) != 2 or len(outcomes) != 2:
        return False
    if token_ids[0] == token_ids[1]:
        return False
    if any(not token.strip() for token in token_ids):
        return False
    return all(label.strip() for label in outcomes)


def match_winner_decider_map(best_of: int | None, has_game_winner: bool) -> int | None:
    """Map number Match Winner may represent in Stage 01, or None."""
    if best_of not in DECIDER_BEST_OF:
        return None
    if best_of == 1 or has_game_winner:
        return best_of
    return None


def resolved_outcome_index(yes_price: Decimal | None, no_price: Decimal | None) -> int | None:
    """Return 0 or 1 when prices are a strict Decimal 1/0 pair."""
    if yes_price is None or no_price is None:
        return None
    one = Decimal("1")
    zero = Decimal("0")
    if yes_price == one and no_price == zero:
        return 0
    if yes_price == zero and no_price == one:
        return 1
    return None


def decide_inclusion(
    kind: LolContractKind,
    tokens_ok: bool,
    best_of: int | None,
    decider_map: int | None,
    resolved_index: int | None,
) -> InclusionDecision:
    """Apply inclusion gates in order; first matching exclusion wins."""
    if not tokens_ok:
        return InclusionDecision(False, REASON_MALFORMED_TOKENS)
    if kind == "other":
        return InclusionDecision(False, REASON_UNSUPPORTED_CONTRACT)
    if best_of == 2:
        return InclusionDecision(False, REASON_UNSUPPORTED_BO2)
    if kind == "match_winner" and decider_map is None:
        if best_of in (3, 5):
            return InclusionDecision(False, REASON_SERIES_ONLY)
        return InclusionDecision(False, REASON_MISSING_BEST_OF)
    if resolved_index is None:
        return InclusionDecision(False, REASON_UNRESOLVED_MARKET)
    if kind == "game_winner":
        return InclusionDecision(True, REASON_GAME_WINNER)
    return InclusionDecision(True, REASON_MATCH_WINNER_DECIDER)


def require_unique_condition_ids(rows: list[LolUniverseMarketRow]) -> None:
    """Abort when two rows share a non-null condition id."""
    seen: set[str] = set()
    for row in rows:
        condition_id = row["condition_id"]
        if condition_id is None:
            continue
        if condition_id in seen:
            raise RuntimeError(f"duplicate condition_id {condition_id}")
        seen.add(condition_id)


def build_market_row(facts: EventFacts, raw_market: dict[str, object]) -> LolUniverseMarketRow:
    """Turn one raw Gamma market into a universe parquet row."""
    parsed = parse_raw_market(raw_market)
    kind_result = classify_contract_kind(parsed.question, parsed.sports_market_type)
    kind = kind_result.contract_kind
    tokens_ok = tokens_and_outcomes_are_valid(parsed.token_ids, parsed.outcomes)
    decider_map = match_winner_decider_map(facts.best_of, facts.has_game_winner)
    resolved_index = resolved_outcome_index(parsed.yes_price, parsed.no_price)
    decision = decide_inclusion(kind, tokens_ok, facts.best_of, decider_map, resolved_index)
    game_number = kind_result.game_number
    if kind == "match_winner" and decider_map is not None:
        game_number = decider_map
    resolved_label: str | None = None
    stored_index: int | None = None
    if decision.included and resolved_index is not None:
        stored_index = resolved_index
        resolved_label = parsed.outcomes[resolved_index]
    scheduled_time = facts.scheduled_time
    scheduled_ts = facts.scheduled_ts
    if scheduled_ts is None:
        scheduled_time = parsed.game_start_time
        scheduled_ts = parsed.game_start_ts
    return {
        "event_id": facts.event_id,
        "event_slug": facts.event_slug,
        "market_id": parsed.market_id,
        "condition_id": parsed.condition_id,
        "question": parsed.question,
        "group_item_title": parsed.group_item_title,
        "sports_market_type": parsed.sports_market_type,
        "outcomes_json": encode_json(parsed.outcomes),
        "clob_token_ids_json": encode_json(parsed.token_ids),
        "team_a": facts.team_a,
        "team_b": facts.team_b,
        "game_number": game_number,
        "best_of": facts.best_of,
        "league": facts.league,
        "scheduled_time": scheduled_time,
        "scheduled_ts": scheduled_ts,
        "market_start_time": parsed.market_start_time,
        "market_end_time": parsed.market_end_time,
        "resolved_outcome": resolved_label,
        "resolved_outcome_index": stored_index,
        "contract_kind": kind,
        "included": decision.included,
        "reason": decision.reason,
    }


def build_universe_rows(raw_events: Sequence[object]) -> list[LolUniverseMarketRow]:
    """Classify every raw Gamma market into one parquet row."""
    rows: list[LolUniverseMarketRow] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            raise RuntimeError("Gamma event is not an object")
        event_dict = cast(dict[str, object], raw_event)
        try:
            event = Event.parse_response(event_dict)
        except UnexpectedResponseError as error:
            raise RuntimeError("Gamma event did not match the SDK schema") from error
        facts = read_event_facts(event, event_dict)
        raw_markets = event_dict.get("markets")
        if not isinstance(raw_markets, list):
            continue
        for raw_market in cast(list[object], raw_markets):
            if not isinstance(raw_market, dict):
                raise RuntimeError("Gamma market is not an object")
            rows.append(build_market_row(facts, cast(dict[str, object], raw_market)))
    require_unique_condition_ids(rows)
    return rows


def count_reason_totals(rows: Sequence[LolUniverseMarketRow]) -> dict[str, int]:
    """Count rows per stable reason string."""
    totals: dict[str, int] = {}
    for row in rows:
        reason = row["reason"]
        totals[reason] = totals.get(reason, 0) + 1
    return totals


def write_universe_parquet(rows: list[LolUniverseMarketRow], output_path: Path) -> None:
    write_parquet_rows(
        rows,
        output_path,
        ROW_COLUMNS,
        INTEGER_COLUMNS,
        ["scheduled_ts", "event_id", "condition_id"],
    )


def print_universe_totals(raw_events: list[object], rows: list[LolUniverseMarketRow]) -> None:
    """Print event/market/inclusion totals and every reason count."""
    event_ids: set[str] = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event_id = cast(dict[str, object], raw).get("id")
        if event_id is not None:
            event_ids.add(str(event_id))
    print_count("events", len(event_ids))
    print_count("markets", len(rows))
    print_count("included", sum(1 for row in rows if row["included"]))
    for reason, count in sorted(count_reason_totals(rows).items()):
        print_count(reason, count)


def build_universe(raw_dir: Path, output_path: Path, fetch: bool) -> None:
    """Optionally fetch Gamma pages, then rebuild markets.parquet from cache."""
    if fetch:
        refresh_raw_snapshot(raw_dir)
    raw_events = load_cached_events(raw_dir)
    rows = build_universe_rows(raw_events)
    write_universe_parquet(rows, output_path)
    print_universe_totals(raw_events, rows)


def main(
    fetch: Annotated[bool, typer.Option("--fetch")] = False,
    raw_dir: Annotated[Path, typer.Option("--raw-dir")] = LOL_RAW_GAMMA_DIR,
    output_path: Annotated[Path, typer.Option("--output-path")] = LOL_UNIVERSE_PATH,
) -> None:
    """Download Gamma tag 65 if requested, then write LoL markets.parquet."""
    build_universe(raw_dir, output_path, fetch)


if __name__ == "__main__":
    typer.run(main)
