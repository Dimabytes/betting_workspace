"""Compare CLOB/data-api fills to the live journal and sqlite ledger.

Default `--report` prints per-market gaps. `--apply` writes recovered fills
into live.db and late_fill rows into session.jsonl. `--apply` refuses while
the live container is running.

Invocation:
  PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest \\
    python scripts/backfill_trader_fills.py --report
"""

# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

import argparse
import json
import os
import sqlite3
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv
from polymaker.domain import Side
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import TradeParams

from shared.constants.paths import BASE_DIR
from trader.archived_markets import ArchivedMarket, iter_archived_markets
from trader.clob_transport import use_http1_clob_transport
from trader.paths import SESSION_JOURNAL_FILENAME
from trader.session_journal import append_late_fill
from trader.session_quoting import fill_ts_utc
from trader.trade_backfill import backfill_trades, clob_number, rest_fills
from trader.wallet_store import WalletStateStore

LIVE_CONTAINER = "esports-trader-live-1"
LIVE_ARCHIVE = BASE_DIR / "data" / "trader_live"
PAPER_ARCHIVE = BASE_DIR / "data" / "live_paper"
LIVE_DB = LIVE_ARCHIVE / "wallet" / "live.db"
DATA_API = "https://data-api.polymarket.com/trades"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 2
GAP_CENTS = 0.01


@dataclass(frozen=True, slots=True)
class MatchGroup:
    """One match, plus every archive copy of it that carries a session journal."""

    match_id: str
    condition_id: str
    archive_dirs: tuple[Path, ...]
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class GapRow:
    """Chain vs journal vs sqlite cash for one match."""

    match_id: str
    chain: float
    journal: float
    sqlite: float


def live_container_running() -> bool:
    """True when esports-trader-live-1 is up."""
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", LIVE_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _session_execution_mode(archive_dir: Path) -> str | None:
    path = archive_dir / SESSION_JOURNAL_FILENAME
    try:
        with path.open("r", encoding="utf-8", newline="\n") as handle:
            first = handle.readline()
        document = json.loads(first)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if type(document) is not dict:
        return None
    raw = document.get("execution_mode")
    if raw is None:
        return "paper"
    if raw == "live" or raw == "paper":
        return str(raw)
    return None


def iter_live_markets(roots: Sequence[Path]) -> list[ArchivedMarket]:
    """Archived markets whose session journal says the session ran in live mode."""
    return [
        market
        for market in iter_archived_markets(roots)
        if _session_execution_mode(market.archive_dir) == "live"
    ]


def group_by_match(markets: Sequence[ArchivedMarket]) -> list[MatchGroup]:
    """Merge duplicate archive copies of one match into a single group."""
    order: list[str] = []
    copies: dict[str, list[ArchivedMarket]] = {}
    for market in markets:
        if market.match_id not in copies:
            order.append(market.match_id)
            copies[market.match_id] = []
        copies[market.match_id].append(market)
    groups: list[MatchGroup] = []
    for match_id in order:
        group = copies[match_id]
        tokens = frozenset().union(*(market.tokens for market in group))
        dirs = tuple(market.archive_dir for market in group)
        groups.append(MatchGroup(match_id, group[0].condition_id, dirs, tokens))
    return groups


def other_token_fn(markets: Sequence[ArchivedMarket]) -> Callable[[str], str | None]:
    """Pair YES/NO tokens across the loaded markets."""
    pairs: dict[str, str] = {}
    for market in markets:
        pairs[market.yes_token_id] = market.no_token_id
        pairs[market.no_token_id] = market.yes_token_id

    def other_token(token_id: str) -> str | None:
        return pairs.get(token_id)

    return other_token


def market_by_token(markets: Sequence[ArchivedMarket]) -> dict[str, ArchivedMarket]:
    """YES/NO token → market. First wins so trader_live beats live_paper."""
    by_token: dict[str, ArchivedMarket] = {}
    for market in markets:
        by_token.setdefault(market.yes_token_id, market)
        by_token.setdefault(market.no_token_id, market)
    return by_token


def _signed_cash(side: str, price: float, size: float) -> float:
    """Signed cash of one fill: a SELL credits, a BUY debits."""
    signed = 1.0 if side.upper() == "SELL" else -1.0
    return price * size * signed


def _row_cash(row: dict[str, object]) -> float:
    """Signed cash of one JSON fill row, treating unparseable numbers as zero."""
    price = clob_number(row.get("price"))
    size = clob_number(row.get("size"))
    if price is None or size is None:
        return 0.0
    return _signed_cash(str(row.get("side", "")), price, size)


def _fill_identity(row: dict[str, object]) -> tuple[object, ...]:
    """Dedup key for one journal fill: ledger key, else the fill body."""
    key = row.get("fill_key")
    if type(key) is str and key:
        return ("key", key)
    return (
        "body",
        row.get("token_id"),
        row.get("side"),
        row.get("price"),
        row.get("size"),
        row.get("ts_utc"),
    )


def iter_journal_fills(archive_dir: Path) -> Iterable[dict[str, object]]:
    """Every fill and late_fill record in one session.jsonl, in file order."""
    path = archive_dir / SESSION_JOURNAL_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if type(row) is not dict:
            continue
        if row.get("kind") not in {"fill", "late_fill"}:
            continue
        yield row


def journal_cash(archive_dirs: Sequence[Path], tokens: frozenset[str]) -> float:
    """Sum fill cash across copies of one match, once per fill identity."""
    total = 0.0
    seen: set[tuple[object, ...]] = set()
    for archive_dir in archive_dirs:
        for row in iter_journal_fills(archive_dir):
            if row.get("venue") not in (None, "polymarket"):
                continue
            if row.get("token_id") not in tokens:
                continue
            ident = _fill_identity(row)
            if ident in seen:
                continue
            seen.add(ident)
            total += _row_cash(row)
    return total


def journal_fill_keys(archive_dir: Path) -> set[str]:
    """fill_key values already in this session.jsonl (fill or late_fill)."""
    found: set[str] = set()
    for row in iter_journal_fills(archive_dir):
        key = row.get("fill_key")
        if type(key) is str and key:
            found.add(key)
    return found


def sqlite_cash(conn: sqlite3.Connection, tokens: frozenset[str]) -> float:
    """SUM MATCHED+CONFIRMED cash_delta for these tokens."""
    if not tokens:
        return 0.0
    placeholders = ",".join("?" * len(tokens))
    row = conn.execute(
        "SELECT COALESCE(SUM(cash_delta), 0) FROM fill_ledger"
        f" WHERE status IN ('MATCHED', 'CONFIRMED') AND token_id IN ({placeholders})",
        tuple(tokens),
    ).fetchone()
    return float(row[0])


class ChainCash:
    """Ground-truth cash per match, from whichever exchange feed we could reach."""

    def for_group(self, group: MatchGroup) -> float:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ClobChainCash(ChainCash):
    """Maker and taker fills from the authenticated CLOB feed, keyed by token."""

    by_token: dict[str, float]

    def for_group(self, group: MatchGroup) -> float:
        return sum(self.by_token.get(token, 0.0) for token in group.tokens)


@dataclass(frozen=True, slots=True)
class DataApiChainCash(ChainCash):
    """The public activity feed, keyed by condition id."""

    by_cid: dict[str, float]

    def for_group(self, group: MatchGroup) -> float:
        return self.by_cid.get(group.condition_id, 0.0)


def fetch_clob_trades(funder: str, pk: str) -> list[object]:
    """All maker trades for the Safe, L2-auth CLOB /data/trades."""
    client = ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=pk,
        signature_type=SIGNATURE_TYPE,
        funder=funder,
    )
    creds = client.create_or_derive_api_key()
    client.set_api_creds(creds)
    return list(client.get_trades(TradeParams(maker_address=funder)))


def fetch_data_api_trades(funder: str) -> list[dict[str, object]]:
    """Public trades for the funder, including maker fills."""
    out: list[dict[str, object]] = []
    offset = 0
    limit = 1000
    with httpx.Client(timeout=60.0) as client:
        while True:
            response = client.get(
                DATA_API,
                params={
                    "user": funder,
                    "takerOnly": "false",
                    "limit": limit,
                    "offset": offset,
                },
            )
            response.raise_for_status()
            page = response.json()
            if type(page) is not list or not page:
                break
            for item in page:
                if type(item) is dict:
                    out.append(item)
            if len(page) < limit:
                break
            offset += len(page)
    return out


def clob_chain_cash(
    trades: Sequence[object],
    funder: str,
    other_token: Callable[[str], str | None],
) -> ClobChainCash:
    """token_id → signed cash from our settled maker and taker CLOB fills."""
    by_token: dict[str, float] = {}
    for row in trades:
        for item in rest_fills(row, funder, other_token):
            ev = item.event
            signed = 1.0 if ev.our_side is Side.SELL else -1.0
            by_token[ev.token_id] = by_token.get(ev.token_id, 0.0) + ev.price * ev.size * signed
    return ClobChainCash(by_token)


def data_api_chain_cash(trades: Sequence[dict[str, object]]) -> DataApiChainCash:
    """condition_id → signed cash from the public activity feed."""
    by_cid: dict[str, float] = {}
    for row in trades:
        cid = row.get("conditionId")
        if type(cid) is not str:
            continue
        by_cid[cid] = by_cid.get(cid, 0.0) + _row_cash(row)
    return DataApiChainCash(by_cid)


def build_gap_rows(
    groups: Sequence[MatchGroup],
    chain: ChainCash,
    conn: sqlite3.Connection | None,
) -> list[GapRow]:
    """One row per match: exchange cash, journal cash, ledger cash."""
    return [
        GapRow(
            group.match_id,
            chain.for_group(group),
            journal_cash(group.archive_dirs, group.tokens),
            sqlite_cash(conn, group.tokens) if conn is not None else 0.0,
        )
        for group in groups
    ]


def print_report(rows: Sequence[GapRow]) -> None:
    mismatched = [
        row
        for row in rows
        if abs(row.chain - row.journal) > GAP_CENTS or abs(row.chain - row.sqlite) > GAP_CENTS
    ]
    print(f"{'match':<12} {'chain':>10} {'journal':>10} {'sqlite':>10} {'j_err':>10} {'s_err':>10}")
    journal_under = 0.0
    sqlite_under = 0.0
    for row in sorted(mismatched, key=lambda item: abs(item.chain - item.journal), reverse=True):
        j_err = row.chain - row.journal
        s_err = row.chain - row.sqlite
        journal_under += j_err
        sqlite_under += s_err
        print(
            f"{row.match_id:<12} {row.chain:10.2f} {row.journal:10.2f} {row.sqlite:10.2f} "
            f"{j_err:10.2f} {s_err:10.2f}"
        )
    print(f"{len(mismatched)} markets")
    print(f"journal understates ${journal_under:.2f}")
    print(f"sqlite understates ${sqlite_under:.2f}")


def _trade_sort_ts(row: object) -> float:
    """Exchange time for ordering a CLOB trade, or 0 when missing."""
    if type(row) is not dict:
        return 0.0
    raw = row.get("timestamp")
    if raw in (None, ""):
        raw = row.get("match_time")
    return clob_number(raw) or 0.0


def ack_fill_outbox(store: WalletStateStore, key: str) -> None:
    """Ack CONFIRMED and MATCHED outbox rows for a fill already in the journal."""
    for event in ("confirmed", "matched"):
        seq = store.unacked_seq(key, event)
        if seq is not None:
            store.ack_outbox(seq)


def apply_backfill(
    store: WalletStateStore,
    trades: Sequence[object],
    funder: str,
    markets: Sequence[ArchivedMarket],
) -> None:
    """Book missing fills into live.db, then append the ones the journals still lack."""
    other_token = other_token_fn(markets)
    by_token = market_by_token(markets)
    known = set(by_token)
    scoped = [
        row
        for row in trades
        if any(item.event.token_id in known for item in rest_fills(row, funder, other_token))
    ]
    scoped.sort(key=_trade_sort_ts)
    result = backfill_trades(store, scoped, funder, other_token)
    print(f"applied={result.applied} confirmed={result.confirmed} cash={result.cash:.2f}")
    seen: set[str] = set()
    for row in scoped:
        for item in rest_fills(row, funder, other_token):
            ev = item.event
            if ev.token_id not in known or ev.trade_id in seen:
                continue
            seen.add(ev.trade_id)
            market = by_token[ev.token_id]
            if ev.trade_id in journal_fill_keys(market.archive_dir):
                ack_fill_outbox(store, ev.trade_id)
                continue
            fill = store.fill_for_key(ev.trade_id)
            if fill is None:
                continue
            append_late_fill(
                market.archive_dir,
                fill,
                store.position(fill.token_id).size,
                store.ledger_net_cash_for_tokens(set(market.tokens)),
                fill_ts_utc(fill.ts),
                fill.trade_id,
                "rest_backfill",
            )
            ack_fill_outbox(store, ev.trade_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write recovered fills")
    parser.add_argument("--report", action="store_true", help="print the gap table (default)")
    args = parser.parse_args()
    apply = bool(args.apply)
    load_dotenv(BASE_DIR / ".env")
    funder = os.environ.get("BROWSER_ADDRESS", "")
    pk = os.environ.get("PK", "")
    if not funder:
        raise SystemExit("BROWSER_ADDRESS is missing")
    if apply and not pk:
        raise SystemExit("--apply needs PK for CLOB /data/trades")
    markets = iter_live_markets((LIVE_ARCHIVE, PAPER_ARCHIVE))
    clob_trades: list[object] = []
    if pk:
        use_http1_clob_transport()
        clob_trades = fetch_clob_trades(funder, pk)
        chain: ChainCash = clob_chain_cash(clob_trades, funder, other_token_fn(markets))
    else:
        chain = data_api_chain_cash(fetch_data_api_trades(funder))
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True) if LIVE_DB.is_file() else None
    try:
        print_report(build_gap_rows(group_by_match(markets), chain, conn))
    finally:
        if conn is not None:
            conn.close()
    if not apply:
        return
    if live_container_running():
        raise SystemExit(f"--apply refused: {LIVE_CONTAINER} is running")
    store = WalletStateStore(LIVE_DB)
    try:
        apply_backfill(store, clob_trades, funder, markets)
    finally:
        store.close()


if __name__ == "__main__":
    main()
