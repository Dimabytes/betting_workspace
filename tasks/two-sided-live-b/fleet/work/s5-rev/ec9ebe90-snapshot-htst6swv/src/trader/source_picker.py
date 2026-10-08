"""Pick a live source once per map by the smallest usable declared delay."""

import asyncio
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import websockets

from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from shared.utils.log import get_logger
from trader.grid_widgets import (
    GRID_WIDGETS_ORIGIN,
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    Frame,
    Scoreboard,
    build_socket_url,
    parse_frame,
    read_current_scoreboard,
    read_net_worth,
)
from trader.live_feed import FeedSource
from trader.oddin_client import iter_scoreboard_payloads, widget_config
from trader.oddin_crypto import OddinCryptoError, load_feed_key
from trader.oddin_feed import parse_oddin_timestamp, project_tick
from trader.oddin_types import VALID_DATA, OddinFeedError

# Feed-choice gate: admits the standard 60 s tournament delay and rejects anything above it.
MAX_FEED_DELAY_SECONDS = 61
# Overall cap for the Oddin delay probe: connect, handshake, and one usable tick.
ODDIN_PROBE_SECONDS = 15.0
# Tie-break when delays are equal: GRID, then Oddin.
SOURCE_TIE_ORDER: tuple[FeedSource, ...] = (FeedSource.GRID, FeedSource.ODDIN)

logger = get_logger(__name__)


@dataclass(frozen=True)
class FeedCandidate:
    """One live source that can be picked: identity and declared delay.

    Connection params stay on DiscoveredMatch; the picker only compares delay.
    """

    source: FeedSource
    delay_s: int


def pick_source(candidates: Sequence[FeedCandidate]) -> FeedCandidate | None:
    """Return the fastest candidate at or under the delay gate; tie-break is SOURCE_TIE_ORDER."""
    usable = [candidate for candidate in candidates if candidate.delay_s <= MAX_FEED_DELAY_SECONDS]
    if not usable:
        return None
    return min(
        usable,
        key=lambda candidate: (candidate.delay_s, SOURCE_TIE_ORDER.index(candidate.source)),
    )


@dataclass(frozen=True)
class GridProbeCycle:
    """One parallel GRID scoreboard pass: parsed boards and handshake-rejected series."""

    boards: dict[str, Scoreboard]
    unpublished: frozenset[str]


def _frame_value[T](raw: str, service: str, read: Callable[[Frame], T | None]) -> T | None:
    """Apply `read` to one frame of `service`, or None when the frame is unusable."""
    try:
        frame = parse_frame(raw)
        if frame.service != service or not frame.payload:
            return None
        return read(frame)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError):
        return None


async def _probe_first[T](
    series_id: str,
    service: str,
    read: Callable[[Frame], T | None],
    label: str,
    unpublished: set[str],
) -> T | None:
    """Read the first frame `read` accepts off the widget socket, or None if the probe fails."""
    try:
        async with asyncio.timeout(GRID_FEED_STALE_SECONDS):
            async with websockets.connect(
                build_socket_url(series_id),
                origin=cast(websockets.Origin, GRID_WIDGETS_ORIGIN),
                max_size=None,
            ) as socket:
                async for raw in socket:
                    value = _frame_value(str(raw), service, read)
                    if value is not None:
                        return value
    except TimeoutError:
        logger.debug("grid %s probe failed series=%s: TimeoutError", label, series_id)
    except websockets.InvalidStatus:
        unpublished.add(series_id)
        logger.debug("grid %s probe failed series=%s: InvalidStatus", label, series_id)
    except (OSError, websockets.WebSocketException) as exc:
        logger.info(
            "grid %s probe failed series=%s: %s",
            label,
            series_id,
            type(exc).__name__,
        )
    return None


def _table_delay_for_map(frame: Frame, map_number: int) -> int | None:
    """Return `frame.delay` when this table is the pinned map the model quotes on."""
    snapshot = read_net_worth(frame.payload, frame.delay)
    if snapshot is None or snapshot.game_number != map_number:
        return None
    return frame.delay


async def probe_grid_delay(series_id: str, map_number: int) -> int | None:
    """Read the pinned map's table delay. The scoreboard delay is not the model's lag."""
    unpublished: set[str] = set()
    return await _probe_first(
        series_id,
        TABLE_SERVICE,
        lambda frame: _table_delay_for_map(frame, map_number),
        f"table map={map_number}",
        unpublished,
    )


async def _probe_one_scoreboard(
    series_id: str, unpublished: set[str]
) -> tuple[str, Scoreboard | None]:
    """Probe one series scoreboard and keep the series id next to the board."""
    board = await _probe_first(
        series_id,
        SCOREBOARD_SERVICE,
        lambda frame: read_current_scoreboard(frame.payload),
        "scoreboard",
        unpublished,
    )
    return series_id, board


async def _probe_grid_scoreboard_cycle(series_ids: tuple[str, ...]) -> GridProbeCycle:
    """Probe every series scoreboard at once; handshake rejects land in unpublished."""
    unpublished: set[str] = set()
    probed = await asyncio.gather(
        *(_probe_one_scoreboard(series_id, unpublished) for series_id in series_ids)
    )
    boards = {series_id: board for series_id, board in probed if board is not None}
    return GridProbeCycle(boards, frozenset(unpublished))


def probe_grid_scoreboard_cycle(series_ids: Iterable[str]) -> GridProbeCycle:
    """Blocking GRID scoreboard cycle: boards plus handshake-rejected series ids."""
    ordered = tuple(sorted(set(series_ids)))
    if not ordered:
        return GridProbeCycle({}, frozenset())
    return asyncio.run(_probe_grid_scoreboard_cycle(ordered))


def probe_grid_scoreboards(series_ids: Iterable[str]) -> dict[str, Scoreboard]:
    """Blocking GRID scoreboard per series, probed in parallel. Failed probes drop out."""
    return probe_grid_scoreboard_cycle(series_ids).boards


async def probe_oddin_delay(oddin_match_id: str, map_number: int) -> int | None:
    """Read `received - lastUpdatedAt` from the first VALID_DATA snapshot of this map.

    HTTP seed is not used. Any fault, timeout, wrong map, or missing VALID_DATA is None.
    """
    try:
        async with asyncio.timeout(ODDIN_PROBE_SECONDS):
            config = widget_config(oddin_match_id)
            key = load_feed_key()
            async for payload in iter_scoreboard_payloads(config, key, ODDIN_PROBE_SECONDS):
                tick = project_tick(payload)
                if tick.data_status != VALID_DATA or tick.map_order != map_number:
                    continue
                updated = parse_oddin_timestamp(tick.last_updated_at)
                if updated is None:
                    logger.info("oddin delay probe failed match=%s: lastUpdatedAt", oddin_match_id)
                    return None
                delay = round((datetime.now(UTC) - updated.astimezone(UTC)).total_seconds())
                logger.info("oddin delay probe match=%s delay=%ss", oddin_match_id, delay)
                return delay
    except TimeoutError:
        logger.debug("oddin delay probe failed match=%s: TimeoutError", oddin_match_id)
    except (OSError, OddinFeedError, OddinCryptoError, websockets.WebSocketException) as exc:
        logger.info("oddin delay probe failed match=%s: %s", oddin_match_id, type(exc).__name__)
    return None
