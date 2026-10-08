"""Shared builders for trader discovery and sidecar tests."""

import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import httpx
import pytest

from trader import discovery
from trader.bindings import DiscoveredMatch, MarketReference, TeamSides
from trader.discovery import MarketDiscovery
from trader.game_profile import GAME_PROFILES
from trader.grid_widgets import LIVE_STATUS, Scoreboard, TeamSide
from trader.oddin_types import OddinMatch
from trader.source_picker import GridProbeCycle
from trader.steam_client import SteamClient

SCAN_NOW = 1_800_000_000.0
NOW_EPOCH = time.time()


def sidecar_body(**overrides: object) -> dict[str, object]:
    """Build one valid collector-v1 map_winner sidecar document."""
    body: dict[str, object] = {
        "schemaVersion": 1,
        "eventId": "808454",
        "eventSlug": "dota2-aurora-secret",
        "conditionId": "0x1",
        "marketSlug": "dota2-aurora-secret-game1",
        "marketKind": "map_winner",
        "mapNumber": 1,
        "outcomes": [
            {"index": 0, "name": "Aurora", "tokenId": "TOKEN0"},
            {"index": 1, "name": "Team Secret", "tokenId": "TOKEN1"},
        ],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "tickSize": "0.001",
        "minOrderSize": "5",
        "negRisk": False,
        "gridSeriesId": "2974458",
    }
    body.update(overrides)
    return body


def series_winner_body(
    condition_id: str = "0xmatch", event_id: str = "808454"
) -> dict[str, object]:
    """Build one valid collector-v1 series_winner sidecar document."""
    body = sidecar_body(conditionId=condition_id, eventId=event_id, marketKind="series_winner")
    body["marketSlug"] = f"dota2-aurora-secret-match-{condition_id}"
    body["mapNumber"] = None
    body["tickSize"] = None
    body["minOrderSize"] = None
    body["gridSeriesId"] = None
    return body


def outcome(index: object, name: object, token: object) -> dict[str, object]:
    """Build one sidecar outcome block; types are loose to feed rejection cases."""
    return {"index": index, "name": name, "tokenId": token}


MALFORMED_SIDECAR_CASES: list[tuple[str, object]] = [
    ("top-level-array", [1, 2]),
    ("schema-missing", sidecar_body(schemaVersion=None)),
    ("schema-version-two", sidecar_body(schemaVersion=2)),
    ("schema-version-string", sidecar_body(schemaVersion="1")),
    ("schema-version-bool", sidecar_body(schemaVersion=True)),
    ("map-number-bool", sidecar_body(mapNumber=True)),
    ("map-number-zero", sidecar_body(mapNumber=0)),
    ("map-number-string", sidecar_body(mapNumber="1")),
    ("map-number-null", sidecar_body(mapNumber=None)),
    ("series-winner-with-map-number", sidecar_body(marketKind="series_winner")),
    ("unknown-market-kind", sidecar_body(marketKind="total_winner")),
    ("event-id-int", sidecar_body(eventId=808454)),
    ("event-id-empty", sidecar_body(eventId="")),
    ("outcome-count-one", sidecar_body(outcomes=[outcome(0, "Aurora", "TOKEN0")])),
    (
        "outcome-count-three",
        sidecar_body(
            outcomes=[
                outcome(0, "Aurora", "TOKEN0"),
                outcome(1, "Team Secret", "TOKEN1"),
                outcome(2, "Extra", "TOKEN2"),
            ]
        ),
    ),
    (
        "outcome-index-swapped",
        sidecar_body(
            outcomes=[outcome(1, "Aurora", "TOKEN0"), outcome(0, "Team Secret", "TOKEN1")]
        ),
    ),
    (
        "outcome-index-string",
        sidecar_body(
            outcomes=[outcome("0", "Aurora", "TOKEN0"), outcome("1", "Team Secret", "TOKEN1")]
        ),
    ),
    (
        "token-empty",
        sidecar_body(outcomes=[outcome(0, "Aurora", ""), outcome(1, "Team Secret", "TOKEN1")]),
    ),
    (
        "token-duplicate",
        sidecar_body(
            outcomes=[outcome(0, "Aurora", "TOKEN0"), outcome(1, "Team Secret", "TOKEN0")]
        ),
    ),
    (
        "name-empty",
        sidecar_body(outcomes=[outcome(0, "", "TOKEN0"), outcome(1, "Team Secret", "TOKEN1")]),
    ),
    (
        "name-duplicate",
        sidecar_body(outcomes=[outcome(0, "Aurora", "TOKEN0"), outcome(1, "Aurora", "TOKEN1")]),
    ),
    ("accepting-orders-string", sidecar_body(acceptingOrders="true")),
    ("tick-size-int", sidecar_body(tickSize=1)),
    ("active-string", sidecar_body(active="true")),
    ("condition-id-mismatch", sidecar_body(conditionId="0x2")),
]


def write_sidecar(
    root: Path,
    condition_id: str,
    body: object | None = None,
    mtime: float | None = None,
) -> Path:
    """Write one sidecar file, optionally pinning its mtime."""
    markets = root / "metadata" / "markets"
    markets.mkdir(parents=True, exist_ok=True)
    document = sidecar_body(conditionId=condition_id) if body is None else body
    path = markets / f"{condition_id}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def league_row(
    match_id: int,
    radiant: str = "Aurora Gaming",
    dire: str = "Team Secret",
    series_type: int | None = 0,
    radiant_wins: int = 0,
    dire_wins: int = 0,
) -> dict[str, object]:
    """Build one usable GetLiveLeagueGames row."""
    row: dict[str, object] = {
        "match_id": match_id,
        "league_id": 19719,
        "radiant_team": {"team_name": radiant},
        "dire_team": {"team_name": dire},
        "scoreboard": {"duration": 0.0},
        "radiant_series_wins": radiant_wins,
        "dire_series_wins": dire_wins,
    }
    if series_type is not None:
        row["series_type"] = series_type
    return row


class FakeSteamApi:
    """Deterministic Steam API double recording requests and serving fixed responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.league_status = 200
        self.league_content: bytes | None = None
        self.league_error: Exception | None = None

    @property
    def transport(self) -> httpx.MockTransport:
        """Build the MockTransport that feeds SteamClient."""
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.league_error is not None:
            raise self.league_error
        return httpx.Response(self.league_status, content=self.league_content, request=request)

    def set_league_games(self, *rows: dict[str, object]) -> None:
        """Serve one GetLiveLeagueGames response with the given rows."""
        self.league_content = json.dumps({"result": {"games": list(rows)}}).encode()


class FixedTime:
    """Module-level time stand-in returning one fixed wall-clock epoch."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        """Return the fixed epoch."""
        return self.now


class AlertRecorder:
    """Stand-in for notify_in_background recording every alert text."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


def patch_discovery_time(monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    """Point the discovery module's wall clock at a fixed epoch."""
    monkeypatch.setattr(discovery, "time", FixedTime(now))


def patch_alerts(_monkeypatch: pytest.MonkeyPatch) -> AlertRecorder:
    """Return an empty recorder; discovery no longer sends Telegram."""
    return AlertRecorder()


def make_scoreboard(
    map_number: int,
    radiant: str = "Aurora Gaming",
    dire: str = "Team Secret",
    series_format: str = "best-of-3",
    tournament: str = "Test Cup",
) -> Scoreboard:
    """One live scoreboard whose `map_number_from_scoreboard` is `map_number`."""
    return Scoreboard(
        series_status=LIVE_STATUS,
        series_format=series_format,
        tournament=tournament,
        game_label=f"Game {map_number}",
        game_number=map_number,
        active_game_number=map_number,
        game_status=LIVE_STATUS,
        clock_seconds=0,
        clock_ticking=False,
        occurred_at="2026-08-25T12:00:00Z",
        publish_delay=8,
        teams=(
            TeamSide(
                team_id="radiant-id",
                name=radiant,
                side="RADIANT",
                kills=0,
                maps_won=0,
                won=False,
            ),
            TeamSide(
                team_id="dire-id",
                name=dire,
                side="DIRE",
                kills=0,
                maps_won=0,
                won=False,
            ),
        ),
    )


def _no_grid_scoreboards(series_ids: Iterable[str]) -> GridProbeCycle:
    """Keep Steam's series-wins map in tests that do not mock GRID."""
    del series_ids
    return GridProbeCycle({}, frozenset())


def grid_maps_all(map_number: int) -> Callable[[Iterable[str]], GridProbeCycle]:
    """Stub probe that reports the same live map for every series it is asked about."""

    def probe(series_ids: Iterable[str]) -> GridProbeCycle:
        board = make_scoreboard(map_number)
        return GridProbeCycle({series_id: board for series_id in series_ids}, frozenset())

    return probe


def _no_oddin_matches() -> tuple[OddinMatch, ...]:
    """Tests that do not mock Oddin get an empty list."""
    return ()


def make_oddin_card(
    match_id: str = "od:match:3211324",
    home: str = "Aurora Gaming",
    away: str = "Team Secret",
) -> OddinMatch:
    """One open Disir card for discovery bind tests."""
    return OddinMatch(
        id=match_id,
        home_name=home,
        away_name=away,
        home_score=0,
        away_score=0,
        is_closed=False,
        start_timestamp="2026-09-26T12:00:00Z",
        end_timestamp="",
        tournament_id=14906,
        tournament_name="TI",
    )


def make_discovery(
    root: Path,
    api: FakeSteamApi,
    probe_grid_scoreboards: Callable[[Iterable[str]], GridProbeCycle] = _no_grid_scoreboards,
    list_oddin_matches: Callable[[], Sequence[OddinMatch]] = _no_oddin_matches,
    title_blacklist: tuple[str, ...] = (),
) -> MarketDiscovery:
    """Build one MarketDiscovery on the fake API and one test Steam key."""
    client = httpx.Client(transport=api.transport)
    return MarketDiscovery(
        root,
        SteamClient(client, ("test-key",)),
        probe_grid_scoreboards,
        list_oddin_matches,
        GAME_PROFILES["dota"],
        title_blacklist,
    )


def prime_happy_api(
    api: FakeSteamApi,
    match_id: int = 100,
    radiant: str = "Aurora Gaming",
    dire: str = "Team Secret",
    series_type: int | None = 0,
    radiant_wins: int = 0,
    dire_wins: int = 0,
) -> None:
    """Configure one API double for the standard one-game happy path."""
    api.set_league_games(
        league_row(
            match_id,
            radiant=radiant,
            dire=dire,
            series_type=series_type,
            radiant_wins=radiant_wins,
            dire_wins=dire_wins,
        )
    )


def discovery_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the formatted discovery log lines of the current test."""
    return [record.getMessage() for record in caplog.records if record.name == "trader.discovery"]


def sidecar_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the formatted sidecar-scanner log lines of the current test."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "trader.collector_sidecars"
    ]


def make_discovered_match(match_id: str = "111", condition_id: str = "0x1") -> DiscoveredMatch:
    """Build one minimal DiscoveredMatch for cadence tests."""
    return DiscoveredMatch(
        match_id=match_id,
        game="dota",
        steam_match_id=match_id,
        league_id=19719,
        tournament=None,
        sides=TeamSides(radiant="Aurora", dire="Team Secret"),
        map_number=1,
        market=MarketReference(
            condition_id=condition_id,
            market_slug="dota2-aurora-secret-game1",
            event_slug="dota2-aurora-secret",
            yes_token_id="TOKEN0",
            no_token_id="TOKEN1",
            yes_is_radiant=True,
            outcome_0_name="Aurora",
            outcome_1_name="Team Secret",
            tick_size="0.001",
            min_order_size="5",
            neg_risk=False,
            grid_series_id=None,
        ),
    )


def env_stub(value: str | None) -> Callable[[str], str | None]:
    """Build an env lookup returning one fixed value."""

    def lookup(name: str) -> str | None:
        return value

    return lookup
