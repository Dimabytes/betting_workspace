import gzip
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from shared.utils.jsonl_io import resolve_jsonl

TAIL_MAX_PATHS = 128
TAIL_BYTES = 256 * 1024
TAIL_MAX_RECORDS = 512
HEAD_BYTES = 64 * 1024
HEAD_MAX_RECORDS = 8


@dataclass(frozen=True)
class TailView:
    path: Path
    mtime_ns: int
    size: int
    compressed: bool
    truncated_start: bool
    records_kept: int
    malformed: int
    saw_session_end: bool
    last_signal: dict[str, object] | None
    last_quote: dict[str, object] | None
    last_fill: dict[str, object] | None
    last_error: dict[str, object] | None
    last_record: dict[str, object] | None


@dataclass(frozen=True)
class RecordTail:
    path: Path
    mtime_ns: int
    size: int
    compressed: bool
    truncated_start: bool
    malformed: int
    read_error: bool
    records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _HeadEntry:
    mtime_ns: int
    size: int
    records: tuple[dict[str, object], ...]


def _json_record(line: str) -> dict[str, object] | None:
    if not line.strip():
        return None
    try:
        document = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    return cast(dict[str, object], document)


def _head_records(lines: Iterable[str]) -> tuple[dict[str, object], ...]:
    out: list[dict[str, object]] = []
    for line in lines:
        if len(out) >= HEAD_MAX_RECORDS:
            break
        record = _json_record(line)
        if record is not None:
            out.append(record)
    return tuple(out)


def _tail_records(lines: Iterable[str], limit: int) -> tuple[list[dict[str, object]], int]:
    records: deque[dict[str, object]] = deque(maxlen=limit)
    malformed = 0
    for line in lines:
        if not line.strip():
            continue
        record = _json_record(line)
        if record is None:
            malformed += 1
        else:
            records.append(record)
    return list(records), malformed


def _build_tail(read: RecordTail) -> TailView:
    last_signal: dict[str, object] | None = None
    last_quote: dict[str, object] | None = None
    last_fill: dict[str, object] | None = None
    last_error: dict[str, object] | None = None
    last_record: dict[str, object] | None = None
    saw_end = False
    for record in read.records:
        last_record = record
        kind = record.get("kind")
        if kind == "signal":
            last_signal = record
        elif kind == "quote":
            last_quote = record
        elif kind in ("fill", "late_fill"):
            last_fill = record
        elif kind == "trading_error":
            last_error = record
        elif kind == "session_end":
            saw_end = True
    return TailView(
        path=read.path,
        mtime_ns=read.mtime_ns,
        size=read.size,
        compressed=read.compressed,
        truncated_start=read.truncated_start,
        records_kept=len(read.records),
        malformed=read.malformed,
        saw_session_end=saw_end,
        last_signal=last_signal,
        last_quote=last_quote,
        last_fill=last_fill,
        last_error=last_error,
        last_record=last_record,
    )


class TailCache:
    def __init__(
        self,
        *,
        max_paths: int = TAIL_MAX_PATHS,
        tail_bytes: int = TAIL_BYTES,
        max_records: int = TAIL_MAX_RECORDS,
    ) -> None:
        self._max_paths = max_paths
        self._tail_bytes = tail_bytes
        self._max_records = max_records
        self._tails: dict[Path, TailView] = {}
        self._record_tails: dict[Path, RecordTail] = {}
        self._heads: dict[Path, _HeadEntry] = {}

    def _evict[T](self, cache: dict[Path, T], keep: Path) -> None:
        while len(cache) > self._max_paths:
            for key in cache:
                if key != keep:
                    del cache[key]
                    break
            else:
                break

    def _read_tail(self, resolved: Path) -> RecordTail:
        raw: RecordTail | None = None
        for _attempt in range(2):
            stat = resolved.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            raw = self._read_resolved(resolved, stat.st_mtime_ns, stat.st_size)
            try:
                after = resolved.stat()
            except OSError:
                return raw
            if (after.st_mtime_ns, after.st_size) == stamp:
                self._record_tails[resolved] = raw
                self._tails[resolved] = _build_tail(raw)
                self._evict(self._record_tails, resolved)
                self._evict(self._tails, resolved)
                return raw
        assert raw is not None
        return raw

    def read(self, path: Path) -> TailView | None:
        raw = self.read_records(path)
        if raw is None:
            return None
        view = self._tails.get(raw.path)
        if view is not None and (view.mtime_ns, view.size) == (raw.mtime_ns, raw.size):
            return view
        return _build_tail(raw)

    def read_records(self, path: Path) -> RecordTail | None:
        resolved = resolve_jsonl(path)
        previous = self._record_tails.get(resolved)
        try:
            stat = resolved.stat()
        except OSError:
            self._tails.pop(resolved, None)
            self._record_tails.pop(resolved, None)
            self._heads.pop(resolved, None)
            return None
        if previous is not None and (previous.mtime_ns, previous.size) == (
            stat.st_mtime_ns,
            stat.st_size,
        ):
            return previous
        try:
            raw = self._read_tail(resolved)
        except OSError:
            return previous
        if self._record_tails.get(resolved) is not raw:
            return previous
        return self._record_tails.get(resolved)

    def _read_resolved(self, resolved: Path, mtime_ns: int, size: int) -> RecordTail:
        if resolved.suffix == ".gz":
            return self._read_gz_tail(resolved, mtime_ns, size)
        try:
            with resolved.open("rb") as handle:
                truncated = size > self._tail_bytes
                if truncated:
                    handle.seek(size - self._tail_bytes)
                data = handle.read()
        except OSError:
            return RecordTail(resolved, mtime_ns, size, False, False, 0, True, ())
        text = data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        if truncated:
            lines = lines[1:]
        if lines and not text.endswith("\n"):
            lines = lines[:-1]
        kept, malformed = _tail_records(lines, self._max_records)
        return RecordTail(resolved, mtime_ns, size, False, truncated, malformed, False, tuple(kept))

    def _read_gz_tail(self, resolved: Path, mtime_ns: int, size: int) -> RecordTail:
        try:
            with gzip.open(resolved, "rt", encoding="utf-8", newline="\n") as handle:
                kept, malformed = _tail_records(handle, self._max_records)
        except OSError:
            return RecordTail(resolved, mtime_ns, size, True, False, 0, True, ())
        return RecordTail(resolved, mtime_ns, size, True, False, malformed, False, tuple(kept))

    def read_head(self, path: Path) -> tuple[dict[str, object], ...] | None:
        resolved = resolve_jsonl(path)
        try:
            stat = resolved.stat()
        except OSError:
            self._heads.pop(resolved, None)
            return None
        cached = self._heads.get(resolved)
        if cached is not None and (cached.mtime_ns, cached.size) == (
            stat.st_mtime_ns,
            stat.st_size,
        ):
            return cached.records
        records = self._read_head_resolved(resolved)
        if records is None:
            return None
        self._heads[resolved] = _HeadEntry(stat.st_mtime_ns, stat.st_size, records)
        self._evict(self._heads, resolved)
        return records

    def _read_head_resolved(self, resolved: Path) -> tuple[dict[str, object], ...] | None:
        if resolved.suffix == ".gz":
            try:
                with gzip.open(resolved, "rt", encoding="utf-8", newline="\n") as handle:
                    return _head_records(handle)
            except OSError:
                return None
        try:
            with resolved.open("rb") as handle:
                data = handle.read(HEAD_BYTES)
        except OSError:
            return None
        text = data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        if lines and not text.endswith("\n"):
            lines = lines[:-1]
        return _head_records(lines)
