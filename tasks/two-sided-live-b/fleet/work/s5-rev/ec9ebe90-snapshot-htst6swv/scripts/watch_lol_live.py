"""Print every tick of one live LoL match from the public GRID widget socket.

Same feed and printer as watch_grid_live.py. Listing is Gamma tag
league-of-legends. This is the feed the Polymarket match page renders.

Invocation:
  make run F=scripts/watch_lol_live.py
  make run F=scripts/watch_lol_live.py ARGS="<market url>"
  make run F=scripts/watch_lol_live.py ARGS="lol-cpd-mvu-2026-08-28"
  make run F=scripts/watch_lol_live.py ARGS="2975621"
"""

import asyncio
import sys

from watch_grid_live import (
    follow_series,
    list_live_events,
    log_events,
    resolve_event,
)

from shared.utils.http import http_client
from shared.utils.log import get_logger, setup_logging

logger = get_logger(__name__)

LOL_TAG_SLUG = "league-of-legends"


def main() -> int:
    """List live LoL events, or follow the one named by the first argument."""
    setup_logging()
    with http_client() as client:
        if len(sys.argv) < 2:
            log_events(list_live_events(client, LOL_TAG_SLUG))
            logger.info("pass a market url, a slug or a grid series id to follow one series")
            return 0
        event = resolve_event(client, sys.argv[1])
    if event.title:
        logger.info("%s (%s)", event.title, event.slug)
    return asyncio.run(follow_series(event.series_id))


if __name__ == "__main__":
    raise SystemExit(main())
