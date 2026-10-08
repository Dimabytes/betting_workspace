"""Oddin payload → OddinTick projection and OddinTick → FeedEvent reducer."""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from shared.constants.strategy import PLAYERS_PER_SIDE
from shared.utils.json_read import as_map, read_str, try_int
from shared.utils.log import get_logger
from shared.utils.match_time import parse_utc
from shared.utils.top_players import (
    ZERO_TOP,
    build_top_player_features,
)
from trader.archive_types import MatchWinner
from trader.grid_feed import received_at_utc
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, MatchPhase, horn_clock_is_positive
from trader.oddin_crypto import open_envelope
from trader.oddin_types import (
    DIRE,
    FINISHED_STATUS,
    RADIANT,
    VALID_DATA,
    OddinStateArchiveRecord,
    OddinTick,
    PlayerTick,
    TeamTick,
)

logger = get_logger(__name__)

ODDIN_FEED_STALE_SECONDS = 15.0
SNAPSHOT_EVENT = "snapshot"
WS_EVENT = "ws"
RECONNECT_EVENT = "reconnect"
CLOSED_EVENT = "closed"
_GO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.(\d+))? ([+-]\d{4}) UTC$")


def parse_oddin_timestamp(raw: str) -> datetime | None:
    """Parse Oddin's Go stamp `2006-01-02 15:04:05.999999999 -0700 UTC`, or ISO."""
    stripped = raw.strip()
    if not stripped:
        return None
    matched = _GO_TS.match(stripped)
    if matched is not None:
        date, clock, frac, offset = matched.groups()
        if frac is None:
            parsed = datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M:%S")
        else:
            micro = (frac + "000000")[:6]
            parsed = datetime.strptime(f"{date} {clock}.{micro}", "%Y-%m-%d %H:%M:%S.%f")
        sign = 1 if offset[0] == "+" else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        zone = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
        return parsed.replace(tzinfo=zone)
    try:
        return parse_utc(stripped)
    except ValueError:
        return None


def estimated_horn_unix(last_updated: datetime, game_time: int) -> int:
    """Horn unix second: published stamp minus game clock."""
    return int(last_updated.timestamp()) - game_time


@dataclass(frozen=True)
class _FactionTeam:
    """One parsed map side bound to its faction."""

    faction: str
    tick: TeamTick


def _read_player(raw: Mapping[str, object]) -> PlayerTick | None:
    net_worth = try_int(raw.get("netWorth"))
    deaths = try_int(raw.get("deaths"))
    if net_worth is None or deaths is None:
        return None
    player = as_map(raw.get("player")) or {}
    hero = as_map(raw.get("hero")) or {}
    alive = raw.get("alive")
    return PlayerTick(
        nickname=read_str(player, "nickname") or "?",
        hero=read_str(hero, "name") or "?",
        net_worth=net_worth,
        kills=try_int(raw.get("kills")) or 0,
        deaths=deaths,
        assists=try_int(raw.get("assists")) or 0,
        alive=True if not isinstance(alive, bool) else alive,
        respawn_timer=try_int(raw.get("respawnTimer")),
        has_aegis=raw.get("hasAegis") is True,
    )


def _read_map_team(raw: object) -> _FactionTeam | None:
    fields = as_map(raw)
    if fields is None:
        return None
    faction = read_str(fields, "faction").upper()
    if faction not in {RADIANT, DIRE}:
        return None
    team = as_map(fields.get("team")) or {}
    players_raw = fields.get("players")
    players: list[PlayerTick] = []
    if isinstance(players_raw, list):
        for item in cast(list[object], players_raw):
            mapped = as_map(item)
            if mapped is None:
                continue
            player = _read_player(mapped)
            if player is not None:
                players.append(player)
    net_worth = try_int(fields.get("netWorthNullable"))
    if net_worth is None:
        net_worth = try_int(fields.get("netWorth")) or 0
    barracks = try_int(fields.get("barracksNullable"))
    if barracks is None:
        barracks = try_int(fields.get("barracks")) or 0
    tick = TeamTick(
        name=read_str(team, "name") or "?",
        kills=try_int(fields.get("kills")) or 0,
        net_worth=net_worth,
        towers=try_int(fields.get("towers")) or 0,
        barracks=barracks,
        roshans=try_int(fields.get("roshans")) or 0,
        players=tuple(players),
    )
    return _FactionTeam(faction, tick)


def project_tick(payload: Mapping[str, object]) -> OddinTick:
    """Project decrypted Oddin JSON onto Radiant/Dire by `faction`."""
    home = as_map(payload.get("homeTeam")) or {}
    away = as_map(payload.get("awayTeam")) or {}
    current = as_map(payload.get("currentMap"))
    radiant: TeamTick | None = None
    dire: TeamTick | None = None
    map_order: int | None = None
    game_time: int | None = None
    if current is not None:
        map_order = try_int(current.get("mapOrder"))
        game_time = try_int(current.get("gameTime"))
        for side_key in ("homeTeam", "awayTeam"):
            parsed = _read_map_team(current.get(side_key))
            if parsed is None:
                continue
            if parsed.faction == RADIANT:
                radiant = parsed.tick
            else:
                dire = parsed.tick
    paused = payload.get("mapPaused")
    return OddinTick(
        match_status=read_str(payload, "matchStatus") or "?",
        data_status=read_str(payload, "dataStatus") or "?",
        last_updated_at=read_str(payload, "lastUpdatedAt"),
        map_paused=paused is True,
        home_name=read_str(home, "name") or "Home",
        away_name=read_str(away, "name") or "Away",
        home_score=try_int(payload.get("homeScore")) or 0,
        away_score=try_int(payload.get("awayScore")) or 0,
        map_order=map_order,
        game_time=game_time,
        radiant=radiant,
        dire=dire,
    )


def open_tick(envelope: str, key: bytes) -> OddinTick:
    """Decrypt an envelope and project it."""
    return project_tick(open_envelope(envelope, key))


def _current_map_id(payload: Mapping[str, object]) -> str:
    current = as_map(payload.get("currentMap"))
    if current is None:
        return ""
    return read_str(current, "id")


def _previous_maps(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    raw = payload.get("previousMaps")
    if not isinstance(raw, list):
        return ()
    maps: list[dict[str, object]] = []
    for item in cast(list[object], raw):
        mapped = as_map(item)
        if mapped is not None:
            maps.append(mapped)
    return tuple(maps)


def _complete_side(team: TeamTick) -> bool:
    nicknames = [player.nickname for player in team.players]
    return len(nicknames) == PLAYERS_PER_SIDE and len(set(nicknames)) == PLAYERS_PER_SIDE


@dataclass(frozen=True)
class _FactionWon:
    """One previous-map side's faction and won flag."""

    faction: str
    won: bool


def _won_faction(raw: object) -> _FactionWon | None:
    fields = as_map(raw)
    if fields is None:
        return None
    faction = read_str(fields, "faction").upper()
    if faction not in {RADIANT, DIRE}:
        return None
    won = fields.get("won")
    if not isinstance(won, bool):
        return None
    return _FactionWon(faction, won)


def _previous_map_entry(
    previous: Sequence[Mapping[str, object]], map_order: int, map_id: str | None
) -> Mapping[str, object] | None:
    if map_id:
        for item in previous:
            if read_str(item, "id") == map_id:
                return item
    for item in previous:
        if try_int(item.get("mapOrder")) == map_order:
            return item
    return None


def _winner_from_map_entry(target: Mapping[str, object]) -> MatchWinner | None:
    radiant_won: bool | None = None
    dire_won: bool | None = None
    for side_key in ("homeTeam", "awayTeam"):
        parsed = _won_faction(target.get(side_key))
        if parsed is None:
            return None
        if parsed.faction == RADIANT:
            radiant_won = parsed.won
        else:
            dire_won = parsed.won
    if radiant_won is True and dire_won is False:
        return "radiant"
    if dire_won is True and radiant_won is False:
        return "dire"
    return None


def winner_from_previous_maps(
    previous: Sequence[Mapping[str, object]], map_order: int, map_id: str | None
) -> MatchWinner | None:
    """Winner from `won` on the finished map; ambiguous or missing sides are None."""
    target = _previous_map_entry(previous, map_order, map_id)
    if target is None:
        return None
    return _winner_from_map_entry(target)


@dataclass(frozen=True)
class _LiveTick:
    """A VALID_DATA tick narrowed to the pinned map with both sides and a clock."""

    radiant: TeamTick
    dire: TeamTick
    game_time: int


def _oddin_phase(game_time: int, countdown_started: bool) -> MatchPhase:
    """Map the Oddin clock and the caller's countdown flag onto a match phase."""
    if game_time >= 0:
        return MatchPhase.IN_PROGRESS
    if countdown_started:
        return MatchPhase.PRE_HORN
    return MatchPhase.PRE_MATCH


def _terminal_snapshot(second: int, server_timestamp: int) -> GameSnapshot:
    return GameSnapshot(
        second=second,
        server_timestamp=server_timestamp,
        phase=MatchPhase.FINISHED,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top=ZERO_TOP,
        paused=False,
    )


class OddinSnapshotReducer:
    """Full-snapshot reducer bound to one map number and market orientation."""

    def __init__(self, expected_map_order: int, yes_is_radiant: bool) -> None:
        """Bind the 1-based map and whether YES is Radiant."""
        self._expected_map_order = expected_map_order
        self._yes_is_radiant = yes_is_radiant
        self._horn_unix: int | None = None
        self._last_clock: int | None = None
        self._countdown_started: bool = False
        self._last_updated: datetime | None = None
        self._last_tick: FeedEvent | None = None
        self._map_id: str | None = None
        self._finished = False
        self._winner: MatchWinner | None = None

    @property
    def winner(self) -> MatchWinner | None:
        """Winner from previousMaps.won after a terminal tick, else None."""
        return self._winner

    def reset(self) -> None:
        """Drop clock tracking after a disconnect. Horn, countdown, and last tick stay."""
        self._last_clock = None

    def apply_payload(
        self, payload: Mapping[str, object], received_at: datetime
    ) -> FeedEvent | None:
        """Reduce one decrypted snapshot. HTTP seeds must not call this."""
        if self._finished:
            return None
        tick = project_tick(payload)
        previous = _previous_maps(payload)
        map_id = _current_map_id(payload)
        if self._is_finished(tick, previous):
            return self._emit_finish(tick, previous, received_at)
        return self._emit_live(tick, payload, map_id, received_at)

    def _is_finished(self, tick: OddinTick, previous: Sequence[Mapping[str, object]]) -> bool:
        if tick.match_status == FINISHED_STATUS:
            return True
        if self._map_id and any(read_str(item, "id") == self._map_id for item in previous):
            return True
        return tick.map_order is not None and tick.map_order > self._expected_map_order

    def _emit_finish(
        self,
        tick: OddinTick,
        previous: Sequence[Mapping[str, object]],
        received_at: datetime,
    ) -> FeedEvent:
        self._finished = True
        self._winner = winner_from_previous_maps(previous, self._expected_map_order, self._map_id)
        last = self._last_tick
        updated = parse_oddin_timestamp(tick.last_updated_at)
        if last is None:
            second = 0
            horn = 0
            server_timestamp = int(updated.timestamp()) if updated is not None else 0
        else:
            second = last.snapshot.second
            horn = last.horn_unix_seconds
            server_timestamp = (
                int(updated.timestamp()) if updated is not None else last.snapshot.server_timestamp
            )
        event = FeedEvent(
            snapshot=_terminal_snapshot(second, server_timestamp),
            received_at_utc=received_at_utc(received_at),
            source=FeedSource.ODDIN,
            horn_unix_seconds=horn,
            yes_is_radiant=self._yes_is_radiant,
        )
        self._last_tick = event
        return event

    def _emit_live(
        self,
        tick: OddinTick,
        payload: Mapping[str, object],
        map_id: str,
        received_at: datetime,
    ) -> FeedEvent | None:
        live = self._live_tick(tick)
        if isinstance(live, str):
            logger.debug("oddin tick rejected: %s", live)
            return None
        updated = parse_oddin_timestamp(tick.last_updated_at)
        if updated is None:
            logger.debug("oddin tick rejected: lastUpdatedAt")
            return None
        if self._last_updated is not None:
            if updated < self._last_updated:
                logger.debug("oddin tick rejected: out of order")
                return None
            if updated == self._last_updated:
                return None
        radiant = live.radiant
        dire = live.dire
        game_time = live.game_time
        if self._last_clock is not None and game_time > self._last_clock:
            self._countdown_started = True
        phase = _oddin_phase(game_time, self._countdown_started)
        if self._horn_unix is None and horn_clock_is_positive(phase, game_time):
            self._horn_unix = estimated_horn_unix(updated, game_time)
        horn = (
            self._horn_unix
            if self._horn_unix is not None
            else estimated_horn_unix(updated, game_time)
        )
        radiant_nws = [player.net_worth for player in radiant.players]
        dire_nws = [player.net_worth for player in dire.players]
        snapshot = GameSnapshot(
            second=game_time,
            server_timestamp=int(updated.timestamp()),
            phase=phase,
            radiant_nw_adv=sum(radiant_nws) - sum(dire_nws),
            radiant_nw=sum(radiant_nws),
            dire_nw=sum(dire_nws),
            radiant_xp_adv=0,
            deaths_radiant=sum(player.deaths for player in radiant.players),
            deaths_dire=sum(player.deaths for player in dire.players),
            top=build_top_player_features(radiant_nws, dire_nws),
            paused=tick.map_paused,
        )
        if map_id:
            self._map_id = map_id
        self._last_clock = game_time
        self._last_updated = updated
        event = FeedEvent(
            snapshot=snapshot,
            received_at_utc=received_at_utc(received_at),
            source=FeedSource.ODDIN,
            horn_unix_seconds=horn,
            yes_is_radiant=self._yes_is_radiant,
        )
        self._last_tick = event
        return event

    def _live_tick(self, tick: OddinTick) -> _LiveTick | str:
        """The narrowed live fields, or the reject reason."""
        if tick.data_status != VALID_DATA:
            return "dataStatus"
        if tick.map_order != self._expected_map_order:
            return "mapOrder"
        if tick.game_time is None:
            return "gameTime"
        if tick.radiant is None or tick.dire is None:
            return "sides"
        if not _complete_side(tick.radiant) or not _complete_side(tick.dire):
            return "players"
        return _LiveTick(tick.radiant, tick.dire, tick.game_time)


def replay_oddin_records(
    records: Iterable[OddinStateArchiveRecord],
    reducer: OddinSnapshotReducer,
) -> Iterator[FeedEvent]:
    """Replay archived Oddin rows. Seed is skipped; reconnect resets; stop on FINISHED."""
    for record in records:
        event_name = record["event"]
        if event_name == SNAPSHOT_EVENT:
            continue
        if event_name == RECONNECT_EVENT:
            reducer.reset()
            continue
        mapped = as_map(record["payload"])
        if mapped is None:
            continue
        event = reducer.apply_payload(mapped, parse_utc(record["received_at_utc"]))
        if event is not None:
            yield event
            if event.snapshot.finished:
                return
