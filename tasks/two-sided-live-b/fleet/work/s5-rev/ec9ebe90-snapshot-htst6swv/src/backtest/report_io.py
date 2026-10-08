"""Read optional tables from a backtest report."""

from pathlib import Path

import pandas as pd


def load_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)
