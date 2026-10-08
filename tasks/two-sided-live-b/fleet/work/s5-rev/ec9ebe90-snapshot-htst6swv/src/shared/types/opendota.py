"""Shape of the gzipped OpenDota match cache written by s05_fetch_opendota_matches.

Derived from the full 5133-file sample of data/raw/opendota_matches/: `data`,
`fetched_at`, `match_id` are always present at the top level, and `match_id`,
`version` are always present on the match; `pauses` is absent on rare payloads
(e.g. match 9024056927).

The match object carries ~58 keys. Only the ones we read are typed here; add a
field when you start using it rather than mirroring the whole OpenDota schema.

These are static types only: `cast()` at the read boundary, no runtime validation.
"""

from typing import Literal, NotRequired, TypedDict

RadiantTokenIndex = Literal[0, 1]


class OpenDotaPause(TypedDict):
    """One pause. `time` is the game clock second it started, negative before the horn."""

    time: int
    duration: int


class OpenDotaMatch(TypedDict):
    match_id: int
    version: int | None  # None when OpenDota has not parsed the replay
    pauses: NotRequired[list[OpenDotaPause]]
    radiant_win: bool


class OpenDotaMatchCache(TypedDict):
    """Top level of match_<id>.json.gz."""

    match_id: int
    fetched_at: str
    data: OpenDotaMatch


class OpenDotaHero(TypedDict):
    """One row of /api/heroes, used to print hero names instead of ids."""

    id: int
    localized_name: str
