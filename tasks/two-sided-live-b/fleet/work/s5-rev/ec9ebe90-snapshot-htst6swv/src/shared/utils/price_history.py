from dataclasses import dataclass
from typing import cast

from shared.types.polymarket import PricePoint
from shared.utils.parsing import parse_ts

PRIOR_PAIR_ALIGN_SLOP_SECONDS = 30


@dataclass(frozen=True)
class TokenQuote:
    """The last minute mid for one token before its anchor."""

    price: float
    quote_ts: int


@dataclass(frozen=True)
class PreAnchorPair:
    """Same-bar lasts, or both legs at the older print when lasts are a full bar apart."""

    left: TokenQuote
    right: TokenQuote


def parse_history(body: object) -> list[PricePoint]:
    """Convert one /prices-history response body into cache points."""
    if not isinstance(body, dict):
        raise ValueError(f"prices-history response is not an object: {type(body)}")
    raw_history = cast(dict[str, object], body).get("history")
    if not isinstance(raw_history, list):
        raise ValueError("prices-history response carries no history list")
    points: list[PricePoint] = []
    for entry in cast(list[object], raw_history):
        if not isinstance(entry, dict):
            raise ValueError(f"prices-history point is not an object: {entry!r}")
        point = cast(dict[str, object], entry)
        price = point["p"]
        if not isinstance(price, (int, float, str)):
            raise ValueError(f"prices-history point has no numeric price: {entry!r}")
        quote_ts = parse_ts(point["t"])
        if quote_ts is None:
            raise ValueError(f"prices-history point has no timestamp: {entry!r}")
        points.append({"t": quote_ts, "p": float(price)})
    return points


def last_pre_anchor_quote(history: list[PricePoint], anchor_ts: int) -> TokenQuote | None:
    """Latest mid strictly before the anchor, or None when there is none."""
    latest: TokenQuote | None = None
    for point in history:
        quote_ts = point["t"]
        if quote_ts >= anchor_ts:
            continue
        if latest is None or quote_ts > latest.quote_ts:
            latest = TokenQuote(price=float(point["p"]), quote_ts=quote_ts)
    return latest


def last_aligned_pre_anchor_pair(
    left_history: list[PricePoint], right_history: list[PricePoint], anchor_ts: int
) -> PreAnchorPair | None:
    """Keep same-bar lasts; rewind to min(ts) only when the gap exceeds the slop."""
    left = last_pre_anchor_quote(left_history, anchor_ts)
    right = last_pre_anchor_quote(right_history, anchor_ts)
    if left is None or right is None:
        return None
    if abs(left.quote_ts - right.quote_ts) <= PRIOR_PAIR_ALIGN_SLOP_SECONDS:
        return PreAnchorPair(left=left, right=right)
    cutoff = min(left.quote_ts, right.quote_ts) + 1
    aligned_left = last_pre_anchor_quote(left_history, cutoff)
    aligned_right = last_pre_anchor_quote(right_history, cutoff)
    if aligned_left is None or aligned_right is None:
        return None
    return PreAnchorPair(left=aligned_left, right=aligned_right)
