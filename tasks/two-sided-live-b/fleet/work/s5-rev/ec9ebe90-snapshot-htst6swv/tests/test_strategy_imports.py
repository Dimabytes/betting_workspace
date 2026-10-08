"""Importing strategy must not pull backtest, trader, nautilus, polymaker, pandas, or I/O."""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

PROBE = """
import builtins
import socket
import sys
import time

def fail_open(*_args, **_kwargs):
    raise AssertionError("strategy called open()")

class FailSocket:
    def __init__(self, *args, **kwargs):
        raise AssertionError("strategy opened a socket")

def fail_time() -> float:
    raise AssertionError("strategy called time.time")

def fail_time_ns() -> int:
    raise AssertionError("strategy called time.time_ns")

builtins.open = fail_open
socket.socket = FailSocket
time.time = fail_time
time.time_ns = fail_time_ns

import strategy.engine as strategy_engine

banned = {"backtest", "trader", "nautilus_trader", "polymaker", "pandas"}
loaded = banned.intersection(sys.modules)
assert not loaded, loaded
assert hasattr(strategy_engine, "step")
"""


def test_strategy_import_is_isolated() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
