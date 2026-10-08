"""Read plain and gzipped JSON objects and atomically write JSON files."""

import gzip
import json
from pathlib import Path
from typing import Any, cast


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"json is not an object: {path}")
    return cast(dict[str, Any], payload)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"gzip json is not an object: {path}")
    return cast(dict[str, Any], payload)
