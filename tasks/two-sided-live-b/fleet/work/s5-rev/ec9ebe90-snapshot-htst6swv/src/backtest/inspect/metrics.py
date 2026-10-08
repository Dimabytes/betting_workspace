"""Run/seed headline metrics for the Streamlit inspector (from summary + results)."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pyarrow.parquet as pq

from backtest.marks import calculate_cvar_5
from backtest.paths import RESULTS_FILENAME, SUMMARY_FILENAME


@dataclass(frozen=True)
class HeadlineMetrics:
    """Compact report-style numbers for one seed or a multi-seed mean."""

    label: str
    completed: int | None = None
    traded: int | None = None
    no_trades: int | None = None
    terminated: int | None = None
    buy_fills: int | None = None
    sell_fills: int | None = None
    buy_turnover: float | None = None
    pnl_before_rebate: float | None = None
    maker_rebate: float | None = None
    net_pnl: float | None = None
    median_match_pnl: float | None = None
    pnl_per_match: float | None = None
    deposit_with_reserves: float | None = None
    roi_with_rebate: float | None = None
    loss_match_rate: float | None = None
    cvar_5: float | None = None
    worst_match: float | None = None
    hold_p50_seconds: float | None = None
    live_equity_sum: float | None = None
    vs_live: float | None = None


def _num(value: object) -> float | None:
    if type(value) is float or type(value) is int:
        return float(value)
    return None


def _int(value: object) -> int | None:
    number = _num(value)
    if number is None:
        return None
    return int(number)


def _first_arm(seed_dir: Path) -> dict[str, object] | None:
    path = seed_dir / SUMMARY_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    arms = payload.get("arms")
    if not isinstance(arms, list) or not arms:
        return None
    arm = arms[0]
    return arm if isinstance(arm, dict) else None


def _pnls_from_results(seed_dir: Path) -> list[float] | None:
    path = seed_dir / RESULTS_FILENAME
    if not path.is_file():
        return None
    available = set(pq.read_schema(path).names)
    if "engine_pnl" not in available:
        return None
    columns = ["engine_pnl"]
    if "terminated_early" in available:
        columns.append("terminated_early")
    frame = pq.read_table(path, columns=columns).to_pandas()
    if "terminated_early" in frame.columns:
        frame = frame.loc[~frame["terminated_early"].astype(bool)]
    return [float(value) for value in frame["engine_pnl"].tolist()]


def _enrich_risk_from_results(arm: dict[str, object], seed_dir: Path) -> dict[str, object]:
    """Fill CVaR/worst/median from results when summary stubbed them (live-cohort)."""
    pnls = _pnls_from_results(seed_dir)
    if not pnls:
        return arm
    cvar = _num(arm.get("cvar_5"))
    # Live-cohort used to hardcode 0.0; recompute whenever results exist and arm is thin
    # or cvar looks like the old stub with non-empty losses.
    thin = "completed" not in arm or "worst_match" not in arm
    stub = cvar == 0.0 and any(pnl < 0 for pnl in pnls)
    if not (thin or stub):
        return arm
    enriched = dict(arm)
    enriched["cvar_5"] = calculate_cvar_5(pnls)
    enriched["worst_match"] = min(pnls)
    enriched["median_match_pnl"] = float(median(pnls))
    if "completed" not in enriched:
        enriched["completed"] = len(pnls)
    if "matches" not in enriched:
        enriched["matches"] = len(pnls)
    if "loss_match_rate" not in enriched:
        enriched["loss_match_rate"] = sum(1 for pnl in pnls if pnl < 0) / len(pnls)
    return enriched


def metrics_from_arm(arm: dict[str, object], *, label: str) -> HeadlineMetrics:
    wallet = arm.get("wallet")
    hold = arm.get("hold")
    deposit = None
    roi = None
    hold_p50 = None
    if isinstance(wallet, dict):
        deposit = _num(wallet.get("required_cash_with_reserves"))
        roi = _num(wallet.get("roi_with_rebate"))
    if isinstance(hold, dict):
        hold_p50 = _num(hold.get("p50_seconds"))
    net = _num(arm.get("net_pnl"))
    live = _num(arm.get("live_equity_sum"))
    vs_live = None
    if net is not None and live is not None:
        vs_live = net - live
    pnl_per = _num(arm.get("net_pnl_per_match"))
    if pnl_per is None:
        pnl_per = _num(arm.get("pnl_per_eligible_match"))
    return HeadlineMetrics(
        label=label,
        completed=_int(arm.get("completed")),
        traded=_int(arm.get("traded")),
        no_trades=_int(arm.get("no_trades")),
        terminated=_int(arm.get("terminated")),
        buy_fills=_int(arm.get("buy_fills")),
        sell_fills=_int(arm.get("sell_fills")),
        buy_turnover=_num(arm.get("buy_turnover")),
        pnl_before_rebate=_num(arm.get("pnl_before_rebate")),
        maker_rebate=_num(arm.get("maker_rebate")),
        net_pnl=net,
        median_match_pnl=_num(arm.get("median_match_pnl")),
        pnl_per_match=pnl_per,
        deposit_with_reserves=deposit,
        roi_with_rebate=roi,
        loss_match_rate=_num(arm.get("loss_match_rate")),
        cvar_5=_num(arm.get("cvar_5")),
        worst_match=_num(arm.get("worst_match")),
        hold_p50_seconds=hold_p50,
        live_equity_sum=live,
        vs_live=vs_live,
    )


def load_seed_metrics(seed_dir: Path, *, seed: int) -> HeadlineMetrics | None:
    """Metrics for one seed directory."""
    arm = _first_arm(seed_dir)
    if arm is None:
        return None
    arm = _enrich_risk_from_results(arm, seed_dir)
    return metrics_from_arm(arm, label=f"seed{seed}")


def _mean_from_seeds_json(run_dir: Path) -> HeadlineMetrics | None:
    path = run_dir / "seeds.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    mean = payload.get("mean")
    if isinstance(mean, dict) and mean:
        return metrics_from_arm(mean, label="run mean")
    return None


def _average_seed_arms(run_dir: Path, seeds: tuple[int, ...]) -> HeadlineMetrics | None:
    arms: list[dict[str, object]] = []
    for seed in seeds:
        arm = _first_arm(run_dir / f"seed{seed}")
        if arm is None:
            continue
        arms.append(_enrich_risk_from_results(arm, run_dir / f"seed{seed}"))
    if not arms:
        return None
    keys = (
        "completed",
        "traded",
        "no_trades",
        "terminated",
        "buy_fills",
        "sell_fills",
        "buy_turnover",
        "pnl_before_rebate",
        "maker_rebate",
        "net_pnl",
        "median_match_pnl",
        "net_pnl_per_match",
        "pnl_per_eligible_match",
        "loss_match_rate",
        "cvar_5",
        "worst_match",
        "live_equity_sum",
    )
    mean_arm: dict[str, object] = {}
    for key in keys:
        present = [value for value in (_num(arm.get(key)) for arm in arms) if value is not None]
        if present:
            mean_arm[key] = sum(present) / len(present)
    return metrics_from_arm(mean_arm, label="run mean")


def load_run_metrics(run_dir: Path, seeds: tuple[int, ...]) -> HeadlineMetrics | None:
    """Run-level metrics: seeds.json mean when present, else the only seed's arm."""
    if len(seeds) == 1:
        return load_seed_metrics(run_dir / f"seed{seeds[0]}", seed=seeds[0])
    from_json = _mean_from_seeds_json(run_dir)
    if from_json is not None:
        return from_json
    return _average_seed_arms(run_dir, seeds)
