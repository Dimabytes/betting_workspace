#!/usr/bin/env python3
"""Hold an exclusive flock around a command. The OS drops the lock when the process exits."""

import fcntl
import os
import sys


def main() -> None:
    if len(sys.argv) < 5 or sys.argv[1] not in ("--wait", "--fail") or sys.argv[3] != "--":
        print("usage: run_locked.py (--wait|--fail) LOCK -- CMD...", file=sys.stderr)
        raise SystemExit(2)
    mode, lock_path, cmd = sys.argv[1], sys.argv[2], sys.argv[4:]
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if mode == "--fail" else 0)
    try:
        fcntl.flock(fd, flags)
    except BlockingIOError:
        print(f"{lock_path} held by another run", file=sys.stderr)
        raise SystemExit(1) from None
    os.set_inheritable(fd, True)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
