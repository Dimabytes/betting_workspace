"""In-memory Dota catalog from Disir tournament listing and details."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

import httpx

from shared.utils.json_read import as_map, read_str, try_int
from shared.utils.log import get_logger
from trader.oddin_client import (
    ODDIN_QUERY,
    REQUEST_TIMEOUT_S,
    direct_match_id,
    disir_client,
    graphql_headers,
)
from trader.oddin_types import DOTA_SPORT, LOL_SPORT, OddinFeedError, OddinMatch, OddinTournament

logger = get_logger(__name__)

REFRESH_EVERY_S = 60.0
ACTIVE_GRACE = timedelta(days=1)
TOURNAMENT_QUERY = (
    "query Dota2TournamentInfo($tournamentId: ID!) { "
    "dota2TournamentInfo(tournamentId: $tournamentId) { "
    "tournament { id name startTimestamp endTimestamp } "
    "matches { id plannedStartTimestamp startTimestamp endTimestamp isClosed "
    "homeTeam { id name } awayTeam { id name } homeScore awayScore bestOfType } } }"
)
DOTA_TOURNAMENT_LOOKBACK = timedelta(days=90)
DOTA_TOURNAMENT_LOOKAHEAD = timedelta(days=30)
LOL_TOURNAMENT_LOOKBACK = timedelta(days=45)
LOL_TOURNAMENT_LOOKAHEAD = timedelta(days=30)
TOURNAMENTS_QUERY = (
    "query Tournaments($sportId: ID!, $from: String, $to: String) { "
    "tournaments(sportId: $sportId, dateFrom: $from, dateTo: $to) { "
    "id name startTimestamp endTimestamp } }"
)
LOL_INFO_QUERY = (
    "query LolTournamentInfo($tournamentId: ID!) { "
    "lolTournamentInfo(tournamentId: $tournamentId) { "
    "tournament { name } "
    "matches { id plannedStartTimestamp startTimestamp endTimestamp isClosed "
    "homeTeam { id name } awayTeam { id name } homeScore awayScore bestOfType } } }"
)

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
ReplyKind = Literal["tournament", "null", "not_found", "invalid", "error"]


@dataclass(frozen=True)
class _Reply:
    """One tournament-id response. `tournament` is set only when kind is `tournament`."""

    numeric_id: int
    kind: ReplyKind
    tournament: OddinTournament | None


@dataclass(frozen=True)
class _StoredTournament:
    """Last good tournament payload and when it was loaded."""

    tournament: OddinTournament
    loaded_at: datetime


def tournament_graphql_id(numeric_id: int) -> str:
    """Base64 of `tournament/od:tournament:N`. A bare id is rejected by Disir."""
    raw = f"tournament/od:tournament:{numeric_id}"
    return base64.b64encode(raw.encode("ascii")).decode("ascii")


def sport_graphql_id(sport: str) -> str:
    """Base64 of `sport/od:sport:N`. A bare id is rejected by Disir."""
    return base64.b64encode(f"sport/{sport}".encode("ascii")).decode("ascii")


def decode_oddin_id(encoded: str) -> str:
    """`od:match:N` or `od:tournament:N` from a Disir base64 id. Empty when it is not one."""
    try:
        raw = base64.b64decode(encoded, validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return ""
    prefix, separator, value = raw.partition("/")
    if separator != "/" or not prefix or not value:
        return ""
    return value


def _error_messages(body: dict[str, object]) -> tuple[str, ...]:
    raw = body.get("errors")
    if not isinstance(raw, list):
        return ()
    messages: list[str] = []
    for item in cast(list[object], raw):
        mapped = as_map(item)
        if mapped is None:
            continue
        message = read_str(mapped, "message")
        if message:
            messages.append(message)
    return tuple(messages)


def _team_name(raw: object) -> str:
    mapped = as_map(raw)
    if mapped is None:
        return ""
    return read_str(mapped, "name")


def _parse_match(raw: object, tournament_id: int, tournament_name: str) -> OddinMatch | None:
    mapped = as_map(raw)
    if mapped is None:
        return None
    match_id = decode_oddin_id(read_str(mapped, "id"))
    if not match_id.startswith("od:match:"):
        return None
    return OddinMatch(
        id=match_id,
        home_name=_team_name(mapped.get("homeTeam")),
        away_name=_team_name(mapped.get("awayTeam")),
        home_score=try_int(mapped.get("homeScore")),
        away_score=try_int(mapped.get("awayScore")),
        is_closed=mapped.get("isClosed") is True,
        start_timestamp=read_str(mapped, "startTimestamp"),
        end_timestamp=read_str(mapped, "endTimestamp"),
        tournament_id=tournament_id,
        tournament_name=tournament_name,
    )


def _parse_matches(raw: object, tournament_id: int, tournament_name: str) -> tuple[OddinMatch, ...]:
    if not isinstance(raw, list):
        return ()
    matches: list[OddinMatch] = []
    for item in cast(list[object], raw):
        parsed = _parse_match(item, tournament_id, tournament_name)
        if parsed is not None:
            matches.append(parsed)
    return tuple(matches)


def _read_when(raw: object) -> datetime | None:
    """An aware ISO timestamp; anything missing, broken, or naive is unusable."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def read_reply(numeric_id: int, body: object) -> _Reply:
    """One pass over the body: the kind plus, for `tournament`, the parsed payload.

    Missing tournament objects or unusable dates are `invalid`. HTTP or
    GraphQL failures are `error`. Any reply without a tournament preserves
    that id's previous payload until a later refresh succeeds.
    """
    mapped = as_map(body)
    if mapped is None:
        return _Reply(numeric_id, "error", None)
    messages = _error_messages(mapped)
    if "Not found" in messages:
        return _Reply(numeric_id, "not_found", None)
    if mapped.get("errors"):
        return _Reply(numeric_id, "error", None)
    data = as_map(mapped.get("data"))
    if data is None:
        return _Reply(numeric_id, "error", None)
    info = data.get("dota2TournamentInfo")
    if info is None:
        return _Reply(numeric_id, "null", None)
    info_map = as_map(info)
    if info_map is None:
        return _Reply(numeric_id, "invalid", None)
    tournament_map = as_map(info_map.get("tournament"))
    if tournament_map is None:
        return _Reply(numeric_id, "invalid", None)
    starts_at = _read_when(tournament_map.get("startTimestamp"))
    ends_at = _read_when(tournament_map.get("endTimestamp"))
    if starts_at is None or ends_at is None:
        return _Reply(numeric_id, "invalid", None)
    name = read_str(tournament_map, "name")
    return _Reply(
        numeric_id,
        "tournament",
        OddinTournament(
            numeric_id=numeric_id,
            name=name,
            starts_at=starts_at,
            ends_at=ends_at,
            matches=_parse_matches(info_map.get("matches"), numeric_id, name),
        ),
    )


class DisirCatalog:
    """Refresh in the background; discovery reads the last published matches."""

    def __init__(self, token: str, now: Clock, sleep: Sleeper) -> None:
        self._token = token
        self._now = now
        self._sleep = sleep
        self._stored: tuple[_StoredTournament, ...] = ()
        self._open: tuple[OddinMatch, ...] = ()
        self._refreshed_at: datetime | None = None

    def open_matches(self) -> tuple[OddinMatch, ...]:
        """Open cards from the last refresh. No network."""
        return self._open

    def map_ended(self, match_id: str, map_number: int) -> bool:
        """True when the series is closed or its map score already counts this map.

        The live socket freezes on the last map frame, so the catalog is the end signal.
        A match that is not in the last refresh is not ended: a failed listing
        must not close a live map.
        """
        for item in self._stored:
            for match in item.tournament.matches:
                if match.id != match_id:
                    continue
                if match.is_closed or match.end_timestamp != "":
                    return True
                maps_played = (match.home_score or 0) + (match.away_score or 0)
                return maps_played >= map_number
        return False

    async def refresh_now(self) -> bool:
        """One refresh before the background loop, so boot can see closed series."""
        async with disir_client(REQUEST_TIMEOUT_S) as client:
            return await self.refresh(client)

    def _refresh_due(self) -> bool:
        """False when refresh_now already published inside this interval."""
        refreshed_at = self._refreshed_at
        if refreshed_at is None:
            return True
        return (self._now() - refreshed_at).total_seconds() >= REFRESH_EVERY_S

    async def run(self) -> None:
        """Refresh every minute. A refresh_now just before this waits out the interval."""
        async with disir_client(REQUEST_TIMEOUT_S) as client:
            while True:
                if self._refresh_due():
                    await self.refresh(client)
                await self._sleep(REFRESH_EVERY_S)

    async def refresh(self, client: httpx.AsyncClient) -> bool:
        """List active ids, fetch details in parallel, and publish one snapshot.

        A failed listing leaves all memory untouched. A failed detail keeps
        the last good payload for that id. Neither failure retries this cycle.
        """
        now = self._now()
        try:
            numeric_ids = await fetch_active_dota_tournament_ids(client, self._token, now)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("oddin catalog refresh failed listing: %s", exc)
            return False
        replies = await asyncio.gather(*(self._fetch(client, item) for item in numeric_ids))
        self._apply(replies, now)
        self._publish()
        self._refreshed_at = now
        logger.info(
            "oddin catalog refresh tournaments=%s open=%s", len(self._stored), len(self._open)
        )
        return True

    def _refreshed(
        self, item: _StoredTournament, reply: _Reply, now: datetime
    ) -> _StoredTournament:
        if reply.tournament is None:
            age = (now - item.loaded_at).total_seconds()
            logger.warning(
                "oddin catalog match list stale tournament=%s age_s=%.0f kind=%s",
                item.tournament.numeric_id,
                age,
                reply.kind,
            )
            return item
        return _StoredTournament(reply.tournament, now)

    def _apply(self, replies: Sequence[_Reply], now: datetime) -> None:
        previous = {item.tournament.numeric_id: item for item in self._stored}
        listed = {reply.numeric_id for reply in replies}
        removed = previous.keys() - listed
        if removed:
            logger.info(
                "oddin catalog dropped tournaments: %s",
                ",".join(str(item) for item in sorted(removed)),
            )
        stored: list[_StoredTournament] = []
        fresh: list[int] = []
        for reply in replies:
            item = previous.get(reply.numeric_id)
            if item is not None:
                stored.append(self._refreshed(item, reply, now))
            elif reply.tournament is not None:
                stored.append(_StoredTournament(reply.tournament, now))
                fresh.append(reply.numeric_id)
            else:
                logger.warning(
                    "oddin catalog detail failed tournament=%s kind=%s",
                    reply.numeric_id,
                    reply.kind,
                )
        self._stored = tuple(stored)
        if fresh:
            logger.info("oddin catalog new tournaments: %s", ",".join(str(item) for item in fresh))

    def _publish(self) -> None:
        self._open = tuple(
            match
            for item in self._stored
            for match in item.tournament.matches
            if not match.is_closed
        )

    async def _fetch(self, client: httpx.AsyncClient, numeric_id: int) -> _Reply:
        try:
            response = await client.post(
                ODDIN_QUERY,
                headers=graphql_headers(self._token),
                json={
                    "operationName": "Dota2TournamentInfo",
                    "query": TOURNAMENT_QUERY,
                    "variables": {"tournamentId": tournament_graphql_id(numeric_id)},
                },
            )
        except (httpx.HTTPError, OSError):
            return _Reply(numeric_id, "error", None)
        if response.status_code != 200:
            return _Reply(numeric_id, "error", None)
        try:
            payload = cast(object, json.loads(response.text))
        except json.JSONDecodeError:
            return _Reply(numeric_id, "error", None)
        return read_reply(numeric_id, payload)


def log_matches(matches: Sequence[OddinMatch], sport: str) -> None:
    """Print the open-match table used to pick a series."""
    logger.info("open %s matches: %s", sport, len(matches))
    for match in matches:
        logger.info(
            "  %s  %-12s %s vs %s  series=%s:%s  %s",
            match.id,
            match.phase(),
            match.home_name,
            match.away_name,
            match.home_score,
            match.away_score,
            match.tournament_name,
        )


def select_open_match(matches: Sequence[OddinMatch], selector: str) -> OddinMatch:
    """Pick the open match whose id or team name matches `selector`."""
    match_id = direct_match_id(selector)
    if match_id is not None:
        for match in matches:
            if match.id == match_id:
                return match
        raise OddinFeedError(f"no open match {match_id}")
    needle = selector.casefold()
    hits = [
        match
        for match in matches
        if needle in match.home_name.casefold() or needle in match.away_name.casefold()
    ]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = ", ".join(f"{match.id} {match.home_name} vs {match.away_name}" for match in hits)
        raise OddinFeedError(f"ambiguous selector {selector!r}: {names}")
    raise OddinFeedError(f"no open match matches {selector!r}")


def _tournament_row_active(raw: object, now: datetime) -> bool:
    """True while a `tournaments` row's end plus one day is still ahead."""
    mapped = as_map(raw)
    if mapped is None:
        return False
    ends_at = _read_when(mapped.get("endTimestamp"))
    return ends_at is not None and now < ends_at + ACTIVE_GRACE


async def _fetch_lol_tournament_matches(
    client: httpx.AsyncClient, token: str, raw: object
) -> tuple[OddinMatch, ...]:
    """`lolTournamentInfo` matches for one `tournaments` row; none when it fails."""
    row = as_map(raw) or {}
    encoded_id = read_str(row, "id")
    numeric_text = decode_oddin_id(encoded_id).rsplit(":", 1)[-1]
    if not encoded_id or not numeric_text.isdigit():
        return ()
    try:
        response = await client.post(
            ODDIN_QUERY,
            headers=graphql_headers(token),
            json={
                "operationName": "LolTournamentInfo",
                "query": LOL_INFO_QUERY,
                "variables": {"tournamentId": encoded_id},
            },
        )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("lol tournament matches failed id=%s: %s", encoded_id, exc)
        return ()
    if response.status_code != 200:
        logger.warning(
            "lol tournament matches failed id=%s status=%s", encoded_id, response.status_code
        )
        return ()
    try:
        body = cast(object, json.loads(response.text))
    except json.JSONDecodeError:
        return ()
    mapped = as_map(body) or {}
    messages = _error_messages(mapped)
    if messages:
        logger.warning("lol tournament matches failed id=%s: %s", encoded_id, messages)
        return ()
    data = as_map(mapped.get("data")) or {}
    info = as_map(data.get("lolTournamentInfo")) or {}
    tournament = as_map(info.get("tournament")) or {}
    name = read_str(tournament, "name") or read_str(row, "name")
    return _parse_matches(info.get("matches"), int(numeric_text), name)


async def fetch_tournament_rows(
    client: httpx.AsyncClient,
    token: str,
    sport: str,
    date_from: datetime,
    date_to: datetime,
) -> tuple[object, ...]:
    """Fetch a complete listing; errors and missing lists raise instead of clearing it."""
    response = await client.post(
        ODDIN_QUERY,
        headers=graphql_headers(token),
        json={
            "operationName": "Tournaments",
            "query": TOURNAMENTS_QUERY,
            "variables": {
                "sportId": sport_graphql_id(sport),
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            },
        },
    )
    response.raise_for_status()
    body = as_map(response.json())
    if body is None:
        raise OddinFeedError("tournaments response is not an object")
    if body.get("errors"):
        raise OddinFeedError(f"tournaments errors: {_error_messages(body)}")
    data = as_map(body.get("data")) or {}
    raw = data.get("tournaments")
    if not isinstance(raw, list):
        raise OddinFeedError("tournaments list is missing")
    return tuple(cast(list[object], raw))


async def fetch_active_dota_tournament_ids(
    client: httpx.AsyncClient, token: str, now: datetime
) -> tuple[int, ...]:
    """Active Dota ids with starts in the previous 90 or next 30 days.

    The listing filters by start date, so events that began over 90 days ago
    are outside this window even when they are still running.
    """
    rows = await fetch_tournament_rows(
        client,
        token,
        DOTA_SPORT,
        now - DOTA_TOURNAMENT_LOOKBACK,
        now + DOTA_TOURNAMENT_LOOKAHEAD,
    )
    numeric_ids: dict[int, None] = {}
    for raw in rows:
        if not _tournament_row_active(raw, now):
            continue
        row = as_map(raw) or {}
        decoded = decode_oddin_id(read_str(row, "id"))
        prefix = "od:tournament:"
        numeric_text = decoded.removeprefix(prefix)
        if not decoded.startswith(prefix) or not numeric_text.isdigit():
            raise OddinFeedError("active tournament has an invalid id")
        numeric_ids[int(numeric_text)] = None
    return tuple(numeric_ids)


async def fetch_open_lol_matches(
    client: httpx.AsyncClient, token: str, now: datetime
) -> tuple[OddinMatch, ...]:
    """Open LoL matches: list a wide start-date window, then fetch active details."""
    rows = await fetch_tournament_rows(
        client,
        token,
        LOL_SPORT,
        now - LOL_TOURNAMENT_LOOKBACK,
        now + LOL_TOURNAMENT_LOOKAHEAD,
    )
    active = [item for item in rows if _tournament_row_active(item, now)]
    groups = await asyncio.gather(
        *(_fetch_lol_tournament_matches(client, token, item) for item in active)
    )
    return tuple(match for group in groups for match in group if not match.is_closed)
