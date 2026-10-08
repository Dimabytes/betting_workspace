"""GRID widget frame reducer: turn pinned-map frames into source-neutral ticks."""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.constants.strategy import KILL_GATE_HOLD_S, KILL_GATE_MAX_BOARD_AGE_S
from shared.utils.level_xp import xp_advantage
from shared.utils.log import get_logger
from shared.utils.match_time import parse_utc
from shared.utils.team_names import orient_outcomes
from shared.utils.top_players import (
    ZERO_TOP,
    build_top_player_features,
    build_top_player_features_over_total,
)
from trader.game_profile import GameProfile
from trader.grid_widget_types import GridStateArchiveRecord
from trader.grid_widgets import (
    FINISHED_STATUS,
    LIVE_STATUS,
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    UPCOMING_STATUS,
    Frame,
    NetWorthSnapshot,
    PlayerNetWorth,
    Scoreboard,
    clock_age_seconds,
    clock_stamp_unix_seconds,
    live_clock_seconds,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
    same_table_content,
    select_side_players,
    side_team,
)
from trader.live_feed import FeedEvent, FeedSource, GameSnapshot, KillTick, MatchPhase, SideWait

logger = get_logger(__name__)

FEED_GONE_SERVICE = "live_paper_feed_gone"
FEED_GONE_FRAME = '{"service":"live_paper_feed_gone","delay":0,"data":[]}'


class GridOrientationError(Exception):
    """The pinned map's GRID sides cannot be oriented against the market names."""


@dataclass(frozen=True)
class BoardSides:
    """One board's two sides plus which side holds market outcome 0."""

    radiant_id: str
    dire_id: str
    radiant_name: str
    dire_name: str
    outcome_0_is_radiant: bool


@dataclass(frozen=True)
class BoardSideRead:
    """GRID board vs market names: ready sides, or why they are missing."""

    sides: BoardSides | None
    pending: bool


@dataclass(frozen=True)
class _PendingWait:
    """One side's unexpired kill wait inside the reducer."""

    awaited_deaths: int
    until: datetime


@dataclass(frozen=True)
class SidePlayers:
    """Both pinned sides' selected five-player rows of one table."""

    radiant: tuple[PlayerNetWorth, ...]
    dire: tuple[PlayerNetWorth, ...]


def select_board_players(table: NetWorthSnapshot, sides: BoardSides) -> SidePlayers | None:
    """Both sides' five selected rows, or None when either side's shape is off."""
    radiant = select_side_players(table, sides.radiant_id)
    dire = select_side_players(table, sides.dire_id)
    if radiant is None or dire is None:
        return None
    return SidePlayers(radiant=radiant, dire=dire)


def read_board_sides(
    board: Scoreboard,
    outcome_0_name: str,
    outcome_1_name: str,
    side_0_text: str,
    side_1_text: str,
    aliases: Mapping[str, tuple[str, ...]],
) -> BoardSideRead:
    """Resolve both sides of `board` and orient the market outcomes against them.

    `pending` is True when a side is missing. `sides` is None and `pending` is
    False when the names failed to orient, including aliases. Discovery and the
    reducer both read sides through this function; expected GRID infoText
    values come from the caller.
    """
    side_0 = side_team(board, side_0_text)
    side_1 = side_team(board, side_1_text)
    if side_0 is None or side_1 is None:
        return BoardSideRead(sides=None, pending=True)
    orientation = orient_outcomes(outcome_0_name, outcome_1_name, side_0.name, side_1.name, aliases)
    if orientation is None:
        return BoardSideRead(sides=None, pending=False)
    return BoardSideRead(
        sides=BoardSides(
            radiant_id=side_0.team_id,
            dire_id=side_1.team_id,
            radiant_name=side_0.name,
            dire_name=side_1.name,
            outcome_0_is_radiant=orientation,
        ),
        pending=False,
    )


def received_at_utc(now: datetime) -> str:
    """Format `now` as ISO-8601 UTC with a Z suffix."""
    return now.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _horn_unix_seconds(board: Scoreboard) -> int:
    """Horn Unix second from the published clock stamp, not the extrapolated clock."""
    return clock_stamp_unix_seconds(board.occurred_at) - board.clock_seconds


def _match_phase(game_status: str, second: int) -> MatchPhase:
    """Map GRID map status and the delayed game second onto a source-neutral phase."""
    if game_status == FINISHED_STATUS:
        return MatchPhase.FINISHED
    if game_status != LIVE_STATUS:
        return MatchPhase.PRE_MATCH
    if second >= 0:
        return MatchPhase.IN_PROGRESS
    return MatchPhase.PRE_HORN


def _feed_event(
    snapshot: GameSnapshot, board: Scoreboard, now: datetime, sides: BoardSides
) -> FeedEvent:
    """Attach GRID source, receipt stamp, horn, and the pinned map's market orientation."""
    return FeedEvent(
        snapshot=snapshot,
        received_at_utc=received_at_utc(now),
        source=FeedSource.GRID,
        horn_unix_seconds=_horn_unix_seconds(board),
        yes_is_radiant=sides.outcome_0_is_radiant,
    )


def _terminal_snapshot(board: Scoreboard, now: datetime) -> GameSnapshot:
    """Finished snapshot from the board clock: no table delay, zero features."""
    age = clock_age_seconds(board.occurred_at, now)
    return GameSnapshot(
        second=live_clock_seconds(board, age),
        server_timestamp=clock_stamp_unix_seconds(board.occurred_at),
        phase=MatchPhase.FINISHED,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top=ZERO_TOP,
        paused=not board.clock_ticking,
    )


def _live_snapshot(
    board: Scoreboard,
    players: SidePlayers,
    feed_delay: int,
    now: datetime,
    profile: GameProfile,
) -> GameSnapshot:
    """Build a model snapshot from the pinned map's scoreboard clock and selected rows."""
    radiant_nws = [player.net_worth for player in players.radiant]
    dire_nws = [player.net_worth for player in players.dire]
    radiant_nw = sum(radiant_nws)
    dire_nw = sum(dire_nws)
    radiant_levels = [player.level for player in players.radiant]
    dire_levels = [player.level for player in players.dire]
    if profile.game == "lol":
        top = build_top_player_features_over_total(radiant_nws, dire_nws)
    else:
        top = build_top_player_features(radiant_nws, dire_nws)
    age = clock_age_seconds(board.occurred_at, now)
    second = live_clock_seconds(board, age) - feed_delay
    return GameSnapshot(
        second=second,
        server_timestamp=clock_stamp_unix_seconds(board.occurred_at),
        phase=_match_phase(board.game_status, second),
        radiant_nw_adv=radiant_nw - dire_nw,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=xp_advantage(profile.level_xp, radiant_levels, dire_levels),
        deaths_radiant=sum(player.deaths for player in players.radiant),
        deaths_dire=sum(player.deaths for player in players.dire),
        top=top,
        paused=not board.clock_ticking,
    )


class GridFrameReducer:
    """Pinned-map frame reducer: pure state, no socket and no archive."""

    def __init__(
        self,
        map_number: int,
        outcome_0_name: str,
        outcome_1_name: str,
        profile: GameProfile,
    ) -> None:
        """Bind the pinned map, market names, and the profile that names GRID sides and the XP table."""
        self._map_number = map_number
        self._outcome_0_name = outcome_0_name
        self._outcome_1_name = outcome_1_name
        self._profile = profile
        self._table: NetWorthSnapshot | None = None
        self._players: SidePlayers | None = None
        self.board: Scoreboard | None = None
        self._sides: BoardSides | None = None
        self._board_kills: dict[str, int] | None = None
        self._kill_wait: dict[str, _PendingWait | None] = {"radiant": None, "dire": None}

    def reduce_frame(self, frame: Frame, now: datetime) -> FeedEvent | KillTick | None:
        """Fold one parsed widget frame into the state and emit a tick when it qualifies."""
        if frame.service == FEED_GONE_SERVICE:
            return self._on_feed_gone(now)
        if not frame.payload:
            return None
        if frame.service == SCOREBOARD_SERVICE:
            return self._on_scoreboard(frame.payload, now)
        if frame.service == TABLE_SERVICE:
            return self._on_table(frame.payload, frame.delay, now)
        return None

    def _lock_sides(self, board: Scoreboard) -> None:
        """Pin market names to this map's GRID sides on the first complete live or finished board.

        Upcoming or incomplete sides stay pending. A complete board that fails
        to orient raises GridOrientationError. Discovery's Steam sides are not
        a second source of truth: this feed is GRID, so GRID orients the market.
        """
        if self._sides is not None:
            return
        if board.game_status == UPCOMING_STATUS:
            return
        read = read_board_sides(
            board,
            self._outcome_0_name,
            self._outcome_1_name,
            self._profile.side_0_text,
            self._profile.side_1_text,
            self._profile.aliases,
        )
        if read.pending:
            return
        sides = read.sides
        if sides is None:
            raise GridOrientationError(
                f"grid orientation unresolved map={self._map_number} "
                f"market={self._outcome_0_name!r}/{self._outcome_1_name!r}"
            )
        self._sides = sides
        table = self._table
        if table is None:
            return
        self._players = select_board_players(table, sides)
        if self._players is None:
            self._table = None

    def _on_feed_gone(self, now: datetime) -> FeedEvent | None:
        """Emit a finished tick from the last pinned board when the widget is gone."""
        board = self.board
        sides = self._sides
        if board is None or sides is None:
            return None
        return _feed_event(_terminal_snapshot(board, now), board, now, sides)

    def _side_deaths(self, side: str) -> int:
        """Deaths of one pinned side in the last stored table's selected rows."""
        players = self._players
        if players is None:
            return 0
        side_players = players.radiant if side == "radiant" else players.dire
        return sum(player.deaths for player in side_players)

    def _side_wait(self, side: str, now: datetime) -> SideWait:
        """Project one side's tracked wait onto the KillTick wire shape."""
        wait = self._kill_wait[side]
        if wait is None or now >= wait.until:
            return SideWait(awaited_deaths=self._side_deaths(side), seconds_left=0.0)
        return SideWait(
            awaited_deaths=wait.awaited_deaths,
            seconds_left=(wait.until - now).total_seconds(),
        )

    def _track_kills(self, board: Scoreboard, now: datetime) -> KillTick | None:
        """Compare board kills to the tracked max; open a wait on each victim side."""
        sides = self._sides
        if sides is None:
            return None
        kills = {"radiant": 0, "dire": 0}
        for team in board.teams:
            if team.team_id == sides.radiant_id:
                kills["radiant"] = team.kills
            elif team.team_id == sides.dire_id:
                kills["dire"] = team.kills
        if self._board_kills is None:
            self._board_kills = kills
            return None
        age = clock_age_seconds(board.occurred_at, now)
        opened = False
        for killer, victim in (("radiant", "dire"), ("dire", "radiant")):
            growth = kills[killer] - self._board_kills[killer]
            if growth <= 0 or age >= KILL_GATE_MAX_BOARD_AGE_S:
                continue
            wait = self._kill_wait[victim]
            if wait is None or now >= wait.until:
                base = self._side_deaths(victim)
            else:
                base = max(wait.awaited_deaths, self._side_deaths(victim))
            self._kill_wait[victim] = _PendingWait(
                awaited_deaths=base + growth,
                until=now + timedelta(seconds=KILL_GATE_HOLD_S),
            )
            opened = True
        self._board_kills = {side: max(self._board_kills[side], kills[side]) for side in kills}
        if not opened:
            return None
        return KillTick(
            received_at_utc=received_at_utc(now),
            radiant=self._side_wait("radiant", now),
            dire=self._side_wait("dire", now),
        )

    def _on_scoreboard(self, payload: str, now: datetime) -> FeedEvent | KillTick | None:
        """Store the pinned map's scoreboard; emit a kill marker or a finished tick."""
        board = read_map_scoreboard(payload, self._map_number)
        if board is None:
            return None
        self.board = board
        self._lock_sides(board)
        if self._sides is None:
            return None
        if board.game_status == FINISHED_STATUS:
            return _feed_event(_terminal_snapshot(board, now), board, now, self._sides)
        return self._track_kills(board, now)

    def _on_table(self, payload: str, delay: int, now: datetime) -> FeedEvent | None:
        """Store a changed table and emit a tick when the pinned map and market names line up.

        Once sides are locked, only a table with a readable five-row shape on
        both sides replaces the stored one; a degenerate table is no tick and
        keeps the last good table for the kill gate.
        """
        table = read_net_worth(payload, delay)
        if table is None:
            return None
        if self._table is not None and same_table_content(table, self._table):
            return None
        board = self.board
        sides = self._sides
        if board is None or sides is None:
            self._table = table
            self._players = None
            return None
        players = select_board_players(table, sides)
        if players is None:
            logger.debug(
                "grid table rejected: %d rows on teams %s",
                len(table.players),
                sorted({player.team_id for player in table.players}),
            )
            return None
        self._table = table
        self._players = players
        if table.game_number != self._map_number:
            return None
        if board.active_game_number != self._map_number:
            return None
        return _feed_event(
            _live_snapshot(board, players, table.feed_delay, now, self._profile), board, now, sides
        )


def replay_grid_records(
    records: Iterable[GridStateArchiveRecord],
    reducer: GridFrameReducer,
) -> Iterator[FeedEvent]:
    """Replay archived widget frames through the frame reducer."""
    for record in records:
        frame = parse_frame(record["frame"])
        event = reducer.reduce_frame(frame, parse_utc(record["received_at_utc"]))
        if isinstance(event, FeedEvent):
            yield event
