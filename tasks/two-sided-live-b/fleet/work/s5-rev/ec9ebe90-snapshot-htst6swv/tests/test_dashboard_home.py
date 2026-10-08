import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from polymaker.marketdata.orderbook import BookLevel

from dashboard import diagnostics, summarize
from dashboard.balance import BalanceSnapshot
from dashboard.catalog import ArchiveEntry, DecisionFacts, MapView, SessionParams
from dashboard.health import EMPTY_HEALTH
from dashboard.home import find_match, fmt_clock
from dashboard.home_diag import SellWatcher, build_diagnostics
from dashboard.home_lists import build_lists
from dashboard.home_strip import build_strip
from dashboard.hub_types import (
    EMPTY_DAY_SNAPSHOT,
    EMPTY_NONTRADING,
    EMPTY_SUBSCRIPTION_FACTS,
    HubSnapshot,
    LogsFacts,
    NontradingFacts,
    SellOrderFacts,
    SessionFacts,
    WalletFacts,
)
from dashboard.logs import SkippedMarket
from dashboard.market_books import BookSnapshot
from dashboard.reserve import ReserveDetail, ReserveReport
from dashboard.summarize import (
    ActivityEntry,
    DayFold,
    PositionEntry,
    PositionsResult,
    fold_after_payout,
)
from dashboard.wallet import TokenCid, WalletPosition
from trader.core_persistence import UnsettledBuy

NOW = time.time()
YES = "yes-t"
NO = "no-t"
CID = "0xcid"


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


def _wallet(**kw: object) -> WalletFacts:
    base = WalletFacts(
        ok=True,
        error=None,
        read_at=NOW - 1,
        funder="0xf",
        max_outbox_seq=3,
        sessions=(),
        positions=(),
        bindings=(),
        open_buys=(),
        unsettled=(),
        token_cids=(),
        notes=(),
    )
    return replace(base, **kw)


def _logs() -> LogsFacts:
    return LogsFacts(
        service="live",
        ok=True,
        error=None,
        attempt_at=NOW - 5,
        read_at=NOW - 5,
        text="",
        truncated=False,
    )


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
        service_logs=_logs(),
    )
    return replace(base, **kw)


def _decision(**kw: object) -> DecisionFacts:
    base = DecisionFacts(
        second=120.0,
        snapshot_second=118.0,
        phase="playing",
        paused=False,
        reason="min_delta",
        entry_block="min_delta",
        model_evaluated=True,
        raw_delta=0.02,
        feed_source="grid",
        recorded_at_utc="2026-01-01T00:00:00Z",
        feed_received_at_utc="2026-01-01T00:00:00Z",
        path=Path("/x/session.jsonl"),
    )
    return replace(base, **kw)


def _entry(match_id: str, **kw: object) -> ArchiveEntry:
    base = ArchiveEntry(
        match_id=match_id,
        archive_dir=Path("/x") / match_id,
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
        params=None,
    )
    return replace(base, **kw)


def _view(entry: ArchiveEntry, **kw: object) -> MapView:
    base = MapView(
        entry=entry,
        status="live",
        write_age_s=5.0,
        decision_age_s=3.0,
        feed_age_s=4.0,
        decision=_decision(),
        evidence=("recent_writes",),
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


def _session(**kw: object) -> SessionFacts:
    base = SessionFacts(
        session_id="s1",
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


def _positions(*entries: PositionEntry, complete: bool = True) -> PositionsResult:
    return PositionsResult(
        positions=tuple(entries),
        as_of=NOW - 30,
        started_at=NOW - 31,
        completed_at=NOW - 30,
        pages=1,
        traversal_complete=complete,
        stop_reason="exhausted",
        error=None,
    )


def _berlin_today() -> str:
    return datetime.fromtimestamp(NOW, summarize.BERLIN).date().isoformat()


def test_active_row_marks_and_reason() -> None:
    entry = _entry("m1", params=SessionParams(0.05, 900, 5.0, 5.0, 15.0, 25.0))
    snap = _snap(
        maps=(_view(entry),),
        books=(_book(YES), _book(NO)),
        wallet=_wallet(positions=(WalletPosition(YES, 4.0, 0.5, NOW),)),
    )
    lists = build_lists(snap, NOW)
    assert len(lists.active) == 1
    row = lists.active[0]
    assert row.title == "Rad vs Dir · карта 1"
    assert row.second == 120.0
    leg = row.positions[0]
    assert leg.mark == 0.56
    assert leg.mark_state == "book"
    assert leg.unrealized == 4.0 * (0.56 - 0.5)
    assert row.unrealized_total == leg.unrealized
    assert "порога" in row.reason
    assert "0.05" in row.reason
    assert row.match_url == "?match=m1"
    assert row.polymarket_url == "https://polymarket.com/event/ev"


def test_active_row_open_buy_is_not_realized() -> None:
    snap = _snap(
        maps=(_view(_entry("m1", realized=-2.0, rebate=0.1)),),
        books=(_book(YES), _book(NO)),
        wallet=_wallet(positions=(WalletPosition(YES, 4.0, 0.5, NOW),)),
    )
    row = build_lists(snap, NOW).active[0]
    assert row.realized == 0.0
    assert row.net == 4.0 * (0.56 - 0.5) + 0.1


def test_active_row_unknown_mark_states() -> None:
    view = _view(_entry("m1"))
    wallet = _wallet(positions=(WalletPosition(YES, 4.0, 0.5, NOW),))
    for book in (
        _book(YES, bid=0.0, ask=0.0),
        _book(YES, bid=0.6, ask=0.5),
        _book(YES, ready=False),
        None,
    ):
        snap = _snap(
            maps=(view,),
            books=() if book is None else (book,),
            wallet=wallet,
        )
        row = build_lists(snap, NOW).active[0]
        assert row.positions[0].mark is None
        assert row.positions[0].mark_state == "unknown"
        assert row.unrealized_total is None


def test_active_row_stale_book_mark() -> None:
    book = _book(YES, connected=False)
    snap = _snap(
        maps=(_view(_entry("m1")),),
        books=(book,),
        wallet=_wallet(positions=(WalletPosition(YES, 4.0, 0.5, NOW),)),
    )
    row = build_lists(snap, NOW).active[0]
    assert row.positions[0].mark_state == "book_stale"
    assert row.unrealized_total is not None


def test_stale_and_record_only_rows_leave_active() -> None:
    stale = _view(_entry("m1"), status="stale")
    record_only = _view(_entry("m2", record_only=True), status="live")
    snap = _snap(maps=(stale, record_only))
    lists = build_lists(snap, NOW)
    assert [row.match_id for row in lists.active] == ["m1"]
    assert lists.active[0].stale
    assert {row.match_url for row in lists.idle} == {"?match=m2"}
    assert lists.idle[0].reason == "только запись"


def test_paused_clock_uses_recorded_second() -> None:
    decision = _decision(paused=True, second=42.0, snapshot_second=40.0)
    view = _view(_entry("m1"), decision=decision)
    row = build_lists(_snap(maps=(view,)), NOW).active[0]
    assert row.second == 42.0
    assert row.paused is True


def test_unknown_cutoff_window() -> None:
    entry = _entry("m1", params=SessionParams(None, None, None, None, None, None))
    view = _view(entry)
    row = build_lists(_snap(maps=(view,)), NOW).active[0]
    assert row.window is None
    entry2 = _entry("m2", params=SessionParams(None, 900, None, None, None, None))
    row2 = build_lists(_snap(maps=(_view(entry2),)), NOW).active[0]
    assert row2.window == "вход до 900с"


def test_unevaluated_decision_reason() -> None:
    decision = _decision(model_evaluated=False, entry_block=None, reason="feed_stale")
    row = build_lists(_snap(maps=(_view(_entry("m1"), decision=decision),)), NOW).active[0]
    assert row.reason == diagnostics.reason_label("feed_stale")


def test_closed_today_split_by_observed_day() -> None:
    today = _berlin_today()
    closed_now = _entry(
        "m1",
        session_ended=True,
        closed_observed_at=NOW,
        realized=10.0,
        imv=2.0,
        rebate=0.5,
        net=12.5,
        fill_count=3,
    )
    closed_old = _entry("m2", session_ended=True, closed_observed_at=NOW - 90000)
    undated = _entry("m3", session_ended=True, closed_observed_at=None)
    old_undated = _entry("m4", session_ended=True, closed_observed_at=None, last_write=NOW - 90000)
    snap = _snap(
        maps=(
            _view(closed_now, status="terminal"),
            _view(closed_old, status="terminal"),
            _view(undated, status="terminal"),
            _view(old_undated, status="terminal"),
        ),
        day=replace(EMPTY_DAY_SNAPSHOT, payout_known=True, payout_ts=NOW - 50000),
    )
    lists = build_lists(snap, NOW)
    assert [row.match_id for row in lists.closed_dated] == ["m1"]
    assert [row.match_id for row in lists.closed_undated] == ["m3"]
    row = lists.closed_dated[0]
    assert row.net == 12.5
    assert row.fill_count == 3
    assert _berlin_today() == today


def test_closed_net_unknown_without_imv() -> None:
    entry = _entry(
        "m1", session_ended=True, closed_observed_at=NOW, realized=10.0, imv=None, net=None
    )
    lists = build_lists(
        _snap(
            maps=(_view(entry, status="terminal"),),
            day=replace(EMPTY_DAY_SNAPSHOT, payout_known=True, payout_ts=NOW - 50000),
        ),
        NOW,
    )
    assert lists.closed_dated[0].net is None
    assert lists.closed_dated[0].realized == 10.0


def test_duplicate_live_legacy_prefers_live() -> None:
    live = _view(_entry("m1", session_ended=True, closed_observed_at=NOW), status="terminal")
    legacy = _view(
        _entry("m1", tree="legacy", session_ended=True, closed_observed_at=NOW),
        status="terminal",
    )
    paid = replace(EMPTY_DAY_SNAPSHOT, payout_known=True, payout_ts=NOW - 50000)
    lists = build_lists(_snap(maps=(live,), legacy_maps=(legacy,), day=paid), NOW)
    assert len(lists.closed_dated) == 1
    matches = find_match(_snap(maps=(live,), legacy_maps=(legacy,)), "m1")
    assert len(matches) == 2


def test_residuals_from_api_and_local() -> None:
    entry = _entry("m1", session_ended=True, closed_observed_at=NOW)
    api = PositionEntry(
        asset=YES, condition_id=CID, title="m1", size=0.01, cur_price=0.9, redeemable=True
    )
    day = replace(
        EMPTY_DAY_SNAPSHOT,
        positions=_positions(api),
        positions_candidate=None,
    )
    snap = _snap(
        maps=(_view(entry, status="terminal"),),
        day=day,
        wallet=_wallet(positions=(WalletPosition(YES, 5.0, 0.5, NOW),)),
        books=(_book(YES),),
    )
    lists = build_lists(snap, NOW)
    assert len(lists.residuals) == 1
    row = lists.residuals[0]
    assert row.redeem_state == "доступен redeem"
    leg = row.legs[0]
    assert leg.api_size == 0.01
    assert leg.local_size == 5.0
    assert leg.mark_state == "api"
    assert leg.value == 0.01 * 0.9


def test_local_leg_missing_from_complete_api_is_not_a_residual() -> None:
    entry = _entry("m1", session_ended=True, closed_observed_at=NOW)
    other = PositionEntry(
        asset="x", condition_id="0xother", title="x", size=1.0, cur_price=0.0, redeemable=True
    )
    snap = _snap(
        maps=(_view(entry, status="terminal"),),
        day=replace(EMPTY_DAY_SNAPSHOT, positions=_positions(other), positions_candidate=None),
        wallet=_wallet(positions=(WalletPosition(YES, 52.08, 0.96, NOW),)),
    )
    lists = build_lists(snap, NOW)
    assert lists.positions_state == "complete"
    assert lists.residuals == ()


def test_residual_reserve_only_row() -> None:
    entry = _entry("m1", session_ended=True, closed_observed_at=NOW)
    unsettled = UnsettledBuy(
        venue_id="v-1",
        session_id="s1",
        token_id=YES,
        price=0.5,
        qty=2.0,
        proven=False,
        resolved=False,
        created_at=NOW - 3600,
        updated_at=NOW - 60,
    )
    reserve = ReserveReport(
        core_buy=0.0,
        command_buy=0.0,
        unsettled_buy=1.0,
        total_known=1.0,
        available_cash=99.0,
        details=(
            ReserveDetail(
                source="unsettled",
                session_id="s1",
                condition_id=CID,
                venue_id="v-1",
                core_order_id=None,
                token_index=0,
                price=0.5,
                remaining_qty=2.0,
                notional=1.0,
                note=None,
            ),
        ),
        incomplete=True,
        limitations=("order log incomplete",),
    )
    snap = _snap(
        maps=(_view(entry, status="terminal"),),
        wallet=_wallet(unsettled=(unsettled,), token_cids=(TokenCid(YES, CID),)),
        reserve=reserve,
    )
    lists = build_lists(snap, NOW)
    assert len(lists.residuals) == 1
    row = lists.residuals[0]
    assert not row.legs
    assert row.commitments[0].venue_id == "v-1"
    assert row.commitments[0].age_s is not None


def test_terminal_exclusion_line_is_not_a_residual() -> None:
    entry = _entry("m1", session_ended=True, closed_observed_at=NOW)
    reserve = ReserveReport(
        core_buy=0.0,
        command_buy=0.0,
        unsettled_buy=0.0,
        total_known=0.0,
        available_cash=100.0,
        details=(
            ReserveDetail(
                source="core",
                session_id="s1",
                condition_id=CID,
                venue_id="v-1",
                core_order_id="c0",
                token_index=0,
                price=0.5,
                remaining_qty=10.0,
                notional=0.0,
                note="excluded: session terminal",
            ),
        ),
        incomplete=False,
        limitations=(),
    )
    snap = _snap(maps=(_view(entry, status="terminal"),), reserve=reserve)
    assert build_lists(snap, NOW).residuals == ()


def test_residual_unmatched_api_position() -> None:
    api = PositionEntry(
        asset="orphan-token",
        condition_id="0xother",
        title="Foreign map",
        size=3.0,
        cur_price=0.4,
        redeemable=True,
    )
    snap = _snap(day=replace(EMPTY_DAY_SNAPSHOT, positions=_positions(api)))
    lists = build_lists(snap, NOW)
    assert len(lists.residuals) == 1
    row = lists.residuals[0]
    assert row.match_id is None
    assert row.legs[0].mark_state == "api"


def test_residual_hidden_when_positions_incomplete() -> None:
    api = PositionEntry(
        asset="x", condition_id="0xother", title="x", size=1.0, cur_price=0.5, redeemable=None
    )
    snap = _snap(
        day=replace(
            EMPTY_DAY_SNAPSHOT,
            positions=_positions(api, complete=False),
            positions_candidate=_positions(api, complete=False),
        )
    )
    lists = build_lists(snap, NOW)
    assert lists.positions_state == "incomplete"
    assert lists.residuals == ()


def test_strip_metrics_states() -> None:
    snap = _snap()
    strip = build_strip(snap, NOW)
    assert strip.collateral.text == "$100.00"
    assert strip.collateral.verdict.state == "fresh"
    assert strip.collateral.age_text == "2с назад"
    assert "Свежий" not in strip.collateral.age_text
    assert strip.pnl.verdict.state == "no_data"
    assert strip.pnl_since == "выплата не найдена"
    assert strip.pnl_split is None

    stale_snap = _snap(balance=_balance(response_at=NOW - 1000, last_error="boom"))
    strip2 = build_strip(stale_snap, NOW)
    assert strip2.collateral.text == "$100.00"
    assert strip2.collateral.verdict.state == "stale"

    pending = _snap(balance=_balance(inflight=True))
    assert build_strip(pending, NOW).collateral.verdict.state == "updating"


def test_fold_after_payout_skips_the_payout_itself() -> None:
    payout = NOW - 3600
    rows = (
        ActivityEntry(payout, "MAKER_REBATE", "", 31.26),
        ActivityEntry(payout + 10, "TRADE", "BUY", 4.0),
        ActivityEntry(payout + 20, "TRADE", "SELL", 6.0),
    )
    folded = fold_after_payout(rows, (), payout)
    assert folded.rebate == 0.0
    assert folded.cash == 2.0
    assert folded.pnl == 2.0


def test_strip_day_fold_today() -> None:
    fold = DayFold(
        buy=10.0,
        sell=12.0,
        redeem=0.0,
        rebate=0.5,
        cash=2.5,
        open_mark=1.0,
        pnl=3.5,
        n_buy=1,
        n_sell=1,
        n_redeem=0,
        n_rebate=1,
        n_open=1,
    )
    day = replace(
        EMPTY_DAY_SNAPSHOT,
        day=_berlin_today(),
        fold=fold,
        fold_at=NOW - 10,
        complete=True,
        payout_known=True,
        payout_ts=NOW - 3600,
        accrual_total=1.25,
        accrual_at=NOW - 10,
    )
    strip = build_strip(_snap(day=day), NOW)
    assert strip.pnl.text == "$+4.75"
    assert strip.pnl.verdict.state == "fresh"
    assert strip.pnl_split is not None
    assert strip.pnl_split.trading == 2.0
    assert strip.pnl_split.open_mark == 1.0
    assert strip.pnl_split.rebate == 1.75
    assert strip.pnl_split.trading + strip.pnl_split.open_mark + strip.pnl_split.rebate == 4.75
    assert strip.pnl_since == fmt_clock(NOW - 3600)


def test_strip_portfolio_and_available_visibility() -> None:
    pos = PositionEntry(
        asset="t",
        condition_id="0xother",
        title="t",
        size=10.0,
        cur_price=0.5,
        redeemable=True,
    )
    day = replace(EMPTY_DAY_SNAPSHOT, positions=_positions(pos))
    strip = build_strip(_snap(day=day), NOW)
    assert strip.portfolio.text == "$105.00"
    assert strip.portfolio.verdict.state == "fresh"
    assert strip.portfolio.age_text == "30с назад"

    even = ReserveReport(
        core_buy=0.0,
        command_buy=0.0,
        unsettled_buy=0.0,
        total_known=0.0,
        available_cash=100.0,
        details=(),
        incomplete=False,
        limitations=(),
    )
    assert build_strip(_snap(day=day, reserve=even), NOW).available is None

    held = replace(even, total_known=3.0, available_cash=97.0)
    held_strip = build_strip(_snap(day=day, reserve=held), NOW)
    assert held_strip.available is not None
    assert held_strip.available.text == "$97.00"

    missing = replace(EMPTY_DAY_SNAPSHOT, positions=_positions(pos, complete=False))
    incomplete = build_strip(_snap(day=missing), NOW)
    assert incomplete.portfolio.text == "—"
    assert incomplete.portfolio.verdict.state == "no_data"


def test_min_delta_finding_uses_delta_threshold() -> None:
    entry = _entry("m1", params=SessionParams(0.05, 900, 5.0, 5.0, 15.0, 25.0))
    snap = _snap(maps=(_view(entry),))
    diag = build_diagnostics(snap, SellWatcher(), NOW)
    labels = " ".join(f.label for f in diag.findings)
    assert "порога входа 0.05" in labels


def test_diagnostics_orders_fatal_first() -> None:
    snap = _snap(error="SourceDied", wallet=_wallet(ok=False, error="db gone"))
    diag = build_diagnostics(snap, SellWatcher(), NOW)
    assert diag.headline is not None
    assert diag.headline.severity == "fatal"
    assert "диагностика:" not in diag.line
    assert diag.findings[0].severity == "fatal"
    assert diag.findings[1].severity == "error"


def test_diagnostics_clean_reports_observed() -> None:
    healthy = replace(
        EMPTY_HEALTH,
        ok=True,
        consistent=True,
        container_id="abc",
        container_state="running",
        halted=False,
    )
    clean = _snap(
        health=healthy,
        nontrading=replace(EMPTY_NONTRADING, ok=True, scanned_at=NOW),
    )
    diag = build_diagnostics(clean, SellWatcher(), NOW)
    assert diag.findings == ()
    assert "проблем" in diag.line and "контейнер работает" in diag.line


def test_diagnostics_halt_findings() -> None:
    healthy = replace(
        EMPTY_HEALTH,
        ok=True,
        consistent=True,
        container_id="abc",
        container_state="running",
        halted=True,
        halt_label="risk_halt:map1",
        halt_at=NOW - 10,
    )
    diag = build_diagnostics(_snap(health=healthy), SellWatcher(), NOW)
    assert diag.headline is not None
    assert "halt" in diag.headline.label
    assert diag.headline.severity == "error"


def test_sell_watcher_wedges_after_threshold() -> None:
    watcher = SellWatcher()
    sell = SellOrderFacts(
        order_id="o1",
        token_index=0,
        token_id=YES,
        status="canceling",
        price=0.7,
        submitted_qty=4.0,
        filled_qty=0.0,
        remaining_qty=4.0,
        held_qty=4.0,
        venue_id="v-1",
        cancel_reason="",
    )
    session = _session(sells=(sell,))
    snap = _snap(wallet=_wallet(sessions=(session,)))
    first = build_diagnostics(snap, watcher, NOW)
    later_snap = _snap(wallet=_wallet(sessions=(session,), read_at=NOW + 30))
    later = build_diagnostics(later_snap, watcher, NOW + 31.0)
    wedge = [f for f in later.findings if "SELL" in f.label]
    assert not any("SELL" in f.label and "зависан" in f.label for f in first.findings)
    assert wedge and wedge[0].severity == "warn"


def test_sell_watcher_resets_on_stale_input() -> None:
    watcher = SellWatcher()
    sell = SellOrderFacts(
        order_id="o1",
        token_index=0,
        token_id=YES,
        status="canceling",
        price=0.7,
        submitted_qty=4.0,
        filled_qty=0.0,
        remaining_qty=4.0,
        held_qty=4.0,
        venue_id="v-1",
        cancel_reason="",
    )
    session = _session(sells=(sell,))
    snap = _snap(wallet=_wallet(sessions=(session,), read_at=NOW + 59))
    build_diagnostics(snap, watcher, NOW + 60.0)
    stale_wallet = _snap(wallet=_wallet(sessions=(session,), read_at=NOW, ok=True))
    later = build_diagnostics(stale_wallet, watcher, NOW + 100.0)
    assert not any("зависан" in f.label for f in later.findings)


def test_idle_rows_from_sidecars() -> None:
    market = SkippedMarket(
        condition_id="0xskip",
        game="dota",
        event_slug="ev2",
        market_slug="m-skip",
        map_number=3,
        outcome_names=("A", "B"),
        reason="market inactive",
        reason_source="flags",
        reason_ts=NOW - 100,
        starts_at=NOW + 3600,
    )
    snap = _snap(
        nontrading=NontradingFacts(
            ok=True,
            error=None,
            attempt_at=NOW - 30,
            scanned_at=NOW - 30,
            markets=(market,),
            invalid_sidecars=0,
            missing_roots=(),
            root_errors=(),
        )
    )
    lists = build_lists(snap, NOW)
    assert len(lists.idle) == 1
    row = lists.idle[0]
    assert row.reason == "рынок неактивен"
    assert row.starts_at == NOW + 3600
    past = SkippedMarket(
        condition_id="0xold",
        game="dota",
        event_slug="ev-old",
        market_slug="m-old",
        map_number=1,
        outcome_names=("A", "B"),
        reason="reason unknown",
        reason_source="flags",
        reason_ts=NOW - 100,
        starts_at=NOW - 86400 * 30,
    )
    old_snap = _snap(
        nontrading=NontradingFacts(
            ok=True,
            error=None,
            attempt_at=NOW - 30,
            scanned_at=NOW - 30,
            markets=(market, past),
            invalid_sidecars=0,
            missing_roots=(),
            root_errors=(),
        )
    )
    assert [row.key for row in build_lists(old_snap, NOW).idle] == ["sidecar:0xskip"]
    assert row.match_url is None
    assert row.polymarket_url == "https://polymarket.com/event/ev2"


def test_nontrading_note_on_failure() -> None:
    snap = _snap(nontrading=replace(EMPTY_NONTRADING, ok=False, error="scan died", attempt_at=NOW))
    lists = build_lists(snap, NOW)
    assert lists.nontrading_note == "scan died"
