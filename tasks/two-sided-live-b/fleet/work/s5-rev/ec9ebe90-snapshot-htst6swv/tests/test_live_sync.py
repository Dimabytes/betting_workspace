"""Tests for the live inspector's VPS archive sync launcher."""

import time
from pathlib import Path

from viewer import live_sync

_TRADER_OK = """
from pathlib import Path
Path(__file__).with_name("calls.txt").write_text("trader\\n", encoding="utf-8")
print("trader: matches=3", flush=True)
"""

_PARQUET_OK = """
from pathlib import Path
import sys
log = Path(__file__).with_name("calls.txt")
with log.open("a", encoding="utf-8") as handle:
    handle.write("parquet " + " ".join(sys.argv[1:]) + "\\n")
print("parquet ok", flush=True)
"""


def _write_scripts(root: Path, trader: str, parquet: str | None = None) -> None:
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "sync_trader.py").write_text(trader, encoding="utf-8")
    if parquet is not None:
        (scripts / "sync_collector_parquet.py").write_text(parquet, encoding="utf-8")


def test_sync_live_matches_runs_existing_script(tmp_path: Path) -> None:
    """The inspector invokes sync_trader.py in the project environment."""
    _write_scripts(tmp_path, _TRADER_OK)
    chunks: list[str] = []

    result = live_sync.sync_live_matches(tmp_path, 5.0, chunks.append)

    assert result.returncode == 0
    assert "trader: matches=3" in result.output
    assert result.error == ""
    assert not result.timed_out
    assert "trader: matches=3" in "".join(chunks)


def test_sync_live_matches_returns_failure_output(tmp_path: Path) -> None:
    """A failed rsync remains visible to the Streamlit caller."""
    _write_scripts(
        tmp_path,
        """
import sys
print("partial output", flush=True)
print("ssh failed", file=sys.stderr, flush=True)
raise SystemExit(23)
""",
    )

    result = live_sync.sync_live_matches(tmp_path, 5.0, lambda _text: None)

    assert result.returncode == 23
    assert "partial output" in result.output
    assert "ssh failed" in result.output
    assert result.error == ""
    assert not result.timed_out


def test_sync_live_matches_reports_timeout(tmp_path: Path) -> None:
    """A hung SSH/rsync process is reported as a timeout instead of blocking forever."""
    _write_scripts(tmp_path, "import time\ntime.sleep(60)\n")
    started = time.monotonic()

    result = live_sync.sync_live_matches(tmp_path, 0.4, lambda _text: None)

    assert result.returncode is None
    assert result.error == ""
    assert result.timed_out
    assert time.monotonic() - started < 5.0


def test_sync_live_matches_streams_output_before_exit(tmp_path: Path) -> None:
    """Chunks reach on_output while the child is still running, not only at exit."""
    flag = tmp_path / "go"
    _write_scripts(
        tmp_path,
        f"""
import time
from pathlib import Path
print("hello", flush=True)
for _ in range(50):
    if Path({str(flag)!r}).exists():
        print("done", flush=True)
        raise SystemExit(0)
    time.sleep(0.05)
raise SystemExit(7)
""",
    )
    chunks: list[str] = []

    def on_output(text: str) -> None:
        chunks.append(text)
        if "hello" in "".join(chunks):
            flag.write_text("go", encoding="utf-8")

    result = live_sync.sync_live_matches(tmp_path, 5.0, on_output)

    assert result.returncode == 0
    assert "hello" in result.output
    assert "done" in result.output
    assert not result.timed_out


def test_full_sync_runs_trader_then_both_parquet_games(tmp_path: Path) -> None:
    """One inspector click runs trader, then collector parquet for dota and lol."""
    _write_scripts(tmp_path, _TRADER_OK, _PARQUET_OK)

    trader = live_sync.sync_live_matches(tmp_path, 5.0, lambda _text: None)
    dota = live_sync.sync_collector_parquet(tmp_path, "dota", 5.0, lambda _text: None)
    lol = live_sync.sync_collector_parquet(tmp_path, "lol", 5.0, lambda _text: None)

    assert trader.returncode == 0
    assert dota.returncode == 0
    assert lol.returncode == 0
    assert (tmp_path / "scripts" / "calls.txt").read_text(encoding="utf-8") == (
        "trader\nparquet --game dota\nparquet --game lol\n"
    )


def test_parquet_still_runs_after_trader_failure(tmp_path: Path) -> None:
    """A failed match sync does not skip collector parquet."""
    _write_scripts(
        tmp_path,
        """
import sys
print("partial output", flush=True)
raise SystemExit(23)
""",
        _PARQUET_OK,
    )

    trader = live_sync.sync_live_matches(tmp_path, 5.0, lambda _text: None)
    dota = live_sync.sync_collector_parquet(tmp_path, "dota", 5.0, lambda _text: None)
    lol = live_sync.sync_collector_parquet(tmp_path, "lol", 5.0, lambda _text: None)

    assert trader.returncode == 23
    assert dota.returncode == 0
    assert lol.returncode == 0
    assert (tmp_path / "scripts" / "calls.txt").read_text(encoding="utf-8") == (
        "parquet --game dota\nparquet --game lol\n"
    )
