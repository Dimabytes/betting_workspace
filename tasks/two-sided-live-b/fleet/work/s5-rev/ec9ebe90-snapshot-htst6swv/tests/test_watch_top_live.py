"""GetTopLiveGame watcher: row projection, side split, selector."""

from watch_top_live import (
    board_key,
    format_heroes,
    read_tick,
    select_game,
)


def _row() -> dict[str, object]:
    return {
        "match_id": "9005981988",
        "league_id": 20279,
        "game_time": 1179,
        "radiant_score": 22,
        "dire_score": 9,
        "radiant_lead": 5272,
        "building_state": 9568337,
        "delay": 900,
        "spectators": 391,
        "team_name_radiant": "Team Nemesis",
        "team_name_dire": "1w",
        "last_update_time": 1789809000.0,
        "server_steam_id": "90293007002861582",
        "players": [
            {"account_id": 1, "hero_id": 123, "team_slot": 2, "team": 1},
            {"account_id": 2, "hero_id": 155, "team_slot": 1, "team": 0},
            {"account_id": 3, "hero_id": 98, "team_slot": 2, "team": 0},
            {"account_id": 4, "hero_id": 55, "team_slot": 1, "team": 1},
        ],
    }


HEROES = {155: "Marci", 98: "Timbersaw", 123: "Hoodwink", 55: "Dark Seer"}


def test_read_tick_string_match_id_and_team_gold_is_lead_only() -> None:
    tick = read_tick(_row(), HEROES)
    assert tick.match_id == 9005981988
    assert tick.game_time == 1179
    assert tick.radiant_lead == 5272
    assert tick.delay == 900
    assert tick.radiant_name == "Team Nemesis"
    assert tick.dire_name == "1w"
    assert [slot.hero for slot in tick.radiant_heroes] == ["Marci", "Timbersaw"]
    assert [slot.hero for slot in tick.dire_heroes] == ["Dark Seer", "Hoodwink"]
    assert format_heroes(tick.radiant_heroes) == "Marci  Timbersaw"


def test_select_game_by_match_id_or_team() -> None:
    tick = read_tick(_row(), HEROES)
    games = (tick,)
    assert select_game(games, "9005981988") is tick
    assert select_game(games, "nemesis") is tick
    assert select_game(games, "missing") is None


def test_board_key_ignores_spectator_flicker() -> None:
    tick = read_tick(_row(), HEROES)
    louder = read_tick({**_row(), "spectators": 999}, HEROES)
    later = read_tick({**_row(), "game_time": 1180}, HEROES)
    assert board_key(tick) == board_key(louder)
    assert board_key(tick) != board_key(later)
