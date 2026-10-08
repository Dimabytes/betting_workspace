from dataclasses import dataclass

from dashboard.wallet import WalletSnapshot
from trader.core_persistence import CoreCommand, OrderCheckpoint, UnsettledBuy


@dataclass(frozen=True)
class ReserveDetail:
    source: str
    session_id: str | None
    condition_id: str | None
    venue_id: str | None
    core_order_id: str | None
    token_index: int | None
    price: float | None
    remaining_qty: float
    notional: float
    note: str | None


@dataclass(frozen=True)
class ReserveReport:
    core_buy: float
    command_buy: float
    unsettled_buy: float
    total_known: float
    available_cash: float | None
    details: tuple[ReserveDetail, ...]
    incomplete: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class _OrderReserve:
    detail: ReserveDetail
    incomplete: bool


@dataclass(frozen=True)
class _ReservePart:
    total: float
    incomplete: bool


_GONE_CANCEL_REASON = "unsettled"


def _effective_status(status: str, cancel_reason: str) -> str:
    if status == "unknown" and cancel_reason == _GONE_CANCEL_REASON:
        return "gone"
    return status


def _core_detail(
    session_id: str,
    condition_id: str,
    order: OrderCheckpoint,
    venue: str | None,
    remaining: float,
    notional: float,
    note: str | None,
) -> ReserveDetail:
    return ReserveDetail(
        source="core",
        session_id=session_id,
        condition_id=condition_id,
        venue_id=venue,
        core_order_id=order.order_id,
        token_index=order.token_index,
        price=order.price,
        remaining_qty=remaining,
        notional=notional,
        note=note,
    )


def _reserve_core_order(
    session_id: str,
    condition_id: str,
    order: OrderCheckpoint,
    *,
    finished: bool,
    venue: str | None,
    unsettled_by_venue: dict[str, UnsettledBuy],
) -> _OrderReserve | None:
    if order.side != "BUY":
        return None
    remaining = max(0.0, order.submitted_qty - order.filled_qty)
    if finished:
        return _OrderReserve(
            _core_detail(
                session_id,
                condition_id,
                order,
                venue,
                remaining,
                0.0,
                "excluded: session terminal",
            ),
            incomplete=False,
        )
    if _effective_status(order.status, order.cancel_reason) == "gone":
        if venue is not None and venue in unsettled_by_venue:
            return _OrderReserve(
                _core_detail(
                    session_id,
                    condition_id,
                    order,
                    venue,
                    remaining,
                    0.0,
                    "gone BUY covered by unsettled row",
                ),
                incomplete=False,
            )
        return _OrderReserve(
            _core_detail(
                session_id,
                condition_id,
                order,
                venue,
                remaining,
                remaining * order.price,
                "gone BUY without durable unsettled row",
            ),
            incomplete=True,
        )
    if remaining <= 0.0:
        return None
    row = unsettled_by_venue.get(venue) if venue is not None else None
    if row is None:
        return _OrderReserve(
            _core_detail(
                session_id,
                condition_id,
                order,
                venue,
                remaining,
                remaining * order.price,
                None,
            ),
            incomplete=False,
        )
    note = "open order shares venue with unsettled row"
    if abs(row.price - order.price) > 1e-9:
        note = "open order shares venue with unsettled row; price differs"
    return _OrderReserve(
        _core_detail(
            session_id, condition_id, order, venue, remaining, remaining * order.price, note
        ),
        incomplete=True,
    )


def _predates_catalog(
    condition_id: str,
    updated_at: float,
    catalog_cids: frozenset[str],
    run_started_at: float | None,
) -> bool:
    if run_started_at is None:
        return False
    return condition_id not in catalog_cids and updated_at < run_started_at


def _reserve_core_orders(
    snapshot: WalletSnapshot,
    *,
    finished_cids: frozenset[str],
    catalog_cids: frozenset[str],
    run_started_at: float | None,
    binding_venue: dict[tuple[str, str], str | None],
    unsettled_by_venue: dict[str, UnsettledBuy],
    covered_venues: set[str],
    details: list[ReserveDetail],
    limitations: list[str],
) -> _ReservePart:
    total = 0.0
    incomplete = False
    for session in snapshot.sessions:
        if session.checkpoint is None:
            incomplete = True
            limitations.append(
                f"session {session.session_id} checkpoint undecodable: {session.checkpoint_error}"
            )
            continue
        if _predates_catalog(
            session.condition_id, session.updated_at, catalog_cids, run_started_at
        ):
            continue
        finished = session.condition_id in finished_cids
        for order in session.checkpoint.orders:
            venue = binding_venue.get((session.session_id, order.order_id))
            outcome = _reserve_core_order(
                session.session_id,
                session.condition_id,
                order,
                finished=finished,
                venue=venue,
                unsettled_by_venue=unsettled_by_venue,
            )
            if outcome is None:
                continue
            details.append(outcome.detail)
            total += outcome.detail.notional
            incomplete = incomplete or outcome.incomplete
            if outcome.detail.notional > 0.0 and venue in unsettled_by_venue:
                covered_venues.add(venue)
    return _ReservePart(total=total, incomplete=incomplete)


def _command_reserve(
    command: CoreCommand,
    *,
    cid: str | None,
    order: OrderCheckpoint | None,
    venue: str | None,
) -> _OrderReserve:
    price = command.price if command.price is not None else 0.0
    quantity = command.quantity if command.quantity is not None else 0.0
    counted = price * quantity
    note = None
    incomplete = False
    if order is not None:
        counted = 0.0
        note = "covered by checkpoint order"
        if command.price is not None and abs(command.price - order.price) > 1e-9:
            incomplete = True
            note = "covered by checkpoint order; price conflicts with checkpoint"
        if venue is not None and command.venue_id is not None and venue != command.venue_id:
            incomplete = True
            note = "covered by checkpoint order; venue conflicts with binding"
    return _OrderReserve(
        ReserveDetail(
            source="command",
            session_id=command.session_id,
            condition_id=cid,
            venue_id=command.venue_id or venue,
            core_order_id=command.core_order_id,
            token_index=command.token_index,
            price=command.price,
            remaining_qty=quantity,
            notional=counted,
            note=note,
        ),
        incomplete=incomplete,
    )


def _reserve_commands(
    snapshot: WalletSnapshot,
    *,
    finished_cids: frozenset[str],
    catalog_cids: frozenset[str],
    run_started_at: float | None,
    binding_venue: dict[tuple[str, str], str | None],
    details: list[ReserveDetail],
) -> _ReservePart:
    orders_by_session = {
        session.session_id: (
            {order.order_id: order for order in session.checkpoint.orders}
            if session.checkpoint is not None
            else {}
        )
        for session in snapshot.sessions
    }
    session_by_id = {session.session_id: session for session in snapshot.sessions}
    total = 0.0
    incomplete = False
    for command in snapshot.open_buy_commands:
        session = session_by_id.get(command.session_id)
        if session is not None and (
            session.condition_id in finished_cids
            or _predates_catalog(
                session.condition_id, session.updated_at, catalog_cids, run_started_at
            )
        ):
            continue
        outcome = _command_reserve(
            command,
            cid=None if session is None else session.condition_id,
            order=orders_by_session.get(command.session_id, {}).get(command.core_order_id),
            venue=binding_venue.get((command.session_id, command.core_order_id)),
        )
        total += outcome.detail.notional
        incomplete = incomplete or outcome.incomplete
        details.append(outcome.detail)
    return _ReservePart(total=total, incomplete=incomplete)


def _reserve_unsettled(
    buys: tuple[UnsettledBuy, ...],
    bookings: dict[str, float],
    covered_venues: set[str],
    details: list[ReserveDetail],
) -> float:
    total = 0.0
    for row in buys:
        if row.venue_id in covered_venues:
            details.append(
                ReserveDetail(
                    source="unsettled",
                    session_id=row.session_id,
                    condition_id=None,
                    venue_id=row.venue_id,
                    core_order_id=None,
                    token_index=None,
                    price=row.price,
                    remaining_qty=0.0,
                    notional=0.0,
                    note="venue covered by open checkpoint order",
                )
            )
            continue
        remaining = max(0.0, row.qty - bookings.get(row.venue_id, 0.0))
        notional = remaining * row.price
        total += notional
        details.append(
            ReserveDetail(
                source="unsettled",
                session_id=row.session_id,
                condition_id=None,
                venue_id=row.venue_id,
                core_order_id=None,
                token_index=None,
                price=row.price,
                remaining_qty=remaining,
                notional=notional,
                note=None if row.proven else "not yet proven at venue",
            )
        )
    return total


def compute_reserve(
    snapshot: WalletSnapshot,
    *,
    finished_cids: frozenset[str],
    catalog_cids: frozenset[str],
    run_started_at: float | None,
    collateral_usdc: float | None,
) -> ReserveReport:
    if not snapshot.ok:
        return ReserveReport(
            core_buy=0.0,
            command_buy=0.0,
            unsettled_buy=0.0,
            total_known=0.0,
            available_cash=None,
            details=(),
            incomplete=True,
            limitations=(f"wallet unreadable: {snapshot.error}",),
        )
    details: list[ReserveDetail] = []
    limitations: list[str] = []
    bookings = {row.maker_order_id: row.booked_qty for row in snapshot.booked_buy_qty}
    unsettled_by_venue = {row.venue_id: row for row in snapshot.unsettled_buys}
    binding_venue = {
        (binding.session_id, binding.core_order_id): binding.venue_id
        for binding in snapshot.bindings
    }
    covered_venues: set[str] = set()
    core = _reserve_core_orders(
        snapshot,
        finished_cids=finished_cids,
        catalog_cids=catalog_cids,
        run_started_at=run_started_at,
        binding_venue=binding_venue,
        unsettled_by_venue=unsettled_by_venue,
        covered_venues=covered_venues,
        details=details,
        limitations=limitations,
    )
    commands = _reserve_commands(
        snapshot,
        finished_cids=finished_cids,
        catalog_cids=catalog_cids,
        run_started_at=run_started_at,
        binding_venue=binding_venue,
        details=details,
    )
    unsettled_total = _reserve_unsettled(snapshot.unsettled_buys, bookings, covered_venues, details)
    total = core.total + commands.total + unsettled_total
    available = None if collateral_usdc is None else collateral_usdc - total
    return ReserveReport(
        core_buy=core.total,
        command_buy=commands.total,
        unsettled_buy=unsettled_total,
        total_known=total,
        available_cash=available,
        details=tuple(details),
        incomplete=core.incomplete or commands.incomplete,
        limitations=tuple(limitations),
    )
