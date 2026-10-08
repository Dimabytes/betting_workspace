"""Live paper daemon: one process, one Engine, one wallet.

Consumes `cadence.poll_discoveries` inside WalletHost. Completion is a durable
match.json final, never an exit code.
"""

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from pathlib import Path

from shared.utils.log import setup_logging
from trader.host_resources import run_wallet_daemon
from trader.session_journal import read_git_head_commit
from trader.trading_mode import ExecutionMode, execution_mode

_GIT_DIR = Path(__file__).resolve().parents[2] / ".git"


async def run_daemon(git_commit: str, mode: ExecutionMode) -> None:
    """Compose and run the one-process wallet host. SIGTERM cancels so teardown runs."""
    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await run_wallet_daemon(git_commit, mode)


def build_arg_parser() -> argparse.ArgumentParser:
    """Daemon-only trader CLI; required --mode live|paper."""
    parser = argparse.ArgumentParser(prog="trader")
    subparsers = parser.add_subparsers(dest="command", required=True)
    daemon = subparsers.add_parser("daemon", help="run the live paper wallet daemon")
    daemon.add_argument("--mode", choices=("live", "paper"), required=True)
    return parser


def main(argv: list[str]) -> None:
    """Dispatch the wallet daemon. Git HEAD is captured once before the event loop."""
    setup_logging()
    args = build_arg_parser().parse_args(argv)
    if args.command != "daemon":
        raise SystemExit(2)
    git_commit = read_git_head_commit(_GIT_DIR)
    with suppress(asyncio.CancelledError):
        asyncio.run(run_daemon(git_commit, execution_mode(args.mode)))


if __name__ == "__main__":
    main(sys.argv[1:])
