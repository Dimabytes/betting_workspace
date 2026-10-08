"""Live trader archive lookups for the backtest inspector."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog
from shared.utils.parsing import parse_steam_match_id


def _as_dict(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


@dataclass(frozen=True)
class LiveMatchIndexes:
    """slug / condition_id to numeric match id, both from the Dota catalog."""

    slug_to_match_id: dict[str, int]
    condition_to_match_id: dict[str, int]


def build_dota_live_indexes() -> LiveMatchIndexes:
    """Index the Dota catalog by gamma slug and by condition id."""
    catalog = load_match_catalog(MATCH_CATALOG_PATH)
    slug_to_match_id: dict[str, int] = {}
    condition_to_match_id: dict[str, int] = {}
    for match_id, entry in catalog.items():
        slug = entry.gamma.slug
        if slug and slug not in slug_to_match_id:
            slug_to_match_id[slug] = int(match_id)
        condition_id = entry.condition_id
        if condition_id and condition_id not in condition_to_match_id:
            condition_to_match_id[condition_id] = int(match_id)
    return LiveMatchIndexes(
        slug_to_match_id=slug_to_match_id, condition_to_match_id=condition_to_match_id
    )


def load_match_json(archive_dir: Path) -> dict[str, Any]:
    path = archive_dir / "match.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing match.json: {archive_dir}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"match.json must be an object: {path}")
    return cast(dict[str, Any], payload)


def _market_slug(meta: Mapping[str, Any]) -> str | None:
    market = _as_dict(meta.get("market"))
    if market is None:
        return None
    slug = market.get("market_slug")
    if isinstance(slug, str) and slug:
        return slug
    return None


def _condition_id(meta: Mapping[str, Any]) -> str | None:
    market = _as_dict(meta.get("market"))
    if market is None:
        return None
    condition = market.get("condition_id")
    if isinstance(condition, str) and condition:
        return condition
    return None


def resolve_archive_match_id(
    archive_dir: Path,
    *,
    slug_to_match_id: Mapping[str, int],
    condition_to_match_id: Mapping[str, int],
) -> int:
    """Prefer steam_match_id; else catalog/audit slug or condition_id."""
    meta = load_match_json(archive_dir)
    steam = parse_steam_match_id(meta.get("steam_match_id"))
    if steam is not None:
        return steam
    slug = _market_slug(meta)
    if slug is not None and slug in slug_to_match_id:
        return int(slug_to_match_id[slug])
    condition = _condition_id(meta)
    if condition is not None and condition in condition_to_match_id:
        return int(condition_to_match_id[condition])
    raise ValueError(
        f"archive has no steam_match_id and market_slug/condition_id not in catalog: {archive_dir}"
    )


def read_session_live_equity(archive_dir: Path) -> float | None:
    """Final session_end equity from session.jsonl, or None."""
    session = archive_dir / "session.jsonl"
    if not session.is_file():
        return None
    equity: float | None = None
    with session.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "session_end":
                raw = row.get("equity")
                if raw is not None:
                    equity = float(raw)
    return equity


def live_equity_by_match_ids(
    match_ids: Sequence[int],
    *,
    trader_dir: Path,
    indexes: LiveMatchIndexes,
) -> dict[int, float]:
    """Map backtest match ids to live archive equity when an archive exists."""
    wanted = {int(mid) for mid in match_ids}
    out: dict[int, float] = {}
    for path in sorted(trader_dir.glob("grid-*")):
        if not path.is_dir() or not (path / "match.json").is_file():
            continue
        if not (path / "session.jsonl").is_file():
            continue
        try:
            mid = resolve_archive_match_id(
                path,
                slug_to_match_id=indexes.slug_to_match_id,
                condition_to_match_id=indexes.condition_to_match_id,
            )
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if mid not in wanted or mid in out:
            continue
        equity = read_session_live_equity(path)
        if equity is not None:
            out[mid] = equity
        if len(out) >= len(wanted):
            break
    return out
