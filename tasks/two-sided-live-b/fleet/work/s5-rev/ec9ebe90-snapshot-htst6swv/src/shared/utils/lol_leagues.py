"""Select LoL maps whose Polymarket event league is on an explicit whitelist.

The whitelist names base leagues, the alias table maps observed universe names onto
them, and every other name (a separate tournament, or a missing name) is excluded.

`no_live_feed` names whitelisted leagues whose Polymarket events carry no
`eventMetadata.gridSeriesId`, so live and paper never trade them. Live admission
ignores that list; the backtest drops those maps so its population matches live.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

import pandas as pd

from shared.constants.paths import BASE_DIR
from shared.utils.log import get_logger

LOL_LEAGUE_WHITELIST_PATH = BASE_DIR / "config" / "lol_league_whitelist.json"
LEAGUE_SUFFIX_RE = re.compile(r"\(BO\s*\d+\)\s*-\s*(.+)$", re.IGNORECASE)
logger = get_logger(__name__)


@dataclass(frozen=True)
class LeagueWhitelist:
    """One whitelist file: base leagues, observed-name aliases, and its content hash."""

    path: Path
    sha256: str
    leagues: frozenset[str]
    no_live_feed: frozenset[str]
    aliases: Mapping[str, str]


def read_league_whitelist(path: Path) -> LeagueWhitelist:
    """Read and validate the whitelist JSON."""
    try:
        raw = path.read_bytes()
        payload: object = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise ValueError(f"LoL league whitelist {path}: cannot read configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: whitelist must be a JSON object")
    fields = cast(dict[str, object], payload)
    names = fields.get("leagues")
    raw_aliases = fields.get("aliases")
    if not isinstance(names, list) or not names:
        raise ValueError(f"{path}: leagues must be a nonempty list")
    if not isinstance(raw_aliases, dict):
        raise ValueError(f"{path}: aliases must be an object")
    entries = cast(list[object], names)
    alias_entries = cast(dict[str, object], raw_aliases)
    all_names = [*entries, *alias_entries, *alias_entries.values()]
    if any(
        not isinstance(name, str) or not name.strip() or name != name.strip() for name in all_names
    ):
        raise ValueError(
            f"{path}: league and alias names must be nonempty strings without outer whitespace"
        )
    leagues = frozenset(cast(list[str], entries))
    if len(leagues) != len(entries):
        raise ValueError(f"{path}: duplicate league names")
    aliases = cast(dict[str, str], alias_entries)
    outside = sorted({base for base in aliases.values() if base not in leagues})
    if outside:
        raise ValueError(f"{path}: aliases point at leagues outside the whitelist: {outside}")
    shadowed = sorted(set(aliases) & leagues)
    if shadowed:
        raise ValueError(f"{path}: aliases repeat whitelisted league names: {shadowed}")
    return LeagueWhitelist(
        path=path,
        sha256=sha256(raw).hexdigest(),
        leagues=leagues,
        no_live_feed=_read_no_live_feed(path, fields.get("no_live_feed", []), leagues),
        aliases=aliases,
    )


def _read_no_live_feed(path: Path, raw: object, leagues: frozenset[str]) -> frozenset[str]:
    """Whitelisted leagues the live GRID feed never serves; the backtest drops them."""
    if not isinstance(raw, list):
        raise ValueError(f"{path}: no_live_feed must be a list")
    names = cast(list[object], raw)
    if any(not isinstance(name, str) or name != name.strip() or not name for name in names):
        raise ValueError(f"{path}: no_live_feed names must be nonempty stripped strings")
    no_live_feed = frozenset(cast(list[str], names))
    unlisted = sorted(no_live_feed - leagues)
    if unlisted:
        raise ValueError(f"{path}: no_live_feed names leagues outside the whitelist: {unlisted}")
    return no_live_feed


def load_canonical_league_whitelist() -> LeagueWhitelist:
    """Load the mandatory LoL admission policy and announce its exact identity."""
    whitelist = read_league_whitelist(LOL_LEAGUE_WHITELIST_PATH)
    logger.info("LoL league whitelist path=%s sha256=%s", whitelist.path, whitelist.sha256)
    return whitelist


def parse_event_league(metadata: object, title: object) -> str | None:
    """Read eventMetadata.league first, otherwise the Polymarket (BOx) - suffix."""
    if isinstance(metadata, dict):
        league = _clean_league(cast(dict[str, object], metadata).get("league"))
        if league is not None:
            return league
    if not isinstance(title, str):
        return None
    match = LEAGUE_SUFFIX_RE.search(title)
    return None if match is None else _clean_league(match.group(1))


def _clean_league(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def is_allowed_league(whitelist: LeagueWhitelist, league: str | None) -> bool:
    """Admit exact names and explicit aliases only, never tournament prefixes."""
    return league is not None and resolve_base_league(whitelist, league) in whitelist.leagues


def resolve_base_league(whitelist: LeagueWhitelist, observed_league: str) -> str:
    """Map one observed universe league name onto its base league."""
    return whitelist.aliases.get(observed_league, observed_league)


def load_event_leagues(universe_path: Path) -> dict[str, str]:
    """Observed league name per PM event_id; events with no name are left out."""
    universe = pd.read_parquet(universe_path, columns=["event_id", "league"])
    named = universe.dropna(subset=["league"])
    leagues_by_event: dict[str, str] = {}
    for raw_event_id, raw_league in zip(named["event_id"], named["league"], strict=True):
        event_id = str(raw_event_id)
        league = str(raw_league)
        known = leagues_by_event.setdefault(event_id, league)
        if known != league:
            raise ValueError(f"event {event_id} carries two league names: {known!r} and {league!r}")
    return leagues_by_event


def select_backtest_event_ids(whitelist: LeagueWhitelist, universe_path: Path) -> frozenset[str]:
    """PM event ids the backtest replays: whitelisted leagues minus the no_live_feed ones."""
    leagues_by_event = load_event_leagues(universe_path)
    return frozenset(
        event_id
        for event_id, league in leagues_by_event.items()
        if is_allowed_league(whitelist, league)
        and resolve_base_league(whitelist, league) not in whitelist.no_live_feed
    )
