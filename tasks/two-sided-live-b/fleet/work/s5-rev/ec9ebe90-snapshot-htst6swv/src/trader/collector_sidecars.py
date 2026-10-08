"""Collector-v1 market sidecar scan: fresh, structurally valid frozen records.

The linker never sees JSON. One scandir of the archive's metadata/markets
yields frozen `FreshSidecar` values; malformed files are skipped and counted.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shared.utils.environment import env_value
from shared.utils.log import get_logger
from trader.game_profile import GameProfile
from trader.strict_json import (
    StrictJsonError,
    require_bool,
    require_int,
    require_list,
    require_nonempty_str,
    require_nullable_bool,
    require_nullable_nonempty_str,
    require_nullable_str,
    require_object,
)

logger = get_logger(__name__)

_FIELD = "field"
_OUTCOME_FIELD = "outcome field"

SIDECAR_MAX_AGE_SECONDS = 2 * 60 * 60
SIDECAR_SCHEMA_VERSION = 1
MarketKind = Literal["map_winner", "series_winner"]


@dataclass(frozen=True)
class _OutcomePair:
    """The two validated sidecar outcomes in canonical index order."""

    name_0: str
    token_0: str
    name_1: str
    token_1: str


@dataclass(frozen=True)
class FreshSidecar:
    """One structurally valid collector-v1 sidecar; trade flags are not yet applied."""

    event_id: str
    event_slug: str
    condition_id: str
    market_slug: str
    question: str | None
    market_kind: MarketKind
    map_number: int | None
    outcome_0_name: str
    outcome_0_token: str
    outcome_1_name: str
    outcome_1_token: str
    active: bool
    closed: bool
    accepting_orders: bool | None
    enable_order_book: bool
    tick_size: str | None
    min_order_size: str | None
    neg_risk: bool
    grid_series_id: str | None
    event_title: str | None

    def is_tradeable(self) -> bool:
        """Apply the trade flags by identity: only exact true/false values trade."""
        return (
            self.active is True
            and self.closed is False
            and self.accepting_orders is True
            and self.enable_order_book is True
        )


@dataclass(frozen=True)
class SidecarScan:
    """One markets-directory scan: the valid sidecars and the invalid-file count."""

    sidecars: tuple[FreshSidecar, ...]
    invalid_count: int


def load_archive_root(profile: GameProfile) -> Path:
    """Read `profile.archive_root_env` and return it as a Path; blank or missing raises."""
    value = env_value(profile.archive_root_env)
    if not value or not value.strip():
        raise RuntimeError(f"{profile.archive_root_env} is not set (env or .env)")
    return Path(value)


def scan_sidecars(archive_root: Path, now_epoch: float) -> SidecarScan:
    """Scan the markets directory once: fresh valid sidecars plus the invalid count."""
    markets_dir = archive_root / "metadata" / "markets"
    try:
        entries_context = os.scandir(markets_dir)
    except OSError as exc:
        logger.warning("markets directory %s unavailable: %s", markets_dir, type(exc).__name__)
        return SidecarScan((), 0)
    sidecars: list[FreshSidecar] = []
    invalid_count = 0
    with entries_context as entries:
        for entry in sorted(entries, key=lambda entry: entry.name):
            try:
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                mtime = entry.stat(follow_symlinks=False).st_mtime
            except OSError:
                # The collector's atomic replace can race scandir: a vanished
                # entry is a skipped file, not a daemon crash.
                logger.debug("sidecar %s vanished during scan", entry.name)
                continue
            # ponytail: предфильтр по mtime. Потолок — рынок, который коллектор не трогал
            # 2 часа, но который ещё торгуется. Если такое встретится, читать весь каталог.
            if mtime < now_epoch - SIDECAR_MAX_AGE_SECONDS:
                continue
            sidecar = _read_sidecar_file(Path(entry.path))
            if sidecar is None:
                invalid_count += 1
                continue
            sidecars.append(sidecar)
    sidecars.sort(key=lambda sidecar: sidecar.condition_id)
    return SidecarScan(tuple(sidecars), invalid_count)


def _read_sidecar_file(path: Path) -> FreshSidecar | None:
    """Decode and validate one sidecar file; None on any read/validation failure."""
    try:
        raw = path.read_bytes()
    except OSError:
        logger.debug("sidecar %s vanished during scan", path.name)
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("sidecar %s skipped: invalid utf-8", path.name)
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("sidecar %s skipped: invalid json", path.name)
        return None
    try:
        return _validate_sidecar(path.name, document)
    except StrictJsonError as exc:
        logger.warning("sidecar %s skipped: %s", path.name, exc)
        return None


def _validate_sidecar(name: str, document: object) -> FreshSidecar:
    """Validate one decoded sidecar against the collector-v1 contract."""
    sidecar = require_object(document, "sidecar document")
    schema_version = require_int(sidecar, "schemaVersion", _FIELD)
    if schema_version != SIDECAR_SCHEMA_VERSION:
        raise StrictJsonError(f"unsupported schema version {schema_version}")
    condition_id = require_nonempty_str(sidecar, "conditionId", _FIELD)
    if name != f"{condition_id}.json":
        raise StrictJsonError("file name must be <conditionId>.json")
    market_kind = sidecar.get("marketKind")
    if market_kind not in ("map_winner", "series_winner"):
        raise StrictJsonError(f"unknown marketKind {market_kind!r}")
    map_number = _validate_map_number(sidecar, market_kind)
    question = require_nullable_nonempty_str(sidecar, "question", _FIELD)
    outcomes = _validate_outcomes(require_list(sidecar, "outcomes", _FIELD))
    return FreshSidecar(
        event_id=require_nonempty_str(sidecar, "eventId", _FIELD),
        event_slug=require_nonempty_str(sidecar, "eventSlug", _FIELD),
        condition_id=condition_id,
        market_slug=require_nonempty_str(sidecar, "marketSlug", _FIELD),
        question=question.strip() if question is not None else None,
        market_kind=market_kind,
        map_number=map_number,
        outcome_0_name=outcomes.name_0,
        outcome_0_token=outcomes.token_0,
        outcome_1_name=outcomes.name_1,
        outcome_1_token=outcomes.token_1,
        active=require_bool(sidecar, "active", _FIELD),
        closed=require_bool(sidecar, "closed", _FIELD),
        accepting_orders=require_nullable_bool(sidecar, "acceptingOrders", _FIELD),
        enable_order_book=require_bool(sidecar, "enableOrderBook", _FIELD),
        tick_size=require_nullable_str(sidecar, "tickSize", _FIELD),
        min_order_size=require_nullable_str(sidecar, "minOrderSize", _FIELD),
        neg_risk=require_bool(sidecar, "negRisk", _FIELD),
        grid_series_id=require_nullable_str(sidecar, "gridSeriesId", _FIELD),
        event_title=require_nullable_str(sidecar, "eventTitle", _FIELD),
    )


def _validate_map_number(sidecar: Mapping[str, object], market_kind: MarketKind) -> int | None:
    """A map_winner needs a positive integer mapNumber; a series_winner needs null."""
    if market_kind == "series_winner":
        if sidecar.get("mapNumber") is not None:
            raise StrictJsonError("series_winner needs a null mapNumber")
        return None
    map_number = require_int(sidecar, "mapNumber", _FIELD)
    if map_number <= 0:
        raise StrictJsonError("map_winner needs a positive integer mapNumber")
    return map_number


def _validate_outcomes(rows: list[object]) -> _OutcomePair:
    """Validate the two sidecar outcomes in canonical index-0/1 order."""
    if len(rows) != 2:
        raise StrictJsonError("outcomes must be exactly two")
    names: list[str] = []
    tokens: list[str] = []
    for expected_index, raw in enumerate(rows):
        outcome = require_object(raw, "sidecar outcome")
        if require_int(outcome, "index", _OUTCOME_FIELD) != expected_index:
            raise StrictJsonError("outcomes must carry indexes 0, 1 in order")
        names.append(require_nonempty_str(outcome, "name", _OUTCOME_FIELD))
        tokens.append(require_nonempty_str(outcome, "tokenId", _OUTCOME_FIELD))
    if tokens[0] == tokens[1]:
        raise StrictJsonError("outcome tokens must be distinct")
    if names[0] == names[1]:
        raise StrictJsonError("outcome names must be distinct")
    return _OutcomePair(name_0=names[0], token_0=tokens[0], name_1=names[1], token_1=tokens[1])
