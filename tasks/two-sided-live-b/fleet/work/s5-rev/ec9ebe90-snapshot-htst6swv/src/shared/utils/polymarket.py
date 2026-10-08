"""Read archived Polymarket universe pages through the official SDK models."""

import gzip
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from polymarket.errors import UnexpectedResponseError
from polymarket.models.gamma.event import Event
from polymarket.models.gamma.market import Market

from shared.types.polymarket import (
    GammaMarketIndexRow,
    GammaMarketsIndexFile,
    PolymarketUniversePage,
    UniverseEventsPage,
)
from shared.utils.json_io import read_json


@dataclass(frozen=True)
class GammaMarket:
    """The archived Gamma facts a replay needs: identity, settlement, venue delay."""

    slug: str
    closed_at: datetime
    seconds_delay: int
    token_ids: tuple[str, str]


def read_polymarket_universe_page(path: Path) -> PolymarketUniversePage:
    """Read one archived Gamma page and validate its events with the Polymarket SDK."""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload: object = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"gzip json is not an object: {path}")
    raw_page = cast(UniverseEventsPage, payload)
    try:
        events = Event.parse_response_list(raw_page["events"])
    except UnexpectedResponseError as error:
        raise ValueError(f"archived Gamma events do not match the SDK schema: {path}") from error
    return PolymarketUniversePage(
        events=events,
        endpoint=raw_page.get("endpoint"),
        source=raw_page.get("source"),
        query=raw_page.get("query"),
        request_cursor=raw_page.get("request_cursor"),
        next_cursor=raw_page.get("next_cursor"),
        fetched_at=raw_page.get("fetched_at"),
        snapshot=raw_page.get("as_of") or raw_page.get("run_cutoff"),
    )


def parse_market(payload: Market) -> GammaMarket | None:
    """Read one archived Gamma market, or None when it lacks a replayable field."""
    condition_id = payload.condition_id
    slug = payload.slug
    closed_time = payload.state.closed_time
    seconds_delay = payload.trading.seconds_delay
    yes_token = payload.outcomes.yes.token_id
    no_token = payload.outcomes.no.token_id
    if not condition_id or not slug or closed_time is None or seconds_delay is None:
        return None
    if yes_token is None or no_token is None:
        return None
    return GammaMarket(
        slug=slug,
        closed_at=closed_time.astimezone(UTC),
        seconds_delay=int(seconds_delay),
        token_ids=(yes_token, no_token),
    )


GAMMA_MARKETS_INDEX_NAME = "gamma_markets_index.json"


def gamma_pages_fingerprint(events_dir: Path) -> list[list[str | int]]:
    """Stamp every `*/*.json.gz` page: relative path, mtime_ns, size."""
    rows: list[list[str | int]] = []
    for path in sorted(events_dir.glob("*/*.json.gz")):
        stat = path.stat()
        rows.append([str(path.relative_to(events_dir)), stat.st_mtime_ns, stat.st_size])
    return rows


def parse_gamma_event_pages(events_dir: Path) -> dict[str, GammaMarket]:
    """Glob and parse archived Gamma pages under events_dir by condition id."""
    markets: dict[str, GammaMarket] = {}
    for path in sorted(events_dir.glob("*/*.json.gz")):
        page = read_polymarket_universe_page(path)
        for event in page.events:
            for payload in event.markets:
                condition_id = payload.condition_id
                gamma = parse_market(payload)
                if condition_id is None or gamma is None:
                    continue
                markets[condition_id] = gamma
    return markets


def gamma_market_from_cache_row(row: GammaMarketIndexRow) -> tuple[str, GammaMarket] | None:
    """Rebuild one GammaMarket from a cache row, or None when the row is corrupt."""
    token_ids = row["token_ids"]
    if len(token_ids) != 2:
        return None
    try:
        market = GammaMarket(
            slug=row["slug"],
            closed_at=datetime.fromisoformat(row["closed_at"]).astimezone(UTC),
            seconds_delay=int(row["seconds_delay"]),
            token_ids=(token_ids[0], token_ids[1]),
        )
    except (TypeError, ValueError):
        return None
    return row["condition_id"], market


def read_gamma_markets_index(
    path: Path, fingerprint: list[list[str | int]]
) -> dict[str, GammaMarket] | None:
    """Return cached markets when the file matches fingerprint; otherwise None."""
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    index = cast(GammaMarketsIndexFile, payload)
    markets: dict[str, GammaMarket] = {}
    try:
        for row in index["markets"]:
            parsed = gamma_market_from_cache_row(row)
            if parsed is None:
                return None
            condition_id, market = parsed
            markets[condition_id] = market
    except (KeyError, TypeError):
        return None
    return markets


def write_gamma_markets_index(
    path: Path, fingerprint: list[list[str | int]], markets: dict[str, GammaMarket]
) -> None:
    """Atomically replace the Gamma markets index next to events_dir."""
    rows: list[GammaMarketIndexRow] = [
        {
            "condition_id": condition_id,
            "slug": market.slug,
            "closed_at": market.closed_at.isoformat(),
            "seconds_delay": market.seconds_delay,
            "token_ids": [market.token_ids[0], market.token_ids[1]],
        }
        for condition_id, market in markets.items()
    ]
    payload: GammaMarketsIndexFile = {"fingerprint": fingerprint, "markets": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def index_gamma_markets(events_dir: Path) -> dict[str, GammaMarket]:
    """Index archived Gamma event pages under events_dir by condition id."""
    fingerprint = gamma_pages_fingerprint(events_dir)
    cache_path = events_dir / GAMMA_MARKETS_INDEX_NAME
    cached = read_gamma_markets_index(cache_path, fingerprint)
    if cached is not None:
        return cached
    markets = parse_gamma_event_pages(events_dir)
    # ponytail: concurrent shards may stampede a cold parse; file lock if that shows up.
    write_gamma_markets_index(cache_path, fingerprint, markets)
    return markets
