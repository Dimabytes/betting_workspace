"""Write shared Parquet artifacts and normalize nullable table cells."""

from pathlib import Path

import pandas as pd


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def replace_nulls_with_none(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace pandas nulls with None so records match `X | None` TypedDict fields."""
    # pandas-stubs types `other` as non-None; None is valid at runtime.
    return frame.astype(object).where(pd.notna(frame), None)  # pyright: ignore[reportArgumentType]
