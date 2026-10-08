import gzip
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import pandas as pd
import typer
from polymarket.models.gamma.event import Event
from polymarket.models.gamma.market import Market

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect.common.catalog_types import (
    MarketContractKind,
    MarketContractRow,
    MarketInventoryStatus,
)
from collect.common.paths import (
    RAW_UNIVERSE_DIR,
    UNIVERSE_DIR,
    UNIVERSE_MANIFEST_PATH,
    UNIVERSE_PATH,
)
from collect.common.timestamps import require_ts
from shared.constants import api
from shared.types.polymarket import (
    GammaDiscoveryFilters,
    GammaKeysetQuery,
    UniverseEventsPage,
)
from shared.utils.filesystem import staging_directory
from shared.utils.http import get_json, http_client
from shared.utils.json_io import write_json
from shared.utils.parquet_io import write_parquet
from shared.utils.polymarket import parse_market, read_polymarket_universe_page

DEFAULT_START_DATE = "2025-07-01T00:00:00Z"
DOTA_TAG_ID = 102366
GAMMA_LIMIT = 500

SCORELINE_RE = re.compile(r"\b\d\s*[-–]\s*\d\b")
BO_RE = re.compile(r"\bBO\s*(?P<best_of>\d+)\b", re.IGNORECASE)
SCORE_BO_RE = re.compile(r"(?:^|\|)\s*Bo\s*(?P<best_of>\d+)\s*(?:$|\|)", re.IGNORECASE)
MATCH_RE = re.compile(
    r"Dota\s*2\s*:\s*(?P<team_a>.+?)\s+v(?:s\.?|ersus)\s+"
    r"(?P<team_b>.+?)(?=\s*\(\s*BO\s*\d+\s*\)|\s+-\s+|$)",
    re.IGNORECASE,
)
GAME_WINNER_RE = re.compile(r"\bGame\s*(?P<game_number>\d+)\s+Winner\b", re.IGNORECASE)

CONTRACT_COLUMNS: list[str] = list(MarketContractRow.__annotations__)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def utc_ts(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp())


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def page_path(raw_dir: Path, source: str, page_number: int) -> Path:
    return raw_dir / "events" / source / f"page_{page_number:05d}.json.gz"


def write_gzip_json(path: Path, payload: UniverseEventsPage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
    ):
        zipped.write(canonical_json(payload).encode("utf-8"))
    tmp.replace(path)


def discovery_queries() -> list[tuple[str, GammaDiscoveryFilters]]:
    queries: list[tuple[str, GammaDiscoveryFilters]] = []
    for closed in (True, False):
        state = "closed" if closed else "open"
        tag_filters: GammaDiscoveryFilters = {
            "tag_id": DOTA_TAG_ID,
            "closed": str(closed).lower(),
        }
        queries.append((f"tag_{DOTA_TAG_ID}_{state}", tag_filters))
    return queries


def parse_keyset_page(
    response: object,
    source: str,
    expected_query: GammaKeysetQuery,
    cursor: str | None,
    as_of: str,
) -> UniverseEventsPage:
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
        "as_of": as_of,
        "events": cast(list[object], events_raw),
    }


def fetch_query_pages(
    raw_dir: Path,
    source: str,
    filters: GammaDiscoveryFilters,
    as_of: str,
) -> int:
    page_number = 0
    cursor: str | None = None
    seen_cursors: set[str] = set()
    with http_client() as client:
        while True:
            path = page_path(raw_dir, source, page_number)
            expected_query: GammaKeysetQuery = {"limit": GAMMA_LIMIT, **filters}
            params = {**expected_query, "after_cursor": cursor}
            response = get_json(client, f"{api.POLYMARKET_GAMMA_API}/events/keyset", params)
            payload = parse_keyset_page(response, source, expected_query, cursor, as_of)
            write_gzip_json(path, payload)
            events = payload["events"]
            print(f"saved {source} page {page_number}: {len(events)} events", flush=True)

            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                return page_number + 1
            if next_cursor in seen_cursors or next_cursor == cursor:
                raise RuntimeError(
                    f"Gamma returned a repeated cursor for {source} page {page_number}"
                )
            if not events:
                raise RuntimeError(f"Gamma returned next_cursor with an empty page for {source}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            page_number += 1


def fetch_all_pages(raw_dir: Path, as_of: str) -> None:
    for source, filters in discovery_queries():
        count = fetch_query_pages(raw_dir, source, filters, as_of)
        print(f"complete {source}: {count} pages", flush=True)


def refresh_raw_snapshot(raw_dir: Path, as_of: str) -> None:
    events_dir = raw_dir / "events"
    with staging_directory(raw_dir, ".events.tmp-") as temp_root:
        fetch_all_pages(temp_root, as_of)
        shutil.rmtree(events_dir, ignore_errors=True)
        (temp_root / "events").replace(events_dir)


def discovery_page_paths(raw_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for source, _ in discovery_queries():
        paths.extend(sorted((raw_dir / "events" / source).glob("page_*.json.gz")))
    return paths


@dataclass(frozen=True)
class ContractClassification:
    contract_kind: MarketContractKind
    game_number: int | None


def load_discovered_events(raw_dir: Path, start_ts: int) -> list[Event]:
    events_by_id: dict[str, Event] = {}
    raw_paths = discovery_page_paths(raw_dir)
    if not raw_paths:
        raise FileNotFoundError(raw_dir / "events")
    for path in raw_paths:
        payload = read_polymarket_universe_page(path)
        for event in payload.events:
            event_id = event.id
            event_ts = utc_ts(event.schedule.start_time)
            if event_ts is None or event_ts < start_ts:
                continue
            if event_id in events_by_id:
                raise RuntimeError(f"duplicate event {event_id} across discovery sources")
            events_by_id[event_id] = event
    return list(events_by_id.values())


def parse_title_teams(title: str | None) -> tuple[str | None, str | None]:
    match = MATCH_RE.search(str(title or ""))
    if not match:
        return None, None
    return clean_text(match.group("team_a")), clean_text(match.group("team_b"))


def read_structured_teams(event: Event) -> tuple[str | None, str | None]:
    names = [name for team in event.sports.teams if (name := clean_text(team.name))]
    if len(names) != 2:
        return None, None
    return names[0], names[1]


def best_of(event: Event) -> int | None:
    match = BO_RE.search(event.title or "")
    if not match:
        match = SCORE_BO_RE.search(event.sports.score or "")
    return int(match.group("best_of")) if match else None


def parse_status(
    team_a: str | None, team_b: str | None, bo: int | None, start_ts: int | None
) -> str:
    reasons: list[str] = []
    if not team_a or not team_b:
        reasons.append("missing_teams")
    if bo is None:
        reasons.append("missing_best_of")
    if start_ts is None:
        reasons.append("missing_scheduled_time")
    return "ok" if not reasons else "|".join(reasons)


def classify_contract(market: Market, has_match_teams: bool) -> ContractClassification:
    question = clean_text(market.question) or ""
    sports_type = (market.sports.sports_market_type or "").lower()
    game_match = GAME_WINNER_RE.search(question)
    if sports_type == "child_moneyline" or game_match:
        game_number = int(game_match.group("game_number")) if game_match else None
        return ContractClassification(contract_kind="map_winner", game_number=game_number)
    if SCORELINE_RE.search(question):
        return ContractClassification(contract_kind="other", game_number=None)
    if has_match_teams and (sports_type == "moneyline" or MATCH_RE.search(question)):
        return ContractClassification(contract_kind="series_winner", game_number=None)
    return ContractClassification(contract_kind="other", game_number=None)


def classify_inventory(
    kind: MarketContractKind, best_of_value: int | None
) -> MarketInventoryStatus:
    if kind == "map_winner" or (kind == "series_winner" and best_of_value == 1):
        return "candidate"
    if kind == "series_winner":
        return "series_linking_required"
    return "excluded"


def build_frames(events: list[Event]) -> pd.DataFrame:
    contract_rows: list[MarketContractRow] = []
    for event in events:
        event_id = event.id
        title = clean_text(event.title)
        structured_a, structured_b = read_structured_teams(event)
        parsed_a, parsed_b = parse_title_teams(title)
        team_a, team_b = (
            (structured_a, structured_b) if structured_a and structured_b else (parsed_a, parsed_b)
        )
        bo = best_of(event)
        for market in event.markets:
            condition_id = market.condition_id
            if not condition_id:
                continue
            market_start_ts = utc_ts(market.sports.game_start_time)
            classification = classify_contract(market, bool(team_a and team_b))
            kind = classification.contract_kind
            game_number = classification.game_number
            if kind == "series_winner" and bo == 1:
                game_number = 1
            yes_token = market.outcomes.yes.token_id
            no_token = market.outcomes.no.token_id
            complete_pair = yes_token is not None and no_token is not None
            token_id_0 = yes_token if complete_pair else None
            token_id_1 = no_token if complete_pair else None
            gamma = parse_market(market)
            market_slug = None
            seconds_delay = None
            market_closed_at = None
            if gamma is not None:
                market_slug = gamma.slug
                seconds_delay = gamma.seconds_delay
                market_closed_at = iso_datetime(gamma.closed_at)
            contract_row: MarketContractRow = {
                "conditionId": condition_id,
                "event_id": event_id,
                "event_title": title,
                "contract_kind": kind,
                "team_a": team_a,
                "team_b": team_b,
                "best_of": bo,
                "game_number": game_number,
                "scheduled_ts": market_start_ts,
                "parse_status": parse_status(team_a, team_b, bo, market_start_ts),
                "market_slug": market_slug,
                "seconds_delay": seconds_delay,
                "market_closed_at": market_closed_at,
                "token_id_0": token_id_0,
                "token_id_1": token_id_1,
                "inventory_status": classify_inventory(kind, bo),
            }
            contract_rows.append(contract_row)

    contracts = pd.DataFrame(contract_rows, columns=CONTRACT_COLUMNS)
    if contracts.empty:
        raise RuntimeError("discovery produced no market contracts")
    integer_columns = ["best_of", "game_number", "scheduled_ts", "seconds_delay"]
    contracts[integer_columns] = contracts[integer_columns].astype("Int64")
    if contracts["conditionId"].duplicated().any():
        raise RuntimeError("market contract conditionId must be unique")
    series_winner_counts = contracts.loc[
        contracts["contract_kind"] == "series_winner", "event_id"
    ].value_counts()
    multi = series_winner_counts[series_winner_counts > 1]
    if not multi.empty:
        raise RuntimeError(f"event {multi.index[0]} has multiple series-winner contracts")
    return contracts.sort_values(
        ["scheduled_ts", "event_id", "conditionId"], na_position="last"
    ).reset_index(drop=True)


def build_as_of_stamp(as_of: str) -> str:
    as_of_ts = require_ts(as_of, "--as-of")
    return datetime.fromtimestamp(as_of_ts, tz=UTC).isoformat().replace("+00:00", "Z")


def main(
    as_of: Annotated[str, typer.Option(help="UTC stamp for this universe snapshot.")],
    fetch_universe: Annotated[bool, typer.Option("--fetch-universe")] = False,
    start_date: Annotated[str, typer.Option(help="Inclusive UTC start ISO.")] = DEFAULT_START_DATE,
    raw_dir: Annotated[Path, typer.Option(help="Raw Gamma cache dir.")] = RAW_UNIVERSE_DIR,
    output_dir: Annotated[Path, typer.Option(help="Published universe dir.")] = UNIVERSE_DIR,
) -> None:
    start_ts = require_ts(start_date, "--start-date")
    as_of_stamp = build_as_of_stamp(as_of)

    if fetch_universe:
        refresh_raw_snapshot(raw_dir, as_of_stamp)

    events = load_discovered_events(raw_dir, start_ts)
    contracts = build_frames(events)
    write_parquet(contracts, output_dir / UNIVERSE_PATH.name)
    write_json(output_dir / UNIVERSE_MANIFEST_PATH.name, {"data_as_of": as_of_stamp})
    candidate_count = int((contracts["inventory_status"] == "candidate").sum())
    print(f"events: {contracts['event_id'].nunique()}")
    print(f"market_contracts: {len(contracts)}")
    print(f"candidate_contracts: {candidate_count}")
    print(f"saved: {output_dir}")


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Build the rolling Polymarket Dota universe.")(main)
    app()
