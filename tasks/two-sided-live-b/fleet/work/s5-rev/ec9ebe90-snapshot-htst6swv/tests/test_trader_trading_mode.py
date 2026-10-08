"""Tests for CLI --mode, per-game assignment, and the LIVE_TRADING reject."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from collections.abc import Callable

import pytest

from trader import session_types
from trader.game_profile import GAME_PROFILES, GameProfile
from trader.trading_mode import (
    assigned_games,
    execution_mode,
    reject_legacy_env,
    require_live_wallet,
    steam_required,
)

DOTA = GAME_PROFILES["dota"]
LOL = GAME_PROFILES["lol"]


def _env_get(mapping: dict[str, str]) -> Callable[[str], str | None]:
    """Build an env_value stand-in that only sees `mapping`."""

    def read(name: str) -> str | None:
        return mapping.get(name)

    return read


def test_execution_mode_accepts_live_and_paper() -> None:
    """Only exact live and paper tokens become ExecutionMode."""
    assert execution_mode("paper") == "paper"
    assert execution_mode("live") == "live"


@pytest.mark.parametrize("token", ["1", "true", "Live"])
def test_execution_mode_rejects_unknown_tokens(token: str) -> None:
    """Unknown CLI tokens fail closed naming --mode."""
    with pytest.raises(session_types.TradingDisabled, match="--mode"):
        execution_mode(token)


def test_assigned_games_splits_dota_live_and_lol_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dota live + LoL paper assigns each game to exactly one process mode."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "live", "LOL_TRADING_MODE": "paper"}),
    )
    assert assigned_games("live") == (DOTA,)
    assert assigned_games("paper") == (LOL,)


def test_assigned_games_both_live_fills_live_and_idles_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both games live: trader-live gets both, trader-paper is empty."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "live", "LOL_TRADING_MODE": "live"}),
    )
    assert assigned_games("live") == (DOTA, LOL)
    assert assigned_games("paper") == ()


def test_assigned_games_both_off_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit off assigns the game to no process; empty result is allowed."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "off", "LOL_TRADING_MODE": "off"}),
    )
    assert assigned_games("live") == ()
    assert assigned_games("paper") == ()


def test_assigned_games_strips_off_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A padded off token is off after strip, not missing."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "live", "LOL_TRADING_MODE": " off "}),
    )
    assert assigned_games("live") == (DOTA,)
    assert assigned_games("paper") == ()


def test_assigned_games_missing_both_vars_is_trading_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset per-game modes are a startup error, not implicit off."""
    monkeypatch.setattr("trader.trading_mode.env_value", _env_get({}))
    with pytest.raises(session_types.TradingDisabled, match="DOTA_TRADING_MODE"):
        assigned_games("live")


def test_assigned_games_missing_lol_is_trading_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both variables must validate even when this process is live."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "live"}),
    )
    with pytest.raises(session_types.TradingDisabled, match="LOL_TRADING_MODE"):
        assigned_games("live")


def test_assigned_games_missing_dota_is_trading_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid LoL token does not skip a missing Dota variable."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"LOL_TRADING_MODE": "paper"}),
    )
    with pytest.raises(session_types.TradingDisabled, match="DOTA_TRADING_MODE"):
        assigned_games("paper")


@pytest.mark.parametrize("lol_raw", ["", "  ", "true", "1", "Live"])
def test_assigned_games_rejects_empty_or_unknown_lol(
    monkeypatch: pytest.MonkeyPatch, lol_raw: str
) -> None:
    """Empty, whitespace-only, and unknown LoL tokens fail closed."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"DOTA_TRADING_MODE": "live", "LOL_TRADING_MODE": lol_raw}),
    )
    with pytest.raises(session_types.TradingDisabled, match="LOL_TRADING_MODE"):
        assigned_games("live")


def test_reject_legacy_env_allows_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIVE_TRADING may only be absent."""
    monkeypatch.setattr("trader.trading_mode.env_value", _env_get({}))
    reject_legacy_env()


@pytest.mark.parametrize("legacy", ["1", "0", "true", ""])
def test_reject_legacy_env_errors_when_set(monkeypatch: pytest.MonkeyPatch, legacy: str) -> None:
    """Any assigned LIVE_TRADING value is fatal, including 0 and empty."""
    monkeypatch.setattr(
        "trader.trading_mode.env_value",
        _env_get({"LIVE_TRADING": legacy}),
    )
    with pytest.raises(session_types.TradingDisabled, match="LIVE_TRADING"):
        reject_legacy_env()


def test_live_without_wallet_is_trading_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live requested without both secrets fails closed before any engine."""
    monkeypatch.setattr("trader.trading_mode.env_value", _env_get({}))
    with pytest.raises(session_types.TradingDisabled, match="PK and BROWSER_ADDRESS"):
        require_live_wallet()


def test_live_with_wallet_passes_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both secrets present: live may construct an engine."""

    def fake_env(name: str) -> str | None:
        if name == "PK":
            return "0xabc"
        if name == "BROWSER_ADDRESS":
            return "0xdef"
        return None

    monkeypatch.setattr("trader.trading_mode.env_value", fake_env)
    require_live_wallet()


@pytest.mark.parametrize(
    "games, want_steam",
    [
        ((), False),
        ((LOL,), False),
        ((DOTA,), True),
        ((DOTA, LOL), True),
    ],
)
def test_steam_required_follows_assigned_profiles(
    games: tuple[GameProfile, ...], want_steam: bool
) -> None:
    """Steam follows uses_steam; empty assignment is neither."""
    assert steam_required(games) is want_steam
