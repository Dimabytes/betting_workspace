"""Shared rsync-pull plumbing for the VPS sync scripts.

Each sync script owns its constants, its rsync flags and its coverage report.
Everything else — the argv tail, the CLI, the destination prep and the child
process — lives here.
"""

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SSH_HOST = "sun"


@dataclass(frozen=True)
class RsyncArgs:
    """Parsed CLI for one VPS pull."""

    ssh_host: str
    remote: str
    dest: Path
    dry_run: bool


def rsync_argv(
    ssh_host: str,
    remote: str,
    dest: Path,
    flags: list[str],
    dry_run: bool,
) -> list[str]:
    """One rsync: `ssh_host:remote/` → `dest/` with the caller's flags; never deletes."""
    # Apple rsync 2.6 has -v/--progress/-n, not GNU --info= or --dry-run.
    argv = ["rsync", *flags, "--partial", "--progress"]
    if dry_run:
        argv.append("-n")
    argv.extend([f"{ssh_host}:{remote.rstrip('/')}/", f"{dest}/"])
    return argv


def run_rsync_status(argv: list[str]) -> int:
    """Run rsync with the inherited TTY and return its exit code."""
    print(" ".join(argv), flush=True)
    return subprocess.run(argv, check=False).returncode


def run_rsync(argv: list[str]) -> None:
    """Run rsync; nonzero exit aborts with SystemExit."""
    rc = run_rsync_status(argv)
    if rc != 0:
        raise SystemExit(rc)


def parse_argv(
    argv: list[str],
    description: str | None,
    remote_default: str,
    dest_default: Path,
) -> RsyncArgs:
    """CLI: `--ssh-host`, `--remote` root, local `--dest`, optional `--dry-run`."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    parser.add_argument("--remote", default=remote_default)
    parser.add_argument("--dest", type=Path, default=dest_default)
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(argv)
    return RsyncArgs(
        ssh_host=parsed.ssh_host,
        remote=parsed.remote,
        dest=parsed.dest,
        dry_run=parsed.dry_run,
    )


def prepared_dest(dest: Path) -> Path:
    """Expand, resolve and create the local destination directory."""
    resolved = dest.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved
