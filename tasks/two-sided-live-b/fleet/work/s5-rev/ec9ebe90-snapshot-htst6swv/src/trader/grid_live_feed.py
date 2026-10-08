"""GRID LiveFeed: archive each raw widget frame, then yield the reduced tick."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import websockets

from shared.constants.paths import TRADER_DIR
from shared.constants.strategy import BUY_CUTOFF_SECOND, GRID_FEED_STALE_SECONDS
from shared.utils.log import get_logger
from trader.archive_paths import FsyncedJsonlWriter, match_archive_dir
from trader.game_profile import GameProfile
from trader.grid_feed import FEED_GONE_FRAME, GridFrameReducer, received_at_utc
from trader.grid_widget_types import GridStateArchiveRecord
from trader.grid_widgets import (
    GRID_WIDGETS_ORIGIN,
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_SECONDS,
    build_socket_url,
    parse_frame,
)
from trader.live_feed import FeedEvent, FeedSource, KillTick
from trader.paths import GRID_STATE_ARCHIVE_FILENAME

logger = get_logger(__name__)


@dataclass(frozen=True)
class AppliedWidgetFrame:
    """One archived widget frame that reduced to a tick, plus cutoff tracking."""

    event: FeedEvent | KillTick
    past_cutoff: bool


class GridLiveFeed:
    """Widget-socket adapter that owns grid_state.jsonl and yields FeedEvent ticks."""

    source: FeedSource = FeedSource.GRID
    stale_seconds: float = GRID_FEED_STALE_SECONDS

    def __init__(
        self,
        series_id: str,
        map_number: int,
        outcome_0_name: str,
        outcome_1_name: str,
        match_id: str,
        profile: GameProfile,
    ) -> None:
        """Bind the GRID series, the 1-based map, market names, and the match archive id.

        Snapshot XP and top-player rules come from `profile`. GRID orients YES
        against this map's board; discovery's Steam sides are not an input.
        """
        self._url = build_socket_url(series_id)
        self._match_id = match_id
        self._reducer = GridFrameReducer(map_number, outcome_0_name, outcome_1_name, profile)

    def _archive_feed_gone(
        self, writer: FsyncedJsonlWriter, now: datetime
    ) -> FeedEvent | KillTick | None:
        """Persist the feed-gone sentinel and reduce it to a finished tick when sides are pinned."""
        record: GridStateArchiveRecord = {
            "received_at_utc": received_at_utc(now),
            "frame": FEED_GONE_FRAME,
        }
        writer.write_record(record)
        return self._reducer.reduce_frame(parse_frame(FEED_GONE_FRAME), now)

    def _apply_widget_frame(
        self,
        writer: FsyncedJsonlWriter,
        raw: object,
        now: datetime,
        past_cutoff: bool,
    ) -> AppliedWidgetFrame | None:
        """Archive one raw widget frame and reduce it. None when the frame is not a tick."""
        text = str(raw)
        record: GridStateArchiveRecord = {
            "received_at_utc": received_at_utc(now),
            "frame": text,
        }
        writer.write_record(record)
        event = self._reducer.reduce_frame(parse_frame(text), now)
        if event is None:
            return None
        if isinstance(event, FeedEvent) and event.snapshot.second >= BUY_CUTOFF_SECOND:
            past_cutoff = True
        return AppliedWidgetFrame(event, past_cutoff)

    async def ticks(self) -> AsyncIterator[FeedEvent | KillTick]:
        """Reconnect to the same series URL until a terminal event or too many socket faults."""
        archive_dir = match_archive_dir(TRADER_DIR, self._match_id)
        writer = FsyncedJsonlWriter(archive_dir / GRID_STATE_ARCHIVE_FILENAME)
        failures = 0
        past_cutoff = False
        try:
            while failures < MAX_CONSECUTIVE_FAILURES:
                try:
                    async with websockets.connect(
                        self._url,
                        origin=cast(websockets.Origin, GRID_WIDGETS_ORIGIN),
                        max_size=None,
                    ) as socket:
                        async for raw in socket:
                            failures = 0
                            applied = self._apply_widget_frame(
                                writer, raw, datetime.now(UTC), past_cutoff
                            )
                            if applied is None:
                                continue
                            past_cutoff = applied.past_cutoff
                            yield applied.event
                            if (
                                isinstance(applied.event, FeedEvent)
                                and applied.event.snapshot.finished
                            ):
                                return
                    # StopAsyncIteration is "stream over", not a GRID drop.
                    return
                except websockets.WebSocketException as exc:
                    unpublished = isinstance(exc, websockets.InvalidStatus)
                    if unpublished and past_cutoff:
                        gone = self._archive_feed_gone(writer, datetime.now(UTC))
                        if gone is not None:
                            yield gone
                        return
                    failures += 1
                    logger.warning(
                        "grid socket closed (%s/%s): %s",
                        failures,
                        MAX_CONSECUTIVE_FAILURES,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(RECONNECT_SECONDS)
            if past_cutoff:
                gone = self._archive_feed_gone(writer, datetime.now(UTC))
                if gone is not None:
                    yield gone
                return
            logger.error("grid feed stopping: %s closed sockets in a row", MAX_CONSECUTIVE_FAILURES)
        finally:
            writer.close()
