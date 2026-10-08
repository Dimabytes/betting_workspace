"""Promotion validates a whole catalog before it moves parent/LIVE."""

import json
from pathlib import Path

import pandas as pd
import pytest
from promote_backtest import LIVE, LIVE_NOXP, promote

MANIFEST = {"buy_ladder_policy": "follow300-v1", "model_name": "20260904T222046Z"}


def _write_seed(
    seed_dir: Path,
    *,
    seed: int,
    match_ids: list[int],
    terminated: bool,
    stop_reason: str | None = None,
) -> None:
    """One complete seed directory: artifacts, manifest, and a matching summary."""
    seed_dir.mkdir(parents=True)
    rows = [
        {
            "match_id": match_id,
            "terminated_early": terminated,
            "stop_reason": stop_reason if terminated else None,
        }
        for match_id in match_ids
    ]
    pd.DataFrame(rows).to_parquet(seed_dir / "results.parquet", index=False)
    for name in ("fills.parquet", "quote_events.parquet"):
        pd.DataFrame([{"match_id": match_ids[0]}]).to_parquet(seed_dir / name, index=False)
    (seed_dir / "manifest.json").write_text(json.dumps({**MANIFEST, "signal_cadence_seed": seed}))
    completed = 0 if terminated else len(match_ids)
    (seed_dir / "summary.json").write_text(
        json.dumps({"arms": [{"completed": completed, "terminated": len(match_ids) - completed}]})
    )


def _write_catalog(run: Path, *, seeds: int = LIVE.seeds) -> None:
    """A catalog of `seeds` complete seeds over one shared map universe."""
    for seed in range(seeds):
        _write_seed(run / f"seed{seed}", seed=seed, match_ids=[1, 2, 3], terminated=False)


def test_promote_writes_relative_symlink(tmp_path: Path) -> None:
    """LIVE points at the run name and can be replaced."""
    maker = tmp_path / "dota_maker"
    run = maker / "validation_join_delta01_cut540_nw350_p35_lead2min"
    _write_catalog(run)

    live = promote(run, LIVE)
    assert live == maker / LIVE.link_name
    assert live.is_symlink()
    assert live.readlink() == Path(run.name)
    assert live.resolve() == run

    other = maker / "validation_join_delta01_cut540_nw350_p35_next"
    _write_catalog(other)
    promote(other, LIVE)
    assert live.readlink() == Path(other.name)


def test_promote_rejects_a_three_seed_experiment(tmp_path: Path) -> None:
    """A 3-seed experiment run cannot be LIVE and leaves the old symlink alone."""
    maker = tmp_path / "dota_maker"
    live_run = maker / "good"
    _write_catalog(live_run)
    promote(live_run, LIVE)

    run = maker / "three_seeds"
    _write_catalog(run, seeds=3)
    with pytest.raises(SystemExit, match="seed3"):
        promote(run, LIVE)
    assert (maker / LIVE.link_name).readlink() == Path(live_run.name)


def test_promote_rejects_a_catalog_with_one_failed_result(tmp_path: Path) -> None:
    """Twelve summaries plus one terminated map is not a promotable catalog."""
    maker = tmp_path / "lol_maker"
    live_run = maker / "good"
    _write_catalog(live_run)
    promote(live_run, LIVE)

    run = maker / "one_failed"
    _write_catalog(run)
    for path in (run / "seed7").iterdir():
        path.unlink()
    (run / "seed7").rmdir()
    _write_seed(run / "seed7", seed=7, match_ids=[1, 2, 3], terminated=True)
    with pytest.raises(SystemExit, match="terminated_early"):
        promote(run, LIVE)
    assert (maker / LIVE.link_name).readlink() == Path(live_run.name)


def test_promote_allows_known_nautilus_zero_fill_faults(tmp_path: Path) -> None:
    """A recorded Nautilus 1.226 zero-fill fault is not an unexpected termination."""
    maker = tmp_path / "lol_maker"
    run = maker / "nautilus-fault"
    for seed in range(LIVE.seeds):
        _write_seed(
            run / f"seed{seed}",
            seed=seed,
            match_ids=[1, 2, 3],
            terminated=True,
            stop_reason="nautilus_zero_fill",
        )
    live = promote(run, LIVE)
    assert live.readlink() == Path(run.name)


def test_promote_rejects_a_catalog_missing_fills(tmp_path: Path) -> None:
    """A seed without fills.parquet is an incomplete checkpoint, not a baseline."""
    maker = tmp_path / "dota_maker"
    live_run = maker / "good"
    _write_catalog(live_run)
    promote(live_run, LIVE)

    run = maker / "missing_fills"
    _write_catalog(run)
    (run / "seed4" / "fills.parquet").unlink()
    with pytest.raises(SystemExit, match=r"fills\.parquet"):
        promote(run, LIVE)
    assert (maker / LIVE.link_name).readlink() == Path(live_run.name)


def test_promote_rejects_a_mixed_policy_catalog(tmp_path: Path) -> None:
    """Seeds that ran different policies cannot merge into one baseline."""
    maker = tmp_path / "dota_maker"
    run = maker / "mixed"
    _write_catalog(run)
    (run / "seed5" / "manifest.json").write_text(
        json.dumps({**MANIFEST, "buy_ladder_policy": "buy-ladder-v2", "signal_cadence_seed": 5})
    )
    with pytest.raises(SystemExit, match="buy_ladder_policy"):
        promote(run, LIVE)
    assert not (maker / LIVE.link_name).exists()


def test_promote_noxp_writes_its_own_symlink_and_leaves_live_alone(tmp_path: Path) -> None:
    """--noxp promotes a 1-seed catalog to LIVE_NOXP without touching LIVE."""
    maker = tmp_path / "dota_maker"
    good = maker / "good"
    _write_catalog(good)
    promote(good, LIVE)

    noxp = maker / "noxp"
    _write_catalog(noxp, seeds=1)
    with pytest.raises(SystemExit, match="seed1"):
        promote(noxp, LIVE)

    live_noxp = promote(noxp, LIVE_NOXP)
    assert live_noxp == maker / "LIVE_NOXP"
    assert live_noxp.readlink() == Path(noxp.name)
    assert (maker / LIVE.link_name).readlink() == Path(good.name)
