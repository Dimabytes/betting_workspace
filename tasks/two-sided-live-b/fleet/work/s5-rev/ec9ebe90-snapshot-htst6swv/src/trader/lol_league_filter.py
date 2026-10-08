"""Admit LoL collector events using their current archived Polymarket league."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from shared.utils.log import get_logger
from shared.utils.lol_leagues import (
    is_allowed_league,
    load_canonical_league_whitelist,
    parse_event_league,
)
from trader.collector_sidecars import FreshSidecar

logger = get_logger(__name__)


@dataclass(frozen=True)
class LeagueSelection:
    """One cycle's admitted sidecars: whitelisted trade, other known leagues record-only."""

    traded: tuple[FreshSidecar, ...]
    record_only: tuple[FreshSidecar, ...]


@dataclass(frozen=True)
class _EventLeague:
    league: str | None
    reason: str | None


def read_event_league(archive_root: Path, event_id: str) -> _EventLeague:
    """Read one collector Gamma event; unavailable or mismatched files fail closed."""
    if Path(event_id).name != event_id or event_id in (".", ".."):
        return _EventLeague(None, "invalid_event_id")
    path = archive_root / "metadata" / "events" / f"{event_id}.json"
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _EventLeague(None, "missing_event_file")
    except OSError:
        return _EventLeague(None, "unreadable_event_file")
    except (ValueError, UnicodeError):
        return _EventLeague(None, "invalid_event_file")
    if not isinstance(document, dict):
        return _EventLeague(None, "invalid_event_file")
    event = _unwrap_collector_event(cast(dict[str, object], document))
    if event is None:
        return _EventLeague(None, "invalid_event_file")
    league = parse_event_league(event.get("metadata"), None)
    if league is None:
        league = parse_event_league(event.get("eventMetadata"), event.get("title"))
    if _recorded_event_id(event) != event_id:
        return _EventLeague(league, "event_id_mismatch")
    return _EventLeague(league, None)


def _unwrap_collector_event(document: dict[str, object]) -> dict[str, object] | None:
    """Return the Gamma payload; collector-v1 files wrap it under `event`."""
    if "schemaVersion" not in document:
        return document
    nested = document.get("event")
    if isinstance(nested, dict):
        return cast(dict[str, object], nested)
    return None


def _recorded_event_id(event: dict[str, object]) -> str | None:
    """String event id; Gamma may persist the same value as an int."""
    raw = event.get("id")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    return None


class LolLeagueFilter:
    """Snapshot the canonical whitelist at startup, retry event files every cycle."""

    def __init__(self, archive_root: Path) -> None:
        self._archive_root = archive_root
        self._whitelist = load_canonical_league_whitelist()
        self._skips: dict[str, _EventLeague] = {}

    def select_sidecars(self, sidecars: tuple[FreshSidecar, ...]) -> LeagueSelection:
        """Read each event once per cycle and log skip transitions once at INFO.

        A known league outside the whitelist is record-only: the match writes its
        GRID archive but never attaches. Unreadable or mismatched event files
        drop outright: starting a whitelisted map as record-only would mute it
        until the map ends.
        """
        allowed: set[str] = set()
        record_only: set[str] = set()
        skips: dict[str, _EventLeague] = {}
        for event_id in sorted({sidecar.event_id for sidecar in sidecars}):
            observed = read_event_league(self._archive_root, event_id)
            if observed.reason is None:
                if is_allowed_league(self._whitelist, observed.league):
                    allowed.add(event_id)
                    continue
                if observed.league is None:
                    observed = _EventLeague(None, "missing_league")
                else:
                    record_only.add(event_id)
                    observed = _EventLeague(observed.league, "league_not_whitelisted")
            verdict = "record-only" if event_id in record_only else "skipped"
            log = logger.debug if self._skips.get(event_id) == observed else logger.info
            log(
                "LoL event %s event_id=%s league=%r reason=%s",
                verdict,
                event_id,
                observed.league,
                observed.reason,
            )
            skips[event_id] = observed
        self._skips = skips
        return LeagueSelection(
            traded=tuple(sidecar for sidecar in sidecars if sidecar.event_id in allowed),
            record_only=tuple(sidecar for sidecar in sidecars if sidecar.event_id in record_only),
        )
