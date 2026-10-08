"""Reconstruct capital from archived fills, reserves, and realized settlement cash."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import cast

import pandas as pd

from backtest.marks import EnrichedFill
from backtest.postprocess import read_fills_checkpoint
from backtest.quote_store import read_quote_telemetry
from backtest.report_types import SummaryPayload
from backtest.results import MakerMatchResult, read_results_checkpoint
from backtest.telemetry import QuoteEvent
from backtest.wallet_path import (
    LotKey,
    MergeCredit,
    ReserveClose,
    ReserveOpen,
    SettleEvent,
    build_reserve_events,
)
from shared.constants.lol import LOL_BACKTEST_AUDIT_PATH
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog


@dataclass(frozen=True)
class CapitalMetrics:
    """Minimum initial cash for fills alone and for fills plus working BUYs."""

    cash_fills: float
    deposit: float


def apply_capital_fill(
    fill: EnrichedFill,
    reserved: dict[str, float],
    open_quantity: dict[LotKey, float],
) -> float:
    """Update unfilled BUY reserves and remaining shares; return the fill's cash flow."""
    notional = fill.price * fill.quantity
    key = LotKey(fill.match_id, fill.token_index)
    if fill.side == "BUY":
        open_quantity[key] = open_quantity.get(key, 0.0) + fill.quantity
        if fill.order_id in reserved:
            reserved[fill.order_id] = max(0.0, reserved[fill.order_id] - notional)
        return -notional
    quantity = open_quantity.get(key, 0.0) - fill.quantity
    if quantity <= 1e-12:
        open_quantity.pop(key, None)
    else:
        open_quantity[key] = quantity
    return notional


def _release_pairs(open_quantity: dict[LotKey, float], event: MergeCredit) -> float:
    yes_key = LotKey(event.match_id, 0)
    no_key = LotKey(event.match_id, 1)
    available = min(
        event.shares,
        open_quantity.get(yes_key, 0.0),
        open_quantity.get(no_key, 0.0),
    )
    if available <= 1e-12:
        return 0.0
    for key in (yes_key, no_key):
        left = open_quantity[key] - available
        if left <= 1e-12:
            del open_quantity[key]
        else:
            open_quantity[key] = left
    return available


def calculate_report_capital(
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    quotes: Sequence[QuoteEvent],
    winning_tokens: Mapping[int, int],
) -> CapitalMetrics:
    """Rebuild cash on a selected map set using binary settlement of remaining shares.

    The wallet report samples fills-only cash after each timestamp; reserves are
    checked after each event, in the live reserve path's ordering. Rebates do not
    fund purchases in either calculation.
    """
    events = build_reserve_events(results, fills, quotes, settlement_at="game_end")
    cash = 0.0
    cash_fills = 0.0
    deposit = 0.0
    reserved: dict[str, float] = {}
    open_quantity: dict[LotKey, float] = {}
    for _, simultaneous in groupby(events, key=lambda event: event.ts_ns):
        for event in simultaneous:
            match event:
                case ReserveOpen():
                    reserved[event.order_id] = event.notional
                case ReserveClose():
                    reserved.pop(event.order_id, None)
                case EnrichedFill():
                    cash += apply_capital_fill(event, reserved, open_quantity)
                case MergeCredit():
                    cash += _release_pairs(open_quantity, event)
                case SettleEvent():
                    keys = [key for key in open_quantity if key.match_id == event.match_id]
                    for key in keys:
                        quantity = open_quantity.pop(key)
                        if quantity and key.token_index == winning_tokens[event.match_id]:
                            cash += quantity
            deposit = max(deposit, sum(reserved.values()) - cash)
        cash_fills = max(cash_fills, -cash)
    return CapitalMetrics(cash_fills=cash_fills, deposit=deposit)


def read_shared_capital(seed_dir: Path, shared_ids: frozenset[int]) -> CapitalMetrics:
    """Select every input to the same shared completed map universe before replaying cash."""
    completed = [
        result for result in read_results_checkpoint(seed_dir) if not result.terminated_early
    ]
    if frozenset(result.match_id for result in completed) == shared_ids:
        payload = cast(SummaryPayload, json.loads((seed_dir / "summary.json").read_text()))
        wallet = payload["arms"][0]["wallet"]
        return CapitalMetrics(wallet["required_cash"], wallet["required_cash_with_reserves"])
    quote_path = seed_dir / "quote_events.parquet"
    if not quote_path.is_file():
        raise ValueError(f"{quote_path} is required to calculate deposit w/ reserves")
    results = [result for result in completed if result.match_id in shared_ids]
    fills = [fill for fill in read_fills_checkpoint(seed_dir) if fill.match_id in shared_ids]
    telemetry = read_quote_telemetry(seed_dir)
    quotes = [
        event
        for event in (*telemetry.reserve_events, *telemetry.merge_events)
        if event.match_id in shared_ids
    ]
    winning_tokens = read_winning_tokens(seed_dir, frozenset(fill.match_id for fill in fills))
    return calculate_report_capital(results, fills, quotes, winning_tokens)


def read_winning_tokens(seed_dir: Path, match_ids: frozenset[int]) -> dict[int, int]:
    """Use the same local outcomes and token mapping as the replay for subset settlement."""
    manifest = json.loads((seed_dir / "manifest.json").read_text())
    winners: dict[int, int] = {}
    if manifest.get("game", "dota") == "lol":
        audit = pd.read_parquet(
            LOL_BACKTEST_AUDIT_PATH, columns=["match_id", "radiant_token_index", "radiant_win"]
        )
        selected = audit.loc[audit["match_id"].isin(match_ids)]
        for match_id, token, radiant_win in selected.itertuples(index=False, name=None):
            winners[int(match_id)] = int(token) if bool(radiant_win) else 1 - int(token)
    else:
        catalog = load_match_catalog(MATCH_CATALOG_PATH)
        for match_id in match_ids:
            entry = catalog[match_id]
            winners[match_id] = (
                entry.radiant_token_index if entry.radiant_win else 1 - entry.radiant_token_index
            )
    missing = match_ids - winners.keys()
    if missing:
        raise ValueError(f"local settlement outcomes missing for shared matches: {sorted(missing)}")
    return winners
