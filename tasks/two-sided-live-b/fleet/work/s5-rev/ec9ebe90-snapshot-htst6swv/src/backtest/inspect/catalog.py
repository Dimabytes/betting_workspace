"""Discover finished maker backtest runs without importing the Nautilus runner."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backtest.paths import (
    DOTA_MAKER_BACKTESTS_DIR,
    LOL_MAKER_BACKTESTS_DIR,
    SUMMARY_FILENAME,
)

Game = Literal["dota", "lol"]

GAME_ROOTS: dict[Game, Path] = {
    "dota": DOTA_MAKER_BACKTESTS_DIR,
    "lol": LOL_MAKER_BACKTESTS_DIR,
}


@dataclass(frozen=True)
class BacktestRun:
    """One run directory that holds at least one seed summary."""

    game: Game
    name: str
    path: Path
    seeds: tuple[int, ...]
    net_pnl: float | None
    cvar_5: float | None
    modified_at: float


def discover_seeds(run_root: Path) -> tuple[int, ...]:
    """Seed numbers of every seed{N}/ dir under a run root that holds a summary."""
    seeds = sorted(
        int(path.name.removeprefix("seed"))
        for path in run_root.glob("seed*")
        if path.is_dir() and (path / SUMMARY_FILENAME).is_file()
    )
    return tuple(seeds)


def _read_summary_metrics(seed_dir: Path) -> tuple[float, float] | None:
    """net_pnl and CVaR 5% from the first arm, or None when the summary is unreadable."""
    path = seed_dir / SUMMARY_FILENAME
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    arms = payload.get("arms")
    if not isinstance(arms, list) or not arms:
        return None
    arm = arms[0]
    if not isinstance(arm, dict):
        return None
    net_pnl = arm.get("net_pnl")
    cvar_5 = arm.get("cvar_5")
    if type(net_pnl) is not float and type(net_pnl) is not int:
        return None
    if type(cvar_5) is not float and type(cvar_5) is not int:
        return None
    return float(net_pnl), float(cvar_5)


def _mean_metrics(run_root: Path, seeds: tuple[int, ...]) -> tuple[float | None, float | None]:
    """Mean net and CVaR across seeds, preferring seeds.json when present."""
    seeds_path = run_root / "seeds.json"
    if seeds_path.is_file():
        try:
            payload = json.loads(seeds_path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            mean = payload.get("mean")
            if isinstance(mean, dict):
                net_pnl = mean.get("net_pnl")
                cvar_5 = mean.get("cvar_5")
                if (type(net_pnl) is float or type(net_pnl) is int) and (
                    type(cvar_5) is float or type(cvar_5) is int
                ):
                    return float(net_pnl), float(cvar_5)
    nets: list[float] = []
    cvars: list[float] = []
    for seed in seeds:
        metrics = _read_summary_metrics(run_root / f"seed{seed}")
        if metrics is None:
            continue
        nets.append(metrics[0])
        cvars.append(metrics[1])
    if not nets:
        return None, None
    return sum(nets) / len(nets), sum(cvars) / len(cvars)


def _modified_at(run_root: Path, seeds: tuple[int, ...]) -> float:
    """Newest seeds.json or seed summary mtime."""
    seeds_path = run_root / "seeds.json"
    stamps: list[float] = []
    if seeds_path.is_file():
        stamps.append(seeds_path.stat().st_mtime)
    for seed in seeds:
        summary = run_root / f"seed{seed}" / SUMMARY_FILENAME
        if summary.is_file():
            stamps.append(summary.stat().st_mtime)
    return max(stamps) if stamps else run_root.stat().st_mtime


def list_runs(*, game: Game, root: Path) -> tuple[BacktestRun, ...]:
    """Runs under `root` that have seed summaries, newest first."""
    if not root.is_dir():
        return ()
    runs: list[BacktestRun] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        seeds = discover_seeds(path)
        if not seeds:
            continue
        net_pnl, cvar_5 = _mean_metrics(path, seeds)
        runs.append(
            BacktestRun(
                game=game,
                name=path.name,
                path=path,
                seeds=seeds,
                net_pnl=net_pnl,
                cvar_5=cvar_5,
                modified_at=_modified_at(path, seeds),
            )
        )
    runs.sort(key=lambda run: run.modified_at, reverse=True)
    return tuple(runs)


def game_root(game: Game) -> Path:
    """On-disk maker backtest root for one title."""
    return GAME_ROOTS[game]
