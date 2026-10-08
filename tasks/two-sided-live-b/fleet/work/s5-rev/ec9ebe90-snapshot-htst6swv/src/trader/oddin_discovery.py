"""Bind an Oddin match onto an already-known Steam/GRID source."""

import asyncio
from collections.abc import Mapping, Sequence

import httpx

from shared.utils.log import get_logger
from shared.utils.team_names import orient_outcomes
from trader.oddin_client import USER_AGENT, fetch_snapshot_envelope, widget_config
from trader.oddin_crypto import OddinCryptoError, load_feed_key, open_envelope
from trader.oddin_feed import project_tick
from trader.oddin_types import FINISHED_STATUS, VALID_DATA, OddinFeedError, OddinMatch, OddinTick

logger = get_logger(__name__)

# One Disir HTTP snapshot. Long enough for a cold TLS handshake, short enough
# that a dead host cannot stall the 30s discovery cycle.
PROBE_TIMEOUT_SECONDS = 5.0


def snapshot_is_playing(tick: OddinTick) -> bool:
    """True when the scoreboard is a map in progress, not a future or finished card."""
    return (
        tick.data_status == VALID_DATA
        and tick.map_order is not None
        and tick.match_status != FINISHED_STATUS
    )


async def _probe_one(client: httpx.AsyncClient, match_id: str, key: bytes) -> bool:
    try:
        envelope = await fetch_snapshot_envelope(client, widget_config(match_id))
        tick = project_tick(open_envelope(envelope, key))
    except (OddinFeedError, OddinCryptoError, httpx.HTTPError, OSError):
        return False
    return snapshot_is_playing(tick)


def probe_playing(match_ids: Sequence[str]) -> dict[str, bool]:
    """Whether each card's Disir snapshot is a map in progress. Faults are not playing."""
    if not match_ids:
        return {}
    try:
        key = load_feed_key()
    except OddinCryptoError as exc:
        logger.info("oddin playing probe skipped: %s", type(exc).__name__)
        return {match_id: False for match_id in match_ids}

    async def run() -> dict[str, bool]:
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            flags = await asyncio.gather(
                *(_probe_one(client, match_id, key) for match_id in match_ids)
            )
        return dict(zip(match_ids, flags, strict=True))

    try:
        return asyncio.run(run())
    except (OddinFeedError, httpx.HTTPError, OSError) as exc:
        logger.info("oddin playing probe failed: %s", type(exc).__name__)
        return {match_id: False for match_id in match_ids}


def unique_oddin_match_id(
    radiant: str,
    dire: str,
    matches: Sequence[OddinMatch],
    aliases: Mapping[str, tuple[str, ...]],
    playing: dict[str, bool],
) -> str | None:
    """The one open card whose home/away orient against these sides, or None.

    One name hit binds immediately. Several hits keep the single card whose
    Disir snapshot is already a map in progress. `playing` caches that probe
    for the rest of the discovery cycle.
    """
    hits = [
        match
        for match in matches
        if orient_outcomes(match.home_name, match.away_name, radiant, dire, aliases) is not None
    ]
    if len(hits) == 1:
        return hits[0].id
    if not matches:
        return None
    if not hits:
        logger.info("oddin bind skipped: no name match for %s vs %s", radiant, dire)
        return None
    missing = tuple(hit.id for hit in hits if hit.id not in playing)
    if missing:
        playing.update(probe_playing(missing))
    live_hits = [hit for hit in hits if playing.get(hit.id)]
    if len(live_hits) == 1:
        logger.info(
            "oddin bind %s for %s vs %s (%d cards)",
            live_hits[0].id,
            radiant,
            dire,
            len(hits),
        )
        return live_hits[0].id
    logger.info(
        "oddin bind skipped: %d name matches for %s vs %s (%s)",
        len(hits),
        radiant,
        dire,
        ", ".join(hit.id for hit in hits),
    )
    return None
