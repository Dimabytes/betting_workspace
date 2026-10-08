"""Synthetic livestats window fixtures for the LoL fetch stage. No network."""

import gzip
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import httpx
import pandas as pd
import pytest
import typer

from lol.constants import (
    LOL_FETCH_STALL_RESPONSE_LIMIT,
    REASON_DOWNLOAD_COMPLETE,
    REASON_EMPTY_BODY,
    REASON_FEED_ENDED_NO_FLAG,
    REASON_HTTP_404,
    REASON_RETRIES_EXHAUSTED,
    REASON_WALL_TIME_LIMIT,
)
from lol.types import LolLinkAssignment, LolLinkRow, LolRadiantTokenIndex

ANCHOR = datetime(2026, 3, 1, 12, 5, 3, tzinfo=UTC)
ANCHOR_TS = int(ANCHOR.timestamp())
T0 = ANCHOR_TS - (ANCHOR_TS % 10)
T0_STARTING = "2026-03-01T12:05:00.000Z"


class FetchModule(Protocol):
    """Importable behavior from the numbered LoL fetch stage."""

    LOL_FETCH_END_SECOND: int
    LOL_FETCH_MAX_WALL_SECONDS: int

    def fetch_lolesports(
        self, links_path: Path, windows_dir: Path, details_dir: Path, audit_path: Path, workers: int
    ) -> None:
        """Download livestats windows and details for every accepted link and write the audit."""
        ...

    def write_gzip_jsonl(self, path: Path, payloads: Sequence[object]) -> None:
        """Write raw window objects as gzip JSONL."""
        ...

    def append_gzip_jsonl(self, path: Path, payload: object) -> None:
        """Append one window as its own gzip member."""
        ...

    def read_gzip_jsonl(self, path: Path) -> list[object]:
        """Read one gzip JSONL archive into parsed JSON values."""
        ...

    def archive_path(self, windows_dir: Path, esports_game_id: str) -> Path:
        """Gzip JSONL path for one map's raw window archive."""
        ...


def load_fetch() -> FetchModule:
    """Import the numbered LoL fetch module through its typed test surface."""
    return cast(FetchModule, cast(object, import_module("lol.04_fetch_lolesports")))


def load_link_stage() -> object:
    """Import Stage 03 so tests can patch http_get on the name fetch_json uses."""
    return import_module("lol.03_link_lolesports")


def iso_z(ts: int) -> str:
    """UTC Z stamp without fractional seconds."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def starting_time_for(ts: int) -> str:
    """startingTime query value for a grid timestamp."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def participant(gold: int) -> dict[str, object]:
    """One livestats participant with totalGold."""
    return {"totalGold": gold}


def frame_at(ts: int, gold: int) -> dict[str, object]:
    """One window frame at unix seconds with both sides sharing the same gold."""
    people = [participant(gold)]
    return {
        "rfc460Timestamp": iso_z(ts),
        "blueTeam": {"participants": people},
        "redTeam": {"participants": people},
    }


def window_body(game_id: str, frames: list[dict[str, object]]) -> dict[str, object]:
    """Raw provider window object."""
    return {"esportsGameId": game_id, "frames": frames}


def gold_window(game_id: str, ts: int) -> dict[str, object]:
    """Window whose single frame is spawn gold at ts."""
    return window_body(game_id, [frame_at(ts, 500)])


def hz_gold_window(game_id: str, start_ts: int, count: int) -> dict[str, object]:
    """Window with 1 Hz gold frames so 10s steps are not false pauses."""
    return window_body(game_id, [frame_at(start_ts + offset, 500) for offset in range(count)])


def paused_window(game_id: str, ts: int) -> dict[str, object]:
    """Window whose frame repeats the spawn timestamp while the game is paused."""
    frame = frame_at(ts, 500)
    frame["gameState"] = "paused"
    return window_body(game_id, [frame])


def finished_window(game_id: str, ts: int) -> dict[str, object]:
    """Window whose final frame carries Riot's terminal game state."""
    frame = frame_at(ts, 500)
    frame["gameState"] = "finished"
    return window_body(game_id, [frame])


def make_link(esports_game_id: str, event_id: str, game_number: int) -> LolLinkRow:
    """One accepted links.parquet row with the 12:05:03 loading anchor."""
    radiant: LolRadiantTokenIndex = 0
    assignment: LolLinkAssignment = "game_winner"
    return {
        "event_id": event_id,
        "market_id": f"{event_id}-m",
        "condition_id": f"cid-{event_id}",
        "outcomes_json": '["T1","Gen.G"]',
        "clob_token_ids_json": '["tok-a","tok-b"]',
        "esports_game_id": esports_game_id,
        "esports_match_id": f"match-{event_id}",
        "game_number": game_number,
        "loading_anchor": "2026-03-01T12:05:03Z",
        "loading_anchor_ts": ANCHOR_TS,
        "radiant_token_index": radiant,
        "resolved_outcome": "T1",
        "resolved_outcome_index": 0,
        "blue_esports_team_id": "blue-1",
        "red_esports_team_id": "red-1",
        "assignment": assignment,
    }


def write_links(path: Path, links: list[LolLinkRow]) -> None:
    """Write a tiny links.parquet for fetch tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(links).to_parquet(path, index=False)


def json_response(url: str, payload: object) -> httpx.Response:
    """HTTP 200 JSON response for a monkeypatched GET."""
    return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


def status_response(url: str, status: int) -> httpx.Response:
    """HTTP response with no JSON body."""
    return httpx.Response(status, request=httpx.Request("GET", url))


def empty_response(url: str) -> httpx.Response:
    """HTTP 200 with an empty body."""
    return httpx.Response(200, content=b"", request=httpx.Request("GET", url))


def read_jsonl(path: Path) -> list[object]:
    """Read a gzip JSONL archive for assertions."""
    payloads: list[object] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payloads.append(json.loads(text))
    return payloads


def read_audit(path: Path) -> pd.DataFrame:
    """Load download_audit.parquet."""
    return pd.read_parquet(path)


def audit_row(path: Path, game_id: str) -> pd.Series:
    """Return the unique audit row for one map."""
    frame = read_audit(path)
    matched = frame[frame["esports_game_id"] == game_id]
    assert len(matched) == 1, game_id
    return matched.iloc[0]


def patch_http(monkeypatch: pytest.MonkeyPatch, fake_get: Callable[..., httpx.Response]) -> None:
    """Patch Stage 03 http_get, the name fetch_json looks up."""
    monkeypatch.setattr(load_link_stage(), "http_get", fake_get)


TEST_WORKERS = 8


def run_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    links: list[LolLinkRow],
    fake_get: Callable[..., httpx.Response],
    end_second: int,
    max_wall: int,
) -> tuple[FetchModule, Path, Path]:
    """Write links, patch HTTP/limits, and run Stage 04 into tmp_path."""
    module = load_fetch()
    patch_http(monkeypatch, fake_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", end_second)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", max_wall)
    links_path = tmp_path / "links.parquet"
    write_links(links_path, links)
    windows_dir = tmp_path / "windows"
    audit_path = tmp_path / "download_audit.parquet"
    module.fetch_lolesports(links_path, windows_dir, tmp_path / "details", audit_path, TEST_WORKERS)
    return module, windows_dir, audit_path


def gold_at_query(
    _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
) -> httpx.Response:
    """Return one gold frame at the requested startingTime."""
    game_id = url.rsplit("/", 1)[-1]
    starting = params["startingTime"]
    parsed = datetime.fromisoformat(starting.replace("Z", "+00:00"))
    ts = int(parsed.timestamp())
    return json_response(url, hz_gold_window(game_id, ts, 10))


def test_grid_rounds_anchor_then_steps_ten_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anchor 12:05:03Z starts at 12:05:00.000Z, then +10s until game second 20."""
    starts: list[str] = []

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        starts.append(params["startingTime"])
        return gold_at_query(client, url, headers, params)

    run_fetch(monkeypatch, tmp_path, [make_link("g-grid", "e-grid", 1)], fake_get, 20, 3600)
    assert starts[0] == T0_STARTING
    assert starts[1] == starting_time_for(T0 + 10)
    assert starts[-1] == starting_time_for(T0 + 20)


def test_pause_extends_requests_past_wall_minus_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading→spawn 6s is not a pause; a >5s post-spawn gap is and delays stop."""
    starts: list[str] = []

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        starting = params["startingTime"]
        starts.append(starting)
        if starting == starting_time_for(T0):
            payload = window_body(
                "g-pause", [frame_at(T0, 0), frame_at(T0 + 6, 500), frame_at(T0 + 8, 500)]
            )
        elif starting == starting_time_for(T0 + 10):
            payload = window_body("g-pause", [])
        else:
            parsed = datetime.fromisoformat(starting.replace("Z", "+00:00"))
            payload = hz_gold_window("g-pause", int(parsed.timestamp()), 10)
        return json_response(url, payload)

    run_fetch(monkeypatch, tmp_path, [make_link("g-pause", "e-pause", 1)], fake_get, 20, 3600)
    assert starts[-1] == starting_time_for(T0 + 30)
    assert starting_time_for(T0 + 20) in starts
    assert starting_time_for(T0 + 40) not in starts


def test_wall_safety_completes_without_end_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hitting the wall-time cap with saved lines is complete, reason wall_time_limit."""
    _, windows_dir, audit_path = run_fetch(
        monkeypatch, tmp_path, [make_link("g-wall", "e-wall", 1)], gold_at_query, 9999, 30
    )
    row = audit_row(audit_path, "g-wall")
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_WALL_TIME_LIMIT
    target = windows_dir / "g-wall.jsonl.gz"
    assert target.exists()
    assert not (windows_dir / "g-wall.jsonl.gz.partial").exists()
    assert not list(windows_dir.glob("*.tmp"))
    payloads = read_jsonl(target)
    assert payloads
    first = payloads[0]
    assert isinstance(first, dict)
    assert "startingTime" not in first
    assert "body" not in first
    assert first["esportsGameId"] == "g-wall"
    assert "frames" in first
    details_target = tmp_path / "details" / "g-wall.jsonl.gz"
    assert details_target.exists()
    assert not (tmp_path / "details" / "g-wall.jsonl.gz.partial").exists()
    details_payloads = read_jsonl(details_target)
    assert len(details_payloads) == len(payloads)
    assert int(row["details_response_count"]) == len(details_payloads)


def test_partial_rewrite_keeps_previous_on_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash while rewriting .partial leaves the previous valid file in place."""
    module = load_fetch()
    path = tmp_path / "g-crash.jsonl.gz.partial"
    module.write_gzip_jsonl(path, [gold_window("g-crash", T0)])
    before = path.read_bytes()

    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("crash during gzip")

    monkeypatch.setattr(gzip, "GzipFile", boom)
    with pytest.raises(OSError, match="crash during gzip"):
        module.write_gzip_jsonl(path, [gold_window("g-crash", T0), gold_window("g-crash", T0 + 10)])
    assert path.read_bytes() == before


def test_resume_keeps_target_prefix_when_partial_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the first appended window must not drop the target prefix on resume."""
    module = load_fetch()
    windows_dir = tmp_path / "windows"
    game_id = "g-seed"
    target = module.archive_path(windows_dir, game_id)
    prefix = window_body(game_id, [frame_at(T0, 0), frame_at(T0 + 5, 500), frame_at(T0 + 12, 500)])
    module.write_gzip_jsonl(target, [prefix])
    original_append = module.append_gzip_jsonl

    def crash_after_first(path: Path, payload: object) -> None:
        original_append(path, payload)
        raise OSError("crash after first window")

    monkeypatch.setattr(module, "append_gzip_jsonl", crash_after_first)
    patch_http(monkeypatch, gold_at_query)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 30)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    write_links(tmp_path / "links.parquet", [make_link(game_id, "e-seed", 1)])
    with pytest.raises(OSError, match="crash after first window"):
        module.fetch_lolesports(
            tmp_path / "links.parquet",
            windows_dir,
            tmp_path / "details",
            tmp_path / "download_audit.parquet",
            TEST_WORKERS,
        )
    monkeypatch.setattr(module, "append_gzip_jsonl", original_append)
    module.fetch_lolesports(
        tmp_path / "links.parquet",
        windows_dir,
        tmp_path / "details",
        tmp_path / "download_audit.parquet",
        TEST_WORKERS,
    )
    payloads = read_jsonl(target)
    assert payloads[0] == prefix
    assert len(payloads) > 1


def test_resume_starts_after_last_saved_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid incomplete archive resumes at floor_10(last stamp)+10, not t0."""
    module = load_fetch()
    windows_dir = tmp_path / "windows"
    target = module.archive_path(windows_dir, "g-resume")
    module.write_gzip_jsonl(
        target,
        [window_body("g-resume", [frame_at(T0, 0), frame_at(T0 + 5, 500), frame_at(T0 + 12, 500)])],
    )
    starts: list[str] = []

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" in url:
            starts.append(params["startingTime"])
        return gold_at_query(client, url, headers, params)

    links_path = tmp_path / "links.parquet"
    write_links(links_path, [make_link("g-resume", "e-resume", 1)])
    patch_http(monkeypatch, fake_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 30)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    module.fetch_lolesports(
        links_path, windows_dir, tmp_path / "details", tmp_path / "download_audit.parquet", 8
    )
    assert starts[0] == starting_time_for(T0 + 20)
    assert T0_STARTING not in starts


def test_complete_archive_skips_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A published complete archive skips HTTP."""
    module = load_fetch()
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 20)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    windows_dir = tmp_path / "windows"
    details_dir = tmp_path / "details"
    complete_payloads = [hz_gold_window("g-skip", T0, 21)]
    windows_target = module.archive_path(windows_dir, "g-skip")
    details_target = module.archive_path(details_dir, "g-skip")
    module.write_gzip_jsonl(windows_target, complete_payloads)
    module.write_gzip_jsonl(details_target, complete_payloads)
    windows_partial = windows_target.with_suffix(windows_target.suffix + ".partial")
    details_partial = details_target.with_suffix(details_target.suffix + ".partial")
    windows_partial.write_bytes(b"stale")
    details_partial.write_bytes(b"stale")
    calls = {"n": 0}

    def fail_if_called(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        calls["n"] += 1
        raise AssertionError(f"http_get should not be called: {url}")

    links_path = tmp_path / "links.parquet"
    write_links(links_path, [make_link("g-skip", "e-skip", 1)])
    patch_http(monkeypatch, fail_if_called)

    def fail_download(*_args: object) -> object:
        raise AssertionError("cached map must not enter download_one_map")

    monkeypatch.setattr(module, "download_one_map", fail_download)

    def fail_bulk_read(*_args: object) -> object:
        raise AssertionError("cached audit must not load the whole archive")

    monkeypatch.setattr(module, "read_gzip_jsonl", fail_bulk_read)
    module.fetch_lolesports(
        links_path, windows_dir, details_dir, tmp_path / "audit-skip.parquet", TEST_WORKERS
    )
    assert calls["n"] == 0
    assert not windows_partial.exists()
    assert not details_partial.exists()


def test_finished_frame_completes_without_walking_the_wall_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first terminal frame completes the map without post-game requests."""
    window_starts: list[str] = []

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" in url:
            window_starts.append(params["startingTime"])
        return json_response(url, finished_window("g-finished", T0))

    _, windows_dir, audit_path = run_fetch(
        monkeypatch,
        tmp_path,
        [make_link("g-finished", "e-finished", 1)],
        fake_get,
        9999,
        3600,
    )
    row = audit_row(audit_path, "g-finished")
    assert window_starts == [T0_STARTING]
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_DOWNLOAD_COMPLETE
    assert len(read_jsonl(windows_dir / "g-finished.jsonl.gz")) == 1


def test_frozen_feed_stops_complete_without_saving_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten repeated in_game timestamps mean the feed ended: stop complete, append nothing."""
    window_starts: list[str] = []

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" in url:
            window_starts.append(params["startingTime"])
        return json_response(url, gold_window("g-stalled", T0))

    _, _, audit_path = run_fetch(
        monkeypatch,
        tmp_path,
        [make_link("g-stalled", "e-stalled", 1)],
        fake_get,
        9999,
        3600,
    )
    row = audit_row(audit_path, "g-stalled")
    assert len(window_starts) == 11
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_FEED_ENDED_NO_FLAG
    assert int(row["window_response_count"]) == 1


def test_pause_longer_than_stall_limit_keeps_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paused feed does not count as a stall; the walk resumes when frames advance."""
    calls = {"n": 0}
    resume_after = LOL_FETCH_STALL_RESPONSE_LIMIT + 5

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" not in url:
            return json_response(url, {"frames": []})
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response(url, gold_window("g-paused", T0))
        if calls["n"] <= resume_after:
            return json_response(url, paused_window("g-paused", T0))
        return json_response(url, finished_window("g-paused", T0 + 600))

    _, windows_dir, audit_path = run_fetch(
        monkeypatch,
        tmp_path,
        [make_link("g-paused", "e-paused", 1)],
        fake_get,
        9999,
        3600,
    )
    row = audit_row(audit_path, "g-paused")
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_DOWNLOAD_COMPLETE
    assert len(read_jsonl(windows_dir / "g-paused.jsonl.gz")) == 2


def test_terminal_partial_backfills_details_without_bulk_loading_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal resume file is published and only its missing details are fetched."""
    module = load_fetch()
    game_id = "g-terminal-partial"
    windows_dir = tmp_path / "windows"
    target = module.archive_path(windows_dir, game_id)
    partial = target.with_suffix(target.suffix + ".partial")
    module.write_gzip_jsonl(partial, [finished_window(game_id, T0)])
    window_calls = {"n": 0}

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" in url:
            window_calls["n"] += 1
            raise AssertionError("terminal partial must not resume window HTTP")
        return json_response(url, window_body(game_id, [frame_at(T0, 500)]))

    def fail_bulk_read(*_args: object) -> object:
        raise AssertionError("terminal partial must not be loaded into a list")

    monkeypatch.setattr(module, "read_gzip_jsonl", fail_bulk_read)
    patch_http(monkeypatch, fake_get)
    links_path = tmp_path / "links.parquet"
    audit_path = tmp_path / "download_audit.parquet"
    write_links(links_path, [make_link(game_id, "e-terminal-partial", 1)])
    module.fetch_lolesports(links_path, windows_dir, tmp_path / "details", audit_path, TEST_WORKERS)
    row = audit_row(audit_path, game_id)
    assert window_calls["n"] == 0
    assert target.exists()
    assert not partial.exists()
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_DOWNLOAD_COMPLETE


def test_fetch_prints_plan_and_final_download_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fetch emits one plan line and a final progress line."""
    run_fetch(
        monkeypatch,
        tmp_path,
        [make_link("g-log", "e-log", 1)],
        gold_at_query,
        0,
        3600,
    )
    output = capsys.readouterr().out
    assert "lolesports_plan total=1 cached=0 queued=1 workers=1" in output
    assert "lolesports_progress done=1/1" in output


def test_empty_http_body_advances_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 200 empty body is not a schema error; only empties through wall is empty_body."""

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        return empty_response(url)

    with pytest.raises(typer.Exit) as exc:
        run_fetch(monkeypatch, tmp_path, [make_link("g-empty", "e-empty", 1)], fake_get, 9999, 20)
    assert exc.value.exit_code == 1
    row = audit_row(tmp_path / "download_audit.parquet", "g-empty")
    assert bool(row["complete"]) is False
    assert str(row["reason"]) == REASON_EMPTY_BODY
    assert int(row["window_response_count"]) == 0


def test_empty_frames_json_is_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 200 with frames=[] is a valid JSONL line and the loop continues."""
    payloads_by_start: dict[str, dict[str, object]] = {
        starting_time_for(T0): window_body("g-quiet", []),
        starting_time_for(T0 + 10): gold_window("g-quiet", T0 + 10),
        starting_time_for(T0 + 20): gold_window("g-quiet", T0 + 20),
    }

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        return json_response(url, payloads_by_start[params["startingTime"]])

    _, windows_dir, audit_path = run_fetch(
        monkeypatch, tmp_path, [make_link("g-quiet", "e-quiet", 1)], fake_get, 0, 3600
    )
    lines = read_jsonl(windows_dir / "g-quiet.jsonl.gz")
    assert isinstance(lines[0], dict)
    assert lines[0]["frames"] == []
    row = audit_row(audit_path, "g-quiet")
    assert str(row["reason"]) == REASON_DOWNLOAD_COMPLETE
    assert int(row["window_response_count"]) >= 2


def no_sleep(_seconds: float) -> None:
    """Skip tenacity backoff waits in retry tests."""
    return


def test_backoff_retries_then_exhausts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four 429 then 200 completes; five 429 on another map is retries_exhausted."""
    monkeypatch.setattr("time.sleep", no_sleep)
    hits: dict[str, int] = {"g-ok": 0, "g-fail": 0}

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        game_id = url.rsplit("/", 1)[-1]
        hits[game_id] = hits.get(game_id, 0) + 1
        if game_id == "g-fail":
            return status_response(url, 429)
        if hits[game_id] < 5:
            return status_response(url, 429)
        return gold_at_query(client, url, headers, params)

    with pytest.raises(typer.Exit) as exc:
        run_fetch(
            monkeypatch,
            tmp_path,
            [make_link("g-ok", "e-ok", 1), make_link("g-fail", "e-fail", 2)],
            fake_get,
            0,
            3600,
        )
    assert exc.value.exit_code == 1
    ok_row = audit_row(tmp_path / "download_audit.parquet", "g-ok")
    fail_row = audit_row(tmp_path / "download_audit.parquet", "g-fail")
    assert str(ok_row["reason"]) == REASON_DOWNLOAD_COMPLETE
    assert bool(ok_row["complete"]) is True
    assert str(fail_row["reason"]) == REASON_RETRIES_EXHAUSTED
    assert bool(fail_row["complete"]) is False
    assert hits["g-fail"] == 5


def test_http_403_aborts_without_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 403 aborts the run and does not write download_audit.parquet."""

    def fake_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        return status_response(url, 403)

    with pytest.raises(RuntimeError, match="HTTP 403"):
        run_fetch(monkeypatch, tmp_path, [make_link("g-403", "e-403", 1)], fake_get, 20, 3600)
    assert not (tmp_path / "download_audit.parquet").exists()


def test_http_404_audits_sibling_and_exits_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 map is audited; a sibling still downloads; incomplete runs exit 1."""

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        game_id = url.rsplit("/", 1)[-1]
        if game_id == "g-404":
            return status_response(url, 404)
        return gold_at_query(client, url, headers, params)

    with pytest.raises(typer.Exit) as exc:
        run_fetch(
            monkeypatch,
            tmp_path,
            [make_link("g-404", "e-404", 1), make_link("g-ok", "e-ok", 2)],
            fake_get,
            0,
            3600,
        )
    assert exc.value.exit_code == 1
    audit_path = tmp_path / "download_audit.parquet"
    missing = audit_row(audit_path, "g-404")
    ok_row = audit_row(audit_path, "g-ok")
    assert str(missing["reason"]) == REASON_HTTP_404
    assert bool(missing["complete"]) is False
    assert str(ok_row["reason"]) == REASON_DOWNLOAD_COMPLETE
    assert bool(ok_row["complete"]) is True
    assert (tmp_path / "windows" / "g-ok.jsonl.gz").exists()


def test_http_404_after_spawn_is_complete_and_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 after a gold frame is complete; a second run issues no HTTP."""
    hits = {"n": 0}

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        hits["n"] += 1
        if params["startingTime"] == T0_STARTING:
            return gold_at_query(client, url, headers, params)
        return status_response(url, 404)

    _, windows_dir, audit_path = run_fetch(
        monkeypatch, tmp_path, [make_link("g-404-end", "e-404-end", 1)], fake_get, 9999, 3600
    )
    row = audit_row(audit_path, "g-404-end")
    assert bool(row["complete"]) is True
    assert str(row["reason"]) == REASON_HTTP_404
    assert (windows_dir / "g-404-end.jsonl.gz").exists()
    first_hits = hits["n"]
    assert first_hits >= 1

    def fail_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        raise AssertionError(f"http_get should not be called: {url}")

    module = load_fetch()
    patch_http(monkeypatch, fail_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 9999)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    module.fetch_lolesports(
        tmp_path / "links.parquet", windows_dir, tmp_path / "details", audit_path, TEST_WORKERS
    )
    skipped = audit_row(audit_path, "g-404-end")
    assert str(skipped["reason"]) == REASON_HTTP_404
    assert bool(skipped["complete"]) is True


def test_complete_at_old_20_archive_resumes_not_from_t0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive that stopped at game second 20 resumes after the last stamp, not t0."""
    module = load_fetch()
    windows_dir = tmp_path / "windows"
    game_id = "g-old20"
    target = module.archive_path(windows_dir, game_id)
    module.write_gzip_jsonl(target, [hz_gold_window(game_id, T0, 21)])
    audit_path = tmp_path / "download_audit.parquet"
    pd.DataFrame(
        [
            {
                "esports_game_id": game_id,
                "event_id": "e-old20",
                "esports_match_id": "match-e-old20",
                "game_number": 1,
                "window_response_count": 1,
                "unique_frame_count": 21,
                "max_game_second": 20,
                "complete": True,
                "reason": REASON_DOWNLOAD_COMPLETE,
            }
        ]
    ).to_parquet(audit_path, index=False)
    starts: list[str] = []

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        if "/window/" in url:
            starts.append(params["startingTime"])
        return gold_at_query(client, url, headers, params)

    write_links(tmp_path / "links.parquet", [make_link(game_id, "e-old20", 1)])
    patch_http(monkeypatch, fake_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 40)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    module.fetch_lolesports(
        tmp_path / "links.parquet", windows_dir, tmp_path / "details", audit_path, TEST_WORKERS
    )
    assert starts[0] == starting_time_for(T0 + 30)
    assert T0_STARTING not in starts


def test_old_wall_limit_resumes_under_raised_wall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wall_time_limit archive still walks when the current wall constant is higher."""
    run_fetch(monkeypatch, tmp_path, [make_link("g-wall", "e-wall", 1)], gold_at_query, 9999, 20)
    row = audit_row(tmp_path / "download_audit.parquet", "g-wall")
    assert str(row["reason"]) == REASON_WALL_TIME_LIMIT
    starts: list[str] = []

    def fake_get(
        client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, str]
    ) -> httpx.Response:
        starts.append(params["startingTime"])
        return gold_at_query(client, url, headers, params)

    module = load_fetch()
    patch_http(monkeypatch, fake_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 9999)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 40)
    module.fetch_lolesports(
        tmp_path / "links.parquet",
        tmp_path / "windows",
        tmp_path / "details",
        tmp_path / "download_audit.parquet",
        TEST_WORKERS,
    )
    assert starts
    assert starts[0] != T0_STARTING
    resumed = audit_row(tmp_path / "download_audit.parquet", "g-wall")
    assert str(resumed["reason"]) == REASON_WALL_TIME_LIMIT
    assert bool(resumed["complete"]) is True


def test_wall_skip_only_when_current_wall_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip wall_time_limit HTTP only when next_starting_ts already exceeds the current wall."""
    run_fetch(monkeypatch, tmp_path, [make_link("g-wall", "e-wall", 1)], gold_at_query, 9999, 20)
    row = audit_row(tmp_path / "download_audit.parquet", "g-wall")
    assert str(row["reason"]) == REASON_WALL_TIME_LIMIT

    def fail_get(
        _client: httpx.Client, url: str, _headers: dict[str, str], _params: dict[str, str]
    ) -> httpx.Response:
        raise AssertionError(f"http_get should not be called: {url}")

    module = load_fetch()
    patch_http(monkeypatch, fail_get)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 9999)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 20)
    module.fetch_lolesports(
        tmp_path / "links.parquet",
        tmp_path / "windows",
        tmp_path / "details",
        tmp_path / "download_audit.parquet",
        TEST_WORKERS,
    )
    skipped = audit_row(tmp_path / "download_audit.parquet", "g-wall")
    assert str(skipped["reason"]) == REASON_WALL_TIME_LIMIT
    assert bool(skipped["complete"]) is True


def test_fetch_does_not_rewrite_full_partial_each_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-loop saves must not rewrite the whole .partial on every window."""
    module = load_fetch()
    rewrite_sizes: list[int] = []
    original = module.write_gzip_jsonl

    def counting(path: Path, payloads: Sequence[object]) -> None:
        rewrite_sizes.append(len(list(payloads)))
        original(path, payloads)

    monkeypatch.setattr(module, "write_gzip_jsonl", counting)
    patch_http(monkeypatch, gold_at_query)
    monkeypatch.setattr(module, "LOL_FETCH_END_SECOND", 30)
    monkeypatch.setattr(module, "LOL_FETCH_MAX_WALL_SECONDS", 3600)
    write_links(tmp_path / "links.parquet", [make_link("g-append", "e-append", 1)])
    module.fetch_lolesports(
        tmp_path / "links.parquet",
        tmp_path / "windows",
        tmp_path / "details",
        tmp_path / "download_audit.parquet",
        TEST_WORKERS,
    )
    assert rewrite_sizes
    assert len(rewrite_sizes) == 1


def test_append_gzip_jsonl_keeps_previous_bytes(tmp_path: Path) -> None:
    """Each window is one appended gzip member; earlier bytes are not rewritten."""
    module = load_fetch()
    path = tmp_path / "g-app.jsonl.gz.partial"
    module.append_gzip_jsonl(path, gold_window("g-app", T0))
    before = path.read_bytes()
    module.append_gzip_jsonl(path, gold_window("g-app", T0 + 10))
    after = path.read_bytes()
    assert after.startswith(before)
    assert len(after) > len(before)
    payloads = module.read_gzip_jsonl(path)
    assert len(payloads) == 2


def test_read_gzip_jsonl_keeps_prefix_when_last_member_truncated(tmp_path: Path) -> None:
    """A truncated trailing gzip member does not discard earlier windows."""
    module = load_fetch()
    path = tmp_path / "g-trunc.jsonl.gz.partial"
    module.append_gzip_jsonl(path, gold_window("g-trunc", T0))
    module.append_gzip_jsonl(path, gold_window("g-trunc", T0 + 10))
    path.write_bytes(path.read_bytes()[:-12])
    payloads = module.read_gzip_jsonl(path)
    assert len(payloads) == 1
    first = payloads[0]
    assert isinstance(first, dict)
    assert first["esportsGameId"] == "g-trunc"
