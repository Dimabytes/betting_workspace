import gzip
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from polymaker.marketdata.orderbook import BookLevel

from dashboard import diagnostics
from dashboard.catalog import ArchiveEntry, MapView, SessionParams
from dashboard.diagnostics import (
    FreshnessState,
    SellObservation,
    SellWatchState,
    SellWedgeVerdict,
    stamp_age_s,
)
from dashboard.game_types import DecisionSlice, GameIdentity, GameSummary
from dashboard.home import fmt_usd, map_title, polymarket_url
from dashboard.hub_types import HubSnapshot, OrderFacts, SessionFacts, WalletFacts
from dashboard.market_books import BookSnapshot
from dashboard.match_trace import AdvisoryTrace
from dashboard.summarize import maker_rebate, parse_utc
from dashboard.wallet import WalletPosition
from shared.utils.json_read import as_map
from shared.utils.jsonl_io import resolve_jsonl
from trader.game_profile import GAME_PROFILES

LADDER_LEVELS = 10
WALLET_STALE_S = 15.0
TRANSITIONAL_STATUSES = frozenset({"pending", "canceling", "unknown"})
LIVE_OVERLAY_STATUSES = frozenset({"live"})
SIDE0_LABELS = frozenset(profile.side_0_text.capitalize() for profile in GAME_PROFILES.values())
# ponytail: fixed deadband hides direction under ~0.5pp noise;
# upgrade path is threading policy.min_abs_delta into _delta_text
DELTA_DIRECTION_MIN = 0.005


@dataclass(frozen=True)
class SideFacts:
    side: str
    token_id: str | None
    team: str | None
    side_label: str | None
    outcome_name: str | None


@dataclass(frozen=True)
class SideMapping:
    yes: SideFacts
    no: SideFacts
    known: bool
    note: str | None


@dataclass(frozen=True)
class SessionLink:
    session: SessionFacts | None
    state: Literal["matched", "none", "conflict"]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class NowFacts:
    position_line: str
    sell_line: str
    sell_detail: str | None
    wedge: bool


@dataclass(frozen=True)
class BuyBlock:
    reason: str
    block: str
    decision_age_s: float | None
    delta_text: str
    threshold_text: str | None
    window_text: str | None
    map_limit: str
    map_details: tuple[str, ...]
    params_missing: bool


@dataclass(frozen=True)
class AgeLine:
    label: str
    age_s: float | None
    suffix: str | None


@dataclass(frozen=True)
class PositionLeg:
    side: str
    team: str | None
    side_label: str | None
    token_id: str | None
    size: float | None
    avg_price: float | None
    mark: float | None
    mark_state: Literal["book", "book_stale", "unknown"]
    unrealized: float | None
    held_qty: float | None
    held_cost: float | None


@dataclass(frozen=True)
class LadderRow:
    price: float | None
    size: float | None
    own_qty: float
    own_note: str | None


@dataclass(frozen=True)
class BookPanel:
    side: str
    token_id: str | None
    label: str
    state: str
    fresh: FreshnessState
    change_age_s: float | None
    asks: tuple[LadderRow, ...]
    bids: tuple[LadderRow, ...]
    own_extra: tuple[LadderRow, ...]
    stale_orders: bool
    note: str | None


@dataclass(frozen=True)
class OrderRow:
    order_id: str
    session_id: str
    side: str
    token_label: str
    price: float
    submitted: float
    filled: float
    remaining: float
    status: str
    accepted: bool
    observed_s: float | None
    venue: str | None
    reason: str | None
    binding_conflict: bool
    transitional: bool
    overlay: bool


@dataclass(frozen=True)
class FillRow:
    side: str
    token_id: str
    token_label: str
    size: float | None
    price: float | None
    is_maker: bool
    cash: float | None
    net_cash: float | None
    ts: float | None


@dataclass(frozen=True)
class PositionBlock:
    legs: tuple[PositionLeg, ...]
    wallet_ok: bool
    wallet_age_s: float | None
    realized_text: str
    rebate_text: str
    net_text: str | None
    closed: bool


@dataclass(frozen=True)
class MatchState:
    title: str
    match_id: str
    map_status: str
    poly_url: str | None
    sides_line: str
    orientation_note: str | None
    session: SessionLink
    now: NowFacts
    buy: BuyBlock
    ages: tuple[AgeLine, ...]
    position: PositionBlock
    books: tuple[BookPanel, ...]
    orders: tuple[OrderRow, ...]
    fills: tuple[FillRow, ...]
    details: tuple[str, ...]
    notes: tuple[str, ...]


def _side_labels(game: str | None) -> tuple[str, str]:
    profile = GAME_PROFILES.get(game or "dota", GAME_PROFILES["dota"])
    return (profile.side_0_text.capitalize(), profile.side_1_text.capitalize())


def resolve_sides(
    entry: ArchiveEntry, identity: GameIdentity | None, session: SessionFacts | None
) -> SideMapping:
    if identity is not None:
        side_0_label, side_1_label = identity.side_0_label, identity.side_1_label
        team_0, team_1 = identity.team_0, identity.team_1
        yes_is_side_0 = identity.yes_is_side_0
    else:
        side_0_label, side_1_label = _side_labels(entry.game)
        team_0, team_1 = entry.radiant, entry.dire
        yes_is_side_0 = entry.yes_is_radiant
    note: str | None = None
    if (
        session is not None
        and yes_is_side_0 is not None
        and session.yes_is_radiant != yes_is_side_0
    ):
        yes_is_side_0 = None
        note = "ориентация YES/NO сессии не совпадает с картой"
    known = yes_is_side_0 is not None
    if not known and note is None:
        note = "ориентация YES/NO неизвестна"

    def _facts(side: str, is_0: bool | None) -> SideFacts:
        token = entry.yes_token if side == "YES" else entry.no_token
        outcome = entry.outcome_0_name if side == "YES" else entry.outcome_1_name
        if is_0 is None:
            return SideFacts(side, token, None, None, outcome)
        return SideFacts(
            side,
            token,
            team_0 if is_0 else team_1,
            side_0_label if is_0 else side_1_label,
            outcome,
        )

    return SideMapping(
        yes=_facts("YES", yes_is_side_0),
        no=_facts("NO", None if yes_is_side_0 is None else not yes_is_side_0),
        known=known,
        note=note,
    )


def _unlinked_notes(entry: ArchiveEntry) -> tuple[str, ...]:
    if entry.fill_count:
        return ("сессия ядра для этой карты не найдена",)
    if entry.record_only:
        return ("карта записана без торговой сессии — ордеров не было",)
    if not entry.has_journal:
        return ("сессия ядра для этой карты не найдена",)
    if entry.session_ended or entry.finished:
        return ("карта завершилась без ордеров — сессия ядра не сохранялась",)
    return ("сессия ядра ещё не сохранялась — привязка появится с первым ордером",)


def link_session(entry: ArchiveEntry, wallet: WalletFacts) -> SessionLink:
    if entry.condition_id is None:
        return SessionLink(None, "none", ("condition_id неизвестен — сессия не привязана",))
    candidates = [s for s in wallet.sessions if s.condition_id == entry.condition_id]
    if not candidates:
        return SessionLink(None, "none", _unlinked_notes(entry))
    if entry.yes_token is None or entry.no_token is None:
        return SessionLink(
            None,
            "conflict",
            ("токены карты неизвестны — привязка сессии недоказана",),
        )
    exact = [
        s for s in candidates if s.yes_token == entry.yes_token and s.no_token == entry.no_token
    ]
    if not exact:
        return SessionLink(
            None,
            "conflict",
            (f"{len(candidates)} сессий с этим condition_id, но токены не совпадают",),
        )
    exact.sort(key=lambda s: s.updated_at or 0.0, reverse=True)
    notes: list[str] = []
    if len(exact) > 1:
        notes.append(f"{len(exact)} сессий у этой карты — показана последняя")
    sess = exact[0]
    if entry.yes_is_radiant is not None and sess.yes_is_radiant != entry.yes_is_radiant:
        return SessionLink(None, "conflict", ("ориентация YES/NO сессии не совпадает с картой",))
    return SessionLink(sess, "matched", tuple(notes))


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


def _position_legs(
    sides: SideMapping,
    positions: Mapping[str, WalletPosition],
    books: Mapping[str, BookSnapshot],
    session: SessionFacts | None,
) -> tuple[PositionLeg, ...]:
    legs: list[PositionLeg] = []
    held = session.held if session is not None else (None, None)
    held_cost = session.held_cost if session is not None else (None, None)
    for idx, side in enumerate((sides.yes, sides.no)):
        position = positions.get(side.token_id) if side.token_id else None
        book = books.get(side.token_id) if side.token_id else None
        mark, mark_state = _book_mark(book)
        size = position.size if position is not None else None
        avg = position.avg_price if position is not None else None
        unrealized = None
        if size is not None and avg is not None and mark is not None:
            unrealized = size * (mark - avg)
        legs.append(
            PositionLeg(
                side=side.side,
                team=side.team or side.outcome_name,
                side_label=side.side_label,
                token_id=side.token_id,
                size=size,
                avg_price=avg,
                mark=mark,
                mark_state=mark_state,
                unrealized=unrealized,
                held_qty=held[idx],
                held_cost=held_cost[idx],
            )
        )
    return tuple(legs)


def _same_level(order_price: float, level_price: float, tick: float | None) -> bool:
    tolerance = (tick or 0.01) * 0.5
    return abs(order_price - level_price) <= tolerance


def _book_state(book: BookSnapshot) -> str:
    if not book.connected:
        return "socket отключён"
    if not book.initialized:
        return "ждём book"
    if not book.ready:
        return "ждём book после reconnect"
    return "live"


def _book_fresh(book: BookSnapshot) -> FreshnessState:
    if not book.connected:
        return "stale"
    if not book.initialized or not book.ready:
        return "updating"
    return "fresh"


def _ladder_rows(
    levels: Sequence[BookLevel],
    live_own: Sequence[OrderFacts],
    tick: float | None,
    *,
    reverse: bool,
) -> tuple[LadderRow, ...]:
    ordered = sorted(levels, key=lambda lvl: lvl.price, reverse=reverse)[:LADDER_LEVELS]
    rows: list[LadderRow] = []
    for level in ordered:
        matching = [order for order in live_own if _same_level(order.price, level.price, tick)]
        own_qty = sum(order.remaining_qty for order in matching)
        note = None
        if own_qty > 0.0:
            own_sides = "/".join(sorted({order.side for order in matching}))
            note = f"наши {own_sides} {own_qty:g}"
        rows.append(LadderRow(level.price, level.size, own_qty, note))
    return tuple(rows)


def _extra_rows(
    live_own: Sequence[OrderFacts],
    asks: tuple[LadderRow, ...],
    bids: tuple[LadderRow, ...],
    tick: float | None,
) -> tuple[LadderRow, ...]:
    shown_prices = [row.price for row in (*asks, *bids) if row.price is not None]
    return tuple(
        LadderRow(order.price, None, order.remaining_qty, f"наши {order.side}")
        for order in live_own
        if not any(_same_level(order.price, price, tick) for price in shown_prices)
    )


def _live_own(own_orders: Sequence[OrderFacts]) -> list[OrderFacts]:
    return [
        order
        for order in own_orders
        if order.status in LIVE_OVERLAY_STATUSES and order.remaining_qty > 0.0
    ]


def _book_panel(
    side: SideFacts,
    book: BookSnapshot | None,
    own_orders: Sequence[OrderFacts],
    *,
    stale_orders: bool,
    now_s: float,
) -> BookPanel:
    team = side.team or side.outcome_name
    label = f"{side.side} · {team}" if team else side.side
    if side.side_label:
        label = f"{label} · {side.side_label}"
    live_own = _live_own(own_orders)
    if book is None:
        note = "нет текущей книги — токен не подписан"
        if live_own:
            note += "; наши live — по последнему checkpoint"
        return BookPanel(
            side.side,
            side.token_id,
            label,
            "нет данных",
            "no_data",
            None,
            (),
            (),
            _extra_rows(live_own, (), (), None),
            stale_orders,
            note,
        )
    tick = book.tick_size
    asks = _ladder_rows(book.asks, live_own, tick, reverse=False)
    bids = _ladder_rows(book.bids, live_own, tick, reverse=True)
    note = None
    if stale_orders and live_own:
        note = "наши live — последнее известное checkpoint"
    change_age = max(0.0, now_s - book.local_ts) if book.local_ts is not None else None
    return BookPanel(
        side.side,
        side.token_id,
        label,
        _book_state(book),
        _book_fresh(book),
        change_age,
        asks,
        bids,
        _extra_rows(live_own, asks, bids, tick),
        stale_orders,
        note,
    )


def _order_rows(
    session: SessionFacts | None,
    sides: SideMapping,
    observed: Mapping[tuple[str, str, str], float],
    now_s: float,
) -> tuple[OrderRow, ...]:
    if session is None:
        return ()
    labels = {sides.yes.token_id: sides.yes, sides.no.token_id: sides.no}
    rows: list[OrderRow] = []
    for order in session.orders:
        token_label = order.token_id or f"idx {order.token_index}"
        facts = labels.get(order.token_id)
        if facts is not None:
            team = facts.team or facts.outcome_name
            token_label = f"{facts.side} {team}" if team else facts.side
        first_seen = observed.get((order.session_id, order.order_id, order.status))
        reason = order.cancel_reason or order.ack_reason or None
        rows.append(
            OrderRow(
                order_id=order.order_id,
                session_id=order.session_id,
                side=order.side,
                token_label=token_label,
                price=order.price,
                submitted=order.submitted_qty,
                filled=order.filled_qty,
                remaining=order.remaining_qty,
                status=order.status,
                accepted=order.accepted,
                observed_s=(max(0.0, now_s - first_seen) if first_seen is not None else None),
                venue=order.venue_id,
                reason=reason,
                binding_conflict=order.binding_conflict,
                transitional=order.status in TRANSITIONAL_STATUSES,
                overlay=order.status in LIVE_OVERLAY_STATUSES and order.remaining_qty > 0.0,
            )
        )
    return tuple(rows)


def _now_facts(
    legs: tuple[PositionLeg, ...],
    session: SessionFacts | None,
    min_order_size: float | None,
    wedge_verdicts: Mapping[str, SellWedgeVerdict],
    wallet_ok: bool,
) -> NowFacts:
    if not wallet_ok:
        return NowFacts(
            "позиция неизвестна — live.db не читается",
            "SELL неизвестен",
            None,
            False,
        )
    parts: list[str] = []
    for leg in legs:
        if leg.size is not None and leg.size > 0.0:
            parts.append(f"{leg.side} {leg.size:g}")
    if session is not None:
        for idx, side in enumerate(("YES", "NO")):
            held = session.held[idx]
            if (
                held is not None
                and held > 0.0
                and not any(leg.side == side and leg.size for leg in legs)
            ):
                parts.append(f"{side} {held:g} (checkpoint)")
    position_line = "держим " + " · ".join(parts) if parts else "позиция не открыта"
    held = sum(leg.size or 0.0 for leg in legs)
    sells: tuple[SellObservation, ...] = ()
    if session is not None:
        held = max((q for q in session.held if q is not None), default=0.0)
        sells = tuple(sell.observation(session.session_id) for sell in session.sells)
    wedge = next((verdict for verdict in wedge_verdicts.values() if verdict.wedged), None)
    assessment = diagnostics.explain_exit(
        held_qty=held,
        min_order_size=min_order_size,
        sells=sells,
        sell_only=session.sell_only if session else None,
        recovery_pending=session.recovery_pending if session else None,
        unconfirmed=session.unconfirmed if session else 0,
        pending_ownership=session.pending_ownership if session else 0,
        wedge=wedge,
    )
    return NowFacts(
        position_line,
        assessment.label,
        assessment.detail,
        assessment.kind == "wedge",
    )


def _favored_team(delta: float, sides: SideMapping) -> str | None:
    if abs(delta) < DELTA_DIRECTION_MIN:
        return None
    radiant, dire = (
        (sides.yes, sides.no) if sides.yes.side_label in SIDE0_LABELS else (sides.no, sides.yes)
    )
    favored = radiant if delta > 0 else dire
    return favored.team or favored.side_label


def _delta_text(decision: DecisionSlice, sides: SideMapping) -> str:
    if decision.model_evaluated is False:
        return "Δ не рассчитан (модель не вызвана)"
    if decision.raw_delta is None or not math.isfinite(decision.raw_delta):
        return "Δ нет данных"
    favored = _favored_team(decision.raw_delta, sides)
    suffix = f" в пользу {favored}" if favored else ""
    return f"Δ {decision.raw_delta:+.3f}{suffix}"


def _window_text(
    decision: DecisionSlice,
    params: SessionParams | None,
    decision_age: float | None,
) -> str:
    cutoff = params.buy_cutoff_second if params else None
    if cutoff is None:
        return "окно входа неизвестно"
    if decision.second is None:
        return "игровая секунда неизвестна — окно не проверить"
    remaining = cutoff - float(decision.second)
    if remaining <= 0:
        return f"окно входа закрыто (cutoff {cutoff}с)"
    if decision.paused:
        text = f"до cutoff {remaining:.0f}с — игра на паузе, окно не тикает"
    else:
        text = f"до cutoff ≈{remaining:.0f}с по последнему решению"
    stale_s = params.entry_stale_s if params else None
    if stale_s is not None and decision_age is not None and decision_age > stale_s:
        text += " · решение устарело"
    return text


def _map_limit_text(advisory: AdvisoryTrace, now_s: float) -> str:
    if advisory.budget is None:
        return "лимит карты неизвестен"
    budget = advisory.budget
    age = max(0.0, now_s - budget.wall_ts) if budget.wall_ts is not None else None
    age_text = f", {age:.0f}с назад" if age is not None else ""
    if budget.cap_room_usdc is None:
        return f"лимит карты не ограничен (последняя запись seq {budget.seq}{age_text})"
    return (
        f"остаток лимита карты ≈${budget.cap_room_usdc:.2f} "
        f"(последняя запись seq {budget.seq}{age_text}) — текущее состояние неизвестно"
    )


def _buy_details(
    params: SessionParams | None,
    session: SessionFacts | None,
    reserve_map: tuple[tuple[str, float], ...],
) -> tuple[str, ...]:
    details: list[str] = []
    if params is not None and params.level_usdc is not None:
        details.append(f"уровень ${params.level_usdc:g} на ступень (policy из trace)")
    if session is not None:
        costs = [cost for cost in session.held_cost if cost is not None]
        if costs:
            details.append(f"удержано себестоимости ${sum(costs):.2f} (checkpoint)")
    details.extend(f"{name} ${value:.2f}" for name, value in reserve_map)
    return tuple(details)


def _buy_block(
    decision: DecisionSlice | None,
    params: SessionParams | None,
    advisory: AdvisoryTrace,
    session: SessionFacts | None,
    sides: SideMapping,
    reserve_map: tuple[tuple[str, float], ...],
    now_s: float,
) -> BuyBlock:
    if decision is None:
        return BuyBlock(
            "нет данных",
            "нет данных",
            None,
            "Δ нет данных",
            None,
            "окно входа неизвестно",
            "лимит карты неизвестен",
            (),
            True,
        )
    min_abs = params.min_abs_delta if params else None
    decision_age = stamp_age_s(now_s, decision.recorded_at_utc)
    return BuyBlock(
        reason=diagnostics.reason_label(decision.reason),
        block=diagnostics.entry_block_label(
            decision.entry_block,
            model_evaluated=decision.model_evaluated,
            raw_delta=decision.raw_delta,
            min_abs_delta=min_abs,
        ),
        decision_age_s=decision_age,
        delta_text=_delta_text(decision, sides),
        threshold_text=(
            f"порог входа |Δ| ≥ {min_abs:g}" if min_abs is not None else "порог входа неизвестен"
        ),
        window_text=_window_text(decision, params, decision_age),
        map_limit=_map_limit_text(advisory, now_s),
        map_details=_buy_details(params, session, reserve_map),
        params_missing=params is None,
    )


def _age_lines(
    decision: DecisionSlice | None,
    session: SessionFacts | None,
    book_panels: tuple[BookPanel, ...],
    now_s: float,
) -> tuple[AgeLine, ...]:
    lines: list[AgeLine] = []
    if decision is None:
        lines.append(AgeLine("фид/решение", None, "нет данных"))
        lines.append(AgeLine("core_sessions", None, "нет данных"))
        return tuple(lines)
    source = decision.feed_source or "неизвестен"
    if decision.provenance == "legacy":
        source = f"{source} (match.json)" if source != "неизвестен" else "match.json"
    lines.append(
        AgeLine(
            "фид",
            stamp_age_s(now_s, decision.feed_received_at_utc),
            f"источник: {source}",
        )
    )
    lines.append(AgeLine("решение записано", stamp_age_s(now_s, decision.recorded_at_utc), None))
    if session is None:
        lines.append(AgeLine("core_sessions", None, "сессия не привязана"))
    else:
        lines.append(
            AgeLine(
                "core_sessions",
                max(0.0, now_s - session.updated_at),
                f"revision {session.revision}",
            )
        )
    for panel in book_panels:
        lines.append(AgeLine(f"книга {panel.side} (WS дашборда)", panel.change_age_s, panel.state))
    return tuple(lines)


class MatchObserver:
    def __init__(self) -> None:
        self._identity: str | None = None
        self._generation = -1
        self._seen: dict[tuple[str, str, str], float] = {}
        self._sell_states: dict[tuple[str, str, str], SellWatchState] = {}
        self._wedge: dict[str, SellWedgeVerdict] = {}

    def observe(
        self,
        *,
        identity: str,
        generation: int,
        session: SessionFacts | None,
        stale: bool,
        now_s: float,
    ) -> None:
        if stale or identity != self._identity or generation != self._generation:
            self._seen = {}
            self._sell_states = {}
            self._wedge = {}
            self._identity = identity
            self._generation = generation
            if stale:
                return
        orders = session.orders if session is not None else ()
        current: set[tuple[str, str, str]] = set()
        for order in orders:
            key = (order.session_id, order.order_id, order.status)
            current.add(key)
            if key not in self._seen:
                self._seen[key] = now_s
        for key in list(self._seen):
            if key not in current:
                del self._seen[key]
        self._observe_sells(session, current, now_s)

    def _observe_sells(
        self,
        session: SessionFacts | None,
        current: set[tuple[str, str, str]],
        now_s: float,
    ) -> None:
        self._wedge = {}
        if session is None:
            self._sell_states = {}
            return
        for sell in session.sells:
            key = (session.session_id, sell.order_id, sell.status)
            obs = sell.observation(session.session_id)
            state = self._sell_states.get(key, SellWatchState(None, None))
            state, verdict = diagnostics.observe_sell(state, obs, now_s)
            self._sell_states[key] = state
            self._wedge[sell.order_id] = verdict
        for key in list(self._sell_states):
            if key not in current:
                del self._sell_states[key]

    def age(self, session_id: str, order_id: str, status: str, now_s: float) -> float | None:
        first = self._seen.get((session_id, order_id, status))
        if first is None:
            return None
        return max(0.0, now_s - first)

    @property
    def first_seen(self) -> Mapping[tuple[str, str, str], float]:
        return self._seen

    @property
    def wedge(self) -> Mapping[str, SellWedgeVerdict]:
        return self._wedge


def wallet_stale(snap: HubSnapshot, now_s: float) -> bool:
    if not snap.wallet.ok:
        return True
    if snap.wallet.read_at is None:
        return True
    return (now_s - snap.wallet.read_at) > WALLET_STALE_S


_RESERVE_NAMES = {
    "core": "BUY-резерв ядра",
    "command": "команды в outbox",
    "unsettled": "неучтённые покупки",
}


def _reserve_rows(
    snap: HubSnapshot,
    entry: ArchiveEntry,
    session: SessionFacts | None,
) -> tuple[tuple[str, float], ...]:
    if snap.reserve is None:
        return ()
    rows: list[tuple[str, float]] = []
    for detail in snap.reserve.details:
        if detail.condition_id is not None and detail.condition_id != entry.condition_id:
            continue
        if (
            detail.session_id is not None
            and session is not None
            and detail.session_id != session.session_id
        ):
            continue
        if detail.notional <= 0.0:
            continue
        rows.append((_RESERVE_NAMES.get(detail.source, detail.source), detail.notional))
    return tuple(rows)


def _money(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _fill_row(record: Mapping[str, object]) -> FillRow | None:
    side = record.get("side")
    token_id = record.get("token_id")
    if side not in ("BUY", "SELL") or not isinstance(token_id, str):
        return None
    size = _money(record.get("size"))
    price = _money(record.get("price"))
    cash = None
    if size is not None and price is not None:
        cash = size * price if side == "SELL" else -size * price
    maker = record.get("is_maker")
    ts_raw = record.get("ts_utc")
    stamp = parse_utc(ts_raw if isinstance(ts_raw, str) else None)
    return FillRow(
        side=side,
        token_id=token_id,
        token_label="",
        size=size,
        price=price,
        is_maker=maker is True,
        cash=cash,
        net_cash=_money(record.get("net_cash")),
        ts=None if stamp is None else stamp.timestamp(),
    )


class FillLog:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._offset = 0
        self._stamp = (0, 0)
        self._pending = ""
        self._rows: list[FillRow] = []

    def read(self, path: Path) -> tuple[FillRow, ...]:
        resolved = resolve_jsonl(path)
        if resolved.suffix == ".gz":
            return self._read_gz(resolved)
        return self._read_plain(resolved)

    def _reset(self, path: Path) -> None:
        self._path = path
        self._offset = 0
        self._stamp = (0, 0)
        self._pending = ""
        self._rows = []

    def _take(self, line: str) -> None:
        if not line.strip():
            return
        try:
            loaded = cast(object, json.loads(line))
        except json.JSONDecodeError:
            return
        record = as_map(loaded)
        if record is None or record.get("kind") not in ("fill", "late_fill"):
            return
        row = _fill_row(record)
        if row is None:
            return
        self._rows.append(row)

    def _read_plain(self, path: Path) -> tuple[FillRow, ...]:
        try:
            size = path.stat().st_size
        except OSError:
            if self._path != path:
                self._reset(path)
            return tuple(self._rows)
        if self._path != path or size < self._offset:
            self._reset(path)
        if size == self._offset:
            return tuple(self._rows)
        with path.open("rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read(size - self._offset)
        self._offset = size
        text = self._pending + chunk.decode("utf-8", errors="replace")
        if text.endswith("\n"):
            self._pending = ""
        else:
            cut = text.rfind("\n")
            self._pending = text if cut < 0 else text[cut + 1 :]
            text = "" if cut < 0 else text[: cut + 1]
        for line in text.split("\n"):
            self._take(line)
        return tuple(self._rows)

    def _read_gz(self, path: Path) -> tuple[FillRow, ...]:
        try:
            stat = path.stat()
        except OSError:
            if self._path != path:
                self._reset(path)
            return tuple(self._rows)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._path == path and self._stamp == stamp:
            return tuple(self._rows)
        self._reset(path)
        self._stamp = stamp
        try:
            text = path.read_bytes()
        except OSError:
            return ()
        try:
            decoded = gzip.decompress(text).decode("utf-8", errors="replace")
        except (OSError, EOFError):
            return ()
        for line in decoded.split("\n"):
            self._take(line)
        return tuple(self._rows)


def _labeled_fills(fills: tuple[FillRow, ...], sides: SideMapping) -> tuple[FillRow, ...]:
    labels = {
        sides.yes.token_id: sides.yes,
        sides.no.token_id: sides.no,
    }
    labeled: list[FillRow] = []
    for row in fills:
        facts = labels.get(row.token_id)
        if facts is None:
            label = row.token_id[:6]
        else:
            team = facts.team or facts.outcome_name or ""
            label = f"{facts.side} {team}".strip()
        labeled.append(replace(row, token_label=label))
    return tuple(labeled)


def _fill_money(fills: tuple[FillRow, ...]) -> tuple[float | None, str | None]:
    if not fills:
        return None, None
    net_cash = fills[-1].net_cash
    rebate = 0.0
    priced = False
    for row in fills:
        if row.price is None or row.size is None:
            continue
        priced = True
        rebate += maker_rebate(row.price, row.size, row.is_maker)
    rebate_text = f"${rebate:.2f}" if priced else None
    return net_cash, rebate_text


def _open_cost(legs: tuple[PositionLeg, ...], wallet_ok: bool) -> float | None:
    if not wallet_ok:
        return None
    return sum(
        leg.size * leg.avg_price
        for leg in legs
        if leg.size is not None and leg.avg_price is not None
    )


def build_match_state(
    *,
    snap: HubSnapshot,
    view: MapView,
    summary: GameSummary | None,
    advisory: AdvisoryTrace | None,
    observed: Mapping[tuple[str, str, str], float],
    wedge: Mapping[str, SellWedgeVerdict],
    fills: tuple[FillRow, ...],
    now_s: float,
) -> MatchState:
    entry = view.entry
    identity = summary.identity if summary is not None else None
    session_link = link_session(entry, snap.wallet)
    session = session_link.session
    sides = resolve_sides(entry, identity, session)
    books = {book.token_id: book for book in snap.books}
    positions = {pos.token_id: pos for pos in snap.wallet.positions}
    wallet_age = max(0.0, now_s - snap.wallet.read_at) if snap.wallet.read_at is not None else None
    stale = wallet_stale(snap, now_s)
    legs = _position_legs(sides, positions, books, session)
    own_orders = session.orders if session is not None else ()
    min_order = entry.params.min_order_size if entry.params else None
    now_facts = _now_facts(legs, session, min_order, wedge, snap.wallet.ok)
    panels = (
        _book_panel(
            sides.yes,
            books.get(sides.yes.token_id) if sides.yes.token_id else None,
            tuple(o for o in own_orders if o.token_id == sides.yes.token_id),
            stale_orders=stale,
            now_s=now_s,
        ),
        _book_panel(
            sides.no,
            books.get(sides.no.token_id) if sides.no.token_id else None,
            tuple(o for o in own_orders if o.token_id == sides.no.token_id),
            stale_orders=stale,
            now_s=now_s,
        ),
    )
    reserve_map = _reserve_rows(snap, entry, session)
    decision = summary.decision if summary is not None else None
    buy = _buy_block(
        decision,
        entry.params,
        advisory if advisory is not None else AdvisoryTrace(None, False, True, None, ()),
        session,
        sides,
        tuple(reserve_map),
        now_s,
    )
    ages = _age_lines(decision, session, panels, now_s)
    order_rows = _order_rows(session, sides, observed, now_s)
    labeled = _labeled_fills(fills, sides)
    fill_net_cash, fill_rebate = _fill_money(labeled)
    net_cash = fill_net_cash if fill_net_cash is not None else entry.realized
    open_cost = _open_cost(legs, snap.wallet.ok)
    if net_cash is None:
        realized_text = "нет сделок" if not entry.fill_count else "нет данных"
    elif open_cost is None:
        realized_text = "нет данных"
    else:
        realized_text = fmt_usd(net_cash + open_cost, signed=True)
    open_size = any((leg.size or 0.0) > 0.0 or (leg.held_qty or 0.0) > 0.0 for leg in legs)
    if fill_rebate is not None:
        rebate_text = fill_rebate
    else:
        rebate_text = f"${entry.rebate:.2f}" if entry.rebate is not None else "нет данных"
    net_text = f"${entry.net:.2f}" if entry.net is not None else None
    if sides.known:
        sides_line = f"YES = {sides.yes.team or '—'} ({sides.yes.side_label}) · NO = {sides.no.team or '—'} ({sides.no.side_label})"
    else:
        sides_line = "YES/NO ↔ стороны неизвестны"
    details = (
        f"match_id {entry.match_id}",
        f"condition_id {entry.condition_id or '—'}",
        f"archive {entry.archive_dir}",
        f"yes_token {entry.yes_token or '—'}",
        f"no_token {entry.no_token or '—'}",
        f"session {session.session_id if session else '—'}",
        f"feed {summary.identity.source if summary and summary.identity.source else '—'}",
    )
    notes = session_link.notes
    if advisory is not None and advisory.identity_ok is False:
        notes = notes + advisory.notes
    if entry.params is None:
        notes = (*notes, "параметры сессии не сохранены — порог/лимит неизвестны")
    if stale:
        notes = (
            *notes,
            "live.db читался давно или с ошибкой — ордера и позиция могут устареть",
        )
    title = map_title(entry)
    prior = decision.market_radiant_prior if decision is not None else None
    if prior is not None and entry.radiant and entry.dire:
        teams = f"{entry.radiant} ({prior * 100:.0f}%) vs {entry.dire} ({(1.0 - prior) * 100:.0f}%)"
        title = f"{teams} · карта {entry.map_number}" if entry.map_number is not None else teams
    return MatchState(
        title=title,
        match_id=entry.match_id,
        map_status=view.status,
        poly_url=polymarket_url(entry.event_slug, entry.slug),
        sides_line=sides_line,
        orientation_note=sides.note,
        session=session_link,
        now=now_facts,
        buy=buy,
        ages=ages,
        position=PositionBlock(
            legs,
            snap.wallet.ok,
            wallet_age,
            realized_text,
            rebate_text,
            net_text,
            closed=not open_size and net_cash is not None,
        ),
        books=panels,
        orders=order_rows,
        fills=labeled,
        details=details,
        notes=notes,
    )
