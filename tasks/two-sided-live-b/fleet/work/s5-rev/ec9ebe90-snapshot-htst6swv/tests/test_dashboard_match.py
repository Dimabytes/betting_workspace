import json
import time
from dataclasses import replace
from pathlib import Path

import dashboard_fixture as fx
import pytest
from polymaker.marketdata.orderbook import BookLevel

from dashboard.balance import BalanceSnapshot
from dashboard.catalog import ArchiveEntry, MapView, SessionParams
from dashboard.game_types import (
    DecisionSlice,
    GameIdentity,
    GameSummary,
    SourceFacts,
)
from dashboard.health import EMPTY_HEALTH
from dashboard.hub_types import (
    EMPTY_DAY_SNAPSHOT,
    EMPTY_NONTRADING,
    EMPTY_SUBSCRIPTION_FACTS,
    HubSnapshot,
    LogsFacts,
    OrderFacts,
    SellOrderFacts,
    SessionFacts,
    WalletFacts,
    wallet_facts,
)
from dashboard.market_books import BookSnapshot
from dashboard.match_history import project_events, read_journal
from dashboard.match_state import (
    FillLog,
    MatchObserver,
    MatchState,
    build_match_state,
    link_session,
    resolve_sides,
)
from dashboard.match_trace import AdvisoryTrace, fold_advisory, read_advisory
from dashboard.tails import RecordTail, TailCache
from dashboard.wallet import WalletPosition, read_wallet_snapshot
from strategy.types import RestingOrder
from trader.core_persistence import OrderBinding
from trader.paths import SESSION_JOURNAL_FILENAME

NOW = time.time()
YES = "yes-t"
NO = "no-t"
CID = "0xcid"
SID = "sess-1"
ARCHIVE = Path("/x/m1")


def _entry(match_id: str = "m1", **kw: object) -> ArchiveEntry:
    base = ArchiveEntry(
        match_id=match_id,
        archive_dir=ARCHIVE,
        tree="live",
        game="dota",
        slug=f"slug-{match_id}",
        event_slug="ev",
        joined_at_utc=None,
        condition_id=CID,
        yes_token=YES,
        no_token=NO,
        yes_is_radiant=True,
        radiant="Rad",
        dire="Dir",
        map_number=1,
        outcome_0_name="Rad",
        outcome_1_name="Dir",
        finished=False,
        cleanup_proven=False,
        session_ended=False,
        has_journal=True,
        record_only=False,
        last_write=NOW - 5,
        realized=None,
        imv=None,
        rebate=None,
        net=None,
        fill_count=None,
        closed_observed_at=None,
        last_decision=None,
        params=SessionParams(0.05, 900, 5.0, 5.0, 15.0, 25.0),
    )
    return replace(base, **kw)


def _view(entry: ArchiveEntry, **kw: object) -> MapView:
    base = MapView(
        entry=entry,
        status="live",
        write_age_s=5.0,
        decision_age_s=3.0,
        feed_age_s=4.0,
        decision=None,
        evidence=("recent_writes",),
    )
    return replace(base, **kw)


def _wallet(**kw: object) -> WalletFacts:
    base = WalletFacts(
        ok=True,
        error=None,
        read_at=NOW - 1,
        funder="0xf",
        max_outbox_seq=0,
        sessions=(),
        positions=(),
        bindings=(),
        open_buys=(),
        unsettled=(),
        token_cids=(),
        notes=(),
    )
    return replace(base, **kw)


def _session(**kw: object) -> SessionFacts:
    base = SessionFacts(
        session_id=SID,
        condition_id=CID,
        game="dota",
        yes_token=YES,
        no_token=NO,
        yes_is_radiant=True,
        revision=3,
        recovery=False,
        created_at=NOW - 100,
        updated_at=NOW - 5,
        checkpoint_error=None,
        sell_only=False,
        recovery_pending=False,
        winding_down=False,
        unconfirmed=0,
        pending_ownership=0,
        has_buy_fill=True,
        held=(4.0, 0.0),
        held_cost=(2.0, 0.0),
        sells=(),
        orders=(),
    )
    return replace(base, **kw)


def _order(order_id: str, **kw: object) -> OrderFacts:
    base = OrderFacts(
        session_id=SID,
        order_id=order_id,
        token_index=0,
        token_id=YES,
        side="BUY",
        status="live",
        price=0.55,
        submitted_qty=10.0,
        filled_qty=4.0,
        remaining_qty=6.0,
        accepted=True,
        level_index=0,
        venue_id="v-1",
        binding_conflict=False,
        cancel_reason="",
        ack_reason="",
    )
    return replace(base, **kw)


def _sell(order_id: str, **kw: object) -> SellOrderFacts:
    base = SellOrderFacts(
        order_id=order_id,
        token_index=0,
        token_id=YES,
        status="pending",
        price=0.7,
        submitted_qty=5.0,
        filled_qty=0.0,
        remaining_qty=5.0,
        held_qty=8.0,
        venue_id="v-1",
        cancel_reason="",
    )
    return replace(base, **kw)


def _book(token_id: str, *, bid: float = 0.55, ask: float = 0.57, **kw: object) -> BookSnapshot:
    base = BookSnapshot(
        token_id=token_id,
        condition_id=CID,
        bids=(BookLevel(price=bid, size=10.0),),
        asks=(BookLevel(price=ask, size=10.0),),
        tick_size=0.01,
        book_hash=None,
        exchange_ts=None,
        local_ts=NOW,
        generation=1,
        initialized=True,
        ready=True,
        connected=True,
        disconnected_since=None,
        truncated=False,
    )
    return replace(base, **kw)


def _balance(**kw: object) -> BalanceSnapshot:
    base = BalanceSnapshot(
        collateral_usdc=100.0,
        funder="0xf",
        funder_mismatch=False,
        request_started_at=NOW - 2,
        response_at=NOW - 2,
        last_attempt_at=NOW - 2,
        last_error=None,
        queued=False,
        inflight=False,
        request_cutoff_seq=None,
        covered_seq=None,
        significant_seq=None,
        evidence_gaps=0,
        pending=(),
        pending_dropped=0,
    )
    return replace(base, **kw)


def _snap(**kw: object) -> HubSnapshot:
    base = HubSnapshot(
        generation=1,
        published_at=NOW,
        running=True,
        error=None,
        books=(),
        balance=_balance(),
        wallet=_wallet(),
        reserve=None,
        day=EMPTY_DAY_SNAPSHOT,
        subscriptions=EMPTY_SUBSCRIPTION_FACTS,
        maps=(),
        legacy_maps=(),
        nontrading=EMPTY_NONTRADING,
        health=EMPTY_HEALTH,
        service_logs=LogsFacts(
            service="live",
            ok=True,
            error=None,
            attempt_at=NOW,
            read_at=NOW,
            text="",
            truncated=False,
        ),
    )
    return replace(base, **kw)


def _state(
    snap: HubSnapshot | None = None,
    *,
    summary: GameSummary | None = None,
    advisory: AdvisoryTrace | None = None,
) -> MatchState:
    return build_match_state(
        snap=snap if snap is not None else _snap(),
        view=_view(_entry()),
        summary=summary,
        advisory=advisory,
        observed={},
        wedge={},
        fills=(),
        now_s=NOW,
    )


def test_wallet_publishes_all_orders(tmp_path: Path) -> None:
    fixture = fx.build(tmp_path)
    orders = (
        RestingOrder(
            order_id="c-buy",
            episode_id=1,
            token_index=0,
            side="BUY",
            price=0.5,
            submitted_qty=40.0,
            filled_qty=45.0,
            level_index=0,
            status="live",
            accepted=True,
            partially_filled=True,
            cancel_reason="",
            ack_reason="",
            placed_ns=0,
            accepted_ns=0,
        ),
        RestingOrder(
            order_id="c-sell",
            episode_id=1,
            token_index=1,
            side="SELL",
            price=0.7,
            submitted_qty=5.0,
            filled_qty=1.0,
            level_index=None,
            status="pending",
            accepted=False,
            partially_filled=False,
            cancel_reason="",
            ack_reason="",
            placed_ns=0,
            accepted_ns=0,
        ),
        RestingOrder(
            order_id="c-gone",
            episode_id=1,
            token_index=0,
            side="BUY",
            price=0.4,
            submitted_qty=5.0,
            filled_qty=0.0,
            level_index=1,
            status="gone",
            accepted=True,
            partially_filled=False,
            cancel_reason="unsettled",
            ack_reason="",
            placed_ns=0,
            accepted_ns=0,
        ),
        RestingOrder(
            order_id="c-unknown",
            episode_id=1,
            token_index=1,
            side="BUY",
            price=0.3,
            submitted_qty=5.0,
            filled_qty=0.0,
            level_index=2,
            status="unknown",
            accepted=False,
            partially_filled=False,
            cancel_reason="",
            ack_reason="",
            placed_ns=0,
            accepted_ns=0,
        ),
    )
    fx.insert_session(fixture, orders=orders, qty=8.0)
    snap = read_wallet_snapshot(fixture.wallet_db)
    facts = wallet_facts(snap, read_ok=True, error=None)
    session = facts.sessions[0]
    by_id = {order.order_id: order for order in session.orders}
    assert set(by_id) == {"c-buy", "c-sell", "c-gone", "c-unknown"}
    assert by_id["c-buy"].remaining_qty == 0.0
    assert by_id["c-sell"].side == "SELL" and by_id["c-sell"].remaining_qty == 4.0
    assert by_id["c-gone"].status == "gone"
    assert by_id["c-gone"].cancel_reason == "unsettled"
    assert by_id["c-unknown"].status == "unknown"
    assert len(session.sells) == 1 and session.sells[0].order_id == "c-sell"
    assert session.held == (8.0, 0.0)
    assert session.held_cost == (4.0, 0.0)


def test_wallet_binding_conflict(tmp_path: Path) -> None:
    fixture = fx.build(tmp_path)
    fx.insert_session(
        fixture,
        orders=(
            RestingOrder(
                order_id="c0",
                episode_id=1,
                token_index=0,
                side="BUY",
                price=0.5,
                submitted_qty=5.0,
                filled_qty=0.0,
                level_index=0,
                status="live",
                accepted=True,
                partially_filled=False,
                cancel_reason="",
                ack_reason="",
                placed_ns=0,
                accepted_ns=0,
            ),
        ),
    )
    snap = read_wallet_snapshot(fixture.wallet_db)
    bindings = (
        OrderBinding(
            session_id="sess-1",
            core_order_id="c0",
            venue_id="v-1",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=5.0,
            level_index=0,
            terminal=False,
        ),
        OrderBinding(
            session_id="sess-1",
            core_order_id="c0",
            venue_id="v-9",
            episode_id=1,
            token_index=0,
            side="BUY",
            submitted_qty=5.0,
            level_index=0,
            terminal=False,
        ),
    )
    facts = wallet_facts(replace(snap, bindings=bindings), read_ok=True, error=None)
    order = facts.sessions[0].orders[0]
    assert order.binding_conflict is True


def test_link_session_identity() -> None:
    entry = _entry()
    session = _session()
    wallet = _wallet(sessions=(session,))
    assert link_session(entry, wallet).session is session
    foreign = _session(yes_token="other")
    conflict = link_session(entry, _wallet(sessions=(foreign,)))
    assert conflict.state == "conflict" and conflict.session is None
    no_cid = link_session(_entry(condition_id=None), wallet)
    assert no_cid.state == "none"
    no_tokens = link_session(_entry(yes_token=None), wallet)
    assert no_tokens.state == "conflict"
    orient = _session(yes_is_radiant=False)
    assert link_session(entry, _wallet(sessions=(orient,))).state == "conflict"


def test_link_session_missing_notes() -> None:
    wallet = _wallet(sessions=())
    live = link_session(_entry(), wallet)
    assert live.state == "none" and "первым ордером" in live.notes[0]
    ended = link_session(_entry(session_ended=True, fill_count=0), wallet)
    assert "завершилась без ордеров" in ended.notes[0]
    final = link_session(_entry(finished=True, fill_count=None), wallet)
    assert "завершилась без ордеров" in final.notes[0]
    traded = link_session(_entry(session_ended=True, fill_count=2), wallet)
    assert "не найдена" in traded.notes[0]
    no_journal = link_session(_entry(has_journal=False), wallet)
    assert "не найдена" in no_journal.notes[0]
    record = link_session(_entry(record_only=True, has_journal=False), wallet)
    assert "без торговой сессии" in record.notes[0]


def test_resolve_sides_orientation() -> None:
    entry = _entry()
    sides = resolve_sides(entry, None, None)
    assert sides.known
    assert sides.yes.team == "Rad" and sides.yes.side_label == "Radiant"
    assert sides.no.team == "Dir" and sides.no.side_label == "Dire"
    flipped = resolve_sides(_entry(yes_is_radiant=False), None, None)
    assert flipped.yes.team == "Dir" and flipped.yes.side_label == "Dire"
    unknown = resolve_sides(_entry(yes_is_radiant=None), None, None)
    assert not unknown.known and unknown.yes.team is None
    conflicted = resolve_sides(entry, None, _session(yes_is_radiant=False))
    assert not conflicted.known and "не совпадает" in (conflicted.note or "")
    lol = resolve_sides(_entry(game="lol"), None, None)
    assert lol.yes.side_label == "Blue" and lol.no.side_label == "Red"


def _trace_event(seq: int, now_ns: int, cap: float | None) -> dict[str, object]:
    return {
        "seq": seq,
        "kind": "event",
        "now_ns": now_ns,
        "event": {
            "type": "BudgetUpdate",
            "now_ns": now_ns,
            "budget": {
                "cash_usdc": 900.0,
                "cap_room_usdc": cap,
                "account_cap_room_usdc": None,
            },
        },
    }


def _advisory_tail(tmp_path: Path, records: list[dict[str, object]]) -> RecordTail:
    path = tmp_path / "core_trace.jsonl"
    path.write_text("".join(json.dumps(rec) + "\n" for rec in records))
    tail = TailCache().read_records(path)
    assert tail is not None
    return tail


def test_advisory_budget_and_revert(tmp_path: Path) -> None:
    header: dict[str, object] = {
        "kind": "header",
        "match_id": "m1",
        "session_id": SID,
        "yes_token": YES,
        "no_token": NO,
        "opened_wall_s": 1000.0,
        "opened_now_ns": 5_000_000_000,
    }
    records: list[dict[str, object]] = [
        header,
        _trace_event(3, 6_000_000_000, 100.0),
        _trace_event(5, 8_000_000_000, 80.0),
        {"seq": 6, "kind": "revert", "to_seq": 4},
        _trace_event(7, 9_000_000_000, 60.0),
    ]
    tail = _advisory_tail(tmp_path, records)
    advisory = fold_advisory(tail, header=header)
    assert advisory.identity_ok
    assert advisory.budget is not None
    assert advisory.budget.cap_room_usdc == 60.0
    assert advisory.budget.seq == 7
    assert advisory.budget.wall_ts == pytest.approx(1004.0)


def test_advisory_truncated_and_foreign(tmp_path: Path) -> None:
    header: dict[str, object] = {"kind": "header", "match_id": "m1", "session_id": SID}
    records: list[dict[str, object]] = [
        header,
        _trace_event(3, 6_000_000_000, 100.0),
        {"seq": 4, "kind": "truncated", "reason": "size_cap"},
    ]
    advisory = fold_advisory(_advisory_tail(tmp_path, records), header=header)
    assert advisory.budget is None

    tails = TailCache()
    archive = tmp_path / "arch"
    archive.mkdir()
    (archive / "core_trace.jsonl").write_text(
        '{"kind":"header","match_id":"other","yes_token":"y","no_token":"n"}\n'
    )
    foreign = read_advisory(
        tails,
        archive,
        match_id="m1",
        session_id=SID,
        yes_token=YES,
        no_token=NO,
        trace_file="core_trace.jsonl",
    )
    assert not foreign.identity_ok
    assert foreign.budget is None


def test_match_state_position_and_marks() -> None:
    session = _session()
    snap = _snap(
        books=(_book(YES), _book(NO, bid=0.9, ask=0.4)),
        wallet=_wallet(
            sessions=(session,),
            positions=(WalletPosition(YES, 4.0, 0.5, NOW),),
        ),
    )
    state = _state(snap)
    yes_leg, no_leg = state.position.legs
    assert yes_leg.mark == pytest.approx(0.56)
    assert yes_leg.mark_state == "book"
    assert yes_leg.unrealized == pytest.approx(4.0 * (0.56 - 0.5))
    assert no_leg.mark is None and no_leg.mark_state == "unknown"
    assert state.position.realized_text == "нет сделок"
    assert state.position.closed is False
    assert "держим YES 4" in state.now.position_line
    flat = build_match_state(
        snap=_snap(wallet=_wallet()),
        view=_view(_entry(realized=-77.23, rebate=3.5, fill_count=10)),
        summary=None,
        advisory=None,
        observed={},
        wedge={},
        fills=(),
        now_s=NOW,
    )
    assert flat.position.closed
    assert flat.position.realized_text == "$-77.23"
    assert flat.position.rebate_text == "$3.50"
    bought = build_match_state(
        snap=snap,
        view=_view(_entry(realized=-2.0, fill_count=1)),
        summary=None,
        advisory=None,
        observed={},
        wedge={},
        fills=(),
        now_s=NOW,
    )
    assert bought.position.realized_text == "$+0.00"
    assert not bought.position.closed


def test_fill_log_shows_cash_before_catalog(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    journal.write_text(
        json.dumps(
            {
                "kind": "fill",
                "side": "BUY",
                "token_id": YES,
                "size": 10,
                "price": 0.4,
                "is_maker": True,
                "net_cash": -4.0,
            }
        )
        + "\n"
    )
    log = FillLog()
    first = log.read(journal)
    assert first[0].cash == pytest.approx(-4.0)
    with journal.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": "fill",
                    "side": "SELL",
                    "token_id": YES,
                    "size": 10,
                    "price": 0.5,
                    "is_maker": True,
                    "net_cash": 1.0,
                }
            )
            + "\n"
        )
    second = log.read(journal)
    assert len(second) == 2
    assert second[1].cash == pytest.approx(5.0)
    state = build_match_state(
        snap=_snap(wallet=_wallet()),
        view=_view(_entry()),
        summary=None,
        advisory=None,
        observed={},
        wedge={},
        fills=second,
        now_s=NOW,
    )
    assert state.position.closed
    assert state.position.realized_text == "$+1.00"
    assert state.fills[0].token_label == "YES Rad"
    assert state.sides_line == "YES = Rad (Radiant) · NO = Dir (Dire)"


def test_match_state_books_overlay() -> None:
    own = (
        _order("o1", side="BUY", price=0.55, remaining_qty=6.0),
        _order("o2", side="SELL", price=0.61, remaining_qty=3.0),
        _order("o3", side="SELL", price=0.70, status="pending", remaining_qty=2.0),
    )
    session = _session(orders=own)
    snap = _snap(
        books=(_book(YES), _book(NO)),
        wallet=_wallet(sessions=(session,)),
    )
    state = _state(snap)
    yes_panel = state.books[0]
    assert yes_panel.state == "live"
    bid_row = yes_panel.bids[0]
    assert bid_row.own_qty == 6.0 and "наши" in (bid_row.own_note or "")
    assert bid_row.size == 10.0
    extra = {row.price for row in yes_panel.own_extra}
    assert extra == {0.61}
    statuses = {order.order_id: order.status for order in state.orders}
    assert statuses == {"o1": "live", "o2": "live", "o3": "pending"}
    transitional = [o for o in state.orders if o.transitional]
    assert [o.order_id for o in transitional] == ["o3"]


def test_match_state_books_not_subscribed() -> None:
    session = _session(orders=(_order("o1"),))
    snap = _snap(wallet=_wallet(sessions=(session,)))
    state = _state(snap)
    assert state.books[0].state == "нет данных"
    assert state.books[0].own_extra[0].price == 0.55


def test_match_state_wallet_stale_overlay() -> None:
    session = _session(orders=(_order("o1"),))
    snap = _snap(
        books=(_book(YES), _book(NO)),
        wallet=_wallet(sessions=(session,), ok=False, read_at=NOW - 120),
    )
    state = _state(snap)
    assert state.books[0].stale_orders
    assert "live.db" in " ".join(state.notes)
    assert state.now.position_line.startswith("позиция неизвестна")


def test_match_observer_ages_and_wedge() -> None:
    observer = MatchObserver()
    orders = (
        _order("o1", status="pending"),
        _order("o2", status="live"),
    )
    observer.observe(
        identity="a:sess-1",
        generation=1,
        session=_session(orders=orders),
        stale=False,
        now_s=100.0,
    )
    observer.observe(
        identity="a:sess-1",
        generation=1,
        session=_session(orders=orders),
        stale=False,
        now_s=140.0,
    )
    ages = observer.first_seen
    assert ages[(SID, "o1", "pending")] == 100.0
    sell = _sell("o3")
    session = _session(
        orders=(_order("o3", side="SELL", status="pending", remaining_qty=5.0),),
        sells=(sell,),
    )
    observer.observe(identity="a:sess-1", generation=1, session=session, stale=False, now_s=150.0)
    assert (SID, "o1", "pending") not in observer.first_seen
    observer.observe(identity="a:sess-1", generation=1, session=session, stale=False, now_s=185.0)
    assert observer.wedge["o3"].wedged
    observer.observe(identity="a:sess-1", generation=1, session=session, stale=True, now_s=190.0)
    assert not observer.wedge
    observer.observe(
        identity="other:sess-1", generation=1, session=session, stale=False, now_s=191.0
    )
    assert not observer.wedge["o3"].wedged


def test_match_observer_generation_reset() -> None:
    observer = MatchObserver()
    session = _session(orders=(_order("o1", status="pending"),))
    observer.observe(identity="a:s", generation=1, session=session, stale=False, now_s=100.0)
    observer.observe(identity="a:s", generation=2, session=session, stale=False, now_s=200.0)
    assert observer.first_seen[(SID, "o1", "pending")] == 200.0


def test_buy_block_delta_and_window() -> None:
    identity = GameIdentity(
        archive_dir=ARCHIVE,
        match_id="m1",
        condition_id=CID,
        game="dota",
        map_number=1,
        source="grid",
        side_0_label="Radiant",
        side_1_label="Dire",
        team_0="Rad",
        team_1="Dir",
        yes_is_side_0=True,
        outcome_0_name="Rad",
        outcome_1_name="Dir",
    )

    def summary_for(
        *,
        evaluated: bool | None,
        delta: float | None,
        second: int | None,
        paused: bool,
        identity: GameIdentity = identity,
    ) -> GameSummary:
        decision = DecisionSlice(
            provenance="signal",
            second=second,
            server_timestamp=None,
            phase="in_progress",
            paused=paused,
            radiant_nw=None,
            dire_nw=None,
            radiant_nw_adv=None,
            xp_status="unknown",
            radiant_xp_adv=None,
            xp_text=None,
            deaths_radiant=None,
            deaths_dire=None,
            top=None,
            model_evaluated=evaluated,
            raw_delta=delta,
            market_radiant_prior=None,
            reason="model",
            entry_block="min_delta",
            feed_source="grid",
            feed_received_at_utc="2026-01-01T00:00:00Z",
            recorded_at_utc="2026-01-01T00:00:00Z",
            journal_path=ARCHIVE / SESSION_JOURNAL_FILENAME,
            notes=(),
        )
        return GameSummary(
            identity=identity,
            decision=decision,
            board=None,
            table=None,
            source=SourceFacts(
                continuity=None,
                complete=True,
                evidence=(),
                control=(),
                rejected=0,
                terminal=False,
                legacy_replay=False,
                comparison="unknown",
            ),
            decision_label="d",
            archive_label="a",
        )

    state = _state(summary=summary_for(evaluated=True, delta=0.012, second=100, paused=False))
    assert state.buy.delta_text == "Δ +0.012 в пользу Rad"
    flipped = _state(
        summary=summary_for(
            evaluated=True,
            delta=0.012,
            second=100,
            paused=False,
            identity=replace(identity, yes_is_side_0=False),
        )
    )
    assert flipped.buy.delta_text == "Δ +0.012 в пользу Rad"
    negative = _state(summary=summary_for(evaluated=True, delta=-0.03, second=100, paused=False))
    assert negative.buy.delta_text == "Δ -0.030 в пользу Dir"
    noise = _state(summary=summary_for(evaluated=True, delta=0.002, second=100, paused=False))
    assert noise.buy.delta_text == "Δ +0.002"
    assert "порог" in (state.buy.threshold_text or "")
    assert "800" in (state.buy.window_text or "")
    skipped = _state(summary=summary_for(evaluated=False, delta=0.012, second=100, paused=False))
    assert "не рассчитан" in skipped.buy.delta_text
    missing = _state(summary=summary_for(evaluated=True, delta=None, second=100, paused=False))
    assert "нет данных" in missing.buy.delta_text
    paused = _state(summary=summary_for(evaluated=True, delta=0.5, second=200, paused=True))
    assert "пауз" in (paused.buy.window_text or "")
    closed = _state(summary=summary_for(evaluated=True, delta=0.5, second=950, paused=False))
    assert "закрыто" in (closed.buy.window_text or "")


def test_advisory_reset_clears_budget(tmp_path: Path) -> None:
    header: dict[str, object] = {"kind": "header", "match_id": "m1", "session_id": SID}
    reset: dict[str, object] = {"kind": "reset", "now_ns": 7_000_000_000}
    records: list[dict[str, object]] = [header, _trace_event(3, 6_000_000_000, 100.0), reset]
    advisory = fold_advisory(_advisory_tail(tmp_path, records), header=header)
    assert advisory.budget is None
    records.append(_trace_event(7, 9_000_000_000, 60.0))
    advisory = fold_advisory(_advisory_tail(tmp_path, records), header=header)
    assert advisory.budget is not None
    assert advisory.budget.cap_room_usdc == 60.0 and advisory.budget.seq == 7


def test_project_events_dedup() -> None:
    records = [
        {
            "kind": "signal",
            "second": 1.0,
            "reason": "a",
            "entry_block": "none",
            "recorded_at_utc": "2026-01-01T00:00:01Z",
        },
        {
            "kind": "signal",
            "second": 2.0,
            "reason": "a",
            "entry_block": "none",
            "recorded_at_utc": "2026-01-01T00:00:02Z",
        },
        {
            "kind": "fill",
            "second": 3.0,
            "side": "BUY",
            "qty": 4.0,
            "price": 0.5,
            "token": "YES",
            "ts_utc": "2026-01-01T00:00:03Z",
        },
        {
            "kind": "signal",
            "second": 4.0,
            "reason": "b",
            "entry_block": "min_delta",
            "recorded_at_utc": "2026-01-01T00:00:04Z",
        },
        {
            "kind": "quote",
            "second": 5.0,
            "placed": [{"side": "SELL", "qty": 2.0, "price": 0.7}],
            "ts_utc": "2026-01-01T00:00:05Z",
        },
    ]
    rows = project_events(records, now_s=NOW)
    labels = [row.label for row in rows]
    assert labels == ["signal", "fill", "signal", "quote"]
    assert rows[0].initial
    assert "reason=a" in rows[0].text
    assert rows[2].text == "reason=b block=min_delta"


def test_read_journal_tail_and_full(tmp_path: Path) -> None:
    archive = tmp_path / "m"
    archive.mkdir()
    journal = archive / SESSION_JOURNAL_FILENAME
    journal.write_text(
        "".join(
            f'{{"kind":"signal","second":{i},"reason":"r","entry_block":"b"}}\n' for i in range(600)
        )
    )
    tail = read_journal(archive, SESSION_JOURNAL_FILENAME, full=False)
    assert tail.ok
    full = read_journal(archive, SESSION_JOURNAL_FILENAME, full=True)
    assert full.ok and len(full.records) == 600
