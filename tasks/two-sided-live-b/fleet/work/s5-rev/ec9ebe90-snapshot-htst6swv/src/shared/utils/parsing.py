"""Parse timestamps and list cells from source records."""

import json
from datetime import datetime
from typing import Any, cast

import numpy as np
import pandas as pd


def isna(value: Any) -> bool:
    """True when a *scalar* is None/NaN/NaT. Passing a Series/array is a bug."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def notna(value: Any) -> bool:
    return not isna(value)


def parse_ts(value: Any) -> int | None:
    """Parse unix seconds from int/float/ISO string; None if missing or invalid."""
    if value is None or value == "":
        return None
    if isna(value):
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return int(value / 1000)
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return parse_ts(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp())


def opt_str(value: Any) -> str | None:
    """A non-empty string cell, or None for pandas nulls and blanks."""
    if isna(value):
        return None
    text = str(value).strip()
    return text or None


def opt_int(value: Any) -> int | None:
    """An int cell, or None for missing/NaN."""
    if not isinstance(value, (int, float, np.integer, np.floating)) or isna(value):
        return None
    return int(cast("int | float", value))


def opt_float(value: Any) -> float | None:
    """A float cell, or None for missing/NaN."""
    if not isinstance(value, (int, float, np.integer, np.floating)) or isna(value):
        return None
    return float(cast("int | float", value))


def parse_steam_match_id(value: Any) -> int | None:
    """Steam match id as int; match.json and the archive index store it as a string."""
    if isinstance(value, (int, np.integer)):
        return int(cast(int, value))
    text = opt_str(value)
    return int(text) if text is not None and text.isdigit() else None


def opt_bool(value: Any) -> bool | None:
    """A bool cell (including numpy bools), or None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, np.bool_):
        return cast(bool, value.item())
    return None


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return cast(list[Any], value)
    if isinstance(value, tuple):
        return list(cast(tuple[Any, ...], value))
    if value is None:
        return []
    if isna(value):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return cast(list[Any], parsed) if isinstance(parsed, list) else [parsed]
    return [value]
