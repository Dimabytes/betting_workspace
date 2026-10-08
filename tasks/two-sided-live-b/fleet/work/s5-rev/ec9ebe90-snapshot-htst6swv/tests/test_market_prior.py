from typing import cast

import httpx
import pytest

from shared.constants.api import POLYMARKET_CLOB_API
from trader.market_prior import PRIOR_GET_TIMEOUT_SECONDS, fetch_market_prior


class _FakeResponse:
    """One prices-history body plus the status the live client would see."""

    def __init__(self, body: object, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise like httpx on a non-2xx status."""
        if self.status_code < 400:
            return
        request = httpx.Request("GET", f"{POLYMARKET_CLOB_API}/prices-history")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("prior fetch failed", request=request, response=response)

    def json(self) -> object:
        """Return the scripted body."""
        return self._body


class _RecordingClient:
    """Stand-in for http_client() that records timeout and returns scripted bodies."""

    def __init__(self, bodies: list[_FakeResponse]) -> None:
        self.bodies = bodies
        self.calls: list[dict[str, object]] = []

    def __enter__(self) -> "_RecordingClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(
        self, url: str, params: dict[str, object] | None = None, timeout: object = None
    ) -> _FakeResponse:
        """Record one GET and pop the next scripted body."""
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.bodies.pop(0)


def test_fetch_market_prior_uses_collect_client_timeout_and_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each GET uses the collect client timeout."""
    recorder = _RecordingClient(
        [
            _FakeResponse({"history": [{"t": 100, "p": 0.52}]}),
            _FakeResponse({"history": [{"t": 100, "p": 0.48}]}),
        ]
    )
    monkeypatch.setattr("trader.market_prior.http_client", lambda: recorder)

    prior = fetch_market_prior("yes-token", "no-token", 150, yes_is_radiant=True)

    assert prior == pytest.approx(0.52)
    assert len(recorder.calls) == 2
    for call in recorder.calls:
        assert call["timeout"] == PRIOR_GET_TIMEOUT_SECONDS
        assert call["url"] == f"{POLYMARKET_CLOB_API}/prices-history"
        params = cast(dict[str, object], call["params"])
        assert params["fidelity"] == 1
        assert params["endTs"] == 150


def test_fetch_market_prior_returns_none_on_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-2xx GET raises so the worker can clear the task and retry."""
    recorder = _RecordingClient([_FakeResponse({}, status_code=500)])
    monkeypatch.setattr("trader.market_prior.http_client", lambda: recorder)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_market_prior("yes-token", "no-token", 150, yes_is_radiant=True)
