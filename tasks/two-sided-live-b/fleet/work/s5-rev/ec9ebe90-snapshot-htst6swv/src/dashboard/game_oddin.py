from collections.abc import Mapping
from typing import cast

from dashboard.game_types import (
    BoardSlice,
    Objectives,
    PlayerSummary,
    SeriesScore,
    TableSlice,
    compact,
    side_slice,
)
from shared.utils.json_read import as_map, read_str, try_int
from shared.utils.match_time import parse_utc
from trader.oddin_feed import (
    RECONNECT_EVENT,
    SNAPSHOT_EVENT,
    WS_EVENT,
    OddinSnapshotReducer,
    project_tick,
)
from trader.oddin_types import DIRE, RADIANT, OddinTick
from viewer.archive_read import as_str

CONTROL_LIMIT = 8


def _faction_side(payload: Mapping[str, object], faction: str) -> dict[str, object] | None:
    current = as_map(payload.get("currentMap"))
    if current is None:
        return None
    for side_key in ("homeTeam", "awayTeam"):
        side = as_map(current.get(side_key))
        if side is None:
            continue
        if read_str(side, "faction").upper() == faction:
            return side
    return None


def _team_gold(side: Mapping[str, object] | None) -> int | None:
    if side is None:
        return None
    gold = try_int(side.get("netWorthNullable"))
    if gold is not None:
        return gold
    return try_int(side.get("netWorth"))


def _team_cell(side: Mapping[str, object] | None, name: str) -> int | None:
    if side is None:
        return None
    return try_int(side.get(name))


def _team_barracks(side: Mapping[str, object] | None) -> int | None:
    if side is None:
        return None
    barracks = try_int(side.get("barracksNullable"))
    if barracks is not None:
        return barracks
    return try_int(side.get("barracks"))


def _player(row: Mapping[str, object] | None, nick: str) -> PlayerSummary:
    if row is None:
        return PlayerSummary(
            nick=nick,
            nick_compact=compact(nick),
            hero=None,
            hero_compact=None,
            net_worth=None,
            kills=None,
            deaths=None,
            assists=None,
            level=None,
            alive=None,
            respawn=None,
            aegis=None,
        )
    player = as_map(row.get("player")) or {}
    hero = as_map(row.get("hero")) or {}
    alive = row.get("alive")
    aegis = row.get("hasAegis")
    hero_name = read_str(hero, "name") or None
    return PlayerSummary(
        nick=read_str(player, "nickname") or None,
        nick_compact=compact(read_str(player, "nickname") or None),
        hero=hero_name,
        hero_compact=compact(hero_name),
        net_worth=try_int(row.get("netWorth")),
        kills=try_int(row.get("kills")),
        deaths=try_int(row.get("deaths")),
        assists=try_int(row.get("assists")),
        level=None,
        alive=alive if isinstance(alive, bool) else None,
        respawn=try_int(row.get("respawnTimer")),
        aegis=aegis if isinstance(aegis, bool) else None,
    )


def _players(
    side: Mapping[str, object] | None, nicks: tuple[str, ...]
) -> tuple[PlayerSummary, ...]:
    if side is None:
        return ()
    rows: list[dict[str, object]] = []
    raw_players = side.get("players")
    if isinstance(raw_players, list):
        rows = [
            row
            for row in (as_map(item) for item in cast(list[object], raw_players))
            if row is not None
        ]
    out: list[PlayerSummary] = []
    for nick in nicks:
        row = next(
            (
                item
                for item in rows
                if read_str(as_map(item.get("player")) or {}, "nickname") == nick
            ),
            None,
        )
        out.append(_player(row, nick))
    return tuple(out)


def _board_slice(
    payload: Mapping[str, object],
    tick: OddinTick,
    raw_0: Mapping[str, object] | None,
    raw_1: Mapping[str, object] | None,
    received: str,
) -> BoardSlice:
    paused = payload.get("mapPaused")
    paused_flag = paused if isinstance(paused, bool) else None
    return BoardSlice(
        second=tick.game_time,
        clock_ticking=not paused_flag if paused_flag is not None else None,
        status=tick.match_status if tick.match_status != "?" else None,
        paused=paused_flag,
        kills_0=_team_cell(raw_0, "kills"),
        kills_1=_team_cell(raw_1, "kills"),
        series=SeriesScore(
            name_0=tick.home_name,
            name_1=tick.away_name,
            score_0=try_int(payload.get("homeScore")),
            score_1=try_int(payload.get("awayScore")),
        ),
        received_at_utc=received,
        updated_at=tick.last_updated_at or None,
        archive_path=None,
        notes=(),
    )


def _table_slice(
    tick: OddinTick,
    raw_0: Mapping[str, object] | None,
    raw_1: Mapping[str, object] | None,
    received: str,
) -> TableSlice:
    return TableSlice(
        second=tick.game_time,
        second_reconstructed=False,
        received_at_utc=received,
        updated_at=tick.last_updated_at or None,
        feed_delay=None,
        board_received_at_utc=received,
        archive_path=None,
        side_0=side_slice(
            "Radiant",
            tick.radiant.name if tick.radiant is not None else None,
            _team_gold(raw_0),
            _players(raw_0, tuple(p.nickname for p in tick.radiant.players))
            if tick.radiant is not None
            else (),
        ),
        side_1=side_slice(
            "Dire",
            tick.dire.name if tick.dire is not None else None,
            _team_gold(raw_1),
            _players(raw_1, tuple(p.nickname for p in tick.dire.players))
            if tick.dire is not None
            else (),
        ),
        objectives_0=Objectives(
            _team_cell(raw_0, "towers"),
            _team_barracks(raw_0),
            _team_cell(raw_0, "roshans"),
        ),
        objectives_1=Objectives(
            _team_cell(raw_1, "towers"),
            _team_barracks(raw_1),
            _team_cell(raw_1, "roshans"),
        ),
        notes=(),
    )


class OddinFold:
    def __init__(self, *, map_number: int, yes_is_radiant: bool) -> None:
        self._reducer = OddinSnapshotReducer(map_number, yes_is_radiant)
        self.board: BoardSlice | None = None
        self.table: TableSlice | None = None
        self.team_0_name: str | None = None
        self.team_1_name: str | None = None
        self.terminal = False
        self.rejected = 0
        self.control: tuple[str, ...] = ()

    def _note(self, text: str) -> None:
        self.control = (*self.control, text)[-CONTROL_LIMIT:]

    def apply(self, record: Mapping[str, object]) -> None:
        event_name = as_str(record.get("event"))
        received = as_str(record.get("received_at_utc"))
        if event_name == SNAPSHOT_EVENT:
            self._note(f"seed @{received}")
            return
        if event_name == RECONNECT_EVENT:
            self._reducer.reset()
            self._note(f"reconnect @{received}")
            return
        if event_name != WS_EVENT:
            self.rejected += 1
            self._note(f"unknown event {event_name!r}")
            return
        if self.terminal:
            return
        payload = as_map(record.get("payload"))
        if payload is None or received is None:
            self.rejected += 1
            self._note("malformed ws record")
            return
        try:
            received_dt = parse_utc(received)
        except ValueError:
            self.rejected += 1
            self._note("bad receipt stamp")
            return
        event = self._reducer.apply_payload(payload, received_dt)
        if event is None:
            self.rejected += 1
            return
        if event.snapshot.finished:
            self.terminal = True
            self._note(f"terminal @{received}")
            return
        tick = project_tick(payload)
        if tick.radiant is None or tick.dire is None:
            return
        raw_0 = _faction_side(payload, RADIANT)
        raw_1 = _faction_side(payload, DIRE)
        self.team_0_name = tick.radiant.name
        self.team_1_name = tick.dire.name
        self.board = _board_slice(payload, tick, raw_0, raw_1, received)
        self.table = _table_slice(tick, raw_0, raw_1, received)
