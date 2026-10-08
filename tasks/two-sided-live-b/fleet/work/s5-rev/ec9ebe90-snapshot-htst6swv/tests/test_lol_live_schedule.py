"""Live schedule matching and livestats tick projection. No network."""

from datetime import UTC, datetime

from lol.live_schedule import (
    LiveLolGame,
    LivePmEvent,
    aligned_starting_time,
    event_matches_game,
    games_for_pm_event,
    match_live_pairs,
    names_from_title,
    parse_get_live_games,
    parse_live_pm_event,
    parse_rfc460_seconds,
    project_tick,
    spawn_seek_starts,
    spawn_wall_from_payload,
)


def _pm_event(slug: str, team_a: str, team_b: str) -> LivePmEvent:
    """Minimal Polymarket live event for matching tests."""
    return LivePmEvent(
        slug=slug,
        title=f"LoL: {team_a} vs {team_b}",
        grid_series_id=None,
        team_a=team_a,
        team_b=team_b,
        score="",
        period="",
    )


def _live_game(
    game_id: str, team_a_name: str, team_a_code: str, team_b_name: str, team_b_code: str
) -> LiveLolGame:
    """Minimal getLive map for matching tests."""
    return LiveLolGame(
        esports_game_id=game_id,
        game_number=2,
        league="LCK Challengers League",
        team_a_name=team_a_name,
        team_a_code=team_a_code,
        team_b_name=team_b_name,
        team_b_code=team_b_code,
    )


def _participant(
    participant_id: int, gold: int, level: int, kills: int, deaths: int
) -> dict[str, int]:
    """One livestats participant object."""
    return {
        "participantId": participant_id,
        "totalGold": gold,
        "level": level,
        "kills": kills,
        "deaths": deaths,
    }


def _team(
    start_id: int, total_gold: int, total_kills: int, golds: tuple[int, ...]
) -> dict[str, object]:
    """Five participants whose golds sum is not checked against totalGold."""
    players = [
        _participant(start_id + index, gold, 11 + index, 1, 0) for index, gold in enumerate(golds)
    ]
    return {"totalGold": total_gold, "totalKills": total_kills, "participants": players}


def test_names_from_title_strips_lol_prefix_and_bo() -> None:
    names = names_from_title(
        "LoL: Nongshim Esports Academy vs DN SOOPers Challengers (BO5) - LCK Challengers"
    )
    assert names == ("Nongshim Esports Academy", "DN SOOPers Challengers")


def test_parse_live_pm_event_uses_outcomes_and_allows_missing_grid() -> None:
    event = parse_live_pm_event(
        {
            "slug": "lol-nsea-dnsc-2026-08-31",
            "title": "ignored vs ignored (BO5)",
            "live": True,
            "score": "1-0",
            "period": "2/5",
            "eventMetadata": {},
            "markets": [
                {
                    "outcomes": '["Nongshim Esports Academy", "DN SOOPers Challengers"]',
                }
            ],
        }
    )
    assert event is not None
    assert event.grid_series_id is None
    assert event.team_a == "Nongshim Esports Academy"
    assert event.team_b == "DN SOOPers Challengers"
    assert event.period == "2/5"


def test_academy_names_match_unique_getlive_game() -> None:
    event = _pm_event(
        "lol-nsea-dnsc-2026-08-31",
        "Nongshim Esports Academy",
        "DN SOOPers Challengers",
    )
    hit = _live_game(
        "game-nsea",
        "Nongshim Esports Academy",
        "NSEA",
        "DN SOOPers Challengers",
        "DNSC",
    )
    other = _live_game("game-lcs", "Cloud9 Kia", "C9", "Shopify Rebellion", "SR")
    assert event_matches_game(event, hit) is True
    assert event_matches_game(event, other) is False
    assert games_for_pm_event(event, (hit, other)) == (hit,)
    pairs = match_live_pairs((event,), (hit, other))
    assert len(pairs) == 1
    assert pairs[0].game.esports_game_id == "game-nsea"


def test_ambiguous_two_live_series_omitted() -> None:
    event = _pm_event(
        "lol-nsea-dnsc-2026-08-31",
        "Nongshim Esports Academy",
        "DN SOOPers Challengers",
    )
    first = _live_game(
        "game-1",
        "Nongshim Esports Academy",
        "NSEA",
        "DN SOOPers Challengers",
        "DNSC",
    )
    second = _live_game(
        "game-2",
        "Nongshim Esports Academy",
        "NSEA",
        "DN SOOPers Challengers",
        "DNSC",
    )
    assert games_for_pm_event(event, (first, second)) == (first, second)
    assert match_live_pairs((event,), (first, second)) == ()


def test_no_getlive_hit() -> None:
    event = _pm_event(
        "lol-nsea-dnsc-2026-08-31",
        "Nongshim Esports Academy",
        "DN SOOPers Challengers",
    )
    lcs = _live_game("game-lcs", "Cloud9 Kia", "C9", "Shopify Rebellion", "SR")
    assert games_for_pm_event(event, (lcs,)) == ()
    assert match_live_pairs((event,), (lcs,)) == ()


def test_parse_get_live_keeps_in_progress_only() -> None:
    body = {
        "data": {
            "schedule": {
                "events": [
                    {
                        "league": {"name": "LCK Challengers League"},
                        "match": {
                            "teams": [
                                {"name": "Nongshim Esports Academy", "code": "NSEA"},
                                {"name": "DN SOOPers Challengers", "code": "DNSC"},
                            ],
                            "games": [
                                {"id": "g1", "number": 1, "state": "completed"},
                                {"id": "g2", "number": 2, "state": "inProgress"},
                            ],
                        },
                    }
                ]
            }
        }
    }
    games = parse_get_live_games(body)
    assert len(games) == 1
    assert games[0].esports_game_id == "g2"
    assert games[0].game_number == 2
    assert games[0].team_a_code == "NSEA"


def test_aligned_starting_time_floors_to_ten_seconds() -> None:
    stamp = aligned_starting_time(datetime(2026, 8, 31, 12, 0, 7, tzinfo=UTC), 75)
    assert stamp == "2026-08-31T11:58:50.000Z"


def test_project_tick_uses_newest_frame_and_frame_age() -> None:
    stamp = "2026-08-31T12:00:00.000Z"
    payload = {
        "gameMetadata": {
            "blueTeamMetadata": {
                "participantMetadata": [
                    {"participantId": 1, "summonerName": "blue1", "championId": "Ahri"},
                    {"participantId": 2, "summonerName": "blue2"},
                    {"participantId": 3, "summonerName": "blue3"},
                    {"participantId": 4, "summonerName": "blue4"},
                    {"participantId": 5, "summonerName": "blue5"},
                ]
            },
            "redTeamMetadata": {
                "participantMetadata": [
                    {"participantId": 6, "championId": "Zed"},
                    {"participantId": 7, "summonerName": "red2"},
                    {"participantId": 8, "summonerName": "red3"},
                    {"participantId": 9, "summonerName": "red4"},
                    {"participantId": 10, "summonerName": "red5"},
                ]
            },
        },
        "frames": [
            {
                "rfc460Timestamp": "2026-08-31T11:59:50.000Z",
                "blueTeam": _team(1, 1000, 0, (200, 200, 200, 200, 200)),
                "redTeam": _team(6, 1000, 0, (200, 200, 200, 200, 200)),
            },
            {
                "rfc460Timestamp": stamp,
                "blueTeam": _team(1, 5000, 3, (1500, 1200, 900, 800, 600)),
                "redTeam": _team(6, 4000, 1, (1100, 1000, 800, 600, 500)),
            },
        ],
    }
    wall = parse_rfc460_seconds(stamp)
    assert wall is not None
    tick = project_tick(payload, wall + 55.0, wall - 120.0)
    assert tick is not None
    assert tick.stamp == stamp
    assert tick.blue_gold == 5000
    assert tick.red_gold == 4000
    assert tick.blue_kills == 3
    assert tick.red_kills == 1
    assert abs(tick.frame_age_seconds - 55.0) < 0.01
    assert tick.game_seconds is not None
    assert abs(tick.game_seconds - 120.0) < 0.01
    blue = [player for player in tick.players if player.side == "BLUE"]
    assert blue[0].label == "blue1"
    assert blue[0].gold == 1500
    red = [player for player in tick.players if player.side == "RED"]
    assert red[0].label == "Zed"
    assert red[0].gold == 1100
    missing_spawn = project_tick(payload, wall + 55.0, None)
    assert missing_spawn is not None
    assert missing_spawn.game_seconds is None


def test_spawn_wall_is_first_frame_with_gold() -> None:
    payload = {
        "frames": [
            {
                "rfc460Timestamp": "2026-08-31T12:00:00.000Z",
                "blueTeam": _team(1, 0, 0, (0, 0, 0, 0, 0)),
                "redTeam": _team(6, 0, 0, (0, 0, 0, 0, 0)),
            },
            {
                "rfc460Timestamp": "2026-08-31T12:00:10.000Z",
                "blueTeam": _team(1, 2500, 0, (500, 500, 500, 500, 500)),
                "redTeam": _team(6, 2500, 0, (500, 500, 500, 500, 500)),
            },
        ]
    }
    spawn = spawn_wall_from_payload(payload)
    expected = parse_rfc460_seconds("2026-08-31T12:00:10.000Z")
    assert spawn == expected


def test_spawn_seek_starts_floors_and_steps_ten_seconds() -> None:
    origin = parse_rfc460_seconds("2026-08-31T12:02:34.388Z")
    assert origin is not None
    starts = spawn_seek_starts(origin, 20)
    assert starts[0] == "2026-08-31T12:02:30.000Z"
    assert starts[1] == "2026-08-31T12:02:40.000Z"
    assert starts[-1] == "2026-08-31T12:02:50.000Z"
