"""Telegram delivery for the live paper trading daemon."""

import threading
from dataclasses import dataclass

import httpx

from shared.constants.api import HTTP_TIMEOUT_SECONDS
from shared.utils.environment import env_value
from shared.utils.log import get_logger, suppress_http_url_logging
from trader.archive_types import MatchMeta, match_game
from trader.bindings import DiscoveredMatch
from trader.trading_mode import ExecutionMode

logger = get_logger(__name__)

TELEGRAM_BOT_API = "https://api.telegram.org"
SESSION_STARTED_PREFIX = "trader session started"
SESSION_FINISHED_PREFIX = "trader session finished"
SESSION_FEED_DEAD_PREFIX = "trader feed dead"
SESSION_EXHAUSTED_PREFIX = "trader session exhausted"


@dataclass(frozen=True)
class SessionAlert:
    """Public match identity on Telegram. No tokens, keys, condition ids, or paths."""

    game: str
    mode: ExecutionMode
    match_id: str
    radiant: str
    dire: str
    map_number: int
    market_slug: str
    market_kind: str


def send_telegram_message(message: str) -> None:
    """Send one Telegram message; missing credentials or a send failure never raise."""
    token = env_value("TG_BOT_API_TOKEN")
    chat_id = env_value("TG_CHAT_ID")
    if not token or not chat_id:
        logger.warning("telegram message skipped: TG_BOT_API_TOKEN or TG_CHAT_ID not set")
        return
    # httpx INFO prints the full URL, which carries the bot token.
    suppress_http_url_logging()
    url = f"{TELEGRAM_BOT_API}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("telegram message failed with HTTP %d", exc.response.status_code)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("telegram message failed: %s", type(exc).__name__)


def notify_in_background(message: str) -> None:
    """Deliver one alert on a daemon thread so the caller is never delayed."""
    threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()


def session_alert_from_match(discovered: DiscoveredMatch, mode: ExecutionMode) -> SessionAlert:
    """Copy public identity fields for Telegram. Never tokens or condition ids."""
    kind = discovered.market_kind
    kind_label = "unknown" if kind is None else kind
    return SessionAlert(
        game=discovered.game,
        mode=mode,
        match_id=discovered.match_id,
        radiant=discovered.sides.radiant,
        dire=discovered.sides.dire,
        map_number=discovered.map_number,
        market_slug=discovered.market.market_slug,
        market_kind=kind_label,
    )


def session_alert_from_meta(
    document: MatchMeta, mode: ExecutionMode, market_kind: str
) -> SessionAlert:
    """Public identity from a persisted match.json. Never tokens or condition ids."""
    teams = document["teams"]
    market = document["market"]
    return SessionAlert(
        game=match_game(document),
        mode=mode,
        match_id=document["match_id"],
        radiant=teams["radiant"],
        dire=teams["dire"],
        map_number=document["map_number"],
        market_slug=market["market_slug"],
        market_kind=market_kind,
    )


def session_headline(alert: SessionAlert) -> str:
    """Game, mode, kind, sides, map, slug, match id. Shared by start, finish, and faults."""
    return (
        f"{alert.game.upper()} · {alert.mode} · {alert.market_kind}\n"
        f"{alert.radiant} vs {alert.dire} · map {alert.map_number}\n"
        f"{alert.market_slug}\n"
        f"{alert.match_id}"
    )


def session_started_message(alert: SessionAlert) -> str:
    """Opened-market page: same identity block as finish, no PnL."""
    return f"{SESSION_STARTED_PREFIX}\n{session_headline(alert)}"


def session_feed_dead_message(alert: SessionAlert, source: str, feed_id: str | None) -> str:
    """Same identity as session start, plus which feed died."""
    feed_label = "none" if feed_id is None else feed_id
    return (
        f"{SESSION_FEED_DEAD_PREFIX}\n"
        f"{session_headline(alert)}\n"
        f"source {source}  feed {feed_label}; retrying"
    )


def session_exhausted_message(alert: SessionAlert) -> str:
    """Same identity as session start after MAX_CRASH_RESTARTS."""
    return f"{SESSION_EXHAUSTED_PREFIX}\n{session_headline(alert)}"


def _format_usdc(value: float | None) -> str:
    """Four-decimal USDC label; missing values render as n/a."""
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _format_signed_usdc(value: float | None) -> str:
    """USDC with an explicit + on profits so a phone scan can see the sign."""
    if value is None:
        return "n/a"
    if value > 0:
        return f"+{value:.4f}"
    return f"{value:.4f}"


def session_finished_message(
    alert: SessionAlert,
    realized_pnl_usdc: float | None,
    inventory_value_usdc: float | None,
    maker_rebate_usdc: float,
    leftover_yes: float,
    leftover_no: float,
) -> str:
    """Public end-of-match PnL under the same identity as start."""
    net_label = "n/a"
    if realized_pnl_usdc is not None and inventory_value_usdc is not None:
        net_usdc = realized_pnl_usdc + inventory_value_usdc + maker_rebate_usdc
        net_label = _format_signed_usdc(net_usdc)
    return (
        f"{SESSION_FINISHED_PREFIX}\n"
        f"{session_headline(alert)}\n"
        f"net {net_label}\n"
        f"realized {_format_usdc(realized_pnl_usdc)}  "
        f"imv {_format_usdc(inventory_value_usdc)}  "
        f"rebate {_format_usdc(maker_rebate_usdc)}\n"
        f"leftover yes {_format_usdc(leftover_yes)}  no {_format_usdc(leftover_no)}"
    )


def notify_session_finished(
    alert: SessionAlert,
    realized_pnl_usdc: float | None,
    inventory_value_usdc: float | None,
    maker_rebate_usdc: float,
    leftover_yes: float,
    leftover_no: float,
) -> None:
    """Best-effort Telegram on a match final; never raises. WalletHost keeps running."""
    message = session_finished_message(
        alert,
        realized_pnl_usdc,
        inventory_value_usdc,
        maker_rebate_usdc,
        leftover_yes,
        leftover_no,
    )
    logger.info("%s", message)
    try:
        notify_in_background(message)
    except Exception as exc:
        logger.warning("trader session finished notify failed: %s", type(exc).__name__)
