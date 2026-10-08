"""Run the VPS archive syncs used by the live inspector."""

import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SYNC_TIMEOUT_SECONDS = 10 * 60
_PIPE_CHUNK_BYTES = 4096
_SELECT_SLICE_SECONDS = 0.25
CollectorGame = Literal["dota", "lol"]


@dataclass(frozen=True)
class SyncResult:
    """Captured result of one archive sync attempt."""

    returncode: int | None
    output: str
    error: str
    timed_out: bool


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the sync script and its rsync child."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _emit_bytes(data: bytes, chunks: list[str], on_output: Callable[[str], None]) -> None:
    """Decode one pipe chunk and forward it to the Streamlit log."""
    if not data:
        return
    text = data.decode(errors="replace")
    chunks.append(text)
    on_output(text)


def _drain_pipe(fd: int, chunks: list[str], on_output: Callable[[str], None]) -> None:
    """Read whatever is left on the pipe after the child exits or is killed."""
    while True:
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return
        data = os.read(fd, _PIPE_CHUNK_BYTES)
        if not data:
            return
        _emit_bytes(data, chunks, on_output)


def _stream_process(
    process: subprocess.Popen[bytes],
    timeout_seconds: float,
    on_output: Callable[[str], None],
) -> SyncResult:
    """Forward stdout/stderr until the child exits or the timeout fires."""
    if process.stdout is None:
        returncode = process.wait()
        return SyncResult(returncode=returncode, output="", error="", timed_out=False)
    fd = process.stdout.fileno()
    deadline = time.monotonic() + timeout_seconds
    chunks: list[str] = []
    timed_out = False
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process_group(process)
            timed_out = True
            break
        ready, _, _ = select.select([fd], [], [], min(remaining, _SELECT_SLICE_SECONDS))
        if ready:
            _emit_bytes(os.read(fd, _PIPE_CHUNK_BYTES), chunks, on_output)
    _drain_pipe(fd, chunks, on_output)
    returncode = process.wait()
    output = "".join(chunks)
    if timed_out:
        return SyncResult(returncode=None, output=output, error="", timed_out=True)
    return SyncResult(returncode=returncode, output=output, error="", timed_out=False)


def _run_sync_script(
    repo_root: Path,
    script_name: str,
    extra_args: Sequence[str],
    timeout_seconds: float,
    on_output: Callable[[str], None],
) -> SyncResult:
    """Run one `scripts/*.py` sync with live stdout (including rsync \\r progress)."""
    script = repo_root / "scripts" / script_name
    command = [sys.executable, "-u", str(script), *extra_args]
    try:
        with subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            return _stream_process(process, timeout_seconds, on_output)
    except OSError as exc:
        return SyncResult(returncode=None, output="", error=str(exc), timed_out=False)


def sync_live_matches(
    repo_root: Path,
    timeout_seconds: float,
    on_output: Callable[[str], None],
) -> SyncResult:
    """Run the existing trader sync script from the repository root."""
    return _run_sync_script(repo_root, "sync_trader.py", (), timeout_seconds, on_output)


def sync_collector_parquet(
    repo_root: Path,
    game: CollectorGame,
    timeout_seconds: float,
    on_output: Callable[[str], None],
) -> SyncResult:
    """Run the collector parquet sync for one game from the repository root."""
    return _run_sync_script(
        repo_root,
        "sync_collector_parquet.py",
        ("--game", game),
        timeout_seconds,
        on_output,
    )
