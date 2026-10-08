"""Count how much recent LoL Polymarket volume sits on events that carry a GRID series id.

The live LoL feed reads the GRID widget socket, which needs
`eventMetadata.gridSeriesId`. Events without it cannot be traded. This probe
measures the share we keep.

    make run F=scripts/probe_lol_grid_coverage.py
    make run F=scripts/probe_lol_grid_coverage.py ARGS="--days 60"
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from lol.constants import LOL_GAMMA_TAG_ID
from shared.constants.api import POLYMARKET_GAMMA_API
from shared.utils.http import get_json, http_client
from shared.utils.log import get_logger, setup_logging

logger = get_logger(__name__)

PAGE_LIMIT = 100
MAP_MARKET_TYPE = "child_moneyline"
SERIES_MARKET_TYPE = "moneyline"


@dataclass(frozen=True)
class EventRow:
    """One LoL event reduced to the fields this probe counts."""

    slug: str
    start_date: str
    series_id: str | None
    map_markets: int
    map_volume: float
    series_volume: float


def parse_start(value: object) -> datetime | None:
    """Parse a Gamma ISO stamp into an aware datetime, or None when unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def market_volume(market: dict[str, object]) -> float:
    """Read volumeNum as a float; anything unusable counts as zero."""
    raw = market.get("volumeNum")
    if isinstance(raw, int | float):
        return float(raw)
    return 0.0


def read_event(event: dict[str, object]) -> EventRow:
    """Project one Gamma event onto its slug, GRID id, and winner-market volume."""
    metadata = event.get("eventMetadata")
    series_id: str | None = None
    if isinstance(metadata, dict):
        raw_id = cast(dict[str, object], metadata).get("gridSeriesId")
        if isinstance(raw_id, str) and raw_id:
            series_id = raw_id
    markets_raw = event.get("markets")
    markets = cast(list[object], markets_raw) if isinstance(markets_raw, list) else []
    map_markets = 0
    map_volume = 0.0
    series_volume = 0.0
    for item in markets:
        if not isinstance(item, dict):
            continue
        market = cast(dict[str, object], item)
        kind = market.get("sportsMarketType")
        if kind == MAP_MARKET_TYPE:
            map_markets += 1
            map_volume += market_volume(market)
        elif kind == SERIES_MARKET_TYPE:
            series_volume += market_volume(market)
    return EventRow(
        slug=str(event.get("slug", "")),
        start_date=str(event.get("startDate", "")),
        series_id=series_id,
        map_markets=map_markets,
        map_volume=map_volume,
        series_volume=series_volume,
    )


def fetch_events(closed: bool, cutoff: datetime) -> list[EventRow]:
    """Page newest-first through one closed state until events fall before `cutoff`."""
    rows: list[EventRow] = []
    offset = 0
    with http_client() as client:
        while True:
            params: dict[str, Any] = {
                "tag_id": LOL_GAMMA_TAG_ID,
                "closed": str(closed).lower(),
                "limit": PAGE_LIMIT,
                "offset": offset,
                "order": "startDate",
                "ascending": "false",
            }
            payload = get_json(client, f"{POLYMARKET_GAMMA_API}/events", params)
            if not isinstance(payload, list):
                raise RuntimeError("gamma /events did not return a list")
            page = cast(list[object], payload)
            if not page:
                return rows
            reached_cutoff = False
            for item in page:
                if not isinstance(item, dict):
                    continue
                event = cast(dict[str, object], item)
                started = parse_start(event.get("startDate"))
                if started is not None and started < cutoff:
                    reached_cutoff = True
                    continue
                rows.append(read_event(event))
            logger.info("closed=%s offset=%d page=%d kept=%d", closed, offset, len(page), len(rows))
            if reached_cutoff or len(page) < PAGE_LIMIT:
                return rows
            offset += PAGE_LIMIT


def report(rows: list[EventRow], days: int) -> None:
    """Print the coverage totals and the biggest events we would have to skip."""
    with_grid = [row for row in rows if row.series_id is not None]
    without_grid = [row for row in rows if row.series_id is None]
    total_maps = sum(row.map_markets for row in rows)
    grid_maps = sum(row.map_markets for row in with_grid)
    total_volume = sum(row.map_volume for row in rows)
    grid_volume = sum(row.map_volume for row in with_grid)
    logger.info("window: last %d days", days)
    logger.info("events: %d total, %d with gridSeriesId", len(rows), len(with_grid))
    logger.info("map markets: %d total, %d under a GRID event", total_maps, grid_maps)
    logger.info("map volume: %.0f total, %.0f under a GRID event", total_volume, grid_volume)
    if total_maps:
        logger.info("map count coverage: %.1f%%", 100.0 * grid_maps / total_maps)
    if total_volume:
        logger.info("map volume coverage: %.1f%%", 100.0 * grid_volume / total_volume)
    missed = sorted(without_grid, key=lambda row: row.map_volume, reverse=True)[:10]
    logger.info("biggest events without gridSeriesId:")
    for row in missed:
        logger.info(
            "  %-40s maps=%-3d volume=%.0f %s",
            row.slug,
            row.map_markets,
            row.map_volume,
            row.start_date,
        )


def main(argv: list[str]) -> int:
    """Fetch open and closed LoL events over the window and report GRID coverage."""
    setup_logging()
    parser = argparse.ArgumentParser(prog="probe-lol-grid-coverage")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    cutoff = datetime.now(tz=UTC) - timedelta(days=args.days)
    rows = fetch_events(True, cutoff) + fetch_events(False, cutoff)
    report(rows, args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
