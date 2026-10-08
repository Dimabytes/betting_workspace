"""Apply CLOB /data/trades rows onto the wallet ledger with the WS fill key."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from polymaker.domain import Fill, Side, TradeState
from polymaker.state.tracker import TradeEvent

from trader.fill_parsing import (
    TRADE_STATES,
    cash_delta,
    event_ts,
    fill_key,
    normalize_maker_trades,
    trade_time_or_receipt,
)
from trader.wallet_store import WalletStateStore

# Backfill only books trades the exchange already settled. FAILED and RETRYING
# moved no cash. MATCHED is still in flight and belongs to the live WS path,
# which can still reverse it; it reappears here as CONFIRMED on a later pass.
_SETTLED = {TradeState.MINED, TradeState.CONFIRMED}


@dataclass(frozen=True, slots=True)
class RestFill:
    """One of our fills parsed from a CLOB trade, with the side of the book we sat on."""

    event: TradeEvent
    is_maker: bool


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Counts from one REST trade pass. `cash` is newly credited, not MATCHED confirms."""

    applied: int
    confirmed: int
    cash: float
    keys: tuple[str, ...]


def clob_number(raw: object) -> float | None:
    """Parse a CLOB numeric field, or None when the value is not a number."""
    if type(raw) is bool or raw is None:
        return None
    if type(raw) is int or type(raw) is float:
        return float(raw)
    if type(raw) is str:
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _rest_payload(row: object) -> dict[str, object] | None:
    """Copy a REST trade and fill fields `normalize_maker_trades` requires."""
    if type(row) is not dict:
        return None
    payload = dict(cast(dict[str, object], row))
    if not payload.get("status"):
        payload["status"] = "CONFIRMED"
    if payload.get("timestamp") in (None, ""):
        match_time = payload.get("match_time")
        if match_time not in (None, ""):
            payload["timestamp"] = match_time
    return payload


def _taker_event(payload: dict[str, object]) -> TradeEvent | None:
    """Our taker fill from a CLOB trade. Maker rows on this object are counterparties."""
    if str(payload.get("trader_side", "")).upper() != "TAKER":
        return None
    status = TRADE_STATES.get(str(payload.get("status", "")).upper())
    if status is None:
        return None
    clob_id = str(payload.get("id", ""))
    order_id = str(payload.get("taker_order_id", ""))
    token = str(payload.get("asset_id", ""))
    size = clob_number(payload.get("size", 0))
    price = clob_number(payload.get("price", 0))
    if not clob_id or not order_id or not token or size is None or price is None:
        return None
    if size <= 0:
        return None
    side = Side.SELL if str(payload.get("side", "")).upper() == "SELL" else Side.BUY
    return TradeEvent(
        token,
        side,
        price,
        size,
        fill_key(clob_id, order_id),
        status,
        event_ts(payload.get("timestamp")),
    )


def rest_fills(
    row: object,
    funder: str,
    other_token: Callable[[str], str | None],
) -> list[RestFill]:
    """Our settled fills on one CLOB trade: maker rows, plus a taker row when we took."""
    payload = _rest_payload(row)
    if payload is None:
        return []
    found = [RestFill(ev, True) for ev in normalize_maker_trades(payload, funder, other_token)]
    taker = _taker_event(payload)
    if taker is not None:
        found.append(RestFill(taker, False))
    return [item for item in found if item.event.status in _SETTLED]


def backfill_trades(
    store: WalletStateStore,
    trades: Sequence[object],
    funder: str,
    other_token: Callable[[str], str | None],
) -> BackfillResult:
    """Insert settled fills the ledger lacks, and confirm its pending MATCHED rows.

    A key already sitting at CONFIRMED, FAILED, or SUPERSEDED is left untouched.
    """
    applied = 0
    confirmed = 0
    cash = 0.0
    keys: list[str] = []
    received_at = time.time()
    matched = TradeState.MATCHED.value
    for row in trades:
        for item in rest_fills(row, funder, other_token):
            ev = item.event
            fill = Fill(
                ev.token_id,
                ev.our_side,
                ev.price,
                ev.size,
                ev.trade_id,
                trade_time_or_receipt(ev.ts, received_at),
                is_maker=item.is_maker,
            )
            status = store.ledger_status(ev.trade_id)
            if status is None:
                if not store.apply_backfilled_fill(fill, ev.trade_id):
                    continue
                applied += 1
                cash += cash_delta(fill)
                keys.append(ev.trade_id)
                continue
            if status != matched:
                continue
            result = store.apply_confirmed_fill(fill, ev.trade_id)
            if not result.confirmed:
                continue
            confirmed += 1
            keys.append(ev.trade_id)
    return BackfillResult(applied, confirmed, cash, tuple(keys))
