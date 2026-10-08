"""Every backtest module must import on its own, without a sibling going first."""

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = [path.stem for path in sorted((SRC / "backtest").glob("*.py")) if path.stem != "run"]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_first(module: str) -> None:
    """A module that only works when another one wins the import race hides a cycle."""
    result = subprocess.run(
        [sys.executable, "-c", f"import backtest.{module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
