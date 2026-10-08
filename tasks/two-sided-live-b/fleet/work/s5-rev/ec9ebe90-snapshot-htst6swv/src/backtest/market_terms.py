"""Per-match fee terms. The backtest fallback is sports_fees_v3; this file overrides it."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarketFee:
    """One market's taker fee rate and maker rebate rate."""

    fee_rate: float
    rebate_rate: float
    fee_type: str


def load_market_fees(path: Path) -> dict[int, MarketFee]:
    """Read match-id keyed fee terms. Missing file is the caller's problem."""
    payload = json.loads(path.read_text())
    fees: dict[int, MarketFee] = {}
    for match_id, row in payload.items():
        fees[int(match_id)] = MarketFee(
            fee_rate=float(row["fee_rate"]),
            rebate_rate=float(row["rebate_rate"]),
            fee_type=str(row["fee_type"]),
        )
    return fees
