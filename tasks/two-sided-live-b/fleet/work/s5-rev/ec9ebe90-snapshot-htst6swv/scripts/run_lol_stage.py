"""Run a numbered LoL stage without shadowing the stdlib ``types`` module.

``python src/lol/01_….py`` puts ``src/lol/`` first on ``sys.path``, so
``import types`` resolves to ``lol/types.py``. Import the stage as
``lol.01_…`` via ``PYTHONPATH=src`` instead.

Usage:
  uv run python scripts/run_lol_stage.py 01_build_universe --fetch
  uv run python scripts/run_lol_stage.py 03_link_lolesports --fetch
  uv run python scripts/run_lol_stage.py 04_fetch_lolesports
  uv run python scripts/run_lol_stage.py 05_prepare_dataset
  uv run python scripts/run_lol_stage.py 06_train_model
"""

import importlib
import sys
from pathlib import Path

import typer

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: run_lol_stage.py <01_build_universe|03_link_lolesports|…> [typer args…]"
        )
    stage = sys.argv[1]
    if stage.endswith(".py"):
        stage = Path(stage).stem
    if stage.startswith("lol."):
        module_name = stage
        stage = stage.removeprefix("lol.")
    else:
        module_name = f"lol.{stage}"

    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

    sys.argv = [stage, *sys.argv[2:]]
    module = importlib.import_module(module_name)
    entry = getattr(module, "main", None)
    if entry is None:
        raise SystemExit(f"{module_name} has no main()")
    typer.run(entry)


if __name__ == "__main__":
    main()
