"""Cross-seed table mean/sd and seeds.json body."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from report_seeds import (
    COHORT_TITLE,
    TIME_RESULT_COLUMNS,
    TIME_TITLE,
    SeedRow,
    build_cohort_rows,
    build_seeds_payload,
    build_time_rows,
    format_seeds_table,
    format_time_table,
    load_seed_frames,
    match_mean_over_sd,
    maxdd_over_total,
    mean_row,
    sd_row,
)


def _row(net_pnl: float, traded: float) -> SeedRow:
    """One table row; only net_pnl and traded vary in these checks."""
    return SeedRow(
        completed=447.0,
        traded=traded,
        buy_fills=800.0,
        sell_fills=1200.0,
        net_pnl=net_pnl,
        match_mean_sd=0.2,
        maxdd_over_total=0.1,
        roi_with_rebate=0.5,
        loss_match_rate=0.4,
        cvar_5=-30.0,
        worst_match=-80.0,
        buy_300s=0.018,
        required_cash_with_reserves=5000.0,
        required_cash_with_reserves_at_close=5200.0,
        peak_reserved=900.0,
        net_per_required_deposit=net_pnl / 5000.0,
    )


def test_mean_and_sd_of_three_seed_rows() -> None:
    """Mean is the arithmetic mean; sd is the sample standard deviation."""
    rows = (_row(10.0, 100.0), _row(20.0, 100.0), _row(30.0, 100.0))
    mean = mean_row(rows)
    sd = sd_row(rows)
    assert mean.net_pnl == 20.0
    assert mean.traded == 100.0
    assert sd.net_pnl == pytest.approx(10.0)
    assert sd.traded == 0.0


def test_match_mean_over_sd_and_maxdd_over_total() -> None:
    """Per-match mean/sd is mean divided by sample sd; maxDD/total is the ratio."""
    assert match_mean_over_sd((1.0, 3.0, 5.0)) == pytest.approx(3.0 / 2.0)
    assert match_mean_over_sd((1.0,)) == 0.0
    assert match_mean_over_sd((2.0, 2.0)) == 0.0
    assert maxdd_over_total(max_match_drawdown=20.0, total=100.0) == pytest.approx(0.2)
    assert maxdd_over_total(max_match_drawdown=10.0, total=0.0) == 0.0


def test_seeds_payload_and_table_list_each_seed() -> None:
    """JSON body has seeds/mean/sd; the printed table names every metric."""
    rows = (_row(10.0, 1.0), _row(20.0, 2.0), _row(30.0, 3.0))
    payload = build_seeds_payload(rows)
    assert len(payload["seeds"]) == 3
    assert payload["mean"]["net_pnl"] == 20.0
    table = format_seeds_table((0, 1, 2), rows)
    assert "net_pnl" in table
    assert "mean/sd" in table
    assert "maxDD/tot" in table
    assert "buy_300s" in table
    assert "cash_res" in table
    assert "peak_res" in table
    assert "net/dep" in table
    assert table.splitlines()[1].startswith("0 ")
    assert table.splitlines()[-2].startswith("mean")


def test_one_seed_reports_without_sd() -> None:
    """A single seed prints its table and writes its JSON; sample sd needs two."""
    rows = (_row(10.0, 1.0),)
    payload = build_seeds_payload(rows)
    assert payload["mean"]["net_pnl"] == 10.0
    assert "sd" not in payload
    table = format_seeds_table((0,), rows)
    assert table.splitlines()[-1].startswith("mean")


def test_time_table_uses_equal_match_thirds_and_turnover(tmp_path: Path) -> None:
    """Each chronological third prints distinct BUY metrics for all three seeds."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for seed in range(3):
        seed_dir = tmp_path / f"seed{seed}"
        seed_dir.mkdir()
        pd.DataFrame(
            [
                {
                    "match_id": match_id,
                    "horn_at": (start + timedelta(days=match_id)).isoformat(),
                    "terminated_early": False,
                    "buy_fills": 1,
                    "sell_fills": 0,
                    "buy_quantity": 10.0,
                    "engine_pnl": float(match_id + seed),
                }
                for match_id in range(1, 7)
            ]
        ).to_parquet(seed_dir / "results.parquet", index=False)
        pd.DataFrame(
            [
                {
                    "match_id": match_id,
                    "side": "BUY",
                    "price": 0.5 + 0.1 * seed,
                    "quantity": 10.0,
                    "maker_rebate": 0.1 * (seed + 1),
                    "markout_300s": 0.01 * (seed + 1),
                }
                for match_id in range(1, 7)
            ]
        ).to_parquet(seed_dir / "fills.parquet", index=False)
    frames = load_seed_frames(tmp_path, (0, 1, 2), TIME_RESULT_COLUMNS)
    rows = build_time_rows(frames, (0, 1, 2))
    assert [(row.period, row.seed) for row in rows] == [
        (period, seed) for period in ("early", "middle", "late") for seed in range(3)
    ]
    assert [row.matches for row in rows] == [2] * 9
    assert [row.turnover for row in rows[:3]] == pytest.approx([10.0, 12.0, 14.0])
    assert [row.pre_pnl_per_match for row in rows[:3]] == pytest.approx([1.5, 2.5, 3.5])
    assert [row.net_pnl_per_share for row in rows[:3]] == pytest.approx([0.16, 0.27, 0.38])
    assert [row.buy_300s for row in rows[:3]] == pytest.approx([0.01, 0.02, 0.03])
    table = format_time_table(rows, TIME_TITLE)
    assert "matches" in table
    assert "turnover" in table
    assert "avg" not in table
    assert "seed0" in table
    assert "seed1" in table
    assert "seed2" in table
    assert "$10" in table


def test_cohort_rows_split_archive_feeds_from_grid_v1(tmp_path: Path) -> None:
    """Each feed cohort gets its own per-seed row over only its maps."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cohorts = {1: ("schedule", "grid"), 2: ("schedule", "grid"), 3: ("schedule", "oddin")}
    cohorts |= {4: ("grid_v1", ""), 5: ("grid_v1", ""), 6: ("grid_v1", "")}
    for seed in range(2):
        seed_dir = tmp_path / f"seed{seed}"
        seed_dir.mkdir()
        pd.DataFrame(
            [
                {
                    "match_id": match_id,
                    "horn_at": (start + timedelta(days=match_id)).isoformat(),
                    "terminated_early": False,
                    "buy_fills": 1,
                    "sell_fills": 0,
                    "buy_quantity": 10.0,
                    "engine_pnl": float(match_id + seed),
                    "signal_mode": mode,
                    "feed_source": source,
                }
                for match_id, (mode, source) in cohorts.items()
            ]
        ).to_parquet(seed_dir / "results.parquet", index=False)
        pd.DataFrame(
            [
                {
                    "match_id": match_id,
                    "side": "BUY",
                    "price": 0.5,
                    "quantity": 10.0,
                    "maker_rebate": 0.0,
                    "markout_300s": 0.01,
                }
                for match_id in cohorts
            ]
        ).to_parquet(seed_dir / "fills.parquet", index=False)
    frames = load_seed_frames(
        tmp_path, (0, 1), (*TIME_RESULT_COLUMNS, "signal_mode", "feed_source")
    )
    rows = build_cohort_rows(frames, (0, 1))
    assert [(row.period, row.seed, row.matches) for row in rows] == [
        ("grid_v1", 0, 3),
        ("grid_v1", 1, 3),
        ("schedule:grid", 0, 2),
        ("schedule:grid", 1, 2),
        ("schedule:oddin", 0, 1),
        ("schedule:oddin", 1, 1),
    ]
    assert rows[2].pre_pnl_per_match == pytest.approx(1.5)
    assert "schedule:oddin" in format_time_table(rows, COHORT_TITLE)
