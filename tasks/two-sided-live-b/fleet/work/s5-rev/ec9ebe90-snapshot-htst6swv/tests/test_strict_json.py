"""Tests for the trader strict JSON decoder used by untrusted-document readers."""

import pytest

from trader import strict_json


def test_require_object_accepts_an_object_and_rejects_everything_else() -> None:
    """Only a dict is an object; a list, a scalar and null all raise."""
    assert strict_json.require_object({"a": 1}, "document") == {"a": 1}
    for value in ([1, 2], "text", 3, None, True):
        with pytest.raises(strict_json.StrictJsonError, match="document must be an object"):
            strict_json.require_object(value, "document")


def test_require_exact_keys_rejects_missing_and_extra_keys() -> None:
    """A missing nullable key is corruption, and an unknown key is rejected too."""
    keys = frozenset({"a", "b"})
    strict_json.require_exact_keys({"a": 1, "b": None}, keys, "record")
    for fields in ({"a": 1}, {"a": 1, "b": None, "c": 2}, {}):
        with pytest.raises(strict_json.StrictJsonError, match="record keys do not match"):
            strict_json.require_exact_keys(fields, keys, "record")


def test_require_list_returns_the_list_and_rejects_other_shapes() -> None:
    """A list value passes; a tuple, an object and a missing key raise."""
    assert strict_json.require_list({"items": [1, "a"]}, "items", "record") == [1, "a"]
    for fields in ({"items": (1, 2)}, {"items": {"a": 1}}, {}):
        with pytest.raises(strict_json.StrictJsonError, match="'items' must be a list"):
            strict_json.require_list(fields, "items", "record")


def test_require_str_list_rejects_a_non_string_item() -> None:
    """Every item must be a string; one number rejects the whole list."""
    assert strict_json.require_str_list({"f": ["a", "b"]}, "f", "record") == ["a", "b"]
    assert strict_json.require_str_list({"f": []}, "f", "record") == []
    with pytest.raises(strict_json.StrictJsonError, match="'f' must be a list of strings"):
        strict_json.require_str_list({"f": ["a", 2]}, "f", "record")
    with pytest.raises(strict_json.StrictJsonError, match="'f' must be a list"):
        strict_json.require_str_list({"f": "ab"}, "f", "record")


def test_require_str_needs_an_exact_string() -> None:
    """Only str passes; a missing key, null and a number raise."""
    assert strict_json.require_str({"name": " x "}, "name", "record") == " x "
    assert strict_json.require_str({"name": ""}, "name", "record") == ""
    for fields in ({"name": None}, {"name": 5}, {}):
        with pytest.raises(strict_json.StrictJsonError, match="'name' must be a string"):
            strict_json.require_str(fields, "name", "record")


def test_require_nonempty_str_rejects_blank_strings() -> None:
    """A whitespace-only string is not a usable identifier."""
    assert strict_json.require_nonempty_str({"name": "x"}, "name", "record") == "x"
    with pytest.raises(strict_json.StrictJsonError, match="'name' must be a nonempty string"):
        strict_json.require_nonempty_str({"name": "   "}, "name", "record")


def test_require_int_rejects_bool_and_float() -> None:
    """`True` is an int at runtime, so the exact-type check must reject it."""
    assert strict_json.require_int({"n": 7}, "n", "record") == 7
    assert strict_json.require_int({"n": -1}, "n", "record") == -1
    for fields in ({"n": True}, {"n": False}, {"n": 1.0}, {"n": "1"}, {}):
        with pytest.raises(strict_json.StrictJsonError, match="'n' must be an integer"):
            strict_json.require_int(fields, "n", "record")


def test_require_number_accepts_int_and_float_and_rejects_bool_and_nan() -> None:
    """JSON numbers pass; bool, NaN, Infinity, strings and a missing key raise."""
    assert strict_json.require_number({"n": 7}, "n", "record") == 7.0
    assert strict_json.require_number({"n": 1.5}, "n", "record") == 1.5
    for fields in (
        {"n": True},
        {"n": False},
        {"n": float("nan")},
        {"n": float("inf")},
        {"n": "1"},
        {},
    ):
        with pytest.raises(strict_json.StrictJsonError, match="'n' must be a finite number"):
            strict_json.require_number(fields, "n", "record")


def test_require_bool_rejects_int_and_string() -> None:
    """Only True/False pass the boolean contract."""
    assert strict_json.require_bool({"flag": False}, "flag", "record") is False
    for fields in ({"flag": 1}, {"flag": "true"}, {"flag": None}, {}):
        with pytest.raises(strict_json.StrictJsonError, match="'flag' must be a boolean"):
            strict_json.require_bool(fields, "flag", "record")


def test_require_nullable_str_accepts_explicit_null_and_a_missing_key() -> None:
    """Null and a missing key both read as None; a wrong type raises.

    Forbidding a missing key is `require_exact_keys`' job, so the nullable
    helpers stay usable for documents whose optional fields may be absent.
    """
    assert strict_json.require_nullable_str({"id": None}, "id", "record") is None
    assert strict_json.require_nullable_str({}, "id", "record") is None
    assert strict_json.require_nullable_str({"id": "7"}, "id", "record") == "7"
    with pytest.raises(strict_json.StrictJsonError, match="'id' must be a string or null"):
        strict_json.require_nullable_str({"id": 7}, "id", "record")


def test_require_nullable_nonempty_str_rejects_blank_but_keeps_null() -> None:
    """Null stays null; a blank string is corruption."""
    assert strict_json.require_nullable_nonempty_str({"q": None}, "q", "record") is None
    with pytest.raises(strict_json.StrictJsonError, match="'q' must be a nonempty string or null"):
        strict_json.require_nullable_nonempty_str({"q": " "}, "q", "record")


def test_require_nullable_int_rejects_bool_but_keeps_null() -> None:
    """Null passes; `True` never does."""
    assert strict_json.require_nullable_int({"n": None}, "n", "record") is None
    assert strict_json.require_nullable_int({"n": 3}, "n", "record") == 3
    with pytest.raises(strict_json.StrictJsonError, match="'n' must be an integer or null"):
        strict_json.require_nullable_int({"n": True}, "n", "record")


def test_require_nullable_number_rejects_bool_and_keeps_null() -> None:
    """Null passes; an int or float is a finite number; bool is not."""
    assert strict_json.require_nullable_number({"n": None}, "n", "record") is None
    assert strict_json.require_nullable_number({"n": 3}, "n", "record") == 3.0
    assert strict_json.require_nullable_number({"n": 1.5}, "n", "record") == 1.5
    with pytest.raises(strict_json.StrictJsonError, match="'n' must be a finite number or null"):
        strict_json.require_nullable_number({"n": True}, "n", "record")


def test_require_nullable_bool_accepts_explicit_null() -> None:
    """Null passes; an int never does."""
    assert strict_json.require_nullable_bool({"open": None}, "open", "record") is None
    assert strict_json.require_nullable_bool({"open": True}, "open", "record") is True
    with pytest.raises(strict_json.StrictJsonError, match="'open' must be a boolean or null"):
        strict_json.require_nullable_bool({"open": 1}, "open", "record")


def test_strict_json_error_is_a_value_error() -> None:
    """Callers that already catch ValueError keep working unchanged."""
    assert issubclass(strict_json.StrictJsonError, ValueError)
