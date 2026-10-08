"""Unit tests for archived Gamma metadata parsing in shared.utils.polymarket."""

import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from polymarket.models.gamma.market import Market

import shared.utils.polymarket as polymarket_mod
from shared.utils.polymarket import GammaMarket, index_gamma_markets, parse_market

CLOSED_AT = datetime(2026, 4, 19, 13, 13, 48, tzinfo=UTC)


def test_parse_market_keeps_complete_replay_fields() -> None:
    """A complete archive yields slug, close, delay, and tokens."""
    complete: dict[str, object] = {
        "id": "1",
        "conditionId": f"0x{'ab' * 32}",
        "slug": "demo-market",
        "closedTime": "2026-04-19 13:13:48+00",
        "secondsDelay": 3,
        "outcomes": json.dumps(["Team A", "Team B"]),
        "clobTokenIds": json.dumps(["111", "222"]),
    }
    parsed = parse_market(Market.parse_response(complete))
    assert parsed == GammaMarket(
        slug="demo-market",
        closed_at=CLOSED_AT,
        seconds_delay=3,
        token_ids=("111", "222"),
    )


def test_parse_market_rejects_incomplete_archives() -> None:
    """Archived markets without close time, delay, or both tokens are skipped."""
    complete: dict[str, object] = {
        "id": "1",
        "conditionId": f"0x{'ab' * 32}",
        "slug": "demo-market",
        "closedTime": "2026-04-19 13:13:48+00",
        "secondsDelay": 3,
        "outcomes": json.dumps(["Team A", "Team B"]),
        "clobTokenIds": json.dumps(["111", "222"]),
    }
    missing_close = {**complete, "closedTime": None}
    missing_delay = {**complete, "secondsDelay": None}
    missing_token = {**complete, "clobTokenIds": json.dumps(["111"])}
    assert parse_market(Market.parse_response(missing_close)) is None
    assert parse_market(Market.parse_response(missing_delay)) is None
    assert parse_market(Market.parse_response(missing_token)) is None


CONDITION_ID = f"0x{'ab' * 32}"
COMPLETE_MARKET: dict[str, object] = {
    "id": "1",
    "conditionId": CONDITION_ID,
    "slug": "demo-market",
    "closedTime": "2026-04-19 13:13:48+00",
    "secondsDelay": 3,
    "outcomes": json.dumps(["Team A", "Team B"]),
    "clobTokenIds": json.dumps(["111", "222"]),
}


def write_gamma_index_page(events_dir: Path) -> Path:
    """Write one gzipped Gamma events page under events_dir/<source>/."""
    source_dir = events_dir / "tag_65_closed"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / "page_1.json.gz"
    event = {
        "id": "1",
        "slug": "event-1",
        "title": "A vs B",
        "startTime": "2026-03-01T12:00:00Z",
        "markets": [COMPLETE_MARKET],
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump({"events": [event]}, stream)
    return path


def expected_gamma_market() -> GammaMarket:
    """The replay fields COMPLETE_MARKET parses to."""
    return GammaMarket(
        slug="demo-market",
        closed_at=CLOSED_AT,
        seconds_delay=3,
        token_ids=("111", "222"),
    )


def test_index_gamma_markets_cache_skips_gzip_on_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second call with unchanged page stamps does not reopen gzip."""
    events_dir = tmp_path / "events"
    write_gamma_index_page(events_dir)
    first = index_gamma_markets(events_dir)
    assert first[CONDITION_ID] == expected_gamma_market()

    def boom(path: Path) -> None:
        """Fail if the cache miss path re-parses pages."""
        raise AssertionError(f"gzip should not reopen {path}")

    monkeypatch.setattr(polymarket_mod, "read_polymarket_universe_page", boom)
    second = index_gamma_markets(events_dir)
    assert second == first


def test_index_gamma_markets_mtime_miss_reparses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed page mtime invalidates the index and re-parses gzip."""
    events_dir = tmp_path / "events"
    page = write_gamma_index_page(events_dir)
    index_gamma_markets(events_dir)
    calls: list[Path] = []
    real_read = polymarket_mod.read_polymarket_universe_page

    def counting_read(path: Path) -> object:
        """Record gzip reads then parse for real."""
        calls.append(path)
        return real_read(path)

    monkeypatch.setattr(polymarket_mod, "read_polymarket_universe_page", counting_read)
    index_gamma_markets(events_dir)
    assert calls == []
    stat = page.stat()
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    index_gamma_markets(events_dir)
    assert calls == [page]


def test_index_gamma_markets_concurrent_writes(tmp_path: Path) -> None:
    """Two writers must not share a tmp name; both calls return the same index."""
    events_dir = tmp_path / "events"
    write_gamma_index_page(events_dir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(index_gamma_markets, (events_dir, events_dir)))
    assert first == second
    assert first[CONDITION_ID] == expected_gamma_market()
