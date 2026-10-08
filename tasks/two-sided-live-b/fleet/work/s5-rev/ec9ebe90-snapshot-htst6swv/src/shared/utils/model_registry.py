"""Read, write, and publish named model directories."""

import shutil
from pathlib import Path
from typing import cast

from shared.types.model import ModelMeta
from shared.utils.json_io import read_json, write_json

MODEL_META_FILENAME = "model.json"


def read_model_meta(path: Path) -> ModelMeta:
    """Read model metadata from JSON."""
    return cast(ModelMeta, read_json(path))


def write_model_meta(meta: ModelMeta, path: Path) -> None:
    """Write model metadata as JSON."""
    write_json(path, meta)


def _archive_target(archive_dir: Path, name: str) -> Path:
    """First free archive directory: <name>, then <name>_2, <name>_3, ..."""
    candidate = archive_dir / name
    suffix = 2
    while candidate.exists():
        candidate = archive_dir / f"{name}_{suffix}"
        suffix += 1
    return candidate


def staging_model_dir(live_dir: Path) -> Path:
    """Create an empty staging sibling of one live model dir, dropping a leftover one."""
    staging_dir = live_dir.with_name(f".next-{live_dir.name}")
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    return staging_dir


def publish_model_dir(staging_dir: Path, live_dir: Path, archive_dir: Path) -> str | None:
    """Archive live_dir under archive_dir, then move a fully written staging_dir in its place.

    Both moves are renames on one filesystem, so a reader of live_dir sees either the
    whole old catalog or the whole new one, never a mixed members / model.json pair.
    A live_dir without model.json is a stale leftover: it is deleted, not archived.
    Returns the archived directory name, or None on a first publish.
    """
    if not (live_dir / MODEL_META_FILENAME).is_file():
        if live_dir.exists():
            shutil.rmtree(live_dir)
        live_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.rename(live_dir)
        return None
    archived_name = read_model_meta(live_dir / MODEL_META_FILENAME)["name"]
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_dir = _archive_target(archive_dir, archived_name)
    live_dir.rename(archived_dir)
    staging_dir.rename(live_dir)
    return archived_dir.name
