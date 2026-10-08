import httpx

from shared.constants.api import POLYMARKET_CLOB_API, QUOTE_TRAILING_SECONDS
from shared.types.polymarket import PricePoint
from shared.utils.http import http_client
from shared.utils.log import get_logger
from shared.utils.price_history import last_aligned_pre_anchor_pair, parse_history
from shared.utils.telonex_book import PAIR_SUM_TOLERANCE, normalize_pair_mids

logger = get_logger(__name__)

PRIOR_GET_TIMEOUT_SECONDS = 3.0


def fetch_market_prior(
    yes_token_id: str,
    no_token_id: str,
    anchor_ts: int,
    *,
    yes_is_radiant: bool,
) -> float | None:
    """Return P(Radiant) from same-bar minute mids before `anchor_ts`, or None."""
    start_ts = anchor_ts - QUOTE_TRAILING_SECONDS
    with http_client() as client:
        yes_history = _get_token_history(client, yes_token_id, start_ts, anchor_ts)
        no_history = _get_token_history(client, no_token_id, start_ts, anchor_ts)
    pair = last_aligned_pre_anchor_pair(yes_history, no_history, anchor_ts)
    if pair is None:
        logger.warning("trader market prior failed: missing_quote")
        return None
    if yes_is_radiant:
        radiant_mid, dire_mid = pair.left.price, pair.right.price
    else:
        radiant_mid, dire_mid = pair.right.price, pair.left.price
    prior = normalize_pair_mids(
        radiant_mid=radiant_mid, dire_mid=dire_mid, tolerance=PAIR_SUM_TOLERANCE
    )
    if prior is None:
        logger.warning("trader market prior failed: pair_broken")
        return None
    logger.info("trader market prior %s", prior)
    return prior


def _get_token_history(
    client: httpx.Client, token_id: str, start_ts: int, end_ts: int
) -> list[PricePoint]:
    """GET one token's minute mids over the trailing window."""
    response = client.get(
        f"{POLYMARKET_CLOB_API}/prices-history",
        params={
            "market": token_id,
            "fidelity": 1,
            "startTs": start_ts,
            "endTs": end_ts,
        },
        timeout=PRIOR_GET_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return parse_history(response.json())
