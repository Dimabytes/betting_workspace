"""Tests for Telegram notification delivery."""

import logging
import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest

from shared.constants.api import HTTP_TIMEOUT_SECONDS
from trader import notify


def patch_credentials(
    monkeypatch: pytest.MonkeyPatch, token: str | None, chat_id: str | None
) -> None:
    """Point the notify module's env lookup at fixed test credentials."""
    values = {"TG_BOT_API_TOKEN": token, "TG_CHAT_ID": chat_id}

    def env_lookup(name: str) -> str | None:
        return values[name]

    monkeypatch.setattr(notify, "env_value", env_lookup)


def build_response(status_code: int) -> httpx.Response:
    """Build a response object as the Telegram API would send it."""
    request = httpx.Request("POST", "https://api.telegram.org/bottok-123/sendMessage")
    return httpx.Response(status_code, request=request)


def test_send_telegram_message_posts_payload_with_shared_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured bot posts {chat_id, text} with the shared HTTP timeout."""
    patch_credentials(monkeypatch, "tok-123", "chat-42")
    post_calls: list[tuple[str, dict[str, str]]] = []

    def fake_post(url: str, json: dict[str, str]) -> httpx.Response:
        post_calls.append((url, json))
        return build_response(200)

    client = MagicMock()
    client.__enter__.return_value = client
    client.post.side_effect = fake_post
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(notify.httpx, "Client", client_cls)

    notify.send_telegram_message("hello dota")

    assert client_cls.call_args is not None
    assert client_cls.call_args.kwargs == {"timeout": HTTP_TIMEOUT_SECONDS}
    assert post_calls == [
        (
            "https://api.telegram.org/bottok-123/sendMessage",
            {"chat_id": "chat-42", "text": "hello dota"},
        )
    ]


def test_send_telegram_message_skips_when_credentials_are_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without bot credentials nothing is posted and a warning explains why."""
    patch_credentials(monkeypatch, None, None)
    client_cls = MagicMock()
    monkeypatch.setattr(notify.httpx, "Client", client_cls)

    with caplog.at_level(logging.WARNING, logger="trader.notify"):
        notify.send_telegram_message("hello")

    assert client_cls.call_count == 0
    assert any(
        "TG_BOT_API_TOKEN or TG_CHAT_ID not set" in record.message for record in caplog.records
    )


def test_send_telegram_message_logs_status_without_propagating_or_leaking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 5xx response is logged with the status only and never raised."""
    patch_credentials(monkeypatch, "tok-123", "chat-42")
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = build_response(500)
    monkeypatch.setattr(notify.httpx, "Client", MagicMock(return_value=client))

    with caplog.at_level(logging.WARNING, logger="trader.notify"):
        notify.send_telegram_message("hello")

    assert any("HTTP 500" in record.message for record in caplog.records)
    assert all("tok-123" not in record.message for record in caplog.records)


def test_send_telegram_message_logs_invalid_url_without_propagating(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed token that breaks the URL is logged by type and never raised."""
    patch_credentials(monkeypatch, "bad\ntoken", "chat-42")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    monkeypatch.setattr(
        notify.httpx, "Client", MagicMock(return_value=httpx.Client(transport=transport))
    )

    with caplog.at_level(logging.WARNING, logger="trader.notify"):
        notify.send_telegram_message("hello")

    assert any("InvalidURL" in record.message for record in caplog.records)
    assert all(
        "bad" not in record.message and "\n" not in record.message for record in caplog.records
    )


def test_send_telegram_message_logs_transport_error_without_propagating(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A transport exception is logged by type and never raised."""
    patch_credentials(monkeypatch, "tok-123", "chat-42")
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.side_effect = httpx.TransportError("connection dropped")
    monkeypatch.setattr(notify.httpx, "Client", MagicMock(return_value=client))

    with caplog.at_level(logging.WARNING, logger="trader.notify"):
        notify.send_telegram_message("hello")

    assert any("TransportError" in record.message for record in caplog.records)
    assert all("tok-123" not in record.message for record in caplog.records)


def sample_alert() -> notify.SessionAlert:
    """One Dota live identity used by finished-message tests."""
    return notify.SessionAlert(
        game="dota",
        mode="live",
        match_id="8944931337",
        radiant="Aurora",
        dire="Team Secret",
        map_number=1,
        market_slug="dota2-aurora-secret-game1",
        market_kind="map_winner",
    )


def test_session_started_message_is_multiline_identity() -> None:
    """Start pages game, mode, sides, map, slug, and match id. Not the condition hex."""
    alert = sample_alert()
    text = notify.session_started_message(alert)
    assert text.startswith(notify.SESSION_STARTED_PREFIX)
    assert "DOTA · live · map_winner" in text
    assert "Aurora vs Team Secret · map 1" in text
    assert "dota2-aurora-secret-game1" in text
    assert "8944931337" in text
    assert "0x" not in text


def test_session_started_message_marks_lol_paper() -> None:
    """LoL paper is readable without inferring from the slug alone."""
    alert = notify.SessionAlert(
        game="lol",
        mode="paper",
        match_id="grid-2966909-m1",
        radiant="Team Heretics",
        dire="Movistar KOI",
        map_number=1,
        market_slug="lol-mkoi-th-2026-08-30-game1",
        market_kind="map_winner",
    )
    text = notify.session_started_message(alert)
    assert "LOL · paper · map_winner" in text
    assert "Team Heretics vs Movistar KOI · map 1" in text


def test_notify_session_finished_sends_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finished alerts must not block the wallet loop: delivery is a daemon thread."""
    seen: list[int] = []

    def record(message: str) -> None:
        del message
        seen.append(threading.get_ident())

    monkeypatch.setattr(notify, "send_telegram_message", record)
    notify.notify_session_finished(sample_alert(), None, None, 0.0, 0.0, 0.0)
    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen
    assert seen[0] != threading.get_ident()


def test_session_finished_message_repeats_start_identity() -> None:
    """Finish carries the same identity block as start, then PnL."""
    text = notify.session_finished_message(sample_alert(), 1.25, 0.5, 0.01, 2.0, 0.0)
    assert text.startswith(notify.SESSION_FINISHED_PREFIX)
    assert "DOTA · live · map_winner" in text
    assert "Aurora vs Team Secret · map 1" in text
    assert "dota2-aurora-secret-game1" in text
    assert "8944931337" in text
    assert "net +1.7600" in text
    assert "realized 1.2500" in text
    assert "imv 0.5000" in text
    assert "rebate 0.0100" in text
    assert "leftover yes 2.0000  no 0.0000" in text
    assert "kalshi" not in text
    assert "usd" not in text


def test_session_feed_dead_and_exhausted_reuse_identity() -> None:
    """Fault pages keep the same identity block as session start."""
    alert = sample_alert()
    dead = notify.session_feed_dead_message(alert, "steam", "901234")
    assert dead.startswith(notify.SESSION_FEED_DEAD_PREFIX)
    assert "DOTA · live · map_winner" in dead
    assert "source steam  feed 901234; retrying" in dead
    exhausted = notify.session_exhausted_message(alert)
    assert exhausted.startswith(notify.SESSION_EXHAUSTED_PREFIX)
    assert "8944931337" in exhausted
