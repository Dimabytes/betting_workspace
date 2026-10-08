"""Check the fixed level XP table against cached STRATZ matches."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import mean, median
from typing import cast

from shared.constants.dota import LEVEL_XP
from shared.constants.paths import RAW_STRATZ_MATCHES_DIR
from shared.types.stratz import (
    StratzMatch,
    StratzPlayerPlayback,
)
from shared.utils.dota_levels import radiant_xp_advantage_at_second
from shared.utils.json_io import read_gzip_json


@dataclass
class ExactCheckStats:
    """Level checks and mismatches for one calendar month."""

    matches: int = 0
    checks: int = 0
    mismatches: int = 0


@dataclass
class ResidualStats:
    """Level-based XP residuals against STRATZ minute leads for one month."""

    residuals: list[int] = field(default_factory=list)


def match_month(match: StratzMatch) -> str:
    """Return the UTC calendar month of one match."""
    return datetime.fromtimestamp(match["startDateTime"], tz=UTC).strftime("%Y-%m")


def has_v3_level_timelines(player: object) -> bool:
    """Return whether one cache player has a v3 stats.level list."""
    if not isinstance(player, dict):
        return False
    typed_player = cast(dict[str, object], player)
    stats = typed_player.get("stats")
    if not isinstance(stats, dict):
        return False
    typed_stats = cast(dict[str, object], stats)
    return isinstance(typed_stats.get("level"), list)


def load_matches() -> list[StratzMatch]:
    """Load cached STRATZ matches that have a v3 level timeline for every player."""
    matches: list[StratzMatch] = []
    for path in sorted(RAW_STRATZ_MATCHES_DIR.glob("match_*.json.gz")):
        payload = read_gzip_json(path)
        match = payload["data"]["match"]
        players = cast(list[object], match["players"])
        if not all(has_v3_level_timelines(player) for player in players):
            continue
        matches.append(cast(StratzMatch, match))
    return matches


def check_playback_levels(match: StratzMatch, stats: ExactCheckStats) -> None:
    """Compare cumulative playback XP with the fixed table within one level."""
    if match["playbackData"] is None:
        return
    stats.matches += 1
    for player in match["players"]:
        playback_data = player["playbackData"]
        if playback_data is None:
            continue
        playback = cast(StratzPlayerPlayback, playback_data)
        events = sorted(playback["experienceEvents"], key=lambda event: event["time"])
        event_index = 0
        cumulative_xp = 0
        for level_index, level_second in enumerate(player["stats"]["level"]):
            while event_index < len(events) and events[event_index]["time"] <= level_second:
                cumulative_xp += events[event_index]["amount"]
                event_index += 1
            expected_level = level_index + 1
            actual_level = bisect_right(LEVEL_XP, cumulative_xp)
            stats.checks += 1
            if abs(actual_level - expected_level) > 1:
                stats.mismatches += 1


def collect_lead_residuals(match: StratzMatch, stats: ResidualStats) -> None:
    """Collect level-based XP minus STRATZ XP leads at minute boundaries."""
    xp_leads = match["radiantExperienceLeads"]
    if xp_leads is None:
        return
    for second in range(0, 541, 60):
        lead_index = second // 60 + 1
        if lead_index >= len(xp_leads):
            continue
        level_xp_advantage = radiant_xp_advantage_at_second(match["players"], second)
        stats.residuals.append(level_xp_advantage - xp_leads[lead_index])


def print_exact_report(stats_by_month: dict[str, ExactCheckStats]) -> None:
    """Print the monthly playback-level mismatch report."""
    print("exact_level_checks")
    print("month,matches,checks,mismatches,mismatch_rate")
    for month, stats in sorted(stats_by_month.items()):
        rate = stats.mismatches / stats.checks if stats.checks else 0.0
        print(f"{month},{stats.matches},{stats.checks},{stats.mismatches},{rate:.6f}")


def print_residual_report(stats_by_month: dict[str, ResidualStats]) -> None:
    """Print monthly mean and median XP residuals."""
    print("level_xp_residuals")
    print("month,samples,mean_residual_xp,median_residual_xp")
    for month, stats in sorted(stats_by_month.items()):
        if not stats.residuals:
            continue
        print(
            f"{month},{len(stats.residuals)},{mean(stats.residuals):.3f},"
            f"{median(stats.residuals):.3f}"
        )


def main() -> int:
    """Run both non-failing level XP reports."""
    exact_by_month: dict[str, ExactCheckStats] = defaultdict(ExactCheckStats)
    residuals_by_month: dict[str, ResidualStats] = defaultdict(ResidualStats)
    for match in load_matches():
        month = match_month(match)
        check_playback_levels(match, exact_by_month[month])
        collect_lead_residuals(match, residuals_by_month[month])
    print_exact_report(exact_by_month)
    print_residual_report(residuals_by_month)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
