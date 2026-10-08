"""LoL GRID-only discovery: no Steam, no gridSeriesId inheritance."""

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from lol_grid_widget_fixtures import LEC_SCOREBOARD, LEC_SERIES_ID, wrap
from trader_discovery_fixtures import (
    NOW_EPOCH,
    patch_discovery_time,
    sidecar_body,
    write_sidecar,
)

from shared.utils.json_io import write_json
from trader.discovery import MarketDiscovery
from trader.game_profile import GAME_PROFILES
from trader.grid_widgets import Scoreboard, parse_frame, read_map_scoreboard
from trader.lol_league_filter import read_event_league
from trader.source_picker import GridProbeCycle

LOL = GAME_PROFILES["lol"]


@pytest.fixture(autouse=True)
def write_lec_event(tmp_path: Path) -> None:
    """Existing discovery fixtures are from an allowed archived LEC event."""
    write_json(
        tmp_path / "metadata/events/lec-event.json",
        {"id": "lec-event", "eventMetadata": {"league": "LEC"}},
    )


def _lec_board() -> Scoreboard:
    """LEC map 2 scoreboard from the compact fixture."""
    frame = parse_frame(wrap("series_scoreboard_v2", 0, LEC_SCOREBOARD, LEC_SERIES_ID))
    board = read_map_scoreboard(frame.payload, 2)
    assert board is not None
    return board


def _lol_body(
    condition_id: str,
    grid_series_id: str | None,
    outcome_0: str = "GIANTX",
    outcome_1: str = "Natus Vincere",
) -> dict[str, object]:
    """One LoL map_winner sidecar; outcomes default to LEC GIANTX vs Natus Vincere."""
    return sidecar_body(
        conditionId=condition_id,
        eventId="lec-event",
        eventSlug="lol-navi-gx",
        marketSlug=f"lol-navi-gx-{condition_id}",
        mapNumber=2,
        gridSeriesId=grid_series_id,
        outcomes=[
            {"index": 0, "name": outcome_0, "tokenId": f"{condition_id}-0"},
            {"index": 1, "name": outcome_1, "tokenId": f"{condition_id}-1"},
        ],
    )


def _probe_lec(series_ids: Iterable[str]) -> GridProbeCycle:
    """Return the LEC map 2 board for every series id asked."""
    board = _lec_board()
    return GridProbeCycle({series_id: board for series_id in series_ids}, frozenset())


def make_lol_discovery(
    root: Path, probe: Callable[[Iterable[str]], GridProbeCycle]
) -> MarketDiscovery:
    """LoL discovery with no Steam client."""
    return MarketDiscovery(root, None, probe, lambda: (), LOL, ())


def test_lol_grid_emits_map_two_with_blue_red_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """LEC map 2 with gridSeriesId yields one LoL match; BLUE maps onto radiant."""
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    caplog.set_level(logging.DEBUG, logger="trader.discovery")

    matches = make_lol_discovery(tmp_path, _probe_lec).discover()

    assert len(matches) == 1
    match = matches[0]
    assert match.game == "lol"
    assert match.match_id == "grid-2966907-m2"
    assert match.map_number == 2
    assert match.steam_match_id is None
    assert match.sides.radiant == "GIANTX"
    assert match.sides.dire == "Natus Vincere"
    assert match.market.grid_series_id == LEC_SERIES_ID
    assert match.market.yes_is_radiant is True
    assert match.record_only is False
    assert "game=lol" in caplog.text
    assert str(tmp_path) in caplog.text


def test_lol_missing_grid_series_id_does_not_inherit_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LoL map without gridSeriesId does not inherit a sibling Game-N id."""
    write_sidecar(tmp_path, "0xmissing", _lol_body("0xmissing", None))
    write_sidecar(tmp_path, "0xhas", _lol_body("0xhas", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    matches = make_lol_discovery(tmp_path, _probe_lec).discover()

    cids = {match.market.condition_id for match in matches}
    assert "0xhas" in cids
    assert "0xmissing" not in cids


def test_lol_identical_sides_yields_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate BLUE infoText is a missing side: no LoL match."""
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    board = replace(
        _lec_board(),
        teams=tuple(replace(team, side="BLUE") for team in _lec_board().teams),
    )

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        return GridProbeCycle({series_id: board for series_id in series_ids}, frozenset())

    matches = make_lol_discovery(tmp_path, probe).discover()

    assert matches == ()


def test_lol_ambiguous_outcomes_yield_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outcome names that do not orient against GIANTX/NAVI emit nothing."""
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID, "Foo", "Bar"))
    patch_discovery_time(monkeypatch, NOW_EPOCH)

    matches = make_lol_discovery(tmp_path, _probe_lec).discover()

    assert matches == ()


def test_lol_steam_client_none_never_fetches_steam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligible LoL sidecar with steam_client=None does not construct a Steam fetch."""
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    fetches: list[str] = []

    def boom(client: object) -> object:
        del client
        fetches.append("steam")
        return ()

    monkeypatch.setattr("trader.discovery._fetch_live_league_games", boom)

    matches = make_lol_discovery(tmp_path, _probe_lec).discover()

    assert len(matches) == 1
    assert fetches == []


@pytest.mark.parametrize(
    "document,reason",
    [
        (None, "missing_event_file"),
        (b"{", "invalid_event_file"),
        (b"\xff", "invalid_event_file"),
        (b"[]", "invalid_event_file"),
        (b'{"id":"wrong", "eventMetadata":{"league":"LEC"}}', "event_id_mismatch"),
        (b'{"eventMetadata":{"league":"LEC"}}', "event_id_mismatch"),
        (b'{"id":"lec-event", "eventMetadata":{"league":""}}', "missing_league"),
    ],
)
def test_event_failure_retries_without_grid_or_repeated_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    document: bytes | None,
    reason: str,
) -> None:
    """Blocked events make no GRID call or session candidate, and recover on the next cycle."""
    path = tmp_path / "metadata/events/lec-event.json"
    if document is None:
        path.unlink()
    else:
        path.write_bytes(document)
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    write_sidecar(tmp_path, "0xsibling", _lol_body("0xsibling", None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    caplog.set_level(logging.INFO, logger="trader.lol_league_filter")
    with (
        patch("trader.lol_league_filter.read_event_league", wraps=read_event_league) as reader,
        patch("test_trader_lol_discovery._probe_lec", wraps=_probe_lec) as probe,
    ):
        discovery = make_lol_discovery(tmp_path, probe)
        assert discovery.discover() == ()
        assert discovery.discover() == ()
        assert reader.call_count == 2
        probe.assert_not_called()
        messages = [r.message for r in caplog.records if r.name == "trader.lol_league_filter"]
        assert len(messages) == 1
        assert reason in messages[0] and "event_id=lec-event" in messages[0]
        assert "league=" in messages[0]
        path.write_text(json.dumps({"id": "lec-event", "title": "LoL: A vs B (BO3) - LEC"}))
        assert discovery.discover()
        assert reader.call_count == 3
        probe.assert_called_once()


@pytest.mark.parametrize(
    "document",
    [
        b'{"id":"lec-event", "title":"LoL: A vs B (BO3) - LCK Cup"}',
        b'{"id":"lec-event", "eventMetadata":{"league":"LEC Versus"}, "title":"LoL: A vs B (BO3) - LEC"}',
    ],
)
def test_unwhitelisted_league_runs_record_only_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    document: bytes,
) -> None:
    """A known league outside the whitelist emits record_only matches and still probes GRID."""
    path = tmp_path / "metadata/events/lec-event.json"
    path.write_bytes(document)
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    write_sidecar(tmp_path, "0xsibling", _lol_body("0xsibling", None))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    caplog.set_level(logging.INFO, logger="trader.lol_league_filter")
    with (
        patch("trader.lol_league_filter.read_event_league", wraps=read_event_league) as reader,
        patch("test_trader_lol_discovery._probe_lec", wraps=_probe_lec) as probe,
    ):
        discovery = make_lol_discovery(tmp_path, probe)
        for _ in range(2):
            matches = discovery.discover()
            assert len(matches) == 1
            assert matches[0].market.condition_id == "0xlec"
            assert matches[0].record_only is True
        assert reader.call_count == 2
        assert probe.call_count == 2
        messages = [r.message for r in caplog.records if r.name == "trader.lol_league_filter"]
        assert len(messages) == 1
        assert "record-only" in messages[0] and "event_id=lec-event" in messages[0]
        path.write_text(json.dumps({"id": "lec-event", "title": "LoL: A vs B (BO3) - LEC"}))
        traded = discovery.discover()
        assert len(traded) == 1
        assert traded[0].record_only is False
        assert reader.call_count == 3


def test_skip_reason_change_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    path = tmp_path / "metadata/events/lec-event.json"
    path.unlink()
    discovery = make_lol_discovery(tmp_path, _probe_lec)
    caplog.set_level(logging.INFO, logger="trader.lol_league_filter")
    assert discovery.discover() == ()
    write_json(path, {"id": "lec-event", "eventMetadata": {"league": "LCK Cup"}})
    matches = discovery.discover()
    assert len(matches) == 1
    assert matches[0].record_only is True
    assert "missing_event_file" in caplog.text
    assert "record-only" in caplog.text


def _collector_event(event_id: object, league: str) -> dict[str, object]:
    """Collector-v1 event sidecar: envelope plus SDK `metadata.league`."""
    return {
        "schemaVersion": 1,
        "fetchedAtUs": 1,
        "source": "gamma",
        "tagId": "65",
        "event": {
            "id": event_id,
            "title": f"LoL: A vs B (BO5) - {league} Playoffs",
            "metadata": {"league": league},
        },
    }


def test_read_event_league_unwraps_collector_envelope(tmp_path: Path) -> None:
    """VPS files wrap Gamma under `event`; league is `metadata.league`, id may be int."""
    write_json(
        tmp_path / "metadata/events/987089.json", _collector_event(987089, "Hitpoint Masters")
    )
    observed = read_event_league(tmp_path, "987089")
    assert observed.league == "Hitpoint Masters"
    assert observed.reason is None


def test_collector_envelope_admits_whitelisted_league(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_json(tmp_path / "metadata/events/lec-event.json", _collector_event("lec-event", "LEC"))
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    assert make_lol_discovery(tmp_path, _probe_lec).discover()


def test_collector_envelope_records_non_whitelisted_league(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A collector-wrapped event with an unlisted league still emits record_only."""
    write_json(tmp_path / "metadata/events/lec-event.json", _collector_event("lec-event", "LRS"))
    write_sidecar(tmp_path, "0xlec", _lol_body("0xlec", LEC_SERIES_ID))
    patch_discovery_time(monkeypatch, NOW_EPOCH)
    caplog.set_level(logging.INFO, logger="trader.lol_league_filter")
    matches = make_lol_discovery(tmp_path, _probe_lec).discover()
    assert len(matches) == 1
    assert matches[0].record_only is True
    assert "record-only" in caplog.text
    assert "league='LRS'" in caplog.text
