"""Prove unsettled BUYs from raw user-WS order messages and REST trades."""

# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from polymaker.execution.gateway import ExecutionGateway
from py_clob_client_v2.clob_types import TradeParams

from shared.utils.log import get_logger
from trader.core_persistence import UnsettledBuy
from trader.fill_parsing import maker_order_id_of
from trader.trade_backfill import backfill_trades, clob_number
from trader.wallet_store import WalletStateStore

logger = get_logger(__name__)

# ponytail: one complete trades read can lag and prove zero. Upgrade path: require
# the same sum on two passes before proving zero.
REST_PROOF_MIN_AGE_S = 60
ORDER_LIFETIME_S = 7200
UNSETTLED_ALERT_AGE_S = 600


@dataclass(frozen=True)
class BuyExecutionProof:
    venue_id: str
    matched_qty: float


@dataclass(frozen=True)
class BuyCancellation:
    venue_id: str


def parse_buy_cancellation(msg: Mapping[str, object]) -> BuyCancellation | None:
    """BUY cancellation that may open a row. Empty size_matched is still a cancellation."""
    if str(msg.get("side", "")).upper() != "BUY":
        return None
    venue_id = msg.get("id")
    if type(venue_id) is not str or not venue_id:
        return None
    if str(msg.get("type", "")).upper() != "CANCELLATION":
        return None
    if str(msg.get("status", "")).upper() != "CANCELED":
        return None
    return BuyCancellation(venue_id=venue_id)


def parse_terminal_buy_proof(msg: Mapping[str, object]) -> BuyExecutionProof | None:
    """Matched qty from a terminal BUY order message, or None when it is not proof."""
    if str(msg.get("side", "")).upper() != "BUY":
        return None
    venue_id = msg.get("id")
    if type(venue_id) is not str or not venue_id:
        return None
    matched_qty = _nonnegative_qty(msg.get("size_matched"))
    if matched_qty is None or not _terminal_buy(msg, matched_qty):
        return None
    return BuyExecutionProof(venue_id=venue_id, matched_qty=matched_qty)


def eligible_rest_rows(rows: tuple[UnsettledBuy, ...], now_s: float) -> tuple[UnsettledBuy, ...]:
    """Unproven rows old enough for a trades read."""
    return tuple(
        row for row in rows if not row.proven and now_s - row.created_at >= REST_PROOF_MIN_AGE_S
    )


async def collect_rest_buy_proofs(
    gateway: ExecutionGateway,
    rows: tuple[UnsettledBuy, ...],
    now_s: float,
    store: WalletStateStore,
    other_token: Callable[[str], str | None],
) -> tuple[BuyExecutionProof, ...]:
    """Sum maker fills for eligible rows. A failed token contributes no proof."""
    eligible = eligible_rest_rows(rows, now_s)
    if not eligible:
        return ()
    client = gateway._client
    funder = gateway.funder
    if client is None or not funder:
        return ()
    grouped: dict[str, list[UnsettledBuy]] = {}
    for row in eligible:
        grouped.setdefault(row.token_id, []).append(row)
    proofs: list[BuyExecutionProof] = []
    for token_id in sorted(grouped):
        token_rows = tuple(grouped[token_id])
        oldest = min(row.created_at for row in token_rows)
        params = TradeParams(
            maker_address=funder,
            asset_id=token_id,
            after=int(oldest) - ORDER_LIFETIME_S,
        )

        def fetch(params: TradeParams = params) -> list[object]:
            return list(client.get_trades(params))

        try:
            trades = await gateway._io(fetch)
        except Exception as exc:
            logger.warning("unsettled buy proof failed token=%s err=%s", token_id[:12], exc)
            continue
        if type(trades) is not list:
            continue
        parsed = _proofs_from_trades(trades, tuple(row.venue_id for row in token_rows))
        if parsed is None:
            continue
        backfill_trades(store, trades, funder, other_token)
        proofs.extend(parsed)
    return tuple(proofs)


def _terminal_buy(msg: Mapping[str, object], matched_qty: float) -> bool:
    status = str(msg.get("status", "")).upper()
    kind = str(msg.get("type", "")).upper()
    if kind == "CANCELLATION" or status in ("CANCELED", "CANCELLED"):
        return True
    if status == "MATCHED":
        return True
    original = _positive_qty(msg.get("original_size"))
    if original is None:
        return False
    return matched_qty >= original


def _nonnegative_qty(raw: object) -> float | None:
    if type(raw) is str and raw.strip() == "":
        return None
    value = clob_number(raw)
    if value is None or not math.isfinite(value) or value < 0.0:
        return None
    return value


def _positive_qty(raw: object) -> float | None:
    value = _nonnegative_qty(raw)
    if value is None or value <= 0.0:
        return None
    return value


def _proofs_from_trades(
    trades: list[object], venue_ids: tuple[str, ...]
) -> tuple[BuyExecutionProof, ...] | None:
    """None when the reply structure cannot prove a quantity, including zero."""
    sums = {venue_id: 0.0 for venue_id in venue_ids}
    tainted: set[str] = set()
    for trade in trades:
        if type(trade) is not dict:
            return None
        makers = trade.get("maker_orders")
        if type(makers) is not list:
            return None
        for maker in makers:
            if type(maker) is not dict:
                return None
            order_id = maker_order_id_of(maker)
            if order_id is None or order_id not in sums:
                continue
            amount = _nonnegative_qty(maker.get("matched_amount"))
            if amount is None:
                tainted.add(order_id)
                continue
            sums[order_id] += amount
    return tuple(
        BuyExecutionProof(venue_id=venue_id, matched_qty=sums[venue_id])
        for venue_id in venue_ids
        if venue_id not in tainted
    )
