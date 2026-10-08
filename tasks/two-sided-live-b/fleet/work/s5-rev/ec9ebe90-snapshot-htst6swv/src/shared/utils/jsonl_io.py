"""Read .jsonl archives transparently whether they are plain or gzip-compressed."""

import gzip
import hashlib
import os
from pathlib import Path
from typing import TextIO

from shared.utils.hashing import sha256_file


def resolve_jsonl(path: Path) -> Path:
    """The file to read: `path` when it exists, else `path + ".gz"`, else `path`."""
    if path.exists() or path.suffix == ".gz":
        return path
    gz = path.with_name(path.name + ".gz")
    return gz if gz.exists() else path


def open_maybe_gz(path: Path) -> TextIO:
    """Text stream over `resolve_jsonl(path)`; decompresses `.gz`."""
    resolved = resolve_jsonl(path)
    if resolved.suffix == ".gz":
        return gzip.open(resolved, "rt", encoding="utf-8", newline="\n")
    return resolved.open("r", encoding="utf-8", newline="\n")


def sha256_maybe_gz(path: Path) -> str:
    """sha256 of the content readers see, so gzipping a file keeps its hash."""
    resolved = resolve_jsonl(path)
    if resolved.suffix == ".gz":
        with gzip.open(resolved, "rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    return sha256_file(resolved)


def decompressed_size(path: Path) -> int:
    """Content byte size: stat for plain files, the ISIZE trailer for `.gz`.

    ponytail: ISIZE is the last member's size mod 2**32 — exact for the
    single-member archives the compressor writes (all well under 4 GiB). A
    multi-member file just fails the size check and re-extracts.
    """
    resolved = resolve_jsonl(path)
    if resolved.suffix != ".gz":
        return resolved.stat().st_size
    with resolved.open("rb") as handle:
        handle.seek(-4, os.SEEK_END)
        return int.from_bytes(handle.read(4), "little")
