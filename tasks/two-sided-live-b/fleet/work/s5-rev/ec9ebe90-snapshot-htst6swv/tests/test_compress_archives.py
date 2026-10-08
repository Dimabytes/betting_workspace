"""`trader.compress_archives`: gates, compress, dedup, conflicts, interruptions."""

import gzip
import os
import time
from io import BytesIO
from pathlib import Path

import pytest

import trader.compress_archives as ca
from shared.utils.jsonl_io import open_maybe_gz
from trader.compress_archives import (
    LOCK_FILENAME,
    ScanReport,
    capture_identity,
    dedup_pair,
    gz_content_matches,
    identity_intact,
    scan_root,
    scan_roots,
)
from trader.paths import EXECUTION_CLEANUP_FILENAME
from trader.process_lock import acquire_file_lock

CONTENT = b'{"a":1}\n{"b":2}\n'
OTHER = b'{"z":9}\n'
HOUR_NS = 3_600_000_000_000
MIN_AGE_NS = 48 * HOUR_NS
OLD_AGE_NS = 72 * HOUR_NS


def _match_dir(root: Path, name: str = "m1", *, marker: bool = True) -> Path:
    match_dir = root / name
    match_dir.mkdir(parents=True)
    if marker:
        (match_dir / EXECUTION_CLEANUP_FILENAME).write_text("{}\n", encoding="utf-8")
    return match_dir


def _age(path: Path) -> Path:
    old_ns = time.time_ns() - OLD_AGE_NS
    os.utime(path, ns=(old_ns, old_ns))
    return path


def _feed(match_dir: Path, name: str, content: bytes = CONTENT, *, old: bool = True) -> Path:
    path = match_dir / name
    path.write_bytes(content)
    return _age(path) if old else path


def _gz_bytes(content: bytes) -> bytes:
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as stream:
        stream.write(content)
    return buf.getvalue()


def _gz(match_dir: Path, name: str, content: bytes = CONTENT, *, old: bool = True) -> Path:
    path = match_dir / f"{name}.gz"
    path.write_bytes(_gz_bytes(content))
    return _age(path) if old else path


def _scan(
    root: Path,
    *,
    require_marker: bool = True,
    compress_new: bool = True,
    dry_run: bool = False,
) -> ScanReport:
    return scan_root(
        root,
        require_marker=require_marker,
        min_age_ns=MIN_AGE_NS,
        now_ns=time.time_ns(),
        compress_new=compress_new,
        dry_run=dry_run,
    )


def _only(report: ScanReport) -> str:
    assert len(report.results) == 1
    return report.results[0].action


def test_compress_happy_path(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "grid_state.jsonl")
    report = _scan(root)
    assert _only(report) == "compressed"
    assert not plain.exists()
    gz_path = match_dir / "grid_state.jsonl.gz"
    assert gzip.decompress(gz_path.read_bytes()) == CONTENT
    assert not (match_dir / "grid_state.jsonl.gz.tmp").exists()
    with open_maybe_gz(plain) as handle:
        assert handle.read() == CONTENT.decode()
    second = _scan(root)
    assert second.results == []  # rerun is a no-op


def test_requires_cleanup_marker(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root, marker=False)
    plain = _feed(match_dir, "grid_state.jsonl")
    report = _scan(root)
    assert report.skips[0].reason == "no_cleanup_marker"
    assert plain.exists()
    assert not (match_dir / "grid_state.jsonl.gz").exists()


def test_fresh_feed_defers_whole_dir(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    grid = _feed(match_dir, "grid_state.jsonl", old=True)
    _feed(match_dir, "oddin_state.jsonl", old=False)
    report = _scan(root)
    assert report.skips[0].reason == "fresh:oddin_state.jsonl"
    assert grid.exists()
    assert not (match_dir / "grid_state.jsonl.gz").exists()


def test_session_jsonl_ignored_and_never_blocks(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    _feed(match_dir, "grid_state.jsonl", old=True)
    session = _feed(match_dir, "session.jsonl", old=False)
    report = _scan(root)
    assert _only(report) == "compressed"
    assert session.exists()
    assert not (match_dir / "session.jsonl.gz").exists()


def test_no_marker_mode_for_historical_roots(tmp_path: Path) -> None:
    root = tmp_path / "live_paper"
    match_dir = _match_dir(root, marker=False)
    _feed(match_dir, "core_trace.jsonl")
    report = _scan(root, require_marker=False)
    assert _only(report) == "compressed"


def test_dedup_drops_equal_plain(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl")
    gz_path = _gz(match_dir, "state.jsonl")
    report = _scan(root)
    result = report.results[0]
    assert result.action == "deduped"
    assert result.freed_bytes == len(CONTENT)
    assert not plain.exists()
    assert gz_path.exists()


def test_dedup_conflict_keeps_both_and_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl")
    gz_path = _gz(match_dir, "state.jsonl", OTHER)
    before = gz_path.read_bytes()
    report = _scan(root)
    result = report.results[0]
    assert result.action == "conflict"
    assert result.reason == "content_differs"
    assert plain.exists()
    assert gz_path.read_bytes() == before


def test_dedup_corrupt_gz_keeps_both(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl")
    gz_path = match_dir / "state.jsonl.gz"
    gz_path.write_bytes(b"this is not a gzip stream")
    _age(gz_path)
    report = _scan(root)
    result = report.results[0]
    assert result.action == "conflict"
    assert result.reason == "gz_corrupt"
    assert plain.exists()
    assert gz_path.exists()


def test_fresh_plain_defers_dedup(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl", old=False)
    _gz(match_dir, "state.jsonl", old=True)
    report = _scan(root)
    assert report.skips[0].reason == "fresh:state.jsonl"
    assert plain.exists()


def test_dedup_only_mode_leaves_plain_alone(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    solo = _feed(_match_dir(root, "solo"), "state.jsonl")
    pair_dir = _match_dir(root, "pair")
    _feed(pair_dir, "state.jsonl")
    _gz(pair_dir, "state.jsonl")
    report = _scan(root, compress_new=False)
    assert _only(report) == "deduped"
    assert solo.exists()
    assert not (solo.parent / "state.jsonl.gz").exists()


def test_stale_tmp_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    _feed(match_dir, "grid_state.jsonl")
    tmp = match_dir / "grid_state.jsonl.gz.tmp"
    tmp.write_bytes(b"partial write")
    report = _scan(root)
    assert _only(report) == "compressed"
    assert not tmp.exists()
    assert gzip.decompress((match_dir / "grid_state.jsonl.gz").read_bytes()) == CONTENT


def test_interrupted_after_publish_dedups(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl")
    _gz(match_dir, "state.jsonl")
    tmp = match_dir / "state.jsonl.gz.tmp"
    tmp.write_bytes(b"partial write")
    report = _scan(root)
    assert _only(report) == "deduped"
    assert not plain.exists()
    assert not tmp.exists()


def test_mutation_during_compress_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "grid_state.jsonl")

    def never_intact(path: Path, ident: ca.FileIdentity) -> bool:
        return False

    monkeypatch.setattr(ca, "identity_intact", never_intact)
    report = _scan(root)
    result = report.results[0]
    assert result.action == "skipped"
    assert result.reason == "changed_during_compress"
    assert plain.exists()
    assert not (match_dir / "grid_state.jsonl.gz").exists()
    assert not (match_dir / "grid_state.jsonl.gz.tmp").exists()


def test_mutation_after_publish_keeps_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "grid_state.jsonl")
    real = ca.identity_intact
    calls = 0

    def fail_second(path: Path, ident: ca.FileIdentity) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1 and real(path, ident)

    monkeypatch.setattr(ca, "identity_intact", fail_second)
    report = _scan(root)
    result = report.results[0]
    assert result.action == "skipped"
    assert result.reason == "changed_after_publish"
    assert plain.exists()
    assert (match_dir / "grid_state.jsonl.gz").exists()


def test_changed_during_compare_keeps_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "state.jsonl")
    _gz(match_dir, "state.jsonl")

    def never_intact(path: Path, ident: ca.FileIdentity) -> bool:
        return False

    monkeypatch.setattr(ca, "identity_intact", never_intact)
    report = _scan(root)
    result = report.results[0]
    assert result.action == "skipped"
    assert result.reason == "changed_during_compare"
    assert plain.exists()


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "grid_state.jsonl")
    pair_dir = _match_dir(root, "m2")
    twin = _feed(pair_dir, "state.jsonl")
    _gz(pair_dir, "state.jsonl")
    report = _scan(root, dry_run=True)
    actions = sorted(result.action for result in report.results)
    assert actions == ["would_compress", "would_dedup"]
    assert plain.exists()
    assert twin.exists()
    assert not (match_dir / "grid_state.jsonl.gz").exists()
    assert not (match_dir / "grid_state.jsonl.gz.tmp").exists()


def test_session_gz_is_a_conflict_not_a_dedup(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    session = _feed(match_dir, "session.jsonl")
    (match_dir / "session.jsonl.gz").write_bytes(_gz_bytes(CONTENT))
    report = _scan(root)
    result = report.results[0]
    assert result.action == "conflict"
    assert result.reason == "session_gz_unexpected"
    assert session.exists()


def test_held_lock_skips_root(tmp_path: Path) -> None:
    root = tmp_path / "trader"
    match_dir = _match_dir(root)
    plain = _feed(match_dir, "grid_state.jsonl")
    lock = acquire_file_lock(root / LOCK_FILENAME)
    try:
        reports = scan_roots(
            [root],
            require_marker=True,
            min_age_ns=MIN_AGE_NS,
            now_ns=time.time_ns(),
            compress_new=True,
            dry_run=False,
        )
    finally:
        lock.close()
    assert reports[0].lock_held
    assert plain.exists()


def test_scan_roots_missing_root_reports_error(tmp_path: Path) -> None:
    reports = scan_roots(
        [tmp_path / "missing"],
        require_marker=True,
        min_age_ns=MIN_AGE_NS,
        now_ns=time.time_ns(),
        compress_new=True,
        dry_run=False,
    )
    assert _only(reports[0]) == "error"


def test_identity_detects_change_and_vanish(tmp_path: Path) -> None:
    path = _feed(tmp_path, "state.jsonl")
    ident = capture_identity(path)
    assert ident is not None
    assert identity_intact(path, ident)
    path.write_bytes(CONTENT + b"more\n")
    assert not identity_intact(path, ident)
    path.unlink()
    assert capture_identity(path) is None
    assert not identity_intact(path, ident)


def test_gz_content_matches_directly(tmp_path: Path) -> None:
    plain = _feed(tmp_path, "f.jsonl")
    good = tmp_path / "good.gz"
    good.write_bytes(_gz_bytes(CONTENT))
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"junk")
    short = tmp_path / "short.gz"
    short.write_bytes(_gz_bytes(CONTENT[:-2]))
    assert gz_content_matches(plain, good)
    assert not gz_content_matches(plain, short)
    with pytest.raises(ca.GzCorrupt):
        gz_content_matches(plain, bad)


def test_dedup_pair_vanished_plain(tmp_path: Path) -> None:
    result = dedup_pair(tmp_path / "none.jsonl", tmp_path / "none.jsonl.gz", dry_run=False)
    assert result.action == "skipped"
    assert result.reason == "vanished"
