import json
from collections.abc import Mapping
from typing import cast

from dashboard.game_types import (
    BoardSlice,
    PlayerSummary,
    SeriesScore,
    TableSlice,
    compact,
    side_slice,
)
from shared.utils.json_read import as_map, try_int
from shared.utils.match_time import parse_utc
from trader.game_profile import GameProfile
from trader.grid_feed import (
    FEED_GONE_SERVICE,
    BoardSides,
    read_board_sides,
    select_board_players,
)
from trader.grid_widget_types import TableRow
from trader.grid_widgets import (
    FINISHED_STATUS,
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    Frame,
    NetWorthSnapshot,
    PlayerNetWorth,
    Scoreboard,
    clock_age_seconds,
    live_clock_seconds,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
    same_table_content,
    side_team,
)
from viewer.archive_read import as_str

CONTROL_LIMIT = 8
_FRAME_ERRORS = (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError)


def _cell(row: Mapping[str, object], name: str) -> int | None:
    cell = as_map(row.get(name))
    if cell is None:
        return None
    return try_int(cell.get("value"))


def _game_state(raw: dict[str, object]) -> dict[str, object] | None:
    for group in cast(list[object], raw.get("stateGroups") or []):
        group_map = as_map(group)
        if group_map is not None and group_map.get("name") == "Game":
            states = cast(list[object], group_map.get("states") or [])
            if states:
                return as_map(states[-1])
    return None


def _player_table(state: dict[str, object]) -> dict[str, object] | None:
    for entity_group in cast(list[object], state.get("entityGroups") or []):
        entity = as_map(entity_group)
        if entity is not None and entity.get("name") == "Player":
            tables = cast(list[object], entity.get("tables") or [])
            if tables:
                return as_map(tables[0])
    return None


def _raw_rows(payload: str) -> dict[tuple[str, str], TableRow]:
    raw = as_map(json.loads(payload))
    state = _game_state(raw) if raw is not None else None
    table = _player_table(state) if state is not None else None
    out: dict[tuple[str, str], TableRow] = {}
    if table is None:
        return out
    for row in cast(list[object], table.get("tableRows") or []):
        row_map = as_map(row)
        if row_map is None:
            continue
        entity_cell = as_map(row_map.get("entity"))
        if entity_cell is None:
            continue
        nick = as_str(entity_cell.get("value"))
        team_color = as_str(entity_cell.get("teamColor"))
        if nick is None or team_color is None:
            continue
        out[(team_color.rsplit(".", 1)[-1], nick)] = cast(TableRow, row_map)
    return out


def _player(parsed: PlayerNetWorth, rows: Mapping[tuple[str, str], TableRow]) -> PlayerSummary:
    row = rows.get((parsed.team_id, parsed.nick))
    level: int | None = None
    if row is not None:
        level_cell = row.get("increaseLevel")
        cell_map = as_map(level_cell) if level_cell is not None else None
        level_ups = try_int(cell_map.get("value")) if cell_map is not None else None
        if level_ups is not None:
            level = level_ups + 1
    return PlayerSummary(
        nick=parsed.nick or None,
        nick_compact=compact(parsed.nick or None),
        hero=parsed.hero or None,
        hero_compact=compact(parsed.hero or None),
        net_worth=_cell(row, "NetWorth") if row is not None else None,
        kills=_cell(row, "Kills") if row is not None else None,
        deaths=_cell(row, "Deaths") if row is not None else None,
        assists=_cell(row, "KillAssistsGiven") if row is not None else None,
        level=level,
        alive=None,
        respawn=None,
        aegis=None,
    )


def _board_signature(board: Scoreboard) -> tuple[object, ...]:
    return (
        board.game_status,
        board.clock_seconds,
        board.clock_ticking,
        board.active_game_number,
        tuple(sorted((team.team_id, team.kills, team.maps_won, team.won) for team in board.teams)),
    )


def _series_scores(payload: str) -> dict[str, int]:
    raw = as_map(json.loads(payload))
    if raw is None:
        return {}
    series = as_map(raw.get("series"))
    if series is None:
        return {}
    out: dict[str, int] = {}
    for item in cast(list[object], series.get("teams") or []):
        team = as_map(item)
        if team is None:
            continue
        team_id = as_str(team.get("id"))
        score = try_int(team.get("score"))
        if team_id is not None and score is not None:
            out[team_id] = score
    return out


def _board_slice(
    payload: str, board: Scoreboard, sides: BoardSides | None, received: str
) -> BoardSlice:
    series = _series_scores(payload)
    kills_0 = kills_1 = series_0 = series_1 = None
    name_0 = name_1 = None
    if sides is not None:
        name_0 = sides.radiant_name
        name_1 = sides.dire_name
        for team in board.teams:
            if team.team_id == sides.radiant_id:
                kills_0 = team.kills
                series_0 = series.get(team.team_id)
            elif team.team_id == sides.dire_id:
                kills_1 = team.kills
                series_1 = series.get(team.team_id)
    return BoardSlice(
        second=board.clock_seconds,
        clock_ticking=board.clock_ticking,
        status=board.game_status,
        paused=not board.clock_ticking,
        kills_0=kills_0,
        kills_1=kills_1,
        series=SeriesScore(
            name_0=name_0,
            name_1=name_1,
            score_0=series_0,
            score_1=series_1,
        ),
        received_at_utc=received,
        updated_at=board.occurred_at,
        archive_path=None,
        notes=(),
    )


def _table_second(board: Scoreboard, table: NetWorthSnapshot, received: str) -> int | None:
    try:
        received_dt = parse_utc(received)
    except ValueError:
        return None
    age = clock_age_seconds(board.occurred_at, received_dt)
    return live_clock_seconds(board, age) - table.feed_delay


class GridFold:
    def __init__(
        self,
        *,
        map_number: int,
        outcome_0_name: str | None,
        outcome_1_name: str | None,
        profile: GameProfile,
    ) -> None:
        self._map_number = map_number
        self._outcome_0_name = outcome_0_name
        self._outcome_1_name = outcome_1_name
        self._profile = profile
        self._sides: BoardSides | None = None
        self._board: Scoreboard | None = None
        self._board_signature: tuple[object, ...] | None = None
        self._board_received: str | None = None
        self._table_snapshot: NetWorthSnapshot | None = None
        self.board: BoardSlice | None = None
        self.table: TableSlice | None = None
        self.orientation: bool | None = None
        self.team_0_name: str | None = None
        self.team_1_name: str | None = None
        self.terminal = False
        self.rejected = 0
        self.control: tuple[str, ...] = ()

    def _note(self, text: str) -> None:
        self.control = (*self.control, text)[-CONTROL_LIMIT:]

    def _lock_sides(self, board: Scoreboard) -> None:
        if self._sides is not None:
            return
        side_0 = side_team(board, self._profile.side_0_text)
        side_1 = side_team(board, self._profile.side_1_text)
        if side_0 is None or side_1 is None:
            return
        self._sides = BoardSides(
            radiant_id=side_0.team_id,
            dire_id=side_1.team_id,
            radiant_name=side_0.name,
            dire_name=side_1.name,
            outcome_0_is_radiant=False,
        )
        self.team_0_name = side_0.name
        self.team_1_name = side_1.name
        if self._outcome_0_name is None or self._outcome_1_name is None:
            return
        read = read_board_sides(
            board,
            self._outcome_0_name,
            self._outcome_1_name,
            self._profile.side_0_text,
            self._profile.side_1_text,
            self._profile.aliases,
        )
        if read.sides is not None:
            self._sides = read.sides
            self.orientation = read.sides.outcome_0_is_radiant
            self.team_0_name = read.sides.radiant_name
            self.team_1_name = read.sides.dire_name
        elif not read.pending:
            self._note("outcome orientation unresolved")

    def apply(self, record: Mapping[str, object]) -> None:
        received = as_str(record.get("received_at_utc"))
        frame_text = as_str(record.get("frame"))
        if received is None or frame_text is None:
            self.rejected += 1
            self._note("malformed record")
            return
        try:
            frame = parse_frame(frame_text)
        except _FRAME_ERRORS:
            self.rejected += 1
            self._note("unparsable frame")
            return
        if frame.service == FEED_GONE_SERVICE:
            self.terminal = True
            self._note(f"feed gone @{received}")
            return
        if not frame.payload:
            return
        if frame.service == SCOREBOARD_SERVICE:
            self._on_board(frame, received)
        elif frame.service == TABLE_SERVICE:
            self._on_table(frame, received)

    def _on_board(self, frame: Frame, received: str) -> None:
        try:
            board = read_map_scoreboard(frame.payload, self._map_number)
        except _FRAME_ERRORS:
            self.rejected += 1
            self._note("unparsable scoreboard")
            return
        if board is None:
            return
        self._lock_sides(board)
        self._board = board
        self._board_received = received
        signature = _board_signature(board)
        if signature == self._board_signature:
            return
        self._board_signature = signature
        self.board = _board_slice(frame.payload, board, self._sides, received)
        if board.game_status == FINISHED_STATUS:
            self.terminal = True

    def _on_table(self, frame: Frame, received: str) -> None:
        try:
            table = read_net_worth(frame.payload, frame.delay)
        except _FRAME_ERRORS:
            self.rejected += 1
            self._note("unparsable table")
            return
        if table is None:
            self.rejected += 1
            self._note("table without players")
            return
        if table.game_number != self._map_number:
            self.rejected += 1
            self._note(f"table for map {table.game_number}")
            return
        board = self._board
        sides = self._sides
        if board is None or sides is None:
            self.rejected += 1
            self._note("table before pinned board")
            return
        if board.active_game_number != self._map_number:
            self.rejected += 1
            self._note("table while other map active")
            return
        players = select_board_players(table, sides)
        if players is None:
            self.rejected += 1
            self._note("incomplete roster")
            return
        if self._table_snapshot is not None and same_table_content(table, self._table_snapshot):
            return
        rows = _raw_rows(frame.payload)
        self._table_snapshot = table
        self.table = TableSlice(
            second=_table_second(board, table, received),
            second_reconstructed=True,
            received_at_utc=received,
            updated_at=board.occurred_at,
            feed_delay=table.feed_delay,
            board_received_at_utc=self._board_received or received,
            archive_path=None,
            side_0=side_slice(
                self._profile.side_0_text.capitalize(),
                sides.radiant_name,
                None,
                tuple(_player(player, rows) for player in players.radiant),
            ),
            side_1=side_slice(
                self._profile.side_1_text.capitalize(),
                sides.dire_name,
                None,
                tuple(_player(player, rows) for player in players.dire),
            ),
            objectives_0=None,
            objectives_1=None,
            notes=(),
        )
