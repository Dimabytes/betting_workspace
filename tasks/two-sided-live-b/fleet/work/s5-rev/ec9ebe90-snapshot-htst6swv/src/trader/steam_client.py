"""Steam Web API client with one-way key rotation on 429/403."""

import json
import threading
from collections.abc import Mapping
from typing import Self

import httpx

from shared.utils.environment import env_value
from shared.utils.log import get_logger, suppress_http_url_logging
from trader.notify import notify_in_background

# Steam Web API (Valve). Keys in .env as STEAM_KEYS (comma-separated list; the
# trader client rotates to the next key on HTTP 429/403) or the legacy
# single STEAM_KEY, sent as the `key` query param.
# GetLiveLeagueGames = full scoreboard of every ticketed league game, but a new
# snapshot only every ~15s. GetRealtimeStats = one match by server_steam_id and
# is fresh on every request; measured 0.2-17.3s ahead of GetLiveLeagueGames.
# Server ids come from GetTopLiveGame (top 10 games) or from OpenDota /live.
# The floor is the league's own DotaTV delay, in `stream_delay_s`.
STEAM_LIVE_LEAGUE_GAMES_API = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"

STEAM_TOP_LIVE_GAME_API = "https://api.steampowered.com/IDOTA2Match_570/GetTopLiveGame/v1/"

STEAM_REALTIME_STATS_API = "https://api.steampowered.com/IDOTA2MatchStats_570/GetRealtimeStats/v1/"


logger = get_logger(__name__)

ROTATION_STATUS_CODES: frozenset[int] = frozenset({429, 403})
JSON_FAILED: object = object()


def load_steam_keys() -> tuple[str, ...]:
    """Parse the ordered Steam keys from STEAM_KEYS, falling back to STEAM_KEY."""
    listed = env_value("STEAM_KEYS")
    if listed:
        keys = tuple(key.strip() for key in listed.split(",") if key.strip())
        if keys:
            return keys
    legacy = env_value("STEAM_KEY")
    if legacy:
        key = legacy.strip()
        if key:
            return (key,)
    raise RuntimeError("neither STEAM_KEYS nor STEAM_KEY is set (env or .env)")


def decode_json(response: httpx.Response, label: str) -> object:
    """Decode a 2xx body as JSON; JSON_FAILED on UTF-8 or JSON failure.

    A JSON `null` body is a successful decode of None, not a failure: the
    caller treats that as a malformed Steam payload.
    """
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("%s json failed: %s", label, type(exc).__name__)
        return JSON_FAILED


class SteamClient:
    """Sync Steam Web API client that rotates to the next key on HTTP 429/403."""

    def __init__(self, client: httpx.Client, keys: tuple[str, ...]) -> None:
        """Bind an httpx client and the ordered keys; the first key serves first."""
        if not keys:
            raise ValueError("SteamClient needs at least one key")
        self._client = client
        self._keys = keys
        self._key_index = 0
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls, client: httpx.Client) -> Self:
        """Build a client from the STEAM_KEYS / STEAM_KEY values in env or .env."""
        return cls(client, load_steam_keys())

    def get(self, url: str, params: Mapping[str, str]) -> httpx.Response:
        """GET one Steam endpoint with the current key injected; rotate on 429/403.

        The key lock is held only to copy the current index into params and to
        rotate on 429/403. HTTP runs outside the lock so concurrent matches are
        not serialized to 1/RTT. Redirects are off: the key lives in the query
        string and must not follow Location onto another host.
        """
        suppress_http_url_logging()
        with self._lock:
            used_index = self._key_index
            params_with_key = dict(params)
            params_with_key["key"] = self._keys[used_index]
        response = self._client.get(url, params=params_with_key, follow_redirects=False)
        if response.status_code in ROTATION_STATUS_CODES:
            with self._lock:
                if self._key_index == used_index:
                    self._rotate_key(response.status_code)
        return response

    def get_ok(self, url: str, params: Mapping[str, str], label: str) -> httpx.Response | None:
        """GET; None on transport error or non-2xx. Rotation still happens on 429/403."""
        try:
            response = self.get(url, params)
        except httpx.HTTPError as exc:
            logger.warning("%s failed: %s", label, type(exc).__name__)
            return None
        if not 200 <= response.status_code < 300:
            logger.warning("%s http %d", label, response.status_code)
            return None
        return response

    def _rotate_key(self, status_code: int) -> None:
        """Advance one key past a rate limit and notify once; the last key stays."""
        used_ordinal = self._key_index + 1
        if self._key_index + 1 < len(self._keys):
            self._key_index += 1
            logger.warning(
                "steam %d on key %d: switched to key %d",
                status_code,
                used_ordinal,
                self._key_index + 1,
            )
            notify_in_background(
                f"steam {status_code} on key {used_ordinal}: switched to key {self._key_index + 1}"
            )
            return
        logger.warning("steam %d on last key %d: no more keys", status_code, used_ordinal)
        notify_in_background(f"steam {status_code} on last key {used_ordinal}: no more keys")
