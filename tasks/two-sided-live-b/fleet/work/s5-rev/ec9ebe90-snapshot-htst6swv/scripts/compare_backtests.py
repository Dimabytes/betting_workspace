"""Paired comparison of two multi-seed maker-backtest catalogs.

Seeds are the seed{N}/ dirs both runs hold (three by default). Section 1 pairs
(seed, match) on engine PnL before rebate, then pools per-match deltas
across seeds. There is no keep/drop verdict: read the numbers and decide.
Section 2 rebuilds position rounds from seed0 fills only.

  uv run python scripts/compare_backtests.py <baseline_run_dir> <candidate_run_dir>
"""

# pyright: reportMissingTypeStubs=false

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import pandas as pd
from report_seeds import (
    COHORT_TITLE,
    TIME_TITLE,
    discover_seeds,
    label_signal_group,
    summarize_time_seed,
    validate_catalog,
)
from scipy import stats

from backtest.chart import render_balance_chart
from backtest.postprocess import calculate_cvar_5
from backtest.report_capital import CapitalMetrics, read_shared_capital

NS_PER_SECOND = 1_000_000_000
# Same flat-position tolerance as summary's hold logic, so round counts agree.
QTY_EPSILON = 1e-12
ENTRY_PAIR_WINDOW_NS = 5 * NS_PER_SECOND
TIE_THRESHOLD_USD = 0.005
TRIM_FRACTION = 0.10
TOP_MATCHES = 15
CHART_POINTS = 150
SHARES_PER_LOT = 100.0
# Model, league filter, and strategy version may differ in a paired experiment.
# Input and execution settings still have to agree.
EXPECTED_MANIFEST_DIFFS = frozenset(
    {
        "signal_cadence_seed",
        "buy_ladder_policy",
        "model_name",
        "model_path",
        "model_sha256",
        "league_whitelist_path",
        "league_whitelist_sha256",
        "selected_matches",
        "selected_matches_sha256",
        "validation_dataset_sha256",
        "min_abs_delta",
        "exit_abs_delta",
        "archive_policy_sha",
        "archive_model_sha256",
        "signal_source",
        "schedule_map_sha256",
        "archive_exclusions",
        "since_match",
        "model_sha256_by_dir",
        "game_features_sha256",
        "sell_full_age_seconds",
        "sell_ask_age_seconds",
        "max_exit_age_seconds",
        "max_position_levels",
        "layer_usdc",
        "base_size_usdc",
        "adverse_taker",
        "signal_contract",
        "board_reaction_seconds",
    }
)


@dataclass(frozen=True)
class Fill:
    """One fills.parquet row reduced to the fields round rebuilding needs."""

    ts_ns: int
    side: str
    price: float
    quantity: float


@dataclass(frozen=True)
class Round:
    """One position round trip; exit_ns is None for a leftover still open at match end."""

    match_id: int
    entry_ns: int
    exit_ns: int | None
    pnl: float
    fills_signature: tuple[tuple[int, str, float, float], ...]


@dataclass(frozen=True)
class RunData:
    """Per-match engine PnL, traded volume, and rebuilt rounds for one run directory."""

    label: str
    match_pnl: dict[int, float]
    match_rebate: dict[int, float]
    match_taker: dict[int, float]
    match_buy_notional: dict[int, float]
    match_buy_shares: dict[int, float]
    rounds_by_match: dict[int, tuple[Round, ...]]


@dataclass(frozen=True)
class MetricRow:
    """One printed metric: its label and the accessor that reads it."""

    label: str
    read: Callable[["SharedMetrics"], float | None]
    decimals: int
    percent: bool
    worst_is_max: bool


@dataclass(frozen=True)
class SharedMetrics:
    """One seed's metrics over the shared map set, before and after maker rebate."""

    pnl_pre: float
    pnl_net: float
    buy_notional: float
    net_pnl_per_100_shares: float
    cvar_5: float
    worst_match: float
    capital: CapitalMetrics
    matches: int


@dataclass(frozen=True)
class SeededRun:
    """Every seed{N} under one validation catalog, plus the shared entry-age contract."""

    root: Path
    by_seed: dict[int, RunData]
    max_signal_age_seconds: float


@dataclass(frozen=True)
class MatchHorn:
    """One shared completed match ordered by its UTC horn."""

    match_id: int
    horn: datetime


@dataclass(frozen=True)
class MatchComparison:
    """Shared match set and per-match PnL diffs (candidate - baseline)."""

    shared_ids: tuple[int, ...]
    diffs: tuple[float, ...]
    baseline_shared_pnl: float
    candidate_shared_pnl: float


@dataclass(frozen=True)
class RoundPair:
    """One baseline round matched to one candidate round by entry time."""

    baseline: Round
    candidate: Round


@dataclass(frozen=True)
class PairedRounds:
    """Round pairing for one match: pairs plus unmatched rounds on each side."""

    pairs: tuple[RoundPair, ...]
    baseline_only: tuple[Round, ...]
    candidate_only: tuple[Round, ...]


@dataclass(frozen=True)
class StatTest:
    """Statistic and p-value of one significance test."""

    statistic: float
    pvalue: float


@dataclass(frozen=True)
class TimeDeltaRow:
    """Candidate-minus-baseline metrics for one chronological third."""

    period: str
    dates: str
    matches: int
    seed_deltas: tuple[float, ...]
    pooled_per_match: float
    wilcoxon_p: float | None
    match_ids: tuple[int, ...]


@dataclass(frozen=True)
class Bucket:
    """One decomposition bucket: round count and summed PnL delta."""

    rounds: int
    delta: float


@dataclass(frozen=True)
class Decomposition:
    """Round-level buckets over shared matches plus the reconciliation residual."""

    identical: Bucket
    diverged: Bucket
    baseline_only: Bucket
    candidate_only: Bucket
    shared_match_delta: float
    residual: float


def read_match_pnls(results: pd.DataFrame) -> dict[int, float]:
    """Engine PnL per completed match; raise if match_id is duplicated."""
    completed = results[~results["terminated_early"].astype(bool)]
    ids = [int(value) for value in completed["match_id"].tolist()]
    if len(ids) != len(set(ids)):
        raise ValueError("results.parquet has duplicate match_id")
    pnls = [float(value) for value in completed["engine_pnl"].tolist()]
    return dict(zip(ids, pnls, strict=True))


def read_fills_by_match(fills: pd.DataFrame, match_ids: set[int]) -> dict[int, list[Fill]]:
    """Time-ordered fills per completed match; other matches are dropped."""
    by_match: dict[int, list[Fill]] = {}
    rows = zip(
        fills["match_id"].tolist(),
        fills["ts_ns"].tolist(),
        fills["side"].tolist(),
        fills["price"].tolist(),
        fills["quantity"].tolist(),
        strict=True,
    )
    for raw_match, raw_ts, raw_side, raw_price, raw_qty in rows:
        match_id = int(raw_match)
        if match_id not in match_ids:
            continue
        fill = Fill(int(raw_ts), str(raw_side), float(raw_price), float(raw_qty))
        by_match.setdefault(match_id, []).append(fill)
    for match_fills in by_match.values():
        match_fills.sort(key=lambda fill: fill.ts_ns)
    return by_match


def rebuild_rounds(match_id: int, fills: Sequence[Fill], engine_pnl: float) -> tuple[Round, ...]:
    """Split one match's fills into rounds: BUY from flat opens, back to ~0 closes.

    The leftover round (still open at match end) takes engine_pnl minus the
    closed rounds' cash, so per-match round PnLs sum exactly to engine_pnl.
    """
    rounds: list[Round] = []
    qty = 0.0
    cash = 0.0
    entry_ns = 0
    signature: list[tuple[int, str, float, float]] = []
    open_round = False
    for fill in fills:
        if not open_round:
            open_round = True
            entry_ns = fill.ts_ns
            cash = 0.0
            signature = []
        signature.append((fill.ts_ns, fill.side, fill.price, fill.quantity))
        notional = fill.price * fill.quantity
        if fill.side == "BUY":
            qty += fill.quantity
            cash -= notional
            continue
        qty -= fill.quantity
        cash += notional
        if qty > QTY_EPSILON:
            continue
        qty = 0.0
        rounds.append(Round(match_id, entry_ns, fill.ts_ns, cash, tuple(signature)))
        open_round = False
    if open_round:
        closed_pnl = sum(closed.pnl for closed in rounds)
        rounds.append(Round(match_id, entry_ns, None, engine_pnl - closed_pnl, tuple(signature)))
    return tuple(rounds)


def sum_by_match(frame: pd.DataFrame, values: pd.Series, match_ids: Set[int]) -> dict[int, float]:  # pyright: ignore[reportMissingTypeArgument]
    """Sum one value column per completed match; other matches are dropped."""
    totals: dict[int, float] = {}
    for raw_match, raw_value in zip(frame["match_id"].tolist(), values.tolist(), strict=True):
        match_id = int(raw_match)
        if match_id not in match_ids:
            continue
        totals[match_id] = totals.get(match_id, 0.0) + float(raw_value)  # pyright: ignore[reportUnknownArgumentType]
    return totals


def apply_cohort_filter(
    results: pd.DataFrame, signal_mode: str | None, feed_source: str | None
) -> pd.DataFrame:
    """Keep result rows in one signal cohort; both runs filter to paired matches."""
    frame = results
    if signal_mode is not None:
        frame = frame[frame["signal_mode"].astype(str) == signal_mode]
    if feed_source is not None:
        frame = frame[frame["feed_source"].astype(str) == feed_source]
    return frame


def load_run(run_dir: Path, signal_mode: str | None, feed_source: str | None) -> RunData:
    """Read one run directory into per-match PnL, volume, and rebuilt rounds."""
    results = apply_cohort_filter(
        pd.read_parquet(run_dir / "results.parquet"), signal_mode, feed_source
    )
    match_pnl = read_match_pnls(results)
    completed = set(match_pnl)
    fills = pd.read_parquet(run_dir / "fills.parquet")
    if "taker_fee" not in fills.columns:
        fills["taker_fee"] = 0.0
    buys = fills.loc[fills["side"].astype(str) == "BUY"]
    match_rebate = sum_by_match(fills, fills["maker_rebate"], completed)
    match_taker = sum_by_match(fills, fills["taker_fee"], completed)
    match_buy_notional = sum_by_match(buys, buys["price"] * buys["quantity"], completed)
    match_buy_shares = sum_by_match(buys, buys["quantity"], completed)
    fills_by_match = read_fills_by_match(fills, completed)
    rounds_by_match = {
        match_id: rebuild_rounds(match_id, match_fills, match_pnl[match_id])
        for match_id, match_fills in fills_by_match.items()
    }
    return RunData(
        label=run_dir.name,
        match_pnl=match_pnl,
        match_rebate=match_rebate,
        match_taker=match_taker,
        match_buy_notional=match_buy_notional,
        match_buy_shares=match_buy_shares,
        rounds_by_match=rounds_by_match,
    )


def build_shared_metrics(
    run: RunData, shared_ids: Sequence[int], capital: CapitalMetrics
) -> SharedMetrics:
    """One seed's shared-set metrics. CVaR and worst map stay before rebate, as in seeds.json."""
    pre = [run.match_pnl[match_id] for match_id in shared_ids]
    net = [
        run.match_pnl[match_id]
        + run.match_rebate.get(match_id, 0.0)
        - run.match_taker.get(match_id, 0.0)
        for match_id in shared_ids
    ]
    shares = sum(run.match_buy_shares.get(match_id, 0.0) for match_id in shared_ids)
    return SharedMetrics(
        pnl_pre=sum(pre),
        pnl_net=sum(net),
        buy_notional=sum(run.match_buy_notional.get(match_id, 0.0) for match_id in shared_ids),
        net_pnl_per_100_shares=SHARES_PER_LOT * sum(net) / shares if shares else 0.0,
        cvar_5=calculate_cvar_5(pre),
        worst_match=min(pre) if pre else 0.0,
        capital=capital,
        matches=len(shared_ids),
    )


def compare_matches(baseline: RunData, candidate: RunData) -> MatchComparison:
    """Per-match PnL diffs on the intersection of the two runs."""
    shared_ids = tuple(sorted(set(baseline.match_pnl) & set(candidate.match_pnl)))
    return comparison_on_ids(baseline, candidate, shared_ids)


def pair_rounds(baseline: Sequence[Round], candidate: Sequence[Round]) -> PairedRounds:
    """Greedy entry-time pairing; rounds without a partner in the window stay unpaired."""
    pairs: list[RoundPair] = []
    baseline_only: list[Round] = []
    candidate_only: list[Round] = []
    i = 0
    j = 0
    while i < len(baseline) and j < len(candidate):
        gap_ns = candidate[j].entry_ns - baseline[i].entry_ns
        if abs(gap_ns) <= ENTRY_PAIR_WINDOW_NS:
            pairs.append(RoundPair(baseline=baseline[i], candidate=candidate[j]))
            i += 1
            j += 1
        elif gap_ns > 0:
            baseline_only.append(baseline[i])
            i += 1
        else:
            candidate_only.append(candidate[j])
            j += 1
    baseline_only.extend(baseline[i:])
    candidate_only.extend(candidate[j:])
    return PairedRounds(
        pairs=tuple(pairs),
        baseline_only=tuple(baseline_only),
        candidate_only=tuple(candidate_only),
    )


def decompose_rounds(
    baseline: RunData, candidate: RunData, comparison: MatchComparison
) -> Decomposition:
    """Bucket paired rounds over shared matches and reconcile against match-level delta."""
    identical_n = 0
    diverged_n = 0
    diverged_delta = 0.0
    baseline_only_n = 0
    baseline_only_delta = 0.0
    candidate_only_n = 0
    candidate_only_delta = 0.0
    for match_id in comparison.shared_ids:
        paired = pair_rounds(
            baseline.rounds_by_match.get(match_id, ()),
            candidate.rounds_by_match.get(match_id, ()),
        )
        for pair in paired.pairs:
            delta = pair.candidate.pnl - pair.baseline.pnl
            same_fills = pair.candidate.fills_signature == pair.baseline.fills_signature
            if same_fills and delta == 0.0:
                identical_n += 1
                continue
            diverged_n += 1
            diverged_delta += delta
        baseline_only_n += len(paired.baseline_only)
        baseline_only_delta -= sum(lone.pnl for lone in paired.baseline_only)
        candidate_only_n += len(paired.candidate_only)
        candidate_only_delta += sum(lone.pnl for lone in paired.candidate_only)
    shared_match_delta = sum(comparison.diffs)
    bucket_sum = diverged_delta + baseline_only_delta + candidate_only_delta
    return Decomposition(
        identical=Bucket(rounds=identical_n, delta=0.0),
        diverged=Bucket(rounds=diverged_n, delta=diverged_delta),
        baseline_only=Bucket(rounds=baseline_only_n, delta=baseline_only_delta),
        candidate_only=Bucket(rounds=candidate_only_n, delta=candidate_only_delta),
        shared_match_delta=shared_match_delta,
        residual=shared_match_delta - bucket_sum,
    )


def format_money(value: float) -> str:
    """Signed dollar amount with two decimals."""
    return f"{value:+,.2f}"


def format_seed_sd(values: Sequence[float]) -> str:
    if len(values) == 1:
        return "n/a (one seed)"
    return f"{statistics.stdev(values):.2f}"


def run_trimmed_mean(diffs: Sequence[float]) -> float:
    """10% trimmed mean of the per-match diffs."""
    trimmed = stats.trim_mean(diffs, TRIM_FRACTION)  # pyright: ignore[reportUnknownMemberType]
    return cast(float, trimmed)


def run_paired_t(diffs: Sequence[float]) -> StatTest:
    """Paired t-test: one-sample t of the per-match diffs against zero."""
    result = stats.ttest_1samp(diffs, 0.0)  # pyright: ignore[reportUnknownMemberType]
    return StatTest(
        statistic=cast(float, result.statistic),  # pyright: ignore[reportAttributeAccessIssue]
        pvalue=cast(float, result.pvalue),  # pyright: ignore[reportAttributeAccessIssue]
    )


def run_wilcoxon(nonzero_diffs: Sequence[float]) -> StatTest:
    """Wilcoxon signed-rank test of the nonzero per-match diffs."""
    result = stats.wilcoxon(nonzero_diffs)  # pyright: ignore[reportUnknownMemberType]
    return StatTest(
        statistic=cast(float, result.statistic),  # pyright: ignore[reportAttributeAccessIssue]
        pvalue=cast(float, result.pvalue),  # pyright: ignore[reportAttributeAccessIssue]
    )


def print_match_section(comparison: MatchComparison) -> None:
    """Print section 1: paired match-level stats on engine PnL before rebate."""
    diffs = comparison.diffs
    print("== 1. match-level: engine PnL before rebate ==")
    print(f"matches: shared {len(diffs)}")
    delta = comparison.candidate_shared_pnl - comparison.baseline_shared_pnl
    print(
        f"shared PnL: candidate {format_money(comparison.candidate_shared_pnl)}"
        f" vs baseline {format_money(comparison.baseline_shared_pnl)}"
        f", delta {format_money(delta)}"
    )
    mean_diff = sum(diffs) / len(diffs) if diffs else 0.0
    trimmed = run_trimmed_mean(diffs) if diffs else 0.0
    print(f"per-match diff: mean {mean_diff:+.4f}, 10% trimmed mean {trimmed:+.4f}")
    nonzero = [diff for diff in diffs if diff != 0.0]
    if not nonzero:
        print("paired t / wilcoxon: skipped, all per-match diffs are zero")
    else:
        t_test = run_paired_t(diffs)
        w_test = run_wilcoxon(nonzero)
        print(
            f"paired t: t={t_test.statistic:+.3f} p={t_test.pvalue:.4f}"
            f" | wilcoxon: W={w_test.statistic:.1f} p={w_test.pvalue:.4f}"
            f" (nonzero diffs {len(nonzero)})"
        )
    better = sum(1 for diff in diffs if diff >= TIE_THRESHOLD_USD)
    worse = sum(1 for diff in diffs if diff <= -TIE_THRESHOLD_USD)
    tie = len(diffs) - better - worse
    print(f"better {better} / worse {worse} / tie {tie} (tie: |diff| < ${TIE_THRESHOLD_USD})")


def describe_rounds(rounds: Sequence[Round]) -> str:
    """Compact per-round text: hold seconds slash PnL; open leftovers show 'open'."""
    if not rounds:
        return "no rounds"
    parts: list[str] = []
    for one in rounds:
        if one.exit_ns is None:
            hold = "open"
        else:
            hold = f"{(one.exit_ns - one.entry_ns) / NS_PER_SECOND:.0f}s"
        parts.append(f"{hold}/{one.pnl:+.2f}")
    return f"{len(rounds)}r: " + " ".join(parts)


def print_round_section(baseline: RunData, candidate: RunData, comparison: MatchComparison) -> None:
    """Print section 2: round buckets, reconciliation, and the top-|delta| match table."""
    decomposition = decompose_rounds(baseline, candidate, comparison)
    print()
    print("== 2. round-level decomposition over shared matches ==")
    rows = (
        ("identical", decomposition.identical),
        ("diverged pairs", decomposition.diverged),
        ("baseline-only", decomposition.baseline_only),
        ("candidate-only", decomposition.candidate_only),
    )
    print(f"{'bucket':<16}{'rounds':>7}  {'sum delta $':>12}")
    for name, bucket in rows:
        print(f"{name:<16}{bucket.rounds:>7}  {format_money(bucket.delta):>12}")
    print(
        f"{'buckets total':<25}{format_money(decomposition.shared_match_delta - decomposition.residual):>12}"
    )
    print(f"{'match-level delta':<25}{format_money(decomposition.shared_match_delta):>12}")
    print(f"{'residual':<25}{format_money(decomposition.residual):>12}")
    print()
    print(f"top {TOP_MATCHES} matches by |delta|:")
    diff_by_id = dict(zip(comparison.shared_ids, comparison.diffs, strict=True))
    top_ids = sorted(diff_by_id, key=lambda match_id: abs(diff_by_id[match_id]), reverse=True)
    top_ids = top_ids[:TOP_MATCHES]
    baseline_texts = [
        describe_rounds(baseline.rounds_by_match.get(match_id, ())) for match_id in top_ids
    ]
    width = max((len(text) for text in baseline_texts), default=0) + 2
    print(f"{'match_id':<12}{'delta $':>9}  {'baseline (hold/pnl)':<{width}}candidate (hold/pnl)")
    for match_id, baseline_text in zip(top_ids, baseline_texts, strict=True):
        candidate_text = describe_rounds(candidate.rounds_by_match.get(match_id, ()))
        print(
            f"{match_id:<12}{format_money(diff_by_id[match_id]):>9}"
            f"  {baseline_text:<{width}}{candidate_text}"
        )


def load_seeded_run(run_dir: Path, signal_mode: str | None, feed_source: str | None) -> SeededRun:
    """Load every seed{N} dir that holds a summary, in the chosen cohort."""
    by_seed: dict[int, RunData] = {}
    ages: list[float] = []
    for seed in discover_seeds(run_dir):
        seed_dir = run_dir / f"seed{seed}"
        by_seed[seed] = load_run(seed_dir, signal_mode, feed_source)
        manifest = json.loads((seed_dir / "manifest.json").read_text())
        ages.append(float(manifest["max_signal_age_seconds"]))
    return SeededRun(root=run_dir, by_seed=by_seed, max_signal_age_seconds=ages[0])


def shared_match_ids(runs: Sequence[RunData]) -> frozenset[int]:
    """Intersection of completed match_id sets."""
    shared = set(runs[0].match_pnl)
    for run in runs[1:]:
        shared &= set(run.match_pnl)
    return frozenset(shared)


def outside_match_ids(runs: Sequence[RunData], shared: Set[int]) -> tuple[int, ...]:
    """Matches that appear in at least one run but not in the all-run intersection."""
    union: set[int] = set()
    for run in runs:
        union |= set(run.match_pnl)
    return tuple(sorted(union - set(shared)))


def seed_total_delta(baseline: RunData, candidate: RunData, match_ids: Sequence[int]) -> float:
    """Candidate minus baseline engine PnL summed over the shared match set."""
    return sum(
        candidate.match_pnl[match_id] - baseline.match_pnl[match_id] for match_id in match_ids
    )


def per_match_deltas(
    baseline: RunData, candidate: RunData, match_ids: Sequence[int]
) -> tuple[float, ...]:
    """Candidate minus baseline engine PnL per shared match, stable order."""
    return tuple(
        candidate.match_pnl[match_id] - baseline.match_pnl[match_id] for match_id in match_ids
    )


def pooled_match_deltas(
    baseline_by_seed: Mapping[int, Mapping[int, float]],
    candidate_by_seed: Mapping[int, Mapping[int, float]],
    match_ids: Sequence[int],
) -> dict[int, float]:
    """Per-match mean across seeds of (candidate - baseline) engine PnL."""
    pooled: dict[int, float] = {}
    for match_id in match_ids:
        deltas = [
            candidate_by_seed[seed][match_id] - baseline_by_seed[seed][match_id]
            for seed in baseline_by_seed
        ]
        pooled[match_id] = sum(deltas) / len(deltas)
    return pooled


def read_match_horns(results_path: Path, match_ids: Set[int]) -> tuple[MatchHorn, ...]:
    """Read shared match ids and UTC horns, sorted chronologically."""
    results = pd.read_parquet(results_path, columns=["match_id", "horn_at", "terminated_early"])
    completed = results[~results["terminated_early"].astype(bool)]
    rows = [
        MatchHorn(int(mid), datetime.fromisoformat(str(horn)))
        for mid, horn in zip(completed["match_id"], completed["horn_at"], strict=True)
        if int(mid) in match_ids
    ]
    rows.sort(key=lambda row: (row.horn, row.match_id))
    return tuple(rows)


def read_match_signal_groups(results_path: Path, match_ids: Set[int]) -> dict[int, str]:
    """Feed cohort label (schedule:grid, schedule:oddin, grid_v1) per shared match."""
    results = pd.read_parquet(results_path, columns=["match_id", "signal_mode", "feed_source"])
    return {
        int(mid): label_signal_group(str(mode), str(source))
        for mid, mode, source in zip(
            results["match_id"], results["signal_mode"], results["feed_source"], strict=True
        )
        if int(mid) in match_ids
    }


def summarize_delta_group(
    period: str,
    group: Sequence[MatchHorn],
    baseline_by_seed: Mapping[int, Mapping[int, float]],
    candidate_by_seed: Mapping[int, Mapping[int, float]],
    pooled: Mapping[int, float],
) -> TimeDeltaRow:
    """Paired per-seed deltas and the pooled Wilcoxon over one horn-ordered group."""
    match_ids = [row.match_id for row in group]
    seed_deltas = [
        sum(
            candidate_by_seed[seed][match_id] - baseline_by_seed[seed][match_id]
            for match_id in match_ids
        )
        for seed in baseline_by_seed
    ]
    pooled_diffs = [pooled[match_id] for match_id in match_ids]
    nonzero = [diff for diff in pooled_diffs if diff != 0.0]
    return TimeDeltaRow(
        period=period,
        dates=f"{group[0].horn.date()}..{group[-1].horn.date()}",
        matches=len(group),
        seed_deltas=tuple(seed_deltas),
        pooled_per_match=sum(pooled_diffs) / len(pooled_diffs),
        wilcoxon_p=run_wilcoxon(nonzero).pvalue if nonzero else None,
        match_ids=tuple(match_ids),
    )


def build_time_delta_rows(
    match_horns: Sequence[MatchHorn],
    baseline_by_seed: Mapping[int, Mapping[int, float]],
    candidate_by_seed: Mapping[int, Mapping[int, float]],
    pooled: Mapping[int, float],
) -> list[TimeDeltaRow]:
    """Split shared matches into equal-count thirds and summarize paired deltas."""
    if len(match_horns) < 3:
        return []
    boundaries = (0, len(match_horns) // 3, 2 * len(match_horns) // 3, len(match_horns))
    return [
        summarize_delta_group(
            period,
            match_horns[boundaries[index] : boundaries[index + 1]],
            baseline_by_seed,
            candidate_by_seed,
            pooled,
        )
        for index, period in enumerate(("early", "middle", "late"))
    ]


def build_cohort_delta_rows(
    match_horns: Sequence[MatchHorn],
    groups: Mapping[int, str],
    baseline_by_seed: Mapping[int, Mapping[int, float]],
    candidate_by_seed: Mapping[int, Mapping[int, float]],
    pooled: Mapping[int, float],
) -> list[TimeDeltaRow]:
    """One paired-delta row per feed cohort, in label order."""
    return [
        summarize_delta_group(
            label,
            [row for row in match_horns if groups[row.match_id] == label],
            baseline_by_seed,
            candidate_by_seed,
            pooled,
        )
        for label in sorted(set(groups.values()))
    ]


def format_time_delta_table(
    rows: Sequence[TimeDeltaRow], baseline: SeededRun, candidate: SeededRun, title: str
) -> str:
    """Absolute profits and paired effects on the same groups (thirds or feed cohorts)."""
    lines = [
        title,
        "  shared matches only; delta = candidate - baseline; pre = before rebate; net = with rebate",
        "  activity columns (traded, turnover, pre/match, net/share, buy 300s) describe candidate",
    ]
    if not rows:
        lines.append("  skipped: fewer than 3 shared completed matches")
        return "\n".join(lines)
    seeds = sorted(set(baseline.by_seed) & set(candidate.by_seed))
    results = {
        seed: pd.read_parquet(candidate.root / f"seed{seed}" / "results.parquet") for seed in seeds
    }
    fills = {
        seed: pd.read_parquet(candidate.root / f"seed{seed}" / "fills.parquet") for seed in seeds
    }
    for row in rows:
        lines.append(f"  {row.period}  {row.dates}  matches {row.matches}")
        lines.append(
            f"{'seed':<8}{'base pre $':>12}{'cand pre $':>12}{'delta pre $':>12}"
            f"{'base net $':>12}{'cand net $':>12}{'delta net $':>12}"
            f"{'traded':>8}{'turnover $':>12}{'pre $/match':>13}"
            f"{'net ¢/share':>13}{'buy 300s':>12}"
        )
        for seed in seeds:
            base = baseline.by_seed[seed]
            cand = candidate.by_seed[seed]
            base_pre = sum(base.match_pnl[mid] for mid in row.match_ids)
            cand_pre = sum(cand.match_pnl[mid] for mid in row.match_ids)
            base_net = (
                base_pre
                + sum(base.match_rebate.get(mid, 0.0) for mid in row.match_ids)
                - sum(base.match_taker.get(mid, 0.0) for mid in row.match_ids)
            )
            cand_net = (
                cand_pre
                + sum(cand.match_rebate.get(mid, 0.0) for mid in row.match_ids)
                - sum(cand.match_taker.get(mid, 0.0) for mid in row.match_ids)
            )
            activity = summarize_time_seed(results[seed], fills[seed], frozenset(row.match_ids))
            lines.append(
                f"{'seed' + str(seed):<8}{base_pre:+12.2f}{cand_pre:+12.2f}"
                f"{cand_pre - base_pre:+12.2f}{base_net:+12.2f}{cand_net:+12.2f}"
                f"{cand_net - base_net:+12.2f}{activity.traded:8d}{activity.turnover:12,.0f}"
                f"{activity.pre_pnl_per_match:13.3f}{activity.net_pnl_per_share * 100:12.3f}¢"
                f"{activity.buy_300s * 100:11.3f}¢"
            )
        pvalue = "n/a" if row.wilcoxon_p is None else f"{row.wilcoxon_p:.4f}"
        lines.append(
            f"  {row.period} comparison (pre): mean seed delta {statistics.fmean(row.seed_deltas):+.2f}"
            f" | pooled delta $/match {row.pooled_per_match:+.4f} | wilcoxon p {pvalue}"
        )
    return "\n".join(lines)


def comparison_on_ids(
    baseline: RunData, candidate: RunData, shared_ids: Sequence[int]
) -> MatchComparison:
    """MatchComparison restricted to an already-chosen shared population."""
    diffs = per_match_deltas(baseline, candidate, shared_ids)
    return MatchComparison(
        shared_ids=tuple(shared_ids),
        diffs=diffs,
        baseline_shared_pnl=sum(baseline.match_pnl[match_id] for match_id in shared_ids),
        candidate_shared_pnl=sum(candidate.match_pnl[match_id] for match_id in shared_ids),
    )


def format_seed_totals(totals: Sequence[float]) -> str:
    """Per-seed dollar amounts joined with slashes."""
    return "/".join(format_money(total) for total in totals)


def outside_totals(run: SeededRun, seeds: Sequence[int], outside: Sequence[int]) -> list[float]:
    """Per-seed engine PnL over outside ids; ids absent from a seed count as zero."""
    return [
        sum(run.by_seed[seed].match_pnl.get(match_id, 0.0) for match_id in outside)
        for seed in seeds
    ]


def full_totals(run: SeededRun, seeds: Sequence[int]) -> list[float]:
    """Per-seed engine PnL over every completed match, i.e. the seeds.json total before rebate."""
    return [sum(run.by_seed[seed].match_pnl.values()) for seed in seeds]


def print_universe_header(
    baseline: SeededRun,
    candidate: SeededRun,
    seeds: Sequence[int],
    shared: Sequence[int],
    outside: Sequence[int],
) -> None:
    """Shared count plus what each run earned outside the intersection.

    The outside PnL is what separates seeds.json totals from the paired
    stats below; a fat outside line means the validation universe moved,
    not the model.
    """
    print(f"shared matches (all {2 * len(seeds)} runs): {len(shared)}")
    print(f"outside intersection (excluded from stats): {len(outside)}")
    if not outside:
        return
    for label, run in (("baseline ", baseline), ("candidate", candidate)):
        print(
            f"  {label} PnL per seed: outside "
            f"{format_seed_totals(outside_totals(run, seeds, outside))}"
            f"  | full catalog {format_seed_totals(full_totals(run, seeds))}"
        )
    print("  full-catalog totals are not comparable; read the shared-set stats below")


def print_seed_block(
    seed: int,
    comparison: MatchComparison,
    baseline: SharedMetrics,
    candidate: SharedMetrics,
) -> None:
    """One seed's totals, paired tests, and better/worse/tie on the shared set."""
    print(f"-- seed{seed} --")
    print_match_section(comparison)
    print(format_seed_metrics_table(baseline, candidate))
    print()


def read_seed_manifest(run_dir: Path, seed: int) -> dict[str, object]:
    """Manifest of one seed directory."""
    payload = json.loads((run_dir / f"seed{seed}" / "manifest.json").read_text())
    return cast(dict[str, object], payload)


def describe_manifest_diffs(
    baseline_dir: Path, candidate_dir: Path, seed: int
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the two runs' manifest differences into expected and unexpected keys."""
    baseline = read_seed_manifest(baseline_dir, seed)
    candidate = read_seed_manifest(candidate_dir, seed)
    differing = sorted(
        key for key in set(baseline) | set(candidate) if baseline.get(key) != candidate.get(key)
    )
    lines = tuple(
        f"  {key}: baseline {baseline.get(key)!r} | candidate {candidate.get(key)!r}"
        for key in differing
    )
    unexpected = tuple(key for key in differing if key not in EXPECTED_MANIFEST_DIFFS)
    return lines, unexpected


def reject_short_catalog(run: SeededRun, seeds: Sequence[int]) -> None:
    """Require every seed to hold one result row per map its manifest selected."""
    selected = read_seed_manifest(run.root, seeds[0]).get("selected_matches")
    if selected is None:
        return
    expected = int(cast(int, selected))
    for seed in seeds:
        frame = pd.read_parquet(run.root / f"seed{seed}" / "results.parquet", columns=["match_id"])
        if len(frame) != expected:
            raise ValueError(
                f"{run.root} seed{seed} holds {len(frame)} result rows but its manifest "
                f"selected {expected} maps"
            )


def print_input_checks(
    baseline: SeededRun,
    candidate: SeededRun,
    seeds: Sequence[int],
    expected_seeds: int | None,
    allow_drift: bool,
) -> None:
    """Reject an unfinished catalog or runs whose inputs and execution policy differ.

    The paired stats below run on the intersection of the two catalogs, which stays
    populated even when a seed died halfway; these checks are what makes the
    intersection trustworthy.
    """
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        if expected_seeds is not None:
            missing = [seed for seed in range(expected_seeds) if seed not in run.by_seed]
            if missing:
                raise ValueError(
                    f"{label} {run.root} is missing seeds {missing}; "
                    f"expected seed0..seed{expected_seeds - 1}"
                )
    lines, unexpected = describe_manifest_diffs(baseline.root, candidate.root, seeds[0])
    print(f"manifest differences on seed{seeds[0]}: {len(lines)}")
    for line in lines:
        print(line)
    if allow_drift:
        return
    if unexpected:
        raise ValueError(
            f"the two runs disagree on inputs or execution policy for keys {list(unexpected)}; "
            "pass --allow-drift to compare them anyway"
        )
    for run in (baseline, candidate):
        validate_catalog(run.root, seeds)
        reject_short_catalog(run, seeds)


def read_pnl_pre(metrics: SharedMetrics) -> float:
    """Shared-set engine PnL before rebate."""
    return metrics.pnl_pre


def read_pnl_net(metrics: SharedMetrics) -> float:
    """Shared-set PnL after maker rebate."""
    return metrics.pnl_net


def read_buy_notional(metrics: SharedMetrics) -> float:
    """Shared-set BUY notional in USDC."""
    return metrics.buy_notional


def read_net_pnl_per_100_shares(metrics: SharedMetrics) -> float:
    """Shared-set net PnL per 100 bought shares."""
    return metrics.net_pnl_per_100_shares


def read_cvar_5(metrics: SharedMetrics) -> float:
    """Shared-set CVaR 5% of per-map PnL before rebate."""
    return metrics.cvar_5


def read_worst_match(metrics: SharedMetrics) -> float:
    """Shared-set worst map PnL before rebate."""
    return metrics.worst_match


def divide_metric(numerator: float, denominator: float) -> float | None:
    """An undefined ROI is not a zero return."""
    return numerator / denominator if denominator > 0 else None


def build_metric_rows() -> tuple[MetricRow, ...]:
    """Use the same metrics and units in per-seed and aggregate comparisons."""
    return (
        MetricRow("cash est (fills only)", lambda m: m.capital.cash_fills, 2, False, True),
        MetricRow("deposit w/ reserves", lambda m: m.capital.deposit, 2, False, True),
        MetricRow("pnl before rebate", read_pnl_pre, 2, False, False),
        MetricRow(
            "ROI before rebate",
            lambda m: divide_metric(m.pnl_pre, m.capital.cash_fills),
            1,
            True,
            False,
        ),
        MetricRow("rebate", lambda m: m.pnl_net - m.pnl_pre, 2, False, False),
        MetricRow("pnl with rebate", read_pnl_net, 2, False, False),
        MetricRow(
            "ROI with rebate",
            lambda m: divide_metric(m.pnl_net, m.capital.cash_fills),
            1,
            True,
            False,
        ),
        MetricRow(
            "net / deposit w/ reserves",
            lambda m: divide_metric(m.pnl_net, m.capital.deposit),
            3,
            False,
            False,
        ),
        MetricRow("pnl per match", lambda m: divide_metric(m.pnl_pre, m.matches), 3, False, False),
        MetricRow(
            "pnl per match with rebate",
            lambda m: divide_metric(m.pnl_net, m.matches),
            3,
            False,
            False,
        ),
        MetricRow("buy volume usdc", read_buy_notional, 0, False, False),
        MetricRow("net pnl / 100 shares", read_net_pnl_per_100_shares, 4, False, False),
        MetricRow("cvar 5% (pre)", read_cvar_5, 2, False, False),
        MetricRow("worst map (pre)", read_worst_match, 2, False, False),
    )


def format_metric(value: float | None, row: MetricRow, *, delta: bool) -> str:
    """ROI levels use percent; ROI differences use percentage points."""
    if value is None:
        return "n/a"
    suffix = ""
    if row.percent:
        value *= 100
        suffix = " pp" if delta else "%"
    return f"{value:+,.{row.decimals}f}{suffix}"


def format_seed_metrics_table(baseline: SharedMetrics, candidate: SharedMetrics) -> str:
    """Shared-set capital and PnL for one paired seed."""
    lines = [f"{'metric':<28}{'baseline':>18}{'candidate':>18}{'delta':>18}"]
    for row in build_metric_rows():
        base = row.read(baseline)
        cand = row.read(candidate)
        delta = cand - base if cand is not None and base is not None else None
        lines.append(
            f"{row.label:<28}{format_metric(base, row, delta=False):>18}"
            f"{format_metric(cand, row, delta=False):>18}{format_metric(delta, row, delta=True):>18}"
        )
    lines.append("  ROI uses cash est (fills only); net / deposit uses deposit w/ reserves.")
    return "\n".join(lines)


def summarize_metric(values: Sequence[float | None], row: MetricRow) -> str:
    """Do not silently drop seeds with an undefined denominator."""
    if any(value is None for value in values):
        return "n/a / n/a"
    defined = cast(Sequence[float], values)
    worst = max(defined) if row.worst_is_max else min(defined)
    return (
        f"{format_metric(statistics.fmean(defined), row, delta=False)} / "
        f"{format_metric(worst, row, delta=False)}"
    )


def format_shared_metrics_table(
    baseline_metrics: Sequence[SharedMetrics],
    candidate_metrics: Sequence[SharedMetrics],
    seed_deltas: Sequence[float],
) -> str:
    """Cross-seed mean and worst seed, with capital requirements worst at their maximum."""
    lines = [
        "SHARED-SET METRICS  (mean over seeds / worst seed)",
        "  worst = max for cash/deposit, min otherwise; ROI uses fills-only cash; ROI deltas in pp",
        f"{'metric':<28}{'baseline':>28}{'candidate':>28}{'delta of means':>18}",
    ]
    for row in build_metric_rows():
        base = [row.read(metrics) for metrics in baseline_metrics]
        cand = [row.read(metrics) for metrics in candidate_metrics]
        delta = None
        if all(value is not None for value in [*base, *cand]):
            delta = statistics.fmean(cast(list[float], cand)) - statistics.fmean(
                cast(list[float], base)
            )
        lines.append(
            f"{row.label:<28}{summarize_metric(base, row):>28}"
            f"{summarize_metric(cand, row):>28}{format_metric(delta, row, delta=True):>18}"
        )
    lines.append(
        "paired seed delta (pre): "
        + format_seed_totals(seed_deltas)
        + f"  | mean {statistics.fmean(seed_deltas):+.2f}"
        f"  | worst {min(seed_deltas):+.2f}"
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Two required positional multi-seed run directories."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_run_dir", type=Path)
    parser.add_argument("candidate_run_dir", type=Path)
    parser.add_argument(
        "--expected-seeds",
        type=int,
        help="assert both catalogs hold seed0..seedN-1; extra seeds are allowed",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="report an unfinished catalog or differing inputs instead of refusing to compare",
    )
    parser.add_argument(
        "--signal-mode",
        choices=("grid_v1", "schedule"),
        help="restrict both runs to matches replayed under this signal mode",
    )
    parser.add_argument(
        "--feed-source",
        choices=("grid", "oddin"),
        help="restrict both runs to schedule matches bound to this feed",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    """Load both multi-seed catalogs and print the paired keep/drop report."""
    args = parse_args(argv)
    baseline_dir = cast(Path, args.baseline_run_dir)
    candidate_dir = cast(Path, args.candidate_run_dir)
    signal_mode = cast(str | None, args.signal_mode)
    feed_source = cast(str | None, args.feed_source)
    baseline = load_seeded_run(baseline_dir, signal_mode, feed_source)
    candidate = load_seeded_run(candidate_dir, signal_mode, feed_source)
    seeds = tuple(sorted(set(baseline.by_seed) & set(candidate.by_seed)))
    if not seeds:
        raise ValueError("the two runs share no seed{N} directories")
    expected_seeds = cast(int | None, args.expected_seeds)
    if expected_seeds is not None and expected_seeds < 1:
        raise SystemExit("--expected-seeds must be >= 1")
    print_input_checks(baseline, candidate, seeds, expected_seeds, cast(bool, args.allow_drift))
    print()
    seed_list = ",".join(str(seed) for seed in seeds)
    all_runs = [baseline.by_seed[seed] for seed in seeds] + [
        candidate.by_seed[seed] for seed in seeds
    ]
    shared = tuple(sorted(shared_match_ids(all_runs)))
    outside = outside_match_ids(all_runs, set(shared))
    print(
        f"baseline:  {baseline_dir}  seeds {seed_list}"
        f"  max_signal_age_seconds={baseline.max_signal_age_seconds}"
    )
    print(
        f"candidate: {candidate_dir}  seeds {seed_list}"
        f"  max_signal_age_seconds={candidate.max_signal_age_seconds}"
    )
    if signal_mode is not None or feed_source is not None:
        cohort = "+".join(part for part in (signal_mode, feed_source) if part is not None)
        print(f"cohort filter: {cohort}")
    print()
    print_universe_header(baseline, candidate, seeds, shared, outside)
    print()
    seed_deltas: list[float] = []
    baseline_metrics: list[SharedMetrics] = []
    candidate_metrics: list[SharedMetrics] = []
    for seed in seeds:
        comparison = comparison_on_ids(baseline.by_seed[seed], candidate.by_seed[seed], shared)
        seed_deltas.append(
            seed_total_delta(baseline.by_seed[seed], candidate.by_seed[seed], shared)
        )
        base_metrics = build_shared_metrics(
            baseline.by_seed[seed],
            shared,
            read_shared_capital(baseline_dir / f"seed{seed}", frozenset(shared)),
        )
        cand_metrics = build_shared_metrics(
            candidate.by_seed[seed],
            shared,
            read_shared_capital(candidate_dir / f"seed{seed}", frozenset(shared)),
        )
        baseline_metrics.append(base_metrics)
        candidate_metrics.append(cand_metrics)
        print_seed_block(seed, comparison, base_metrics, cand_metrics)
    print()
    print(
        format_shared_metrics_table(
            baseline_metrics,
            candidate_metrics,
            seed_deltas,
        )
    )
    print()
    baseline_pnls = {seed: baseline.by_seed[seed].match_pnl for seed in seeds}
    candidate_pnls = {seed: candidate.by_seed[seed].match_pnl for seed in seeds}
    pooled = pooled_match_deltas(baseline_pnls, candidate_pnls, shared)
    pooled_diffs = tuple(pooled[match_id] for match_id in shared)
    print("== pooled: mean_k delta[m,k] ==")
    if all(diff == 0.0 for diff in pooled_diffs):
        print("identical")
        return
    t_test = run_paired_t(pooled_diffs)
    nonzero = [diff for diff in pooled_diffs if diff != 0.0]
    w_test = run_wilcoxon(nonzero)
    print(
        f"paired t: t={t_test.statistic:+.3f} p={t_test.pvalue:.4f}"
        f" | wilcoxon: W={w_test.statistic:.1f} p={w_test.pvalue:.4f}"
        f" (n={len(shared)}, nonzero {len(nonzero)})"
    )
    baseline_totals = [
        sum(baseline.by_seed[seed].match_pnl[match_id] for match_id in shared) for seed in seeds
    ]
    candidate_totals = [
        sum(candidate.by_seed[seed].match_pnl[match_id] for match_id in shared) for seed in seeds
    ]
    print(
        f"seed-total sd: baseline {format_seed_sd(baseline_totals)}"
        f"  candidate {format_seed_sd(candidate_totals)}"
        f"  effect {sum(seed_deltas) / len(seed_deltas):+.2f}"
    )
    print()
    first_seed = seeds[0]
    match_horns = read_match_horns(
        baseline_dir / f"seed{first_seed}" / "results.parquet", set(shared)
    )
    time_rows = build_time_delta_rows(match_horns, baseline_pnls, candidate_pnls, pooled)
    print(format_time_delta_table(time_rows, baseline, candidate, TIME_TITLE))

    if time_rows and sum(pooled_diffs) * time_rows[-1].pooled_per_match < 0:
        print(
            "RECENT CONFLICT (candidate - baseline, before rebate): "
            f"full-period mean seed delta {statistics.fmean(seed_deltas):+.2f}; "
            f"late-third {statistics.fmean(time_rows[-1].seed_deltas):+.2f}. "
            "Opposite comparison signs; this does not mean candidate lost money."
        )
    print()
    groups = read_match_signal_groups(
        baseline_dir / f"seed{first_seed}" / "results.parquet", set(shared)
    )
    cohort_rows = build_cohort_delta_rows(
        match_horns, groups, baseline_pnls, candidate_pnls, pooled
    )
    print(format_time_delta_table(cohort_rows, baseline, candidate, COHORT_TITLE))
    print()
    cumulative: list[float] = []
    running = 0.0
    for match_horn in match_horns:
        running += pooled[match_horn.match_id]
        cumulative.append(running)
    if len(cumulative) > CHART_POINTS:
        last_index = len(cumulative) - 1
        indices = [round(point * last_index / (CHART_POINTS - 1)) for point in range(CHART_POINTS)]
        cumulative = [cumulative[index] for index in indices]
    print("CUMULATIVE DELTA  (candidate - baseline, before rebate; mean over seeds)")
    print(render_balance_chart(cumulative, 0.0))
    print()
    print(f"round-level section uses seed{first_seed} only")
    first = comparison_on_ids(baseline.by_seed[first_seed], candidate.by_seed[first_seed], shared)
    print_round_section(baseline.by_seed[first_seed], candidate.by_seed[first_seed], first)


if __name__ == "__main__":
    main(sys.argv[1:])
