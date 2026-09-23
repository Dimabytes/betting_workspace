from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
paths = [
    ROOT / "data/lol/processed/datasets/training.parquet",
    ROOT / "data/lol/processed/datasets/validation.parquet",
    ROOT / "data/lol/processed/datasets/production_training.parquet",
    ROOT / "data/lol/processed/datasets/split.parquet",
    ROOT / "data/lol/processed/datasets/audit.parquet",
    ROOT / "data/lol/processed/datasets/backtest_audit.parquet",
    ROOT / "data/experiments/lol-horizon-train/datasets/training.parquet",
    ROOT / "data/experiments/lol-horizon-train/datasets/validation.parquet",
    ROOT / "data/experiments/lol-horizon-train/datasets/split.parquet",
    ROOT / "data/lol/processed/lolesports_links/links.parquet",
    ROOT / "data/lol/processed/universe/markets.parquet",
    ROOT / "data/new_processed/dataset/training_dataset.parquet",
    ROOT / "data/new_processed/dataset/validation_dataset.parquet",
    ROOT / "data/new_model/research/split.parquet",
]

for path in paths:
    parquet = pq.ParquetFile(path)
    print(f"\n{path.relative_to(ROOT)} rows={parquet.metadata.num_rows}")
    print("columns:", ", ".join(parquet.schema.names))
    table = parquet.read_row_group(0).slice(0, 2)
    print(table.to_pylist())
