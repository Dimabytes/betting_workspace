"""Tests for `trader.session_journal`: the durable session.jsonl record."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import io
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self, cast

import httpx
import pytest
from polymaker.domain import Fill, Side
from trader_session_fixtures import (
    CONDITION_ID,
    MATCH_ID,
    NO_TOKEN,
    YES_TOKEN,
    AlertRecorder,
    build_discovered,
    build_event,
    make_binding,
    read_session_records,
)

from shared.utils.match_time import parse_utc
from shared.utils.top_players import TopPlayerFeatures
from trader import archive_paths, notify, session_journal, session_types
from trader.archive_types import SessionRecord, SessionSignalRecord
from trader.bindings import ModelReference
from trader.clip_rules import ClipChoice
from trader.live_feed import FeedSource, GameSnapshot, MatchPhase
from trader.paths import SESSION_JOURNAL_FILENAME
from trader.session_types import (
    EntryBlock,
    RawBookPair,
    SignalDecision,
    SignalReason,
)
from viewer.archive_read import iter_json_objects
from viewer.game_state_replay import FeedClock
from viewer.live_tape import _LiveTokens, _reduce_journal

_CLIP = ClipChoice(5.0, "default")


def test_session_journal_writes_then_flushes_then_fsyncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact write -> flush -> fsync ordering of the journal."""
    archive_dir = tmp_path / "match"
    calls: list[str] = []

    def record_fsync(fd: int) -> None:
        calls.append("fsync")

    monkeypatch.setattr(archive_paths.os, "fsync", record_fsync)

    class RecordingHandle:
        """TextIO stand-in recording the call order."""

        def write(self, text: str) -> int:
            calls.append("write")
            return len(text)

        def flush(self) -> None:
            calls.append("flush")

        def fileno(self) -> int:
            calls.append("fileno")
            return -1

        def close(self) -> None:
            calls.append("close")

    journal = session_journal.SessionJournal(archive_dir)
    cast(Any, journal)._writer._handle.close()
    cast(Any, journal)._writer._handle = RecordingHandle()
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal.write_start(start, "abc1234", None, clip=_CLIP)
    assert calls == ["write", "flush", "fileno", "fsync"]
    journal.close()
    assert calls == ["write", "flush", "fileno", "fsync", "close"]


def test_session_journal_reopen_truncates_only_the_crash_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated tail is dropped and fsynced on reopen; completed lines survive."""
    archive_dir = tmp_path / "match"
    archive_dir.mkdir(parents=True)
    path = archive_dir / "session.jsonl"
    path.write_bytes(
        b'{"kind":"tick_size_change","old_tick_size":"0.01","new_tick_size":"0.001"}\n{"kind":"trad'
    )
    fsync_calls: list[int] = []

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    monkeypatch.setattr(archive_paths.os, "fsync", record_fsync)
    journal = session_journal.SessionJournal(archive_dir)
    assert fsync_calls  # the truncation itself was synced
    assert journal.is_fresh() is False  # one completed record survives
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "decision", "error_type": "X"})
    journal.write(record)
    journal.close()
    records = read_session_records(archive_dir)
    assert len(records) == 2
    assert records[0]["kind"] == "tick_size_change"
    assert records[1]["kind"] == "trading_error"


def test_session_journal_keeps_the_original_provenance_on_resume(tmp_path: Path) -> None:
    """A resumed journal never rewrites the first session_start record."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    first = session_journal.SessionJournal(archive_dir)
    assert first.is_fresh() is True
    first.write_start(start, "commit-1", None, clip=_CLIP)
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "setup", "error_type": "X"})
    first.write(record)
    first.close()

    resumed = session_journal.SessionJournal(archive_dir)
    assert resumed.is_fresh() is False
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "decision", "error_type": "Y"})
    resumed.write(record)
    resumed.close()
    records = read_session_records(archive_dir)
    assert len(records) == 3
    assert records[0]["kind"] == "session_start"
    assert records[0]["git_commit"] == "commit-1"
    assert records[0]["execution_mode"] == "paper"
    assert records[0]["schema_version"] == 7


def test_lol_match_start_does_not_put_game_on_session_provenance(tmp_path: Path) -> None:
    """Session journal schema stays 7; LoL identity is match.json, not the tape."""
    archive_dir = tmp_path / "match"
    start = replace(build_discovered(), game="lol").with_model(
        ModelReference(name="m", trained_at="t")
    )
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "abc1234", None, clip=_CLIP)
    journal.close()
    records = read_session_records(archive_dir)
    assert records[0]["schema_version"] == 7
    assert "game" not in records[0]


def test_fault_reporter_dedupes_alerts_per_phase_and_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Journal records every fault; Telegram alerts once per (phase, type)."""
    journal = session_journal.SessionJournal(tmp_path / "match")
    alerts = AlertRecorder()
    monkeypatch.setattr(session_journal, "notify_in_background", alerts)
    reporter = session_journal.FaultReporter(journal)
    reporter.report("setup", "RuntimeError")
    reporter.report("setup", "RuntimeError")
    reporter.report("setup", "ValueError")
    reporter.report("decision", "RuntimeError")
    journal.close()
    records = read_session_records(tmp_path / "match")
    assert len(records) == 4
    assert alerts.messages == [
        "trader trading setup failed: RuntimeError",
        "trader trading setup failed: ValueError",
        "trader trading decision failed: RuntimeError",
    ]


def test_unpatched_fault_report_never_constructs_telegram_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault with no AlertRecorder must still not construct the Telegram client."""
    constructed: list[str] = []

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append("constructed")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise AssertionError("telegram http must not run")

    monkeypatch.setattr(notify.httpx, "Client", FakeClient)
    journal = session_journal.SessionJournal(tmp_path / "journal")
    reporter = session_journal.FaultReporter(journal)
    reporter.report("market_data", "RuntimeError")
    journal.close()
    time.sleep(0.05)
    assert constructed == []


def test_session_journal_sanitizes_nonfinite_fields_to_null(tmp_path: Path) -> None:
    """A nonfinite value becomes null: the record survives, JSON stays strict."""
    journal = session_journal.SessionJournal(tmp_path / "match")
    record = cast(
        SessionRecord,
        {
            "kind": "session_end",
            "terminal_reason": "finished",
            "positions": {"TOKEN0": float("inf")},
            "net_cash": float("nan"),
            "inventory_value": 0.0,
            "equity": 0.0,
        },
    )
    journal.write(record)
    journal.close()
    documents = [
        json.loads(
            line,
            parse_constant=lambda value: pytest.fail(
                f"nonfinite constant {value} in session.jsonl"
            ),
        )
        for line in (tmp_path / "match" / "session.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(documents) == 1
    assert documents[0]["net_cash"] is None
    assert documents[0]["positions"] == {"TOKEN0": None}
    assert documents[0]["inventory_value"] == 0.0


def test_session_journal_provenance_round_trips_the_binding(tmp_path: Path) -> None:
    """The provenance record persists and re-reads the canonical binding."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), clip=_CLIP)
    assert journal.first_binding(MATCH_ID, CONDITION_ID) == make_binding()
    journal.close()

    resumed = session_journal.SessionJournal(archive_dir)
    assert resumed.is_fresh() is False
    assert resumed.first_binding(MATCH_ID, CONDITION_ID) == make_binding()
    resumed.close()

    empty_dir = tmp_path / "empty"
    empty = session_journal.SessionJournal(empty_dir)
    empty.write_start(start, "commit-1", None, clip=_CLIP)
    assert empty.first_binding(MATCH_ID, CONDITION_ID) is None
    empty.close()


def test_schema_3_resume_does_not_rewrite_schema_6(tmp_path: Path) -> None:
    """A schema-3 journal keeps version 3 on resume; fresh journals write 7."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), "live", clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = 3
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    pinned = resumed.first_start(MATCH_ID, CONDITION_ID)
    assert pinned.schema_version == 3
    assert pinned.execution_mode == "live"
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "setup", "error_type": "X"})
    resumed.write(record)
    resumed.close()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["schema_version"] == 3


def test_schema_4_resume_does_not_rewrite_schema_6(tmp_path: Path) -> None:
    """A schema-4 journal keeps version 4 on resume; fresh journals write 7."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), "live", clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = 4
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    pinned = resumed.first_start(MATCH_ID, CONDITION_ID)
    assert pinned.schema_version == 4
    assert pinned.execution_mode == "live"
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "setup", "error_type": "X"})
    resumed.write(record)
    resumed.close()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["schema_version"] == 4


def test_schema_5_resume_does_not_rewrite_schema_6(tmp_path: Path) -> None:
    """A schema-5 journal keeps version 5 on resume; fresh journals write 7."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), "live", clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = 5
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    pinned = resumed.first_start(MATCH_ID, CONDITION_ID)
    assert pinned.schema_version == 5
    assert pinned.execution_mode == "live"
    record = cast(SessionRecord, {"kind": "trading_error", "phase": "setup", "error_type": "X"})
    resumed.write(record)
    resumed.close()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["schema_version"] == 5


def test_schema_5_write_fill_includes_fill_key(tmp_path: Path) -> None:
    """Fresh journals write schema 6 and every fill record carries fill_key and venue."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    fill = Fill("tok", Side.BUY, 0.40, 10.0, "clob-1:order-1", 1.0, is_maker=True)
    journal.write_fill(fill, 10.0, -4.0, 12, "2026-08-19T00:00:00Z", "clob-1:order-1")
    journal.close()
    records = read_session_records(archive_dir)
    assert records[0]["schema_version"] == 7
    assert "venue" not in records[0]
    assert records[1]["kind"] == "fill"
    assert records[1]["fill_key"] == "clob-1:order-1"
    assert records[1]["venue"] == "polymarket"


def test_first_binding_accepts_schema_version_1(tmp_path: Path) -> None:
    """A schema-1 provenance written by an older session still resumes."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = 1
    document.pop("execution_mode", None)
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    assert resumed.first_binding(MATCH_ID, CONDITION_ID) == make_binding()
    resumed.close()


def test_malformed_provenance_binding_fails_closed(tmp_path: Path) -> None:
    """A corrupted provenance binding is a typed setup fault, never a default."""
    archive_dir = tmp_path / "match"
    archive_dir.mkdir(parents=True)
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    document = json.loads(lines[0])
    document["sidecar_binding"] = {"schema_version": 1, "condition_id": 5}
    lines[0] = json.dumps(document)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    with pytest.raises(
        session_types.TradingDisabled, match="sidecar binding record keys do not match"
    ):
        resumed.first_binding(MATCH_ID, CONDITION_ID)
    resumed.close()


def test_grid_binding_round_trips_through_the_provenance(tmp_path: Path) -> None:
    """A non-null gridSeriesId survives write_start -> reopen -> first_binding."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    binding = make_binding(grid_series_id="2974458")
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", binding, clip=_CLIP)
    journal.close()
    resumed = session_journal.SessionJournal(archive_dir)
    assert resumed.first_binding(MATCH_ID, CONDITION_ID) == binding
    resumed.close()


def _write_fill(
    journal: session_journal.SessionJournal,
    token_id: str,
    side: Side,
    position_after: float,
    ts_utc: str,
) -> None:
    """Append one fill record used by the open-round scanner tests."""
    fill = Fill(token_id, side, 0.40, 10.0, "k", 1.0, is_maker=True)
    journal.write_fill(fill, position_after, -4.0, 12, ts_utc, "k")


def test_scan_open_round_restores_after_buy(tmp_path: Path) -> None:
    """A BUY fill becomes the open-round start and the last-BUY stamp."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    ts_utc = "2026-08-19T00:01:00.000000Z"
    _write_fill(journal, YES_TOKEN, Side.BUY, 10.0, ts_utc)
    scanned = journal.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    journal.close()
    expected = parse_utc(ts_utc).timestamp()
    assert scanned == session_journal.JournalRound(10.0, 0.0, expected)


def test_scan_open_round_resets_after_full_sell(tmp_path: Path) -> None:
    """A SELL back to size 0 clears the open-round start."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    _write_fill(journal, YES_TOKEN, Side.BUY, 10.0, "2026-08-19T00:01:00.000000Z")
    _write_fill(journal, YES_TOKEN, Side.SELL, 0.0, "2026-08-19T00:02:00.000000Z")
    scanned = journal.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    journal.close()
    assert scanned == session_journal.JournalRound(0.0, 0.0, None)


def test_scan_open_round_rejects_a_foreign_token(tmp_path: Path) -> None:
    """A fill for a token that is not YES or NO disables trading."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    _write_fill(journal, "foreign-token", Side.BUY, 10.0, "2026-08-19T00:01:00.000000Z")
    with pytest.raises(
        session_types.TradingDisabled, match="session journal open round cannot be read"
    ):
        journal.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    journal.close()


def test_scan_open_round_resets_after_sell_to_dust(tmp_path: Path) -> None:
    """A SELL leftover below min_order_size clears the open-round stamps."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    _write_fill(journal, YES_TOKEN, Side.BUY, 10.0, "2026-08-19T00:01:00.000000Z")
    _write_fill(journal, YES_TOKEN, Side.SELL, 0.009, "2026-08-19T00:02:00.000000Z")
    scanned = journal.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    journal.close()
    assert scanned.yes_size == pytest.approx(0.009)
    assert scanned.no_size == 0.0
    assert scanned.last_buy_unix is None


_KALSHI_TICKER = "KXDOTA2MAP-1-AUR"


def _tape_keys(row: dict[str, object]) -> set[str]:
    """Key set of one journal row."""
    return set(row)


def _pm_signal(market_p: float, prior: float) -> SignalDecision:
    """Tiny PM SignalDecision so write_signal is the production path."""
    event = build_event(12)
    return SignalDecision(
        snapshot=event.snapshot,
        arrived_at=0.0,
        raw=RawBookPair(0.4, 0.5, 0.45, 0.5, 0.6, 0.55),
        market_p_radiant=market_p,
        market_radiant_prior=prior,
        radiant_fair=0.52,
        yes_fair=0.52,
        reason=SignalReason.MODEL,
        entry_block=EntryBlock.NONE,
        entry_token_id=None,
        entry_price=0.0,
        exit_state="none",
        pos_yes=0.0,
        pos_no=0.0,
        feed_source=event.source,
        feed_received_at_utc=event.received_at_utc,
        model_evaluated=True,
        raw_delta=0.07,
    )


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_resume_schemas_1_to_5_as_polymarket(tmp_path: Path, version: int) -> None:
    """Resume 1-5 keeps provenance; missing venue reads PM; new fills stamp polymarket."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", make_binding(), "live", clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = version
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    if version in {1, 2}:
        document.pop("execution_mode", None)
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    pinned = resumed.first_start(MATCH_ID, CONDITION_ID)
    assert pinned.schema_version == version
    if version in {1, 2}:
        assert pinned.execution_mode == "paper"
    else:
        assert pinned.execution_mode == "live"
    _write_fill(resumed, YES_TOKEN, Side.BUY, 10.0, "2026-08-19T00:01:00.000000Z")
    scanned = resumed.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    resumed.close()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    records = read_session_records(archive_dir)
    fill_row = next(row for row in records if row["kind"] == "fill")
    assert first["schema_version"] == version
    assert fill_row["venue"] == "polymarket"
    assert scanned.yes_size == 10.0


def test_schema_5_fill_without_venue_opens_a_pm_round(tmp_path: Path) -> None:
    """A schema-5 fill that omits venue still reconstructs the PM open round."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    document["schema_version"] = 5
    document.pop("clip_usdc", None)
    document.pop("clip_reason", None)
    fill = {
        "kind": "fill",
        "token_id": YES_TOKEN,
        "side": "BUY",
        "price": 0.4,
        "size": 10.0,
        "is_maker": True,
        "position_after": 10.0,
        "net_cash": -4.0,
        "second": 12,
        "ts_utc": "2026-08-19T00:01:00.000000Z",
        "fill_key": "k",
    }
    path.write_text(json.dumps(document) + "\n" + json.dumps(fill) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    scanned = resumed.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    resumed.close()
    assert "venue" not in fill
    assert session_journal.record_venue(cast(dict[str, object], fill)) == "polymarket"
    assert scanned.yes_size == 10.0


def test_fresh_start_is_schema_6_without_venue(tmp_path: Path) -> None:
    """Fresh write_start is schema 6, has execution_mode, and does not carry venue."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, "live", clip=_CLIP)
    journal.close()
    records = read_session_records(archive_dir)
    assert records[0]["kind"] == "session_start"
    assert records[0]["schema_version"] == 7
    assert records[0]["execution_mode"] == "live"
    assert "venue" not in records[0]


def test_pm_signal_carries_the_polymarket_venue(tmp_path: Path) -> None:
    """write_signal stamps polymarket and keeps market_p_radiant."""
    archive_dir = tmp_path / "match"
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_signal(_pm_signal(0.45, 0.50))
    journal.close()
    row = read_session_records(archive_dir)[0]
    assert row["venue"] == "polymarket"
    assert "market_p_radiant" in _tape_keys(row)
    assert "kalshi_radiant_prior" not in row
    assert row["market_p_radiant"] == 0.45


@pytest.mark.parametrize("source", [FeedSource.GRID, FeedSource.ODDIN])
def test_signal_record_carries_feed_provenance_and_game_snapshot(
    tmp_path: Path, source: FeedSource
) -> None:
    """The signal row mirrors the decision snapshot and the feed receipt verbatim."""
    archive_dir = tmp_path / "match"
    snapshot = GameSnapshot(
        second=321,
        server_timestamp=2_000_000_321,
        phase=MatchPhase.PRE_HORN,
        radiant_nw_adv=1_500,
        radiant_nw=12_345,
        dire_nw=10_845,
        radiant_xp_adv=777,
        deaths_radiant=4,
        deaths_dire=9,
        top=TopPlayerFeatures(900, 0.31, 0.27, 2_400, 0.58, 0.52),
        paused=True,
    )
    event = replace(build_event(321), snapshot=snapshot, source=source)
    decision = replace(
        _pm_signal(0.45, 0.50),
        snapshot=event.snapshot,
        feed_source=event.source,
        feed_received_at_utc=event.received_at_utc,
        raw_delta=-0.03125,
    )
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_signal(decision)
    journal.close()
    row = read_session_records(archive_dir)[0]
    assert row["venue"] == "polymarket"
    assert row["second"] == 321
    assert row["yes_best_bid"] == 0.4
    assert row["yes_best_ask"] == 0.5
    assert row["yes_mid"] == 0.45
    assert row["no_best_bid"] == 0.5
    assert row["no_best_ask"] == 0.6
    assert row["no_mid"] == 0.55
    assert row["market_p_radiant"] == 0.45
    assert row["market_radiant_prior"] == 0.5
    assert row["radiant_fair"] == 0.52
    assert row["yes_fair"] == 0.52
    assert row["reason"] == "model"
    assert row["entry_block"] == "none"
    assert row["exit_state"] == "none"
    assert row["pos_yes"] == 0.0
    assert row["pos_no"] == 0.0
    assert row["feed_source"] == source.value
    assert row["feed_received_at_utc"] == "2026-08-14T12:00:00.100000Z"
    assert row["model_evaluated"] is True
    assert row["raw_delta"] == -0.03125
    game = cast(dict[str, object], row["game_snapshot"])
    top = cast(dict[str, object], game["top"])
    assert game == {
        "second": 321,
        "server_timestamp": 2_000_000_321,
        "phase": "pre_horn",
        "paused": True,
        "radiant_nw_adv": 1_500,
        "radiant_nw": 12_345,
        "dire_nw": 10_845,
        "radiant_xp_adv": 777,
        "deaths_radiant": 4,
        "deaths_dire": 9,
        "top": {
            "top1_nw_adv": 900,
            "radiant_top1_nw_ratio": 0.31,
            "dire_top1_nw_ratio": 0.27,
            "top3_nw_adv": 2_400,
            "radiant_top3_nw_ratio": 0.58,
            "dire_top3_nw_ratio": 0.52,
        },
    }
    assert isinstance(game["phase"], str)
    assert isinstance(game["paused"], bool)
    assert isinstance(top["top1_nw_adv"], int)
    assert isinstance(top["radiant_top1_nw_ratio"], float)


def test_signal_record_times_come_from_journal_and_feed_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """recorded_at_utc is the journal's own now; feed receipt and arrived_at stay theirs."""
    archive_dir = tmp_path / "match"

    class FrozenUtc:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            del tz
            return datetime(2026, 8, 14, 12, 0, 7, 890123, tzinfo=UTC)

    monkeypatch.setattr(session_journal, "datetime", FrozenUtc)
    decision = replace(_pm_signal(0.45, 0.50), arrived_at=1_700_000_000.25)
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_signal(decision)
    journal.close()
    row = read_session_records(archive_dir)[0]
    assert row["recorded_at_utc"] == "2026-08-14T12:00:07.890123Z"
    assert row["feed_received_at_utc"] == "2026-08-14T12:00:00.100000Z"
    recorded = parse_utc(cast(str, row["recorded_at_utc"]))
    received = parse_utc(cast(str, row["feed_received_at_utc"]))
    assert recorded > received


def test_signal_record_is_one_line_one_write_one_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extended signal keeps the single-line write -> flush -> fsync contract."""
    archive_dir = tmp_path / "match"
    calls: list[str] = []

    def record_fsync(fd: int) -> None:
        calls.append("fsync")

    monkeypatch.setattr(archive_paths.os, "fsync", record_fsync)

    class RecordingHandle(io.TextIOWrapper):
        """TextIO stand-in recording the call order and the bytes written."""

        def __init__(self, path: Path) -> None:
            super().__init__(path.open("ab"), encoding="utf-8", newline="\n")
            self.text = ""

        def write(self, text: str) -> int:
            calls.append("write")
            self.text += text
            return super().write(text)

        def flush(self) -> None:
            calls.append("flush")
            super().flush()

        def fileno(self) -> int:
            calls.append("fileno")
            return super().fileno()

    journal = session_journal.SessionJournal(archive_dir)
    journal._writer._handle.close()
    handle = RecordingHandle(archive_dir / SESSION_JOURNAL_FILENAME)
    journal._writer._handle = handle
    journal.write_signal(_pm_signal(0.45, 0.50))
    assert calls == ["write", "flush", "fileno", "fsync"]
    assert handle.text.count("\n") == 1
    assert json.loads(handle.text)["kind"] == "signal"
    journal.close()
    assert calls == ["write", "flush", "fileno", "fsync", "flush"]
    assert handle.closed


def test_signal_record_keeps_zero_delta_as_a_model_result(tmp_path: Path) -> None:
    """raw_delta=0.0 is a computed value, not a skipped model."""
    journal = session_journal.SessionJournal(tmp_path / "match")
    journal.write_signal(replace(_pm_signal(0.45, 0.50), raw_delta=0.0))
    journal.close()
    row = read_session_records(tmp_path / "match")[0]
    assert row["model_evaluated"] is True
    assert row["raw_delta"] == 0.0


def test_signal_record_skip_writes_false_and_null_delta(tmp_path: Path) -> None:
    """A gated decision journals model_evaluated=false and raw_delta=null, one row."""
    archive_dir = tmp_path / "match"
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_signal(
        replace(
            _pm_signal(0.45, 0.50),
            radiant_fair=None,
            yes_fair=None,
            reason=SignalReason.PAUSED,
            model_evaluated=False,
            raw_delta=None,
        )
    )
    journal.write_error("decision", "ModelPredictionError")
    journal.close()
    records = read_session_records(archive_dir)
    signals = [record for record in records if record["kind"] == "signal"]
    assert len(signals) == 1
    assert signals[0]["model_evaluated"] is False
    assert signals[0]["raw_delta"] is None
    assert signals[0]["radiant_fair"] is None
    assert signals[0]["yes_fair"] is None
    assert records[-1]["kind"] == "trading_error"


def test_legacy_signal_without_new_fields_reads_beside_new_rows(tmp_path: Path) -> None:
    """A schema-7 signal without the new keys still reduces; mixed archives read."""
    archive_dir = tmp_path / "match"
    archive_dir.mkdir(parents=True)
    path = archive_dir / SESSION_JOURNAL_FILENAME
    legacy: SessionSignalRecord = {
        "kind": "signal",
        "venue": "polymarket",
        "second": 76,
        "yes_best_bid": 0.50,
        "yes_best_ask": 0.52,
        "yes_mid": 0.51,
        "no_best_bid": 0.48,
        "no_best_ask": 0.50,
        "no_mid": 0.49,
        "market_p_radiant": 0.51,
        "market_radiant_prior": 0.5,
        "radiant_fair": 0.55,
        "yes_fair": 0.55,
        "reason": "model",
        "entry_block": "none",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_signal(_pm_signal(0.45, 0.50))
    journal.close()
    records = list(iter_json_objects(path))
    assert len(records) == 2
    assert records[0].get("recorded_at_utc") is None
    assert records[0].get("feed_source") is None
    assert records[0].get("feed_received_at_utc") is None
    assert records[0].get("game_snapshot") is None
    assert records[0].get("model_evaluated") is None
    assert records[0].get("raw_delta") is None
    reduced = _reduce_journal(records, _LiveTokens(YES_TOKEN, NO_TOKEN, 0), FeedClock({}, None))
    assert reduced.yes.mid.values == [0.51, 0.45]


def test_scan_open_round_counts_only_polymarket_fills(tmp_path: Path) -> None:
    """A leftover kalshi fill line is skipped; PM sizes and stamps come from PM rows."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    _write_fill(journal, YES_TOKEN, Side.BUY, 10.0, "2026-08-19T00:01:00.000000Z")
    journal.close()
    kalshi_fill = {
        "kind": "fill",
        "venue": "kalshi",
        "ticker": _KALSHI_TICKER,
        "count": "5",
        "position_after": "5",
        "second": 12,
        "ts_utc": "2026-08-19T00:02:00Z",
    }
    path = archive_dir / "session.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(kalshi_fill) + "\n")
    resumed = session_journal.SessionJournal(archive_dir)
    scanned = resumed.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    resumed.close()
    expected = parse_utc("2026-08-19T00:01:00.000000Z").timestamp()
    assert scanned == session_journal.JournalRound(10.0, 0.0, expected)


def test_scan_open_round_unknown_venue_is_corrupt(tmp_path: Path) -> None:
    """A fill with an unknown venue disables trading like a foreign token."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    journal.close()
    path = archive_dir / "session.jsonl"
    fill = {
        "kind": "fill",
        "venue": "binance",
        "token_id": YES_TOKEN,
        "side": "BUY",
        "price": 0.4,
        "size": 10.0,
        "is_maker": True,
        "position_after": 10.0,
        "net_cash": -4.0,
        "second": 12,
        "ts_utc": "2026-08-19T00:01:00.000000Z",
        "fill_key": "k",
    }
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(fill) + "\n", encoding="utf-8")
    resumed = session_journal.SessionJournal(archive_dir)
    with pytest.raises(
        session_types.TradingDisabled, match="session journal open round cannot be read"
    ):
        resumed.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    resumed.close()


def test_tick_size_change_has_no_venue(tmp_path: Path) -> None:
    """tick_size_change is not in the venue set."""
    journal = session_journal.SessionJournal(tmp_path / "match")
    journal.write_tick_change("0.01", "0.001")
    journal.close()
    row = read_session_records(tmp_path / "match")[0]
    assert row["kind"] == "tick_size_change"
    assert "venue" not in row


def test_append_late_fill_appends_to_a_closed_journal(tmp_path: Path) -> None:
    """append_late_fill opens a closed session.jsonl, writes one row, and closes."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    journal.write_end("finished", None)
    journal.close()
    fill = Fill(YES_TOKEN, Side.SELL, 0.57, 17.86, "k1", 1_800_000_000.0, is_maker=True)
    session_journal.append_late_fill(
        archive_dir, fill, 0.0, 10.18, "2026-09-05T09:16:58.000000Z", "k1", "rest_backfill"
    )
    records = read_session_records(archive_dir)
    assert records[-1]["kind"] == "late_fill"
    assert records[-1]["fill_key"] == "k1"
    assert records[-1]["source"] == "rest_backfill"
    assert records[-1]["side"] == "SELL"


def test_scan_open_round_reads_late_fill(tmp_path: Path) -> None:
    """A late_fill BUY is an open-round fill, same as kind fill."""
    archive_dir = tmp_path / "match"
    start = build_discovered().with_model(ModelReference(name="m", trained_at="t"))
    journal = session_journal.SessionJournal(archive_dir)
    journal.write_start(start, "commit-1", None, clip=_CLIP)
    journal.close()
    ts_utc = "2026-08-19T00:01:00.000000Z"
    fill = Fill(YES_TOKEN, Side.BUY, 0.40, 10.0, "k", 1.0, is_maker=True)
    session_journal.append_late_fill(archive_dir, fill, 10.0, -4.0, ts_utc, "k", "user_ws")
    resumed = session_journal.SessionJournal(archive_dir)
    scanned = resumed.scan_open_round(YES_TOKEN, NO_TOKEN, 5.0)
    resumed.close()
    expected = parse_utc(ts_utc).timestamp()
    assert scanned == session_journal.JournalRound(10.0, 0.0, expected)


_COMMIT = "41b3b3cd97af04a9ec711df4c177b76a6f90bed3"


def test_read_git_head_commit_follows_a_loose_branch_ref(tmp_path: Path) -> None:
    """HEAD names a branch; the loose ref file holds the commit."""
    (tmp_path / "refs" / "heads").mkdir(parents=True)
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "refs" / "heads" / "main").write_text(f"{_COMMIT}\n")
    assert session_journal.read_git_head_commit(tmp_path) == _COMMIT


def test_read_git_head_commit_finds_a_packed_branch_ref(tmp_path: Path) -> None:
    """After git gc the branch lives only in packed-refs, next to peeled tag lines."""
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted \n"
        f"{'a' * 40} refs/heads/feature\n"
        f"{_COMMIT} refs/heads/main\n"
        f"{'b' * 40} refs/tags/v1\n"
        f"^{'c' * 40}\n"
    )
    assert session_journal.read_git_head_commit(tmp_path) == _COMMIT


def test_read_git_head_commit_reads_a_detached_head(tmp_path: Path) -> None:
    """A rollback via `git checkout <sha>` leaves the commit itself in HEAD."""
    (tmp_path / "HEAD").write_text(f"{_COMMIT}\n")
    assert session_journal.read_git_head_commit(tmp_path) == _COMMIT


def test_read_git_head_commit_returns_unknown_without_git_dir(tmp_path: Path) -> None:
    """No mounted .git yields the fixed unknown sentinel."""
    missing_git_dir = tmp_path / ".git"
    assert session_journal.read_git_head_commit(missing_git_dir) == "unknown"


def test_read_git_head_commit_returns_unknown_for_an_absent_branch(tmp_path: Path) -> None:
    """HEAD names a branch that has no loose ref and no packed-refs line."""
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "packed-refs").write_text(f"{'a' * 40} refs/heads/feature\n")
    assert session_journal.read_git_head_commit(tmp_path) == "unknown"
