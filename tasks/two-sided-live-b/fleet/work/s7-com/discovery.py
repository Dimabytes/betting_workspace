"""Discovery of tradable markets: collector sidecars, GRID, Oddin, and Steam when present.

GRID-only when Steam is None. Steam live-list linking is Dota and only binds sides
and ids; Steam is never a trading feed.
"""

import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic as monotonic_clock
from typing import cast

from shared.utils.log import get_logger
from shared.utils.lol_leagues import parse_event_league
from shared.utils.series_format import (
    DECIDER_BEST_OF,
    SERIES_TYPE_TO_BEST_OF,
    best_of_from_grid_format,
    series_winner_covers_map,
)
from shared.utils.team_names import orient_outcomes
from trader.bindings import DiscoveredMatch, MarketReference, TeamSides
from trader.collector_sidecars import FreshSidecar, scan_sidecars
from trader.game_profile import GameProfile
from trader.grid_feed import read_board_sides
from trader.grid_widgets import LIVE_STATUS, Scoreboard
from trader.lol_league_filter import LolLeagueFilter
from trader.oddin_discovery import unique_oddin_match_id
from trader.oddin_types import OddinMatch
from trader.source_picker import GridProbeCycle
from trader.steam_client import (
    JSON_FAILED,
    STEAM_LIVE_LEAGUE_GAMES_API,
    SteamClient,
    decode_json,
)
from trader.steam_types import SteamLiveLeagueGamesResponse
from trader.strict_json import (
    StrictJsonError,
    require_int,
    require_nonempty_str,
    require_nullable_int,
    require_object,
)

logger = get_logger(__name__)

SERIES_ENDED_SKIP_SECONDS = 3600.0


def grid_match_id(series_id: str, map_number: int) -> str:
    """Archive and session id for one GRID series map: `grid-{series}-m{map}`."""
    return f"grid-{series_id}-m{map_number}"


def archive_id_kind(match_id: str) -> str:
    """`grid` for a GRID series-map id, otherwise `steam`."""
    if match_id.startswith("grid-"):
        return "grid"
    return "steam"


_LEAGUE_FIELD = "live-list field"

LIVE_LEAGUE_LABEL = "get-live-league-games"


def league_matches(title: str | None, names: tuple[str, ...]) -> bool:
    league = parse_event_league(None, title)
    if league is None:
        return False
    folded = league.casefold()
    return any(name.casefold() in folded for name in names)


@dataclass(frozen=True)
class _UsableLeagueGame:
    """One usable GetLiveLeagueGames row: the fields matching and emission read."""

    match_id: int
    league_id: int
    radiant_name: str
    dire_name: str
    series_type: int | None
    radiant_series_wins: int
    dire_series_wins: int

    @property
    def current_map(self) -> int:
        """The map number being played: the series wins so far plus one."""
        return self.radiant_series_wins + self.dire_series_wins + 1


@dataclass(frozen=True)
class _NameLink:
    """One threshold-passing market/game name orientation."""

    game: _UsableLeagueGame
    yes_is_radiant: bool


@dataclass(frozen=True)
class _SideSource:
    """One place a market can read its two sides, its live map, and its best-of.

    Steam builds one per name-linked live-list row. GRID builds one per probed
    scoreboard. Every gate after this point treats both the same way.
    """

    match_id: str
    steam_match_id: str | None
    league_id: int | None
    tournament: str | None
    sides: TeamSides
    current_map: int
    best_of: int | None
    yes_is_radiant: bool
    grid_series_id: str | None
    oddin_match_id: str | None


@dataclass(frozen=True)
class _GridLook:
    """GRID's source for this market, or why it is not ready yet."""

    source: _SideSource | None
    sides_pending: bool


@dataclass(frozen=True)
class _SidecarSources:
    """One sidecar's sources in precedence order, plus why candidates dropped."""

    sources: tuple[_SideSource, ...]
    name_missed: bool


@dataclass(frozen=True)
class _CycleScan:
    """Everything one cycle looked up before it starts gating markets."""

    games_by_match: Mapping[int, _UsableLeagueGame]
    boards: Mapping[str, Scoreboard]
    grid_maps: Mapping[str, int]
    grid_map_by_match: Mapping[int, int]
    grid_map_conflicts: int
    event_series: Mapping[str, str]
    oddin_matches: tuple[OddinMatch, ...]


@dataclass(frozen=True)
class _CycleResult:
    """One resolve pass: emitted matches and skip counts."""

    matches: tuple[DiscoveredMatch, ...] = ()
    map_mismatches: int = 0
    name_misses: int = 0
    series_skips: int = 0
    grid_map_conflicts: int = 0


@dataclass(frozen=True)
class _GridMapScan:
    """The GRID map per Steam match id, and how many matches reported conflicting maps."""

    by_match: Mapping[int, int]
    conflicts: int


class MarketDiscovery:
    """Synchronous market linker bound to one archive root, optional Steam, and a game."""

    def __init__(
        self,
        archive_root: Path,
        steam_client: SteamClient | None,
        probe_grid_scoreboards: Callable[[Iterable[str]], GridProbeCycle],
        list_oddin_matches: Callable[[], Sequence[OddinMatch]],
        profile: GameProfile,
        title_blacklist: tuple[str, ...],
        title_whitelist: tuple[str, ...],
    ) -> None:
        self._archive_root = archive_root
        self._steam_client = steam_client
        self._probe_grid_scoreboards = probe_grid_scoreboards
        self._list_oddin_matches = list_oddin_matches
        self._profile = profile
        self._title_blacklist = title_blacklist
        self._title_whitelist = title_whitelist
        self._title_skips: set[str] = set()
        self._league_filter = LolLeagueFilter(archive_root) if profile.game == "lol" else None
        self._last_live: dict[str, Scoreboard] = {}
        self._ended_until: dict[str, float] = {}
        self._unpublished_logged: set[str] = set()

    def discover(self) -> tuple[DiscoveredMatch, ...]:
        """Run one synchronous discovery cycle and return the linked matches."""
        scan = scan_sidecars(self._archive_root, time.time())
        tradeable = tuple(sidecar for sidecar in scan.sidecars if sidecar.is_tradeable())
        eligible = self._drop_skipped_titles(tradeable)
        record_only: tuple[FreshSidecar, ...] = ()
        if self._league_filter is not None:
            selection = self._league_filter.select_sidecars(eligible)
            eligible = selection.traded + selection.record_only
            record_only = selection.record_only
            if not eligible:
                return ()
        if self._steam_client is None or not eligible:
            games = ()
        else:
            games = _fetch_live_league_games(self._steam_client)
        oddin_matches = self._list_oddin_matches() if self._profile.uses_steam and eligible else ()
        result = _resolve_cycle(
            eligible,
            games,
            self._probe_with_end_skip,
            oddin_matches,
            self._profile,
        )
        logger.debug(
            "discovery cycle: game=%s archive=%s fresh=%d invalid=%d eligible=%d steam_games=%d "
            "map_mismatches=%d name_misses=%d series_skips=%d "
            "grid_map_conflicts=%d emitted=%d",
            self._profile.game,
            self._archive_root,
            len(scan.sidecars),
            scan.invalid_count,
            len(eligible),
            len(games),
            result.map_mismatches,
            result.name_misses,
            result.series_skips,
            result.grid_map_conflicts,
            len(result.matches),
        )
        matches = _mark_record_only(result.matches, record_only)
        for match in matches:
            _log_emit(match)
        return matches

    def _title_skip_reason(self, title: str | None) -> str | None:
        if league_matches(title, self._title_blacklist):
            return "title_blacklist"
        if self._title_whitelist and not league_matches(title, self._title_whitelist):
            return "title_whitelist"
        return None

    def _drop_skipped_titles(self, sidecars: tuple[FreshSidecar, ...]) -> tuple[FreshSidecar, ...]:
        kept: list[FreshSidecar] = []
        skipped: set[str] = set()
        for sidecar in sidecars:
            reason = self._title_skip_reason(sidecar.event_title)
            if reason is None:
                kept.append(sidecar)
                continue
            cid = sidecar.condition_id
            log = logger.debug if cid in self._title_skips else logger.info
            log("discovery skip reason=%s cid=%s title=%r", reason, cid, sidecar.event_title)
            skipped.add(cid)
        self._title_skips = skipped
        return tuple(kept)

    def _drop_expired_ended(self, now: float) -> None:
        """Forget series whose skip window has elapsed."""
        expired = [series_id for series_id, until in self._ended_until.items() if now >= until]
        for series_id in expired:
            del self._ended_until[series_id]

    def _probe_with_end_skip(self, series_ids: Iterable[str]) -> Mapping[str, Scoreboard]:
        """Probe GRID, skip decided series for an hour, and log unpublished transitions."""
        now = monotonic_clock()
        self._drop_expired_ended(now)
        to_probe = tuple(
            series_id for series_id in sorted(set(series_ids)) if series_id not in self._ended_until
        )
        cycle = self._probe_grid_scoreboards(to_probe)
        for series_id, board in cycle.boards.items():
            if board.series_status == LIVE_STATUS:
                self._last_live[series_id] = board
            if series_id in self._unpublished_logged:
                logger.info("grid widget recovered series=%s", series_id)
                self._unpublished_logged.discard(series_id)
        for series_id in cycle.unpublished:
            last = self._last_live.get(series_id)
            if last is not None and _series_is_decided(last):
                self._ended_until[series_id] = now + SERIES_ENDED_SKIP_SECONDS
                self._unpublished_logged.discard(series_id)
                logger.info("grid series ended, skip probes series=%s", series_id)
                continue
            if series_id not in self._unpublished_logged:
                logger.info("grid scoreboard probe failed series=%s: InvalidStatus", series_id)
                self._unpublished_logged.add(series_id)
                continue
            logger.debug("grid scoreboard probe failed series=%s: InvalidStatus", series_id)
        return cycle.boards


def _series_is_decided(board: Scoreboard) -> bool:
    """True when a live scoreboard already has a series-winning maps_won count."""
    best_of = best_of_from_grid_format(board.series_format)
    if best_of is None:
        return False
    needed = best_of // 2 + 1
    return any(team.maps_won >= needed for team in board.teams)


def _fetch_live_league_games(steam_client: SteamClient) -> tuple[_UsableLeagueGame, ...]:
    """Fetch GetLiveLeagueGames once and project its usable league rows."""
    response = steam_client.get_ok(STEAM_LIVE_LEAGUE_GAMES_API, {}, LIVE_LEAGUE_LABEL)
    if response is None:
        return ()
    document = decode_json(response, LIVE_LEAGUE_LABEL)
    if document is JSON_FAILED:
        return ()
    try:
        parsed = cast(SteamLiveLeagueGamesResponse, document)
        games_raw = cast(object, parsed["result"]["games"])
    except (KeyError, TypeError):
        logger.warning("%s payload malformed", LIVE_LEAGUE_LABEL)
        return ()
    if not isinstance(games_raw, list):
        logger.warning("%s payload malformed", LIVE_LEAGUE_LABEL)
        return ()
    games: list[_UsableLeagueGame] = []
    for row in cast(list[object], games_raw):
        game = _parse_league_game(row)
        if game is not None:
            games.append(game)
    return tuple(games)


def _read_team_name(row: Mapping[str, object], field: str) -> str:
    """Read one nonempty team_name from a GetLiveLeagueGames team block."""
    team = require_object(row.get(field), f"live-list {field}")
    return require_nonempty_str(team, "team_name", _LEAGUE_FIELD)


def _parse_league_game(row: object) -> _UsableLeagueGame | None:
    """Project one live-list row to its usable fields, or None when unusable."""
    try:
        game = require_object(row, "live-list row")
        match_id = require_int(game, "match_id", _LEAGUE_FIELD)
        radiant_series_wins = require_int(game, "radiant_series_wins", _LEAGUE_FIELD)
        dire_series_wins = require_int(game, "dire_series_wins", _LEAGUE_FIELD)
        if match_id <= 0 or radiant_series_wins < 0 or dire_series_wins < 0:
            return None
        return _UsableLeagueGame(
            match_id=match_id,
            league_id=require_int(game, "league_id", _LEAGUE_FIELD),
            radiant_name=_read_team_name(game, "radiant_team"),
            dire_name=_read_team_name(game, "dire_team"),
            series_type=require_nullable_int(game, "series_type", _LEAGUE_FIELD),
            radiant_series_wins=radiant_series_wins,
            dire_series_wins=dire_series_wins,
        )
    except StrictJsonError:
        return None


def _index_usable_games(games: tuple[_UsableLeagueGame, ...]) -> dict[int, _UsableLeagueGame]:
    """Keep unconflicted live-list rows; duplicate match ids with different fields drop."""
    games_by_match: dict[int, _UsableLeagueGame] = {}
    conflicted: set[int] = set()
    for game in games:
        if game.match_id in conflicted:
            continue
        previous = games_by_match.get(game.match_id)
        if previous is None:
            games_by_match[game.match_id] = game
            continue
        if previous != game:
            del games_by_match[game.match_id]
            conflicted.add(game.match_id)
            logger.warning("get-live-league-games: conflicting rows for match %d", game.match_id)
    return games_by_match


def _grid_map_per_steam_match(
    eligible: tuple[FreshSidecar, ...],
    links: Mapping[str, tuple[_NameLink, ...]],
    grid_maps: Mapping[str, int],
) -> _GridMapScan:
    """Attach the probed GRID map to each name-linked Steam match.

    Match Winner sidecars often omit `grid_series_id`. The map still applies
    when a name-linked Game-N sidecar of the same Steam game carried one. Two
    series that disagree on the map of one Steam game are a conflict: the
    match keeps the Steam number and the cycle counts the drop.
    """
    series_by_match: dict[int, set[str]] = defaultdict(set)
    for sidecar in eligible:
        series_id = sidecar.grid_series_id
        if series_id is None:
            continue
        for link in links[sidecar.condition_id]:
            series_by_match[link.game.match_id].add(series_id)
    by_match: dict[int, int] = {}
    conflicts = 0
    for match_id, linked_series in series_by_match.items():
        maps = {grid_maps[series_id] for series_id in linked_series if series_id in grid_maps}
        if len(maps) > 1:
            conflicts += 1
            logger.warning(
                "conflicting grid maps for steam match %d: series %s report maps %s",
                match_id,
                ", ".join(sorted(linked_series)),
                ", ".join(str(number) for number in sorted(maps)),
            )
            continue
        if not maps:
            continue
        by_match[match_id] = next(iter(maps))
    return _GridMapScan(by_match=by_match, conflicts=conflicts)


def _unique_event_series(eligible: tuple[FreshSidecar, ...]) -> dict[str, str]:
    """Map event_id to its unique Game-N grid_series_id; conflicts are omitted."""
    series_by_event: dict[str, set[str]] = defaultdict(set)
    for sidecar in eligible:
        if sidecar.market_kind != "map_winner" or sidecar.grid_series_id is None:
            continue
        series_by_event[sidecar.event_id].add(sidecar.grid_series_id)
    unique: dict[str, str] = {}
    for event_id, series_ids in series_by_event.items():
        if len(series_ids) != 1:
            logger.warning(
                "conflicting grid series for event %s: %s",
                event_id,
                ", ".join(sorted(series_ids)),
            )
            continue
        unique[event_id] = next(iter(series_ids))
    return unique


def _effective_grid_series_id(sidecar: FreshSidecar, event_series: Mapping[str, str]) -> str | None:
    """Sidecar series id, or the unique Game-N series of its event."""
    if sidecar.grid_series_id is not None:
        return sidecar.grid_series_id
    return event_series.get(sidecar.event_id)


def _link_steam_games(
    sidecar: FreshSidecar,
    games_by_match: Mapping[int, _UsableLeagueGame],
    aliases: Mapping[str, tuple[str, ...]],
) -> tuple[_NameLink, ...]:
    """Every live-list game whose Radiant/Dire names orient against this market."""
    links: list[_NameLink] = []
    for game in games_by_match.values():
        orientation = orient_outcomes(
            sidecar.outcome_0_name,
            sidecar.outcome_1_name,
            game.radiant_name,
            game.dire_name,
            aliases,
        )
        if orientation is not None:
            links.append(_NameLink(game=game, yes_is_radiant=orientation))
    return tuple(links)


def _steam_source(
    sidecar: FreshSidecar,
    links: tuple[_NameLink, ...],
    grid_map_by_match: Mapping[int, int],
    grid_series_id: str | None,
) -> _SideSource | None:
    """The Steam source of this market, or None when no single game links to it.

    Names that link two live games drop the Steam source. Oddin stamps onto this
    Steam identity later; it does not rebuild it.
    """
    if len(links) > 1:
        logger.warning(
            "ambiguous sidecar %s (market %s): names link %d steam games (%s)",
            sidecar.condition_id,
            sidecar.market_slug,
            len(links),
            ", ".join(str(link.game.match_id) for link in links),
        )
        return None
    if not links:
        return None
    game = links[0].game
    best_of = SERIES_TYPE_TO_BEST_OF.get(game.series_type) if game.series_type is not None else None
    return _SideSource(
        match_id=str(game.match_id),
        steam_match_id=str(game.match_id),
        league_id=game.league_id,
        tournament=None,
        sides=TeamSides(radiant=game.radiant_name, dire=game.dire_name),
        current_map=grid_map_by_match.get(game.match_id, game.current_map),
        best_of=best_of,
        yes_is_radiant=links[0].yes_is_radiant,
        grid_series_id=grid_series_id,
        oddin_match_id=None,
    )


def _grid_source(
    sidecar: FreshSidecar,
    boards: Mapping[str, Scoreboard],
    series_id: str | None,
    profile: GameProfile,
) -> _GridLook:
    """GRID's source for this market, or pending/unresolved when the board cannot supply one."""
    if series_id is None:
        return _GridLook(source=None, sides_pending=False)
    board = boards.get(series_id)
    if board is None:
        return _GridLook(source=None, sides_pending=False)
    read = read_board_sides(
        board,
        sidecar.outcome_0_name,
        sidecar.outcome_1_name,
        profile.side_0_text,
        profile.side_1_text,
        profile.aliases,
    )
    if read.sides is None:
        return _GridLook(source=None, sides_pending=read.pending)
    sides = read.sides
    current_map = board.game_number
    return _GridLook(
        source=_SideSource(
            match_id=grid_match_id(series_id, current_map),
            steam_match_id=None,
            league_id=None,
            tournament=board.tournament,
            sides=TeamSides(radiant=sides.radiant_name, dire=sides.dire_name),
            current_map=current_map,
            best_of=best_of_from_grid_format(board.series_format),
            yes_is_radiant=sides.outcome_0_is_radiant,
            grid_series_id=series_id,
            oddin_match_id=None,
        ),
        sides_pending=False,
    )


def _bind_oddin(
    sources: tuple[_SideSource, ...],
    matches: Sequence[OddinMatch],
    aliases: Mapping[str, tuple[str, ...]],
    playing: dict[str, bool],
) -> tuple[_SideSource, ...]:
    """Stamp the unique Oddin match id onto existing Steam/GRID sources."""
    if not sources:
        return sources
    first = sources[0]
    oddin_match_id = unique_oddin_match_id(
        first.sides.radiant, first.sides.dire, matches, aliases, playing
    )
    if oddin_match_id is None:
        return sources
    return tuple(replace(source, oddin_match_id=oddin_match_id) for source in sources)


def _sources_for_sidecar(
    sidecar: FreshSidecar,
    links: tuple[_NameLink, ...],
    scan: _CycleScan,
    profile: GameProfile,
    playing: dict[str, bool],
) -> _SidecarSources:
    """This market's sources, Steam first and GRID as the fallback.

    Steam may still supply ids. GRID sides win once GRID oriented
    this map. An incomplete GRID board (no Radiant/Dire yet) blocks Steam
    sides so a leftover map-1 listing cannot stamp map-2 orientation.

    Oddin stamps onto an already-bound Steam/GRID source. While GRID sides
    are pending, Steam+Oddin may still emit on Steam orientation.

    `name_missed` is True when candidates existed on either side but none of
    their names oriented against this market. A pending GRID board is not a miss.
    """
    if profile.uses_steam:
        series_id = _effective_grid_series_id(sidecar, scan.event_series)
    else:
        series_id = sidecar.grid_series_id
    steam = _steam_source(sidecar, links, scan.grid_map_by_match, series_id)
    look = _grid_source(sidecar, scan.boards, series_id, profile)
    sources = _prefer_grid_sides(steam, look)
    candidates = sources or ((steam,) if steam is not None and look.sides_pending else ())
    bound = _bind_oddin(candidates, scan.oddin_matches, profile.aliases, playing)
    sources = bound if sources or (bound and bound[0].oddin_match_id is not None) else ()
    had_candidate = bool(scan.games_by_match) or (
        series_id is not None and series_id in scan.boards
    )
    name_missed = had_candidate and not sources and not look.sides_pending
    return _SidecarSources(sources=sources, name_missed=name_missed)


def _prefer_grid_sides(steam: _SideSource | None, look: _GridLook) -> tuple[_SideSource, ...]:
    """Keep Steam ids; take Radiant/Dire from GRID when GRID has this map."""
    grid = look.source
    if grid is not None:
        if steam is None:
            return (grid,)
        oriented = replace(steam, sides=grid.sides, yes_is_radiant=grid.yes_is_radiant)
        return (oriented, grid)
    if look.sides_pending or steam is None:
        return ()
    return (steam,)


def _scan_cycle(
    eligible: tuple[FreshSidecar, ...],
    games_by_match: Mapping[int, _UsableLeagueGame],
    links: Mapping[str, tuple[_NameLink, ...]],
    probe_grid_scoreboards: Callable[[Iterable[str]], Mapping[str, Scoreboard]],
    oddin_matches: Sequence[OddinMatch],
) -> _CycleScan:
    """Probe every series once and derive both map views from that one pass."""
    event_series = _unique_event_series(eligible)
    series_ids = {
        series_id
        for sidecar in eligible
        if (series_id := _effective_grid_series_id(sidecar, event_series)) is not None
    }
    boards = probe_grid_scoreboards(series_ids)
    grid_maps = {series_id: board.game_number for series_id, board in boards.items()}
    map_scan = _grid_map_per_steam_match(eligible, links, grid_maps)
    return _CycleScan(
        games_by_match=games_by_match,
        boards=boards,
        grid_maps=grid_maps,
        grid_map_by_match=map_scan.by_match,
        grid_map_conflicts=map_scan.conflicts,
        event_series=event_series,
        oddin_matches=tuple(oddin_matches),
    )


def _resolve_cycle(
    eligible: tuple[FreshSidecar, ...],
    games: tuple[_UsableLeagueGame, ...],
    probe_grid_scoreboards: Callable[[Iterable[str]], Mapping[str, Scoreboard]],
    oddin_matches: Sequence[OddinMatch],
    profile: GameProfile,
) -> _CycleResult:
    """Bind eligible markets to one side source each and emit the unambiguous matches.

    Every market tries Steam first and GRID second, and takes the first source
    that clears the kind gate and the map gate. Name misses, map mismatches and
    series_winner kind-gate rejects increment cycle counts. Ambiguities and
    a Steam-only source with no grid_series_id and no Oddin match stay log-only.
    """
    games_by_match = _index_usable_games(games) if games else {}
    links = {
        sidecar.condition_id: _link_steam_games(sidecar, games_by_match, profile.aliases)
        for sidecar in eligible
    }
    scan = _scan_cycle(eligible, games_by_match, links, probe_grid_scoreboards, oddin_matches)
    map_winner_keys = frozenset(
        (sidecar.event_id, sidecar.map_number)
        for sidecar in eligible
        if sidecar.market_kind == "map_winner" and sidecar.map_number is not None
    )
    bound: list[DiscoveredMatch] = []
    map_mismatches = 0
    name_misses = 0
    series_skips = 0
    playing: dict[str, bool] = {}
    for sidecar in eligible:
        candidates = _sources_for_sidecar(
            sidecar, links[sidecar.condition_id], scan, profile, playing
        )
        if candidates.name_missed:
            name_misses += 1
        for source in candidates.sources:
            expected_map = _expected_map(
                sidecar, source.best_of, map_winner_keys, source.current_map
            )
            if expected_map is None:
                series_skips += 1
                continue
            if expected_map != source.current_map:
                map_mismatches += 1
                continue
            if source.grid_series_id is None and source.oddin_match_id is None:
                logger.info(
                    "no grid_series_id or oddin match for steam match %s: condition %s skipped",
                    source.match_id,
                    sidecar.condition_id,
                )
                continue
            bound.append(_build_match(sidecar, source, profile.game))
            break
    return _CycleResult(
        _emit_unambiguous(bound),
        map_mismatches,
        name_misses,
        series_skips,
        scan.grid_map_conflicts,
    )


def _emit_unambiguous(bound: list[DiscoveredMatch]) -> tuple[DiscoveredMatch, ...]:
    """Keep one match per id; ids claimed by two condition ids drop with a warning."""
    by_id: dict[str, list[DiscoveredMatch]] = defaultdict(list)
    for match in bound:
        by_id[match.match_id].append(match)
    matches: list[DiscoveredMatch] = []
    for match_id in sorted(by_id):
        group = by_id[match_id]
        conditions = {match.market.condition_id for match in group}
        if len(conditions) > 1:
            logger.warning(
                "ambiguous match %s: multiple markets survive (%s)",
                match_id,
                ", ".join(sorted(conditions)),
            )
            continue
        matches.append(group[0])
    matches.sort(key=lambda match: (match.match_id, match.market.condition_id))
    return tuple(matches)


def merge_cycle_matches(
    chunks: tuple[tuple[DiscoveredMatch, ...], ...],
) -> tuple[DiscoveredMatch, ...]:
    """Collapse one cadence tick of per-game discoveries; ambiguous match ids drop."""
    bound = [match for chunk in chunks for match in chunk]
    return _emit_unambiguous(bound)


def _expected_map(
    sidecar: FreshSidecar,
    best_of: int | None,
    map_winner_keys: frozenset[tuple[str, int]],
    current_map: int,
) -> int | None:
    """Return the market's expected map number, or None when its kind gate rejects.

    `map_winner` always expects its own positive mapNumber. `series_winner`
    covers the series last map (BO1 map 1, BO3 map 3, BO5 map 5) when the
    source's live map equals that best-of and no eligible Game-N sidecar exists
    for the event. `best_of` comes from the Steam series type or the GRID
    series format, whichever source the caller is trying.
    """
    if sidecar.market_kind == "map_winner":
        return sidecar.map_number
    if best_of is None or best_of not in DECIDER_BEST_OF:
        return None
    map_winner_exists = (sidecar.event_id, best_of) in map_winner_keys
    if series_winner_covers_map(best_of, current_map, map_winner_exists=map_winner_exists):
        return best_of
    return None


def _archive_match_id(source: _SideSource) -> str:
    """Canonical archive id: GRID series+map when known, else the source's native id."""
    series_id = source.grid_series_id
    if series_id is not None:
        return grid_match_id(series_id, source.current_map)
    return source.match_id


def _mark_record_only(
    matches: tuple[DiscoveredMatch, ...], record_only: tuple[FreshSidecar, ...]
) -> tuple[DiscoveredMatch, ...]:
    """Flag every emitted match whose market came from a record-only sidecar."""
    cids = {sidecar.condition_id for sidecar in record_only}
    return tuple(
        replace(match, record_only=True) if match.market.condition_id in cids else match
        for match in matches
    )


def _log_emit(match: DiscoveredMatch) -> None:
    """Write one INFO line for a market that survived this cycle's gates."""
    logger.info(
        "discovery emit: game=%s match_id=%s steam_match_id=%s grid_series_id=%s "
        "oddin_match_id=%s map=%s slug=%s cid=%s archive_id_kind=%s",
        match.game,
        match.match_id,
        match.steam_match_id,
        match.market.grid_series_id,
        match.oddin_match_id,
        match.map_number,
        match.market.market_slug,
        match.market.condition_id,
        archive_id_kind(match.match_id),
    )


def _build_match(sidecar: FreshSidecar, source: _SideSource, game: str) -> DiscoveredMatch:
    """Project one sidecar, its winning side source, and game identity onto the handoff."""
    return DiscoveredMatch(
        match_id=_archive_match_id(source),
        game=game,
        steam_match_id=source.steam_match_id,
        league_id=source.league_id,
        tournament=source.tournament,
        sides=source.sides,
        map_number=source.current_map,
        market=MarketReference(
            condition_id=sidecar.condition_id,
            market_slug=sidecar.market_slug,
            event_slug=sidecar.event_slug,
            yes_token_id=sidecar.outcome_0_token,
            no_token_id=sidecar.outcome_1_token,
            yes_is_radiant=source.yes_is_radiant,
            outcome_0_name=sidecar.outcome_0_name,
            outcome_1_name=sidecar.outcome_1_name,
            tick_size=sidecar.tick_size,
            min_order_size=sidecar.min_order_size,
            neg_risk=sidecar.neg_risk,
            grid_series_id=source.grid_series_id,
        ),
        market_kind=sidecar.market_kind,
        oddin_match_id=source.oddin_match_id,
    )
