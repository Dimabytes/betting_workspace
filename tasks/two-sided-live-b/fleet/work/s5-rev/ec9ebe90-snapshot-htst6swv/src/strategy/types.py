"""Frozen Follow300 snapshots, inbound events, and outbound plans."""

from dataclasses import dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]
OrderStatus = Literal["pending", "live", "canceling", "unknown", "gone"]
SettlementKind = Literal["matched", "confirmed", "failed"]
OwnershipReason = Literal["unknown", "contradictory"]
BlockReason = Literal[
    "",
    "cutoff",
    "min_delta",
    "nw_velocity",
    "missing_nw",
    "wide_spread",
    "min_price",
    "max_price",
    "no_cash",
    "position_cap",
    "account_cap",
    "ladder_budget",
    "stale_signal",
    "stale_book",
    "pair_tolerance",
    "band",
    "anchor",
    "halt",
    "paused",
    "game_end",
    "recovery",
    "fair",
    "reduce_only",
    "ownership_unresolved",
    "mid_spike",
    "kill",
    "winding_down",
    "position_open",
]


@dataclass(frozen=True)
class TokenBook:
    token_index: int
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts_ns: int


@dataclass(frozen=True)
class BookPair:
    tokens: tuple[TokenBook, TokenBook]

    def __post_init__(self) -> None:
        for index, book in enumerate(self.tokens):
            if book.token_index != index:
                raise ValueError(f"tokens[{index}] has token_index={book.token_index}")


@dataclass(frozen=True)
class RawDeltaSignal:
    predicted_delta: float
    received_ns: int
    source_received_ns: int
    anchor_p: float
    deaths_radiant: int
    deaths_dire: int


@dataclass(frozen=True)
class KillWait:
    """One side's pending kill wait: table deaths must reach awaited_deaths by until_ns."""

    awaited_deaths: int
    until_ns: int


@dataclass(frozen=True)
class KillGate:
    """Per-side kill waits from the latest scoreboard kill marker."""

    radiant: KillWait
    dire: KillWait


@dataclass(frozen=True)
class GameClock:
    now_ns: int
    game_second: int
    paused: bool
    game_ended: bool


@dataclass(frozen=True)
class MarketLimits:
    min_order_size: float
    tick_size: float
    pair_sum_tolerance: float
    radiant_token_index: int


@dataclass(frozen=True)
class FreshnessLimits:
    book_stale_s: float
    entry_stale_s: float
    exit_stale_s: float


@dataclass(frozen=True)
class Permissions:
    halt: bool
    reduce_only: bool
    allow_buy: bool
    allow_sell: bool
    sell_unconfirmed: bool


@dataclass(frozen=True)
class Budget:
    """cap_room is this map. account_cap_room is the whole wallet; inf means no account cap.

    The core keeps the last BudgetUpdate minus BUYs placed since that update.
    """

    cash_usdc: float
    cap_room_usdc: float
    account_cap_room_usdc: float


@dataclass(frozen=True)
class RestingOrder:
    order_id: str
    episode_id: int
    token_index: int
    side: Side
    price: float
    submitted_qty: float
    filled_qty: float
    level_index: int | None
    status: OrderStatus
    accepted: bool
    partially_filled: bool
    cancel_reason: str
    ack_reason: str
    placed_ns: int
    accepted_ns: int | None


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    episode_id: int
    token_index: int
    side: Side
    price: float
    submitted_qty: float
    filled_qty: float
    level_index: int | None
    terminal: bool
    accepted: bool
    partially_filled: bool
    placed_ns: int
    accepted_ns: int | None


@dataclass(frozen=True)
class Rung:
    index: int
    price: float
    filled_qty: float
    live_id: str | None
    done: bool
    held_qty: float = 0.0
    held_cost: float = 0.0


@dataclass(frozen=True)
class TokenInventory:
    token_index: int
    qty: float
    cost_basis: float
    last_buy_ns: int | None


@dataclass(frozen=True)
class Position:
    token_index: int | None
    qty: float
    cost_basis: float


@dataclass(frozen=True)
class EpisodeArchive:
    episode_id: int
    token_index: int
    rungs: tuple[Rung, ...]
    has_buy_fill: bool
    episode_buy_notional: float
    winding_down: bool


@dataclass(frozen=True)
class PendingFill:
    fill_id: str
    order_id: str
    qty: float
    price: float
    now_ns: int
    token_index: int | None
    side: Side | None
    inventory_credited: bool
    reason: OwnershipReason


@dataclass(frozen=True)
class QuoteSchedule:
    dirty_open_ns: int | None
    last_eval_ns: int


@dataclass(frozen=True)
class Latch:
    fair_radiant: float
    predicted_delta: float
    anchor_p: float
    book_p_radiant: float
    fair_valid: bool
    fair_ts_ns: int
    signal_ts_ns: int
    anchored_ns: int
    no_buy_reason: str


def empty_inventory() -> tuple[TokenInventory, TokenInventory]:
    return (
        TokenInventory(token_index=0, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
    )


def selected_position(inventory: tuple[TokenInventory, TokenInventory]) -> Position:
    left, right = inventory
    if left.qty <= 0.0 and right.qty <= 0.0:
        return Position(token_index=None, qty=0.0, cost_basis=0.0)
    if left.qty >= right.qty:
        return Position(token_index=0, qty=left.qty, cost_basis=left.cost_basis)
    return Position(token_index=1, qty=right.qty, cost_basis=right.cost_basis)


def total_qty(inventory: tuple[TokenInventory, TokenInventory]) -> float:
    return inventory[0].qty + inventory[1].qty


@dataclass(frozen=True)
class MidSample:
    ts_ns: int
    mid: float


@dataclass(frozen=True)
class MidSpikeState:
    samples: tuple[tuple[MidSample, ...], tuple[MidSample, ...]]
    cooloff_until_ns: int


@dataclass(frozen=True)
class StrategyState:
    clock: GameClock
    books: BookPair | None
    signal: RawDeltaSignal | None
    limits: MarketLimits
    freshness: FreshnessLimits
    permissions: Permissions
    budget: Budget
    episode_id: int
    episode_counter: int
    episode_token_index: int | None
    has_buy_fill: bool
    episode_buy_notional: float
    winding_down: bool
    sell_only: bool
    recovery_pending: bool
    recovery_generation: int
    rungs: tuple[Rung, ...]
    orders: tuple[RestingOrder, ...]
    records: tuple[OrderRecord, ...]
    archives: tuple[EpisodeArchive, ...]
    seen_fill_ids: tuple[str, ...]
    pending_ownership: tuple[PendingFill, ...]
    unconfirmed_keys: tuple[str, ...]
    inventory: tuple[TokenInventory, TokenInventory]
    latch: Latch | None
    mid_spike: MidSpikeState
    kill_gate: KillGate
    next_order_seq: int
    schedule: QuoteSchedule
    reprice_since_ns: tuple[int | None, int | None]
    delta_gate_open: bool = False

    @property
    def position(self) -> Position:
        return selected_position(self.inventory)

    @property
    def last_buy_ns(self) -> int | None:
        held = self.position
        if held.token_index is None:
            return None
        return self.inventory[held.token_index].last_buy_ns

    @property
    def ownership_unresolved(self) -> bool:
        return bool(self.pending_ownership)


@dataclass(frozen=True)
class BookUpdate:
    now_ns: int
    books: BookPair | None


@dataclass(frozen=True)
class SignalUpdate:
    now_ns: int
    signal: RawDeltaSignal | None


@dataclass(frozen=True)
class ClockUpdate:
    now_ns: int
    clock: GameClock


@dataclass(frozen=True)
class KillGateUpdate:
    """Latest scoreboard kill state; replaces the stored gate wholesale."""

    now_ns: int
    gate: KillGate


@dataclass(frozen=True)
class PermissionsUpdate:
    now_ns: int
    permissions: Permissions


@dataclass(frozen=True)
class BudgetUpdate:
    now_ns: int
    budget: Budget


@dataclass(frozen=True)
class LimitsUpdate:
    now_ns: int
    limits: MarketLimits


@dataclass(frozen=True)
class Wake:
    now_ns: int
    forced: bool


@dataclass(frozen=True)
class Fill:
    now_ns: int
    fill_id: str
    order_id: str
    qty: float
    price: float
    token_index: int
    side: Side


@dataclass(frozen=True)
class OrderAccepted:
    now_ns: int
    order_id: str


@dataclass(frozen=True)
class OrderRejected:
    now_ns: int
    order_id: str
    reason: str


@dataclass(frozen=True)
class SubmitTimeout:
    now_ns: int
    order_id: str


@dataclass(frozen=True)
class CancelAck:
    now_ns: int
    order_id: str


@dataclass(frozen=True)
class CancelTimeout:
    now_ns: int
    order_id: str


@dataclass(frozen=True)
class CancelUnsettled:
    now_ns: int
    order_id: str


@dataclass(frozen=True)
class BuySettled:
    now_ns: int
    order_id: str
    matched_qty: float


@dataclass(frozen=True)
class Recovery:
    now_ns: int
    restored_buy_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryVerified:
    now_ns: int
    generation: int
    inventory: tuple[TokenInventory, TokenInventory]


@dataclass(frozen=True)
class OwnershipResolved:
    now_ns: int
    order_id: str
    episode_id: int
    token_index: int
    side: Side
    price: float
    submitted_qty: float
    filled_qty: float
    level_index: int | None
    terminal: bool


@dataclass(frozen=True)
class SettlementStatus:
    now_ns: int
    fill_id: str
    status: SettlementKind


@dataclass(frozen=True)
class Merged:
    now_ns: int
    token_index: int
    qty: float


InboundEvent = (
    BookUpdate
    | SignalUpdate
    | ClockUpdate
    | KillGateUpdate
    | PermissionsUpdate
    | BudgetUpdate
    | LimitsUpdate
    | Wake
    | Fill
    | OrderAccepted
    | OrderRejected
    | SubmitTimeout
    | CancelAck
    | CancelTimeout
    | CancelUnsettled
    | BuySettled
    | Recovery
    | RecoveryVerified
    | OwnershipResolved
    | SettlementStatus
    | Merged
)


@dataclass(frozen=True)
class KeepOrder:
    order_id: str
    level_index: int | None


@dataclass(frozen=True)
class MoveOrder:
    order_id: str
    from_level: int
    to_level: int


@dataclass(frozen=True)
class CancelOrder:
    order_id: str
    reason: str


@dataclass(frozen=True)
class PlaceOrder:
    order_id: str
    episode_id: int
    token_index: int
    side: Side
    price: float
    quantity: float
    level_index: int | None
    reduce_only: bool


@dataclass(frozen=True)
class Plan:
    keep: tuple[KeepOrder, ...]
    moves: tuple[MoveOrder, ...]
    cancels: tuple[CancelOrder, ...]
    places: tuple[PlaceOrder, ...]
    block_reason: BlockReason


@dataclass(frozen=True)
class EngineOutput:
    state: StrategyState
    plan: Plan
    next_wake_ns: int
