"""Known SELL place refusals: one reason and one hold, shared by freeze and the error counter."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from polymaker.domain import Quote, Side

from shared.utils.log import get_logger
from trader.execution_policy import CROSS_REJECT_HOLD_SECONDS, SELL_REJECT_HOLD_SECONDS
from trader.wallet_store import WalletStateStore

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SellReject:
    """A SELL the venue refused for a reason that is not a place error."""

    reason: str
    hold_seconds: float


def classify_sell_reject(quote: Quote, item: object) -> SellReject | None:
    """Return the pause for this SELL refusal, or None when it counts as an error."""
    if quote.side is not Side.SELL:
        return None
    error = _item_error(item).lower()
    if "not enough balance" in error:
        return SellReject("matched_unsettled", SELL_REJECT_HOLD_SECONDS)
    if "crosses book" in error:
        return SellReject("crosses_book", CROSS_REJECT_HOLD_SECONDS)
    return None


def clob_place_items(resp: object) -> tuple[object, ...]:
    """Unwrap a CLOB post_orders body to the per-quote item list."""
    items = _as_list(resp)
    if items is not None:
        return tuple(items)
    fields = _as_dict(resp)
    if fields is None:
        return ()
    orders = fields.get("orders", fields.get("data", ()))
    nested = _as_list(orders)
    if nested is None:
        return ()
    return tuple(nested)


def freeze_sell_rejects(
    store: WalletStateStore,
    quotes: Sequence[Quote],
    items: tuple[object, ...] | None,
) -> None:
    """Hold each SELL token for the pause its venue refusal names."""
    if items is None:
        return
    for quote, item in zip(quotes, items, strict=False):
        reject = classify_sell_reject(quote, item)
        if reject is None:
            continue
        store.freeze_sell(quote.token_id, reject.hold_seconds)
        logger.info(
            "trader sell frozen token=%s reason=%s seconds=%.0f",
            quote.token_id[:12],
            reject.reason,
            reject.hold_seconds,
        )


def unplaced_quotes_explained(
    dropped_sell: bool,
    sent: Sequence[Quote],
    items: tuple[object, ...] | None,
) -> bool:
    """True when every quote that did not rest is a local SELL drop or a known refusal."""
    if not sent:
        return dropped_sell
    if items is None or len(items) < len(sent):
        return False
    saw_unplaced = dropped_sell
    for quote, item in zip(sent, items, strict=False):
        if _item_order_id(item):
            continue
        saw_unplaced = True
        if classify_sell_reject(quote, item) is None:
            return False
    return saw_unplaced


def _as_dict(value: object) -> dict[object, object] | None:
    if type(value) is dict:
        return cast(dict[object, object], value)
    return None


def _as_list(value: object) -> list[object] | None:
    if type(value) is list:
        return cast(list[object], value)
    return None


def _item_error(item: object) -> str:
    """Return the CLOB error string from one post_orders item, or empty."""
    fields = _as_dict(item)
    if fields is None:
        return str(item)
    for key in ("errorMsg", "error_msg", "error"):
        value = fields.get(key)
        if value:
            return str(value)
    return ""


def _item_order_id(item: object) -> str:
    """Venue id from one post_orders item, or empty when the quote did not rest."""
    fields = _as_dict(item)
    if fields is None:
        return ""
    for key in ("orderID", "orderId", "order_id", "id", "hash"):
        value = fields.get(key)
        if value:
            return str(value)
    return ""
