"""Durable fill ledger that replaces the fork StateStore and UserEventProcessor."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from polymaker.domain import Fill, Position, Side, TradeState
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor

from shared.constants.strategy import EXIT_SETTLE_SECONDS
from shared.utils.log import get_logger
from shared.utils.trading import HALF_SHARE_TICK, share_floor
from trader.core_persistence import migrate_core_schema, outbox_after
from trader.fill_parsing import cash_delta, split_fill_key, trade_time_or_receipt
from trader.notify import notify_in_background

logger = get_logger(__name__)

BUSY_TIMEOUT_MS = 30_000


def share_qty_matches(left: float, right: float) -> bool:
    return abs(share_floor(left) - share_floor(right)) <= HALF_SHARE_TICK


def rest_size_within_tolerance(internal: float, rest: float) -> bool:
    """True when REST size is close enough to sqlite that it is not drift."""
    return abs(internal - rest) <= max(1.0, 0.02 * rest)


def rest_size_down_skip_reason(store: "WalletStateStore", token_id: str, now: float) -> str | None:
    if store.merge_is_held(token_id):
        return "merge"
    if store.inflight(token_id) > 0:
        return "inflight"
    if store.is_settling(token_id, now):
        return "settle"
    for order in store.orders_for(token_id):
        if order.side is Side.SELL:
            return "live_sell"
    return None


_LEDGER_MATCHED = TradeState.MATCHED.value
_LEDGER_CONFIRMED = TradeState.CONFIRMED.value
_LEDGER_FAILED = TradeState.FAILED.value
_LEDGER_SUPERSEDED = "SUPERSEDED"
_LEDGER_MERGED = "MERGED"
_MERGE_SIDE = "MERGE"
_MERGE_LEG_PRICE = 0.5

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS fill_ledger (
    fill_key TEXT PRIMARY KEY,
    clob_trade_id TEXT NOT NULL,
    maker_order_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    ts REAL NOT NULL,
    status TEXT NOT NULL,
    pre_size REAL NOT NULL,
    pre_avg REAL NOT NULL,
    cash_delta REAL NOT NULL,
    is_maker INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fill_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_key TEXT NOT NULL,
    event TEXT NOT NULL,
    acked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wallet_day (
    utc_day TEXT PRIMARY KEY,
    day_start_equity REAL NOT NULL,
    seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wallet_identity (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_cid (
    token_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class OutboxItem:
    """One unacked ledger event waiting for journal and risk side effects."""

    seq: int
    fill_key: str
    event: str


@dataclass(frozen=True, slots=True)
class DayRow:
    """Persisted UTC-day equity baseline used by the daily kill switch."""

    utc_day: str
    day_start_equity: float
    seq: int


@dataclass(frozen=True, slots=True)
class WalletIdentity:
    """Pinned funder / signature_type / chain for this wallet database."""

    funder: str
    signature_type: int
    chain_id: int


@dataclass(frozen=True, slots=True)
class ConfirmedApply:
    """Result of applying a CONFIRMED event to the ledger."""

    applied_new: bool
    confirmed: bool


class OversizedSellError(Exception):
    """SELL size exceeds the held position; the ledger is left unchanged."""


class WalletStateStore(StateStore):
    """StateStore with a one-transaction fill ledger, outbox, and day row."""

    def __init__(self, db_path: str | Path = "state.db") -> None:
        path = Path(db_path)
        preexisting = path.is_file() and path.stat().st_size > 0
        super().__init__(db_path)
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if preexisting:
            self._refuse_legacy_merge()
        self._conn.executescript(_LEDGER_SCHEMA)
        self._migrate_ledger_columns()
        migrate_core_schema(self._conn)
        self._conn.commit()
        self._net_cash = self.ledger_net_cash()
        self._day = self.day_for_utc(_utc_day())
        self._equity_snapshot: Callable[[], float] = lambda: self._net_cash
        self._oversized_sell_handler: Callable[[str], None] = lambda _token_id: None
        self._fill_credited_handler: Callable[[Fill], None] = lambda _fill: None
        self._fill_failed_handler: Callable[[Fill], None] = lambda _fill: None
        self._sell_frozen_until: dict[str, float] = {}
        self._buy_blocked: set[str] = set()
        self._merge_held: set[str] = set()
        self._writedown_snapshot_ts: dict[str, float] = {}
        self._rest_positions_sent_at = 0.0
        self._boot_wall = time.time()
        self._settled_wall: dict[str, float] = {}
        self._rebuild_inflight_from_ledger()

    def drop_untracked_positions(self, tracked: set[str]) -> list[str]:
        """No-op: boot `_token_cid` is empty and must not wipe the wallet."""
        del tracked
        return []

    def is_settling(self, token_id: str, now: float) -> bool:
        """True while this token is inside EXIT_SETTLE_SECONDS after the last fill stamp."""
        return now - self._last_fill_ts.get(token_id, 0.0) < EXIT_SETTLE_SECONDS

    def freeze_sell(self, token_id: str, seconds: float) -> None:
        """Block SELL quotes on this token for this long, or until a fill clears it."""
        self._sell_frozen_until[token_id] = time.monotonic() + seconds

    def clear_sell_freeze(self, token_id: str) -> None:
        """Allow SELL quotes on this token again."""
        self._sell_frozen_until.pop(token_id, None)

    def is_sell_frozen(self, token_id: str) -> bool:
        """True while a CLOB inventory reject still freezes SELL on this token."""
        return time.monotonic() < self._sell_frozen_until.get(token_id, 0.0)

    def block_buy(self, token_id: str) -> None:
        """Stop new entry BUYs on this token: on-chain holds more than sqlite knows."""
        self._buy_blocked.add(token_id)

    def unblock_buy(self, token_id: str) -> None:
        """Allow entry BUYs on this token again."""
        self._buy_blocked.discard(token_id)

    def is_buy_blocked(self, token_id: str) -> bool:
        """True while an unexplained on-chain excess blocks new clips on this token."""
        return token_id in self._buy_blocked

    def merge_is_held(self, token_id: str) -> bool:
        return token_id in self._merge_held

    def hold_merge(self, *, token_ids: tuple[str, str]) -> None:
        self._merge_held.update(token_ids)

    def release_merge(self, *, token_ids: tuple[str, str]) -> None:
        self._merge_held.difference_update(token_ids)

    def note_rest_positions_sent(self, sent_at: float) -> None:
        """Record when the REST positions request left, before its reply can be applied."""
        self._rest_positions_sent_at = sent_at

    def note_writedown_snapshot(self, token_id: str, snapshot_ts: float) -> None:
        """Record that an exchange snapshot from `snapshot_ts` already contained every fill."""
        previous = self._writedown_snapshot_ts.get(token_id, 0.0)
        if snapshot_ts > previous:
            self._writedown_snapshot_ts[token_id] = snapshot_ts

    def fill_precedes_writedown(self, token_id: str, fill_ts: float) -> bool:
        """True when a write-down from a later snapshot already contained this fill."""
        return fill_ts < self._writedown_snapshot_ts.get(token_id, 0.0)

    def note_chain_floor(self, token_id: str, ts: float) -> None:
        """Ignore chain blocks older than this for the token.

        CONFIRMED, backfill, and FAILED stamp wall time. Accepting a chain block
        stamps that block's time, so an older read cannot undo it.
        """
        previous = self._settled_wall.get(token_id, 0.0)
        if ts > previous:
            self._settled_wall[token_id] = ts

    def chain_read_floor(self, token_id: str) -> float:
        """Earliest block time a chain read may use for this token."""
        return max(self._boot_wall, self._settled_wall.get(token_id, 0.0))

    def ledger_position(self, token_id: str) -> Position:
        rows = self._conn.execute(
            "SELECT l.status, l.side, l.price, l.size, l.ts FROM fill_ledger l "
            "JOIN (SELECT fill_key, MIN(seq) AS seq FROM fill_outbox GROUP BY fill_key) o "
            "ON o.fill_key = l.fill_key "
            "WHERE l.token_id=? AND l.status IN (?, ?, ?) ORDER BY o.seq",
            (token_id, _LEDGER_MATCHED, _LEDGER_CONFIRMED, _LEDGER_MERGED),
        ).fetchall()
        pos = Position(token_id, 0.0, 0.0)
        for row in rows:
            if row["status"] == _LEDGER_MERGED:
                pos = _subtract_merged_shares(pos, row["size"])
            else:
                replay = Fill(
                    token_id,
                    Side(row["side"]),
                    row["price"],
                    row["size"],
                    "",
                    row["ts"],
                    is_maker=True,
                )
                pos = _position_after_fill(pos, replay)
        return pos

    def restore_ledger_position(self, token_id: str, block_ts: float) -> None:
        """Write the ledger size and ignore any chain block older than this one."""
        self.note_chain_floor(token_id, block_ts)
        pos = self.ledger_position(token_id)
        self.set_position(token_id, pos.size, pos.avg_price)
        self._writedown_snapshot_ts.pop(token_id, None)
        self.clear_sell_freeze(token_id)
        self.unblock_buy(token_id)
        logger.warning(
            "trader position restored from ledger token=%s size=%.2f",
            token_id[:12],
            pos.size,
        )

    def write_down_chain_position(
        self,
        token_id: str,
        size: float,
        avg_price: float,
        block_ts: float,
        writedown_ts: float,
    ) -> None:
        """Accept a lower chain size and ignore any chain block older than this one."""
        self.note_chain_floor(token_id, block_ts)
        self.note_writedown_snapshot(token_id, writedown_ts)
        self.force_set_position(token_id, size, avg_price, source="onchain")
        self.clear_sell_freeze(token_id)

    def reconcile_positions(self, api_positions: dict[str, tuple[float, float]]) -> None:
        """Apply REST rounding. A shrink larger than half a share tick waits for chain."""
        now = time.time()
        for token_id, (size, avg) in api_positions.items():
            if rest_size_down_skip_reason(self, token_id, now) is not None:
                continue
            previous_size = self.position(token_id).size
            if size < previous_size - HALF_SHARE_TICK:
                logger.warning(
                    "trader rest size-down ignored token=%s internal=%.2f rest=%.2f",
                    token_id[:12],
                    previous_size,
                    size,
                )
                continue
            if size > previous_size and not rest_size_within_tolerance(previous_size, size):
                logger.warning(
                    "trader rest size-up ignored token=%s internal=%.2f rest=%.2f",
                    token_id[:12],
                    previous_size,
                    size,
                )
                continue
            self._apply_rest_size(token_id, size, avg, previous_size)

    def _apply_rest_size(
        self, token_id: str, size: float, avg: float, previous_size: float
    ) -> None:
        """Write the REST size and, when it lowers sqlite, stamp the snapshot watermark."""
        self.set_position(token_id, size, avg)
        self.clear_sell_freeze(token_id)
        if size < previous_size:
            self.note_writedown_snapshot(token_id, self._rest_positions_sent_at)

    def zero_token_sizes(self, token_ids: set[str]) -> None:
        """Write size 0 / avg 0 for these tokens. No fill_ledger payout row."""
        with self._conn:
            for token_id in token_ids:
                pos = Position(token_id, 0.0, 0.0)
                self._write_position_row(pos)
                self.positions[token_id] = pos
                self.clear_sell_freeze(token_id)
                self.unblock_buy(token_id)

    def has_unacked_matched(self, token_ids: set[str]) -> bool:
        """True when an unacked MATCHED outbox row still points at one of these tokens."""
        if not token_ids:
            return False
        for item in self.pending_outbox():
            if item.event != "matched":
                continue
            if self.ledger_status(item.fill_key) != _LEDGER_MATCHED:
                continue
            fill = self.fill_for_key(item.fill_key)
            if fill is not None and fill.token_id in token_ids:
                return True
        return False

    def apply_fill(self, fill: Fill) -> bool:
        """One-shot CONFIRMED apply used by paper fills and REST-only confirms."""
        return self.apply_confirmed_fill(fill, fill.trade_id).applied_new

    def apply_matched_fill(self, fill: Fill, key: str) -> bool:
        """Record MATCHED: position, pending row, outbox, inflight, one commit."""
        if self.fill_precedes_writedown(fill.token_id, fill.ts):
            self._record_superseded(fill, key)
            return False
        clob_trade_id, maker_order_id = split_fill_key(key)
        pos = self.positions.get(fill.token_id, Position(fill.token_id))
        pre_size = pos.size
        pre_avg = pos.avg_price
        new_pos = self._position_or_reject(pos, fill)
        if new_pos is None:
            return False
        delta = cash_delta(fill)
        self.ensure_utc_day()
        with self._conn:
            if self.ledger_status(key) is not None:
                return False
            self._insert_ledger(
                key,
                clob_trade_id,
                maker_order_id,
                fill,
                _LEDGER_MATCHED,
                pre_size,
                pre_avg,
                delta,
            )
            self._write_position_row(new_pos)
            self._insert_outbox(key, "matched")
        self.positions[fill.token_id] = new_pos
        self._net_cash += delta
        self.mark_inflight(fill.token_id)
        self._stamp_settle(fill.token_id, fill.ts)
        self._fill_credited_handler(fill)
        return True

    def apply_confirmed_fill(self, fill: Fill, key: str) -> ConfirmedApply:
        """Confirm a pending MATCHED, or apply CONFIRMED with no prior MATCHED."""
        status = self.ledger_status(key)
        if status == _LEDGER_CONFIRMED:
            return ConfirmedApply(False, False)
        if status == _LEDGER_FAILED:
            return ConfirmedApply(False, False)
        if status == _LEDGER_SUPERSEDED:
            return ConfirmedApply(False, False)
        if status == _LEDGER_MATCHED:
            token_id = self._ledger_token_id(key)
            with self._conn:
                self._conn.execute(
                    "UPDATE fill_ledger SET status=? WHERE fill_key=?",
                    (_LEDGER_CONFIRMED, key),
                )
                self._insert_outbox(key, "confirmed")
            self.clear_inflight(token_id)
            self.clear_sell_freeze(token_id)
            self.note_chain_floor(token_id, time.time())
            return ConfirmedApply(False, True)
        if self.fill_precedes_writedown(fill.token_id, fill.ts):
            self._record_superseded(fill, key)
            return ConfirmedApply(False, False)
        pos = self.positions.get(fill.token_id, Position(fill.token_id))
        new_pos = self._position_or_reject(pos, fill)
        if new_pos is None:
            return ConfirmedApply(False, False)
        applied = self._commit_confirmed_row(fill, key, pos, new_pos)
        if applied:
            self._fill_credited_handler(fill)
        return ConfirmedApply(applied, applied)

    def apply_backfilled_fill(self, fill: Fill, key: str) -> bool:
        """Record a REST-recovered fill as CONFIRMED. Ignores the write-down watermark.

        A SELL larger than the held size is clamped to zero and still credits cash.
        Idempotent on fill_key: any existing ledger row is left untouched.
        """
        pos = self.positions.get(fill.token_id, Position(fill.token_id))
        new_pos = _position_after_backfill(pos, fill)
        applied = self._commit_confirmed_row(fill, key, pos, new_pos)
        if applied:
            self._fill_credited_handler(fill)
        return applied

    def _commit_confirmed_row(self, fill: Fill, key: str, pos: Position, new_pos: Position) -> bool:
        """Write one CONFIRMED ledger row, the position, and the outbox in one commit.

        False when the key already has a ledger row; nothing is changed then.
        """
        clob_trade_id, maker_order_id = split_fill_key(key)
        delta = cash_delta(fill)
        self.ensure_utc_day()
        with self._conn:
            if self.ledger_status(key) is not None:
                return False
            self._insert_ledger(
                key,
                clob_trade_id,
                maker_order_id,
                fill,
                _LEDGER_CONFIRMED,
                pos.size,
                pos.avg_price,
                delta,
            )
            self._write_position_row(new_pos)
            self._insert_outbox(key, "confirmed")
        self.positions[fill.token_id] = new_pos
        self._net_cash += delta
        self._stamp_settle(fill.token_id, fill.ts)
        self.note_chain_floor(fill.token_id, time.time())
        return True

    def apply_failed_fill(self, fill: Fill, key: str) -> bool:
        """Mark MATCHED FAILED and rebuild the token from remaining live fills.

        `fill` is the WS event; the position dict and inflight key use the ledger
        row's token_id. Remaining MATCHED+CONFIRMED rows replay in outbox seq order.
        """
        row = self._conn.execute(
            "SELECT status, token_id, cash_delta FROM fill_ledger WHERE fill_key=?",
            (key,),
        ).fetchone()
        if row is None or row["status"] != _LEDGER_MATCHED:
            return False
        token_id = str(row["token_id"])
        delta = float(row["cash_delta"])
        self.ensure_utc_day()
        with self._conn:
            self._conn.execute(
                "UPDATE fill_ledger SET status=?, cash_delta=0 WHERE fill_key=?",
                (_LEDGER_FAILED, key),
            )
            rebuilt = self.ledger_position(token_id)
            self._write_position_row(rebuilt)
            self._insert_outbox(key, "failed")
            self._conn.execute(
                "UPDATE fill_outbox SET acked=1 WHERE fill_key=? AND event IN (?, ?)",
                (key, "matched", "failed"),
            )
        self._net_cash -= delta
        self.positions[token_id] = rebuilt
        if rebuilt.size <= 0:
            self.positions.pop(token_id, None)
        self.clear_inflight(token_id)
        self.clear_sell_freeze(token_id)
        self._fill_failed_handler(fill)
        self.note_chain_floor(token_id, time.time())
        return True

    def apply_merge(self, *, tx_hash: str, token_ids: tuple[str, str], qty: float) -> bool:
        now = time.time()
        self.ensure_utc_day()
        merged: list[Position] = []
        with self._conn:
            if self.ledger_status(_format_merge_key(tx_hash, token_ids[0])) is not None:
                return False
            for token_id in token_ids:
                pos = self.position(token_id)
                new_pos = _subtract_merged_shares(pos, qty)
                self._insert_merge_leg(
                    key=_format_merge_key(tx_hash, token_id),
                    tx_hash=tx_hash,
                    pos=pos,
                    qty=qty,
                    ts=now,
                )
                self._write_position_row(new_pos)
                merged.append(new_pos)
        for new_pos in merged:
            self.positions[new_pos.token_id] = new_pos
            self.note_chain_floor(new_pos.token_id, now)
        self._net_cash += qty
        return True

    @property
    def running_net_cash(self) -> float:
        return self._net_cash

    def set_equity_snapshot(self, snapshot: Callable[[], float]) -> None:
        """Provide cash+inventory for a new UTC day opened before the first fill."""
        self._equity_snapshot = snapshot

    def set_oversized_sell_handler(self, handler: Callable[[str], None]) -> None:
        """Halt the market that produced a SELL larger than the held position."""
        self._oversized_sell_handler = handler

    def set_fill_credited_handler(self, handler: Callable[[Fill], None]) -> None:
        """Notify when the ledger newly credits a fill_key."""
        self._fill_credited_handler = handler

    def set_fill_failed_handler(self, handler: Callable[[Fill], None]) -> None:
        """Notify when a MATCHED fill is marked FAILED."""
        self._fill_failed_handler = handler

    def ledger_net_cash(self) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cash_delta), 0) FROM fill_ledger WHERE status IN (?, ?, ?)",
            (_LEDGER_MATCHED, _LEDGER_CONFIRMED, _LEDGER_MERGED),
        ).fetchone()
        return float(row[0])

    def ledger_net_cash_for_tokens(self, tokens: set[str]) -> float:
        if not tokens:
            return 0.0
        placeholders = ",".join("?" * len(tokens))
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cash_delta), 0) FROM fill_ledger"
            f" WHERE status IN (?, ?, ?) AND token_id IN ({placeholders})",
            (_LEDGER_MATCHED, _LEDGER_CONFIRMED, _LEDGER_MERGED, *tokens),
        ).fetchone()
        return float(row[0])

    def pending_outbox(self) -> list[OutboxItem]:
        """Unacked outbox rows in commit order, for restart replay."""
        rows = self._conn.execute(
            "SELECT seq, fill_key, event FROM fill_outbox WHERE acked=0 ORDER BY seq"
        ).fetchall()
        return [OutboxItem(int(row["seq"]), row["fill_key"], row["event"]) for row in rows]

    def core_outbox_after(self, *, after_seq: int, tokens: frozenset[str]) -> list[OutboxItem]:
        """Outbox rows after a core cursor, including already journal-acked events."""
        return [
            OutboxItem(seq, fill_key, event)
            for seq, fill_key, event in outbox_after(self._conn, after_seq=after_seq, tokens=tokens)
        ]

    def matched_keys_for_tokens(self, tokens: set[str]) -> tuple[str, ...]:
        """MATCHED fill keys still open on these tokens, regardless of journal ack."""
        if not tokens:
            return ()
        placeholders = ",".join("?" * len(tokens))
        rows = self._conn.execute(
            "SELECT fill_key FROM fill_ledger WHERE status=? AND token_id IN"
            f" ({placeholders}) ORDER BY fill_key",
            (_LEDGER_MATCHED, *tokens),
        ).fetchall()
        return tuple(str(row["fill_key"]) for row in rows)

    def ack_outbox(self, seq: int) -> None:
        """Mark one outbox row acked after journal and risk have recorded it."""
        with self._conn:
            self._conn.execute("UPDATE fill_outbox SET acked=1 WHERE seq=?", (seq,))

    def unacked_seq(self, fill_key: str, event: str) -> int | None:
        """Seq of the unacked outbox row for this key and event, if any."""
        row = self._conn.execute(
            "SELECT seq FROM fill_outbox WHERE fill_key=? AND event=? AND acked=0 "
            "ORDER BY seq LIMIT 1",
            (fill_key, event),
        ).fetchone()
        if row is None:
            return None
        return int(row["seq"])

    def fill_for_key(self, key: str) -> Fill | None:
        row = self._conn.execute(
            "SELECT token_id, side, price, size, ts, is_maker FROM fill_ledger"
            " WHERE fill_key=? AND status != ?",
            (key, _LEDGER_MERGED),
        ).fetchone()
        if row is None:
            return None
        return Fill(
            row["token_id"],
            Side(row["side"]),
            row["price"],
            row["size"],
            key,
            trade_time_or_receipt(row["ts"], time.time()),
            is_maker=bool(row["is_maker"]),
        )

    def day_for_utc(self, utc_day: str) -> DayRow | None:
        """The wallet_day row for this UTC date, or None if that day is not open."""
        row = self._conn.execute(
            "SELECT utc_day, day_start_equity, seq FROM wallet_day WHERE utc_day=?",
            (utc_day,),
        ).fetchone()
        if row is None:
            return None
        return DayRow(row["utc_day"], row["day_start_equity"], int(row["seq"]))

    def current_day(self) -> DayRow | None:
        """Today's UTC day row, or None if this UTC date is not open yet."""
        return self.day_for_utc(_utc_day())

    @property
    def cached_day(self) -> DayRow | None:
        """In-memory UTC day row restored at open and refreshed on day roll."""
        return self._day

    def ensure_utc_day(self) -> DayRow:
        """Open today's UTC day before the first economic change of that date."""
        utc = _utc_day()
        if self._day is not None and self._day.utc_day == utc:
            return self._day
        self._day = self.open_utc_day(utc, self._equity_snapshot())
        return self._day

    def open_utc_day(self, utc_day: str, equity: float) -> DayRow:
        """Return this UTC day's row, inserting it when the date has no row yet."""
        existing = self.day_for_utc(utc_day)
        if existing is not None:
            self._day = existing
            return existing
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM wallet_day"
        ).fetchone()
        seq = int(seq_row["n"])
        with self._conn:
            self._conn.execute(
                "INSERT INTO wallet_day(utc_day, day_start_equity, seq) VALUES(?,?,?) "
                "ON CONFLICT(utc_day) DO NOTHING",
                (utc_day, equity, seq),
            )
        opened = self.day_for_utc(utc_day)
        if opened is None:
            raise RuntimeError("wallet_day insert did not produce a row")
        self._day = opened
        return opened

    def pin_static(self, signature_type: int, chain_id: int) -> None:
        """Write signature/chain on first use; refuse a different pair. Does not touch a stored funder."""
        existing = self.read_identity()
        if existing is None:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO wallet_identity(k, v) VALUES(?, ?)",
                    ("funder", ""),
                )
                self._conn.execute(
                    "INSERT INTO wallet_identity(k, v) VALUES(?, ?)",
                    ("signature_type", str(signature_type)),
                )
                self._conn.execute(
                    "INSERT INTO wallet_identity(k, v) VALUES(?, ?)",
                    ("chain_id", str(chain_id)),
                )
            return
        if existing.signature_type != signature_type or existing.chain_id != chain_id:
            raise RuntimeError("wallet identity does not match this database")

    def pin_funder(self, funder: str) -> None:
        """Require nonempty funder. Upgrade empty stored funder; refuse a different nonempty one."""
        existing = self.read_identity()
        if existing is None:
            raise RuntimeError("wallet identity does not match this database")
        if existing.funder == funder:
            return
        if existing.funder == "":
            with self._conn:
                self._conn.execute(
                    "UPDATE wallet_identity SET v=? WHERE k=?",
                    (funder, "funder"),
                )
            return
        raise RuntimeError("wallet identity does not match this database")

    def read_identity(self) -> WalletIdentity | None:
        """Pinned identity, or None on a fresh ledger."""
        rows = {
            row["k"]: row["v"] for row in self._conn.execute("SELECT k, v FROM wallet_identity")
        }
        if not rows:
            return None
        funder = rows.get("funder")
        signature_raw = rows.get("signature_type")
        chain_raw = rows.get("chain_id")
        if funder is None or signature_raw is None or chain_raw is None:
            raise RuntimeError("wallet identity row is incomplete")
        return WalletIdentity(funder, int(signature_raw), int(chain_raw))

    def persist_token_cid(self, token_id: str, condition_id: str) -> None:
        """Remember token → condition so leftover tokens survive a restart."""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO token_cid(token_id, condition_id) VALUES(?, ?)",
                (token_id, condition_id),
            )

    def load_token_cid(self) -> dict[str, str]:
        """All persisted token → condition mappings."""
        return {
            row["token_id"]: row["condition_id"]
            for row in self._conn.execute("SELECT token_id, condition_id FROM token_cid")
        }

    def _refuse_legacy_merge(self) -> None:
        """Fail loud rather than import a per-match paper_state.db into the wallet file."""
        if self._table_exists("fill_ledger"):
            return
        count_row = self._conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()
        if int(count_row["n"]) > 0:
            raise RuntimeError("old paper_state.db cannot be merged into the wallet ledger")

    def _migrate_ledger_columns(self) -> None:
        """Add is_maker to an older fill_ledger. Every existing row is a maker fill.

        The live parser read maker_orders only until REST backfill landed, so
        defaulting the column to 1 restates the recorded past, it does not invent it.
        """
        columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(fill_ledger)").fetchall()
        }
        if "is_maker" in columns:
            return
        self._conn.execute("ALTER TABLE fill_ledger ADD COLUMN is_maker INTEGER NOT NULL DEFAULT 1")

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def ledger_status(self, key: str) -> str | None:
        """The recorded state of this fill key, or None when the ledger has no row."""
        row = self._conn.execute(
            "SELECT status FROM fill_ledger WHERE fill_key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        return str(row["status"])

    def _insert_ledger(
        self,
        key: str,
        clob_trade_id: str,
        maker_order_id: str,
        fill: Fill,
        status: str,
        pre_size: float,
        pre_avg: float,
        delta: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO fill_ledger("
            "fill_key, clob_trade_id, maker_order_id, token_id, side, price, size, ts,"
            " status, pre_size, pre_avg, cash_delta, is_maker)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                clob_trade_id,
                maker_order_id,
                fill.token_id,
                fill.side.value,
                fill.price,
                fill.size,
                fill.ts,
                status,
                pre_size,
                pre_avg,
                delta,
                int(fill.is_maker),
            ),
        )

    def _write_position_row(self, pos: Position) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions(token_id, size, avg_price, updated_ts)"
            " VALUES(?,?,?,?)",
            (pos.token_id, pos.size, pos.avg_price, time.time()),
        )

    def _record_superseded(self, fill: Fill, key: str) -> None:
        """Log the fill an earlier write-down already contained. No position, cash, or outbox."""
        clob_trade_id, maker_order_id = split_fill_key(key)
        pos = self.positions.get(fill.token_id, Position(fill.token_id))
        with self._conn:
            if self.ledger_status(key) is not None:
                return
            self._insert_ledger(
                key,
                clob_trade_id,
                maker_order_id,
                fill,
                _LEDGER_SUPERSEDED,
                pos.size,
                pos.avg_price,
                0.0,
            )
        logger.error(
            "trader fill superseded by write-down token=%s side=%s size=%.2f ts=%.0f",
            fill.token_id[:12],
            fill.side.value,
            fill.size,
            fill.ts,
        )
        notify_in_background("trader fill superseded by write-down")

    def _insert_outbox(self, key: str, event: str) -> None:
        self._conn.execute(
            "INSERT INTO fill_outbox(fill_key, event, acked) VALUES(?,?,0)",
            (key, event),
        )

    def _insert_merge_leg(
        self, *, key: str, tx_hash: str, pos: Position, qty: float, ts: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO fill_ledger("
            "fill_key, clob_trade_id, maker_order_id, token_id, side, price, size, ts,"
            " status, pre_size, pre_avg, cash_delta, is_maker)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                tx_hash,
                "",
                pos.token_id,
                _MERGE_SIDE,
                _MERGE_LEG_PRICE,
                qty,
                ts,
                _LEDGER_MERGED,
                pos.size,
                pos.avg_price,
                _MERGE_LEG_PRICE * qty,
                0,
            ),
        )
        self._conn.execute(
            "INSERT INTO fill_outbox(fill_key, event, acked) VALUES(?,?,1)",
            (key, "merged"),
        )

    def _stamp_settle(self, token_id: str, ts: float) -> None:
        """Record the fill time used by the 10s divergence skip and clear a SELL freeze."""
        self._last_fill_ts[token_id] = ts
        self.clear_sell_freeze(token_id)

    def _rebuild_inflight_from_ledger(self) -> None:
        """Rebuild MATCHED inflight counts and the last fill timestamp per token."""
        for row in self._conn.execute("SELECT token_id, status, ts FROM fill_ledger"):
            token_id = row["token_id"]
            status = row["status"]
            if status == _LEDGER_MATCHED:
                self.mark_inflight(token_id)
            if status not in {_LEDGER_MATCHED, _LEDGER_CONFIRMED}:
                continue
            ts = row["ts"]
            if ts > self._last_fill_ts.get(token_id, 0.0):
                self._last_fill_ts[token_id] = ts

    def _ledger_token_id(self, key: str) -> str:
        """Token id persisted on this ledger row. Raises if the row is missing."""
        row = self._conn.execute(
            "SELECT token_id FROM fill_ledger WHERE fill_key=?", (key,)
        ).fetchone()
        if row is None:
            raise RuntimeError("fill_ledger row missing for confirmed token lookup")
        return str(row["token_id"])

    def _position_or_reject(self, pos: Position, fill: Fill) -> Position | None:
        """Apply one fill, or halt/alert and leave the ledger unchanged."""
        try:
            return _position_after_fill(pos, fill)
        except OversizedSellError:
            logger.warning("trader oversized sell")
            notify_in_background("trader oversized sell")
            self._oversized_sell_handler(fill.token_id)
            return None


class WalletFillProcessor(UserEventProcessor):
    """MATCHED pending on disk; FAILED rebuilds remaining fills; CONFIRMED may stand alone."""

    def __init__(
        self,
        store: StateStore,
        on_change: Callable[[str], None] | None = None,
        on_fill: Callable[[Fill], None] | None = None,
    ) -> None:
        if not isinstance(store, WalletStateStore):
            raise TypeError("WalletFillProcessor requires WalletStateStore")
        super().__init__(store, on_change, on_fill)
        self._wallet = store

    def on_trade(self, ev: TradeEvent, condition_id: str) -> None:
        """Apply one normalized trade to the durable ledger."""
        fill = Fill(
            ev.token_id,
            ev.our_side,
            ev.price,
            ev.size,
            ev.trade_id,
            trade_time_or_receipt(ev.ts, time.time()),
            is_maker=True,
        )
        if ev.status is TradeState.MATCHED:
            if not self._wallet.apply_matched_fill(fill, ev.trade_id):
                return
            self._on_change(condition_id)
            return
        if ev.status is TradeState.MINED:
            return
        if ev.status is TradeState.RETRYING:
            logger.warning("trade retrying trade_id=%s token=%s", ev.trade_id, ev.token_id[:12])
            return
        if ev.status is TradeState.CONFIRMED:
            result = self._wallet.apply_confirmed_fill(fill, ev.trade_id)
            if result.confirmed:
                self._on_fill(fill)
                self._on_change(condition_id)
            return
        if ev.status is TradeState.FAILED:
            if not self._wallet.apply_failed_fill(fill, ev.trade_id):
                return
            self._on_change(condition_id)

    def on_order(self, ev: OrderEvent, condition_id: str) -> None:
        """Keep the fork's remaining-size order tracking."""
        super().on_order(ev, condition_id)


def _position_after_backfill(pos: Position, fill: Fill) -> Position:
    """Apply one recovered fill. A SELL above held zeros the token and does not raise."""
    if fill.side is Side.SELL and fill.size > pos.size + HALF_SHARE_TICK:
        return Position(fill.token_id, 0.0, 0.0)
    return _position_after_fill(pos, fill)


def _position_after_fill(pos: Position, fill: Fill) -> Position:
    """Apply one fill. A SELL above held raises; float residue is not oversized."""
    if fill.side is Side.SELL and fill.size > pos.size + HALF_SHARE_TICK:
        raise OversizedSellError
    signed = fill.size if fill.side is Side.BUY else -fill.size
    new_size = pos.size + signed
    avg_price = pos.avg_price
    if fill.side is Side.BUY:
        if pos.size <= 0:
            avg_price = fill.price
        else:
            avg_price = (pos.avg_price * pos.size + fill.price * fill.size) / (pos.size + fill.size)
    if new_size <= 0:
        avg_price = 0.0
        new_size = 0.0
    return Position(fill.token_id, new_size, avg_price)


def _subtract_merged_shares(pos: Position, qty: float) -> Position:
    new_size = pos.size - qty
    if new_size <= 0:
        return Position(pos.token_id, 0.0, 0.0)
    return Position(pos.token_id, new_size, pos.avg_price)


def _format_merge_key(tx_hash: str, token_id: str) -> str:
    return f"merge:{tx_hash}:{token_id}"


def _utc_day() -> str:
    """Calendar date in UTC used as the wallet_day primary key."""
    return datetime.now(UTC).strftime("%Y-%m-%d")
