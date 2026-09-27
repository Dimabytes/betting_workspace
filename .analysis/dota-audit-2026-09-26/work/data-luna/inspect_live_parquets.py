from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq


ROOT = Path("data/backtests/dota_maker/LIVE/seed0")
for name in ("quote_events.parquet", "fills.parquet"):
    path = ROOT / name
    parquet = pq.ParquetFile(path)
    print(name, "schema", parquet.schema_arrow)
    print(name, "metadata_rows", parquet.metadata.num_rows, "row_groups", parquet.metadata.num_row_groups)

q = pq.read_table(
    ROOT / "quote_events.parquet",
    columns=["match_id", "ts_ns", "kind", "token_index", "side", "price", "order_id", "quantity"],
).to_pandas()
print("quote kinds", q.kind.value_counts(dropna=False).to_dict())
print("quote matches", q.match_id.nunique(), "orders", q.loc[q.order_id.ne(""), "order_id"].nunique())
print("order events", q.loc[q.kind.ne("no_quote")].groupby("kind").size().to_dict())
for kind in ("submitted", "accepted", "cancel_request", "canceled", "cancel_ack", "rejected"):
    print("sample", kind, q.loc[q.kind.eq(kind)].head(2).to_dict("records"))

f = pq.read_table(ROOT / "fills.parquet").to_pandas()
print("fills", len(f), "matches", f.match_id.nunique(), "orders", f.order_id.nunique())
print("fill sample", f.head(3).to_dict("records"))
print("fill side", f.side.value_counts(dropna=False).to_dict())
print("fill token_index", f.token_index.value_counts(dropna=False).to_dict())
print("fills qty", float(f.quantity.sum()), "notional", float((f.price * f.quantity).sum()))

manifest = json.loads((ROOT / "manifest.json").read_text())
print("manifest selected_matches", manifest["selected_matches"], "framework_commit", manifest["framework_commit"])
