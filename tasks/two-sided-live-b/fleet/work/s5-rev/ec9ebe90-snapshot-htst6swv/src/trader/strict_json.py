"""Strict JSON field readers: exact runtime types, bool rejected as int, missing key fails."""

import math
from collections.abc import Mapping
from typing import cast


class StrictJsonError(ValueError):
    """One JSON field failed its exact-type or exact-key-set contract."""


def require_object(value: object, label: str) -> dict[str, object]:
    """Return `value` as a JSON object, or raise."""
    if type(value) is not dict:
        raise StrictJsonError(f"{label} must be an object")
    return cast(dict[str, object], value)


def require_exact_keys(fields: Mapping[str, object], keys: frozenset[str], label: str) -> None:
    """Require exactly `keys`, so a missing nullable key is corruption, not a default."""
    if frozenset(fields) != keys:
        raise StrictJsonError(f"{label} keys do not match the expected set")


def require_list(fields: Mapping[str, object], key: str, label: str) -> list[object]:
    """Return the list value of one field, or raise."""
    value = fields.get(key)
    if type(value) is not list:
        raise StrictJsonError(f"{label} {key!r} must be a list")
    return cast(list[object], value)


def require_str_list(fields: Mapping[str, object], key: str, label: str) -> list[str]:
    """Return the list-of-strings value of one field, or raise."""
    items = require_list(fields, key, label)
    if any(type(item) is not str for item in items):
        raise StrictJsonError(f"{label} {key!r} must be a list of strings")
    return cast(list[str], items)


def require_str(fields: Mapping[str, object], key: str, label: str) -> str:
    """Return the exact-string value of one field, or raise."""
    value = fields.get(key)
    if type(value) is not str:
        raise StrictJsonError(f"{label} {key!r} must be a string")
    return value


def require_nonempty_str(fields: Mapping[str, object], key: str, label: str) -> str:
    """Return the nonblank string value of one field, or raise."""
    value = require_str(fields, key, label)
    if not value.strip():
        raise StrictJsonError(f"{label} {key!r} must be a nonempty string")
    return value


def require_int(fields: Mapping[str, object], key: str, label: str) -> int:
    """Return the exact-int value of one field, or raise (bool is rejected)."""
    value = fields.get(key)
    if type(value) is not int:
        raise StrictJsonError(f"{label} {key!r} must be an integer")
    return value


def require_number(fields: Mapping[str, object], key: str, label: str) -> float:
    """Return a finite JSON number. Bool is rejected."""
    value = fields.get(key)
    if type(value) is bool or (type(value) is not int and type(value) is not float):
        raise StrictJsonError(f"{label} {key!r} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise StrictJsonError(f"{label} {key!r} must be a finite number")
    return number


def require_bool(fields: Mapping[str, object], key: str, label: str) -> bool:
    """Return the exact-bool value of one field, or raise."""
    value = fields.get(key)
    if type(value) is not bool:
        raise StrictJsonError(f"{label} {key!r} must be a boolean")
    return value


def require_nullable_str(fields: Mapping[str, object], key: str, label: str) -> str | None:
    """Return the string-or-null value of one field (a missing key is null), or raise."""
    value = fields.get(key)
    if value is None:
        return None
    if type(value) is not str:
        raise StrictJsonError(f"{label} {key!r} must be a string or null")
    return value


def require_nullable_nonempty_str(fields: Mapping[str, object], key: str, label: str) -> str | None:
    """Return the nonblank-string-or-null value of one field (a missing key is null)."""
    value = require_nullable_str(fields, key, label)
    if value is not None and not value.strip():
        raise StrictJsonError(f"{label} {key!r} must be a nonempty string or null")
    return value


def require_nullable_int(fields: Mapping[str, object], key: str, label: str) -> int | None:
    """Return the int-or-null value of one field (a missing key is null), or raise."""
    value = fields.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise StrictJsonError(f"{label} {key!r} must be an integer or null")
    return value


def require_nullable_number(fields: Mapping[str, object], key: str, label: str) -> float | None:
    """Return a finite JSON number or null (a missing key is null). Bool is rejected."""
    value = fields.get(key)
    if value is None:
        return None
    if type(value) is bool or (type(value) is not int and type(value) is not float):
        raise StrictJsonError(f"{label} {key!r} must be a finite number or null")
    number = float(value)
    if not math.isfinite(number):
        raise StrictJsonError(f"{label} {key!r} must be a finite number or null")
    return number


def require_nullable_bool(fields: Mapping[str, object], key: str, label: str) -> bool | None:
    """Return the boolean-or-null value of one field (a missing key is null), or raise."""
    value = fields.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise StrictJsonError(f"{label} {key!r} must be a boolean or null")
    return value
