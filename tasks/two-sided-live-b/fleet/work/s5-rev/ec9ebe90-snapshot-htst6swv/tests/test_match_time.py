"""Unit tests for shared match timing: horn, pauses, and game-clock conversion."""

from datetime import UTC, datetime, timedelta

import pytest

from shared.types.opendota import OpenDotaPause
from shared.utils.match_time import (
    HORN_OFFSET_SECONDS,
    calculate_game_second,
    format_clock,
    get_horn_datetime,
    get_paused_seconds_before,
    get_spawn_unix_from_horn,
    get_state_available_ts,
)


def test_format_clock_matches_dota_timer() -> None:
    """Show signed, normal, and hour-long game times as Dota clocks."""
    assert format_clock(-90) == "-1:30"
    assert format_clock(755) == "12:35"
    assert format_clock(3600) == "60:00"


def test_horn_includes_ninety_seconds_and_prehorn_pauses() -> None:
    """Horn is GRID startedAt plus 90s plus pauses that started before game time 0."""
    started = datetime(2026, 4, 19, 10, 22, 53, 599000, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [{"time": -20, "duration": 5}, {"time": 100, "duration": 30}]

    assert get_paused_seconds_before(pauses, 0) == 5
    assert get_horn_datetime(started, pauses) == datetime(
        2026, 4, 19, 10, 24, 28, 599000, tzinfo=UTC
    )


def test_pauses_before_spawn_do_not_shift_horn_or_end() -> None:
    """OpenDota pauses with time before -90 are draft/load; GRID duration does not include them."""
    spawn = datetime(2026, 4, 19, 10, 22, 53, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [
        {"time": -882, "duration": 68},
        {"time": -20, "duration": 5},
        {"time": 60, "duration": 30},
    ]

    assert get_paused_seconds_before(pauses, 0) == 5
    horn = get_horn_datetime(spawn, pauses)
    assert horn == spawn + timedelta(seconds=90 + 5)
    assert get_state_available_ts(horn=horn, second=100, pauses=pauses) == spawn + timedelta(
        seconds=90 + 5 + 100 + 30
    )


def test_spawn_unix_from_horn_inverts_get_horn_datetime() -> None:
    """horn - 90s - pauses in [-90, 0); earlier pauses ignored, empty pauses give -90."""
    horn_unix = 1_786_800_000
    pauses: list[OpenDotaPause] = [
        {"time": -40, "duration": 120},
        {"time": -200, "duration": 45},
    ]
    assert get_spawn_unix_from_horn(horn_unix, pauses) == horn_unix - HORN_OFFSET_SECONDS - 120
    assert get_spawn_unix_from_horn(horn_unix, []) == horn_unix - HORN_OFFSET_SECONDS

    spawn = datetime(2026, 4, 19, 10, 22, 53, tzinfo=UTC)
    horn = get_horn_datetime(spawn, pauses)
    assert get_spawn_unix_from_horn(int(horn.timestamp()), pauses) == int(spawn.timestamp())


def test_game_ended_at_is_last_stratz_second_on_the_wall() -> None:
    """GG wall time is spawn plus 90s plus STRATZ duration plus pauses before that second."""
    spawn = datetime(2026, 4, 19, 10, 22, 53, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [
        {"time": -10, "duration": 5},
        {"time": 60, "duration": 30},
    ]
    horn = get_horn_datetime(spawn, pauses)

    assert get_state_available_ts(horn=horn, second=100, pauses=pauses) == spawn + timedelta(
        seconds=90 + 5 + 100 + 30
    )
    pauseless_horn = get_horn_datetime(spawn, [])
    assert get_state_available_ts(horn=pauseless_horn, second=100, pauses=[]) == spawn + timedelta(
        seconds=90 + 100
    )


def test_started_pauses_shift_available_ts_without_waiting_for_them_to_finish() -> None:
    """A pause shifts every later game second by its full wall-clock duration."""
    horn = datetime(2026, 4, 19, 10, 24, 23, 599000, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [
        {"time": -10, "duration": 4},
        {"time": 60, "duration": 30},
        {"time": 180, "duration": 20},
    ]

    assert get_paused_seconds_before(pauses, 60) == 4
    assert get_paused_seconds_before(pauses, 90) == 34

    # second 75 falls inside the 30s pause that began at 60: it is still delayed by 30s,
    # even though 60 + 30 has not elapsed on the game clock
    assert get_state_available_ts(horn=horn, second=75, pauses=pauses) == horn + timedelta(
        seconds=105
    )
    assert get_state_available_ts(horn=horn, second=90, pauses=pauses) == datetime(
        2026, 4, 19, 10, 26, 23, 599000, tzinfo=UTC
    )
    # pre-horn pauses are already inside `horn`, so they must not be added twice
    assert get_state_available_ts(horn=horn, second=0, pauses=pauses) == horn


def test_state_available_ts_accepts_prehorn_seconds() -> None:
    """Second -60 is horn minus 60s minus pre-horn pauses that start after it."""
    horn = datetime(2026, 4, 19, 10, 24, 23, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [{"time": -10, "duration": 4}]

    assert get_state_available_ts(horn=horn, second=-60, pauses=pauses) == horn - timedelta(
        seconds=64
    )
    assert get_state_available_ts(horn=horn, second=-60, pauses=[]) == horn - timedelta(seconds=60)
    with pytest.raises(ValueError):
        get_state_available_ts(horn=horn, second=-61, pauses=[])


def test_game_second_reads_back_through_pauses() -> None:
    """Wall-clock entry times map back onto the frozen game clock."""
    horn = datetime(2026, 4, 19, 10, 22, 53, tzinfo=UTC)
    pauses: list[OpenDotaPause] = [{"time": 300, "duration": 30}]

    def game_second(wall_seconds: int) -> int:
        """Game second at `wall_seconds` after the horn."""
        return calculate_game_second(
            horn=horn, at=horn + timedelta(seconds=wall_seconds), pauses=pauses
        )

    assert game_second(120) == 120
    # inside the 30s pause that began at second 300 the clock stays at 300
    assert game_second(310) == 300
    assert game_second(330) == 330 - 30
    # a pre-horn pause is already folded into the horn and must not shift again
    assert (
        calculate_game_second(
            horn=horn, at=horn + timedelta(seconds=60), pauses=[{"time": -20, "duration": 5}]
        )
        == 60
    )
