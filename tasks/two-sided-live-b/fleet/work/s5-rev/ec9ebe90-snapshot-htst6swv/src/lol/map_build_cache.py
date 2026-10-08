"""Gzipped per-map prepare cache keyed by an input stamp stored in the file.

No TTL or size cap. Delete `LOL_MAP_BUILD_CACHE_DIR` by hand if it grows stale.
"""

import gzip
import hashlib
import math
import os
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

import msgspec

from lol.replay import parse_clob_tokens
from lol.types import LolLinkRow, MapBuild
from shared.constants.telonex import ONCHAIN_CHANNEL, TELONEX_BOOK_CHANNEL
from shared.utils.hashing import sha256_file
from shared.utils.telonex_capture import asset_channel_dir

_SRC_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def cache_version() -> str:
    """Hash src/ files of modules imported by prepare.

    Lazy so the import graph is complete. A later lazy import inside a function
    would make cold and warm stamps differ.
    """
    paths: set[Path] = set()
    for module in tuple(sys.modules.values()):
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str):
            continue
        path = Path(raw).resolve()
        if path.suffix != ".py" or not path.is_relative_to(_SRC_ROOT):
            continue
        paths.add(path)
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(str(path.relative_to(_SRC_ROOT)).encode())
        hasher.update(sha256_file(path).encode())
    return hasher.hexdigest()


@dataclass(frozen=True)
class CachedMapBuild:
    """One map's prepare result and the input stamp that produced it."""

    stamp: str
    build: MapBuild


def _archive_stamp(directory: Path, esports_game_id: str) -> tuple[int, int]:
    path = directory / f"{esports_game_id}.jsonl.gz"
    if not path.is_file():
        return (0, 0)
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _file_listing(directory: Path) -> list[tuple[str, int, int]]:
    if not directory.is_dir():
        return []
    rows: list[tuple[str, int, int]] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        rows.append((path.name, stat.st_size, stat.st_mtime_ns))
    rows.sort()
    return rows


def _catalog_stamp(item_catalog_dir: Path) -> list[tuple[str, str]]:
    if not item_catalog_dir.is_dir():
        return []
    return [
        (path.name, sha256_file(path))
        for path in sorted(item_catalog_dir.iterdir())
        if path.is_file()
    ]


def build_input_stamp(
    link: LolLinkRow,
    windows_dir: Path,
    details_dir: Path,
    telonex_root: Path,
    item_catalog_dir: Path,
) -> str:
    """Hash cache version, link row, archives, catalog bytes, and Telonex listings."""
    game_id = str(link["esports_game_id"])
    tokens = parse_clob_tokens(link)
    token_ids = () if tokens is None else (tokens.token_id_0, tokens.token_id_1)
    telonex_listing = [
        (channel, token_id, _file_listing(asset_channel_dir(telonex_root, channel, token_id)))
        for token_id in token_ids
        for channel in (TELONEX_BOOK_CHANNEL, ONCHAIN_CHANNEL)
    ]
    hasher = hashlib.sha256()
    hasher.update(cache_version().encode())
    hasher.update(msgspec.json.encode(link))
    hasher.update(repr(_archive_stamp(windows_dir, game_id)).encode())
    hasher.update(repr(_archive_stamp(details_dir, game_id)).encode())
    hasher.update(repr(_catalog_stamp(item_catalog_dir)).encode())
    hasher.update(repr(telonex_listing).encode())
    return hasher.hexdigest()


def map_cache_path(cache_dir: Path, esports_game_id: str) -> Path:
    """Gzipped msgspec JSON path for one map."""
    return cache_dir / f"{esports_game_id}.json.gz"


def read_cached_build(path: Path, stamp: str) -> MapBuild | None:
    """Return the stored MapBuild when the file exists, matches stamp, and decodes."""
    try:
        record = msgspec.json.decode(gzip.decompress(path.read_bytes()), type=CachedMapBuild)
    except (OSError, EOFError, gzip.BadGzipFile, msgspec.DecodeError, msgspec.ValidationError):
        return None
    if record.stamp != stamp:
        return None
    return record.build


def _require_finite_floats(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite float cannot be cached")
    values: tuple[object, ...]
    if isinstance(value, dict):
        values = tuple(cast(dict[object, object], value).values())
    elif isinstance(value, list | tuple):
        values = tuple(cast(list[object] | tuple[object, ...], value))
    else:
        return
    for item in values:
        _require_finite_floats(item)


def write_cached_build(path: Path, stamp: str, build: MapBuild) -> None:
    """Atomically write one gzipped CachedMapBuild."""
    _require_finite_floats(asdict(build))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(msgspec.json.encode(CachedMapBuild(stamp, build)))
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
