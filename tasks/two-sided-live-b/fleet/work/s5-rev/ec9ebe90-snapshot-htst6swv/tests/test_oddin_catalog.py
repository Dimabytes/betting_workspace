"""Disir tournament parsing and the in-memory catalog."""

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import probe_disir_catalog as probe
import pytest

from trader.oddin_catalog import (
    REFRESH_EVERY_S,
    DisirCatalog,
    _tournament_row_active,  # pyright: ignore[reportPrivateUsage]
    decode_oddin_id,
    fetch_open_lol_matches,
    read_reply,
    sport_graphql_id,
    tournament_graphql_id,
)
from trader.oddin_client import require_brand_token, widget_config
from trader.oddin_types import OddinFeedError, OddinMatch

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
NOT_FOUND: dict[str, object] = {
    "errors": [{"message": "Not found"}],
    "data": {"dota2TournamentInfo": None},
}
NULL: dict[str, object] = {"data": {"dota2TournamentInfo": None}}
ERROR: dict[str, object] = {
    "errors": [{"message": "internal error"}],
    "data": {"dota2TournamentInfo": None},
}
FIXTURE = Path(__file__).parent / "fixtures" / "disir_tournament_info.json"


def _match(
    match_id: str,
    home: str,
    away: str,
    closed: bool,
    start: str | None,
    end: str | None,
    home_score: int | None,
    away_score: int | None,
) -> dict[str, object]:
    encoded = base64.b64encode(f"match/{match_id}".encode()).decode("ascii")
    return {
        "id": encoded,
        "plannedStartTimestamp": start,
        "startTimestamp": start,
        "endTimestamp": end,
        "isClosed": closed,
        "homeTeam": {"id": "h", "name": home},
        "awayTeam": {"id": "a", "name": away},
        "homeScore": home_score,
        "awayScore": away_score,
        "bestOfType": "BO3",
    }


def _tournament(
    numeric_id: int,
    name: str,
    start: str | None,
    end: str | None,
    matches: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "data": {
            "dota2TournamentInfo": {
                "tournament": {
                    "id": tournament_graphql_id(numeric_id),
                    "name": name,
                    "startTimestamp": start,
                    "endTimestamp": end,
                },
                "matches": matches,
            }
        }
    }


def _open(
    match_id: str, home: str, away: str, home_score: int, away_score: int
) -> dict[str, object]:
    return _match(match_id, home, away, False, "2026-09-26T09:00:00Z", None, home_score, away_score)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def _no_sleep(seconds: float) -> None:
    del seconds


class _StopRun(Exception):
    """Sentinel that ends the infinite catalog loop at a chosen point."""


def _listing_row(numeric_id: int) -> dict[str, object]:
    return {
        "id": tournament_graphql_id(numeric_id),
        "name": f"Cup {numeric_id}",
        "startTimestamp": "2026-09-19T00:00:00Z",
        "endTimestamp": "2026-09-20T00:00:00Z" if numeric_id == 14900 else "2026-09-27T00:00:00Z",
    }


class _Disir:
    def __init__(self) -> None:
        self.table = _table()
        self.rows = [_listing_row(14900), _listing_row(14903)]
        self.listing_failure: httpx.Response | Exception | None = None
        self.detail_failures: dict[int, httpx.Response | Exception] = {}
        self.operations: list[str] = []
        self.detail_ids: list[int] = []
        self.list_variables: list[dict[str, object]] = []

    def reply(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        operation = body["operationName"]
        self.operations.append(operation)
        assert request.url.host == "api-disir.oddin.gg"
        if operation == "Tournaments":
            self.list_variables.append(body["variables"])
            failure = self.listing_failure
            response = httpx.Response(200, json={"data": {"tournaments": self.rows}})
        else:
            assert operation == "Dota2TournamentInfo"
            encoded = body["variables"]["tournamentId"]
            numeric_id = int(decode_oddin_id(encoded).rsplit(":", 1)[-1])
            self.detail_ids.append(numeric_id)
            failure = self.detail_failures.get(numeric_id)
            response = httpx.Response(200, json=self.table.get(numeric_id, NOT_FOUND))
        if isinstance(failure, Exception):
            raise failure
        return failure if failure is not None else response

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.reply))


def _table() -> dict[int, dict[str, object]]:
    return {
        14900: _tournament(
            14900,
            "Old Cup",
            "2026-09-01T00:00:00Z",
            "2026-09-20T00:00:00Z",
            [
                _match(
                    "od:match:1",
                    "A",
                    "B",
                    True,
                    "2026-09-10T00:00:00Z",
                    "2026-09-10T02:00:00Z",
                    2,
                    0,
                )
            ],
        ),
        14901: NULL,
        14903: _tournament(
            14903,
            "PGL Wallachia",
            "2026-09-19T00:00:00Z",
            "2026-09-27T00:00:00Z",
            [
                _open("od:match:3232752", "Aurora Gaming", "Natus Vincere", 0, 1),
                _match(
                    "od:match:3232753",
                    "Xtreme Gaming",
                    "GamerLegion",
                    True,
                    "2026-09-26T08:00:00Z",
                    "2026-09-26T10:00:00Z",
                    2,
                    0,
                ),
            ],
        ),
    }


def _catalog(clock: _Clock) -> DisirCatalog:
    return DisirCatalog("token", now=clock, sleep=_no_sleep)


def test_parse_three_phases_empty_matches_and_error_bodies() -> None:
    encoded = tournament_graphql_id(14906)
    assert decode_oddin_id(encoded) == "od:tournament:14906"
    body = _tournament(
        14906,
        "Cup",
        "2026-09-19T00:00:00Z",
        "2026-09-27T00:00:00Z",
        [
            _match("od:match:1", "Home", "Away", False, None, None, None, None),
            _match("od:match:2", "Home", "Away", False, "2026-09-26T09:00:00Z", None, 0, 1),
            _match(
                "od:match:3",
                "Home",
                "Away",
                True,
                "2026-09-26T09:00:00Z",
                "2026-09-26T11:00:00Z",
                2,
                0,
            ),
        ],
    )
    reply = read_reply(14906, body)
    assert reply.kind == "tournament"
    parsed = reply.tournament
    assert parsed is not None and parsed.name == "Cup"
    assert [match.phase() for match in parsed.matches] == ["not_started", "live", "finished"]
    assert parsed.matches[0].id == "od:match:1"
    assert read_reply(1, NULL).kind == "null"
    assert read_reply(1, NOT_FOUND).kind == "not_found"
    assert read_reply(1, ERROR).kind == "error"
    no_dates = _tournament(14906, "Cup", "", "", [])
    assert read_reply(14906, no_dates).kind == "invalid"
    no_tournament: dict[str, object] = {"data": {"dota2TournamentInfo": {"matches": []}}}
    assert read_reply(14906, no_tournament).kind == "invalid"


def test_fixture_tournament_round_trip() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = document["responses"]
    row = next(item for item in rows if item["kind"] == "tournament")
    reply = read_reply(row["numeric_id"], row["body"])
    assert reply.kind == "tournament"
    parsed = reply.tournament
    assert parsed is not None
    assert parsed.numeric_id == row["numeric_id"]
    assert parsed.matches


def test_map_ended_on_close_end_timestamp_or_map_score() -> None:
    catalog = _catalog(_Clock())
    body = _tournament(
        14906,
        "Cup",
        "2026-09-19T00:00:00Z",
        "2026-09-27T00:00:00Z",
        [
            _match("od:match:1", "Home", "Away", False, "2026-09-26T09:00:00Z", None, 1, 0),
            _match(
                "od:match:2",
                "Home",
                "Away",
                False,
                "2026-09-26T09:00:00Z",
                "2026-09-26T11:00:00Z",
                1,
                1,
            ),
            _match(
                "od:match:3",
                "Home",
                "Away",
                True,
                "2026-09-26T09:00:00Z",
                "2026-09-26T11:00:00Z",
                2,
                1,
            ),
        ],
    )
    catalog._apply((read_reply(14906, body),), NOW)  # pyright: ignore[reportPrivateUsage]
    assert catalog.map_ended("od:match:1", 1) is True
    assert catalog.map_ended("od:match:1", 2) is False
    assert catalog.map_ended("od:match:2", 3) is True
    assert catalog.map_ended("od:match:3", 3) is True
    assert catalog.map_ended("od:match:missing", 1) is False


def test_open_matches_before_start_does_not_fetch() -> None:
    catalog = _catalog(_Clock())
    assert catalog.open_matches() == ()
    assert catalog.open_matches() is catalog.open_matches()


def test_refresh_lists_dota_window_and_skips_expired_and_closed() -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)

    asyncio.run(run())
    assert disir.operations == ["Tournaments", "Dota2TournamentInfo"]
    assert disir.detail_ids == [14903]
    assert disir.list_variables == [
        {
            "sportId": sport_graphql_id("od:sport:2"),
            "from": (NOW - timedelta(days=90)).isoformat(),
            "to": (NOW + timedelta(days=30)).isoformat(),
        }
    ]
    assert [match.id for match in catalog.open_matches()] == ["od:match:3232752"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": {"tournaments": None}}),
        httpx.Response(200, json={"data": {"tournaments": {}}}),
        httpx.Response(200, json={"errors": [{"message": "partial"}], "data": {"tournaments": []}}),
        httpx.Response(200, json={"errors": [{}], "data": {"tournaments": []}}),
        httpx.ReadTimeout("timeout"),
        OSError("connection failed"),
    ],
)
def test_listing_failure_preserves_memory_and_does_not_fetch_details(
    failure: httpx.Response | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            before = catalog.open_matches()
            disir.listing_failure = failure
            with caplog.at_level(logging.WARNING):
                assert not await catalog.refresh(client)
            assert catalog.open_matches() is before
            disir.listing_failure = None
            disir.table[14903] = ERROR
            assert await catalog.refresh(client)
            assert catalog.open_matches() == before

    asyncio.run(run())
    assert disir.detail_ids == [14903, 14903]
    assert "oddin catalog refresh failed listing" in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=ERROR),
        httpx.Response(200, json={"errors": [{}], **_table()[14903]}),
        httpx.Response(200, json=NULL),
        httpx.Response(200, json=NOT_FOUND),
        httpx.Response(200, json=_tournament(14903, "Broken", "", "", [])),
        httpx.ReadTimeout("timeout"),
        OSError("connection failed"),
    ],
)
def test_failed_detail_keeps_old_matches_while_neighbor_updates(
    failure: httpx.Response | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    disir = _Disir()
    disir.rows.append(_listing_row(14950))
    disir.table[14950] = _tournament(
        14950,
        "BLAST",
        "2026-09-20T00:00:00Z",
        "2026-10-11T00:00:00Z",
        [_open("od:match:9", "A", "B", 0, 0)],
    )
    clock = _Clock()
    catalog = _catalog(clock)

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            before = catalog.open_matches()[0]
            clock.advance(90)
            disir.detail_failures[14903] = failure
            disir.table[14950] = _tournament(
                14950,
                "BLAST",
                "2026-09-20T00:00:00Z",
                "2026-10-11T00:00:00Z",
                [_open("od:match:9", "A", "B", 1, 1)],
            )
            with caplog.at_level(logging.WARNING):
                assert await catalog.refresh(client)
            assert catalog.open_matches()[0] is before
            assert catalog.open_matches()[1].home_score == 1

    asyncio.run(run())
    assert disir.detail_ids == [14903, 14950, 14903, 14950]
    assert "match list stale tournament=14903 age_s=90" in caplog.text


def test_new_tournament_appears_and_missing_tournament_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            disir.rows = [_listing_row(14950)]
            disir.table[14950] = _tournament(
                14950,
                "BLAST",
                "2026-09-20T00:00:00Z",
                "2026-10-11T00:00:00Z",
                [_open("od:match:9", "A", "B", 0, 0)],
            )
            with caplog.at_level(logging.INFO):
                assert await catalog.refresh(client)

    asyncio.run(run())
    assert [match.id for match in catalog.open_matches()] == ["od:match:9"]
    assert "oddin catalog dropped tournaments: 14903" in caplog.text
    assert "oddin catalog new tournaments: 14950" in caplog.text
    assert "oddin catalog refresh tournaments=1 open=1" in caplog.text


@pytest.mark.parametrize("rows", [[], [_listing_row(14900)]])
def test_successful_empty_or_expired_listing_clears_stored_tournaments(
    rows: list[dict[str, object]],
) -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            disir.rows = rows
            assert await catalog.refresh(client)
            assert catalog.open_matches() == ()
            disir.rows = [_listing_row(14903)]
            disir.table[14903] = ERROR
            assert await catalog.refresh(client)
            assert catalog.open_matches() == ()

    asyncio.run(run())
    assert disir.detail_ids == [14903, 14903]


def test_failed_new_detail_recovers_next_refresh_and_duplicate_ids_fetch_once() -> None:
    disir = _Disir()
    disir.rows.append(_listing_row(14903))
    disir.detail_failures[14903] = httpx.Response(500)
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            assert catalog.open_matches() == ()
            disir.detail_failures.clear()
            assert await catalog.refresh(client)

    asyncio.run(run())
    assert disir.detail_ids == [14903, 14903]
    assert len(catalog.open_matches()) == 1


@pytest.mark.parametrize("encoded_id", ["", "broken", sport_graphql_id("od:sport:2")])
def test_invalid_active_id_preserves_catalog(encoded_id: str) -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
            before = catalog.open_matches()
            disir.rows[1]["id"] = encoded_id
            assert not await catalog.refresh(client)
            assert catalog.open_matches() is before

    asyncio.run(run())
    assert disir.detail_ids == [14903]


def test_details_run_in_parallel_and_publish_after_all_replies() -> None:
    disir = _Disir()
    catalog = _catalog(_Clock())

    async def run() -> None:
        async with disir.client() as client:
            assert await catalog.refresh(client)
        before = catalog.open_matches()
        disir.rows.append(_listing_row(14950))
        disir.table[14903] = _tournament(
            14903,
            "PGL",
            "2026-09-19T00:00:00Z",
            "2026-09-27T00:00:00Z",
            [_open("od:match:3232752", "Aurora", "Navi", 1, 1)],
        )
        disir.table[14950] = _tournament(
            14950,
            "BLAST",
            "2026-09-20T00:00:00Z",
            "2026-10-11T00:00:00Z",
            [_open("od:match:9", "A", "B", 0, 0)],
        )
        both_started = asyncio.Event()
        release = asyncio.Event()
        pending = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal pending
            if json.loads(request.content)["operationName"] == "Dota2TournamentInfo":
                pending += 1
                if pending == 2:
                    both_started.set()
                await release.wait()
            return disir.reply(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            refresh = asyncio.create_task(catalog.refresh(client))
            try:
                await asyncio.wait_for(both_started.wait(), timeout=1)
                assert catalog.open_matches() is before
            finally:
                release.set()
                assert await refresh
        assert [match.home_score for match in catalog.open_matches()] == [1, 0]

    asyncio.run(run())


def test_run_refreshes_immediately_and_retries_next_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disir = _Disir()
    disir.listing_failure = httpx.Response(500)
    clock = _Clock()
    refresh_times: list[float] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["operationName"] == "Tournaments":
            refresh_times.append(clock.now.timestamp())
        return disir.reply(request)

    def client_factory(timeout: float) -> httpx.AsyncClient:
        del timeout
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)
        disir.listing_failure = None if len(sleeps) == 1 else httpx.Response(500)
        if len(sleeps) == 3:
            raise _StopRun

    monkeypatch.setattr("trader.oddin_catalog.disir_client", client_factory)
    catalog = DisirCatalog("token", now=clock, sleep=sleeper)
    with pytest.raises(_StopRun):
        asyncio.run(catalog.run())
    assert refresh_times == [NOW.timestamp() + i * REFRESH_EVERY_S for i in range(3)]
    assert sleeps == [REFRESH_EVERY_S] * 3
    assert disir.detail_ids == [14903]
    assert len(catalog.open_matches()) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, False),
        ({}, False),
        ({"endTimestamp": "not a date"}, False),
        ({"endTimestamp": "2026-09-27T00:00:00"}, False),
        ({"endTimestamp": "2026-09-25T12:00:00Z"}, False),
        ({"endTimestamp": "2026-09-25T12:00:01Z"}, True),
        ({"endTimestamp": "2026-10-01T00:00:00Z"}, True),
    ],
)
def test_tournament_row_active(raw: object, expected: bool) -> None:
    assert _tournament_row_active(raw, NOW) is expected


def test_probe_fetches_only_listed_ids_and_writes_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disir = _Disir()
    disir.rows = [_listing_row(14903)]
    disir.rows[0]["endTimestamp"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    fixture_path = tmp_path / "details.json"

    def client_factory(timeout: float) -> httpx.AsyncClient:
        del timeout
        return disir.client()

    monkeypatch.setattr(probe, "disir_client", client_factory)
    monkeypatch.setattr(probe, "BASE_DIR", tmp_path)
    monkeypatch.setattr(probe, "FIXTURE_PATH", fixture_path)
    assert asyncio.run(probe.amain("secret-brand-token")) == 0
    document = json.loads(fixture_path.read_text())
    assert document["tournament_ids"] == [14903]
    assert document["responses"][0]["body"] == disir.table[14903]
    assert "secret-brand-token" not in fixture_path.read_text()
    assert disir.operations == ["Tournaments", "Dota2TournamentInfo"]


@pytest.mark.parametrize("failure", ["listing", "detail", "empty"])
def test_probe_failure_preserves_existing_fixture(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disir = _Disir()
    disir.rows = [_listing_row(14903)]
    disir.rows[0]["endTimestamp"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    fixture_path = tmp_path / "details.json"
    fixture_path.write_text("previous fixture")
    if failure == "listing":
        disir.listing_failure = httpx.Response(500)
    elif failure == "detail":
        disir.detail_failures[14903] = httpx.Response(500)
    else:
        disir.rows = []

    def client_factory(timeout: float) -> httpx.AsyncClient:
        del timeout
        return disir.client()

    monkeypatch.setattr(probe, "disir_client", client_factory)
    monkeypatch.setattr(probe, "FIXTURE_PATH", fixture_path)
    assert asyncio.run(probe.amain("secret-brand-token")) == 1
    assert fixture_path.read_text() == "previous fixture"


def _lol_client(calls: list[str]) -> httpx.AsyncClient:
    """Disir with one active and one expired LoL tournament; info for the active one."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["operationName"])
        if body["operationName"] == "Tournaments":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "tournaments": [
                            {
                                "id": tournament_graphql_id(14912),
                                "name": "WSCI",
                                "startTimestamp": "2026-09-20T00:00:00Z",
                                "endTimestamp": "2026-10-02T00:00:00Z",
                            },
                            {
                                "id": tournament_graphql_id(14900),
                                "name": "Old Cup",
                                "startTimestamp": "2026-09-01T00:00:00Z",
                                "endTimestamp": "2026-09-20T00:00:00Z",
                            },
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "lolTournamentInfo": {
                        "tournament": {"name": "WSCI"},
                        "matches": [
                            _open("od:match:3232729", "KT", "GL", 0, 0),
                            _match(
                                "od:match:1",
                                "A",
                                "B",
                                True,
                                "2026-09-25T09:00:00Z",
                                "2026-09-25T10:00:00Z",
                                1,
                                0,
                            ),
                        ],
                    }
                }
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_open_lol_matches_skips_expired_tournaments_and_closed_matches() -> None:
    calls: list[str] = []

    async def run() -> tuple[OddinMatch, ...]:
        async with _lol_client(calls) as client:
            return await fetch_open_lol_matches(client, "token", NOW)

    matches = asyncio.run(run())
    assert calls == ["Tournaments", "LolTournamentInfo"]
    assert [match.id for match in matches] == ["od:match:3232729"]
    assert matches[0].tournament_name == "WSCI"
    assert matches[0].phase() == "live"


def test_runtime_code_does_not_call_bitsler() -> None:
    root = Path(__file__).parents[1]
    hits = [
        str(path.relative_to(root))
        for folder in ("src", "scripts")
        for path in (root / folder).rglob("*.py")
        if "bitsler.com" in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def _no_token(name: str) -> None:
    del name
    return None


def test_missing_brand_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trader.oddin_client.env_value", _no_token)
    with pytest.raises(OddinFeedError, match="ODDIN_BRAND_TOKEN"):
        require_brand_token()
    with pytest.raises(OddinFeedError, match="ODDIN_BRAND_TOKEN"):
        widget_config("od:match:3232752")
