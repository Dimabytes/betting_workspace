import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from shared.constants.api import OPENDOTA_API
from shared.utils.environment import env_value
from shared.utils.filesystem import staging_directory
from shared.utils.http import get_json, http_client
from shared.utils.json_io import read_json, write_json


class OpenDotaCandidateRow(TypedDict):
    match_id: int
    start_time: int
    duration: int
    leagueid: int
    radiant_team_id: int
    dire_team_id: int
    radiant_name: str | None
    dire_name: str | None
    series_id: int | None
    series_type: int | None
    radiant_win: bool


class OpenDotaCandidatePage(TypedDict):
    schema: str
    start_ts: int
    end_ts: int
    after: int
    rows: list[OpenDotaCandidateRow]


def opendota_params(params: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(params or {})
    key = env_value("OPENDOTA_API_KEY")
    if key:
        out["api_key"] = key
    return out


OPENDOTA_CANDIDATE_PAGE_SIZE = 200
OPENDOTA_CANDIDATE_PAGE_SCHEMA = "opendota_candidates_match_id_cursor_v3"
OPENDOTA_CANDIDATE_REQUEST_SLEEP_SECONDS = 1.2
OPENDOTA_CANDIDATE_BOUNDARY_PAD = 2_000_000
OPENDOTA_CANDIDATE_COLUMNS = set(OpenDotaCandidateRow.__annotations__)


def explorer_sql(sql: str) -> list[dict[str, Any]]:
    with http_client() as client:
        raw = get_json(client, f"{OPENDOTA_API}/explorer", opendota_params({"sql": sql}))
    if not isinstance(raw, dict):
        raise RuntimeError("OpenDota explorer response is not an object")
    rows = cast(dict[str, Any], raw).get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("OpenDota explorer response has no rows list")
    return cast(list[dict[str, Any]], rows)


def require_positive_candidate_ints(values: dict[str, object], path: Path) -> None:
    for field in (
        "match_id",
        "start_time",
        "leagueid",
        "radiant_team_id",
        "dire_team_id",
    ):
        value = values[field]
        if type(value) is not int or value <= 0:
            raise RuntimeError(f"candidate {field} is not a positive integer at {path}")


def validate_opendota_candidate_row(row: object, path: Path) -> OpenDotaCandidateRow:
    if not isinstance(row, dict):
        raise RuntimeError(f"candidate page row is not an object: {path}")
    values = cast(dict[str, object], row)
    columns = set(values)
    if columns != OPENDOTA_CANDIDATE_COLUMNS:
        missing = sorted(OPENDOTA_CANDIDATE_COLUMNS - columns)
        extra = sorted(columns - OPENDOTA_CANDIDATE_COLUMNS)
        raise RuntimeError(f"candidate schema mismatch at {path}: missing={missing} extra={extra}")
    require_positive_candidate_ints(values, path)
    duration = values["duration"]
    if type(duration) is not int or duration < 0:
        raise RuntimeError(f"candidate duration is not a non-negative integer at {path}")
    for field in ("series_id", "series_type"):
        value = values[field]
        if value is not None and type(value) is not int:
            raise RuntimeError(f"candidate {field} is not an optional integer at {path}")
    for field in ("radiant_name", "dire_name"):
        value = values[field]
        if value is not None and not isinstance(value, str):
            raise RuntimeError(f"candidate {field} is not an optional string at {path}")
    if not isinstance(values["radiant_win"], bool):
        raise RuntimeError(f"candidate radiant_win is not boolean at {path}")
    return cast(OpenDotaCandidateRow, values)


def build_opendota_candidate_query(after_match_id: int, end_ts: int) -> str:
    return (
        "SELECT m.match_id, m.start_time, m.duration, m.leagueid, "
        "m.radiant_team_id, m.dire_team_id, m.series_id, m.series_type, m.radiant_win, "
        "rt.name AS radiant_name, dt.name AS dire_name FROM matches m "
        "LEFT JOIN teams rt ON rt.team_id = m.radiant_team_id "
        "LEFT JOIN teams dt ON dt.team_id = m.dire_team_id "
        f"WHERE m.match_id > {after_match_id} AND m.start_time < {end_ts} "
        "AND m.leagueid IS NOT NULL AND m.leagueid != 0 "
        "AND m.radiant_team_id IS NOT NULL AND m.dire_team_id IS NOT NULL "
        f"ORDER BY m.match_id ASC LIMIT {OPENDOTA_CANDIDATE_PAGE_SIZE}"
    )


def build_opendota_candidate_page_path(raw_dir: Path, after_match_id: int) -> Path:
    return raw_dir / f"page_after_{after_match_id:010d}.json"


def find_opendota_candidate_boundary(start_ts: int) -> int:
    rows = explorer_sql(
        "SELECT match_id FROM matches "
        f"WHERE start_time >= {start_ts} ORDER BY start_time ASC, match_id ASC LIMIT 1"
    )
    if not rows:
        return 0
    match_id = rows[0].get("match_id")
    if not isinstance(match_id, int):
        raise RuntimeError("OpenDota start boundary returned an invalid match id")
    return max(0, match_id - OPENDOTA_CANDIDATE_BOUNDARY_PAD)


def fetch_opendota_candidate_pages(raw_dir: Path, start_ts: int, end_ts: int) -> None:
    after = find_opendota_candidate_boundary(start_ts)
    while True:
        path = build_opendota_candidate_page_path(raw_dir, after)
        query = build_opendota_candidate_query(after, end_ts)
        rows = [validate_opendota_candidate_row(row, path) for row in explorer_sql(query)]
        page: OpenDotaCandidatePage = {
            "schema": OPENDOTA_CANDIDATE_PAGE_SCHEMA,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "after": after,
            "rows": rows,
        }
        write_json(path, page)
        newest = max((row["start_time"] for row in rows), default=None)
        newest_iso = (
            datetime.fromtimestamp(newest, tz=UTC).isoformat().replace("+00:00", "Z")
            if newest is not None
            else None
        )
        print(f"candidate page after={after}: {len(rows)} rows newest={newest_iso}", flush=True)
        if len(rows) < OPENDOTA_CANDIDATE_PAGE_SIZE:
            return
        next_after = max(row["match_id"] for row in rows)
        if next_after <= after:
            raise RuntimeError(f"non-advancing OpenDota candidate cursor at {path}")
        after = next_after
        time.sleep(OPENDOTA_CANDIDATE_REQUEST_SLEEP_SECONDS)


def load_opendota_candidate_rows(
    raw_dir: Path, start_ts: int, end_ts: int
) -> list[OpenDotaCandidateRow]:
    paths = sorted(raw_dir.glob("page_after_*.json"))
    if not paths:
        raise FileNotFoundError(raw_dir)
    rows_by_id: dict[int, OpenDotaCandidateRow] = {}
    for path in paths:
        page = cast(OpenDotaCandidatePage, read_json(path))
        for row in page["rows"]:
            if start_ts <= row["start_time"] < end_ts:
                rows_by_id[row["match_id"]] = row
    return sorted(rows_by_id.values(), key=lambda row: (row["start_time"], row["match_id"]))


def refresh_opendota_candidate_pages(raw_dir: Path, start_ts: int, end_ts: int) -> None:
    with staging_directory(raw_dir.parent, f".{raw_dir.name}.tmp-") as temp:
        fetch_opendota_candidate_pages(temp, start_ts, end_ts)
        shutil.rmtree(raw_dir, ignore_errors=True)
        temp.replace(raw_dir)
