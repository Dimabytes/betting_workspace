"""Read and write the LoL pipeline stage tables."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pandas as pd

from shared.utils.parquet_io import replace_nulls_with_none, write_parquet


def write_parquet_rows(
    rows: Sequence[Mapping[str, object]],
    path: Path,
    columns: list[str],
    integer_columns: list[str],
    sort_columns: list[str],
) -> None:
    """Write typed rows as one parquet with fixed columns, Int64 ints, and a stable order."""
    frame = pd.DataFrame(list(rows), columns=pd.Index(columns))
    if not frame.empty:
        present = [column for column in integer_columns if column in frame.columns]
        frame[present] = frame[present].astype("Int64")
        frame = frame.sort_values(sort_columns, na_position="last").reset_index(drop=True)
    write_parquet(frame, path)


def read_parquet_rows(path: Path) -> list[dict[str, object]]:
    """Read one stage parquet as records; a missing file raises FileNotFoundError."""
    records = replace_nulls_with_none(pd.read_parquet(path)).to_dict(orient="records")
    return cast(list[dict[str, object]], records)
