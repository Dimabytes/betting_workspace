"""Tests for collector-v1 sidecar scan and per-game archive roots."""

import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from trader_discovery_fixtures import (
    MALFORMED_SIDECAR_CASES,
    SCAN_NOW,
    env_stub,
    sidecar_body,
    sidecar_logs,
    write_sidecar,
)

from trader import collector_sidecars
from trader.collector_sidecars import SIDECAR_MAX_AGE_SECONDS, load_archive_root, scan_sidecars
from trader.game_profile import GAME_PROFILES, GameProfile

DOTA = GAME_PROFILES["dota"]
LOL = GAME_PROFILES["lol"]


def _recording_env(value: str | None) -> tuple[list[str], Callable[[str], str | None]]:
    """Stub env_value and record the names it was asked for."""
    names: list[str] = []

    def lookup(name: str) -> str | None:
        names.append(name)
        return value

    return names, lookup


def test_load_archive_root_dota_reads_dota_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOTA_ARCHIVE_ROOT is returned as a Path without any filesystem work."""
    names, lookup = _recording_env("/srv/dota")
    monkeypatch.setattr(collector_sidecars, "env_value", lookup)

    assert load_archive_root(DOTA) == Path("/srv/dota")
    assert names == ["DOTA_ARCHIVE_ROOT"]


def test_load_archive_root_lol_reads_lol_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOL_ARCHIVE_ROOT is returned as a Path without any filesystem work."""
    names, lookup = _recording_env("/srv/lol")
    monkeypatch.setattr(collector_sidecars, "env_value", lookup)

    assert load_archive_root(LOL) == Path("/srv/lol")
    assert names == ["LOL_ARCHIVE_ROOT"]


@pytest.mark.parametrize("profile", [DOTA, LOL])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_load_archive_root_raises_when_blank(
    monkeypatch: pytest.MonkeyPatch, profile: GameProfile, value: str | None
) -> None:
    """A missing or blank per-game archive env raises naming that variable."""
    monkeypatch.setattr(collector_sidecars, "env_value", env_stub(value))

    with pytest.raises(RuntimeError, match=f"{profile.archive_root_env} is not set"):
        load_archive_root(profile)


# --- sidecar scan -------------------------------------------------------------


def test_scan_returns_fresh_valid_sidecars_sorted_by_condition_id(tmp_path: Path) -> None:
    """Fresh valid sidecars come back sorted by condition id, staler ones are skipped."""
    write_sidecar(tmp_path, "0xb", mtime=SCAN_NOW - 100)
    write_sidecar(tmp_path, "0xa", mtime=SCAN_NOW - 50)
    write_sidecar(tmp_path, "0xc", mtime=SCAN_NOW - 200)

    result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert [sidecar.condition_id for sidecar in result] == ["0xa", "0xb", "0xc"]


def test_scan_ignores_tmp_dirs_symlinks_and_stale_files(tmp_path: Path) -> None:
    """Only fresh regular .json files count; stale-but-active files are skipped."""
    markets = tmp_path / "metadata" / "markets"
    write_sidecar(tmp_path, "0xfresh", mtime=SCAN_NOW - 100)
    write_sidecar(tmp_path, "0xstale", mtime=SCAN_NOW - SIDECAR_MAX_AGE_SECONDS - 60)
    (markets / "0xtmp.json.tmp").write_text("{}", encoding="utf-8")
    (markets / "0xdir.json").mkdir()
    os.symlink(markets / "0xfresh.json", markets / "0xlink.json")
    os.symlink(markets / "0xmissing.json", markets / "0xbroken.json")

    result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert [sidecar.condition_id for sidecar in result] == ["0xfresh"]


def test_scan_missing_markets_directory_returns_empty_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing or unreadable markets directory is an empty scan, not a crash."""
    with caplog.at_level(logging.WARNING, logger="trader.collector_sidecars"):
        result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert result == ()
    assert any("markets directory" in message for message in sidecar_logs(caplog))


def test_scan_atomic_rename_disappearance_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file vanishing between scandir and read is a skipped entry, not a crash."""

    original_read_bytes = Path.read_bytes

    def raising_read_bytes(self: Path) -> bytes:
        if self.name == "0xgone.json":
            raise FileNotFoundError(self.name)
        return original_read_bytes(self)

    write_sidecar(tmp_path, "0xgone", mtime=SCAN_NOW - 100)
    write_sidecar(tmp_path, "0xkept", mtime=SCAN_NOW - 100)
    monkeypatch.setattr(Path, "read_bytes", raising_read_bytes)

    result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert [sidecar.condition_id for sidecar in result] == ["0xkept"]


def test_scan_rejects_invalid_utf8_and_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid UTF-8 and broken JSON are skipped with a basename reason, no crash."""
    markets = tmp_path / "metadata" / "markets"
    markets.mkdir(parents=True)
    utf8_path = markets / "0xutf8.json"
    json_path = markets / "0xjson.json"
    utf8_path.write_bytes(b"\xff\xfe\xfa")
    json_path.write_text("{not-json", encoding="utf-8")
    os.utime(utf8_path, (SCAN_NOW - 100, SCAN_NOW - 100))
    os.utime(json_path, (SCAN_NOW - 100, SCAN_NOW - 100))

    with caplog.at_level(logging.WARNING, logger="trader.collector_sidecars"):
        result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert result == ()
    messages = sidecar_logs(caplog)
    assert any("0xutf8.json skipped: invalid utf-8" in message for message in messages)
    assert any("0xjson.json skipped: invalid json" in message for message in messages)


@pytest.mark.parametrize("case_id,body", MALFORMED_SIDECAR_CASES)
def test_scan_rejects_malformed_sidecars_without_escaping(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    case_id: str,
    body: object,
) -> None:
    """Contract violations skip the file with basename plus reason; nothing is written."""
    write_sidecar(tmp_path, "0xbad", body, mtime=SCAN_NOW - 100)
    markets = tmp_path / "metadata" / "markets"
    names_before = sorted(path.name for path in markets.iterdir())

    with caplog.at_level(logging.WARNING, logger="trader.collector_sidecars"):
        result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert result == (), case_id
    assert sorted(path.name for path in markets.iterdir()) == names_before
    assert any("sidecar 0xbad.json skipped" in message for message in sidecar_logs(caplog)), case_id


def test_scan_ignores_unknown_future_keys(tmp_path: Path) -> None:
    """Extra future JSON keys are ignored, not rejected."""
    body = sidecar_body(**{"futureField": "future-value"})
    write_sidecar(tmp_path, "0x1", body, mtime=SCAN_NOW - 100)

    result = scan_sidecars(tmp_path, SCAN_NOW).sidecars

    assert [sidecar.condition_id for sidecar in result] == ["0x1"]
