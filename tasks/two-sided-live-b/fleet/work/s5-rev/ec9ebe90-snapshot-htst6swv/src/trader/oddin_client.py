"""Oddin widget config, GraphQL HTTP snapshot, and WS protocol."""

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator
from typing import cast
from urllib.parse import parse_qs, urlparse

import httpx
import websockets

from shared.utils.environment import env_value
from shared.utils.json_read import as_map
from trader.oddin_crypto import open_envelope
from trader.oddin_types import (
    OddinFeedError,
    ScoreboardSilent,
    SocketAction,
    SocketFrame,
    WidgetConfig,
)

ODDIN_ORIGIN = "https://disir.oddin.gg"
ODDIN_QUERY = "https://api-disir.oddin.gg/main/disir/query"
ODDIN_WS = "wss://api-disir.oddin.gg/main/disir/query"
USER_AGENT = "dota-2-model/0.1 research pipeline"
BRAND_TOKEN_ENV = "ODDIN_BRAND_TOKEN"
RECONNECT_SECONDS = 3.0
MAX_CONSECUTIVE_FAILURES = 5
REQUEST_TIMEOUT_S = 5.0
MATCH_ID_RE = re.compile(r"od:match:(\d+)")
SNAPSHOT_QUERY = (
    "query Dota2ScoreboardEncrypted($matchId: ID!) { "
    "dota2ScoreboardData(matchId: $matchId) { id data } }"
)
SUBSCRIBE_QUERY = (
    "subscription OnDota2ScoreboardFeedEncrypted($matchId: ID!) { "
    "onDota2ScoreboardFeedData(matchId: $matchId) { id data } }"
)


def disir_client(timeout: float) -> httpx.AsyncClient:
    """Async HTTP client for Disir GraphQL."""
    return httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def require_brand_token() -> str:
    """Stake operator token. The trader has no fallback."""
    token = env_value(BRAND_TOKEN_ENV)
    if not token:
        raise OddinFeedError("ODDIN_BRAND_TOKEN is not set")
    return token


def direct_match_id(selector: str) -> str | None:
    """`od:match:<id>` from a numeric id, prefixed id, or match URL. Else None."""
    stripped = selector.strip()
    if stripped.startswith("http://") or stripped.startswith("https://"):
        parsed = urlparse(stripped)
        found = MATCH_ID_RE.search(stripped)
        if found is not None:
            return f"od:match:{found.group(1)}"
        query_id = parse_qs(parsed.query).get("id", [""])[0]
        query_match = MATCH_ID_RE.search(query_id)
        if query_match is not None:
            return f"od:match:{query_match.group(1)}"
        tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            return f"od:match:{tail}"
        raise OddinFeedError(f"cannot parse match id from URL {selector!r}")
    if stripped.startswith("od:match:"):
        return stripped
    if stripped.isdigit():
        return f"od:match:{stripped}"
    return None


def widget_config(match_id: str) -> WidgetConfig:
    """GraphQL id is base64 of `match/od:match:<N>`, with `ODDIN_BRAND_TOKEN`."""
    if MATCH_ID_RE.fullmatch(match_id) is None:
        raise OddinFeedError(f"not an oddin match id: {match_id}")
    encoded = base64.b64encode(f"match/{match_id}".encode()).decode("ascii")
    return WidgetConfig(brand_token=require_brand_token(), match_id=encoded)


def graphql_headers(brand_token: str) -> dict[str, str]:
    """Headers Disir expects on every HTTP query."""
    return {
        "X-Api-Key": brand_token,
        "X-Locale": "en",
        "Origin": ODDIN_ORIGIN,
        "Content-Type": "application/json",
    }


def _graphql_envelope(body: object, field: str) -> str:
    mapped = as_map(body)
    if mapped is None:
        raise OddinFeedError("GraphQL response is not an object")
    errors = mapped.get("errors")
    if errors:
        raise OddinFeedError(f"GraphQL errors: {errors}")
    data = as_map(mapped.get("data"))
    if data is None:
        raise OddinFeedError("GraphQL response has no data")
    node = as_map(data.get(field))
    if node is None:
        raise OddinFeedError(f"GraphQL data.{field} is missing")
    envelope = node.get("data")
    if not isinstance(envelope, str) or not envelope:
        raise OddinFeedError(f"GraphQL data.{field}.data is missing")
    return envelope


async def fetch_snapshot_envelope(client: httpx.AsyncClient, config: WidgetConfig) -> str:
    """HTTP GraphQL encrypted snapshot."""
    response = await client.post(
        ODDIN_QUERY,
        headers=graphql_headers(config.brand_token),
        json={
            "operationName": "Dota2ScoreboardEncrypted",
            "query": SNAPSHOT_QUERY,
            "variables": {"matchId": config.match_id},
        },
    )
    response.raise_for_status()
    return _graphql_envelope(response.json(), "dota2ScoreboardData")


def websocket_url(brand_token: str) -> str:
    """graphql-transport-ws URL for the encrypted scoreboard feed."""
    return f"{ODDIN_WS}?apiKey={brand_token}"


def connection_init_message(brand_token: str) -> str:
    """`connection_init` with the widget API key."""
    return json.dumps(
        {"type": "connection_init", "payload": {"X-Api-Key": brand_token, "X-Locale": "en"}}
    )


def subscribe_message(match_id: str) -> str:
    """Encrypted scoreboard subscription after `connection_ack`."""
    return json.dumps(
        {
            "id": "scoreboard",
            "type": "subscribe",
            "payload": {
                "operationName": "OnDota2ScoreboardFeedEncrypted",
                "variables": {"matchId": match_id},
                "query": SUBSCRIBE_QUERY,
            },
        }
    )


def pong_message(payload: object) -> str:
    """Reply to a graphql-transport-ws ping with the same payload."""
    message: dict[str, object] = {"type": "pong"}
    if payload is not None:
        message["payload"] = payload
    return json.dumps(message)


def parse_socket_frame(raw: str) -> SocketFrame | None:
    """Parse one websocket text frame, or None when it is not JSON."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    mapped = as_map(parsed)
    if mapped is None:
        return None
    frame_type = mapped.get("type")
    if not isinstance(frame_type, str) or not frame_type:
        return None
    frame_id = mapped.get("id")
    return SocketFrame(
        type=frame_type,
        id=frame_id if isinstance(frame_id, str) else None,
        payload=mapped.get("payload"),
    )


def next_envelope(payload: object) -> str:
    """Encrypted blob from a `next` payload."""
    mapped = as_map(payload)
    if mapped is None:
        raise OddinFeedError("next payload is not an object")
    return _graphql_envelope({"data": mapped.get("data")}, "onDota2ScoreboardFeedData")


def apply_socket_frame(frame: SocketFrame | None) -> SocketAction:
    """Translate one protocol frame into watcher actions."""
    if frame is None:
        return SocketAction(False, False, None, None, True, False)
    if frame.type == "connection_ack":
        return SocketAction(True, False, None, None, False, False)
    if frame.type == "ping":
        return SocketAction(False, True, frame.payload, None, False, False)
    if frame.type == "next":
        return SocketAction(False, False, None, next_envelope(frame.payload), False, False)
    if frame.type == "error":
        return SocketAction(False, False, None, None, True, False)
    if frame.type == "complete":
        return SocketAction(False, False, None, None, False, True)
    return SocketAction(False, False, None, None, False, False)


async def iter_scoreboard_payloads(
    config: WidgetConfig, key: bytes, stale_seconds: float
) -> AsyncIterator[dict[str, object]]:
    """Connect, init, subscribe, answer pings, yield each decrypted `next` payload.

    Returns on `complete`. Raises OddinFeedError on an error frame.
    TimeoutError when no frame arrives for stale_seconds. ScoreboardSilent when
    frames keep arriving (pings) but no scoreboard payload does: pings do not
    refresh the scoreboard clock.
    """
    async with websockets.connect(
        websocket_url(config.brand_token),
        origin=cast(websockets.Origin, ODDIN_ORIGIN),
        subprotocols=[cast(websockets.Subprotocol, "graphql-transport-ws")],
        max_size=None,
        ping_interval=10,
        ping_timeout=10,
    ) as socket:
        await socket.send(connection_init_message(config.brand_token))
        loop = asyncio.get_running_loop()
        last_scoreboard = loop.time()
        while True:
            remaining = stale_seconds - (loop.time() - last_scoreboard)
            if remaining <= 0:
                raise ScoreboardSilent()
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
            action = apply_socket_frame(parse_socket_frame(str(raw)))
            if action.send_subscribe:
                await socket.send(subscribe_message(config.match_id))
                continue
            if action.send_pong:
                await socket.send(pong_message(action.pong_payload))
                continue
            if action.envelope is not None:
                last_scoreboard = loop.time()
                yield open_envelope(action.envelope, key)
                continue
            if action.stream_complete:
                return
            if action.reconnect:
                raise OddinFeedError("oddin socket error frame")
