"""Project the collector sidecar into the session's market metadata and binding."""

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from polymaker.domain import MarketMeta, TokenMeta

from shared.utils.log import get_logger
from shared.utils.series_format import DECIDER_BEST_OF
from trader.archive_types import SessionSidecarBindingRecord
from trader.bindings import DiscoveredMatch
from trader.collector_sidecars import FreshSidecar, MarketKind, SidecarScan, scan_sidecars
from trader.session_types import SidecarBinding, SidecarMeta, TradingDisabled
from trader.strict_json import (
    StrictJsonError,
    require_bool,
    require_exact_keys,
    require_int,
    require_list,
    require_nonempty_str,
    require_nullable_int,
    require_nullable_str,
    require_object,
)

logger = get_logger(__name__)

SIDECAR_BINDING_SCHEMA_VERSION = 1
_BINDING_LABEL = "sidecar binding record"
_BINDING_OUTCOME_LABEL = "sidecar binding outcome"

_SIDECAR_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "condition_id",
        "market_slug",
        "event_slug",
        "event_id",
        "market_kind",
        "map_number",
        "outcomes",
        "neg_risk",
        "grid_series_id",
    }
)


def sidecar_binding(sidecar: FreshSidecar) -> SidecarBinding:
    """Project one frozen sidecar into the immutable session binding."""
    return SidecarBinding(
        condition_id=sidecar.condition_id,
        market_slug=sidecar.market_slug,
        event_slug=sidecar.event_slug,
        event_id=sidecar.event_id,
        market_kind=sidecar.market_kind,
        map_number=sidecar.map_number,
        outcome_0_name=sidecar.outcome_0_name,
        outcome_0_token=sidecar.outcome_0_token,
        outcome_1_name=sidecar.outcome_1_name,
        outcome_1_token=sidecar.outcome_1_token,
        neg_risk=sidecar.neg_risk,
        grid_series_id=sidecar.grid_series_id,
    )


def select_current_sidecar(scan: SidecarScan, discovered: DiscoveredMatch) -> FreshSidecar | None:
    """Return this match's sidecar, or None when it is absent or changed identity."""
    market = discovered.market
    for sidecar in scan.sidecars:
        if sidecar.condition_id != market.condition_id:
            continue
        if (
            sidecar.market_slug != market.market_slug
            or sidecar.event_slug != market.event_slug
            or sidecar.outcome_0_token != market.yes_token_id
            or sidecar.outcome_1_token != market.no_token_id
        ):
            logger.warning(
                "collector sidecar %s changed its binding; trading stays disabled",
                market.condition_id,
            )
            return None
        return sidecar
    return None


def _parse_positive_decimal(field: str, raw: str | None) -> float | None:
    """Parse one collector decimal string; None when unusable."""
    if raw is None:
        logger.warning("collector sidecar %s is null; trading disabled", field)
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        logger.warning("collector sidecar %s is not a decimal string; trading disabled", field)
        return None
    if not value.is_finite() or value <= 0:
        logger.warning(
            "collector sidecar %s is not a positive finite decimal; trading disabled", field
        )
        return None
    try:
        return float(value)
    except OverflowError:
        logger.warning("collector sidecar %s overflows a float; trading disabled", field)
        return None


def parse_tick_decimal(raw: str | None) -> float | None:
    """Parse a usable tick size: positive, finite and below one."""
    number = _parse_positive_decimal("tickSize", raw)
    if number is None:
        return None
    if number >= 1.0:
        logger.warning("collector sidecar tickSize must be below 1.0; trading disabled")
        return None
    return number


def parse_min_order_decimal(raw: str | None) -> float | None:
    """Parse a usable exchange min order size: positive and finite."""
    return _parse_positive_decimal("minOrderSize", raw)


def _build_market_meta(sidecar: FreshSidecar, tick: float, min_order: float) -> MarketMeta:
    """Build the fork MarketMeta from the sidecar; rewards/fees stay zero."""
    return MarketMeta(
        condition_id=sidecar.condition_id,
        question=sidecar.question or sidecar.market_slug,
        slug=sidecar.market_slug,
        tokens=(
            TokenMeta(sidecar.outcome_0_token, sidecar.outcome_0_name),
            TokenMeta(sidecar.outcome_1_token, sidecar.outcome_1_name),
        ),
        tick_size=tick,
        neg_risk=sidecar.neg_risk,
        min_order_size=min_order,
        rewards_min_size=0.0,
        rewards_max_spread=0.0,
        rewards_daily_rate=0.0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=None,
        event_id=sidecar.event_id,
    )


def _require_matching_map(discovered: DiscoveredMatch, sidecar: FreshSidecar) -> None:
    """Raise when the sidecar map does not match the discovered market."""
    if discovered.market_kind == "map_winner":
        if sidecar.map_number != discovered.map_number:
            raise TradingDisabled(
                "collector sidecar map number does not match the discovered market"
            )
    else:
        # Discovery already required current_map == best_of. This only rejects
        # map 2/4 (and sidecar mapNumber set); it cannot tell BO5-on-map-3 from
        # a BO3 decider because the handoff has no best_of.
        if discovered.map_number not in DECIDER_BEST_OF or sidecar.map_number is not None:
            raise TradingDisabled(
                "collector sidecar series market does not match the discovered decider"
            )


def build_sidecar_meta(
    discovered: DiscoveredMatch,
    archive_root: Path,
    persisted_binding: SidecarBinding | None,
) -> SidecarMeta:
    """Project the current collector sidecar into a fork MarketMeta, or fault.

    Kind, map and the persisted provenance binding must match before any Engine.
    """
    scan = scan_sidecars(archive_root, time.time())
    sidecar = select_current_sidecar(scan, discovered)
    if sidecar is None:
        raise TradingDisabled("collector sidecar unavailable")
    if not sidecar.is_tradeable():
        raise TradingDisabled("market is not tradeable")
    if discovered.market_kind is None:
        raise TradingDisabled("discovered market carries no market kind")
    if sidecar.market_kind != discovered.market_kind:
        raise TradingDisabled("collector sidecar market kind does not match the discovered market")
    _require_matching_map(discovered, sidecar)
    if sidecar.neg_risk != discovered.market.neg_risk:
        raise TradingDisabled("collector sidecar negRisk does not match the discovered market")
    if sidecar.grid_series_id != discovered.market.grid_series_id:
        raise TradingDisabled("collector sidecar gridSeriesId does not match the discovered market")
    tick = parse_tick_decimal(sidecar.tick_size)
    min_order = parse_min_order_decimal(sidecar.min_order_size)
    if tick is None or min_order is None:
        raise TradingDisabled("collector sidecar has no usable tick or min order size")
    assert sidecar.tick_size is not None  # guarded by the parse above
    current = sidecar_binding(sidecar)
    if persisted_binding is None:
        raise TradingDisabled("no pinned sidecar binding in the session provenance")
    if persisted_binding != current:
        raise TradingDisabled(
            "collector sidecar changed its immutable binding since the session provenance"
        )
    return SidecarMeta(_build_market_meta(sidecar, tick, min_order), sidecar.tick_size, current)


def sidecar_binding_record(binding: SidecarBinding) -> SessionSidecarBindingRecord:
    """Serialize one binding into the durable record shape (public ids only)."""
    return {
        "schema_version": SIDECAR_BINDING_SCHEMA_VERSION,
        "condition_id": binding.condition_id,
        "market_slug": binding.market_slug,
        "event_slug": binding.event_slug,
        "event_id": binding.event_id,
        "market_kind": binding.market_kind,
        "map_number": binding.map_number,
        "outcomes": [
            {"index": 0, "name": binding.outcome_0_name, "tokenId": binding.outcome_0_token},
            {"index": 1, "name": binding.outcome_1_name, "tokenId": binding.outcome_1_token},
        ],
        "neg_risk": binding.neg_risk,
        "grid_series_id": binding.grid_series_id,
    }


@dataclass(frozen=True)
class _OutcomeNames:
    """The two parsed outcome names/tokens in canonical index order."""

    name_0: str
    token_0: str
    name_1: str
    token_1: str


def parse_sidecar_binding(document: object) -> SidecarBinding:
    """Parse one persisted binding record; malformed records fail closed."""
    try:
        record = require_object(document, _BINDING_LABEL)
        require_exact_keys(record, _SIDECAR_BINDING_KEYS, _BINDING_LABEL)
        schema_version = require_int(record, "schema_version", _BINDING_LABEL)
        market_kind = require_nonempty_str(record, "market_kind", _BINDING_LABEL)
        map_number = require_nullable_int(record, "map_number", _BINDING_LABEL)
        outcomes = _parse_binding_outcomes(require_list(record, "outcomes", _BINDING_LABEL))
        binding = SidecarBinding(
            condition_id=require_nonempty_str(record, "condition_id", _BINDING_LABEL),
            market_slug=require_nonempty_str(record, "market_slug", _BINDING_LABEL),
            event_slug=require_nonempty_str(record, "event_slug", _BINDING_LABEL),
            event_id=require_nonempty_str(record, "event_id", _BINDING_LABEL),
            market_kind=cast(MarketKind, market_kind),
            map_number=map_number,
            outcome_0_name=outcomes.name_0,
            outcome_0_token=outcomes.token_0,
            outcome_1_name=outcomes.name_1,
            outcome_1_token=outcomes.token_1,
            neg_risk=require_bool(record, "neg_risk", _BINDING_LABEL),
            grid_series_id=require_nullable_str(record, "grid_series_id", _BINDING_LABEL),
        )
    except StrictJsonError as exc:
        raise TradingDisabled(f"{exc}") from None
    if schema_version != SIDECAR_BINDING_SCHEMA_VERSION:
        raise TradingDisabled("sidecar binding record has an unknown schema version")
    if market_kind not in ("map_winner", "series_winner"):
        raise TradingDisabled("sidecar binding record market_kind is malformed")
    if market_kind == "map_winner" and (map_number is None or map_number <= 0):
        raise TradingDisabled("sidecar binding record map_number is malformed")
    if market_kind == "series_winner" and map_number is not None:
        raise TradingDisabled("sidecar binding record map_number is malformed")
    return binding


def _parse_binding_outcomes(rows: list[object]) -> _OutcomeNames:
    """Parse the two persisted outcome blocks in canonical index-0/1 order."""
    if len(rows) != 2:
        raise StrictJsonError("sidecar binding record outcomes must be exactly two")
    names: list[str] = []
    tokens: list[str] = []
    for expected_index, raw in enumerate(rows):
        outcome = require_object(raw, _BINDING_OUTCOME_LABEL)
        if require_int(outcome, "index", _BINDING_OUTCOME_LABEL) != expected_index:
            raise StrictJsonError("sidecar binding record outcomes are out of order")
        names.append(require_nonempty_str(outcome, "name", _BINDING_OUTCOME_LABEL))
        tokens.append(require_nonempty_str(outcome, "tokenId", _BINDING_OUTCOME_LABEL))
    if tokens[0] == tokens[1] or names[0] == names[1]:
        raise StrictJsonError("sidecar binding record outcomes are not distinct")
    return _OutcomeNames(name_0=names[0], token_0=tokens[0], name_1=names[1], token_1=tokens[1])


def provenance_sidecar_preview(
    discovered: DiscoveredMatch, archive_root: Path
) -> FreshSidecar | None:
    """The sidecar present at the first feed event, for the provenance pin."""
    scan = scan_sidecars(archive_root, time.time())
    return select_current_sidecar(scan, discovered)
