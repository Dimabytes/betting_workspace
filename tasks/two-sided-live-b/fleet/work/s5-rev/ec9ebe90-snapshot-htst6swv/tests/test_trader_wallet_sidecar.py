"""Host-level sidecar refresh and MatchWorker watchdog BUY-cancel."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.domain import MarketMeta, Quote, Side
from trader_session_fixtures import (
    MATCH_ID,
    NO_TOKEN,
    YES_TOKEN,
    FakeModelServer,
    build_attached_worker,
    build_discovered,
    build_event,
    make_meta,
    read_session_records,
    sidecar_body,
    write_sidecar,
)

from trader import session_journal, wallet_host
from trader.bindings import ModelReference
from trader.collector_sidecars import scan_sidecars


def test_host_sidecar_refresh_updates_tick_min_and_cancels_off_grid_only(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A tick change 0.001 -> 0.01 updates meta, catalog, books; off-grid dies, on-grid stays."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(tickSize="0.001"))
    meta = make_meta(tick_size=0.001)
    worker, fake = build_attached_worker(
        tmp_path, request, archive_root=archive_root, meta=meta, tick_str="0.001"
    )
    fake.metas[meta.condition_id] = meta
    fake.md.books[YES_TOKEN].set_tick_size(0.001)
    fake.md.books[NO_TOKEN].set_tick_size(0.001)
    placed = asyncio.run(
        fake.gateway.place(
            [
                Quote(YES_TOKEN, Side.BUY, 0.045, 5.0),
                Quote(YES_TOKEN, Side.BUY, 0.05, 5.0),
                Quote(NO_TOKEN, Side.SELL, 0.05, 5.0),
            ],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)
    write_sidecar(archive_root, sidecar_body(tickSize="0.01"))
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker,)))

    assert fake.metas[meta.condition_id].tick_size == 0.01
    assert cast(MarketMeta, fake.catalog.get(meta.condition_id)).tick_size == 0.01
    assert fake.md.books[YES_TOKEN].tick_size == 0.01
    assert fake.md.books[NO_TOKEN].tick_size == 0.01
    remaining = list(fake.state.orders.values())
    assert len(remaining) == 2
    assert all(order.price == 0.05 for order in remaining)
    assert {order.side for order in remaining} == {Side.BUY, Side.SELL}
    records = read_session_records(tmp_path / "journal")
    tick_records = [record for record in records if record["kind"] == "tick_size_change"]
    assert len(tick_records) == 1
    assert tick_records[0]["old_tick_size"] == "0.001"
    assert tick_records[0]["new_tick_size"] == "0.01"
    assert worker._observed_tick == pytest.approx(0.01)


def test_refresh_sidecars_by_game_scans_each_archive(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A LoL CID missing from the Dota archive still refreshes from the LoL archive."""
    lol_cid = "0xLOLCID"
    dota_archive = tmp_path / "archive_dota"
    lol_archive = tmp_path / "archive_lol"
    write_sidecar(dota_archive, sidecar_body())
    write_sidecar(lol_archive, sidecar_body(conditionId=lol_cid))
    dota_worker, _dota = build_attached_worker(
        tmp_path / "dota", request, archive_root=dota_archive
    )
    lol_worker, _lol = build_attached_worker(tmp_path / "lol", request, archive_root=lol_archive)
    lol_worker._discovered = replace(
        lol_worker._discovered,
        game="lol",
        market=replace(lol_worker._discovered.market, condition_id=lol_cid),
    )
    lol_worker._cid = lol_cid
    assert lol_worker._binding is not None
    lol_worker._binding = replace(lol_worker._binding, condition_id=lol_cid)
    asyncio.run(
        wallet_host.refresh_sidecars_by_game(
            {"dota": dota_archive, "lol": lol_archive},
            (dota_worker, lol_worker),
        )
    )
    assert dota_worker._sidecar_usable is True
    assert lol_worker._sidecar_usable is True


def test_sidecar_refresh_skips_after_quiesce(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A quiesced worker does not apply sidecar updates or write trading_error."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(tickSize="0.001"))
    meta = make_meta(tick_size=0.001)
    worker, fake = build_attached_worker(
        tmp_path, request, archive_root=archive_root, meta=meta, tick_str="0.001"
    )
    fake.metas[meta.condition_id] = meta
    worker._quiesced = True
    write_sidecar(archive_root, sidecar_body(tickSize="0.01"))
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker,)))
    assert fake.metas[meta.condition_id].tick_size == 0.001
    records = read_session_records(tmp_path / "journal")
    assert [record["kind"] for record in records] == []


def test_host_sidecar_refresh_pins_place_tick_when_sidecar_shrinks_to_millitick(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """A live 0.01→0.001 keeps CLOB meta/books at 0.01 and journals the shrink."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body(tickSize="0.01"))
    meta = make_meta(tick_size=0.01)
    worker, fake = build_attached_worker(
        tmp_path, request, archive_root=archive_root, meta=meta, tick_str="0.01"
    )
    fake.metas[meta.condition_id] = meta
    fake.catalog.upsert_market(meta)
    fake.md.books[YES_TOKEN].set_tick_size(0.01)
    fake.md.books[NO_TOKEN].set_tick_size(0.01)
    placed = asyncio.run(
        fake.gateway.place(
            [
                Quote(YES_TOKEN, Side.BUY, 0.45, 5.0),
                Quote(NO_TOKEN, Side.SELL, 0.45, 5.0),
            ],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)
    write_sidecar(archive_root, sidecar_body(tickSize="0.001"))
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker,)))

    assert fake.metas[meta.condition_id].tick_size == 0.01
    assert cast(MarketMeta, fake.catalog.get(meta.condition_id)).tick_size == 0.01
    assert fake.md.books[YES_TOKEN].tick_size == 0.01
    assert fake.md.books[NO_TOKEN].tick_size == 0.01
    remaining = list(fake.state.orders.values())
    assert len(remaining) == 2
    assert {order.side for order in remaining} == {Side.BUY, Side.SELL}
    assert worker._observed_tick == pytest.approx(0.001)
    records = read_session_records(tmp_path / "journal")
    tick_records = [record for record in records if record["kind"] == "tick_size_change"]
    assert len(tick_records) == 1
    assert tick_records[0]["old_tick_size"] == "0.01"
    assert tick_records[0]["new_tick_size"] == "0.001"


def test_host_sidecar_scan_runs_once_for_every_live_cid(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One directory scan serves every attached worker."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())
    worker, _fake = build_attached_worker(tmp_path, request, archive_root=archive_root)
    scans: list[int] = []
    real_scan = scan_sidecars

    def counting_scan(root: Path, now: float) -> Any:
        scans.append(1)
        return real_scan(root, now)

    monkeypatch.setattr(wallet_host, "scan_sidecars", counting_scan)
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker, worker)))
    assert scans == [1]


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "outcomes": [
                {"index": 0, "name": "Aurora", "tokenId": "TOKEN9"},
                {"index": 1, "name": "Team Secret", "tokenId": NO_TOKEN},
            ]
        },
        {"tickSize": None},
        {"minOrderSize": None},
        {"acceptingOrders": False},
        {"closed": True},
        {"eventId": "999999"},
    ],
)
def test_sidecar_gone_or_changed_enters_permanent_safe_mode(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    mutation: dict[str, object],
) -> None:
    """Binding gone/changed/not tradeable: cell forced, BUYs cancelled, SELL remains, no retry."""
    archive_root = tmp_path / "archive"
    write_sidecar(archive_root, sidecar_body())
    worker, fake = build_attached_worker(tmp_path, request, archive_root=archive_root)
    meta = make_meta()
    placed = asyncio.run(
        fake.gateway.place(
            [Quote(YES_TOKEN, Side.BUY, 0.45, 5.0), Quote(NO_TOKEN, Side.SELL, 0.45, 5.0)],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)
    write_sidecar(archive_root, sidecar_body(**mutation))
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker,)))
    assert worker._cell.forced is True
    assert worker._sidecar_usable is False
    remaining = list(fake.state.orders.values())
    assert remaining
    assert all(order.side is Side.SELL for order in remaining)
    write_sidecar(archive_root, sidecar_body())
    asyncio.run(wallet_host.refresh_attached_sidecars(archive_root, (worker,)))
    assert worker._sidecar_usable is False
    assert worker._cell.forced is True


def test_watchdog_entry_timeout_cancels_buy_only(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Entry timeout cancels BUY; an empty cell stays forced and SELL remains."""
    worker, fake = build_attached_worker(
        tmp_path,
        request,
        model=FakeModelServer(radiant_fair=0.60),
        feed_timeout_seconds=0.05,
    )
    meta = make_meta()
    placed = asyncio.run(
        fake.gateway.place(
            [
                Quote(YES_TOKEN, Side.BUY, 0.45, 5.0),
                Quote(NO_TOKEN, Side.SELL, 0.55, 5.0),
            ],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)

    async def scenario() -> None:
        worker._watchdog.arm()
        await asyncio.sleep(0.1)
        await asyncio.sleep(0)
        assert worker._cell.forced is True
        remaining = list(fake.state.orders.values())
        assert remaining
        assert all(order.side is Side.SELL for order in remaining)

    asyncio.run(scenario())


def test_match_journal_uses_host_git_commit_not_a_fresh_lookup(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Journal provenance uses the boot-time git string; git is not called per match."""

    def boom(git_dir: Path) -> str:
        raise AssertionError("must not look up git per match")

    monkeypatch.setattr(session_journal, "read_git_head_commit", boom)
    worker, _fake = build_attached_worker(tmp_path, request, git_commit="pinned-commit")
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    archive_dir = tmp_path / MATCH_ID
    archive_dir.mkdir(parents=True)
    journal = worker._open_journal(archive_dir, start, False)
    assert journal is not None
    records = read_session_records(archive_dir)
    assert records[0]["git_commit"] == "pinned-commit"


def test_scoped_cancel_never_calls_broad_cancel(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cancel_entry_buys never uses cancel_all or cancel_asset."""
    worker, fake = build_attached_worker(tmp_path, request)
    meta = make_meta()
    placed = asyncio.run(
        fake.gateway.place(
            [
                Quote(YES_TOKEN, Side.BUY, 0.45, 5.0),
                Quote(YES_TOKEN, Side.SELL, 0.55, 5.0),
            ],
            meta,
        )
    )
    for order in placed:
        fake.state.upsert_order(order)

    def broad_cancel_boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("broad cancellation must never run")

    monkeypatch.setattr(fake.gateway, "cancel_all", broad_cancel_boom)
    monkeypatch.setattr(fake.gateway, "cancel_asset", broad_cancel_boom)
    asyncio.run(worker.cancel_entry_buys())
    remaining = list(fake.state.orders.values())
    assert all(order.side is Side.SELL for order in remaining)


def test_match_worker_does_not_quote_until_both_books_ready(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """After attach the cell stays clear until both tokens note a snapshot for this generation."""
    worker, _fake = build_attached_worker(
        tmp_path, request, model=FakeModelServer(radiant_fair=0.60)
    )
    readiness = worker._host.readiness
    readiness.attach_token(YES_TOKEN)
    readiness.attach_token(NO_TOKEN)

    async def scenario() -> None:
        await worker.handle_event(build_event(10))
        assert worker._cell.forced is True
        readiness.note_snapshot(YES_TOKEN)
        await worker.handle_event(build_event(11))
        assert worker._cell.forced is True
        readiness.note_snapshot(NO_TOKEN)
        await worker.handle_event(build_event(12))
        assert worker._cell.forced is False
        assert worker._cell.yes_fair is not None

    asyncio.run(scenario())
    records = read_session_records(tmp_path / "journal")
    reasons = [record["reason"] for record in records if record["kind"] == "signal"]
    assert reasons[0] == "missing_book"
    assert reasons[1] == "missing_book"
    assert reasons[2] == "model"
