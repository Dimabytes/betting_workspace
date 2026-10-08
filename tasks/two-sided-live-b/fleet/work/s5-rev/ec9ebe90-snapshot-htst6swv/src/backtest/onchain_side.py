# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Add the file-token aggressor side to Telonex onchain fills for the framework.

Telonex writes one matched trade into the day files of both outcome tokens:
`price` is mirrored into the file's own token, while `taker_side` stays the
taker's side on the token it actually traded. The framework hands the raw
column to `telonex_aggressor_side` without checking `taker_asset_id`
(`telonex.py` `_onchain_fill_trade_ticks_from_frame`), so the half of each file
where the taker traded the sibling token arrives with the aggressor flipped.
The rewritten copy keeps `taker_side` untouched and adds `side`, the first
column the loader probes.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def write_aggressor_side(*, source_path: Path, out_path: Path) -> None:
    """Copy one onchain_fills day file with `side` = aggressor on this file's book."""
    table = pq.read_table(source_path)
    taker_side = table["taker_side"].cast(pa.string())
    unknown = set(pc.unique(taker_side).to_pylist()) - {"buy", "sell"}
    if unknown:
        raise ValueError(f"{source_path}: unexpected taker_side {sorted(map(str, unknown))}")
    own_token = pc.equal(
        table["taker_asset_id"].cast(pa.string()), table["asset_id"].cast(pa.string())
    )
    opposite = pc.if_else(pc.equal(taker_side, pa.scalar("buy")), "sell", "buy")
    side = pc.if_else(own_token, taker_side, opposite)
    pq.write_table(table.append_column("side", side), out_path)
