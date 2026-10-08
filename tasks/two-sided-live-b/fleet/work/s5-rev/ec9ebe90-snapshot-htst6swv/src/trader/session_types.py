"""Shared value types for one live paper session. No behavior, no I/O."""

from dataclasses import dataclass
from enum import StrEnum

from polymaker.domain import MarketMeta

from trader.collector_sidecars import MarketKind
from trader.live_feed import FeedSource, GameSnapshot

PHASE_SETUP = "setup"
PHASE_DECISION = "decision"
PHASE_MARKET_DATA = "market_data"
PHASE_SIDECAR_REFRESH = "sidecar_refresh"


class SignalReason(StrEnum):
    """Per-snapshot decision label. A member IS its journal wire string."""

    MODEL = "model"
    PRE_HORN = "pre_horn"
    PAUSED = "paused"
    FINISHED = "finished"
    STALE = "stale"
    MISSING_BOOK = "missing_book"
    OWN_LIQUIDITY_ONLY = "own_liquidity_only"
    MISSING_PRIOR = "missing_prior"
    RESUME_LOCKED = "resume_locked"
    ONE_SIDED = "one_sided_book"
    CROSSED = "crossed_book"
    NONFINITE = "nonfinite_pair"
    OUT_OF_RANGE = "out_of_range_pair"
    PAIR_BROKEN = "pair_out_of_tolerance"
    MODEL_ERROR = "model_error"
    SIDECAR_FAULT = "sidecar_fault"
    TRADING_ERROR = "trading_error"


class EntryBlock(StrEnum):
    """Why a model tick did not open an entry. A member IS its journal wire string."""

    NONE = "none"
    CUTOFF = "cutoff"
    MIN_DELTA = "min_delta"
    NW_VELOCITY = "nw_velocity"
    MISSING_NW = "missing_nw"
    OFF_GRID = "off_grid"
    POSITION_OPEN = "position_open"
    NO_EDGE = "no_edge"
    NO_CASH = "no_cash"
    POSITION_CAP = "position_cap"
    ACCOUNT_CAP = "account_cap"
    PAUSED = "paused"
    HALT = "halt"
    MID_SPIKE = "mid_spike"
    KILL = "kill"
    RECOVERY = "recovery"
    MIN_PRICE = "min_price"
    MAX_PRICE = "max_price"
    WIDE_SPREAD = "wide_spread"
    WINDING_DOWN = "winding_down"
    OWNERSHIP_UNRESOLVED = "ownership_unresolved"


class TradingDisabled(RuntimeError):
    """Trading cannot start or continue; the message is a fixed safe label."""


class SidecarUnavailable(TradingDisabled):
    """The collector sidecar went missing, changed or became non-tradeable."""


@dataclass(frozen=True)
class RawBookPair:
    """Raw YES/NO best bid/ask/mid read straight off the two token books."""

    yes_best_bid: float | None
    yes_best_ask: float | None
    yes_mid: float | None
    no_best_bid: float | None
    no_best_ask: float | None
    no_mid: float | None


@dataclass(frozen=True)
class SignalDecision:
    """One snapshot decision. Fair fields stay None when the model was not called."""

    snapshot: GameSnapshot
    arrived_at: float
    raw: RawBookPair
    market_p_radiant: float | None
    market_radiant_prior: float | None
    radiant_fair: float | None
    yes_fair: float | None
    reason: SignalReason
    entry_block: EntryBlock
    entry_token_id: str | None
    entry_price: float
    exit_state: str
    pos_yes: float
    pos_no: float
    feed_source: FeedSource
    feed_received_at_utc: str
    model_evaluated: bool
    raw_delta: float | None


@dataclass(frozen=True)
class ModelFair:
    """One model result: fair prices and the unclipped Radiant-side delta."""

    radiant: float
    yes: float
    raw_delta: float


@dataclass(frozen=True)
class SidecarMeta:
    """One validated sidecar projection: fork meta, raw tick string, immutable binding."""

    meta: MarketMeta
    tick_size_str: str
    binding: "SidecarBinding"


@dataclass(frozen=True)
class SidecarBinding:
    """The immutable collector-sidecar identity one session is bound to."""

    condition_id: str
    market_slug: str
    event_slug: str
    event_id: str
    market_kind: MarketKind
    map_number: int | None
    outcome_0_name: str
    outcome_0_token: str
    outcome_1_name: str
    outcome_1_token: str
    neg_risk: bool
    grid_series_id: str | None


@dataclass(frozen=True)
class SessionEndSnapshot:
    """The engine risk/position labels taken before shutdown, or all-None."""

    positions: dict[str, float]
    leftover_yes: float
    leftover_no: float
    net_cash: float | None
    inventory_value: float | None
    equity: float | None
