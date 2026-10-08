"""Guarded per-match archive paths and the fsynced JSONL writer."""

import json
import math
import os
from pathlib import Path
from typing import TextIO, cast

from shared.constants.paths import TRADER_DIR
from trader.paths import EXECUTION_CLEANUP_FILENAME, MATCH_META_FILENAME
from trader.strict_json import (
    StrictJsonError,
    require_int,
    require_nonempty_str,
    require_object,
)


def sanitize_nonfinite(value: object) -> object:
    """Return value with every nonfinite float replaced by None, recursively."""
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {
            key: sanitize_nonfinite(item) for key, item in cast(dict[object, object], value).items()
        }
    if isinstance(value, list):
        return [sanitize_nonfinite(item) for item in cast(list[object], value)]
    return value


def validate_match_id(match_id: str) -> None:
    """Reject ids that are not a single relative path component, including `wallet`."""
    if not match_id or match_id in {".", "..", "wallet"} or "/" in match_id or "\\" in match_id:
        raise ValueError(f"invalid match id {match_id!r}: expected a single path component")


def match_archive_dir(root: Path, match_id: str) -> Path:
    """Return `<root>/<match_id>` after validating the id is one safe path component."""
    validate_match_id(match_id)
    return root / match_id


def archive_has_prior_session(archive_dir: Path) -> bool:
    """True when match.json exists. Feed-only files are not a session: GRID/Oddin write them before the first yield."""
    return (archive_dir / MATCH_META_FILENAME).exists()


def truncate_after_last_newline(path: Path) -> None:
    """Drop an unterminated crash tail; completed records are never rewritten."""
    with path.open("rb") as handle:
        data = handle.read()
    keep = data.rfind(b"\n") + 1
    if keep == len(data):
        return
    with path.open("r+b") as handle:
        handle.truncate(keep)
        os.fsync(handle.fileno())


class FsyncedJsonlWriter:
    """Append-only JSONL: crash-tail truncate on open, then write/flush/fsync per record."""

    def __init__(self, path: Path) -> None:
        """Open `path` in append mode, creating its directory and dropping a crash tail."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        if path.exists():
            truncate_after_last_newline(path)
        self._fresh = not path.exists() or path.stat().st_size == 0
        self._handle: TextIO = path.open("a", encoding="utf-8", newline="\n")

    @property
    def path(self) -> Path:
        """The archive file this writer appends to."""
        return self._path

    def is_fresh(self) -> bool:
        """True when the file held no completed record when this writer opened it."""
        return self._fresh

    def write_record(self, record: object) -> None:
        """Append one compact LF-terminated JSON record, then flush and fsync it."""
        payload = sanitize_nonfinite(record)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self._handle.write(line + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        """Flush and close the archive file; closing twice is a no-op."""
        self._handle.close()


def write_execution_cleanup(archive_dir: Path, match_id: str, condition_id: str) -> None:
    """Atomically write execution_cleanup.json once this market's orders are proven gone."""
    document = {
        "schema_version": 1,
        "match_id": match_id,
        "condition_id": condition_id,
    }
    target = archive_dir / EXECUTION_CLEANUP_FILENAME
    temp = archive_dir / f".{EXECUTION_CLEANUP_FILENAME}.tmp"
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    archive_dir.mkdir(parents=True, exist_ok=True)
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def cleanup_path(match_id: str) -> Path:
    """Path of this match's execution_cleanup.json."""
    return match_archive_dir(TRADER_DIR, match_id) / EXECUTION_CLEANUP_FILENAME


def own_execution_cleanup(match_id: str, condition_id: str) -> bool:
    """True when execution_cleanup.json is schema 1 and names this match and CID."""
    path = cleanup_path(match_id)
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        fields = require_object(loaded, "execution_cleanup.json")
        schema_version = require_int(fields, "schema_version", "execution_cleanup.json")
        stored_match = require_nonempty_str(fields, "match_id", "execution_cleanup.json")
        stored_cid = require_nonempty_str(fields, "condition_id", "execution_cleanup.json")
    except (OSError, ValueError, RecursionError, StrictJsonError):
        return False
    return schema_version == 1 and stored_match == match_id and stored_cid == condition_id
