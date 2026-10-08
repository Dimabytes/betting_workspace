import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polymaker.marketdata.orderbook import BookLevel

from dashboard.balance import BalanceAccount, BalanceRead
from dashboard.live_hub import (
    BooksCallback,
    HubConfig,
    HubTiming,
    LiveHub,
    ScheduleFn,
)
from dashboard.market_books import BookSnapshot, DesiredMarket
from strategy.lifecycle import empty_state
from strategy.types import (
    Budget,
    FreshnessLimits,
    GameClock,
    MarketLimits,
    Permissions,
    RestingOrder,
    TokenInventory,
)
from trader.core_persistence import CoreSessionKey, snapshot_checkpoint, upsert_session
from trader.wallet_store import WalletStateStore

YES = "yes-token-1"
NO = "no-token-1"
YES2 = "yes-token-2"
NO2 = "no-token-2"
CID = "0xcond1"
CID2 = "0xcond2"
CID3 = "0xcond3"
SID = "sess-1"
FUNDER = "0xfixture"
NOW = time.time()

FIXTURE_TIMING = HubTiming(
    disk_s=0.05,
    catalog_s=0.1,
    subs_s=0.05,
    day_s=0.2,
    logs_s=0.2,
    drain_timeout_s=2.0,
    balance_min_s=0.05,
    balance_period_s=0.2,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class FakeBooks:
    def __init__(self, schedule: ScheduleFn, on_books: BooksCallback) -> None:
        self._schedule = schedule
        self._on_books = on_books
        self._stop = asyncio.Event()
        self.calls: list[tuple[DesiredMarket, ...]] = []
        self.stopped = False

    @property
    def applied_at(self) -> float | None:
        return time.time() if self.calls else None

    async def run(self) -> None:
        await self._stop.wait()

    async def apply_subscriptions(self, markets: tuple[DesiredMarket, ...]) -> bool:
        self.calls.append(markets)
        return True

    def emit(self, books: tuple[BookSnapshot, ...]) -> None:
        self._schedule(lambda: self._on_books(books))

    async def shutdown(self) -> None:
        self.stopped = True
        self._stop.set()


@dataclass
class FixtureState:
    activity: object
    positions: object
    balance: BalanceRead
    logs: bytes
    container_id: str | None
    container_state: str
    container_started: str
    fetch_error: str | None
    fetch_sleep_s: float = 0.0


@dataclass(frozen=True)
class DashboardFixture:
    config: HubConfig
    state: FixtureState
    wallet_db: Path
    live_root: Path
    legacy_root: Path
    sidecar_root: Path
    compose_file: Path
    counters: dict[str, int]
    books: list[FakeBooks]

    def start_hub(self) -> LiveHub:
        return LiveHub(self.config).start()


def _signal(
    second: float,
    *,
    reason: str | None = "min_delta",
    phase: str = "playing",
    entry_block: str | None = None,
    model_evaluated: bool = True,
    raw_delta: float | None = 0.01,
    recorded_at_utc: str | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "kind": "signal",
        "second": second,
        "reason": reason,
        "model_evaluated": model_evaluated,
        "raw_delta": raw_delta,
        "feed_source": "grid",
        "recorded_at_utc": recorded_at_utc or utc_now_iso(),
        "feed_received_at_utc": recorded_at_utc or utc_now_iso(),
        "game_snapshot": {"second": second, "phase": phase, "paused": False},
    }
    if entry_block is not None:
        rec["entry_block"] = entry_block
    return rec


def write_match(
    archive: Path,
    match_id: str,
    *,
    cid: str,
    yes: str,
    no: str,
    teams: tuple[str, str] = ("Team Radiant", "Team Dire"),
    map_number: int = 1,
    event_slug: str | None = "fixture-event",
    final: dict[str, Any] | None = None,
    record_only: bool = False,
) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "match_id": match_id,
        "game": "dota",
        "joined_at_utc": utc_now_iso(),
        "teams": {"radiant": teams[0], "dire": teams[1]},
        "map_number": map_number,
        "market": {
            "condition_id": cid,
            "market_slug": f"dota-map-{match_id}",
            "event_slug": event_slug,
            "yes_token_id": yes,
            "no_token_id": no,
            "yes_is_radiant": True,
            "outcome_0_name": teams[0],
            "outcome_1_name": teams[1],
        },
    }
    if final is not None:
        meta["final"] = final
    if record_only:
        meta["record_only"] = True
    (archive / "match.json").write_text(json.dumps(meta))
    return archive


def write_journal(archive: Path, records: list[dict[str, Any]]) -> None:
    (archive / "session.jsonl").write_text("".join(json.dumps(rec) + "\n" for rec in records))


def _write_trace(archive: Path, match_id: str, *, yes: str, no: str) -> None:
    header = {
        "schema_version": 1,
        "match_id": match_id,
        "yes_token": yes,
        "no_token": no,
        "policy": {"min_abs_delta": 0.05, "buy_cutoff_second": 900},
        "limits": {"min_order_size": 5.0},
        "freshness": {"entry_stale_s": 5.0, "exit_stale_s": 15.0},
    }
    (archive / "core_trace.jsonl").write_text(json.dumps(header) + "\n")


def _write_sidecar(root: Path, cid: str, *, slug: str, closed: bool) -> None:
    markets = root / "metadata" / "markets"
    markets.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "schemaVersion": 1,
        "eventId": "ev-1",
        "eventSlug": "fixture-sidecar-event",
        "conditionId": cid,
        "marketSlug": slug,
        "question": "fixture question",
        "marketKind": "map_winner",
        "mapNumber": 2,
        "outcomes": [
            {"index": 0, "name": "SideA", "tokenId": f"tok-{cid}-a"},
            {"index": 1, "name": "SideB", "tokenId": f"tok-{cid}-b"},
        ],
        "active": not closed,
        "closed": closed,
        "acceptingOrders": not closed,
        "enableOrderBook": not closed,
        "tickSize": "0.01",
        "minOrderSize": "5",
        "negRisk": False,
        "gridSeriesId": None,
        "eventTitle": None,
    }
    (markets / f"{cid}.json").write_text(json.dumps(sidecar))


def build_wallet_db(
    path: Path, *, conn_hook: Callable[[sqlite3.Connection], None] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    WalletStateStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO wallet_identity(k, v) VALUES('funder', ?)", (FUNDER,))
    conn.execute("INSERT INTO token_cid(token_id, condition_id) VALUES(?,?)", (YES, CID))
    conn.execute("INSERT INTO token_cid(token_id, condition_id) VALUES(?,?)", (NO, CID))
    conn.execute(
        "INSERT INTO positions(token_id, size, avg_price, updated_ts) VALUES(?,?,?,?)",
        (YES, 4.0, 0.5, NOW),
    )
    conn.execute(
        "INSERT INTO fill_ledger(fill_key, clob_trade_id, maker_order_id, token_id,"
        " side, price, size, ts, status, pre_size, pre_avg, cash_delta, is_maker)"
        " VALUES('k1','t-k1','v-1',?, 'BUY', 0.5, 4.0, ?, 'MATCHED', 0,0,-2.0,1)",
        (YES, NOW),
    )
    conn.execute("INSERT INTO fill_outbox(fill_key, event) VALUES('k1','matched')")
    if conn_hook is not None:
        conn_hook(conn)
    conn.commit()
    conn.close()


def _fixture_fetch(state: FixtureState, counters: dict[str, int]) -> Callable[[str, float], object]:
    def fetch(url: str, timeout: float) -> object:
        counters["fetch"] += 1
        if state.fetch_sleep_s > 0:
            time.sleep(state.fetch_sleep_s)
        if state.fetch_error is not None:
            raise OSError(state.fetch_error)
        if "/activity" in url:
            return state.activity
        if "/positions" in url:
            return state.positions
        raise OSError(f"unexpected url {url}")

    return fetch


def _fixture_runner(
    state: FixtureState, counters: dict[str, int]
) -> Callable[[Sequence[str], float], bytes]:
    def runner(argv: Sequence[str], timeout_s: float) -> bytes:
        counters["log_runner"] += 1
        joined = " ".join(str(a) for a in argv)
        if argv[0] != "docker":
            raise OSError("only docker commands allowed")
        if "ps" in argv:
            return (state.container_id or "").encode()
        if "inspect" in argv:
            return f"{state.container_state}|{state.container_started}|/fixture".encode()
        if "logs" in argv:
            return state.logs
        raise OSError(f"unexpected docker call: {joined}")

    return runner


def _fixture_balance(state: FixtureState, counters: dict[str, int]) -> Callable[[], BalanceRead]:
    def read() -> BalanceRead:
        counters["balance"] += 1
        return state.balance

    return read


def build(
    root: Path,
    *,
    conn_hook: Callable[[sqlite3.Connection], None] | None = None,
    timing: HubTiming = FIXTURE_TIMING,
) -> DashboardFixture:
    live_root = root / "trader_live"
    legacy_root = root / "live_paper"
    sidecar_root = root / "sidecars" / "dota"
    wallet_db = live_root / "wallet" / "live.db"
    for path in (live_root, legacy_root, sidecar_root):
        path.mkdir(parents=True, exist_ok=True)
    compose_file = root / "compose.yaml"
    compose_file.write_text("services: {}\n")
    build_wallet_db(wallet_db, conn_hook=conn_hook)

    live = write_match(live_root / "m-live", "m-live", cid=CID, yes=YES, no=NO)
    _write_trace(live, "m-live", yes=YES, no=NO)
    write_journal(
        live,
        [
            {"kind": "session_start", "execution_mode": "live", "schema_version": 7},
            _signal(120.0, entry_block="min_delta"),
            _signal(300.0, reason="model"),
        ],
    )

    closed = write_match(
        live_root / "m-closed",
        "m-closed",
        cid=CID2,
        yes=YES2,
        no=NO2,
        map_number=2,
        final={"winner": "radiant"},
    )
    write_journal(
        closed,
        [
            {"kind": "session_start", "execution_mode": "live", "schema_version": 7},
            _signal(200.0, reason="finished", phase="finished", raw_delta=None),
            {
                "kind": "session_end",
                "net_cash": 12.0,
                "inventory_value": 3.0,
                "positions": {YES2: 6.0},
            },
        ],
    )

    record_only = write_match(
        live_root / "m-record",
        "m-record",
        cid="0xcondR",
        yes="yes-R",
        no="no-R",
        record_only=True,
    )
    (record_only / "note.txt").write_text("record only")

    _write_sidecar(sidecar_root, CID3, slug="fixture-skipped", closed=True)

    state = FixtureState(
        activity=[],
        positions=[
            {
                "asset": YES2,
                "conditionId": CID2,
                "title": "m-closed",
                "size": 6.0,
                "curPrice": 0.9,
                "redeemable": True,
            }
        ],
        balance=BalanceRead(
            ok=True, collateral_usdc=100.0, error=None, status_code=None, retry_after_s=None
        ),
        logs=b"fixture log line\n",
        container_id="abc123",
        container_state="running",
        container_started=datetime.now(UTC).isoformat(),
        fetch_error=None,
        fetch_sleep_s=0.0,
    )
    counters: dict[str, int] = {"fetch": 0, "log_runner": 0, "balance": 0}
    books: list[FakeBooks] = []

    def factory(schedule: ScheduleFn, on_books: BooksCallback) -> FakeBooks:
        fake = FakeBooks(schedule, on_books)
        books.append(fake)
        return fake

    config = HubConfig(
        wallet_db=wallet_db,
        live_root=live_root,
        legacy_root=legacy_root,
        compose_file=compose_file,
        service="live",
        ws_url="ws://unused",
        proxy=None,
        account=None,
        account_error="no credentials",
        sidecar_roots={"dota": sidecar_root},
        sidecar_root_errors=(),
        timing=timing,
        fetch=_fixture_fetch(state, counters),
        log_runner=_fixture_runner(state, counters),
        read_balance=None,
        make_books=factory,
        wall=time.time,
        mono=time.monotonic,
    )
    return DashboardFixture(
        config=config,
        state=state,
        wallet_db=wallet_db,
        live_root=live_root,
        legacy_root=legacy_root,
        sidecar_root=sidecar_root,
        compose_file=compose_file,
        counters=counters,
        books=books,
    )


def wait_for(pred: Callable[[], bool], timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("fixture condition not met")


def make_book(
    token_id: str, *, bid: float = 0.55, ask: float = 0.57, cid: str = CID
) -> BookSnapshot:
    return BookSnapshot(
        token_id=token_id,
        condition_id=cid,
        bids=(BookLevel(price=bid, size=10.0),),
        asks=(BookLevel(price=ask, size=10.0),),
        tick_size=0.01,
        book_hash=None,
        exchange_ts=None,
        local_ts=time.time(),
        generation=1,
        initialized=True,
        ready=True,
        connected=True,
        disconnected_since=None,
        truncated=False,
    )


def emit_books(fixture: DashboardFixture, books: tuple[BookSnapshot, ...]) -> None:
    for fake in fixture.books:
        fake.emit(books)


def balance_reader(fixture: DashboardFixture) -> Callable[[], BalanceRead]:
    return _fixture_balance(fixture.state, fixture.counters)


def enable_balance(fixture: DashboardFixture) -> DashboardFixture:
    return DashboardFixture(
        config=replace(
            fixture.config,
            account=BalanceAccount(
                host="https://clob.example",
                chain_id=137,
                signature_type=2,
                pk="k",
                funder=FUNDER,
            ),
            account_error=None,
            read_balance=_fixture_balance(fixture.state, fixture.counters),
        ),
        state=fixture.state,
        wallet_db=fixture.wallet_db,
        live_root=fixture.live_root,
        legacy_root=fixture.legacy_root,
        sidecar_root=fixture.sidecar_root,
        compose_file=fixture.compose_file,
        counters=fixture.counters,
        books=fixture.books,
    )


def insert_session(
    fixture: DashboardFixture,
    *,
    session_id: str = SID,
    condition_id: str = CID,
    yes: str = YES,
    no: str = NO,
    orders: tuple[RestingOrder, ...] = (),
    qty: float = 0.0,
) -> None:
    state = empty_state(
        limits=MarketLimits(
            min_order_size=5.0,
            tick_size=0.01,
            pair_sum_tolerance=0.05,
            radiant_token_index=0,
        ),
        freshness=FreshnessLimits(book_stale_s=5.0, entry_stale_s=16.0, exit_stale_s=45.0),
        permissions=Permissions(
            halt=False,
            reduce_only=False,
            allow_buy=True,
            allow_sell=True,
            sell_unconfirmed=False,
        ),
        budget=Budget(
            cash_usdc=1000.0,
            cap_room_usdc=float("inf"),
            account_cap_room_usdc=float("inf"),
        ),
        clock=GameClock(now_ns=0, game_second=10, paused=False, game_ended=False),
    )
    state = replace(
        state,
        episode_id=1,
        episode_counter=1,
        episode_token_index=0,
        orders=orders,
        inventory=(
            TokenInventory(token_index=0, qty=qty, cost_basis=0.5 * qty, last_buy_ns=None),
            TokenInventory(token_index=1, qty=0.0, cost_basis=0.0, last_buy_ns=None),
        ),
        next_order_seq=1,
    )
    checkpoint = snapshot_checkpoint(state=state, now_ns=0, now_wall_s=NOW, sell_min_life_s=1.0)
    conn = sqlite3.connect(fixture.wallet_db)
    upsert_session(
        conn,
        session_id=session_id,
        key=CoreSessionKey(
            condition_id=condition_id,
            game="dota",
            yes_token=yes,
            no_token=no,
            yes_is_radiant=True,
        ),
        revision=1,
        recovery=False,
        recovery_generation=0,
        checkpoint=checkpoint,
        last_outbox_seq=0,
    )
    conn.commit()
    conn.close()
