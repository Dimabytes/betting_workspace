import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from trader.core_persistence import (
    CoreCommand,
    CoreSchemaError,
    OrderBinding,
    StructuralCheckpoint,
    UnsettledBuy,
    decode_checkpoint,
    list_bindings,
    list_open_buy_commands,
    open_unsettled_buys,
)


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    condition_id: str
    game: str
    yes_token: str
    no_token: str
    yes_is_radiant: bool
    schema_version: int
    policy_version: str
    revision: int
    recovery: bool
    recovery_generation: int
    last_outbox_seq: int
    created_at: float
    updated_at: float
    checkpoint: StructuralCheckpoint | None
    checkpoint_error: str | None


@dataclass(frozen=True)
class WalletPosition:
    token_id: str
    size: float
    avg_price: float
    updated_ts: float


@dataclass(frozen=True)
class TokenCid:
    token_id: str
    condition_id: str


@dataclass(frozen=True)
class BookedVenue:
    maker_order_id: str
    booked_qty: float


@dataclass(frozen=True)
class FillEvent:
    seq: int
    fill_key: str
    event: str
    token_id: str | None
    side: str | None
    price: float | None
    size: float | None
    ts: float | None
    status: str | None
    cash_delta: float | None
    maker_order_id: str | None


@dataclass(frozen=True)
class OutboxPage:
    events: tuple[FillEvent, ...]
    next_seq: int | None
    max_seq: int
    has_more: bool


@dataclass(frozen=True)
class WalletSnapshot:
    db_path: Path
    ok: bool
    error: str | None
    read_at: float
    funder: str | None
    sessions: tuple[SessionSnapshot, ...]
    bindings: tuple[OrderBinding, ...]
    open_buy_commands: tuple[CoreCommand, ...]
    unsettled_buys: tuple[UnsettledBuy, ...]
    positions: tuple[WalletPosition, ...]
    token_cids: tuple[TokenCid, ...]
    booked_buy_qty: tuple[BookedVenue, ...]
    max_outbox_seq: int
    matched_fill_keys: frozenset[str]
    fill_boundary_ok: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class _SessionRow:
    session_id: str
    condition_id: str
    game: str
    yes_token: str
    no_token: str
    yes_is_radiant: bool
    schema_version: int
    policy_version: str
    revision: int
    recovery: bool
    recovery_generation: int
    last_outbox_seq: int
    created_at: float
    updated_at: float
    checkpoint_raw: str


@dataclass(frozen=True)
class _WalletRows:
    sessions: tuple[_SessionRow, ...]
    bindings: tuple[OrderBinding, ...]
    open_buy_commands: tuple[CoreCommand, ...]
    unsettled_buys: tuple[UnsettledBuy, ...]
    positions: tuple[WalletPosition, ...]
    token_cids: tuple[TokenCid, ...]
    booked_buy_qty: tuple[BookedVenue, ...]
    funder: str | None
    max_outbox_seq: int | None
    matched_fill_keys: frozenset[str] | None


def open_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def cell(row: sqlite3.Row, key: str) -> object:
    value: object = row[key]
    return value


def _num(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"numeric cell expected, got {type(value).__name__}")
    return float(value)


def _opt_num(value: object) -> float | None:
    return None if value is None else _num(value)


def _opt_text(value: object) -> str | None:
    return None if value is None else str(value)


def _num_cell(row: sqlite3.Row, key: str) -> float:
    return _num(cell(row, key))


def _int_cell(row: sqlite3.Row, key: str) -> int:
    return int(_num_cell(row, key))


def _text_cell(row: sqlite3.Row, key: str) -> str:
    return str(cell(row, key))


@dataclass(frozen=True)
class _DecodedCheckpoint:
    updated_at: float
    revision: int
    checkpoint: StructuralCheckpoint | None
    error: str | None


class CheckpointCache:
    def __init__(self) -> None:
        self._decoded: dict[str, _DecodedCheckpoint] = {}

    def decode(
        self, *, session_id: str, updated_at: float, revision: int, raw: str
    ) -> tuple[StructuralCheckpoint | None, str | None]:
        hit = self._decoded.get(session_id)
        if hit is not None and hit.updated_at == updated_at and hit.revision == revision:
            return hit.checkpoint, hit.error
        try:
            entry = _DecodedCheckpoint(updated_at, revision, decode_checkpoint(raw), None)
        except CoreSchemaError as exc:
            entry = _DecodedCheckpoint(updated_at, revision, None, str(exc))
        self._decoded[session_id] = entry
        return entry.checkpoint, entry.error

    def retain(self, session_ids: frozenset[str]) -> None:
        for key in tuple(self._decoded):
            if key not in session_ids:
                del self._decoded[key]


def table_rows(
    conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
) -> tuple[sqlite3.Row, ...] | None:
    try:
        return tuple(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return None


def _copy_sessions(conn: sqlite3.Connection, notes: list[str]) -> tuple[_SessionRow, ...]:
    rows = table_rows(conn, "SELECT * FROM core_sessions ORDER BY condition_id")
    if rows is None:
        notes.append("core_sessions unreadable or missing")
        return ()
    return tuple(
        _SessionRow(
            session_id=_text_cell(row, "session_id"),
            condition_id=_text_cell(row, "condition_id"),
            game=_text_cell(row, "game"),
            yes_token=_text_cell(row, "yes_token"),
            no_token=_text_cell(row, "no_token"),
            yes_is_radiant=bool(cell(row, "yes_is_radiant")),
            schema_version=_int_cell(row, "schema_version"),
            policy_version=_text_cell(row, "policy_version"),
            revision=_int_cell(row, "revision"),
            recovery=bool(cell(row, "recovery")),
            recovery_generation=_int_cell(row, "recovery_generation"),
            last_outbox_seq=_int_cell(row, "last_outbox_seq"),
            created_at=_num_cell(row, "created_at"),
            updated_at=_num_cell(row, "updated_at"),
            checkpoint_raw=_text_cell(row, "checkpoint"),
        )
        for row in rows
    )


def _decode_sessions(
    rows: tuple[_SessionRow, ...], checkpoints: CheckpointCache
) -> tuple[SessionSnapshot, ...]:
    sessions: list[SessionSnapshot] = []
    for row in rows:
        checkpoint, error = checkpoints.decode(
            session_id=row.session_id,
            updated_at=row.updated_at,
            revision=row.revision,
            raw=row.checkpoint_raw,
        )
        sessions.append(
            SessionSnapshot(
                session_id=row.session_id,
                condition_id=row.condition_id,
                game=row.game,
                yes_token=row.yes_token,
                no_token=row.no_token,
                yes_is_radiant=row.yes_is_radiant,
                schema_version=row.schema_version,
                policy_version=row.policy_version,
                revision=row.revision,
                recovery=row.recovery,
                recovery_generation=row.recovery_generation,
                last_outbox_seq=row.last_outbox_seq,
                created_at=row.created_at,
                updated_at=row.updated_at,
                checkpoint=checkpoint,
                checkpoint_error=error,
            )
        )
    return tuple(sessions)


def _read_bindings(
    conn: sqlite3.Connection, session_ids: tuple[str, ...], notes: list[str]
) -> tuple[OrderBinding, ...]:
    bindings: list[OrderBinding] = []
    for session_id in session_ids:
        try:
            bindings.extend(list_bindings(conn, session_id))
        except (sqlite3.Error, CoreSchemaError) as exc:
            notes.append(f"bindings for {session_id} unreadable: {exc}")
    return tuple(bindings)


def _sessions_with_orders(
    rows: tuple[_SessionRow, ...], checkpoints: CheckpointCache
) -> tuple[str, ...]:
    holding: list[str] = []
    for row in rows:
        checkpoint, _error = checkpoints.decode(
            session_id=row.session_id,
            updated_at=row.updated_at,
            revision=row.revision,
            raw=row.checkpoint_raw,
        )
        if checkpoint is not None and checkpoint.orders:
            holding.append(row.session_id)
    return tuple(holding)


def _read_open_buy_commands(conn: sqlite3.Connection, notes: list[str]) -> tuple[CoreCommand, ...]:
    try:
        return list_open_buy_commands(conn)
    except (sqlite3.Error, CoreSchemaError) as exc:
        notes.append(f"open buy commands unreadable: {exc}")
        return ()


def _read_unsettled(conn: sqlite3.Connection, notes: list[str]) -> tuple[UnsettledBuy, ...]:
    try:
        return tuple(open_unsettled_buys(conn))
    except sqlite3.Error:
        notes.append("unsettled_buys unreadable or missing")
        return ()


def _read_positions(conn: sqlite3.Connection, notes: list[str]) -> tuple[WalletPosition, ...]:
    rows = table_rows(
        conn, "SELECT token_id, size, avg_price, updated_ts FROM positions ORDER BY token_id"
    )
    if rows is None:
        notes.append("positions unreadable or missing")
        return ()
    return tuple(
        WalletPosition(
            token_id=_text_cell(row, "token_id"),
            size=_num_cell(row, "size"),
            avg_price=_num_cell(row, "avg_price"),
            updated_ts=_num_cell(row, "updated_ts"),
        )
        for row in rows
    )


def _read_identity(conn: sqlite3.Connection, notes: list[str]) -> str | None:
    rows = table_rows(conn, "SELECT v FROM wallet_identity WHERE k='funder'")
    if rows is None:
        notes.append("wallet_identity unreadable or missing")
        return None
    if not rows:
        return None
    return _opt_text(cell(rows[0], "v"))


def _read_token_cids(conn: sqlite3.Connection, notes: list[str]) -> tuple[TokenCid, ...]:
    rows = table_rows(conn, "SELECT token_id, condition_id FROM token_cid ORDER BY token_id")
    if rows is None:
        notes.append("token_cid unreadable or missing")
        return ()
    return tuple(
        TokenCid(token_id=_text_cell(row, "token_id"), condition_id=_text_cell(row, "condition_id"))
        for row in rows
    )


def _read_booked_qty(conn: sqlite3.Connection, notes: list[str]) -> tuple[BookedVenue, ...]:
    rows = table_rows(
        conn,
        "SELECT maker_order_id, SUM(size) AS booked FROM fill_ledger"
        " WHERE side='BUY' AND status IN ('MATCHED', 'CONFIRMED') GROUP BY maker_order_id",
    )
    if rows is None:
        notes.append("fill_ledger booked quantities unreadable or missing")
        return ()
    return tuple(
        BookedVenue(
            maker_order_id=_text_cell(row, "maker_order_id"),
            booked_qty=_num_cell(row, "booked"),
        )
        for row in rows
    )


def _read_max_outbox_seq(conn: sqlite3.Connection, notes: list[str]) -> int | None:
    rows = table_rows(conn, "SELECT COALESCE(MAX(seq), 0) AS m FROM fill_outbox")
    if rows is None:
        notes.append("fill_outbox unreadable or missing")
        return None
    return _int_cell(rows[0], "m")


def _read_matched_fill_keys(conn: sqlite3.Connection, notes: list[str]) -> frozenset[str] | None:
    rows = table_rows(conn, "SELECT fill_key FROM fill_ledger WHERE status='MATCHED'")
    if rows is None:
        notes.append("fill_ledger matched keys unreadable or missing")
        return None
    return frozenset(_text_cell(row, "fill_key") for row in rows)


def _copy_wallet_rows(
    conn: sqlite3.Connection, notes: list[str], checkpoints: CheckpointCache
) -> _WalletRows:
    session_rows = _copy_sessions(conn, notes)
    return _WalletRows(
        sessions=session_rows,
        bindings=_read_bindings(conn, _sessions_with_orders(session_rows, checkpoints), notes),
        open_buy_commands=_read_open_buy_commands(conn, notes),
        unsettled_buys=_read_unsettled(conn, notes),
        positions=_read_positions(conn, notes),
        token_cids=_read_token_cids(conn, notes),
        booked_buy_qty=_read_booked_qty(conn, notes),
        funder=_read_identity(conn, notes),
        max_outbox_seq=_read_max_outbox_seq(conn, notes),
        matched_fill_keys=_read_matched_fill_keys(conn, notes),
    )


def _failed_snapshot(
    path: Path, read_at: float, error: str, notes: tuple[str, ...]
) -> WalletSnapshot:
    return WalletSnapshot(
        db_path=path,
        ok=False,
        error=error,
        read_at=read_at,
        funder=None,
        sessions=(),
        bindings=(),
        open_buy_commands=(),
        unsettled_buys=(),
        positions=(),
        token_cids=(),
        booked_buy_qty=(),
        max_outbox_seq=0,
        matched_fill_keys=frozenset(),
        fill_boundary_ok=False,
        notes=notes,
    )


def read_wallet_snapshot(
    path: Path, *, checkpoints: CheckpointCache | None = None, now: float | None = None
) -> WalletSnapshot:
    read_at = time.time() if now is None else now
    cache = checkpoints if checkpoints is not None else CheckpointCache()
    try:
        conn = open_readonly(path)
    except (sqlite3.Error, OSError) as exc:
        return _failed_snapshot(path, read_at, type(exc).__name__, ())
    notes: list[str] = []
    raw: _WalletRows | None = None
    error: str | None = None
    try:
        conn.execute("BEGIN")
        raw = _copy_wallet_rows(conn, notes, cache)
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        error = type(exc).__name__
        notes.append("snapshot transaction aborted")
    finally:
        conn.close()
    if raw is None or error is not None:
        return _failed_snapshot(path, read_at, error or "read failed", tuple(notes))
    sessions = _decode_sessions(raw.sessions, cache)
    cache.retain(frozenset(row.session_id for row in raw.sessions))
    return WalletSnapshot(
        db_path=path,
        ok=True,
        error=None,
        read_at=read_at,
        funder=raw.funder,
        sessions=sessions,
        bindings=raw.bindings,
        open_buy_commands=raw.open_buy_commands,
        unsettled_buys=raw.unsettled_buys,
        positions=raw.positions,
        token_cids=raw.token_cids,
        booked_buy_qty=raw.booked_buy_qty,
        max_outbox_seq=raw.max_outbox_seq if raw.max_outbox_seq is not None else 0,
        matched_fill_keys=(
            raw.matched_fill_keys if raw.matched_fill_keys is not None else frozenset()
        ),
        fill_boundary_ok=raw.max_outbox_seq is not None and raw.matched_fill_keys is not None,
        notes=tuple(notes),
    )


def read_outbox_page(path: Path, *, after_seq: int, limit: int) -> OutboxPage | None:
    try:
        conn = open_readonly(path)
    except (sqlite3.Error, OSError):
        return None
    try:
        conn.execute("BEGIN")
        max_row = table_rows(conn, "SELECT COALESCE(MAX(seq), 0) AS m FROM fill_outbox")
        rows = table_rows(
            conn,
            "SELECT o.seq, o.fill_key, o.event, l.token_id, l.side, l.price, l.size,"
            " l.ts, l.status, l.cash_delta, l.maker_order_id"
            " FROM fill_outbox o JOIN fill_ledger l ON l.fill_key = o.fill_key"
            " WHERE o.seq > ? ORDER BY o.seq LIMIT ?",
            (after_seq, limit),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if rows is None:
        return None
    max_seq = _int_cell(max_row[0], "m") if max_row else 0
    events = tuple(
        FillEvent(
            seq=_int_cell(row, "seq"),
            fill_key=_text_cell(row, "fill_key"),
            event=_text_cell(row, "event"),
            token_id=_opt_text(cell(row, "token_id")),
            side=_opt_text(cell(row, "side")),
            price=_opt_num(cell(row, "price")),
            size=_opt_num(cell(row, "size")),
            ts=_opt_num(cell(row, "ts")),
            status=_opt_text(cell(row, "status")),
            cash_delta=_opt_num(cell(row, "cash_delta")),
            maker_order_id=_opt_text(cell(row, "maker_order_id")),
        )
        for row in rows
    )
    last_seq = events[-1].seq if events else after_seq
    return OutboxPage(
        events=events,
        next_seq=last_seq if events else None,
        max_seq=max_seq,
        has_more=last_seq < max_seq,
    )
