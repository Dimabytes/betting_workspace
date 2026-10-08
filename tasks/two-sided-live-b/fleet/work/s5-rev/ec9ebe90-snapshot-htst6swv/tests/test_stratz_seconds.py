from typing import cast

import pytest

from prepare_dataset.stratz_seconds import (
    ExactSecondState,
    MatchDataError,
    MinuteLeadMismatchError,
    build_exact_second_states,
    build_minute_states,
)
from shared.constants.dataset import MODEL_START_SECOND
from shared.types.stratz import (
    StratzPlayer,
    StratzPlayerDeathEvent,
    StratzPlayerUpdateGoldEvent,
    UsableStratzMatch,
)


def build_gold_event(time: int, networth: int) -> StratzPlayerUpdateGoldEvent:
    """Build the minimal typed absolute-networth playback event for a test."""
    return cast(
        StratzPlayerUpdateGoldEvent,
        {
            "time": time,
            "gold": 0,
            "networth": networth,
            "networthDifference": 0,
            "unreliableGold": 0,
        },
    )


def build_player(
    radiant: bool,
    gold_events: list[StratzPlayerUpdateGoldEvent],
    level_seconds: list[int],
    death_event_times: list[int],
) -> StratzPlayer:
    """Build the playback, level, and death fields used by exact-second tests."""
    death_events = [
        cast(StratzPlayerDeathEvent, {"time": time, "timeDead": 0}) for time in death_event_times
    ]
    networth_per_minute: list[int] = []
    for minute in range(2):
        networth = 0
        for event in gold_events:
            if event["time"] <= 60 * minute:
                networth = event["networth"]
        networth_per_minute.append(networth)
    return cast(
        StratzPlayer,
        {
            "isRadiant": radiant,
            "steamAccountId": (
                (1 if radiant else 2)
                + 10 * len(level_seconds)
                + 100 * len(death_event_times)
                + 1000 * (gold_events[0]["networth"] if gold_events else 0)
            ),
            "playbackData": {"playerUpdateGoldEvents": gold_events},
            "level": len(level_seconds),
            "stats": {
                "level": level_seconds,
                "deathEvents": death_events,
                "networthPerMinute": networth_per_minute,
            },
        },
    )


def build_match(
    match_id: int,
    duration_seconds: int,
    radiant_win: bool,
    players: list[StratzPlayer],
    nw_leads: list[int],
) -> UsableStratzMatch:
    """Build the match identity, networth leads, and players used by exact-second tests."""
    return cast(
        UsableStratzMatch,
        {
            "id": match_id,
            "durationSeconds": duration_seconds,
            "startDateTime": 1_700_000_000,
            "didRadiantWin": radiant_win,
            "radiantNetworthLeads": nw_leads,
            "players": players,
        },
    )


def state_at(rows: tuple[ExactSecondState, ...], second: int) -> ExactSecondState:
    """Return the exact-second row for a game second."""
    return rows[second - MODEL_START_SECOND]


def test_builds_seconds_zero_through_duration() -> None:
    """Emit one ordered state row from MODEL_START through duration inclusive."""
    player = build_player(True, [], [-1], [])
    match = build_match(100, 3, True, [player], [0, 0])

    rows = build_exact_second_states(match)

    assert [row.second for row in rows] == list(range(MODEL_START_SECOND, 4))
    assert all(row.match_id == 100 for row in rows)


def test_networth_forward_fills_absolute_updates() -> None:
    """Forward-fill each player's absolute networth without crossing players."""
    radiant = build_player(
        True,
        [build_gold_event(10, 100), build_gold_event(30, 200)],
        [-1],
        [],
    )
    dire = build_player(False, [build_gold_event(5, 50)], [-1], [])
    match = build_match(200, 35, False, [radiant, dire], [0, 0])

    rows = build_exact_second_states(match)

    assert state_at(rows, 0).radiant_nw_adv == 0
    assert state_at(rows, 0).radiant_nw == 0
    assert state_at(rows, 0).dire_nw == 0
    assert state_at(rows, 5).radiant_nw_adv == -50
    assert state_at(rows, 5).radiant_nw == 0
    assert state_at(rows, 5).dire_nw == 50
    assert state_at(rows, 10).radiant_nw_adv == 50
    assert state_at(rows, 10).radiant_nw == 100
    assert state_at(rows, 10).dire_nw == 50
    assert state_at(rows, 29).radiant_nw_adv == 50
    assert state_at(rows, 30).radiant_nw_adv == 150
    assert state_at(rows, 30).radiant_nw == 200
    assert state_at(rows, 30).dire_nw == 50


def test_prehorn_gold_applies_at_second_zero() -> None:
    """Apply a gold update before the horn to the first exact-second row."""
    player = build_player(True, [build_gold_event(-5, 600)], [-1], [])
    match = build_match(300, 0, True, [player], [0, 600])

    rows = build_exact_second_states(match)

    assert state_at(rows, MODEL_START_SECOND).radiant_nw == 0
    assert state_at(rows, 0).radiant_nw_adv == 600
    assert state_at(rows, 0).radiant_nw == 600
    assert state_at(rows, 0).dire_nw == 0


def test_prehorn_minute_skips_npm_and_checks_lead_zero() -> None:
    """Minute -60 validates leads[0] and does not index networthPerMinute."""
    player = build_player(True, [build_gold_event(-89, 600)], [-1], [])
    player["stats"]["networthPerMinute"] = [600, 999]
    match = build_match(301, 0, True, [player], [600, 600])

    rows = build_exact_second_states(match)

    assert state_at(rows, MODEL_START_SECOND).radiant_nw == 600
    assert state_at(rows, 0).radiant_nw == 600


def test_level_timeline_builds_cumulative_xp_advantage() -> None:
    """Convert level-up seconds into cumulative XP separately for each side."""
    radiant = build_player(True, [], [-1, 2, 5], [])
    dire = build_player(False, [], [-1, 3], [])
    match = build_match(400, 8, True, [radiant, dire], [0, 0])

    rows = build_exact_second_states(match)

    assert state_at(rows, 1).radiant_xp_adv == 0
    assert state_at(rows, 2).radiant_xp_adv == 240
    assert state_at(rows, 3).radiant_xp_adv == 0
    assert state_at(rows, 5).radiant_xp_adv == 400


def test_deaths_include_event_on_exact_second() -> None:
    """Count side-specific death events at the second where they occur."""
    radiant = build_player(True, [], [-1], [60])
    dire = build_player(False, [], [-1], [59])
    match = build_match(500, 60, False, [radiant, dire], [0, 0, 0])

    rows = build_exact_second_states(match)

    assert state_at(rows, 59).deaths_radiant == 0
    assert state_at(rows, 59).deaths_dire == 1
    assert state_at(rows, 60).deaths_radiant == 1
    assert state_at(rows, 60).deaths_dire == 1


def build_minute_match(nw_at_sixty: int) -> UsableStratzMatch:
    """Build a two-player match whose playback has known minute-boundary networth leads."""
    radiant = build_player(
        True,
        [build_gold_event(-1, 100), build_gold_event(60, 200)],
        [-1, 60],
        [],
    )
    dire = build_player(
        False,
        [build_gold_event(-1, 40), build_gold_event(60, 100)],
        [-1, 60],
        [],
    )
    return build_match(600, 60, True, [radiant, dire], [0, 60, nw_at_sixty])


def test_minute_boundary_matches_stratz_networth_leads() -> None:
    """Accept playback state that matches networth leads at zero and sixty."""
    rows = build_exact_second_states(build_minute_match(100))

    assert state_at(rows, 0).radiant_nw_adv == 60
    assert state_at(rows, 60).radiant_nw_adv == 100


def test_minute_mismatch_reports_match_second_expected_actual() -> None:
    """Report structured identity and values for the first minute mismatch."""
    with pytest.raises(MinuteLeadMismatchError) as raised:
        build_exact_second_states(build_minute_match(99))

    mismatch = raised.value.mismatch
    assert mismatch.match_id == 600
    assert mismatch.second == 60
    assert mismatch.expected == 99
    assert mismatch.actual == 100
    assert "match_id=600" in str(raised.value)
    assert "second=60" in str(raised.value)
    assert "expected=99" in str(raised.value)
    assert "actual=100" in str(raised.value)


def test_missing_player_playback_raises() -> None:
    """Reject a match before emitting rows when playback is absent."""
    player = cast(
        StratzPlayer,
        {
            "isRadiant": True,
            "playbackData": None,
            "level": 1,
            "stats": {"level": [-1], "deathEvents": []},
        },
    )
    match = build_match(700, 0, True, [player], [0, 0])

    with pytest.raises(ValueError, match="playbackData"):
        build_exact_second_states(match)


def test_short_lead_array_raises_with_boundary_context() -> None:
    """Reject a short lead array instead of silently skipping validation."""
    match = build_match(900, 60, True, [], [0, 0])

    with pytest.raises(ValueError, match="index=2"):
        build_exact_second_states(match)


def test_top_player_fields_follow_networth_rank_at_exact_second() -> None:
    """Rank by playback net worth; ratios follow that rank at the row second."""
    radiant_carry = build_player(True, [build_gold_event(-1, 1200)], [-1], [20, 30])
    radiant_support = build_player(True, [build_gold_event(-1, 400)], [-1], [10])
    dire_carry = build_player(False, [build_gold_event(-1, 900)], [-1], [15])
    dire_support = build_player(False, [build_gold_event(-1, 300)], [-1], [5])
    match = build_match(
        1000,
        25,
        True,
        [radiant_carry, radiant_support, dire_carry, dire_support],
        [0, 400],
    )

    rows = build_exact_second_states(match)

    row = state_at(rows, 25)
    assert row.top.top1_nw_adv == 300
    assert row.top.radiant_top1_nw_ratio == 1200 / 400
    assert row.top.dire_top1_nw_ratio == 900 / 300


def test_player_npm_mismatch_raises_with_identity() -> None:
    """Reject playback net worth that disagrees with stats.networthPerMinute at a minute."""
    radiant = build_player(True, [build_gold_event(-1, 100)], [-1], [])
    dire = build_player(False, [build_gold_event(-1, 40)], [-1], [])
    radiant["stats"]["networthPerMinute"] = [99, 99]
    match = build_match(1100, 0, True, [radiant, dire], [0, 60])

    with pytest.raises(ValueError, match="player_index=0") as raised:
        build_exact_second_states(match)

    assert "match_id=1100" in str(raised.value)
    assert "expected=99" in str(raised.value)
    assert "actual=100" in str(raised.value)


def build_npm_match(
    match_id: int, radiant_npm: list[int], dire_npm: list[int]
) -> UsableStratzMatch:
    """Build a playback-free match whose leads agree with its networthPerMinute sums."""
    radiant = build_player(True, [], [-1], [])
    dire = build_player(False, [], [-1], [])
    radiant["stats"]["networthPerMinute"] = radiant_npm
    dire["stats"]["networthPerMinute"] = dire_npm
    leads = [0] + [
        radiant_nw - dire_nw for radiant_nw, dire_nw in zip(radiant_npm, dire_npm, strict=True)
    ]
    return build_match(match_id, 60 * len(radiant_npm), True, [radiant, dire], leads)


def test_build_minute_states_uses_networth_per_minute_without_playback() -> None:
    """Minute states come from networthPerMinute; the pre-horn minute needs playback."""
    match = build_npm_match(700, [100, 300], [40, 90])
    for player in match["players"]:
        player["playbackData"] = None

    states = build_minute_states(match, 600)

    assert [state.second for state in states] == [0, 60]
    assert [state.radiant_nw for state in states] == [100, 300]
    assert [state.dire_nw for state in states] == [40, 90]
    assert [state.radiant_nw_adv for state in states] == [60, 210]


def test_build_minute_states_keeps_the_prehorn_minute_from_playback() -> None:
    """A present playback gives the pre-horn minute; the rest still come from npm."""
    match = build_npm_match(701, [100], [40])
    match["players"][0]["playbackData"] = {"playerUpdateGoldEvents": [build_gold_event(-80, 70)]}
    match["players"][1]["playbackData"] = {"playerUpdateGoldEvents": [build_gold_event(-80, 30)]}
    leads = match["radiantNetworthLeads"]
    assert leads is not None
    leads[0] = 40

    states = build_minute_states(match, 600)

    assert [state.second for state in states] == [MODEL_START_SECOND, 0]
    assert states[0].radiant_nw == 70
    assert states[0].dire_nw == 30


def test_build_minute_states_stops_where_the_lead_array_ends() -> None:
    """A match that ended early yields fewer states instead of being excluded."""
    match = build_npm_match(702, [100, 300], [40, 90])
    for player in match["players"]:
        player["playbackData"] = None

    states = build_minute_states(match, 600)

    assert [state.second for state in states] == [0, 60]


def test_build_minute_states_excludes_a_short_networth_per_minute() -> None:
    """networthPerMinute shorter than the lead array is a defect, not an early end."""
    match = build_npm_match(703, [100, 300], [40, 90])
    for player in match["players"]:
        player["playbackData"] = None
    match["players"][0]["stats"]["networthPerMinute"] = [100]

    with pytest.raises(MatchDataError) as error:
        build_minute_states(match, 600)

    assert error.value.reason == "short networthPerMinute"


def test_build_minute_states_rejects_a_lead_that_contradicts_the_sums() -> None:
    """Contradictory data still fails loudly instead of excluding the match."""
    match = build_npm_match(706, [100], [40])
    for player in match["players"]:
        player["playbackData"] = None
    leads = match["radiantNetworthLeads"]
    assert leads is not None
    leads[1] = 999

    with pytest.raises(MinuteLeadMismatchError, match="second=0"):
        build_minute_states(match, 600)


def test_both_builders_emit_the_same_features_at_one_minute() -> None:
    """The minute path and the exact-second path agree field by field on a boundary."""
    radiant = build_player(True, [build_gold_event(0, 100)], [-1, 30], [10])
    dire = build_player(False, [build_gold_event(0, 40)], [-1], [20])
    match = build_match(707, 60, True, [radiant, dire], [0, 60, 60])

    minute_state = build_minute_states(match, 600)[1]
    exact_state = state_at(build_exact_second_states(match), 0)

    assert minute_state == exact_state


def test_build_minute_states_walks_minutes_past_the_train_window() -> None:
    """Passing duration+1 emits minute boundaries after second 540."""
    radiant_npm = [100 + 20 * minute for minute in range(15)]
    dire_npm = [40 + 10 * minute for minute in range(15)]
    match = build_npm_match(708, radiant_npm, dire_npm)
    for player in match["players"]:
        player["playbackData"] = None

    states = build_minute_states(match, match["durationSeconds"] + 1)

    seconds = [state.second for state in states]
    assert seconds[0] == 0
    assert seconds[-1] > 540
    assert 540 in seconds
    assert 600 in seconds
