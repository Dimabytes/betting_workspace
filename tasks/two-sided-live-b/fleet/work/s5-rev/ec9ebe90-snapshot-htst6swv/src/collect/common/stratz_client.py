from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx

from shared.utils.environment import env_value

STRATZ_GRAPHQL_API = "https://api.stratz.com/graphql"


STRATZ_USER_AGENT = "STRATZ_API"


class StratzError(RuntimeError):
    pass


class StratzAuthError(StratzError):
    pass


class StratzForbiddenError(StratzError):
    pass


class StratzRateLimitError(StratzError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class StratzServerError(StratzError):
    pass


@dataclass(frozen=True)
class StratzResponse:
    data: dict[str, Any] | None
    errors: list[Any]


def stratz_token() -> str:
    token = env_value("STRATZ_TOKEN")
    if not token:
        raise StratzAuthError("STRATZ_TOKEN not set (env or .env)")
    return token


def stratz_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {stratz_token()}",
        "User-Agent": STRATZ_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def stratz_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers=stratz_headers(),
        follow_redirects=False,
    )


def _raise_for_transport(response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 403:
        raise StratzForbiddenError("STRATZ HTTP 403 (Cloudflare/auth block)")
    if status == 429:
        retry_after_raw = response.headers.get("retry-after")
        retry_after: float | None = None
        if retry_after_raw:
            try:
                retry_after = float(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = None
        raise StratzRateLimitError("STRATZ HTTP 429 (rate limited)", retry_after=retry_after)
    if 500 <= status < 600:
        raise StratzServerError(f"STRATZ HTTP {status}")
    raise StratzError(f"STRATZ HTTP {status}")


def stratz_graphql(
    client: httpx.Client,
    query: str,
    variables: Mapping[str, Any],
) -> StratzResponse:
    response = client.post(
        STRATZ_GRAPHQL_API,
        json={"query": query, "variables": variables},
    )
    _raise_for_transport(response)
    body: Any = response.json()
    payload = cast(dict[str, Any], body) if isinstance(body, dict) else {}
    data: Any = payload.get("data")
    errors: Any = payload.get("errors")
    return StratzResponse(
        data=cast(dict[str, Any], data) if isinstance(data, dict) else None,
        errors=cast(list[Any], errors) if isinstance(errors, list) else [],
    )
