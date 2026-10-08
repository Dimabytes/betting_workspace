"""Process-wide exclusive flock. The caller chooses which path to lock."""

import fcntl
import os
from pathlib import Path


class FileLockHeld(Exception):
    """Another process already holds this flock."""


class FileLock:
    """Exclusive flock held for the process lifetime."""

    def __init__(self, fd: int, path: Path) -> None:
        """Keep the lock fd so close() can unlock it."""
        self._fd = fd
        self.path = path

    def close(self) -> None:
        """Unlock and close the fd."""
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)


def acquire_file_lock(path: Path) -> FileLock:
    """Non-blocking exclusive flock. Raises FileLockHeld if busy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise FileLockHeld(str(path)) from None
    return FileLock(fd, path)
