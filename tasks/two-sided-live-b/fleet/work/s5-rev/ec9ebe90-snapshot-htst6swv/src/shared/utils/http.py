"""HTTP client defaults and retried JSON requests."""

import time
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.constants import api


def http_client() -> httpx.Client:
    headers = {"User-Agent": "dota-2-model/0.1 research pipeline"}
    return httpx.Client(
        timeout=api.HTTP_TIMEOUT_SECONDS,
        headers=headers,
        follow_redirects=True,
    )


@retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(5))
def get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(url, params={k: v for k, v in (params or {}).items() if v is not None})
    if response.status_code == 429:
        retry_after = int(response.headers.get("retry-after", "5"))
        time.sleep(retry_after)
    response.raise_for_status()
    return response.json()
