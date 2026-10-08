"""Completed maps from one seed's results.parquet, worst engine PnL first."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false
# pyright: reportAttributeAccessIssue=false

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from backtest.live_archives import (
    LiveMatchIndexes,
    build_dota_live_indexes,
    live_equity_by_match_ids,
)
from backtest.paths import RESULTS_FILENAME, SUMMARY_FILENAME
from shared.constants.paths import MATCH_CATALOG_PATH, TRADER_DIR

_RESULT_COLUMNS = (
    "match_id",
    "slug",
    "engine_pnl",
    "buy_fills",
    "sell_fills",
    "horn_at",
    "game_ended_at",
    "terminated_early",
)
_OPTIONAL_RESULT_COLUMNS = ("grid_id",)


@dataclass(frozen=True)
class TailMatch:
    """One completed map in a seed."""

    match_id: int
    slug: str
    engine_pnl: float
    buy_fills: int
    sell_fills: int
    horn_at: str
    game_ended_at: str
    live_equity: float | None = None


@dataclass(frozen=True)
class SeedTail:
    """Completed maps plus the summary numbers shown above the table."""

    seed: int
    completed: int
    cvar_5: float | None
    worst_match: float | None
    min_abs_delta: float | None
    matches: tuple[TailMatch, ...]


def _slug_condition_indexes() -> LiveMatchIndexes:
    """Catalog slug/condition → match_id for joining trader archives."""
    if not MATCH_CATALOG_PATH.is_file():
        return LiveMatchIndexes(slug_to_match_id={}, condition_to_match_id={})
    return build_dota_live_indexes()


def _attach_live_equity(frame: pd.DataFrame) -> pd.DataFrame:
    """Fill missing live_equity from trader session archives when possible."""
    if "match_id" not in frame.columns:
        return frame
    if "live_equity" in frame.columns and bool(frame["live_equity"].notna().all()):
        return frame
    indexes = _slug_condition_indexes()
    if not indexes.slug_to_match_id and not indexes.condition_to_match_id:
        return frame
    equities = live_equity_by_match_ids(
        [int(mid) for mid in frame["match_id"].tolist()],
        trader_dir=TRADER_DIR,
        indexes=indexes,
    )
    if not equities:
        return frame
    mapped = frame["match_id"].map(lambda mid: equities.get(int(mid)))
    out = frame.copy()
    if "live_equity" in out.columns:
        out["live_equity"] = out["live_equity"].where(out["live_equity"].notna(), mapped)
    else:
        out["live_equity"] = mapped
    return out


def load_seed_results(seed_dir: Path) -> pd.DataFrame:
    """Completed and terminated rows for one seed."""
    path = seed_dir / RESULTS_FILENAME
    available = set(pq.read_schema(path).names)
    columns = [name for name in _RESULT_COLUMNS if name in available]
    optional = [name for name in _OPTIONAL_RESULT_COLUMNS if name in available]
    frame = pd.read_parquet(path, columns=[*columns, *optional])
    live_ref = seed_dir / "live_reference.parquet"
    if live_ref.is_file() and "match_id" in frame.columns:
        ref = pd.read_parquet(live_ref, columns=["match_id", "live_equity"])
        frame = frame.merge(ref, on="match_id", how="left")
    return _attach_live_equity(frame)


def pick_completed_matches(results: pd.DataFrame) -> pd.DataFrame:
    """Completed maps by engine PnL, most negative first."""
    completed = results.loc[~results["terminated_early"].astype(bool)]
    return completed.sort_values("engine_pnl", kind="mergesort")


def _summary_arm(seed_dir: Path) -> dict[str, object] | None:
    """First arm object from summary.json, or None when missing or malformed."""
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
    if not isinstance(arm, dict):
        return None
    return arm


def _arm_float(arm: dict[str, object] | None, key: str) -> float | None:
    """One numeric arm field, or None when absent."""
    if arm is None:
        return None
    value = arm.get(key)
    if type(value) is float or type(value) is int:
        return float(value)
    return None


def _manifest_min_abs_delta(seed_dir: Path) -> float | None:
    """Entry-gate |predicted_delta| from summary manifest, or None."""
    path = seed_dir / SUMMARY_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("min_abs_delta")
    if type(value) is float or type(value) is int:
        return float(value)
    return None


def _optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if type(value) is float or type(value) is int:
        return float(value)
    return None


def _row_to_match(row: object) -> TailMatch:
    """Build TailMatch; live-cohort optional fields are None on normal backtests."""
    return TailMatch(
        match_id=int(row.match_id),
        slug=str(row.slug),
        engine_pnl=float(row.engine_pnl),
        buy_fills=int(row.buy_fills),
        sell_fills=int(row.sell_fills),
        horn_at=str(row.horn_at),
        game_ended_at=str(row.game_ended_at),
        live_equity=_optional_float(getattr(row, "live_equity", None)),
    )


def load_seed_tail(seed_dir: Path, seed: int) -> SeedTail:
    """Completed maps and headline numbers for one seed directory."""
    results = load_seed_results(seed_dir)
    completed = pick_completed_matches(results)
    matches = tuple(_row_to_match(row) for row in completed.itertuples(index=False))
    arm = _summary_arm(seed_dir)
    return SeedTail(
        seed=seed,
        completed=len(matches),
        cvar_5=_arm_float(arm, "cvar_5"),
        worst_match=_arm_float(arm, "worst_match"),
        min_abs_delta=_manifest_min_abs_delta(seed_dir),
        matches=matches,
    )
