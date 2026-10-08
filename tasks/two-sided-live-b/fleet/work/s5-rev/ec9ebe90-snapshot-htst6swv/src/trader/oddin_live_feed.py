"""Oddin LiveFeed: archive each decrypted snapshot, then yield the reduced tick."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets

from shared.constants.paths import TRADER_DIR
from shared.utils.log import get_logger
from trader.archive_paths import FsyncedJsonlWriter, match_archive_dir
from trader.grid_feed import received_at_utc
from trader.live_feed import FeedEvent, FeedSource
from trader.oddin_client import (
    MAX_CONSECUTIVE_FAILURES,
    RECONNECT_SECONDS,
    disir_client,
    fetch_snapshot_envelope,
    iter_scoreboard_payloads,
    widget_config,
)
from trader.oddin_crypto import OddinCryptoError, load_feed_key, open_envelope
from trader.oddin_feed import (
    CLOSED_EVENT,
    ODDIN_FEED_STALE_SECONDS,
    RECONNECT_EVENT,
    SNAPSHOT_EVENT,
    WS_EVENT,
    OddinSnapshotReducer,
)
from trader.oddin_types import (
    FINISHED_STATUS,
    OddinFeedError,
    OddinStateArchiveRecord,
    ScoreboardSilent,
    WidgetConfig,
)
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME

logger = get_logger(__name__)


def append_series_closed(archive_dir: Path) -> None:
    """Append a terminal scoreboard row so a later replay ends finished."""
    writer = FsyncedJsonlWriter(archive_dir / ODDIN_STATE_ARCHIVE_FILENAME)
    record: OddinStateArchiveRecord = {
        "received_at_utc": received_at_utc(datetime.now(UTC)),
        "event": CLOSED_EVENT,
        "payload": {"matchStatus": FINISHED_STATUS},
    }
    writer.write_record(record)
    writer.close()


# One Disir snapshot per reconnect: a cold handshake must not burn a retry.
SNAPSHOT_TIMEOUT_S = 30.0


class OddinLiveFeed:
    """Widget-socket adapter that owns oddin_state.jsonl and yields FeedEvent ticks."""

    source: FeedSource = FeedSource.ODDIN
    stale_seconds: float = ODDIN_FEED_STALE_SECONDS

    def __init__(
        self,
        oddin_match_id: str,
        map_number: int,
        match_id: str,
        yes_is_radiant: bool,
        map_ended: Callable[[str, int], bool],
    ) -> None:
        """Bind the Oddin match id, 1-based map, archive id, orientation, and close signal."""
        self._oddin_match_id = oddin_match_id
        self._map_number = map_number
        self._match_id = match_id
        self._reducer = OddinSnapshotReducer(map_number, yes_is_radiant)
        self._map_ended = map_ended
        self._last_payload: dict[str, object] | None = None

    def _archive_event(
        self, writer: FsyncedJsonlWriter, now: datetime, event_name: str, payload: object
    ) -> None:
        record: OddinStateArchiveRecord = {
            "received_at_utc": received_at_utc(now),
            "event": event_name,
            "payload": payload,
        }
        writer.write_record(record)

    def _closed_payload(self) -> dict[str, object]:
        if self._last_payload is None:
            return {"matchStatus": FINISHED_STATUS}
        payload = dict(self._last_payload)
        payload["matchStatus"] = FINISHED_STATUS
        return payload

    def _emit_closed(self, writer: FsyncedJsonlWriter) -> FeedEvent | None:
        """Archive a terminal scoreboard row when the catalog says this map is over."""
        if not self._map_ended(self._oddin_match_id, self._map_number):
            return None
        now = datetime.now(UTC)
        payload = self._closed_payload()
        self._archive_event(writer, now, CLOSED_EVENT, payload)
        event = self._reducer.apply_payload(payload, now)
        logger.info("oddin map closed match=%s reason=catalog", self._match_id)
        return event

    def _resync(self, writer: FsyncedJsonlWriter) -> None:
        self._reducer.reset()
        self._archive_event(writer, datetime.now(UTC), RECONNECT_EVENT, None)

    def _count_stop(self, quiet: bool, fault: str | None, failures: int) -> int:
        if quiet:
            logger.info("oddin scoreboard silent, reconnecting")
            return failures
        if fault is None:
            logger.info("oddin scoreboard complete, reconnecting")
            return failures
        failures += 1
        logger.warning("%s (%s/%s)", fault, failures, MAX_CONSECUTIVE_FAILURES)
        return failures

    async def ticks(self) -> AsyncIterator[FeedEvent]:
        """Reconnect until a terminal tick, a catalog close, or too many faults before a tick.

        Before the first tick, five faults in a row stop the feed. After a tick the
        feed only ends on a terminal tick or a catalog close: the socket freezes on
        the last frame of a map, and the catalog can lag that freeze by a minute.
        Scoreboard silence is not a fault: pings keep the socket up between maps.
        """
        archive_dir = match_archive_dir(TRADER_DIR, self._match_id)
        writer = FsyncedJsonlWriter(archive_dir / ODDIN_STATE_ARCHIVE_FILENAME)
        failures = 0
        ticked = False
        key = load_feed_key()
        try:
            config = widget_config(self._oddin_match_id)
            async with disir_client(SNAPSHOT_TIMEOUT_S) as client:
                # ponytail: a ticked map whose catalog row never closes reconnects until restart.
                while ticked or failures < MAX_CONSECUTIVE_FAILURES:
                    quiet = False
                    fault: str | None = None
                    try:
                        yielded = False
                        async for event in self._stream_once(client, writer, config, key):
                            yielded = True
                            ticked = True
                            failures = 0
                            yield event
                            if event.snapshot.finished:
                                return
                        if not yielded:
                            fault = "oddin scoreboard complete without ticks"
                    except ScoreboardSilent:
                        quiet = True
                    except OddinCryptoError as exc:
                        fault = f"oddin decrypt: {type(exc).__name__}"
                    except (
                        OddinFeedError,
                        TimeoutError,
                        websockets.WebSocketException,
                        httpx.HTTPError,
                        OSError,
                    ) as exc:
                        fault = f"oddin socket closed: {type(exc).__name__}"
                    if quiet or fault is not None:
                        closed = self._emit_closed(writer)
                        if closed is not None:
                            yield closed
                            return
                    failures = self._count_stop(quiet, fault, failures)
                    self._resync(writer)
                    await asyncio.sleep(RECONNECT_SECONDS)
                logger.error(
                    "oddin feed stopping: %s closed streams in a row", MAX_CONSECUTIVE_FAILURES
                )
        finally:
            writer.close()

    async def _stream_once(
        self,
        client: httpx.AsyncClient,
        writer: FsyncedJsonlWriter,
        config: WidgetConfig,
        key: bytes,
    ) -> AsyncIterator[FeedEvent]:
        """One HTTP seed (archived, not reduced) plus one websocket until complete or fault."""
        seed = await fetch_snapshot_envelope(client, config)
        seed_payload = open_envelope(seed, key)
        self._archive_event(writer, datetime.now(UTC), SNAPSHOT_EVENT, seed_payload)
        async for payload in iter_scoreboard_payloads(config, key, ODDIN_FEED_STALE_SECONDS):
            now = datetime.now(UTC)
            self._last_payload = dict(payload)
            self._archive_event(writer, now, WS_EVENT, payload)
            event = self._reducer.apply_payload(payload, now)
            if event is not None:
                yield event
                if event.snapshot.finished:
                    return
