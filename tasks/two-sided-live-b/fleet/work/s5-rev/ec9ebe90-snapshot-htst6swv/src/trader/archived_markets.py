"""Resolve a token id back to the match archive that traded it, using match.json on disk.

Every match writes both of its token ids into `match.json` before the first quote,
so the archive itself is the durable token index. Nothing has to be cached in the
process, and a fill that arrives after a restart still finds its session journal.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from trader.paths import MATCH_META_FILENAME


@dataclass(frozen=True, slots=True)
class ArchivedMarket:
    """One match archive and the Polymarket market it traded."""

    match_id: str
    archive_dir: Path
    condition_id: str
    yes_token_id: str
    no_token_id: str

    @property
    def tokens(self) -> frozenset[str]:
        """Both outcome tokens of this market."""
        return frozenset({self.yes_token_id, self.no_token_id})


def read_archived_market(archive_dir: Path) -> ArchivedMarket | None:
    """The market block of one match.json, or None when it is absent or unreadable."""
    try:
        loaded = json.loads((archive_dir / MATCH_META_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if type(loaded) is not dict:
        return None
    document = cast(dict[str, object], loaded)
    market_field = document.get("market")
    if type(market_field) is not dict:
        return None
    market = cast(dict[str, object], market_field)
    match_id = _nonempty_str(document.get("match_id"))
    condition_id = _nonempty_str(market.get("condition_id"))
    yes_token_id = _nonempty_str(market.get("yes_token_id"))
    no_token_id = _nonempty_str(market.get("no_token_id"))
    if match_id is None or condition_id is None or yes_token_id is None or no_token_id is None:
        return None
    return ArchivedMarket(match_id, archive_dir, condition_id, yes_token_id, no_token_id)


def _nonempty_str(value: object) -> str | None:
    """The value when it is a non-empty string, else None."""
    if type(value) is str and value:
        return value
    return None


def iter_archived_markets(roots: Sequence[Path]) -> list[ArchivedMarket]:
    """Every readable match archive under these roots, in directory-name order."""
    found: list[ArchivedMarket] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name == "wallet":
                continue
            market = read_archived_market(child)
            if market is not None:
                found.append(market)
    return found


class ArchivedMarketIndex:
    """token id -> the archive that traded it. Rescans on a miss so new matches resolve."""

    def __init__(self, roots: Sequence[Path]) -> None:
        self._roots = tuple(roots)
        self._by_token: dict[str, ArchivedMarket] = {}

    def find(self, token_id: str) -> ArchivedMarket | None:
        """The market holding this token, or None when no archive claims it."""
        hit = self._by_token.get(token_id)
        if hit is not None:
            return hit
        self._rescan()
        return self._by_token.get(token_id)

    def _rescan(self) -> None:
        for market in iter_archived_markets(self._roots):
            self._by_token[market.yes_token_id] = market
            self._by_token[market.no_token_id] = market
