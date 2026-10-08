"""Shared complete catalog row fixture for pipeline tests.

Tests that need a parsed entry wrap this in `match_catalog.catalog_entry_from_row`,
so a fixture row goes through exactly the production parse.
"""

import json
from datetime import UTC, datetime
from typing import TypedDict

from shared.types.dataset import MatchCatalogRow
from shared.types.opendota import OpenDotaPause
from shared.utils.match_time import get_horn_datetime, get_state_available_ts, parse_utc


class ReplayCatalogKwargs(TypedDict):
    """Venue fields the backtest Telonex fixture layout expects."""

    token_id_0: str
    token_id_1: str
    seconds_delay: int
    market_slug: str
    market_closed_at: str
    started_at: datetime
    ended_at: datetime


def catalog_row(
    match_id: int,
    *,
    start_time: int = 1,
    playback_available: bool = True,
    radiant_prior: float = 0.5,
    condition_id: str | None = None,
    event_id: str | None = None,
    duration: int = 1,
    radiant_token_index: int = 1,
    radiant_win: bool = True,
    token_id_0: str = "yes",
    token_id_1: str = "no",
    seconds_delay: int = 0,
    market_slug: str = "test-market",
    market_closed_at: str = "2026-01-01T00:00:00+00:00",
    spawn_at: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    horn_at: datetime | None = None,
    pauses: list[OpenDotaPause] | None = None,
    archive_id: str | None = None,
    archive_root: str | None = None,
    archive_feed_source: str | None = None,
    schedule_fingerprint: str | None = None,
) -> MatchCatalogRow:
    """One complete published catalog row.

    Spawn may be passed as unix (`start_time`), ISO (`spawn_at`), or datetime
    (`started_at`). Archive-attached rows carry `archive_id`; without a spawn
    they keep `spawn_at=None` and need an explicit `horn_at`.
    """
    pauses = [] if pauses is None else pauses
    if started_at is not None:
        spawn_at = started_at.isoformat()
    elif spawn_at is None and archive_id is None:
        spawn_at = datetime.fromtimestamp(start_time, tz=UTC).isoformat()
    if horn_at is not None:
        horn = horn_at
    elif spawn_at is not None:
        horn = get_horn_datetime(parse_utc(spawn_at), pauses)
    else:
        horn = datetime.fromtimestamp(start_time, tz=UTC)
    if ended_at is None:
        ended_at_iso = get_state_available_ts(horn=horn, second=duration, pauses=pauses).isoformat()
    else:
        ended_at_iso = ended_at.isoformat()
    return MatchCatalogRow(
        match_id=match_id,
        condition_id=condition_id if condition_id is not None else f"condition-{match_id}",
        event_id=event_id if event_id is not None else f"event-{match_id}",
        radiant_token_index=radiant_token_index,
        spawn_at=spawn_at,
        ended_at=ended_at_iso,
        playback_available=playback_available,
        duration=duration,
        radiant_prior=radiant_prior,
        seconds_delay=seconds_delay,
        token_id_0=token_id_0,
        token_id_1=token_id_1,
        market_slug=market_slug,
        market_closed_at=market_closed_at,
        horn_at=horn.isoformat(),
        horn_source="archive" if archive_id is not None else "grid_derived",
        pauses_json=json.dumps(pauses),
        pauses_source="archive" if archive_id is not None else "opendota",
        radiant_win=radiant_win,
        winner_source="stratz",
        archive_id=archive_id,
        archive_root=archive_root,
        archive_feed_source=archive_feed_source,
        schedule_fingerprint=schedule_fingerprint,
    )
