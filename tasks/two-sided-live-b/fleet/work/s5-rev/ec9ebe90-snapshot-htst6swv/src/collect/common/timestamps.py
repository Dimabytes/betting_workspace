from typing import Any

from shared.utils.parsing import parse_ts


def require_ts(value: Any, label: str) -> int:
    ts = parse_ts(value)
    if ts is None:
        raise SystemExit(f"invalid {label}: {value}")
    return ts
