"""Parquet shapes and per-map prepare result for the LoL pipeline."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from shared.types.dataset import DotaGameFeatureRow, MarketQuoteStatus, StateFeatures
from shared.types.model import ModelMeta

LolContractKind = Literal["game_winner", "match_winner", "other"]
LolLinkAssignment = Literal["game_winner", "match_winner_decider"]
LolLinkAuditScope = Literal["event", "map"]
LolRadiantTokenIndex = Literal[0, 1]


class LolUniverseMarketRow(TypedDict):
    """One row written to `data/lol/processed/universe/markets.parquet`."""

    event_id: str
    event_slug: str | None
    market_id: str | None
    condition_id: str | None
    question: str | None
    group_item_title: str | None
    sports_market_type: str | None
    outcomes_json: str
    clob_token_ids_json: str
    team_a: str | None
    team_b: str | None
    game_number: int | None
    best_of: int | None
    league: str | None
    scheduled_time: str | None
    scheduled_ts: int | None
    market_start_time: str | None
    market_end_time: str | None
    resolved_outcome: str | None
    resolved_outcome_index: int | None
    contract_kind: LolContractKind
    included: bool
    reason: str


class LolGameRow(TypedDict):
    """One completed lolesports map written to `games.parquet`."""

    esports_game_id: str
    esports_match_id: str
    league_slug: str | None
    league_name: str | None
    start_time: str | None
    start_ts: int | None
    best_of: int | None
    game_number: int
    team_a_id: str
    team_a_name: str
    team_a_code: str | None
    team_b_id: str
    team_b_name: str
    team_b_code: str | None
    team_a_game_wins: int | None
    team_b_game_wins: int | None
    blue_esports_team_id: str
    red_esports_team_id: str
    loading_anchor: str
    loading_anchor_ts: int
    patch_version: str | None


class LolLinkRow(TypedDict):
    """One accepted Polymarket-to-lolesports map written to `links.parquet`."""

    event_id: str
    market_id: str | None
    condition_id: str
    outcomes_json: str
    clob_token_ids_json: str
    esports_game_id: str
    esports_match_id: str
    game_number: int
    loading_anchor: str
    loading_anchor_ts: int
    radiant_token_index: LolRadiantTokenIndex
    resolved_outcome: str
    resolved_outcome_index: int
    blue_esports_team_id: str
    red_esports_team_id: str
    assignment: LolLinkAssignment


class LolLinkAuditRow(TypedDict):
    """One event- or map-grain explanation written to `audit.parquet`."""

    event_id: str
    esports_match_id: str | None
    esports_game_id: str | None
    game_number: int | None
    scope: LolLinkAuditScope
    reason: str
    candidate_count: int
    duplicate_of: str | None


class LolDownloadAuditRow(TypedDict):
    """One map-grain row written to `download_audit.parquet`."""

    esports_game_id: str
    event_id: str
    esports_match_id: str
    game_number: int
    window_response_count: int
    unique_frame_count: int
    max_game_second: int | None
    details_response_count: int
    complete: bool
    reason: str


class LolTelonexAuditRow(TypedDict):
    """One Telonex asset/day or market-level row in `download_audit.parquet`."""

    condition_id: str
    channel: str
    asset_id: str
    date: str | None
    row_count: int
    complete: bool
    reason: str


LolPrepareSplit = Literal["train", "validation"]
LolPrepareAuditSplit = Literal["train", "validation", "none"]


class LolPrepareSplitRow(TypedDict):
    """One accepted map's research split written to `datasets/split.parquet`."""

    match_id: int
    event_id: str
    esports_game_id: str
    game_number: int
    start_time: int
    event_start_time: int
    split: LolPrepareSplit


class LolPrepareAuditRow(TypedDict):
    """One linked-map row written to `datasets/audit.parquet`."""

    event_id: str
    esports_game_id: str
    game_number: int
    match_id: int | None
    start_time: int | None
    split: LolPrepareAuditSplit
    included: bool
    reason: str
    invariant_rule: int | None
    pause_count: int
    pause_seconds: float
    frame_count: int
    grid_seconds: int
    dataset_rows: int
    skipped_age_rows: int
    skipped_market_rows: int
    skipped_invariant_rows: int
    skipped_stamp_rows: int


class LolDatasetRow(StateFeatures):
    """Stage 05 row: shared top-3 snapshot state, wall clock, market mid, and the 300s label."""

    match_id: int
    start_time: int
    event_id: str
    second: int
    state_ts_us: int
    market_radiant_prior: float
    market_p_radiant: float
    signal_market_p_radiant_300s: float | None


class LolModelMeta(ModelMeta):
    """JSON metadata stored beside one LoL research or production model."""

    xp_source: str
    state_source: str
    validation_matches: int | None


class LolValidationMetricsCsvRow(TypedDict):
    """One full-window or per-minute row written to research validation_metrics.csv."""

    bucket: str
    second: int
    rows: int
    future_300_n: int
    no_move_mae_300_cents: float | None
    model_mae_300_cents: float | None
    mae_gain_300_cents: float | None
    mae_gain_300_ci_low_cents: float | None
    mae_gain_300_ci_high_cents: float | None
    model_bias_300_cents: float | None
    dir_300_cents: float | None
    dir_300_ci_low_cents: float | None
    dir_300_ci_high_cents: float | None


class LolBacktestMarketSecondRow(TypedDict):
    """One thin 1 Hz midpoint row for LoL markout, drawdown, and cutoff."""

    match_id: int
    event_id: str
    condition_id: str
    second: int
    state_ts_us: int
    market_status: MarketQuoteStatus
    market_p_radiant: float | None


class LolBacktestAuditRow(TypedDict):
    """One validation-card eligibility row written to datasets/backtest_audit.parquet."""

    match_id: int
    event_id: str
    condition_id: str
    slug: str
    token_id_0: str
    token_id_1: str
    radiant_token_index: int
    resolved_outcome: str
    resolved_outcome_index: int
    radiant_win: bool
    replay_start_ts: int | None
    game_ended_at_ts: int | None
    replay_end_ts: int | None
    has_books: bool
    has_onchain_fills: bool
    signal_row_count: int
    market_second_count: int
    source_lag_seconds: int
    eligible: bool
    reason: str
    ok_quote_fraction: float


@dataclass(frozen=True)
class MapBuild:
    """Per-map prepare result before event split and publish."""

    link: LolLinkRow
    match_id: int
    start_time: int | None
    pause_count: int
    pause_seconds: float
    frame_count: int
    grid_seconds: int
    rows: tuple[LolDatasetRow, ...]
    skipped_age_rows: int
    skipped_market_rows: int
    skipped_invariant_rows: int
    skipped_stamp_rows: int
    included: bool
    reason: str
    invariant_rule: int | None
    market_rows: tuple[LolBacktestMarketSecondRow, ...]
    feature_rows: tuple[DotaGameFeatureRow, ...]
    backtest_audit: LolBacktestAuditRow | None
