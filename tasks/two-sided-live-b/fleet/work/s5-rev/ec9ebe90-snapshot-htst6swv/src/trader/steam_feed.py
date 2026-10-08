"""Reduce archived Steam GetRealtimeStats payloads to GameSnapshot for the viewer.

Steam is no longer a live feed; old `state.jsonl` archives still replay through here.
"""

from dataclasses import dataclass

from shared.utils.dota_levels import radiant_xp_advantage
from shared.utils.top_players import build_top_player_features
from trader.live_feed import GameSnapshot, MatchPhase
from trader.steam_types import SteamRealtimeMatch, SteamRealtimeStats, SteamRealtimeTeam

RADIANT_TEAM_NUMBER = 2
DIRE_TEAM_NUMBER = 3
GAME_STATE_PRE_GAME = 4
GAME_STATE_IN_PROGRESS = 5
GAME_STATE_POST_GAME = 6


def match_phase(game_state: int) -> MatchPhase:
    """Map one Steam game_state number onto the source-neutral match phase."""
    if game_state == GAME_STATE_PRE_GAME:
        return MatchPhase.PRE_HORN
    if game_state == GAME_STATE_IN_PROGRESS:
        return MatchPhase.IN_PROGRESS
    if game_state == GAME_STATE_POST_GAME:
        return MatchPhase.FINISHED
    return MatchPhase.PRE_MATCH


@dataclass(frozen=True)
class _ResolvedTeams:
    """The Radiant and Dire blocks of one payload, found by Steam team number."""

    radiant: SteamRealtimeTeam
    dire: SteamRealtimeTeam


def _resolve_teams(payload: SteamRealtimeStats) -> _ResolvedTeams:
    """Address the two sides by Steam team number: 2 is Radiant, 3 is Dire."""
    teams = {team["team_number"]: team for team in payload["teams"]}
    return _ResolvedTeams(radiant=teams[RADIANT_TEAM_NUMBER], dire=teams[DIRE_TEAM_NUMBER])


def _side_networths(team: SteamRealtimeTeam) -> list[int]:
    """Project one Steam side onto its per-player net worths."""
    return [player["net_worth"] for player in team["players"]]


def _is_paused(match: SteamRealtimeMatch, previous_snapshot: GameSnapshot | None) -> bool:
    """True on the first tick (unknown) or when timestamp - game_time grew."""
    if previous_snapshot is None:
        return True
    return (
        match["timestamp"] - match["game_time"]
        > previous_snapshot.server_timestamp - previous_snapshot.second
    )


def build_game_snapshot(
    payload: SteamRealtimeStats,
    previous_snapshot: GameSnapshot | None,
) -> GameSnapshot:
    """Reduce one live payload to the frozen per-second numbers the consumers read."""
    match = payload["match"]
    teams = _resolve_teams(payload)
    radiant_levels = [player["level"] for player in teams.radiant["players"]]
    dire_levels = [player["level"] for player in teams.dire["players"]]
    top = build_top_player_features(_side_networths(teams.radiant), _side_networths(teams.dire))
    radiant_nw = teams.radiant["net_worth"]
    dire_nw = teams.dire["net_worth"]
    return GameSnapshot(
        second=match["game_time"],
        server_timestamp=match["timestamp"],
        phase=match_phase(match["game_state"]),
        radiant_nw_adv=radiant_nw - dire_nw,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=radiant_xp_advantage(radiant_levels, dire_levels),
        deaths_radiant=sum(player["death_count"] for player in teams.radiant["players"]),
        deaths_dire=sum(player["death_count"] for player in teams.dire["players"]),
        top=top,
        paused=_is_paused(match, previous_snapshot),
    )
