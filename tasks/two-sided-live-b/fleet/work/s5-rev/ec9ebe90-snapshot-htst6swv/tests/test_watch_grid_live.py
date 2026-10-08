"""Watcher-only GRID script helpers: socket stale, slug extract, player formatting."""

import json

from grid_widget_fixtures import KLIM, LYNX, SERIES_TABLE
from watch_grid_live import extract_slug, format_players, grid_socket_is_stale

from shared.constants.strategy import GRID_FEED_STALE_SECONDS
from trader.grid_widgets import NetWorthSnapshot, PlayerNetWorth, read_net_worth


def test_grid_stale_threshold_is_sixteen_seconds() -> None:
    """GRID watchdog is 16s of raw socket silence, not Steam's 3s and not unique-gold gaps."""
    assert GRID_FEED_STALE_SECONDS == 16.0
    assert grid_socket_is_stale(15.999) is False
    assert grid_socket_is_stale(16.0) is True


def test_extract_slug_from_url_or_bare_slug() -> None:
    url = "https://polymarket.com/esports/dota-2/epl-masters/dota2-ks-lynx-2026-08-24"
    assert extract_slug(url) == "dota2-ks-lynx-2026-08-24"
    assert extract_slug("dota2-ks-lynx-2026-08-24") == "dota2-ks-lynx-2026-08-24"
    lol = "https://polymarket.com/esports/league-of-legends/lec/lol-vit-fnc-2026-08-28"
    assert extract_slug(lol) == "lol-vit-fnc-2026-08-28"


def test_format_players_lists_the_side_richest_first() -> None:
    snapshot = read_net_worth(json.dumps(SERIES_TABLE), 8)
    assert snapshot is not None
    assert format_players(snapshot, LYNX).startswith("juggernaut L20 23074  puck L19 22479")
    assert "life stealer L23 30784" in format_players(snapshot, KLIM)


def test_format_players_prints_nick_when_hero_is_empty() -> None:
    snapshot = NetWorthSnapshot(
        game_number=1,
        feed_delay=8,
        players=(
            PlayerNetWorth(
                team_id="1",
                nick="alice",
                hero="",
                has_portrait=False,
                level=14,
                net_worth=1000,
                kills=0,
                deaths=0,
                assists=0,
            ),
        ),
    )
    assert format_players(snapshot, "1") == "alice L14 1000"
