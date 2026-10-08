import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from dashboard import diagnostics
from dashboard.catalog import ArchiveEntry, DecisionFacts, MapView
from dashboard.home import fmt_clock, fmt_money, map_title, match_url, polymarket_url
from dashboard.hub_types import HubSnapshot, SessionFacts
from dashboard.market_books import BookSnapshot
from dashboard.reserve import ReserveDetail
from dashboard.summarize import PositionEntry
from dashboard.wallet import WalletPosition
from trader.core_persistence import CoreCommand, UnsettledBuy


@dataclass(frozen=True)
class PositionLine:
    token_id: str
    outcome: str
    size: float
    avg_price: float
    mark: float | None
    mark_state: Literal["book", "book_stale", "unknown"]
    unrealized: float | None


@dataclass(frozen=True)
class ActiveRow:
    match_id: str
    title: str
    game: str | None
    second: float | None
    phase: str | None
    window: str | None
    paused: bool | None
    positions: tuple[PositionLine, ...]
    position_state: Literal["known", "empty", "unknown"]
    state_lines: tuple[str, ...]
    unrealized_total: float | None
    realized: float | None
    rebate: float | None
    net: float | None
    reason: str
    decision_age_s: float | None
    feed_age_s: float | None
    stale: bool
    match_url: str
    polymarket_url: str | None
    details: tuple[str, ...]


@dataclass(frozen=True)
class IdleRow:
    key: str
    title: str
    game: str | None
    reason: str
    reason_source: str | None
    reason_age_s: float | None
    starts_at: float | None
    source: str
    match_url: str | None
    polymarket_url: str | None
    details: tuple[str, ...]


@dataclass(frozen=True)
class ClosedRow:
    match_id: str
    title: str
    game: str | None
    closed_observed_at: float | None
    closed_age_s: float | None
    net: float | None
    realized: float | None
    imv: float | None
    rebate: float | None
    fill_count: int | None
    match_url: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class UnsettledLine:
    venue_id: str | None
    core_order_id: str | None
    session_id: str | None
    token_id: str | None
    qty: float | None
    remaining_notional: float | None
    age_s: float | None
    status: str
    source: str


@dataclass(frozen=True)
class ResidualLeg:
    token_id: str | None
    outcome: str
    api_size: float | None
    api_price: float | None
    api_redeemable: bool | None
    local_size: float | None
    local_avg: float | None
    mark: float | None
    mark_state: Literal["api", "book", "book_stale", "unknown"]
    value: float | None


@dataclass(frozen=True)
class ResidualRow:
    key: str
    match_id: str | None
    title: str
    game: str | None
    redeem_state: str
    legs: tuple[ResidualLeg, ...]
    commitments: tuple[UnsettledLine, ...]
    positions_state: Literal["complete", "incomplete", "error", "unknown"]
    match_url: str | None
    polymarket_url: str | None
    details: tuple[str, ...]


@dataclass(frozen=True)
class ListsView:
    active: tuple[ActiveRow, ...]
    idle: tuple[IdleRow, ...]
    closed_dated: tuple[ClosedRow, ...]
    closed_undated: tuple[ClosedRow, ...]
    residuals: tuple[ResidualRow, ...]
    positions_state: Literal["complete", "incomplete", "error", "unknown"]
    nontrading_note: str | None
    closed_since: str


@dataclass(frozen=True)
class ListIndexes:
    books: Mapping[str, BookSnapshot]
    positions: Mapping[str, WalletPosition]
    sessions_by_cid: Mapping[str, SessionFacts]
    sessions_by_id: Mapping[str, SessionFacts]
    token_cid: Mapping[str, str]
    buys_by_session: Mapping[str, tuple[CoreCommand, ...]]
    unsettled_by_cid: Mapping[str, tuple[UnsettledBuy, ...]]
    terminal: tuple[MapView, ...]
    api_rows: tuple[PositionEntry, ...]
    api_by_cid: Mapping[str, tuple[PositionEntry, ...]]
    positions_state: Literal["complete", "incomplete", "error", "unknown"]
    reserve_by_venue: Mapping[str, ReserveDetail]
    reserve_open_by_cid: Mapping[str, tuple[ReserveDetail, ...]]
    known_cids: frozenset[str]


def _positions_state(snap: HubSnapshot) -> Literal["complete", "incomplete", "error", "unknown"]:
    result = snap.day.positions_candidate or snap.day.positions
    if result is None:
        return "unknown"
    if result.error is not None:
        return "error"
    if not result.traversal_complete:
        return "incomplete"
    return "complete"


def _terminal_views(snap: HubSnapshot) -> tuple[MapView, ...]:
    seen: dict[str, MapView] = {}
    for view in (*snap.maps, *snap.legacy_maps):
        entry = view.entry
        if view.status not in ("terminal", "final") and not entry.session_ended:
            continue
        prev = seen.get(entry.match_id)
        if prev is None or (prev.entry.tree != "live" and entry.tree == "live"):
            seen[entry.match_id] = view
    return tuple(seen.values())


def _list_indexes(snap: HubSnapshot) -> ListIndexes:
    sessions_by_id = {s.session_id: s for s in snap.wallet.sessions}
    token_cid = {row.token_id: row.condition_id for row in snap.wallet.token_cids}
    buys_by_session: dict[str, list[CoreCommand]] = {}
    for cmd in snap.wallet.open_buys:
        buys_by_session.setdefault(cmd.session_id, []).append(cmd)
    unsettled_by_cid: dict[str, list[UnsettledBuy]] = {}
    for row in snap.wallet.unsettled:
        cid = token_cid.get(row.token_id)
        if cid is None and row.session_id in sessions_by_id:
            cid = sessions_by_id[row.session_id].condition_id
        if cid is not None:
            unsettled_by_cid.setdefault(cid, []).append(row)
    api_result = snap.day.positions_candidate or snap.day.positions
    api_rows = () if api_result is None else api_result.positions
    api_by_cid: dict[str, list[PositionEntry]] = {}
    for pos in api_rows:
        if pos.condition_id is not None and pos.size != 0.0:
            api_by_cid.setdefault(pos.condition_id, []).append(pos)
    reserve_details = () if snap.reserve is None else snap.reserve.details
    reserve_by_venue: dict[str, ReserveDetail] = {}
    reserve_open: dict[str, list[ReserveDetail]] = {}
    for detail in reserve_details:
        if detail.source == "unsettled" and detail.venue_id:
            reserve_by_venue[detail.venue_id] = detail
        elif detail.source in ("command", "core") and detail.condition_id is not None:
            reserve_open.setdefault(detail.condition_id, []).append(detail)
    return ListIndexes(
        books={book.token_id: book for book in snap.books},
        positions={pos.token_id: pos for pos in snap.wallet.positions},
        sessions_by_cid={s.condition_id: s for s in snap.wallet.sessions},
        sessions_by_id=sessions_by_id,
        token_cid=token_cid,
        buys_by_session={k: tuple(v) for k, v in buys_by_session.items()},
        unsettled_by_cid={k: tuple(v) for k, v in unsettled_by_cid.items()},
        terminal=_terminal_views(snap),
        api_rows=api_rows,
        api_by_cid={k: tuple(v) for k, v in api_by_cid.items()},
        positions_state=_positions_state(snap),
        reserve_by_venue=reserve_by_venue,
        reserve_open_by_cid={k: tuple(v) for k, v in reserve_open.items()},
        known_cids=frozenset(
            view.entry.condition_id
            for view in (*snap.maps, *snap.legacy_maps)
            if view.entry.condition_id is not None
        ),
    )


def _book_mark(
    book: BookSnapshot | None,
) -> tuple[float | None, Literal["book", "book_stale", "unknown"]]:
    if book is None or not book.initialized or not book.ready:
        return None, "unknown"
    if not book.bids or not book.asks:
        return None, "unknown"
    bid = book.bids[0].price
    ask = book.asks[0].price
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid > ask:
        return None, "unknown"
    mark = (bid + ask) / 2.0
    if not (0.0 < mark < 1.0):
        return None, "unknown"
    return mark, "book" if book.connected else "book_stale"


@dataclass(frozen=True)
class _TokenSide:
    side: str
    token_id: str | None
    team: str | None


def _token_sides(entry: ArchiveEntry) -> tuple[_TokenSide, _TokenSide]:
    yes_team = entry.radiant if entry.yes_is_radiant else entry.dire
    no_team = entry.dire if entry.yes_is_radiant else entry.radiant
    return (
        _TokenSide("YES", entry.yes_token, yes_team),
        _TokenSide("NO", entry.no_token, no_team),
    )


def _position_lines(entry: ArchiveEntry, idx: ListIndexes) -> tuple[PositionLine, ...]:
    legs: list[PositionLine] = []
    for side in _token_sides(entry):
        if side.token_id is None:
            continue
        position = idx.positions.get(side.token_id)
        if position is None or position.size == 0.0:
            continue
        mark, mark_state = _book_mark(idx.books.get(side.token_id))
        outcome = f"{side.side} {side.team}" if side.team else side.side
        legs.append(
            PositionLine(
                token_id=side.token_id,
                outcome=outcome,
                size=position.size,
                avg_price=position.avg_price,
                mark=mark,
                mark_state=mark_state,
                unrealized=(None if mark is None else position.size * (mark - position.avg_price)),
            )
        )
    return tuple(legs)


def _decision_second(decision: DecisionFacts | None) -> float | None:
    if decision is None:
        return None
    return decision.second if decision.second is not None else decision.snapshot_second


def _row_reason(decision: DecisionFacts | None, entry: ArchiveEntry) -> str:
    if decision is None:
        return "нет данных"
    min_delta = entry.params.min_abs_delta if entry.params is not None else None
    if decision.model_evaluated and decision.entry_block:
        return diagnostics.entry_block_label(
            decision.entry_block,
            model_evaluated=decision.model_evaluated,
            raw_delta=decision.raw_delta,
            min_abs_delta=min_delta,
        )
    if decision.reason:
        return diagnostics.reason_label(decision.reason)
    if decision.entry_block:
        return diagnostics.entry_block_label(
            decision.entry_block,
            model_evaluated=decision.model_evaluated,
            raw_delta=decision.raw_delta,
            min_abs_delta=min_delta,
        )
    return "нет данных"


def _row_window(decision: DecisionFacts | None, entry: ArchiveEntry) -> str | None:
    cutoff = entry.params.buy_cutoff_second if entry.params is not None else None
    second = _decision_second(decision)
    if cutoff is None:
        return None
    if second is None:
        return f"вход до {cutoff:g}с"
    return f"вход до {cutoff:g}с" if second <= cutoff else f"после cutoff {cutoff:g}с"


def _session_state_lines(
    session: SessionFacts | None,
    open_buys: Sequence[CoreCommand],
    unsettled: Sequence[UnsettledBuy],
) -> tuple[str, ...]:
    lines: list[str] = []
    if session is not None:
        if session.sell_only:
            lines.append("режим: только SELL")
        if session.recovery_pending:
            lines.append("recovery pending")
        if session.winding_down:
            lines.append("winding down")
        for sell in session.sells:
            lines.append(
                f"SELL {sell.remaining_qty:g}/{sell.submitted_qty:g}"
                f" @ {sell.price:g} · {sell.status}"
            )
        if session.unconfirmed or session.pending_ownership:
            lines.append(f"unconfirmed {session.unconfirmed} · pending {session.pending_ownership}")
    for cmd in open_buys:
        lines.append(
            f"BUY open {cmd.quantity if cmd.quantity is not None else '?'}"
            f" @ {cmd.price if cmd.price is not None else '?'}"
        )
    for row in unsettled:
        lines.append(f"BUY unsettled {row.qty:g} · venue {row.venue_id}")
    return tuple(lines)


def _active_row(
    view: MapView,
    idx: ListIndexes,
    wallet_ok: bool,
) -> ActiveRow:
    entry = view.entry
    decision = view.decision
    cid = entry.condition_id or ""
    session = idx.sessions_by_cid.get(cid)
    legs = _position_lines(entry, idx)
    if not wallet_ok:
        position_state: Literal["known", "empty", "unknown"] = "unknown"
    elif legs:
        position_state = "known"
    else:
        position_state = "empty"
    marks_known = [leg.unrealized for leg in legs if leg.unrealized is not None]
    unrealized_total = sum(marks_known) if marks_known else None
    realized = None
    if wallet_ok and entry.realized is not None:
        realized = entry.realized + sum(leg.size * leg.avg_price for leg in legs)
    stale = view.status != "live"
    details: list[str] = [
        f"match {entry.match_id}",
        f"condition {entry.condition_id or '—'}",
        f"дерево {entry.tree}",
    ]
    if decision is not None:
        details.append(f"signal {decision.path.name} · feed {decision.feed_source or '?'}")
        if decision.model_evaluated and decision.raw_delta is not None:
            details.append(f"raw Δ {decision.raw_delta:g}")
    details.extend(view.evidence)
    return ActiveRow(
        match_id=entry.match_id,
        title=map_title(entry),
        game=entry.game,
        second=_decision_second(decision),
        phase=decision.phase if decision is not None else None,
        window=_row_window(decision, entry),
        paused=decision.paused if decision is not None else None,
        positions=legs,
        position_state=position_state,
        state_lines=_session_state_lines(
            session,
            idx.buys_by_session.get(session.session_id, ()) if session is not None else (),
            idx.unsettled_by_cid.get(cid, ()),
        ),
        unrealized_total=unrealized_total,
        realized=realized,
        rebate=entry.rebate,
        net=(
            (realized or 0.0) + (unrealized_total or 0.0) + (entry.rebate or 0.0)
            if realized is not None or unrealized_total is not None or entry.rebate is not None
            else None
        ),
        reason=_row_reason(decision, entry),
        decision_age_s=view.decision_age_s,
        feed_age_s=view.feed_age_s,
        stale=stale,
        match_url=match_url(entry.match_id),
        polymarket_url=polymarket_url(entry.event_slug, entry.slug),
        details=tuple(details),
    )


_IDLE_REASONS: dict[str, str] = {
    "market inactive": "рынок неактивен",
    "not accepting orders": "не принимает ордера",
    "order book disabled": "стакан отключён",
    "reason unknown": "причина неизвестна",
}


def _idle_rows(snap: HubSnapshot, now_s: float) -> tuple[IdleRow, ...]:
    rows: list[IdleRow] = []
    for market in snap.nontrading.markets:
        title_bits = [market.market_slug]
        if market.map_number is not None:
            title_bits.append(f"карта {market.map_number}")
        outcomes = " / ".join(name for name in market.outcome_names if name)
        if outcomes:
            title_bits.append(outcomes)
        rows.append(
            IdleRow(
                key=f"sidecar:{market.condition_id}",
                title=" · ".join(title_bits),
                game=market.game,
                reason=_IDLE_REASONS.get(market.reason, market.reason),
                reason_source=market.reason_source,
                reason_age_s=(
                    None if market.reason_ts is None else max(0.0, now_s - market.reason_ts)
                ),
                starts_at=market.starts_at,
                source="sidecar",
                match_url=None,
                polymarket_url=polymarket_url(market.event_slug, market.market_slug),
                details=(f"condition {market.condition_id}", f"игра {market.game}"),
            )
        )
    for view in snap.maps:
        entry = view.entry
        if not entry.record_only and (view.status != "incomplete" or entry.condition_id is None):
            continue
        rows.append(
            IdleRow(
                key=f"archive:{entry.match_id}",
                title=map_title(entry),
                game=entry.game,
                reason=("только запись" if entry.record_only else "данные неполные"),
                reason_source="archive",
                reason_age_s=view.write_age_s,
                starts_at=None,
                source="archive",
                match_url=match_url(entry.match_id),
                polymarket_url=polymarket_url(entry.event_slug, entry.slug),
                details=(
                    f"match {entry.match_id}",
                    f"condition {entry.condition_id or '—'}",
                    *view.evidence,
                ),
            )
        )
    upcoming = [
        row
        for row in rows
        if row.source != "sidecar" or (row.starts_at is not None and row.starts_at >= now_s)
    ]
    upcoming.sort(key=lambda row: (row.starts_at is None, row.starts_at or 0.0))
    return tuple(upcoming)


def _closed_rows(
    idx: ListIndexes, payout_ts: float | None, now_s: float
) -> tuple[tuple[ClosedRow, ...], tuple[ClosedRow, ...]]:
    dated: list[ClosedRow] = []
    undated: list[ClosedRow] = []
    if payout_ts is None:
        return (), ()
    for view in idx.terminal:
        entry = view.entry
        details: list[str] = [
            f"match {entry.match_id}",
            f"condition {entry.condition_id or '—'}",
            f"дерево {entry.tree}",
            f"realized {fmt_money(entry.realized)} · imv {fmt_money(entry.imv)} · ребейт {fmt_money(entry.rebate)}",
        ]
        row = ClosedRow(
            match_id=entry.match_id,
            title=map_title(entry),
            game=entry.game,
            closed_observed_at=entry.closed_observed_at,
            closed_age_s=(
                None
                if entry.closed_observed_at is None
                else max(0.0, now_s - entry.closed_observed_at)
            ),
            net=entry.net,
            realized=entry.realized,
            imv=entry.imv,
            rebate=entry.rebate,
            fill_count=entry.fill_count,
            match_url=match_url(entry.match_id),
            details=tuple(details),
        )
        if entry.closed_observed_at is not None and entry.closed_observed_at >= payout_ts:
            dated.append(row)
        elif (
            entry.closed_observed_at is None
            and entry.last_write is not None
            and entry.last_write >= payout_ts
        ):
            undated.append(row)
    dated.sort(key=lambda r: r.closed_observed_at or 0.0, reverse=True)
    return tuple(dated), tuple(undated)


def _unsettled_lines(idx: ListIndexes, now_s: float, cid: str | None) -> tuple[UnsettledLine, ...]:
    lines: list[UnsettledLine] = []
    for row in idx.unsettled_by_cid.get(cid or "", ()):
        detail = idx.reserve_by_venue.get(row.venue_id)
        lines.append(
            UnsettledLine(
                venue_id=row.venue_id,
                core_order_id=None,
                session_id=row.session_id,
                token_id=row.token_id,
                qty=row.qty,
                remaining_notional=detail.notional if detail is not None else None,
                age_s=max(0.0, now_s - row.created_at),
                status="proven" if row.proven else "не подтверждён на venue",
                source="unsettled_buys",
            )
        )
    for detail in idx.reserve_open_by_cid.get(cid or "", ()):
        if detail.notional <= 0.0:
            continue
        lines.append(
            UnsettledLine(
                venue_id=detail.venue_id,
                core_order_id=detail.core_order_id,
                session_id=detail.session_id,
                token_id=None,
                qty=detail.remaining_qty,
                remaining_notional=detail.notional,
                age_s=None,
                status=detail.note or "open order",
                source=detail.source,
            )
        )
    return tuple(lines)


def _redeem_state(
    legs: Sequence[ResidualLeg],
    positions_state: Literal["complete", "incomplete", "error", "unknown"],
) -> str:
    redeemables = {leg.api_redeemable for leg in legs if leg.api_redeemable is not None}
    if True in redeemables:
        return "доступен redeem"
    if not legs or positions_state != "complete":
        return "статус неизвестен"
    return "ожидаем разрешения"


def _residual_legs(
    entry: ArchiveEntry,
    api_rows: Sequence[PositionEntry],
    idx: ListIndexes,
) -> tuple[ResidualLeg, ...]:
    api_by_token = {pos.asset: pos for pos in api_rows}
    legs: list[ResidualLeg] = []
    for side in _token_sides(entry):
        if side.token_id is None:
            continue
        api_pos = api_by_token.get(side.token_id)
        if api_pos is not None and api_pos.cur_price == 0.0:
            api_pos = None
        local = idx.positions.get(side.token_id)
        local_size = local.size if local is not None and local.size != 0.0 else None
        api_size = api_pos.size if api_pos is not None and api_pos.size != 0.0 else None
        if api_size is None and (local_size is None or idx.positions_state == "complete"):
            continue
        mark, mark_state = _book_mark(idx.books.get(side.token_id))
        if api_pos is not None:
            leg_mark: float | None = api_pos.cur_price
            leg_mark_state: Literal["api", "book", "book_stale", "unknown"] = "api"
        else:
            leg_mark = mark
            leg_mark_state = mark_state
        size = api_size if api_size is not None else local_size
        legs.append(
            ResidualLeg(
                token_id=side.token_id,
                outcome=f"{side.side} {side.team}" if side.team else side.side,
                api_size=api_size,
                api_price=api_pos.cur_price if api_pos is not None else None,
                api_redeemable=api_pos.redeemable if api_pos is not None else None,
                local_size=local_size,
                local_avg=local.avg_price if local is not None else None,
                mark=leg_mark,
                mark_state=leg_mark_state,
                value=None if size is None or leg_mark is None else size * leg_mark,
            )
        )
    return tuple(legs)


def _unmatched_api_rows(
    idx: ListIndexes,
    used_api: set[tuple[str | None, str]],
    now_s: float,
) -> list[ResidualRow]:
    rows: list[ResidualRow] = []
    for pos in idx.api_rows:
        if (
            (pos.condition_id, pos.asset) in used_api
            or pos.size == 0.0
            or pos.cur_price == 0.0
            or pos.redeemable is not True
        ):
            continue
        if pos.condition_id in idx.known_cids:
            continue
        rows.append(
            ResidualRow(
                key=f"api:{pos.asset}",
                match_id=None,
                title=pos.title or f"позиция {pos.asset[:12]}…",
                game=None,
                redeem_state="доступен redeem",
                legs=(
                    ResidualLeg(
                        token_id=pos.asset,
                        outcome="—",
                        api_size=pos.size,
                        api_price=pos.cur_price,
                        api_redeemable=pos.redeemable,
                        local_size=None,
                        local_avg=None,
                        mark=pos.cur_price,
                        mark_state="api",
                        value=pos.size * pos.cur_price,
                    ),
                ),
                commitments=_unsettled_lines(idx, now_s, pos.condition_id),
                positions_state=idx.positions_state,
                match_url=None,
                polymarket_url=None,
                details=(
                    f"condition {pos.condition_id or '—'}",
                    "позиция API вне каталога карт",
                ),
            )
        )
    return rows


def _residual_rows(idx: ListIndexes, now_s: float) -> tuple[ResidualRow, ...]:
    rows: list[ResidualRow] = []
    used_api: set[tuple[str | None, str]] = set()
    for view in idx.terminal:
        entry = view.entry
        cid = entry.condition_id
        api_rows = idx.api_by_cid.get(cid or "", ())
        used_api.update((pos.condition_id, pos.asset) for pos in api_rows)
        legs = _residual_legs(entry, api_rows, idx)
        commitments = _unsettled_lines(idx, now_s, cid)
        if not legs and not commitments:
            continue
        rows.append(
            ResidualRow(
                key=f"map:{entry.match_id}",
                match_id=entry.match_id,
                title=map_title(entry),
                game=entry.game,
                redeem_state=_redeem_state(legs, idx.positions_state),
                legs=legs,
                commitments=commitments,
                positions_state=idx.positions_state,
                match_url=match_url(entry.match_id),
                polymarket_url=polymarket_url(entry.event_slug, entry.slug),
                details=(
                    f"condition {cid or '—'}",
                    f"статус карты {view.status}",
                    f"positions {idx.positions_state}",
                ),
            )
        )
    rows.extend(_unmatched_api_rows(idx, used_api, now_s))
    return tuple(rows)


def build_lists(snap: HubSnapshot, now_s: float) -> ListsView:
    idx = _list_indexes(snap)
    active: list[ActiveRow] = []
    for view in snap.maps:
        if view.status not in ("live", "stale") or view.entry.record_only:
            continue
        active.append(_active_row(view, idx, snap.wallet.ok))
    payout_ts = snap.day.payout_ts if snap.day.payout_known else None
    dated, undated = _closed_rows(idx, payout_ts, now_s)
    closed_since = "выплата не найдена" if payout_ts is None else f"с {fmt_clock(payout_ts)}"
    nontrading_note: str | None = None
    if not snap.nontrading.ok:
        nontrading_note = snap.nontrading.error or "сканирование не выполнялось"
    elif snap.nontrading.missing_roots or snap.nontrading.root_errors:
        missing = ", ".join((*snap.nontrading.missing_roots, *snap.nontrading.root_errors))
        nontrading_note = f"неполный обход: {missing}"
    return ListsView(
        active=tuple(active),
        idle=_idle_rows(snap, now_s),
        closed_dated=dated,
        closed_undated=undated,
        residuals=_residual_rows(idx, now_s),
        positions_state=idx.positions_state,
        nontrading_note=nontrading_note,
        closed_since=closed_since,
    )
