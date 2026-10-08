import gzip
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from collect import s05_fetch_opendota_matches as fetcher


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def cache_path(match_id: int) -> Path:
        return tmp_path / "opendota" / f"match_{int(match_id)}.json.gz"

    monkeypatch.setattr(fetcher, "opendota_match_cache_path", cache_path)

    def no_params() -> dict[str, str]:
        return {}

    monkeypatch.setattr(fetcher, "opendota_params", no_params)
    monkeypatch.setattr(fetcher, "http_client", lambda: httpx.Client())
    return tmp_path / "opendota"


def stub_get_json(
    monkeypatch: pytest.MonkeyPatch, matches: dict[int, Any], requested: list[int]
) -> None:
    def get_json(_client: httpx.Client, url: str, _params: dict[str, Any]) -> Any:
        match_id = int(url.rsplit("/", 1)[1])
        requested.append(match_id)
        payload = matches[match_id]
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(fetcher, "get_json", get_json)


def read_cache(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def test_only_missing_files_are_requested(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A match with a cache file is never re-requested."""
    monkeypatch.setattr(fetcher, "load_admitted_match_ids", lambda: [1, 2])
    cache_dir.mkdir(parents=True)
    (cache_dir / "match_1.json.gz").write_bytes(b"x")
    requested: list[int] = []
    stub_get_json(monkeypatch, {2: {"match_id": 2, "version": 21}}, requested)

    fetcher.main(budget=1900, sleep=0.0, workers=1)

    assert requested == [2]
    assert read_cache(cache_dir / "match_2.json.gz")["data"] == {"match_id": 2, "version": 21}


def test_budget_zero_requests_nothing(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "load_admitted_match_ids", lambda: [1, 2])
    requested: list[int] = []
    stub_get_json(monkeypatch, {}, requested)

    fetcher.main(budget=0, sleep=0.0, workers=1)

    assert requested == []
    assert not cache_dir.exists()


def test_one_failing_match_does_not_stop_the_run(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(fetcher, "load_admitted_match_ids", lambda: [1, 2, 3])
    requested: list[int] = []
    stub_get_json(
        monkeypatch,
        {1: {"match_id": 1}, 2: httpx.ConnectError("boom"), 3: {"match_id": 3}},
        requested,
    )

    fetcher.main(budget=1900, sleep=0.0, workers=1)

    out = capsys.readouterr().out
    assert requested == [1, 2, 3]
    assert sorted(p.name for p in cache_dir.iterdir()) == ["match_1.json.gz", "match_3.json.gz"]
    assert "failed OpenDota match 2: boom" in out
    assert "saved_this_run: 2" in out
    assert "failed_this_run: 1" in out


def test_parallel_mode_saves_every_match(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--workers>1 fetches the same worklist and reports failures against the right id."""
    monkeypatch.setattr(fetcher, "load_admitted_match_ids", lambda: [1, 2, 3, 4])
    requested: list[int] = []
    stub_get_json(
        monkeypatch,
        {1: {"match_id": 1}, 2: {"match_id": 2}, 3: httpx.ConnectError("boom"), 4: {"match_id": 4}},
        requested,
    )

    fetcher.main(budget=1900, sleep=0.0, workers=4)

    assert sorted(requested) == [1, 2, 3, 4]
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "match_1.json.gz",
        "match_2.json.gz",
        "match_4.json.gz",
    ]


def test_failed_write_leaves_no_cache_file(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash inside the atomic write must not publish a truncated cache file."""
    monkeypatch.setattr(fetcher, "load_admitted_match_ids", lambda: [7])
    requested: list[int] = []
    stub_get_json(monkeypatch, {7: {"match_id": 7, "bad": object()}}, requested)

    fetcher.main(budget=1900, sleep=0.0, workers=1)

    assert requested == [7]
    assert not (cache_dir / "match_7.json.gz").exists()
