"""Daemon-only trader CLI."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from trader import orchestrator
from trader.trading_mode import ExecutionMode


def test_orchestrator_cli_is_daemon_only() -> None:
    """The session child subcommand is gone; argparse requires daemon --mode."""
    parser = orchestrator.build_arg_parser()
    live = parser.parse_args(["daemon", "--mode", "live"])
    assert live.command == "daemon"
    assert live.mode == "live"
    paper = parser.parse_args(["daemon", "--mode", "paper"])
    assert paper.mode == "paper"
    with pytest.raises(SystemExit):
        parser.parse_args(["daemon"])
    with pytest.raises(SystemExit):
        parser.parse_args(["daemon", "--mode", "off"])
    with pytest.raises(SystemExit):
        parser.parse_args(["session"])


def test_orchestrator_main_captures_git_commit_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git HEAD is read once at boot from the repo-root .git, before the host loop."""
    calls: list[Path] = []

    def fake_git(git_dir: Path) -> str:
        calls.append(git_dir)
        return "deadbeef"

    async def fake_daemon(git_commit: str, mode: str) -> None:
        assert git_commit == "deadbeef"
        assert mode == "paper"

    monkeypatch.setattr(orchestrator, "read_git_head_commit", fake_git)
    monkeypatch.setattr(orchestrator, "run_daemon", fake_daemon)
    monkeypatch.setattr(orchestrator, "setup_logging", lambda: None)
    orchestrator.main(["daemon", "--mode", "paper"])
    assert len(calls) == 1
    git_dir = calls[0]
    assert git_dir.name == ".git"
    assert (git_dir.parent / "pyproject.toml").is_file()


def test_sigterm_runs_wallet_daemon_finally(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGTERM cancels the daemon task so the wallet host finally block runs."""
    stopped = False

    async def fake_wallet(git_commit: str, mode: ExecutionMode) -> None:
        nonlocal stopped
        del git_commit, mode
        try:
            await asyncio.Event().wait()
        finally:
            stopped = True

    monkeypatch.setattr(orchestrator, "run_wallet_daemon", fake_wallet)

    async def drive() -> None:
        loop = asyncio.get_running_loop()
        loop.call_later(0, lambda: os.kill(os.getpid(), signal.SIGTERM))
        await orchestrator.run_daemon("deadbeef", "live")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(drive())
    assert stopped
