"""`shared.utils.jsonl_io`: plain/gzip resolution, reads, content hash and size."""

import gzip
import hashlib
from pathlib import Path

from shared.utils.jsonl_io import (
    decompressed_size,
    open_maybe_gz,
    resolve_jsonl,
    sha256_maybe_gz,
)

LINES = '{"a":1}\n{"b":2}\n'


def _write_pair(directory: Path, name: str = "state.jsonl") -> Path:
    plain = directory / name
    plain.write_text(LINES, encoding="utf-8")
    with gzip.open(directory / f"{name}.gz", "wt", encoding="utf-8", newline="\n") as handle:
        handle.write(LINES)
    return plain


def test_resolve_prefers_plain_when_both_exist(tmp_path: Path) -> None:
    plain = _write_pair(tmp_path)
    assert resolve_jsonl(plain) == plain


def test_resolve_falls_back_to_gz(tmp_path: Path) -> None:
    plain = _write_pair(tmp_path)
    plain.unlink()
    assert resolve_jsonl(plain) == plain.with_name(plain.name + ".gz")


def test_resolve_missing_and_gz_suffix(tmp_path: Path) -> None:
    missing = tmp_path / "core_trace.jsonl"
    assert resolve_jsonl(missing) == missing
    gz = tmp_path / "core_trace.jsonl.gz"
    assert resolve_jsonl(gz) == gz


def test_open_maybe_gz_reads_plain_and_gz(tmp_path: Path) -> None:
    plain = _write_pair(tmp_path)
    gz = plain.with_name(plain.name + ".gz")
    with open_maybe_gz(plain) as handle:
        assert handle.read() == LINES
    with open_maybe_gz(gz) as handle:
        assert handle.read() == LINES


def test_sha256_and_size_are_content_based(tmp_path: Path) -> None:
    plain = _write_pair(tmp_path)
    gz = plain.with_name(plain.name + ".gz")
    expected = hashlib.sha256(LINES.encode()).hexdigest()
    assert sha256_maybe_gz(plain) == expected
    assert sha256_maybe_gz(gz) == expected
    assert decompressed_size(plain) == len(LINES.encode())
    assert decompressed_size(gz) == len(LINES.encode())
