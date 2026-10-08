"""Audit archived Gamma trading terms against the sports maker assumptions.

Run before progon 3 on new matches. Exit 1 when any validation market mismatches.

Invocation:
  PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest \\
    python scripts/check_gamma_trading_terms.py
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
from polymarket.models.gamma.market import Market

from backtest.selection import load_validation_match_ids
from shared.constants.paths import (
    MATCH_CATALOG_PATH,
    POLYMARKET_UNIVERSE_EVENTS_DIR,
    RESEARCH_MODEL_SPLIT_PATH,
)
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_catalog import MatchCatalog, load_match_catalog
from shared.utils.polymarket import read_polymarket_universe_page

logger = get_logger(__name__)


@dataclass(frozen=True)
class TradingTerms:
    """Archived fee/size/delay fields the maker path assumes for latency and rebates."""

    minimum_order_size: Decimal | None
    seconds_delay: int | None
    fees_enabled: bool | None
    fee_type: str | None
    fee_rate: Decimal | None
    fee_exponent: Decimal | None
    rebate_rate: Decimal | None
    taker_only: bool | None


EXPECTED_TERMS = TradingTerms(
    minimum_order_size=Decimal("5"),
    seconds_delay=1,
    fees_enabled=True,
    fee_type="sports_fees_v3",
    fee_rate=Decimal("0.05"),
    fee_exponent=Decimal("1"),
    rebate_rate=Decimal("0.15"),
    taker_only=True,
)


@dataclass(frozen=True)
class SplitReport:
    """Counts for one train/validation split against EXPECTED_TERMS."""

    split: str
    matches: int
    expected: int
    mismatched: int


def extract_trading_terms(payload: Market) -> TradingTerms:
    """Read trading fee/size/delay fields from one SDK Market, or Nones when absent."""
    trading = payload.trading
    schedule = trading.fee_schedule
    if schedule is None:
        return TradingTerms(
            minimum_order_size=trading.minimum_order_size,
            seconds_delay=trading.seconds_delay,
            fees_enabled=trading.fees_enabled,
            fee_type=trading.fee_type,
            fee_rate=None,
            fee_exponent=None,
            rebate_rate=None,
            taker_only=None,
        )
    return TradingTerms(
        minimum_order_size=trading.minimum_order_size,
        seconds_delay=trading.seconds_delay,
        fees_enabled=trading.fees_enabled,
        fee_type=trading.fee_type,
        fee_rate=schedule.rate,
        fee_exponent=Decimal(str(schedule.exponent)),
        rebate_rate=schedule.rebate_rate,
        taker_only=schedule.taker_only,
    )


def load_trading_terms_by_condition_id(universe_dir: Path) -> dict[str, TradingTerms]:
    """Index archived Gamma markets by condition id to their trading terms."""
    terms_by_condition: dict[str, TradingTerms] = {}
    for path in sorted(universe_dir.glob("*/*.json.gz")):
        page = read_polymarket_universe_page(path)
        for event in page.events:
            for payload in event.markets:
                condition_id = payload.condition_id
                if not condition_id:
                    continue
                terms_by_condition[condition_id] = extract_trading_terms(payload)
    return terms_by_condition


def load_train_match_ids(split_path: Path) -> tuple[int, ...]:
    """Train match ids from the model split, in chronological order."""
    split = pd.read_parquet(split_path)
    train = split[split["split"] == "train"].sort_values(
        ["start_time", "match_id"], ignore_index=True
    )
    return tuple(int(match_id) for match_id in train["match_id"])


def score_split(
    split_name: str,
    match_ids: tuple[int, ...],
    catalog: MatchCatalog,
    terms_by_condition: dict[str, TradingTerms],
) -> tuple[SplitReport, Counter[TradingTerms]]:
    """Count expected vs mismatched markets for one split and collect observed terms."""
    observed: Counter[TradingTerms] = Counter()
    expected = 0
    mismatched = 0
    for match_id in match_ids:
        row = catalog.get(match_id)
        if row is None:
            mismatched += 1
            continue
        terms = terms_by_condition.get(row.condition_id)
        if terms is None:
            mismatched += 1
            continue
        observed[terms] += 1
        if terms == EXPECTED_TERMS:
            expected += 1
        else:
            mismatched += 1
    report = SplitReport(
        split=split_name,
        matches=len(match_ids),
        expected=expected,
        mismatched=mismatched,
    )
    return report, observed


def format_terms(terms: TradingTerms) -> str:
    """Render one TradingTerms tuple for the distinct-observed report line."""
    return (
        f"size={terms.minimum_order_size} delay={terms.seconds_delay} "
        f"fees_enabled={terms.fees_enabled} fee_type={terms.fee_type} "
        f"rate={terms.fee_rate} exponent={terms.fee_exponent} "
        f"rebate={terms.rebate_rate} taker_only={terms.taker_only}"
    )


def main() -> int:
    """Audit train/validation markets and exit 1 on any validation mismatch."""
    setup_logging()
    catalog = load_match_catalog(MATCH_CATALOG_PATH)
    terms_by_condition = load_trading_terms_by_condition_id(POLYMARKET_UNIVERSE_EVENTS_DIR)
    train_ids = load_train_match_ids(RESEARCH_MODEL_SPLIT_PATH)
    validation_ids = load_validation_match_ids()

    train_report, train_observed = score_split("train", train_ids, catalog, terms_by_condition)
    validation_report, validation_observed = score_split(
        "validation", validation_ids, catalog, terms_by_condition
    )
    observed = train_observed + validation_observed

    for report in (train_report, validation_report):
        logger.info(
            "%s: %s matches, %s expected, %s mismatched",
            report.split,
            report.matches,
            report.expected,
            report.mismatched,
        )
    logger.info("distinct observed term tuples (%s):", len(observed))
    for terms, count in sorted(
        observed.items(), key=lambda item: (-item[1], format_terms(item[0]))
    ):
        logger.info("  %s x %s", count, format_terms(terms))

    if validation_report.mismatched > 0:
        logger.error(
            "validation has %s mismatched markets; refuse to trust fee constants",
            validation_report.mismatched,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
