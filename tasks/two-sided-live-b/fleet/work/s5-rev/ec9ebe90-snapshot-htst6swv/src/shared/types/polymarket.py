"""Types for the archived Polymarket universe cache.

TypedDict classes describe the envelope this repo writes around each Gamma
keyset response. The Gamma events inside stay opaque here: they are handed to
`polymarket.models.gamma.event.Event`, which validates them at read time.
"""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from polymarket.models.gamma.event import Event


class GammaDiscoveryFilters(TypedDict):
    """Per-source keyset filters before ``limit`` is attached."""

    closed: str
    tag_id: int


class GammaKeysetQuery(TypedDict):
    """Exact query object stored on each cached page."""

    limit: int
    closed: str
    tag_id: int


class GammaMarketIndexRow(TypedDict):
    """One replayable Gamma market as stored in the events-dir index cache."""

    condition_id: str
    slug: str
    closed_at: str
    seconds_delay: int
    token_ids: list[str]


class GammaMarketsIndexFile(TypedDict):
    """Disk cache next to Gamma `*/*.json.gz` pages: stamps plus replay fields."""

    fingerprint: list[list[str | int]]
    markets: list[GammaMarketIndexRow]


class UniverseEventsPage(TypedDict):
    """Top level of one `page_*.json.gz` universe cache file."""

    events: list[object]
    endpoint: NotRequired[str]
    source: NotRequired[str]
    query: NotRequired[GammaKeysetQuery]
    request_cursor: NotRequired[str | None]
    next_cursor: NotRequired[str | None]
    fetched_at: NotRequired[str]
    as_of: NotRequired[str]
    run_cutoff: NotRequired[str]


CollectorMarketKind = Literal["map_winner", "series_winner"]


class CollectorMarketOutcome(TypedDict):
    """One outcome of a collector-v1 market sidecar, in canonical index order.

    Index 0 carries the YES projection of the collector's durable `match.json`
    mapping: `yes_token_id` is index 0 and `no_token_id` is index 1, never
    inferred from the team name.
    """

    index: int
    name: str
    tokenId: str


class CollectorMarketSidecar(TypedDict):
    """The collector-v1 stable market sidecar fields the discovery reader uses.

    These are static types only: the scanner validates JSON at the boundary
    (schema version 1, exact scalar types, nonempty ids and two distinct
    canonical outcomes) before projecting. Nullable fields follow the durable
    collector contract: `mapNumber` is null for `series_winner`, and
    `question`, `acceptingOrders`, `tickSize`, `minOrderSize` and
    `gridSeriesId` may all be null. Decimal values stay strings and are passed
    through untouched.
    """

    schemaVersion: int
    eventId: str
    eventSlug: str
    conditionId: str
    marketSlug: str
    question: str | None
    marketKind: CollectorMarketKind
    mapNumber: int | None
    outcomes: list[CollectorMarketOutcome]
    active: bool
    closed: bool
    acceptingOrders: bool | None
    enableOrderBook: bool
    tickSize: str | None
    minOrderSize: str | None
    negRisk: bool
    gridSeriesId: str | None


class GammaEventMetadata(TypedDict):
    """The `eventMetadata` fields watch_grid_live reads off a live Gamma event."""

    gridSeriesId: NotRequired[str]
    league: NotRequired[str]
    tournament: NotRequired[str]


class GammaLiveEvent(TypedDict):
    """One live Gamma esports event, as returned by `GET /events?slug=` or `?tag_slug=`.

    `score` is Polymarket's own cached string ("23-22|0-0|Bo3") and `period` is
    "<map>/<maps>". Both come from the same GRID series as the widget socket.
    """

    id: str
    slug: str
    title: str
    startTime: NotRequired[str]
    live: NotRequired[bool]
    ended: NotRequired[bool]
    score: NotRequired[str]
    period: NotRequired[str]
    eventMetadata: NotRequired[GammaEventMetadata]


class PricePoint(TypedDict):
    """One CLOB `/prices-history` point: unix seconds and the midpoint."""

    t: int
    p: float


class PricesHistoryPayload(TypedDict):
    """Shape of one `prices_history/{condition}_{token}.json.gz` cache file.

    `startTs`/`endTs` are the interval the cached history is known to cover, so a
    later run can tell whether it must widen the request.
    """

    conditionId: str
    token_id: str
    startTs: int
    endTs: int
    fetched_at: str
    history: list[PricePoint]


@dataclass(frozen=True)
class PolymarketUniversePage:
    """One archived universe page: cache metadata plus its validated events."""

    events: tuple[Event, ...]
    endpoint: str | None
    source: str | None
    query: GammaKeysetQuery | None
    request_cursor: str | None
    next_cursor: str | None
    fetched_at: str | None
    snapshot: str | None
