# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false
"""Synthetic Telonex catalog/channel fixtures for LoL Stage 02. No network."""

import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import typer

from lol.constants import (
    REASON_GAME_WINNER,
    REASON_TELONEX_DOWNLOADED,
    REASON_TELONEX_HTTP_404,
    REASON_TELONEX_MISSING_FROM_CATALOG,
    REASON_TELONEX_MISSING_INTERVAL,
    REASON_TELONEX_RETRIES_EXHAUSTED,
    REASON_TELONEX_SKIPPED_VALID,
    REASON_TELONEX_TOKEN_MISMATCH,
    REASON_UNSUPPORTED_CONTRACT,
    TELONEX_BOOK_CHANNEL,
)
from lol.types import LolContractKind, LolUniverseMarketRow

TEST_KEY = "test-key-do-not-log"
CID = "cid-1"
TOK_A = "tok-a"
TOK_B = "tok-b"
DAY_1 = "2026-08-01"
DAY_2 = "2026-08-02"


class TelonexModule(Protocol):
    """Importable behavior from the numbered LoL Telonex stage."""

    def http_stream(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> object:
        """Open one streaming GET. Tests replace this."""
        ...

    def download_one(self, api_key: str, job: object) -> object:
        """Download or skip one planned parquet job."""
        ...

    def fetch_telonex_books(
        self, universe_path: Path, books_dir: Path, catalog_path: Path, audit_path: Path
    ) -> None:
        """Slice the catalog and download included winner-market days."""
        ...


def load_telonex() -> TelonexModule:
    """Import the numbered LoL Telonex module through its typed test surface."""
    return cast(TelonexModule, cast(object, import_module("lol.02_fetch_telonex_books")))


def encode_list(values: list[str]) -> str:
    """Stable JSON list for parquet string columns."""
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def make_universe_row(
    condition_id: str,
    tokens: list[str],
    included: bool,
    reason: str,
) -> LolUniverseMarketRow:
    """One universe row with explicit tokens and inclusion."""
    kind: LolContractKind = "game_winner" if included else "other"
    return {
        "event_id": "e-1",
        "event_slug": "event-e-1",
        "market_id": "m-1",
        "condition_id": condition_id,
        "question": "LoL: T1 vs Gen.G",
        "group_item_title": "Game 1 Winner",
        "sports_market_type": "child_moneyline",
        "outcomes_json": encode_list(["T1", "Gen.G"]),
        "clob_token_ids_json": encode_list(tokens),
        "team_a": "T1",
        "team_b": "Gen.G",
        "game_number": 1,
        "best_of": 1,
        "league": "LCK",
        "scheduled_time": "2026-08-01T12:00:00Z",
        "scheduled_ts": 1782580800,
        "market_start_time": "2026-08-01T12:00:00Z",
        "market_end_time": "2026-08-01T14:00:00Z",
        "resolved_outcome": "T1",
        "resolved_outcome_index": 0,
        "contract_kind": kind,
        "included": included,
        "reason": reason,
    }


def included_row(condition_id: str, tokens: list[str]) -> LolUniverseMarketRow:
    """Resolved included Game N winner."""
    return make_universe_row(condition_id, tokens, True, REASON_GAME_WINNER)


def excluded_row(condition_id: str) -> LolUniverseMarketRow:
    """Unsupported contract that Stage 02 must ignore."""
    return make_universe_row(condition_id, ["x", "y"], False, REASON_UNSUPPORTED_CONTRACT)


def write_universe(path: Path, rows: list[LolUniverseMarketRow]) -> None:
    """Write a tiny markets.parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def date_array(raw: list[object], from_type: str) -> Any:
    """Build a catalog date column as UTC timestamps or ISO strings."""
    if from_type == "timestamp":
        return pa.array(raw, type=pa.timestamp("us", tz="UTC"))
    return pa.array([str(item) for item in raw], type=pa.string())


def catalog_table(
    rows: list[dict[str, object]],
    from_type: str,
) -> pa.Table:
    """Build a provider-shaped catalog table, optionally with timestamp dates."""
    market_ids = [str(row["market_id"]) for row in rows]
    asset_0 = [str(row["asset_id_0"]) for row in rows]
    asset_1 = [str(row["asset_id_1"]) for row in rows]
    extra = [str(row.get("extra_col", "keep-me")) for row in rows]
    book_from = [row["book_snapshot_full_from"] for row in rows]
    book_to = [row["book_snapshot_full_to"] for row in rows]
    return pa.table(
        {
            "market_id": pa.array(market_ids, type=pa.string()),
            "asset_id_0": pa.array(asset_0, type=pa.string()),
            "asset_id_1": pa.array(asset_1, type=pa.string()),
            "book_snapshot_full_from": date_array(book_from, from_type),
            "book_snapshot_full_to": date_array(book_to, from_type),
            "extra_col": pa.array(extra, type=pa.string()),
        }
    )


def catalog_bytes(
    rows: list[dict[str, object]],
    from_type: str,
) -> bytes:
    """Serialize a catalog table to parquet bytes."""
    buffer = BytesIO()
    pq.write_table(catalog_table(rows, from_type), buffer)
    return buffer.getvalue()


def default_catalog_row(
    market_id: str,
    asset_0: str,
    asset_1: str,
    start: object,
    end: object,
) -> dict[str, object]:
    """One catalog market with a book window and an extra column."""
    return {
        "market_id": market_id,
        "asset_id_0": asset_0,
        "asset_id_1": asset_1,
        "book_snapshot_full_from": start,
        "book_snapshot_full_to": end,
        "extra_col": "keep-me",
    }


def book_table(asset_id: str, row_count: int) -> pa.Table:
    """Tiny book_snapshot_full table with required columns."""
    n = row_count
    return pa.table(
        {
            "timestamp_us": pa.array([1] * n, type=pa.int64()),
            "local_timestamp_us": pa.array([1] * n, type=pa.int64()),
            "exchange": pa.array(["polymarket"] * n),
            "market_id": pa.array([CID] * n),
            "slug": pa.array(["slug"] * n),
            "asset_id": pa.array([asset_id] * n),
            "outcome": pa.array(["Yes"] * n),
            "bids": pa.array([[{"price": 0.4, "size": 1.0}]] * n),
            "asks": pa.array([[{"price": 0.6, "size": 1.0}]] * n),
        }
    )


def parquet_bytes(table: pa.Table) -> bytes:
    """Serialize one Arrow table to parquet bytes."""
    buffer = BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def book_bytes(asset_id: str) -> bytes:
    """Serialize one valid book parquet."""
    return parquet_bytes(book_table(asset_id, 1))


def write_book(path: Path, asset_id: str) -> None:
    """Write a valid book parquet to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(book_table(asset_id, 1), path)


@contextmanager
def as_stream(response: httpx.Response) -> Generator[httpx.Response]:
    """Yield a fake httpx response as a streaming context manager."""
    yield response


def status_response(url: str, status: int) -> httpx.Response:
    """Empty HTTP response with a status code."""
    return httpx.Response(
        status,
        content=b"",
        request=httpx.Request("GET", url),
        headers={"Retry-After": "1"},
    )


def body_response(url: str, body: bytes, status: int) -> httpx.Response:
    """HTTP response carrying parquet bytes."""
    return httpx.Response(status, content=body, request=httpx.Request("GET", url))


def read_audit(path: Path) -> pd.DataFrame:
    """Load download_audit.parquet."""
    return pd.read_parquet(path)


def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Disable backoff sleeps and record the durations that would have been used."""
    sleeps: list[float] = []

    def record(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", record)
    return sleeps


def assert_auth_headers(url: str, headers: dict[str, str], params: dict[str, str]) -> None:
    """Catalog is unauthenticated; every channel download sends Bearer, never as a query."""
    if "/datasets/" in url:
        assert "Authorization" not in headers
        return
    assert headers.get("Authorization") == f"Bearer {TEST_KEY}"
    assert TEST_KEY not in params.values()


def parse_download_url(url: str) -> tuple[str, str]:
    """Return (channel, day) from a Telonex download URL."""
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]


def run_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    universe: list[LolUniverseMarketRow],
    fake_stream: object,
) -> tuple[TelonexModule, Path, Path, Path, list[float]]:
    """Write universe, patch HTTP/sleep/key, and run Stage 02 into tmp_path."""
    module = load_telonex()
    monkeypatch.setenv("TELONEX_API_KEY", TEST_KEY)
    monkeypatch.setattr(module, "http_stream", fake_stream)
    sleeps = patch_sleep(monkeypatch)
    universe_path = tmp_path / "markets.parquet"
    write_universe(universe_path, universe)
    books_dir = tmp_path / "book_snapshot_full"
    catalog_path = tmp_path / "catalog.parquet"
    audit_path = tmp_path / "download_audit.parquet"
    module.fetch_telonex_books(universe_path, books_dir, catalog_path, audit_path)
    return module, books_dir, catalog_path, audit_path, sleeps


def two_day_catalog(asset_0: str, asset_1: str) -> list[dict[str, object]]:
    """Included market plus an extra condition id, two UTC days."""
    return [
        default_catalog_row(CID, asset_0, asset_1, DAY_1, "2026-08-03"),
        default_catalog_row("cid-extra", "tok-x", "tok-y", DAY_1, "2026-08-03"),
    ]


def one_day_catalog() -> list[dict[str, object]]:
    """Included market, one UTC day."""
    return [default_catalog_row(CID, TOK_A, TOK_B, DAY_1, DAY_2)]


def catalog_then_books(
    catalog_rows: list[dict[str, object]],
    download_status: dict[tuple[str, str, str], list[int]],
    from_type: str,
) -> object:
    """http_stream fake: one catalog body, then per (channel, date, asset_id) status queue."""
    catalog_body = catalog_bytes(catalog_rows, from_type)
    queues = {key: list(values) for key, values in download_status.items()}

    def fake_stream(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            return as_stream(body_response(url, catalog_body, 200))
        asset_id = params["asset_id"]
        channel, day = parse_download_url(url)
        remaining = queues.get((channel, day, asset_id))
        status = 200
        if remaining:
            status = remaining.pop(0)
        if status != 200:
            return as_stream(status_response(url, status))
        return as_stream(body_response(url, book_bytes(asset_id), 200))

    return fake_stream


def catalog_only_stream(
    catalog_rows: list[dict[str, object]], from_type: str
) -> tuple[object, dict[str, int]]:
    """Catalog 200; any channel download URL is a test failure."""
    catalog_body = catalog_bytes(catalog_rows, from_type)
    hits = {"n": 0}

    def fake_stream(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            return as_stream(body_response(url, catalog_body, 200))
        hits["n"] += 1
        raise AssertionError("channel HTTP should not run")

    return fake_stream, hits


def test_catalog_slice_keeps_included_schema_and_deletes_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written catalog has only the included id, extra column, and no full temp."""
    run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B]), excluded_row("cid-excluded")],
        catalog_then_books(two_day_catalog(TOK_A, TOK_B), {}, "string"),
    )
    catalog = pq.read_table(tmp_path / "catalog.parquet")
    assert catalog.column("market_id").to_pylist() == [CID]
    assert "extra_col" in catalog.schema.names
    assert catalog.column("extra_col").to_pylist() == ["keep-me"]
    assert not (tmp_path / "markets.full.parquet").exists()
    assert not (tmp_path / "markets.full.parquet.partial").exists()


def test_token_set_match_and_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Swapped catalog ids still download; one wrong id audits token_id_mismatch."""
    run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(two_day_catalog(TOK_B, TOK_A), {}, "string"),
    )
    books_dir = tmp_path / "book_snapshot_full"
    assert (books_dir / f"asset_id={TOK_A}" / f"{DAY_1}.parquet").exists()
    assert (books_dir / f"asset_id={TOK_B}" / f"{DAY_2}.parquet").exists()
    assert not (tmp_path / "onchain_fills").exists()

    mismatch_dir = tmp_path / "mismatch"
    download_calls: list[str] = []

    def no_download(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            body = catalog_bytes(
                [default_catalog_row(CID, TOK_A, "tok-wrong", DAY_1, "2026-08-03")],
                "string",
            )
            return as_stream(body_response(url, body, 200))
        download_calls.append(url)
        raise AssertionError("download HTTP should not run on token mismatch")

    with pytest.raises(typer.Exit) as exc:
        run_fetch(monkeypatch, mismatch_dir, [included_row(CID, [TOK_A, TOK_B])], no_download)
    assert exc.value.exit_code == 1
    assert download_calls == []
    audit = read_audit(mismatch_dir / "download_audit.parquet")
    assert list(audit["reason"]) == [REASON_TELONEX_TOKEN_MISMATCH]
    assert list(audit["channel"]) == [""]
    assert bool(audit.iloc[0]["complete"]) is False


def test_interval_expands_half_open_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[2026-08-01, 2026-08-03) plans four book jobs; from==to is missing_interval."""
    _, books_dir, _, audit_path, _ = run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(two_day_catalog(TOK_A, TOK_B), {}, "string"),
    )
    audit = read_audit(audit_path)
    assert len(audit) == 4
    dates = set(audit["date"].astype(str))
    assert dates == {DAY_1, DAY_2}
    assert set(audit["asset_id"]) == {TOK_A, TOK_B}
    assert set(audit["channel"]) == {TELONEX_BOOK_CHANNEL}
    assert (books_dir / f"asset_id={TOK_A}" / f"{DAY_1}.parquet").exists()
    assert not (books_dir / f"asset_id={TOK_A}" / "2026-08-03.parquet").exists()

    empty_dir = tmp_path / "empty-interval"
    download_hits = {"n": 0}

    def catalog_only(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            body = catalog_bytes(
                [default_catalog_row(CID, TOK_A, TOK_B, DAY_1, DAY_1)],
                "string",
            )
            return as_stream(body_response(url, body, 200))
        download_hits["n"] += 1
        raise AssertionError("no day jobs when from==to")

    with pytest.raises(typer.Exit) as exc:
        run_fetch(monkeypatch, empty_dir, [included_row(CID, [TOK_A, TOK_B])], catalog_only)
    assert exc.value.exit_code == 1
    assert download_hits["n"] == 0
    miss = read_audit(empty_dir / "download_audit.parquet")
    assert list(miss["reason"]) == [REASON_TELONEX_MISSING_INTERVAL]
    assert set(miss["channel"]) == {TELONEX_BOOK_CHANNEL}


def test_iso_timestamp_from_to_uses_utc_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timestamp 2026-08-01T05:00:00Z .. 2026-08-03T00:00:00Z expands to 01 and 02."""
    rows = [
        default_catalog_row(
            CID,
            TOK_A,
            TOK_B,
            datetime(2026, 8, 1, 5, 0, tzinfo=UTC),
            datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
        )
    ]
    _, _, _, audit_path, _ = run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(rows, {}, "timestamp"),
    )
    dates = set(read_audit(audit_path)["date"].astype(str))
    assert dates == {DAY_1, DAY_2}


def test_missing_catalog_row_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Included id absent from the catalog is missing_from_catalog and exit 1."""
    download_hits = {"n": 0}

    def extra_only(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            body = catalog_bytes(
                [default_catalog_row("cid-extra", "tok-x", "tok-y", DAY_1, "2026-08-03")],
                "string",
            )
            return as_stream(body_response(url, body, 200))
        download_hits["n"] += 1
        raise AssertionError("no download HTTP when catalog misses the market")

    with pytest.raises(typer.Exit) as exc:
        run_fetch(monkeypatch, tmp_path, [included_row(CID, [TOK_A, TOK_B])], extra_only)
    assert exc.value.exit_code == 1
    assert download_hits["n"] == 0
    audit = read_audit(tmp_path / "download_audit.parquet")
    assert list(audit["reason"]) == [REASON_TELONEX_MISSING_FROM_CATALOG]
    assert list(audit["channel"]) == [""]


def test_validation_skip_valid_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid existing book parquets skip HTTP."""
    for token in (TOK_A, TOK_B):
        for day in (DAY_1, DAY_2):
            write_book(
                tmp_path / TELONEX_BOOK_CHANNEL / f"asset_id={token}" / f"{day}.parquet", token
            )
    fake, hits = catalog_only_stream(two_day_catalog(TOK_A, TOK_B), "string")
    _, _, _, audit_path, _ = run_fetch(
        monkeypatch, tmp_path, [included_row(CID, [TOK_A, TOK_B])], fake
    )
    assert hits["n"] == 0
    reasons = set(read_audit(audit_path)["reason"])
    assert reasons == {REASON_TELONEX_SKIPPED_VALID}


def test_partial_replaced_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful download leaves the target and no sibling .partial."""
    _, books_dir, _, _, _ = run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(one_day_catalog(), {}, "string"),
    )
    target = books_dir / f"asset_id={TOK_A}" / f"{DAY_1}.parquet"
    assert target.exists()
    assert not target.with_suffix(target.suffix + ".partial").exists()
    leftovers = list(tmp_path.rglob("*.partial"))
    assert leftovers == []


def test_backoff_then_download_and_retries_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five 429 then 200 downloads; six 429 audits retries_exhausted."""
    ok_status = {
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_A): [429, 429, 429, 429, 429, 200],
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_B): [429, 429, 429, 429, 429, 200],
    }
    _, _, _, audit_path, sleeps = run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(one_day_catalog(), ok_status, "string"),
    )
    reasons = set(read_audit(audit_path)["reason"])
    assert reasons == {REASON_TELONEX_DOWNLOADED}
    assert sleeps
    assert all(duration == 1.0 for duration in sleeps)

    fail_dir = tmp_path / "exhausted"
    fail_status = {
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_A): [429, 429, 429, 429, 429, 429],
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_B): [429, 429, 429, 429, 429, 429],
    }
    with pytest.raises(typer.Exit) as exc:
        run_fetch(
            monkeypatch,
            fail_dir,
            [included_row(CID, [TOK_A, TOK_B])],
            catalog_then_books(one_day_catalog(), fail_status, "string"),
        )
    assert exc.value.exit_code == 1
    fail_audit = read_audit(fail_dir / "download_audit.parquet")
    book_fail = fail_audit[fail_audit["channel"] == TELONEX_BOOK_CHANNEL]
    assert set(book_fail["reason"]) == {REASON_TELONEX_RETRIES_EXHAUSTED}
    assert bool(book_fail["complete"].all()) is False


def test_http_403_aborts_without_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP 403 aborts the run and does not write download_audit.parquet."""

    def forbidden(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        if "/datasets/" in url:
            return as_stream(
                body_response(url, catalog_bytes(two_day_catalog(TOK_A, TOK_B), "string"), 200)
            )
        return as_stream(status_response(url, 403))

    with pytest.raises(RuntimeError, match="HTTP 403"):
        run_fetch(monkeypatch, tmp_path, [included_row(CID, [TOK_A, TOK_B])], forbidden)
    assert not (tmp_path / "download_audit.parquet").exists()
    captured = capsys.readouterr()
    blob = captured.out + captured.err + caplog.text
    assert TEST_KEY not in blob


def test_http_401_does_not_log_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 401 path never prints the fixture API key."""

    def unauthorized(
        _client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> object:
        assert_auth_headers(url, headers, params)
        return as_stream(status_response(url, 401))

    with pytest.raises(RuntimeError, match="HTTP 401"):
        run_fetch(monkeypatch, tmp_path, [included_row(CID, [TOK_A, TOK_B])], unauthorized)
    captured = capsys.readouterr()
    blob = captured.out + captured.err + caplog.text
    assert TEST_KEY not in blob
    assert not (tmp_path / "download_audit.parquet").exists()


def test_http_404_continues_sibling_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A book 404 is audited; the sibling still downloads; incomplete required jobs exit 1."""
    statuses = {(TELONEX_BOOK_CHANNEL, DAY_1, TOK_A): [404]}
    with pytest.raises(typer.Exit) as exc:
        run_fetch(
            monkeypatch,
            tmp_path,
            [included_row(CID, [TOK_A, TOK_B])],
            catalog_then_books(one_day_catalog(), statuses, "string"),
        )
    assert exc.value.exit_code == 1
    audit = read_audit(tmp_path / "download_audit.parquet")
    books = audit[audit["channel"] == TELONEX_BOOK_CHANNEL]
    by_asset = {str(row["asset_id"]): row for row in books.to_dict(orient="records")}
    assert str(by_asset[TOK_A]["reason"]) == REASON_TELONEX_HTTP_404
    assert bool(by_asset[TOK_A]["complete"]) is False
    assert str(by_asset[TOK_B]["reason"]) == REASON_TELONEX_DOWNLOADED
    assert bool(by_asset[TOK_B]["complete"]) is True
    books_dir = tmp_path / "book_snapshot_full"
    assert (books_dir / f"asset_id={TOK_B}" / f"{DAY_1}.parquet").exists()
    assert not (books_dir / f"asset_id={TOK_A}" / f"{DAY_1}.parquet").exists()


def test_missing_key_before_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset TELONEX_API_KEY fails before any HTTP."""
    module = load_telonex()
    monkeypatch.delenv("TELONEX_API_KEY", raising=False)
    hits = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> object:
        hits["n"] += 1
        raise AssertionError("HTTP must not run without a key")

    monkeypatch.setattr(module, "http_stream", boom)
    write_universe(tmp_path / "markets.parquet", [included_row(CID, [TOK_A, TOK_B])])
    with pytest.raises(RuntimeError, match="TELONEX_API_KEY is required"):
        module.fetch_telonex_books(
            tmp_path / "markets.parquet",
            tmp_path / "book_snapshot_full",
            tmp_path / "catalog.parquet",
            tmp_path / "download_audit.parquet",
        )
    assert hits["n"] == 0


def test_downloads_books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty capture root downloads books for both tokens."""
    _, _, _, audit_path, _ = run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(one_day_catalog(), {}, "string"),
    )
    for token in (TOK_A, TOK_B):
        path = tmp_path / TELONEX_BOOK_CHANNEL / f"asset_id={token}" / f"{DAY_1}.parquet"
        assert path.exists(), path
    audit = read_audit(audit_path)
    assert set(audit["channel"]) == {TELONEX_BOOK_CHANNEL}
    assert set(audit["reason"]) == {REASON_TELONEX_DOWNLOADED}
    assert len(audit) == 2
    assert not (tmp_path / "onchain_fills").exists()


def test_per_channel_partial_retry_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """429 then 200 leaves the book target and no .partial."""
    statuses = {
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_A): [429, 200],
        (TELONEX_BOOK_CHANNEL, DAY_1, TOK_B): [429, 200],
    }
    run_fetch(
        monkeypatch,
        tmp_path,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(one_day_catalog(), statuses, "string"),
    )
    target = tmp_path / TELONEX_BOOK_CHANNEL / f"asset_id={TOK_A}" / f"{DAY_1}.parquet"
    assert target.exists()
    assert pq.ParquetFile(target).metadata is not None
    assert not target.with_suffix(target.suffix + ".partial").exists()
    assert list(tmp_path.rglob("*.partial")) == []


def test_channel_audit_distinguishes_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit has a channel column, stable sort, and is rebuilt rather than appended."""
    for token in (TOK_A, TOK_B):
        write_book(
            tmp_path / "book_snapshot_full" / f"asset_id={token}" / f"{DAY_1}.parquet", token
        )
    fake, hits = catalog_only_stream(one_day_catalog(), "string")
    _, _, _, audit_path, _ = run_fetch(
        monkeypatch, tmp_path, [included_row(CID, [TOK_A, TOK_B])], fake
    )
    assert hits["n"] == 0
    audit = read_audit(audit_path)
    assert "channel" in audit.columns
    assert set(audit["channel"]) == {TELONEX_BOOK_CHANNEL}
    assert set(audit["reason"]) == {REASON_TELONEX_SKIPPED_VALID}
    sort_keys = ["condition_id", "channel", "asset_id", "date"]
    resorted = audit.sort_values(sort_keys, kind="mergesort").reset_index(drop=True)
    actual = audit[sort_keys].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, resorted[sort_keys])

    rebuild_dir = tmp_path / "rebuild"
    run_fetch(
        monkeypatch,
        rebuild_dir,
        [included_row(CID, [TOK_A, TOK_B])],
        catalog_then_books(one_day_catalog(), {}, "string"),
    )
    first = read_audit(rebuild_dir / "download_audit.parquet")
    assert len(first) == 2
    assert set(first["reason"]) == {REASON_TELONEX_DOWNLOADED}
    skip_stream, skip_hits = catalog_only_stream(one_day_catalog(), "string")
    run_fetch(monkeypatch, rebuild_dir, [included_row(CID, [TOK_A, TOK_B])], skip_stream)
    second = read_audit(rebuild_dir / "download_audit.parquet")
    assert skip_hits["n"] == 0
    assert len(second) == 2
    assert set(second["reason"]) == {REASON_TELONEX_SKIPPED_VALID}


def test_keyboard_interrupt_writes_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C during downloads still writes the channel audit."""
    module = load_telonex()

    def boom(_api_key: str, _job: object) -> object:
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, "download_one", boom)
    with pytest.raises(KeyboardInterrupt):
        run_fetch(
            monkeypatch,
            tmp_path,
            [
                included_row(CID, [TOK_A, TOK_B]),
                included_row("cid-gone", [TOK_A, TOK_B]),
            ],
            catalog_then_books(one_day_catalog(), {}, "string"),
        )
    audit = read_audit(tmp_path / "download_audit.parquet")
    assert "channel" in audit.columns
    assert REASON_TELONEX_MISSING_FROM_CATALOG in set(audit["reason"])
