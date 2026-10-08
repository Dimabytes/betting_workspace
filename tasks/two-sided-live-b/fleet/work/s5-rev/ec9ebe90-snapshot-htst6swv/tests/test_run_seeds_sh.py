import subprocess
from pathlib import Path

RUN_SEEDS = Path(__file__).resolve().parents[1] / "scripts" / "run_seeds.sh"


def test_run_seeds_invokes_backtest_as_module() -> None:
    """Script-path CLI puts backtest/ on sys.path and shadows src/strategy."""
    text = RUN_SEEDS.read_text()
    assert "python -m backtest.run" in text
    assert "src/backtest/run.py" not in text


def test_run_seeds_reports_through_the_backtest_interpreter() -> None:
    """Both reporting scripts import backtest.context, so they need the framework path."""
    text = RUN_SEEDS.read_text()
    assert "run_report scripts/report_seeds.py" in text
    assert "run_report scripts/compare_backtests.py" in text
    assert "uv run python scripts/" not in text


def _wait_all_source() -> str:
    text = RUN_SEEDS.read_text()
    start = text.index("wait_all() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _drive(children: str) -> subprocess.CompletedProcess[str]:
    script = f"""
set -euo pipefail
{_wait_all_source()}
pids=()
{children}
status=0
wait_all "${{pids[@]}}" || status=$?
echo "status=$status"
exit "$status"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def test_wait_all_returns_zero_when_every_child_succeeds() -> None:
    """Three successful children leave the driver at exit 0."""
    children = "\n".join(f'( sleep 0.0{index}; exit 0 ) & pids+=("$!")' for index in range(1, 4))
    result = _drive(children)
    assert result.returncode == 0
    assert "status=0" in result.stdout


def test_wait_all_fails_even_when_a_later_child_succeeds() -> None:
    """One failing child makes the driver nonzero and it still waits for its siblings."""
    children = "\n".join(
        [
            '( exit 3 ) & pids+=("$!")',
            '( sleep 0.2; echo late-child-finished; exit 0 ) & pids+=("$!")',
        ]
    )
    result = _drive(children)
    assert result.returncode == 3
    assert "late-child-finished" in result.stdout
    assert "status=3" in result.stdout
