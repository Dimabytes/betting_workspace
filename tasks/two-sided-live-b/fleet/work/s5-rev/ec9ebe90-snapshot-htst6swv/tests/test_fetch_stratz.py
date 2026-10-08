import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from collect import s05b_fetch_stratz_matches as fetcher
from collect.common.stratz_client import (
    StratzError,
    StratzForbiddenError,
    StratzRateLimitError,
    StratzResponse,
    StratzServerError,
)


def test_pending_defaults_to_missing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache file on disk is never re-requested unless --force."""

    def cache_path(match_id: int) -> Path:
        return tmp_path / f"match_{match_id}.json.gz"

    monkeypatch.setattr(fetcher, "stratz_match_cache_path", cache_path)
    (tmp_path / "match_2.json.gz").write_bytes(b"x")
    (tmp_path / "match_3.json.gz").write_bytes(b"x")

    pending = fetcher.select_pending_match_ids([1, 2, 3], force=False, limit=0)

    assert pending == [1]


def test_force_widens_the_worklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--force re-fetches every id; --limit truncates that list."""

    def cache_path(match_id: int) -> Path:
        return tmp_path / f"match_{match_id}.json.gz"

    monkeypatch.setattr(fetcher, "stratz_match_cache_path", cache_path)
    (tmp_path / "match_2.json.gz").write_bytes(b"x")
    (tmp_path / "match_3.json.gz").write_bytes(b"x")

    forced = fetcher.select_pending_match_ids([1, 2, 3], force=True, limit=0)
    limited = fetcher.select_pending_match_ids([1, 2, 3], force=True, limit=2)

    assert forced == [1, 2, 3]
    assert limited == [1, 2]


def test_slow_response_does_not_stack_a_full_sleep() -> None:
    """A request that outran the interval sleeps 0, not another full interval."""
    assert fetcher.start_to_start_sleep(100.0, 2.5, 100.5) == 2.0
    assert fetcher.start_to_start_sleep(100.0, 2.5, 104.0) == 0.0


def usable_match(match_id: int) -> dict[str, object]:
    leads = [step * 37 for step in range(60)]
    return {
        "id": match_id,
        "startDateTime": 1700000000,
        "durationSeconds": 2400,
        "didRadiantWin": True,
        "radiantNetworthLeads": leads,
        "radiantExperienceLeads": leads,
        "playbackData": {"towerDeathEvents": []},
    }


@pytest.fixture
def offline_fetcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the fetcher at a temp cache dir and index, and never sleep."""

    def cache_path(match_id: int) -> Path:
        return tmp_path / "cache" / f"match_{int(match_id)}.json.gz"

    monkeypatch.setattr(fetcher, "stratz_match_cache_path", cache_path)
    monkeypatch.setattr(fetcher, "STRATZ_MATCH_INDEX_PATH", tmp_path / "index.parquet")

    def no_sleep(_seconds: float) -> None:
        return None

    def fake_client(timeout: float) -> contextlib.AbstractContextManager[Mock]:
        return contextlib.nullcontext(Mock())

    monkeypatch.setattr(fetcher.time, "sleep", no_sleep)
    monkeypatch.setattr(fetcher, "stratz_client", fake_client)
    return tmp_path


def stub_graphql(
    monkeypatch: pytest.MonkeyPatch, replies: dict[int, object], asked: list[int]
) -> None:
    def graphql(_client: object, _query: str, variables: Mapping[str, Any]) -> object:
        match_id = int(cast(int, variables["id"]))
        asked.append(match_id)
        reply = replies[match_id]
        if isinstance(reply, Exception):
            raise reply
        return StratzResponse(data={"match": reply}, errors=[])

    monkeypatch.setattr(fetcher, "stratz_graphql", graphql)


def test_usable_and_unusable_matches_are_both_indexed(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both statuses land in the index; the unusable one keeps its reason."""
    asked: list[int] = []
    stub_graphql(monkeypatch, {1: usable_match(1), 2: None}, asked)
    summaries: dict[int, fetcher.CacheSummary] = {}

    fetcher.fetch_pending_matches([1, 2], summaries, 0.0)

    assert asked == [1, 2]
    assert summaries[1].status == "usable"
    assert summaries[1].playback_available is True
    assert summaries[2].status == "unusable"
    assert summaries[2].reason
    assert (offline_fetcher / "cache" / "match_1.json.gz").exists()
    assert (offline_fetcher / "cache" / "match_2.json.gz").exists()


def test_one_failing_match_does_not_stop_the_run(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[int] = []
    stub_graphql(monkeypatch, {1: StratzError("STRATZ HTTP 404"), 2: usable_match(2)}, asked)
    summaries: dict[int, fetcher.CacheSummary] = {}

    fetcher.fetch_pending_matches([1, 2], summaries, 0.0)

    out = capsys.readouterr().out
    assert asked == [1, 2]
    assert list(summaries) == [2]
    assert "match=1 status=error error=STRATZ HTTP 404" in out
    assert not (offline_fetcher / "cache" / "match_1.json.gz").exists()


def test_forbidden_aborts_the_whole_run(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[int] = []
    stub_graphql(monkeypatch, {1: StratzForbiddenError("blocked"), 2: usable_match(2)}, asked)

    with pytest.raises(SystemExit, match="403"):
        fetcher.fetch_pending_matches([1, 2], {}, 0.0)

    assert asked == [1]


def test_server_errors_retry_then_give_up(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx is retried up to the limit and then surfaces as a per-match error."""
    attempts = 0

    def graphql(_client: object, _query: str, _variables: Mapping[str, Any]) -> object:
        nonlocal attempts
        attempts += 1
        raise StratzServerError("STRATZ HTTP 503")

    monkeypatch.setattr(fetcher, "stratz_graphql", graphql)
    summaries: dict[int, fetcher.CacheSummary] = {}

    fetcher.fetch_pending_matches([1], summaries, 0.0)

    assert attempts == fetcher.SERVER_ERROR_MAX_RETRIES + 1
    assert summaries == {}


def test_rate_limit_retries_once_then_gives_up(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def graphql(_client: object, _query: str, _variables: Mapping[str, Any]) -> object:
        nonlocal attempts
        attempts += 1
        raise StratzRateLimitError("429", retry_after=1.0)

    monkeypatch.setattr(fetcher, "stratz_graphql", graphql)

    fetcher.fetch_pending_matches([1], {}, 0.0)

    assert attempts == fetcher.RATE_LIMIT_MAX_RETRIES + 1


def test_empty_queue_rebuilds_the_index_without_a_client(
    offline_fetcher: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing pending means no STRATZ token and no HTTP client are needed."""

    def no_client(timeout: float) -> object:
        raise AssertionError("must not build a STRATZ client")

    monkeypatch.setattr(fetcher, "stratz_client", no_client)

    def no_match_ids() -> list[int]:
        return []

    monkeypatch.setattr(fetcher, "load_admitted_match_ids", no_match_ids)

    fetcher.main(limit=0, force=False, interval=2.5)

    assert (offline_fetcher / "index.parquet").exists()
