from typing import cast
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from collect.common.stratz_client import (
    STRATZ_USER_AGENT,
    StratzError,
    StratzForbiddenError,
    StratzRateLimitError,
    StratzServerError,
    stratz_graphql,
    stratz_headers,
)


def test_transport_error_is_not_wrapped_as_5xx() -> None:
    client = Mock()
    client.post.side_effect = httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        stratz_graphql(client, "query { __typename }", {})


def test_headers_match_the_stratz_cloudflare_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_env(name: str) -> str | None:
        return "tok" if name == "STRATZ_TOKEN" else None

    monkeypatch.setattr("collect.common.stratz_client.env_value", fake_env)
    headers = stratz_headers()

    assert headers == {
        "Authorization": "Bearer tok",
        "User-Agent": "STRATZ_API",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert headers["User-Agent"] == STRATZ_USER_AGENT
    assert "cookie" not in {key.lower() for key in headers}


def test_graphql_posts_json_body_without_a_query_key() -> None:
    client = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {"data": {}}
    client.post.return_value = response

    stratz_graphql(client, "query { x }", {"id": 1})

    url = cast(str, client.post.call_args.args[0])
    assert parse_qs(urlparse(url).query) == {}
    assert client.post.call_args.kwargs["json"] == {
        "query": "query { x }",
        "variables": {"id": 1},
    }


def graphql_with_status(status: int, headers: dict[str, str]) -> None:
    client = Mock()
    response = Mock(status_code=status, headers=headers)
    response.json.return_value = {"data": {}}
    client.post.return_value = response
    stratz_graphql(client, "query { x }", {})


def test_transport_statuses_map_to_their_error_classes() -> None:
    with pytest.raises(StratzForbiddenError):
        graphql_with_status(403, {})
    with pytest.raises(StratzServerError, match="503"):
        graphql_with_status(503, {})
    with pytest.raises(StratzError, match="418"):
        graphql_with_status(418, {})


def test_retry_after_is_parsed_and_tolerates_garbage() -> None:
    with pytest.raises(StratzRateLimitError) as numeric:
        graphql_with_status(429, {"retry-after": "12"})
    assert numeric.value.retry_after == 12.0

    with pytest.raises(StratzRateLimitError) as garbage:
        graphql_with_status(429, {"retry-after": "soon"})
    assert garbage.value.retry_after is None

    with pytest.raises(StratzRateLimitError) as missing:
        graphql_with_status(429, {})
    assert missing.value.retry_after is None
