"""Temporary staging directories for atomic artifact publication."""

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def staging_directory(parent: Path, prefix: str) -> Generator[Path]:
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix=prefix) as path:
        yield Path(path)
