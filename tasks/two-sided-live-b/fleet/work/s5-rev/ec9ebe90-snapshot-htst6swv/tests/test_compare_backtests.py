"""Bucket split and three-seed pooled stats."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from compare_backtests import (
    TIME_TITLE,
    MatchHorn,
    RunData,
    SeededRun,
    SharedMetrics,
    build_cohort_delta_rows,
    build_shared_metrics,
    build_time_delta_rows,
    compare_matches,
    decompose_rounds,
    describe_manifest_diffs,
    format_shared_metrics_table,
    format_time_delta_table,
    load_run,
    load_seeded_run,
    pooled_match_deltas,
    print_input_checks,
    reject_short_catalog,
)

from backtest.report_capital import CapitalMetrics

NS = 1_000_000_000
ARM = {"placement": "join", "fill_model": "queue", "signal_mode": "schedule", "feed_source": "grid"}


def write_run(
    run_dir: Path,
    results_rows: Sequence[Mapping[str, object]],
    fills_rows: Sequence[Mapping[str, object]],
) -> None:
    """Write a minimal results.parquet + fills.parquet run directory."""
    run_dir.mkdir()
    results = pd.DataFrame([{**ARM, "terminated_early": False, **row} for row in results_rows])
    results.to_parquet(run_dir / "results.parquet", index=False)
    fills = pd.DataFrame([{**ARM, **row} for row in fills_rows])
    fills.to_parquet(run_dir / "fills.parquet", index=False)


def make_fill(
    match_id: int, second: int, side: str, price: float, quantity: float
) -> dict[str, object]:
    """One synthetic fills.parquet row."""
    return {
        "match_id": match_id,
        "ts_ns": second * NS,
        "side": side,
        "price": price,
        "quantity": quantity,
        "maker_rebate": 0.0,
    }


def test_buckets_and_reconciliation(tmp_path: Path) -> None:
    """identical / diverged / only-buckets split and their sums hit the match-level delta."""
    # match 1: identical round in both runs (+1.00).
    # match 2: diverged pair, entries 2 s apart (+1.00 vs +2.00).
    # match 3: baseline-only round (-1.00); candidate never trades.
    # match 4: candidate-only leftover round settling to +5.00.
    baseline_results = [
        {"match_id": 1, "engine_pnl": 1.0},
        {"match_id": 2, "engine_pnl": 1.0},
        {"match_id": 3, "engine_pnl": -1.0},
        {"match_id": 4, "engine_pnl": 0.0},
    ]
    baseline_fills = [
        make_fill(1, 1, "BUY", 0.5, 10.0),
        make_fill(1, 100, "SELL", 0.6, 10.0),
        make_fill(2, 10, "BUY", 0.5, 10.0),
        make_fill(2, 50, "SELL", 0.6, 10.0),
        make_fill(3, 5, "BUY", 0.5, 10.0),
        make_fill(3, 30, "SELL", 0.4, 10.0),
    ]
    candidate_results = [
        {"match_id": 1, "engine_pnl": 1.0},
        {"match_id": 2, "engine_pnl": 2.0},
        {"match_id": 3, "engine_pnl": 0.0},
        {"match_id": 4, "engine_pnl": 5.0},
    ]
    candidate_fills = [
        make_fill(1, 1, "BUY", 0.5, 10.0),
        make_fill(1, 100, "SELL", 0.6, 10.0),
        make_fill(2, 12, "BUY", 0.5, 10.0),
        make_fill(2, 60, "SELL", 0.7, 10.0),
        make_fill(4, 5, "BUY", 0.5, 10.0),
    ]
    write_run(tmp_path / "baseline", baseline_results, baseline_fills)
    write_run(tmp_path / "candidate", candidate_results, candidate_fills)

    baseline = load_run(tmp_path / "baseline", None, None)
    candidate = load_run(tmp_path / "candidate", None, None)
    comparison = compare_matches(baseline, candidate)
    decomposition = decompose_rounds(baseline, candidate, comparison)

    assert comparison.shared_ids == (1, 2, 3, 4)
    assert sum(comparison.diffs) == pytest.approx(7.0)

    assert decomposition.identical.rounds == 1
    assert decomposition.diverged.rounds == 1
    assert decomposition.diverged.delta == pytest.approx(1.0)
    assert decomposition.baseline_only.rounds == 1
    assert decomposition.baseline_only.delta == pytest.approx(1.0)
    assert decomposition.candidate_only.rounds == 1
    assert decomposition.candidate_only.delta == pytest.approx(5.0)
    assert decomposition.residual == pytest.approx(0.0, abs=1e-9)


def test_entry_gap_beyond_window_splits_into_only_buckets(tmp_path: Path) -> None:
    """Rounds whose entries are more than 5 s apart do not pair."""
    results = [{"match_id": 1, "engine_pnl": 1.0}]
    baseline_fills = [
        make_fill(1, 10, "BUY", 0.5, 10.0),
        make_fill(1, 20, "SELL", 0.6, 10.0),
    ]
    candidate_fills = [
        make_fill(1, 30, "BUY", 0.5, 10.0),
        make_fill(1, 40, "SELL", 0.6, 10.0),
    ]
    write_run(tmp_path / "baseline", results, baseline_fills)
    write_run(tmp_path / "candidate", results, candidate_fills)

    baseline = load_run(tmp_path / "baseline", None, None)
    candidate = load_run(tmp_path / "candidate", None, None)
    comparison = compare_matches(baseline, candidate)
    decomposition = decompose_rounds(baseline, candidate, comparison)

    assert decomposition.identical.rounds == 0
    assert decomposition.diverged.rounds == 0
    assert decomposition.baseline_only.rounds == 1
    assert decomposition.candidate_only.rounds == 1
    assert decomposition.residual == pytest.approx(0.0, abs=1e-9)


def test_load_run_rejects_duplicate_match_id(tmp_path: Path) -> None:
    """Two result rows for the same match_id are a contract break."""
    results = [
        {"match_id": 1, "engine_pnl": 1.0},
        {"match_id": 1, "engine_pnl": 2.0},
    ]
    fills = [make_fill(1, 1, "BUY", 0.4, 10.0)]
    write_run(tmp_path / "dup", results, fills)
    with pytest.raises(ValueError, match="duplicate match_id"):
        load_run(tmp_path / "dup", None, None)


def test_pooled_match_deltas_are_mean_across_seeds() -> None:
    """d[m] averages the three per-seed (candidate - baseline) values."""
    baseline = {
        0: {10: 1.0, 11: 0.0},
        1: {10: 1.0, 11: 0.0},
        2: {10: 1.0, 11: 0.0},
    }
    candidate = {
        0: {10: 2.0, 11: 3.0},
        1: {10: 4.0, 11: 3.0},
        2: {10: 6.0, 11: 3.0},
    }
    pooled = pooled_match_deltas(baseline, candidate, (10, 11))
    assert pooled[10] == pytest.approx(3.0)
    assert pooled[11] == pytest.approx(3.0)


def test_load_seeded_run_rejects_run_without_summaries(tmp_path: Path) -> None:
    """A seed dir that never merged (no summary.json) does not count as a seed."""
    seed0 = tmp_path / "run" / "seed0"
    seed0.parent.mkdir()
    write_run(seed0, [{"match_id": 1, "engine_pnl": 1.0}], [make_fill(1, 1, "BUY", 0.5, 10.0)])
    (seed0 / "manifest.json").write_text(json.dumps({"max_signal_age_seconds": 16.0}))
    with pytest.raises(ValueError, match="no seed"):
        load_seeded_run(tmp_path / "run", None, None)


def test_time_delta_table_uses_equal_match_thirds(tmp_path: Path) -> None:
    """Six horn-ordered matches become three two-match diagnostic rows."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    match_horns = tuple(
        MatchHorn(match_id, start + timedelta(days=match_id)) for match_id in range(1, 7)
    )
    baseline = {seed: {match_id: 0.0 for match_id in range(1, 7)} for seed in range(3)}
    candidate = {
        seed: {match_id: float(match_id + seed) for match_id in range(1, 7)} for seed in range(3)
    }
    pooled = pooled_match_deltas(baseline, candidate, range(1, 7))
    rows = build_time_delta_rows(match_horns, baseline, candidate, pooled)
    assert [row.matches for row in rows] == [2, 2, 2]
    assert rows[0].seed_deltas[0] == pytest.approx(3.0)
    baseline_run = seeded_run(tmp_path / "baseline", range(3))
    candidate_run = seeded_run(tmp_path / "candidate", range(3))
    candidate_run.root.mkdir()
    for seed in range(3):
        baseline_run.by_seed[seed] = replace(baseline_run.by_seed[seed], match_pnl=baseline[seed])
        candidate_run.by_seed[seed] = replace(
            candidate_run.by_seed[seed], match_pnl=candidate[seed]
        )
        write_run(
            candidate_run.root / f"seed{seed}",
            [
                {
                    "match_id": mid,
                    "engine_pnl": pnl,
                    "buy_fills": 1,
                    "sell_fills": 0,
                    "buy_quantity": 10.0,
                }
                for mid, pnl in candidate[seed].items()
            ],
            [{**make_fill(mid, mid, "BUY", 0.5, 10), "markout_300s": 0.1} for mid in range(1, 7)],
        )
    table = format_time_delta_table(rows, baseline_run, candidate_run, TIME_TITLE)
    assert "matches 2" in table
    assert "delta pre $" in table
    assert "cand net $" in table
    assert "candidate - baseline" in table
    assert table.count("seed0") == 3


def test_cohort_delta_rows_pair_each_feed_cohort() -> None:
    """Archive and grid-v1 maps get separate paired-delta rows."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    match_horns = tuple(
        MatchHorn(match_id, start + timedelta(days=match_id)) for match_id in range(1, 5)
    )
    groups = {1: "schedule:grid", 2: "grid_v1", 3: "schedule:grid", 4: "grid_v1"}
    baseline = {seed: {match_id: 0.0 for match_id in range(1, 5)} for seed in range(2)}
    candidate = {
        seed: {match_id: float(match_id * (seed + 1)) for match_id in range(1, 5)}
        for seed in range(2)
    }
    pooled = pooled_match_deltas(baseline, candidate, range(1, 5))
    rows = build_cohort_delta_rows(match_horns, groups, baseline, candidate, pooled)
    assert [(row.period, row.match_ids) for row in rows] == [
        ("grid_v1", (2, 4)),
        ("schedule:grid", (1, 3)),
    ]
    assert rows[1].seed_deltas == pytest.approx((4.0, 8.0))
    assert rows[1].pooled_per_match == pytest.approx(3.0)


def empty_run(label: str) -> RunData:
    """A RunData with no maps, for checks that only read the seed set."""
    return RunData(
        label=label,
        match_pnl={},
        match_rebate={},
        match_taker={},
        match_buy_notional={},
        match_buy_shares={},
        rounds_by_match={},
    )


def seeded_run(root: Path, seeds: Sequence[int]) -> SeededRun:
    """A SeededRun holding the given seed numbers and nothing else."""
    return SeededRun(
        root=root,
        by_seed={seed: empty_run(f"seed{seed}") for seed in seeds},
        max_signal_age_seconds=16.0,
    )


def write_manifest(run_dir: Path, seed: int, payload: Mapping[str, object]) -> None:
    """Write one seed manifest under a run catalog."""
    seed_dir = run_dir / f"seed{seed}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "manifest.json").write_text(json.dumps(dict(payload)))


def test_shared_metrics_report_pnl_before_and_after_rebate(tmp_path: Path) -> None:
    """Volume, rebate, and per-100-share PnL come off the same shared map set."""
    run_dir = tmp_path / "seed0"
    write_run(
        run_dir,
        [{"match_id": 1, "engine_pnl": 2.0}, {"match_id": 2, "engine_pnl": -1.0}],
        [
            {**make_fill(1, 1, "BUY", 0.5, 10.0), "maker_rebate": 0.25},
            {**make_fill(1, 100, "SELL", 0.7, 10.0), "maker_rebate": 0.25},
            {**make_fill(2, 1, "BUY", 0.4, 10.0), "maker_rebate": 0.10},
            {**make_fill(2, 100, "SELL", 0.3, 10.0), "maker_rebate": 0.10},
        ],
    )

    metrics = build_shared_metrics(load_run(run_dir, None, None), (1, 2), CapitalMetrics(5.0, 10.0))

    assert metrics.pnl_pre == pytest.approx(1.0)
    assert metrics.pnl_net == pytest.approx(1.70)
    assert metrics.buy_notional == pytest.approx(9.0)
    assert metrics.net_pnl_per_100_shares == pytest.approx(8.5)
    assert metrics.cvar_5 == pytest.approx(-1.0)
    assert metrics.worst_match == pytest.approx(-1.0)


def test_metrics_table_names_both_rebate_sides() -> None:
    """The table prints mean and worst seed for PnL before and after rebate."""
    baseline = SharedMetrics(10.0, 12.0, 100.0, 1.0, -2.0, -3.0, CapitalMetrics(5.0, 10.0), 2)
    candidate = SharedMetrics(14.0, 16.0, 90.0, 1.5, -1.0, -2.0, CapitalMetrics(4.0, 8.0), 2)

    table = format_shared_metrics_table([baseline], [candidate], [4.0])

    assert "pnl before rebate" in table
    assert "pnl with rebate" in table
    assert "+400.0%" in table
    assert "+160.0 pp" in table
    assert "+2.000" in table
    assert "cvar 5% (pre)" in table
    assert "paired seed delta (pre)" in table


def test_manifest_diffs_separate_the_model_from_the_inputs(tmp_path: Path) -> None:
    """A different model or whitelist is expected; a different input hash is not."""
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    base_payload = {"signals_sha256": "aaa", "model_sha256": "bbb", "min_abs_delta": 0.01}
    write_manifest(baseline_dir, 0, base_payload)
    write_manifest(
        candidate_dir,
        0,
        {
            **base_payload,
            "model_sha256": "ccc",
            "league_whitelist_sha256": "ddd",
            "buy_ladder_policy": "follow300-v5",
        },
    )

    lines, unexpected = describe_manifest_diffs(baseline_dir, candidate_dir, 0)

    assert unexpected == ()
    assert any("model_sha256" in line for line in lines)

    write_manifest(candidate_dir, 0, {**base_payload, "min_abs_delta": 0.015})
    _, unexpected = describe_manifest_diffs(baseline_dir, candidate_dir, 0)
    assert unexpected == ()

    write_manifest(candidate_dir, 0, {**base_payload, "adverse_taker": True})
    _, unexpected = describe_manifest_diffs(baseline_dir, candidate_dir, 0)
    assert unexpected == ()

    write_manifest(candidate_dir, 0, {**base_payload, "signals_sha256": "zzz"})
    _, unexpected = describe_manifest_diffs(baseline_dir, candidate_dir, 0)
    assert unexpected == ("signals_sha256",)


def test_input_checks_reject_a_missing_expected_seed(tmp_path: Path) -> None:
    """A catalog short of one seed fails before the intersection can hide it."""
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    payload = {"signals_sha256": "aaa"}
    write_manifest(baseline_dir, 0, payload)
    write_manifest(candidate_dir, 0, payload)

    with pytest.raises(ValueError, match=r"is missing seeds \[1\]"):
        print_input_checks(
            seeded_run(baseline_dir, (0,)),
            seeded_run(candidate_dir, (0,)),
            (0,),
            2,
            True,
        )
    # A catalog with spare seeds still satisfies the assertion; the stats run on the
    # intersection.
    print_input_checks(
        seeded_run(baseline_dir, (0, 1, 2)),
        seeded_run(candidate_dir, (0, 1)),
        (0, 1),
        2,
        True,
    )


def test_input_checks_reject_differing_inputs(tmp_path: Path) -> None:
    """Two runs replayed over different prepare artifacts are not comparable."""
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    write_manifest(baseline_dir, 0, {"signals_sha256": "aaa"})
    write_manifest(candidate_dir, 0, {"signals_sha256": "zzz"})

    with pytest.raises(ValueError, match="disagree on inputs"):
        print_input_checks(
            seeded_run(baseline_dir, (0,)),
            seeded_run(candidate_dir, (0,)),
            (0,),
            None,
            False,
        )


def test_short_catalog_is_rejected_against_its_manifest(tmp_path: Path) -> None:
    """A seed missing maps its manifest selected fails, even though seeds still intersect."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_run(run_dir / "seed0", [{"match_id": 1, "engine_pnl": 1.0}], [])
    write_manifest(run_dir, 0, {"selected_matches": 2})

    with pytest.raises(ValueError, match="holds 1 result rows"):
        reject_short_catalog(seeded_run(run_dir, (0,)), (0,))


def test_capital_worst_seed_is_max_and_roi_is_mean_of_seed_ratios() -> None:
    first = SharedMetrics(10.0, 12.0, 100.0, 1.0, -2.0, -3.0, CapitalMetrics(5.0, 10.0), 2)
    second = replace(first, capital=CapitalMetrics(10.0, 20.0))
    table = format_shared_metrics_table([first, second], [first, second], [0.0, 0.0])
    deposit = next(line for line in table.splitlines() if line.startswith("deposit w/ reserves"))
    roi = next(line for line in table.splitlines() if line.startswith("ROI with rebate"))
    assert "+15.00 / +20.00" in deposit
    assert "+180.0% / +120.0%" in roi


def test_zero_deposit_keeps_roi_undefined() -> None:
    empty = SharedMetrics(0, 0, 0, 0, 0, 0, CapitalMetrics(0, 0), 0)
    table = format_shared_metrics_table([empty], [empty], [0.0])
    roi = next(line for line in table.splitlines() if line.startswith("ROI with rebate"))
    assert "n/a / n/a" in roi


def test_cohort_filter_selects_one_signal_mode_and_feed(tmp_path: Path) -> None:
    """--signal-mode/--feed-source keep only that cohort's result rows."""
    results = [
        {"match_id": 1, "engine_pnl": 1.0},
        {"match_id": 2, "engine_pnl": 2.0, "feed_source": "oddin"},
        {"match_id": 3, "engine_pnl": 3.0, "signal_mode": "grid_v1"},
    ]
    fills = [make_fill(1, 1, "BUY", 0.5, 10.0)]
    write_run(tmp_path / "run", results, fills)

    grid = load_run(tmp_path / "run", "schedule", "grid")
    assert set(grid.match_pnl) == {1}
    oddin = load_run(tmp_path / "run", "schedule", "oddin")
    assert set(oddin.match_pnl) == {2}
    v1 = load_run(tmp_path / "run", "grid_v1", None)
    assert set(v1.match_pnl) == {3}
    everything = load_run(tmp_path / "run", None, None)
    assert set(everything.match_pnl) == {1, 2, 3}
