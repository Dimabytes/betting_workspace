"""Print per-seed, cross-seed, and chronological-third reports for one run."""

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pandas as pd

from backtest.lol_inputs import NO_REPLAY_STOP_REASONS
from backtest.paths import (
    FILLS_FILENAME,
    MANIFEST_FILENAME,
    QUOTE_EVENTS_FILENAME,
    RESULTS_FILENAME,
    SUMMARY_FILENAME,
)
from backtest.report import format_terminal_report
from backtest.report_types import ArmPayload, SummaryPayload

METRIC_KEYS = (
    "completed",
    "traded",
    "buy_fills",
    "sell_fills",
    "net_pnl",
    "match_mean_sd",
    "maxdd_over_total",
    "roi_with_rebate",
    "loss_match_rate",
    "cvar_5",
    "worst_match",
    "buy_300s",
    "required_cash_with_reserves",
    "required_cash_with_reserves_at_close",
    "peak_reserved",
    "net_per_required_deposit",
)
METRIC_HEADERS = {
    "match_mean_sd": "mean/sd",
    "maxdd_over_total": "maxDD/tot",
    "required_cash_with_reserves": "cash_res",
    "required_cash_with_reserves_at_close": "cash_res_cl",
    "peak_reserved": "peak_res",
    "net_per_required_deposit": "net/dep",
}
REQUIRED_SEED_ARTIFACTS = (
    SUMMARY_FILENAME,
    RESULTS_FILENAME,
    FILLS_FILENAME,
    QUOTE_EVENTS_FILENAME,
    MANIFEST_FILENAME,
)
SEED_MANIFEST_KEY = "signal_cadence_seed"


@dataclass(frozen=True)
class SeedRow:
    """One seed's cross-seed table fields, taken from that seed's summary arm."""

    completed: float
    traded: float
    buy_fills: float
    sell_fills: float
    net_pnl: float
    match_mean_sd: float
    maxdd_over_total: float
    roi_with_rebate: float
    loss_match_rate: float
    cvar_5: float
    worst_match: float
    buy_300s: float
    required_cash_with_reserves: float
    required_cash_with_reserves_at_close: float
    peak_reserved: float
    net_per_required_deposit: float


@dataclass(frozen=True)
class TimeSeedMetrics:
    """Metrics for one seed inside one chronological third."""

    traded: int
    turnover: float
    net_pnl: float
    pre_pnl_per_match: float
    net_pnl_per_share: float
    buy_300s: float


@dataclass(frozen=True)
class TimeRow:
    """One seed's printed metrics inside one chronological third."""

    period: str
    dates: str
    matches: int
    seed: int
    traded: int
    turnover: float
    net_pnl: float
    pre_pnl_per_match: float
    net_pnl_per_share: float
    buy_300s: float


def discover_seeds(run_root: Path) -> tuple[int, ...]:
    """Seed numbers of every seed{N}/ dir under a run root that holds a summary."""
    seeds = sorted(
        int(path.name.removeprefix("seed"))
        for path in run_root.glob("seed*")
        if (path / "summary.json").is_file()
    )
    if not seeds:
        raise ValueError(f"{run_root} has no seed*/summary.json")
    return tuple(seeds)


def load_seed_payload(run_root: Path, seed: int) -> SummaryPayload:
    """Read seed{N}/summary.json under a run root."""
    path = run_root / f"seed{seed}" / "summary.json"
    return cast(SummaryPayload, json.loads(path.read_text()))


def _wallet_float(wallet: Mapping[str, object], key: str) -> float:
    """One wallet number, or 0.0 when an archived summary predates that key."""
    raw = wallet.get(key)
    if type(raw) is float or type(raw) is int:
        return float(raw)
    return 0.0


def match_mean_over_sd(pnls: Sequence[float]) -> float:
    """Per-match engine PnL mean / sample sd. Zero when sd is zero."""
    if len(pnls) < 2:
        return 0.0
    sd = statistics.stdev(pnls)
    if sd == 0.0:
        return 0.0
    return statistics.mean(pnls) / sd


def maxdd_over_total(*, max_match_drawdown: float, total: float) -> float:
    """Worst per-match drawdown over total engine PnL. Zero when total is zero."""
    if total == 0.0:
        return 0.0
    return max_match_drawdown / total


def completed_engine_pnls(seed_dir: Path) -> list[float]:
    """Engine PnL of completed maps in one seed's results.parquet."""
    frame = pd.read_parquet(seed_dir / RESULTS_FILENAME, columns=["engine_pnl", "terminated_early"])
    completed = frame.loc[~frame["terminated_early"].astype(bool), "engine_pnl"]
    return [float(value) for value in completed.tolist()]


def row_from_arm(arm: ArmPayload, match_pnls: Sequence[float]) -> SeedRow:
    """Pull the cross-seed table fields from one summary arm plus per-match engine PnLs."""
    markout = arm["markout"]["buy_300s"]
    roi = arm["wallet"]["roi_with_rebate"]
    wallet = cast(Mapping[str, object], arm["wallet"])
    net_pnl = float(arm["net_pnl"])
    deposit = _wallet_float(wallet, "required_cash_with_reserves")
    return SeedRow(
        completed=float(arm["completed"]),
        traded=float(arm["traded"]),
        buy_fills=float(arm["buy_fills"]),
        sell_fills=float(arm["sell_fills"]),
        net_pnl=net_pnl,
        match_mean_sd=match_mean_over_sd(match_pnls),
        maxdd_over_total=maxdd_over_total(
            max_match_drawdown=float(arm["max_match_drawdown"]),
            total=float(arm["total_engine_pnl"]),
        ),
        roi_with_rebate=0.0 if roi is None else float(roi),
        loss_match_rate=float(arm["loss_match_rate"]),
        cvar_5=float(arm["cvar_5"]),
        worst_match=float(arm["worst_match"]),
        buy_300s=0.0 if markout is None else float(markout["estimate"]),
        required_cash_with_reserves=deposit,
        required_cash_with_reserves_at_close=_wallet_float(
            wallet, "required_cash_with_reserves_at_close"
        ),
        peak_reserved=_wallet_float(wallet, "peak_reserved"),
        net_per_required_deposit=net_pnl / deposit if deposit > 0 else 0.0,
    )


def _metric(row: SeedRow, key: str) -> float:
    """One SeedRow field by table-column name."""
    return float(getattr(row, key))


def mean_row(rows: Sequence[SeedRow]) -> SeedRow:
    """Per-metric mean across seeds."""
    return SeedRow(
        **{key: statistics.mean(_metric(row, key) for row in rows) for key in METRIC_KEYS}
    )


def sd_row(rows: Sequence[SeedRow]) -> SeedRow:
    """Per-metric sample sd across seeds."""
    return SeedRow(
        **{key: statistics.stdev(_metric(row, key) for row in rows) for key in METRIC_KEYS}
    )


def format_seeds_table(seeds: Sequence[int], rows: Sequence[SeedRow]) -> str:
    """Aligned cross-seed table: one row per seed, then mean and sd rows."""
    headers = [METRIC_HEADERS.get(key, key) for key in METRIC_KEYS]
    width = max(12, *(len(header) + 2 for header in headers))
    header = f"{'seed':<8}" + "".join(f"{text:>{width}}" for text in headers)
    lines = [header]
    labelled = [*zip((str(seed) for seed in seeds), rows, strict=True)]
    labelled.append(("mean", mean_row(rows)))
    if len(rows) > 1:
        # Sample sd needs two seeds. A one-seed run is a real case (first seed of
        # a sequential sweep), and it must print its table, not raise.
        labelled.append(("sd", sd_row(rows)))
    for label, row in labelled:
        lines.append(
            f"{label:<8}" + "".join(f"{_metric(row, key):{width}.4f}" for key in METRIC_KEYS)
        )
    return "\n".join(lines)


def summarize_time_seed(
    results: pd.DataFrame, fills: pd.DataFrame, match_ids: frozenset[int]
) -> TimeSeedMetrics:
    """Calculate one seed's compact metrics inside one chronological third."""
    selected_results = results.loc[
        results["match_id"].isin(match_ids) & ~results["terminated_early"].astype(bool)
    ]
    selected_fills = fills.loc[fills["match_id"].isin(match_ids)]
    buy_fills = selected_fills.loc[selected_fills["side"].eq("BUY")]
    matches = len(selected_results)
    traded = int(((selected_results["buy_fills"] + selected_results["sell_fills"]) > 0).sum())
    turnover = float((buy_fills["price"] * buy_fills["quantity"]).sum())
    engine_pnl = float(selected_results["engine_pnl"].sum())
    rebate = float(selected_fills["maker_rebate"].sum())
    taker = (
        float(selected_fills["taker_fee"].sum()) if "taker_fee" in selected_fills.columns else 0.0
    )
    net_pnl = engine_pnl + rebate - taker
    bought_shares = float(selected_results["buy_quantity"].sum())
    markout_quantity = float(buy_fills["quantity"].sum())
    weighted_markout = float((buy_fills["markout_300s"] * buy_fills["quantity"]).sum())
    return TimeSeedMetrics(
        traded=traded,
        turnover=turnover,
        net_pnl=net_pnl,
        pre_pnl_per_match=engine_pnl / matches if matches else 0.0,
        net_pnl_per_share=net_pnl / bought_shares if bought_shares else 0.0,
        buy_300s=weighted_markout / markout_quantity if markout_quantity else 0.0,
    )


TIME_TITLE = "TIME STABILITY  (equal-match chronological thirds; one row per seed)"
COHORT_TITLE = (
    "FEED COHORTS  (schedule = archive replay, the cadence seed does not change it;"
    " grid_v1 = synthetic cadence)"
)
TIME_RESULT_COLUMNS = (
    "match_id",
    "horn_at",
    "terminated_early",
    "buy_fills",
    "sell_fills",
    "buy_quantity",
    "engine_pnl",
)


@dataclass(frozen=True)
class SeedFrames:
    """results.parquet and fills.parquet of every seed, in seed order."""

    results: list[pd.DataFrame]
    fills: list[pd.DataFrame]


def label_signal_group(signal_mode: str, feed_source: str) -> str:
    """Cohort label of one result row: the bound feed disambiguates schedule rows."""
    return f"{signal_mode}:{feed_source}" if feed_source else signal_mode


def load_seed_frames(run_root: Path, seeds: Sequence[int], columns: Sequence[str]) -> SeedFrames:
    """Read the chosen results columns and every fill of each seed."""
    results_by_seed: list[pd.DataFrame] = []
    fills_by_seed: list[pd.DataFrame] = []
    for seed in seeds:
        seed_dir = run_root / f"seed{seed}"
        results_by_seed.append(pd.read_parquet(seed_dir / "results.parquet", columns=list(columns)))
        fills = pd.read_parquet(seed_dir / "fills.parquet")
        if "taker_fee" not in fills.columns:
            fills["taker_fee"] = 0.0
        fills_by_seed.append(fills)
    return SeedFrames(results=results_by_seed, fills=fills_by_seed)


def order_shared_completed(results_by_seed: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Seed0 rows of maps every seed completed, sorted by horn, with a parsed `_horn`."""
    completed_sets: list[set[int]] = [
        {
            int(match_id)
            for match_id in frame.loc[~frame["terminated_early"].astype(bool), "match_id"].tolist()
        }
        for frame in results_by_seed
    ]
    shared_ids = completed_sets[0].copy()
    for completed_set in completed_sets[1:]:
        shared_ids.intersection_update(completed_set)
    seed0 = results_by_seed[0]
    completed = seed0.loc[
        seed0["match_id"].isin(shared_ids) & ~seed0["terminated_early"].astype(bool)
    ].copy()
    completed["_horn"] = [
        datetime.fromisoformat(str(horn)) for horn in completed["horn_at"].tolist()
    ]
    return completed.sort_values(["_horn", "match_id"])


def build_group_rows(
    period: str, group: pd.DataFrame, frames: SeedFrames, seeds: Sequence[int]
) -> list[TimeRow]:
    """One TimeRow per seed over the maps of one horn-ordered group."""
    match_ids = frozenset(int(match_id) for match_id in group["match_id"].tolist())
    metrics = [
        summarize_time_seed(results, fills, match_ids)
        for results, fills in zip(frames.results, frames.fills, strict=True)
    ]
    start = datetime.fromisoformat(str(group["_horn"].iloc[0]))
    end = datetime.fromisoformat(str(group["_horn"].iloc[-1]))
    dates = f"{start.date()}..{end.date()}"
    return [
        TimeRow(
            period=period,
            dates=dates,
            matches=len(match_ids),
            seed=seed,
            traded=metric.traded,
            turnover=metric.turnover,
            net_pnl=metric.net_pnl,
            pre_pnl_per_match=metric.pre_pnl_per_match,
            net_pnl_per_share=metric.net_pnl_per_share,
            buy_300s=metric.buy_300s,
        )
        for seed, metric in zip(seeds, metrics, strict=True)
    ]


def build_time_rows(frames: SeedFrames, seeds: Sequence[int]) -> list[TimeRow]:
    """Build equal-match chronological-third rows from already-loaded seed frames."""
    ordered = order_shared_completed(frames.results)
    if len(ordered) < 3:
        return []
    boundaries = (0, len(ordered) // 3, 2 * len(ordered) // 3, len(ordered))
    rows: list[TimeRow] = []
    for index, period in enumerate(("early", "middle", "late")):
        group = ordered.iloc[boundaries[index] : boundaries[index + 1]]
        rows.extend(build_group_rows(period, group, frames, seeds))
    return rows


def build_cohort_rows(frames: SeedFrames, seeds: Sequence[int]) -> list[TimeRow]:
    """Per-seed rows for each feed cohort (schedule:grid, schedule:oddin, grid_v1)."""
    ordered = order_shared_completed(frames.results)
    ordered["_cohort"] = [
        label_signal_group(str(mode), str(source))
        for mode, source in zip(ordered["signal_mode"], ordered["feed_source"], strict=True)
    ]
    rows: list[TimeRow] = []
    for cohort, group in ordered.groupby("_cohort", sort=True):
        rows.extend(build_group_rows(str(cohort), group, frames, seeds))
    return rows


def format_time_table(rows: Sequence[TimeRow], title: str) -> str:
    """Format the compact per-group table (thirds or feed cohorts) for the terminal report."""
    lines = [title]
    if not rows:
        lines.append("  skipped: fewer than 3 shared completed matches")
        return "\n".join(lines)
    lines.append(
        f"{'period':<16}{'dates UTC':<24}{'matches':>9}{'seed':>7}{'traded':>9}"
        f"{'turnover':>14}{'net PnL $':>13}{'pre $/match':>14}"
        f"{'net ¢/share':>14}{'buy 300s':>12}"
    )
    for row in rows:
        lines.append(
            f"{row.period:<16}{row.dates:<24}{row.matches:9d}{'seed' + str(row.seed):>7}"
            f"{row.traded:9d}{'$' + format(row.turnover, ',.0f'):>14}{row.net_pnl:+13.0f}"
            f"{row.pre_pnl_per_match:14.3f}{row.net_pnl_per_share * 100:13.3f}¢"
            f"{row.buy_300s * 100:11.3f}¢"
        )
    return "\n".join(lines)


def format_completeness_note(run_root: Path, seeds: Sequence[int]) -> str:
    """State the seed count actually read and how many maps did not complete."""
    incomplete = 0
    for seed in seeds:
        frame = pd.read_parquet(
            run_root / f"seed{seed}" / RESULTS_FILENAME, columns=["terminated_early"]
        )
        incomplete += int(frame["terminated_early"].astype(bool).sum())
    return f"CATALOG  seeds read: {len(seeds)}  incomplete maps: {incomplete}"


class SeedsPayload(TypedDict):
    """seeds.json body: one row per seed plus mean and sd."""

    seeds: list[dict[str, float]]
    mean: dict[str, float]
    sd: NotRequired[dict[str, float]]


def build_seeds_payload(rows: Sequence[SeedRow]) -> SeedsPayload:
    """JSON body for <run_root>/seeds.json: per-seed rows, mean, and sd when >1 seed."""
    payload: SeedsPayload = {
        "seeds": [asdict(row) for row in rows],
        "mean": asdict(mean_row(rows)),
    }
    # Sample sd needs two seeds. Leave the key out for a one-seed run rather
    # than writing zeros, which would read as a perfectly stable run.
    if len(rows) > 1:
        payload["sd"] = asdict(sd_row(rows))
    return payload


def write_seeds_json(run_root: Path, rows: Sequence[SeedRow]) -> Path:
    """Write the cross-seed table to <run_root>/seeds.json."""
    path = run_root / "seeds.json"
    path.write_text(json.dumps(build_seeds_payload(rows), indent=2) + "\n")
    return path


def collect_seed_rows(run_root: Path, seeds: Sequence[int]) -> list[SeedRow]:
    """Load every seed summary and return their table rows in seed order."""
    rows: list[SeedRow] = []
    for seed in seeds:
        if rows:
            print()
        print(f"{'=' * 34} SEED {seed} {'=' * 34}", flush=True)
        payload = load_seed_payload(run_root, seed)
        print(format_terminal_report(payload), flush=True)
        rows.append(
            row_from_arm(payload["arms"][0], completed_engine_pnls(run_root / f"seed{seed}"))
        )
    return rows


def _read_manifest(seed_dir: Path) -> dict[str, object]:
    """Manifest of one seed directory."""
    return cast(dict[str, object], json.loads((seed_dir / MANIFEST_FILENAME).read_text()))


def _reject_missing_artifacts(run_root: Path, expected_seeds: Sequence[int]) -> None:
    """Raise when a requested seed directory or one of its artifacts is absent."""
    for seed in expected_seeds:
        seed_dir = run_root / f"seed{seed}"
        if not seed_dir.is_dir():
            raise ValueError(f"{run_root} has no seed{seed} directory")
        for name in REQUIRED_SEED_ARTIFACTS:
            if not (seed_dir / name).is_file():
                raise ValueError(f"{seed_dir} has no {name}")


def _reject_incompatible_manifests(run_root: Path, expected_seeds: Sequence[int]) -> None:
    """Raise when the seeds do not share one manifest apart from the cadence seed."""
    reference_seed = expected_seeds[0]
    reference = _read_manifest(run_root / f"seed{reference_seed}")
    reference.pop(SEED_MANIFEST_KEY, None)
    for seed in expected_seeds[1:]:
        manifest = _read_manifest(run_root / f"seed{seed}")
        manifest.pop(SEED_MANIFEST_KEY, None)
        differing = sorted(
            key for key in set(reference) | set(manifest) if reference.get(key) != manifest.get(key)
        )
        if differing:
            raise ValueError(
                f"seed{seed} manifest differs from seed{reference_seed} for keys {differing}"
            )


def _unexpected_terminated_count(frame: pd.DataFrame) -> int:
    """terminated_early maps that are not a known no-replay fault."""
    terminated = frame.loc[frame["terminated_early"].astype(bool)]
    if terminated.empty:
        return 0
    if "stop_reason" not in terminated.columns:
        return len(terminated)
    unexpected = terminated.loc[~terminated["stop_reason"].astype(str).isin(NO_REPLAY_STOP_REASONS)]
    return len(unexpected)


def _reject_incomplete_results(run_root: Path, expected_seeds: Sequence[int]) -> None:
    """Raise on duplicate maps, a differing map universe, or an unexpected terminated map."""
    reference_universe: frozenset[int] | None = None
    for seed in expected_seeds:
        seed_dir = run_root / f"seed{seed}"
        frame = pd.read_parquet(seed_dir / RESULTS_FILENAME)
        match_ids = [int(match_id) for match_id in frame["match_id"].tolist()]
        universe = frozenset(match_ids)
        if len(universe) != len(match_ids):
            raise ValueError(f"seed{seed} results hold duplicate match ids")
        unexpected = _unexpected_terminated_count(frame)
        if unexpected:
            raise ValueError(f"seed{seed} has {unexpected} terminated_early maps")
        if reference_universe is None:
            reference_universe = universe
        elif universe != reference_universe:
            raise ValueError(f"seed{seed} replayed a different map universe than seed0")
        payload = load_seed_payload(run_root, seed)
        arm = payload["arms"][0]
        counted = arm["completed"] + arm["terminated"]
        if counted != len(match_ids):
            raise ValueError(
                f"seed{seed} summary counts {counted} maps but results.parquet holds "
                f"{len(match_ids)}"
            )


def validate_catalog(run_root: Path, expected_seeds: Sequence[int]) -> None:
    """Return only for a complete, single-policy catalog covering every expected seed.

    Rejects missing seeds or artifacts, duplicate map ids, a differing map universe,
    summary/result count mismatches, incompatible manifests, and unexpected terminated
    maps. Known Nautilus 1.226 zero-fill faults are recorded as terminated_early and
    allowed.
    """
    if not expected_seeds:
        raise ValueError("validate_catalog needs at least one expected seed")
    _reject_missing_artifacts(run_root, expected_seeds)
    _reject_incompatible_manifests(run_root, expected_seeds)
    _reject_incomplete_results(run_root, expected_seeds)


def parse_args() -> argparse.Namespace:
    """Parse the run root and the optional completeness assertion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument(
        "--expected-seeds",
        type=int,
        help="assert the catalog holds seed0..seedN-1 and every map completed",
    )
    parser.add_argument(
        "--skip-time-stability",
        action="store_true",
        help="omit the standalone time table when appending compare_backtests output",
    )
    return parser.parse_args()


def main() -> None:
    """Print seed, aggregate, and time reports, then write seeds.json."""
    args = parse_args()
    run_root: Path = args.run_root
    if args.expected_seeds is not None:
        if args.expected_seeds < 1:
            raise SystemExit("--expected-seeds must be >= 1")
        validate_catalog(run_root, range(args.expected_seeds))
        seeds = tuple(range(args.expected_seeds))
    else:
        seeds = discover_seeds(run_root)
    rows = collect_seed_rows(run_root, seeds)
    print(format_seeds_table(seeds, rows), flush=True)
    print()
    if not args.skip_time_stability:
        frames = load_seed_frames(
            run_root, seeds, (*TIME_RESULT_COLUMNS, "signal_mode", "feed_source")
        )
        print(format_time_table(build_time_rows(frames, seeds), TIME_TITLE), flush=True)
        print()
        print(format_time_table(build_cohort_rows(frames, seeds), COHORT_TITLE), flush=True)
    print(format_completeness_note(run_root, seeds), flush=True)
    write_seeds_json(run_root, rows)


if __name__ == "__main__":
    main()
