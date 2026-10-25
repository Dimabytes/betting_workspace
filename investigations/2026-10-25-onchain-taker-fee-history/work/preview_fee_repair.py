import json
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

WORK = Path(__file__).resolve().parent


def preview(source):
    relative = source.relative_to(WORK / "collector")
    game = relative.parts[0]
    token_folder = source.parent.name
    provider = {}
    for path in sorted((WORK / "telonex" / game / "onchain_fills" / token_folder).glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(columns=["tx_hash", "log_index", "asset_id", "taker_fee"]):
            for row in batch.to_pylist():
                key = (row["tx_hash"], row["log_index"], row["asset_id"])
                fee = row["taker_fee"]
                if key in provider and Decimal(provider[key]) != Decimal(fee):
                    raise RuntimeError("Conflicting provider fees for one execution")
                provider[key] = fee
    original = pq.ParquetFile(source).read()
    rows = original.select(["tx_hash", "log_index", "asset_id", "taker_fee"]).to_pylist()
    values = []
    unmatched = 0
    changed = 0
    for row in rows:
        key = (row["tx_hash"], row["log_index"], row["asset_id"])
        fee = provider.get(key)
        if fee is None:
            unmatched += 1
            continue
        changed += Decimal(fee) != Decimal(row["taker_fee"])
        values.append(fee)
    result = {"file": str(relative), "rows": len(rows), "unmatched": unmatched, "changed_fees": changed}
    if unmatched:
        result["status"] = "blocked_unmatched_rows"
        return result
    index = original.schema.get_field_index("taker_fee")
    repaired = original.set_column(index, original.schema.field(index), pa.array(values, type=original.schema.field(index).type))
    assert repaired.num_rows == original.num_rows
    assert repaired.drop(["taker_fee"]).equals(original.drop(["taker_fee"]))
    target = WORK / "preview" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(repaired, target)
    roundtrip = pq.ParquetFile(target).read()
    assert roundtrip.equals(repaired)
    result["status"] = "verified_fee_only_rewrite"
    return result


if __name__ == "__main__":
    files = sorted((WORK / "collector").glob("*/onchain_fills/*/*.parquet"))
    results = [preview(path) for path in files]
    (WORK / "preview_repair_results.json").write_text(json.dumps(results, indent=2))
    print("preview files", len(results), "rows", sum(r["rows"] for r in results),
          "changed fees", sum(r["changed_fees"] for r in results),
          "unmatched", sum(r["unmatched"] for r in results), flush=True)
    print("all non-fee columns and row order preserved", all(r["status"] == "verified_fee_only_rewrite" for r in results), flush=True)
