from bisect import bisect_right
from dataclasses import dataclass
from typing import cast

from shared.constants.dataset import MODEL_START_SECOND
from shared.types.stratz import (
    StratzMatchBase,
    StratzPlayerPlayback,
    StratzPlayerUpdateGoldEvent,
    UsableStratzMatch,
)
from shared.utils.dota_levels import (
    level_at_second,
    radiant_xp_advantage,
    radiant_xp_advantage_at_second,
)
from shared.utils.stratz import death_times
from shared.utils.top_players import TopPlayerFeatures, build_top_player_features


@dataclass(frozen=True)
class ExactSecondState:
    match_id: int
    second: int
    radiant_win: bool
    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: TopPlayerFeatures


@dataclass(frozen=True)
class MinuteLeadMismatch:
    match_id: int
    second: int
    expected: int
    actual: int


class MinuteLeadMismatchError(ValueError):
    def __init__(self, mismatch: MinuteLeadMismatch) -> None:
        self.mismatch = mismatch
        super().__init__(
            f"match_id={mismatch.match_id} second={mismatch.second} "
            f"expected={mismatch.expected} actual={mismatch.actual}"
        )


@dataclass(frozen=True)
class _PlayerPlayback:
    radiant: bool
    gold_events: tuple[StratzPlayerUpdateGoldEvent, ...]
    level_seconds: tuple[int, ...]
    player_index: int


@dataclass
class _PlayerCursor:
    gold_index: int = 0
    networth: int = 0


def lead_array_index(second: int) -> int:
    """Map minute-boundary second to lead index: 0→1, 60→2 (index 0 is pre-horn -60)."""
    return second // 60 + 1


def validate_minute_leads(
    match_id: int,
    second: int,
    radiant_nw_adv: int,
    nw_leads: list[int],
) -> None:
    index = lead_array_index(second)
    if index >= len(nw_leads):
        raise ValueError(
            f"match_id={match_id} second={second} index={index} "
            f"radiantNetworthLeads length={len(nw_leads)}"
        )
    expected_nw = nw_leads[index]
    if radiant_nw_adv != expected_nw:
        raise MinuteLeadMismatchError(
            MinuteLeadMismatch(
                match_id=match_id,
                second=second,
                expected=expected_nw,
                actual=radiant_nw_adv,
            )
        )


def assert_minute_consistency(
    *,
    match: StratzMatchBase,
    second: int,
    player_playbacks: list[_PlayerPlayback],
    cursors: list[_PlayerCursor],
    radiant_nw_adv: int,
    nw_leads: list[int],
) -> None:
    validate_minute_leads(
        match_id=match["id"],
        second=second,
        radiant_nw_adv=radiant_nw_adv,
        nw_leads=nw_leads,
    )
    if second < 0:
        return
    npm_index = second // 60
    for playback, cursor in zip(player_playbacks, cursors, strict=True):
        player = match["players"][playback.player_index]
        expected_nw = player["stats"]["networthPerMinute"][npm_index]
        if cursor.networth != expected_nw:
            raise ValueError(
                f"match_id={match['id']} player_index={playback.player_index} "
                f"second={second} expected={expected_nw} actual={cursor.networth}"
            )


class MatchDataError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SideNetworths:
    radiant: tuple[int, ...]
    dire: tuple[int, ...]


def _split_by_side(match: StratzMatchBase, networths: list[int]) -> SideNetworths:
    radiant: list[int] = []
    dire: list[int] = []
    for player, networth in zip(match["players"], networths, strict=True):
        if player["isRadiant"]:
            radiant.append(networth)
        else:
            dire.append(networth)
    return SideNetworths(radiant=tuple(radiant), dire=tuple(dire))


def _playback_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
    networths: list[int] = []
    for player in match["players"]:
        playback_data = player.get("playbackData")
        if playback_data is None:
            return None
        playback = cast(StratzPlayerPlayback, playback_data)
        networth = 0
        for event in sorted(playback["playerUpdateGoldEvents"], key=lambda item: item["time"]):
            if event["time"] > second:
                break
            networth = event["networth"]
        networths.append(networth)
    return _split_by_side(match, networths)


def _minute_networths(match: StratzMatchBase, second: int) -> SideNetworths | None:
    if second < 0:
        return _playback_networths(match, second)
    npm_index = second // 60
    networths: list[int] = []
    for player in match["players"]:
        per_minute = player["stats"]["networthPerMinute"]
        if npm_index >= len(per_minute):
            raise MatchDataError("short networthPerMinute")
        networths.append(per_minute[npm_index])
    return _split_by_side(match, networths)


def build_second_state(
    match: StratzMatchBase,
    second: int,
    *,
    networths: SideNetworths,
    radiant_xp_adv: int,
    radiant_deaths: list[int],
    dire_deaths: list[int],
) -> ExactSecondState:
    radiant_nw = sum(networths.radiant)
    dire_nw = sum(networths.dire)
    return ExactSecondState(
        match_id=match["id"],
        second=second,
        radiant_win=match["didRadiantWin"],
        radiant_nw_adv=radiant_nw - dire_nw,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_xp_adv=radiant_xp_adv,
        deaths_radiant=bisect_right(radiant_deaths, second),
        deaths_dire=bisect_right(dire_deaths, second),
        top=build_top_player_features(networths.radiant, networths.dire),
    )


def build_minute_states(
    match: UsableStratzMatch, end_second_exclusive: int
) -> tuple[ExactSecondState, ...]:
    nw_leads = match["radiantNetworthLeads"]

    radiant_deaths = death_times(match, radiant=True)
    dire_deaths = death_times(match, radiant=False)
    states: list[ExactSecondState] = []
    for second in range(MODEL_START_SECOND, end_second_exclusive, 60):
        if lead_array_index(second) >= len(nw_leads):
            break
        networths = _minute_networths(match, second)
        if networths is None:
            continue
        state = build_second_state(
            match,
            second,
            networths=networths,
            radiant_xp_adv=radiant_xp_advantage_at_second(match["players"], second),
            radiant_deaths=radiant_deaths,
            dire_deaths=dire_deaths,
        )
        validate_minute_leads(match["id"], second, state.radiant_nw_adv, nw_leads)
        states.append(state)
    return tuple(states)


def collect_player_playbacks(match: StratzMatchBase) -> list[_PlayerPlayback]:
    player_playbacks: list[_PlayerPlayback] = []
    for player_index, player in enumerate(match["players"]):
        playback_data = player["playbackData"]
        if playback_data is None:
            raise ValueError(
                f"match_id={match['id']} player_index={player_index} player playbackData is missing"
            )
        playback = cast(StratzPlayerPlayback, playback_data)
        gold_events = tuple(
            sorted(playback["playerUpdateGoldEvents"], key=lambda event: event["time"])
        )
        player_playbacks.append(
            _PlayerPlayback(
                radiant=player["isRadiant"],
                gold_events=gold_events,
                level_seconds=tuple(player["stats"]["level"]),
                player_index=player_index,
            )
        )
    return player_playbacks


def build_exact_second_states(match: UsableStratzMatch) -> tuple[ExactSecondState, ...]:
    nw_leads = match["radiantNetworthLeads"]
    player_playbacks = collect_player_playbacks(match)

    radiant_deaths = death_times(match, True)
    dire_deaths = death_times(match, False)
    cursors = [_PlayerCursor() for _ in player_playbacks]
    rows: list[ExactSecondState] = []

    for second in range(MODEL_START_SECOND, match["durationSeconds"] + 1):
        radiant_levels: list[int] = []
        dire_levels: list[int] = []
        radiant_networths: list[int] = []
        dire_networths: list[int] = []
        for playback, cursor in zip(player_playbacks, cursors, strict=True):
            gold_events = playback.gold_events
            while (
                cursor.gold_index < len(gold_events)
                and gold_events[cursor.gold_index]["time"] <= second
            ):
                cursor.networth = gold_events[cursor.gold_index]["networth"]
                cursor.gold_index += 1

            if playback.radiant:
                radiant_levels.append(level_at_second(playback.level_seconds, second))
                radiant_networths.append(cursor.networth)
            else:
                dire_levels.append(level_at_second(playback.level_seconds, second))
                dire_networths.append(cursor.networth)
        state = build_second_state(
            match,
            second,
            networths=SideNetworths(
                radiant=tuple(radiant_networths),
                dire=tuple(dire_networths),
            ),
            radiant_xp_adv=radiant_xp_advantage(radiant_levels, dire_levels),
            radiant_deaths=radiant_deaths,
            dire_deaths=dire_deaths,
        )
        if second % 60 == 0:
            assert_minute_consistency(
                match=match,
                second=second,
                player_playbacks=player_playbacks,
                cursors=cursors,
                radiant_nw_adv=state.radiant_nw_adv,
                nw_leads=nw_leads,
            )
        rows.append(state)

    return tuple(rows)
