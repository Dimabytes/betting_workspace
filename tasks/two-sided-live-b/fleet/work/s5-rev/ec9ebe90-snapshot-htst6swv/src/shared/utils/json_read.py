"""Tolerant JSON field readers shared by feed, archive, and scraper parsers."""

from collections.abc import Mapping
from typing import cast


def as_map(value: object) -> dict[str, object] | None:
    """JSON object, or None."""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def try_int(value: object) -> int | None:
    """JSON number or decimal string, else None. Bool is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            return None
    return None


def read_str(raw: Mapping[str, object], key: str) -> str:
    """Read a JSON string, or empty when missing."""
    value = raw.get(key)
    return str(value) if value is not None else ""
