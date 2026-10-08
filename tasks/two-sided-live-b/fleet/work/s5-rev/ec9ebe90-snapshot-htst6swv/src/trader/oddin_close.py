"""Close Oddin archives whose series ended while this process was down."""

from collections.abc import Callable
from pathlib import Path

from shared.utils.log import get_logger
from shared.utils.trading import drop_share_residue
from trader.bindings import SessionPnl
from trader.match_meta import finalize_match, read_match_meta
from trader.notify import notify_session_finished, session_alert_from_meta
from trader.oddin_live_feed import append_series_closed
from trader.paths import MATCH_META_FILENAME, TRADER_WALLET_DIR
from trader.session_journal import (
    TERMINAL_REASON_FINISHED,
    SessionJournal,
    read_maker_rebate_usdc,
    read_market_kind,
)
from trader.session_types import SessionEndSnapshot
from trader.trading_mode import ExecutionMode

logger = get_logger(__name__)


def close_ended_oddin_archives(
    match_root: Path,
    map_ended: Callable[[str, int], bool],
    position_size: Callable[[str], float],
    net_cash_for_tokens: Callable[[set[str]], float],
    mode: ExecutionMode,
) -> None:
    """Write final and session_end for Oddin maps the catalog already ended.

    execution_cleanup stays absent so the boot scan can cancel and fence first.
    """
    if not match_root.is_dir():
        return
    for archive in sorted(match_root.iterdir(), key=lambda path: path.name):
        if not archive.is_dir() or archive.name == TRADER_WALLET_DIR.name:
            continue
        if not (archive / MATCH_META_FILENAME).is_file():
            continue
        try:
            _close_one(archive, map_ended, position_size, net_cash_for_tokens, mode)
        except Exception as exc:
            logger.warning("oddin archive close failed dir=%s: %s", archive.name, exc)


def _close_one(
    archive: Path,
    map_ended: Callable[[str, int], bool],
    position_size: Callable[[str], float],
    net_cash_for_tokens: Callable[[set[str]], float],
    mode: ExecutionMode,
) -> None:
    document = read_match_meta(archive / MATCH_META_FILENAME)
    if document["final"] is not None or document["feed_source"] != "oddin":
        return
    oddin_match_id = document.get("oddin_match_id")
    if oddin_match_id is None or not map_ended(oddin_match_id, document["map_number"]):
        return
    market_kind = read_market_kind(archive)
    rebate = read_maker_rebate_usdc(archive)
    market = document["market"]
    yes = market["yes_token_id"]
    no = market["no_token_id"]
    yes_size = position_size(yes)
    no_size = position_size(no)
    net_cash = net_cash_for_tokens({yes, no})
    flat = drop_share_residue(yes_size) == 0.0 and drop_share_residue(no_size) == 0.0
    append_series_closed(archive)
    if flat:
        inventory = 0.0
        pnl = SessionPnl(net_cash, inventory)
        equity = net_cash
    else:
        inventory = None
        pnl = None
        equity = None
    finalize_match(document["match_id"], pnl)
    snapshot = SessionEndSnapshot(
        {yes: yes_size, no: no_size},
        yes_size,
        no_size,
        net_cash,
        inventory,
        equity,
    )
    journal = SessionJournal(archive)
    try:
        journal.write_end(TERMINAL_REASON_FINISHED, snapshot)
    finally:
        journal.close()
    if document.get("record_only", False):
        return
    notify_session_finished(
        session_alert_from_meta(document, mode, market_kind),
        net_cash,
        inventory,
        rebate,
        yes_size,
        no_size,
    )
