"""Sizing sweep table over the shared completed map set of several seeded runs.

  uv run python scripts/sizing_sweep_report.py <run_dir> [<run_dir>...] [--seed N]

Every metric is computed on the intersection of completed (not terminated_early)
match ids across every seed dir of every run, so points compare on one map
universe. Each run prints an `all` row and an `oddin` row (feed_source == oddin);
metrics are the mean over seeds, and the indented `range` line under a row gives
the per-seed min..max. Deposit is read_shared_capital on the subset; CVaR is
calculate_cvar_5 of per-map engine PnL before rebate, as in report_seeds.
"""

# pyright: reportMissingTypeStubs=false

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pandas as pd
from report_seeds import discover_seeds

from backtest.marks import (
    EnrichedFill,
    MidSeries,
    calculate_cvar_5,
    settlement_value_for_token,
)
from backtest.postprocess import (
    calculate_match_drawdown,
    load_match_mid_series,
    read_fills_checkpoint,
)
from backtest.report_capital import read_shared_capital
from backtest.results import MakerMatchResult, read_results_checkpoint
from shared.constants.lol import LOL_BACKTEST_AUDIT_PATH
from shared.constants.paths import MATCH_CATALOG_PATH
from shared.utils.match_catalog import load_match_catalog

BASE_S = 300.0
BASE_L = 9


@dataclass(frozen=True)
class SweepRun:
    """One sweep point: run root plus the sizing it ran with."""

    root: Path
    level_usdc: float
    max_position_levels: int

    @property
    def label(self) -> str:
        return f"{self.level_usdc:g}x{self.max_position_levels}"

    @property
    def cap_usdc(self) -> float:
        return self.level_usdc * self.max_position_levels


@dataclass(frozen=True)
class MatchOrientation:
    """Radiant token index and winner for drawdown settlement marks."""

    radiant_token_index: int
    radiant_win: bool


@dataclass(frozen=True)
class SeedMetrics:
    """One seed's metrics on one cohort subset of the shared map set."""

    matches: int
    traded: int
    pnl_pre: float
    net_pnl: float
    deposit: float
    cvar_5: float
    worst_match: float
    loss_rate: float
    max_match_drawdown: float
    turnover: float
    fill_rate: float
    median_order: float | None
    buy_300s: float | None
    cap_bound: float


@dataclass(frozen=True)
class CohortRow:
    """One table line: cohort metrics averaged over seeds, with the spread kept."""

    run: SweepRun
    cohort: str
    seeds: tuple[SeedMetrics, ...]


def read_manifest_sizing(run_root: Path, seed: int) -> tuple[float, int]:
    payload = json.loads((run_root / f"seed{seed}" / "manifest.json").read_text())
    return float(payload["layer_usdc"]), int(payload["max_position_levels"])


def read_orientations(seed_dir: Path, match_ids: frozenset[int]) -> dict[int, MatchOrientation]:
    """Radiant orientation per shared match, from the same sources the replay used."""
    manifest = json.loads((seed_dir / "manifest.json").read_text())
    orientations: dict[int, MatchOrientation] = {}
    if manifest.get("game", "dota") == "lol":
        audit = pd.read_parquet(
            LOL_BACKTEST_AUDIT_PATH, columns=["match_id", "radiant_token_index", "radiant_win"]
        )
        selected = audit.loc[audit["match_id"].isin(match_ids)]
        for match_id, token, radiant_win in selected.itertuples(index=False, name=None):
            orientations[int(match_id)] = MatchOrientation(int(token), bool(radiant_win))
    else:
        catalog = load_match_catalog(MATCH_CATALOG_PATH)
        for match_id in match_ids:
            entry = catalog[match_id]
            orientations[match_id] = MatchOrientation(entry.radiant_token_index, entry.radiant_win)
    missing = match_ids - orientations.keys()
    if missing:
        raise ValueError(f"no radiant orientation for shared matches: {sorted(missing)}")
    return orientations


def collect_seed_metrics(
    run: SweepRun,
    seed_dir: Path,
    results: Sequence[MakerMatchResult],
    fills: Sequence[EnrichedFill],
    match_ids: frozenset[int],
    mids: Mapping[int, MidSeries],
    orientations: Mapping[int, MatchOrientation],
) -> SeedMetrics:
    """Metrics of one seed on the given shared subset of completed matches."""
    selected = [result for result in results if result.match_id in match_ids]
    selected_fills = [fill for fill in fills if fill.match_id in match_ids]
    fills_by_match: dict[int, list[EnrichedFill]] = {}
    for fill in selected_fills:
        fills_by_match.setdefault(fill.match_id, []).append(fill)
    result_by_id = {result.match_id: result for result in selected}
    buy_fills = [fill for fill in selected_fills if fill.side == "BUY"]

    pre_pnls = [result.engine_pnl for result in selected]
    rebate = sum(fill.maker_rebate for fill in selected_fills)
    taker = sum(fill.taker_fee for fill in selected_fills)
    capital = read_shared_capital(seed_dir, match_ids)

    traded = [result for result in selected if result.buy_fills + result.sell_fills > 0]
    submitted = sum(result.orders_submitted for result in selected)
    filled_orders = {fill.order_id for fill in selected_fills}
    buy_notionals: dict[str, float] = {}
    for fill in buy_fills:
        buy_notionals[fill.order_id] = (
            buy_notionals.get(fill.order_id, 0.0) + fill.price * fill.quantity
        )
    markout_quantity = sum(fill.quantity for fill in buy_fills)

    cap_line = (run.max_position_levels - 1) * run.level_usdc
    bound = sum(
        1
        for result in traded
        if max(
            (
                fill.position_cost_basis + fill.reserved_buy_notional
                for fill in fills_by_match.get(result.match_id, [])
            ),
            default=0.0,
        )
        >= cap_line
    )

    worst_drawdown = 0.0
    for match_id in match_ids:
        result = result_by_id[match_id]
        orientation = orientations[match_id]
        settlement = (
            0.0
            if result.terminal_token_index < 0
            else settlement_value_for_token(
                result.terminal_token_index,
                orientation.radiant_token_index,
                orientation.radiant_win,
            )
        )
        worst_drawdown = max(
            worst_drawdown,
            calculate_match_drawdown(
                fills_by_match.get(match_id, []),
                mids[match_id],
                settlement,
                orientation.radiant_token_index,
            ),
        )

    return SeedMetrics(
        matches=len(selected),
        traded=len(traded),
        pnl_pre=sum(pre_pnls),
        net_pnl=sum(pre_pnls) + rebate - taker,
        deposit=capital.deposit,
        cvar_5=calculate_cvar_5(pre_pnls),
        worst_match=min(pre_pnls) if pre_pnls else 0.0,
        loss_rate=sum(1 for pnl in pre_pnls if pnl < 0) / len(selected) if selected else 0.0,
        max_match_drawdown=worst_drawdown,
        turnover=sum(fill.price * fill.quantity for fill in buy_fills),
        fill_rate=len(filled_orders) / submitted if submitted else 0.0,
        median_order=median(buy_notionals.values()) if buy_notionals else None,
        buy_300s=(
            sum(fill.markout_300s * fill.quantity for fill in buy_fills) / markout_quantity
            if markout_quantity
            else None
        ),
        cap_bound=bound / len(traded) if traded else 0.0,
    )


def discover_runs(run_dirs: Sequence[Path]) -> tuple[list[SweepRun], tuple[int, ...]]:
    """Read each run's sizing from its seed manifests; seeds must line up."""
    runs: list[SweepRun] = []
    seed_sets: list[tuple[int, ...]] = []
    for run_dir in run_dirs:
        seeds = discover_seeds(run_dir)
        level_usdc, max_position_levels = read_manifest_sizing(run_dir, seeds[0])
        runs.append(
            SweepRun(
                root=run_dir,
                level_usdc=level_usdc,
                max_position_levels=max_position_levels,
            )
        )
        seed_sets.append(seeds)
    shared_seeds = tuple(seed for seed in seed_sets[0] if all(seed in s for s in seed_sets[1:]))
    return runs, shared_seeds


def load_seed_frames(run: SweepRun, seed: int) -> tuple[list[MakerMatchResult], list[EnrichedFill]]:
    seed_dir = run.root / f"seed{seed}"
    return read_results_checkpoint(seed_dir), read_fills_checkpoint(seed_dir)


def shared_completed_ids(
    frames: Mapping[tuple[int, int], tuple[list[MakerMatchResult], list[EnrichedFill]]],
) -> frozenset[int]:
    """Completed match ids present in every (run, seed) frame pair."""
    shared: set[int] | None = None
    for results, _fills in frames.values():
        completed = {result.match_id for result in results if not result.terminated_early}
        shared = completed if shared is None else shared & completed
    return frozenset(shared or set())


def describe_dropped(
    frames: Mapping[tuple[int, int], tuple[list[MakerMatchResult], list[EnrichedFill]]],
    runs: Sequence[SweepRun],
    shared: frozenset[int],
) -> list[str]:
    """Match ids outside the shared set, with the reason and where they died."""
    union: set[int] = set()
    terminated_at: dict[int, set[str]] = {}
    for (run_index, seed), (results, _fills) in frames.items():
        for result in results:
            if result.terminated_early:
                if result.match_id not in shared:
                    terminated_at.setdefault(result.match_id, set()).add(
                        f"{runs[run_index].label}/s{seed}:{result.stop_reason or 'terminated'}"
                    )
            else:
                union.add(result.match_id)
    dropped = sorted(union - shared)
    lines = [f"shared completed maps: {len(shared)} (dropped {len(dropped)} from the union)"]
    never = sorted(set(terminated_at) - union)
    if never:
        lines.append(
            "never completed anywhere: "
            + ", ".join(f"{mid} ({sorted(terminated_at[mid])})" for mid in never)
        )
    for match_id in dropped:
        where = sorted(terminated_at.get(match_id, {"completed nowhere?"}))
        lines.append(f"  {match_id}: {'; '.join(where)}")
    return lines


def marginal_deposit_return(
    row: CohortRow, base: CohortRow | None
) -> tuple[float, float, float] | None:
    """Per-seed (net - net_base) / (dep - dep_base); mean, min, max over seeds."""
    if base is None or base.run is row.run:
        return None
    values: list[float] = []
    for seed_metrics, base_metrics in zip(row.seeds, base.seeds, strict=True):
        delta_dep = seed_metrics.deposit - base_metrics.deposit
        if delta_dep <= 0:
            continue
        values.append((seed_metrics.net_pnl - base_metrics.net_pnl) / delta_dep)
    if not values:
        return None
    return statistics.fmean(values), min(values), max(values)


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values)


def _fmt_money(value: float) -> str:
    return f"{value:,.0f}"


def format_sweep_table(rows: Sequence[CohortRow], base_rows: Mapping[str, CohortRow]) -> str:
    """Capital/PnL block: one mean line and one seed-range line per cohort."""
    header = (
        f"{'point':<9}{'cohort':<7}{'n':>5}{'traded':>7}{'net$':>10}{'dep$':>9}"
        f"{'net/dep':>8}{'marg':>7}{'cvar5$':>8}{'worst$':>9}{'wst/dep':>8}"
        f"{'loss%':>7}{'maxDD$':>8}"
    )
    lines = ["== capital & pnl (mean over seeds; range line = per-seed min..max) ==", header]
    for row in rows:
        seeds = row.seeds
        marg = marginal_deposit_return(row, base_rows.get(row.cohort))
        deposit = _mean([m.deposit for m in seeds])
        worst = _mean([m.worst_match for m in seeds])
        mean_fields = (
            f"{_fmt_money(_mean([m.net_pnl for m in seeds])):>10}"
            f"{_fmt_money(deposit):>9}"
            f"{_mean([m.net_pnl for m in seeds]) / deposit if deposit > 0 else 0.0:>8.2f}"
            f"{('—' if marg is None else f'{marg[0]:.2f}'):>7}"
            f"{_fmt_money(_mean([m.cvar_5 for m in seeds])):>8}"
            f"{_fmt_money(worst):>9}"
            f"{(abs(worst) / deposit if deposit > 0 else 0.0):>8.2f}"
            f"{_mean([m.loss_rate for m in seeds]) * 100:>6.1f}%"
            f"{_fmt_money(_mean([m.max_match_drawdown for m in seeds])):>8}"
        )
        lines.append(
            f"{row.run.label:<9}{row.cohort:<7}{seeds[0].matches:>5}"
            f"{round(_mean([m.traded for m in seeds])):>7}{mean_fields}"
        )
        nets = [m.net_pnl for m in seeds]
        deps = [m.deposit for m in seeds]
        nds = [m.net_pnl / m.deposit if m.deposit > 0 else 0.0 for m in seeds]
        cvars = [m.cvar_5 for m in seeds]
        worsts = [m.worst_match for m in seeds]
        wdeps = [abs(m.worst_match) / m.deposit if m.deposit > 0 else 0.0 for m in seeds]
        losses = [m.loss_rate for m in seeds]
        dds = [m.max_match_drawdown for m in seeds]
        marg_range = "—" if marg is None else f"{marg[1]:.2f}..{marg[2]:.2f}"
        lines.append(
            f"{'':<9}{'range':<7}{'':>5}{'':>7}"
            f"{f'{min(nets):,.0f}..{max(nets):,.0f}':>10}"
            f"{f'{min(deps):,.0f}..{max(deps):,.0f}':>9}"
            f"{f'{min(nds):.2f}..{max(nds):.2f}':>8}"
            f"{marg_range:>7}"
            f"{f'{min(cvars):,.0f}..{max(cvars):,.0f}':>8}"
            f"{f'{min(worsts):,.0f}..{max(worsts):,.0f}':>9}"
            f"{f'{min(wdeps):.2f}..{max(wdeps):.2f}':>8}"
            f"{f'{min(losses):.1%}..{max(losses):.1%}':>7}"
            f"{f'{min(dds):,.0f}..{max(dds):,.0f}':>8}"
        )
    return "\n".join(lines)


def format_execution_table(rows: Sequence[CohortRow]) -> str:
    """Turnover/saturation block: same mean + seed-range line layout."""
    header = (
        f"{'point':<9}{'cohort':<7}{'turn$':>11}{'fill%':>7}{'medOrd$':>9}"
        f"{'medOrd/S':>9}{'buy300s¢':>9}{'capbind%':>9}"
    )
    lines = ["== execution (mean over seeds; range line = per-seed min..max) ==", header]
    for row in rows:
        seeds = row.seeds
        medians = [m.median_order for m in seeds if m.median_order is not None]
        med_mean = _mean(medians) if medians else None
        buy300s = [m.buy_300s for m in seeds if m.buy_300s is not None]
        b3_mean = _mean(buy300s) if buy300s else None
        lines.append(
            f"{row.run.label:<9}{row.cohort:<7}"
            f"{_fmt_money(_mean([m.turnover for m in seeds])):>11}"
            f"{_mean([m.fill_rate for m in seeds]):>6.1%}"
            f"{('n/a' if med_mean is None else f'{med_mean:,.0f}'):>9}"
            f"{('n/a' if med_mean is None else f'{med_mean / row.run.level_usdc:.2f}'):>9}"
            f"{('n/a' if b3_mean is None else f'{b3_mean * 100:.2f}'):>9}"
            f"{_mean([m.cap_bound for m in seeds]):>8.1%}"
        )
        turns = [m.turnover for m in seeds]
        fills = [m.fill_rate for m in seeds]
        caps = [m.cap_bound for m in seeds]
        med_range = "n/a" if not medians else f"{min(medians):,.0f}..{max(medians):,.0f}"
        med_s_range = (
            "n/a"
            if not medians
            else f"{min(medians) / row.run.level_usdc:.2f}..{max(medians) / row.run.level_usdc:.2f}"
        )
        b3_range = "n/a" if not buy300s else f"{min(buy300s) * 100:.2f}..{max(buy300s) * 100:.2f}"
        lines.append(
            f"{'':<9}{'range':<7}"
            f"{f'{min(turns):,.0f}..{max(turns):,.0f}':>11}"
            f"{f'{min(fills):.1%}..{max(fills):.1%}':>7}"
            f"{med_range:>9}"
            f"{med_s_range:>9}"
            f"{b3_range:>9}"
            f"{f'{min(caps):.1%}..{max(caps):.1%}':>9}"
        )
    return "\n".join(lines)


def build_cohort_rows(
    runs: Sequence[SweepRun],
    seeds: Sequence[int],
    frames: Mapping[tuple[int, int], tuple[list[MakerMatchResult], list[EnrichedFill]]],
    shared: frozenset[int],
    mids: Mapping[int, MidSeries],
    orientations: Mapping[int, MatchOrientation],
) -> list[CohortRow]:
    """All- and oddin-cohort metrics for every run, on the shared map set."""
    oddin_ids = frozenset(
        result.match_id
        for result in frames[(0, seeds[0])][0]
        if result.feed_source == "oddin" and result.match_id in shared
    )
    rows: list[CohortRow] = []
    for run_index, run in enumerate(runs):
        for cohort_name, cohort_ids in (("all", shared), ("oddin", oddin_ids)):
            seed_metrics = tuple(
                collect_seed_metrics(
                    run,
                    run.root / f"seed{seed}",
                    frames[(run_index, seed)][0],
                    frames[(run_index, seed)][1],
                    cohort_ids,
                    mids,
                    orientations,
                )
                for seed in seeds
            )
            rows.append(CohortRow(run=run, cohort=cohort_name, seeds=seed_metrics))
    return rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--seed", type=int, default=None, help="report one seed only")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run_dirs: list[Path] = list(args.run_dirs)
    runs, seeds = discover_runs(run_dirs)
    if args.seed is not None:
        for run in runs:
            if args.seed not in discover_seeds(run.root):
                raise SystemExit(f"{run.root} has no seed{args.seed}")
        seeds = (args.seed,)
    if not seeds:
        raise SystemExit("no seeds shared by every run dir")

    frames: dict[tuple[int, int], tuple[list[MakerMatchResult], list[EnrichedFill]]] = {}
    for run_index, run in enumerate(runs):
        for seed in seeds:
            frames[(run_index, seed)] = load_seed_frames(run, seed)
    shared = shared_completed_ids(frames)

    first_seed_dir = runs[0].root / f"seed{seeds[0]}"
    mids = load_match_mid_series(sorted(shared))
    orientations = read_orientations(first_seed_dir, shared)

    rows = build_cohort_rows(runs, seeds, frames, shared, mids, orientations)

    print("runs:")
    for run in runs:
        print(f"  {run.label:<9} {run.root}")
    print(f"seeds: {', '.join(str(seed) for seed in seeds)}")
    for line in describe_dropped(frames, runs, shared):
        print(line)
    print()
    base_rows = {
        row.cohort: row
        for row in rows
        if row.run.level_usdc == BASE_S and row.run.max_position_levels == BASE_L
    }
    print(format_sweep_table(rows, base_rows))
    print()
    print(format_execution_table(rows))


if __name__ == "__main__":
    main()
