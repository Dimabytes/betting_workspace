"""Rewrite tournament details from one Dota listing and parallel detail calls.

Raw detail replies land verbatim so tests replay real GraphQL shapes. The
brand token is only a request header and is never written. To list the open
matches without changing fixtures, run watch_oddin_live.py.

  PYTHONPATH=src uv run python scripts/probe_disir_catalog.py
"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from shared.constants.paths import BASE_DIR
from trader.oddin_catalog import (
    TOURNAMENT_QUERY,
    ReplyKind,
    fetch_active_dota_tournament_ids,
    read_reply,
    tournament_graphql_id,
)
from trader.oddin_client import (
    ODDIN_QUERY,
    REQUEST_TIMEOUT_S,
    disir_client,
    graphql_headers,
    require_brand_token,
)
from trader.oddin_types import OddinFeedError

FIXTURE_PATH = BASE_DIR / "tests" / "fixtures" / "disir_tournament_info.json"


@dataclass(frozen=True)
class Row:
    """One listed id: the reply kind and raw body or failure reason."""

    numeric_id: int
    kind: ReplyKind
    body: str


async def fetch_row(client: httpx.AsyncClient, token: str, numeric_id: int) -> Row:
    """Fetch one detail without retry; transport and HTTP failures are `error`."""
    try:
        response = await client.post(
            ODDIN_QUERY,
            headers=graphql_headers(token),
            json={
                "operationName": "Dota2TournamentInfo",
                "query": TOURNAMENT_QUERY,
                "variables": {"tournamentId": tournament_graphql_id(numeric_id)},
            },
        )
    except (httpx.HTTPError, OSError) as exc:
        return Row(numeric_id, "error", type(exc).__name__)
    if response.status_code != 200:
        return Row(numeric_id, "error", f"http_{response.status_code}")
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        return Row(numeric_id, "error", "not_json")
    return Row(numeric_id, read_reply(numeric_id, payload).kind, response.text)


def fixture_text(rows: Sequence[Row], fetched_at: str) -> str:
    """Serialize a successful listing and its tournament detail replies."""
    document: dict[str, object] = {
        "fetched_at_utc": fetched_at,
        "tournament_ids": [row.numeric_id for row in rows],
        "responses": [
            {"numeric_id": row.numeric_id, "kind": row.kind, "body": json.loads(row.body)}
            for row in rows
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


async def amain(token: str) -> int:
    now = datetime.now(UTC)
    try:
        async with disir_client(REQUEST_TIMEOUT_S) as client:
            numeric_ids = await fetch_active_dota_tournament_ids(client, token, now)
            print(f"listed {len(numeric_ids)} active tournaments", flush=True)
            rows = await asyncio.gather(*(fetch_row(client, token, item) for item in numeric_ids))
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"listing failed: {type(exc).__name__}; fixture not written")
        return 1
    for row in rows:
        print(f"{row.kind} {row.numeric_id}", flush=True)
    if not rows or any(row.kind != "tournament" for row in rows):
        print("empty listing or failed detail; fixture not written")
        return 1
    text = fixture_text(rows, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if token in text:
        raise OddinFeedError("fixture still contains the brand token")
    FIXTURE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {FIXTURE_PATH.relative_to(BASE_DIR)} ({len(rows)} tournaments)", flush=True)
    return 0


def main() -> int:
    try:
        token = require_brand_token()
    except OddinFeedError:
        print("missing ODDIN_BRAND_TOKEN")
        return 1
    return asyncio.run(amain(token))


if __name__ == "__main__":
    raise SystemExit(main())
