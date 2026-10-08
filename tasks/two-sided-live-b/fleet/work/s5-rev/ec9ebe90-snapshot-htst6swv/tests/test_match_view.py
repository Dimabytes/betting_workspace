# pyright: reportPrivateUsage=false

import pytest

import dashboard.match_view as match_view
from dashboard.game_types import PlayerSummary, side_slice


def _player(nick: str, net_worth: int, hero: str | None = None) -> PlayerSummary:
    return PlayerSummary(
        nick=nick,
        nick_compact=None,
        hero=hero,
        hero_compact=None,
        net_worth=net_worth,
        kills=1,
        deaths=0,
        assists=2,
        level=None,
        alive=None,
        respawn=None,
        aegis=None,
    )


def test_players_html_hides_hero_column_for_lol() -> None:
    players = tuple(_player(f"p{i}", 1000) for i in range(5))
    html = match_view._players_html(players)
    assert "герой" not in html
    assert html.count("<th") == 3


def test_players_html_shows_hero_column_for_dota() -> None:
    players = tuple(_player(f"p{i}", 1000, hero="bane") for i in range(5))
    html = match_view._players_html(players)
    assert "герой" in html
    assert "bane" in html
    assert html.count("<th") == 4


def _capture_markdown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []

    def capture(html: object, **kw: object) -> None:
        out.append(str(html))

    monkeypatch.setattr(match_view.st, "markdown", capture)
    return out


def test_side_block_gold_is_the_model_input(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture_markdown(monkeypatch)
    side = side_slice("Blue", "Team", 99999, tuple(_player(f"p{i}", 1000) for i in range(5)))
    match_view._side_block(side, None)
    assert "gold 5000" in out[0]


def test_side_block_gold_falls_back_to_team_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _capture_markdown(monkeypatch)
    players = (
        *(_player(f"p{i}", 0) for i in range(4)),
        PlayerSummary(
            nick="p4",
            nick_compact=None,
            hero=None,
            hero_compact=None,
            net_worth=None,
            kills=None,
            deaths=None,
            assists=None,
            level=None,
            alive=None,
            respawn=None,
            aegis=None,
        ),
    )
    side = side_slice("Radiant", "Team", 12345, players)
    match_view._side_block(side, None)
    assert "gold 12345" in out[0]
