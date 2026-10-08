"""Persisted match.json and session.jsonl shapes for the trader daemon."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from trader.live_feed import FeedSource, MatchPhase


class MatchMetaTeams(TypedDict):
    """The two team names: Radiant and Dire."""

    radiant: str
    dire: str


class MatchMetaMarket(TypedDict):
    """The Polymarket market the match is traded on.

    `tick_size` and `min_order_size` stay collector decimal strings; a null
    `grid_series_id` means the collector recorded no series id, not a missing
    field. `outcome_0_name` / `outcome_1_name` are the YES/NO labels the GRID
    feed uses to pin Radiant/Dire.
    """

    condition_id: str
    market_slug: str
    event_slug: str
    yes_token_id: str
    no_token_id: str
    yes_is_radiant: bool
    outcome_0_name: str
    outcome_1_name: str
    tick_size: str | None
    min_order_size: str | None
    neg_risk: bool
    grid_series_id: str | None


class MatchMetaModel(TypedDict):
    """The pinned model identity, taken from model.json by the session owner."""

    name: str
    trained_at: str


class MatchMetaPnl(TypedDict):
    """The session's engine PnL handoff at match end, in USDC."""

    realized_pnl_usdc: float
    unrealized_pnl_usdc: float


MatchWinner = Literal["radiant", "dire"]


class MatchMetaFinal(TypedDict):
    """The end-of-match block computed from the persisted feed archive.

    Steam fills pause/missing from state.jsonl. GRID writes JSON null for both.
    `winner` is null when the source is ambiguous. `pnl` is null when no final
    PnL handoff was available — a valid zero PnL stays two `0.0` values.
    """

    duration_seconds: int
    winner: MatchWinner | None
    pause_seconds: int | None
    missing_seconds: int | None
    snapshot_count: int
    pnl: MatchMetaPnl | None


class MatchMeta(TypedDict):
    """Root of one match.json document (schema_version 3 through 9).

    Schema 5, 6, 7, 8, and 9 require `game`; 3/4 omit it (readers default to dota
    at MatchStart rebuild). Schema 4/5 carried a `kalshi` object; the reader
    validates it and drops it, so it is not a field here. Schema 7 adds PGL
    channel, expected match id, assumed delay, and PGL/Steam side orientation.
    Schema 8 replaces those with Oddin match id and probed delay. Schema 9 adds
    the record-only marker: the archive holds the feed but no session ran.
    """

    schema_version: int
    match_id: str
    game: NotRequired[str]
    steam_match_id: str | None
    server_steam_id: str | None
    league_id: int | None
    tournament: str | None
    teams: MatchMetaTeams
    map_number: int
    joined_at_second: int
    joined_at_utc: str
    horn_at_utc: str
    market: MatchMetaMarket
    model: MatchMetaModel
    feed_source: Literal["steam", "grid", "oddin"]
    steam_delay_s: int | None
    steam_observed_lag_s: float | None
    grid_delay_s: float | None
    pgl_channel: NotRequired[int | None]
    pgl_match_id: NotRequired[str | None]
    pgl_delay_s: NotRequired[int | None]
    pgl_sides_match_steam: NotRequired[bool]
    oddin_match_id: NotRequired[str | None]
    oddin_delay_s: NotRequired[int | None]
    record_only: NotRequired[bool]
    final: MatchMetaFinal | None


def match_game(document: MatchMeta) -> str:
    """Schema 3/4 omit game; those archives are Dota."""
    game = document.get("game")
    if game is None:
        return "dota"
    return game


@dataclass(frozen=True)
class MatchArchiveSummary:
    """One-pass reduction of a persisted feed archive."""

    snapshot_count: int
    duration_seconds: int
    pause_seconds: int | None
    missing_seconds: int | None
    winner: MatchWinner | None
    finished: bool


@dataclass(frozen=True)
class ArchiveOutcome:
    """One reduced feed archive: the summary plus the stats only that source can measure.

    GRID fills `grid_delay_s`; Oddin leaves it None. `finalize_match` writes it
    over the None in the start document.
    """

    summary: MatchArchiveSummary
    archive_path: Path
    grid_delay_s: float | None
    trusted_horn: str | None


class SessionModelRef(TypedDict):
    """The pinned model identity inside a session journal record."""

    name: str
    trained_at: str


class SessionSidecarOutcome(TypedDict):
    """One pinned outcome inside the durable sidecar binding record."""

    index: int
    name: str
    tokenId: str


class SessionSidecarBindingRecord(TypedDict):
    """The durable nonsecret sidecar binding persisted once per match archive.

    Written at first acceptance and re-read on resume so a process restart
    can never re-baseline a changed static sidecar. Public market
    identifiers only — never config, env, URLs or secrets.
    """

    schema_version: int
    condition_id: str
    market_slug: str
    event_slug: str
    event_id: str
    market_kind: str
    map_number: int | None
    outcomes: list[SessionSidecarOutcome]
    neg_risk: bool
    grid_series_id: str | None


class SessionStartRecord(TypedDict):
    """First session.jsonl record: provenance of the session that produced it.

    Carries only public match/condition identifiers, the pinned model
    identity, the local git commit, the execution mode (paper or live) and the
    canonical nonsecret sidecar binding (null when the sidecar was absent at
    the first event). Never config content, env, URLs or secrets.
    """

    schema_version: int
    kind: Literal["session_start"]
    match_id: str
    condition_id: str
    git_commit: str
    model: SessionModelRef
    execution_mode: Literal["paper", "live"]
    sidecar_binding: SessionSidecarBindingRecord | None
    clip_usdc: float
    clip_reason: str


class SessionSignalTop(TypedDict):
    """The top-net-worth scoreboard block nested inside a signal's snapshot."""

    top1_nw_adv: int
    radiant_top1_nw_ratio: float
    dire_top1_nw_ratio: float
    top3_nw_adv: int
    radiant_top3_nw_ratio: float
    dire_top3_nw_ratio: float


class SessionSignalSnapshot(TypedDict):
    """The normalized feed snapshot a signal decision ran on."""

    second: int
    server_timestamp: int
    phase: MatchPhase
    paused: bool
    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: SessionSignalTop


class SessionSignalRecord(TypedDict):
    """One per-feed-event trading decision: raw best bid/ask/mids and the model path.

    Only the decision inputs are persisted — best bid, best ask and mid per
    token, the normalized Radiant price, the map-load prior, and the fair values.
    `reason` is the enum-like no-signal label when the model was not called; a
    valid model signal uses the fixed label "model". Schema 2 adds `entry_block`.
    Schema 6 writes `venue=polymarket`; schema 1-5 omit it.

    `entry_block` covers the BUY side only. `exit_state` carries the resting
    SELL's status ("none" when there is none) and `pos_yes`/`pos_no` the held
    shares, so "long and the exit is wedged" is readable without a replay.
    Rows written before these fields omit all three.

    Newer rows carry the decision inputs as written: `recorded_at_utc` is the
    journal's own write clock, `feed_source`/`feed_received_at_utc` pin the
    accepted feed event, `game_snapshot` is the normalized snapshot verbatim,
    `model_evaluated` marks a successful model call, and `raw_delta` is the
    unclipped Radiant-side delta of that call (null otherwise). Rows written
    before these fields omit all six.
    """

    kind: Literal["signal"]
    venue: NotRequired[Literal["polymarket"]]
    second: int
    yes_best_bid: float | None
    yes_best_ask: float | None
    yes_mid: float | None
    no_best_bid: float | None
    no_best_ask: float | None
    no_mid: float | None
    market_p_radiant: float | None
    market_radiant_prior: float | None
    radiant_fair: float | None
    yes_fair: float | None
    reason: str
    entry_block: str
    exit_state: NotRequired[str]
    pos_yes: NotRequired[float]
    pos_no: NotRequired[float]
    recorded_at_utc: NotRequired[str]
    feed_source: NotRequired[FeedSource]
    feed_received_at_utc: NotRequired[str]
    game_snapshot: NotRequired[SessionSignalSnapshot]
    model_evaluated: NotRequired[bool]
    raw_delta: NotRequired[float | None]


class SessionPlacedQuote(TypedDict):
    """One placed order line inside a quote record; a quote, not a book mirror."""

    token_id: str
    side: Literal["BUY", "SELL"]
    price: float
    size: float


class SessionQuoteRecord(TypedDict):
    """One engine placement/cancellation batch with the decision it ran under.

    `decision` is the session's gate at that moment (normal vs forced
    reduce-only); `fv_source` says whether the engine fair value came from the
    model cell or from the original fork computation.
    """

    kind: Literal["quote"]
    venue: NotRequired[Literal["polymarket"]]
    decision: Literal["normal", "reduce_only"]
    fv_source: Literal["model", "engine"]
    placed: list[SessionPlacedQuote]
    canceled: list[str]
    second: int


class SessionFillRecord(TypedDict):
    """One durable fill with the post-fill position, net cash, and ledger key.

    `ts_utc` is the exchange trade time from Fill.ts, not CONFIRMED receipt.
    `fill_key` is always written on schema 5. Older records may omit it.
    """

    kind: Literal["fill"]
    venue: NotRequired[Literal["polymarket"]]
    token_id: str
    side: Literal["BUY", "SELL"]
    price: float
    size: float
    is_maker: bool
    position_after: float
    net_cash: float
    second: int
    ts_utc: str
    fill_key: NotRequired[str]


class SessionLateFillRecord(TypedDict):
    """A fill written after session_end, recovered from user WS or REST backfill."""

    kind: Literal["late_fill"]
    venue: NotRequired[Literal["polymarket"]]
    token_id: str
    side: Literal["BUY", "SELL"]
    price: float
    size: float
    is_maker: bool
    position_after: float
    net_cash: float
    second: int
    ts_utc: str
    fill_key: str
    source: Literal["user_ws", "rest_backfill"]


class SessionTickSizeChangeRecord(TypedDict):
    """One sidecar tick change; old/new stay the collector decimal strings."""

    kind: Literal["tick_size_change"]
    old_tick_size: str
    new_tick_size: str


class SessionHistoryGapRecord(TypedDict):
    """A feed tick skipped on a history-tape gap: no signal, no watchdog reset.

    The snapshot stays on the tape, so a later tick can pivot to it.
    """

    kind: Literal["history_gap"]
    venue: NotRequired[Literal["polymarket"]]
    second: int


class SessionTradingErrorRecord(TypedDict):
    """One trading fault: phase and exception type only, never the message."""

    kind: Literal["trading_error"]
    venue: NotRequired[Literal["polymarket"]]
    phase: str
    error_type: str


class SessionEndRecord(TypedDict):
    """Final session.jsonl record: positions and the engine risk labels.

    The cash block is null when trading never started or the values were not
    finite; positions map token ids to held size.
    """

    kind: Literal["session_end"]
    venue: NotRequired[Literal["polymarket"]]
    terminal_reason: str
    positions: dict[str, float]
    net_cash: float | None
    inventory_value: float | None
    equity: float | None


SessionRecord = (
    SessionStartRecord
    | SessionSignalRecord
    | SessionQuoteRecord
    | SessionFillRecord
    | SessionLateFillRecord
    | SessionTickSizeChangeRecord
    | SessionHistoryGapRecord
    | SessionTradingErrorRecord
    | SessionEndRecord
)
