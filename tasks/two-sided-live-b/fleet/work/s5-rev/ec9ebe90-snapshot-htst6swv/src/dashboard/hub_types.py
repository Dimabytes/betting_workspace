from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from dashboard import reserve, summarize
from dashboard.balance import BalanceSnapshot
from dashboard.catalog import CatalogSnapshot, MapView, classify_entry
from dashboard.diagnostics import SellObservation
from dashboard.health import HealthFacts
from dashboard.logs import NontradingResult, ServiceLogs, SkippedMarket
from dashboard.market_books import BookSnapshot, DesiredMarket
from dashboard.reserve import ReserveReport
from dashboard.summarize import (
    DayFold,
    DayResult,
    FetchJson,
    PositionsResult,
    RebateAccrual,
)
from dashboard.tails import TailView
from dashboard.wallet import (
    SessionSnapshot,
    TokenCid,
    WalletPosition,
    WalletSnapshot,
)
from trader.core_persistence import (
    UNSETTLED_CANCEL_REASON,
    CoreCommand,
    OrderBinding,
    UnsettledBuy,
)
from trader.paths import SESSION_JOURNAL_FILENAME

TAIL_TRACK_LIMIT = 64


@dataclass(frozen=True)
class SellOrderFacts:
    order_id: str
    token_index: int
    token_id: str | None
    status: str
    price: float
    submitted_qty: float
    filled_qty: float
    remaining_qty: float
    held_qty: float
    venue_id: str | None
    cancel_reason: str

    def observation(self, session_key: str) -> SellObservation:
        return SellObservation(
            session_key=session_key,
            token_id=self.token_id or "?",
            order_id=self.order_id,
            status=self.status,
            price=self.price,
            remaining_qty=self.remaining_qty,
            venue_id=self.venue_id,
            held_qty=self.held_qty,
        )


@dataclass(frozen=True)
class OrderFacts:
    session_id: str
    order_id: str
    token_index: int
    token_id: str | None
    side: str
    status: str
    price: float
    submitted_qty: float
    filled_qty: float
    remaining_qty: float
    accepted: bool
    level_index: int | None
    venue_id: str | None
    binding_conflict: bool
    cancel_reason: str
    ack_reason: str


@dataclass(frozen=True)
class SessionFacts:
    session_id: str
    condition_id: str
    game: str
    yes_token: str
    no_token: str
    yes_is_radiant: bool
    revision: int
    recovery: bool
    created_at: float
    updated_at: float
    checkpoint_error: str | None
    sell_only: bool | None
    recovery_pending: bool | None
    winding_down: bool | None
    unconfirmed: int
    pending_ownership: int
    has_buy_fill: bool | None
    held: tuple[float | None, float | None]
    held_cost: tuple[float | None, float | None]
    sells: tuple[SellOrderFacts, ...]
    orders: tuple[OrderFacts, ...]


@dataclass(frozen=True)
class WalletFacts:
    ok: bool
    error: str | None
    read_at: float | None
    funder: str | None
    max_outbox_seq: int
    sessions: tuple[SessionFacts, ...]
    positions: tuple[WalletPosition, ...]
    bindings: tuple[OrderBinding, ...]
    open_buys: tuple[CoreCommand, ...]
    unsettled: tuple[UnsettledBuy, ...]
    token_cids: tuple[TokenCid, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class DaySnapshot:
    day: str | None
    fold: DayFold | None
    fold_at: float | None
    complete: bool
    inflight: bool
    attempt_started_at: float | None
    attempt_at: float | None
    error: str | None
    positions: PositionsResult | None
    positions_candidate: PositionsResult | None
    payout_ts: float | None
    payout_known: bool
    accrual_per_game: Mapping[str, float]
    accrual_total: float | None
    accrual_fills: int | None
    accrual_matches: int | None
    accrual_at: float | None
    accrual_error: str | None


EMPTY_DAY_SNAPSHOT = DaySnapshot(
    day=None,
    fold=None,
    fold_at=None,
    complete=False,
    inflight=False,
    attempt_started_at=None,
    attempt_at=None,
    error=None,
    positions=None,
    positions_candidate=None,
    payout_ts=None,
    payout_known=False,
    accrual_per_game=MappingProxyType({}),
    accrual_total=None,
    accrual_fills=None,
    accrual_matches=None,
    accrual_at=None,
    accrual_error=None,
)


@dataclass(frozen=True)
class DayState:
    result: DayResult | None
    snapshot: DaySnapshot


EMPTY_DAY_STATE = DayState(result=None, snapshot=EMPTY_DAY_SNAPSHOT)


@dataclass(frozen=True)
class SubscriptionFacts:
    catalog_built_at: float | None
    applied_at: float | None
    markets: tuple[DesiredMarket, ...]
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]
    error: str | None


EMPTY_SUBSCRIPTION_FACTS = SubscriptionFacts(
    catalog_built_at=None,
    applied_at=None,
    markets=(),
    missing=(),
    conflicts=(),
    error=None,
)


@dataclass(frozen=True)
class LogsFacts:
    service: str
    ok: bool
    error: str | None
    attempt_at: float | None
    read_at: float | None
    text: str
    truncated: bool


@dataclass(frozen=True)
class NontradingFacts:
    ok: bool
    error: str | None
    attempt_at: float | None
    scanned_at: float | None
    markets: tuple[SkippedMarket, ...]
    invalid_sidecars: int
    missing_roots: tuple[str, ...]
    root_errors: tuple[str, ...]


EMPTY_NONTRADING = NontradingFacts(
    ok=False,
    error=None,
    attempt_at=None,
    scanned_at=None,
    markets=(),
    invalid_sidecars=0,
    missing_roots=(),
    root_errors=(),
)


@dataclass(frozen=True)
class HubSnapshot:
    generation: int
    published_at: float
    running: bool
    error: str | None
    books: tuple[BookSnapshot, ...]
    balance: BalanceSnapshot
    wallet: WalletFacts
    reserve: ReserveReport | None
    day: DaySnapshot
    subscriptions: SubscriptionFacts
    maps: tuple[MapView, ...]
    legacy_maps: tuple[MapView, ...]
    nontrading: NontradingFacts
    health: HealthFacts
    service_logs: LogsFacts


@dataclass(frozen=True)
class DesiredSubs:
    markets: tuple[DesiredMarket, ...]
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class DayJob:
    funder: str
    generation: int
    started_at: float
    finished_at: float
    day: DayResult
    payout_known: bool
    payout_ts: float | None
    accrual: RebateAccrual | None
    accrual_error: str | None


def _order_facts(
    sess: SessionSnapshot,
    venues: Mapping[tuple[str, str], str | None],
    conflicts: frozenset[tuple[str, str]],
) -> tuple[OrderFacts, ...]:
    checkpoint = sess.checkpoint
    if checkpoint is None:
        return ()
    tokens = (sess.yes_token, sess.no_token)
    out: list[OrderFacts] = []
    for order in checkpoint.orders:
        idx = order.token_index
        key = (sess.session_id, order.order_id)
        status = order.status
        if status == "unknown" and order.cancel_reason == UNSETTLED_CANCEL_REASON:
            status = "gone"
        out.append(
            OrderFacts(
                session_id=sess.session_id,
                order_id=order.order_id,
                token_index=idx,
                token_id=tokens[idx] if 0 <= idx < len(tokens) else None,
                side=order.side,
                status=status,
                price=order.price,
                submitted_qty=order.submitted_qty,
                filled_qty=order.filled_qty,
                remaining_qty=max(0.0, order.submitted_qty - order.filled_qty),
                accepted=order.accepted,
                level_index=order.level_index,
                venue_id=venues.get(key),
                binding_conflict=key in conflicts,
                cancel_reason=order.cancel_reason,
                ack_reason=order.ack_reason,
            )
        )
    return tuple(out)


def _session_facts(snap: WalletSnapshot) -> tuple[SessionFacts, ...]:
    venues: dict[tuple[str, str], str | None] = {}
    conflicts: set[tuple[str, str]] = set()
    for binding in snap.bindings:
        key = (binding.session_id, binding.core_order_id)
        seen = venues.get(key)
        if seen is not None and binding.venue_id is not None and seen != binding.venue_id:
            conflicts.add(key)
        if key not in venues or not binding.terminal:
            venues[key] = binding.venue_id
    out: list[SessionFacts] = []
    for sess in snap.sessions:
        checkpoint = sess.checkpoint
        if checkpoint is None:
            sells: tuple[SellOrderFacts, ...] = ()
            orders: tuple[OrderFacts, ...] = ()
            held: tuple[float | None, float | None] = (None, None)
            held_cost: tuple[float | None, float | None] = (None, None)
            sell_only: bool | None = None
            recovery_pending: bool | None = None
            winding_down: bool | None = None
            unconfirmed = 0
            pending_ownership = 0
            has_buy_fill: bool | None = None
        else:
            orders = _order_facts(sess, venues, frozenset(conflicts))
            inventory = checkpoint.inventory
            sells = tuple(
                SellOrderFacts(
                    order_id=order.order_id,
                    token_index=order.token_index,
                    token_id=order.token_id,
                    status=order.status,
                    price=order.price,
                    submitted_qty=order.submitted_qty,
                    filled_qty=order.filled_qty,
                    remaining_qty=order.remaining_qty,
                    held_qty=(
                        inventory[order.token_index].qty
                        if 0 <= order.token_index < len(inventory)
                        else 0.0
                    ),
                    venue_id=order.venue_id,
                    cancel_reason=order.cancel_reason,
                )
                for order in orders
                if order.side == "SELL" and order.remaining_qty > 0.0
            )
            held = (inventory[0].qty, inventory[1].qty)
            held_cost = (inventory[0].cost_basis, inventory[1].cost_basis)
            sell_only = checkpoint.sell_only
            recovery_pending = checkpoint.recovery_pending
            winding_down = checkpoint.winding_down
            unconfirmed = len(checkpoint.unconfirmed_keys)
            pending_ownership = len(checkpoint.pending_ownership)
            has_buy_fill = checkpoint.has_buy_fill
        out.append(
            SessionFacts(
                session_id=sess.session_id,
                condition_id=sess.condition_id,
                game=sess.game,
                yes_token=sess.yes_token,
                no_token=sess.no_token,
                yes_is_radiant=sess.yes_is_radiant,
                revision=sess.revision,
                recovery=sess.recovery,
                created_at=sess.created_at,
                updated_at=sess.updated_at,
                checkpoint_error=sess.checkpoint_error,
                sell_only=sell_only,
                recovery_pending=recovery_pending,
                winding_down=winding_down,
                unconfirmed=unconfirmed,
                pending_ownership=pending_ownership,
                has_buy_fill=has_buy_fill,
                held=held,
                held_cost=held_cost,
                sells=sells,
                orders=orders,
            )
        )
    return tuple(out)


def wallet_facts(snap: WalletSnapshot | None, *, read_ok: bool, error: str | None) -> WalletFacts:
    if snap is None:
        return WalletFacts(
            ok=False,
            error=error or "no wallet read yet",
            read_at=None,
            funder=None,
            max_outbox_seq=0,
            sessions=(),
            positions=(),
            bindings=(),
            open_buys=(),
            unsettled=(),
            token_cids=(),
            notes=(),
        )
    notes = snap.notes + (() if error is None else (f"latest read: {error}",))
    return WalletFacts(
        ok=read_ok,
        error=error,
        read_at=snap.read_at,
        funder=snap.funder,
        max_outbox_seq=snap.max_outbox_seq,
        sessions=_session_facts(snap),
        positions=snap.positions,
        bindings=snap.bindings,
        open_buys=snap.open_buy_commands,
        unsettled=snap.unsettled_buys,
        token_cids=snap.token_cids,
        notes=notes,
    )


def tail_paths(snap: CatalogSnapshot | None) -> tuple[Path, ...]:
    if snap is None:
        return ()
    candidates = [
        entry
        for entry in snap.entries
        if not entry.finished and not entry.cleanup_proven and not entry.session_ended
    ]
    candidates.sort(key=lambda entry: -(entry.last_write or 0.0))
    return tuple(
        entry.archive_dir / SESSION_JOURNAL_FILENAME for entry in candidates[:TAIL_TRACK_LIMIT]
    )


def classify_map_views(
    snap: CatalogSnapshot | None,
    tail_views: Mapping[Path, TailView],
    *,
    now_wall: float,
    run_started_at: float | None,
) -> tuple[MapView, ...]:
    if snap is None:
        return ()
    return tuple(
        classify_entry(
            entry,
            now_wall=now_wall,
            tail=tail_views.get(entry.archive_dir / SESSION_JOURNAL_FILENAME),
            run_started_at=run_started_at,
        )
        for entry in snap.entries
    )


def reserve_snapshot(
    snap: WalletSnapshot | None,
    map_views: tuple[MapView, ...],
    collateral: float | None,
    run_started_at: float | None,
) -> ReserveReport | None:
    if snap is None:
        return None
    finished = frozenset(
        view.entry.condition_id
        for view in map_views
        if view.status in ("final", "terminal") and view.entry.condition_id is not None
    )
    catalog_cids = frozenset(
        view.entry.condition_id for view in map_views if view.entry.condition_id is not None
    )
    return reserve.compute_reserve(
        snap,
        finished_cids=finished,
        catalog_cids=catalog_cids,
        run_started_at=run_started_at,
        collateral_usdc=collateral,
    )


def desired_subscriptions(
    map_views: tuple[MapView, ...], token_cids: tuple[TokenCid, ...]
) -> DesiredSubs:
    token_cid = {row.token_id: row.condition_id for row in token_cids}
    markets: list[DesiredMarket] = []
    missing: list[str] = []
    conflicts: list[str] = []
    for view in map_views:
        if view.status != "live":
            continue
        entry = view.entry
        cid = entry.condition_id
        yes = entry.yes_token
        no = entry.no_token
        if cid is None or yes is None or no is None or yes == no:
            missing.append(entry.match_id)
            continue
        bad = [token for token in (yes, no) if token_cid.get(token) not in (None, cid)]
        if bad:
            conflicts.append(entry.match_id)
            continue
        markets.append(DesiredMarket(cid, (yes, no)))
    return DesiredSubs(
        markets=tuple(markets),
        missing=tuple(missing),
        conflicts=tuple(conflicts),
    )


def run_day_job(
    *,
    funder: str,
    generation: int,
    trees: tuple[tuple[str, Path], ...],
    fetch: FetchJson,
    wall: Callable[[], float],
) -> DayJob:
    started_at = wall()
    day = summarize.fetch_day(funder, now=started_at, fetch=fetch, wall=wall)
    payout_known = False
    payout_ts: float | None = None
    accrual: RebateAccrual | None = None
    accrual_error: str | None = None
    activity = day.activity
    if activity is None:
        accrual_error = day.error or "no activity result"
        day = replace(day, fold=None, complete=False)
    elif not activity.payout_search_complete:
        accrual_error = f"payout search incomplete: {activity.stop_reason}"
        day = replace(day, fold=None, complete=False)
    else:
        payout_known = True
        payout_ts = activity.newest_payout
        if payout_ts is None:
            accrual_error = "no known rebate payout"
            day = replace(day, fold=None)
        else:
            positions = () if day.positions is None else day.positions.positions
            day = replace(
                day,
                fold=summarize.fold_after_payout(activity.entries, positions, payout_ts),
            )
            try:
                accrual = summarize.accrued_rebate_since(
                    payout_ts, matches=summarize.match_dirs(trees)
                )
            except Exception as exc:
                accrual_error = type(exc).__name__
    return DayJob(
        funder=funder,
        generation=generation,
        started_at=started_at,
        finished_at=wall(),
        day=day,
        payout_known=payout_known,
        payout_ts=payout_ts,
        accrual=accrual,
        accrual_error=accrual_error,
    )


def merge_day_state(
    prev: DayState, job: DayJob, *, generation: int, funder: str | None
) -> DayState:
    if job.generation != generation or funder is None or job.funder != funder:
        if not prev.snapshot.inflight:
            return prev
        return DayState(result=prev.result, snapshot=replace(prev.snapshot, inflight=False))
    result = summarize.publish_day(prev.result, job.day)
    snapshot = prev.snapshot
    positions = job.day.positions
    if positions is not None and positions.traversal_complete:
        kept_positions: PositionsResult | None = positions
    else:
        kept_positions = snapshot.positions
    if positions is not None:
        kept_candidate: PositionsResult | None = positions
    else:
        kept_candidate = snapshot.positions_candidate
    payout_known = snapshot.payout_known or job.payout_known
    payout_ts = job.payout_ts if job.payout_known else snapshot.payout_ts
    if job.accrual is not None:
        per_game: Mapping[str, float] = MappingProxyType(dict(job.accrual.per_game))
        accrual_total: float | None = job.accrual.total
        accrual_fills: int | None = job.accrual.fills
        accrual_matches: int | None = job.accrual.matches
        accrual_at = job.finished_at
        accrual_error = None
    else:
        per_game = snapshot.accrual_per_game
        accrual_total = snapshot.accrual_total
        accrual_fills = snapshot.accrual_fills
        accrual_matches = snapshot.accrual_matches
        accrual_at = snapshot.accrual_at
        accrual_error = (
            job.accrual_error if job.accrual_error is not None else snapshot.accrual_error
        )
    return DayState(
        result=result,
        snapshot=DaySnapshot(
            day=result.day,
            fold=result.fold,
            fold_at=result.fetched_at,
            complete=result.complete,
            inflight=False,
            attempt_started_at=job.started_at,
            attempt_at=job.finished_at,
            error=job.day.error,
            positions=kept_positions,
            positions_candidate=kept_candidate,
            payout_ts=payout_ts,
            payout_known=payout_known,
            accrual_per_game=per_game,
            accrual_total=accrual_total,
            accrual_fills=accrual_fills,
            accrual_matches=accrual_matches,
            accrual_at=accrual_at,
            accrual_error=accrual_error,
        ),
    )


def mark_day_inflight(prev: DayState, *, started_at: float) -> DayState:
    return DayState(
        result=prev.result,
        snapshot=replace(prev.snapshot, inflight=True, attempt_started_at=started_at),
    )


def fail_day_state(prev: DayState, error: str, *, attempt_at: float | None) -> DayState:
    return DayState(
        result=prev.result,
        snapshot=replace(
            prev.snapshot,
            inflight=False,
            attempt_at=prev.snapshot.attempt_at if attempt_at is None else attempt_at,
            error=error,
        ),
    )


def merge_service_logs(prev: LogsFacts, result: ServiceLogs, *, attempt_at: float) -> LogsFacts:
    if result.ok:
        return replace(
            prev,
            ok=True,
            error=None,
            attempt_at=attempt_at,
            read_at=result.read_at,
            text=result.text,
            truncated=result.truncated,
        )
    return replace(prev, ok=False, error=result.error, attempt_at=attempt_at)


def fail_service_logs(prev: LogsFacts, error: str, *, attempt_at: float) -> LogsFacts:
    return replace(prev, ok=False, error=error, attempt_at=attempt_at)


def merge_nontrading(
    result: NontradingResult, *, attempt_at: float, root_errors: tuple[str, ...]
) -> NontradingFacts:
    return NontradingFacts(
        ok=True,
        error=None,
        attempt_at=attempt_at,
        scanned_at=result.scanned_at,
        markets=result.markets,
        invalid_sidecars=result.invalid_sidecars,
        missing_roots=result.missing_roots,
        root_errors=root_errors,
    )


def fail_nontrading(
    prev: NontradingFacts,
    error: str,
    *,
    attempt_at: float,
    root_errors: tuple[str, ...] = (),
) -> NontradingFacts:
    return replace(prev, ok=False, error=error, attempt_at=attempt_at, root_errors=root_errors)


def merge_health(result: HealthFacts, *, attempt_at: float) -> HealthFacts:
    return replace(result, attempt_at=attempt_at)


def fail_health(prev: HealthFacts, error: str, *, attempt_at: float) -> HealthFacts:
    return replace(prev, ok=False, error=error, attempt_at=attempt_at)
