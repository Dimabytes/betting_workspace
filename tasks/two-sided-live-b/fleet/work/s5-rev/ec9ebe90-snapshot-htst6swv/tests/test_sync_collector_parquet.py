"""Tests for collector → Telonex rsync argv, coverage scan, and onchain manifests."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sync_collector_parquet as sync

DAY = "2026-09-19"


def _entry(asset_id: str, path: str, sha256: str = "0" * 64) -> dict[str, object]:
    return {
        "assetId": asset_id,
        "path": path,
        "sha256": sha256,
        "bytes": 12,
        "rows": 3,
        "minTimestampUs": None,
        "maxTimestampUs": None,
    }


def _manifest_payload(
    day: str = DAY, status: str = "ready", files: list[dict[str, object]] | None = None
) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 1,
            "channel": "onchain_fills",
            "game": "dota",
            "date": day,
            "status": status,
            "files": files if files is not None else [],
        }
    ).encode()


def _manifest(day: str = DAY, files: list[dict[str, object]] | None = None) -> sync.OnchainManifest:
    return sync.decode_manifest(_manifest_payload(day=day, files=files))


def _relpath(token: str, day: str = DAY) -> str:
    return f"parquet/onchain_fills/asset_id={token}/{day}.parquet"


def _write_manifests(manifests_dir: Path, payloads: list[tuple[str, bytes]]) -> None:
    manifests_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads:
        (manifests_dir / name).write_bytes(payload)


def test_rsync_skips_existing_and_today_and_never_deletes() -> None:
    argv = sync.build_rsync_argv(
        "sun",
        "/var/lib/polymarket-dota-archive/parquet",
        Path("/tmp/telonex"),
        date(2026, 8, 15),
        False,
    )
    assert argv[0] == "rsync"
    assert "-av" in argv
    assert "--progress" in argv
    assert "--ignore-existing" in argv
    assert "--delete" not in argv
    assert not any(flag.startswith("--info=") for flag in argv)
    assert "--exclude=2026-08-15.parquet" in argv
    assert "--exclude=trades/" in argv
    assert "--exclude=onchain_fills/" in argv
    assert argv[-2] == "sun:/var/lib/polymarket-dota-archive/parquet/"
    assert argv[-1] == "/tmp/telonex/"


def test_dry_run_flag_and_trailing_slashes() -> None:
    argv = sync.build_rsync_argv(
        "sun",
        "/var/lib/polymarket-dota-archive/parquet/",
        Path("/tmp/telonex"),
        date(2026, 8, 15),
        True,
    )
    assert "-n" in argv
    assert argv[-2].endswith("/")


def test_scan_channel_counts_asset_day_files(tmp_path: Path) -> None:
    books = tmp_path / "book_snapshot_full"
    (books / "asset_id=aaa").mkdir(parents=True)
    (books / "asset_id=bbb").mkdir()
    (books / "asset_id=aaa" / "2026-08-08.parquet").write_bytes(b"x")
    (books / "asset_id=bbb" / "2026-08-14.parquet").write_bytes(b"x")
    (books / "not-an-asset").mkdir()
    coverage = sync.scan_channel(tmp_path, "book_snapshot_full")
    assert coverage.assets == 2
    assert coverage.files == 2
    assert coverage.min_day == "2026-08-08"
    assert coverage.max_day == "2026-08-14"


def test_utc_today_uses_utc_calendar_date() -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    assert sync.utc_today(now) == date(2026, 8, 15)


def test_paths_for_game_dota_and_lol() -> None:
    dota = sync.paths_for_game("dota")
    lol = sync.paths_for_game("lol")
    assert dota.archive == "/var/lib/polymarket-dota-archive"
    assert lol.archive == "/var/lib/polymarket-lol-archive"
    assert dota.remote == "/var/lib/polymarket-dota-archive/parquet"
    assert lol.remote == "/var/lib/polymarket-lol-archive/parquet"
    assert dota.dest.name == "polymarket"
    assert "lol" in lol.dest.parts
    assert "raw" in dota.dest.parts


def test_parse_argv_requires_game() -> None:
    with pytest.raises(SystemExit):
        sync.parse_argv([])
    args = sync.parse_argv(["--game", "lol", "--dry-run"])
    assert args.paths.game == "lol"
    assert args.ssh_host == "sun"
    assert args.dry_run is True
    assert args.onchain_start_date is None


def test_parse_argv_onchain_start_date() -> None:
    args = sync.parse_argv(["--game", "dota", "--onchain-start-date", "2026-09-20"])
    assert args.onchain_start_date == date(2026, 9, 20)
    with pytest.raises(SystemExit):
        sync.parse_argv(["--game", "dota", "--onchain-start-date", "not-a-date"])


def test_manifest_decode_ignores_unknown_keys() -> None:
    manifest = _manifest(files=[_entry("t1", _relpath("t1"))])
    assert manifest.date == DAY
    assert manifest.status == "ready"
    assert manifest.files[0].asset_id == "t1"
    assert manifest.files[0].path == _relpath("t1")
    assert manifest.files[0].sha256 == "0" * 64
    assert manifest.files[0].bytes == 12


def test_load_ready_manifests_skips_not_ready_and_today(tmp_path: Path) -> None:
    _write_manifests(
        tmp_path,
        [
            ("2026-09-18.json", _manifest_payload("2026-09-18")),
            ("2026-09-19.json", _manifest_payload("2026-09-19", status="pending")),
            ("2026-09-20.json", _manifest_payload("2026-09-20")),
            ("2026-09-21.json", b"not json"),
        ],
    )
    manifests, seen = sync.load_ready_manifests(tmp_path, date(2026, 9, 20))
    assert seen == 4
    assert [m.date for m in manifests] == ["2026-09-18"]


def test_plan_actions_missing_local_is_install(tmp_path: Path) -> None:
    manifest = _manifest(files=[_entry("tok1", _relpath("tok1"))])
    entries, rejected = sync.plan_onchain_actions(manifest, tmp_path, None)
    assert rejected == 0
    assert [e.action for e in entries] == ["install"]
    assert entries[0].relpath == _relpath("tok1")
    assert entries[0].target == tmp_path / "onchain_fills" / "asset_id=tok1" / f"{DAY}.parquet"


def test_plan_actions_same_sha_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "onchain_fills" / "asset_id=tok1" / f"{DAY}.parquet"
    target.parent.mkdir(parents=True)
    payload = b"local bytes"
    target.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    manifest = _manifest(files=[_entry("tok1", _relpath("tok1"), sha)])
    entries, rejected = sync.plan_onchain_actions(manifest, tmp_path, None)
    assert rejected == 0
    assert [e.action for e in entries] == ["kept"]


def test_plan_actions_differ_respects_start_date(tmp_path: Path) -> None:
    target = tmp_path / "onchain_fills" / "asset_id=tok1" / f"{DAY}.parquet"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"legacy bytes")
    manifest = _manifest(files=[_entry("tok1", _relpath("tok1"))])
    entries, _ = sync.plan_onchain_actions(manifest, tmp_path, date(2026, 9, 19))
    assert [e.action for e in entries] == ["replace"]
    entries, _ = sync.plan_onchain_actions(manifest, tmp_path, date(2026, 9, 20))
    assert [e.action for e in entries] == ["conflict"]
    entries, _ = sync.plan_onchain_actions(manifest, tmp_path, None)
    assert [e.action for e in entries] == ["conflict"]


def test_plan_actions_malformed_path_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(
        files=[
            _entry("tok1", "parquet/book_snapshot_full/asset_id=tok1/2026-09-19.parquet"),
            _entry("tok1", _relpath("tok2")),
            _entry("tok1", _relpath("tok1", "2026-09-18")),
            _entry("tok1", "onchain_fills/asset_id=tok1/2026-09-19.parquet"),
        ]
    )
    entries, rejected = sync.plan_onchain_actions(manifest, tmp_path, None)
    assert entries == []
    assert rejected == 4


def test_fetchable_relpaths_only_install_and_replace(tmp_path: Path) -> None:
    kept_target = tmp_path / "onchain_fills" / "asset_id=k" / f"{DAY}.parquet"
    kept_target.parent.mkdir(parents=True)
    kept_target.write_bytes(b"same")
    kept_sha = hashlib.sha256(b"same").hexdigest()
    replace_target = tmp_path / "onchain_fills" / "asset_id=r" / f"{DAY}.parquet"
    replace_target.parent.mkdir(parents=True)
    replace_target.write_bytes(b"old")
    start = date(2026, 9, 19)
    manifest = _manifest(
        files=[
            _entry("i", _relpath("i")),
            _entry("k", _relpath("k"), kept_sha),
            _entry("r", _relpath("r")),
        ]
    )
    entries, _ = sync.plan_onchain_actions(manifest, tmp_path, start)
    assert [e.action for e in entries] == ["install", "kept", "replace"]
    conflict_target = tmp_path / "onchain_fills" / "asset_id=c" / "2026-09-18.parquet"
    conflict_target.parent.mkdir(parents=True)
    conflict_target.write_bytes(b"legacy")
    older = _manifest("2026-09-18", files=[_entry("c", _relpath("c", "2026-09-18"))])
    conflicted, _ = sync.plan_onchain_actions(older, tmp_path, start)
    assert [e.action for e in conflicted] == ["conflict"]
    assert sync.fetchable_relpaths(entries + conflicted) == [
        _relpath("i"),
        _relpath("r"),
    ]
    empty, _ = sync.plan_onchain_actions(
        _manifest(files=[_entry("k", _relpath("k"), kept_sha)]), tmp_path, None
    )
    assert sync.fetchable_relpaths(empty) == []


def test_install_staged_files_replaces_and_counts_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    staged_root = tmp_path / "staged"
    good_rel = _relpath("good")
    bad_rel = _relpath("bad")
    good_staged = staged_root / good_rel
    bad_staged = staged_root / bad_rel
    good_staged.parent.mkdir(parents=True)
    bad_staged.parent.mkdir(parents=True)
    good_payload = b"verified bytes"
    good_staged.write_bytes(good_payload)
    bad_staged.write_bytes(b"tampered")
    existing = dest / "onchain_fills" / "asset_id=bad" / f"{DAY}.parquet"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"local stays")
    entries = [
        sync.OnchainPlanEntry(
            relpath=good_rel,
            target=dest / "onchain_fills" / "asset_id=good" / f"{DAY}.parquet",
            sha256=hashlib.sha256(good_payload).hexdigest(),
            action="install",
        ),
        sync.OnchainPlanEntry(
            relpath=bad_rel,
            target=existing,
            sha256="f" * 64,
            action="replace",
        ),
    ]
    installed, replaced, mismatched = sync.install_staged_files(staged_root, entries)
    assert (installed, replaced, mismatched) == (1, 0, 1)
    assert entries[0].target.read_bytes() == good_payload
    assert existing.read_bytes() == b"local stays"
