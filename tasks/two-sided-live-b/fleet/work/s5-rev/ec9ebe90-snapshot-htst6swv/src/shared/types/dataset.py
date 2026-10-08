from typing import Literal, TypedDict

HornSource = Literal["grid_derived", "archive"]
PausesSource = Literal["opendota", "archive"]
WinnerSource = Literal["stratz"]


class MatchCatalogRow(TypedDict):
    """One row of `match_catalog.parquet`; field order is the parquet column order.

    The published schema, not the domain object: timestamps stay ISO-8601 text.
    `spawn_at` is GRID `startedAt` (players appear on the map) — null on rows
    whose only timing is an archive horn; horn is never turned into a fake
    spawn. `horn_at` is the confirmed horn instant; `ended_at` is
    `get_state_available_ts(horn=horn_at, second=duration, pauses=pauses)`.
    `pauses_json` null means pauses are unknown — never an empty list.
    `radiant_win` is outcome/settlement data, never a feature.
    `match_catalog.catalog_entry_from_row` parses one row into `CatalogEntry`.
    """

    match_id: int
    condition_id: str
    event_id: str
    radiant_token_index: int
    spawn_at: str | None
    ended_at: str
    playback_available: bool
    duration: int
    radiant_prior: float
    seconds_delay: int
    token_id_0: str
    token_id_1: str
    market_slug: str
    market_closed_at: str
    horn_at: str
    horn_source: HornSource
    pauses_json: str | None
    pauses_source: PausesSource | None
    radiant_win: bool
    winner_source: WinnerSource
    archive_id: str | None
    archive_root: str | None
    archive_feed_source: str | None
    schedule_fingerprint: str | None


class DotaSnapshotFeatures(TypedDict):
    """Snapshot fields shared by minute and exact-second dataset rows, top-1 and top-3."""

    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top1_nw_adv: int
    radiant_top1_nw_ratio: float
    dire_top1_nw_ratio: float
    top3_nw_adv: int
    radiant_top3_nw_ratio: float
    dire_top3_nw_ratio: float


class StateFeatures(DotaSnapshotFeatures):
    """A snapshot plus the settled outcome; `radiant_win` never enters feature rows."""

    radiant_win: bool


class DatasetRow(StateFeatures):
    """Minute-boundary STRATZ state; market mid and 300s future are from second + TRAIN_LAG_SECONDS."""

    match_id: int
    start_time: int  # unix seconds; catalog anchor (spawn when known, else horn)
    second: int  # from horn; minute boundary in the model window
    market_radiant_prior: float  # pre-game market P(Radiant); constant per match
    market_p_radiant: float
    signal_market_p_radiant_300s: float


class GameHistoryMinuteRow(DotaSnapshotFeatures):
    """Minute-boundary game levels for the whole match; no market, label, derived."""

    match_id: int
    game_second: int


class GameDeathRow(TypedDict):
    """Exact STRATZ death counts at the model start and each death second."""

    match_id: int
    game_second: int
    deaths_radiant: int
    deaths_dire: int


class DotaGameFeatureRow(GameHistoryMinuteRow):
    """Dota exact-second game state with top-3; the schedule replay input block."""

    market_radiant_prior: float


class SplitRow(TypedDict):
    """One match's model split."""

    match_id: int
    start_time: int
    split: Literal["train", "validation"]


MarketQuoteStatus = Literal[
    "ok",
    "missing_quote",
    "stale_quote",
    "inconsistent_pair",
    "wide_spread",
]

TradeSide = Literal["radiant", "dire"]


class MarketSecondRowBase(TypedDict):
    """Every market-second field except the live mid, which the two shapes below narrow."""

    match_id: int
    condition_id: str
    event_id: str
    second: int
    state_ts_us: int

    market_status: MarketQuoteStatus

    signal_market_p_radiant_30s: float | None
    signal_market_p_radiant_300s: float | None


class MarketSecondRow(MarketSecondRowBase):
    """One game second of Telonex market quotes written by `make market-data`.

    `resolve_market_pair` sets a mid only on status "ok", so every other status
    leaves `market_p_radiant` null.
    """

    market_p_radiant: float | None


class OkMarketSecondRow(MarketSecondRowBase):
    """A second whose status is "ok", so it always carries a mid."""

    market_p_radiant: float


class ValidationDatasetRow(MarketSecondRow, StateFeatures):
    """Exact-second STRATZ state joined with one `MarketSecondRow`."""

    start_time: int
    market_radiant_prior: float


class ValidationMetricRow(ValidationDatasetRow):
    """Validation second with model prediction and market metrics."""

    model_p_radiant: float
    model_market_difference: float | None

    directional_side: TradeSide | None
    directional_markout_30s: float | None
    directional_markout_300s: float | None
