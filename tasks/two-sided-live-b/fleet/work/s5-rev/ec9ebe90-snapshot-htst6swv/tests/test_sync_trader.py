"""Tests for trader rsync argv and local archive coverage scan."""

import gzip
import json
import os
import time
from io import BytesIO
from pathlib import Path

import pytest
import rsync_pull
import sync_trader as sync


def test_rsync_skips_wallet_and_never_deletes() -> None:
    argv = sync.build_rsync_argv(
        "sun",
        "/root/work/esports-trader/data/trader_live",
        Path("/tmp/trader"),
        False,
    )
    assert argv[0] == "rsync"
    assert "-avz" in argv
    assert "--progress" in argv
    assert "--ignore-existing" not in argv
    assert "--delete" not in argv
    assert "--exclude=wallet/" in argv
    assert not any(flag.startswith("--info=") for flag in argv)
    assert argv[-2] == "sun:/root/work/esports-trader/data/trader_live/"
    assert argv[-1] == "/tmp/trader/"


def test_dry_run_flag_and_trailing_slashes() -> None:
    argv = sync.build_rsync_argv(
        "sun",
        "/root/work/esports-trader/data/trader_live/",
        Path("/tmp/trader"),
        True,
    )
    assert "-n" in argv
    assert argv[-2].endswith("/")


def test_scan_archive_counts_finalized_and_join_span(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    (root / "wallet").mkdir(parents=True)
    _write_meta(root / "1", joined="2026-08-15T12:00:00Z", finalized=True)
    _write_meta(root / "2", joined="2026-08-21T18:00:00Z", finalized=False)
    (root / "3").mkdir()
    coverage = sync.scan_archive(root)
    assert coverage.matches == 3
    assert coverage.finalized == 1
    assert coverage.unfinalized == 2
    assert coverage.min_joined == "2026-08-15T12:00:00Z"
    assert coverage.max_joined == "2026-08-21T18:00:00Z"


def test_scan_archive_counts_a_truncated_meta_as_unfinalized(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    (root / "1").mkdir(parents=True)
    (root / "1" / "match.json").write_text('{"joined_at_utc": "2026-08', encoding="utf-8")
    coverage = sync.scan_archive(root)
    assert coverage.matches == 1
    assert coverage.unfinalized == 1
    assert coverage.min_joined is None


def test_scan_archive_empty_root(tmp_path: Path) -> None:
    coverage = sync.scan_archive(tmp_path / "missing")
    assert coverage.matches == 0
    assert coverage.min_joined is None


def _write_meta(match_dir: Path, joined: str, finalized: bool) -> None:
    """Write a minimal match.json used only by the coverage scan."""
    match_dir.mkdir(parents=True)
    document = {
        "joined_at_utc": joined,
        "final": {"duration_seconds": 1} if finalized else None,
    }
    (match_dir / "match.json").write_text(json.dumps(document), encoding="utf-8")


def _twin_dir(root: Path, name: str = "1") -> Path:
    """A cleaned-up match dir with an old `state.jsonl` + `state.jsonl.gz` pair."""
    match_dir = root / name
    match_dir.mkdir(parents=True)
    (match_dir / "execution_cleanup.json").write_text("{}\n", encoding="utf-8")
    content = b'{"a":1}\n'
    plain = match_dir / "state.jsonl"
    plain.write_bytes(content)
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as stream:
        stream.write(content)
    gz_path = match_dir / "state.jsonl.gz"
    gz_path.write_bytes(buf.getvalue())
    old_ns = time.time_ns() - 72 * 3_600_000_000_000
    os.utime(plain, ns=(old_ns, old_ns))
    os.utime(gz_path, ns=(old_ns, old_ns))
    return match_dir


def _noop_rsync(argv: list[str]) -> None:
    """Stand-in for a successful rsync pass."""


def test_successful_rsync_dedups_twins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "trader"
    match_dir = _twin_dir(root)
    monkeypatch.setattr(rsync_pull, "run_rsync", _noop_rsync)
    sync.main(["--dest", str(root)])
    assert not (match_dir / "state.jsonl").exists()
    assert (match_dir / "state.jsonl.gz").is_file()


def test_failed_rsync_never_dedups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "trader"
    match_dir = _twin_dir(root)

    def boom(argv: list[str]) -> None:
        raise SystemExit(23)

    monkeypatch.setattr(rsync_pull, "run_rsync", boom)
    with pytest.raises(SystemExit):
        sync.main(["--dest", str(root)])
    assert (match_dir / "state.jsonl").is_file()


def test_dry_run_never_dedups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "trader"
    match_dir = _twin_dir(root)
    monkeypatch.setattr(rsync_pull, "run_rsync", _noop_rsync)
    sync.main(["--dest", str(root), "--dry-run"])
    assert (match_dir / "state.jsonl").is_file()
    assert not (root / ".compress_archive.lock").exists()
