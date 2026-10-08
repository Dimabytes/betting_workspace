from pathlib import Path
from typing import Literal, TypeGuard, cast

from shared.constants.paths import RAW_STRATZ_MATCHES_DIR
from shared.types.stratz import (
    StratzMatch,
    StratzMatchBase,
    StratzMatchCache,
    StratzPlayer,
    UsableStratzMatch,
)
from shared.utils.dota_levels import is_level_timeline_monotonic
from shared.utils.json_io import read_gzip_json

# Longest constant prefix a real match shows is 3; broken parses start at 60.
FLAT_PREFIX_LIMIT = 5
RESET_SAMPLE_LIMIT = 2000

UnusableReason = Literal["missing_leads", "flat_edge", "reset_tail", "level_timeline"]


def player_death_times(player: StratzPlayer) -> list[int]:
    return sorted(event["time"] for event in player["stats"]["deathEvents"])


def death_times(match: StratzMatchBase, radiant: bool) -> list[int]:
    times: list[int] = []
    for player in match["players"]:
        if player["isRadiant"] != radiant:
            continue
        times.extend(player_death_times(player))
    return sorted(times)


def stratz_match_cache_path(match_id: int) -> Path:
    return RAW_STRATZ_MATCHES_DIR / f"match_{int(match_id)}.json.gz"


def try_load_stratz_winners(match_ids: tuple[int, ...]) -> dict[int, bool]:
    """`didRadiantWin` per cached match; ids without a cache file are absent."""
    winners: dict[int, bool] = {}
    for match_id in match_ids:
        try:
            payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
        except FileNotFoundError:
            continue
        match = cast("StratzMatch | None", payload["data"].get("match"))
        if match is None:
            continue
        winners[match_id] = bool(match["didRadiantWin"])
    return winners


def get_stratz_match_reach_data(match_id: int) -> UsableStratzMatch:
    """Read one cached match. Callers pass catalog ids, so the collect gate already ran."""
    payload = cast(StratzMatchCache, read_gzip_json(stratz_match_cache_path(match_id)))
    match = payload["data"]["match"]
    assert is_usable_stratz_match(match)
    return match


def stratz_networth_xp_leads(match: StratzMatch) -> tuple[list[float], list[float]]:
    raw_nw = match.get("radiantNetworthLeads")
    raw_xp = match.get("radiantExperienceLeads")
    nw = [float(value) for value in raw_nw] if raw_nw else []
    xp = [float(value) for value in raw_xp] if raw_xp else []
    return nw, xp


def constant_prefix_length(values: list[float]) -> int:
    length = 0
    while length < len(values) and values[length] == values[0]:
        length += 1
    return length


def has_reset_tail(nw: list[float], xp: list[float]) -> bool:
    end = len(xp)
    if end >= 2 and xp[end - 1] == 0:
        networth_is_live = abs(nw[end - 1]) > RESET_SAMPLE_LIMIT
        experience_was_live = abs(xp[end - 2]) > RESET_SAMPLE_LIMIT
        return networth_is_live or experience_was_live
    return False


def stratz_match_unusable_reason(match: StratzMatch | None) -> UnusableReason | None:
    if match is None:
        return "missing_leads"
    nw, xp = stratz_networth_xp_leads(match)
    if len(nw) < 2 or len(xp) < 2:
        return "missing_leads"
    edges = [nw, nw[::-1], xp, xp[::-1]]
    longest_flat_edge = max(constant_prefix_length(values) for values in edges)
    if longest_flat_edge >= FLAT_PREFIX_LIMIT:
        return "flat_edge"
    if has_reset_tail(nw, xp):
        return "reset_tail"
    if not all(is_level_timeline_monotonic(player) for player in match.get("players") or []):
        return "level_timeline"
    return None


def is_usable_stratz_match(match: StratzMatch) -> TypeGuard[UsableStratzMatch]:
    return stratz_match_unusable_reason(match) is None
