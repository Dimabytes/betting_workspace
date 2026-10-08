from typing import Literal, TypedDict

from shared.types.opendota import RadiantTokenIndex

MarketContractKind = Literal["map_winner", "series_winner", "other"]
MarketInventoryStatus = Literal["candidate", "series_linking_required", "excluded"]


class MarketContractRow(TypedDict):
    conditionId: str
    event_id: str
    event_title: str | None
    contract_kind: MarketContractKind
    team_a: str | None
    team_b: str | None
    best_of: int | None
    game_number: int | None
    scheduled_ts: int | None
    parse_status: str
    market_slug: str | None
    seconds_delay: int | None
    market_closed_at: str | None
    token_id_0: str | None
    token_id_1: str | None
    inventory_status: MarketInventoryStatus


class GridGameWindowRow(TypedDict):
    condition_id: str
    spawn_at: str


class PregameQuoteRow(TypedDict):
    condition_id: str
    match_id: int
    anchor_ts: int
    radiant_prior: float
    radiant_price: float
    dire_price: float
    radiant_quote_ts: int
    dire_quote_ts: int


OpenDotaLinkStatus = Literal["matched", "no_candidates", "ambiguous", "duplicate"]
OpenDotaLinkReason = Literal[
    "no_complete_series_in_window",
    "multiple_complete_series",
    "series_claimed_by_richer_event",
    "series_claim_tie",
]


class OpenDotaLinkRow(TypedDict):
    event_id: str
    game_number: int
    match_id: int
    map_condition_id: str
    match_start_time: int
    grid_clock_seconds: int
    radiant_token_index: RadiantTokenIndex
    opendota_radiant_name: str | None
    opendota_dire_name: str | None


class OpenDotaLinkAuditRow(TypedDict):
    event_id: str
    event_title: str | None
    match_status: OpenDotaLinkStatus
    ambiguity_reason: OpenDotaLinkReason | None
    candidate_count: int
    accepted_link_count: int
    duplicate_of: str | None


MatchLinkSource = Literal["opendota", "archive", "opendota+archive"]
ArchiveFeedSource = Literal["grid", "oddin"]
MatchLinkResolution = Literal[
    "attach_by_condition",
    "attach_by_match",
    "new_link",
    "no_steam_no_link",
    "excluded_winner_mismatch",
    "excluded_map_mismatch",
    "excluded_series_rule",
    "excluded_horn_missing",
    "excluded_incomplete_identity",
    "excluded_steam_condition_conflict",
]
ArchiveWinner = Literal["radiant", "dire"]


class MatchLinkRow(TypedDict):
    """One row of `match_links.parquet`: a linked market/map and its provenance.

    OpenDota-only fields stay null on archive-created rows; they are never
    fabricated. `archive_condition_id` is set only when the archive's own
    condition differs from the row's `map_condition_id`.
    """

    event_id: str
    game_number: int
    match_id: int
    map_condition_id: str
    radiant_token_index: RadiantTokenIndex
    match_start_time: int | None
    grid_clock_seconds: int | None
    opendota_radiant_name: str | None
    opendota_dire_name: str | None
    link_source: MatchLinkSource
    sort_ts: int
    archive_id: str | None
    archive_root: str | None
    archive_feed_source: ArchiveFeedSource | None
    archive_condition_id: str | None
    archive_steam_match_id: int | None
    archive_map_number: int | None
    archive_joined_at_second: int | None
    archive_horn_at_utc: str | None
    archive_winner: ArchiveWinner | None
    archive_duration_seconds: int | None
    schedule_fingerprint: str | None
    identity_conflict: str | None


class MatchLinkAuditRow(TypedDict):
    """One row per iterated admitted archive: what the merge did with it."""

    archive_id: str
    archive_root: str
    condition_id: str
    event_id: str
    feed_source: str
    resolution: MatchLinkResolution
    detail: str
