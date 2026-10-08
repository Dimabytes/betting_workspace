"""Tolerant archive readers shared by the tape and game-state replay loaders.

Journals and match.json are read tolerantly: blank lines, torn tail lines,
and mistyped values degrade to None rather than raising.
"""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import cast

from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.match_time import parse_utc
from trader.paths import MATCH_META_FILENAME


def as_float(value: object) -> float | None:
    """int/float as float, except bool; nulls and anything else are None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    return None


def as_int(value: object) -> int | None:
    """int as int, except bool; nulls and anything else are None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def as_str(value: object) -> str | None:
    """str as str; nulls and anything else are None."""
    return value if isinstance(value, str) else None


def iter_json_objects(path: Path) -> Iterator[dict[str, object]]:
    """Yield each well-formed JSON object line; skip blanks and torn tail lines."""
    try:
        handle = open_maybe_gz(path)
    except OSError:
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(document, dict):
                yield cast(dict[str, object], document)


def read_match_document(archive_dir: Path) -> dict[str, object] | None:
    """Parsed match.json, or None when it is missing or malformed."""
    try:
        document = json.loads((archive_dir / MATCH_META_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return cast(dict[str, object], document) if isinstance(document, dict) else None


def market_block(document: dict[str, object]) -> dict[str, object] | None:
    """match.json market block, or None when it is missing or mistyped."""
    market = document.get("market")
    return cast(dict[str, object], market) if isinstance(market, dict) else None


def horn_at_utc(document: dict[str, object]) -> datetime | None:
    """match.json horn_at_utc as aware UTC, or None when missing/malformed."""
    raw = as_str(document.get("horn_at_utc"))
    if raw is None:
        return None
    try:
        return parse_utc(raw)
    except ValueError:
        return None
