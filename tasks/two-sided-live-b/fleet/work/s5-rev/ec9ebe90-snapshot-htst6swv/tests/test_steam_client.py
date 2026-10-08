"""Tests for the Steam client key rotation."""

import logging
import threading
import time

import httpx
import pytest

from trader import notify, steam_client

URL = "https://api.steampowered.com/IDOTA2MatchStats_570/GetRealtimeStats/v1/"


class AlertRecorder:
    """Thread-safe stand-in for send_telegram_message with a completion signal."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._condition = threading.Condition()

    def __call__(self, message: str) -> None:
        with self._condition:
            self.messages.append(message)
            self._condition.notify_all()

    def wait_for(self, count: int, timeout: float = 5.0) -> list[str]:
        """Wait until at least `count` alerts arrived and return a snapshot."""
        with self._condition:
            self._condition.wait_for(lambda: len(self.messages) >= count, timeout=timeout)
            return list(self.messages)


def patch_env_values(monkeypatch: pytest.MonkeyPatch, values: dict[str, str | None]) -> None:
    """Point the steam_client module's env lookup at fixed values."""

    def env_lookup(name: str) -> str | None:
        return values[name]

    monkeypatch.setattr(steam_client, "env_value", env_lookup)


def ignore_alert(message: str) -> None:
    """Stand-in for send_telegram_message in tests that only check requests."""


def test_load_steam_keys_trims_and_prefers_steam_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STEAM_KEYS wins over STEAM_KEY; blank entries and whitespace are dropped."""
    patch_env_values(monkeypatch, {"STEAM_KEYS": "  k1 , k2 ,, k3 ", "STEAM_KEY": "legacy"})

    assert steam_client.load_steam_keys() == ("k1", "k2", "k3")


def test_load_steam_keys_falls_back_to_legacy_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty STEAM_KEYS leaves the legacy single STEAM_KEY in charge."""
    patch_env_values(monkeypatch, {"STEAM_KEYS": "", "STEAM_KEY": "  legacy-key  "})

    assert steam_client.load_steam_keys() == ("legacy-key",)


def test_load_steam_keys_raises_when_neither_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing or blank values on both variables raise a clear RuntimeError."""
    patch_env_values(monkeypatch, {"STEAM_KEYS": None, "STEAM_KEY": None})

    with pytest.raises(RuntimeError, match="neither STEAM_KEYS nor STEAM_KEY"):
        steam_client.load_steam_keys()


def test_get_injects_current_key_and_overwrites_caller_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every request carries the current key; a caller-supplied key is replaced."""
    monkeypatch.setattr(notify, "send_telegram_message", ignore_alert)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b"))
        first = steam.get(URL, {"server_steam_id": "42", "key": "caller-key"})
        second = steam.get(URL, {"server_steam_id": "42"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert requests_seen == [
        {"server_steam_id": "42", "key": "key-a"},
        {"server_steam_id": "42", "key": "key-a"},
    ]


def test_get_rotates_after_429_and_notifies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first 429 switches the next request to key 2 and sends one alert."""
    recorder = AlertRecorder()
    monkeypatch.setattr(notify, "send_telegram_message", recorder)
    statuses = iter([429, 200])
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(next(statuses), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b"))
        limited = steam.get(URL, {})
        recovered = steam.get(URL, {})

    assert limited.status_code == 429
    assert recovered.status_code == 200
    assert requests_seen == [{"key": "key-a"}, {"key": "key-b"}]
    alerts = recorder.wait_for(1)
    assert alerts == ["steam 429 on key 1: switched to key 2"]
    assert "key-a" not in alerts[0]


def test_get_rotates_after_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 rotates keys exactly like a 429."""
    recorder = AlertRecorder()
    monkeypatch.setattr(notify, "send_telegram_message", recorder)
    statuses = iter([403, 200])
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(next(statuses), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b"))
        steam.get(URL, {})
        steam.get(URL, {})

    assert requests_seen == [{"key": "key-a"}, {"key": "key-b"}]
    assert recorder.wait_for(1) == ["steam 403 on key 1: switched to key 2"]


def test_last_key_repeats_with_one_alert_per_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the last key is reached, each 429 alerts once and the key stays."""
    recorder = AlertRecorder()
    monkeypatch.setattr(notify, "send_telegram_message", recorder)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(429, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("k1", "k2"))
        first = steam.get(URL, {})
        second = steam.get(URL, {})
        third = steam.get(URL, {})

    assert [response.status_code for response in (first, second, third)] == [429, 429, 429]
    assert requests_seen == [{"key": "k1"}, {"key": "k2"}, {"key": "k2"}]
    assert recorder.wait_for(3) == [
        "steam 429 on key 1: switched to key 2",
        "steam 429 on last key 2: no more keys",
        "steam 429 on last key 2: no more keys",
    ]


def test_ok_responses_keep_the_key_and_send_no_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal 200 response neither rotates nor notifies."""
    recorder = AlertRecorder()
    monkeypatch.setattr(notify, "send_telegram_message", recorder)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b"))
        steam.get(URL, {})
        steam.get(URL, {})

    assert requests_seen == [{"key": "key-a"}, {"key": "key-a"}]
    assert recorder.messages == []


def test_alert_delivery_does_not_delay_the_steam_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow Telegram delivery runs off the request path; get() returns promptly."""
    alert_started = threading.Event()
    release = threading.Event()

    def slow_alert(message: str) -> None:
        alert_started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(notify, "send_telegram_message", slow_alert)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(429, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("k1", "k2"))
        started = time.monotonic()
        response = steam.get(URL, {})
        elapsed = time.monotonic() - started

    assert alert_started.wait(timeout=5)
    assert response.status_code == 429
    assert requests_seen == [{"key": "k1"}]
    assert elapsed < 1.0
    release.set()


def test_httpx_info_logging_cannot_capture_the_steam_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the httpx logger at INFO, no log record carries the injected key."""
    monkeypatch.setattr(notify, "send_telegram_message", ignore_alert)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("super-secret-key", "backup-key"))
        with caplog.at_level(logging.INFO, logger="httpx"):
            response = steam.get(URL, {})

    assert response.status_code == 200
    assert requests_seen == [{"key": "super-secret-key"}]
    assert all("super-secret-key" not in record.getMessage() for record in caplog.records)


def test_from_environment_builds_client_from_env_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """from_environment reads STEAM_KEYS and starts requests on the first key."""
    patch_env_values(monkeypatch, {"STEAM_KEYS": "k1,k2", "STEAM_KEY": "legacy"})
    monkeypatch.setattr(notify, "send_telegram_message", ignore_alert)
    requests_seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(dict(request.url.params))
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient.from_environment(client)
        response = steam.get(URL, {})

    assert response.status_code == 200
    assert requests_seen == [{"key": "k1"}]


def test_get_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 is returned as-is; the key never follows Location onto another host."""
    monkeypatch.setattr(notify, "send_telegram_message", ignore_alert)
    hosts_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts_seen.append(request.url.host)
        if request.url.host == "api.steampowered.com":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/steal"},
                request=request,
            )
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        steam = steam_client.SteamClient(client, ("secret-key",))
        response = steam.get(URL, {"server_steam_id": "42"})

    assert response.status_code == 302
    assert hosts_seen == ["api.steampowered.com"]


def test_two_gets_overlap_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP runs outside the key lock, so two match feeds can be in flight together."""
    monkeypatch.setattr(notify, "send_telegram_message", ignore_alert)
    entered = threading.Barrier(2, timeout=5)
    count_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    errors: list[BaseException] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        with count_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        entered.wait()
        with count_lock:
            in_flight -= 1
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b"))

        def worker() -> None:
            try:
                steam.get(URL, {})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

    assert errors == []
    assert max_in_flight == 2


def test_concurrent_429_rotates_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two overlapping 429s on the same key advance the index once, not twice."""
    recorder = AlertRecorder()
    monkeypatch.setattr(notify, "send_telegram_message", recorder)
    entered = threading.Barrier(2, timeout=5)
    requests_seen: list[dict[str, str]] = []
    errors: list[BaseException] = []
    seen_lock = threading.Lock()
    racing = True

    def handler(request: httpx.Request) -> httpx.Response:
        with seen_lock:
            requests_seen.append(dict(request.url.params))
            still_racing = racing
        if still_racing:
            entered.wait()
            return httpx.Response(429, request=request)
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        steam = steam_client.SteamClient(client, ("key-a", "key-b", "key-c"))

        def worker() -> None:
            try:
                steam.get(URL, {})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        racing = False
        recovered = steam.get(URL, {})

    assert errors == []
    assert recovered.status_code == 200
    assert [row["key"] for row in requests_seen[:2]] == ["key-a", "key-a"]
    assert requests_seen[-1] == {"key": "key-b"}
    alerts = recorder.wait_for(1)
    assert alerts == ["steam 429 on key 1: switched to key 2"]
