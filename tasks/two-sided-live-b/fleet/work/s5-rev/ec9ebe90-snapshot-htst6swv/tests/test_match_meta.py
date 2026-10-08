"""Tests for the per-match metadata writer."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from grid_widget_fixtures import SCOREBOARD, SERIES_TABLE, wrap

from shared.utils.match_time import parse_utc
from shared.utils.top_players import ZERO_TOP
from trader import match_meta
from trader.bindings import (
    MarketReference,
    MatchStart,
    ModelReference,
    SessionPnl,
    TeamSides,
)
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import FEED_GONE_FRAME, GridFrameReducer
from trader.grid_widgets import clock_stamp_unix_seconds, parse_frame
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, MatchPhase
from trader.match_meta import (
    finalize_match,
    inspect_match_archive,
    match_has_final,
    pin_horn_from_event,
    read_feed_pin,
    read_finalized_match,
    write_match_start,
)
from trader.paths import GRID_STATE_ARCHIVE_FILENAME

MATCH_ID = "8944931337"
# Unix second of 2026-08-14T12:01:30Z, the horn the plan example anchors.
HORN_UNIX = 1_786_708_890
LOBBY_LEAD_SECONDS = 1_035
LOBBY_UNIX = HORN_UNIX - LOBBY_LEAD_SECONDS
META_FILENAME = "match.json"


def iso_z(unix_seconds: int) -> str:
    """Format a Unix epoch second as UTC ISO-8601 with a Z suffix."""
    return (
        datetime.fromtimestamp(unix_seconds, tz=UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build_match_start() -> MatchStart:
    """Build the fixed discovery/session context of the plan example."""
    return MatchStart(
        match_id=MATCH_ID,
        game="dota",
        steam_match_id=MATCH_ID,
        league_id=19719,
        tournament=None,
        sides=TeamSides(radiant="Aurora Gaming", dire="Team Yandex"),
        map_number=2,
        market=MarketReference(
            condition_id="0x8f2c3d",
            market_slug="aurora-vs-yandex-game-2",
            event_slug="aurora-vs-yandex",
            yes_token_id="123456",
            no_token_id="789012",
            yes_is_radiant=True,
            outcome_0_name="Aurora Gaming",
            outcome_1_name="Team Yandex",
            tick_size="0.001",
            min_order_size="5",
            neg_risk=False,
            grid_series_id=None,
        ),
        model=ModelReference(name="20260814T165239Z", trained_at="2026-08-14T16:52:39Z"),
    )


def _phase_from_steam_state(game_state: int) -> MatchPhase:
    """Map a Steam state number the way the Steam adapter does."""
    if game_state == 4:
        return MatchPhase.PRE_HORN
    if game_state == 5:
        return MatchPhase.IN_PROGRESS
    if game_state == 6:
        return MatchPhase.FINISHED
    return MatchPhase.PRE_MATCH


def build_first_event(
    second: int = -90,
    received_at_utc: str = "2026-08-14T12:00:00.123456Z",
) -> FeedEvent:
    """Build a first feed event whose horn lands on HORN_UNIX."""
    relative_timestamp = 1_000
    snapshot = GameSnapshot(
        second=second,
        server_timestamp=relative_timestamp,
        phase=MatchPhase.IN_PROGRESS,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top=ZERO_TOP,
        paused=False,
    )
    return FeedEvent(
        snapshot=snapshot,
        received_at_utc=received_at_utc,
        source=FeedSource.GRID,
        horn_unix_seconds=HORN_UNIX,
        yes_is_radiant=True,
    )


def read_meta(live_paper_dir: Path) -> dict[str, Any]:
    """Parse the persisted match.json."""
    meta_path = live_paper_dir / MATCH_ID / META_FILENAME
    return cast(dict[str, Any], json.loads(meta_path.read_text(encoding="utf-8")))


def prepare_finished_match(live_paper_dir: Path) -> None:
    """Write a GRID start document and a terminal GRID archive for one match."""
    write_match_start(_grid_start(), _grid_first_event())
    _write_grid_archive(
        live_paper_dir,
        [
            wrap("series_scoreboard_v2", 0, SCOREBOARD),
            wrap("series_table", 8, SERIES_TABLE),
            FEED_GONE_FRAME,
        ],
    )


def test_start_document_has_exact_schema_and_derived_join_horn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start document has the exact key set; join/horn come only from the first event."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    start = build_match_start()
    event = build_first_event()

    write_match_start(start, event)

    assert (live_paper_dir / MATCH_ID / META_FILENAME).is_file()
    document = read_meta(live_paper_dir)
    assert set(document) == {
        "schema_version",
        "match_id",
        "steam_match_id",
        "server_steam_id",
        "league_id",
        "tournament",
        "teams",
        "map_number",
        "joined_at_second",
        "joined_at_utc",
        "horn_at_utc",
        "market",
        "game",
        "model",
        "feed_source",
        "steam_delay_s",
        "steam_observed_lag_s",
        "grid_delay_s",
        "oddin_match_id",
        "oddin_delay_s",
        "record_only",
        "final",
    }
    assert document["schema_version"] == 9
    # The canonical id comes from MatchStart, never from the null payload id.
    assert document["match_id"] == MATCH_ID
    assert document["game"] == "dota"
    assert isinstance(document["match_id"], str)
    assert document["steam_match_id"] == MATCH_ID
    assert document["server_steam_id"] is None
    assert document["league_id"] == 19719
    assert document["tournament"] is None
    assert document["teams"] == {"radiant": start.sides.radiant, "dire": start.sides.dire}
    assert set(document["teams"]) == {"radiant", "dire"}
    assert document["map_number"] == 2
    assert document["joined_at_second"] == -90
    assert document["joined_at_utc"] == "2026-08-14T12:00:00.123456Z"
    assert document["horn_at_utc"] == iso_z(HORN_UNIX) == "2026-08-14T12:01:30Z"
    assert document["feed_source"] == "grid"
    assert document["steam_delay_s"] is None
    assert document["steam_observed_lag_s"] is None
    assert document["grid_delay_s"] is None
    assert document["oddin_match_id"] is None
    assert document["oddin_delay_s"] is None
    assert document["final"] is None

    market = document["market"]
    assert set(market) == {
        "condition_id",
        "market_slug",
        "event_slug",
        "yes_token_id",
        "no_token_id",
        "yes_is_radiant",
        "outcome_0_name",
        "outcome_1_name",
        "tick_size",
        "min_order_size",
        "neg_risk",
        "grid_series_id",
    }
    assert market["condition_id"] == start.market.condition_id
    assert market["market_slug"] == start.market.market_slug
    assert market["event_slug"] == start.market.event_slug
    assert market["yes_token_id"] == start.market.yes_token_id
    assert market["no_token_id"] == start.market.no_token_id
    assert market["yes_is_radiant"] is True
    assert market["outcome_0_name"] == start.market.outcome_0_name
    assert market["outcome_1_name"] == start.market.outcome_1_name
    assert market["tick_size"] == "0.001"
    assert isinstance(market["tick_size"], str)
    assert market["min_order_size"] == "5"
    assert isinstance(market["min_order_size"], str)
    assert market["neg_risk"] is False
    assert market["grid_series_id"] is None

    assert set(document["model"]) == {"name", "trained_at"}
    assert document["model"]["name"] == "20260814T165239Z"
    assert document["model"]["trained_at"] == "2026-08-14T16:52:39Z"

    assert "kalshi" not in document


def test_finalize_without_pnl_serializes_json_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No PnL handoff means `pnl: null`, not zeros and not a missing field."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)

    finalize_match(MATCH_ID, None)

    final = read_meta(live_paper_dir)["final"]
    assert final["pnl"] is None
    assert "pnl" in final


def test_finalize_zero_pnl_remains_numeric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid zero PnL handoff stays two numeric 0.0 values."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)

    finalize_match(MATCH_ID, SessionPnl(0.0, 0.0))

    final = read_meta(live_paper_dir)["final"]
    assert final["pnl"] == {"realized_pnl_usdc": 0.0, "unrealized_pnl_usdc": 0.0}


@pytest.mark.parametrize(
    "session_pnl",
    [
        pytest.param(SessionPnl(float("nan"), 0.0), id="realized-nan"),
        pytest.param(SessionPnl(0.0, float("inf")), id="unrealized-inf"),
        pytest.param(SessionPnl(float("-inf"), 0.0), id="realized-neg-inf"),
    ],
)
def test_finalize_rejects_non_finite_pnl_and_leaves_start_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_pnl: SessionPnl
) -> None:
    """Non-finite PnL values raise before any write; the start document is untouched."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)
    start_document = read_meta(live_paper_dir)

    with pytest.raises(ValueError, match="finite"):
        finalize_match(MATCH_ID, session_pnl)

    assert read_meta(live_paper_dir) == start_document


@pytest.mark.parametrize(
    "match_id",
    [
        pytest.param("", id="empty"),
        pytest.param(".", id="dot"),
        pytest.param("..", id="dot-dot"),
        pytest.param("../outside", id="parent-traversal"),
        pytest.param("a/b", id="slash"),
        pytest.param("a\\b", id="backslash"),
        pytest.param("wallet", id="wallet"),
    ],
)
def test_invalid_match_ids_raise_before_filesystem_mutation_on_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_id: str
) -> None:
    """Empty, dot, traversal and separator ids raise ValueError before mkdir/open."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    bad_start = replace(build_match_start(), match_id=match_id)

    with pytest.raises(ValueError, match="match id"):
        write_match_start(bad_start, build_first_event())

    assert not live_paper_dir.exists()


def test_absolute_match_id_raises_before_mkdir_on_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute id cannot replace the archive root: nothing outside it is created."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    outside = tmp_path / "outside"
    bad_start = replace(build_match_start(), match_id=str(outside))

    with pytest.raises(ValueError, match="match id"):
        write_match_start(bad_start, build_first_event())

    assert not outside.exists()
    assert not live_paper_dir.exists()


def test_invalid_match_id_raises_before_filesystem_mutation_on_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalize guards the id too, before opening the archive or the metadata file."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)

    with pytest.raises(ValueError, match="match id"):
        finalize_match("../outside", None)

    assert not live_paper_dir.exists()


def test_restart_reuses_matching_start_and_keeps_original_join_horn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restarted process with the same binding keeps the first join/horn stamps."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    original = read_meta(live_paper_dir)

    write_match_start(
        build_match_start(),
        build_first_event(second=-60, received_at_utc="2026-08-14T12:00:30.000000Z"),
    )

    assert read_meta(live_paper_dir) == original
    assert original["joined_at_second"] == -90
    assert original["joined_at_utc"] == "2026-08-14T12:00:00.123456Z"


def test_restart_keeps_archive_tick_when_sidecar_tick_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-map tick or min-size change does not refuse the resume or rewrite match.json."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    original = read_meta(live_paper_dir)
    moved = replace(
        build_match_start(),
        market=replace(build_match_start().market, tick_size="0.01", min_order_size="1"),
    )

    write_match_start(moved, build_first_event())

    assert read_meta(live_paper_dir) == original


@pytest.mark.parametrize(
    "conflicting_start",
    [
        pytest.param(replace(build_match_start(), league_id=111), id="league"),
        pytest.param(replace(build_match_start(), tournament="Other Cup"), id="tournament"),
        pytest.param(replace(build_match_start(), steam_match_id="1"), id="steam-id"),
        pytest.param(replace(build_match_start(), map_number=3), id="map"),
        pytest.param(
            replace(
                build_match_start(),
                market=replace(build_match_start().market, condition_id="0xdead"),
            ),
            id="market",
        ),
        pytest.param(
            replace(
                build_match_start(),
                model=ModelReference(name="other", trained_at="2026-08-14T00:00:00Z"),
            ),
            id="model",
        ),
        pytest.param(replace(build_match_start(), game="lol"), id="game"),
    ],
)
def test_conflicting_immutable_start_input_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflicting_start: MatchStart
) -> None:
    """A restarted process cannot silently rebind any immutable start field."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    original = read_meta(live_paper_dir)

    with pytest.raises(ValueError, match="refusing to rebind"):
        write_match_start(conflicting_start, build_first_event())

    assert read_meta(live_paper_dir) == original


def test_start_after_finalize_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed document can never be rebound by a restarted process."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)
    finalize_match(MATCH_ID, None)
    finalized = read_meta(live_paper_dir)

    with pytest.raises(ValueError, match="already finalized"):
        write_match_start(build_match_start(), build_first_event())

    assert read_meta(live_paper_dir) == finalized


def test_finalize_is_idempotent_for_the_same_final_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-finalizing with the same computed block is a no-op, not a conflict."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)
    finalize_match(MATCH_ID, SessionPnl(1.25, -0.10))
    finalized = read_meta(live_paper_dir)

    finalize_match(MATCH_ID, SessionPnl(1.25, -0.10))

    assert read_meta(live_paper_dir) == finalized


def test_conflicting_finalize_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different completion must not silently overwrite the persisted one."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)
    finalize_match(MATCH_ID, SessionPnl(1.25, -0.10))
    finalized = read_meta(live_paper_dir)

    with pytest.raises(ValueError, match="different final block"):
        finalize_match(MATCH_ID, SessionPnl(2.0, 0.0))

    assert read_meta(live_paper_dir) == finalized


def test_failed_atomic_replace_leaves_original_start_json_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rename leaves the original parseable start document untouched."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    prepare_finished_match(live_paper_dir)
    start_document = read_meta(live_paper_dir)

    def failing_replace(source: object, target: object) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(match_meta.os, "replace", failing_replace)
    with pytest.raises(OSError, match="rename failed"):
        finalize_match(MATCH_ID, None)

    assert read_meta(live_paper_dir) == start_document
    assert start_document["final"] is None
    # The leftover temp is not read as metadata.
    assert (live_paper_dir / MATCH_ID / f".{META_FILENAME}.tmp").exists()


def test_finalize_without_terminal_archive_raises_and_leaves_final_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive that never reaches a terminal snapshot keeps `final: null`."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())
    _write_grid_archive(
        live_paper_dir,
        [wrap("series_scoreboard_v2", 0, SCOREBOARD), wrap("series_table", 8, SERIES_TABLE)],
    )

    with pytest.raises(ValueError, match="terminal"):
        finalize_match(MATCH_ID, None)

    assert read_meta(live_paper_dir)["final"] is None


def test_finalize_absent_archive_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finalizing a match without grid_state.jsonl raises instead of undercounting."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())

    with pytest.raises(FileNotFoundError):
        finalize_match(MATCH_ID, None)


def test_finalize_empty_archive_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty archive raises instead of publishing a zeroed final block."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())
    _write_grid_archive(live_paper_dir, [])

    with pytest.raises(ValueError, match="empty archive"):
        finalize_match(MATCH_ID, None)


def test_finalize_malformed_completed_archive_line_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed but unparseable record fails loudly instead of undercounting."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())
    archive_dir = live_paper_dir / MATCH_ID
    good_record = {
        "received_at_utc": _grid_received(),
        "frame": wrap("series_table", 8, SERIES_TABLE),
    }
    good_line = json.dumps(good_record, separators=(",", ":"))
    (archive_dir / GRID_STATE_ARCHIVE_FILENAME).write_text(
        good_line + "\n" + '{"broken": ', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="malformed archive record"):
        finalize_match(MATCH_ID, None)


def test_finalize_without_start_document_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalizing without a start document raises instead of inventing one."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    _write_grid_archive(live_paper_dir, [wrap("series_table", 8, SERIES_TABLE), FEED_GONE_FRAME])

    with pytest.raises(FileNotFoundError):
        finalize_match(MATCH_ID, None)


def test_match_start_is_discovered_match_plus_model() -> None:
    """US-011 pins the model onto a discovery result without copying binding fields."""
    start = build_match_start()
    rebuilt = start.with_model(start.model)
    assert rebuilt == start


def _clock_event(
    game_time: int,
    timestamp: int,
    game_state: int,
    start_timestamp: int,
) -> FeedEvent:
    """One feed event with an explicit lobby start_timestamp, not the horn identity."""
    return FeedEvent(
        snapshot=GameSnapshot(
            second=game_time,
            server_timestamp=timestamp,
            phase=_phase_from_steam_state(game_state),
            radiant_nw_adv=0,
            radiant_nw=0,
            dire_nw=0,
            radiant_xp_adv=0,
            deaths_radiant=0,
            deaths_dire=0,
            top=ZERO_TOP,
            paused=False,
        ),
        received_at_utc="2026-08-14T16:10:10.000000Z",
        source=FeedSource.GRID,
        horn_unix_seconds=start_timestamp + timestamp - game_time,
        yes_is_radiant=True,
    )


def test_draft_then_state_five_updates_horn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hero-select first tick must not keep lobby-start as horn after spawn/in-progress."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    draft = _clock_event(61, 61, 2, LOBBY_UNIX)
    write_match_start(build_match_start(), draft)
    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(LOBBY_UNIX)
    assert read_meta(live_paper_dir)["joined_at_second"] == 61

    pin_horn_from_event(MATCH_ID, _clock_event(1, LOBBY_LEAD_SECONDS + 1, 5, LOBBY_UNIX))

    document = read_meta(live_paper_dir)
    assert document["horn_at_utc"] == iso_z(HORN_UNIX)
    assert document["joined_at_second"] == 61
    assert document["joined_at_utc"] == "2026-08-14T16:10:10.000000Z"


def test_late_join_state_five_keeps_the_start_horn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Joining already in-progress pins the real horn; a later tick does not move it."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    first = _clock_event(0, LOBBY_LEAD_SECONDS, 5, LOBBY_UNIX)
    write_match_start(build_match_start(), first)
    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(HORN_UNIX)

    pin_horn_from_event(MATCH_ID, first)
    pin_horn_from_event(MATCH_ID, _clock_event(100, LOBBY_LEAD_SECONDS + 100, 5, LOBBY_UNIX))

    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(HORN_UNIX)


def _v1_start_document() -> dict[str, object]:
    """A leftover schema 1 match.json with no v2 source/delay keys."""
    start = build_match_start()
    return {
        "schema_version": 1,
        "match_id": start.match_id,
        "server_steam_id": "90123456789012345",
        "league_id": start.league_id,
        "teams": {"radiant": start.sides.radiant, "dire": start.sides.dire},
        "map_number": start.map_number,
        "joined_at_second": -90,
        "joined_at_utc": "2026-08-14T12:00:00.123456Z",
        "horn_at_utc": iso_z(HORN_UNIX),
        "market": {
            "condition_id": start.market.condition_id,
            "market_slug": start.market.market_slug,
            "event_slug": start.market.event_slug,
            "yes_token_id": start.market.yes_token_id,
            "no_token_id": start.market.no_token_id,
            "yes_is_radiant": start.market.yes_is_radiant,
            "outcome_0_name": start.market.outcome_0_name,
            "outcome_1_name": start.market.outcome_1_name,
            "tick_size": start.market.tick_size,
            "min_order_size": start.market.min_order_size,
            "neg_risk": start.market.neg_risk,
            "grid_series_id": start.market.grid_series_id,
        },
        "model": {"name": start.model.name, "trained_at": start.model.trained_at},
        "final": None,
    }


def test_v1_on_disk_refuses_start_and_leaves_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session start has no v1 converter; the leftover document stays untouched."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    archive_dir = live_paper_dir / MATCH_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    meta_path = archive_dir / META_FILENAME
    original = json.dumps(_v1_start_document(), indent=2) + "\n"
    meta_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        write_match_start(build_match_start(), build_first_event())

    assert meta_path.read_text(encoding="utf-8") == original


def test_feed_source_mismatch_refuses_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GRID archive cannot be continued from an Oddin first event."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    original = read_meta(live_paper_dir)
    oddin_event = replace(build_first_event(), source=FeedSource.ODDIN)

    with pytest.raises(ValueError, match="refusing to rebind"):
        write_match_start(build_match_start(), oddin_event)

    assert read_meta(live_paper_dir) == original


def test_later_grid_side_labels_reuse_the_original_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steam vs GRID team strings must not block resume of the same market."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    later = replace(
        build_match_start(),
        sides=TeamSides(radiant="Team Spirit Academy", dire="RE ARISE"),
    )
    write_match_start(later, build_first_event(second=-60))
    document = read_meta(live_paper_dir)
    assert document["joined_at_second"] == -90
    assert document["teams"]["radiant"] == build_match_start().sides.radiant


def _grid_start() -> MatchStart:
    """MatchStart pinned to the recorded Lynx/Klim map 1 GRID series."""
    start = build_match_start()
    return replace(
        start,
        map_number=1,
        sides=TeamSides(radiant="Team Lynx", dire="Klim Sani4"),
        market=replace(
            start.market,
            grid_series_id="2995964",
            outcome_0_name="Team Lynx",
            outcome_1_name="Klim Sani4",
        ),
    )


def _grid_received() -> str:
    """Receipt stamp matching the recorded widget fixture clock."""
    return "2026-08-24T11:07:54.653000Z"


def _write_grid_archive(live_paper_dir: Path, frames: list[str]) -> None:
    """Persist raw GRID socket frames as grid_state.jsonl."""
    archive_dir = live_paper_dir / MATCH_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    received = _grid_received()
    lines = [
        json.dumps({"received_at_utc": received, "frame": frame}, separators=(",", ":")) + "\n"
        for frame in frames
    ]
    (archive_dir / GRID_STATE_ARCHIVE_FILENAME).write_text("".join(lines), encoding="utf-8")


def _grid_first_event() -> FeedEvent:
    """First accepted GRID table tick from the recorded fixture."""
    reducer = GridFrameReducer(1, "Team Lynx", "Klim Sani4", GAME_PROFILES["dota"])
    now = parse_utc(_grid_received())
    board_frame = parse_frame(wrap("series_scoreboard_v2", 0, SCOREBOARD))
    assert reducer.reduce_frame(board_frame, now) is None
    event = reducer.reduce_frame(parse_frame(wrap("series_table", 8, SERIES_TABLE)), now)
    assert isinstance(event, FeedEvent)
    return event


def test_grid_finalize_uses_emitted_ticks_and_finished_map_won(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GRID final counts emitted events, ignores a repeated table, and takes Dire from won."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())
    other_map = json.loads(json.dumps(SERIES_TABLE))
    other_map["stateGroups"][1]["states"][0]["sequenceNumber"] = 2
    finished = json.loads(json.dumps(SCOREBOARD))
    finished["games"][0]["status"] = "finished"
    _write_grid_archive(
        live_paper_dir,
        [
            wrap("series_scoreboard_v2", 0, SCOREBOARD),
            wrap("series_table", 8, SERIES_TABLE),
            wrap("series_table", 60, SERIES_TABLE),
            wrap("series_table", 8, other_map),
            wrap("series_scoreboard_v2", 0, finished),
        ],
    )

    finalize_match(MATCH_ID, None)

    document = read_meta(live_paper_dir)
    final = document["final"]
    assert final["snapshot_count"] == 2
    assert final["winner"] == "dire"
    assert final["pause_seconds"] is None
    assert final["missing_seconds"] is None
    assert "pause_seconds" in final
    assert document["grid_delay_s"] == 8.0
    assert document["steam_observed_lag_s"] is None
    assert document["feed_source"] == "grid"
    assert document["server_steam_id"] is None
    expected_horn = clock_stamp_unix_seconds("2026-08-24T11:07:52.653Z") - 2647
    assert document["horn_at_utc"] == iso_z(expected_horn)
    assert final["duration_seconds"] == 2649


def test_grid_finalize_accepts_feed_gone_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live archive ending in the widget-gone sentinel still writes `final`."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(_grid_start(), _grid_first_event())
    _write_grid_archive(
        live_paper_dir,
        [
            wrap("series_scoreboard_v2", 0, SCOREBOARD),
            wrap("series_table", 8, SERIES_TABLE),
            FEED_GONE_FRAME,
        ],
    )

    finalize_match(MATCH_ID, None)

    document = read_meta(live_paper_dir)
    final = document["final"]
    assert final["snapshot_count"] == 2
    assert final["winner"] is None
    assert final["duration_seconds"] == 2649


def test_grid_native_start_writes_v3_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GRID-native match.json stores schema 9, the minted id, tournament, and null Steam fields."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    start = replace(
        _grid_start(),
        match_id="grid-2995964-m1",
        steam_match_id=None,
        league_id=None,
        tournament="Test Cup",
    )
    write_match_start(start, _grid_first_event())
    document = json.loads((live_paper_dir / start.match_id / META_FILENAME).read_text())
    assert document["schema_version"] == 9
    assert document["game"] == "dota"
    assert document["record_only"] is False
    assert document["match_id"] == "grid-2995964-m1"
    assert document["steam_match_id"] is None
    assert document["league_id"] is None
    assert document["tournament"] == "Test Cup"
    assert document["market"]["grid_series_id"] == "2995964"


def test_read_feed_pin_skips_foreign_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover v2 match.json is not a pin; select_feed must pick live."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    archive_dir = live_paper_dir / MATCH_ID
    archive_dir.mkdir(parents=True)
    (archive_dir / META_FILENAME).write_text(
        json.dumps({"schema_version": 2, "feed_source": "grid", "match_id": MATCH_ID}),
        encoding="utf-8",
    )
    assert read_feed_pin(MATCH_ID) is None


def test_legacy_steam_archive_reads_and_flags_its_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old feed_source=steam match.json still parses; its pin is flagged, never masked."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v3_start_document()
    document["feed_source"] = "steam"
    _write_raw_meta(live_paper_dir, document)

    owner = inspect_match_archive(live_paper_dir, MATCH_ID).owner
    assert owner is not None
    assert owner.pins_steam is True
    with pytest.raises(ValueError, match="steam"):
        read_feed_pin(MATCH_ID)
    with pytest.raises(ValueError, match="different feed source"):
        write_match_start(build_match_start(), build_first_event())


def _v3_start_document() -> dict[str, object]:
    """A leftover schema 3 match.json: today's 18 keys, no kalshi."""
    start = build_match_start()
    return {
        "schema_version": 3,
        "match_id": start.match_id,
        "steam_match_id": start.steam_match_id,
        "server_steam_id": "90123456789012345",
        "league_id": start.league_id,
        "tournament": start.tournament,
        "teams": {"radiant": start.sides.radiant, "dire": start.sides.dire},
        "map_number": start.map_number,
        "joined_at_second": -90,
        "joined_at_utc": "2026-08-14T12:00:00.123456Z",
        "horn_at_utc": iso_z(HORN_UNIX),
        "market": {
            "condition_id": start.market.condition_id,
            "market_slug": start.market.market_slug,
            "event_slug": start.market.event_slug,
            "yes_token_id": start.market.yes_token_id,
            "no_token_id": start.market.no_token_id,
            "yes_is_radiant": start.market.yes_is_radiant,
            "outcome_0_name": start.market.outcome_0_name,
            "outcome_1_name": start.market.outcome_1_name,
            "tick_size": start.market.tick_size,
            "min_order_size": start.market.min_order_size,
            "neg_risk": start.market.neg_risk,
            "grid_series_id": start.market.grid_series_id,
        },
        "model": {"name": start.model.name, "trained_at": start.model.trained_at},
        "feed_source": "grid",
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": None,
        "final": None,
    }


def _null_kalshi_block() -> dict[str, object]:
    """The nine-key kalshi object schema 4/5 files carry, with every bind null."""
    return {
        "event_ticker": None,
        "ticker": None,
        "series_ticker": "KXDOTA2MAP",
        "yes_outcome": None,
        "no_outcome": None,
        "yes_is_radiant": None,
        "price_level_structure": None,
        "tick_size": None,
        "reason": "pending",
    }


def _v4_start_document() -> dict[str, object]:
    """A leftover schema 4 match.json: v3 keys plus kalshi, no game."""
    document = _v3_start_document()
    document["schema_version"] = 4
    document["kalshi"] = _null_kalshi_block()
    return document


def _v5_start_document() -> dict[str, object]:
    """A leftover schema 5 match.json: v4 keys plus game."""
    document = _v4_start_document()
    document["schema_version"] = 5
    document["game"] = "dota"
    return document


def _write_raw_meta(live_paper_dir: Path, document: dict[str, object]) -> Path:
    """Write match.json bytes without going through the schema-4 writer."""
    archive_dir = live_paper_dir / MATCH_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    meta_path = archive_dir / META_FILENAME
    meta_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return meta_path


def test_schema_3_unfinished_resume_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unfinished schema-3 file resumes the PM session and never grows kalshi."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    original = json.dumps(_v3_start_document(), indent=2) + "\n"
    meta_path = _write_raw_meta(live_paper_dir, _v3_start_document())
    write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original
    loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "kalshi" not in loaded
    assert "game" not in loaded


def test_schema_3_pin_horn_does_not_grow_kalshi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pin_horn may update horn_at_utc on schema 3 and still omits kalshi."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v3_start_document()
    document["horn_at_utc"] = iso_z(LOBBY_UNIX)
    _write_raw_meta(live_paper_dir, document)
    pin_horn_from_event(MATCH_ID, _clock_event(1, LOBBY_LEAD_SECONDS + 1, 5, LOBBY_UNIX))
    after = read_meta(live_paper_dir)
    assert after["schema_version"] == 3
    assert "kalshi" not in after
    assert "game" not in after
    assert after["horn_at_utc"] == iso_z(HORN_UNIX)


def test_schema_3_finalized_boots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot scan still accepts a schema-3 document with a non-null final."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    archive_dir = live_paper_dir / MATCH_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / META_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 3,
                "match_id": MATCH_ID,
                "final": {"duration_seconds": 100, "winner": "radiant"},
                "market": {
                    "condition_id": "0xA",
                    "yes_token_id": "YES-A",
                    "no_token_id": "NO-A",
                },
            }
        ),
        encoding="utf-8",
    )
    finalized = read_finalized_match(live_paper_dir, MATCH_ID)
    assert finalized is not None
    assert finalized.match_id == MATCH_ID
    assert finalized.condition_id == "0xA"
    assert match_has_final(live_paper_dir, MATCH_ID) is True


def test_inspect_match_archive_reads_unfinished_and_foreign_as_unreadable(
    tmp_path: Path,
) -> None:
    """Unfinished start is an owner; garbage and id mismatch fail closed."""
    live_paper_dir = tmp_path / "live_paper"
    archive = live_paper_dir / MATCH_ID
    archive.mkdir(parents=True, exist_ok=True)
    (archive / META_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 3,
                "match_id": MATCH_ID,
                "final": None,
                "market": {"condition_id": "0xA"},
            }
        ),
        encoding="utf-8",
    )
    inspection = inspect_match_archive(live_paper_dir, MATCH_ID)
    assert inspection.unreadable is False
    assert inspection.owner is not None
    assert inspection.owner.condition_id == "0xA"
    assert inspection.owner.has_final is False
    (archive / META_FILENAME).write_text("{", encoding="utf-8")
    broken = inspect_match_archive(live_paper_dir, MATCH_ID)
    assert broken.unreadable is True
    assert broken.owner is None
    missing = inspect_match_archive(live_paper_dir, "grid-1-m1")
    assert missing.unreadable is False
    assert missing.owner is None


def test_schema_3_grid_pin_boots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A leftover schema-3 GRID pin is still a pin."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v3_start_document()
    document["feed_source"] = "grid"
    _write_raw_meta(live_paper_dir, document)
    pin = read_feed_pin(MATCH_ID)
    assert pin is not None
    assert pin.source == "grid"
    assert pin.oddin_match_id is None


def test_schema_4_unfinished_resume_does_not_add_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unfinished leftover schema-4 file resumes without growing game."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    original = json.dumps(_v4_start_document(), indent=2) + "\n"
    meta_path = _write_raw_meta(live_paper_dir, _v4_start_document())
    write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original
    loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "game" not in loaded
    assert loaded["kalshi"]["reason"] == "pending"


def test_schema_4_pin_horn_does_not_add_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pin_horn may update horn_at_utc on schema 4 and still omits game."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v4_start_document()
    document["horn_at_utc"] = iso_z(LOBBY_UNIX)
    _write_raw_meta(live_paper_dir, document)
    pin_horn_from_event(MATCH_ID, _clock_event(1, LOBBY_LEAD_SECONDS + 1, 5, LOBBY_UNIX))
    after = read_meta(live_paper_dir)
    assert after["schema_version"] == 4
    assert "game" not in after
    assert after["horn_at_utc"] == iso_z(HORN_UNIX)


def test_lol_start_writes_game_and_does_not_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LoL start writes schema 9 with game=lol; a matching resume does not rewrite."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    start = replace(build_match_start(), game="lol")
    write_match_start(start, build_first_event())
    document = read_meta(live_paper_dir)
    assert document["schema_version"] == 9
    assert document["game"] == "lol"
    assert document["record_only"] is False
    assert "kalshi" not in document
    meta_path = live_paper_dir / MATCH_ID / META_FILENAME
    original = meta_path.read_text(encoding="utf-8")
    write_match_start(start, build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


def test_record_only_marker_clears_on_trading_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record-only match.json resumed by a trading launch drops the marker."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    start = replace(build_match_start(), game="lol", record_only=True)
    write_match_start(start, build_first_event())
    assert read_meta(live_paper_dir)["record_only"] is True
    write_match_start(replace(start, record_only=False), build_first_event())
    document = read_meta(live_paper_dir)
    assert document["record_only"] is False
    assert document["schema_version"] == 9


def test_schema_6_missing_game_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema 6 without game fails the strict reader; defaulting is absence-only on 3/4."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    document = read_meta(live_paper_dir)
    del document["game"]
    meta_path = _write_raw_meta(live_paper_dir, document)
    original = meta_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("game", ["csgo", ""])
def test_schema_6_unknown_or_empty_game_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """Schema 6 rejects a missing GameProfile key and an empty string."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    document = read_meta(live_paper_dir)
    document["game"] = game
    meta_path = _write_raw_meta(live_paper_dir, document)
    original = meta_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


def test_schema_5_reads_as_pm_only_and_rewrites_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid schema-5 kalshi block validates, then drops; the file is not rewritten."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    original = json.dumps(_v5_start_document(), indent=2) + "\n"
    meta_path = _write_raw_meta(live_paper_dir, _v5_start_document())
    write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original
    pin = read_feed_pin(MATCH_ID)
    assert pin is not None
    assert pin.source == "grid"
    assert pin.oddin_match_id is None


def test_schema_5_broken_kalshi_block_is_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema-5 file with an unknown kalshi reason fails the reader, not a skip."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v5_start_document()
    cast(dict[str, object], document["kalshi"])["reason"] = "bound"
    meta_path = _write_raw_meta(live_paper_dir, document)
    original = meta_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


def test_schema_6_with_kalshi_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema 6 carrying a leftover kalshi key is corruption, not a tolerated extra."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    write_match_start(build_match_start(), build_first_event())
    document = read_meta(live_paper_dir)
    document["kalshi"] = _null_kalshi_block()
    meta_path = _write_raw_meta(live_paper_dir, document)
    original = meta_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


def test_schema_4_with_game_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema 4 with a game key fails exact keys; defaulting is absence-only."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v4_start_document()
    document["game"] = "dota"
    meta_path = _write_raw_meta(live_paper_dir, document)
    original = meta_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_match_start(build_match_start(), build_first_event())
    assert meta_path.read_text(encoding="utf-8") == original


def _v7_start_document() -> dict[str, object]:
    """A leftover schema 7 match.json: v6 keys plus the PGL-era bind fields."""
    document = _v3_start_document()
    document["schema_version"] = 7
    document["game"] = "dota"
    document["feed_source"] = "grid"
    document["pgl_channel"] = None
    document["pgl_match_id"] = None
    document["pgl_delay_s"] = None
    document["pgl_sides_match_steam"] = True
    return document


def test_schema_7_pin_still_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Viewer/trader can still parse a leftover v7 match.json."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    _write_raw_meta(live_paper_dir, _v7_start_document())
    pin = read_feed_pin(MATCH_ID)
    assert pin is not None
    assert pin.source == "grid"
    assert pin.oddin_match_id is None
    document = read_meta(live_paper_dir)
    assert document["pgl_channel"] is None
    assert document["pgl_match_id"] is None
    assert document["pgl_delay_s"] is None


def test_pgl_feed_source_pin_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A leftover feed_source=pgl document is invalid metadata, not a pin."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    document = _v7_start_document()
    document["feed_source"] = "pgl"
    document["pgl_match_id"] = MATCH_ID
    _write_raw_meta(live_paper_dir, document)
    assert read_feed_pin(MATCH_ID) is None


def test_oddin_start_writes_v8_probed_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oddin match.json stores the probed delay and Bitsler match id."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    start = replace(
        build_match_start(),
        oddin_match_id="od:match:3211324",
        oddin_delay_s=15,
    )
    event = replace(build_first_event(), source=FeedSource.ODDIN)
    write_match_start(start, event)
    document = read_meta(live_paper_dir)
    assert document["schema_version"] == 9
    assert document["feed_source"] == "oddin"
    assert document["oddin_match_id"] == "od:match:3211324"
    assert document["oddin_delay_s"] == 15
    assert document["steam_delay_s"] is None
    pin = read_feed_pin(MATCH_ID)
    assert pin is not None
    assert pin.source == "oddin"
    assert pin.oddin_match_id == "od:match:3211324"


def _oddin_start() -> MatchStart:
    """An Oddin start: probed delay, Bitsler match id."""
    return replace(
        build_match_start(),
        oddin_match_id="od:match:3211324",
        oddin_delay_s=15,
    )


@pytest.mark.parametrize(
    ("source", "pre_second", "start"),
    [
        (FeedSource.ODDIN, -90, _oddin_start()),
        (FeedSource.GRID, -47, build_match_start()),
    ],
)
def test_pin_horn_ignores_nonpositive_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: FeedSource,
    pre_second: int,
    start: MatchStart,
) -> None:
    """A pre-horn tick must not freeze horn_at_utc; the first second > 0 may."""
    live_paper_dir = tmp_path / "live_paper"
    monkeypatch.setattr(match_meta, "TRADER_DIR", live_paper_dir)
    early_horn = HORN_UNIX - 750
    pre = replace(
        build_first_event(second=pre_second),
        source=source,
        horn_unix_seconds=early_horn,
        snapshot=replace(
            build_first_event(second=pre_second).snapshot,
            second=pre_second,
            phase=MatchPhase.PRE_HORN,
        ),
    )
    write_match_start(start, pre)
    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(early_horn)

    pin_horn_from_event(MATCH_ID, pre)
    pin_horn_from_event(
        MATCH_ID,
        replace(
            pre,
            snapshot=replace(pre.snapshot, second=0, phase=MatchPhase.IN_PROGRESS),
            horn_unix_seconds=HORN_UNIX - 12,
        ),
    )
    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(early_horn)

    pin_horn_from_event(
        MATCH_ID,
        replace(
            pre,
            snapshot=replace(pre.snapshot, second=1, phase=MatchPhase.IN_PROGRESS),
            horn_unix_seconds=HORN_UNIX,
        ),
    )
    assert read_meta(live_paper_dir)["horn_at_utc"] == iso_z(HORN_UNIX)
