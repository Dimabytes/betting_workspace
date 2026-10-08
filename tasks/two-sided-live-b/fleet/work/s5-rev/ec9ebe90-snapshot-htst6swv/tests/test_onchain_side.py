"""Aggressor side column for Telonex onchain fills."""

from pathlib import Path

import pandas as pd
import pytest

from backtest.onchain_side import write_aggressor_side

FILE_TOKEN = "111"
SIBLING_TOKEN = "222"


def write_day(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_write_aggressor_side_flips_only_sibling_taker_rows(tmp_path: Path) -> None:
    """One row per Telonex fill class: `side` flips iff taker_asset_id != asset_id."""
    source = tmp_path / "fills.parquet"
    write_day(
        source,
        [
            # maker on file token, taker on sibling (mint/merge): flip
            {
                "asset_id": FILE_TOKEN,
                "maker_asset_id": FILE_TOKEN,
                "taker_asset_id": SIBLING_TOKEN,
                "taker_side": "buy",
                "mirrored": False,
            },
            # maker and taker on the file token: keep
            {
                "asset_id": FILE_TOKEN,
                "maker_asset_id": FILE_TOKEN,
                "taker_asset_id": FILE_TOKEN,
                "taker_side": "sell",
                "mirrored": False,
            },
            # taker on file token against a sibling order (mirrored): keep
            {
                "asset_id": FILE_TOKEN,
                "maker_asset_id": SIBLING_TOKEN,
                "taker_asset_id": FILE_TOKEN,
                "taker_side": "buy",
                "mirrored": True,
            },
            # both on the sibling token, mirrored row in this file: flip
            {
                "asset_id": FILE_TOKEN,
                "maker_asset_id": SIBLING_TOKEN,
                "taker_asset_id": SIBLING_TOKEN,
                "taker_side": "sell",
                "mirrored": True,
            },
        ],
    )
    out = tmp_path / "out.parquet"
    write_aggressor_side(source_path=source, out_path=out)

    result = pd.read_parquet(out)
    assert list(result["side"]) == ["sell", "sell", "buy", "buy"]
    assert list(result["taker_side"]) == ["buy", "sell", "buy", "sell"]


def test_write_aggressor_side_rejects_unknown_taker_side(tmp_path: Path) -> None:
    source = tmp_path / "fills.parquet"
    write_day(
        source,
        [{"asset_id": FILE_TOKEN, "taker_asset_id": FILE_TOKEN, "taker_side": "x"}],
    )
    with pytest.raises(ValueError, match="unexpected taker_side"):
        write_aggressor_side(source_path=source, out_path=tmp_path / "out.parquet")
