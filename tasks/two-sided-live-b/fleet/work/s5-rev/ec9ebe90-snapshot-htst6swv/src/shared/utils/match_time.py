"""Match timing anchors: horn, pauses, and game-clock <-> wall-clock conversion."""

from datetime import UTC, datetime, timedelta

from shared.constants.dataset import MODEL_START_SECOND
from shared.types.opendota import OpenDotaPause

NS_PER_SECOND = 1_000_000_000
HORN_OFFSET_SECONDS = 90
HORN_OFFSET = timedelta(seconds=HORN_OFFSET_SECONDS)


def format_clock(seconds: float) -> str:
    """Format signed seconds as the Dota match clock `m:ss`."""
    total = round(abs(seconds))
    sign = "-" if seconds < 0 else ""
    return f"{sign}{total // 60}:{total % 60:02d}"


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp into an aware datetime."""
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def datetime_to_ns(value: datetime) -> int:
    """Convert an aware datetime to UTC nanoseconds since epoch."""
    return int(value.astimezone(UTC).timestamp() * NS_PER_SECOND)


def get_paused_seconds_before(pauses: list[OpenDotaPause], second: int) -> int:
    """Wall-clock seconds lost to pauses that began before the given game second.

    `pause["time"]` is a game-clock second and `pause["duration"]` is wall-clock
    seconds: the game clock is frozen while paused, so the two are never summed.
    A pause counts once it has started, because every later game second is
    already shifted by its full duration.

    Pauses with `time < -HORN_OFFSET_SECONDS` are before GRID spawn and are ignored:
    they never happen in the wall-clock window that starts at `startedAt`.
    """
    return sum(
        pause["duration"]
        for pause in pauses
        if pause["time"] >= -HORN_OFFSET_SECONDS and pause["time"] < second
    )


def get_horn_datetime(grid_started_at: datetime, pauses: list[OpenDotaPause]) -> datetime:
    """Compute horn UTC from GRID startedAt, the 90s pre-game, and pauses in [-90, 0)."""
    pre_horn_pause_seconds = get_paused_seconds_before(pauses, 0)
    return grid_started_at + HORN_OFFSET + timedelta(seconds=pre_horn_pause_seconds)


def get_spawn_unix_from_horn(horn_unix: int, pauses: list[OpenDotaPause]) -> int:
    """The game-second -90 wall instant from an archived horn; inverse of `get_horn_datetime`."""
    return horn_unix - HORN_OFFSET_SECONDS - get_paused_seconds_before(pauses, 0)


def get_state_available_ts(
    *,
    horn: datetime,
    second: int,
    pauses: list[OpenDotaPause],
) -> datetime:
    """UTC time when a game-second state becomes available (zero lag).

    Raises ValueError when second is before MODEL_START_SECOND.
    """
    if second < MODEL_START_SECOND:
        raise ValueError(f"second {second} is before MODEL_START_SECOND ({MODEL_START_SECOND})")
    # pre-horn pauses are already folded into `horn`, so count only what came after
    pre_horn = get_paused_seconds_before(pauses, 0)
    post_horn = get_paused_seconds_before(pauses, second) - pre_horn
    return horn + timedelta(seconds=second + post_horn)


def calculate_game_second(*, horn: datetime, at: datetime, pauses: list[OpenDotaPause]) -> int:
    """Game-clock second showing at a wall-clock instant — inverse of `get_state_available_ts`.

    The game clock is frozen for the whole of a pause, so an instant inside one
    reads back as the second the pause started on.
    """
    wall_seconds = int((at - horn).total_seconds())
    # pre-horn pauses are already folded into `horn`
    post_horn_pauses = sorted(
        (pause for pause in pauses if pause["time"] >= 0), key=lambda pause: pause["time"]
    )
    shift = 0
    for pause in post_horn_pauses:
        pause_started_at = pause["time"] + shift
        if wall_seconds < pause_started_at:
            break
        if wall_seconds < pause_started_at + pause["duration"]:
            return pause["time"]
        shift += pause["duration"]
    return wall_seconds - shift
