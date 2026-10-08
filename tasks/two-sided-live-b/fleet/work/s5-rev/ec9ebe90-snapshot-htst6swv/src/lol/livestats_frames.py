"""Dedup, clock, pause, invariants, and spawn-clocked grid for LoL livestats frames."""

import gzip
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from lol.constants import (
    LOL_CASHBACK_UNDO_MAX_GOLD,
    LOL_FRAME_MAX_AGE_SECONDS,
    LOL_GRID_START_SECOND,
    LOL_MAX_SKIPPED_FRAME_FRACTION,
    LOL_PAUSE_MIN_GAP_SECONDS,
    LOL_SPAWN_GOLD,
    LOL_SPAWN_SEARCH_SECONDS,
    REASON_ABORTED_FEED,
    REASON_LIVESTATS_INVARIANT_VIOLATION,
    REASON_NEGATIVE_NET_WORTH,
    REASON_NO_DETAILS,
    REASON_NO_LIVESTATS,
    REASON_NO_PATCH_VERSION,
    REASON_NO_SPAWN_FRAME,
    REASON_WINDOW_DETAILS_MISMATCH,
    REASON_ZERO_USABLE_FRAMES,
)
from lol.networth import (
    ConsumedFrame,
    ConsumedTimeline,
    ItemCatalog,
    build_consumed_timeline,
    table_for_game_patch,
)
from lol.types import LolLinkRow
from shared.constants.lol import LOL_LEVEL_XP
from shared.utils.level_xp import xp_advantage
from shared.utils.top_players import TopPlayerFeatures, build_top_player_features_over_total


class FetchStage(Protocol):
    """Stage 04 helpers used to read raw livestats archives."""

    def archive_path(self, windows_dir: Path, esports_game_id: str) -> Path:
        """Gzip JSONL path for one map's raw window archive."""
        ...

    def read_gzip_jsonl(self, path: Path) -> list[object]:
        """Read gzip JSONL into parsed window objects."""
        ...

    def parse_rfc460_seconds(self, stamp: str) -> float | None:
        """Parse rfc460Timestamp to float unix seconds; None if invalid."""
        ...


FETCH = cast(FetchStage, cast(object, import_module("lol.04_fetch_lolesports")))


@dataclass(frozen=True)
class FrameProgressKey:
    """Gold, kills, and HP summed across both sides for pause freeze checks."""

    gold: int
    kills: int
    hp: int


def _clock_int(value: object) -> int | None:
    """Parse a JSON number as an int; reject bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    as_int = int(value)
    if float(value) != float(as_int):
        return None
    return as_int


def frame_progress_key(frame: Mapping[str, object]) -> FrameProgressKey | None:
    """Sum participant gold, team kills, and participant HP; None when gold is unreadable."""
    gold = 0
    kills = 0
    hp = 0
    saw_gold = False
    for side in ("blueTeam", "redTeam"):
        team = frame.get(side)
        if not isinstance(team, dict):
            continue
        body = cast(dict[str, object], team)
        team_kills = _clock_int(body.get("totalKills"))
        if team_kills is not None:
            kills += team_kills
        participants = body.get("participants")
        if not isinstance(participants, list):
            continue
        for raw in cast(list[object], participants):
            if not isinstance(raw, dict):
                continue
            player = cast(dict[str, object], raw)
            player_gold = _clock_int(player.get("totalGold"))
            if player_gold is not None:
                gold += player_gold
                saw_gold = True
            player_hp = _clock_int(player.get("currentHealth"))
            if player_hp is not None:
                hp += player_hp
    if not saw_gold:
        return None
    return FrameProgressKey(gold, kills, hp)


def is_pause_gap(
    gap: float,
    previous_key: FrameProgressKey | None,
    current_key: FrameProgressKey | None,
) -> bool:
    """True when a wall gap is a pause: >5s and gold/kills/HP did not change."""
    if gap <= LOL_PAUSE_MIN_GAP_SECONDS:
        return False
    if previous_key is None or current_key is None:
        return True
    return previous_key == current_key


@dataclass(frozen=True)
class StampedFrame:
    """One unique livestats frame keyed by rfc460Timestamp."""

    stamp: str
    wall_seconds: float
    payload: dict[str, object]


@dataclass(frozen=True)
class ParsedPlayer:
    """One participant after required gold, level, deaths, and kills are validated."""

    participant_id: int
    gold: int
    level: int
    deaths: int
    kills: int


@dataclass(frozen=True)
class ParsedTeam:
    """One blueTeam/redTeam object after required fields are validated."""

    total_gold: int
    total_kills: int
    players: tuple[ParsedPlayer, ...]


@dataclass(frozen=True)
class ParsedSides:
    """Both sides of one post-spawn frame."""

    blue: ParsedTeam
    red: ParsedTeam


@dataclass(frozen=True)
class ClockFrame:
    """Post-spawn frame with pause-aware game time, before invariant parse."""

    wall_seconds: float
    game_time: float
    payload: dict[str, object]


@dataclass(frozen=True)
class FrameFeatures:
    """Game features taken from one selected livestats frame."""

    radiant_nw: int
    dire_nw: int
    radiant_nw_adv: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: TopPlayerFeatures


@dataclass(frozen=True)
class TimedFrame:
    """Post-spawn frame after invariants: parsed sides and features attached."""

    wall_seconds: float
    game_time: float
    payload: dict[str, object]
    sides: ParsedSides
    features: FrameFeatures


@dataclass(frozen=True)
class GridRow:
    """One integer-second slot that passed the age gate."""

    second: int
    state_wall_us: int
    features: FrameFeatures


@dataclass(frozen=True)
class LivestatsDrop:
    """Map excluded before market join."""

    reason: str
    invariant_rule: int | None
    start_time: int | None
    pause_count: int
    pause_seconds: float
    frame_count: int
    skipped_invariant_rows: int
    skipped_stamp_rows: int


@dataclass(frozen=True)
class PauseGap:
    """One post-spawn wall gap counted as a pause, keyed by game_time at gap start."""

    start_game_time: float
    duration: float


@dataclass(frozen=True)
class LivestatsOk:
    """Spawn-clocked map with age-gated grid rows."""

    match_id: int
    start_time: int
    spawn_wall_seconds: float
    spawn_us: int
    pause_count: int
    pause_seconds: float
    frame_count: int
    grid_rows: tuple[GridRow, ...]
    skipped_age_rows: int
    skipped_invariant_rows: int
    skipped_stamp_rows: int
    last_game_time: float
    last_frame_wall_seconds: float
    pauses: tuple[PauseGap, ...]


@dataclass(frozen=True)
class PauseClock:
    """Pause-aware times for every post-spawn frame."""

    timed: tuple[ClockFrame, ...]
    pause_count: int
    pause_seconds: float
    pauses: tuple[PauseGap, ...]


@dataclass(frozen=True)
class GridSelection:
    """Age-gated grid rows plus the count of seconds dropped for age."""

    rows: tuple[GridRow, ...]
    skipped_age_rows: int


@dataclass(frozen=True)
class ValidatedFrames:
    """Post-spawn frames that passed parse, invariants, and details lookup."""

    frames: tuple[TimedFrame, ...]
    skipped_invariant_rows: int
    skipped_stamp_rows: int
    last_rule: int | None


def as_whole_int(value: object) -> int | None:
    """Parse a JSON number as an int; reject bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    as_int = int(value)
    if float(value) != float(as_int):
        return None
    return as_int


def parse_player(raw: object, expected: frozenset[int], seen: set[int]) -> ParsedPlayer | int:
    """Parse one participant, or the invariant rule that failed."""
    if not isinstance(raw, dict):
        return 7
    body = cast(dict[str, object], raw)
    participant_id = body.get("participantId")
    if isinstance(participant_id, bool) or not isinstance(participant_id, int):
        return 7
    if participant_id not in expected or participant_id in seen:
        return 7
    gold = as_whole_int(body.get("totalGold"))
    level = as_whole_int(body.get("level"))
    deaths = as_whole_int(body.get("deaths"))
    kills = as_whole_int(body.get("kills"))
    if gold is None:
        return 2
    if level is None:
        return 5
    if deaths is None:
        return 6
    if kills is None:
        return 7
    return ParsedPlayer(participant_id, gold, level, deaths, kills)


def parse_team(side: str, raw: object) -> ParsedTeam | int:
    """Parse one side, or the invariant rule that failed."""
    if not isinstance(raw, dict):
        return 7
    team = cast(dict[str, object], raw)
    participants_raw = team.get("participants")
    if not isinstance(participants_raw, list):
        return 7
    items = cast(list[object], participants_raw)
    if len(items) != 5:
        return 7
    expected = frozenset(range(1, 6)) if side == "blue" else frozenset(range(6, 11))
    seen: set[int] = set()
    players: list[ParsedPlayer] = []
    for item in items:
        player = parse_player(item, expected, seen)
        if isinstance(player, int):
            return player
        seen.add(player.participant_id)
        players.append(player)
    total_gold = as_whole_int(team.get("totalGold"))
    if total_gold is None:
        return 3
    total_kills = as_whole_int(team.get("totalKills"))
    if total_kills is None:
        return 4
    return ParsedTeam(total_gold, total_kills, tuple(players))


def parse_sides(payload: Mapping[str, object]) -> ParsedSides | int:
    """Parse blue and red, or the first invariant rule that failed."""
    blue = parse_team("blue", payload.get("blueTeam"))
    if isinstance(blue, int):
        return blue
    red = parse_team("red", payload.get("redTeam"))
    if isinstance(red, int):
        return red
    return ParsedSides(blue, red)


def is_spawn_sides(sides: ParsedSides) -> bool:
    """True when all ten players are 500 gold, level 1, deaths 0."""
    for player in (*sides.blue.players, *sides.red.players):
        if player.gold != LOL_SPAWN_GOLD or player.level != 1 or player.deaths != 0:
            return False
    return True


def find_spawn_index(frames: Sequence[StampedFrame], loading_anchor_ts: int) -> int | None:
    """Index of the first spawn-shaped frame in the loading-anchor window, or None."""
    earliest = float(loading_anchor_ts)
    latest = earliest + LOL_SPAWN_SEARCH_SECONDS
    for index, item in enumerate(frames):
        if item.wall_seconds < earliest:
            continue
        if item.wall_seconds > latest:
            return None
        parsed = parse_sides(item.payload)
        if isinstance(parsed, int):
            continue
        if is_spawn_sides(parsed):
            return index
    return None


def players_by_key(sides: ParsedSides) -> dict[int, ParsedPlayer]:
    """Index both sides' players by participant id."""
    return {player.participant_id: player for player in (*sides.blue.players, *sides.red.players)}


def stats_non_decreasing(previous: ParsedSides, current: ParsedSides) -> int | None:
    """Return rule 2/5/6; one-player Cash Back undo (≤ refund cap) is not rule 2."""
    prev_map = players_by_key(previous)
    cur_map = players_by_key(current)
    if prev_map.keys() != cur_map.keys():
        return 2
    gold_drops: list[int] = []
    for key, prev in prev_map.items():
        cur = cur_map[key]
        if cur.gold < prev.gold:
            gold_drops.append(prev.gold - cur.gold)
        if cur.level < prev.level:
            return 5
        if cur.deaths < prev.deaths:
            return 6
    if len(gold_drops) == 1 and gold_drops[0] <= LOL_CASHBACK_UNDO_MAX_GOLD:
        return None
    if gold_drops:
        return 2
    return None


def check_player_bounds(parsed: ParsedSides) -> int | None:
    """Return rule 5/6 when a player level or death count is out of range."""
    for player in (*parsed.blue.players, *parsed.red.players):
        if player.level < 1 or player.level > len(LOL_LEVEL_XP):
            return 5
        if player.deaths < 0:
            return 6
    return None


def invariant_rule(parsed: ParsedSides, previous: ParsedSides | None) -> int | None:
    """Run the seven livestats invariants on one already-parsed post-spawn frame."""
    if previous is None:
        for player in (*parsed.blue.players, *parsed.red.players):
            if player.gold != LOL_SPAWN_GOLD:
                return 1
    if parsed.blue.total_gold != sum(player.gold for player in parsed.blue.players):
        return 3
    if parsed.red.total_gold != sum(player.gold for player in parsed.red.players):
        return 3
    if parsed.blue.total_kills != sum(player.deaths for player in parsed.red.players):
        return 4
    if parsed.red.total_kills != sum(player.deaths for player in parsed.blue.players):
        return 4
    bound = check_player_bounds(parsed)
    if bound is not None:
        return bound
    if previous is not None:
        return stats_non_decreasing(previous, parsed)
    return None


def features_from_sides(sides: ParsedSides, consumed: ConsumedFrame) -> FrameFeatures | None:
    """Compute the Dota-shaped game features from corrected per-player net worth.

    Top-player fields are the shared over-total features GRID reduces live.
    A negative corrected side is replay poison, never clamped.
    """
    blue_net_worth = [
        player.gold - consumed.by_participant[player.participant_id]
        for player in sides.blue.players
    ]
    red_net_worth = [
        player.gold - consumed.by_participant[player.participant_id] for player in sides.red.players
    ]
    if any(value < 0 for value in (*blue_net_worth, *red_net_worth)):
        return None
    radiant_nw = sum(blue_net_worth)
    dire_nw = sum(red_net_worth)
    return FrameFeatures(
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_nw_adv=radiant_nw - dire_nw,
        radiant_xp_adv=xp_advantage(
            LOL_LEVEL_XP,
            [player.level for player in sides.blue.players],
            [player.level for player in sides.red.players],
        ),
        deaths_radiant=sum(player.deaths for player in sides.blue.players),
        deaths_dire=sum(player.deaths for player in sides.red.players),
        top=build_top_player_features_over_total(blue_net_worth, red_net_worth),
    )


def dedup_sort_frames(payloads: Sequence[object]) -> list[StampedFrame]:
    """Keep the last payload per rfc460Timestamp and sort by wall time."""
    by_stamp: dict[str, StampedFrame] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        raw_frames = cast(dict[str, object], payload).get("frames")
        if not isinstance(raw_frames, list):
            continue
        for raw in cast(list[object], raw_frames):
            if not isinstance(raw, dict):
                continue
            frame = cast(dict[str, object], raw)
            stamp = frame.get("rfc460Timestamp")
            if not isinstance(stamp, str) or not stamp:
                continue
            wall = FETCH.parse_rfc460_seconds(stamp)
            if wall is None:
                continue
            by_stamp[stamp] = StampedFrame(stamp, wall, frame)
    return sorted(by_stamp.values(), key=lambda item: item.wall_seconds)


def read_archive_payloads(windows_dir: Path, esports_game_id: str) -> list[object] | None:
    """Read one gzip JSONL archive; None when missing or unreadable."""
    path = FETCH.archive_path(windows_dir, esports_game_id)
    if not path.is_file():
        return None
    try:
        return FETCH.read_gzip_jsonl(path)
    except (
        OSError,
        EOFError,
        gzip.BadGzipFile,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None


def read_archive_frames(windows_dir: Path, esports_game_id: str) -> list[StampedFrame] | None:
    """Load and dedup one map archive; None when missing or unreadable."""
    payloads = read_archive_payloads(windows_dir, esports_game_id)
    if payloads is None:
        return None
    return dedup_sort_frames(payloads)


def game_patch_from_payloads(payloads: Sequence[object]) -> str | None:
    """Single non-empty gameMetadata.patchVersion, or None if missing or conflicting."""
    if not payloads:
        return None
    seen: str | None = None
    for payload in payloads:
        if not isinstance(payload, dict):
            return None
        metadata = cast(dict[str, object], payload).get("gameMetadata")
        if not isinstance(metadata, dict):
            return None
        patch = cast(dict[str, object], metadata).get("patchVersion")
        if not isinstance(patch, str) or not patch:
            return None
        if seen is None:
            seen = patch
        elif patch != seen:
            return None
    return seen


def assign_game_times(post_spawn: Sequence[StampedFrame]) -> PauseClock:
    """Subtract post-spawn pauses; spawn frame is game_time 0."""
    spawn_wall = post_spawn[0].wall_seconds
    timed = [ClockFrame(wall_seconds=spawn_wall, game_time=0.0, payload=post_spawn[0].payload)]
    paused = 0.0
    pause_count = 0
    pause_seconds = 0.0
    previous = post_spawn[0]
    previous_game_time = 0.0
    pauses: list[PauseGap] = []
    # ponytail: 1 Hz frames with frozen stats never make gap>5s, so a live pause that keeps ticking the feed is invisible; detect streak freeze if archives show that shape
    for item in post_spawn[1:]:
        gap = item.wall_seconds - previous.wall_seconds
        if is_pause_gap(
            gap,
            frame_progress_key(previous.payload),
            frame_progress_key(item.payload),
        ):
            paused += gap
            pause_count += 1
            pause_seconds += gap
            pauses.append(PauseGap(start_game_time=previous_game_time, duration=gap))
        previous = item
        game_time = item.wall_seconds - spawn_wall - paused
        timed.append(ClockFrame(item.wall_seconds, game_time, item.payload))
        previous_game_time = game_time
    return PauseClock(tuple(timed), pause_count, pause_seconds, tuple(pauses))


def wall_us_for_second(spawn_wall_seconds: float, pauses: Sequence[PauseGap], second: int) -> int:
    """Pause-adjusted wall us for the theoretical integer-second boundary S."""
    paused = 0.0
    for gap in pauses:
        if gap.start_game_time <= second:
            paused += gap.duration
    return round((spawn_wall_seconds + second + paused) * 1_000_000)


def validate_timed_frames(
    timed: Sequence[ClockFrame], consumed: ConsumedTimeline, end_second: int
) -> ValidatedFrames | str:
    """Parse each frame once, run invariants, attach features.

    Unparseable or invariant-failing frames are skipped so a local glitch does
    not drop the map. A window stamp missing from details is skipped the same
    way. Frames after `end_second` are ignored. Returns a drop reason string
    for negative net worth (map-wide reconstruction poison).
    """
    previous: ParsedSides | None = None
    validated: list[TimedFrame] = []
    last_rule: int | None = None
    skipped_invariant = 0
    skipped_stamp = 0
    for frame in timed:
        if frame.game_time > end_second:
            break
        parsed = parse_sides(frame.payload)
        if isinstance(parsed, int):
            last_rule = parsed
            skipped_invariant += 1
            continue
        rule = invariant_rule(parsed, previous)
        if rule is not None:
            last_rule = rule
            skipped_invariant += 1
            continue
        stamp = frame.payload.get("rfc460Timestamp")
        if not isinstance(stamp, str) or stamp not in consumed.by_stamp:
            skipped_stamp += 1
            previous = parsed
            continue
        features = features_from_sides(parsed, consumed.by_stamp[stamp])
        if features is None:
            return REASON_NEGATIVE_NET_WORTH
        validated.append(
            TimedFrame(
                wall_seconds=frame.wall_seconds,
                game_time=frame.game_time,
                payload=frame.payload,
                sides=parsed,
                features=features,
            )
        )
        previous = parsed
    return ValidatedFrames(tuple(validated), skipped_invariant, skipped_stamp, last_rule)


def skipped_frame_fraction(result: ValidatedFrames) -> float:
    """Share of considered frames skipped for an invariant or missing stamp."""
    considered = len(result.frames) + result.skipped_invariant_rows + result.skipped_stamp_rows
    if considered == 0:
        return 0.0
    return (result.skipped_invariant_rows + result.skipped_stamp_rows) / considered


def select_grid_rows(timed: Sequence[TimedFrame], end_second: int) -> GridSelection:
    """Latest frame with game_time <= S; drop the second when age > 2s."""
    skipped = 0
    rows: list[GridRow] = []
    index = -1
    for second in range(LOL_GRID_START_SECOND, end_second + 1):
        while index + 1 < len(timed) and timed[index + 1].game_time <= second:
            index += 1
        if index < 0:
            skipped += 1
            continue
        chosen = timed[index]
        age = second - chosen.game_time
        if age > LOL_FRAME_MAX_AGE_SECONDS:
            skipped += 1
            continue
        rows.append(
            GridRow(
                second=second,
                state_wall_us=round(chosen.wall_seconds * 1_000_000),
                features=chosen.features,
            )
        )
    return GridSelection(tuple(rows), skipped)


def empty_drop(reason: str, frame_count: int) -> LivestatsDrop:
    """Map drop with no spawn clock."""
    return LivestatsDrop(
        reason=reason,
        invariant_rule=None,
        start_time=None,
        pause_count=0,
        pause_seconds=0.0,
        frame_count=frame_count,
        skipped_invariant_rows=0,
        skipped_stamp_rows=0,
    )


def clocked_drop(
    reason: str,
    start_time: int,
    clock: PauseClock,
    frame_count: int,
    validated: ValidatedFrames,
    invariant_rule: int | None,
) -> LivestatsDrop:
    """Map drop after spawn, carrying skip counts from validation."""
    return LivestatsDrop(
        reason=reason,
        invariant_rule=invariant_rule,
        start_time=start_time,
        pause_count=clock.pause_count,
        pause_seconds=clock.pause_seconds,
        frame_count=frame_count,
        skipped_invariant_rows=validated.skipped_invariant_rows,
        skipped_stamp_rows=validated.skipped_stamp_rows,
    )


def drop_unusable_map(
    start_time: int,
    clock: PauseClock,
    frame_count: int,
    validated: ValidatedFrames,
) -> LivestatsDrop | None:
    """Drop when frames are unusable, aborted, or too often skipped; None to keep the map."""
    if not validated.frames:
        if validated.last_rule is None:
            return clocked_drop(
                REASON_ZERO_USABLE_FRAMES, start_time, clock, frame_count, validated, None
            )
        return clocked_drop(
            REASON_LIVESTATS_INVARIANT_VIOLATION,
            start_time,
            clock,
            frame_count,
            validated,
            validated.last_rule,
        )
    if is_spawn_sides(validated.frames[-1].sides):
        return clocked_drop(REASON_ABORTED_FEED, start_time, clock, frame_count, validated, None)
    if skipped_frame_fraction(validated) > LOL_MAX_SKIPPED_FRAME_FRACTION:
        if validated.last_rule is None:
            return clocked_drop(
                REASON_WINDOW_DETAILS_MISMATCH, start_time, clock, frame_count, validated, None
            )
        return clocked_drop(
            REASON_LIVESTATS_INVARIANT_VIOLATION,
            start_time,
            clock,
            frame_count,
            validated,
            validated.last_rule,
        )
    return None


def prepare_map_livestats_until(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    catalog: ItemCatalog,
    end_second: int,
) -> LivestatsOk | LivestatsDrop:
    """Turn one map's raw archive into a spawn-clocked grid through end_second."""
    game_id = str(link["esports_game_id"])
    payloads = read_archive_payloads(windows_dir, game_id)
    if payloads is None or not payloads:
        return empty_drop(REASON_NO_LIVESTATS, 0)
    game_patch = game_patch_from_payloads(payloads)
    if game_patch is None:
        return empty_drop(REASON_NO_PATCH_VERSION, 0)
    item_table = table_for_game_patch(catalog, game_patch)
    frames = dedup_sort_frames(payloads)
    if not frames:
        return empty_drop(REASON_NO_LIVESTATS, 0)
    details_path = FETCH.archive_path(details_dir, game_id)
    if not details_path.is_file():
        return empty_drop(REASON_NO_DETAILS, len(frames))
    try:
        details_payloads = FETCH.read_gzip_jsonl(details_path)
    except (
        OSError,
        EOFError,
        gzip.BadGzipFile,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise RuntimeError(f"unreadable details archive for {game_id}") from exc
    details_frames = dedup_sort_frames(details_payloads)
    consumed = build_consumed_timeline([frame.payload for frame in details_frames], item_table)
    spawn_index = find_spawn_index(frames, int(link["loading_anchor_ts"]))
    if spawn_index is None:
        return empty_drop(REASON_NO_SPAWN_FRAME, len(frames))
    spawn = frames[spawn_index]
    clock = assign_game_times(frames[spawn_index:])
    start_time = math.floor(round(spawn.wall_seconds * 1000) / 1000)
    validated = validate_timed_frames(clock.timed, consumed, end_second)
    if isinstance(validated, str):
        return LivestatsDrop(
            reason=validated,
            invariant_rule=None,
            start_time=start_time,
            pause_count=clock.pause_count,
            pause_seconds=clock.pause_seconds,
            frame_count=len(frames),
            skipped_invariant_rows=0,
            skipped_stamp_rows=0,
        )
    dropped = drop_unusable_map(start_time, clock, len(frames), validated)
    if dropped is not None:
        return dropped
    grid = select_grid_rows(validated.frames, end_second)
    last = validated.frames[-1]
    return LivestatsOk(
        match_id=int(str(link["esports_game_id"])),
        start_time=start_time,
        spawn_wall_seconds=spawn.wall_seconds,
        spawn_us=round(spawn.wall_seconds * 1_000_000),
        pause_count=clock.pause_count,
        pause_seconds=clock.pause_seconds,
        frame_count=len(frames),
        grid_rows=grid.rows,
        skipped_age_rows=grid.skipped_age_rows,
        skipped_invariant_rows=validated.skipped_invariant_rows,
        skipped_stamp_rows=validated.skipped_stamp_rows,
        # ponytail: last_game_time from livestats frames, not an official end event; feed death before 7200 looks like early end until windows include a real end marker
        last_game_time=last.game_time,
        last_frame_wall_seconds=last.wall_seconds,
        pauses=clock.pauses,
    )
