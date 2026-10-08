"""Parse CLOB trade payloads into TradeEvents. Shared by the user stream and REST backfill."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Callable
from dataclasses import dataclass

from polymaker.domain import Fill, Side, TradeState
from polymaker.state.tracker import TradeEvent

from shared.utils.log import get_logger
from trader.notify import notify_in_background

logger = get_logger(__name__)

TRADE_STATES = {
    "MATCHED": TradeState.MATCHED,
    "MINED": TradeState.MINED,
    "CONFIRMED": TradeState.CONFIRMED,
    "RETRYING": TradeState.RETRYING,
    "FAILED": TradeState.FAILED,
}


# The CLOB user stream reports 0.0 when a trade message carries no parseable
# timestamp. That zero reads as a trade from 1970, which would let the exit
# skip EXIT_SETTLE_SECONDS outright, so anything older than this epoch or ahead
# of the receipt clock falls back to receipt time.
_MIN_TRADE_TS = 1_577_836_800.0  # 2020-01-01T00:00:00Z
_MAX_TRADE_TS_AHEAD_SECONDS = 60.0


def trade_time_or_receipt(ts: float, received_at: float) -> float:
    """The exchange trade time, or the receipt clock when the feed gave no usable stamp."""
    if _MIN_TRADE_TS <= ts <= received_at + _MAX_TRADE_TS_AHEAD_SECONDS:
        return ts
    logger.warning("trader unusable trade timestamp ts=%r; using receipt time", ts)
    return received_at


def fill_key(clob_trade_id: str, maker_order_id: str) -> str:
    """Index-free fill identity: CLOB trade id plus our maker order id."""
    return f"{clob_trade_id}:{maker_order_id}"


def maker_order_id_of(row: object) -> str | None:
    """Return the maker-order id from one maker_orders object, or None."""
    if type(row) is not dict:
        return None
    fields = row
    for key in ("order_id", "id", "maker_order_id"):
        value = fields.get(key)
        if type(value) is str and value:
            return value
    return None


@dataclass(frozen=True, slots=True)
class MakerTradeTaker:
    """Taker-side fields shared while walking maker_orders."""

    addr: str
    asset: str
    side: Side
    outcome: object
    ts: float
    clob_trade_id: str
    status: TradeState
    other_token: Callable[[str], str | None]


def extract_maker_trade(row: object, taker: MakerTradeTaker) -> TradeEvent | None:
    """One of our maker fills, or None when the row is not ours or not parseable."""
    if type(row) is not dict:
        return None
    maker = row
    if str(maker.get("maker_address", "")).lower() != taker.addr:
        return None
    order_id = maker_order_id_of(maker)
    if order_id is None:
        return None
    try:
        size = float(maker.get("matched_amount", 0))
        price = float(maker.get("price", 0))
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    if maker.get("outcome") == taker.outcome:
        token = taker.asset
        our_side = taker.side.opposite
    else:
        token = taker.other_token(taker.asset)
        if token is None:
            return None
        our_side = taker.side
    return TradeEvent(
        token_id=token,
        our_side=our_side,
        price=price,
        size=size,
        trade_id=fill_key(taker.clob_trade_id, order_id),
        status=taker.status,
        ts=taker.ts,
    )


def normalize_maker_trades(
    msg: object,
    our_address: str,
    other_token: Callable[[str], str | None],
) -> list[TradeEvent]:
    """Extract our maker fills with an index-free fill key.

    REST backfill and the user-WS parser must produce the same key even when
    other makers' rows change order.
    """
    if type(msg) is not dict:
        return []
    payload = msg
    status = TRADE_STATES.get(str(payload.get("status", "")).upper())
    if status is None:
        return []
    taker_asset = str(payload.get("asset_id", ""))
    taker_side = _side(payload.get("side"))
    taker_outcome = payload.get("outcome")
    ts = event_ts(payload.get("timestamp"))
    clob_trade_id = str(payload.get("id", ""))
    if not clob_trade_id:
        return []
    makers = payload.get("maker_orders", [])
    if type(makers) is not list:
        return []
    taker = MakerTradeTaker(
        addr=our_address.lower(),
        asset=taker_asset,
        side=taker_side,
        outcome=taker_outcome,
        ts=ts,
        clob_trade_id=clob_trade_id,
        status=status,
        other_token=other_token,
    )
    out: list[TradeEvent] = []
    for row in makers:
        event = extract_maker_trade(row, taker)
        if event is None:
            continue
        if any(existing.trade_id == event.trade_id for existing in out):
            logger.warning("trader duplicate maker fill key")
            notify_in_background("trader duplicate maker fill key")
            return []
        out.append(event)
    return out


def split_fill_key(key: str) -> tuple[str, str]:
    """Split informational columns on the last colon. Does not change the PK."""
    clob_trade_id, separator, maker_order_id = key.rpartition(":")
    if not separator or not maker_order_id:
        return key, key
    return clob_trade_id, maker_order_id


def cash_delta(fill: Fill) -> float:
    """Signed cash of one fill: a SELL credits, a BUY debits."""
    signed = 1.0 if fill.side is Side.SELL else -1.0
    return fill.price * fill.size * signed


def _side(value: object) -> Side:
    return Side.SELL if str(value).upper() == "SELL" else Side.BUY


def event_ts(raw: object) -> float:
    if type(raw) is int or type(raw) is float:
        value = float(raw)
    elif type(raw) is str:
        try:
            value = float(raw)
        except ValueError:
            return 0.0
    else:
        return 0.0
    return value / 1000.0 if value > 1e12 else value
