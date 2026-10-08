"""Per-match metadata file: written at start, finalized at match end."""

import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from shared.constants.paths import TRADER_DIR
from shared.utils.log import get_logger
from trader.archive_paths import match_archive_dir
from trader.archive_types import (
    ArchiveOutcome,
    MatchArchiveSummary,
    MatchMeta,
    MatchMetaFinal,
    MatchMetaMarket,
    MatchMetaModel,
    MatchMetaPnl,
    MatchMetaTeams,
    MatchWinner,
    match_game,
)
from trader.bindings import (
    MarketReference,
    MatchStart,
    ModelReference,
    SessionPnl,
    TeamSides,
)
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import summarize as summarize_grid_archive
from trader.live_feed import FeedEvent, FeedSource, horn_is_pinnable, unix_seconds_to_iso_z
from trader.oddin_archive import summarize as summarize_oddin_archive
from trader.paths import MATCH_META_FILENAME
from trader.strict_json import (
    StrictJsonError,
    require_bool,
    require_exact_keys,
    require_int,
    require_nonempty_str,
    require_nullable_bool,
    require_nullable_int,
    require_nullable_nonempty_str,
    require_nullable_number,
    require_nullable_str,
    require_number,
    require_object,
    require_str,
)

logger = get_logger(__name__)

MATCH_META_SCHEMA_VERSION = 9
ACCEPTED_MATCH_META_SCHEMA_VERSIONS = frozenset({3, 4, 5, 6, 7, 8, 9})
_META_LABEL = "match.json"
_MATCH_META_KEYS_V3 = frozenset(
    {
        "schema_version",
        "match_id",
        "steam_match_id",
        "server_steam_id",
        "league_id",
        "tournament",
        "teams",
        "map_number",
        "joined_at_second",
        "joined_at_utc",
        "horn_at_utc",
        "market",
        "model",
        "feed_source",
        "steam_delay_s",
        "steam_observed_lag_s",
        "grid_delay_s",
        "final",
    }
)
_MATCH_META_KEYS_V4 = _MATCH_META_KEYS_V3 | {"kalshi"}
_MATCH_META_KEYS_V5 = _MATCH_META_KEYS_V4 | {"game"}
_MATCH_META_KEYS_V6 = _MATCH_META_KEYS_V3 | {"game"}
_MATCH_META_KEYS_V7 = _MATCH_META_KEYS_V6 | {
    "pgl_channel",
    "pgl_match_id",
    "pgl_delay_s",
    "pgl_sides_match_steam",
}
_MATCH_META_KEYS_V8 = _MATCH_META_KEYS_V6 | {
    "oddin_match_id",
    "oddin_delay_s",
}
_MATCH_META_KEYS_V9 = _MATCH_META_KEYS_V8 | {"record_only"}
# Schema 4/5 carried this Kalshi block. It is still validated on read, then dropped.
_LEGACY_KALSHI_KEYS = frozenset(
    {
        "event_ticker",
        "ticker",
        "series_ticker",
        "yes_outcome",
        "no_outcome",
        "yes_is_radiant",
        "price_level_structure",
        "tick_size",
        "reason",
    }
)
_LEGACY_KALSHI_REASONS = frozenset(
    {"pending", "matched", "none", "ambiguous", "off_grid", "cutoff", "error", "off"}
)
_TEAMS_KEYS = frozenset({"radiant", "dire"})
_MARKET_KEYS = frozenset(
    {
        "condition_id",
        "market_slug",
        "event_slug",
        "yes_token_id",
        "no_token_id",
        "yes_is_radiant",
        "outcome_0_name",
        "outcome_1_name",
        "tick_size",
        "min_order_size",
        "neg_risk",
        "grid_series_id",
    }
)
_MODEL_KEYS = frozenset({"name", "trained_at"})
_FINAL_KEYS = frozenset(
    {
        "duration_seconds",
        "winner",
        "pause_seconds",
        "missing_seconds",
        "snapshot_count",
        "pnl",
    }
)
_PNL_KEYS = frozenset({"realized_pnl_usdc", "unrealized_pnl_usdc"})


_SUMMARIZERS: dict[str, Callable[[Path, MatchMeta], ArchiveOutcome]] = {
    "grid": summarize_grid_archive,
    "oddin": summarize_oddin_archive,
}


@dataclass(frozen=True)
class FinalizedMatch:
    """The market identifiers the boot scan needs from a finalized match.json."""

    match_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True)
class MatchArchiveOwner:
    """The CID that owns this match.json, whether `final` is present, and a Steam pin.

    `pins_steam` marks a pre-removal Steam archive: no live feed can resume it.
    """

    match_id: str
    condition_id: str
    has_final: bool
    pins_steam: bool


@dataclass(frozen=True)
class MatchArchiveInspection:
    """Ownership read of match.json: absent, unreadable, or a known owner."""

    owner: MatchArchiveOwner | None
    unreadable: bool


def read_match_meta(meta_path: Path) -> MatchMeta:
    """Strictly parse one match.json; malformed or foreign-schema documents raise."""
    return _read_existing_meta(meta_path)


@dataclass(frozen=True)
class FeedPin:
    """The archive pin: feed source, plus the Oddin id when the file stores one."""

    source: FeedSource
    oddin_match_id: str | None


def read_feed_pin(match_id: str) -> FeedPin | None:
    """Return the pinned feed, or None when this build cannot read match.json.

    A missing file, an unreadable one, or a leftover foreign schema is None,
    not a raise: the picker then chooses a live source instead of treating the
    folder as a pin. Schema 3-7 have no Oddin id; that field is None. A legacy
    `steam` pin raises: WalletHost skips those archives before it launches.
    """
    meta_path = match_archive_dir(TRADER_DIR, match_id) / MATCH_META_FILENAME
    if not meta_path.exists():
        return None
    try:
        document = _read_existing_meta(meta_path)
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning("trader skip unusable match.json pin match=%s: %s", match_id, exc)
        return None
    return FeedPin(FeedSource(document["feed_source"]), document.get("oddin_match_id"))


def _reject_json_constant(value: str) -> object:
    """Reject nonstandard NaN/Infinity constants inside a match.json document."""
    raise ValueError(f"nonstandard JSON constant {value} in {MATCH_META_FILENAME}")


def read_finalized_match(match_root: Path, match_id: str) -> FinalizedMatch | None:
    """Read the boot-scan fields of a finalized schema 3-6 match.json, or None.

    Fail-soft on purpose: the supervisor asks this about arbitrary directories.
    Only the fields the boot scan acts on are required, so an unrelated field
    problem still lets leftover orders get cancelled. `read_feed_pin` is
    the strict reader; this one is deliberately narrow. Schema 4/5 `kalshi` and
    schema 5/6 `game` are not required here.
    """
    meta_path = match_archive_dir(match_root, match_id) / MATCH_META_FILENAME
    try:
        loaded: object = json.loads(
            meta_path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
        )
    except (OSError, ValueError, RecursionError):
        return None
    try:
        fields = require_object(loaded, _META_LABEL)
        schema_version = require_int(fields, "schema_version", _META_LABEL)
        market_label = f"{_META_LABEL} market"
        market = require_object(fields.get("market"), market_label)
        finalized = FinalizedMatch(
            match_id=require_nonempty_str(fields, "match_id", _META_LABEL),
            condition_id=require_nonempty_str(market, "condition_id", market_label),
            yes_token_id=require_nonempty_str(market, "yes_token_id", market_label),
            no_token_id=require_nonempty_str(market, "no_token_id", market_label),
        )
    except StrictJsonError:
        return None
    if schema_version not in ACCEPTED_MATCH_META_SCHEMA_VERSIONS:
        logger.info(
            "trader skip match.json with foreign schema match=%s schema=%s",
            match_id,
            schema_version,
        )
        return None
    if finalized.match_id != match_id or fields.get("final") is None:
        return None
    return finalized


def inspect_match_archive(match_root: Path, match_id: str) -> MatchArchiveInspection:
    """Read CID ownership of schema 3-6 match.json, including an unfinished start.

    Missing file: owner None, unreadable False. Unreadable, foreign schema, or
    match_id mismatch: owner None, unreadable True. Do not guess the owner.
    """
    meta_path = match_archive_dir(match_root, match_id) / MATCH_META_FILENAME
    if not meta_path.exists():
        return MatchArchiveInspection(owner=None, unreadable=False)
    try:
        loaded: object = json.loads(
            meta_path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
        )
        fields = require_object(loaded, _META_LABEL)
        schema_version = require_int(fields, "schema_version", _META_LABEL)
        market_label = f"{_META_LABEL} market"
        market = require_object(fields.get("market"), market_label)
        owner = MatchArchiveOwner(
            match_id=require_nonempty_str(fields, "match_id", _META_LABEL),
            condition_id=require_nonempty_str(market, "condition_id", market_label),
            has_final=fields.get("final") is not None,
            pins_steam=fields.get("feed_source") == "steam",
        )
    except (OSError, ValueError, RecursionError, StrictJsonError):
        return MatchArchiveInspection(owner=None, unreadable=True)
    if schema_version not in ACCEPTED_MATCH_META_SCHEMA_VERSIONS:
        return MatchArchiveInspection(owner=None, unreadable=True)
    if owner.match_id != match_id:
        return MatchArchiveInspection(owner=None, unreadable=True)
    return MatchArchiveInspection(owner=owner, unreadable=False)


def match_has_final(match_root: Path, match_id: str) -> bool:
    """True when this build owns match.json, its id matches, and `final` is non-null."""
    return read_finalized_match(match_root, match_id) is not None


def match_has_start(match_root: Path, match_id: str) -> bool:
    """True when match.json exists for this id (the first tick was archived)."""
    return (match_archive_dir(match_root, match_id) / MATCH_META_FILENAME).exists()


def write_match_start(start: MatchStart, first_event: FeedEvent) -> None:
    """Persist the start document; reuse an unfinished file only when bindings match."""
    archive_dir = match_archive_dir(TRADER_DIR, start.match_id)
    meta_path = archive_dir / MATCH_META_FILENAME
    if meta_path.exists():
        document = _read_existing_meta(meta_path)
        if document["final"] is not None:
            raise ValueError(
                f"match {start.match_id!r} already finalized; refusing to rewrite its start"
            )
        if document["feed_source"] != first_event.source:
            raise ValueError(
                f"existing start metadata for match {start.match_id!r} binds a different "
                "feed source; refusing to rebind"
            )
        existing = _match_start_from_document(document)
        # GRID labels can move after the first tick.
        resume_start = replace(start, sides=existing.sides)
        if existing != resume_start:
            changed = tuple(
                name
                for name in (
                    "match_id",
                    "steam_match_id",
                    "league_id",
                    "tournament",
                    "map_number",
                    "market",
                    "model",
                    "game",
                )
                if getattr(existing, name) != getattr(resume_start, name)
            )
            raise ValueError(
                f"existing start metadata for match {start.match_id!r} binds different "
                f"match/market/model fields {changed}; refusing to rebind"
            )
        if existing.record_only and not resume_start.record_only:
            # The first writer archived record-only; a trading resume clears the marker.
            # The reverse direction never writes: False already means trading intent.
            updated = cast(MatchMeta, dict(document))
            updated["record_only"] = False
            _atomic_write_json(archive_dir, updated)
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(archive_dir, _build_start_document(start, first_event))


def pin_horn_from_event(match_id: str, event: FeedEvent) -> None:
    """Set horn_at_utc from this tick when the phase uses the horn clock.

    No-op on pre-match ticks, a clock still at or below 0, a missing file,
    or an already-finalized archive.
    """
    if not horn_is_pinnable(event):
        return
    horn_iso = unix_seconds_to_iso_z(event.horn_unix_seconds)
    archive_dir = match_archive_dir(TRADER_DIR, match_id)
    meta_path = archive_dir / MATCH_META_FILENAME
    if not meta_path.exists():
        return
    document = _read_existing_meta(meta_path)
    if document["final"] is not None or document["horn_at_utc"] == horn_iso:
        return
    updated = cast(MatchMeta, dict(document))
    updated["horn_at_utc"] = horn_iso
    _atomic_write_json(archive_dir, updated)


def finalize_match(match_id: str, session_pnl: SessionPnl | None) -> None:
    """Compute the final block from the closed archive and atomically replace match.json."""
    archive_dir = match_archive_dir(TRADER_DIR, match_id)
    meta_path = archive_dir / MATCH_META_FILENAME
    document = _read_existing_meta(meta_path)
    summarizer = _SUMMARIZERS.get(document["feed_source"])
    if summarizer is None:
        raise ValueError(f"unsupported feed source {document['feed_source']!r}")
    outcome = summarizer(archive_dir, document)
    if not outcome.summary.finished:
        raise ValueError(
            f"archive {outcome.archive_path} does not end in a terminal snapshot; "
            "final block not written"
        )
    final_block = _build_final_block(outcome.summary, session_pnl)
    if document["final"] is not None and document["final"] != final_block:
        raise ValueError(f"match {match_id!r} already finalized with a different final block")
    if document["final"] is None:
        updated = cast(MatchMeta, dict(document))
        updated["final"] = final_block
        updated["grid_delay_s"] = outcome.grid_delay_s
        if outcome.trusted_horn is not None:
            updated["horn_at_utc"] = outcome.trusted_horn
        _atomic_write_json(archive_dir, updated)


def _build_start_document(start: MatchStart, first_event: FeedEvent) -> MatchMeta:
    """Build the root start document: binding fields plus join/horn stamps."""
    snapshot = first_event.snapshot
    return {
        "schema_version": MATCH_META_SCHEMA_VERSION,
        "match_id": start.match_id,
        "game": start.game,
        "steam_match_id": start.steam_match_id,
        "server_steam_id": None,
        "league_id": start.league_id,
        "tournament": start.tournament,
        "teams": _build_teams_block(start.sides),
        "map_number": start.map_number,
        "joined_at_second": snapshot.second,
        "joined_at_utc": first_event.received_at_utc,
        "horn_at_utc": unix_seconds_to_iso_z(first_event.horn_unix_seconds),
        "market": _build_market_block(start.market),
        "model": _build_model_block(start.model),
        "feed_source": first_event.source.value,
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": None,
        "oddin_match_id": start.oddin_match_id,
        "oddin_delay_s": start.oddin_delay_s if first_event.source is FeedSource.ODDIN else None,
        "record_only": start.record_only,
        "final": None,
    }


def _build_final_block(
    summary: MatchArchiveSummary,
    session_pnl: SessionPnl | None,
) -> MatchMetaFinal:
    """Build the final block from the archive summary and the session's PnL handoff."""
    return {
        "duration_seconds": summary.duration_seconds,
        "winner": summary.winner,
        "pause_seconds": summary.pause_seconds,
        "missing_seconds": summary.missing_seconds,
        "snapshot_count": summary.snapshot_count,
        "pnl": _build_pnl_block(session_pnl),
    }


def _build_pnl_block(session_pnl: SessionPnl | None) -> MatchMetaPnl | None:
    """Serialize the PnL handoff, or None when the session supplied no values."""
    if session_pnl is None:
        return None
    if not math.isfinite(session_pnl.realized_pnl_usdc):
        raise ValueError("realized_pnl_usdc must be a finite float")
    if not math.isfinite(session_pnl.unrealized_pnl_usdc):
        raise ValueError("unrealized_pnl_usdc must be a finite float")
    return {
        "realized_pnl_usdc": session_pnl.realized_pnl_usdc,
        "unrealized_pnl_usdc": session_pnl.unrealized_pnl_usdc,
    }


def _build_teams_block(sides: TeamSides) -> MatchMetaTeams:
    """Serialize the two team names."""
    return {"radiant": sides.radiant, "dire": sides.dire}


def _build_market_block(market: MarketReference) -> MatchMetaMarket:
    """Serialize the market reference; decimal fields stay strings, series id stays null."""
    return {
        "condition_id": market.condition_id,
        "market_slug": market.market_slug,
        "event_slug": market.event_slug,
        "yes_token_id": market.yes_token_id,
        "no_token_id": market.no_token_id,
        "yes_is_radiant": market.yes_is_radiant,
        "outcome_0_name": market.outcome_0_name,
        "outcome_1_name": market.outcome_1_name,
        "tick_size": market.tick_size,
        "min_order_size": market.min_order_size,
        "neg_risk": market.neg_risk,
        "grid_series_id": market.grid_series_id,
    }


def _build_model_block(model: ModelReference) -> MatchMetaModel:
    """Serialize the pinned model identity."""
    return {"name": model.name, "trained_at": model.trained_at}


def _match_start_from_document(document: MatchMeta) -> MatchStart:
    """Rebuild the start binding from a persisted match.json document."""
    teams = document["teams"]
    market = document["market"]
    model = document["model"]
    return MatchStart(
        match_id=document["match_id"],
        game=match_game(document),
        steam_match_id=document["steam_match_id"],
        league_id=document["league_id"],
        tournament=document["tournament"],
        sides=TeamSides(radiant=teams["radiant"], dire=teams["dire"]),
        map_number=document["map_number"],
        market=MarketReference(
            condition_id=market["condition_id"],
            market_slug=market["market_slug"],
            event_slug=market["event_slug"],
            yes_token_id=market["yes_token_id"],
            no_token_id=market["no_token_id"],
            yes_is_radiant=market["yes_is_radiant"],
            outcome_0_name=market["outcome_0_name"],
            outcome_1_name=market["outcome_1_name"],
            tick_size=market["tick_size"],
            min_order_size=market["min_order_size"],
            neg_risk=market["neg_risk"],
            grid_series_id=market["grid_series_id"],
        ),
        model=ModelReference(name=model["name"], trained_at=model["trained_at"]),
        oddin_match_id=document.get("oddin_match_id"),
        oddin_delay_s=document.get("oddin_delay_s"),
        record_only=document.get("record_only", False),
    )


def _read_existing_meta(meta_path: Path) -> MatchMeta:
    """Parse the persisted match.json; malformed or foreign-schema documents raise loudly.

    Field errors carry the generic `match.json` label, so this wrapper re-raises
    them with the path: the daemon runs many matches at once.
    """
    try:
        loaded: object = json.loads(
            meta_path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed metadata {meta_path}: {type(exc).__name__}") from exc
    try:
        return _parse_meta_document(loaded, meta_path)
    except StrictJsonError as exc:
        raise ValueError(f"invalid metadata {meta_path}: {exc}") from exc


def _match_meta_keys(schema_version: int) -> frozenset[str]:
    """Exact key set for one accepted match.json schema version."""
    return {
        3: _MATCH_META_KEYS_V3,
        4: _MATCH_META_KEYS_V4,
        5: _MATCH_META_KEYS_V5,
        6: _MATCH_META_KEYS_V6,
        7: _MATCH_META_KEYS_V7,
        8: _MATCH_META_KEYS_V8,
        9: _MATCH_META_KEYS_V9,
    }[schema_version]


def _apply_schema_7_extras(parsed: MatchMeta, fields: dict[str, object]) -> None:
    """Parse the leftover PGL bind fields of a schema 7 start."""
    parsed["pgl_channel"] = require_nullable_int(fields, "pgl_channel", _META_LABEL)
    parsed["pgl_match_id"] = require_nullable_nonempty_str(fields, "pgl_match_id", _META_LABEL)
    parsed["pgl_delay_s"] = require_nullable_int(fields, "pgl_delay_s", _META_LABEL)
    parsed["pgl_sides_match_steam"] = require_bool(fields, "pgl_sides_match_steam", _META_LABEL)


def _apply_schema_8_extras(parsed: MatchMeta, fields: dict[str, object]) -> None:
    """Parse the Oddin bind fields of a schema 8 start."""
    parsed["oddin_match_id"] = require_nullable_nonempty_str(fields, "oddin_match_id", _META_LABEL)
    parsed["oddin_delay_s"] = require_nullable_int(fields, "oddin_delay_s", _META_LABEL)


def _apply_schema_9_extras(parsed: MatchMeta, fields: dict[str, object]) -> None:
    """Parse the Oddin bind and the record-only marker of a schema 9 start."""
    _apply_schema_8_extras(parsed, fields)
    parsed["record_only"] = require_bool(fields, "record_only", _META_LABEL)


_SCHEMA_EXTRAS: dict[int, Callable[[MatchMeta, dict[str, object]], None]] = {
    7: _apply_schema_7_extras,
    8: _apply_schema_8_extras,
    9: _apply_schema_9_extras,
}


def _apply_schema_extras(parsed: MatchMeta, fields: dict[str, object], schema_version: int) -> None:
    """Fill version-specific fields after the shared v3 body is validated."""
    if schema_version in {4, 5}:
        _validate_legacy_kalshi(require_object(fields.get("kalshi"), f"{_META_LABEL} kalshi"))
    if schema_version in {5, 6, 7, 8, 9}:
        parsed["game"] = _parse_game(fields)
    apply_extras = _SCHEMA_EXTRAS.get(schema_version)
    if apply_extras is not None:
        apply_extras(parsed, fields)


def _parse_meta_document(loaded: object, meta_path: Path) -> MatchMeta:
    """Validate one decoded schema 3-9 match.json document field by field."""
    fields = require_object(loaded, _META_LABEL)
    schema_version = require_int(fields, "schema_version", _META_LABEL)
    if schema_version not in ACCEPTED_MATCH_META_SCHEMA_VERSIONS:
        raise ValueError(
            f"{meta_path} schema_version {schema_version} is not an accepted match.json schema"
        )
    require_exact_keys(fields, _match_meta_keys(schema_version), _META_LABEL)
    feed_source = require_str(fields, "feed_source", _META_LABEL)
    if feed_source != "steam" and feed_source != "grid" and feed_source != "oddin":
        raise StrictJsonError(f"{_META_LABEL} 'feed_source' must be 'steam', 'grid', or 'oddin'")
    parsed: MatchMeta = {
        "schema_version": schema_version,
        "match_id": require_nonempty_str(fields, "match_id", _META_LABEL),
        "steam_match_id": require_nullable_nonempty_str(fields, "steam_match_id", _META_LABEL),
        "server_steam_id": require_nullable_nonempty_str(fields, "server_steam_id", _META_LABEL),
        "league_id": require_nullable_int(fields, "league_id", _META_LABEL),
        "tournament": require_nullable_str(fields, "tournament", _META_LABEL),
        "teams": _parse_teams(require_object(fields.get("teams"), f"{_META_LABEL} teams")),
        "map_number": require_int(fields, "map_number", _META_LABEL),
        "joined_at_second": require_int(fields, "joined_at_second", _META_LABEL),
        "joined_at_utc": require_str(fields, "joined_at_utc", _META_LABEL),
        "horn_at_utc": require_str(fields, "horn_at_utc", _META_LABEL),
        "market": _parse_market(require_object(fields.get("market"), f"{_META_LABEL} market")),
        "model": _parse_model(require_object(fields.get("model"), f"{_META_LABEL} model")),
        "feed_source": feed_source,
        "steam_delay_s": require_nullable_int(fields, "steam_delay_s", _META_LABEL),
        "steam_observed_lag_s": require_nullable_number(
            fields, "steam_observed_lag_s", _META_LABEL
        ),
        "grid_delay_s": require_nullable_number(fields, "grid_delay_s", _META_LABEL),
        "final": _parse_final(fields.get("final")),
    }
    _apply_schema_extras(parsed, fields, schema_version)
    return parsed


def _validate_legacy_kalshi(fields: dict[str, object]) -> None:
    """Validate the schema 4/5 kalshi block and drop it; a broken block is corruption."""
    label = f"{_META_LABEL} kalshi"
    require_exact_keys(fields, _LEGACY_KALSHI_KEYS, label)
    reason = require_str(fields, "reason", label)
    if reason not in _LEGACY_KALSHI_REASONS:
        raise StrictJsonError(f"{label} 'reason' must be a kalshi meta reason")
    for key in ("event_ticker", "ticker", "series_ticker", "yes_outcome", "no_outcome"):
        require_nullable_nonempty_str(fields, key, label)
    require_nullable_bool(fields, "yes_is_radiant", label)
    require_nullable_nonempty_str(fields, "price_level_structure", label)
    require_nullable_nonempty_str(fields, "tick_size", label)


def _parse_game(fields: dict[str, object]) -> str:
    """Parse game as a nonempty GAME_PROFILES key; unknown values fail closed."""
    game = require_nonempty_str(fields, "game", _META_LABEL)
    if game not in GAME_PROFILES:
        raise StrictJsonError(f"{_META_LABEL} 'game' must be a GameProfile key")
    return game


def _parse_teams(fields: dict[str, object]) -> MatchMetaTeams:
    """Parse the two team names; both keys are required strings."""
    require_exact_keys(fields, _TEAMS_KEYS, f"{_META_LABEL} teams")
    return {
        "radiant": require_str(fields, "radiant", f"{_META_LABEL} teams"),
        "dire": require_str(fields, "dire", f"{_META_LABEL} teams"),
    }


def _parse_market(fields: dict[str, object]) -> MatchMetaMarket:
    """Parse the market block; decimals stay strings and grid_series_id may be null."""
    require_exact_keys(fields, _MARKET_KEYS, f"{_META_LABEL} market")
    return {
        "condition_id": require_str(fields, "condition_id", f"{_META_LABEL} market"),
        "market_slug": require_str(fields, "market_slug", f"{_META_LABEL} market"),
        "event_slug": require_str(fields, "event_slug", f"{_META_LABEL} market"),
        "yes_token_id": require_str(fields, "yes_token_id", f"{_META_LABEL} market"),
        "no_token_id": require_str(fields, "no_token_id", f"{_META_LABEL} market"),
        "yes_is_radiant": require_bool(fields, "yes_is_radiant", f"{_META_LABEL} market"),
        "outcome_0_name": require_nonempty_str(fields, "outcome_0_name", f"{_META_LABEL} market"),
        "outcome_1_name": require_nonempty_str(fields, "outcome_1_name", f"{_META_LABEL} market"),
        "tick_size": require_nullable_str(fields, "tick_size", f"{_META_LABEL} market"),
        "min_order_size": require_nullable_str(fields, "min_order_size", f"{_META_LABEL} market"),
        "neg_risk": require_bool(fields, "neg_risk", f"{_META_LABEL} market"),
        "grid_series_id": require_nullable_str(fields, "grid_series_id", f"{_META_LABEL} market"),
    }


def _parse_model(fields: dict[str, object]) -> MatchMetaModel:
    """Parse the pinned model identity."""
    require_exact_keys(fields, _MODEL_KEYS, f"{_META_LABEL} model")
    return {
        "name": require_str(fields, "name", f"{_META_LABEL} model"),
        "trained_at": require_str(fields, "trained_at", f"{_META_LABEL} model"),
    }


def _parse_winner(fields: dict[str, object], label: str) -> MatchWinner | None:
    """Parse `winner` as radiant, dire, or null."""
    value = fields.get("winner")
    if value is None:
        return None
    if value == "radiant":
        return "radiant"
    if value == "dire":
        return "dire"
    raise StrictJsonError(f"{label} 'winner' must be 'radiant', 'dire', or null")


def _parse_pnl(value: object) -> MatchMetaPnl | None:
    """Parse the PnL object, or None when the field is JSON null."""
    if value is None:
        return None
    fields = require_object(value, f"{_META_LABEL} final pnl")
    require_exact_keys(fields, _PNL_KEYS, f"{_META_LABEL} final pnl")
    return {
        "realized_pnl_usdc": require_number(
            fields, "realized_pnl_usdc", f"{_META_LABEL} final pnl"
        ),
        "unrealized_pnl_usdc": require_number(
            fields, "unrealized_pnl_usdc", f"{_META_LABEL} final pnl"
        ),
    }


def _parse_final(value: object) -> MatchMetaFinal | None:
    """Parse the final block, or None when the field is JSON null."""
    if value is None:
        return None
    fields = require_object(value, f"{_META_LABEL} final")
    require_exact_keys(fields, _FINAL_KEYS, f"{_META_LABEL} final")
    return {
        "duration_seconds": require_int(fields, "duration_seconds", f"{_META_LABEL} final"),
        "winner": _parse_winner(fields, f"{_META_LABEL} final"),
        "pause_seconds": require_nullable_int(fields, "pause_seconds", f"{_META_LABEL} final"),
        "missing_seconds": require_nullable_int(fields, "missing_seconds", f"{_META_LABEL} final"),
        "snapshot_count": require_int(fields, "snapshot_count", f"{_META_LABEL} final"),
        "pnl": _parse_pnl(fields.get("pnl")),
    }


def _atomic_write_json(archive_dir: Path, document: MatchMeta) -> None:
    """Serialize `document` to a temp sibling and atomically replace match.json."""
    target = archive_dir / MATCH_META_FILENAME
    temp = archive_dir / f".{MATCH_META_FILENAME}.tmp"
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    _sync_directory(archive_dir)


def _sync_directory(directory: Path) -> None:
    """Best-effort fsync of the directory so the rename itself is durable."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
