"""Read cached OpenDota matches and pauses across pipeline stages."""

from pathlib import Path
from typing import cast

from shared.constants.paths import RAW_OPENDOTA_MATCHES_DIR
from shared.types.opendota import OpenDotaMatch, OpenDotaMatchCache, OpenDotaPause
from shared.utils.json_io import read_gzip_json


def opendota_match_cache_path(match_id: int) -> Path:
    return RAW_OPENDOTA_MATCHES_DIR / f"match_{int(match_id)}.json.gz"


def get_opendota_match(match_id: int) -> OpenDotaMatch:
    """The cached OpenDota match object for one match id."""
    payload = cast(OpenDotaMatchCache, read_gzip_json(opendota_match_cache_path(match_id)))
    return payload["data"]


def try_load_opendota_pauses(match_ids: tuple[int, ...]) -> dict[int, list[OpenDotaPause]]:
    """Load cached OpenDota pauses for ids that have a match file."""
    pauses_by_match: dict[int, list[OpenDotaPause]] = {}
    for match_id in match_ids:
        try:
            match = get_opendota_match(match_id)
        except FileNotFoundError:
            continue
        pauses = match.get("pauses")
        if pauses is None:
            continue
        pauses_by_match[match_id] = pauses
    return pauses_by_match
