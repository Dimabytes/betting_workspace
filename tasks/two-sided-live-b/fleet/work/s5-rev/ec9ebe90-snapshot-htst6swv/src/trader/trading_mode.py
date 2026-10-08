"""Process mode is CLI --mode; per-game assignment is DOTA/LOL_TRADING_MODE.

LIVE_TRADING is forbidden.
"""

from typing import Literal

from shared.utils.environment import env_value
from trader.game_profile import GAME_PROFILES, GameProfile
from trader.session_types import TradingDisabled

ExecutionMode = Literal["paper", "live"]

_GAME_MODE_TOKENS = ("off", "paper", "live")


def execution_mode(mode: str) -> ExecutionMode:
    """Return live or paper from the required CLI --mode token."""
    if mode == "live":
        return "live"
    if mode == "paper":
        return "paper"
    raise TradingDisabled(f"--mode {mode!r} is not live or paper")


def assigned_games(mode: ExecutionMode) -> tuple[GameProfile, ...]:
    """Return GAME_PROFILES whose DOTA/LOL_TRADING_MODE matches this process mode."""
    matched: list[GameProfile] = []
    for profile in GAME_PROFILES.values():
        raw = env_value(profile.mode_env)
        if raw is None or not raw.strip():
            raise TradingDisabled(f"{profile.mode_env} is missing")
        token = raw.strip()
        if token not in _GAME_MODE_TOKENS:
            raise TradingDisabled(f"{profile.mode_env} {token!r} is not off, paper or live")
        if token == mode:
            matched.append(profile)
    return tuple(matched)


def reject_legacy_env() -> None:
    """Fail if LIVE_TRADING is present; mode is CLI --mode plus per-game env vars."""
    if env_value("LIVE_TRADING") is not None:
        raise TradingDisabled(
            "LIVE_TRADING is removed; use --mode and DOTA_TRADING_MODE / LOL_TRADING_MODE"
        )


def require_live_wallet() -> None:
    """Fail closed when live is requested without both wallet secrets."""
    if env_value("PK") and env_value("BROWSER_ADDRESS"):
        return
    raise TradingDisabled("live trading requires PK and BROWSER_ADDRESS")


def steam_required(games: tuple[GameProfile, ...]) -> bool:
    """True when at least one assigned profile talks to the Steam Web API."""
    return any(profile.uses_steam for profile in games)
