"""Pytest hooks that keep the suite off the live Telegram Bot API and off tmpfs."""

import os
from pathlib import Path

import pytest

from trader import notify


def _tmp_is_tmpfs() -> bool:
    """True when /tmp is tmpfs (the VPS): pytest leftovers would sit in RAM."""
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return False
    for line in mounts.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "/tmp" and fields[2] == "tmpfs":
            return True
    return False


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> None:
    """PYTEST_N opts into xdist workers per host. 8 workers peaked at 7.8 GB RSS,
    which OOM-killed the live trader on the VPS — so serial stays the default.
    xdist resolves -n in this hook, so PYTEST_N must land here, not in configure."""
    workers = os.environ.get("PYTEST_N")
    if not workers or config.getoption("usepdb", False):
        return
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return  # workers re-run this hook; without the guard they fork-bomb
    if config.getoption("numprocesses", default=None) is None:
        config.option.numprocesses = int(workers) if workers.isdigit() else workers
        config.option.dist = "worksteal"


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest tmp on disk when /tmp is RAM. A hung GRID archive hit 3GiB there."""
    # Fixture day files must not land in the developer's real Telonex tree cache.
    os.environ.setdefault("TELONEX_TREE_CACHE", "0")
    if config.option.basetemp:
        return
    if not _tmp_is_tmpfs():
        return
    dest = Path("/var/tmp/pytest-esports-trader")
    dest.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(dest)


def discard_telegram_message(message: str) -> None:
    """No-op stand-in for send_telegram_message; tests must never POST."""
    del message


@pytest.fixture(autouse=True)
def clear_legacy_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest must not inherit LIVE_TRADING from the operator .env."""
    monkeypatch.delenv("LIVE_TRADING", raising=False)


@pytest.fixture(autouse=True)
def block_real_telegram(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub notify.send_telegram_message so pytest cannot hit the Bot API.

    test_trader_notify.py is the HTTP contract and keeps the real function
    (it injects fake creds and a mocked httpx.Client). Every other module binds
    notify_in_background at import time; those wrappers still call
    notify.send_telegram_message, so one patch here covers all importers.
    """
    if request.path.name == "test_trader_notify.py":
        return
    monkeypatch.setattr(notify, "send_telegram_message", discard_telegram_message)
