"""Score-gap experiment (arXiv:2607.06166): does the model beat the market under proper scores?

Fundamental: S(model; radiant_win) - S(market; radiant_win) for Brier and log.
Momentum (their 3.3.5 variant, matches the 300s-mid target): same gaps vs the
realized future midpoint, where the baseline forecast is the current mid itself.
Dota rows also carry decision bid/ask, so we price the Brier proper bet
s = 4*|p-q| shares at the ask and decompose profit = score gap + Bregman - slippage.
"""

import numpy as np
import pandas as pd

from shared.constants.dataset import TRAIN_LAG_SECONDS
from shared.constants.lol import (
    LOL_GAME_FEATURES_PATH,
    LOL_RESEARCH_MODEL_DIR,
    LOL_VALIDATION_PATH,
)
from shared.constants.paths import (
    GAME_FEATURES_DATASET_PATH,
    RESEARCH_MODEL_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.dota_features import (
    DOTA_XP_FEATURE_COLUMNS,
    GRID_HISTORY_POLICY,
    attach_catalog_features,
)
from shared.utils.gbm import load_predictor, predict_future_prices
from train_model.train_model import lagged_source_features, select_validation_prediction_frame

EPS = 0.001
DELTA_SLICES = [
    (0.0, 0.02, "<0.02"),
    (0.02, 0.05, "0.02-0.05"),
    (0.05, 0.10, "0.05-0.10"),
    (0.10, 1.01, ">=0.10"),
]
PRICE_SLICES = [(0.0, 0.35, "<0.35"), (0.35, 0.85, "0.35-0.85"), (0.85, 1.01, ">0.85")]
SECOND_SLICES = [
    (-60, 0, "-60..0"),
    (0, 120, "0..120"),
    (120, 300, "120..300"),
    (300, 600, "300..600"),
]


def load_dota_frame() -> pd.DataFrame:
    # Full read: VALIDATION_COLUMNS drops the decision bid/ask needed for slippage.
    rows = select_validation_prediction_frame(pd.read_parquet(VALIDATION_DATASET_PATH))
    predictor = load_predictor(RESEARCH_MODEL_DIR)
    tape = pd.read_parquet(GAME_FEATURES_DATASET_PATH)
    enriched = attach_catalog_features(
        rows,
        tape,
        key_seconds=rows["second"] - TRAIN_LAG_SECONDS,
        start_second=GRID_HISTORY_POLICY.start_second,
        board_tape=tape,
    )
    features = lagged_source_features(enriched, TRAIN_LAG_SECONDS, DOTA_XP_FEATURE_COLUMNS)
    rows = rows.copy()
    rows["model_p"] = predict_future_prices(
        predictor, features, rows["market_p_radiant"].to_numpy(dtype=np.float64)
    )
    return rows


def load_lol_frame() -> pd.DataFrame:
    rows = pd.read_parquet(LOL_VALIDATION_PATH)
    rows = rows[(rows["second"] >= 0) & (rows["second"] <= 540)]
    predictor = load_predictor(LOL_RESEARCH_MODEL_DIR)
    tape = pd.read_parquet(LOL_GAME_FEATURES_PATH)
    enriched = attach_catalog_features(
        rows,
        tape,
        key_seconds=rows["second"],
        start_second=GRID_HISTORY_POLICY.start_second,
        board_tape=tape,
    )
    rows = rows.copy()
    rows["model_p"] = predict_future_prices(
        predictor,
        enriched[list(DOTA_XP_FEATURE_COLUMNS)],
        rows["market_p_radiant"].to_numpy(dtype=np.float64),
    )
    return rows


def add_score_gaps(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    p = out["model_p"].clip(EPS, 1 - EPS)
    q = out["market_p_radiant"].clip(EPS, 1 - EPS)
    y = out["radiant_win"].astype(float)
    # Vector-form scores on the probability simplex, matching the paper's S.
    out["brier_gap_outcome"] = ((q - y) ** 2 - (p - y) ** 2) * 2
    out["log_gap_outcome"] = y * np.log(p / q) + (1 - y) * np.log((1 - p) / (1 - q))
    future = out["signal_market_p_radiant_300s"]
    has_future = future.notna()
    out["brier_gap_momentum"] = np.where(
        has_future, ((q - future) ** 2 - (p - future) ** 2) * 2, np.nan
    )
    out["log_gap_momentum"] = np.where(
        has_future,
        future * np.log(p / q) + (1 - future) * np.log((1 - p) / (1 - q)),
        np.nan,
    )
    out["abs_delta"] = (p - q).abs()
    out["bregman_brier"] = (p - q) ** 2 * 2
    return out


def add_dota_liquidity(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    radiant_side = out["model_p"] >= out["market_p_radiant"]
    exec_price = np.where(radiant_side, out["radiant_decision_ask"], out["dire_decision_ask"])
    mid_side = np.where(radiant_side, out["market_p_radiant"], 1 - out["market_p_radiant"])
    valid_quote = ~pd.isna(exec_price)
    out["exec_price"] = np.where(valid_quote, exec_price, np.nan)
    out["half_spread"] = np.where(valid_quote, exec_price - mid_side, np.nan)
    # Brier proper bet: |s1|+|s2| = 4*|p-q| shares, all on the favored token.
    out["proper_shares"] = out["abs_delta"] * 4
    out["liquidity_loss"] = out["proper_shares"] * out["half_spread"]
    out["passes_liquidity_gate"] = out["bregman_brier"] >= out["liquidity_loss"]
    y = out["radiant_win"].astype(float)
    side_payoff = np.where(radiant_side, y, 1 - y)
    out["proper_profit"] = out["proper_shares"] * (side_payoff - out["exec_price"])
    return out


def match_ci(frame: pd.DataFrame, column: str) -> tuple[float, float, int]:
    """Mean of per-match means +- 1.96 SE; rows inside a match are correlated."""
    per_match = frame.groupby("match_id")[column].mean().dropna()
    if len(per_match) < 2:
        return float("nan"), float("nan"), len(per_match)
    se = float(per_match.std(ddof=1)) / np.sqrt(len(per_match))
    return float(per_match.mean()), 1.96 * se, len(per_match)


def report_table(frame: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} | rows={len(frame)} matches={frame['match_id'].nunique()}")
    print(
        f"max |model-market| = {frame['abs_delta'].max():.4f} | "
        f"rows |d|>=0.02: {(frame['abs_delta'] >= 0.02).mean():.1%} | "
        f"|d|>=0.05: {(frame['abs_delta'] >= 0.05).mean():.1%}"
    )
    header = f"{'slice':<14}{'rows':>9} {'brier_gap_out':>14} {'log_gap_out':>12} {'brier_gap_mom':>14} {'log_gap_mom':>12}"
    print(header)
    print("-" * len(header))

    def emit(name: str, sub: pd.DataFrame) -> None:
        bo, _, _ = match_ci(sub, "brier_gap_outcome")
        lo, _, _ = match_ci(sub, "log_gap_outcome")
        bm, _, _ = match_ci(sub, "brier_gap_momentum")
        lm, _, _ = match_ci(sub, "log_gap_momentum")
        print(f"{name:<14}{len(sub):>9} {bo:>14.4f} {lo:>12.4f} {bm:>14.4f} {lm:>12.4f}")

    emit("ALL", frame)
    for lo, hi, name in DELTA_SLICES:
        emit(f"|d| {name}", frame[(frame["abs_delta"] >= lo) & (frame["abs_delta"] < hi)])
    for lo, hi, name in PRICE_SLICES:
        emit(
            f"q {name}",
            frame[(frame["market_p_radiant"] >= lo) & (frame["market_p_radiant"] < hi)],
        )
    for lo, hi, name in SECOND_SLICES:
        emit(f"sec {name}", frame[(frame["second"] >= lo) & (frame["second"] < hi)])


def report_liquidity(frame: pd.DataFrame) -> None:
    q = frame.dropna(subset=["half_spread", "proper_profit"])
    print(f"\n=== Dota liquidity | quoted rows={len(q)}")
    print(
        f"half_spread cents: median={q['half_spread'].median() * 100:.2f} "
        f"p90={q['half_spread'].quantile(0.9) * 100:.2f}"
    )
    for name, sub in (
        ("ALL", q),
        ("|d|>=0.02", q[q["abs_delta"] >= 0.02]),
        ("|d|>=0.05", q[q["abs_delta"] >= 0.05]),
    ):
        if not len(sub):
            continue
        share = sub["passes_liquidity_gate"].mean()
        profit, profit_se, n_matches = match_ci(sub, "proper_profit")
        gap, _, _ = match_ci(sub, "brier_gap_outcome")
        breg, _, _ = match_ci(sub, "bregman_brier")
        loss, _, _ = match_ci(sub, "liquidity_loss")
        print(
            f"{name:<10} rows={len(sub):>7} gate_pass={share:>6.1%} | "
            f"profit={profit:>8.4f}+-{profit_se:.4f} (matches={n_matches}) | "
            f"gap={gap:>7.4f} bregman={breg:>7.4f} slippage={loss:>7.4f}"
        )


def main() -> None:
    dota = add_dota_liquidity(add_score_gaps(load_dota_frame()))
    report_table(dota, "DOTA research model, validation split")
    report_liquidity(dota)
    lol = add_score_gaps(load_lol_frame())
    report_table(lol, "LOL research model, validation split")


if __name__ == "__main__":
    main()
